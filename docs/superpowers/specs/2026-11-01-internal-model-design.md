# Spec: Chuyển sang model nội bộ và bỏ che thông tin

Ngày: 2026-11-01 · Thành phần: `backend/app/ai/` (`provider.py`, `client.py`, `models.py`, `settings.py`, `ask.py`, `translate.py`, `glossary.py`, `prompts.py`, mới `text.py`; xóa `masking.py`, `names.py`, `people.py`), `backend/app/auto_translate.py`, `backend/app/ml_client.py`, `backend/app/main.py`, `backend/app/worker.py`, `backend/app/api/ai_routes.py`, `backend/app/web_schemas.py`, `backend/app/db/models.py`, `ml-service/` (xóa `/ner`, `app/ner.py`, spaCy, GiNZA), `web/src/pages/AiSettingsPage.tsx`, `web/src/pages/AskPage.tsx`, `web/src/components/MessageTranslation.tsx`, migration 0019 · Trạng thái: xong (sửa sau review: mục 12; chạy thử đầu cuối trên DB test với gateway giả: mục 10; chưa gọi thử gateway thật)

## 1. Mục đích

Quyết định của người dùng (2026-11-01):

- **Không dùng API ngoài.** AI chạy trên model local nội bộ, gọi qua gateway hoạt động như 9router.
- **Không cần che thông tin**, vì mail không ra ngoài. Các ràng buộc NDA về gửi AI ra ngoài không còn cần thiết. Thay cho quyết định bắt buộc che (2026-10-19) và "che thông tin giữ nguyên dù model là nội bộ" (2026-10-31).
- **Giữ màn hình Cài đặt AI:** người dùng nhập URL gateway, API key, model.
- **Ô URL nhận mọi địa chỉ.** Người dùng chọn phương án này sau khi được nêu rủi ro, thay vì chỉ nhận địa chỉ nội bộ. Code không chặn API ngoài: "không gửi mail ra API ngoài" là quy ước do người cấu hình đảm bảo.

Trước spec này code làm ngược lại (đã sửa): `validate_base_url` từ chối gateway trong LAN, mọi text gửi AI qua `Masker`, và có đường gọi Anthropic mặc định bằng `ANTHROPIC_API_KEY`.

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
- IP dạng số hoặc hex không chuẩn (`2130706433`, `0x7f.1`): nhãn cuối toàn số hoặc bắt đầu bằng `0x` mà không phải IP hợp lệ. Dấu chấm cuối của host được bỏ trước khi xét (`http://2852039166.`, `http://169.254.169.254.`), URL lưu lại không còn dấu chấm đó.
- Ký tự điều khiển và khoảng trắng ở bất kỳ đâu trong URL (`http://host/\x01x`): xét trên chuỗi gốc, vì `urlsplit` tự bỏ `\t \r \n`.
- **Địa chỉ link-local** (`169.254.0.0/16`, `fe80::/10`), gồm dịch vụ metadata của cloud. Quyết định của người viết spec: đây không phải gateway hợp lệ, chặn để `/ai/provider/test` không chạm tới.

