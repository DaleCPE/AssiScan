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
import hashlib
import secrets
from datetime import datetime, timedelta

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

# Password hashing functions
def hash_password(password):
    """Hash a password using SHA-256 with salt"""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${password_hash}"

def verify_password(password, hashed_password):
    """Verify a password against its hash"""
    if not hashed_password or '$' not in hashed_password:
        return False
    
    salt, stored_hash = hashed_password.split('$')
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return password_hash == stored_hash

def generate_reset_token():
    """Generate a password reset token"""
    return secrets.token_urlsafe(32)

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
            
            # Create main records table with COLLEGE field
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
                    college VARCHAR(150),
                    program VARCHAR(150),
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
                    goodmoral_analysis JSONB,
                    disciplinary_status VARCHAR(50),
                    goodmoral_score INTEGER DEFAULT 0,
                    has_disciplinary_record BOOLEAN DEFAULT FALSE,
                    disciplinary_details TEXT,
                    other_documents JSONB,
                    created_by INTEGER
                )
            ''')
            
            # Create colleges table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS colleges (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(150) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    display_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create programs table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS programs (
                    id SERIAL PRIMARY KEY,
                    college_id INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
                    code VARCHAR(50),
                    name VARCHAR(150) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    display_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create users table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(150),
                    email VARCHAR(150),
                    role VARCHAR(50) DEFAULT 'scanner_operator',
                    college_id INTEGER REFERENCES colleges(id) ON DELETE SET NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_login TIMESTAMP,
                    failed_attempts INTEGER DEFAULT 0,
                    locked_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INTEGER REFERENCES users(id),
                    reset_token VARCHAR(100),
                    reset_token_expiry TIMESTAMP
                )
            ''')
            
            # Create user_activities table for logging
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_activities (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    activity_type VARCHAR(50),
                    description TEXT,
                    ip_address VARCHAR(45),
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            print("✅ Database tables created/verified")
            
            # Insert default colleges if empty
            cur.execute("SELECT COUNT(*) FROM colleges")
            if cur.fetchone()[0] == 0:
                print("📝 Inserting default colleges...")
                default_colleges = [
                    ("CCJE", "College of Criminal Justice Education", "College of Criminal Justice Education", 1),
                    ("CEAS", "College of Education, Arts and Sciences", "College of Education, Arts and Sciences", 2),
                    ("CITEC", "College of Information Technology, Entertainment and Communication", "College of IT, Entertainment & Communication", 3),
                    ("CENAR", "College of Engineering and Architecture", "College of Engineering and Architecture", 4),
                    ("CBAA", "College of Business, Accountancy and Auditing", "College of Business, Accountancy & Auditing", 5)
                ]
                
                for code, name, desc, order in default_colleges:
                    cur.execute("INSERT INTO colleges (code, name, description, display_order) VALUES (%s, %s, %s, %s) RETURNING id", 
                               (code, name, desc, order))
                    college_id = cur.fetchone()[0]
                    
                    # Insert default programs based on college
                    if code == "CCJE":
                        cur.execute("INSERT INTO programs (college_id, name, display_order) VALUES (%s, %s, %s)",
                                   (college_id, "Bachelor of Science in Criminology", 1))
                    elif code == "CEAS":
                        programs = [
                            "Bachelor of Elementary Education",
                            "Bachelor of Secondary Education", 
                            "Bachelor of Science in Psychology",
                            "Bachelor of Science in Legal Management",
                            "Bachelor of Science in Social Work"
                        ]
                        for i, program in enumerate(programs):
                            cur.execute("INSERT INTO programs (college_id, name, display_order) VALUES (%s, %s, %s)",
                                       (college_id, program, i+1))
                    elif code == "CITEC":
                        programs = [
                            "Bachelor of Science in Information Technology",
                            "Bachelor of Arts in Multimedia Arts"
                        ]
                        for i, program in enumerate(programs):
                            cur.execute("INSERT INTO programs (college_id, name, display_order) VALUES (%s, %s, %s)",
                                       (college_id, program, i+1))
                    elif code == "CENAR":
                        programs = [
                            "Bachelor of Science in Industrial Engineering",
                            "Bachelor of Science in Computer Engineering",
                            "Bachelor of Science in Architecture"
                        ]
                        for i, program in enumerate(programs):
                            cur.execute("INSERT INTO programs (college_id, name, display_order) VALUES (%s, %s, %s)",
                                       (college_id, program, i+1))
                    elif code == "CBAA":
                        programs = [
                            "Bachelor of Science in Business Administration",
                            "Bachelor of Science in Accountancy",
                            "Bachelor of Science in Internal Auditing"
                        ]
                        for i, program in enumerate(programs):
                            cur.execute("INSERT INTO programs (college_id, name, display_order) VALUES (%s, %s, %s)",
                                       (college_id, program, i+1))
                
                conn.commit()
                print("✅ Default colleges and programs inserted")
            
            # Create default super admin user if no users exist
            cur.execute("SELECT COUNT(*) FROM users")
            if cur.fetchone()[0] == 0:
                print("👤 Creating default super admin user...")
                hashed_password = hash_password(ADMIN_PASSWORD)
                cur.execute("""
                    INSERT INTO users (username, password_hash, full_name, email, role, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (ADMIN_USERNAME, hashed_password, "System Administrator", "admin@assiscan.edu.ph", "super_admin", True))
                conn.commit()
                print("✅ Default super admin user created")
            
            # Check for missing columns in records table
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
        ("college", "VARCHAR(150)"),
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
        ("goodmoral_analysis", "JSONB"),
        ("disciplinary_status", "VARCHAR(50)"),
        ("goodmoral_score", "INTEGER DEFAULT 0"),
        ("has_disciplinary_record", "BOOLEAN DEFAULT FALSE"),
        ("disciplinary_details", "TEXT"),
        ("other_documents", "JSONB"),
        ("created_by", "INTEGER")
    ]
    
    for column_name, column_type in columns_to_add:
        try:
            cur.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {column_name} {column_type}")
            print(f"   ✅ Verified column: {column_name}")
        except Exception as e:
            print(f"   ⚠️ Column {column_name} already exists or error: {e}")
    
    conn.commit()

def log_user_activity(user_id, activity_type, description, ip_address=None, user_agent=None):
    """Log user activity"""
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO user_activities (user_id, activity_type, description, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, activity_type, description, ip_address, user_agent))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"❌ Failed to log activity: {e}")

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

# ================= USER AUTHENTICATION MIDDLEWARE =================
def login_required(f):
    """Decorator to require login for routes"""
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def role_required(*required_roles):
    """Decorator to require specific role(s)"""
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if not session.get('logged_in'):
                return redirect('/login')
            
            user_role = session.get('user_role')
            if user_role not in required_roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator

# ================= USER MANAGEMENT ROUTES =================

@app.route('/api/users', methods=['GET'])
@login_required
@role_required('super_admin', 'college_admin')
def get_users():
    """Get all users (with filtering based on role)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get current user's role and college
        current_user_id = session.get('user_id')
        current_user_role = session.get('user_role')
        current_college_id = session.get('college_id')
        
        if current_user_role == 'super_admin':
            # Super admin can see all users
            cur.execute("""
                SELECT u.*, c.name as college_name
                FROM users u
                LEFT JOIN colleges c ON u.college_id = c.id
                ORDER BY u.created_at DESC
            """)
        elif current_user_role == 'college_admin':
            # College admin can only see users from their college
            cur.execute("""
                SELECT u.*, c.name as college_name
                FROM users u
                LEFT JOIN colleges c ON u.college_id = c.id
                WHERE u.college_id = %s OR u.id = %s
                ORDER BY u.created_at DESC
            """, (current_college_id, current_user_id))
        else:
            # Other roles can only see themselves
            cur.execute("""
                SELECT u.*, c.name as college_name
                FROM users u
                LEFT JOIN colleges c ON u.college_id = c.id
                WHERE u.id = %s
            """, (current_user_id,))
        
        users = cur.fetchall()
        
        # Remove password hash from response
        for user in users:
            if 'password_hash' in user:
                del user['password_hash']
            if 'reset_token' in user:
                del user['reset_token']
            if 'reset_token_expiry' in user:
                del user['reset_token_expiry']
        
        return jsonify(users)
    except Exception as e:
        print(f"❌ Error getting users: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/users', methods=['POST'])
@login_required
@role_required('super_admin', 'college_admin')
def create_user():
    """Create a new user account"""
    data = request.json
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"error": "Username and password are required"}), 400
    
    # Validate role permissions
    current_user_role = session.get('user_role')
    requested_role = data.get('role', 'scanner_operator')
    
    # College admins can only create scanner_operator or viewer roles
    if current_user_role == 'college_admin' and requested_role in ['super_admin', 'college_admin']:
        return jsonify({"error": "Insufficient permissions to create this role"}), 403
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if username already exists
        cur.execute("SELECT id FROM users WHERE username = %s", (data['username'],))
        if cur.fetchone():
            return jsonify({"error": "Username already exists"}), 409
        
        # Hash the password
        hashed_password = hash_password(data['password'])
        
        # If college admin is creating user, auto-assign their college
        college_id = data.get('college_id')
        if current_user_role == 'college_admin':
            college_id = session.get('college_id')
        
        # Insert new user
        cur.execute("""
            INSERT INTO users (
                username, password_hash, full_name, email, role, 
                college_id, is_active, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, username, full_name, email, role, 
                     college_id, is_active, created_at
        """, (
            data['username'],
            hashed_password,
            data.get('full_name'),
            data.get('email'),
            requested_role,
            college_id,
            data.get('is_active', True),
            session.get('user_id')
        ))
        
        new_user = cur.fetchone()
        conn.commit()
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'user_created',
            f"Created user: {new_user['username']} ({new_user['role']})",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({
            "status": "success",
            "message": "User created successfully",
            "user": new_user
        })
    except Exception as e:
        print(f"❌ Error creating user: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    """Update a user account"""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Check permissions
    current_user_id = session.get('user_id')
    current_user_role = session.get('user_role')
    
    # Users can only update themselves unless they're admin
    if current_user_role not in ['super_admin', 'college_admin'] and current_user_id != user_id:
        return jsonify({"error": "Insufficient permissions"}), 403
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Build update query dynamically
        updates = []
        values = []
        
        if 'full_name' in data:
            updates.append("full_name = %s")
            values.append(data['full_name'])
        
        if 'email' in data:
            updates.append("email = %s")
            values.append(data['email'])
        
        # Only admins can update these fields
        if current_user_role in ['super_admin', 'college_admin']:
            if 'role' in data:
                # Validate role changes
                if current_user_role == 'college_admin' and data['role'] in ['super_admin', 'college_admin']:
                    return jsonify({"error": "Cannot assign admin roles"}), 403
                updates.append("role = %s")
                values.append(data['role'])
            
            if 'college_id' in data:
                updates.append("college_id = %s")
                values.append(data['college_id'])
            
            if 'is_active' in data:
                updates.append("is_active = %s")
                values.append(data['is_active'])
        
        # Password update
        if 'password' in data and data['password']:
            hashed_password = hash_password(data['password'])
            updates.append("password_hash = %s")
            values.append(hashed_password)
        
        if not updates:
            return jsonify({"error": "No fields to update"}), 400
        
        values.append(user_id)
        update_query = f"UPDATE users SET {', '.join(updates)} WHERE id = %s RETURNING *"
        
        cur.execute(update_query, values)
        updated_user = cur.fetchone()
        conn.commit()
        
        if not updated_user:
            return jsonify({"error": "User not found"}), 404
        
        # Remove sensitive data
        if 'password_hash' in updated_user:
            del updated_user['password_hash']
        if 'reset_token' in updated_user:
            del updated_user['reset_token']
        
        # Log activity
        log_user_activity(
            current_user_id,
            'user_updated',
            f"Updated user: {updated_user['username']}",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({
            "status": "success",
            "message": "User updated successfully",
            "user": updated_user
        })
    except Exception as e:
        print(f"❌ Error updating user: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('super_admin', 'college_admin')
def delete_user(user_id):
    """Delete a user account (soft delete)"""
    # Cannot delete yourself
    if user_id == session.get('user_id'):
        return jsonify({"error": "Cannot delete your own account"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        # Get user info for logging
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Soft delete by setting is_active to false
        cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s RETURNING id", (user_id,))
        
        if cur.rowcount == 0:
            return jsonify({"error": "User not found"}), 404
        
        conn.commit()
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'user_deleted',
            f"Deleted user: {user[0]}",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({
            "status": "success",
            "message": "User deactivated successfully"
        })
    except Exception as e:
        print(f"❌ Error deleting user: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/users/<int:user_id>/restore', methods=['POST'])
@login_required
@role_required('super_admin', 'college_admin')
def restore_user(user_id):
    """Restore a deleted user"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("UPDATE users SET is_active = TRUE WHERE id = %s RETURNING id", (user_id,))
        
        if cur.rowcount == 0:
            return jsonify({"error": "User not found"}), 404
        
        conn.commit()
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'user_restored',
            f"Restored user ID: {user_id}",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({
            "status": "success",
            "message": "User restored successfully"
        })
    except Exception as e:
        print(f"❌ Error restoring user: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/users/me', methods=['GET'])
@login_required
def get_current_user():
    """Get current logged in user info"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.*, c.name as college_name
            FROM users u
            LEFT JOIN colleges c ON u.college_id = c.id
            WHERE u.id = %s
        """, (session.get('user_id'),))
        
        user = cur.fetchone()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Remove sensitive data
        if 'password_hash' in user:
            del user['password_hash']
        if 'reset_token' in user:
            del user['reset_token']
        if 'reset_token_expiry' in user:
            del user['reset_token_expiry']
        
        return jsonify(user)
    except Exception as e:
        print(f"❌ Error getting current user: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/user-activities', methods=['GET'])
@login_required
@role_required('super_admin', 'college_admin')
def get_user_activities():
    """Get user activity logs"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        current_user_role = session.get('user_role')
        current_college_id = session.get('college_id')
        
        if current_user_role == 'super_admin':
            # Super admin can see all activities
            cur.execute("""
                SELECT a.*, u.username, u.full_name
                FROM user_activities a
                JOIN users u ON a.user_id = u.id
                ORDER BY a.created_at DESC
                LIMIT 100
            """)
        elif current_user_role == 'college_admin':
            # College admin can only see activities from their college users
            cur.execute("""
                SELECT a.*, u.username, u.full_name
                FROM user_activities a
                JOIN users u ON a.user_id = u.id
                WHERE u.college_id = %s OR u.id = %s
                ORDER BY a.created_at DESC
                LIMIT 100
            """, (current_college_id, session.get('user_id')))
        
        activities = cur.fetchall()
        return jsonify(activities)
    except Exception as e:
        print(f"❌ Error getting user activities: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================= COLLEGE MANAGEMENT ROUTES =================

@app.route('/api/colleges', methods=['GET'])
@login_required
@role_required('super_admin', 'college_admin', 'scanner_operator', 'viewer')
def get_colleges():
    """Get all colleges with their programs"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get all active colleges ordered by display_order
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order, created_at
            FROM colleges 
            WHERE is_active = TRUE
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        # For each college, get its programs
        for college in colleges:
            cur.execute("""
                SELECT id, code, name, is_active, display_order, created_at
                FROM programs 
                WHERE college_id = %s AND is_active = TRUE
                ORDER BY display_order, name
            """, (college['id'],))
            college['programs'] = cur.fetchall()
        
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting colleges: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges/all', methods=['GET'])
@login_required
@role_required('super_admin', 'college_admin')
def get_all_colleges():
    """Get all colleges (including inactive) for admin management"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get all colleges ordered by display_order
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order, created_at
            FROM colleges 
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        # For each college, get its programs
        for college in colleges:
            cur.execute("""
                SELECT id, code, name, is_active, display_order, created_at
                FROM programs 
                WHERE college_id = %s
                ORDER BY display_order, name
            """, (college['id'],))
            college['programs'] = cur.fetchall()
        
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting all colleges: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges', methods=['POST'])
@login_required
@role_required('super_admin')
def create_college():
    """Create a new college"""
    data = request.json
    if not data or not data.get('code') or not data.get('name'):
        return jsonify({"error": "College code and name are required"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if code already exists
        cur.execute("SELECT id FROM colleges WHERE code = %s", (data['code'],))
        if cur.fetchone():
            return jsonify({"error": "College code already exists"}), 409
        
        # Insert new college
        cur.execute("""
            INSERT INTO colleges (code, name, description, is_active, display_order)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, code, name, description, is_active, display_order, created_at
        """, (
            data['code'],
            data['name'],
            data.get('description', ''),
            data.get('is_active', True),
            data.get('display_order', 0)
        ))
        
        new_college = cur.fetchone()
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "College created successfully",
            "college": new_college
        })
    except Exception as e:
        print(f"❌ Error creating college: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges/<int:college_id>', methods=['PUT'])
@login_required
@role_required('super_admin')
def update_college(college_id):
    """Update a college"""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Build update query dynamically
        updates = []
        values = []
        
        if 'code' in data:
            # Check if new code conflicts with another college
            cur.execute("SELECT id FROM colleges WHERE code = %s AND id != %s", (data['code'], college_id))
            if cur.fetchone():
                return jsonify({"error": "College code already exists"}), 409
            updates.append("code = %s")
            values.append(data['code'])
        
        if 'name' in data:
            updates.append("name = %s")
            values.append(data['name'])
        
        if 'description' in data:
            updates.append("description = %s")
            values.append(data['description'])
        
        if 'is_active' in data:
            updates.append("is_active = %s")
            values.append(data['is_active'])
        
        if 'display_order' in data:
            updates.append("display_order = %s")
            values.append(data['display_order'])
        
        if not updates:
            return jsonify({"error": "No fields to update"}), 400
        
        values.append(college_id)
        update_query = f"UPDATE colleges SET {', '.join(updates)} WHERE id = %s RETURNING *"
        
        cur.execute(update_query, values)
        updated_college = cur.fetchone()
        conn.commit()
        
        if not updated_college:
            return jsonify({"error": "College not found"}), 404
        
        return jsonify({
            "status": "success",
            "message": "College updated successfully",
            "college": updated_college
        })
    except Exception as e:
        print(f"❌ Error updating college: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges/<int:college_id>', methods=['DELETE'])
@login_required
@role_required('super_admin')
def delete_college(college_id):
    """Delete a college (soft delete)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        # Soft delete by setting is_active to false
        cur.execute("UPDATE colleges SET is_active = FALSE WHERE id = %s RETURNING id", (college_id,))
        
        if cur.rowcount == 0:
            return jsonify({"error": "College not found"}), 404
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "College deactivated successfully"
        })
    except Exception as e:
        print(f"❌ Error deleting college: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges/<int:college_id>/restore', methods=['POST'])
@login_required
@role_required('super_admin')
def restore_college(college_id):
    """Restore a deleted college"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("UPDATE colleges SET is_active = TRUE WHERE id = %s RETURNING id", (college_id,))
        
        if cur.rowcount == 0:
            return jsonify({"error": "College not found"}), 404
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "College restored successfully"
        })
    except Exception as e:
        print(f"❌ Error restoring college: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/colleges/<int:college_id>/programs', methods=['GET'])
@login_required
def get_college_programs(college_id):
    """Get all programs for a specific college"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if college exists
        cur.execute("SELECT id FROM colleges WHERE id = %s", (college_id,))
        if not cur.fetchone():
            return jsonify({"error": "College not found"}), 404
        
        # Get all programs for this college
        cur.execute("""
            SELECT id, code, name, is_active, display_order, created_at
            FROM programs 
            WHERE college_id = %s
            ORDER BY display_order, name
        """, (college_id,))
        
        programs = cur.fetchall()
        return jsonify(programs)
    except Exception as e:
        print(f"❌ Error getting college programs: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/programs', methods=['POST'])
@login_required
@role_required('super_admin', 'college_admin')
def create_program():
    """Create a new program"""
    data = request.json
    if not data or not data.get('college_id') or not data.get('name'):
        return jsonify({"error": "College ID and program name are required"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if college exists
        cur.execute("SELECT id FROM colleges WHERE id = %s", (data['college_id'],))
        if not cur.fetchone():
            return jsonify({"error": "College not found"}), 404
        
        # Check if program name already exists for this college
        cur.execute("SELECT id FROM programs WHERE college_id = %s AND LOWER(name) = LOWER(%s)", 
                   (data['college_id'], data['name']))
        if cur.fetchone():
            return jsonify({"error": "Program name already exists for this college"}), 409
        
        # Insert new program
        cur.execute("""
            INSERT INTO programs (college_id, code, name, is_active, display_order)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, college_id, code, name, is_active, display_order, created_at
        """, (
            data['college_id'],
            data.get('code', ''),
            data['name'],
            data.get('is_active', True),
            data.get('display_order', 0)
        ))
        
        new_program = cur.fetchone()
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Program created successfully",
            "program": new_program
        })
    except Exception as e:
        print(f"❌ Error creating program: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/programs/<int:program_id>', methods=['PUT'])
@login_required
@role_required('super_admin', 'college_admin')
def update_program(program_id):
    """Update a program"""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Build update query dynamically
        updates = []
        values = []
        
        if 'name' in data:
            # Check if new name conflicts with another program in the same college
            cur.execute("SELECT college_id FROM programs WHERE id = %s", (program_id,))
            result = cur.fetchone()
            if not result:
                return jsonify({"error": "Program not found"}), 404
            
            college_id = result['college_id']
            cur.execute("SELECT id FROM programs WHERE college_id = %s AND LOWER(name) = LOWER(%s) AND id != %s", 
                       (college_id, data['name'], program_id))
            if cur.fetchone():
                return jsonify({"error": "Program name already exists in this college"}), 409
            updates.append("name = %s")
            values.append(data['name'])
        
        if 'code' in data:
            updates.append("code = %s")
            values.append(data['code'])
        
        if 'is_active' in data:
            updates.append("is_active = %s")
            values.append(data['is_active'])
        
        if 'display_order' in data:
            updates.append("display_order = %s")
            values.append(data['display_order'])
        
        if not updates:
            return jsonify({"error": "No fields to update"}), 400
        
        values.append(program_id)
        update_query = f"UPDATE programs SET {', '.join(updates)} WHERE id = %s RETURNING *"
        
        cur.execute(update_query, values)
        updated_program = cur.fetchone()
        conn.commit()
        
        if not updated_program:
            return jsonify({"error": "Program not found"}), 404
        
        return jsonify({
            "status": "success",
            "message": "Program updated successfully",
            "program": updated_program
        })
    except Exception as e:
        print(f"❌ Error updating program: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/programs/<int:program_id>', methods=['DELETE'])
@login_required
@role_required('super_admin', 'college_admin')
def delete_program(program_id):
    """Delete a program"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("DELETE FROM programs WHERE id = %s RETURNING id", (program_id,))
        
        if cur.rowcount == 0:
            return jsonify({"error": "Program not found"}), 404
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Program deleted successfully"
        })
    except Exception as e:
        print(f"❌ Error deleting program: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================= AUTHENTICATION ROUTES =================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login endpoint"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            return render_template('login.html', error="Username and password are required")
        
        conn = get_db_connection()
        if not conn:
            return render_template('login.html', error="Database connection failed")
        
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get user with college info
            cur.execute("""
                SELECT u.*, c.name as college_name
                FROM users u
                LEFT JOIN colleges c ON u.college_id = c.id
                WHERE u.username = %s AND u.is_active = TRUE
            """, (username,))
            
            user = cur.fetchone()
            
            if not user:
                return render_template('login.html', error="Invalid username or password")
            
            # Check if account is locked
            if user['locked_until'] and user['locked_until'] > datetime.now():
                locked_time = user['locked_until'].strftime('%Y-%m-%d %H:%M:%S')
                return render_template('login.html', error=f"Account locked until {locked_time}")
            
            # Verify password
            if not verify_password(password, user['password_hash']):
                # Increment failed attempts
                cur.execute("""
                    UPDATE users 
                    SET failed_attempts = failed_attempts + 1 
                    WHERE id = %s
                """, (user['id'],))
                
                # Lock account after 5 failed attempts
                if user['failed_attempts'] + 1 >= 5:
                    lock_until = datetime.now() + timedelta(minutes=30)
                    cur.execute("""
                        UPDATE users 
                        SET locked_until = %s 
                        WHERE id = %s
                    """, (lock_until, user['id']))
                    conn.commit()
                    return render_template('login.html', error="Account locked for 30 minutes due to too many failed attempts")
                
                conn.commit()
                return render_template('login.html', error="Invalid username or password")
            
            # Reset failed attempts on successful login
            cur.execute("""
                UPDATE users 
                SET failed_attempts = 0, 
                    locked_until = NULL,
                    last_login = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (user['id'],))
            
            conn.commit()
            
            # Set session data
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['user_role'] = user['role']
            session['full_name'] = user['full_name']
            session['college_id'] = user['college_id']
            session['college_name'] = user['college_name']
            
            # Log activity
            log_user_activity(
                user['id'],
                'login',
                f"User logged in",
                request.remote_addr,
                request.user_agent.string
            )
            
            # Redirect based on role
            if user['role'] in ['super_admin', 'college_admin']:
                return redirect('/admin/dashboard')
            elif user['role'] == 'scanner_operator':
                return redirect('/scanner')
            else:  # viewer
                return redirect('/history.html')
            
        except Exception as e:
            print(f"❌ Login error: {e}")
            return render_template('login.html', error="Login failed")
        finally:
            conn.close()
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """User logout"""
    if session.get('logged_in'):
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'logout',
            f"User logged out",
            request.remote_addr,
            request.user_agent.string
        )
    
    session.clear()
    return redirect('/login')

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change current user's password"""
    data = request.json
    if not data or not data.get('current_password') or not data.get('new_password'):
        return jsonify({"error": "Current and new password are required"}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get current user's password hash
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (session.get('user_id'),))
        result = cur.fetchone()
        
        if not result:
            return jsonify({"error": "User not found"}), 404
        
        # Verify current password
        if not verify_password(data['current_password'], result['password_hash']):
            return jsonify({"error": "Current password is incorrect"}), 400
        
        # Hash new password
        new_hashed_password = hash_password(data['new_password'])
        
        # Update password
        cur.execute("""
            UPDATE users 
            SET password_hash = %s,
                reset_token = NULL,
                reset_token_expiry = NULL
            WHERE id = %s
        """, (new_hashed_password, session.get('user_id')))
        
        conn.commit()
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'password_changed',
            f"Password changed",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({
            "status": "success",
            "message": "Password changed successfully"
        })
    except Exception as e:
        print(f"❌ Password change error: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================= ROUTES =================

@app.route('/')
def index():
    """Redirect to appropriate page based on login status"""
    if session.get('logged_in'):
        user_role = session.get('user_role')
        if user_role in ['super_admin', 'college_admin']:
            return redirect('/admin/dashboard')
        elif user_role == 'scanner_operator':
            return redirect('/scanner')
        else:  # viewer
            return redirect('/history.html')
    return redirect('/login')

@app.route('/scanner')
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
def scanner_page():
    """Scanner page for operators"""
    return render_template('index.html')

@app.route('/admin/dashboard')
@login_required
@role_required('super_admin', 'college_admin')
def admin_dashboard():
    """Admin dashboard"""
    return render_template('admin_dashboard.html')

@app.route('/admin/users')
@login_required
@role_required('super_admin', 'college_admin')
def admin_users_page():
    """User management page"""
    return render_template('admin_users.html')

@app.route('/history.html')
@login_required
def history_page():
    """History/records page"""
    return render_template('history.html')

@app.route('/admin/colleges')
@login_required
@role_required('super_admin', 'college_admin')
def admin_colleges():
    """Admin page for managing colleges and programs"""
    return render_template('admin_colleges.html')

# ================= GOOD MORAL SCANNING ENDPOINT =================
@app.route('/scan-goodmoral', methods=['POST'])
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
                other_documents,
                -- User tracking
                created_by
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
                %s,
                -- User tracking
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
            other_documents_json,
            # User tracking
            session.get('user_id')
        ))
        
        new_id = cur.fetchone()[0]
        conn.commit()

        print(f"✅ Record saved with ID: {new_id}")
        print(f"🎓 College: {college}")
        print(f"📚 Program: {program}")
        print(f"📊 Good Moral Score: {goodmoral_score} | Status: {disciplinary_status}")
        
        if has_disciplinary_record:
            print(f"⚠️ Student has disciplinary record: {disciplinary_details}")

        # Log activity
        log_user_activity(
            session.get('user_id'),
            'record_created',
            f"Created student record: {d.get('name')} (ID: {new_id})",
            request.remote_addr,
            request.user_agent.string
        )

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
@login_required
def get_records():
    conn = get_db_connection()
    if not conn: 
        return jsonify({"records": [], "error": "Database connection failed"})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        current_user_role = session.get('user_role')
        current_user_id = session.get('user_id')
        current_college_id = session.get('college_id')
        
        # Different queries based on role
        if current_user_role == 'super_admin':
            # Super admin can see all records
            cur.execute("SELECT * FROM records ORDER BY id DESC")
        elif current_user_role == 'college_admin':
            # College admin can see records from their college
            cur.execute("""
                SELECT * FROM records 
                WHERE college IN (SELECT name FROM colleges WHERE id = %s)
                ORDER BY id DESC
            """, (current_college_id,))
        elif current_user_role == 'scanner_operator':
            # Scanner operators can only see their own scanned records
            cur.execute("""
                SELECT * FROM records 
                WHERE created_by = %s
                ORDER BY id DESC
            """, (current_user_id,))
        else:  # viewer
            # Viewers can see all records (read-only)
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
def upload_other_document(record_id):
    """Upload other documents with title"""
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
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'document_uploaded',
            f"Uploaded other document: {title} for record ID: {record_id}",
            request.remote_addr,
            request.user_agent.string
        )
        
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
@login_required
@role_required('super_admin', 'college_admin')
def delete_other_document(record_id, doc_id):
    """Delete an other document"""
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
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'document_deleted',
            f"Deleted other document ID: {doc_id} from record ID: {record_id}",
            request.remote_addr,
            request.user_agent.string
        )
        
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
@login_required
def view_form(record_id):
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
            
            # Log activity
            log_user_activity(
                session.get('user_id'),
                'email_sent',
                f"Sent email for student: {student_name} (Record ID: {record_id})",
                request.remote_addr,
                request.user_agent.string
            )
            
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
            
            # Log activity
            log_user_activity(
                session.get('user_id'),
                'email_resent',
                f"Resent email for student: {student_name} (Record ID: {record_id})",
                request.remote_addr,
                request.user_agent.string
            )
            
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
@login_required
@role_required('scanner_operator', 'super_admin', 'college_admin')
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
@login_required
@role_required('super_admin', 'college_admin')
def delete_record(record_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        
        # Log activity
        log_user_activity(
            session.get('user_id'),
            'record_deleted',
            f"Deleted record ID: {record_id}",
            request.remote_addr,
            request.user_agent.string
        )
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally: 
        conn.close()

@app.route('/check-email-status/<int:record_id>', methods=['GET'])
@login_required
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

# ================= COLLEGE API FOR FRONTEND DROPDOWNS =================
@app.route('/api/colleges-dropdown', methods=['GET'])
@login_required
def get_colleges_dropdown():
    """Get active colleges and their programs for frontend dropdowns"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get all active colleges ordered by display_order
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order
            FROM colleges 
            WHERE is_active = TRUE
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        # Get all active programs
        cur.execute("""
            SELECT p.id, p.college_id, p.name, p.code, p.is_active, p.display_order
            FROM programs p
            JOIN colleges c ON p.college_id = c.id
            WHERE p.is_active = TRUE AND c.is_active = TRUE
            ORDER BY p.display_order, p.name
        """)
        programs = cur.fetchall()
        
        # Group programs by college_id
        programs_by_college = {}
        for program in programs:
            college_id = program['college_id']
            if college_id not in programs_by_college:
                programs_by_college[college_id] = []
            programs_by_college[college_id].append({
                'id': program['id'],
                'name': program['name'],
                'code': program['code']
            })
        
        # Add programs to colleges
        for college in colleges:
            college['programs'] = programs_by_college.get(college['id'], [])
        
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting colleges dropdown: {e}")
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
        "user_management": "ENABLED",
        "roles": ["super_admin", "college_admin", "scanner_operator", "viewer"],
        "dropdown_support": "ENABLED (College & Program dropdowns)",
        "college_management": "ENABLED",
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
    print("🚀 ASSISCAN WITH USER MANAGEMENT SYSTEM")
    print("="*60)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"🤖 Model: gemini-2.5-flash")
    print(f"📧 SendGrid: {'✅ SET' if SENDGRID_API_KEY else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print(f"📁 Uploads: {UPLOAD_FOLDER}")
    print(f"🔐 User Management: ✅ ENABLED")
    print(f"👥 Roles: super_admin, college_admin, scanner_operator, viewer")
    print("="*60)
    print("📊 FEATURES:")
    print("   • PSA, Form 137, Good Moral scanning")
    print("   • User Management System")
    print("   • Role-based access control")
    print("   • College Management System")
    print("   • Dynamic College & Program dropdowns")
    print("   • Disciplinary record detection")
    print("   • Other documents upload with title")
    print("   • Email notifications")
    print("   • Complete student record management")
    print("   • Activity logging")
    print("="*60)
    
    if os.path.exists(UPLOAD_FOLDER):
        file_count = len([f for f in os.listdir(UPLOAD_FOLDER) if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))])
        print(f"📊 Uploads folder contains {file_count} files")
    
    app.run(host='0.0.0.0', port=port, debug=False)
