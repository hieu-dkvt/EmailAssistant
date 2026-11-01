"""Nhà cung cấp AI tùy chỉnh theo người dùng: OpenRouter, 9router…

Spec 2026-10-25-ai-provider-design.

- validate_base_url: http/https tới mọi địa chỉ, chỉ chặn URL sai dạng và link-local
  (spec 28 mục 3). is_public_host báo địa chỉ công cộng để web cảnh báo.
- OpenAiLlm: client chat/completions tương thích OpenAI (mục 3).
- load/save/delete_provider và LlmResolver: cấu hình lưu DB (RLS), key mã hóa (mục 4, 5).
- Bên gọi chỉ đưa vào đây text ĐÃ CHE (spec 20; bỏ ở spec 28 task 3-4). Log chỉ có
  host/giao thức, không có key, URL đầy đủ hay body phản hồi.
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai import secrets
from app.ai.client import (
    TIMEOUT_SECONDS,
    LlmClient,
    LlmError,
    LlmResponse,
    LlmUsage,
)
from app.ai.models import Effort
from app.db.session import user_session

log = logging.getLogger("app.ai")

type Protocol = Literal["anthropic", "openai"]
PROTOCOLS: tuple[Protocol, ...] = ("anthropic", "openai")
MAX_RETRIES = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """IP từ chuỗi (bỏ zone id "%eth0"); None nếu không phải IP."""
    try:
        return ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return None


def _is_link_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:169.254.169.254
    return ip.is_link_local


def validate_base_url(raw: str) -> str:
    """URL gốc hợp lệ (đã chuẩn hóa, không có "/" cuối) hoặc ValueError.

    Nhận mọi địa chỉ (LAN, localhost, công cộng); chỉ từ chối URL sai dạng và link-local.
    """
    raw = raw.strip()
    try:
        url = urlsplit(raw)
        port = url.port  # ném ValueError khi cổng không phải số hoặc ngoài 0–65535
    except ValueError:
        raise ValueError("URL không hợp lệ") from None
    scheme = url.scheme.lower()
    host = (url.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError("URL phải bắt đầu bằng http:// hoặc https://")
    if url.username is not None or url.password is not None or "@" in url.netloc:
        raise ValueError("URL không được chứa tài khoản")
    if url.query or url.fragment:
        raise ValueError("URL không được chứa query hoặc #")
    if port is not None and port < 1:
        raise ValueError("Cổng không hợp lệ")
    ip = _parse_ip(host)
    if ip is not None:
        if _is_link_local(ip):
            raise ValueError("Không nhận địa chỉ link-local")
    else:
        # Dạng viết tắt của IP (2130706433, 0x7f.1…) không nhận.
        last = host.rsplit(".", 1)[-1]
        if last.isdigit() or last.startswith("0x"):
            raise ValueError("Tên máy chủ không hợp lệ")
    netloc = f"[{host}]" if ip is not None and ip.version == 6 else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}{url.path.rstrip('/')}"


async def resolve_ips(host: str, port: int) -> list[str]:
    """Địa chỉ IP của host (tách ra để test thay thế, không gọi DNS thật)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(i[4][0]) for i in infos]


def _default_port(url: SplitResult) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


async def reject_link_local(base_url: str) -> None:
    """ValueError nếu host phân giải về địa chỉ link-local (gồm metadata của cloud).

    Không phân giải được thì bỏ qua: lỗi mạng sẽ báo ở lần gọi. Giới hạn: DNS có thể đổi giữa
    lúc kiểm tra và lúc kết nối (rebinding).
    """
    url = urlsplit(base_url)
    try:
        ips = await resolve_ips(url.hostname or "", _default_port(url))
    except OSError:
        return
    for raw in ips:
        ip = _parse_ip(raw)
        if ip is not None and _is_link_local(ip):
            raise ValueError("Tên máy chủ phân giải về địa chỉ link-local")


async def is_public_host(base_url: str) -> bool:
    """Host là IP công cộng hoặc phân giải ra ít nhất một IP công cộng (không được: False)."""
    url = urlsplit(base_url)
    host = url.hostname or ""
    literal = _parse_ip(host)
    if literal is not None:
        return literal.is_global
    try:
        ips = await resolve_ips(host, _default_port(url))
    except OSError:
        return False
    return any((ip := _parse_ip(raw)) is not None and ip.is_global for raw in ips)


