# Spec: Giao diện web (danh sách thread, xem thread, tìm kiếm, sửa tay)

Ngày: 2026-10-18 · Thành phần: `web/`, `backend/app/web_static.py`, `backend/app/main.py` (header bảo mật), `backend/app/openapi_dump.py` · Trạng thái: xong (sửa sau review bảo mật: mục 10)

## 1. Mục đích

PM mở trình duyệt, đăng nhập, xem thread mới nhất, tìm mail bằng từ khóa có tô màu, đọc thread dạng hội thoại, xem toàn văn một mail, và sửa thread gom sai. Dùng thay cho search của Thunderbird (tiêu chí giai đoạn 4).

## 2. Không làm

- Lọc theo dự án, dashboard, trạng thái đối ứng, tóm tắt, hỏi đáp AI, soạn nháp: giai đoạn sau.
- Mở mail gốc trong Thunderbird (spec 15 mục 2).
- Gửi mail, trả lời, đánh dấu đã đọc: tool chỉ đọc.
- Giao diện cho điện thoại: bố cục co được nhưng không tối ưu cho màn hình nhỏ.

## 3. Công nghệ và cấu trúc

React 19, Vite, TypeScript, TanStack Query, React Router, Tailwind CSS 4, shadcn/ui (component chép vào `src/components/ui/`), lucide-react. Client API: `openapi-typescript` sinh kiểu từ `web/openapi.json` (xuất từ FastAPI), `openapi-fetch` gọi API theo kiểu đã sinh. Test: Vitest + Testing Library. ESLint + Prettier.

```
web/openapi.json            # sinh: npm run gen:api (python -m app.openapi_dump)
web/src/api/schema.ts       # sinh từ openapi.json, không sửa tay
web/src/api/client.ts       # openapi-fetch + header X-PMM-Request + chuyển về /login khi 401
web/src/components/ui/      # shadcn/ui
web/src/components/         # Highlighted, FilterBar, Layout, MessageCard, MergeDialog, rethread
web/src/lib/                # filters (URL ↔ tham số API), format, links
web/src/pages/              # Login, ThreadList, Search, Thread, Overrides
```

## 4. Màn hình

Mọi chữ trên giao diện bằng tiếng Việt; nội dung mail hiển thị nguyên văn (giữ xuống dòng, không tự tạo link).

| Đường dẫn | Nội dung |
|---|---|
| `/login` | Email, mật khẩu, nút "Đăng nhập". Lỗi 401/429 hiện thông điệp của API. Đăng nhập xong về trang đang mở trước đó (`?next=`, chỉ nhận đường dẫn nội bộ bắt đầu bằng `/`) |
| `/threads` | Danh sách thread: subject, người gửi mail mới nhất, số mail, ngày mới nhất, biểu tượng đính kèm. Thanh lọc (folder, người gửi, từ ngày, đến ngày, có đính kèm). Nút "Tải thêm" (cursor) |
| `/search?q=…` | Kết quả search: subject và đoạn trích có từ khóa tô `<mark>`, người gửi, ngày, folder, số mail của thread. Cùng thanh lọc, sắp theo ngày hoặc độ liên quan, phân trang. Bấm kết quả → mở thread, cuộn tới mail đó, giữ tô từ khóa |
| `/threads/:id` | Hội thoại: các mail theo thời gian, mỗi mail một khung (người gửi, To/Cc, ngày, folder, file đính kèm chỉ tên + kích thước, nội dung đã làm sạch). Nút "Toàn văn" mở nội dung đầy đủ, phần quote/chữ ký đã cắt hiện mờ. Ghi chú khi mail chưa được làm sạch hoặc nội dung bị cắt |
| `/overrides` | Danh sách sửa tay (tách/gộp, mail liên quan, thời gian), nút "Hoàn tác" |

Đầu trang: tên ứng dụng, ô tìm kiếm (gõ rồi Enter → `/search`, giữ bộ lọc đang chọn), liên kết "Thread", "Sửa tay", trạng thái xử lý ("Đang xử lý N mail mới", "Đang gom lại thread…" từ `GET /processing`), tên người dùng, "Đăng xuất".

Trạng thái lọc, trang, từ khóa nằm trên URL (dán link mở lại được), không lưu vào storage.

## 5. Sửa thread bằng tay

- **Tách**: nút "Tách từ mail này" trên mỗi mail không phải mail đầu thread. Hộp xác nhận nói rõ "mail này và các mail trả lời nó sẽ thành thread riêng". Gửi `POST /thread-overrides` (split).
- **Gộp**: nút "Gộp vào thread khác…" ở đầu thread mở hộp thoại có ô tìm kiếm (dùng `GET /search`); chọn một kết quả thuộc thread khác → xác nhận → `POST /thread-overrides` (merge, `message_id` = mail đầu của thread đang xem, `target_message_id` = mail đã chọn).
- Sau khi gửi: hiện "Đang gom lại thread…", hỏi `GET /processing` mỗi 2 giây tới khi `rethread_pending = false`, rồi `GET /messages/{id}` để mở thread mới của mail.
- **Hoàn tác** trên `/overrides`: `DELETE`, cùng cách chờ.

