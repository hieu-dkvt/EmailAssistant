# Spec: Gợi ý thread liên quan (lớp 4 gom thread)

Ngày: 2026-10-19 · Thành phần: `backend/app/search/similar.py`, `backend/app/api/web_routes.py` (`GET /api/v1/threads/{id}/similar`), `web/src/components/SimilarThreads.tsx` · Trạng thái: xong (ngưỡng cần chỉnh trên mail thật: mục 8)

## 1. Mục đích

Lớp 4 của gom thread trong kế hoạch: khi khách trả lời bằng mail mới (mất References, đổi subject), lớp 1–3 không nối được. Trang xem thread gợi ý các thread có nội dung gần giống (embedding), người dùng bấm "Gộp" để gộp bằng sửa tay có sẵn (spec 15). Không tự gộp.

## 2. Không làm

- Danh sách gợi ý toàn cục (mọi cặp thread nên gộp): chỉ tính cho thread đang xem, lúc xem, không lưu.
- Dùng Claude để xác nhận: chỉ embedding (không gửi gì ra ngoài).
- Nhớ gợi ý đã bỏ qua.

## 3. Thuật toán

- Hạt giống: đoạn số 0 của tối đa 5 mail mới nhất trong thread (đoạn hợp lệ: model hiện hành, `cleaner_version` khớp).
- Với mỗi hạt giống: 30 đoạn gần nhất (HNSW, iterative scan) thuộc mail **không** nằm trong thread đang xem.
- Gộp theo thread: điểm = độ tương đồng cosine lớn nhất (`1 - khoảng cách`). Giữ thread có điểm ≥ `SIMILAR_MIN` (mục 7), tối đa 5, điểm cao trước.
- Kèm mỗi thread: subject (mail đầu thread), số mail, ngày mail cuối, mail đại diện (mail có đoạn gần nhất) để làm đích khi gộp.

## 4. API

`GET /api/v1/threads/{id}/similar` (phiên web) → `{items: [{thread_id, subject, message_count, last_message_at, score, message_id}], pending}`; `pending = true` khi thread chưa có embedding (web hiện "chưa xử lý xong"). Thread không có → 404. ml-service không cần (dùng vector đã lưu).

## 5. Web

Trang thread: khối "Thread có thể liên quan" (tải sau phần nội dung) liệt kê gợi ý (subject, số mail, ngày, điểm %), mỗi dòng có link mở thread và nút "Gộp vào thread này" → xác nhận → tạo sửa tay `merge` (mail đầu thread đang xem → mail đại diện), như hộp thoại gộp hiện có. Không có gợi ý thì không hiện khối.

## 6. Phân quyền và dữ liệu

Truy vấn trong `user_session` (RLS): hạt giống và kết quả chỉ từ mail của người dùng. Không gọi dịch vụ ngoài.

## 7. Kiểm thử và ngưỡng

- DB với vector dựng sẵn: thread có đoạn gần → được gợi ý, điểm đúng; dưới ngưỡng → không; không gợi ý chính thread đang xem; mail của B gần hơn vẫn không xuất hiện; thread chưa embed → `pending`.
- `SIMILAR_MIN` chọn sau khi đo trên profile giả (mục 8).

## 8. Kết quả đo

- `SIMILAR_MIN = 0,80` (quyết định của người viết spec). Trên 1.050 mail giả (2026-10-21, bge-m3 thật): thread báo giá "在庫管理システム改修" được gợi ý 5 thread báo giá/sửa chức năng khác với điểm 0,93–0,97, vì phần mail sinh hàng loạt của bộ giả dùng chung mẫu câu. Dữ liệu giả không có cặp "cùng trao đổi bị tách" thật nên chưa đo được độ chính xác.
- Cần chỉnh ngưỡng trên mail thật: người dùng xem các gợi ý và cho biết gợi ý đúng/sai (Claude Code không đọc mail thật).
- Tốc độ: mỗi hạt giống một truy vấn HNSW (≤ 5), vài chục ms trên 1.050 mail; trang thread tải khối này sau phần nội dung.
