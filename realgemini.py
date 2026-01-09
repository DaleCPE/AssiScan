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
                ("siblings", "TEXT")  # <--- NEW: SIBLINGS FIELD (JSON)
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

# --- EMAIL FUNCTION (UPDATED) ---
def send_email_notification(recipient_email, student_name, file_paths):
    """
    Send email notification with attachments
    """
    print(f"\n📧 Email Function Called")
    print(f"   To: {recipient_email}")
    print(f"   Student: {student_name}")
    
    # Validation
    if not recipient_email or recipient_email.strip() == "":
        print("❌ No recipient email provided")
        return False
    
    if not EMAIL_SENDER:
        print("❌ Email sender not configured")
        return False
    
    if not EMAIL_PASSWORD:
        print("❌ Email password not configured")
        return False
    
    # Clean email
    recipient_email = recipient_email.strip()
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient_email
        msg['Subject'] = "AssiScan Verification Complete - Document Copy"
        
        # Email body
        body = f"""Dear {student_name},

Your documents have been successfully verified by the AssiScan System.

Attached are copies of your submitted documents:
1. PSA/Birth Certificate
2. Form 137/SF10

Please keep these documents for your records.

Regards,
AssiScan Admissions System
{datetime.now().strftime('%B %d, %Y %I:%M %p')}
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Add attachments (if any)
        attachment_count = 0
        if file_paths and isinstance(file_paths, list):
            for path_item in file_paths:
                if path_item and isinstance(path_item, str):
                    # Handle comma-separated paths
                    paths = path_item.split(',')
                    for file_path in paths:
                        file_path = file_path.strip()
                        if file_path and os.path.exists(file_path):
                            try:
                                # Get file size
                                file_size = os.path.getsize(file_path)
                                if file_size > 25 * 1024 * 1024:  # 25MB limit
                                    print(f"⚠️ File too large: {file_path} ({file_size/1024/1024:.1f}MB)")
                                    continue
                                
                                # Open and attach file
                                with open(file_path, "rb") as f:
                                    filename = os.path.basename(file_path)
                                    part = MIMEBase('application', 'octet-stream')
                                    part.set_payload(f.read())
                                    encoders.encode_base64(part)
                                    part.add_header(
                                        'Content-Disposition',
                                        f'attachment; filename="{filename}"'
                                    )
                                    msg.attach(part)
                                    attachment_count += 1
                                    print(f"   ✅ Attached: {filename}")
                            except Exception as file_error:
                                print(f"   ⚠️ Failed to attach {file_path}: {file_error}")
        
        print(f"📎 Total attachments: {attachment_count}")
        
        # SMTP Configuration
        smtp_server = "smtp.gmail.com"
        
        # APPROACH 1: Try TLS (Port 587)
        try:
            print(f"🔗 Trying TLS (Port 587)...")
            server = smtplib.SMTP(smtp_server, 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"✅ Email sent successfully to {recipient_email}")
            return True
            
        except smtplib.SMTPAuthenticationError as auth_error:
            print(f"❌ SMTP Authentication Failed: {auth_error}")
            print("   Please check:")
            print("   1. Email address and password are correct")
            print("   2. For Gmail: Enable 'Less Secure Apps' at:")
            print("      https://myaccount.google.com/lesssecureapps")
            print("   3. If 2FA is enabled, use App Password")
            return False
            
        except smtplib.SMTPRecipientsRefused as recip_error:
            print(f"❌ Recipient refused: {recip_error}")
            print("   Invalid email address format")
            return False
            
        except Exception as tls_error:
            print(f"⚠️ TLS failed: {tls_error}")
            
            # APPROACH 2: Try SSL (Port 465) as fallback
            try:
                print(f"🔗 Trying SSL (Port 465)...")
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(smtp_server, 465, context=context, timeout=30) as server:
                    server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                    server.send_message(msg)
                print(f"✅ Email sent successfully via SSL")
                return True
                
            except Exception as ssl_error:
                print(f"❌ SSL also failed: {ssl_error}")
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

# Helper: Intelligent Model Selector (UPDATED FOR GEMINI 2.5 FLASH)
def generate_content_standard(parts):
    print("🤖 AI START: Initializing Model Selection...")
    
    # LISTahan ng Models (Inuuna ang request mo)
    # Note: Kung wala pang 'gemini-2.5-flash' sa API, gagamitin niya ang next available (2.0 or 1.5)
    target_models = [
        "gemini-2.5-flash",        # <--- PRIORITY 1: Your Request
        "models/gemini-2.5-flash", # Alternative format
        "gemini-2.0-flash",        # <--- PRIORITY 2: Newest Standard
        "models/gemini-2.0-flash",
        "gemini-1.5-flash",        # <--- PRIORITY 3: Stable Fallback
        "models/gemini-1.5-flash",
        "gemini-1.5-pro"
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
            # Kapag nag-fail (halimbawa: wala pang 2.5), itatry niya ang susunod sa listahan
            # print(f"    ⚠️ Failed on {model_name}: {str(e)}") 
            # (Optional: uncomment above line to see specific errors)
            last_error = e
            continue 
            
    print("❌ ALL AVAILABLE MODELS FAILED.")
    raise last_error if last_error else Exception("No AI models available.")

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
    if 'imageFiles' not in request.files: return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': return jsonify({"error": "No selected file"}), 400

    try:
        saved_paths, pil_images = save_multiple_files(files, "PSA")
        
        if not pil_images:
             return jsonify({"error": "No valid images found"}), 400

        prompt = """
        SYSTEM ROLE: Strict Philippine Document Verifier.
        TASK: Analyze these images. It MUST be a "Certificate of Live Birth".
        If there are multiple pages, analyze them as one document.
        OUTPUT FORMAT (JSON ONLY):
        {
            "is_valid_document": boolean,
            "rejection_reason": "string or null",
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
        
        res = generate_content_standard([prompt, *pil_images])
        
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        s = raw_text.find('{')
        e = raw_text.rfind('}') + 1
        data = json.loads(raw_text[s:e])

        if not data.get("is_valid_document", False):
            return jsonify({"error": f"Invalid Document: {data.get('rejection_reason')}"}), 400

        return jsonify({"message": "Success", "structured_data": data, "image_paths": ",".join(saved_paths)})
    except Exception as e:
        traceback.print_exc() 
        return jsonify({"error": f"Server Error: {str(e)}"}), 500

# --- EXTRACT FORM 137 ---
@app.route('/extract-form137', methods=['POST'])
def extract_form137():
    if 'imageFiles' not in request.files: return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': return jsonify({"error": "No selected file"}), 400
    
    try:
        saved_paths, pil_images = save_multiple_files(files, "F137")
        print(f"📸 Processing Form 137: {len(pil_images)} pages")

        if not pil_images:
            return jsonify({"error": "No valid images found"}), 400
        
        prompt = """
        SYSTEM ROLE: Expert Data Encoder.
        TASK: Extract details from Form 137 / SF10.
        This document may span multiple pages. Look across ALL pages to find the requested info.
        JSON FORMAT ONLY:
        {
            "lrn": "123456789012",
            "school_name": "Name of School",
            "school_address": "City, Province",
            "final_general_average": "85" (Get the latest general average found)
        }
        """
        
        res = generate_content_standard([prompt, *pil_images])
        
        raw_text = res.text.replace('```json', '').replace('```', '').strip()
        s = raw_text.find('{')
        e = raw_text.rfind('}') + 1
        if s != -1 and e != -1: raw_text = raw_text[s:e]

        try: data = json.loads(raw_text)
        except: return jsonify({"error": "AI Extraction Failed (Invalid JSON)"}), 500
        
        return jsonify({"message": "Success", "structured_data": data, "image_paths": ",".join(saved_paths)})
    except Exception as e:
        return jsonify({"error": f"AI Error: {str(e)}"}), 500

# --- SAVE RECORD ---
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Received Data: {d}")
        
        siblings_list = d.get('siblings', [])
        siblings_json = json.dumps(siblings_list)
        
        conn = get_db_connection()
        if not conn: return jsonify({"error": "DB Connection Failed"}), 500
        cur = conn.cursor()
        
        if d.get('name') and d.get('birthdate'):
            cur.execute("SELECT id FROM records WHERE LOWER(name) = LOWER(%s) AND birthdate = %s", (d.get('name'), d.get('birthdate')))
            if cur.fetchone():
                return jsonify({"status": "error", "error": "DUPLICATE_ENTRY", "message": f"Record already exists."}), 409

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
            d.get('name'), d.get('sex'), d.get('birthdate') or None, d.get('birthplace'), d.get('birth_order'), d.get('religion'), d.get('age'),
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
            
            d.get('is_gifted'), d.get('needs_assistance'), d.get('school_type'), d.get('year_attended'), d.get('special_talents'), d.get('is_scholar'),
            siblings_json
        ))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        # Send email notification
        email_addr = d.get('email', '')
        files_to_send = []
        if d.get('psa_image_path'): 
            files_to_send.append(d.get('psa_image_path'))
        if d.get('f137_image_path'): 
            files_to_send.append(d.get('f137_image_path'))

        if email_addr:
            print(f"📧 Attempting to send email to: {email_addr}")
            email_sent = send_email_notification(email_addr, d.get('name'), files_to_send)
            if email_sent:
                print(f"✅ Email notification sent to {email_addr}")
            else:
                print(f"⚠️ Email notification failed for {email_addr}")
        else:
            print("ℹ️ No email provided, skipping email notification")

        return jsonify({"status": "success", "db_id": new_id})
    except Exception as e:
        print(f"❌ SAVE ERROR: {e}")
        if conn: conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if conn: conn.close()

