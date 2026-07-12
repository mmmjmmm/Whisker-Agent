import html

from app.domain.models.tool_result import ToolResult
from app.domain.services.skills.runtime import (
    SkillNotFoundError,
    SkillRuntime,
)

from .base import BaseTool, tool


class SkillTool(BaseTool):
    """把完整 Skill 指令加载到当前 Agent。"""

    name = "skill"

    def __init__(self, runtime: SkillRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._loaded: dict[str, str] = {}

    @tool(
        name="load_skill",
        description=(
            "加载一个已启用 Skill 的完整指令，并把完整 Skill 包同步到"
            "当前任务沙箱。"
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "available_skills 目录中的 Skill 名称",
            }
        },
        required=["name"],
    )
    async def load_skill(self, name: str) -> ToolResult:
        if name in self._loaded:
            return ToolResult(
                success=True,
                message="Skill 已在当前 Agent 中加载",
                data={
                    "name": name,
                    "skill_dir": self._loaded[name],
                    "content": None,
                    "already_loaded": True,
                },
            )

        try:
            loaded = await self._runtime.load(name)
        except SkillNotFoundError as exc:
            return ToolResult(success=False, message=str(exc))

        content = (
            f'<skill_content name="{html.escape(loaded.name, quote=True)}">\n'
            f"{loaded.skill_md}\n\n"
            f"Skill directory: {loaded.skill_dir}\n"
            "Relative paths in this skill are relative to the skill "
            "directory.\n"
            "</skill_content>"
        )
        self._loaded[name] = loaded.skill_dir
        return ToolResult(
            success=True,
            data={
                "name": loaded.name,
                "skill_dir": loaded.skill_dir,
                "content": content,
                "already_loaded": False,
            },
        )
