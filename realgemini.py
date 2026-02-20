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

# ================= CONFIGURATION =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# Configure Gemini - REMOVED list_models() to save memory
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ Google Generative AI Configured")
    except Exception as e:
        print(f"⚠️ Error configuring Gemini: {e}")
else:
    print("❌ CRITICAL: GEMINI_API_KEY is missing!")

# Admin credentials
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# CORS
CORS(app, resources={r"/*": {"origins": "*"}})

# Upload folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB max (reduced from 50MB)

# ================= USER ROLES =================
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

# ================= DECORATORS =================
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
            if session.get('role') != required_role:
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

# ================= DATABASE CONNECTION =================
def get_db_connection():
    """Get database connection with retry logic"""
    max_retries = 2
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            if not DATABASE_URL:
                return None
            
            if DATABASE_URL.startswith("postgres://"):
                DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=5)
            return conn
        except Exception as e:
            print(f"❌ DB Error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    return None

# ================= SIMPLE TABLE CHECK (NO DROPPING) =================
def check_tables_exist():
    """Quick check if tables exist"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users')")
        users_exist = cur.fetchone()[0]
        
        if not users_exist:
            conn.close()
            return False
        
        # Check if citizenship column exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_name='records' AND column_name='citizenship'
            )
        """)
        citizenship_exists = cur.fetchone()[0]
        
        conn.close()
        return users_exist and citizenship_exists
    except Exception as e:
        print(f"❌ Check tables error: {e}")
        conn.close()
        return False

