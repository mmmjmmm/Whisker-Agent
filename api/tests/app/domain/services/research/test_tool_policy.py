from app.domain.models.agent_run import CapabilityProfile
from app.domain.models.tool_result import ToolResult
from app.domain.services.research.tool_policy import PolicyViolation, ToolPolicy
from app.domain.services.tools.base import BaseTool, tool


class ResearchTools(BaseTool):
    name = "research"

    @tool(
        name="search_web",
        description="search",
        parameters={"query": {"type": "string"}},
        required=["query"],
    )
    async def search_web(self, query: str) -> ToolResult:
        return ToolResult(data={"query": query})

    @tool(
        name="web_read",
        description="read",
        parameters={"url": {"type": "string"}},
        required=["url"],
    )
    async def web_read(self, url: str) -> ToolResult:
        return ToolResult(data={"url": url})


class ShellTools(BaseTool):
    name = "shell"

    @tool(
        name="shell_exec",
        description="execute",
        parameters={"command": {"type": "string"}},
        required=["command"],
    )
    async def shell_exec(self, command: str) -> ToolResult:
        return ToolResult(data={"command": command})


def test_schema_only_contains_profile_tools() -> None:
    policy = ToolPolicy([ResearchTools(), ShellTools()])

    names = {
        schema["function"]["name"]
        for schema in policy.schemas(CapabilityProfile.RESEARCH_READONLY)
    }

    assert names == {"search_web", "web_read"}
    assert policy.schemas(CapabilityProfile.ANALYSIS) == []


def test_resolve_rejects_hidden_tool() -> None:
    policy = ToolPolicy([ResearchTools(), ShellTools()])

    try:
        policy.resolve(CapabilityProfile.RESEARCH_READONLY, "shell_exec")
    except PolicyViolation as exc:
        assert "shell_exec" in str(exc)
        assert exc.retryable is False
    else:
        raise AssertionError("hidden tool was authorized")

