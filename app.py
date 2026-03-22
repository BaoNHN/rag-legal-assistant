from flask import Flask, render_template, request
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

# init DB
init_db()


@app.route("/")
def home():
    return render_template("index.html")


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
    chats = get_all_chats()
    return jsonify(chats)


@app.route("/create_chat", methods=["POST"])
def api_create_chat():
    chat_id = create_chat()
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