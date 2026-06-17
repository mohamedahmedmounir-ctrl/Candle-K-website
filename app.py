import os, sqlite3, hashlib, re, csv, io, uuid, smtplib, threading, secrets, hmac
from datetime import datetime
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory, flash, g, Response)
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ── Security: secret key from environment ────────────────────────────────────
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATABASE       = os.environ.get('DATABASE_URL', os.path.join(BASE_DIR, 'candle_k.db'))
UPLOAD_FOLDER  = os.path.join(BASE_DIR, 'uploads', 'products')
PAYMENT_FOLDER = os.path.join(BASE_DIR, 'uploads', 'payments')
ALLOWED_EXT    = {'png','jpg','jpeg','gif','webp'}
os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(PAYMENT_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

# ── Secure password hashing (PBKDF2 — no external deps needed) ───────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000).hex()
    return f'pbkdf2:{salt}:{key}'

def check_password(password: str, stored: str) -> bool:
    if stored.startswith('pbkdf2:'):
        _, salt, key = stored.split(':', 2)
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000).hex()
        return hmac.compare_digest(check, key)
    # Legacy sha256 fallback for old passwords
    return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)

# ── CSRF protection ───────────────────────────────────────────────────────────
def generate_csrf():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(16)
    return session['_csrf']

def validate_csrf():
    token = request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
    return token and hmac.compare_digest(token, session.get('_csrf', ''))

app.jinja_env.globals['csrf_token'] = generate_csrf

@app.before_request
def csrf_protect():
    if request.method == 'POST':
        # Skip CSRF for cart/promo AJAX endpoints
        exempt = ['/cart/add','/cart/update','/cart/remove','/promo/apply','/cart/data']
        if request.path not in exempt and not validate_csrf():
            if not request.path.startswith('/admin/login'):
                pass  # enforce after login is stable

# ── Image compression ─────────────────────────────────────────────────────────
def compress_image(filepath, max_size=(1200, 1200), quality=82):
    try:
        from PIL import Image as PILImage
        img = PILImage.open(filepath)
        img = img.convert('RGB')
        img.thumbnail(max_size, PILImage.LANCZOS)
        img.save(filepath, 'JPEG', quality=quality, optimize=True)
    except Exception:
        pass  # non-fatal — keep original if PIL fails

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db: db.close()

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

def slugify(t):
    t = re.sub(r'[^\w\s-]','',t.lower().strip())
    return re.sub(r'[\s_-]+','-',t).strip('-')

def get_all_content():
    rows = get_db().execute('SELECT key,value FROM site_content').fetchall()
    return {r['key']:r['value'] for r in rows}

def get_cart():       return session.get('cart', {})
def cart_count():     return sum(i['qty'] for i in get_cart().values())
def cart_total():     return sum(i['price']*i['qty'] for i in get_cart().values())

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return dec

@app.context_processor
def inject_globals():
    cats = get_db().execute('SELECT * FROM categories ORDER BY sort_order,name').fetchall()
    return dict(categories=cats, site=get_all_content(), cart_count=cart_count(), now=datetime.now())

@app.template_filter('currency')
def currency_fmt(v):
    try: return f"{float(v):,.0f}"
    except: return v

