# ML service (Python)

Service nội bộ chạy model trên máy mình, phục vụ backend (api và worker): embedding (`bge-m3`) cho tìm kiếm ngữ nghĩa, embed nền và embed câu hỏi của hỏi đáp; rerank (tùy chọn, mặc định tắt) cho hỏi đáp. Không lưu dữ liệu, không gửi dữ liệu ra ngoài. Spec: `docs/superpowers/specs/2026-10-19-ml-service-design.md` (spec 17); `/rerank`: spec 27.

Từ spec 28 (2026-11-02) service không còn nhận dạng tên riêng (NER): AI chạy trên model nội bộ nên mail gửi đi không che (quyết định của người dùng 2026-11-01). Dịch mail không gọi ml-service nữa.

## Công nghệ

Python 3.12, uv, FastAPI, PyTorch 2.14 (bản CPU của PyPI trên Windows), sentence-transformers 6.1. Phiên bản Python ghim `>=3.12,<3.13` trong `pyproject.toml` vốn vì GiNZA/spaCy chưa chắc có wheel 3.13; spaCy, GiNZA đã gỡ nên lý do này hết, giữ nguyên ghim cho tới khi có người kiểm lại torch và sentence-transformers trên 3.13.

## Cấu trúc

```
app/config.py    # Settings từ biến môi trường ML_* (model, max_seq_length, batch, số luồng, Host,
                 #   rerank_model, rerank_max_length)
app/embedder.py  # SentenceTransformerEmbedder: vector chuẩn hóa, khóa một lần suy luận
app/reranker.py  # CrossEncoderReranker (sentence_transformers.CrossEncoder, CPU), khóa riêng, import trễ
app/main.py      # create_app(load_embedder, settings, load_reranker): /health, /embed, /rerank;
                 #   Host, giới hạn body, lỗi 422/500 không trả lại text
app/run.py       # python -m app.run [--port 8001]: uvicorn 127.0.0.1, tắt telemetry Hugging Face
tests/           # test_api.py (model giả; /ner trả 404; app không import spaCy; /rerank), test_run.py
```

## API

- `POST /embed` `{texts}` (1–64 text, ≤ 10.000 ký tự) → `{model, dim, vectors}` (vector chuẩn hóa, cosine).
- `POST /rerank` `{query, texts}` (query 1–2.000 ký tự, 1–64 text, mỗi text ≤ 10.000 ký tự) → `{model, scores}`, `scores` cùng thứ tự `texts` (điểm cao hơn là liên quan hơn). `ML_RERANK_MODEL` rỗng (mặc định) thì không nạp model và trả 503 `{"detail": "rerank_disabled"}`. Chỉ backend gọi, khi `PMM_RERANK=1` (spec 27 mục 7).
- `GET /health` → tên model, số chiều, `max_seq_length`, `rerank_model` (null khi tắt).
- `POST /ner` đã bỏ (404).

## Quy tắc

- Model embedding mặc định `BAAI/bge-m3`, đặt bằng `ML_EMBED_MODEL`; mọi response kèm tên model. Backend chỉ dùng vector của model trùng `PMM_EMBED_MODEL`. **Đổi model nghĩa là embed lại toàn bộ: hỏi người dùng trước.**
- Model tải từ Hugging Face lúc khởi động lần đầu (bge-m3 khoảng 2,3 GB vào cache `~/.cache/huggingface`). Text đầu vào không gửi tới dịch vụ nào.
- Không ghi text đầu vào vào log; log chỉ số lượng và thời gian. Lỗi 422 của FastAPI mặc định trả lại input: đã thay bằng handler chỉ trả `loc`, `type`.
- Chỉ lắng nghe `127.0.0.1`, chỉ nhận Host trong `ML_ALLOWED_HOSTS`; không có CORS. Telemetry OpenTelemetry bật sẵn của FastAPI phải tắt (`create_app` truyền `telemetry={...False}`).
- Lỗi khi suy luận bắt ngay trong endpoint (`failed()`), không để lan tới handler `Exception`: uvicorn sẽ log traceback, mà thông điệp lỗi của tokenizer có thể chứa text.
- Model đã có trong cache thì `app.run` tự đặt `HF_HUB_OFFLINE=1`. Khi cấu hình `ML_RERANK_MODEL`, chỉ đặt offline khi **cả hai** model (embedding và rerank) đã có trong cache; nếu không, lần đầu bật reranker sẽ không tải được model (`can_go_offline` trong `app/run.py`).
- Reranker mặc định tắt. Bật: `$env:ML_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"` (cache 2,2 GB, tải ở lần chạy đầu), tùy chọn `ML_RERANK_MAX_LENGTH` (mặc định 512). Đo 2026-11-03 trên CPU 6 nhân: khoảng 1,1 giây mỗi đoạn 800 ký tự (40 đoạn 45 giây), bộ nhớ private 5,8 GB khi nạp cả bge-m3 (spec 27 mục 10). `/rerank` dùng chung quy tắc không log text và lỗi qua `failed()` như `/embed`.

## Lệnh

Chạy từ thư mục gốc `Email Assistant`.

| Việc | Lệnh |
|---|---|
| Cài (mạng chậm: `$env:UV_HTTP_TIMEOUT = "900"`). Dừng ml-service đang chạy trước: Windows khóa file `.pyd` của venv | `uv sync --directory ml-service` |
| Chạy (127.0.0.1:8001; lần đầu tải model, các lần sau tự offline) | `uv run --directory ml-service python -m app.run` |
| Test | `uv run --directory ml-service pytest` (dùng `uv run --no-sync` nếu ml-service đang chạy từ venv này) |
| Lint, format, kiểm tra kiểu | `uv run --directory ml-service ruff check .` · `ruff format .` · `pyright` |

## Hiệu năng trên máy dev

Đo lại 2026-11-02 sau khi gỡ NER (spec 28): i5-8500 (6 nhân, 3,0 GHz), 15,8 GB RAM, không GPU, Windows 11; cổng 8002 song song với ml-service của người dùng; `uv run --no-sync`, cache model đã có. Venv lúc đo vẫn còn spaCy cài sẵn (chưa `uv sync` được vì file bị khóa) nhưng không có code nào import; test `test_no_spacy_import` kiểm điều này.

- Khởi động tới khi `/health` trả lời: khoảng 39 giây, trong đó nạp model khoảng 38 giây (`ml models loaded ms=37891`).
- Bộ nhớ private của process: khoảng 3,2 GB sau khi nạp model, 3.250 MB sau lần `/embed` đầu (5 câu ngắn, lần đầu 4,3 giây, lần hai 0,3 giây). Số cũ khoảng 4 GB (kèm NER) đo trên máy khác (i5-1235U, 7,7 GB RAM, spec 17 mục 8): hai số đo trên hai máy, venv lúc đo mới vẫn còn spaCy cài sẵn, nên không so sánh được và chưa biết gỡ NER bớt bao nhiêu.
- Tốc độ embed: 2026-11-03 trên máy dev hiện tại (6 nhân), profile giả 1.050 đoạn ngắn mất 463 giây (khoảng 2,3 đoạn/giây, lô 16 đoạn khoảng 8 giây); chưa đo lại với đoạn 800 ký tự. Số cũ (i5-1235U, 7,7 GB RAM, spec 17 mục 8): khoảng 0,3 đoạn/giây với đoạn 800 ký tự, 0,8 đoạn/giây với 300 ký tự (giới hạn bởi tính toán CPU; lượng tử hóa int8 động hay đổi số luồng không nhanh hơn rõ rệt); câu tìm kiếm khoảng 0,4 giây.
