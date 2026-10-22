# Backend (Python)

API (FastAPI) và worker xử lý nền (Procrastinate) dùng chung một codebase. Nhận dữ liệu từ collector, làm sạch, gom thread, gọi AI, phục vụ search và web.

## Công nghệ

Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.0 + psycopg 3, `pgvector` (kiểu `HALFVEC` cho model), Alembic, Procrastinate, SDK `anthropic` 1.x (dùng `httpx2`), `httpx` (gọi ml-service). Model (bge-m3, GiNZA, spaCy) chạy ở `ml-service/`, backend không nạp model.

## Cấu trúc

Đã có:

```
app/config.py            # URL DB theo role (owner/api/worker), biến DATABASE_URL_* ghi đè giá trị dev;
                         #   ML_SERVICE_URL, PMM_EMBED_MODEL
app/db/models.py         # model SQLAlchemy: users, api_tokens, user_passwords, web_sessions, mail_sources,
                         #   messages (gồm index PGroonga ix_messages_search), message_ownership, threads,
                         #   thread_overrides, user_thread_state, message_chunks (halfvec + HNSW),
                         #   user_ai_settings, ai_calls
app/db/session.py        # create_api_engine (hide_parameters), user_session(engine, user_id): transaction + app.user_id (RLS)
app/main.py              # create_app (FastAPI, giới hạn body, tắt /docs /redoc, header bảo mật, mount web/dist)
app/run.py               # chạy uvicorn 127.0.0.1 (SelectorEventLoop trên Windows); access log bỏ query string
app/auth.py              # token collector pmm_..., băm SHA-256
app/web_auth.py          # mật khẩu web (scrypt), token phiên pmw_..., LoginThrottle (5 lần sai / 15 phút)
app/cli.py               # create-user / create-token / revoke-token / set-password (role owner);
                         #   process-mail, mail-stats (role worker), review-threads (role api + RLS)
app/api/deps.py          # xác thực Bearer token qua auth_token_user, đọc body có giới hạn + gunzip an toàn
app/api/web_deps.py      # phiên cookie qua web_session_user, header X-PMM-Request (CSRF), json_body() cho OpenAPI
app/api/routes.py        # API collector (chỉ token): /api/v1/me, /ingest, /sources, /sources/delete-mail
app/api/session_routes.py # đăng nhập web: POST/GET/DELETE /api/v1/session
app/api/web_routes.py    # API web (chỉ phiên): /folders, /search (mode=keyword|hybrid), /threads,
                         #   /threads/{id}, /threads/{id}/similar, /messages/{id}, /messages/{id}/full,
                         #   /processing, /thread-overrides
app/api/ai_routes.py     # API web AI (chỉ phiên): POST /ask, GET/PUT /ai/settings, GET /ai/usage
app/api/errors.py        # lỗi 422 không trả lại dữ liệu gửi lên; lỗi DB chỉ log sqlstate/ràng buộc;
                         #   ApiError → {"detail", "code"}; MlUnavailable / EmbeddingModelMismatch → 503
app/schemas.py           # schema Ingest API (hợp đồng với collector)
app/web_schemas.py       # schema API web (hợp đồng với web/, sinh TypeScript qua OpenAPI)
app/search/query.py      # dựng truy vấn Groonga từ ô tìm kiếm (AND, OR, -từ, "cụm"), không nhận cú pháp thô
app/search/highlight.py  # tô từ khóa (NFKC + lower), đoạn trích; trả danh sách đoạn {text, hit}
app/search/keyword.py    # search PGroonga + bộ lọc (MessageFilters, dùng chung với danh sách thread)
app/search/semantic.py   # nhánh vector (2 bước: index HNSW rồi lọc), nhánh từ khóa OR, RRF, hybrid_search
app/search/similar.py    # thread liên quan bằng vector đã lưu (lớp 4 gom thread)
app/ml_client.py         # HttpMlClient: /embed (kiểm model = PMM_EMBED_MODEL), /ner; MlUnavailable
app/embedding.py         # cắt đoạn body_clean (CHUNKER_VERSION), mail chờ embed, embed_batch/embed_user
app/ai/models.py         # cấp haiku/sonnet/opus → model (PMM_MODEL_*), giá ước tính, tác vụ (TASKS)
app/ai/settings.py       # user_ai_settings (cấp theo tác vụ), thống kê ai_calls theo tháng
app/ai/names.py          # tách tên hiển thị header thành PERSON/ORG/EMAIL, mẫu tên công ty, tên lõi
app/ai/people.py         # từ điển người gửi/nhận + công ty trong chữ ký (cache 10 phút / người dùng)
app/ai/masking.py        # Masker: che (mẫu + từ điển + NER, nhất quán, fail closed), thay ngược
app/ai/prompts.py        # system prompt cố định, QuestionPlan, AnswerOut, dựng tin nhắn
app/ai/client.py         # AnthropicLlm (structured output, fallback), call_task ghi ai_calls, LlmError
app/ai/ask.py            # RAG: che → phân tích → tìm cục bộ → che nguồn → trả lời → thay ngược; AskGuard
app/threads_view.py      # danh sách thread (keyset), chi tiết thread, toàn văn theo clean_cuts, trạng thái xử lý
app/overrides.py         # tạo/xóa sửa tay (thread_overrides) + tăng user_thread_state.overrides_version
app/web_static.py        # phục vụ web/dist (SPA), header CSP/nosniff/no-referrer, /api no-store
app/openapi_dump.py      # python -m app.openapi_dump <file>: OpenAPI cho npm run gen:api (không cần DB)
app/ingest.py            # ghi lô mail (ON CONFLICT DO NOTHING, ownership upsert)
app/sources.py           # danh sách folder, xóa mail của folder
app/mail/hashing.py      # message_id_hash
app/mail/subject.py      # chuẩn hóa subject (Re:/Fw:/返信:/【至急】…), prefix ngoài cùng
app/mail/cleaning.py     # cắt quote/chữ ký → body_clean + clean_cuts (CLEANER_VERSION), hàm thuần
app/mail/threading.py    # gom thread JWZ + Thread-Index + subject + override, assign_thread_ids (THREADER_VERSION)
app/processing.py        # chạy làm sạch/gom trong DB theo từng người dùng (role worker), pending_users,
                         #   pending_override_users, ghi applied_overrides_version sau khi gom
app/tasks/__init__.py    # App Procrastinate: process_user_mail, sweep mỗi phút, apply_overrides mỗi 5 giây,
                         #   embed_sweep mỗi phút, embed_user (job ≤ 10 giây, tự đẩy tiếp)
app/worker.py            # python -m app.worker (additional_context: engine, ml)
app/review.py            # mail-stats (chỉ số liệu tổng hợp, có embedding), HTML review-threads
app/safe_errors.py       # describe()/sanitized(): mô tả lỗi không có thông điệp gốc (cho log worker, CLI)
migrations/versions/     # 0001_tables … 0005_api_tokens_ingest_grants, 0006_cleaning_threads,
                         #   0007_message_thread_indexes (CONCURRENTLY), 0008_procrastinate (schema riêng),
                         #   0009_threads_drop_subject (threads không chứa nội dung mail),
                         #   0010_web_sessions, 0011_thread_override_writes, 0012_messages_search_index (CONCURRENTLY),
                         #   0013_secdef_search_path (pg_temp cuối search_path, PUBLIC không có TEMP),
                         #   0014_threads_list_index (CONCURRENTLY, thứ tự danh sách thread),
                         #   0015_embeddings_ai (message_chunks + HNSW, user_ai_settings, ai_calls)
tests/                   # pytest trên database mailassist_test; tests/dbutil.py: helper tạo dữ liệu, as_user;
                         #   tests/webutil.py: client web, người dùng có mật khẩu, đăng nhập;
                         #   tests/mlfake.py: FakeMl (vector theo "khái niệm", NER giả), FakeLlm
```