def init_db():
    db = sqlite3.connect(DATABASE)
    c  = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT NOT NULL,
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        description TEXT,
        sort_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        category_id INTEGER,
        price REAL NOT NULL,
        original_price REAL,
        short_description TEXT,
        description TEXT,
        stock INTEGER DEFAULT 0,
        is_featured INTEGER DEFAULT 0,
        is_bestseller INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS product_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        is_primary INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        customer_name TEXT NOT NULL,
        customer_email TEXT,
        customer_phone TEXT NOT NULL,
        address TEXT NOT NULL,
        city TEXT NOT NULL,
        notes TEXT,
        payment_method TEXT NOT NULL,
        payment_screenshot TEXT,
        promo_code TEXT,
        discount_amount REAL DEFAULT 0,
        subtotal REAL NOT NULL,
        shipping REAL DEFAULT 0,
        total REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        tracking_number TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER,
        product_name TEXT NOT NULL,
        product_price REAL NOT NULL,
        quantity INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS order_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL COLLATE NOCASE,
        discount_type TEXT NOT NULL DEFAULT 'percent',
        discount_value REAL NOT NULL,
        min_order REAL DEFAULT 0,
        max_uses INTEGER DEFAULT NULL,
        uses_count INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        expires_at TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS site_content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        value TEXT,
        content_type TEXT DEFAULT 'text'
    );
    CREATE TABLE IF NOT EXISTS testimonials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        text TEXT NOT NULL,
        rating INTEGER DEFAULT 5,
        is_active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS email_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        recipient TEXT NOT NULL,
        subject TEXT NOT NULL,
        email_type TEXT NOT NULL,
        success INTEGER DEFAULT 1,
        error_msg TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        product_id INTEGER NOT NULL,
        customer_name TEXT NOT NULL,
        customer_email TEXT,
        rating INTEGER NOT NULL DEFAULT 5,
        title TEXT,
        body TEXT NOT NULL,
        is_approved INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS wishlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        customer_email TEXT NOT NULL,
        notified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(product_id, customer_email)
    );
    CREATE TABLE IF NOT EXISTS custom_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        customer_name TEXT NOT NULL,
        customer_email TEXT,
        customer_phone TEXT NOT NULL,
        address TEXT,
        city TEXT,
        candle_shape TEXT NOT NULL,
        candle_size TEXT NOT NULL,
        scent TEXT NOT NULL,
        scent_intensity TEXT DEFAULT 'medium',
        color TEXT,
        message_on_candle TEXT,
        quantity INTEGER DEFAULT 1,
        notes TEXT,
        budget TEXT,
        payment_method TEXT DEFAULT 'cod',
        payment_screenshot TEXT,
        calculated_price REAL,
        status TEXT DEFAULT 'pending',
        admin_notes TEXT,
        quoted_price REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS inventory_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        change_type TEXT NOT NULL,
        quantity_change INTEGER NOT NULL,
        quantity_before INTEGER NOT NULL,
        quantity_after INTEGER NOT NULL,
        reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS custom_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        option_type TEXT NOT NULL,
        name TEXT NOT NULL,
        icon TEXT DEFAULT '',
        description TEXT DEFAULT '',
        price_extra REAL DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    """)

    # ── Migrations: add columns safely if upgrading from old schema ──────────
    existing_orders = {r[1] for r in c.execute('PRAGMA table_info(orders)').fetchall()}
    for col,defn in [('promo_code','TEXT'),('discount_amount','REAL DEFAULT 0'),('tracking_number','TEXT')]:
        if col not in existing_orders:
            c.execute(f'ALTER TABLE orders ADD COLUMN {col} {defn}')
    existing_tracking = {r[1] for r in c.execute('PRAGMA table_info(order_tracking)').fetchall()}
    if 'title' not in existing_tracking:
        c.execute('ALTER TABLE order_tracking ADD COLUMN title TEXT NOT NULL DEFAULT ""')

    # Migrate custom_orders to add payment columns if upgrading
    co_cols = {r[1] for r in c.execute('PRAGMA table_info(custom_orders)').fetchall()}
    for col, defn in [('payment_method','TEXT DEFAULT "cod"'),('payment_screenshot','TEXT'),
                      ('calculated_price','REAL'),('address','TEXT'),('city','TEXT')]:
        if col not in co_cols:
            c.execute(f'ALTER TABLE custom_orders ADD COLUMN {col} {defn}')

    # ── Upgrade legacy sha256 admin password to PBKDF2 ───────────────────────
    old_hash = hashlib.sha256(b'admin123').hexdigest()
    admin = c.execute('SELECT * FROM admins WHERE username=?',('admin',)).fetchone()
    if admin is None:
        c.execute('INSERT OR IGNORE INTO admins (username,password_hash) VALUES (?,?)',
                  ('admin', hash_password('admin123')))
    elif admin[2] == old_hash:  # still old sha256 hash — upgrade it
        c.execute('UPDATE admins SET password_hash=? WHERE username=?',
                  (hash_password('admin123'), 'admin'))

    for nm,sl,ds in [
        ('Scented Candles','scented-candles','Luxurious fragrances'),
        ('Decorative Candles','decorative-candles','Beautiful pieces'),
        ('Gift Sets','gift-sets','Perfect gifts'),
        ('Seasonal','seasonal','Limited editions'),
    ]:
        c.execute('INSERT OR IGNORE INTO categories (name,slug,description) VALUES (?,?,?)',(nm,sl,ds))

    # ── New table migrations ─────────────────────────────────────────────────
    # Add estimated_delivery to orders if missing
    ord_cols = {r[1] for r in c.execute('PRAGMA table_info(orders)').fetchall()}
    if 'estimated_delivery' not in ord_cols:
        c.execute('ALTER TABLE orders ADD COLUMN estimated_delivery TEXT')

    # Add stock_reserved to products if missing
    prod_cols = {r[1] for r in c.execute('PRAGMA table_info(products)').fetchall()}
    if 'stock_reserved' not in prod_cols:
        c.execute('ALTER TABLE products ADD COLUMN stock_reserved INTEGER DEFAULT 0')
    if 'restock_threshold' not in prod_cols:
        c.execute('ALTER TABLE products ADD COLUMN restock_threshold INTEGER DEFAULT 5')

    for k,v in [
        ('hero_title','Illuminate Your World'),
        ('hero_subtitle','Handcrafted candles made with pure ingredients, designed to transform any space into a sanctuary of warmth and elegance.'),
        ('hero_cta','Shop Now'),
        ('about_title','The Story of Candle K'),
        ('about_text','Born from a passion for craftsmanship and a love of fragrance, Candle K creates handpoured candles that bring warmth, elegance, and tranquility to your space.'),
        ('about_tagline','Every flame tells a story.'),
        ('promo_title','Free Shipping on Orders Over 500 EGP'),
        ('promo_subtitle','Use code CANDLE20 for 20% off your first order'),
        ('promo_active','true'),
        ('contact_email','hello@candlek.com'),
        ('contact_phone','+20 100 000 0000'),
        ('contact_instagram','@candlek.eg'),
        ('contact_whatsapp','201000000000'),
        ('instapay_number','01000000000'),
        ('instapay_name','Candle K'),
        ('instapay_instructions','Transfer the exact amount to the Instapay number below, then upload a clear screenshot of your payment confirmation.'),
        ('shipping_fee','50'),
        ('free_shipping_threshold','500'),
        ('footer_text','Handcrafted with love in Egypt. Every candle is a small act of self-care.'),
        ('email_host','smtp.gmail.com'),
        ('email_port','587'),
        ('email_user',''),
        ('email_password',''),
        ('email_enabled','false'),
        ('store_name','Candle K'),
        ('store_url','http://localhost:5000'),
        ('currency','EGP'),
        ('meta_description','Candle K — Handcrafted luxury candles made in Egypt. Scented candles, gift sets and more. Shop now.'),
        ('low_stock_threshold','5'),
        ('whatsapp_orders','false'),

        ('delivery_days_min','2'),
        ('delivery_days_max','5'),
        ('custom_order_enabled','true'),
        ('custom_order_min_price','300'),
        ('custom_order_note','Custom candles are handmade to order and take 5-7 business days.'),
        # Custom order pricing (EGP) — admin sets these
        ('custom_price_shape_pillar','150'),
        ('custom_price_shape_jar','120'),
        ('custom_price_shape_votive','80'),
        ('custom_price_shape_taper','100'),
        ('custom_price_shape_sphere','180'),
        ('custom_price_shape_custom','200'),
        ('custom_price_size_small','0'),
        ('custom_price_size_medium','80'),
        ('custom_price_size_large','160'),
        ('custom_price_size_extra_large','240'),
        ('custom_price_scent_surcharge','50'),
        ('custom_price_message_surcharge','30'),
        ('custom_price_shipping','60'),
    ]:
        c.execute('INSERT OR IGNORE INTO site_content (key,value) VALUES (?,?)',(k,v))

    for nm,tx,rt in [
        ('Sara M.','The scent is absolutely divine. My whole apartment smells incredible!',5),
        ('Ahmed K.','Beautiful packaging and the candle burns so cleanly. Perfect gift.',5),
        ('Nour H.','Candle K is on another level. The lavender one is my favorite.',5),
    ]:
        c.execute('INSERT OR IGNORE INTO testimonials (name,text,rating) VALUES (?,?,?)',(nm,tx,rt))

    for p in [
        ('Velvet Rose','velvet-rose',1,350,420,'A rich, romantic blend of red rose and warm musk.','Indulge in the timeless romance of Velvet Rose. Rich rose, warm musk and amber.',50,1,1),
        ('Midnight Oud','midnight-oud',1,420,None,'Deep oriental notes of oud, sandalwood and amber.','Precious oud wood, creamy sandalwood, and golden amber.',30,1,1),
        ('Coastal Breeze','coastal-breeze',1,300,380,'Fresh ocean air with hints of sea salt and driftwood.','Crisp ocean air and warm driftwood.',45,0,1),
        ('Vanilla Dreams','vanilla-dreams',1,280,None,'Warm, comforting vanilla with a hint of caramel.','Rich Madagascar vanilla, sweet caramel, and toasted almond.',60,0,1),
        ('Garden Bloom','garden-bloom',2,320,None,'A floral arrangement of jasmine, peony and fresh greens.','Fresh jasmine, peony, and lush green notes.',35,0,0),
        ('Gift Set Trio','gift-set-trio',3,850,1050,'Three bestselling candles beautifully packaged.','Three beloved fragrances in an elegant gift box.',20,1,0),
    ]:
        c.execute('INSERT OR IGNORE INTO products (name,slug,category_id,price,original_price,short_description,description,stock,is_featured,is_bestseller) VALUES (?,?,?,?,?,?,?,?,?,?)',p)

    for code,dtype,val,mn,mx in [
        ('CANDLE20','percent',20,0,100),
        ('WELCOME10','percent',10,0,None),
        ('SAVE50','fixed',50,300,50),
        ('FREESHIP','shipping',100,200,None),
    ]:
        c.execute('INSERT OR IGNORE INTO promo_codes (code,discount_type,discount_value,min_order,max_uses) VALUES (?,?,?,?,?)',(code,dtype,val,mn,mx))

    # Default custom candle options
    for otype,name,icon,desc,price,sort in [
        ('shape','Pillar','🕯️','Classic tall column',150,1),
        ('shape','Jar','🫙','Glass jar candle',120,2),
        ('shape','Votive','🕍','Small cube/cylinder',80,3),
        ('shape','Taper','🕎','Slim elegant taper',100,4),
        ('shape','Sphere','⚪','Round ball candle',180,5),
        ('size','Small','🤏','~100g · 20hrs burn',0,1),
        ('size','Medium','👋','~200g · 35hrs burn',80,2),
        ('size','Large','✋','~350g · 55hrs burn',160,3),
        ('size','Extra Large','🤲','~500g · 80hrs burn',240,4),
        ('scent','Rose & Musk','🌹','Classic romantic',0,1),
        ('scent','Oud & Amber','🌿','Oriental warmth',0,2),
        ('scent','Vanilla & Caramel','🍯','Sweet comfort',0,3),
        ('scent','Jasmine & Peony','🌸','Fresh floral',0,4),
        ('scent','Lavender & Mint','💜','Calm & fresh',0,5),
        ('scent','Citrus & Bergamot','🍋','Bright & uplifting',0,6),
        ('scent','Sandalwood & Cedar','🪵','Warm woody',0,7),
        ('scent','Ocean Breeze','🌊','Clean & airy',0,8),
        ('scent','Coffee & Cardamom','☕','Rich & spiced',0,9),
        ('scent','Custom Blend','✨','Describe your own',50,10),
    ]:
        c.execute('INSERT OR IGNORE INTO custom_options (option_type,name,icon,description,price_extra,sort_order) VALUES (?,?,?,?,?,?)',
                  (otype,name,icon,desc,price,sort))

    db.commit(); db.close()

# ── EMAIL ─────────────────────────────────────────────────────────────────────
STATUS_INFO = {
    'pending':              ('Order Received','We have received your order and are preparing it.'),
    'pending_verification': ('Payment Under Review','Your Instapay screenshot is being reviewed by our team.'),
    'paid':                 ('Payment Confirmed','Your payment has been confirmed. We are now preparing your order.'),
    'processing':           ('Being Prepared','Your order is being carefully packed by hand.'),
    'shipped':              ('Order Shipped','Your order has been shipped. Track it with the number below.'),
    'out_for_delivery':     ('Out for Delivery','Your candles are out for delivery today!'),
    'delivered':            ('Order Delivered','Your order has been delivered. Enjoy your candles!'),
    'cancelled':            ('Order Cancelled','Your order has been cancelled. Contact us if you have questions.'),
}

def send_order_email(order_id, email_type, site, tracking_events=None):
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    order = db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    items = db.execute('SELECT * FROM order_items WHERE order_id=?',(order_id,)).fetchall()
    db.close()
    if not order or not order['customer_email']:
        return
    _build_and_send(dict(order),[dict(i) for i in items],email_type,site,tracking_events,order_id)

def _build_and_send(order,items,email_type,site,tracking_events,order_id):
    store=site.get('store_name','Candle K'); url=site.get('store_url','http://localhost:5000'); year=datetime.now().year
    CD='#1E1C19'; CM='#2C2A27'; CI='#F7F5EC'; CS='#E8E2D4'; CC='#FAF8F2'
    CP='#C97878'; CPL='#E8A0A0'; CG='#7FA882'; CD2='#8A8278'; CX='#B8B0A6'
    CONFIGS={
        'order_placed':        {'subject':f'Order Confirmed ✓ — {order["order_number"]}','banner':'🕯️ Thank you for your order!','color':CP,'greeting':f'Thank you, {order["customer_name"]}!','body':'Your order has been received and is being lovingly prepared. We\'ll keep you updated every step of the way.','icon':'✨'},
        'pending_verification':{'subject':f'Payment Under Review — {order["order_number"]}','banner':'🔍 Reviewing your payment','color':'#C8A45A','greeting':f'Hi {order["customer_name"]}!','body':'We\'ve received your Instapay screenshot and our team is reviewing it. This usually takes under 2 hours.','icon':'🔍'},
        'paid':                {'subject':f'Payment Confirmed ✓ — {order["order_number"]}','banner':'✅ Payment confirmed!','color':CG,'greeting':f'Great news, {order["customer_name"]}!','body':'Your payment has been verified and your order is now being carefully prepared.','icon':'✅'},
        'processing':          {'subject':f'Your Order is Being Prepared — {order["order_number"]}','banner':'📦 Packing your candles','color':'#7A8FBF','greeting':f'Hi {order["customer_name"]}!','body':'Your candles are being carefully packed by hand. You\'ll get a shipping notification soon.','icon':'📦'},
        'shipped':             {'subject':f'Your Order is on its Way! 🚚 — {order["order_number"]}','banner':'🚚 Your candles have shipped!','color':'#7A8FBF','greeting':f'Exciting news, {order["customer_name"]}!','body':'Your order has left and is on its way to you. Use your tracking number below to follow the journey.','icon':'🚚'},
        'out_for_delivery':    {'subject':f'Out for Delivery Today! 🏠 — {order["order_number"]}','banner':'🏠 Arriving today!','color':'#C8A45A','greeting':f'Today\'s the day, {order["customer_name"]}!','body':'Your candles are out for delivery and will arrive at your door today.','icon':'🏠'},
        'delivered':           {'subject':f'Your Candles Have Arrived! 🎉 — {order["order_number"]}','banner':'🎉 Delivered with love!','color':CG,'greeting':f'Welcome to the glow, {order["customer_name"]}! 🕯️','body':'Your order has been delivered. We hope every candle fills your space with warmth and light. If anything isn\'t perfect, reply to this email.','icon':'🎉'},
        'cancelled':           {'subject':f'Order Cancelled — {order["order_number"]}','banner':'Order Cancelled','color':'#B0776A','greeting':f'Hi {order["customer_name"]},','body':'Your order has been cancelled. If this was a mistake, please contact us and we\'ll be happy to help.','icon':'❌'},
        'status_update':       {'subject':f'Order Update — {order["order_number"]}','banner':STATUS_INFO.get(order["status"],("Order Update",""))[0],'color':CP,'greeting':f'Hi {order["customer_name"]}!','body':STATUS_INFO.get(order["status"],("","Your order status has been updated."))[1],'icon':'📬'},
    }
    cfg=CONFIGS.get(email_type,CONFIGS['status_update'])
    irows=''.join(f'<tr><td style="padding:14px 0;border-bottom:1px solid {CS};font-family:Georgia,serif;font-size:14px;color:{CM};">{i["product_name"]}</td><td style="padding:14px 0;border-bottom:1px solid {CS};text-align:center;color:{CD2};font-size:13px;">×{i["quantity"]}</td><td style="padding:14px 0;border-bottom:1px solid {CS};text-align:right;font-family:Georgia,serif;font-size:14px;color:{CM};white-space:nowrap;">{int(i["product_price"]*i["quantity"]):,} EGP</td></tr>' for i in items)
    ship_txt='FREE 🎁' if order['shipping']==0 else f'{int(order["shipping"]):,} EGP'
    disc_row=''
    if order.get('discount_amount') and float(order['discount_amount'])>0:
        lbl=f' ({order["promo_code"]})' if order.get('promo_code') else ''
        disc_row=f'<tr><td colspan="2" style="padding:6px 0;text-align:right;font-size:13px;color:{CG};">Discount{lbl}</td><td style="padding:6px 0;text-align:right;font-size:13px;color:{CG};">−{int(order["discount_amount"]):,} EGP</td></tr>'
    tnum_html=''
    if order.get('tracking_number'):
        tnum_html=f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;"><tr><td style="background:{CC};border:1px solid {CS};border-radius:8px;padding:18px;text-align:center;"><div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:{CD2};margin-bottom:6px;">Tracking Number</div><div style="font-family:Georgia,serif;font-size:1.6rem;color:{CM};">{order["tracking_number"]}</div></td></tr></table>'
    tl_html=''
    if tracking_events and len(tracking_events)>0:
        evts=''
        for idx,ev in enumerate(reversed(tracking_events)):
            il=idx==0; dc=cfg['color'] if il else CS; db_=f'border:3px solid {cfg["color"]};' if il else f'border:2px solid {CS};'; ck='<span style="color:white;font-size:9px;font-weight:bold;">✓</span>' if il else ''
            evts+=f'<tr><td style="width:22px;padding-top:4px;vertical-align:top;"><div style="width:22px;height:22px;border-radius:50%;background:{dc};{db_}display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;">{ck}</div></td><td style="padding:0 0 18px 14px;"><div style="font-size:13px;font-weight:700;color:{CM if il else CD2};">{ev.get("title","")}</div><div style="font-size:12px;color:{CD2};margin-top:3px;">{ev.get("message","") or ""}</div><div style="font-size:11px;color:{CX};margin-top:4px;">{str(ev.get("created_at",""))[:16]}</div></td></tr>'
        tl_html=f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;background:{CC};border:1px solid {CS};border-radius:8px;padding:20px;"><tr><td colspan="2" style="padding-bottom:16px;"><div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:{CD2};">Order Timeline</div></td></tr>{evts}</table>'
    html=f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>{cfg['subject']}</title></head>
<body style="margin:0;padding:0;background:{CI};font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;">{cfg['greeting']} {cfg['body'][:80]}...</div>
<table width="100%" cellpadding="0" cellspacing="0" style="background:{CI};padding:40px 20px;"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
<tr><td style="background:{CD};border-radius:12px 12px 0 0;padding:40px 48px 32px;text-align:center;">
  <div style="font-family:Georgia,serif;font-size:32px;color:{CI};letter-spacing:.25em;text-transform:uppercase;">{store}</div>
  <div style="font-size:12px;color:{CPL};letter-spacing:.1em;font-style:italic;margin-bottom:28px;">Pure light, pure comfort</div>
  <div style="width:40px;height:1px;background:{CP};margin:0 auto 28px;"></div>
  <div style="font-size:36px;margin-bottom:12px;">{cfg['icon']}</div>
  <div style="font-family:Georgia,serif;font-size:22px;color:{CI};">{cfg['banner']}</div>
</td></tr>
<tr><td style="background:{cfg['color']};padding:0;height:4px;"></td></tr>
<tr><td style="background:#fff;padding:40px 48px;">
  <p style="font-family:Georgia,serif;font-size:20px;color:{CM};margin:0 0 12px;">{cfg['greeting']}</p>
  <p style="font-size:15px;color:{CD2};line-height:1.8;margin:0 0 32px;">{cfg['body']}</p>
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;"><tr><td style="background:{CC};border:1px solid {CS};border-radius:8px;padding:18px 24px;text-align:center;">
    <div style="font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:{CD2};margin-bottom:6px;">Order Number</div>
    <div style="font-family:Georgia,serif;font-size:22px;color:{CM};">{order['order_number']}</div>
    <div style="font-size:11px;color:{CX};margin-top:6px;">Placed {str(order.get('created_at',''))[:10]}</div>
  </td></tr></table>
  {tnum_html}{tl_html}
  <div style="height:1px;background:{CS};margin:0 0 24px;"></div>
  <div style="font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:{CD2};margin-bottom:4px;">Order Summary</div>
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:{CX};padding:8px 0;">Item</td><td style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:{CX};padding:8px 0;text-align:center;">Qty</td><td style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:{CX};padding:8px 0;text-align:right;">Price</td></tr>
    {irows}
    <tr><td colspan="2" style="padding:10px 0 4px;text-align:right;font-size:13px;color:{CD2};">Subtotal</td><td style="padding:10px 0 4px;text-align:right;font-size:13px;color:{CM};">{int(order['subtotal']):,} EGP</td></tr>
    <tr><td colspan="2" style="padding:4px 0;text-align:right;font-size:13px;color:{CD2};">Shipping</td><td style="padding:4px 0;text-align:right;font-size:13px;color:{CM};">{ship_txt}</td></tr>
    {disc_row}
    <tr style="border-top:2px solid {CS};"><td colspan="2" style="padding:14px 0 8px;text-align:right;font-family:Georgia,serif;font-size:16px;color:{CM};">Total</td><td style="padding:14px 0 8px;text-align:right;font-family:Georgia,serif;font-size:20px;color:{CM};white-space:nowrap;">{int(order['total']):,} EGP</td></tr>
  </table>
  <div style="margin:20px 0 0;font-size:12px;color:{CD2};">Payment: <strong style="color:{CM};">{'Cash on Delivery' if order['payment_method']=='cod' else 'Instapay'}</strong> · Status: <strong style="color:{cfg['color']};">{order['status'].replace('_',' ').title()}</strong></div>
  <div style="height:1px;background:{CS};margin:28px 0;"></div>
  <div style="font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:{CD2};margin-bottom:12px;">Delivery Address</div>
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:14px;color:{CM};line-height:1.6;">{order['address']},<br>{order['city']}</td>
    <td style="text-align:right;font-size:14px;color:{CD2};vertical-align:top;">📞 {order['customer_phone']}</td>
  </tr></table>
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:36px;"><tr><td align="center">
    <a href="{url}/track/{order['order_number']}" style="display:inline-block;background:{CD};color:{CI};text-decoration:none;padding:15px 40px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;">Track My Order &rarr;</a>
  </td></tr></table>
</td></tr>
<tr><td style="background:{CD};border-radius:0 0 12px 12px;padding:32px 48px;text-align:center;">
  <div style="font-family:Georgia,serif;font-size:14px;color:rgba(247,245,236,.5);font-style:italic;margin-bottom:16px;">Every flame tells a story.</div>
  <div style="width:32px;height:1px;background:rgba(201,120,120,.4);margin:0 auto 16px;"></div>
  <div style="font-size:12px;color:rgba(247,245,236,.45);line-height:2;">{site.get('contact_email','hello@candlek.com')}<br>{site.get('contact_phone','')} · {site.get('contact_instagram','')}</div>
  <div style="margin-top:16px;font-size:11px;color:rgba(247,245,236,.2);">© {year} {store} · Handcrafted with ♥ in Egypt<br><span style="font-size:10px;">You received this because you placed an order with us.</span></div>
</td></tr>
<tr><td style="height:24px;"></td></tr>
</table></td></tr></table>
</body></html>"""
    subject=cfg['subject']
    def _send():
        host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
        user=site.get('email_user',''); pw=site.get('email_password','')
        enabled=site.get('email_enabled','false').lower()=='true'
        succ=1; err=''
        if enabled and user and pw:
            try:
                msg=MIMEMultipart('alternative'); msg['Subject']=subject; msg['From']=f'{store} <{user}>'; msg['To']=order['customer_email']
                msg.attach(MIMEText(html,'html','utf-8'))
                with smtplib.SMTP(host,port,timeout=15) as s:
                    s.ehlo(); s.starttls(); s.login(user,pw); s.sendmail(user,order['customer_email'],msg.as_string())
            except Exception as exc: succ=0; err=str(exc)
        try:
            db2=sqlite3.connect(DATABASE)
            db2.execute('INSERT INTO email_log (order_id,recipient,subject,email_type,success,error_msg) VALUES (?,?,?,?,?,?)',(order_id,order['customer_email'],subject,email_type,succ,err))
            db2.commit(); db2.close()
        except: pass
    threading.Thread(target=_send,daemon=True).start()

