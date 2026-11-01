---
name: survey-profile
description: Khảo sát profile Thunderbird cho giai đoạn 0 (danh sách folder, kích thước mbox, số mail ước lượng), chỉ thu số liệu tổng hợp, không đọc nội dung mail. Dùng khi người dùng muốn khảo sát dữ liệu mail trên một máy.
disable-model-invocation: true
argument-hint: "[đường dẫn profile Thunderbird]"
---

# Khảo sát profile Thunderbird

Mục tiêu: lấy số liệu cho báo cáo khảo sát giai đoạn 0 (mục 6 của `email-assistant-plan.md`) mà không đưa nội dung mail vào ngữ cảnh của Claude.

## Nguyên tắc

- Không dùng Read, Grep, Glob trên thư mục profile (đã bị chặn trong settings). Chỉ chạy lệnh PowerShell có output là tên folder, kích thước, số đếm.
- Không in bất kỳ dòng nào của mbox, kể cả header (Subject, From). Lệnh đọc nội dung file phải kết thúc bằng phép đếm hoặc tổng hợp, ví dụ `Measure-Object`.
- Chỉ đọc. Không chạy lệnh ghi, đổi tên hay xóa trong profile.
- Tên folder có thể chứa tên khách hàng: hỏi người dùng có muốn che tên folder trong báo cáo không.

## Các bước

1. Xác định profile: dùng `$ARGUMENTS` nếu có. Nếu không, liệt kê `$env:APPDATA\Thunderbird\Profiles\` và hỏi người dùng chọn profile.
2. Liệt kê file mbox trong `Mail\Local Folders\` và `ImapMail\<server>\`, đệ quy vào `*.sbd`. Mbox là file không có đuôi; bỏ qua `*.msf` và các file có đuôi khác. Với mỗi file lấy: đường dẫn tương đối, kích thước (MB), ngày sửa cuối.
3. Đếm số mail mỗi file bằng số dòng bắt đầu bằng `From `, đọc dạng stream. File lớn có thể mất nhiều phút: chạy nền và báo tiến độ. Đây là số ước lượng, chưa trừ mail đã xóa nhưng chưa compact.
4. Tổng hợp: số folder, tổng dung lượng, tổng số mail, 10 folder lớn nhất, tách riêng `Local Folders` và `ImapMail`.
5. Ghi báo cáo vào `docs/survey/<tên máy>-<YYYY-MM-DD>.md`: bảng folder và phần tổng hợp. Nhắc người dùng đối chiếu số mail với số Thunderbird hiển thị.

Tỷ lệ dung lượng đính kèm và việc lấy mẫu mail đại diện cần đọc nội dung mail nên không làm trong skill này. Các việc đó làm bằng script chạy cục bộ.
