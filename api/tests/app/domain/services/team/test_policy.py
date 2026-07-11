from app.domain.models.team import TeamCapability
from app.domain.services.team.policy import ToolPolicy
from app.domain.services.tools.base import BaseTool, tool


class DemoTool(BaseTool):
    name = "demo"

    @tool(name="search_web", description="search", parameters={}, required=[])
    async def search_web(self):
        raise AssertionError("not invoked")

    @tool(name="shell_execute", description="shell", parameters={}, required=[])
    async def shell_execute(self):
        raise AssertionError("not invoked")

    @tool(name="read_file", description="read", parameters={}, required=[])
    async def read_file(self):
        raise AssertionError("not invoked")

    @tool(name="write_file", description="write", parameters={}, required=[])
    async def write_file(self):
        raise AssertionError("not invoked")


def test_policy_filters_tools_by_capability():
    policy = ToolPolicy([DemoTool()])

    assert policy.allowed_names(TeamCapability.SEARCH) == frozenset({"search_web"})
    assert policy.allowed_names(TeamCapability.ANALYSIS) == frozenset()
    assert policy.allowed_names(TeamCapability.FILE_READ) == frozenset({"read_file"})
    assert policy.allowed_names(TeamCapability.FILE_WRITE) == frozenset(
        {"read_file", "write_file"}
    )
    assert policy.is_parallel_safe(TeamCapability.FILE_READ)
    assert not policy.is_parallel_safe(TeamCapability.FILE_WRITE)
    assert not policy.is_parallel_safe(TeamCapability.SHELL)


def test_dynamic_toolboxes_use_only_their_own_schemas():
    class ShellTool(BaseTool):
        name = "shell"

        @tool(name="shell_execute", description="shell", parameters={}, required=[])
        async def shell_execute(self):
            raise AssertionError("not invoked")

    policy = ToolPolicy([DemoTool(), ShellTool()])

    assert policy.allowed_names(TeamCapability.SHELL) == frozenset({"shell_execute"})
    assert [
        schema["function"]["name"]
        for schema in policy.available_schemas(TeamCapability.SEARCH)
    ] == ["search_web"]
