# Thiết kế: màn hình setting của collector (chọn profile và folder đồng bộ)

Ngày: 2026-10-16 · Thành phần: `collector/` (Go)

## 1. Mục đích

Người dùng (PM, sau này cả BrSE và leader) cần tự quyết collector được đọc những folder mail nào. Mail cá nhân, Trash, Junk không được rời khỏi máy. Màn hình setting là nơi người dùng chọn profile Thunderbird và chọn các folder sẽ đồng bộ vào DB.

Đây là phần code đầu tiên của collector. Danh sách folder lưu ở bước này là đầu vào của bước tiếp theo, tức đọc mbox và gửi lên server. Bước tiếp theo nằm ngoài tài liệu này.

## 2. Mục tiêu

1. Chọn được profile Thunderbird bằng hai cách: tự dò profile trên máy, hoặc chỉ tới một thư mục bất kỳ (ví dụ profile thử `data/thunderbird-profile`).
2. Chọn profile xong thì hiện ngay toàn bộ folder, chia nhóm theo tài khoản: từng server IMAP (`ImapMail\<server>\`) và Local Folders (`Mail\Local Folders\`). Folder con hiện dạng cây.
3. Mỗi folder hiện dung lượng file mbox và ngày sửa cuối. Mỗi nhóm hiện tổng số folder và tổng dung lượng.
4. Người dùng tick hoặc bỏ tick từng folder rồi bấm Lưu. Lựa chọn được lưu vào file cấu hình cục bộ.
5. Mở lại màn hình thì thấy đúng lựa chọn đã lưu. Folder mới xuất hiện kể từ lần lưu trước được đánh dấu "mới".

## 3. Không làm (non-goals)

- Không đọc nội dung mbox, không parse mail, không gửi gì lên server. Màn hình này chỉ cần `stat` và liệt kê thư mục.
- Không đếm số mail trong folder. File vài GB thì đếm mất vài phút. Chỉ hiện dung lượng.
- Không làm tray icon. Tray làm sau, khi đó menu tray chỉ cần mở lại trang này.
- Không hỗ trợ Thunderbird lưu dạng maildir. Nếu gặp thì báo "chưa hỗ trợ" cho tài khoản đó.
- Không đồng bộ cấu hình lên server. Lựa chọn folder chỉ nằm trên máy người dùng.

## 4. Điều tuyệt đối tránh (anti-goals)

- **Ghi bất cứ thứ gì vào profile Thunderbird** (kể cả tạo file tạm, khóa file). Profile chỉ được đọc metadata.
- **Mở file mbox để đọc nội dung.** Tính năng này không có lý do gì để đọc byte của mbox.
- **Trang cục bộ truy cập được từ máy khác hoặc từ trang web khác.** API của trang này liệt kê được thư mục trên máy, nên chỉ chính người dùng được gọi.
- Folder bỏ tick vẫn bị đọc hoặc gửi đi ở bước sau. Cấu hình phải đủ rõ để bước sau không phải đoán.

## 5. Ràng buộc

- Go, một file `.exe`, không dùng CGO (quy ước của `collector/CLAUDE.md`).
- Chỉ đọc profile: `os.Stat`, `os.ReadDir`, đọc `profiles.ini`. Không mở mbox.
- Log và stdout chỉ ghi đường dẫn folder, số lượng, thời gian, lỗi. Không ghi nội dung mail (tính năng này vốn không chạm tới nội dung).
- Giao diện bằng tiếng Việt. Tên biến, hàm, khóa JSON bằng tiếng Anh.
- Chạy được trên Windows 10/11. Code đường dẫn dùng `path/filepath`. Test chạy được trên mọi hệ điều hành.

## 6. Người dùng đã quyết

- Màn hình theo **bố cục A (một trang)**: ô đường dẫn profile ở trên, kèm nút "Chọn thư mục…" và dòng gợi ý các profile tự dò được. Bên dưới là cây folder chia hai nhóm Server và Local Folders. Cuối trang có dòng tổng "Đã chọn X/Y folder · Z GB", ô "Tự thêm folder mới xuất hiện sau này" và nút Lưu. Đổi profile thì cây folder tải lại ngay.
- Trash, Junk, Drafts **mặc định bỏ tick**.
- Folder bỏ tick thì collector không đọc và không gửi lên server.
- Folder mới xuất hiện sau này **mặc định không tự đồng bộ**. Có ô để bật.
- Màn hình thuộc collector chạy trên máy người dùng, mở dưới dạng trang web cục bộ trong trình duyệt. Lý do: web app trên server không nhìn thấy ổ đĩa của người dùng, còn thư viện giao diện desktop của Go cần CGO.
- Phạm vi lần này: chỉ lưu lựa chọn. Đọc mail và đẩy vào DB là bước sau.

## 7. Thiết kế (các quyết định của người viết, builder có thể chỉnh nếu có lý do)

### 7.1 Cách chạy

- Lệnh: `collector.exe settings`. Collector mở HTTP server tại `127.0.0.1` với cổng ngẫu nhiên còn trống, rồi tự mở trình duyệt mặc định (`rundll32 url.dll,FileProtocolHandler <url>` trên Windows). URL cũng được in ra stdout để mở tay nếu cần.
- Nút "Lưu" ghi cấu hình và giữ trang mở. Liên kết "Đóng" ở góc trang gọi `/api/shutdown`. Server cũng dừng khi nhấn Ctrl+C hoặc sau 30 phút không có request.
- Mỗi collector chỉ theo dõi **một profile**. Đổi profile nghĩa là thay profile cũ.
- Chạy `collector.exe` không kèm tham số khi chưa có file cấu hình thì cũng mở màn hình setting. Các lệnh quét sẽ thêm ở bước sau.

### 7.2 Bảo vệ trang cục bộ

- Chỉ bind `127.0.0.1`, không bind `0.0.0.0`.
- Mỗi lần chạy sinh một token ngẫu nhiên 32 byte và đưa vào URL (`?key=…`). Lần tải đầu, server đặt cookie `HttpOnly; SameSite=Strict`. Mọi request API phải có cookie hợp lệ, nếu không thì trả 403.
- Kiểm tra header `Host` phải là `127.0.0.1:<port>` hoặc `localhost:<port>` (chống DNS rebinding).
- API ghi (`POST`) kiểm tra thêm header `Origin` phải trùng origin của server.

### 7.3 Dò profile

Thứ tự:
1. Đọc `%APPDATA%\Thunderbird\profiles.ini`, lấy các mục `[ProfileN]` (`Name`, `Path`, `IsRelative`, `Default`). Profile `Default=1` (hoặc profile trong `installs.ini` nếu có) được chọn sẵn.
2. Người dùng có thể dán đường dẫn vào ô, hoặc bấm "Chọn thư mục…" để mở bộ duyệt thư mục ngay trong trang. Bộ duyệt gọi API liệt kê ổ đĩa và thư mục con, chỉ trả về tên thư mục, không trả về file. Lý do làm bộ duyệt trong trang: trình duyệt không cho trang web biết đường dẫn đầy đủ của thư mục người dùng chọn, còn hộp thoại chọn thư mục gốc của Windows muốn gọi mà không có CGO thì phải đi qua COM, phức tạp.
3. Kiểm tra thư mục được chọn:
   - Có `ImapMail\` hoặc `Mail\` → đây là profile.
   - Không có, nhưng có `profiles.ini` → đây là thư mục gốc Thunderbird. Liệt kê các profile bên trong để người dùng chọn.
   - Không thuộc hai trường hợp trên → báo "Không phải profile Thunderbird".

`data/thunderbird-profile` có cả `ImapMail\`, `Mail\` và `profiles.ini` (với `Path=.`), nên được nhận là profile.

### 7.4 Dò tài khoản và folder

- **Nhóm Server**: mỗi thư mục con của `ImapMail\` là một tài khoản IMAP, tên nhóm là tên thư mục (ví dụ `imap.vn-outsource.example.com`). Thunderbird thêm hậu tố `-1`, `-2` khi trùng host, giữ nguyên tên đó.
- **Nhóm Local**: mỗi thư mục con của `Mail\` là một nhóm (thường chỉ có `Local Folders`; tài khoản POP nếu có cũng nằm ở đây và hiện thành nhóm riêng).
- **Nhận diện file mbox** trong một thư mục tài khoản hoặc `*.sbd`:
  - Là file thường (không phải thư mục).
  - Bỏ qua: `*.msf`, và các file có đuôi thuộc danh sách đã biết không phải mbox (`.dat`, `.html`, `.json`, `.sqlite`, `.js`, `.ini`, `.log`, `.mab`, `.bak`, `.tmp`).
  - Được nhận nếu không có đuôi, **hoặc** có file `<tên>.msf` cạnh nó. Quy tắc thứ hai để không bỏ sót folder có dấu chấm trong tên (ví dụ `v1.2`).
- **Folder con**: `<tên>.sbd\` chứa folder con của `<tên>`. Có `.sbd` mà không có file mbox `<tên>` thì vẫn hiện `<tên>` như một nút cha không có mail riêng, không có checkbox của chính nó. `.sbd` rỗng thì bỏ qua.
- **Maildir**: thư mục tài khoản chứa thư mục con có `cur\` và `new\` → đánh dấu tài khoản "chưa hỗ trợ maildir", không liệt kê folder.
- Mỗi folder lấy: đường dẫn tương đối so với profile (dùng `/`), tên hiển thị (bỏ `.sbd`), dung lượng, `mtime`.
- Tên hiển thị lấy từ tên file. Folder tiếng Nhật/Việt hiện đúng vì Windows lưu tên file Unicode. Thunderbird đôi khi đổi tên quá dài hoặc có ký tự cấm thành chuỗi hash: khi đó hiện nguyên chuỗi hash, không đọc `.msf` để lấy tên thật (`.msf` chứa tóm tắt mail). Đây là giới hạn đã biết.
- Sắp xếp: `INBOX`/`Inbox` trước, các folder còn lại theo tên (không phân biệt hoa thường).
- Không theo symlink/junction ra ngoài profile.

### 7.5 Mặc định bỏ tick

Folder (ở bất kỳ cấp nào) có tên khớp danh sách sau, không phân biệt hoa thường, thì mặc định bỏ tick và hiện nhãn "mặc định bỏ qua":
`Trash`, `Deleted Items`, `Deleted Messages`, `Junk`, `Junk E-mail`, `Spam`, `Drafts`, `Templates`, `Unsent Messages`, `Outbox`, cùng tên tiếng Nhật và tiếng Việt mà server IMAP hay dùng: `ゴミ箱`, `削除済みアイテム`, `迷惑メール`, `下書き`, `送信トレイ`, `Thùng rác`, `Thư rác`, `Thư nháp`.

Danh sách này nằm trong code (một hằng số), chưa cho người dùng sửa. Folder con của folder bị bỏ tick mặc định thì cũng bỏ tick.

### 7.6 Hành vi checkbox

- Bấm checkbox của một folder thì đổi trạng thái folder đó và toàn bộ folder con. Sau đó vẫn chỉnh được từng folder con.
- Checkbox của nhóm (Server, Local) có ba trạng thái: tất cả, một phần, không. Bấm vào thì chọn hết hoặc bỏ hết cả nhóm.
- Dòng tổng ở cuối trang cập nhật ngay khi tick.
- Đổi sang profile khác thì hỏi xác nhận nếu có thay đổi chưa lưu. Lựa chọn của profile mới lấy từ cấu hình đã lưu (nếu từng lưu cho đúng profile đó) hoặc từ mặc định.

### 7.7 File cấu hình

- Vị trí: `%APPDATA%\PMMailAssistant\collector\config.json`. Ghi bằng cách viết ra file tạm cùng thư mục rồi đổi tên, để không bao giờ để lại file hỏng.
- Định dạng JSON (thư viện chuẩn):

```json
{
  "config_version": 1,
  "profile_path": "C:\\Users\\...\\Profiles\\x8k2.default-release",
  "auto_include_new_folders": false,
  "folders": [
    { "path": "ImapMail/imap.vn-outsource.example.com/INBOX", "account": "imap.vn-outsource.example.com", "kind": "imap_cache", "selected": true },
    { "path": "ImapMail/imap.vn-outsource.example.com/Trash", "account": "imap.vn-outsource.example.com", "kind": "imap_cache", "selected": false },
    { "path": "Mail/Local Folders/Inbox", "account": "Local Folders", "kind": "local", "selected": true }
  ],
  "saved_at": "2026-10-16T10:00:00+07:00"
}
```

- Lưu **cả folder được chọn lẫn folder bỏ tick**, nhưng chỉ những folder trang đã hiển thị cho người dùng. Folder xuất hiện sau khi trang mở thì chưa có quyết định và vẫn là "mới" ở lần sau. Nhờ đó lần sau phân biệt được folder đã biết (đã có quyết định) với folder mới.
- `kind` dùng đúng giá trị của `mail_sources.kind` trong kế hoạch (`local` | `imap_cache`).
- Quy tắc cho bước quét sau (`Config.ShouldScan`), so khớp đường dẫn không phân biệt hoa thường:
  1. Folder đã có quyết định → theo `selected`.
  2. Folder mới thuộc danh sách mặc định bỏ tick → không đọc.
  3. Folder mới mà folder cha gần nhất có quyết định đã bị bỏ tick → không đọc (bỏ tick "Cá nhân" thì "Cá nhân/Ngân hàng" tạo sau cũng bị loại).
  4. Còn lại → chỉ đọc khi `auto_include_new_folders` là `true`.

  (Bổ sung 2026-10-16 sau review: bản đầu chỉ có quy tắc 1 và 4.)
- Token xác thực collector sau này sẽ nằm trong file này hoặc file cạnh nó. Bước này chưa có token.

### 7.8 API cục bộ

| Method | Đường dẫn | Việc |
|---|---|---|
| GET | `/` | Trang HTML (nhúng vào binary bằng `go:embed`) |
| GET | `/api/profiles` | Các profile tự dò được và profile đang lưu trong cấu hình |
| GET | `/api/browse?path=` | Ổ đĩa (khi `path` rỗng) hoặc thư mục con của `path`. Chỉ trả thư mục |
| GET | `/api/folders?profile=` | Kiểm tra profile, trả về cây tài khoản/folder kèm trạng thái chọn (từ cấu hình hoặc mặc định) và cờ `is_new` |
| POST | `/api/config` | Lưu cấu hình. Body: `{"profile_path", "auto_include_new_folders", "folders": {"<path>": true\|false}}`, gồm mọi folder trang đang hiển thị |
| POST | `/api/shutdown` | Dừng server (nút "Lưu và đóng") |

Lỗi trả JSON `{ "error": "<thông điệp tiếng Việt>" }`.

### 7.9 Giao diện

- HTML + CSS + JavaScript thuần trong một thư mục `web/` nhúng vào binary. Không có bước build front-end, không dùng CDN (máy người dùng có thể không có internet và trang không nên tải gì từ bên ngoài).
- Theo bố cục A trong bản phác đã duyệt. Số liệu định dạng kiểu Việt (`24,7 GB`), ngày dạng `dd/MM/yyyy` hoặc "hôm nay", "hôm qua".
- Folder mới hiện nhãn "mới". Tài khoản maildir hiện thông báo "chưa hỗ trợ".

### 7.10 Cấu trúc code

```
collector/
  go.mod                       # module pmmail/collector
  cmd/collector/main.go        # lệnh: settings (mặc định khi chưa có cấu hình)
  internal/profile/            # đọc profiles.ini, nhận diện profile, dò tài khoản và folder
  internal/config/             # đọc/ghi config.json, quy tắc folder mới
  internal/settingsui/         # HTTP server, bảo vệ token/Host/Origin, API, web/ nhúng
```

`internal/scan/` (quét tăng dần) giữ nguyên cho bước sau, dùng lại `internal/profile` và `internal/config`. Cập nhật mục "Cấu trúc" và "Lệnh" trong `collector/CLAUDE.md` theo thực tế.

## 8. Kiểm thử

- `internal/profile`: test bằng cây thư mục dựng trong `t.TempDir()` với file rỗng (không cần nội dung mail). Các trường hợp: folder có dấu chấm trong tên và có `.msf`, mbox thiếu `.msf`, `.sbd` không có mbox cha, `.sbd` rỗng, file không phải mbox (`.dat`, `.json`, `.html`), nhiều tài khoản IMAP, tài khoản maildir, `profiles.ini` với `IsRelative=0/1`, thư mục không phải profile.
- `internal/config`: đọc/ghi qua lại, ghi atomic, gộp cấu hình cũ với cây folder mới (folder mới, folder đã mất), áp dụng mặc định bỏ tick.
- `internal/settingsui`: dùng `httptest`. Thiếu hoặc sai token thì trả 403, `Host` lạ thì trả 403, `Origin` lạ trên `POST` thì trả 403, `/api/folders` trả đúng cây cho profile dựng sẵn.
- Không test nào được mở file mbox. Có thể thêm một test kiểm tra điều này bằng cách cho file mbox quyền không đọc được (trên hệ điều hành hỗ trợ).
- Chạy thử thủ công với `data/thunderbird-profile` (dữ liệu tự sinh, tên miền `*.example.*`). Kết quả mong đợi: nhóm Server `imap.vn-outsource.example.com` có `INBOX` (con `ProjectA`, `ProjectB`), `Sent`, `Drafts`, `Trash`; nhóm `Local Folders` có `Inbox`, `Trash` (từ khi generator có phần bulk, 2026-10-17: thêm `Sent`, `Project-C` … `Project-G`; spec 12). Drafts và Trash bỏ tick sẵn. `Inbox.sbd` rỗng không sinh nút.
- Lệnh: `go test ./...`, `go vet ./...`. `golangci-lint` chưa cài trên máy. Builder quyết định cài hay tạm bỏ qua và ghi rõ trong plan.

## 9. Để builder tự quyết

- Chi tiết HTML/CSS, miễn là giữ bố cục A và chạy được ở cả nền sáng và tối.
- Thư viện đọc `.ini` (tự viết vài dòng hay dùng thư viện nhỏ thuần Go).
- Cách mở trình duyệt mặc định và cách chọn cổng trống.
- Tên hàm, kiểu dữ liệu nội bộ.
