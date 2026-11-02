# Nhật ký thay đổi và quyết định

Ghi lại những gì đã làm, đã đổi và đã xử lý, mới nhất ở trên. Chi tiết từng chức năng: [docs/superpowers/specs/README.md](superpowers/specs/README.md). Bản ghi chi tiết từng bước (ruling, review) nằm trong `.superpowers/sdd/*/progress.md` (không commit).

## 2026-11-02: chuyển sang model nội bộ (spec 28, xong)

Thực hiện quyết định của người dùng ngày 2026-11-01 ở mục bên dưới. Mục này ghi phần đã làm.

### Đã làm
- URL gateway: `validate_base_url` nhận `http` và `https` tới mọi địa chỉ (LAN, localhost, công cộng), chỉ từ chối URL sai dạng, IP viết tắt và link-local (`reject_link_local`). `AiProviderCustom.public_host` và `Alert` cảnh báo trên Cài đặt AI khi host là địa chỉ công cộng.
- Bỏ khóa mặc định: backend không đọc `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `PMM_ANTHROPIC_BASE_URL`, `PMM_AI_FALLBACKS`, `PMM_MODEL_*`. `LlmResolver.for_user` trả `None` khi chưa nhập nhà cung cấp: `/ask`, dịch tay → 503 `ai_not_configured`, job dịch dừng. Câu hỏi còn treo ở spec 26 (job dịch rơi về khóa môi trường) không còn.
- Bỏ ba cấp Haiku/Sonnet/Opus và giá ước tính; mỗi tác vụ chỉ có tên model người dùng nhập. Migration 0019: xóa dòng `user_ai_settings` chỉ có cấp, `DROP COLUMN tier`, `model NOT NULL`, cấp `DELETE` cho `app_api`.
- Bỏ che thông tin: xóa `Masker` (`masking.py`, `names.py`, `people.py`); `/ask` và dịch gửi text gốc; thêm `app/ai/text.py` (`cut_text`); `AskResult` bỏ `mask_counts`; `TRANSLATE_PROMPT_VERSION` lên 2. Dịch không còn gọi ml-service.
- ml-service: xóa `POST /ner`, `app/ner.py`, spaCy, GiNZA. Đo lại trên máy dev (i5-8500, 15,8 GB RAM): khởi động khoảng 39 giây, bộ nhớ private khoảng 3,2 GB. Số cũ khoảng 4 GB (kèm NER) đo trên máy khác (i5-1235U, 7,7 GB RAM): không so sánh được, chưa biết gỡ NER bớt bao nhiêu.
- Web: Cài đặt AI bỏ ba cấp và thẻ "Che thông tin", luôn hiện "Mail không che sẽ gửi tới <host>"; Hỏi đáp bỏ chữ "đã che".
- Chạy thử đầu cuối trên `mailassist_test` với gateway giả `127.0.0.2`: request có text gốc, không có nhãn, `model` đúng tên đã nhập; đặt `ANTHROPIC_API_KEY` giả rồi xóa nhà cung cấp thì `/ask` và dịch trả 503 `ai_not_configured`, không có request nào ra ngoài (spec 28 mục 10).
- Kiểm tra cuối (sau đợt sửa review): backend 900 test (đầu đợt 28 có 1.051, bớt phần test che đã xóa cùng code; trước đợt sửa review 835), ml-service 13 test, web 109 test, ruff, pyright, ESLint, Prettier, `tsc` và build web sạch.
- Tài liệu: spec 28 (xong, mục 10 kết quả đo, mục 11 quyết định của người viết), dòng cập nhật ở đầu spec 17, 20, 21, 22, 24, 26, 27, danh mục spec, `CLAUDE.md` gốc, `backend/CLAUDE.md` (mục "Gọi AI" viết lại), `ml-service/CLAUDE.md`, `web/CLAUDE.md`, `email-assistant-plan.md`.

### Quyết định của người dùng
- Làm theo quyết định 2026-11-01 (model nội bộ, ô URL nhận mọi địa chỉ, bỏ che).
- **Đổi URL thì nhập lại API key, không dùng key cũ** (sau review, 2026-11-02): ô key trống chỉ giữ key đã lưu khi URL không đổi, đổi URL thì `PUT /ai/provider` trả 422 ở `api_key`. Web bỏ gợi ý "để trống để giữ nguyên" khi URL trong ô khác URL đã lưu. Thay cho hành vi spec 24. Spec 28 mục 13.
- **Gateway: mạng nội bộ đã chặn kết nối ra ngoài ở tầng mạng** (2026-11-02), nên code không chặn thêm và không làm cấu hình CA riêng cho gateway `https`.

### Quyết định của người viết spec (người dùng có thể đổi)
- Thêm quy tắc tên người vào `TRANSLATE_SYSTEM` (giữ nguyên hoặc phiên âm, 様 → -sama, さん → -san), vì model giờ thấy tên thật; spec 28 chỉ nói bỏ quy tắc nhãn. Có thể sinh cảnh báo `glossary_missing` nếu glossary có thuật ngữ là tên người.
- `ask()` tự trả 503 `ai_model_missing` khi thiếu tên model; cột đọc mail dịch rút còn `id, thread_id, subject, body_clean, sent_at`; bỏ `AiProvider.configured_env`.
- Ghim Python `<3.13` của ml-service giữ nguyên dù lý do (GiNZA) đã hết.

### Đã xử lý sau review
- Sửa spec 28 mục 5: giới hạn thân mail không phải `SOURCE_LIMITS` (hằng đó chỉ giữ From, To, Cc, Subject) mà là đoạn ≤ 800 ký tự (`/ask`) và 12.000 ký tự (dịch).
- **Client AI đi theo proxy của môi trường** (Critical): có `HTTP_PROXY` hoặc proxy hệ thống Windows thì mail chưa che và API key đi qua proxy, kể cả địa chỉ LAN. Cả hai client đặt `trust_env=False`; giao thức `anthropic` đổi từ `DefaultAsyncHttpxClient` sang `httpx2.AsyncClient` vì lớp cũ vẫn gắn proxy. `httpx2` thành dependency chính của backend.
- SDK `anthropic` vẫn đọc `ANTHROPIC_CUSTOM_HEADERS`: xóa sau khi dựng client, có test canh khi nâng SDK.
- **Một mail gateway không dịch xong không còn chặn cả hàng dịch.** Mã lỗi mới `ai_timeout` (503) cho hết giờ đọc phản hồi, không thử lại ở cả hai giao thức; job ghi `retry` chờ tối đa 60 phút, không bao giờ `failed`, đi tiếp mail sau. Hết giờ kết nối vẫn là `ai_unavailable`. Web có chữ riêng cho `ai_timeout`.
- Prompt dịch: glossary được ưu tiên hơn quy tắc tên riêng (giữ `TRANSLATE_PROMPT_VERSION` = 2 vì chưa phát hành).
- `usage` sai dạng từ gateway thành `ai_bad_output`, không còn đưa chuỗi của gateway vào traceback; `/ai/provider/test` trả 500 `provider_test_failed` với lỗi lạ, chỉ log tên lớp.
- `validate_base_url` bỏ dấu chấm cuối trước khi xét host, từ chối ký tự điều khiển và khoảng trắng. Tên model do gateway trả chỉ nhận chuỗi in được, tối đa 200 ký tự.
- `load_provider`, `delete_provider`, `user_models` tự lọc `user_id`, không chỉ dựa vào RLS. `create_app` bỏ `app.state.llm`.
- Trang Hỏi đáp không còn khẳng định gửi cho model nội bộ (ô URL nhận mọi địa chỉ).
- Test: `/ask` log đi đủ hai lượt qua client thật, cô lập nhà cung cấp với một `LlmResolver`, biến môi trường đặt trước `create_app`.
- Tài liệu: sửa quyền cột ở spec 28 (migration là `GRANT DELETE`), phạm vi `reject_link_local`, bỏ cách nói gỡ NER bớt 0,8 GB, thêm các giới hạn ở spec 28 mục 8, thêm chỉ dẫn đầu spec 27 và kế hoạch 27.

### Người dùng cần làm sau khi cập nhật code
1. Dừng API và worker, rồi `uv run --directory backend alembic upgrade head` trên DB dev (migration 0019 xóa cột `tier`, code cũ đang chạy sẽ lỗi).
2. Dừng ml-service đang chạy rồi `uv sync --directory ml-service` (gỡ spaCy, GiNZA; Windows khóa file nên không sync được khi service chạy).
3. `npm --prefix web install`, `npm --prefix web run build`: `web/dist` không theo git, web cũ gọi API đã đổi sẽ lỗi.
4. Khởi động lại ml-service, API và worker.
5. Ở Cài đặt AI nhập URL gateway, API key và tên model cho từng tác vụ. Dòng cấu hình chỉ có cấp bị xóa nên `/ask` và dịch báo thiếu tên model cho tới khi nhập; trang không tự báo tác vụ nào thiếu. Cần `$env:PMM_SECRET_KEY` để lưu key.
6. Bản dịch cũ hiện nhãn "cũ" và có thể còn nhãn che; bấm "Dịch lại" nếu cần.

### Còn để sau
- Gọi thử gateway thật, đo chất lượng và khả năng output có cấu trúc của model nội bộ.
- Dòng `message_auto_runs` đã `failed` với mã `mask_unstable` giữ nguyên `failed` (dịch tay hoặc "Thử lại").
- Cảnh báo địa chỉ công cộng theo URL đã lưu, chưa theo URL đang gõ.
- Chặn metadata cloud ngoài link-local (`fd00:ec2::254`, `100.100.100.200`): chờ quyết định nơi đặt server.
- Gateway `https` với CA riêng: `trust_env=False` tắt `SSL_CERT_FILE`, giao thức `openai` chỉ tin `certifi`. Không làm cấu hình CA riêng (người dùng: mạng nội bộ đã chặn ra ngoài; spec 28 mục 8).
- Kiểm lại link-local mỗi lần `for_user`.
- Web: cảnh báo công cộng khi đang gõ URL, báo tác vụ thiếu tên model ở Cài đặt AI, test xóa tên model.
- `MessageTranslation.tsx` map `ml_unavailable` không còn dùng cho dịch.
- Lỗi `/ask`: ml-service tắt thì 503 đến sau một lượt gọi AI phân tích.
- Dọn nhỏ: `MAX_RETRIES` trùng ở `client.py` và `provider.py`, `except` ở `ai_routes.py` (dịch), comment test, test giới hạn To/Cc.
- Làm spec 27 (truy xuất hỏi đáp), rồi phần còn lại của giai đoạn 6.
- Chờ người dùng: có nới "mỗi bản mail chỉ gọi AI một lần" không.

## 2026-11-01: chuyển sang model nội bộ, không còn yêu cầu che thông tin (mới đổi tài liệu, code chưa đổi)

### Quyết định của người dùng
- **Không dùng API ngoài, AI chạy trên model local nội bộ**, gọi qua gateway hoạt động như 9router. Thay cho: được gửi qua Claude API (2026-10-19), gateway công cộng (2026-10-25), OpenRouter để thử (2026-10-31).
- **Không cần che thông tin, các ràng buộc NDA về gửi AI ra ngoài không còn cần thiết**, vì mail không ra ngoài. Thay cho quyết định bắt buộc che (2026-10-19) và "Che thông tin giữ nguyên dù model là nội bộ" (2026-10-31).
- **Giữ màn hình Cài đặt AI**: người dùng nhập URL gateway, API key, model.
- **Làm trực tiếp trên `master`**, không tạo nhánh riêng. Spec 25, 26 đã merge qua PR #1, #2.
- **Ô URL nhận mọi địa chỉ** (chọn sau khi được nêu rủi ro nhập nhầm URL công cộng): code không chặn API ngoài, "không gửi mail ra API ngoài" là quy ước do người cấu hình đảm bảo.

### Đã làm
- Rà soát toàn bộ tài liệu theo hai quyết định trên: `CLAUDE.md` gốc, `backend/CLAUDE.md`, `ml-service/CLAUDE.md`, `web/CLAUDE.md`, `email-assistant-plan.md`, `.claude/` (security-reviewer, mẫu spec, skill survey-profile), danh mục spec, spec 12, 17, 20, 21, 22, 24, 26, 27 và kế hoạch 27, `backend/Mail automation spec.md`.
- Viết spec 28 (`2026-11-01-internal-model-design.md`), đã duyệt thiết kế, chưa có code. Spec 27 làm sau spec 28, migration đổi thành 0020.
- Không sửa code: `Masker` vẫn chạy, `ANTHROPIC_API_KEY` vẫn dùng được, gateway trong LAN vẫn bị từ chối.

### Quyết định của người viết (người dùng có thể đổi)
- Spec đã xong giữ nguyên nội dung vì mô tả code hiện tại, chỉ thêm dòng cập nhật ở đầu. Lịch sử CHANGELOG, kế hoạch đã chạy xong, `docs/progress-2026-10-18.md` không sửa.
- Giữ các nguyên tắc không phụ thuộc việc che: chỉ đọc mbox, không tự gửi mail, không commit mail thật, không ghi nội dung mail vào log, không lưu nội dung file đính kèm, RLS. Giữ "Claude Code không đọc mail thật" vì Claude Code gửi mọi thứ nó đọc tới Anthropic, tức là một API ngoài.
- **Bỏ `Masker` và khóa mặc định `ANTHROPIC_API_KEY` trong cùng một đợt**: nếu không, người chưa nhập gateway sẽ gửi mail chưa che tới Anthropic.
- Bỏ khỏi kế hoạch: Voyage AI dự phòng, Message Batches API, tool runner của SDK `anthropic` (đều cần API ngoài hoặc SDK ngoài).
- Giữ "mỗi bản mail chỉ gọi AI một lần": người dùng chưa quyết định đổi.
- Spec 28: giữ chặn địa chỉ link-local (metadata cloud), cảnh báo trên màn hình khi host là địa chỉ công cộng, xóa cột `user_ai_settings.tier`, bỏ giá ước tính.

### Còn để sau
- Làm spec 28: nhận gateway trong LAN, bỏ khóa mặc định, bỏ `Masker`, NER, phần giao diện liên quan, đổi ba cấp Haiku/Sonnet/Opus thành tên model. Sau đó mới làm spec 27.
- **Chờ người dùng:** có nới "mỗi bản mail chỉ gọi AI một lần" không.
- Câu hỏi "job dịch rơi về khóa môi trường mặc định khi xóa nhà cung cấp" (spec 26) sẽ hết khi bỏ khóa môi trường.

## 2026-11-01: dịch mail tự động, glossary, chuyển mail sang folder dự án (spec 26, xong)

Thiết kế và quyết định của người dùng ở mục 2026-10-31 bên dưới. Mục này ghi phần đã làm và đã sửa.

### Đã làm
- Migration 0018: cột `customer_domains`, `processed_source_id`, `ai_since` của `projects`; bảng `glossary_terms`, `message_translations`, `message_auto_runs`.
- Dịch: `app/ai/translate.py` (che → glossary → `call_task("translate")` → kiểm tra → thay ngược), nút Dịch / Dịch lại trên web (`POST /messages/{id}/translate`), job nền `translate_sweep` (mỗi phút) và `translate_user` (queue `translate`, role `app_api` qua `user_session`), `app/worker.py` chạy hai vòng worker trong một process. Tiêu chí tự động: mail thuộc dự án, From thuộc domain khách, nằm trong Inbox, từ mốc 14 ngày của dự án.
- Glossary chung và theo dự án (`/glossary`, nhập CSV hoặc dán nhiều dòng có xem trước, xuất CSV), trang `/settings/glossary`, tab Glossary trong cấu hình dự án, thêm từ đoạn bôi đen trong thẻ mail, file hạt giống `backend/seed/glossary-it-ja-vi.csv` (151 thuật ngữ công khai).
- Chuyển mail đã dịch sang folder dự án **chỉ trên web** (`message_auto_runs.target_source_id`), `move`, `move-back`, `auto-retry`; folder hiệu lực áp cho bộ lọc folder, nhãn folder, `GET /folders`. Thunderbird vẫn thấy mail ở Inbox.
- Web: hai cột bản gốc / bản dịch, cảnh báo kiểm tra sau dịch, `MovePicker`, khối "Dịch tự động" ở cấu hình dự án, `GlossaryPage`.
- Kiểm tra cuối: pytest backend 1033 đạt (trước đợt này 712), Vitest 100 đạt (trước 48), ruff, pyright, ESLint, build web sạch. Chạy thử đầu cuối trên `mailassist_test` với AI giả (server tương thích OpenAI trên loopback), chưa gọi thử gateway thật.

### Quyết định của người viết spec (người dùng có thể đổi)
- Job dịch chạy bằng role `app_api` qua `user_session`, không dùng `engine` worker (BYPASSRLS): các hàm dùng chung dựa vào RLS.
- `ai_bad_request` ghi `retry` chờ tối đa 60 phút và không bao giờ thành `failed`, vì lỗi này có thể do sai tên model chứ không do mail.
- Worker hai vòng, vòng nào kết thúc thì dừng vòng kia (treo khi tắt trên Linux nếu không).
- Thêm trường `projects` và `move_projects` vào `ThreadTranslationItem` để ô chọn dự án và phạm vi glossary chỉ hiện dự án của mail.
- Sửa thẳng migration 0018 (bỏ `SELECT` thừa của `app_worker`) vì chưa phát hành; `mailassist` và `mailassist_proj` mới ở 0017.
- Giữ phần bỏ đuôi chuỗi liền dài hơn phần đệm khỏi đoạn gửi của `/ask`: thà thiếu ngữ cảnh còn hơn lọt mảnh chưa che.
- Che thừa cạnh chữ Nhật và từ thường (hệ quả của ranh giới từ chỉ xét chữ Latin) được chấp nhận.

### Đã xử lý sau review (review toàn nhánh và security-reviewer, 2026-11-01)
- **Che thông tin, cũng ảnh hưởng `/ask`:** (1) cắt sau khi che thay vì trước, che tối đa 40.000 ký tự rồi `cut_masked`; (2) ranh giới từ của EMAIL, URL, PHONE, MONEY và mẫu tên chỉ xét chữ Latin nên địa chỉ, số điện thoại liền sau chữ Nhật được che; (3) chuỗi trên 500 ký tự không vào từ điển, URL dài không còn làm `RecursionError`; (4) **`/ask` lọt giá trị nguyên vẹn do cắt đoạn trước khi che** (nghiêm trọng, có sẵn từ spec 21): nay che theo cửa sổ ±200 ký tự (`Masker.mask(keep=…)`, `_chunk_range`), kể cả URL dài chưa mã hóa có chữ Nhật (0 trên 975 điểm cắt lọt, trước đó 449); (5) `_person` và header `/ask` che tới trần ingest rồi mới cắt. Spec 20 mục 10, spec 21 mục 11. **Spec 27 phải giữ `keep` và `_chunk_range` khi đổi `SOURCES`.**
- Bẫy `no_folder`: mail dịch trước khi chọn folder nay được xét lại khi lưu dự án; `MovePicker` hiện cho mọi mail `done` chưa có đích.
- Mail ở hai Inbox: dùng "mọi Inbox" thay cho `from_source_id`; `GET /folders` không đếm đôi; viết lại `LIST_FOLDERS` bằng `LEFT JOIN … IS NULL` (60 ms thay vì 1.562 ms).
- Glossary: khớp katakana nửa độ rộng và NFD; xuất chặn ký tự công thức full-width; nhập bỏ BOM.
- `app_worker` bỏ quyền `SELECT` trên `glossary_terms`, `message_translations`.
- Job: lỗi lạ thành `translate_failed` thay vì lỗi mãi ở mail cũ nhất, `mask_unstable` là lỗi theo mail, `httpx.HTTPError` không tính lượt thử; pytest có `faulthandler_timeout = 300` sau một lần treo 6 giờ không tái hiện được.
- Giao diện: hiện mã lỗi cạnh "Đang chờ dịch lại tự động"; nút "Chuyển lại Inbox" kiểu viền.

### Còn để sau
- **Chờ người dùng quyết định:** khi người dùng xóa nhà cung cấp của mình, job dịch rơi về khóa môi trường mặc định và vẫn gọi AI; có nên chặn không.
- Gọi thử gateway thật; move thật qua IMAP (đổi nguyên tắc "không ghi lên mail server"); cho phép gateway ở máy khác trong LAN.
- Chưa đo lại `GET /folders` và lọc theo folder dưới phiên RLS sau thay đổi "mọi Inbox" (số đo mới bằng superuser).
- Các điểm nhỏ ở spec 26 mục 15: chất lượng dịch giảm vì che thừa, thuật ngữ thêm từ đoạn bôi đen vẫn còn sau khi xóa mail, `ai_bad_request` thử lại mỗi giờ mãi, bộ đếm lượt thử dùng chung, xóa thuật ngữ không có xác nhận, dữ liệu mẫu `data/project-test-profile` có ngày 9 nên không qua mốc 14 ngày.
- Môi trường: `npm run lint` bước prettier báo 13 file CRLF có từ trước; `npm run gen:api` cần `$env:PYTHONUTF8 = "1"` trên máy này.

## 2026-10-31: thiết kế dịch mail tự động và folder dự án (spec 26; làm xong ngày 2026-11-01, xem mục trên)

### Quyết định của người dùng (2026-10-31)
- **Model:** đang dùng OpenRouter để thử, khi vận hành người dùng tự chuyển sang model nội bộ qua gateway kiểu 9router ở máy khác trong mạng nội bộ. Việc chọn model không thuộc phạm vi thiết kế dịch.
- **Che thông tin giữ nguyên** dù model là nội bộ.
- **Dịch tự động mail đủ 3 tiêu chí:** thuộc dự án, From thuộc domain khách khai báo ở dự án, nằm trong Inbox. Chỉ từ mốc 14 ngày, mail cũ hơn dịch tay được.
- **Sau khi dịch, chuyển mail sang folder dự án** chọn ở cấu hình dự án. Đợt này chỉ chuyển trên web, Thunderbird và mail server không đổi. Move thật qua IMAP làm spec riêng sau, lúc đó mới đổi nguyên tắc "không ghi lên mail server".
- Làm trước phần còn lại của giai đoạn 6.

### Quyết định của người viết spec (người dùng có thể đổi)
- Mốc 14 ngày tính theo từng dự án, đặt lần đầu khai báo domain. Mục "Mốc theo người dùng hay theo dự án" của ngày 2026-10-31 chốt tại đây.
- Mail khớp nhiều folder đích thì ở lại Inbox và hỏi người dùng, theo nguyên tắc spec 25.
- Glossary theo từng người (chung và theo dự án), so khớp trên text đã che; không có translation memory ở đợt này.
- Kiểm tra sau dịch chỉ cảnh báo, không tự gọi AI lại. Giữ nút dịch tay cho mail ngoài tiêu chí.
- Job dịch chạy trên queue riêng, dùng role `app_api`.

### Còn để sau
- Move thật qua IMAP. Cho phép gateway ở máy khác trong LAN, sửa khi chuyển sang model nội bộ.

## 2026-10-31: bỏ chỗ ghi riêng Claude trong giao diện và tài liệu

### Quyết định của người dùng (2026-10-31)
- Người dùng đã đặt model bất kỳ ở Cài đặt AI, nên giao diện và tài liệu không còn nói riêng về Claude hay Anthropic. Chờ trả lời hiện "Đang tìm mail và chờ AI trả lời…".

### Đã làm
- Web: `AskPage`, `AiSettingsPage` đổi chữ "Claude" thành "AI" (khối "Nội dung đã gửi cho AI", thông báo gateway, lựa chọn giao thức "Anthropic (Messages API)", placeholder tên model không còn ví dụ Claude). Thông điệp lỗi và docstring backend, ml-service tương tự; schema API sinh lại (`web/openapi.json`).
- Tài liệu: `CLAUDE.md` gốc, backend, web, ml-service, danh mục spec, spec 20, 21, 22, kế hoạch (bảng công nghệ mục LLM).
- Không đổi: giao thức `anthropic`/`openai` trong DB và API, SDK `anthropic`, khóa mặc định `ANTHROPIC_API_KEY` (vẫn dùng được khi chưa nhập nhà cung cấp).

## 2026-10-31: profile mẫu để thử bộ lọc dự án

### Đã làm
- `data/generate_project_test_profile.py` sinh `data/project-test-profile/` (30 mail giả cho 3 dự án Test 1, Test 2, Test 3, mail khách gửi tài liệu đính kèm kèm bản cập nhật, và mail không thuộc dự án nào) cùng `data/expected_projects.json`, `data/expected_documents.json`. Chi tiết: spec 12, mục 10.

## 2026-10-31: lộ trình mail tự động và cấu hình dự án (spec 25)

### Quyết định của người dùng (2026-10-31)
- **Backlog → Redmine và GitHub review: đợi, chưa đối ứng.** Chỉ ghi trong kế hoạch, chưa làm và chưa viết tài liệu. Người dùng sẽ quyết định khi nào thực hiện.
- **Việc đầu tiên là màn hình cấu hình dự án và gán mail theo bộ lọc** (spec 25). Trường Git, Backlog, Redmine có trên màn hình và được lưu nhưng chưa dùng, không lưu API key hay token.
- **Quy tắc gán dự án: người dùng tự khai báo bộ lọc From, To, CC, Subject** cho từng dự án, thay cho chốt danh sách trong code. Mail khớp nhiều dự án được gán vào tất cả dự án khớp.
- **Phạm vi AI khi tự động xử lý mail: chỉ mail từ mốc.** Mốc là ngày bật lùi 14 ngày, cố định, không trượt theo thời gian; mail cũ hơn mốc vẫn tìm kiếm được nhưng không gọi AI. Lý do: tránh chi phí xử lý lịch sử mà vẫn theo dõi được các việc đang dở. Mốc theo người dùng hay theo dự án sẽ chốt khi làm phân loại AI.
- **Không làm import `.eml`, `.msg`:** Thunderbird đã cung cấp đủ mail qua mbox.

### Đã làm
- Spec 25 xong: migration 0017 (`projects`, `message_projects`, `user_project_state`), `app/projects.py` (khớp bộ lọc, gán mail mới trong `rethread_user`, quét lại khi sửa bộ lọc), API `/projects`, lệnh `assign-projects`, nhãn dự án trên danh sách thread, màn hình `/settings/projects` (`TagInput`).
- Chạy thử đầu cuối trên DB `mailassist_test` với profile giả (1.050 mail nạp bằng collector): tạo, sửa bộ lọc, xóa dự án; nhãn trên danh sách thread đổi theo bộ lọc sau vài giây; giao diện hẹp xuống một cột.
- Kiểm tra đã chạy trên commit cuối: pytest 712 test đạt, ruff (check, format) và pyright sạch, Vitest 48 test đạt, build web và eslint sạch. Sau đó chỉ rút một docstring test cho vừa 100 cột và chạy lại ruff cùng `test_projects_api.py` (46 đạt). `npm run lint` bước prettier vẫn báo file cấu hình do CRLF của working copy (có từ trước).

### Đã xử lý sau review
- URL dự án: từ chối query, fragment, tên đăng nhập ssh khác `git`, host bắt đầu bằng `-`; kiểm tra độ dài giá trị lọc trước khi chuẩn hóa NFKC.
- Gán dự án ghi từng lô trong SAVEPOINT, thử lại khi mail/dự án bị xóa giữa chừng hoặc deadlock; sửa điểm vào CLI `assign-projects`; Enter khi đang soạn IME không thêm giá trị vào `TagInput`.
- Thêm test RLS theo hành vi cho `message_projects`, `user_project_state`, `projects`.
- Đợt sửa cuối: xóa dự án đang chọn quay về trạng thái chưa chọn; ô tìm kiếm không co lại khi dải "Đang xử lý" hiện; lưu bộ lọc dùng `FOR NO KEY UPDATE` nên không chờ worker và bớt đường deadlock; refetch nền lỗi không gỡ form đang sửa; giới hạn độ dài từng ô theo spec; nhãn thread tải lại khi gán xong; thêm test RLS WITH CHECK và nhãn theo tách/gộp thread (spec 25 mục 11).

### Còn để sau
- Xem mục 10 của spec 25 (xóa dự án có thể hiếm khi 500 do deadlock, giới hạn 100 dự án có thể vượt nhẹ, cần `--` và danh sách folder gốc khi dùng URL/đường dẫn về sau).

### Quyết định của người viết spec (người dùng có thể đổi)
- Dự án là của từng người dùng, chưa chia sẻ cho thành viên.
- Bộ lọc so với địa chỉ mail và subject, không so tên hiển thị. Trống cả 4 ô thì dự án không nhận mail nào.
- Thread hiện tên dự án khi có ít nhất một mail trong thread thuộc dự án đó.

## 2026-10-25: nhà cung cấp AI tùy chỉnh (spec 24)

### Quyết định của người dùng (2026-10-25)
- **NDA nới thêm: cho phép gửi nội dung mail đã che tới gateway công cộng** (OpenRouter, 9router…), không chỉ Claude API của Anthropic. Việc che 4 nhóm thông tin vẫn bắt buộc. Người dùng chịu trách nhiệm xác nhận với khách; trang Cài đặt AI hiện cảnh báo theo host.
- **Trang Cài đặt AI cho nhập URL, API key và tên model** để hỏi đáp AI chạy qua các gateway đó.

### Quyết định của người viết spec (người dùng có thể đổi)
- Cấu hình theo từng người dùng, không dùng chung toàn hệ thống.
- Khóa mã hóa API key lấy từ biến môi trường `PMM_SECRET_KEY` (Fernet), không tự sinh file khóa; thiếu thì không lưu key.
- Hai giao thức: `openai` (chat/completions, `json_schema` rồi lùi về `json_object`) và `anthropic`.
- `http://` chỉ cho loopback; `https://` mọi host công cộng, chặn IP riêng/metadata/userinfo/query, không theo redirect.

