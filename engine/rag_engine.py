import os
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_groq import ChatGroq

# =========================
# CONFIG
# =========================

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "chroma_db")

# 🔐 load API key
with open(os.path.join(BASE_DIR, "groqkey.txt"), "r") as f:
    GROQ_API_KEY = f.read().strip()

# =========================
# EMBEDDING
# =========================

embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)

# =========================
# VECTOR STORE
# =========================

vectorstore = Chroma(
    persist_directory=DB_PATH,
    embedding_function=embedding
)

retriever = vectorstore.as_retriever(
    search_kwargs={"k": 3}   # 🔥 quan trọng
)

# =========================
# LLM (GROQ)
# =========================

llm = ChatGroq(
    api_key=GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0
)

# =========================
# RAG FUNCTION
# =========================

def ask_rag(question: str) -> str:
    try:
        # 🔥 fix unicode input
        question = str(question).encode("utf-8", "ignore").decode("utf-8")

        # =========================
        # 1. RETRIEVE
        # =========================
        docs = retriever.invoke(question)

        if not docs:
            return "❌ Không tìm thấy thông tin phù hợp."

        # =========================
        # 2. BUILD CONTEXT (TRÁNH 413)
        # =========================
        context_parts = []
        total_chars = 0
        MAX_CONTEXT = 4000   # 🔥 tránh token limit

        for d in docs:
            text = d.page_content.strip()

            if total_chars + len(text) > MAX_CONTEXT:
                break

            context_parts.append(text)
            total_chars += len(text)

        context = "\n\n".join(context_parts)

        # =========================
        # 3. PROMPT
        # =========================
        prompt = f"""
Bạn là trợ lý pháp lý Việt Nam.

Dựa vào tài liệu sau:
{context}

Câu hỏi: {question}

Trả lời rõ ràng, dễ hiểu, có thể trích dẫn luật nếu cần.
"""

        # =========================
        # 4. LLM CALL
        # =========================
        response = llm.invoke(prompt)

        answer = response.content

        # =========================
        # 5. SOURCE
        # =========================
        sources = "\n".join([
            f"- {d.metadata.get('title','')} \n  🔗 {d.metadata.get('url','')}"
            for d in docs
        ])

        final_text = (
            "🤖 Trả lời:\n"
            + answer
            + "\n\n📖 Nguồn:\n"
            + sources
        )

        # 🔥 fix unicode output
        final_text = final_text.encode("utf-8", "ignore").decode("utf-8")

        return final_text

    except Exception as e:
        print("RAG ERROR:", repr(e))
        return "❌ Lỗi hệ thống. Vui lòng thử lại."