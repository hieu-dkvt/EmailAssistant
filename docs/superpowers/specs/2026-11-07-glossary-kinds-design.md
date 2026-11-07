# Spec: Glossary theo loại, thay trực tiếp, giữ tên tiếng Nhật, kiểm tra sau dịch

Ngày: 2026-11-07 · Thành phần (dự kiến): `backend/app/ai/glossary.py`, `backend/app/ai/translate.py`, `backend/app/ai/prompts.py`, `backend/app/api/glossary_routes.py`, `backend/seed/*.csv`, migration (cột mới của `glossary_terms`), `web/src/components/GlossaryTable.tsx`, `AddGlossaryDialog.tsx`, `MessageTranslation.tsx`, `eval/` (bộ đo dịch) · Trạng thái: **đang thiết kế, chưa có code**. Spec này ghi lại kết quả rà soát ngày 2026-11-07, các hướng và quyết định đã thống nhất với người dùng (mục 5, 7); thiết kế chi tiết đang trình bày từng phần (mục 9: phần 1 chờ duyệt, phần 2–4 chưa trình bày). Làm sau [spec 29](2026-11-07-ai-token-budget-design.md).

## 1. Mục đích

Bản dịch mail khách dùng đúng thuật ngữ dự án, giữ được tên màn hình, tên mục, tên bảng, tên cột để tra lại tài liệu gốc, và phát hiện được lỗi máy dịch mà không cần AI. Model nội bộ yếu hơn API thương mại, nên glossary, cách đưa thuật ngữ vào prompt và kiểm tra sau dịch là phần bù chất lượng.

## 2. Nguồn: tài liệu đề xuất của người dùng

File `Thiet ke glossary dich jp vn.md` ở thư mục gốc repo (người dùng soạn, cập nhật 07/11/2026). **Không commit** (quyết định của người dùng 2026-11-07), nên tóm tắt dưới đây là bản tham chiếu duy nhất trong repo. Nội dung chính:

- Mục glossary có các trường: ID, từ gốc JP, biến thể JP, VN chuẩn, VN chấp nhận, VN cấm, loại, phạm vi (Chung / Khách hàng / Dự án), ghi chú, trạng thái (draft/approved), phiên bản.
- Bốn loại: **Cố định** (tên màn hình, chức năng, hệ thống: thay bằng placeholder ⟦T⟧ rồi điền lại), **Ưu tiên** (thuật ngữ IT: khối "Thuật ngữ bắt buộc" trong prompt), **Theo ngữ cảnh** (từ nhiều nghĩa như 対応, 確認, 検討: khối "Gợi ý" kèm ghi chú, không kiểm tra cứng), **Không dịch** (placeholder, điền lại chuỗi quy định). Mã ID, URL, email, đường dẫn, code nhận bằng regex, bảo vệ bằng placeholder ⟦P⟧.
- Quy trình 5 bước: chuẩn hóa (đổi độ rộng chữ) → tìm thuật ngữ (cụm dài thắng, bỏ qua vùng regex) → thay placeholder và dựng prompt → gọi model → kiểm tra (placeholder đủ, VN chuẩn/chấp nhận có mặt, không có VN cấm, không sót kana, số khớp, tỷ lệ độ dài) rồi điền lại; lỗi thì dịch lại một lần, vẫn lỗi thì chuyển người duyệt.
- Ngoài phạm vi dịch: nguồn IMAP IDLE, Redmine (ghi note bản dịch), lưu Drafts, tự gửi theo chế độ review/hybrid/auto, glossary trên Excel/Google Sheet do BrSE duyệt, tự cải tiến từ bản người duyệt sửa, dịch VN→JP bằng tra ngược.

## 3. Rà soát tài liệu đề xuất (2026-11-07)

### Mâu thuẫn với nguyên tắc dự án (chờ người dùng quyết định, không thuộc spec này)

