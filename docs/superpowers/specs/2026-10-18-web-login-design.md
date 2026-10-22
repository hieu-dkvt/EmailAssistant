# Spec: Đăng nhập web (mật khẩu, phiên cookie)

Ngày: 2026-10-18 · Thành phần: `backend/app/web_auth.py`, `backend/app/api/web_deps.py`, `backend/app/api/session_routes.py` (`/api/v1/session`), `backend/app/main.py` (Host), `backend/app/cli.py` (`set-password`), migration 0010, 0013 · Trạng thái: xong (sửa sau review bảo mật: mục 10; sửa CLI đặt mật khẩu: mục 11)

## 1. Mục đích

Người dùng đăng nhập giao diện web (spec 16) bằng email và mật khẩu. Mọi API của web chạy trong `user_session` của người đã đăng nhập (RLS), giống API của collector. Kế hoạch giai đoạn 4: "tài khoản đơn giản, chuẩn bị sẵn cho SSO".

## 2. Không làm

- SSO (Microsoft Entra, Google, LDAP): giai đoạn 7, chờ người dùng chốt hệ thống SSO. Lớp phiên (mục 4) độc lập với cách xác minh danh tính, nên SSO chỉ cần thêm một đường đăng nhập gọi cùng hàm tạo phiên.
- Tự đăng ký, quên mật khẩu, đổi mật khẩu trên web: quản trị đặt mật khẩu bằng CLI.
- Phân quyền theo vai trò (`admin`, `pm`, `member`): giai đoạn 7.
- Audit log đăng nhập: giai đoạn 7.

## 3. Dữ liệu (migration 0010)

**`user_passwords`**: một dòng mỗi người dùng có mật khẩu web.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | `bigint` PK → `users` on delete cascade | |
| `password_hash` | `text` not null | `scrypt$<n>$<r>$<p>$<salt base64>$<hash base64>` |
| `updated_at` | `timestamptz` not null default `now()` | |

**`web_sessions`**: phiên đăng nhập.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | `bigint` identity PK | |
| `user_id` | `bigint` → `users` on delete cascade | index |
| `token_hash` | `bytea` unique | SHA-256 của token phiên; không lưu token gốc |
| `created_at` | `timestamptz` not null default `now()` | |
| `expires_at` | `timestamptz` not null | `created_at` + 12 giờ |
| `last_seen_at` | `timestamptz` not null default `now()` | cập nhật tối đa 5 phút một lần |
| `revoked_at` | `timestamptz` null | đăng xuất, đổi mật khẩu |

- Hai bảng không chứa mail, không bật RLS, `REVOKE ALL FROM PUBLIC`, **không cấp quyền nào cho `app_api` và `app_worker`** (như `api_tokens`).
- Hàm `SECURITY DEFINER`, chủ `app_owner`, `SET search_path = pg_catalog`, `REVOKE ALL FROM PUBLIC`, chỉ `app_api` được `EXECUTE`:
  - `web_password_hash(p_email text) RETURNS TABLE (user_id bigint, password_hash text)`: mật khẩu của người dùng có email `lower(p_email)`; không có thì không trả dòng nào.
  - `web_session_start(p_user_id bigint, p_token_hash bytea, p_expires_at timestamptz) RETURNS void`: xóa phiên đã hết hạn hoặc đã thu hồi của người đó, thêm phiên mới.
  - `web_session_user(p_token_hash bytea) RETURNS bigint`: `user_id` nếu phiên chưa thu hồi và `expires_at > now()`, ngược lại `NULL`. Cập nhật `last_seen_at` khi đã cũ hơn 5 phút.
  - `web_session_end(p_token_hash bytea) RETURNS void`: đặt `revoked_at = now()`.
- `app.user_id` vốn là GUC mà SQL nào cũng đặt được, nên API vốn đã được tin để xác định người dùng. Các hàm trên chỉ nhằm `app_api` không đọc được hàng loạt mật khẩu băm và token phiên.

## 4. Mật khẩu và phiên

