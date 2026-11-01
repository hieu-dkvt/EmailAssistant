# Spec: Che thông tin nhạy cảm trước khi gửi AI

Ngày: 2026-10-19 · Thành phần: `backend/app/ai/masking.py`, `backend/app/ai/names.py`, `backend/app/ai/people.py`, NER ở `ml-service/app/ner.py` · Trạng thái: xong (sửa sau review bảo mật: mục 9; sửa 2026-11-01 khi làm spec 26: mục 10)

## 1. Mục đích

Khách cho phép gửi nội dung mail qua AI **với điều kiện che thông tin nhạy cảm** (quyết định của người dùng 2026-10-19). Mọi text gửi AI (câu hỏi của người dùng, subject, người gửi, đoạn mail) đi qua bước che: thông tin bị thay bằng nhãn giả, AI chỉ thấy nhãn; câu trả lời của AI được thay nhãn về giá trị thật **trên server** trước khi hiện cho người dùng.

Bốn nhóm phải che (người dùng chọn cả bốn):

| Nhóm | Nhãn |
|---|---|
| Email, điện thoại/FAX, URL/tên miền, địa chỉ | `[EMAIL_n]`, `[PHONE_n]`, `[URL_n]`, `[ADDRESS_n]` |
| Tên người | `[PERSON_n]` |
| Tên công ty, tổ chức | `[ORG_n]` |
| Số tiền, số hợp đồng/đơn hàng | `[MONEY_n]`, `[CONTRACT_n]` |

## 2. Không làm

- Không che: ngày giờ, mã lỗi, tên màn hình/chức năng, mã dự án, số lượng không kèm đơn vị tiền. AI cần các thông tin này để trả lời.
- Không bảo đảm che 100%: nhận dạng tên dựa trên từ điển, mẫu và NER (mục 4), có thể sót. Người dùng xem được đúng nội dung đã gửi AI (spec 21) để kiểm tra.
- Không lưu bảng nhãn ↔ giá trị: chỉ tồn tại trong bộ nhớ trong một lần hỏi.

## 3. Nguyên tắc

- **Nhất quán trong một lần hỏi**: cùng một thực thể luôn mang cùng nhãn (câu hỏi và mọi đoạn mail dùng chung một bảng), để AI hiểu "`[PERSON_1]` gửi … `[PERSON_1]` trả lời …".
- **Lỗi thì không gửi** (fail closed): NER lỗi hay bất kỳ bước che nào lỗi → không gọi AI, trả lỗi cho người dùng.
- Giữ kính ngữ và ngữ cảnh: `田中様` → `[PERSON_1]様`, `anh Hiếu` → `anh [PERSON_2]`, `株式会社サンプルA` → `[ORG_1]`.
- Text gốc đã có chuỗi giống nhãn (vd. mail chứa `[PERSON_1]`) được đổi `[` thành `［` trước khi che, để không bị thay nhầm khi trả về.

## 4. Nhận dạng

Phát hiện theo nhiều nguồn rồi gộp: các khoảng chồng nhau cùng loại gộp thành một; khác loại thì giữ khoảng dài hơn, dài bằng nhau theo thứ tự ưu tiên EMAIL > URL > PHONE > CONTRACT > MONEY > ADDRESS > ORG > PERSON.

**Chuẩn hóa**: mẫu (4.1, 4.3) và từ điển (4.2) chạy trên bản NFKC của text (`nfkc_map`: chuẩn hóa theo cụm ký tự gốc + các dấu tổ hợp theo sau, nên chữ Việt dạng NFD và `ｶﾞ` cũng ghép đúng; giữ bảng vị trí gốc), rồi đổi vị trí về text gốc để thay. Nhờ vậy `Ｙａｍａｄａ`, `ｻﾝﾌﾟﾙ太郎`, `tanaka＠…`, số full-width, `Hiếu` gõ bằng bộ gõ tổ hợp cũng bị che. NER chạy trên text gốc.

