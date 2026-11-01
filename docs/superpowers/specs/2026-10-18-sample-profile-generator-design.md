# Spec: Bộ sinh profile Thunderbird giả (dữ liệu chạy thử và test)

Ngày: 2026-10-18 (viết bù: script có từ 2026-10-16, thêm phần bulk 2026-10-17) · Thành phần: `data/generate_sample_profile.py`, `data/expected_threads.json`, skill `/gen-sample-mail` (`.claude/skills/gen-sample-mail/`) · Trạng thái: xong (2026-10-18: sinh lại profile, test collector lấy số mail từ đáp án: mục 9)

## 1. Mục đích

Tạo một profile Thunderbird có cấu trúc như thật để chạy thử collector, backend, worker và làm golden test mà không phải dùng mail thật (theo NDA, Claude Code không đọc mail thật). Kèm theo là đáp án gom thread để đo thuật toán gom (spec 10).

## 2. Không làm

- Không đọc, không chép, không ẩn danh hóa mail thật. Mẫu lấy từ mail thật là việc của `samples/anonymized/` (`.claude/rules/test-data.md`).
- Không giả lập đúng định dạng `.msf` (Mork): mỗi `.msf` chỉ có dòng đầu, đủ để collector nhận ra folder.
- Không sinh mail cố ý hỏng (Date bất thường, header sai chuẩn, uuencode, charset lạ). Các trường hợp này nằm trong test của từng thành phần.
- Không gán dự án trong generator; gán theo bộ lọc người dùng khai báo nay do spec 25 (`2026-10-31-project-settings-design.md`).

## 3. Chạy

- Chạy từ thư mục `Email Assistant`: `python data/generate_sample_profile.py [--threads M] [--total N]`. Chỉ dùng thư viện chuẩn của Python 3.
  - `--threads` (mặc định 70) và `--total` (mặc định 1000) là tham số của phần bulk. Điều kiện `M*10 <= N <= M*20`, sai thì argparse báo lỗi.
- Hoặc dùng skill `/gen-sample-mail [--total N] [--threads M]`. Skill hỏi người dùng trước khi xóa profile cũ và kiểm tra kết quả chỉ bằng số liệu tổng hợp.
- Script **xóa rồi tạo lại toàn bộ** `data/thunderbird-profile/`. Trước khi xóa, script thử mở ghi từng file: nếu có file bị khóa (Thunderbird hoặc collector đang mở profile) thì dừng với thông báo `Không ghi được <file>: …` và không xóa gì.
- Output là một dòng số liệu, không có nội dung mail: `mbox files: 11, messages written: 1051, threads/groups: 88`.
- Kết quả lặp lại được nhờ seed cố định: `random.Random(42)` cho phần mẫu nhỏ, `random.Random(20261017)` cho phần bulk. Message-ID của phần mẫu nhỏ được sinh trước bulk nên không đổi khi đổi `--threads`/`--total`. Đổi seed hoặc dữ liệu trong script thì Message-ID và `expected_threads.json` đổi theo.
- Test có thể dùng script mà không ghi vào `data/`: import module rồi trỏ `ROOT`, `PROFILE`, `IMAP`, `LOCAL` sang thư mục tạm trước khi gọi `main()` (fixture `sample_profile` trong `backend/tests/test_threading.py`).

## 4. Kết quả (mặc định)

```
data/thunderbird-profile/
├── profiles.ini              # [Profile0] Path=. : chính thư mục này là profile
├── prefs.js                  # 2 tài khoản: IMAP imap.vn-outsource.example.com, Local Folders
├── ImapMail/imap.vn-outsource.example.com/
│   ├── INBOX (11 mail), Sent (17), Drafts (rỗng), Trash (rỗng)
│   └── INBOX.sbd/ProjectA (13), ProjectB (4)
└── Mail/Local Folders/
    ├── Inbox (6), Sent (462), Trash (rỗng), Inbox.sbd/ (thư mục rỗng)
    └── Project-C (108), Project-D (110), Project-E (106), Project-F (104), Project-G (110)
```

- Mỗi mbox có một file `.msf` đi kèm. Mail nhận có `X-Mozilla-Status: 0001` (đã đọc), mail gửi có `0000`. Không có mail nào mang cờ expunged.
- `data/expected_threads.json` có dạng `{"<nhóm>": ["<Message-ID>", …]}` với 88 nhóm: 9 thread mẫu (`A-estimate`, `A-bug`, `A-spec`, `A-minutes`, `B-kickoff`, `B-invoice`, `VN-sprint`, `VN-qa`, `C-license`), 9 mail lẻ (`S1`–`S9`, mỗi nhóm một mail) và 70 thread bulk (`BULK-001` … `BULK-070`). Tổng 1.050 Message-ID khác nhau. Bản chép trùng không tạo nhóm riêng.
- Message-ID có dạng `<nhóm.khóa.số@domain-của-người-gửi>`.
- Số liệu trên được kiểm lại ngày 2026-10-18 bằng cách sinh vào thư mục tạm.

## 5. Nội dung

### 5.1 Người và địa chỉ

