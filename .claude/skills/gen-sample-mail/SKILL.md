---
name: gen-sample-mail
description: Sinh lại profile Thunderbird giả (mbox, thread reply/forward, tiếng Nhật kèm bản dịch tiếng Việt) trong data/thunderbird-profile để chạy thử và test. Dùng khi người dùng cần tạo, làm mới hoặc đổi số lượng mail mẫu.
argument-hint: "[--total N] [--threads M]"
---

# Tạo mail mẫu

Sinh profile Thunderbird **giả** (toàn bộ nội dung tự bịa, tên miền `*.example.*`) bằng `data/generate_sample_profile.py`. Không đọc mail thật, không đụng `samples/raw/`.

## Kết quả

- `data/thunderbird-profile/`: `profiles.ini`, `prefs.js`, `ImapMail/imap.vn-outsource.example.com/` (INBOX, Sent, `INBOX.sbd/ProjectA|B`) và `Mail/Local Folders/` (Inbox, Sent, `Project-C` … `Project-G`), kèm `.msf`.
- `data/expected_threads.json`: đáp án gom thread (nhóm → danh sách Message-ID) để làm golden test.
- Mặc định: 51 mail mẫu nhỏ (9 thread + mail lẻ + 1 mail trùng Message-ID) và 1000 mail bulk (70 thread, mỗi thread 10–20 mail, có reply và forward, mail tiếng Nhật kèm bản dịch tiếng Việt, mail nội bộ tiếng Việt). Charset: ISO-2022-JP, Shift_JIS, UTF-8.

## Các bước

1. Xác nhận với người dùng: chạy lại sẽ **xóa và tạo lại toàn bộ** `data/thunderbird-profile/`. Nếu người dùng vừa trỏ collector vào profile này và đang đồng bộ, nhắc họ đồng bộ lại sau khi sinh. Thunderbird hoặc collector đang mở profile sẽ giữ khóa file (`.msf`) và script dừng với thông báo "Không ghi được ...": yêu cầu người dùng đóng chúng rồi chạy lại, không tự tắt tiến trình của họ.
2. Chạy từ thư mục `Email Assistant`:
   - Mặc định: `python data/generate_sample_profile.py`
   - Tùy số lượng: `python data/generate_sample_profile.py --threads M --total N` (`M*10 <= N <= M*20`). Lấy `N`, `M` từ `$ARGUMENTS`; nếu người dùng chỉ nói tổng số mail thì chọn `M` sao cho `N/M` nằm trong 10–20 (ví dụ `M = N/14`).
3. Kiểm tra bằng số liệu tổng hợp, không in nội dung mail: dòng cuối của script in số file mbox, số mail, số nhóm thread. Đối chiếu tổng số mail với yêu cầu (51 mẫu nhỏ + số bulk).
4. Chạy lại test có dùng profile: `go test -C collector ./...` và `uv run --directory backend pytest tests/test_threading.py tests/test_e2e_sample.py`. Test fail vì số mail thay đổi thì báo người dùng, không tự sửa số mong đợi.
5. Báo lại người dùng: số mail, số thread, đường dẫn profile (`data/thunderbird-profile`), kết quả test.

## Lưu ý

- Mô tả đầy đủ (cấu trúc, nội dung, nơi dùng, giới hạn): spec 12 `docs/superpowers/specs/2026-10-18-sample-profile-generator-design.md`. Sửa script thì cập nhật spec đó (`.claude/rules/documentation.md`).
- Dữ liệu này là giả, đặt ở `data/`. Test được phép dùng nó (`.claude/rules/test-data.md`), nhưng nó **không** thay thế `samples/anonymized/` cho các trường hợp cần mẫu từ mailer thật.
- Không thêm tài khoản thật vào profile này trong Thunderbird: mail thật sẽ được tải vào repo.
- Muốn thêm chủ đề, người, hay kiểu mail: sửa `TOPICS`, `BULK_JP_NAMES`, `BULK_VN` hoặc `THREADS` trong script rồi chạy lại. Seed cố định nên kết quả lặp lại được; đổi seed hay cấu trúc thì `expected_threads.json` đổi theo.
- Script chỉ dùng thư viện chuẩn của Python 3.
