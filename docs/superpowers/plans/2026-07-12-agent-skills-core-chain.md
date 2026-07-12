# WhiskerAgent Agent Skills Core Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 WhiskerAgent 中实现从设置页 Skill ZIP 管理，到 Agent 元数据发现、load_skill 渐进加载、完整包同步至会话沙箱，再到 references/assets/scripts 按需使用的完整最小核心链路。

**Architecture:** PostgreSQL 保存系统全局 Skill 元数据和完整 SKILL.md，OSS 保存当前 ZIP；SkillRegistry 负责管理与任务快照，SkillRuntime 负责单任务目录与沙箱同步，SkillTool 负责把完整指令注入调用 Agent 的 Memory。Planner/ReAct/Team 共用任务快照但各自持有 SkillTool，资源访问继续使用现有 File/Shell 工具，加载过程继续使用现有 ToolEvent。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy/Alembic、阿里云 OSS、OpenAI-compatible tool calling、React 19、Next.js 16、TypeScript、现有 Sandbox/File/Shell/SSE 基础设施。

---

## 实施边界

本计划只实现已批准规格中的核心链路：

```text
Skill ZIP
  -> SkillRegistry
  -> 已启用 Skill 元数据目录
  -> Planner / ReAct / Team Agent
  -> load_skill(name)
  -> 注入 SKILL.md
  -> 同步完整 Skill 到会话沙箱
  -> 按需读取 references/assets 或执行 scripts
```

必须同时包含设置页上传、列表、详情、启停、删除、同名覆盖，聊天输入框美元符号选择器，自动匹配，多 Skill 连续加载和现有 ToolEvent 展示。

不得增加版本号、版本历史、回滚、版本选择、安全审批、签名、脚本扫描、来源审核、市场、Git 安装、向量检索、专用资源读取工具或专用脚本执行工具。不得修改 .env、Docker 运行配置或 Agent Provider。

## 执行许可门

仓库 AGENTS.md 要求：任何 pytest、lint、build、Alembic 运行检查、启动服务、访问本地服务、容器操作都必须先获得用户明确许可。因此：

- 写代码、写测试和静态审查可按任务进行。
- 每个“运行测试”步骤在实际执行时都必须先确认已有用户许可；没有许可就保留测试并继续静态实现，不能伪称测试通过。
- 本计划不需要启动 API、UI、Sandbox、PostgreSQL、Redis、OSS 或 Docker。
- 最终端到端运行验证需要单独说明将启动或访问哪些组件，再取得用户许可。

## 文件结构总览

新增后端核心文件：

