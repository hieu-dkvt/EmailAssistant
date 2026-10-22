# Spec: API xem thread, toàn văn mail và gộp/tách thread bằng tay

Ngày: 2026-10-18 · Thành phần: `backend/app/threads_view.py`, `backend/app/overrides.py`, `backend/app/api/web_routes.py`, `backend/app/processing.py`, `backend/app/tasks/`, migration 0011 · Trạng thái: xong (sửa sau review bảo mật: mục 10)

## 1. Mục đích

Web (spec 16) xem danh sách thread, mở thread dạng hội thoại, xem toàn văn một mail (gồm phần quote/chữ ký đã cắt), và sửa thread gom sai bằng tay (tách, gộp, hoàn tác). Sửa tay lưu ở `thread_overrides` (spec 10 mục 4.4) và được worker áp dụng trong vài giây.

## 2. Không làm

- **Danh sách thread theo dự án**: chờ gán dự án. Danh sách hiện là mọi thread của người dùng, có bộ lọc folder, người gửi, ngày, file đính kèm.
- **Mở mail gốc trong Thunderbird / qua collector theo offset mbox**: để sau (cần collector mở cổng cục bộ cho web gọi, có xác thực riêng). "Toàn văn" hiện lấy `body_text` đã có trên server (text đã giải mã, không phải MIME gốc), không lưu thêm gì lên server.
- Cờ "khóa riêng tư" của thread: làm cùng chia sẻ theo dự án.
- Trạng thái đối ứng, tóm tắt: giai đoạn 6.

## 3. Dữ liệu (migration 0011)

**`user_thread_state`**: báo cho worker có sửa tay chưa áp dụng.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | `bigint` PK → `users` on delete cascade | |
| `overrides_version` | `bigint` not null default 0 | API tăng mỗi lần thêm hoặc xóa sửa tay |
| `applied_overrides_version` | `bigint` not null default 0 | worker ghi sau mỗi lần gom |
| `updated_at` | `timestamptz` not null default `now()` | |

- RLS: policy `user_id = app_user_id()` cho `SELECT`, `INSERT`, `UPDATE`. `app_api`: `SELECT`, `INSERT (user_id, overrides_version)`, `UPDATE (overrides_version, updated_at)`. `app_worker`: `SELECT, INSERT, UPDATE, DELETE`. Không chứa nội dung mail.

**`thread_overrides`** (đã có từ 0006): cấp thêm cho `app_api` `INSERT (user_id, action, message_id, target_message_id)` và `DELETE`; policy `FOR INSERT WITH CHECK (user_id = app_user_id())`, `FOR DELETE USING (user_id = app_user_id())`. FK kép `(message_id, user_id)` và `(target_message_id, user_id)` bảo đảm chỉ tham chiếu mail của chính người đó.

## 4. Đọc (`app/threads_view.py`)

Mọi endpoint xác thực bằng phiên web (spec 13), chạy trong `user_session`. Join giữa `threads`, `messages`, `message_ownership`, `mail_sources` luôn kèm `user_id` của dòng bên trái (khi có chia sẻ theo dự án, RLS cho thấy dòng của người khác, nhưng không được ghép mail của người này vào thread của người kia).

### 4.1 `GET /api/v1/folders`

Danh sách folder như `/api/v1/sources` của collector (id, đường dẫn, số mail), cho bộ lọc.

### 4.2 `GET /api/v1/threads`

- Tham số: `folder_id`, `sender`, `sent_after`, `sent_before`, `has_attachments` (cùng nghĩa với search, spec 14 mục 5), `cursor`, `limit` (1–100, mặc định 50).
- Thread được chọn khi có **ít nhất một mail** thỏa đồng thời mọi bộ lọc mức mail.
- Thứ tự: `last_message_at` giảm dần (null cuối), rồi `id` giảm dần, có index khớp đúng thứ tự `ix_threads_list (user_id, last_message_at DESC NULLS LAST, id DESC)` (migration 0014; trang đầu 9 ms với 100.000 thread, spec 14 mục 10). Phân trang keyset: `next_cursor` là base64url của `[last_message_at, id]` của dòng cuối; null khi hết. Cursor hỏng → 400 `{"code": "bad_cursor"}`.
- Mỗi dòng: `id`, `subject` (của mail `thread_method = 'root'`), `message_count`, `first_message_at`, `last_message_at`, `last_from_name`, `last_from_addr` (mail mới nhất), `has_attachments` (thread có mail có đính kèm).

