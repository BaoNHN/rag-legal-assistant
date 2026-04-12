from flask import Flask, render_template, request, session, redirect
from engine.rag_engine import ask_rag
from database.database import (
    init_db,
    create_chat,
    get_all_chats,
    save_message,
    get_messages,
    rename_chat
)

app = Flask(__name__)
app.secret_key = "secret_key"

# init DB
init_db()


@app.route("/")
def home():
    if "student_id" not in session:
        return render_template("login.html")
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    student_name = data.get("student_name")
    password = data.get("password")

    import sqlite3
    conn = sqlite3.connect("chat.db")
    c = conn.cursor()

    c.execute(
        "SELECT student_id FROM students WHERE student_name=? AND password=?",
        (student_name, password)
    )

    user = c.fetchone()
    conn.close()

    if user:
        session["student_id"] = user[0]
        return jsonify({"status": "success"})
    return jsonify({"status": "fail"}), 401

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})

# =========================
# CHAT API (RAG)
# =========================
from flask import jsonify

@app.route("/get", methods=["POST"])
def chatbot():
    try:
        data = request.get_json()

        user_input = data.get("prompt")
        chat_id = data.get("chat_id")

        if not user_input:
            return jsonify({
                "status": "error",
                "text": "⚠️ Bạn chưa nhập câu hỏi."
            })

        # 🔥 save user message
        save_message(chat_id, "user", user_input)

        response = ask_rag(user_input)

        # 🔥 save bot message
        save_message(chat_id, "assistant", response)

        return jsonify({
            "status": "success",
            "text": response
        })

    except Exception as e:
        print("SERVER ERROR:", e)
        return jsonify({
            "status": "error",
            "text": str(e)
        })

# =========================
# CHAT MANAGEMENT
# =========================

@app.route("/list_chats", methods=["GET"])
def api_list_chats():
    if "student_id" not in session:
        return jsonify([])

    chats = get_all_chats(session["student_id"])
    return jsonify(chats)


@app.route("/create_chat", methods=["POST"])
def api_create_chat():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    chat_id = create_chat(session["student_id"])
    return jsonify({"chat_id": chat_id})

@app.route("/rename_chat", methods=["POST"])
def api_rename_chat():
    data = request.get_json()
    rename_chat(data["chat_id"], data["title"])
    return jsonify({"status": "ok"})

@app.route("/get_chat_messages")
def api_get_messages():
    chat_id = request.args.get("chat_id")
    messages = get_messages(chat_id)
    return jsonify(messages)


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)