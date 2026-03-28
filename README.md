# 🚀 Smart Payment Reminder System
## 📌 Overview

Managing payments, dues, and invoices manually can lead to missed deadlines and financial loss.
This system provides a **centralized and automated solution** to:

* 📅 Track pending payments
* 🔔 Send timely reminders
* 📊 Monitor financial activity
* 📁 Manage customers and transactions

---

## ✨ Features

* 🔐 Secure User Authentication (Login/Register)
* 👤 Profile Management System
* 💰 Payment & Transaction Tracking
* ⏰ Automated Reminder System
* 📜 Reminder History Logs
* 📊 Dashboard with Insights
* 🧾 Invoice & Payment Records
* 🔍 Audit Logs for tracking activity
* 📩 OTP Verification System

---

## 🛠 Tech Stack

| Category       | Technology            |
| -------------- | --------------------- |
| Backend        | Python (Flask)        |
| Database       | SQLite                |
| Frontend       | HTML, CSS, JavaScript |
| Authentication | Flask-Login           |
| Scheduling     | APScheduler           |

---

## 📂 Project Structure

```bash
Smart-Payment-Reminder-System-v2/
│── app.py
│── database.py
│── requirements.txt
│── static/
│   ├── style.css
│   └── uploads/
│── templates/
│   ├── dashboard.html
│   ├── login.html
│   ├── register.html
│   └── ...
│── .gitignore
│── README.md
```

---

## ⚙️ Installation & Setup

### 1️⃣ Clone the repository

```bash
git clone https://github.com/Rohit202621/Smart-Payment-Reminder-System-v2.git
cd Smart-Payment-Reminder-System-v2
```

### 2️⃣ Create virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 3️⃣ Install dependencies

```bash
pip install -r requirements.txt
```

### 4️⃣ Run the application

```bash
python app.py
```

---

## 🔐 Environment Variables

Create a `.env` file in root directory:

```env
SECRET_KEY=your_secret_key
EMAIL_USER=your_email
EMAIL_PASS=your_password
GOOGLE_CLIENT_ID=**********
GOOGLE_CLIENT_SECRET=***************
GEMINI_API_KEY=********************
```

⚠️ Never upload `.env` to GitHub

---
## 🚀 Future Enhancements

* 🤖 AI-powered reminder system (Gemini / Claude)
* 📱 WhatsApp & SMS notifications
* ☁️ Cloud-based database (PostgreSQL)
* 📊 Advanced analytics dashboard
* 🔔 Real-time push notifications
* 🌍 Multi-user role management

---

## 🧠 Use Cases

* Small business payment tracking
* Freelancers managing invoices
* Personal finance monitoring
* Subscription reminder system

---

## 🧪 Testing

Basic manual testing can be done via:

* Creating users
* Adding transactions
* Triggering reminders

(You can add automated tests later)

---
## 👨‍💻 Author
**Rohit Ranjan Giri**
## ⭐ Support

If you like this project:

* ⭐ Star the repo
* 🍴 Fork it
* 📢 Share with others

---

## 📜 License

This project is for educational purposes only .

---

## 💡 Final Note

This project demonstrates a **real-world implementation of payment tracking and reminder automation**, combining backend logic with user-friendly design.

---
