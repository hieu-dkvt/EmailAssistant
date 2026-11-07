# Spec: Nhà cung cấp AI theo người dùng (gateway kiểu 9router)

Ngày: 2026-10-25 · Thành phần: `backend/app/ai/` (`client.py`, `provider.py`, `secrets.py`, `settings.py`, `models.py`), `backend/app/api/ai_routes.py`, migration 0016 (`user_ai_provider`, cột `user_ai_settings.model`), 0021 (chỉ giao thức `openai`), `web/src/pages/AiSettingsPage.tsx` · Trạng thái: xong (sửa sau review: mục 12; chưa gọi thử gateway thật)

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Đã làm ở [spec 28](2026-11-01-internal-model-design.md) (2026-11-02): ô URL nhận mọi địa chỉ (chỉ chặn link-local) kèm cảnh báo địa chỉ công cộng, bỏ khóa AI mặc định từ biến môi trường. Từ 2026-11-05 chỉ còn giao thức tương thích OpenAI (migration 0021). Tài liệu viết lại theo code hiện tại ngày 2026-11-05.

## 1. Mục đích

Yêu cầu của người dùng (2026-10-25): trang "Cài đặt AI" cho nhập URL, API key và tên model, để hỏi đáp AI (spec 21) và dịch mail (spec 26) chạy qua gateway tương thích OpenAI như 9router. Từ 2026-11-01 gateway là model nội bộ của công ty.

## 2. Không làm

- Cấu hình dùng chung cho cả hệ thống (admin): mỗi người dùng cấu hình cho mình.
- Nhà cung cấp cho embedding, rerank: vẫn chạy self-host trong `ml-service`, không đổi.
- Streaming, tool use, nhiều provider mỗi người dùng, tự chuyển provider khi lỗi.
- Giao thức khác ngoài tương thích OpenAI. Cột `protocol` và trường `protocol` của API vẫn giữ (chỉ nhận `openai`) để thêm giao thức sau mà không đổi schema (quyết định của người viết, 2026-11-05).

## 3. Gọi gateway (`OpenAiLlm`, `provider.py`)

- `POST {base_url}/chat/completions` bằng `httpx`. Header `Authorization: Bearer <key>`, `Content-Type`; không gửi header nào khác (không `HTTP-Referer`/`X-Title`). Body `{model, max_tokens, messages: [system, user], response_format: {type: "json_schema", json_schema: {name, strict: true, schema}}}`; schema từ `strict_schema` (`client.py`).
- Gateway từ chối `response_format` (400) thì thử lại một lần với `{type: "json_object"}` và schema đưa vào cuối system prompt. Kết quả luôn kiểm bằng Pydantic (`ai_bad_output` khi sai).
- `finish_reason`: `stop` → bình thường; `length` → `max_tokens`; `content_filter` → `refusal`. `usage.prompt_tokens`/`completion_tokens` → token vào/ra; không có cache token (0).
- Timeout 120 giây. Lỗi mạng, 429, 5xx: thử lại tối đa 2 lần (`MAX_RETRIES`). Hết giờ đọc phản hồi (`ReadTimeout`): `ai_timeout`, không thử lại (spec 28). Không theo redirect, `trust_env=False` (không đi qua proxy của môi trường). Body phản hồi đọc tối đa 4 MiB.
- Lỗi đổi thành `LlmError` (`ai_auth_failed`, `ai_unavailable`, `ai_bad_request`, `ai_bad_output`, `ai_timeout`), không chép body phản hồi của gateway (có thể phản chiếu nội dung). Lỗi 429/5xx/mạng hiện kèm mã HTTP hoặc tên lớp lỗi, host, model, số lần thử.

## 4. Dữ liệu

- Bảng `user_ai_provider(user_id PK FK users ON DELETE CASCADE, protocol, base_url, api_key_enc, updated_at)`, `CHECK protocol = 'openai'` (migration 0021; 0016 nhận thêm một giao thức đã bỏ, dòng cũ được đổi sang `openai`). RLS theo `user_id`. `app_api` có `SELECT`, `INSERT`, `UPDATE (protocol, base_url, api_key_enc, updated_at)`, `DELETE` trên chính dòng của mình. Không phải bảng dữ liệu mail nhưng chứa bí mật nên cùng quy tắc RLS.
- Tên model theo tác vụ ở `user_ai_settings.model` (spec 22).
- Chưa lưu provider: `/ask` và dịch trả 503 `ai_not_configured`, job dịch dừng. Không có khóa mặc định từ biến môi trường.

## 5. Lưu và bảo vệ API key

- Mã hóa Fernet (thư viện `cryptography`), khóa trong biến môi trường `PMM_SECRET_KEY` (sinh bằng `python -m app.cli gen-secret-key`). Chưa đặt biến này thì `PUT /ai/provider` trả 503 `secret_key_missing`, key không được lưu.
- Khóa sai hoặc đổi khóa → giải mã lỗi → `ai_auth_failed` ("nhập lại"), không crash.
- API không bao giờ trả key: chỉ `api_key_set: bool` và `api_key_hint` (4 ký tự cuối, chỉ khi key dài từ 16 ký tự). Key không vào log, `ai_calls`, thông điệp lỗi, OpenAPI example.
- Giải mã chỉ khi dựng client. `LlmResolver` cache client theo người dùng và hash `(protocol, base_url, key)`; cấu hình đổi hoặc bị xóa thì bỏ cache và đóng client cũ.

## 6. Kiểm tra URL

