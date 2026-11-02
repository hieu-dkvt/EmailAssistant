# Spec: Nhà cung cấp AI tùy chỉnh (OpenRouter, 9router…)

Ngày: 2026-10-25 · Thành phần: `backend/app/ai/` (`client.py`, `provider.py`, `secrets.py`, `settings.py`, `models.py`), `backend/app/api/ai_routes.py`, migration 0016 (`user_ai_provider`, cột `user_ai_settings.model`), `web/src/pages/AiSettingsPage.tsx` · Trạng thái: xong (sửa sau review: mục 13; chưa gọi thử gateway thật)

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Màn hình giữ nguyên, ô URL nhận mọi địa chỉ. Đã làm ở [spec 28](2026-11-01-internal-model-design.md) (2026-11-02): mục 6 (từ chối IP riêng) đổi thành chỉ chặn link-local kèm cảnh báo địa chỉ công cộng, mục 2 "Tắt việc che thông tin" và việc che không còn; khóa môi trường mặc định bỏ, `LlmResolver.for_user` trả `None` khi chưa nhập nhà cung cấp. Nội dung về che chỉ còn ý nghĩa lịch sử.

## 1. Mục đích

Yêu cầu của người dùng (2026-10-25): trang "Cài đặt AI" cho nhập API key, URL và tên model, để hỏi đáp AI (spec 21) chạy qua gateway tương thích như OpenRouter, 9router thay vì chỉ `ANTHROPIC_API_KEY` trong biến môi trường (spec 22 mục 2 đã loại việc nhập key trên web; spec này đảo lại quyết định đó theo yêu cầu mới).

**Quyết định của người dùng (2026-10-25): cho phép gửi nội dung mail đã che tới gateway công cộng** (vd. openrouter.ai), không chỉ Claude API của Anthropic. Điều kiện che 4 nhóm thông tin (spec 20) vẫn bắt buộc, không tắt được. Quyết định này nới NDA 2026-10-19 (chỉ Claude API): cần ghi vào CHANGELOG và kế hoạch, và người dùng chịu trách nhiệm xác nhận với khách.

## 2. Không làm

- Cấu hình dùng chung cho cả hệ thống (admin): mỗi người dùng cấu hình cho mình.
- Nhà cung cấp cho embedding, NER, rerank: vẫn chạy self-host trong `ml-service`, không đổi.
- Tắt việc che thông tin, hoặc bỏ che riêng cho một provider.
- Streaming, tool use, nhiều provider mỗi người dùng, tự chuyển provider khi lỗi.
- Giá ước tính cho model tự nhập: model không thuộc 3 cấp Claude thì `estimated_usd = null`.

## 3. Giao thức

| `protocol` | Dùng cho | Gọi |
|---|---|---|
| `anthropic` | Claude API (mặc định, giữ nguyên `AnthropicLlm`) | SDK `anthropic`, `base_url` mặc định `https://api.anthropic.com` |
| `openai` | OpenRouter, 9router, mọi gateway tương thích OpenAI | `POST {base_url}/chat/completions` bằng `httpx` |

Gọi kiểu `openai`:
- Header `Authorization: Bearer <key>`; body `{model, messages: [system, user], max_tokens, response_format: {type: "json_schema", json_schema: {name, strict: true, schema}}}`.
- Gateway từ chối `response_format` (400) thì thử lại một lần với `{type: "json_object"}` và schema đưa vào cuối system prompt. Kết quả luôn kiểm bằng Pydantic như hiện nay (`ai_bad_output` khi sai).
- `finish_reason`: `stop` → bình thường; `length` → `max_tokens`; `content_filter` → `refusal`. `usage.prompt_tokens`/`completion_tokens` → token vào/ra; không có cache token (0).
- Timeout 120 giây, retry tối đa 2 lần cho lỗi mạng và 5xx/429. Không theo redirect. Không gửi header nào ngoài `Authorization`, `Content-Type` (không `HTTP-Referer`/`X-Title`).
- Lỗi đổi thành `LlmError` cùng mã với hiện nay (`ai_auth_failed`, `ai_unavailable`, `ai_bad_request`…), thông điệp chung, không chép body phản hồi của gateway (có thể phản chiếu nội dung).
- Không có server-side fallback của Anthropic: `fallbacks=False` với `openai`.

## 4. Dữ liệu (migration 0016)

