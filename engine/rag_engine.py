import os
import re
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_groq import ChatGroq
from langchain.schema import Document

# =========================
# CONFIG
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(BASE_DIR, "chroma_db")

with open(os.path.join(BASE_DIR, "groqkey.txt"), "r") as f:
    GROQ_API_KEY = f.read().strip()

# =========================
# EMBEDDING + VECTORSTORE
# =========================
embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

vectorstore = Chroma(
    persist_directory=DB_PATH,
    embedding_function=embedding
)

retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

# =========================
# LLM
# =========================
llm = ChatGroq(
    api_key=GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0
)

# =========================
# CLEAN TEXT
# =========================
def clean_answer(text: str) -> str:
    text = re.sub(r"(?i)^xin chào.*?\n", "", text)
    lines = text.split("\n")
    seen, cleaned = set(), []
    for l in lines:
        if l.strip() and l not in seen:
            cleaned.append(l)
            seen.add(l)
    return "\n".join(cleaned).strip()

# =========================
# QUERY REWRITE
# =========================
def rewrite_query(question: str) -> str:
    prompt = f"""Viết lại câu hỏi ngắn gọn, rõ nghĩa để tìm trong luật:

{question}"""
    try:
        return llm.invoke(prompt).content.strip()
    except Exception:
        return question

# =========================
# EXTRACT TOPIC FROM QUESTION
# ─────────────────────────────
# Knowledge questions always follow: "quy định về [TOPIC] là gì?"
# This matches the KB_Articles topic field exactly (100% match rate).
# Used for ChromaDB metadata filtering to pinpoint the right article.
# =========================
def extract_topic_from_question(question: str) -> str | None:
    q = question.strip()
    # Pattern: "quy định về X là gì" or "X là gì" or "X theo quy định"
    patterns = [
        r'quy định về (.+?) là gì',
        r'quy định về (.+?) như thế nào',
        r'quy định về (.+?) gồm',
        r'về (.+?) là gì',
        r'(.+?) là gì theo Luật',
    ]
    for p in patterns:
        m = re.search(p, q, re.IGNORECASE)
        if m:
            topic = m.group(1).strip().lower()
            # Remove trailing noise
            topic = re.sub(r'\s*(theo luật.*|của luật.*)$', '', topic).strip()
            return topic
    return None

# =========================
# TOPIC-AWARE RETRIEVAL
# ─────────────────────────────
# FIX for knowledge questions scoring 57.7/100:
# 1. Try ChromaDB metadata filter on topic first (exact match)
# 2. Fall back to standard semantic search if no match
# This eliminates wrong article retrieval (Điều 34 instead of 36, etc.)
# =========================
def retrieve_docs(question: str, rewritten_q: str):
    topic = extract_topic_from_question(question)

    if topic:
        try:
            results = vectorstore.get(include=["documents", "metadatas"])

            topic_docs = []
            for doc_text, meta in zip(results["documents"], results["metadatas"]):
                kb_topic = meta.get("topic", "").lower()
                if topic in kb_topic or kb_topic in topic:
                    topic_docs.append(Document(
                        page_content=doc_text,
                        metadata=meta
                    ))

            if topic_docs:
                # If multiple docs match (e.g. "Tài sản góp vốn" AND
                # "Định giá tài sản góp vốn" both match "định giá tài sản góp vốn"),
                # pick the one whose topic most closely matches the question topic.
                # Strategy: longest KB topic that is still contained in question topic
                # wins — it is the most specific match.
                def topic_score(doc):
                    kb_t = doc.metadata.get("topic", "").lower()
                    # Score 1: exact match
                    if kb_t == topic:
                        return (2, len(kb_t))
                    # Score 2: question topic contains KB topic (KB is subset)
                    if kb_t in topic:
                        return (1, len(kb_t))
                    # Score 3: KB topic contains question topic
                    return (0, len(kb_t))

                topic_docs.sort(key=topic_score, reverse=True)
                return topic_docs[:5]

        except Exception:
            pass

    # Fallback: standard semantic retrieval
    return retriever.invoke(rewritten_q)

# =========================
# RERANK
# ─────────────────────────
# Scores against page_content + retrieval_keywords (weighted x2)
# + prefers KB_Articles docs over Q&A docs
# =========================
def select_best_doc(question: str, docs):
    best_doc   = None
    best_score = -1
    q_words    = set(question.lower().split())

    for d in docs:
        # Score against content
        text_score = sum(1 for w in q_words if w in d.page_content.lower())

        # Score against curated keywords (weighted)
        kw_field = d.metadata.get("retrieval_keywords", "")
        if kw_field:
            kw_words  = set(kw_field.lower().replace(";", " ").split())
            kw_score  = sum(1 for w in q_words if w in kw_words) * 2
        else:
            kw_score = 0

        # Prefer KB_Articles over Q&A docs
        source_bonus = 1 if "KB_Articles" in d.metadata.get("nguon_thu_thap", "") else 0

        total = text_score + kw_score + source_bonus
        if total > best_score:
            best_score = total
            best_doc   = d

    return best_doc

