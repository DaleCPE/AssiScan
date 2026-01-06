import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename

# --- CONFIGURATION FROM RENDER ENVIRONMENT ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- ADMIN SECURITY CONFIG ---
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123" # Pwede mong palitan

app = Flask(__name__)
app.secret_key = "super_secret_security_key_change_me" # REQUIRED FOR LOGIN SESSION
CORS(app)

# Setup Upload Folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- DATABASE CONNECTION ---
def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return None

# --- INIT DATABASE TABLE ---
def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS records (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255),
                    sex VARCHAR(50),
                    birthdate DATE,
                    birthplace TEXT,
                    mother_name VARCHAR(255),
                    mother_citizenship VARCHAR(100),
                    mother_occupation VARCHAR(100),
                    father_name VARCHAR(255),
                    father_citizenship VARCHAR(100),
                    father_occupation VARCHAR(100),
                    image_path TEXT,
                    form137_path TEXT,
                    form138_path TEXT,
                    goodmoral_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.commit()
            cur.close()
            print("✅ Database initialized successfully!")
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
        finally:
            conn.close()

init_db()

# --- SUREFIRE MODEL SELECTOR ---
def get_working_model():
    print("🔍 QUERYING GOOGLE FOR AVAILABLE MODELS...")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ FOUND VALID MODEL: {m.name}")
                return m.name
        print("❌ NO MODELS FOUND with 'generateContent' capability.")
        return "models/gemini-1.5-flash"
    except Exception as e:
        print(f"❌ CRITICAL ERROR listing models: {e}")
        return "models/gemini-1.5-flash"

active_model_name = get_working_model()
print(f"🚀 SYSTEM WILL USE: {active_model_name}")

safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

model = genai.GenerativeModel(active_model_name, safety_settings=safety_settings)

# --- EMAIL FUNCTION ---
def send_email_notification(recipient_email, student_name, file_paths):
    if not recipient_email or not EMAIL_SENDER: return False
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient_email
        msg['Subject'] = "AssiScan Verification Complete - Document Copy"
        body = f"Dear {student_name},\n\nYour documents have been verified by the AssiScan System.\n\nRegards,\nAssiScan Admin"
        msg.attach(MIMEText(body, 'plain'))
        for fpath in file_paths:
            if fpath and os.path.exists(fpath):
                with open(fpath, "rb") as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(fpath)}")
                    msg.attach(part)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, recipient_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"❌ Email Error: {e}")
        return False

# ================= ROUTES =================

@app.route('/')
def index():
    return render_template('index.html')

# --- LOGIN ROUTE (NEW) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/history.html')
        else:
            return render_template('login.html', error="Invalid Credentials")
    return render_template('login.html')

# --- LOGOUT ROUTE (NEW) ---
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/login')

# --- PROTECTED HISTORY PAGE ---
@app.route('/history.html')
def history_page():
    if not session.get('logged_in'):
        return redirect('/login') # Kick out if not logged in
    return render_template('history.html')

# --- PROTECTED API ---
@app.route('/get-records', methods=['GET'])
def get_records():
    # Security check: Block API access if not logged in
    if not session.get('logged_in'):
        return jsonify({"records": [], "error": "Unauthorized"}), 401

    conn = get_db_connection()
    if not conn: return jsonify({"records": []})
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records ORDER BY id DESC")
        rows = cur.fetchall()
        for r in rows:
            if r['created_at']: r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: r['birthdate'] = str(r['birthdate'])
        return jsonify({"records": rows})
    except Exception as e:
        return jsonify({"records": []})
    finally:
        conn.close()

# --- PUBLIC SCANNING ROUTES (Walang Login para makapag-scan ang students) ---

@app.route('/extract', methods=['POST'])
def extract_data():
    if 'imageFile' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['imageFile']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(f"PSA_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    print(f"📸 Processing image: {filename} using {active_model_name}")

    try:
        myfile = genai.upload_file(filepath)
        
        prompt = """
        Analyze this Birth Certificate image.
        Extract the following fields and return ONLY a valid JSON object.
        Keys needed: "Name", "Sex", "Birthdate" (YYYY-MM-DD), "PlaceOfBirth", "Mother_MaidenName", "Mother_Citizenship", "Mother_Occupation", "Father_Name", "Father_Citizenship", "Father_Occupation".
        If a field is missing or unreadable, leave it as an empty string "".
        Do not include markdown formatting (like ```json). Return raw JSON only.
        """
        
        res = model.generate_content([myfile, prompt])
        
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        print(f"🤖 AI Response: {raw_text}") 

        data = json.loads(raw_text)
        return jsonify({"message": "Success", "structured_data": data, "image_path": filename})

    except Exception as e:
        print(f"❌ Extraction Failed: {e}")
        return jsonify({"error": f"Model Error ({active_model_name}): {str(e)}"}), 500

@app.route('/save-record', methods=['POST'])
def save_record():
    d = request.json
    img_filename = os.path.basename(d.get('image_path', ''))
    
    db_image_path = os.path.join('uploads', img_filename) if img_filename else None
    full_image_path = os.path.join(app.config['UPLOAD_FOLDER'], img_filename) if img_filename else None

    conn = get_db_connection()
    if not conn: return jsonify({"error": "DB Connection Failed"}), 500
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT INTO records (name, sex, birthdate, birthplace, mother_name, mother_citizenship, mother_occupation, father_name, father_citizenship, father_occupation, image_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        ''', (d['name'], d['sex'], d['birthdate'], d['birthplace'], d['mother_name'], d['mother_citizenship'], d['mother_occupation'], d['father_name'], d['father_citizenship'], d['father_occupation'], db_image_path))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        email_addr = d.get('email', '')
        email_status = "Not Sent"
        if email_addr:
            success = send_email_notification(email_addr, d['name'], [full_image_path] if full_image_path else [])
            email_status = "Sent" if success else "Failed"

        return jsonify({"status": "success", "db_id": new_id, "email_status": email_status})
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        conn.close()

@app.route('/upload-additional', methods=['POST'])
def upload_additional():
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    record_id = request.form.get('id')
    doc_type = request.form.get('type') 

    if not record_id or not doc_type: return jsonify({"error": "Missing ID or Type"}), 400

    filename = secure_filename(f"{doc_type}_{record_id}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    db_path = os.path.join('uploads', filename)

    col_map = { 'form137': 'form137_path', 'form138': 'form138_path', 'goodmoral': 'goodmoral_path' }
    if doc_type not in col_map: return jsonify({"error": "Invalid type"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        sql = f"UPDATE records SET {col_map[doc_type]} = %s WHERE id = %s"
        cur.execute(sql, (db_path, record_id))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