| Trong tài liệu | Nguyên tắc hiện tại |
|---|---|
| Chế độ hybrid/auto tự gửi bản dịch | "Không bao giờ tự gửi mail" |
| Drafts trên IMAP, nhận mail bằng IMAP IDLE | "Không ghi gì lên mail server"; collector chỉ đọc mbox, server không giữ mật khẩu IMAP |
| Ghi note bản dịch lên Redmine | Backlog → Redmine đang "đợi, chưa đối ứng" (kế hoạch, giai đoạn 8); spec tự động hóa (`backend/Mail automation spec.md`) yêu cầu PM duyệt trước khi ghi Redmine |
| Dịch lại 1 lần khi kiểm tra lỗi | Từ 2026-11-07 nguyên tắc là "mỗi bản mail chỉ **dịch** một lần" ([spec 29](2026-11-07-ai-token-budget-design.md)). Người dùng đồng ý dịch lại tự động tối đa một lần, nhưng chỉ khi gặp lỗi nặng (mục 5) |
| Glossary trên Google Sheet, BrSE duyệt | Glossary theo từng người dùng; glossary có thể chứa tên khách nên đưa lên dịch vụ ngoài là đưa dữ liệu ra ngoài |

### Vấn đề kỹ thuật của tài liệu đề xuất

1. Placeholder cho mã ID ngắn làm mất nghĩa: model vẫn giữ `SCR001` mà không cần placeholder. Ví dụ trong tài liệu: `SCR001の受注一覧画面` ra "màn hình Danh sách đơn hàng **của SCR001**" thay vì "(SCR001)". Placeholder chỉ đáng dùng cho chuỗi dài, dễ hỏng (URL, đường dẫn, khối code, log).
2. Placeholder ⟦T⟧ cho thuật ngữ rủi ro với model nhỏ: ⟦ ⟧ là ký tự hiếm, hay bị đổi thành `[T1]`, `【T1】`; dễ lặp danh từ chính ("màn hình màn hình …"); quy tắc "đúng 1 lần" chưa định nghĩa khi thuật ngữ xuất hiện nhiều lần.
3. Khớp chuỗi con với thuật ngữ 2 chữ Hán: 対応 nằm trong 対応表, 未対応; 確認 trong 確認書. Thay cứng một chỗ khớp nhầm làm hỏng câu.
4. Kiểm tra 5c báo lỗi oan: tên người, nhãn 「」 còn kana; chữ Hán sót lại không bị bắt; số đổi dạng hợp lệ (`1万円` → 10.000 yên, `11月7日` → 7/11, `1.5` → 1,5); từ cấm "cải tiến" của 改修 trùng bản dịch đúng của 改善.
5. "Một GPU chỉ phục vụ tốt từng việc một" đúng với Ollama mặc định, sai với vLLM (gộp request).
6. Tra ngược glossary cho VN→JP bị mơ hồ ("bug" ← 不具合 hoặc バグ): cần đánh dấu bản JP chuẩn khi dịch ngược.
7. Thiếu so với hệ thống hiện tại: cắt trích dẫn và chữ ký, chống prompt injection, ô `notes` ghi sắc thái cho PM, ngôn ngữ khác (tiếng Anh), mail thuộc nhiều dự án có glossary mâu thuẫn, quy tắc log và phân quyền.
8. Tự cải tiến từ bản người duyệt sửa cần có chức năng sửa bản dịch trước (spec 26 chưa làm).

## 4. Hệ thống hiện tại dịch thế nào (spec 26, code ngày 2026-11-07)

```
(0) Lúc nhận mail: clean_body cắt trích dẫn + chữ ký → messages.body_clean
(1) Kích hoạt: job translate_sweep/translate_user (mail dự án, From domain khách, Inbox, từ mốc 14 ngày) hoặc nút Dịch
(2) Đọc DB (user_session): mail, mail trước trong thread, tên model, glossary (load_entries: mục chung + mục của
    các dự án chứa mail, đọc lại mỗi lần dịch)
(3) Cắt: subject 300, body 12.000, ngữ cảnh 1.500 ký tự (spec 29 sẽ bỏ, thay bằng ngân sách token)
(4) Khớp glossary trên subject + body đã cắt (match_glossary)
(5) Dựng system prompt + user prompt bằng TEXT GỐC
(6) POST {base_url}/chat/completions (json_schema strict; 400 thì lùi json_object)
(7) Kiểm tra định dạng → check_translation → lưu message_translations → (job) chuyển folder trên web
```