Dự kiến thêm:

```
app/projects.py  # gán dự án (chờ người dùng chốt danh sách dự án và quy tắc)
```

## DB

- Chạy DB: `docker compose up -d` ở thư mục gốc (PostgreSQL 16 + PGroonga + pgvector, `127.0.0.1:15432`). Docker Desktop phải đang chạy.
- Role: `app_owner` (chủ bảng, migration), `app_api` (chịu RLS), `app_worker` (BYPASSRLS, job nền). Database `mailassist` và `mailassist_test` (test).
- Mỗi người dùng có bản `messages` riêng (`messages.user_id`, unique `(user_id, message_id_hash)`). `message_ownership` trỏ tới `messages` và `mail_sources` bằng FK kép kèm `user_id`, nên chỉ gắn được vào mail và folder của chính người đó. Chống trùng chỉ trong phạm vi một người; không bao giờ tra mail của người khác.
- Code phục vụ người dùng chỉ lấy phiên DB qua `user_session`.
- Quyền ghi của `app_api` (migration 0005, đã áp dụng các điều kiện từ review bảo mật 0004):
  - `INSERT` theo từng cột, **không có cột `id`** (chặn `OVERRIDING SYSTEM VALUE`), cho `messages`, `message_ownership`, `mail_sources`; policy `WITH CHECK (user_id = app_user_id())`.
  - `messages`: không `UPDATE`; ingest dùng `ON CONFLICT (user_id, message_id_hash) DO NOTHING` (mail giả cùng Message-ID không ghi đè bản thật), lấy `id` mail đã có bằng `SELECT` riêng. Khi trùng mà `from_addr`/`sent_at` khác → đếm `mismatched`, chỉ log số lượng. `DELETE` của chính mình (xóa mail của folder).
  - `message_ownership`: `UPDATE` chỉ `mbox_offset`, `last_seen_at`, `is_deleted` và 3 cờ; không bao giờ `message_id`, `source_id`, `user_id`.
  - `api_tokens`: `app_api` không có quyền gì; xác thực qua hàm `SECURITY DEFINER auth_token_user(bytea)` (search_path cố định, chỉ `app_api` EXECUTE).
  - Bảng mới chứa dữ liệu mail của người dùng: làm theo cùng mẫu (cột `user_id`, INSERT theo cột, FK kép kèm `user_id`, `ON DELETE CASCADE` tới `messages` để chức năng xóa mail của folder xóa luôn dữ liệu dẫn xuất).
