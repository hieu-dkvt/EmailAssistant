# Spec: Làm sạch nội dung mail (cắt quote, chữ ký)

Ngày: 2026-10-17 · Thành phần: `backend/app/mail/cleaning.py`, `backend/app/processing.py`, migration 0006 · Trạng thái: xong (sửa sau review bảo mật: mục 8)

## 1. Mục đích

`messages.body_text` là toàn văn: phần người gửi tự viết, phần trích dẫn mail trước và chữ ký. Search, embedding và AI cần phần tự viết (`body_clean`). Nếu không cắt, thread 20 mail mà mail nào cũng trích dẫn lại mail trước thì một từ khóa khớp cả 20 mail.

Làm sạch chạy ở backend (worker, spec 11), lưu vị trí cắt và `cleaner_version`. Đổi quy tắc thì tăng `CLEANER_VERSION`; worker tự làm sạch lại mọi mail có version khác.

## 2. Không làm

- Không sửa hay xóa `body_text`. Toàn văn luôn còn để xem lại và chạy lại.
- Không phát hiện ngôn ngữ (`body_lang`): làm khi search hoặc AI cần.
- Không cắt lời chào và câu kết (お世話になっております, よろしくお願いいたします…): đó là nội dung.
- Không cắt nội dung được chuyển tiếp (mục 4.3).
- Không dùng AI.

## 3. Lưu trữ (migration 0006)

Thêm vào `messages`:

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `body_clean` | `text` null | `body_text` đã bỏ các đoạn cắt |
| `clean_cuts` | `jsonb` null | `[{"kind": "quote" \| "signature", "start": int, "end": int}]` |
| `cleaner_version` | `smallint` null | phiên bản quy tắc đã dùng |

- Chưa làm sạch: cả ba cột null.
- Vị trí tính theo ký tự (code point) của `body_text`, nửa mở `[start, end)`, tăng dần, không chồng nhau. `body_text[start:end]` trong Python bằng `substr(body_text, start + 1, end - start)` trong PostgreSQL.
- `body_clean` = `body_text` bỏ các đoạn cắt, rồi bỏ khoảng trắng cuối mỗi dòng (kể cả `\r`), gộp nhiều dòng trống liên tiếp thành một, bỏ dòng trống ở đầu và cuối. Cắt đoạn ở giữa thường để lại hai dòng trống kề nhau, nên gộp về một.
- `app_api` chỉ đọc ba cột này (quyền INSERT theo cột của 0005 không gồm chúng). Chỉ `app_worker` ghi.

## 4. Quy tắc (`CLEANER_VERSION = 1`)

Xử lý theo dòng (tách ở `\n`, giữ `\r` nếu có). Dòng trống là dòng chỉ có khoảng trắng (kể cả `　`). "Dòng quote" là dòng mà sau khoảng trắng đầu dòng là `>`.

### 4.1 Mốc lịch sử

Tìm mốc đầu tiên trong mail, thuộc một trong bốn loại:

- **a. Dòng giới thiệu trích dẫn.** Xét từng dòng, và từng cặp hai dòng liền nhau ghép lại (Gmail gấp dòng dài ở khoảng 78 ký tự). Là mốc nếu:
  - kết thúc bằng `wrote:`, `đã viết:`, `書きました:` / `書きました：` / `書きました`, và có địa chỉ mail hoặc ngày/giờ; hoặc
  - kết thúc bằng `:` / `：`, có địa chỉ mail **và** có ngày/giờ. Đây là dạng Gmail tiếng Nhật, chiếm đa số trên dữ liệu thật: `2026年10月1日(水) 10:00 田中 <t@x>:`.

  Ngày/giờ: `\d{4}年\d{1,2}月\d{1,2}日`, `\d{4}[/-]\d{1,2}[/-]\d{1,2}`, `\d{1,2}/\d{1,2}/\d{2,4}`, `\d{1,2}:\d{2}`, tên thứ hoặc tháng tiếng Anh viết tắt, `thg` (Gmail tiếng Việt).
