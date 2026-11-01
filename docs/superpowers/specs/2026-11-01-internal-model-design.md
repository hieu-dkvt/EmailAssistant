# Spec: Chuyển sang model nội bộ và bỏ che thông tin

Ngày: 2026-11-01 · Thành phần: `backend/app/ai/` (`provider.py`, `client.py`, `models.py`, `settings.py`, `ask.py`, `translate.py`, `glossary.py`, `prompts.py`; xóa `masking.py`, `names.py`, `people.py`), `backend/app/auto_translate.py`, `backend/app/ml_client.py`, `backend/app/main.py`, `backend/app/worker.py`, `backend/app/api/ai_routes.py`, `backend/app/web_schemas.py`, `backend/app/db/models.py`, `ml-service/` (xóa `/ner`, `app/ner.py`, spaCy, GiNZA), `web/src/pages/AiSettingsPage.tsx`, `web/src/pages/AskPage.tsx`, `web/src/components/MessageTranslation.tsx`, migration 0019 · Trạng thái: đang làm (đã duyệt thiết kế, chưa có code)

## 1. Mục đích

Quyết định của người dùng (2026-11-01):

- **Không dùng API ngoài.** AI chạy trên model local nội bộ, gọi qua gateway hoạt động như 9router.
- **Không cần che thông tin**, vì mail không ra ngoài. Các ràng buộc NDA về gửi AI ra ngoài không còn cần thiết. Thay cho quyết định bắt buộc che (2026-10-19) và "che thông tin giữ nguyên dù model là nội bộ" (2026-10-31).
- **Giữ màn hình Cài đặt AI:** người dùng nhập URL gateway, API key, model.
- **Ô URL nhận mọi địa chỉ.** Người dùng chọn phương án này sau khi được nêu rủi ro, thay vì chỉ nhận địa chỉ nội bộ. Code không chặn API ngoài: "không gửi mail ra API ngoài" là quy ước do người cấu hình đảm bảo.

Code hiện tại làm ngược lại: `validate_base_url` từ chối gateway trong LAN, mọi text gửi AI qua `Masker`, và có đường gọi Anthropic mặc định bằng `ANTHROPIC_API_KEY`.

## 2. Không làm

- Chặn URL công cộng ở code: người dùng đã chọn nhận mọi URL.
- Danh sách gateway cấu hình ở server, cấu hình gateway chung cho cả hệ thống: mỗi người dùng tự nhập như spec 24.
- Bỏ giao thức `anthropic` trên màn hình: màn hình giữ nguyên, SDK `anthropic` vẫn còn cho giao thức này.
- Nới nguyên tắc "mỗi bản mail chỉ gọi AI một lần": người dùng chưa quyết định đổi.
- Tăng ngân sách ngữ cảnh, tool, agent: chức năng khác.
- Dịch lại tự động các bản dịch cũ.

## 3. Kiểm tra URL (`app/ai/provider.py`)

`validate_base_url` nhận:

- `http` và `https`, tới mọi địa chỉ: IP LAN, tên máy một nhãn (`gateway`), `.local`, `.internal`, localhost, IP công cộng.
- Cổng tùy ý, đường dẫn tùy ý (`http://10.0.0.5:20128/v1`).

Vẫn từ chối (`ValueError`, 422 như hiện tại):

- Scheme khác, thiếu host, URL chứa tài khoản, query hoặc `#`, cổng sai.
- IP dạng số hoặc hex không chuẩn (`2130706433`, `0x7f.1`): nhãn cuối toàn số hoặc bắt đầu bằng `0x` mà không phải IP hợp lệ.
- **Địa chỉ link-local** (`169.254.0.0/16`, `fe80::/10`), gồm dịch vụ metadata của cloud. Quyết định của người viết spec: đây không phải gateway hợp lệ, chặn để `/ai/provider/test` không chạm tới.

`ensure_public_host` đổi thành `reject_link_local(base_url)`: phân giải host, có IP link-local thì `ValueError`, không phân giải được thì bỏ qua (lỗi mạng báo ở lần gọi). `LlmResolver` và `/ai/provider/test` gọi hàm này thay cho hàm cũ. Mã lỗi giữ `ai_bad_request`, thông điệp không còn nói "mạng nội bộ".