**Ranh giới từ chỉ xét chữ Latin** (từ 2026-11-01): mẫu EMAIL, URL (tên miền), PHONE, MONEY, kính ngữ Latin và lời chào dùng lớp `WORD_CHARS` (chữ Latin, số, `_`; `names.py`: `NOT_AFTER_WORD`, `NOT_BEFORE_WORD`) thay cho `\w` (gồm cả chữ Nhật). Trước đó địa chỉ, số điện thoại liền sau chữ Nhật (`連絡先:tanaka@…`, `電話0312345678`) không có ranh giới nên không được che.

**Regex tuyến tính**: text là nội dung mail do người ngoài gửi (subject tới 10.000 ký tự, chữ ký tới 20.000): mọi lượng từ đều có giới hạn (`{0,30}`…), không có lượng từ lồng nhau không giới hạn. Phần regex và dựng từ điển chạy ở thread riêng (`asyncio.to_thread`), không chặn event loop.

### 4.1 Mẫu (regex, `masking.py`)

- **EMAIL**: `local@domain.tld` (local gồm cả chữ Latin có dấu).
- **URL**: `http(s)://…`, `www.…`, và tên miền đứng riêng có đuôi phổ biến (`.com`, `.jp`, `.co.jp`, `.vn`, `.de`, `.kr`… khoảng 50 đuôi), kèm cả đường dẫn phía sau (`sample-a.example.de/project/tanaka`).
- **PHONE**: tổng 9–15 chữ số, gồm: dãy số có dấu phân cách (`-`, khoảng trắng, `.`, `−`, `–`, `—`, ngoặc, `+` hay `(+84)` đầu); số liền bắt đầu bằng `0` (10–11 số: `0912345678`) hay `+` mã nước (`+84912345678`); kiểu Nhật `03(1234)5678`. Ranh giới: không dính chữ/số khác; dấu chấm chỉ chặn khi nối với chữ số, nên số ở cuối câu (`…0912345678.`) và sau `Tel.` vẫn khớp. Ngày (`2026-10-19`, `2026/10/19`) không khớp.
- **ADDRESS**: `〒123-4567` và phần còn lại của dòng; tên tỉnh Nhật (`東京都`, `大阪府`, `北海道`, `…県`) theo sau là `市/区/町/村/郡`; địa chỉ Nhật không có tỉnh dạng `港区芝公園4-2-8`; địa chỉ Việt Nam (`Số/Tầng/Lầu/Phòng` + số hoặc số nhà + tên đường, … tới `Phường/Quận/Huyện/TP/Tỉnh/Hà Nội/Hồ Chí Minh/HCM/Đà Nẵng`; `123 đường …`); giá trị sau nhãn `住所`, `所在地`, `Địa chỉ`, `Address`.
- **MONEY**: số kèm đơn vị tiền (`円`, `万円`, `¥`, `$`, `€`, `USD`, `JPY`, `VND`, `đ`, `đồng`, `nghìn`, `trăm triệu`, `triệu`, `tỷ`…); số + `万`/`億` không có `円` (trừ khi theo sau là từ đếm: `1万件`, `3人`…); `15k USD`; số đứng sau từ chỉ giá (`金額`, `単価`, `予算`, `見積額`, `giá`, `chi phí`, `price`, `total`…: `見積金額 1,200,000（税別）`).
- **CONTRACT**: mã đứng sau nhãn số hiệu: `契約(書)番号`, `契約書No.`, `注文(書)番号`, `発注(書)番号`, `受注番号`, `見積(書)番号/No.`, `請求(書)番号/No.`, `案件番号`, `管理番号`, `伝票番号`, `PO No.`, `Contract No.`, `Order No.`, `Invoice No.`, `Số hợp đồng`, `Số HĐ`, `Mã HĐ`, `Hợp đồng số`, `Số đơn hàng`… (chỉ che phần mã, giữ nhãn); mã dạng `HD-2026-0012` đứng ngay sau từ chỉ hợp đồng/đơn hàng (`契約`, `発注`, `注文`, `請求書`, `hợp đồng`, `HĐ`, `PO`, `contract`, `order`, `invoice`). Không che mã dự án đứng một mình (`PRJ-1234`).

### 4.2 Từ điển người gửi/nhận (`names.py`, `people.py`)

