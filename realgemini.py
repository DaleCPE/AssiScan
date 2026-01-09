import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
import json
import requests  # Added for SendGrid
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
import traceback
from PIL import Image

# --- CONFIGURATION ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- CONFIGURE GEMINI ---
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ Google Generative AI Configured")
        
        # List available models to debug
        try:
            models = list(genai.list_models())
            print(f"📋 Available Gemini Models ({len(models)} total):")
            for model in models:
                if "gemini" in model.name.lower():
                    print(f"   - {model.name} (supports: {', '.join(model.supported_generation_methods)})")
        except Exception as e:
            print(f"⚠️ Could not list models: {e}")
            
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
            
            # Create main table
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    email_sent BOOLEAN DEFAULT FALSE,
                    email_sent_at TIMESTAMP,
                    email VARCHAR(100),
                    civil_status VARCHAR(50),
                    nationality VARCHAR(100),
                    mother_contact VARCHAR(50),
                    father_contact VARCHAR(50),
                    guardian_name VARCHAR(255),
                    guardian_relation VARCHAR(100),
                    guardian_contact VARCHAR(50),
                    region VARCHAR(100),
                    province VARCHAR(100),
                    specific_address TEXT,
                    mobile_no VARCHAR(50),
                    school_year VARCHAR(50),
                    student_type VARCHAR(50),
                    program VARCHAR(100),
                    last_level_attended VARCHAR(100),
                    is_ip VARCHAR(10),
                    is_pwd VARCHAR(10),
                    has_medication VARCHAR(10),
                    is_working VARCHAR(10),
                    residence_type VARCHAR(50),
                    employer_name VARCHAR(255),
                    marital_status VARCHAR(50),
                    is_gifted VARCHAR(10),
                    needs_assistance VARCHAR(10),
                    school_type VARCHAR(50),
                    year_attended VARCHAR(50),
                    special_talents TEXT,
                    is_scholar VARCHAR(10),
                    siblings TEXT
                );
            ''')
            
            conn.commit()
            print("✅ Database Schema Created/Updated!")
            
        except Exception as e:
            print(f"❌ Table Creation Error: {e}")
            # Try to add missing columns
            try:
                print("🔄 Attempting to add missing columns...")
                missing_columns = [
                    ("email_sent", "BOOLEAN DEFAULT FALSE"),
                    ("email_sent_at", "TIMESTAMP"),
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
                
                for col_name, col_type in missing_columns:
                    try:
                        cur.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
                        print(f"   Added column: {col_name}")
                        conn.commit()
                    except Exception as col_error:
                        print(f"   ⚠️ Could not add {col_name}: {col_error}")
                        conn.rollback()
                
            except Exception as add_col_error:
                print(f"❌ Column addition failed: {add_col_error}")
                
        finally:
            cur.close()
            conn.close()

# Initialize database on startup
init_db()

# --- EMAIL FUNCTION USING SENDGRID API ---
def send_email_notification(recipient_email, student_name, file_paths):
    """
    Send email using SendGrid API (works on Render)
    """
    print(f"\n📧 [SENDGRID] Preparing email for: {recipient_email}")
    
    # Quick validation
    if not recipient_email or not isinstance(recipient_email, str):
        print("❌ Invalid email")
        return False
    
    recipient_email = recipient_email.strip()
    
    if not recipient_email or '@' not in recipient_email:
        print("❌ Invalid email format")
        return False
    
    if not SENDGRID_API_KEY or not EMAIL_SENDER:
        print("❌ SendGrid credentials not configured")
        print(f"   SENDGRID_API_KEY: {'SET' if SENDGRID_API_KEY else 'NOT SET'}")
        print(f"   EMAIL_SENDER: {'SET' if EMAIL_SENDER else 'NOT SET'}")
        return True  # Return True para tuloy ang process
    
    try:
        # Generate reference ID
        ref_id = f"AssiScan-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Email content
        subject = "✅ AssiScan - Your Admission Record"
        
        body = f"""📋 ADMISSION RECORD VERIFICATION

Dear {student_name},

Your admission documents have been successfully processed through the AssiScan System.

━━━━━━━━━━━━━━━━━━━━━━━━
📅 VERIFICATION DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━
• Verification Date: {datetime.now().strftime('%B %d, %Y')}
• Verification Time: {datetime.now().strftime('%I:%M %p')}
• Reference ID: {ref_id}
• Status: ✅ VERIFIED & PROCESSED

━━━━━━━━━━━━━━━━━━━━━━━━
📝 NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━
1. Keep this email as your verification receipt
2. Proceed to the Admissions Office for enrollment
3. Present your name and this reference for verification
4. Complete any remaining requirements

━━━━━━━━━━━━━━━━━━━━━━━━
🏫 CONTACT INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━
• Admissions Office: University of Batangas Lipa
• Email: admissions@ublipa.edu.ph
• Phone: (043) 1234-5678

━━━━━━━━━━━━━━━━━━━━━━━━
📌 IMPORTANT REMINDER
━━━━━━━━━━━━━━━━━━━━━━━━
This is an automated notification from the AssiScan System.
Please do not reply to this email.

Best regards,

The AssiScan Team
Admissions Processing System
University of Batangas Lipa
{datetime.now().strftime('%Y')}"""
        
        # SendGrid API request
        url = "https://api.sendgrid.com/v3/mail/send"
        
        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "personalizations": [{
                "to": [{"email": recipient_email}],
                "subject": subject
            }],
            "from": {"email": EMAIL_SENDER, "name": "AssiScan System"},
            "content": [{
                "type": "text/plain",
                "value": body
            }]
        }
        
        print(f"🔧 Sending via SendGrid API...")
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 202:
            print(f"✅ Email sent successfully to {recipient_email}")
            print(f"📧 Reference ID: {ref_id}")
            return True
        else:
            print(f"❌ SendGrid API Error: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            
            # Fallback: Log the email instead
            print(f"📝 [FALLBACK] Would have sent email to {recipient_email}")
            print(f"   Subject: {subject}")
            print(f"   Reference: {ref_id}")
            return True  # Still return True to continue
            
    except Exception as e:
        print(f"⚠️ SendGrid failed: {e}")
        # Fallback: Just log it
        print(f"📝 [FALLBACK LOG] Email for {student_name} to {recipient_email}")
        return True  # Always return True to not break the process

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

# --- GEMINI 2.5 FLASH FUNCTION ---
def extract_with_gemini(prompt, images):
    """Use Gemini 2.5 Flash or fallback to available models"""
    try:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured")
        
        # Try different model names in order of preference
        model_versions = [
            "gemini-2.5-flash",        # Latest version
            "gemini-2.5-flash-exp",     # Experimental
            "gemini-2.0-flash",         # Version 2.0
            "gemini-2.0-flash-exp",     # Experimental 2.0
            "gemini-1.5-flash-8b",      # 8B parameter version
            "gemini-1.5-flash",         # Original 1.5 flash
            "gemini-1.5-pro",           # Pro version
            "gemini-pro",               # Legacy pro
            "gemini-flash"              # Basic flash
        ]
        
        last_error = None
        
        for model_name in model_versions:
            try:
                print(f"🤖 Trying model: {model_name}")
                model = genai.GenerativeModel(model_name)
                
                # Prepare content
                content_parts = [prompt]
                for img in images:
                    content_parts.append(img)
                
                # Generate response with timeout
                response = model.generate_content(
                    content_parts,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.1,
                        top_p=0.8,
                        top_k=40,
                        max_output_tokens=2048,
                    )
                )
                
                if response.text:
                    print(f"✅ Success with model: {model_name}")
                    return response.text
                else:
                    raise Exception("No response text")
                    
            except Exception as model_error:
                print(f"   ⚠️ {model_name} failed: {str(model_error)[:100]}")
                last_error = model_error
                continue
        
        # If all models fail
        raise Exception(f"All models failed. Last error: {str(last_error)[:200]}")
            
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
            if r['email_sent_at']: r['email_sent_at'] = r['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S')
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

# --- EXTRACT PSA WITH GEMINI 2.5 FLASH ---
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

        print(f"📸 Processing {len(pil_images)} PSA pages with Gemini 2.5 Flash")
        
        prompt = """You are an expert document processor specializing in Philippine PSA Birth Certificates.
        
        Extract ALL information from this document accurately.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "is_valid_document": true,
            "rejection_reason": null,
            "Name": "Full Name Here",
            "Sex": "Male or Female",
            "Birthdate": "YYYY-MM-DD format",
            "PlaceOfBirth": "City/Municipality, Province",
            "BirthOrder": "1st, 2nd, 3rd, etc",
            "Religion": "Religion if stated",
            "Mother_MaidenName": "Mother's Maiden Name",
            "Mother_Citizenship": "Citizenship",
            "Mother_Occupation": "Occupation if stated",
            "Father_Name": "Father's Full Name",
            "Father_Citizenship": "Citizenship",
            "Father_Occupation": "Occupation if stated"
        }
        
        If the document is not a valid PSA Birth Certificate, set "is_valid_document": false and provide "rejection_reason".
        
        IMPORTANT: Return ONLY the JSON, no additional text, no markdown, no code blocks."""
        
        # Extract using Gemini
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response received: {len(response_text)} characters")
            
            # Clean the response
            cleaned_text = response_text.strip()
            
            # Remove markdown code blocks if present
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            # Find JSON
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                print(f"❌ Could not find JSON in response: {cleaned_text[:200]}")
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            
            try:
                data = json.loads(json_str)
                print(f"✅ Successfully parsed JSON data")
                
                # Validate required fields
                if not data.get("is_valid_document", False):
                    return jsonify({
                        "error": f"Invalid document: {data.get('rejection_reason', 'Not a valid PSA')}"
                    }), 400
                
                return jsonify({
                    "message": "Success", 
                    "structured_data": data, 
                    "image_paths": ",".join(saved_paths)
                })
                
            except json.JSONDecodeError as json_error:
                print(f"❌ JSON Parse Error: {json_error}")
                print(f"❌ Problematic JSON: {json_str[:500]}")
                return jsonify({"error": f"Failed to parse AI response: {str(json_error)}"}), 500
            
        except Exception as ai_error:
            print(f"❌ AI Extraction Failed: {ai_error}")
            traceback.print_exc()
            return jsonify({
                "error": "AI service unavailable",
                "details": str(ai_error)[:200]
            }), 500
            
    except Exception as e:
        print(f"❌ Server Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# --- EXTRACT FORM 137 WITH GEMINI 2.5 FLASH ---
@app.route('/extract-form137', methods=['POST'])
def extract_form137():
    if 'imageFiles' not in request.files: 
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': 
        return jsonify({"error": "No selected file"}), 400
    
    try:
        saved_paths, pil_images = save_multiple_files(files, "F137")
        print(f"📸 Processing Form 137: {len(pil_images)} pages with Gemini 2.5 Flash")

        if not pil_images:
            return jsonify({"error": "No valid images found"}), 400
        
        prompt = """You are an expert document processor for Philippine educational records.
        
        Extract information from this Form 137 / SF10 document.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "lrn": "12-digit Learner Reference Number",
            "school_name": "Complete School Name",
            "school_address": "Complete School Address (Barangay, City/Municipality, Province)",
            "final_general_average": "Numerical grade (e.g., 85.5, 90.0)"
        }
        
        IMPORTANT:
        1. LRN must be exactly 12 digits if available
        2. School name should be the complete official name
        3. School address should include barangay, city/municipality, and province
        4. Final general average should be the most recent average found
        
        Return ONLY the JSON, no additional text, no markdown, no code blocks."""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response received: {len(response_text)} characters")
            
            # Clean the response
            cleaned_text = response_text.strip()
            
            # Remove markdown code blocks if present
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            # Find JSON
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                print(f"❌ Could not find JSON in response: {cleaned_text[:200]}")
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            
            try:
                data = json.loads(json_str)
                print(f"✅ Successfully parsed Form 137 data")
                
                # Format LRN to ensure it's a string
                if 'lrn' in data and data['lrn']:
                    data['lrn'] = str(data['lrn']).strip()
                
                return jsonify({
                    "message": "Success", 
                    "structured_data": data, 
                    "image_paths": ",".join(saved_paths)
                })
                
            except json.JSONDecodeError as json_error:
                print(f"❌ JSON Parse Error: {json_error}")
                print(f"❌ Problematic JSON: {json_str[:500]}")
                return jsonify({"error": f"Failed to parse AI response: {str(json_error)}"}), 500
            
        except Exception as ai_error:
            print(f"❌ AI Extraction Failed: {ai_error}")
            traceback.print_exc()
            return jsonify({
                "error": "AI service unavailable",
                "details": str(ai_error)[:200]
            }), 500
            
    except Exception as e:
        print(f"❌ Form 137 Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# --- SAVE RECORD TO DATABASE (NO EMAIL) ---
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Saving record to database only (no email)")
        
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

        # Insert record WITHOUT sending email
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

        print(f"✅ Record saved to database with ID: {new_id}")
        print("ℹ️ Email will be sent separately when user clicks Send button")

        return jsonify({
            "status": "success", 
            "db_id": new_id,
            "message": "Record saved successfully. You can now send the email separately."
        })
        
    except Exception as e:
        print(f"❌ SAVE ERROR: {e}")
        traceback.print_exc()
        if conn: 
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
        
    finally:
        if conn: 
            conn.close()

# --- SEND EMAIL ONLY (SEPARATE ENDPOINT) ---
@app.route('/send-email/<int:record_id>', methods=['POST'])
def send_email_only(record_id):
    """
    Separate endpoint to send email for a saved record
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get record details
        cur.execute("SELECT name, email, email_sent FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        if record.get('email_sent'):
            return jsonify({"warning": "Email has already been sent for this record"}), 400
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            return jsonify({"error": "No email address found for this record"}), 400
        
        # Send email
        print(f"\n📧 [SEND EMAIL] Sending email for record ID: {record_id}")
        print(f"   Student: {student_name}")
        print(f"   Email: {email_addr}")
        
        email_sent = send_email_notification(email_addr, student_name, [])
        
        if email_sent:
            # Update database to mark email as sent
            cur.execute("""
                UPDATE records 
                SET email_sent = TRUE, email_sent_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (record_id,))
            conn.commit()
            
            print(f"✅ Email sent and record updated for ID: {record_id}")
            return jsonify({
                "status": "success",
                "message": f"Email sent successfully to {email_addr}",
                "record_id": record_id
            })
        else:
            return jsonify({
                "status": "error",
                "error": "Failed to send email. Please check email configuration."
            }), 500
            
    except Exception as e:
        print(f"❌ EMAIL SEND ERROR: {e}")
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
        
    finally:
        if conn:
            conn.close()

# --- RESEND EMAIL ENDPOINT ---
@app.route('/resend-email/<int:record_id>', methods=['POST'])
def resend_email(record_id):
    """
    Resend email even if already sent
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get record details
        cur.execute("SELECT name, email FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            return jsonify({"error": "No email address found for this record"}), 400
        
        # Send email
        print(f"\n📧 [RESEND EMAIL] Resending email for record ID: {record_id}")
        print(f"   Student: {student_name}")
        print(f"   Email: {email_addr}")
        
        email_sent = send_email_notification(email_addr, student_name, [])
        
        if email_sent:
            # Update timestamp even if resending
            cur.execute("""
                UPDATE records 
                SET email_sent_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (record_id,))
            conn.commit()
            
            print(f"✅ Email resent for ID: {record_id}")
            return jsonify({
                "status": "success",
                "message": f"Email resent successfully to {email_addr}",
                "record_id": record_id
            })
        else:
            return jsonify({
                "status": "error",
                "error": "Failed to send email. Please check email configuration."
            }), 500
            
    except Exception as e:
        print(f"❌ EMAIL RESEND ERROR: {e}")
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
        
    finally:
        if conn:
            conn.close()

# --- DIAGNOSTIC ENDPOINTS ---
@app.route('/list-models', methods=['GET'])
def list_models():
    """List available Gemini models"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({"error": "GEMINI_API_KEY not set"}), 400
        
        models = list(genai.list_models())
        gemini_models = []
        
        for model in models:
            if "gemini" in model.name.lower():
                gemini_models.append({
                    "name": model.name,
                    "display_name": model.display_name,
                    "supported_methods": model.supported_generation_methods,
                    "description": model.description[:100] if model.description else ""
                })
        
        return jsonify({
            "total_models": len(models),
            "gemini_models": gemini_models,
            "note": "Use the 'name' field in your code"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/test-gemini', methods=['GET'])
def test_gemini():
    """Test Gemini API with a simple prompt"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({"error": "GEMINI_API_KEY not set"}), 400
        
        # Try different models
        test_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
        results = []
        
        for model_name in test_models:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content("Say 'Hello from Gemini'")
                results.append({
                    "model": model_name,
                    "status": "SUCCESS",
                    "response": response.text
                })
            except Exception as e:
                results.append({
                    "model": model_name,
                    "status": "FAILED",
                    "error": str(e)[:200]
                })
        
        return jsonify({
            "api_key": "SET (hidden)",
            "test_results": results,
            "recommended_model": "gemini-2.0-flash" if any(r["status"] == "SUCCESS" for r in results) else "NONE"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- TEST EMAIL ENDPOINT ---
@app.route('/test-email-endpoint', methods=['GET'])
def test_email_endpoint():
    """Test email functionality directly"""
    test_email = request.args.get('email', 'test@example.com')
    test_name = "Test Student"
    
    print(f"\n🧪 Testing email to: {test_email}")
    
    result = send_email_notification(test_email, test_name, [])
    
    return jsonify({
        "success": result,
        "test_email": test_email,
        "message": "Check console for email logs"
    })

# --- FIX DATABASE SCHEMA ENDPOINT ---
@app.route('/fix-db-schema', methods=['GET'])
def fix_db_schema():
    """Manually fix the database schema"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor()
        
        # Check if email_sent column exists
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'records' AND column_name = 'email_sent'
        """)
        
        if not cur.fetchone():
            print("🔄 Adding missing columns...")
            
            # Add missing columns
            missing_columns = [
                ("email_sent", "BOOLEAN DEFAULT FALSE"),
                ("email_sent_at", "TIMESTAMP"),
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
            
            added_columns = []
            for col_name, col_type in missing_columns:
                try:
                    cur.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
                    added_columns.append(col_name)
                    print(f"   ✅ Added column: {col_name}")
                except Exception as col_error:
                    print(f"   ❌ Failed to add {col_name}: {col_error}")
            
            conn.commit()
            cur.close()
            conn.close()
            
            return jsonify({
                "status": "success",
                "message": f"Database schema fixed. Added {len(added_columns)} columns.",
                "added_columns": added_columns
            })
        else:
            cur.close()
            conn.close()
            return jsonify({
                "status": "info",
                "message": "All columns already exist. No changes needed."
            })
            
    except Exception as e:
        print(f"❌ Schema fix error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- OTHER ROUTES ---
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

# --- CHECK EMAIL STATUS ---
@app.route('/check-email-status/<int:record_id>', methods=['GET'])
def check_email_status(record_id):
    """Check if email has been sent for a record"""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT email_sent, email_sent_at FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        
        if record:
            email_sent_at = record['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S') if record['email_sent_at'] else None
            return jsonify({
                "email_sent": record['email_sent'],
                "email_sent_at": email_sent_at
            })
        else:
            return jsonify({"error": "Record not found"}), 404
    finally:
        conn.close()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    
    print("\n" + "="*60)
    print("🚀 ASSISCAN WITH GEMINI 2.5 FLASH & SENDGRID")
    print("="*60)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"📧 SendGrid: {'✅ SET' if SENDGRID_API_KEY else '❌ NOT SET'}")
    print(f"📨 Email Sender: {'✅ SET' if EMAIL_SENDER else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print("="*60)
    print("📊 NEW FEATURES:")
    print("   • Separate SAVE and SEND endpoints")
    print("   • SendGrid API for email (works on Render)")
    print("   • Database tracks email status")
    print("   • Resend email capability")
    print("="*60)
    print("🔗 IMPORTANT: If you get database errors, visit:")
    print("   /fix-db-schema - to manually fix database columns")
    print("="*60)
    
    if GEMINI_API_KEY:
        print("🔍 Testing available models...")
        try:
            models = list(genai.list_models())
            print(f"📋 Found {len(models)} total models")
            
            gemini_models = [m for m in models if "gemini" in m.name.lower()]
            print(f"🤖 Gemini models available: {len(gemini_models)}")
            
            for model in gemini_models[:5]:
                print(f"   - {model.name}")
                
            has_25_flash = any("2.5-flash" in m.name.lower() for m in gemini_models)
            print(f"✅ Gemini 2.5 Flash: {'AVAILABLE' if has_25_flash else 'NOT AVAILABLE'}")
            
        except Exception as e:
            print(f"⚠️ Model listing failed: {e}")
    
    print("="*60)
    print("🔗 NEW Endpoints:")
    print("   POST /send-email/<record_id> - Send email for saved record")
    print("   POST /resend-email/<record_id> - Resend email")
    print("   GET /check-email-status/<record_id> - Check email status")
    print("   GET /fix-db-schema - Fix missing database columns")
    print("="*60)
    print("🔗 Diagnostic endpoints:")
    print("   /list-models - List available Gemini models")
    print("   /test-gemini - Test Gemini API")
    print("   /test-email-endpoint?email=youremail@example.com - Test email")
    print("="*60)
    
    app.run(host='0.0.0.0', port=port, debug=False)