- Băm bằng `hashlib.scrypt` (thư viện chuẩn): `n = 2^15`, `r = 8`, `p = 1`, salt 16 byte ngẫu nhiên, khóa 32 byte, `maxmem` 64 MiB. So sánh bằng `hmac.compare_digest`. Chạy trong thread riêng để không chặn event loop, tối đa 4 lần cùng lúc (semaphore `password_slots`): mỗi lần dùng khoảng 32 MiB RAM và đăng nhập không cần xác thực. Một lần mất khoảng 0,6 giây trên máy dev.
- Email không có hoặc chưa đặt mật khẩu: vẫn chạy scrypt trên một hash giả để thời gian trả lời như khi sai mật khẩu.
- Mật khẩu tối thiểu 12 ký tự, không có ký tự ẩn/điều khiển (BOM, ký tự thay thế `U+FFFD`, tab, zero-width…; kiểm tra ở CLI, `password_problem`).
- Mật khẩu chuẩn hóa NFC trước khi băm và khi kiểm tra: chữ Việt có dấu gõ dạng dựng sẵn hay tổ hợp đều khớp.
- Token phiên: `pmw_` + `secrets.token_urlsafe(32)`. DB chỉ lưu SHA-256.
- Phiên hết hạn tuyệt đối sau 12 giờ (một ngày làm việc), không gia hạn. Không có hết hạn khi không dùng (quyết định của người viết spec: máy là máy cá nhân của PM, Thunderbird mở cùng lúc cũng không khóa).
- Cookie `pmm_session`: `HttpOnly`, `SameSite=Strict`, `Path=/api`, `Max-Age` = 12 giờ, `Secure` khi request là HTTPS.

## 5. API

| Endpoint | Việc |
|---|---|
| `POST /api/v1/session` | Body `{"email", "password"}`. Đúng → 200 `{"id", "email", "display_name"}` và đặt cookie. Sai, email không có, chưa có mật khẩu → 401 `{"detail": "Email hoặc mật khẩu không đúng"}` (một thông điệp cho mọi trường hợp) |
| `GET /api/v1/session` | Người đang đăng nhập `{"id", "email", "display_name"}`; không có phiên → 401 |
| `DELETE /api/v1/session` | Thu hồi phiên, xóa cookie, 204. Không có phiên cũng 204 |

- Dependency `session_user_id` (dùng cho mọi endpoint của web, spec 14, 15): đọc cookie, gọi `web_session_user`. Không có hoặc hết hạn → 401 `{"detail": "Chưa đăng nhập", "code": "not_authenticated"}`.
- **Chống CSRF**: request `POST`, `PUT`, `PATCH`, `DELETE` của web (gồm cả đăng nhập) phải có header `X-PMM-Request: 1`, thiếu → 403 `{"code": "csrf_header_missing"}`. Trang web khác không gửi được header tùy biến tới API mà không qua CORS preflight, và API không bật CORS. Cùng với `SameSite=Strict`.
- **Tách quyền token và phiên**: endpoint của collector (`/me`, `/ingest`, `/sources`, `/sources/delete-mail`) chỉ nhận token Bearer; endpoint của web chỉ nhận phiên cookie. Token collector (lưu trên máy, mã hóa DPAPI) không đọc được nội dung mail.
- **Giới hạn đăng nhập sai**: 5 lần sai cho một email trong 15 phút → 429 `{"detail": "Đăng nhập sai quá nhiều lần, thử lại sau 15 phút", "code": "too_many_attempts"}` cho tới hết 15 phút tính từ lần sai đầu. Mỗi lần thử giữ chỗ trước khi kiểm tra mật khẩu: số lần sai cộng số lần đang kiểm tra đạt 5 thì từ chối ngay. Đếm trong bộ nhớ của tiến trình API (một tiến trình, giai đoạn một người dùng). Đăng nhập đúng xóa bộ đếm.
- **Header Host**: chỉ nhận `127.0.0.1`, `localhost` (biến `PMM_ALLOWED_HOSTS`), Host khác → 400 (chống DNS rebinding). Collector và trình duyệt phải dùng `http://127.0.0.1:8000` hoặc `http://localhost:8000`.
- Body đọc bằng `read_body` (giới hạn kích thước), validate bằng Pydantic; lỗi 422 không trả lại giá trị gửi lên (mật khẩu).
- Log: đăng nhập đúng `login user=<id>`; sai chỉ `login failed`, không ghi email hay mật khẩu.