- Không log `str(exception)` của lỗi DB (có thể chứa DETAIL với nội dung dòng): chỉ log `sqlstate` và `diag.constraint_name`. Engine mới (worker…) cũng đặt `hide_parameters=True`. Task Procrastinate và lệnh CLI chạm dữ liệu mail bọc lỗi bằng `app/safe_errors.py` (`raise sanitized(...) from None`): Procrastinate log cả traceback.
- Bảng dẫn xuất từ mail mà `app_api` không ghi được (vd. `threads`) không được chép nội dung mail: xóa mail của folder chạy bằng `app_api` phải làm mất hết nội dung ngay, không chờ worker.
- Regex chạy trên nội dung mail phải tuyến tính: dòng có thể dài 2.000.000 ký tự; có test mail cố tình dựng trong `tests/test_cleaning.py`.
- `app.user_id` là GUC mà bất kỳ SQL nào cũng tự đặt được: RLS chặn lỗi quên lọc, không chặn SQL injection. Luôn dùng tham số bind; tool của search agent không nhận SQL thô.
- Sửa `db/init/` hoặc `db/sql/` thì build lại image: `docker compose up -d --build`.
- Làm lại DB dev từ đầu: `docker compose down -v`, `docker compose up -d --build`, rồi `uv run --directory backend alembic upgrade head` (script `db/init/01-init.sh` chỉ chạy khi volume mới được tạo).
- Volume tạo trước khi có `db/sql/revoke-pgroonga-admin.sql`: chạy tay một lần cho từng database, ví dụ `docker compose exec -T db psql -U postgres -d mailassist -f /opt/pm-mail-assistant/sql/revoke-pgroonga-admin.sql` (lặp lại với `mailassist_test`).

## Lệnh

Chạy từ thư mục gốc `Email Assistant` (hoặc bỏ `--directory backend` khi đứng trong `backend/`). Test cần DB đang chạy.

