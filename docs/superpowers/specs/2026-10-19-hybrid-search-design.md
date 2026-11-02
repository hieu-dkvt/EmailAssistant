# Spec: Tìm kiếm ngữ nghĩa (hybrid: PGroonga + pgvector, RRF)

Ngày: 2026-10-19 · Thành phần: `backend/app/search/semantic.py`, `backend/app/search/query.py` (chế độ "một trong các từ", dùng ở spec 21), `backend/app/api/web_routes.py` (`GET /api/v1/search?mode=hybrid`), `web/src/pages/SearchPage.tsx`, `eval/` · Trạng thái: xong

## 1. Mục đích

Tìm mail theo ý nghĩa, không cần gõ đúng từ trong mail: câu hỏi tiếng Việt tìm được mail tiếng Nhật (bge-m3 đa ngôn ngữ), câu tự nhiên ("khách hỏi lại báo giá Project B") tìm được mail liên quan. Kết hợp với search từ khóa để không mất các mail khớp đúng mã, số hiệu (`E1024`, `PRJ-1234`). Không dùng AI cloud: embedding chạy self-host (spec 17).

## 2. Không làm

- Reranker: `/search` không dùng. Spec 17 mục 2 ban đầu không làm; từ spec 27 có `/rerank` (mặc định tắt) nhưng chỉ cho `/ask`.
- Gộp kết quả theo thread: vẫn trả từng mail như search từ khóa.
- Sắp theo ngày ở chế độ hybrid: luôn theo độ liên quan.

## 3. API

`GET /api/v1/search` thêm tham số `mode`: `keyword` (mặc định, như spec 14) | `hybrid`. Ở chế độ `hybrid`:

- `q` bắt buộc (400 `query_required` nếu thiếu). Bộ lọc (folder, người gửi, ngày, đính kèm) áp dụng cho cả hai nhánh. `sort` bị bỏ qua.
- `total` = số mail sau khi gộp (tối đa 200), `total_capped = false`; `offset`/`limit` phân trang trong danh sách đã gộp.
- Mỗi kết quả có thêm `match`: `keyword` | `semantic` | `both`. Đoạn trích: nếu khớp ngữ nghĩa thì là đoạn (`message_chunks`) gần nhất với câu tìm kiếm (200 ký tự đầu của đoạn), có tô từ khóa; nếu chỉ khớp từ khóa thì như spec 14.
- `embedding_pending`: số mail của người dùng chưa có embedding (đếm tối đa 10.001), để web báo "còn N mail chưa được xử lý cho tìm kiếm ngữ nghĩa".
- Lỗi: ml-service không chạy → 503 `ml_unavailable`; model của ml-service khác `PMM_EMBED_MODEL` → 503 `embedding_model_mismatch`.

Schema thay đổi (hợp đồng với web): `SearchHit.match` (mới, null ở chế độ `keyword`), `SearchResult.mode`, `SearchResult.embedding_pending` (mới, null ở chế độ `keyword`). Collector không bị ảnh hưởng.

## 4. Thuật toán (`app/search/semantic.py`)

1. **Nhánh vector** (`vector_chunks`): embed `q` bằng ml-service **trước** khi mở phiên DB (không giữ transaction trong lúc chờ ml-service, có thể vài giây khi worker đang embed). Trong phiên `app_api` + RLS: `SET LOCAL hnsw.ef_search = 200`, `SET LOCAL hnsw.iterative_scan = relaxed_order` (pgvector 0.8: quét tiếp index khi bộ lọc loại bớt kết quả). Hai bước:
   - Bước 1 (`NEAREST_CHUNKS`): chỉ bảng `message_chunks`, `WHERE model = :model ORDER BY embedding <=> :qv LIMIT k`. Truy vấn chỉ một bảng để planner dùng index HNSW: khi JOIN `messages` ngay trong truy vấn này, planner bỏ index và sắp toàn bộ (đã thấy bằng EXPLAIN). Policy RLS là bộ lọc trên chính bảng nên iterative scan vẫn quét đủ k dòng của người dùng.
   - Bước 2: lọc các đoạn bằng `messages` (bộ lọc người dùng chọn, `cleaner_version` = `cleaner_version` của đoạn: bỏ đoạn cũ của mail vừa làm sạch lại) trong cùng phiên RLS. Nếu còn ít hơn số cần mà bước 1 trả đủ k dòng, tăng k ×4 (tối đa 20.000 = `hnsw.max_scan_tuples` mặc định) và làm lại.
   Gộp theo mail: lấy đoạn gần nhất; giữ `BRANCH_LIMIT` mail.