@dataclass(frozen=True)
class ProviderConfig:
    protocol: Protocol
    base_url: str
    api_key: str

    def fingerprint(self) -> str:
        raw = f"{self.protocol}\0{self.base_url}\0{self.api_key}".encode()
        return hashlib.sha256(raw).hexdigest()

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""


# --- client OpenAI-compatible ---

FINISH_REASONS = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


def _llm_error(code: str, http_status: int, message: str, cause: str) -> LlmError:
    log.warning("ai provider error type=%s", cause)
    return LlmError(code, http_status, message)


class OpenAiLlm:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        for name in ("httpx", "httpcore"):  # debug log của httpx có cả URL/header
            logging.getLogger(name).setLevel(logging.WARNING)
        self._url = f"{base_url}/chat/completions"
        self._key = api_key
        self._retries = max_retries
        self._http = httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, object],
        max_tokens: int,
        effort: Effort,  # noqa: ARG002 (gateway không có tham số tương đương)
    ) -> LlmResponse:
        strict: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
        resp = await self._post(model, system, user, max_tokens, strict)
        if resp.status_code == 400:
            # Gateway/model không hỗ trợ json_schema: hỏi lại bằng json_object, schema trong prompt.
            hint = f"{system}\n\nTrả về đúng một đối tượng JSON theo schema:\n{json.dumps(schema)}"
            resp = await self._post(model, hint, user, max_tokens, {"type": "json_object"})
        return self._parse(resp, model)

    async def _post(
        self, model: str, system: str, user: str, max_tokens: int, response_format: dict[str, Any]
    ) -> httpx.Response:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_format,
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        last: Exception | None = None
        last_status = 0
        for _ in range(self._retries + 1):
            try:
                resp = await self._send(headers, json.dumps(body))
            except httpx.HTTPError as e:  # lỗi mạng, timeout
                last = e
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = None
                last_status = resp.status_code
                continue
            return resp
        # Chi tiết hiện ra màn hình: host, model, mã HTTP, số lần thử; không có body phản hồi.
        where = (
            f"host {urlsplit(self._url).hostname}, model {model}, đã thử {self._retries + 1} lần"
        )
        if last is not None:
            raise _llm_error(
                "ai_unavailable",
                503,
                f"Không kết nối được nhà cung cấp AI ({type(last).__name__}; {where})",
                type(last).__name__,
            )
        message = (
            "Nhà cung cấp AI giới hạn tốc độ: chờ một lúc, đổi model hoặc nạp thêm hạn mức"
            if last_status == 429
            else "Nhà cung cấp AI đang quá tải hoặc lỗi"
        )
        raise _llm_error(
            "ai_unavailable",
            503,
            f"{message} (HTTP {last_status}; {where})",
            f"Status{last_status}",
        )

    async def _send(self, headers: dict[str, str], content: str) -> httpx.Response:
        """POST và đọc body có giới hạn kích thước (gateway độc hại không làm đầy RAM)."""
        async with self._http.stream("POST", self._url, headers=headers, content=content) as r:
            chunks: list[bytes] = []
            size = 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise _llm_error(
                        "ai_bad_output", 502, "Phản hồi của nhà cung cấp AI quá lớn", "TooLarge"
                    )
                chunks.append(chunk)
            return httpx.Response(r.status_code, content=b"".join(chunks))

    def _parse(self, resp: httpx.Response, model: str) -> LlmResponse:
        status = resp.status_code
        if status in (401, 403):
            raise _llm_error(
                "ai_auth_failed", 503, "API key của nhà cung cấp AI không hợp lệ", "Auth"
            )
        if status != 200:  # 3xx (không theo redirect), 400 lần hai, 404…
            raise _llm_error("ai_bad_request", 502, "Nhà cung cấp AI từ chối request", "Status")
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            raise _llm_error(
                "ai_bad_output", 502, "Nhà cung cấp AI trả kết quả sai định dạng", "Shape"
            ) from None
        return LlmResponse(
            text=content if isinstance(content, str) else None,
            model=str(data.get("model") or model),
            stop_reason=FINISH_REASONS.get(str(finish), "end_turn"),
            usage=LlmUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
        )


