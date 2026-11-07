# Spec: Cấu hình dự án và gán mail theo bộ lọc

Ngày: 2026-10-31 · Thành phần: `backend/app/projects.py`, `backend/app/api/project_routes.py`, `backend/app/processing.py`, `backend/app/tasks/__init__.py`, `backend/app/threads_view.py`, `backend/app/cli.py`, `backend/app/db/locks.py`, migration 0017 (`projects`, `message_projects`, `user_project_state`), `web/src/pages/ProjectsPage.tsx`, `web/src/pages/ThreadListPage.tsx` · Trạng thái: xong (sửa sau review: mục 11)

## 1. Mục đích

Người dùng cần khai báo từng dự án trên web và để hệ thống tự biết mail nào thuộc dự án nào. Chức năng này là nền cho các luồng tự động về sau (bắt đầu từ mail khách gửi tài liệu), và chốt phần "quy tắc gán dự án" đang chờ người dùng ở mục 9 của kế hoạch: người dùng tự khai báo bộ lọc trên màn hình, không có danh sách cố định trong code.

Quyết định của người dùng (2026-10-31):

- Bố cục màn hình: danh sách dự án ở cột trái, form bên phải, cùng một trang (bố cục A trên canvas [Màn hình Dự án](https://claude.ai/artifact/E41kXo4sdQYKoqZvqSR1i2)).
- Bộ lọc gồm 4 ô From, To, CC, Subject, mỗi ô nhiều giá trị. Mail thuộc dự án khi khớp ít nhất một ô địa chỉ, và nếu ô Subject có giá trị thì subject cũng phải khớp. Lý do: địa chỉ khách chỉ nằm ở From **hoặc** To/CC tùy mail đến hay đi, còn Subject tách được các dự án của cùng một khách.
- Trường Git, Backlog, Redmine có trên màn hình và được lưu, nhưng chưa dùng ở giai đoạn này. Không có ô API key hay token.
- Có gán mail vào dự án ngay ở chức năng này, và tên dự án hiện trên danh sách thread để kiểm tra kết quả.
- Mail khớp nhiều dự án được gán vào tất cả dự án khớp.

## 2. Không làm

- Xử lý mail Backlog → Redmine, xử lý mail GitHub review: đợi, chưa đối ứng (kế hoạch giai đoạn 8). Các trường `backlog_*`, `redmine_*`, `github_mail`, `review_*` chỉ được lưu, không code nào đọc để xử lý.
- Doc pipeline: `docs_repo_url`, `docs_local_path` chỉ được lưu. Không clone, không đọc folder, không kiểm tra đường dẫn hay URL có tồn tại không.
- Phân loại mail bằng AI, trạng thái đối ứng, tồn đọng.
- Bộ lọc theo dự án trên danh sách thread và search, tên dự án trên trang chi tiết thread.
- Chia sẻ dự án cho thành viên khác (`project_members`, kế hoạch giai đoạn 7): mỗi người dùng có danh sách dự án riêng.
- Gán tay một mail hoặc thread vào dự án, loại trừ tay một mail khỏi dự án.
- Regex hay cú pháp lọc riêng trong bộ lọc: chỉ so "có chứa".
- Lưu API key, token. Chưa dùng thì chưa lưu bí mật.

## 3. Quy tắc gán dự án

Hàm thuần `match_projects(mail, rules) -> set[int]` trong `app/projects.py`.

Dữ liệu vào của mail: `from_addr`, các `addr` trong `to_addrs`, `cc_addrs`, và `subject`. Không dùng tên hiển thị và không đọc nội dung mail.

Chuẩn hóa cả giá trị bộ lọc và giá trị mail bằng NFKC rồi `casefold`, sau đó so bằng toán tử `in` của Python (có chứa, tuyến tính, không regex).

Với mỗi dự án:

```
address_ok = (filter_from, filter_to, filter_cc đều trống)
             hoặc from_addr chứa một giá trị của filter_from
             hoặc một địa chỉ To chứa một giá trị của filter_to
             hoặc một địa chỉ CC chứa một giá trị của filter_cc
subject_ok = (filter_subject trống) hoặc subject chứa một giá trị của filter_subject
khớp       = address_ok và subject_ok, và bốn ô không đồng thời trống
```

- Trống cả 4 ô: dự án không nhận mail nào, để không gán nhầm toàn bộ mail.
- Chỉ có Subject: chỉ xét Subject.
- Một mail có thể khớp nhiều dự án: gán vào tất cả (quyết định của người dùng). Thread hiện đủ các tên, người dùng thấy và chỉnh lại bộ lọc. Luồng tự động về sau gặp mail thuộc nhiều dự án phải hỏi người dùng, không tự chọn.

Ví dụ với bộ lọc `@sample-a.example.jp` ở From, To, CC và `【サンプルA】` ở Subject:

| Mail | Kết quả |
|---|---|
| Từ `tanaka@sample-a.example.jp`, subject `【サンプルA】仕様確認` | Thuộc dự án |
| Từ mình, To `tanaka@sample-a.example.jp`, subject `Re: 【サンプルA】仕様確認` | Thuộc dự án |
| Từ `tanaka@sample-a.example.jp`, subject `【サンプルB】見積` | Không thuộc dự án |

Thread: gán lưu theo từng mail. Thread hiện tên một dự án khi có ít nhất một mail trong thread thuộc dự án đó, nên mail nội bộ không có địa chỉ khách vẫn đi cùng dự án trên danh sách. Gán theo mail vì id thread có thể đổi khi gom lại (spec 10).

## 4. Dữ liệu (migration 0017)

Bảng mới, chưa có dữ liệu, không cần `CONCURRENTLY`. RLS, GRANT viết bằng SQL trong migration; thêm model vào `app/db/models.py`.

### `projects`

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity | PK |
| `user_id` | `bigint` | FK `users` `ON DELETE CASCADE` |
| `name` | `text` | `UNIQUE (user_id, name)` |
| `filter_from`, `filter_to`, `filter_cc`, `filter_subject` | `text[]` | mặc định rỗng; dùng cho mục 3 |
| `docs_repo_url`, `docs_local_path` | `text` NULL | lưu sẵn |
| `github_mail` | `text[]` | lưu sẵn |
| `review_repo_url`, `review_local_path` | `text` NULL | lưu sẵn |
| `backlog_mail` | `text[]` | lưu sẵn |
| `backlog_space_url`, `backlog_project_key` | `text` NULL | lưu sẵn |
| `redmine_url`, `redmine_project` | `text` NULL | lưu sẵn |
| `customer_domains` | `text[]`, mặc định `'{}'` | Domain mail khách, tối đa 20 giá trị, dùng cho dịch tự động (spec 26 mục 3). Thêm ở migration 0018 |
| `processed_source_id` | `bigint` NULL | Folder mail dự án / AI đã xử lý: mail dịch xong chuyển tới đây trên web. FK kép `(processed_source_id, user_id)` tới `mail_sources`, xóa folder thì đặt NULL (spec 26). Migration 0018 |
| `ai_since` | `timestamptz` NULL | Mốc dịch tự động của dự án: đặt một lần khi `customer_domains` lần đầu có giá trị (hiện tại trừ 14 ngày), do API đặt, web chỉ đọc (spec 26). Migration 0018 |
| `created_at`, `updated_at` | `timestamptz` | |

`UNIQUE (id, user_id)` cho FK kép. RLS owner theo `user_id` (`USING` và `WITH CHECK`). `app_api`: `SELECT`, `DELETE`, `INSERT` theo cột không có `id`, `UPDATE` theo cột không có `id`, `user_id`. `app_worker`: `SELECT`.

Khác kế hoạch mục 4 (`projects(code, client_domains, keywords)`, `project_members`): bộ lọc 4 ô thay cho `client_domains`, `keywords`; chưa có `code` và chưa có thành viên.

### `message_projects`

`(user_id, message_id, project_id)`, PK cả ba cột.

- FK kép `(message_id, user_id) → messages (id, user_id)` `ON DELETE CASCADE`: xóa mail của folder làm mất liên kết ngay.
- FK kép `(project_id, user_id) → projects (id, user_id)` `ON DELETE CASCADE`.
- Index `(user_id, project_id)`.
- Không chép nội dung mail.
- RLS owner. `app_api`: chỉ `SELECT`. `app_worker`: `SELECT`, `INSERT`, `DELETE`.

### `user_project_state`

`user_id` PK (FK `users` `ON DELETE CASCADE`), `rules_version`, `applied_rules_version`, `scan_version`, `scan_after_id` (đều `bigint` mặc định 0), `updated_at`. Theo cùng mẫu `user_thread_state`.

RLS owner. `app_api`: `SELECT`, `INSERT (user_id, rules_version)`, `UPDATE (rules_version, updated_at)`. `app_worker`: `SELECT`, `UPDATE (applied_rules_version, scan_version, scan_after_id, updated_at)`.

## 5. Luồng gán

### Mail mới

`rethread_user` (`app/processing.py`) đã xử lý mọi mail có `thread_id IS NULL` trong một transaction giữ `LOCK` theo người dùng. Trong chính transaction đó, sau khi ghi thread, mail vừa được gom lần đầu được gán dự án theo quy tắc hiện hành. Thread và dự án của mail vì vậy cùng commit.

Không dùng mốc `messages.id` để tìm mail mới: id cấp trước khi commit, hai lô ingest commit ngược thứ tự thì mốc sẽ bỏ sót mail. Không thêm cột vào `messages`: quét lại toàn bộ sẽ ghi lại mọi dòng của bảng có index PGroonga.

Người dùng chưa có dự án thì bỏ qua bước này.

### Sửa bộ lọc

API tăng `user_project_state.rules_version` trong cùng transaction khi:

- tạo dự án có ít nhất một giá trị lọc;
- sửa dự án làm đổi một trong 4 ô lọc.

Sửa trường khác, hoặc xóa dự án, không tăng version (xóa dự án đã bỏ liên kết qua FK).

Task định kỳ `project_rules_sweep` chạy mỗi 5 giây, chỉ đọc bảng nhỏ `user_project_state`, đẩy job `assign_user_projects(user_id)` cho người có `rules_version > applied_rules_version`. Job dùng `lock` theo người dùng và `queueing_lock` riêng.

Job quét lại theo lô 2.000 mail, mỗi lô một transaction giữ `LOCK`:

1. Đọc `rules_version` = v **trước** khi đọc `projects`.
2. `scan_version <> v` thì bắt đầu lại: `scan_after_id = 0`, `scan_version = v`.
3. Lấy tối đa 2.000 mail có `id > scan_after_id` theo thứ tự `id`, chỉ các cột địa chỉ và subject.
4. Xóa `message_projects` của các mail đó, ghi lại theo `match_projects`, cập nhật `scan_after_id`.
5. Lô chưa đủ 2.000 mail: ghi `applied_rules_version = v`.

Mail không còn khớp bị gỡ khỏi dự án ở bước 4. Job chạy tối đa 10 giây rồi tự đẩy job tiếp, như `embed_user`. Bộ lọc đổi giữa chừng thì version khác, quét lại từ đầu với quy tắc mới.

Mail commit trong lúc quét có `id` nhỏ hơn `scan_after_id` không bị sót: mail đó còn `thread_id IS NULL` nên đi qua nhánh mail mới, và cả hai nhánh đều đọc quy tắc sau khi giữ `LOCK`.

`assign_messages` ghi mỗi lô trong một SAVEPOINT và thử lại tối đa 3 lần khi gặp sqlstate 23503 (mail hoặc dự án vừa bị xóa) hoặc 40P01 (deadlock); hết lần thử thì lỗi được ném ra.

Gán là idempotent: chạy lại cho cùng kết quả. Task bọc lỗi bằng `sanitized`.

## 6. API (chỉ phiên web)

File `app/api/project_routes.py`, xác thực `WebUserId`. Đọc body bằng `read_body` rồi `model_validate_json`, khai báo `openapi_extra=json_body(...)`. Token collector không gọi được.

| Method | Đường dẫn | Việc |
|---|---|---|
| `GET` | `/api/v1/projects` | `{items: Project[], assigning: bool}`; `assigning` = `rules_version > applied_rules_version` |
| `POST` | `/api/v1/projects` | Tạo, trả 201 và `Project` |
| `PUT` | `/api/v1/projects/{id}` | Thay toàn bộ các trường, trả `Project` |
| `DELETE` | `/api/v1/projects/{id}` | Xóa, trả 204. Mail không bị xóa |

Path id dùng `Path(gt=0, le=MAX_BIGINT)`.

`ProjectInput` (Pydantic, `extra="forbid"`) dùng chung cho `POST`, `PUT`:

- `name`: bắt buộc, 1–200 ký tự sau khi bỏ khoảng trắng hai đầu, không có ký tự điều khiển.
- Mỗi ô danh sách (`filter_*`, `github_mail`, `backlog_mail`): tối đa 20 giá trị, mỗi giá trị 1–255 ký tự, bỏ khoảng trắng hai đầu, không có ký tự điều khiển, bỏ giá trị trùng sau chuẩn hóa, giữ thứ tự.
- `docs_repo_url`, `review_repo_url`, `backlog_space_url`, `redmine_url`: có thể trống, tối đa 2.000 ký tự. Nhận `https://`, `http://`, `ssh://`, và dạng `git@host:path`. Từ chối URL có userinfo với `http`, `https`, và có mật khẩu với `ssh`, vì giá trị này lưu dạng thường. Từ chối thêm: có query (`?`) hoặc fragment (`#`), tên đăng nhập ssh khác `git`, host bắt đầu bằng `-` (cả `ssh://` và `git@host:path`). Không gọi tới URL.
- `docs_local_path`, `review_local_path`: có thể trống, tối đa 1.000 ký tự, không có ký tự điều khiển. Không kiểm tra tồn tại.
- `backlog_project_key`, `redmine_project`: có thể trống, tối đa 200 ký tự.
- Giá trị trống ghi `NULL`.
- `customer_domains` (spec 26): tối đa 20 giá trị, mỗi giá trị chuẩn hóa (`normalize_domain`: NFKC, casefold, bỏ `@` đầu, phải có dấu chấm, không khoảng trắng hay ký tự điều khiển, không nhãn rỗng, tối đa 253 ký tự), bỏ trùng. `processed_source_id`: folder của chính người dùng và không phải Inbox, không thì 422 `project_folder_invalid`. `ai_since` chỉ có trong `Project` trả về, `ProjectInput` không nhận. Lưu dự án đổi `processed_source_id` sang folder khác thì xét lại mail `done` đang `no_folder` của dự án trong cùng transaction.

`Project` = các trường trên, cộng `id`, `created_at`, `updated_at`.

Lỗi:

| Mã | `code` | Khi |
|---|---|---|
| 404 | `project_not_found` | Không có dự án, hoặc dự án của người khác |
| 409 | `project_name_taken` | Trùng tên trong danh sách của người dùng |
| 409 | `project_limit` | Đã có 100 dự án |
| 422 | | Sai dữ liệu, không trả lại giá trị đã gửi |

Danh sách thread: `ThreadSummary` thêm `projects: list[ProjectRef]`, `ProjectRef = {id, name}`. `list_threads` lấy tên bằng một truy vấn riêng theo các `thread_id` của trang, giống `folders_of`; không thêm vào truy vấn phân trang. Hợp đồng web đổi theo: `npm run gen:api`. Collector không bị ảnh hưởng.

CLI: `python -m app.cli assign-projects --email <e>` chạy gán lại ngay, không cần worker, in số mail đã xét và số mail có dự án. `mail-stats` thêm số mail đã gán dự án.

## 7. Giao diện

Trang `ProjectsPage` ở `/settings/projects`, mục menu "Dự án" đặt trước "Cài đặt AI". Dùng shadcn/ui và token sẵn có, không thêm màu hay font. Dự án đang chọn nằm trên URL (`?project=<id>`).

- **Cột trái:** tiêu đề "Dự án", nút "Thêm dự án", danh sách tên kèm số điều kiện lọc (`5 điều kiện lọc`, hoặc `Chưa có bộ lọc`), và dòng "Danh sách này chỉ áp dụng cho tài khoản của bạn". Xuống một cột ở màn hình hẹp.
- **Form bên phải**, theo thứ tự:
  - Tên dự án.
  - Bộ lọc mail: From, To, CC, Subject, kèm mô tả quy tắc khớp.
  - Tài liệu khách hàng: Repo URL lưu tài liệu, Folder local dự án.
  - GitHub review: Mail GitHub, GitHub khách review, Git local của dự án.
  - Backlog và Redmine: Mail Backlog, URL space Backlog, Project key Backlog, URL Redmine, Project Redmine.
  - Ba nhóm lưu sẵn có nhãn "Chưa dùng, lưu sẵn cho giai đoạn sau".
  - Nút "Lưu dự án" (tắt khi chưa đổi gì), "Xóa dự án".
- **Ô nhiều giá trị** (component `TagInput`): Enter hoặc dấu phẩy thêm giá trị, rời ô thì thêm phần đang gõ, Backspace ở ô trống bỏ giá trị cuối, dán nhiều giá trị cách nhau bằng dấu phẩy hoặc xuống dòng được tách ra. Enter trong ô không gửi form. Nút bỏ từng giá trị có `aria-label`.
- **Trống:** chưa có dự án thì hiện "Chưa có dự án nào" cùng nút "Thêm dự án".
- **Chưa có bộ lọc:** hiện cảnh báo "Dự án chưa có bộ lọc nên chưa nhận mail nào".
- **Đang gán lại:** khi `assigning` thì hiện "Đang gán lại mail theo bộ lọc…" và tải lại trạng thái định kỳ; worker không chạy thì nhắc giống dải gom thread.
- **Xóa:** dialog xác nhận "Xóa dự án <tên>? Mail không bị xóa, chỉ bỏ liên kết với dự án."
- **Rời form chưa lưu:** chọn dự án khác khi còn thay đổi chưa lưu thì hỏi trước.
- **Lỗi:** `project_name_taken` hiện dưới ô tên; lỗi 422 hiện theo từng ô.

`ThreadListPage`: mỗi dòng hiện tên dự án bằng `Badge` (outline) trước số mail, tối đa 2 tên, phần còn lại ghi `+n`, đủ tên nằm trong `title`.

## 8. Phân quyền và dữ liệu (RLS, log)

- Bộ lọc và tên dự án có thể chứa tên, địa chỉ khách: không ghi log, không đưa vào thông báo lỗi, không gửi cho AI, không in ở `mail-stats`. Log của gán dự án chỉ có `user`, số mail, số dự án, thời gian.
- Claude Code không đọc `projects` trên DB dev `mailassist`, và mockup, test chỉ dùng dữ liệu giả `*.example.*`.
- API chạy trong `user_session` (RLS). Worker dùng `app_worker` nên mọi truy vấn lọc `user_id = :u`. Không bao giờ so bộ lọc của người này với mail của người khác.
- Mọi unique index bắt đầu bằng `user_id`, trừ `UNIQUE (id, user_id)` chỉ làm đích FK kép (mục 10).
- Chức năng không gọi dịch vụ ngoài, không đọc đường dẫn local, không dùng AI.

## 9. Kiểm thử

Chỉ dùng dữ liệu giả `*.example.*`.

- `tests/test_projects.py`, hàm thuần: bảng ca của mục 3 gồm mail đến, mail đi, CC, chỉ Subject, trống cả 4 ô, khác dự án cùng khách, nhiều dự án, full-width và half-width, hoa thường, mail thiếu `from_addr` hoặc `subject`.
- Test API: tạo, sửa, xóa, trùng tên, giới hạn 100 dự án, thiếu header `X-PMM-Request`, chưa đăng nhập, token collector bị từ chối, URL có mật khẩu bị từ chối, trường lạ bị từ chối, lỗi 422 không chứa giá trị đã gửi.
- Test phân quyền: người khác không đọc, sửa, xóa được dự án (404) và không thấy `message_projects`; dự án của A không gán được cho mail của B (FK kép); `app_api` không ghi được `message_projects`.
- Test gán: mail mới qua `process_user`; sửa bộ lọc làm mail được thêm và bị gỡ; đổi trường không phải bộ lọc không tăng version; quét nhiều lô; bộ lọc đổi giữa lúc quét; chạy lại không tạo trùng; xóa mail của folder và xóa dự án làm mất liên kết.
- Test danh sách thread: `projects` đúng khi chỉ một mail trong thread khớp, khi thread thuộc 2 dự án, và không làm đổi phân trang.
- Test log: giá trị bộ lọc không xuất hiện trong log của API và worker.
- Web (Vitest): `TagInput`, chọn dự án, lưu, xóa có xác nhận, trạng thái trống, cảnh báo chưa có bộ lọc, Badge trên danh sách thread.
- Chạy thử giao diện trên DB test với profile giả, không mở trên DB dev.

## 10. Giới hạn đã biết

- "Có chứa" khớp cả chuỗi con: `@sample-a.example.jp` cũng khớp `x@sample-a.example.jp.example.com`, và người gửi có thể giả địa chỉ From. Giai đoạn này chỉ ảnh hưởng nhãn hiển thị. Luồng tự động về sau không được chỉ dựa vào nhãn dự án để tải file hay gọi hệ thống ngoài.
- Lọc địa chỉ công ty mình ở From làm mọi mail gửi đi khớp. Hệ thống không biết domain nội bộ nên không cảnh báo.
- `PUT` không kiểm tra phiên bản: hai tab cùng sửa thì bản lưu sau thắng.
- Quét lại toàn bộ mất thời gian tỷ lệ với số mail; trong lúc đó nhãn dự án trên danh sách có thể chưa cập nhật.
- Người dùng dùng chung một khách cho nhiều dự án mà không có Subject thì mail thuộc cả các dự án đó, theo quyết định ở mục 3.
- Lưu hoặc xóa dự án qua web, và xóa mail của folder, hiếm khi trả 500 nếu bị chọn làm nạn nhân deadlock; người dùng bấm lại là được. Đọc bộ lọc cũ khi lưu dùng `FOR NO KEY UPDATE` để không chờ khóa `FOR KEY SHARE` mà kiểm FK của worker giữ đến khi commit.
- Rời trang bằng menu đầu trang (hoặc nút Back) làm mất phần sửa chưa lưu, không hỏi: app dùng `BrowserRouter`, chỉ việc chuyển sang dự án khác trong trang mới hỏi trước.
- Giới hạn 100 dự án kiểm bằng đếm rồi chèn, nên nhiều lần tạo đồng thời có thể vượt nhẹ.
- Ràng buộc `UNIQUE (id, user_id)` trên `projects` là mẫu làm đích FK kép, dùng chung với `messages`, `threads`, `mail_sources`.
- Các tính năng về sau dùng `docs_repo_url` hoặc đường dẫn local phải truyền `--` cho git, giới hạn giao thức git, và chỉ cho phép trong danh sách folder gốc cho trước. Hiện UNC và `..` vẫn được nhận vì giá trị chưa bao giờ được dùng.

Thêm 2026-11-01 (spec 26): `Project` và `ProjectInput` có `customer_domains`, `processed_source_id` (và `ai_since` chỉ đọc); xóa dự án xóa luôn `glossary_terms` của dự án (`ON DELETE CASCADE`), hộp thoại xóa nói rõ. Hợp đồng web đổi theo `npm run gen:api`; collector không bị ảnh hưởng.

## 11. Sửa sau review (2026-10-31)

- Điểm vào `assign-projects` của CLI được sửa để chạy đúng khi gọi như lệnh thật (có test chạy tiến trình con).
- `assign_messages` ghi từng lô trong SAVEPOINT và thử lại tối đa 3 lần khi gặp sqlstate 23503 (mail hoặc dự án bị xóa giữa chừng) hoặc 40P01 (deadlock).
- `TagInput`: Enter khi đang soạn bằng IME không còn thêm giá trị.
- Quy tắc URL chặt hơn (mục 6): từ chối query, fragment, tên đăng nhập ssh khác `git`, host bắt đầu bằng `-` ở cả dạng `ssh://` và `git@host:path`.
- Kiểm tra độ dài giá trị lọc chạy trước chuẩn hóa NFKC.
- Thêm test RLS theo hành vi: người khác không thấy `message_projects`, `user_project_state` và không sửa được; `app_api` không đổi được cột tiến độ của worker; `app_worker` không ghi được `projects`; danh sách cột bị cấm trong `test_api_column_privileges` có thêm hai bảng mới.

Đợt sửa cuối (cùng ngày):

- Xóa dự án đang chọn: dùng `mutateAsync(...).then(onDeleted)` vì callback của `mutate` bị bỏ khi form đã gỡ (dự án rời cache), nên URL còn `?project=<id>` cũ; test stub có độ trễ 20 ms để tái hiện.
- Ô tìm kiếm ở đầu trang có `min-w-48` để dòng xuống hàng thay vì co lại khi dải "Đang xử lý" hiện.
- `OLD_FILTERS` đổi `FOR UPDATE` thành `FOR NO KEY UPDATE`; có test lưu bộ lọc không chờ khi có transaction giữ `FOR KEY SHARE`.
- Refetch nền lỗi không gỡ form đang sửa (cổng theo `projects.data`, không theo `isSuccess`).
- `maxLength` từng ô văn bản theo đúng giới hạn mục 6 (URL 2.000, đường dẫn 1.000, key và project 200).
- Khi `assigning` chuyển từ true sang false, danh sách thread được đánh dấu cũ để nhãn dự án tải lại.
- Thêm test: `app_api` không chèn được dự án mang `user_id` người khác (RLS WITH CHECK); nhãn dự án trên danh sách thread đi theo tách và gộp thread tay.
