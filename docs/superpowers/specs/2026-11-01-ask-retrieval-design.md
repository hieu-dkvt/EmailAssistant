# Spec: Cải thiện truy xuất cho hỏi đáp trên mail

> **Cập nhật 2026-11-02:** spec 28 đã xóa `Masker`, `learn_rows`, `FakeMl.ner_names`, `people`: mọi yêu cầu che trong tài liệu này không còn áp dụng.

Ngày: 2026-11-01 · Thành phần: `backend/app/ai/ask.py`, `backend/app/ai/prompts.py`, `backend/app/search/keyword.py`, `backend/app/search/semantic.py`, `backend/app/ml_client.py`, `backend/app/config.py`, `backend/app/web_schemas.py`, `backend/app/api/ai_routes.py`, `backend/app/db/models.py`, `ml-service/app/` (`reranker.py`, `main.py`, `config.py`, `run.py`), `web/src/pages/AskPage.tsx`, `eval/retrieval_eval.py`, `eval/vector_bench.py`, migration 0020 · Trạng thái: đang làm (đã duyệt spec và kế hoạch, chưa có code)

## 1. Mục đích

Hỏi đáp (spec 21) đang có bốn điểm yếu trên mail:

- Không hỏi được trong phạm vi một dự án, dù mail đã được gán dự án (spec 25).
- AI chỉ thấy đoạn tối đa 800 ký tự. `body_clean` đã cắt quote, nên một reply ngắn không còn câu hỏi mà nó trả lời.
- `vector_chunks` lọc sau khi lấy tối đa 20.000 ứng viên từ HNSW, nên phạm vi nhỏ có thể bị trả thiếu mà không báo lỗi.
- Không có bước xếp lại, thứ hạng chỉ dựa vào RRF.

Tiêu chí thành công: eval có số so sánh trước và sau, người ngoài không thấy mail của người khác, nguồn gửi AI không ít hơn hiện nay, và thời gian chờ tăng có giới hạn (mục 6).

## 2. Không làm

- Bộ lọc dự án trên `/search` và danh sách thread: điều kiện SQL dùng chung nhưng không mở trên API và giao diện của hai màn hình này.
- Dữ liệu ngoài mail (Excel, Redmine), bảng `documents`, phân loại câu hỏi theo nguồn: spec riêng.
- Đổi cách cắt đoạn, đổi model embedding, embed lại.
- Partition `message_chunks`: chỉ cần khi có nhiều người dùng.
- Embedding tóm tắt thread: chờ giai đoạn 6.
- Gửi nhiều mail trong thread: người dùng chọn chỉ gửi mail liền trước (mục 6).
- Bật reranker mặc định: người dùng quyết định sau khi có số liệu eval (mục 10).
- Rerank cho `/search`: chỉ dùng cho `/ask`.

## 3. Lọc theo dự án

- `AskRequest` có thêm `project_id` tùy chọn (`gt=0`, `le=MAX_BIGINT`). Không gửi thì tìm trên toàn bộ mail như hiện nay.
- `ask()` kiểm tra dự án trong `user_session` trước khi gọi ml-service hay AI. Không tìm thấy (không tồn tại hoặc của người khác, RLS ẩn) → 404 `project_not_found`.
- `AskFilters` và `MessageFilters` có thêm `project_id`. `filter_conditions` thêm điều kiện, dùng cho cả nhánh vector và nhánh từ khóa:

  ```sql
  EXISTS (SELECT 1 FROM message_projects mp
          WHERE mp.message_id = m.id AND mp.user_id = m.user_id AND mp.project_id = :project_id)
  ```

- `project_id` là bộ lọc người dùng chọn: nằm trong cả `narrowed` và `base`, không bị nới khi tìm lại không dùng `sender`/ngày của bước phân tích.
- Tên dự án không gửi cho AI (giống spec 26).
- `AskPage` có thêm ô chọn dự án cạnh ô folder, lấy từ `useProjects`, mặc định "Tất cả dự án". Không lưu vào storage trình duyệt.
- Mail mới ingest chỉ vào phạm vi dự án sau khi worker gán xong (spec 25).

## 4. Quét chính xác khi phạm vi nhỏ

Áp dụng trong `vector_chunks` (`app/search/semantic.py`), nên `/search` có bộ lọc và thread liên quan (spec 23) cũng được lợi. Thay đổi với hai chức năng đó chỉ là kết quả đầy đủ hơn.

