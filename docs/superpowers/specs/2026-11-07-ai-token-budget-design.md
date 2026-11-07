# Spec: Ngân sách token và thời gian chờ theo tác vụ, chia đoạn mail dài, giới hạn glossary cấu hình được

Ngày: 2026-11-07 · Thành phần (dự kiến): `backend/app/ai/budget.py` (mới), `backend/app/ai/models.py`, `backend/app/ai/settings.py`, `backend/app/ai/client.py`, `backend/app/ai/provider.py`, `backend/app/ai/translate.py`, `backend/app/ai/ask.py`, `backend/app/ai/glossary.py`, `backend/app/auto_translate.py`, `backend/app/api/ai_routes.py`, `backend/app/api/glossary_routes.py`, `backend/app/web_schemas.py`, `backend/app/db/models.py`, `backend/app/review.py`, migration 0022 (dự kiến, sau `0021_openai_protocol_only`), `web/src/pages/AiSettingsPage.tsx`, `web/src/components/GlossaryTable.tsx`, `web/src/components/MessageTranslation.tsx` · Trạng thái: **đang thiết kế, chưa có code**. Mục 4 (cài đặt) người dùng đã duyệt 2026-11-07; mục 5–10 là đề xuất của người viết, chờ duyệt; câu hỏi còn mở ở mục 15.

Spec đi kèm: [spec 30](2026-11-07-glossary-kinds-design.md) (glossary theo loại, giữ tên tiếng Nhật), làm sau spec này.

## 1. Mục đích

Dịch mail và hỏi đáp phải chạy được với model nội bộ có độ dài ngữ cảnh bất kỳ, và không mất nội dung mail dài. Hiện code có ba vấn đề, phát hiện khi rà luồng dịch ngày 2026-11-07:

1. **`max_tokens` cố định** theo tác vụ (`TASKS` trong `app/ai/models.py`: phân tích 2.000, trả lời 8.000, dịch 16.000), gửi kèm mọi request. vLLM từ chối request khi số token vào cộng `max_tokens` vượt độ dài ngữ cảnh của model (`max_model_len`). Với model chạy ngữ cảnh 16.384 token, **mọi** mail dịch đều lỗi 400 (`ai_bad_request`, job thử lại mỗi giờ mãi), kể cả mail ngắn nhất (1.275 + 16.000). Ollama mặc định ngữ cảnh nhỏ (vài nghìn token, tùy phiên bản) và cắt bớt prompt mà không báo lỗi.
2. **Cắt theo số ký tự cố định, không theo model:** thân mail dịch 12.000 ký tự (`MAX_BODY_CHARS`), phần sau bị bỏ hẳn (cảnh báo `truncated`); ngữ cảnh 1.500 ký tự; tối đa 60 dòng glossary trong prompt. Khối glossary không bị giới hạn theo độ dài: 60 mục có ghi chú dài tối đa (500 ký tự) đẩy prompt lên khoảng 43.000 token (phụ lục A).
3. **Thời gian chờ cố định 120 giây** mỗi lần gọi (`TIMEOUT_SECONDS`, `app/ai/client.py`). Model 14B–32B trên một GPU thường sinh khoảng 20–60 token/giây (tùy phần cứng, chưa đo trên gateway thật), nên 120 giây chỉ đủ cho khoảng 2.400–7.200 token bản dịch. Một mail 12.000 ký tự cần khoảng 7.500 token ra.

Số đo token và hiệu năng khớp glossary: phụ lục A, B.

## 2. Quyết định

### Của người dùng (2026-11-07)