- Mọi địa chỉ dùng tên miền dành riêng `*.example.*`: phía mình là `vn-outsource.example.com`; khách là `client-a.example.jp`, `client-b.example.co.jp`, `client-c.example.jp`, `client-d.example.jp`; nhà cung cấp là `vendor-c.example.com`. Người dùng của profile là `pm@vn-outsource.example.com`.
- Mỗi người có charset, múi giờ (JST, ICT, UTC) và chữ ký `--` riêng. Chữ ký của khách Nhật có tên công ty `株式会社…` và `TEL`. Khi viết tên người Việt, người Nhật dùng katakana.

### 5.2 Phần mẫu nhỏ (51 mail, viết tay trong `THREADS`, `STANDALONE`, `DUP_OF`)

- 9 thread, tổng 41 mail: báo giá, báo sự cố, đổi spec, kick-off, hóa đơn, sprint nội bộ, QA nội bộ, gia hạn license (tiếng Anh, nằm ở Local Folders/Inbox) và biên bản họp (chỉ có HTML).
- 9 mail lẻ: thông báo nghỉ, auto-reply (`Auto-Submitted: auto-replied`), newsletter `multipart/alternative`, mail mình gửi, mail có đính kèm, và `S7` (một mail `Re:` không có thread gốc, không có In-Reply-To/References).
- 1 bản chép trùng Message-ID (mail 5 của `A-estimate`) sang Local Folders/Inbox, dùng để kiểm tra chống trùng và `message_ownership`.
- Trường hợp đặc biệt cho gom thread:
  - Mail 7 của `A-bug` mất In-Reply-To và References (giống Outlook làm đứt header) nhưng có `Thread-Index` giả.
  - Mail 3 và mail 4 của `A-bug` cùng trả lời mail 2, tạo thành cây có nhánh. Mail 3 là mail nội bộ tiếng Việt.
- Charset: ISO-2022-JP (7bit), Shift_JIS (base64), UTF-8 (base64 hoặc quoted-printable, chọn ngẫu nhiên). Nếu thân mail có ký tự mà charset tiếng Nhật không mã hóa được thì dùng UTF-8. Subject mã hóa theo charset của người gửi nếu được, không thì UTF-8.
- Quote: dòng giới thiệu kiểu Gmail tiếng Nhật (`2026年9月1日 10:00 名前 <addr>:`), tiếng Anh (`On … wrote:`) hoặc tiếng Việt (`Vào … đã viết:`), sau đó là khối dòng `>` chứa toàn bộ mail cha.
- Đính kèm: file `.xlsx`/`.pdf` giả dài vài chục byte, tên file theo RFC 2231 với UTF-8.

### 5.3 Phần bulk (mặc định 1.000 mail, 70 thread)

- Mỗi thread có 10–20 mail (tổng được chia ngẫu nhiên). Có 11 chủ đề (`TOPICS`) xoay vòng, 5 dự án `Project-C` … `Project-G`, 4 công ty khách, 12 người Nhật (`BULK_JP_NAMES`) và 6 người Việt.
- Thứ tự mail trong một thread: mail đầu là của khách, sau đó lặp lại chu kỳ `pm` → `client` → `vn` → `fwd`:
  - `pm`: mình trả lời mail gần nhất của khách.
  - `client`: khách trả lời mail ngay trước.
  - `vn`: trao đổi nội bộ viết tiếng Việt, subject `Re: <subject tiếng Việt>`.
  - `fwd`: mình chuyển mail gần nhất của khách cho team, subject `Fwd: …`, có khối `---------- Forwarded message ----------`.
- Mail của khách và mail `pm` viết tiếng Nhật, có thêm `--- Bản dịch tiếng Việt ---`, và trích 4 dòng đầu của mail cha. Vì thân mail có tiếng Việt nên luôn là UTF-8; subject vẫn theo charset của người gửi.
- Mọi mail có References tới chuỗi mail cha, nên cả thread nối được bằng lớp 1 (References/In-Reply-To).
- 20% mail đầu thread có đính kèm `.xlsx` giả.
- Ngày gửi trong năm 2026: bắt đầu từ 1/1 cộng 0–270 ngày, hai mail liên tiếp cách nhau 1–12 giờ.
- Toàn bộ bulk nằm trong Local Folders: mail mình gửi vào `Sent`, còn lại vào folder dự án.

## 6. Nơi sử dụng

| Nơi | Cách dùng |
|---|---|
| `backend/tests/test_threading.py` (golden), `backend/tests/test_e2e_sample.py` | Sinh mới vào thư mục tạm mỗi lần chạy, so nhóm thread (thuần và trong DB) với `expected_threads.json`: khớp 100% |
| `collector/internal/parse/profile_test.go` | Đọc `data/thunderbird-profile`, so tập Message-ID với `expected_threads.json` (cộng thêm bản trùng) |
| `collector/internal/syncer`, `internal/service`, `internal/settingsui` (test) | Chép `data/thunderbird-profile` sang thư mục tạm rồi đồng bộ với server giả. Số mail mong đợi lấy từ `expected_threads.json` (tổng Message-ID cộng 1 bản trùng), không viết cứng |
| Chạy thử bằng tay | Trang setting của collector trỏ tới `data/thunderbird-profile` (CLAUDE.md gốc, mục "Chạy thử toàn bộ") |

