# Collector (Go)

Agent chạy trên máy từng người dùng: quét mbox của Thunderbird, trích xuất mail và gửi lên Ingest API. Đóng gói thành một file `.exe`; người dùng thao tác trên trang setting cục bộ (`collector settings`), hoặc chạy định kỳ bằng Task Scheduler (`collector sync`).

Spec theo chức năng: [docs/superpowers/specs/README.md](../docs/superpowers/specs/README.md) (trang setting, trích xuất mail, đồng bộ tăng dần, giao diện đồng bộ, xóa mail khỏi server).

## Phạm vi

- **Làm**: duyệt folder, đọc mbox dạng stream, tách mail, parse MIME, giải mã charset, chuyển HTML sang text, lấy metadata (Message-ID, From/To/Cc, Date, Subject, In-Reply-To, References, Thread-Index, metadata đính kèm), lưu state quét tăng dần, gửi lên API theo lô nén gzip, retry và tiếp tục khi bị ngắt.
- **Không làm**: cắt quote/chữ ký, chống trùng lặp, gom thread, gán dự án, gọi AI. Các việc này thuộc backend.
- Cấu hình loại trừ folder (Trash, Junk, folder cá nhân) áp dụng ngay tại máy: folder bị loại trừ không được đọc, không gửi lên server.

## Thư viện

- `github.com/jhillyerd/enmime/v2`: parse MIME, chịu được mail sai chuẩn, giải mã charset (qua `golang.org/x/text`), chuyển HTML sang text.
- `golang.org/x/net/html/charset`: giải mã header khi enmime không đọc được mail (chỉ lấy header).
- `golang.org/x/sys/windows`: DPAPI (`CryptProtectData`) để mã hóa token.
- State cục bộ là file JSON (`internal/state`), không dùng SQLite: chỉ có vài số cho mỗi folder, chống trùng do server làm.
- `fyne.io/systray`: tray app, chỉ thêm khi cần.

## Cấu trúc

```
cmd/collector/          # main: lệnh settings (mặc định), sync
internal/profile/       # đọc profiles.ini, nhận diện profile, dò tài khoản và cây folder (chỉ stat/readdir)
internal/config/        # config.json (%APPDATA%\PMMailAssistant\collector\): folder chọn, server_url, ShouldScan
internal/mbox/          # mở mbox không khóa (CreateFile share READ|WRITE|DELETE), tách mail dạng stream
internal/parse/         # MIME, charset, HTML → text, metadata, cắt trường theo giới hạn Ingest API
internal/state/         # state.json: vị trí đã gửi của từng folder (%LOCALAPPDATA%)
internal/tokenstore/    # token collector mã hóa DPAPI (%LOCALAPPDATA%\...\token.dat)
internal/upload/        # client gọi backend: ingest (gzip, retry), me, sources, delete-mail
internal/syncer/        # đồng bộ tăng dần một lượt, khóa sync.lock, FormatSummary
internal/service/       # ghép các phần trên cho lệnh sync và trang setting
internal/fsutil/        # ghi file atomic
internal/settingsui/    # trang setting cục bộ: HTTP 127.0.0.1 + token/Host/Origin, API JSON, web/ nhúng
```

## Lệnh

Chạy từ thư mục gốc `Email Assistant`:

