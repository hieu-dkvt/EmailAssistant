# PM Mail Assistant

Trợ lý email dùng AI cho PM dự án outsource Nhật: trích xuất mail từ Thunderbird (mbox), gom thread, theo dõi trạng thái đối ứng, tìm kiếm bằng ngôn ngữ tự nhiên (Nhật / Việt / Anh).

- Yêu cầu, mô hình dữ liệu, lộ trình: [email-assistant-plan.md](email-assistant-plan.md).
- Công nghệ: theo mục "Kiến trúc" bên dưới. Bảng "Công nghệ đề xuất" ở mục 3 của file kế hoạch (TypeScript toàn bộ) đã cũ, không dùng nữa.
- File này áp dụng cho thư mục `Email Assistant` và được ưu tiên hơn `d:\App\Management\CLAUDE.md` (file mẫu chung của thư mục cha).

## Trạng thái hiện tại

Spec từng chức năng và trạng thái: [docs/superpowers/specs/README.md](docs/superpowers/specs/README.md). Thay đổi và quyết định theo ngày: [docs/CHANGELOG.md](docs/CHANGELOG.md).

- Giai đoạn 1–2 (trích xuất và nạp dữ liệu) đã chạy được đầu cuối và đã qua review cuối (2026-10-17): chọn profile và folder trên trang setting của collector, nhập kết nối server, bấm "Đồng bộ" → mail vào DB, có thông báo "Đồng bộ hoàn thành"; chọn xóa mail của folder đã bỏ tick (hoặc không còn trong Thunderbird) khỏi server.
- Giai đoạn 3 (2026-10-17): làm sạch quote/chữ ký (`body_clean`) và gom thread (References/In-Reply-To, Thread-Index, subject; bảng `thread_overrides` cho gộp/tách tay) chạy tự động bằng worker sau mỗi lần đồng bộ. Chưa làm: gán dự án tự động (chờ người dùng chốt danh sách dự án và quy tắc).
- Giai đoạn 4 (2026-10-18, spec 13–16): web (React) đăng nhập bằng mật khẩu, danh sách thread có bộ lọc (folder, người gửi, ngày, đính kèm), search từ khóa PGroonga có tô từ khóa, xem thread dạng hội thoại, toàn văn (quote/chữ ký đã cắt hiện mờ), tách/gộp/hoàn tác thread bằng tay (worker áp dụng trong vài giây). Chưa làm: lọc theo dự án (chờ gán dự án), mở mail gốc trong Thunderbird.
- Giai đoạn 5 (2026-10-19 → 2026-10-21, spec 17–23, đã qua security-reviewer 2026-10-21): ml-service (bge-m3 embedding, NER GiNZA/spaCy) chạy trên CPU; worker embed mail nền (mới nhất trước); tìm kiếm "Ngữ nghĩa" (hybrid PGroonga + pgvector, RRF; eval trên mail giả hit@1 0,89, MRR 0,92); gợi ý thread liên quan trên trang thread (lớp 4 gom thread, người dùng bấm gộp); hỏi đáp AI (RAG) với **che thông tin bắt buộc** (email/điện thoại/URL/địa chỉ, tên người, công ty, số tiền/số hợp đồng: khách cho phép dùng Claude API với điều kiện này, người dùng xác nhận 2026-10-19); trang "Cài đặt AI" chọn Haiku/Sonnet/Opus cho từng tác vụ, thống kê token; từ 2026-10-25 (spec 24) nhập được nhà cung cấp riêng (OpenRouter, 9router…: URL, API key mã hóa, tên model). Đã chạy thử đầu cuối với Claude giả; chưa gọi thử Claude thật (máy dev chưa có `ANTHROPIC_API_KEY`). Embed trên CPU máy dev chậm (khoảng 0,3 đoạn/giây, spec 17 mục 8).
- Backend: FastAPI. API collector (chỉ token): `/api/v1/me`, `/ingest`, `/sources`, `/sources/delete-mail`. API web (chỉ phiên cookie): `/session`, `/folders`, `/search` (`mode=keyword|hybrid`), `/threads` (+ `/threads/{id}/similar`), `/messages`, `/thread-overrides`, `/processing`, `/ask`, `/ai/settings`, `/ai/usage`. DB PostgreSQL 16 (PGroonga + pgvector) với RLS, mỗi người dùng một bản mail; worker Procrastinate (bảng job ở schema `procrastinate`). Backend phục vụ bản build của web (`web/dist`).
- Bước tiếp theo theo kế hoạch: giai đoạn 6 (phân loại, theo dõi đối ứng, tóm tắt, soạn nháp reply). Còn chờ người dùng: phạm vi xử lý AI lần đầu (mục 9 kế hoạch), danh sách dự án và quy tắc gán. Khi dựng khung một thành phần, cập nhật mục "Lệnh" trong CLAUDE.md của thành phần đó cho đúng thực tế.