- Bảng `user_ai_provider(user_id PK FK users ON DELETE CASCADE, protocol, base_url, api_key_enc, updated_at)`. `CHECK protocol IN ('anthropic','openai')`. RLS theo `user_id`. `app_api` có `SELECT`, `INSERT`, `UPDATE (protocol, base_url, api_key_enc, updated_at)`, `DELETE` trên chính dòng của mình. Không phải bảng dữ liệu mail nhưng chứa bí mật nên cùng quy tắc RLS.
- `user_ai_settings.model text NULL` (tên model người dùng nhập cho tác vụ). `tier` giữ nguyên; `model` có giá trị thì ưu tiên hơn `tier` khi người dùng có provider tùy chỉnh.
- Chưa cấu hình provider: toàn bộ chạy như spec 22 (biến môi trường, cấp Haiku/Sonnet/Opus). Có provider: mỗi tác vụ dùng `model` nếu có, không có thì lỗi `ai_model_missing` (422 khi lưu cài đặt, 503 khi hỏi).

## 5. Lưu và bảo vệ API key

- Mã hóa Fernet (thư viện `cryptography`), khóa trong biến môi trường `PMM_SECRET_KEY` (sinh bằng `python -m app.cli gen-secret-key`, in ra khóa mới). Chưa đặt biến này thì `PUT /ai/provider` trả 503 `secret_key_missing`, key không được lưu.
- Khóa sai hoặc đổi khóa → giải mã lỗi → coi như chưa có key (`ai_auth_failed`), không crash. Đổi khóa nghĩa là người dùng nhập lại key.
- API không bao giờ trả key: chỉ `api_key_set: bool` và `api_key_hint` (4 ký tự cuối). Key không vào log, không vào `ai_calls`, không vào thông điệp lỗi, không vào OpenAPI example.
- Giải mã chỉ khi dựng client (cache theo `(user_id, hash cấu hình)` trong bộ nhớ tiến trình; cấu hình đổi hoặc bị xóa thì bỏ cache).

## 6. Kiểm tra URL (chống SSRF)