- **Một con số "tổng ngữ cảnh" (token vào + ra) cho mỗi tác vụ**, nhập ở Cài đặt AI cạnh tên model. Code tự chia ngân sách cho prompt và cho câu trả lời. Lý do: chỉ cần hỏi người quản trị gateway một con số (`max_model_len` của vLLM, `num_ctx` của Ollama). Model người dùng đang nhắm tới có ngữ cảnh khoảng 100.000 token.
- **Áp dụng cho mọi tác vụ** (phân tích câu hỏi, trả lời, dịch mail), mỗi dòng một ô riêng: hỏi đáp cũng gặp lỗi `max_tokens` (8.000 + tới khoảng 15.000 token ngữ cảnh).
- **Mail dài thì chia đoạn rồi ghép**, không bỏ phần sau. Nguyên tắc "mỗi bản mail chỉ gọi AI một lần" (`CLAUDE.md` gốc) đổi thành "mỗi bản mail chỉ **dịch** một lần": một mail dài có thể tốn nhiều lần gọi, mỗi đoạn một lần, kết quả lưu thành một bản dịch. Đây là câu trả lời cho câu hỏi "có nới nguyên tắc này không" đang chờ từ 2026-11-01.
- **Không cắt theo giới hạn ký tự cố định trong code.** Chỉ chia hoặc cắt khi vượt ngân sách token đã nhập ở Cài đặt AI.
- **Thêm ô "Thời gian chờ (giây)" vào từng dòng tác vụ** (chọn trong ba cách: ô cài đặt, tự điều chỉnh theo tốc độ đo được, chuyển sang stream). Lý do: đơn giản, mọi con số nằm ở Cài đặt AI.
- **Giới hạn glossary cấu hình được, tối đa 50.000 mục mỗi phạm vi**, nhập ở Cài đặt AI, cạnh dòng "Dịch mail". Lý do: glossary cần chứa tên màn hình, tên bảng, tên cột nhập từ tài liệu thiết kế (dự án 200 bảng × 20 cột đã có 4.000 tên cột).
- Đếm token bằng **ước lượng theo ký tự** (mục 5), không gói tokenizer, không gọi `/tokenize` của gateway (người dùng không phản đối đề xuất).
- Thiết kế mục 4 (dữ liệu, API, màn hình) đã duyệt.

### Của người viết spec (người dùng có thể đổi)

- Khoảng giá trị và mặc định: ngữ cảnh 4.096–131.072 (mặc định 32.768), thời gian chờ 30–3.600 giây (mặc định 120), giới hạn glossary 100–50.000 (mặc định 5.000).
- Giới hạn glossary để ở bảng riêng `user_glossary_settings`, không ở dòng "Dịch mail" của `user_ai_settings`: dòng đó bị xóa khi xóa tên model dịch.
- Các hệ số của mục 5, 6 (dự phòng 10%, phần ngữ cảnh 10%, phần glossary 15%, hệ số đầu ra).
- Hỏi đáp giữ các giới hạn ký tự của spec 27 (`CONTEXT_MAX_CHARS` 24.000, `SOURCE_MAX_CHARS` 3.000, `PREVIOUS_MAX_CHARS` 1.200) làm **mức trần chất lượng truy xuất**, không phải giới hạn của model; chỉ bớt nguồn khi ngân sách nhỏ hơn (mục 7). Mở rộng ngữ cảnh khi ngân sách lớn là câu hỏi mở (mục 15).
- Prompt dịch giữ nguyên chữ (system và cấu trúc tin nhắn), nên `TRANSLATE_PROMPT_VERSION` giữ 2 và bản dịch đã lưu không bị đánh dấu "cũ".

## 3. Không làm

- Glossary theo loại, thay trực tiếp, giữ tên tiếng Nhật, kiểm tra URL và chữ Nhật sót: [spec 30](2026-11-07-glossary-kinds-design.md).
- Đếm token chính xác bằng tokenizer của model; tự hiệu chỉnh hệ số theo `usage` gateway trả về.
- Stream phản hồi; tự điều chỉnh thời gian chờ theo tốc độ model.
- Mở rộng ngữ cảnh hỏi đáp quá các giới hạn của spec 27 khi ngân sách lớn.
- Chuyển nút Dịch tay sang job nền (câu hỏi mở, mục 15).
- Cài đặt dùng chung giữa người dùng: Cài đặt AI vẫn theo từng người.

## 4. Cài đặt (đã duyệt 2026-11-07)

### Dữ liệu (migration 0022)

`user_ai_settings` (mỗi dòng là một tác vụ có tên model; xóa tên model = xóa dòng) thêm:

| Cột | Kiểu | Ràng buộc |
|---|---|---|
| `context_tokens` | `integer NOT NULL DEFAULT 32768` | `CHECK (context_tokens BETWEEN 4096 AND 131072)` |
| `timeout_seconds` | `integer NOT NULL DEFAULT 120` | `CHECK (timeout_seconds BETWEEN 30 AND 3600)` |

