# Spec: Xác thực API và người dùng (backend)

Ngày: 2026-10-17 · Thành phần: `backend/app/main.py`, `backend/app/api/deps.py`, `backend/app/cli.py`, migration 0005 · Trạng thái: xong (sửa sau review 2026-10-17: mục 4)

## 1. Mục đích

Collector của mỗi người gọi API bằng token riêng. API biết token thuộc người dùng nào và chạy mọi truy vấn trong `user_session` của người đó (RLS).

## 2. Dữ liệu

Bảng `api_tokens` (không chứa mail, không bật RLS, **không cấp quyền nào cho `app_api`**):

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity PK | |
| `user_id` | `bigint` → `users` on delete cascade | |
| `token_hash` | `bytea` unique | SHA-256 của token; không lưu token gốc |
| `label` | `text` | vd. "collector máy PM" |
| `created_at`, `last_used_at`, `revoked_at` | `timestamptz` | |

Hàm `auth_token_user(p_token_hash bytea) RETURNS bigint`, `SECURITY DEFINER`, chủ là `app_owner`, `SET search_path = pg_catalog, public`: trả `user_id` nếu token tồn tại và chưa thu hồi (cập nhật `last_used_at`), ngược lại `NULL`. `REVOKE ALL ... FROM PUBLIC`, chỉ `GRANT EXECUTE` cho `app_api`. API không đọc được bảng token.

## 3. Token

- Dạng `pmm_` + base64url của 32 byte ngẫu nhiên (`secrets.token_urlsafe(32)`).
- Chỉ hiển thị một lần khi tạo. Không bao giờ ghi vào log.

## 4. API

- Ứng dụng FastAPI `app.main:app`, chạy dev: `uv run --directory backend python -m app.run` (uvicorn trên `127.0.0.1:8000`; trên Windows dùng SelectorEventLoop vì psycopg async không chạy trên ProactorEventLoop).
- Dependency `current_user_id`: đọc `Authorization: Bearer <token>`, băm SHA-256, gọi `auth_token_user`. Thiếu hoặc sai → 401 `{"detail": "Token không hợp lệ"}` kèm `WWW-Authenticate: Bearer`.
- `GET /api/v1/me` → `{"id", "email", "display_name"}`.
- Lỗi validate (422) không trả lại giá trị đầu vào (Pydantic mặc định trả `input`, có thể là nội dung mail): handler riêng chỉ trả `loc`, `msg`, `type`.
- Engine API bật `hide_parameters=True`. Log chỉ có `user_id`, đường dẫn API, mã trạng thái, số lượng, thời gian.
- Lỗi DB: handler riêng chỉ log `sqlstate` và tên ràng buộc, trả 500 chung (spec Ingest API mục 2).
- FastAPI 0.143 bật sẵn OpenTelemetry (trace/metric/log của request, tự cấu hình exporter nếu có biến môi trường). Tắt hẳn bằng `telemetry={"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False}`: không có thông tin request nào được gửi ra ngoài.
- Endpoint có body: đọc body sau khi xác thực (`read_body`), không khai báo body là tham số FastAPI (sẽ bị parse trước dependency xác thực).

## 5. CLI quản trị (`python -m app.cli`, chạy bằng role owner)

- `create-user --email <e> --name <n> [--role pm]` → tạo người dùng và một token, in token.
- `create-token --email <e> [--label <l>]` → in token mới.
- `revoke-token --email <e>` → thu hồi mọi token của người dùng.

## 6. Kiểm thử

- Token đúng → `/me` trả đúng người; token sai, thiếu, đã thu hồi → 401.
- `app_api` không `SELECT` được `api_tokens`; `auth_token_user` không chạy được bằng role không được cấp.
- Hai người dùng: token của A chỉ thấy dữ liệu của A.
- Response 422 không chứa giá trị đầu vào.