### Đã làm
- Migration `0016_ai_provider`: bảng `user_ai_provider` (RLS, key mã hóa), cột `user_ai_settings.model`, `tier` cho phép NULL.
- Backend: `app/ai/provider.py` (kiểm tra URL, `OpenAiLlm`, `LlmResolver`), `app/ai/secrets.py`, `call_task(custom_model=…)`, `ask(custom_provider=…)`; API `GET/PUT/DELETE /ai/provider`, `POST /ai/provider/test`; `/ai/settings` thêm `custom_provider` và `models`. CLI `gen-secret-key`. Phụ thuộc mới: `cryptography`.
- Web: khối "Nhà cung cấp AI" (giao thức, URL, API key không hiện lại, Kiểm tra kết nối, Xóa, cảnh báo NDA theo host), ô tên model theo tác vụ khi có provider tùy chỉnh.
- Kiểm thử: 47 test cho mã hóa, URL, client OpenAI-compatible (gateway giả); 20 test API (key không lộ qua response/log, RLS, che thông tin với provider ngoài, khóa sai); web thêm 1 test.

### Đã xử lý sau review
- security-reviewer (2026-10-25): giao thức `anthropic` theo redirect (có thể đưa key và mail đã che sang host khác) đã tắt; kiểm tra URL thêm tên nội bộ, IP dạng số/hex và phân giải DNS; giới hạn 4 MiB body phản hồi; đóng client cũ; chỉ hiện gợi ý key khi key dài từ 16 ký tự. Chi tiết: spec 24 mục 13.

