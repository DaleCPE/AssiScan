import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
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
import traceback
from PIL import Image

# --- CONFIGURATION ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- CONFIGURE GEMINI ---
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ Google Generative AI Configured")
        
        # Test the API key immediately
        try:
            models = genai.list_models()
            model_list = list(models)
            print(f"✅ Gemini API Test: {len(model_list)} models available")
            for model in model_list[:3]:  # Show first 3 models
                print(f"   - {model.name}")
        except Exception as e:
            print(f"⚠️ Gemini API Test Failed: {e}")
            
    except Exception as e:
        print(f"⚠️ Error configuring Gemini: {e}")
else:
    print("❌ CRITICAL: GEMINI_API_KEY is missing!")

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
        # Fix Render PostgreSQL URL
        if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
            DATABASE_URL_FIXED = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            conn = psycopg2.connect(DATABASE_URL_FIXED)
        else:
            conn = psycopg2.connect(DATABASE_URL)
        return conn
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
                    birth_order VARCHAR(50),
                    religion VARCHAR(100),
                    age INTEGER,
                    mother_name VARCHAR(255),
                    mother_citizenship VARCHAR(100),
                    mother_occupation VARCHAR(100),
                    father_name VARCHAR(255),
                    father_citizenship VARCHAR(100),
                    father_occupation VARCHAR(100),
                    lrn VARCHAR(50),
                    school_name TEXT,
                    school_address TEXT,
                    final_general_average VARCHAR(50),
                    image_path TEXT,
                    form137_path TEXT,
                    form138_path TEXT,
                    goodmoral_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            
            # Add other columns...
            new_columns = [
                ("email", "VARCHAR(100)"),
                ("civil_status", "VARCHAR(50)"),
                ("nationality", "VARCHAR(100)"),
                ("mother_contact", "VARCHAR(50)"),
                ("father_contact", "VARCHAR(50)"),
                ("guardian_name", "VARCHAR(255)"),
                ("guardian_relation", "VARCHAR(100)"),
                ("guardian_contact", "VARCHAR(50)"),
                ("region", "VARCHAR(100)"),
                ("province", "VARCHAR(100)"),
                ("specific_address", "TEXT"),
                ("mobile_no", "VARCHAR(50)"),
                ("school_year", "VARCHAR(50)"),
                ("student_type", "VARCHAR(50)"),
                ("program", "VARCHAR(100)"),
                ("last_level_attended", "VARCHAR(100)"),
                ("is_ip", "VARCHAR(10)"),
                ("is_pwd", "VARCHAR(10)"),
                ("has_medication", "VARCHAR(10)"),
                ("is_working", "VARCHAR(10)"),
                ("residence_type", "VARCHAR(50)"),
                ("employer_name", "VARCHAR(255)"),
                ("marital_status", "VARCHAR(50)"),
                ("is_gifted", "VARCHAR(10)"),
                ("needs_assistance", "VARCHAR(10)"),
                ("school_type", "VARCHAR(50)"),
                ("year_attended", "VARCHAR(50)"),
                ("special_talents", "TEXT"),
                ("is_scholar", "VARCHAR(10)"),
                ("siblings", "TEXT")
            ]
            
            for col_name, col_type in new_columns:
                try:
                    cur.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
                except Exception:
                    conn.rollback() 
                else:
                    conn.commit()

            conn.commit()
            cur.close()
            print("✅ Database Schema Updated!")
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
        finally:
            conn.close()

init_db()

# --- EMAIL FUNCTION ---
def send_email_notification(recipient_email, student_name, file_paths):
    if not recipient_email or not EMAIL_SENDER: return False
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient_email
        msg['Subject'] = "AssiScan Verification Complete"
        body = f"Dear {student_name},\n\nYour documents have been verified.\n\nRegards,\nAssiScan"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, recipient_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"❌ Email Error: {e}")
        return False

# ================= HELPER FUNCTIONS =================

def save_multiple_files(files, prefix):
    saved_paths = []
    pil_images = []
    
    for i, file in enumerate(files):
        if file and file.filename:
            timestamp = int(datetime.now().timestamp())
            filename = secure_filename(f"{prefix}_{timestamp}_{i}_{file.filename}")
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(path)
            saved_paths.append(path)
            try:
                img = Image.open(path)
                pil_images.append(img)
            except Exception as e:
                print(f"Error opening image {filename}: {e}")
                
    return saved_paths, pil_images

# --- SIMPLE & WORKING GEMINI FUNCTION ---
def extract_with_gemini(prompt, images):
    """Simple working Gemini function"""
    try:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured")
        
        # Use the most reliable model
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Prepare content
        content_parts = [prompt]
        for img in images:
            content_parts.append(img)
        
        # Generate response
        response = model.generate_content(content_parts)
        
        if response.text:
            return response.text
        else:
            raise Exception("No response from Gemini")
            
    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        raise e

