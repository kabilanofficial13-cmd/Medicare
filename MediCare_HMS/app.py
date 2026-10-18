"""MediCare Hospital Management System with AI  (Flask + SQLite)."""
import re, threading, webbrowser, os, json, urllib.request, urllib.error, io, base64, urllib.parse, smtplib, mimetypes, uuid
from email.message import EmailMessage
from datetime import date, timedelta
from functools import wraps
# pyrefly: ignore [missing-import]
from flask import Flask, render_template, request, redirect, session, url_for, flash, g, jsonify
# pyrefly: ignore [missing-import]
from werkzeug.security import generate_password_hash, check_password_hash
import db as database   
import ai

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

def load_env_file():
    """Read settings from hms/.env (KEY=value per line) so passwords never live in the code."""
    if not os.path.exists(ENV_PATH):
        return
    raw = open(ENV_PATH, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):      # Notepad may save with a BOM or as UTF-16
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and not os.environ.get(k):            # a blank leftover variable must not block .env
                os.environ[k] = v

load_env_file()

app = Flask(__name__)
app.secret_key = os.environ.get("MEDICARE_SECRET_KEY", "hms-secret-key-change-me")
SLOTS = database.SLOTS
EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')

# ============================================================ helpers
def conn():
    if "db" not in g:
        g.db = database.connect()
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    d = g.pop("db", None)
    if d:
        d.close()

def q(sql, *a):  return conn().execute(sql, a).fetchall()
def q1(sql, *a): return conn().execute(sql, a).fetchone()
def run(sql, *a):
    cur = conn().execute(sql, a)
    conn().commit()
    return cur

def log(action):
    run("INSERT INTO audit(ts,user,action) VALUES(?,?,?)", database.now(), session.get("user", "-"), action)

def num(v):
    try: return int(v)
    except (TypeError, ValueError): return None

def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return None

@app.template_filter("inr")
def inr(v):
    return "₹{:,.2f}".format(v or 0)

def roles(*allowed):
    """Login + role check. Admin can open everything."""
    def deco(f):
        @wraps(f)
        def wrap(*a, **k):
            if "user" not in session:
                return redirect(url_for("login"))
            if session["role"] != "admin" and allowed and session["role"] not in allowed:
                flash("You do not have permission to open that page.")
                return redirect(url_for("dashboard"))
            return f(*a, **k)
        return wrap
    return deco

# ---- billing helpers
def open_bill(pid):
    b = q1("SELECT id FROM bills WHERE patient_id=? AND status='Pending' ORDER BY id DESC", pid)
    return b["id"] if b else run("INSERT INTO bills(patient_id,bdate) VALUES(?,?)", pid, str(date.today())).lastrowid

def add_item(pid, desc, cat, qty, price):
    run("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
        open_bill(pid), desc, cat, qty, price)

def bill_totals(bid):
    gross = q1("SELECT COALESCE(SUM(qty*price),0) FROM bill_items WHERE bill_id=?", bid)[0]
    disc = q1("SELECT discount FROM bills WHERE id=?", bid)[0] or 0
    return gross, disc, gross - disc