# =========================
# CLASSIFY QUESTION
# =========================
_DOC_TYPE_MAP = {
    "definition": "definition", "rights": "condition",
    "obligations": "condition", "prohibited": "condition",
    "establishment_eligibility": "condition",
    "registration_procedure": "procedure",
    "registration_private_enterprise": "procedure",
    "registration_partnership": "procedure",
    "registration_llc": "procedure",
    "registration_jsc": "procedure",
    "change_registration": "procedure",
    "notification_change": "procedure",
    "publication": "procedure",
    "asset_transfer": "procedure",
    "erc_issuance": "condition",
    "name_prohibitions": "condition",
    "asset_valuation": "condition",
    "legal_representative_duty": "condition",
    "dependent_units": "definition",
}

def classify_question(question: str, best_doc=None) -> str:
    if best_doc:
        doc_type = best_doc.metadata.get("doc_type", "")
        if doc_type and doc_type in _DOC_TYPE_MAP:
            return _DOC_TYPE_MAP[doc_type]
    q = question.lower()
    if any(w in q for w in ["bước", "thủ tục", "quy trình", "đăng ký", "cách"]):
        return "procedure"
    if any(w in q for w in ["điều kiện", "yêu cầu", "cần có", "phải có"]):
        return "condition"
    if any(w in q for w in ["là gì", "định nghĩa", "khái niệm"]):
        return "definition"
    return "general"

# =========================
# BUILD PROMPT
# =========================
def build_prompt(context: str, question: str, q_type: str,
                 article_ref: str = "", topic: str = "") -> str:
    article_hint = ""
    if article_ref:
        line = article_ref
        if topic:
            line += f" — {topic}"
        article_hint = f"\nCăn cứ pháp lý: {line}\n"

    base = f"""Bạn là trợ lý pháp lý Việt Nam.

⚠️ QUY TẮC:
- Chỉ trả lời 1 cách duy nhất
- Không chào hỏi, không giải thích dư
- Luôn trích dẫn đúng điều luật cụ thể
- Không bịa thêm điều luật ngoài tài liệu
{article_hint}
Tài liệu:
{context}

Câu hỏi: {question}

"""
    if q_type == "procedure":
        base += "Trả lời dạng các bước rõ ràng (1, 2, 3...)."
    elif q_type == "condition":
        base += "Chỉ liệt kê các điều kiện/yêu cầu."
    elif q_type == "definition":
        base += "Trả lời ngắn gọn định nghĩa, nêu rõ căn cứ điều luật."
    else:
        base += "Trả lời ngắn gọn, đúng trọng tâm, nêu căn cứ pháp lý."
    return base

# =========================
# BUILD CITATION
# =========================
def build_citation(meta: dict) -> str:
    article_ref = meta.get("article_reference", "")
    topic       = meta.get("topic", "")
    so_ky_hieu  = meta.get("so_ky_hieu", "")
    loai        = meta.get("loai_van_ban", "")
    source_url  = meta.get("source_url", "")

    parts = []
    if article_ref:
        line = article_ref
        if topic:
            line += f" ({topic})"
        parts.append(line)
    law_name = f"{loai} {so_ky_hieu}".strip()
    if law_name:
        parts.append(law_name)

    citation = " — ".join(parts) if parts else meta.get("nguon_thu_thap", "")
    result   = f"\n\n📖 Nguồn: {citation}"
    if source_url and "vbpl.vn" in source_url:
        result += f"\n🔗 {source_url}"
    return result

# =========================
# MAIN
# =========================
def ask_rag(question: str) -> str:
    try:
        question = str(question)

        # STEP 1: rewrite
        better_q = rewrite_query(question)

        # STEP 2: topic-aware retrieval (fixes knowledge question accuracy)
        docs = retrieve_docs(question, better_q)

        if not docs:
            return "❌ Không tìm thấy thông tin liên quan trong cơ sở dữ liệu pháp luật."

        # STEP 3: rerank
        best_doc = select_best_doc(better_q, docs)
        if not best_doc:
            return "❌ Không đủ dữ liệu để trả lời câu hỏi này."

        # STEP 4: context
        context = best_doc.page_content[:6000]

        # STEP 5: classify using doc_type metadata
        q_type = classify_question(question, best_doc)

        # STEP 6: prompt with article hint
        article_ref = best_doc.metadata.get("article_reference", "")
        topic       = best_doc.metadata.get("topic", "")
        prompt      = build_prompt(context, question, q_type, article_ref, topic)

        # STEP 7: generate
        answer = llm.invoke(prompt).content.strip()

        # STEP 8: clean
        answer = clean_answer(answer)

        # STEP 9: citation
        return answer + build_citation(best_doc.metadata)

    except Exception as e:
        print("RAG ERROR:", e)
        return "❌ Lỗi hệ thống."