- Test: `go test -C collector ./...`
- Kiểm tra: `go vet -C collector ./...` · `gofmt -l collector`
- Build: `$env:CGO_ENABLED="0"; go build -C collector -o collector.exe ./cmd/collector`
- Trang setting: `collector\collector.exe settings [-config <file>] [-no-browser]`
- Đồng bộ một lượt: `collector\collector.exe sync [-config <file>]`
- `golangci-lint` chưa cài trên máy dev; tạm dùng `go vet` + `gofmt`.
- Chạy thử bằng Claude Code: đặt `$env:APPDATA` và `$env:LOCALAPPDATA` sang thư mục tạm (để không dò ra profile thật và không đụng state/token thật), dùng `-config <file tạm>` và `-no-browser`, chỉ mở profile tự sinh `data/thunderbird-profile`.
- Test dùng profile tự sinh `data/thunderbird-profile` (sinh lại: `python data/generate_sample_profile.py` hoặc `/gen-sample-mail`; mặc định 1.051 mail gồm 51 mail mẫu nhỏ và 1.000 mail bulk; đáp án ở `data/expected_threads.json`; spec 12). Số mail mong đợi trong test lấy từ `expected_threads.json` (`totalMails`/`sampleMailCount` trong test), không viết cứng. Thunderbird mở profile này làm nó bị dở và test fail: sinh lại trước khi chạy test.

## Trang setting

- Folder được nhận là mbox khi: là file thường, đuôi không thuộc danh sách loại trừ (`.msf`, `.dat`, `.html`, `.json`…), không phải `nstmp`/`nstmp-N`, và (không có đuôi hoặc có `<tên>.msf` cạnh nó).
- Danh sách mặc định bỏ tick nằm trong `internal/profile/defaults.go` (gồm tên tiếng Nhật và tiếng Việt).
- `config.json` lưu quyết định (chọn/bỏ) cho các folder người dùng đã thấy, và `server_url` (lưu folder không xóa `server_url`). Bước quét chỉ đọc folder mà `Config.ShouldScan(path)` trả `true` (folder mới kế thừa việc bỏ tick của folder cha). Lấy danh sách folder từ `profile.ScanAccounts`, không tự duyệt lại thư mục.
- Không gọi `os.ReadDir`/`os.Open` lên đường dẫn chưa chắc là thư mục: trên Windows chúng mở handle không có `FILE_SHARE_DELETE`, làm Thunderbird không đổi tên/xóa được file. `os.Stat` thì không mở handle.
- Kết nối server: URL `http://` chỉ nhận cho `127.0.0.1`/`localhost`; server khác phải `https://`. Token không bao giờ trả về trình duyệt.
- Đồng bộ chạy nền trong tiến trình settings (`/api/sync/start|stop|status`); server cục bộ không tự tắt khi đang đồng bộ; bấm Đóng thì đồng bộ dừng sau lô hiện tại.
- Xóa mail khỏi server chỉ cho folder đang bỏ tick (đã lưu), xong thì reset state của folder đó.
- Giao diện: HTML/CSS/JS thuần trong `internal/settingsui/web/`, không tải gì từ bên ngoài (CSP `default-src 'self'`); dữ liệu từ server chỉ đưa vào DOM bằng `textContent`.

## Thunderbird mbox