- Lấy từ header của mail **của chính người hỏi** (`from_name`/`from_addr`, `to_addrs`, `cc_addrs`; trong phiên RLS): tối đa 3.000 cặp (tên, địa chỉ) xuất hiện nhiều nhất, cache trong bộ nhớ 10 phút theo người dùng. Cộng thêm người gửi/nhận của các mail được đưa vào câu hỏi.
- Tên hiển thị được tách thành thực thể:
  - chia theo `/`, `|`, `,`, `、`, ngoặc `()（）[]【】「」<>`;
  - phần có dấu hiệu công ty (mục 4.3) → ORG; phần là tên phòng ban (`…部`, `…課`, `…室`, `…チーム`, `Team`, `Dept`…) hoặc từ chung (`info`, `support`, `noreply`, `事務局`, `担当`…) → bỏ;
  - còn lại → PERSON với các biến thể: tên đầy đủ (có và không có khoảng trắng); tên Nhật có khoảng trắng (`田中 健二`) thêm từng phần ≥ 2 ký tự (`田中`, `健二`); tên tiếng Việt thêm họ và tên gọi ≥ 2 ký tự (bỏ tên đệm `Văn`, `Thị`…) và bản không dấu (`Tran Hieu`, `Hieu`: mail tiếng Anh hay viết không dấu); tên Latin khác thêm từng từ ≥ 3 ký tự.
  - Địa chỉ mail → EMAIL.
- Mỗi biến thể dài ≥ 2 ký tự được thay ở mọi chỗ xuất hiện (so khớp trên bản NFKC). Biến thể chữ Latin so khớp **phân biệt hoa thường**, có thêm dạng Title và UPPER (`Taro YAMADA` → cả `Yamada`, `YAMADA`; `Hiếu` → `HIẾU`), không thêm dạng chữ thường (tên trùng từ thường như `Will`, `Bình` sẽ bị che khắp nơi). Ranh giới từ chỉ xét chữ Latin/số: `Bình` (tên) bị che, `bình thường` thì không; `Trầnです` (tên Latin dính chữ Nhật) vẫn bị che.

### 4.3 Mẫu tên người, tên công ty

- **Kính ngữ tiếng Nhật**: 1–4 chữ Hán (có thể kèm tên katakana: `鈴木エリカ`) hoặc 2–10 chữ katakana đứng trước `様`, `さん`, `さま`, `殿`, `氏`, `君`, `くん`, `ちゃん`, `先生`, `部長`, `課長`, `社長`, `専務`, `常務`, `係長`, `主任`, `室長`, `所長`, `次長`, `支店長` → PERSON. Không tính: danh sách loại trừ (`皆`, `各位`, `お客`, `担当`, `関係者`, `営業`, `開発`…); chữ trước `様` tạo thành từ thường: chữ không bao giờ đứng cuối tên (`仕`, `同`, `模`, `異`, `何`… → `仕様`, `同様`, `帳票仕様`) thì loại mọi khi, chữ có thể là cuối tên (`一`, `多`, `有`…) chỉ loại khi đứng một mình (`一様`, `多様` không che; `健一様`, `本多様` vẫn che); `様` là đầu từ khác (`様式`, `様子`, `様相`, `様々`).
- **Tên Latin + kính ngữ Nhật**: `Trần様`, `Hiếu さん`, `Tanaka-san`, `TANAKA-san`, `Tanaka-sama`, `-sensei`, `-kun`… → PERSON.
- **Lời chào, danh xưng tiếng Việt/Anh** (+ 1–4 từ viết hoa): lời chào `Hi`, `Hello`, `Dear`, `Chào`, `Xin chào`, `Kính gửi`, `Gửi`, `Thanks`, `Thank you`, `Cảm ơn`, có thể kèm danh xưng (`Chào Anh Tuấn`, `Dear Mr. Tanaka`: chỉ che tên, giữ danh xưng); danh xưng `anh`, `chị`, `em`, `ông`, `bà`, `cô`, `chú`, `bác`, `bạn`, `Mr.`, `Ms.`, `Mrs.`, `Miss`, `Dr.`; viết tắt `a`/`c`/`e` + tên khi ngay sau là dấu câu, `ơi` hay hết dòng (`a Tuấn,`, `c Lan ơi`).
- **Công ty**: `株式会社/有限会社/合同会社/一般社団法人…` + tên (chữ Hán, katakana, Latin, số; dừng ở hiragana, dấu câu, khoảng trắng) hoặc tên + `株式会社…`; `(株)`, `（株）`, `㈱` + tên; `…御中`; tiếng Anh `… Co., Ltd.`, `Inc.`, `Corp.`, `LLC`, `JSC`; tiếng Việt `Công ty/Cty (TNHH|CP|Cổ phần)? …` → ORG.