- Chẩn đoán lỗi "Nhà cung cấp AI đang quá tải hoặc lỗi" (2026-10-26): log `ai provider error` nay ghi thêm mã HTTP cuối cùng (`type=Status429`, `Status502`…, không có body) để phân biệt giới hạn tốc độ với lỗi gateway. Log trên máy dev cho thấy `Status429` với model `:free` của OpenRouter (giới hạn tốc độ của gói miễn phí), nên với 429 thông báo nói rõ "giới hạn tốc độ: chờ, đổi model hoặc nạp thêm hạn mức". Theo yêu cầu người dùng, lỗi 429/5xx/mạng của provider kiểu `openai` hiện kèm chi tiết ra màn hình: mã HTTP (hoặc tên lớp lỗi mạng), host, model, số lần thử. Vẫn không chép body phản hồi của gateway.

### Còn để sau
- Thử lại khi gateway trả 429/5xx hiện không chờ giữa các lần (3 lần liên tiếp, bỏ qua `Retry-After`); chưa sửa vì chưa biết mã lỗi thật (cần log mới).
- Chưa gọi thử OpenRouter/9router thật (cần key của người dùng).
- DNS rebinding (đổi DNS giữa lúc kiểm tra và lúc kết nối) chưa chặn; model tự nhập không có giá ước tính; đổi `PMM_SECRET_KEY` làm mất key đã lưu.