## 6. CLI (role owner)

- `set-password --email <e>`: kiểm tra email có trong DB trước (không có → báo lỗi, mã thoát 1, không hỏi mật khẩu), rồi hỏi mật khẩu hai lần (`getpass`, không hiện ra màn hình). `--password-stdin`: đọc một dòng từ stdin dạng byte UTF-8, bỏ BOM ở đầu và `\r\n` ở cuối (cho script, test). Ngắn hơn 12 ký tự, có ký tự ẩn, không phải UTF-8, hoặc hai lần không khớp → báo lỗi, mã thoát 2. Ghi đè mật khẩu cũ và thu hồi mọi phiên web của người đó.
- Dùng chế độ hỏi (mặc định) cho mật khẩu thật. Pipe qua PowerShell có thể chèn BOM (đã xử lý) hoặc, với `$OutputEncoding` mặc định là ASCII của Windows PowerShell 5.1, đổi chữ có dấu thành `?` trước khi tới Python (không phát hiện được).
- `revoke-token` không đụng phiên web; đổi mật khẩu là cách đăng xuất mọi nơi.

## 7. Phân quyền và dữ liệu

- Không có mail trong hai bảng mới. Log không có email, mật khẩu, token.
- `app_api` không `SELECT` được `user_passwords`, `web_sessions`; `app_worker` cũng không.
- Lớp phiên chỉ trả `user_id`; mọi truy vấn mail sau đó chạy trong `user_session` (RLS).

## 8. Kiểm thử

- Đăng nhập đúng → cookie có `HttpOnly`, `SameSite=Strict`, `Path=/api`; `GET /session` trả đúng người. Sai mật khẩu, email lạ, người chưa có mật khẩu → 401 cùng thông điệp.
- Thiếu header `X-PMM-Request` khi đăng nhập, đăng xuất, ghi dữ liệu → 403.
- Đăng xuất → cookie cũ 401. Phiên hết hạn (sửa `expires_at` bằng owner) → 401. `set-password` thu hồi phiên cũ.
- 5 lần sai → lần thứ 6 (kể cả đúng mật khẩu) 429; hết 15 phút (giả thời gian) → đăng nhập được. 20 request sai gửi cùng lúc → đúng 5 lần 401.
- Hàm `SECURITY DEFINER` có `search_path = pg_catalog, pg_temp`; `app_api`, `app_worker` không có quyền TEMP trên database. Host lạ → 400.
- Token collector gọi endpoint web → 401; cookie phiên gọi `/ingest`, `/sources` → 401.
- `app_api`, `app_worker` không `SELECT` được hai bảng; hàm chỉ `app_api` gọi được.
- 422 khi đăng nhập không chứa mật khẩu đã gửi.
- Băm mật khẩu: verify đúng/sai, hai lần băm cùng mật khẩu khác nhau (salt), chuỗi hỏng → sai (không lỗi).

## 9. Giới hạn đã biết