1. `filter_conditions` không trả điều kiện nào (không có bộ lọc người dùng): đi qua vòng lặp HNSW như hiện nay. `exclude_thread_id` một mình không tính là bộ lọc, vì điều kiện này loại rất ít mail.
2. Có điều kiện: đếm số đoạn trong phạm vi, dừng ở `EXACT_SCAN_MAX_CHUNKS + 1`:

   ```sql
   SELECT count(*) FROM (
     SELECT 1 FROM message_chunks c
     JOIN messages m ON m.id = c.message_id AND m.user_id = c.user_id
     WHERE c.model = :model AND m.cleaner_version = c.cleaner_version AND <điều kiện>
     LIMIT :cap
   ) s
   ```

3. Không quá ngưỡng: quét chính xác. CTE `MATERIALIZED` để `ORDER BY` không dùng được index HNSW, kết quả xác định:

   ```sql
   WITH scope AS MATERIALIZED (
     SELECT c.message_id, c.chunk_no, c.start_pos, c.end_pos,
            c.embedding <=> CAST(:qv AS halfvec(1024)) AS distance
     FROM message_chunks c
     JOIN messages m ON m.id = c.message_id AND m.user_id = c.user_id
     WHERE c.model = :model AND m.cleaner_version = c.cleaner_version AND <điều kiện>
   )
   SELECT * FROM scope ORDER BY distance, message_id, chunk_no LIMIT :k
   ```

4. Quá ngưỡng: vòng lặp HNSW hiện tại (`iterative_scan`, tăng `k` ×4 tới `MAX_CANDIDATES`).

- Đếm một lần cho mỗi lượt `_retrieve` (tối đa 4 vector dùng chung một bộ lọc), không đếm lại cho từng vector.
- Hai câu chạy trong `user_session`, RLS áp dụng cho cả `message_chunks` và `messages`.
- Quy tắc "không JOIN trong câu `ORDER BY embedding <=> … LIMIT`" vẫn đúng cho đường HNSW. Đường quét chính xác cố ý không dùng index.
- `EXACT_SCAN_MAX_CHUNKS = 20_000` là giá trị tạm, chốt bằng `eval/vector_bench.py` (mục 10). Mục tiêu dưới 1 giây mỗi truy vấn. Không đạt thì giảm ngưỡng.

## 5. Index HNSW

Migration 0020 (file riêng, `autocommit_block`, theo mẫu 0007; `down_revision = "0019"`):

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_message_chunks_embedding_new ON message_chunks
  USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 128);