- **Tên lõi của công ty**: mỗi tên công ty nhận ra (từ mẫu, NER hay từ điển) kéo theo tên bỏ loại hình (`株式会社サンプルA` → `サンプルA`, `Vendor C Inc.` → `Vendor C`, `Công ty TNHH Mẫu Thử` → `Mẫu Thử`; ≥ 3 ký tự) cùng nhãn: subject hay viết tắt `【サンプルA】`.
- **Công ty trong toàn văn**: tên công ty thường chỉ có trong chữ ký, mà chữ ký đã bị cắt khỏi `body_clean` (đoạn gửi đi). Vì vậy quét mẫu công ty trên `body_text` (20.000 ký tự đầu, cả chữ ký) của 500 mail mới nhất (cùng cache từ điển) và của mọi mail nguồn, chỉ để học tên cần che; phần chữ ký không được gửi đi.

### 4.4 NER (ml-service, spec 17)

Gọi `/ner` cho mọi text (một lần cho câu hỏi, một lần cho câu hỏi + các nguồn). ml-service chọn model theo từng dòng (spec 17). Nhãn của GiNZA (tiếng Nhật) và spaCy (tiếng Anh): `PERSON`, `ORG`, `ADDRESS`, cộng `PHONE`, `EMAIL`, `URL`, `MONEY` (lớp bổ sung cho mẫu regex). Tiếng Việt không có NER.

Kết quả NER được làm sạch (`clean_ner_span`) trước khi dùng: bỏ kính ngữ dính vào tên (`田中様` → `田中`, để cùng nhãn với tên trong từ điển); bỏ "tên người" có chữ Hán kèm từ katakana chỉ hệ thống/sản phẩm (`システム`, `サーバ`, `データ`, `ツール`…: GiNZA từng nhận `在庫管理システム` là tên người; `鈴木エリカ` vẫn che); bỏ tên chữ Nhật dài hơn 20 ký tự (tên Latin dài như `Nguyen Thi Phuong Thao` vẫn che, tới 60 ký tự); bỏ từ của header và tiền tố subject (`From`, `To`, `Cc`, `Subject`, `Date`, `Re`, `Fwd`: GiNZA nhận nhầm là tổ chức). ml-service trả kết quả sai dạng hay không có model NER nào → lỗi, không gửi (`ml_client.ner`).

Header của nguồn được che **theo từng giá trị** (người gửi, người nhận, cc, subject, đoạn mail riêng), tên trường `From:`, `Subject:` ghép vào sau khi che (spec 21).

### 4.5 Lan truyền

Sau khi phát hiện ở mọi text, mọi chuỗi đã được gán nhãn (dài ≥ 2 ký tự) được tìm và thay ở **tất cả** text của lần hỏi: tên NER nhận ra ở đoạn 3 cũng bị che ở đoạn 1 nơi NER bỏ sót. Chuỗi dài hơn `MAX_SURFACE_CHARS` = 500 ký tự (và biến thể `upper()` dài hơn 500) không vào từ điển: cây tiền tố của từ điển sâu bằng độ dài chuỗi và `_trie_pattern` đệ quy theo độ sâu đó (URL dài làm `RecursionError`); nơi khác mẫu vẫn che nó ở từng text. Không lan: chuỗi 1 ký tự (vd. họ một chữ `林`) và chuỗi chỉ có chữ số ít hơn 6 số (`2026` bắt nhầm là tiền, `12` sau `Order No.`): chỉ che ở đúng chỗ đã phát hiện, nếu lan thì mọi năm/ngày ở nguồn khác bị che theo.

## 5. Thay ngược (unmask)