- Profile nằm ở `%APPDATA%\Thunderbird\Profiles\<profile>\`. Nguồn dữ liệu: `Mail\Local Folders\` (chỉ có trên máy) và `ImapMail\<server>\` (cache offline của IMAP).
- Mỗi folder là 1 file mbox không có đuôi. Bỏ qua `*.msf`. Folder con nằm trong `<tên folder>.sbd/`.
- Tách mail theo dòng bắt đầu bằng `From ` ở đầu file hoặc ngay sau dòng trống (`>From ` là nội dung). Đọc dạng stream (`internal/mbox`), không nạp cả file vào RAM. Mail lớn hơn 64 MiB chỉ giữ 1 MiB đầu (header).
- Bỏ mail có cờ expunged (bit `0x0008`) trong header `X-Mozilla-Status`.
- Quét tăng dần (`internal/syncer`):
  - File không đổi (kích thước và mtime): bỏ qua.
  - File lớn hơn: đọc tiếp từ offset cũ, **chỉ khi** 4 KiB trước offset vẫn khớp `tail` trong state và tại offset là dòng `From `. Không khớp (compact rồi file lại lớn lên) thì đọc lại từ đầu: không bao giờ tin offset mà không kiểm tra.
  - File nhỏ đi: đã compact, đọc lại từ đầu; server chống trùng theo `(user_id, message_id_hash)`.
  - Kích thước lấy bằng `Stat` của handle đang mở, đọc qua `io.LimitReader` tới đúng kích thước đó.
  - Mail cuối file chỉ đọc khi file không bị sửa trong 30 giây; nếu để lại thì state không ghi kích thước (lần sau đọc tiếp).
  - Server từ chối lô (400/413/422) → chia đôi để chỉ bỏ mail lỗi; từ chối mọi mail → lỗi folder, không bỏ qua gì.
- Khóa `sync.lock` do hệ điều hành giữ (share mode 0 + `FILE_FLAG_DELETE_ON_CLOSE`), không dùng tuổi file để đoán khóa cũ.
- Mở file bằng `mbox.Open` (không dùng `os.Open`: thiếu `FILE_SHARE_DELETE`, làm hỏng compact của Thunderbird). Không bao giờ ghi hay khóa file trong profile.

## Giải mã tiếng Nhật

- Mail khai báo `ISO-2022-JP` hoặc `Shift_JIS` thực tế thường chứa ký tự mở rộng của CP932 (①, ㈱, Ⅲ…). Bộ giải mã `x/text` (qua enmime) xử lý được; có test với byte viết tay theo bảng mã.
- Header MIME encoded-word (`=?ISO-2022-JP?B?...?=`) cũng phải giải mã theo cùng quy tắc. Header gửi thẳng byte JIS/Shift_JIS (không encoded-word) được giải mã bằng `decodeRaw`.
- Nhãn charset lạ (`MS932`, `ISO-2022-JP-MS`, `eucJP-win`…) đổi về bộ mã gần nhất qua bảng `charsetAliases` trong `internal/parse`; gặp nhãn mới thì thêm vào bảng kèm test.

## Thân mail và file đính kèm

- Không dùng `env.Text`/`env.HTML` của enmime cho mail nhiều part: nó gộp cả part text có tên file (file đính kèm) vào thân mail.
- Không tin `Part.FileName`/`Part.Disposition` của enmime để quyết định "là file đính kèm": chúng rỗng khi tên file RFC 2231 dùng charset Nhật hoặc Content-Disposition sai cú pháp. Dùng `declaredAttachment` (đọc header thô). Chỉ đi xuống `multipart/*` (`walkLeaves`).
- Thân mail không được chứa nội dung file đính kèm dưới mọi dạng (part có tên, `message/rfc822`, uuencode). Gặp dạng mới thì thêm test vào `parse_test.go` trước.
- Đổi quy tắc trích xuất làm thay đổi dữ liệu gửi lên thì tăng `parse.ParserVersion`. Nếu bản cũ gửi dữ liệu không được phép, tăng cả `MIN_PARSER_VERSION` ở `backend/app/ingest.py`.
- Output gửi lên server luôn là UTF-8. Backend không tự giải mã charset từ byte gốc.

## Quy ước

- `gofmt`; ưu tiên thư viện chuẩn; không dùng CGO.
- Không in nội dung mail (kể cả Subject, From) ra log hay stdout. Log chỉ ghi đường dẫn folder, số lượng, offset, thời gian, lỗi.
- Schema dữ liệu gửi lên do backend định nghĩa (`backend/app/schemas.py`). Đổi schema thì sửa ở backend trước, payload luôn có `schema_version`. Trường vượt giới hạn của backend làm cả lô bị 422: `internal/parse` cắt trước khi gửi.
- Token xác thực collector lưu mã hóa (DPAPI) ở `%LOCALAPPDATA%\PMMailAssistant\collector\token.dat` cùng origin của server, không ghi vào log, không nằm trong `config.json`. Chỉ gửi token tới đúng origin đó (`Service.tokenFor`); không theo redirect.
- Không log giá trị panic (`recover()`) và thông điệp lỗi có thể chứa dữ liệu mail.
