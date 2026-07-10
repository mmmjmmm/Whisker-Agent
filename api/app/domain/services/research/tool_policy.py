from typing import Any

from app.domain.models.agent_run import CapabilityProfile
from app.domain.services.research.errors import (
    ResearchErrorCode,
    ResearchExecutionError,
)
from app.domain.services.tools.base import BaseTool


ALLOWED_FUNCTIONS = {
    CapabilityProfile.RESEARCH_READONLY: frozenset({"search_web", "web_read"}),
    CapabilityProfile.ANALYSIS: frozenset(),
}


class PolicyViolation(ResearchExecutionError):
    def __init__(self, function_name: str) -> None:
        self.function_name = function_name
        super().__init__(
            code=ResearchErrorCode.POLICY_VIOLATION,
            message=f"tool is not authorized: {function_name}",
            retryable=False,
            scope="task",
        )


class ToolPolicy:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = tools

    def schemas(self, profile: CapabilityProfile) -> list[dict[str, Any]]:
        allowed = ALLOWED_FUNCTIONS[profile]
        return [
            schema
            for tool in self._tools
            for schema in tool.get_tools()
            if schema["function"]["name"] in allowed
        ]

    def resolve(
            self,
            profile: CapabilityProfile,
            function_name: str,
    ) -> BaseTool:
        if function_name not in ALLOWED_FUNCTIONS[profile]:
            raise PolicyViolation(function_name)
        for tool in self._tools:
            if tool.has_tool(function_name):
                return tool
        raise PolicyViolation(function_name)

