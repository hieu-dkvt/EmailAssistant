# Spec: Trích xuất mail từ mbox (collector)

Ngày: 2026-10-17 · Thành phần: `collector/internal/mbox`, `collector/internal/parse` · Trạng thái: xong (sửa sau 2 lượt review 2026-10-17: mục 3, 4; `ParserVersion` = `collector/0.4.0`)

## 1. Mục đích

Đọc file mbox của Thunderbird mà không làm phiền Thunderbird, tách từng mail, giải mã (kể cả tiếng Nhật), lấy metadata và text, tạo dữ liệu gửi lên Ingest API. Collector chỉ trích xuất; mọi xử lý nghiệp vụ (cắt quote, chống trùng, gom thread) nằm ở backend.

## 2. Ràng buộc bắt buộc

- **Chỉ đọc, không khóa:** trên Windows mở mbox bằng `CreateFile` với `GENERIC_READ` và share mode `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE` (`os.Open` của Go thiếu `FILE_SHARE_DELETE`, làm hỏng thao tác compact của Thunderbird). Không bao giờ ghi, đổi tên, khóa file trong profile.
- **Stream:** đọc tuần tự, không nạp cả file (file có thể vài GB). Mỗi lần chỉ giữ một mail trong RAM.
- **Không log nội dung mail** (subject, người gửi, body, tên file đính kèm). Log chỉ có đường dẫn folder, offset, số lượng, lỗi.
- Output luôn là UTF-8; backend không tự giải mã charset.

## 3. Tách mail (`internal/mbox`)

- Dòng phân cách: dòng bắt đầu bằng `From ` (5 ký tự) **ở đầu file hoặc ngay sau một dòng trống**. Dòng `>From ` là nội dung (Thunderbird escape), không tách.
- Chấp nhận xuống dòng `\n` và `\r\n`.
- `Reader` đọc từ một offset (phải là đầu một dòng phân cách; nếu không, bỏ qua tới dòng phân cách kế tiếp). Mỗi mail trả về: `Offset` (vị trí dòng `From `), `End` (vị trí ngay sau mail, tức offset của dòng phân cách kế tiếp hoặc cuối file), `Raw` (header + body, không gồm dòng `From `, bỏ dòng trống cuối).
- **Mail cuối file có thể đang ghi dở:** mail kết thúc bởi dòng phân cách kế tiếp là hoàn chỉnh. Mail kết thúc ở EOF chỉ được trả về khi người gọi cho phép (`AllowLastAtEOF`), do bước đồng bộ quyết định theo thời điểm sửa file (xem spec đồng bộ).
- Giới hạn kích thước: mail lớn hơn 64 MiB chỉ giữ 1 MiB đầu (đủ phần header), đánh dấu `Truncated`; phần còn lại đọc bỏ qua.
- Một dòng dài hơn 64 MiB (file hỏng, không có xuống dòng) chỉ giữ 64 MiB đầu trong RAM; phần còn lại bỏ qua nhưng vẫn tính offset.

## 4. Parse (`internal/parse`)

Dùng `github.com/jhillyerd/enmime` (chịu được mail sai chuẩn, giải mã charset bằng `golang.org/x/text`, chuyển HTML sang text).

| Trường gửi lên | Cách lấy |
|---|---|
| `message_id` | header `Message-ID`: phần trong cặp `< >` đầu tiên (bỏ chú thích phía sau); rỗng → `null` |
| `subject` | header `Subject` đã giải mã encoded-word; header gửi thẳng byte tiếng Nhật cũng được giải mã (mục 4.1) |
| `from` | `{name, addr}` từ `From`, `addr` viết thường (mục 4.2) |
| `to`, `cc` | danh sách `{name, addr}` (mục 4.2) |
| `date`, `date_tz_offset_min` | `Date` (RFC 5322, `net/mail.ParseDate`); không đọc được, múi giờ ngoài ±14 giờ, hoặc năm (giờ gốc hoặc UTC) ngoài 1970–2100 → cả hai là `null` |
| `in_reply_to` | phần trong cặp `< >` đầu tiên |
| `references` | tách theo khoảng trắng, bỏ `< >` |
| `thread_index` | header `Thread-Index` giữ nguyên |
| `body_text` | xem mục 4.3; chuẩn hóa xuống dòng `\n`; tối đa 1.000.000 ký tự |
| `body_from_html` | `true` khi không có part `text/plain` là thân mail và text lấy từ HTML |
| `attachments` | `{filename, size, content_type}` của mọi part là file đính kèm theo mục 4.3, theo thứ tự trong mail, cộng các khối uuencode trong thân mail; **không gửi nội dung** |
| `flags` | từ `X-Mozilla-Status` (hex): `0x0001` đã đọc, `0x0002` đã trả lời, `0x0004` gắn sao |
| `mbox_offset` | `Offset` của mail |