- Tìm `[(PERSON|ORG|EMAIL|PHONE|URL|ADDRESS|MONEY|CONTRACT)_n]` trong output của AI và thay bằng giá trị gốc (dạng xuất hiện đầu tiên). Nhãn không có trong bảng giữ nguyên.
- Giá trị AI trả về để dùng tiếp cục bộ (bộ lọc người gửi, từ khóa tìm kiếm ở spec 21) cũng được thay ngược trước khi dùng.

## 6. Phân quyền và dữ liệu

- Giới hạn đầu vào và **cắt sau khi che**: mỗi text đưa đi che tối đa `MAX_MASK_INPUT_CHARS` = 40.000 ký tự (dưới giới hạn 50.000 của `/ner`; đuôi URL cũng giới hạn bằng cỡ này). Việc cắt theo giới hạn gửi đi (`cut_masked`) chỉ chạy trên text **đã che**: cắt trước làm địa chỉ, số điện thoại cụt không khớp mẫu và phần đầu lọt ra ngoài. `cut_masked(text, limit, capped=…)` cắt ở ranh giới đoạn (`\n\n`), rồi cuối dòng, trong nửa sau giới hạn; không có thì cắt tại giới hạn nhưng không cắt giữa nhãn; `capped` (text gốc đã bị chặn ở 40.000) thì bỏ thêm `CAPPED_TAIL_CHARS` = 512 ký tự đuôi vì đoạn đó có thể là mảnh chưa che được. Ai dùng `Masker` để gửi đi (hỏi đáp, dịch) phải theo thứ tự này.
- Từ điển chỉ đọc header mail của người hỏi trong `user_session` (RLS). Cache chỉ trong bộ nhớ process, theo `user_id`, hết hạn 10 phút (mục hết hạn của mọi người bị xóa ở lần đọc sau); đăng xuất không cần xóa vì không trả ra ngoài.
- Không log text, không log bảng nhãn. Log: số nhãn theo nhóm.

## 7. Kiểm thử (dữ liệu giả `*.example.*`)

- Từng mẫu: email, URL, tên miền, điện thoại Nhật/Việt/quốc tế, ngày không bị nhận là điện thoại, `〒` + địa chỉ, tiền `円`/`万円`/`¥`/`USD`/`VND`/`triệu`, mã sau `契約番号`, `Số hợp đồng`.
- Tên hiển thị: `田中 健二` → `田中 健二`, `田中健二`, `田中`, `健二`; `Nguyễn Văn An` → `Nguyễn Văn An`, `Nguyễn`, `An`, `Nguyen Van An`, `Nguyen`; `Brian Carter` → `Brian Carter`, `Brian`, `Carter`; `株式会社サンプルA 田中` → ORG + PERSON; `noreply`, `営業部` bỏ.
- Kính ngữ: `田中様`, `山本部長`, `皆様` (không che), `anh Hiếu`, `Tanaka-san`.
- Công ty: `株式会社サンプルA`, `サンプルB株式会社` (không dính phòng ban phía sau), `㈱テストC`, `Vendor C Inc.` (không dính tên người đứng trước), `Công ty TNHH Mẫu Thử`; tên lõi bị che ở text khác (`【サンプルA】`); chữ ký `--\n株式会社サンプルA` (dòng `--` không bị coi là tên).
- Nhất quán: cùng tên ở câu hỏi và đoạn mail cùng nhãn; biến thể của một người cùng nhãn; lan truyền từ NER sang text khác.
- Thay ngược đúng; nhãn lạ giữ nguyên; text có sẵn `[PERSON_1]` không bị thay nhầm.
- Không còn giá trị gốc nào trong text đã che (kiểm trên một mail giả đầy đủ).
- NER lỗi → ném lỗi (không trả text chưa che).
- Sau review: lời chào + danh xưng, `-sama`, `a/c` + tên; biến thể hoa/thường và full-width/half-width của tên trong từ điển; tên Latin dính chữ Nhật; điện thoại liền số / `03(1234)5678` / dấu `−`; email `＠` full-width, local có dấu; tiền `万`, sau từ chỉ giá, `nghìn`, `trăm triệu`, `15k USD`; số hợp đồng không có nhãn số; địa chỉ Việt Nam và Nhật không có tỉnh; tên miền kèm path; từ thường không bị che (`仕様書`, `同様`, `様式`, `1万件`, `PRJ-1234`); làm sạch kết quả NER (kính ngữ, chữ Hán + katakana, từ header).
- Regex tuyến tính: 9 chuỗi cố tình dựng (10.000 chữ số, `A-A-…`, `a.a.…`, 20.000 `a`, `株式会社` lặp…) mỗi chuỗi che xong dưới 3 giây (đo: ≤ 0,2 giây; trước khi sửa 13 giây với 10.000 chữ số).
- E2E (scratchpad, không commit): 1.050 mail giả + ml-service thật + AI giả ghi lại request: quét request không còn tên, công ty, tên miền, điện thoại nào của dữ liệu giả.