DROP INDEX CONCURRENTLY IF EXISTS ix_message_chunks_embedding;
ALTER INDEX IF EXISTS ix_message_chunks_embedding_new RENAME TO ix_message_chunks_embedding;
```

- Giữ tên index cũ. `models.py` thêm `postgresql_with={"m": 16, "ef_construction": 128}`.
- `m` giữ mặc định 16: tăng `m` làm index lớn hơn, chưa có số liệu cho thấy cần.
- Downgrade tạo lại index với tham số mặc định theo cùng mẫu.
- `ef_search = 200` đã cao nên mức tăng recall dự kiến nhỏ. Số đo trước và sau ghi ở mục 13.
- Thời gian chạy tùy số đoạn và CPU. `CONCURRENTLY` không chặn đọc và ghi. Lỗi giữa chừng để lại index `INVALID` tên `..._new`: xóa bằng `DROP INDEX CONCURRENTLY` rồi chạy lại.

## 6. Ngữ cảnh gửi AI

Người dùng chọn (2026-11-01): gửi cả mail kèm mail liền trước, tổng tối đa khoảng 24.000 ký tự.

### Gộp theo mail

Việc tìm vẫn ở mức đoạn: RRF, rerank, `MAX_PER_MESSAGE = 3`, `CONTEXT_CHUNKS = 20` không đổi. Sau khi chọn đoạn, gộp theo mail: **mỗi mail là một nguồn `[n]`**, thứ tự theo đoạn đứng đầu của mail. Các khoảng chồng hoặc liền nhau của cùng mail được nối lại.

`AskSource` không đổi kiểu dữ liệu. Với cùng câu hỏi, `n` chỉ còn đếm theo mail nên có thể ít nguồn hơn trước nhưng không mất đoạn nào.

### Chia ngân sách

Hai lớp, thực hiện bằng hàm thuần (không chạm DB), tính trên số ký tự thân mail. Header có giới hạn riêng như spec 21.

- **Lớp đảm bảo:** mọi đoạn đã chọn đều được gửi (tối đa 20 × 800 = 16.000 ký tự). Không có đoạn nào đang gửi hiện nay bị mất vì ngân sách mở rộng.
- **Lớp mở rộng:** phần còn lại tới `CONTEXT_MAX_CHARS` chia theo thứ tự nguồn. Với từng nguồn:
  1. Mở rộng trong mail tới `SOURCE_MAX_CHARS`, không vượt phần ngân sách còn lại. Mail không dài hơn giới hạn thì gửi cả mail. Mail dài hơn thì nới đều mỗi khoảng về hai phía rồi nối lại, các phần cách nhau bằng dòng `…`. Phần dư do chạm đầu hoặc cuối mail không chia lại.
  2. Thêm mail trước, lấy `PREVIOUS_MAX_CHARS` ký tự đầu của `body_clean`, không vượt phần ngân sách còn lại.
  3. Phần ngân sách còn lại dưới `MIN_EXPANSION_CHARS` thì dừng, các nguồn sau giữ nguyên đoạn khớp.

Thân mail đọc tối đa `MAX_CHUNKS × CHUNK_CHARS` ký tự đầu, là vùng có thể có đoạn. Vị trí tính theo code point, giống `start_pos`, `end_pos`. Vị trí vượt độ dài thân mail (đoạn cũ, mail vừa làm sạch lại) được cắt về độ dài thật; khoảng nằm hẳn ngoài thân mail thì bỏ, không còn khoảng nào thì coi như `[0, 0)`.

### Mail trước

Lấy cùng phiên với `SOURCES`, một mail trước cho mỗi nguồn:

```sql
SELECT s.id AS source_id, p.id, p.from_name, p.from_addr, p.to_addrs, p.cc_addrs,
       p.sent_at, p.sent_tz_offset_min,
       left(p.body_clean, :previous_max) AS body, left(p.body_text, 20000) AS full_text
FROM messages s
JOIN LATERAL (
  SELECT x.* FROM messages x
  WHERE x.user_id = s.user_id AND x.thread_id = s.thread_id AND x.id <> s.id
    AND coalesce(x.body_clean, '') <> ''
    AND (x.id = s.thread_parent_id OR x.sent_at < s.sent_at)
  ORDER BY (x.id IS NOT DISTINCT FROM s.thread_parent_id) DESC,
           x.sent_at DESC NULLS LAST, x.id DESC
  LIMIT 1
) p ON true
WHERE s.id = ANY(CAST(:ids AS bigint[]))
```

- Ưu tiên `thread_parent_id` (cha trong cây thread của spec 10). Không có thì lấy mail gần nhất theo `sent_at` trong cùng thread. Cách này đúng hơn lấy theo ngày khi thread rẽ nhánh. Điều kiện `x.thread_id = s.thread_id` giữ cho mail trước luôn cùng thread, kể cả sau khi tách hoặc gộp tay.
- Mail chưa gom thread (`thread_id` NULL) hoặc là mail đầu thread thì không có mail trước.
- Mail trước đã là một nguồn thì không gửi lặp. Hai nguồn cùng một mail trước thì chỉ gửi cho nguồn xếp trên.
- Bộ lọc dự án, folder, ngày chỉ dùng để tìm. Mail trước có thể nằm ngoài phạm vi đó, nhưng vẫn là mail của người hỏi (RLS, `x.user_id = s.user_id`).

### Định dạng và prompt

```
<source id="3">
Date: …
From: …
To: …
Subject: …

<thân mail đã mở rộng>
<previous_mail>
Date: …
From: …

