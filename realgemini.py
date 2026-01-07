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
ADMIN_PASSWORD = "admin123"

app = Flask(__name__)
app.secret_key = "super_secret_security_key_change_me"
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

# --- INIT DATABASE TABLE (UPDATED SCHEMA) ---
def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # UPDATED: Added birth_order, religion, age columns
            cur.execute('''
                CREATE TABLE IF NOT EXISTS records (
                    id SERIAL PRIMARY KEY,
                    
                    -- PSA Data
                    name VARCHAR(255),
                    sex VARCHAR(50),
                    birthdate DATE,
                    birthplace TEXT,
                    birth_order VARCHAR(50),
                    religion VARCHAR(100),
                    age INTEGER,

                    -- Parents
                    mother_name VARCHAR(255),
                    mother_citizenship VARCHAR(100),
                    mother_occupation VARCHAR(100),
                    father_name VARCHAR(255),
                    father_citizenship VARCHAR(100),
                    father_occupation VARCHAR(100),
                    
                    -- Form 137 / School Data
                    lrn VARCHAR(50),
                    school_name TEXT,
                    school_address TEXT,
                    final_general_average VARCHAR(50),

                    -- Files
                    image_path TEXT,      -- PSA Image
                    form137_path TEXT,
                    form138_path TEXT,
                    goodmoral_path TEXT,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.commit()
            cur.close()
            print("✅ Database initialized successfully with ALL columns!")
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
        finally:
            conn.close()

init_db()

# --- MODEL SELECTOR ---
def get_working_model():
    print("🔍 QUERYING GOOGLE FOR AVAILABLE MODELS...")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ FOUND VALID MODEL: {m.name}")
                return m.name
        return "models/gemini-1.5-flash"
    except Exception as e:
        print(f"❌ CRITICAL ERROR listing models: {e}")
        return "models/gemini-1.5-flash"

active_model_name = get_working_model()

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

# --- LOGIN ROUTE ---
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

# --- LOGOUT ROUTE ---
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/login')

# --- PROTECTED HISTORY PAGE ---
@app.route('/history.html')
def history_page():
    if not session.get('logged_in'):
        return redirect('/login') 
    return render_template('history.html')

# --- PROTECTED API: GET RECORDS ---
@app.route('/get-records', methods=['GET'])
def get_records():
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
        print(f"DB Error: {e}")
        return jsonify({"records": []})
    finally:
        conn.close()

# --- 1. STRICT PSA SCANNING (UPDATED WITH BIRTH ORDER/RELIGION) ---
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

    print(f"📸 Processing PSA: {filename}")

    try:
        myfile = genai.upload_file(filepath)
        
        prompt = """
        SYSTEM ROLE: Strict Philippine Document Verifier.
        TASK: Analyze this image. It MUST be a "Certificate of Live Birth" (PSA/NSO/LCR).
        
        STRICT VALIDATION RULES:
        1. Look for the text "Certificate of Live Birth" OR "Republic of the Philippines" AND "Office of the Civil Registrar General".
        2. If the image is a selfie, a landscape, an ID, a receipt, or NOT a birth certificate, mark "is_valid_document": false.
        
        IF VALID, EXTRACT THESE FIELDS:
        - Name, Sex, Birthdate (YYYY-MM-DD), PlaceOfBirth
        - BirthOrder (e.g. First, Second)
        - Religion (if present)
        - Mother_MaidenName, Mother_Citizenship, Mother_Occupation
        - Father_Name, Father_Citizenship, Father_Occupation

        OUTPUT FORMAT (JSON ONLY):
        {
            "is_valid_document": boolean,
            "rejection_reason": "string or null",
            "Name": "string",
            "Sex": "string",
            "Birthdate": "string",
            "PlaceOfBirth": "string",
            "BirthOrder": "string",
            "Religion": "string",
            "Mother_MaidenName": "string",
            "Mother_Citizenship": "string",
            "Mother_Occupation": "string",
            "Father_Name": "string",
            "Father_Citizenship": "string",
            "Father_Occupation": "string"
        }
        """
        
        res = model.generate_content([myfile, prompt])
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw_text)

        if not data.get("is_valid_document", False):
            if os.path.exists(filepath):
                os.remove(filepath)
            reason = data.get("rejection_reason", "Not a valid PSA Birth Certificate.")
            return jsonify({"error": f"Invalid Document: {reason}"}), 400

        return jsonify({"message": "Success", "structured_data": data, "image_path": filename})

    except Exception as e:
        print(f"❌ Extraction Failed: {e}")
        return jsonify({"error": f"AI Error: {str(e)}"}), 500

# --- 2. FORM 137 SCANNING ---
@app.route('/extract-form137', methods=['POST'])
def extract_form137():
    if 'imageFile' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['imageFile']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(f"F137_SCAN_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    print(f"📸 Processing Form 137: {filename}")

    try:
        myfile = genai.upload_file(filepath)
        
        prompt = """
        SYSTEM ROLE: Philippine School Document Analyzer.
        TASK: Analyze this image. It should be a Form 137, SF10, or Permanent Record.
        
        EXTRACT THE FOLLOWING:
        1. LRN (Learner Reference Number) - usually a 12-digit number.
        2. School Name - The name of the school appearing in the header or most recent entry.
        3. School Address - The location/address of the school.
        4. Final General Average - The final grade/GPA if visible (e.g. 90.5).

        OUTPUT FORMAT (JSON ONLY):
        {
            "lrn": "string",
            "school_name": "string",
            "school_address": "string",
            "final_general_average": "string"
        }
        """
        
        res = model.generate_content([myfile, prompt])
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw_text)
        
        return jsonify({"message": "Success", "structured_data": data, "image_path": filename})

    except Exception as e:
        print(f"❌ Form 137 Extraction Failed: {e}")
        return jsonify({"error": f"AI Error: {str(e)}"}), 500

# --- 3. UPDATED SAVE RECORD (ALL FIELDS) ---
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Received Data for Saving: {d}") # Debugging
        
        # --- PSA FIELDS ---
        name = d.get('name') or d.get('Name')
        sex = d.get('sex') or d.get('Sex')
        birthdate = d.get('birthdate') or d.get('Birthdate')
        birthplace = d.get('birthplace') or d.get('PlaceOfBirth')
        
        # NEW: Birth Order, Religion, Age
        birth_order = d.get('birth_order') or d.get('BirthOrder')
        religion = d.get('religion') or d.get('Religion')
        age = d.get('age')

        m_name = d.get('mother_name') or d.get('Mother_MaidenName')
        m_cit = d.get('mother_citizenship') or d.get('Mother_Citizenship')
        m_occ = d.get('mother_occupation') or d.get('Mother_Occupation')
        
        f_name = d.get('father_name') or d.get('Father_Name')
        f_cit = d.get('father_citizenship') or d.get('Father_Citizenship')
        f_occ = d.get('father_occupation') or d.get('Father_Occupation')

        # --- FORM 137 FIELDS ---
        lrn = d.get('lrn', '')
        school_name = d.get('school_name', '')
        school_address = d.get('school_address', '')
        final_grade = d.get('final_general_average', '')

        # --- FIX EMPTY DATES ---
        if not birthdate or birthdate == "null" or birthdate == "":
            birthdate = None 

        # --- HANDLE IMAGES ---
        # PSA Image
        psa_img = d.get('psa_image_path') or d.get('image_path')
        db_psa_path = os.path.join('uploads', os.path.basename(psa_img)) if psa_img else None
        
        # Form 137 Image
        f137_img = d.get('f137_image_path')
        db_f137_path = os.path.join('uploads', os.path.basename(f137_img)) if f137_img else None

        conn = get_db_connection()
        if not conn: return jsonify({"error": "DB Connection Failed"}), 500
        cur = conn.cursor()
        
        cur.execute('''
            INSERT INTO records (
                name, sex, birthdate, birthplace, birth_order, religion, age,
                mother_name, mother_citizenship, mother_occupation, 
                father_name, father_citizenship, father_occupation, 
                lrn, school_name, school_address, final_general_average,
                image_path, form137_path
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) 
            RETURNING id
        ''', (
            name, sex, birthdate, birthplace, birth_order, religion, age,
            m_name, m_cit, m_occ, 
            f_name, f_cit, f_occ, 
            lrn, school_name, school_address, final_grade,
            db_psa_path, db_f137_path
        ))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        # Email Notification (Optional)
        email_addr = d.get('email', '')
        full_psa_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(psa_img)) if psa_img else None
        if email_addr:
            send_email_notification(email_addr, name, [full_psa_path] if full_psa_path else [])

        return jsonify({"status": "success", "db_id": new_id})
    except Exception as e:
        print(f"❌ SAVE ERROR: {e}")
        if conn: conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if conn: conn.close()

# --- DELETE RECORD ROUTE ---
@app.route('/delete-record/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    if not conn: return jsonify({"error": "DB Connection Error"}), 500

    try:
        cur = conn.cursor()
        
        # 1. Get file paths first
        cur.execute("SELECT image_path, form137_path, form138_path, goodmoral_path FROM records WHERE id = %s", (record_id,))
        row = cur.fetchone()

        if row:
            # Delete physical files
            for file_path in row:
                if file_path:
                    clean_filename = os.path.basename(file_path)
                    full_path = os.path.join(app.config['UPLOAD_FOLDER'], clean_filename)
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception as e:
                            print(f"⚠️ Failed to delete file {full_path}: {e}")

            # 2. Delete DB record
            cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
            conn.commit()
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Record not found"}), 404

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- UPLOAD ADDITIONAL FILES (Form 138 / Good Moral) ---
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

    col_map = { 
        'form137': 'form137_path', 
        'form138': 'form138_path', 
        'goodmoral': 'goodmoral_path' 
    }
    
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

# --- FILE SERVER ---
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- DB RESET TOOL (UPDATED) ---
@app.route('/fix-db')
def fix_db():
    conn = get_db_connection()
    if not conn: return "DB Config Error"
    try:
        cur = conn.cursor()
        # WARNING: DROPS TABLE
        cur.execute("DROP TABLE IF EXISTS records;")
        conn.commit()
        
        # Recreate with NEW columns
        init_db()
        return "✅ Database has been RESET! Now supports Age, Religion, Birth Order, and Form 137 fields."
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
