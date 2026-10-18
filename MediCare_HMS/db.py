"""Database layer: schema + demo data (SQLite)."""
import os, sqlite3
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hospital.db")
SLOTS = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT, full_name TEXT, doctor_id INTEGER);
CREATE TABLE IF NOT EXISTS doctors(id INTEGER PRIMARY KEY, name TEXT, department TEXT, qualification TEXT, phone TEXT, fee REAL);
CREATE TABLE IF NOT EXISTS patients(id INTEGER PRIMARY KEY, uhid TEXT UNIQUE, name TEXT, age INTEGER, gender TEXT, blood_group TEXT,
    phone TEXT, address TEXT, emergency_contact TEXT, allergies TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS vitals(id INTEGER PRIMARY KEY, patient_id INTEGER, bp_sys INTEGER, bp_dia INTEGER, sugar INTEGER,
    heart_rate INTEGER, spo2 INTEGER, temp REAL, recorded_at TEXT, recorded_by TEXT);
CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY, patient_id INTEGER, doctor_id INTEGER, adate TEXT, atime TEXT,
    reason TEXT, status TEXT DEFAULT 'Scheduled');
CREATE TABLE IF NOT EXISTS consultations(id INTEGER PRIMARY KEY, appointment_id INTEGER, patient_id INTEGER, doctor_id INTEGER,
    cdate TEXT, symptoms TEXT, diagnosis TEXT, notes TEXT, advice TEXT);
CREATE TABLE IF NOT EXISTS medicines(id INTEGER PRIMARY KEY, name TEXT UNIQUE, category TEXT, stock INTEGER, price REAL,
    reorder_level INTEGER, expiry TEXT);
CREATE TABLE IF NOT EXISTS prescriptions(id INTEGER PRIMARY KEY, consultation_id INTEGER, patient_id INTEGER, medicine_id INTEGER,
    dosage TEXT, days INTEGER, qty INTEGER, dispensed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS lab_tests(id INTEGER PRIMARY KEY, name TEXT UNIQUE, price REAL, unit TEXT, low REAL, high REAL);
CREATE TABLE IF NOT EXISTS lab_orders(id INTEGER PRIMARY KEY, patient_id INTEGER, test_id INTEGER, doctor_id INTEGER,
    consultation_id INTEGER, ordered_at TEXT, status TEXT DEFAULT 'Pending', result REAL, flag TEXT, resulted_at TEXT);
CREATE TABLE IF NOT EXISTS wards(id INTEGER PRIMARY KEY, name TEXT UNIQUE, charge REAL);
CREATE TABLE IF NOT EXISTS beds(id INTEGER PRIMARY KEY, ward_id INTEGER, bed_no TEXT, occupied INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS admissions(id INTEGER PRIMARY KEY, patient_id INTEGER, bed_id INTEGER, doctor_id INTEGER, admit_date TEXT,
    discharge_date TEXT, reason TEXT, status TEXT DEFAULT 'Admitted', summary TEXT);
CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, patient_id INTEGER, bdate TEXT, status TEXT DEFAULT 'Pending',
    mode TEXT, discount REAL DEFAULT 0, paid_on TEXT);
CREATE TABLE IF NOT EXISTS bill_items(id INTEGER PRIMARY KEY, bill_id INTEGER, description TEXT, category TEXT, qty INTEGER, price REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT);
CREATE TABLE IF NOT EXISTS stock_log(id INTEGER PRIMARY KEY, medicine_id INTEGER, change INTEGER, reason TEXT, user TEXT, ts TEXT);
"""

MIGRATIONS = [
    "ALTER TABLE patients ADD COLUMN email TEXT",
    "ALTER TABLE medicines ADD COLUMN batch_no TEXT",
    "ALTER TABLE medicines ADD COLUMN manufacturer TEXT",
    "ALTER TABLE medicines ADD COLUMN rack TEXT",
    "ALTER TABLE medicines ADD COLUMN cost REAL DEFAULT 0",
    "ALTER TABLE bills ADD COLUMN email_status TEXT",
]

def migrate(db):
    """Add new columns to an existing database without losing data."""
    for sql in MIGRATIONS:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def new_patient(db, name, age, gender, bg, phone, address, ec, allergies, email=""):
    cur = db.execute("""INSERT INTO patients(name,age,gender,blood_group,phone,address,emergency_contact,allergies,created_at,email)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (name, age, gender, bg, phone, address, ec, allergies, now(), (email or "").strip()))
    pid = cur.lastrowid
    db.execute("UPDATE patients SET uhid=? WHERE id=?", (f"UH{pid:05d}", pid))
    db.commit()
    return pid

