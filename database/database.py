# database.py

import sqlite3
import time
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # database/ → root
DB_NAME  = os.path.join(BASE_DIR, "chat.db")


def get_conn():
    return sqlite3.connect(DB_NAME)


# =========================
# INIT DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()

    # ── users table (replaces students + teachers)
    # role: 0 = student, 1 = teacher, 2 = admin (extensible)
    # status: 0 = active, 1 = disabled (login restricted)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT UNIQUE,
            password  TEXT,
            role      INTEGER DEFAULT 0,
            status    INTEGER DEFAULT 0
        )
    """)
    # Migration for existing DBs missing status column
    try:
        c.execute("ALTER TABLE users ADD COLUMN status INTEGER DEFAULT 0")
    except Exception:
        pass

    # ── chats
    # role: 0 = student chat, 1 = teacher chat
    # Filtering by (user_id, role) keeps teacher/student chats separate
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id         TEXT PRIMARY KEY,
            student_id INTEGER,
            title      TEXT,
            created_at REAL,
            role       INTEGER DEFAULT 0
        )
    """)
    # Migration for existing DBs missing role column
    try:
        c.execute("ALTER TABLE chats ADD COLUMN role INTEGER DEFAULT 0")
    except Exception:
        pass

    # Rename the legacy "Import new law" status chat to "Thông báo" — the
    # reserved title used from here on (see NOTIFICATION_CHAT_TITLE below).
    # NOTIFICATION_CHAT_TITLE isn't defined yet at this point in the file, but
    # init_db() only ever runs after the whole module has finished loading.
    c.execute("UPDATE chats SET title=? WHERE title=?", (NOTIFICATION_CHAT_TITLE, "Import new law"))

    # Scenario import used to post to its own separate status chat
    # ("Nhập văn bản tình huống") instead of the shared "Thông báo" chat —
    # redundant, and unlike NOTIFICATION_CHAT_TITLE it wasn't excluded from
    # MAX_CHATS_PER_USER or locked read-only in the UI. Fold any such legacy
    # chat's messages into the user's "Thông báo" chat (creating one if they
    # don't have it yet) and drop the now-empty legacy chat.
    c.execute("SELECT id, student_id FROM chats WHERE title=? AND role=1", ("Nhập văn bản tình huống",))
    for legacy_id, uid in c.fetchall():
        c.execute(
            "SELECT id FROM chats WHERE student_id=? AND title=? AND role=1 ORDER BY created_at DESC LIMIT 1",
            (uid, NOTIFICATION_CHAT_TITLE)
        )
        row = c.fetchone()
        if row:
            target_id = row[0]
        else:
            target_id = f"import_{int(time.time()*1000)}_{uid}"
            c.execute(
                "INSERT INTO chats (id, student_id, title, created_at, role) VALUES (?,?,?,?,?)",
                (target_id, uid, NOTIFICATION_CHAT_TITLE, time.time(), 1)
            )
        c.execute("UPDATE messages SET chat_id=? WHERE chat_id=?", (target_id, legacy_id))
        c.execute("DELETE FROM chats WHERE id=?", (legacy_id,))
        _trim_notification_messages(c, target_id)

    # ── messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id   TEXT,
            role      TEXT,
            text      TEXT,
            timestamp REAL
        )
    """)

    # ── Migrate old students table → users (if exists)
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='students'")
    if c.fetchone():
        c.execute("SELECT student_id, student_name, password FROM students")
        old_students = c.fetchall()
        for sid, sname, spwd in old_students:
            try:
                c.execute(
                    "INSERT OR IGNORE INTO users (user_id, user_name, password, role) VALUES (?,?,?,0)",
                    (sid, sname, spwd)
                )
            except Exception:
                pass

    # ── Migrate old teachers table → users (if exists)
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='teachers'")
    if c.fetchone():
        c.execute("SELECT teacher_id, teacher_name, password FROM teachers")
        old_teachers = c.fetchall()
        for tid, tname, tpwd in old_teachers:
            try:
                c.execute(
                    "INSERT OR IGNORE INTO users (user_id, user_name, password, role) VALUES (?,?,?,1)",
                    (tid, tname, tpwd)
                )
            except Exception:
                pass

    # ── Seed default accounts if users table is empty
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (user_name, password, role) VALUES (?,?,?)",
            ("testStudent1", "123456P@ss", 0)
        )
        c.execute(
            "INSERT INTO users (user_name, password, role) VALUES (?,?,?)",
            ("teacher1", "Teacher@123", 1)
        )
        c.execute(
            "INSERT INTO users (user_name, password, role) VALUES (?,?,?)",
            ("admin1", "Admin@123", 2)
        )

    # ── const (key/value store for cross-cutting config — e.g. the whitelist
    # of legitimate document sources currently indexed in chroma_db, used to
    # verify a citation isn't naming a source that was never actually imported)
    c.execute("""
        CREATE TABLE IF NOT EXISTS const (
            name    TEXT PRIMARY KEY,
            content TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================
# CONST (key/value store)
# =========================
def _ensure_const_table(c):
    # engine.rag_engine calls set_const()/get_const() at module import time,
    # which can run before app.py's init_db() — don't depend on ordering.
    c.execute("""
        CREATE TABLE IF NOT EXISTS const (
            name    TEXT PRIMARY KEY,
            content TEXT
        )
    """)


def set_const(name: str, content: str):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    _ensure_const_table(c)
    c.execute(
        "INSERT INTO const (name, content) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET content=excluded.content",
        (name, content)
    )
    conn.commit()
    conn.close()


def get_const(name: str) -> str:
    conn = sqlite3.connect(DB_NAME)
    _ensure_const_table(conn.cursor())
    row  = conn.execute("SELECT content FROM const WHERE name=?", (name,)).fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else ""


# =========================
# CHAT LIMITS / RESERVED NAMES
# =========================
# The teacher/admin "import law" status chat is reserved under this exact
# title — it's excluded from the 5-chat cap and users may not create/rename
# any chat to exactly this string (substrings like "Thông báo họp" are fine).
NOTIFICATION_CHAT_TITLE = "Thông báo"
MAX_CHATS_PER_USER = 5
NOTIFICATION_KEEP_LATEST = 5


# =========================
# LOGIN
# =========================
ROLE_NAMES = {0: "Student", 1: "Teacher", 2: "Admin"}


def role_name(role: int) -> str:
    return ROLE_NAMES.get(int(role), "Student")


def login_user(username: str, password: str):
    """
    Returns dict: {user_id, user_name, user_type, role} on success.
    Returns {"disabled": True} if credentials match a disabled account.
    Returns None if credentials don't match any account.
    role: 0=student, 1=teacher, 2=admin
    user_type: 'student' | 'teacher' | 'admin'
    """
    conn = sqlite3.connect(DB_NAME)
    row  = conn.execute(
        "SELECT user_id, role, status FROM users WHERE user_name=? AND password=?",
        (username, password)
    ).fetchone()
    conn.close()

    if not row:
        return None

    role   = int(row[1])
    status = int(row[2] or 0)
    if status == 1:
        return {"disabled": True}

    user_type = "admin" if role == 2 else ("teacher" if role == 1 else "student")
    return {
        "user_id":   row[0],
        "user_name": username,
        "user_type": user_type,
        "role":      role,
    }


# =========================
# CHAT MANAGEMENT
# =========================
def create_chat(user_id, owner_role: int = 0):
    """
    owner_role: 0=student chat, 1=teacher chat.
    Stored in chats.role so get_all_chats filters correctly.
    """
    conn    = sqlite3.connect(DB_NAME)
    c       = conn.cursor()
    chat_id = f"chat_{int(time.time()*1000)}"
    c.execute(
        "INSERT INTO chats (id, student_id, title, created_at, role) VALUES (?,?,?,?,?)",
        (chat_id, user_id, "Đoạn chat mới", time.time(), owner_role)
    )
    conn.commit()
    conn.close()
    return chat_id


def get_all_chats(user_id, owner_role: int = 0):
    """
    Filters by both user_id AND role — teachers and students
    never see each other's chats even if they share the same numeric id.
    """
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, title FROM chats WHERE student_id=? AND role=? ORDER BY created_at DESC",
            (user_id, owner_role)
        )
        return [{"id": r[0], "title": r[1]} for r in c.fetchall()]


