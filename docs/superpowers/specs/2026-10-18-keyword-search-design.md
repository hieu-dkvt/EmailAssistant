# Spec: Search từ khóa (PGroonga, bộ lọc, làm nổi bật từ khóa)

Ngày: 2026-10-18 · Thành phần: `backend/app/search/` (`query.py`, `highlight.py`, `keyword.py`), `backend/app/api/web_routes.py` (`GET /api/v1/search`), `backend/app/run.py` (access log), migration 0012, 0014 · Trạng thái: xong (sửa sau review bảo mật: mục 11)

## 1. Mục đích

PM tìm mail bằng từ khóa tiếng Nhật, Việt, Anh trên subject và nội dung đã làm sạch, lọc theo folder, người gửi, khoảng ngày, file đính kèm, và thấy từ khóa được làm nổi bật. Không dùng AI. Tiêu chí của kế hoạch: trả kết quả dưới 1 giây trên toàn bộ dữ liệu, PM dùng thay cho search của Thunderbird.

## 2. Không làm

- **Lọc theo dự án**: chờ người dùng chốt danh sách dự án và quy tắc gán (mục 9 kế hoạch).
- Tìm kiếm ngữ nghĩa, hybrid với pgvector: giai đoạn 5.
- Tìm không dấu tiếng Việt (`bao gia` → `báo giá`): normalizer hiện tại phân biệt dấu. Gõ đúng dấu như trong mail.
- Tìm trong tên file đính kèm, địa chỉ người nhận.
- Audit log mỗi lần tìm: giai đoạn 7.

## 3. Index (migration 0012)

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_search ON messages
USING pgroonga ((ARRAY[subject, body_clean]))
WITH (tokenizer = 'TokenNgram("unify_alphabet", false, "unify_symbol", false, "unify_digit", false)',
      normalizers = 'NormalizerNFKC150');
```

- `TokenNgram` với `unify_* = false`: chữ Latin và số cũng được cắt bigram, nên tìm một phần từ được (`prj` khớp `PRJ-1234`, `123` khớp `1234`). Tiếng Nhật cắt bigram như mặc định.
- `NormalizerNFKC150`: không phân biệt hoa thường, full-width/half-width (`ＦＵＬＬ` = `full`, `ｶﾀｶﾅ` = `カタカナ`). Không bỏ dấu tiếng Việt.
- Tạo `CONCURRENTLY` trong migration riêng (bảng đã có dữ liệu), như 0007.
- Đã kiểm (2026-10-18, DB test): trong phiên `app_api` có RLS, planner vẫn dùng index (`Index Cond` là điều kiện PGroonga, policy RLS là `Filter`).
- Mail chưa làm sạch (`body_clean` null) chỉ tìm được theo subject cho tới khi worker làm sạch (tối đa khoảng một phút).

## 4. Cú pháp ô tìm kiếm (`app/search/query.py`)

Ô tìm kiếm không đưa thẳng chuỗi người dùng gõ cho Groonga. Backend tự dựng truy vấn Groonga từ các phần sau, để không có lỗi cú pháp và không dùng được cú pháp lạ (`column:value`, `*`…):

| Người dùng gõ | Ý nghĩa |
|---|---|
| `見積 仕様書` | có cả hai từ (AND) |
| `見積 OR 請求` | có một trong hai (chữ `OR` viết hoa, đứng giữa hai từ) |
| `見積 -工数` | có `見積`, không có `工数` |
| `"Project B"` | cụm từ (khoảng trắng nằm trong cụm). Thiếu dấu `"` đóng thì cụm kéo tới cuối |

