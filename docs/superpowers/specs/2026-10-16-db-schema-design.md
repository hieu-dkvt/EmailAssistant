# Thiết kế: DB và các bảng cho bước đồng bộ mail

Ngày: 2026-10-16 · Thành phần: hạ tầng DB (`docker-compose.yml`, `db/`) và `backend/` (Python)

## 1. Mục đích

Bước tiếp theo của dự án là nút "Đồng bộ": collector đọc các folder đã chọn trên trang setting và đẩy mail vào DB. Bước này dựng nền cho việc đó: chạy PostgreSQL trên máy và tạo các bảng chứa mail, có phân quyền ngay từ đầu.

Sau bước này, chưa có mail nào vào DB. Bước kế tiếp (ingest API và bộ đọc mbox của collector) sẽ ghi vào các bảng này.

## 2. Mục tiêu

1. `docker compose up -d` chạy PostgreSQL 16 có sẵn extension PGroonga và pgvector, chỉ nghe trên `127.0.0.1`.
2. `uv run alembic upgrade head` (trong `backend/`) tạo 4 bảng: `users`, `mail_sources`, `messages`, `message_ownership`. `alembic downgrade base` xóa sạch.
3. Bảng chứa mail bật Row-Level Security. Có test chứng minh người dùng khác không thấy mail của mình.
4. Có helper phiên DB chung trong `backend/app/db/` để các bước sau dùng (đặt `app.user_id` trong transaction).

## 3. Không làm (non-goals)

- Ingest API, đọc mbox, nút "Đồng bộ": bước sau.
- Bảng thread, dự án, AI, embedding, audit: tạo ở giai đoạn dùng tới (mỗi thay đổi schema là một migration mới).
- Ứng dụng FastAPI, đăng nhập, SSO, giao diện web.
- Index tìm kiếm PGroonga và HNSW: làm ở giai đoạn 4 (PGroonga: migration 0012, spec 14) và 5. Bước này chỉ cài sẵn extension.

## 4. Điều tuyệt đối tránh (anti-goals)

- Role mà API dùng có thể đọc mail của người khác (có `BYPASSRLS`, là owner của bảng, hoặc là superuser).
- Lọc quyền ở tầng code Python thay vì trong DB.
- Cổng DB mở ra ngoài máy (bind `0.0.0.0`).
- Mật khẩu thật hoặc `.env` bị commit. Test dùng mail thật.

## 5. Ràng buộc

- PostgreSQL 16, image tự build có PGroonga và pgvector (theo `CLAUDE.md`). Version của image gốc và extension phải được ghim, không dùng `latest`.
- Python 3.12+ (máy đã có 3.12.10), quản lý bằng `uv` (chưa cài, cần cài). SQLAlchemy 2.0, psycopg 3, Alembic, pytest. Ruff và pyright không báo lỗi.
- Code I/O trong `app/` dùng async (quy ước của `backend/CLAUDE.md`). Alembic chạy sync là được.
- Quy tắc trong `.claude/rules/database.md`: RLS cho mọi bảng chứa mail, `SET LOCAL app.user_id`, API không có `BYPASSRLS`, worker dùng role riêng, policy RLS viết bằng SQL trong migration.
- Docker Desktop đã cài nhưng phải được bật trước khi chạy.

## 6. Người dùng đã quyết

- Bước này chỉ dựng DB và tạo bảng.
- **Không lưu bản gốc MIME của mail lên server.** Server lưu text đã giải mã (toàn văn, chưa cắt quote) và metadata. Cần xem bản gốc thì collector mở từ mbox trên máy.
- DB chạy bằng Docker trên chính máy này (giai đoạn một người dùng).
- Chỉ tạo các bảng mà bước đồng bộ cần; bảng khác tạo ở giai đoạn sau.
- **(2026-10-17) Mỗi người dùng có bản `messages` riêng, không dùng chung mail giữa người dùng.** Lý do: nếu dùng chung theo Message-ID, ai biết Message-ID (vd. thấy trong header References của một reply) có thể gửi mail giả cùng Message-ID để được gắn vào bản thật và đọc nó. Đánh đổi: mail gửi cho nhiều người được lưu và xử lý AI ở bản của từng người. Thực hiện bằng migration 0004.

## 7. Thiết kế (quyết định của người viết, builder có thể chỉnh nếu có lý do)

### 7.1 Hạ tầng

```
docker-compose.yml        # service "db"
db/Dockerfile             # PostgreSQL 16 + PGroonga + pgvector
db/init/01-init.sh        # chạy một lần khi tạo volume: role, database, extension
.env.example              # biến môi trường mẫu (mật khẩu dev)
```

