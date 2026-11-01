# Spec: Embedding mail (cắt đoạn, bảng `message_chunks`, worker)

Ngày: 2026-10-19 · Thành phần: `backend/app/embedding.py`, `backend/app/ml_client.py`, `backend/app/tasks/__init__.py` (task `embed_sweep`, `embed_user`), `backend/app/cli.py` (`embed-mail`, `mail-stats`), migration 0015 · Trạng thái: xong (sửa sau review bảo mật: mục 9)

## 1. Mục đích

Tạo embedding (bge-m3, spec 17) cho nội dung đã làm sạch của mỗi mail, để tìm kiếm ngữ nghĩa (spec 19), hỏi đáp (spec 21) và gợi ý thread liên quan (spec 23). Chạy nền bằng worker, mail mới nhất trước; mail cũ được embed dần.

## 2. Không làm

- Embedding bản tóm tắt thread (`thread_embeddings` trong kế hoạch): chưa có tóm tắt (giai đoạn 6).
- Ưu tiên theo dự án đang chạy: chưa ưu tiên theo dự án (gán dự án đã có từ spec 25 nhưng chưa dùng để xếp hàng embed). Hiện chỉ ưu tiên theo ngày gửi.
- Giới hạn phạm vi theo năm: embedding chạy self-host, không tốn tiền, nên embed toàn bộ (khác với gọi Claude: từ 2026-10-31 AI tự động chỉ xử lý mail từ mốc lùi 14 ngày, áp dụng cho giai đoạn 6).

## 3. Cắt đoạn (`app/embedding.py`, `CHUNKER_VERSION`)

- Đầu vào: `body_clean` (mail chưa làm sạch thì chưa embed). Mỗi đoạn là một khoảng `[start_pos, end_pos)` trên `body_clean` (code point).
- Đoạn dài tối đa `CHUNK_CHARS` ký tự, đoạn sau lặp lại `CHUNK_OVERLAP` ký tự cuối của đoạn trước. Ưu tiên cắt ở cuối dòng, rồi sau `。`/`. `/`! `/`? `, trong nửa sau của đoạn; không có thì cắt cứng.
- Tối đa `MAX_CHUNKS` đoạn mỗi mail (phần sau không được embed; mail dài như log, bảng số liệu).
- `body_clean` rỗng → một đoạn `[0, 0)` (chỉ có subject).
- Text gửi đi embed: `subject (300 ký tự đầu) + "\n" + body_clean[start:end]` (subject giúp đoạn ngắn có ngữ cảnh; cắt vì subject nhận tới 10.000 ký tự, vượt giới hạn 10.000 ký tự của `/embed` thì cả lô lỗi). ml-service cắt phần dư sau `ML_MAX_SEQ_LENGTH` token.
- Giá trị hiện hành ghi ở mục 8 (đo số token trên dữ liệu giả).

## 4. Bảng `message_chunks` (migration 0015)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | bigint | FK `users` |
| `message_id` | bigint | FK kép `(message_id, user_id)` → `messages(id, user_id)` `ON DELETE CASCADE` |
| `chunk_no` | smallint | 0, 1, … |
| `start_pos`, `end_pos` | integer | vị trí trong `body_clean` |
| `model` | text | tên model ml-service trả về (vd. `BAAI/bge-m3`) |
| `chunker_version`, `cleaner_version` | smallint | phiên bản cắt đoạn và phiên bản `body_clean` lúc embed |
| `embedding` | `halfvec(1024)` | |
| `created_at` | timestamptz | |

- Khóa chính `(user_id, message_id, chunk_no)`. Index HNSW `halfvec_cosine_ops` trên `embedding` (bảng mới, tạo trong cùng migration).
- **Không chép nội dung mail**: chỉ có vị trí; đoạn text đọc lại từ `body_clean`. Xóa mail (vd. xóa mail của folder, chạy bằng `app_api`) xóa luôn đoạn và vector qua cascade.
- RLS: policy `user_id = app_user_id()`. `app_api` chỉ `SELECT`; `app_worker` ghi.

## 5. Xử lý nền