- Mail có cờ `0x0008` (expunged, đã xóa chưa compact) bị bỏ qua.
- Mọi chuỗi bỏ ký tự NUL (`\x00`, PostgreSQL không nhận).
- Mail `Truncated` hoặc enmime lỗi: chỉ đọc header bằng `net/mail`, `body_text` rỗng. Một mail lỗi không làm dừng cả folder; chỉ đếm số lỗi.
- Ký tự mở rộng CP932 (①, ㈱, Ⅲ) trong mail khai báo ISO-2022-JP/Shift_JIS phải giải mã đúng (bộ giải mã WHATWG của `x/text` có các ký tự này).
- Parser panic (lỗi lập trình) chỉ làm hỏng mail đó: bước đồng bộ thử lại chỉ với header, vẫn lỗi thì bỏ qua và đếm (spec đồng bộ mục 5).

### 4.1 Charset

- Nhãn charset hay gặp ở mail Nhật mà enmime/`x/text` không biết được đổi về bộ mã gần nhất trước khi giải mã: `MS932`, `x-ms-cp932`, `Windows-932`, `CP943(C)`, `IBM-943`, `SJIS-open`, `SJIS-win` → Shift_JIS (bản WHATWG = CP932); `ISO-2022-JP-MS`, `-1`, `-2`, `-3`, `-2004`, `csISO2022JP2`, `x-windows-iso2022jp` → ISO-2022-JP; `EUC-JP-MS`, `eucJP-win`, `eucJP-open`, `CP51932` → EUC-JP. Áp dụng cho Content-Type của part (qua `SetCustomParseMediaType` của enmime) và encoded-word trong header.
- Header gửi thẳng byte tiếng Nhật (không mã hóa RFC 2047): có ESC → giải mã ISO-2022-JP; không phải UTF-8 hợp lệ → thử Shift_JIS rồi EUC-JP (chỉ nhận khi kết quả không có ký tự lỗi).

### 4.2 Địa chỉ

- Phân tích cả header bằng `enmime.ParseAddressList`. Lỗi thì tách theo `,` và `;` nằm ngoài `"..."`, `<...>`, `(...)` rồi phân tích từng mục; mục vẫn lỗi thì lấy phần `<addr>` (tên là phần trước `<`), hoặc từ đầu tiên có `@`. Mục không có `@` (vd. `undisclosed-recipients:;`) bị bỏ.
- Mục đích: một địa chỉ hỏng (vd. `Tanaka [Sales] <t@x>`, dấu `[ ]` không hợp lệ trong tên) không làm mất cả From/To. Trước khi sửa, cả danh sách bị bỏ.

### 4.3 Thân mail và file đính kèm

- **Quyết định từ header thô của part** (`declaredAttachment`), không từ `FileName`/`Disposition` của enmime: enmime và `mime` của Go bỏ tham số RFC 2231 có charset khác UTF-8 (tên file ISO-2022-JP/Shift_JIS của mailer Nhật) và bỏ `Content-Disposition` sai cú pháp. Part là **file đính kèm** khi:
  - Content-Type hoặc Content-Disposition có tham số tên file: `name`, `filename`, `file`, kể cả dạng `name*`, `name*0*`… và giá trị rỗng; hoặc
  - có Content-Disposition mà không phải `inline` hợp lệ: `attachment`, sai cú pháp, kiểu lạ như `attachement`, `x-attached` (RFC 2183 mục 2.8: kiểu không biết coi là attachment).