## 2026-10-19 → 2026-10-21: giai đoạn 5, tìm kiếm ngữ nghĩa và hỏi đáp AI

### Quyết định của người dùng (2026-10-19)
- **NDA: khách cho phép gửi nội dung mail qua Claude API, với điều kiện phải che thông tin nhạy cảm**: email, điện thoại, URL, địa chỉ; tên người; tên công ty, tổ chức; số tiền, số hợp đồng/đơn hàng (chọn cả bốn nhóm). Đã ghi vào kế hoạch (giai đoạn 0, mục 3) và `CLAUDE.md`.
- **Thêm màn hình cài đặt model AI**: 3 cấp Haiku / Sonnet / Opus, chọn theo từng tác vụ.

### Đã làm
- Spec 17 (ml-service), 18 (embedding mail), 19 (tìm kiếm hybrid + `eval/`), 20 (che thông tin), 21 (hỏi đáp AI), 22 (cài đặt model AI), 23 (thread liên quan).
- `ml-service/` (mới, Python 3.12): `/embed` (bge-m3, CPU), `/ner` (GiNZA tiếng Nhật, spaCy tiếng Anh; tiếng Việt không có NER), `/health`; chỉ `127.0.0.1`, kiểm Host, không log text, lỗi không trả lại input.
- DB: migration `0015_embeddings_ai`: `message_chunks` (vị trí đoạn trên `body_clean` + `halfvec(1024)`, index HNSW cosine, RLS, `app_api` chỉ đọc, cascade khi xóa mail), `user_ai_settings`, `ai_calls` (model, token, thời gian; không có nội dung).
- Worker: `embed_sweep` mỗi phút + `embed_user` (job ≤ 10 giây, tự đẩy tiếp; mail mới nhất trước); làm sạch xong mail mới thì embed ngay. CLI `embed-mail`; `mail-stats` có số mail đã/chờ embed.
- Tìm kiếm: `GET /api/v1/search?mode=hybrid` (nhánh vector + nhánh từ khóa cùng cú pháp ô tìm kiếm, gộp RRF k=60, tối đa 200 kết quả, nhãn khớp từ khóa/ngữ nghĩa, số mail chưa embed). Web: nút "Từ khóa / Ngữ nghĩa" trên trang kết quả.
- Đánh giá (`eval/retrieval_eval.py`, `eval/questions-sample.jsonl`, kết quả `eval/results-sample.md`): 1.050 mail giả embed bằng bge-m3 thật, 28 câu hỏi Việt/Nhật/Anh. Vector/hybrid: hit@1 0,89, hit@5 0,96, MRR 0,92; từ khóa AND: hit@1 0,18. Chi tiết spec 19 mục 10.
- Thread liên quan: `GET /api/v1/threads/{id}/similar` (cosine ≥ 0,80, tối đa 5), khối "Thread có thể liên quan" trên trang thread, gộp bằng sửa tay có sẵn.
- Che thông tin (`app/ai/masking.py`): mẫu regex + từ điển người gửi/nhận của chính người hỏi + tên công ty học từ toàn văn (cả chữ ký) + NER; nhãn nhất quán trong một lần hỏi (`[PERSON_1]`…), lan sang mọi đoạn, che cả câu hỏi; lỗi thì không gửi; thay ngược trên server.
- Hỏi đáp: `POST /api/v1/ask`: che câu hỏi → Claude phân tích (từ khóa, câu tìm tiếng Nhật, người gửi, khoảng ngày) → tìm 20 đoạn trong phạm vi RLS → che nguồn → Claude trả lời tiếng Việt kèm `[n]` → thay nhãn. Structured outputs; fallback phía server cho Sonnet/Opus; mỗi lần gọi ghi `ai_calls`. Không lưu câu hỏi/câu trả lời. Web: trang "Hỏi AI" (câu trả lời, nguồn, khối "Nội dung đã gửi Claude" để tự kiểm tra việc che, model và token).
- Cài đặt AI: `GET/PUT /api/v1/ai/settings`, `GET /api/v1/ai/usage`; web: trang "Cài đặt AI" (cấp model từng tác vụ, giá, nhóm thông tin bị che, thống kê tháng).
- Kiểm thử: backend 517 test (thêm embedding, hybrid, thread liên quan, che thông tin, hỏi đáp với Claude/ml-service giả, client SDK với transport giả, worker embed); ml-service 27 test (API với model giả, NER GiNZA/spaCy thật); web 17 test Vitest; ruff, pyright, ESLint, Prettier, `tsc` sạch.