- **Mail cần embed**: `body_clean IS NOT NULL` và chưa có đoạn số 0 với `model` = model hiện hành, `chunker_version` = bản hiện hành, `cleaner_version` = `messages.cleaner_version`. Làm sạch lại (tăng `CLEANER_VERSION`) hay đổi cách cắt đoạn (tăng `CHUNKER_VERSION`) thì mail tự được embed lại.
- **Model hiện hành**: biến `PMM_EMBED_MODEL` của backend (mặc định `BAAI/bge-m3`). Worker hỏi `/health` của ml-service; model khác `PMM_EMBED_MODEL` thì **không embed** và log lỗi (đổi model phải embed lại toàn bộ: hỏi người dùng trước, rồi đổi cả hai biến).
- Task `mail:embed_sweep` chạy mỗi phút: liệt kê người dùng có mail cần embed, đẩy `mail:embed_user(user_id)` (khóa hàng đợi `embed:<user>`, không đẩy trùng). `process_user_mail` làm sạch xong mail mới cũng đẩy ngay `embed_user` cho người đó (không chờ phút sau).
- Worker không có client ml-service trong `additional_context` (vd. test cũ) thì hai task này không làm gì.
- Task `embed_user`: lấy lần lượt từng lô `EMBED_BATCH` mail mới nhất trước (`sent_at DESC NULLS LAST, id DESC`), cắt đoạn, gọi `/embed` (tối đa 32 đoạn mỗi lần gọi), trong một transaction: xóa đoạn cũ của các mail đó rồi ghi đoạn mới. Chạy tối đa `EMBED_JOB_SECONDS` giây rồi tự đẩy job tiếp theo cho cùng người dùng nếu còn mail. Worker chạy một job tại một thời điểm, nên job ngắn giữ cho sửa tay thread (mỗi 5 giây) và làm sạch/gom thread không phải chờ lâu.
- Đọc `body_clean` chỉ tới `MAX_CHUNKS × CHUNK_CHARS` ký tự đầu.
- ml-service không chạy: `embed_user` log `embed user=… ml-service unavailable` và dừng; lần quét sau thử lại. Mail đang chờ embed vẫn tìm được bằng từ khóa.
- Idempotent: chạy lại không sinh đoạn trùng (khóa chính; xóa rồi ghi trong cùng transaction).
- CLI `python -m app.cli embed-mail --email <e> [--limit N]`: embed ngay không cần worker (role worker), in tiến độ; ml-service không chạy hay sai model → mã thoát 1 kèm lời nhắc. `mail-stats` có thêm dòng "Embedding (model, cắt đoạn vN): đã embed · chờ embed · số đoạn".

## 6. Phân quyền và dữ liệu

- Worker dùng `app_worker` (BYPASSRLS): mọi truy vấn có `user_id = :u`; chỉ đọc mail của người đang xử lý.
- Text gửi tới ml-service qua `127.0.0.1` (self-host), không ra ngoài.
- Log: `embed user=… messages=… chunks=… ms=…`. Không ghi subject, nội dung. Lỗi bọc bằng `app/safe_errors.py`.

## 7. Kiểm thử

- Cắt đoạn: mail ngắn một đoạn; mail dài cắt ở cuối dòng, có phần lặp; dòng rất dài cắt cứng; giới hạn `MAX_CHUNKS`; `body_clean` rỗng; vị trí khớp text.
- DB (ml-service giả): embed mail mới nhất trước; chạy lại không đổi gì; làm sạch lại (đổi `cleaner_version`) → embed lại; model ml-service khác cấu hình → không ghi; ml-service lỗi → không ghi, không lỗi job; mail chưa làm sạch không embed; chỉ mail của người đang xử lý.
- Worker thật (Procrastinate trên DB test): xử lý mail xong tự embed; ml-service tắt thì job thành công nhưng không ghi gì, bật lại thì `embed_sweep` embed nốt; worker không có ml-service thì bỏ qua.
- Client ml-service (`tests/test_ml_client.py`, transport giả): chia lô 32, model khác → `EmbeddingModelMismatch`, số chiều sai / HTTP lỗi / JSON hỏng → `MlUnavailable` (thông điệp không chứa text), NER bỏ khoảng sai và nhãn lạ.
- RLS: `app_api` của A không thấy đoạn của B; `app_api` không ghi được bảng; xóa mail xóa luôn đoạn.

## 8. Giá trị và kết quả đo

| Hằng số | Giá trị | Lý do |
|---|---|---|
| `CHUNKER_VERSION` | 1 | |
| `CHUNK_CHARS` / `CHUNK_OVERLAP` | 800 / 100 ký tự | bge-m3 trên 1.050 mail giả: 0,37 token/ký tự (p90 0,41, cao nhất 0,57) → 800 ký tự + subject dưới 512 token, không bị cắt |
| `MAX_CHUNKS` | 16 đoạn (~12.800 ký tự đầu) | |
| `EMBED_BATCH` | 16 mail mỗi lô | |
| `EMBED_JOB_SECONDS` | 10 giây mỗi job | |

Đo ngày 2026-10-20 trên máy dev không GPU (spec 17 mục 8): embed 1.050 mail giả khoảng 0,6–0,7 mail/giây.

## 9. Sửa sau review bảo mật (2026-10-21)

- Mail có subject rất dài làm text embed vượt giới hạn của `/embed` → cả lô 16 mail lỗi mãi, mail cũ hơn không bao giờ được embed: cắt subject 300 ký tự (`MAX_SUBJECT_CHARS`).
- `ML_SERVICE_URL` chỉ nhận `http://` tới chính máy này (`127.0.0.1`, `localhost`, `::1`) hoặc `https://`: ml-service nhận text chưa che, không được đi qua HTTP thường tới máy khác.
- Để sau: một mail lỗi khi embed vẫn làm lỗi cả lô (chưa đánh dấu bỏ qua từng mail); với giới hạn độ dài hiện tại chưa thấy trường hợp nào.