| Việc | Lệnh |
|---|---|
| Cài | `uv sync --directory backend` |
| Test | `uv run --directory backend pytest` |
| Lint, format, kiểm tra kiểu | `uv run --directory backend ruff check .` · `uv run --directory backend ruff format .` · `uv run --directory backend pyright` |
| Migration | `uv run --directory backend alembic upgrade head` · `uv run --directory backend alembic revision --autogenerate -m "..."` |
| Chạy API (dev, 127.0.0.1:8000) | `uv run --directory backend python -m app.run [--port 8000]` |
| Tạo người dùng + token collector | `uv run --directory backend python -m app.cli create-user --email <e> --name "<tên>" [--role pm]` (token chỉ hiện một lần) |
| Token khác / thu hồi | `... python -m app.cli create-token --email <e>` · `... python -m app.cli revoke-token --email <e>` |
| Mật khẩu đăng nhập web (thu hồi mọi phiên web cũ) | `... python -m app.cli set-password --email <e>` (hỏi 2 lần, không hiện ra màn hình; dùng cách này cho mật khẩu thật) · `--password-stdin` cho script (đọc UTF-8, bỏ BOM; Windows PowerShell 5.1 mặc định đổi chữ có dấu thành `?` khi pipe) |
| OpenAPI cho web (không cần DB) | `... python -m app.openapi_dump ../web/openapi.json` (thường gọi qua `npm run gen:api` trong `web/`) |
| Chạy worker (làm sạch + gom thread tự động) | `uv run --directory backend python -m app.worker` (không dùng CLI `procrastinate`: trên Windows cần SelectorEventLoop) |
| Làm sạch + gom ngay, không cần worker | `... python -m app.cli process-mail --email <e> [--rethread]` |
| Embed ngay, không cần worker (cần ml-service chạy) | `... python -m app.cli embed-mail --email <e> [--limit N]` |
| Số liệu tổng hợp (chạy được trên dữ liệu thật) | `... python -m app.cli mail-stats [--email <e>]` |
| Đánh giá tìm kiếm trên profile giả (DB test, cần ml-service; không chạy cùng pytest) | `uv run --directory backend python ../eval/retrieval_eval.py [--skip-load] [--out FILE]` |
| Người dùng tự kiểm tra thread (file có nội dung mail, Claude Code không đọc) | `... python -m app.cli review-threads --email <e> [--sample 100]` |

- `alembic.ini` chỉ dùng ký tự ASCII (Alembic đọc bằng codepage hệ thống trên Windows).
- `env.py` chạy mỗi migration trong transaction riêng. Migration cần `CREATE INDEX CONCURRENTLY` thì đặt riêng một file chỉ có `autocommit_block` + `IF NOT EXISTS` (như 0007).
- Bảng job Procrastinate ở schema `procrastinate`, chỉ `app_worker` có quyền. Nâng bản Procrastinate (đang ghim 3.10.0) thì thêm migration chạy các file trong `procrastinate/sql/migrations`.
- Mạng chậm thì đặt `$env:UV_HTTP_TIMEOUT = "300"` trước `uv sync`.

## Làm sạch nội dung

Quy tắc đầy đủ: spec `docs/superpowers/specs/2026-10-17-content-cleaning-design.md`.

- Quote: dòng giới thiệu (`… wrote:`, `… đã viết:`, `…書きました:`, dạng Gmail tiếng Nhật `2026年10月1日(水) 10:00 名前 <addr>:` chiếm đa số trên dữ liệu thật), `-----Original Message-----` / `-----元のメッセージ-----`, khối header Outlook (`From/差出人/Từ` + `Sent/送信日時` + `Subject/件名`), khối dòng `>`.
- Nội dung chuyển tiếp (`Forwarded message`, `転送メッセージ`, hoặc Outlook với subject `Fw:`) được giữ.
- Chữ ký: `--`, dòng kẻ + dấu hiệu liên hệ (TEL, 〒, 株式会社…), chân trang di động, khối công ty + TEL.
- Lưu vị trí cắt (`clean_cuts`) và `cleaner_version`. Đổi quy tắc thì tăng `CLEANER_VERSION` trong `app/mail/cleaning.py`: worker tự làm sạch lại. Kiểm tra trên dữ liệu thật chỉ bằng `mail-stats`.

## Gom thread (độ tin cậy giảm dần)

Spec: `docs/superpowers/specs/2026-10-17-threading-design.md`.

