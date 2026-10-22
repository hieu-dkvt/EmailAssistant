---
paths:
  - "samples/**"
  - "eval/**"
  - "collector/**/*_test.go"
  - "collector/testdata/**"
  - "backend/tests/**"
  - "ml-service/tests/**"
  - "data/**"
---

# Quy tắc dữ liệu test

- `samples/raw/`: mail thật dùng để tạo mẫu. Không commit, Claude Code không đọc thư mục này (đã chặn trong settings).
- `samples/anonymized/`: mail mẫu đã ẩn danh, dạng `.eml`, dùng chung cho test của collector và backend.
- `data/thunderbird-profile/`: profile Thunderbird **tự sinh hoàn toàn** bằng `data/generate_sample_profile.py` (nội dung bịa, tên miền `*.example.*`), đáp án gom thread ở `data/expected_threads.json`. Được dùng trong test và khi chạy thử. Sửa script sinh thì sinh lại profile và chạy lại test. Mô tả đầy đủ: spec 12 (`docs/superpowers/specs/2026-10-18-sample-profile-generator-design.md`).
- Test chỉ dùng dữ liệu ở hai nơi trên, hoặc dữ liệu tự dựng trong test.
- Ẩn danh hóa bằng script chạy cục bộ. Người dùng kiểm tra bằng mắt trước khi đưa file vào `samples/anonymized/`.
- Ẩn danh hóa phải giữ nguyên những gì cần test:
  - Charset gốc (ISO-2022-JP, Shift_JIS, UTF-8) và cách mã hóa header.
  - Cấu trúc MIME (multipart, HTML-only, đính kèm).
  - Header threading: Message-ID, In-Reply-To, References, Thread-Index.
  - Mẫu quote và chữ ký.
- Chỉ thay: tên người, tên công ty, địa chỉ mail, số điện thoại, địa chỉ, nội dung nghiệp vụ.
- Golden test: input là file trong `samples/anonymized/`, kết quả mong đợi lưu cạnh test (`collector/testdata/golden/`, `backend/tests/golden/`). Khi kết quả thay đổi có chủ đích, cập nhật golden và ghi lý do.
- Bộ câu hỏi trong `eval/` chỉ tham chiếu mail bằng ID, không chép nội dung mail thật.
- Không tạo dữ liệu test bằng cách copy mail trực tiếp từ profile Thunderbird.