## 8. Giới hạn đã biết

- **Che thừa cạnh chữ Nhật** (hệ quả của ranh giới chỉ xét chữ Latin): `user.idが`, `System.IOで`, `PRJ-2026-1234-5678まで`, `0123456789件` bị che; từ thường như "Friday. Reach", "đã" bị thay bằng `[PERSON_n]`. Chấp nhận (lọt NDA nặng hơn che thừa), làm giảm chất lượng dịch.
- Loại trừ chuỗi dài hơn 500 ký tự khỏi từ điển có thể để lọt ở text sau nếu mẫu không bắt lại chuỗi đó; giới hạn 40.000 áp trên text NFKC.
- Che mail dài có nhiều tên miền tốn thời gian (bậc hai: khoảng 6 giây mỗi lần với 40.000 ký tự).

- Tên người không có trong header, không có kính ngữ, NER không nhận ra (đặc biệt tên tiếng Việt viết trong mail tiếng Việt không kèm `anh/chị`) sẽ lọt.
- Từ viết hoa trùng tên trong từ điển bị che thừa (vd. `Bình` ở đầu câu "Bình thường…", `An` ở đầu câu "An toàn…"). Tên phòng ban lạ có thể bị coi là tên người.
- Kính ngữ tiếng Nhật bắt tối đa 4 chữ Hán trước `様`/`さん`…: có thể dính chữ đứng trước (`開発部田中様` → che `発部田中`); nếu đuôi là tên đã biết trong từ điển thì chỉ che tên đó.
- Số tiền viết bằng chữ (`五百万円` có chữ số Hán) chỉ che khi dùng chữ số Hán liền với `円`.
- Tên viết toàn chữ thường (`yamada`, `hieu`) không bị che bằng từ điển (chỉ có biến thể Title/UPPER).
- `a`/`c`/`e` + tên ở giữa câu (`a Tuấn gửi file…`) không được bắt (chỉ khi sau tên là dấu câu, `ơi` hay hết dòng); tên đứng ngay sau từ khác không có kính ngữ (`弊社山田が対応します`) chỉ trông vào NER và từ điển.
- Địa chỉ tiếng Anh không có nhãn `Address:` không bị che. NER tiếng Anh không có nhãn địa chỉ.
- Dãy số dài có dấu cách (bảng số liệu `100 200 300 400 500`) có thể bị coi là điện thoại và bị che.
- Dòng có cả chữ Nhật và tên tiếng Anh (`Brian様より`) chỉ chạy GiNZA: tên tiếng Anh ở dòng đó chỉ được bắt bằng mẫu kính ngữ hoặc từ điển.

## 9. Sửa sau review bảo mật (2026-10-21)

security-reviewer kiểm bằng `Masker` trên chuỗi giả, tìm ra các dạng phổ biến bị lọt. Đã sửa (mục 4, 7):