Test của collector tự bỏ qua (skip) khi không có `data/thunderbird-profile`.

## 7. Dữ liệu và bảo mật

- Toàn bộ nội dung đều tự bịa với tên miền `*.example.*`, nên Claude Code được phép đọc và có thể commit.
- **Không thêm tài khoản thật vào profile này trong Thunderbird.** Nếu thêm, mail thật sẽ được tải vào `data/` trong repo, nơi Claude Code đọc được và có thể bị commit.
- Mở profile bằng Thunderbird làm phát sinh thêm file của Thunderbird (`msgFilterRules.dat`, `Unsent Messages`…). Các file này vẫn là dữ liệu giả, nhưng sẽ làm sai số liệu của test collector. Sinh lại profile để xóa chúng.
- Profile này không thay được `samples/anonymized/` cho những trường hợp cần mẫu từ mailer thật.

## 8. Kiểm thử

- Script không có test riêng. Nó được kiểm gián tiếp qua golden test của backend (sinh mới mỗi lần chạy) và test của collector.
- Sửa script thì sinh lại profile và chạy lại cả test backend và test collector (`.claude/rules/test-data.md`).

## 9. Giới hạn đã biết

- **Đã xử lý ngày 2026-10-18:**
  - `data/thunderbird-profile` từng bị dở: thư mục `ImapMail` rỗng, thiếu `Local Folders/Inbox`, có file do Thunderbird tạo. Đã sinh lại bằng `/gen-sample-mail` (lúc chạy không có tiến trình Thunderbird hay collector).
  - Có 8 test collector fail, gồm một panic nil pointer trong `TestLastMailHeldBackWhileFileIsFresh` vì không có INBOX. Ngoài ra `syncer_test.go`, `service_test.go` và `sync_api_test.go` viết cứng 51 mail (số mail từ trước khi có phần bulk). Nay các test này lấy số mail từ `expected_threads.json` (hàm `totalMails`/`sampleMailCount`).
  - Kết quả sau khi sửa: `go test -C collector ./...` pass toàn bộ; `pytest tests/test_threading.py tests/test_e2e_sample.py` 30 passed.
- Khi Thunderbird mở profile này, profile lại bị thêm file hoặc bị dở. Sinh lại bằng `/gen-sample-mail` trước khi chạy test collector.
- ISO-2022-JP và Shift_JIS chỉ xuất hiện trong 9 part text của phần mẫu nhỏ; 1.043 part còn lại là UTF-8 (thân mail bulk luôn là UTF-8).
- `Thread-Index` giả (`AQHa…`) chỉ có 15 byte, ngắn hơn 22 byte nên lớp 2 bỏ qua. Mail 7 của `A-bug` được nối bằng lớp 3 (subject + người tham gia + thời gian). Chưa có mẫu Thread-Index đúng định dạng Outlook.
- Chỉ có một trường hợp trùng Message-ID và nội dung giống hệt. Chưa có trường hợp trùng Message-ID mà khác nội dung (trường hợp đếm `mismatched` ở ingest).
- Nội dung bulk ghép từ một bộ câu mẫu nhỏ nên lặp lại nhiều. Không dùng để đánh giá search (`eval/`).

## 10. Lịch sử

- 2026-10-16: bản đầu, 51 mail (9 thread, 9 mail lẻ, 1 bản trùng).
- 2026-10-17: sửa lỗi header ghi base64 trong khi body là byte thô (bỏ `Content-Transfer-Encoding` trước `set_payload`); sinh lại profile, Message-ID không đổi.
- 2026-10-17 (tối): thêm phần bulk 1.000 mail, tham số `--threads`/`--total`, skill `/gen-sample-mail`.
- 2026-10-18: viết spec này; sinh lại `data/thunderbird-profile`; test collector không còn viết cứng số mail.
- 2026-10-31: thêm `data/generate_project_test_profile.py`, sinh profile riêng `data/project-test-profile/` (30 mail, Local Folders `Inbox`, `Sent`), `data/expected_projects.json` (Message-ID → dự án mong đợi) và `data/expected_documents.json` (mail có tài liệu đính kèm: dự án, tên file, phiên bản) để thử spec 25 và luồng lấy tài liệu về local về sau. Có 3 thread khách gửi tài liệu (xlsx, pdf giả; cùng tên file có bản 1 và bản cập nhật bản 2) và 1 mail có tài liệu nhưng không thuộc dự án nào. Ba dự án "Test 1", "Test 2", "Test 3" dùng domain `test1|2|3.example.jp`; có ca nhiều dự án, ca khớp địa chỉ nhưng sai Subject, ca chỉ có nhãn Subject mà không có địa chỉ khách. Bộ lọc cần nhập nằm ở docstring của script. Không đụng `data/thunderbird-profile` nên không ảnh hưởng số mail của các test khác.