1. `References` / `In-Reply-To`, dựng cây theo thuật toán JWZ.
2. `Thread-Index` của Outlook: 22 byte đầu (sau khi giải base64) là ID cuộc hội thoại.
3. Subject đã chuẩn hóa + người gửi và người nhận thuộc thread cũ + trong 60 ngày, chỉ cho nhóm bắt đầu bằng mail có prefix `Re:`/`Fw:`… Chuẩn hóa subject: bỏ `Re:`, `RE[2]:`, `Fw:`, `Fwd:`, `返信:`, `転送:`, `【至急】`…, NFKC, giữ thẻ khác như `【サンプルA】`.
4. (Giai đoạn 5) gợi ý gộp bằng embedding, người dùng xác nhận.

- Gom lại toàn bộ mail của một người mỗi lần có mail mới; id thread giữ theo số mail chung (`assign_thread_ids`). Đổi thuật toán thì tăng `THREADER_VERSION`.
- `thread_overrides` (split/merge, tham chiếu mail) luôn được áp dụng lại, thuật toán không ghi bảng này. Web ghi qua `app/overrides.py` (spec 15), kèm tăng `user_thread_state.overrides_version`; `rethread_user` đọc version **trước** khi đọc override rồi ghi `applied_overrides_version` (sửa tay commit giữa lúc gom không bị mất).
- Không bao giờ gom mail của hai người dùng với nhau; code chạy bằng role worker phải lọc `user_id` ở mọi truy vấn.

Gán dự án tự động theo domain khách và mã dự án trong subject: chưa làm, chờ người dùng chốt danh sách dự án và quy tắc.

## Embedding và tìm kiếm ngữ nghĩa

Spec 18, 19, 23 (`docs/superpowers/specs/2026-10-19-*`).

- ml-service (`ML_SERVICE_URL`, mặc định `http://127.0.0.1:8001`) phải chạy cho: embed (worker), tìm kiếm `mode=hybrid`, hỏi đáp (embed câu hỏi + NER). Không chạy thì worker bỏ qua và thử lại phút sau, API trả 503 `ml_unavailable`.
- `PMM_EMBED_MODEL` (mặc định `BAAI/bge-m3`) phải trùng model ml-service đang chạy; khác thì không embed, không tìm (503 `embedding_model_mismatch`). Đổi model = embed lại toàn bộ: hỏi người dùng trước.
- `message_chunks` chỉ lưu vị trí đoạn trên `body_clean`, không chép nội dung. Đoạn hợp lệ: `model` hiện hành và `cleaner_version` = `messages.cleaner_version`. Đổi cách cắt đoạn thì tăng `CHUNKER_VERSION` trong `app/embedding.py`: worker tự embed lại.
- **Truy vấn vector không JOIN trong cùng câu `ORDER BY embedding <=> … LIMIT`**: planner bỏ index HNSW và sắp toàn bộ. Dùng `vector_chunks` (bước 1 chỉ `message_chunks`, bước 2 lọc bằng `messages`, tăng k khi thiếu). Luôn `SET LOCAL hnsw.iterative_scan = relaxed_order` (RLS và bộ lọc loại bớt dòng).
- Vector truyền vào SQL bằng tham số bind dạng chuỗi `'[…]'` + `CAST(… AS halfvec(1024))` (`vector_literal`); đọc ra là chuỗi (không đăng ký adapter pgvector).

## Claude API

Spec 20, 21, 22.