# ── STATIC FILES ──────────────────────────────────────────────────────────────
@app.route('/uploads/products/<filename>')
def uploaded_product(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/payments/<filename>')
def uploaded_payment(filename):
    return send_from_directory(PAYMENT_FOLDER, filename)

@app.route('/admin/payment-proof/<filename>')
@admin_required
def admin_payment_proof(filename):
    return send_from_directory(PAYMENT_FOLDER, filename)

# ── STOREFRONT ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    db=get_db()
    featured    =db.execute('SELECT p.*,pi.filename as image FROM products p LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1 WHERE p.is_featured=1 AND p.is_active=1 LIMIT 6').fetchall()
    bestsellers =db.execute('SELECT p.*,pi.filename as image FROM products p LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1 WHERE p.is_bestseller=1 AND p.is_active=1 LIMIT 4').fetchall()
    testimonials=db.execute('SELECT * FROM testimonials WHERE is_active=1').fetchall()
    return render_template('index.html',featured=featured,bestsellers=bestsellers,testimonials=testimonials)

@app.route('/shop')
def shop():
    db=get_db(); cat=request.args.get('category',''); qs=request.args.get('q',''); sort=request.args.get('sort','newest')
    q='SELECT p.*,pi.filename as image,c.name as category_name FROM products p LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1 LEFT JOIN categories c ON c.id=p.category_id WHERE p.is_active=1'
    params=[]
    if cat: q+=' AND c.slug=?'; params.append(cat)
    if qs:  q+=' AND (p.name LIKE ? OR p.short_description LIKE ?)'; params+=[f'%{qs}%']*2
    q+={'price_asc':' ORDER BY p.price ASC','price_desc':' ORDER BY p.price DESC'}.get(sort,' ORDER BY p.created_at DESC')
    products=db.execute(q,params).fetchall()
    current_cat=db.execute('SELECT * FROM categories WHERE slug=?',(cat,)).fetchone() if cat else None
    return render_template('shop.html',products=products,current_cat=current_cat,search=qs,sort=sort)

@app.route('/product/<slug>')
def product_detail(slug):
    db=get_db()
    product=db.execute('SELECT p.*,c.name as category_name,c.slug as category_slug FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.slug=? AND p.is_active=1',(slug,)).fetchone()
    if not product: return redirect(url_for('shop'))
    images =db.execute('SELECT * FROM product_images WHERE product_id=? ORDER BY is_primary DESC,sort_order',(product['id'],)).fetchall()
    related=db.execute('SELECT p.*,pi.filename as image FROM products p LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1 WHERE p.category_id=? AND p.id!=? AND p.is_active=1 LIMIT 4',(product['category_id'],product['id'])).fetchall()
    return render_template('product.html',product=product,images=images,related=related)

@app.route('/cart')
def cart():
    data=get_cart(); items=[{**i,'product_id':pid} for pid,i in data.items()]
    sub=sum(i['price']*i['qty'] for i in items); c=get_all_content()
    ship=float(c.get('shipping_fee',50)); thresh=float(c.get('free_shipping_threshold',500))
    if sub>=thresh: ship=0
    return render_template('cart.html',items=items,subtotal=sub,shipping=ship,total=sub+ship,threshold=thresh)

@app.route('/cart/add', methods=['POST'])
def cart_add():
    pid=str(request.form.get('product_id')); qty=int(request.form.get('qty',1))
    db=get_db(); p=db.execute('SELECT * FROM products WHERE id=? AND is_active=1',(pid,)).fetchone()
    if not p: return jsonify(error='Not found'),404
    if p['stock'] <= 0: return jsonify(error='Out of stock'),400
    img=db.execute('SELECT filename FROM product_images WHERE product_id=? AND is_primary=1',(pid,)).fetchone()
    cart=get_cart()
    if pid in cart: cart[pid]['qty']+=qty
    else: cart[pid]={'name':p['name'],'price':p['price'],'qty':qty,'image':img['filename'] if img else None}
    session['cart']=cart; session.modified=True
    return jsonify(success=True,cart_count=cart_count(),cart_total=cart_total())

@app.route('/cart/update', methods=['POST'])
def cart_update():
    pid=str(request.form.get('product_id')); qty=int(request.form.get('qty',1))
    cart=get_cart()
    if pid in cart:
        if qty<=0: del cart[pid]
        else: cart[pid]['qty']=qty
    session['cart']=cart; session.modified=True
    return redirect(url_for('cart'))

@app.route('/cart/remove', methods=['POST'])
def cart_remove():
    pid=str(request.form.get('product_id')); cart=get_cart(); cart.pop(pid,None)
    session['cart']=cart; session.modified=True
    return redirect(url_for('cart'))

@app.route('/cart/data')
def cart_data():
    cart=get_cart(); items=[{'product_id':pid,'name':i['name'],'price':i['price'],'qty':i['qty'],'image':i.get('image')} for pid,i in cart.items()]
    sub=sum(i['price']*i['qty'] for i in items); c=get_all_content()
    ship=float(c.get('shipping_fee',50))
    if sub>=float(c.get('free_shipping_threshold',500)): ship=0
    return jsonify(items=items,subtotal=sub,shipping=ship,total=sub+ship,count=sum(i['qty'] for i in items))

@app.route('/promo/apply', methods=['POST'])
def promo_apply():
    code=request.form.get('code','').strip().upper(); subtotal=float(request.form.get('subtotal',cart_total()))
    db=get_db()
    promo=db.execute("SELECT * FROM promo_codes WHERE code=? COLLATE NOCASE AND is_active=1 AND (expires_at IS NULL OR expires_at>datetime('now')) AND (max_uses IS NULL OR uses_count<max_uses)",(code,)).fetchone()
    if not promo: return jsonify(success=False,error='Invalid or expired promo code.')
    if subtotal<promo['min_order']: return jsonify(success=False,error=f'Minimum order of {int(promo["min_order"])} EGP required.')
    dtype=promo['discount_type']; val=promo['discount_value']
    if dtype=='percent': discount=round(subtotal*val/100,2); label=f'{int(val)}% off'
    elif dtype=='fixed':  discount=min(val,subtotal); label=f'{int(val)} EGP off'
    elif dtype=='shipping': discount=0; label='Free shipping'
    else: discount=0; label=''
    return jsonify(success=True,code=code,discount=discount,type=dtype,label=label,message=f'Code applied: {label}!')

@app.route('/checkout', methods=['GET','POST'])
def checkout():
    cart_data=get_cart()
    if not cart_data: return redirect(url_for('cart'))
    content=get_all_content()
    ship_fee=float(content.get('shipping_fee',50)); thresh=float(content.get('free_shipping_threshold',500))
    subtotal=cart_total()
    if subtotal>=thresh: ship_fee=0

    if request.method=='POST':
        promo_code=request.form.get('promo_code','').strip().upper()
        discount_amount=float(request.form.get('discount_amount',0))
        payment_method=request.form.get('payment_method','cod')
        screenshot=None; db=get_db(); promo=None

        if promo_code:
            promo=db.execute("SELECT * FROM promo_codes WHERE code=? COLLATE NOCASE AND is_active=1 AND (expires_at IS NULL OR expires_at>datetime('now')) AND (max_uses IS NULL OR uses_count<max_uses)",(promo_code,)).fetchone()
            if promo:
                if promo['discount_type']=='shipping': ship_fee=0
                elif promo['discount_type']=='percent': discount_amount=round(subtotal*promo['discount_value']/100,2)
                elif promo['discount_type']=='fixed':   discount_amount=min(promo['discount_value'],subtotal)
            else: promo_code=''; discount_amount=0

        total=subtotal-discount_amount+ship_fee

        if payment_method=='instapay' and 'payment_screenshot' in request.files:
            f=request.files['payment_screenshot']
            if f and f.filename:
                ext='jpg'
                if '.' in f.filename:
                    e=f.filename.rsplit('.',1)[1].lower()
                    if e in ALLOWED_EXT: ext=e
                fn='payment_'+uuid.uuid4().hex+'.'+ext
                save_path=os.path.join(PAYMENT_FOLDER,fn)
                os.makedirs(PAYMENT_FOLDER,exist_ok=True)
                f.save(save_path)
                compress_image(save_path)
                screenshot=fn

        order_num='CK-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:6].upper()
        c=db.execute('INSERT INTO orders (order_number,customer_name,customer_email,customer_phone,address,city,notes,payment_method,payment_screenshot,promo_code,discount_amount,subtotal,shipping,total,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (order_num,request.form.get('name'),request.form.get('email'),request.form.get('phone'),
             request.form.get('address'),request.form.get('city'),request.form.get('notes'),
             payment_method,screenshot,promo_code or None,discount_amount,subtotal,ship_fee,total,
             'pending_verification' if payment_method=='instapay' else 'pending'))
        order_id=c.lastrowid

        for pid,item in cart_data.items():
            db.execute('INSERT INTO order_items (order_id,product_id,product_name,product_price,quantity) VALUES (?,?,?,?,?)',(order_id,pid,item['name'],item['price'],item['qty']))

        # ── Deduct stock ──────────────────────────────────────────────────────
        for pid,item in cart_data.items():
            db.execute('UPDATE products SET stock=MAX(0,stock-?) WHERE id=?',(item['qty'],pid))

        status='pending_verification' if payment_method=='instapay' else 'pending'
        title='Order Received' if payment_method=='cod' else 'Awaiting Payment Verification'
        msg='Your order has been received. Thank you for choosing Candle K!' if payment_method=='cod' else 'We received your Instapay screenshot and are reviewing it.'
        db.execute('INSERT INTO order_tracking (order_id,status,title,message) VALUES (?,?,?,?)',(order_id,status,title,msg))
        if promo: db.execute('UPDATE promo_codes SET uses_count=uses_count+1 WHERE id=?',(promo['id'],))
        db.commit()
        session.pop('cart',None); session['last_order']=order_num
        send_order_email(order_id,'order_placed',content)
        # Log inventory changes
        for pid, item in cart_data.items():
            log_inventory_change(db, pid, 'sale', -item['qty'], order_num)
        db.commit()
        # Estimated delivery
        est_delivery = calculate_delivery_date(content)
        db.execute('UPDATE orders SET estimated_delivery=? WHERE id=?',(est_delivery, order_id))
        db.commit()
        return redirect(url_for('order_success'))

    return render_template('checkout.html',subtotal=subtotal,shipping=ship_fee,total=subtotal+ship_fee,content=content)

@app.route('/order-success')
def order_success():
    num=session.get('last_order')
    if not num: return redirect(url_for('index'))
    order=get_db().execute('SELECT * FROM orders WHERE order_number=?',(num,)).fetchone()
    return render_template('order_success.html',order=order)

@app.route('/track')
@app.route('/track/<order_number>')
def order_track(order_number=None):
    order,items,tracking=None,[],[]
    if order_number:
        db=get_db()
        order=db.execute('SELECT * FROM orders WHERE order_number=?',(order_number,)).fetchone()
        if order:
            items=db.execute('SELECT * FROM order_items WHERE order_id=?',(order['id'],)).fetchall()
            tracking=db.execute('SELECT * FROM order_tracking WHERE order_id=? ORDER BY created_at ASC',(order['id'],)).fetchall()
    return render_template('track.html',order=order,items=items,tracking=tracking,searched=order_number)

# ── ADMIN ─────────────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if session.get('admin_logged_in'): return redirect(url_for('admin_dashboard'))
    error,locked=None,False
    if request.method=='POST':
        db=get_db(); ip=request.remote_addr or '0.0.0.0'
        cnt=db.execute("SELECT COUNT(*) as c FROM login_attempts WHERE ip=? AND attempted_at>datetime('now','-15 minutes')",(ip,)).fetchone()['c']
        if cnt>=5: locked=True; error='Too many attempts. Wait 15 minutes.'
        else:
            username=request.form.get('username','').strip()
            password=request.form.get('password','')
            admin=db.execute('SELECT * FROM admins WHERE username=?',(username,)).fetchone()
            if admin and check_password(password, admin['password_hash']):
                db.execute('DELETE FROM login_attempts WHERE ip=?',(ip,)); db.commit()
                session.clear(); session['admin_logged_in']=True; session['admin_username']=admin['username']; session['admin_id']=admin['id']; session.permanent=True
                return redirect(url_for('admin_dashboard'))
            db.execute('INSERT INTO login_attempts (ip) VALUES (?)',(ip,)); db.commit()
            rem=max(0,5-(cnt+1)); error=f'Wrong credentials. {rem} attempt{"s" if rem!=1 else ""} remaining.'
    return render_template('admin/login.html',error=error,locked=locked)

@app.route('/admin/logout')
def admin_logout():
    session.clear(); return redirect(url_for('admin_login'))

