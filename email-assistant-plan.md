# Kế hoạch triển khai: PM Mail Assistant

> Trợ lý email dùng AI cho PM dự án outsource Nhật: trích xuất mail từ Thunderbird (mbox), gom thread, theo dõi tiến độ đối ứng, tìm kiếm bằng ngôn ngữ tự nhiên.
> Phiên bản: 0.1 (ý tưởng / lập kế hoạch) · Ngày: 2026-10-16

---

## 1. Bối cảnh và yêu cầu

| Hạng mục | Hiện trạng / yêu cầu |
|---|---|
| Mail client | Thunderbird |
| Mail server | IMAP. Tool **chỉ đọc**, không ghi gì lên server |
| Dữ liệu nguồn | Mỗi folder là 1 file mbox: `Mail\Local Folders\` (chỉ có trên máy) và `ImapMail\<server>\` (cache của server, **đã bật đồng bộ offline**) |
| Dung lượng | **Vài chục GB mỗi người** |
| File đính kèm | **Không cần search nội dung.** Chỉ lưu metadata (tên, kích thước, loại) |
| Người dùng | Ban đầu 1 PM, **mở rộng ra nhiều người** (PM, BrSE, leader) |
| Ngôn ngữ mail | Chủ yếu tiếng Nhật, có tiếng Việt và tiếng Anh |
| Bảo mật | Dữ liệu khách Nhật. AI chạy trên model nội bộ, không đưa mail ra API ngoài (đổi ngày 2026-11-01) |

### Mục tiêu chức năng
1. Trích xuất toàn bộ mail (local và server) và cập nhật tăng dần.
2. Gom mail theo thread, chống trùng lặp.
3. Tìm kiếm theo từ khóa (Nhật/Việt/Anh) và theo ngữ cảnh bằng ngôn ngữ tự nhiên.
4. Phân loại mail, theo dõi trạng thái đối ứng (chờ mình / chờ khách / đã đóng).
5. Hỗ trợ tóm tắt thread và soạn nháp reply (người dùng duyệt trước khi gửi).

---

## 2. Nguyên tắc thiết kế

1. **mbox là bản gốc, DB là chỉ mục và dữ liệu đã xử lý.** Không chép nguyên vài chục GB vào DB. Chỉ lưu metadata, nội dung đã làm sạch và kết quả xử lý.
2. **Xử lý nặng tại máy người dùng, server chỉ nhận dữ liệu gọn.** Collector chạy trên máy từng người: parse, giải mã, làm sạch, rồi chỉ gửi phần text đã làm sạch (khoảng vài % dung lượng gốc) lên server.
3. **Thiết kế cho nhiều người dùng ngay từ đầu.** Mọi bảng đều có khái niệm chủ sở hữu và quyền truy cập, kể cả khi giai đoạn đầu chỉ có 1 người dùng.
4. **Mỗi bản mail xử lý AI đúng 1 lần.** *(Đổi ngày 2026-10-17: ban đầu là "mail gửi cho 5 người chỉ lưu 1 bản". Nay mỗi người dùng có bản riêng, vì dùng chung theo Message-ID cho phép người biết Message-ID chiếm được mail của người khác. Mail gửi cho 5 người được lưu 5 bản và xử lý AI ở từng bản.)*
5. **Ưu tiên không dùng AI.** Gom thread, search từ khóa và lọc đều chạy không cần AI. AI chỉ dùng cho tìm kiếm ngữ nghĩa, phân loại và tóm tắt.
6. **Con người duyệt mọi hành động ra ngoài.** Tool không tự gửi mail.

---

## 3. Kiến trúc tổng thể

```
 Máy người dùng A, B, C...                         Server nội bộ công ty
