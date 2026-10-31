# PM Mail Assistant

Trợ lý email dùng AI cho PM dự án outsource Nhật: trích xuất mail từ Thunderbird (mbox), gom thread, theo dõi đối ứng, tìm kiếm bằng ngôn ngữ tự nhiên (Nhật / Việt / Anh).

- Yêu cầu, lộ trình: [email-assistant-plan.md](email-assistant-plan.md) (bảng công nghệ ở mục 3 đã cũ, dùng "Kiến trúc" bên dưới). Spec: [danh mục](docs/superpowers/specs/README.md). Quyết định theo ngày: [CHANGELOG](docs/CHANGELOG.md).
- File này được ưu tiên hơn `d:\App\Management\CLAUDE.md` (file mẫu chung của thư mục cha).

## Trạng thái hiện tại

| Giai đoạn | Đã có | Spec |
|---|---|---|
| 1–3 | Collector đọc mbox, đồng bộ tăng dần, ingest, xóa mail của folder; worker tự làm sạch và gom thread | 1–11 |
| 4 | Web: đăng nhập, danh sách thread, search từ khóa, toàn văn, tách/gộp tay | 13–16 |
| 5 | ml-service, embed nền, search ngữ nghĩa, thread liên quan, hỏi đáp AI có che thông tin, Cài đặt AI, nhà cung cấp riêng | 17–24 |
| 5b | Cấu hình dự án, gán mail theo bộ lọc, nhãn dự án trên danh sách thread | 25 |

- Đã có (spec 25, 2026-10-31): màn hình cấu hình dự án và worker gán mail theo bộ lọc, nhãn dự án trên danh sách thread. Tiếp theo: giai đoạn 6 (phân loại, theo dõi đối ứng, tóm tắt, nháp reply).
- Đang đợi, chưa đối ứng: xử lý Backlog → Redmine và GitHub review. Chưa làm: lọc theo dự án, mở mail gốc trong Thunderbird. Chưa gọi thử Claude thật (máy dev chưa có `ANTHROPIC_API_KEY`). Embed trên CPU máy dev chậm (khoảng 0,3 đoạn/giây).
- Khi dựng khung một thành phần, cập nhật mục "Lệnh" trong CLAUDE.md của thành phần đó.

## Chạy thử toàn bộ

Dưới đây, `<api>` là `uv run --directory backend python -m app.cli`.

1. Bật Docker Desktop, `docker compose up -d`, `uv run --directory backend alembic upgrade head`.
2. `<api> create-user --email <e> --name "<tên>" --role pm` → lấy token; `<api> set-password --email <e>`.
3. Build web (lần đầu và sau mỗi lần sửa web): `npm --prefix web install`, `npm --prefix web run build`.
4. Cửa sổ riêng: `uv run --directory ml-service python -m app.run` (`127.0.0.1:8001`). Lần đầu chạy `uv sync --directory ml-service` và tải model khoảng 2,3 GB. Cần cho search ngữ nghĩa, embed nền, hỏi đáp; thiếu thì chức năng khác vẫn chạy.
5. `uv run --directory backend python -m app.run` (`http://127.0.0.1:8000`). Hỏi đáp AI cần `$env:ANTHROPIC_API_KEY`, hoặc nhập nhà cung cấp ở Cài đặt AI (lưu key cần `$env:PMM_SECRET_KEY`, sinh bằng `<api> gen-secret-key`).
6. Cửa sổ riêng: `uv run --directory backend python -m app.worker`.
7. `collector\collector.exe settings` → chọn profile và folder, Lưu → nhập URL server và token, Lưu kết nối → Đồng bộ.
8. Mở `http://127.0.0.1:8000`. Số liệu: `<api> mail-stats`. Người dùng tự xem thread: `<api> review-threads --email <e>` (file có nội dung mail, Claude Code không đọc).

Profile Thunderbird giả: `data/thunderbird-profile`, sinh bằng `/gen-sample-mail` (spec 12). Claude Code chỉ mở web trên DB test với profile giả.

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
| Collector | [collector/](collector/CLAUDE.md) | Go, 1 file `.exe` |
| Backend API + worker | [backend/](backend/CLAUDE.md) | Python 3.12+, FastAPI, SQLAlchemy 2.0, Procrastinate, SDK `anthropic` |
| ML service | [ml-service/](ml-service/CLAUDE.md) | Python, PyTorch: `bge-m3`, reranker |
| Frontend | [web/](web/CLAUDE.md) | React + Vite + TypeScript |
| Đánh giá, mẫu | `eval/`, `samples/` | Đo recall@k / MRR · `samples/raw/`: mail thật, không commit · `samples/anonymized/`: dùng cho test |
| DB | — | PostgreSQL 16+ với PGroonga, pgvector (image tự build) |

- **Collector chỉ trích xuất**: quét mbox, parse MIME, giải mã charset, HTML sang text, lấy metadata, gửi lên API. Không có logic nghiệp vụ.
- **Backend làm mọi xử lý còn lại**: làm sạch, chống trùng, gom thread, gán dự án, gọi AI. Sửa quy tắc chỉ cần chạy lại job, không phát hành lại collector.
- **Frontend chỉ hiển thị**: client API và kiểu dữ liệu sinh từ OpenAPI của FastAPI.