2. **Nhánh từ khóa**: cùng cú pháp ô tìm kiếm (`parse_query(q)`: AND giữa các từ, `OR`, `-từ`, `"cụm"`). Câu tự nhiên hiếm khi có đủ mọi từ trong một mail nên nhánh này thường rỗng (không thêm nhiễu); mã lỗi, số hiệu, cụm chính xác thì khớp và được đẩy lên. Sắp theo `pgroonga_score`, giữ `BRANCH_LIMIT` mail. Chuỗi quá dài cho cú pháp từ khóa (> 500 ký tự, > 20 từ) hay chỉ có từ phủ định: bỏ nhánh này, chỉ tìm theo nghĩa.
   *Đã thử và bỏ*: nhánh "một trong các từ" (`any_term`, OR giữa các từ) kéo về nhiều mail chỉ khớp từ chung ("khách", "của"), làm kết quả kém hẳn (mục 10). `any_term` vẫn dùng ở hỏi đáp (spec 21) cho các từ khóa do Claude chọn.
3. **Gộp bằng Reciprocal Rank Fusion**: `score(mail) = Σ 1 / (RRF_K + hạng)` trên hai nhánh (hạng bắt đầu từ 1), `RRF_K = 60`. Bằng điểm thì mail có id lớn hơn (nạp sau) trước.
4. Lấy nội dung cho các mail của trang (như spec 14: subject có tô, người gửi, folder, thread).

Hằng số: `VECTOR_CHUNKS = 300`, `BRANCH_LIMIT = 100`, kết quả tối đa 200 mail.

## 5. Phân quyền và dữ liệu

- Cả hai nhánh chạy trong `user_session` (`app_api` + RLS) với bộ lọc trong cùng truy vấn; không lọc sau khi lấy kết quả. Join `messages` bằng `(id, user_id)` của đoạn.
- Câu tìm kiếm gửi tới ml-service (cục bộ) để embed; không ghi vào log. Log: `search user=… mode=hybrid total=… ms=…`.
- Vector của câu tìm kiếm truyền vào SQL bằng tham số bind.

## 6. Web

Trang kết quả có lựa chọn "Từ khóa" / "Ngữ nghĩa" (tham số `mode=hybrid` trên URL). Ở chế độ ngữ nghĩa: ẩn ô sắp xếp, mỗi kết quả có nhãn "từ khóa" / "ngữ nghĩa" / "cả hai", hiện số mail chưa được xử lý nếu > 0, báo lỗi rõ khi ml-service không chạy.

## 7. Đánh giá (`eval/`)

- `eval/questions-sample.jsonl`: câu hỏi tiếng Việt/Nhật/Anh trên profile giả (spec 12), mỗi câu ghi thread đúng theo khóa của `data/expected_threads.json` (không chép nội dung mail thật; theo `.claude/rules/test-data.md`).
- `eval/retrieval_eval.py`: nạp `data/thunderbird-profile` vào DB test (người dùng `eval@vn-outsource.example.com`), làm sạch, gom thread, embed (cần ml-service thật), rồi đo hit@1, hit@5, hit@10 và MRR theo thread cho bốn cách: từ khóa AND như ô tìm kiếm (`parse_query` mặc định, sắp theo độ liên quan), từ khóa OR (`any_term`), vector, hybrid. Không chạy cùng lúc với pytest (cùng database test). Kết quả ghi ở mục 10 và `eval/results-sample.md`.