Áp dụng khi lưu và khi gọi:
- `https://` nhận mọi host công cộng. `http://` chỉ nhận `127.0.0.1`, `localhost`, `[::1]` (để dùng 9router chạy trên máy).
- Từ chối: có userinfo (`user:pass@`), có query hoặc fragment, host là IP literal thuộc dải riêng, link-local hoặc metadata (`10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `fc00::/7`, `fe80::/10`), cổng ngoài 1–65535. Loopback chỉ hợp lệ với `http`.
- Không theo redirect (`follow_redirects=False`).
- Tên máy chủ: https cần tên miền có dấu chấm; từ chối hậu tố `.localhost`, `.local`, `.internal`, `.localdomain`, `.home.arpa`, nhãn cuối toàn số hoặc dạng `0x…` (2130706433, 0x7f.1).
- Phân giải DNS (`ensure_public_host`) khi lưu và mỗi lần dựng client mới: mọi địa chỉ phải là địa chỉ công cộng, một địa chỉ riêng là từ chối. Loopback `http` được miễn.
- Giới hạn đã biết: DNS đổi giữa lúc kiểm tra và lúc kết nối (rebinding) chưa chặn được.

## 7. API (chỉ phiên web, header `X-PMM-Request` cho PUT/POST/DELETE)

- `GET /api/v1/ai/provider` → `{configured_env: bool, custom: {protocol, base_url, api_key_set, api_key_hint} | null, secret_key_ready: bool}`.
- `PUT /api/v1/ai/provider` body `{protocol, base_url, api_key?}`: `api_key` bỏ trống khi đã có key thì giữ key cũ; tạo mới mà thiếu key → 422. Sai URL → 422 (`loc` chỉ rõ trường).
- `DELETE /api/v1/ai/provider`: xóa cấu hình, quay về biến môi trường.
- `POST /api/v1/ai/provider/test`: gửi một câu cố định không liên quan tới mail ("trả về JSON {ok:true}") bằng model đã nhập của tác vụ `answer`; trả `{ok, model, ms}` hoặc mã lỗi như `LlmError`. Ghi `ai_calls` (task `provider_test`).
- `GET /ai/settings` thêm `custom_provider: bool` và mỗi tác vụ thêm `model: string | null`; `PUT /ai/settings` nhận thêm `models: {<task>: <string|null>}` (tối đa 200 ký tự, không chứa khoảng trắng đầu/cuối).
- `app.state.llm` toàn cục đổi thành bộ chọn theo người dùng: có provider tùy chỉnh thì dựng client theo cấu hình đó, không thì dùng client môi trường như cũ. `configured` = có provider tùy chỉnh hoặc có biến môi trường.
- Mã lỗi `ai_not_configured` đổi thông điệp: "Chưa cấu hình AI: nhập nhà cung cấp ở Cài đặt AI hoặc đặt ANTHROPIC_API_KEY".

## 8. Web (`AiSettingsPage`)

- Khối "Nhà cung cấp AI": chọn giao thức, ô URL (gợi ý `https://openrouter.ai/api/v1`), ô API key kiểu password (để trống = giữ key cũ, hiện "đã đặt …abcd"), nút Lưu, Kiểm tra kết nối, Xóa (quay về biến môi trường).
- Cảnh báo cố định khi dùng provider ngoài: "Mail đã che thông tin sẽ đi qua <host>. Bạn chịu trách nhiệm với NDA của khách."
- `secret_key_ready=false` thì hiện hướng dẫn đặt `PMM_SECRET_KEY` và khóa nút Lưu.
- Bảng tác vụ: có provider tùy chỉnh thì thay 3 radio cấp bằng ô nhập tên model; không có thì giữ như cũ.
- Khối thống kê tháng giữ nguyên; model lạ hiện "không rõ giá".

## 9. Phân quyền, NDA và log

- Che thông tin (spec 20) áp dụng cho mọi provider; che lỗi thì không gửi. Câu thử kết nối không chứa dữ liệu mail.
- Log chỉ ghi `host`, `protocol`, tên lớp lỗi, token, thời gian. Không log key, URL đầy đủ (có thể chứa token trong path), body phản hồi. Tắt log debug của `httpx`.
- `user_ai_provider` có RLS; test: người dùng B không đọc/sửa/xóa được provider của A.
- Bí mật lưu mã hóa; bản sao lưu DB không đủ để lấy key nếu không có `PMM_SECRET_KEY`.

## 10. Kiểm thử

- Mã hóa/giải mã, khóa sai, thiếu `PMM_SECRET_KEY`.
- Kiểm tra URL: cho `https` công cộng, `http://127.0.0.1`; chặn `http` công cộng, IP riêng/metadata, userinfo, query.
- API không trả key (GET/PUT/log); PUT giữ key cũ khi bỏ trống; DELETE quay về biến môi trường; thiếu header → 403; chưa đăng nhập → 401.
- Client `openai` với gateway giả (`httpx.MockTransport`): `json_schema` thành công, lùi về `json_object` khi 400, `length`, `content_filter`, 401, 429, 5xx, redirect không theo, body lỗi không lọt vào thông điệp.
- `call_task` ghi `ai_calls` với model tự nhập; `estimated_usd = null` cho model lạ.
- RLS `user_ai_provider`; `user_ai_settings.model`.
- Web: render, lưu, kiểm tra kết nối, cảnh báo NDA, trạng thái thiếu `PMM_SECRET_KEY`.

## 11. Khác với thiết kế ban đầu (khi làm)

- `user_ai_settings.tier` đổi thành cho phép NULL: dòng chỉ có `model` (chưa chọn cấp) đọc ra thì dùng cấp mặc định của tác vụ. Downgrade xóa các dòng NULL.
- `PUT /ai/settings`: `tiers` và `models` đều tùy chọn (mặc định `{}`); web chỉ gửi `models` khi đang dùng provider tùy chỉnh, chỉ gửi `tiers` khi không.
- Hỏi đáp qua provider tùy chỉnh bắt buộc có tên model cho mọi tác vụ, thiếu thì 503 `ai_model_missing` (trước khi gọi).
- `LlmResolver` (`app/ai/provider.py`) giữ client môi trường (`app.state.llm`, vẫn tiêm được cho test) và dựng client riêng theo người dùng; cache theo hash cấu hình.
- Lệnh `python -m app.cli gen-secret-key` in khóa Fernet mới.

## 12. Giới hạn đã biết

- Gateway tùy chỉnh có thể không hỗ trợ structured output: dùng đường lùi `json_object`, độ ổn định phụ thuộc model.
- Không giá ước tính cho model tự nhập.
- Đổi `PMM_SECRET_KEY` làm mất mọi key đã lưu.
- Chưa gọi thử gateway thật (cần key của người dùng).

## 13. Sửa sau review (2026-10-25, security-reviewer)

- Giao thức `anthropic`: SDK mặc định theo redirect (header `x-api-key` và mail đã che có thể đi sang host khác, kể cả `127.0.0.1:8001`). Sửa: `http_client=DefaultAsyncHttpxClient(follow_redirects=False)`; có test. (Spec 28 mục 12 đổi thành `httpx2.AsyncClient(trust_env=False, follow_redirects=False)`.)
- Kiểm tra URL chỉ chặn IP viết thẳng: thêm chặn tên nội bộ, IP dạng số/hex, và phân giải DNS (mục 6). `/ai/provider/test` không còn dò được cổng nội bộ qua host kiểu này.
- `OpenAiLlm` đọc body có giới hạn 4 MiB (`ai_bad_output` khi vượt); client bị thay thế hoặc xóa được đóng (`aclose`/`close`).
- `api_key_hint` chỉ trả khi key dài từ 16 ký tự.
