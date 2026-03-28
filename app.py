# ================================
# Smart Payment Reminder System v4
# All features: QR, UPI, Address, Search, Template Invoice
# ================================
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, session, make_response, jsonify)
from flask_login import (LoginManager, UserMixin, login_user,
                         login_required, logout_user, current_user)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable, Image as RLImage)
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from io import BytesIO
import os, smtplib, urllib.parse, random, re, json, threading, time, qrcode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
from database import get_db, init_db

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

SUPPORT_EMAIL = "sprssuppot@gmail.com"
OTP_EXPIRE_MINUTES = 5
MAX_REMINDERS = 5
UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Gemini Setup ──
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
gemini_available = False

def _get_genai():
    """Safely import google-genai. Tries both known import paths."""
    try:
        import google.genai as genai
        return genai
    except ImportError:
        pass
    try:
        from google import genai
        return genai
    except ImportError:
        pass
    return None

def init_gemini():
    global gemini_available
    if not GEMINI_API_KEY:
        print("[GEMINI] No API key set - using built-in model")
        return
    try:
        genai = _get_genai()
        if genai is None:
            raise ImportError("google-genai package not found. Run: pip install google-genai")
        client = genai.Client(api_key=GEMINI_API_KEY)
        client.models.generate_content(model="gemini-2.0-flash", contents="hi")
        gemini_available = True
        print("[GEMINI] Available")
    except Exception as e:
        gemini_available = False
        print(f"[GEMINI] Not available: {e}")

def gemini_watcher():
    global gemini_available
    while True:
        time.sleep(300)
        if not GEMINI_API_KEY:
            continue
        try:
            genai = _get_genai()
            if genai is None:
                continue
            client = genai.Client(api_key=GEMINI_API_KEY)
            client.models.generate_content(model="gemini-2.0-flash", contents="ping")
            if not gemini_available:
                gemini_available = True
                print("[GEMINI] Switched back to Gemini AI")
        except Exception:
            if gemini_available:
                gemini_available = False
                print("[GEMINI] Quota hit - switched to built-in model")

init_gemini()
threading.Thread(target=gemini_watcher, daemon=True).start()

# ── Google OAuth ──
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

init_db()
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, uid, uname):
        self.id = uid
        self.username = uname

@login_manager.user_loader
def load_user(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return User(u["id"], u["username"]) if u else None

def log_action(uid, action, details=""):
    conn = get_db()
    conn.execute("INSERT INTO audit_logs (user_id,action,details) VALUES (?,?,?)", (uid, action, details))
    conn.commit()
    conn.close()

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def generate_otp():
    return str(random.randint(100000, 999999))

def get_user_info(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return u

def generate_upi_qr(upi_id, amount=None, name="Store"):
    """Generate a QR code image (PNG bytes) for UPI payment."""
    try:
        if amount and float(amount) > 0:
            upi_url = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(name)}&am={amount:.2f}&cu=INR"
        else:
            upi_url = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(name)}&cu=INR"
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=6, border=2)
        qr.add_data(upi_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        print(f"[QR] Error generating QR: {e}")
        return None

def send_email(to, subject, html_body=None, plain_body=None, reply_to=None,
               attachment_bytes=None, attachment_filename=None):
    se = os.getenv("SMTP_EMAIL")
    sp = os.getenv("SMTP_PASSWORD")
    if not se or not sp:
        raise Exception("SMTP not configured")
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = se
    msg["To"] = to
    if reply_to: msg["Reply-To"] = reply_to
    alt = MIMEMultipart("alternative")
    if plain_body: alt.attach(MIMEText(plain_body, "plain"))
    if html_body:  alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)
    if attachment_bytes and attachment_filename:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={attachment_filename}")
        msg.attach(part)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(se, sp)
        s.sendmail(se, to, msg.as_string())

