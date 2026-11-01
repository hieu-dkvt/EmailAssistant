# Quy tắc cập nhật tài liệu

Áp dụng mỗi khi thêm, sửa hoặc bỏ một chức năng: code, migration, lệnh CLI, API, cấu hình, script, skill. Tài liệu được cập nhật **trong cùng lượt làm việc với code** và là một phần của việc "xong": chưa cập nhật tài liệu thì chưa báo xong.

## Cập nhật những gì

| Khi | Cập nhật |
|---|---|
| Có chức năng mới | Spec mới `docs/superpowers/specs/YYYY-MM-DD-<tên>-design.md` theo mẫu bên dưới, kèm một dòng trong [danh mục spec](../../docs/superpowers/specs/README.md) (số thứ tự, thành phần, trạng thái) |
| Sửa chức năng đã có spec | Sửa spec cho khớp code: dòng đầu (thành phần, trạng thái), quy tắc xử lý, kiểm thử, giới hạn. Phần sửa sau review ghi vào mục "Sửa sau review (ngày)". Trạng thái trong danh mục spec cũng phải khớp |
| Thay đổi có tác động tới hành vi hoặc dữ liệu | `docs/CHANGELOG.md`: mục theo ngày, mới nhất ở trên, chia thành "Đã làm", "Quyết định của người dùng", "Quyết định của người viết spec (người dùng có thể đổi)", "Đã xử lý sau review", "Còn để sau" |
| Thêm hoặc đổi file, lệnh, quy tắc nghiệp vụ, bẫy kỹ thuật của một thành phần | `CLAUDE.md` của thành phần đó: mục "Cấu trúc", "Lệnh" và các mục quy tắc |
| Xong một chức năng lớn hoặc một giai đoạn, đổi cách chạy thử, đổi nguyên tắc | `CLAUDE.md` gốc: "Trạng thái hiện tại", "Chạy thử toàn bộ", "Nguyên tắc bắt buộc" |
| Thêm hoặc đổi rule, skill, agent, quyền trong `.claude/` | Bảng "Cấu hình Claude Code của dự án" trong `CLAUDE.md` gốc. Rule hoặc skill bị thay đổi làm sai thì sửa luôn |
| Người dùng quyết định làm đổi yêu cầu, mô hình dữ liệu hoặc lộ trình | `email-assistant-plan.md`, cộng với mục "Quyết định của người dùng" trong CHANGELOG. Tiến độ không theo dõi trong file kế hoạch mà ở danh mục spec |
| Đổi schema API (hợp đồng với collector và web) | Spec và CHANGELOG ghi rõ phần nào bị ảnh hưởng |

- **Tìm chỗ ghi cũ:** khi đổi tên, đường dẫn, lệnh hay số liệu (số mail mẫu, version, cổng…), Grep giá trị cũ trong mọi file `*.md` (gồm cả `.claude/`) rồi sửa hết. Ví dụ đã gặp: generator mail giả đã lên 1.051 mail nhưng `collector/CLAUDE.md` và test collector vẫn ghi 51.
- Không cần spec mới cho sửa lỗi nhỏ, refactor không đổi hành vi, hoặc thay đổi chỉ nằm trong test. Tuy vậy nếu hành vi mô tả trong spec thay đổi thì vẫn sửa spec, và nếu người dùng thấy được khác biệt thì ghi một dòng CHANGELOG.
- Chức năng đang làm dở qua nhiều lượt: spec ghi trạng thái "đang làm" và nói rõ phần nào đã chạy được.

## Mẫu spec

```markdown
# Spec: <tên chức năng>

Ngày: YYYY-MM-DD · Thành phần: `<đường dẫn file/thư mục>`, migration … · Trạng thái: đang làm | xong (sửa sau review: mục N)

## 1. Mục đích
## 2. Không làm
## 3. … Thiết kế và quy tắc xử lý (đủ để viết lại code mà không cần đọc code)
## n. Phân quyền và dữ liệu (RLS, log, gửi AI): bắt buộc nếu chức năng chạm vào mail
## n. Kiểm thử
## n. Giới hạn đã biết
## n. Sửa sau review (YYYY-MM-DD)
```

## Cách viết

- Viết tiếng Việt, tên trong code giữ tiếng Anh, văn phong như các spec có sẵn.
- Ghi đúng những gì code đang làm. Không ghi dự định như thể đã làm: phần chưa làm đưa vào "Không làm" hoặc "Còn để sau".
- Quyết định chưa chốt (mục 9 của kế hoạch) ghi là "chờ người dùng", không tự chốt trong tài liệu.
- Quyết định của người dùng ghi kèm ngày và lý do. Quyết định của người viết thì ghi rõ là của người viết.
- **Không chép nội dung mail thật** vào tài liệu (subject, địa chỉ, tên người hoặc công ty khách, đoạn trích). Chỉ dùng số liệu tổng hợp và dữ liệu giả `*.example.*`, vì tài liệu được commit và Claude Code đọc được.

## Khi báo xong

Câu trả lời cuối cho người dùng liệt kê các tài liệu đã cập nhật. Nếu không có gì cần cập nhật thì ghi rõ "không cần cập nhật tài liệu" kèm lý do.

Hook `.claude/hooks/doc_reminder.py` nhắc tự động: nếu một lượt có sửa code (trừ test và dữ liệu sinh ra) mà không sửa file `.md` nào, Claude bị chặn dừng một lần kèm lời nhắc. Hook chỉ biết có file `.md` nào được sửa hay không, không biết tài liệu đã đúng và đủ chưa: việc đó vẫn theo bảng ở trên.
