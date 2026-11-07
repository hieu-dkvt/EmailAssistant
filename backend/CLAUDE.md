# Backend (Python)

API (FastAPI) và worker xử lý nền (Procrastinate) dùng chung một codebase. Nhận dữ liệu từ collector, làm sạch, gom thread, gọi AI, phục vụ search và web.

## Công nghệ

Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.0 + psycopg 3, `pgvector` (kiểu `HALFVEC` cho model), Alembic, Procrastinate, `httpx` (gọi ml-service và gateway AI tương thích OpenAI). Model embedding (bge-m3) chạy ở `ml-service/`, backend không nạp model.

## Cấu trúc

Đã có:

```
app/config.py            # URL DB theo role (owner/api/worker), biến DATABASE_URL_* ghi đè giá trị dev;
                         #   ML_SERVICE_URL, PMM_EMBED_MODEL
app/db/models.py         # model SQLAlchemy: users, api_tokens, user_passwords, web_sessions, mail_sources,
                         #   messages (gồm index PGroonga ix_messages_search), message_ownership, threads,
                         #   thread_overrides, user_thread_state, message_chunks (halfvec + HNSW),
                         #   user_ai_settings, ai_calls, projects, message_projects, user_project_state (spec 25),
                         #   glossary_terms, message_translations, message_auto_runs (spec 26)
app/db/session.py        # create_api_engine (hide_parameters), user_session(engine, user_id): transaction + app.user_id (RLS)
app/main.py              # create_app (FastAPI, giới hạn body, tắt /docs /redoc, header bảo mật, mount web/dist)
app/run.py               # chạy uvicorn 127.0.0.1 (SelectorEventLoop trên Windows); access log bỏ query string
app/auth.py              # token collector pmm_..., băm SHA-256
app/web_auth.py          # mật khẩu web (scrypt), token phiên pmw_..., LoginThrottle (5 lần sai / 15 phút)
app/cli.py               # create-user / create-token / revoke-token / set-password (role owner);
                         #   process-mail, assign-projects, mail-stats (role worker), review-threads (role api + RLS)
app/api/deps.py          # xác thực Bearer token qua auth_token_user, đọc body có giới hạn + gunzip an toàn
app/api/web_deps.py      # phiên cookie qua web_session_user, header X-PMM-Request (CSRF), json_body() cho OpenAPI
app/api/routes.py        # API collector (chỉ token): /api/v1/me, /ingest, /sources, /sources/delete-mail
app/api/session_routes.py # đăng nhập web: POST/GET/DELETE /api/v1/session
app/api/web_routes.py    # API web (chỉ phiên): /folders, /search (mode=keyword|hybrid), /threads,
                         #   /threads/{id}, /threads/{id}/similar, /messages/{id}, /messages/{id}/full,
                         #   /processing, /thread-overrides
app/api/ai_routes.py     # API web AI (chỉ phiên): POST /ask, GET/PUT /ai/settings, GET /ai/usage,
                         #   GET/PUT/DELETE /ai/provider, POST /ai/provider/test; dịch và chuyển folder (spec 26):
                         #   POST /messages/{id}/translate, GET /threads/{id}/translations,
                         #   POST /messages/{id}/move-back, /move, /auto-retry
app/api/glossary_routes.py # glossary (chỉ phiên web): CRUD, nhập có xem trước, xuất CSV (spec 26)
app/api/errors.py        # lỗi 422 không trả lại dữ liệu gửi lên; lỗi DB chỉ log sqlstate/ràng buộc;
                         #   ApiError → {"detail", "code"}; MlUnavailable / EmbeddingModelMismatch → 503
app/schemas.py           # schema Ingest API (hợp đồng với collector)
app/web_schemas.py       # schema API web (hợp đồng với web/, sinh TypeScript qua OpenAPI)
app/search/query.py      # dựng truy vấn Groonga từ ô tìm kiếm (AND, OR, -từ, "cụm"), không nhận cú pháp thô
app/search/highlight.py  # tô từ khóa (NFKC + lower), đoạn trích; trả danh sách đoạn {text, hit}
app/search/keyword.py    # search PGroonga + bộ lọc (MessageFilters, dùng chung với danh sách thread);
                         #   bộ lọc folder_id tính theo folder hiệu lực (xem mục "Dịch mail tự động")
app/search/semantic.py   # nhánh vector (HNSW 2 bước, hoặc quét chính xác khi phạm vi lọc ≤ EXACT_SCAN_MAX_CHUNKS =
                         #   20.000 đoạn: scope_is_small, exact_chunks_sql), nhánh từ khóa OR, RRF, hybrid_search
app/search/similar.py    # thread liên quan bằng vector đã lưu (lớp 4 gom thread)
app/ml_client.py         # HttpMlClient: /embed (kiểm model = PMM_EMBED_MODEL), /rerank (timeout 30 giây, cắt query
                         #   2.000 / text 10.000 ký tự, tối đa 64 text, check_scores); MlUnavailable
app/embedding.py         # cắt đoạn body_clean (CHUNKER_VERSION), mail chờ embed, embed_batch/embed_user
app/ai/models.py         # chỉ còn tác vụ (TASKS: question_analysis, answer, translate); không còn cấp model, giá
app/ai/settings.py       # user_ai_settings (tên model theo tác vụ), thống kê ai_calls theo tháng
app/ai/provider.py       # nhà cung cấp tùy chỉnh (spec 24, 28): validate_base_url, reject_link_local, is_public_host,
                         #   OpenAiLlm, LlmResolver (trả None khi chưa nhập), user_ai_provider
app/ai/secrets.py        # Fernet mã hóa API key (PMM_SECRET_KEY)
app/ai/text.py           # cut_text(text, limit): cắt ở ranh giới đoạn / cuối dòng trước khi gửi AI (spec 28)
app/ai/prompts.py        # system prompt cố định, QuestionPlan, AnswerOut, TranslationOut, dựng tin nhắn
app/ai/glossary.py       # so khớp glossary trên text gốc đã cắt (cụm dài trước, không chồng lấn), load_entries, nfkc_map
app/ai/translate.py      # luồng dịch một mail: cắt → glossary → call_task → check_translation;
                         #   save_translation, saved_translations (kèm stale), thread_translations
app/auto_translate.py    # job dịch tự động: tiêu chí (ELIGIBLE_SQL), translate_next, choose_target,
                         #   retarget_no_folder, pending_translate_users (role worker duy nhất)
app/mail/domains.py      # normalize_domain, domain_matches, is_inbox_path, inbox_path_sql
seed/glossary-it-ja-vi.csv # 151 thuật ngữ IT/outsource công khai, nạp bằng chức năng nhập của web
seed/glossary-programming-ja-vi.csv # 153 thuật ngữ lập trình, không trùng file trên (test_seed_files_do_not_overlap)
app/ai/client.py         # kiểu chung LlmClient/LlmResponse, strict_schema (JSON schema từ Pydantic), call_task ghi ai_calls, LlmError,
                         #   served_model
app/ai/ask.py            # RAG: phân tích → tìm cục bộ (_candidates → rerank tùy chọn `_rerank_head` → pick) → đọc nguồn và mail trước
                         #   (_fetch_context, user_session thứ hai) → trả lời (text gốc, không che); AskGuard
app/ai/context.py        # hàm thuần dựng ngữ cảnh gửi AI (spec 27 mục 6): gộp đoạn theo mail (group_sources), mở rộng
                         #   (expand_spans), chia ngân sách (plan_context), CONTEXT_MAX_CHARS 24.000,
                         #   SOURCE_MAX_CHARS 3.000, PREVIOUS_MAX_CHARS 1.200, MIN_EXPANSION_CHARS 200
app/threads_view.py      # danh sách thread (keyset), chi tiết thread, toàn văn theo clean_cuts, trạng thái xử lý
app/overrides.py         # tạo/xóa sửa tay (thread_overrides) + tăng user_thread_state.overrides_version
app/web_static.py        # phục vụ web/dist (SPA), header CSP/nosniff/no-referrer, /api no-store
app/openapi_dump.py      # python -m app.openapi_dump <file>: OpenAPI cho npm run gen:api (không cần DB)
app/ingest.py            # ghi lô mail (ON CONFLICT DO NOTHING, ownership upsert)
app/sources.py           # danh sách folder (list_sources: số vật lý cho collector; list_folders: số theo
                         #   folder hiệu lực cho GET /folders), xóa mail của folder
app/mail/hashing.py      # message_id_hash
app/mail/subject.py      # chuẩn hóa subject (Re:/Fw:/返信:/【至急】…), prefix ngoài cùng
app/mail/cleaning.py     # cắt quote/chữ ký → body_clean + clean_cuts (CLEANER_VERSION), hàm thuần
app/mail/threading.py    # gom thread JWZ + Thread-Index + subject + override, assign_thread_ids (THREADER_VERSION)
app/processing.py        # chạy làm sạch/gom trong DB theo từng người dùng (role worker), pending_users,
                         #   pending_override_users, ghi applied_overrides_version sau khi gom
app/tasks/__init__.py    # App Procrastinate: process_user_mail, sweep mỗi phút, apply_overrides mỗi 5 giây,
                         #   embed_sweep mỗi phút, embed_user (job ≤ 10 giây, tự đẩy tiếp),
                         #   project_rules_sweep mỗi 5 giây, assign_user_projects (spec 25),
                         #   translate_sweep mỗi phút (queue default), translate_user (queue translate, spec 26)
app/worker.py            # python -m app.worker (additional_context: engine, api_engine, ml, resolver);
                         #   hai vòng run_worker_async: queue default và queue translate
app/review.py            # mail-stats (chỉ số liệu tổng hợp, có embedding), HTML review-threads
app/safe_errors.py       # describe()/sanitized(): mô tả lỗi không có thông điệp gốc (cho log worker, CLI)
migrations/versions/     # 0001_tables … 0005_api_tokens_ingest_grants, 0006_cleaning_threads,
                         #   0007_message_thread_indexes (CONCURRENTLY), 0008_procrastinate (schema riêng),
                         #   0009_threads_drop_subject (threads không chứa nội dung mail),
                         #   0010_web_sessions, 0011_thread_override_writes, 0012_messages_search_index (CONCURRENTLY),
                         #   0013_secdef_search_path (pg_temp cuối search_path, PUBLIC không có TEMP),
                         #   0014_threads_list_index (CONCURRENTLY, thứ tự danh sách thread),
                         #   0015_embeddings_ai (message_chunks + HNSW, user_ai_settings, ai_calls),
                         #   0017_projects (projects, message_projects, user_project_state, spec 25),
                         #   0018_translation (cột mới của projects, glossary_terms, message_translations,
                         #   message_auto_runs, spec 26), 0019_drop_ai_tier (bỏ cột tier, model NOT NULL, spec 28),
                         #   0020_chunks_hnsw_params (build lại index HNSW m=16, ef_construction=128, CONCURRENTLY, spec 27)
                         #   0021_openai_protocol_only (chỉ còn giao thức openai, đổi dòng giao thức cũ, spec 28 mục 14)
tests/                   # pytest trên database mailassist_test; tests/dbutil.py: helper tạo dữ liệu, as_user;
                         #   tests/webutil.py: client web, người dùng có mật khẩu, đăng nhập;
                         #   tests/mlfake.py: FakeMl (vector theo "khái niệm"), FakeLlm
app/projects.py          # bộ lọc From, To, CC, Subject: match_projects (hàm thuần), assign_messages, assign_user (job `assign_user_projects` ở tasks bọc hàm này, spec 25)
app/api/project_routes.py  # CRUD dự án (chỉ phiên web)
app/db/locks.py          # khóa advisory theo người dùng dùng chung cho gom thread và gán dự án
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
| Chạy worker (làm sạch, gom thread, gán dự án, embed, dịch tự động) | `uv run --directory backend python -m app.worker` (không dùng CLI `procrastinate`: trên Windows cần SelectorEventLoop). Dịch cần `$env:PMM_SECRET_KEY` (cùng khóa với API), nhà cung cấp và tên model dịch đã nhập ở Cài đặt AI; dịch không cần ml-service |
| Làm sạch + gom ngay, không cần worker | `... python -m app.cli process-mail --email <e> [--rethread]` |
| Embed ngay, không cần worker (cần ml-service chạy) | `... python -m app.cli embed-mail --email <e> [--limit N]` |
| Gán lại mail vào dự án ngay, không cần worker (in số mail đã xét và số mail có dự án) | `... python -m app.cli assign-projects --email <e>` |
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

Gán dự án theo bộ lọc From, To, CC, Subject do người dùng khai báo (spec 25, `app/projects.py`). Bẫy:

- `assign_messages` chạy bên trong transaction của `rethread_user` (giữ `USER_MAIL_LOCK`), nên mail mới và dự án cùng commit. Ghi mỗi lô trong SAVEPOINT, thử lại tối đa 3 lần với sqlstate 23503 (mail/dự án bị xóa giữa chừng) và 40P01.
- Đọc `rules_version` **trước** khi đọc `projects` (sửa bộ lọc commit giữa chừng thì lần sau quét lại, không bị mất).
- Không dùng mốc `messages.id` để tìm mail mới; không log giá trị bộ lọc hay tên dự án.
- Quét lại: `project_rules_sweep` (5 giây) đẩy `assign_user_projects`; chạy tay `python -m app.cli assign-projects --email <e>`.

## Embedding và tìm kiếm ngữ nghĩa

Spec 18, 19, 23 (`docs/superpowers/specs/2026-10-19-*`); spec 27 (`2026-11-01-ask-retrieval-design.md`): lọc dự án, quét chính xác, ngữ cảnh, rerank.

- ml-service (`ML_SERVICE_URL`, mặc định `http://127.0.0.1:8001`) phải chạy cho: embed (worker), tìm kiếm `mode=hybrid`, hỏi đáp (embed câu hỏi). Không chạy thì worker bỏ qua và thử lại phút sau, API trả 503 `ml_unavailable`.
- `PMM_EMBED_MODEL` (mặc định `BAAI/bge-m3`) phải trùng model ml-service đang chạy; khác thì không embed, không tìm (503 `embedding_model_mismatch`). Đổi model = embed lại toàn bộ: hỏi người dùng trước.
- `message_chunks` chỉ lưu vị trí đoạn trên `body_clean`, không chép nội dung. Đoạn hợp lệ: `model` hiện hành và `cleaner_version` = `messages.cleaner_version`. Đổi cách cắt đoạn thì tăng `CHUNKER_VERSION` trong `app/embedding.py`: worker tự embed lại.
- **Truy vấn vector không JOIN trong cùng câu `ORDER BY embedding <=> … LIMIT`**: planner bỏ index HNSW và sắp toàn bộ. Dùng `vector_chunks` (bước 1 chỉ `message_chunks`, bước 2 lọc bằng `messages`, tăng k khi thiếu). Luôn `SET LOCAL hnsw.iterative_scan = relaxed_order` (RLS và bộ lọc loại bớt dòng).
- **Phạm vi lọc nhỏ thì quét chính xác** (spec 27 mục 4): `vector_chunks` có bộ lọc người dùng (folder, dự án, người gửi, ngày, đính kèm; `exclude_thread_id` không tính) thì `scope_is_small` đếm tới `EXACT_SCAN_MAX_CHUNKS + 1` đoạn một lần mỗi lượt `_candidates`; không quá ngưỡng thì CTE `MATERIALIZED` quét hết, `ORDER BY distance, message_id, chunk_no` (không dùng index HNSW, cố ý). Quá ngưỡng hoặc không có bộ lọc thì vòng lặp HNSW. Index HNSW build với `m = 16`, `ef_construction = 128` (migration 0020, `models.py`); dev DB cũ cần `alembic upgrade head`, build lâu theo số đoạn.
- **Hỏi đáp theo dự án và ngữ cảnh** (spec 27): `AskRequest.project_id` (dự án không thấy → 404 `project_not_found`, kiểm trước ml-service và AI); `project_id` nằm trong cả `base` và `narrowed`. Đoạn được gộp theo mail, mỗi mail một nguồn, mở rộng và kèm mail liền trước (`PREVIOUS_MAILS`) trong `<previous_mail>`; mọi hằng số ở `app/ai/context.py`. `PMM_RERANK=1` bật rerank (mặc định tắt): lấy `RERANK_CANDIDATES = 40` đoạn đầu sau RRF, gọi `/rerank` ngoài transaction, lỗi thì giữ thứ tự RRF (log `reranked=0`, không log câu hỏi hay điểm). Cần `ML_RERANK_MODEL` ở ml-service, nếu không `/rerank` trả 503 và backend dùng thứ tự RRF.
- Vector truyền vào SQL bằng tham số bind dạng chuỗi `'[…]'` + `CAST(… AS halfvec(1024))` (`vector_literal`); đọc ra là chuỗi (không đăng ký adapter pgvector).