- **b. Dòng phân cách:** `-----Original Message-----`, `-----元のメッセージ-----` (từ 2 gạch, không phân biệt hoa thường, khoảng trắng tùy ý).
- **c. Khối header kiểu Outlook:** dòng `From:` / `差出人:` / `送信者:` / `Từ:` (cho phép `*` bao tên trường, do HTML in đậm), và trong 6 dòng tiếp theo có cả dòng `Sent:` / `Date:` / `送信日時:` / `日付:` / `Gửi:` / `Ngày:` lẫn dòng `Subject:` / `件名:` / `Chủ đề:` / `Tiêu đề:`. Nếu ngay trên khối (bỏ qua dòng trống) là dòng `_` dài từ 20 ký tự hoặc dòng phân cách b, mốc bắt đầu từ dòng đó.
- **d. Mốc chuyển tiếp:** dòng `---------- Forwarded message ---------` (Gmail), `-------- Forwarded Message --------`, `-------- 転送メッセージ --------` (Thunderbird), `Begin forwarded message:` (Apple Mail). Số gạch từ 2, không phân biệt hoa thường.

### 4.2 Trích dẫn (quote)

Khi mốc đầu tiên M thuộc loại a, b hoặc c và mail không phải chuyển tiếp (4.3):

| M | Phần sau M | Cắt |
|---|---|---|
| b hoặc c (Outlook, không có `>`) | bất kỳ | từ M tới hết mail |
| a | chỉ có dòng quote và dòng trống | từ M tới hết mail (trả lời ở trên) |
| a | không có dòng quote nào (HTML chuyển sang text mất `>`) | từ M tới hết mail |
| a | có dòng quote, sau đó còn dòng nội dung khác | chỉ dòng M và từng khối dòng quote (trả lời xen kẽ) |

Ngoài vùng đã cắt, mọi khối dòng quote liên tiếp cũng bị cắt (trả lời xen kẽ không có dòng giới thiệu).

### 4.3 Chuyển tiếp

Mail là chuyển tiếp khi mốc đầu tiên thuộc loại d, hoặc thuộc loại b/c mà prefix ngoài cùng của subject là `Fw:` / `Fwd:` / `転送:` (chuẩn hóa theo spec gom thread mục 5).

- Giữ nguyên từ mốc tới hết mail. Chỉ làm sạch phần trước mốc (dòng quote và chữ ký).
- Lý do (quyết định của người viết spec): nội dung chuyển tiếp thường không có ở chỗ khác trong hộp thư (vd. mail khách chuyển cho mình); cắt đi thì search không tìm thấy. Đánh đổi: chuyển tiếp một mail đã có trong hộp thư thì search thấy nội dung đó hai lần, thường trong cùng thread.

### 4.4 Chữ ký

"Đoạn tự viết" là khoảng liên tục không bị cắt ở 4.2 và nằm trước mốc chuyển tiếp. Chữ ký chỉ được tìm trong đoạn tự viết. Điểm bắt đầu chữ ký là dòng sớm nhất thỏa mãn một trong các điều kiện sau:

1. Dòng `--` hoặc `-- ` (RFC 3676; dữ liệu thật có 1.060/4.792 mail).
2. Dòng kẻ: từ 6 ký tự trong `━─═=＝-_＿*＊~～・◆◇■□●○☆★`, không có chữ, và trong 6 dòng sau có dấu hiệu liên hệ (`TEL`, `FAX`, `Mail`, `〒`, `URL`, `http`, `携帯`, `Mobile`, `Phone`, `Điện thoại`, `株式会社`, `Co., Ltd`…).
3. Chân trang di động: `Sent from my …`, `…から送信`, `Gửi từ … của tôi`, `Get Outlook for …`.
4. Khối liên hệ ở cuối đoạn tự viết: dòng có tên công ty (`株式会社`, `有限会社`, `㈱`, `Co., Ltd`, `Inc.`, `Corporation`, `Công ty`…), từ đó tới hết đoạn không quá 8 dòng, mỗi dòng không quá 80 ký tự, và có thêm ít nhất một dòng `TEL` / `FAX` / `Mail` / `〒` / `URL` / `Phone`.

Điều kiện chung:
- Trước điểm bắt đầu phải còn nội dung tự viết không trống, ở bất kỳ đoạn nào (không cắt hết mail).
- Từ điểm bắt đầu tới hết đoạn chứa nó có không quá 15 dòng không trống.

