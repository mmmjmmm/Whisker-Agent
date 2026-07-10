from app.domain.external.web_reader import WebReadResult, WebReader
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool


class WebReadTool(BaseTool):
    name = "web"

    def __init__(self, reader: WebReader) -> None:
        super().__init__()
        self._reader = reader

    @tool(
        name="web_read",
        description="读取公开 HTTP/HTTPS 网页并返回经过清理的文本内容。",
        parameters={
            "url": {
                "type": "string",
                "description": "要读取的公开网页 URL。",
            },
        },
        required=["url"],
    )
    async def web_read(self, url: str) -> ToolResult[WebReadResult]:
        return ToolResult(data=await self._reader.read(url))