@app.route('/admin')
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    db     = get_db()
    thresh = int(get_all_content().get('low_stock_threshold', 5))
    low_stock    = db.execute('SELECT * FROM products WHERE stock<=? AND stock>0 AND is_active=1 ORDER BY stock ASC',(thresh,)).fetchall()
    out_of_stock = db.execute('SELECT COUNT(*) as c FROM products WHERE stock=0 AND is_active=1').fetchone()['c']
    pending_reviews = db.execute('SELECT COUNT(*) as c FROM reviews WHERE is_approved=0').fetchone()['c']
    pending_custom  = db.execute("SELECT COUNT(*) as c FROM custom_orders WHERE status='pending'").fetchone()['c']
    return render_template('admin/dashboard.html',
        total_orders   = db.execute('SELECT COUNT(*) as c FROM orders').fetchone()['c'],
        total_revenue  = db.execute("SELECT COALESCE(SUM(total),0) as s FROM orders WHERE status!='cancelled'").fetchone()['s'],
        pending_orders = db.execute("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending','pending_verification')").fetchone()['c'],
        total_products = db.execute('SELECT COUNT(*) as c FROM products WHERE is_active=1').fetchone()['c'],
        recent_orders  = db.execute('SELECT * FROM orders ORDER BY created_at DESC LIMIT 10').fetchall(),
        bestsellers    = db.execute('SELECT p.name,SUM(oi.quantity) as total_sold FROM order_items oi JOIN products p ON p.id=oi.product_id GROUP BY oi.product_id ORDER BY total_sold DESC LIMIT 5').fetchall(),
        low_stock      = low_stock,
        out_of_stock   = out_of_stock,
        pending_reviews= pending_reviews,
        pending_custom = pending_custom,
    )

@app.route('/admin/products')
@admin_required
def admin_products():
    db=get_db()
    return render_template('admin/products.html',
        products=db.execute('SELECT p.*,c.name as category_name,pi.filename as image FROM products p LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1 ORDER BY p.created_at DESC').fetchall(),
        categories=db.execute('SELECT * FROM categories').fetchall())

@app.route('/admin/products/add', methods=['GET','POST'])
@admin_required
def admin_product_add():
    db=get_db(); cats=db.execute('SELECT * FROM categories').fetchall()
    if request.method=='POST':
        name=request.form.get('name'); slug=slugify(name); base=slug; n=1
        while db.execute('SELECT id FROM products WHERE slug=?',(slug,)).fetchone(): slug=f'{base}-{n}'; n+=1
        db.execute('INSERT INTO products (name,slug,category_id,price,original_price,short_description,description,stock,is_featured,is_bestseller,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (name,slug,request.form.get('category_id') or None,float(request.form.get('price',0)),
             float(request.form.get('original_price')) if request.form.get('original_price') else None,
             request.form.get('short_description'),request.form.get('description'),int(request.form.get('stock',0)),
             1 if request.form.get('is_featured') else 0,1 if request.form.get('is_bestseller') else 0,
             1 if request.form.get('is_active') else 0))
        pid=db.execute('SELECT id FROM products WHERE slug=?',(slug,)).fetchone()['id']
        for i,f in enumerate(request.files.getlist('images')):
            if f and f.filename:
                ext=f.filename.rsplit('.',1)[1].lower() if '.' in f.filename else 'jpg'
                if ext not in ALLOWED_EXT: continue
                fn=f'{slug}_{uuid.uuid4().hex[:8]}.{ext}'
                fp=os.path.join(UPLOAD_FOLDER,fn); f.save(fp); compress_image(fp)
                db.execute('INSERT INTO product_images (product_id,filename,is_primary,sort_order) VALUES (?,?,?,?)',(pid,fn,1 if i==0 else 0,i))
        db.commit(); flash('Product added!','success'); return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html',product=None,categories=cats)

@app.route('/admin/products/edit/<int:pid>', methods=['GET','POST'])
@admin_required
def admin_product_edit(pid):
    db=get_db(); product=db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
    cats=db.execute('SELECT * FROM categories').fetchall()
    images=db.execute('SELECT * FROM product_images WHERE product_id=? ORDER BY is_primary DESC,sort_order',(pid,)).fetchall()
    if request.method=='POST':
        db.execute('UPDATE products SET name=?,category_id=?,price=?,original_price=?,short_description=?,description=?,stock=?,is_featured=?,is_bestseller=?,is_active=? WHERE id=?',
            (request.form.get('name'),request.form.get('category_id') or None,float(request.form.get('price',0)),
             float(request.form.get('original_price')) if request.form.get('original_price') else None,
             request.form.get('short_description'),request.form.get('description'),int(request.form.get('stock',0)),
             1 if request.form.get('is_featured') else 0,1 if request.form.get('is_bestseller') else 0,
             1 if request.form.get('is_active') else 0,pid))
        has=db.execute('SELECT id FROM product_images WHERE product_id=? AND is_primary=1',(pid,)).fetchone()
        slug=db.execute('SELECT slug FROM products WHERE id=?',(pid,)).fetchone()['slug']
        for i,f in enumerate(request.files.getlist('images')):
            if f and f.filename:
                ext=f.filename.rsplit('.',1)[1].lower() if '.' in f.filename else 'jpg'
                if ext not in ALLOWED_EXT: continue
                fn=f'{slug}_{uuid.uuid4().hex[:8]}.{ext}'
                fp=os.path.join(UPLOAD_FOLDER,fn); f.save(fp); compress_image(fp)
                db.execute('INSERT INTO product_images (product_id,filename,is_primary,sort_order) VALUES (?,?,?,?)',(pid,fn,1 if(not has and i==0) else 0,i))
        db.commit(); flash('Product updated!','success'); return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html',product=product,categories=cats,images=images)

@app.route('/admin/products/delete/<int:pid>', methods=['POST'])
@admin_required
def admin_product_delete(pid):
    get_db().execute('UPDATE products SET is_active=0 WHERE id=?',(pid,)); get_db().commit()
    flash('Product removed.','success'); return redirect(url_for('admin_products'))

@app.route('/admin/products/image/delete/<int:iid>', methods=['POST'])
@admin_required
def admin_image_delete(iid):
    db=get_db(); img=db.execute('SELECT * FROM product_images WHERE id=?',(iid,)).fetchone()
    if img:
        try: os.remove(os.path.join(UPLOAD_FOLDER,img['filename']))
        except: pass
        db.execute('DELETE FROM product_images WHERE id=?',(iid,)); db.commit()
    return redirect(request.referrer or url_for('admin_products'))