Bỏ `INTERNAL_SUFFIXES`, `LOOPBACK_NAMES` và quy tắc "http chỉ cho localhost, localhost chỉ dùng http".

Không theo redirect, không log URL đầy đủ, key, body phản hồi, không chép thông điệp của gateway vào response: giữ như spec 24.

### Cảnh báo địa chỉ công cộng

Đây là lớp bảo vệ duy nhất còn lại trước việc nhập nhầm URL, quyết định của người viết spec.

- `AiProviderCustom` có thêm `public_host: bool`: host là IP công cộng, hoặc phân giải ra ít nhất một IP công cộng (`is_global`). Không phân giải được → `false`.
- `AiSettingsPage` luôn hiện "Mail **không che** sẽ gửi tới `host`", thay cho dòng "Mail đã che… NDA" ở dòng 237. `public_host = true` thì hiện `Alert` kiểu cảnh báo: "Đây là địa chỉ công cộng, mail sẽ ra ngoài mạng nội bộ."
- Chỉ cảnh báo, không chặn lưu.

## 4. Bỏ đường gọi Anthropic mặc định

Phải làm cùng đợt với bỏ che: nếu còn khóa mặc định, người chưa nhập gateway sẽ âm thầm gửi mail chưa che tới Anthropic.

- `app/ai/client.py`: bỏ `configured()`, `default_llm()`, `anthropic_base_url()`, `API_BASE_URL`, `fallbacks_enabled()`, tham số `fallbacks` của `LlmClient.create`. Giữ `AnthropicLlm` cho giao thức `anthropic`, luôn nhận client đã cấu hình `base_url` từ `_anthropic_llm`.
- `app/ai/provider.py`: bỏ `env_llm()`. `LlmResolver.for_user` trả `LlmClient | None`, không còn cờ `custom`. `None` khi chưa nhập nhà cung cấp.
- Bỏ biến `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `PMM_ANTHROPIC_BASE_URL`, `PMM_AI_FALLBACKS`, `PMM_MODEL_HAIKU/SONNET/OPUS`. Code không đọc các biến này nữa, kể cả khi còn đặt trong môi trường.
- Bỏ tham số `custom_provider` của `ask`, `translate`, `auto_translate`.
- Chưa nhập nhà cung cấp: `/ask`, dịch tay → 503 `ai_not_configured`; job dịch dừng, phút sau thử lại, như nhánh `llm is None` hiện tại. Câu hỏi còn treo ở spec 26 (job dịch rơi về khóa môi trường) không còn.
- `create_app(llm=...)` giữ tham số cho test: truyền client giả vào `LlmResolver`, không có đường đọc môi trường.

### Bỏ ba cấp model

Ba cấp Haiku/Sonnet/Opus chỉ có nghĩa với Claude.

- `app/ai/models.py` chỉ còn `TASKS`. Bỏ `TIER_INFO`, `Tier`, `model_for`, `tier_of_model`, bảng giá.
- `call_task` nhận `model: str` (tên người dùng nhập), bỏ `tier`, `custom_model`. Thiếu tên model cho tác vụ xử lý như nhánh hiện có của spec 24, 26.
- Migration 0019 (`down_revision = "0018"`): xóa dòng `user_ai_settings` có `model IS NULL`, `DROP COLUMN tier` (kèm `ck_user_ai_settings_tier`), đặt `model NOT NULL`, cấp lại quyền cột cho `app_api`. Đổi `models.py` cho khớp. Downgrade thêm lại cột `tier` nullable, không khôi phục dữ liệu. Quyết định của người viết spec.
- `/ai/usage` bỏ `estimated_usd`, `total_estimated_usd`. Thống kê token giữ nguyên.

## 5. Bỏ che thông tin

### Backend

- Xóa `app/ai/masking.py`, `app/ai/names.py`, `app/ai/people.py`, `build_masker`, `learn_rows`, `_participants`, `_mask_ranges`, `WINDOW_CHARS`.
- `/ask`: câu hỏi và nguồn gửi thẳng, kết quả AI dùng trực tiếp, không còn `unmask`. `plan.sender`, `search_queries`, `keywords` dùng nguyên giá trị.
- Giữ mọi giới hạn độ dài: subject 300 ký tự, To/Cc 10 người, tên và địa chỉ 100 ký tự, thân mail `SOURCE_LIMITS`. Đây là giới hạn chi phí xử lý, không phụ thuộc việc che.
- `cut_masked` đổi thành `cut_text(text, limit) -> tuple[str, bool]` trong `app/ai/text.py`: cắt ở ranh giới đoạn, rồi cuối dòng, trong nửa sau giới hạn; không có thì cắt tại giới hạn.
- Dịch: bỏ `_mask_mail`, `_masked_glossary`, `MAX_MASK_INPUT_CHARS`, lỗi `mask_unstable`. Glossary khớp trên text gốc đã cắt. Dịch **không còn gọi ml-service**: bỏ tham số `ml` của `translate`, `auto_translate`, và nhánh chờ `ml_unavailable` của job.
- `check_translation`: bỏ `labels_missing`, `labels_unknown`. Giữ `glossary_missing`, `glossary_conflict`, `truncated`.
- `nfkc_map` chuyển từ `masking.py` sang `glossary.py`. Bỏ `LABEL_RE` và nhánh bỏ qua nhãn trong `match_glossary`.
- `ml_client.py`: bỏ `ner`, `NerEntity`, `NER_LABELS`.
- `/ask` vẫn cần ml-service để embed câu hỏi. Không chạy thì 503 `ml_unavailable` như hiện tại.

### Prompt

- `ANALYSIS_SYSTEM`, `ANSWER_SYSTEM`, prompt dịch: bỏ mọi quy tắc về nhãn `[PERSON_1]`.
- `TRANSLATE_PROMPT_VERSION` lên 2: bản dịch cũ hiện nhãn "cũ", chỉ gọi AI lại khi người dùng bấm "Dịch lại" (spec 26 mục 10).

### API

| Thay đổi | Ảnh hưởng |
|---|---|
| `AskResult` bỏ `mask_counts` | Web |
| `AiSettings` bỏ `masking`, `tiers`, `custom_provider`; `AiTaskInfo` bỏ `tier`, `default_tier` | Web |
| `AiSettingsUpdate` bỏ `tiers` | Web |
| `AiProvider` bỏ `configured_env`; `AiProviderCustom` thêm `public_host` | Web |
| `/ai/usage` bỏ giá ước tính | Web |

Sinh lại `web/openapi.json`, `web/src/api/schema.ts`. Collector không bị ảnh hưởng.

### Web

- `AiSettingsPage`: bỏ ba cấp model, thẻ "Che thông tin trước khi gửi AI", nhánh `configuredEnv`. Luôn hiện ô tên model theo tác vụ. Ô giao thức, URL, API key, nút thử kết nối giữ nguyên.
- `AskPage`: giữ khối "Nội dung đã gửi cho AI", bỏ chữ "đã che", số nhãn, câu giải thích về che.
- `MessageTranslation`: sửa thông báo `ml_unavailable`. Giữ chữ hiển thị của `labels_missing`, `labels_unknown` vì bản dịch cũ còn lưu mã này.

### ml-service

- Xóa `POST /ner`, `app/ner.py`, `tests/test_ner.py`, `ner_models` trong `/health`, `ML_NER_JA`, `ML_NER_EN`, tham số `load_recognizer`.
- Gỡ `spacy`, `ginza`, `ja-ginza`, `en-core-web-sm` khỏi `pyproject.toml`, `[tool.uv.sources]`, `uv.lock`.
- Giữ `/embed`. Đo lại thời gian khởi động và bộ nhớ, ghi vào `ml-service/CLAUDE.md`.

## 6. Phân quyền và dữ liệu

- Mail gửi tới AI ở dạng gốc, gồm tên người, công ty, số tiền. Vì vậy địa chỉ nhập ở Cài đặt AI quyết định mail đi đâu.
- Không đổi: tìm kiếm và RAG lọc bằng RLS **trước** khi gửi AI; không log nội dung mail, câu hỏi, câu trả lời; `ai_calls` không có nội dung; AI không có tool; không lưu câu hỏi.
- Lỗi của gateway có thể chứa nội dung request: giữ quy tắc không chép thông điệp gateway vào log hay response.
- `Claude Code không đọc mail thật`: không đổi.
- API key đi qua `http` trong LAN ở dạng rõ, chấp nhận vì người dùng chọn gateway.
- `/ai/provider/test` chạy với URL người dùng nhập, nên người đã đăng nhập gọi được tới máy nội bộ từ server (SSRF). Giảm bằng: chỉ phiên web, không theo redirect, không trả body, chặn link-local. Xem lại trước khi mở cho nhiều người dùng.

## 7. Kiểm thử

- **URL:** nhận `http://10.0.0.5:20128/v1`, `http://192.168.1.2`, `http://gateway:8080`, `https://gateway.internal/v1`, `http://127.0.0.1:20128`, `https://localhost`, `https://openrouter.ai/api/v1`; từ chối `ftp://`, `http://u:p@host`, `http://host?x=1`, `http://host#a`, `http://2130706433`, `http://0x7f.1`, `http://169.254.169.254`, `http://[fe80::1]`, host phân giải về link-local.
- **Cảnh báo:** `public_host` đúng với IP công cộng, tên phân giải về IP công cộng, IP LAN, tên không phân giải được. Web hiện `Alert` khi `true`.
- **Khóa mặc định:** đặt `ANTHROPIC_API_KEY` nhưng chưa nhập nhà cung cấp → `/ask`, dịch tay 503 `ai_not_configured`, không có request nào; job dịch dừng.
- **Không che:** request gửi AI có text gốc của câu hỏi và nguồn, không có nhãn dạng `[PERSON_1]`; `/ask` không gọi `/ner`; prompt không còn nhắc nhãn.
- **Dịch:** chạy khi ml-service tắt; glossary khớp trên text gốc, kể cả khi thuật ngữ là tên người; `TRANSLATE_PROMPT_VERSION` mới làm bản dịch cũ hiện nhãn "cũ", job không tự chạy lại.
- **RLS:** mail của B không vào request của A; nhà cung cấp của B không dùng cho A.
- **Log:** `caplog` khẳng định không có text mail, câu hỏi, API key, URL đầy đủ.
- **Migration:** 0019 chạy trên DB test, cột `tier` không còn, dòng thiếu `model` bị xóa, RLS và quyền `app_api` đúng; downgrade được.
- **ml-service:** `/ner` trả 404, `/health` không có `ner_models`, `/embed` như cũ, app chạy khi không cài spaCy.
- **Web:** `AiSettingsPage` không còn ba cấp và thẻ che; `AskPage` không còn chữ "đã che".
- Test che cũ (`test_masking.py`, phần che trong `test_ai_api.py`, `test_translate.py`) xóa cùng code. Không xóa test RLS, test log.
- Chạy thử đầu cuối trên DB test với server tương thích OpenAI giả trên một IP loopback, cấu hình qua màn hình Cài đặt AI.

