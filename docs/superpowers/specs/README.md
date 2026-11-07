# Danh mục spec theo chức năng

Mỗi chức năng có một spec mô tả mục đích, quy tắc xử lý, ràng buộc bảo mật và cách kiểm thử. Thay đổi và quyết định theo thời gian: [docs/CHANGELOG.md](../../CHANGELOG.md). Thêm hoặc sửa chức năng thì cập nhật spec và bảng này trong cùng lượt làm việc ([.claude/rules/documentation.md](../../../.claude/rules/documentation.md)).

| # | Chức năng | Thành phần | Spec | Trạng thái |
|---|---|---|---|---|
| 1 | Trang setting: chọn profile Thunderbird và folder đồng bộ | collector | [2026-10-16-collector-settings-design.md](2026-10-16-collector-settings-design.md) | Xong |
| 2 | DB và các bảng mail (RLS, mỗi người một bản mail) | DB, backend | [2026-10-16-db-schema-design.md](2026-10-16-db-schema-design.md) | Xong |
| 3 | Trích xuất mail từ mbox (tách mail, MIME, tiếng Nhật, metadata) | collector | [2026-10-17-mail-extraction-design.md](2026-10-17-mail-extraction-design.md) | Xong |
| 4 | Đồng bộ tăng dần (state, gửi theo lô, retry, `collector sync`) | collector | [2026-10-17-incremental-sync-design.md](2026-10-17-incremental-sync-design.md) | Xong |
| 5 | Xác thực API và người dùng (token, CLI quản trị) | backend | [2026-10-17-api-auth-design.md](2026-10-17-api-auth-design.md) | Xong |
| 6 | Ingest API (nhận lô mail, idempotent) | backend | [2026-10-17-ingest-api-design.md](2026-10-17-ingest-api-design.md) | Xong |
| 7 | Xóa mail của folder khỏi server | backend, collector | [2026-10-17-delete-folder-mail-design.md](2026-10-17-delete-folder-mail-design.md) | Xong |
| 8 | Giao diện đồng bộ (kết nối server, nút Đồng bộ, tiến độ, thông báo hoàn thành) | collector | [2026-10-17-sync-ui-design.md](2026-10-17-sync-ui-design.md) | Xong |
| 9 | Làm sạch nội dung (cắt quote, chữ ký) | backend | [2026-10-17-content-cleaning-design.md](2026-10-17-content-cleaning-design.md) | Xong |
| 10 | Gom thread (References, Thread-Index, subject; sửa tay) | DB, backend | [2026-10-17-threading-design.md](2026-10-17-threading-design.md) | Xong (API/UI gộp tách tay: spec 15, 16) |
| 11 | Xử lý nền (worker) và công cụ kiểm tra (`process-mail`, `mail-stats`, `review-threads`) | backend | [2026-10-17-worker-design.md](2026-10-17-worker-design.md) | Xong |
| 12 | Bộ sinh profile Thunderbird giả cho chạy thử và test (`/gen-sample-mail`) | công cụ test (`data/`) | [2026-10-18-sample-profile-generator-design.md](2026-10-18-sample-profile-generator-design.md) | Xong |
| 13 | Đăng nhập web (mật khẩu, phiên cookie, chống CSRF, Host, `set-password`) | DB, backend | [2026-10-18-web-login-design.md](2026-10-18-web-login-design.md) | Xong |
| 14 | Search từ khóa (PGroonga, bộ lọc, làm nổi bật từ khóa) | DB, backend | [2026-10-18-keyword-search-design.md](2026-10-18-keyword-search-design.md) | Xong |
| 15 | API xem thread, toàn văn mail, gộp/tách thread bằng tay | DB, backend, worker | [2026-10-18-thread-api-design.md](2026-10-18-thread-api-design.md) | Xong |
| 16 | Giao diện web (danh sách thread, xem thread, tìm kiếm, sửa tay) | web, backend | [2026-10-18-web-ui-design.md](2026-10-18-web-ui-design.md) | Xong |
| 17 | ML service: embedding bge-m3, rerank | ml-service | [2026-10-19-ml-service-design.md](2026-10-19-ml-service-design.md) | Xong |
| 18 | Embedding mail (cắt đoạn, `message_chunks`, worker) | DB, backend, worker | [2026-10-19-mail-embedding-design.md](2026-10-19-mail-embedding-design.md) | Xong |
| 19 | Tìm kiếm ngữ nghĩa hybrid (PGroonga + pgvector, RRF), đánh giá `eval/` | backend, web | [2026-10-19-hybrid-search-design.md](2026-10-19-hybrid-search-design.md) | Xong |
| 20 | Che thông tin trước khi gửi AI | — | — | Đã bỏ (không cần vì AI chạy trên model nội bộ; code xóa ở spec 28, 2026-11-02; file spec xóa 2026-11-05) |
| 21 | Hỏi đáp AI trên mail (RAG), ghi lại lần gọi AI (`ai_calls`) | DB, backend, web | [2026-10-19-ai-ask-design.md](2026-10-19-ai-ask-design.md) | Xong |
| 22 | Cài đặt model AI theo tác vụ (tên model do người dùng nhập), thống kê token | DB, backend, web | [2026-10-19-ai-settings-design.md](2026-10-19-ai-settings-design.md) | Xong (sửa ở spec 28) |
| 23 | Gợi ý thread liên quan bằng embedding (lớp 4 gom thread) | backend, web | [2026-10-19-similar-threads-design.md](2026-10-19-similar-threads-design.md) | Xong (ngưỡng chỉnh trên mail thật) |
| 24 | Nhà cung cấp AI theo người dùng (gateway kiểu 9router, giao thức OpenAI): nhập URL, API key, model | DB, backend, web | [2026-10-25-ai-provider-design.md](2026-10-25-ai-provider-design.md) | Xong (chưa gọi thử gateway thật; gateway trong LAN và mọi địa chỉ được nhận từ spec 28; chỉ giao thức OpenAI từ 2026-11-05, migration 0021) |
| 25 | Cấu hình dự án và gán mail theo bộ lọc (From, To, CC, Subject) | DB, backend, worker, web | [2026-10-31-project-settings-design.md](2026-10-31-project-settings-design.md) | Xong (sửa sau review: mục 11; chạy thử đầu cuối trên DB test) |
| 26 | Dịch mail tự động, glossary, chuyển mail sang folder dự án trên web | DB, backend, worker, web | [2026-10-31-mail-translation-design.md](2026-10-31-mail-translation-design.md) | Xong (sửa sau review: mục 16; chạy thử đầu cuối trên DB test với AI giả, chưa gọi thử gateway thật) |
| 27 | Cải thiện truy xuất cho hỏi đáp: lọc dự án, quét chính xác, ngữ cảnh mail trước, reranker | DB, backend, ml-service, web, eval | [2026-11-01-ask-retrieval-design.md](2026-11-01-ask-retrieval-design.md) | Xong (2026-11-02; reranker làm sẵn, mặc định tắt; đo 2026-11-03: MRR tăng nhưng 40 đoạn mất 17–45 giây trên CPU, mục 10; sửa sau review: mục 15) |
| 28 | Chuyển sang model nội bộ: ô URL nhận mọi địa chỉ, bỏ khóa mặc định, bỏ ba cấp model, bỏ che thông tin và NER, chỉ giao thức OpenAI (2026-11-05) | DB, backend, ml-service, web | [2026-11-01-internal-model-design.md](2026-11-01-internal-model-design.md) | Xong (2026-11-02; sửa sau review: mục 12; quyết định sau review: mục 13; chạy thử đầu cuối trên DB test với gateway giả, chưa gọi thử gateway thật) |

Chức năng 3–8 đã qua review cuối (code reviewer + security-reviewer) ngày 2026-10-17; chức năng 9–11 qua security-reviewer cùng ngày; chức năng 13–16 qua security-reviewer ngày 2026-10-19; chức năng 17–23 qua security-reviewer ngày 2026-10-21; chức năng 26 qua review toàn nhánh và security-reviewer ngày 2026-11-01. Các điểm đã sửa ghi ở mục "Sửa sau review" của từng spec và trong CHANGELOG.

Chưa có spec (theo lộ trình trong [email-assistant-plan.md](../../../email-assistant-plan.md)): bộ lọc theo dự án của giai đoạn 4 (làm sau spec 25, đã có nhãn dự án trên danh sách thread), phân loại và theo dõi đối ứng (giai đoạn 6), mở rộng nhiều người dùng (giai đoạn 7).