## Gọi AI (nhà cung cấp và model bất kỳ)

Spec 21, 22, 24, 28.

**Chỉ model nội bộ (quyết định của người dùng 2026-11-01, đã làm ở spec 28):** AI chạy trên model nội bộ qua gateway kiểu 9router, giao thức tương thích OpenAI (`OpenAiLlm`, `provider.py`), giao thức duy nhất từ 2026-11-05 (migration 0021). Text gửi AI là text gốc, không che. Ô URL ở Cài đặt AI nhận mọi địa chỉ nên **code không ngăn mail ra ngoài**: chỉ có cảnh báo trên màn hình khi host là địa chỉ công cộng (`is_public_host`, `AiProviderCustom.public_host`). Code mới không thêm đường gọi AI ngoài cấu hình của người dùng.

- **Không có khóa mặc định.** Backend không đọc khóa, URL hay tên model AI từ biến môi trường. Nhà cung cấp của người dùng nằm ở bảng `user_ai_provider` (key mã hóa Fernet bằng `PMM_SECRET_KEY`, spec 24). `LlmResolver.for_user` trả `LlmClient | None`: `None` khi chưa nhập nhà cung cấp, `/ask` và dịch tay trả 503 `ai_not_configured`, job dịch dừng. `LlmResolver(default=…)` và `create_app(llm=…)` chỉ để test truyền client giả.
- **Tên model do người dùng nhập theo tác vụ** (bảng `user_ai_settings`, cột `model` NOT NULL, xóa tên = xóa dòng; không còn ba cấp model và giá ước tính). `call_task(model=…)` nhận chuỗi này. Thiếu tên model → 503 `ai_model_missing`: route kiểm trước, `ask()` và `produce_translation` tự kiểm lại vì job dịch và `ask()` không đi qua route. Thêm tác vụ mới (giai đoạn 6): thêm vào `TASKS` (`app/ai/models.py`), gọi qua `call_task`.
- URL: `validate_base_url` nhận `http` và `https` tới mọi địa chỉ (LAN, localhost, công cộng); từ chối URL sai dạng, IP viết tắt (`2130706433`, `0x7f.1`), link-local (`169.254.0.0/16`, `fe80::/10`), ký tự điều khiển và khoảng trắng; dấu chấm cuối của host được bỏ trước khi xét. `reject_link_local` phân giải host khi lưu (`PUT /ai/provider`) và khi `LlmResolver` dựng client mới; cache trúng thì không kiểm lại, nên DNS đổi sau đó không chặn được cho tới khi client được dựng lại. Không log key, URL đầy đủ hay body phản hồi; thông điệp lỗi của gateway không được chép vào response. `/ai/provider/test` chạy với URL người dùng nhập (SSRF, đã giảm: chỉ phiên web, không theo redirect, không trả body, chặn link-local).
- Giới hạn độ dài là giới hạn chi phí, không phụ thuộc việc che: subject 300 ký tự, To/Cc 10 người, tên và địa chỉ 100 ký tự, thân mail trong `/ask` tối đa `SOURCE_MAX_CHARS` = 3.000 ký tự mỗi nguồn và `CONTEXT_MAX_CHARS` = 24.000 tổng (spec 27; trước đó chỉ đoạn ≤ 800 ký tự `CHUNK_CHARS`); mail dịch `MAX_BODY_CHARS` = 12.000, ngữ cảnh `MAX_CONTEXT_CHARS` = 1.500. Cắt bằng `cut_text` (`app/ai/text.py`): ranh giới đoạn, rồi cuối dòng, trong nửa sau giới hạn.
- **`trust_env=False` là bắt buộc** ở `OpenAiLlm`, như `ml_client.py`: có `HTTP_PROXY` hoặc proxy hệ thống Windows thì mail gốc và API key sẽ đi qua proxy, kể cả tới địa chỉ LAN. Client cũng đặt `follow_redirects=False` (redirect sẽ mang key và mail gốc sang host khác). Tác dụng phụ: `SSL_CERT_FILE`, `SSL_CERT_DIR` cũng bị bỏ qua (spec 28 mục 8).
- `PUT /ai/provider` với ô key trống chỉ giữ key đã lưu khi URL không đổi; đổi URL thì 422 `api_key`, key cũ không bao giờ đi theo sang địa chỉ khác (quyết định của người dùng 2026-11-02, spec 28 mục 13).
- `ML_SERVICE_URL` chỉ nhận `http://` loopback hoặc `https://` (ml-service nhận text mail gốc).
- Output có cấu trúc: `response_format` kiểu `json_schema` (`strict`) với schema từ `strict_schema` (`model_json_schema()` của Pydantic, mọi object `additionalProperties: false`), rồi tự `model_validate_json` để luôn đọc được `stop_reason` và `usage`. Gateway trả 400 thì hỏi lại bằng `json_object` kèm schema trong prompt. Output model mới chỉ dùng kiểu đơn giản và mọi trường bắt buộc (`strict_schema` không chuyển ràng buộc như `minLength`).
- Mỗi lần gọi ghi một dòng `ai_calls` (tác vụ, model thực sự trả lời, status, token, thời gian), kể cả khi lỗi. Tên model do gateway trả đi qua `served_model`: chỉ nhận chuỗi in được, tối đa 200 ký tự, sai thì dùng tên đã yêu cầu. Cột dự án thêm khi có gán dự án. `/ai/usage` chỉ thống kê token, không có giá.
- Lỗi gọi gateway → `LlmError(code, http_status)`; log chỉ tên lớp lỗi, không log thông điệp hay body phản hồi.
- **Hết giờ:** `ReadTimeout` (gateway nhận request, không trả lời xong trong `TIMEOUT_SECONDS` = 120 giây) → `ai_timeout`, 503, thông điệp cố định `TIMEOUT_MESSAGE`, **không thử lại** (mỗi lần thử tốn tới 120 giây tải của gateway). Lỗi mạng khác, 429 và 5xx vẫn là `ai_unavailable`, thử lại tối đa `MAX_RETRIES` = 2 lần.
- Quy tắc tên riêng của prompt dịch (`TRANSLATE_SYSTEM`): giữ nguyên hoặc phiên âm, trừ khi có trong `<glossary>`, viết 様 thành -sama, さん thành -san. Đổi prompt dịch thì tăng `TRANSLATE_PROMPT_VERSION` (hiện 2); bản dịch cũ hiện nhãn "cũ", chỉ dịch lại khi người dùng bấm "Dịch lại".
- Xử lý dữ liệu cũ hàng loạt (giai đoạn 6) chạy bằng worker qua gateway nội bộ, giới hạn đồng thời theo khả năng của model.
- Không dùng LangChain / LlamaIndex. Search kiểu agent (nếu làm) không dựa vào tool calling: model nội bộ có thể không hỗ trợ tool.
- Câu trả lời luôn kèm link tới mail/thread gốc.
- Test không gọi AI thật: `tests/mlfake.FakeLlm` cho luồng hỏi đáp; `tests/test_ai_provider.py` dựng `OpenAiLlm` với `httpx.MockTransport` để kiểm request/response gửi gateway; `tests/test_ai_client.py` kiểm `strict_schema`, `served_model`.

