from collections import deque
from typing import Any

from app.domain.external.llm import LLMInvocationResult


class FakeLLM:
    def __init__(self, results: list[LLMInvocationResult]) -> None:
        self.results = deque(results)
        self.calls: list[dict[str, Any]] = []

    async def invoke_with_usage(
            self,
            messages,
            tools=None,
            response_format=None,
            tool_choice=None,
    ) -> LLMInvocationResult:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "response_format": response_format,
            "tool_choice": tool_choice,
        })
        return self.results.popleft()

    async def invoke(
            self,
            messages,
            tools=None,
            response_format=None,
            tool_choice=None,
    ):
        result = await self.invoke_with_usage(
            messages,
            tools,
            response_format,
            tool_choice,
        )
        return result.message

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def temperature(self) -> float:
        return 0

    @property
    def max_tokens(self) -> int:
        return 4096