@app.route('/admin/products/image/primary/<int:iid>', methods=['POST'])
@admin_required
def admin_image_primary(iid):
    db=get_db(); img=db.execute('SELECT * FROM product_images WHERE id=?',(iid,)).fetchone()
    if img:
        db.execute('UPDATE product_images SET is_primary=0 WHERE product_id=?',(img['product_id'],))
        db.execute('UPDATE product_images SET is_primary=1 WHERE id=?',(iid,)); db.commit()
    return redirect(request.referrer or url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    db=get_db(); status=request.args.get('status','')
    q='SELECT * FROM orders'+('' if not status else ' WHERE status=?')+' ORDER BY created_at DESC'
    orders=db.execute(q,([status] if status else [])).fetchall()
    return render_template('admin/orders.html',orders=orders,current_status=status)

@app.route('/admin/orders/<int:oid>')
@admin_required
def admin_order_detail(oid):
    db=get_db()
    return render_template('admin/order_detail.html',
        order=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone(),
        items=db.execute('SELECT * FROM order_items WHERE order_id=?',(oid,)).fetchall(),
        tracking=db.execute('SELECT * FROM order_tracking WHERE order_id=? ORDER BY created_at ASC',(oid,)).fetchall())

@app.route('/admin/orders/<int:oid>/status', methods=['POST'])
@admin_required
def admin_order_status(oid):
    db=get_db(); new_status=request.form.get('status')
    track_msg=request.form.get('tracking_message','').strip()
    track_num=request.form.get('tracking_number','').strip()
    db.execute('UPDATE orders SET status=? WHERE id=?',(new_status,oid))
    if track_num: db.execute('UPDATE orders SET tracking_number=? WHERE id=?',(track_num,oid))
    title,auto=STATUS_INFO.get(new_status,('Status Updated','Order status has been updated.'))
    db.execute('INSERT INTO order_tracking (order_id,status,title,message) VALUES (?,?,?,?)',(oid,new_status,title,track_msg or auto))
    db.commit()
    content=get_all_content()
    tracking=db.execute('SELECT * FROM order_tracking WHERE order_id=? ORDER BY created_at ASC',(oid,)).fetchall()
    etype=new_status if new_status in ('shipped','out_for_delivery','delivered','cancelled','paid') else 'status_update'
    send_order_email(oid,etype,content,[dict(t) for t in tracking])
    flash('Status updated! Customer notified by email.','success')
    return redirect(url_for('admin_order_detail',oid=oid))

@app.route('/admin/orders/export')
@admin_required
def admin_orders_export():
    orders=get_db().execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(['Order Number','Customer','Phone','Email','City','Subtotal','Discount','Shipping','Total','Promo','Payment','Status','Date'])
    for o in orders:
        w.writerow([o['order_number'],o['customer_name'],o['customer_phone'],o['customer_email'],o['city'],o['subtotal'],o['discount_amount'],o['shipping'],o['total'],o['promo_code'],o['payment_method'],o['status'],o['created_at']])
    out.seek(0)
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=orders.csv'})

@app.route('/admin/promos', methods=['GET','POST'])
@admin_required
def admin_promos():
    db=get_db()
    if request.method=='POST':
        a=request.form.get('action')
        if a=='add':
            db.execute('INSERT OR IGNORE INTO promo_codes (code,discount_type,discount_value,min_order,max_uses,expires_at) VALUES (?,?,?,?,?,?)',
                (request.form.get('code','').upper(),request.form.get('discount_type','percent'),float(request.form.get('discount_value',0)),
                 float(request.form.get('min_order',0)),int(request.form.get('max_uses')) if request.form.get('max_uses') else None,
                 request.form.get('expires_at') or None))
            flash('Promo created!','success')
        elif a=='toggle': db.execute('UPDATE promo_codes SET is_active=NOT is_active WHERE id=?',(request.form.get('id'),))
        elif a=='delete': db.execute('DELETE FROM promo_codes WHERE id=?',(request.form.get('id'),))
        db.commit(); return redirect(url_for('admin_promos'))
    return render_template('admin/promos.html',promos=db.execute('SELECT * FROM promo_codes ORDER BY created_at DESC').fetchall())

@app.route('/admin/content', methods=['GET','POST'])
@admin_required
def admin_content():
    db=get_db()
    if request.method=='POST':
        for k,v in request.form.items():
            if k!='_csrf': db.execute('INSERT OR REPLACE INTO site_content (key,value) VALUES (?,?)',(k,v))
        db.commit(); flash('Content updated!','success'); return redirect(url_for('admin_content'))
    return render_template('admin/content.html',content=get_all_content())

@app.route('/admin/testimonials', methods=['GET','POST'])
@admin_required
def admin_testimonials():
    db=get_db()
    if request.method=='POST':
        a=request.form.get('action')
        if a=='add': db.execute('INSERT INTO testimonials (name,text,rating) VALUES (?,?,?)',(request.form.get('name'),request.form.get('text'),int(request.form.get('rating',5))))
        elif a=='delete': db.execute('DELETE FROM testimonials WHERE id=?',(request.form.get('id'),))
        elif a=='toggle': db.execute('UPDATE testimonials SET is_active=NOT is_active WHERE id=?',(request.form.get('id'),))
        db.commit(); return redirect(url_for('admin_testimonials'))
    return render_template('admin/testimonials.html',testimonials=db.execute('SELECT * FROM testimonials ORDER BY id DESC').fetchall())

@app.route('/admin/categories', methods=['GET','POST'])
@admin_required
def admin_categories():
    db=get_db()
    if request.method=='POST':
        a=request.form.get('action')
        if a=='add':
            name=request.form.get('name')
            db.execute('INSERT INTO categories (name,slug,description) VALUES (?,?,?)',(name,slugify(name),request.form.get('description','')))
        elif a=='delete': db.execute('DELETE FROM categories WHERE id=?',(request.form.get('id'),))
        db.commit(); return redirect(url_for('admin_categories'))
    return render_template('admin/categories.html',categories=db.execute('SELECT * FROM categories ORDER BY sort_order').fetchall())

@app.route('/admin/change-password', methods=['GET','POST'])
@admin_required
def admin_change_password():
    error=None
    if request.method=='POST':
        db=get_db()
        admin=db.execute('SELECT * FROM admins WHERE username=?',(session['admin_username'],)).fetchone()
        if not admin or not check_password(request.form.get('current_password',''), admin['password_hash']):
            error='Current password incorrect.'
        elif request.form.get('new_password')!=request.form.get('confirm_password'):
            error='Passwords do not match.'
        elif len(request.form.get('new_password',''))<8:
            error='Password must be at least 8 characters.'
        else:
            db.execute('UPDATE admins SET password_hash=? WHERE id=?',(hash_password(request.form.get('new_password')),admin['id']))
            db.commit(); flash('Password changed!','success'); return redirect(url_for('admin_dashboard'))
    return render_template('admin/change_password.html',error=error)

@app.route('/admin/email-log')
@admin_required
def admin_email_log():
    return render_template('admin/email_log.html',logs=get_db().execute('SELECT * FROM email_log ORDER BY sent_at DESC LIMIT 200').fetchall())



# ══════════════════════════════════════════════════════
#  MOBILE API — JSON endpoints for React Native app
# ══════════════════════════════════════════════════════

def api_response(data=None, error=None, status=200):
    if error:
        return jsonify({'success': False, 'error': error}), status
    return jsonify({'success': True, **(data or {})}), status

# ── Products ──────────────────────────────────────────
@app.route('/api/products')
def api_products():
    db   = get_db()
    cat  = request.args.get('category', '')
    q    = request.args.get('q', '')
    sort = request.args.get('sort', 'newest')
    sql  = """SELECT p.*,pi.filename as image,c.name as category_name,c.slug as category_slug
              FROM products p
              LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1
              LEFT JOIN categories c ON c.id=p.category_id
              WHERE p.is_active=1"""
    params = []
    if cat: sql += ' AND c.slug=?'; params.append(cat)
    if q:   sql += ' AND (p.name LIKE ? OR p.short_description LIKE ?)'; params += [f'%{q}%']*2
    sql += {'price_asc':' ORDER BY p.price ASC','price_desc':' ORDER BY p.price DESC'}.get(sort,' ORDER BY p.created_at DESC')
    products = [dict(p) for p in db.execute(sql, params).fetchall()]
    site = get_all_content()
    base = site.get('store_url','http://localhost:5000')
    for p in products:
        p['image_url'] = f"{base}/uploads/products/{p['image']}" if p.get('image') else None
    return api_response({'products': products})

@app.route('/api/products/<slug>')
def api_product(slug):
    db = get_db()
    p  = db.execute("""SELECT p.*,c.name as category_name,c.slug as category_slug
                       FROM products p LEFT JOIN categories c ON c.id=p.category_id
                       WHERE p.slug=? AND p.is_active=1""", (slug,)).fetchone()
    if not p: return api_response(error='Not found', status=404)
    product = dict(p)
    images  = db.execute('SELECT * FROM product_images WHERE product_id=? ORDER BY is_primary DESC,sort_order', (p['id'],)).fetchall()
    related = db.execute("""SELECT p.*,pi.filename as image FROM products p
                            LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1
                            WHERE p.category_id=? AND p.id!=? AND p.is_active=1 LIMIT 4""",
                         (p['category_id'], p['id'])).fetchall()
    site = get_all_content()
    base = site.get('store_url','http://localhost:5000')
    product['images']  = [{'id':i['id'],'url':f"{base}/uploads/products/{i['filename']}",'is_primary':i['is_primary']} for i in images]
    product['related'] = [{'id':r['id'],'name':r['name'],'slug':r['slug'],'price':r['price'],
                           'image_url':f"{base}/uploads/products/{r['image']}" if r.get('image') else None} for r in related]
    return api_response({'product': product})

@app.route('/api/categories')
def api_categories():
    cats = get_db().execute('SELECT * FROM categories ORDER BY sort_order,name').fetchall()
    return api_response({'categories': [dict(c) for c in cats]})

@app.route('/api/featured')
def api_featured():
    db   = get_db()
    site = get_all_content()
    base = site.get('store_url','http://localhost:5000')
    featured     = db.execute("""SELECT p.*,pi.filename as image FROM products p
                                  LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1
                                  WHERE p.is_featured=1 AND p.is_active=1 LIMIT 6""").fetchall()
    bestsellers  = db.execute("""SELECT p.*,pi.filename as image FROM products p
                                  LEFT JOIN product_images pi ON pi.product_id=p.id AND pi.is_primary=1
                                  WHERE p.is_bestseller=1 AND p.is_active=1 LIMIT 4""").fetchall()
    testimonials = db.execute('SELECT * FROM testimonials WHERE is_active=1 LIMIT 6').fetchall()
    def enrich(rows):
        out = []
        for r in rows:
            d = dict(r)
            d['image_url'] = f"{base}/uploads/products/{d['image']}" if d.get('image') else None
            out.append(d)
        return out
    return api_response({
        'featured':     enrich(featured),
        'bestsellers':  enrich(bestsellers),
        'testimonials': [dict(t) for t in testimonials],
        'site':         site,
    })

# ── Cart (session-based) ──────────────────────────────
@app.route('/api/cart', methods=['GET'])
def api_cart_get():
    cart  = get_cart()
    items = [{'product_id':pid, **i} for pid,i in cart.items()]
    sub   = sum(i['price']*i['qty'] for i in items)
    c     = get_all_content()
    ship  = float(c.get('shipping_fee',50))
    if sub >= float(c.get('free_shipping_threshold',500)): ship = 0
    return api_response({'items':items,'subtotal':sub,'shipping':ship,'total':sub+ship,'count':sum(i['qty'] for i in items)})

@app.route('/api/cart/add', methods=['POST'])
def api_cart_add():
    data = request.get_json() or {}
    pid  = str(data.get('product_id'))
    qty  = int(data.get('qty', 1))
    db   = get_db()
    p    = db.execute('SELECT * FROM products WHERE id=? AND is_active=1',(pid,)).fetchone()
    if not p: return api_response(error='Product not found', status=404)
    if p['stock'] <= 0: return api_response(error='Out of stock', status=400)
    img  = db.execute('SELECT filename FROM product_images WHERE product_id=? AND is_primary=1',(pid,)).fetchone()
    site = get_all_content()
    base = site.get('store_url','http://localhost:5000')
    cart = get_cart()
    if pid in cart: cart[pid]['qty'] += qty
    else: cart[pid] = {'name':p['name'],'price':p['price'],'qty':qty,
                       'image':img['filename'] if img else None,
                       'image_url':f"{base}/uploads/products/{img['filename']}" if img else None}
    session['cart'] = cart; session.modified = True
    return api_response({'cart_count': cart_count(), 'message': f"{p['name']} added to cart"})

@app.route('/api/cart/update', methods=['POST'])
def api_cart_update():
    data = request.get_json() or {}
    pid  = str(data.get('product_id'))
    qty  = int(data.get('qty', 0))
    cart = get_cart()
    if pid in cart:
        if qty <= 0: del cart[pid]
        else: cart[pid]['qty'] = qty
    session['cart'] = cart; session.modified = True
    return api_response({'cart_count': cart_count()})

@app.route('/api/cart/remove', methods=['POST'])
def api_cart_remove():
    data = request.get_json() or {}
    pid  = str(data.get('product_id'))
    cart = get_cart(); cart.pop(pid, None)
    session['cart'] = cart; session.modified = True
    return api_response({'cart_count': cart_count()})

@app.route('/api/cart/clear', methods=['POST'])
def api_cart_clear():
    session.pop('cart', None); session.modified = True
    return api_response({'message': 'Cart cleared'})

# ── Promo ─────────────────────────────────────────────
@app.route('/api/promo/apply', methods=['POST'])
def api_promo_apply():
    data     = request.get_json() or {}
    code     = data.get('code','').strip().upper()
    subtotal = float(data.get('subtotal', cart_total()))
    db       = get_db()
    promo    = db.execute("""SELECT * FROM promo_codes WHERE code=? COLLATE NOCASE
                              AND is_active=1
                              AND (expires_at IS NULL OR expires_at>datetime('now'))
                              AND (max_uses IS NULL OR uses_count<max_uses)""", (code,)).fetchone()
    if not promo: return api_response(error='Invalid or expired promo code.', status=400)
    if subtotal < promo['min_order']:
        return api_response(error=f'Minimum order of {int(promo["min_order"])} EGP required.', status=400)
    dtype = promo['discount_type']; val = promo['discount_value']
    if dtype=='percent':  discount=round(subtotal*val/100,2); label=f'{int(val)}% off'
    elif dtype=='fixed':  discount=min(val,subtotal);         label=f'{int(val)} EGP off'
    elif dtype=='shipping': discount=0;                       label='Free shipping'
    else: discount=0; label=''
    return api_response({'code':code,'discount':discount,'type':dtype,'label':label,'message':f'Code applied: {label}!'})

# ── Checkout ──────────────────────────────────────────
@app.route('/api/checkout', methods=['POST'])
def api_checkout():
    # Accept JSON body or form data
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict()

    cart_data = get_cart()
    if not cart_data: return api_response(error='Cart is empty', status=400)

    required = ['name','phone','address','city','payment_method']
    for field in required:
        if not data.get(field):
            return api_response(error=f'{field} is required', status=400)

    content  = get_all_content()
    ship_fee = float(content.get('shipping_fee',50))
    thresh   = float(content.get('free_shipping_threshold',500))
    subtotal = cart_total()
    if subtotal >= thresh: ship_fee = 0

    promo_code      = data.get('promo_code','').strip().upper()
    discount_amount = float(data.get('discount_amount', 0))
    payment_method  = data.get('payment_method','cod')
    screenshot      = None

    db = get_db(); promo = None
    if promo_code:
        promo = db.execute("""SELECT * FROM promo_codes WHERE code=? COLLATE NOCASE
                               AND is_active=1 AND (expires_at IS NULL OR expires_at>datetime('now'))
                               AND (max_uses IS NULL OR uses_count<max_uses)""", (promo_code,)).fetchone()
        if promo:
            if promo['discount_type']=='shipping':  ship_fee=0
            elif promo['discount_type']=='percent':  discount_amount=round(subtotal*promo['discount_value']/100,2)
            elif promo['discount_type']=='fixed':    discount_amount=min(promo['discount_value'],subtotal)
        else: promo_code=''; discount_amount=0

    total = subtotal - discount_amount + ship_fee

    # Handle Instapay screenshot upload
    if payment_method=='instapay' and 'payment_screenshot' in request.files:
        f = request.files['payment_screenshot']
        if f and f.filename:
            ext = 'jpg'
            if '.' in f.filename:
                e = f.filename.rsplit('.',1)[1].lower()
                if e in ALLOWED_EXT: ext = e
            fn = 'payment_'+uuid.uuid4().hex+'.'+ext
            save_path = os.path.join(PAYMENT_FOLDER, fn)
            os.makedirs(PAYMENT_FOLDER, exist_ok=True)
            f.save(save_path); compress_image(save_path)
            screenshot = fn

    order_num = 'CK-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:6].upper()
    c = db.execute("""INSERT INTO orders
        (order_number,customer_name,customer_email,customer_phone,address,city,notes,
         payment_method,payment_screenshot,promo_code,discount_amount,subtotal,shipping,total,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (order_num, data.get('name'), data.get('email'), data.get('phone'),
         data.get('address'), data.get('city'), data.get('notes'),
         payment_method, screenshot, promo_code or None, discount_amount,
         subtotal, ship_fee, total,
         'pending_verification' if payment_method=='instapay' else 'pending'))
    order_id = c.lastrowid

    for pid, item in cart_data.items():
        db.execute('INSERT INTO order_items (order_id,product_id,product_name,product_price,quantity) VALUES (?,?,?,?,?)',
                   (order_id, pid, item['name'], item['price'], item['qty']))
        db.execute('UPDATE products SET stock=MAX(0,stock-?) WHERE id=?', (item['qty'], pid))

    status_label = 'pending_verification' if payment_method=='instapay' else 'pending'
    title = 'Order Received' if payment_method=='cod' else 'Awaiting Payment Verification'
    msg   = 'Your order has been received. Thank you for choosing Candle K!' if payment_method=='cod' else 'We received your Instapay screenshot and are reviewing it.'
    db.execute('INSERT INTO order_tracking (order_id,status,title,message) VALUES (?,?,?,?)', (order_id,status_label,title,msg))
    if promo: db.execute('UPDATE promo_codes SET uses_count=uses_count+1 WHERE id=?', (promo['id'],))
    db.commit()

    session.pop('cart', None); session['last_order'] = order_num
    send_order_email(order_id, 'order_placed', content)

    return api_response({
        'order_number': order_num,
        'order_id':     order_id,
        'total':        total,
        'status':       status_label,
        'message':      'Order placed successfully!',
    })

# ── Order Tracking ────────────────────────────────────
@app.route('/api/track/<order_number>')
def api_track(order_number):
    db    = get_db()
    order = db.execute('SELECT * FROM orders WHERE order_number=?', (order_number,)).fetchone()
    if not order: return api_response(error='Order not found', status=404)
    items    = db.execute('SELECT * FROM order_items WHERE order_id=?', (order['id'],)).fetchall()
    tracking = db.execute('SELECT * FROM order_tracking WHERE order_id=? ORDER BY created_at ASC', (order['id'],)).fetchall()
    return api_response({
        'order':    dict(order),
        'items':    [dict(i) for i in items],
        'tracking': [dict(t) for t in tracking],
    })

# ── Push Notification Token Registration ─────────────
@app.route('/api/push/register', methods=['POST'])
def api_push_register():
    data  = request.get_json() or {}
    token = data.get('token','').strip()
    order = data.get('order_number','').strip()
    if not token: return api_response(error='Token required', status=400)
    db = get_db()
    # Store push token linked to order for notifications
    try:
        existing = {r[1] for r in db.execute('PRAGMA table_info(push_tokens)').fetchall()}
        if not existing:
            db.execute("""CREATE TABLE IF NOT EXISTS push_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                order_number TEXT,
                customer_email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        db.execute('INSERT INTO push_tokens (token,order_number) VALUES (?,?)', (token,order))
        db.commit()
    except: pass
    return api_response({'message': 'Token registered'})

# ── Site Settings (for app config) ───────────────────
@app.route('/api/settings')
def api_settings():
    c = get_all_content()
    # Only expose safe public fields to the app
    return api_response({'settings': {
        'store_name':              c.get('store_name','Candle K'),
        'store_url':               c.get('store_url',''),
        'contact_phone':           c.get('contact_phone',''),
        'contact_email':           c.get('contact_email',''),
        'contact_whatsapp':        c.get('contact_whatsapp',''),
        'contact_instagram':       c.get('contact_instagram',''),
        'instapay_number':         c.get('instapay_number',''),
        'instapay_name':           c.get('instapay_name',''),
        'instapay_instructions':   c.get('instapay_instructions',''),
        'shipping_fee':            c.get('shipping_fee','50'),
        'free_shipping_threshold': c.get('free_shipping_threshold','500'),
        'promo_title':             c.get('promo_title',''),
        'promo_subtitle':          c.get('promo_subtitle',''),
        'promo_active':            c.get('promo_active','false'),
        'currency':                c.get('currency','EGP'),
        'hero_title':              c.get('hero_title',''),
        'hero_subtitle':           c.get('hero_subtitle',''),
    }})

# ── Admin API: Send push to customer ─────────────────
@app.route('/api/admin/push/send', methods=['POST'])
@admin_required
def api_admin_push_send():
    data         = request.get_json() or {}
    order_number = data.get('order_number','')
    title        = data.get('title','Order Update')
    body         = data.get('body','Your order has been updated.')
    db           = get_db()
    try:
        tokens = db.execute('SELECT DISTINCT token FROM push_tokens WHERE order_number=?',(order_number,)).fetchall()
        if not tokens: return api_response({'sent': 0, 'message': 'No tokens found'})
        sent = 0
        for t in tokens:
            _send_expo_push(t['token'], title, body, {'order_number': order_number})
            sent += 1
        return api_response({'sent': sent})
    except Exception as e:
        return api_response(error=str(e), status=500)

def _send_expo_push(token, title, body, data=None):
    """Send push notification via Expo Push Notification service."""
    import urllib.request, json as _json
    payload = _json.dumps({
        'to':    token,
        'title': title,
        'body':  body,
        'data':  data or {},
        'sound': 'default',
        'badge': 1,
    }).encode()
    req = urllib.request.Request(
        'https://exp.host/--/api/v2/push/send',
        data=payload,
        headers={'Content-Type':'application/json','Accept':'application/json'},
        method='POST'
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ══════════════════════════════════════════════════════
#  INVENTORY ENGINE
# ══════════════════════════════════════════════════════

def log_inventory_change(db, product_id, change_type, qty_change, reference=''):
    prod = db.execute('SELECT stock FROM products WHERE id=?',(product_id,)).fetchone()
    if not prod: return
    before = prod['stock']
    after  = max(0, before + qty_change)
    db.execute('INSERT INTO inventory_log (product_id,change_type,quantity_change,quantity_before,quantity_after,reference) VALUES (?,?,?,?,?,?)',
               (product_id, change_type, qty_change, before, after, reference))

def notify_wishlist_back_in_stock(product_id, site):
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    product   = db.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
    wishlists = db.execute('SELECT * FROM wishlist WHERE product_id=? AND notified=0',(product_id,)).fetchall()
    if not product or not wishlists:
        db.close(); return
    store = site.get('store_name','Candle K')
    url   = site.get('store_url','http://localhost:5000')
    year  = datetime.now().year
    for w in wishlists:
        em  = w['customer_email']
        subj = f"Back in Stock! {product['name']} — {store}"
        html = f"""<!DOCTYPE html><html><body style="margin:0;padding:32px 16px;background:#F7F5EC;font-family:Arial,sans-serif;">
<table width="560" style="max-width:560px;margin:0 auto;"><tr><td style="background:#2C2A27;border-radius:12px 12px 0 0;padding:28px;text-align:center;">
<div style="font-family:Georgia,serif;font-size:24px;color:#F7F5EC;letter-spacing:.2em;">{store}</div></td></tr>
<tr><td style="background:#fff;padding:36px;text-align:center;">
<div style="font-size:48px;margin-bottom:16px;">🕯️</div>
<h1 style="font-family:Georgia,serif;font-size:26px;color:#2C2A27;margin:0 0 12px;">It's back in stock!</h1>
<p style="font-size:15px;color:#7A736A;line-height:1.8;margin:0 0 20px;">
<strong style="color:#2C2A27;">{product['name']}</strong> is available again.<br>
You added it to your wishlist — grab it before it sells out!
</p>
<div style="font-family:Georgia,serif;font-size:24px;color:#2C2A27;margin-bottom:28px;">{int(product['price']):,} EGP</div>
<a href="{url}/product/{product['slug']}" style="display:inline-block;background:#2C2A27;color:#F7F5EC;text-decoration:none;padding:14px 36px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;">Shop Now &#8594;</a>
</td></tr>
<tr><td style="background:#2C2A27;border-radius:0 0 12px 12px;padding:18px;text-align:center;">
<div style="font-size:11px;color:rgba(247,245,236,.3);">&#169; {year} {store} &middot; Made with &#9825; in Egypt</div>
</td></tr></table></body></html>"""
        def _send(e=em, s=subj, h=html):
            host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
            user=site.get('email_user',''); pw=site.get('email_password','')
            if not (site.get('email_enabled','false').lower()=='true' and user and pw): return
            try:
                msg=MIMEMultipart('alternative'); msg['Subject']=s
                msg['From']=f'{store} <{user}>'; msg['To']=e
                msg.attach(MIMEText(h,'html','utf-8'))
                with smtplib.SMTP(host,port,timeout=15) as conn:
                    conn.ehlo(); conn.starttls(); conn.login(user,pw); conn.sendmail(user,e,msg.as_string())
            except: pass
        threading.Thread(target=_send, daemon=True).start()
        db.execute('UPDATE wishlist SET notified=1 WHERE id=?',(w['id'],))
    db.commit(); db.close()

def calculate_delivery_date(site):
    from datetime import timedelta
    min_days = int(site.get('delivery_days_min',2))
    max_days = int(site.get('delivery_days_max',5))
    def add_biz(start, days):
        cur=start; added=0
        while added < days:
            cur += timedelta(days=1)
            if cur.weekday() != 4: added += 1
        return cur
    early = add_biz(datetime.now(), min_days)
    late  = add_biz(datetime.now(), max_days)
    if early.month == late.month:
        return f"{early.strftime('%b')} {early.day}–{late.day}"
    return f"{early.strftime('%b %d')} – {late.strftime('%b %d')}"

# ══════════════════════════════════════════════════════
#  REVIEWS
# ══════════════════════════════════════════════════════

@app.route('/api/reviews/<int:product_id>')
def api_reviews(product_id):
    reviews = get_db().execute(
        'SELECT * FROM reviews WHERE product_id=? AND is_approved=1 ORDER BY created_at DESC',(product_id,)).fetchall()
    avg = 0
    if reviews:
        avg = round(sum(r['rating'] for r in reviews) / len(reviews), 1)
    return jsonify({'success':True,'reviews':[dict(r) for r in reviews],'average':avg,'count':len(reviews)})

@app.route('/api/reviews/submit', methods=['POST'])
def api_submit_review():
    data = request.get_json() or {}
    if not data.get('body') or not data.get('name') or not data.get('product_id'):
        return jsonify({'success':False,'error':'Name, review and product required'})
    get_db().execute('INSERT INTO reviews (order_id,product_id,customer_name,customer_email,rating,title,body) VALUES (?,?,?,?,?,?,?)',
               (data.get('order_id'), data['product_id'], data['name'], data.get('email'),
                int(data.get('rating',5)), data.get('title'), data['body']))
    get_db().commit()
    return jsonify({'success':True,'message':'Review submitted! It appears after approval.'})

# ══════════════════════════════════════════════════════
#  WISHLIST
# ══════════════════════════════════════════════════════

@app.route('/wishlist/add', methods=['POST'])
def wishlist_add():
    pid   = request.form.get('product_id')
    email = request.form.get('email','').strip()
    if not email:
        return jsonify({'success':False,'error':'Email required'})
    db = get_db()
    try:
        db.execute('INSERT OR IGNORE INTO wishlist (product_id,customer_email) VALUES (?,?)',(pid,email))
        db.commit()
        return jsonify({'success':True,'message':"Added! We'll email you when it's back in stock."})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)})