Quyền: cấp thêm cho `app_api` quyền `INSERT`, `UPDATE` trên hai cột mới, theo đúng mẫu quyền theo cột hiện có của bảng (xem migration 0015, 0019). `app_worker` giữ nguyên quyền hiện có (job dịch đọc cài đặt qua `api_engine`, spec 26 mục 5).

Bảng mới `user_glossary_settings`:

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | `bigint` | PK, FK `users` `ON DELETE CASCADE` |
| `max_terms_per_scope` | `integer NOT NULL` | `CHECK (max_terms_per_scope BETWEEN 100 AND 50000)` |
| `updated_at` | `timestamptz NOT NULL DEFAULT now()` | |

RLS theo `user_id` (`app_user_id()`), `app_api` có `SELECT`, `INSERT (user_id, max_terms_per_scope)`, `UPDATE (max_terms_per_scope, updated_at)`. `app_worker` không có quyền (chỉ API thêm và nhập glossary). Không có dòng = 5.000. Không chứa nội dung mail.

Hằng số (một chỗ, ví dụ `app/ai/models.py`): `CONTEXT_TOKENS_MIN/MAX/DEFAULT` = 4.096 / 131.072 / 32.768; `TIMEOUT_SECONDS_MIN/MAX/DEFAULT` = 30 / 3.600 / 120; `GLOSSARY_MAX_TERMS_MIN/MAX/DEFAULT` = 100 / 50.000 / 5.000.

### API (hợp đồng với web thay đổi; collector không bị ảnh hưởng)

- `GET /api/v1/ai/settings`: `AiTaskInfo` thêm `context_tokens: int`, `timeout_seconds: int` (tác vụ chưa có dòng thì trả mặc định); `AiSettings` thêm `glossary_max_terms: int`.
- `PUT /api/v1/ai/settings`: `AiSettingsUpdate` giữ `models` như cũ, thêm:
  - `limits: dict[str, AiTaskLimits] = {}`, `AiTaskLimits` (`StrictModel`) gồm `context_tokens: int`, `timeout_seconds: int`, đều bắt buộc.
  - `glossary_max_terms: int | None = None` (`None` = không đổi).
  - Ngoài khoảng → 422 qua `validation_error_response` với `loc` chỉ đúng ô; tác vụ không có trong `TASKS` → 422 `unknown_task` như `models`.
  - Trong cùng transaction: ghi `models` trước (dòng mới nhận mặc định), rồi `UPDATE` `limits` cho các dòng đang tồn tại. Tác vụ không có tên model (chưa nhập, hoặc bị xóa trong cùng request) thì `limits` của nó bị bỏ qua.
  - `glossary_max_terms` ghi bằng upsert vào `user_glossary_settings`.
- Hạ giới hạn glossary xuống dưới số mục đang có: mục cũ giữ nguyên, thêm và nhập bị chặn (409 `glossary_limit`).

### Màn hình Cài đặt AI

```
Tác vụ               Model           Ngữ cảnh (token)   Chờ tối đa (giây)
Phân tích câu hỏi    [qwen3-14b   ]  [ 100000 ]         [ 120 ]
Trả lời câu hỏi      [qwen3-32b   ]  [ 100000 ]         [ 300 ]
Dịch mail            [qwen3-32b   ]  [ 100000 ]         [ 600 ]
   Glossary tối đa mỗi phạm vi: [ 50000 ]
```

- Ô số khóa khi dòng chưa có tên model. Kiểm tra khoảng giá trị ở web giống backend.
- Ô glossary không phụ thuộc tên model.
- Dòng gợi ý dưới bảng: "Ngữ cảnh: hỏi người quản trị gateway, là `max_model_len` của vLLM hoặc `num_ctx` của Ollama."

## 5. Ước lượng token (phương pháp đã chọn; hệ số chờ duyệt)

Module thuần mới `app/ai/budget.py`.