### Quyết định của người viết spec (người dùng có thể đổi)
- NER chạy trong ml-service (không nạp model trong backend). Không làm reranker (CPU quá chậm).
- Che cả câu hỏi của người dùng (có thể chứa tên khách), không chỉ đoạn mail.
- Không lưu lịch sử hỏi đáp; mỗi câu hỏi độc lập, không stream.
- Cài đặt model theo từng người dùng; mặc định phân tích = Haiku, trả lời = Sonnet.
- Lớp 4 gom thread: gợi ý tính lúc xem trang thread, không có danh sách gợi ý toàn cục.
- Embed toàn bộ mail (self-host, không tốn tiền); quyết định "phạm vi xử lý AI lần đầu" vẫn để mở cho giai đoạn 6.
- Nhánh từ khóa của tìm kiếm ngữ nghĩa dùng AND (cú pháp ô tìm kiếm), không dùng "một trong các từ": đo trên bộ giả, OR kéo về mail nhiễu làm MRR giảm từ 0,92 xuống 0,71 (RRF ngang nhau) hay 0,77 (trọng số 0,3); AND giữ nguyên chất lượng của vector và vẫn đẩy mã/cụm chính xác lên đầu.
- Ngưỡng thread liên quan cosine ≥ 0,80: cần chỉnh trên mail thật (dữ liệu giả sinh hàng loạt nên điểm cao 0,93–0,97).

### Đã đo (máy dev, CPU, dữ liệu giả)
- bge-m3 trên CPU i5-1235U: khoảng 0,3 đoạn/giây (đoạn 800 ký tự), câu tìm kiếm 0,4 giây; int8 động không nhanh hơn. Embed lần đầu vài nghìn mail mất vài giờ chạy nền. NER 7–11 giây cho 21 đoạn. Chi tiết spec 17 mục 8.

### Chạy thử (E2E, DB test, dữ liệu giả)
- Edge headless + backend trên DB test + ml-service thật + Claude **giả** ghi lại request: đăng nhập, tìm ngữ nghĩa câu tiếng Việt ra mail tiếng Nhật (100 kết quả, nhãn "ngữ nghĩa"), trang thread có 5 thread liên quan, Hỏi AI (20 nguồn, `[n]` thành link, khối "Nội dung đã gửi Claude"), lưu cài đặt AI; không có lỗi console/CSP ngoài 401 kiểm tra phiên ở trang đăng nhập.
- Quét request gửi Claude giả: lần đầu thấy che thừa (`仕様書`, `在庫管理システム`, `From`/`To`) và lọt `Trầnです`; sau khi sửa không còn tên, công ty, tên miền, điện thoại nào của dữ liệu giả.

### Đã xử lý sau security review (2026-10-21)
- **Nghiêm trọng: tên người lọt** qua lời chào + danh xưng (`Chào Anh Tuấn`, `Dear Mr. Tanaka`), `-sama`, `a/c` + tên, tên trong từ điển viết hoa/thường khác, full-width, katakana half-width. Sửa: mẫu lời chào/danh xưng mới, biến thể Title/UPPER, mẫu và từ điển chạy trên bản NFKC (ánh xạ vị trí về text gốc).
- **Nghiêm trọng: điện thoại, email lọt** (`0912345678`, `+84…`, `03(1234)5678`, dấu `−`, `＠` full-width). Sửa: thêm mẫu, NFKC.
- Mẫu tiền (`500万`, số sau `金額`/`giá`, `nghìn`, `trăm triệu`, `15k USD`), số hợp đồng (thêm nhãn, mã sau "hợp đồng"), địa chỉ (Việt Nam, Nhật không có tỉnh), tên miền (thêm đuôi, nuốt path).
- **Regex chạy theo bình phương** (subject 10.000 chữ số: 13 giây, chặn cả API): mọi lượng từ có giới hạn, che chạy ở thread riêng; test 9 chuỗi cố tình dựng (≤ 0,2 giây).
- NER chọn model theo từng dòng; kiểm nhãn model lúc khởi động; backend coi kết quả NER sai dạng là lỗi. Làm sạch kết quả NER (kính ngữ, "tên" trộn Hán + katakana, từ header).
- Client Claude ghim `https://api.anthropic.com` (bỏ qua `ANTHROPIC_BASE_URL`/profile), tắt OpenTelemetry của SDK, log SDK ở mức WARNING. ml-service tắt telemetry của FastAPI, bắt lỗi trong endpoint (không để uvicorn log traceback có text), tự offline khi model đã có trong cache.
- Subject cắt 300 ký tự, To/Cc tối đa 10 người khi embed/che/gửi (mail độc không làm kẹt embedding hay làm `/ask` lỗi). Header của nguồn che từng giá trị riêng.
- `ML_SERVICE_URL` chỉ nhận loopback hoặc https; embed câu tìm kiếm trước khi mở phiên DB; cache từ điển xóa mục hết hạn; eval đọc mbox chỉ đọc; lời nhắc về prompt injection trên trang Hỏi AI; test CSRF `/ask` và từ điển theo người dùng.
- **Vòng 2 (rà lại lượt sửa)**: Nghiêm trọng: số điện thoại cuối câu / sau `Tel.` vẫn lọt → sửa ranh giới. Lỗi do lượt sửa trước: lọt `健一様`, `本多様` (lọc `仕様` quá rộng), bỏ hẳn tên NER dài / Hán + katakana, chữ Việt NFD lọt, ngưỡng tiếng Việt theo dòng quá chặt, tự offline Hugging Face không có tác dụng → đã sửa. Thêm: không lan số ngắn (năm/ngày không bị che theo), `ASP.NET` không bị coi là tên miền, `TANAKA-san`, `4丁目2番8号`, `(+84)`, `鈴木エリカ様`.

### Còn để sau
- Chưa gọi thử Claude thật: máy dev chưa có `ANTHROPIC_API_KEY`. Đã kiểm request/response của SDK bằng transport giả và cả luồng bằng Claude giả; cần chạy thử một câu hỏi trên dữ liệu giả khi có khóa.
- Che thông tin còn giới hạn (spec 20 mục 8): tên viết toàn chữ thường, địa chỉ tiếng Anh không có nhãn, tên không có trong header/kính ngữ mà NER bỏ sót.
- Tiêu chí giai đoạn 5 (30 câu hỏi do PM soạn, ≥ 80% đúng có nguồn): PM tự chấm trên dữ liệu thật.
- Embed nhanh hơn: máy có GPU, hoặc đổi model nhỏ hơn (phải hỏi người dùng: đổi model là embed lại toàn bộ).
- Index vector riêng theo người dùng trước khi nhiều người dùng (spec 19 mục 9).

## 2026-10-19: kiểm tra cách đặt mật khẩu web

### Đã xử lý
- **Lỗi: `set-password --password-stdin` qua pipe PowerShell lưu cả BOM vào mật khẩu**: CLI báo đã đặt nhưng đăng nhập bằng đúng mật khẩu luôn bị từ chối. Nay đọc stdin dạng byte UTF-8, bỏ BOM, từ chối mật khẩu có ký tự ẩn/điều khiển hoặc không phải UTF-8.
- Mật khẩu chuẩn hóa NFC khi băm và khi kiểm tra (chữ Việt có dấu gõ kiểu dựng sẵn hay tổ hợp đều khớp). Chưa có mật khẩu web nào trên DB dev nên không cần đặt lại.
- `set-password` kiểm tra email trước khi hỏi mật khẩu; in đường dẫn đăng nhập sau khi đặt xong.
- Kiểm lại thật trên DB test (scrypt thật): đặt mật khẩu ASCII và có dấu qua pipe PowerShell → đăng nhập 200, sai mật khẩu 401; mật khẩu ngắn → mã thoát 2; email không có → báo lỗi, không hỏi mật khẩu. Spec 13 mục 11.