Theo spec 28 mục 3: `validate_base_url` nhận `http` và `https` tới mọi địa chỉ (LAN, localhost, công cộng); từ chối URL sai dạng, userinfo, query hoặc fragment, cổng không hợp lệ, IP viết tắt (`2130706433`, `0x7f.1`), link-local (`169.254.0.0/16`, `fe80::/10`), ký tự điều khiển và khoảng trắng. `reject_link_local` phân giải DNS khi lưu và khi dựng client mới, từ chối host phân giải về link-local. `is_public_host` báo host công cộng để web cảnh báo.

## 7. API (chỉ phiên web, header `X-PMM-Request` cho PUT/POST/DELETE)

- `GET /api/v1/ai/provider` → `{secret_key_ready: bool, custom: {protocol, base_url, api_key_set, api_key_hint, public_host} | null}`.
- `PUT /api/v1/ai/provider` body `{protocol: "openai", base_url, api_key?}`: `api_key` bỏ trống chỉ giữ key đã lưu khi URL không đổi (đổi URL → 422 `api_key`, spec 28 mục 13); tạo mới mà thiếu key → 422. Sai URL → 422 (`loc` chỉ rõ trường).
- `DELETE /api/v1/ai/provider`: xóa cấu hình; AI không còn chạy cho tới khi nhập lại.
- `POST /api/v1/ai/provider/test`: gửi một câu cố định không liên quan tới mail bằng model đã nhập của tác vụ `answer`; trả `{ok, model, duration_ms}` hoặc mã lỗi như `LlmError`. Ghi `ai_calls` (task `provider_test`).

## 8. Web (`AiSettingsPage`)

- Khối "Nhà cung cấp AI": ô URL gốc (gợi ý `http://10.0.0.5:20128/v1`, gateway tương thích OpenAI, thường kết thúc bằng `/v1`), ô API key kiểu password (để trống = giữ key cũ khi URL không đổi, hiện "đã đặt …abcd"), nút Lưu, Kiểm tra kết nối, Xóa. Không có ô chọn giao thức; web luôn gửi `protocol: "openai"`.
- Luôn hiện "Mail không che sẽ gửi tới <host>"; thêm cảnh báo khi `public_host` (spec 28).
- `secret_key_ready=false` thì hiện hướng dẫn đặt `PMM_SECRET_KEY`.
- Bảng tác vụ: ô nhập tên model theo tác vụ (spec 22).

## 9. Phân quyền và log

- Mail gửi gateway ở dạng gốc (spec 28); câu thử kết nối không chứa dữ liệu mail. Lọc quyền (RLS) trước khi đưa dữ liệu cho AI.
- Log chỉ ghi `host`, `protocol`, tên lớp lỗi, token, thời gian. Không log key, URL đầy đủ (có thể chứa token trong path), body phản hồi. Tắt log debug của `httpx`.
- `user_ai_provider` có RLS; test: người dùng B không đọc/sửa/xóa được provider của A.
- Bí mật lưu mã hóa; bản sao lưu DB không đủ để lấy key nếu không có `PMM_SECRET_KEY`.

## 10. Kiểm thử

- Mã hóa/giải mã, khóa sai, thiếu `PMM_SECRET_KEY`.
- Kiểm tra URL: nhận `http`/`https` LAN, localhost, công cộng; chặn link-local (cả qua DNS), IP viết tắt, userinfo, query.
- API không trả key (GET/PUT/log); PUT giữ key cũ khi bỏ trống và URL không đổi, đổi URL thì 422; `protocol` khác `openai` → 422; DELETE thì `/ask` trả `ai_not_configured`; thiếu header → 403; chưa đăng nhập → 401.
- `OpenAiLlm` với gateway giả (`httpx.MockTransport`): `json_schema` thành công, lùi về `json_object` khi 400, `length`, `content_filter`, 401, 429, 5xx, hết giờ đọc không thử lại, redirect không theo, proxy của môi trường không dùng, body lỗi không lọt vào thông điệp.
- `strict_schema` (`tests/test_ai_client.py`): schema của output model đóng mọi object, giữ trường và mô tả.
- `call_task` ghi `ai_calls` với model tự nhập.
- RLS `user_ai_provider`; `user_ai_settings.model`.
- Web: render, lưu, kiểm tra kết nối, cảnh báo địa chỉ công cộng, trạng thái thiếu `PMM_SECRET_KEY`, không có ô chọn giao thức.

## 11. Giới hạn đã biết

- Gateway có thể không hỗ trợ structured output: dùng đường lùi `json_object`, độ ổn định phụ thuộc model.
- `strict_schema` chỉ thêm `additionalProperties: false`: output model mới phải dùng kiểu đơn giản và mọi trường bắt buộc.
- Đổi `PMM_SECRET_KEY` làm mất mọi key đã lưu.
- DNS đổi giữa lúc kiểm tra và lúc kết nối (rebinding) chưa chặn được.
- Dòng provider cũ đổi sang `openai` ở migration 0021 giữ nguyên URL: URL thiếu `/v1` thì lần gọi trả `ai_bad_request`, người dùng sửa ở Cài đặt AI.
- Chưa gọi thử gateway thật.

## 12. Sửa sau review (2026-10-25, security-reviewer)

- Client theo redirect có thể đưa key và mail sang host khác (kể cả `127.0.0.1:8001`): mọi client đặt `follow_redirects=False`; có test.
- Kiểm tra URL chỉ chặn IP viết thẳng: thêm chặn IP dạng số/hex và phân giải DNS (nay theo spec 28 mục 3). `/ai/provider/test` không còn dò được cổng nội bộ qua host kiểu này.
- `OpenAiLlm` đọc body có giới hạn 4 MiB (`ai_bad_output` khi vượt); client bị thay thế hoặc xóa được đóng (`aclose`).
- `api_key_hint` chỉ trả khi key dài từ 16 ký tự.
