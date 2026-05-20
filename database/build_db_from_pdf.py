# build_db_from_pdf.py  —  VietOCR version (accuracy-optimized)
# - Suppresses batch_first warning
# - Beamsearch ON for better accuracy
# - Higher DPI default
# - Better line detection (padding + min-height tuned)
# - Image preprocessing (contrast + sharpness boost)
# - Full article text, multi-page articles handled correctly
#
# USAGE:
#   conda activate rag_env
#   cd D:\hoc\project\rag-legal-assistant-master
#   python database/build_db_from_pdf.py

import os
import re
import shutil
import warnings

# ── Suppress the batch_first / nested_tensor warning from PyTorch ──
warnings.filterwarnings(
    "ignore",
    message=".*enable_nested_tensor.*",
    category=UserWarning
)
warnings.filterwarnings(
    "ignore",
    message=".*batch_first.*",
    category=UserWarning
)

from PIL import Image, ImageEnhance, ImageFilter
from pdf2image import convert_from_path
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# =========================
# CONFIG
# =========================
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_PATH     = os.path.join(BASE_DIR, "luat-doanh-nghiep-2020_20_281_29.pdf")
DB_PATH      = os.path.join(BASE_DIR, "chroma_db")
POPPLER_PATH = os.path.join(BASE_DIR, "poppler", "Library", "bin")
RAW_TXT_PATH = os.path.join(BASE_DIR, "ocr_raw_output.txt")
DEVICE_CFG   = os.path.join(BASE_DIR, ".device_config")

DPI          = 250   # increased from 200 → better OCR accuracy
BATCH_SIZE   = 5     # pages per batch (reduce to 2 if RAM < 8GB)
INSERT_BATCH = 32    # chromadb insert batch

# ── Auto-detect GPU from install.bat config ──
# Reads .device_config written by install.bat
# Falls back to torch auto-detection if file missing
def detect_device():
    # Check .device_config written by install.bat
    if os.path.exists(DEVICE_CFG):
        with open(DEVICE_CFG, "r") as f:
            content = f.read().strip()
        if "cuda" in content:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                print(f"  GPU detected: {gpu_name}")
                return "cuda"
            else:
                print("  Warning: .device_config says cuda but no GPU found → using cpu")
                return "cpu"
    # Fallback: auto-detect
    import torch
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        print(f"  GPU auto-detected: {gpu_name}")
        return "cuda"
    print("  No GPU found → using cpu")
    return "cpu"

# =========================
# STEP 1: LOAD VIETOCR
# =========================
print("Loading VietOCR model (downloads ~300MB first time)...")
print("Detecting device...")

DEVICE = detect_device()
print(f"  Using device: {DEVICE.upper()}")
print()

from vietocr.tool.predictor import Predictor
from vietocr.tool.config import Cfg

config = Cfg.load_config_from_name('vgg_transformer')
config['device'] = DEVICE

# ── Key accuracy improvements ──
# beamsearch=True → considers multiple candidate sequences, picks best one
# Much more accurate than greedy (False)
# GPU: ~15 min for 141 pages | CPU: ~2 hours
config['predictor']['beamsearch'] = True

detector = Predictor(config)
print(f"VietOCR loaded! (device={DEVICE.upper()}, beamsearch=True)\n")

# =========================
# STEP 2: IMAGE PREPROCESSING
# ─────────────────────────────
# Enhance image before OCR:
# 1. Convert to grayscale
# 2. Boost contrast  → makes text darker, background whiter
# 3. Boost sharpness → clearer character edges
# This significantly improves accuracy on scanned documents
# =========================
def preprocess_image(pil_image):
    # Convert to grayscale
    img = pil_image.convert('L')
    # Boost contrast (1.0=original, 2.0=double contrast)
    img = ImageEnhance.Contrast(img).enhance(1.8)
    # Boost sharpness
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    # Convert back to RGB (VietOCR expects RGB)
    img = img.convert('RGB')
    return img

# =========================
# STEP 3: OCR FUNCTION
# =========================
def ocr_page(pil_image):
    """
    Preprocess page image, split into text lines,
    OCR each line with VietOCR, join results.
    """
    import numpy as np

    # Preprocess for better accuracy
    pil_image = preprocess_image(pil_image)

    # Work on grayscale for line detection
    gray = pil_image.convert('L')
    arr  = np.array(gray)

    # Find rows with text (dark pixels on white background)
    # threshold 200 = anything darker than light gray counts as text
    row_darkness = (arr < 200).sum(axis=1)

    in_line     = False
    line_starts = []
    line_ends   = []

    for i, dark in enumerate(row_darkness):
        if dark > 5 and not in_line:
            in_line = True
            # Add padding above line for ascenders (é, ắ, etc.)
            line_starts.append(max(0, i - 4))
        elif dark <= 2 and in_line:
            in_line = False
            # Add padding below line for descenders (g, p, y, etc.)
            line_ends.append(min(arr.shape[0], i + 4))

    if in_line:
        line_ends.append(arr.shape[0])

    # No lines detected → process whole page at once
    if not line_starts:
        return detector.predict(pil_image)

    width      = pil_image.width
    lines_text = []

    for start, end in zip(line_starts, line_ends):
        height = end - start
        # Skip tiny fragments (noise, decorations)
        # min 15px — Vietnamese diacritics need height
        if height < 15:
            continue
        line_img = pil_image.crop((0, start, width, end))
        try:
            text = detector.predict(line_img)
            if text and text.strip():
                lines_text.append(text.strip())
        except Exception:
            pass

    return "\n".join(lines_text)