## Nguyên tắc bắt buộc

### Dữ liệu và bảo mật (NDA)
- **Chỉ đọc** mbox và profile Thunderbird: không khóa, không ghi, không đổi tên. Không ghi gì lên mail server.
- **Không bao giờ tự gửi mail.** Tool chỉ tạo bản nháp, người dùng tự copy sang Thunderbird.
- **Claude Code không đọc mail thật**, vì mọi thứ nó đọc đều gửi tới Anthropic. Không đọc profile Thunderbird hay `samples/raw/`. Lệnh trên dữ liệu thật chỉ in số liệu tổng hợp. Không gọi API đọc mail hay chụp web trên DB dev `mailassist`.
- **Không commit mail thật**, test chỉ dùng mail đã ẩn danh. **Không ghi nội dung mail vào log**: log chỉ có ID, số lượng, thời gian, lỗi.
- Nội dung mail chỉ ra ngoài qua Claude API hoặc nhà cung cấp AI do người dùng nhập ở Cài đặt AI (cho phép gateway công cộng 2026-10-25, người dùng chịu trách nhiệm với khách), sau khi đã lọc quyền và **che thông tin** (bắt buộc theo NDA 2026-10-19: email, điện thoại, URL, địa chỉ, tên người, tên công ty/tổ chức, số tiền, số hợp đồng/đơn hàng; spec 20). Câu hỏi của người dùng cũng được che. Che lỗi thì không gửi. Embedding, NER, rerank luôn self-host (`ml-service`).
- Không lưu nội dung file đính kèm, chỉ lưu metadata.

### Phân quyền
- Mọi truy vấn phục vụ người dùng chạy trong phiên DB có Row-Level Security (`.claude/rules/database.md`).
- Search và RAG lọc quyền **trước** khi đưa dữ liệu cho AI. Tool của search kiểu agent chạy trong phiên của người hỏi.
- Bảng hoặc truy vấn mới liên quan đến mail phải kèm test: người ngoài dự án không thấy mail của dự án.

### Xử lý dữ liệu
- **mbox là bản gốc, DB là chỉ mục.** Khóa mail là `(user_id, message_id_hash)`, không phải offset.
- **Mỗi người dùng có bản mail riêng.** Ingest không bao giờ tra hay gắn vào mail của người khác: dùng chung theo Message-ID cho phép mail giả chiếm bản thật. **Ingest idempotent** trong phạm vi một người: gửi lại chỉ thêm `message_ownership` nếu chưa có.
- **Mỗi bản mail chỉ gọi AI một lần**, lưu kèm model và phiên bản prompt; chỉ xử lý lại khi chủ động tăng phiên bản. Muốn dùng lại kết quả AI giữa các bản giống hệt thì hỏi người dùng trước.
- **Ưu tiên không dùng AI**: gom thread, search từ khóa và bộ lọc không gọi AI.
- Thuật toán gom thread không bao giờ ghi đè sửa tay trong `thread_overrides`.
- Làm sạch lưu kèm vị trí cắt và `cleaner_version`. Đổi quy tắc thì tăng version và chạy lại bằng job.
- Mỗi embedding lưu kèm tên model. Đổi model là embed lại toàn bộ: hỏi người dùng trước.

## Cấu hình Claude Code của dự án

| Đường dẫn | Nội dung |
|---|---|
| `.claude/settings.json` | Quyền dùng chung; chặn đọc mail thật, file `review-threads`, `.env`; khai báo hook nhắc tài liệu. Cá nhân, không commit: `settings.local.json`, `CLAUDE.local.md` |
| `.claude/rules/database.md`, `test-data.md` | RLS, migration, search; dữ liệu test, ẩn danh. Tự nạp theo thư mục đang làm |
| `.claude/rules/documentation.md` | Quy tắc cập nhật tài liệu, mẫu spec. Luôn nạp |
| `.claude/hooks/doc_reminder.py` | Lượt có sửa code mà không sửa `.md` thì nhắc một lần. Chỉ đọc đường dẫn file |
| `.claude/agents/security-reviewer.md` | Rà soát NDA và phân quyền trước khi commit thay đổi về mail, DB, log, dịch vụ ngoài |
| `.claude/skills/` | `/survey-profile`: khảo sát profile, chỉ thu số liệu tổng hợp · `/gen-sample-mail`: sinh lại profile giả (spec 12) |

## Quy ước chung

- Tên biến, hàm, bảng, cột bằng tiếng Anh. Giao diện, câu trả lời, bản tóm tắt cho người dùng bằng tiếng Việt.
- Thêm hoặc sửa chức năng thì cập nhật tài liệu trong cùng lượt, trước khi báo xong (`.claude/rules/documentation.md`).

## Quyết định chưa chốt

Mục 9 của kế hoạch: nơi đặt server, có lưu bản gốc lên server không, hệ thống SSO. Đổi model embedding cũng phải hỏi người dùng. Gặp các điểm này thì hỏi, không tự quyết.
