# Spec: Giao diện đồng bộ trên trang setting (collector)

Ngày: 2026-10-17 · Thành phần: `collector/internal/settingsui`, `collector/internal/service`, `collector/internal/tokenstore` · Trạng thái: xong (sửa sau review 2026-10-17: mục 2, 3, 4)

## 1. Mục đích

Trên trang setting đã có (chọn profile, chọn folder), người dùng nhập kết nối server, bấm "Đồng bộ", thấy tiến độ, và **nhận thông báo khi đồng bộ hoàn thành** (yêu cầu của người dùng 2026-10-17). Cũng từ trang này xóa mail của folder khỏi server (spec xóa mail).

## 2. Kết nối server

- Mục "Kết nối server": ô URL (mặc định `http://127.0.0.1:8000`), ô token (kiểu mật khẩu; đã lưu thì hiện "Đã lưu token"), nút "Lưu kết nối". Sau khi lưu, trang gọi kiểm tra và hiện "Đã kết nối: <email>" hoặc lỗi.
- `server_url` lưu trong `config.json`. Token lưu ở `%LOCALAPPDATA%\PMMailAssistant\collector\token.dat`, mã hóa bằng DPAPI (`CryptProtectData`, phạm vi người dùng hiện tại) trên Windows; hệ điều hành khác: file quyền 0600 (chỉ dùng khi dev).
- Token **không bao giờ** trả về trình duyệt, không ghi log.
- URL chỉ nhận `http://` hoặc `https://`; `http://` chỉ cho `127.0.0.1`/`localhost`, server khác bị từ chối (token đi dạng rõ qua mạng) và phải dùng `https://`.
- **Token gắn với server**: file token lưu `{origin, token}` (origin = `scheme://host:port`). Lưu URL có origin khác mà để trống ô token → từ chối "token đã lưu dùng cho server khác: đổi server thì phải nhập token của server mới". Mỗi lần gọi server, URL trong `config.json` được kiểm tra lại (http chỉ cho loopback) và phải cùng origin với token; nếu không, token không được gửi đi (chống `config.json` bị sửa tay trỏ sang server lạ). Client được tạo bằng chính URL đã kiểm tra và chuẩn hóa, không dùng chuỗi thô.
- Server trả 426 (collector quá cũ) → thông báo "Collector quá cũ so với server: hãy cập nhật collector rồi đồng bộ lại". File token của bản cũ (không có origin) phải nhập lại token.
- Không theo redirect của server (spec đồng bộ mục 5).
- DPAPI phạm vi người dùng: người dùng khác/máy khác không giải mã được file token; **không** chống được chương trình độc hại chạy dưới chính tài khoản người dùng (entropy nằm sẵn trong `.exe`).

## 3. Đồng bộ

- Nút "Đồng bộ" ở chân trang, chỉ bật khi: đã lưu cấu hình folder (không còn thay đổi chưa lưu), đã có kết nối server, không có đồng bộ đang chạy.
- Bấm → `POST /api/sync/start` → collector chạy đồng bộ nền (spec đồng bộ tăng dần). Trang hỏi `GET /api/sync/status` mỗi giây.
- Trong lúc chạy: thanh tiến độ theo byte, dòng "Đang đồng bộ folder i/N: <tên folder> — X mail đã gửi", nút "Dừng".
- **Khi xong:** hộp thông báo nổi bật "Đồng bộ hoàn thành" kèm: số mail mới, số mail đã có trên server, số folder đã đọc / bỏ qua vì không đổi, thời gian; thêm "Có N folder lỗi", "Có N mail bị bỏ qua (server từ chối hoặc không đọc được)", "Có N mail chỉ đọc được header" khi có, và từng lỗi folder. Tiêu đề tab đổi thành "✓ Đồng bộ hoàn thành". Nếu lỗi: "Đồng bộ dừng do lỗi: <thông điệp>" kèm số đã gửi được. Lỗi nội bộ (panic) của collector: "Đồng bộ dừng do lỗi nội bộ của collector…", collector không bị sập.
- Đang đồng bộ thì `POST /api/config` và `POST /api/server/settings` trả 409 "Đang đồng bộ: bấm Dừng rồi lưu lại" (đổi folder hoặc server giữa lượt làm state lệch).
- Server cục bộ không tự tắt vì "không hoạt động" khi đang đồng bộ. Bấm "Đóng" khi đang đồng bộ → hỏi "Đang đồng bộ. Dừng và đóng?".

## 4. API cục bộ mới

| Method | Đường dẫn | Việc |
|---|---|---|
| GET | `/api/server/status` | `{server_url, has_token, connected, email, error}` (gọi `/api/v1/me`) |
| POST | `/api/server/settings` | `{server_url, token?}` lưu kết nối (token rỗng = giữ token cũ, chỉ khi cùng server); đang đồng bộ → 409 |
| GET | `/api/server/sources` | danh sách folder trên server (spec xóa mail) |
| POST | `/api/server/delete-folder` | xóa mail của folder khỏi server (spec xóa mail) |
| POST | `/api/sync/start` | bắt đầu đồng bộ; đang chạy → 409 |
| POST | `/api/sync/stop` | yêu cầu dừng |
| GET | `/api/sync/status` | `{state: idle|running|done|error|stopped, progress, summary, error}` |

Tất cả đi qua lớp bảo vệ token/Host/Origin hiện có.

## 5. Kiểm thử

- Lưu kết nối: token được mã hóa và không xuất hiện trong response; URL sai bị từ chối; đổi server mà không nhập token → từ chối; `config.json` bị sửa trỏ sang server khác → token không được gửi đi (đã kiểm bằng mutation test).
- Đang đồng bộ: lưu cấu hình folder / kết nối → 409; panic trong lượt đồng bộ → trạng thái `error`, thông điệp không chứa giá trị panic.
- `sync/start` khi chưa có kết nối hoặc config → 400; khi đang chạy → 409; trạng thái chuyển `running` → `done` với summary đúng (server giả).
- Chạy thử thủ công với backend thật + `data/thunderbird-profile`: bấm Đồng bộ, thấy thông báo "Đồng bộ hoàn thành" với số mail đúng; bấm lại → 0 mail mới.