- **Chuẩn hóa chỉ để khớp glossary:** `nfkc_map` + casefold + bỏ khoảng trắng tạo một chuỗi tạm trong bộ nhớ (`scr001の受注一覧画面で…エビデンス…safari対応`), giữ ánh xạ vị trí về text gốc. Model nhận **text gốc** (`ＳＣＲ００１…ｴﾋﾞﾃﾞﾝｽ`). `term_norm` của mục glossary tính lúc lưu mục.
- **Khớp:** cụm dài trước, không chồng lấn (`テスト` trong `結合テスト` bị bỏ); cùng `term_norm` thì mục dự án thắng mục chung; nhiều dự án dịch khác nhau thì gửi các bản dịch kèm "(chọn theo ngữ cảnh)"; thuật ngữ chữ Latin phải ở ranh giới từ; tối đa 60 dòng (spec 29 thay bằng phần ngân sách).
- **Một loại mục duy nhất:** `term`, `translation`, `note`; mọi dòng gửi trong `<glossary>` là "bắt buộc". Muốn giữ nguyên thì đặt `translation` trùng `term`.
- **Prompt:** `TRANSLATE_SYSTEM` (phiên bản 2), tin nhắn gồm `<context>`, `<glossary>`, `<subject>`, `<mail>`; chuỗi giống thẻ trong nội dung bị vô hiệu hóa (`_safe_translate`). Model trả `{subject, body, notes}`.
- **Kiểm tra sau dịch:** `glossary_missing`, `glossary_conflict`, `truncated`; chỉ ghi cảnh báo, không dịch lại.
- **Không có:** placeholder, regex bảo vệ URL/mã, kiểm tra URL hay chữ Nhật sót, loại mục, VN chấp nhận/cấm.

Ba lỗi lộ ra khi chạy code hiện tại trên mail giả (2026-11-07):

1. Mục `Redmine` khớp bên trong URL `https://redmine.example.jp/...` (dấu `.` và `/` được coi là ranh giới từ): dòng glossary thừa được gửi cho model.
2. Seed `対応 = xử lý` là mục bắt buộc ([glossary-it-ja-vi.csv:94](../../../backend/seed/glossary-it-ja-vi.csv)): "Safari対応" bị ép thành "xử lý Safari", hoặc dịch đúng "hỗ trợ Safari" thì bị `glossary_missing` oan. Seed `改修 = chỉnh sửa cải tiến` (dòng 50) dùng đúng từ mà tài liệu đề xuất xếp vào loại cấm.
3. Model nhận nguyên chữ toàn độ rộng và katakana nửa độ rộng; model nhỏ có thể chép nguyên vào bản dịch.

## 5. Hướng đã thống nhất (2026-11-07)

### Quyết định của người dùng

- **Tên tiếng Nhật được giữ trình bày theo kiểu (A): chữ Nhật trước, nghĩa sau**, ví dụ `「受注日」 (Ngày nhận đơn)`. Lý do: dễ tra lại tài liệu gốc, giao diện, code.
- **Thay trực tiếp thay cho placeholder ⟦T⟧** với mục cố định (ý của người dùng): code thay chữ Nhật bằng bản dịch chuẩn ngay trong văn bản trước khi gửi model, không cần bảng ánh xạ hay bước điền lại.
- Giới hạn glossary tới 50.000 mục, cấu hình được: làm ở [spec 29](2026-11-07-ai-token-budget-design.md).
- **Nghĩa tiếng Việt của tên giữ nguyên chỉ ghi ở lần xuất hiện đầu tiên** trong thân mail; subject tính riêng (hiển thị tách); mail chia đoạn (spec 29) thì "lần đầu" tính trên cả mail. Code chèn nghĩa từ glossary, không để model viết. Lý do: mail nhắc một tên nhiều lần mà lần nào cũng kèm nghĩa thì rối.
- **Dịch lại tự động tối đa một lần khi kiểm tra sau dịch phát hiện lỗi nặng**, kèm lời nhắc cụ thể (ví dụ "bản trước đã đổi cụm 'màn hình Danh sách đơn hàng', hãy giữ nguyên văn"), rồi giữ bản ít lỗi hơn. Lỗi nặng: cụm cố định hoặc tên giữ nguyên bị đổi hay mất; URL, email, đường dẫn bị đổi; sót cả câu tiếng Nhật. Lỗi nhẹ (từ cấm, thiếu thuật ngữ bắt buộc, sót chữ Nhật lẻ) chỉ cảnh báo. Nguyên tắc dịch mail thành: mỗi bản mail chỉ dịch một lần, được dịch lại tự động tối đa một lần khi gặp lỗi nặng. Người viết spec đã đề xuất chỉ cảnh báo; người dùng chọn dịch lại để chất lượng tự động tốt hơn, chấp nhận tốn gấp đôi thời gian với mail lỗi.
- **Làm chức năng trước, bộ đo sau** (người viết spec đã đề xuất bộ đo trước để có số gốc).

