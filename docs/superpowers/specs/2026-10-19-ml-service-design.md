# Spec: ML service (embedding bge-m3, nhận dạng tên riêng)

Ngày: 2026-10-19 · Thành phần: `ml-service/` (`app/main.py`, `app/embedder.py`, `app/ner.py`, `app/config.py`, `app/run.py`) · Trạng thái: xong (sửa sau review bảo mật: mục 9)

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). NER chỉ phục vụ bước che nên bỏ cùng `Masker` ở [spec 28](2026-11-01-internal-model-design.md). Embedding vẫn self-host.

## 1. Mục đích

Service nội bộ chạy model trên máy mình, phục vụ backend (API và worker):

- **Embedding** cho tìm kiếm ngữ nghĩa (spec 18, 19) và gợi ý thread liên quan (spec 23): model `BAAI/bge-m3` (đa ngôn ngữ Nhật/Việt/Anh, 1024 chiều).
- **Nhận dạng tên riêng (NER)** cho bước che thông tin trước khi gửi Claude (spec 20): tên người, tổ chức, địa chỉ.

Theo nguyên tắc NDA, embedding và NER luôn chạy self-host: nội dung mail không ra khỏi máy.

## 2. Không làm

- Reranker (`/rerank`): chạy CPU quá chậm (vài giây cho 50 đoạn). Để sau khi có GPU hoặc khi eval cho thấy cần.
- Vector sparse/ColBERT của bge-m3: search từ khóa đã có PGroonga.
- NER tiếng Việt: không có model tốt chạy cục bộ; backend che tên tiếng Việt bằng từ điển người gửi/nhận và mẫu (spec 20).
- Xác thực giữa backend và ml-service: chỉ lắng nghe `127.0.0.1` và kiểm Host (mục 5). Khi tách máy (giai đoạn 7) cần thêm.

## 3. API

| Endpoint | Vào | Ra |
|---|---|---|
| `GET /health` | — | `{status, embed_model, dim, max_seq_length, ner_models}` |
| `POST /embed` | `{texts: [str]}`: 1–64 text, mỗi text ≤ 10.000 ký tự | `{model, dim, vectors}`: vector đã chuẩn hóa (độ dài 1), dùng khoảng cách cosine |
| `POST /ner` | `{texts: [str]}`: 1–64 text, mỗi text ≤ 50.000 ký tự | `{models, entities}`: mỗi text một danh sách `{start, end, label}`, vị trí theo code point, `label` ∈ `PERSON`, `ORG`, `ADDRESS`, `PHONE`, `EMAIL`, `URL`, `MONEY` |

- Body tối đa 8 MiB theo `Content-Length` (413). Lỗi 422 chỉ trả vị trí và loại lỗi, **không trả lại text** (mặc định FastAPI trả cả input). Lỗi khi suy luận được bắt ngay trong endpoint, trả 500 `{"detail": "Lỗi ml-service"}`, log chỉ có tên lớp lỗi (handler `Exception` của Starlette vẫn để uvicorn log cả traceback, mà thông điệp lỗi của tokenizer/Sudachi có thể chứa mẩu text).
- OpenTelemetry bật sẵn của FastAPI bị tắt (như backend): service này nhận text **chưa che**.
- Lúc khởi động kiểm model NER có các nhãn cần (`Person`, `Company` của GiNZA; `PERSON`, `ORG` của spaCy en); đặt sai model thì không chạy (nếu không, NER lặng lẽ không nhận ra ai).
- Mọi response kèm tên model để backend lưu cùng dữ liệu (đổi model = embed lại toàn bộ, phải hỏi người dùng).
- `/docs`, `/redoc` tắt.
- Endpoint là hàm đồng bộ (FastAPI chạy ở threadpool); mỗi model có khóa: một lần suy luận tại một thời điểm, giữ bộ nhớ ổn định trên máy ít RAM.

