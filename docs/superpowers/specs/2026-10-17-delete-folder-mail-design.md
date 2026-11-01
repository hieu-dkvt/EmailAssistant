# Spec: Xóa mail của folder khỏi server

Ngày: 2026-10-17 · Thành phần: `backend/app/sources.py`, `backend/app/api/routes.py`, collector (API cục bộ + giao diện) · Trạng thái: xong (sửa sau review 2026-10-17: mục 2, 3, 4)

## 1. Mục đích (người dùng quyết 2026-10-17)

- Bỏ chọn folder trên trang setting **không xóa** mail đã đồng bộ; collector chỉ ngừng đọc folder đó.
- Người dùng có thể **chủ động chọn xóa** mail đã đồng bộ của một folder khỏi server (vd. lỡ đồng bộ folder của dự án NDA khác).

## 2. Quy tắc xóa

- Chỉ xóa bản mail mà sau khi bỏ folder này **không còn nằm trong folder nào khác** của chính người đó. Mail còn ở folder khác được giữ (chỉ bỏ liên kết với folder bị xóa).
- Mail đã mồ côi từ trước (không thuộc folder này) không bị đụng tới.
- Xóa luôn dòng `mail_sources` của folder. Dữ liệu dẫn xuất (AI, embedding… ở giai đoạn sau) phải có FK `ON DELETE CASCADE` tới `messages`.
- Xóa hẳn, không khôi phục. Phải xác nhận trên giao diện.
- Chỉ xóa được folder đang **không được chọn** đồng bộ (tránh xóa trong lúc vẫn đồng bộ lại), **hoặc** folder không còn file mbox trong profile (đã đổi tên/xóa trong Thunderbird: không có dòng nào để bỏ tick, nhất là khi "tự thêm folder mới" đang bật). Sau khi xóa, collector reset state của folder đó, nên nếu sau này chọn lại thì đọc lại từ đầu.
- Không chạy song song với đồng bộ (cùng khóa `sync.lock`).
- **Folder hiệu lực (spec 26, 2026-11-01):** việc xóa luôn theo `message_ownership` vật lý. Mail dịch tự động được "chuyển" sang folder dự án chỉ trên web (`message_auto_runs.target_source_id`), ownership vẫn ở Inbox. Hệ quả: xóa mail của Inbox xóa cả mail đã chuyển đi (bản dịch và `message_auto_runs` đi theo bằng `ON DELETE CASCADE`); xóa folder dự án (đích) không xóa mail chuyển tới đó, dòng `target_source_id` về NULL và mail hiện lại ở Inbox trên web. Số mail của `/sources` (collector) vẫn là số vật lý; số mail theo folder hiệu lực chỉ ở `GET /folders` của web. Thuật ngữ glossary thêm từ đoạn bôi đen không bị xóa theo mail (không có khóa tới mail).

## 3. Backend

- `GET /api/v1/sources` → danh sách folder của người dùng: `[{id, folder_path, account, kind, message_count, last_scan_at}]` (`message_count` = số ownership chưa `is_deleted`).
- `POST /api/v1/sources/delete-mail`, body `{"folder_path": "..."}` (đọc sau khi xác thực token) → trong `user_session`:
  1. tìm `mail_sources` của chính người đó (không có → 404 `{"detail": "...", "code": "source_not_found"}`);
  2. lấy danh sách `message_id` thuộc folder;
  3. xóa dòng `mail_sources` (cascade xóa ownership);
  4. `DELETE FROM messages WHERE id = ANY(:ids) AND NOT EXISTS (ownership còn lại)`.
  Response `{"deleted_messages", "kept_messages"}`.
- Quyền (migration 0005): `GRANT DELETE ON messages TO app_api` + policy `messages_owner_delete FOR DELETE USING (user_id = app_user_id())`.
- Log: `user_id`, `source_id`, số lượng.

## 4. Collector

- API cục bộ của trang setting:
  - `GET /api/server/sources` → gọi backend, trả danh sách folder trên server kèm số mail.
  - `POST /api/server/delete-folder` `{"path": "..."}` → từ chối đường dẫn không phải dạng tương đối trong profile (`..`, `\`, `:`, bắt đầu bằng `/`, rỗng); từ chối (409) nếu folder đang được chọn trong cấu hình đã lưu và file mbox còn tồn tại; gọi backend; thành công thì reset state cục bộ của folder. Backend trả 404 **kèm mã `source_not_found`** (đã xóa từ trước, vd. ở máy khác) → coi như xong (0 mail), vẫn reset state. 404 không có mã đó (proxy, sai đường dẫn API) là lỗi: không reset state, không báo "đã xóa" (người dùng không được tin nhầm dữ liệu NDA đã bị xóa).
- Giao diện: folder **bỏ tick** mà server còn mail hiện liên kết "Xóa N mail khỏi server". Folder có trên server nhưng không còn trong profile hiện ở nhóm riêng "Có trên server, không còn trong Thunderbird", mỗi dòng có liên kết xóa. Bấm → hộp xác nhận ghi rõ tên folder, số mail, "không khôi phục được" → kết quả "Đã xóa X mail (giữ Y mail còn ở folder khác)".

## 5. Kiểm thử

- Backend: xóa đúng mail chỉ thuộc folder; mail còn ở folder khác được giữ; mail mồ côi khác không bị xóa; dữ liệu người khác không đổi; folder không tồn tại hoặc của người khác → 404; không có token → 401; không có token + body hỏng → 401 (xác thực trước khi đọc body).
- Collector: từ chối khi folder đang được chọn; reset state sau khi xóa thành công; folder không còn file mbox xóa được dù "tự thêm folder mới" bật; đường dẫn lạ bị từ chối; backend 404 có mã `source_not_found` → coi như đã xóa, 404 không mã → lỗi và state giữ nguyên.
