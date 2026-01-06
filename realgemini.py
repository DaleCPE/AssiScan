import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold # Added for Safety Settings
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

# --- CONFIGURATION FROM RENDER ENVIRONMENT ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# DATABASE CONFIGURATION FOR RENDER
DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return None

# --- AUTO-SETUP DATABASE TABLE ---
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
            print("✅ Database tables initialized successfully!")
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
        finally:
            conn.close()

init_db()

# --- MODEL SETUP WITH SAFETY SETTINGS ---
# Importante ito para payagan ang PII (Personal Info) sa documents
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)

# --- EMAIL NOTIFICATION ---
def send_email_notification(recipient_email, student_name, file_paths):
    if not recipient_email: return
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

@app.route('/history.html')
def history_page():
    return render_template('history.html')

@app.route('/extract', methods=['POST'])
def extract_data():
    if 'imageFile' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['imageFile']
    
    # Secure filename and path
    filename = secure_filename(f"PSA_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    print(f"📸 Scanning file: {filename}") # Log entry

    try:
        myfile = genai.upload_file(filepath)
        
        prompt = """
        You are a data entry assistant. Analyze this Birth Certificate image.
        Extract the information strictly in valid JSON format.
        If a field is not visible or unclear, use an empty string "".
        Keys: "Name", "Sex", "Birthdate" (YYYY-MM-DD format), "PlaceOfBirth", "Mother_MaidenName", "Mother_Citizenship", "Mother_Occupation", "Father_Name", "Father_Citizenship", "Father_Occupation".
        Do not include markdown formatting like ```json. Just raw JSON.
        """
        
        res = model.generate_content([myfile, prompt])
        
        # Clean the response text to ensure it's valid JSON
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        print(f"🤖 AI Response: {raw_text}") # Log raw AI response for debugging

        data = json.loads(raw_text)
        
        return jsonify({"message": "Success", "structured_data": data, "image_path": filename})
    
    except Exception as e:
        print(f"❌ Extraction Error: {str(e)}") # Print error to Render Logs
        return jsonify({"error": str(e)}), 500

@app.route('/save-record', methods=['POST'])
def save_record():
    d = request.json
    img_filename = os.path.basename(d.get('image_path', ''))
    # Use relative path for database storage to avoid local path issues
    db_image_path = os.path.join('uploads', img_filename) if img_filename else None
    
    # Path for email attachment (full path needed)
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

        # Handle Email
        email_addr = d.get('email', '')
        email_status = "Not Sent"
        if email_addr:
            success = send_email_notification(email_addr, d['name'], [full_image_path] if full_image_path else [])
            email_status = "Sent" if success else "Failed"

        return jsonify({"status": "success", "db_id": new_id, "email_status": email_status})
    except Exception as e:
        conn.rollback()
        print(f"❌ Save Error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        conn.close()

@app.route('/get-records', methods=['GET'])
def get_records():
    conn = get_db_connection()
    if not conn: return jsonify({"records": []})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records ORDER BY id DESC")
        rows = cur.fetchall()
        
        # Convert objects to string for JSON serialization
        for r in rows: 
            if r['created_at']: r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: r['birthdate'] = str(r['birthdate'])
            
        return jsonify({"records": rows})
    except Exception as e:
        print(f"❌ Fetch Error: {e}")
        return jsonify({"records": []})
    finally:
        conn.close()

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_record(id):
    conn = get_db_connection()
    if not conn: return jsonify({"error": "DB Connection Failed"}), 500
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"message": "Deleted successfully"})
    except Exception as e:
        print(f"❌ Delete Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