# --- UPLOAD ADDITIONAL ---
@app.route('/upload-additional', methods=['POST'])
def upload_additional():
    files = request.files.getlist('files')
    rid, dtype = request.form.get('id'), request.form.get('type')
    
    if not files or not rid: return jsonify({"error": "Data Missing"}), 400
    
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
    finally: conn.close()

@app.route('/delete-record/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- EMAIL TESTING ENDPOINT ---
@app.route('/test-email', methods=['GET'])
def test_email():
    """
    Test email functionality
    """
    print("\n" + "="*50)
    print("🧪 TESTING EMAIL FUNCTION")
    print("="*50)
    
    # Check if credentials are loaded
    print(f"Email Sender: {'✅ SET' if EMAIL_SENDER else '❌ NOT SET'}")
    print(f"Email Password: {'✅ SET' if EMAIL_PASSWORD else '❌ NOT SET'}")
    
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        return jsonify({
            "status": "error",
            "message": "Email credentials not configured",
            "instructions": [
                "1. Set EMAIL_SENDER in environment variables",
                "2. Set EMAIL_PASSWORD in environment variables",
                "3. For Gmail: Enable 'Less Secure Apps' or use App Password"
            ]
        })
    
    # Test email
    test_recipient = "your-test-email@gmail.com"  # ⬅️ PALITAN MO ITO NG ACTUAL EMAIL MO
    test_name = "Test Student"
    
    # Create a test file
    test_file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'test_email.txt')
    try:
        with open(test_file_path, 'w') as f:
            f.write("This is a test attachment for AssiScan email testing.")
        print(f"📁 Created test file: {test_file_path}")
    except Exception as e:
        print(f"⚠️ Could not create test file: {e}")
        test_file_path = None
    
    # Send test email
    print(f"\n📤 Sending test email to: {test_recipient}")
    result = send_email_notification(
        recipient_email=test_recipient,
        student_name=test_name,
        file_paths=[test_file_path] if test_file_path else []
    )
    
    return jsonify({
        "status": "success" if result else "error",
        "message": "Test email sent successfully" if result else "Failed to send test email",
        "sender": EMAIL_SENDER,
        "recipient": test_recipient,
        "result": result,
        "note": "Check terminal for detailed logs"
    })

