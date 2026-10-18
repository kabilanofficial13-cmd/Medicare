"""AI module: rule-based expert system (runs offline, no API key needed)."""
import re
from datetime import date, timedelta

DEPT_KEYWORDS = {
    "Cardiology": ["chest pain", "palpitation", "heart", "breathless", "shortness of breath", "chest tightness", "நெஞ்சு வலி"],
    "Neurology": ["headache", "migraine", "dizz", "seizure", "numbness", "faint", "stroke", "memory", "தலைவலி", "தலை சுற்றல்"],
    "Orthopedics": ["bone", "fracture", "joint", "back pain", "knee", "sprain", "shoulder", "neck pain", "முதுகு வலி", "மூட்டு வலி"],
    "Dermatology": ["rash", "itch", "skin", "acne", "allergy", "hair fall", "pimple", "தோல்", "அரிப்பு"],
    "ENT": ["ear", "throat", "nose", "sinus", "tonsil", "cough", "cold", "hearing", "இருமல்", "தொண்டை", "சளி"],
    "General Medicine": ["fever", "vomit", "diarrhea", "fatigue", "weakness", "body pain", "stomach", "nausea", "காய்ச்சல்", "வாந்தி", "வயிற்று வலி"],
}
DEPARTMENTS = list(DEPT_KEYWORDS)
EMERGENCY = ["chest pain", "unconscious", "severe bleeding", "can't breathe", "cannot breathe",
             "shortness of breath", "seizure", "stroke", "suicid", "poison", "accident", "நெஞ்சு வலி", "மூச்சுத் திணறல்"]