@app.route('/api/wishlist/add', methods=['POST'])
def api_wishlist_add():
    data = request.get_json() or {}
    pid  = data.get('product_id'); email = data.get('email','').strip()
    if not email: return jsonify({'success':False,'error':'Email required'})
    if not pid:   return jsonify({'success':False,'error':'Product required'})
    db = get_db()
    p  = db.execute('SELECT name FROM products WHERE id=?',(pid,)).fetchone()
    if not p: return jsonify({'success':False,'error':'Product not found'})
    try:
        db.execute('INSERT OR IGNORE INTO wishlist (product_id,customer_email) VALUES (?,?)',(pid,email))
        db.commit()
        return jsonify({'success':True,'message':f"We'll email you when {p['name']} is back!"})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)})

# ══════════════════════════════════════════════════════
#  CUSTOM CANDLE ORDERS
# ══════════════════════════════════════════════════════

@app.route('/custom', methods=['GET','POST'])
def custom_order():
    content = get_all_content()
    if request.method == 'POST':
        order_num      = 'CKC-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:6].upper()
        payment_method = request.form.get('payment_method','cod')
        screenshot     = None

        # Handle Instapay screenshot upload
        if payment_method == 'instapay' and 'payment_screenshot' in request.files:
            f = request.files['payment_screenshot']
            if f and f.filename:
                ext = 'jpg'
                if '.' in f.filename:
                    e = f.filename.rsplit('.',1)[1].lower()
                    if e in ALLOWED_EXT: ext = e
                fn = 'custom_payment_'+uuid.uuid4().hex+'.'+ext
                save_path = os.path.join(PAYMENT_FOLDER, fn)
                os.makedirs(PAYMENT_FOLDER, exist_ok=True)
                f.save(save_path)
                compress_image(save_path)
                screenshot = fn

        # Get calculated price from hidden field
        calc_price = request.form.get('calculated_price')

        db = get_db()
        db.execute('''INSERT INTO custom_orders
            (order_number,customer_name,customer_email,customer_phone,address,city,
             candle_shape,candle_size,scent,scent_intensity,color,
             message_on_candle,quantity,notes,budget,
             payment_method,payment_screenshot,calculated_price)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (order_num,
             request.form.get('name'),       request.form.get('email'),
             request.form.get('phone'),      request.form.get('address'),
             request.form.get('city'),       request.form.get('candle_shape'),
             request.form.get('candle_size'),request.form.get('scent'),
             request.form.get('scent_intensity','medium'),
             request.form.get('color'),      request.form.get('message_on_candle'),
             int(request.form.get('quantity',1)),
             request.form.get('notes'),      request.form.get('budget'),
             payment_method, screenshot,
             float(calc_price) if calc_price else None))
        db.commit()
        _notify_admin_custom_order(order_num, request.form.to_dict(), content)
        send_custom_order_email(order_num, request.form.to_dict(), content, 'placed')
        flash(f'Custom order submitted! Reference: {order_num} — Check your email for confirmation.', 'success')
        return redirect(url_for('custom_order'))

    # Load options from DB for the form
    db      = get_db()
    shapes  = db.execute("SELECT * FROM custom_options WHERE option_type='shape' AND is_active=1 ORDER BY sort_order").fetchall()
    sizes   = db.execute("SELECT * FROM custom_options WHERE option_type='size'  AND is_active=1 ORDER BY sort_order").fetchall()
    scents  = db.execute("SELECT * FROM custom_options WHERE option_type='scent' AND is_active=1 ORDER BY sort_order").fetchall()
    return render_template('custom_order.html', content=content, shapes=shapes, sizes=sizes, scents=scents)

@app.route('/api/custom-order', methods=['POST'])
def api_custom_order():
    data = request.get_json() or {}
    for f in ['name','phone','candle_shape','candle_size','scent']:
        if not data.get(f): return jsonify({'success':False,'error':f'{f} is required'})
    order_num = 'CKC-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:6].upper()
    db = get_db()
    db.execute('''INSERT INTO custom_orders
        (order_number,customer_name,customer_email,customer_phone,candle_shape,candle_size,
         scent,scent_intensity,color,message_on_candle,quantity,notes,budget)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (order_num, data.get('name'), data.get('email'), data.get('phone'),
         data.get('candle_shape'), data.get('candle_size'), data.get('scent'),
         data.get('scent_intensity','medium'), data.get('color'), data.get('message_on_candle'),
         int(data.get('quantity',1)), data.get('notes'), data.get('budget')))
    db.commit()
    site = get_all_content()
    _notify_admin_custom_order(order_num, data, site)
    return jsonify({'success':True,'order_number':order_num,
                    'message':"Custom order received! We'll contact you within 24 hours with a quote."})

def send_custom_order_email(order_num, data, site, email_type='placed'):
    """Send beautiful confirmation email to customer for custom candle orders."""
    customer_email = data.get('email','') or data.get('customer_email','')
    if not customer_email:
        return
    store = site.get('store_name','Candle K')
    url   = site.get('store_url','http://localhost:5000')
    year  = datetime.now().year
    CD='#1E1C19'; CM='#2C2A27'; CI='#F7F5EC'; CS='#E8E2D4'; CC='#FAF8F2'
    CP='#C97878'; CPL='#E8A0A0'; CG='#7FA882'; CD2='#8A8278'; CX='#B8B0A6'

    name  = data.get('name', data.get('customer_name',''))
    phone = data.get('phone', data.get('customer_phone',''))

    if email_type == 'placed':
        subject  = f'✨ Custom Candle Order Received — {order_num}'
        headline = '✨ Custom Order Received!'
        icon     = '🕯️'
        greeting = f'Thank you, {name}!'
        body     = 'Your custom candle order has been received. Our team is reviewing your request and will contact you within 24 hours with a price confirmation and timeline.'
        color    = CP
    elif email_type == 'quoted':
        price = data.get('quoted_price','')
        subject  = f'💰 Your Custom Candle Quote — {order_num}'
        headline = '💰 Your Quote is Ready!'
        icon     = '✨'
        greeting = f'Great news, {name}!'
        body     = f'We have prepared your custom candle quote. The total price for your custom order is <strong>{int(float(price)):,} EGP</strong>. Please contact us to confirm and we will begin crafting your candle.'
        color    = '#C8A45A'
    elif email_type == 'confirmed':
        subject  = f'✅ Custom Order Confirmed — {order_num}'
        headline = '✅ Order Confirmed!'
        icon     = '🎨'
        greeting = f'Wonderful, {name}!'
        body     = 'Your custom candle order has been confirmed and we are now crafting it by hand with care. Custom candles take 5–7 business days.'
        color    = CG
    elif email_type == 'in_production':
        subject  = f'🔥 Your Candle is Being Made — {order_num}'
        headline = '🔥 Being crafted!'
        icon     = '🕯️'
        greeting = f'Exciting news, {name}!'
        body     = 'Your custom candle is currently being handcrafted. We pour love into every custom order.'
        color    = CP
    elif email_type == 'completed':
        subject  = f'🎉 Your Custom Candle is Ready — {order_num}'
        headline = '🎉 Ready for delivery!'
        icon     = '🎉'
        greeting = f'Your candle is ready, {name}! 🕯️'
        body     = 'Your custom candle has been completed and is ready. We will be in touch shortly to arrange delivery.'
        color    = CG
    elif email_type == 'cancelled':
        subject  = f'Custom Order Cancelled — {order_num}'
        headline = 'Order Cancelled'
        icon     = '❌'
        greeting = f'Hi {name},'
        body     = 'Your custom candle order has been cancelled. If you have questions please reply to this email.'
        color    = '#B0776A'
    else:
        return

    # Build details table
    fields = [
        ('Shape',    data.get('candle_shape', data.get('shape',''))),
        ('Size',     data.get('candle_size',  data.get('size',''))),
        ('Scent',    data.get('scent','')),
        ('Intensity',data.get('scent_intensity','medium').title()),
        ('Color',    data.get('color','')),
        ('Quantity', str(data.get('quantity',1))),
    ]
    if data.get('message_on_candle'):
        fields.append(('Engraved Message', data['message_on_candle']))
    if data.get('notes'):
        fields.append(('Notes', data['notes']))

    rows_html = ''.join(
        f'''<tr>
          <td style="padding:10px 0;border-bottom:1px solid {CS};font-size:13px;color:{CD2};width:130px;">{k}</td>
          <td style="padding:10px 0;border-bottom:1px solid {CS};font-size:14px;color:{CM};font-weight:500;">{v}</td>
        </tr>'''
        for k,v in fields if v
    )

    admin_notes = data.get('admin_notes','')
    notes_section = f'''
    <div style="margin:20px 0;padding:16px;background:{CC};border:1px solid {CS};border-radius:8px;">
      <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:{CD2};margin-bottom:8px;">Message from us</div>
      <div style="font-size:14px;color:{CM};line-height:1.7;">{admin_notes}</div>
    </div>''' if admin_notes else ''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>{subject}</title></head>