`ensure_public_host` đổi thành `reject_link_local(base_url)`: phân giải host, có IP link-local thì `ValueError`, không phân giải được thì bỏ qua (lỗi mạng báo ở lần gọi). `PUT /ai/provider` gọi hàm này lúc lưu, `LlmResolver` gọi lúc dựng client mới (`/ai/provider/test` đi qua `LlmResolver`); cache trúng thì không kiểm lại. Mã lỗi giữ `ai_bad_request`, thông điệp không còn nói "mạng nội bộ".

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
- Bỏ biến `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `PMM_ANTHROPIC_BASE_URL`, `PMM_AI_FALLBACKS`, `PMM_MODEL_HAIKU/SONNET/OPUS`. Code không đọc các biến này nữa, kể cả khi còn đặt trong môi trường. SDK vẫn tự đọc `ANTHROPIC_CUSTOM_HEADERS` trong hàm dựng và không có tham số tắt: `_anthropic_llm` xóa ngay sau khi dựng (`client._custom_headers = {}`), header từ môi trường không gửi tới URL người dùng nhập.
- **`trust_env=False` là bắt buộc ở cả hai client.** `OpenAiLlm` đặt trên `httpx.AsyncClient`. `_anthropic_llm` dùng `httpx2.AsyncClient(trust_env=False, follow_redirects=False, timeout=TIMEOUT_SECONDS)`, không dùng `DefaultAsyncHttpxClient`: lớp đó luôn đọc proxy của môi trường, `trust_env=False` không tắt được.
- Bỏ tham số `custom_provider` của `ask`, `translate`, `auto_translate`.
- Chưa nhập nhà cung cấp: `/ask`, dịch tay → 503 `ai_not_configured`; job dịch dừng, phút sau thử lại, như nhánh `llm is None` hiện tại. Câu hỏi còn treo ở spec 26 (job dịch rơi về khóa môi trường) không còn.
- `create_app(llm=...)` giữ tham số cho test: truyền client giả vào `LlmResolver`, không có đường đọc môi trường, không có `app.state.llm`.

### Hết giờ chờ model

- Mã lỗi `ai_timeout` cho `ReadTimeout`: gateway nhận request nhưng không trả lời xong trong `TIMEOUT_SECONDS` = 120 giây. Áp dụng cho cả hai giao thức. Lỗi kết nối (`ConnectTimeout`, `ConnectError`, `PoolTimeout`) vẫn là `ai_unavailable`, vì gateway tắt thì dừng người dùng là đúng.
- Không thử lại: mỗi lần tốn tới 120 giây tải của gateway. `OpenAiLlm` ném lỗi ngay. SDK `anthropic` thử lại mọi `APITimeoutError` nên `_anthropic_llm` gắn middleware `no_retry_on_read_timeout`; 429, 5xx và lỗi kết nối vẫn được thử lại như cũ.
- `/ask`, dịch tay, `/ai/provider/test`: 503 (cùng status với `ai_unavailable`), thông điệp cố định "Model trả lời quá lâu, thử lại sau hoặc dùng model nhanh hơn".
- Job dịch: `ai_timeout` thuộc `UNBOUNDED_RETRY_ERRORS`, xử lý như `ai_bad_request` (spec 26 mục 5): ghi `retry`, chờ `min(attempts × 10, 60)` phút, không bao giờ `failed`, job đi tiếp mail sau. Vì job chọn mail cũ nhất trước, nếu dừng người dùng ở lỗi này thì một mail gateway không dịch xong sẽ chặn mọi mail sau nó.

### Bỏ ba cấp model

Ba cấp Haiku/Sonnet/Opus chỉ có nghĩa với Claude.

- `app/ai/models.py` chỉ còn `TASKS`. Bỏ `TIER_INFO`, `Tier`, `model_for`, `tier_of_model`, bảng giá.
- `call_task` nhận `model: str` (tên người dùng nhập), bỏ `tier`, `custom_model`. Thiếu tên model cho tác vụ xử lý như nhánh hiện có của spec 24, 26.
- Migration 0019 (`down_revision = "0018"`): xóa dòng `user_ai_settings` có `model IS NULL`, `DROP COLUMN tier` (kèm `ck_user_ai_settings_tier` và quyền cột), đặt `model NOT NULL`, `GRANT DELETE ON user_ai_settings TO app_api` (xóa tên model là xóa dòng). Đổi `models.py` cho khớp. Downgrade thêm lại cột `tier` nullable, không khôi phục dữ liệu. Quyết định của người viết spec.
- `/ai/usage` bỏ `estimated_usd`, `total_estimated_usd`. Thống kê token giữ nguyên.

## 5. Bỏ che thông tin

### Backend

- Xóa `app/ai/masking.py`, `app/ai/names.py`, `app/ai/people.py`, `build_masker`, `learn_rows`, `_participants`, `_mask_ranges`, `WINDOW_CHARS`.
- `/ask`: câu hỏi và nguồn gửi thẳng, kết quả AI dùng trực tiếp, không còn `unmask`. `plan.sender`, `search_queries`, `keywords` dùng nguyên giá trị.
- Giữ mọi giới hạn độ dài: subject 300 ký tự, To/Cc 10 người, tên và địa chỉ 100 ký tự (`MAX_ADDRESSES`, `MAX_NAME_CHARS`, `MAX_SUBJECT_CHARS`). Thân mail trong `/ask` là đoạn embed, mỗi đoạn tối đa 800 ký tự (`CHUNK_CHARS`); thân mail dịch `MAX_BODY_CHARS` = 12.000, ngữ cảnh `MAX_CONTEXT_CHARS` = 1.500. Đây là giới hạn chi phí xử lý, không phụ thuộc việc che. (Bản đầu của spec ghi nhầm giới hạn thân mail là `SOURCE_LIMITS`; hằng đó chỉ giữ From, To, Cc, Subject.)
- `cut_masked` đổi thành `cut_text(text, limit) -> tuple[str, bool]` trong `app/ai/text.py`: cắt ở ranh giới đoạn, rồi cuối dòng, trong nửa sau giới hạn; không có thì cắt tại giới hạn.
- Dịch: bỏ `_mask_mail`, `_masked_glossary`, `MAX_MASK_INPUT_CHARS`, lỗi `mask_unstable`. Glossary khớp trên text gốc đã cắt. Dịch **không còn gọi ml-service**: bỏ tham số `ml` của `translate`, `auto_translate`, và nhánh chờ `ml_unavailable` của job.
- `check_translation`: bỏ `labels_missing`, `labels_unknown`. Giữ `glossary_missing`, `glossary_conflict`, `truncated`.
- `nfkc_map` chuyển từ `masking.py` sang `glossary.py`. Bỏ `LABEL_RE` và nhánh bỏ qua nhãn trong `match_glossary`.
- `ml_client.py`: bỏ `ner`, `NerEntity`, `NER_LABELS`.
- `/ask` vẫn cần ml-service để embed câu hỏi. Không chạy thì 503 `ml_unavailable` như hiện tại.

### Prompt

- `ANALYSIS_SYSTEM`, `ANSWER_SYSTEM`, prompt dịch: bỏ mọi quy tắc về nhãn `[PERSON_1]`.
- `TRANSLATE_PROMPT_VERSION` lên 2: bản dịch cũ hiện nhãn "cũ", chỉ gọi AI lại khi người dùng bấm "Dịch lại" (spec 26 mục 10). Bản 2 còn thêm quy tắc tên riêng (mục 11), glossary được ưu tiên hơn quy tắc này.

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
- `/ai/provider/test` chạy với URL người dùng nhập, nên người đã đăng nhập gọi được tới máy nội bộ từ server (SSRF). Giảm bằng: chỉ phiên web, không theo redirect, không trả body, chặn link-local. `reject_link_local` chạy khi lưu và khi dựng client mới; cache trúng thì không kiểm lại. Xem lại trước khi mở cho nhiều người dùng.

## 7. Kiểm thử

- **URL:** nhận `http://10.0.0.5:20128/v1`, `http://192.168.1.2`, `http://gateway:8080`, `https://gateway.internal/v1`, `http://127.0.0.1:20128`, `https://localhost`, `https://openrouter.ai/api/v1`; từ chối `ftp://`, `http://u:p@host`, `http://host?x=1`, `http://host#a`, `http://2130706433`, `http://0x7f.1`, `http://169.254.169.254`, `http://[fe80::1]`, host phân giải về link-local, các dạng trên có dấu chấm cuối, ký tự điều khiển và khoảng trắng trong host hoặc path.
- **Môi trường:** đặt `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`: client hai giao thức không có transport proxy, request tới đúng URL đã lưu. Đặt `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`: request của `_anthropic_llm` chỉ có key và URL người dùng, không có header môi trường.
- **Hết giờ:** `ReadTimeout` thành `ai_timeout`, đúng một request ở cả hai giao thức, kể cả khi hết giờ giữa lúc đọc body; lỗi kết nối vẫn `ai_unavailable` và vẫn thử lại; job ghi `retry` rồi dịch được mail sau; `ConnectTimeout` vẫn dừng người dùng; job tự đẩy tiếp sau `ai_timeout`, không tự đẩy sau `ai_unavailable`.
- **Phản hồi lạ của gateway:** `usage` sai dạng thành `ai_bad_output`, chuỗi của gateway không có trong response, log; tên model có xuống dòng, ký tự điều khiển hoặc quá 200 ký tự thì dùng tên đã yêu cầu.
- **Cảnh báo:** `public_host` đúng với IP công cộng, tên phân giải về IP công cộng, IP LAN, tên không phân giải được. Web hiện `Alert` khi `true`.
- **Khóa mặc định:** đặt `ANTHROPIC_API_KEY` nhưng chưa nhập nhà cung cấp → `/ask`, dịch tay 503 `ai_not_configured`, không có request nào; job dịch dừng.
- **Không che:** request gửi AI có text gốc của câu hỏi và nguồn, không có nhãn dạng `[PERSON_1]`; `/ask` không gọi `/ner`; prompt không còn nhắc nhãn.
- **Dịch:** chạy khi ml-service tắt; glossary khớp trên text gốc, kể cả khi thuật ngữ là tên người; `TRANSLATE_PROMPT_VERSION` mới làm bản dịch cũ hiện nhãn "cũ", job không tự chạy lại.
- **RLS:** mail của B không vào request của A; nhà cung cấp của B không dùng cho A. Một `LlmResolver()` trên DB test thật: A có nhà cung cấp, B không thì B nhận `None`; hai URL khác nhau thì client của mỗi người tới đúng URL và key của mình; sửa hoặc xóa nhà cung cấp mà không gọi `forget` (đường worker) thì lần sau nhận client mới hoặc `None`. `load_provider`, `delete_provider`, `user_models` chạy đúng cả trên role không chịu RLS.
- **Log:** `caplog` ở mức DEBUG, đi đủ hai lượt gọi AI qua `OpenAiLlm` thật (`/ask`) và một lượt (dịch tay), khẳng định không có text mail, câu hỏi, API key, URL đầy đủ.
- **Migration:** 0019 chạy trên DB test, cột `tier` không còn, dòng thiếu `model` bị xóa, RLS và quyền `app_api` đúng; downgrade được.
- **ml-service:** `/ner` trả 404, `/health` không có `ner_models`, `/embed` như cũ, app chạy khi không cài spaCy.
- **Web:** `AiSettingsPage` không còn ba cấp và thẻ che; `AskPage` không còn chữ "đã che".
- Test che cũ (`test_masking.py`, phần che trong `test_ai_api.py`, `test_translate.py`) xóa cùng code. Không xóa test RLS, test log.
- Chạy thử đầu cuối trên DB test với server tương thích OpenAI giả trên một IP loopback (đã làm, kết quả ở mục 10). Lưu nhà cung cấp và tên model gọi qua API của màn hình Cài đặt AI, không bấm trên giao diện.