def send_welcome_email(to, username, store_name):
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{{margin:0;padding:0;background:#f0f4ff;font-family:'Segoe UI',Arial,sans-serif;}}
.w{{max-width:600px;margin:30px auto;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 10px 40px rgba(79,70,229,0.15);}}
.h{{background:linear-gradient(135deg,#4f46e5,#2563eb);padding:40px 32px;text-align:center;}}
.lt{{color:white;font-size:18px;font-weight:900;letter-spacing:1px;line-height:72px;width:72px;text-align:center;display:block;}}
.logo{{width:72px;height:72px;background:rgba(255,255,255,0.18);border-radius:18px;margin:0 auto 16px;display:flex;align-items:center;justify-content:center;}}
.h h1{{color:white;margin:0 0 6px;font-size:24px;font-weight:900;}}
.h p{{color:rgba(255,255,255,0.75);margin:0;font-size:13px;}}
.b{{padding:36px 32px;}}
.g{{font-size:20px;font-weight:900;color:#1e293b;margin:0 0 10px;}}
.t{{color:#475569;font-size:14px;line-height:1.7;margin:0 0 18px;}}
.fb{{background:#f8faff;border:1px solid #e0e7ff;border-radius:14px;padding:20px 22px;margin:20px 0;}}
.fi{{display:flex;align-items:flex-start;gap:14px;margin-bottom:16px;}}
.fi:last-child{{margin-bottom:0;}}
.ic{{width:38px;height:38px;background:linear-gradient(135deg,#4f46e5,#2563eb);border-radius:10px;color:white;font-size:17px;text-align:center;line-height:38px;flex-shrink:0;}}
.ft{{color:#334155;font-size:13px;line-height:1.6;}}
.fn{{font-weight:700;color:#1e293b;display:block;margin-bottom:3px;font-size:14px;}}
.fd{{color:#64748b;font-size:12px;}}
.cta{{text-align:center;margin:24px 0;}}
.cta a{{background:linear-gradient(135deg,#4f46e5,#2563eb);color:white;text-decoration:none;padding:13px 32px;border-radius:50px;font-weight:900;font-size:14px;display:inline-block;}}
.div{{height:1px;background:#e2e8f0;margin:24px 0;}}
.fo{{background:#f8faff;padding:20px 32px;text-align:center;border-top:1px solid #e2e8f0;}}
.fo p{{color:#94a3b8;font-size:11px;margin:3px 0;}}
.badge{{display:inline-block;background:#dcfce7;color:#16a34a;font-size:11px;font-weight:700;padding:3px 12px;border-radius:50px;margin-bottom:14px;}}
</style></head><body>
<div class="w">
  <div class="h">
    <div class="logo"><span class="lt">SPRS</span></div>
    <h1>Smart Payment Reminder</h1>
    <p>Intelligent payment management for your business</p>
  </div>
  <div class="b">
    <div class="badge">&#10003; Account Activated</div>
    <p class="g">Hello {username} &#128075;</p>
    <p class="t">Welcome to <strong>Smart Payment Reminder System</strong>! Your account for <strong>{store_name}</strong> has been created and is ready to use.</p>
    <p class="t" style="margin-bottom:6px;">Here is what you can do with SPRS:</p>
    <div class="fb">
      <div class="fi"><div class="ic">&#128101;</div><div class="ft"><span class="fn">Customer Management</span><span class="fd">Add, edit, and manage customers with name, phone, email, address and PIN.</span></div></div>
      <div class="fi"><div class="ic">&#129534;</div><div class="ft"><span class="fn">Transaction Tracking</span><span class="fd">Record every sale. Track total, paid and pending amounts. See status instantly.</span></div></div>
      <div class="fi"><div class="ic">&#128276;</div><div class="ft"><span class="fn">AI Payment Reminders</span><span class="fd">Auto reminders at 3, 7 and 15 days via email. Manual WhatsApp reminders too.</span></div></div>
      <div class="fi"><div class="ic">&#128196;</div><div class="ft"><span class="fn">PDF Invoices with QR</span><span class="fd">Professional invoices with your UPI QR code so customers can scan and pay directly.</span></div></div>
      <div class="fi"><div class="ic">&#128274;</div><div class="ft"><span class="fn">Secure and Private</span><span class="fd">Your data is fully isolated per account. Only you can access your records.</span></div></div>
    </div>
    <div class="cta"><a href="http://localhost:5000">Go to Dashboard &rarr;</a></div>
    <div class="div"></div>
    <p class="t" style="font-size:12px;color:#94a3b8;text-align:center;margin:0;">If you did not create this account, please ignore this email.</p>
  </div>
  <div class="fo">
    <p><strong>Smart Payment Reminder System (SPRS)</strong></p>
    <p>This is an automated message — please do not reply directly.</p>
    <p>Support: {SUPPORT_EMAIL}</p>
  </div>
</div></body></html>"""
    plain = (
        f"Hello {username},\n\nWelcome to Smart Payment Reminder System!\n"
        f"Your account for {store_name} is now active.\n\n"
        f"Features:\n"
        f"  - Add and manage customers (with address + PIN)\n"
        f"  - Track transactions (paid vs pending)\n"
        f"  - Auto email reminders at 3, 7 and 15 days\n"
        f"  - WhatsApp reminders\n"
        f"  - PDF invoices with UPI QR code for direct payment\n\n"
        f"Login at: http://localhost:5000\n\nThanks,\nSPRS Team"
    )
    send_email(to, f"Welcome to Smart Payment Reminder System, {username}", html_body=html, plain_body=plain)

def generate_reminder_builtin(store_name, store_phone, customer_name, purchase_date, total, paid, pending):
    try:
        days = (datetime.now() - datetime.strptime(purchase_date, "%Y-%m-%d")).days
    except:
        days = 0
    deadline = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    coupon = ""
    tone = "POLITE"
    if pending <= 0: tone = "PAID"
    elif pending <= 500: tone = "POLITE"
    elif pending <= 3000: tone = "NORMAL"
    else: tone = "FIRM"
    if days > 10 and pending > 0:
        tone = "URGENT"
        deadline = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    if 0 < pending <= 2000: coupon = "EARLY50"
    contact = f"\n\nFor any queries:\nPhone: {store_phone}\nStore: {store_name}"
    header = f"{store_name}\n\n"
    if tone == "POLITE":
        msg = header + f"Dear {customer_name},\n\nThis is a gentle reminder regarding your pending payment.\n\nPurchase Date : {purchase_date}\nTotal Amount  : Rs. {total}\nPaid Amount   : Rs. {paid}\nPending Amount: Rs. {pending}\n\nKindly clear the payment by {deadline}. Thank you." + contact
    elif tone == "NORMAL":
        msg = header + f"Dear {customer_name},\n\nReminder for your pending payment.\n\nPurchase Date : {purchase_date}\nTotal Amount  : Rs. {total}\nPaid Amount   : Rs. {paid}\nPending Amount: Rs. {pending}\n\nPlease clear by {deadline}." + contact
    elif tone == "FIRM":
        msg = header + f"Dear {customer_name},\n\nYour payment is still pending.\n\nPurchase Date : {purchase_date}\nTotal Amount  : Rs. {total}\nPaid Amount   : Rs. {paid}\nPending Amount: Rs. {pending}\n\nPlease pay by {deadline} to avoid inconvenience." + contact
    elif tone == "URGENT":
        msg = header + f"Dear {customer_name},\n\nYour payment is overdue and requires immediate attention.\n\nPurchase Date : {purchase_date}\nTotal Amount  : Rs. {total}\nPaid Amount   : Rs. {paid}\nPending Amount: Rs. {pending}\n\nPlease pay by {deadline} immediately." + contact
    else:
        msg = header + "Payment already completed. Thank you."
    if coupon: msg += f"\n\nPay early and use coupon: {coupon}"
    # QR note
    msg += f"\n\nNOTE: If you pay through QR code, please contact the store after payment to confirm: {store_phone}"
    return msg, tone, deadline, coupon

def generate_reminder_message(store_name, store_phone, customer_name, purchase_date, total, paid, pending):
    global gemini_available
    if gemini_available and GEMINI_API_KEY:
        try:
            genai = _get_genai()
            if genai is None:
                raise ImportError("google-genai not available")
            try:
                days = (datetime.now() - datetime.strptime(purchase_date, "%Y-%m-%d")).days
            except:
                days = 0
            prompt = (
                f"Generate a professional payment reminder message (under 130 words, no emojis, no hashtags).\n"
                f"Business: {store_name}\nPhone: {store_phone}\nCustomer: {customer_name}\n"
                f"Purchase Date: {purchase_date}\nTotal: Rs.{total}\nPaid: Rs.{paid}\nPending: Rs.{pending}\n"
                f"Days since purchase: {days}\n"
                f"End with store contact info.\n"
                f"Also add a note at the end: 'NOTE: If you pay through QR code, please contact the store after payment to confirm.'"
            )
            client = genai.Client(api_key=GEMINI_API_KEY)
            r = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            msg = r.text.strip()
            try:
                days = (datetime.now() - datetime.strptime(purchase_date, "%Y-%m-%d")).days
            except:
                days = 0
            deadline = (datetime.now() + timedelta(days=1 if days > 10 else 3)).strftime("%Y-%m-%d")
            coupon = "EARLY50" if 0 < pending <= 2000 else ""
            return msg, "GEMINI-AI", deadline, coupon
        except Exception as e:
            gemini_available = False
            print(f"[GEMINI] Fallback to built-in: {e}")
    msg, tone, deadline, coupon = generate_reminder_builtin(store_name, store_phone, customer_name, purchase_date, total, paid, pending)
    return msg, tone, deadline, coupon

def generate_invoice_pdf(store_name, store_phone, customer_name, customer_phone,
                          customer_email, customer_address, customer_pin,
                          purchase_date, total_amount, paid_amount,
                          pending_amount, transaction_id, upi_id=None):
    """
    Generate invoice PDF matching the template style:
    - Title: Smart Payment Reminder System (blue, centered, underlined)
    - Table: Company | Invoice Details
    - Bill To section
    - Amount table (3 cols)
    - Payment Status bar
    - Footer with contact + QR code
    - NO background colors except the status bar
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=1.5*cm, leftMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    el = []

    # Colors from the template
    TITLE_BLUE  = colors.HexColor("#31849B")   # title color from docx
    HEADER_BLUE = colors.HexColor("#4472C4")   # table header blue
    BLACK       = colors.HexColor("#000000")
    DARK_GRAY   = colors.HexColor("#404040")
    RED_COLOR   = colors.HexColor("#C00000")   # footer color from docx
    WHITE       = colors.white
    LIGHT_GRAY  = colors.HexColor("#F2F2F2")
    GREEN_COLOR = colors.HexColor("#16a34a")
    STATUS_RED  = colors.HexColor("#C00000")
    STATUS_GREEN= colors.HexColor("#16a34a")

    is_paid = pending_amount <= 0
    sc = STATUS_GREEN if is_paid else STATUS_RED
    st = "PAID" if is_paid else "PENDING"
    inv_no   = f"SPRS-{datetime.now().year}-{transaction_id:04d}"
    inv_date = datetime.now().strftime("%d-%m-%Y")
    due_date = (datetime.now() + timedelta(days=3)).strftime("%d-%m-%Y")

    # ── TITLE ──
    title_style = ParagraphStyle("InvTitle", parent=styles["Normal"],
                                  fontSize=16, fontName="Helvetica-Bold",
                                  alignment=TA_CENTER, textColor=TITLE_BLUE,
                                  spaceAfter=4,
                                  underlineWidth=1, underlineColor=TITLE_BLUE)
    el.append(Paragraph("<u>Smart Payment Reminder System</u>", title_style))
    el.append(HRFlowable(width="100%", thickness=2, color=TITLE_BLUE, spaceAfter=8))

    # ── HEADER TABLE: Company | Invoice Details ──
    bdr = {"style": "LINEBELOW", "color": colors.HexColor("#CCCCCC")}
    header_data = [[
        Paragraph(f'<b>{store_name}</b><br/><font size="9" color="#555555">Phone: {store_phone}</font>',
                  ParagraphStyle("THL", parent=styles["Normal"], fontSize=10, leading=14)),
        Paragraph(
            f'<b>Invoice No:</b> <font color="#4472C4"><b>{inv_no}</b></font><br/>'
            f'<font size="9">Date: {inv_date}<br/>Due Date: {due_date}</font>',
            ParagraphStyle("THR", parent=styles["Normal"], fontSize=10, alignment=TA_RIGHT, leading=14))
    ]]
    ht = Table(header_data, colWidths=[9*cm, 8*cm])
    ht.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), LIGHT_GRAY),
        ("TOPPADDING", (0,0), (-1,-1), 8),("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 10),("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEAFTER", (0,0), (0,-1), 0.5, colors.HexColor("#CCCCCC")),
    ]))
    el.append(ht)
    el.append(Spacer(1, 0.3*cm))

    # ── BILL TO ──
    el.append(Paragraph("<b>Bill To:</b>",
        ParagraphStyle("BT", parent=styles["Normal"], fontSize=11, spaceAfter=4)))

    bill_rows = [
        ["Customer Name", customer_name or "-"],
        ["Email", customer_email or "-"],
        ["Phone", customer_phone or "-"],
    ]
    if customer_address:
        bill_rows.append(["Address", customer_address + (f" - {customer_pin}" if customer_pin else "")])
    bill_rows.append(["Purchase Date", purchase_date])

    bt = Table(bill_rows, colWidths=[4*cm, 13*cm])
    bt.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("TEXTCOLOR", (0,0), (0,-1), DARK_GRAY),
        ("FONTNAME", (1,0), (1,-1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, LIGHT_GRAY]),
        ("TOPPADDING", (0,0), (-1,-1), 6),("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEBELOW", (0,0), (-1,-2), 0.3, colors.HexColor("#DDDDDD")),
    ]))
    el.append(bt)
    el.append(Spacer(1, 0.3*cm))

    # ── AMOUNT TABLE ──
    amt_header = [[
        Paragraph("<font color='white'><b>Total Amount (&#8377;)</b></font>", ParagraphStyle("AH", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER)),
        Paragraph("<font color='white'><b>Paid Amount (&#8377;)</b></font>", ParagraphStyle("AH2", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER)),
        Paragraph("<font color='white'><b>Pending (&#8377;)</b></font>", ParagraphStyle("AH3", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER)),
    ]]
    amt_vals = [[
        Paragraph(f"<b>Rs. {total_amount:,.2f}</b>", ParagraphStyle("AV", parent=styles["Normal"], fontSize=12, alignment=TA_CENTER)),
        Paragraph(f'<font color="#16a34a"><b>Rs. {paid_amount:,.2f}</b></font>', ParagraphStyle("AV2", parent=styles["Normal"], fontSize=12, alignment=TA_CENTER)),
        Paragraph(f'<font color="{("#C00000" if not is_paid else "#16a34a")}"><b>Rs. {pending_amount:,.2f}</b></font>', ParagraphStyle("AV3", parent=styles["Normal"], fontSize=12, alignment=TA_CENTER)),
    ]]
    at = Table(amt_header + amt_vals, colWidths=[5.67*cm, 5.67*cm, 5.66*cm])
    at.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), HEADER_BLUE),
        ("BACKGROUND", (0,1), (-1,1), LIGHT_GRAY),
        ("TOPPADDING", (0,0), (-1,-1), 10),("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEAFTER", (0,0), (1,-1), 0.5, colors.HexColor("#CCCCCC")),
    ]))
    el.append(at)
    el.append(Spacer(1, 0.3*cm))

    # ── PAYMENT STATUS BAR ──
    sb = Table([[Paragraph(f'<font color="white"><b>Payment Status: {st}</b></font>',
        ParagraphStyle("SB", parent=styles["Normal"], fontSize=12, alignment=TA_CENTER))]], colWidths=[17*cm])
    sb.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), sc),
        ("TOPPADDING", (0,0), (-1,-1), 10),("BOTTOMPADDING", (0,0), (-1,-1), 10),
    ]))
    el.append(sb)
    el.append(Spacer(1, 0.4*cm))
    el.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")))
    el.append(Spacer(1, 0.2*cm))

    # ── FOOTER: Contact + QR ──
    contact_text = (
        f'<font color="#C00000">For Any Queries Contact: <b>{store_name}</b></font><br/>'
        f'<font color="#C00000">Phone No: <b>{store_phone}</b></font>'
    )

    if upi_id:
        # Generate QR for pending amount
        qr_bytes = generate_upi_qr(upi_id, pending_amount if not is_paid else 0, store_name)
        if qr_bytes:
            qr_img = RLImage(BytesIO(qr_bytes), width=3*cm, height=3*cm)
            qr_note = Paragraph(
                f'<font size="8" color="#404040">Scan to Pay (UPI)<br/>{upi_id}<br/>'
                f'<b>Amount: Rs. {pending_amount:,.2f}</b><br/>'
                f'(You can change amount before paying)</font>',
                ParagraphStyle("QN", parent=styles["Normal"], fontSize=8, alignment=TA_CENTER)
            )
            footer_data = [[
                Paragraph(contact_text, ParagraphStyle("FC", parent=styles["Normal"], fontSize=10, leading=16)),
                [qr_img, qr_note]
            ]]
            ft = Table(footer_data, colWidths=[11*cm, 6*cm])
            ft.setStyle(TableStyle([
                ("VALIGN", (0,0), (-1,-1), "TOP"),
                ("LEFTPADDING", (0,0), (-1,-1), 0),
                ("RIGHTPADDING", (0,0), (-1,-1), 0),
            ]))
            el.append(ft)
        else:
            el.append(Paragraph(contact_text, ParagraphStyle("FC", parent=styles["Normal"], fontSize=10, leading=16)))
    else:
        el.append(Paragraph(contact_text, ParagraphStyle("FC", parent=styles["Normal"], fontSize=10, leading=16)))

    el.append(Spacer(1, 0.3*cm))

    # ── QR NOTE ──
    if upi_id and not is_paid:
        el.append(Paragraph(
            '<font size="9" color="#404040"><b>NOTE:</b> If you pay through QR code, '
            'please contact the store after payment to confirm. Thank you!</font>',
            ParagraphStyle("QN2", parent=styles["Normal"], fontSize=9, spaceAfter=4)
        ))

    # ── BOTTOM META ──
    el.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")))
    el.append(Spacer(1, 0.1*cm))
    el.append(Paragraph(
        f'<font size="8" color="#888888">Invoice No: {inv_no} | Generated: {datetime.now().strftime("%d %b %Y %H:%M")} | Tx: #{transaction_id:05d} | SPRS</font>',
        ParagraphStyle("META", parent=styles["Normal"], alignment=TA_CENTER, fontSize=8)))

    doc.build(el)
    return buffer.getvalue()

def get_payment_insights(uid):
    conn = get_db()
    rows = conn.execute("""SELECT c.id,c.name,COUNT(t.id) as tc,
        SUM(CASE WHEN t.status='PENDING' THEN 1 ELSE 0 END) as pc,
        AVG(t.pending_amount) as ap,SUM(t.total_amount) as tb
        FROM customers c LEFT JOIN transactions t ON c.id=t.customer_id AND t.user_id=?
        WHERE c.user_id=? GROUP BY c.id""", (uid, uid)).fetchall()
    conn.close()
    ins = []
    for c in rows:
        if not c["tc"]: continue
        pr = (c["pc"] or 0) / c["tc"]
        ap = c["ap"] or 0
        score = max(0, int(100 - (pr*60) - min(40, ap/100)))
        tag = ("Excellent" if score >= 80 else "Good" if score >= 60 else "Average" if score >= 40 else "Poor" if score >= 20 else "Critical")
        ins.append({"name": c["name"], "tag": tag, "score": score, "avg_pending": round(ap,2), "tx_count": c["tc"], "total_biz": round(c["tb"] or 0, 2)})
    ins.sort(key=lambda x: x["score"])
    return ins

def get_monthly_chart_data(uid):
    conn = get_db()
    rows = conn.execute("""SELECT strftime('%Y-%m',purchase_date) as month,
        SUM(paid_amount) as paid,SUM(pending_amount) as pending
        FROM transactions WHERE user_id=? GROUP BY month ORDER BY month DESC LIMIT 6""", (uid,)).fetchall()
    conn.close()
    rows = list(reversed(rows))
    return {"labels": [r["month"] for r in rows],
            "paid": [round(r["paid"] or 0, 2) for r in rows],
            "pending": [round(r["pending"] or 0, 2) for r in rows]}

def auto_send_reminders():
    print(f"[{datetime.now()}] Auto reminder scheduler running...")
    conn = get_db()
    txs = conn.execute("""SELECT t.*,c.name as customer_name,c.email as customer_email,
        c.phone as customer_phone,c.address as customer_address,c.pin as customer_pin,
        u.store_name,u.phone as store_phone,u.upi_id
        FROM transactions t JOIN customers c ON t.customer_id=c.id JOIN users u ON t.user_id=u.id
        WHERE t.status='PENDING' AND c.email IS NOT NULL AND c.email!=''""").fetchall()
    for t_row in txs:
        try:
            t = dict(t_row)
            days = (datetime.now() - datetime.strptime(t["purchase_date"], "%Y-%m-%d")).days
            sn = t["store_name"] or "Smart Payment Store"
            sp = t["store_phone"] or "-"
            msg, tone, deadline, coupon = generate_reminder_message(sn, sp, t["customer_name"],
                t["purchase_date"], t["total_amount"], t["paid_amount"], t["pending_amount"])
            flag = None
            if days >= 15 and not t["reminder_sent_15"]: flag = "15"
            elif days >= 7 and not t["reminder_sent_7"]: flag = "7"
            elif days >= 3 and not t["reminder_sent_3"]: flag = "3"
            if flag:
                pdf = generate_invoice_pdf(sn, sp, t.get("customer_name"), t.get("customer_phone"),
                    t.get("customer_email"), t.get("customer_address"), t.get("customer_pin"),
                    t.get("purchase_date"), t.get("total_amount"), t.get("paid_amount"),
                    t.get("pending_amount"), t["id"], t.get("upi_id"))
                send_email(t["customer_email"], f"{sn} - Payment Reminder",
                    plain_body=msg, attachment_bytes=pdf, attachment_filename="Invoice.pdf")
                conn.execute(f"UPDATE transactions SET reminder_sent_{flag}=1 WHERE id=?", (t["id"],))
                conn.commit()
                print(f"  Sent auto reminder tx={t['id']} day={flag}")
        except Exception as e:
            print(f"  Failed tx={t_row['id']}: {e}")
    conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(auto_send_reminders, "interval", hours=24, next_run_time=datetime.now() + timedelta(seconds=15))
scheduler.start()

# ═══════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════

@app.route("/")
def home():
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))

@app.route("/login/google")
def google_login():
    return google.authorize_redirect(url_for("google_callback", _external=True))

@app.route("/login/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()
        ui = token.get("userinfo")
        if not ui:
            flash("Google login failed.", "danger"); return redirect(url_for("login"))
        ge = ui.get("email", "").lower()
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE email=?", (ge,)).fetchone()
        conn.close()
        if u:
            login_user(User(u["id"], u["username"]))
            log_action(u["id"], "GOOGLE_LOGIN", f"Google: {ge}")
            flash("Logged in with Google.", "success")
            return redirect(url_for("dashboard"))
        flash(f"No account for {ge}. Please register first.", "danger")
        return redirect(url_for("register"))
    except Exception:
        flash("Google login error.", "danger"); return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for("dashboard"))
    if request.method == "POST":
        username   = request.form.get("username", "").strip()
        email      = request.form.get("email", "").strip().lower()
        phone      = request.form.get("phone", "").strip()
        password   = request.form.get("password", "").strip()
        store_name = request.form.get("store_name", "").strip() or "Smart Payment Store"
        upi_id     = request.form.get("upi_id", "").strip()
        if len(username) < 3: flash("Username min 3 chars", "danger"); return redirect(url_for("register"))
        if not email or "@" not in email: flash("Valid email required", "danger"); return redirect(url_for("register"))
        if not phone or len(phone) < 10: flash("Valid phone required", "danger"); return redirect(url_for("register"))
        if len(password) < 4: flash("Password too short", "danger"); return redirect(url_for("register"))
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username,password_hash,email,phone,store_name,upi_id) VALUES (?,?,?,?,?,?)",
                (username, generate_password_hash(password), email, phone, store_name, upi_id or None))
            conn.commit(); conn.close()
            try: send_welcome_email(email, username, store_name)
            except: pass
            flash("Account created. Please login.", "success")
            return redirect(url_for("login"))
        except Exception:
            conn.close(); flash("Username/Email/Phone already exists.", "danger"); return redirect(url_for("register"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("dashboard"))
    if request.method == "POST":
        ident = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "").strip()
        conn  = get_db()
        u = conn.execute("SELECT * FROM users WHERE email=? OR username=?", (ident, ident)).fetchone()
        conn.close()
        if u and check_password_hash(u["password_hash"], pw):
            login_user(User(u["id"], u["username"]))
            log_action(u["id"], "LOGIN", f"Login: {u['email']}")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password", "danger")
    return render_template("login.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email: flash("Email required", "danger"); return redirect(url_for("forgot_password"))
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u: conn.close(); flash("No account found", "danger"); return redirect(url_for("forgot_password"))
        otp = generate_otp()
        exp = (datetime.now() + timedelta(minutes=OTP_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE users SET reset_otp=?,reset_otp_expiry=? WHERE id=?", (otp, exp, u["id"]))
        conn.commit(); conn.close()
        session["reset_user_id"] = u["id"]
        try:
            send_email(u["email"], f"Password Reset OTP - {u['store_name'] or 'SPRS'}",
                plain_body=f"Hello {u['username']},\n\nYour OTP: {otp}\nValid for {OTP_EXPIRE_MINUTES} minutes.")
        except: flash("OTP sending failed.", "danger"); return redirect(url_for("forgot_password"))
        flash("OTP sent. Check your email.", "success")
        return redirect(url_for("verify_otp"))
    return render_template("forgot_password.html", prefill=request.args.get("email", ""))

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    uid = session.get("reset_user_id")
    if not uid: flash("Session expired.", "danger"); return redirect(url_for("forgot_password"))
    if request.method == "POST":
        entered = request.form.get("otp", "").strip()
        conn = get_db(); u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); conn.close()
        if not u or not u["reset_otp"]: flash("OTP not found.", "danger"); return redirect(url_for("forgot_password"))
        if datetime.now() > datetime.strptime(u["reset_otp_expiry"], "%Y-%m-%d %H:%M:%S"): flash("OTP expired.", "danger"); return redirect(url_for("forgot_password"))
        if entered != u["reset_otp"]: flash("Invalid OTP", "danger"); return redirect(url_for("verify_otp"))
        session["otp_verified"] = True
        return redirect(url_for("reset_password"))
    return render_template("verify_otp.html")

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if not session.get("otp_verified"): return redirect(url_for("forgot_password"))
    uid = session.get("reset_user_id")
    if request.method == "POST":
        pw = request.form.get("password", "").strip(); cpw = request.form.get("confirm_password", "").strip()
        if len(pw) < 4: flash("Password too short", "danger"); return redirect(url_for("reset_password"))
        if pw != cpw: flash("Passwords do not match", "danger"); return redirect(url_for("reset_password"))
        conn = get_db(); conn.execute("UPDATE users SET password_hash=?,reset_otp=NULL,reset_otp_expiry=NULL WHERE id=?", (generate_password_hash(pw), uid)); conn.commit(); conn.close()
        session.pop("reset_user_id", None); session.pop("otp_verified", None)
        flash("Password updated. Please login.", "success"); return redirect(url_for("login"))
    return render_template("reset_password.html")

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db(); u = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
    tc = conn.execute("SELECT COUNT(*) as c FROM customers WHERE user_id=?", (current_user.id,)).fetchone()["c"]
    tt = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE user_id=?", (current_user.id,)).fetchone()["c"]
    tp = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE status='PENDING' AND user_id=?", (current_user.id,)).fetchone()["c"]
    ps = conn.execute("SELECT COALESCE(SUM(pending_amount),0) as s FROM transactions WHERE status='PENDING' AND user_id=?", (current_user.id,)).fetchone()["s"]
    tr = conn.execute("SELECT COUNT(*) as c FROM reminders WHERE user_id=?", (current_user.id,)).fetchone()["c"]
    conn.close()
    return render_template("dashboard.html", username=u["username"], profile_pic=u["profile_pic"],
        total_customers=tc, total_transactions=tt, total_pending_tx=tp, pending_sum=ps, total_reminders=tr,
        insights=get_payment_insights(current_user.id), chart_data=json.dumps(get_monthly_chart_data(current_user.id)),
        gemini_status="Gemini AI" if gemini_available else "Built-in Model")

@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", u=get_user_info(current_user.id))

@app.route("/profile/upload-pic", methods=["POST"])
@login_required
def upload_profile_pic():
    if "profile_pic" not in request.files: flash("No file", "danger"); return redirect(url_for("profile"))
    f = request.files["profile_pic"]
    if f.filename == "" or not allowed_file(f.filename): flash("Invalid file type", "danger"); return redirect(url_for("profile"))
    fn = secure_filename(f"user_{current_user.id}_{f.filename}")
    f.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
    conn = get_db(); conn.execute("UPDATE users SET profile_pic=? WHERE id=?", (fn, current_user.id)); conn.commit(); conn.close()
    flash("Profile picture updated.", "success"); return redirect(url_for("profile"))

@app.route("/profile/request-otp", methods=["POST"])
@login_required
def profile_request_otp():
    action = request.form.get("action", "update"); u = get_user_info(current_user.id)
    otp = generate_otp(); exp = (datetime.now() + timedelta(minutes=OTP_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db(); conn.execute("UPDATE users SET profile_otp=?,profile_otp_expiry=?,profile_otp_action=? WHERE id=?", (otp, exp, action, current_user.id)); conn.commit(); conn.close()
    session["profile_action"] = action
    try:
        if action == "change_password":
            subj = f"WARNING: Password Change - {u['store_name'] or 'SPRS'}"
            plain = f"Hello {u['username']},\n\nWARNING: Someone is attempting to change your password.\nIf this was you, OTP: {otp}\nValid for {OTP_EXPIRE_MINUTES} minutes.\n\nIf NOT you, contact: {SUPPORT_EMAIL}"
        else:
            subj = f"Profile Update OTP - {u['store_name'] or 'SPRS'}"
            plain = f"Hello {u['username']},\n\nOTP to update profile: {otp}\nValid for {OTP_EXPIRE_MINUTES} minutes."
        send_email(u["email"], subj, plain_body=plain)
        flash(f"OTP sent to {u['email']}.", "success")
    except: flash("OTP sending failed.", "danger"); return redirect(url_for("profile"))
    return redirect(url_for("profile_verify_otp"))

@app.route("/profile/verify-otp", methods=["GET", "POST"])
@login_required
def profile_verify_otp():
    action = session.get("profile_action", "update")
    if request.method == "POST":
        entered = request.form.get("otp", "").strip(); u = get_user_info(current_user.id)
        if not u["profile_otp"]: flash("OTP not found.", "danger"); return redirect(url_for("profile"))
        if datetime.now() > datetime.strptime(u["profile_otp_expiry"], "%Y-%m-%d %H:%M:%S"): flash("OTP expired.", "danger"); return redirect(url_for("profile"))
        if entered != u["profile_otp"]: flash("Invalid OTP", "danger"); return redirect(url_for("profile_verify_otp"))
        session["profile_otp_verified"] = True
        return redirect(url_for("profile_change_password") if action == "change_password" else url_for("profile_edit"))
    return render_template("verify_profile_otp.html", action=action)

@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    if not session.get("profile_otp_verified"): flash("OTP required", "danger"); return redirect(url_for("profile"))
    u = get_user_info(current_user.id)
    if request.method == "POST":
        ne = request.form.get("email", "").strip().lower()
        np = request.form.get("phone", "").strip()
        ns = request.form.get("store_name", "").strip()
        nupi = request.form.get("upi_id", "").strip()
        if not ne or "@" not in ne: flash("Valid email required", "danger"); return redirect(url_for("profile_edit"))
        if not np or len(np) < 10: flash("Valid phone required", "danger"); return redirect(url_for("profile_edit"))
        conn = get_db()
        try:
            conn.execute("UPDATE users SET email=?,phone=?,store_name=?,upi_id=? WHERE id=?", (ne, np, ns, nupi or None, current_user.id))
            conn.commit(); conn.close(); session.pop("profile_otp_verified", None)
            log_action(current_user.id, "PROFILE_UPDATED", "Profile updated"); flash("Profile updated.", "success")
        except: conn.close(); flash("Email/Phone already in use.", "danger")
        return redirect(url_for("profile"))
    return render_template("profile_edit.html", u=u)

@app.route("/profile/change-password", methods=["GET", "POST"])
@login_required
def profile_change_password():
    if not session.get("profile_otp_verified"): flash("OTP required", "danger"); return redirect(url_for("profile"))
    if request.method == "POST":
        pw = request.form.get("password", "").strip(); cpw = request.form.get("confirm_password", "").strip()
        if len(pw) < 4: flash("Too short", "danger"); return redirect(url_for("profile_change_password"))
        if pw != cpw: flash("Do not match", "danger"); return redirect(url_for("profile_change_password"))
        conn = get_db(); conn.execute("UPDATE users SET password_hash=?,profile_otp=NULL WHERE id=?", (generate_password_hash(pw), current_user.id)); conn.commit(); conn.close()
        session.pop("profile_otp_verified", None); session.pop("profile_action", None)
        log_action(current_user.id, "PASSWORD_CHANGED", "Changed from profile"); flash("Password changed.", "success")
        return redirect(url_for("profile"))
    return render_template("profile_change_password.html")

@app.route("/help", methods=["GET", "POST"])
@login_required
def contact():
    u = get_user_info(current_user.id); sn = u["store_name"] or "Smart Payment Store"
    if request.method == "POST":
        subj = request.form.get("subject", "").strip(); msg = request.form.get("message", "").strip()
        if not subj or not msg: flash("Subject and message required", "danger"); return redirect(url_for("contact"))
        try:
            send_email(SUPPORT_EMAIL, f"[HELP] {sn} - {subj}", plain_body=f"Store: {sn}\nEmail: {u['email']}\nPhone: {u['phone']}\n\n{msg}", reply_to=u["email"])
            log_action(current_user.id, "HELP_REQUEST", f"Subject: {subj}"); flash("Message sent to support.", "success")
        except: flash("Message failed. Check SMTP.", "danger")
        return redirect(url_for("contact"))
    return render_template("contact.html", store_name=sn, support_email=SUPPORT_EMAIL, username=u["username"], user_email=u["email"])

@app.route("/audit-logs")
@login_required
def audit_logs():
    conn = get_db(); logs = conn.execute("SELECT * FROM audit_logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (current_user.id,)).fetchall(); conn.close()
    return render_template("audit_logs.html", logs=logs)

# ── CUSTOMERS ──────────────────────────────────────────────────────────

@app.route("/customers")
@login_required
def customers():
    q = request.args.get("q", "").strip(); conn = get_db()
    if q:
        custs = conn.execute("SELECT * FROM customers WHERE user_id=? AND (name LIKE ? OR phone LIKE ? OR email LIKE ?) ORDER BY id DESC",
            (current_user.id, f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    else:
        custs = conn.execute("SELECT * FROM customers WHERE user_id=? ORDER BY id DESC", (current_user.id,)).fetchall()
    conn.close()
    return render_template("customers.html", customers=custs, q=q)

@app.route("/customers/add", methods=["GET", "POST"])
@login_required
def add_customer():
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        phone   = request.form.get("phone", "").strip()
        email   = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        pin     = request.form.get("pin", "").strip()
        if phone and (not phone.isdigit() or len(phone) != 10):
            flash("Phone number must be exactly 10 digits.", "danger"); return redirect(url_for("add_customer"))
        conn = get_db()
        try:
            conn.execute("INSERT INTO customers (name,phone,email,address,pin,preferred_payment_method,user_id) VALUES (?,?,?,?,?,?,?)",
                (name, phone, email, address, pin, "", current_user.id))
            conn.commit()
        except:
            flash("Error adding customer", "danger")
        finally:
            conn.close()
        log_action(current_user.id, "ADD_CUSTOMER", f"Added: {name}")
        flash("Customer added successfully.", "success")
        return redirect(url_for("customers"))
    return render_template("add_customer.html")

@app.route("/customers/delete/<int:cid>")
@login_required
def delete_customer(cid):
    conn = get_db()
    conn.execute("DELETE FROM customers WHERE id=? AND user_id=?", (cid, current_user.id))
    conn.commit(); conn.close()
    flash("Customer deleted.", "success")
    return redirect(url_for("customers"))

@app.route("/customers/edit/<int:cid>", methods=["GET", "POST"])
@login_required
def edit_customer(cid):
    conn = get_db()
    c = conn.execute("SELECT * FROM customers WHERE id=? AND user_id=?", (cid, current_user.id)).fetchone()
    if not c: conn.close(); flash("Customer not found.", "danger"); return redirect(url_for("customers"))
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        phone   = request.form.get("phone", "").strip()
        email   = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        pin     = request.form.get("pin", "").strip()
        if not name: flash("Name is required.", "danger"); conn.close(); return redirect(url_for("edit_customer", cid=cid))
        if phone and (not phone.isdigit() or len(phone) != 10):
            flash("Phone number must be exactly 10 digits.", "danger"); conn.close(); return redirect(url_for("edit_customer", cid=cid))
        conn.execute("UPDATE customers SET name=?,phone=?,email=?,address=?,pin=? WHERE id=? AND user_id=?",
            (name, phone, email, address, pin, cid, current_user.id))
        conn.commit(); conn.close()
        log_action(current_user.id, "EDIT_CUSTOMER", f"Edited: {name}")
        flash("Customer updated successfully.", "success")
        return redirect(url_for("customers"))
    conn.close()
    return render_template("edit_customer.html", c=c)

# ── CUSTOMER SEARCH API (for add_transaction dropdown) ──

@app.route("/api/customers/search")
@login_required
def api_customer_search():
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        custs = conn.execute(
            "SELECT id,name,phone,email FROM customers WHERE user_id=? AND (name LIKE ? OR phone LIKE ?) ORDER BY name ASC LIMIT 20",
            (current_user.id, f"%{q}%", f"%{q}%")).fetchall()
    else:
        custs = conn.execute("SELECT id,name,phone,email FROM customers WHERE user_id=? ORDER BY name ASC LIMIT 20", (current_user.id,)).fetchall()
    conn.close()
    return jsonify([dict(c) for c in custs])

# ── TRANSACTIONS ───────────────────────────────────────────────────────

@app.route("/transactions")
@login_required
def transactions():
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        txs = conn.execute(
            "SELECT t.*,c.name as customer_name FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.user_id=? AND c.name LIKE ? ORDER BY t.id DESC",
            (current_user.id, f"%{q}%")).fetchall()
    else:
        txs = conn.execute("SELECT t.*,c.name as customer_name FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.user_id=? ORDER BY t.id DESC", (current_user.id,)).fetchall()
    conn.close()
    return render_template("transactions.html", transactions=txs, q=q)

@app.route("/transactions/add", methods=["GET", "POST"])
@login_required
def add_transaction():
    conn = get_db(); custs = conn.execute("SELECT * FROM customers WHERE user_id=? ORDER BY name ASC", (current_user.id,)).fetchall()
    if request.method == "POST":
        cid   = request.form.get("customer_id"); pd = request.form.get("purchase_date")
        total = float(request.form.get("total_amount", 0)); paid = float(request.form.get("paid_amount", 0))
        pending = total - paid; status = "PAID" if pending <= 0 else "PENDING"
        if pending < 0: pending = 0
        conn.execute("INSERT INTO transactions (customer_id,purchase_date,total_amount,paid_amount,pending_amount,status,user_id) VALUES (?,?,?,?,?,?,?)",
            (cid, pd, total, paid, pending, status, current_user.id))
        conn.commit(); conn.close()
        log_action(current_user.id, "ADD_TRANSACTION", f"Added tx cid={cid}"); flash("Transaction added.", "success")
        return redirect(url_for("transactions"))
    conn.close()
    return render_template("add_transaction.html", customers=custs)

@app.route("/transactions/update/<int:tid>", methods=["GET", "POST"])
@login_required
def update_payment(tid):
    conn = get_db()
    t = conn.execute("SELECT t.*,c.name as customer_name FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.id=? AND t.user_id=?", (tid, current_user.id)).fetchone()
    if not t: conn.close(); flash("Transaction not found", "danger"); return redirect(url_for("transactions"))
    if request.method == "POST":
        additional = float(request.form.get("additional_payment", 0))
        total = float(t["total_amount"]); new_paid = float(t["paid_amount"]) + additional
        if new_paid > total: new_paid = total
        pending = total - new_paid; status = "PAID" if pending <= 0 else "PENDING"
        if pending < 0: pending = 0
        conn.execute("UPDATE transactions SET paid_amount=?,pending_amount=?,status=? WHERE id=? AND user_id=?",
            (new_paid, pending, status, tid, current_user.id))
        conn.commit(); conn.close()
        log_action(current_user.id, "UPDATE_PAYMENT", f"Updated tx={tid} added={additional}"); flash("Payment updated.", "success")
        return redirect(url_for("transactions"))
    conn.close()
    return render_template("update_payment.html", t=t)

@app.route("/reminder/<int:tid>")
@login_required
def reminder(tid):
    conn = get_db()
    t_row = conn.execute("SELECT t.*,c.name as customer_name,c.phone as customer_phone,c.email as customer_email,c.address as customer_address,c.pin as customer_pin FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.id=? AND t.user_id=?", (tid, current_user.id)).fetchone()
    if not t_row: conn.close(); flash("Transaction not found", "danger"); return redirect(url_for("transactions"))
    t = dict(t_row)
    count = conn.execute("SELECT COUNT(*) as c FROM reminders WHERE transaction_id=? AND user_id=?", (tid, current_user.id)).fetchone()["c"]
    if count >= MAX_REMINDERS: conn.close(); flash(f"Reminder limit reached (Max {MAX_REMINDERS}).", "danger"); return redirect(url_for("transactions"))
    u_row = get_user_info(current_user.id); u = dict(u_row)
    sn = u.get("store_name") or "Smart Payment Store"; sp = u.get("phone") or "-"
    msg, tone, deadline, coupon = generate_reminder_message(sn, sp, t["customer_name"], t["purchase_date"], t["total_amount"], t["paid_amount"], t["pending_amount"])
    email_sent = False
    email_error = None
    if t.get("customer_email") and t.get("pending_amount", 0) > 0:
        try:
            pdf = generate_invoice_pdf(sn, sp, t.get("customer_name"), t.get("customer_phone"), t.get("customer_email"),
                t.get("customer_address"), t.get("customer_pin"),
                t.get("purchase_date"), t.get("total_amount"), t.get("paid_amount"), t.get("pending_amount"), tid, u.get("upi_id"))
            send_email(t["customer_email"], f"{sn} - Payment Reminder", plain_body=msg, attachment_bytes=pdf, attachment_filename="Invoice.pdf")
            email_sent = True
            log_action(current_user.id, "EMAIL_REMINDER_SENT", f"Email sent tx={tid}")
        except Exception as e:
            email_error = str(e)
            print(f"[EMAIL] Failed to send reminder email tx={tid}: {e}")
    conn.close()
    return render_template("reminder.html", t=t, msg=msg, tone=tone, deadline=deadline, coupon=coupon, count=count, email_sent=email_sent, email_error=email_error, gemini_status="Gemini AI" if gemini_available else "Built-in Model")

@app.route("/reminder/<int:tid>/save", methods=["POST"])
@login_required
def save_reminder(tid):
    conn = get_db()
    conn.execute("INSERT INTO reminders (transaction_id,message,tone,suggested_deadline,coupon_code,user_id) VALUES (?,?,?,?,?,?)",
        (tid, request.form.get("message"), request.form.get("tone"), request.form.get("deadline"), request.form.get("coupon"), current_user.id))
    conn.commit(); conn.close()
    log_action(current_user.id, "SAVE_REMINDER", f"Saved tx={tid}"); flash("Reminder saved.", "success")
    return redirect(url_for("reminder_history", tid=tid))

@app.route("/reminder-history/<int:tid>")
@login_required
def reminder_history(tid):
    conn = get_db()
    t = conn.execute("SELECT t.*,c.name as customer_name FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.id=? AND t.user_id=?", (tid, current_user.id)).fetchone()
    reminders = conn.execute("SELECT * FROM reminders WHERE transaction_id=? AND user_id=? ORDER BY id DESC", (tid, current_user.id)).fetchall()
    conn.close()
    return render_template("reminder_history.html", t=t, reminders=reminders)

@app.route("/reminder/<int:tid>/invoice")
@login_required
def download_invoice(tid):
    conn = get_db()
    t = conn.execute("SELECT t.*,c.name as customer_name,c.phone as customer_phone,c.email as customer_email,c.address as customer_address,c.pin as customer_pin FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.id=? AND t.user_id=?", (tid, current_user.id)).fetchone()
    conn.close()
    if not t: flash("Not found", "danger"); return redirect(url_for("transactions"))
    t = dict(t)
    u = get_user_info(current_user.id); u = dict(u)
    sn = u.get("store_name") or "Smart Payment Store"; sp = u.get("phone") or "-"
    pdf = generate_invoice_pdf(sn, sp, t.get("customer_name"), t.get("customer_phone"), t.get("customer_email"),
        t.get("customer_address"), t.get("customer_pin"),
        t.get("purchase_date"), t.get("total_amount"), t.get("paid_amount"), t.get("pending_amount"), tid, u.get("upi_id"))
    resp = make_response(pdf); resp.headers["Content-Type"] = "application/pdf"; resp.headers["Content-Disposition"] = "attachment; filename=Invoice.pdf"
    log_action(current_user.id, "INVOICE_DOWNLOADED", f"Invoice tx={tid}"); return resp

@app.route("/reminder/<int:tid>/whatsapp")
@login_required
def whatsapp_reminder(tid):
    conn = get_db()
    t_row = conn.execute("SELECT t.*,c.name as customer_name,c.phone as customer_phone FROM transactions t JOIN customers c ON t.customer_id=c.id WHERE t.id=? AND t.user_id=?", (tid, current_user.id)).fetchone()
    conn.close()
    if not t_row or not t_row["customer_phone"]: flash("Phone not found.", "danger"); return redirect(url_for("reminder", tid=tid))
    t = dict(t_row)
    u_row = get_user_info(current_user.id); u = dict(u_row)
    sn = u.get("store_name") or "Smart Payment Store"; sp = u.get("phone") or "-"
    msg, _, _, _ = generate_reminder_message(sn, sp, t["customer_name"], t["purchase_date"], t["total_amount"], t["paid_amount"], t["pending_amount"])
    invoice_link = url_for("download_invoice", tid=tid, _external=True)
    msg += f"\n\nDownload your invoice: {invoice_link}"
    phone = str(t["customer_phone"]).replace(" ", "").replace("-", "")
    if len(phone) == 10: phone = "91" + phone
    log_action(current_user.id, "WHATSAPP_OPENED", f"WhatsApp tx={tid}")
    return redirect("https://wa.me/" + phone + "?text=" + urllib.parse.quote(msg))

@app.route("/logout")
@login_required
def logout():
    log_action(current_user.id, "LOGOUT", "Logged out"); logout_user(); return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)