def build_llm(config: ProviderConfig) -> LlmClient:
    log.info("ai provider host=%s protocol=%s", config.host, config.protocol)
    if config.protocol == "openai":
        return OpenAiLlm(base_url=config.base_url, api_key=config.api_key)
    return _anthropic_llm(config)


def _anthropic_llm(config: ProviderConfig) -> LlmClient:
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

    from app.ai.client import AnthropicLlm, _quiet_sdk_logs

    _quiet_sdk_logs()
    return AnthropicLlm(
        AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
            open_telemetry=False,
            # SDK mặc định theo redirect: header x-api-key và mail đã che sẽ bị gửi tới host khác.
            http_client=DefaultAsyncHttpxClient(follow_redirects=False, timeout=TIMEOUT_SECONDS),
        )
    )


# --- DB (RLS: chỉ dòng của chính người dùng) ---

SELECT_PROVIDER = text("SELECT protocol, base_url, api_key_enc FROM user_ai_provider")
UPSERT_PROVIDER = text(
    """
    INSERT INTO user_ai_provider (user_id, protocol, base_url, api_key_enc)
    VALUES (:u, :protocol, :url, :key)
    ON CONFLICT (user_id) DO UPDATE SET protocol = excluded.protocol,
        base_url = excluded.base_url, api_key_enc = excluded.api_key_enc, updated_at = now()
    """
)
DELETE_PROVIDER = text("DELETE FROM user_ai_provider")


@dataclass(frozen=True)
class StoredProvider:
    protocol: Protocol
    base_url: str
    api_key_enc: str

    def key(self) -> str | None:
        return secrets.decrypt(self.api_key_enc)


async def load_provider(engine: AsyncEngine, user_id: int) -> StoredProvider | None:
    async with user_session(engine, user_id) as s:
        row = (await s.execute(SELECT_PROVIDER)).first()
    if row is None:
        return None
    return StoredProvider(row.protocol, row.base_url, row.api_key_enc)


async def save_provider(
    engine: AsyncEngine, user_id: int, protocol: Protocol, base_url: str, api_key: str
) -> None:
    enc = secrets.encrypt(api_key)
    async with user_session(engine, user_id) as s:
        await s.execute(
            UPSERT_PROVIDER, {"u": user_id, "protocol": protocol, "url": base_url, "key": enc}
        )


async def delete_provider(engine: AsyncEngine, user_id: int) -> None:
    async with user_session(engine, user_id) as s:
        await s.execute(DELETE_PROVIDER)


def key_hint(key: str | None) -> str | None:
    return key[-4:] if key and len(key) >= 16 else None


def _close_later(client: LlmClient) -> None:
    """Đóng kết nối của client bị thay thế (nếu có)."""
    inner = getattr(client, "_client", None)
    closer = getattr(client, "aclose", None) or getattr(inner, "close", None)
    if closer is None:
        return
    result = closer()
    if asyncio.iscoroutine(result):
        asyncio.ensure_future(result)


class LlmResolver:
    """Chọn client theo người dùng từ nhà cung cấp đã lưu trong DB; chưa nhập thì None.

    `default` chỉ để test truyền client giả: không có đường đọc môi trường (spec 28 mục 4).
    """

    def __init__(self, default: LlmClient | None = None) -> None:
        self.default = default
        self._cache: dict[int, tuple[str, LlmClient]] = {}

    def forget(self, user_id: int) -> None:
        cached = self._cache.pop(user_id, None)
        if cached is not None:
            _close_later(cached[1])

    async def for_user(self, engine: AsyncEngine, user_id: int) -> LlmClient | None:
        """Client của người dùng, hoặc None nếu chưa cấu hình."""
        stored = await load_provider(engine, user_id)
        if stored is None:
            self.forget(user_id)
            return self.default
        key = stored.key()
        if key is None:
            raise LlmError("ai_auth_failed", 503, "Không đọc được API key đã lưu, hãy nhập lại")
        config = ProviderConfig(stored.protocol, stored.base_url, key)
        cached = self._cache.get(user_id)
        if cached is None or cached[0] != config.fingerprint():
            try:
                await reject_link_local(config.base_url)
            except ValueError:
                raise LlmError(
                    "ai_bad_request", 502, "Địa chỉ nhà cung cấp AI không hợp lệ"
                ) from None
            self.forget(user_id)
            cached = (config.fingerprint(), build_llm(config))
            self._cache[user_id] = cached
        return cached[1]