### 4.3 `GET /api/v1/threads/{id}?q=`

- Không thấy thread (không có, của người khác) → 404 `{"code": "thread_not_found"}`.
- `q` (tùy chọn, cú pháp spec 14 mục 4): tô từ khóa trong subject và nội dung; `keywords` trả về để web hiển thị.
- Mail theo `sent_at` (null cuối), `id`. Tối đa 1.000 mail (`truncated = true` nếu còn).
- Mỗi mail: `id`, `thread_parent_id`, `thread_method`, `sent_at`, `from_name`, `from_addr`, `to`, `cc`, `subject` (đoạn tô), `body` (đoạn tô của `body_clean`; chưa làm sạch thì `body_text`), `cleaned`, `cut_count` (số đoạn đã cắt), `body_truncated` (nội dung bị cắt, xem toàn văn để đọc hết), `has_attachments`, `attachments` (metadata: tên, kích thước, loại), `folders` (`[{id, path}]`).
- Giới hạn nội dung: mỗi mail tối đa 200.000 ký tự, cả thread tối đa 2.000.000 ký tự (mail theo thứ tự thời gian, mail sau nhận phần còn lại, có thể là 0). Tính trong SQL (window function), không kéo cả nội dung ra khỏi DB. Tô từ khóa chạy ở thread riêng.

### 4.4 `GET /api/v1/messages/{id}`

`{"id", "thread_id"}`. Web dùng sau khi gộp/tách để mở thread mới của mail. 404 `{"code": "message_not_found"}`.

### 4.5 `GET /api/v1/messages/{id}/full?q=`

Toàn văn `body_text` chia theo `clean_cuts`: `parts: [{"kind": "body" | "quote" | "signature", "segments": [...]}]` theo thứ tự trong mail. Chưa làm sạch → một phần `body`. Tô từ khóa nếu có `q`.

### 4.6 `GET /api/v1/processing`

`{"rethread_pending": bool, "unprocessed_messages": int}`: có sửa tay chưa áp dụng (`overrides_version > applied_overrides_version`); số mail chưa làm sạch theo version hiện hành hoặc chưa gom (đếm tối đa 10.001). Web hỏi định kỳ khi đang chờ.

## 5. Sửa tay (`app/overrides.py`)

| Endpoint | Việc |
|---|---|
| `POST /api/v1/thread-overrides` | Body `{"action": "split", "message_id"}` hoặc `{"action": "merge", "message_id", "target_message_id"}` → 201 sửa tay vừa tạo |
| `GET /api/v1/thread-overrides` | Danh sách sửa tay, mới nhất trước: `id`, `action`, `created_at`, `message`, `target` (mỗi mail: `id`, `thread_id`, `subject`, `from_name`, `from_addr`, `sent_at`) |
| `DELETE /api/v1/thread-overrides/{id}` | Hoàn tác, 204. Không thấy → 404 `{"code": "override_not_found"}` |

Nghĩa (spec 10 mục 4.4): `split` M tách M và các mail trả lời nó thành thread riêng; `merge` (M, T) gộp thread chứa M vào thread chứa T.

Kiểm tra trước khi ghi (trong cùng transaction):

- Mail hoặc mail đích không thấy được → 404 `{"code": "message_not_found"}`.
- Mail chưa gom thread (`thread_id` null) → 409 `{"code": "not_threaded"}`.
- `split` mail đầu thread (`thread_method = 'root'`) → 400 `{"code": "cannot_split_root"}` (không có gì để tách).
- `merge` hai mail đang cùng thread → 400 `{"code": "same_thread"}`.
- Đã có sửa tay giống hệt (cùng action, mail, mail đích) → 200, trả bản đã có, không ghi thêm.
- Ghi xong thì tăng `user_thread_state.overrides_version` (upsert) trong cùng transaction. Xóa cũng vậy.