- `estimate_tokens(text) = ceil(Σ trọng số từng ký tự)`:
  - **1,0** cho ký tự Hán (U+3400–9FFF, U+F900–FAFF, U+20000–2FFFF), kana (U+3040–30FF, U+31F0–31FF), katakana nửa độ rộng (U+FF61–FF9F), dạng toàn độ rộng (U+FF00–FF60, U+FFE0–FFEF), dấu câu CJK (U+3000–303F).
  - **0,4** cho mọi ký tự khác (Latin, tiếng Việt, số, khoảng trắng, xuống dòng).
  - Cơ sở: đo bằng tokenizer Qwen3 được 0,60 token/ký tự tiếng Nhật và 0,31 token/ký tự tiếng Việt (phụ lục A). Hệ số trên ước lượng dư khoảng 1,7 lần với tiếng Nhật, 1,3 lần với tiếng Việt, để không vượt ngữ cảnh với tokenizer khác (Gemma chưa đo).
- Mỗi request cộng thêm `TEMPLATE_OVERHEAD` = 50 token (khuôn chat của role system/user) và `estimate_tokens` của JSON schema đầu ra (gateway lùi về `json_object` thì schema được chép vào system prompt; luôn tính để an toàn).
- **Ngân sách dùng được** `B = floor(context_tokens × 0,9)` (`RESERVE_RATIO` = 0,10).
- `max_tokens` gửi gateway = `B − ước lượng token vào`. Vì ước lượng vào luôn lớn hơn thực tế, tổng thực tế không vượt `context_tokens`.
- **Cắt theo token:** khi cần cắt một phần (subject, ngữ cảnh, nguồn hỏi đáp) cho vừa N token, gọi `cut_text` (`app/ai/text.py`) với giới hạn N ký tự. Mỗi ký tự được ước lượng tối đa 1 token, nên phần cắt ra luôn vừa.

## 6. Dịch mail: ngân sách và chia đoạn (đề xuất, chờ duyệt)

### Bỏ các giới hạn cố định

Bỏ khỏi luồng dịch: `MAX_BODY_CHARS` (12.000), `MAX_CONTEXT_CHARS` (1.500), việc cắt subject theo `MAX_SUBJECT_CHARS` (300), `MAX_GLOSSARY_IN_PROMPT` (60), `TASKS["translate"].max_tokens` (16.000), và việc sinh cảnh báo `truncated`. Web vẫn hiển thị nhãn `truncated` cho bản dịch cũ đã lưu.

### Chia ngân sách cho một mail

1. `fixed` = ước lượng của system prompt + schema + `TEMPLATE_OVERHEAD` + các thẻ `<context>…</mail>` + subject.
2. Subject chiếm quá 10% `B` (hiếm, chỉ khi subject cực dài) thì cắt subject bằng `cut_text` cho vừa 10% `B`, cảnh báo mới `subject_cut`.
3. Phần dành cho ngữ cảnh: tối đa `CONTEXT_SHARE` = 10% `B`. Phần dành cho khối glossary: tối đa `GLOSSARY_SHARE` = 15% `B`.
4. Đầu ra mỗi đoạn ước lượng = `OUTPUT_RATIO` (1,0) × ước lượng thân đoạn + `OUTPUT_EXTRA` (500 token cho subject, notes, ký tự JSON). Cơ sở: bản dịch tiếng Việt tốn xấp xỉ số token của bản gốc tiếng Nhật (phụ lục A), mà ước lượng đầu vào đã dư 1,7 lần.
5. Sức chứa thân mỗi đoạn: `cap = floor((B − fixed − CONTEXT_SHARE·B − GLOSSARY_SHARE·B − OUTPUT_EXTRA) / (1 + OUTPUT_RATIO))`, tính với phần ngữ cảnh và glossary đầy đủ nên mọi đoạn đều vừa. `cap` dưới `MIN_PART_TOKENS` (200) → lỗi `ai_context_too_small`.

Ví dụ sức chứa (ước lượng 1 token/ký tự tiếng Nhật, `fixed` ≈ 500):

| `context_tokens` | `B` | Thân mỗi đoạn |
|---|---|---|
| 4.096 | 3.686 | khoảng 880 ký tự tiếng Nhật |
| 32.768 | 29.491 | khoảng 10.500 ký tự |
| 100.000 | 90.000 | khoảng 33.000 ký tự |

### Chia đoạn

