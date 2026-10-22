"""Phục vụ bản build của web và header bảo mật (spec 2026-10-18-web-ui-design.md mục 7)."""

import os
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

# script chỉ từ file của chính server; 'unsafe-inline' cho style vì Radix (shadcn/ui) chèn thẻ
# <style>. Không tải gì từ bên ngoài.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
BASE_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def web_dist_dir() -> Path | None:
    """Thư mục build của web (PMM_WEB_DIST hoặc web/dist); None nếu chưa build."""
    path = Path(os.environ.get("PMM_WEB_DIST") or DEFAULT_WEB_DIST)
    return path if (path / "index.html").is_file() else None


class SPAStaticFiles(StaticFiles):
    """File có thật thì trả file; đường dẫn khác trả index.html (React Router xử lý).

    Đường dẫn api/… không bao giờ trả index.html: API không có thì là 404 JSON. File không có trong
    assets/ cũng 404 (không để index.html bị cache lâu dưới tên file asset).
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        top = path.replace("\\", "/").split("/", 1)[0]
        if top == "api":
            raise HTTPException(status_code=404)
        if top == "assets":
            return await super().get_response(path, scope)
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


class SecurityHeaders:
    """Header bảo mật cho mọi response; /api/… không được lưu cache (có nội dung mail)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in BASE_HEADERS.items():
                    headers[key] = value
                if path.startswith("/api/") or path == "/api":
                    headers["Cache-Control"] = "no-store"
                elif path.startswith("/assets/") and message.get("status") == 200:
                    # Tên file có hash, không có nội dung mail.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                else:
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)
