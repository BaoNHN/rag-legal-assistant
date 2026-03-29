# rag_engine.py

import os
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.chains import RetrievalQA
from langchain_groq import ChatGroq

# =========================
# 1. Load Groq API Key
# =========================
def load_groq_key(path="groqkey.txt"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if not key:
                raise ValueError("Empty API key")
            return key
    except Exception as e:
        raise ValueError(f"❌ Cannot read groqkey.txt: {e}")


GROQ_API_KEY = load_groq_key()


# =========================
# 2. Embedding
# =========================
embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)


# =========================
# 3. Load Chroma DB
# =========================
vectorstore = Chroma(
    persist_directory="../chroma_db",
    embedding_function=embedding
)

retriever = vectorstore.as_retriever(
    search_kwargs={"k": 3}
)


# =========================
# 4. LLM (Groq)
# =========================
llm = ChatGroq(
    model="llama-3.1-8b-instant",
    temperature=0,
    max_tokens=512,
    api_key=GROQ_API_KEY
)


# =========================
# 5. RAG Chain
# =========================
qa_chfain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=retriever,
    return_source_documents=True
)


# =========================
# 6. Ask function
# =========================
def ask_rag(question: str) -> str:
    try:
        result = qa_chain.invoke({"query": question})

        answer = result.get("result", "")

        sources = "\n".join([
            f"- {doc.metadata.get('title', '')} \n  🔗 {doc.metadata.get('url', '')}"
            for doc in result.get("source_documents", [])
        ])

        return (
            "🤖 Trả lời:\n"
            + answer
            + "\n\n📖 Nguồn:\n"
            + sources
        )

    except Exception as e:
        return f"❌ Lỗi hệ thống: {str(e)}"