## Dịch mail tự động, glossary, chuyển folder

Spec 26 (`docs/superpowers/specs/2026-10-31-mail-translation-design.md`). Bẫy:

- **Job dịch chạy bằng `api_engine` (role `app_api`) qua `user_session`**, không bằng `engine` của worker: role `app_worker` có BYPASSRLS, không có quyền trên `glossary_terms`, `message_translations`, `user_ai_provider`, `user_ai_settings`, nên RLS là lớp chặn thứ hai cho các truy vấn đọc theo người dùng. `load_entries`, `user_models`, `load_provider`, `delete_provider` vẫn tự lọc `user_id`, không chỉ dựa vào RLS. Chỉ `pending_translate_users` chạy role worker (luôn lọc `user_id`, không đọc nội dung mail). Hàm dùng chung mới phải kiểm tra điều này.
- Worker cần `$env:PMM_SECRET_KEY` để giải mã key nhà cung cấp của người dùng (không có thì job dịch dừng, thử lại phút sau). Người dùng chưa nhập nhà cung cấp (hoặc đã xóa) thì `LlmResolver.for_user` trả `None` và job dịch dừng, không có khóa môi trường để rơi về (spec 28); thiếu tên model dịch thì cũng dừng. Job dịch không gọi ml-service.
- `app/worker.py` chạy hai vòng `run_worker_async` trong một process: `queues=["default"]` (làm sạch, gom thread, sửa tay, embed, gán dự án, `translate_sweep`) và `queues=["translate"]` (`translate_user`, một lần chờ AI tới 120 giây). Một vòng kết thúc thì dừng vòng kia; không dùng `gather`.
- `translate_user` xử lý một mail rồi tự đẩy tiếp (`queueing_lock` theo người dùng). Lỗi: theo mail (`ai_bad_output`, `ai_truncated`, `ai_refused`, và `translate_failed` cho lỗi lạ) thì ghi `retry`, chờ `attempts × 10` phút, đủ `MAX_MAIL_ATTEMPTS` = 3 lần thì `failed`; `ai_bad_request` và `ai_timeout` (`UNBOUNDED_RETRY_ERRORS`) ghi `retry` chờ tối đa 60 phút và không bao giờ `failed` (có thể do sai tên model, hoặc mail dài trên model chậm), job đi tiếp mail sau; lỗi tạm khác (chưa cấu hình, thiếu tên model, `ai_auth_failed`, `ai_unavailable`) dừng người dùng, không ghi gì; lỗi hạ tầng (`OperationalError`, `OSError`, `httpx.HTTPError`…) ném ra để job báo lỗi và lần quét sau làm lại. `UPSERT_RUN` chỉ ghi đè dòng `retry`: dòng `done`/`failed` giữ nguyên.
- Dòng `message_auto_runs` đã `failed` với mã cũ `mask_unstable` (trước spec 28) giữ nguyên `failed`: chỉ nút Dịch tay hoặc "Thử lại" (`auto-retry`) xử lý được; code không còn sinh mã này.
- Text gửi AI (subject, body, ngữ cảnh mail trước, các dòng glossary) là text gốc đã cắt bằng `cut_text`, không che. Glossary khớp trên text gốc đã cắt. Không log text, giá trị glossary, tên dự án, domain.
- **Folder hiệu lực**: chuyển mail chỉ ghi `message_auto_runs.target_source_id`, `message_ownership` không đổi (đồng bộ lại không đảo được). Mọi nơi hiển thị folder phải tính theo cùng quy tắc: `filter_conditions` (`search/keyword.py`), `folders_of`, `sources.LIST_FOLDERS`. Mail đã có đích thì ownership ở bất kỳ Inbox nào (hai tài khoản đều có Inbox) coi như đã chuyển; `inbox_path_sql` (`mail/domains.py`) và `is_inbox_path` phải cùng nghĩa (đoạn cuối `folder_path` là `inbox`, không phân biệt hoa thường). `LIST_FOLDERS` viết bằng `LEFT JOIN … IS NULL`, không `NOT (… AND EXISTS …)` (1.562 ms thay vì 60 ms trên 50.000 mail). Xóa mail của folder theo ownership vật lý (spec 7).
- `update_project` gọi `retarget_no_folder` trong cùng transaction khi `processed_source_id` đổi: mail đã `done` với `move_note = 'no_folder'` được xét lại. `move-back` xóa note nên không bị đảo. `POST /messages/{id}/move` chỉ cho mail có run `done` (409 `move_not_ready`), chọn lại được cả mail đã chuyển.
- `message_translations` chứa chữ dịch (ngoại lệ có chủ đích với quy tắc không chép nội dung mail): do `app_api` ghi, `ON DELETE CASCADE` tới `messages`. `app_worker` không có quyền gì trên `glossary_terms`, `message_translations` (chỉ `SELECT` trên `message_auto_runs`). Mọi truy vấn bản dịch chạy trong `user_session`.
- Glossary: `term_norm` = NFKC + casefold + bỏ khoảng trắng (`normalize_term`); khớp bằng `nfkc_map` (cả katakana nửa độ rộng, NFD) giữ vị trí gốc, thuật ngữ toàn chữ Latin phải ở ranh giới từ Latin. Xuất CSV có BOM và thêm `'` trước giá trị bắt đầu bằng `= + - @` (xét trên NFKC); nhập bỏ BOM và bỏ đúng một `'`. Không dùng regex trên giá trị người dùng.
- `pytest` đặt `faulthandler_timeout = 300` (`pyproject.toml`) để lần treo sau còn stack.

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
