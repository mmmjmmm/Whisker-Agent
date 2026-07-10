from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class WebReadResult(BaseModel):
    requested_url: str
    final_url: str
    title: str
    content_type: str
    text: str
    raw_content: bytes
    retrieved_at: datetime
    response_headers: dict[str, str]


class WebReader(Protocol):
    async def read(self, url: str) -> WebReadResult: ...