- Thân mail vừa `cap` thì một đoạn: hành vi như hiện nay, chỉ khác là không còn cắt cố định và `max_tokens`, thời gian chờ lấy từ cài đặt.
- Dài hơn: gom tham lam từng khối ngăn bởi `\n\n` cho tới `cap`; khối đơn lẻ vượt `cap` thì chia theo `\n`; một dòng vượt `cap` thì chia cứng theo ký tự tại điểm ước lượng chạm `cap`. **Không mất ký tự nào:** nối các đoạn cùng dấu ngăn cách ở ranh giới thì ra đúng `body_clean`.
- Mail chỉ có subject (thân rỗng): một đoạn với thân rỗng, như hiện nay.

### Dịch từng đoạn (tuần tự)

Với đoạn thứ k:

- **Glossary:** khớp trên `subject + "\n" + đoạn k` (hàm `match_glossary` như cũ, bỏ trần 60). Các dòng xếp như cũ (cụm dài trước) và được thêm vào khối cho tới khi chạm `GLOSSARY_SHARE`; dòng bị bỏ thì cảnh báo mới `glossary_trimmed` và không tính vào kiểm tra `glossary_missing`.
- **Ngữ cảnh:** đoạn 1 dùng `body_clean` của mail liền trước trong thread (như cũ), giữ phần đầu, bỏ phần sau cho vừa `CONTEXT_SHARE`. Đoạn k ≥ 2 dùng **phần cuối của đoạn k−1 bản gốc** (các khối `\n\n` cuối, vừa `CONTEXT_SHARE`), vì liền mạch với đoạn đang dịch quan trọng hơn mail trước.
- **Tin nhắn:** `translate_message(context, glossary, subject, đoạn k)`, cùng system prompt `TRANSLATE_SYSTEM`. Mọi đoạn đều gửi subject để model hiểu chủ đề.
- **Gọi:** `call_task(..., max_tokens = B − ước lượng vào, timeout = timeout_seconds)`. Mỗi lần gọi ghi một dòng `ai_calls` như hiện nay.
- **Kiểm tra từng đoạn:** đoạn có nội dung mà model trả `body` rỗng → `ai_bad_output`. `check_translation` chạy trên kết quả của đoạn với các mục glossary của đoạn (đoạn 1 tính cả subject đã dịch).

### Ghép kết quả

- `subject` lấy từ đoạn 1; subject dịch ở các đoạn sau bị bỏ.
- `body` = các bản dịch nối theo dấu ngăn cách ở ranh giới (`\n\n` hoặc `\n`; chỗ chia cứng giữa dòng thì nối bằng `\n`).
- `notes`: gộp theo thứ tự, bỏ trùng, tối đa 5. `warnings`: hợp các đoạn, giữ thứ tự xuất hiện. `glossary_used`: hợp, bỏ trùng theo `(term_norm, translation)`. `model`: model của lần gọi cuối, như hiện nay.
- `source_hash` tính trên subject và `body_clean` đầy đủ như cũ, nên nhãn "Bản dịch cũ" không đổi cách tính.
- Lưu một dòng `message_translations` như cũ. Đoạn nào lỗi thì **cả mail lỗi**, không lưu bản dịch dở; các đoạn đã dịch bị bỏ. Mã lỗi và luật thử lại theo spec 26 mục 5, một lượt thử của mail tính cho cả mail.

### Thời gian

- Job: một mail nhiều đoạn chiếm queue `translate` trong suốt thời gian dịch các đoạn. Số đoạn không giới hạn (câu hỏi mở, mục 15).
- Nút Dịch tay: request chờ dịch xong mọi đoạn (`AskGuard` riêng như cũ). Mail nhiều đoạn với thời gian chờ dài có thể làm request kéo dài nhiều phút (câu hỏi mở, mục 15).
- Log: thêm `parts=%d est_in=%d` (chỉ số), không log text.

## 7. Hỏi đáp: ngân sách (đề xuất, chờ duyệt)

`ask()` đọc cài đặt của `question_analysis` và `answer` (model, `context_tokens`, `timeout_seconds`).

