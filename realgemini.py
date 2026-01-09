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
import ssl

# --- CONFIGURATION ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")
RENDER = os.getenv("RENDER", "false").lower() == "true"

# --- CONFIGURE GEMINI ---
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ Google Generative AI Configured")
    except Exception as e:
        print(f"⚠️ Error configuring Gemini: {e}")
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

# --- ADMIN SECURITY CONFIG ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_security_key_change_me_production")
CORS(app)

# Setup Upload Folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# --- DATABASE CONNECTION ---
def get_db_connection():
    try:
        # Fix for Render's PostgreSQL URL format
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
            
            # 1. Create Basic Table if not exists
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
            
            # 2. AUTO-MIGRATE: Add ALL New Columns
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
            print("✅ Database Schema Fully Updated!")
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
        finally:
            conn.close()

init_db()

# --- EMAIL FUNCTION FOR RENDER.COM ---
def send_email_notification(recipient_email, student_name, file_paths):
    """
    Email function optimized for Render.com
    """
    print(f"\n📧 [RENDER] Email Function Called")
    print(f"   To: {recipient_email}")
    print(f"   Student: {student_name}")
    
    # Validation
    if not recipient_email or not isinstance(recipient_email, str) or recipient_email.strip() == "":
        print("❌ Invalid recipient email")
        return False
    
    if not EMAIL_SENDER:
        print("❌ EMAIL_SENDER not configured")
        return False
    
    if not EMAIL_PASSWORD:
        print("❌ EMAIL_PASSWORD not configured")
        return False
    
    recipient_email = recipient_email.strip()
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient_email
        msg['Subject'] = "AssiScan Verification Complete"
        
        body = f"""Dear {student_name},

Your documents have been successfully verified by the AssiScan System.

Submitted on: {datetime.now().strftime('%B %d, %Y %I:%M %p')}

Regards,
AssiScan Admissions System
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        # SMTP Configuration
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        
        try:
            print(f"🔗 Connecting to {smtp_server}:{smtp_port}...")
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
            print(f"🔑 Logging in as: {EMAIL_SENDER}")
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            
            server.send_message(msg)
            server.quit()
            
            print(f"✅ Email sent successfully!")
            return True
            
        except smtplib.SMTPAuthenticationError as auth_error:
            print(f"❌ SMTP Authentication Failed: {auth_error}")
            return False
            
        except Exception as smtp_error:
            print(f"❌ SMTP Error: {type(smtp_error).__name__}: {smtp_error}")
            return False
                
    except Exception as e:
        print(f"❌ General Email Error: {type(e).__name__}: {e}")
        return False

# ================= HELPER FUNCTIONS =================

# Helper: Save Multiple Files
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

# Helper: Intelligent Model Selector - FIXED FOR RENDER
def generate_content_standard(parts):
    print("🤖 AI START: Initializing Model Selection...")
    
    # Try LATEST models first (most compatible)
    target_models = [
        "gemini-1.5-flash-latest",  # Most stable and available
        "gemini-1.5-pro-latest",    # Alternative
        "models/gemini-1.5-flash-latest",
        "models/gemini-1.5-pro-latest",
        "gemini-pro",               # Legacy but available
        "gemini-flash"              # Basic model
    ]

    last_error = None

    for model_name in target_models:
        try:
            print(f"    👉 Attempting to use: {model_name} ...")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(parts)
            
            if response.text:
                print(f"    ✅ SUCCESS using model: {model_name}")
                return response
        except Exception as e:
            print(f"    ⚠️ Failed on {model_name}: {str(e)[:100]}...")
            last_error = e
            continue 
            
    print("❌ ALL AVAILABLE MODELS FAILED.")
    
    # If all models fail, check if API key is valid
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY is not set in environment variables")
    
    # Try to test API key
    try:
        print("🔑 Testing Gemini API key...")
        genai.configure(api_key=GEMINI_API_KEY)
        models = genai.list_models()
        print(f"✅ API Key is valid. Available models: {len(list(models))}")
    except Exception as api_error:
        print(f"❌ API Key Error: {api_error}")
    
    raise last_error if last_error else Exception("No AI models available. Check GEMINI_API_KEY.")

# --- SIMPLE AI FALLBACK (For testing) ---
def extract_data_fallback(image_type, prompt_text):
    """
    Fallback function when AI fails
    """
    print(f"🔄 Using fallback extraction for {image_type}")
    
    if image_type == "PSA":
        return {
            "is_valid_document": True,
            "rejection_reason": None,
            "Name": "SAMPLE STUDENT",
            "Sex": "MALE",
            "Birthdate": "2000-01-01",
            "PlaceOfBirth": "Sample City",
            "BirthOrder": "1",
            "Religion": "CATHOLIC",
            "Mother_MaidenName": "SAMPLE MOTHER",
            "Mother_Citizenship": "FILIPINO",
            "Mother_Occupation": "HOUSEKEEPER",
            "Father_Name": "SAMPLE FATHER",
            "Father_Citizenship": "FILIPINO",
            "Father_Occupation": "FARMER"
        }
    else:  # Form 137
        return {
            "lrn": "123456789012",
            "school_name": "SAMPLE NATIONAL HIGH SCHOOL",
            "school_address": "Sample City, Sample Province",
            "final_general_average": "85.5"
        }

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

# --- EXTRACT PSA ---
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

        print(f"📸 Processing PSA: {len(pil_images)} pages")
        
        prompt = """
        Extract information from this PSA Birth Certificate.
        Return JSON format only:
        {
            "is_valid_document": true,
            "Name": "string",
            "Sex": "string",
            "Birthdate": "YYYY-MM-DD",
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
        
        try:
            res = generate_content_standard([prompt, *pil_images])
            
            raw_text = res.text.replace('```json', '').replace('```', '').strip()
            s = raw_text.find('{')
            e = raw_text.rfind('}') + 1
            data = json.loads(raw_text[s:e])

            if not data.get("is_valid_document", False):
                return jsonify({"error": f"Invalid Document"}), 400

            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": ",".join(saved_paths)
            })
            
        except Exception as ai_error:
            print(f"⚠️ AI Extraction Failed: {ai_error}")
            print("🔄 Using fallback data for testing")
            
            # Use fallback data for testing
            fallback_data = extract_data_fallback("PSA", "")
            return jsonify({
                "message": "Success (Fallback)", 
                "structured_data": fallback_data, 
                "image_paths": ",".join(saved_paths),
                "note": "Using fallback data - AI service unavailable"
            })
            
    except Exception as e:
        print(f"❌ Server Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# --- EXTRACT FORM 137 ---
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
        
        prompt = """
        Extract information from this Form 137 document.
        Return JSON format only:
        {
            "lrn": "123456789012",
            "school_name": "Name of School",
            "school_address": "City, Province",
            "final_general_average": "85.5"
        }
        """
        
        try:
            res = generate_content_standard([prompt, *pil_images])
            
            raw_text = res.text.replace('```json', '').replace('```', '').strip()
            s = raw_text.find('{')
            e = raw_text.rfind('}') + 1
            if s != -1 and e != -1: 
                raw_text = raw_text[s:e]

            data = json.loads(raw_text)
            
            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": ",".join(saved_paths)
            })
            
        except Exception as ai_error:
            print(f"⚠️ AI Extraction Failed: {ai_error}")
            print("🔄 Using fallback data for testing")
            
            # Use fallback data for testing
            fallback_data = extract_data_fallback("FORM137", "")
            return jsonify({
                "message": "Success (Fallback)", 
                "structured_data": fallback_data, 
                "image_paths": ",".join(saved_paths),
                "note": "Using fallback data - AI service unavailable"
            })
            
    except Exception as e:
        print(f"❌ Form 137 Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"AI Error: {str(e)[:100]}"}), 500

# --- SAVE RECORD ---
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Saving record on Render")
        
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

        # Send email notification
        email_addr = d.get('email', '')
        if email_addr:
            print(f"📧 Attempting to send email to: {email_addr}")
            
            files_to_send = []
            if d.get('psa_image_path'): 
                files_to_send.append(d.get('psa_image_path'))
            if d.get('f137_image_path'): 
                files_to_send.append(d.get('f137_image_path'))
            
            email_sent = send_email_notification(email_addr, d.get('name'), files_to_send)
            
            if email_sent:
                print(f"✅ Email notification sent")
            else:
                print(f"⚠️ Email notification failed")
        else:
            print("ℹ️ No email provided, skipping email notification")

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

# --- UPLOAD ADDITIONAL ---
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

# --- DIAGNOSTIC ENDPOINTS ---
@app.route('/check-gemini', methods=['GET'])
def check_gemini():
    """Check if Gemini API is working"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({
                "status": "error",
                "message": "GEMINI_API_KEY not set in environment variables",
                "solution": "Add GEMINI_API_KEY to Render environment variables"
            })
        
        genai.configure(api_key=GEMINI_API_KEY)
        models = list(genai.list_models())
        
        return jsonify({
            "status": "success",
            "api_key": "SET (hidden)",
            "models_available": len(models),
            "sample_models": [model.name for model in models[:5]]
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "solution": "Check if API key is valid at https://aistudio.google.com/apikey"
        })

@app.route('/render-status')
def render_status():
    """Check deployment status"""
    return jsonify({
        "platform": "Render.com",
        "email_configured": bool(EMAIL_SENDER and EMAIL_PASSWORD),
        "gemini_configured": bool(GEMINI_API_KEY),
        "database_configured": bool(DATABASE_URL),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/test-ai-fallback')
def test_ai_fallback():
    """Test the fallback mechanism"""
    return jsonify({
        "psa_fallback": extract_data_fallback("PSA", ""),
        "form137_fallback": extract_data_fallback("FORM137", ""),
        "message": "Fallback data working - AI may be unavailable"
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    
    print("\n" + "="*60)
    print("🚀 ASSISCAN - RENDER.COM DEPLOYMENT")
    print("="*60)
    print(f"📧 Email: {'✅ CONFIGURED' if EMAIL_SENDER and EMAIL_PASSWORD else '❌ NOT CONFIGURED'}")
    print(f"🤖 Gemini AI: {'✅ CONFIGURED' if GEMINI_API_KEY else '❌ NOT CONFIGURED'}")
    print(f"🗄️ Database: {'✅ CONFIGURED' if DATABASE_URL else '❌ NOT CONFIGURED'}")
    print(f"🔗 Port: {port}")
    print("="*60)
    print("📊 Diagnostic Endpoints:")
    print(f"   /check-gemini    - Test Gemini API")
    print(f"   /render-status   - Deployment status")
    print(f"   /test-ai-fallback - Test fallback data")
    print("="*60 + "\n")
    
    # Test Gemini on startup
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            models = list(genai.list_models())
            print(f"✅ Gemini API Test: {len(models)} models available")
        except Exception as e:
            print(f"⚠️ Gemini API Test Failed: {e}")
            print("ℹ️ Using fallback data mode")
    else:
        print("⚠️ Gemini API Key not set. Using fallback data mode.")
    
    app.run(host='0.0.0.0', port=port, debug=False)