Chạy thử toàn bộ trên máy dev:
1. Bật Docker Desktop, `docker compose up -d`, `uv run --directory backend alembic upgrade head`.
2. `uv run --directory backend python -m app.cli create-user --email <e> --name "<tên>" --role pm` → lấy token. Đặt mật khẩu web: `... python -m app.cli set-password --email <e>`.
3. Build web một lần (và sau mỗi lần sửa web): `npm --prefix web install`, `npm --prefix web run build`.
4. `uv run --directory ml-service python -m app.run` (cửa sổ riêng, `127.0.0.1:8001`; lần đầu `uv sync --directory ml-service` và tải model khoảng 2,3 GB, các lần sau tự chạy offline). Cần cho tìm kiếm ngữ nghĩa, embed nền và hỏi đáp; thiếu thì các chức năng khác vẫn chạy.
5. `uv run --directory backend python -m app.run` (API và web ở `http://127.0.0.1:8000`). Muốn dùng hỏi đáp AI: đặt `$env:ANTHROPIC_API_KEY` trong cửa sổ này trước khi chạy, hoặc nhập nhà cung cấp (URL, API key, model) ở trang Cài đặt AI trên web; lưu key cần `$env:PMM_SECRET_KEY` (sinh bằng `uv run --directory backend python -m app.cli gen-secret-key`).
6. `uv run --directory backend python -m app.worker` (cửa sổ riêng): mỗi phút tự làm sạch, gom thread và embed mail mới; sửa tay từ web áp dụng trong vài giây.
7. `collector\collector.exe settings` → chọn profile và folder, Lưu → nhập URL `http://127.0.0.1:8000` và token, Lưu kết nối → Đồng bộ.
8. Mở `http://127.0.0.1:8000`, đăng nhập bằng email và mật khẩu ở bước 2. Kiểm tra số liệu: `uv run --directory backend python -m app.cli mail-stats` (chỉ số liệu tổng hợp, có số mail đã/chờ embed). Người dùng tự xem 100 thread ngẫu nhiên: `... python -m app.cli review-threads --email <e>` (file HTML có nội dung mail, Claude Code không đọc).

Claude Code chỉ mở giao diện web (chụp màn hình, đọc trang) trên DB test với profile giả, không mở trên DB dev `mailassist` (có mail thật).

Profile Thunderbird tự sinh để chạy thử: `data/thunderbird-profile` (sinh bằng `python data/generate_sample_profile.py` hoặc `/gen-sample-mail`, toàn bộ là dữ liệu giả, tên miền `*.example.*`; mặc định 1.051 mail, đáp án gom thread ở `data/expected_threads.json`; xem spec 12).

## Kiến trúc

```
Collector (Go, máy người dùng) ──HTTPS──▶ api (FastAPI) ◀──── web (React SPA)
                                            │      │
                                            │      └──▶ ml-service: embed, rerank
                                            ▼                    ▲
                                       PostgreSQL ◀────── worker (Procrastinate)
                                PGroonga + pgvector + bảng job
```

| Thành phần | Thư mục | Công nghệ |
|---|---|---|
| Collector | [collector/](collector/CLAUDE.md) | Go, đóng gói thành 1 file `.exe` |
| Backend API + worker | [backend/](backend/CLAUDE.md) | Python 3.12+, FastAPI, SQLAlchemy 2.0, Procrastinate, SDK `anthropic` |
| ML service | [ml-service/](ml-service/CLAUDE.md) | Python, PyTorch: embedding (`bge-m3`) và reranker |
| Frontend | [web/](web/CLAUDE.md) | React + Vite + TypeScript |
| Đánh giá search | `eval/` | Notebook, bộ câu hỏi mẫu, script đo recall@k / MRR |
| Mail mẫu cho test | `samples/` | `raw/`: mail thật, không commit · `anonymized/`: đã ẩn danh, dùng cho test |
| DB | — | PostgreSQL 16+ với PGroonga và pgvector (image tự build) |