- Image: dựa trên `pgvector/pgvector:<ver>-pg16` (Debian, đã có pgvector), cài thêm gói `postgresql-16-pgdg-pgroonga` từ apt repo của Groonga. Nếu cách này vướng, builder có thể đi ngược lại (image PGroonga + gói pgvector của PGDG). Yêu cầu không đổi: PG 16, có cả hai extension, version được ghim.
- Cổng: `127.0.0.1:15432:5432`. Dùng 15432 để không đụng PostgreSQL khác nếu sau này có cài.
- Dữ liệu nằm trong volume Docker tên `pgdata`.
- Mật khẩu: compose đọc từ `.env`, nếu không có thì dùng giá trị dev mặc định (`${VAR:-...}`). `.env` đã nằm trong `.gitignore`. Claude Code không đọc `.env` (đã chặn trong settings).

### 7.2 Role và database (tạo trong `db/init/01-init.sh`)

| Role | Thuộc tính | Dùng cho |
|---|---|---|
| `app_owner` | LOGIN, không superuser | Chủ các bảng, chạy migration |
| `app_api` | LOGIN, `NOBYPASSRLS` | API phục vụ người dùng, luôn chịu RLS |
| `app_worker` | LOGIN, `BYPASSRLS` | Job nền (dedupe, gom thread…) |

- Hai database: `mailassist` (dùng thật) và `mailassist_test` (pytest), cả hai thuộc `app_owner`.
- Script init chạy với superuser `postgres` của image: tạo role, tạo database, `CREATE EXTENSION pgroonga` và `CREATE EXTENSION vector` trong cả hai database (hai extension này cần superuser để tạo).
- Quyền trên bảng (`GRANT`) nằm trong migration, không nằm trong script init.

### 7.3 Bảng

**`users`**

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity, PK | |
| `email` | `text` not null, unique | lưu chữ thường |
| `display_name` | `text` not null | |
| `role` | `text` not null default `'member'` | check: `admin`, `pm`, `member` |
| `created_at` | `timestamptz` not null default `now()` | |

Không bật RLS (không chứa mail). `app_api` chỉ được `SELECT`.

**`mail_sources`**: mỗi folder mbox của mỗi người dùng.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity, PK | |
| `user_id` | `bigint` not null → `users` | |
| `kind` | `text` not null | check: `local`, `imap_cache` (trùng `kind` trong `config.json`) |
| `account` | `text` not null | vd. `imap.vn-outsource.example.com`, `Local Folders` |
| `folder_path` | `text` not null | đường dẫn tương đối trong profile, giống `config.json` |
| `file_size` | `bigint` null | collector báo; chỉ để hiển thị |
| `last_offset` | `bigint` null | collector báo; chỉ để hiển thị |
| `last_mtime` | `timestamptz` null | collector báo; chỉ để hiển thị |
| `last_scan_at` | `timestamptz` null | |
| `created_at` | `timestamptz` not null default `now()` | |

- Unique `(user_id, folder_path)`, và unique `(id, user_id)` để `message_ownership` tham chiếu kép.
- Trạng thái quét tăng dần thật sự nằm trong SQLite của collector. Các cột `file_size`, `last_offset`, `last_mtime` chỉ để màn hình hiển thị, không dùng để quyết định quét.
- Giới hạn đã biết: mỗi người dùng một collector (một máy, một profile), giống trang setting. Hai máy có cùng `Mail/Local Folders/Inbox` sẽ bị coi là một folder. Khi cần nhiều máy cho một người, thêm cột định danh collector bằng migration mới.
- Tạo người dùng đầu tiên (dòng trong `users`) là việc của bước ingest, cùng với token của collector.