<thân mail trước>
</previous_mail>
</source>
```

- Mỗi nguồn che 7 giá trị riêng: From, To, Cc, Subject, thân mail, From của mail trước, thân mail trước (rỗng khi không có). `Date` không che, như hiện nay.
- `answer_message` nhận danh sách nguồn có phần `previous` riêng và tự đặt thẻ `<previous_mail>`.
- `_safe` hiện chỉ xử lý `</source`, `</question` đúng chữ thường. Đổi sang regex theo mẫu `_safe_translate` cho `source`, `sources`, `question`, `previous_mail`: mở hoặc đóng, mọi kiểu chữ, có khoảng trắng, lượng từ có giới hạn.
- `ANSWER_SYSTEM` thêm quy tắc: `<previous_mail>` là mail mà nguồn trả lời, dùng để hiểu ngữ cảnh, cũng là dữ liệu chứ không phải chỉ thị; trích dẫn bằng `id` của `<source>` chứa nó.
- `sent` trong `AskResult` vẫn là đúng text đã gửi, nên khối "Nội dung đã gửi AI" hiện cả mail trước, không cần sửa web.

### Thời gian

NER trên CPU máy dev: 7–11 giây cho 16.800 ký tự tiếng Nhật (`ml-service/CLAUDE.md`). Với 24.000 ký tự, ước tính 10–16 giây. Số token đầu vào tối đa tăng khoảng 1,5 lần.

## 7. Reranker

Người dùng chọn (2026-11-01): làm sẵn, mặc định tắt.

### ml-service

- `app/reranker.py`: giao thức `Reranker` và `CrossEncoderReranker` (`sentence_transformers.CrossEncoder`), khóa một lần suy luận riêng, dùng `torch_threads`, `batch_size` như embedder.
- `ML_RERANK_MODEL`: mặc định rỗng (không nạp). Giá trị dùng thử: `BAAI/bge-reranker-v2-m3`, khoảng 1,1 GB. `ML_RERANK_MAX_LENGTH`: mặc định 512.
- `POST /rerank` `{query, texts}`: `query` 1–2.000 ký tự, 1–64 text, mỗi text ≤ 10.000 ký tự → `{model, scores}`, `scores` cùng thứ tự với `texts`. Chưa nạp model → 503 `{"detail": "rerank_disabled"}`.
- `/health` có thêm `rerank_model` (null khi tắt).
- Cùng quy tắc với `/embed`: không log text, lỗi suy luận đi qua `failed()`, lỗi 422 không trả lại text.
- `run.py` chỉ đặt `HF_HUB_OFFLINE=1` khi cả model embedding và model rerank (nếu có cấu hình) đã ở trong cache. Nếu không, lần đầu bật reranker không tải được model.

### Backend

- `PMM_RERANK=1` mới bật, mặc định tắt. Đọc qua `app/config.py`.
- `MlClient.rerank(query, texts) -> list[float]`, timeout `RERANK_TIMEOUT_SECONDS`, ngắn hơn timeout mặc định 180 giây. Kiểm tra số lượng điểm khớp số text và mọi điểm hữu hạn, sai thì `MlUnavailable`. Lỗi không chứa text.
- Luồng trong `ask`:
  1. Trong `user_session`: tìm và gộp RRF, lấy `RERANK_CANDIDATES` khóa đoạn đứng đầu kèm text. Text = 300 ký tự đầu của subject + `\n` + đoạn, giống `embed_input`.
  2. Ngoài transaction: gọi `rerank` với câu hỏi gốc chưa che. Sắp giảm dần theo điểm, bằng điểm giữ thứ tự RRF.
  3. Áp `MAX_PER_MESSAGE`, `CONTEXT_CHUNKS`, rồi đọc `SOURCES` và mail trước trong `user_session` mới.
- `MlUnavailable` ở bước rerank: dùng thứ tự RRF, không báo lỗi cho người dùng. Log `ask user=… reranked=0`.
- Tắt reranker: bước 1 và 2 không có, luồng giữ nguyên.

## 8. API

| Thay đổi | Ảnh hưởng |
|---|---|
| `POST /api/v1/ask` thêm `project_id` tùy chọn | Web (sinh lại `openapi.json`, `schema.ts`). Client cũ không gửi trường này vẫn chạy |
| Lỗi mới 404 `project_not_found` | Web hiện thông báo theo `errorMessage` |
| `AskResult` giữ nguyên kiểu | Không ảnh hưởng. `sources` theo mail thay vì theo đoạn |

Collector không bị ảnh hưởng. API `/rerank` chỉ dùng nội bộ giữa backend và ml-service.

## 9. Phân quyền và dữ liệu

> **Cập nhật 2026-11-01 (quyết định của người dùng):** AI chỉ chạy trên model nội bộ qua gateway, không dùng API ngoài, không cần che thông tin ([CHANGELOG](../../CHANGELOG.md)). Spec này viết khi `Masker` còn bắt buộc. Spec 27 làm sau [spec 28](2026-11-01-internal-model-design.md), spec 28 đã xong (2026-11-02): `Masker`, `learn_rows`, NER không còn trong code. Các yêu cầu về che và `learn_rows` ở mục 6, 9, 11 và phần "Thời gian" (tính theo NER) là bản viết trước, bỏ khi làm; phần còn lại giữ nguyên.

- Mọi đọc mail (đếm phạm vi, quét chính xác, đoạn, mail trước) chạy trong `user_session` với role `app_api`, RLS áp dụng trước khi có gì gửi đi.
- Không có bảng mới. `message_projects`, `projects` đã có RLS (spec 25).
- **Mail trước phải đi qua `learn_rows` như nguồn chính.** Thiếu bước này thì tên người gửi của mail trước, người gửi mới chưa có trong `correspondents`, và tên công ty trong chữ ký không vào từ điển che. `learn_rows` chạy trên mọi mail trước đã lấy, kể cả mail không được gửi vì hết ngân sách.
- Mọi giá trị của mail trước đi qua `Masker`, cùng bảng nhãn. Che lỗi thì không gửi.
- `/rerank` nhận text chưa che, nên chỉ đi tới ml-service qua `ML_SERVICE_URL` đã kiểm tra (loopback hoặc https), `trust_env=False`.
- Chỉ log số lượng, cờ `reranked`, thời gian. Không log câu hỏi, text, điểm từng đoạn.
- Mail vẫn có thể chứa prompt injection: thẻ được vô hiệu hóa (mục 6), AI vẫn không có tool (spec 21 mục 6).

## 10. Eval và tiêu chí bật reranker

Chạy trên DB test với profile giả, không chạy trên `mailassist`.

`eval/retrieval_eval.py`:

- Thêm cách xếp hạng thứ năm: hybrid + rerank trên 40 đoạn đầu. Đo hit@1, hit@5, hit@10, MRR, thời gian rerank trung bình. ml-service không bật reranker thì bỏ qua dòng này và ghi rõ.
- Thêm recall của `vector_chunks` so với quét chính xác, có và không có bộ lọc.
- Kết quả ghi vào `eval/results-sample.md`.

`eval/vector_bench.py` (mới, chỉ dùng vector ngẫu nhiên, không có nội dung mail): sinh khoảng 100.000 đoạn trên DB test, đo `EXPLAIN ANALYZE` và thời gian của đường HNSW, đường quét chính xác ở các ngưỡng 5.000, 10.000, 20.000 đoạn, và recall với `ef_construction` 64 và 128. Vector ngẫu nhiên khó hơn dữ liệu thật với HNSW, nên số recall chỉ để so sánh tương đối.

**Tiêu chí đề xuất bật reranker** (người dùng quyết định): MRR tăng, hit@5 không giảm, rerank 40 đoạn không quá 10 giây trên máy dev. Báo cáo kèm bộ nhớ ml-service khi có reranker (máy dev hiện tại 15,8 GB RAM, ml-service khoảng 3,2 GB bộ nhớ private sau khi nạp model, đo 2026-11-02 ở spec 28 mục 10).

Giới hạn của eval: 28 câu trên mail giả, mỗi câu chiếm khoảng 0,036 điểm hit@1, nên chỉ mang tính định hướng. Chất lượng câu trả lời và phần ngữ cảnh người dùng tự chấm trên mail thật (30 câu của spec 21). Claude Code không đọc mail thật.

## 11. Kiểm thử

- **Hàm thuần:** gộp đoạn theo mail, nối khoảng chồng và liền nhau; lớp đảm bảo giữ đủ mọi đoạn; ngân sách cấp theo thứ tự, không vượt `CONTEXT_MAX_CHARS`; mail ngắn gửi cả mail; mail dài nới đều, có dòng `…`; dừng dưới `MIN_EXPANSION_CHARS`.
- **Lọc dự án:** chỉ mail của dự án; mail thuộc hai dự án xuất hiện ở cả hai; `project_id` của B → 404, không gọi ml-service, không gọi AI; tìm lại khi không ra kết quả vẫn giữ `project_id`; người ngoài dự án không thấy mail của dự án.
- **Quét chính xác:** ép `MAX_CANDIDATES` nhỏ trong test để đường HNSW bỏ sót, đường quét chính xác vẫn tìm được; quá ngưỡng thì dùng HNSW; không có bộ lọc thì không đếm; đoạn sai `model` hay `cleaner_version` không được chọn; RLS: không thấy đoạn của B.
- **Mail trước:** ưu tiên `thread_parent_id`; không có thì theo `sent_at`; mail đầu thread không có; đã là nguồn thì không lặp; hai nguồn chung một mail trước; `thread_id` NULL.
- **Che thông tin:** giá trị gốc (tên, email, công ty trong chữ ký, số tiền) của mail trước không có trong request gửi AI, kể cả người gửi chưa có trong `correspondents`; mail của B không bao giờ được chọn làm mail trước; `Masker` lỗi thì không gọi AI; thẻ `</previous_mail>`, `<SOURCE id=…>` trong mail bị vô hiệu hóa.
- **Reranker, backend:** thứ tự đổi theo điểm; bằng điểm giữ thứ tự RRF; `/rerank` lỗi, sai số lượng, có NaN → dùng RRF, `/ask` vẫn trả lời, log không có text; `PMM_RERANK` tắt thì không gọi.
- **Reranker, ml-service (model giả):** điểm đúng thứ tự; tắt → 503 `rerank_disabled`; vượt giới hạn → 422 không trả lại text; lỗi suy luận không chứa text; `/health` có `rerank_model`; `run.py` không bật offline khi model rerank chưa có trong cache.
- **Migration:** 0020 chạy trên DB test, index có `m = 16`, `ef_construction = 128`, tên giữ nguyên; downgrade được.
- **Web:** `AskPage` gửi `project_id` khi chọn, không gửi khi chọn "Tất cả dự án"; hiện lỗi `project_not_found`.
- **Log:** `caplog` khẳng định không có text mail hay câu hỏi trong luồng có rerank.

Test không gọi AI thật, dùng `FakeMl`, `FakeLlm`. Trước khi commit chạy `security-reviewer`.

## 12. Giới hạn đã biết

- Phạm vi lọc lớn hơn `EXACT_SCAN_MAX_CHUNKS` vẫn dùng HNSW, nên vẫn có thể thiếu kết quả khi bộ lọc loại nhiều. Chỉ xảy ra với phạm vi lớn, nơi số ứng viên thường đủ.
- Câu hỏi tổng hợp nhiều thread vẫn có thể thiếu nguồn: số đoạn tối đa vẫn là 20 (spec 21 mục 9).
- Lấy phần đầu của mail trước: nội dung quan trọng nằm cuối mail dài có thể không được gửi.
- Chỉ một mail trước: hội thoại nhiều lượt vẫn thiếu các mail xa hơn.
- `thread_parent_id` thay đổi khi gom lại thread, không ảnh hưởng vì không lưu lịch sử hỏi đáp.
- Reranker chạy CPU, cùng tranh tài nguyên với worker embed. Timeout thì quay về RRF.

## 13. Giá trị và kết quả đo

| Hằng số | Giá trị | Lý do |
|---|---|---|
| `CONTEXT_CHUNKS`, `MAX_PER_MESSAGE` | 20, 3 | Không đổi |
| `CONTEXT_MAX_CHARS` | 24.000 | Người dùng chọn 2026-11-01 |
| `SOURCE_MAX_CHARS` | 3.000 | Của người viết spec |
| `PREVIOUS_MAX_CHARS` | 1.200 | Của người viết spec |
| `MIN_EXPANSION_CHARS` | 200 | Của người viết spec: phần nhỏ hơn không đủ nghĩa |
| `EXACT_SCAN_MAX_CHUNKS` | 20.000 (tạm) | Chốt bằng `eval/vector_bench.py` |
| `RERANK_CANDIDATES` | 40 | Của người viết spec: spec 17 đo vài giây cho 50 đoạn |
| `RERANK_TIMEOUT_SECONDS` | 30 | Của người viết spec |
| `ef_construction`, `m` | 128, 16 | Của người viết spec |

Chưa có kết quả đo.

## 14. Phụ thuộc và thứ tự làm

- **Làm sau spec 28**, trực tiếp trên `master`.
- Thứ tự: (1) lọc dự án và quét chính xác, (2) migration index, (3) ngữ cảnh gửi AI, (4) `/rerank` và eval.
- Khi xong phải sửa cho khớp: spec 17 mục 2 và spec 19 mục 2 (đang ghi không có reranker), spec 21 (luồng, nguồn, API), `backend/CLAUDE.md`, `ml-service/CLAUDE.md`, `.claude/rules/database.md`, CHANGELOG, danh mục spec.
