# build_db.py  --  metadata-only Chroma DB (real schema from dataset card)
#
# 17 metadata columns: id, title, so_ky_hieu, ngay_ban_hanh, loai_van_ban,
# ngay_co_hieu_luc, ngay_het_hieu_luc, nguon_thu_thap, ngay_dang_cong_bao,
# nganh, linh_vuc, co_quan_ban_hanh, chuc_danh, nguoi_ky, pham_vi,
# thong_tin_ap_dung, tinh_trang_hieu_luc
#
# Filters:
#   1. nganh  matches a business-related sector
#   2. tinh_trang_hieu_luc != "Hết hiệu lực toàn bộ"  (skip expired laws)
#
# Citations: so_ky_hieu + nguon_thu_thap (no synthetic URLs -- safer
# against link rot, and is how Vietnamese legal docs are normally cited).

import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"]    = "False"
os.environ["HF_HUB_TIMEOUT"]      = "60"
os.environ["HF_HUB_MAX_RETRIES"]  = "10"

from datasets import load_dataset
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# =========================
# CONFIG
# =========================
MAX_DOCS     = 5000
INSERT_BATCH = 64
SKIP_EXPIRED = True

REPO_ID = "th1nhng0/vietnamese-legal-documents"

BUSINESS_SECTORS = (
    "Tài chính",
    "Công Thương",
    "Kế hoạch và Đầu tư",
    "Kế hoạch - Đầu tư",
    "Lao động - Thương binh và Xã hội",
    "Ngân hàng",
    "Công nghiệp",
    "Bảo Hiểm",
    "Thuỷ sản",
    "Bưu chính - Viễn thông",
    "Thương mại",
)

EXPIRED_STATUSES = (
    "Hết hiệu lực toàn bộ",
    "Hết hiệu lực",
)


def matches_business_sector(nganh: str) -> bool:
    if not nganh:
        return False
    n = nganh.lower()
    return any(s.lower() in n for s in BUSINESS_SECTORS)


def is_expired(status: str) -> bool:
    if not status:
        return False
    s = status.lower()
    return any(e.lower() in s for e in EXPIRED_STATUSES)


# Fields concatenated into the embedded text. Order matters -- title first.
TEXT_FIELDS = [
    "title",
    "loai_van_ban",        # Type: Quyết định / Nghị định / Thông tư
    "so_ky_hieu",          # Official number (the durable citation key)
    "nganh",               # Sector
    "linh_vuc",            # Legal field (often null but useful when present)
    "co_quan_ban_hanh",    # Issuing authority
    "chuc_danh",           # Signatory title
    "nguoi_ky",            # Signatory name
    "pham_vi",             # Geographical scope
    "thong_tin_ap_dung",   # Implementation note
    "ngay_ban_hanh",       # Issuance date
    "ngay_co_hieu_luc",    # Effective date
    "nguon_thu_thap",      # Collection source (e.g. Công báo) -- helps citation
    "tinh_trang_hieu_luc", # Effect status
]

VN_LABEL = {
    "title":               "Tiêu đề",
    "loai_van_ban":        "Loại văn bản",
    "so_ky_hieu":          "Số ký hiệu",
    "nganh":               "Ngành",
    "linh_vuc":            "Lĩnh vực",
    "co_quan_ban_hanh":    "Cơ quan ban hành",
    "chuc_danh":           "Chức danh",
    "nguoi_ky":            "Người ký",
    "pham_vi":             "Phạm vi",
    "thong_tin_ap_dung":   "Thông tin áp dụng",
    "ngay_ban_hanh":       "Ngày ban hành",
    "ngay_co_hieu_luc":    "Ngày có hiệu lực",
    "nguon_thu_thap":      "Nguồn thu thập",
    "tinh_trang_hieu_luc": "Tình trạng hiệu lực",
}


def build_text(row: dict) -> str:
    parts = []
    for f in TEXT_FIELDS:
        v = row.get(f)
        if v is None:
            continue
        v = str(v).strip()
        if not v:
            continue
        parts.append(f"{VN_LABEL.get(f, f)}: {v}")
    return "\n".join(parts)


# =========================
# 1. LOAD METADATA
# =========================
print("Loading metadata...")
meta_ds = load_dataset(REPO_ID, "metadata", split="data")
print(f"  metadata rows: {len(meta_ds)}")
print(f"  metadata fields: {list(meta_ds[0].keys())}")


# =========================
# 2. FILTER + BUILD DOCS
# =========================
print("Filtering...")
docs = []
n_business = n_expired = 0

for row in meta_ds:
    if len(docs) >= MAX_DOCS:
        break

    if not matches_business_sector(row.get("nganh") or ""):
        continue
    n_business += 1

    if SKIP_EXPIRED and is_expired(row.get("tinh_trang_hieu_luc") or ""):
        n_expired += 1
        continue

    text = build_text(row)
    if len(text) < 30:
        continue

    docs.append(Document(
        page_content=text,
        metadata={
            "id":                int(row.get("id") or 0),
            "title":             row.get("title", "") or "",
            "so_ky_hieu":        (row.get("so_ky_hieu") or "").strip(),
            "loai_van_ban":      row.get("loai_van_ban", "") or "",
            "nganh":             row.get("nganh", "") or "",
            "linh_vuc":          row.get("linh_vuc", "") or "",
            "co_quan_ban_hanh":  row.get("co_quan_ban_hanh", "") or "",
            "ngay_ban_hanh":     row.get("ngay_ban_hanh", "") or "",
            "nguon_thu_thap":    row.get("nguon_thu_thap", "") or "",
            "tinh_trang":        row.get("tinh_trang_hieu_luc", "") or "",
        },
    ))

print(f"  business rows scanned: {n_business}")
print(f"  expired rows skipped:  {n_expired}")
print(f"  kept documents:        {len(docs)}")


# =========================
# 3. EMBED + WRITE CHROMA
# =========================
print("Loading embedding model...")
embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "chroma_db")
print(f"Building Chroma DB at {DB_PATH} ...")

vs = None
for i in range(0, len(docs), INSERT_BATCH):
    chunk = docs[i:i + INSERT_BATCH]
    if vs is None:
        vs = Chroma.from_documents(chunk, embedding, persist_directory=DB_PATH)
    else:
        vs.add_documents(chunk)
    print(f"  indexed {min(i + INSERT_BATCH, len(docs))}/{len(docs)}")

print("DONE: metadata-only DB ready!")
