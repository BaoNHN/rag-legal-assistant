import sys

# Every user-facing message in this app is Vietnamese. Attached to a real
# Windows console Python already emits UTF-8, but the moment stdout/stderr is
# redirected to a file or pipe (log file, service wrapper, `> out.log`), it
# falls back to the system ANSI codepage (cp1252 here) and any print()
# containing Vietnamese diacritics raises UnicodeEncodeError. Worst case this
# hits inside an except-block's own error-reporting print (e.g.
# run_evaluation's traceback.print_exc() in engine/evaluate_engine.py), which
# then dies silently instead of marking the job "failed" — the admin UI is
# left showing "running" forever. Reconfigured once here, first thing, since
# background threads (eval jobs, etc.) share this same process's stdout/stderr.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import os
import re
import secrets
import time
import uuid
import io
import warnings

# Silence two known-harmless, third-party UserWarnings (see
# evaluate/evaluate_rag.py's matching filter for the full explanation):
# gdown 4.4.0 (transitive dep of vietocr, imported below via
# engine.import_law_engine for PDF OCR) still uses deprecated pkg_resources;
# openpyxl warns on Excel conditional-formatting/data-validation features it
# doesn't parse when reading uploaded .xlsx files, without affecting the
# actual cell data read. Registered before the import_law_engine import below.
warnings.filterwarnings("ignore", message="pkg_resources is deprecated.*")
warnings.filterwarnings("ignore", message=".*Conditional Formatting extension.*")
warnings.filterwarnings("ignore", message=".*Data Validation extension.*")

from engine.rag_engine import (
    ask_rag,
    list_indexed_sources, delete_source, get_law_source_info, get_law_source_articles,
    list_scenario_sources, delete_scenario_source,
    list_dataset_sources, delete_dataset_source,
    set_source_publish_status,
)
from database.database import (
    init_db, get_conn,
    login_user,
    create_chat, get_all_chats,
    save_message, get_messages, count_user_messages,
    rename_chat, delete_chat,
    get_chat_title, NOTIFICATION_CHAT_TITLE, MAX_CHATS_PER_USER, MAX_MESSAGES_PER_CHAT,
    get_all_users, set_user_status, delete_user,
    change_user_password,
    create_keyword, get_all_keywords, get_active_keywords,
    get_active_priority_keywords, set_keyword_status, get_source_keywords,
    set_source_keywords, MAX_KEYWORD_NAME_LENGTH, get_tagged_articles,
    get_all_dataset_files, delete_dataset_file,
)
from engine.import_law_engine import run_import, get_job
from engine.import_scenario_engine import run_import_scenario, get_scenario_job
from engine.import_dataset_engine import run_import_dataset, get_dataset_job
from engine.evaluate_engine import (
    run_evaluation, get_eval_job, list_available_datasets,
    get_latest_eval_result, get_latest_vanilla_eval_result,
)
from engine.regression_test_engine import run_regression_tests, get_regression_job, get_latest_regression_results
from engine.import_account_engine import run_import_accounts, build_template_bytes

PASSWORD_RE = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_tmp")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Session secret — generated once and persisted to disk (gitignored), not
# hardcoded. A hardcoded/known secret lets anyone forge a signed session
# cookie for any user_id/role (including role=2 admin) without ever logging
# in, since Starlette's SessionMiddleware only signs the cookie with this key.
SESSION_SECRET_PATH = os.path.join(BASE_DIR, "session_secret.txt")
if not os.path.exists(SESSION_SECRET_PATH):
    with open(SESSION_SECRET_PATH, "w") as f:
        f.write(secrets.token_urlsafe(32))
with open(SESSION_SECRET_PATH) as f:
    SESSION_SECRET = f.read().strip()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=7200)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
# Cache-busting query param for /static/* asset URLs — bumps on every server
# restart so browsers can't keep serving a stale cached style.css/script.js
# after a deploy (this was the actual cause of a UI change appearing "not
# applied" a few times during development, not a real code bug).
templates.env.globals["static_version"] = str(int(time.time()))

init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────
def logged_in(request: Request) -> bool:
    return "user_id" in request.session

def is_teacher(request: Request) -> bool:
    # Admins have all Teacher-role functionality too.
    return request.session.get("role", 0) in (1, 2)

