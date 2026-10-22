# Spec: Đồng bộ tăng dần (collector)

Ngày: 2026-10-17 · Thành phần: `collector/internal/syncer`, `collector/internal/state`, `collector/internal/upload`, lệnh `collector sync` · Trạng thái: xong (sửa sau review 2026-10-17: mục 4, 5, 6, 7)

## 1. Mục đích

Đọc các folder người dùng đã chọn trên trang setting, chỉ đọc phần mới kể từ lần trước, gửi lên Ingest API theo lô, tiếp tục được khi bị ngắt, và báo kết quả khi xong.

## 2. Đầu vào

- `config.json` (trang setting): `profile_path`, danh sách folder, `Config.ShouldScan(path)` quyết định folder nào được đọc (folder mới kế thừa việc bỏ tick của folder cha).
- Danh sách folder thật lấy từ `profile.ScanAccounts` (đã lọc `nstmp`, symlink, file không phải mbox).
- Kết nối server: `server_url` trong `config.json`; token trong file riêng mã hóa (xem spec giao diện đồng bộ).

## 3. State cục bộ

- File `%LOCALAPPDATA%\PMMailAssistant\collector\state.json`, ghi atomic (file tạm + rename) sau mỗi lô gửi thành công.
- Nội dung: `state_version`, `server_url`, `profile_path`, và theo từng folder: `offset` (vị trí đã gửi xong), `tail` (SHA-256 của tối đa 4 KiB ngay trước `offset`), `size`, `mtime`, `last_sync_at`.
- `server_url` hoặc `profile_path` khác với config hiện tại → coi như state rỗng (mail chưa có trên server mới).
- Quyết định của người viết: dùng file JSON thay cho SQLite như ghi chú ban đầu trong `collector/CLAUDE.md`. State chỉ gồm vài số cho mỗi folder (vài trăm folder); việc chống trùng do server làm, nên không cần lưu danh sách mail đã gửi. Tránh thêm dependency lớn.

## 4. Quyết định đọc cho từng folder

| Tình huống | Hành động |
|---|---|
| Chưa có state | Đọc từ 0 |
| `size` và `mtime` không đổi | Bỏ qua |
| `size` < `offset` đã lưu | Thunderbird đã compact: đọc lại từ 0 (server tự chống trùng, cập nhật `mbox_offset`) |
| `tail` không khớp, hoặc tại `offset` không phải đầu một mail (dòng `From `, có thể có dòng trống trước) | Nội dung đã dịch chuyển (compact rồi có mail mới làm file lại lớn hơn `offset`): đọc lại từ 0 |
| State của bản cũ không có `tail` | Đọc lại từ 0 một lần |
| Còn lại | Đọc từ `offset` |

- Hàng "tail không khớp" được thêm sau review: trước đó collector đọc tiếp từ `offset` cũ đã lệch và **bỏ sót mail mới vĩnh viễn** (đã tái hiện bằng test `TestCompactionThenGrowthRereads`).
- Kích thước và `mtime` lấy bằng `fstat` của chính handle đang mở, và chỉ đọc tới kích thước đó (`io.LimitReader`). Phần Thunderbird ghi thêm trong lúc đang đọc để lần sau, không bao giờ gửi mail đang ghi dở.
- Mail cuối file (không có dòng phân cách sau nó) chỉ được đọc khi file không bị sửa trong 30 giây gần nhất; nếu không, dừng trước mail đó và để lần sau.
- Giới hạn đã biết: Thunderbird sửa cờ đã đọc/đã trả lời ngay trong mbox mà không đổi kích thước; những thay đổi đó chỉ cập nhật khi folder được đọc lại từ đầu.
- Giới hạn đã biết: mail bị xóa khỏi folder (sau compact) chưa được đánh dấu `is_deleted` trên server.

## 5. Gửi lên server (`internal/upload`)