def rename_chat(chat_id, title) -> bool:
    """Returns False (no-op) if `title` is exactly the reserved
    NOTIFICATION_CHAT_TITLE — only an exact match is blocked, a title merely
    containing the word (e.g. "Thông báo họp lúc 9h") is fine."""
    if (title or "").strip() == NOTIFICATION_CHAT_TITLE:
        return False
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
    conn.commit()
    conn.close()
    return True


def get_chat_title(chat_id) -> str | None:
    conn = sqlite3.connect(DB_NAME)
    row  = conn.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def save_message(chat_id, role, text):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute(
        "INSERT INTO messages (chat_id, role, text, timestamp) VALUES (?,?,?,?)",
        (chat_id, role, str(text), time.time())
    )
    conn.commit()
    conn.close()


def get_messages(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute(
        "SELECT role, text FROM messages WHERE chat_id=? ORDER BY timestamp ASC",
        (chat_id,)
    )
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "text": r[1]} for r in rows]


def delete_chat(chat_id: str):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    c.execute("DELETE FROM chats    WHERE id=?",      (chat_id,))
    conn.commit()
    conn.close()


def _trim_notification_messages(c, chat_id: str, keep: int = NOTIFICATION_KEEP_LATEST):
    """Delete all but the `keep` most recent messages in a notification chat —
    these chats only ever accumulate (one import = one more message, forever),
    so without a cap they'd grow unbounded."""
    c.execute(
        "DELETE FROM messages WHERE chat_id=? AND id NOT IN ("
        "  SELECT id FROM messages WHERE chat_id=? ORDER BY timestamp DESC LIMIT ?"
        ")",
        (chat_id, chat_id, keep)
    )