## 2026-10-18 → 2026-10-19: giai đoạn 4, search từ khóa và web UI

### Đã làm
- Spec 13 (đăng nhập web), 14 (search từ khóa), 15 (API thread, toàn văn, gộp/tách tay), 16 (giao diện web).
- DB: migration `0010_web_sessions` (`user_passwords`, `web_sessions`, 4 hàm `SECURITY DEFINER` chỉ `app_api` gọi được; `app_api` không đọc được hai bảng), `0011_thread_override_writes` (`user_thread_state` có RLS; `app_api` được `INSERT` theo cột và `DELETE` `thread_overrides` của chính mình), `0012_messages_search_index` (index PGroonga trên `ARRAY[subject, body_clean]`, tạo `CONCURRENTLY`).
- Backend:
  - Đăng nhập web: mật khẩu scrypt, phiên cookie `HttpOnly`/`SameSite=Strict` 12 giờ, header `X-PMM-Request` chống CSRF, giới hạn 5 lần sai / 15 phút, CLI `set-password`. Endpoint web chỉ nhận phiên, endpoint collector chỉ nhận token: token collector không đọc được mail.
  - `GET /api/v1/search`: ô tìm kiếm hỗ trợ AND, `OR`, `-từ`, `"cụm từ"` (backend tự dựng truy vấn Groonga); lọc folder, người gửi, khoảng ngày, file đính kèm; sắp theo ngày hoặc độ liên quan; tô từ khóa và đoạn trích (danh sách đoạn `{text, hit}`). Không log chuỗi tìm kiếm.
  - `GET /api/v1/folders`, `/threads` (keyset), `/threads/{id}`, `/messages/{id}`, `/messages/{id}/full` (toàn văn chia theo quote/chữ ký), `/processing`, `/thread-overrides` (tạo, liệt kê, hoàn tác).
  - Worker: task `apply_overrides` mỗi 5 giây; hỏi bảng job mỗi giây. Sửa tay có hiệu lực sau khoảng 2–6 giây (đo trên profile giả).
  - Backend phục vụ bản build của web; header CSP `default-src 'self'`, `nosniff`, `no-referrer`, `X-Frame-Options: DENY`; `/api/…` `Cache-Control: no-store`. Tắt `/docs`, `/redoc` (Swagger UI tải script từ CDN). `python -m app.openapi_dump` cho client web.
- Web (`web/`, mới): React 19 + Vite 8 + TypeScript + TanStack Query + React Router 8 + Tailwind 4 + shadcn/ui; client sinh từ OpenAPI (`npm run gen:api`). Trang đăng nhập, danh sách thread, kết quả search, hội thoại (toàn văn, tách), hộp thoại gộp, trang "Sửa tay".
- Kiểm thử: backend 316 test (thêm đăng nhập, search, thread, sửa tay, phân quyền giữa hai người dùng, EXPLAIN dùng index trong phiên RLS, worker áp dụng sửa tay kể cả khi sửa tay được ghi giữa lúc gom, các điểm sửa sau review); web 13 test Vitest; ruff, pyright, ESLint, Prettier, `tsc` sạch.
- DB dev đã lên migration 0014 (index mới hợp lệ). Đo trên DB dev, chỉ số liệu: search 16–34 ms, danh sách thread 16 ms với 4.792 mail. Chạy thử bằng Edge headless trên DB test với profile giả 1.050 mail: đăng nhập sai/đúng, lọc, search `見積` (113 kết quả có tô), mở thread, toàn văn, tách → gộp → hoàn tác, đăng xuất; không có lỗi console hay vi phạm CSP.

### Đo tốc độ (dữ liệu tổng hợp, DB test)
- 300.000 mail của một người + 100.000 của người khác, từ "phổ biến" có trong ~73% số mail (trường hợp xấu nhất). Từ hiếm 29 ms; từ phổ biến 0,4–0,7 s; danh sách thread 9 ms. Chưa đạt dưới 1 giây: từ phổ biến kèm lọc người gửi (1,1 s). Chi tiết: spec 14 mục 10.
- Đã tối ưu sau lần đo đầu (1,1–1,5 s): đếm tối đa 10.000 (`total_capped`, web hiện "Hơn 10.000 kết quả"; thay đổi schema API của web), sắp/cắt trang trên cột hẹp trước khi lấy nội dung, migration `0014_threads_list_index`, cờ đính kèm của thread bằng `LATERAL`.

### Quyết định của người viết spec (người dùng có thể đổi)
- Đăng nhập bằng mật khẩu đặt qua CLI, phiên 12 giờ không gia hạn; SSO để giai đoạn 7.
- "Xem bản gốc" của kế hoạch làm thành "Toàn văn": hiện `body_text` đã có trên server (text đã giải mã), phần quote/chữ ký đã cắt hiện mờ. Mở mail gốc trong Thunderbird qua collector để sau. Không lưu thêm gì lên server (quyết định mở số 6 của kế hoạch không bị ảnh hưởng).
- Search trả từng mail (không gộp theo thread), mặc định mới nhất trước.
- Gộp thread từ web: gộp thread đang xem (theo mail đầu thread) vào thread của mail chọn qua search.

### Đã xử lý sau security review (2026-10-19)
- **Nghiêm trọng: access log của uvicorn ghi chuỗi tìm kiếm và bộ lọc người gửi** (`GET /api/v1/search?q=…&sender=…`). `app/run.py` gắn filter bỏ query string cho logger `uvicorn.access`; có test.
- **Hàm `SECURITY DEFINER` thiếu `pg_temp` ở cuối `search_path`** (0005, 0010): phiên `app_api` có thể tạo kiểu giả trong `pg_temp` để chạy code bằng quyền `app_owner`. Migration `0013_secdef_search_path`: `search_path = pg_catalog, pg_temp` cho 5 hàm, thu hồi quyền TEMP trên database của PUBLIC; test kiểm mọi hàm `SECURITY DEFINER`.
- **Giới hạn đăng nhập sai bị vượt bằng request đồng thời**, và endpoint đăng nhập có thể làm cạn bộ nhớ (scrypt ~32 MiB mỗi lần): giữ chỗ mỗi lần thử trước khi băm, tối đa 4 lần scrypt cùng lúc; test 20 request đồng thời → 5 lần 401.
- Chống DNS rebinding: chỉ nhận Host `127.0.0.1`, `localhost` (biến `PMM_ALLOWED_HOSTS`).
- Thread lớn: giới hạn 2.000.000 ký tự nội dung cho cả thread; tô từ khóa, đoạn trích chạy ở thread riêng (không chặn event loop).
- Web: `?next=` phân tích bằng `URL` và so origin (trước đó `/%09/evil…` lọt qua, React Router chặn nên chỉ làm hỏng trang); ô tìm kiếm, người gửi `autoComplete="off"`.
- Hash giả cho email không tồn tại tạo theo tham số scrypt hiện hành.
- Ghi thành giới hạn (chưa sửa): kênh phụ thời gian qua index PGroonga dùng chung khi có nhiều người dùng; `app_api` lấy được hash mật khẩu qua hàm (ngang quyền đặt `app.user_id`); cookie `Secure` theo scheme, không tách theo cổng; tạo/xóa sửa tay liên tục làm worker bận.

### Còn để sau
- Lọc và danh sách thread theo dự án: chờ người dùng chốt danh sách dự án và quy tắc gán.
- Tìm tiếng Việt không dấu; mở mail gốc qua collector; audit log xem/tìm (giai đoạn 7).
- Trước khi chạy nhiều người dùng hoặc mở API ra ngoài máy: index PGroonga có `user_id`, cookie `Secure`/`__Host-`, giới hạn đăng nhập theo IP lưu trong DB, giới hạn tần suất sửa tay.

## 2026-10-18: quy tắc và hook cập nhật tài liệu, spec bộ sinh mail giả, sửa test collector

### Quyết định của người dùng
- **Mỗi khi thêm hoặc sửa một chức năng thì phải cập nhật tài liệu liên quan** (spec, danh mục spec, CHANGELOG, CLAUDE.md của thành phần, bảng cấu hình Claude Code) trong cùng lượt làm việc, trước khi báo xong. Quy tắc nằm ở `.claude/rules/documentation.md` (luôn được nạp), có trỏ từ `CLAUDE.md`. Trước đó chỉ có quy tắc cập nhật mục "Lệnh" khi dựng khung một thành phần.