## 4. Model và cấu hình

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `ML_EMBED_MODEL` | `BAAI/bge-m3` | model sentence-transformers |
| `ML_MAX_SEQ_LENGTH` | `512` | số token tối đa mỗi text, phần dư bị cắt (backend cắt đoạn ngắn hơn mức này, spec 18) |
| `ML_BATCH_SIZE` | `8` | |
| `ML_TORCH_THREADS` | `0` (PyTorch tự chọn) | |
| `ML_NER_JA` / `ML_NER_EN` | `ja_ginza` / `en_core_web_sm` | model spaCy |
| `ML_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host được nhận |

- Embedding: `SentenceTransformer(model, device="cpu")`, `normalize_embeddings=True`. Model tải từ Hugging Face lúc khởi động lần đầu rồi đọc từ cache cục bộ; `HF_HUB_DISABLE_TELEMETRY=1`. Model đã có trong cache thì `python -m app.run` tự đặt `HF_HUB_OFFLINE=1` (không hỏi Hugging Face nữa). Text đầu vào không gửi đi đâu.
- NER chọn model theo chữ viết của **từng dòng** (`language_blocks`, các dòng liền nhau cùng ngôn ngữ chạy chung một lần; dòng không có chữ đi theo khối đang mở):
  - có kana/kanji → GiNZA (`ja_ginza`, spaCy + SudachiPy; chỉ chạy `tok2vec` + `ner`, tắt `parser`, `morphologizer`, `attribute_ruler`, `compound_splitter`, `bunsetu_recognizer`: đo trên text giả cho kết quả NER như nhau);
  - không có chữ Nhật, tỷ lệ chữ cái riêng của tiếng Việt (ă, â, đ, ơ, ư, nguyên âm có dấu thanh…) > 10% trên dòng (`VI_RATIO_LINE`) → coi là tiếng Việt, **không chạy NER**;
  - còn lại → `en_core_web_sm`.
- Nhãn GiNZA (ENE, đã kiểm danh sách nhãn của `ja_ginza` 5.3) quy về: `Person` → `PERSON`; `Company`, `Company_Group`, `Corporation_Other`, `Organization_Other`, `Government`, `Public_Institution`, `School`, `Research_Institute`… → `ORG`; `Postal_Address` → `ADDRESS`; `Phone_Number`, `Email`, `URL`, `Money` → `PHONE`, `EMAIL`, `URL`, `MONEY` (lớp bổ sung cho mẫu regex của backend) (bảng `JA_LABELS`). Không che: tỉnh/thành phố, ngày, số lượng, `ID_Number` (hay gặp ở mã lỗi như `E1024`, Claude cần để trả lời), `Position_Vocation`. spaCy en: `PERSON`, `ORG`, `MONEY`.
- Sudachi từ chối input dài hơn khoảng 49 KB: text được chia thành đoạn ≤ 10.000 ký tự (cắt ở cuối dòng nếu được), vị trí cộng lại theo đoạn.
- Python 3.12 (GiNZA 5.3, spaCy 3.8, PyTorch 2.14, sentence-transformers 6.1). Model `en_core_web_sm` 3.8.0 cài từ GitHub release của spaCy (không có trên PyPI).

## 5. Phân quyền và dữ liệu

- Lắng nghe `127.0.0.1` (`python -m app.run`), chỉ nhận Host trong `ML_ALLOWED_HOSTS`: trang web lạ mở trong trình duyệt không gọi được qua DNS rebinding. Request JSON từ origin khác bị CORS chặn (không có header CORS).
- Không lưu text, không ghi text vào log. Log chỉ có: `embed n=… ms=…`, `ner n=… entities=… ms=…`, `models loaded ms=…`, `error type=…`.
- Không có dữ liệu người dùng nên không có RLS; backend chịu trách nhiệm chỉ gửi text của người dùng đang được phục vụ.

## 6. Kiểm thử

- API với model giả: `/health`, `/embed`, `/ner`, giới hạn số text và độ dài (422), lỗi 422 không chứa input, body quá lớn (413), Host lạ (400), `/docs` tắt.
- NER thật (GiNZA, spaCy en) trên câu giả: tên người Nhật, tên người tiếng Anh, vị trí đúng sau khi chia đoạn, tiếng Việt bị bỏ qua; chọn ngôn ngữ; chia đoạn.

## 7. Giới hạn đã biết

- Máy dev không có GPU: embed chạy CPU (số đo ở mục 8).
- NER có thể sót hoặc nhận nhầm (vd. tên sản phẩm thành tổ chức). Backend kết hợp thêm từ điển và mẫu (spec 20); người dùng xem được bản đã che trước khi tin kết quả.
- Tiếng Việt không có NER.

## 8. Kết quả đo

Ngày 2026-10-20, máy dev (Intel i5-1235U 10 nhân, 7,7 GB RAM, không GPU, Windows; lúc đo còn trống 0,5–1,6 GB RAM vì Docker Desktop, VS Code), text giả:

| Việc | Kết quả |
|---|---|
| Nạp model (bge-m3 + GiNZA + spaCy en, từ cache) | khoảng 40 giây; process khoảng 4 GB bộ nhớ private (1,2 GB working set: Windows đẩy phần còn lại ra file trang) |
| Embed đoạn 800 ký tự (~300 token) | 0,24–0,28 đoạn/giây (4 hoặc 10 luồng) |
| Embed đoạn 300 ký tự | 0,8 đoạn/giây |
| Embed câu tìm kiếm ngắn | khoảng 0,37 giây (trung vị) |
| Lượng tử hóa int8 động (`quantize_dynamic`) | không nhanh hơn rõ rệt (0,21–0,36 đoạn/giây): không dùng |
| NER 21 đoạn 800 ký tự tiếng Nhật | 7–11 giây |
| Token bge-m3 / ký tự trên 1.050 mail giả | 0,37 (p90 0,41, cao nhất 0,57) |

Hệ quả: embed lần đầu trên CPU này khoảng 0,6–0,7 mail/giây với mail giả (mail ngắn); vài nghìn mail mất vài giờ chạy nền, vài trăm nghìn mail mất nhiều ngày. Máy có GPU (hoặc model nhỏ hơn: phải hỏi người dùng vì đổi model là embed lại toàn bộ) sẽ nhanh hơn nhiều lần.

## 9. Sửa sau review bảo mật (2026-10-21)

- NER chọn model theo cả text: một ký tự Nhật (vd. subject 【至急】) làm cả mail tiếng Anh chỉ chạy GiNZA; tỷ lệ chữ Việt > 2% làm đoạn tiếng Anh không chạy NER nào. Nay theo từng dòng (`language_blocks`).
- Telemetry OpenTelemetry của FastAPI chưa tắt: đã tắt.
- Lỗi khi suy luận lan tới handler `Exception` vẫn bị uvicorn log traceback (có thể chứa text): nay bắt trong endpoint.
- Model NER đặt sai làm NER lặng lẽ trả rỗng: kiểm nhãn lúc khởi động; backend coi kết quả không có model / sai dạng là lỗi.
- Mỗi lần khởi động vẫn hỏi Hugging Face: tự đặt `HF_HUB_OFFLINE=1` khi model đã có trong cache. Vòng 2: bản đầu import `huggingface_hub` để kiểm cache nên biến đặt sau không còn tác dụng (thư viện đọc biến lúc import); nay kiểm bằng đường dẫn file `HF_HUB_CACHE` / `HF_HOME/hub/models--…/snapshots/*/config.json` (`model_in_cache`).
- Vòng 2: chọn ngôn ngữ theo dòng với ngưỡng 2% làm dòng tiếng Anh có tên Việt có dấu ("ask John Smith and Nguyễn Văn Hiếu") bị coi là tiếng Việt và không chạy NER: ngưỡng theo dòng nâng lên 10% (`VI_RATIO_LINE`; dòng tiếng Việt thật ~15% trở lên).
- Để sau: giới hạn body chỉ theo `Content-Length` (request chunked đi qua được; chỉ tiến trình trên cùng máy gọi được nên rủi ro là tốn bộ nhớ). Ánh xạ nhãn địa điểm của spaCy en (GPE/FAC) sang địa chỉ.