---
paths:
  - "backend/app/db/**"
  - "backend/app/search/**"
  - "backend/migrations/**"
---

# Quy tắc DB, migration và search

## Row-Level Security

- Mọi bảng chứa dữ liệu mail hoặc dẫn xuất từ mail (`messages`, `message_ownership`, `threads`, `message_ai`, `message_embeddings`, `thread_embeddings`…) phải bật RLS và có policy.
- Phiên DB phục vụ request người dùng: mở transaction, `SET LOCAL app.user_id = ...` trước mọi truy vấn. Dùng helper chung trong `app/db/`, không tự mở kết nối riêng.
- Role của API không có quyền `BYPASSRLS`. Worker xử lý nền dùng role hệ thống riêng.
- Quy tắc xem:
  - Chủ sở hữu xem được bản mail của mình (`messages.user_id = app_user_id()`). Mỗi người dùng có bản mail riêng, không dùng chung giữa người dùng.
  - Bảng chứa dữ liệu mail: có cột `user_id`; mọi unique index bắt đầu bằng `user_id` (kiểm tra unique không đi qua RLS, nên khóa toàn cục làm lộ sự tồn tại dữ liệu của người khác); FK tới bảng mail khác dùng FK kép kèm `user_id`.
  - Thành viên dự án xem được thread của dự án (chỉ đọc), trừ thread bị khóa riêng tư.
  - Mail không thuộc dự án nào chỉ chủ sở hữu xem được.
- Thêm bảng hoặc policy mới phải kèm test: người ngoài dự án không thấy; thread khóa riêng tư không bị chia sẻ.

## Migration

- Mỗi thay đổi schema là một migration Alembic mới. Không sửa migration đã chạy.
- Index PGroonga, index HNSW của pgvector và policy RLS viết bằng SQL trong migration (`op.execute`), vì autogenerate không sinh được.
- Tạo index trên bảng đã có dữ liệu thì dùng `CREATE INDEX CONCURRENTLY` (chạy ngoài transaction).
- Hàm `SECURITY DEFINER`: `SET search_path = pg_catalog, pg_temp` (pg_temp phải ở cuối, nếu không PostgreSQL tra kiểu và bảng trong pg_temp trước pg_catalog: phiên của role thấp tạo được kiểu giả để chạy code bằng quyền chủ hàm), bảng ghi kèm schema `public.`, `REVOKE ALL … FROM PUBLIC`, chỉ `GRANT EXECUTE` cho role cần. PUBLIC không có quyền TEMP trên database (migration 0013).

## Search

- Từ khóa: toán tử PGroonga `&@~` trên `ARRAY[subject, body_clean]` (index `ix_messages_search`, migration 0012). Biểu thức trong truy vấn phải giống hệt biểu thức index. Chuỗi Groonga chỉ do `app/search/query.py` dựng (mỗi từ bọc `"…"`), không đưa cú pháp Groonga thô của người dùng vào.
- Đã kiểm (2026-10-18): trong phiên `app_api` có RLS, planner vẫn dùng index PGroonga (`Index Cond`), policy là `Filter`.
- Vector: kiểu `halfvec`, số chiều theo model (`bge-m3`: 1024), khoảng cách cosine, index HNSW (`message_chunks`, migration 0015; build lại với `m = 16`, `ef_construction = 128` ở migration 0020, spec 27). Câu `ORDER BY embedding <=> … LIMIT` chỉ chạy trên `message_chunks` (JOIN trong cùng câu làm planner bỏ index HNSW); lọc theo `messages` ở bước sau và lấy thêm ứng viên khi thiếu (`app/search/semantic.py: vector_chunks`). Luôn `SET LOCAL hnsw.iterative_scan = relaxed_order`. Chỉ dùng đoạn có `model` hiện hành và `cleaner_version` khớp `messages`. **Ngoại lệ có chủ đích (spec 27):** khi có bộ lọc người dùng và phạm vi ≤ `EXACT_SCAN_MAX_CHUNKS` (20.000 đoạn) thì `vector_chunks` quét chính xác bằng CTE `MATERIALIZED` có JOIN `messages` và `ORDER BY distance, message_id, chunk_no`, cố ý không dùng index HNSW (vì HNSW lọc sau có thể bỏ sót); đường này vẫn chạy trong `user_session` (RLS) và có test EXPLAIN canh nó không dùng index. Quy tắc "chỉ `message_chunks`" vẫn đúng cho đường HNSW.
- Bảng dẫn xuất từ mail (như `message_chunks`) không chép nội dung mail (chỉ vị trí), FK kép `(message_id, user_id)` `ON DELETE CASCADE`. **Ngoại lệ có chủ đích: `message_translations`** (spec 26) lưu chữ dịch của mail; chấp nhận vì `app_api` ghi và xóa, FK `ON DELETE CASCADE` nên xóa mail của folder làm mất bản dịch ngay, `app_worker` không có quyền đọc, và mọi truy vấn chạy trong `user_session`. Bảng khác muốn chép nội dung mail phải được người dùng duyệt tương tự. `glossary_terms` do người dùng nhập (kể cả thuật ngữ thêm từ đoạn bôi đen) không gắn với mail nên không bị xóa theo.
- Gộp kết quả từ khóa và vector bằng Reciprocal Rank Fusion, không dùng AI.
- Lọc quyền nằm trong cùng truy vấn (qua RLS), không lọc sau khi đã lấy kết quả.
- Truy vấn search mới phải kiểm tra `EXPLAIN ANALYZE` để chắc index được dùng. Mục tiêu dưới 1 giây trên toàn bộ dữ liệu.