- Bộ đếm đăng nhập sai nằm trong bộ nhớ: khởi động lại API thì mất; chạy nhiều tiến trình API thì mỗi tiến trình đếm riêng. Lên server nhiều người dùng (giai đoạn 7) thì chuyển vào DB hoặc dùng SSO.
- Kẻ tấn công biết email có thể làm người đó bị khóa đăng nhập 15 phút. Gửi liên tục với email ngẫu nhiên giữ 4 suất scrypt bận, làm chậm đăng nhập của người khác (không có giới hạn theo IP; giai đoạn một người dùng chạy trên `127.0.0.1`).
- Cookie không `Secure` khi chạy HTTP trên `127.0.0.1` (dev); cờ `Secure` suy từ scheme của request, nên sau proxy TLS phải cấu hình lại. Cookie không tách theo cổng: app khác trên `127.0.0.1` nhận được cookie nếu đường dẫn bắt đầu bằng `/api`. Lên server (giai đoạn 7): bật HTTPS, cấu hình bắt buộc `Secure`, cân nhắc tiền tố `__Host-`.
- `app_api` đọc được bảng `users`, nên qua `web_password_hash(email)` lấy được hash mật khẩu của mọi người dùng, và `web_session_start` tạo được phiên cho mọi `user_id`. Ngang quyền đặt GUC `app.user_id` mà API vốn có; các hàm chỉ ngăn đọc thẳng bảng.
- Đổi tham số scrypt (`SCRYPT_N`…) thì hash cũ kiểm tra nhanh hơn hash giả của email không tồn tại: đặt lại mật khẩu (`set-password`) cho mọi người để không lộ email nào tồn tại qua thời gian trả lời.

## 10. Sửa sau review bảo mật (2026-10-19)

- **Hàm `SECURITY DEFINER` (`search_path`)**: 0005 và 0010 đặt `search_path = pg_catalog`, thiếu `pg_temp` ở cuối, nên PostgreSQL tra kiểu/bảng trong `pg_temp` trước. Một phiên `app_api` tạo được domain `pg_temp.timestamptz` có CHECK gọi hàm của mình và chạy nó bằng quyền `app_owner` qua `web_session_user`. Migration 0013: `search_path = pg_catalog, pg_temp` cho cả 5 hàm (gồm `auth_token_user`), `REVOKE TEMPORARY ON DATABASE … FROM PUBLIC`. Test kiểm `proconfig` của mọi hàm `SECURITY DEFINER` trong `public` và quyền TEMP của `app_api`, `app_worker`.
- **Giới hạn đăng nhập sai bị vượt bằng request đồng thời** (mọi request qua bước kiểm tra trước khi lần sai đầu được đếm): mỗi lần thử giữ chỗ trước khi băm (`LoginThrottle.begin`/`finish`), lần đang chạy cũng được tính. Test gửi 20 request cùng lúc → 5 lần 401, 15 lần 429.
- **Endpoint đăng nhập làm cạn bộ nhớ** (mỗi scrypt ~32 MiB, threadpool 40 luồng): tối đa 4 lần scrypt cùng lúc (mục 4).
- **DNS rebinding**: trang độc trên tên miền trỏ về `127.0.0.1` được coi là cùng origin, gửi được header `X-PMM-Request`. Thêm `TrustedHostMiddleware`: chỉ nhận Host `127.0.0.1`, `localhost` (đổi bằng biến `PMM_ALLOWED_HOSTS`, phân cách bằng dấu phẩy); Host khác → 400.
- Hash giả tạo theo tham số scrypt hiện hành, lúc dùng lần đầu (`dummy_hash()`).

## 11. Sửa sau khi kiểm tra cách đặt mật khẩu (2026-10-19)

- **Lỗi: `set-password --password-stdin` qua pipe PowerShell lưu cả BOM vào mật khẩu**, CLI vẫn báo đã đặt, nhưng đăng nhập bằng đúng mật khẩu luôn 401 (đã tái hiện trên DB test). Nay đọc byte UTF-8, bỏ BOM, từ chối ký tự ẩn/điều khiển. Kiểm lại thật trên DB test với scrypt thật: mật khẩu ASCII và có dấu pipe từ PowerShell đều đăng nhập được (200), sai → 401.
- Chuẩn hóa NFC mật khẩu (mục 4). Kiểm tra email trước khi hỏi mật khẩu (mục 6).
- Test: pipe có BOM và CRLF, byte không phải UTF-8, chế độ hỏi (khớp/không khớp, email viết hoa có khoảng trắng), email không có không hỏi mật khẩu, `password_problem`, NFC/NFD.
