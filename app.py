import os
import sqlite3
import datetime
import random
import shutil
import sys
import time
from flask import Flask, request, redirect, url_for, session, render_template_string, send_from_directory, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "imana_free_interest_microfinance_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Render Persistent Volume Support (Yoo /data jiraate achi fayyadama, yoo hin jirre folder BASE_DIR fayyada)
DATA_DIR = "/data" if os.path.exists("/data") else BASE_DIR
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')
BACKUP_FOLDER = os.path.join(DATA_DIR, 'backups')
DB_PATH = os.path.join(DATA_DIR, "web_banking.db")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['BACKUP_FOLDER'] = BACKUP_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)

# --- 1. OPTIMIZATION FOR NETWORK & RENDER DEPLOYMENT (Prevent 500 Internal Server Error) ---
def get_db_connection(max_retries=10, delay=0.5):
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            return conn
        except sqlite3.OperationalError as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise e

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_commission(amount):
    if 1000 <= amount < 3000:
        return 50.0
    elif 3000 <= amount < 5000:
        return 100.0
    elif 7000 <= amount < 10000:
        return 200.0
    elif 10000 <= amount <= 20000:
        return 400.0
    return 0.0

def send_sms_alert(phone_number, message):
    print(f"📱 [SMS SENT TO {phone_number}]: {message}")

