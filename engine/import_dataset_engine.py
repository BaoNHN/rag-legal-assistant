# import_dataset_engine.py
# Background worker: register an uploaded Excel dataset file as a test/
# evaluation dataset.
#
# As of 2026-07-28 this NO LONGER embeds anything into ChromaDB. A direct
# inspection of the live vectorstore confirmed that content from this
# pipeline's Dataset_*/Demo_* Q&A sheets was retrievable during real question
# answering — the exact question/answer pair used to test the system could be
# retrieved back as "context" for that same question, a genuine data-leakage
# risk. Rather than special-casing which sheets are "safe" to index, dataset
# uploads are now treated purely as evaluation fixtures: the file is saved to
# Dataset/ and tracked in chat.db (see database.database.dataset_file) for
# engine.evaluate_engine's Quick/Full Evaluation to read directly from disk.
# It never touches ChromaDB and has zero effect on how real questions are
# answered.

import os
import shutil
import threading
from datetime import datetime
import pandas as pd

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(BASE_DIR, "Dataset")
os.makedirs(DATASET_DIR, exist_ok=True)

# ── Job registry ─────────────────────────────────────────────────────────────
_jobs: dict = {}
_lock = threading.Lock()


def get_dataset_job(job_id: str) -> dict:
    with _lock:
        return _jobs.get(job_id, {})


def _set(job_id: str, **kwargs):
    with _lock:
        if job_id not in _jobs:
            _jobs[job_id] = {}
        _jobs[job_id].update(kwargs)


def _persist_uploaded_dataset(tmp_path: str, original_filename: str = None) -> str:
    """
    Moves the uploaded dataset .xlsx into DATASET_DIR (Dataset/) so it becomes
    available for RAG evaluation (see evaluate_engine.list_available_datasets).
    Never overwrites an existing file — appends a timestamp on name clash.
    Returns the saved filename, or None if the move failed.
    """
    base_name = os.path.basename(original_filename) if original_filename else "imported_dataset.xlsx"
    if not base_name.lower().endswith(".xlsx"):
        base_name += ".xlsx"
    stem, ext = os.path.splitext(base_name)

    dest_name = base_name
    dest_path = os.path.join(DATASET_DIR, dest_name)
    if os.path.exists(dest_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_name = f"{stem}_{ts}{ext}"
        dest_path = os.path.join(DATASET_DIR, dest_name)

    try:
        shutil.move(tmp_path, dest_path)
        return dest_name
    except Exception:
        return None


# ── Main background task ──────────────────────────────────────────────────────
def run_import_dataset(job_id: str, file_path: str, original_filename: str = None, importer: str = "admin1"):
    """
    Validates an uploaded Excel workbook has at least one Demo_*/Dataset_*
    sheet, saves it to Dataset/, and registers it in the dataset_file
    tracking table. Does not embed anything into ChromaDB — see module
    docstring.
    """
    _set(job_id, status="running", message="Đang kiểm tra file dataset…")

    try:
        with pd.ExcelFile(file_path) as xl:
            sheets = xl.sheet_names

        demo_sheets    = [s for s in sheets if s.startswith("Demo_")]
        dataset_sheets = [s for s in sheets if s.startswith("Dataset_")]

        if not demo_sheets and not dataset_sheets:
            _set(job_id, status="failed",
                 message="❌ Không tìm thấy sheet Demo_*/Dataset_* nào trong file — "
                         "không dùng được cho Quick/Full Evaluation.")
            return

        report = []
        if demo_sheets:
            report.append(f"Demo: {', '.join(demo_sheets)}")
        if dataset_sheets:
            report.append(f"Dataset: {', '.join(dataset_sheets)}")

        saved_name = _persist_uploaded_dataset(file_path, original_filename)
        if not saved_name:
            _set(job_id, status="failed", message="❌ Lỗi khi lưu file vào thư mục Dataset/.")
            return

        from database.database import register_dataset_file
        register_dataset_file(saved_name, importer)

        result_msg = (
            "✅ Đã lưu làm bộ dữ liệu kiểm thử/đánh giá (không nạp vào ChromaDB, "
            "không ảnh hưởng câu trả lời thật).\n"
            + "\n".join(report)
            + "\nSẵn sàng dùng cho Quick/Full Evaluation."
        )
        _set(job_id, status="done", message=result_msg, saved_dataset_file=saved_name)

    except Exception as e:
        import traceback
        traceback.print_exc()
        _set(job_id, status="failed", message=f"❌ Lỗi: {e}")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