def upsert_import_chat(user_id: int, message: str, title: str = NOTIFICATION_CHAT_TITLE):
    """Create the given teacher-chat title if missing, append message to it,
    then trim to the latest NOTIFICATION_KEEP_LATEST messages."""
    conn  = sqlite3.connect(DB_NAME)
    c     = conn.cursor()

    c.execute(
        "SELECT id FROM chats WHERE student_id=? AND title=? AND role=1 ORDER BY created_at DESC LIMIT 1",
        (user_id, title)
    )
    row = c.fetchone()
    if row:
        chat_id = row[0]
    else:
        chat_id = f"import_{int(time.time()*1000)}"
        c.execute(
            "INSERT INTO chats (id, student_id, title, created_at, role) VALUES (?,?,?,?,?)",
            (chat_id, user_id, title, time.time(), 1)
        )
    c.execute(
        "INSERT INTO messages (chat_id, role, text, timestamp) VALUES (?,?,?,?)",
        (chat_id, "assistant", message, time.time())
    )
    _trim_notification_messages(c, chat_id)
    conn.commit()
    conn.close()


# =========================
# ACCOUNT MANAGEMENT (admin)
# =========================
def get_all_users():
    """Returns list of {user_id, user_name, role, role_name, status} ordered by user_id."""
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT user_id, user_name, role, status FROM users ORDER BY user_id ASC")
    rows = c.fetchall()
    conn.close()
    return [
        {
            "user_id":   r[0],
            "user_name": r[1],
            "role":      int(r[2]),
            "role_name": role_name(r[2]),
            "status":    int(r[3] or 0),
        }
        for r in rows
    ]


def set_user_status(user_id: int, status: int):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id: int):
    """Deletes a user, all their chats (student + teacher role chats) and messages."""
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT id FROM chats WHERE student_id=?", (user_id,))
    chat_ids = [r[0] for r in c.fetchall()]
    for chat_id in chat_ids:
        c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    c.execute("DELETE FROM chats WHERE student_id=?", (user_id,))
    c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def create_user(user_name: str, password: str, role: int):
    """Creates a new user. Returns True if created, False if user_name already exists."""
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (user_name, password, role, status) VALUES (?,?,?,0)",
            (user_name, password, role)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_user_by_name(username: str):
    conn = sqlite3.connect(DB_NAME)
    row  = conn.execute(
        "SELECT user_id, user_name, password, role, status FROM users WHERE user_name=?",
        (username,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id":   row[0],
        "user_name": row[1],
        "password":  row[2],
        "role":      int(row[3]),
        "status":    int(row[4] or 0),
    }


def change_user_password(username: str, old_password: str, new_password: str):
    """
    Verifies old_password against DB then updates to new_password.
    Returns (True, "") on success, or (False, reason) on failure.
    """
    user = get_user_by_name(username)
    if not user:
        return False, "Tài khoản không tồn tại"
    if user["password"] != old_password:
        return False, "Mật khẩu cũ không đúng"

    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("UPDATE users SET password=? WHERE user_name=?", (new_password, username))
    conn.commit()
    conn.close()
    return True, ""