Cắt từ điểm bắt đầu tới hết đoạn đó.

## 5. Chạy

- Worker (spec 11) làm sạch mail có `cleaner_version IS DISTINCT FROM CLEANER_VERSION`, theo từng người dùng, mỗi lô 500 mail một transaction.
- Log chỉ có `user_id`, số mail, thời gian. Không ghi subject hay nội dung.
- Lỗi khi làm sạch một mail (bug) → log id mail và tên lớp lỗi, ghi `body_clean = body_text`, `clean_cuts = []`, `cleaner_version` hiện hành, đếm `failed`. Một mail lỗi không chặn cả lô. Sửa bug thì tăng version để chạy lại.

## 6. Kiểm thử

- Test với mail tự bịa (không dùng mail thật), mỗi case gồm input, subject, `body_clean` mong đợi và loại đoạn cắt:
  - Gmail EN / JP / VN có dòng giới thiệu bị gấp 2 dòng; Thunderbird `… wrote:`; Outlook EN/JP (khối header, `-----Original Message-----`, dòng `_`).
  - Trả lời xen kẽ (giữ câu trả lời, bỏ dòng quote); dòng quote không có dòng giới thiệu.
  - Chuyển tiếp: Gmail, Thunderbird JP, Outlook với subject `FW:`; reply của một mail chuyển tiếp (`Re: Fwd:`) thì cắt như quote.
  - Chữ ký: `--`, dòng kẻ + TEL, chân trang di động, khối công ty + TEL không có dòng kẻ, chữ ký nằm sau quote.
  - Không cắt nhầm: dòng kẻ phân mục trong nội dung không có liên hệ; câu kết thúc bằng `:` không có mail/ngày; `--` ở đầu mail; mail chỉ có chữ ký.
- Thuộc tính: đoạn cắt tăng dần, không chồng; ghép `body_text` bỏ đoạn cắt rồi chuẩn hóa ra đúng `body_clean`; chạy hai lần cùng kết quả; không có đoạn cắt thì `body_clean` là `body_text` đã chuẩn hóa.
- Profile tự sinh (`data/thunderbird-profile`): mail trả lời có quote bị cắt quote, chữ ký `--` bị cắt, nội dung chuyển tiếp còn nguyên.
- Dữ liệu thật: chỉ đo bằng `mail-stats` (spec 11): tỷ lệ mail có cắt quote / chữ ký, tỷ lệ giảm độ dài, số mail `body_clean` rỗng mà `body_text` không rỗng.

## 7. Giới hạn đã biết

- Quote bằng `＞` hoặc `|` chưa cắt (dữ liệu thật: 0 và 13 mail).
- Chữ ký chỉ có tên và chức danh, không có dấu hiệu liên hệ, không bị cắt.
- Dòng bắt đầu bằng `>` trong nội dung thật (hiếm) bị coi là quote.
- Chuyển tiếp lồng trong quote: cắt cả khối theo quote.

## 8. Sửa sau review bảo mật (2026-10-17)

- **ReDoS:** regex tìm địa chỉ mail chạy bậc hai trên dòng dài không có `@` (dòng 20.000 ký tự sau đường kẻ mất 2 giây; `body_text` cho phép 2.000.000 ký tự, tức một mail spam có thể làm worker treo hàng giờ và chặn mọi người dùng). Sửa:
  - Lookbehind để regex chỉ bắt đầu ở đầu một cụm ký tự.
  - Cửa sổ sau đường kẻ bỏ qua dòng dài hơn 1.000 ký tự.
  - Lọc nhanh bằng chuỗi con trước regex dấu hiệu liên hệ / tên công ty, cache theo dòng.
  - Đếm dòng có giới hạn (dừng khi vượt giới hạn).
- Có test với 6 kiểu mail cố tình dựng, tới 2.000.000 ký tự: mỗi mail dưới 2 giây (chậm nhất 1,3 giây).
- Không tăng `CLEANER_VERSION`: kết quả chỉ khác khi dòng dài hơn 1.000 ký tự nằm trong 6 dòng sau đường kẻ và là dấu hiệu liên hệ duy nhất.