- api/app/domain/models/skill.py
- api/app/domain/repositories/skill_repository.py
- api/app/domain/external/skill_bundle_storage.py
- api/app/domain/services/skills/**init**.py
- api/app/domain/services/skills/parser.py
- api/app/domain/services/skills/registry.py
- api/app/domain/services/skills/runtime.py
- api/app/domain/services/tools/skill.py
- api/app/infrastructure/models/skill.py
- api/app/infrastructure/repositories/db_skill_repository.py
- api/app/infrastructure/external/skill_bundle_storage/**init**.py
- api/app/infrastructure/external/skill_bundle_storage/oss_skill_bundle_storage.py
- api/app/application/services/skill_service.py
- api/app/interfaces/schemas/skill.py
- api/app/interfaces/endpoints/skill_routes.py
- api/alembic/versions/9a4f6c2e1d70_create_skills_table.py

新增前端核心文件：

- ui/src/lib/api/skill.ts
- ui/src/components/skill-settings.tsx
- ui/src/components/tool-use/skill-tool.tsx

新增测试文件按任务列出。现有文件只修改明确接入点，不做顺手重构。

## Task 1: 建立 Skill 领域对象与 SKILL.md 解析

**Files:**

- Create: api/app/domain/models/skill.py
- Create: api/app/domain/services/skills/**init**.py
- Create: api/app/domain/services/skills/parser.py
- Test: api/tests/app/domain/services/skills/test_parser.py

- [ ] **Step 1: 先写解析失败测试与标准包测试**

测试使用内存 ZIP，不访问磁盘或服务：

```python
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.domain.services.skills.parser import SkillParseError, SkillParser


def build_zip(entries: list[tuple[str, str]]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return stream.getvalue()


def test_parse_nested_skill_bundle() -> None:
    bundle = build_zip([
        (
            "demo/SKILL.md",
            "---\nname: demo-skill\ndescription: 处理演示任务\n---\n# Demo\n按步骤执行。",
        ),
        ("demo/references/guide.md", "# Guide"),
        ("demo/scripts/run.py", "print('ok')"),
    ])

    parsed = SkillParser().parse(bundle)

    assert parsed.name == "demo-skill"
    assert parsed.description == "处理演示任务"
    assert parsed.root_path == "demo"
    assert parsed.skill_md.endswith("# Demo\n按步骤执行。")


def test_first_skill_md_wins() -> None:
    bundle = build_zip([
        ("first/SKILL.md", "---\nname: first\ndescription: first skill\n---\n"),
        ("second/SKILL.md", "---\nname: second\ndescription: second skill\n---\n"),
    ])
    assert SkillParser().parse(bundle).name == "first"


@pytest.mark.parametrize(
    "entries",
    [
        [("README.md", "no skill")],
        [("SKILL.md", "# no frontmatter")],
        [("SKILL.md", "---\ndescription: missing name\n---\n")],
        [("SKILL.md", "---\nname: demo\n---\n")],
    ],
)
def test_parse_rejects_missing_runtime_fields(entries: list[tuple[str, str]]) -> None:
    with pytest.raises(SkillParseError):
        SkillParser().parse(build_zip(entries))
```

- [ ] **Step 2: 在获得运行许可后确认测试先失败**

Command:

```bash
cd api
uv run pytest tests/app/domain/services/skills/test_parser.py -q
```

Expected: FAIL，提示 parser 模块或类型尚不存在。

- [ ] **Step 3: 实现最小领域模型**

api/app/domain/models/skill.py 必须包含以下三个对象；Snapshot 冻结且只存在内存中，不含版本字段：

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    skill_md: str
    root_path: str = ""
    bundle_key: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ParsedSkill(BaseModel):
    name: str
    description: str
    skill_md: str
    root_path: str


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    skill_md: str
    root_path: str
    bundle_bytes: bytes | None = None
    bundle_load_error: str | None = None
```

- [ ] **Step 4: 实现功能性解析，不增加独立安全或格式校验阶段**

api/app/domain/services/skills/parser.py 使用 zipfile 和项目已安装的 yaml.safe_load：

```python
import re
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml

from app.domain.models.skill import ParsedSkill


class SkillParseError(ValueError):
    pass


class SkillParser:
    _frontmatter = re.compile(
        r"\A---[ \t]*\r?\n(?P<yaml>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)",
        re.DOTALL,
    )

    def parse(self, bundle: bytes) -> ParsedSkill:
        try:
            with ZipFile(BytesIO(bundle)) as archive:
                skill_path = next(
                    (
                        name
                        for name in archive.namelist()
                        if not name.endswith("/")
                        and PurePosixPath(name).name == "SKILL.md"
                    ),
                    None,
                )
                if skill_path is None:
                    raise SkillParseError("ZIP 中未找到 SKILL.md")
                skill_md = archive.read(skill_path).decode("utf-8")
        except (BadZipFile, UnicodeDecodeError) as exc:
            raise SkillParseError("无法读取 Skill ZIP") from exc

        match = self._frontmatter.match(skill_md)
        if match is None:
            raise SkillParseError("SKILL.md 缺少 YAML frontmatter")
        metadata = yaml.safe_load(match.group("yaml")) or {}
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise SkillParseError("SKILL.md 缺少 name")
        if not isinstance(description, str) or not description.strip():
            raise SkillParseError("SKILL.md 缺少 description")

        parent = PurePosixPath(skill_path).parent.as_posix()
        return ParsedSkill(
            name=name.strip(),
            description=description.strip(),
            skill_md=skill_md,
            root_path="" if parent == "." else parent,
        )
```

这里不检查 ZIP 大小、文件数、路径、目录名一致性、脚本内容、签名或来源。解析失败仅覆盖系统无法运行 Skill 的必要字段。

- [ ] **Step 5: 在获得许可后运行测试并提交**

Expected: PASS，6 个参数化/普通场景全部通过。

```bash
git add api/app/domain/models/skill.py api/app/domain/services/skills api/tests/app/domain/services/skills/test_parser.py
git commit -m "feat: parse agent skill bundles"
```

## Task 2: 增加 Skill 持久化协议、数据库模型和 OSS Bundle Storage

**Files:**

- Create: api/app/domain/repositories/skill_repository.py
- Create: api/app/domain/external/skill_bundle_storage.py
- Create: api/app/infrastructure/models/skill.py
- Create: api/app/infrastructure/repositories/db_skill_repository.py
- Create: api/app/infrastructure/external/skill_bundle_storage/**init**.py
- Create: api/app/infrastructure/external/skill_bundle_storage/oss_skill_bundle_storage.py
- Create: api/alembic/versions/9a4f6c2e1d70_create_skills_table.py
- Modify: api/app/domain/repositories/uow.py
- Modify: api/app/infrastructure/repositories/db_uow.py
- Modify: api/app/infrastructure/models/**init**.py
- Test: api/tests/app/infrastructure/models/test_skill_model.py
- Test: api/tests/app/infrastructure/external/skill_bundle_storage/test_oss_skill_bundle_storage.py

- [ ] **Step 1: 写 ORM 映射和 OSS 行为测试**

测试必须断言领域对象往返不丢字段，以及上传路径只有当前对象语义：

```python
from io import BytesIO

from app.domain.models.skill import Skill
from app.infrastructure.external.skill_bundle_storage.oss_skill_bundle_storage import (
    OSSSkillBundleStorage,
)
from app.infrastructure.models.skill import SkillModel


def test_skill_model_round_trip() -> None:
    skill = Skill(
        id="skill-id",
        name="demo",
        description="demo description",
        skill_md="---\nname: demo\ndescription: demo description\n---\n",
        root_path="demo",
        bundle_key="skills/skill-id/upload.zip",
        enabled=False,
    )
    assert SkillModel.from_domain(skill).to_domain() == skill


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    def get_object(self, key: str) -> BytesIO:
        return BytesIO(self.objects[key])

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeOSS:
    def __init__(self) -> None:
        self.bucket = FakeBucket()


def test_oss_bundle_storage_round_trip() -> None:
    async def scenario() -> None:
        oss = FakeOSS()
        storage = OSSSkillBundleStorage(oss)
        key = await storage.upload_bundle("skill-id", b"zip-bytes")
        assert key.startswith("skills/skill-id/")
        assert key.endswith(".zip")
        assert await storage.download_bundle(key) == b"zip-bytes"
        await storage.delete_bundle(key)
        assert key not in oss.bucket.objects

    import asyncio
    asyncio.run(scenario())
```

- [ ] **Step 2: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/infrastructure/models/test_skill_model.py tests/app/infrastructure/external/skill_bundle_storage/test_oss_skill_bundle_storage.py -q
```

Expected: FAIL，缺少 SkillModel、协议和 OSS 实现。

- [ ] **Step 3: 定义 Repository 与 Bundle Storage 协议**

skill_repository.py 精确定义：

```python
from typing import Protocol

from app.domain.models.skill import Skill


class SkillRepository(Protocol):
    async def save(self, skill: Skill) -> None: ...
    async def get_all(self) -> list[Skill]: ...
    async def get_enabled(self) -> list[Skill]: ...
    async def get_by_id(self, skill_id: str) -> Skill | None: ...
    async def get_by_name(self, name: str) -> Skill | None: ...
    async def delete_by_id(self, skill_id: str) -> None: ...
```

skill_bundle_storage.py 精确定义：

```python
from typing import Protocol


class SkillBundleStorage(Protocol):
    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str: ...
    async def download_bundle(self, key: str) -> bytes: ...
    async def delete_bundle(self, key: str) -> None: ...
```

IUnitOfWork 增加 skill: SkillRepository；DBUnitOfWork.**aenter** 初始化 DBSkillRepository。

- [ ] **Step 4: 实现 ORM、Repository 和迁移**

SkillModel 映射字段：

```python
class SkillModel(Base):
    __tablename__ = "skills"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skills_id"),
        UniqueConstraint("name", name="uq_skills_name"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    skill_md: Mapped[str] = mapped_column(Text, nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bundle_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, onupdate=datetime.now, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
```

from*domain、to_domain、update_from_domain 完全映射 Skill 的七个业务字段和两个时间字段。DBSkillRepository 使用 select/order_by(SkillModel.name)、enabled.is*(True)、id/name 精确匹配、现有记录更新和 delete。

迁移固定：

```python
revision = "9a4f6c2e1d70"
down_revision = "0e0d242438bc"
```

upgrade 创建 skills 表和 uq_skills_name；downgrade 只 drop skills 表。不增加 version 列或其他表。

- [ ] **Step 5: 实现 OSS 当前 Bundle 存储**

```python
import uuid

from starlette.concurrency import run_in_threadpool

from app.infrastructure.storage.oss import OSS


class OSSSkillBundleStorage:
    def __init__(self, oss: OSS) -> None:
        self._oss = oss

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str:
        key = f"skills/{skill_id}/{uuid.uuid4()}.zip"
        await run_in_threadpool(self._oss.bucket.put_object, key, bundle)
        return key

    async def download_bundle(self, key: str) -> bytes:
        response = await run_in_threadpool(self._oss.bucket.get_object, key)
        return await run_in_threadpool(response.read)

    async def delete_bundle(self, key: str) -> None:
        await run_in_threadpool(self._oss.bucket.delete_object, key)
```

upload UUID 只是原子覆盖所需的内部对象名，不暴露为版本能力。

- [ ] **Step 6: 在获得许可后运行测试并提交**

Expected: PASS。随后只暂存本任务文件：

```bash
git add api/app/domain/repositories/skill_repository.py api/app/domain/external/skill_bundle_storage.py api/app/infrastructure/models/skill.py api/app/infrastructure/repositories/db_skill_repository.py api/app/infrastructure/external/skill_bundle_storage api/app/domain/repositories/uow.py api/app/infrastructure/repositories/db_uow.py api/app/infrastructure/models/__init__.py api/alembic/versions/9a4f6c2e1d70_create_skills_table.py api/tests/app/infrastructure/models/test_skill_model.py api/tests/app/infrastructure/external/skill_bundle_storage/test_oss_skill_bundle_storage.py
git commit -m "feat: persist agent skill bundles"
```

## Task 3: 实现 SkillRegistry、同名覆盖和任务快照

**Files:**

- Create: api/app/domain/services/skills/registry.py
- Test: api/tests/app/domain/services/skills/fakes.py
- Test: api/tests/app/domain/services/skills/test_registry.py

- [ ] **Step 1: 写 Registry 生命周期与不可变快照测试**

Fake Repository/UoW 必须实现协议所需方法，Fake Storage 保存 key 到 bytes，并记录 deleted_keys。测试覆盖：

```python
def test_upsert_defaults_enabled_and_preserves_state_on_overwrite() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)

        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        assert created.enabled is True

        await registry.set_enabled(created.id, False)
        replaced = await registry.upsert_bundle(skill_zip("demo", "second"))

        assert replaced.id == created.id
        assert replaced.enabled is False
        assert replaced.description == "second"
        assert created.bundle_key in storage.deleted_keys

    asyncio.run(scenario())


def test_snapshot_keeps_bundle_after_registry_changes() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))

        snapshot = await registry.create_enabled_snapshot()
        await registry.upsert_bundle(skill_zip("demo", "second"))
        await registry.delete_skill(created.id)

        assert snapshot[0].description == "first"
        assert snapshot[0].bundle_bytes is not None

    asyncio.run(scenario())


def test_snapshot_records_download_error() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        storage.fail_download_for.add(created.bundle_key)

        snapshot = await registry.create_enabled_snapshot()

        assert snapshot[0].bundle_bytes is None
        assert snapshot[0].bundle_load_error

    asyncio.run(scenario())
```

同一文件继续覆盖 list/get、404 返回 None、enable/disable、delete、数据库保存失败清理新对象且保留旧记录。

- [ ] **Step 2: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/domain/services/skills/test_registry.py -q
```

Expected: FAIL，SkillRegistry 尚不存在。

- [ ] **Step 3: 实现 Registry 公共接口**

构造函数只依赖 uow_factory、SkillBundleStorage、SkillParser：

```python
class SkillRegistry:
    def __init__(self, uow_factory, bundle_storage, parser) -> None:
        self._uow_factory = uow_factory
        self._bundle_storage = bundle_storage
        self._parser = parser

    async def list_skills(self) -> list[Skill]: ...
    async def get_skill(self, skill_id: str) -> Skill | None: ...
    async def upsert_bundle(self, bundle: bytes) -> Skill: ...
    async def set_enabled(self, skill_id: str, enabled: bool) -> Skill | None: ...
    async def delete_skill(self, skill_id: str) -> bool: ...
    async def create_enabled_snapshot(self) -> tuple[SkillSnapshot, ...]: ...
```

upsert_bundle 的顺序必须固定为：

1. parser.parse(bundle)。
2. 通过 name 精确查询当前记录。
3. 新建时生成 Skill.id 且 enabled=True；覆盖时复用 id、enabled、created_at。
4. 上传新 OSS 对象。
5. 在新的 UoW 中保存新记录并等待上下文提交。
6. 保存失败时尽力删除新对象并重新抛出。
7. 保存成功后尽力删除旧对象。

尽力删除只记录 warning，不覆盖主操作结果。delete_skill 先提交数据库删除，再尽力删除当前 bundle_key。

- [ ] **Step 4: 实现任务快照**

create_enabled_snapshot 先在一个 UoW 内取得 enabled 列表并退出事务，再逐项下载 ZIP：

```python
snapshots: list[SkillSnapshot] = []
for skill in skills:
    try:
        bundle = await self._bundle_storage.download_bundle(skill.bundle_key)
        load_error = None
    except Exception as exc:
        bundle = None
        load_error = str(exc)
    snapshots.append(
        SkillSnapshot(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            skill_md=skill.skill_md,
            root_path=skill.root_path,
            bundle_bytes=bundle,
            bundle_load_error=load_error,
        )
    )
return tuple(snapshots)
```

返回 tuple，管理操作之后不再读取 Registry，从而保证运行中任务不变化。

- [ ] **Step 5: 在获得许可后运行测试并提交**

Expected: PASS，包含新增、覆盖、启停、删除、清理、快照和下载失败场景。

```bash
git add api/app/domain/services/skills/registry.py api/tests/app/domain/services/skills/fakes.py api/tests/app/domain/services/skills/test_registry.py
git commit -m "feat: add global skill registry"
```

## Task 4: 暴露设置模块 Skill 管理 API

**Files:**

- Create: api/app/application/services/skill_service.py
- Create: api/app/interfaces/schemas/skill.py
- Create: api/app/interfaces/endpoints/skill_routes.py
- Modify: api/app/interfaces/service_dependencies.py
- Modify: api/app/interfaces/endpoints/routes.py
- Test: api/tests/app/interfaces/endpoints/test_skill_routes.py

- [ ] **Step 1: 先写不启动项目生命周期的路由测试**

测试单独构造 FastAPI，只挂载 skill_routes.router，并覆盖 get_skill_service；禁止使用现有 session 级 client fixture，以免启动项目：

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.interfaces.endpoints import skill_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_skill_service


def make_client(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skill_routes.router, prefix="/api")
    app.dependency_overrides[get_skill_service] = lambda: service
    return TestClient(app)


def test_skill_management_routes() -> None:
    service = FakeSkillService()
    client = make_client(service)

    uploaded = client.post(
        "/api/app-config/skills",
        files={"file": ("demo.zip", b"bundle", "application/zip")},
    )
    skill_id = uploaded.json()["data"]["id"]

    assert client.get("/api/app-config/skills").json()["data"]["skills"][0]["name"] == "demo"
    assert client.get(f"/api/app-config/skills/{skill_id}").json()["data"]["skill_md"]
    assert client.post(
        f"/api/app-config/skills/{skill_id}/enabled",
        json={"enabled": False},
    ).status_code == 200
    assert client.post(
        f"/api/app-config/skills/{skill_id}/delete",
        json={},
    ).status_code == 200
```

另写不存在详情返回 404、解析错误返回 400、列表不泄漏 bundle_key 的断言。

- [ ] **Step 2: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/interfaces/endpoints/test_skill_routes.py -q
```

Expected: FAIL，路由、schema 和依赖尚不存在。

- [ ] **Step 3: 实现应用服务错误映射**

SkillService 只做接口层协调：

```python
class SkillService:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def upload(self, file: UploadFile) -> Skill:
        try:
            return await self._registry.upsert_bundle(await file.read())
        except SkillParseError as exc:
            raise BadRequestError(str(exc)) from exc

    async def list_skills(self) -> list[Skill]:
        return await self._registry.list_skills()

    async def get_skill(self, skill_id: str) -> Skill:
        skill = await self._registry.get_skill(skill_id)
        if skill is None:
            raise NotFoundError("Skill 不存在")
        return skill

    async def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        skill = await self._registry.set_enabled(skill_id, enabled)
        if skill is None:
            raise NotFoundError("Skill 不存在")
        return skill

    async def delete_skill(self, skill_id: str) -> None:
        if not await self._registry.delete_skill(skill_id):
            raise NotFoundError("Skill 不存在")
```

- [ ] **Step 4: 实现响应 Schema 与五个路由**

Schema：

```python
class SkillListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    enabled: bool


class SkillDetail(SkillListItem):
    skill_md: str


class ListSkillsResponse(BaseModel):
    skills: list[SkillListItem] = Field(default_factory=list)
```

路由固定为：

```text
GET  /api/app-config/skills
POST /api/app-config/skills
GET  /api/app-config/skills/{skill_id}
POST /api/app-config/skills/{skill_id}/enabled
POST /api/app-config/skills/{skill_id}/delete
```

POST 上传参数为 file: UploadFile = File(...)；enabled 使用 enabled: bool = Body(..., embed=True)。列表只返回 id/name/description/enabled，详情额外返回 skill_md。

- [ ] **Step 5: 组装依赖**

service_dependencies.py 增加：

```python
def get_skill_registry(oss: OSS = Depends(get_oss)) -> SkillRegistry:
    return SkillRegistry(
        uow_factory=get_uow,
        bundle_storage=OSSSkillBundleStorage(oss),
        parser=SkillParser(),
    )


def get_skill_service(
    registry: SkillRegistry = Depends(get_skill_registry),
) -> SkillService:
    return SkillService(registry)
```

routes.py 只新增 skill_routes.router。不得修改 app_config 配置文件或运行配置。

- [ ] **Step 6: 在获得许可后运行测试并提交**

Expected: PASS，五个接口和错误码测试通过。

```bash
git add api/app/application/services/skill_service.py api/app/interfaces/schemas/skill.py api/app/interfaces/endpoints/skill_routes.py api/app/interfaces/service_dependencies.py api/app/interfaces/endpoints/routes.py api/tests/app/interfaces/endpoints/test_skill_routes.py
git commit -m "feat: expose skill management api"
```

## Task 5: 实现任务级 SkillRuntime 与完整 ZIP 沙箱同步

**Files:**

- Create: api/app/domain/services/skills/runtime.py
- Test: api/tests/app/domain/services/skills/test_runtime.py

- [ ] **Step 1: 先写目录、首次同步、并发去重和错误测试**

FakeSandbox 只实现 runtime 会调用的 upload_file、exec_command、wait_process，并记录调用次数：

```python
class FakeSandbox:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.commands: list[str] = []

    async def upload_file(self, file_data, filepath, filename=None):
        self.uploads.append((filepath, file_data.read()))
        return ToolResult(success=True)

    async def exec_command(self, session_id, exec_dir, command):
        self.commands.append(command)
        return ToolResult(
            success=True,
            data={"session_id": session_id, "status": "completed", "returncode": 0},
        )

    async def wait_process(self, session_id, seconds=None):
        return ToolResult(success=True, data={"returncode": 0})
```

核心断言：

```python
def test_runtime_syncs_full_bundle_once_for_concurrent_loads() -> None:
    async def scenario() -> None:
        sandbox = FakeSandbox()
        snapshot = make_snapshot(
            skill_id="skill-id",
            name="demo",
            root_path="demo",
            bundle_bytes=b"zip-bytes",
        )
        runtime = SkillRuntime((snapshot,), sandbox)

        first, second = await asyncio.gather(
            runtime.load("demo"),
            runtime.load("demo"),
        )

        assert first.skill_dir == second.skill_dir
        assert first.skill_dir == "/home/ubuntu/.whisker-manus/skills/skill-id/content/demo"
        assert len(sandbox.uploads) == 1
        assert len(sandbox.commands) == 1
        assert "python3 -m zipfile -e" in sandbox.commands[0]

    asyncio.run(scenario())


def test_runtime_reports_snapshot_download_error() -> None:
    async def scenario() -> None:
        runtime = SkillRuntime(
            (make_snapshot(bundle_bytes=None, bundle_load_error="OSS unavailable"),),
            FakeSandbox(),
        )
        with pytest.raises(SkillLoadError, match="OSS unavailable"):
            await runtime.load("demo")

    asyncio.run(scenario())
```

另写未知名称抛 SkillNotFoundError、解压 returncode 非零抛 SkillLoadError、空 snapshot 不生成目录提示词。

- [ ] **Step 2: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/domain/services/skills/test_runtime.py -q
```

Expected: FAIL，SkillRuntime 尚不存在。

- [ ] **Step 3: 实现元数据目录**

runtime.py 定义：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    skill_md: str
    skill_dir: str


class SkillNotFoundError(LookupError):
    pass


class SkillLoadError(RuntimeError):
    pass
```

SkillRuntime.**init** 将 snapshots 按 name 建立只读查找，并为每个 id 建 asyncio.Lock、\_synced_dirs 字典；同时提供以下只读属性供 Flow 判断，不让 Flow 读取内部字典：

```python
@property
def names(self) -> tuple[str, ...]:
    return tuple(self._snapshots)

@property
def has_skills(self) -> bool:
    return bool(self._snapshots)
```

catalog_prompt 属性在无快照时返回空字符串；有快照时只包含经过 html.escape 的 name 和 description：

```text
<available_skills>
  <skill>
    <name>demo</name>
    <description>处理演示任务</description>
  </skill>
</available_skills>

When a task matches a skill description, call load_skill before proceeding.
When the user explicitly mentions $<skill-name>, call that skill.
Resolve relative paths against the returned skill directory.
```

catalog_prompt 不得包含 skill_md、bundle_key、ZIP 字节或脚本内容。

- [ ] **Step 4: 实现一次物理同步**

固定路径：

```python
base_dir = f"/home/ubuntu/.whisker-manus/skills/{snapshot.id}"
bundle_path = f"{base_dir}/bundle.zip"
content_dir = f"{base_dir}/content"
skill_dir = f"{content_dir}/{snapshot.root_path}" if snapshot.root_path else content_dir
```

锁内再次检查 \_synced_dirs。若 bundle_load_error 或 bundle_bytes 为空，抛 SkillLoadError。调用：

```python
upload_result = await self._sandbox.upload_file(
    file_data=BytesIO(snapshot.bundle_bytes),
    filepath=bundle_path,
    filename="bundle.zip",
)
extract_result = await self._sandbox.exec_command(
    session_id=f"skill-{snapshot.id}",
    exec_dir=base_dir,
    command=f"python3 -m zipfile -e {bundle_path} {content_dir}",
)
```

若 exec_command 返回 status=running，再调用 wait_process(session_id, seconds=60)。任何 ToolResult.success=False 或非零 returncode 都转成 SkillLoadError；成功后才写 \_synced_dirs。

这里同步完整 ZIP；不枚举、审查或过滤 references/assets/scripts。

- [ ] **Step 5: 在获得许可后运行测试并提交**

Expected: PASS，串行和并发加载都只上传/解压一次。

```bash
git add api/app/domain/services/skills/runtime.py api/tests/app/domain/services/skills/test_runtime.py
git commit -m "feat: sync skills into task sandbox"
```

## Task 6: 实现 load_skill 工具与渐进式上下文注入

**Files:**

- Create: api/app/domain/services/tools/skill.py
- Modify: api/app/domain/services/agents/base.py
- Test: api/tests/app/domain/services/tools/test_skill_tool.py
- Test: api/tests/app/domain/services/agents/test_skill_context.py

- [ ] **Step 1: 先写 SkillTool 独立去重测试**

```python
def test_each_tool_injects_full_skill_once() -> None:
    async def scenario() -> None:
        runtime = FakeSkillRuntime(
            LoadedSkill(
                name="demo",
                skill_md="FULL SKILL BODY",
                skill_dir="/home/ubuntu/.whisker-manus/skills/id/content/demo",
            )
        )
        first_agent_tool = SkillTool(runtime)
        second_agent_tool = SkillTool(runtime)

        first = await first_agent_tool.invoke("load_skill", name="demo")
        repeated = await first_agent_tool.invoke("load_skill", name="demo")
        second = await second_agent_tool.invoke("load_skill", name="demo")

        assert "FULL SKILL BODY" in first.data["content"]
        assert repeated.data["already_loaded"] is True
        assert repeated.data["content"] is None
        assert "FULL SKILL BODY" in second.data["content"]

    asyncio.run(scenario())
```

再写同一个 SkillTool 连续加载两个不同 Skill 的测试，断言两个完整正文都分别返回且 Runtime 各调用一次。未知 Skill 由 SkillTool 返回 success=False；Runtime 同步异常由 BaseAgent 的现有 \_invoke_tool 重试，耗尽后转换为 success=False 的 ToolResult，不让 Flow 崩溃。

- [ ] **Step 2: 先写 BaseAgent 渐进披露测试**

FakeLLM 记录每次 messages/tools/tool_choice，第一次返回 load_skill tool_call，第二次返回最终文本。断言：

- 第一次 LLM 请求的 system prompt 含 demo 和 description，但不含 FULL SKILL BODY。
- tools 中含 load_skill。
- 第二次 LLM 请求的 tool message 含 FULL SKILL BODY 和 skill_dir。
- Memory.compact 后该 tool message 仍存在。
- 对原本 \_tool_choice 为 none 的 ProbePlanner，有 SkillTool 时传给 LLM 的 tool_choice 是 None；无 SkillTool 时仍是 none。

- [ ] **Step 3: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/domain/services/tools/test_skill_tool.py tests/app/domain/services/agents/test_skill_context.py -q
```

Expected: FAIL，SkillTool 和实例级提示词逻辑不存在。

- [ ] **Step 4: 实现唯一工具 load_skill(name)**

```python
class SkillTool(BaseTool):
    name = "skill"

    def __init__(self, runtime: SkillRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._loaded: dict[str, str] = {}

    @tool(
        name="load_skill",
        description="加载一个已启用 Skill 的完整指令，并把完整 Skill 包同步到当前任务沙箱。",
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
            "Relative paths in this skill are relative to the skill directory.\n"
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
```

SkillLoadError 不在 SkillTool 内吞掉，让 BaseAgent 现有重试逻辑生效；直接调用 SkillTool 的单元测试应断言该异常继续抛出，BaseAgent 集成测试再断言最终失败 ToolResult。不得增加 read_skill_resource 或 run_skill_script。

- [ ] **Step 5: 让 BaseAgent 使用实例级目录且仅对 Skill Planner 开放工具**

BaseAgent.**init** 新增 system_prompt_suffix: str = ""，不得修改类级常量：

```python
base_prompt = type(self)._system_prompt
self._system_prompt = (
    f"{base_prompt.rstrip()}\n\n{system_prompt_suffix}"
    if system_prompt_suffix
    else base_prompt
)
self._tool_choice = type(self)._tool_choice
if self._tool_choice == "none" and any(
    tool.has_tool("load_skill") for tool in self._tools
):
    self._tool_choice = None
```

这使 Planner/Team Planner/Synthesizer 只有在实际获得 SkillTool 时才能先调用 load_skill；无 Skill 时行为不变。

- [ ] **Step 6: 在获得许可后运行测试并提交**

Expected: PASS，确认三级披露边界和 Agent 级去重。

```bash
git add api/app/domain/services/tools/skill.py api/app/domain/services/agents/base.py api/tests/app/domain/services/tools/test_skill_tool.py api/tests/app/domain/services/agents/test_skill_context.py
git commit -m "feat: load skills into agent context"
```

## Task 7: 把任务快照接入 AgentService、Planner/ReAct 与 ToolEvent

**Files:**

- Modify: api/app/application/services/agent_service.py
- Modify: api/app/interfaces/service_dependencies.py
- Modify: api/app/domain/services/agent_task_runner.py
- Modify: api/app/domain/services/flows/planner_react.py
- Modify: api/app/domain/models/event.py
- Test: api/tests/app/application/services/test_agent_service_skills.py
- Test: api/tests/app/domain/services/flows/test_planner_react_skills.py
- Test: api/tests/app/domain/services/test_agent_task_runner_skills.py

- [ ] **Step 1: 写新任务快照边界测试**

FakeSkillRegistry.create_enabled_snapshot 记录调用次数并返回固定 tuple。使用 Fake Task、Sandbox、Session UoW 验证 AgentService.\_create_task：

```python
runner = FakeTask.created_runner
assert registry.snapshot_calls == 1
assert runner._skill_runtime.names == ("demo",)
```

再修改 Fake Registry 当前数据，断言已创建 runner 的 runtime 不变化；再次创建任务才取得新快照。

- [ ] **Step 2: 写 Planner/ReAct 工具装配测试**

直接构造 PlannerReActFlow：

```python
planner_names = {
    schema["function"]["name"]
    for schema in flow.planner._get_available_tools()
}
react_names = {
    schema["function"]["name"]
    for schema in flow.react._get_available_tools()
}
assert planner_names == {"load_skill"}
assert "load_skill" in react_names
assert "read_file" in react_names
assert flow.planner._tools[-1] is not flow.react._tools[-1]
```

再用序列 FakeLLM 调用 PlannerAgent.create_plan：第一次响应 load_skill，第二次响应合法 Plan JSON；断言依次产生 calling/called ToolEvent 和 PlanEvent，证明结构化 Planner 能在输出 JSON 前加载指令。无快照时断言 Planner 工具仍为空、tool_choice 仍为 none，ReAct 没有 load_skill。

- [ ] **Step 3: 写 Skill ToolEvent 摘要测试**

构造 called ToolEvent，其中 function_result.data 含 name、skill_dir、content；调用 AgentTaskRunner.\_handle_tool_event 后断言：

```python
assert event.tool_content.name == "demo"
assert event.tool_content.skill_dir.endswith("/content/demo")
assert "content" not in event.tool_content.model_dump()
```

- [ ] **Step 4: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/application/services/test_agent_service_skills.py tests/app/domain/services/flows/test_planner_react_skills.py tests/app/domain/services/test_agent_task_runner_skills.py -q
```

Expected: FAIL，构造参数和事件内容尚未接入。

- [ ] **Step 5: 在 AgentService 创建任务时固定快照**

AgentService.**init** 增加 skill_registry: SkillRegistry 并保存。\_create_task 在构造 AgentTaskRunner 前执行：

```python
skill_snapshots = await self._skill_registry.create_enabled_snapshot()
```

AgentTaskRunner 新增 skill_snapshots: tuple[SkillSnapshot, ...]，初始化：

```python
self._skill_runtime = SkillRuntime(skill_snapshots, sandbox)
```

get_agent_service 使用 get_skill_registry(oss) 构造同一类 Registry 并注入 AgentService。不得在每次 Agent 调用时重新查 Registry。

- [ ] **Step 6: 接入 PlannerReActFlow**

PlannerReActFlow.**init** 新增 skill_runtime。先组装现有业务工具，再按是否存在快照分别创建独立工具：

```python
catalog = skill_runtime.catalog_prompt
planner_tools = [SkillTool(skill_runtime)] if catalog else []
react_tools = [
    FileTool(sandbox=sandbox),
    ShellTool(sandbox=sandbox),
    BrowserTool(browser=browser),
    SearchTool(search_engine=search_engine),
    MessageTool(),
    mcp_tool,
    a2a_tool,
]
if catalog:
    react_tools.append(SkillTool(skill_runtime))
```

PlannerAgent 和 ReActAgent 都传 system_prompt_suffix=catalog。禁止共用同一个 SkillTool 实例。

- [ ] **Step 7: 增加 SkillToolContent 并沿用 ToolEvent**

event.py：

```python
class SkillToolContent(BaseModel):
    name: str
    skill_dir: str
```

把它加入 ToolContent union。AgentTaskRunner.\_handle_tool_event 在 CALLED 分支处理 event.tool_name == "skill"：

```python
data = event.function_result.data if event.function_result else None
if event.function_result and event.function_result.success and isinstance(data, dict):
    event.tool_content = SkillToolContent(
        name=str(data.get("name", "")),
        skill_dir=str(data.get("skill_dir", "")),
    )
```

不新增 SkillEvent；ToolSSEEvent 现有 content 字段会自动序列化摘要。

- [ ] **Step 8: 在获得许可后运行测试并提交**

Expected: PASS，React 主链可以发现、加载和显示 Skill。

```bash
git add api/app/application/services/agent_service.py api/app/interfaces/service_dependencies.py api/app/domain/services/agent_task_runner.py api/app/domain/services/flows/planner_react.py api/app/domain/models/event.py api/tests/app/application/services/test_agent_service_skills.py api/tests/app/domain/services/flows/test_planner_react_skills.py api/tests/app/domain/services/test_agent_task_runner_skills.py
git commit -m "feat: wire skills into react tasks"
```

## Task 8: 把同一核心链路接入 Team Planner、Worker 与 Synthesizer

**Files:**

- Modify: api/app/domain/services/agents/team_planner.py
- Modify: api/app/domain/services/agents/team_synthesizer.py
- Modify: api/app/domain/services/flows/team.py
- Modify: api/app/domain/services/team/policy.py
- Test: api/tests/app/domain/services/flows/test_team_skills.py
- Test: api/tests/app/domain/services/team/test_skill_policy.py

- [ ] **Step 1: 写 Team 工具边界测试**

直接通过 build_team_flow 返回对象的 \_planner、\_orchestrator.\_worker_factory 和 \_synthesizer_factory 断言：

- TeamPlanner 只有 load_skill。
- TeamSynthesizer 只有 load_skill。
- 每次 worker_factory 创建独立 SkillTool。
- analysis Worker 有 load_skill，但没有 File/Shell/Browser。
- shell Worker 有 load_skill 和 Shell，其他业务工具仍不可用。
- file_read Worker 有 load_skill 和只读 File 函数，不因 Skill 获得 Shell。
- 所有 Agent system prompt 包含相同元数据目录，不含完整 skill_md。

- [ ] **Step 2: 写 Team ToolEvent 转发测试**

Fake LLM 让 TeamPlanner 和 Synthesizer 先返回 load_skill tool_call，再返回合法 JSON；向 create_graph/synthesize 传 emit callback，断言 callback 收到 calling 与 called 两个现有 ToolEvent。TaskWorker 保留当前 emit 行为。

- [ ] **Step 3: 在获得许可后确认测试先失败**

```bash
cd api
uv run pytest tests/app/domain/services/flows/test_team_skills.py tests/app/domain/services/team/test_skill_policy.py -q
```

Expected: FAIL，Team 还没有 SkillTool 和 planner/synth 事件转发。

- [ ] **Step 4: 保持 ToolPolicy 业务权限并额外允许 load_skill**

不要把 SkillTool 塞入 TOOLBOX_NAMES。ToolPolicy 增加明确的组合方法：

```python
def tools_and_names_for(
    self,
    capability: TeamCapability,
    extra_tools: list[BaseTool],
) -> tuple[list[BaseTool], frozenset[str]]:
    business_tools = self.tools_for(capability)
    business_names = self.allowed_names(capability)
    extra_names = {
        schema["function"]["name"]
        for tool in extra_tools
        for schema in tool.get_tools()
    }
    return (
        [*business_tools, *extra_tools],
        frozenset(set(business_names).union(extra_names)),
    )
```

extra_tools 只传每个 Agent 新建的 SkillTool；它不改变 capability 或 PARALLEL_SAFE。

- [ ] **Step 5: 组装 Team 三类 Agent**

build_team_flow 新增 skill_runtime 参数。catalog 非空时：

```python
planner_tools = [SkillTool(skill_runtime)]
planner = TeamPlannerAgent(
    tools=planner_tools,
    system_prompt_suffix=catalog,
    memory=Memory(),
)
```

worker_factory 每次调用新建 SkillTool，再通过 tools_and_names_for 合并。synthesizer_factory 每次也新建 SkillTool。无 catalog 时全部保持当前 tools=[] 和当前 ToolPolicy。

- [ ] **Step 6: 转发 Planner 和 Synthesizer 的现有事件**

TeamPlannerAgent.create_graph 和 TeamSynthesizerAgent.synthesize 增加可选 emit: Callable[[BaseEvent], Awaitable[None]]。invoke 遇 ToolEvent 时 await emit(event)，遇 Error/Message 保持当前语义。

TeamFlow.invoke 为每次 Planner/Synthesizer 尝试创建 attempt_events 列表与异步 capture callback；await 调用返回后，按顺序 yield attempt_events，再继续 TaskGraph 或最终 Message。Worker 继续直接通过 QueuedEventEmitter 转发。

不得新增事件类型，也不得改变 DAG、重试、并发或 capability 语义。

- [ ] **Step 7: 在 AgentTaskRunner Team factory 传入共享 Runtime**

```python
self._team_flow_factory = lambda: build_team_flow(
    uow_factory=uow_factory,
    session_id=session_id,
    agent_config=agent_config,
    llm=llm,
    json_parser=json_parser,
    browser=browser,
    sandbox=sandbox,
    search_engine=search_engine,
    mcp_tool=self._mcp_tool,
    a2a_tool=self._a2a_tool,
    skill_runtime=self._skill_runtime,
)
```

- [ ] **Step 8: 在获得许可后运行测试并提交**

Expected: PASS，Team 三类 Agent 都能加载 Skill，但 Skill 不扩大业务工具权限。

```bash
git add api/app/domain/services/agents/team_planner.py api/app/domain/services/agents/team_synthesizer.py api/app/domain/services/flows/team.py api/app/domain/services/team/policy.py api/app/domain/services/agent_task_runner.py api/tests/app/domain/services/flows/test_team_skills.py api/tests/app/domain/services/team/test_skill_policy.py
git commit -m "feat: wire skills into team agents"
```

## Task 9: 增加前端 Skill API 与设置页管理

**Files:**

- Create: ui/src/lib/api/skill.ts
- Create: ui/src/components/skill-settings.tsx
- Modify: ui/src/lib/api/types.ts
- Modify: ui/src/lib/api/index.ts
- Modify: ui/src/components/manus-settings.tsx

- [ ] **Step 1: 增加前端领域类型与 API**

types.ts：

```typescript
export type SkillListItem = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
};

export type SkillDetail = SkillListItem & {
  skill_md: string;
};

export type SkillsData = {
  skills: SkillListItem[];
};
```

skill.ts：

```typescript
import { get, post } from "./fetch";
import type { SkillDetail, SkillListItem, SkillsData } from "./types";

export const skillApi = {
  list: (): Promise<SkillsData> => get<SkillsData>("/app-config/skills"),
  detail: (id: string): Promise<SkillDetail> =>
    get<SkillDetail>("/app-config/skills/" + id),
  upload: (file: File): Promise<SkillListItem> => {
    const form = new FormData();
    form.append("file", file);
    return post<SkillListItem>("/app-config/skills", form);
  },
  setEnabled: (id: string, enabled: boolean): Promise<SkillListItem> =>
    post<SkillListItem>("/app-config/skills/" + id + "/enabled", { enabled }),
  delete: (id: string): Promise<void> =>
    post<void>("/app-config/skills/" + id + "/delete", {}),
};
```

index.ts 导出三种类型和 skillApi。

- [ ] **Step 2: 实现 SkillSettings 的完整最小管理交互**

skill-settings.tsx 自己维护 skills、loading、uploading、selectedDetail 和 detailOpen。挂载时 list；上传成功后重新 list；启停使用乐观更新和失败回滚；删除使用现有 MCP/A2A 模式；点击名称请求 detail 后在 Dialog 内用 pre 展示完整 skill_md。

必须提供：

- 隐藏 file input，accept=".zip,application/zip"。
- “上传 Skill ZIP”按钮和上传 Loader。
- 名称、description、禁用 Badge、Switch、详情按钮、Trash 按钮。
- 空列表和加载态。
- 同名上传成功 Toast 使用“Skill 已上传或覆盖”措辞，不出现版本。

不得提供 ZIP 下载、版本历史、扫描状态、审批状态或来源字段。

- [ ] **Step 3: 把 Skill 设置项接入 ManusSettings**

```typescript
type SettingTab =
  | "common-setting"
  | "llm-setting"
  | "skill-setting"
  | "a2a-setting"
  | "mcp-setting";
```

SETTING_MENUS 在模型提供商之后加入 BookOpen 图标和“Skills”。右侧内容在 activeSetting === "skill-setting" 时渲染 SkillSettings。ManusSettings 不复制 Skill 状态和 CRUD handler。

- [ ] **Step 4: 静态自审后提交**

在未获运行许可时，只检查 import、类型名、JSX 闭合和 API 路径，不运行 npm。

```bash
git add ui/src/lib/api/skill.ts ui/src/lib/api/types.ts ui/src/lib/api/index.ts ui/src/components/skill-settings.tsx ui/src/components/manus-settings.tsx
git commit -m "feat: manage skills in settings"
```

## Task 10: 在聊天输入框实现美元符号 Skill 选择器

**Files:**

- Modify: ui/src/components/chat-input.tsx

- [ ] **Step 1: 实现当前 mention 查询定位**

在 chat-input.tsx 内加入纯函数：

```typescript
type SkillMention = { start: number; query: string };

function getSkillMention(value: string, cursor: number): SkillMention | null {
  const prefix = value.slice(0, cursor);
  const match = prefix.match(/(?:^|\s)\$([^\s$]*)$/);
  if (!match) return null;
  return {
    start: cursor - match[1].length - 1,
    query: match[1].toLowerCase(),
  };
}
```

它只控制 UI，不解析或改变发送协议。

- [ ] **Step 2: 加载并过滤已启用目录**

ChatInput 增加 skills、skillsLoaded、loadingSkills、mention、activeSkillIndex 状态。输入变化时若 getSkillMention 非空且尚未加载，调用 skillApi.list 一次；候选只保留 enabled=true，按 name 或 description 包含 query 过滤。

请求失败关闭菜单并 Toast；不得阻止普通消息输入或发送。

- [ ] **Step 3: 实现选择和键盘交互**

选择候选时用以下替换，保留消息其他文本，因此可连续选择多个 Skill：

```typescript
const insertSkill = (skill: SkillListItem) => {
  if (!mention) return;
  const cursor = textareaRef.current?.selectionStart ?? inputValue.length;
  const next =
    inputValue.slice(0, mention.start) +
    "$" +
    skill.name +
    " " +
    inputValue.slice(cursor);
  setInputValue(next);
  onInputValueChange?.(next);
  setMention(null);
  requestAnimationFrame(() => textareaRef.current?.focus());
};
```

菜单打开时 ArrowDown/ArrowUp 改 activeSkillIndex，Enter 选择，Escape 关闭；Ctrl/Cmd+Enter 只在菜单关闭时发送。鼠标点击候选同样插入普通文本。

候选浮层展示 name 和 description，不增加 checkbox、消息字段或后端显式 Skill 数组。

- [ ] **Step 4: 静态自审后提交**

确认首页与会话详情页仍使用同一个 ChatInput，无需改 Page、SessionDetailView 或 ChatParams。

```bash
git add ui/src/components/chat-input.tsx
git commit -m "feat: add explicit skill mentions"
```

## Task 11: 展示 load_skill 的现有 ToolEvent

**Files:**

- Create: ui/src/components/tool-use/skill-tool.tsx
- Modify: ui/src/components/tool-use/utils.ts
- Modify: ui/src/components/tool-use/index.tsx
- Modify: ui/src/components/tool-preview-panel.tsx
- Modify: ui/src/lib/api/types.ts

- [ ] **Step 1: 增加 Skill Tool 类型与时间线 Badge**

ToolKind 加入 skill。getToolKind 在 default 前判断 name === "skill" 或 function === "load_skill"。getFriendlyToolLabel：

```typescript
if (name === "skill" || fn === "load_skill") {
  const skillName = getArg(args, "name");
  return skillName
    ? "正在加载 Skill " + truncate(skillName, 60)
    : "正在加载 Skill";
}
```

skill-tool.tsx 使用 BookOpen 图标和现有 ToolBadge。index.tsx 导出组件并把 skill 映射到 SkillTool。

- [ ] **Step 2: 为 ToolEvent content 增加摘要类型**

```typescript
export type SkillToolContent = {
  name: string;
  skill_dir: string;
};
```

ToolEvent.content 保持可兼容其他工具的 unknown，不把完整 SKILL.md 暴露到前端。

- [ ] **Step 3: 增加 Skill 预览**

tool-preview-panel.tsx 的 getToolDescription/getToolIcon 映射加入 skill。SkillPreview 优先读取 content.name、content.skill_dir；calling 阶段 content 为空时用 getArg(tool.args, "name") 显示名称。它只显示：

- Skill 名称。
- calling/called 对应的加载状态。
- 沙箱目录。

主内容 switch 在 skill 时渲染 SkillPreview。不得展示 function_result、完整 skill_md、ZIP 或脚本内容；完整正文只在设置页详情展示。

- [ ] **Step 4: 静态自审后提交**

确认 ChatMessage、eventsToTimeline、ToolSSEEvent 不需要新增事件分支，因为仍是 type=tool。

```bash
git add ui/src/components/tool-use/skill-tool.tsx ui/src/components/tool-use/utils.ts ui/src/components/tool-use/index.tsx ui/src/components/tool-preview-panel.tsx ui/src/lib/api/types.ts
git commit -m "feat: render skill load tool events"
```

## Task 12: 端到端静态审查与经许可的验证

**Files:**

- Modify only if a failing Skill test exposes a defect in files already listed above.
- Do not add unrelated documentation, configuration, abstractions or cleanup.

- [ ] **Step 1: 静态检查范围与禁止项**

先执行不启动项目的只读/静态检查：

```bash
git status --short
git diff --check
rg -n "version|rollback|signature|scan|approval|read_skill_resource|run_skill_script" api/app ui/src
```

逐项确认命中只来自既有无关代码或文本；新 Skill 实现不能出现版本、安全治理或专用资源/脚本工具。

- [ ] **Step 2: 静态追踪完整数据流**

人工沿以下调用逐个核对构造参数和返回类型：

```text
skill_routes
  -> SkillService
  -> SkillRegistry
  -> SkillRepository + SkillBundleStorage

AgentService._create_task
  -> create_enabled_snapshot
  -> AgentTaskRunner
  -> SkillRuntime
  -> PlannerReActFlow / build_team_flow
  -> SkillTool.load_skill
  -> Sandbox upload + python zipfile extraction
  -> FileTool / ShellTool
  -> ToolEvent
  -> ToolSSEEvent
  -> frontend ToolUse / ToolPreviewPanel
```

检查无 Skill 时所有新增参数都有明确空路径，Planner 保持 tool_choice=none。

- [ ] **Step 3: 请求一次明确的非服务运行许可**

向用户明确申请运行以下命令；许可范围不包含启动服务、Docker 或访问本地 URL：

```bash
cd api
uv run pytest tests/app/domain/services/skills tests/app/domain/services/tools/test_skill_tool.py tests/app/domain/services/agents/test_skill_context.py tests/app/application/services/test_agent_service_skills.py tests/app/domain/services/flows/test_planner_react_skills.py tests/app/domain/services/flows/test_team_skills.py tests/app/domain/services/team/test_skill_policy.py tests/app/domain/services/test_agent_task_runner_skills.py tests/app/interfaces/endpoints/test_skill_routes.py tests/app/infrastructure/models/test_skill_model.py tests/app/infrastructure/external/skill_bundle_storage/test_oss_skill_bundle_storage.py -q

cd ui
npm run lint
npm run build
```

Expected: 新增后端测试全部 PASS；ESLint 无 error；Next build 成功。若用户只允许部分命令，只报告实际执行部分。

- [ ] **Step 4: 仅在用户另外授权后做运行态验收**

运行态验收会启动或访问 API/UI/Sandbox/PostgreSQL/Redis/OSS，必须再次说明影响并取得明确许可。获准后用一个含 SKILL.md、references、assets、scripts 的 ZIP 验证：

1. 设置页上传、详情、禁用、启用、同名覆盖、删除。
2. 新任务目录只含 name/description。
3. 自动匹配和美元符号显式匹配都产生 load_skill ToolEvent。
4. 首次 load_skill 同步完整包，后续同任务不重复同步。
5. ReAct 可读取 reference/asset 并通过现有 Shell 执行 script。
6. Team 各 Agent 可加载 Skill，Worker 工具仍受 capability 限制。
7. 运行中任务不受 Registry 覆盖或删除影响，新任务读取新状态。

没有运行态许可时，不执行此步骤，也不声称端到端已验证。

- [ ] **Step 5: 使用 verification-before-completion 做最终证据审查**

实施者必须调用 superpowers:verification-before-completion，基于实际获得许可后运行的输出给出结论。检查：

- git diff 只包含本计划文件。
- 不含 .env、Docker 或其他运行配置变更。
- 不含版本或安全治理能力。
- 没有遗漏 Settings ZIP -> Registry -> Catalog -> Agent -> load_skill -> SKILL.md -> Sandbox -> resources/scripts 的任一节点。

- [ ] **Step 6: 提交最终验证修正**

若验证产生了仅限 Skill 核心链路的修正，回到对应 Task 的精确 git add 清单，只暂存实际修正的已列明文件，然后执行：

```bash
git commit -m "fix: complete agent skills core chain"
```

若没有修正，不创建空提交。最终交付只报告已实现能力、实际验证证据和任何因未授权而未执行的运行验证。