### Đã làm
- Spec 12 (viết bù): bộ sinh profile Thunderbird giả (`data/generate_sample_profile.py`, `data/expected_threads.json`, skill `/gen-sample-mail`). Kiểm lại bằng cách sinh vào thư mục tạm: 1.051 mail, 11 mbox có mail, 88 nhóm, 1.050 Message-ID.
- Sửa các tài liệu đã cũ so với generator: `collector/CLAUDE.md` (51 → 1.051 mail), `CLAUDE.md` (mô tả profile; thêm skill `gen-sample-mail` và rule tài liệu vào bảng cấu hình), skill `gen-sample-mail` (bỏ câu mâu thuẫn với `test-data.md`, thêm bước chạy lại test), spec 1 (cây folder mong đợi), `.claude/rules/test-data.md` (nạp cả khi sửa `data/`).

### Đã xử lý (người dùng đồng ý cùng ngày)
- **8 test collector fail**: nguyên nhân là `data/thunderbird-profile` bị dở (thư mục `ImapMail` rỗng, thiếu `Local Folders/Inbox`), trong đó có một panic nil pointer trong `syncer`. Đã sinh lại profile bằng `/gen-sample-mail`: 1.051 mail, 88 nhóm.
- `syncer_test.go`, `service_test.go`, `sync_api_test.go` viết cứng 51 mail; nay lấy số mail từ `expected_threads.json`. Kết quả: `go test -C collector ./...` pass toàn bộ (gofmt, vet sạch); test backend dùng generator 30 passed.
- **Hook nhắc tài liệu** (`.claude/hooks/doc_reminder.py`, khai báo trong `.claude/settings.json`): PostToolUse ghi lại file vừa sửa, UserPromptSubmit xóa danh sách khi bắt đầu lượt mới, Stop kiểm tra cuối lượt. Nếu lượt có sửa code (trừ test và dữ liệu sinh ra) mà không sửa file `.md` nào thì hook chặn dừng một lần và nhắc Claude. Hook chỉ đọc đường dẫn file. Đã chạy thử với input giả cho 7 tình huống và input hỏng; đã xác nhận PostToolUse chạy thật trong phiên.

## 2026-10-17 (tối): giai đoạn 3, làm sạch nội dung và gom thread

### Đã làm
- Spec 9 (làm sạch), 10 (gom thread), 11 (worker và công cụ kiểm tra).
- DB: migration `0006_cleaning_threads` (`messages.body_clean`, `clean_cuts`, `cleaner_version`, `thread_id`, `thread_parent_id`, `thread_method`; bảng `threads`, `thread_overrides` có RLS, FK kép kèm `user_id`, `app_api` chỉ đọc), `0007` (index tạo CONCURRENTLY), `0008` (hàng đợi Procrastinate ở schema riêng `procrastinate`, chỉ `app_worker`). `env.py` chạy mỗi migration trong transaction riêng.
- Backend: `app/mail/cleaning.py` (cắt quote/chữ ký, giữ nội dung chuyển tiếp), `app/mail/threading.py` (References/In-Reply-To theo JWZ, Thread-Index, subject + người tham gia + 60 ngày, gộp/tách tay), `app/processing.py`, worker `python -m app.worker` (tự quét mỗi phút), CLI `process-mail`, `mail-stats`, `review-threads`.
- Kiểm thử: 209 test; profile giả 1.051 mail gom đúng 100% so với `data/expected_threads.json` (cả trong DB).
- Chạy trên DB dev (chỉ xem số liệu tổng hợp): worker tự xử lý 4.792 mail trong 27 giây, 0 lỗi; 3.812 thread (3.719 thread một mail); 978 mail nối bằng References, 1 bằng Thread-Index, 1 bằng subject; cắt quote 710 mail, chữ ký 1.058 mail; 6 mail thành rỗng (đều là `Re:` chỉ có phần trích dẫn). Gom lại lần hai không đổi mail nào.

### Quyết định của người viết spec (người dùng có thể đổi)
- Nội dung chuyển tiếp được giữ trong `body_clean` (search không mất mail khách chuyển cho mình); đổi lại, chuyển tiếp một mail đã có thì nội dung trùng.
- Gom lại toàn bộ mail của một người mỗi lần có mail mới, giữ id thread theo số mail chung; khi chậm (khoảng vài trăm nghìn mail) thì chuyển sang gom tăng dần.
- Gán dự án tự động chưa làm: cần người dùng chốt danh sách dự án và quy tắc gán (domain khách, mã dự án).
- API/giao diện xem thread và gộp/tách tay làm cùng web ở giai đoạn 4.