┌──────────────────────────────┐                ┌────────────────────────────────────────────┐
│ Thunderbird profile (mbox)   │                │  Ingest API ──▶ Queue (Redis/BullMQ)        │
│   ├─ Mail\Local Folders\     │                │                   │                         │
│   └─ ImapMail\<server>\      │                │                   ▼                         │
│            │ (chỉ đọc)       │   HTTPS        │  Workers:                                   │
│            ▼                 │  (text gọn,    │   ├─ Dedupe + Threading                     │
│ Collector Agent              │   nén)         │   ├─ Full-text index                        │
│   ├─ Quét tăng dần           │ ─────────────▶ │   ├─ Embedding (model local, vd bge-m3)     │
│   ├─ Parse MIME, giải mã JP  │                │   └─ AI: phân loại / tóm tắt (model nội bộ) │
│   ├─ Làm sạch quote/chữ ký   │                │                   │                         │
│   └─ State cục bộ (SQLite)   │                │                   ▼                         │
└──────────────────────────────┘                │  PostgreSQL + PGroonga + pgvector           │
                                                │                   │                         │
                                                │  Web App (Next.js) + Query API (agent AI)   │
                                                └────────────────────────────────────────────┘
```

### Lý do chọn mô hình Collector (agent cục bộ) + Server trung tâm
- Local Folders **chỉ tồn tại trên máy từng người**, server không tự đọc được.
- Vài chục GB mỗi người: parse tại chỗ rồi gửi lên phần text đã làm sạch giúp tiết kiệm băng thông và dung lượng server.
- Không cần lưu mật khẩu IMAP của nhân viên trên server.
- Giai đoạn 1 người dùng: chạy cả collector lẫn server trên cùng một máy (Docker). Khi mở rộng chỉ cần dời server lên máy chủ nội bộ, không phải viết lại.

### Công nghệ đề xuất

| Thành phần | Lựa chọn | Ghi chú |
|---|---|---|
| Ngôn ngữ | TypeScript (Node.js) | Thống nhất toàn dự án |
| Collector | CLI Node.js, đóng gói thành `.exe` | Chạy định kỳ bằng Task Scheduler, hoặc chạy nền dưới dạng tray app |
| State của collector | SQLite (file cục bộ) | Lưu offset và kích thước từng file mbox, các mail đã gửi lên |
| Parse mail | `mailparser` + `iconv-lite` | Map ISO-2022-JP / Shift_JIS sang CP932 |
| DB trung tâm | PostgreSQL 16+ | |
| Full-text tiếng Nhật | **PGroonga** | Tìm tốt cả Nhật, Việt và Anh |
| Vector | **pgvector** (kiểu `halfvec` để giảm dung lượng) | |
| Queue | Redis + BullMQ | Xử lý nền: thread, embedding, AI |
| Embedding | `bge-m3` self-host | Self-host để không đưa dữ liệu ra ngoài |
| LLM | Model nội bộ của công ty qua gateway kiểu 9router, nhập ở Cài đặt AI, theo từng tác vụ (mặc định gợi ý: cấp nhỏ cho phân loại và phân tích câu hỏi, cấp vừa cho tóm tắt, trả lời, soạn reply) | Quyết định của người dùng 2026-11-01: không dùng API ngoài, không cần che thông tin. Code chưa đổi, xem spec 28 |
| Web | Next.js | |
| Xác thực | SSO công ty (Microsoft Entra / Google) hoặc LDAP | Áp dụng từ giai đoạn nhiều người dùng |
| Triển khai | Docker Compose → server nội bộ / VPC riêng | |

---

## 4. Mô hình dữ liệu (tóm tắt)

```
users                (id, email, name, role)
mail_sources         (id, user_id, kind[local|imap_cache], folder_path, file_size, last_offset, last_mtime, last_scan_at)

messages             (id, user_id, message_id_hash, message_id, subject, subject_norm,
                      from_addr, to_addrs, cc_addrs, sent_at,
                      in_reply_to, references[], thread_index,
                      body_clean, body_lang, has_attachments, attachments_meta jsonb,
                      thread_id, created_at)
                      -- mỗi người dùng một bản; UNIQUE (user_id, message_id_hash) (đổi 2026-10-17)

message_ownership    (message_id, user_id, source_id, folder, mbox_offset, is_deleted, seen_flag)
                      -- người nào có mail này, ở folder nào, vị trí nào trong mbox

