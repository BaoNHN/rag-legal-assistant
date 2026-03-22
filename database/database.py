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

    # bảng chats
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at REAL
        )
    """)

    # bảng messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            text TEXT,
            timestamp REAL
        )
    """)

    conn.commit()
    conn.close()


# =========================
# 🆕 CREATE CHAT
# =========================
def create_chat():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    chat_id = f"chat_{int(time.time()*1000)}"
    title = "New Chat"
    created_at = time.time()

    c.execute(
        "INSERT INTO chats (id, title, created_at) VALUES (?, ?, ?)",
        (chat_id, title, created_at)
    )

    conn.commit()
    conn.close()

    return chat_id


# =========================
# 📋 GET ALL CHATS
# =========================
def get_all_chats():
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        c = conn.cursor()

        c.execute("""
            SELECT id, title FROM chats
            ORDER BY created_at DESC
        """)

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