def send_whatsapp_bill(bid):
    """Send a bill using Meta WhatsApp Cloud API when credentials are configured.
    Free-form text messages require an open 24-hour customer-service window.
    Outside that window, an approved WhatsApp template must be used instead.
    """
    token = os.environ.get("WHATSAPP_TOKEN", "").strip()
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if not token or not phone_id:
        return "manual"
    b, items, (gross, disc, net) = load_bill(bid)
    if not b or not b["phone"]:
        return "no_phone"
    phone = re.sub(r"\D", "", b["phone"] or "")
    if len(phone) == 10:
        phone = "91" + phone
    if not 11 <= len(phone) <= 15:
        return "invalid_phone"
    lines = [f"Dear {b['pname']},", "", "MediCare Hospital - Bill Receipt",
             f"Bill No: #{b['id']}", f"Patient ID: {b['uhid']}",
             f"Date: {b['paid_on'] or b['bdate']}", ""]
    lines += [f"{i['description']} x {i['qty']} = ₹{i['qty']*i['price']:.2f}" for i in items]
    lines += ["", f"Gross: ₹{gross:.2f}", f"Discount: ₹{disc:.2f}",
              f"Total paid: ₹{net:.2f}", f"Payment mode: {b['mode'] or '-'}",
              "", "Thank you for choosing MediCare Hospital."]
    payload = json.dumps({"messaging_product":"whatsapp", "to":phone, "type":"text",
                          "text":{"preview_url":False, "body":"\n".join(lines)}}).encode("utf-8")
    req = urllib.request.Request(
        f"https://graph.facebook.com/v21.0/{phone_id}/messages", data=payload,
        headers={"Authorization":f"Bearer {token}", "Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
            return "sent" if result.get("messages") else "failed"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return "failed"

def invoice_pdf_bytes(bid):
    """Create a PDF invoice in memory; no patient file is written to disk."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    b, items, (gross, disc, net) = load_bill(bid)
    if not b:
        return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm, title=f'MediCare Invoice #{bid}')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Hospital', parent=styles['Title'], textColor=colors.HexColor('#4338ca'), fontSize=21, leading=25))
    story = [Paragraph('MediCare Hospital', styles['Hospital']),
             Paragraph('12, Main Road, Tambaram, Chennai · 044-0000-0000', styles['Normal']),
             Spacer(1, 8*mm), Paragraph(f'<b>INVOICE #{b["id"]}</b>', styles['Heading2']),
             Paragraph(f'Date: {b["paid_on"] or b["bdate"]}<br/>Patient: {b["pname"]} ({b["uhid"]})<br/>Age: {b["age"] or "-"} · Phone: {b["phone"] or "-"}<br/>Payment mode: {b["mode"] or "-"}<br/>Status: {b["status"]}', styles['Normal']),
             Spacer(1, 5*mm)]
    data = [['Description', 'Qty', 'Rate (INR)', 'Amount (INR)']]
    for item in items:
        data.append([str(item['description']), str(item['qty']), f'{item["price"]:.2f}', f'{item["qty"]*item["price"]:.2f}'])
    data += [['', '', 'Gross', f'{gross:.2f}'], ['', '', 'Discount', f'{disc:.2f}'], ['', '', 'TOTAL', f'{net:.2f}']]
    table = Table(data, colWidths=[86*mm, 18*mm, 34*mm, 34*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#4338ca')), ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'), ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#d1d5db')),
        ('ALIGN',(1,1),(-1,-1),'RIGHT'), ('ALIGN',(0,0),(0,-1),'LEFT'),
        ('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#ede9fe')), ('FONTNAME',(2,-3),(-1,-1),'Helvetica-Bold'),
        ('TOPPADDING',(0,0),(-1,-1),7), ('BOTTOMPADDING',(0,0),(-1,-1),7),
    ]))
    story += [table, Spacer(1, 8*mm)]
    # Add an amount-locked UPI intent QR to pending invoices when a real UPI ID is configured.
    upi_id = os.environ.get('MEDICARE_UPI_ID', '').strip()
    if b['status'] != 'Paid' and upi_id and '@' in upi_id and net > 0:
        upi_params = urllib.parse.urlencode({'pa': upi_id, 'pn': 'MediCare Hospital', 'am': f'{net:.2f}',
                                             'cu': 'INR', 'tn': f'MediCare Bill {bid}'})
        qr_buf = io.BytesIO()
        import qrcode
        qrcode.make('upi://pay?' + upi_params).save(qr_buf, format='PNG')
        qr_buf.seek(0)
        from reportlab.platypus import Image as PDFImage
        story += [Paragraph('<b>Payment pending — Scan to pay the exact amount using a UPI app</b>', styles['Heading3']),
                  Paragraph(f'Amount due: INR {net:.2f} · UPI ID: {upi_id}', styles['Normal']),
                  Spacer(1, 3*mm), PDFImage(qr_buf, width=38*mm, height=38*mm), Spacer(1, 4*mm)]
    story += [Paragraph('Thank you for choosing MediCare Hospital.', styles['Normal'])]
    doc.build(story)
    return buf.getvalue()

def smtp_settings():
    user = os.environ.get('MEDICARE_SMTP_USER', '').strip()
    return dict(host=os.environ.get('MEDICARE_SMTP_HOST', 'smtp.gmail.com').strip(),
                port=int(os.environ.get('MEDICARE_SMTP_PORT', '587') or 587),
                user=user,
                # Google shows app passwords as 4 groups with spaces; spaces are not part of the password.
                password=os.environ.get('MEDICARE_SMTP_PASSWORD', '').replace(' ', '').strip(),
                sender=os.environ.get('MEDICARE_EMAIL_FROM', user).strip() or user)

def email_diagnosis():
    """Plain-English reason why email is not ready (shown to the user)."""
    c = smtp_settings()
    where = f".env looked for at: {ENV_PATH}"
    if not os.path.exists(ENV_PATH):
        sib = [f for f in os.listdir(os.path.dirname(ENV_PATH)) if f.lower().startswith(".env")]
        return f"No file named exactly .env was found. {where}. Files starting with .env in that folder: {sib or 'none'}. (If you see .env.txt, rename it to .env.)"
    missing = [n for n, v in (("MEDICARE_SMTP_USER", c["user"]), ("MEDICARE_SMTP_PASSWORD", c["password"])) if not v]
    if missing:
        return f".env was found ({ENV_PATH}) but these values are empty or missing: {', '.join(missing)}."
    return "Email settings look complete."

def email_configured():
    c = smtp_settings()
    return bool(c['user'] and c['password'])

def smtp_deliver(msg):
    """Send one prepared EmailMessage. Returns 'sent', 'auth_failed' or 'failed'. Safe to call from a thread."""
    c = smtp_settings()
    try:
        if c['port'] == 465:
            import ssl
            with smtplib.SMTP_SSL(c['host'], c['port'], timeout=25, context=ssl.create_default_context()) as server:
                server.login(c['user'], c['password'])
                server.send_message(msg)
        else:
            with smtplib.SMTP(c['host'], c['port'], timeout=25) as server:
                server.starttls()
                server.login(c['user'], c['password'])
                server.send_message(msg)
        return 'sent'
    except smtplib.SMTPAuthenticationError:
        app.logger.error('SMTP login rejected. For Gmail use a Google App Password, not the account password.')
        return 'auth_failed'
    except (OSError, smtplib.SMTPException, ValueError):
        app.logger.exception('SMTP delivery failed')
        return 'failed'

def build_invoice_message(bid, recipient):
    """Build the email with the PDF attached. Needs a request context (reads the bill)."""
    b, _, (gross, disc, net) = load_bill(bid)
    if not b:
        return None
    msg = EmailMessage()
    paid = b['status'] == 'Paid'
    msg['Subject'] = f'MediCare Hospital - {"Receipt" if paid else "Invoice"} #{bid}'
    msg['From'] = f'MediCare Hospital <{smtp_settings()["sender"]}>'
    msg['To'] = recipient
    msg.set_content(
        f'Dear {b["pname"]},\n\n'
        f'Thank you for visiting MediCare Hospital. Your {"payment receipt" if paid else "invoice"} is attached as a PDF.\n\n'
        f'Invoice no : #{bid}\nPatient ID : {b["uhid"]}\nDate       : {b["paid_on"] or b["bdate"]}\n'
        f'Amount     : INR {net:.2f}\nStatus     : {b["status"]}\n\n'
        f'If you have any questions about this bill, please contact the hospital billing desk.\n\n'
        f'Regards,\nMediCare Hospital\n12, Main Road, Tambaram, Chennai · 044-0000-0000\n\n'
        f'This message contains your private medical bill. If it reached you by mistake, please delete it.')
    msg.add_attachment(invoice_pdf_bytes(bid), maintype='application', subtype='pdf', filename=f'MediCare_Invoice_{bid}.pdf')
    return msg

def send_invoice_email(bid, recipient):
    """Send now and wait for the result (used by the manual 'Send PDF by Email' button)."""
    if not email_configured():
        return 'not_configured'
    msg = build_invoice_message(bid, recipient)
    if msg is None:
        return 'missing_bill'
    result = smtp_deliver(msg)
    run("UPDATE bills SET email_status=? WHERE id=?", f'{result} to {recipient} on {database.now()}', bid)
    return result

def send_invoice_email_async(bid, recipient):
    """Queue the email in the background so billing is never slowed down by the mail server."""
    if not email_configured():
        return 'not_configured'
    msg = build_invoice_message(bid, recipient)
    if msg is None:
        return 'missing_bill'
    def worker():
        result = smtp_deliver(msg)
        c = database.connect()
        try:
            c.execute("UPDATE bills SET email_status=? WHERE id=?", (f'{result} to {recipient} on {database.now()}', bid))
            c.execute("INSERT INTO audit(ts,user,action) VALUES(?,?,?)",
                      (database.now(), 'system', f'Invoice #{bid} email to {recipient}: {result}'))
            c.commit()
        finally:
            c.close()
    threading.Thread(target=worker, daemon=True).start()
    return 'queued'

def send_whatsapp_pdf(bid):
    """Upload and send the PDF using Meta WhatsApp Cloud API if configured."""
    token = os.environ.get('WHATSAPP_TOKEN', '').strip()
    phone_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '').strip()
    b, _, (_, _, net) = load_bill(bid)
    if not b:
        return 'missing_bill'
    phone = re.sub(r'\D', '', b['phone'] or '')
    if len(phone) == 10:
        phone = '91' + phone
    if not 11 <= len(phone) <= 15:
        return 'invalid_phone'
    if not token or not phone_id:
        return 'not_configured'
    pdf = invoice_pdf_bytes(bid)
    boundary = '----MediCare' + uuid.uuid4().hex
    chunks = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="MediCare_Invoice_{bid}.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
        pdf, f'\r\n--{boundary}--\r\n'.encode()
    ]
    req = urllib.request.Request(f'https://graph.facebook.com/v21.0/{phone_id}/media', data=b''.join(chunks),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            media_id = json.loads(response.read().decode('utf-8')).get('id')
        if not media_id:
            return 'failed'
        payload = json.dumps({'messaging_product':'whatsapp', 'to':phone, 'type':'document',
            'document':{'id':media_id, 'filename':f'MediCare_Invoice_{bid}.pdf',
                        'caption':f'MediCare Hospital invoice #{bid} · Amount ₹{net:.2f} · Status {b["status"]}'}}).encode()
        msg_req = urllib.request.Request(f'https://graph.facebook.com/v21.0/{phone_id}/messages', data=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(msg_req, timeout=20) as response:
            result = json.loads(response.read().decode('utf-8'))
        return 'sent' if result.get('messages') else 'failed'
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        app.logger.exception('WhatsApp PDF sending failed for bill %s', bid)
        return 'failed'

def revenue(day=None):
    w, a = (" AND b.paid_on=?", (day,)) if day else ("", ())
    gross = q1("SELECT COALESCE(SUM(i.qty*i.price),0) FROM bill_items i JOIN bills b ON b.id=i.bill_id WHERE b.status='Paid'" + w, *a)[0]
    disc = q1("SELECT COALESCE(SUM(b.discount),0) FROM bills b WHERE b.status='Paid'" + w, *a)[0]
    return gross - disc

def dues():
    return q1("SELECT COALESCE(SUM(i.qty*i.price),0) FROM bill_items i JOIN bills b ON b.id=i.bill_id WHERE b.status='Pending'")[0]

def order_lab(pid, tid, did, cid=None):
    t = q1("SELECT * FROM lab_tests WHERE id=?", tid)
    run("INSERT INTO lab_orders(patient_id,test_id,doctor_id,consultation_id,ordered_at) VALUES(?,?,?,?,?)",
        pid, tid, did, cid, database.now())
    add_item(pid, f"Lab: {t['name']}", "Laboratory", 1, t["price"])

# ============================================================ auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q1("SELECT * FROM users WHERE username=?", request.form["username"].strip())
        if u and check_password_hash(u["password"], request.form["password"]):
            session.update(user=u["username"], role=u["role"], name=u["full_name"], doctor_id=u["doctor_id"])
            log("Logged in")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ============================================================ dashboard
@app.route("/")
@roles()
def dashboard():
    t = str(date.today())
    one = lambda s, *a: q1(s, *a)[0]
    stats = dict(
        patients=one("SELECT COUNT(*) FROM patients"), doctors=one("SELECT COUNT(*) FROM doctors"),
        today=one("SELECT COUNT(*) FROM appointments WHERE adate=?", t),
        admitted=one("SELECT COUNT(*) FROM admissions WHERE status='Admitted'"),
        free_beds=one("SELECT COUNT(*) FROM beds WHERE occupied=0"),
        pending_lab=one("SELECT COUNT(*) FROM lab_orders WHERE status='Pending'"),
        low_stock=one("SELECT COUNT(*) FROM medicines WHERE stock<=reorder_level"),
        pending_rx=one("SELECT COUNT(*) FROM prescriptions WHERE dispensed=0"),
        revenue=revenue(), today_rev=revenue(t), dues=dues())
    stats["beds"] = one("SELECT COUNT(*) FROM beds")
    stats["occupied"] = stats["beds"] - stats["free_beds"]
    stats["occ_pct"] = round(stats["occupied"] * 100 / stats["beds"]) if stats["beds"] else 0
    week = [(str(date.today() - timedelta(days=i))[5:], one("SELECT COUNT(*) FROM appointments WHERE adate=?",
            str(date.today() - timedelta(days=i)))) for i in range(6, -1, -1)]
    today_appts = q("""SELECT a.*, p.name pname, p.uhid, d.name dname FROM appointments a JOIN patients p ON p.id=a.patient_id
                       JOIN doctors d ON d.id=a.doctor_id WHERE a.adate=? ORDER BY a.atime""", t)
    return render_template("dashboard.html", stats=stats, week=week, today_appts=today_appts, insights=ai.insights(conn()))

# ============================================================ patients
@app.route("/patients", methods=["GET", "POST"])
@roles("receptionist", "doctor")
def patients():
    if request.method == "POST":
        if session["role"] not in ("admin", "receptionist"):
            flash("Only reception can register patients.")
            return redirect(url_for("patients"))
        f = request.form
        pid = database.new_patient(conn(), f["name"], num(f["age"]), f["gender"], f["blood_group"], f["phone"],
                                   f["address"], f["emergency_contact"], f["allergies"] or "None", f.get("email", ""))
        log(f"Registered patient #{pid} {f['name']}")
        flash(f"Patient registered with UHID UH{pid:05d}")
        return redirect(url_for("patient", pid=pid))
    s = request.args.get("q", "")
    rows = q("SELECT * FROM patients WHERE name LIKE ? OR phone LIKE ? OR uhid LIKE ? ORDER BY id DESC",
             f"%{s}%", f"%{s}%", f"%{s}%")
    return render_template("patients.html", patients=rows, q=s)

@app.route("/patients/<int:pid>")
@roles("receptionist", "doctor")
def patient(pid):
    p = q1("SELECT * FROM patients WHERE id=?", pid)
    if not p:
        flash("Patient not found")
        return redirect(url_for("patients"))
    vit = q("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT 10", pid)
    level, alerts = ai.assess_vitals(vit[0] if vit else None)
    trend = ai.vitals_trend(vit)
    consults = q("""SELECT c.*, d.name dname FROM consultations c JOIN doctors d ON d.id=c.doctor_id
                    WHERE c.patient_id=? ORDER BY c.id DESC""", pid)
    labs = [dict(l, note=ai.interpret_lab(l["name"], l["result"], l["low"], l["high"])[1]) for l in q(
        """SELECT o.*, t.name, t.unit, t.low, t.high FROM lab_orders o JOIN lab_tests t ON t.id=o.test_id
           WHERE o.patient_id=? ORDER BY o.id DESC""", pid)]
    adms = q("""SELECT a.*, d.name dname, w.name ward, b.bed_no FROM admissions a JOIN doctors d ON d.id=a.doctor_id
                JOIN beds b ON b.id=a.bed_id JOIN wards w ON w.id=b.ward_id WHERE a.patient_id=? ORDER BY a.id DESC""", pid)
    appts = q("""SELECT a.*, d.name dname FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                 WHERE patient_id=? ORDER BY adate DESC, atime DESC""", pid)
    bills = q("""SELECT b.*, (SELECT COALESCE(SUM(qty*price),0) FROM bill_items WHERE bill_id=b.id)-b.discount total
                 FROM bills b WHERE patient_id=? ORDER BY id DESC""", pid)
    return render_template("patient.html", p=p, vit=vit, level=level, alerts=alerts, trend=trend, consults=consults,
                           labs=labs, adms=adms, appts=appts, bills=bills, tests=q("SELECT * FROM lab_tests ORDER BY name"),
                           summary=ai.make_summary(p, level, alerts, consults, labs, adms))

@app.route("/patients/<int:pid>/email", methods=["POST"])
@roles("receptionist")
def update_patient_email(pid):
    em = (request.form.get("email") or "").strip()
    if em and not EMAIL_RE.fullmatch(em):
        flash("Enter a valid email address.")
    else:
        run("UPDATE patients SET email=? WHERE id=?", em, pid)
        log(f"Updated email for patient #{pid}")
        flash("Patient email saved.")
    return redirect(url_for("patient", pid=pid))

@app.route("/patients/<int:pid>/vitals", methods=["POST"])
@roles("receptionist", "doctor")
def add_vitals(pid):
    f = request.form
    run("""INSERT INTO vitals(patient_id,bp_sys,bp_dia,sugar,heart_rate,spo2,temp,recorded_at,recorded_by)
           VALUES(?,?,?,?,?,?,?,?,?)""", pid, num(f.get("bp_sys")), num(f.get("bp_dia")), num(f.get("sugar")), num(f.get("heart_rate")),
        num(f.get("spo2")), fnum(f.get("temp")), database.now(), session["user"])
    level, alerts = ai.assess_vitals(q1("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC", pid))
    flash("Vitals saved." + (f" AI ALERT ({level}): " + "; ".join(alerts) if level in ("High", "Critical") else ""))
    log(f"Vitals recorded for patient #{pid} (risk {level})")
    return redirect(url_for("patient", pid=pid))

@app.route("/patients/<int:pid>/lab_order", methods=["POST"])
@roles("doctor")
def patient_lab_order(pid):
    order_lab(pid, int(request.form["test_id"]), session.get("doctor_id") or 1)
    flash("Lab test ordered and added to bill")
    return redirect(url_for("patient", pid=pid))

@app.route("/patients/<int:pid>/delete", methods=["POST"])
@roles()
def delete_patient(pid):
    if session["role"] != "admin":
        flash("Only admin can delete patients.")
        return redirect(url_for("patients"))
    for a in q("SELECT bed_id FROM admissions WHERE patient_id=? AND status='Admitted'", pid):
        run("UPDATE beds SET occupied=0 WHERE id=?", a["bed_id"])
    run("DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE patient_id=?)", pid)
    for t in ("bills", "admissions", "lab_orders", "prescriptions", "consultations", "appointments", "vitals"):
        run(f"DELETE FROM {t} WHERE patient_id=?", pid)
    run("DELETE FROM patients WHERE id=?", pid)
    log(f"Deleted patient #{pid}")
    return redirect(url_for("patients"))

# ============================================================ doctors
@app.route("/doctors", methods=["GET", "POST"])
@roles()
def doctors():
    if request.method == "POST":
        if session["role"] != "admin":
            flash("Only admin can add doctors.")
        else:
            f = request.form
            run("INSERT INTO doctors(name,department,qualification,phone,fee) VALUES(?,?,?,?,?)",
                f["name"], f["department"], f["qualification"], f["phone"], fnum(f["fee"]) or 500)
            log(f"Added doctor {f['name']}")
            flash("Doctor added")
        return redirect(url_for("doctors"))
    rows = q("""SELECT d.*, (SELECT COUNT(*) FROM appointments WHERE doctor_id=d.id AND adate=?) today
                FROM doctors d ORDER BY department, name""", str(date.today()))
    return render_template("doctors.html", doctors=rows, departments=ai.DEPARTMENTS)

@app.route("/doctors/<int:did>/delete", methods=["POST"])
@roles()
def delete_doctor(did):
    if session["role"] == "admin":
        run("DELETE FROM doctors WHERE id=?", did)
        log(f"Deleted doctor #{did}")
    return redirect(url_for("doctors"))

# ============================================================ appointments
@app.route("/appointments", methods=["GET", "POST"])
@roles("receptionist", "doctor")
def appointments():
    if request.method == "POST":
        if session["role"] not in ("admin", "receptionist"):
            flash("Only reception can book appointments.")
            return redirect(url_for("appointments"))
        f = request.form
        if q1("""SELECT 1 FROM appointments WHERE doctor_id=? AND adate=? AND atime=? AND status!='Cancelled'""",
              f["doctor_id"], f["adate"], f["atime"]):
            flash("That doctor is already booked at that time. Choose another slot.")
        else:
            run("INSERT INTO appointments(patient_id,doctor_id,adate,atime,reason) VALUES(?,?,?,?,?)",
                f["patient_id"], f["doctor_id"], f["adate"], f["atime"], f["reason"])
            log(f"Booked appointment for patient #{f['patient_id']}")
            flash("Appointment booked")
        return redirect(url_for("appointments"))
    day = request.args.get("d", "")
    sql = """SELECT a.*, p.name pname, p.uhid, d.name dname, d.department FROM appointments a
             JOIN patients p ON p.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id"""
    rows = q(sql + " WHERE a.adate=? ORDER BY a.atime", day) if day else q(sql + " ORDER BY a.adate DESC, a.atime LIMIT 100")
    return render_template("appointments.html", appts=rows, today=str(date.today()), slots=SLOTS, day=day,
                           patients=q("SELECT id,name,uhid FROM patients ORDER BY name"), pid=request.args.get("pid", ""),
                           doctors=q("SELECT * FROM doctors ORDER BY department"), departments=ai.DEPARTMENTS)

@app.route("/appointments/<int:aid>/<status>", methods=["POST"])
@roles("receptionist", "doctor")
def appt_status(aid, status):
    if status in ("Completed", "Cancelled"):
        run("UPDATE appointments SET status=? WHERE id=?", status, aid)
    return redirect(request.referrer or url_for("appointments"))

# ============================================================ OPD consultation (doctor)
@app.route("/opd")
@roles("doctor")
def opd():
    d = request.args.get("d", str(date.today()))
    sql = """SELECT a.*, p.name pname, p.age, p.gender, p.uhid, p.allergies, dd.name dname FROM appointments a
             JOIN patients p ON p.id=a.patient_id JOIN doctors dd ON dd.id=a.doctor_id WHERE a.adate=?"""
    args = [d]
    if session["role"] == "doctor" and session.get("doctor_id"):
        sql += " AND a.doctor_id=?"
        args.append(session["doctor_id"])
    return render_template("opd.html", appts=q(sql + " ORDER BY a.atime", *args), d=d)

@app.route("/consult/<int:aid>", methods=["GET", "POST"])
@roles("doctor")
def consult(aid):
    a = q1("""SELECT a.*, d.fee, d.name dname, d.department FROM appointments a JOIN doctors d ON d.id=a.doctor_id
              WHERE a.id=?""", aid)
    if not a:
        return redirect(url_for("opd"))
    p = q1("SELECT * FROM patients WHERE id=?", a["patient_id"])
    if request.method == "POST":
        f = request.form
        cid = run("""INSERT INTO consultations(appointment_id,patient_id,doctor_id,cdate,symptoms,diagnosis,notes,advice)
                     VALUES(?,?,?,?,?,?,?,?)""", aid, p["id"], a["doctor_id"], str(date.today()), f.get("symptoms", ""),
                  f.get("diagnosis", ""), f.get("notes", ""), f.get("advice", "")).lastrowid
        dos, days = f.getlist("dosage"), f.getlist("days")
        for i, m in enumerate(f.getlist("med_id")):
            if m:
                nd = num(days[i]) or 1
                per_day = sum(int(x) for x in re.findall(r"\d", dos[i])) or 1
                run("INSERT INTO prescriptions(consultation_id,patient_id,medicine_id,dosage,days,qty) VALUES(?,?,?,?,?,?)",
                    cid, p["id"], m, dos[i], nd, nd * per_day)
        for t in f.getlist("test_id"):
            order_lab(p["id"], int(t), a["doctor_id"], cid)
        if any(f.get(k) for k in ("bp_sys", "sugar", "heart_rate", "spo2", "temp")):
            run("""INSERT INTO vitals(patient_id,bp_sys,bp_dia,sugar,heart_rate,spo2,temp,recorded_at,recorded_by)
                   VALUES(?,?,?,?,?,?,?,?,?)""", p["id"], num(f.get("bp_sys")), num(f.get("bp_dia")), num(f.get("sugar")),
                num(f.get("heart_rate")), num(f.get("spo2")), fnum(f.get("temp")), database.now(), session["user"])
        add_item(p["id"], f"Consultation - {a['dname']}", "Consultation", 1, a["fee"])
        run("UPDATE appointments SET status='Completed' WHERE id=?", aid)
        log(f"Consultation #{cid} saved for patient #{p['id']}")
        flash("Consultation saved. Prescription sent to pharmacy, lab tests sent to laboratory.")
        return redirect(url_for("prescription", cid=cid))
    vit = q("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT 5", p["id"])
    level, alerts = ai.assess_vitals(vit[0] if vit else None)
    past = q("SELECT * FROM consultations WHERE patient_id=? ORDER BY id DESC LIMIT 3", p["id"])
    return render_template("consult.html", a=a, p=p, level=level, alerts=alerts, past=past,
                           meds=q("SELECT * FROM medicines ORDER BY name"), tests=q("SELECT * FROM lab_tests ORDER BY name"))

@app.route("/prescription/<int:cid>")
@roles()
def prescription(cid):
    c = q1("""SELECT c.*, d.name dname, d.department, d.qualification FROM consultations c
              JOIN doctors d ON d.id=c.doctor_id WHERE c.id=?""", cid)
    if not c:
        return redirect(url_for("dashboard"))
    return render_template("prescription.html", c=c, p=q1("SELECT * FROM patients WHERE id=?", c["patient_id"]),
        rx=q("""SELECT r.*, m.name FROM prescriptions r JOIN medicines m ON m.id=r.medicine_id WHERE r.consultation_id=?""", cid),
        labs=q("""SELECT t.name FROM lab_orders o JOIN lab_tests t ON t.id=o.test_id WHERE o.consultation_id=?""", cid))

# ============================================================ pharmacy
def stock_move(mid, change, reason):
    """Change stock and record it in the stock log (one place for every movement)."""
    run("UPDATE medicines SET stock=stock+? WHERE id=?", change, mid)
    run("INSERT INTO stock_log(medicine_id,change,reason,user,ts) VALUES(?,?,?,?,?)",
        mid, change, reason, session.get("user", "-"), database.now())

def rx_warnings(r):
    """Safety warnings for one pending prescription row: allergy, age, and interactions with the patient's other pending medicines."""
    p = q1("SELECT allergies, age FROM patients WHERE id=?", r["patient_id"])
    others = [x["name"] for x in q("""SELECT m.name FROM prescriptions x JOIN medicines m ON m.id=x.medicine_id
                                      WHERE x.patient_id=? AND x.dispensed=0 AND x.id<>?""", r["patient_id"], r["id"])]
    return ai.check_interactions([r["mname"] if "mname" in r.keys() else r["name"]] + others, p["allergies"], p["age"])

@app.route("/pharmacy")
@roles("pharmacist")
def pharmacy():
    t = date.today()
    soon = str(t + timedelta(days=60))
    today = str(t)
    f = request.args.get("filter", "all")
    term = request.args.get("q", "").strip()
    pending = q("""SELECT r.*, m.name mname, m.stock, m.price, m.expiry, p.name pname, p.uhid, p.allergies, c.cdate FROM prescriptions r
                   JOIN medicines m ON m.id=r.medicine_id JOIN patients p ON p.id=r.patient_id
                   JOIN consultations c ON c.id=r.consultation_id WHERE r.dispensed=0 ORDER BY r.id""")
    rows = []
    for r in pending:
        w = rx_warnings(r)
        rows.append(dict(r, warns=w, high=any(x["level"] == "HIGH" for x in w)))
    pending = rows
    where, args = ["1=1"], []
    if term:
        where.append("(name LIKE ? OR category LIKE ? OR manufacturer LIKE ? OR batch_no LIKE ?)")
        args += [f"%{term}%"] * 4
    if f == "low":      where.append("stock<=reorder_level")
    elif f == "soon":   where.append("expiry<=? AND expiry>=?"); args += [soon, today]
    elif f == "expired": where.append("expiry<?"); args.append(today)
    elif f == "out":    where.append("stock<=0")
    meds = q("SELECT * FROM medicines WHERE " + " AND ".join(where) + " ORDER BY name", *args)
    s = q1("""SELECT COUNT(*) n, COALESCE(SUM(stock*price),0) retail, COALESCE(SUM(stock*COALESCE(cost,0)),0) cost,
              COALESCE(SUM(stock<=reorder_level),0) low, COALESCE(SUM(expiry<?),0) expired,
              COALESCE(SUM(expiry<=? AND expiry>=?),0) soon, COALESCE(SUM(stock<=0),0) out FROM medicines""", today, soon, today)
    cats = [r[0] for r in q("SELECT DISTINCT category FROM medicines ORDER BY category")]
    return render_template("pharmacy.html", meds=meds, pending=pending, soon=soon, today=today, stats=s, f=f, term=term,
                           cats=cats, patients=q("SELECT id,name,uhid FROM patients ORDER BY name"),
                           allmeds=q("SELECT id,name,stock,price,expiry FROM medicines WHERE stock>0 ORDER BY name"))

@app.route("/pharmacy/dispense/<int:rid>", methods=["POST"])
@roles("pharmacist")
def dispense(rid):
    r = q1("""SELECT r.*, m.name, m.name mname, m.stock, m.price, m.expiry FROM prescriptions r JOIN medicines m ON m.id=r.medicine_id WHERE r.id=?""", rid)
    if not r or r["dispensed"]:
        return redirect(url_for("pharmacy"))
    if r["expiry"] < str(date.today()):
        flash(f"{r['name']} in stock is EXPIRED ({r['expiry']}). It cannot be dispensed - write it off and restock.")
    elif r["stock"] < r["qty"]:
        flash(f"Not enough stock for {r['name']} (need {r['qty']}, have {r['stock']}). Please restock.")
    elif any(w["level"] == "HIGH" for w in rx_warnings(r)) and request.form.get("override") != "1":
        flash(f"Safety warning for {r['name']}: review the red alert, then use 'Dispense anyway' only if the doctor has confirmed.")
    else:
        stock_move(r["medicine_id"], -r["qty"], f"Dispensed on prescription #{rid}")
        run("UPDATE prescriptions SET dispensed=1 WHERE id=?", rid)
        add_item(r["patient_id"], f"Medicine: {r['name']}", "Pharmacy", r["qty"], r["price"])
        log(f"Dispensed {r['qty']} x {r['name']}" + (" (safety warning overridden)" if request.form.get("override") == "1" else ""))
        flash(f"Dispensed {r['qty']} x {r['name']} and added to patient bill")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/add", methods=["POST"])
@roles("pharmacist")
def add_medicine():
    f = request.form
    name, qty = f["name"].strip(), num(f["stock"]) or 0
    ex = q1("SELECT id FROM medicines WHERE name=?", name)
    if ex:
        run("UPDATE medicines SET price=?, expiry=?, batch_no=COALESCE(NULLIF(?,''),batch_no), cost=COALESCE(?,cost) WHERE id=?",
            fnum(f["price"]), f["expiry"], f.get("batch_no", "").strip(), fnum(f.get("cost")), ex["id"])
        stock_move(ex["id"], qty, f"Restock (batch {f.get('batch_no') or '-'})")
        flash("Existing medicine restocked")
    else:
        mid = run("""INSERT INTO medicines(name,category,stock,price,reorder_level,expiry,batch_no,manufacturer,rack,cost)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""", name, f["category"], 0, fnum(f["price"]), num(f["reorder"]) or 20, f["expiry"],
                    f.get("batch_no", "").strip(), f.get("manufacturer", "").strip(), f.get("rack", "").strip(), fnum(f.get("cost")) or 0).lastrowid
        stock_move(mid, qty, "Opening stock (new medicine)")
        flash("New medicine added")
    log(f"Stock update: {name} +{qty}")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/<int:mid>/edit", methods=["POST"])
@roles("pharmacist")
def edit_medicine(mid):
    f = request.form
    run("""UPDATE medicines SET category=?, price=?, cost=?, reorder_level=?, expiry=?, batch_no=?, manufacturer=?, rack=? WHERE id=?""",
        f["category"], fnum(f["price"]) or 0, fnum(f.get("cost")) or 0, num(f["reorder"]) or 0, f["expiry"],
        f.get("batch_no", "").strip(), f.get("manufacturer", "").strip(), f.get("rack", "").strip(), mid)
    log(f"Edited medicine #{mid}")
    flash("Medicine details updated")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/<int:mid>/adjust", methods=["POST"])
@roles("pharmacist")
def adjust_stock(mid):
    m = q1("SELECT * FROM medicines WHERE id=?", mid)
    qty, reason = num(request.form.get("qty")), request.form.get("reason", "Correction")
    if not m or not qty:
        flash("Enter a quantity to adjust.")
    elif reason in ("Expired write-off", "Damaged", "Lost") and qty > 0:
        qty = -qty            # these reasons always reduce stock
    if m and qty:
        if m["stock"] + qty < 0:
            flash(f"Cannot remove {-qty}: only {m['stock']} in stock.")
        else:
            stock_move(mid, qty, f"Adjustment: {reason}")
            log(f"Stock adjust {m['name']} {qty:+d} ({reason})")
            flash(f"Stock of {m['name']} adjusted by {qty:+d}.")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/sale", methods=["POST"])
@roles("pharmacist")
def counter_sale():
    """Sell a medicine directly (no prescription) and put it on the patient's open bill."""
    f = request.form
    pid, mid, qty = num(f.get("patient_id")), num(f.get("medicine_id")), num(f.get("qty")) or 0
    p, m = q1("SELECT * FROM patients WHERE id=?", pid), q1("SELECT * FROM medicines WHERE id=?", mid)
    if not p or not m or qty <= 0:
        flash("Choose a patient, a medicine and a quantity.")
    elif m["expiry"] < str(date.today()):
        flash(f"{m['name']} is expired and cannot be sold.")
    elif m["stock"] < qty:
        flash(f"Only {m['stock']} of {m['name']} in stock.")
    else:
        warns = [w for w in ai.check_interactions([m["name"]], p["allergies"], p["age"]) if w["level"] == "HIGH"]
        if warns and f.get("override") != "1":
            flash("Safety warning: " + " ".join(w["msg"] for w in warns) + " Tick 'Override safety warning' to sell anyway.")
        else:
            stock_move(mid, -qty, f"Counter sale to {p['uhid']}")
            add_item(pid, f"Medicine: {m['name']}", "Pharmacy", qty, m["price"])
            log(f"Counter sale {qty} x {m['name']} to {p['uhid']}")
            flash(f"Sold {qty} x {m['name']} ({qty*m['price']:.2f}) and added to {p['name']}'s bill.")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/history")
@roles("pharmacist")
def pharmacy_history():
    mid = num(request.args.get("medicine"))
    rows = q("""SELECT s.*, m.name FROM stock_log s JOIN medicines m ON m.id=s.medicine_id
                WHERE (? IS NULL OR s.medicine_id=?) ORDER BY s.id DESC LIMIT 300""", mid, mid)
    return render_template("pharmacy_log.html", rows=rows, meds=q("SELECT id,name FROM medicines ORDER BY name"), mid=mid)

@app.route("/pharmacy/reorder.csv")
@roles("pharmacist")
def reorder_csv():
    import csv
    # pyrefly: ignore [missing-import]
    from flask import make_response
    rows = q("""SELECT name, category, manufacturer, stock, reorder_level, cost, price FROM medicines
                WHERE stock<=reorder_level ORDER BY category, name""")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Medicine", "Category", "Manufacturer", "In stock", "Reorder level", "Suggested order qty", "Est. cost (INR)"])
    for r in rows:
        order = max(r["reorder_level"] * 3 - r["stock"], r["reorder_level"])
        w.writerow([r["name"], r["category"], r["manufacturer"] or "", r["stock"], r["reorder_level"], order, round(order * (r["cost"] or 0), 2)])
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = f"attachment; filename=reorder_list_{date.today()}.csv"
    return resp

# ============================================================ laboratory
@app.route("/lab")
@roles("lab")
def lab():
    base = """SELECT o.*, t.name tname, t.unit, t.low, t.high, p.name pname, p.uhid, d.name dname FROM lab_orders o
              JOIN lab_tests t ON t.id=o.test_id JOIN patients p ON p.id=o.patient_id JOIN doctors d ON d.id=o.doctor_id """
    done = [dict(r, note=ai.interpret_lab(r["tname"], r["result"], r["low"], r["high"])[1])
            for r in q(base + "WHERE o.status='Completed' ORDER BY o.id DESC LIMIT 20")]
    return render_template("lab.html", pending=q(base + "WHERE o.status='Pending' ORDER BY o.id"), done=done)

@app.route("/lab/<int:oid>/result", methods=["POST"])
@roles("lab")
def lab_result(oid):
    o = q1("""SELECT o.*, t.name, t.low, t.high FROM lab_orders o JOIN lab_tests t ON t.id=o.test_id WHERE o.id=?""", oid)
    val = fnum(request.form["result"])
    if not o or val is None:
        flash("Enter a valid numeric result")
        return redirect(url_for("lab"))
    flag, note = ai.interpret_lab(o["name"], val, o["low"], o["high"])
    run("UPDATE lab_orders SET result=?, flag=?, status='Completed', resulted_at=? WHERE id=?", val, flag, database.now(), oid)
    log(f"Lab result entered: {o['name']} = {val} ({flag})")
    flash(f"Result saved: {o['name']} = {val} -> {flag}. {note}")
    return redirect(url_for("lab"))

# ============================================================ IPD: admissions & beds
@app.route("/admissions", methods=["GET", "POST"])
@roles("receptionist", "doctor")
def admissions():
    if request.method == "POST":
        f = request.form
        if q1("SELECT 1 FROM admissions WHERE patient_id=? AND status='Admitted'", f["patient_id"]):
            flash("This patient is already admitted.")
        elif not q1("SELECT 1 FROM beds WHERE id=? AND occupied=0", f["bed_id"]):
            flash("That bed is no longer free.")
        else:
            run("INSERT INTO admissions(patient_id,bed_id,doctor_id,admit_date,reason) VALUES(?,?,?,?,?)",
                f["patient_id"], f["bed_id"], f["doctor_id"], str(date.today()), f["reason"])
            run("UPDATE beds SET occupied=1 WHERE id=?", f["bed_id"])
            log(f"Admitted patient #{f['patient_id']} to bed #{f['bed_id']}")
            flash("Patient admitted")
        return redirect(url_for("admissions"))
    base = """SELECT a.*, p.name pname, p.uhid, d.name dname, w.name ward, w.charge, b.bed_no FROM admissions a
              JOIN patients p ON p.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id JOIN beds b ON b.id=a.bed_id
              JOIN wards w ON w.id=b.ward_id """
    return render_template("admissions.html", active=q(base + "WHERE a.status='Admitted' ORDER BY a.id DESC"),
        history=q(base + "WHERE a.status='Discharged' ORDER BY a.id DESC LIMIT 15"),
        wards=q("""SELECT w.*, (SELECT COUNT(*) FROM beds WHERE ward_id=w.id) total,
                   (SELECT COUNT(*) FROM beds WHERE ward_id=w.id AND occupied=0) free FROM wards w"""),
        free_beds=q("""SELECT b.id, b.bed_no, w.name ward, w.charge FROM beds b JOIN wards w ON w.id=b.ward_id
                       WHERE b.occupied=0 ORDER BY w.id, b.id"""),
        patients=q("SELECT id,name,uhid FROM patients ORDER BY name"), doctors=q("SELECT * FROM doctors ORDER BY name"),
        today=date.today())

@app.route("/admissions/<int:aid>/discharge", methods=["POST"])
@roles("receptionist", "doctor")
def discharge(aid):
    a = q1("""SELECT a.*, w.name ward, w.charge FROM admissions a JOIN beds b ON b.id=a.bed_id
              JOIN wards w ON w.id=b.ward_id WHERE a.id=?""", aid)
    if a and a["status"] == "Admitted":
        days = max(1, (date.today() - date.fromisoformat(a["admit_date"])).days)
        add_item(a["patient_id"], f"Bed charges - {a['ward']} ({days} day(s))", "Bed", days, a["charge"])
        run("UPDATE admissions SET status='Discharged', discharge_date=?, summary=? WHERE id=?",
            str(date.today()), request.form.get("summary", ""), aid)
        run("UPDATE beds SET occupied=0 WHERE id=?", a["bed_id"])
        log(f"Discharged admission #{aid}")
        flash(f"Patient discharged. Bed charges for {days} day(s) added to the bill.")
    return redirect(url_for("discharge_summary", aid=aid))

@app.route("/admissions/<int:aid>/summary")
@roles()
def discharge_summary(aid):
    a = q1("""SELECT a.*, d.name dname, w.name ward, b.bed_no FROM admissions a JOIN doctors d ON d.id=a.doctor_id
              JOIN beds b ON b.id=a.bed_id JOIN wards w ON w.id=b.ward_id WHERE a.id=?""", aid)
    if not a:
        return redirect(url_for("admissions"))
    return render_template("discharge.html", a=a, p=q1("SELECT * FROM patients WHERE id=?", a["patient_id"]))

# ============================================================ billing
@app.route("/billing")
@roles("receptionist")
def billing():
    rows = q("""SELECT b.*, p.name pname, p.uhid, (SELECT COALESCE(SUM(qty*price),0) FROM bill_items WHERE bill_id=b.id)-b.discount total
                FROM bills b JOIN patients p ON p.id=b.patient_id ORDER BY (b.status='Pending') DESC, b.id DESC""")
    return render_template("billing.html", bills=rows, dues=dues(), revenue=revenue())

@app.route("/billing/new/<int:pid>")
@roles("receptionist")
def new_bill(pid):
    return redirect(url_for("bill", bid=open_bill(pid)))

def load_bill(bid):
    b = q1("SELECT b.*, p.name pname, p.uhid, p.age, p.phone, p.email FROM bills b JOIN patients p ON p.id=b.patient_id WHERE b.id=?", bid)
    return b, q("SELECT * FROM bill_items WHERE bill_id=?", bid), bill_totals(bid)

@app.route("/billing/<int:bid>")
@roles("receptionist")
def bill(bid):
    b, items, (gross, disc, net) = load_bill(bid)
    if not b:
        return redirect(url_for("billing"))
    return render_template("bill.html", b=b, items=items, gross=gross, disc=disc, net=net)

@app.route("/billing/<int:bid>/item", methods=["POST"])
@roles("receptionist")
def bill_item(bid):
    f = request.form
    run("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
        bid, f["description"], "Other", num(f["qty"]) or 1, fnum(f["price"]) or 0)
    return redirect(url_for("bill", bid=bid))

@app.route("/billing/<int:bid>/payment")
@roles("receptionist")
def payment_page(bid):
    b, items, (gross, disc, net) = load_bill(bid)
    if not b:
        return redirect(url_for("billing"))
    if b["status"] == "Paid":
        return redirect(url_for("invoice", bid=bid))
    upi_id = os.environ.get("MEDICARE_UPI_ID", "").strip()
    qr_data = None
    if upi_id and "@" in upi_id:
        # UPI intent QR: scan with a UPI app. This does not verify payment automatically.
        params = urllib.parse.urlencode({"pa": upi_id, "pn": "MediCare Hospital", "am": f"{net:.2f}",
                                         "cu": "INR", "tn": f"MediCare Bill {bid}"})
        upi_uri = "upi://pay?" + params
        try:
            import qrcode
            qr = qrcode.make(upi_uri)
            buf = io.BytesIO()
            qr.save(buf, format="PNG")
            qr_data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except ImportError:
            qr_data = None
    return render_template("payment.html", b=b, items=items, gross=gross, disc=disc, net=net,
                           upi_id=upi_id, qr_data=qr_data)

@app.route("/billing/<int:bid>/pay", methods=["POST"])
@roles("receptionist")
def pay_bill(bid):
    gross = bill_totals(bid)[0]
    disc = min(max(fnum(request.form.get("discount")) or 0, 0), gross)
    run("UPDATE bills SET status='Paid', mode=?, discount=?, paid_on=? WHERE id=?",
        request.form["mode"], disc, str(date.today()), bid)
    log(f"Bill #{bid} paid ({request.form['mode']})")
    # Automatic PDF receipt by email. A new address typed at payment time is saved to the patient record.
    typed = (request.form.get("email") or "").strip()
    b = q1("SELECT b.patient_id, p.email FROM bills b JOIN patients p ON p.id=b.patient_id WHERE b.id=?", bid)
    if typed and EMAIL_RE.fullmatch(typed) and typed != (b["email"] or ""):
        run("UPDATE patients SET email=? WHERE id=?", typed, b["patient_id"])
    to_addr = typed if EMAIL_RE.fullmatch(typed) else (b["email"] or "")
    if request.form.get("send_email", "1") == "1" and to_addr:
        em = send_invoice_email_async(bid, to_addr)
        if em == "queued":
            flash(f"PDF receipt is being emailed to {to_addr}.")
        elif em == "not_configured":
            flash("Email is not set up yet (see README: PDF INVOICE + EMAIL DELIVERY), so no email was sent.")
    elif request.form.get("send_email", "1") == "1":
        flash("No patient email on file - add one on the invoice page to email the PDF.")
    # If Meta WhatsApp Cloud API credentials are configured, send the receipt now.
    # Otherwise, the invoice page offers a click-to-send WhatsApp fallback.
    wa_result = send_whatsapp_bill(bid)
    if wa_result == "sent":
        flash("Payment received. Bill marked as Paid, and WhatsApp receipt sent.")
        log(f"WhatsApp receipt sent for bill #{bid}")
    elif wa_result == "manual":
        flash("Payment received. Bill marked as Paid. Use 'Send Bill via WhatsApp' on the invoice to send it manually.")
    elif wa_result == "no_phone":
        flash("Payment received. Bill marked as Paid, but the patient has no phone number saved.")
    elif wa_result == "invalid_phone":
        flash("Payment received. Bill marked as Paid, but the patient's phone number is invalid. Update it and send via WhatsApp.")
    else:
        flash("Payment received. Bill marked as Paid, but WhatsApp API sending failed. Check API setup or send from WhatsApp manually.")
    return redirect(url_for("invoice", bid=bid))

@app.route("/billing/<int:bid>/invoice")
@roles("receptionist")
def invoice(bid):
    b, items, (gross, disc, net) = load_bill(bid)
    if not b:
        return redirect(url_for("billing"))
    return render_template("invoice.html", b=b, items=items, gross=gross, disc=disc, net=net)

@app.route("/billing/<int:bid>/invoice.pdf")
@roles("receptionist")
def download_invoice_pdf(bid):
    from flask import make_response, abort
    pdf = invoice_pdf_bytes(bid)
    if pdf is None:
        abort(404)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=MediCare_Invoice_{bid}.pdf'
    return response

@app.route("/billing/<int:bid>/email-invoice", methods=["POST"])
@roles("receptionist")
def email_invoice(bid):
    recipient = (request.form.get('email') or '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', recipient):
        flash('Enter a valid patient email address.')
        return redirect(url_for('invoice', bid=bid))
    pb = q1("SELECT patient_id FROM bills WHERE id=?", bid)
    if pb and request.form.get('save_email'):
        run("UPDATE patients SET email=? WHERE id=?", recipient, pb["patient_id"])
    result = send_invoice_email(bid, recipient)
    if result == 'sent':
        flash(f'PDF invoice emailed to {recipient}.')
        log(f'PDF invoice #{bid} emailed to {recipient}')
    elif result == 'not_configured':
        flash('Email is not configured yet. ' + email_diagnosis())
    elif result == 'auth_failed':
        flash('Gmail rejected the login. Use a Google App Password (16 characters) in .env, not your normal Gmail password.')
    else:
        flash('Email sending failed. Check the SMTP settings and try again.')
    return redirect(url_for('invoice', bid=bid))

@app.route("/billing/<int:bid>/whatsapp-invoice", methods=["POST"])
@roles("receptionist")
def whatsapp_invoice_pdf(bid):
    result = send_whatsapp_pdf(bid)
    if result == 'sent':
        flash('PDF invoice sent via WhatsApp.')
        log(f'PDF invoice #{bid} sent via WhatsApp')
        return redirect(url_for('invoice', bid=bid))
    if result == 'not_configured':
        flash('WhatsApp PDF auto-send needs Meta Cloud API credentials. Download the PDF and attach it manually in WhatsApp.')
        return redirect(url_for('invoice', bid=bid))
    flash('WhatsApp PDF could not be sent. Check the patient phone number and WhatsApp API setup.')
    return redirect(url_for('invoice', bid=bid))

@app.route("/billing/<int:bid>/send-pdf", methods=["POST"])
@roles("receptionist")
def send_bill_pdf_email(bid):
    """Send the bill PDF to any email address typed by the user (from the bill page)."""
    recipient = (request.form.get('email') or '').strip()
    # Validate email format before doing anything
    if not EMAIL_RE.fullmatch(recipient):
        flash('Please enter a valid email address (e.g. someone@example.com).')
        return redirect(url_for('bill', bid=bid))
    # Make sure the bill actually exists
    b = q1("SELECT id FROM bills WHERE id=?", bid)
    if not b:
        flash('Bill not found.')
        return redirect(url_for('billing'))
    # Verify email is configured before attempting
    if not email_configured():
        flash('Email is not set up yet. ' + email_diagnosis())
        return redirect(url_for('bill', bid=bid))
    result = send_invoice_email(bid, recipient)
    if result == 'sent':
        flash(f'Bill PDF successfully sent to {recipient}.')
        log(f'Bill #{bid} PDF emailed to {recipient}')
    elif result == 'auth_failed':
        flash('Gmail rejected the login. Use a Google App Password (16 characters) in .env, not your normal Gmail password.')
    elif result == 'missing_bill':
        flash('Could not generate the PDF — bill has no items.')
    else:
        flash('Email sending failed. Check your SMTP settings in .env and try again.')
    return redirect(url_for('bill', bid=bid))

# ============================================================ reports / staff / audit (admin)
def report_range():
    """Date range from ?from=&to= (or ?range=7|30|90|365); defaults to the last 30 days."""
    t = date.today()
    d_to, d_from = request.args.get("to"), request.args.get("from")
    preset = num(request.args.get("range"))
    try:
        end = date.fromisoformat(d_to) if d_to else t
        start = date.fromisoformat(d_from) if d_from else end - timedelta(days=(preset or 30) - 1)
    except ValueError:
        end, start = t, t - timedelta(days=29)
    if start > end:
        start, end = end, start
    return start, end

def paid_bills(start, end):
    return q("""SELECT b.id, b.paid_on, b.mode, b.discount, b.patient_id, p.name pname, p.uhid,
                (SELECT COALESCE(SUM(qty*price),0) FROM bill_items WHERE bill_id=b.id) gross
                FROM bills b JOIN patients p ON p.id=b.patient_id
                WHERE b.status='Paid' AND b.paid_on BETWEEN ? AND ? ORDER BY b.paid_on, b.id""", str(start), str(end))

@app.route("/reports")
@roles()
def reports():
    if session["role"] != "admin":
        flash("Reports are for admin only.")
        return redirect(url_for("dashboard"))
    start, end = report_range()
    a, b = str(start), str(end)
    days = (end - start).days + 1
    pairs = lambda rows: [(r[0], r[1]) for r in rows]
    bills = paid_bills(start, end)
    nets = [r["gross"] - (r["discount"] or 0) for r in bills]
    total = sum(nets)
    # revenue trend: daily up to 31 days, otherwise monthly
    trend = {}
    for r, n in zip(bills, nets):
        key = r["paid_on"][5:] if days <= 31 else r["paid_on"][:7]
        trend[key] = trend.get(key, 0) + n
    if days <= 31:
        trend_rows = [(str(start + timedelta(days=i))[5:], round(trend.get(str(start + timedelta(days=i))[5:], 0))) for i in range(days)]
    else:
        trend_rows = [(k, round(v)) for k, v in sorted(trend.items())]
    by_mode = {}
    for r, n in zip(bills, nets):
        by_mode[r["mode"] or "-"] = by_mode.get(r["mode"] or "-", 0) + n
    appts = q1("""SELECT COUNT(*), COALESCE(SUM(status='Completed'),0), COALESCE(SUM(status='Cancelled'),0) FROM appointments WHERE adate BETWEEN ? AND ?""", a, b)
    prev_start = start - timedelta(days=days)
    prev_total = sum(r["gross"] - (r["discount"] or 0) for r in paid_bills(prev_start, start - timedelta(days=1)))
    change = round((total - prev_total) * 100 / prev_total, 1) if prev_total else None
    pending = q("""SELECT b.id, b.bdate, p.name pname, p.uhid, p.phone, p.email,
                   (SELECT COALESCE(SUM(qty*price),0) FROM bill_items WHERE bill_id=b.id)-b.discount amount
                   FROM bills b JOIN patients p ON p.id=b.patient_id WHERE b.status='Pending' ORDER BY b.bdate""")
    pending = [dict(r, age=(date.today() - date.fromisoformat(r["bdate"])).days) for r in pending if r["amount"] > 0]
    t = date.today()
    soon, today = str(t + timedelta(days=60)), str(t)
    age_groups = pairs(q("""SELECT CASE WHEN age<13 THEN 'Child (0-12)' WHEN age<20 THEN 'Teen (13-19)' WHEN age<40 THEN 'Adult (20-39)'
                            WHEN age<60 THEN 'Middle (40-59)' ELSE 'Senior (60+)' END g, COUNT(*) FROM patients GROUP BY g ORDER BY MIN(age)"""))
    return render_template("reports.html", start=a, end=b, days=days, total=total, dues=dues(),
        bills_n=len(bills), avg_bill=(total / len(bills)) if bills else 0, discounts=sum((r["discount"] or 0) for r in bills),
        change=change, prev_total=prev_total,
        new_patients=q1("SELECT COUNT(*) FROM patients WHERE substr(created_at,1,10) BETWEEN ? AND ?", a, b)[0],
        appts_n=appts[0], appts_done=appts[1], appts_cancel=appts[2],
        admissions_n=q1("SELECT COUNT(*) FROM admissions WHERE admit_date BETWEEN ? AND ?", a, b)[0],
        trend=trend_rows, trend_label="Daily revenue" if days <= 31 else "Monthly revenue",
        by_mode=[(k, round(v)) for k, v in sorted(by_mode.items(), key=lambda x: -x[1])],
        by_cat=pairs(q("""SELECT i.category, ROUND(SUM(i.qty*i.price)) FROM bill_items i JOIN bills b ON b.id=i.bill_id
                          WHERE b.status='Paid' AND b.paid_on BETWEEN ? AND ? GROUP BY i.category ORDER BY 2 DESC""", a, b)),
        by_dept=pairs(q("""SELECT d.department, COUNT(a.id) FROM doctors d LEFT JOIN appointments a ON a.doctor_id=d.id AND a.adate BETWEEN ? AND ?
                           GROUP BY d.department ORDER BY 2 DESC""", a, b)),
        doctors=q("""SELECT d.name, d.department, COUNT(a.id) total, COALESCE(SUM(a.status='Completed'),0) done,
                     COALESCE(SUM(a.status='Cancelled'),0) cancelled FROM doctors d
                     LEFT JOIN appointments a ON a.doctor_id=d.id AND a.adate BETWEEN ? AND ? GROUP BY d.id ORDER BY total DESC""", a, b),
        top_meds=q("""SELECT REPLACE(i.description,'Medicine: ','') name, SUM(i.qty) units, ROUND(SUM(i.qty*i.price)) amount
                      FROM bill_items i JOIN bills b ON b.id=i.bill_id WHERE i.category='Pharmacy' AND b.bdate BETWEEN ? AND ?
                      GROUP BY i.description ORDER BY units DESC LIMIT 8""", a, b),
        top_labs=pairs(q("""SELECT t.name, COUNT(*) FROM lab_orders o JOIN lab_tests t ON t.id=o.test_id
                            WHERE substr(o.ordered_at,1,10) BETWEEN ? AND ? GROUP BY t.name ORDER BY 2 DESC LIMIT 6""", a, b)),
        lab_flags=pairs(q("""SELECT COALESCE(flag,'Pending'), COUNT(*) FROM lab_orders WHERE substr(ordered_at,1,10) BETWEEN ? AND ? GROUP BY 1""", a, b)),
        gender=pairs(q("SELECT gender, COUNT(*) FROM patients GROUP BY gender")), age_groups=age_groups,
        occupancy=pairs(q("""SELECT w.name, COUNT(CASE WHEN b.occupied=1 THEN 1 END) FROM wards w JOIN beds b ON b.ward_id=w.id GROUP BY w.name""")),
        top_patients=q("""SELECT p.name, p.uhid, COUNT(*) visits FROM appointments a JOIN patients p ON p.id=a.patient_id
                          WHERE a.adate BETWEEN ? AND ? GROUP BY p.id ORDER BY visits DESC LIMIT 5""", a, b),
        stock=q1("""SELECT COUNT(*) n, COALESCE(SUM(stock*price),0) retail, COALESCE(SUM(stock<=reorder_level),0) low,
                    COALESCE(SUM(expiry<?),0) expired, COALESCE(SUM(expiry<=? AND expiry>=?),0) soon FROM medicines""", today, soon, today),
        low_meds=q("SELECT name, stock, reorder_level, expiry FROM medicines WHERE stock<=reorder_level ORDER BY stock LIMIT 8"),
        expiring=q("SELECT name, stock, expiry FROM medicines WHERE expiry<=? ORDER BY expiry LIMIT 8", soon),
        email_sent=q1("SELECT COUNT(*) FROM bills WHERE email_status LIKE 'sent%'")[0],
        email_failed=q1("SELECT COUNT(*) FROM bills WHERE email_status IS NOT NULL AND email_status NOT LIKE 'sent%'")[0],
        pending=pending)

@app.route("/reports.csv")
@roles()
def reports_csv():
    """Download paid bills in the selected date range for Excel / accounts."""
    import csv
    from flask import make_response
    if session["role"] != "admin":
        return redirect(url_for("dashboard"))
    start, end = report_range()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Bill no", "Paid on", "UHID", "Patient", "Payment mode", "Gross (INR)", "Discount (INR)", "Net (INR)"])
    for r in paid_bills(start, end):
        w.writerow([r["id"], r["paid_on"], r["uhid"], r["pname"], r["mode"], f'{r["gross"]:.2f}', f'{(r["discount"] or 0):.2f}', f'{r["gross"]-(r["discount"] or 0):.2f}'])
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = f"attachment; filename=revenue_{start}_to_{end}.csv"
    return resp

@app.route("/staff", methods=["GET", "POST"])
@roles()
def staff():
    if session["role"] != "admin":
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        f = request.form
        if q1("SELECT 1 FROM users WHERE username=?", f["username"]):
            flash("Username already exists")
        else:
            run("INSERT INTO users(username,password,role,full_name,doctor_id) VALUES(?,?,?,?,?)", f["username"],
                generate_password_hash(f["password"]), f["role"], f["full_name"], num(f.get("doctor_id")))
            log(f"Created user {f['username']} ({f['role']})")
            flash("User created")
        return redirect(url_for("staff"))
    return render_template("staff.html", users=q("SELECT * FROM users ORDER BY role"), doctors=q("SELECT * FROM doctors"))

@app.route("/staff/<int:uid>/delete", methods=["POST"])
@roles()
def delete_user(uid):
    u = q1("SELECT * FROM users WHERE id=?", uid)
    if session["role"] == "admin" and u and u["username"] != session["user"]:
        run("DELETE FROM users WHERE id=?", uid)
        log(f"Deleted user {u['username']}")
    return redirect(url_for("staff"))

@app.route("/audit")
@roles()
def audit():
    if session["role"] != "admin":
        return redirect(url_for("dashboard"))
    return render_template("audit.html", rows=q("SELECT * FROM audit ORDER BY id DESC LIMIT 100"))

# ============================================================ AI pages & APIs
@app.route("/ai")
@roles()
def ai_page():
    return render_template("ai.html")

@app.route("/api/symptoms", methods=["POST"])
@roles()
def api_symptoms():
    return jsonify(ai.check_symptoms(request.json.get("text", "")))

@app.route("/api/chat", methods=["POST"])
def api_chat():
    return jsonify({"reply": ai.chatbot_reply(request.json.get("message", ""))})

@app.route("/api/suggest")
@roles()
def api_suggest():
    r = ai.suggest_slot(conn(), SLOTS, request.args.get("department", ""), request.args.get("date", ""))
    return jsonify(r or {"error": "No free slot for that department/date"})

@app.route("/api/interactions", methods=["POST"])
@roles()
def api_interactions():
    j = request.json or {}
    allergies, age = j.get("allergies", ""), None
    if j.get("patient_id"):
        p = q1("SELECT * FROM patients WHERE id=?", j["patient_id"])
        if p:
            allergies, age = p["allergies"], p["age"]
    if j.get("ids"):
        names = [r["name"] for i in j["ids"] if i for r in [q1("SELECT name FROM medicines WHERE id=?", i)] if r]
    else:
        names = [x.strip() for x in (j.get("drugs") or "").split(",") if x.strip()]
    return jsonify({"warnings": ai.check_interactions(names, allergies, age)})

if __name__ == "__main__":
    database.init_db()
    print("EMAIL:", "ready, sending as " + smtp_settings()["user"] if email_configured() else "NOT READY - " + email_diagnosis())
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(debug=False)
