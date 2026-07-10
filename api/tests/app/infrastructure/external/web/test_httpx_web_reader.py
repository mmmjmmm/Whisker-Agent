import httpx
import pytest

from app.infrastructure.external.web.httpx_web_reader import (
    HttpxWebReader,
    UnsupportedContent,
    UnsafeURL,
    WebContentTooLarge,
)
from app.domain.services.tools.web_read import WebReadTool


class FakeNetworkStream:
    def __init__(self, address: str = "93.184.216.34") -> None:
        self.address = address

    def get_extra_info(self, info: str):
        if info == "server_addr":
            return self.address, 443
        return None


async def public_resolver(_hostname: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "file:///etc/passwd",
    ],
)
async def test_web_reader_rejects_unsafe_targets(url: str) -> None:
    reader = HttpxWebReader(resolver=public_resolver)

    with pytest.raises(UnsafeURL):
        await reader.read(url)


async def test_web_reader_rejects_hostname_resolving_to_private_address() -> None:
    async def private_resolver(_hostname: str) -> list[str]:
        return ["10.0.0.8"]

    reader = HttpxWebReader(resolver=private_resolver)

    with pytest.raises(UnsafeURL, match="private"):
        await reader.read("https://safe.example")


async def test_redirect_target_is_revalidated() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
            extensions={"network_stream": FakeNetworkStream()},
        )

    reader = HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    with pytest.raises(UnsafeURL):
        await reader.read("https://safe.example")


async def test_connection_peer_address_is_revalidated() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"safe text",
            request=request,
            extensions={"network_stream": FakeNetworkStream("10.0.0.9")},
        )

    reader = HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    with pytest.raises(UnsafeURL, match="peer"):
        await reader.read("https://safe.example")


async def test_missing_peer_address_fails_closed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"safe text",
            request=request,
        )

    reader = HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    with pytest.raises(UnsafeURL, match="peer address unavailable"):
        await reader.read("https://safe.example")


async def test_html_is_sanitized_and_converted_to_markdown() -> None:
    html = b"""
    <html><head><title>Example</title><style>.x{}</style></head>
    <body><nav>ignore me</nav><h1>Heading</h1><p>Useful text</p><script>bad()</script></body></html>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html,
            request=request,
            extensions={"network_stream": FakeNetworkStream()},
        )

    result = await HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    ).read("https://safe.example/article")

    assert result.title == "Example"
    assert "# Heading" in result.text
    assert "Useful text" in result.text
    assert "ignore me" not in result.text
    assert "bad()" not in result.text


async def test_reader_rejects_unsupported_content_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"pdf",
            request=request,
            extensions={"network_stream": FakeNetworkStream()},
        )

    reader = HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    with pytest.raises(UnsupportedContent):
        await reader.read("https://safe.example/file.pdf")


async def test_reader_stops_after_content_limit() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * (2 * 1024 * 1024 + 1),
            request=request,
            extensions={"network_stream": FakeNetworkStream()},
        )

    reader = HttpxWebReader(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    with pytest.raises(WebContentTooLarge):
        await reader.read("https://safe.example/large")


def test_web_read_tool_exposes_only_url_parameter() -> None:
    tool = WebReadTool(reader=object())
    schema = tool.get_tools()[0]["function"]

    assert schema["name"] == "web_read"
    assert schema["parameters"]["required"] == ["url"]
    assert set(schema["parameters"]["properties"]) == {"url"}