### Đề xuất của người viết, người dùng đã đồng ý hướng (chi tiết chưa duyệt)

**Loại mục và cách đưa cho model.** Code đọc loại (cột mới, ví dụ `kind`) và đặt mỗi loại ở một chỗ khác nhau; model không thấy cột loại.

| Loại | Ví dụ | Model thấy gì | Model được dặn | Kiểm tra sau dịch |
|---|---|---|---|---|
| Cố định (tiếng Việt) | 受注一覧画面 → màn hình Danh sách đơn hàng | Chữ Việt đã thay sẵn trong câu, thêm khoảng trắng hai bên: `SCR001の màn hình Danh sách đơn hàng で` | "Cụm tiếng Việt có sẵn trong `<mail>`, `<subject>`: chép nguyên văn" | Bản dịch chứa đúng chuỗi (không phân biệt hoa thường) |
| Giữ nguyên (tiếng Nhật) | 受注一覧画面 giữ nguyên | Chữ Nhật đánh dấu `⟪受注一覧画面⟫`; nghĩa ở khối `<names>` | "Đoạn trong ⟪ ⟫: chép nguyên văn cả dấu, không dịch; nghĩa ở `<names>` chỉ để đặt câu" | Bản dịch chứa đúng chữ Nhật (theo nội dung, mất dấu vẫn đạt); code đổi ⟪ ⟫ thành 「 」 và chèn nghĩa theo kiểu (A) |
| Bắt buộc | 不具合 = bug | Chữ Nhật trong câu, một dòng trong `<required>` | "Dùng đúng bản dịch bên phải" | Có VN chuẩn hoặc VN chấp nhận, không có VN cấm (cảnh báo) |
| Theo ngữ cảnh | 対応: trình duyệt → hỗ trợ; việc → xử lý | Chữ Nhật trong câu, dòng điều kiện trong `<hints>` | "Chỉ là gợi ý, chọn theo câu" | Không kiểm tra |

- Ví dụ kết quả mục giữ nguyên: model trả `… trên ⟪受注一覧画面⟫ (SCR001).`, code xuất `… trên 「受注一覧画面」 (màn hình Danh sách đơn hàng, SCR001).`
- Mục chữ Latin giữ nguyên (`Redmine`, `API`) không cần đánh dấu.
- Mục được thay trực tiếp không cần dòng trong prompt, nên không chiếm phần ngân sách glossary (spec 29).
- Thẻ mới (`required`, `hints`, `names`) phải thêm vào regex vô hiệu hóa thẻ (`_TRANSLATE_TAG`, `prompts.py`). Đổi prompt thì tăng `TRANSLATE_PROMPT_VERSION` (lên 3).
- Mail vốn có đoạn tiếng Việt (khách trích lời team Việt) cũng sẽ bị chép nguyên theo luật "cụm tiếng Việt có sẵn"; nếu đo thấy gây lỗi thì đánh dấu cả cụm cố định (như ⟪ ⟫), chỉ thêm khi bộ đo cho thấy cần.

**Điều kiện để thay trực tiếp an toàn.**

- Chỉ áp dụng cho loại cố định và giữ nguyên.
- Chống khớp nhầm: độ dài tối thiểu cho mục thay trực tiếp (ví dụ từ 3 ký tự) hoặc cột "cụm loại trừ".
- Không thay và không khớp glossary bên trong URL, email, đường dẫn, khối code (nhận bằng regex tuyến tính).
- Vị trí khớp lấy từ `nfkc_map` (đã giữ vị trí gốc); `match_glossary` cần trả thêm vị trí (hiện bỏ ở `glossary.py`).
- Bản gốc hiển thị trên web không đổi; chỉ text gửi model bị thay.

**Tên màn hình, tên mục, tên bảng, tên cột (không thể khai báo hết trong glossary): ba lớp.**

