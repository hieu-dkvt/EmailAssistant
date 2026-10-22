# Spec: Ingest API (backend)

Ngày: 2026-10-17 · Thành phần: `backend/app/api/routes.py`, `backend/app/ingest.py`, `backend/app/mail/hashing.py`, `backend/app/schemas.py`, migration 0005 · Trạng thái: xong (sửa sau review 2026-10-17: mục 2, 3)

## 1. Mục đích

Nhận lô mail đã trích xuất từ collector, ghi vào DB của đúng người dùng, idempotent (gửi lại không tạo bản ghi mới).

## 2. Endpoint

`POST /api/v1/ingest`, `Authorization: Bearer <token>`, `Content-Type: application/json`, có thể `Content-Encoding: gzip`.

- Giới hạn: body nén ≤ 16 MiB, sau giải nén ≤ 64 MiB (giải nén có giới hạn, chống gzip bomb) → vượt trả 413; gzip hỏng trả 400. ≤ 500 mail mỗi lô → vượt trả 422 (lỗi dữ liệu).
- Body (Pydantic, `extra="forbid"`):

```json
{
  "schema_version": 1,
  "parser_version": "collector/0.4.0",
  "source": {"folder_path": "ImapMail/host/INBOX", "account": "host", "kind": "imap_cache",
             "file_size": 123, "last_offset": 100, "last_mtime": "2026-10-17T09:00:00+07:00"},
  "messages": [{
    "message_id": "abc@example.com", "subject": "...",
    "from": {"name": "...", "addr": "a@example.com"}, "to": [], "cc": [],
    "date": "2026-10-17T09:00:00+09:00", "date_tz_offset_min": 540,
    "in_reply_to": null, "references": [], "thread_index": null,
    "body_text": "...", "body_from_html": false,
    "attachments": [{"filename": "a.pdf", "size": 10, "content_type": "application/pdf"}],
    "mbox_offset": 0, "flags": {"read": true, "replied": false, "flagged": false}
  }]
}
```

- Giới hạn trường: `folder_path` ≤ 1024, `body_text` ≤ 2.000.000 ký tự, `to`/`cc` ≤ 1000 địa chỉ, `references` ≤ 500, `attachments` ≤ 200 (chỉ 3 khóa), `date_tz_offset_min` trong [-840, 840]; `mbox_offset`, `file_size`, `last_offset`, `attachments[].size` trong [0, 2^63−1] (vượt → 422 thay vì lỗi 500 của PostgreSQL). Ký tự NUL bị bỏ ở mọi chuỗi.
- `date`, `last_mtime` có năm (giờ gốc hoặc sau khi đổi sang UTC) ngoài 1970–2100, hoặc không đổi được sang UTC → coi như `null`, không từ chối cả lô. Collector cũng tự lọc (spec trích xuất mục 4); đây là lớp bảo vệ thứ hai.
- `parser_version` phải dạng `collector/X.Y.Z` và ≥ `collector/0.4.0`, nếu không trả **426** "Collector quá cũ: hãy cập nhật collector rồi đồng bộ lại" (kiểm tra sau validate, trước khi ghi DB). Lý do: parser cũ hơn gửi nội dung file đính kèm text trong `body_text`. Tăng mức tối thiểu (`MIN_PARSER_VERSION` trong `backend/app/ingest.py`) mỗi khi một bản parser cũ bị phát hiện gửi dữ liệu không được phép.
- Response 200: `{"source_id", "received", "inserted", "duplicates", "mismatched"}`.
- Lỗi DB (`DBAPIError`) → 500 `{"detail": "Lỗi cơ sở dữ liệu"}`. Log chỉ có đường dẫn API, `sqlstate`, tên ràng buộc, tên lớp lỗi: thông điệp của PostgreSQL có thể chứa dòng dữ liệu (`DETAIL: Failing row contains ...`).

`POST /api/v1/sources/delete-mail` (spec xóa mail) cũng đọc body bằng `read_body` **sau khi xác thực** token: tham số body của FastAPI được parse trước dependency, nên client chưa xác thực có thể bắt server đọc và parse body.