## 6. Dữ liệu và bảo mật phía trình duyệt

- Không lưu nội dung mail vào `localStorage`, `sessionStorage`, IndexedDB. Cache của TanStack Query chỉ trong bộ nhớ.
- Mọi request ghi gửi header `X-PMM-Request: 1` (spec 13); cookie phiên `HttpOnly`, JavaScript không đọc được.
- Không dùng `dangerouslySetInnerHTML`. Từ khóa được tô bằng cách ghép các đoạn `{text, hit}` của API (spec 14 mục 6).
- `<html lang="vi" translate="no">` và `<meta name="google" content="notranslate">`: trình duyệt không gợi ý dịch trang (gửi nội dung mail cho dịch vụ dịch bên ngoài).
- Không tải font, script, ảnh từ bên ngoài.
- Ô tìm kiếm, ô người gửi có `autoComplete="off"`: trình duyệt không lưu lịch sử từ khóa, địa chỉ xuống đĩa.
- `?next=` sau đăng nhập được phân tích bằng `URL` như trình duyệt và chỉ nhận khi cùng origin (chặn cả `/\t/evil…`, `/\\evil…`, `/.//evil…`).

## 7. Backend phục vụ web

- `create_app` mount `web/dist` (bản build) tại `/` nếu thư mục tồn tại (đổi được bằng biến `PMM_WEB_DIST`): file có thật thì trả file, đường dẫn khác trả `index.html` (React Router xử lý). Đường dẫn bắt đầu bằng `api/` không bao giờ trả `index.html`.
- Header cho mọi response:
  - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` (`'unsafe-inline'` cho style vì Radix chèn thẻ `<style>`; script chỉ từ file của chính server).
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`.
  - `/api/…`: `Cache-Control: no-store` (nội dung mail không nằm trong cache đĩa của trình duyệt). `index.html`: `no-cache`. File trong `assets/` (tên có hash, không có mail): cache lâu.
- Dev: `npm run dev` (Vite, `http://127.0.0.1:5173`) proxy `/api` sang `http://127.0.0.1:8000`. Chạy thử như người dùng: `npm run build` rồi mở `http://127.0.0.1:8000`.
- `python -m app.openapi_dump <file>` ghi OpenAPI của app (không cần DB) cho `npm run gen:api`.

## 8. Kiểm thử

- Vitest: tô đoạn `hit`; dựng tham số lọc từ URL và ngược lại (ngày theo giờ trình duyệt); `?next=` chỉ nhận đường dẫn nội bộ; trang đăng nhập gửi đúng body và header; client chuyển về `/login` khi 401.
- `npm run lint`, `npm run build` sạch; `tsc` không lỗi.
- Backend: đường dẫn SPA trả `index.html`; `/api/không-có` trả 404 JSON; header bảo mật và `Cache-Control` đúng; không có `web/dist` thì app vẫn chạy.
- Chạy thử đầu cuối với **profile giả** trên DB test (không dùng DB dev có mail thật): đăng nhập, danh sách thread, search `見積` có tô, mở thread, toàn văn, tách rồi hoàn tác. Chụp màn hình bằng trình duyệt Edge headless để kiểm tra bố cục.
- Kết quả chạy thử (2026-10-18, Edge headless qua `playwright-core`, API cổng 8001 + worker trên DB test, profile giả 1.050 mail / 88 thread): đăng nhập sai hiện thông điệp, đúng thì về trang `?next=`; lọc "có đính kèm" 24 thread; danh sách 50 thread mỗi trang; search `見積` 113 kết quả có tô; thread 3 mail hiện đính kèm, số đoạn đã ẩn, toàn văn với quote/chữ ký mờ; tách mail thứ 3 → thread mới sau khoảng 2–6 giây, gộp lại qua hộp thoại → về thread cũ 3 mail, hoàn tác cả hai → 3 mail; đăng xuất → API trả 401. Không có lỗi JavaScript hay vi phạm CSP (chỉ có 401 dự kiến). Lần chạy đầu thấy một 404 do trang làm mới thread vừa bị xóa khi gộp; đã sửa (chuyển trang trước, chỉ đánh dấu cache cũ).

## 9. Giới hạn đã biết

- Thread rất dài (hàng trăm mail) hiện toàn bộ trên một trang, chưa ảo hóa danh sách.
- Khi đang chờ gom lại, các thread khác vẫn hiện kết quả cũ cho tới khi worker xong.
- Vite dev server in URL kèm query (từ khóa) khi proxy lỗi (backend tắt). Chỉ ảnh hưởng chế độ dev; bản build do backend phục vụ không qua Vite.

## 10. Sửa sau review bảo mật (2026-10-19)

- `safeNext` cho qua `/%09/evil.example.com` (trình duyệt bỏ tab thành `//evil…`); React Router chặn điều hướng ra ngoài nên chỉ làm hỏng trang. Nay phân tích bằng `URL` và so origin; thêm test.
- Thêm `autoComplete="off"` cho ô tìm kiếm, ô người gửi, ô tìm trong hộp thoại gộp.
- Backend kiểm tra header Host (spec 13 mục 10), chống DNS rebinding.