# ================= ROUTES =================

@app.route('/')
def index():
    return render_template('index.html')

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

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/login')

@app.route('/history.html')
def history_page():
    if not session.get('logged_in'):
        return redirect('/login') 
    return render_template('history.html')

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
        return jsonify({"records": []})
    finally:
        conn.close()

@app.route('/view-form/<int:record_id>')
def view_form(record_id):
    if not session.get('logged_in'):
        return redirect('/login')
        
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        
        if record:
            if record.get('birthdate'):
                record['birthdate'] = str(record['birthdate'])
            
            if record.get('siblings'):
                try:
                    record['siblings'] = json.loads(record['siblings'])
                except Exception:
                    record['siblings'] = []
            else:
                record['siblings'] = []

            return render_template('print_form.html', r=record)
        else:
            return "Record not found", 404
    except Exception as e:
        return f"Error loading form: {str(e)}", 500
    finally:
        conn.close()

# --- EXTRACT PSA (WORKING VERSION) ---
@app.route('/extract', methods=['POST'])
def extract_data():
    if 'imageFiles' not in request.files: 
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': 
        return jsonify({"error": "No selected file"}), 400

    try:
        saved_paths, pil_images = save_multiple_files(files, "PSA")
        
        if not pil_images:
             return jsonify({"error": "No valid images found"}), 400

        print(f"📸 Processing {len(pil_images)} PSA pages")
        
        prompt = """Extract information from this PSA Birth Certificate. 
        Return ONLY valid JSON without any markdown or code blocks.
        {
            "is_valid_document": true,
            "Name": "extract full name",
            "Sex": "Male or Female",
            "Birthdate": "YYYY-MM-DD",
            "PlaceOfBirth": "city/municipality",
            "BirthOrder": "1st, 2nd, etc",
            "Religion": "religion",
            "Mother_MaidenName": "mother's maiden name",
            "Mother_Citizenship": "citizenship",
            "Mother_Occupation": "occupation",
            "Father_Name": "father's name",
            "Father_Citizenship": "citizenship",
            "Father_Occupation": "occupation"
        }"""
        
        # Extract using Gemini
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response: {response_text[:200]}...")
            
            # Clean the response
            cleaned_text = response_text.strip()
            if '```json' in cleaned_text:
                cleaned_text = cleaned_text.replace('```json', '').replace('```', '')
            
            # Find JSON
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                return jsonify({"error": "Invalid response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            data = json.loads(json_str)
            
            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": ",".join(saved_paths)
            })
            
        except Exception as ai_error:
            print(f"❌ AI Extraction Failed: {ai_error}")
            return jsonify({
                "error": "AI service unavailable. Please check Gemini API configuration.",
                "details": str(ai_error)[:100]
            }), 500
            
    except Exception as e:
        print(f"❌ Server Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# --- EXTRACT FORM 137 (WORKING VERSION) ---
@app.route('/extract-form137', methods=['POST'])
def extract_form137():
    if 'imageFiles' not in request.files: 
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': 
        return jsonify({"error": "No selected file"}), 400
    
    try:
        saved_paths, pil_images = save_multiple_files(files, "F137")
        print(f"📸 Processing Form 137: {len(pil_images)} pages")

        if not pil_images:
            return jsonify({"error": "No valid images found"}), 400
        
        prompt = """Extract information from this Form 137 document.
        Return ONLY valid JSON without any markdown or code blocks.
        {
            "lrn": "12-digit number",
            "school_name": "name of school",
            "school_address": "city, province",
            "final_general_average": "grade like 85.5 or 90"
        }"""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response: {response_text[:200]}...")
            
            # Clean the response
            cleaned_text = response_text.strip()
            if '```json' in cleaned_text:
                cleaned_text = cleaned_text.replace('```json', '').replace('```', '')
            
            # Find JSON
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                return jsonify({"error": "Invalid response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            data = json.loads(json_str)
            
            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": ",".join(saved_paths)
            })
            
        except Exception as ai_error:
            print(f"❌ AI Extraction Failed: {ai_error}")
            return jsonify({
                "error": "AI service unavailable. Please check Gemini API configuration.",
                "details": str(ai_error)[:100]
            }), 500
            
    except Exception as e:
        print(f"❌ Form 137 Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# --- SAVE RECORD ---
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Saving record")
        
        siblings_list = d.get('siblings', [])
        siblings_json = json.dumps(siblings_list)
        
        conn = get_db_connection()
        if not conn: 
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor()
        
        # Check for duplicates
        if d.get('name') and d.get('birthdate'):
            cur.execute("SELECT id FROM records WHERE LOWER(name) = LOWER(%s) AND birthdate = %s", 
                       (d.get('name'), d.get('birthdate')))
            if cur.fetchone():
                return jsonify({
                    "status": "error", 
                    "error": "DUPLICATE_ENTRY", 
                    "message": "Record already exists."
                }), 409

        # Insert record
        cur.execute('''
            INSERT INTO records (
                name, sex, birthdate, birthplace, birth_order, religion, age,
                mother_name, mother_citizenship, mother_occupation, 
                father_name, father_citizenship, father_occupation, 
                lrn, school_name, school_address, final_general_average,
                image_path, form137_path,
                email, mobile_no, civil_status, nationality,
                mother_contact, father_contact,
                guardian_name, guardian_relation, guardian_contact,
                region, province, specific_address,
                school_year, student_type, program, last_level_attended,
                is_ip, is_pwd, has_medication, is_working,
                residence_type, employer_name, marital_status,
                is_gifted, needs_assistance, school_type, year_attended, special_talents, is_scholar,
                siblings
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 
                %s, %s, %s, 
                %s, %s, %s, %s, 
                %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s
            ) 
            RETURNING id
        ''', (
            d.get('name'), d.get('sex'), d.get('birthdate') or None, d.get('birthplace'), 
            d.get('birth_order'), d.get('religion'), d.get('age'),
            d.get('mother_name'), d.get('mother_citizenship'), d.get('mother_occupation'), 
            d.get('father_name'), d.get('father_citizenship'), d.get('father_occupation'), 
            d.get('lrn'), d.get('school_name'), d.get('school_address'), d.get('final_general_average'),
            d.get('psa_image_path', ''), d.get('f137_image_path', ''), 
            d.get('email'), d.get('mobile_no'), d.get('civil_status'), d.get('nationality'),
            d.get('mother_contact'), d.get('father_contact'),
            d.get('guardian_name'), d.get('guardian_relation'), d.get('guardian_contact'),
            d.get('region'), d.get('province'), d.get('specific_address'),
            d.get('school_year'), d.get('student_type'), d.get('program'), d.get('last_level_attended'),
            d.get('is_ip'), d.get('is_pwd'), d.get('has_medication'), d.get('is_working'),
            d.get('residence_type'), d.get('employer_name'), d.get('marital_status'),
            d.get('is_gifted'), d.get('needs_assistance'), d.get('school_type'), 
            d.get('year_attended'), d.get('special_talents'), d.get('is_scholar'),
            siblings_json
        ))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        # Send email
        email_addr = d.get('email', '')
        if email_addr:
            send_email_notification(email_addr, d.get('name'), [])

        return jsonify({"status": "success", "db_id": new_id})
        
    except Exception as e:
        print(f"❌ SAVE ERROR: {e}")
        traceback.print_exc()
        if conn: 
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
        
    finally:
        if conn: 
            conn.close()

# --- DIAGNOSTIC ENDPOINT ---
@app.route('/check-api', methods=['GET'])
def check_api():
    """Check if API keys are configured"""
    return jsonify({
        "gemini_api_key": "SET" if GEMINI_API_KEY else "NOT SET",
        "email_sender": "SET" if EMAIL_SENDER else "NOT SET",
        "database_url": "SET" if DATABASE_URL else "NOT SET",
        "note": "Check https://your-app.onrender.com/check-api for status"
    })

# --- OTHER ROUTES (keep your existing ones) ---
@app.route('/upload-additional', methods=['POST'])
def upload_additional():
    files = request.files.getlist('files')
    rid, dtype = request.form.get('id'), request.form.get('type')
    
    if not files or not rid: 
        return jsonify({"error": "Data Missing"}), 400
    
    saved_paths = []
    for i, file in enumerate(files):
        if file and file.filename:
            timestamp = int(datetime.now().timestamp())
            fname = secure_filename(f"{dtype}_{rid}_{timestamp}_{i}_{file.filename}")
            path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
            file.save(path)
            saved_paths.append(path)

    full_path_str = ",".join(saved_paths)
    
    col_map = {'form137': 'form137_path', 'form138': 'form138_path', 'goodmoral': 'goodmoral_path'}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE records SET {col_map[dtype]} = %s WHERE id = %s", (full_path_str, rid))
        conn.commit()
        return jsonify({"status": "success"})
    finally: 
        conn.close()

@app.route('/delete-record/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    if not session.get('logged_in'): 
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        return jsonify({"success": True})
    finally: 
        conn.close()

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    
    print("\n" + "="*50)
    print("🚀 ASSISCAN SYSTEM STARTING")
    print("="*50)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"📧 Email: {'✅ SET' if EMAIL_SENDER else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print("="*50)
    
    if not GEMINI_API_KEY:
        print("❌ CRITICAL: GEMINI_API_KEY is missing!")
        print("👉 Go to Render Dashboard → Environment")
        print("👉 Add: GEMINI_API_KEY = your_key_from_google_ai_studio")
    
    app.run(host='0.0.0.0', port=port, debug=False)
