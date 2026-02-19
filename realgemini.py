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
from functools import wraps
import time

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
            models = list(genai.list_models())
            gemini_2_5_flash_available = False
            
            for model in models:
                model_name = model.name
                if "gemini-2.5-flash" in model_name:
                    gemini_2_5_flash_available = True
                    print(f"✨ Found Gemini 2.5 Flash: {model_name}")
            
            if not gemini_2_5_flash_available:
                print("⚠️ Warning: gemini-2.5-flash not found in available models")
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

# Setup CORS - Allow all origins for Render
CORS(app, resources={
    r"/*": {
        "origins": ["*"],
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

# --- USER ROLES ---
ROLES = {
    'SUPER_ADMIN': 1,
    'STUDENT': 2
}

PERMISSIONS = {
    'SUPER_ADMIN': [
        'manage_users', 'manage_colleges', 'manage_programs',
        'view_all_records', 'edit_records', 'delete_records',
        'send_emails', 'view_dashboard', 'access_admin_panel',
        'manage_settings'
    ],
    'STUDENT': [
        'access_scanner', 'submit_documents', 'view_own_records',
        'change_password', 'view_own_documents', 'download_own_documents',
        'upload_additional_documents'
    ]
}

# ================= DECORATORS FOR ROLE-BASED ACCESS =================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

def role_required(required_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({"error": "Authentication required"}), 401
            
            user_role = session.get('role', '').upper()
            if user_role != required_role:
                return jsonify({"error": f"{required_role} access required"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({"error": "Authentication required"}), 401
            
            user_role = session.get('role', '').upper()
            if user_role not in PERMISSIONS or permission not in PERMISSIONS[user_role]:
                return jsonify({"error": "Permission denied"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ================= SCHOOL YEAR SETTINGS =================
SCHOOL_YEAR_FILE = os.path.join(BASE_DIR, 'school_year.json')

def get_school_year():
    """Get the current active school year"""
    default_year = "2025-2026"
    try:
        if os.path.exists(SCHOOL_YEAR_FILE):
            with open(SCHOOL_YEAR_FILE, 'r') as f:
                data = json.load(f)
                return data.get('school_year', default_year)
    except Exception as e:
        print(f"⚠️ Error reading school year file: {e}")
    return default_year

def save_school_year(school_year):
    """Save the active school year"""
    try:
        with open(SCHOOL_YEAR_FILE, 'w') as f:
            json.dump({
                'school_year': school_year, 
                'updated_at': datetime.now().isoformat()
            }, f)
        return True
    except Exception as e:
        print(f"❌ Error saving school year: {e}")
        return False

@app.route('/api/settings/school-year', methods=['GET'])
@login_required
def get_school_year_endpoint():
    """Get current active school year"""
    try:
        school_year = get_school_year()
        return jsonify({
            "school_year": school_year,
            "success": True
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/settings/school-year', methods=['POST'])
@login_required
@permission_required('manage_settings')
def set_school_year():
    """Set active school year (Super Admin only)"""
    try:
        data = request.json
        school_year = data.get('school_year')
        
        if not school_year:
            return jsonify({"error": "School year is required"}), 400
        
        # Validate format YYYY-YYYY
        if not re.match(r'^\d{4}-\d{4}$', school_year):
            return jsonify({"error": "Invalid format. Use YYYY-YYYY (e.g., 2025-2026)"}), 400
        
        # Validate year logic
        start_year, end_year = map(int, school_year.split('-'))
        if end_year != start_year + 1:
            return jsonify({"error": "End year must be exactly one year after start year"}), 400
        
        if save_school_year(school_year):
            return jsonify({
                "success": True,
                "message": "School year updated successfully",
                "school_year": school_year
            })
        else:
            return jsonify({"error": "Failed to save school year"}), 500
            
    except Exception as e:
        print(f"❌ Error setting school year: {e}")
        return jsonify({"error": str(e)}), 500

# ================= PASSWORD FUNCTIONS =================
def hash_password(password):
    """Hash password with salt"""
    salt = secrets.token_hex(16)
    return salt + "$" + hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(stored_hash, password):
    """Verify password against stored hash"""
    if "$" not in stored_hash:
        return False
    
    salt, hash_value = stored_hash.split("$", 1)
    computed_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return hash_value == computed_hash

def generate_temp_password(length=8):
    """Generate temporary password for new users"""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ================= DATABASE FUNCTIONS =================
def get_db_connection():
    """Get database connection for Render PostgreSQL with retry logic"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            if DATABASE_URL:
                if DATABASE_URL.startswith("postgres://"):
                    DATABASE_URL_FIXED = DATABASE_URL.replace("postgres://", "postgresql://", 1)
                    conn = psycopg2.connect(DATABASE_URL_FIXED, sslmode='require')
                else:
                    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
                print(f"✅ Database connection successful (attempt {attempt + 1})")
                return conn
            else:
                print("❌ DATABASE_URL not found in environment")
                return None
        except Exception as e:
            print(f"❌ DB Connection Error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                return None

def drop_all_tables():
    """Drop all existing tables to start fresh"""
    print("🗑️  Dropping all existing tables...")
    conn = get_db_connection()
    if not conn:
        print("❌ Failed to connect to database for dropping tables")
        return False
    
    try:
        cur = conn.cursor()
        
        tables = [
            'user_sessions',
            'records',
            'programs',
            'colleges',
            'users'
        ]
        
        for table in tables:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                print(f"   ✅ Dropped table: {table}")
            except Exception as e:
                print(f"   ⚠️ Could not drop table {table}: {e}")
        
        conn.commit()
        print("✅ All tables dropped successfully")
        return True
    except Exception as e:
        print(f"❌ Error dropping tables: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def init_db():
    """Initialize database tables with UNIQUE constraints to prevent duplicates"""
    print("🔧 Initializing database from scratch...")
    
    if not drop_all_tables():
        print("❌ Failed to drop existing tables")
        return False
    
    conn = get_db_connection()
    if not conn:
        print("❌ Failed to connect to database for initialization")
        return False
    
    try:
        cur = conn.cursor()
        
        print("📝 Creating users table...")
        cur.execute('''
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                role VARCHAR(20) NOT NULL CHECK (role IN ('SUPER_ADMIN', 'STUDENT')),
                college_id INTEGER,
                program_id INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                requires_password_reset BOOLEAN DEFAULT TRUE,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("   ✅ Created users table")
        
        print("📝 Creating user_sessions table...")
        cur.execute('''
            CREATE TABLE user_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                session_token VARCHAR(255) UNIQUE NOT NULL,
                ip_address VARCHAR(45),
                user_agent TEXT,
                login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                logout_at TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        print("   ✅ Created user_sessions table")
        
        print("📝 Creating colleges table...")
        cur.execute('''
            CREATE TABLE colleges (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                name VARCHAR(150) NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER REFERENCES users(id)
            )
        ''')
        print("   ✅ Created colleges table")
        
        print("📝 Creating programs table...")
        cur.execute('''
            CREATE TABLE programs (
                id SERIAL PRIMARY KEY,
                college_id INTEGER,
                code VARCHAR(50),
                name VARCHAR(150) NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER REFERENCES users(id),
                FOREIGN KEY (college_id) REFERENCES colleges(id) ON DELETE CASCADE
            )
        ''')
        print("   ✅ Created programs table")
        
        print("📝 Creating records table...")
        cur.execute('''
            CREATE TABLE records (
                id SERIAL PRIMARY KEY,
                user_id INTEGER UNIQUE,
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                document_status JSONB DEFAULT '{"psa": false, "form137": false, "form138": false, "goodmoral": false}'::jsonb,
                rejection_reason TEXT,
                status VARCHAR(20) DEFAULT 'INCOMPLETE' CHECK (status IN ('INCOMPLETE', 'PENDING', 'APPROVED', 'REJECTED')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT one_record_per_user UNIQUE (user_id)
            )
        ''')
        print("   ✅ Created records table")
        
        print("🔗 Adding foreign key constraints...")
        cur.execute('''
            ALTER TABLE users 
            ADD CONSTRAINT fk_users_college 
            FOREIGN KEY (college_id) REFERENCES colleges(id) ON DELETE SET NULL
        ''')
        
        cur.execute('''
            ALTER TABLE users 
            ADD CONSTRAINT fk_users_program 
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE SET NULL
        ''')
        
        cur.execute('''
            ALTER TABLE users 
            ADD CONSTRAINT fk_users_created_by 
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ''')
        
        print("   ✅ Added foreign key constraints")
        
        conn.commit()
        print("✅ Database tables created successfully")
        
        print("👑 Creating default Super Admin...")
        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        admin_user = cur.fetchone()
        
        if not admin_user:
            password_hash = hash_password(ADMIN_PASSWORD)
            
            cur.execute("""
                INSERT INTO users (username, password_hash, full_name, email, role, is_active, requires_password_reset)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                ADMIN_USERNAME,
                password_hash,
                'System Administrator',
                'admin@assiscan.com',
                'SUPER_ADMIN',
                True,
                False
            ))
            admin_id = cur.fetchone()[0]
            print(f"✅ Default Super Admin created with ID: {admin_id}")
            
            print("📝 Inserting default colleges...")
            default_colleges = [
                ("CCJE", "College of Criminal Justice Education", "College of Criminal Justice Education", 1),
                ("CEAS", "College of Education, Arts and Sciences", "College of Education, Arts and Sciences", 2),
                ("CITEC", "College of Information Technology, Entertainment and Communication", "College of IT, Entertainment & Communication", 3),
                ("CENAR", "College of Engineering and Architecture", "College of Engineering and Architecture", 4),
                ("CBAA", "College of Business, Accountancy and Auditing", "College of Business, Accountancy & Auditing", 5)
            ]
            
            for code, name, desc, order in default_colleges:
                cur.execute("""
                    INSERT INTO colleges (code, name, description, display_order, created_by) 
                    VALUES (%s, %s, %s, %s, %s) 
                    RETURNING id
                """, (code, name, desc, order, admin_id))
                college_id = cur.fetchone()[0]
                
                if code == "CCJE":
                    cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                               (college_id, "Bachelor of Science in Criminology", 1, admin_id))
                elif code == "CEAS":
                    programs = [
                        "Bachelor of Elementary Education",
                        "Bachelor of Secondary Education", 
                        "Bachelor of Science in Psychology",
                        "Bachelor of Science in Legal Management",
                        "Bachelor of Science in Social Work"
                    ]
                    for i, program in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, program, i+1, admin_id))
                elif code == "CITEC":
                    programs = [
                        "Bachelor of Science in Information Technology",
                        "Bachelor of Arts in Multimedia Arts"
                    ]
                    for i, program in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, program, i+1, admin_id))
                elif code == "CENAR":
                    programs = [
                        "Bachelor of Science in Industrial Engineering",
                        "Bachelor of Science in Computer Engineering",
                        "Bachelor of Science in Architecture"
                    ]
                    for i, program in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, program, i+1, admin_id))
                elif code == "CBAA":
                    programs = [
                        "Bachelor of Science in Business Administration",
                        "Bachelor of Science in Accountancy",
                        "Bachelor of Science in Internal Auditing"
                    ]
                    for i, program in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, program, i+1, admin_id))
            
            print("✅ Default colleges and programs inserted")
        else:
            print("✅ Super Admin already exists")
        
        conn.commit()
        print("🎉 Database initialization COMPLETE!")
        return True
        
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        traceback.print_exc()
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def check_tables_exist():
    """Check if all required tables exist"""
    print("🔍 Checking if tables exist...")
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        tables = ['users', 'user_sessions', 'colleges', 'programs', 'records']
        missing_tables = []
        
        for table in tables:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                )
            """, (table,))
            exists = cur.fetchone()[0]
            
            if not exists:
                missing_tables.append(table)
        
        if missing_tables:
            print(f"❌ Missing tables: {missing_tables}")
            return False
        
        print("✅ All tables exist")
        return True
        
    except Exception as e:
        print(f"❌ Error checking tables: {e}")
        return False
    finally:
        conn.close()

# Initialize database on startup
print("\n" + "="*60)
print("🔄 DATABASE INITIALIZATION")
print("="*60)

if not check_tables_exist():
    print("⚠️ Tables missing, initializing database...")
    if init_db():
        print("✅ Database initialization successful!")
    else:
        print("❌ Database initialization failed!")
else:
    print("✅ Database tables already exist")

# ================= USER MANAGEMENT FUNCTIONS =================
def create_session(user_id, ip_address=None, user_agent=None):
    """Create a new session for user"""
    session_token = secrets.token_urlsafe(32)
    conn = get_db_connection()
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_sessions (user_id, session_token, ip_address, user_agent)
            VALUES (%s, %s, %s, %s)
            RETURNING id, session_token
        """, (user_id, session_token, ip_address, user_agent))
        
        session_data = cur.fetchone()
        conn.commit()
        
        return session_token
    except Exception as e:
        print(f"❌ Session creation error: {e}")
        return None
    finally:
        conn.close()