- **Phân tích câu hỏi:** `max_tokens = min(2.000, B − ước lượng vào)`. Dưới `MIN_OUTPUT_TOKENS` (500) → `ai_context_too_small`.
- **Trả lời:** dựng tin nhắn theo spec 27 như hiện nay. Nếu ước lượng vào + 8.000 > `B`: bỏ dần nguồn **cuối cùng** (thứ hạng thấp nhất sau `pick`), dựng lại tin nhắn, lặp tới khi vừa hoặc còn một nguồn. Còn một nguồn mà vẫn không vừa thì cắt thân nguồn đó bằng `cut_text` cho vừa. Sau đó `max_tokens = min(8.000, B − ước lượng vào)`; dưới 500 → `ai_context_too_small`.
- `AskResult.sources` và `sent` phản ánh tin nhắn cuối cùng (đánh số trích dẫn khớp). Log thêm `dropped=%d`.
- 2.000 và 8.000 là **mức đầu ra mong muốn** (giữ trong `TASKS`), không còn là giá trị gửi thẳng.

## 8. Thời gian chờ (đề xuất, chờ duyệt)

- `LlmClient.create` thêm tham số `timeout: float`. `OpenAiLlm` truyền thời gian chờ theo từng request (httpx nhận `timeout` cho từng lần gửi), thay cho giá trị đặt cố định lúc dựng client. Client cache theo người dùng trong `LlmResolver` không phải dựng lại khi đổi cài đặt.
- `call_task` nhận `timeout_seconds` từ cài đặt của tác vụ và chuyển xuống.
- `TIMEOUT_MESSAGE` nói rõ cách xử lý, ví dụ: "Model trả lời quá lâu (quá {n} giây). Tăng 'Chờ tối đa' ở Cài đặt AI hoặc dùng model nhanh hơn." Thông điệp chỉ có con số, không có nội dung mail.
- Luật thử lại giữ nguyên: `ReadTimeout` → `ai_timeout`, không thử lại; lỗi mạng, 429, 5xx thử lại tối đa 2 lần.
- `POST /ai/provider/test` giữ thời gian chờ cố định riêng (kiểm tra kết nối, không phải tác vụ).
- Worker: Ctrl+C lần đầu chờ lần gọi đang chạy xong, giờ có thể tới `timeout_seconds` của tác vụ dịch (tối đa 3.600 giây); Ctrl+C lần nữa hủy (spec 26 mục 5).

## 9. Giới hạn glossary (đề xuất áp dụng, chờ duyệt)

- `glossary_routes.py`: đọc giới hạn của người dùng (dòng `user_glossary_settings`, không có thì 5.000) trong cùng phiên, thay cho `MAX_TERMS_PER_SCOPE` ở chỗ thêm một mục và chỗ nhập. Thông điệp: "Tối đa {n} thuật ngữ mỗi phạm vi (đổi ở Cài đặt AI)".
- Nhập file: số dòng tối đa theo giới hạn của người dùng thay cho 5.000 cố định; kích thước tối đa 1 MiB → 8 MiB (50.000 dòng × khoảng 160 byte). Giới hạn body chung của app (16 MiB, `app/main.py`) giữ nguyên. Chữ hướng dẫn ở `GlossaryTable.tsx` lấy số từ cài đặt.
- Thuật toán khớp giữ nguyên: 50.000 mục mất khoảng 0,6 giây mỗi đoạn trên máy dev, chạy trong `asyncio.to_thread` (phụ lục B). Vượt khoảng 100.000 mục mới cần đổi sang Aho-Corasick.

## 10. Số liệu cho người dùng chọn cài đặt (đề xuất, chờ duyệt)

`mail-stats` (chỉ số tổng hợp, chạy được trên dữ liệu thật) in thêm phân bố độ dài `body_clean`: trung vị, p90, p99, lớn nhất, và số mail vượt 3.000 / 10.000 / 30.000 / 100.000 ký tự. Dùng để chọn `context_tokens` và trả lời câu hỏi số đoạn tối đa ở mục 15. Không in nội dung.

## 11. Lỗi

