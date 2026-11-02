# Web (React + Vite + TypeScript)

Giao diện cho PM, BrSE, leader: danh sách thread, xem thread dạng hội thoại, tìm kiếm có tô từ khóa, sửa thread gom sai (spec 16 `docs/superpowers/specs/2026-10-18-web-ui-design.md`); tìm kiếm ngữ nghĩa, gợi ý thread liên quan, hỏi đáp AI, cài đặt AI (spec 19, 21, 22, 23, 24, 28); cấu hình dự án (spec 25); dịch mail, glossary, chuyển mail sang folder dự án (spec 26). Sau này: dashboard theo dõi đối ứng.

## Công nghệ

React 19, Vite 8, TypeScript 5.9, TanStack Query 5, React Router 8 (chế độ khai báo `<BrowserRouter>`/`<Routes>`), Tailwind CSS 4 (`@tailwindcss/vite`), shadcn/ui (component chép trong `src/components/ui/`, hàm `cn` từ gói `cn` của shadcn), lucide-react. Client API: `openapi-typescript` + `openapi-fetch`. Test: Vitest + Testing Library (jsdom). ESLint (flat config) + Prettier.

- TypeScript ghim `^5.9`: `openapi-typescript` 7 chỉ nhận TS 5 (TS 6, 7 đã có).
- Thêm component shadcn: `npx shadcn@4.22.0 add <tên> --yes` (đứng trong `web/`), rồi `npm run format`. Đổi chữ tiếng Anh trong component sang tiếng Việt (vd. "Close" → "Đóng").

## Cấu trúc

```
openapi.json               # SINH bởi npm run gen:api (OpenAPI của backend), không sửa tay
src/api/schema.ts          # SINH từ openapi.json, không sửa tay
src/api/client.ts          # openapi-fetch: header X-PMM-Request cho request ghi, 401 → handler, unwrap/ApiError
src/api/hooks.ts           # TanStack Query: useMe, useThreads (cursor), useSearch (mode), useThread,
                           #   useProjects/useCreateProject/useUpdateProject/useDeleteProject, useFolders,
                           #   useGlossary/useSaveGlossaryTerm/useDeleteGlossaryTerm/useImportGlossary/useExportGlossary,
                           #   useThreadTranslations/useTranslateMessage/useMoveBack/useMoveMessage/useAutoRetry (spec 26),
                           #   useSimilarThreads, useMessageFull, useProcessing, useOverrides, useAsk,
                           #   useAiSettings/useSaveAiSettings, useAiUsage, mutation đăng nhập/đăng xuất/sửa tay
src/lib/filters.ts         # bộ lọc trên URL ↔ tham số API (ngày theo giờ trình duyệt), safeNext
src/lib/format.ts, links.ts, tags.ts (gộp giá trị cho TagInput)
src/components/            # Layout (đầu trang, ô tìm kiếm, trạng thái xử lý), FilterBar, Highlighted,
                           #   MessageCard (toàn văn, tách, bôi đen để thêm glossary), MessageTranslation (hai cột bản gốc / bản dịch,
                           #   cảnh báo, nút Dịch / Dịch lại, MovePicker chọn dự án, Chuyển lại Inbox, Thử lại),
                           #   AddGlossaryDialog (thêm từ đoạn bôi đen), GlossaryTable (bảng dùng chung: tìm, phân trang,
                           #   sửa trực tiếp, nhập có xem trước, dán nhiều dòng, xuất CSV), MergeDialog, SimilarThreads (gợi ý gộp),
                           #   rethread.tsx + rethread-context.ts,
                           #   TagInput (ô nhiều giá trị, `draftRef` giữ phần đang gõ)
src/components/ui/         # shadcn/ui: button, input, label, badge, card, dialog, alert
src/pages/                 # LoginPage, ThreadListPage, SearchPage (Từ khóa / Ngữ nghĩa), ThreadPage,
                           #   OverridesPage, AskPage (/ask), ProjectsPage (/settings/projects: dự án, bộ lọc gán mail, khối "Dịch tự động" gồm domain khách và folder đích, tab Glossary của dự án), GlossaryPage (/settings/glossary: glossary chung hoặc `?project=id`), AiSettingsPage (/settings/ai: nhà cung cấp AI (URL, key, giao thức), tên model theo tác vụ, thống kê token)
src/**/*.test.ts(x)        # Vitest
```

## Lệnh

Chạy trong `web/` (hoặc `npm --prefix web run …` từ thư mục gốc).