## 8. Giới hạn đã biết

- **Code không ngăn gửi mail chưa che ra ngoài.** Nhập URL công cộng thì mail ra ngoài, chỉ có cảnh báo trên màn hình.
- Chất lượng hỏi đáp và dịch phụ thuộc model nội bộ, chưa đo. Output có cấu trúc có thể phải dùng đường lùi `json_object` (spec 24).
- Chưa gọi thử gateway thật.
- DNS có thể đổi giữa lúc kiểm tra và lúc kết nối, nên chặn link-local không tuyệt đối.
- Bản dịch cũ lưu nhãn đã thay ngược, có thể dịch sai tên riêng do bị che lúc dịch. Người dùng bấm "Dịch lại" nếu cần.

## 9. Thứ tự và tài liệu

- Làm trước spec 27, trực tiếp trên `master`.
- Thứ tự: (1) URL và cảnh báo, (2) bỏ khóa mặc định và ba cấp, migration 0019, (3) bỏ che ở backend, (4) xóa NER ở ml-service, (5) web, (6) tài liệu.
- Bước (2) và (3) phải vào cùng một đợt phát hành.
- Khi xong sửa cho khớp: spec 17, 20, 21, 22, 24, 26 (dòng cập nhật ở đầu), `backend/CLAUDE.md` (cấu trúc, mục "Gọi AI"), `ml-service/CLAUDE.md`, `web/CLAUDE.md`, `CLAUDE.md` gốc (bỏ đoạn "Code chưa theo kịp", bước chạy thử 4–5), CHANGELOG, danh mục spec.