**`messages`**: bản mail của một người dùng (mỗi người một bản, xem mục 6). Trong một người dùng, cùng mail ở nhiều folder vẫn là một bản.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity, PK | |
| `user_id` | `bigint` not null → `users` on delete cascade | người sở hữu bản mail này |
| `message_id_hash` | `text` not null | SHA-256 hex; khóa chống trùng là `(user_id, message_id_hash)` |
| `message_id` | `text` null | Message-ID gốc, bỏ `< >` |
| `subject` | `text` null | |
| `from_addr` | `text` null | chữ thường |
| `from_name` | `text` null | |
| `to_addrs` | `jsonb` not null default `'[]'` | `[{"name": ..., "addr": ...}]` |
| `cc_addrs` | `jsonb` not null default `'[]'` | như trên |
| `sent_at` | `timestamptz` null | từ header Date, null nếu không đọc được |
| `sent_tz_offset_min` | `smallint` null | múi giờ trong header Date (vd. +540 cho JST), để sau này hiểu "来週水曜日" theo giờ người gửi |
| `in_reply_to` | `text` null | |
| `references_ids` | `text[]` not null default `'{}'` | header References (`references` là từ khóa SQL) |
| `thread_index` | `text` null | header Thread-Index của Outlook |
| `body_text` | `text` not null default `''` | toàn văn đã giải mã, chưa cắt quote/chữ ký |
| `body_from_html` | `boolean` not null default `false` | text được chuyển từ HTML |
| `has_attachments` | `boolean` not null default `false` | |
| `attachments` | `jsonb` not null default `'[]'` | `[{"filename", "size", "content_type"}]`, không có nội dung file |
| `parser_version` | `text` not null | phiên bản bộ parse của collector, để tìm mail cần parse lại khi sửa lỗi giải mã |
| `created_at` | `timestamptz` not null default `now()` | |

- Unique `(user_id, message_id_hash)`, và unique `(id, user_id)` cho FK kép từ `message_ownership`.
- Index `(user_id, sent_at)`, `(user_id, from_addr)`: mọi truy vấn đều lọc theo người dùng (RLS).
- Các cột của giai đoạn sau (`subject_norm`, `body_clean`, `cleaner_version`, `body_lang`, `thread_id`) sẽ thêm bằng migration ở giai đoạn đó.

**`message_ownership`**: người dùng nào có mail nào, ở folder nào, vị trí nào.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `message_id` | `bigint` not null | FK `(message_id, user_id)` → `messages(id, user_id)` on delete cascade: chỉ gắn được vào mail của đúng người dùng |
| `user_id` | `bigint` not null | |
| `source_id` | `bigint` not null | FK `(source_id, user_id)` → `mail_sources(id, user_id)` on delete cascade, bảo đảm folder thuộc đúng người dùng |
| `mbox_offset` | `bigint` not null | vị trí dòng `From ` trong mbox |
| `is_read` | `boolean` not null default `false` | từ `X-Mozilla-Status` |
| `is_replied` | `boolean` not null default `false` | như trên; giai đoạn 6 dùng để xác định "chờ mình / chờ khách" |
| `is_flagged` | `boolean` not null default `false` | như trên |
| `is_deleted` | `boolean` not null default `false` | mail đã biến mất khỏi folder |
| `first_seen_at` | `timestamptz` not null default `now()` | |
| `last_seen_at` | `timestamptz` not null default `now()` | |

- PK `(message_id, source_id)`: cùng một mail trong hai folder của cùng người dùng là hai dòng. Gửi lại cùng mail cùng folder không tạo dòng mới.
- Index `(user_id, message_id)`.

### 7.4 Row-Level Security

- Hàm `app_user_id()` trả `nullif(current_setting('app.user_id', true), '')::bigint`. Chưa đặt `app.user_id` thì trả null, và mọi policy dưới đây không trả dòng nào.
- Bật RLS trên `mail_sources`, `messages`, `message_ownership`:
  - `mail_sources`: mọi thao tác chỉ trên dòng có `user_id = app_user_id()`.
  - `message_ownership`: như trên.
  - `messages`: `SELECT` khi `user_id = app_user_id()` (từ 0004; trước đó là EXISTS qua `message_ownership`). Chia sẻ theo dự án sẽ thêm policy ở giai đoạn có bảng dự án.
- Quyền của `app_api`:
  - `users`: `SELECT`.
  - `mail_sources`: `SELECT`, `INSERT`, `UPDATE`, `DELETE` (RLS giới hạn trong dữ liệu của chính người đó).
  - `message_ownership`: `SELECT`, `DELETE`, và `UPDATE` chỉ trên cột `is_read`, `is_replied`, `is_flagged`. **Không có `INSERT` và không sửa được `message_id`/`source_id`/`user_id`**: nếu có, người dùng tự gắn mình vào mail bất kỳ (id tăng dần, đoán được) rồi đọc toàn văn. Ownership chỉ được tạo bởi hàm ingest bên dưới. (Bổ sung 2026-10-17 sau review bảo mật, migration 0003.)
  - `messages`: chỉ `SELECT`.
