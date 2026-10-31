# Spec: Gom thread

Ngày: 2026-10-17 · Thành phần: `backend/app/mail/subject.py`, `backend/app/mail/threading.py`, `backend/app/processing.py`, migration 0006, 0007, 0009 · Trạng thái: xong (sửa sau review bảo mật: mục 3, 10)

## 1. Mục đích

Gom mail của một người dùng thành thread, để xem dạng hội thoại, search theo thread và (giai đoạn 6) theo dõi trạng thái đối ứng. Không dùng AI. Mục tiêu của kế hoạch: kiểm tra ngẫu nhiên 100 thread, gom đúng từ 95%.

## 2. Không làm

- **Gán dự án tự động:** nằm ở spec 25 (`2026-10-31-project-settings-design.md`).
- API, giao diện xem thread và gộp/tách tay: làm ở giai đoạn 4, spec 15 (`2026-10-18-thread-api-design.md`) và 16. Spec này chỉ có bảng `thread_overrides` và thuật toán tôn trọng nó.
- Lớp 4 (gợi ý gộp bằng embedding): giai đoạn 5.
- **Không bao giờ gom mail của hai người dùng với nhau.** Mỗi người có bản mail riêng, nên cùng một mail ở hai người là hai bản, nằm trong hai thread riêng.

## 3. Lưu trữ (migration 0006)

**`threads`**: một dòng mỗi thread của một người dùng.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity, PK | |
| `user_id` | `bigint` not null → `users` on delete cascade | |
| `message_count` | `integer` not null | |
| `first_message_at`, `last_message_at` | `timestamptz` null | min/max `sent_at` |
| `threader_version` | `smallint` not null | phiên bản thuật toán đã tạo ra dòng |
| `created_at`, `updated_at` | `timestamptz` not null default `now()` | |

- Unique `(id, user_id)` cho FK kép. Index `(user_id, last_message_at)`; từ migration 0014 thay bằng `ix_threads_list (user_id, last_message_at DESC NULLS LAST, id DESC)` cho danh sách thread của web (spec 15).
- **`threads` không chứa nội dung mail** (chỉ số đếm, ngày). Subject của thread là subject của mail có `thread_method = 'root'`, lấy khi đọc. Lý do (review bảo mật): xóa mail của folder chạy bằng `app_api`, không ghi được `threads`, nên bản sao subject sẽ còn lại trên server tới khi worker gom lại. 0006 từng có `subject`, `subject_norm`; 0009 bỏ hai cột này.
- Các cột của giai đoạn sau (`project_id`, `status`, `ball_in_court`, `summary`…) thêm bằng migration ở giai đoạn đó. Dữ liệu có nội dung (vd. tóm tắt) gắn theo thread phải bị xóa cùng lúc khi mail bị xóa (trigger, hoặc lưu theo mail), không chờ worker.

**Thêm vào `messages`:**

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `thread_id` | `bigint` null | FK `(thread_id, user_id)` → `threads(id, user_id)` `ON DELETE SET NULL (thread_id)`. Null = chưa gom |
| `thread_parent_id` | `bigint` null | mail mà mail này trả lời (tổ tiên gần nhất có trong DB). FK `(thread_parent_id, user_id)` → `messages(id, user_id)` `ON DELETE SET NULL (thread_parent_id)` |
| `thread_method` | `text` null | lớp đã đưa mail vào thread: `root`, `references`, `thread_index`, `subject`, `merge` |

Index `(user_id, thread_id)` và `(thread_parent_id)` (cho FK khi xóa mail).

**`thread_overrides`**: sửa tay của người dùng. Tham chiếu mail, không tham chiếu thread, vì id thread có thể đổi khi gom lại.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity, PK | |
| `user_id` | `bigint` not null → `users` on delete cascade | |
| `action` | `text` not null | `split` hoặc `merge` |
| `message_id` | `bigint` not null | FK kép → `messages` on delete cascade |
| `target_message_id` | `bigint` null | chỉ có với `merge`; FK kép → `messages` on delete cascade |
| `created_at` | `timestamptz` not null default `now()` | |

