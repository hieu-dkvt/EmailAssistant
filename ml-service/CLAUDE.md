# ML service (Python)

Service nội bộ chạy model trên máy mình, phục vụ backend (api và worker): embedding (`bge-m3`) cho tìm kiếm ngữ nghĩa và nhận dạng tên riêng (NER) cho bước che thông tin trước khi gửi cho AI. Không lưu dữ liệu, không gửi dữ liệu ra ngoài. Spec: `docs/superpowers/specs/2026-10-19-ml-service-design.md` (spec 17).

NER chỉ phục vụ bước che. Từ 2026-11-01 không cần che thông tin (AI chạy trên model nội bộ, quyết định của người dùng), nên NER bỏ cùng `Masker` ở spec 28.

## Công nghệ

Python 3.12 (GiNZA/spaCy chưa chắc có wheel 3.13), uv, FastAPI, PyTorch 2.14 (bản CPU của PyPI trên Windows), sentence-transformers 6.1, spaCy 3.8 + GiNZA 5.3 (`ja_ginza`, SudachiPy), `en_core_web_sm` 3.8.0 (cài từ GitHub release của spaCy, khai báo trong `[tool.uv.sources]`).

## Cấu trúc

```
app/config.py    # Settings từ biến môi trường ML_* (model, max_seq_length, batch, số luồng, Host)
app/embedder.py  # SentenceTransformerEmbedder: vector chuẩn hóa, khóa một lần suy luận
app/ner.py       # SpacyRecognizer: chọn model theo chữ viết (language_of), chia đoạn ≤ 10.000 ký tự,
                 #   bảng nhãn GiNZA (ENE) → PERSON/ORG/ADDRESS/PHONE/EMAIL/URL/MONEY
app/main.py      # create_app(load_embedder, load_recognizer): /health, /embed, /ner; Host, giới hạn body,
                 #   lỗi 422/500 không trả lại text
app/run.py       # python -m app.run [--port 8001]: uvicorn 127.0.0.1, tắt telemetry Hugging Face
tests/           # test_api.py (model giả), test_ner.py (GiNZA, spaCy thật trên câu giả)
```

## API

- `POST /embed` `{texts}` (1–64 text, ≤ 10.000 ký tự) → `{model, dim, vectors}` (vector chuẩn hóa, cosine).
- `POST /ner` `{texts}` (1–64 text, ≤ 50.000 ký tự) → `{models, entities}`: mỗi text một danh sách `{start, end, label}` (code point).
- `GET /health` → tên model, số chiều, `max_seq_length`, model NER.

## Quy tắc

- Model embedding mặc định `BAAI/bge-m3`, đặt bằng `ML_EMBED_MODEL`; mọi response kèm tên model. Backend chỉ dùng vector của model trùng `PMM_EMBED_MODEL`. **Đổi model nghĩa là embed lại toàn bộ: hỏi người dùng trước.**
- Model tải từ Hugging Face lúc khởi động lần đầu (bge-m3 khoảng 2,3 GB vào cache `~/.cache/huggingface`). Text đầu vào không gửi tới dịch vụ nào.
- Không ghi text đầu vào vào log; log chỉ số lượng và thời gian. Lỗi 422 của FastAPI mặc định trả lại input: đã thay bằng handler chỉ trả `loc`, `type`.
- Chỉ lắng nghe `127.0.0.1`, chỉ nhận Host trong `ML_ALLOWED_HOSTS`; không có CORS. Telemetry OpenTelemetry bật sẵn của FastAPI phải tắt (`create_app` truyền `telemetry={...False}`).
- Lỗi khi suy luận bắt ngay trong endpoint (`failed()`), không để lan tới handler `Exception`: uvicorn sẽ log traceback, mà thông điệp lỗi của tokenizer/Sudachi có thể chứa text.
- NER chọn model theo từng dòng (`language_blocks`), không theo cả text. NER tiếng Nhật: tắt `parser`, `morphologizer`… (chỉ cần `tok2vec` + `ner`). Sudachi từ chối input > ~49 KB: luôn đi qua `segments()`. Tiếng Việt không chạy NER. Lúc khởi động kiểm model NER có nhãn cần (`Person`, `Company` / `PERSON`, `ORG`).
- Model đã có trong cache thì `app.run` tự đặt `HF_HUB_OFFLINE=1`.
- Thêm nhãn NER mới thì sửa cả `NER_LABELS` ở `backend/app/ml_client.py` và loại che ở `backend/app/ai/masking.py`.

## Lệnh

Chạy từ thư mục gốc `Email Assistant`.

| Việc | Lệnh |
|---|---|
| Cài (mạng chậm: `$env:UV_HTTP_TIMEOUT = "900"`) | `uv sync --directory ml-service` |
| Chạy (127.0.0.1:8001; lần đầu tải model, các lần sau tự offline) | `uv run --directory ml-service python -m app.run` |
| Test | `uv run --directory ml-service pytest` (test NER nạp GiNZA thật, khoảng 10 giây) |
| Lint, format, kiểm tra kiểu | `uv run --directory ml-service ruff check .` · `ruff format .` · `pyright` |

## Hiệu năng trên máy dev (i5-1235U, 7,7 GB RAM, không GPU; spec 17 mục 8)

- Nạp model khoảng 40 giây; process chiếm khoảng 4 GB bộ nhớ (private).
- Embed: khoảng 0,3 đoạn/giây với đoạn 800 ký tự, 0,8 đoạn/giây với 300 ký tự (giới hạn bởi tính toán CPU; lượng tử hóa int8 động hay đổi số luồng không nhanh hơn rõ rệt). Câu tìm kiếm: khoảng 0,4 giây.
- NER: khoảng 7–11 giây cho 21 đoạn 800 ký tự tiếng Nhật.
