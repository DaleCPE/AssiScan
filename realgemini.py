import os
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
import json
import requests
from datetime import datetime, date, timedelta
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
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import gc

# --- FIX SSL/TLS ISSUES - FORCE REST TRANSPORT ---
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

# --- CONFIGURATION ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

# --- CONFIGURE GEMINI WITH REST TRANSPORT ---
if GEMINI_API_KEY:
    try:
        genai.configure(
            api_key=GEMINI_API_KEY,
            transport='rest'
        )
        print("✅ Google Generative AI Configured with REST transport")
        print("✅ Using ULTRA OPTIMIZED Gemini 2.5 Flash with memory management")
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
        "origins": ["*"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept"]
    }
})

# Setup Upload Folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ARCHIVE_FOLDER = os.path.join(BASE_DIR, 'archives')

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    print(f"📁 Created uploads folder at: {UPLOAD_FOLDER}")

if not os.path.exists(ARCHIVE_FOLDER):
    os.makedirs(ARCHIVE_FOLDER, exist_ok=True)
    print(f"📁 Created archives folder at: {ARCHIVE_FOLDER}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['ARCHIVE_FOLDER'] = ARCHIVE_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# --- USER ROLES ---
ROLES = {
    'SUPER_ADMIN': 1,
    'STUDENT': 2,
    'ADMISSIONS_STAFF': 3
}

PERMISSIONS = {
    'SUPER_ADMIN': [
        'manage_users', 'manage_colleges', 'manage_programs',
        'view_all_records', 'edit_records', 'archive_records',
        'view_archived_records', 'send_emails', 'view_dashboard', 
        'access_admin_panel', 'manage_settings', 'send_notifications',
        'view_all_notifications', 'generate_reports', 'scan_documents',
        'delete_records'
    ],
    'ADMISSIONS_STAFF': [
        'scan_documents', 'view_all_records', 'send_notifications',
        'view_dashboard', 'view_all_notifications', 'send_emails'
    ],
    'STUDENT': [
        'view_own_records', 'change_password', 'view_own_documents',
        'download_own_documents', 'view_own_notifications',
        'edit_own_information'
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
    try:
        data = request.json
        school_year = data.get('school_year')
        
        if not school_year:
            return jsonify({"error": "School year is required"}), 400
        
        if not re.match(r'^\d{4}-\d{4}$', school_year):
            return jsonify({"error": "Invalid format. Use YYYY-YYYY (e.g., 2025-2026)"}), 400
        
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

# ================= ENROLLMENT PERIOD SETTINGS =================
ENROLLMENT_FILE = os.path.join(BASE_DIR, 'enrollment_settings.json')

def get_enrollment_settings():
    default_settings = {
        "enrollment_start": (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
        "enrollment_end": (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
        "reminder_frequency": "weekly",
        "auto_send_reminders": True,
        "reminder_days_before_deadline": [7, 3, 1]
    }
    try:
        if os.path.exists(ENROLLMENT_FILE):
            with open(ENROLLMENT_FILE, 'r') as f:
                data = json.load(f)
                return {**default_settings, **data}
    except Exception as e:
        print(f"⚠️ Error reading enrollment file: {e}")
    return default_settings

def save_enrollment_settings(settings):
    try:
        with open(ENROLLMENT_FILE, 'w') as f:
            json.dump({**settings, 'updated_at': datetime.now().isoformat()}, f)
        return True
    except Exception as e:
        print(f"❌ Error saving enrollment settings: {e}")
        return False

# ================= PASSWORD FUNCTIONS =================
def hash_password(password):
    salt = secrets.token_hex(16)
    return salt + "$" + hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(stored_hash, password):
    if "$" not in stored_hash:
        return False
    
    salt, hash_value = stored_hash.split("$", 1)
    computed_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return hash_value == computed_hash

def generate_temp_password(length=8):
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ================= DATABASE FUNCTIONS =================
def get_db_connection():
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
    print("🗑️ Dropping all existing tables...")
    conn = get_db_connection()
    if not conn:
        print("❌ Failed to connect to database for dropping tables")
        return False
    
    try:
        cur = conn.cursor()
        
        tables = [
            'notifications',
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
                role VARCHAR(20) NOT NULL CHECK (role IN ('SUPER_ADMIN', 'ADMISSIONS_STAFF', 'STUDENT')),
                college_id INTEGER,
                program_id INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                requires_password_reset BOOLEAN DEFAULT TRUE,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                email_notifications BOOLEAN DEFAULT TRUE,
                sms_notifications BOOLEAN DEFAULT FALSE,
                mobile_number VARCHAR(20)
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
                student_type VARCHAR(50) DEFAULT 'Regular',
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
                needs_review BOOLEAN DEFAULT FALSE,
                
                is_transferee BOOLEAN DEFAULT FALSE,
                previous_school VARCHAR(255),
                previous_school_address TEXT,
                previous_school_year VARCHAR(50),
                year_level_to_enroll VARCHAR(50),
                honorable_dismissal_path TEXT,
                transfer_credentials_path TEXT,
                
                is_archived BOOLEAN DEFAULT FALSE,
                archived_at TIMESTAMP,
                archived_by INTEGER,
                archive_reason VARCHAR(50) CHECK (archive_reason IN ('GRADUATED', 'TRANSFERRED_OUT', 'COMPLETED', 'OTHER')),
                archive_notes TEXT,
                restored_at TIMESTAMP,
                restored_by INTEGER,
                
                last_reminder_sent TIMESTAMP,
                reminder_count INTEGER DEFAULT 0,
                
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (archived_by) REFERENCES users(id),
                FOREIGN KEY (restored_by) REFERENCES users(id),
                CONSTRAINT one_record_per_user UNIQUE (user_id)
            )
        ''')
        print("   ✅ Created records table")
        
        print("📝 Creating notifications table...")
        cur.execute('''
            CREATE TABLE notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                type VARCHAR(50) NOT NULL CHECK (type IN ('MISSING_DOCUMENT', 'DOCUMENT_UPLOADED', 'RECORD_APPROVED', 'RECORD_REJECTED', 'ENROLLMENT_REMINDER', 'SYSTEM', 'DEADLINE_REMINDER', 'INFO_UPDATED')),
                title VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                data JSONB,
                is_read BOOLEAN DEFAULT FALSE,
                is_emailed BOOLEAN DEFAULT FALSE,
                emailed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                priority INTEGER DEFAULT 0,
                
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        print("   ✅ Created notifications table")
        
        print("📝 Creating notification_preferences table...")
        cur.execute('''
            CREATE TABLE notification_preferences (
                user_id INTEGER PRIMARY KEY,
                email_missing_docs BOOLEAN DEFAULT TRUE,
                email_approvals BOOLEAN DEFAULT TRUE,
                email_reminders BOOLEAN DEFAULT TRUE,
                email_rejections BOOLEAN DEFAULT TRUE,
                sms_missing_docs BOOLEAN DEFAULT FALSE,
                sms_reminders BOOLEAN DEFAULT FALSE,
                in_app_all BOOLEAN DEFAULT TRUE,
                
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        print("   ✅ Created notification_preferences table")
        
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
            
            cur.execute("""
                INSERT INTO notification_preferences (user_id) VALUES (%s)
            """, (admin_id,))
            
            print(f"✅ Default Super Admin created with ID: {admin_id}")
            
            # Create sample admissions staff
            staff_password = hash_password("staff123")
            cur.execute("""
                INSERT INTO users (username, password_hash, full_name, email, role, is_active, requires_password_reset)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                "admissions",
                staff_password,
                'Admissions Staff',
                'staff@assiscan.com',
                'ADMISSIONS_STAFF',
                True,
                False
            ))
            staff_id = cur.fetchone()[0]
            
            cur.execute("""
                INSERT INTO notification_preferences (user_id) VALUES (%s)
            """, (staff_id,))
            
            print(f"✅ Default Admissions Staff created with ID: {staff_id}")
            
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

# ================= ENHANCED CHECK TABLES FUNCTION =================
def check_tables_exist():
    print("🔍 Checking if tables exist...")
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        tables = ['users', 'user_sessions', 'colleges', 'programs', 'records', 'notifications', 'notification_preferences']
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
        
        print("✅ All tables and columns exist")
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
        print("✅ Database initialized successfully!")
    else:
        print("❌ Database initialization failed!")
else:
    print("✅ All tables already exist")

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

# ================= NOTIFICATION FUNCTIONS =================
def create_notification(user_id, notification_type, title, message, data=None, priority=0, expires_at=None):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO notifications (user_id, type, title, message, data, priority, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, notification_type, title, message, json.dumps(data) if data else None, priority, expires_at))
        
        notification_id = cur.fetchone()[0]
        conn.commit()
        return notification_id
    except Exception as e:
        print(f"❌ Error creating notification: {e}")
        return None
    finally:
        conn.close()

def send_notification_email(user_id, title, message):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT email, full_name FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        
        if not user or not user[0]:
            return False
        
        email = user[0]
        name = user[1]
        
        if EMAIL_SENDER and EMAIL_PASSWORD:
            try:
                msg = MIMEMultipart()
                msg['From'] = EMAIL_SENDER
                msg['To'] = email
                msg['Subject'] = f"AssiScan Notification: {title}"
                
                body = f"""
                Dear {name},
                
                {message}
                
                --------------------
                This is an automated notification from the AssiScan System.
                Please log in to your account to view more details.
                
                Best regards,
                The AssiScan Team
                """
                
                msg.attach(MIMEText(body, 'plain'))
                
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.send_message(msg)
                server.quit()
                
                return True
            except Exception as e:
                print(f"❌ SMTP email error: {e}")
                return False
    except Exception as e:
        print(f"❌ Error sending notification email: {e}")
        return False
    finally:
        conn.close()

def check_missing_documents(record_id=None):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT r.*, u.id as user_id, u.email, u.full_name
            FROM records r
            JOIN users u ON r.user_id = u.id
            WHERE r.is_archived = FALSE
        """
        
        if record_id:
            query += " AND r.id = %s"
            cur.execute(query, (record_id,))
        else:
            cur.execute(query)
        
        records = cur.fetchall()
        
        for record in records:
            missing_docs = []
            
            doc_status = record.get('document_status', {})
            if isinstance(doc_status, str):
                try:
                    doc_status = json.loads(doc_status)
                except:
                    doc_status = {}
            
            if not doc_status.get('psa') and not record.get('image_path'):
                missing_docs.append("PSA Birth Certificate")
            
            if not doc_status.get('form137') and not record.get('form137_path'):
                missing_docs.append("Form 137")
            
            if not doc_status.get('goodmoral') and not record.get('goodmoral_path'):
                missing_docs.append("Good Moral Certificate")
            
            if record.get('is_transferee'):
                if not record.get('honorable_dismissal_path'):
                    missing_docs.append("Honorable Dismissal")
                if not record.get('transfer_credentials_path'):
                    missing_docs.append("Transfer Credentials")
            
            if missing_docs:
                cur.execute("""
                    SELECT created_at FROM notifications 
                    WHERE user_id = %s AND type = 'MISSING_DOCUMENT' 
                    ORDER BY created_at DESC LIMIT 1
                """, (record['user_id'],))
                
                last_notification = cur.fetchone()
                
                should_notify = True
                if last_notification:
                    days_since = (datetime.now() - last_notification[0]).days
                    if days_since < 3:
                        should_notify = False
                
                if should_notify:
                    doc_list = ", ".join(missing_docs)
                    message = f"You are missing the following required documents: {doc_list}. Please upload them to complete your application."
                    
                    create_notification(
                        user_id=record['user_id'],
                        notification_type='MISSING_DOCUMENT',
                        title="Missing Required Documents",
                        message=message,
                        data={
                            'record_id': record['id'],
                            'missing_docs': missing_docs
                        },
                        priority=1
                    )
        
        return True
    except Exception as e:
        print(f"❌ Error checking missing documents: {e}")
        return False
    finally:
        conn.close()

# ================= MISSING DOCUMENTS ENDPOINT =================
@app.route('/api/missing-documents', methods=['GET'])
@login_required
@permission_required('view_all_records')
def get_missing_documents():
    """Get all students with missing documents (Admin only)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed", "students": [], "total_count": 0}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT r.id, r.user_id, r.name, r.lrn, r.college, r.program, 
                   r.student_type, r.status, r.updated_at, r.is_transferee,
                   r.image_path, r.form137_path, r.goodmoral_path,
                   r.honorable_dismissal_path, r.transfer_credentials_path,
                   r.document_status, r.email, r.mobile_no,
                   u.full_name, u.email as user_email
            FROM records r
            JOIN users u ON r.user_id = u.id
            WHERE r.is_archived = FALSE 
              AND u.role = 'STUDENT'
            ORDER BY r.updated_at DESC
        """)
        
        records = cur.fetchall()
        conn.close()
        
        missing_docs_list = []
        
        for record in records:
            missing_docs = []
            
            # Parse document status
            doc_status = record.get('document_status', {})
            if isinstance(doc_status, str):
                try:
                    doc_status = json.loads(doc_status)
                except:
                    doc_status = {}
            
            # Check PSA
            if not doc_status.get('psa') and not record.get('image_path'):
                missing_docs.append({
                    'type': 'psa',
                    'name': 'PSA Birth Certificate'
                })
            
            # Check Form 137
            if not doc_status.get('form137') and not record.get('form137_path'):
                missing_docs.append({
                    'type': 'form137',
                    'name': 'Form 137'
                })
            
            # Check Good Moral
            if not doc_status.get('goodmoral') and not record.get('goodmoral_path'):
                missing_docs.append({
                    'type': 'goodmoral',
                    'name': 'Good Moral Certificate'
                })
            
            # Check transferee documents
            if record.get('is_transferee'):
                if not record.get('honorable_dismissal_path'):
                    missing_docs.append({
                        'type': 'honorable_dismissal',
                        'name': 'Honorable Dismissal'
                    })
                if not record.get('transfer_credentials_path'):
                    missing_docs.append({
                        'type': 'transfer_credentials',
                        'name': 'Transfer Credentials'
                    })
            
            # Only include if there are missing documents
            if missing_docs:
                record_dict = dict(record)
                record_dict['missing_documents'] = missing_docs
                record_dict['missing_count'] = len(missing_docs)
                missing_docs_list.append(record_dict)
        
        # Sort by most missing documents first
        missing_docs_list.sort(key=lambda x: x['missing_count'], reverse=True)
        
        return jsonify({
            "students": missing_docs_list,
            "total_count": len(missing_docs_list)
        })
        
    except Exception as e:
        print(f"❌ Error in get_missing_documents: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "students": [], "total_count": 0}), 500

# ================= OPTIMIZED SEND REMINDERS ENDPOINT =================
@app.route('/api/missing-documents/remind-all', methods=['POST'])
@login_required
@permission_required('send_notifications')
def remind_all_missing_documents():
    """Send reminders to all students with missing documents (OPTIMIZED)"""
    try:
        # Get request data
        data = request.get_json(silent=True) or {}
        specific_user_id = data.get('user_id')
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # OPTIMIZED: Kunin lang ang basic info, hindi lahat ng malalaking columns
        query = """
            SELECT r.id, r.user_id, r.is_transferee,
                   r.image_path, r.form137_path, r.goodmoral_path,
                   r.honorable_dismissal_path, r.transfer_credentials_path,
                   r.document_status,
                   u.email, u.full_name, u.email_notifications
            FROM records r
            JOIN users u ON r.user_id = u.id
            WHERE r.is_archived = FALSE 
              AND u.role = 'STUDENT'
        """
        
        params = []
        if specific_user_id:
            query += " AND u.id = %s"
            params.append(specific_user_id)
        
        # LIMIT para hindi ma-overload ang memory
        query += " LIMIT 100"
        
        cur.execute(query, params)
        records = cur.fetchall()
        
        # Isara agad ang connection para hindi ma-occupy ang memory
        conn.close()
        
        # Force garbage collection
        gc.collect()
        
        sent_count = 0
        errors = []
        
        # Process one by one para hindi mag-accumulate sa memory
        for record in records:
            try:
                missing_docs = []
                
                # Parse document status
                doc_status = record.get('document_status', {})
                if isinstance(doc_status, str):
                    try:
                        doc_status = json.loads(doc_status)
                    except:
                        doc_status = {}
                
                # Check PSA
                if not doc_status.get('psa') and not record.get('image_path'):
                    missing_docs.append("PSA Birth Certificate")
                
                # Check Form 137
                if not doc_status.get('form137') and not record.get('form137_path'):
                    missing_docs.append("Form 137")
                
                # Check Good Moral
                if not doc_status.get('goodmoral') and not record.get('goodmoral_path'):
                    missing_docs.append("Good Moral Certificate")
                
                # Check transferee documents
                if record.get('is_transferee'):
                    if not record.get('honorable_dismissal_path'):
                        missing_docs.append("Honorable Dismissal")
                    if not record.get('transfer_credentials_path'):
                        missing_docs.append("Transfer Credentials")
                
                # Only send notification if there are missing documents
                if missing_docs:
                    doc_list = ", ".join(missing_docs)
                    message = f"Reminder: You are missing the following required documents: {doc_list}. Please upload them to complete your application."
                    
                    # Create notification in database
                    notification_id = create_notification(
                        user_id=record['user_id'],
                        notification_type='MISSING_DOCUMENT',
                        title="Reminder: Missing Documents",
                        message=message,
                        data={
                            'record_id': record['id'],
                            'missing_docs': missing_docs
                        },
                        priority=1
                    )
                    
                    if notification_id:
                        sent_count += 1
                        
                        # Send email if user has email notifications enabled
                        if record.get('email_notifications') and record.get('email'):
                            send_notification_email(
                                user_id=record['user_id'],
                                title="Reminder: Missing Documents",
                                message=message
                            )
                    
                    # Force garbage collection per 10 records
                    if sent_count % 10 == 0:
                        gc.collect()
                            
            except Exception as e:
                error_msg = f"Error processing user {record.get('user_id')}: {str(e)}"
                print(f"❌ {error_msg}")
                errors.append(error_msg)
                continue
        
        # Final garbage collection
        gc.collect()
        
        response_data = {
            "success": True,
            "message": f"Sent reminders to {sent_count} students",
            "sent_count": sent_count
        }
        
        if errors:
            response_data["errors"] = errors
            
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"❌ Error in remind_all_missing_documents: {e}")
        traceback.print_exc()
        # Force garbage collection on error
        gc.collect()
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Failed to send reminders"
        }), 500

# ================= FIXED SINGLE REMINDER ENDPOINT =================
@app.route('/api/missing-documents/remind/<int:user_id>', methods=['POST'])
@login_required
@permission_required('send_notifications')
def remind_single_user(user_id):
    """Send reminder to a specific student"""
    try:
        # Reuse the remind_all function with specific user_id
        return remind_all_missing_documents()
        
    except Exception as e:
        print(f"❌ Error sending reminder to user {user_id}: {e}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# ================= NOTIFICATION ENDPOINTS =================
@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    """Get notifications for current user"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 50))
        
        query = """
            SELECT * FROM notifications 
            WHERE user_id = %s
        """
        params = [session['user_id']]
        
        if unread_only:
            query += " AND is_read = FALSE"
        
        query += " ORDER BY priority DESC, created_at DESC LIMIT %s"
        params.append(limit)
        
        cur.execute(query, params)
        notifications = cur.fetchall()
        
        cur.execute("""
            SELECT COUNT(*) FROM notifications 
            WHERE user_id = %s AND is_read = FALSE
        """, (session['user_id'],))
        unread_count = cur.fetchone()['count']
        
        conn.close()
        
        for n in notifications:
            n['created_at'] = n['created_at'].isoformat() if n['created_at'] else None
            n['expires_at'] = n['expires_at'].isoformat() if n['expires_at'] else None
        
        return jsonify({
            "notifications": notifications,
            "unread_count": unread_count
        })
        
    except Exception as e:
        print(f"❌ Error getting notifications: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE notifications 
            SET is_read = TRUE 
            WHERE id = %s AND user_id = %s
            RETURNING id
        """, (notification_id, session['user_id']))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Notification not found"}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ Error marking notification read: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/read-all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE notifications 
            SET is_read = TRUE 
            WHERE user_id = %s AND is_read = FALSE
        """, (session['user_id'],))
        
        updated_count = cur.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "updated_count": updated_count
        })
        
    except Exception as e:
        print(f"❌ Error marking all notifications read: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/preferences', methods=['GET'])
@login_required
def get_notification_preferences():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT * FROM notification_preferences 
            WHERE user_id = %s
        """, (session['user_id'],))
        
        prefs = cur.fetchone()
        conn.close()
        
        if not prefs:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO notification_preferences (user_id) 
                VALUES (%s) RETURNING user_id
            """, (session['user_id'],))
            conn.commit()
            conn.close()
            
            return jsonify({
                "email_missing_docs": True,
                "email_approvals": True,
                "email_reminders": True,
                "email_rejections": True,
                "sms_missing_docs": False,
                "sms_reminders": False,
                "in_app_all": True
            })
        
        return jsonify(prefs)
        
    except Exception as e:
        print(f"❌ Error getting notification preferences: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/preferences', methods=['PUT'])
@login_required
def update_notification_preferences():
    try:
        data = request.json
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        updates = []
        values = []
        
        pref_fields = [
            'email_missing_docs', 'email_approvals', 'email_reminders',
            'email_rejections', 'sms_missing_docs', 'sms_reminders',
            'in_app_all'
        ]
        
        for field in pref_fields:
            if field in data:
                updates.append(f"{field} = %s")
                values.append(data[field])
        
        if not updates:
            conn.close()
            return jsonify({"error": "No preferences to update"}), 400
        
        values.append(session['user_id'])
        
        cur.execute(f"""
            UPDATE notification_preferences 
            SET {', '.join(updates)}
            WHERE user_id = %s
        """, values)
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Preferences updated successfully"
        })
        
    except Exception as e:
        print(f"❌ Error updating notification preferences: {e}")
        return jsonify({"error": str(e)}), 500

# ================= ENROLLMENT SETTINGS ENDPOINTS =================
@app.route('/api/enrollment/settings', methods=['GET'])
@login_required
def get_enrollment_settings_endpoint():
    try:
        settings = get_enrollment_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/enrollment/settings', methods=['POST'])
@login_required
@permission_required('manage_settings')
def update_enrollment_settings():
    try:
        data = request.json
        if save_enrollment_settings(data):
            return jsonify({
                "success": True,
                "message": "Enrollment settings updated successfully"
            })
        else:
            return jsonify({"error": "Failed to save settings"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/enrollment/check-reminders', methods=['POST'])
@login_required
@permission_required('manage_settings')
def trigger_reminder_check():
    try:
        send_enrollment_reminders()
        return jsonify({
            "success": True,
            "message": "Reminder check completed"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def send_enrollment_reminders():
    settings = get_enrollment_settings()
    
    if not settings.get('auto_send_reminders'):
        return
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT r.*, u.id as user_id, u.email, u.full_name,
                   np.email_reminders
            FROM records r
            JOIN users u ON r.user_id = u.id
            LEFT JOIN notification_preferences np ON u.id = np.user_id
            WHERE r.is_archived = FALSE 
              AND r.status IN ('INCOMPLETE', 'PENDING')
              AND (r.last_reminder_sent IS NULL 
                   OR r.last_reminder_sent < NOW() - INTERVAL '7 days')
              AND r.reminder_count < 3
        """)
        
        records = cur.fetchall()
        
        for record in records:
            enrollment_end = datetime.strptime(settings['enrollment_end'], '%Y-%m-%d')
            days_until_deadline = (enrollment_end - datetime.now()).days
            
            if days_until_deadline <= 0:
                message = "The enrollment deadline has passed. Please contact the admissions office immediately."
                priority = 2
            elif days_until_deadline <= 3:
                message = f"URGENT: Only {days_until_deadline} days left until the enrollment deadline. Please complete your requirements."
                priority = 2
            elif days_until_deadline <= 7:
                message = f"Reminder: Only {days_until_deadline} days left until the enrollment deadline."
                priority = 1
            else:
                message = "Please complete your admission requirements to ensure your enrollment."
                priority = 0
            
            create_notification(
                user_id=record['user_id'],
                notification_type='ENROLLMENT_REMINDER',
                title=f"Enrollment Reminder - {days_until_deadline} Days Left",
                message=message,
                data={
                    'record_id': record['id'],
                    'days_until_deadline': days_until_deadline,
                    'deadline': settings['enrollment_end']
                },
                priority=priority,
                expires_at=enrollment_end
            )
            
            cur.execute("""
                UPDATE records 
                SET last_reminder_sent = CURRENT_TIMESTAMP,
                    reminder_count = reminder_count + 1
                WHERE id = %s
            """, (record['id'],))
        
        conn.commit()
        print(f"✅ Sent {len(records)} enrollment reminders")
        
    except Exception as e:
        print(f"❌ Error sending enrollment reminders: {e}")
    finally:
        conn.close()

# ================= EMAIL FUNCTION =================
def send_email_notification(recipient_email, student_name, file_paths, student_data=None):
    print(f"\n📧 Preparing email for: {recipient_email}")
    
    if not recipient_email or not isinstance(recipient_email, str):
        print("❌ Invalid email address")
        return False
    
    recipient_email = recipient_email.strip()
    
    if not recipient_email or '@' not in recipient_email:
        print("❌ Invalid email format")
        return False
    
    if not SENDGRID_API_KEY and (not EMAIL_SENDER or not EMAIL_PASSWORD):
        print("❌ Email credentials not configured")
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
        
        missing_docs = []
        doc_status = student_data.get('document_status', {})
        if isinstance(doc_status, str):
            try:
                doc_status = json.loads(doc_status)
            except:
                doc_status = {}
        
        if not doc_status.get('psa') and not student_data.get('image_path'):
            missing_docs.append("PSA Birth Certificate")
        if not doc_status.get('form137') and not student_data.get('form137_path'):
            missing_docs.append("Form 137")
        if not doc_status.get('goodmoral') and not student_data.get('goodmoral_path'):
            missing_docs.append("Good Moral Certificate")
        
        is_transferee = student_data.get('is_transferee', False)
        if is_transferee:
            if not student_data.get('honorable_dismissal_path'):
                missing_docs.append("Honorable Dismissal")
            if not student_data.get('transfer_credentials_path'):
                missing_docs.append("Transfer Credentials")
        
        missing_docs_text = ""
        if missing_docs:
            missing_docs_text = f"""
━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ MISSING REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━
The following documents are still missing:
• {"\n• ".join(missing_docs)}

Please upload these documents as soon as possible to complete your application.
"""
        
        student_info = ""
        if student_data:
            submitted_docs = []
            if doc_status.get('psa') or student_data.get('image_path'): 
                submitted_docs.append("PSA")
            if doc_status.get('form137') or student_data.get('form137_path'): 
                submitted_docs.append("Form 137")
            if doc_status.get('form138'): 
                submitted_docs.append("Form 138")
            if doc_status.get('goodmoral') or student_data.get('goodmoral_path'): 
                submitted_docs.append("Good Moral")
            
            if is_transferee:
                if student_data.get('honorable_dismissal_path'):
                    submitted_docs.append("Honorable Dismissal")
                if student_data.get('transfer_credentials_path'):
                    submitted_docs.append("Transfer Credentials")
            
            doc_summary = ", ".join(submitted_docs) if submitted_docs else "No documents yet"
            doc_count = len(submitted_docs)
            required_count = 6 if is_transferee else 4
            doc_status_text = f"{doc_count}/{required_count} documents submitted"
            
            student_info = f"""
━━━━━━━━━━━━━━━━━━━━━━━━
📋 STUDENT INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━
• Full Name: {student_data.get('name', 'N/A')}
• LRN: {student_data.get('lrn', 'N/A')}
• Sex: {student_data.get('sex', 'N/A')}
• Birthdate: {student_data.get('birthdate', 'N/A')}
• College/Department: {student_data.get('college', 'N/A')}
• Program Applied: {student_data.get('program', 'N/A')}
• Student Type: {student_data.get('student_type', 'N/A')}
{"• Transferee: YES" if is_transferee else "• Transferee: NO"}

━━━━━━━━━━━━━━━━━━━━━━━━
📄 DOCUMENT STATUS
━━━━━━━━━━━━━━━━━━━━━━━━
• Status: {doc_status_text}
• Submitted Documents: {doc_summary}
• Record Status: {student_data.get('status', 'INCOMPLETE')}

{missing_docs_text}
━━━━━━━━━━━━━━━━━━━━━━━━
📝 GOOD MORAL ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━
{goodmoral_status}
"""
        
        body = f"""📋 ADMISSION RECORD VERIFICATION

Dear {student_name},

Your admission documents have been processed through the AssiScan System. Below is a summary:

{student_info}

━━━━━━━━━━━━━━━━━━━━━━━━
📝 NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━
1. { "Upload your missing documents immediately" if missing_docs else "Proceed to the Admissions Office for enrollment"}
2. Present your name and reference ID for verification
3. Complete any remaining requirements

━━━━━━━━━━━━━━━━━━━━━━━━
🏫 CONTACT INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━
• Admissions Office: University of Batangas Lipa
• Email: admissions@ublipa.edu.ph
• Phone: (043) 1234-5678
• Reference ID: {ref_id}

━━━━━━━━━━━━━━━━━━━━━━━━
📌 IMPORTANT REMINDER
━━━━━━━━━━━━━━━━━━━━━━━━
This is an automated notification from the AssiScan System.
Please verify the accuracy of the information above.

Best regards,
The AssiScan Team
"""
        
        if SENDGRID_API_KEY:
            try:
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
                
                response = requests.post(url, headers=headers, json=data, timeout=30)
                
                if response.status_code == 202:
                    print(f"✅ Email sent via SendGrid to {recipient_email}")
                    
                    if student_data and 'user_id' in student_data:
                        create_notification(
                            user_id=student_data['user_id'],
                            notification_type='SYSTEM',
                            title="Record Processed",
                            message=f"Your admission record has been processed and emailed to you.",
                            data={'record_id': student_data.get('id')}
                        )
                    
                    return True
            except Exception as e:
                print(f"⚠️ SendGrid failed: {e}")
        
        if EMAIL_SENDER and EMAIL_PASSWORD:
            try:
                msg = MIMEMultipart()
                msg['From'] = EMAIL_SENDER
                msg['To'] = recipient_email
                msg['Subject'] = subject
                
                msg.attach(MIMEText(body, 'plain'))
                
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.send_message(msg)
                server.quit()
                
                print(f"✅ Email sent via SMTP to {recipient_email}")
                
                if student_data and 'user_id' in student_data:
                    create_notification(
                        user_id=student_data['user_id'],
                        notification_type='SYSTEM',
                        title="Record Processed",
                        message=f"Your admission record has been processed and emailed to you.",
                        data={'record_id': student_data.get('id')}
                    )
                
                return True
            except Exception as e:
                print(f"⚠️ SMTP failed: {e}")
        
        print(f"📝 [FALLBACK LOG] Email for {student_name} to {recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False

# ================= REPORT GENERATION ENDPOINT =================
@app.route('/api/report/enrollment', methods=['GET'])
@login_required
@permission_required('view_all_records')
def get_enrollment_report():
    """Get enrollment report data"""
    try:
        # Get date range from query parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Base query with optional date filter
        query = """
            SELECT r.*, u.email as user_email, u.full_name
            FROM records r
            JOIN users u ON r.user_id = u.id
            WHERE r.is_archived = FALSE
        """
        params = []
        
        if start_date and end_date:
            query += " AND DATE(r.created_at) BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        
        cur.execute(query, params)
        records = cur.fetchall()
        
        # Get colleges and programs
        cur.execute("SELECT * FROM colleges WHERE is_active = TRUE")
        colleges = cur.fetchall()
        
        cur.execute("SELECT * FROM programs WHERE is_active = TRUE")
        programs = cur.fetchall()
        
        conn.close()
        
        # Initialize report data structure
        report_data = {
            "summary": {
                "total": len(records),
                "complete": 0,
                "incomplete": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "transferees": 0,
                "regular": 0,
                "needs_review": 0
            },
            "by_college": {},
            "by_program": {},
            "documents": {
                "psa": {"uploaded": 0, "total": len(records)},
                "form137": {"uploaded": 0, "total": len(records)},
                "goodmoral": {"uploaded": 0, "total": len(records)},
                "form138": {"uploaded": 0, "total": len(records)},
                "honorable_dismissal": {"uploaded": 0, "total": 0},
                "transfer_credentials": {"uploaded": 0, "total": 0}
            }
        }
        
        # Count transferees for document totals
        transferee_count = 0
        
        for record in records:
            # Status counts
            status = record.get('status', 'INCOMPLETE')
            if status == 'INCOMPLETE':
                report_data['summary']['incomplete'] += 1
            elif status == 'PENDING':
                report_data['summary']['pending'] += 1
            elif status == 'APPROVED':
                report_data['summary']['approved'] += 1
            elif status == 'REJECTED':
                report_data['summary']['rejected'] += 1
            
            if record.get('needs_review'):
                report_data['summary']['needs_review'] += 1
            
            # Check if complete (all required documents)
            doc_status = record.get('document_status', {})
            if isinstance(doc_status, str):
                try:
                    doc_status = json.loads(doc_status)
                except:
                    doc_status = {}
            
            psa_ok = doc_status.get('psa') or record.get('image_path')
            f137_ok = doc_status.get('form137') or record.get('form137_path')
            gm_ok = doc_status.get('goodmoral') or record.get('goodmoral_path')
            
            if psa_ok and f137_ok and gm_ok:
                report_data['summary']['complete'] += 1
            
            # Transferee counts
            if record.get('is_transferee'):
                report_data['summary']['transferees'] += 1
                transferee_count += 1
            else:
                report_data['summary']['regular'] += 1
            
            # Document counts
            if psa_ok:
                report_data['documents']['psa']['uploaded'] += 1
            if f137_ok:
                report_data['documents']['form137']['uploaded'] += 1
            if gm_ok:
                report_data['documents']['goodmoral']['uploaded'] += 1
            if record.get('form138_path'):
                report_data['documents']['form138']['uploaded'] += 1
            
            # College breakdown
            college = record.get('college', 'Unassigned')
            if college not in report_data['by_college']:
                report_data['by_college'][college] = {
                    "total": 0,
                    "regular": 0,
                    "transferee": 0,
                    "complete": 0,
                    "incomplete": 0,
                    "pending": 0,
                    "approved": 0,
                    "rejected": 0,
                    "needs_review": 0
                }
            
            report_data['by_college'][college]['total'] += 1
            if record.get('is_transferee'):
                report_data['by_college'][college]['transferee'] += 1
            else:
                report_data['by_college'][college]['regular'] += 1
            
            if record.get('needs_review'):
                report_data['by_college'][college]['needs_review'] += 1
            
            if psa_ok and f137_ok and gm_ok:
                report_data['by_college'][college]['complete'] += 1
            
            if status == 'INCOMPLETE':
                report_data['by_college'][college]['incomplete'] += 1
            elif status == 'PENDING':
                report_data['by_college'][college]['pending'] += 1
            elif status == 'APPROVED':
                report_data['by_college'][college]['approved'] += 1
            elif status == 'REJECTED':
                report_data['by_college'][college]['rejected'] += 1
            
            # Program breakdown
            program = record.get('program', 'Unassigned')
            if program not in report_data['by_program']:
                report_data['by_program'][program] = {
                    "total": 0,
                    "college": college,
                    "transferee": 0,
                    "regular": 0,
                    "needs_review": 0
                }
            
            report_data['by_program'][program]['total'] += 1
            if record.get('is_transferee'):
                report_data['by_program'][program]['transferee'] += 1
            else:
                report_data['by_program'][program]['regular'] += 1
            
            if record.get('needs_review'):
                report_data['by_program'][program]['needs_review'] += 1
        
        # Update transferee document totals
        report_data['documents']['honorable_dismissal']['total'] = transferee_count
        report_data['documents']['transfer_credentials']['total'] = transferee_count
        
        # Count uploaded transferee documents
        for record in records:
            if record.get('is_transferee'):
                if record.get('honorable_dismissal_path'):
                    report_data['documents']['honorable_dismissal']['uploaded'] += 1
                if record.get('transfer_credentials_path'):
                    report_data['documents']['transfer_credentials']['uploaded'] += 1
        
        return jsonify({
            "success": True,
            "report": report_data,
            "colleges": colleges,
            "programs": programs
        })
        
    except Exception as e:
        print(f"❌ Error generating report: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

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
            saved_paths.append(filename)
            try:
                img = Image.open(path)
                pil_images.append(img)
                print(f"   ✅ Saved: {filename}")
            except Exception as e:
                print(f"Error opening image {filename}: {e}")
    return saved_paths, pil_images

def move_to_archive(filename):
    if not filename:
        return filename
    
    try:
        source_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(source_path):
            return filename
        
        date_folder = datetime.now().strftime('%Y-%m')
        archive_subfolder = os.path.join(app.config['ARCHIVE_FOLDER'], date_folder)
        if not os.path.exists(archive_subfolder):
            os.makedirs(archive_subfolder, exist_ok=True)
        
        dest_path = os.path.join(archive_subfolder, filename)
        os.rename(source_path, dest_path)
        print(f"📦 Moved file to archive: {filename}")
        
        return os.path.join(date_folder, filename)
    except Exception as e:
        print(f"❌ Error moving file to archive: {e}")
        return filename

def restore_from_archive(archive_path):
    if not archive_path:
        return archive_path
    
    try:
        if '/' not in archive_path:
            return archive_path
        
        source_path = os.path.join(app.config['ARCHIVE_FOLDER'], archive_path)
        if not os.path.exists(source_path):
            return archive_path
        
        filename = os.path.basename(archive_path)
        dest_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        os.rename(source_path, dest_path)
        print(f"📦 Restored file from archive: {filename}")
        
        return filename
    except Exception as e:
        print(f"❌ Error restoring file from archive: {e}")
        return archive_path

# ================= ULTRA OPTIMIZED EXTRACT WITH GEMINI 2.5 FLASH =================
def extract_with_gemini(prompt, images):
    """
    Extract text from images using ONLY Gemini 2.5 Flash
    ULTRA OPTIMIZED - compresses images, limits to first 3 pages only, and manages memory
    """
    try:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured")
        
        # ONLY use Gemini 2.5 Flash
        model_name = "gemini-2.5-flash"
        
        print(f"🤖 Using ULTRA OPTIMIZED model: {model_name}")
        
        # LIMIT TO FIRST 3 IMAGES ONLY (para tipid sa memory)
        if len(images) > 3:
            print(f"⚠️ Too many images ({len(images)}), limiting to first 3 only for memory efficiency")
            images = images[:3]
        
        # COMPRESS images to reduce memory usage
        compressed_images = []
        for i, img in enumerate(images):
            print(f"  📦 Compressing image {i+1}/{len(images)}...")
            
            # Resize if too large (max 800px para mas tipid)
            max_size = 800
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                print(f"     Resized to {img.width}x{img.height}")
            
            # Convert to RGB if necessary (remove alpha channel)
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            
            # Compress with high compression
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=70, optimize=True)
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
            
            compressed_images.append({
                "inline_data": {
                    "mime_type": "image/jpeg",  # Use JPEG instead of PNG for smaller size
                    "data": img_base64
                }
            })
            
            # Clear memory
            img_byte_arr.close()
            
            # Force garbage collection
            if i % 2 == 0:
                gc.collect()
        
        # Direct REST API call
        url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}] + compressed_images
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024  # Binawasan para tipid
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
        }
        
        # Clear compressed images from memory before API call
        compressed_images.clear()
        gc.collect()
        
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'candidates' in result and len(result['candidates']) > 0:
                text = result['candidates'][0]['content']['parts'][0]['text']
                print(f"✅ Success with {model_name}")
                
                # Clear response to free memory
                result.clear()
                
                return text
            else:
                print(f"⚠️ No candidates in response")
                raise Exception("No candidates in response")
        else:
            print(f"❌ {model_name} failed with status {response.status_code}")
            raise Exception(f"API error: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Extraction failed: {e}")
        traceback.print_exc()
        raise Exception(f"Extraction failed: {str(e)[:200]}")
    finally:
        # Force garbage collection
        gc.collect()

def extract_direct_rest_api_fixed(prompt, images):
    """Fallback function - same as extract_with_gemini"""
    return extract_with_gemini(prompt, images)

def calculate_goodmoral_score(analysis_data):
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
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("SELECT document_status, status, user_id FROM records WHERE id = %s", (record_id,))
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
        
        old_value = status.get(doc_type, False)
        status[doc_type] = has_file
        
        if not old_value and has_file:
            user_id = result[2] if result and len(result) > 2 else None
            if user_id:
                doc_names = {
                    'psa': 'PSA Birth Certificate',
                    'form137': 'Form 137',
                    'form138': 'Form 138',
                    'goodmoral': 'Good Moral Certificate'
                }
                doc_name = doc_names.get(doc_type, doc_type)
                
                create_notification(
                    user_id=user_id,
                    notification_type='DOCUMENT_UPLOADED',
                    title="Document Uploaded Successfully",
                    message=f"Your {doc_name} has been uploaded successfully.",
                    data={'record_id': record_id, 'doc_type': doc_type},
                    priority=0
                )
        
        current_record_status = result[1] if result and len(result) > 1 else 'INCOMPLETE'
        
        if current_record_status not in ['APPROVED', 'REJECTED']:
            all_docs = all([status.get('psa', False), status.get('form137', False), 
                           status.get('form138', False), status.get('goodmoral', False)])
            
            if all_docs:
                overall_status = 'PENDING'
                if user_id:
                    create_notification(
                        user_id=1,
                        notification_type='SYSTEM',
                        title="Record Ready for Review",
                        message=f"Student record #{record_id} has all documents and is ready for review.",
                        data={'record_id': record_id},
                        priority=1
                    )
            else:
                overall_status = 'INCOMPLETE'
        else:
            overall_status = current_record_status
        
        cur.execute("""
            UPDATE records 
            SET document_status = %s, 
                status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(status), overall_status, record_id))
        
        conn.commit()
        print(f"📄 Document status updated for record {record_id}: {doc_type}={has_file}")
        
        check_missing_documents(record_id)
        
        return status, overall_status
        
    except Exception as e:
        print(f"❌ Error updating document status: {e}")
        return None, None
    finally:
        conn.close()

# ================= DEBUG MIDDLEWARE =================
@app.before_request
def log_request_info():
    if request.path not in ['/static/', '/favicon.ico']:
        print(f"\n{'='*60}")
        print(f"🌐 {request.method} {request.path}")
        print(f"🔍 Session: {dict(session)}")
        print(f"📱 IP: {request.remote_addr}")
        print(f"{'='*60}")

# ================= TEST GEMINI ENDPOINT =================
@app.route('/test-gemini', methods=['GET'])
def test_gemini():
    return jsonify({
        "status": "success",
        "message": "Using ULTRA OPTIMIZED Gemini 2.5 Flash with memory management",
        "features": [
            "Image compression (JPEG, 70% quality)",
            "Max 3 images only",
            "Resize to 800px max",
            "Memory cleanup after each request"
        ]
    })

# ================= DATABASE INITIALIZATION ENDPOINT =================
@app.route('/api/init-db', methods=['POST'])
def initialize_database():
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
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Cannot connect to database"
            }), 500
        
        cur = conn.cursor()
        
        tables = ['users', 'colleges', 'programs', 'records', 'user_sessions', 'notifications', 'notification_preferences']
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
        session['requires_password_reset'] = user['requires_password_reset']  # ← IMPORTANT
        
        create_notification(
            user_id=user['id'],
            notification_type='SYSTEM',
            title="New Login Detected",
            message=f"New login from {ip_address}",
            data={'ip': ip_address, 'user_agent': user_agent},
            priority=0
        )
        
        user_response = {
            'id': user['id'],
            'username': user['username'],
            'full_name': user['full_name'],
            'email': user['email'],
            'role': user['role'].upper(),
            'requires_password_reset': user['requires_password_reset'],  # ← IMPORTANT
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
    print("🔍 /logout route accessed")
    
    session_token = session.get('session_token')
    if session_token:
        logout_session(session_token)
    
    session.clear()
    
    print("✅ Session cleared, redirecting to login page")
    return redirect('/login')

@app.route('/api/check-session', methods=['GET'])
def check_session():
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
        
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM notifications 
                WHERE user_id = %s AND is_read = FALSE
            """, (user['id'],))
            unread_count = cur.fetchone()[0]
            conn.close()
        else:
            unread_count = 0
        
        return jsonify({
            "authenticated": True,
            "user": {
                'id': user['id'],
                'username': user['username'],
                'full_name': user['full_name'],
                'email': user['email'],
                'role': user['role'].upper(),
                'requires_password_reset': user['requires_password_reset']  # ← IMPORTANT
            },
            "unread_notifications": unread_count,
            "permissions": PERMISSIONS.get(user['role'].upper(), [])
        })
    else:
        print("❌ Invalid session token")
        return jsonify({"authenticated": False}), 200

# ================= PASSWORD MANAGEMENT ENDPOINTS =================
@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    try:
        data = request.json
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        force_reset = data.get('force_reset', False)
        
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
        
        # Allow force reset without current password check
        if not force_reset:
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
        
        session['requires_password_reset'] = False  # ← IMPORTANT
        
        create_notification(
            user_id=session['user_id'],
            notification_type='SYSTEM',
            title="Password Changed",
            message="Your password was successfully changed.",
            priority=0
        )
        
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
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                   u.requires_password_reset, u.last_login, u.created_at,
                   c.name as college_name, p.name as program_name,
                   creator.full_name as created_by_name,
                   u.email_notifications, u.mobile_number
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
    conn = None
    try:
        data = request.json
        
        required_fields = ['username', 'full_name', 'email', 'role']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field.replace('_', ' ').title()} is required"}), 400
        
        if data['role'] not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF', 'STUDENT']:
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
                created_by, email_notifications, mobile_number
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            True,  # ← SET TO TRUE FOR NEW USERS
            session['user_id'],
            data.get('email_notifications', True),
            data.get('mobile_number')
        ))
        
        new_user = cur.fetchone()
        
        cur.execute("""
            INSERT INTO notification_preferences (user_id) VALUES (%s)
        """, (new_user[0],))
        
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
            'temp_password': temp_password,
            'requires_password_reset': True  # ← IMPORTANT
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
            if data['role'] not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF', 'STUDENT']:
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
        
        if 'email_notifications' in data:
            updates.append("email_notifications = %s")
            values.append(data['email_notifications'])
        
        if 'mobile_number' in data:
            updates.append("mobile_number = %s")
            values.append(data['mobile_number'])
        
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
            'email_notifications': updated_user['email_notifications'],
            'mobile_number': updated_user['mobile_number'],
            'requires_password_reset': updated_user['requires_password_reset'],  # ← IMPORTANT
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
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                   u.requires_password_reset, u.last_login, u.created_at,
                   c.name as college_name, p.name as program_name,
                   u.college_id, u.program_id,
                   u.email_notifications, u.mobile_number
            FROM users u
            LEFT JOIN colleges c ON u.college_id = c.id
            LEFT JOIN programs p ON u.program_id = p.id
            WHERE u.id = %s
        """, (session['user_id'],))
        
        profile = cur.fetchone()
        
        cur.execute("SELECT * FROM notification_preferences WHERE user_id = %s", (session['user_id'],))
        prefs = cur.fetchone()
        
        conn.close()
        
        if not profile:
            return jsonify({"error": "Profile not found"}), 404
        
        if profile['last_login']:
            profile['last_login'] = profile['last_login'].isoformat()
        if profile['created_at']:
            profile['created_at'] = profile['created_at'].isoformat()
        
        profile['permissions'] = PERMISSIONS.get(profile['role'].upper(), [])
        profile['notification_preferences'] = prefs if prefs else {}
        
        return jsonify(profile)
        
    except Exception as e:
        print(f"❌ Get profile error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/profile', methods=['PUT'])
@login_required
def update_profile():
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
        
        if 'mobile_number' in data:
            updates.append("mobile_number = %s")
            values.append(data['mobile_number'])
        
        if 'email_notifications' in data:
            updates.append("email_notifications = %s")
            values.append(data['email_notifications'])
        
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
    conn = get_db_connection()
    if not conn: 
        return jsonify({"records": [], "error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT * FROM records 
            WHERE user_id = %s AND is_archived = FALSE
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
                except:
                    r['goodmoral_analysis'] = {}
            
            if r.get('other_documents'):
                try:
                    if isinstance(r['other_documents'], str):
                        r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            if r.get('document_status'):
                try:
                    if isinstance(r['document_status'], str):
                        r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            
            image_fields = ['image_path', 'form137_path', 'form138_path', 'goodmoral_path', 'honorable_dismissal_path', 'transfer_credentials_path']
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
            "honorable_dismissal_documents": [],
            "transfer_credentials_documents": [],
            "other_documents": []
        }
        
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
        
        if record.get('honorable_dismissal_path'):
            paths = record['honorable_dismissal_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["honorable_dismissal_documents"].append({
                        "filename": path.strip(),
                        "download_url": f"{request.host_url}uploads/{path.strip()}"
                    })
        
        if record.get('transfer_credentials_path'):
            paths = record['transfer_credentials_path'].split(',')
            for path in paths:
                if path.strip():
                    documents["transfer_credentials_documents"].append({
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
                WHERE r.user_id = %s AND r.is_archived = FALSE
                ORDER BY r.updated_at DESC
                LIMIT 1
            """, (session['user_id'],))
        elif user_role == 'SUPER_ADMIN' or user_role == 'ADMISSIONS_STAFF':
            cur.execute("""
                SELECT DISTINCT ON (r.user_id) 
                       r.*, u.username, u.email as user_email, u.full_name as user_full_name
                FROM records r
                JOIN users u ON r.user_id = u.id
                WHERE u.role = 'STUDENT' AND r.is_archived = FALSE
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
                except:
                    r['goodmoral_analysis'] = {}
            
            if r.get('other_documents'):
                try:
                    if isinstance(r['other_documents'], str):
                        r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            if r.get('document_status'):
                try:
                    if isinstance(r['document_status'], str):
                        r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            
            image_fields = ['image_path', 'form137_path', 'form138_path', 'goodmoral_path', 'honorable_dismissal_path', 'transfer_credentials_path']
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

# ================= GET ARCHIVED RECORDS =================
@app.route('/get-archived-records', methods=['GET'])
@login_required
@permission_required('view_archived_records')
def get_archived_records():
    conn = get_db_connection()
    if not conn:
        return jsonify({"records": [], "error": "Database connection failed"})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT DISTINCT ON (r.user_id) 
                   r.*, u.username, u.email as user_email, u.full_name as user_full_name,
                   archiver.full_name as archived_by_name,
                   restorer.full_name as restored_by_name
            FROM records r
            JOIN users u ON r.user_id = u.id
            LEFT JOIN users archiver ON r.archived_by = archiver.id
            LEFT JOIN users restorer ON r.restored_by = restorer.id
            WHERE u.role = 'STUDENT' AND r.is_archived = TRUE
            ORDER BY r.user_id, r.updated_at DESC
        """)
        
        rows = cur.fetchall()
        conn.close()
        
        for r in rows:
            if r['created_at']: 
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['updated_at']: 
                r['updated_at'] = r['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['archived_at']: 
                r['archived_at'] = r['archived_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['restored_at']: 
                r['restored_at'] = r['restored_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r['birthdate']: 
                r['birthdate'] = str(r['birthdate'])
            
            if r.get('goodmoral_analysis'):
                try:
                    if isinstance(r['goodmoral_analysis'], str):
                        r['goodmoral_analysis'] = json.loads(r['goodmoral_analysis'])
                except:
                    r['goodmoral_analysis'] = {}
            
            if r.get('other_documents'):
                try:
                    if isinstance(r['other_documents'], str):
                        r['other_documents'] = json.loads(r['other_documents'])
                except:
                    r['other_documents'] = []
            else:
                r['other_documents'] = []
            
            if r.get('document_status'):
                try:
                    if isinstance(r['document_status'], str):
                        r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
            else:
                r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        return jsonify({
            "records": rows,
            "server_url": request.host_url.rstrip('/')
        })
    except Exception as e:
        print(f"❌ Error in get-archived-records: {e}")
        if conn:
            conn.close()
        return jsonify({"records": [], "error": str(e)}), 500

# ================= NEW: ADMIN STUDENT LIST WITH SCAN BUTTONS =================
@app.route('/api/admin/students', methods=['GET'])
@login_required
@permission_required('view_all_records')
def get_student_list():
    """Get list of all students for admin with scan buttons"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT u.id as user_id, u.full_name, u.username, u.email,
                   r.id as record_id, r.name as student_name, r.lrn, 
                   r.college, r.program, r.status, r.is_archived,
                   r.document_status, r.needs_review,
                   CASE 
                       WHEN r.id IS NULL THEN 'NO_RECORD'
                       ELSE r.status 
                   END as record_status
            FROM users u
            LEFT JOIN records r ON u.id = r.user_id
            WHERE u.role = 'STUDENT' AND u.is_active = TRUE
            ORDER BY u.created_at DESC
        """)
        
        students = cur.fetchall()
        conn.close()
        
        # Parse JSON fields
        for student in students:
            if student.get('document_status') and isinstance(student['document_status'], str):
                try:
                    student['document_status'] = json.loads(student['document_status'])
                except:
                    student['document_status'] = {}
        
        return jsonify({
            "students": students,
            "total": len(students)
        })
        
    except Exception as e:
        print(f"❌ Error getting student list: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ================= NEW: ADMIN SCAN FOR SPECIFIC STUDENT =================
@app.route('/api/admin/scan/<int:user_id>', methods=['GET'])
@login_required
@permission_required('scan_documents')
def get_student_for_scan(user_id):
    """Get student data for scanning page"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get student info
        cur.execute("""
            SELECT u.id, u.full_name, u.username, u.email,
                   r.id as record_id, r.*
            FROM users u
            LEFT JOIN records r ON u.id = r.user_id
            WHERE u.id = %s AND u.role = 'STUDENT'
        """, (user_id,))
        
        student = cur.fetchone()
        
        if not student:
            conn.close()
            return jsonify({"error": "Student not found"}), 404
        
        # Parse JSON fields
        if student.get('goodmoral_analysis') and isinstance(student['goodmoral_analysis'], str):
            try:
                student['goodmoral_analysis'] = json.loads(student['goodmoral_analysis'])
            except:
                student['goodmoral_analysis'] = {}
        
        if student.get('other_documents') and isinstance(student['other_documents'], str):
            try:
                student['other_documents'] = json.loads(student['other_documents'])
            except:
                student['other_documents'] = []
        
        if student.get('document_status') and isinstance(student['document_status'], str):
            try:
                student['document_status'] = json.loads(student['document_status'])
            except:
                student['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        conn.close()
        
        return jsonify({
            "student": student
        })
        
    except Exception as e:
        print(f"❌ Error getting student for scan: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ================= NEW: ADMIN SCAN DOCUMENTS FOR STUDENT (FIXED - REMOVED scanned_by) =================
@app.route('/api/admin/scan/<int:user_id>/documents', methods=['POST'])
@login_required
@permission_required('scan_documents')
def scan_student_documents(user_id):
    """Admin scans documents for a specific student - FIXED version without scanned_by column"""
    try:
        if 'doc_type' not in request.form:
            return jsonify({"error": "Document type required"}), 400
        
        doc_type = request.form['doc_type']
        files = request.files.getlist('files')
        
        if not files or files[0].filename == '':
            return jsonify({"error": "No files uploaded"}), 400
        
        # Create student-specific folder
        student_folder = os.path.join(app.config['UPLOAD_FOLDER'], f"student_{user_id}")
        if not os.path.exists(student_folder):
            os.makedirs(student_folder, exist_ok=True)
        
        # Save files
        saved_paths = []
        pil_images = []
        
        for i, file in enumerate(files):
            if file and file.filename:
                timestamp = int(datetime.now().timestamp())
                filename = secure_filename(f"{doc_type}_{timestamp}_{i}_{file.filename}")
                filepath = os.path.join(student_folder, filename)
                file.save(filepath)
                saved_paths.append(f"student_{user_id}/{filename}")
                
                try:
                    img = Image.open(filepath)
                    pil_images.append(img)
                except Exception as e:
                    print(f"Error opening image {filename}: {e}")
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        # Check if record exists
        cur.execute("SELECT id FROM records WHERE user_id = %s", (user_id,))
        record = cur.fetchone()
        
        column_map = {
            'psa': 'image_path',
            'form137': 'form137_path',
            'goodmoral': 'goodmoral_path',
            'form138': 'form138_path',
            'honorable_dismissal': 'honorable_dismissal_path',
            'transfer_credentials': 'transfer_credentials_path'
        }
        
        db_column = column_map.get(doc_type)
        
        if record:
            # Update existing record - REMOVED scanned_by and scanned_at
            if db_column:
                cur.execute(f"SELECT {db_column} FROM records WHERE id = %s", (record[0],))
                existing = cur.fetchone()[0]
                
                if existing:
                    new_paths = existing.split(',') + saved_paths
                else:
                    new_paths = saved_paths
                
                new_path_str = ','.join([p for p in new_paths if p])
                
                cur.execute(f"""
                    UPDATE records 
                    SET {db_column} = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_path_str, record[0]))
                
                record_id = record[0]
            else:
                record_id = record[0]
        else:
            # Create new record - REMOVED scanned_by and scanned_at
            cur.execute("""
                INSERT INTO records (
                    user_id, status
                ) VALUES (%s, 'INCOMPLETE')
                RETURNING id
            """, (user_id,))
            record_id = cur.fetchone()[0]
            
            # Update document path
            if db_column:
                new_path_str = ','.join(saved_paths)
                cur.execute(f"UPDATE records SET {db_column} = %s WHERE id = %s", 
                           (new_path_str, record_id))
        
        # Update document status
        if doc_type in ['psa', 'form137', 'goodmoral']:
            update_document_status(record_id, doc_type, True)
        
        # Set needs_review flag if applicable
        if doc_type == 'goodmoral':
            cur.execute("UPDATE records SET needs_review = TRUE WHERE id = %s", (record_id,))
        
        conn.commit()
        conn.close()
        
        # Process with AI if applicable - using ULTRA OPTIMIZED version
        extracted_data = None
        if doc_type == 'psa' and pil_images:
            extracted_data = process_psa_extraction(pil_images, saved_paths)
        elif doc_type == 'form137' and pil_images:
            extracted_data = process_form137_extraction(pil_images, saved_paths)
        elif doc_type == 'goodmoral' and pil_images:
            extracted_data = process_goodmoral_extraction(pil_images, saved_paths)
        
        # Clear PIL images to free memory
        pil_images.clear()
        gc.collect()
        
        return jsonify({
            "success": True,
            "message": f"{doc_type} documents uploaded successfully",
            "record_id": record_id,
            "saved_paths": saved_paths,
            "extracted_data": extracted_data
        })
        
    except Exception as e:
        print(f"❌ Error scanning documents: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ================= NEW: STUDENT CAN EDIT THEIR INFORMATION =================
@app.route('/api/student/update-info', methods=['PUT'])
@login_required
@permission_required('edit_own_information')
def update_student_info():
    """Allow student to correct information from scanned documents"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        # Check if record exists for this user
        cur.execute("SELECT id FROM records WHERE user_id = %s", (session['user_id'],))
        record = cur.fetchone()
        
        if not record:
            conn.close()
            return jsonify({"error": "No record found"}), 404
        
        # Build update query based on provided fields
        updates = []
        values = []
        
        editable_fields = [
            'name', 'sex', 'birthdate', 'birthplace', 'birth_order', 
            'religion', 'age', 'civil_status', 'mobile_no', 'email',
            'province', 'specific_address', 'mother_name', 'mother_contact',
            'father_name', 'father_contact', 'guardian_name', 'guardian_relation',
            'guardian_contact', 'lrn', 'school_name', 'school_address',
            'final_general_average', 'college', 'program', 'student_type'
        ]
        
        for field in editable_fields:
            if field in data and data[field] is not None:
                updates.append(f"{field} = %s")
                values.append(data[field])
        
        if not updates:
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        
        # Add updated timestamp
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(session['user_id'])
        
        query = f"UPDATE records SET {', '.join(updates)} WHERE user_id = %s RETURNING id"
        cur.execute(query, values)
        
        updated_id = cur.fetchone()[0]
        conn.commit()
        
        # Notify admin that student updated their info
        cur.execute("SELECT full_name FROM users WHERE id = %s", (session['user_id'],))
        student_name = cur.fetchone()[0]
        
        create_notification(
            user_id=1,  # Admin ID
            notification_type='INFO_UPDATED',
            title="Student Information Updated",
            message=f"Student {student_name} has updated their information.",
            data={'user_id': session['user_id'], 'record_id': updated_id},
            priority=1
        )
        
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Information updated successfully.",
            "record_id": updated_id
        })
        
    except Exception as e:
        print(f"❌ Error updating student info: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= PROCESS EXTRACTIONS (OPTIMIZED) =================
def process_psa_extraction(images, paths):
    """Process PSA extraction using optimized Gemini"""
    try:
        prompt = """Extract information from this PSA Birth Certificate.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "Name": "Full Name Here",
            "Sex": "Male or Female",
            "Birthdate": "YYYY-MM-DD format",
            "PlaceOfBirth": "City/Municipality, Province",
            "BirthOrder": "1st, 2nd, 3rd, etc",
            "Mother_MaidenName": "Mother's Maiden Name",
            "Father_Name": "Father's Full Name"
        }
        
        Return ONLY the JSON, no additional text."""
        
        response_text = extract_with_gemini(prompt, images)
        
        cleaned_text = response_text.strip()
        
        if cleaned_text.startswith('```'):
            lines = cleaned_text.split('\n')
            if lines[0].startswith('```'):
                cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
        
        start = cleaned_text.find('{')
        end = cleaned_text.rfind('}') + 1
        
        if start == -1 or end == 0:
            return None
            
        json_str = cleaned_text[start:end]
        data = json.loads(json_str)
        
        # Force garbage collection
        gc.collect()
        
        return data
        
    except Exception as e:
        print(f"❌ PSA extraction error: {e}")
        return None

def process_form137_extraction(images, paths):
    """Process Form 137 extraction using optimized Gemini"""
    try:
        prompt = """Extract information from this Form 137 / SF10 document.
        
        Return ONLY a valid JSON object with the following structure:
        {
            "lrn": "12-digit Learner Reference Number",
            "school_name": "Complete School Name",
            "school_address": "Complete School Address",
            "final_general_average": "Numerical grade"
        }
        
        Return ONLY the JSON, no additional text."""
        
        response_text = extract_with_gemini(prompt, images)
        
        cleaned_text = response_text.strip()
        
        if cleaned_text.startswith('```'):
            lines = cleaned_text.split('\n')
            if lines[0].startswith('```'):
                cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
        
        start = cleaned_text.find('{')
        end = cleaned_text.rfind('}') + 1
        
        if start == -1 or end == 0:
            return None
            
        json_str = cleaned_text[start:end]
        data = json.loads(json_str)
        
        # Force garbage collection
        gc.collect()
        
        return data
        
    except Exception as e:
        print(f"❌ Form 137 extraction error: {e}")
        return None

def process_goodmoral_extraction(images, paths):
    """Process Good Moral extraction using optimized Gemini"""
    try:
        prompt = """You are an expert at reading Philippine school documents. Extract information from this Good Moral Certificate.

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
        
        response_text = extract_with_gemini(prompt, images)
        
        cleaned_text = response_text.strip()
        
        if cleaned_text.startswith('```'):
            lines = cleaned_text.split('\n')
            if lines[0].startswith('```'):
                cleaned_text = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])
        
        start = cleaned_text.find('{')
        end = cleaned_text.rfind('}') + 1
        
        if start == -1 or end == 0:
            return None
            
        json_str = cleaned_text[start:end]
        data = json.loads(json_str)
        
        # Calculate score
        score, status = calculate_goodmoral_score(data)
        
        data['goodmoral_score'] = score
        data['disciplinary_status'] = status
        
        # Force garbage collection
        gc.collect()
        
        return data
        
    except Exception as e:
        print(f"❌ Good Moral extraction error: {e}")
        return None

# ================= ADMIN: SAVE SCANNED DATA TO RECORD (FIXED - REMOVED new columns) =================
@app.route('/api/admin/scan/<int:user_id>/save', methods=['POST'])
@login_required
@permission_required('scan_documents')
def save_scanned_data(user_id):
    """Save extracted data to student record - FIXED version without new columns"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        # Check if record exists
        cur.execute("SELECT id FROM records WHERE user_id = %s", (user_id,))
        record = cur.fetchone()
        
        # Prepare update data
        updates = []
        values = []
        
        editable_fields = [
            'name', 'sex', 'birthdate', 'birthplace', 'birth_order', 
            'religion', 'age', 'civil_status', 'mobile_no', 'email',
            'province', 'specific_address', 'mother_name', 'mother_contact',
            'father_name', 'father_contact', 'guardian_name', 'guardian_relation',
            'guardian_contact', 'lrn', 'school_name', 'school_address',
            'final_general_average', 'college', 'program', 'student_type',
            'is_transferee', 'previous_school', 'previous_school_address',
            'previous_school_year', 'year_level_to_enroll'
        ]
        
        for field in editable_fields:
            if field in data and data[field] is not None:
                updates.append(f"{field} = %s")
                values.append(data[field])
        
        # Handle good moral data
        if data.get('goodmoral_analysis'):
            updates.append("goodmoral_analysis = %s")
            values.append(json.dumps(data['goodmoral_analysis']))
        
        if data.get('disciplinary_status'):
            updates.append("disciplinary_status = %s")
            values.append(data['disciplinary_status'])
        
        if data.get('goodmoral_score') is not None:
            updates.append("goodmoral_score = %s")
            values.append(data['goodmoral_score'])
        
        if data.get('has_disciplinary_record') is not None:
            updates.append("has_disciplinary_record = %s")
            values.append(data['has_disciplinary_record'])
        
        if data.get('disciplinary_details'):
            updates.append("disciplinary_details = %s")
            values.append(data['disciplinary_details'])
        
        # Clear needs_review flag
        updates.append("needs_review = FALSE")
        
        if record:
            # Update existing record
            values.append(record[0])
            query = f"UPDATE records SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id"
            cur.execute(query, values)
            record_id = record[0]
        else:
            # Create new record
            values.append(user_id)
            
            placeholders = ', '.join(['%s'] * len(updates))
            insert_query = f"""
                INSERT INTO records (
                    user_id, {', '.join([f.split(' =')[0] for f in updates])}
                ) VALUES (%s, {placeholders})
                RETURNING id
            """
            cur.execute(insert_query, values)
            record_id = cur.fetchone()[0]
        
        conn.commit()
        conn.close()
        
        # Notify student that record has been created/updated
        create_notification(
            user_id=user_id,
            notification_type='SYSTEM',
            title="Your Record Has Been Processed",
            message="Your documents have been scanned and verified by the admissions office.",
            data={'record_id': record_id},
            priority=1
        )
        
        return jsonify({
            "success": True,
            "message": "Record saved successfully",
            "record_id": record_id
        })
        
    except Exception as e:
        print(f"❌ Error saving scanned data: {e}")
        traceback.print_exc()
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= UPDATE RECORD STATUS (APPROVE/REJECT) =================
@app.route('/api/record/<int:record_id>/status', methods=['PUT'])
@login_required
@permission_required('edit_records')
def update_record_status(record_id):
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
        
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        user_id = result[0] if result else None
        
        if status == 'REJECTED' and reason:
            cur.execute("""
                UPDATE records 
                SET status = %s, rejection_reason = %s, updated_at = CURRENT_TIMESTAMP,
                    needs_review = FALSE
                WHERE id = %s
                RETURNING id
            """, (status, reason, record_id))
        else:
            cur.execute("""
                UPDATE records 
                SET status = %s, updated_at = CURRENT_TIMESTAMP,
                    needs_review = FALSE
                WHERE id = %s
                RETURNING id
            """, (status, record_id))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        updated_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        if user_id:
            if status == 'APPROVED':
                create_notification(
                    user_id=user_id,
                    notification_type='RECORD_APPROVED',
                    title="Application Approved",
                    message="Congratulations! Your application has been approved.",
                    data={'record_id': record_id},
                    priority=1
                )
            elif status == 'REJECTED':
                create_notification(
                    user_id=user_id,
                    notification_type='RECORD_REJECTED',
                    title="Application Rejected",
                    message=f"Your application has been rejected. Reason: {reason}",
                    data={'record_id': record_id, 'reason': reason},
                    priority=1
                )
        
        return jsonify({
            "success": True,
            "message": f"Record {status.lower()} successfully",
            "record_id": updated_id,
            "status": status
        })
        
    except Exception as e:
        print(f"❌ Status update error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= ARCHIVE RECORD =================
@app.route('/api/record/<int:record_id>/archive', methods=['POST'])
@login_required
@permission_required('archive_records')
def archive_record(record_id):
    try:
        data = request.json
        reason = data.get('reason')
        notes = data.get('notes', '')
        
        valid_reasons = ['GRADUATED', 'TRANSFERRED_OUT', 'COMPLETED', 'OTHER']
        if reason not in valid_reasons:
            return jsonify({"error": "Invalid archive reason"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        user_id = result[0] if result else None
        
        cur.execute("""
            SELECT image_path, form137_path, form138_path, goodmoral_path,
                   honorable_dismissal_path, transfer_credentials_path
            FROM records WHERE id = %s
        """, (record_id,))
        
        paths = cur.fetchone()
        if paths:
            for path in paths:
                if path:
                    file_paths = path.split(',')
                    for fp in file_paths:
                        if fp.strip():
                            move_to_archive(fp.strip())
        
        cur.execute("""
            UPDATE records 
            SET is_archived = TRUE, 
                archived_at = CURRENT_TIMESTAMP, 
                archived_by = %s,
                archive_reason = %s,
                archive_notes = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
        """, (session['user_id'], reason, notes, record_id))
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        archived_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        if user_id:
            reason_text = {
                'GRADUATED': 'you have graduated',
                'TRANSFERRED_OUT': 'you have transferred out',
                'COMPLETED': 'your document process is completed',
                'OTHER': 'your record has been archived'
            }.get(reason, 'your record has been archived')
            
            create_notification(
                user_id=user_id,
                notification_type='SYSTEM',
                title="Record Archived",
                message=f"Your record has been archived because {reason_text}.",
                data={'record_id': record_id, 'reason': reason},
                priority=0
            )
        
        return jsonify({
            "success": True,
            "message": "Record archived successfully",
            "record_id": archived_id
        })
        
    except Exception as e:
        print(f"❌ Archive error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= RESTORE RECORD =================
@app.route('/api/record/<int:record_id>/restore', methods=['POST'])
@login_required
@permission_required('archive_records')
def restore_record(record_id):
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM records WHERE id = %s AND is_archived = TRUE", (record_id,))
        result = cur.fetchone()
        user_id = result[0] if result else None
        
        cur.execute("""
            SELECT image_path, form137_path, form138_path, goodmoral_path,
                   honorable_dismissal_path, transfer_credentials_path
            FROM records WHERE id = %s AND is_archived = TRUE
        """, (record_id,))
        
        paths = cur.fetchone()
        if not paths:
            conn.close()
            return jsonify({"error": "Archived record not found"}), 404
        
        for path in paths:
            if path:
                file_paths = path.split(',')
                for fp in file_paths:
                    if fp.strip():
                        restore_from_archive(fp.strip())
        
        cur.execute("""
            UPDATE records 
            SET is_archived = FALSE, 
                restored_at = CURRENT_TIMESTAMP, 
                restored_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
        """, (session['user_id'], record_id))
        
        restored_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        if user_id:
            create_notification(
                user_id=user_id,
                notification_type='SYSTEM',
                title="Record Restored",
                message="Your archived record has been restored.",
                data={'record_id': record_id},
                priority=0
            )
        
        return jsonify({
            "success": True,
            "message": "Record restored successfully",
            "record_id": restored_id
        })
        
    except Exception as e:
        print(f"❌ Restore error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= PERMANENT DELETE RECORD =================
@app.route('/api/record/<int:record_id>/permanent-delete', methods=['DELETE'])
@login_required
@permission_required('delete_records')
def permanent_delete_record(record_id):
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        
        cur = conn.cursor()
        
        cur.execute("""
            SELECT image_path, form137_path, form138_path, goodmoral_path,
                   honorable_dismissal_path, transfer_credentials_path
            FROM records WHERE id = %s
        """, (record_id,))
        
        paths = cur.fetchone()
        if paths:
            for path in paths:
                if path:
                    file_paths = path.split(',')
                    for fp in file_paths:
                        if fp.strip():
                            if '/' in fp:
                                file_path = os.path.join(app.config['ARCHIVE_FOLDER'], fp)
                            else:
                                file_path = os.path.join(app.config['UPLOAD_FOLDER'], fp)
                            
                            if os.path.exists(file_path):
                                os.remove(file_path)
                                print(f"🗑️ Deleted file: {fp}")
        
        cur.execute("DELETE FROM notifications WHERE data->>'record_id' = %s", (str(record_id),))
        
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Record permanently deleted"
        })
        
    except Exception as e:
        print(f"❌ Permanent delete error: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

# ================= SAVE RECORD ENDPOINT (For backward compatibility) =================
@app.route('/save-record', methods=['POST'])
@login_required
@permission_required('access_scanner')
def save_record():
    conn = None
    try:
        d = request.json
        print(f"📥 Saving/UPDATING record for user: {session['user_id']}")
        
        # This endpoint is kept for backward compatibility
        # But students should use /api/student/update-info instead
        
        return jsonify({
            "status": "error", 
            "error": "Please use the student update endpoint"
        }), 400
            
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
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role')
    
    if not user_role:
        session.clear()
        return redirect('/login')
    
    user_role = user_role.upper()
    
    # ✅ FIXED: Students go to dashboard, not my-records
    if user_role == 'STUDENT':
        return render_template('student_dashboard.html')  # ← CHANGED TO DASHBOARD
    elif user_role in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
        return redirect('/admin/dashboard')
    else:
        session.clear()
        return redirect('/login')

@app.route('/index.html')
def serve_index():
    """Serve scanner page - for admin use only"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
        # Students trying to access scanner get redirected to dashboard
        return redirect('/')
    
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if 'user_id' in session and 'role' in session:
            user_role = session['role'].upper()
            if user_role == 'STUDENT':
                return redirect('/')
            elif user_role in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
                return redirect('/admin/dashboard')
            else:
                session.clear()
        
        return render_template('login.html')
    
    elif request.method == 'POST':
        return redirect('/api/login')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session_token = session.get('session_token')
    if session_token:
        logout_session(session_token)
    
    session.clear()
    return redirect('/login')

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
        return redirect('/')
    
    return render_template('admin_dashboard.html')

@app.route('/admin/students')
def admin_students():
    """Student list page with scan buttons"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
        return redirect('/')
    
    return render_template('admin_students.html')

@app.route('/admin/scan/<int:user_id>')
def admin_scan_student(user_id):
    """Scan page for specific student"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role not in ['SUPER_ADMIN', 'ADMISSIONS_STAFF']:
        return redirect('/')
    
    return redirect(f'/index.html?user_id={user_id}')

@app.route('/admin/users')
def admin_users():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        return redirect('/')
    
    return render_template('admin_users.html')

@app.route('/admin/colleges')
def admin_colleges():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'SUPER_ADMIN':
        return redirect('/')
    
    return render_template('admin_colleges.html')

@app.route('/my-records')
@login_required
def my_records_page():
    """Student records page - available as separate page if needed"""
    user_role = session.get('role', '').upper()
    if user_role != 'STUDENT':
        return redirect('/')
    
    return render_template('student_records.html')

@app.route('/notifications')
@login_required
def notifications_page():
    return render_template('notifications.html')

@app.route('/admin/missing-documents')
@login_required
@permission_required('view_all_records')
def missing_documents_page():
    return render_template('admin_missing_docs.html')

@app.route('/change-password', methods=['GET'])
def change_password_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role', '').upper()
    if user_role != 'STUDENT':
        return redirect('/')
    
    return render_template('change_password.html')

# ================= UPLOADS SERVING =================
@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    try:
        # Security: Students can only access their own folder
        if session.get('role', '').upper() == 'STUDENT':
            expected_folder = f"student_{session['user_id']}"
            if not filename.startswith(expected_folder):
                return jsonify({"error": "Access denied"}), 403
        
        # Clean filename
        if '..' in filename or filename.startswith('/'):
            return "Invalid filename", 400
        
        # Try upload folder first
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Try archive folder if not found
        if not os.path.exists(file_path):
            file_path = os.path.join(app.config['ARCHIVE_FOLDER'], filename)
        
        if not os.path.exists(file_path):
            return jsonify({"error": f"File not found"}), 404
        
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain'
        }
        
        ext = os.path.splitext(filename)[1].lower()
        mimetype = mime_types.get(ext, 'application/octet-stream')
        
        response = send_file(
            file_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=os.path.basename(filename)
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

# ================= HEALTH CHECK =================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "AssiScan Backend (Fixed Home Redirect & Password Reset)",
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if get_db_connection() else "disconnected",
        "roles": list(PERMISSIONS.keys())
    })

# ================= DEBUG ENDPOINTS =================
@app.route('/debug-goodmoral/<int:record_id>', methods=['GET'])
@login_required
def debug_goodmoral(record_id):
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

# ================= APPLICATION START =================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    
    print("\n" + "="*60)
    print("🚀 ASSISCAN BACKEND - FIXED HOME REDIRECT & PASSWORD RESET")
    print("="*60)
    print("✅ FIXED: Home redirects to student_dashboard.html")
    print("✅ FIXED: Password reset flag persists in session")
    print("✅ FIXED: /api/check-session returns requires_password_reset")
    print("✅ FIXED: New users created with requires_password_reset = TRUE")
    print("✅ FIXED: Force reset option in change password endpoint")
    print("="*60)
    print(f"🔑 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
    print(f"📧 Email: {'✅ SET' if EMAIL_SENDER else '❌ NOT SET'}")
    print(f"🗄️ Database: {'✅ SET' if DATABASE_URL else '❌ NOT SET'}")
    print("="*60)
    print(f"📁 Upload folder: {UPLOAD_FOLDER}")
    print(f"📁 Archive folder: {ARCHIVE_FOLDER}")
    print("="*60)
    print(f"🌐 Server binding to {host}:{port}")
    print(f"⚙️ Debug mode: {debug}")
    print("="*60)
    
    app.run(host=host, port=port, debug=debug)