- `split` trên mail M: M và các mail trả lời nó (theo `thread_parent_id`) tách thành thread riêng.
- `merge` (M, T): thread chứa M gộp vào thread chứa T.
- Check: `(action = 'merge') = (target_message_id IS NOT NULL)`, `target_message_id <> message_id`.

## 4. Thuật toán (`THREADER_VERSION = 1`)

Chạy trên toàn bộ mail của **một** người dùng (mọi truy vấn có `user_id = :u`). Cùng dữ liệu thì cùng kết quả. Mail được xét theo thứ tự `(sent_at, id)`, `sent_at` null xếp cuối.

### 4.1 Lớp 1: References / In-Reply-To (JWZ)

- Khóa của mail: Message-ID viết thường. Mail không có Message-ID có khóa riêng theo id, không ai tham chiếu được.
- Danh sách tham chiếu của mail = `references_ids` theo thứ tự, viết thường, bỏ trùng, bỏ chính nó. Nếu có `in_reply_to` thì đưa nó xuống cuối danh sách (In-Reply-To chỉ đúng mail cha trực tiếp).
- Mỗi Message-ID là một "container". Container không có mail trong DB là container ảo (vd. mail cha người dùng không nhận được).
- Với mỗi cặp liền nhau (p, c) trong danh sách tham chiếu: nếu c chưa có cha, c không phải chính mail đang xét, và không tạo vòng → đặt cha của c là p.
- Cha của chính mail = phần tử cuối danh sách (không tạo vòng), ghi đè cha do mail khác đặt.
- Mỗi cây container là một nhóm lớp 1. Hai mail trả lời cùng một mail cha ảo nằm cùng nhóm.
- `thread_parent_id` = tổ tiên gần nhất có mail trong DB (bỏ qua container ảo).

### 4.2 Lớp 2: Thread-Index (Outlook)

- Giải base64 `thread_index` (bỏ khoảng trắng). Hỏng, hoặc ngắn hơn 22 byte → bỏ qua.
- 22 byte đầu là ID cuộc hội thoại. Các nhóm có mail cùng ID được gộp.

### 4.3 Lớp 3: subject + người tham gia + thời gian

Xét từng nhóm C (sau lớp 1, 2) theo thứ tự mail sớm nhất của nhóm. Gọi F là mail sớm nhất của C. C được gộp vào nhóm D khi đủ mọi điều kiện:

1. Subject của F có prefix trả lời/chuyển tiếp (`Re:`, `Fw:`… mục 5), tức F là mail giữa chừng mà không có header nối. Nhóm bắt đầu bằng subject không có prefix (vd. "週次進捗報告" mỗi tuần) không bao giờ bị gộp theo subject.
2. Subject đã chuẩn hóa của F khác rỗng, F có `sent_at` và `from_addr`.
3. D có mail sớm hơn C, và có mail mang cùng subject chuẩn hóa với F.
4. D có mail với `sent_at` trong khoảng `[F.sent_at − 60 ngày, F.sent_at]`.
5. Người gửi F nằm trong tập người tham gia của D (From/To/Cc mọi mail của D), và ít nhất một người nhận (To/Cc) của F cũng nằm trong tập đó.

Nhiều D thỏa → chọn D có mail thỏa điều kiện 4 gần F nhất; bằng nhau thì chọn D có mail sớm nhất có id nhỏ hơn.

### 4.4 Sửa tay (`thread_overrides`)

- `split` M: container của M không có cha, không mail nào khác đặt cha cho nó được. Cây của M (M và các mail trả lời nó) không tham gia lớp 2 và không bị gộp ở lớp 3 (vẫn có thể là đích của lớp 3).
- `merge` (M, T): sau lớp 3, gộp nhóm chứa M với nhóm chứa T, theo thứ tự `created_at`, `id`.
- Thuật toán không bao giờ ghi hay xóa `thread_overrides`. Sửa tay luôn được áp dụng lại sau mỗi lần gom.

