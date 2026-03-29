# build_db.py

import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

import pandas as pd
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter

# =========================
# 📂 PATH
# =========================

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

CONTENT_PATH = os.path.join(BASE_DIR, "data", "data-00000-of-00011.parquet")
META_PATH = os.path.join(BASE_DIR, "data", "metadata.parquet")
DB_PATH = os.path.join(BASE_DIR, "chroma_db")

# =========================
# LOAD DATA
# =========================

print("🔄 Loading parquet...")

df_content = pd.read_parquet(CONTENT_PATH)

if os.path.exists(META_PATH):
    df_meta = pd.read_parquet(META_PATH)
    df = df_content.merge(df_meta, on="id", how="left")
else:
    df = df_content

print("✅ Data loaded:", df.shape)

# =========================
# ⚙️ CONFIG
# =========================

MAX_DOCS = 1000 # scan rộng hơn để tìm đủ doc
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP
)

docs = []

print("🔄 Processing + chunking...")

for i, row in df.iterrows():

    if len(docs) >= MAX_DOCS:
        break

    content = str(row.get("content", "")).strip()

    if not content:
        continue

    # 🎯 FILTER doanh nghiệp
    sectors = str(row.get("legal_sectors", "")).lower()
    if "doanh nghiệp" not in sectors:
        continue

    # 🧠 CHUNK (KHÔNG truncate nữa)
    chunks = splitter.split_text(content)

    for chunk in chunks:
        docs.append(
            Document(
                page_content=chunk,
                metadata={
                    "id": row.get("id"),
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "legal_type": row.get("legal_type", ""),
                    "source": row.get("document_number", "")
                }
            )
        )

    if i % 200 == 0:
        print(f"📄 Processed rows: {i} | Docs: {len(docs)}")

print(f"✅ Total docs: {len(docs)}")

# =========================
# 🧠 EMBEDDING
# =========================

print("🔄 Loading embedding model...")

embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)

# =========================
# 💾 BUILD CHROMA
# =========================

print("💾 Building Chroma DB...")

vectorstore = Chroma.from_documents(
    docs,
    embedding,
    persist_directory=DB_PATH
)

print("🎉 DONE: DB ready!")