### Đã xử lý sau security review (không có mục Nghiêm trọng)
- **Lỗi DB trong worker/CLI có thể in nội dung mail:** `DETAIL: Failing row contains (…)` là cả dòng `messages`, và Procrastinate log cả traceback. Nay task và CLI thay lỗi bằng mô tả chỉ có tên lớp lỗi, `sqlstate`, ràng buộc, vị trí code (`app/safe_errors.py`). Có test ép lỗi CHECK: log/stderr không có nội dung.
- **ReDoS trong làm sạch:** regex địa chỉ mail chạy bậc hai trên dòng dài. Một mail spam 2 triệu ký tự có thể làm worker treo hàng giờ. Đã sửa (lookbehind, bỏ dòng dài, lọc nhanh, cache, đếm có giới hạn). Mail cố tình dựng xấu nhất nay 1,3 giây.
- **`threads.subject` còn lại sau khi xóa mail của folder:** migration 0009 bỏ `subject`, `subject_norm`. `threads` không còn chứa nội dung mail.
- **`review-threads --out` ghi nội dung mail ra bất kỳ đâu:** bỏ `--out`, chỉ ghi vào `%LOCALAPPDATA%\PMMailAssistant\review\`. Tự xóa file cũ hơn 7 ngày; `.gitignore` có `threads-*.html`.
- Lưu ý đã làm: CSP chỉ cho script theo hash, `translate="no"`, truy vấn review tự lọc `user_id` (không chỉ dựa vào RLS), helper `user_connection` (bản sync của `user_session`), worker tắt LISTEN/NOTIFY và xóa job đã xong.
- Không tăng `CLEANER_VERSION`: kết quả chỉ khác ở trường hợp hiếm (dòng dài hơn 1.000 ký tự ngay sau đường kẻ). DB dev đã lên 0009; gom lại không đổi mail nào.
- Còn để sau: đề xuất thêm deny lệnh PowerShell/Bash đọc thư mục review vào `.claude/settings.json` (thay đổi quyền, chờ người dùng); audit log khi xuất review (giai đoạn 7); kiểm tra index INVALID và `NOT VALID`/`VALIDATE` khi lên production.

### Lưu ý
- `data/thunderbird-profile` đang thiếu `ImapMail` và `Local Folders/Inbox` (có file do Thunderbird tạo, script sinh vừa được sửa). Test không phụ thuộc thư mục này: golden test tự sinh profile giả vào thư mục tạm.

## 2026-10-17

### Quyết định của người dùng
- **Mỗi người dùng có bản mail riêng, không dùng chung mail giữa người dùng.** Lý do: dùng chung theo Message-ID cho phép người biết Message-ID (vd. từ header References) gửi mail giả để đọc bản thật. Đánh đổi: mail gửi cho nhiều người được lưu và xử lý AI ở bản của từng người. Đã sửa `CLAUDE.md` (nguyên tắc "mỗi bản mail gọi AI một lần"), kế hoạch, spec DB, `.claude/rules/database.md`.
- **Bỏ chọn folder không xóa mail trên server**; thêm chức năng chủ động chọn xóa mail của folder khỏi server (spec 7).
- **Đồng bộ xong phải có thông báo hoàn thành** (spec 8).
- Làm tiếp các bước còn lại không cần hỏi xác nhận; mỗi chức năng có spec riêng (danh mục spec).

### Đã làm
- DB: migration `0004_per_user_messages` (cột `messages.user_id`, khóa `(user_id, message_id_hash)`, FK kép từ `message_ownership`, policy theo `user_id`, index bắt đầu bằng `user_id`).
- Spec cho các chức năng 3–8 (trích xuất mail, đồng bộ tăng dần, xác thực API, ingest API, xóa mail của folder, giao diện đồng bộ).
- **Chức năng đồng bộ chạy được từ đầu đến cuối**: chọn folder → bấm "Đồng bộ" → mail vào DB → hộp "✓ Đồng bộ hoàn thành" kèm số mail mới / đã có / thời gian.
  - Collector: đọc mbox không khóa, tách mail, parse MIME + tiếng Nhật (`enmime`), đồng bộ tăng dần theo state JSON, gửi lô gzip có retry, lệnh `collector sync`, khóa chống chạy trùng, kết nối server và token mã hóa DPAPI trên trang setting, tiến độ, nút Dừng.
  - Backend: FastAPI (`python -m app.run`), token API (`python -m app.cli create-user/create-token/revoke-token`), migration `0005` (`api_tokens`, hàm `auth_token_user` SECURITY DEFINER, quyền INSERT/DELETE theo cột), `POST /api/v1/ingest` idempotent, `GET /api/v1/sources`, `POST /api/v1/sources/delete-mail`.
  - Xóa mail của folder khỏi server từ trang setting (folder đã bỏ tick, hoặc folder không còn trong Thunderbird).
- Chạy thử E2E với backend + PostgreSQL thật và profile tự sinh: lần 1 "50 mail mới, 1 mail đã có trên server", lần 2 "0 mail mới"; xóa folder đang chọn bị từ chối; bỏ tick ProjectB rồi xóa → server còn 46 mail; log backend chỉ có ID và số lượng.

### Đã xử lý sau review cuối chức năng đồng bộ (code reviewer + security-reviewer)
- **Nghiêm trọng (bảo mật): nội dung file đính kèm dạng text bị gửi lên server** trong `body_text` (part text có tên file, vd. `log.txt` inline hoặc chỉ có `name=`). Tự chọn thân mail: chỉ part text không có tên file và không phải attachment; part có tên file chỉ gửi metadata. `ParserVersion` → `collector/0.3.0`.
- **Nghiêm trọng (mất dữ liệu): compact rồi có mail mới làm file lớn hơn vị trí đã lưu → bỏ sót mail mới vĩnh viễn.** Lưu `tail` (SHA-256 4 KiB trước vị trí) và kiểm tra lại khi đọc tiếp; lệch thì đọc lại từ đầu.
- Token chỉ gửi tới server đã nhập cùng nó (file token lưu origin; đổi server phải nhập token mới; URL trong `config.json` được kiểm tra lại mỗi lần).
- `delete-mail` đọc body sau khi xác thực token.
- Một mail có Date bất thường (năm 0001, múi +24:00…) làm cả lô bị 422 và folder kẹt mãi → collector bỏ ngày không hợp lệ; backend coi ngày ngoài 1970–2100 là null; lô bị từ chối thì chia đôi để chỉ bỏ mail lỗi.
- Đọc vượt kích thước đã đo (gửi mail Thunderbird đang ghi dở) → chỉ đọc tới kích thước lúc mở file.
- Khóa đồng bộ do hệ điều hành giữ (tiến trình chết thì tự nhả, không còn "khóa cũ 6 giờ"); panic trong parser hoặc lượt đồng bộ không làm sập collector; kiểm tra hủy trước mỗi mail; Ctrl+C / đóng cửa sổ dừng gọn.
- Một địa chỉ hỏng (vd. `Tanaka [Sales] <t@x>`) không còn làm mất cả From/To; header gửi thẳng byte JIS/Shift_JIS được giải mã; nhận nhãn charset `MS932`, `ISO-2022-JP-MS`…
- Xóa được mail của folder đã đổi tên/xóa trong Thunderbird (nhóm "Có trên server, không còn trong Thunderbird").
- Lưu ý đã sửa: không theo redirect; không cho lưu cấu hình/kết nối khi đang đồng bộ (409); lỗi DB chỉ log `sqlstate`/ràng buộc; **tắt telemetry OpenTelemetry bật sẵn của FastAPI 0.143**; mail giả trùng Message-ID không đổi `mbox_offset` của bản thật; giới hạn độ dài một dòng mbox; backend 404 khi xóa = đã xóa; thông báo hoàn thành có số mail bị bỏ qua / chỉ đọc được header; `collector sync` trả mã 3 khi có folder lỗi; Message-ID có chú thích phía sau; giới hạn số nguyên 2^63−1; sửa chú thích DPAPI (không chống được mã độc chạy dưới cùng tài khoản).

### Đã xử lý sau security review lần 2 (rà lại lượt sửa)
- Review xác nhận đã sửa đúng: token theo server, không theo redirect, `delete-mail` xác thực trước, Date bất thường, lỗi DB, telemetry, khóa, recover.
- **Nghiêm trọng: nội dung file đính kèm text vẫn lọt vào `body_text`** khi enmime không đọc được tên file hoặc `Content-Disposition`: tên file RFC 2231 bằng ISO-2022-JP/Shift_JIS (mailer Nhật hay dùng), `attachment filename=…` thiếu `;`, kiểu lạ `attachement`/`x-attached`, `filename=""`. Sửa: quyết định "là file đính kèm" từ header thô của part; tự giải mã tên file RFC 2231 charset Nhật. `ParserVersion` → `collector/0.4.0`.
- Text bên trong `message/rfc822` có tham số `boundary` không còn vào thân mail (chỉ đi xuống `multipart/*`).
- Khối uuencode trong thân mail bị cắt, ghi thành metadata file đính kèm.
- **Backend từ chối (426) dữ liệu từ collector có parser < `collector/0.4.0`.** Dữ liệu đã nạp bằng parser cũ: đã kiểm DB dev (2026-10-17) không có mail nào (0 dòng `messages`), không cần làm sạch. Nếu có DB khác từng nạp bằng parser cũ: xóa mail của các folder đó khỏi server (trang setting) rồi đồng bộ lại.
- Collector chỉ coi 404 là "đã xóa từ trước" khi backend trả mã `source_not_found`; 404 khác (proxy, sai đường dẫn) là lỗi.
- Client tới server được tạo bằng URL đã kiểm tra và chuẩn hóa; khóa `sync.lock` không đi theo symlink (`FILE_FLAG_OPEN_REPARSE_POINT`; test cho phần này bị bỏ qua trên máy dev vì không có quyền tạo symlink).

### Còn để sau (chức năng đồng bộ)
- File đính kèm mã hóa yEnc trong thân mail chưa được cắt (hiếm trong mail công việc).
- Mail đã xóa trong Thunderbird (sau compact) vẫn còn trên server; cờ đã đọc/đã trả lời chỉ cập nhật khi folder được đọc lại từ đầu.
- Hai profile/máy của cùng một người có `folder_path` trùng nhau sẽ ghi chung một folder trên server.
- Khóa chính `message_ownership` nên bắt đầu bằng `user_id`; token chưa có phạm vi quyền và hạn dùng; chưa giới hạn tần suất gọi API.
- Múi giờ dạng tên (`JST`) chưa đọc được (ngày = null); chưa bỏ escape `>From ` trong thân mail; ghi state chưa thử lại khi rename bị chặn (antivirus); nút Đồng bộ có thể bật cho profile chưa từng lưu; dòng phân cách mbox chưa kiểm tra chặt dạng `From <addr> <date>`.
- Mail trùng Message-ID nhưng khác dấu vân tay chỉ được đếm `mismatched`, chưa có chỗ để người dùng xem.

### Đã xử lý sau review bảo mật DB (migration 0003 và hạ tầng)
- **Nghiêm trọng:** role API tự gắn mình vào mail người khác qua `message_ownership` → thu hồi `INSERT/UPDATE`, chỉ cho sửa 3 cột cờ; từ 0004 còn có FK kép chặn ở cấu trúc.
- `app_user_id()` khai báo `PARALLEL SAFE` (truy vấn trên bảng mail chạy song song được).
- Thu hồi quyền chạy hàm quản trị PGroonga (`pgroonga_command`…) khỏi PUBLIC.
- Log không chứa nội dung mail: server `log_error_verbosity=terse`, engine `hide_parameters`.

### Thay đổi kỹ thuật
- Cổng DB đổi 55432 → **15432** (55432 nằm trong dải Windows/Hyper-V giữ trước).
- PGroonga ghim 4.0.6-1; backend chạy bằng Python 3.13 do `uv` chọn.

## 2026-10-16

### Đã làm
- Trang setting của collector (`collector settings`): chọn profile, cây folder Server/Local, chọn folder đồng bộ, lưu `config.json`.
- DB: PostgreSQL 16 + PGroonga + pgvector bằng Docker; 4 bảng `users`, `mail_sources`, `messages`, `message_ownership`; RLS; helper `user_session`.

### Quyết định của người dùng
- Không dùng git cho dự án.
- Không lưu bản gốc MIME lên server (chỉ text đã giải mã và metadata).
- DB chạy bằng Docker trên máy dev (giai đoạn một người dùng).

### Đã xử lý sau review trang setting
- `/api/browse` mở handle tới file mbox → kiểm tra bằng `os.Stat` trước.
- Folder con mới của folder đã bỏ tick bị tự thêm → kế thừa quyết định của folder cha; so khớp đường dẫn không phân biệt hoa thường.
- Lưu cấu hình ghi folder chưa từng hiển thị thành "bỏ tick" → chỉ ghi folder đã hiển thị.
- File tạm `nstmp-N` của Thunderbird hiện thành folder → lọc theo mẫu.
- Double-click `collector.exe` sau lần lưu đầu không mở lại setting → luôn mở trang setting.

### Còn để sau (ghi trong ledger)
- Trang setting: chặn request từ cổng localhost khác, cookie theo cổng, bỏ `?key=` khỏi URL, CSP thêm `base-uri`, danh sách ổ đĩa có thể treo với ổ mạng, `config.json` có BOM bị coi là hỏng, thông báo lỗi khi server cục bộ tự tắt.
- DB: test quyền đầy đủ, mật khẩu dev khi lên server, siết quyền `app_worker`, index `(source_id, message_id)`, ghim digest image, dọn mail mồ côi, thống nhất phiên bản Python.
- Cần khảo sát giai đoạn 0: tên folder IMAP dạng modified UTF-7, tài khoản có thư mục lưu ngoài profile.
