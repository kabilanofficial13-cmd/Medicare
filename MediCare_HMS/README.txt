MEDICARE HOSPITAL MANAGEMENT SYSTEM WITH AI  (Python Flask + SQLite)

HOW TO RUN (Windows)
  Easiest: double-click run.bat   (it installs Flask and opens the browser)
  Manual : open Command Prompt in this folder, then
           pip install flask
           python app.py
  The browser opens at http://127.0.0.1:5000  (or open it yourself)

LOGINS
  Admin         admin      / admin123
  Receptionist  reception  / recep123
  Doctor        doctor     / doctor123
  Pharmacist    pharmacy   / pharma123
  Lab           lab        / lab123

To reset all data: stop the program and delete hospital.db, then run again.

PROJECT FILES
  app.py       - all web pages / routes / role permissions
  db.py        - database tables + demo data
  ai.py        - all AI features
  templates/   - HTML pages

UI / EXTRA FEATURES
  - Modern colourful interface with Dark / Light mode (Theme button, top right)
  - Voice: click the mic button on notes / chatbot / symptom box and speak.
    Choose English or Tamil at the top right. Works in Chrome or Edge with internet.
  - AI answers can be read aloud (AI Assistant page).
  - static/logo.svg and static/hero.svg are vector graphics you can also use in your PPT / report.


WHATSAPP BILL SENDING
  - Open a bill and use "Send Bill via WhatsApp". Without API setup, WhatsApp opens
    with the receipt pre-filled; a staff member must press Send. This is WhatsApp's
    normal click-to-chat security behavior.
  - For automatic sending after payment, configure Meta WhatsApp Cloud API first.
    Set these Windows environment variables before launching run.bat / app.py:
      set WHATSAPP_TOKEN=your_long_lived_access_token
      set WHATSAPP_PHONE_NUMBER_ID=your_meta_phone_number_id
    Then restart the app. Use a Meta-approved WhatsApp Business setup and make sure
    patients have consented to receive billing messages. Free-form text is generally
    allowed only during the 24-hour customer-service window after the patient messages
    your business. Outside that window, submit and use an approved utility template.
  - The app does not include or store your API token. Never share the token publicly.
  - Phone numbers should include country code; 10-digit Indian numbers are prefixed +91.


UPI SCAN-AND-PAY DEMO
  - Open a pending bill and click "Scan & Pay by UPI".
  - Set MEDICARE_UPI_ID to a real UPI ID you control before launching the app.
    Example (Command Prompt, for this terminal session only):
      set MEDICARE_UPI_ID=yourname@bank
      python app.py
  - Install dependencies with: python -m pip install -r requirements.txt
  - The QR contains a UPI payment intent with the bill amount and invoice number.
    Scan with a UPI app and complete the payment yourself for a real-money demo.
  - IMPORTANT: this project does not receive payment gateway callbacks and cannot
    verify payment automatically. Check the successful transaction in the UPI app
    or bank account before pressing "Payment received — Confirm & Generate Receipt".
    Do not mark a bill paid based only on a screenshot or a customer's claim.
  - For automatic payment verification, integrate a registered payment gateway
    (for example, Razorpay or Cashfree) and validate signed server-side webhooks.
  - Never put a fake UPI ID into the live demo. Use only an account you control.


PDF INVOICE + AUTOMATIC EMAIL DELIVERY
  - When the receptionist confirms a payment, the PDF receipt is emailed to the patient automatically
    (in the background, so billing is never slowed down). Tick/untick "Email PDF receipt" on the payment form.
  - The patient's email is stored in the patient record (Patients -> register / open patient -> Save email),
    or typed on the payment form (it is saved for next time).
  - Invoice page -> "Send PDF by Email" resends the PDF any time, and shows the last email status.
  - ONE-TIME EMAIL SETUP (Gmail):
      1) Copy ".env.example" to ".env" in this folder.
      2) Create a Google App Password (needs 2-Step Verification): https://myaccount.google.com/apppasswords
      3) Paste the 16-character App Password after MEDICARE_SMTP_PASSWORD= in ".env".
      4) Restart the app (run.bat).
    Gmail will NOT accept your normal Gmail password. Never share or upload the .env file.
  - Gmail free accounts allow roughly 500 emails per day - fine for a clinic.
  - Every email result is written to the Audit Log and the invoice page.
  - For WhatsApp PDF auto-send, set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID using a Meta
    WhatsApp Business Cloud API setup. Patients must have consented to messages; templates may be
    required outside the 24-hour customer-service window. Without API credentials, download the PDF
    and attach it manually in WhatsApp.
  - Keep patient data private and use this student project with demo/test data unless properly secured.


PHARMACY FEATURES
  - Dashboard cards: stock value (retail / cost), low stock, expiring in 60 days, expired.
  - Search + filter inventory (low stock, out of stock, expiring, expired); batch, manufacturer, rack, cost price.
  - Safety check on every prescription: allergy, child/elderly alerts and drug interactions with the
    patient's other pending medicines. HIGH warnings need "Dispense anyway" confirmation. Expired stock is blocked.
  - Counter sale (no prescription) added straight to the patient's bill.
  - Edit medicine details; adjust stock (correction, return, expired write-off, damaged, lost).
  - Stock history log of every movement (who, when, why). Reorder list download (CSV) with suggested quantities.

REPORTS FEATURES (Admin)
  - Choose any date range (or 7 / 30 / 90 days / 1 year); revenue vs previous period.
  - KPIs: revenue, paid bills, average bill, discounts, new patients, appointments, admissions, dues.
  - Charts: revenue trend, category, payment mode, departments, lab tests and results, age groups, gender, bed occupancy.
  - Tables: doctor performance, top medicines sold, frequent patients, low / expiring stock, outstanding bills with age.
  - Revenue CSV download (opens in Excel) and Print button; email delivery counts.