# ---------------------------------------------------------------- 1. symptom checker / triage
def check_symptoms(text):
    t = text.lower()
    scores = {d: sum(1 for k in kws if k in t) for d, kws in DEPT_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "General Medicine"
    red = [k for k in EMERGENCY if k in t]
    if red:
        urgency, advice = "HIGH", "Possible emergency. Go to the Emergency ward immediately."
    elif "fever" in t and any(w in t for w in ["days", "week", "high"]):
        urgency, advice = "MEDIUM", "Consult a doctor within 24 hours."
    elif scores[best] >= 2:
        urgency, advice = "MEDIUM", "Book an appointment soon."
    else:
        urgency, advice = "LOW", "Routine consultation is enough."
    return {"department": best, "urgency": urgency, "advice": advice,
            "matched": [k for k in DEPT_KEYWORDS[best] if k in t], "red_flags": red}

# ---------------------------------------------------------------- 2. vitals risk + trend
def assess_vitals(v):
    if not v:
        return "Unknown", ["No vitals recorded yet"]
    alerts, level = [], 0
    def flag(lv, msg):
        nonlocal level
        level = max(level, lv)
        alerts.append(msg)
    s, d, g, h, o, t = (v["bp_sys"], v["bp_dia"], v["sugar"], v["heart_rate"], v["spo2"], v["temp"])
    if s and d:
        if s >= 180 or d >= 120: flag(3, f"Hypertensive crisis: BP {s}/{d}")
        elif s >= 140 or d >= 90: flag(2, f"High blood pressure: {s}/{d}")
        elif s < 90 or d < 60: flag(2, f"Low blood pressure: {s}/{d}")
    if g:
        if g >= 300: flag(3, f"Very high blood sugar: {g} mg/dL")
        elif g >= 200: flag(2, f"High blood sugar: {g} mg/dL")
        elif g < 70: flag(2, f"Low blood sugar (hypoglycemia): {g} mg/dL")
    if h:
        if h > 130 or h < 40: flag(3, f"Critical heart rate: {h} bpm")
        elif h > 100 or h < 60: flag(1, f"Abnormal heart rate: {h} bpm")
    if o:
        if o < 90: flag(3, f"Critically low oxygen: SpO2 {o}%")
        elif o < 94: flag(2, f"Low oxygen: SpO2 {o}%")
    if t:
        if t >= 41 or t < 33: flag(3, f"Dangerous temperature: {t} C")
        elif t >= 39.5: flag(2, f"High fever: {t} C")
        elif t >= 38: flag(1, f"Fever: {t} C")
        elif t < 35: flag(2, f"Low body temperature: {t} C")
    return ["Normal", "Mild", "High", "Critical"][level], alerts

def vitals_trend(rows):
    """rows: newest first. Compares the last two readings."""
    if len(rows) < 2:
        return []
    a, b, out = rows[0], rows[1], []
    if a["bp_sys"] and b["bp_sys"] and a["bp_sys"] - b["bp_sys"] >= 20:
        out.append(f"Trend: systolic BP rose by {a['bp_sys'] - b['bp_sys']} since previous reading")
    if a["sugar"] and b["sugar"] and a["sugar"] - b["sugar"] >= 50:
        out.append(f"Trend: blood sugar rose by {a['sugar'] - b['sugar']} mg/dL since previous reading")
    if a["spo2"] and b["spo2"] and b["spo2"] - a["spo2"] >= 3:
        out.append(f"Trend: oxygen saturation dropped by {b['spo2'] - a['spo2']}% since previous reading")
    return out

# ---------------------------------------------------------------- 3. clinical summary
def make_summary(p, level, alerts, consults, labs, adms):
    s = f"{p['name']} ({p['uhid']}), {p['age']}-year-old {p['gender']}, blood group {p['blood_group'] or 'unknown'}. "
    if p["allergies"] and p["allergies"].strip().lower() not in ("", "none"):
        s += f"Known allergies: {p['allergies']}. "
    if consults:
        c = consults[0]
        s += f"{len(consults)} consultation(s) on record; last visit {c['cdate']} with diagnosis '{c['diagnosis']}'. "
    abn = [l for l in labs if l["flag"] and l["flag"] != "Normal"]
    if abn:
        s += "Abnormal lab results: " + ", ".join(f"{l['name']} ({l['flag']})" for l in abn[:4]) + ". "
    if any(a["status"] == "Admitted" for a in adms):
        s += "Currently admitted in hospital. "
    s += ("Vitals alerts: " + "; ".join(alerts) + ". ") if level not in ("Normal", "Unknown") else "Vitals are within normal limits. "
    return s + f"Overall risk level: {level}."

# ---------------------------------------------------------------- 4. drug interaction + allergy checker
INTERACTIONS = [
    (("aspirin", "warfarin"), "HIGH", "Aspirin + Warfarin: major bleeding risk."),
    (("ibuprofen", "warfarin"), "HIGH", "Ibuprofen + Warfarin: major bleeding risk."),
    (("diclofenac", "warfarin"), "HIGH", "Diclofenac + Warfarin: major bleeding risk."),
    (("aspirin", "ibuprofen"), "MEDIUM", "Aspirin + Ibuprofen: ibuprofen can reduce aspirin's heart protection; stomach bleeding risk."),
    (("aspirin", "diclofenac"), "MEDIUM", "Aspirin + Diclofenac: increased stomach bleeding risk."),
    (("ibuprofen", "diclofenac"), "MEDIUM", "Two NSAIDs together: stomach bleeding and kidney risk."),
    (("warfarin", "azithromycin"), "MEDIUM", "Azithromycin may increase the effect of Warfarin (monitor INR)."),
    (("clopidogrel", "omeprazole"), "MEDIUM", "Omeprazole reduces Clopidogrel effect; prefer Pantoprazole."),
    (("atenolol", "amlodipine"), "LOW", "Atenolol + Amlodipine: may cause low BP or slow heart rate; monitor."),
    (("atorvastatin", "azithromycin"), "LOW", "Azithromycin may raise statin levels; watch for muscle pain."),
]
ALLERGY_MAP = {"penicillin": ["amoxicillin", "ampicillin", "penicillin"], "sulfa": ["sulfamethoxazole", "cotrimoxazole"],
               "nsaid": ["ibuprofen", "diclofenac", "aspirin"], "aspirin": ["aspirin"], "paracetamol": ["paracetamol"]}

def check_interactions(names, allergies="", age=None):
    low = [n.lower() for n in names]
    out = []
    for (a, b), lvl, msg in INTERACTIONS:
        if any(a in n for n in low) and any(b in n for n in low):
            out.append({"level": lvl, "msg": msg})
    al = (allergies or "").lower()
    for key, drugs in ALLERGY_MAP.items():
        if key in al:
            for n in low:
                if any(d in n for d in drugs):
                    out.append({"level": "HIGH", "msg": f"ALLERGY ALERT: patient is allergic to {key}; '{n}' is related."})
    if len(set(low)) < len(low):
        out.append({"level": "MEDIUM", "msg": "Duplicate medicine selected."})
    if age is not None and age < 12 and any("aspirin" in n for n in low):
        out.append({"level": "HIGH", "msg": "Aspirin is not recommended for children under 12 (Reye's syndrome)."})
    if age is not None and age > 65 and any(n.startswith(("diclofenac", "ibuprofen")) for n in low):
        out.append({"level": "MEDIUM", "msg": "NSAID in elderly patient: higher risk of kidney and stomach problems."})
    seen, uniq = set(), []
    for o in out:
        if o["msg"] not in seen:
            seen.add(o["msg"]); uniq.append(o)
    return uniq

# ---------------------------------------------------------------- 5. lab result interpretation
LAB_NOTES = {
    "Hemoglobin": {"Low": "Low hemoglobin suggests anaemia (iron/B12 deficiency or blood loss).", "High": "High hemoglobin: dehydration or polycythemia; review."},
    "Fasting Blood Sugar": {"High": "High fasting sugar: possible diabetes / pre-diabetes; confirm with HbA1c.", "Low": "Low sugar (hypoglycemia): check medication and diet."},
    "HbA1c": {"High": "High HbA1c indicates poor long-term sugar control / diabetes."},
    "Total Cholesterol": {"High": "High cholesterol increases heart disease risk; diet and statin review."},
    "Serum Creatinine": {"High": "High creatinine: possible reduced kidney function."},
    "Blood Urea": {"High": "High urea: dehydration or kidney problem."},
    "WBC Count": {"High": "High WBC suggests infection or inflammation.", "Low": "Low WBC: reduced immunity; review for viral infection or drugs."},
    "Platelet Count": {"Low": "Low platelets: bleeding risk; consider dengue / viral causes.", "High": "High platelets: inflammation or iron deficiency."},
    "SGPT (ALT)": {"High": "High SGPT indicates liver stress (fatty liver, hepatitis, drugs)."},
    "TSH": {"High": "High TSH suggests hypothyroidism.", "Low": "Low TSH suggests hyperthyroidism."},
}

def interpret_lab(name, value, low, high):
    if value is None:
        return "", ""
    if value < low: flag = "Low"
    elif value > high: flag = "High"
    else: return "Normal", "Within normal range."
    if (flag == "High" and high and value > high * 1.5) or (flag == "Low" and low and value < low * 0.6):
        flag = "Critical"
    side = "Low" if value < low else "High"
    note = LAB_NOTES.get(name, {}).get(side, f"{name} is {side.lower()}; clinical correlation advised.")
    if flag == "Critical":
        note = "CRITICAL VALUE - inform the doctor immediately. " + note
    return flag, note

# ---------------------------------------------------------------- 6. smart appointment slot
def suggest_slot(db, slots, department, adate):
    best = None
    for d in db.execute("SELECT * FROM doctors WHERE department=?", (department,)).fetchall():
        taken = {r["atime"] for r in db.execute(
            "SELECT atime FROM appointments WHERE doctor_id=? AND adate=? AND status!='Cancelled'", (d["id"], adate))}
        free = [s for s in slots if s not in taken]
        if free and (best is None or len(taken) < best[2]):
            best = (d, free[0], len(taken))
    return {"doctor_id": best[0]["id"], "doctor": best[0]["name"], "time": best[1]} if best else None

# ---------------------------------------------------------------- 7. chatbot
FAQ = [
    (["visit", "visitor"], "Visiting hours: 4 PM to 6 PM. Only 2 visitors per patient."),
    (["time", "timing", "open", "hours"], "OPD runs 9 AM to 5 PM (Mon-Sat). Emergency and pharmacy are open 24x7."),
    (["emergency", "ambulance"], "Emergency and ambulance: call 108 or the hospital helpline 044-0000-0000."),
    (["department", "specialist", "doctor"], "We have Cardiology, Neurology, Orthopedics, Dermatology, ENT and General Medicine."),
    (["appointment", "book"], "Book at the reception or the Appointments page. Smart Suggest finds the first free slot."),
    (["bill", "payment", "pay", "insurance"], "Pay at the billing counter by cash, card or UPI. The insurance desk is on the ground floor."),
    (["lab", "test", "report", "blood"], "Laboratory is on the first floor. Reports are ready the same day for most tests."),
    (["pharmacy", "medicine"], "The pharmacy is next to the main entrance and open 24x7."),
    (["icu", "bed", "admit", "ward"], "For admission contact the reception. We have General Ward, Private Rooms and ICU."),
    (["location", "address", "where"], "We are at 12, Main Road, Tambaram, Chennai."),
    (["hello", "hi", "hey"], "Hello! Ask me about timings, departments, appointments, lab, pharmacy or billing."),
]

def chatbot_reply(msg):
    m = msg.lower()
    for keys, ans in FAQ:
        if any(re.search(r"\b" + re.escape(k), m) for k in keys):
            return ans
    return "Sorry, I didn't get that. Try asking about timings, departments, appointments, lab, pharmacy or billing."

# ---------------------------------------------------------------- 8. dashboard insights
def insights(db):
    out = []
    rows = db.execute("""SELECT p.name, v.* FROM vitals v JOIN patients p ON p.id=v.patient_id
                         WHERE v.id IN (SELECT MAX(id) FROM vitals GROUP BY patient_id)""").fetchall()
    risky = [(r["name"], assess_vitals(r)[0]) for r in rows]
    risky = [x for x in risky if x[1] in ("High", "Critical")]
    if risky:
        out.append(("HIGH", f"{len(risky)} patient(s) with high-risk vitals: " + ", ".join(f"{n} ({l})" for n, l in risky)))
    low = db.execute("SELECT name, stock FROM medicines WHERE stock<=reorder_level").fetchall()
    if low:
        out.append(("MEDIUM", "Low stock, reorder needed: " + ", ".join(f"{m['name']} ({m['stock']} left)" for m in low)))
    soon = db.execute("SELECT name, expiry FROM medicines WHERE expiry<=?", (str(date.today() + timedelta(days=60)),)).fetchall()
    if soon:
        out.append(("MEDIUM", "Expiring within 60 days: " + ", ".join(f"{m['name']} ({m['expiry']})" for m in soon)))
    tot = db.execute("SELECT COUNT(*) FROM beds").fetchone()[0]
    occ = db.execute("SELECT COUNT(*) FROM beds WHERE occupied=1").fetchone()[0]
    pct = round(occ * 100 / tot) if tot else 0
    out.append(("HIGH" if pct >= 80 else "LOW", f"Bed occupancy is {pct}% ({occ}/{tot}). " + ("Plan discharges / extra beds." if pct >= 80 else "Capacity is comfortable.")))
    pend = db.execute("SELECT COUNT(*) FROM lab_orders WHERE status='Pending'").fetchone()[0]
    if pend:
        out.append(("LOW", f"{pend} lab test(s) waiting for results."))
    d0 = str(date.today() - timedelta(days=6))
    n = db.execute("SELECT COUNT(*) FROM appointments WHERE adate>=? AND adate<=?", (d0, str(date.today()))).fetchone()[0]
    out.append(("LOW", f"Forecast: about {max(1, round(n / 7))} appointments per day expected (7-day moving average)."))
    return out