- Lô tối đa 200 mail hoặc 8 MiB JSON (kích thước tính bằng JSON thật của từng mail), nén gzip, `POST /api/v1/ingest` kèm `Authorization: Bearer <token>`.
- Thành công: lưu `offset` = `End` của mail cuối trong lô và `tail` tại đó. Folder đọc xong: lưu thêm `size`, `mtime`.
- Lỗi mạng, 5xx, 429: thử lại 3 lần (chờ 1 s, 4 s, 10 s). Hết lượt: dừng đồng bộ, giữ state tới lô thành công cuối.
- 401/403: dừng ngay, báo "Token không hợp lệ hoặc đã bị thu hồi".
- 400/413/422 (server từ chối dữ liệu): chia đôi lô và gửi lại từng nửa để tìm mail lỗi. Mail vẫn bị từ chối khi gửi riêng thì bỏ qua (đếm `rejected`, log chỉ có folder và offset) và đồng bộ đi tiếp. Nếu **mọi** mail của lô đều bị từ chối (vd. server khác phiên bản schema), đó là lỗi folder: không bỏ qua mail nào, `offset` giữ nguyên, chuyển sang folder kế tiếp. Trước khi sửa, một mail lỗi làm folder kẹt mãi.
- 3xx: không theo redirect, coi như lỗi HTTP (Go giữ header `Authorization` khi chuyển sang cổng khác cùng host, và 307/308 gửi lại cả body).
- Mail không mã hóa JSON được, hoặc parser panic (đã thử lại với chỉ header): bỏ qua, đếm `unreadable`. Không log giá trị panic.
- Kiểm tra hủy (`ctx`) trước mỗi mail, không chỉ khi gửi lô.

## 6. Tiến độ và kết quả

- Sự kiện tiến độ: folder đang đọc (thứ tự/tổng), số mail đã gửi, số mới, số đã có, byte đã đọc / tổng byte cần đọc.
- Kết quả khi xong: số folder đã đọc, bỏ qua (không đổi), lỗi; số mail đã đọc, mới, đã có trên server, bỏ qua vì đã xóa (`expunged`), chỉ đọc được header (`degraded`), server từ chối (`rejected`), không đọc được (`unreadable`); thời gian. Đây là nội dung thông báo "Đồng bộ hoàn thành"; câu thông báo nêu thêm "Có N mail bị bỏ qua" và "Có N mail chỉ đọc được header" khi có.
- Chỉ một lần đồng bộ chạy tại một thời điểm: khóa `sync.lock` do hệ điều hành giữ suốt lượt chạy (Windows: mở file với share mode 0, `FILE_FLAG_DELETE_ON_CLOSE` và `FILE_FLAG_OPEN_REPARSE_POINT` để không đi theo symlink; hệ khác: `flock`). Tiến trình chết thì khóa tự nhả; lượt chạy dài bao lâu cũng không bị coi là khóa cũ. (Bản trước dùng file tạo độc quyền + "cũ hơn 6 giờ thì bỏ": tiến trình bị kill làm chặn đồng bộ 6 giờ, lượt chạy trên 6 giờ bị lấy mất khóa.)
- Hủy giữa chừng (Ctrl+C, đóng cửa sổ/đăng xuất, bấm Dừng): dừng trước mail kế tiếp, state giữ tới lô thành công cuối.
- Panic trong lượt đồng bộ chạy từ trang setting: lượt đó chuyển sang "lỗi" với thông báo chung, collector không bị sập.

## 7. Lệnh `collector sync`

- `collector sync [-config <file>]`: chạy đồng bộ, in tiến độ từng folder và dòng tổng kết `Đồng bộ hoàn thành: ...`. Dùng cho Task Scheduler sau này. Mã thoát: 0 thành công, 1 lỗi, 2 sai tham số, 3 xong lượt nhưng có folder lỗi.
- Output chỉ gồm đường dẫn folder và số lượng.

## 8. Kiểm thử

- State: đọc/ghi atomic, đổi `server_url` thì reset.
- Quyết định đọc: bảng ở mục 4 (chưa có state, không đổi, lớn lên, nhỏ đi), mail cuối file mới sửa bị để lại.
- Upload: gzip + header đúng; retry rồi thành công; 401 dừng ngay; state chỉ tiến khi lô thành công.
- Engine với server giả (`httptest`): đọc `data/thunderbird-profile`, số mail gửi đúng, chạy lần hai không gửi lại gì, thêm mail vào mbox (bản sao trong thư mục tạm) thì chỉ gửi mail mới, cắt ngắn file (giả compact) thì đọc lại từ đầu.
- Thêm sau review: compact (bỏ mail đầu) rồi thêm mail làm file lớn hơn `offset` → mọi mail mới đều được gửi; file lớn thêm trong lúc đọc → không gửi mail ghi dở, `offset` = kích thước lúc mở; một mail bị từ chối → chỉ bỏ mail đó; server từ chối mọi mail → lỗi folder, không bỏ qua gì; parser panic → bỏ mail đó; hủy giữa folder → không gửi thêm lô; tiến trình giữ khóa chết → khóa tự nhả (test chạy tiến trình con); `sync.lock` là symlink → file đích không bị đụng (test tự bỏ qua khi máy không cho tạo symlink; trên máy dev hiện tại bị bỏ qua, chưa kiểm chứng bằng test chạy thật).
