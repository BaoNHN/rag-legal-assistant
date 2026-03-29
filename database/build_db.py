# build_db.py (STREAMING VERSION)

import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"
os.environ["HF_HUB_TIMEOUT"] = "60"
os.environ["HF_HUB_MAX_RETRIES"] = "10"

from datasets import load_dataset
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter

# =========================
# CONFIG
# =========================

MAX_DOCS = 1000
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80

# =========================
# LOAD METADATA (NHẸ)
# =========================

print("🔄 Loading metadata...")

meta_ds = load_dataset(
    "th1nhng0/vietnamese-legal-documents",
    "metadata",
    split="data"
)

meta_dict = {item["id"]: item for item in meta_ds}

print(f"✅ Metadata loaded: {len(meta_dict)}")

# =========================
# STREAM CONTENT (NẶNG)
# =========================

print("🔄 Streaming content...")

content_ds = load_dataset(
    "th1nhng0/vietnamese-legal-documents",
    "content",
    split="data",
    streaming=True
)

# =========================
# TEXT SPLITTER
# =========================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP
)

docs = []

print("🔄 Processing...")

for i, item in enumerate(content_ds):

    doc_id = item.get("id")
    content = item.get("content", "")

    if not content or not content.strip():
        continue

    meta = meta_dict.get(doc_id, {})

    # 🎯 FILTER doanh nghiệp
    sectors = str(meta.get("legal_sectors", "")).lower()
    if "doanh nghiệp" not in sectors:
        continue

    # 🧠 CHUNK (KHÔNG truncate)
    chunks = splitter.split_text(content)

    for chunk in chunks:
        docs.append(
            Document(
                page_content=chunk,
                metadata={
                    "id": doc_id,
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "legal_type": meta.get("legal_type", ""),
                    "source": meta.get("document_number", "")
                }
            )
        )

    if i % 200 == 0:
        print(f"📄 Scanned: {i} | Docs: {len(docs)}")

    if len(docs) >= MAX_DOCS:
        break

print(f"✅ Final docs: {len(docs)}")

# =========================
# EMBEDDING
# =========================

print("🔄 Loading embedding model...")

embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)

# =========================
# BUILD CHROMA
# =========================

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "chroma_db")

print("💾 Building Chroma DB...")

Chroma.from_documents(
    docs,
    embedding,
    persist_directory=DB_PATH
)

print("🎉 DONE: Streaming DB ready!")