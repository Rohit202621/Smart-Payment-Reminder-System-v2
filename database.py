import sqlite3
from werkzeug.security import generate_password_hash

DB_NAME = "payment_reminder.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        store_name TEXT DEFAULT 'Smart Payment Store',
        upi_id TEXT DEFAULT NULL,
        profile_pic TEXT DEFAULT NULL,
        reset_otp TEXT,
        reset_otp_expiry TEXT,
        profile_otp TEXT,
        profile_otp_expiry TEXT,
        profile_otp_action TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        pin TEXT,
        preferred_payment_method TEXT,
        user_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        purchase_date TEXT NOT NULL,
        total_amount REAL NOT NULL,
        paid_amount REAL NOT NULL DEFAULT 0,
        pending_amount REAL NOT NULL,
        status TEXT NOT NULL,
        reminder_sent_3 INTEGER DEFAULT 0,
        reminder_sent_7 INTEGER DEFAULT 0,
        reminder_sent_15 INTEGER DEFAULT 0,
        user_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        tone TEXT NOT NULL,
        suggested_deadline TEXT,
        coupon_code TEXT,
        user_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        details TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
    )
    """)

    def add_column(table, column_sql):
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")
        except sqlite3.OperationalError:
            pass

    add_column("users", "phone TEXT")
    add_column("users", "store_name TEXT DEFAULT 'Smart Payment Store'")
    add_column("users", "upi_id TEXT DEFAULT NULL")
    add_column("users", "reset_otp TEXT")
    add_column("users", "reset_otp_expiry TEXT")
    add_column("users", "profile_pic TEXT DEFAULT NULL")
    add_column("users", "profile_otp TEXT")
    add_column("users", "profile_otp_expiry TEXT")
    add_column("users", "profile_otp_action TEXT")
    add_column("customers", "address TEXT")
    add_column("customers", "pin TEXT")
    add_column("transactions", "reminder_sent_3 INTEGER DEFAULT 0")
    add_column("transactions", "reminder_sent_7 INTEGER DEFAULT 0")
    add_column("transactions", "reminder_sent_15 INTEGER DEFAULT 0")

    cur.execute("SELECT id FROM users WHERE username=?", ("admin@example.com",))
    admin = cur.fetchone()
    if not admin:
        cur.execute("""
            INSERT INTO users (username, password_hash, email, phone, store_name)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "admin@example.com",
            generate_password_hash("admin123"),
            "admin@example.com",
            "9999999999",
            "Smart Payment Reminder"
        ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