def init_db():
    db = connect()
    db.executescript(SCHEMA)
    migrate(db)
    if not db.execute("SELECT 1 FROM users").fetchone():
        seed(db)
    db.commit()
    db.close()

def seed(db):
    today = date.today()
    db.executemany("INSERT INTO doctors(name,department,qualification,phone,fee) VALUES(?,?,?,?,?)", [
        ("Dr. Arun Kumar", "Cardiology", "MD, DM (Cardiology)", "9840000001", 800),
        ("Dr. Meena Iyer", "Neurology", "MD, DM (Neurology)", "9840000002", 800),
        ("Dr. Rahul Sharma", "Orthopedics", "MS (Ortho)", "9840000003", 700),
        ("Dr. Priya Nair", "Dermatology", "MD (Dermatology)", "9840000004", 600),
        ("Dr. Sanjay Rao", "ENT", "MS (ENT)", "9840000005", 600),
        ("Dr. Lakshmi Devi", "General Medicine", "MBBS, MD", "9840000006", 500),
        ("Dr. Karthik R", "General Medicine", "MBBS", "9840000007", 400)])
    for u, p, r, n, d in [("admin", "admin123", "admin", "System Administrator", None),
                          ("reception", "recep123", "receptionist", "Front Desk", None),
                          ("doctor", "doctor123", "doctor", "Dr. Arun Kumar", 1),
                          ("pharmacy", "pharma123", "pharmacist", "Pharmacist", None),
                          ("lab", "lab123", "lab", "Lab Technician", None)]:
        db.execute("INSERT INTO users(username,password,role,full_name,doctor_id) VALUES(?,?,?,?,?)",
                   (u, generate_password_hash(p), r, n, d))
    for n, c, s, pr, rl, days in [
        ("Paracetamol 500mg", "Analgesic", 200, 2, 50, 400), ("Aspirin 75mg", "Antiplatelet", 120, 3, 40, 400),
        ("Ibuprofen 400mg", "NSAID", 90, 4, 30, 400), ("Diclofenac 50mg", "NSAID", 80, 5, 30, 400),
        ("Amoxicillin 500mg", "Antibiotic", 100, 8, 30, 300), ("Azithromycin 500mg", "Antibiotic", 60, 20, 20, 300),
        ("Metformin 500mg", "Antidiabetic", 150, 3, 40, 500), ("Amlodipine 5mg", "Antihypertensive", 130, 4, 40, 500),
        ("Atenolol 50mg", "Antihypertensive", 100, 3, 30, 500), ("Atorvastatin 10mg", "Statin", 110, 6, 30, 500),
        ("Omeprazole 20mg", "Antacid", 140, 4, 40, 400), ("Pantoprazole 40mg", "Antacid", 90, 6, 30, 400),
        ("Cetirizine 10mg", "Antiallergic", 160, 2, 40, 400), ("Clopidogrel 75mg", "Antiplatelet", 70, 9, 25, 400),
        ("Warfarin 5mg", "Anticoagulant", 8, 7, 15, 300), ("Cough Syrup 100ml", "Syrup", 45, 60, 15, 40)]:
        db.execute("INSERT INTO medicines(name,category,stock,price,reorder_level,expiry) VALUES(?,?,?,?,?,?)",
                   (n, c, s, pr, rl, str(today + timedelta(days=days))))
    db.executemany("INSERT INTO lab_tests(name,price,unit,low,high) VALUES(?,?,?,?,?)", [
        ("Hemoglobin", 150, "g/dL", 12, 17), ("Fasting Blood Sugar", 80, "mg/dL", 70, 100),
        ("HbA1c", 450, "%", 4.0, 5.6), ("Total Cholesterol", 300, "mg/dL", 0, 200),
        ("Serum Creatinine", 250, "mg/dL", 0.6, 1.3), ("Blood Urea", 200, "mg/dL", 15, 40),
        ("WBC Count", 200, "cells/uL", 4000, 11000), ("Platelet Count", 200, "/uL", 150000, 450000),
        ("SGPT (ALT)", 300, "U/L", 7, 56), ("TSH", 350, "uIU/mL", 0.4, 4.0)])
    for name, charge, n, pre in [("General Ward", 500, 6, "G"), ("ICU", 3000, 3, "ICU"), ("Private Room", 1500, 3, "P")]:
        wid = db.execute("INSERT INTO wards(name,charge) VALUES(?,?)", (name, charge)).lastrowid
        for i in range(1, n + 1):
            db.execute("INSERT INTO beds(ward_id,bed_no) VALUES(?,?)", (wid, f"{pre}-{i}"))
    pts = [("Ravi Kumar", 55, "Male", "B+", "9876500001", "Tambaram, Chennai", "9876500011", "Penicillin"),
           ("Anitha Selvam", 34, "Female", "O+", "9876500002", "Velachery, Chennai", "9876500012", "None"),
           ("Mohammed Irfan", 62, "Male", "A+", "9876500003", "Chromepet, Chennai", "9876500013", "None"),
           ("Divya Raman", 28, "Female", "AB+", "9876500004", "Adyar, Chennai", "9876500014", "Sulfa"),
           ("Suresh Babu", 70, "Male", "O-", "9876500005", "Guindy, Chennai", "9876500015", "NSAIDs")]
    ids = [new_patient(db, *p) for p in pts]
    for pid, v in zip(ids, [(190, 110, 250, 105, 94, 37.2), (118, 76, 95, 78, 98, 36.8), (150, 95, 180, 88, 96, 37.0),
                            (120, 80, 90, 72, 99, 36.6), (85, 55, 60, 125, 88, 38.5)]):
        db.execute("""INSERT INTO vitals(patient_id,bp_sys,bp_dia,sugar,heart_rate,spo2,temp,recorded_at,recorded_by)
                      VALUES(?,?,?,?,?,?,?,?,?)""", (pid, *v, now(), "reception"))
    for i, (pi, doc, t, why) in enumerate([(0, 1, "09:00", "Chest discomfort"), (1, 6, "10:00", "Fever and body pain"),
                                           (2, 2, "11:00", "Headache and dizziness"), (3, 4, "12:00", "Skin rash")]):
        db.execute("INSERT INTO appointments(patient_id,doctor_id,adate,atime,reason) VALUES(?,?,?,?,?)",
                   (ids[pi], doc, str(today), t, why))
    for k, n in enumerate([3, 5, 2, 4, 6, 3], start=1):
        day = str(today - timedelta(days=k))
        for j in range(n):
            db.execute("INSERT INTO appointments(patient_id,doctor_id,adate,atime,reason,status) VALUES(?,?,?,?,?,'Completed')",
                       (ids[(k + j) % 5], ((k + j) % 7) + 1, day, SLOTS[j], "Check-up"))
        bid = db.execute("INSERT INTO bills(patient_id,bdate,status,mode,paid_on) VALUES(?,?,'Paid',?,?)",
                         (ids[k % 5], day, ["Cash", "UPI", "Card"][k % 3], day)).lastrowid
        db.execute("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
                   (bid, "Consultation", "Consultation", 1, 500 + 50 * k))
        db.execute("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
                   (bid, "Medicines", "Pharmacy", 1, 150 + 20 * k))
        if k % 2 == 0:
            db.execute("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
                       (bid, "Lab tests", "Laboratory", 1, 300 + 40 * k))
    bed = db.execute("SELECT id FROM beds WHERE bed_no='ICU-1'").fetchone()["id"]
    db.execute("UPDATE beds SET occupied=1 WHERE id=?", (bed,))
    db.execute("INSERT INTO admissions(patient_id,bed_id,doctor_id,admit_date,reason) VALUES(?,?,?,?,?)",
               (ids[4], bed, 1, str(today - timedelta(days=2)), "Chest infection with low oxygen level"))
    bid = db.execute("INSERT INTO bills(patient_id,bdate) VALUES(?,?)", (ids[2], str(today))).lastrowid
    db.execute("INSERT INTO bill_items(bill_id,description,category,qty,price) VALUES(?,?,?,?,?)",
               (bid, "Consultation - Dr. Meena Iyer", "Consultation", 1, 800))
