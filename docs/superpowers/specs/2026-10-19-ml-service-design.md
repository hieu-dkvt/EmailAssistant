# Spec: ML service (embedding bge-m3, rerank)

Ngày: 2026-10-19 · Thành phần: `ml-service/` (`app/main.py`, `app/embedder.py`, `app/reranker.py`, `app/config.py`, `app/run.py`) · Trạng thái: xong (sửa sau review bảo mật: mục 9)

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Bản đầu của service có thêm nhận dạng tên riêng (NER) phục vụ việc che; đã bỏ ở [spec 28](2026-11-01-internal-model-design.md) (2026-11-02). Số đo bộ nhớ và thời gian khởi động hiện tại: spec 28 mục 10 và `ml-service/CLAUDE.md`. Tài liệu viết lại theo code hiện tại ngày 2026-11-05.

## 1. Mục đích

Service nội bộ chạy model trên máy mình, phục vụ backend (API và worker):

- **Embedding** cho tìm kiếm ngữ nghĩa (spec 18, 19) và gợi ý thread liên quan (spec 23): model `BAAI/bge-m3` (đa ngôn ngữ Nhật/Việt/Anh, 1024 chiều).
- **Rerank** cho hỏi đáp (spec 27 mục 7), mặc định tắt.

Embedding và rerank luôn chạy self-host: nội dung mail không ra khỏi máy.

## 2. Không làm

- Reranker bật sẵn: chạy CPU chậm (spec 27 mục 10). `ML_RERANK_MODEL` rỗng thì không nạp, `/rerank` trả 503 `rerank_disabled`; chỉ `/ask` dùng khi `PMM_RERANK=1`.
- Vector sparse/ColBERT của bge-m3: search từ khóa đã có PGroonga.
- Xác thực giữa backend và ml-service: chỉ lắng nghe `127.0.0.1` và kiểm Host (mục 5). Khi tách máy (giai đoạn 7) cần thêm.

## 3. API

| Endpoint | Vào | Ra |
|---|---|---|
| `GET /health` | — | `{status, embed_model, dim, max_seq_length, rerank_model}` (`rerank_model` null khi tắt) |
| `POST /embed` | `{texts: [str]}`: 1–64 text, mỗi text ≤ 10.000 ký tự | `{model, dim, vectors}`: vector đã chuẩn hóa (độ dài 1), dùng khoảng cách cosine |
| `POST /rerank` | `{query: str, texts: [str]}`: query 1–2.000 ký tự, 1–64 text, mỗi text ≤ 10.000 ký tự | `{model, scores}`: điểm từng text theo thứ tự vào. 503 `rerank_disabled` khi `ML_RERANK_MODEL` rỗng |

- Body tối đa 8 MiB theo `Content-Length` (413). Lỗi 422 chỉ trả vị trí và loại lỗi, **không trả lại text** (mặc định FastAPI trả cả input). Lỗi khi suy luận được bắt ngay trong endpoint, trả 500 `{"detail": "Lỗi ml-service"}`, log chỉ có tên lớp lỗi (handler `Exception` của Starlette vẫn để uvicorn log cả traceback, mà thông điệp lỗi của tokenizer có thể chứa mẩu text).
- OpenTelemetry bật sẵn của FastAPI bị tắt (như backend): service này nhận text mail gốc.
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
| `ML_RERANK_MODEL` / `ML_RERANK_MAX_LENGTH` | rỗng / `512` | reranker (spec 27 mục 7) |
| `ML_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host được nhận |

- Embedding: `SentenceTransformer(model, device="cpu")`, `normalize_embeddings=True`. Model tải từ Hugging Face lúc khởi động lần đầu rồi đọc từ cache cục bộ; `HF_HUB_DISABLE_TELEMETRY=1`. Model đã có trong cache thì `python -m app.run` tự đặt `HF_HUB_OFFLINE=1` (không hỏi Hugging Face nữa). Text đầu vào không gửi đi đâu.
- Python ghim `<3.13` trong `pyproject.toml` (PyTorch, sentence-transformers) cho tới khi kiểm lại trên 3.13.

## 5. Phân quyền và dữ liệu

- Lắng nghe `127.0.0.1` (`python -m app.run`), chỉ nhận Host trong `ML_ALLOWED_HOSTS`: trang web lạ mở trong trình duyệt không gọi được qua DNS rebinding. Request JSON từ origin khác bị CORS chặn (không có header CORS).
- Không lưu text, không ghi text vào log. Log chỉ có: `embed n=… ms=…`, `rerank n=… ms=…`, `models loaded ms=…`, `error type=…`.
- Không có dữ liệu người dùng nên không có RLS; backend chịu trách nhiệm chỉ gửi text của người dùng đang được phục vụ.

## 6. Kiểm thử

- API với model giả: `/health`, `/embed`, `/rerank`, giới hạn số text và độ dài (422), lỗi 422 không chứa input, body quá lớn (413), Host lạ (400), `/docs` tắt.

## 7. Giới hạn đã biết

- Máy dev không có GPU: embed chạy CPU (số đo ở mục 8).

## 8. Kết quả đo

Ngày 2026-10-20, máy dev (Intel i5-1235U 10 nhân, 7,7 GB RAM, không GPU, Windows; lúc đo còn trống 0,5–1,6 GB RAM vì Docker Desktop, VS Code), text giả. Số khởi động và bộ nhớ đo lại sau spec 28: `ml-service/CLAUDE.md`.

| Việc | Kết quả |
|---|---|
| Embed đoạn 800 ký tự (~300 token) | 0,24–0,28 đoạn/giây (4 hoặc 10 luồng) |
| Embed đoạn 300 ký tự | 0,8 đoạn/giây |
| Embed câu tìm kiếm ngắn | khoảng 0,37 giây (trung vị) |
| Lượng tử hóa int8 động (`quantize_dynamic`) | không nhanh hơn rõ rệt (0,21–0,36 đoạn/giây): không dùng |
| Token bge-m3 / ký tự trên 1.050 mail giả | 0,37 (p90 0,41, cao nhất 0,57) |

Hệ quả: embed lần đầu trên CPU này khoảng 0,6–0,7 mail/giây với mail giả (mail ngắn); vài nghìn mail mất vài giờ chạy nền, vài trăm nghìn mail mất nhiều ngày. Máy có GPU (hoặc model nhỏ hơn: phải hỏi người dùng vì đổi model là embed lại toàn bộ) sẽ nhanh hơn nhiều lần.

## 9. Sửa sau review bảo mật (2026-10-21)

- Telemetry OpenTelemetry của FastAPI chưa tắt: đã tắt.
- Lỗi khi suy luận lan tới handler `Exception` vẫn bị uvicorn log traceback (có thể chứa text): nay bắt trong endpoint.
- Mỗi lần khởi động vẫn hỏi Hugging Face: tự đặt `HF_HUB_OFFLINE=1` khi model đã có trong cache. Vòng 2: bản đầu import `huggingface_hub` để kiểm cache nên biến đặt sau không còn tác dụng (thư viện đọc biến lúc import); nay kiểm bằng đường dẫn file `HF_HUB_CACHE` / `HF_HOME/hub/models--…/snapshots/*/config.json` (`model_in_cache`).
- Để sau: giới hạn body chỉ theo `Content-Length` (request chunked đi qua được; chỉ tiến trình trên cùng máy gọi được nên rủi ro là tốn bộ nhớ).