Mỗi thư mục thành phần có CLAUDE.md riêng (công nghệ, cấu trúc, lệnh, kiến thức nghiệp vụ). Hạ tầng chạy chung bằng `docker compose up -d`.

Phân chia trách nhiệm:
- **Collector chỉ trích xuất**: quét mbox tăng dần, tách mail, parse MIME, giải mã charset, chuyển HTML sang text, lấy metadata rồi gửi lên API. Không chứa logic nghiệp vụ.
- **Backend làm mọi xử lý còn lại**: cắt quote/chữ ký, chống trùng lặp, gom thread, gán dự án, gọi AI. Logic nằm ở server để khi sửa quy tắc chỉ cần chạy lại job, không phải phát hành lại collector.
- **Frontend chỉ hiển thị**: client gọi API và kiểu dữ liệu sinh tự động từ OpenAPI của FastAPI.

## Nguyên tắc bắt buộc

### Dữ liệu và bảo mật (NDA)
- **Chỉ đọc** mbox và profile Thunderbird: mở file read-only, không khóa, không ghi, không đổi tên. Không ghi gì lên mail server.
- **Không bao giờ tự gửi mail.** Tool chỉ tạo bản nháp, người dùng tự copy sang Thunderbird.
- **Claude Code không đọc mail thật.** Mọi thứ Claude Code đọc đều được gửi tới Anthropic. Không đọc profile Thunderbird hay `samples/raw/` bằng công cụ file (đã chặn trong `.claude/settings.json`). Lệnh hoặc script chạy trên dữ liệu thật chỉ được in số liệu tổng hợp, không in nội dung hay header của mail. Không gọi API đọc mail (`/search`, `/threads`…) hay chụp màn hình web trên DB dev `mailassist`: chạy thử bằng DB test và profile giả.
- **Không commit dữ liệu mail thật.** Test chỉ dùng mail đã ẩn danh trong `samples/anonymized/`.
- **Không ghi nội dung mail vào log.** Log chỉ ghi ID, số lượng, thời gian, lỗi.
- Nội dung mail chỉ được gửi ra ngoài qua Claude API hoặc nhà cung cấp AI do người dùng tự nhập ở Cài đặt AI (OpenRouter, 9router…; người dùng cho phép gateway công cộng 2026-10-25 và chịu trách nhiệm với khách), sau khi đã lọc quyền và **che thông tin nhạy cảm** (bắt buộc theo xác nhận NDA 2026-10-19: email, điện thoại, URL, địa chỉ, tên người, tên công ty/tổ chức, số tiền, số hợp đồng/đơn hàng; `backend/app/ai/masking.py`, spec 20). Câu hỏi của người dùng cũng được che. Che lỗi thì không gửi. Embedding, NER và rerank luôn chạy self-host (`ml-service`).
- Không lưu nội dung file đính kèm, chỉ lưu metadata (tên, kích thước, loại).

### Phân quyền
- Mọi truy vấn phục vụ request của người dùng chạy trong phiên DB có Row-Level Security. Chi tiết trong `.claude/rules/database.md`.
- Search và RAG lọc theo quyền **trước** khi đưa dữ liệu cho AI. Tool của search kiểu agent cũng chạy trong phiên của người hỏi.
- Thêm bảng hoặc truy vấn mới liên quan đến mail thì phải kèm test: người ngoài dự án không thấy mail của dự án.

