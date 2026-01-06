import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
import json
import smtplib # New library for Email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# --- CONFIGURATION ---
GEMINI_API_KEY = "AIzaSyA4WqrXJiExEBfFJDH4-ibcQ83GgEuydcE"
genai.configure(api_key=GEMINI_API_KEY)

# --- EMAIL SETTINGS (PALITAN MO ITO) ---
EMAIL_SENDER = "dimaanodalevincent@gmail.com"  # <--- Ilagay ang Gmail mo
EMAIL_PASSWORD = "zept dmoj jbfb luvb" # <--- Ilagay ang 16-char APP PASSWORD

DB_HOST = "localhost"
DB_NAME = "assiscan_db"
DB_USER = "postgres"
DB_PASS = "admin123"

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def get_db_connection():
    try:
        return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return None

def get_best_model():
    try:
        all_models = list(genai.list_models())
        supported = [m for m in all_models if 'generateContent' in m.supported_generation_methods]
        chosen = next((m for m in supported if 'flash' in m.name), None)
        if not chosen: chosen = next((m for m in supported if '1.5' in m.name), None)
        return genai.GenerativeModel(chosen.name if chosen else 'gemini-1.5-flash')
    except:
        return genai.GenerativeModel('gemini-1.5-flash')

model = get_best_model()

# --- EMAIL FUNCTION ---
def send_email_notification(recipient_email, student_name, file_paths):
    if not recipient_email: return
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient_email
        msg['Subject'] = "AssiScan Verification Complete - Document Copy"

        body = f"""
        Dear {student_name},

        Your documents have been successfully scanned and verified by the AssiScan System (University of Batangas).

        Attached are the copies of your submitted records.

        Regards,
        AssiScan Admin
        """
        msg.attach(MIMEText(body, 'plain'))

        # Attach Files
        for fpath in file_paths:
            if fpath and os.path.exists(fpath):
                attachment = open(fpath, "rb")
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(fpath)}")
                msg.attach(part)
                attachment.close()

        # Send
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(EMAIL_SENDER, recipient_email, text)
        server.quit()
        print(f"✅ Email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Email Error: {e}")
        return False

# ================= ROUTES =================

@app.route('/extract', methods=['POST'])
def extract_data():
    if 'imageFile' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['imageFile']
    filename = secure_filename(f"PSA_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        myfile = genai.upload_file(filepath)
        prompt = """Analyze this Birth Certificate. Return strictly JSON:
        {"Name": "", "Sex": "", "Birthdate": "YYYY-MM-DD", "PlaceOfBirth": "", "Mother_MaidenName": "", "Mother_Citizenship": "", "Mother_Occupation": "", "Father_Name": "", "Father_Citizenship": "", "Father_Occupation": ""}"""
        res = model.generate_content([myfile, prompt])
        data = json.loads(res.text.replace('```json', '').replace('```', '').strip())
        return jsonify({"message": "Success", "structured_data": data, "image_path": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/save-record', methods=['POST'])
def save_record():
    d = request.json
    img_filename = os.path.basename(d.get('image_path', ''))
    
    # Full path for email attachment
    full_image_path = os.path.join(app.config['UPLOAD_FOLDER'], img_filename) if img_filename else None
    
    # DB path (relative)
    db_image_path = os.path.join('uploads', img_filename) if img_filename else None

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check Duplicate
        cur.execute("SELECT id FROM records WHERE LOWER(name) = LOWER(%s) AND birthdate = %s", (d['name'], d['birthdate']))
        if cur.fetchone():
            return jsonify({"status": "duplicate", "message": "Record already exists!"})

        cur.execute('''
            INSERT INTO records (name, sex, birthdate, birthplace, mother_name, mother_citizenship, mother_occupation, father_name, father_citizenship, father_occupation, image_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        ''', (d['name'], d['sex'], d['birthdate'], d['birthplace'], d['mother_name'], d['mother_citizenship'], d['mother_occupation'], d['father_name'], d['father_citizenship'], d['father_occupation'], db_image_path))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        # --- SEND EMAIL (NEW FEATURE) ---
        email_addr = d.get('email', '')
        email_status = "Skipped"
        if email_addr:
            # We collect attachments (PSA is already saved)
            attachments = [full_image_path] if full_image_path else []
            # Note: Extra forms (137/138) are uploaded separately via /upload-additional
            # For simplicity, we email the PSA first. 
            # If you want to email ALL forms, we need to handle that after all uploads are done.
            # Currently, this sends the PSA immediately upon save.
            send_email_notification(email_addr, d['name'], attachments)
            email_status = "Sent"

        return jsonify({"status": "success", "db_id": new_id, "email_status": email_status})

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        conn.close()

@app.route('/upload-additional', methods=['POST'])
def upload_additional():
    file = request.files['file']
    rid = request.form.get('id')
    dtype = request.form.get('type')
    filename = secure_filename(f"{dtype}_{rid}_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    db_path = os.path.join('uploads', filename)
    
    col_map = {'form137': 'form137_path', 'form138': 'form138_path', 'goodmoral': 'goodmoral_path'}
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE records SET {col_map[dtype]} = %s WHERE id = %s", (db_path, rid))
    conn.commit()
    conn.close()
    return jsonify({"message": "Uploaded"})

@app.route('/get-records', methods=['GET'])
def get_records():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM records ORDER BY id DESC")
    rows = cur.fetchall()
    for r in rows: 
        if r['created_at']: r['created_at'] = str(r['created_at'])
    conn.close()
    return jsonify({"records": rows})

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_record(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM records WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Deleted'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)