## 8. Giới hạn đã biết

- **Code không ngăn gửi mail chưa che ra ngoài.** Nhập URL công cộng thì mail ra ngoài, chỉ có cảnh báo trên màn hình.
- Chất lượng hỏi đáp và dịch phụ thuộc model nội bộ, chưa đo. Output có cấu trúc có thể phải dùng đường lùi `json_object` (spec 24).
- **Hết giờ 120 giây, không streaming.** Timeout đọc phản hồi là 120 giây và request không dùng streaming, nên model chậm có thể không xong mail dài (`MAX_BODY_CHARS` = 12.000); mail đó ghi `ai_timeout`, thử lại mỗi giờ mãi. Chưa đo trên gateway thật. `attempts` dùng chung với lỗi theo mail (spec 26 mục 5): sau vài lần `ai_timeout`, một `ai_bad_output` có thể thành `failed` ngay.
- **Gateway `https` với CA riêng.** `trust_env=False` tắt cả `SSL_CERT_FILE`, `SSL_CERT_DIR` (`create_ssl_context` của `httpx` và `httpx2` chỉ đọc hai biến này khi `trust_env` bật; đã kiểm bằng file CA không tồn tại). Giao thức `openai` (`httpx`) chỉ tin bộ CA `certifi` kèm theo, không đọc kho chứng chỉ của Windows, nên gateway dùng CA riêng báo `ai_unavailable`. Giao thức `anthropic` (`httpx2`) dùng `truststore`, tin kho chứng chỉ của hệ điều hành. Trong LAN dùng `http`, hoặc CA công cộng. Chưa có cấu hình CA riêng.
- Metadata cloud ngoài dải link-local không bị chặn: `fd00:ec2::254`, `100.100.100.200`, IPv6 nhúng IPv4 ngoài dạng `::ffff:`. Xem lại trước khi đặt server trên cloud (nơi đặt server chờ người dùng).
- Cửa sổ DNS rebinding kéo dài theo vòng đời client: `reject_link_local` chỉ chạy khi dựng client, client sống tới khi cấu hình đổi hoặc process dừng.
- Lưu URL công cộng thì worker có thể gửi mail đi trong vòng một phút (`translate_sweep`), trước khi người dùng thấy cảnh báo trên màn hình.
- Thông điệp lỗi của `/ai/provider/test` (tên lớp lỗi, mã HTTP) đủ để người đã đăng nhập dò cổng nội bộ của server.
- Chưa gọi thử gateway thật.
- DNS có thể đổi giữa lúc kiểm tra và lúc kết nối, nên chặn link-local không tuyệt đối.
- Bản dịch cũ lưu nhãn đã thay ngược, có thể dịch sai tên riêng do bị che lúc dịch. Người dùng bấm "Dịch lại" nếu cần.
- Dòng `message_auto_runs` đã `failed` với mã `mask_unstable` (sinh trước spec này) giữ nguyên `failed`: code không còn sinh mã này, chỉ nút Dịch tay hoặc "Thử lại" (`auto-retry`, xóa dòng `failed` để lần quét sau làm lại) xử lý được.
- Cảnh báo địa chỉ công cộng (`public_host`) theo URL **đã lưu**, không theo URL đang gõ: nhập URL công cộng, chưa bấm lưu thì chưa thấy cảnh báo.
- Trang Cài đặt AI không báo tác vụ thiếu tên model. Sau migration 0019 các dòng chỉ có cấp bị xóa, nên người dùng thấy lỗi `ai_model_missing` khi dùng hỏi đáp hoặc dịch rồi mới nhập ở trang này.
- Chưa đo chất lượng và tốc độ trả lời của model nội bộ; chưa kiểm khả năng output có cấu trúc của gateway thật.