## 3. Xử lý (một transaction trong `user_session`)

1. Upsert `mail_sources` theo `(user_id, folder_path)`: cập nhật `account`, `kind`, `file_size`, `last_offset`, `last_mtime`, `last_scan_at = now()`.
2. Mỗi mail: tính `message_id_hash` (mục 4); `INSERT ... ON CONFLICT (user_id, message_id_hash) DO NOTHING RETURNING id`; không có dòng trả về → `SELECT id` mail đã có của chính người đó. **Không dùng `DO UPDATE`** (mail giả cùng Message-ID sẽ ghi đè bản thật).
3. Khi trùng, so dấu vân tay (`from_addr`, `sent_at`) với bản đã lưu; khác → đếm `mismatched` (chỉ log số lượng, giữ bản cũ) và **bỏ qua bước 4** cho mail này: mail giả cùng Message-ID không được làm `mbox_offset` của bản thật trỏ sang mail giả.
4. Upsert `message_ownership` theo `(message_id, source_id)`: `mbox_offset`, cờ, `last_seen_at = now()`, `is_deleted = false`.
5. Commit. Lỗi bất kỳ → rollback cả lô (collector gửi lại cả lô).

## 4. `message_id_hash`

- Có Message-ID (đã bỏ `< >` và khoảng trắng): `sha256("mid:" + lower(message_id))`, hex.
- Không có: `sha256("nomid:" + lower(from_addr) + "\n" + date_utc_iso + "\n" + subject + "\n" + body_text[:200])`, giá trị thiếu thay bằng chuỗi rỗng.
- Phạm vi chống trùng: trong một người dùng (khóa `(user_id, message_id_hash)`).

## 5. Quyền DB (migration 0005)

Theo các điều kiện bắt buộc từ review bảo mật của 0004:

- `messages`: `GRANT INSERT (<mọi cột trừ id, created_at>)` cho `app_api`; policy `messages_owner_insert FOR INSERT WITH CHECK (user_id = app_user_id())`. Không cấp `UPDATE`.
- `message_ownership`: `GRANT INSERT (message_id, source_id, user_id, mbox_offset, is_read, is_replied, is_flagged)`, `GRANT UPDATE (mbox_offset, last_seen_at, is_deleted)` (cộng 3 cột cờ đã có). Không bao giờ `UPDATE` trên `message_id`, `source_id`, `user_id`.
- `mail_sources`: thu hồi `INSERT, UPDATE` cả bảng; cấp lại theo cột, không có `id` (`UPDATE` không có `id`, `user_id`, `created_at`).

## 6. Kiểm thử

- Lô hợp lệ → mail, folder, ownership được ghi; gửi lại cùng lô → `inserted = 0`, `duplicates = N`, số dòng không đổi.
- Cùng mail ở hai folder của một người → một mail, hai ownership. Hai người cùng mail → hai bản.
- Mail không có Message-ID → hash dự phòng ổn định.
- Gzip; quá số mail, quá kích thước → 413; trường thừa hoặc sai kiểu → 422 không chứa giá trị gửi lên.
- Bảo mật (bắt buộc): A chèn mail với `user_id = B` → cùng lỗi 42501 dù B có hay không có hash đó; A chỉ định `id` → bị từ chối quyền; A tạo ownership trỏ tới mail của B → lỗi `fk_message_ownership_message_user`; A không đọc được mail của B qua API.
- Thêm sau review: mail giả trùng Message-ID không đổi `mbox_offset` của bản thật; ngày năm 0001 với múi +14:00 → `sent_at` null, lô vẫn 200; `mbox_offset = 2^63` → 422; `delete-mail` không token + body hỏng → 401; lỗi DB không làm lộ giá trị trong log hay response; telemetry FastAPI tắt; `parser_version` `collector/0.3.0`, `0.2.9`, `collector/test`, `other/9.9.9` → 426 và không ghi gì, `collector/0.10.0` → 200 (so số, không so chuỗi).
