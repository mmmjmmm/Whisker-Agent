from app.domain.models.team import TeamCapability
from app.domain.services.tools.base import BaseTool


STATIC_NAMES: dict[TeamCapability, frozenset[str]] = {
    TeamCapability.ANALYSIS: frozenset(),
    TeamCapability.SEARCH: frozenset({"search_web"}),
    TeamCapability.FILE_READ: frozenset(
        {"read_file", "search_in_file", "find_files"}
    ),
    TeamCapability.FILE_WRITE: frozenset(
        {
            "read_file",
            "search_in_file",
            "find_files",
            "write_file",
            "replace_in_file",
        }
    ),
}

TOOLBOX_NAMES: dict[TeamCapability, str] = {
    TeamCapability.BROWSER: "browser",
    TeamCapability.SHELL: "shell",
    TeamCapability.MCP: "mcp",
    TeamCapability.A2A: "a2a",
}

PARALLEL_SAFE = frozenset(
    {
        TeamCapability.ANALYSIS,
        TeamCapability.SEARCH,
        TeamCapability.FILE_READ,
    }
)


class ToolPolicy:
    def __init__(self, tools: list[BaseTool]):
        self._tools = tools

    def allowed_names(self, capability: TeamCapability) -> frozenset[str]:
        if capability in STATIC_NAMES:
            configured = STATIC_NAMES[capability]
            available = {
                schema["function"]["name"]
                for tool in self._tools
                for schema in tool.get_tools()
            }
            return frozenset(configured.intersection(available))

        toolbox_name = TOOLBOX_NAMES[capability]
        names: set[str] = set()
        for tool in self._tools:
            if tool.name == toolbox_name:
                names.update(
                    schema["function"]["name"] for schema in tool.get_tools()
                )
        return frozenset(names)

    def available_schemas(self, capability: TeamCapability) -> list[dict]:
        allowed = self.allowed_names(capability)
        return [
            schema
            for tool in self._tools
            for schema in tool.get_tools()
            if schema["function"]["name"] in allowed
        ]

    def is_parallel_safe(self, capability: TeamCapability) -> bool:
        return capability in PARALLEL_SAFE
