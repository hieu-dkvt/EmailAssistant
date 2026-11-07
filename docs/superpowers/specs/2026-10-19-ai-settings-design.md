# Spec: Cài đặt model AI theo tác vụ

Ngày: 2026-10-19 · Thành phần: `backend/app/ai/models.py`, `backend/app/ai/settings.py`, `backend/app/api/ai_routes.py` (`/api/v1/ai/settings`, `/api/v1/ai/usage`), migration 0015 (`user_ai_settings`), 0016 (cột `model`), 0019 (bỏ cột `tier`), `web/src/pages/AiSettingsPage.tsx` · Trạng thái: xong (sửa ở spec 28)

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Đã làm ở [spec 28](2026-11-01-internal-model-design.md) (2026-11-02): bản đầu của spec này chọn một trong ba cấp model đặt sẵn và hiện giá ước tính; nay mỗi tác vụ chỉ có tên model do người dùng nhập (migration 0019). Tài liệu viết lại theo code hiện tại ngày 2026-11-05.

## 1. Mục đích

Yêu cầu của người dùng (2026-10-19): có màn hình cài đặt model AI, chọn model cho từng tác vụ. Model nhỏ nhanh và tốn ít tài nguyên gateway, model lớn trả lời tốt hơn với câu hỏi khó.

## 2. Không làm

- Nhập URL và API key của gateway: thuộc spec 24 (khối "Nhà cung cấp AI" trên cùng trang).
- Cài đặt chung cho cả công ty (admin): mỗi người dùng tự chọn cho mình.
- Chỉnh `max_tokens`: cố định theo tác vụ trong code.
- Giới hạn số lần gọi theo tháng: chỉ hiện thống kê. Không có giá ước tính (model nội bộ).

## 3. Tác vụ (`app/ai/models.py`, `TASKS`)

| Tác vụ | Dùng ở | `max_tokens` |
|---|---|---|
| `question_analysis` | Phân tích câu hỏi: tách bộ lọc, từ khóa, dịch sang tiếng Nhật (spec 21 bước 3) | 2.000 |
| `answer` | Trả lời câu hỏi từ các đoạn mail (spec 21 bước 6) | 8.000 |
| `translate` | Dịch mail của khách sang tiếng Việt (spec 26) | 16.000 |

Giai đoạn 6 thêm tác vụ (phân loại, tóm tắt thread, soạn nháp reply) vào `TASKS`. Code không đặt sẵn tên model nào: tác vụ chưa nhập tên thì lần gọi trả 503 `ai_model_missing`.

## 4. Bảng `user_ai_settings`

`(user_id, task, model, updated_at)`, khóa chính `(user_id, task)`, `model` NOT NULL, `CHECK length(model) BETWEEN 1 AND 200 AND model = btrim(model)`. Xóa tên model của tác vụ là xóa dòng. Tác vụ không có trong code bị bỏ qua khi đọc (không CHECK ở DB để thêm tác vụ không cần migration). RLS theo `user_id`; `app_api` được `SELECT`, `INSERT`, `UPDATE (model, updated_at)`, `DELETE` trên dòng của mình.

## 5. API

- `GET /api/v1/ai/settings` → `{configured, tasks: [{task, label, description, model}]}`. `configured`: đã lưu nhà cung cấp AI (spec 24). `model` là `null` khi chưa nhập.
- `PUT /api/v1/ai/settings` (header `X-PMM-Request`), body `{models: {<task>: <tên model> | null}}`: tác vụ lạ hoặc tên sai dạng → 422; `null` hoặc rỗng → xóa. Trả lại như GET.
- `GET /api/v1/ai/usage` → thống kê tháng hiện tại (theo giờ UTC) của người dùng từ `ai_calls`: theo model: số lần gọi, token vào/ra; tổng số lần gọi.

## 6. Web (`AiSettingsPage`, menu "Cài đặt AI")

- Khối "Nhà cung cấp AI" (spec 24, 28) ở trên.
- Bảng tác vụ: mỗi tác vụ một ô "Tên model"; nút Lưu; thông báo đã lưu.
- Thống kê tháng này: số lần gọi, token theo model.

## 7. Phân quyền và dữ liệu

- Bảng có RLS; người dùng chỉ đọc/ghi cài đặt và thống kê của mình.
- Không có nội dung mail.

## 8. Kiểm thử

- GET khi chưa lưu; PUT rồi GET; tác vụ lạ hoặc tên sai dạng → 422; `null` xóa dòng; thiếu header chống CSRF → 403; chưa đăng nhập → 401.
- Cài đặt của A không ảnh hưởng B (RLS).
- Model dùng khi hỏi (spec 21) và khi dịch (spec 26) đúng tên đã nhập; thiếu tên → `ai_model_missing`.
- Thống kê: chỉ dòng `ai_calls` tháng này của chính người dùng.
