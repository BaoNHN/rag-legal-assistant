# build_db.py

import os

os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
os.environ["HF_HUB_TIMEOUT"] = "60"   # tăng timeout
os.environ["HF_HUB_MAX_RETRIES"] = "10"

from datasets import load_dataset
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

print("🔄 Loading metadata...")

# ✅ Load metadata (nhẹ)
meta_ds = load_dataset(
    "th1nhng0/vietnamese-legal-documents",
    "metadata",
    split="data"
)

meta_dict = {item["id"]: item for item in meta_ds}

print(f"✅ Metadata loaded: {len(meta_dict)}")

print("🔄 Streaming content...")

# ✅ Streaming content (nặng)
content_ds = load_dataset(
    "th1nhng0/vietnamese-legal-documents",
    "content",
    split="data",
    streaming=True
)

MAX_DOCS = 2000

docs = []

for i, item in enumerate(content_ds):

    if i >= 5000:   # scan rộng hơn để tìm đủ doc
        break

    doc_id = item.get("id")
    content = item.get("content", "")

    if not content or not content.strip():
        continue

    meta = meta_dict.get(doc_id, {})

    # 🔥 FILTER DOANH NGHIỆP
    sectors = meta.get("legal_sectors", "").lower()

    if "doanh nghiệp" not in sectors:
        continue

    # 🔥 truncate tránh 413
    content = content[:1000]

    full_text = f"""
Tiêu đề: {meta.get("title", "")}
Lĩnh vực: {sectors}

{content}
"""

    docs.append(
        Document(
            page_content=full_text,
            metadata={
                "title": meta.get("title", ""),
                "url": meta.get("url", "")
            }
        )
    )

    if len(docs) >= MAX_DOCS:
        break

    if i % 200 == 0:
        print(f"📄 Scanned: {i} | Collected: {len(docs)}")

print(f"✅ Total docs: {len(docs)}")

# =========================
# Embedding
# =========================
print("🔄 Loading embedding model...")

embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)

# =========================
# Build Chroma DB
# =========================
print("💾 Building Chroma DB...")

Chroma.from_documents(
    docs,
    embedding,
    persist_directory="./chroma_db"
)

print("🎉 DONE: DB created successfully!")