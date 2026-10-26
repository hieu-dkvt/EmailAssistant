# Nhà cung cấp AI tùy chỉnh: kế hoạch triển khai

> Thực hiện tuần tự trong một phiên (native). Mỗi bước đánh dấu khi xong.

**Mục tiêu:** người dùng nhập URL, API key, tên model ở trang Cài đặt AI để hỏi đáp AI chạy qua OpenRouter/9router.
**Spec:** [2026-10-25-ai-provider-design.md](../specs/2026-10-25-ai-provider-design.md)
**Công nghệ:** FastAPI, SQLAlchemy (SQL thô), `httpx`, `cryptography` (Fernet), React.

## Ràng buộc chung
- Che thông tin (spec 20) áp dụng cho mọi provider; log không có key, URL đầy đủ, body phản hồi.
- `https://` công cộng; `http://` chỉ loopback; chặn IP riêng, userinfo, query; không theo redirect.
- API không trả key; chưa có `PMM_SECRET_KEY` thì không lưu key.

## Tập trung kiểm tra (ngoài spec)
- URL dạng `https://127.0.0.1`, `http://[::1]`, `https://user@host`, `https://host?x=1`, `https://10.0.0.1`, host viết hoa.
- Gateway trả 200 nhưng `choices` rỗng hoặc `content` null.
- Đổi `PMM_SECRET_KEY` sau khi lưu: không crash.

## Task 1: Mã hóa key và kiểm tra URL
- Tạo `backend/app/ai/secrets.py`: `encrypt(plain) -> str`, `decrypt(token) -> str | None`, `secret_key_ready() -> bool`, `SecretKeyMissing`.
- Tạo `backend/app/ai/provider.py` (phần 1): `validate_base_url(url) -> str` (ném `ValueError`), `ProviderConfig`.
- Test `backend/tests/test_ai_provider.py`: mã hóa vòng, khóa sai → `None`, bảng URL hợp lệ/không.
- Thêm `cryptography` vào `pyproject.toml`; CLI `gen-secret-key`.

## Task 2: Client OpenAI-compatible
- `provider.py` (phần 2): `OpenAiLlm(LlmClient)` với `httpx.AsyncClient(transport=...)` tiêm được, `follow_redirects=False`.
- `client.call_task(..., model: str | None = None)`: ưu tiên `model` hơn `model_for(tier)`; `fallbacks=False` khi `model` tự nhập.
- Test: `json_schema` ok, lùi `json_object` khi 400, `length`, `content_filter`, 401/429/5xx, redirect không theo, `choices` rỗng → `ai_bad_output`, thông điệp lỗi không chứa body.

## Task 3: DB và truy cập
- Migration `0016_ai_provider.py`: bảng `user_ai_provider` (+RLS, GRANT), cột `user_ai_settings.model`, GRANT cột mới. Model SQLAlchemy `UserAiProvider`.
- `provider.py` (phần 3): `load_provider`, `save_provider`, `delete_provider`; `LlmResolver.for_user(engine, user_id) -> LlmClient | None` (cache theo hash cấu hình, fallback client môi trường).
- `settings.py`: `user_models`, `save_models`.
- Test RLS (`test_rls.py` hoặc file mới): B không thấy provider của A.

## Task 4: API
- `web_schemas.py`: `AiProviderInfo`, `AiProviderUpdate`, `AiProviderTest`; `AiSettings` thêm `custom_provider`, `AiTaskInfo.model`; `AiSettingsUpdate.models`.
- `ai_routes.py`: `GET/PUT/DELETE /ai/provider`, `POST /ai/provider/test`; `post_ask` dùng resolver và truyền `models` vào `ask()`.
- `main.py`: `app.state.llm_resolver`.
- Test `test_ai_api.py`: key không lộ, giữ key khi bỏ trống, DELETE về môi trường, 401/403/422, ask dùng model tự nhập.

## Task 5: Web
- `npm run gen:api`; hooks `useAiProvider`, `useSaveAiProvider`, `useDeleteAiProvider`, `useTestAiProvider`.
- `AiSettingsPage.tsx`: khối "Nhà cung cấp AI", ô model theo tác vụ, cảnh báo NDA.
- Test Vitest cập nhật `AiSettingsPage.test.tsx`.

## Task 6: Tài liệu và kiểm tra cuối
- Spec 24 → "xong", README spec, CHANGELOG, CLAUDE.md gốc (NDA, trạng thái), `backend/CLAUDE.md`, `web/CLAUDE.md`, `email-assistant-plan.md`.
- Chạy `pytest`, `ruff`, `pyright`, `npm run lint/test/build`; dùng `security-reviewer`.