threads              (id, subject_norm, project_id, status, ball_in_court[us|client|internal],
                      due_date, last_message_at, summary, summary_updated_at)
thread_overrides     (thread_id, message_id, action[merge|split], by_user, at)
                      -- sửa tay thì giữ lại, không bị thuật toán ghi đè

projects             (id, code, name, client_domains[], keywords[])
project_members      (project_id, user_id, role)
                      -- đã đổi khi làm (spec 25): projects theo từng người dùng, có bộ lọc
                      --   filter_from/to/cc/subject thay client_domains, keywords; chưa có code;
                      --   chưa có project_members

message_ai           (message_id, category, priority, sentiment, action_items jsonb, model, processed_at)
message_embeddings   (message_id, chunk_no, embedding halfvec(1024))
thread_embeddings    (thread_id, embedding halfvec(1024))

audit_log            (user_id, action, target, at)
```

### Ước lượng dung lượng (giả định 30 GB mbox mỗi người, cần đo lại thực tế)
- Phần lớn dung lượng mbox là file đính kèm và nội dung trích dẫn lặp lại. Sau khi làm sạch, phần text còn lại dự kiến **1–5% dung lượng gốc**.
- Embedding 1024 chiều dạng halfvec: khoảng 2 KB mỗi đoạn. 500.000 đoạn tương đương khoảng 1 GB.
- **Dự kiến DB trung tâm vài GB cho mỗi người dùng.** Mail gửi chung cho nhiều người trong team được lưu ở bản của từng người (đổi 2026-10-17), nên tổng dung lượng tăng theo số người nhận.

---

## 5. Phân quyền khi nhiều người dùng

| Quy tắc | Mô tả |
|---|---|
| Chủ sở hữu | Người dùng luôn xem được bản mail của chính mình (`messages.user_id`; mỗi người một bản, không dùng chung giữa người dùng) |
| Chia sẻ theo dự án | Thread được gán vào dự án X thì thành viên dự án X được xem (quyền chỉ đọc) |
| Mail riêng tư | Mail không thuộc dự án nào (cá nhân, HR...) thì **mặc định chỉ chủ sở hữu xem được** |
| Đánh dấu riêng tư | Người dùng có thể khóa thread để không chia sẻ dù thread thuộc dự án |
| Kiểm soát ở tầng DB | Dùng Row-Level Security của PostgreSQL, không chỉ kiểm tra ở tầng API |
| Search và AI | Search và RAG **chỉ trả về những mail người hỏi có quyền xem**, lọc trước khi đưa dữ liệu cho AI |
| Audit | Ghi log mọi lần xem, tìm kiếm và gọi AI |
| Quy tắc collector | Có thể cấu hình loại trừ folder (ví dụ folder cá nhân) ngay tại máy, không gửi lên server |

---

## 6. Trình tự thực hiện

> Ước lượng thời gian tính cho 1–2 developer. Các giai đoạn 1 đến 4 chưa cần AI.

### Giai đoạn 0: Khảo sát và chuẩn bị (1 tuần)

**Công việc**
- [ ] Liệt kê cấu trúc profile Thunderbird: danh sách folder, kích thước từng file mbox, folder con (`*.sbd`).
- [ ] Lấy mẫu khoảng 50 mail đại diện: ISO-2022-JP, Shift_JIS, UTF-8, HTML-only, mail từ Outlook, mail forward, mail có quote dài.
- [ ] Đếm tổng số mail và tỷ lệ dung lượng của file đính kèm để ước lượng dung lượng DB.
- [x] **Xác nhận chính sách bảo mật:** khách có cho phép gửi nội dung mail qua AI cloud (Claude API) không? Có cần che thông tin nhạy cảm không? *(Kết quả 2026-10-19: được gửi qua Claude API, **bắt buộc che** email/điện thoại/URL/địa chỉ, tên người, tên công ty/tổ chức, số tiền/số hợp đồng. Đổi ngày 2026-11-01: không dùng API ngoài, dùng model nội bộ, không cần che.)*
- [ ] Chốt nơi đặt server (máy cá nhân, server nội bộ hay cloud riêng).

**Đầu ra:** báo cáo khảo sát, bộ mail mẫu dùng làm test, quyết định về bảo mật.

---

### Giai đoạn 1: Collector, trích xuất mbox (2–3 tuần)

**Công việc**
- [ ] Duyệt đệ quy `Mail\Local Folders\` và `ImapMail\<server>\`, bỏ qua `.msf`, đi vào `*.sbd`.
- [ ] Đọc mbox **dạng stream** (file vài GB không được nạp hết vào RAM), tách mail theo dòng `From `.
- [ ] Bỏ mail đã xóa nhưng chưa compact (cờ expunged trong header `X-Mozilla-Status`).
- [ ] Parse MIME và giải mã tiếng Nhật (CP932), lấy phần text. Mail chỉ có HTML thì chuyển HTML sang text.
- [ ] Trích metadata: Message-ID, From/To/Cc, ngày, Subject, In-Reply-To, References, **Thread-Index** (của Outlook), metadata file đính kèm.
- [ ] **Làm sạch nội dung:** cắt quote (`>`, `-----Original Message-----`, `-----元のメッセージ-----`, khối `差出人:`/`送信日時:`, `〜は書きました:`) và chữ ký (đường kẻ `━━━`, `TEL`, `〒`). Lưu cả vị trí cắt để có thể kiểm tra lại.
- [ ] **Quét tăng dần** bằng state SQLite cục bộ:
  - File không đổi: bỏ qua.
  - File lớn hơn: chỉ đọc từ offset cũ.
  - File nhỏ đi (đã compact): quét lại cả folder và đối chiếu theo Message-ID.
  - Mail cuối file đang ghi dở: để lại cho lần quét sau.
- [ ] Mail không có Message-ID: sinh khóa từ hash (From + Date + Subject + đoạn đầu nội dung).
- [ ] Cấu hình loại trừ folder (Trash, Junk, folder cá nhân).
- [ ] Giai đoạn đầu ghi ra file JSONL (nén) để kiểm tra chất lượng trước khi có server.

**Tiêu chí hoàn thành**
- Quét toàn bộ vài chục GB không lỗi, dùng RAM ổn định.
- Số mail trích xuất khớp với số Thunderbird hiển thị (sai lệch dưới 0,5%, có giải thích).
- Không lỗi font tiếng Nhật trên bộ mail mẫu.
- Quét tăng dần khi không có mail mới mất dưới 10 giây.

---

### Giai đoạn 2: Server trung tâm và nạp dữ liệu (2 tuần)

**Công việc**
- [ ] Dựng Docker Compose gồm PostgreSQL (PGroonga, pgvector), Redis, API.
- [ ] Tạo schema theo mục 4, có sẵn `users` và `message_ownership`.
- [ ] Ingest API: nhận theo lô (batch) dữ liệu nén, idempotent (gửi lại cùng mail không bị nhân bản).
- [ ] **Chống trùng lặp** theo `(user_id, message_id_hash)`, trong phạm vi một người dùng: mail người đó đã có thì chỉ thêm bản ghi ownership. Không tra mail của người khác.
- [ ] Collector chuyển từ ghi JSONL sang gửi API, có cơ chế retry và tiếp tục khi bị ngắt giữa chừng.
- [ ] Xác thực collector bằng token theo từng người dùng.
- [ ] **Bỏ chọn folder không xóa mail** (quyết định 2026-10-17): mail đã đồng bộ của folder bị bỏ chọn vẫn giữ trên server, collector chỉ ngừng quét folder đó.
- [ ] **Chức năng chọn xóa mail khỏi server** (quyết định 2026-10-17): người dùng chủ động chọn xóa mail đã đồng bộ của một folder. Chỉ xóa bản mail không còn nằm trong folder nào khác của chính người đó, kèm toàn bộ dữ liệu dẫn xuất (AI, embedding…). Xóa hẳn, không khôi phục; phải xác nhận trước khi xóa.

**Tiêu chí hoàn thành:** import lần đầu toàn bộ dữ liệu 1 người thành công. Chạy lại không sinh bản ghi trùng.

---

### Giai đoạn 3: Gom thread (2 tuần)

**Công việc**: thực hiện theo 4 lớp, độ tin cậy giảm dần:
- [ ] **Lớp 1:** `References` / `In-Reply-To`, dựng cây cha–con theo thuật toán JWZ.
- [ ] **Lớp 2:** `Thread-Index` của Outlook (22 byte đầu là ID cuộc hội thoại). Giúp nối các thread mà khách dùng Outlook làm đứt header.
- [ ] **Lớp 3:** chuẩn hóa subject (bỏ `Re:`, `RE[2]:`, `Fw:`, `返信:`, `転送:`, `【至急】`, khoảng trắng full-width) kết hợp cùng nhóm người tham gia và khoảng thời gian gần nhau.
- [ ] **Lớp 4 (làm sau, ở giai đoạn 5):** dùng embedding và AI để gợi ý gộp các thread rời. Người dùng xác nhận.
- [ ] Hỗ trợ sửa tay (gộp/tách thread), lưu vào `thread_overrides`.
- [ ] Gán dự án tự động theo bộ lọc From, To, CC, Subject do người dùng khai báo cho từng dự án (spec 25, quyết định 2026-10-31).

**Tiêu chí hoàn thành:** kiểm tra ngẫu nhiên 100 thread, tỷ lệ gom đúng ≥ 95%.

---

### Giai đoạn 4: Search từ khóa và Web UI cơ bản (2–3 tuần)

**Công việc**
- [ ] Index PGroonga trên subject và `body_clean`.
- [ ] Bộ lọc: dự án, người gửi, khoảng ngày, folder, có file đính kèm hay không.
- [ ] Web UI:
  - Danh sách thread theo dự án, mở thread xem dạng hội thoại.
  - Ô tìm kiếm, làm nổi bật từ khóa.
  - Nút "Xem bản gốc": collector trên máy chủ sở hữu mở mail gốc bằng offset trong mbox, hoặc hiển thị bản đã làm sạch.
- [ ] Đăng nhập (giai đoạn đầu dùng tài khoản đơn giản, chuẩn bị sẵn cho SSO).

**Tiêu chí hoàn thành:** tìm kiếm trả kết quả dưới 1 giây trên toàn bộ dữ liệu. PM dùng thay cho chức năng search của Thunderbird.

---

### Giai đoạn 5: Tìm kiếm ngữ nghĩa và hỏi đáp bằng AI (3 tuần)

**Công việc**
- [ ] Dựng service embedding `bge-m3` (self-host). Worker tạo embedding cho `body_clean` (cắt đoạn nếu quá dài) và cho bản tóm tắt thread.
- [ ] Chạy embedding cho dữ liệu cũ dần trong nền, **ưu tiên mail mới và dự án đang chạy trước**.
- [ ] **Hybrid search:** kết hợp kết quả PGroonga và pgvector bằng Reciprocal Rank Fusion.
- [ ] **Hỏi đáp bằng ngôn ngữ tự nhiên (RAG):**
  1. Model cấp nhỏ phân tích câu hỏi thành bộ lọc (dự án, ngày, người gửi) và từ khóa, đồng thời tự dịch Việt sang Nhật.
  2. Hybrid search, **lọc theo quyền của người hỏi**, lấy khoảng 20 đoạn liên quan nhất.
  3. Model cấp vừa trả lời bằng tiếng Việt, **kèm link tới mail/thread gốc**.
- [ ] Che thông tin nhạy cảm trước khi gửi AI *(đã làm ở spec 20; từ 2026-11-01 không còn là yêu cầu vì dùng model nội bộ, code bỏ ở spec 28)*.
- [ ] Lớp 4 của gom thread: gợi ý gộp thread bằng embedding.
- [ ] *(Yêu cầu thêm 2026-10-19)* Màn hình cài đặt model AI: 3 cấp Haiku / Sonnet / Opus, chọn theo từng tác vụ (phân tích câu hỏi, trả lời; giai đoạn 6 thêm phân loại, tóm tắt, soạn reply), kèm thống kê token và chi phí ước tính. *(Từ 2026-11-01 model nội bộ chọn bằng tên model do người dùng nhập.)*

**Tiêu chí hoàn thành:** bộ 30 câu hỏi mẫu do PM soạn, ≥ 80% câu trả lời đúng và có trích dẫn nguồn.

---

### Giai đoạn 6: Phân loại và theo dõi đối ứng (3 tuần)

**Công việc**
- [ ] *(Yêu cầu thêm 2026-10-31, làm trước các mục khác)* Tự động dịch mail khách trong Inbox sang tiếng Việt, glossary chung và theo dự án, chuyển mail sang folder dự án trên web (spec 26). Dịch tài liệu, Backlog làm sau bằng spec riêng. Move thật qua IMAP là chức năng riêng, cần đổi nguyên tắc "không ghi lên mail server".
- [ ] Phân loại mail (model cấp nhỏ, structured output): loại (QA, bug, thay đổi spec, lịch, báo cáo, hợp đồng, chỉ thông báo), mức ưu tiên, sắc thái của khách.
- [ ] Trích xuất action item và deadline (ví dụ "来週水曜日までに" chuyển thành ngày cụ thể).
- [ ] Xác định **ball-in-court** của thread: chờ mình / chờ khách / xử lý nội bộ / đã đóng.
- [ ] Tóm tắt thread bằng tiếng Việt, cập nhật khi có mail mới.
- [ ] Dashboard: thread chờ mình, quá hạn, theo dự án. Kanban trạng thái.
- [ ] Nhắc SLA và daily digest buổi sáng.
- [ ] Soạn nháp reply tiếng Nhật (keigo) từ ý chính tiếng Việt, kiểm tra đã trả lời đủ các câu hỏi chưa. Người dùng copy sang Thunderbird để gửi.

**Tiêu chí hoàn thành:** độ chính xác phân loại ≥ 90% trên tập mẫu đã gán nhãn. PM dùng dashboard hằng ngày.

---

### Giai đoạn 7: Mở rộng nhiều người dùng (2–3 tuần)

**Công việc**
- [ ] Đưa server lên máy chủ nội bộ hoặc VPC, bật HTTPS và backup DB định kỳ.
- [ ] Tích hợp SSO công ty và phân vai trò (admin, PM, member).
- [ ] Bật Row-Level Security theo mục 5, kiểm thử phân quyền (người ngoài dự án không thấy mail dự án).
- [ ] Đóng gói collector thành bộ cài, có hướng dẫn cài đặt và cấu hình folder loại trừ.
- [ ] Màn hình quản lý dự án và thành viên, quy tắc gán dự án.
- [ ] Audit log, giám sát chi phí AI theo người dùng và dự án.
- [ ] Triển khai thử với 2–3 người, sau đó mở rộng.

**Tiêu chí hoàn thành:** 3 người dùng chạy song song 2 tuần, không có sự cố lộ dữ liệu giữa các dự án.

---

### Giai đoạn 8 (mở rộng sau)
- Đồng bộ QA表 (Excel / Google Sheet).
- Liên kết thread với Backlog, Redmine hoặc Jira.
- **Đợi, chưa đối ứng** (quyết định của người dùng 2026-10-31): xử lý mail Backlog → Redmine và xử lý mail GitHub review. Chưa làm, chưa viết tài liệu; người dùng sẽ quyết định khi nào thực hiện.
- Báo cáo tuần (週報) tự động.
- Kết hợp dữ liệu task, bug, commit để hỏi đáp về toàn bộ dự án.

---

## 7. Tổng hợp lộ trình

| Giai đoạn | Nội dung | Thời gian | Cần AI? |
|---|---|---|---|
| 1 | Collector trích xuất mbox | 2–3 tuần | Không |
| 2 | Server trung tâm, nạp dữ liệu, chống trùng lặp | 2 tuần | Không |
| 3 | Gom thread | 2 tuần | Không |
| 4 | Search từ khóa và Web UI | 2–3 tuần | Không |
| 5 | Tìm kiếm ngữ nghĩa và hỏi đáp AI | 3 tuần | Có |
| 6 | Phân loại, theo dõi đối ứng, soạn reply | 3 tuần | Có |
| 7 | Mở rộng nhiều người dùng | 2–3 tuần | – |
| **Tổng** | | **khoảng 17–20 tuần** | |

> Có giá trị dùng được sớm: hết giai đoạn 4 (khoảng tuần thứ 9–11) PM đã có công cụ search và xem thread nhanh hơn Thunderbird.

---

## 8. Rủi ro và cách giảm thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Cache IMAP chưa đủ (giới hạn ngày, giới hạn kích thước mail) | Thiếu mail | Đối chiếu số lượng ở giai đoạn 0. Kiểm tra lại cấu hình đồng bộ offline |
| Thunderbird compact làm thay đổi offset trong mbox | Link "xem bản gốc" sai | Phát hiện file nhỏ đi để quét lại. Khóa chính là Message-ID, không phải offset |
| Lỗi encoding tiếng Nhật | Search sai, AI hiểu sai | Bộ mail mẫu đa encoding. Map sang CP932 |
| Cắt quote/chữ ký sai, làm mất nội dung | Thiếu thông tin | Lưu vị trí cắt, có thể xem bản đầy đủ. Kiểm thử trên mail thật |
| Model nội bộ yếu hơn hoặc thiếu GPU | Chậm, tiếng Nhật và keigo kém, output có cấu trúc không ổn định | Đo bằng bộ 30 câu hỏi mẫu trước khi chuyển. Đường lùi `json_object` (spec 24). Chọn model riêng cho từng tác vụ |
| Nhập nhầm URL công cộng ở Cài đặt AI | Mail không che ra ngoài | Ô URL nhận mọi địa chỉ (quyết định 2026-11-01), code không chặn. Màn hình cảnh báo khi host là địa chỉ công cộng. Chỉ nhập gateway nội bộ |
| Lộ mail giữa các dự án hoặc người dùng | Nghiêm trọng | Row-Level Security, lọc quyền trước khi đưa cho AI, audit log, kiểm thử phân quyền |
| Tải model nội bộ khi xử lý dữ liệu cũ | Nghẽn gateway, chậm việc của người dùng | Mỗi bản mail xử lý 1 lần (mail gửi cho nhiều người được xử lý ở bản của từng người). Ưu tiên dữ liệu mới. Chạy nền ngoài giờ. Dùng model nhỏ cho tác vụ hàng loạt |
| Import lần đầu vài chục GB mất nhiều thời gian | Chậm triển khai | Chạy nền, tiếp tục được khi bị ngắt, chạy ngoài giờ làm |

---

## 9. Các quyết định cần chốt

- [ ] Nơi đặt server: máy cá nhân (giai đoạn đầu) → server nội bộ hay cloud riêng?
- [ ] Có lưu bản gốc mail (không gồm file đính kèm) lên server để người khác xem được bản đầy đủ không, hay chỉ lưu bản đã làm sạch?
- [x] Phạm vi xử lý AI khi tự động xử lý mail: chỉ mail từ mốc, là ngày bật lùi 14 ngày, cố định (quyết định 2026-10-31). Mốc theo người dùng hay theo dự án chốt khi làm phân loại AI.
- [x] Danh sách dự án và quy tắc gán: người dùng tự khai báo trên màn hình dự án, bộ lọc From, To, CC, Subject (quyết định 2026-10-31, spec 25).
- [ ] Hệ thống SSO của công ty.
- [x] Nơi chạy AI: model nội bộ qua gateway kiểu 9router, không dùng API ngoài, không cần che thông tin (quyết định 2026-11-01).
- [x] Cấu hình gateway: giữ màn hình Cài đặt AI, người dùng nhập URL, API key, model; ô URL nhận mọi địa chỉ (quyết định 2026-11-01, spec 28).