| Lớp | Loại tên | Cách xử lý | Cần glossary |
|---|---|---|---|
| 1 | Tên vật lý chữ Latin (`T_ORDER`, `ORDER_DATE`, `btnSearch`, `SCR001`) | Luật trong system prompt "không dịch tên bảng, cột, biến, ID chữ Latin"; code kiểm tra chuỗi có `_`, số hoặc camelCase trong bản gốc còn nguyên trong bản dịch | Không |
| 2 | Tên logic tiếng Nhật (`「受注日」項目`, `受注テーブル`) | Luật chung: tên trong 「」『』［］ hoặc đứng trước 画面, 項目, ボタン, 欄, タブ, テーブル, カラム, 列, 帳票, メッセージ… thì giữ chữ Nhật, thêm nghĩa theo kiểu (A); code kiểm tra `「X」` + danh từ chỉ loại còn nguyên (regex hẹp, tuyến tính). 「」 dùng cho mục đích khác (trích lời, `「至急」`) để model tự quyết | Không |
| 3 | Tên cần nghĩa tiếng Việt thống nhất | Nhập hàng loạt từ tài liệu thiết kế (画面一覧, 画面項目定義書, テーブル定義書 xuất CSV: tên Nhật, tên vật lý, tên Việt) vào glossary dự án, loại cố định hoặc giữ nguyên; gợi ý tự động: đếm `「…」` + danh từ chỉ loại hay gặp trong mail dự án mà chưa có trong glossary, người dùng duyệt | Có, nhập hàng loạt |

**Kiểm tra sau dịch mới (chỉ ghi cảnh báo, không gọi AI).** URL, email, đường dẫn, `#1234` còn nguyên; tên vật lý chữ Latin còn nguyên (lớp 1); `「X」` + danh từ chỉ loại còn nguyên (lớp 2); chữ Nhật sót (kana và Hán), bỏ qua tên giữ nguyên có chủ đích; VN cấm (cảnh báo, vì dễ báo oan).

**Cột mới dự kiến của `glossary_terms`.** Loại; VN chấp nhận (nhiều giá trị); VN cấm (nhiều giá trị); `note` giữ vai trò nghĩa hoặc điều kiện ngữ cảnh. Nhập, xuất CSV thêm cột tương ứng. Chưa làm trạng thái draft/approved và phiên bản: `glossary_used` của mỗi bản dịch đã đủ để phát hiện bản dịch cũ.

**Sửa seed.** `対応`, `確認`, `検討` thành loại theo ngữ cảnh có điều kiện; `改修` → "sửa đổi"; rà các mục chữ Latin đang đặt `translation` trùng `term`.

**Bộ đo (làm sau chức năng, theo quyết định của người dùng).** Dựng bộ mail giả hoặc mail ẩn danh có bản dịch tham chiếu (profile giả spec 12 có cặp Nhật–Việt), chạy với gateway thật, so ba cách đưa mục cố định: chỉ ghi trong prompt (hiện tại), thay trực tiếp, placeholder ⟦T⟧. Đo: tỷ lệ tuân thủ glossary, URL và mã giữ nguyên, chữ Nhật sót, thời gian mỗi mail. Placeholder ⟦P⟧ cho URL/khối code chỉ thêm nếu đo thấy model làm hỏng.

## 6. Không làm (spec này)

Redmine, IMAP IDLE, Drafts trên IMAP, tự gửi mail, glossary dùng chung có người duyệt, dịch VN→JP, sửa tay bản dịch và học từ bản sửa, phạm vi glossary "Khách hàng" (chưa có thực thể khách hàng; dự án theo từng người dùng).

## 7. Câu hỏi (đã trả lời 2026-11-07)

1. Nghĩa của tên giữ nguyên: **chỉ lần xuất hiện đầu tiên** (mục 5).
2. Dịch lại khi kiểm tra phát hiện lỗi nặng: **có, tối đa một lần** (mục 5).
3. Thứ tự: **chức năng trước, bộ đo sau** (mục 5).
4. File đề xuất `Thiet ke glossary dich jp vn.md`: **không commit**; mục 2 tóm tắt nội dung của nó.

Còn mở: duyệt thiết kế chi tiết (mục 9).

## 8. Phân quyền và dữ liệu (nguyên tắc cho thiết kế chi tiết)

- Glossary vẫn theo từng người dùng, RLS theo `user_id`; nhập từ tài liệu thiết kế là dữ liệu khách nên không đưa ra dịch vụ ngoài, không commit.
- Text gửi AI vẫn là text gốc người dùng xem được (spec 28); thay trực tiếp chỉ đổi chuỗi gửi đi, không đổi dữ liệu lưu.
- Log không có thuật ngữ, bản dịch, nội dung mail; regex chạy trên nội dung mail phải tuyến tính.