def validate_session(session_token):
    """Validate user session"""
    conn = get_db_connection()
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT u.*, us.session_token 
            FROM user_sessions us
            JOIN users u ON us.user_id = u.id
            WHERE us.session_token = %s 
            AND us.is_active = TRUE 
            AND u.is_active = TRUE
            AND (us.logout_at IS NULL OR us.logout_at > NOW() - INTERVAL '24 hours')
        """, (session_token,))
        
        user = cur.fetchone()
        return user
    except Exception as e:
        print(f"❌ Session validation error: {e}")
        return None
    finally:
        conn.close()

def logout_session(session_token):
    """Logout user session"""
    conn = get_db_connection()
    
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE user_sessions 
            SET logout_at = CURRENT_TIMESTAMP, is_active = FALSE 
            WHERE session_token = %s
        """, (session_token,))
        conn.commit()
        return True
    except Exception as e:
        print(f"❌ Session logout error: {e}")
        return False
    finally:
        conn.close()

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
        
        student_info = ""
        if student_data:
            # Get document status
            doc_status = student_data.get('document_status', {})
            if isinstance(doc_status, str):
                try:
                    doc_status = json.loads(doc_status)
                except:
                    doc_status = {}
            
            # Count submitted documents
            submitted_docs = []
            if doc_status.get('psa'): submitted_docs.append("PSA")
            if doc_status.get('form137'): submitted_docs.append("Form 137")
            if doc_status.get('form138'): submitted_docs.append("Form 138")
            if doc_status.get('goodmoral'): submitted_docs.append("Good Moral")
            
            doc_summary = ", ".join(submitted_docs) if submitted_docs else "No documents yet"
            doc_count = len(submitted_docs)
            doc_status_text = f"{doc_count}/4 documents submitted"
            
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
• Religion: {student_data.get('religion', 'N/A')}

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
📄 DOCUMENT STATUS
━━━━━━━━━━━━━━━━━━━━━━━━
• Status: {doc_status_text}
• Submitted Documents: {doc_summary}
• Record Status: {student_data.get('status', 'INCOMPLETE')}

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
            print(f"⚠️ Trying to find alternative model...")
            
            try:
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
    score = 100
    
    if analysis_data.get('has_disciplinary_record'):
        score -= 40
    
    serious_violations = ['suspended', 'expelled', 'disciplinary action', 'major violation']
    remarks = analysis_data.get('remarks', '').lower()
    
    for violation in serious_violations:
        if violation in remarks:
            score -= 30
            break
    
    conditional_phrases = ['conditional', 'subject to', 'pending', 'under review']
    for phrase in conditional_phrases:
        if phrase in remarks:
            score -= 20
            break
    
    score = max(0, min(100, score))
    
    if score >= 90:
        status = 'EXCELLENT'
    elif score >= 70:
        status = 'GOOD'
    elif score >= 50:
        status = 'FAIR'
    else:
        status = 'POOR'
    
    return score, status

def update_document_status(record_id, doc_type, has_file):
    """Update document status in database"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Get current document_status
        cur.execute("SELECT document_status, status FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        if result and result[0]:
            try:
                if isinstance(result[0], dict):
                    status = result[0]
                else:
                    status = json.loads(result[0])
            except:
                status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        else:
            status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        # Update specific document status
        status[doc_type] = has_file
        
        # Determine overall record status (only if not already APPROVED/REJECTED)
        current_record_status = result[1] if result and len(result) > 1 else 'INCOMPLETE'
        
        if current_record_status not in ['APPROVED', 'REJECTED']:
            all_docs = all([status.get('psa', False), status.get('form137', False), 
                           status.get('form138', False), status.get('goodmoral', False)])
            
            if all_docs:
                overall_status = 'PENDING'
            else:
                overall_status = 'INCOMPLETE'
        else:
            overall_status = current_record_status
        
        # Update database
        cur.execute("""
            UPDATE records 
            SET document_status = %s, 
                status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(status), overall_status, record_id))
        
        conn.commit()
        print(f"📄 Document status updated for record {record_id}: {doc_type}={has_file}")
        return status, overall_status
        
    except Exception as e:
        print(f"❌ Error updating document status: {e}")
        return None, None
    finally:
        conn.close()

# ================= DEBUG MIDDLEWARE =================
@app.before_request
def log_request_info():
    """Log all requests for debugging"""
    if request.path not in ['/static/', '/favicon.ico']:
        print(f"\n{'='*60}")
        print(f"🌐 {request.method} {request.path}")
        print(f"🔍 Session: {dict(session)}")
        print(f"📱 IP: {request.remote_addr}")
        print(f"{'='*60}")