| Mã | Khi | Nút dịch tay, `/ask` | Job dịch |
|---|---|---|---|
| `ai_context_too_small` (mới) | Ngân sách không đủ cho một đoạn tối thiểu hoặc đầu ra tối thiểu | 503, "Ngữ cảnh model ở Cài đặt AI quá nhỏ" | Lỗi cấu hình: dừng người dùng, không ghi gì (như `ai_model_missing`) |
| `ai_timeout` | Một lần gọi quá `timeout_seconds` | 503, thông điệp có số giây | Như spec 26: chờ, thử lại, không bao giờ `failed` |
| `glossary_limit` | Đủ giới hạn của người dùng | 409, thông điệp có số | — |
| 422 (validation) | `PUT /ai/settings` ngoài khoảng | 422 | — |

Cảnh báo mới trên bản dịch: `glossary_trimmed`, `subject_cut` (web cần nhãn tiếng Việt). `truncated` không còn sinh ra.

## 12. Phân quyền và dữ liệu

- Bảng và cột mới không chứa nội dung mail. `user_glossary_settings` có RLS theo `user_id`, chỉ `app_api` có quyền.
- Chia đoạn không đổi bản chất dữ liệu gửi AI: vẫn là text gốc của mail người dùng xem được qua RLS, gửi tới gateway ở Cài đặt AI (spec 28).
- Log chỉ thêm số: `parts`, `est_in`, `dropped`. Không log text, glossary, tên model trong thông điệp lỗi gateway.
- Chạy security-reviewer trước khi commit code.

## 13. Kiểm thử (dự kiến)

- `budget.py`: trọng số từng nhóm ký tự, `B`, `max_tokens` không âm, `cap` theo ba mức `context_tokens` ở mục 6.
- Chia đoạn: nối lại ra đúng `body_clean` (khối `\n\n`, dòng dài, dòng cực dài không có xuống dòng); mail vừa một đoạn giữ hành vi cũ; thân rỗng.
- Dịch nhiều đoạn với `FakeLlm`: số lần gọi, subject lấy từ đoạn 1, ghép body, gộp notes và warnings, ngữ cảnh đoạn k ≥ 2 là cuối đoạn trước, glossary riêng từng đoạn, `glossary_trimmed`, một đoạn lỗi thì không lưu và mã lỗi đúng; job ghi lượt thử một lần cho cả mail.
- `max_tokens` và `timeout` gửi gateway đúng theo cài đặt (`httpx.MockTransport`); `ai_timeout` dùng số giây của cài đặt.
- `/ask`: bỏ nguồn khi ngân sách nhỏ, số trích dẫn khớp, `ai_context_too_small`.
- Cài đặt: GET trả mặc định khi chưa có dòng; PUT lưu `limits` và `glossary_max_terms`; 422 ngoài khoảng; `limits` cho tác vụ chưa có model bị bỏ qua; xóa tên model xóa luôn hai con số.
- Glossary: giới hạn theo người dùng ở thêm và nhập; hạ giới hạn dưới số mục hiện có; nhập 8 MiB.
- RLS: B không đọc, sửa được `user_glossary_settings` của A; `app_worker` không có quyền.
- Migration 0022: giá trị mặc định cho dòng cũ, CHECK.
- Log (`caplog`): không có text mail.
- Web: ô số khóa khi chưa có model, kiểm tra khoảng, lưu.

## 14. Giới hạn đã biết

- Ước lượng theo ký tự lệch với tokenizer thật; với tokenizer kém hiệu quả hơn Qwen3 nhiều (chưa gặp) vẫn có thể vượt ngữ cảnh. Ước lượng dư làm mail bị chia nhiều đoạn hơn cần thiết.
- Gateway hoặc server có thể tự giới hạn `max_tokens` thấp hơn giá trị gửi lên; khi đó bản dịch dài bị `ai_truncated`.
- Với Ollama, `num_ctx` phải được đặt ở phía server đúng bằng số nhập ở Cài đặt AI; code không kiểm tra được.
- Chia cứng giữa một dòng rất dài có thể cắt giữa câu.
- Chưa đo tốc độ model thật; thời gian chờ phù hợp phải chỉnh tay.

## 15. Câu hỏi còn mở (chờ người dùng)