# --- DATABASE SETUP ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_users = [
            ('ceo', 'ceo999', 'CEO', 'ACTIVE'),
            ('manager1', 'manager123', 'MANAGER', 'ACTIVE'),
            ('maker1', 'maker123', 'MAKER', 'ACTIVE'),
            ('auditor1', 'auditor123', 'AUDITOR', 'ACTIVE')
        ]
        cursor.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", default_users)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            photo_path TEXT,
            signature_path TEXT,
            balance REAL DEFAULT 0.0,
            status TEXT DEFAULT 'PENDING_APPROVAL',
            freeze_status TEXT DEFAULT 'UNFROZEN',
            freeze_reason TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY,
            txn_type TEXT,
            customer_id TEXT,
            customer_name TEXT,
            target_account TEXT,
            amount REAL,
            commission REAL DEFAULT 0.0,
            bank_name TEXT,
            ft_reference TEXT,
            status TEXT DEFAULT 'PENDING_MANAGER',
            created_by TEXT,
            timestamp TEXT,
            audited_status TEXT DEFAULT 'OPEN'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reversals (
            reversal_id TEXT PRIMARY KEY,
            txn_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            manager_approved INTEGER DEFAULT 0,
            ceo_approved INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING_APPROVAL',
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# AUTO BACKUP FUNCTION ON STARTUP/TRANSACTION
def auto_backup_db():
    try:
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"auto_backup_{now_str}.db"
        backup_file_path = os.path.join(BACKUP_FOLDER, backup_filename)
        with get_db_connection() as src_conn:
            with sqlite3.connect(backup_file_path) as dst_conn:
                src_conn.backup(dst_conn)
        # Old backups cleanup (keep latest 10)
        backups = sorted([os.path.join(BACKUP_FOLDER, f) for f in os.listdir(BACKUP_FOLDER) if f.endswith('.db')])
        if len(backups) > 10:
            for old_b in backups[:-10]:
                os.remove(old_b)
    except Exception as e:
        print(f"Auto Backup Error: {e}")

auto_backup_db()

def get_bank_capital():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type='DEPOSIT'")
    total_deposit = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type IN ('WITHDRAWAL', 'T24_TRANSFER')")
    total_withdraw = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(balance) FROM customers WHERE status='ACTIVE'")
    total_cust_balance = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(commission) FROM transactions WHERE status='APPROVED'")
    total_commission = cursor.fetchone()[0] or 0.0
    
    net_capital = total_deposit - total_withdraw + total_commission
    conn.close()
    return net_capital, total_deposit, total_withdraw, total_cust_balance, total_commission

# --- UI TEMPLATE ---
HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="om">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Imana Free Interest Microfinance</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: #f8fafc; padding-bottom: 75px; color: #0f172a; }
        nav { background: linear-gradient(135deg, #065f46, #047857); color: white; padding: 12px 16px; position: sticky; top: 0; z-index: 50; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .logo-container { display: flex; align-items: center; gap: 10px; }
        .logo-svg { width: 32px; height: 32px; fill: #fbbf24; }
        nav h1 { font-size: 15px; font-weight: 800; letter-spacing: 0.3px; color: #ffffff; }
        .role-badge { background: #0284c7; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
        .container { max-width: 600px; margin: 0 auto; padding: 16px; }
        
        .card-net { background: linear-gradient(135deg, #064e3b, #047857); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(6,78,59,0.3); margin-bottom: 20px; }
        .net-title { font-size: 12px; opacity: 0.9; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
        .net-amount { font-size: 32px; font-weight: 800; color: #fbbf24; }
        .net-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 16px; pt: 12px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 12px; }
        
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .btn-card { background: white; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #334155; font-weight: bold; font-size: 13px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: 0.2s; }
        .btn-card:active { transform: scale(0.98); }
        .btn-card span.icon { font-size: 24px; margin-bottom: 8px; }
        .btn-card-ceo { background: #faf5ff; border-color: #e9d5ff; color: #581c87; }
        .btn-card-auditor { background: #fff7ed; border-color: #ffedd5; color: #c2410c; }
        
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-around; padding: 10px 0; z-index: 50; }
        .bottom-nav a { text-align: center; color: #64748b; text-decoration: none; font-size: 11px; flex: 1; font-weight: 500; }
        .bottom-nav a span.icon { display: block; font-size: 18px; margin-bottom: 2px; }
        
        .box { background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 16px; }
        .form-group { margin-bottom: 12px; position: relative; }
        .form-group label { display: block; font-size: 12px; font-weight: bold; color: #475569; margin-bottom: 4px; }
        .input-field { width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; outline: none; }
        .btn-submit { width: 100%; background: #047857; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 14px; cursor: pointer; }
        
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block; }
        .badge-pending { background: #fef3c7; color: #92400e; }
        .badge-active { background: #dcfce7; color: #166534; }
        .badge-danger { background: #fee2e2; color: #991b1b; }
        .badge-frozen { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
        
        .item-card { background: white; border-radius: 12px; padding: 14px; margin-bottom: 12px; border: 1px solid #e2e8f0; }
        .img-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 10px 0; }
        .img-grid img { width: 100%; height: 90px; object-fit: cover; border-radius: 6px; border: 1px solid #e2e8f0; }
        
        .btn-action { padding: 6px 12px; border-radius: 6px; color: white; text-decoration: none; font-size: 12px; font-weight: bold; display: inline-block; border:none; cursor:pointer; }
        .btn-blue { background: #2563eb; }
        .btn-green { background: #16a34a; }
        .btn-red { background: #dc2626; }
        .btn-orange { background: #ea580c; }
        .btn-purple { background: #7c3aed; }
        
        .pwd-toggle { position: absolute; right: 10px; top: 32px; cursor: pointer; user-select: none; font-size: 14px; }
        
        .modal { display: none; position: fixed; z-index: 100; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); align-items: center; justify-content: center; }
        .modal-content { background: white; padding: 20px; border-radius: 12px; max-width: 450px; width: 90%; max-height: 85vh; overflow-y: auto; }
    </style>
</head>
<body>
    <nav>
        <div class="logo-container">
            <svg class="logo-svg" viewBox="0 0 24 24">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
            <h1>Imana Free Interest Microfinance</h1>
        </div>
        {% if session.get('role') %}
            <div style="font-size:12px;">
                <span style="margin-right:4px;"><b>{{ session['username'] }}</b></span>
                <span class="role-badge">{{ session['role'] }}</span>
                <a href="/logout" style="color: #fca5a5; margin-left:8px; text-decoration:none;">Logout</a>
            </div>
        {% endif %}
    </nav>

    <div class="container">
        {% block content %}{% endblock %}
    </div>

    {% if session.get('role') %}
    <div class="bottom-nav">
        <a href="/"><span class="icon">🏠</span>Dashboard</a>
        {% if session['role'] == 'MAKER' %}
            <a href="/register"><span class="icon">👤</span>Galmee</a>
            <a href="/transaction"><span class="icon">💸</span>Kaffaltii</a>
            <a href="/maker_receipts"><span class="icon">🧾</span>Nagahee</a>
        {% endif %}
        {% if session['role'] == 'MANAGER' %}
            <a href="/pending"><span class="icon">📋</span>Manager Appr</a>
            <a href="/reversals_list"><span class="icon">🔄</span>Reversals</a>
        {% endif %}
        {% if session['role'] == 'AUDITOR' %}
            <a href="/auditor_reversal_request"><span class="icon">⚠️</span>Reversal Gaafachu</a>
            <a href="/auditor_close"><span class="icon">🔒</span>Cufiinsa</a>
        {% endif %}
        {% if session['role'] == 'CEO' %}
            <a href="/reversals_list" style="color: #581c87;"><span class="icon">🔄</span>Reversal CEO</a>
            <a href="/ceo_commission" style="color: #581c87;"><span class="icon">💰</span>Comishina</a>
            <a href="/manage_users" style="color: #6b21a8;"><span class="icon">⚙️</span>Hojjattoota</a>
        {% endif %}
    </div>
    {% endif %}

    <script>
    function togglePasswordVisibility(inputId, toggleIconId) {
        var input = document.getElementById(inputId);
        var icon = document.getElementById(toggleIconId);
        if (input.type === "password") {
            input.type = "text";
            icon.textContent = "🙈";
        } else {
            input.type = "password";
            icon.textContent = "👁️";
        }
    }
    </script>
</body>
</html>
"""

# --- ROUTES & VIEWS ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT username, role, status FROM users WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        conn.close()

        if user:
            if user['status'] == 'BLOCKED':
                error = "🚫 Akkaawunttii keessan UGGURAMEERA! CEO qunnamaa."
            else:
                session['username'] = user['username']
                session['role'] = user['role']
                return redirect('/')
        else:
            error = "Username ykn Password dogoggoraa!"

    err_html = f"<p style='color:red; font-size:12px; text-align:center; margin-bottom:12px;'>{error}</p>" if error else ""
    content = f"""
    <div class="box" style="margin-top: 30px; text-align: center;">
        <div style="font-size: 40px; margin-bottom: 10px;">🏦</div>
        <h2 style="font-size: 17px; margin-bottom: 4px; color:#065f46;">Imana Free Interest Microfinance</h2>
        <p style="font-size: 12px; color: #64748b; margin-bottom: 16px;">Seensa Systema (Login)</p>
        {err_html}
        <form method="POST">
            <div class="form-group" style="text-align:left;">
                <label>Username</label>
                <input type="text" name="username" placeholder="Fkn: ceo, manager1, maker1" class="input-field" required>
            </div>
            <div class="form-group" style="text-align:left;">
                <label>Password</label>
                <input type="password" id="login_password" name="password" placeholder="Password" class="input-field" required>
                <span id="login_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('login_password', 'login_pwd_toggle')">👁️</span>
            </div>
            <button type="submit" class="btn-submit">Seeni (Login)</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/')
def dashboard():
    if 'role' not in session:
        return redirect('/login')
    
    net_cap, deposits, withdraws, cust_bal, total_comm = get_bank_capital()
    role = session['role']

    maker_btns = ""
    if role == 'MAKER':
        maker_btns = """
        <a href="/register" class="btn-card"><span class="icon">👤</span><span>Galmee Maammilaa</span></a>
        <a href="/transaction" class="btn-card"><span class="icon">💸</span><span>Deposit / Transfer / Withdraw</span></a>
        <a href="/maker_receipts" class="btn-card"><span class="icon">🧾</span><span>Nagahee Maxxansi</span></a>
        """

    manager_btns = ""
    if role == 'MANAGER':
        manager_btns = """
        <a href="/pending" class="btn-card"><span class="icon">🔍</span><span>Manager Approval</span></a>
        <a href="/reversals_list" class="btn-card"><span class="icon">🔄</span><span>Reversal Approvals</span></a>
        """

    auditor_btns = ""
    if role == 'AUDITOR':
        auditor_btns = """
        <a href="/auditor_reversal_request" class="btn-card btn-card-auditor"><span class="icon">⚠️</span><span>Transaction Reversal Gaafachu</span></a>
        <a href="/auditor_close" class="btn-card btn-card-auditor"><span class="icon">🔒</span><span>Cufiinsa Herrega Galgalaa</span></a>
        """

    ceo_btn = ""
    if role == 'CEO':
        ceo_btn = """
        <a href="/ceo_commission" class="btn-card btn-card-ceo"><span class="icon">💰</span><span>Comishina Guyyaa</span></a>
        <a href="/ceo_blank_form" target="_blank" class="btn-card btn-card-ceo"><span class="icon">🖨️</span><span>Formii Duwwaa Maxxansi</span></a>
        <a href="/reversals_list" class="btn-card btn-card-ceo"><span class="icon">🔄</span><span>CEO Reversal Approval</span></a>
        <a href="/manage_users" class="btn-card btn-card-ceo"><span class="icon">⚙️</span><span>Bulchiinsa Hojjattootaa</span></a>
        <a href="/ceo_backup" class="btn-card btn-card-ceo"><span class="icon">💾</span><span>Save / Restore DB</span></a>
        <a href="/ceo_audit" class="btn-card btn-card-ceo"><span class="icon">🌙</span><span>CEO Audit & Reports</span></a>
        """

    content = f"""
    <div class="card-net">
        <div class="net-title">Waliigala Kaabitaala Baankii (Net Capital)</div>
        <div class="net-amount">{net_cap:,.2f} Birr</div>
        <div class="net-grid">
            <div>📥 Deposit: <b>{deposits:,.2f} Birr</b></div>
            <div>📤 Withdraw/FT: <b>{withdraws:,.2f} Birr</b></div>
        </div>
    </div>

    <h3 style="font-size: 14px; margin-bottom: 12px; color: #475569;">Menu Hojii ({role})</h3>
    <div class="grid-2">
        {maker_btns}
        {manager_btns}
        {auditor_btns}
        <a href="/customers" class="btn-card"><span class="icon">👥</span><span>Listii Maammiltootaa</span></a>
        {ceo_btn}
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/pending')
def pending():
    if 'role' not in session or session['role'] != 'MANAGER':
        return "🚫 Hayyama Manager Qofa!", 403

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT customer_id, full_name, phone, photo_path, signature_path FROM customers WHERE status='PENDING_APPROVAL'")
    pending_custs = cursor.fetchall()

    cursor.execute("""
        SELECT 
            t.txn_id, t.txn_type, t.customer_name, t.amount, t.bank_name, t.status,
            c.photo_path, c.signature_path, c.phone, t.customer_id, t.ft_reference, t.target_account, t.commission,
            c.freeze_status, c.freeze_reason
        FROM transactions t
        LEFT JOIN customers c ON t.customer_id = c.customer_id
        WHERE t.status = 'PENDING_MANAGER'
        ORDER BY t.timestamp DESC
    """)
    pending_txns = cursor.fetchall()
    conn.close()

    cards_html = ""

    if pending_custs:
        cards_html += "<h3 style='font-size:12px; color:#1e40af; margin-bottom:8px;'>👤 Galmee Maammiltoota Eeggamaa Jiran</h3>"
        for c in pending_custs:
            cards_html += f"""
            <div class="item-card" style="background:#eff6ff; border-color:#bfdbfe;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-size:12px; font-weight:bold; color:#1e3a8a;">Acc: {c['customer_id']}</span>
                    <span class="badge badge-pending">PENDING</span>
                </div>
                <div style="font-size:13px; font-weight:bold; margin-bottom:6px;">Maqaa: {c['full_name']} (📞 {c['phone']})</div>
                <div class="img-grid">
                    <div style="text-align:center;"><img src="/uploads/{c['photo_path']}"><span style="font-size:10px; color:#64748b;">Fuula</span></div>
                    <div style="text-align:center;"><img src="/uploads/{c['signature_path']}"><span style="font-size:10px; color:#1e40af; font-weight:bold;">Mallattoo ✍️</span></div>
                </div>
                <div style="text-align:right; margin-top:8px;">
                    <a href="/approve_cust/{c['customer_id']}" class="btn-action btn-blue">✅ Approve Customer</a>
                </div>
            </div>
            """

    if pending_txns:
        cards_html += "<h3 style='font-size:12px; color:#b45309; margin-top:16px; margin-bottom:8px;'>💵 Kaffaltii Maker Uume - Mirkaneessa Manager Eeggatu</h3>"
        for r in pending_txns:
            freeze_info = f"<span class='badge badge-frozen'>🔒 UGGURAMEERA ({r['freeze_reason']})</span>" if r['freeze_status'] == 'FROZEN' else "<span class='badge badge-active'>✅ Active</span>"
            
            cards_html += f"""
            <div class="item-card">
                <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                    <span style="font-size:12px; font-weight:bold; color:#065f46;">FT Ref: {r['ft_reference']}</span>
                    <span class="badge badge-pending">PENDING APPROVAL</span>
                </div>
                <div style="font-size:13px; font-weight:bold;">{r['txn_type']}: {r['amount']:,.2f} Birr ({r['bank_name']})</div>
                <div style="font-size:11px; color:#64748b; margin-bottom:8px;">Maammila: <b>{r['customer_name']}</b> ({r['customer_id']})</div>
                
                <div style="margin-bottom:8px;">Status Ugguraa: {freeze_info}</div>

                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
                    <button onclick="openModal('{r['customer_name']}', '{r['photo_path']}', '{r['signature_path']}', '{r['freeze_status']}', '{r['freeze_reason']}')" class="btn-action btn-purple">👁️ View Suuraa & Info</button>
                    <div>
                        <a href="/manager_action/approve/{r['txn_id']}" class="btn-action btn-green">✅ Approve</a>
                        <a href="/manager_action/reject/{r['txn_id']}" class="btn-action btn-red">❌ Reject</a>
                    </div>
                </div>
            </div>
            """

    if not cards_html:
        cards_html = "<p style='text-align:center; color:#64748b; padding:20px; font-size:13px;'>✅ Transaction-ni Manager Approval eeggatu hin jiru!</p>"

    content = f"""
    <h2 style="font-size: 16px; margin-bottom: 12px;">📋 Manager Approval Dashboard</h2>
    {cards_html}

    <!-- Modal for viewing customer details -->
    <div id="infoModal" class="modal">
        <div class="modal-content">
            <h3 id="modalName" style="font-size:15px; color:#065f46; margin-bottom:10px;"></h3>
            <div id="modalFreeze" style="margin-bottom:10px; font-size:12px;"></div>
            <div class="img-grid">
                <div><p style="font-size:10px; font-weight:bold;">Suuraa Fuulaa:</p><img id="modalPhoto" src="" style="width:100%; height:120px; object-fit:cover;"></div>
                <div><p style="font-size:10px; font-weight:bold;">Mallattoo:</p><img id="modalSig" src="" style="width:100%; height:120px; object-fit:cover;"></div>
            </div>
            <button onclick="closeModal()" class="btn-submit" style="background:#64748b; margin-top:12px;">Cufi (Close)</button>
        </div>
    </div>

    <script>
    function openModal(name, photo, sig, freezeSt, freezeRs) {{
        document.getElementById('modalName').innerText = "Maammila: " + name;
        document.getElementById('modalPhoto').src = "/uploads/" + photo;
        document.getElementById('modalSig').src = "/uploads/" + sig;
        
        var freezeDiv = document.getElementById('modalFreeze');
        if(freezeSt === 'FROZEN') {{
            freezeDiv.innerHTML = "<p style='color:#dc2626; font-weight:bold; background:#fee2e2; padding:6px; border-radius:4px;'>🔒 UGGURAMEERA! Sababa: " + freezeRs + "</p>";
        }} else {{
            freezeDiv.innerHTML = "<p style='color:#16a34a; font-weight:bold; background:#dcfce7; padding:6px; border-radius:4px;'>✅ Uggura irra hin jiru (Active)</p>";
        }}
        document.getElementById('infoModal').style.display = 'flex';
    }}
    function closeModal() {{
        document.getElementById('infoModal').style.display = 'none';
    }}
    </script>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/manager_action/<act>/<txn_id>')
def manager_action(act, txn_id):
    if 'role' not in session or session['role'] != 'MANAGER':
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()

    if act == 'approve':
        cursor.execute("SELECT txn_type, customer_id, target_account, amount, commission, ft_reference FROM transactions WHERE txn_id = ?", (txn_id,))
        row = cursor.fetchone()
        if row:
            txn_type = row['txn_type']
            cust_id = row['customer_id']
            target_acc = row['target_account']
            amount = row['amount']
            commission = row['commission']
            ft_ref = row['ft_reference']

            cursor.execute("SELECT balance, phone, full_name, freeze_status FROM customers WHERE customer_id = ?", (cust_id,))
            cust = cursor.fetchone()
            curr_bal = cust['balance'] if cust else 0.0
            phone = cust['phone'] if cust else ""
            name = cust['full_name'] if cust else ""
            freeze_st = cust['freeze_status'] if cust else "UNFROZEN"

            total_deduction = amount + commission

            if freeze_st == 'FROZEN' and txn_type in ['WITHDRAWAL', 'T24_TRANSFER']:
                cursor.execute("UPDATE transactions SET status = 'REJECTED_CUSTOMER_FROZEN' WHERE txn_id = ?", (txn_id,))
            elif txn_type in ['WITHDRAWAL', 'T24_TRANSFER'] and curr_bal < total_deduction:
                cursor.execute("UPDATE transactions SET status = 'REJECTED_INSUFFICIENT_FUNDS' WHERE txn_id = ?", (txn_id,))
            else:
                if txn_type == 'DEPOSIT':
                    cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, cust_id))
                elif txn_type == 'WITHDRAWAL':
                    cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (total_deduction, cust_id))
                elif txn_type == 'T24_TRANSFER':
                    cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, cust_id))
                    cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, target_acc))

                cursor.execute("UPDATE transactions SET status = 'APPROVED' WHERE txn_id = ?", (txn_id,))

                msg_cust = f"Kabajamoo {name}, {txn_type} {amount:,.2f} Birr (Ref: {ft_ref}) Manager-n mirkanaa'ee xumurameera."
                send_sms_alert(phone, msg_cust)

    elif act == 'reject':
        cursor.execute("UPDATE transactions SET status = 'REJECTED_BY_MANAGER' WHERE txn_id = ?", (txn_id,))

    conn.commit()
    conn.close()
    return redirect('/pending')

@app.route('/ceo_blank_form')
def ceo_blank_form():
    if 'role' not in session or session['role'] != 'CEO':
        return "🚫 Hayyama CEO Qofa!", 403

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Formii Risiita Duwwaa - Imana Microfinance</title>
        <style>
            body {{ font-family: sans-serif; padding: 30px; max-width: 750px; margin: 0 auto; border: 2px solid #065f46; border-radius: 8px; }}
            .header {{ text-align: center; border-bottom: 2px solid #065f46; padding-bottom: 12px; margin-bottom: 20px; }}
            .row {{ display: flex; justify-content: space-between; margin-bottom: 20px; font-size: 14px; }}
            .field-line {{ border-bottom: 1px dotted #000; width: 60%; display: inline-block; }}
            .box-area {{ border: 1px solid #000; height: 100px; margin-top: 10px; border-radius: 4px; padding: 10px; font-size: 12px; color: #888; }}
            .btn-print {{ background: #065f46; color: white; border: none; padding: 12px; width: 100%; font-size: 16px; font-weight: bold; cursor: pointer; border-radius: 6px; margin-top: 30px; }}
            @media print {{ .btn-print {{ display: none; }} body {{ border: none; }} }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1 style="color:#065f46; margin:0;">IMANA FREE INTEREST MICROFINANCE</h1>
            <h3>FOORMII GALMEESSA MAAMMILAAGAA FI BAASII-GALII (MAKER FORM)</h3>
        </div>

        <div style="font-size:14px; line-height: 2.2;">
            <div><b>Guyyaa:</b> <span class="field-line"></span></div>
            <div><b>Gosa Foormii:</b> [  ] Galmee Maammilaa &nbsp;&nbsp;&nbsp; [  ] Deposit (Galii) &nbsp;&nbsp;&nbsp; [  ] Withdrawal (Baasii)</div>
            <div><b>Maqaa Guutuu Maammilaa:</b> <span class="field-line"></span></div>
            <div><b>Lakkoofsa Akkaawuntii (T24 ID):</b> <span class="field-line"></span></div>
            <div><b>Lakkoofsa Bilbilaa:</b> <span class="field-line"></span></div>
            <div><b>Hamma Qarshii (Jechaan):</b> <span class="field-line"></span></div>
            <div><b>Hamma Qarshii (Lakkoofsaan):</b> <span class="field-line"></span> Birr</div>
            <div><b>Yaada / Sababa Kaffaltii:</b></div>
            <div class="box-area">Yaada maammilli barreesse bakka kana...</div>
        </div>

        <div style="margin-top: 50px; display: flex; justify-content: space-between; font-size: 13px;">
            <div>________________________<br>Mallattoo Maammilaa</div>
            <div>________________________<br>Mallattoo Maker (Hojjataa)</div>
            <div>________________________<br>Mallattoo Manager</div>
        </div>

        <button onclick="window.print()" class="btn-print">🖨️ Formii Duwwaa Maxxansi (Print Blank Form)</button>
    </body>
    </html>
    """

@app.route('/freeze_customer/<cust_id>', methods=['POST'])
def freeze_customer(cust_id):
    if 'role' not in session or session['role'] != 'CEO':
        return redirect('/login')

    action_type = request.form.get('action_type')
    reason = request.form.get('freeze_reason', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    if action_type == 'freeze':
        cursor.execute("UPDATE customers SET freeze_status = 'FROZEN', freeze_reason = ? WHERE customer_id = ?", (reason, cust_id))
    elif action_type == 'unfreeze':
        cursor.execute("UPDATE customers SET freeze_status = 'UNFROZEN', freeze_reason = '' WHERE customer_id = ?", (cust_id,))

    conn.commit()
    conn.close()
    return redirect('/customers')

@app.route('/auditor_reversal_request', methods=['GET', 'POST'])
def auditor_reversal_request():
    if 'role' not in session or session['role'] != 'AUDITOR':
        return "🚫 Hayyama Auditor Qofa!", 403

    msg = None
    if request.method == 'POST':
        txn_id = request.form.get('txn_id').strip()
        reason = request.form.get('reason').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT txn_id, status FROM transactions WHERE txn_id = ? OR ft_reference = ?", (txn_id, txn_id))
        txn = cursor.fetchone()

        if not txn:
            msg = "❌ Transaction-ni koodii/FT reference kanaan argame hin jiru!"
        elif txn['status'] != 'APPROVED':
            msg = f"❌ Transaction-ni sun status '{txn['status']}' irratti argama. Status APPROVED qofatu reversal ta'uu danda'a."
        else:
            rev_id = f"REV-{int(datetime.datetime.now().timestamp())}"
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO reversals (reversal_id, txn_id, reason, requested_by, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (rev_id, txn['txn_id'], reason, session['username'], now))
            conn.commit()
            msg = "✅ Gaaffiin Reversal sababa gahaa waliin ergameera! Manager fi CEO approval eegaa jira."
        conn.close()

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; color:#c2410c; margin-bottom: 4px;">⚠️ Transaction Reversal Gaafachu (Auditor)</h2>
        <p style="font-size: 11px; color:#64748b; margin-bottom: 14px;">Transaction dogoggoraan raawwatame Reversal sababa gahaa waliin galchi.</p>
        
        {f"<p style='background:#fef3c7; color:#92400e; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""}

        <form method="POST">
            <div class="form-group">
                <label>Txn ID ykn FT Reference</label>
                <input type="text" name="txn_id" placeholder="Fkn: FT2621412345" required class="input-field">
            </div>
            <div class="form-group">
                <label>Sababa Gahaa (Reversal Reason)</label>
                <textarea name="reason" rows="3" placeholder="Sababa reversal..." required class="input-field"></textarea>
            </div>
            <button type="submit" class="btn-submit" style="background:#ea580c;">🔄 Gaaffii Reversal Ergi</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/reversals_list')
def reversals_list():
    if 'role' not in session or session['role'] not in ['MANAGER', 'CEO']:
        return "🚫 Hayyama Manager ykn CEO Qofa!", 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.reversal_id, r.txn_id, r.reason, r.requested_by, r.manager_approved, r.ceo_approved, r.status, r.timestamp,
               t.ft_reference, t.txn_type, t.amount, t.customer_name, t.customer_id
        FROM reversals r
        JOIN transactions t ON r.txn_id = t.txn_id
        ORDER BY r.timestamp DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    cards_html = ""
    for r in rows:
        mgr_st = "✅ Approved" if r['manager_approved'] else "⏳ Pending"
        ceo_st = "✅ Approved" if r['ceo_approved'] else "⏳ Pending"

        action_btn = ""
        if session['role'] == 'MANAGER' and not r['manager_approved'] and r['status'] == 'PENDING_APPROVAL':
            action_btn = f'<a href="/approve_reversal/manager/{r["reversal_id"]}" class="btn-action btn-blue">✅ Manager Approve</a>'
        elif session['role'] == 'CEO' and not r['ceo_approved'] and r['status'] == 'PENDING_APPROVAL':
            action_btn = f'<a href="/approve_reversal/ceo/{r["reversal_id"]}" class="btn-action btn-purple">✅ CEO Approve & Execute</a>'

        cards_html += f"""
        <div class="item-card" style="border-left: 4px solid #ea580c;">
            <div style="display:flex; justify-content:space-between;">
                <span style="font-size:12px; font-weight:bold; color:#ea580c;">FT Ref: {r['ft_reference']}</span>
                <span class="badge badge-pending">{r['status']}</span>
            </div>
            <div style="font-size:13px; font-weight:bold; margin-top:4px;">{r['txn_type']}: {r['amount']:,.2f} Birr (Maammila: {r['customer_name']})</div>
            <div style="font-size:11px; color:#64748b; margin-top:4px;"><b>Sababa Reversal:</b> {r['reason']}</div>
            <div style="font-size:11px; color:#475569; margin-top:4px;">By: {r['requested_by']} | Mgr: <b>{mgr_st}</b> | CEO: <b>{ceo_st}</b></div>
            <div style="text-align:right; margin-top:8px;">
                {action_btn}
            </div>
        </div>
        """

    content = f"""
    <h2 style="font-size: 16px; margin-bottom: 12px; color:#c2410c;">🔄 Gaaffiiwwan Reversal Transactions</h2>
    {cards_html if cards_html else "<p style='text-align:center; padding:20px; font-size:12px; color:#64748b;'>Gaaffiin Reversal eeggatu hin jiru.</p>"}
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/approve_reversal/<role_type>/<rev_id>')
def approve_reversal(role_type, rev_id):
    if 'role' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reversals WHERE reversal_id = ?", (rev_id,))
    rev = cursor.fetchone()

    if not rev:
        conn.close()
        return "Reversal Hin Argamne", 404

    mgr_appr = rev['manager_approved']
    ceo_appr = rev['ceo_approved']

    if role_type == 'manager' and session['role'] == 'MANAGER':
        mgr_appr = 1
    elif role_type == 'ceo' and session['role'] == 'CEO':
        ceo_appr = 1

    if mgr_appr == 1 and ceo_appr == 1:
        cursor.execute("SELECT * FROM transactions WHERE txn_id = ?", (rev['txn_id'],))
        txn = cursor.fetchone()
        
        if txn and txn['status'] == 'APPROVED':
            amount = txn['amount']
            cust_id = txn['customer_id']
            target_acc = txn['target_account']
            txn_type = txn['txn_type']
            comm = txn['commission']

            if txn_type == 'DEPOSIT':
                cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, cust_id))
            elif txn_type == 'WITHDRAWAL':
                cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount + comm, cust_id))
            elif txn_type == 'T24_TRANSFER':
                cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, cust_id))
                cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, target_acc))

            cursor.execute("UPDATE transactions SET status = 'REVERSED' WHERE txn_id = ?", (rev['txn_id'],))
            cursor.execute("UPDATE reversals SET status = 'COMPLETED_REVERSED', manager_approved = 1, ceo_approved = 1 WHERE reversal_id = ?", (rev_id,))
    else:
        cursor.execute("UPDATE reversals SET manager_approved = ?, ceo_approved = ? WHERE reversal_id = ?", (mgr_appr, ceo_appr, rev_id))

    conn.commit()
    conn.close()
    return redirect('/reversals_list')

# --- 5. CEO BACKUP & RESTORE ---
@app.route('/ceo_backup', methods=['GET', 'POST'])
def ceo_backup():
    if 'role' not in session or session['role'] != 'CEO':
        return "🚫 Hayyama CEO Qofa!", 403

    msg = None
    msg_type = "green"

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'restore':
            file = request.files.get('backup_file')
            if file and file.filename.endswith('.db'):
                temp_path = os.path.join(app.config['BACKUP_FOLDER'], "temp_restore.db")
                file.save(temp_path)
                try:
                    test_conn = sqlite3.connect(temp_path)
                    test_conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    test_conn.close()

                    shutil.copyfile(temp_path, DB_PATH)
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    msg = "✅ Database-ni milkaa'inaan deebi'eera (Restore Complete)!"
                except Exception as e:
                    msg = f"❌ Database restore ta'uu hin dandeenye: {str(e)}"
                    msg_type = "red"
            else:
                msg = "❌ Faayila '.db' sirrii ta'e qofa ol-fe'aa!"
                msg_type = "red"

    msg_html = f"<p style='background:{'#dcfce7' if msg_type=='green' else '#fee2e2'}; color:{'#166534' if msg_type=='green' else '#991b1b'}; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; color:#581c87; margin-bottom: 4px;">💾 Safe Data Backup & Restore (CEO)</h2>
        <p style="font-size: 11px; color:#64748b; margin-bottom: 16px;">System-ni auto save & backup kan godhamu yoo ta'uu, safe- ta'uuf DB download / restore godhaa.</p>
        
        {msg_html}

        <div style="background:#faf5ff; border:1px solid #e9d5ff; padding:16px; border-radius:10px; margin-bottom:16px;">
            <h3 style="font-size:13px; color:#581c87; margin-bottom:4px;">📥 1. Save Database (Download)</h3>
            <p style="font-size:11px; color:#64748b; margin-bottom:10px;">Data kuufame saafiyyaan save godhachuuf button kana tuqaa.</p>
            <a href="/download_db" class="btn-submit" style="background:#7c3aed; text-align:center; text-decoration:none; display:block;">💾 Download Database Backup (.db)</a>
        </div>

        <div style="background:#fff7ed; border:1px solid #ffedd5; padding:16px; border-radius:10px;">
            <h3 style="font-size:13px; color:#c2410c; margin-bottom:4px;">📤 2. Restore Database</h3>
            <form method="POST" enctype="multipart/form-data">
                <input type="hidden" name="action" value="restore">
                <div class="form-group">
                    <input type="file" name="backup_file" accept=".db" required class="input-field">
                </div>
                <button type="submit" class="btn-submit" style="background:#c2410c;">🔄 Database Restore Godhi</button>
            </form>
        </div>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/download_db')
def download_db():
    if 'role' not in session or session['role'] != 'CEO':
        return redirect('/login')

    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"imana_microfinance_backup_{now_str}.db"
    backup_file_path = os.path.join(app.config['BACKUP_FOLDER'], backup_filename)
    
    try:
        with get_db_connection() as src_conn:
            with sqlite3.connect(backup_file_path) as dst_conn:
                src_conn.backup(dst_conn)
        return send_file(backup_file_path, as_attachment=True, download_name=backup_filename)
    except Exception as e:
        return f"Backup download error: {str(e)}", 500

@app.route('/customers')
def customers():
    if 'role' not in session:
        return redirect('/login')

    search_query = request.args.get('q', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    if search_query:
        cursor.execute("SELECT customer_id, full_name, phone, photo_path, balance, status, freeze_status, freeze_reason FROM customers WHERE full_name LIKE ? OR phone LIKE ? OR customer_id LIKE ?", 
                       (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
    else:
        cursor.execute("SELECT customer_id, full_name, phone, photo_path, balance, status, freeze_status, freeze_reason FROM customers")
    rows = cursor.fetchall()
    conn.close()

    cust_html = ""
    for r in rows:
        photo = f"/uploads/{r['photo_path']}" if r['photo_path'] else ""
        badge_cls = "badge-active" if r['status'] == 'ACTIVE' else "badge-pending"

        freeze_badge = ""
        if r['freeze_status'] == 'FROZEN':
            freeze_badge = f'<span class="badge badge-frozen" title="{r["freeze_reason"]}">🔒 FROZEN ({r["freeze_reason"]})</span>'

        ceo_freeze_form = ""
        if session['role'] == 'CEO':
            if r['freeze_status'] == 'FROZEN':
                ceo_freeze_form = f"""
                <form method="POST" action="/freeze_customer/{r['customer_id']}" style="display:inline;">
                    <input type="hidden" name="action_type" value="unfreeze">
                    <button type="submit" class="btn-action btn-green" style="font-size:10px; padding:3px 8px;">🔓 Freeze Kaasi</button>
                </form>
                """
            else:
                ceo_freeze_form = f"""
                <button onclick="document.getElementById('freeze_box_{r['customer_id']}').style.display='block'" class="btn-action btn-red" style="font-size:10px; padding:3px 8px;">🔒 Freeze Kaayi</button>
                <div id="freeze_box_{r['customer_id']}" style="display:none; margin-top:8px; background:#fff7ed; padding:8px; border-radius:6px;">
                    <form method="POST" action="/freeze_customer/{r['customer_id']}">
                        <input type="hidden" name="action_type" value="freeze">
                        <input type="text" name="freeze_reason" placeholder="Sababa Ugguraa..." required class="input-field" style="font-size:11px; padding:4px; margin-bottom:4px;">
                        <button type="submit" class="btn-action btn-red" style="font-size:10px;">Mirkanessi Ugguri</button>
                    </form>
                </div>
                """

        edit_btn = ""
        if session['role'] == 'MANAGER':
            edit_btn = f'<a href="/edit_customer/{r["customer_id"]}" class="btn-action btn-blue" style="font-size:10px; padding:3px 8px; margin-right:4px;">✏️ Edit</a>'

        print_form_btn = f'<a href="/print_customer_form/{r["customer_id"]}" target="_blank" class="btn-action btn-purple" style="font-size:10px; padding:3px 8px; margin-right:4px;">🖨️ Formii</a>'
        statement_btn = f'<a href="/statement/{r["customer_id"]}" class="btn-action btn-orange" style="font-size:10px; padding:3px 8px;">📜 Statement</a>'

        cust_html += f"""
        <div class="item-card" style="display:flex; align-items:center;">
            <img src="{photo}" style="width:48px; height:48px; border-radius:50%; object-fit:cover; margin-right:12px; border:1px solid #cbd5e1;">
            <div style="width:100%;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="font-size:13px; font-weight:bold;">{r['full_name']}</h4>
                    <div>
                        <span class="badge {badge_cls}">{r['status']}</span>
                        {freeze_badge}
                    </div>
                </div>
                <p style="font-size:11px; color:#64748b; margin-top:2px;">📞 {r['phone']} | Acc: <b>{r['customer_id']}</b></p>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:6px;">
                    <p style="font-size:12px; font-weight:bold; color:#065f46;">Balance: {r['balance']:,.2f} Birr</p>
                    <div>
                        {ceo_freeze_form}
                        {edit_btn}
                        {print_form_btn}
                        {statement_btn}
                    </div>
                </div>
            </div>
        </div>
        """

    content = f"""
    <h2 style="font-size: 16px; margin-bottom: 12px;">👥 Listii Maammiltootaa</h2>
    
    <div class="box" style="padding:12px; margin-bottom:16px;">
        <form method="GET" action="/customers" style="display:flex; gap:8px;">
            <input type="text" name="q" value="{search_query}" placeholder="🔍 Search Maqaa, Bilbila ykn Acc ID..." class="input-field" style="margin:0;">
            <button type="submit" class="btn-submit" style="width:auto; padding:0 16px;">Barbaadi</button>
        </form>
    </div>

    {cust_html if cust_html else "<p style='text-align:center; color:#64748b; padding:20px; font-size:12px;'>Maammilli argame hin jiru.</p>"}
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

# --- 1 & 8. RISIITA MAKER PRINT GODHU (NO STATUS - REMOVED PENDING/APPROVED) ---
@app.route('/receipt/<txn_id>')
def print_receipt(txn_id):
    if 'role' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT txn_id, txn_type, customer_id, customer_name, target_account, amount, bank_name, ft_reference, status, created_by, timestamp
        FROM transactions WHERE txn_id = ?
    """, (txn_id,))
    t = cursor.fetchone()

    target_name = ""
    if t and t['target_account']:
        cursor.execute("SELECT full_name FROM customers WHERE customer_id = ?", (t['target_account'],))
        t_row = cursor.fetchone()
        target_name = t_row['full_name'] if t_row else "N/A"

    conn.close()

    if not t:
        return "Transaction Hin Argamne", 404

    transfer_details = ""
    if t['txn_type'] == 'T24_TRANSFER':
        transfer_details = f"""
        <div class="row"><span>Nama Erge (Sender):</span><b>{t['customer_name']} (Acc: {t['customer_id']})</b></div>
        <div class="row"><span>Nama Fudhate (Receiver):</span><b>{target_name} (Acc: {t['target_account']})</b></div>
        """

    # 1. Status 'APPROVED' fi 'PENDING' guutumaan guutuutti dhiifameera (haqameera)
    receipt_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nagahee Kaffaltii - {t['ft_reference']}</title>
        <style>
            body {{ font-family: sans-serif; padding: 20px; max-width: 400px; margin: 0 auto; border: 1px solid #ccc; border-radius: 8px; }}
            .header {{ text-align: center; border-bottom: 2px dashed #000; padding-bottom: 10px; margin-bottom: 15px; }}
            .row {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; }}
            .footer {{ text-align: center; border-top: 2px dashed #000; padding-top: 10px; margin-top: 15px; font-size: 11px; color: #555; }}
            .btn-print {{ background: #047857; color: white; border: none; padding: 10px; width: 100%; border-radius: 5px; font-weight: bold; cursor: pointer; margin-top: 15px; }}
            @media print {{ .btn-print {{ display: none; }} body {{ border: none; }} }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="margin:0; font-size:16px; color:#065f46;">IMANA FREE INTEREST MICROFINANCE</h2>
            <p style="margin:4px 0 0 0; font-size:12px;">NAGAHEE KAFFALTII (TRANSACTION RECEIPT)</p>
        </div>
        
        <div class="row"><span>FT Ref:</span><b>{t['ft_reference']}</b></div>
        <div class="row"><span>Guyyaa:</span><span>{t['timestamp']}</span></div>
        <div class="row"><span>Gosa Hojii:</span><b>{t['txn_type']}</b></div>
        
        {transfer_details if t['txn_type'] == 'T24_TRANSFER' else f'''
        <div class="row"><span>Maammila:</span><span>{t['customer_name']}</span></div>
        <div class="row"><span>Acc Maammilaa:</span><span>{t['customer_id']}</span></div>
        '''}

        <div class="row"><span>Baankii:</span><span>{t['bank_name']}</span></div>
        <div class="row" style="font-size:16px; font-weight:bold; background:#f1f5f9; padding:6px 4px; margin-top:10px;">
            <span>HAMMA QARSHII:</span><span>{t['amount']:,.2f} Birr</span>
        </div>
        <div class="row"><span>Maker (Hojjataa):</span><span>{t['created_by']}</span></div>

        <div class="footer">
            <p>Galatoomaa! Imana Free Interest Microfinance Fayyadamuu Keessaniif.</p>
        </div>

        <button onclick="window.print()" class="btn-print">🖨️ Maxxansi (Print Nagahee)</button>
    </body>
    </html>
    """
    return receipt_html

@app.route('/print_customer_form/<cust_id>')
def print_customer_form(cust_id):
    if 'role' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers WHERE customer_id = ?", (cust_id,))
    c = cursor.fetchone()
    conn.close()

    if not c:
        return "Maammilli Hin Argamne", 404

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Formii Galmee Maammilaa - {c['full_name']}</title>
        <style>
            body {{ font-family: sans-serif; padding: 30px; max-width: 700px; margin: 0 auto; border: 2px solid #065f46; border-radius: 8px; }}
            .header {{ text-align: center; border-bottom: 2px solid #065f46; padding-bottom: 12px; margin-bottom: 20px; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }}
            .box-img {{ text-align: center; border: 1px solid #ccc; padding: 10px; border-radius: 6px; }}
            .box-img img {{ max-width: 100%; height: 120px; object-fit: cover; }}
            .field {{ margin-bottom: 12px; font-size: 14px; border-bottom: 1px dotted #ccc; padding-bottom: 4px; }}
            .btn-print {{ background: #065f46; color: white; border: none; padding: 12px; width: 100%; font-size: 16px; font-weight: bold; cursor: pointer; border-radius: 6px; margin-top: 20px; }}
            @media print {{ .btn-print {{ display: none; }} body {{ border: none; }} }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1 style="color:#065f46; margin:0;">IMANA FREE INTEREST MICROFINANCE</h1>
            <h3>WARAQAA GALMEE MAAMMILAA (CUSTOMER REGISTRATION FORM)</h3>
        </div>

        <div class="grid">
            <div class="box-img">
                <img src="/uploads/{c['photo_path']}">
                <p style="font-size:11px; margin-top:4px;"><b>Suuraa Maammilaa</b></p>
            </div>
            <div class="box-img">
                <img src="/uploads/{c['signature_path']}">
                <p style="font-size:11px; margin-top:4px;"><b>Mallattoo Maammilaa</b></p>
            </div>
        </div>

        <div class="field"><b>Lakkoofsa Akkaawuntii (T24 ID):</b> {c['customer_id']}</div>
        <div class="field"><b>Maqaa Guutuu:</b> {c['full_name']}</div>
        <div class="field"><b>Lakkoofsa Bilbilaa:</b> {c['phone']}</div>
        <div class="field"><b>Balansii Jalqabaa:</b> {c['balance']:,.2f} Birr</div>
        <div class="field"><b>Status Akkaawuntii:</b> {c['status']}</div>
        <div class="field"><b>Guyyaa Galmee:</b> {c['created_at']}</div>

        <div style="margin-top: 40px; display: flex; justify-content: space-between; font-size: 12px;">
            <div>________________________<br>Mallattoo Manager</div>
            <div>________________________<br>Mallattoo CEO</div>
        </div>

        <button onclick="window.print()" class="btn-print">🖨️ Formii Galmee Maxxansi (Print Form A)</button>
    </body>
    </html>
    """

@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    if 'role' not in session or session['role'] != 'CEO':
        return "🚫 Hayyama CEO Qofa!", 403

    msg = None
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_user':
            username = request.form.get('username').strip()
            password = request.form.get('password').strip()
            role = request.form.get('role')

            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO users (username, password, role, status) VALUES (?, ?, ?, 'ACTIVE')", (username, password, role))
                conn.commit()
                msg = f"✅ Hojjataa haaraa '{username}' ({role}) galmaa'eera!"
            except sqlite3.IntegrityError:
                msg = f"❌ Usernamni '{username}' duraan exist godha!"
            conn.close()

        elif action == 'change_password':
            username = request.form.get('target_user')
            new_pass = request.form.get('new_password').strip()

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password = ? WHERE username = ?", (new_pass, username))
            conn.commit()
            conn.close()
            msg = f"🔑 Password '<b>{username}</b>'-f haaraa jijjiirameera!"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, role, status, password FROM users")
    users_list = cursor.fetchall()
    conn.close()

    users_html = ""
    for idx, u in enumerate(users_list):
        badge_cls = "badge-active" if u['status'] == 'ACTIVE' else "badge-danger"
        toggle_txt = "🚫 Ugguri" if u['status'] == 'ACTIVE' else "✅ Hiiki"
        toggle_btn_cls = "btn-red" if u['status'] == 'ACTIVE' else "btn-green"

        users_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0; font-size:12px;">
            <td style="padding:8px; font-weight:bold;">{u['username']}</td>
            <td style="padding:8px;"><span class="role-badge">{u['role']}</span></td>
            <td style="padding:8px;"><span class="badge {badge_cls}">{u['status']}</span></td>
            <td style="padding:8px;">
                <input type="password" id="pass_field_{idx}" value="{u['password']}" readonly style="border:none; background:transparent; width:80px; font-size:12px;">
                <span id="pass_toggle_{idx}" style="cursor:pointer;" onclick="togglePasswordVisibility('pass_field_{idx}', 'pass_toggle_{idx}')">👁️</span>
            </td>
            <td style="padding:8px; text-align:right;">
                <a href="/toggle_user/{u['username']}" class="btn-action {toggle_btn_cls}" style="font-size:10px; padding:4px 8px;">{toggle_txt}</a>
            </td>
        </tr>
        """

    msg_html = f"<p style='background:#dcfce7; color:#166534; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; margin-bottom: 12px;">⚙️ Bulchiinsa Hojjattootaa (CEO)</h2>
        {msg_html}

        <h3 style="font-size: 13px; color:#065f46; margin-bottom:8px;">➕ Hojjataa Haaraa Galmeessi</h3>
        <form method="POST" style="margin-bottom:20px;">
            <input type="hidden" name="action" value="add_user">
            <div class="form-group">
                <input type="text" name="username" placeholder="Username" required class="input-field">
            </div>
            <div class="form-group">
                <input type="password" id="new_user_pwd" name="password" placeholder="Password" required class="input-field">
                <span id="new_user_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('new_user_pwd', 'new_user_pwd_toggle')">👁️</span>
            </div>
            <div class="form-group">
                <select name="role" class="input-field">
                    <option value="MAKER">MAKER</option>
                    <option value="MANAGER">MANAGER</option>
                    <option value="AUDITOR">AUDITOR</option>
                    <option value="CEO">CEO</option>
                </select>
            </div>
            <button type="submit" class="btn-submit">Uumii (Create User)</button>
        </form>

        <hr style="margin:16px 0; border:0; border-top:1px solid #e2e8f0;">

        <h3 style="font-size: 13px; color:#581c87; margin-bottom:8px;">🔑 Password Hojjataa Jijjiiri</h3>
        <form method="POST" style="margin-bottom:20px;">
            <input type="hidden" name="action" value="change_password">
            <div class="form-group">
                <select name="target_user" class="input-field">
                    {''.join([f'<option value="{u["username"]}">{u["username"]} ({u["role"]})</option>' for u in users_list])}
                </select>
            </div>
            <div class="form-group">
                <input type="password" id="chg_user_pwd" name="new_password" placeholder="Password Haaraa" required class="input-field">
                <span id="chg_user_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('chg_user_pwd', 'chg_user_pwd_toggle')">👁️</span>
            </div>
            <button type="submit" class="btn-submit" style="background:#581c87;">Jijjiiri Password</button>
        </form>
    </div>

    <h3 style="font-size:14px; margin-bottom:8px; color:#334155;">👥 Listii Hojjattoota Systema</h3>
    <div class="box" style="padding:0; overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f8fafc; font-size:11px; color:#64748b; border-bottom:1px solid #e2e8f0;">
                    <th style="padding:8px;">User</th>
                    <th style="padding:8px;">Shoora</th>
                    <th style="padding:8px;">Status</th>
                    <th style="padding:8px;">Pass</th>
                    <th style="padding:8px; text-align:right;">Action</th>
                </tr>
            </thead>
            <tbody>
                {users_html}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/toggle_user/<username>')
def toggle_user(username):
    if 'role' not in session or session['role'] != 'CEO':
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if res:
        new_status = 'BLOCKED' if res['status'] == 'ACTIVE' else 'ACTIVE'
        cursor.execute("UPDATE users SET status = ? WHERE username = ?", (new_status, username))
        conn.commit()
    conn.close()
    return redirect('/manage_users')

@app.route('/maker_receipts')
def maker_receipts():
    if 'role' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT txn_id, ft_reference, txn_type, customer_name, amount, status, timestamp, created_by 
        FROM transactions 
        ORDER BY timestamp DESC
    """)
    txns = cursor.fetchall()
    conn.close()

    rows_html = ""
    for t in txns:
        rows_html += f"""
        <div class="item-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:12px; font-weight:bold; color:#065f46;">{t['ft_reference']}</span>
            </div>
            <div style="font-size:13px; font-weight:bold; margin-top:4px;">{t['txn_type']}: {t['amount']:,.2f} Birr</div>
            <div style="font-size:11px; color:#64748b;">Maammila: {t['customer_name']} | Maker: {t['created_by']} | Guyyaa: {t['timestamp']}</div>
            <div style="text-align:right; margin-top:8px;">
                <a href="/receipt/{t['txn_id']}" target="_blank" class="btn-action btn-green">🧾 Nagahee Maxxansi</a>
            </div>
        </div>
        """

    content = f"""
    <h2 style="font-size: 16px; margin-bottom: 12px;">🧾 Nagahee Kaffaltii Baasuu</h2>
    {rows_html if rows_html else "<p style='text-align:center; padding:20px; color:#64748b;'>Kaffaltiini galmaa'e hin jiru.</p>"}
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

# --- 3. REGISTER WITH INITIAL BALANCE (NEGATIVE PREVENTED) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'role' not in session or session['role'] != 'MAKER':
        return "🚫 Shoora MAKER qofatu maammila galmeessuu danda'a", 403

    msg = None
    if request.method == 'POST':
        full_name = request.form.get('full_name').strip()
        phone = request.form.get('phone').strip()
        
        try:
            initial_balance = float(request.form.get('initial_balance', 0.0))
        except ValueError:
            initial_balance = 0.0

        if initial_balance < 0:
            msg = "❌ Error: Balansiin ka'umsaa negative ta'uu HIN DANDA'U!"
        else:
            photo_file = request.files.get('photo')
            sig_file = request.files.get('signature')

            if photo_file and sig_file and allowed_file(photo_file.filename) and allowed_file(sig_file.filename):
                timestamp_str = int(datetime.datetime.now().timestamp())
                photo_filename = f"face_{timestamp_str}_" + secure_filename(photo_file.filename)
                sig_filename = f"sig_{timestamp_str}_" + secure_filename(sig_file.filename)

                photo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
                sig_file.save(os.path.join(app.config['UPLOAD_FOLDER'], sig_filename))

                START_ID = 100099008800
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute("SELECT MAX(CAST(customer_id AS INTEGER)) FROM customers WHERE customer_id >= '100099008800'")
                max_id = cursor.fetchone()[0]

                if max_id is None or max_id < START_ID:
                    cust_id = str(START_ID)
                else:
                    cust_id = str(max_id + 1)

                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute("""
                    INSERT INTO customers (customer_id, full_name, phone, photo_path, signature_path, balance, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', ?)
                """, (cust_id, full_name, phone, photo_filename, sig_filename, initial_balance, now))
                
                # Balansii ka'umsaa deposit akka ta'utti transaction keessattis galmeessuu
                if initial_balance > 0:
                    today_code = datetime.datetime.now().strftime("%y%j")
                    ft_ref = f"FT{today_code}{random.randint(10000, 99999)}"
                    txn_id = f"TXN-INIT-{int(datetime.datetime.now().timestamp())}"
                    cursor.execute("""
                        INSERT INTO transactions (txn_id, txn_type, customer_id, customer_name, target_account, amount, commission, bank_name, ft_reference, status, created_by, timestamp)
                        VALUES (?, 'DEPOSIT', ?, ?, '', ?, 0.0, 'Imana Microfinance Core', ?, 'PENDING_MANAGER', ?, ?)
                    """, (txn_id, cust_id, full_name, initial_balance, ft_ref, session['username'], now))

                conn.commit()
                conn.close()
                auto_backup_db()
                msg = f"⏳ Maammilli {full_name} (Initial Balance: {initial_balance:,.2f} Birr) galmaa'eera! (T24 Acc: {cust_id}). MANAGER'n ACTIVE akka ta'u mirkaneessuu qaba."

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; margin-bottom: 12px;">👤 Galmee Maammilaa T24</h2>
        {f"<p style='background:#fef3c7; color:#92400e; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""}
        <form method="POST" enctype="multipart/form-data">
            <div class="form-group">
                <label>Maqaa Guutuu</label>
                <input type="text" name="full_name" required class="input-field">
            </div>
            <div class="form-group">
                <label>Lakkoofsa Bilbilaa</label>
                <input type="text" name="phone" required class="input-field">
            </div>
            <div class="form-group">
                <label>Balansii Jalqabaa (Initial Balance in Birr)</label>
                <input type="number" step="0.01" min="0" name="initial_balance" value="0.00" required class="input-field">
            </div>
            <div class="form-group">
                <label>📸 Suuraa Fuula Maammilaa</label>
                <input type="file" name="photo" accept="image/*" required class="input-field">
            </div>
            <div class="form-group">
                <label>✍️ Mallattoo Galmee (Signature)</label>
                <input type="file" name="signature" accept="image/*" required class="input-field">
            </div>
            <button type="submit" class="btn-submit">Galmeessi (Create T24 Account)</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

# --- 1. TRANSACTION WITH INSTANT PREVIEW & AUTO PRINT (MAKER) ---
@app.route('/transaction', methods=['GET', 'POST'])
def transaction():
    if 'role' not in session or session['role'] != 'MAKER':
        return "🚫 Shoora MAKER qofatu kaffaltii uumuu danda'a", 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_id, full_name, balance, freeze_status FROM customers WHERE status='ACTIVE'")
    active_customers = cursor.fetchall()
    conn.close()

    msg = None
    msg_color = "#dcfce7"
    text_color = "#166534"
    preview_txn = None

    if request.method == 'POST':
        txn_type = request.form.get('txn_type')
        cust_id = request.form.get('customer_id')
        target_account = request.form.get('target_account', '').strip()
        
        try:
            amount = float(request.form.get('amount', 0.0))
        except ValueError:
            amount = 0.0

        bank_name = request.form.get('bank_name')

        if amount <= 0:
            msg = "❌ Error: Hamma qarshii negetive ykn 0 ta'uu HIN DANDA'U!"
            msg_color = "#fee2e2"
            text_color = "#991b1b"
        else:
            commission = 0.0
            if txn_type == 'WITHDRAWAL':
                commission = get_commission(amount)

            total_deduction = amount + commission

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT full_name, balance, freeze_status FROM customers WHERE customer_id = ?", (cust_id,))
            cust_row = cursor.fetchone()
            cust_name = cust_row['full_name'] if cust_row else "Unknown"
            cust_balance = cust_row['balance'] if cust_row else 0.0
            cust_freeze = cust_row['freeze_status'] if cust_row else "UNFROZEN"

            if cust_freeze == 'FROZEN' and txn_type in ['WITHDRAWAL', 'T24_TRANSFER']:
                msg = f"❌ UGGURA! Maammilli kun CEO'n Uggurameera. Deposit malee Baasii ykn Transfer godhuu HIN DANDA'U!"
                msg_color = "#fee2e2"
                text_color = "#991b1b"
            elif txn_type in ['WITHDRAWAL', 'T24_TRANSFER'] and cust_balance < total_deduction:
                msg = f"❌ Balance Check Failed! Balance maammilaa ({cust_balance:,.2f} Birr) maallaqa gaafatame fi comishiniif ({total_deduction:,.2f} Birr) gadi!"
                msg_color = "#fee2e2"
                text_color = "#991b1b"
            else:
                today_code = datetime.datetime.now().strftime("%y%j")
                ft_ref = f"FT{today_code}{random.randint(10000, 99999)}"
                txn_id = f"TXN-{int(datetime.datetime.now().timestamp())}"
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute("""
                    INSERT INTO transactions (txn_id, txn_type, customer_id, customer_name, target_account, amount, commission, bank_name, ft_reference, status, created_by, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MANAGER', ?, ?)
                """, (txn_id, txn_type, cust_id, cust_name, target_account, amount, commission, bank_name, ft_ref, session['username'], now))
                conn.commit()
                auto_backup_db()
                
                msg = f"⏳ {txn_type} Ref: {ft_ref} ({amount:,.2f} Birr) galmaa'eera! Preview gama jalatti ilaaluun print godhaa."
                
                preview_txn = {
                    'txn_id': txn_id,
                    'ft_reference': ft_ref,
                    'txn_type': txn_type,
                    'customer_name': cust_name,
                    'customer_id': cust_id,
                    'amount': amount,
                    'bank_name': bank_name,
                    'timestamp': now,
                    'created_by': session['username']
                }

            conn.close()

    options_html = "".join([f'<option value="{c["customer_id"]}">{c["full_name"]} (Acc: {c["customer_id"]} | Bal: {c["balance"]:,.2f} Birr {"🔒 FROZEN" if c["freeze_status"]=="FROZEN" else ""})</option>' for c in active_customers])

    preview_html = ""
    if preview_txn:
        preview_html = f"""
        <div class="box" style="background:#f0fdf4; border: 2px dashed #16a34a; margin-top:20px;">
            <h3 style="font-size:14px; color:#166534; text-align:center; margin-bottom:10px;">🧾 LIVE PREVIEW RECEIPT (Nagahee)</h3>
            <div style="font-size:12px; line-height:1.8; color:#0f172a;">
                <div><b>FT Ref:</b> {preview_txn['ft_reference']}</div>
                <div><b>Guyyaa:</b> {preview_txn['timestamp']}</div>
                <div><b>Gosa Hojii:</b> {preview_txn['txn_type']}</div>
                <div><b>Maammila:</b> {preview_txn['customer_name']} ({preview_txn['customer_id']})</div>
                <div><b>Hamma Qarshii:</b> <b style="color:#065f46; font-size:15px;">{preview_txn['amount']:,.2f} Birr</b></div>
                <div><b>Maker:</b> {preview_txn['created_by']}</div>
            </div>
            <div style="text-align:center; margin-top:14px;">
                <a href="/receipt/{preview_txn['txn_id']}" target="_blank" class="btn-submit" style="background:#047857; text-decoration:none; display:inline-block; padding:10px 20px;">🖨️ Nagahee Kaa Maxxansi (Print)</a>
            </div>
        </div>
        """

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; margin-bottom: 12px;">💸 Kaffaltii / Deposit / Withdraw / Transfer</h2>
        {f"<p style='background:{msg_color}; color:{text_color}; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""}

        <form method="POST">
            <div class="form-group">
                <label>Gosa Kaffaltii (Transaction Type)</label>
                <select name="txn_type" id="txn_type" class="input-field" onchange="toggleTransferInput()">
                    <option value="DEPOSIT">📥 DEPOSIT (Galii)</option>
                    <option value="WITHDRAWAL">📤 WITHDRAWAL (Baasii)</option>
                    <option value="T24_TRANSFER">🔄 T24 FUNDS TRANSFER (Transfer)</option>
                </select>
            </div>
            <div class="form-group">
                <label>Moo'ata Akkaawuntii (From Account)</label>
                <select name="customer_id" required class="input-field">
                    {options_html if options_html else '<option value="">Maammilli ACTIVE ta\'e hin jiru</option>'}
                </select>
            </div>

            <div class="form-group" id="transfer_target_div" style="display:none;">
                <label>Gara Akkaawuntii (To Target Account)</label>
                <input type="text" name="target_account" id="target_account" placeholder="Fkn: 100099008800" class="input-field" onkeyup="searchTargetCustomer()">
                <div id="target_name_display" style="font-size:12px; font-weight:bold; color:#065f46; margin-top:4px;"></div>
            </div>

            <div class="form-group">
                <label>Hamma Qarshii (Birr)</label>
                <input type="number" step="0.01" min="0.01" name="amount" required class="input-field">
            </div>
            <div class="form-group">
                <label>Baankii</label>
                <select name="bank_name" class="input-field">
                    <option value="Imana Core">Imana Microfinance Core</option>
                    <option value="CBE (T24 Core)">CBE (T24 Core)</option>
                </select>
            </div>
            <button type="submit" class="btn-submit">Galchi Transaction & Preview Ilaali</button>
        </form>

        {preview_html}
    </div>

    <script>
    function toggleTransferInput() {{
        var type = document.getElementById('txn_type').value;
        var targetDiv = document.getElementById('transfer_target_div');
        targetDiv.style.display = (type === 'T24_TRANSFER') ? 'block' : 'none';
    }}

    function searchTargetCustomer() {{
        var accNo = document.getElementById('target_account').value.trim();
        var displayBox = document.getElementById('target_name_display');
        
        if (accNo.length >= 6) {{
            fetch('/api/get_customer/' + accNo)
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        displayBox.innerHTML = "👤 Maqaa Acc Target: " + data.full_name;
                        displayBox.style.color = "#16a34a";
                    }} else {{
                        displayBox.innerHTML = "❌ " + data.full_name;
                        displayBox.style.color = "#dc2626";
                    }}
                }});
        }} else {{
            displayBox.innerHTML = "";
        }}
    }}
    </script>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/api/get_customer/<cust_id>')
def api_get_customer(cust_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name FROM customers WHERE customer_id = ?", (cust_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return jsonify({'success': True, 'full_name': row['full_name']})
    return jsonify({'success': False, 'full_name': 'Akkaawuntiin Hin Argamne!'})

@app.route('/approve_cust/<cust_id>')
def approve_cust(cust_id):
    if 'role' not in session or session['role'] != 'MANAGER':
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE customers SET status = 'ACTIVE' WHERE customer_id = ?", (cust_id,))
    cursor.execute("SELECT phone, full_name FROM customers WHERE customer_id = ?", (cust_id,))
    c_info = cursor.fetchone()
    conn.commit()
    conn.close()

    if c_info:
        send_sms_alert(c_info['phone'], f"Kabajamoo {c_info['full_name']}, Akkaawuntiin keessan ({cust_id}) ACTIVE ta'ee jira!")

    return redirect('/pending')

# --- 2. STATEMENT WITH ACCURATE RUNNING BALANCE ---
@app.route('/statement/<cust_id>')
def statement(cust_id):
    if 'role' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_id, full_name, phone, balance, status FROM customers WHERE customer_id = ?", (cust_id,))
    cust = cursor.fetchone()

    if not cust:
        conn.close()
        return "Maammilli Hin Argamne", 404

    cursor.execute("""
        SELECT txn_id, ft_reference, txn_type, amount, commission, status, timestamp, created_by, customer_id, target_account
        FROM transactions 
        WHERE (customer_id = ? OR target_account = ?) AND status = 'APPROVED'
        ORDER BY timestamp ASC
    """, (cust_id, cust_id))
    txns = cursor.fetchall()
    conn.close()

    rows_html = ""
    running_balance = 0.0

    for t in txns:
        amt = t['amount']
        comm = t['commission']
        t_type = t['txn_type']
        
        if t_type == 'DEPOSIT':
            running_balance += amt
        elif t_type == 'WITHDRAWAL':
            running_balance -= (amt + comm)
        elif t_type == 'T24_TRANSFER':
            if t['customer_id'] == cust_id:
                running_balance -= amt
            else:
                running_balance += amt

        rows_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0; font-size:11px;">
            <td style="padding:8px;">{t['timestamp']}</td>
            <td style="padding:8px; font-weight:bold;">{t['ft_reference']}</td>
            <td style="padding:8px;">{t_type}</td>
            <td style="padding:8px; font-weight:bold;">{amt:,.2f} Birr</td>
            <td style="padding:8px; font-weight:bold; color:#065f46;">{running_balance:,.2f} Birr</td>
            <td style="padding:8px;">{t['created_by']}</td>
        </tr>
        """

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; margin-bottom: 4px;">📜 Statement Maammilaa (Running Balance)</h2>
        <p style="font-size: 13px; color:#065f46; font-weight:bold;">{cust['full_name']} (Acc: {cust['customer_id']})</p>
        <p style="font-size: 11px; color:#64748b;">📞 {cust['phone']} | Balansii Ammaa: <b style="color:#16a34a;">{cust['balance']:,.2f} Birr</b></p>
    </div>

    <div class="box" style="padding:0; overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f8fafc; font-size:10px; color:#64748b; border-bottom:1px solid #e2e8f0;">
                    <th style="padding:8px;">Guyyaa</th>
                    <th style="padding:8px;">Ref</th>
                    <th style="padding:8px;">Gosa</th>
                    <th style="padding:8px;">Hamma</th>
                    <th style="padding:8px;">Running Balansii</th>
                    <th style="padding:8px;">Maker</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else "<tr><td colspan='6' style='padding:16px; text-align:center; font-size:12px; color:#94a3b8;'>Transaction-ni APPROVED ta'e hin jiru</td></tr>"}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/ceo_commission')
def ceo_commission():
    if 'role' not in session or session['role'] != 'CEO':
        return "🚫 Hayyama CEO Qofa!", 403

    search_date = request.args.get('search_date', datetime.datetime.now().strftime("%Y-%m-%d"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ft_reference, txn_type, customer_name, amount, commission, created_by, timestamp 
        FROM transactions 
        WHERE timestamp LIKE ? AND status='APPROVED' AND commission > 0
        ORDER BY timestamp DESC
    """, (f"{search_date}%",))
    rows = cursor.fetchall()

    cursor.execute("SELECT SUM(commission) FROM transactions WHERE status='APPROVED' AND timestamp LIKE ?", (f"{search_date}%",))
    daily_commission = cursor.fetchone()[0] or 0.0
    conn.close()

    comm_html = "".join([f"""
    <tr style="border-bottom:1px solid #e2e8f0; font-size:11px;">
        <td style="padding:8px; font-weight:bold; color:#581c87;">{r['ft_reference']}</td>
        <td style="padding:8px;">{r['customer_name']}</td>
        <td style="padding:8px;">{r['amount']:,.2f} Birr</td>
        <td style="padding:8px; color:#16a34a; font-weight:bold;">+{r['commission']:,.2f} Birr</td>
        <td style="padding:8px;">{r['created_by']}</td>
        <td style="padding:8px;">{r['timestamp']}</td>
    </tr>
    """ for r in rows])

    content = f"""
    <div style="background:#065f46; color:white; border-radius:16px; padding:20px; margin-bottom:20px;">
        <h2 style="font-size:18px;">💰 Gabaasa Comishinii Guyyaa (CEO)</h2>
        <p style="font-size:11px; opacity:0.8;">Guyyaa Filatame: <b>{search_date}</b></p>
        <div style="margin-top:10px; font-size:24px; font-weight:bold; color:#fbbf24;">
            Waliigala Comishinii: {daily_commission:,.2f} Birr
        </div>
    </div>

    <div class="box">
        <form method="GET" action="/ceo_commission" style="display:flex; gap:8px;">
            <input type="date" name="search_date" value="{search_date}" class="input-field" style="margin:0;">
            <button type="submit" class="btn-submit" style="width:auto; padding:0 16px; background:#065f46;">Barbaadi</button>
        </form>
    </div>

    <div class="box" style="padding:0; overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f8fafc; font-size:10px; color:#64748b; border-bottom:1px solid #e2e8f0;">
                    <th style="padding:8px;">FT Ref</th>
                    <th style="padding:8px;">Maammila</th>
                    <th style="padding:8px;">Hamma Withdrawal</th>
                    <th style="padding:8px;">Comishina</th>
                    <th style="padding:8px;">Maker</th>
                    <th style="padding:8px;">Guyyaa</th>
                </tr>
            </thead>
            <tbody>
                {comm_html if comm_html else "<tr><td colspan='6' style='padding:16px; text-align:center; font-size:12px; color:#94a3b8;'>Guyyaa kana comishiniin galmaa'e hin jiru</td></tr>"}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/auditor_close', methods=['GET', 'POST'])
def auditor_close():
    if 'role' not in session or session['role'] != 'AUDITOR':
        return "🚫 Hayyama AUDITOR Qofa!", 403

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    msg = None

    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE transactions SET audited_status = 'CLOSED_AUDITED' WHERE timestamp LIKE ?", (f"{today_str}%",))
        conn.commit()
        conn.close()
        msg = f"🔒 Herregni guyyaa har'aa ({today_str}) guutumaan guutuutti CUFAMEERA!"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT created_by, 
               SUM(CASE WHEN txn_type='DEPOSIT' AND status='APPROVED' THEN amount ELSE 0 END) as total_dep,
               SUM(CASE WHEN txn_type IN ('WITHDRAWAL', 'T24_TRANSFER') AND status='APPROVED' THEN amount ELSE 0 END) as total_with
        FROM transactions 
        WHERE timestamp LIKE ?
        GROUP BY created_by
    """, (f"{today_str}%",))
    maker_summary = cursor.fetchall()
    conn.close()

    summary_html = "".join([f"""
    <div class="item-card" style="border-left:4px solid #ea580c;">
        <div style="font-size:13px; font-weight:bold; color:#c2410c;">👤 Maker: {m['created_by']}</div>
        <div style="font-size:12px; margin-top:6px; display:grid; grid-template-columns:1fr 1fr;">
            <div>📥 Deposit: <b>{m['total_dep']:,.2f} Birr</b></div>
            <div>📤 Withdrawal: <b>{m['total_with']:,.2f} Birr</b></div>
        </div>
    </div>
    """ for m in maker_summary])

    content = f"""
    <div class="box" style="background:#fff7ed; border-color:#ffedd5;">
        <h2 style="font-size: 16px; color:#c2410c; margin-bottom: 4px;">🔍 Cufiinsa Herrega Galgalaa (Auditor Close)</h2>
        <p style="font-size:11px; color:#9a3412;">Guyyaa: {today_str}</p>
    </div>
    {f"<p style='background:#dcfce7; color:#166534; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""}
    <h3 style="font-size: 13px; color:#334155; margin-bottom:8px;">📊 To'annoo Hojii Maker-oota Guyyaa Har'aa</h3>
    {summary_html if summary_html else "<p style='text-align:center; padding:16px; color:#64748b; font-size:12px;'>Har'a Maker-ni hojjate hin jiru.</p>"}

    <div class="box" style="margin-top:16px; text-align:center;">
        <form method="POST">
            <button type="submit" class="btn-submit" style="background:#ea580c;">🔒 Herrega Guyyaa Galgala Kanaa Cufi</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/ceo_audit')
def ceo_audit():
    if 'role' not in session or session['role'] != 'CEO':
        return "🚫 Hayyama CEO Qofa!", 403

    search_date = request.args.get('search_date', datetime.datetime.now().strftime("%Y-%m-%d"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ft_reference, txn_type, customer_name, amount, commission, status, created_by FROM transactions WHERE timestamp LIKE ? ORDER BY timestamp DESC", (f"{search_date}%",))
    rows = cursor.fetchall()

    cursor.execute("SELECT SUM(commission) FROM transactions WHERE status='APPROVED' AND timestamp LIKE ?", (f"{search_date}%",))
    daily_commission = cursor.fetchone()[0] or 0.0
    conn.close()

    net_cap, deposits, withdraws, cust_bal, total_comm = get_bank_capital()

    txns_html = "".join([f"""
    <tr style="border-bottom:1px solid #e2e8f0; font-size:11px;">
        <td style="padding:8px; font-weight:bold; color:#581c87;">{r['ft_reference']}</td>
        <td style="padding:8px;">{r['txn_type']}</td>
        <td style="padding:8px;">{r['customer_name']}</td>
        <td style="padding:8px;">{r['amount']:,.2f} Birr</td>
        <td style="padding:8px; color:#dc2626; font-weight:bold;">{r['commission']:,.2f} Birr</td>
        <td style="padding:8px;">{r['status']}</td>
        <td style="padding:8px;">{r['created_by']}</td>
    </tr>
    """ for r in rows])

    content = f"""
    <div style="background:#581c87; color:white; border-radius:16px; padding:20px; margin-bottom:20px;">
        <h2 style="font-size:18px;">🌙 Executive Audit & Reports</h2>
        <p style="font-size:11px; opacity:0.8;">Guyyaa Filatame: <b>{search_date}</b></p>
        
        <div style="margin-top:16px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.2); display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:12px;">
            <div>Kaabitaala Baankii: <b>{net_cap:,.2f} Birr</b></div>
            <div>Comishinii Guyyaa: <b style="color:#fef08a;">{daily_commission:,.2f} Birr</b></div>
        </div>
    </div>

    <div class="box">
        <form method="GET" action="/ceo_audit" style="display:flex; gap:8px;">
            <input type="date" name="search_date" value="{search_date}" class="input-field" style="margin:0;">
            <button type="submit" class="btn-submit" style="width:auto; padding:0 16px; background:#581c87;">Barbaadi</button>
        </form>
    </div>

    <div class="box" style="padding:0; overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f8fafc; font-size:10px; color:#64748b; border-bottom:1px solid #e2e8f0;">
                    <th style="padding:8px;">FT Ref</th>
                    <th style="padding:8px;">Gosa</th>
                    <th style="padding:8px;">Maammila</th>
                    <th style="padding:8px;">Hamma</th>
                    <th style="padding:8px;">Comishina</th>
                    <th style="padding:8px;">Status</th>
                    <th style="padding:8px;">Maker</th>
                </tr>
            </thead>
            <tbody>
                {txns_html if txns_html else "<tr><td colspan='7' style='padding:16px; text-align:center; font-size:12px; color:#94a3b8;'>Guyyaa kana transaction-ni hin raawwatamne</td></tr>"}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
