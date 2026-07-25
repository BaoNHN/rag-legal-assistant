from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import os
import re
import uuid
import io

from engine.rag_engine import (
    ask_rag,
    list_indexed_sources, delete_source,
    list_dataset_sources, delete_dataset_source,
    list_scenario_sources, delete_scenario_source,
)
from database.database import (
    init_db, get_conn,
    login_user,
    create_chat, get_all_chats,
    save_message, get_messages,
    rename_chat, delete_chat,
    get_all_users, set_user_status, delete_user,
    change_user_password,
)
from engine.import_law_engine import run_import, get_job
from engine.import_scenario_engine import run_import_scenario, get_scenario_job
from engine.import_dataset_engine import run_import_dataset, get_dataset_job
from engine.evaluate_engine import run_evaluation, get_eval_job, list_available_datasets, get_latest_eval_result
from engine.regression_test_engine import run_regression_tests, get_regression_job, get_latest_regression_results
from engine.import_account_engine import run_import_accounts, build_template_bytes

PASSWORD_RE = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$')

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="secret_key", max_age=7200)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_tmp")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────
def logged_in(request: Request) -> bool:
    return "user_id" in request.session

def is_teacher(request: Request) -> bool:
    # Admins have all Teacher-role functionality too.
    return request.session.get("role", 0) in (1, 2)

def is_admin(request: Request) -> bool:
    return request.session.get("role", 0) == 2


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not logged_in(request):
        return templates.TemplateResponse(request, "login.html")
    return templates.TemplateResponse(request, "index.html", {
        "is_teacher": is_teacher(request),
        "is_admin":   is_admin(request),
    })


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
    if not logged_in(request) or not is_admin(request):
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
@app.post("/get")
async def chatbot(request: Request):
    try:
        data       = await request.json()
        user_input = data.get("prompt")
        chat_id    = data.get("chat_id")

        if not user_input:
            return {"status": "error", "text": "⚠️ Bạn chưa nhập câu hỏi."}

        save_message(chat_id, "user", user_input)
        response = ask_rag(user_input)
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
    chat_id = create_chat(request.session["user_id"], owner_role)
    return {"chat_id": chat_id}


@app.post("/rename_chat")
async def api_rename_chat(request: Request):
    data = await request.json()
    rename_chat(data["chat_id"], data["title"])
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
    )

    return {"status": "ok", "job_id": job_id, "message": "Đã nhận file. Đang xử lý nền…"}


@app.get("/import_status/{job_id}")
async def import_status(job_id: str):
    job = get_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/list_law_sources")
async def list_law_sources_route(request: Request):
    if not logged_in(request) or not is_admin(request):
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


@app.get("/list_dataset_sources")
async def list_dataset_sources_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return list_dataset_sources()


@app.post("/delete_dataset_source")
async def delete_dataset_source_route(request: Request):
    if not logged_in(request) or not is_admin(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    data        = await request.json()
    source_name = (data.get("name") or "").strip()
    if not source_name:
        return JSONResponse({"status": "error", "message": "Thiếu name"}, status_code=400)

    deleted = delete_dataset_source(source_name)
    if deleted == 0:
        return JSONResponse(
            {"status": "error", "message": f"Không tìm thấy dataset '{source_name}' trong ChromaDB."},
            status_code=404,
        )
    return {"status": "ok", "deleted": deleted}


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

    if mode not in ("auto", "llm"):
        mode = "auto"
    if split not in ("demo", "all", "test"):
        split = "demo"

    job_id = str(uuid.uuid4())
    background_tasks.add_task(run_evaluation, job_id=job_id, mode=mode, split=split, dataset_file=dataset_file)
    return {"status": "ok", "job_id": job_id}


@app.get("/evaluate_status/{job_id}")
async def evaluate_status_route(job_id: str):
    job = get_eval_job(job_id)
    return job if job else {"status": "unknown"}


@app.get("/latest_eval_result")
async def latest_eval_result_route(request: Request):
    if not logged_in(request) or not is_teacher(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    result = get_latest_eval_result()
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
