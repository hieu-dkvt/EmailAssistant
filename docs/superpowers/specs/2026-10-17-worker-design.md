# Spec: Xử lý nền (worker) và công cụ kiểm tra kết quả

Ngày: 2026-10-17 · Thành phần: `backend/app/processing.py`, `backend/app/tasks/`, `backend/app/worker.py`, `backend/app/cli.py`, `backend/app/review.py`, `backend/app/safe_errors.py`, migration 0008 · Trạng thái: xong (sửa sau review bảo mật: mục 8; thêm task `apply_overrides` 2026-10-18: mục 3, 4)

## 1. Mục đích

Sau khi collector đồng bộ, mail tự được làm sạch (spec 9) và gom thread (spec 10) mà người dùng không phải chạy lệnh gì thêm. Người dùng (và Claude Code, khi làm việc với dữ liệu thật) xem được chất lượng bằng số liệu tổng hợp; người dùng tự kiểm tra 100 thread ngẫu nhiên bằng một file HTML cục bộ.

## 2. Không làm

- Không gửi job từ API: ingest và web không cần quyền trên bảng job. Worker tự quét việc còn tồn mỗi phút (sửa tay từ web: mỗi 5 giây).
- Không xử lý AI, embedding (giai đoạn 5, 6 dùng lại worker này).
- Giao diện web: spec 16 (worker không phục vụ web).

## 3. Worker

- Thư viện: Procrastinate (ghim `procrastinate==3.10.0`), hàng đợi trong PostgreSQL.
- **Bảng job nằm ở schema riêng `procrastinate`** (migration 0008 chạy `SchemaManager.get_schema()` của bản đã ghim với `search_path = procrastinate`). Chỉ `app_worker` có quyền trên schema này; `app_api` không thấy bảng job. Downgrade: `DROP SCHEMA procrastinate CASCADE`.
- Kết nối của worker: role `app_worker`, `search_path=procrastinate` cho kết nối của Procrastinate. Code xử lý mail dùng engine SQLAlchemy riêng (role `app_worker`, `hide_parameters=True`).
- Chạy: `uv run --directory backend python -m app.worker`. Trên Windows dùng `SelectorEventLoop` (psycopg async không chạy trên Proactor). Procrastinate bỏ qua bắt tín hiệu trên Windows: Ctrl+C dừng worker; job đang chạy bị hủy, transaction đang mở rollback, lần quét sau làm lại.
- Task:
  - `process_user_mail(user_id)`: làm sạch mail chờ (spec 9 mục 5), rồi gom lại nếu cần (spec 10 mục 6). `queueing_lock` và `lock` = `user:<id>`: mỗi người dùng tối đa một job chờ và một job đang chạy.
  - `sweep` (periodic, mỗi phút; và một lần khi worker khởi động): tìm người dùng có việc tồn (mục 4), đẩy `process_user_mail` cho từng người (đã có job chờ thì bỏ qua).
  - `apply_overrides` (periodic, mỗi 5 giây, cron 6 trường `* * * * * */5`; thêm 2026-10-18, spec 15 mục 6): chỉ đọc bảng nhỏ `user_thread_state`, đẩy `process_user_mail` cho người có sửa tay từ web chưa áp dụng. Tách khỏi `sweep` vì truy vấn của `sweep` quét cả bảng `messages`.
- Concurrency 1. Hỏi bảng job mỗi giây (`fetch_job_polling_interval=1`, trước 2026-10-18 là 5 giây mặc định) để sửa tay có hiệu lực nhanh, không dùng LISTEN/NOTIFY. Xóa job đã chạy xong.
- Log: chỉ `user_id`, số mail, số thread, thời gian, tên lớp lỗi. Không subject, không nội dung, không địa chỉ mail.
- Lỗi trong task được thay bằng `ProcessingError` chỉ gồm tên lớp lỗi, `sqlstate`, tên ràng buộc, vị trí code (`app/safe_errors.py`), `raise … from None`. Lý do: Procrastinate log cả traceback, và thông điệp lỗi PostgreSQL có `DETAIL: Failing row contains (…)` là cả dòng `messages` (subject, địa chỉ, body).
- Người dùng mà job luôn lỗi (bug) bị đẩy lại mỗi phút, mỗi lần một dòng log lỗi đã làm sạch, cho tới khi sửa bug.

## 4. Việc tồn của một người dùng

- Có mail `cleaner_version IS DISTINCT FROM CLEANER_VERSION`.
- Có mail `thread_id IS NULL`.
- Số mail khác tổng `threads.message_count` (mail bị xóa khỏi server).
- Có thread `threader_version <> THREADER_VERSION`.
- Có sửa tay chưa áp dụng: `user_thread_state.overrides_version > applied_overrides_version` (thêm 2026-10-18).

## 5. CLI (`python -m app.cli …`)

| Lệnh | Việc | Chạy được trên dữ liệu thật bởi Claude Code? |
|---|---|---|
| `process-mail --email <e> [--rethread]` | Làm sạch + gom ngay, không cần worker. `--rethread`: gom lại dù không có việc tồn. In số liệu tổng hợp | Có |
| `mail-stats [--email <e>]` | Chỉ số liệu tổng hợp (mục 6) | Có |
| `review-threads --email <e> [--sample 100] [--seed N]` | Xuất HTML gồm N thread ngẫu nhiên để người dùng tự kiểm tra | **Không**: file chứa nội dung mail |