### 4.5 `thread_method`

- Mail sớm nhất của thread: `root`.
- Mail cùng cây lớp 1 với mail sớm nhất: `references`.
- Mail thuộc cây được gộp vào: theo lớp đã gộp cây đó lần đầu (`thread_index`, `subject`, `merge`).

Dùng để thống kê chất lượng gom (`mail-stats`) mà không cần đọc nội dung mail.

### 4.6 Giữ id thread ổn định

Gom lại toàn bộ mail của người dùng mỗi lần chạy, rồi ánh xạ nhóm mới sang thread cũ:

- Mỗi cặp (nhóm mới, thread cũ) có số mail chung. Xét các cặp theo số mail chung giảm dần (bằng nhau thì thread cũ id nhỏ hơn trước); nhóm và thread cũ chưa được dùng thì ghép.
- Nhóm không ghép được → thread mới. Thread cũ không ghép được → xóa.
- Hệ quả: mail mới nối hai thread cũ thì thread lớn hơn giữ id, thread kia bị xóa. Giai đoạn 6 (trạng thái, tóm tắt gắn theo thread) phải xử lý trường hợp này.
- Chỉ ghi dòng `messages` có `thread_id`, `thread_parent_id` hoặc `thread_method` thay đổi.
- Cả lần gom của một người dùng là một transaction, có `pg_advisory_xact_lock` theo người dùng (worker và CLI không chạy chồng nhau).

## 5. Chuẩn hóa subject

1. NFKC (full-width → half-width, `：` → `:`, khoảng trắng full-width → khoảng trắng), gộp khoảng trắng, bỏ khoảng trắng đầu/cuối.
2. Lặp lại tới khi không bỏ được gì:
   - prefix trả lời: `Re`, `AW`, `SV`, `TL`, `返信`, `回复`, `答复`;
   - prefix chuyển tiếp: `Fw`, `Fwd`, `WG`, `TR`, `RV`, `CT`, `転送`, `转发`;
   - mỗi prefix có thể kèm `[2]` hoặc `(2)`, theo sau là `:` (cho phép khoảng trắng trước `:`), không phân biệt hoa thường;
   - thẻ mức độ ở đầu: `[至急]`, `【至急】`, `〔至急〕`… với `至急`, `緊急`, `重要`, `要返信`, `要確認`, `再送`, `再々送`, `訂正`, `修正`, `修正版`, `URGENT`, `IMPORTANT`, `Gấp`, `Khẩn`, `Quan trọng`.
3. Viết thường (`casefold`).

Thẻ khác như `【サンプルA】`, `[Project-X]` được giữ: đó là một phần của chủ đề, bỏ đi thì "【A社】定例会議" và "【B社】定例会議" trùng nhau.

Prefix ngoài cùng (prefix đầu tiên bị bỏ) cho biết mail là trả lời hay chuyển tiếp; spec làm sạch (mục 4.3) dùng thông tin này.

## 6. Chạy

- Worker (spec 11) gom lại một người dùng khi có mail chưa gom (`thread_id IS NULL`), khi tổng `message_count` của các thread khác số mail (mail bị xóa), khi có thread của `threader_version` khác, hoặc khi có sửa tay chưa áp dụng (`user_thread_state`, spec 15 mục 6).
- Log chỉ có `user_id`, số mail, số thread, số mail đổi thread, thời gian.
- Bộ nhớ: đọc các cột cần cho gom của mọi mail người dùng (không đọc `body_text`). Với khoảng 500.000 mail cần vài trăm MB; khi vượt quá thì chuyển sang gom tăng dần (ghi ở mục 9).

## 7. Phân quyền

- `threads`, `thread_overrides` bật RLS, policy `SELECT` khi `user_id = app_user_id()`.
- `app_api`: `SELECT` cả hai bảng. Không ghi được `threads`, `messages.thread_*`. Từ migration 0011 (spec 15): `INSERT (user_id, action, message_id, target_message_id)` và `DELETE` trên `thread_overrides`, policy chỉ cho dòng của chính mình; không `UPDATE`.
- `app_worker`: `SELECT, INSERT, UPDATE, DELETE` cả hai bảng.
- Chia sẻ thread theo dự án: giai đoạn có bảng dự án.