# ================= DATABASE INITIALIZATION ENDPOINT =================
@app.route('/api/init-db', methods=['POST'])
def initialize_database():
    """Endpoint to manually initialize database"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or auth_header != f"Bearer {app.secret_key}":
            return jsonify({"error": "Unauthorized"}), 401
        
        print("🔄 Manual database initialization requested...")
        
        if init_db():
            return jsonify({
                "status": "success",
                "message": "Database initialized successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Database initialization failed"
            }), 500
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-db', methods=['GET'])
def check_database():
    """Check database status"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Cannot connect to database"
            }), 500
        
        cur = conn.cursor()
        
        tables = ['users', 'colleges', 'programs', 'records', 'user_sessions']
        table_status = {}
        
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                table_status[table] = {"exists": True, "count": count}
            except Exception:
                table_status[table] = {"exists": False, "count": 0}
        
        conn.close()
        
        return jsonify({
            "status": "success",
            "database": "connected",
            "tables": table_status,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ================= USER AUTHENTICATION ROUTES =================
@app.route('/api/login', methods=['POST'])
def login_user():
    """User login endpoint"""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({"error": "Username and password required"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, username, password_hash, full_name, email, role, is_active, 
                   requires_password_reset, college_id, program_id
            FROM users 
            WHERE username = %s OR email = %s
        """, (username, username))
        
        user = cur.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"error": "Invalid credentials"}), 401
        
        if not user['is_active']:
            conn.close()
            return jsonify({"error": "Account is deactivated"}), 403
        
        print(f"🔑 Login attempt: username={username}")
        
        if not verify_password(user['password_hash'], password):
            print(f"❌ Password mismatch for user {username}")
            conn.close()
            return jsonify({"error": "Invalid credentials"}), 401
        
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent')
        session_token = create_session(user['id'], ip_address, user_agent)
        
        if not session_token:
            conn.close()
            return jsonify({"error": "Failed to create session"}), 500
        
        cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user['id'],))
        conn.commit()
        conn.close()
        
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['full_name'] = user['full_name']
        session['role'] = user['role'].upper()
        session['email'] = user['email']
        session['session_token'] = session_token
        session['requires_password_reset'] = user['requires_password_reset']
        
        user_response = {
            'id': user['id'],
            'username': user['username'],
            'full_name': user['full_name'],
            'email': user['email'],
            'role': user['role'].upper(),
            'requires_password_reset': user['requires_password_reset'],
            'college_id': user['college_id'],
            'program_id': user['program_id']
        }
        
        return jsonify({
            "status": "success",
            "message": "Login successful",
            "user": user_response,
            "session_token": session_token,
            "permissions": PERMISSIONS.get(user['role'].upper(), [])
        })
        
    except Exception as e:
        print(f"❌ Login error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/logout', methods=['POST'])
@login_required
def logout_user():
    """User logout endpoint"""
    try:
        session_token = session.get('session_token')
        if session_token:
            logout_session(session_token)
        
        session.clear()
        
        return jsonify({
            "status": "success",
            "message": "Logged out successfully"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Simple logout route that redirects to login page"""
    print("🔍 /logout route accessed")
    
    session_token = session.get('session_token')
    if session_token:
        logout_session(session_token)
    
    session.clear()
    
    print("✅ Session cleared, redirecting to login page")
    return redirect('/login')

@app.route('/api/check-session', methods=['GET'])
def check_session():
    """Check if user session is valid"""
    print(f"🔍 Checking session: {dict(session)}")
    
    if 'user_id' not in session:
        print("❌ No user_id in session")
        return jsonify({"authenticated": False}), 200
    
    session_token = session.get('session_token')
    
    if not session_token:
        print("❌ No session token in session")
        return jsonify({"authenticated": False}), 200
    
    user = validate_session(session_token)
    
    if user:
        print(f"✅ Valid session for user: {user['username']}, role: {user['role']}")
        return jsonify({
            "authenticated": True,
            "user": {
                'id': user['id'],
                'username': user['username'],
                'full_name': user['full_name'],
                'email': user['email'],
                'role': user['role'].upper()
            },
            "permissions": PERMISSIONS.get(user['role'].upper(), [])
        })
    else:
        print("❌ Invalid session token")
        return jsonify({"authenticated": False}), 200

# ================= PASSWORD MANAGEMENT ENDPOINTS =================
@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password"""
    try:
        data = request.json
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        
        if not current_password or not new_password:
            return jsonify({"error": "Current and new password required"}), 400
        
        if len(new_password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        
        if not re.search(r'[A-Z]', new_password):
            return jsonify({"error": "Password must contain at least one uppercase letter"}), 400
        
        if not re.search(r'[a-z]', new_password):
            return jsonify({"error": "Password must contain at least one lowercase letter"}), 400
        
        if not re.search(r'[0-9]', new_password):
            return jsonify({"error": "Password must contain at least one number"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"error": "User not found"}), 404
        
        if not verify_password(user['password_hash'], current_password):
            conn.close()
            return jsonify({"error": "Current password is incorrect"}), 401
        
        new_hash = hash_password(new_password)
        cur.execute("""
            UPDATE users 
            SET password_hash = %s, requires_password_reset = FALSE, updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (new_hash, session['user_id']))
        
        conn.commit()
        conn.close()
        
        session['requires_password_reset'] = False
        
        return jsonify({
            "status": "success",
            "message": "Password changed successfully"
        })
        
    except Exception as e:
        print(f"❌ Password change error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-password-reset', methods=['GET'])
@login_required
def check_password_reset():
    """Check if user needs to reset password"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT requires_password_reset FROM users WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        conn.close()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        return jsonify({
            "requires_password_reset": user['requires_password_reset']
        })
        
    except Exception as e:
        print(f"❌ Password reset check error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= CHANGE PASSWORD PAGE =================
@app.route('/change-password', methods=['GET'])
def change_password_page():
    """Render change password page"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'STUDENT':
        return redirect('/')
    
    return render_template('change_password.html')

# ================= USER MANAGEMENT ROUTES (SUPER ADMIN ONLY) =================
@app.route('/api/users', methods=['GET'])
@login_required
@permission_required('manage_users')
def get_users():
    """Get all users (Super Admin only)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                   u.requires_password_reset, u.last_login, u.created_at,
                   c.name as college_name, p.name as program_name,
                   creator.full_name as created_by_name
            FROM users u
            LEFT JOIN colleges c ON u.college_id = c.id
            LEFT JOIN programs p ON u.program_id = p.id
            LEFT JOIN users creator ON u.created_by = creator.id
            ORDER BY u.created_at DESC
        """)
        
        users = cur.fetchall()
        
        conn.close()
        
        for user in users:
            if user['last_login']:
                user['last_login'] = user['last_login'].isoformat()
            if user['created_at']:
                user['created_at'] = user['created_at'].isoformat()
        
        return jsonify(users)
        
    except Exception as e:
        print(f"❌ Get users error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/users', methods=['POST'])
@login_required
@permission_required('manage_users')
def create_user():
    """Create new user (Super Admin only)"""
    conn = None
    try:
        data = request.json
        
        required_fields = ['username', 'full_name', 'email', 'role']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field.replace('_', ' ').title()} is required"}), 400
        
        if data['role'] not in ['SUPER_ADMIN', 'STUDENT']:
            return jsonify({"error": "Invalid role"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM users WHERE username = %s OR email = %s", 
                   (data['username'], data['email']))
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "Username or email already exists"}), 409
        
        temp_password = generate_temp_password()
        password_hash = hash_password(temp_password)
        
        college_id = data.get('college_id')
        program_id = data.get('program_id')
        
        cur.execute("""
            INSERT INTO users (
                username, password_hash, full_name, email, role,
                college_id, program_id, is_active, requires_password_reset,
                created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, username, full_name, email, role, is_active, created_at
        """, (
            data['username'],
            password_hash,
            data['full_name'],
            data['email'],
            data['role'],
            college_id,
            program_id,
            data.get('is_active', True),
            True,
            session['user_id']
        ))
        
        new_user = cur.fetchone()
        conn.commit()
        conn.close()
        
        user_response = {
            'id': new_user[0],
            'username': new_user[1],
            'full_name': new_user[2],
            'email': new_user[3],
            'role': new_user[4],
            'is_active': new_user[5],
            'created_at': new_user[6].isoformat() if new_user[6] else None,
            'temp_password': temp_password
        }
        
        return jsonify({
            "status": "success",
            "message": "User created successfully",
            "user": user_response
        })
        
    except Exception as e:
        print(f"❌ Create user error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@permission_required('manage_users')
def update_user(user_id):
    """Update user (Super Admin only)"""
    conn = None
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"error": "User not found"}), 404
        
        if user['id'] == session['user_id'] and 'role' in data:
            conn.close()
            return jsonify({"error": "Cannot change your own role"}), 400
        
        updates = []
        values = []
        
        if 'full_name' in data:
            updates.append("full_name = %s")
            values.append(data['full_name'])
        
        if 'email' in data:
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", 
                       (data['email'], user_id))
            if cur.fetchone():
                conn.close()
                return jsonify({"error": "Email already in use"}), 409
            updates.append("email = %s")
            values.append(data['email'])
        
        if 'role' in data:
            if data['role'] not in ['SUPER_ADMIN', 'STUDENT']:
                conn.close()
                return jsonify({"error": "Invalid role"}), 400
            updates.append("role = %s")
            values.append(data['role'])
        
        if 'is_active' in data:
            updates.append("is_active = %s")
            values.append(data['is_active'])
        
        if 'college_id' in data:
            updates.append("college_id = %s")
            values.append(data['college_id'])
        
        if 'program_id' in data:
            updates.append("program_id = %s")
            values.append(data['program_id'])
        
        if data.get('reset_password'):
            temp_password = generate_temp_password()
            password_hash = hash_password(temp_password)
            updates.append("password_hash = %s")
            updates.append("requires_password_reset = TRUE")
            values.append(password_hash)
            temp_password_return = temp_password
        else:
            temp_password_return = None
        
        if not updates:
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(user_id)
        
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = %s RETURNING *"
        cur.execute(query, values)
        
        updated_user = cur.fetchone()
        conn.commit()
        conn.close()
        
        user_response = {
            'id': updated_user['id'],
            'username': updated_user['username'],
            'full_name': updated_user['full_name'],
            'email': updated_user['email'],
            'role': updated_user['role'],
            'is_active': updated_user['is_active'],
            'college_id': updated_user['college_id'],
            'program_id': updated_user['program_id'],
            'updated_at': updated_user['updated_at'].isoformat() if updated_user['updated_at'] else None
        }
        
        response_data = {
            "status": "success",
            "message": "User updated successfully",
            "user": user_response
        }
        
        if temp_password_return:
            response_data['temp_password'] = temp_password_return
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ Update user error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@permission_required('manage_users')
def delete_user(user_id):
    """Delete user (Soft delete - Super Admin only)"""
    try:
        if user_id == session['user_id']:
            return jsonify({"error": "Cannot delete your own account"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s RETURNING id", (user_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "User not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "User deactivated successfully"
        })
        
    except Exception as e:
        print(f"❌ Delete user error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<int:user_id>/activate', methods=['POST'])
@login_required
@permission_required('manage_users')
def activate_user(user_id):
    """Activate user (Super Admin only)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute("UPDATE users SET is_active = TRUE WHERE id = %s RETURNING id", (user_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "User not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "User activated successfully"
        })
        
    except Exception as e:
        print(f"❌ Activate user error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= PROFILE ROUTES =================
@app.route('/api/profile', methods=['GET'])
@login_required
def get_profile():
    """Get current user profile"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                   u.requires_password_reset, u.last_login, u.created_at,
                   c.name as college_name, p.name as program_name,
                   u.college_id, u.program_id
            FROM users u
            LEFT JOIN colleges c ON u.college_id = c.id
            LEFT JOIN programs p ON u.program_id = p.id
            WHERE u.id = %s
        """, (session['user_id'],))
        
        profile = cur.fetchone()
        conn.close()
        
        if not profile:
            return jsonify({"error": "Profile not found"}), 404
        
        if profile['last_login']:
            profile['last_login'] = profile['last_login'].isoformat()
        if profile['created_at']:
            profile['created_at'] = profile['created_at'].isoformat()
        
        profile['permissions'] = PERMISSIONS.get(profile['role'].upper(), [])
        
        return jsonify(profile)
        
    except Exception as e:
        print(f"❌ Get profile error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/profile', methods=['PUT'])
@login_required
def update_profile():
    """Update current user profile"""
    conn = None
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        updates = []
        values = []
        
        if 'full_name' in data:
            updates.append("full_name = %s")
            values.append(data['full_name'])
        
        if 'email' in data:
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", 
                       (data['email'], session['user_id']))
            if cur.fetchone():
                conn.close()
                return jsonify({"error": "Email already in use"}), 409
            updates.append("email = %s")
            values.append(data['email'])
        
        if not updates:
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(session['user_id'])
        
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = %s RETURNING id, username, full_name, email"
        cur.execute(query, values)
        
        updated_profile = cur.fetchone()
        conn.commit()
        conn.close()
        
        if 'full_name' in data:
            session['full_name'] = data['full_name']
        if 'email' in data:
            session['email'] = data['email']
        
        return jsonify({
            "status": "success",
            "message": "Profile updated successfully",
            "profile": {
                'id': updated_profile[0],
                'username': updated_profile[1],
                'full_name': updated_profile[2],
                'email': updated_profile[3]
            }
        })
        
    except Exception as e:
        print(f"❌ Update profile error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= COLLEGE MANAGEMENT ROUTES =================
@app.route('/api/colleges', methods=['GET'])
@login_required
def get_colleges():
    """Get all colleges with their programs"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order, created_at
            FROM colleges 
            WHERE is_active = TRUE
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        for college in colleges:
            cur.execute("""
                SELECT id, code, name, is_active, display_order, created_at
                FROM programs 
                WHERE college_id = %s AND is_active = TRUE
                ORDER BY display_order, name
            """, (college['id'],))
            college['programs'] = cur.fetchall()
        
        conn.close()
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting colleges: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges/all', methods=['GET'])
@login_required
@permission_required('manage_colleges')
def get_all_colleges():
    """Get all colleges (including inactive) for admin management"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order, created_at
            FROM colleges 
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        for college in colleges:
            cur.execute("""
                SELECT id, code, name, is_active, display_order, created_at
                FROM programs 
                WHERE college_id = %s
                ORDER BY display_order, name
            """, (college['id'],))
            college['programs'] = cur.fetchall()
        
        conn.close()
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting all colleges: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges', methods=['POST'])
@login_required
@permission_required('manage_colleges')
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
        
        cur.execute("SELECT id FROM colleges WHERE code = %s", (data['code'],))
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "College code already exists"}), 409
        
        cur.execute("""
            INSERT INTO colleges (code, name, description, is_active, display_order, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, code, name, description, is_active, display_order, created_at
        """, (
            data['code'],
            data['name'],
            data.get('description', ''),
            data.get('is_active', True),
            data.get('display_order', 0),
            session['user_id']
        ))
        
        new_college = cur.fetchone()
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "College created successfully",
            "college": new_college
        })
    except Exception as e:
        print(f"❌ Error creating college: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges/<int:college_id>', methods=['PUT'])
@login_required
@permission_required('manage_colleges')
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
        
        updates = []
        values = []
        
        if 'code' in data:
            cur.execute("SELECT id FROM colleges WHERE code = %s AND id != %s", (data['code'], college_id))
            if cur.fetchone():
                conn.close()
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
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        
        values.append(college_id)
        update_query = f"UPDATE colleges SET {', '.join(updates)} WHERE id = %s RETURNING *"
        
        cur.execute(update_query, values)
        updated_college = cur.fetchone()
        conn.commit()
        conn.close()
        
        if not updated_college:
            return jsonify({"error": "College not found"}), 404
        
        return jsonify({
            "status": "success",
            "message": "College updated successfully",
            "college": updated_college
        })
    except Exception as e:
        print(f"❌ Error updating college: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges/<int:college_id>', methods=['DELETE'])
@login_required
@permission_required('manage_colleges')
def delete_college(college_id):
    """Delete a college (soft delete)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("UPDATE colleges SET is_active = FALSE WHERE id = %s RETURNING id", (college_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "College not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "College deactivated successfully"
        })
    except Exception as e:
        print(f"❌ Error deleting college: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges/<int:college_id>/restore', methods=['POST'])
@login_required
@permission_required('manage_colleges')
def restore_college(college_id):
    """Restore a deleted college"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("UPDATE colleges SET is_active = TRUE WHERE id = %s RETURNING id", (college_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "College not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "College restored successfully"
        })
    except Exception as e:
        print(f"❌ Error restoring college: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/colleges/<int:college_id>/programs', methods=['GET'])
@login_required
def get_college_programs(college_id):
    """Get all programs for a specific college"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT id FROM colleges WHERE id = %s", (college_id,))
        if not cur.fetchone():
            conn.close()
            return jsonify({"error": "College not found"}), 404
        
        cur.execute("""
            SELECT id, code, name, is_active, display_order, created_at
            FROM programs 
            WHERE college_id = %s
            ORDER BY display_order, name
        """, (college_id,))
        
        programs = cur.fetchall()
        conn.close()
        return jsonify(programs)
    except Exception as e:
        print(f"❌ Error getting college programs: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/programs', methods=['POST'])
@login_required
@permission_required('manage_programs')
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
        
        cur.execute("SELECT id FROM colleges WHERE id = %s", (data['college_id'],))
        if not cur.fetchone():
            conn.close()
            return jsonify({"error": "College not found"}), 404
        
        cur.execute("SELECT id FROM programs WHERE college_id = %s AND LOWER(name) = LOWER(%s)", 
                   (data['college_id'], data['name']))
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "Program name already exists for this college"}), 409
        
        cur.execute("""
            INSERT INTO programs (college_id, code, name, is_active, display_order, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, college_id, code, name, is_active, display_order, created_at
        """, (
            data['college_id'],
            data.get('code', ''),
            data['name'],
            data.get('is_active', True),
            data.get('display_order', 0),
            session['user_id']
        ))
        
        new_program = cur.fetchone()
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Program created successfully",
            "program": new_program
        })
    except Exception as e:
        print(f"❌ Error creating program: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/programs/<int:program_id>', methods=['PUT'])
@login_required
@permission_required('manage_programs')
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
        
        updates = []
        values = []
        
        if 'name' in data:
            cur.execute("SELECT college_id FROM programs WHERE id = %s", (program_id,))
            result = cur.fetchone()
            if not result:
                conn.close()
                return jsonify({"error": "Program not found"}), 404
            
            college_id = result['college_id']
            cur.execute("SELECT id FROM programs WHERE college_id = %s AND LOWER(name) = LOWER(%s) AND id != %s", 
                       (college_id, data['name'], program_id))
            if cur.fetchone():
                conn.close()
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
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        
        values.append(program_id)
        update_query = f"UPDATE programs SET {', '.join(updates)} WHERE id = %s RETURNING *"
        
        cur.execute(update_query, values)
        updated_program = cur.fetchone()
        conn.commit()
        conn.close()
        
        if not updated_program:
            return jsonify({"error": "Program not found"}), 404
        
        return jsonify({
            "status": "success",
            "message": "Program updated successfully",
            "program": updated_program
        })
    except Exception as e:
        print(f"❌ Error updating program: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/programs/<int:program_id>', methods=['DELETE'])
@login_required
@permission_required('manage_programs')
def delete_program(program_id):
    """Delete a program"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        cur.execute("DELETE FROM programs WHERE id = %s RETURNING id", (program_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Program not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Program deleted successfully"
        })
    except Exception as e:
        print(f"❌ Error deleting program: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= COLLEGE API FOR FRONTEND DROPDOWNS =================
@app.route('/api/colleges-dropdown', methods=['GET'])
def get_colleges_dropdown():
    """Get active colleges and their programs for frontend dropdowns"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, code, name, description, is_active, display_order
            FROM colleges 
            WHERE is_active = TRUE
            ORDER BY display_order, name
        """)
        colleges = cur.fetchall()
        
        cur.execute("""
            SELECT p.id, p.college_id, p.name, p.code, p.is_active, p.display_order
            FROM programs p
            JOIN colleges c ON p.college_id = c.id
            WHERE p.is_active = TRUE AND c.is_active = TRUE
            ORDER BY p.display_order, p.name
        """)
        programs = cur.fetchall()
        
        conn.close()
        
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
        
        for college in colleges:
            college['programs'] = programs_by_college.get(college['id'], [])
        
        return jsonify(colleges)
    except Exception as e:
        print(f"❌ Error getting colleges dropdown: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= GET STUDENT RECORDS (ONE PER STUDENT) =================
@app.route('/api/my-records', methods=['GET'])
@login_required
@role_required('STUDENT')
def get_my_records():
    """Get only ONE record of the currently logged-in student"""
    conn = get_db_connection()
    if not conn: 
        return jsonify({"records": [], "error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT * FROM records 
            WHERE user_id = %s 
            ORDER BY updated_at DESC
            LIMIT 1
        """, (session['user_id'],))
        
        rows = cur.fetchall()
        conn.close()
        
        for r in rows:
            if r['created_at']: 
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['updated_at']: 
                r['updated_at'] = r['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: 
                r['birthdate'] = str(r['birthdate'])
            if r['email_sent_at']: 
                r['email_sent_at'] = r['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            if r.get('goodmoral_analysis'):
                try:
                    if isinstance(r['goodmoral_analysis'], str):
                        r['goodmoral_analysis'] = json.loads(r['goodmoral_analysis'])
                    print(f"📊 Good Moral Analysis for student {r['id']}: {json.dumps(r['goodmoral_analysis'], indent=2)}")
                except:
                    r['goodmoral_analysis'] = {}
                    print(f"❌ Failed to parse goodmoral_analysis for student {r['id']}")
            
            if r.get('other_documents'):
                try:
                    if isinstance(r['other_documents'], str):
                        r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            # Parse document_status
            if r.get('document_status'):
                try:
                    if isinstance(r['document_status'], str):
                        r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            
            image_fields = ['image_path', 'form137_path', 'form138_path', 'goodmoral_path']
            for field in image_fields:
                if r.get(field):
                    paths = str(r[field]).split(',')
                    if paths and paths[0].strip():
                        first_path = paths[0].strip()
                        r[f'{field}_url'] = f"{request.host_url}uploads/{first_path}"
                    else:
                        r[f'{field}_url'] = None
                else:
                    r[f'{field}_url'] = None
        
        return jsonify({
            "records": rows,
            "server_url": request.host_url.rstrip('/'),
            "user_id": session['user_id']
        })
    except Exception as e:
        print(f"❌ Error in get_my_records: {e}")
        traceback.print_exc()
        if conn:
            conn.close()
        return jsonify({"records": [], "error": str(e)}), 500

# ================= STUDENT DOCUMENTS ENDPOINT =================
@app.route('/api/student/documents/<int:record_id>', methods=['GET'])
@login_required
@role_required('STUDENT')
def get_student_documents(record_id):
    """Get documents for a specific record (students can only access their own)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT * FROM records 
            WHERE id = %s AND user_id = %s
        """, (record_id, session['user_id']))
        
        record = cur.fetchone()
        
        if not record:
            conn.close()
            return jsonify({"error": "Record not found or access denied"}), 404
        
        documents = {
            "psa_documents": [],
            "form137_documents": [],
            "form138_documents": [],
            "goodmoral_documents": [],
            "other_documents": []
        }
        
        # Parse document status
        doc_status = {}
        if record.get('document_status'):
            try:
                if isinstance(record['document_status'], str):
                    doc_status = json.loads(record['document_status'])
                else:
                    doc_status = record['document_status']
            except:
                doc_status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        if record.get('image_path'):
            paths = record['image_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["psa_documents"].append({
                        "filename": path.strip(),
                        "download_url": f"{request.host_url}uploads/{path.strip()}"
                    })
        
        if record.get('form137_path'):
            paths = record['form137_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["form137_documents"].append({
                        "filename": path.strip(),
                        "download_url": f"{request.host_url}uploads/{path.strip()}"
                    })
        
        if record.get('form138_path'):
            paths = record['form138_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["form138_documents"].append({
                        "filename": path.strip(),
                        "download_url": f"{request.host_url}uploads/{path.strip()}"
                    })
        
        if record.get('goodmoral_path'):
            paths = record['goodmoral_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["goodmoral_documents"].append({
                        "filename": path.strip(),
                        "download_url": f"{request.host_url}uploads/{path.strip()}"
                    })
        
        if record.get('other_documents'):
            try:
                other_docs = json.loads(record['other_documents'])
                for doc in other_docs:
                    if doc.get('filename'):
                        documents["other_documents"].append({
                            "title": doc.get('title', 'Untitled'),
                            "filename": doc['filename'],
                            "download_url": f"{request.host_url}uploads/{doc['filename']}"
                        })
            except:
                pass
        
        conn.close()
        
        return jsonify({
            "documents": documents,
            "document_status": doc_status,
            "record_status": record.get('status', 'INCOMPLETE'),
            "rejection_reason": record.get('rejection_reason', ''),
            "record_id": record_id
        })
        
    except Exception as e:
        print(f"❌ Error in get_student_documents: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= GET ALL RECORDS (ONE PER USER) =================
@app.route('/get-records', methods=['GET'])
@login_required
def get_records():
    """Get records - Students see their own, Super Admin sees all (ONE PER USER)"""
    conn = get_db_connection()
    if not conn: 
        return jsonify({"records": [], "error": "Database connection failed"})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        user_role = session.get('role', '').upper()
        if user_role == 'STUDENT':
            cur.execute("""
                SELECT r.*, u.username, u.email as user_email, u.full_name as user_full_name
                FROM records r
                JOIN users u ON r.user_id = u.id
                WHERE r.user_id = %s 
                ORDER BY r.updated_at DESC
                LIMIT 1
            """, (session['user_id'],))
        elif user_role == 'SUPER_ADMIN':
            cur.execute("""
                SELECT DISTINCT ON (r.user_id) 
                       r.*, u.username, u.email as user_email, u.full_name as user_full_name
                FROM records r
                JOIN users u ON r.user_id = u.id
                WHERE u.role = 'STUDENT'
                ORDER BY r.user_id, r.updated_at DESC
            """)
        else:
            conn.close()
            return jsonify({"records": [], "error": "Unknown user role"})
        
        rows = cur.fetchall()
        conn.close()
        
        for r in rows:
            if r['created_at']: 
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['updated_at']: 
                r['updated_at'] = r['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: 
                r['birthdate'] = str(r['birthdate'])
            if r['email_sent_at']: 
                r['email_sent_at'] = r['email_sent_at'].strftime('%Y-%m-d %H:%M:%S')
            
            if r.get('goodmoral_analysis'):
                try:
                    if isinstance(r['goodmoral_analysis'], str):
                        r['goodmoral_analysis'] = json.loads(r['goodmoral_analysis'])
                    print(f"📊 Good Moral Analysis for record {r['id']}: {json.dumps(r['goodmoral_analysis'], indent=2)}")
                except Exception as e:
                    print(f"❌ Failed to parse goodmoral_analysis for record {r['id']}: {e}")
                    print(f"Raw value: {r['goodmoral_analysis']}")
                    r['goodmoral_analysis'] = {}
            else:
                print(f"⚠️ No goodmoral_analysis found for record {r['id']}")
            
            if r.get('other_documents'):
                try:
                    if isinstance(r['other_documents'], str):
                        r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            # Parse document_status
            if r.get('document_status'):
                try:
                    if isinstance(r['document_status'], str):
                        r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            
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
            "server_url": request.host_url.rstrip('/'),
            "user_role": user_role
        })
    except Exception as e:
        print(f"❌ Error in get-records: {e}")
        traceback.print_exc()
        if conn:
            conn.close()
        return jsonify({"records": [], "error": str(e)})