# ================= INIT DATABASE (IF NOT EXISTS) =================
def init_db():
    """Initialize database tables if they don't exist"""
    print("🔧 Initializing database...")
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Create tables if not exists
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
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
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                session_token VARCHAR(255) UNIQUE NOT NULL,
                ip_address VARCHAR(45),
                user_agent TEXT,
                login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                logout_at TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS colleges (
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
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS programs (
                id SERIAL PRIMARY KEY,
                college_id INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
                code VARCHAR(50),
                name VARCHAR(150) NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER REFERENCES users(id)
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(255),
                sex VARCHAR(50),
                birthdate DATE,
                birthplace TEXT,
                birth_order VARCHAR(50),
                religion VARCHAR(100),
                citizenship VARCHAR(100) DEFAULT 'Filipino',
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
                document_status JSONB DEFAULT '{"psa":false,"form137":false,"form138":false,"goodmoral":false}',
                rejection_reason TEXT,
                status VARCHAR(20) DEFAULT 'INCOMPLETE'
            )
        ''')
        
        # Add citizenship column if not exists (for existing databases)
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='records' AND column_name='citizenship'
        """)
        if not cur.fetchone():
            print("➕ Adding citizenship column...")
            cur.execute("ALTER TABLE records ADD COLUMN citizenship VARCHAR(100) DEFAULT 'Filipino'")
        
        conn.commit()
        
        # Create admin user if not exists
        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            password_hash = hash_password(ADMIN_PASSWORD)
            cur.execute("""
                INSERT INTO users (username, password_hash, full_name, email, role, is_active, requires_password_reset)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (ADMIN_USERNAME, password_hash, 'System Administrator', 'admin@assiscan.com', 'SUPER_ADMIN', True, False))
            admin_id = cur.fetchone()[0]
            
            # Insert default colleges
            default_colleges = [
                ("CCJE", "College of Criminal Justice Education", 1),
                ("CEAS", "College of Education, Arts and Sciences", 2),
                ("CITEC", "College of Information Technology", 3),
                ("CENAR", "College of Engineering and Architecture", 4),
                ("CBAA", "College of Business and Accountancy", 5)
            ]
            
            for code, name, order in default_colleges:
                cur.execute("INSERT INTO colleges (code, name, display_order, created_by) VALUES (%s, %s, %s, %s) RETURNING id", 
                          (code, name, order, admin_id))
                college_id = cur.fetchone()[0]
                
                if code == "CCJE":
                    cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                               (college_id, "BS Criminology", 1, admin_id))
                elif code == "CEAS":
                    programs = ["BEEd", "BSEd", "BS Psychology", "BS Legal Management"]
                    for i, p in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, p, i+1, admin_id))
                elif code == "CITEC":
                    cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                               (college_id, "BS Information Technology", 1, admin_id))
                elif code == "CENAR":
                    programs = ["BS Industrial Engineering", "BS Computer Engineering", "BS Architecture"]
                    for i, p in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, p, i+1, admin_id))
                elif code == "CBAA":
                    programs = ["BS Business Administration", "BS Accountancy"]
                    for i, p in enumerate(programs):
                        cur.execute("INSERT INTO programs (college_id, name, display_order, created_by) VALUES (%s, %s, %s, %s)",
                                   (college_id, p, i+1, admin_id))
        
        conn.commit()
        conn.close()
        print("✅ Database ready")
        return True
    except Exception as e:
        print(f"❌ Database init error: {e}")
        conn.rollback()
        conn.close()
        return False

# Initialize database on startup (NO DROPPING!)
if not check_tables_exist():
    print("⚠️ Tables missing, initializing database...")
    init_db()
else:
    print("✅ Database tables ready")

# ================= PASSWORD FUNCTIONS =================
def hash_password(password):
    salt = secrets.token_hex(16)
    return salt + "$" + hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(stored_hash, password):
    if "$" not in stored_hash:
        return False
    salt, hash_value = stored_hash.split("$", 1)
    return hash_value == hashlib.sha256((password + salt).encode()).hexdigest()

def generate_temp_password(length=8):
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ================= SESSION FUNCTIONS =================
def create_session(user_id, ip_address=None, user_agent=None):
    session_token = secrets.token_urlsafe(32)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_sessions (user_id, session_token, ip_address, user_agent)
            VALUES (%s, %s, %s, %s)
        """, (user_id, session_token, ip_address, user_agent))
        conn.commit()
        return session_token
    except Exception as e:
        print(f"❌ Session error: {e}")
        return None
    finally:
        conn.close()

def validate_session(session_token):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT u.* FROM user_sessions us
            JOIN users u ON us.user_id = u.id
            WHERE us.session_token = %s AND us.is_active = TRUE AND u.is_active = TRUE
        """, (session_token,))
        return cur.fetchone()
    except Exception as e:
        print(f"❌ Session validation error: {e}")
        return None
    finally:
        conn.close()

def logout_session(session_token):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_sessions SET logout_at = CURRENT_TIMESTAMP, is_active = FALSE WHERE session_token = %s", (session_token,))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

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
        print(f"⚠️ Error reading school year: {e}")
    return default_year

def save_school_year(school_year):
    try:
        with open(SCHOOL_YEAR_FILE, 'w') as f:
            json.dump({'school_year': school_year, 'updated_at': datetime.now().isoformat()}, f)
        return True
    except Exception as e:
        print(f"❌ Error saving school year: {e}")
        return False

@app.route('/api/settings/school-year', methods=['GET'])
@login_required
def get_school_year_endpoint():
    return jsonify({"school_year": get_school_year(), "success": True})

@app.route('/api/settings/school-year', methods=['POST'])
@login_required
@permission_required('manage_settings')
def set_school_year():
    data = request.json
    school_year = data.get('school_year')
    
    if not school_year:
        return jsonify({"error": "School year required"}), 400
    
    if not re.match(r'^\d{4}-\d{4}$', school_year):
        return jsonify({"error": "Invalid format. Use YYYY-YYYY"}), 400
    
    start_year, end_year = map(int, school_year.split('-'))
    if end_year != start_year + 1:
        return jsonify({"error": "End year must be year after start year"}), 400
    
    if save_school_year(school_year):
        return jsonify({"success": True, "school_year": school_year})
    return jsonify({"error": "Failed to save"}), 500

# ================= AUTH ROUTES =================
@app.route('/api/check-session', methods=['GET'])
def check_session():
    if 'user_id' not in session or 'session_token' not in session:
        return jsonify({"authenticated": False})
    
    user = validate_session(session['session_token'])
    if user:
        return jsonify({
            "authenticated": True,
            "user": {
                'id': user['id'],
                'username': user['username'],
                'full_name': user['full_name'],
                'email': user['email'],
                'role': user['role']
            },
            "permissions": PERMISSIONS.get(user['role'].upper(), [])
        })
    return jsonify({"authenticated": False})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username = %s OR email = %s", (username, username))
        user = cur.fetchone()
        
        if not user or not verify_password(user['password_hash'], password) or not user['is_active']:
            conn.close()
            return jsonify({"error": "Invalid credentials"}), 401
        
        session_token = create_session(user['id'], request.remote_addr, request.headers.get('User-Agent'))
        
        if not session_token:
            conn.close()
            return jsonify({"error": "Session creation failed"}), 500
        
        cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user['id'],))
        conn.commit()
        conn.close()
        
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['full_name'] = user['full_name']
        session['role'] = user['role']
        session['session_token'] = session_token
        session['requires_password_reset'] = user['requires_password_reset']
        
        return jsonify({
            "status": "success",
            "user": {
                'id': user['id'],
                'username': user['username'],
                'full_name': user['full_name'],
                'email': user['email'],
                'role': user['role'],
                'requires_password_reset': user['requires_password_reset']
            },
            "session_token": session_token,
            "permissions": PERMISSIONS.get(user['role'].upper(), [])
        })
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session_token = session.get('session_token')
    if session_token:
        logout_session(session_token)
    session.clear()
    return jsonify({"status": "success"})

@app.route('/logout', methods=['GET'])
def logout_redirect():
    session_token = session.get('session_token')
    if session_token:
        logout_session(session_token)
    session.clear()
    return redirect('/login')

@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({"error": "All fields required"}), 400
    
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        if not verify_password(user['password_hash'], current_password):
            conn.close()
            return jsonify({"error": "Current password is incorrect"}), 401
        
        new_hash = hash_password(new_password)
        cur.execute("UPDATE users SET password_hash = %s, requires_password_reset = FALSE WHERE id = %s", 
                   (new_hash, session['user_id']))
        conn.commit()
        conn.close()
        
        session['requires_password_reset'] = False
        
        return jsonify({"status": "success", "message": "Password changed successfully"})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-password-reset', methods=['GET'])
@login_required
def check_password_reset():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT requires_password_reset FROM users WHERE id = %s", (session['user_id'],))
        result = cur.fetchone()
        conn.close()
        return jsonify({"requires_password_reset": result[0] if result else False})
    except:
        conn.close()
        return jsonify({"requires_password_reset": False})

# ================= USER MANAGEMENT ROUTES =================
@app.route('/api/users', methods=['GET'])
@login_required
@permission_required('manage_users')
def get_users():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                   u.requires_password_reset, u.last_login, u.created_at,
                   c.name as college_name, p.name as program_name
            FROM users u
            LEFT JOIN colleges c ON u.college_id = c.id
            LEFT JOIN programs p ON u.program_id = p.id
            ORDER BY u.created_at DESC
        """)
        users = cur.fetchall()
        conn.close()
        return jsonify(users)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/users', methods=['POST'])
@login_required
@permission_required('manage_users')
def create_user():
    data = request.json
    
    if not data.get('username') or not data.get('full_name') or not data.get('email') or not data.get('role'):
        return jsonify({"error": "All fields required"}), 400
    
    if data['role'] not in ['SUPER_ADMIN', 'STUDENT']:
        return jsonify({"error": "Invalid role"}), 400
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM users WHERE username = %s OR email = %s", (data['username'], data['email']))
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "Username or email already exists"}), 409
        
        temp_password = generate_temp_password()
        password_hash = hash_password(temp_password)
        
        cur.execute("""
            INSERT INTO users (username, password_hash, full_name, email, role, college_id, program_id, is_active, requires_password_reset, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, username, full_name, email, role, is_active, created_at
        """, (
            data['username'], password_hash, data['full_name'], data['email'], data['role'],
            data.get('college_id'), data.get('program_id'), data.get('is_active', True), True, session['user_id']
        ))
        
        new_user = cur.fetchone()
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "user": {
                'id': new_user[0], 'username': new_user[1], 'full_name': new_user[2],
                'email': new_user[3], 'role': new_user[4], 'is_active': new_user[5]
            },
            "temp_password": temp_password
        })
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

# ================= COLLEGE ROUTES =================
@app.route('/api/colleges-dropdown', methods=['GET'])
def get_colleges_dropdown():
    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, code, name FROM colleges WHERE is_active = TRUE ORDER BY display_order, name")
        colleges = cur.fetchall()
        
        for college in colleges:
            cur.execute("SELECT id, name FROM programs WHERE college_id = %s AND is_active = TRUE ORDER BY display_order, name", 
                       (college['id'],))
            college['programs'] = cur.fetchall()
        
        conn.close()
        return jsonify(colleges)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# ================= HELPER FUNCTIONS =================
def save_single_file(file, prefix):
    """Save a single file - memory efficient"""
    if not file or not file.filename:
        return None, None
    
    timestamp = int(datetime.now().timestamp())
    filename = secure_filename(f"{prefix}_{timestamp}_{file.filename}")
    path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(path)
    
    try:
        img = Image.open(path)
        # Resize large images to save memory
        if img.width > 1024 or img.height > 1024:
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            img.save(path, optimize=True, quality=85)
        return filename, img
    except Exception as e:
        print(f"Error opening image: {e}")
        return filename, None

def extract_with_gemini(prompt, image):
    """Use Gemini for text extraction - single image"""
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY not configured")
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content([prompt, image])
    return response.text

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
        cur.execute("SELECT document_status FROM records WHERE id = %s", (record_id,))
        result = cur.fetchone()
        
        if result and result[0]:
            try:
                status = json.loads(result[0])
            except:
                status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        else:
            status = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        status[doc_type] = has_file
        cur.execute("UPDATE records SET document_status = %s WHERE id = %s", (json.dumps(status), record_id))
        conn.commit()
        conn.close()
        return status
    except Exception as e:
        print(f"❌ Error updating document status: {e}")
        conn.close()
        return None

# ================= PSA EXTRACTION =================
@app.route('/extract', methods=['POST'])
@login_required
@permission_required('access_scanner')
def extract_data():
    if 'imageFiles' not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Process only first image
    filename, img = save_single_file(files[0], "PSA")
    if not img:
        return jsonify({"error": "Invalid image"}), 400
    
    try:
        prompt = """Extract information from this PSA Birth Certificate.
        
        Return ONLY JSON with:
        {
            "Name": "...", "Sex": "...", "Birthdate": "YYYY-MM-DD",
            "PlaceOfBirth": "...", "BirthOrder": "...", "Citizenship": "Filipino",
            "Mother_MaidenName": "...", "Mother_Citizenship": "...", "Mother_Occupation": "...",
            "Father_Name": "...", "Father_Citizenship": "...", "Father_Occupation": "..."
        }"""
        
        response_text = extract_with_gemini(prompt, img)
        
        # Extract JSON
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": filename
            })
        
        return jsonify({"error": "Failed to extract data"}), 500
        
    except Exception as e:
        print(f"❌ PSA Error: {e}")
        return jsonify({"error": str(e)[:200]}), 500

# ================= FORM 137 EXTRACTION =================
@app.route('/extract-form137', methods=['POST'])
@login_required
@permission_required('access_scanner')
def extract_form137():
    if 'imageFiles' not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    filename, img = save_single_file(files[0], "F137")
    if not img:
        return jsonify({"error": "Invalid image"}), 400
    
    try:
        prompt = """Extract information from this Form 137 / SF10 document.
        
        Return ONLY JSON with:
        {
            "lrn": "...", "school_name": "...", 
            "school_address": "...", "final_general_average": "..."
        }"""
        
        response_text = extract_with_gemini(prompt, img)
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return jsonify({
                "message": "Success", 
                "structured_data": data, 
                "image_paths": filename
            })
        
        return jsonify({"error": "Failed to extract data"}), 500
        
    except Exception as e:
        print(f"❌ Form137 Error: {e}")
        return jsonify({"error": str(e)[:200]}), 500

# ================= GOOD MORAL SCANNING =================
@app.route('/scan-goodmoral', methods=['POST'])
@login_required
@permission_required('access_scanner')
def scan_goodmoral():
    if 'imageFiles' not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('imageFiles')
    if not files or files[0].filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    filename, img = save_single_file(files[0], "GOODMORAL")
    if not img:
        return jsonify({"error": "Invalid image"}), 400
    
    try:
        prompt = """Extract information from this Good Moral Certificate.
        
        Return ONLY JSON with:
        {
            "issuing_school": "...", "issuing_officer": "...",
            "issued_date": "...", "student_name": "...",
            "has_disciplinary_record": false, "disciplinary_details": "",
            "remarks": ""
        }"""
        
        response_text = extract_with_gemini(prompt, img)
        
        score = 100
        status = "GOOD"
        has_record = False
        details = ""
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
            has_record = analysis.get('has_disciplinary_record', False)
            details = analysis.get('disciplinary_details', '') or analysis.get('remarks', '')
            
            if has_record:
                score -= 40
            if 'suspended' in details.lower() or 'expelled' in details.lower():
                score -= 30
            
            score = max(0, min(100, score))
            
            if score >= 90:
                status = 'EXCELLENT'
            elif score >= 70:
                status = 'GOOD'
            elif score >= 50:
                status = 'FAIR'
            else:
                status = 'POOR'
        
        return jsonify({
            "message": "Good Moral analyzed",
            "analysis": {"has_disciplinary_record": has_record, "remarks": details},
            "goodmoral_score": score,
            "disciplinary_status": status,
            "has_disciplinary_record": has_record,
            "disciplinary_details": details,
            "image_paths": filename
        })
        
    except Exception as e:
        print(f"❌ Good Moral Error: {e}")
        return jsonify({"error": str(e)[:200]}), 500

# ================= SAVE RECORD =================
@app.route('/save-record', methods=['POST'])
@login_required
@permission_required('access_scanner')
def save_record():
    data = request.json
    user_id = session['user_id']
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor()
        
        # Check if record exists
        cur.execute("SELECT id FROM records WHERE user_id = %s", (user_id,))
        existing = cur.fetchone()
        
        # Prepare data
        religion = data.get('religion', '')
        citizenship = data.get('citizenship', 'Filipino')
        siblings = json.dumps(data.get('siblings', []))
        
        # Document status
        doc_status = {
            "psa": bool(data.get('psa_image_path')),
            "form137": bool(data.get('f137_image_path')),
            "form138": False,
            "goodmoral": bool(data.get('goodmoral_image_path'))
        }
        
        all_docs = all([doc_status['psa'], doc_status['form137'], doc_status['goodmoral']])
        record_status = 'PENDING' if all_docs else 'INCOMPLETE'
        
        if existing:
            # Update
            cur.execute('''
                UPDATE records SET
                    name=%s, sex=%s, birthdate=%s, birthplace=%s,
                    birth_order=%s, religion=%s, citizenship=%s, age=%s,
                    mother_name=%s, mother_citizenship=%s, mother_occupation=%s,
                    father_name=%s, father_citizenship=%s, father_occupation=%s,
                    lrn=%s, school_name=%s, school_address=%s, final_general_average=%s,
                    image_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(image_path,''), CASE WHEN image_path!='' THEN ',' ELSE '' END, %s)
                             ELSE image_path END, image_path),
                    form137_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(form137_path,''), CASE WHEN form137_path!='' THEN ',' ELSE '' END, %s)
                             ELSE form137_path END, form137_path),
                    goodmoral_path = COALESCE(
                        CASE WHEN %s IS NOT NULL AND %s != '' 
                             THEN CONCAT(COALESCE(goodmoral_path,''), CASE WHEN goodmoral_path!='' THEN ',' ELSE '' END, %s)
                             ELSE goodmoral_path END, goodmoral_path),
                    email=%s, mobile_no=%s, civil_status=%s, nationality=%s,
                    mother_contact=%s, father_contact=%s,
                    guardian_name=%s, guardian_relation=%s, guardian_contact=%s,
                    region=%s, province=%s, specific_address=%s,
                    school_year=%s, student_type=%s, college=%s, program=%s,
                    last_level_attended=%s, is_ip=%s, is_pwd=%s,
                    has_medication=%s, is_working=%s, residence_type=%s,
                    employer_name=%s, marital_status=%s, is_gifted=%s,
                    needs_assistance=%s, school_type=%s, year_attended=%s,
                    special_talents=%s, is_scholar=%s, siblings=%s,
                    goodmoral_analysis=%s, disciplinary_status=%s,
                    goodmoral_score=%s, has_disciplinary_record=%s,
                    disciplinary_details=%s, other_documents=%s,
                    document_status=%s, status=%s, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=%s
                RETURNING id
            ''', (
                data.get('name'), data.get('sex'), data.get('birthdate'), data.get('birthplace'),
                data.get('birth_order'), religion, citizenship, data.get('age'),
                data.get('mother_name'), data.get('mother_citizenship'), data.get('mother_occupation'),
                data.get('father_name'), data.get('father_citizenship'), data.get('father_occupation'),
                data.get('lrn'), data.get('school_name'), data.get('school_address'), data.get('final_general_average'),
                data.get('psa_image_path'), data.get('psa_image_path'), data.get('psa_image_path'),
                data.get('f137_image_path'), data.get('f137_image_path'), data.get('f137_image_path'),
                data.get('goodmoral_image_path'), data.get('goodmoral_image_path'), data.get('goodmoral_image_path'),
                data.get('email'), data.get('mobile_no'), data.get('civil_status'), data.get('nationality'),
                data.get('mother_contact'), data.get('father_contact'),
                data.get('guardian_name'), data.get('guardian_relation'), data.get('guardian_contact'),
                data.get('region'), data.get('province'), data.get('specific_address'),
                data.get('school_year'), data.get('student_type'), data.get('college'), data.get('program'),
                data.get('last_level_attended'), data.get('is_ip'), data.get('is_pwd'),
                data.get('has_medication'), data.get('is_working'), data.get('residence_type'),
                data.get('employer_name'), data.get('marital_status'), data.get('is_gifted'),
                data.get('needs_assistance'), data.get('school_type'), data.get('year_attended'),
                data.get('special_talents'), data.get('is_scholar'), siblings,
                json.dumps(data.get('goodmoral_analysis')) if data.get('goodmoral_analysis') else None,
                data.get('disciplinary_status'), data.get('goodmoral_score'),
                data.get('has_disciplinary_record'), data.get('disciplinary_details'),
                json.dumps(data.get('other_documents')) if data.get('other_documents') else None,
                json.dumps(doc_status), record_status, user_id
            ))
            record_id = cur.fetchone()[0]
        else:
            # Insert
            cur.execute('''
                INSERT INTO records (
                    user_id, name, sex, birthdate, birthplace, birth_order,
                    religion, citizenship, age, mother_name, mother_citizenship,
                    mother_occupation, father_name, father_citizenship, father_occupation,
                    lrn, school_name, school_address, final_general_average,
                    image_path, form137_path, goodmoral_path,
                    email, mobile_no, civil_status, nationality,
                    mother_contact, father_contact, guardian_name,
                    guardian_relation, guardian_contact, region, province,
                    specific_address, school_year, student_type, college, program,
                    last_level_attended, is_ip, is_pwd, has_medication,
                    is_working, residence_type, employer_name, marital_status,
                    is_gifted, needs_assistance, school_type, year_attended,
                    special_talents, is_scholar, siblings,
                    goodmoral_analysis, disciplinary_status, goodmoral_score,
                    has_disciplinary_record, disciplinary_details,
                    other_documents, document_status, status
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s
                ) RETURNING id
            ''', (
                user_id, data.get('name'), data.get('sex'), data.get('birthdate'), data.get('birthplace'),
                data.get('birth_order'), religion, citizenship, data.get('age'),
                data.get('mother_name'), data.get('mother_citizenship'), data.get('mother_occupation'),
                data.get('father_name'), data.get('father_citizenship'), data.get('father_occupation'),
                data.get('lrn'), data.get('school_name'), data.get('school_address'), data.get('final_general_average'),
                data.get('psa_image_path'), data.get('f137_image_path'), data.get('goodmoral_image_path'),
                data.get('email'), data.get('mobile_no'), data.get('civil_status'), data.get('nationality'),
                data.get('mother_contact'), data.get('father_contact'),
                data.get('guardian_name'), data.get('guardian_relation'), data.get('guardian_contact'),
                data.get('region'), data.get('province'), data.get('specific_address'),
                data.get('school_year'), data.get('student_type'), data.get('college'), data.get('program'),
                data.get('last_level_attended'), data.get('is_ip'), data.get('is_pwd'),
                data.get('has_medication'), data.get('is_working'), data.get('residence_type'),
                data.get('employer_name'), data.get('marital_status'), data.get('is_gifted'),
                data.get('needs_assistance'), data.get('school_type'), data.get('year_attended'),
                data.get('special_talents'), data.get('is_scholar'), siblings,
                json.dumps(data.get('goodmoral_analysis')) if data.get('goodmoral_analysis') else None,
                data.get('disciplinary_status'), data.get('goodmoral_score'),
                data.get('has_disciplinary_record'), data.get('disciplinary_details'),
                json.dumps(data.get('other_documents')) if data.get('other_documents') else None,
                json.dumps(doc_status), record_status
            ))
            record_id = cur.fetchone()[0]
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "db_id": record_id,
            "document_status": doc_status,
            "record_status": record_status
        })
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"❌ Save error: {e}")
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

# ================= GET STUDENT RECORDS =================
@app.route('/api/my-records', methods=['GET'])
@login_required
@role_required('STUDENT')
def get_my_records():
    conn = get_db_connection()
    if not conn:
        return jsonify({"records": []}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1", (session['user_id'],))
        records = cur.fetchall()
        conn.close()
        
        for r in records:
            if r.get('document_status') and isinstance(r['document_status'], str):
                try:
                    r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        return jsonify({"records": records})
    except Exception as e:
        conn.close()
        return jsonify({"records": [], "error": str(e)}), 500

# ================= GET ALL RECORDS =================
@app.route('/get-records', methods=['GET'])
@login_required
@role_required('SUPER_ADMIN')
def get_records():
    conn = get_db_connection()
    if not conn:
        return jsonify({"records": []}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ON (r.user_id) r.*, u.username, u.full_name as user_full_name
            FROM records r
            JOIN users u ON r.user_id = u.id
            WHERE u.role = 'STUDENT'
            ORDER BY r.user_id, r.updated_at DESC
        """)
        records = cur.fetchall()
        conn.close()
        
        for r in records:
            if r.get('document_status') and isinstance(r['document_status'], str):
                try:
                    r['document_status'] = json.loads(r['document_status'])
                except:
                    r['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        return jsonify({"records": records})
    except Exception as e:
        conn.close()
        return jsonify({"records": [], "error": str(e)}), 500

# ================= GET SINGLE RECORD =================
@app.route('/api/record/<int:record_id>', methods=['GET'])
@login_required
def get_single_record(record_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        if session.get('role') == 'STUDENT':
            cur.execute("SELECT * FROM records WHERE id = %s AND user_id = %s", (record_id, session['user_id']))
        else:
            cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        
        record = cur.fetchone()
        conn.close()
        
        if not record:
            return jsonify({"error": "Record not found"}), 404
        
        if record.get('document_status') and isinstance(record['document_status'], str):
            try:
                record['document_status'] = json.loads(record['document_status'])
            except:
                record['document_status'] = {"psa": False, "form137": False, "form138": False, "goodmoral": False}
        
        return jsonify({"record": record})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# ================= UPDATE RECORD STATUS =================
@app.route('/api/record/<int:record_id>/status', methods=['PUT'])
@login_required
@permission_required('edit_records')
def update_record_status(record_id):
    data = request.json
    status = data.get('status')
    reason = data.get('reason', '')
    
    if status not in ['APPROVED', 'REJECTED', 'PENDING']:
        return jsonify({"error": "Invalid status"}), 400
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        if status == 'REJECTED' and reason:
            cur.execute("UPDATE records SET status=%s, rejection_reason=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s", 
                       (status, reason, record_id))
        else:
            cur.execute("UPDATE records SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s", (status, record_id))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Record {status.lower()}"})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# ================= EMAIL FUNCTIONS =================
def send_email_notification(recipient_email, student_name, file_paths, student_data=None):
    print(f"\n📧 Preparing email for: {recipient_email}")
    
    if not recipient_email or '@' not in recipient_email:
        return False
    
    if not SENDGRID_API_KEY or not EMAIL_SENDER:
        print("📝 Email would be sent (logging only)")
        return True
    
    try:
        subject = f"✅ AssiScan - Admission Record for {student_name}"
        
        body = f"Dear {student_name},\n\n"
        body += "Your admission documents have been processed.\n\n"
        
        if student_data:
            body += f"Name: {student_data.get('name', 'N/A')}\n"
            body += f"LRN: {student_data.get('lrn', 'N/A')}\n"
            body += f"College: {student_data.get('college', 'N/A')}\n"
            body += f"Program: {student_data.get('program', 'N/A')}\n"
            body += f"Status: {student_data.get('status', 'INCOMPLETE')}\n\n"
        
        body += "Thank you for using AssiScan.\n"
        body += "University of Batangas Lipa"
        
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "personalizations": [{"to": [{"email": recipient_email}], "subject": subject}],
            "from": {"email": EMAIL_SENDER, "name": "AssiScan System"},
            "content": [{"type": "text/plain", "value": body}]
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        return response.status_code == 202
    except Exception as e:
        print(f"⚠️ Email error: {e}")
        return True

@app.route('/send-email/<int:record_id>', methods=['POST'])
@login_required
@permission_required('send_emails')
def send_email(record_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        record = cur.fetchone()
        
        if not record:
            conn.close()
            return jsonify({"error": "Record not found"}), 404
        
        if record.get('email_sent'):
            conn.close()
            return jsonify({"warning": "Email already sent"}), 400
        
        email_addr = record.get('email')
        student_name = record.get('name')
        
        if not email_addr:
            conn.close()
            return jsonify({"error": "No email address"}), 400
        
        email_sent = send_email_notification(email_addr, student_name, [], dict(record))
        
        if email_sent:
            cur.execute("UPDATE records SET email_sent = TRUE, email_sent_at = CURRENT_TIMESTAMP WHERE id = %s", (record_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Email sent"})
        else:
            conn.close()
            return jsonify({"error": "Failed to send email"}), 500
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# ================= SERVE UPLOADS =================
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename)
    except:
        return jsonify({"error": "File not found"}), 404

@app.route('/delete-record/<int:record_id>', methods=['DELETE'])
@login_required
@permission_required('delete_records')
def delete_record(record_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if get_db_connection() else "disconnected"
    })

# ================= PAGE ROUTES =================
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    
    if session.get('role') == 'SUPER_ADMIN':
        return redirect('/admin/dashboard')
    
    return render_template('index.html')

@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html')

@app.route('/my-records')
@login_required
def my_records_page():
    if session.get('role') != 'STUDENT':
        return redirect('/')
    return render_template('student_records.html')

@app.route('/history.html')
@login_required
def history_page():
    if session.get('role') != 'SUPER_ADMIN':
        return redirect('/')
    return render_template('history.html')

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if session.get('role') != 'SUPER_ADMIN':
        return redirect('/')
    return render_template('admin_dashboard.html')

@app.route('/admin/users')
@login_required
def admin_users():
    if session.get('role') != 'SUPER_ADMIN':
        return redirect('/')
    return render_template('admin_users.html')

@app.route('/admin/colleges')
@login_required
def admin_colleges():
    if session.get('role') != 'SUPER_ADMIN':
        return redirect('/')
    return render_template('admin_colleges.html')

@app.route('/view-form/<int:record_id>')
@login_required
def view_form(record_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        if session.get('role') == 'STUDENT':
            cur.execute("SELECT * FROM records WHERE id = %s AND user_id = %s", (record_id, session['user_id']))
        else:
            cur.execute("SELECT * FROM records WHERE id = %s", (record_id,))
        
        record = cur.fetchone()
        conn.close()
        
        if not record:
            return "Record not found", 404
        
        return render_template('print_form.html', r=record)
    except Exception as e:
        return f"Error: {e}", 500

# ================= APPLICATION START =================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.run(host='0.0.0.0', port=port, debug=debug)