- Chỉ đi xuống `multipart/*`. Part khác có part con (vd. `message/rfc822` có tham số `boundary`, bị enmime mở ra) là **một file đính kèm nguyên khối**: text bên trong không phải thân mail. `message/rfc822` và `message/global` luôn là file đính kèm.
- Thân mail = các part `text/plain` không phải file đính kèm, nối bằng `\n--\n` (multipart/alternative: chỉ part đầu). Không có thì part `text/html` đầu tiên (cùng điều kiện), chuyển sang text bằng `html2text`. Mail một part: chỉ là thân mail khi Content-Type là `text/plain`, `text/html` hoặc không có, và không phải file đính kèm.
- Khối **uuencode** trong thân mail (`begin 644 tên`, các dòng mã hóa, `end`; khối cụt không có `end` cắt tới dòng mã hóa cuối) bị cắt khỏi thân mail và ghi thành file đính kèm `{tên, kích thước giải mã, application/octet-stream}`.
- Tên file trong metadata: tự giải mã RFC 2231 (kể cả charset Nhật và dạng tách `filename*0*=…; filename*1*=…`), rồi tới tên enmime đọc được, rồi `filename=`/`name=` thường (giải mã encoded-word hoặc byte thô).
- Đánh đổi: một thân mail hợp lệ mà bị mailer gắn `name=` sẽ mất phần text (thành file đính kèm). Chấp nhận: thà mất text còn hơn gửi nội dung file đính kèm lên server.
- Lịch sử: bản đầu dùng `env.Text` của enmime (gộp part text có tên file vào thân mail). Review lần 1 phát hiện, sửa bằng `FileName`/`Disposition` (0.3.0). Review lần 2 chỉ ra RFC 2231 charset Nhật, disposition sai/lạ, `message/rfc822` có boundary vẫn lọt; sửa bằng header thô (0.4.0). Backend từ chối dữ liệu từ parser < 0.4.0 (spec Ingest API mục 2).

## 5. Kiểm thử

- Unit test với mail dựng trong test: ISO-2022-JP có ①; Shift_JIS có ㈱; header encoded-word; UTF-8 base64/QP; HTML-only; multipart có attachment; dòng `>From ` không bị tách; CRLF; expunged bị bỏ; mail cuối file không có dòng phân cách kế tiếp chỉ trả khi `AllowLastAtEOF`; mail quá lớn bị cắt; NUL bị bỏ.
- Test tích hợp đọc profile tự sinh `data/thunderbird-profile`: tập Message-ID đọc được khớp `data/expected_threads.json` (cộng mail trùng ở Local Folders/Inbox); subject tiếng Nhật giải mã đúng; mail HTML-only có `body_from_html = true`; attachment `estimate_A_v1.xlsx` có metadata.
- Test xác nhận mở file không chặn xóa/đổi tên (Windows): trong lúc reader đang mở, `os.Rename` file đó vẫn thành công.
- Thêm sau review: part text inline có tên file, `text/plain; name=`, `text/html; name=`, `text/csv` attachment, `message/rfc822` → không vào `body_text`, có metadata; HTML có tên không làm thân mail; mail một part có tên file; ngày năm 0001/0000/9999, múi giờ +2400/-1500 → `null`; header byte JIS/Shift_JIS thô; `Tanaka [Sales] <t@x>`, một địa chỉ hỏng giữa danh sách, `undisclosed-recipients:;`; nhãn `MS932`, `x-ms-cp932`, `Windows-932`, `ISO-2022-JP-MS`; Message-ID có chú thích; dòng dài hơn `MaxSize`.
- Thêm sau review lần 2: tên RFC 2231 ISO-2022-JP trong `name*` (multipart, một part, text/html), Shift_JIS trong `filename*` inline, dạng tách `filename*0*`/`*1*` (tên giải mã đúng `見積.txt`); `attachment filename=…` thiếu `;`; `attachement`; `x-attached`; `inline; filename=""`; thân mail `Content-Disposition: inline` không tham số vẫn là thân mail; `message/rfc822; boundary=` không lọt text; khối uuencode (dữ liệu sinh bằng `binascii.b2a_uu`) bị cắt, có metadata 18 byte.