## 8. Kiểm thử

- **Golden:** đọc mbox của profile tự sinh `data/thunderbird-profile` (Python `mailbox`, không qua collector), gom, so với `data/expected_threads.json` theo Message-ID: khớp 100%. Gồm mail mất header kiểu Outlook nối được bằng lớp 3 (`A-bug` mail 7), mail `Re:` lẻ không có thread gốc (`S7`), mail chuyển tiếp nội bộ có References, mail trùng ở hai folder.
- **Lớp 1:** cha ảo (hai mail trả lời một mail không có trong DB); References thiếu phần tử cuối nhưng có In-Reply-To; vòng tham chiếu không làm treo hay mất mail; mail không có Message-ID; `thread_parent_id` bỏ qua container ảo.
- **Lớp 2:** Thread-Index 22 byte giống nhau gộp được hai nhóm không có References; Thread-Index hỏng hoặc ngắn bị bỏ qua.
- **Lớp 3 (không gộp nhầm):** subject không có prefix; người gửi không thuộc thread cũ; quá 60 ngày; subject khác sau chuẩn hóa; chọn đúng thread gần nhất khi có hai thread cùng subject.
- **Sửa tay:** `split` tách cả cây trả lời và lớp 2/3 không gộp lại; `merge` gộp hai thread; mail của override bị xóa thì override bị xóa theo.
- **Ổn định:** chạy lại không đổi gì; thêm mail nối hai thread thì thread lớn giữ id; xóa mail thì thread rỗng bị xóa và `message_count` đúng.
- **Phân quyền:** hai người dùng có cùng mail (cùng Message-ID, References) → mỗi người một thread riêng, không mail nào của A nằm trong thread của B; B không thấy thread và override của A qua `app_api`; không đặt `app.user_id` → không thấy gì; `app_api` không ghi được `threads`, `messages.thread_id`, `body_clean`.
- **Chuẩn hóa subject:** `RE[2]:`, `Re: RE: Fw:`, `ＲＥ：`, `返信:`, `【至急】`, `[重要]`, khoảng trắng full-width; giữ `【サンプルA】`.

## 9. Giới hạn đã biết

- Mail trả lời một mail cũ để mở chủ đề mới (giữ header) vẫn nằm chung thread: sửa bằng `split`.
- Gom lại toàn bộ mỗi lần có mail mới: chi phí tỉ lệ với số mail của người dùng. Khi chậm thì chuyển sang gom tăng dần (chỉ nối mail mới vào thread có sẵn, gom toàn bộ khi đổi version).
- Thread bị xóa khi gộp mất id; dữ liệu gắn theo thread ở giai đoạn sau phải được chuyển sang thread giữ lại.
- Xóa mail làm một thread lẽ ra phải tách đôi: được tách ở lần gom kế tiếp. Trong lúc chờ, `threads` còn số đếm và ngày cũ (không có nội dung).
- Cờ "khóa riêng tư" (giai đoạn 4/6) không được gắn theo id thread (id bị tái dùng/xóa khi gom lại, lớp 3 có thể gộp thread riêng tư vào thread dự án): gắn ở mức mail hoặc override, khi gộp thì lấy phía chặt hơn.
- Downgrade 0006 xóa `thread_overrides` (sửa tay của người dùng, không sinh lại được): sao lưu trước khi downgrade khi bảng đã có dữ liệu (từ giai đoạn 4).

## 10. Sửa sau review bảo mật (2026-10-17)

- Bỏ `threads.subject`, `subject_norm` (migration 0009), xem mục 3.
- Không có đường nào lẫn mail giữa người dùng (review đã kiểm: mọi truy vấn của worker lọc `user_id`, FK kép, `assign_thread_ids` chỉ dùng thread của chính người đó; trên DB dev 0 mail trỏ tới thread hoặc mail cha của người khác).
