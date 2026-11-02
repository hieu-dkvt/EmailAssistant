# Spec: Cài đặt model AI theo tác vụ

Ngày: 2026-10-19 · Thành phần: `backend/app/ai/models.py`, `backend/app/ai/settings.py`, `backend/app/api/ai_routes.py` (`/api/v1/ai/settings`, `/api/v1/ai/usage`), migration 0015 (`user_ai_settings`), `web/src/pages/AiSettingsPage.tsx` · Trạng thái: xong

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Đã làm ở [spec 28](2026-11-01-internal-model-design.md) (2026-11-02): ba cấp Haiku/Sonnet/Opus, giá ước tính và ghi chú "không tắt được" đã bỏ, nội dung về che và ba cấp chỉ còn ý nghĩa lịch sử; mỗi tác vụ chỉ còn tên model do người dùng nhập (migration 0019).

## 1. Mục đích

Yêu cầu của người dùng (2026-10-19): có màn hình cài đặt model AI với 3 cấp **Haiku / Sonnet / Opus**, chọn cấp cho từng tác vụ. Cấp thấp rẻ và nhanh, cấp cao trả lời tốt hơn với câu hỏi khó.

## 2. Không làm

- Nhập API key trên web: spec này chỉ dùng biến môi trường `ANTHROPIC_API_KEY` (không lưu DB). Từ 2026-10-25 spec 24 thêm nhà cung cấp tùy chỉnh (URL, key mã hóa, tên model); không nhập thì vẫn chạy như spec này.
- Cài đặt chung cho cả công ty (admin): mỗi người dùng tự chọn cho mình.
- Chỉnh `effort`, `max_tokens`: cố định theo tác vụ trong code.
- Giới hạn chi phí theo tháng: chỉ hiện thống kê.

## 3. Cấp model (`app/ai/models.py`)

| Cấp | Model mặc định | Giá (USD / 1 triệu token, vào / ra) |
|---|---|---|
| `haiku` | `claude-haiku-5-5` | 0,10 / 0,50 |
| `sonnet` | `claude-sonnet-5-5` | 2,00 / 10,00 |
| `opus` | `claude-opus-5-5` | 4,00 / 20,00 |

- Đổi model của một cấp bằng biến môi trường `PMM_MODEL_HAIKU`, `PMM_MODEL_SONNET`, `PMM_MODEL_OPUS` (khi Anthropic ra model mới). Giá là bảng tĩnh (ngày 2026-10-06) để **ước tính** chi phí; giá thật xem hóa đơn Anthropic.
- Ước tính = token vào × giá vào + token ra × giá ra + token đọc cache × giá đọc cache + token ghi cache × 1,25 × giá vào.

## 4. Tác vụ

| Tác vụ | Dùng ở | Cấp mặc định |
|---|---|---|
| `question_analysis` | Phân tích câu hỏi: tách bộ lọc, từ khóa, dịch sang tiếng Nhật (spec 21 bước 3) | `haiku` |
| `answer` | Trả lời câu hỏi từ các đoạn mail (spec 21 bước 6) | `sonnet` |

Giai đoạn 6 thêm tác vụ (phân loại, tóm tắt thread, soạn nháp reply) vào bảng này; người dùng chưa chọn thì dùng cấp mặc định.

## 5. Bảng `user_ai_settings` (migration 0015)

`(user_id, task, tier, updated_at)`, khóa chính `(user_id, task)`, `CHECK tier IN ('haiku','sonnet','opus')`. Tác vụ không có trong code bị bỏ qua khi đọc (không CHECK ở DB để thêm tác vụ không cần migration). RLS theo `user_id`; `app_api` được `SELECT`, `INSERT (user_id, task, tier)`, `UPDATE (tier, updated_at)`.

## 6. API

- `GET /api/v1/ai/settings` → `{configured, tasks: [{task, label, description, tier, default_tier}], tiers: [{tier, model, label, input_price, output_price}], masking: [nhóm thông tin bị che]}`.
- `PUT /api/v1/ai/settings` (header `X-PMM-Request`), body `{tiers: {<task>: <tier>}}`: tác vụ lạ hoặc cấp lạ → 422. Ghi bằng `INSERT … ON CONFLICT (user_id, task) DO UPDATE`. Trả lại như GET.
- `GET /api/v1/ai/usage` → thống kê tháng hiện tại (theo giờ UTC) của người dùng từ `ai_calls`: theo model: số lần gọi, token vào/ra, chi phí ước tính; tổng.

## 7. Web (`AiSettingsPage`, menu "Cài đặt AI")

- Trạng thái kết nối AI: "Đã cấu hình" hoặc hướng dẫn đặt `ANTHROPIC_API_KEY` rồi chạy lại backend.
- Bảng tác vụ: mỗi tác vụ 3 lựa chọn (radio) Haiku / Sonnet / Opus kèm tên model và giá; nút Lưu; thông báo đã lưu.
- Ghi chú các nhóm thông tin luôn được che trước khi gửi AI (không tắt được: theo yêu cầu NDA).
- Thống kê tháng này: số lần gọi, token, chi phí ước tính theo model.

## 8. Phân quyền và dữ liệu

- Bảng có RLS; người dùng chỉ đọc/ghi cài đặt và thống kê của mình.
- Không có nội dung mail. `configured` chỉ cho biết có biến môi trường, không trả key.

## 9. Kiểm thử

- GET mặc định khi chưa lưu; PUT rồi GET; tác vụ/cấp lạ → 422; thiếu header chống CSRF → 403; chưa đăng nhập → 401.
- Cài đặt của A không ảnh hưởng B (RLS).
- Model dùng khi hỏi (spec 21) đúng theo cài đặt; biến `PMM_MODEL_*` đổi model của cấp.
- Thống kê: chỉ dòng `ai_calls` tháng này của chính người dùng; chi phí ước tính đúng công thức.
