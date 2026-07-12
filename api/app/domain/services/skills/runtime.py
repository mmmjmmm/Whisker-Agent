import asyncio
import html
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Sequence

from app.domain.external.sandbox import Sandbox
from app.domain.models.skill import SkillSnapshot
from app.domain.models.tool_result import ToolResult


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    skill_md: str
    skill_dir: str


class SkillNotFoundError(LookupError):
    pass


class SkillLoadError(RuntimeError):
    pass


class SkillRuntime:
    """当前任务内的 Skill 目录和沙箱同步状态。"""

    def __init__(
        self,
        snapshots: Sequence[SkillSnapshot],
        sandbox: Sandbox,
    ) -> None:
        self._snapshots = {snapshot.name: snapshot for snapshot in snapshots}
        self._sandbox = sandbox
        self._locks = {
            snapshot.id: asyncio.Lock() for snapshot in snapshots
        }
        self._synced_dirs: dict[str, str] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._snapshots)

    @property
    def has_skills(self) -> bool:
        return bool(self._snapshots)

    @property
    def catalog_prompt(self) -> str:
        if not self._snapshots:
            return ""
        entries = "\n".join(
            "  <skill>\n"
            f"    <name>{html.escape(snapshot.name)}</name>\n"
            "    <description>"
            f"{html.escape(snapshot.description)}"
            "</description>\n"
            "  </skill>"
            for snapshot in self._snapshots.values()
        )
        return (
            "<available_skills>\n"
            f"{entries}\n"
            "</available_skills>\n\n"
            "When a task matches a skill description, call load_skill "
            "before proceeding.\n"
            "When the user explicitly mentions $<skill-name>, call that "
            "skill.\n"
            "Resolve relative paths against the returned skill directory."
        )

    async def load(self, name: str) -> LoadedSkill:
        snapshot = self._snapshots.get(name)
        if snapshot is None:
            raise SkillNotFoundError(f"Skill 不存在或未启用: {name}")

        skill_dir = await self._ensure_synced(snapshot)
        return LoadedSkill(
            name=snapshot.name,
            skill_md=snapshot.skill_md,
            skill_dir=skill_dir,
        )

    async def _ensure_synced(self, snapshot: SkillSnapshot) -> str:
        synced = self._synced_dirs.get(snapshot.id)
        if synced:
            return synced

        async with self._locks[snapshot.id]:
            synced = self._synced_dirs.get(snapshot.id)
            if synced:
                return synced
            if snapshot.bundle_load_error:
                raise SkillLoadError(snapshot.bundle_load_error)
            if snapshot.bundle_bytes is None:
                raise SkillLoadError(f"Skill ZIP 不可用: {snapshot.name}")

            base_dir = f"/home/ubuntu/.whisker-manus/skills/{snapshot.id}"
            bundle_path = f"{base_dir}/bundle.zip"
            content_dir = f"{base_dir}/content"
            skill_dir = (
                f"{content_dir}/{snapshot.root_path}"
                if snapshot.root_path
                else content_dir
            )
            session_id = f"skill-{snapshot.id}"
            try:
                upload_result = await self._sandbox.upload_file(
                    file_data=BytesIO(snapshot.bundle_bytes),
                    filepath=bundle_path,
                    filename="bundle.zip",
                )
                self._require_success(upload_result, "上传")

                extract_result = await self._sandbox.exec_command(
                    session_id=session_id,
                    exec_dir=base_dir,
                    command=(
                        f"rm -rf {content_dir} && "
                        f"python3 -m zipfile -e {bundle_path} {content_dir}"
                    ),
                )
                self._require_success(extract_result, "解压")
                if self._result_value(extract_result, "status") == "running":
                    extract_result = await self._sandbox.wait_process(
                        session_id,
                        seconds=60,
                    )
                    self._require_success(extract_result, "解压")
                if self._result_value(extract_result, "returncode") != 0:
                    raise SkillLoadError(f"Skill ZIP 解压失败: {snapshot.name}")
            except SkillLoadError:
                raise
            except Exception as exc:
                raise SkillLoadError(
                    f"Skill 同步失败[{snapshot.name}]: {exc}"
                ) from exc

            self._synced_dirs[snapshot.id] = skill_dir
            return skill_dir

    @staticmethod
    def _result_value(result: ToolResult, key: str) -> Any:
        data = result.data
        if isinstance(data, dict):
            return data.get(key)
        return getattr(data, key, None)

    @staticmethod
    def _require_success(result: ToolResult, action: str) -> None:
        if not result.success:
            raise SkillLoadError(
                f"Skill ZIP {action}失败: {result.message or 'unknown error'}"
            )