# ================= UPDATE RECORD STATUS (APPROVE/REJECT) =================
@app.route('/api/record/<int:record_id>/status', methods=['PUT'])
@login_required
@permission_required('edit_records')
def update_record_status(record_id):
    """Update record status (approve/reject) - Super Admin only"""
    try:
        data = request.json
        status = data.get('status')
        reason = data.get('reason', '')
        
        if status not in ['APPROVED', 'REJECTED', 'PENDING']:
            return jsonify({"error": "Invalid status"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        # Update status and optionally add rejection reason
        if status == 'REJECTED' and reason:
            cur.execute("""
                UPDATE records 
                SET status = %s, rejection_reason = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
                RETURNING id
            """, (status, reason, record_id))
        else:
            cur.execute("""
                UPDATE records 
                SET status = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
                RETURNING id
            """, (status, record_id))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        updated_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Optional: Send email notification about status change
        if status in ['APPROVED', 'REJECTED']:
            # You can add email notification here
            print(f"📧 Record {record_id} {status} - notification would be sent")
        
        return jsonify({
            "success": True,
            "message": f"Record {status.lower()} successfully",
            "record_id": updated_id,
            "status": status
        })
        
    except Exception as e:
        print(f"❌ Status update error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= SAVE RECORD ENDPOINT (UPSERT) =================
@app.route('/save-record', methods=['POST'])
@login_required
@permission_required('access_scanner')
def save_record():
    """Save or UPDATE record - Only ONE record per student (UPSERT)"""
    conn = None
    try:
        d = request.json
        print(f"📥 Saving/UPDATING record for user: {session['user_id']}")
        
        goodmoral_analysis = d.get('goodmoral_analysis')
        print(f"📊 Incoming goodmoral_analysis: {json.dumps(goodmoral_analysis, indent=2) if goodmoral_analysis else 'None'}")
        
        disciplinary_status = d.get('disciplinary_status')
        goodmoral_score = d.get('goodmoral_score')
        disciplinary_details = d.get('disciplinary_details')
        has_disciplinary_record = d.get('has_disciplinary_record', False)
        
        religion = d.get('religion', '')
        
        other_documents = d.get('other_documents')
        if other_documents and isinstance(other_documents, list):
            other_documents_json = json.dumps(other_documents)
        else:
            other_documents_json = None
        
        siblings_list = d.get('siblings', [])
        siblings_json = json.dumps(siblings_list)
        
        college = d.get('college', '')
        program = d.get('program', '')
        
        print(f"🎓 College selected: {college}")
        print(f"📚 Program selected: {program}")
        print(f"🙏 Religion selected: {religion}")
        
        conn = get_db_connection()
        if not conn: 
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM records WHERE user_id = %s", (session['user_id'],))
        existing_record = cur.fetchone()
        
        if existing_record:
            print(f"🔄 Updating existing record ID: {existing_record[0]} for user: {session['user_id']}")
            
            # Get current document status and paths
            cur.execute("SELECT document_status, image_path, form137_path, goodmoral_path, status FROM records WHERE id = %s", (existing_record[0],))
            current = cur.fetchone()
            current_status = {}
            if current and current[0]:
                try:
                    if isinstance(current[0], dict):
                        current_status = current[0]
                    else:
                        current_status = json.loads(current[0])
                except:
                    current_status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                current_status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            
            # Get current record status (don't change if already APPROVED/REJECTED)
            current_record_status = current[4] if current and len(current) > 4 else 'INCOMPLETE'
            
            # Update status based on new uploads (only if not APPROVED/REJECTED)
            if current_record_status not in ['APPROVED', 'REJECTED']:
                if d.get('psa_image_path') and d.get('psa_image_path') != current[1]:
                    current_status['psa'] = True
                if d.get('f137_image_path') and d.get('f137_image_path') != current[2]:
                    current_status['form137'] = True
                if d.get('goodmoral_image_path') and d.get('goodmoral_image_path') != current[3]:
                    current_status['goodmoral'] = True
            
            goodmoral_analysis_json = None
            if goodmoral_analysis:
                if isinstance(goodmoral_analysis, dict):
                    goodmoral_analysis_json = json.dumps(goodmoral_analysis)
                    print(f"✅ Converted goodmoral_analysis to JSON string")
                else:
                    goodmoral_analysis_json = goodmoral_analysis
                    print(f"⚠️ goodmoral_analysis is already a string")
            
            # Determine new overall status (only if not APPROVED/REJECTED)
            if current_record_status not in ['APPROVED', 'REJECTED']:
                all_docs = all([current_status.get('psa', False), current_status.get('form137', False), 
                               current_status.get('form138', False), current_status.get('goodmoral', False)])
                
                if all_docs:
                    overall_status = 'PENDING'
                else:
                    overall_status = 'INCOMPLETE'
            else:
                overall_status = current_record_status
            
            cur.execute('''
                UPDATE records SET
                    name = %s, sex = %s, birthdate = %s, birthplace = %s, 
                    birth_order = %s, religion = %s, age = %s,
                    mother_name = %s, mother_citizenship = %s, mother_occupation = %s, 
                    father_name = %s, father_citizenship = %s, father_occupation = %s, 
                    lrn = %s, school_name = %s, school_address = %s, final_general_average = %s,
                    image_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(image_path, ''), CASE WHEN image_path IS NOT NULL AND image_path != '' THEN ',' ELSE '' END, %s)
                             ELSE image_path
                        END, image_path),
                    form137_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(form137_path, ''), CASE WHEN form137_path IS NOT NULL AND form137_path != '' THEN ',' ELSE '' END, %s)
                             ELSE form137_path
                        END, form137_path),
                    goodmoral_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(goodmoral_path, ''), CASE WHEN goodmoral_path IS NOT NULL AND goodmoral_path != '' THEN ',' ELSE '' END, %s)
                             ELSE goodmoral_path
                        END, goodmoral_path),
                    email = %s, mobile_no = %s, civil_status = %s, nationality = %s,
                    mother_contact = %s, father_contact = %s,
                    guardian_name = %s, guardian_relation = %s, guardian_contact = %s,
                    region = %s, province = %s, specific_address = %s,
                    school_year = %s, student_type = %s, college = %s, program = %s, last_level_attended = %s,
                    is_ip = %s, is_pwd = %s, has_medication = %s, is_working = %s,
                    residence_type = %s, employer_name = %s, marital_status = %s,
                    is_gifted = %s, needs_assistance = %s, school_type = %s, year_attended = %s, 
                    special_talents = %s, is_scholar = %s, siblings = %s,
                    goodmoral_analysis = %s, disciplinary_status = %s, goodmoral_score = %s,
                    has_disciplinary_record = %s, disciplinary_details = %s,
                    other_documents = %s,
                    document_status = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                RETURNING id
            ''', (
                d.get('name'), d.get('sex'), d.get('birthdate') or None, d.get('birthplace'), 
                d.get('birth_order'), religion, d.get('age'),
                d.get('mother_name'), d.get('mother_citizenship'), d.get('mother_occupation'), 
                d.get('father_name'), d.get('father_citizenship'), d.get('father_occupation'), 
                d.get('lrn'), d.get('school_name'), d.get('school_address'), d.get('final_general_average'),
                # PSA image - append mode
                d.get('psa_image_path', ''), d.get('psa_image_path', ''), d.get('psa_image_path', ''),
                # Form137 image - append mode
                d.get('f137_image_path', ''), d.get('f137_image_path', ''), d.get('f137_image_path', ''),
                # Good Moral image - append mode
                d.get('goodmoral_image_path', ''), d.get('goodmoral_image_path', ''), d.get('goodmoral_image_path', ''),
                d.get('email'), d.get('mobile_no'), d.get('civil_status'), d.get('nationality'),
                d.get('mother_contact'), d.get('father_contact'),
                d.get('guardian_name'), d.get('guardian_relation'), d.get('guardian_contact'),
                d.get('region'), d.get('province'), d.get('specific_address'),
                d.get('school_year'), d.get('student_type'), college, program, d.get('last_level_attended'),
                d.get('is_ip'), d.get('is_pwd'), d.get('has_medication'), d.get('is_working'),
                d.get('residence_type'), d.get('employer_name'), d.get('marital_status'),
                d.get('is_gifted'), d.get('needs_assistance'), d.get('school_type'), 
                d.get('year_attended'), d.get('special_talents'), d.get('is_scholar'),
                siblings_json,
                goodmoral_analysis_json,
                disciplinary_status,
                goodmoral_score,
                has_disciplinary_record,
                disciplinary_details,
                other_documents_json,
                json.dumps(current_status),
                overall_status,
                session['user_id']
            ))
            
            updated_id = cur.fetchone()[0]
            conn.commit()
            conn.close()
            
            print(f"✅ Record UPDATED with ID: {updated_id}")
            print(f"👤 User ID: {session['user_id']}")
            print(f"🎓 College: {college}")
            print(f"📚 Program: {program}")
            print(f"🙏 Religion: {religion}")
            print(f"📊 Good Moral Score: {goodmoral_score} | Status: {disciplinary_status}")
            print(f"📊 Good Moral Analysis saved: {goodmoral_analysis_json is not None}")
            print(f"📄 Document Status: {current_status}")
            print(f"📋 Record Status: {overall_status}")
            
            if has_disciplinary_record:
                print(f"⚠️ Student has disciplinary record: {disciplinary_details}")
            
            return jsonify({
                "status": "success", 
                "db_id": updated_id,
                "college": college,
                "program": program,
                "religion": religion,
                "goodmoral_score": goodmoral_score,
                "disciplinary_status": disciplinary_status,
                "has_disciplinary_record": has_disciplinary_record,
                "document_status": current_status,
                "record_status": overall_status,
                "message": "Record UPDATED successfully.",
                "operation": "update"
            })
            
        else:
            print(f"🆕 Creating NEW record for user: {session['user_id']}")
            
            # Initialize document status
            doc_status = {
                "psa": bool(d.get('psa_image_path')),
                "form137": bool(d.get('f137_image_path')),
                "form138": False,
                "goodmoral": bool(d.get('goodmoral_image_path'))
            }
            
            # Determine initial status
            all_docs = all([doc_status.get('psa', False), doc_status.get('form137', False), 
                           doc_status.get('form138', False), doc_status.get('goodmoral', False)])
            
            if all_docs:
                initial_status = 'PENDING'
            else:
                initial_status = 'INCOMPLETE'
            
            goodmoral_analysis_json = None
            if goodmoral_analysis:
                if isinstance(goodmoral_analysis, dict):
                    goodmoral_analysis_json = json.dumps(goodmoral_analysis)
                    print(f"✅ Converted goodmoral_analysis to JSON string")
                else:
                    goodmoral_analysis_json = goodmoral_analysis
                    print(f"⚠️ goodmoral_analysis is already a string")
            
            cur.execute('''
                INSERT INTO records (
                    user_id, name, sex, birthdate, birthplace, birth_order, religion, age,
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
                    goodmoral_analysis, disciplinary_status, goodmoral_score,
                    has_disciplinary_record, disciplinary_details,
                    other_documents,
                    document_status,
                    status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
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
                    %s, %s, %s,
                    %s, %s,
                    %s,
                    %s,
                    %s
                ) 
                RETURNING id
            ''', (
                session['user_id'],
                d.get('name'), d.get('sex'), d.get('birthdate') or None, d.get('birthplace'), 
                d.get('birth_order'), religion, d.get('age'),
                d.get('mother_name'), d.get('mother_citizenship'), d.get('mother_occupation'), 
                d.get('father_name'), d.get('father_citizenship'), d.get('father_occupation'), 
                d.get('lrn'), d.get('school_name'), d.get('school_address'), d.get('final_general_average'),
                d.get('psa_image_path', ''), d.get('f137_image_path', ''), d.get('goodmoral_image_path', ''), 
                d.get('email'), d.get('mobile_no'), d.get('civil_status'), d.get('nationality'),
                d.get('mother_contact'), d.get('father_contact'),
                d.get('guardian_name'), d.get('guardian_relation'), d.get('guardian_contact'),
                d.get('region'), d.get('province'), d.get('specific_address'),
                d.get('school_year'), d.get('student_type'), college, program, d.get('last_level_attended'),
                d.get('is_ip'), d.get('is_pwd'), d.get('has_medication'), d.get('is_working'),
                d.get('residence_type'), d.get('employer_name'), d.get('marital_status'),
                d.get('is_gifted'), d.get('needs_assistance'), d.get('school_type'), 
                d.get('year_attended'), d.get('special_talents'), d.get('is_scholar'),
                siblings_json,
                goodmoral_analysis_json,
                disciplinary_status,
                goodmoral_score,
                has_disciplinary_record,
                disciplinary_details,
                other_documents_json,
                json.dumps(doc_status),
                initial_status
            ))
            
            new_id = cur.fetchone()[0]
            conn.commit()
            conn.close()

            print(f"✅ Record CREATED with ID: {new_id}")
            print(f"👤 User ID: {session['user_id']}")
            print(f"🎓 College: {college}")
            print(f"📚 Program: {program}")
            print(f"🙏 Religion: {religion}")
            print(f"📊 Good Moral Score: {goodmoral_score} | Status: {disciplinary_status}")
            print(f"📊 Good Moral Analysis saved: {goodmoral_analysis_json is not None}")
            print(f"📄 Document Status: {doc_status}")
            print(f"📋 Record Status: {initial_status}")
            
            if has_disciplinary_record:
                print(f"⚠️ Student has disciplinary record: {disciplinary_details}")

            return jsonify({
                "status": "success", 
                "db_id": new_id,
                "college": college,
                "program": program,
                "religion": religion,
                "goodmoral_score": goodmoral_score,
                "disciplinary_status": disciplinary_status,
                "has_disciplinary_record": has_disciplinary_record,
                "document_status": doc_status,
                "record_status": initial_status,
                "message": "Record CREATED successfully.",
                "operation": "create"
            })
            
    except Exception as e:
        print(f"❌ SAVE ERROR: {e}")
        traceback.print_exc()
        if conn: 
            conn.rollback()
            conn.close()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

# ================= ROUTES WITH ROLE-BASED ACCESS =================
@app.route('/')
def index():
    """Main page - redirect based on role"""
    print(f"🔍 Root route accessed. Session: {dict(session)}")
    
    if 'user_id' not in session:
        print("🔍 No user_id in session, redirecting to login")
        return redirect('/login')
    
    user_role = session.get('role')
    print(f"🔍 User role from session: {user_role}")
    
    if not user_role:
        print("🔍 No role in session, redirecting to login")
        session.clear()
        return redirect('/login')
    
    user_role = user_role.upper()
    
    if user_role == 'STUDENT':
        print("🔍 User is STUDENT, serving index.html")
        return render_template('index.html')
    elif user_role == 'SUPER_ADMIN':
        print("🔍 User is SUPER_ADMIN, redirecting to admin dashboard")
        return redirect('/admin/dashboard')
    else:
        print(f"🔍 Unknown role: {user_role}, redirecting to login")
        session.clear()
        return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    print(f"🔍 Login route accessed. Session: {dict(session)}")
    
    if request.method == 'GET':
        if 'user_id' in session and 'role' in session:
            print(f"🔍 User already has session: user_id={session['user_id']}, role={session['role']}")
            
            user_role = session['role'].upper()
            if user_role == 'STUDENT':
                print("🔍 Redirecting STUDENT to /")
                return redirect('/')
            elif user_role == 'SUPER_ADMIN':
                print("🔍 Redirecting SUPER_ADMIN to /admin/dashboard")
                return redirect('/admin/dashboard')
            else:
                print(f"🔍 Unknown role: {user_role}, clearing session")
                session.clear()
        
        print("🔍 Showing login page")
        return render_template('login.html')
    
    elif request.method == 'POST':
        print("🔍 POST to login form, redirecting to API")
        return redirect('/api/login')

@app.route('/admin/dashboard')
def admin_dashboard():
    """Admin dashboard - Super Admin only"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        print(f"❌ Access denied: User role is {user_role}, expected SUPER_ADMIN")
        return redirect('/')
    
    return render_template('admin_dashboard.html')

@app.route('/admin/users')
def admin_users():
    """User management page - Super Admin only"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        print(f"❌ Access denied: User role is {user_role}, expected SUPER_ADMIN")
        return redirect('/')
    
    return render_template('admin_users.html')

@app.route('/history.html')
def history_page():
    """Records history page"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        print(f"❌ Access denied: User role is {user_role}, expected SUPER_ADMIN")
        return redirect('/')
    
    return render_template('history.html')

@app.route('/admin/colleges')
def admin_colleges():
    """Admin page for managing colleges and programs"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        print(f"❌ Access denied: User role is {user_role}, expected SUPER_ADMIN")
        return redirect('/')
    
    return render_template('admin_colleges.html')

# ================= ROUTES FOR STUDENTS =================
@app.route('/my-records')
@login_required
def my_records_page():
    """Page for students to view their own records"""
    user_role = session.get('role', '').upper()
    if user_role != 'STUDENT':
        return redirect('/')
    
    return render_template('student_records.html')

# ================= DEBUG ENDPOINT FOR GOOD MORAL =================
@app.route('/debug-goodmoral/<int:record_id>', methods=['GET'])
@login_required
def debug_goodmoral(record_id):
    """Debug endpoint to check raw goodmoral_analysis"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB Connection failed"}), 500
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT goodmoral_analysis FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        conn.close()
        
        if not result:
            return jsonify({"error": "Record not found"}), 404
        
        raw_value = result[0]
        
        parsed = None
        parse_error = None
        if raw_value:
            try:
                if isinstance(raw_value, dict):
                    parsed = raw_value
                else:
                    parsed = json.loads(raw_value)
            except Exception as e:
                parse_error = str(e)
        
        return jsonify({
            "record_id": record_id,
            "raw_value": raw_value,
            "type": str(type(raw_value)),
            "is_null": raw_value is None,
            "length": len(str(raw_value)) if raw_value else 0,
            "parsed": parsed,
            "parse_error": parse_error
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= FIXED GOOD MORAL SCANNING ENDPOINT =================
@app.route('/scan-goodmoral', methods=['POST'])
@login_required
@permission_required('access_scanner')
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
        
        # SIMPLIFIED PROMPT
        prompt = """You are an expert at reading Philippine school documents. Extract information from this Good Moral Certificate.

IMPORTANT: Look for these specific details:
- School name (usually at the top or bottom of the document)
- Issuing officer name (person who signed, like Registrar, Principal, etc.)
- Date when certificate was issued
- Student name
- Whether there are any disciplinary records mentioned

Return ONLY this exact JSON format with no other text:
{
  "issuing_school": "full school name or 'Not Found'",
  "issuing_officer": "name of person who signed or 'Not Found'",
  "issued_date": "YYYY-MM-DD format or 'Not Found'",
  "student_name": "full student name or 'Not Found'",
  "has_disciplinary_record": false,
  "disciplinary_details": "any details about disciplinary records or ''",
  "remarks": "any other remarks or ''"
}"""
        
        try:
            response_text = extract_with_gemini(prompt, pil_images)
            print(f"✅ Gemini Response received: {len(response_text)} characters")
            print(f"📝 RAW RESPONSE FROM GEMINI: {response_text}")
            
            cleaned_text = response_text.strip()
            
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                if lines[0].startswith('```'):
                    cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
            
            start = cleaned_text.find('{')
            end = cleaned_text.rfind('}') + 1
            
            if start == -1 or end == 0:
                print(f"❌ Could not find JSON in response")
                return jsonify({"error": "Invalid JSON response from AI"}), 500
                
            json_str = cleaned_text[start:end]
            print(f"📝 EXTRACTED JSON STRING: {json_str}")
            
            try:
                analysis_data = json.loads(json_str)
                print(f"📊 PARSED ANALYSIS DATA: {json.dumps(analysis_data, indent=2)}")
                
                # Manual extraction fallbacks
                if analysis_data.get('issuing_school') == 'Not Found' and 'STI College' in response_text:
                    import re
                    sti_match = re.search(r'STI College[^\n]*', response_text)
                    if sti_match:
                        analysis_data['issuing_school'] = sti_match.group(0).strip()
                        print(f"✅ Manually extracted issuing_school: {analysis_data['issuing_school']}")
                
                if analysis_data.get('issuing_officer') == 'Not Found':
                    import re
                    # Look for name patterns (e.g., "CAMILLE ANN A. TEMPROSA")
                    name_pattern = r'[A-Z][A-Z\s]+(?:[A-Z]\.)?\s*[A-Z][A-Z]+'
                    name_matches = re.findall(name_pattern, response_text)
                    if name_matches:
                        # Filter out common non-name strings
                        valid_names = [n for n in name_matches if len(n) > 5 and not n.startswith('STI')]
                        if valid_names:
                            analysis_data['issuing_officer'] = valid_names[-1].strip()
                            print(f"✅ Manually extracted issuing_officer: {analysis_data['issuing_officer']}")
                
                if analysis_data.get('issued_date') == 'Not Found':
                    import re
                    date_patterns = [
                        r'(\d{4}-\d{2}-\d{2})',
                        r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',
                        r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',
                        r'(March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})'
                    ]
                    
                    for pattern in date_patterns:
                        date_match = re.search(pattern, response_text, re.IGNORECASE)
                        if date_match:
                            analysis_data['issued_date'] = date_match.group(0)
                            print(f"✅ Manually extracted issued_date: {analysis_data['issued_date']}")
                            break
                
                # Ensure all fields have values
                analysis_data['student_name'] = analysis_data.get('student_name', 'Not Found')
                analysis_data['issuing_school'] = analysis_data.get('issuing_school', 'Not Found')
                analysis_data['issuing_officer'] = analysis_data.get('issuing_officer', 'Not Found')
                analysis_data['issued_date'] = analysis_data.get('issued_date', 'Not Found')
                analysis_data['has_disciplinary_record'] = analysis_data.get('has_disciplinary_record', False)
                analysis_data['disciplinary_details'] = analysis_data.get('disciplinary_details', '')
                analysis_data['remarks'] = analysis_data.get('remarks', '')
                
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
                print(f"❌ Invalid JSON string: {json_str}")
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

# ================= PSA EXTRACTION ENDPOINT =================
@app.route('/extract', methods=['POST'])
@login_required
@permission_required('access_scanner')
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
            "Mother_MaidenName": "Mother's Maiden Name",
            "Mother_Citizenship": "Citizenship",
            "Mother_Occupation": "Occupation if stated",
            "Father_Name": "Father's Full Name",
            "Father_Citizenship": "Citizenship",
            "Father_Occupation": "Occupation if stated"
        }
        
        IMPORTANT: DO NOT extract Religion field. Religion is selected separately in the system.
        
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

# ================= UPLOAD OTHER DOCUMENTS ENDPOINT =================
@app.route('/upload-other-document/<int:record_id>', methods=['POST'])
@login_required
@permission_required('access_scanner')
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
    
    user_role = session.get('role', '').upper()
    if user_role == 'STUDENT':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
        if not record or record[0] != session['user_id']:
            return jsonify({"error": "Unauthorized access to record"}), 403
    
    try:
        timestamp = int(datetime.now().timestamp())
        filename = secure_filename(f"OTHER_{record_id}_{timestamp}_{file.filename}")
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("SELECT other_documents FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        existing_documents = []
        if result and result[0]:
            try:
                existing_documents = json.loads(result[0])
            except:
                existing_documents = []
        
        new_document = {
            'id': len(existing_documents) + 1,
            'title': title,
            'filename': filename,
            'uploaded_at': datetime.now().isoformat()
        }
        
        existing_documents.append(new_document)
        new_documents_json = json.dumps(existing_documents)
        
        cur.execute("UPDATE records SET other_documents = %s WHERE id = %s", 
                   (new_documents_json, record_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Document uploaded successfully",
            "document": new_document,
            "download_url": f"{request.host_url}uploads/{filename}"
        })
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= DELETE OTHER DOCUMENT ENDPOINT =================
@app.route('/delete-other-document/<int:record_id>/<int:doc_id>', methods=['DELETE'])
@login_required
@permission_required('access_scanner')
def delete_other_document(record_id, doc_id):
    """Delete an other document"""
    user_role = session.get('role', '').upper()
    if user_role == 'STUDENT':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
        if not record or record[0] != session['user_id']:
            return jsonify({"error": "Unauthorized access to record"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("SELECT other_documents FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        if not result or not result[0]:
            conn.close()
            return jsonify({"error": "No documents found"}), 404
        
        existing_documents = json.loads(result[0])
        
        document_to_delete = None
        updated_documents = []
        
        for doc in existing_documents:
            if doc.get('id') == doc_id:
                document_to_delete = doc
            else:
                updated_documents.append(doc)
        
        if not document_to_delete:
            conn.close()
            return jsonify({"error": "Document not found"}), 404
        
        filename = document_to_delete.get('filename')
        if filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(file_path):
                os.remove(file_path)
        
        updated_documents_json = json.dumps(updated_documents)
        cur.execute("UPDATE records SET other_documents = %s WHERE id = %s", 
                   (updated_documents_json, record_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Document deleted successfully"
        })
    except Exception as e:
        print(f"❌ Delete error: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= FORM 137 ENDPOINT =================
@app.route('/extract-form137', methods=['POST'])
@login_required
@permission_required('access_scanner')
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
@permission_required('send_emails')
def send_email_only(record_id):
    """Send email for a saved record"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT name, email, email_sent, religion,
                   goodmoral_score, disciplinary_status, disciplinary_details,
                   lrn, sex, birthdate, birthplace, age, 
                   civil_status, nationality,
                   mother_name, mother_citizenship, mother_contact,
                   father_name, father_citizenship, father_contact,
                   province, specific_address, mobile_no,
                   school_name, school_address, final_general_average,
                   last_level_attended, student_type, college, program,
                   school_year, is_ip, is_pwd, has_medication,
                   special_talents, document_status, status, rejection_reason
            FROM records WHERE id = %s
        """, (record_id,))
        
        record = cur.fetchone()
        
        if not record:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        if record.get('email_sent'):
            conn.close()
            return jsonify({"warning": "Email has already been sent"}), 400
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            conn.close()
            return jsonify({"error": "No email address found"}), 400
        
        print(f"\n📧 Sending email for record ID: {record_id}")
        print(f"🎓 College: {record.get('college', 'N/A')}")
        print(f"📚 Program: {record.get('program', 'N/A')}")
        print(f"🙏 Religion: {record.get('religion', 'N/A')}")
        print(f"📄 Document Status: {record.get('document_status', {})}")
        print(f"📋 Record Status: {record.get('status', 'INCOMPLETE')}")
        
        student_data = dict(record)
        email_sent = send_email_notification(email_addr, student_name, [], student_data)
        
        if email_sent:
            cur.execute("""
                UPDATE records 
                SET email_sent = TRUE, email_sent_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (record_id,))
            conn.commit()
            conn.close()
            
            print(f"✅ Email sent for ID: {record_id}")
            return jsonify({
                "status": "success",
                "message": f"Email sent successfully to {email_addr}",
                "record_id": record_id
            })
        else:
            conn.close()
            return jsonify({
                "status": "error",
                "error": "Failed to send email."
            }), 500
    except Exception as e:
        print(f"❌ EMAIL SEND ERROR: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

@app.route('/resend-email/<int:record_id>', methods=['POST'])
@login_required
@permission_required('send_emails')
def resend_email(record_id):
    """Resend email even if already sent"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "DB Connection Failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT name, email, religion,
                   goodmoral_score, disciplinary_status, disciplinary_details,
                   lrn, sex, birthdate, birthplace, age, 
                   civil_status, nationality,
                   mother_name, mother_citizenship, mother_contact,
                   father_name, father_citizenship, father_contact,
                   province, specific_address, mobile_no,
                   school_name, school_address, final_general_average,
                   last_level_attended, student_type, college, program,
                   school_year, is_ip, is_pwd, has_medication,
                   special_talents, document_status, status
            FROM records WHERE id = %s
        """, (record_id,))
        
        record = cur.fetchone()
        
        if not record:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        email_addr = record['email']
        student_name = record['name']
        
        if not email_addr:
            conn.close()
            return jsonify({"error": "No email address found"}), 400
        
        print(f"\n📧 Resending email for ID: {record_id}")
        print(f"🎓 College: {record.get('college', 'N/A')}")
        print(f"📚 Program: {record.get('program', 'N/A')}")
        print(f"🙏 Religion: {record.get('religion', 'N/A')}")
        
        student_data = dict(record)
        email_sent = send_email_notification(email_addr, student_name, [], student_data)
        
        if email_sent:
            cur.execute("""
                UPDATE records 
                SET email_sent_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (record_id,))
            conn.commit()
            conn.close()
            
            print(f"✅ Email resent for ID: {record_id}")
            return jsonify({
                "status": "success",
                "message": f"Email resent successfully to {email_addr}",
                "record_id": record_id
            })
        else:
            conn.close()
            return jsonify({
                "status": "error",
                "error": "Failed to send email."
            }), 500
    except Exception as e:
        print(f"❌ EMAIL RESEND ERROR: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

# ================= OTHER ENDPOINTS =================
@app.route('/uploads/<path:filename>')
@login_required
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
    """View printable form"""
    user_role = session.get('role', '').upper()
    if user_role == 'STUDENT':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
        if not record or record[0] != session['user_id']:
            return "Unauthorized access", 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
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
                
            if record.get('goodmoral_analysis'):
                try:
                    if isinstance(record['goodmoral_analysis'], str):
                        record['goodmoral_analysis'] = json.loads(record['goodmoral_analysis'])
                except:
                    record['goodmoral_analysis'] = {}
            
            if record.get('other_documents'):
                try:
                    if isinstance(record['other_documents'], str):
                        record['other_documents'] = json.loads(record['other_documents'])
                except:
                    record['other_documents'] = []
            else:
                record['other_documents'] = []
            
            # Parse document status
            if record.get('document_status'):
                try:
                    if isinstance(record['document_status'], str):
                        record['document_status'] = json.loads(record['document_status'])
                except:
                    record['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                record['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}

            return render_template('print_form.html', r=record)
        else:
            return "Record not found", 404
    except Exception as e:
        return f"Error loading form: {str(e)}", 500

@app.route('/upload-additional', methods=['POST'])
@login_required
@permission_required('access_scanner')
def upload_additional():
    files = request.files.getlist('files')
    rid, dtype = request.form.get('id'), request.form.get('type')
    
    if not files or not rid: 
        return jsonify({"error": "Data Missing"}), 400
    
    user_role = session.get('role', '').upper()
    if user_role == 'STUDENT':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM records WHERE id = %s", (rid,))
        record = cur.fetchone()
        conn.close()
        
        if not record or record[0] != session['user_id']:
            return jsonify({"error": "Unauthorized access to record"}), 403
    
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
        
        # Update document status
        doc_type_map = {'form137': 'form137', 'form138': 'form138', 'goodmoral': 'goodmoral'}
        update_document_status(rid, doc_type_map[dtype], True)
        
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "File uploaded successfully"})
    except Exception as e:
        print(f"❌ Upload error: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/delete-record/<int:record_id>', methods=['DELETE'])
@login_required
@permission_required('delete_records')
def delete_record(record_id):
    """Delete record (Super Admin only)"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/check-email-status/<int:record_id>', methods=['GET'])
@login_required
def check_email_status(record_id):
    """Check if email has been sent for a record"""
    user_role = session.get('role', '').upper()
    if user_role == 'STUDENT':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
        if not record or record[0] != session['user_id']:
            return jsonify({"error": "Unauthorized access to record"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT email_sent, email_sent_at FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        conn.close()
        
        if record:
            email_sent_at = record['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S') if record['email_sent_at'] else None
            return jsonify({
                "email_sent": record['email_sent'],
                "email_sent_at": email_sent_at
            })
        else:
            return jsonify({"error": "Record not found"}), 404
    except Exception as e:
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= HEALTH AND DIAGNOSTIC ENDPOINTS =================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "AssiScan Backend",
        "goodmoral_scanning": "ENABLED",
        "model": "Gemini 2.5 Flash",
        "user_management": "ENABLED",
        "roles": ["SUPER_ADMIN", "STUDENT"],
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if get_db_connection() else "disconnected",
        "features": {
            "password_reset": "ENABLED",
            "change_password": "ENABLED",
            "college_management": "ENABLED",
            "goodmoral_analysis": "ENABLED",
            "religion_dropdown": "ENABLED",
            "student_records": "ENABLED",
            "document_access": "ENABLED",
            "one_record_per_user": "ENABLED",
            "school_year_management": "ENABLED",
            "tofollow_documents": "ENABLED",
            "approve_reject": "ENABLED"
        }
    })

@app.route('/list-uploads', methods=['GET'])
@login_required
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

# ================= SIMPLE SESSION CHECK =================
@app.route('/check-login', methods=['GET'])
def check_login():
    """Simple endpoint to check if user is logged in"""
    print(f"🔍 /check-login accessed. Session: {dict(session)}")
    
    if 'user_id' in session and 'role' in session:
        return jsonify({
            "logged_in": True,
            "username": session.get('username'),
            "role": session.get('role'),
            "full_name": session.get('full_name'),
            "requires_password_reset": session.get('requires_password_reset', False)
        })
    else:
        return jsonify({"logged_in": False})

# ================= STUDENT RECORDS API =================
@app.route('/api/record/<int:record_id>', methods=['GET'])
@login_required
def get_single_record(record_id):
    """Get a single record by ID"""
    conn = get_db_connection()
    if not conn: 
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        user_role = session.get('role', '').upper()
        
        if user_role == 'STUDENT':
            cur.execute("""
                SELECT * FROM records 
                WHERE id = %s AND user_id = %s
            """, (record_id, session['user_id']))
        elif user_role == 'SUPER_ADMIN':
            cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        else:
            conn.close()
            return jsonify({"error": "Unauthorized"}), 403
        
        record = cur.fetchone()
        conn.close()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        if record['created_at']: 
            record['created_at'] = record['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if record['updated_at']: 
            record['updated_at'] = record['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
        if record['birthdate']: 
            record['birthdate'] = str(record['birthdate'])
        if record['email_sent_at']: 
            record['email_sent_at'] = record['email_sent_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        if record.get('goodmoral_analysis'):
            try:
                if isinstance(record['goodmoral_analysis'], str):
                    record['goodmoral_analysis'] = json.loads(record['goodmoral_analysis'])
                print(f"📊 Good Moral Analysis for record {record['id']}: {json.dumps(record['goodmoral_analysis'], indent=2)}")
            except:
                record['goodmoral_analysis'] = {}
        
        if record.get('other_documents'):
            try:
                if isinstance(record['other_documents'], str):
                    record['other_documents'] = json.loads(record['other_documents'])
            except:
                record['other_documents'] = []
        else:
            record['other_documents'] = []
        
        # Parse document_status
        if record.get('document_status'):
            try:
                if isinstance(record['document_status'], str):
                    record['document_status'] = json.loads(record['document_status'])
            except:
                record['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        else:
            record['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        image_fields = ['image_path', 'form137_path', 'form138_path', 'goodmoral_path']
        for field in image_fields:
            if record.get(field):
                paths = str(record[field]).split(',')
                if paths and paths[0].strip():
                    first_path = paths[0].strip()
                    record[f'{field}_url'] = f"{request.host_url}uploads/{first_path}"
                else:
                    record[f'{field}_url'] = None
            else:
                record[f'{field}_url'] = None
        
        return jsonify({"record": record})
    except Exception as e:
        print(f"❌ Error in get_single_record: {e}")
        if conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= APPLICATION START =================
if __name__ == '__main__':
    # Get port from environment variable (Render sets this automatically)
    port = int(os.environ.get("PORT", 10000))
    
    # Get host - VERY IMPORTANT: Use 0.0.0.0 for Render
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Debug mode - set to False in production
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    
    print("\n" + "="*60)
    print("🚀 ASSISCAN WITH COMPLETE FEATURES")
    print("="*60)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"🤖 Model: gemini-2.5-flash")
    print(f"📧 SendGrid: {'✅ SET' if SENDGRID_API_KEY else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print("="*60)
    print("👥 USER ROLES:")
    print("   • SUPER_ADMIN: Full system access")
    print("   • STUDENT: Scanner access + View own records + Download documents")
    print("="*60)
    print("✅ FIXED FEATURES:")
    print("   • Database tables will be recreated on startup")
    print("   • ONE RECORD PER USER enforced")
    print("   • Foreign key constraints properly set")
    print("   • Default admin user created")
    print("="*60)
    print("🔐 SECURITY FEATURES:")
    print("   • Role-based access control")
    print("   • Students can only access their own records")
    print("   • Document access permissions")
    print("="*60)
    print("🔄 DATABASE STATUS:")
    print("   • Checking table existence...")
    
    if not check_tables_exist():
        print("   ⚠️ Tables missing, initializing database...")
        if init_db():
            print("   ✅ Database initialized successfully!")
        else:
            print("   ❌ Database initialization failed!")
    else:
        print("   ✅ All tables exist")
    
    if os.path.exists(UPLOAD_FOLDER):
        file_count = len([f for f in os.listdir(UPLOAD_FOLDER) if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))])
        print(f"📊 Uploads folder contains {file_count} files")
    
    print("="*60)
    print("🔍 DEBUGGING FEATURES:")
    print("   • /debug-goodmoral/<id> - Check raw database values")
    print("   • Enhanced logging for Good Moral extraction")
    print("   • Manual extraction fallbacks")
    print("="*60)
    print("📅 SCHOOL YEAR MANAGEMENT:")
    print("   • /api/settings/school-year - GET/POST school year")
    print("   • Auto-updates in scanner interface")
    print("   • Persistent storage in JSON file")
    print("="*60)
    print("📄 TOFOLLOW DOCUMENTS:")
    print("   • Append new uploads to existing record")
    print("   • Track document status (PSA, Form137, GoodMoral)")
    print("   • Automatic status updates (INCOMPLETE/PENDING)")
    print("="*60)
    print("✅ APPROVE/REJECT SYSTEM:")
    print("   • /api/record/<id>/status - PUT endpoint")
    print("   • Status: INCOMPLETE → PENDING → APPROVED/REJECTED")
    print("   • Rejection reason storage")
    print("="*60)
    print(f"🌐 Server starting on {host}:{port}")
    print(f"⚙️ Debug mode: {debug}")
    print("="*60)
    
    # Force Flask to bind to all interfaces
    app.run(host=host, port=port, debug=debug)