# --- EMAIL DEBUG ENDPOINT ---
@app.route('/debug-email', methods=['GET'])
def debug_email():
    """
    Debug email configuration
    """
    print("\n" + "="*50)
    print("🔧 DEBUGGING EMAIL CONFIGURATION")
    print("="*50)
    
    debug_info = {
        "env_variables": {
            "EMAIL_SENDER": EMAIL_SENDER if EMAIL_SENDER else "NOT SET",
            "EMAIL_PASSWORD": "SET" if EMAIL_PASSWORD else "NOT SET (but hidden)"
        },
        "checks": []
    }
    
    # Check 1: Environment variables
    if not EMAIL_SENDER:
        debug_info["checks"].append({"check": "EMAIL_SENDER", "status": "❌ NOT SET"})
    else:
        debug_info["checks"].append({"check": "EMAIL_SENDER", "status": "✅ SET", "value": EMAIL_SENDER})
    
    if not EMAIL_PASSWORD:
        debug_info["checks"].append({"check": "EMAIL_PASSWORD", "status": "❌ NOT SET"})
    else:
        debug_info["checks"].append({"check": "EMAIL_PASSWORD", "status": "✅ SET", "value": "********"})
    
    # Check 2: Test SMTP connection
    try:
        print("Testing SMTP connection to smtp.gmail.com:587...")
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        debug_info["checks"].append({"check": "SMTP Connection", "status": "✅ Connected"})
        
        server.ehlo()
        debug_info["checks"].append({"check": "EHLO", "status": "✅ Successful"})
        
        server.starttls()
        debug_info["checks"].append({"check": "TLS", "status": "✅ Started"})
        
        server.ehlo()
        debug_info["checks"].append({"check": "EHLO after TLS", "status": "✅ Successful"})
        
        # Try login if credentials are available
        if EMAIL_SENDER and EMAIL_PASSWORD:
            try:
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                debug_info["checks"].append({"check": "SMTP Login", "status": "✅ Successful"})
            except smtplib.SMTPAuthenticationError as e:
                debug_info["checks"].append({
                    "check": "SMTP Login", 
                    "status": "❌ Failed", 
                    "error": str(e),
                    "solution": "Enable 'Less Secure Apps' or use App Password"
                })
            except Exception as e:
                debug_info["checks"].append({
                    "check": "SMTP Login", 
                    "status": "❌ Failed", 
                    "error": str(e)
                })
        
        server.quit()
        
    except Exception as e:
        debug_info["checks"].append({
            "check": "SMTP Connection", 
            "status": "❌ Failed", 
            "error": str(e)
        })
    
    return jsonify(debug_info)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    
    print("\n" + "="*50)
    print("🚀 ASSISCAN SYSTEM STARTING")
    print("="*50)
    print(f"📧 Email Sender: {'✅ ' + EMAIL_SENDER if EMAIL_SENDER else '❌ NOT SET'}")
    print(f"🔑 Email Password: {'✅ SET' if EMAIL_PASSWORD else '❌ NOT SET'}")
    print(f"🤖 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"🗄️ Database URL: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print("="*50)
    print(f"🌐 Server running on: http://0.0.0.0:{port}")
    print(f"🔗 Test email: http://localhost:{port}/test-email")
    print(f"🔧 Debug email: http://localhost:{port}/debug-email")
    print("="*50 + "\n")
    
    app.run(host='0.0.0.0', port=port)