- **Nghiêm trọng: tên người lọt** qua lời chào kèm danh xưng (`Chào Anh Tuấn`, `Kính gửi Chị Lan`, `Dear Mr. Tanaka` chỉ che `Mr`), `-sama`, `a/c` + tên; tên trong từ điển viết khác hoa/thường (`YAMADA`/`Yamada`), full-width (`Ｙａｍａｄａ`), katakana half-width.
- **Nghiêm trọng: điện thoại, email lọt**: số liền (`0912345678`, `+84912345678`), `03(1234)5678`, dấu `−` (U+2212), `＠` full-width (phần local là tên người).
- Mẫu tiền, số hợp đồng, địa chỉ, tên miền còn thiếu (danh sách ở mục 4.1).
- **Regex chạy theo bình phương**: subject 10.000 chữ số mất 13 giây, mẫu công ty tiếng Anh trên `A-A-…` 20.000 ký tự khoảng 12 giây, chặn cả API. Nay mọi lượng từ có giới hạn và việc che chạy ở thread riêng.
- NER chọn model theo cả text (một ký tự Nhật làm cả mail tiếng Anh chạy GiNZA): nay theo từng dòng (spec 17). NER sai dạng / không có model → lỗi (fail closed).
- Thấy thêm khi chạy E2E trên dữ liệu giả: `仕様書` bị che thành `[PERSON]様書`; GiNZA nhận `在庫管理システム` là tên người và `From`/`To`/`Subject` là tổ chức; `田中様` (NER) khác nhãn với `田中`; `Trầnです` lọt vì ranh giới từ dùng `\w` (gồm cả chữ Nhật). Đã sửa (mục 4.3, 4.4, 4.2).

**Vòng 2** (rà lại lượt sửa, cùng ngày):
- **Nghiêm trọng: số điện thoại ở cuối câu hay sau `Tel.` vẫn lọt** (ranh giới `(?![\w.])` chặn mọi dấu chấm): nay chỉ chặn dấu chấm nối với chữ số; thêm `(+84)`.
- Lỗi do lượt sửa trước: lọc `仕様` quá rộng làm lọt `健一様`, `本多様`; bỏ hẳn tên NER dài (`Nguyen Thi Phuong Thao`) và tên Hán + katakana (`鈴木エリカ`); chữ Việt dạng NFD lọt qua mẫu và từ điển. Đã sửa (mục 4, 4.3, 4.4).
- Lan truyền số ngắn làm che cả năm/ngày (`予算 2026年度` → mọi `2026年` bị che): không lan số ngắn, `AMOUNT_AFTER_WORD` bỏ năm/ngày. `ASP.NET`, `VB.NET` không còn bị coi là tên miền. Thêm `TANAKA-san`, địa chỉ `4丁目2番8号`.
- Script kiểm của reviewer (chuỗi giả) chạy lại trên code mới: mọi dạng đã báo đều được che, regex trên 17 chuỗi dựng sẵn ≤ 0,08 giây.
- Còn để sau (giới hạn mục 8): `a Tuấn` giữa câu, `弊社山田が対応します` (không kính ngữ, chỉ trông NER).

## 10. Sửa 2026-11-01 (khi làm spec 26)

- `Masker.mask(texts, keep=None)`: tham số `keep` song song với `texts`, mỗi phần tử là `(đầu, cuối)` hoặc `None`. Cả text vẫn đi qua mẫu và NER nên giá trị có nhãn nằm ngoài phần giữ vẫn được nhận ra; chỉ phần `[đầu, cuối)` được xuất, khoảng phát hiện vắt qua biên thì biên mở rộng theo khoảng (`_widen`) nên không còn mảnh chưa che. Chuỗi phát hiện ngoài phần giữ vào từ điển nhưng không có nhãn, nên bảng nhãn và `mask_counts` chỉ tính thứ đã gửi. Không truyền `keep` thì hành vi như cũ. `/ask` dùng để che theo cửa sổ (spec 21 mục 11).
- `Masker.known_surfaces` (số chuỗi trong từ điển) và `counts` dùng để phát hiện từ điển còn đổi (dịch lặp tới khi ổn định, spec 26 mục 6).
- Ranh giới từ Latin cho EMAIL/URL/PHONE/MONEY và mẫu tên (mục 4); bỏ chuỗi dài khỏi từ điển (mục 4.5); giới hạn đầu vào 40.000 và `cut_masked` (mục 6).
- Hằng `URL_BREAK_CHARS` (`<>"'）)】」、。`) là phần ký tự kết thúc URL của `_URL_TAIL`, dùng chung với `ask._chunk_range`.
- Kiểm thử thêm: giá trị có nhãn bị cắt đôi ở mọi vị trí, địa chỉ liền chữ Nhật, từ thường không bị che, URL dài không làm `RecursionError`, `keep` với khoảng vắt qua biên, `mask_counts` chỉ tính phần giữ.