## 9. Thứ tự và tài liệu

- Làm trước spec 27, trực tiếp trên `master`.
- Thứ tự: (1) URL và cảnh báo, (2) bỏ khóa mặc định và ba cấp, migration 0019, (3) bỏ che ở backend, (4) xóa NER ở ml-service, (5) web, (6) tài liệu.
- Bước (2) và (3) phải vào cùng một đợt phát hành.
- Khi xong sửa cho khớp: spec 17, 20, 21, 22, 24, 26 (dòng cập nhật ở đầu), `backend/CLAUDE.md` (cấu trúc, mục "Gọi AI"), `ml-service/CLAUDE.md`, `web/CLAUDE.md`, `CLAUDE.md` gốc (bỏ đoạn "Code chưa theo kịp", bước chạy thử 4–5), CHANGELOG, danh mục spec. Đã làm 2026-11-02.

## 10. Kết quả đo và chạy thử (2026-11-02)

### ml-service sau khi gỡ NER

Máy: i5-8500 (6 nhân, 3,0 GHz), 15,8 GB RAM, không GPU, Windows 11. (`ml-service/CLAUDE.md` cũ ghi i5-1235U, 7,7 GB RAM: số đo cũ, có thể máy khác.) Chạy cổng 8002 song song với ml-service của người dùng (`uv run --no-sync`, cache model đã có). Venv lúc đo vẫn còn spaCy cài sẵn (người dùng đang giữ file nên chưa `uv sync` được), nhưng không có code nào import.