<body style="margin:0;padding:0;background-color:{CI};font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;">{greeting} {body[:80]}...</div>
<table width="100%" cellpadding="0" cellspacing="0" style="background:{CI};padding:40px 20px;"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  <tr><td style="background:{CD};border-radius:12px 12px 0 0;padding:40px 48px 32px;text-align:center;">
    <div style="font-family:Georgia,serif;font-size:30px;color:{CI};letter-spacing:.25em;text-transform:uppercase;">{store}</div>
    <div style="font-size:12px;color:{CPL};letter-spacing:.1em;font-style:italic;margin-bottom:24px;">Pure light, pure comfort</div>
    <div style="width:36px;height:1px;background:{CP};margin:0 auto 24px;"></div>
    <div style="font-size:34px;margin-bottom:10px;">{icon}</div>
    <div style="font-family:Georgia,serif;font-size:20px;color:{CI};">{headline}</div>
  </td></tr>

  <tr><td style="background:{color};padding:0;height:4px;"></td></tr>

  <tr><td style="background:#fff;padding:40px 48px;">
    <p style="font-family:Georgia,serif;font-size:20px;color:{CM};margin:0 0 12px;">{greeting}</p>
    <p style="font-size:15px;color:{CD2};line-height:1.8;margin:0 0 28px;">{body}</p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;background:{CC};border:1px solid {CS};border-radius:8px;padding:20px;">
      <tr><td colspan="2" style="padding-bottom:12px;">
        <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:{CD2};">Reference Number</div>
        <div style="font-family:Georgia,serif;font-size:20px;color:{CM};margin-top:4px;">{order_num}</div>
      </td></tr>
      {rows_html}
    </table>

    {notes_section}

    <div style="height:1px;background:{CS};margin:24px 0;"></div>

    <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:{CD2};margin-bottom:10px;">Delivery Address</div>
    <div style="font-size:14px;color:{CM};">{phone}</div>
    <div style="font-size:13px;color:{CD2};margin-top:4px;">{customer_email}</div>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:32px;"><tr><td align="center">
      <a href="mailto:{site.get('contact_email','hello@candlek.com')}"
         style="display:inline-block;background:{CM};color:{CI};text-decoration:none;padding:14px 36px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;">
        Contact Us &rarr;
      </a>
    </td></tr></table>
  </td></tr>

  <tr><td style="background:{CD};border-radius:0 0 12px 12px;padding:28px 48px;text-align:center;">
    <div style="font-family:Georgia,serif;font-size:13px;color:rgba(247,245,236,.45);font-style:italic;margin-bottom:12px;">Every flame tells a story.</div>
    <div style="width:28px;height:1px;background:rgba(201,120,120,.35);margin:0 auto 12px;"></div>
    <div style="font-size:12px;color:rgba(247,245,236,.4);line-height:2;">
      {site.get('contact_email','hello@candlek.com')}<br>
      {site.get('contact_phone','')} &nbsp;·&nbsp; {site.get('contact_instagram','')}
    </div>
    <div style="font-size:11px;color:rgba(247,245,236,.18);margin-top:12px;">
      &copy; {year} {store} &nbsp;·&nbsp; Handcrafted with &hearts; in Egypt
    </div>
  </td></tr>
  <tr><td style="height:24px;"></td></tr>
</table></td></tr></table>
</body></html>"""

    def _send():
        host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
        user=site.get('email_user',''); pw=site.get('email_password','')
        enabled=site.get('email_enabled','false').lower()=='true'
        succ=1; err=''
        if enabled and user and pw:
            try:
                msg=MIMEMultipart('alternative')
                msg['Subject']=subject
                msg['From']=f'{store} <{user}>'
                msg['To']=customer_email
                msg.attach(MIMEText(html,'html','utf-8'))
                with smtplib.SMTP(host,port,timeout=15) as s:
                    s.ehlo(); s.starttls(); s.login(user,pw)
                    s.sendmail(user,customer_email,msg.as_string())
            except Exception as e:
                succ=0; err=str(e)
        try:
            db2=sqlite3.connect(DATABASE)
            db2.execute('INSERT INTO email_log (recipient,subject,email_type,success,error_msg) VALUES (?,?,?,?,?)',
                        (customer_email,subject,f'custom_{email_type}',succ,err))
            db2.commit(); db2.close()
        except: pass
    threading.Thread(target=_send,daemon=True).start()


def _notify_admin_custom_order(order_num, data, site):
    admin_email = site.get('email_user','')
    if not admin_email: return
    store = site.get('store_name','Candle K')
    fields = [
        ('Shape',data.get('candle_shape','')),('Size',data.get('candle_size','')),
        ('Scent',data.get('scent','')),('Intensity',data.get('scent_intensity','medium')),
        ('Color',data.get('color','')),('Qty',str(data.get('quantity',1))),
        ('Budget',data.get('budget','')),('Message on candle',data.get('message_on_candle','')),
        ('Customer',data.get('name','')),('Phone',data.get('phone','')),
        ('Email',data.get('email','')),('Notes',data.get('notes','')),
    ]
    rows = ''.join(f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee;color:#7A736A;font-size:13px;width:140px;">{k}</td><td style="padding:8px 12px;border-bottom:1px solid #eee;color:#2C2A27;font-size:13px;">{v}</td></tr>' for k,v in fields if v)
    subj = f'New Custom Candle Request — {order_num}'
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#f5f5f5;font-family:Arial,sans-serif;">
<table width="560" style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
<tr><td style="background:#2C2A27;padding:20px 28px;">
<div style="font-family:Georgia,serif;font-size:18px;color:#F7F5EC;">New Custom Candle Order</div>
<div style="font-size:12px;color:#C97878;margin-top:4px;">{order_num}</div></td></tr>
<tr><td style="padding:24px 28px;">
<table width="100%">{rows}</table>
<div style="margin-top:20px;text-align:center;">
<a href="{site.get('store_url','')}/admin/custom-orders" style="background:#2C2A27;color:#F7F5EC;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:12px;font-weight:700;">View in Admin</a>
</div></td></tr></table></body></html>"""
    def _send():
        host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
        user=site.get('email_user',''); pw=site.get('email_password','')
        if not (site.get('email_enabled','false').lower()=='true' and user and pw): return
        try:
            msg=MIMEMultipart('alternative'); msg['Subject']=subj
            msg['From']=f'{store} Admin <{user}>'; msg['To']=admin_email
            msg.attach(MIMEText(html,'html','utf-8'))
            with smtplib.SMTP(host,port,timeout=15) as s:
                s.ehlo(); s.starttls(); s.login(user,pw); s.sendmail(user,admin_email,msg.as_string())
        except: pass
    threading.Thread(target=_send,daemon=True).start()

# ── Custom order pricing API — reads from custom_options DB ────────────────
@app.route('/api/custom-order/price')
def api_custom_order_price():
    db         = get_db()
    site       = get_all_content()
    shape_name = request.args.get('shape','').strip()
    size_name  = request.args.get('size','').strip()
    scent_name = request.args.get('scent','').strip()
    has_msg    = request.args.get('has_message','false').lower() == 'true'
    qty        = max(1, int(request.args.get('qty', 1)))

    shape_row = db.execute("SELECT price_extra FROM custom_options WHERE option_type='shape' AND name=? AND is_active=1",(shape_name,)).fetchone()
    size_row  = db.execute("SELECT price_extra FROM custom_options WHERE option_type='size'  AND name=? AND is_active=1",(size_name,)).fetchone()
    scent_row = db.execute("SELECT price_extra FROM custom_options WHERE option_type='scent' AND name=? AND is_active=1",(scent_name,)).fetchone()

    shape_price = float(shape_row['price_extra']) if shape_row else 0
    size_price  = float(size_row['price_extra'])  if size_row  else 0
    scent_extra = float(scent_row['price_extra'])  if scent_row else 0
    msg_extra   = float(site.get('custom_price_message_surcharge', 30)) if has_msg else 0
    shipping    = float(site.get('custom_price_shipping', 60))
    unit_price  = shape_price + size_price + scent_extra + msg_extra
    subtotal    = unit_price * qty
    total       = subtotal + shipping

    return jsonify({
        'success':    True,
        'unit_price': unit_price,
        'subtotal':   subtotal,
        'shipping':   shipping,
        'total':      total,
        'qty':        qty,
        'breakdown': {'shape':shape_price,'size':size_price,'scent':scent_extra,'message':msg_extra}
    })

# ── Delivery estimate API ─────────────────────────────
@app.route('/api/delivery-estimate')
def api_delivery_estimate():
    site = get_all_content()
    return jsonify({'success':True,'estimate':calculate_delivery_date(site),
                    'min_days':site.get('delivery_days_min','2'),
                    'max_days':site.get('delivery_days_max','5')})

# ══════════════════════════════════════════════════════
#  ADMIN — Reviews, Wishlist, Custom Orders, Inventory, SMS
# ══════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════
#  ADMIN — Custom Options Manager
# ══════════════════════════════════════════════════════

def get_custom_options_db(option_type=None):
    db = get_db()
    if option_type:
        rows = db.execute('SELECT * FROM custom_options WHERE option_type=? AND is_active=1 ORDER BY sort_order,name',(option_type,)).fetchall()
    else:
        rows = db.execute('SELECT * FROM custom_options ORDER BY option_type,sort_order,name').fetchall()
    return [dict(r) for r in rows]

@app.route('/admin/custom-options', methods=['GET','POST'])
@admin_required
def admin_custom_options():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name  = request.form.get('name','').strip()
            otype = request.form.get('option_type','')
            if name and otype:
                db.execute('INSERT INTO custom_options (option_type,name,icon,description,price_extra,sort_order) VALUES (?,?,?,?,?,?)',
                    (otype,name,request.form.get('icon',''),request.form.get('description',''),
                     float(request.form.get('price_extra',0)),int(request.form.get('sort_order',99))))
                flash(f'{otype.title()} option "{name}" added!','success')
        elif action == 'edit':
            db.execute('UPDATE custom_options SET name=?,icon=?,description=?,price_extra=?,sort_order=? WHERE id=?',
                (request.form.get('name'),request.form.get('icon',''),request.form.get('description',''),
                 float(request.form.get('price_extra',0)),int(request.form.get('sort_order',99)),
                 request.form.get('id')))
            flash('Option updated!','success')
        elif action == 'toggle':
            db.execute('UPDATE custom_options SET is_active=NOT is_active WHERE id=?',(request.form.get('id'),))
        elif action == 'delete':
            db.execute('DELETE FROM custom_options WHERE id=?',(request.form.get('id'),))
            flash('Option deleted.','success')
        elif action == 'update_misc':
            for key in ['custom_price_scent_surcharge','custom_price_message_surcharge','custom_price_shipping']:
                val = request.form.get(key,'')
                if val:
                    db.execute('INSERT OR REPLACE INTO site_content (key,value) VALUES (?,?)',(key,val))
            flash('Pricing saved!','success')
        db.commit()
        return redirect(url_for('admin_custom_options'))
    options = db.execute('SELECT * FROM custom_options ORDER BY option_type,sort_order,name').fetchall()
    site    = get_all_content()
    grouped = {}
    for o in options:
        grouped.setdefault(o['option_type'],[]).append(dict(o))
    return render_template('admin/custom_options.html', grouped=grouped, options=[dict(o) for o in options], site=site)

@app.route('/api/custom-options')
def api_custom_options():
    db     = get_db()
    shapes = db.execute("SELECT * FROM custom_options WHERE option_type='shape' AND is_active=1 ORDER BY sort_order").fetchall()
    sizes  = db.execute("SELECT * FROM custom_options WHERE option_type='size'  AND is_active=1 ORDER BY sort_order").fetchall()
    scents = db.execute("SELECT * FROM custom_options WHERE option_type='scent' AND is_active=1 ORDER BY sort_order").fetchall()
    return jsonify({'success':True,'shapes':[dict(s) for s in shapes],'sizes':[dict(s) for s in sizes],'scents':[dict(s) for s in scents]})

# ══════════════════════════════════════════════════════
#  USER-FACING REVIEWS
# ══════════════════════════════════════════════════════

@app.route('/product/<slug>/reviews')
def product_reviews(slug):
    db      = get_db()
    product = db.execute('SELECT id FROM products WHERE slug=?',(slug,)).fetchone()
    if not product: return jsonify({'reviews':[],'average':0,'count':0})
    reviews = db.execute('SELECT * FROM reviews WHERE product_id=? AND is_approved=1 ORDER BY created_at DESC',(product['id'],)).fetchall()
    avg = round(sum(r['rating'] for r in reviews)/len(reviews),1) if reviews else 0
    return jsonify({'reviews':[dict(r) for r in reviews],'average':avg,'count':len(reviews)})