def is_admin(request: Request) -> bool:
    return request.session.get("role", 0) == 2

def _parse_keyword_ids(raw: str) -> list:
    """Parses a comma-joined string of keyword ids (as sent by the tag-picker
    FormData field) into a list of ints, silently dropping non-numeric parts."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Guests (not logged in) get index.html too — a single, ephemeral,
    # never-persisted chat capped at MAX_MESSAGES_PER_CHAT (see /get below).
    # They can still reach /login from the header to get a full account.
    return templates.TemplateResponse(request, "index.html", {
        "is_teacher": is_teacher(request),
        "is_admin":   is_admin(request),
        "is_guest":   not logged_in(request),
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if logged_in(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html")


@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "import_law.html")


@app.get("/manage_accounts", response_class=HTMLResponse)
async def manage_accounts_page(request: Request):
    if not logged_in(request) or not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "manage_accounts.html")


@app.get("/manage_law", response_class=HTMLResponse)
async def manage_law_page(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "manage_law.html")


@app.get("/import_account", response_class=HTMLResponse)
async def import_account_page(request: Request):
    if not logged_in(request) or not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "import_account.html")


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.post("/login")
async def login(request: Request):
    data     = await request.json()
    username = data.get("student_name") or data.get("username", "")
    password = data.get("password", "")

    user = login_user(username, password)
    if user and user.get("disabled"):
        return JSONResponse(
            {"status": "fail", "message": "Tài khoản đã bị vô hiệu hóa."},
            status_code=403
        )
    if user:
        request.session["user_id"]   = user["user_id"]
        request.session["user_name"] = user["user_name"]
        request.session["user_type"] = user["user_type"]
        request.session["role"]      = int(user["role"])
        return {"status": "success", "user_type": user["user_type"]}
    return JSONResponse({"status": "fail"}, status_code=401)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@app.get("/session_info")
async def session_info(request: Request):
    return {
        "user_id":   request.session.get("user_id"),
        "user_type": request.session.get("user_type", "student"),
        "role":      request.session.get("role", 0),
    }


# ── Chat API ──────────────────────────────────────────────────────────────────
# A question this long stops being a legal question and starts diluting both
# retrieval signals: keyword overlap (each matched word is a smaller share of
# the total) and embedding similarity (averaging many concepts drifts the
# vector away from one specific legal topic) — see engine.rag_engine's
# retrieve_docs(). 150 words comfortably covers even a detailed scenario
# description (the TLDN_001 example is ~35-40 words) while blocking someone
# pasting a full document into the box. Checked server-side (authoritative)
# and mirrored client-side in static/script.js for instant feedback.
MAX_QUESTION_WORDS = 150


@app.post("/get")
async def chatbot(request: Request):
    try:
        data       = await request.json()
        user_input = data.get("prompt")
        chat_id    = data.get("chat_id")
        voice      = data.get("voice", "formal")
        if voice not in ("formal", "casual"):
            voice = "formal"

        if not user_input:
            return {"status": "error", "text": "⚠️ Bạn chưa nhập câu hỏi."}

        word_count = len(user_input.split())
        if word_count > MAX_QUESTION_WORDS:
            return {
                "status": "error",
                "text": f"⚠️ Câu hỏi quá dài ({word_count} từ). Vui lòng rút gọn còn tối đa {MAX_QUESTION_WORDS} từ.",
            }

        # Guests: single ephemeral chat, nothing written to chat.db. Turn
        # count lives only in the signed session cookie (same cap as
        # logged-in users, MAX_MESSAGES_PER_CHAT), so it resets if the
        # session cookie is cleared/expires — that's fine, guests have no
        # history to lose either way.
        if not logged_in(request):
            guest_count = request.session.get("guest_msg_count", 0)
            if guest_count >= MAX_MESSAGES_PER_CHAT:
                return {
                    "status": "limit",
                    "text": f"⚠️ Bạn đã dùng hết {MAX_MESSAGES_PER_CHAT} câu hỏi miễn phí cho khách. "
                            f"Vui lòng đăng nhập để tiếp tục trò chuyện.",
                }
            response = ask_rag(user_input, voice=voice)
            request.session["guest_msg_count"] = guest_count + 1
            return {"status": "success", "text": response}

        if chat_id and get_chat_title(chat_id) == NOTIFICATION_CHAT_TITLE:
            return {"status": "error", "text": f"⚠️ Không thể gửi tin nhắn trong đoạn chat '{NOTIFICATION_CHAT_TITLE}'."}

        if chat_id and count_user_messages(chat_id) >= MAX_MESSAGES_PER_CHAT:
            return {
                "status": "limit",
                "text": f"⚠️ Đoạn chat này đã đạt giới hạn {MAX_MESSAGES_PER_CHAT} câu hỏi. "
                        f"Vui lòng tạo đoạn chat mới để tiếp tục.",
            }

        save_message(chat_id, "user", user_input)
        response = ask_rag(user_input, voice=voice)
        save_message(chat_id, "assistant", response)
        return {"status": "success", "text": response}
    except Exception as e:
        return {"status": "error", "text": str(e)}


# ── Chat management ───────────────────────────────────────────────────────────
@app.get("/list_chats")
async def api_list_chats(request: Request):
    if not logged_in(request):
        return []
    owner_role = 1 if is_teacher(request) else 0
    return get_all_chats(request.session["user_id"], owner_role)


@app.post("/create_chat")
async def api_create_chat(request: Request):
    if not logged_in(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    owner_role = 1 if is_teacher(request) else 0
    user_id    = request.session["user_id"]

    existing = [c for c in get_all_chats(user_id, owner_role) if c["title"] != NOTIFICATION_CHAT_TITLE]
    if len(existing) >= MAX_CHATS_PER_USER:
        return JSONResponse({
            "status":  "error",
            "message": f"Bạn đã đạt giới hạn {MAX_CHATS_PER_USER} đoạn chat. "
                       f"Vui lòng xoá một đoạn chat cũ trước khi tạo mới.",
            "chats":   existing,
        }, status_code=409)

    chat_id = create_chat(user_id, owner_role)
    return {"chat_id": chat_id}


@app.post("/rename_chat")
async def api_rename_chat(request: Request):
    data = await request.json()
    ok = rename_chat(data["chat_id"], data["title"])
    if not ok:
        return JSONResponse({
            "status":  "error",
            "message": f"Không thể đặt tên chat trùng với '{NOTIFICATION_CHAT_TITLE}'.",
        }, status_code=400)
    return {"status": "ok"}


@app.post("/delete_chat")
async def api_delete_chat(request: Request):
    if not logged_in(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    chat_id = data.get("chat_id")
    if not chat_id:
        return JSONResponse({"error": "Missing chat_id"}, status_code=400)
    delete_chat(chat_id)
    return {"status": "ok"}


@app.get("/get_chat_messages")
async def api_get_messages(chat_id: str = Query(...)):
    return get_messages(chat_id)


# ── Import law ────────────────────────────────────────────────────────────────
@app.post("/import_law")
async def import_law(
    request: Request,
    background_tasks: BackgroundTasks,
    so_ky_hieu: str = Form(""),
    loai_van_ban: str = Form(""),
    nguon_thu_thap: str = Form(""),
    primary_keyword_ids: str = Form(""),
    secondary_keyword_ids: str = Form(""),
    pdf_file: UploadFile = File(None),
):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    so_ky_hieu     = so_ky_hieu.strip()
    loai_van_ban   = loai_van_ban.strip()
    nguon_thu_thap = nguon_thu_thap.strip()

    if not pdf_file or not so_ky_hieu or not loai_van_ban or not nguon_thu_thap:
        return JSONResponse({
            "status": "error",
            "message": "Vui lòng tải lên file PDF hoặc DOCX và điền đầy đủ tất cả 3 trường."
        }, status_code=400)

    primary_ids   = _parse_keyword_ids(primary_keyword_ids)
    secondary_ids = _parse_keyword_ids(secondary_keyword_ids)
    if not primary_ids:
        return JSONResponse({
            "status": "error",
            "message": "Vui lòng chọn ít nhất 1 Từ khóa chính."
        }, status_code=400)

    filename_lower = (pdf_file.filename or "").lower()
    if not (filename_lower.endswith(".pdf") or filename_lower.endswith(".docx")):
        return JSONResponse({"status": "error", "message": "Chỉ chấp nhận file PDF hoặc DOCX."}, status_code=400)

    ext      = ".docx" if filename_lower.endswith(".docx") else ".pdf"
    job_id   = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")

    content = await pdf_file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    teacher_id = request.session["user_id"]
    importer   = request.session.get("user_name") or "admin1"

    background_tasks.add_task(
        run_import,
        job_id=job_id,
        file_path=file_path,
        so_ky_hieu=so_ky_hieu,
        loai_van_ban=loai_van_ban,
        nguon_thu_thap=nguon_thu_thap,
        student_id=teacher_id,
        db_conn_factory=get_conn,
        importer=importer,
        primary_keyword_ids=primary_ids,
        secondary_keyword_ids=secondary_ids,
    )

    return {"status": "ok", "job_id": job_id, "message": "Đã nhận file. Đang xử lý nền…"}


@app.get("/import_status/{job_id}")
async def import_status(job_id: str):
    job = get_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/list_active_keywords")
async def list_active_keywords_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return get_active_keywords()


@app.get("/list_active_priority_keywords")
async def list_active_priority_keywords_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return get_active_priority_keywords()


@app.get("/list_law_sources")
async def list_law_sources_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return list_indexed_sources()


@app.post("/delete_law_source")
async def delete_law_source_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data       = await request.json()
    so_ky_hieu = (data.get("so_ky_hieu") or "").strip()
    if not so_ky_hieu:
        return JSONResponse({"status": "error", "message": "Thiếu so_ky_hieu"}, status_code=400)

    deleted = delete_source(so_ky_hieu)
    if deleted == 0:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy văn bản '{so_ky_hieu}' trong ChromaDB."},
            status_code=404,
        )
    return {"status": "ok", "deleted": deleted}


@app.post("/set_source_publish_status")
async def set_source_publish_status_route(request: Request):
    # Admin-only, same as delete — publishing/unpublishing controls whether a
    # source can affect real answers, at least as consequential as deleting
    # it (see rag_engine.set_source_publish_status).
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data        = await request.json()
    source_type = (data.get("source_type") or "").strip()
    source_key  = (data.get("source_key") or "").strip()
    status      = (data.get("status") or "").strip()

    if source_type not in ("law", "dataset", "scenario"):
        return JSONResponse({"status": "error", "message": "source_type không hợp lệ."}, status_code=400)
    if status not in ("published", "pending"):
        return JSONResponse({"status": "error", "message": "status không hợp lệ."}, status_code=400)
    if not source_key:
        return JSONResponse({"status": "error", "message": "Thiếu source_key"}, status_code=400)

    updated = set_source_publish_status(source_type, source_key, status)
    if updated == 0:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy nguồn '{source_key}'."},
            status_code=404,
        )
    return {"status": "ok", "updated": updated}


# Law only — Dataset/Scenario are test/enrichment data (auto-tagged secondary
# keywords at import time, see import_dataset_engine.py/import_scenario_engine.py)
# and deliberately have no "Xem thông tin" view/edit capability in Manage Law.
_SOURCE_INFO_FNS = {
    "law": (get_law_source_info, "văn bản"),
}


@app.get("/get_source_info")
async def get_source_info_route(request: Request, source_type: str = Query(...), source_key: str = Query(...)):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    entry = _SOURCE_INFO_FNS.get(source_type)
    if not entry:
        return JSONResponse({"status": "error", "message": "source_type không hợp lệ."}, status_code=400)
    info_fn, label = entry

    info = info_fn(source_key)
    if not info:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy {label} '{source_key}' trong ChromaDB."},
            status_code=404,
        )
    info["source_type"] = source_type
    info["source_key"]  = source_key
    info["keywords"]    = get_source_keywords(source_type, source_key)
    return info



# "law_article" tags one specific Điều within a document (source_key encodes
# both: "<so_ky_hieu>#<article_number>") — added 2026-07-29 (user request) so
# primary/secondary tagging can discriminate between sibling articles of the
# same document, which document-wide tagging structurally cannot (a
# document-wide boost lifts every article in it equally). Not in
# _SOURCE_INFO_FNS since there's no separate "Xem thông tin" chunk-count view
# for a single article — it's only ever written via /update_source_keywords
# and read back via /get_article_keywords.
_UPDATABLE_SOURCE_TYPES = set(_SOURCE_INFO_FNS) | {"law_article"}


@app.get("/get_article_keywords")
async def get_article_keywords_route(request: Request, source_key: str = Query(...), article_number: str = Query(...)):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    article_number = article_number.strip()
    if not article_number:
        return JSONResponse({"status": "error", "message": "Thiếu article_number"}, status_code=400)
    return get_source_keywords("law_article", f"{source_key}#{article_number}")


@app.get("/list_tagged_articles")
async def list_tagged_articles_route(request: Request, source_key: str = Query(...)):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return {"articles": get_tagged_articles(source_key)}


@app.get("/list_source_articles")
async def list_source_articles_route(request: Request, source_key: str = Query(...)):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return {"articles": get_law_source_articles(source_key)}


@app.post("/update_source_keywords")
async def update_source_keywords_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data        = await request.json()
    source_type = (data.get("source_type") or "").strip()
    source_key  = (data.get("source_key") or "").strip()
    primary     = data.get("primary_keyword_ids") or []
    secondary   = data.get("secondary_keyword_ids") or []
    priority    = data.get("priority_keyword_ids") or []

    if source_type not in _UPDATABLE_SOURCE_TYPES:
        return JSONResponse({"status": "error", "message": "source_type không hợp lệ."}, status_code=400)
    if not source_key:
        return JSONResponse({"status": "error", "message": "Thiếu source_key"}, status_code=400)
    # Primary/secondary are no longer required at the whole-document level —
    # document-level authority is now expressed via priority instead
    # (2026-07-29, user request: primary/secondary reserved for article-level
    # tagging only, which is genuinely optional per document; penalty
    # retired the same day — see database.database's KEYWORD_STATUS_* comment).

    try:
        primary_ids   = [int(x) for x in primary]
        secondary_ids = [int(x) for x in secondary]
        priority_ids  = [int(x) for x in priority]
    except (TypeError, ValueError):
        return JSONResponse({"status": "error", "message": "ID từ khóa không hợp lệ."}, status_code=400)

    set_source_keywords(source_type, source_key, primary_ids, secondary_ids, priority_ids)
    return {"status": "ok"}


# ── Keyword management (admin adds/toggles, teacher+admin read for pickers) ────
@app.get("/list_keywords")
async def list_keywords_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return get_all_keywords()


@app.post("/add_keyword")
async def add_keyword_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data = await request.json()
    name = (data.get("name") or "").strip()
    kind = data.get("kind") or "scoring"  # scoring|oos|priority
    if not name:
        return JSONResponse({"status": "error", "message": "Vui lòng nhập tên từ khóa."}, status_code=400)
    if len(name) > MAX_KEYWORD_NAME_LENGTH:
        return JSONResponse(
            {"status": "error", "message": f"Tên từ khóa tối đa {MAX_KEYWORD_NAME_LENGTH} ký tự (hiện tại {len(name)})."},
            status_code=400,
        )
    if kind not in ("scoring", "oos", "priority"):
        return JSONResponse({"status": "error", "message": "Loại từ khóa không hợp lệ."}, status_code=400)

    keyword_id = create_keyword(name, kind=kind)
    if keyword_id is None:
        return JSONResponse({"status": "error", "message": f"Từ khóa '{name}' đã tồn tại."}, status_code=400)
    return {"status": "ok", "id": keyword_id}


@app.post("/toggle_keyword_status")
async def toggle_keyword_status_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data       = await request.json()
    keyword_id = data.get("id")
    status     = data.get("status")
    # 0/1 = scoring keyword active/disabled, 2/3 = out-of-scope keyword
    # active/disabled, 8/9 = priority active/disabled — see
    # database.database's KEYWORD_STATUS_* constants (4-7 retired 2026-07-29,
    # formerly penalty — intentionally rejected here, not just unused, so a
    # stray old request can't silently resurrect the mechanism).
    if keyword_id is None or status not in (0, 1, 2, 3, 8, 9):
        return JSONResponse({"status": "error", "message": "Thiếu tham số"}, status_code=400)

    set_keyword_status(keyword_id, status)
    return {"status": "ok"}


@app.get("/list_dataset_sources")
async def list_dataset_sources_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    # Uploaded .xlsx files are tracked in chat.db (dataset_file table). Only
    # their KB_Articles / KB_Articles_Updated / Legal_Update_2025 sheets get
    # embedded into ChromaDB (curated reference content); Dataset_*/Demo_*
    # sheets (the test Q&A pairs) never do — see engine/import_dataset_engine.py.
    # Merge chat.db tracking with real Chroma chunk counts for display.
    chroma  = {s["name"]: s for s in list_dataset_sources()}
    tracked = {f["filename"]: f for f in get_all_dataset_files()}

    result = []
    for name in set(chroma) | set(tracked):
        c  = chroma.get(name)
        db = tracked.get(name)
        result.append({
            "name": name,
            "chunk_count": c["chunk_count"] if c else 0,
            "importer": (db["importer"] if db else None) or (c["importer"] if c else None) or "—",
            "uploaded_at": db["uploaded_at"] if db else None,
        })
    result.sort(key=lambda x: x["name"])
    return result


@app.post("/delete_dataset_source")
async def delete_dataset_source_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data        = await request.json()
    source_name = (data.get("name") or "").strip()
    if not source_name:
        return JSONResponse({"status": "error", "message": "Thiếu name"}, status_code=400)

    tracked_names = {f["filename"] for f in get_all_dataset_files()}
    chroma_names  = {s["name"] for s in list_dataset_sources()}
    if source_name not in tracked_names and source_name not in chroma_names:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy dataset '{source_name}'."},
            status_code=404,
        )

    delete_dataset_file(source_name)
    delete_dataset_source(source_name)
    # Also remove the physical file so it stops showing up in the Quick/Full
    # Evaluation dataset dropdown (engine.evaluate_engine.list_available_datasets
    # scans Dataset/ directly from disk).
    file_path = os.path.join(BASE_DIR, "Dataset", source_name)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass
    return {"status": "ok"}


@app.get("/list_scenario_sources")
async def list_scenario_sources_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return list_scenario_sources()


@app.post("/delete_scenario_source")
async def delete_scenario_source_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data        = await request.json()
    source_name = (data.get("name") or "").strip()
    if not source_name:
        return JSONResponse({"status": "error", "message": "Thiếu name"}, status_code=400)

    deleted = delete_scenario_source(source_name)
    if deleted == 0:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy tình huống '{source_name}' trong ChromaDB."},
            status_code=404,
        )
    return {"status": "ok", "deleted": deleted}


# ── Regression tests (admin, Manage Law → "Kiểm thử hồi quy" tab) ───────────────
@app.post("/run_regression_tests")
async def run_regression_tests_route(request: Request, background_tasks: BackgroundTasks):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    job_id = str(uuid.uuid4())
    background_tasks.add_task(run_regression_tests, job_id=job_id)
    return {"status": "ok", "job_id": job_id}


@app.get("/regression_test_status/{job_id}")
async def regression_test_status_route(request: Request, job_id: str):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    job = get_regression_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/latest_regression_results")
async def latest_regression_results_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return get_latest_regression_results()


# ── Import scenario (DOCX case-study set) ───────────────────────────────────────
@app.post("/import_scenario")
async def import_scenario_route(
    request: Request,
    background_tasks: BackgroundTasks,
    docx_file: UploadFile = File(None),
):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not docx_file or not (docx_file.filename or "").lower().endswith(".docx"):
        return JSONResponse({"status": "error", "message": "Chỉ chấp nhận file .docx"}, status_code=400)

    job_id    = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{job_id}.docx")
    content   = await docx_file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    teacher_id = request.session["user_id"]
    importer   = request.session.get("user_name") or "admin1"

    background_tasks.add_task(
        run_import_scenario,
        job_id=job_id,
        file_path=file_path,
        student_id=teacher_id,
        original_filename=docx_file.filename,
        importer=importer,
    )
    return {"status": "ok", "job_id": job_id, "message": "Đã nhận file. Đang xử lý nền…"}


@app.get("/import_scenario_status/{job_id}")
async def import_scenario_status(job_id: str):
    job = get_scenario_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/download_scenario_example")
async def download_scenario_example_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return RedirectResponse("/", status_code=302)

    file_path = os.path.join(BASE_DIR, "Dataset", "example_scenario.docx")
    if not os.path.exists(file_path):
        return JSONResponse({"status": "error", "message": "Không tìm thấy file mẫu"}, status_code=404)

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=example_scenario.docx"},
    )


# ── Import dataset (Excel) ─────────────────────────────────────────────────────
@app.post("/import_dataset")
async def import_dataset_route(
    request: Request,
    background_tasks: BackgroundTasks,
    dataset_file: UploadFile = File(None),
):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not dataset_file:
        return JSONResponse(
            {"status": "error", "message": "Vui lòng tải lên file dataset (.xlsx)"},
            status_code=400,
        )

    fname = (dataset_file.filename or "").lower()
    if not fname.endswith(".xlsx"):
        return JSONResponse(
            {"status": "error", "message": "Chỉ chấp nhận file .xlsx"},
            status_code=400,
        )

    job_id    = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{job_id}.xlsx")
    content   = await dataset_file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    importer = request.session.get("user_name") or "admin1"

    background_tasks.add_task(
        run_import_dataset,
        job_id=job_id,
        file_path=file_path,
        original_filename=dataset_file.filename,
        importer=importer,
    )
    return {"status": "ok", "job_id": job_id, "message": "Đang xử lý dataset…"}


@app.get("/import_dataset_status/{job_id}")
async def import_dataset_status(job_id: str):
    job = get_dataset_job(job_id)
    return job if job else {"status": "unknown"}


# ── Evaluate RAG ──────────────────────────────────────────────────────────────
@app.get("/list_datasets")
async def list_datasets_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return list_available_datasets()


@app.get("/download_dataset_example")
async def download_dataset_example_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return RedirectResponse("/", status_code=302)

    file_path = os.path.join(BASE_DIR, "Dataset", "example_sheet.xlsx")
    if not os.path.exists(file_path):
        return JSONResponse({"status": "error", "message": "Không tìm thấy file mẫu"}, status_code=404)

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=example_sheet.xlsx"},
    )


@app.post("/evaluate")
async def evaluate_route(
    request: Request,
    background_tasks: BackgroundTasks,
):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data         = await request.json()
    mode         = data.get("mode", "auto")
    split        = data.get("split", "demo")
    dataset_file = data.get("dataset_file") or None
    pipeline     = data.get("pipeline", "custom")

    if mode not in ("auto", "llm"):
        mode = "auto"
    if split not in ("demo", "all", "test", "random"):
        split = "demo"
    if pipeline not in ("custom", "vanilla"):
        pipeline = "custom"

    job_id = str(uuid.uuid4())
    background_tasks.add_task(run_evaluation, job_id=job_id, mode=mode, split=split,
                              dataset_file=dataset_file, pipeline=pipeline)
    return {"status": "ok", "job_id": job_id}


@app.get("/evaluate_status/{job_id}")
async def evaluate_status_route(job_id: str):
    job = get_eval_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/latest_eval_result")
async def latest_eval_result_route(request: Request, dataset_file: str = None):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    result = get_latest_eval_result(dataset_file=dataset_file)
    return result if result else {"status": "empty"}


@app.get("/latest_vanilla_eval_result")
async def latest_vanilla_eval_result_route(request: Request, dataset_file: str = None):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    result = get_latest_vanilla_eval_result(dataset_file=dataset_file)
    return result if result else {"status": "empty"}


@app.get("/download_eval_result/{filename}")
async def download_eval_result_route(request: Request, filename: str):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    safe_name = os.path.basename(filename)
    if not re.match(r'^eval_results_.*\.xlsx$', safe_name):
        return JSONResponse({"status": "error", "message": "Tên file không hợp lệ"}, status_code=400)

    file_path = os.path.join(BASE_DIR, safe_name)
    if not os.path.exists(file_path):
        return JSONResponse({"status": "error", "message": "Không tìm thấy file kết quả"}, status_code=404)

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={safe_name}"},
    )


@app.get("/download_low_score_result/{filename}")
async def download_low_score_result_route(request: Request, filename: str):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    safe_name = os.path.basename(filename)
    if not re.match(r'^eval_low_score_.*\.xlsx$', safe_name):
        return JSONResponse({"status": "error", "message": "Tên file không hợp lệ"}, status_code=400)

    file_path = os.path.join(BASE_DIR, safe_name)
    if not os.path.exists(file_path):
        return JSONResponse({"status": "error", "message": "Không tìm thấy file kết quả"}, status_code=404)

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={safe_name}"},
    )


@app.get("/download_connection_errors/{filename}")
async def download_connection_errors_route(request: Request, filename: str):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    safe_name = os.path.basename(filename)
    if not re.match(r'^eval_connection_errors_.*\.xlsx$', safe_name):
        return JSONResponse({"status": "error", "message": "Tên file không hợp lệ"}, status_code=400)

    file_path = os.path.join(BASE_DIR, safe_name)
    if not os.path.exists(file_path):
        return JSONResponse({"status": "error", "message": "Không tìm thấy file kết quả"}, status_code=404)

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={safe_name}"},
    )


# ── Account management (admin) ─────────────────────────────────────────────────
@app.get("/list_users")
async def list_users_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return get_all_users()


@app.post("/toggle_user_status")
async def toggle_user_status_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data    = await request.json()
    user_id = data.get("user_id")
    status  = data.get("status")
    if user_id is None or status not in (0, 1):
        return JSONResponse({"status": "error", "message": "Thiếu tham số"}, status_code=400)
    if int(user_id) == request.session.get("user_id"):
        return JSONResponse({"status": "error", "message": "Không thể tự vô hiệu hóa tài khoản của chính mình."}, status_code=400)

    set_user_status(user_id, status)
    return {"status": "ok"}


@app.post("/delete_user")
async def delete_user_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data    = await request.json()
    user_id = data.get("user_id")
    if user_id is None:
        return JSONResponse({"status": "error", "message": "Thiếu user_id"}, status_code=400)
    if int(user_id) == request.session.get("user_id"):
        return JSONResponse({"status": "error", "message": "Không thể tự xoá tài khoản của chính mình."}, status_code=400)

    delete_user(user_id)
    return {"status": "ok"}


@app.post("/import_account")
async def import_account_route(
    request: Request,
    account_file: UploadFile = File(None),
):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not account_file or not (account_file.filename or "").lower().endswith(".xlsx"):
        return JSONResponse({"status": "error", "message": "Chỉ chấp nhận file .xlsx"}, status_code=400)

    job_id    = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{job_id}.xlsx")
    content   = await account_file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    try:
        report = run_import_accounts(file_path)
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass

    status_code = 200 if report.get("status") == "ok" else 400
    return JSONResponse(report, status_code=status_code)


@app.get("/download_account_template")
async def download_account_template(request: Request):
    if not logged_in(request) or not is_admin(request):
        return RedirectResponse("/", status_code=302)

    content = build_template_bytes()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=account_import_template.xlsx"},
    )


# ── Change password (public, from login page) ──────────────────────────────────
@app.post("/change_password")
async def change_password_route(request: Request):
    data             = await request.json()
    username         = (data.get("username") or "").strip()
    old_password     = data.get("old_password") or ""
    new_password     = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not username or not old_password or not new_password or not confirm_password:
        return JSONResponse({"status": "error", "message": "Vui lòng điền đầy đủ tất cả các trường."}, status_code=400)
    if new_password != confirm_password:
        return JSONResponse({"status": "error", "message": "Mật khẩu mới và xác nhận mật khẩu không khớp."}, status_code=400)
    if not PASSWORD_RE.match(new_password):
        return JSONResponse({
            "status": "error",
            "message": "Mật khẩu mới cần tối thiểu 8 ký tự, gồm chữ hoa, chữ thường, số và ký tự đặc biệt."
        }, status_code=400)

    ok, reason = change_user_password(username, old_password, new_password)
    if not ok:
        return JSONResponse({"status": "error", "message": reason}, status_code=400)
    return {"status": "ok", "message": "Đổi mật khẩu thành công."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