## 8. Kiểm thử

- `parse_query(any_term=True)`: OR giữa các từ, phủ định giữ nguyên, cụm từ.
- RRF: thứ hạng gộp, mail chỉ có ở một nhánh, bằng điểm.
- DB (ml-service giả trả vector định sẵn): hybrid trả mail khớp ngữ nghĩa dù không có từ khóa; `match` đúng; bộ lọc áp dụng cho nhánh vector; đoạn cũ (cleaner_version khác) bị bỏ; `embedding_pending`; phân trang.
- Phân quyền: mail của B có vector gần nhất vẫn không xuất hiện trong kết quả của A.
- `EXPLAIN` trong phiên `app_api` (bước 1 của nhánh vector): dùng được index HNSW `ix_message_chunks_embedding` dưới RLS (bảng test nhỏ nên tắt seqscan và sort để planner chọn index).
- ml-service lỗi → 503 `ml_unavailable`; thiếu `q` → 400.

## 9. Giới hạn đã biết

- Mail chưa embed (worker đang chạy dần, spec 18) chỉ tìm được bằng nhánh từ khóa.
- Embed câu tìm kiếm trên CPU máy dev mất khoảng 0,4 giây mỗi lần (spec 17 mục 8).
- Index HNSW dùng chung cho mọi người dùng: khi có nhiều người, người có ít mail phải quét nhiều đoạn của người khác (bị RLS loại) trước khi đủ kết quả; quá 20.000 đoạn thì dừng và có thể trả ít kết quả hơn. Trước giai đoạn 7 cân nhắc index riêng theo người dùng (partial index / partition).
- Như search từ khóa: thời gian trả lời có thể để lộ thông tin qua kênh phụ khi nhiều người dùng chung index (spec 14 mục 9).

## 10. Kết quả đo

**Chất lượng** (2026-10-20, `eval/retrieval_eval.py`, 1.050 mail giả của spec 12 đã embed bằng bge-m3 thật, 28 câu hỏi trong `eval/questions-sample.jsonl`: 24 câu tự nhiên tiếng Việt/Nhật/Anh + 4 câu kiểu từ khóa/mã; tính theo thread đúng đầu tiên):

| Cách | hit@1 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| Từ khóa AND (ô tìm kiếm, theo độ liên quan) | 0,18 | 0,18 | 0,18 | 0,18 |
| Từ khóa OR | 0,39 | 0,43 | 0,54 | 0,43 |
| Vector | 0,89 | 0,96 | 0,96 | 0,92 |
| Hybrid vector + OR (RRF ngang nhau) | 0,64 | 0,79 | 0,93 | 0,71 |
| Hybrid vector + OR × 0,3 | 0,68 | 0,86 | 0,96 | 0,77 |
| **Hybrid vector + AND (đang dùng)** | **0,89** | **0,96** | **0,96** | **0,92** |

- Từ khóa AND chỉ tìm được 4 câu kiểu từ khóa và 1 câu tiếng Anh trùng chữ trong mail; câu tự nhiên tiếng Việt không bao giờ khớp mail tiếng Nhật.
- Vector tìm đúng thread ở hạng 1 cho 25/28 câu, kể cả hỏi tiếng Việt về mail tiếng Nhật. Câu kém nhất: "Báo giá 45 人月 cho khách A gửi khi nào?" (hạng 14: dữ liệu giả có nhiều thread báo giá na ná nhau).
- Bộ câu hỏi và mail đều giả, ngắn và đều; kết quả trên mail thật cần PM tự đo (tiêu chí giai đoạn 5).

**Tốc độ**: embed câu tìm kiếm khoảng 0,4 giây trên CPU máy dev (spec 17 mục 8); truy vấn DB với 1.050 mail không đáng kể. Chưa đo trên dữ liệu tổng hợp lớn (vài trăm nghìn đoạn).