- `process-mail`, `mail-stats` dùng role `app_worker`; mọi truy vấn lọc theo `user_id`. Lỗi chỉ in mô tả an toàn (như mục 3), mã thoát 1.
- `review-threads`:
  - **Chỉ** ghi vào `%LOCALAPPDATA%\PMMailAssistant\review\threads-<thời gian>.html`, không có tùy chọn đường dẫn khác (tránh file nằm trong repo hoặc thư mục đồng bộ cloud như Desktop/Documents). `.claude/settings.json` chặn tool Read/Edit của Claude Code ở thư mục này; `.gitignore` có `threads-*.html`. Mỗi lần chạy xóa file review cũ hơn 7 ngày.
  - Đọc bằng `app_api` trong phiên đã đặt `app.user_id` (`user_connection`), và truy vấn tự lọc `user_id` (khi có chia sẻ theo dự án, RLS sẽ cho thấy cả thread người khác).
  - Mỗi thread: subject (của mail root), số mail, cách gom (`thread_method`), từng mail theo thời gian (ngày, người gửi, subject, `body_clean`, đoạn đã cắt hiện mờ và thu gọn). Ô "Gom sai" để đếm tỷ lệ sai.
  - HTML tự chứa, mọi giá trị được escape. CSP `default-src 'none'`, script chỉ chạy theo hash. `translate="no"` để trình duyệt không gợi ý dịch (gửi nội dung cho dịch vụ dịch). In nhắc người dùng xóa file sau khi kiểm tra.
  - Chọn ngẫu nhiên trong mọi thread (kể cả thread một mail: mail lẽ ra phải nằm trong thread khác sẽ hiện ra là thread một mail).

## 6. `mail-stats`

Theo từng người dùng (hoặc một người):

- Mail: tổng, đã làm sạch theo version, chờ làm sạch, lỗi làm sạch không đếm được (log).
- Làm sạch: số mail có cắt quote, có cắt chữ ký; tổng độ dài `body_text` và `body_clean` (tỷ lệ giảm); số mail `body_clean` rỗng mà `body_text` không rỗng.
- Thread: tổng, số thread một mail, phân bố số mail mỗi thread (1, 2–5, 6–20, >20), thread lớn nhất; số mail theo `thread_method`; số mail chưa gom.

Không in subject, địa chỉ, tên folder hay nội dung.

## 7. Kiểm thử

- Schema `procrastinate` được tạo và xóa sạch khi downgrade; `app_api` không có quyền `USAGE` trên schema.
- Worker chạy thật trên DB test (`run_worker_async(wait=False)`): đẩy `process_user_mail` → mail được làm sạch và gom; chạy `sweep` → chỉ người dùng có việc tồn được đẩy job; job thứ hai cho cùng người dùng khi đã có job chờ không lỗi.
- `process-mail` chỉ đụng mail của người được chỉ định.
- `mail-stats` không in chuỗi nào lấy từ mail (test: dữ liệu có subject/địa chỉ đặc biệt, output không chứa chúng).
- `review-threads`: escape HTML (subject `<script>` hiện thành text); chỉ chứa thread của người được chỉ định; không có URL ngoài.
- E2E: profile tự sinh → collector đồng bộ → worker chạy → `mail-stats` đủ mail đã làm sạch và gom; nhóm thread khớp `data/expected_threads.json`.
  - Thực tế (2026-10-17): `data/thunderbird-profile` đang dở và bị Thunderbird mở, nên thay bằng test tích hợp `test_e2e_sample.py`: sinh profile giả vào thư mục tạm, nạp 1.051 mail vào DB test, `process_user`, so nhóm trong DB với đáp án (khớp 100%).
  - Trên DB dev, worker tự xử lý 4.792 mail sau khi khởi động.
- Lỗi DB có DETAIL chứa nội dung mail (ép bằng `thread_method` sai): log của worker và stderr của `process-mail` chỉ có `type`, `sqlstate`, `constraint`.

## 8. Sửa sau review bảo mật (2026-10-17)

- Lỗi trong task và CLI không còn in thông điệp gốc (mục 3, 5).
- `review-threads`: bỏ `--out`, xóa file cũ hơn 7 ngày, lọc `user_id` tường minh, CSP theo hash, `translate="no"` (mục 5).
- Worker: tắt LISTEN/NOTIFY (NOTIFY không phân quyền được: role nào cũng gửi được lệnh hủy job giả), xóa job đã xong.
- Còn để sau:
  - Review đề xuất thêm luật deny cho lệnh PowerShell/Bash có `review-threads` / `PMMailAssistant` vào `.claude/settings.json`: deny `Read` không chặn được `Get-Content` chạy qua PowerShell. Đây là thay đổi quyền của Claude Code, chờ người dùng quyết.
  - Chưa có audit log khi xuất review (giai đoạn 7 có bảng audit).
  - 0007: nếu `CREATE INDEX CONCURRENTLY` bị ngắt, index INVALID còn lại và `IF NOT EXISTS` bỏ qua nó. DB dev không có index INVALID. Lên production thì kiểm tra `pg_index.indisvalid` sau migration.
  - 0006 kiểm tra CHECK/FK trên toàn bảng trong lúc giữ khóa: lên production với bảng lớn thì dùng `NOT VALID` rồi `VALIDATE`.