### Xử lý dữ liệu
- **mbox là bản gốc, DB là chỉ mục.** Khóa của mail là `(user_id, message_id_hash)`, không phải offset (offset đổi khi Thunderbird compact).
- **Mỗi người dùng có bản mail riêng, không dùng chung mail giữa người dùng** (quyết định 2026-10-17). Dùng chung theo Message-ID cho phép người biết Message-ID (vd. thấy trong header References) gửi mail giả để chiếm bản thật. Ingest không bao giờ tra hay gắn vào mail của người khác.
- **Ingest phải idempotent** (trong phạm vi một người dùng): gửi lại cùng mail không tạo bản ghi mới, chỉ thêm `message_ownership` nếu chưa có.
- **Mỗi bản mail chỉ gọi AI một lần.** Lưu kèm tên model và phiên bản prompt; chỉ xử lý lại khi chủ động tăng phiên bản. Mail gửi cho nhiều người được xử lý ở bản của từng người (chi phí tăng theo số người nhận). Muốn giảm chi phí bằng cách dùng lại kết quả AI giữa các bản có nội dung giống hệt thì hỏi người dùng trước.
- **Ưu tiên không dùng AI**: gom thread, search từ khóa và bộ lọc không được gọi AI.
- Sửa tay trong `thread_overrides` không bao giờ bị thuật toán gom thread ghi đè.
- Kết quả làm sạch lưu kèm vị trí cắt và `cleaner_version`. Đổi quy tắc làm sạch thì tăng version và chạy lại bằng job.
- Mỗi embedding lưu kèm tên model. Đổi model embedding nghĩa là phải embed lại toàn bộ dữ liệu: hỏi người dùng trước khi làm.

## Cấu hình Claude Code của dự án

| Đường dẫn | Nội dung |
|---|---|
| `.claude/settings.json` | Quyền dùng chung: cho phép lệnh test/lint/build, chặn đọc mail thật, file `review-threads` (`%LOCALAPPDATA%\PMMailAssistant\review`) và `.env`. Khai báo hook nhắc tài liệu |
| `.claude/rules/database.md` | RLS, migration, search. Tự nạp khi làm việc với `backend/app/db/`, `backend/app/search/`, `backend/migrations/` |
| `.claude/rules/test-data.md` | Dữ liệu test và ẩn danh. Tự nạp khi làm việc với test, `samples/`, `eval/`, `data/` |
| `.claude/rules/documentation.md` | Cập nhật tài liệu khi thêm hoặc sửa chức năng (spec, danh mục spec, CHANGELOG, CLAUDE.md), mẫu spec. Luôn được nạp |
| `.claude/hooks/doc_reminder.py` | Hook trong `settings.json` (PostToolUse ghi file vừa sửa, UserPromptSubmit xóa danh sách, Stop kiểm tra): lượt có sửa code (trừ test, dữ liệu sinh ra) mà không sửa file `.md` nào thì nhắc Claude một lần theo `.claude/rules/documentation.md`. Chỉ đọc đường dẫn file, trạng thái lưu ở `%TEMP%\pmmail-doc-reminder\` |
| `.claude/agents/security-reviewer.md` | Subagent rà soát thay đổi theo nguyên tắc NDA và phân quyền. Dùng trước khi commit thay đổi liên quan đến mail, DB, log hoặc gọi dịch vụ ngoài |
| `.claude/skills/survey-profile/` | `/survey-profile`: khảo sát profile Thunderbird cho giai đoạn 0, chỉ thu số liệu tổng hợp |
| `.claude/skills/gen-sample-mail/` | `/gen-sample-mail`: sinh lại profile Thunderbird giả trong `data/thunderbird-profile` (spec 12) |
| `.claude/settings.local.json`, `CLAUDE.local.md` | Thiết lập và ghi chú cá nhân, không commit |

## Quy ước chung

- Tên biến, hàm, bảng, cột bằng tiếng Anh. Giao diện, câu trả lời và bản tóm tắt cho người dùng bằng tiếng Việt.
- Thêm hoặc sửa một chức năng thì cập nhật tài liệu liên quan (spec, danh mục spec, CHANGELOG, CLAUDE.md của thành phần) trong cùng lượt làm việc, trước khi báo xong. Chi tiết và mẫu spec: `.claude/rules/documentation.md`.

## Quyết định chưa chốt

Xem mục 9 của file kế hoạch (nơi đặt server, có lưu bản gốc lên server không, phạm vi xử lý AI lần đầu, quy tắc gán dự án, hệ thống SSO). NDA đã xác nhận 2026-10-19 (được dùng Claude API, phải che 4 nhóm thông tin). Đổi model embedding (vd. sang model nhỏ hơn vì CPU chậm) cũng phải hỏi người dùng. Gặp các điểm này thì hỏi người dùng, không tự quyết.
