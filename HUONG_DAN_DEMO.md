# Hướng Dẫn Demo — RAG Legal Assistant

> Hệ thống trợ lý pháp lý thông minh sử dụng kỹ thuật **RAG (Retrieval-Augmented Generation)**, hỗ trợ tra cứu văn bản luật bằng ngôn ngữ tự nhiên.

---

## Mục Lục

1. [Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống)
2. [Yêu Cầu & Cài Đặt](#2-yêu-cầu--cài-đặt)
3. [Khởi Động Ứng Dụng](#3-khởi-động-ứng-dụng)
4. [Demo Đăng Nhập](#4-demo-đăng-nhập)
5. [Demo Hỏi Đáp Pháp Lý — Vai Trò Học Sinh](#5-demo-hỏi-đáp-pháp-lý--vai-trò-học-sinh)
6. [Demo Nhập Văn Bản Luật — Vai Trò Giáo Viên](#6-demo-nhập-văn-bản-luật--vai-trò-giáo-viên)
7. [Câu Hỏi Demo Gợi Ý](#7-câu-hỏi-demo-gợi-ý)
8. [Kiến Trúc Kỹ Thuật](#8-kiến-trúc-kỹ-thuật)
9. [Xử Lý Sự Cố](#9-xử-lý-sự-cố)

---

## 1. Tổng Quan Hệ Thống

### Stack Công Nghệ

| Thành phần | Công nghệ |
|---|---|
| Backend | Flask (Python 3.10+) |
| Vector Database | ChromaDB |
| Embedding Model | `BAAI/bge-small-en-v1.5` (HuggingFace) |
| LLM | Groq — `llama-3.1-8b-instant` |
| OCR tiếng Việt | VietOCR (`vgg_transformer`) |
| Chuyển PDF → ảnh | pdf2image + Poppler |
| Nhận diện dòng văn bản | OpenCV |
| Database | SQLite (`chat.db`) |

### Pipeline RAG (9 bước)

```
[1] Câu hỏi người dùng
       ↓
[2] Trích xuất chủ đề (topic extraction)
     → Nếu nhận ra chủ đề: bỏ qua bước viết lại → tiết kiệm 1 lần gọi API
       ↓
[3] Viết lại câu hỏi (query rewrite) — chỉ khi cần
       ↓
[4] Tìm kiếm ngữ nghĩa trong ChromaDB (topic-aware retrieval)
     → Ưu tiên khớp metadata, fallback sang semantic search
       ↓
[5] Xếp hạng lại tài liệu (rerank)
     → Tính điểm từ: từ khóa + cụm từ + số điều luật + nguồn KB
       ↓
[6] Phân loại câu hỏi (definition / condition / procedure / general)
       ↓
[7] Xây dựng prompt có căn cứ pháp lý + gọi Groq LLM (có retry tự động)
       ↓
[8] Làm sạch câu trả lời (loại bỏ chào hỏi, dòng trùng lặp)
       ↓
[9] Gắn trích dẫn điều luật (📖 Nguồn) + đường dẫn vbpl.vn
```

---

## 2. Yêu Cầu & Cài Đặt

### 2.1 Phần Mềm Cần Có

- **Python 3.10+**
- **Poppler** — đã có sẵn trong thư mục `poppler/` của dự án
- **Groq API key** — đăng ký miễn phí tại https://console.groq.com

### 2.2 Cài Đặt Thư Viện

```bash
pip install -r requirements.txt
```

### 2.3 Cấu Hình API Key

Tạo file `groqkey.txt` tại thư mục gốc dự án, dán API key vào:

```
gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 2.4 Kiểm Tra Nhanh

```bash
# Xác nhận Python đúng phiên bản
python --version

# Xác nhận Groq key hợp lệ
python -c "print(open('groqkey.txt').read().strip()[:8] + '...')"
```

---

## 3. Khởi Động Ứng Dụng

```bash
python app.py
```

Khi khởi động thành công, terminal hiển thị:

```
 * Running on http://127.0.0.1:5000
 * Debug mode: on
```

Mở trình duyệt và truy cập: **http://127.0.0.1:5000**

> **Lần đầu khởi động:** Hệ thống tự động tạo file `chat.db` và tạo 2 tài khoản mặc định. ChromaDB cũng sẽ tải embedding model từ HuggingFace (cần kết nối Internet).

---

## 4. Demo Đăng Nhập

### Tài Khoản Mặc Định

| Vai trò | Tên đăng nhập | Mật khẩu | Quyền |
|---|---|---|---|
| Học sinh | `testStudent1` | `123456P@ss` | Hỏi đáp RAG |
| Giáo viên | `teacher1` | `Teacher@123` | Hỏi đáp + Import văn bản luật |

### Các Bước Demo

1. Truy cập `http://127.0.0.1:5000`
2. Nhập tên đăng nhập và mật khẩu
3. Nhấn **Đăng nhập**
4. Hệ thống tự động điều hướng theo vai trò:
   - **Học sinh** → giao diện chat hỏi đáp
   - **Giáo viên** → giao diện chat + nút **"Import Law"** trên thanh điều hướng

---

## 5. Demo Hỏi Đáp Pháp Lý — Vai Trò Học Sinh

### Bước 1: Tạo Cuộc Trò Chuyện Mới

1. Đăng nhập bằng tài khoản học sinh (`testStudent1`)
2. Nhấn nút **"+ New Chat"** ở thanh bên trái
3. Một cuộc trò chuyện mới được tạo, sẵn sàng nhận câu hỏi

### Bước 2: Đặt Câu Hỏi

- Gõ câu hỏi vào ô nhập liệu ở cuối trang
- Nhấn **Enter** hoặc nút gửi
- Hệ thống xử lý theo 9 bước pipeline ở mục 1, sau đó hiển thị:
  - Câu trả lời phù hợp với loại câu hỏi (định nghĩa / điều kiện / thủ tục)
  - **📖 Nguồn:** trích dẫn điều luật cụ thể
  - **🔗 Link:** đường dẫn vbpl.vn (nếu có)

### Bước 3: Quản Lý Cuộc Trò Chuyện

| Thao tác | Cách thực hiện |
|---|---|
| Đổi tên chat | Nhấp đúp vào tiêu đề chat ở sidebar |
| Xóa chat | Nhấn icon thùng rác bên cạnh tên chat |
| Chuyển chat | Nhấn vào tên chat khác trong sidebar |
| Xem lại lịch sử | Nhấn vào chat có sẵn — tin nhắn được load từ SQLite |

---

## 6. Demo Nhập Văn Bản Luật — Vai Trò Giáo Viên

Tính năng này cho phép giáo viên tải lên file PDF văn bản luật. Hệ thống tự động OCR, phân đoạn theo điều khoản và lập chỉ mục vào ChromaDB.

### Bước 1: Truy Cập Trang Import

1. Đăng nhập bằng tài khoản giáo viên (`teacher1`)
2. Nhấn nút **"Import Law"** trên thanh điều hướng
3. Trang `/import` mở ra

### Bước 2: Điền Thông Tin và Upload

| Trường | Ví dụ | Mô tả |
|---|---|---|
| Số ký hiệu | `59/2020/QH14` | Số hiệu chính thức của văn bản |
| Loại văn bản | `Luật Doanh nghiệp` | Tên/loại văn bản pháp luật |
| Nguồn thu thập | `vbpl.vn` | Nguồn gốc tài liệu |
| File PDF | _(chọn file .pdf)_ | File PDF của văn bản luật |

Nhấn **"Import"** để bắt đầu.

### Bước 3: Quy Trình Xử Lý Nền

Sau khi nhấn Import, hệ thống chạy nền các bước sau:

```
1. Nhận file PDF → lưu tạm thời vào uploads_tmp/
2. Tải mô hình VietOCR (vgg_transformer)
3. Chuyển PDF → ảnh (DPI 250, từng batch 5 trang)
4. Tiền xử lý ảnh (tăng độ tương phản, làm nét)
5. Nhận dạng dòng văn bản bằng OpenCV
6. OCR từng dòng bằng VietOCR (beam search)
7. Phân đoạn theo "Điều X." trong văn bản
8. Tạo vector embedding và lưu vào ChromaDB
9. Tạo/cập nhật chat "Import new law" với thông báo kết quả
```

### Bước 4: Theo Dõi Tiến Trình

- Giao diện hiển thị **thanh tiến trình realtime** và trạng thái từng bước
- Trạng thái: `running` → `done` / `failed`
- Khi hoàn tất, kết quả xuất hiện trong chat **"Import new law"** ở sidebar

### Lưu Ý Quan Trọng

- Chỉ chấp nhận định dạng **PDF**
- Văn bản có **số ký hiệu trùng** sẽ bị bỏ qua tự động (không import lại)
- OCR chạy trên **CPU** mặc định — file 50 trang có thể mất 10–30 phút
- Để dùng **GPU** (nhanh hơn đáng kể), tạo file `.device_config` tại thư mục gốc:
  ```
  DEVICE=cuda
  ```

---

## 7. Câu Hỏi Demo Gợi Ý

Sử dụng các câu hỏi sau để minh họa từng tính năng trong buổi demo:

### Câu Hỏi Định Nghĩa (`definition`)

```
Quy định về công ty hợp danh là gì?
Quy định về doanh nghiệp tư nhân là gì?
Thành viên hợp danh là gì theo Luật Doanh nghiệp?
Khái niệm công ty TNHH một thành viên là gì?
```

### Câu Hỏi Điều Kiện (`condition`)

```
Điều kiện để thành lập công ty TNHH là gì?
Yêu cầu về vốn góp trong công ty cổ phần như thế nào?
Ai được phép là người đại diện theo pháp luật?
Tên doanh nghiệp bị cấm trong những trường hợp nào?
```

### Câu Hỏi Thủ Tục (`procedure`)

```
Thủ tục đăng ký thành lập doanh nghiệp tư nhân gồm các bước nào?
Hồ sơ đăng ký thành lập công ty TNHH cần những gì?
Quy trình thay đổi đăng ký kinh doanh như thế nào?
Nộp hồ sơ đăng ký doanh nghiệp ở đâu?
```

### Câu Hỏi Tổng Quát (`general`)

```
Công ty cổ phần khác công ty TNHH như thế nào?
Quyền và nghĩa vụ của thành viên góp vốn là gì?
Trách nhiệm của thành viên hợp danh trong công ty hợp danh?
```

> **Mẹo demo:** Bắt đầu bằng câu hỏi định nghĩa để thấy hệ thống khớp chính xác điều luật. Sau đó chuyển sang câu hỏi thủ tục để thấy định dạng danh sách bước. Cuối cùng nhập PDF mới để minh họa pipeline OCR.

---

## 8. Kiến Trúc Kỹ Thuật

### 8.1 Cấu Trúc Thư Mục

```
rag-legal-assistant-master/
├── app.py                    # Flask app — tất cả routes
├── engine/
│   ├── rag_engine.py         # Pipeline RAG 9 bước
│   └── import_law_engine.py  # Pipeline import: OCR → phân đoạn → embedding
├── database/
│   ├── database.py           # SQLite: users, chats, messages
│   ├── build_db.py           # Script tạo ChromaDB từ dữ liệu sẵn có
│   ├── build_db_from_pdf.py  # Script import PDF thủ công
│   └── build_db_from_dataset.py  # Import từ dataset Excel/CSV
├── evaluate/
│   └── evaluate_rag.py       # Đánh giá chất lượng RAG
├── templates/
│   ├── login.html            # Trang đăng nhập
│   ├── index.html            # Giao diện chat chính
│   └── import_law.html       # Giao diện import PDF
├── chroma_db/                # Vector database (ChromaDB)
├── uploads_tmp/              # Lưu PDF tạm thời khi import
├── chat.db                   # SQLite database
├── groqkey.txt               # Groq API key (không commit lên git)
└── requirements.txt          # Thư viện
```

### 8.2 API Endpoints

| Endpoint | Method | Mô tả | Quyền |
|---|---|---|---|
| `/` | GET | Trang chủ / đăng nhập | Public |
| `/login` | POST | Đăng nhập | Public |
| `/logout` | POST | Đăng xuất | Logged in |
| `/session_info` | GET | Thông tin phiên hiện tại | Public |
| `/get` | POST | Hỏi đáp RAG | Logged in |
| `/create_chat` | POST | Tạo chat mới | Logged in |
| `/list_chats` | GET | Danh sách chats | Logged in |
| `/get_chat_messages` | GET | Lịch sử tin nhắn | Logged in |
| `/rename_chat` | POST | Đổi tên chat | Logged in |
| `/delete_chat` | POST | Xóa chat | Logged in |
| `/import` | GET | Trang import văn bản luật | Teacher only |
| `/import_law` | POST | Upload PDF để import | Teacher only |
| `/import_status/<job_id>` | GET | Kiểm tra tiến trình import | Teacher only |

### 8.3 Phân Loại Câu Hỏi RAG

| Loại | Từ khóa nhận dạng | Định dạng trả lời |
|---|---|---|
| `procedure` | trình tự, thủ tục, quy trình, các bước, hồ sơ, nộp ở đâu | Danh sách bước 1, 2, 3… |
| `condition` | điều kiện, yêu cầu, cần có, phải có | Liệt kê điều kiện |
| `definition` | là gì, khái niệm, định nghĩa, quy định về | Ngắn gọn + căn cứ điều luật |
| `general` | _(các câu hỏi khác)_ | Tự do, nêu đủ căn cứ pháp lý |

### 8.4 Schema Database SQLite

```
users     (user_id, user_name, password, role[0=student, 1=teacher])
chats     (id, student_id, title, created_at, role[0=student, 1=teacher])
messages  (id, chat_id, role[user|assistant], text, timestamp)
```

Chat giáo viên và học sinh được tách biệt hoàn toàn theo cột `role`, ngay cả khi `user_id` trùng nhau.

---

## 9. Xử Lý Sự Cố

### Lỗi Không Kết Nối Groq

**Triệu chứng:** Câu trả lời trả về `❌ Lỗi hệ thống.`

**Kiểm tra:**
- File `groqkey.txt` tồn tại và chứa API key hợp lệ (bắt đầu bằng `gsk_`)
- Có kết nối Internet
- API key chưa hết hạn / hết quota tháng

**Lưu ý:** Groq có tự động retry 3 lần (5s / 10s / 15s) khi gặp lỗi rate limit.

---

### ChromaDB Trống — Không Tìm Thấy Kết Quả

**Triệu chứng:** `❌ Không tìm thấy thông tin liên quan trong cơ sở dữ liệu pháp luật.`

**Nguyên nhân:** Chưa có dữ liệu trong ChromaDB.

**Khắc phục:**
```bash
# Tạo ChromaDB từ dữ liệu có sẵn trong dự án
python database/build_db.py

# Hoặc import trực tiếp từ file PDF qua giao diện giáo viên (xem mục 6)
```

---

### OCR Chạy Chậm

**Nguyên nhân:** Mặc định dùng CPU. File 50 trang mất 10–30 phút.

**Tăng tốc với GPU (nếu có NVIDIA CUDA):**

Tạo file `.device_config` tại thư mục gốc:
```
DEVICE=cuda
```

---

### Lỗi Poppler Không Tìm Thấy

**Triệu chứng:** `PDFPageCountError` hoặc `Unable to get page count`

**Khắc phục:** Kiểm tra thư mục `poppler/Library/bin/` tồn tại và có các file binary (pdftoppm.exe, pdfinfo.exe…). Nếu thiếu, tải Poppler cho Windows từ https://github.com/oschwartz10612/poppler-windows/releases và giải nén vào đúng đường dẫn.

---

### Lỗi Đăng Nhập Thất Bại

**Triệu chứng:** Thông báo "Đăng nhập thất bại"

**Kiểm tra:**
- Tên đăng nhập và mật khẩu phân biệt chữ hoa/thường
- Tài khoản mặc định chỉ được tạo khi bảng `users` **trống hoàn toàn** (lần khởi động đầu tiên)
- Nếu đã có `chat.db` cũ từ schema cũ (bảng `students`/`teachers`), hệ thống tự migrate sang bảng `users`

---

## Ghi Chú Thêm

- Hệ thống hỗ trợ **đa phiên đồng thời** — nhiều người dùng có thể truy cập cùng lúc
- Dữ liệu chat được **lưu vĩnh viễn** trong `chat.db`; không mất khi restart
- ChromaDB **tích lũy dữ liệu** — import thêm văn bản mới không xóa dữ liệu cũ
- Mọi câu trả lời đều kèm **📖 trích dẫn điều luật** và **🔗 link nguồn** (nếu có)
- Hệ thống có cơ chế **retry tự động** khi Groq bị rate limit (3 lần, backoff 5s/10s/15s)