Log: `override user=<id> action=<a> override=<id>`; không có subject.

## 6. Worker áp dụng sửa tay

- `rethread_user` (spec 10 mục 4.6): sau khi lấy advisory lock, đọc `overrides_version` = v **trước** khi đọc `thread_overrides`; cuối transaction ghi `applied_overrides_version = v` (nếu đang nhỏ hơn). Sửa tay được commit trước lúc đọc v thì chắc chắn đã nằm trong lần đọc `thread_overrides` (READ COMMITTED, câu sau thấy mọi thứ câu trước thấy); sửa tay commit sau thì làm version lớn hơn v, nên lần sau gom tiếp.
- Việc tồn của người dùng (spec 11 mục 4) có thêm: `overrides_version > applied_overrides_version`.
- Task periodic mới `apply_overrides` **mỗi 5 giây**: chỉ đọc `user_thread_state` (bảng nhỏ), đẩy `process_user_mail` cho người có sửa tay chưa áp dụng (cùng `queueing_lock` theo người dùng). `sweep` mỗi phút giữ nguyên (truy vấn nặng hơn).
- Sau khi gộp, id thread đi theo nhóm có nhiều mail chung hơn (spec 10 mục 4.6), nên web mở lại thread qua `GET /messages/{id}`.

## 7. Phân quyền và dữ liệu

- Đọc chỉ qua `app_api` + RLS. 404 cho thread/mail của người khác (không phân biệt "không có" với "không được xem").
- Ghi `thread_overrides`: API kiểm tra mail thấy được, FK kép chặn ở tầng DB, policy `WITH CHECK`. Không có endpoint ghi `threads` hay `messages`.
- `user_thread_state` không có nội dung mail. Xóa mail của folder xóa override của mail đó (cascade, đã có).
- Response có nội dung mail: `Cache-Control: no-store` (spec 16).

## 8. Kiểm thử

- Danh sách: thứ tự, keyset qua nhiều trang (gồm thread không có ngày), mỗi bộ lọc, cursor hỏng → 400.
- Chi tiết: thứ tự mail, subject của root, tô từ khóa, `truncated`, `body_truncated`, folder, đính kèm; toàn văn chia đúng phần quote/chữ ký.
- Sửa tay: tạo split/merge, trùng → 200 không ghi thêm, các mã lỗi, xóa; version tăng; worker gom lại đúng (split tách cả cây; merge gộp; hoàn tác trả về như cũ).
- Worker: override mới → `apply_overrides` đẩy job; sau gom `applied_overrides_version` = version đã đọc; override commit giữa lúc gom → lần sau vẫn gom lại.
- **Phân quyền**: B không xem được thread, mail, toàn văn, override của A (404); B không tạo override trên mail của A, không merge mail của mình vào mail của A (404); B không xóa override của A; `app_api` không ghi `applied_overrides_version`, không `UPDATE` `thread_overrides`; không phiên → 401.

## 9. Giới hạn đã biết

- Sửa tay có hiệu lực sau vài giây (task 5 giây + thời gian gom lại toàn bộ mail của người dùng); web hiện "Đang gom lại…" trong lúc chờ.
- Hoàn tác một `merge` sau khi đã có mail mới nối hai thread bằng header thì hai thread vẫn chung (đúng theo thuật toán).
- Thread hơn 1.000 mail chỉ hiện 1.000 mail đầu.
- Người dùng tạo/xóa sửa tay liên tục buộc worker (concurrency 1) gom lại toàn bộ mail của họ mỗi 5 giây, làm chậm xử lý của người khác. Chưa giới hạn tần suất (giai đoạn một người dùng); giai đoạn 7 thêm giới hạn theo người dùng.

## 10. Sửa sau review bảo mật (2026-10-19)

- Thread lớn (1.000 mail × 200.000 ký tự) từng có thể tạo response hàng trăm MB và chặn event loop khi tô từ khóa: thêm giới hạn 2.000.000 ký tự cho cả thread (mục 4.3), tô từ khóa ở thread riêng (cả toàn văn). Có test.
- Ghi giới hạn tạo/xóa sửa tay liên tục (mục 9).