- `app_user_id()` khai báo `PARALLEL SAFE` (nằm trong mọi policy; nếu không, truy vấn trên bảng mail không chạy song song được).
- Hàm quản trị của PGroonga (`pgroonga_command`, `pgroonga_set_writable`, `pgroonga_wal_*`, `pgroonga_flush`, `pgroonga_vacuum`) bị thu hồi khỏi PUBLIC trong script init: chúng đọc/ghi index Groonga mà không qua RLS.
- Log: server chạy `log_error_verbosity=terse` và engine bật `hide_parameters`, để lỗi ràng buộc không đưa nội dung dòng (nội dung mail) vào log hay exception.
- `app_worker`: toàn quyền đọc/ghi các bảng, `BYPASSRLS`.
- **Ghi `messages` khi ingest (để bước sau làm):** chống trùng chỉ trong phạm vi một người: `INSERT ... ON CONFLICT (user_id, message_id_hash)` với `user_id = app_user_id()`, rồi thêm `message_ownership` cho folder của chính người đó. Không bao giờ tra mail của người khác. Hiện `app_api` chưa có quyền `INSERT` trên `messages` và `message_ownership`. Bước ingest chọn một trong hai cách: cấp `INSERT` kèm policy `WITH CHECK (user_id = app_user_id())` (FK kép đã bảo đảm ownership chỉ trỏ tới mail của chính người đó), hoặc dùng hàm `SECURITY DEFINER`. Cách viết trước 0004 (dùng chung mail theo `message_id_hash`) đã bỏ.

### 7.5 Code backend

```
backend/
  pyproject.toml          # uv; sqlalchemy, psycopg[binary], alembic; dev: pytest, pytest-asyncio, ruff, pyright
  alembic.ini
  migrations/env.py
  migrations/versions/0001_initial.py   # bảng, index, RLS, GRANT (SQL viết tay cho RLS/GRANT)
  app/__init__.py
  app/config.py           # đọc URL kết nối từ biến môi trường
  app/db/models.py        # model SQLAlchemy 2.0 cho 4 bảng
  app/db/session.py       # engine async; user_session(user_id): transaction + set_config('app.user_id', ..., true)
  tests/conftest.py       # migrate DB test lên head, dọn dữ liệu giữa các test
  tests/test_schema.py
  tests/test_rls.py
```

- Biến môi trường: `DATABASE_URL_OWNER` (migration, test seed), `DATABASE_URL_API`, `DATABASE_URL_WORKER`. `.env.example` ghi sẵn giá trị dev trỏ tới `127.0.0.1:15432`.
- `user_session(user_id)` là cách duy nhất để code phục vụ người dùng lấy phiên DB: mở transaction, gọi `set_config('app.user_id', <id>, true)` (tương đương `SET LOCAL`, nhận tham số bind) rồi mới trả session.

## 8. Kiểm thử

Test chạy trên database `mailassist_test` trong container. Dữ liệu là dòng tự tạo trong test, không có mail thật.

- **Schema:** migrate lên head có đủ 4 bảng; RLS bật trên 3 bảng; `downgrade base` rồi `upgrade head` lại chạy được.
- **Ràng buộc:**
  - trùng `(user_id, message_id_hash)` → lỗi; cùng hash ở hai người dùng → hợp lệ (hai bản);
  - `message_ownership` trỏ tới `mail_sources` hoặc `messages` của người khác → lỗi;
  - `kind`, `role` ngoài danh sách → lỗi.
- **RLS** (kết nối bằng `app_api`):
  - A thấy mail của A, B không thấy mail của A (`messages`, `message_ownership`, `mail_sources`).
  - Mail cả A và B cùng nhận: mỗi người có bản riêng và chỉ thấy bản của mình.
  - Không đặt `app.user_id` → không thấy dòng nào.
  - `app_api` không `INSERT`/`UPDATE` được `messages`; không chèn được `mail_sources` với `user_id` của người khác.
  - `app_api` không có `BYPASSRLS` và không phải owner của bảng (kiểm tra trong `pg_roles`/`pg_tables`).
- **Helper:** `user_session(A)` chỉ thấy dữ liệu của A; sau khi transaction kết thúc thì `app.user_id` không còn hiệu lực.
- Lệnh: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`.

## 9. Kết quả người dùng thấy được

Sau khi xong: bật Docker Desktop, chạy `docker compose up -d` ở thư mục gốc rồi `uv run alembic upgrade head` trong `backend/`. Lệnh `docker compose exec db psql -U app_owner -d mailassist -c "\dt"` liệt kê 4 bảng. Toàn bộ test đạt.

## 10. Để builder tự quyết

- Version cụ thể của image và extension (ghim, kiểm tra tồn tại).
- Cách dọn dữ liệu giữa các test (truncate hay transaction rollback).
- pytest-asyncio hay anyio.
- Tên index và constraint.
