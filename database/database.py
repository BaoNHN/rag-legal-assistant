# database.py

import sqlite3
import time
import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, "chat.db")


# =========================
# 🔧 INIT DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # 👤 students
    c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id INTEGER PRIMARY KEY,
            student_name TEXT,
            password TEXT
        )
    """)

    # 💬 chats (THÊM student_id)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            student_id INTEGER,
            title TEXT,
            created_at REAL
        )
    """)

    # 💬 messages (giữ nguyên)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            text TEXT,
            timestamp REAL
        )
    """)
    # Add student for testing
    c.execute("SELECT * FROM students WHERE student_id=1")
    if not c.fetchone():
        c.execute("""
            INSERT INTO students (student_id, student_name, password)
            VALUES (1, 'test', '123');
        """)
    conn.commit()
    conn.close()


# =========================
# 🆕 CREATE CHAT
# =========================
def create_chat(student_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    chat_id = f"chat_{int(time.time()*1000)}"
    title = "New Chat"
    created_at = time.time()

    c.execute(
        "INSERT INTO chats (id, student_id, title, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, student_id, title, created_at)
    )

    conn.commit()
    conn.close()

    return chat_id


# =========================
# 📋 GET ALL CHATS
# =========================
def get_all_chats(student_id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        c.execute("""
            SELECT id, title FROM chats
            WHERE student_id=?
            ORDER BY created_at DESC
        """, (student_id,))

        rows = c.fetchall()

        return [{"id": r[0], "title": r[1]} for r in rows]


# =========================
# ✏️ RENAME CHAT
# =========================
def rename_chat(chat_id, title):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute(
        "UPDATE chats SET title=? WHERE id=?",
        (title, chat_id)
    )

    conn.commit()
    conn.close()


# =========================
# 💬 SAVE MESSAGE
# =========================
def save_message(chat_id, role, text):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # 🔥 đảm bảo unicode safe
    safe_text = str(text)

    c.execute("""
            INSERT INTO messages (chat_id, role, text, timestamp)
            VALUES (?, ?, ?, ?)
    """, (chat_id, role, safe_text, time.time()))

    conn.commit()
    conn.close()


# =========================
# 📜 GET MESSAGES
# =========================
def get_messages(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        SELECT role, text FROM messages
        WHERE chat_id=?
        ORDER BY timestamp ASC
    """, (chat_id,))

    rows = c.fetchall()
    conn.close()

    return [
        {"role": row[0], "text": row[1]}
        for row in rows
    ]