## 9. Thiết kế chi tiết (đang trình bày từng phần)

Thứ tự các phần: 1. dữ liệu glossary; 2. trước khi gửi (vùng bảo vệ URL/code, khớp, thay trực tiếp, ⟪⟫, các khối prompt); 3. sau dịch (⟪⟫ thành 「」, chèn nghĩa kiểu (A), kiểm tra nặng/nhẹ, dịch lại một lần, lưu); 4. sửa seed, bộ đo, kiểm thử, tài liệu. Phần 2–4 chưa trình bày.

### Phần 1: Dữ liệu glossary (đề xuất, chờ duyệt)

Cột mới của `glossary_terms`:

| Cột | Kiểu | Ý nghĩa |
|---|---|---|
| `kind` | `text NOT NULL DEFAULT 'required'`, `CHECK (kind IN ('fixed','keep','required','context'))` | Loại mục |
| `accepted` | `text[] NOT NULL DEFAULT '{}'` | VN chấp nhận được (tối đa 10 giá trị, mỗi giá trị ≤ 200 ký tự) |
| `forbidden` | `text[] NOT NULL DEFAULT '{}'` | VN cấm (tối đa 10) |
| `exclude` | `text[] NOT NULL DEFAULT '{}'` | Cụm loại trừ: chỗ khớp nằm trong cụm này thì bỏ qua, ví dụ `対応` loại trừ `対応表` (tối đa 10) |

Ý nghĩa của `translation` theo loại:

| `kind` | Tên trên giao diện | `term` | `translation` | `note` |
|---|---|---|---|---|
| `fixed` | Cố định | 受注一覧画面 | màn hình Danh sách đơn hàng (thay thẳng vào câu) | ghi chú cho người |
| `keep` | Giữ nguyên | 受注日 | Ngày nhận đơn (nghĩa, chèn sau lần đầu theo kiểu (A)); trùng `term` thì không chèn nghĩa | ghi chú cho người |
| `required` | Bắt buộc | 不具合 | bug | gửi kèm cho model như hiện nay |
| `context` | Theo ngữ cảnh | 対応 | xử lý (gợi ý mặc định) | điều kiện chọn, ví dụ "trình duyệt/OS → hỗ trợ; người → phụ trách" |

Quy tắc:

- Giữ nguyên xuất đúng `term` chuẩn, kể cả khi khách viết biến thể (`ｴﾋﾞﾃﾞﾝｽ` → `エビデンス`). Tên chữ Latin (`Redmine`) không cần ⟪⟫.
- Gộp như hiện nay: mục dự án thắng mục chung, loại lấy theo mục thắng. Xung đột giữa các dự án của mail (bản dịch khác nhau) thì **không thay trực tiếp**, chuyển thành dòng gợi ý "chọn theo ngữ cảnh" như hiện nay.
- Mục cố định hoặc giữ nguyên dài 1–2 ký tự: giao diện cảnh báo "dễ khớp nhầm, nên thêm cụm loại trừ", không chặn.
- `glossary_used` lưu thêm `kind`; đổi loại của mục đã dùng làm bản dịch thành "cũ". Đổi `accepted`, `forbidden` không làm bản dịch cũ (chỉ ảnh hưởng kiểm tra). `TRANSLATE_PROMPT_VERSION` lên 3.
- Migration dữ liệu cũ: mục có `translation` trùng `term` (sau `normalize_term`) thành `keep`, còn lại `required`.
- CSV nhập, xuất: cột `term,translation,note,kind,accepted,forbidden,exclude`; giá trị nhiều phần ngăn bằng `|`; ô `kind` nhận mã hoặc tên tiếng Việt. CSV 3 cột cũ và dạng dán `term = translation` vẫn nhận: `keep` nếu bản dịch trùng thuật ngữ, còn lại `required`.
- API: item glossary trả thêm 4 trường; POST, PATCH nhận 4 trường (loại sai hoặc mảng vượt giới hạn → 422).
- Web: bảng glossary thêm cột "Loại" (ô chọn), các trường nhiều giá trị nằm trong phần mở rộng của dòng; hộp thoại "Thêm vào glossary" có ô chọn loại, mặc định "Bắt buộc".