- Trước khi viết code gọi Claude, dùng skill `claude-api` để lấy cách dùng SDK hiện hành. Không viết theo trí nhớ.
- Khóa: biến môi trường `ANTHROPIC_API_KEY` (hoặc `ANTHROPIC_AUTH_TOKEN`) của process backend; không lưu DB, không trả ra API. Không có thì `create_app` để `llm = None` và `/ask` trả 503 `ai_not_configured`.
- Model theo cấp và tác vụ (`app/ai/models.py`): mặc định phân tích câu hỏi = `claude-haiku-5-5`, trả lời = `claude-sonnet-5-5`; người dùng đổi trên trang "Cài đặt AI" (bảng `user_ai_settings`). `PMM_MODEL_HAIKU/SONNET/OPUS` đổi model của cấp. Thêm tác vụ mới (giai đoạn 6): thêm vào `TASKS`, gọi qua `call_task`.
- **Mọi text gửi Claude phải qua `Masker` (`app/ai/masking.py`)** cùng một bảng nhãn cho cả lần hỏi, kể cả câu hỏi của người dùng. Masker lỗi (NER lỗi…) thì không gửi. Giá trị Claude trả về để dùng cục bộ (người gửi, từ khóa) phải `unmask` trước. Không bao giờ log text hay bảng nhãn.
- Che từng giá trị riêng (người gửi, người nhận, subject, đoạn mail) rồi mới ghép tên trường (`From:`…): GiNZA nhận nhầm tên trường là tổ chức. Giới hạn độ dài mọi phần (subject 300, To/Cc 10 người).
- Regex trong `masking.py`/`names.py` chạy trên bản NFKC (`nfkc_map`, đổi vị trí về text gốc); mọi lượng từ phải có giới hạn (`{0,30}`…, không `*`/`+` không giới hạn, không lồng nhau): test `test_patterns_linear_on_crafted_text`. Ranh giới từ cho tên Latin dùng lớp chữ Latin, không dùng `\w` (gồm cả chữ Nhật).
- Thêm mẫu che mới thì thêm cả test "không che từ thường" (vd. `仕様書`, `1万件`, `PRJ-1234`): che thừa làm Claude mất ngữ cảnh.
- Client SDK luôn `base_url=https://api.anthropic.com` (`anthropic_base_url`; `PMM_ANTHROPIC_BASE_URL` chỉ nhận `https://*.anthropic.com`), `open_telemetry=False`. Không bật `ANTHROPIC_LOG=debug` cho backend (code đã giữ logger SDK ở WARNING).
- `ML_SERVICE_URL` chỉ nhận `http://` loopback hoặc `https://` (ml-service nhận text chưa che).
- Output có cấu trúc: `output_config.format` với schema từ Pydantic (`anthropic.transform_schema`), rồi tự `model_validate_json`. Không dùng `messages.parse`: nó ném lỗi khi model từ chối hay hết token, mất `stop_reason` và `usage`.
- Sonnet, Opus: `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")` (fallback khi bị từ chối); Haiku không có. Tắt bằng `PMM_AI_FALLBACKS=0`.
- Mỗi lần gọi ghi một dòng `ai_calls` (tác vụ, model thực sự trả lời, status, token, thời gian), kể cả khi lỗi. Cột dự án thêm khi có gán dự án.
- Lỗi SDK → `LlmError(code, http_status)`; log chỉ tên lớp lỗi và `request_id`, không log thông điệp.
- Xử lý dữ liệu cũ hàng loạt (giai đoạn 6) qua Message Batches API. System prompt dài cố định thì đặt `cache_control`.
- Không dùng LangChain / LlamaIndex. Search kiểu agent dùng tool runner của SDK `anthropic`.
- Câu trả lời luôn kèm link tới mail/thread gốc.
- Test không gọi Anthropic thật: `tests/mlfake.FakeLlm` cho luồng hỏi đáp; `tests/test_ai_client.py` dựng `AsyncAnthropic` với `httpx2.MockTransport` để kiểm request/response của SDK.

## Quy ước