1. **Duyệt mục 5–10** (ước lượng token, chia đoạn, hỏi đáp, thời gian chờ, giới hạn glossary, `mail-stats`).
2. **Mail rất dài trong job tự động:** `body_clean` có thể tới hàng trăm nghìn ký tự (log dán vào mail). Với ngữ cảnh 100.000, mail 200.000 ký tự thành 6 đoạn, chiếm queue dịch có thể cả giờ. Chọn: (a) không giới hạn số đoạn; (b) job bỏ qua mail cần quá N đoạn, để dịch tay. Nên xem số liệu `mail-stats` (mục 10) trước khi chọn.
3. **Nút Dịch tay với mail nhiều đoạn:** (a) giữ chờ trong request như hiện nay (đề xuất, đơn giản); (b) chuyển sang job nền, web hiện trạng thái đang dịch.
4. **Hỏi đáp khi ngân sách lớn:** có mở rộng ngữ cảnh quá 24.000 ký tự không. Đề xuất: không trong spec này, làm spec riêng sau khi đo chất lượng.
5. **Mặc định** ngữ cảnh 32.768 và thời gian chờ 120 giây có giữ không.

## 16. Tiếp tục ở máy khác

1. Đọc spec này, [spec 30](2026-11-07-glossary-kinds-design.md), mục 2026-11-07 của [CHANGELOG](../../CHANGELOG.md).
2. Trả lời các câu hỏi ở mục 15, sửa spec theo câu trả lời, người dùng duyệt spec.
3. Lập kế hoạch thực hiện (skill writing-plans), lưu ở `docs/superpowers/plans/2026-11-07-ai-token-budget.md`.
4. Kiểm tra migration head trước khi tạo 0022 (`uv run --directory backend alembic heads`, hiện là `0021_openai_protocol_only`).
5. Sau khi làm xong: cập nhật `backend/CLAUDE.md` (mục "Gọi AI", "Dịch mail tự động": bỏ 12.000 ký tự, 60 dòng, 120 giây cố định), `README.md` (mục Cài đặt AI), spec 22, 26 (mục 6, 9, 11, 15), spec 21 (`max_tokens`), spec 24 (timeout), CHANGELOG, danh mục spec; đổi trạng thái spec này.

## Phụ lục A. Số token của prompt dịch hiện tại (đo 2026-11-07)

Tokenizer Qwen3 (lấy từ cache `Qwen/Qwen3-Embedding-0.6B`, dòng Qwen3 dùng chung tokenizer), chạy trong môi trường `ml-service`, mail giả. Model khác tokenizer khác, số lệch nhưng cùng cỡ.

- Tiếng Nhật 0,60 token/ký tự; tiếng Việt 0,31 token/ký tự; bản dịch tiếng Việt ≈ 1,0 lần số token bản gốc tiếng Nhật.
- System prompt dịch 271 token; JSON schema đầu ra 121 token.

| Trường hợp | Ngữ cảnh | Glossary | Subject + body | Vào | Ra (ước tính) | Tổng |
|---|---|---|---|---|---|---|
| Mail 800 ký tự, 8 mục glossary | 249 | 208 | 521 | 1.275 | ~670 | ~1.900 |
| Mail 3.000 ký tự, 25 mục | 904 | 650 | 1.833 | 3.684 | ~2.000 | ~5.700 |
| Mail 12.000 ký tự (mức tối đa hiện tại), 60 mục | 904 | 1.560 | 7.341 | 10.102 | ~7.500 | ~17.600 |
| Như trên, 60 mục dài hết cỡ (200 + 200 + 500 ký tự) | 904 | 34.320 | 7.341 | 42.862 | ~7.500 | ~50.300 |

Mọi request hiện xin thêm `max_tokens` = 16.000 cho phần trả lời, bất kể mail dài hay ngắn.

## Phụ lục B. Thời gian khớp glossary theo số mục (đo 2026-11-07)

`match_glossary` hiện tại, máy dev, thuật ngữ ngẫu nhiên 3–8 ký tự Hán/katakana, text 12.000 ký tự:

| Số mục | Thời gian mỗi lần khớp |
|---|---|
| 5.000 | 83 ms |
| 20.000 | 253 ms |
| 50.000 | 606 ms |
| 100.000 | 1,2 giây |

Tăng tuyến tính vì code tìm lần lượt từng thuật ngữ (`str.find`). GPU và model không bị ảnh hưởng: prompt chỉ chứa các mục khớp.