- Khởi động tới khi `/health` trả lời: khoảng 39 giây (nạp model 37,9 giây).
- Bộ nhớ private: khoảng 3,2 GB sau khi nạp, 3.250 MB sau `/embed` 5 câu ngắn (lần đầu 4,3 giây, lần hai 0,3 giây). Số cũ khoảng 4 GB (kèm NER) đo trên máy khác (i5-1235U, 7,7 GB RAM, spec 17 mục 8): hai số đo trên hai máy, không so sánh được, chưa biết gỡ NER bớt bao nhiêu.
- `POST /ner` trả 404. Tốc độ embed theo đoạn 800 ký tự không đo lại (số cũ 0,3 đoạn/giây trên máy cũ).

### Chạy thử đầu cuối trên DB test

`mailassist_test` (API, role `app_api`, qua `DATABASE_URL_*`), API riêng cổng 8020, ml-service cổng 8002, server tương thích OpenAI giả bind `127.0.0.2:20128` ghi lại mọi request, `PMM_SECRET_KEY` sinh tạm, `ANTHROPIC_API_KEY` giả đặt trong môi trường API. Dữ liệu: một người dùng và hai mail giả (`*.example.*`, tên người, số tiền, số điện thoại giả) dựng bằng helper của test, đã làm sạch và embed bằng ml-service thật. Kết quả:

- Chưa nhập nhà cung cấp (khóa Anthropic giả có trong môi trường): `/ask` → 503 `ai_not_configured`, gateway giả không nhận request nào.
- Lưu nhà cung cấp giao thức `openai` và ba tên model qua `PUT /ai/provider`, `PUT /ai/settings`; `POST /ai/provider/test` thành công với tên model của tác vụ trả lời.
- `/ask`: đúng hai request (phân tích, trả lời), `model` đúng tên đã nhập cho từng tác vụ; câu hỏi nằm nguyên văn trong cả hai request; request trả lời chứa nguyên văn tên người, số tiền, số điện thoại của mail nguồn; không có nhãn dạng `[PERSON_n]`; header `Authorization` đúng key đã nhập; response không còn `mask_counts`.
- `POST /messages/{id}/translate`: một request, `model` đúng tên tác vụ dịch, subject và thân mail gốc, không có nhãn che; system prompt không còn nhắc nhãn. ml-service không tham gia.
- Xóa nhà cung cấp rồi `/ask` và dịch tay → 503 `ai_not_configured`, gateway giả không nhận thêm request nào (khóa Anthropic giả vẫn đặt).
- Log của API không có text mail, câu hỏi, key; chỉ có host, giao thức, tên model, token, thời gian.
- Đã dừng các tiến trình tự khởi động và xóa người dùng thử khỏi `mailassist_test`.

## 11. Quyết định của người viết (làm trong lúc code, người dùng có thể đổi)

- **Quy tắc tên người trong `TRANSLATE_SYSTEM`** (`prompts.py`): "Tên người, tên công ty: giữ nguyên hoặc phiên âm, không dịch nghĩa, trừ khi có trong <glossary>. Viết 様 thành -sama, さん thành -san." Spec 28 mục 5 chỉ nói bỏ quy tắc nhãn; quy tắc này thêm vì trước đây nhãn che giữ nguyên tên và giờ model thấy tên thật, có thể dịch nghĩa tên. Vế "trừ khi có trong <glossary>" thêm sau review để glossary không thua quy tắc này (giữ `TRANSLATE_PROMPT_VERSION` = 2 vì chưa phát hành).
- **Kiểm tên model trong `ask()`**: ngoài route, `ask()` tự trả 503 `ai_model_missing` khi thiếu tên model của tác vụ phân tích hoặc trả lời (cùng cách `produce_translation` làm cho job dịch), để không gọi AI với model rỗng.
- **Cột đọc mail dịch** (`_MAIL_COLUMNS` trong `translate.py`): chỉ còn `id, thread_id, subject, body_clean, sent_at`, bỏ `from_name`, `from_addr`, `to_addrs`, `cc_addrs`, `body_text` vì chỉ phục vụ dựng từ điển che.
- **`AiProvider.configured_env` bỏ** (spec mục 5 đã nêu, kế hoạch sót): trường này đọc `configured()` đã xóa cùng khóa mặc định.