- Mỗi từ được bọc thành `"…"` (escape `\` và `"`), `-` thành `-"…"`. `OR` ở đầu, cuối, hoặc đứng liền nhau bị bỏ. `-` đứng riêng bị bỏ.
- Chỉ có từ phủ định → 400 `{"code": "query_needs_positive_term"}`. Quá 20 từ hoặc chuỗi dài hơn 500 ký tự → 400 `{"code": "query_too_long"}`.
- Từ khóa để làm nổi bật = các từ không phủ định (bỏ trùng).

## 5. API `GET /api/v1/search`

Xác thực bằng phiên web (spec 13). Tham số (query string):

| Tham số | Kiểu | Ghi chú |
|---|---|---|
| `q` | string, tùy chọn | mục 4. Không có `q` thì chỉ lọc |
| `folder_id` | int | mail đang nằm trong folder này (`message_ownership`, `is_deleted = false`) |
| `sender` | string ≤ 200 | chứa trong `from_addr` hoặc `from_name`, không phân biệt hoa thường (`ILIKE`, escape `%` `_` `\`) |
| `sent_after`, `sent_before` | datetime có múi giờ | `sent_at >= sent_after`, `sent_at < sent_before`. Web đổi ngày theo giờ của trình duyệt |
| `has_attachments` | bool | |
| `sort` | `date` (mặc định) \| `relevance` | `relevance` chỉ khi có `q`: `pgroonga_score` giảm dần, rồi theo ngày |
| `offset` | int 0–10.000 | |
| `limit` | int 1–100, mặc định 50 | |

Kết quả `SearchResult`:

```json
{
  "total": 12, "total_capped": false, "offset": 0, "limit": 50, "keywords": ["見積"],
  "items": [{
    "message_id": 1, "thread_id": 7, "thread_message_count": 6,
    "subject": [{"text": "【サンプルA】在庫管理システム改修のお", "hit": false}, {"text": "見積", "hit": true}, …],
    "from_name": "…", "from_addr": "…", "sent_at": "…", "has_attachments": true,
    "snippet": [{"text": "…お", "hit": false}, {"text": "見積", "hit": true}, …],
    "folders": [{"id": 3, "path": "ImapMail/…/ProjectA"}]
  }]
}
```

- Thứ tự mặc định: `sent_at` giảm dần (null cuối), rồi `id` giảm dần. Mỗi mail là một kết quả; `thread_id` để mở thread và cuộn tới mail.
- `total`: đếm bằng truy vấn riêng cùng điều kiện, tối đa 10.000; còn nhiều hơn thì `total_capped = true` (web hiện "Hơn 10.000 kết quả"). Phân trang cũng chỉ tới offset 10.000.
- Truy vấn trang: subquery chỉ chọn `id`, `sent_at`, điểm, sắp và `LIMIT/OFFSET`; sau đó mới nối lại `messages` lấy nội dung và nối `threads` cho đúng các mail của trang.
- Lọc quyền nằm trong cùng truy vấn qua RLS (`user_session`), không lọc sau khi lấy kết quả. Join `threads` bằng `(id, user_id)` của chính mail.
- Log: `search user=<id> total=<n> ms=<t>`. **Không ghi chuỗi tìm kiếm** (có thể có tên khách, nội dung nghiệp vụ). Access log của uvicorn bỏ query string (`app/run.py`, filter `DropQueryString`): chỉ ghi method, path, mã trạng thái.
- Tô từ khóa và đoạn trích chạy ở thread riêng (`run_in_threadpool`), không chặn event loop.

## 6. Làm nổi bật (`app/search/highlight.py`)

- Trả về danh sách đoạn `{"text", "hit"}` thay vì vị trí ký tự: vị trí trong Python (code point) và JavaScript (UTF-16) lệch nhau khi có ký tự ngoài BMP (vd. kanji 𠮷 trong tên người). Web chỉ ghép các đoạn, `hit = true` hiện bằng `<mark>`.
- So khớp sau chuẩn hóa từng ký tự: `NFKC` rồi `lower()`, giữ bảng ánh xạ về vị trí gốc, nên `ＲＥ`, `ｶﾀｶﾅ` trong mail vẫn được tô khi gõ `re`, `カタカナ`. Văn bản chỉ có ASCII đi đường nhanh (`lower()`).
- Các đoạn khớp chồng nhau được gộp. Chỉ tô trong 300.000 ký tự đầu của một văn bản, phần sau trả nguyên.
- **Đoạn trích (`snippet`)** của kết quả search: tìm lần khớp đầu tiên trong `body_clean` (300.000 ký tự đầu), lấy 60 ký tự trước và 140 ký tự sau, đổi xuống dòng thành khoảng trắng, thêm `…` khi bị cắt. Không khớp trong nội dung (khớp ở subject) → 200 ký tự đầu.

## 7. Phân quyền và dữ liệu

- Truy vấn chạy bằng `app_api` trong `user_session`; không thêm điều kiện `user_id` tường minh để khi có chia sẻ theo dự án, RLS tự mở rộng đúng phạm vi.
- Mọi giá trị đi vào SQL bằng tham số bind; chuỗi Groonga do backend dựng (mục 4), không nhận cú pháp Groonga thô.
- Hàm quản trị PGroonga (`pgroonga_command`…) đã bị thu hồi khỏi PUBLIC: `app_api` không đọc được index Groonga ngoài RLS.
- Kết quả có nội dung mail: response có `Cache-Control: no-store` (spec 16).

## 8. Kiểm thử

- Cú pháp (mục 4): AND, `OR`, `-`, cụm từ, dấu `"` thiếu, `OR` thừa, chỉ có từ phủ định, quá dài, escape `"` và `\`.
- Làm nổi bật: tiếng Nhật, hoa thường, full-width trong mail, ký tự ngoài BMP đứng trước từ khóa, khớp chồng nhau, giới hạn 300.000 ký tự, snippet có `…`.
- DB (profile giả + dữ liệu tự dựng): tìm `見積` ra đúng các mail có từ đó; mỗi bộ lọc; `sort=relevance`; `offset`/`limit`/`total`.
- **Phân quyền**: B có mail chứa cùng từ khóa → kết quả của A không có mail của B và ngược lại; `folder_id` của người khác → 0 kết quả; không có phiên → 401.
- `EXPLAIN` trong phiên `app_api` (RLS): truy vấn có `q` dùng `ix_messages_search`.
- Đo tốc độ: dữ liệu tổng hợp lớn (mục 9) với `EXPLAIN ANALYZE`, ghi kết quả vào mục 10.

## 9. Giới hạn đã biết

- Từ khóa phổ biến (khớp hàng trăm nghìn mail) phải đọc và sắp theo ngày toàn bộ các mail khớp trước khi lấy trang đầu (mục 10: 0,4–0,7 s với 218.000 mail khớp; kèm lọc người gửi 1,1 s).
- Một ký tự kana half-width kèm dấu `ﾞ` (vd. `ｶﾞ`) được chuẩn hóa từng ký tự khi tô, nên có thể không được tô dù search tìm thấy.
- Từ vựng (bigram) của mail đã xóa còn trong lexicon của index Groonga tới khi `REINDEX`; dòng đã xóa được dọn khi `VACUUM`. `app_api` không đọc được lexicon (hàm quản trị đã thu hồi).
- `offset` tối đa 10.000.
- **Kênh phụ thời gian giữa người dùng**: index PGroonga dùng chung, điều kiện `&@~` quét index của mọi người rồi RLS mới lọc. Khi có nhiều người dùng, người B đo thời gian của `count(*)` có thể đoán một từ xuất hiện nhiều trong mail của người khác. Trước giai đoạn 7 cân nhắc index PGroonga nhiều cột có `user_id` để lọc ngay trong Groonga.

## 10. Kết quả đo

Ngày 2026-10-19, máy dev (Windows, PostgreSQL trong Docker Desktop), DB test, **dữ liệu tổng hợp** (không phải mail thật): người A 300.000 mail, người B 100.000 mail (index dùng chung). Mỗi mail 60 từ lấy từ 88 từ Nhật/Việt/Anh, nên mỗi từ "phổ biến" có trong khoảng 73% số mail (trường hợp xấu nhất); từ hiếm có trong 120 mail. Đo qua hàm `search_messages` thật (gồm đếm, trang 50 kết quả, đoạn trích, folder), phiên `app_api` + RLS, trung vị 3 lần chạy khi cache đã ấm:

| Truy vấn | Số mail khớp | Thời gian |
|---|---|---|
| Từ hiếm | 120 | 29 ms |
| Từ phổ biến, mới nhất trước | 218.182 (hiện "hơn 10.000") | 0,70 s |
| Từ phổ biến, liên quan nhất | 218.182 | 0,59 s |
| Hai từ AND | 136.364 | 0,40 s |
| `OR` + `-từ` | 95.455 | 0,50 s |
| Tiếng Việt `báo cáo` | 129.546 | 0,48 s |
| Từ phổ biến + 1 năm | 54.607 | 0,40 s |
| Từ phổ biến + người gửi | 4.772 | **1,09 s** |
| Không từ khóa, chỉ lọc đính kèm | 33.333 | 0,22 s |
| Danh sách thread trang đầu (100.000 thread) | — | 9 ms |
| Danh sách thread lọc người gửi | — | 0,44 s |

- Lần chạy đầu ngay sau khi nạp dữ liệu (cache lạnh) chậm hơn nhiều (tới 3,7 s).
- DB dev (4.792 mail thật, chỉ in số lượng và thời gian): 5 từ khóa thử khớp 113–649 mail, mỗi lần search 16–34 ms; trang đầu danh sách thread 16 ms.
- Trước khi tối ưu: từ phổ biến 1,1–1,5 s, danh sách thread 0,43 s. Đã sửa: (1) đếm tối đa 10.000 mail (`total_capped`), (2) truy vấn trang sắp và cắt 50 id trên cột hẹp trước rồi mới lấy nội dung và nối `threads` (trước đó planner nối `threads` và đọc nội dung cho cả 218.000 dòng), (3) index `ix_threads_list` khớp thứ tự `last_message_at DESC NULLS LAST, id DESC` (migration 0014), (4) cờ đính kèm của thread bằng `LATERAL … LIMIT 1` thay cho `EXISTS` (bị đổi thành subplan quét cả bảng).
- Chưa đạt mục tiêu dưới 1 giây: từ phổ biến kèm lọc người gửi (lọc `ILIKE` chạy trên từng mail khớp). Cải thiện sau nếu cần: index PGroonga cho người gửi, hoặc index PGroonga nhiều cột (`user_id`, `sent_at`, nội dung) để lọc trong Groonga (giải quyết luôn kênh phụ thời gian ở mục 9).

## 11. Sửa sau review bảo mật (2026-10-19)

- **Nghiêm trọng: access log của uvicorn ghi cả query string** (`GET /api/v1/search?q=…&sender=…`), trái với quy tắc không ghi chuỗi tìm kiếm. Thêm filter cho logger `uvicorn.access`, chỉ giữ path; có test.
- Tô từ khóa, đoạn trích chuyển sang thread riêng (một từ khóa một ký tự trên nhiều mail dài từng chặn event loop vài giây).
- Ghi giới hạn kênh phụ thời gian (mục 9).