| Việc                                                              | Lệnh                                                                                     |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Cài                                                               | `npm install`                                                                            |
| Dev (`http://127.0.0.1:5173`, `/api` proxy sang `127.0.0.1:8000`) | `npm run dev` (backend: `python -m app.run`)                                             |
| Build (`dist/`, backend phục vụ ở `http://127.0.0.1:8000`)        | `npm run build` (gồm `tsc -b`)                                                           |
| Test                                                              | `npm run test`                                                                           |
| Lint + kiểm tra format / format                                   | `npm run lint` · `npm run format`                                                        |
| Sinh client API từ OpenAPI của backend                            | `npm run gen:api` (cần `uv`; chạy `python -m app.openapi_dump` rồi `openapi-typescript`; trên Windows đặt `$env:PYTHONUTF8 = "1"` trước) |

## Quy tắc

- Không viết tay kiểu dữ liệu hay đường dẫn API: lấy từ `src/api/schema.ts`. Backend đổi schema thì chạy `npm run gen:api` rồi `npm run build` để `tsc` báo chỗ hỏng.
- Không chứa logic nghiệp vụ hay phân quyền. Quyền do backend và DB kiểm soát; ẩn nút trên UI chỉ là tiện ích, không phải bảo mật. Tô từ khóa, đoạn trích, chia phần quote/chữ ký đều do backend trả (danh sách đoạn `{text, hit}`); web chỉ ghép.
- Không lưu nội dung mail vào localStorage, sessionStorage hay IndexedDB (ESLint chặn). Cache TanStack Query chỉ trong bộ nhớ; đăng xuất hoặc phiên hết hạn thì xóa cache.
- Không dùng `dangerouslySetInnerHTML` (ESLint chặn). Nội dung mail hiển thị dạng text (`<pre>` giữ xuống dòng), không tự tạo link.
- Request ghi phải có header `X-PMM-Request: 1` (middleware trong `client.ts` tự thêm). Cookie phiên `HttpOnly`, JavaScript không đọc.
- Không tải font, script, ảnh từ bên ngoài: backend đặt CSP `default-src 'self'`. `index.html` có `translate="no"`.
- Trạng thái lọc, trang, từ khóa nằm trên URL. `?next=` sau đăng nhập chỉ nhận đường dẫn nội bộ (`safeNext` phân tích bằng `URL` và so origin, không so chuỗi).
- Ô nhập từ khóa, người gửi có `autoComplete="off"` (trình duyệt không lưu lịch sử xuống đĩa).
- Backend chỉ nhận Host `127.0.0.1`, `localhost` (biến `PMM_ALLOWED_HOSTS`): mở web bằng một trong hai tên này.
- Vite dev in URL kèm query (từ khóa) khi proxy lỗi; không gửi log của `npm run dev` ra ngoài khi đang dùng dữ liệu thật.
- Function component + hooks. Không gọi `setState` đồng bộ trong `useEffect` (rule `react-hooks/set-state-in-effect`): việc chờ worker gom lại chạy trong callback (`rethread.tsx`).
- Giao diện tiếng Việt; nội dung mail hiển thị nguyên văn.
- Bản nháp reply (giai đoạn 6) chỉ có nút copy, không có nút gửi.
- Bản dịch hiển thị dạng text thuần (không render HTML), lấy từ `GET /threads/{id}/translations` (một request cho cả thread, `MessageCard` nhận `translation` theo `message_id`). `MovePicker` hiện cho mọi mail `done` chưa có folder đích khi `move_projects` không rỗng, kể cả sau "Chuyển lại Inbox". Chuyển folder chỉ trên web: Thunderbird vẫn thấy mail ở Inbox.
- Hỏi đáp AI: câu hỏi và kết quả chỉ nằm trong state của mutation (không lưu storage, không đưa lên URL). Câu trả lời hiển thị dạng text (`whitespace-pre-wrap`); chỉ `[n]` được đổi thành link tới thread của nguồn n. Luôn có khối "Nội dung đã gửi cho AI" hiện đúng text gốc mà backend gửi đi, để người dùng tự kiểm tra.
- Từ spec 28 mail gửi AI không che (dùng model nội bộ): trang Cài đặt AI luôn hiện "Mail không che sẽ gửi tới <host>" và thêm `Alert` cảnh báo khi `public_host` (host là địa chỉ công cộng, theo URL đã lưu, không theo URL đang gõ). Trang không có ba cấp model: mỗi tác vụ một ô tên model; tác vụ thiếu tên model không có gợi ý trên trang, người dùng thấy lỗi `ai_model_missing` khi dùng chức năng. `MessageTranslation` còn giữ chữ cho cảnh báo `labels_missing`, `labels_unknown` vì bản dịch cũ trong DB còn mã này.
- Chạy thử giao diện chỉ với dữ liệu giả (profile tự sinh trên DB test), không mở web trên DB dev có mail thật khi Claude Code đọc màn hình.