## 12. Sửa sau review (2026-11-02)

Review toàn nhánh và security-reviewer. Kiểm tra cuối: backend 900 test, ml-service 13 test, web 109 test, ruff, pyright, ESLint, Prettier, `tsc`, build web sạch. Schema API không đổi, không sinh lại `web/openapi.json`.

- **Client AI đi theo proxy của môi trường** (Critical): có `HTTP_PROXY` hoặc proxy hệ thống Windows thì mail chưa che và API key đi qua proxy, kể cả địa chỉ LAN. Đặt `trust_env=False` ở `OpenAiLlm`; `_anthropic_llm` đổi sang `httpx2.AsyncClient`, vì `DefaultAsyncHttpxClient(trust_env=False)` vẫn gắn proxy (mục 4). `httpx2` chuyển từ nhóm dev sang dependency chính. Tác dụng phụ với CA riêng: mục 8.
- **SDK vẫn đọc `ANTHROPIC_CUSTOM_HEADERS`**: xóa sau khi dựng client (mục 4), có test thay cho `test_client_is_required`.
- **Một mail gateway không dịch xong chặn cả hàng dịch**: thêm `ai_timeout` (mục 4). Không thử lại ở cả hai giao thức, cả với SDK `anthropic` nhờ middleware.
- **Test log không tới bước trả lời**: `test_ask_log_has_no_question` đi đủ hai lượt gọi AI qua `OpenAiLlm` thật ở mức DEBUG; thêm test tương tự cho dịch tay kiểm key và URL đầy đủ.
- **Thiếu test cô lập nhà cung cấp**: thêm bốn test dùng một `LlmResolver()` trên DB test (mục 7).
- **Test env không bảo vệ `/ask`**: `test_env_key_ignored_without_provider` dựng app sau khi đặt biến môi trường.
- **Quy tắc tên riêng không ưu tiên glossary**: thêm "trừ khi có trong <glossary>", giữ `TRANSLATE_PROMPT_VERSION` = 2.
- **`create_app` lệch spec**: sửa docstring, xóa `app.state.llm`.
- **Thông điệp gateway vào traceback**: đọc `usage` trong `try`, sai dạng thành `ai_bad_output`; `/ai/provider/test` bắt `Exception`, chỉ log tên lớp, trả 500 `provider_test_failed`.
- **`validate_base_url` lọt hai dạng**: dấu chấm cuối và ký tự điều khiển, khoảng trắng (mục 3).
- **Giá trị `model` do gateway trả không giới hạn**: `served_model` ở `OpenAiLlm._parse` và `AnthropicLlm.create`, nên `call_task`, `record_call`, bản dịch đã lưu và `/ai/provider/test` đều nhận giá trị đã kiểm. Chỉ nhận chuỗi in được, tối đa 200 ký tự, sai thì dùng tên đã yêu cầu.
- **Truy vấn dựa hoàn toàn vào RLS**: `load_provider`, `delete_provider`, `user_models(session, user_id)` tự lọc `user_id`. Không đổi hành vi với `app_api`. `DELETE_PROVIDER` không có trong báo cáo review, sửa cùng lý do.
- **Chữ trên web sai khi URL công cộng**: trang Hỏi đáp ghi "gửi tới nhà cung cấp AI đã cấu hình", không khẳng định nội bộ. `MessageTranslation` thêm chữ cho `ai_timeout`.