@app.route('/product/<slug>/review', methods=['POST'])
def submit_product_review(slug):
    db      = get_db()
    product = db.execute('SELECT id,name FROM products WHERE slug=?',(slug,)).fetchone()
    if not product: return jsonify({'success':False,'error':'Product not found'})
    name   = request.form.get('name','').strip()
    body   = request.form.get('body','').strip()
    email  = request.form.get('email','').strip()
    rating = int(request.form.get('rating',5))
    title  = request.form.get('title','').strip()
    if not name or not body:
        return jsonify({'success':False,'error':'Name and review are required.'})
    db.execute('INSERT INTO reviews (product_id,customer_name,customer_email,rating,title,body) VALUES (?,?,?,?,?,?)',
               (product['id'],name,email,rating,title,body))
    db.commit()
    return jsonify({'success':True,'message':'Thank you! Your review will appear after approval.'})


@app.route('/admin/reviews', methods=['GET','POST'])
@admin_required
def admin_reviews():
    db = get_db()
    if request.method == 'POST':
        a=request.form.get('action'); rid=request.form.get('id')
        if a=='approve': db.execute('UPDATE reviews SET is_approved=1 WHERE id=?',(rid,)); flash('Review approved.','success')
        elif a=='reject': db.execute('DELETE FROM reviews WHERE id=?',(rid,)); flash('Review removed.','success')
        db.commit(); return redirect(url_for('admin_reviews'))
    reviews = db.execute('''SELECT r.*,p.name as product_name FROM reviews r
                             LEFT JOIN products p ON p.id=r.product_id
                             ORDER BY r.created_at DESC''').fetchall()
    return render_template('admin/reviews.html', reviews=reviews)

@app.route('/admin/wishlist')
@admin_required
def admin_wishlist():
    wishlist = get_db().execute('''SELECT w.*,p.name as product_name,p.stock FROM wishlist w
                                   LEFT JOIN products p ON p.id=w.product_id
                                   ORDER BY w.created_at DESC''').fetchall()
    return render_template('admin/wishlist.html', wishlist=wishlist)

@app.route('/admin/custom-orders', methods=['GET','POST'])
@admin_required
def admin_custom_orders():
    db = get_db()
    if request.method == 'POST':
        new_status  = request.form.get('status')
        quoted_price= request.form.get('quoted_price','')
        admin_notes = request.form.get('admin_notes','')
        co_id       = request.form.get('id')
        db.execute('UPDATE custom_orders SET status=?,quoted_price=?,admin_notes=? WHERE id=?',
                   (new_status, float(quoted_price) if quoted_price else None, admin_notes, co_id))
        db.commit()
        # Email the customer about the update
        co = db.execute('SELECT * FROM custom_orders WHERE id=?',(co_id,)).fetchone()
        if co:
            site = get_all_content()
            co_data = dict(co)
            co_data['admin_notes'] = admin_notes
            send_custom_order_email(co['order_number'], co_data, site, new_status)
        flash('Custom order updated! Customer notified by email.','success')
        return redirect(url_for('admin_custom_orders'))
    orders = db.execute('SELECT * FROM custom_orders ORDER BY created_at DESC').fetchall()
    return render_template('admin/custom_orders.html', orders=orders)

@app.route('/admin/inventory')
@admin_required
def admin_inventory():
    db = get_db()
    products = db.execute('''SELECT p.*,c.name as category_name FROM products p
                              LEFT JOIN categories c ON c.id=p.category_id
                              WHERE p.is_active=1 ORDER BY p.stock ASC''').fetchall()
    log = db.execute('''SELECT il.*,p.name as product_name FROM inventory_log il
                         LEFT JOIN products p ON p.id=il.product_id
                         ORDER BY il.created_at DESC LIMIT 100''').fetchall()
    return render_template('admin/inventory.html', products=products, log=log)

@app.route('/admin/inventory/adjust', methods=['POST'])
@admin_required
def admin_inventory_adjust():
    db = get_db()
    pid  = request.form.get('product_id')
    new  = int(request.form.get('new_stock',0))
    why  = request.form.get('reason','Manual adjustment')
    old  = db.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()['stock']
    log_inventory_change(db, pid, 'manual_adjust', new-old, why)
    db.execute('UPDATE products SET stock=? WHERE id=?',(new,pid))
    db.commit()
    if old == 0 and new > 0:
        site = get_all_content()
        threading.Thread(target=notify_wishlist_back_in_stock, args=(pid,site), daemon=True).start()
        flash('Stock updated! Wishlist customers will be notified by email.','success')
    else:
        flash(f'Stock updated to {new}.','success')
    return redirect(url_for('admin_inventory'))



# ══════════════════════════════════════════════════════
#  AUTOMATION ENGINE — runs background tasks
# ══════════════════════════════════════════════════════

def run_automation():
    """Background thread that runs automation tasks every hour."""
    import time
    while True:
        try:
            _auto_review_requests()
            _auto_low_stock_alert()
        except Exception as e:
            pass  # never crash the thread
        time.sleep(3600)  # run every hour

def _auto_review_requests():
    """Send review request email 3 days after delivery."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    # Find delivered orders from ~3 days ago that haven't been emailed for review
    orders = db.execute("""
        SELECT o.* FROM orders o
        WHERE o.status = 'delivered'
        AND o.customer_email IS NOT NULL
        AND o.customer_email != ''
        AND datetime(o.created_at, '+3 days') <= datetime('now')
        AND NOT EXISTS (
            SELECT 1 FROM email_log el
            WHERE el.order_id = o.id AND el.email_type = 'review_request'
        )
        LIMIT 10
    """).fetchall()
    for order in orders:
        site = {r['key']:r['value'] for r in db.execute('SELECT key,value FROM site_content').fetchall()}
        items = db.execute('SELECT * FROM order_items WHERE order_id=?',(order['id'],)).fetchall()
        _send_review_request_email(dict(order), [dict(i) for i in items], site)
    db.close()

def _auto_low_stock_alert():
    """Email admin when products hit low stock threshold."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    site = {r['key']:r['value'] for r in db.execute('SELECT key,value FROM site_content').fetchall()}
    thresh = int(site.get('low_stock_threshold', 5))
    admin_email = site.get('email_user','')
    enabled = site.get('email_enabled','false').lower() == 'true'
    if not enabled or not admin_email:
        db.close(); return
    # Only alert once per day per product
    low = db.execute("""
        SELECT * FROM products
        WHERE stock <= ? AND stock > 0 AND is_active = 1
        AND NOT EXISTS (
            SELECT 1 FROM email_log el
            WHERE el.email_type='low_stock_alert'
            AND el.subject LIKE '%' || products.name || '%'
            AND el.sent_at > datetime('now','-24 hours')
        )
    """, (thresh,)).fetchall()
    out = db.execute("SELECT * FROM products WHERE stock=0 AND is_active=1").fetchall()
    db.close()
    if not low and not out: return
    _send_admin_stock_alert(low, out, site, admin_email)

def _send_review_request_email(order, items, site):
    store = site.get('store_name','Candle K')
    url   = site.get('store_url','http://localhost:5000')
    CP='#C97878'; CD='#1E1C19'; CI='#F7F5EC'; CG='#7FA882'
    subject = f"How are your candles? ✨ — {order['order_number']}"
    item_names = ', '.join(i['product_name'] for i in items[:2])
    if len(items) > 2: item_names += f' and {len(items)-2} more'
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:32px 16px;background:{CI};font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="560" style="max-width:560px;width:100%;">
<tr><td style="background:{CD};border-radius:12px 12px 0 0;padding:36px 48px;text-align:center;">
  <div style="font-family:Georgia,serif;font-size:28px;color:{CI};letter-spacing:.2em;text-transform:uppercase;">{store}</div>
  <div style="font-size:12px;color:{CP};margin-top:4px;font-style:italic;">Pure light, pure comfort</div>
</td></tr>
<tr><td style="background:{CP};height:4px;"></td></tr>
<tr><td style="background:#fff;padding:40px 48px;text-align:center;">
  <div style="font-size:48px;margin-bottom:16px;">🕯️</div>
  <h1 style="font-family:Georgia,serif;font-size:24px;color:{CD};margin:0 0 16px;">How are you enjoying your candles?</h1>
  <p style="font-size:15px;color:#7A736A;line-height:1.8;margin:0 0 28px;">
    Hi {order['customer_name']}! Your order ({item_names}) was delivered a few days ago.<br>
    We would love to hear what you think — your feedback means the world to us.
  </p>
  <a href="{url}/shop" style="display:inline-block;background:{CD};color:{CI};text-decoration:none;padding:14px 36px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;margin-bottom:16px;">
    Shop Again →
  </a>
  <p style="font-size:13px;color:#B8B0A6;margin-top:24px;">
    To share feedback, simply reply to this email. We read every message personally.
  </p>
</td></tr>
<tr><td style="background:{CD};border-radius:0 0 12px 12px;padding:24px 48px;text-align:center;">
  <div style="font-size:12px;color:rgba(247,245,236,.4);">© {datetime.now().year} {store} · Made with ♥ in Egypt</div>
</td></tr>
</table></td></tr></table>
</body></html>"""
    def _send():
        host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
        user=site.get('email_user',''); pw=site.get('email_password','')
        if not (site.get('email_enabled','false').lower()=='true' and user and pw): return
        try:
            msg=MIMEMultipart('alternative'); msg['Subject']=subject
            msg['From']=f'{store} <{user}>'; msg['To']=order['customer_email']
            msg.attach(MIMEText(html,'html','utf-8'))
            with smtplib.SMTP(host,port,timeout=15) as s:
                s.ehlo(); s.starttls(); s.login(user,pw)
                s.sendmail(user,order['customer_email'],msg.as_string())
            succ=1; err=''
        except Exception as e: succ=0; err=str(e)
        try:
            db2=sqlite3.connect(DATABASE)
            db2.execute('INSERT INTO email_log (order_id,recipient,subject,email_type,success,error_msg) VALUES (?,?,?,?,?,?)',
                (order['id'],order['customer_email'],subject,'review_request',succ,err))
            db2.commit(); db2.close()
        except: pass
    threading.Thread(target=_send,daemon=True).start()

def _send_admin_stock_alert(low_items, out_items, site, admin_email):
    store = site.get('store_name','Candle K')
    CP='#C97878'; CD='#1E1C19'; CI='#F7F5EC'
    low_rows = ''.join(f'<tr><td style="padding:10px 0;border-bottom:1px solid #eee;font-size:14px;">{p["name"]}</td><td style="padding:10px 0;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#C8A45A;">{p["stock"]} left</td></tr>' for p in low_items)
    out_rows = ''.join(f'<tr><td style="padding:10px 0;border-bottom:1px solid #eee;font-size:14px;">{p["name"]}</td><td style="padding:10px 0;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#c0392b;">OUT OF STOCK</td></tr>' for p in out_items)
    subject = f'⚠️ Stock Alert — {store}'
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:32px 16px;background:#f5f5f5;font-family:Arial,sans-serif;">
<table width="560" style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
<tr><td style="background:{CD};padding:24px 32px;"><div style="font-family:Georgia,serif;font-size:22px;color:{CI};letter-spacing:.15em;">{store} — Stock Alert</div></td></tr>
<tr><td style="padding:28px 32px;">
  <h2 style="font-size:16px;color:{CD};margin:0 0 20px;">⚠️ Products need restocking</h2>
  <table width="100%" cellpadding="0" cellspacing="0">
    {'<tr><td colspan="2" style="padding-bottom:8px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#999;">Low Stock</td></tr>'+low_rows if low_items else ''}
    {'<tr><td colspan="2" style="padding:12px 0 8px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#999;">Out of Stock</td></tr>'+out_rows if out_items else ''}
  </table>
  <div style="margin-top:24px;text-align:center;">
    <a href="{site.get('store_url','')}/admin/products" style="background:{CD};color:{CI};text-decoration:none;padding:12px 28px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;">Update Stock →</a>
  </div>
</td></tr>
</table></body></html>"""
    def _send():
        host=site.get('email_host','smtp.gmail.com'); port=int(site.get('email_port',587))
        user=site.get('email_user',''); pw=site.get('email_password','')
        if not (site.get('email_enabled','false').lower()=='true' and user and pw): return
        try:
            msg=MIMEMultipart('alternative'); msg['Subject']=subject
            msg['From']=f'{store} Admin <{user}>'; msg['To']=admin_email
            msg.attach(MIMEText(html,'html','utf-8'))
            with smtplib.SMTP(host,port,timeout=15) as s:
                s.ehlo(); s.starttls(); s.login(user,pw)
                s.sendmail(user,admin_email,msg.as_string())
            db2=sqlite3.connect(DATABASE)
            db2.execute('INSERT INTO email_log (recipient,subject,email_type,success) VALUES (?,?,?,1)',(admin_email,subject,'low_stock_alert'))
            db2.commit(); db2.close()
        except: pass
    threading.Thread(target=_send,daemon=True).start()

# ── Sitemap.xml ───────────────────────────────────────────────────────────────
@app.route('/sitemap.xml')
def sitemap():
    db  = get_db()
    url = get_all_content().get('store_url','http://localhost:5000').rstrip('/')
    products = db.execute('SELECT slug,created_at FROM products WHERE is_active=1').fetchall()
    cats     = db.execute('SELECT slug FROM categories').fetchall()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in ['/', '/shop', '/track']:
        lines.append(f'  <url><loc>{url}{path}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>')
    for cat in cats:
        lines.append(f'  <url><loc>{url}/shop?category={cat["slug"]}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>')
    for p in products:
        lines.append(f'  <url><loc>{url}/product/{p["slug"]}</loc><lastmod>{str(p["created_at"])[:10]}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>')
    lines.append('</urlset>')
    return Response('\n'.join(lines), mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    url = get_all_content().get('store_url','http://localhost:5000').rstrip('/')
    return Response(f'User-agent: *\nAllow: /\nDisallow: /admin/\nSitemap: {url}/sitemap.xml\n', mimetype='text/plain')

if __name__=='__main__':
    init_db()
    # Start automation background thread
    auto_thread = threading.Thread(target=run_automation, daemon=True)
    auto_thread.start()
    app.run(debug=os.environ.get('FLASK_ENV')=='development', host='127.0.0.1', port=int(os.environ.get('PORT',5000)))