# =========================
# STEP 4: OCR ALL PAGES
# =========================
from pypdf import PdfReader
total_pages = len(PdfReader(PDF_PATH).pages)

est = "~15 min" if DEVICE == "cuda" else "~2 hours"
print(f"PDF      : {PDF_PATH}")
print(f"Pages    : {total_pages}")
print(f"DPI      : {DPI}  |  Batch: {BATCH_SIZE}  |  Device: {DEVICE.upper()}")
print(f"Estimated time: {est}")
print("Starting OCR...\n")

all_text_by_page = {}

for batch_start in range(1, total_pages + 1, BATCH_SIZE):
    batch_end = min(batch_start + BATCH_SIZE - 1, total_pages)
    print(f"  Pages {batch_start:3d}–{batch_end:3d} / {total_pages} ...", end=" ", flush=True)

    pages = convert_from_path(
        PDF_PATH,
        dpi=DPI,
        first_page=batch_start,
        last_page=batch_end,
        fmt='jpeg',
        poppler_path=POPPLER_PATH
    )

    for i, page_img in enumerate(pages):
        page_num = batch_start + i
        text = ocr_page(page_img)
        all_text_by_page[page_num] = text.strip()
        print(".", end="", flush=True)

    del pages
    print(" done")

print(f"\nOCR complete — {len(all_text_by_page)} pages processed.")

# Save raw OCR text for inspection
with open(RAW_TXT_PATH, "w", encoding="utf-8") as f:
    for p in sorted(all_text_by_page):
        f.write(f"\n\n=== TRANG {p} ===\n")
        f.write(all_text_by_page[p])

print(f"Raw OCR saved → {RAW_TXT_PATH}")
print("  ↳ Open this file to check quality before continuing!\n")

# =========================
# STEP 5: JOIN ALL PAGES → SEGMENT BY ARTICLE
# ─────────────────────────────────────────────
# Join ALL pages first so articles spanning
# multiple pages are captured as one complete chunk
# =========================
print("Segmenting by legal article (Điều)...")

full_text = "\n".join(all_text_by_page[p] for p in sorted(all_text_by_page))

# Clean up whitespace
full_text = re.sub(r'\n{3,}', '\n\n', full_text)
full_text = re.sub(r'[ \t]+', ' ', full_text).strip()

# Split on "Điều X." — lookahead keeps delimiter at start of each segment
pattern    = r'(?=Điều\s+\d+[a-z]?[\.\s])'
raw_splits = re.split(pattern, full_text)

segments_raw = [s.strip() for s in raw_splits if len(s.strip()) > 50]
print(f"Found {len(segments_raw)} legal article segments.")

# Fallback: fixed-size chunks if article detection fails
if len(segments_raw) < 10:
    print("Warning: few articles detected — using fixed-size chunking as fallback.")
    chunk_size   = 3000
    overlap      = 300
    segments_raw = []
    i = 0
    while i < len(full_text):
        segments_raw.append(full_text[i:i + chunk_size])
        i += chunk_size - overlap
    print(f"  Created {len(segments_raw)} chunks.")

# =========================
# STEP 6: BUILD DOCUMENTS
# =========================
print("Building document objects...")

docs = []
for i, seg in enumerate(segments_raw):
    text = seg.strip()
    if len(text) < 30:
        continue

    article_match = re.match(r'Điều\s+(\d+[a-z]?)[\.\s]', text)
    article_num   = article_match.group(1) if article_match else str(i + 1)

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    title = lines[0][:120] if lines else f"Điều {article_num}"

    docs.append(Document(
        page_content=text,          # full text, no limit
        metadata={
            "so_ky_hieu":     "59/2020/QH14",
            "loai_van_ban":   "Luật",
            "title":          title,
            "article_number": article_num,
            "nguon_thu_thap": "Luật Doanh nghiệp 2020",
            "char_count":     len(text),
            "segment_index":  i,
        }
    ))

print(f"Built {len(docs)} document segments.")
if docs:
    avg_len = sum(len(d.page_content) for d in docs) // len(docs)
    max_len = max(len(d.page_content) for d in docs)
    min_len = min(len(d.page_content) for d in docs)
    print(f"  Average chars : {avg_len:,}")
    print(f"  Longest       : {max_len:,} chars")
    print(f"  Shortest      : {min_len:,} chars")

# =========================
# STEP 7: EMBED + CHROMADB
# =========================
print("\nLoading embedding model (BAAI/bge-small-en-v1.5)...")
embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

print(f"Building ChromaDB at: {DB_PATH}")

if os.path.exists(DB_PATH):
    print("  Clearing old ChromaDB...")
    shutil.rmtree(DB_PATH)

vs = None
for i in range(0, len(docs), INSERT_BATCH):
    chunk = docs[i:i + INSERT_BATCH]
    if vs is None:
        vs = Chroma.from_documents(chunk, embedding, persist_directory=DB_PATH)
    else:
        vs.add_documents(chunk)
    print(f"  Indexed {min(i + INSERT_BATCH, len(docs))}/{len(docs)} segments")

print("\n✅ DONE!")
print(f"   Articles indexed  : {len(docs)}")
print(f"   ChromaDB path     : {DB_PATH}")
print(f"   Raw OCR text      : {RAW_TXT_PATH}")
print("\nNext step: python database/build_db_from_txt.py  (to rebuild DB anytime)")
print("Or run  : python app.py")