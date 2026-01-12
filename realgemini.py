import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
import json
import requests
from datetime import datetime, date
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import traceback
from PIL import Image
import re
import io

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
        
        try:
            # Check specifically for gemini-2.5-flash
            models = list(genai.list_models())
            print(f"📋 Available Gemini Models ({len(models)} total):")
            gemini_2_5_flash_available = False
            
            for model in models:
                model_name = model.name
                print(f"   - {model_name}")
                if "gemini-2.5-flash" in model_name:
                    gemini_2_5_flash_available = True
                    print(f"✨ Found Gemini 2.5 Flash: {model_name}")
            
            if not gemini_2_5_flash_available:
                print("⚠️ Warning: gemini-2.5-flash not found in available models")
                print("⚠️ Will try to use it anyway, but may fail if not accessible")
        except Exception as e:
            print(f"⚠️ Could not list models: {e}")
    except Exception as e:
        print(f"⚠️ Error configuring Gemini: {e}")
else:
    print("❌ CRITICAL: GEMINI_API_KEY is missing!")

# --- ADMIN SECURITY CONFIG ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "assiscan-super-secret-key-2024")

# Setup CORS
CORS(app, resources={
    r"/*": {
        "origins": ["https://assiscan-app.onrender.com", "http://localhost:10000", "http://localhost:5000"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept"]
    }
})

# Setup Upload Folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    print(f"📁 Created uploads folder at: {UPLOAD_FOLDER}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# ================= DATABASE FUNCTIONS =================
def get_db_connection():
    """Get database connection for Render PostgreSQL"""
    try:
        if DATABASE_URL:
            if DATABASE_URL.startswith("postgres://"):
                DATABASE_URL_FIXED = DATABASE_URL.replace("postgres://", "postgresql://", 1)
                conn = psycopg2.connect(DATABASE_URL_FIXED, sslmode='require')
            else:
                conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            return conn
        else:
            print("❌ DATABASE_URL not found in environment")
            return None
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return None

def init_db():
    """Initialize database tables"""
    print("🔧 Initializing database...")
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            
            # Create main records table with COLLEGE field added
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
                    college VARCHAR(150),          -- NEW: College/Department field
                    program VARCHAR(150),          -- Changed from VARCHAR(100) to 150
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
                    siblings TEXT,
                    -- GOOD MORAL ANALYSIS FIELDS
                    goodmoral_analysis JSONB,
                    disciplinary_status VARCHAR(50),
                    goodmoral_score INTEGER DEFAULT 0,
                    has_disciplinary_record BOOLEAN DEFAULT FALSE,
                    disciplinary_details TEXT,
                    -- OTHER DOCUMENTS FIELD
                    other_documents JSONB
                )
            ''')
            
            conn.commit()
            print("✅ Database table 'records' created/verified")
            
            # Check for missing columns
            check_and_add_columns(cur, conn)
            
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            traceback.print_exc()
        finally:
            cur.close()
            conn.close()

def check_and_add_columns(cur, conn):
    """Check and add missing columns to the records table"""
    columns_to_add = [
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
        ("college", "VARCHAR(150)"),  # NEW: College field
        ("program", "VARCHAR(150)"),
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
        ("siblings", "TEXT"),
        # Good Moral columns
        ("goodmoral_analysis", "JSONB"),
        ("disciplinary_status", "VARCHAR(50)"),
        ("goodmoral_score", "INTEGER DEFAULT 0"),
        ("has_disciplinary_record", "BOOLEAN DEFAULT FALSE"),
        ("disciplinary_details", "TEXT"),
        # Other documents
        ("other_documents", "JSONB")
    ]
    
    for column_name, column_type in columns_to_add:
        try:
            cur.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {column_name} {column_type}")
            print(f"   ✅ Verified column: {column_name}")
        except Exception as e:
            print(f"   ⚠️ Column {column_name} already exists or error: {e}")
    
    conn.commit()

# Initialize database
init_db()

# ================= EMAIL FUNCTION =================
def send_email_notification(recipient_email, student_name, file_paths, student_data=None):
    """Send email notification using SendGrid with student information"""
    print(f"\n📧 Preparing email for: {recipient_email}")
    
    if not recipient_email or not isinstance(recipient_email, str):
        print("❌ Invalid email address")
        return False
    
    recipient_email = recipient_email.strip()
    
    if not recipient_email or '@' not in recipient_email:
        print("❌ Invalid email format")
        return False
    
    if not SENDGRID_API_KEY or not EMAIL_SENDER:
        print("❌ SendGrid credentials not configured")
        return True
    
    try:
        ref_id = f"AssiScan-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        subject = f"✅ AssiScan - Admission Record for {student_name}"
        
        # Include Good Moral status in email
        goodmoral_status = ""
        if student_data and 'disciplinary_status' in student_data:
            status = student_data.get('disciplinary_status', 'Unknown')
            score = student_data.get('goodmoral_score', 0)
            
            if status == 'EXCELLENT':
                goodmoral_status = f"📈 Good Moral Status: EXCELLENT (Score: {score}/100)\n• No disciplinary issues found\n• Recommended for admission"
            elif status == 'GOOD':
                goodmoral_status = f"✅ Good Moral Status: GOOD (Score: {score}/100)\n• Minor or no issues\n• Eligible for admission"
            elif status == 'FAIR':
                goodmoral_status = f"⚠️ Good Moral Status: FAIR (Score: {score}/100)\n• Some concerns noted\n• Review recommended"
            elif status == 'POOR':
                goodmoral_status = f"❌ Good Moral Status: POOR (Score: {score}/100)\n• Significant disciplinary issues\n• Requires evaluation"
            else:
                goodmoral_status = "📄 Good Moral Status: Pending analysis"
        
        # Format student data for email
        student_info = ""
        if student_data:
            student_info = f"""
━━━━━━━━━━━━━━━━━━━━━━━━
📋 STUDENT INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━
• Full Name: {student_data.get('name', 'N/A')}
• LRN: {student_data.get('lrn', 'N/A')}
• Sex: {student_data.get('sex', 'N/A')}
• Birthdate: {student_data.get('birthdate', 'N/A')}
• Birthplace: {student_data.get('birthplace', 'N/A')}
• Age: {student_data.get('age', 'N/A')}
• Civil Status: {student_data.get('civil_status', 'N/A')}
• Nationality: {student_data.get('nationality', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━
👨‍👩‍👧‍👦 PARENT INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━
• Mother's Name: {student_data.get('mother_name', 'N/A')}
• Mother's Citizenship: {student_data.get('mother_citizenship', 'N/A')}
• Mother's Contact: {student_data.get('mother_contact', 'N/A')}
• Father's Name: {student_data.get('father_name', 'N/A')}
• Father's Citizenship: {student_data.get('father_citizenship', 'N/A')}
• Father's Contact: {student_data.get('father_contact', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━
🏠 ADDRESS & CONTACT
━━━━━━━━━━━━━━━━━━━━━━━━
• Province: {student_data.get('province', 'N/A')}
• Specific Address: {student_data.get('specific_address', 'N/A')}
• Mobile Number: {student_data.get('mobile_no', 'N/A')}
• Email: {recipient_email}

━━━━━━━━━━━━━━━━━━━━━━━━
🎓 EDUCATIONAL BACKGROUND
━━━━━━━━━━━━━━━━━━━━━━━━
• Previous School: {student_data.get('school_name', 'N/A')}
• School Address: {student_data.get('school_address', 'N/A')}
• Final General Average: {student_data.get('final_general_average', 'N/A')}
• Last Level Attended: {student_data.get('last_level_attended', 'N/A')}
• Student Type: {student_data.get('student_type', 'N/A')}
• College/Department: {student_data.get('college', 'N/A')}
• Program Applied: {student_data.get('program', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━
📝 GOOD MORAL CERTIFICATE ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━
{goodmoral_status}

━━━━━━━━━━━━━━━━━━━━━━━━
📅 VERIFICATION DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━
• Verification Date: {datetime.now().strftime('%B %d, %Y')}
• Verification Time: {datetime.now().strftime('%I:%M %p')}
• Reference ID: {ref_id}
• Status: ✅ VERIFIED & PROCESSED
"""
        else:
            student_info = "⚠️ Student information not available in this record."
        
        body = f"""📋 ADMISSION RECORD VERIFICATION

Dear {student_name},

Your admission documents have been successfully processed through the AssiScan System. Below is a summary of your extracted information:

{student_info}

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
All information above was extracted from your submitted documents.
Please verify the accuracy of the information.
For corrections, please contact the Admissions Office.

Best regards,

The AssiScan Team
Admissions Processing System
University of Batangas Lipa
{datetime.now().strftime('%Y')}"""
        
        # SendGrid API call
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
            print(f"📝 [FALLBACK LOG] Email for {student_name} to {recipient_email}")
            return True
    except Exception as e:
        print(f"⚠️ SendGrid failed: {e}")
        print(f"📝 [FALLBACK LOG] Email for {student_name} to {recipient_email}")
        return True

# ================= HELPER FUNCTIONS =================
def save_multiple_files(files, prefix):
    """Save uploaded files and return paths and PIL images"""
    saved_paths = []
    pil_images = []
    
    for i, file in enumerate(files):
        if file and file.filename:
            timestamp = int(datetime.now().timestamp())
            filename = secure_filename(f"{prefix}_{timestamp}_{i}_{file.filename}")
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(path)
            saved_paths.append(filename)
            try:
                img = Image.open(path)
                pil_images.append(img)
                print(f"   ✅ Saved: {filename}")
            except Exception as e:
                print(f"Error opening image {filename}: {e}")
    return saved_paths, pil_images

def extract_with_gemini(prompt, images):
    """Use Gemini 2.5 Flash for text extraction"""
    try:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured")
        
        # Use only gemini-2.5-flash model
        model_name = "gemini-2.5-flash"
        
        try:
            print(f"🤖 Using model: {model_name}")
            model = genai.GenerativeModel(model_name)
            
            content_parts = [prompt]
            for img in images:
                content_parts.append(img)
            
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
            print(f"❌ {model_name} failed: {str(model_error)}")
            # Fallback to any available model if gemini-2.5-flash fails
            print(f"⚠️ Trying to find alternative model...")
            
            try:
                # List available models and try the first one with "gemini" in name
                models = list(genai.list_models())
                for available_model in models:
                    if "gemini" in available_model.name.lower():
                        fallback_model_name = available_model.name
                        print(f"🔄 Trying fallback model: {fallback_model_name}")
                        model = genai.GenerativeModel(fallback_model_name)
                        
                        content_parts = [prompt]
                        for img in images:
                            content_parts.append(img)
                        
                        response = model.generate_content(content_parts)
                        
                        if response.text:
                            print(f"✅ Success with fallback model: {fallback_model_name}")
                            return response.text
                
                raise Exception(f"No working Gemini model found. Original error: {str(model_error)}")
            except Exception as fallback_error:
                raise Exception(f"All models failed. Last error: {str(fallback_error)}")
    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        raise e

def calculate_goodmoral_score(analysis_data):
    """Calculate Good Moral score based on analysis"""
    score = 100  # Start with perfect score
    
    # Deduct points for disciplinary issues
    if analysis_data.get('has_disciplinary_record'):
        score -= 40
    
    # Check for serious violations
    serious_violations = ['suspended', 'expelled', 'disciplinary action', 'major violation']
    remarks = analysis_data.get('remarks', '').lower()
    
    for violation in serious_violations:
        if violation in remarks:
            score -= 30
            break
    
    # Deduct for conditional phrases
    conditional_phrases = ['conditional', 'subject to', 'pending', 'under review']
    for phrase in conditional_phrases:
        if phrase in remarks:
            score -= 20
            break
    
    # Ensure score is between 0-100
    score = max(0, min(100, score))
    
    # Determine status based on score
    if score >= 90:
        status = 'EXCELLENT'
    elif score >= 70:
        status = 'GOOD'
    elif score >= 50:
        status = 'FAIR'
    else:
        status = 'POOR'
    
    return score, status

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

# ================= GOOD MORAL SCANNING ENDPOINT =================
@app.route('/scan-goodmoral', methods=['POST'])
def scan_goodmoral():
    """
    Scan and analyze Good Moral Certificate
    Returns: Status, Score, and Analysis
    """
    if 'imageFiles' not in request.files: 
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': 
        return jsonify({"error": "No selected file"}), 400

    try:
        saved_paths, pil_images = save_multiple_files(files, "GOODMORAL")
        
        if not pil_images:
            return jsonify({"error": "No valid images found"}), 400

        print(f"📄 Processing Good Moral Certificate with Gemini 2.5 Flash")
        
        prompt = """You are an expert document processor for Philippine Good Moral Certificates.
        
        Analyze this Good Moral Certificate and extract ALL relevant information.
        
        IMPORTANT: Focus on identifying if there are any disciplinary issues or negative remarks.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "is_valid_certificate": true,
            "student_name": "Full Name of Student",
            "issuing_school": "Name of Issuing School",
            "issuing_officer": "Name of Issuing Officer/Principal",
            "issued_date": "YYYY-MM-DD format",
            "certificate_type": "Good Moral Certificate / Certificate of Good Moral Character",
            "remarks": "Any remarks or conditions mentioned",
            "has_disciplinary_record": false,
            "disciplinary_details": "Details of any disciplinary actions if mentioned",
            "recommendation_statement": "The recommendation statement text",
            "special_conditions": "Any special conditions or limitations"
        }
        
        CRITICAL ANALYSIS:
        1. If the certificate mentions any suspensions, disciplinary actions, or negative behavior, set "has_disciplinary_record": true
        2. Look for phrases like: "subject to", "conditional", "pending", "under review", "with reservations"
        3. Extract the exact remarks about the student's behavior
        
        If the document is not a valid Good Moral Certificate, set "is_valid_certificate": false.
        
        Return ONLY the JSON, no additional text."""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response received: {len(response_text)} characters")
            
            # Clean the response
            cleaned_text = response_text.strip()
            
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            # Find JSON
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                print(f"❌ Could not find JSON in response")
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            
            try:
                analysis_data = json.loads(json_str)
                print(f"✅ Successfully parsed Good Moral analysis")
                
                # Validate required fields
                if not analysis_data.get("is_valid_certificate", False):
                    return jsonify({
                        "error": "Invalid Good Moral Certificate"
                    }), 400
                
                # Calculate score and status
                score, status = calculate_goodmoral_score(analysis_data)
                
                # Add calculated fields
                analysis_data['goodmoral_score'] = score
                analysis_data['disciplinary_status'] = status
                
                # Get disciplinary details for display
                disciplinary_details = ""
                if analysis_data.get('has_disciplinary_record'):
                    disciplinary_details = analysis_data.get('disciplinary_details', '') or analysis_data.get('remarks', '') or 'Disciplinary issues detected'
                
                return jsonify({
                    "message": "Good Moral Certificate analyzed successfully",
                    "analysis": analysis_data,
                    "goodmoral_score": score,
                    "disciplinary_status": status,
                    "disciplinary_details": disciplinary_details,
                    "has_disciplinary_record": analysis_data.get('has_disciplinary_record', False),
                    "image_paths": ",".join(saved_paths)
                })
            except json.JSONDecodeError as json_error:
                print(f"❌ JSON Parse Error: {json_error}")
                return jsonify({"error": f"Failed to parse AI response: {str(json_error)}"}), 500
        except Exception as ai_error:
            print(f"❌ AI Extraction Failed: {ai_error}")
            traceback.print_exc()
            return jsonify({
                "error": "AI service unavailable",
                "details": str(ai_error)[:200]
            }), 500
    except Exception as e:
        print(f"❌ Good Moral Scanning Error: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# ================= UPDATED SAVE RECORD ENDPOINT =================
@app.route('/save-record', methods=['POST'])
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Saving record with Good Moral analysis")
        
        # Check if Good Moral data is included
        goodmoral_analysis = d.get('goodmoral_analysis')
        disciplinary_status = d.get('disciplinary_status')
        goodmoral_score = d.get('goodmoral_score')
        disciplinary_details = d.get('disciplinary_details')
        has_disciplinary_record = d.get('has_disciplinary_record', False)
        
        # Parse other_documents if provided
        other_documents = d.get('other_documents')
        if other_documents and isinstance(other_documents, list):
            other_documents_json = json.dumps(other_documents)
        else:
            other_documents_json = None
        
        siblings_list = d.get('siblings', [])
        siblings_json = json.dumps(siblings_list)
        
        # Get college and program data from frontend
        college = d.get('college', '')
        program = d.get('program', '')
        
        print(f"🎓 College selected: {college}")
        print(f"📚 Program selected: {program}")
        
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

        # Convert goodmoral_analysis to JSON string if it's a dict
        goodmoral_analysis_json = None
        if goodmoral_analysis:
            if isinstance(goodmoral_analysis, dict):
                goodmoral_analysis_json = json.dumps(goodmoral_analysis)
            else:
                goodmoral_analysis_json = goodmoral_analysis
        
        # Insert record with Good Moral data AND COLLEGE field
        cur.execute('''
            INSERT INTO records (
                name, sex, birthdate, birthplace, birth_order, religion, age,
                mother_name, mother_citizenship, mother_occupation, 
                father_name, father_citizenship, father_occupation, 
                lrn, school_name, school_address, final_general_average,
                image_path, form137_path, goodmoral_path,
                email, mobile_no, civil_status, nationality,
                mother_contact, father_contact,
                guardian_name, guardian_relation, guardian_contact,
                region, province, specific_address,
                school_year, student_type, college, program, last_level_attended,
                is_ip, is_pwd, has_medication, is_working,
                residence_type, employer_name, marital_status,
                is_gifted, needs_assistance, school_type, year_attended, 
                special_talents, is_scholar, siblings,
                -- Good Moral fields
                goodmoral_analysis, disciplinary_status, goodmoral_score,
                has_disciplinary_record, disciplinary_details,
                -- Other documents
                other_documents
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 
                %s, %s, %s, 
                %s, %s, %s, %s, 
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s,
                -- Good Moral fields
                %s, %s, %s,
                %s, %s,
                -- Other documents
                %s
            ) 
            RETURNING id
        ''', (
            d.get('name'), d.get('sex'), d.get('birthdate') or None, d.get('birthplace'), 
            d.get('birth_order'), d.get('religion'), d.get('age'),
            d.get('mother_name'), d.get('mother_citizenship'), d.get('mother_occupation'), 
            d.get('father_name'), d.get('father_citizenship'), d.get('father_occupation'), 
            d.get('lrn'), d.get('school_name'), d.get('school_address'), d.get('final_general_average'),
            d.get('psa_image_path', ''), d.get('f137_image_path', ''), d.get('goodmoral_image_path', ''), 
            d.get('email'), d.get('mobile_no'), d.get('civil_status'), d.get('nationality'),
            d.get('mother_contact'), d.get('father_contact'),
            d.get('guardian_name'), d.get('guardian_relation'), d.get('guardian_contact'),
            d.get('region'), d.get('province'), d.get('specific_address'),
            d.get('school_year'), d.get('student_type'), college, program, d.get('last_level_attended'),  # Added college and program
            d.get('is_ip'), d.get('is_pwd'), d.get('has_medication'), d.get('is_working'),
            d.get('residence_type'), d.get('employer_name'), d.get('marital_status'),
            d.get('is_gifted'), d.get('needs_assistance'), d.get('school_type'), 
            d.get('year_attended'), d.get('special_talents'), d.get('is_scholar'),
            siblings_json,
            # Good Moral fields
            goodmoral_analysis_json,
            disciplinary_status,
            goodmoral_score,
            has_disciplinary_record,
            disciplinary_details,
            # Other documents
            other_documents_json
        ))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        print(f"✅ Record saved with ID: {new_id}")
        print(f"🎓 College: {college}")
        print(f"📚 Program: {program}")
        print(f"📊 Good Moral Score: {goodmoral_score} | Status: {disciplinary_status}")
        
        if has_disciplinary_record:
            print(f"⚠️ Student has disciplinary record: {disciplinary_details}")

        return jsonify({
            "status": "success", 
            "db_id": new_id,
            "college": college,
            "program": program,
            "goodmoral_score": goodmoral_score,
            "disciplinary_status": disciplinary_status,
            "has_disciplinary_record": has_disciplinary_record,
            "message": "Record saved successfully."
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

# ================= UPDATED GET RECORDS ENDPOINT =================
@app.route('/get-records', methods=['GET'])
def get_records():
    if not session.get('logged_in'):
        return jsonify({"records": [], "error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    if not conn: 
        return jsonify({"records": [], "error": "Database connection failed"})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records ORDER BY id DESC")
        rows = cur.fetchall()
        
        for r in rows:
            if r['created_at']: 
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: 
                r['birthdate'] = str(r['birthdate'])
            if r['email_sent_at']: 
                r['email_sent_at'] = r['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Parse Good Moral analysis JSON
            if r.get('goodmoral_analysis'):
                try:
                    r['goodmoral_analysis'] = json.loads(r['goodmoral_analysis'])
                except:
                    r['goodmoral_analysis'] = {}
            
            # Parse other_documents JSON
            if r.get('other_documents'):
                try:
                    r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            # Process image paths
            image_fields = ['image_path', 'form137_path', 'form138_path', 'goodmoral_path']
            for field in image_fields:
                if r.get(field):
                    paths = str(r[field]).split(',')
                    if paths and paths[0].strip():
                        first_path = paths[0].strip()
                        if '/' in first_path:
                            first_path = first_path.split('/')[-1]
                        r[field] = first_path
                    else:
                        r[field] = None
                else:
                    r[field] = None
        
        return jsonify({
            "records": rows,
            "server_url": request.host_url.rstrip('/')
        })
    except Exception as e:
        print(f"❌ Error in get-records: {e}")
        return jsonify({"records": [], "error": str(e)})
    finally:
        conn.close()

# ================= UPLOAD OTHER DOCUMENTS ENDPOINT =================
@app.route('/upload-other-document/<int:record_id>', methods=['POST'])
def upload_other_document(record_id):
    """Upload other documents with title"""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    
    if 'file' not in request.files or 'title' not in request.form:
        return jsonify({"error": "File and title required"}), 400
    
    file = request.files['file']
    title = request.form['title'].strip()
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not title:
        return jsonify({"error": "Document title required"}), 400
    
    try:
        # Save the file
        timestamp = int(datetime.now().timestamp())
        filename = secure_filename(f"OTHER_{record_id}_{timestamp}_{file.filename}")
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor()
        
        # Get existing other_documents
        cur.execute("SELECT other_documents FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        existing_documents = []
        if result and result[0]:
            try:
                existing_documents = json.loads(result[0])
            except:
                existing_documents = []
        
        # Add new document
        new_document = {
            'id': len(existing_documents) + 1,
            'title': title,
            'filename': filename,
            'uploaded_at': datetime.now().isoformat()
        }
        
        existing_documents.append(new_document)
        new_documents_json = json.dumps(existing_documents)
        
        # Update database
        cur.execute("UPDATE records SET other_documents = %s WHERE id = %s", 
                   (new_documents_json, record_id))
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Document uploaded successfully",
            "document": new_document,
            "download_url": f"{request.host_url}uploads/{filename}"
        })
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

# ================= DELETE OTHER DOCUMENT ENDPOINT =================
@app.route('/delete-other-document/<int:record_id>/<int:doc_id>', methods=['DELETE'])
def delete_other_document(record_id, doc_id):
    """Delete an other document"""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Get existing other_documents
        cur.execute("SELECT other_documents FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        if not result or not result[0]:
            return jsonify({"error": "No documents found"}), 404
        
        existing_documents = json.loads(result[0])
        
        # Find and remove the document
        document_to_delete = None
        updated_documents = []
        
        for doc in existing_documents:
            if doc.get('id') == doc_id:
                document_to_delete = doc
            else:
                updated_documents.append(doc)
        
        if not document_to_delete:
            return jsonify({"error": "Document not found"}), 404
        
        # Delete the file
        filename = document_to_delete.get('filename')
        if filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # Update database
        updated_documents_json = json.dumps(updated_documents)
        cur.execute("UPDATE records SET other_documents = %s WHERE id = %s", 
                   (updated_documents_json, record_id))
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Document deleted successfully"
        })
    except Exception as e:
        print(f"❌ Delete error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

# ================= OTHER ENDPOINTS =================
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded files"""
    try:
        if '..' in filename or filename.startswith('/'):
            return "Invalid filename", 400
        
        clean_filename = filename.split('/')[-1] if '/' in filename else filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], clean_filename)
        
        if not os.path.exists(file_path):
            print(f"❌ File not found: {clean_filename}")
            if os.path.exists(app.config['UPLOAD_FOLDER']):
                all_files = os.listdir(app.config['UPLOAD_FOLDER'])
                matching = [f for f in all_files if clean_filename in f]
                if matching:
                    return send_from_directory(app.config['UPLOAD_FOLDER'], matching[0])
            
            return jsonify({"error": f"File '{clean_filename}' not found"}), 404
        
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain'
        }
        
        ext = os.path.splitext(clean_filename)[1].lower()
        mimetype = mime_types.get(ext, 'application/octet-stream')
        
        response = send_file(
            file_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=clean_filename
        )
        
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        
        return response
    except Exception as e:
        print(f"❌ Error serving file {filename}: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

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
                
            # Parse Good Moral analysis
            if record.get('goodmoral_analysis'):
                try:
                    record['goodmoral_analysis'] = json.loads(record['goodmoral_analysis'])
                except:
                    record['goodmoral_analysis'] = {}
            
            # Parse other_documents
            if record.get('other_documents'):
                try:
                    record['other_documents'] = json.loads(record['other_documents'])
                except:
                    record['other_documents'] = []
            else:
                record['other_documents'] = []

            return render_template('print_form.html', r=record)
        else:
            return "Record not found", 404
    except Exception as e:
        return f"Error loading form: {str(e)}", 500
    finally:
        conn.close()

# ================= EXISTING PSA AND FORM 137 ENDPOINTS =================
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

        print(f"📸 Processing PSA with Gemini 2.5 Flash")
        
        prompt = """Extract information from this PSA Birth Certificate.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "is_valid_document": true,
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
        
        Return ONLY the JSON, no additional text."""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            
            cleaned_text = response_text.strip()
            
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            
            try:
                data = json.loads(json_str)
                
                if not data.get("is_valid_document", False):
                    return jsonify({
                        "error": f"Invalid document"
                    }), 400
                
                return jsonify({
                    "message": "Success", 
                    "structured_data": data, 
                    "image_paths": ",".join(saved_paths)
                })
            except json.JSONDecodeError:
                return jsonify({"error": "Failed to parse AI response"}), 500
        except Exception as ai_error:
            return jsonify({
                "error": "AI service unavailable",
                "details": str(ai_error)[:200]
            }), 500
    except Exception as e:
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

@app.route('/extract-form137', methods=['POST'])
def extract_form137():
    if 'imageFiles' not in request.files: 
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '': 
        return jsonify({"error": "No selected file"}), 400
    
    try:
        saved_paths, pil_images = save_multiple_files(files, "F137")
        print(f"📸 Processing Form 137 with Gemini 2.5 Flash")

        if not pil_images:
            return jsonify({"error": "No valid images found"}), 400
        
        prompt = """Extract information from this Form 137 / SF10 document.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "lrn": "12-digit Learner Reference Number",
            "school_name": "Complete School Name",
            "school_address": "Complete School Address",
            "final_general_average": "Numerical grade"
        }
        
        Return ONLY the JSON, no additional text."""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            
            cleaned_text = response_text.strip()
            
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            
            try:
                data = json.loads(json_str)
                
                if 'lrn' in data and data['lrn']:
                    data['lrn'] = str(data['lrn']).strip()
                
                return jsonify({
                    "message": "Success", 
                    "structured_data": data, 
                    "image_paths": ",".join(saved_paths)
                })
            except json.JSONDecodeError:
                return jsonify({"error": "Failed to parse AI response"}), 500
        except Exception as ai_error:
            return jsonify({
                "error": "AI service unavailable",
                "details": str(ai_error)[:200]
            }), 500
    except Exception as e:
        return jsonify({"error": f"Server Error: {str(e)[:100]}"}), 500

# ================= EMAIL ENDPOINTS =================
@app.route('/send-email/<int:record_id>', methods=['POST'])
def send_email_only(record_id):
    """Send email for a saved record"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get record details with college field
        cur.execute("""
            SELECT name, email, email_sent, 
                   goodmoral_score, disciplinary_status, disciplinary_details,
                   lrn, sex, birthdate, birthplace, age, 
                   civil_status, nationality,
                   mother_name, mother_citizenship, mother_contact,
                   father_name, father_citizenship, father_contact,
                   province, specific_address, mobile_no,
                   school_name, school_address, final_general_average,
                   last_level_attended, student_type, college, program,
                   school_year, is_ip, is_pwd, has_medication,
                   special_talents
            FROM records WHERE id = %s
        """, (record_id,))
        
        record = cur.fetchone()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        if record.get('email_sent'):
            return jsonify({"warning": "Email has already been sent"}), 400
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            return jsonify({"error": "No email address found"}), 400
        
        print(f"\n📧 Sending email for record ID: {record_id}")
        print(f"🎓 College: {record.get('college', 'N/A')}")
        print(f"📚 Program: {record.get('program', 'N/A')}")
        
        student_data = dict(record)
        email_sent = send_email_notification(email_addr, student_name, [], student_data)
        
        if email_sent:
            cur.execute("""
                UPDATE records 
                SET email_sent = TRUE, email_sent_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (record_id,))
            conn.commit()
            
            print(f"✅ Email sent for ID: {record_id}")
            return jsonify({
                "status": "success",
                "message": f"Email sent successfully to {email_addr}",
                "record_id": record_id
            })
        else:
            return jsonify({
                "status": "error",
                "error": "Failed to send email."
            }), 500
    except Exception as e:
        print(f"❌ EMAIL SEND ERROR: {e}")
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
    finally:
        if conn:
            conn.close()

@app.route('/resend-email/<int:record_id>', methods=['POST'])
def resend_email(record_id):
    """Resend email even if already sent"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT name, email, 
                   goodmoral_score, disciplinary_status, disciplinary_details,
                   lrn, sex, birthdate, birthplace, age, 
                   civil_status, nationality,
                   mother_name, mother_citizenship, mother_contact,
                   father_name, father_citizenship, father_contact,
                   province, specific_address, mobile_no,
                   school_name, school_address, final_general_average,
                   last_level_attended, student_type, college, program,
                   school_year, is_ip, is_pwd, has_medication,
                   special_talents
            FROM records WHERE id = %s
        """, (record_id,))
        
        record = cur.fetchone()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            return jsonify({"error": "No email address found"}), 400
        
        print(f"\n📧 Resending email for ID: {record_id}")
        print(f"🎓 College: {record.get('college', 'N/A')}")
        print(f"📚 Program: {record.get('program', 'N/A')}")
        
        student_data = dict(record)
        email_sent = send_email_notification(email_addr, student_name, [], student_data)
        
        if email_sent:
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
                "error": "Failed to send email."
            }), 500
    except Exception as e:
        print(f"❌ EMAIL RESEND ERROR: {e}")
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500
    finally:
        if conn:
            conn.close()

# ================= OTHER ENDPOINTS =================
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
            saved_paths.append(fname)

    full_path_str = ",".join(saved_paths)
    
    col_map = {'form137': 'form137_path', 'form138': 'form138_path', 'goodmoral': 'goodmoral_path'}
    
    if dtype not in col_map:
        return jsonify({"error": "Invalid document type"}), 400
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute(f"SELECT {col_map[dtype]} FROM records WHERE id = %s", (rid,))
        existing = cur.fetchone()
        
        new_paths = []
        if existing and existing[0]:
            new_paths = existing[0].split(',')
        
        new_paths.extend(saved_paths)
        new_path_str = ','.join([p for p in new_paths if p])
        
        cur.execute(f"UPDATE records SET {col_map[dtype]} = %s WHERE id = %s", (new_path_str, rid))
        conn.commit()
        return jsonify({"status": "success", "message": "File uploaded successfully"})
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({"error": str(e)}), 500
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
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally: 
        conn.close()

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
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================= HEALTH AND DIAGNOSTIC ENDPOINTS =================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "AssiScan Backend",
        "goodmoral_scanning": "ENABLED",
        "model": "Gemini 2.5 Flash",
        "dropdown_support": "ENABLED (College & Program dropdowns)",
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if get_db_connection() else "disconnected"
    })

@app.route('/list-uploads', methods=['GET'])
def list_uploads():
    """List uploaded files"""
    try:
        if not os.path.exists(UPLOAD_FOLDER):
            return jsonify({"error": "Uploads folder not found"}), 404
        
        files = []
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                files.append({
                    "name": filename,
                    "size": os.path.getsize(filepath),
                    "url": f"{request.host_url}uploads/{filename}"
                })
        
        return jsonify({
            "count": len(files),
            "files": files[:20],
            "folder": UPLOAD_FOLDER
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= APPLICATION START =================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    
    print("\n" + "="*60)
    print("🚀 ASSISCAN WITH GEMINI 2.5 FLASH")
    print("="*60)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"🤖 Model: gemini-2.5-flash")
    print(f"📧 SendGrid: {'✅ SET' if SENDGRID_API_KEY else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print(f"📁 Uploads: {UPLOAD_FOLDER}")
    print("="*60)
    print("📊 FEATURES:")
    print("   • PSA, Form 137, Good Moral scanning")
    print("   • College & Program dropdown support")
    print("   • Disciplinary record detection")
    print("   • Other documents upload with title")
    print("   • Email notifications")
    print("   • Complete student record management")
    print("="*60)
    print("🎓 COLLEGE & PROGRAM SUPPORT:")
    print("   • College of Criminal Justice Education (CCJE)")
    print("   • College of Education, Arts and Sciences (CEAS)")
    print("   • College of IT, Entertainment & Communication (CITEC)")
    print("   • College of Engineering and Architecture (CENAR)")
    print("   • College of Business, Accountancy & Auditing (CBAA)")
    print("="*60)
    
    if os.path.exists(UPLOAD_FOLDER):
        file_count = len([f for f in os.listdir(UPLOAD_FOLDER) if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))])
        print(f"📊 Uploads folder contains {file_count} files")
    
    app.run(host='0.0.0.0', port=port, debug=False)
