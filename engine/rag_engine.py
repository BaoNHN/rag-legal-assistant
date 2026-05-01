import os
import re
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
    search_kwargs={"k": 5}
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
# 🔥 CLEAN TEXT
# =========================

def clean_answer(text: str) -> str:
    # remove greeting
    text = re.sub(r"(?i)^xin chào.*?\n", "", text)

    # remove duplicate lines
    lines = text.split("\n")
    seen = set()
    cleaned = []
    for l in lines:
        if l.strip() and l not in seen:
            cleaned.append(l)
            seen.add(l)

    return "\n".join(cleaned).strip()


# =========================
# 🔥 QUERY REWRITE
# =========================

def rewrite_query(question: str) -> str:
    prompt = f"""
Viết lại câu hỏi ngắn gọn, rõ nghĩa để tìm trong luật:

{question}
"""
    try:
        return llm.invoke(prompt).content.strip()
    except:
        return question


# =========================
# 🔥 RERANK (CHỈ LẤY 1 DOC)
# =========================

def select_best_doc(question, docs):
    best_doc = None
    best_score = -1

    q_words = set(question.lower().split())

    for d in docs:
        text = d.page_content.lower()
        score = sum(1 for w in q_words if w in text)

        if score > best_score:
            best_score = score
            best_doc = d

    return best_doc


# =========================
# 🔥 CLASSIFY
# =========================

def classify_question(q):
    q = q.lower()

    if "bước" in q or "cách" in q:
        return "procedure"
    if "điều kiện" in q:
        return "condition"
    if "là gì" in q:
        return "definition"
    return "general"


# =========================
# 🔥 BUILD PROMPT (ANTI-LOẠN)
# =========================

def build_prompt(context, question, q_type):
    base = f"""
Bạn là trợ lý pháp lý Việt Nam.

⚠️ QUY TẮC:
- Chỉ trả lời 1 cách duy nhất
- Không chào hỏi
- Không giải thích dư
- Không đưa nhiều phương án

Tài liệu:
{context}

Câu hỏi: {question}

"""

    if q_type == "procedure":
        base += "Trả lời dạng các bước rõ ràng (1,2,3...)."
    elif q_type == "condition":
        base += "Chỉ liệt kê các điều kiện."
    elif q_type == "definition":
        base += "Trả lời ngắn gọn định nghĩa."
    else:
        base += "Trả lời ngắn gọn, đúng trọng tâm."

    return base


# =========================
# 🔥 MAIN
# =========================

def ask_rag(question: str) -> str:
    try:
        question = str(question)

        # STEP 1: rewrite
        better_q = rewrite_query(question)

        # STEP 2: retrieve
        docs = retriever.invoke(better_q)

        if not docs:
            return "❌ Không tìm thấy thông tin."

        # STEP 3: chọn doc tốt nhất
        best_doc = select_best_doc(better_q, docs)

        if not best_doc:
            return "❌ Không đủ dữ liệu."

        # STEP 4: context limit
        context = best_doc.page_content[:3000]

        # STEP 5: classify
        q_type = classify_question(question)

        # STEP 6: prompt
        prompt = build_prompt(context, question, q_type)

        # STEP 7: LLM
        response = llm.invoke(prompt)
        answer = response.content.strip()

        # STEP 8: clean output
        answer = clean_answer(answer)

        # STEP 9: source
        m = best_doc.metadata
        soky = m.get('so_ky_hieu', '')
        loai = m.get('loai_van_ban', '')
        nguon = m.get('nguon_thu_thap', '')

        cite_id = f"{loai} {soky}".strip() or m.get('title', '')
        source = f"\n\n📖 Nguồn: {cite_id}"
        if nguon:
            source += f" — {nguon}"

        return answer + source

    except Exception as e:
        print("RAG ERROR:", e)
        return "❌ Lỗi hệ thống."