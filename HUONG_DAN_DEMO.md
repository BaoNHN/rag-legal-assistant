# Hướng Dẫn Demo — RAG Legal Assistant

> Hệ thống trợ lý pháp lý thông minh sử dụng kỹ thuật **RAG (Retrieval-Augmented Generation)**, hỗ trợ tra cứu văn bản luật bằng ngôn ngữ tự nhiên.

---

## Mục Lục

1. [Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống)
2. [Yêu Cầu & Cài Đặt](#2-yêu-cầu--cài-đặt)
3. [Khởi Động Ứng Dụng](#3-khởi-động-ứng-dụng)
4. [Vai Trò & Tài Khoản](#4-vai-trò--tài-khoản)
5. [Demo Hỏi Đáp Pháp Lý — Vai Trò Học Sinh](#5-demo-hỏi-đáp-pháp-lý--vai-trò-học-sinh)
6. [Demo Nhập Văn Bản Luật (PDF / DOCX) — Vai Trò Giáo Viên](#6-demo-nhập-văn-bản-luật-pdf--docx--vai-trò-giáo-viên)
7. [Demo Nhập Văn Bản Tình Huống (DOCX) — Vai Trò Giáo Viên](#7-demo-nhập-văn-bản-tình-huống-docx--vai-trò-giáo-viên)
8. [Demo Import Dataset Excel — Vai Trò Giáo Viên](#8-demo-import-dataset-excel--vai-trò-giáo-viên)
9. [Demo Đánh Giá Hệ Thống RAG — Vai Trò Giáo Viên](#9-demo-đánh-giá-hệ-thống-rag--vai-trò-giáo-viên)
10. [Demo Quản Lý Tài Khoản — Vai Trò Admin](#10-demo-quản-lý-tài-khoản--vai-trò-admin)
11. [Demo Quản Lý Văn Bản (Manage Law) + Từ Khóa + Kiểm Thử Hồi Quy — Vai Trò Giáo Viên + Admin](#11-demo-quản-lý-văn-bản-manage-law--vai-trò-giáo-viên--admin)
12. [Câu Hỏi Demo Gợi Ý](#12-câu-hỏi-demo-gợi-ý)
13. [Kiến Trúc Kỹ Thuật](#13-kiến-trúc-kỹ-thuật)
14. [Xử Lý Sự Cố](#14-xử-lý-sự-cố)

---

## 1. Tổng Quan Hệ Thống

### Stack Công Nghệ

| Thành phần | Công nghệ |
|---|---|
| Backend | FastAPI (Python 3.10+), chạy bằng Uvicorn |
| Vector Database | ChromaDB |
| Embedding Model | `BAAI/bge-m3` (HuggingFace, đa ngôn ngữ — đổi từ `bge-small-en-v1.5` ngày 2026-07-25, xem mục 14) |
| LLM | Groq — `llama-3.1-8b-instant` |
| OCR tiếng Việt | VietOCR (`vgg_transformer`) |
| Trích xuất PDF/DOCX | pypdf (text số) + pdf2image/Poppler (OCR scan) + python-docx |
| Nhận diện dòng văn bản | OpenCV |
| Xử lý Excel | pandas + openpyxl |
| Database ứng dụng | SQLite (`chat.db`) |

### Pipeline RAG (khi học sinh/giáo viên đặt câu hỏi)

```
[1] Câu hỏi người dùng
       ↓
[2] Kiểm tra ngoài phạm vi (ly hôn, hình sự, đất đai, thuế…) → từ chối sớm nếu không thuộc Luật Doanh nghiệp
       ↓
[3] Kiểm tra câu hỏi "meta" về hệ thống (VD: "database đang lưu bao nhiêu điều luật?") → trả lời trực tiếp, bỏ qua RAG
       ↓
[4] Trích xuất chủ đề (topic extraction)
     → Nếu nhận ra chủ đề: bỏ qua bước viết lại → tiết kiệm 1 lần gọi API
       ↓
[5] Viết lại câu hỏi (query rewrite) — chỉ khi không trích được chủ đề
       ↓
[6] Tìm kiếm ngữ nghĩa trong ChromaDB (topic-aware retrieval)
     → Ưu tiên khớp metadata theo chủ đề, fallback sang semantic search có ngưỡng độ tương đồng
       ↓
[7] Xếp hạng lại tài liệu (rerank)
     → Tính điểm từ: từ khóa + cụm từ + số điều luật + nguồn KB + từ khóa admin
       gắn cho nguồn (mục 13.8, thêm 2026-07-28)
       ↓
[8] Phân loại câu hỏi (definition / condition / procedure / general)
       ↓
[9] Xây dựng prompt có căn cứ pháp lý + gọi Groq LLM (có retry tự động khi rate-limit)
       ↓
[10] Làm sạch câu trả lời (loại bỏ chào hỏi, dòng trùng lặp)
       ↓
[11] Gắn trích dẫn điều luật chính + nguồn tham khảo phụ (📖/📎) + đường dẫn vbpl.vn
        → Trích dẫn (so_ky_hieu) chỉ được in ra nếu đang thực sự có mặt trong ChromaDB
          lúc đó (whitelist CITATION_SOURCE, xem mục 13.4) — chống trích dẫn "ma" từ
          văn bản đã bị xoá hoặc chưa từng được import
```

Hệ thống nạp dữ liệu vào ChromaDB qua **2 luồng import** độc lập, cùng dùng chung một vector store: **Văn bản luật** (mục 6), **Văn bản tình huống** (mục 7). *(Cập nhật 2026-07-28: bỏ luồng thứ 3 — Dataset Excel, mục 8 — khỏi ChromaDB do rủi ro data leakage thật đã xác minh; giờ Dataset chỉ còn là file kiểm thử trên đĩa, xem mục 14.)* Admin quản lý cả 3 loại (2 luồng nạp ChromaDB + Dataset trên đĩa) qua trang **Manage Law** (mục 11).

---

## 2. Yêu Cầu & Cài Đặt

### 2.1 Phần Mềm Cần Có

- **Python 3.10+**
- **Poppler** — đã có sẵn trong thư mục `poppler/` của dự án (dùng khi OCR PDF scan)
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

Nội bộ `app.py` tự gọi Uvicorn (`uvicorn.run("app:app", host="127.0.0.1", port=8000)`), nên không cần chạy lệnh `uvicorn` riêng. Terminal hiển thị:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Mở trình duyệt và truy cập: **http://127.0.0.1:8000**

> **Lần đầu khởi động:** Hệ thống tự động tạo file `chat.db` và seed 3 tài khoản mặc định (học sinh, giáo viên, admin). ChromaDB cũng sẽ tải embedding model từ HuggingFace (cần kết nối Internet lần đầu — `BAAI/bge-m3` khoảng 2GB, chỉ tải một lần rồi cache). `engine/rag_engine.py` còn tự chạy 3 tác vụ bảo trì lúc khởi động: làm mới whitelist trích dẫn (`refresh_citation_sources`), gắn nhãn nguồn gốc cho các đoạn dữ liệu cũ chưa được đánh dấu (`backfill_import_source_tags`, xem mục 13.4), và gắn nhãn người nhập mặc định `"admin1"` cho các đoạn nhập trước khi có tính năng theo dõi người nhập (`backfill_importer_tags`) — cả ba đều an toàn khi chạy lại nhiều lần.

---

## 4. Vai Trò & Tài Khoản

Hệ thống có **3 vai trò**, phân biệt bằng cột `role` trong bảng `users` (0 = Student, 1 = Teacher, 2 = Admin). Admin kế thừa toàn bộ quyền của Teacher.

### Tài Khoản Mặc Định

| Vai trò | Tên đăng nhập | Mật khẩu | Quyền |
|---|---|---|---|
| Học sinh | `testStudent1` | `123456P@ss` | Hỏi đáp RAG |
| Giáo viên | `teacher1` | `Teacher@123` | Hỏi đáp + Import văn bản luật/tình huống/dataset + Đánh giá RAG |
| Admin | `admin1` | `Admin@123` | Toàn bộ quyền Giáo viên + Quản lý tài khoản + Quản lý văn bản (xoá dữ liệu đã import) |

> Các tài khoản mặc định chỉ được tạo **một lần**, khi bảng `users` hoàn toàn trống lúc khởi động. Nếu bảng đã có dữ liệu, cần dùng chức năng **Import tài khoản** hoặc thêm thủ công (mục 10) để có tài khoản admin.

### Các Bước Demo Đăng Nhập

1. Truy cập `http://127.0.0.1:8000`
2. Nhập tên đăng nhập và mật khẩu
3. Nhấn **Đăng nhập**
4. Hệ thống tự động điều hướng theo vai trò:
   - **Học sinh** → giao diện chat hỏi đáp
   - **Giáo viên** → giao diện chat + nút **"Import Law"** trên sidebar + badge **"Giảng viên"**
   - **Admin** → giao diện chat + nút **"Import Law"** + nút **"Manage Account"** + nút **"Manage Law"** + badge **"Quản trị viên"**
5. Trên trang đăng nhập có link **"Đổi mật khẩu"** để tự đổi mật khẩu (yêu cầu tối thiểu 8 ký tự, có chữ hoa, chữ thường, số và ký tự đặc biệt)

![Trang đăng nhập](login.png)

---

## 5. Demo Hỏi Đáp Pháp Lý — Vai Trò Học Sinh

### Bước 1: Tạo Cuộc Trò Chuyện Mới

1. Đăng nhập bằng tài khoản học sinh (`testStudent1`)
2. Nhấn nút **"+ New Chat"** ở thanh bên trái
3. Một cuộc trò chuyện mới được tạo, sẵn sàng nhận câu hỏi

### Bước 2: Đặt Câu Hỏi

- Gõ câu hỏi vào ô nhập liệu ở cuối trang
- Nhấn **Enter** hoặc nút gửi
- Hệ thống xử lý theo pipeline ở mục 1, sau đó hiển thị:
  - Câu trả lời phù hợp với loại câu hỏi (định nghĩa / điều kiện / thủ tục)
  - **📖 Nguồn chính:** trích dẫn điều luật cụ thể
  - **📎 Nguồn tham khảo:** tối đa 3 điều luật liên quan khác (nếu có)
  - **🔗 Link:** đường dẫn vbpl.vn (nếu có)

![Giao diện chat học sinh](chat_index_student.png)

### Bước 3: Quản Lý Cuộc Trò Chuyện

| Thao tác | Cách thực hiện |
|---|---|
| Đổi tên chat | Nhấp đúp vào tiêu đề chat ở sidebar |
| Xóa chat | Nhấn icon thùng rác bên cạnh tên chat |
| Chuyển chat | Nhấn vào tên chat khác trong sidebar |
| Xem lại lịch sử | Nhấn vào chat có sẵn — tin nhắn được load từ SQLite |

> Chat của học sinh và giáo viên tách biệt hoàn toàn (cột `role` trong bảng `chats`), kể cả khi cùng một `user_id`.

---

## 6. Demo Nhập Văn Bản Luật (PDF / DOCX) — Vai Trò Giáo Viên

Tính năng này cho phép giáo viên (hoặc admin) tải lên file **PDF hoặc DOCX**. Hệ thống tự động trích xuất văn bản (hoặc OCR nếu là bản scan), phân đoạn theo điều khoản và lập chỉ mục vào ChromaDB.

### Bước 1: Truy Cập Trang Import

1. Đăng nhập bằng tài khoản giáo viên (`teacher1`) hoặc admin
2. Sidebar hiển thị nút **"Import Law"** và badge vai trò ở header
3. Nhấn nút **"Import Law"** — trang `/import` mở ra, tab mặc định **"📄 Upload PDF / DOCX"**

![Giao diện giáo viên — sidebar có nút Import Law và chat "Import new law"](chat_index_teacher.png)

### Bước 2: Điền Thông Tin và Upload

| Trường | Ví dụ | Mô tả |
|---|---|---|
| Số ký hiệu | `59/2020/QH14` | Số hiệu chính thức của văn bản — dùng để chống import trùng |
| Loại văn bản | `Luật Doanh nghiệp` | Tên/loại văn bản pháp luật |
| Nguồn thu thập | `vbpl.vn` | Nguồn gốc tài liệu |
| **Từ khóa chính** *(bắt buộc, thêm 2026-07-28)* | `hộ kinh doanh` | Chọn ≥1 từ khóa có sẵn trong danh sách (gõ vào ô tìm kiếm phía trên select để lọc nhanh) — quyết định độ ưu tiên **cao** khi chấm điểm nguồn lúc trả lời (mục 13.8) |
| Từ khóa phụ *(tuỳ chọn, thêm 2026-07-28)* | `đăng ký doanh nghiệp` | Giống từ khóa chính nhưng độ ưu tiên **thấp hơn**; có thể để trống |
| File | _(chọn file .pdf hoặc .docx)_ | Hỗ trợ PDF (scan hoặc số) và DOCX |

> Không thấy từ khóa mình cần trong danh sách? Admin cần vào **Manage Law → tab "Từ khóa"** (mục 11, bước 4) thêm từ khóa mới trước — trang Import không tự tạo từ khóa mới, chỉ chọn từ danh sách đã có sẵn.

Nhấn **"🚀 Tải lên & Xử lý AI"** để bắt đầu.

![Trang import PDF](import_pdf.png)

### Bước 3: Quy Trình Xử Lý Nền

```
1. Nhận file → lưu tạm thời vào uploads_tmp/
2. Nếu DOCX: trích xuất văn bản trực tiếp (python-docx, gồm cả bảng)
3. Nếu PDF:
   a. Thử trích xuất text trực tiếp bằng pypdf (PDF văn bản số)
   b. Nếu quá ít ký tự (< 150 ký tự/trang trung bình) → coi là bản scan, chuyển sang OCR:
      - Tải mô hình VietOCR (vgg_transformer), tự phát hiện CPU/GPU
      - Chuyển PDF → ảnh (DPI 250, từng batch 5 trang)
      - Tiền xử lý ảnh (tăng độ tương phản, làm nét)
      - Nhận dạng dòng văn bản bằng OpenCV
      - OCR từng dòng bằng VietOCR (beam search)
4. Phân đoạn theo "Điều X." trong văn bản
5. Tạo vector embedding và thêm vào ChromaDB (bỏ qua nếu Số ký hiệu đã tồn tại)
   — mỗi đoạn được gắn nhãn import_source="law" để trang Manage Law (mục 11)
     nhận diện đúng nguồn gốc
6. Gắn Từ khóa chính/phụ đã chọn ở Bước 2 vào Số ký hiệu này (bảng source_keyword,
   mục 13.8) — luôn ghi lại kể cả khi toàn bộ đoạn bị bỏ qua vì trùng, để hỗ trợ
   trường hợp import lại chỉ để sửa từ khóa
7. Tạo/cập nhật chat "Import new law" với thông báo kết quả
```

### Bước 4: Theo Dõi Tiến Trình

- Giao diện hiển thị **thanh tiến trình realtime** và trạng thái từng bước
- Trạng thái: `running` → `done` / `failed`
- Khi hoàn tất, kết quả xuất hiện trong chat **"Import new law"** ở sidebar
  - Thành công: `✅ Hoàn tất! Đã thêm X đoạn vào ChromaDB (bỏ qua Y đoạn trùng lặp).`
  - Thất bại: `❌ Lỗi: <chi tiết>`

![Kết quả import trong chat "Import new law"](import_result.png)

### Lưu Ý Quan Trọng

- Chỉ chấp nhận định dạng **PDF** hoặc **DOCX**
- Văn bản có **số ký hiệu trùng** sẽ bị bỏ qua tự động (không import lại)
- PDF văn bản số (có thể chọn/copy chữ) được trích xuất trực tiếp — **nhanh, không cần OCR**
- OCR chỉ chạy khi phát hiện PDF là bản scan, mặc định trên **CPU** — file 50 trang có thể mất 10–30 phút
- Nếu hệ thống **không nhận diện được ranh giới "Điều X."** trong văn bản (văn bản không có tiêu đề điều rõ ràng), quy trình sẽ dùng cắt đoạn dự phòng (3000 ký tự/đoạn) và **báo cảnh báo rõ ràng** trong tiến trình + trong chat "Import new law" — các đoạn này sẽ không có trích dẫn số Điều (thay vì âm thầm gán số Điều sai như trước)
- Một Điều đơn lẻ dài hơn 3000 ký tự (VD Điều 74, ~13.400 ký tự) được **chia nhỏ tiếp** thành các đoạn 2000–3000 ký tự, overlap 200–300 ký tự (`_split_long_segment` trong `import_law_engine.py`, thêm ngày 2026-07-25) — mỗi đoạn con vẫn giữ đúng `article_number`/`article_reference` của Điều gốc nên trích dẫn không bị ảnh hưởng. Khác với cắt đoạn dự phòng ở trên, cách này **không** gộp nội dung của nhiều Điều khác nhau vào cùng một đoạn.
- Để dùng **GPU** (nhanh hơn đáng kể), tạo file `.device_config` tại thư mục gốc:
  ```
  DEVICE=cuda
  ```
- Muốn xoá một văn bản đã import (VD nhập nhầm số ký hiệu)? Dùng trang **Manage Law** (mục 11, chỉ Admin) thay vì import chồng lên.

---

## 7. Demo Nhập Văn Bản Tình Huống (DOCX) — Vai Trò Giáo Viên

Ngoài văn bản luật gốc, giáo viên có thể nạp một **bộ tình huống pháp lý mẫu** (dạng phân tích IRAC — Issue/Rule/Application/Conclusion) để làm phong phú câu trả lời cho các câu hỏi tình huống thực tế.

### Bước 1: Chuyển Sang Tab Tình Huống

Tại trang `/import`, nhấn tab **"📚 Tình huống"**.

### Bước 2: Chuẩn Bị File & Upload

- File `.docx` phải theo đúng cấu trúc cố định — mỗi tình huống là một mục **Heading 1** dạng `Tình huống NN. <Chủ đề>`, gồm các mục con:

| Mục | Nội dung |
|---|---|
| Dòng đầu (sau Heading 1) | `Mã: <mã tình huống>   Độ khó: <Dễ/Trung bình/Khó>` |
| `1. Đề bài` | `Tình huống: …` và `Câu hỏi: …` |
| `2. Câu hỏi dẫn dắt xác định vấn đề pháp lý` | Mỗi câu hỏi dẫn dắt một dòng riêng |
| `3. Đáp án theo phương pháp IRAC` | `I – Issue:`, `R – Rule:`, `A – Application:`, `C – Conclusion:` |
| `4. Căn cứ pháp lý` | Mỗi căn cứ pháp lý một dòng riêng (có thể trích nhiều điều/nhiều văn bản khác nhau) |
| `5. Dữ liệu hỗ trợ truy xuất chatbot` | `Từ khóa: k1; k2; k3` và `Câu hỏi tương đương: q1 \| q2` |

- Tải file mẫu qua link **"⬇️ Tải example_scenario.docx"** ở panel bên phải để xem đúng cấu trúc (có sẵn 2 tình huống ví dụ minh hoạ).
- Chọn/kéo thả file `.docx` đã điền, nhấn **"📥 Tải lên & Xử lý"**.

### Bước 3: Quy Trình Xử Lý Nền

```
1. Đọc toàn bộ paragraph trong file .docx, tách theo từng khối "Tình huống NN."
2. Với mỗi tình huống: parse Mã/Độ khó, Đề bài, câu hỏi dẫn dắt, 4 thành phần IRAC,
   căn cứ pháp lý, từ khóa, câu hỏi tương đương
3. Gộp thành một đoạn văn bản có cấu trúc cho mỗi tình huống, gắn nhãn
   doc_type="scenario_qa", import_source="scenario", nguon_thu_thap=<tên file gốc>
4. Tạo vector embedding, thêm vào ChromaDB — bỏ qua nếu "Mã" tình huống đã tồn tại
   (import lại cùng file sẽ không tạo trùng)
5. *(thêm 2026-07-28)* Tự động gom **mọi cụm từ khóa duy nhất** trong mục "Từ khóa:"
   (mục 5. Dữ liệu hỗ trợ truy xuất chatbot) của tất cả tình huống trong file, tạo
   mới trong bảng `keyword` nếu chưa có, rồi gắn làm **Từ khóa phụ** cho cả file —
   không có ô nhập tay, không có Từ khóa chính (Tình huống là dữ liệu làm giàu ngữ
   cảnh, không phải nguồn chính thức nên không được buff điểm cao như Văn bản pháp
   luật, xem mục 13.8)
6. Tạo/cập nhật chat "Nhập văn bản tình huống" với thông báo kết quả
```

### Lưu Ý Quan Trọng

- **Không** gán số ký hiệu (`so_ky_hieu`) cho các đoạn tình huống — một tình huống có thể trích nhiều điều luật từ nhiều văn bản khác nhau cùng lúc (VD vừa Luật Doanh nghiệp vừa Nghị định 168/2025/NĐ-CP), nên hệ thống không tự gán một mã văn bản duy nhất để tránh trích dẫn sai nguồn. Khi trả lời, các đoạn này vẫn được dùng để truy xuất ngữ nghĩa bình thường, chỉ không tự sinh dòng "📖 Nguồn chính" theo số ký hiệu cho riêng chúng.
- Xoá một bộ tình huống đã import: dùng trang **Manage Law** (mục 11) → tab **Tình huống**, xoá theo tên file gốc.
- *(thêm 2026-07-28)* Trang Manage Law **không** có nút "Xem thông tin" cho tab Tình huống (và Dataset) — từ khóa của 2 loại này chỉ tự sinh lúc import, không xem/sửa tay được qua giao diện. Muốn đổi, phải xoá rồi import lại.

---

## 8. Demo Import Dataset Excel — Vai Trò Giáo Viên

> **Thay đổi lớn 2026-07-28:** Dataset Excel giờ **không còn nạp vào ChromaDB nữa**. Trước đây phát hiện rủi ro data leakage thật: đúng 200 dòng câu hỏi–đáp án của sheet `Dataset_200` (bộ câu hỏi dùng để chạy Full Evaluation, mục 9) bị lẫn vào ChromaDB dưới dạng đoạn chứa nguyên văn "Câu hỏi: ... / Trả lời: ...", khiến hệ thống có thể vô tình truy xuất trúng đáp án mẫu khi tự chấm điểm chính mình (xem mục 13.9/14). Để dứt điểm rủi ro thay vì chỉ vá riêng lẻ, **toàn bộ chức năng Import Dataset giờ chỉ dùng để quản lý bộ dữ liệu kiểm thử/đánh giá** — không còn đụng gì tới nội dung dùng để trả lời câu hỏi thật của người dùng. Muốn thêm nội dung luật thật vào ChromaDB, dùng **Import Văn bản luật** (mục 6) thay vì Import Dataset.

### Bước 1: Chuyển Sang Tab Dataset

Tại trang `/import`, nhấn tab **"📊 Import Dataset"**.

### Bước 2: Upload File

- Chọn/kéo thả file `.xlsx` có ít nhất 1 sheet đặt tên `Demo_*` hoặc `Dataset_*`
- Nhấn **"📥 Lưu làm bộ dữ liệu kiểm thử"**

### Bước 3: Quy Trình Xử Lý Nền (đơn giản hoá hoàn toàn, 2026-07-28)

```
1. Đọc tên các sheet trong file .xlsx — chỉ cần biết có sheet nào bắt đầu bằng
   "Demo_" hay "Dataset_" không, KHÔNG đọc/xử lý nội dung từng dòng
2. Nếu không có sheet nào thuộc 2 tiền tố trên → báo lỗi, dừng lại (file này
   không dùng được cho Quick/Full Evaluation)
3. Lưu file .xlsx gốc vào thư mục Dataset/ (không xoá, không ghi đè — trùng tên
   thì tự thêm hậu tố thời gian) — nhờ vậy file tự động xuất hiện trong dropdown
   chọn dataset ở mục 9 (Đánh giá hệ thống RAG)
4. Ghi lại tên file + người nhập + thời điểm nhập vào bảng dataset_file trong
   chat.db (mục 13.5) — đây là nguồn dữ liệu cho tab Dataset ở Manage Law (mục 11),
   KHÔNG còn liên quan gì tới ChromaDB nữa
```

- **Không** còn embedding, không còn nạp vào ChromaDB, không còn gắn Từ khóa (mục 13.8) — file chỉ nằm trên đĩa và được track tên trong `chat.db`
- Kết quả trả về: danh sách sheet Demo/Dataset tìm thấy, xác nhận đã lưu thành công
- Tất cả file dataset (upload qua đây hoặc đặt thủ công) đều nằm trong thư mục
  **`Dataset/`** ở gốc dự án — đây là nơi duy nhất hệ thống quét để tìm file cho
  tính năng Đánh giá RAG (mục 9); file đặt thủ công (không qua giao diện) vẫn dùng
  được cho mục 9 (quét trực tiếp từ đĩa) nhưng sẽ không xuất hiện ở tab Dataset của
  Manage Law cho tới khi được track — chạy `register_dataset_file()` thủ công qua
  Python nếu cần, hoặc import lại qua giao diện
- File `Dataset/example_sheet.xlsx` là **file mẫu/template** (phục vụ nút tải mẫu ở mục 9), luôn bị loại khỏi dropdown đánh giá — không phải dữ liệu thật

---

## 9. Demo Đánh Giá Hệ Thống RAG — Vai Trò Giáo Viên

Ngay bên dưới phần Import Dataset (cùng trang `/import`, tab **"📊 Import Dataset"**) là khu vực **Đánh giá hệ thống RAG**, dùng để đo chất lượng câu trả lời so với đáp án mẫu.

### Bước 0: Kết Quả Lần Gần Nhất Tự Hiện Khi Mở Tab

Ngay khi mở tab, hệ thống tự gọi `GET /latest_eval_result` và hiển thị luôn score card của **lần đánh giá gần nhất** (đánh dấu 📌 "Kết quả lần đánh giá gần nhất") — không cần chạy lại mới thấy điểm. Nếu chưa từng chạy đánh giá nào, khu vực này để trống cho tới khi bấm Quick/Full Evaluation lần đầu.

### Bước 1: Chọn File Dataset

- Dropdown **"File dataset dùng để đánh giá"** liệt kê **mọi file `.xlsx`** trong thư mục **`Dataset/`** (ở gốc dự án) có chứa ít nhất một sheet `Dataset_*` hoặc `Demo_*` — tự động lấy qua `GET /list_datasets`, không cần khai báo tên file cứng trong code (trừ `example_sheet.xlsx`, luôn bị loại vì là file mẫu). Đặt (hoặc để hệ thống tự lưu, xem mục 8) một file mới vào `Dataset/` (VD dataset 300 câu tương lai) là dropdown tự nhận diện ngay, miễn sheet đặt tên đúng quy ước `Dataset_*` / `Demo_*`.
- Mặc định chọn sẵn `enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx` nếu có trong danh sách.
- Ngay dưới dropdown hiển thị gợi ý các sheet Demo/Dataset tìm thấy trong file đang chọn; nếu file không có sheet Demo, nút **Quick Evaluation** tự động bị mờ/disable kèm cảnh báo.

### Bước 2: Chọn Chế Độ Đánh Giá

| Chế độ | Dữ liệu dùng | Cách chấm | Tốc độ |
|---|---|---|---|
| **⚡ Quick Evaluation** | **Toàn bộ** sheet `Demo_*` có trong file đã chọn, gộp lại và loại trùng theo cột `id` (VD file có cả `Demo_30` và `Demo_50` → gộp thành 50 câu duy nhất) | `auto` — so khớp từ khóa/trích dẫn (offline, không cần Groq) | Nhanh |
| **🔬 Full Evaluation** | Gộp toàn bộ sheet `Dataset_*` có trong file đã chọn, loại trùng theo cột `id` (VD file có cả `Dataset_150` và `Dataset_200` → gộp thành 198 câu), sau đó **lấy mẫu ngẫu nhiên tối đa 80 câu** *(giảm từ 100 xuống 80 ngày 2026-07-28 — `FULL_EVAL_SAMPLE_SIZE` trong `engine/evaluate_engine.py` — để mỗi lượt chạy nhanh hơn và ít khả năng dính rate-limit Groq giữa chừng hơn)* từ tập đã gộp nếu tập đó lớn hơn 80 (mỗi lần chạy chọn ngẫu nhiên lại, không cố định) — mỗi câu tốn 1 lượt gọi Groq cho RAG + 1 lượt cho giám khảo | `llm` — chấm bằng Groq LLM theo rubric | Chậm hơn (~3s/câu do throttle Groq) |

File `Dataset/example_sheet.xlsx` minh hoạ đúng quy ước đặt tên: sheet **`Demo_Quick_example`** (tiền tố `Demo_`) cho Quick Evaluation, sheet **`Dataset_example`** (tiền tố `Dataset_`) cho Full Evaluation. Đổi tên sheet sang tiền tố khác sẽ khiến sheet đó biến mất khỏi cả dropdown lẫn 2 nút đánh giá — `list_available_datasets()`/`run_evaluation()` trong `engine/evaluate_engine.py` chỉ quét đúng theo 2 tiền tố này.

> **Cập nhật 2026-07-26:** `example_sheet.xlsx` từng thiếu nhiều cột so với `enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx` thật (8/14 cột ở sheet `Dataset_example`/`Demo_Quick_example`, sai hẳn cấu trúc ở `Legal_Update_2025`) — quan trọng nhất là thiếu cả cột `retrieval_keywords`, khiến ai dùng file mẫu để tạo dataset mới sẽ vô tình lặp lại lỗi thiếu keywords (xem mục 13.7). Đã cập nhật khớp 100% cột với file dataset thật ở cả 4 sheet.

> Nếu file được chọn **không có sheet Demo** mà vẫn bấm Quick Evaluation (hoặc gọi thẳng API), hệ thống báo lỗi: `❌ File '<tên file>' không có sheet Demo — không thể chạy Quick Evaluation cho file này.` Tương tự với Full Evaluation và sheet Dataset. Đây là bộ đánh giá đọc trực tiếp từ file `.xlsx` đã chọn trên đĩa, **không** liên quan tới `chat.db`/ChromaDB.

### Bước 3: Theo Dõi Tiến Trình & Kết Quả

- Thanh tiến trình hiển thị số câu đã xử lý / tổng số câu
- Sau khi hoàn tất, hiển thị:
  - **Điểm tổng** (thang 100), theo rubric 5 tiêu chí có trọng số:

| Tiêu chí | Trọng số |
|---|---|
| Độ chính xác pháp lý | 40% |
| Trích dẫn điều luật | 20% |
| Mức độ liên quan ngữ cảnh | 20% |
| Kiểm soát bịa đặt (hallucination) | 15% |
| Rõ ràng, dễ hiểu | 5% |

  - Điểm chi tiết theo **loại câu hỏi** (definition/condition/procedure/general) và theo **độ khó**
  - Tên file dataset và danh sách sheet đã dùng để đánh giá (VD: `Demo_30, Demo_50`)
- Kết quả chi tiết từng câu được xuất ra file `eval_results_<tên_dataset>_<split>_<mode>_<timestamp>.xlsx` tại thư mục gốc dự án (không phải trong `Dataset/` — đây là file kết quả, không phải file input)
- Nút **"⬇️ Tải sheet kết quả"** xuất hiện ngay trong score card sau khi đánh giá xong — tải trực tiếp file `eval_results_*.xlsx` nói trên qua `GET /download_eval_result/<tên_file>` mà không cần vào thư mục dự án tìm thủ công
- Sau mỗi lần chạy, hệ thống **chỉ giữ lại 2 file `eval_results_*.xlsx` mới nhất** trên đĩa (tự xoá các file cũ hơn) và lưu tóm tắt lần chạy gần nhất vào `eval_results_latest.json` — đây là dữ liệu Bước 0 dùng để hiển thị lại khi mở tab, không cần giữ toàn bộ lịch sử vì giao diện chưa có màn hình duyệt kết quả cũ.

### Lưu Ý Về Độ Chính Xác Của Chấm Điểm `auto` Mode

Chế độ `auto` so khớp trích dẫn bằng cách trích số Điều từ `article_reference` của bộ câu hỏi rồi tìm chuỗi `"điều N"` trong câu trả lời. Với các trích dẫn phức tạp hơn (VD `"Khoản 35 Điều 4"`, `"Điều 17 Nghị định 168/2025/NĐ-CP"`, hoặc nhiều điều gộp `"Điều 27; Điều 38"`), hệ thống chỉ lấy đúng số theo sau từ "Điều" trong chuỗi tham chiếu — **không** gộp lẫn số Khoản/số Nghị định/số năm vào cùng một số Điều như trước (lỗi đã sửa ngày 2026-07-21, xem mục 14). Điểm `auto` vẫn là ước lượng nhanh dựa trên từ khóa, không thay thế được chấm `llm` (Full Evaluation) khi cần độ chính xác cao.

---

## 10. Demo Quản Lý Tài Khoản — Vai Trò Admin

Chỉ tài khoản có vai trò **Admin** mới truy cập được các tính năng này.

### Bước 1: Truy Cập Trang Quản Lý

1. Đăng nhập bằng tài khoản admin (`admin1`)
2. Nhấn nút **"👤 Manage Account"** trên sidebar — trang `/manage_accounts` mở ra
3. Bảng hiển thị toàn bộ tài khoản: tên đăng nhập, vai trò (pill màu), trạng thái (Đang hoạt động / Đã vô hiệu hóa)

### Bước 2: Thao Tác Trên Từng Tài Khoản

| Thao tác | Cách thực hiện | Ghi chú |
|---|---|---|
| Vô hiệu hóa | Nhấn **"Vô hiệu hóa"** | Tài khoản bị khóa đăng nhập, không xóa dữ liệu |
| Kích hoạt lại | Nhấn **"Kích hoạt"** | Cho phép đăng nhập trở lại |
| Xoá tài khoản | Nhấn **"Xoá"** → xác nhận | Xoá vĩnh viễn tài khoản + toàn bộ chat/lịch sử liên quan |

> Admin **không thể tự vô hiệu hóa hoặc tự xoá** chính tài khoản đang đăng nhập — nút tương ứng sẽ bị disable.

### Bước 3: Import Hàng Loạt Tài Khoản

1. Nhấn **"📥 Import tài khoản"** ở góc trên bảng — trang `/import_account` mở ra
2. Tải file mẫu qua link **"⬇️ Tải file Excel mẫu"** (2 cột: `Account Name`, `Role`)
3. Chọn/kéo thả file `.xlsx` đã điền, nhấn **"📥 Import tài khoản"**
4. Mỗi dòng hợp lệ được tạo với mật khẩu mặc định theo vai trò:

| Role trong file | Mật khẩu mặc định |
|---|---|
| Student | `123456P@ss` |
| Teacher | `Teacher@123` |

5. Sau khi import xong, hệ thống báo cáo: **Tạo mới**, **Bỏ qua (trùng tên)**, **Bỏ qua (không hợp lệ)**
6. Nếu có dòng lỗi (trùng tên / thiếu thông tin / role không hợp lệ), nút **"⚠️ Tải tài khoản lỗi"** xuất hiện — tải về file `.xlsx` liệt kê từng dòng lỗi kèm cột thứ 3 **"Nguyên nhân lỗi"**

---

## 11. Demo Quản Lý Văn Bản (Manage Law) — Vai Trò Giáo Viên + Admin

Trang quản lý tập trung cho **2 luồng import vào ChromaDB** (Văn bản pháp luật — mục 6, Tình huống — mục 7) và **Dataset** (mục 8 — chỉ file kiểm thử trên đĩa, không còn vào ChromaDB, xem mục 14). *Cập nhật 2026-07-28:* trang `/manage_law` giờ **mở cho cả Giáo viên**, không còn Admin-only như trước — nhưng Giáo viên chỉ thấy đúng 1 tab **"Văn bản pháp luật"** và **không có nút Xoá**; 3 tab còn lại (Dataset, Tình huống, Kiểm thử hồi quy) cùng tab **Từ khóa** mới vẫn chỉ Admin thấy được (ẩn phía client dựa theo `role` trả về từ `/session_info`, đồng thời các API `/list_dataset_sources`/`/list_scenario_sources`/... vẫn chặn 403 phía server nếu không phải Admin — không chỉ ẩn giao diện).

### Bước 1: Truy Cập Trang Quản Lý

1. Đăng nhập bằng tài khoản admin (`admin1`) hoặc giáo viên (`teacher1`)
2. Nhấn nút **"🗂️ Manage Law"** trên sidebar — trang `/manage_law` mở ra
3. Admin thấy đủ **5 tab**; Giáo viên chỉ thấy tab **"📖 Văn bản pháp luật"**

### Bước 2: Chuyển Tab & Xoá Dữ Liệu (chỉ Admin có nút Xoá)

| Tab | Nhóm theo | Ai thấy | Ghi chú |
|---|---|---|---|
| **📖 Văn bản pháp luật** | Số ký hiệu (`so_ky_hieu`) | Teacher + Admin | Xoá toàn bộ đoạn có cùng số ký hiệu — vd xoá `59/2020/QH14` sẽ gỡ hết các đoạn thuộc văn bản đó. Chỉ Admin thấy nút Xoá; cả 2 vai trò đều thấy nút **"Xem thông tin"** (Bước 3) |
| **📊 Dataset** | Tên file `.xlsx` đã upload — tra từ bảng `dataset_file` trong `chat.db`, **không phải ChromaDB** (đổi 2026-07-28, xem mục 8) | Admin | Xoá ở đây = xoá file thật khỏi `Dataset/` + gỡ khỏi theo dõi, file sẽ biến mất khỏi dropdown đánh giá (mục 9). Không có nút "Xem thông tin" (dataset không còn khái niệm từ khóa/nguồn nữa, chỉ là file test) |
| **📚 Tình huống** | Tên file `.docx` đã upload (`nguon_thu_thap`) | Admin | Xoá theo từng file — không ảnh hưởng các file tình huống khác. Không có nút "Xem thông tin" (từ khóa tự sinh, xem mục 7) |

Nhấn **"Xoá"** ở dòng tương ứng → xác nhận → hệ thống xoá toàn bộ đoạn khớp khỏi ChromaDB, đồng thời làm mới whitelist trích dẫn (`CITATION_SOURCE`) ngay lập tức để không còn trích dẫn tới nguồn đã xoá.

> **Không thể hoàn tác.** Với văn bản pháp luật/dataset, nếu xoá nhầm thì phải import lại từ file gốc.

> **Cập nhật 2026-07-25:** Cả 3 bảng (Văn bản pháp luật/Dataset/Tình huống) có thêm cột **"Người nhập"** — lấy từ `request.session["user_name"]` tại thời điểm import (fallback `admin1` nếu thiếu). Dữ liệu import từ trước ngày này không có tên người nhập chính xác.

### Bước 3: Nút "Xem Thông Tin" — Sửa Từ Khóa Của Một Văn Bản (mới, 2026-07-28)

Chỉ có ở tab **Văn bản pháp luật**, cho cả Teacher và Admin:

1. Nhấn **"Xem thông tin"** ở dòng văn bản muốn sửa — mở modal hiển thị: Số ký hiệu, Loại văn bản, Nguồn thu thập, Đoạn trong database, Người nhập (**không** hiện nội dung đoạn văn bản)
2. Bên dưới là 2 khối **Từ khóa chính** (bắt buộc ≥1) và **Từ khóa phụ** (tuỳ chọn) — mỗi khối có:
   - Các từ khóa đã gắn hiển thị dạng "chip" bo tròn, có dấu ✕ để xoá
   - Ô tìm kiếm + select để thêm từ khóa mới (chọn xong tự thêm thành chip, tự xoá khỏi danh sách select để không chọn trùng)
3. Sửa xong nhấn **"Nộp"** — hệ thống validate lại (không cho lưu nếu Từ khóa chính rỗng, cả phía giao diện lẫn phía server) rồi lưu qua `POST /update_source_keywords`
4. Đóng modal, mở lại vẫn còn nút Xoá bên cạnh (chỉ Admin) — bấm Xoá dùng đúng luồng ở Bước 2, không đi qua modal này

> Một từ khóa đã bị Admin **tắt** (mục Bước 4) vẫn hiển thị đúng nếu đã gắn cho văn bản này từ trước (vẫn tính điểm bình thường) — chỉ là không xuất hiện trong ô select để **gắn thêm mới**.

### Bước 4: Tab "Từ Khóa" — Quản Lý Danh Sách Từ Khóa (mới, 2026-07-28, chỉ Admin)

Bảng `keyword` trong `chat.db` dùng chung cho **2 mục đích khác nhau**, phân biệt bằng cột `status` (xem mục 13.8/13.9):

| Loại | status | Dùng để |
|---|---|---|
| **Chấm điểm nguồn** | `0`=đang dùng, `1`=đã tắt | Gắn cho Văn bản pháp luật (Bước 3, mục 6) để cộng điểm ưu tiên khi trả lời |
| **Chặn ngoài phạm vi** | `2`=đang dùng, `3`=đã tắt | Câu hỏi chứa từ khóa loại này (VD "ly hôn", "hình sự") bị từ chối trả lời ngay từ đầu, trước khi truy xuất |

Thao tác trong tab:
1. Form phía trên: ô nhập tên + select chọn loại (**Chấm điểm nguồn** / **Chặn ngoài phạm vi**) → nhấn **"＋ Thêm từ khóa"**. Tên từ khóa **không được trùng** giữa 2 loại (1 tên chỉ thuộc 1 loại tại một thời điểm)
2. Ô **"🔍 Tìm từ khóa theo tên"** lọc trực tiếp bảng bên dưới (không gọi lại server)
3. Bảng hiển thị **10 dòng/trang**, có nút Trước/Sau ở chân bảng để chuyển trang
4. Cột "Loại" hiện badge màu (xanh ngọc = Chấm điểm nguồn, vàng = Chặn ngoài phạm vi), cột "Trạng thái" hiện Đang dùng/Đã tắt, nút bật/tắt tự nhận đúng cặp trạng thái theo loại (không lẫn 0/1 với 2/3)
5. **Không có nút xoá từ khóa** — chỉ tắt (status lẻ). Từ khóa đã tắt vẫn giữ nguyên hiệu lực với văn bản đã gắn từ trước, chỉ ẩn khỏi các ô chọn mới (Bước 3, mục 6) để tránh việc tắt một từ khóa làm sập điểm số các văn bản đang dùng nó

### Bước 5: Tab Kiểm Thử Hồi Quy (2026-07-25, chỉ Admin)

Tab thứ 4 **"🧪 Kiểm thử hồi quy"** chạy bộ test nhanh (không gọi LLM, không tốn quota Groq) kiểm tra 4 cặp câu hỏi dễ nhầm loại hình doanh nghiệp (VD "TNHH một thành viên" vs "TNHH hai thành viên trở lên") thẳng qua `retrieve_docs → filter_compatible_docs → select_best_doc` — bảo vệ chống hồi quy cho cơ chế lọc entity-type đã thêm ngày 2026-07-25 (xem mục 14).

1. Bấm **"▶️ Chạy kiểm thử"** — job chạy nền, thanh tiến trình cập nhật theo từng câu hỏi (`GET /regression_test_status/<job_id>`)
2. Sau khi xong, mỗi câu hiện ✅/❌ kèm `expected`/`got_best`, và cảnh báo nếu tài liệu bị cấm (entity-type sai) vẫn lọt vào danh sách sau lọc
3. Hệ thống chỉ giữ **2 lần chạy gần nhất** trong `regression_results_history.json` — mở lại tab sẽ tự hiện các lần chạy đã lưu (`GET /latest_regression_results`) mà không cần chạy lại
4. Nên chạy lại sau khi sửa logic truy xuất/rerank trong `engine/rag_engine.py`, hoặc sau khi import dữ liệu luật mới — xem thêm `evaluate/retrieval_regression_tests.py` (dùng chung logic với `engine/regression_test_engine.py`, script CLI này cũng chạy độc lập được: `python evaluate/retrieval_regression_tests.py`)

---

## 12. Câu Hỏi Demo Gợi Ý

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

### Câu Hỏi Tình Huống (minh hoạ dữ liệu từ mục 7)

```
Nam 17 tuổi có thể tự đứng tên thành lập công ty TNHH một thành viên không?
Công chức có được đứng tên thành lập và làm Giám đốc công ty không?
```

### Câu Hỏi Meta Về Hệ Thống

```
Database đang lưu bao nhiêu điều luật?
Luật Doanh nghiệp có bao nhiêu điều?
```

### Câu Hỏi Ngoài Phạm Vi (để minh họa cơ chế từ chối)

```
Thủ tục ly hôn cần giấy tờ gì?
```

### Demo Tính Năng Từ Khóa Chấm Điểm Nguồn (mới, 2026-07-28)

Gợi ý trình tự demo mục 13.8 trực quan nhất — cần tài khoản admin:

1. Vào **Manage Law → tab Từ khóa** (mục 11, Bước 4), thêm 1 từ khóa mới loại "Chấm điểm nguồn", VD `"hộ kinh doanh"` (đã có sẵn trong danh sách seed, có thể bỏ qua bước này nếu chỉ demo xem)
2. Vào tab **Văn bản pháp luật**, bấm **"Xem thông tin"** ở văn bản `168/2025/NĐ-CP`, gắn `"hộ kinh doanh"` làm Từ khóa chính, Nộp
3. Quay lại chat, hỏi: `Hộ kinh doanh phát hiện giấy chứng nhận ghi chưa chính xác so với hồ sơ. Cơ quan đăng ký kinh doanh cấp xã cấp lại trong bao lâu nếu đề nghị chính xác?`
4. Quan sát trích dẫn "📖 Nguồn chính" — nhờ từ khóa vừa gắn, nguồn `168/2025/NĐ-CP` được ưu tiên đúng thay vì lẫn sang Luật Doanh nghiệp gốc

> **Mẹo demo:** Bắt đầu bằng câu hỏi định nghĩa để thấy hệ thống khớp chính xác điều luật. Sau đó chuyển sang câu hỏi thủ tục để thấy định dạng danh sách bước. Thử một câu hỏi ngoài phạm vi để minh họa cơ chế từ chối. Cuối cùng nhập PDF/DOCX/tình huống mới hoặc chạy Đánh giá RAG để minh họa các tính năng cho giáo viên/admin.

---

## 13. Kiến Trúc Kỹ Thuật

### 13.1 Cấu Trúc Thư Mục

```
rag-legal-assistant-master/
├── app.py                          # FastAPI app — tất cả routes
├── engine/
│   ├── rag_engine.py                # Pipeline RAG hỏi đáp + whitelist trích dẫn + quản lý nguồn (Manage Law)
│   ├── import_law_engine.py         # Pipeline import PDF/DOCX: extract/OCR → phân đoạn → embedding
│   ├── import_scenario_engine.py    # Pipeline import DOCX tình huống (IRAC) → ChromaDB
│   ├── import_dataset_engine.py     # Import dataset Excel (150/200-updated) → ChromaDB
│   ├── import_account_engine.py     # Import tài khoản hàng loạt từ Excel
│   └── evaluate_engine.py           # Đánh giá chất lượng RAG (auto/llm, demo/all/test)
├── database/
│   ├── database.py                  # SQLite: users (3 role), chats, messages
│   └── reference_source.py          # Script rời — thêm thủ công vài điều luật tham khảo (không qua UI); chạy `python -m database.reference_source` (lệnh có trong terminal.txt), dedupe theo (so_ky_hieu, article_number) nên chạy lại nhiều lần an toàn
├── templates/
│   ├── login.html                   # Đăng nhập + đổi mật khẩu
│   ├── index.html                   # Giao diện chat chính
│   ├── import_law.html              # Import PDF/DOCX + Import Tình huống + Import Dataset + Đánh giá RAG
│   ├── manage_accounts.html         # Quản lý tài khoản (Admin)
│   ├── manage_law.html              # Quản lý văn bản đã import — 3 tab (Admin)
│   └── import_account.html          # Import tài khoản hàng loạt (Admin)
├── chroma_db/                       # Vector database (ChromaDB)
├── Dataset/                          # Mọi file .xlsx dùng để đánh giá RAG (mục 9) — nơi duy nhất /list_datasets quét
│   ├── enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx   # Dataset mới nhất (200 câu, cập nhật pháp lý 2025)
│   ├── example_sheet.xlsx            # File mẫu cấu trúc Dataset — không dùng để đánh giá
│   └── example_scenario.docx         # File mẫu cấu trúc Tình huống (mục 7)
├── uploads_tmp/                     # Lưu file tạm thời khi import (bị xoá/di chuyển ngay sau khi xử lý xong)
├── eval_results_*.xlsx               # Kết quả đánh giá RAG chi tiết từng câu — chỉ giữ 2 file mới nhất (mục 9)
├── eval_results_latest.json          # Tóm tắt lần đánh giá gần nhất — hiển thị khi mở tab Đánh giá (mục 9)
├── chat.db                          # SQLite database
├── groqkey.txt                      # Groq API key (không commit lên git)
└── requirements.txt                  # Thư viện
```

### 13.2 API Endpoints

| Endpoint | Method | Mô tả | Quyền |
|---|---|---|---|
| `/` | GET | Trang chủ / đăng nhập | Public |
| `/login` | POST | Đăng nhập | Public |
| `/logout` | POST | Đăng xuất | Logged in |
| `/session_info` | GET | Thông tin phiên hiện tại | Public |
| `/change_password` | POST | Tự đổi mật khẩu | Public (cần đúng mật khẩu cũ) |
| `/get` | POST | Hỏi đáp RAG | Logged in |
| `/create_chat` | POST | Tạo chat mới | Logged in |
| `/list_chats` | GET | Danh sách chats | Logged in |
| `/get_chat_messages` | GET | Lịch sử tin nhắn | Logged in |
| `/rename_chat` | POST | Đổi tên chat | Logged in |
| `/delete_chat` | POST | Xóa chat | Logged in |
| `/import` | GET | Trang import văn bản luật / tình huống / dataset | Teacher, Admin |
| `/import_law` | POST | Upload PDF/DOCX để import | Teacher, Admin |
| `/import_status/{job_id}` | GET | Tiến trình import PDF/DOCX | Teacher, Admin |
| `/import_scenario` | POST | Upload DOCX tình huống để import | Teacher, Admin |
| `/import_scenario_status/{job_id}` | GET | Tiến trình import tình huống | Teacher, Admin |
| `/download_scenario_example` | GET | Tải file DOCX mẫu cho tình huống | Teacher, Admin |
| `/import_dataset` | POST | Upload dataset Excel | Teacher, Admin |
| `/import_dataset_status/{job_id}` | GET | Tiến trình import dataset | Teacher, Admin |
| `/list_datasets` | GET | Danh sách file `.xlsx` có thể dùng để đánh giá | Teacher, Admin |
| `/download_dataset_example` | GET | Tải file Excel mẫu cho dataset | Teacher, Admin |
| `/evaluate` | POST | Chạy đánh giá RAG (mode/split/dataset_file) | Teacher, Admin |
| `/evaluate_status/{job_id}` | GET | Tiến trình đánh giá | Teacher, Admin |
| `/latest_eval_result` | GET | Kết quả tóm tắt lần đánh giá gần nhất | Teacher, Admin |
| `/download_eval_result/{filename}` | GET | Tải file kết quả đánh giá (`eval_results_*.xlsx`) | Teacher, Admin |
| `/manage_accounts` | GET | Trang quản lý tài khoản | Admin |
| `/list_users` | GET | Danh sách tài khoản | Admin |
| `/toggle_user_status` | POST | Vô hiệu hóa / kích hoạt tài khoản | Admin |
| `/delete_user` | POST | Xoá tài khoản | Admin |
| `/import_account` | GET / POST | Trang & xử lý import tài khoản hàng loạt | Admin |
| `/download_account_template` | GET | Tải file Excel mẫu import tài khoản | Admin |
| `/manage_law` | GET | Trang quản lý văn bản đã import (5 tab) | **Teacher, Admin** *(mở cho Teacher 2026-07-28, trước đó Admin-only)* |
| `/list_law_sources` | GET | Danh sách văn bản pháp luật (nhóm theo số ký hiệu) | **Teacher, Admin** *(mở cho Teacher 2026-07-28)* |
| `/delete_law_source` | POST | Xoá một văn bản pháp luật theo số ký hiệu | Admin |
| `/list_dataset_sources` | GET | Danh sách dataset đã import (nhóm theo tên file) | Admin |
| `/delete_dataset_source` | POST | Xoá một dataset theo tên file | Admin |
| `/list_scenario_sources` | GET | Danh sách bộ tình huống đã import (nhóm theo tên file) | Admin |
| `/delete_scenario_source` | POST | Xoá một bộ tình huống theo tên file | Admin |
| `/get_source_info` *(mới 2026-07-28)* | GET | Thông tin cơ bản + từ khóa của 1 văn bản (`?source_type=law&source_key=...`) — chỉ hỗ trợ `source_type=law` | Teacher, Admin |
| `/update_source_keywords` *(mới 2026-07-28)* | POST | Cập nhật Từ khóa chính/phụ của 1 văn bản pháp luật | Teacher, Admin |
| `/list_active_keywords` *(mới 2026-07-28)* | GET | Danh sách từ khóa "Chấm điểm nguồn" đang bật (status=0) — dùng cho ô chọn khi import/sửa | Teacher, Admin |
| `/list_keywords` *(mới 2026-07-28)* | GET | Toàn bộ từ khóa (cả 2 loại, cả bật lẫn tắt) — dùng cho tab Từ khóa | Teacher, Admin |
| `/add_keyword` *(mới 2026-07-28)* | POST | Thêm từ khóa mới (`name`, `is_out_of_scope`) | Admin |
| `/toggle_keyword_status` *(mới 2026-07-28)* | POST | Bật/tắt 1 từ khóa (`id`, `status` — nhận 0/1/2/3) | Admin |

### 13.3 Phân Loại Câu Hỏi RAG

| Loại | Từ khóa nhận dạng | Định dạng trả lời |
|---|---|---|
| `procedure` | trình tự, thủ tục, quy trình, các bước, hồ sơ, nộp ở đâu | Danh sách bước 1, 2, 3… |
| `condition` | điều kiện, yêu cầu, cần có, phải có | Liệt kê điều kiện |
| `definition` | là gì, khái niệm, định nghĩa, quy định về | Ngắn gọn + căn cứ điều luật |
| `general` | _(các câu hỏi khác)_ | Tự do, nêu đủ căn cứ pháp lý |

### 13.4 Whitelist Trích Dẫn (`CITATION_SOURCE`) & Nhãn Nguồn Gốc (`import_source`)

- **`CITATION_SOURCE`** (lưu trong `chat.db`, bảng `const`) là danh sách mọi `so_ky_hieu` **đang thực sự có mặt** trong ChromaDB tại thời điểm làm mới gần nhất. `build_citation()` trong `rag_engine.py` từ chối in ra bất kỳ số ký hiệu nào không nằm trong danh sách này — chặn trường hợp trích dẫn tới văn bản đã bị xoá hoặc metadata bị hỏng/giả mạo. Danh sách này tự làm mới sau mỗi lần import (mục 6, 8) và sau mỗi lần xoá (mục 11).
- Đoạn dữ liệu từ **Văn bản tình huống** (mục 7) không có `so_ky_hieu` nên không nằm trong whitelist — đây là chủ đích, không phải thiếu sót (xem giải thích ở mục 7).
- **`import_source`** là nhãn nội bộ (`"law"` / `"dataset"` / `"scenario"`) gắn vào từng đoạn dữ liệu để trang Manage Law (mục 11) biết đoạn đó thuộc luồng import nào. Dữ liệu import **trước khi** nhãn này tồn tại được tự động gắn nhãn suy luận lúc khởi động ứng dụng (`backfill_import_source_tags()`, chạy một lần, an toàn khi gọi lại nhiều lần) — dựa trên các dấu hiệu sẵn có trong metadata (VD: có `segment_index` → `"law"`; `doc_type="scenario_qa"` → `"scenario"`; `so_ky_hieu` khớp mã dataset mặc định và không có `segment_index` → `"dataset"`). Vài đoạn tham khảo thêm bằng tay qua `database/reference_source.py` (ngoài 3 luồng UI) không được gắn nhãn này, nên sẽ không xuất hiện ở tab Dataset/Tình huống của Manage Law — nhưng vẫn xuất hiện đúng ở tab Văn bản pháp luật (nhóm theo `so_ky_hieu`).
- **`importer`** (thêm ngày 2026-07-25) ghi lại `user_name` của giáo viên/admin thực hiện import, hiển thị ở cột "Người nhập" trong cả 3 tab của Manage Law (mục 11). Dữ liệu import **trước khi** có trường này được gắn mặc định `"admin1"` lúc khởi động (`backfill_importer_tags()`, cùng cơ chế idempotent như `backfill_import_source_tags()` ở trên).

### 13.5 Schema Database SQLite

```
users          (user_id, user_name, password, role[0=student,1=teacher,2=admin], status[0=active,1=disabled])
chats          (id, student_id, title, created_at, role[0=student chat, 1=teacher chat])
messages       (id, chat_id, role[user|assistant], text, timestamp)
const          (name, content)   # key/value — VD name="CITATION_SOURCE" (xem mục 13.4)
keyword        (id, name, status)   # thêm 2026-07-28, xem mục 13.8/13.9
               # status: 0=chấm điểm nguồn/đang dùng, 1=chấm điểm nguồn/đã tắt,
               #         2=chặn ngoài phạm vi/đang dùng, 3=chặn ngoài phạm vi/đã tắt
source_keyword (id, source_type, source_key, keyword_id, kind)   # thêm 2026-07-28
               # source_type: law|scenario (dataset đã bỏ khỏi cơ chế này, xem mục
               # 13.8/13.9 và mục 14 "Bộ Câu Hỏi Full Evaluation Từng Bị Lẫn Vào
               # ChromaDB") ; source_key: so_ky_hieu (law) / nguon_thu_thap (scenario)
               # ; kind: primary|secondary
dataset_file   (id, filename, importer, uploaded_at)   # thêm 2026-07-28, xem mục 8/11
               # Danh sách file dataset .xlsx đã upload — CHỈ để track cho tab
               # Dataset ở Manage Law, không liên quan tới ChromaDB
```

Chat giáo viên và học sinh được tách biệt hoàn toàn theo cột `role`, ngay cả khi `user_id` trùng nhau. Admin dùng chung không gian chat với vai trò Teacher (`role=1`).

### 13.6 Bộ Lọc Loại Hình Doanh Nghiệp & Kiểm Tra Trích Dẫn Sau Sinh (thêm 2026-07-25)

- **`filter_compatible_docs()`** (`rag_engine.py`): trước khi rerank, loại khỏi danh sách ứng viên mọi tài liệu có loại hình doanh nghiệp xung đột với câu hỏi (VD câu hỏi về "TNHH một thành viên" sẽ loại tài liệu "TNHH hai thành viên trở lên", công ty cổ phần, doanh nghiệp tư nhân, công ty hợp danh). Loại hình được suy luận bằng `infer_doc_entity()` — quét `page_content`/`topic`/`retrieval_keywords`/`title` vì metadata hiện tại chưa có trường `entity_type` riêng. Tài liệu không xác định được loại hình (điều khoản áp dụng chung) luôn được giữ lại thay vì loại nhầm.
- **`validate_answer_citations()`** (`rag_engine.py`): sau khi LLM sinh câu trả lời, nếu bất kỳ số Điều nào xuất hiện trong phần **thân** câu trả lời không có mặt trong context đã truy xuất, toàn bộ câu trả lời bị từ chối (trả về cảnh báo) thay vì hiển thị — khác với whitelist `CITATION_SOURCE` ở mục 13.4 vốn chỉ kiểm tra dòng trích dẫn cuối, cơ chế này chặn cả trường hợp LLM viết sai số Điều ngay trong nội dung trả lời.
- Xem thêm `evaluate/retrieval_regression_tests.py` — bộ test nhanh (không gọi LLM) kiểm tra các cặp câu hỏi dễ nhầm loại hình doanh nghiệp, nên chạy lại sau khi sửa logic truy xuất/rerank hoặc import dữ liệu mới.

---

### 13.7 "Căn Cứ Pháp Lý", Chấm Điểm Rerank & Chịu Lỗi Chính Tả (thêm 2026-07-25/26)

Loạt sửa lỗi sau khi rà soát kỹ file `25-7 nhan xet.docx` và đo Quick Evaluation nhiều vòng liên tiếp (~62% → 81.8%):

- **`build_legal_basis_line()`** (`rag_engine.py`): mục **"Căn cứ pháp lý"** trong câu trả lời (giữa Kết luận và Phân tích) giờ được **code tự build** từ metadata `best_doc`/`extra_docs` — y hệt cách "Nguồn chính"/"Nguồn tham khảo" đã hoạt động — thay vì để LLM tự viết theo gợi ý trong prompt. Trước đây LLM đôi khi viết lệch (VD ghi "Điều 74" trong khi "Nguồn chính" đúng là "Điều 21"), gây mâu thuẫn ngay trong cùng một câu trả lời.
- **`_detect_khoan()`**: khi tài liệu gốc là một Điều luật có nhiều khoản đánh số, hệ thống so khớp từ ngữ giữa câu trả lời và từng khoản để hiển thị chính xác **"Khoản N Điều X"** thay vì chỉ "Điều X" — chỉ áp dụng khi một khoản rõ ràng nổi bật hơn hẳn các khoản còn lại, mơ hồ thì giữ nguyên cả Điều.
- **`_strip_tone()` / `_phrase_in()`**: các danh sách cụm từ nhận diện ý định câu hỏi (`_INTENT_PROCEDURE_PHRASES`...) và loại hình doanh nghiệp (`_ENTITY_ONE_MEMBER_PHRASES`...) giờ **chịu được lỗi thiếu dấu thanh** (sắc/huyền/hỏi/ngã/nặng — VD gõ "lâp" thay vì "lập"). Trước đây chỉ cần thiếu 1 dấu là so khớp chuỗi con trượt hoàn toàn, khiến câu hỏi rơi về intent "general", mất hẳn ưu tiên truy xuất tài liệu thủ tục thành lập. *Giới hạn:* chỉ chịu được thiếu dấu thanh, chưa xử lý gõ không dấu hoàn toàn (bỏ luôn ă/â/ê/ô/ơ/ư/đ).
- **`_score_doc()` cân bằng lại trọng số**: bonus "nguồn chính thống" (văn bản luật gốc từ Cổng thông tin chính phủ) giảm từ +20 xuống +6; trọng số khớp từ khóa đơn lẻ cho các đoạn luật gốc mới import (`import_source="law"`) giảm từ 3x xuống 1x — tránh việc một Điều luật gốc chỉ trùng từ chung chung ("công ty", "cổ đông"...) thắng điểm so với đúng Điều luật cần tìm. Đồng thời phạt điểm nhóm `doc_type` bắt đầu bằng `convert_*` (chuyển đổi loại hình) trừ khi câu hỏi thực sự hỏi về chuyển đổi — tránh hòa điểm với các Điều về thành lập/định nghĩa cùng loại hình.
- **`_ENTITY_AGNOSTIC_DOC_TYPES`**: các `doc_type` mang tính điều kiện chung cho MỌI loại hình doanh nghiệp (`establishment_eligibility`, `civil_capacity_condition`, `household_business_eligibility_condition`) giờ luôn được `infer_doc_entity()` trả về "không xác định loại hình" thay vì suy luận nhầm — trước đây một Điều luật tổng quát như Điều 17 (quyền thành lập) bị gán nhầm là "công ty cổ phần" chỉ vì tiêu đề có nhắc "mua cổ phần", khiến nó bị loại khỏi mọi câu hỏi về loại hình khác.
- **`_is_out_of_scope()`**: thêm `_BUSINESS_CONTEXT_SIGNALS` — một câu hỏi dính từ khóa "ngoài phạm vi" (VD "hình sự", "gia đình", "quyền sử dụng đất") vẫn được coi là **trong phạm vi** nếu câu hỏi cũng có tín hiệu doanh nghiệp rõ ràng (công ty, doanh nghiệp, góp vốn, thành lập...) — tránh chặn nhầm các câu hỏi Luật Doanh nghiệp hợp lệ chỉ vì nhắc tới điều kiện/lĩnh vực khác như một chi tiết mô tả tình huống.
- **`engine/import_law_engine.py`**: mỗi Điều luật import từ file luật gốc (PDF/DOCX) giờ có `retrieval_keywords` tự sinh từ tiêu đề Điều (bỏ tiền tố "Điều N.") — trước đây hoàn toàn thiếu trường này khiến các Điều luật gốc luôn thua điểm so với dữ liệu Excel curated.
- **`engine/import_dataset_engine.py`**: sửa bug thật trong `_build_qa_docs()` — hàm đọc đúng cột `retrieval_keywords` từ file Excel nhưng **quên đưa vào metadata** khi tạo chunk cho sheet `Dataset_*`/`Demo_*` (chỉ nhét vào nội dung text, không set field mà `_score_doc()` thực sự dùng để chấm điểm). **Cần re-import lại dataset đã có** (xoá qua Manage Law rồi import lại — xem mục 11) để chunk cũ được cập nhật, vì cơ chế chống trùng theo `doc_id` sẽ bỏ qua nếu import chồng lên mà không xoá trước.
- **Prompt (`build_prompt()`)**: thêm quy tắc — nếu câu hỏi về TNHH MỘT thành viên, không liệt kê "danh sách thành viên" trong hồ sơ đăng ký (mục này chỉ áp dụng công ty TNHH hai thành viên trở lên); và quy tắc chung — nội dung tài liệu không áp dụng cho câu hỏi thì **bỏ hẳn** khỏi câu trả lời, không ghi kèm kiểu "(không áp dụng)" gây rối trọng tâm.

---

### 13.8 Cơ Chế Từ Khóa Chấm Điểm Nguồn (Keyword-Based Source Scoring, thêm 2026-07-28)

**Vấn đề trước đây:** `_score_doc()` chỉ boost điểm cho những nguồn khớp điều kiện **viết cứng trong code** (VD `_ESTABLISHMENT_DOC_TYPES`, hoặc check thẳng chuỗi `"168/2025"` cho câu hỏi hộ kinh doanh — xem mục 13.7). Văn bản luật mới import qua mục 6 không khớp bất kỳ điều kiện cứng nào, nên **không bao giờ** được các boost đặc thù này, dù nội dung thực sự liên quan.

**Giải pháp:** admin/giáo viên tự gắn **Từ khóa chính/phụ** cho từng văn bản (mục 6 lúc import, hoặc mục 11 Bước 3 sửa sau) — không cần sửa code mỗi khi có văn bản mới:

- **Chấm điểm (rerank):** nếu câu hỏi khớp Từ khóa chính của nguồn đang xét → **+8 điểm**; khớp Từ khóa phụ → **+4 điểm**. Giá trị cố ý để nhỏ (ban đầu thử +25/+10 giống các boost cứng khác, nhưng test thật phát hiện: một nguồn vừa có đoạn luật gốc vừa có đoạn dataset đã curate riêng cho đúng câu hỏi đó, +25 áp đều cho mọi đoạn của nguồn đủ để đoạn luật gốc chung chung thắng điểm đoạn dataset curate chính xác hơn — xem `_score_doc()` trong `rag_engine.py` để rõ chi tiết phép đo).
- **Truy xuất (retrieval augmentation):** ngoài chấm điểm, nếu câu hỏi khớp từ khóa của 1 nguồn, hệ thống còn **chủ động kéo thêm top-5 đoạn phù hợp nhất** của nguồn đó vào danh sách ứng viên — tránh trường hợp semantic/keyword search thường không tìm ra nguồn mới (chưa có `retrieval_keywords` curate sẵn) nên chấm điểm dù có boost cũng không có cơ hội phát huy. Chỉ kéo top-5, **không kéo cả nguồn**, vì một nguồn được gắn có thể có hàng trăm đoạn (VD Luật Doanh nghiệp 2020 có 310 đoạn) — kéo hết sẽ làm loãng candidate pool.
- Cơ chế **cộng thêm hoàn toàn** (additive) — nguồn chưa được gắn từ khóa nào hoạt động y hệt trước đây, không có gì thay đổi. Xác nhận qua `evaluate/retrieval_regression_tests.py` (4/4 pass) và so sánh trực tiếp trích dẫn 10 câu mẫu trước/sau khi thêm — giống hệt.
- Chỉ **Văn bản pháp luật** mới có Từ khóa chính (buff cao) vì đây là nguồn chính thức quan trọng nhất; **Tình huống** chỉ tự sinh Từ khóa phụ (buff thấp) vì là dữ liệu làm giàu ngữ cảnh, không phải nguồn thẩm quyền (xem mục 7). **Dataset không còn tham gia cơ chế này nữa** — từ 2026-07-28, Dataset chỉ là file kiểm thử trên đĩa, không nạp vào ChromaDB nên không có gì để gắn từ khóa/chấm điểm (xem mục 8 và mục 14 "Bộ Câu Hỏi Full Evaluation Từng Bị Lẫn Vào ChromaDB").

### 13.9 Danh Sách Chặn Ngoài Phạm Vi Chuyển Vào Database (thêm 2026-07-28)

Trước đây `OUT_OF_SCOPE_KEYWORDS` (mục 13.7, `_is_out_of_scope()`) là 1 list Python viết cứng trong `rag_engine.py` — muốn thêm/bớt cụm từ chặn phải sửa code. Giờ nguồn dữ liệu chính là bảng `keyword` với `status=2` (đang dùng) — quản lý qua **Manage Law → tab Từ khóa** (mục 11, Bước 4), admin có thể tự thêm/tắt mà không cần sửa code.

- `ask_rag()` gọi `get_active_out_of_scope_keywords()` (đọc từ DB) mỗi câu hỏi; nếu đọc DB lỗi vì lý do nào đó, **fail-safe** về lại list Python cũ (`OUT_OF_SCOPE_KEYWORDS`) — không bao giờ fail-open (tắt hẳn việc chặn).
- Đã seed sẵn 24 cụm (23 cụm cũ + thêm mới `"nhà đất"` — phát hiện thiếu ngày 2026-07-28: câu "Thủ tục mua bán nhà đất..." lọt qua chặn vì list cũ chỉ có "đất đai"/"nhà ở"/"bất động sản", không có "nhà đất").
- **Không ảnh hưởng** tới chấm điểm nguồn (mục 13.8) — 2 cơ chế dùng chung 1 bảng `keyword` nhưng khác hẳn mục đích, phân biệt bằng `status` (0/1 = chấm điểm, 2/3 = chặn phạm vi), không bao giờ lẫn lộn (`get_active_keywords()` chỉ lấy status=0, `get_active_out_of_scope_keywords()` chỉ lấy status=2).

---

## 14. Xử Lý Sự Cố

### Ứng Dụng Không Khởi Động Được — `ValueError: ... torch.load ...` / `check_torch_load_is_safe`

**Triệu chứng:** `python app.py` (hoặc bất kỳ script nào `import engine.rag_engine`) crash ngay khi tải embedding model, log có dòng `Due to a serious vulnerability issue in torch.load, ... we now require users to upgrade torch to at least v2.6`.

**Nguyên nhân (2026-07-25):** Đổi embedding model sang `BAAI/bge-m3` (đa ngôn ngữ, xem mục 1 và 13.6) — `transformers` chặn load checkpoint qua `torch.load` nếu torch < 2.6 (bản vá CVE-2025-32434). Môi trường có sẵn trước đó thường cài torch 2.5.x.

**Khắc phục:**
```bash
pip uninstall torch torchvision -y
pip install "torch>=2.6" torchvision --index-url https://download.pytorch.org/whl/cpu
```
Dùng `--index-url .../cu118` hoặc `.../cu121` thay cho `/cpu` nếu cần tăng tốc OCR bằng GPU NVIDIA (xem `install.bat` hoặc mục "OCR Chạy Chậm" bên dưới) — không bắt buộc phải có GPU để chạy embedding, `bge-m3` được cấu hình chạy CPU mặc định trong cả 4 nơi dùng đến nó (`rag_engine.py` + 3 file `import_*_engine.py`).

---

### Lỗi Không Kết Nối Groq

**Triệu chứng:** Câu trả lời trả về `❌ Lỗi hệ thống.`

**Kiểm tra:**
- File `groqkey.txt` tồn tại và chứa API key hợp lệ (bắt đầu bằng `gsk_`)
- Có kết nối Internet
- API key chưa hết hạn / hết quota tháng

**Lưu ý:** Groq có tự động retry 3 lần (5s / 10s / 15s) khi gặp lỗi rate limit / timeout.

---

### ChromaDB Trống — Không Tìm Thấy Kết Quả

**Triệu chứng:** `⚠️ Không tìm thấy thông tin đủ liên quan trong cơ sở dữ liệu.`

**Nguyên nhân:** Chưa có dữ liệu trong ChromaDB.

**Khắc phục:**
- Import trực tiếp từ file PDF/DOCX qua giao diện giáo viên (xem mục 6)
- Hoặc import bộ tình huống DOCX (xem mục 7)
- Hoặc import nhanh từ dataset Excel có sẵn qua giao diện (xem mục 8)

> ⚠️ **Hạn chế dùng** các script rời trong `database/` (`build_db_from_pdf.py`, `build_db_from_dataset_updated.py`, `build_db_doc.py`...) để nạp dữ liệu — đây là script bootstrap cũ, không được bảo trì thường xuyên như giao diện Import. Ngày 2026-07-25 đã rà soát và đổi toàn bộ embedding hardcode trong các script này từ `bge-small-en-v1.5` sang `bge-m3` cho khớp với `chroma_db` hiện tại (1024 chiều), nhưng ưu tiên vẫn nên dùng giao diện Import (mục 6/7/8) — nơi được kiểm thử và cập nhật sát nhất với engine hiện hành.

---

### OCR Chạy Chậm

**Nguyên nhân:** Mặc định dùng CPU, chỉ kích hoạt khi PDF là bản scan. File 50 trang mất 10–30 phút.

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

**Triệu chứng:** Thông báo "Đăng nhập thất bại" hoặc "Tài khoản đã bị vô hiệu hóa"

**Kiểm tra:**
- Tên đăng nhập và mật khẩu phân biệt chữ hoa/thường
- Tài khoản mặc định (student/teacher/admin) chỉ được tạo khi bảng `users` **trống hoàn toàn** (lần khởi động đầu tiên) — nếu thiếu tài khoản admin, dùng Import tài khoản (mục 10) hoặc thêm thủ công vào bảng `users`
- Tài khoản bị Admin vô hiệu hóa sẽ không đăng nhập được cho tới khi được kích hoạt lại

---

### Đánh Giá RAG Dùng Sai Dataset

**Triệu chứng:** Kết quả đánh giá không phản ánh dataset mong muốn

**Nguyên nhân:** `/evaluate` đọc từ file `.xlsx` được chọn ở dropdown "File dataset dùng để đánh giá" (mục 9, bước 1), **không** phải từ dữ liệu vừa import vào ChromaDB qua mục 8 — đây là hai nguồn hoàn toàn khác nhau (file trên đĩa vs vector DB).

**Khắc phục:** Kiểm tra lại dropdown đã chọn đúng file mong muốn trước khi bấm Quick/Full Evaluation. Nếu file mới không xuất hiện trong dropdown, xác nhận file `.xlsx` đã nằm trong thư mục **`Dataset/`** (không phải thư mục gốc dự án) và có ít nhất một sheet đặt tên đúng quy ước `Dataset_*` hoặc `Demo_*`.

---

### Quick Evaluation Bị Disable / Báo Lỗi "Không Có Sheet Demo"

**Triệu chứng:** Nút Quick Evaluation bị mờ, hoặc chạy báo `❌ File '...' không có sheet Demo…`

**Nguyên nhân:** File dataset đang chọn chỉ có sheet `Dataset_*` (dùng được cho Full Evaluation) nhưng thiếu sheet `Demo_*`.

**Khắc phục:** Chọn file khác có sẵn sheet `Demo_*` trong dropdown, hoặc thêm một sheet đặt tên `Demo_<số>` vào file Excel đó rồi tải lại trang.

---

### Trả Lời Sai Nội Dung / Trích Dẫn Sai Điều Luật (VD: hỏi "Điều 143" ra nội dung Điều khác)

**Triệu chứng:** Đặt câu hỏi nêu rõ số Điều (VD: "Điều 143") hoặc một từ khóa ngắn (VD: "Tập đoàn") nhưng câu trả lời/trích dẫn không khớp với Điều luật thực sự nói về nội dung đó.

**Nguyên nhân đã phát hiện (2026-07-06):** Dữ liệu văn bản 67/VBHN-VPQH trong ChromaDB từng bị import bằng `database/build_db_doc.py` với regex tách "Điều X." gõ nhầm ký tự (`Dieu` ASCII thay vì `Điều` tiếng Việt) — regex này không bao giờ khớp, khiến toàn bộ văn bản bị cắt cứng thành từng đoạn 3000 ký tự bất kể ranh giới Điều luật, và metadata `article_number` chỉ là số thứ tự đoạn (không phải số Điều thật). Hệ quả: một đoạn có thể chứa nội dung của 2 Điều liền kề, và tra cứu theo số Điều/từ khóa ngắn trả về sai.

**Đã khắc phục:**
- Sửa regex trong `database/build_db_doc.py` và `engine/import_law_engine.py` để khớp đúng "Điều X." (ký tự Đ tiếng Việt)
- Cả 2 script giờ **cảnh báo rõ ràng** thay vì âm thầm gán số Điều giả nếu vẫn rơi vào fallback
- `engine/rag_engine.py` được thêm 2 cơ chế truy xuất mới: (1) tra thẳng theo metadata khi câu hỏi có dạng "Điều N", (2) quét từ khóa trực tiếp cho câu hỏi ngắn (≤5 từ, VD "Tập đoàn") — thay vì chỉ dựa vào tìm kiếm ngữ nghĩa

**Nếu ChromaDB hiện tại vẫn còn dữ liệu luật bị chunk sai** (dấu hiệu: metadata `article_number` trùng với `segment_index + 1`), có thể chạy `python database/rebuild_law_from_docx.py` để build lại đúng theo từng Điều từ file DOCX gốc — script này đã dùng đúng embedding `BAAI/bge-m3` (đã sửa ngày 2026-07-25), khớp với phần dữ liệu còn lại trong `chroma_db`.

---

### Điểm Đánh Giá `auto` Mode Thấp Bất Thường Dù Câu Trả Lời Đúng

**Triệu chứng:** Chạy Quick Evaluation, nhiều câu có `citation_correct = 0` và `hallucination` thấp dù xem thủ công thấy câu trả lời **trích dẫn đúng** điều luật yêu cầu.

**Nguyên nhân đã phát hiện (2026-07-21):** `_auto_score()` trong `engine/evaluate_engine.py` từng lấy số Điều bằng cách xoá hết ký tự không phải số trong toàn bộ chuỗi `article_reference` — với tham chiếu chỉ có một số (VD `"Điều 17"`) thì đúng, nhưng với tham chiếu có nhiều số (VD `"Khoản 35 Điều 4"` → gộp thành `"354"`, `"Điều 17 Nghị định 168/2025/NĐ-CP"` → gộp thành `"171682025"`) thì số bị gộp sai hoàn toàn và không bao giờ khớp được với văn bản trả lời thật, dù trả lời đúng 100%. Lỗi này ảnh hưởng 23/50 câu (46%) trong bộ dữ liệu `enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx`, kéo điểm tổng từ mức thực tế ~73/100 xuống còn 66.7/100.

**Đã khắc phục:** Đổi sang trích riêng số theo sau từ khoá "Điều" (`_extract_article_numbers`), có fallback theo số hiệu văn bản/nghị định khi tham chiếu không chứa "Điều" (VD chỉ có `"76/2025/QH15"`). Xác minh trên dữ liệu thật: 14/50 câu chuyển từ chấm sai (0 điểm) sang chấm đúng (3 điểm), không có câu nào bị chấm sai theo chiều ngược lại.

**Nếu vẫn thấy điểm bất thường:** Kiểm tra định dạng cột `article_reference` trong file dataset — chấm điểm chỉ nhận diện được số Điều đứng ngay sau từ "Điều" (không phân biệt hoa/thường), các dạng viết tắt khác (VD chỉ ghi số Điều mà không có chữ "Điều") sẽ không được nhận diện.

**Đợt sửa tiếp theo (2026-07-25/26) — riêng chỉ số `hallucination` và `clarity` bị chấm oan:**

- **`hallucination` đếm trùng lặp:** câu trả lời hợp lệ nhắc lại cùng một số Điều ở cả "Căn cứ pháp lý" lẫn phần "Nguồn tham khảo" footer (xem mục 13.7) — bản cũ đếm theo `list` nên mỗi lần lặp lại bị trừ điểm thêm một lần. Đã đổi sang đếm theo `set` (không trùng lặp).
- **`hallucination` phạt oan các Điều bổ sung hợp lệ:** một câu trả lời "thành lập X" đúng đắn thường trích dẫn nhiều Điều cùng lúc (hồ sơ + trình tự + định nghĩa — xem `build_legal_basis_line()` mục 13.7), nhưng cột `article_reference` trong dataset chỉ liệt kê **một** Điều làm đáp án mẫu, khiến các Điều còn lại — dù đúng — vẫn bị tính là "hallucination". Đã sửa: một số Điều chỉ bị coi là hallucination nếu **không xuất hiện trong `retrieved_context` thật** (ngữ cảnh RAG đã truy xuất cho câu hỏi đó) — cùng nguyên tắc `validate_answer_citations()` đang dùng trong app thật (mục 13.6). Đồng thời cột `expected_retrieved_context` (nếu dataset có điền) cũng được cộng vào tập "đúng" khi chấm `citation_correct`/`hallucination`.
- **`clarity` tính nhầm độ dài footer:** word-count trước đây tính luôn cả phần "📖 Nguồn chính / 📎 Nguồn tham khảo" (không phải văn xuôi, là metadata trích dẫn hệ thống tự thêm) vào tổng số từ, khiến câu trả lời có nhiều nguồn tham khảo dễ vượt trần 200 từ và bị trừ điểm oan. Đã sửa: chỉ đếm phần thân câu trả lời, bỏ qua mọi thứ từ "📖 Nguồn chính:" trở đi.
- **Kết quả đo thực tế trên `Demo_30`+`Demo_50` (auto mode):** 62.2% → 65.0% → 71.5% → 81.8% → **88.5%** qua các đợt sửa trên (mỗi đợt đo lại bằng Quick Evaluation thật, không ước lượng) — riêng bước cuối (chấm hallucination dựa trên `retrieved_context` grounded, không chỉ dựa vào 1 đáp án mẫu duy nhất) đưa `hallucination` từ 1.52/3 lên 2.86/3.

---

### [LỊCH SỬ, KHÔNG CÒN ÁP DỤNG] Import Dataset Tạo Đoạn Trùng Khi Nhập Lại Cùng File

**Đã sửa 2026-07-28 sáng, rồi toàn bộ pipeline liên quan bị bỏ luôn 2026-07-28 chiều** (xem mục ngay dưới đây) — Import Dataset không còn nạp gì vào ChromaDB nữa, nên lỗi trùng đoạn kiểu này **không còn khả năng xảy ra**. Giữ lại đoạn này chỉ để ghi nhớ bối cảnh: `run_import_dataset()` từng lọc trùng theo `doc_id` hoặc theo Số ký hiệu + nhãn `"KB_Articles"`, nhưng sheet `Legal_Update_2025` không khớp điều kiện nào nên bị trùng mỗi lần nhập lại — chính triệu chứng này dẫn tới việc rà lại toàn bộ pipeline và phát hiện ra rủi ro data leakage nghiêm trọng hơn ở mục dưới.

---

### Bộ Câu Hỏi Full Evaluation Từng Bị Lẫn Vào ChromaDB (data leakage) — ĐÃ KHẮC PHỤC 2026-07-28

**Triệu chứng đã quan sát trước khi sửa:** Điểm Full Evaluation (mục 9) cao bất thường hoặc dao động mạnh giữa các lượt chạy trên cùng 1 bộ câu hỏi.

**Nguyên nhân (phát hiện 2026-07-28, xác minh trực tiếp trên ChromaDB đang chạy):** File dataset dùng để Full Evaluation (sheet `Dataset_150`/`Dataset_200`) cũng chính là file được import vào ChromaDB qua mục 8 kiểu cũ. Kết quả: 200 dòng câu hỏi–đáp án của `Dataset_200` từng nằm trong ChromaDB dưới dạng đoạn chứa nguyên văn `"Câu hỏi: ... / Trả lời: ..."`. Khi chạy Full Evaluation, hệ thống có thể vô tình truy xuất trúng chính đáp án mẫu thay vì tự suy luận từ luật gốc — làm điểm bị thổi phồng ở những câu bị trùng.

**Đã khắc phục dứt điểm (không phải vá riêng lẻ):** Xoá toàn bộ 310 đoạn có `import_source="dataset"` khỏi ChromaDB (gồm cả `KB_Articles_Updated`/`Legal_Update_2025` lẫn `Dataset_200`), rồi viết lại hẳn `import_dataset_engine.py` — Import Dataset giờ **không bao giờ** nạp gì vào ChromaDB nữa, chỉ lưu file vào `Dataset/` + track tên file trong bảng `dataset_file` (`chat.db`, mục 13.5) để phục vụ riêng Quick/Full Evaluation (mục 9). Xem mục 8 để biết luồng mới. Muốn có nội dung luật thật trong ChromaDB, dùng **Import Văn bản luật** (mục 6) — không dùng Import Dataset nữa.

**Xác minh sau khi sửa:** ChromaDB giảm từ 743 → 433 đoạn (166 NĐ 168/2025 + 245 VBHN 67 + 2 BLDS + 20 tình huống — không còn đoạn dataset nào); `evaluate/retrieval_regression_tests.py` vẫn 4/4 pass; Quick/Full Evaluation (mục 9) vẫn hoạt động bình thường vì nó luôn đọc trực tiếp từ file `.xlsx` trên đĩa, không phụ thuộc ChromaDB.

---

## Ghi Chú Thêm

- Hệ thống hỗ trợ **đa phiên đồng thời** — nhiều người dùng có thể truy cập cùng lúc
- Dữ liệu chat được **lưu vĩnh viễn** trong `chat.db`; không mất khi restart
- ChromaDB **tích lũy dữ liệu** — import thêm văn bản/tình huống/dataset mới không xóa dữ liệu cũ, trừ khi admin chủ động xoá qua trang Manage Law (mục 11)
- Mọi câu trả lời đều kèm **📖 trích dẫn điều luật chính** và có thể có **📎 nguồn tham khảo phụ** + **🔗 link nguồn**
- Hệ thống có cơ chế **retry tự động** khi Groq bị rate limit (3 lần, backoff 5s/10s/15s)
- Admin quản lý được vòng đời tài khoản (kích hoạt/vô hiệu hóa/xoá), import tài khoản hàng loạt kèm báo cáo lỗi chi tiết, và quản lý/xoá dữ liệu đã import theo cả 3 luồng (văn bản luật, dataset, tình huống)
- *(mới 2026-07-28)* Chấm điểm ưu tiên nguồn giờ **mở rộng được qua giao diện** (bảng `keyword`/`source_keyword`, mục 13.8) thay vì phải sửa code mỗi khi có văn bản luật mới — Giáo viên cũng được xem/sửa từ khóa của văn bản (không xoá được); Admin quản lý thêm tab Từ khóa riêng, dùng chung cơ chế cho cả danh sách chặn câu hỏi ngoài phạm vi (mục 13.9)
