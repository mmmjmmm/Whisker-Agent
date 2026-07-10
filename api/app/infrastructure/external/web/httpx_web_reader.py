import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from app.domain.external.web_reader import WebReadResult


MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 100_000
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/xhtml+xml",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class WebReadError(RuntimeError):
    pass


class UnsafeURL(WebReadError):
    pass


class UnsupportedContent(WebReadError):
    pass


class WebContentTooLarge(WebReadError):
    pass


Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_hostname(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.run_in_executor(
        None,
        lambda: socket.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        ),
    )
    return sorted({record[4][0] for record in records})


def _unsafe_reason(address: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "invalid"
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    checks = {
        "private": parsed.is_private,
        "loopback": parsed.is_loopback,
        "link-local": parsed.is_link_local,
        "multicast": parsed.is_multicast,
        "reserved": parsed.is_reserved,
        "unspecified": parsed.is_unspecified,
    }
    return next((name for name, unsafe in checks.items() if unsafe), None)


def _validate_address(address: str, source: str) -> None:
    reason = _unsafe_reason(address)
    if reason:
        raise UnsafeURL(f"{source} address is {reason}: {address}")


class HttpxWebReader:
    def __init__(
            self,
            transport: httpx.AsyncBaseTransport | None = None,
            resolver: Resolver = resolve_hostname,
            timeout_seconds: float = 30.0,
            max_content_bytes: int = MAX_CONTENT_BYTES,
            max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._transport = transport
        self._resolver = resolver
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_content_bytes = max_content_bytes
        self._max_redirects = max_redirects

    async def read(self, url: str) -> WebReadResult:
        requested_url = self._normalize_url(url)
        current_url = requested_url

        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "mooc-manus-research-reader/1.0"},
        ) as client:
            for redirect_count in range(self._max_redirects + 1):
                await self._validate_url(current_url)
                async with client.stream("GET", current_url) as response:
                    self._validate_peer(response)

                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise WebReadError("redirect response is missing location")
                        if redirect_count >= self._max_redirects:
                            raise WebReadError("maximum redirects exceeded")
                        current_url = self._normalize_url(
                            urljoin(str(response.url), location)
                        )
                        continue

                    response.raise_for_status()
                    content_type = self._content_type(response)
                    raw_content = await self._read_limited(response)
                    title, text = self._extract_text(
                        raw_content,
                        content_type,
                        response.encoding or "utf-8",
                    )
                    return WebReadResult(
                        requested_url=requested_url,
                        final_url=str(response.url),
                        title=title,
                        content_type=content_type,
                        text=text[:MAX_TEXT_CHARS],
                        raw_content=raw_content,
                        retrieved_at=datetime.now(timezone.utc),
                        response_headers=dict(response.headers),
                    )

        raise WebReadError("web read did not produce a response")

    @staticmethod
    def _normalize_url(url: str) -> str:
        candidate = url.strip()
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeURL(f"unsupported URL scheme: {parsed.scheme}")
        if not parsed.hostname:
            raise UnsafeURL("URL hostname is required")
        if parsed.username or parsed.password:
            raise UnsafeURL("URL credentials are not allowed")
        return urlunsplit(parsed)

    async def _validate_url(self, url: str) -> None:
        hostname = urlsplit(url).hostname
        if hostname is None:
            raise UnsafeURL("URL hostname is required")
        try:
            ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            addresses = await self._resolver(hostname)
            if not addresses:
                raise UnsafeURL(f"hostname has no addresses: {hostname}")
        else:
            addresses = [hostname]
        for address in addresses:
            _validate_address(address, "DNS")

    @staticmethod
    def _validate_peer(response: httpx.Response) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            raise UnsafeURL("peer address unavailable")
        server_address = stream.get_extra_info("server_addr")
        if isinstance(server_address, tuple):
            address = server_address[0]
        else:
            address = server_address
        if not isinstance(address, str) or not address:
            raise UnsafeURL("peer address unavailable")
        _validate_address(address, "peer")

    @staticmethod
    def _content_type(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedContent(f"unsupported content type: {content_type or 'missing'}")
        return content_type

    async def _read_limited(self, response: httpx.Response) -> bytes:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > self._max_content_bytes:
                raise WebContentTooLarge(
                    f"web content exceeds {self._max_content_bytes} bytes"
                )
        return bytes(content)

    @staticmethod
    def _extract_text(
            raw_content: bytes,
            content_type: str,
            encoding: str,
    ) -> tuple[str, str]:
        decoded = raw_content.decode(encoding, errors="replace")
        if content_type == "text/plain":
            return "", decoded.strip()

        soup = BeautifulSoup(decoded, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for element in soup.select("script, style, nav, noscript, iframe"):
            element.decompose()
        root = soup.body or soup
        text = markdownify(str(root), heading_style="ATX").strip()
        return title, text