- Type hint đầy đủ; pyright và Ruff không báo lỗi.
- Code I/O dùng async.
- Endpoint có body: đọc bằng `read_body(request)` rồi `Model.model_validate_json` **sau** dependency xác thực. Không khai báo body là tham số của route (FastAPI parse body trước khi chạy dependency). Endpoint của web khai báo `openapi_extra=json_body(Model)` để client sinh từ OpenAPI có kiểu body.
- **Tách quyền token và phiên**: endpoint của collector chỉ nhận token Bearer (`current_user_id`), endpoint của web chỉ nhận phiên cookie (`WebUserId`). Token collector không được đọc nội dung mail. Request ghi của web phải có header `X-PMM-Request: 1` (dependency tự kiểm tra; login/logout gọi `require_csrf_header`).
- Lỗi mà web/collector cần phân biệt: `raise ApiError(status, detail, code)` (body `{"detail", "code"}`).
- Path/query id đi vào DB giới hạn bigint (`Path(gt=0, le=MAX_BIGINT)`): số quá lớn trả 422, không phải 500.
- Không log chuỗi tìm kiếm (có thể có tên khách, nội dung nghiệp vụ): log search chỉ `user`, `total`, `ms`. Access log của uvicorn mặc định ghi cả query string (`?q=`, `?sender=`): `app/run.py` gắn filter `DropQueryString`; chạy uvicorn cách khác thì phải giữ filter này (hoặc `access_log=False`).
- App chỉ nhận Host trong `PMM_ALLOWED_HOSTS` (mặc định `127.0.0.1,localhost`; chống DNS rebinding). Test đặt thêm `test` (`conftest.py`). Đặt server ở máy khác thì thêm tên máy vào biến này.
- Hàm `SECURITY DEFINER` mới: `SET search_path = pg_catalog, pg_temp` (pg_temp ở **cuối**: thiếu nó thì PostgreSQL tra kiểu/bảng trong pg_temp trước), ghi rõ schema cho bảng (`public.…`), `REVOKE ALL … FROM PUBLIC` rồi chỉ `GRANT EXECUTE` cho role cần. Test `test_security_definer_functions_have_safe_search_path` kiểm mọi hàm trong `public`.
- Việc tốn CPU trên nội dung mail trong request (tô từ khóa, đoạn trích) chạy bằng `run_in_threadpool`, không chặn event loop.
- Truy vấn search phải viết đúng `ARRAY[m.subject, m.body_clean] &@~ :q` (giống biểu thức index 0012), chuỗi `:q` luôn do `app/search/query.py` dựng.
- Truy vấn danh sách có `ORDER BY … LIMIT` trên nhiều dòng khớp: sắp và cắt trang trong subquery chỉ có cột hẹp (`id`, khóa sắp xếp), rồi mới nối bảng khác và lấy nội dung. Không đặt `EXISTS (…)` trong `SELECT` của truy vấn danh sách (planner có thể đổi thành subplan quét cả bảng); dùng `LEFT JOIN LATERAL (… LIMIT 1)`. Kiểm bằng `EXPLAIN (ANALYZE, BUFFERS)` trên dữ liệu tổng hợp lớn (spec 14 mục 10), không trên DB dev có mail thật nếu output có dữ liệu.
- `/docs`, `/redoc` tắt: Swagger UI tải script từ CDN, chạy cùng origin với phiên web thì gọi được API đọc mail. Xem OpenAPI ở `/openapi.json` hoặc `openapi_dump`.
- Response của `/api/…` có `Cache-Control: no-store` (middleware `SecurityHeaders`); thêm endpoint không cần làm gì thêm.
- Test web: `tests/webutil.py` (`add_web_user`, `login`, `CSRF`). `conftest.py` đặt `SCRYPT_N = 2^10` cho nhanh (scrypt thật ~0,6 giây mỗi lần).
- Telemetry OpenTelemetry bật sẵn của FastAPI phải tắt (`create_app` truyền `telemetry={...False}`); không thêm middleware/exporter gửi thông tin request ra ngoài.
- Ingest chỉ nhận `parser_version` ≥ `MIN_PARSER_VERSION` (`app/ingest.py`, hiện `collector/0.4.0`), cũ hơn trả 426. Tăng mức này khi phát hiện bản collector cũ gửi dữ liệu không được phép (vd. nội dung file đính kèm).
- Lỗi mà collector cần phân biệt (vd. 404 "không có folder" với 404 của proxy) trả kèm trường `code` trong body JSON.
- Trường ngày và số nguyên nhận từ collector phải có miền giá trị (`PlausibleTime`, `BigInt` trong `app/schemas.py`): một giá trị lạ không được làm cả lô lỗi 500.
- Schema API và schema output AI viết bằng Pydantic. Schema API là hợp đồng với web và collector: đổi thì báo rõ phần nào bị ảnh hưởng.
- Job phải idempotent: chạy lại không sinh dữ liệu trùng.
- Backend nhận text UTF-8 từ collector, không tự giải mã charset từ byte gốc (codec `iso2022_jp` của Python không đọc được ký tự mở rộng CP932).
