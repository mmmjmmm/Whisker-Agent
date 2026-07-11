# TeamFlow 动态 DAG 多 Agent 编排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变默认 `PlannerReActFlow` 行为的前提下，实现显式触发、真实并行、工具能力完整且可观测的本地多 Agent 动态 DAG 编排。

**Architecture:** `TeamPlannerAgent` 生成受约束的 `PlannedTaskGraph`，确定性图校验函数和 `TeamOrchestrator` 负责 DAG 校验、依赖调度、最多 3 Worker 并行、独占工具、超时和一次重试。通用 `TaskWorker` 按 capability 获得最小工具集，事件经单一队列顺序发布，最后由 `TeamSynthesizerAgent` 汇总。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy、Redis Streams、现有 OpenAI-compatible LLM/Tool/Flow 抽象、Next.js 16、React 19、TypeScript、pytest、Vitest、Testing Library。

---

## 执行约束

- 用户后续明确要求不创建 worktree，直接在 `feature/team-dag-orchestration` 分支实施，不在 `main` 上开发。
- 用户后续已统一授权 pytest、前端测试、lint、类型检查、构建、开发服务、容器和本地服务验证，无需逐项再次确认。
- 不修改 `.env`、`api/config.yaml` 或其他本地运行配置。
- 每个行为先写失败测试，再写最小实现；默认 `mode="react"` 和旧事件 fallback 保持兼容。

## 文件地图

| 文件                                                 | 责任                                                           |
| ---------------------------------------------------- | -------------------------------------------------------------- |
| `api/app/domain/models/team.py`                      | Team 枚举、Planner DTO、运行时 Graph/Task、Worker/Final Result |
| `api/app/domain/services/team/graph.py`              | DAG 校验、ready 节点、依赖失败传播、终态计算                   |
| `api/app/domain/services/team/policy.py`             | capability 到工具 Schema/运行时权限及并发类别映射              |
| `api/app/domain/services/team/orchestrator.py`       | 批次并发、独占执行、Attempt、超时、重试、取消                  |
| `api/app/domain/services/prompts/team.py`            | Planner、Worker、Synthesizer 提示词                            |
| `api/app/domain/services/agents/team_planner.py`     | 结构化生成 PlannedTaskGraph                                    |
| `api/app/domain/services/agents/task_worker.py`      | 执行单节点、事件归属、来源 URL 校验                            |
| `api/app/domain/services/agents/team_synthesizer.py` | 汇总结果、附件和来源，不调用有副作用工具                       |
| `api/app/domain/services/flows/team.py`              | Team 高层阶段、事件队列/确认、取消快照                         |
| `api/app/domain/services/flows/router.py`            | 根据 AgentMode 选择 React 或新 TeamFlow                        |
| `api/app/domain/services/agents/base.py`             | 可选临时 Memory 与工具白名单，旧行为默认不变                   |
| `api/app/domain/models/event.py`                     | mode、TaskGraph/Task Event、Tool 关联字段                      |
| `api/app/domain/models/session.py`                   | 最近 mode 与 Team Graph 事件投影                               |
| `api/app/domain/services/agent_task_runner.py`       | Flow 路由、活动 Flow、取消和单一事件写入                       |
| `api/app/application/services/agent_service.py`      | mode 传递、Team 运行冲突校验                                   |
| `api/app/application/services/session_service.py`    | 失联 Team Session 懒惰收敛                                     |
| `api/app/interfaces/schemas/session.py`              | ChatRequest.mode                                               |
| `api/app/interfaces/schemas/event.py`                | Team Domain Event 到 SSE 映射                                  |
| `api/app/interfaces/endpoints/session_routes.py`     | 请求预检和 mode 传递                                           |
| `api/app/interfaces/service_dependencies.py`         | SessionService Task 依赖注入                                   |
| `ui/src/lib/api/types.ts`                            | AgentMode、Graph、Task、WorkerResult、Team SSE 类型            |
| `ui/src/lib/api/session.ts`                          | ChatParams.mode                                                |
| `ui/src/lib/session-events.ts`                       | Team Event 投影与 task_id 工具归属                             |
| `ui/src/components/agent-mode-selector.tsx`          | 单 Agent/多 Agent 模式选择                                     |
| `ui/src/components/team-task-panel.tsx`              | 列表化 DAG、状态、工具、来源和产物展示                         |
| `ui/src/components/chat-input.tsx`                   | 嵌入模式选择，运行中禁用切换                                   |
| `ui/src/components/session-detail-view.tsx`          | mode 发送、Team 面板与工具预览整合                             |
| `ui/src/hooks/use-session-detail.ts`                 | sendMessage 传递 mode                                          |
| `ui/src/app/page.tsx`                                | 新会话初始 mode 编码                                           |
| `ui/src/app/sessions/[id]/page.tsx`                  | 初始 mode 解码                                                 |
| `ui/package.json`、`ui/vitest.config.ts`             | 前端测试脚本和环境                                             |

### Task 1: 建立 Team 领域模型与默认限制

**Files:**

- Create: `api/app/domain/models/team.py`
- Modify: `api/app/domain/models/app_config.py:29-34`
- Test: `api/tests/app/domain/models/test_team.py`

- [ ] **Step 1: 写领域模型失败测试**

```python
from pydantic import ValidationError

from app.domain.models.app_config import AgentConfig
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    SourceRef,
    TeamCapability,
    WorkerResult,
)


def test_team_config_has_conservative_defaults():
    config = AgentConfig()
    assert config.team_max_tasks == 5
    assert config.team_max_workers == 3
    assert config.team_max_task_retries == 1
    assert config.team_task_timeout_seconds == 300
    assert config.team_max_worker_iterations == 20


def test_successful_worker_result_requires_summary():
    try:
        WorkerResult(success=True, summary="")
    except ValidationError:
        return
    raise AssertionError("successful WorkerResult must reject an empty summary")


def test_source_ref_rejects_non_http_url():
    try:
        SourceRef(title="local", url="file:///etc/passwd")
    except ValidationError:
        return
    raise AssertionError("SourceRef must only accept HTTP(S)")


def test_planner_model_contains_no_runtime_fields():
    planned = PlannedTask.model_validate({
        "id": "collect",
        "description": "收集资料",
        "dependencies": [],
        "capability": "search",
        "success_criteria": "至少返回一个来源",
    })
    assert planned.capability is TeamCapability.SEARCH
    assert "status" not in planned.model_fields
    assert AgentMode.REACT.value == "react"
    assert PlannedTaskGraph(title="t", goal="g", tasks=[planned]).tasks == [planned]
```

- [ ] **Step 2: 取得运行许可后执行模型测试并确认失败**

Run: `cd api && pytest tests/app/domain/models/test_team.py -q`

Expected: FAIL，提示 `app.domain.models.team` 不存在或 `AgentConfig` 缺少 Team 字段。

- [ ] **Step 3: 创建完整领域模型**

```python
# api/app/domain/models/team.py
import uuid
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, model_validator


class AgentMode(str, Enum):
    REACT = "react"
    TEAM = "team"


class TeamCapability(str, Enum):
    ANALYSIS = "analysis"
    SEARCH = "search"
    BROWSER = "browser"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    MCP = "mcp"
    A2A = "a2a"


class TeamTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskGraphStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceRef(BaseModel):
    title: str
    url: HttpUrl
    snippet: str | None = None


class WorkerResult(BaseModel):
    success: bool
    summary: str
    sources: list[SourceRef] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_success_summary(self):
        if self.success and not self.summary.strip():
            raise ValueError("successful worker result requires summary")
        return self


class FinalTeamResponse(BaseModel):
    message: str
    attachments: list[str] = Field(default_factory=list)


class PlannedTask(BaseModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    capability: TeamCapability
    success_criteria: str = Field(min_length=1)


class PlannedTaskGraph(BaseModel):
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[PlannedTask]


class TeamTask(PlannedTask):
    status: TeamTaskStatus = TeamTaskStatus.PENDING
    assigned_agent_id: str | None = None
    attempt_count: int = 0
    result: WorkerResult | None = None
    error: str | None = None


class TaskGraph(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    goal: str
    tasks: list[TeamTask]
    status: TaskGraphStatus = TaskGraphStatus.PENDING
    error: str | None = None

    def task_by_id(self, task_id: str) -> TeamTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)
```

- [ ] **Step 4: 增加代码默认配置，不改配置文件**

```python
# append inside AgentConfig
team_max_tasks: int = Field(default=5, ge=1, le=20)
team_max_workers: int = Field(default=3, ge=1, le=8)
team_max_task_retries: int = Field(default=1, ge=0, le=3)
team_task_timeout_seconds: int = Field(default=300, ge=30, le=1800)
team_max_worker_iterations: int = Field(default=20, ge=1, le=100)
```

- [ ] **Step 5: 取得许可后重新运行模型测试**

Run: `cd api && pytest tests/app/domain/models/test_team.py -q`

Expected: PASS，4 tests passed。

- [ ] **Step 6: 提交领域模型**

```bash
git add api/app/domain/models/team.py api/app/domain/models/app_config.py api/tests/app/domain/models/test_team.py
git commit -m "feat: add team DAG domain models"
```

### Task 2: 实现 DAG 校验和状态计算

**Files:**

- Create: `api/app/domain/services/team/__init__.py`
- Create: `api/app/domain/services/team/graph.py`
- Test: `api/tests/app/domain/services/team/test_graph.py`

- [ ] **Step 1: 写图校验与 ready-task 失败测试**

```python
import pytest

from app.domain.models.team import PlannedTask, PlannedTaskGraph, TeamCapability, TeamTaskStatus
from app.domain.services.team.graph import TaskGraphError, build_task_graph, propagate_skipped, ready_tasks


def node(task_id: str, deps: list[str] | None = None) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        description=task_id,
        dependencies=deps or [],
        capability=TeamCapability.ANALYSIS,
        success_criteria="done",
    )


def test_rejects_cycle():
    plan = PlannedTaskGraph(title="t", goal="g", tasks=[node("a", ["b"]), node("b", ["a"])])
    with pytest.raises(TaskGraphError, match="cycle"):
        build_task_graph(plan, max_tasks=5)


def test_rejects_unknown_dependency():
    plan = PlannedTaskGraph(title="t", goal="g", tasks=[node("a", ["missing"])])
    with pytest.raises(TaskGraphError, match="unknown dependency"):
        build_task_graph(plan, max_tasks=5)


def test_ready_and_skip_propagation():
    graph = build_task_graph(
        PlannedTaskGraph(title="t", goal="g", tasks=[node("a"), node("b", ["a"])]),
        max_tasks=5,
    )
    assert [task.id for task in ready_tasks(graph)] == ["a"]
    graph.task_by_id("a").status = TeamTaskStatus.FAILED
    changed = propagate_skipped(graph)
    assert [task.id for task in changed] == ["b"]
    assert graph.task_by_id("b").status is TeamTaskStatus.SKIPPED
```

- [ ] **Step 2: 取得许可后运行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/team/test_graph.py -q`

Expected: FAIL，提示 `graph` 模块不存在。

- [ ] **Step 3: 实现确定性图操作**

```python
# api/app/domain/services/team/graph.py
from collections import deque

from app.domain.models.team import (
    PlannedTaskGraph,
    TaskGraph,
    TaskGraphStatus,
    TeamTask,
    TeamTaskStatus,
)


class TaskGraphError(ValueError):
    pass


def build_task_graph(plan: PlannedTaskGraph, max_tasks: int) -> TaskGraph:
    if not 1 <= len(plan.tasks) <= max_tasks:
        raise TaskGraphError(f"task count must be between 1 and {max_tasks}")
    ids = [task.id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        raise TaskGraphError("duplicate task id")
    known = set(ids)
    indegree = {task_id: 0 for task_id in ids}
    children = {task_id: [] for task_id in ids}
    for task in plan.tasks:
        if task.id in task.dependencies:
            raise TaskGraphError("self dependency")
        for dependency in task.dependencies:
            if dependency not in known:
                raise TaskGraphError(f"unknown dependency: {dependency}")
            indegree[task.id] += 1
            children[dependency].append(task.id)
    queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(ids):
        raise TaskGraphError("cycle detected")
    return TaskGraph(
        title=plan.title,
        goal=plan.goal,
        tasks=[TeamTask(**task.model_dump()) for task in plan.tasks],
    )


def ready_tasks(graph: TaskGraph) -> list[TeamTask]:
    completed = {task.id for task in graph.tasks if task.status is TeamTaskStatus.COMPLETED}
    return [
        task for task in graph.tasks
        if task.status is TeamTaskStatus.PENDING and set(task.dependencies) <= completed
    ]


def propagate_skipped(graph: TaskGraph) -> list[TeamTask]:
    blocked = {
        task.id for task in graph.tasks
        if task.status in {TeamTaskStatus.FAILED, TeamTaskStatus.SKIPPED, TeamTaskStatus.CANCELLED}
    }
    changed: list[TeamTask] = []
    progress = True
    while progress:
        progress = False
        for task in graph.tasks:
            if task.status is TeamTaskStatus.PENDING and blocked.intersection(task.dependencies):
                task.status = TeamTaskStatus.SKIPPED
                task.error = "dependency_failed"
                blocked.add(task.id)
                changed.append(task)
                progress = True
    return changed


def finalize_graph(graph: TaskGraph) -> TaskGraphStatus:
    completed = sum(task.status is TeamTaskStatus.COMPLETED for task in graph.tasks)
    failed = any(task.status in {TeamTaskStatus.FAILED, TeamTaskStatus.SKIPPED} for task in graph.tasks)
    cancelled = any(task.status is TeamTaskStatus.CANCELLED for task in graph.tasks)
    if cancelled:
        graph.status = TaskGraphStatus.CANCELLED
    elif completed == len(graph.tasks):
        graph.status = TaskGraphStatus.COMPLETED
    elif completed > 0 and failed:
        graph.status = TaskGraphStatus.PARTIAL
    else:
        graph.status = TaskGraphStatus.FAILED
    return graph.status
```

- [ ] **Step 4: 补充超过上限和重复 ID 测试**

```python
def test_rejects_too_many_tasks():
    plan = PlannedTaskGraph(title="t", goal="g", tasks=[node(str(i)) for i in range(6)])
    with pytest.raises(TaskGraphError, match="task count"):
        build_task_graph(plan, max_tasks=5)


def test_rejects_duplicate_ids():
    plan = PlannedTaskGraph(title="t", goal="g", tasks=[node("same"), node("same")])
    with pytest.raises(TaskGraphError, match="duplicate"):
        build_task_graph(plan, max_tasks=5)
```

- [ ] **Step 5: 取得许可后运行图测试**

Run: `cd api && pytest tests/app/domain/services/team/test_graph.py -q`

Expected: PASS，5 tests passed。

- [ ] **Step 6: 提交图模型**

```bash
git add api/app/domain/services/team api/tests/app/domain/services/team/test_graph.py
git commit -m "feat: validate team task graphs"
```

### Task 3: 增加 ToolPolicy、最小权限与临时 Memory

**Files:**

- Create: `api/app/domain/services/team/policy.py`
- Modify: `api/app/domain/services/agents/base.py:35-190`
- Test: `api/tests/app/domain/services/team/test_policy.py`
- Test: `api/tests/app/domain/services/agents/test_base_agent_policy.py`

- [ ] **Step 1: 写 ToolPolicy 失败测试**

```python
from app.domain.models.team import TeamCapability
from app.domain.services.team.policy import ToolPolicy
from app.domain.services.tools.base import BaseTool, tool


class DemoTool(BaseTool):
    name = "demo"

    @tool(name="search_web", description="search", parameters={}, required=[])
    async def search_web(self):
        raise AssertionError

    @tool(name="shell_execute", description="shell", parameters={}, required=[])
    async def shell_execute(self):
        raise AssertionError


def test_policy_filters_tools_by_capability():
    policy = ToolPolicy([DemoTool()])
    assert policy.allowed_names(TeamCapability.SEARCH) == frozenset({"search_web"})
    assert policy.allowed_names(TeamCapability.ANALYSIS) == frozenset()
    assert policy.is_parallel_safe(TeamCapability.FILE_READ)
    assert not policy.is_parallel_safe(TeamCapability.SHELL)
```

- [ ] **Step 2: 取得许可后执行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/team/test_policy.py -q`

Expected: FAIL，提示 `ToolPolicy` 不存在。

- [ ] **Step 3: 实现 capability 到工具的唯一映射**

```python
# api/app/domain/services/team/policy.py
from app.domain.models.team import TeamCapability
from app.domain.services.tools.base import BaseTool


STATIC_NAMES = {
    TeamCapability.ANALYSIS: frozenset(),
    TeamCapability.SEARCH: frozenset({"search_web"}),
    TeamCapability.FILE_READ: frozenset({"read_file", "search_in_file", "find_files"}),
    TeamCapability.FILE_WRITE: frozenset({"read_file", "write_file", "replace_in_file"}),
}
TOOLBOX_NAMES = {
    TeamCapability.BROWSER: "browser",
    TeamCapability.SHELL: "shell",
    TeamCapability.MCP: "mcp",
    TeamCapability.A2A: "a2a",
}
PARALLEL_SAFE = frozenset({TeamCapability.ANALYSIS, TeamCapability.SEARCH, TeamCapability.FILE_READ})


class ToolPolicy:
    def __init__(self, tools: list[BaseTool]):
        self._tools = tools

    def allowed_names(self, capability: TeamCapability) -> frozenset[str]:
        if capability in STATIC_NAMES:
            return STATIC_NAMES[capability]
        toolbox = TOOLBOX_NAMES[capability]
        names: set[str] = set()
        for tool in self._tools:
            if tool.name == toolbox:
                names.update(schema["function"]["name"] for schema in tool.get_tools())
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
```

- [ ] **Step 4: 写 BaseAgent 工具和 Memory 隔离失败测试**

```python
import asyncio

from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.base import BaseTool, tool


class FakeSessionRepository:
    def __init__(self):
        self.save_memory_calls = 0

    async def get_memory(self, session_id, agent_name):
        return Memory()

    async def save_memory(self, session_id, agent_name, memory):
        self.save_memory_calls += 1


class FakeUow:
    def __init__(self):
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class DemoAgentTool(BaseTool):
    name = "demo"

    @tool(name="search_web", description="search", parameters={}, required=[])
    async def search_web(self):
        raise AssertionError("not invoked")

    @tool(name="shell_execute", description="shell", parameters={}, required=[])
    async def shell_execute(self):
        raise AssertionError("not invoked")


class TestAgent(BaseAgent):
    name = "test_agent"


def make_agent(*, allowed_tool_names, memory, persist_memory):
    uow = FakeUow()
    agent = TestAgent(
        uow_factory=lambda: uow,
        session_id="session-1",
        agent_config=AgentConfig(),
        llm=object(),
        json_parser=object(),
        tools=[DemoAgentTool()],
        allowed_tool_names=allowed_tool_names,
        memory=memory,
        persist_memory=persist_memory,
    )
    return agent, uow


def test_agent_filters_schema_and_rejects_runtime_bypass():
    agent, uow = make_agent(
        allowed_tool_names={"search_web"},
        memory=Memory(),
        persist_memory=False,
    )
    assert [item["function"]["name"] for item in agent._get_available_tools()] == ["search_web"]
    try:
        agent._get_tool("shell_execute")
    except PermissionError:
        pass
    else:
        raise AssertionError("runtime tool bypass must be rejected")
    asyncio.run(agent._add_to_memory([{"role": "user", "content": "isolated"}]))
    assert agent._memory.get_last_message()["content"] == "isolated"
    assert uow.session.save_memory_calls == 0
```

- [ ] **Step 5: 修改 BaseAgent，保持默认行为兼容**

```python
# constructor additions
memory: Optional[Memory] = None,
persist_memory: bool = True,
memory_key: Optional[str] = None,
allowed_tool_names: Optional[set[str] | frozenset[str]] = None,

# constructor body
self._memory = memory
self._persist_memory = persist_memory
self._memory_key = memory_key or self.name
self._allowed_tool_names = frozenset(allowed_tool_names) if allowed_tool_names is not None else None

# replace _ensure_memory
async def _ensure_memory(self) -> None:
    if self._memory is not None:
        return
    if not self._persist_memory:
        self._memory = Memory()
        return
    async with self._uow:
        self._memory = await self._uow.session.get_memory(self._session_id, self._memory_key)

# filter in _get_available_tools
schemas = [schema for tool in self._tools for schema in tool.get_tools()]
if self._allowed_tool_names is None:
    return schemas
return [schema for schema in schemas if schema["function"]["name"] in self._allowed_tool_names]

# first line in _get_tool
if self._allowed_tool_names is not None and tool_name not in self._allowed_tool_names:
    raise PermissionError(f"工具未授权: {tool_name}")

# use this helper from _add_to_memory, compact_memory and roll_back
async def _save_memory(self) -> None:
    if not self._persist_memory:
        return
    async with self._uow:
        await self._uow.session.save_memory(self._session_id, self._memory_key, self._memory)
```

- [ ] **Step 6: 取得许可后运行策略和 BaseAgent 测试**

Run: `cd api && pytest tests/app/domain/services/team/test_policy.py tests/app/domain/services/agents/test_base_agent_policy.py -q`

Expected: PASS，策略过滤、运行时鉴权和临时 Memory 均通过。

- [ ] **Step 7: 提交权限与 Memory 改造**

```bash
git add api/app/domain/services/team/policy.py api/app/domain/services/agents/base.py api/tests/app/domain/services/team/test_policy.py api/tests/app/domain/services/agents/test_base_agent_policy.py
git commit -m "feat: isolate team memory and tools"
```

### Task 4: 扩展 Team Domain Event 和 SSE 协议

**Files:**

- Modify: `api/app/domain/models/event.py:18-157`
- Modify: `api/app/interfaces/schemas/event.py:16-309`
- Test: `api/tests/app/interfaces/schemas/test_team_events.py`

- [ ] **Step 1: 写 Event 判别联合和 SSE 映射失败测试**

```python
from pydantic import TypeAdapter

from app.domain.models.event import Event, TaskGraphEvent, TeamTaskEvent, ToolEvent, ToolEventStatus
from app.domain.models.team import PlannedTask, PlannedTaskGraph, TeamCapability
from app.domain.services.team.graph import build_task_graph
from app.interfaces.schemas.event import EventMapper


def graph():
    plan = PlannedTaskGraph(
        title="t",
        goal="g",
        tasks=[PlannedTask(id="a", description="a", capability=TeamCapability.SEARCH, success_criteria="done")],
    )
    return build_task_graph(plan, 5)


def test_team_events_round_trip_and_map_to_sse():
    g = graph()
    events = [
        TaskGraphEvent(graph=g),
        TeamTaskEvent(graph_id=g.id, task=g.tasks[0], agent_id="worker-1", attempt=1),
    ]
    for event in events:
        parsed = TypeAdapter(Event).validate_json(event.model_dump_json())
        assert parsed.type == event.type
        assert EventMapper.event_to_sse_event(parsed).event == event.type


def test_tool_event_keeps_team_metadata():
    event = ToolEvent(
        tool_call_id="call-1",
        tool_name="search",
        function_name="search_web",
        function_args={"query": "x"},
        status=ToolEventStatus.CALLING,
        graph_id="g",
        task_id="a",
        agent_id="worker-1",
        attempt=1,
    )
    data = EventMapper.event_to_sse_event(event).data
    assert data.task_id == "a"
    assert data.agent_id == "worker-1"
```

- [ ] **Step 2: 取得许可后运行测试并确认失败**

Run: `cd api && pytest tests/app/interfaces/schemas/test_team_events.py -q`

Expected: FAIL，Team Event 或 Tool metadata 尚不存在。

- [ ] **Step 3: 增加领域事件和用户 mode**

```python
# event.py additions
from .team import AgentMode, TaskGraph, TeamTask

# MessageEvent field
agent_mode: Optional[AgentMode] = None


class TaskGraphEvent(BaseEvent):
    type: Literal["task_graph"] = "task_graph"
    graph: TaskGraph


class TeamTaskEvent(BaseEvent):
    type: Literal["task"] = "task"
    graph_id: str
    task: TeamTask
    agent_id: Optional[str] = None
    attempt: int = 0

# ToolEvent optional additions
graph_id: Optional[str] = None
task_id: Optional[str] = None
agent_id: Optional[str] = None
attempt: Optional[int] = None

# add TaskGraphEvent and TeamTaskEvent to Event union
```

- [ ] **Step 4: 增加 SSE Data 和 Mapper 类型**

```python
class TaskGraphEventData(BaseEventData):
    graph: TaskGraph


class TaskGraphSSEEvent(BaseSSEEvent):
    event: Literal["task_graph"] = "task_graph"
    data: TaskGraphEventData


class TeamTaskEventData(BaseEventData):
    graph_id: str
    task: TeamTask
    agent_id: Optional[str] = None
    attempt: int = 0


class TeamTaskSSEEvent(BaseSSEEvent):
    event: Literal["task"] = "task"
    data: TeamTaskEventData

# ToolEventData additions
graph_id: Optional[str] = None
task_id: Optional[str] = None
agent_id: Optional[str] = None
attempt: Optional[int] = None

# ToolSSEEvent.from_event passes all four fields
# AgentSSEEvent union includes TaskGraphSSEEvent and TeamTaskSSEEvent
```

- [ ] **Step 5: 取得许可后运行协议测试**

Run: `cd api && pytest tests/app/interfaces/schemas/test_team_events.py -q`

Expected: PASS，2 tests passed。

- [ ] **Step 6: 提交事件协议**

```bash
git add api/app/domain/models/event.py api/app/interfaces/schemas/event.py api/tests/app/interfaces/schemas/test_team_events.py
git commit -m "feat: add team DAG events"
```

### Task 5: 实现确定性 TeamOrchestrator

**Files:**

- Create: `api/app/domain/services/team/orchestrator.py`
- Test: `api/tests/app/domain/services/team/test_orchestrator.py`

- [ ] **Step 1: 写真实并发与独占调度失败测试**

```python
import asyncio

import pytest

from app.domain.models.team import PlannedTask, PlannedTaskGraph, TeamCapability, WorkerResult
from app.domain.services.team.graph import build_task_graph
from app.domain.services.team.orchestrator import TeamOrchestrator


class Probe:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.exclusive_overlap = False


class ProbeWorker:
    def __init__(self, probe: Probe, capability: TeamCapability):
        self.probe = probe
        self.capability = capability

    async def execute(self, **kwargs):
        self.probe.active += 1
        self.probe.max_active = max(self.probe.max_active, self.probe.active)
        if self.capability is TeamCapability.BROWSER and self.probe.active != 1:
            self.probe.exclusive_overlap = True
        await asyncio.sleep(0.02)
        self.probe.active -= 1
        return WorkerResult(success=True, summary="done")


def test_parallel_safe_tasks_overlap_and_exclusive_task_does_not():
    async def scenario():
        probe = Probe()
        plan = PlannedTaskGraph(
            title="t",
            goal="g",
            tasks=[
                PlannedTask(id="a", description="a", capability=TeamCapability.SEARCH, success_criteria="done"),
                PlannedTask(id="b", description="b", capability=TeamCapability.FILE_READ, success_criteria="done"),
                PlannedTask(id="c", description="c", capability=TeamCapability.ANALYSIS, success_criteria="done"),
                PlannedTask(id="d", description="d", capability=TeamCapability.BROWSER, success_criteria="done"),
            ],
        )
        graph = build_task_graph(plan, 5)
        emitted = []
        async def emit(event, wait_for_publish=True):
            emitted.append(event)
        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, task, attempt: ProbeWorker(
                probe,
                task.capability,
            ),
            is_parallel_safe=lambda capability: capability in {
                TeamCapability.ANALYSIS, TeamCapability.SEARCH, TeamCapability.FILE_READ,
            },
            max_workers=3,
            max_retries=1,
            timeout_seconds=1,
        )
        await orchestrator.run(graph, [], emit)
        assert probe.max_active == 3
        assert not probe.exclusive_overlap
    asyncio.run(scenario())
```

- [ ] **Step 2: 写一次重试和依赖跳过失败测试**

```python
class AlwaysFailWorker:
    def __init__(self, calls: list[int], attempt: int):
        self.calls = calls
        self.attempt = attempt

    async def execute(self, **kwargs):
        self.calls.append(self.attempt)
        raise RuntimeError("boom")


def test_worker_retries_once_then_skips_dependents():
    async def scenario():
        calls: list[int] = []
        graph = build_task_graph(
            PlannedTaskGraph(
                title="retry",
                goal="retry once",
                tasks=[
                    PlannedTask(
                        id="failing",
                        description="always fails",
                        capability=TeamCapability.SEARCH,
                        success_criteria="done",
                    ),
                    PlannedTask(
                        id="dependent",
                        description="depends on failure",
                        dependencies=["failing"],
                        capability=TeamCapability.ANALYSIS,
                        success_criteria="done",
                    ),
                ],
            ),
            max_tasks=5,
        )
        emitted = []

        async def emit(event, wait_for_publish=True):
            emitted.append(event)

        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, task, attempt: AlwaysFailWorker(
                calls,
                attempt,
            ),
            is_parallel_safe=lambda capability: True,
            max_workers=3,
            max_retries=1,
            timeout_seconds=1,
        )
        await orchestrator.run(graph, [], emit)

        assert calls == [1, 2]
        assert graph.task_by_id("failing").attempt_count == 2
        assert graph.task_by_id("failing").status.value == "failed"
        assert graph.task_by_id("dependent").status.value == "skipped"
        assert graph.task_by_id("dependent").error == "dependency_failed"

    asyncio.run(scenario())
```

- [ ] **Step 3: 取得许可后运行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/team/test_orchestrator.py -q`

Expected: FAIL，提示 `TeamOrchestrator` 不存在。

- [ ] **Step 4: 实现 Worker 协议、批次调度和 Attempt**

```python
# api/app/domain/services/team/orchestrator.py
import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.domain.models.event import BaseEvent, TeamTaskEvent
from app.domain.models.team import TaskGraph, TaskGraphStatus, TeamCapability, TeamTask, TeamTaskStatus, WorkerResult
from app.domain.services.team.graph import finalize_graph, propagate_skipped, ready_tasks

EmitEvent = Callable[[BaseEvent, bool], Awaitable[None]]


class WorkerExecutor(Protocol):
    async def execute(self, *, goal: str, dependency_results: dict[str, WorkerResult], attachments: list[str], emit: EmitEvent) -> WorkerResult: ...


class TeamOrchestrator:
    def __init__(self, *, worker_factory, is_parallel_safe, max_workers: int, max_retries: int, timeout_seconds: int):
        self._worker_factory = worker_factory
        self._is_parallel_safe = is_parallel_safe
        self._max_workers = max_workers
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds

    async def _emit_task(self, graph: TaskGraph, task: TeamTask, emit: EmitEvent, wait: bool = True):
        await emit(TeamTaskEvent(
            graph_id=graph.id,
            task=task.model_copy(deep=True),
            agent_id=task.assigned_agent_id,
            attempt=task.attempt_count,
        ), wait)

    async def _execute_task(self, graph: TaskGraph, task: TeamTask, slot: int, attachments: list[str], emit: EmitEvent):
        task.assigned_agent_id = f"worker-{slot}"
        dependencies = {
            dependency: graph.task_by_id(dependency).result
            for dependency in task.dependencies
            if graph.task_by_id(dependency).result is not None
        }
        for attempt in range(1, self._max_retries + 2):
            task.attempt_count = attempt
            task.status = TeamTaskStatus.RUNNING
            task.error = None
            await self._emit_task(graph, task, emit)
            worker = self._worker_factory(
                graph.id,
                task.assigned_agent_id,
                task,
                attempt,
            )
            try:
                result = await asyncio.wait_for(
                    worker.execute(goal=graph.goal, dependency_results=dependencies, attachments=attachments, emit=emit),
                    timeout=self._timeout_seconds,
                )
                if not result.success:
                    raise RuntimeError(result.summary or "worker reported failure")
                task.result = result
                task.status = TeamTaskStatus.COMPLETED
                await self._emit_task(graph, task, emit)
                return
            except asyncio.CancelledError:
                task.status = TeamTaskStatus.CANCELLED
                task.error = "cancelled"
                await self._emit_task(graph, task, emit, wait=False)
                raise
            except Exception as exc:
                task.error = str(exc)
                if attempt <= self._max_retries:
                    task.status = TeamTaskStatus.RETRYING
                    await self._emit_task(graph, task, emit)
                else:
                    task.status = TeamTaskStatus.FAILED
                    await self._emit_task(graph, task, emit)

    async def run(self, graph: TaskGraph, attachments: list[str], emit: EmitEvent) -> TaskGraph:
        graph.status = TaskGraphStatus.RUNNING
        while True:
            for skipped in propagate_skipped(graph):
                await self._emit_task(graph, skipped, emit)
            ready = ready_tasks(graph)
            if not ready:
                if all(task.status not in {TeamTaskStatus.PENDING, TeamTaskStatus.RUNNING, TeamTaskStatus.RETRYING} for task in graph.tasks):
                    break
                graph.status = TaskGraphStatus.FAILED
                graph.error = "scheduler_deadlock"
                break
            safe = [task for task in ready if self._is_parallel_safe(task.capability)]
            if safe:
                batch = safe[:self._max_workers]
                await asyncio.gather(*(
                    self._execute_task(graph, task, slot + 1, attachments, emit)
                    for slot, task in enumerate(batch)
                ))
                continue
            await self._execute_task(graph, ready[0], 1, attachments, emit)
        finalize_graph(graph)
        return graph
```

- [ ] **Step 5: 补齐取消测试**

```python
class BlockingWorker:
    def __init__(self, started: asyncio.Event):
        self.started = started

    async def execute(self, **kwargs):
        self.started.set()
        await asyncio.Event().wait()


def test_cancellation_marks_running_task_cancelled():
    async def scenario():
        started = asyncio.Event()
        graph = build_task_graph(
            PlannedTaskGraph(
                title="cancel",
                goal="cancel running work",
                tasks=[
                    PlannedTask(
                        id="blocking",
                        description="wait forever",
                        capability=TeamCapability.SEARCH,
                        success_criteria="done",
                    )
                ],
            ),
            max_tasks=5,
        )
        emitted = []

        async def emit(event, wait_for_publish=True):
            emitted.append(event)

        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, task, attempt: BlockingWorker(
                started,
            ),
            is_parallel_safe=lambda capability: True,
            max_workers=3,
            max_retries=1,
            timeout_seconds=60,
        )
        running = asyncio.create_task(orchestrator.run(graph, [], emit))
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert graph.task_by_id("blocking").status.value == "cancelled"
        assert any(
            getattr(event, "task", None)
            and event.task.id == "blocking"
            and event.task.status.value == "cancelled"
            for event in emitted
        )

    asyncio.run(scenario())
```

- [ ] **Step 6: 取得许可后运行 Orchestrator 测试**

Run: `cd api && pytest tests/app/domain/services/team/test_orchestrator.py -q`

Expected: PASS，并发峰值为 3、独占无重叠、重试恰好一次、取消状态正确。

- [ ] **Step 7: 提交 Orchestrator**

```bash
git add api/app/domain/services/team/orchestrator.py api/tests/app/domain/services/team/test_orchestrator.py
git commit -m "feat: orchestrate team task DAGs"
```

### Task 6: 实现 Planner、通用 Worker 和 Synthesizer

**Files:**

- Create: `api/app/domain/services/prompts/team.py`
- Create: `api/app/domain/services/agents/team_planner.py`
- Create: `api/app/domain/services/agents/task_worker.py`
- Create: `api/app/domain/services/agents/team_synthesizer.py`
- Test: `api/tests/app/domain/services/agents/test_team_agents.py`

- [ ] **Step 1: 写 URL 防伪和结构化输出失败测试**

```python
import asyncio

import pytest

from app.domain.models.team import SourceRef, WorkerResult
from app.domain.services.agents.task_worker import validate_sources
from app.domain.services.agents.team_synthesizer import validate_final_links


def test_worker_rejects_unobserved_source_url():
    result = WorkerResult(
        success=True,
        summary="finding",
        sources=[SourceRef(title="invented", url="https://invented.example/item")],
    )
    with pytest.raises(ValueError, match="unobserved"):
        validate_sources(result, {"https://observed.example/item"})


def test_synthesizer_rejects_new_markdown_link():
    with pytest.raises(ValueError, match="unknown source"):
        validate_final_links(
            "结论 [来源](https://invented.example/item)",
            {"https://observed.example/item"},
        )
```

- [ ] **Step 2: 取得许可后执行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/agents/test_team_agents.py -q`

Expected: FAIL，Team Agent 模块不存在。

- [ ] **Step 3: 写三类 Agent 的明确提示词**

```python
# api/app/domain/services/prompts/team.py
PLANNER_SYSTEM_PROMPT = """你是 Team Planner。只输出 JSON，不调用工具。
把用户目标拆成 1 到 5 个 DAG 节点。每个节点只能选择一个 capability：
analysis, search, browser, file_read, file_write, shell, mcp, a2a。
跨能力工作必须拆成依赖节点。禁止输出 status、agent_id、attempt、result 或工具函数名。
输出格式：
{"title":"...","goal":"...","tasks":[{"id":"task_1","description":"...","dependencies":[],"capability":"search","success_criteria":"..."}]}
"""

WORKER_SYSTEM_PROMPT = """你只负责一个 DAG 节点。只能使用已暴露的工具。
不要改变全局计划，不要向用户提问。最后只输出 JSON：
{"success":true,"summary":"...","sources":[{"title":"...","url":"https://...","snippet":"..."}],"artifacts":[],"notes":[]}
sources 只能引用本节点工具结果中真实出现的 URL。
"""

SYNTHESIZER_SYSTEM_PROMPT = """你负责汇总已经完成的 DAG 结果，不调用工具、不新增事实或来源。
明确说明失败和跳过节点，保留来源 Markdown 链接，并只输出 JSON：
{"message":"...","attachments":["/sandbox/path"]}
"""
```

- [ ] **Step 4: 实现 Planner 的结构化解析**

```python
# api/app/domain/services/agents/team_planner.py
from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.models.team import PlannedTaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import PLANNER_SYSTEM_PROMPT


class TeamPlannerAgent(BaseAgent):
    name = "team_planner"
    _system_prompt = PLANNER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def create_graph(self, message: Message, validation_error: str | None = None) -> PlannedTaskGraph:
        query = {
            "goal": message.message,
            "attachments": message.attachments,
            "previous_validation_error": validation_error,
        }
        async for event in self.invoke(str(query)):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                return PlannedTaskGraph.model_validate(parsed)
        raise RuntimeError("planner produced no graph")
```

- [ ] **Step 5: 实现通用 Worker、事件归属与 URL 收集**

```python
# api/app/domain/services/agents/task_worker.py
import re
from collections.abc import Awaitable, Callable

from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, ToolEvent
from app.domain.models.team import TeamTask, WorkerResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import WORKER_SYSTEM_PROMPT

EmitEvent = Callable[[BaseEvent, bool], Awaitable[None]]
URL_RE = re.compile(r"https?://[^\s\]\)\"']+")


def collect_urls(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return set(URL_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(collect_urls(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple, set)):
        return set().union(*(collect_urls(item) for item in value)) if value else set()
    if hasattr(value, "model_dump"):
        return collect_urls(value.model_dump(mode="json"))
    return set()


def validate_sources(result: WorkerResult, observed_urls: set[str]) -> None:
    unknown = {str(source.url) for source in result.sources} - observed_urls
    if unknown:
        raise ValueError(f"unobserved source URLs: {sorted(unknown)}")


class TaskWorker(BaseAgent):
    name = "task_worker"
    _system_prompt = WORKER_SYSTEM_PROMPT
    _format = "json_object"

    def __init__(self, *args, graph_id: str, task: TeamTask, agent_id: str, attempt: int, **kwargs):
        super().__init__(*args, **kwargs)
        self._graph_id = graph_id
        self._task = task
        self._agent_id = agent_id
        self._attempt = attempt

    async def execute(self, *, goal, dependency_results, attachments, emit: EmitEvent) -> WorkerResult:
        observed_urls: set[str] = set()
        query = str({
            "goal": goal,
            "task": self._task.model_dump(mode="json", exclude={"result", "error"}),
            "dependency_results": {key: value.model_dump(mode="json") for key, value in dependency_results.items()},
            "attachments": attachments,
        })
        async for event in self.invoke(query):
            if isinstance(event, ToolEvent):
                event.graph_id = self._graph_id
                event.task_id = self._task.id
                event.agent_id = self._agent_id
                event.attempt = self._attempt
                observed_urls.update(collect_urls(event.function_args))
                observed_urls.update(collect_urls(event.function_result))
                await emit(event, True)
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            elif isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                result = WorkerResult.model_validate(parsed)
                validate_sources(result, observed_urls)
                return result
        raise RuntimeError("worker produced no result")
```

- [ ] **Step 6: 实现 Synthesizer 和最终链接校验**

```python
# api/app/domain/services/agents/team_synthesizer.py
import re

from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.team import FinalTeamResponse, TaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import SYNTHESIZER_SYSTEM_PROMPT

MARKDOWN_URL_RE = re.compile(r"\]\((https?://[^)]+)\)")


def validate_final_links(message: str, allowed_urls: set[str]) -> None:
    unknown = set(MARKDOWN_URL_RE.findall(message)) - allowed_urls
    if unknown:
        raise ValueError(f"unknown source URLs: {sorted(unknown)}")


class TeamSynthesizerAgent(BaseAgent):
    name = "team_synthesizer"
    _system_prompt = SYNTHESIZER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def synthesize(self, graph: TaskGraph) -> FinalTeamResponse:
        allowed_urls = {
            str(source.url)
            for task in graph.tasks if task.result
            for source in task.result.sources
        }
        async for event in self.invoke(graph.model_dump_json()):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                response = FinalTeamResponse.model_validate(parsed)
                validate_final_links(response.message, allowed_urls)
                return response
        raise RuntimeError("synthesizer produced no response")
```

- [ ] **Step 7: 补充 Planner 解析和 ToolEvent metadata 测试**

在同一测试文件追加以下 Fake 与纵向测试；所有 Agent 显式使用临时 `Memory`，因此不触碰真实仓储：

```python
import json

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import ToolEventStatus
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.team import TeamTaskStatus
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.task_worker import TaskWorker
from app.domain.services.agents.team_planner import TeamPlannerAgent
from app.domain.services.agents.team_synthesizer import TeamSynthesizerAgent
from app.domain.services.team.graph import build_task_graph
from app.domain.services.tools.base import BaseTool, tool


class QueueLLM:
    def __init__(self, *responses):
        self.responses = list(responses)

    async def invoke(self, **kwargs):
        return self.responses.pop(0)


class JsonParser:
    async def invoke(self, text, default_value=None):
        return json.loads(text)


class FakeSessionRepository:
    def __init__(self):
        self.save_memory_calls = 0

    async def get_memory(self, session_id, agent_name):
        return Memory()

    async def save_memory(self, session_id, agent_name, memory):
        self.save_memory_calls += 1


class FakeUow:
    def __init__(self):
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class ObservedSearchTool(BaseTool):
    name = "search"

    @tool(
        name="search_web",
        description="search",
        parameters={"query": {"type": "string"}},
        required=["query"],
    )
    async def search_web(self, query: str):
        return ToolResult(data={"url": "https://observed.example/item"})


def agent_kwargs(llm, tools):
    uow = FakeUow()
    return {
        "uow_factory": lambda: uow,
        "session_id": "session-1",
        "agent_config": AgentConfig(max_iterations=3),
        "llm": llm,
        "json_parser": JsonParser(),
        "tools": tools,
        "memory": Memory(),
        "persist_memory": False,
    }, uow


def test_planner_worker_and_synthesizer_keep_structure_metadata_and_sources():
    async def scenario():
        planner_json = {
            "title": "research",
            "goal": "find a source",
            "tasks": [{
                "id": "collect",
                "description": "collect one source",
                "dependencies": [],
                "capability": "search",
                "success_criteria": "one observed URL",
            }],
        }
        planner_kwargs, planner_uow = agent_kwargs(
            QueueLLM({"role": "assistant", "content": json.dumps(planner_json)}),
            [],
        )
        planned = await TeamPlannerAgent(**planner_kwargs).create_graph(Message(message="research"))
        assert planned.tasks[0].id == "collect"
        assert "status" not in planned.tasks[0].model_fields

        graph = build_task_graph(planned, max_tasks=5)
        worker_llm = QueueLLM(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "search_web",
                        "arguments": json.dumps({"query": "multi agent DAG"}),
                    },
                }],
            },
            {
                "role": "assistant",
                "content": json.dumps({
                    "success": True,
                    "summary": "found an observed source",
                    "sources": [{
                        "title": "Observed",
                        "url": "https://observed.example/item",
                    }],
                    "artifacts": [],
                    "notes": [],
                }),
            },
        )
        worker_kwargs, worker_uow = agent_kwargs(worker_llm, [ObservedSearchTool()])
        worker = TaskWorker(
            **worker_kwargs,
            allowed_tool_names={"search_web"},
            graph_id=graph.id,
            task=graph.tasks[0],
            agent_id="worker-1",
            attempt=1,
        )
        emitted = []

        async def emit(event, wait_for_publish=True):
            emitted.append(event.model_copy(deep=True))

        result = await worker.execute(
            goal=graph.goal,
            dependency_results={},
            attachments=[],
            emit=emit,
        )
        assert [event.status for event in emitted] == [
            ToolEventStatus.CALLING,
            ToolEventStatus.CALLED,
        ]
        assert {
            (event.graph_id, event.task_id, event.agent_id, event.attempt)
            for event in emitted
        } == {(graph.id, "collect", "worker-1", 1)}
        assert str(result.sources[0].url) == "https://observed.example/item"

        graph.tasks[0].status = TeamTaskStatus.COMPLETED
        graph.tasks[0].result = result
        synthesizer_kwargs, synthesizer_uow = agent_kwargs(
            QueueLLM({
                "role": "assistant",
                "content": json.dumps({
                    "message": "结论 [Observed](https://observed.example/item)",
                    "attachments": [],
                }),
            }),
            [],
        )
        final = await TeamSynthesizerAgent(**synthesizer_kwargs).synthesize(graph)
        assert "https://observed.example/item" in final.message
        assert planner_uow.session.save_memory_calls == 0
        assert worker_uow.session.save_memory_calls == 0
        assert synthesizer_uow.session.save_memory_calls == 0

    asyncio.run(scenario())
```

- [ ] **Step 8: 取得许可后运行 Team Agent 测试**

Run: `cd api && pytest tests/app/domain/services/agents/test_team_agents.py -q`

Expected: PASS，URL 防伪、Planner 解析、事件 metadata 和汇总校验通过。

- [ ] **Step 9: 提交 Team Agents**

```bash
git add api/app/domain/services/prompts/team.py api/app/domain/services/agents/team_planner.py api/app/domain/services/agents/task_worker.py api/app/domain/services/agents/team_synthesizer.py api/tests/app/domain/services/agents/test_team_agents.py
git commit -m "feat: add team planner workers and synthesizer"
```

### Task 7: 实现 TeamFlow、事件确认和 FlowRouter

**Files:**

- Create: `api/app/domain/services/flows/team.py`
- Create: `api/app/domain/services/flows/router.py`
- Modify: `api/app/domain/services/flows/base.py:14-38`
- Test: `api/tests/app/domain/services/flows/test_team_flow.py`

- [ ] **Step 1: 写事件确认和 TeamFlow 阶段失败测试**

```python
import asyncio

from app.domain.models.event import MessageEvent, TaskGraphEvent, TeamTaskEvent
from app.domain.models.message import Message
from app.domain.models.team import (
    FinalTeamResponse,
    PlannedTask,
    PlannedTaskGraph,
    TeamCapability,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.services.flows.team import QueuedEventEmitter
from app.domain.services.flows.team import TeamFlow
from app.domain.services.team.graph import build_task_graph, finalize_graph


def valid_plan():
    return PlannedTaskGraph(
        title="research",
        goal="find one result",
        tasks=[PlannedTask(
            id="collect",
            description="collect",
            capability=TeamCapability.SEARCH,
            success_criteria="done",
        )],
    )


def make_graph():
    return build_task_graph(valid_plan(), max_tasks=5)


def test_tool_producer_waits_until_event_is_published():
    async def scenario():
        emitter = QueuedEventEmitter()
        reached_after_emit = asyncio.Event()

        async def producer():
            await emitter.emit(TaskGraphEvent(graph=make_graph()), True)
            reached_after_emit.set()

        task = asyncio.create_task(producer())
        envelope = await emitter.get()
        await asyncio.sleep(0)
        assert not reached_after_emit.is_set()
        envelope.confirm()
        await task
        assert reached_after_emit.is_set()
    asyncio.run(scenario())


class FakeSessionRepository:
    def __init__(self):
        self.statuses = []

    async def update_status(self, session_id, status):
        self.statuses.append(status)


class FakeUow:
    def __init__(self):
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class ReplanningPlanner:
    def __init__(self):
        self.validation_errors = []

    async def create_graph(self, message, validation_error=None):
        self.validation_errors.append(validation_error)
        if len(self.validation_errors) == 1:
            return PlannedTaskGraph(
                title="bad",
                goal="cycle",
                tasks=[
                    PlannedTask(
                        id="a",
                        description="a",
                        dependencies=["b"],
                        capability=TeamCapability.ANALYSIS,
                        success_criteria="done",
                    ),
                    PlannedTask(
                        id="b",
                        description="b",
                        dependencies=["a"],
                        capability=TeamCapability.ANALYSIS,
                        success_criteria="done",
                    ),
                ],
            )
        return valid_plan()


class CompletingOrchestrator:
    async def run(self, graph, attachments, emit):
        task = graph.tasks[0]
        task.status = TeamTaskStatus.RUNNING
        task.assigned_agent_id = "worker-1"
        task.attempt_count = 1
        await emit(TeamTaskEvent(
            graph_id=graph.id,
            task=task.model_copy(deep=True),
            agent_id="worker-1",
            attempt=1,
        ), True)
        task.status = TeamTaskStatus.COMPLETED
        task.result = WorkerResult(success=True, summary="done")
        await emit(TeamTaskEvent(
            graph_id=graph.id,
            task=task.model_copy(deep=True),
            agent_id="worker-1",
            attempt=1,
        ), True)
        finalize_graph(graph)
        return graph


class FakeSynthesizer:
    async def synthesize(self, graph):
        return FinalTeamResponse(message="final answer")


def test_team_flow_replans_once_and_emits_ordered_terminal_events():
    async def scenario():
        planner = ReplanningPlanner()
        uow = FakeUow()
        flow = TeamFlow(
            uow_factory=lambda: uow,
            session_id="session-1",
            team_max_tasks=5,
            planner=planner,
            orchestrator=CompletingOrchestrator(),
            synthesizer_factory=FakeSynthesizer,
        )
        events = [event async for event in flow.invoke(Message(message="research"))]

        assert planner.validation_errors[0] is None
        assert "cycle" in planner.validation_errors[1]
        assert [event.type for event in events] == [
            "title",
            "task_graph",
            "task",
            "task",
            "task_graph",
            "message",
            "done",
        ]
        assert isinstance(events[-2], MessageEvent)
        assert events[-2].message == "final answer"
        assert events[1].graph.status.value == "pending"
        assert events[-3].graph.status.value == "completed"

    asyncio.run(scenario())
```

- [ ] **Step 2: 取得许可后运行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/flows/test_team_flow.py -q`

Expected: FAIL，TeamFlow/QueuedEventEmitter 不存在。

- [ ] **Step 3: 为 BaseFlow 增加向后兼容取消接口**

```python
async def cancel_events(self) -> list[BaseEvent]:
    return []
```

该方法不是 abstract，现有 `PlannerReActFlow` 无需修改。

- [ ] **Step 4: 实现带确认 Future 的事件队列**

```python
@dataclass
class EventEnvelope:
    event: BaseEvent
    published: asyncio.Future[None]

    def confirm(self) -> None:
        if not self.published.done():
            self.published.set_result(None)


class QueuedEventEmitter:
    def __init__(self):
        self._queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue()
        self._closed = False

    async def emit(self, event: BaseEvent, wait_for_publish: bool = True) -> None:
        if self._closed:
            raise RuntimeError("event emitter is closed")
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(EventEnvelope(event, future))
        if wait_for_publish:
            await future

    async def get(self) -> EventEnvelope | None:
        return await self._queue.get()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)
```

- [ ] **Step 5: 实现 TeamFlow 主阶段**

`TeamFlow.invoke()` 必须按以下明确顺序实现：

```python
# methods inside TeamFlow(BaseFlow)
def __init__(
    self,
    *,
    uow_factory,
    session_id: str,
    team_max_tasks: int,
    planner,
    orchestrator,
    synthesizer_factory,
):
    self._uow = uow_factory()
    self._session_id = session_id
    self._team_max_tasks = team_max_tasks
    self._planner = planner
    self._orchestrator = orchestrator
    self._synthesizer_factory = synthesizer_factory
    self._graph: TaskGraph | None = None
    self._producer: asyncio.Task[TaskGraph] | None = None

async def invoke(self, message: Message):
    async with self._uow:
        await self._uow.session.update_status(self._session_id, SessionStatus.RUNNING)

    validation_error = None
    for planner_attempt in range(2):
        planned = await self._planner.create_graph(message, validation_error)
        try:
            self._graph = build_task_graph(planned, self._team_max_tasks)
            break
        except TaskGraphError as exc:
            validation_error = str(exc)
    else:
        yield ErrorEvent(error=f"Team Planner 生成无效 DAG: {validation_error}")
        return

    yield TitleEvent(title=self._graph.title)
    yield TaskGraphEvent(graph=self._graph.model_copy(deep=True))

    emitter = QueuedEventEmitter()

    async def produce():
        try:
            return await self._orchestrator.run(
                self._graph,
                message.attachments,
                emitter.emit,
            )
        finally:
            await emitter.close()

    self._producer = asyncio.create_task(produce())
    while True:
        envelope = await emitter.get()
        if envelope is None:
            break
        try:
            yield envelope.event
        finally:
            envelope.confirm()
    try:
        self._graph = await self._producer
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield ErrorEvent(error=f"Team 调度失败: {exc}")
        return
    yield TaskGraphEvent(graph=self._graph.model_copy(deep=True))

    if self._graph.status in {TaskGraphStatus.COMPLETED, TaskGraphStatus.PARTIAL}:
        last_error = None
        for _ in range(2):
            try:
                final = await self._synthesizer_factory().synthesize(self._graph)
                yield MessageEvent(
                    role="assistant",
                    message=final.message,
                    attachments=[File(filepath=path) for path in final.attachments],
                )
                break
            except Exception as exc:
                last_error = str(exc)
        else:
            yield ErrorEvent(error=f"Team 汇总失败: {last_error}")
            return
    elif self._graph.status is TaskGraphStatus.FAILED:
        yield ErrorEvent(error=self._graph.error or "所有 Team Task 均失败")
        return

    yield DoneEvent()
```

- [ ] **Step 6: 实现取消快照**

`TeamFlow.cancel_events()` 先记录取消前的活动节点，再取消 producer。这样即使 Orchestrator 已在内存中把 running 节点改成 cancelled、但对应队列信封尚未被 Runner 持久化，取消快照仍不会漏掉该节点：

```python
async def cancel_events(self) -> list[BaseEvent]:
    if self._graph is None:
        return []

    active_ids = {
        task.id
        for task in self._graph.tasks
        if task.status in {
            TeamTaskStatus.PENDING,
            TeamTaskStatus.RUNNING,
            TeamTaskStatus.RETRYING,
        }
    }
    if self._producer is not None and not self._producer.done():
        self._producer.cancel()
        await asyncio.gather(self._producer, return_exceptions=True)

    events: list[BaseEvent] = []
    for task in self._graph.tasks:
        if task.id not in active_ids:
            continue
        task.status = TeamTaskStatus.CANCELLED
        task.error = "cancelled_by_user"
        events.append(TeamTaskEvent(
            graph_id=self._graph.id,
            task=task.model_copy(deep=True),
            agent_id=task.assigned_agent_id,
            attempt=task.attempt_count,
        ))
    self._graph.status = TaskGraphStatus.CANCELLED
    self._graph.error = "cancelled_by_user"
    events.append(TaskGraphEvent(graph=self._graph.model_copy(deep=True)))
    return events
```

该方法只返回事件，不直接写 Redis/DB。

- [ ] **Step 7: 实现 FlowRouter**

```python
class FlowRouter:
    def __init__(self, react_flow: BaseFlow, team_flow_factory):
        self._react_flow = react_flow
        self._team_flow_factory = team_flow_factory

    def resolve(self, mode: AgentMode) -> BaseFlow:
        if mode is AgentMode.REACT:
            return self._react_flow
        if mode is AgentMode.TEAM:
            return self._team_flow_factory()
        raise ValueError(f"不支持的 Agent mode: {mode}")
```

- [ ] **Step 8: 取得许可后运行 Flow 测试**

Run: `cd api && pytest tests/app/domain/services/flows/test_team_flow.py -q`

Expected: PASS，确认 backpressure、一次重规划、阶段顺序、partial 汇总和取消事件。

- [ ] **Step 9: 提交 TeamFlow**

```bash
git add api/app/domain/services/flows/base.py api/app/domain/services/flows/router.py api/app/domain/services/flows/team.py api/tests/app/domain/services/flows/test_team_flow.py
git commit -m "feat: add team flow and event queue"
```

### Task 8: 将 TeamFlow 接入 AgentTaskRunner

**Files:**

- Modify: `api/app/domain/services/agent_task_runner.py:42-443`
- Test: `api/tests/app/domain/services/test_agent_task_runner_modes.py`

- [ ] **Step 1: 写默认 React、显式 Team 和取消失败测试**

```python
import asyncio

from app.domain.models.event import DoneEvent, TaskGraphEvent, TeamTaskEvent
from app.domain.models.message import Message
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
)
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.router import FlowRouter
from app.domain.services.team.graph import build_task_graph


async def collect(stream):
    return [item async for item in stream]


class FakeFlow:
    def __init__(self, cancel_events=None):
        self.invocations = 0
        self._cancel_events = cancel_events or []

    async def invoke(self, message):
        self.invocations += 1
        yield DoneEvent()

    async def cancel_events(self):
        return self._cancel_events


def test_runner_routes_modes_without_changing_react_default():
    async def scenario():
        react = FakeFlow()
        team = FakeFlow()
        runner = object.__new__(AgentTaskRunner)
        runner._flow_router = FlowRouter(react, lambda: team)
        runner._active_flow = None

        await collect(runner._run_flow(Message(message="r"), AgentMode.REACT))
        await collect(runner._run_flow(Message(message="t"), AgentMode.TEAM))
        assert react.invocations == 1
        assert team.invocations == 1

    asyncio.run(scenario())


def cancelled_graph_events():
    graph = build_task_graph(
        PlannedTaskGraph(
            title="cancel",
            goal="cancel",
            tasks=[PlannedTask(
                id="a",
                description="a",
                capability=TeamCapability.SEARCH,
                success_criteria="done",
            )],
        ),
        max_tasks=5,
    )
    graph.tasks[0].status = TeamTaskStatus.CANCELLED
    graph.status = TaskGraphStatus.CANCELLED
    return [
        TeamTaskEvent(graph_id=graph.id, task=graph.tasks[0], attempt=1),
        TaskGraphEvent(graph=graph),
    ]


def test_runner_persists_cancel_snapshot_before_done():
    async def scenario():
        persisted = []
        runner = object.__new__(AgentTaskRunner)
        runner._active_flow = FakeFlow(cancelled_graph_events())

        async def record(task, event):
            persisted.append(event)

        runner._put_and_add_event = record
        await runner._persist_cancellation(object())
        assert [event.type for event in persisted] == ["task", "task_graph", "done"]

    asyncio.run(scenario())
```

- [ ] **Step 2: 取得许可后执行测试并确认失败**

Run: `cd api && pytest tests/app/domain/services/test_agent_task_runner_modes.py -q`

Expected: FAIL，Runner 仍固定使用 `_flow`。

- [ ] **Step 3: 构建 TeamFlow factory**

在 `team.py` 增加完整 factory，复用本轮已经初始化的 MCP/A2A、Sandbox、Browser 和 SearchEngine。这里刻意不把 `MessageTool` 放入 Team 工具列表，因此子 Worker 无法调用 `message_ask_user`：

```python
def build_team_flow(
    *,
    uow_factory,
    session_id,
    agent_config,
    llm,
    json_parser,
    browser,
    sandbox,
    search_engine,
    mcp_tool,
    a2a_tool,
) -> TeamFlow:
    tools = [
        FileTool(sandbox=sandbox),
        ShellTool(sandbox=sandbox),
        BrowserTool(browser=browser),
        SearchTool(search_engine=search_engine),
        mcp_tool,
        a2a_tool,
    ]
    policy = ToolPolicy(tools)
    planner = TeamPlannerAgent(
        uow_factory=uow_factory,
        session_id=session_id,
        agent_config=agent_config,
        llm=llm,
        json_parser=json_parser,
        tools=[],
        memory=Memory(),
        persist_memory=False,
        allowed_tool_names=frozenset(),
    )
    worker_config = agent_config.model_copy(update={
        "max_iterations": agent_config.team_max_worker_iterations,
    })

    def worker_factory(graph_id, agent_id, task, attempt):
        return TaskWorker(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=worker_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            memory=Memory(),
            persist_memory=False,
            allowed_tool_names=policy.allowed_names(task.capability),
            graph_id=graph_id,
            task=task,
            agent_id=agent_id,
            attempt=attempt,
        )

    orchestrator = TeamOrchestrator(
        worker_factory=worker_factory,
        is_parallel_safe=policy.is_parallel_safe,
        max_workers=agent_config.team_max_workers,
        max_retries=agent_config.team_max_task_retries,
        timeout_seconds=agent_config.team_task_timeout_seconds,
    )

    def synthesizer_factory():
        return TeamSynthesizerAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=[],
            memory=Memory(),
            persist_memory=False,
            allowed_tool_names=frozenset(),
        )

    return TeamFlow(
        uow_factory=uow_factory,
        session_id=session_id,
        team_max_tasks=agent_config.team_max_tasks,
        planner=planner,
        orchestrator=orchestrator,
        synthesizer_factory=synthesizer_factory,
    )
```

- [ ] **Step 4: 用 FlowRouter 替换固定 `_flow`**

```python
self._react_flow = PlannerReActFlow(
    uow_factory=uow_factory,
    llm=llm,
    agent_config=agent_config,
    session_id=session_id,
    json_parser=json_parser,
    browser=browser,
    sandbox=sandbox,
    search_engine=search_engine,
    mcp_tool=self._mcp_tool,
    a2a_tool=self._a2a_tool,
)
self._flow_router = FlowRouter(
    react_flow=self._react_flow,
    team_flow_factory=lambda: build_team_flow(
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
    ),
)
self._active_flow: BaseFlow | None = None

async def _run_flow(self, message: Message, mode: AgentMode = AgentMode.REACT):
    self._active_flow = self._flow_router.resolve(mode)
    async for event in self._active_flow.invoke(message):
        if isinstance(event, ToolEvent):
            await self._handle_tool_event(event)
        elif isinstance(event, MessageEvent):
            await self._sync_message_attachments_to_storage(event)
        yield event
```

在输入事件转换 Message 时读取：

```python
mode = event.agent_mode or AgentMode.REACT
async for output_event in self._run_flow(message_obj, mode):
    await self._put_and_add_event(task, output_event)
```

- [ ] **Step 5: 扩展取消处理**

```python
async def _persist_cancellation(self, task: Task) -> None:
    if self._active_flow:
        for cancel_event in await self._active_flow.cancel_events():
            await self._put_and_add_event(task, cancel_event)
    await self._put_and_add_event(task, DoneEvent())


except asyncio.CancelledError:
    await self._persist_cancellation(task)
    async with self._uow:
        await self._uow.session.update_status(self._session_id, SessionStatus.COMPLETED)
    raise
```

- [ ] **Step 6: 取得许可后运行 Runner 路由测试和旧 Flow 测试**

Run: `cd api && pytest tests/app/domain/services/test_agent_task_runner_modes.py tests/app/domain/services/flows/test_team_flow.py -q`

Expected: PASS；默认 React 只实例化/调用旧 Flow，Team 才新建 TeamFlow。

- [ ] **Step 7: 提交 Runner 接入**

```bash
git add api/app/domain/services/agent_task_runner.py api/app/domain/services/flows/team.py api/tests/app/domain/services/test_agent_task_runner_modes.py
git commit -m "feat: route agent runs to team flow"
```

### Task 9: 接入 API mode、运行冲突和进程中断收敛

**Files:**

- Modify: `api/app/application/errors/exceptions.py:10-52`
- Modify: `api/app/application/services/agent_service.py:129-247`
- Modify: `api/app/application/services/session_service.py:18-85`
- Modify: `api/app/domain/models/session.py:24-63`
- Modify: `api/app/interfaces/schemas/session.py:31-39`
- Modify: `api/app/interfaces/endpoints/session_routes.py:123-166`
- Modify: `api/app/interfaces/service_dependencies.py:43-78`
- Test: `api/tests/app/application/services/test_team_session_rules.py`
- Test: `api/tests/app/interfaces/endpoints/test_team_chat_schema.py`

- [ ] **Step 1: 写 mode 默认值、409 预检和失联收敛失败测试**

```python
import asyncio

import pytest

from app.application.errors.exceptions import ConflictError
from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.domain.models.event import MessageEvent, TaskGraphEvent, TeamTaskEvent
from app.domain.models.session import Session, SessionStatus
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
)
from app.domain.services.team.graph import build_task_graph
from app.interfaces.schemas.session import ChatRequest


class InMemorySessionRepository:
    def __init__(self, session):
        self.value = session
        self.persisted_events = []

    async def get_by_id(self, session_id):
        return self.value if self.value.id == session_id else None

    async def add_event(self, session_id, event):
        self.persisted_events.append(event)

    async def update_status(self, session_id, status):
        self.value.status = status


class FakeUow:
    def __init__(self, session):
        self.session = InMemorySessionRepository(session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class MissingTaskRegistry:
    @classmethod
    def get(cls, task_id):
        return None


def running_team_session():
    graph = build_task_graph(
        PlannedTaskGraph(
            title="team",
            goal="work",
            tasks=[PlannedTask(
                id="a",
                description="a",
                capability=TeamCapability.SEARCH,
                success_criteria="done",
            )],
        ),
        max_tasks=5,
    )
    graph.status = TaskGraphStatus.RUNNING
    graph.tasks[0].status = TeamTaskStatus.RUNNING
    return Session(
        id="session",
        task_id="missing-task",
        status=SessionStatus.RUNNING,
        events=[
            MessageEvent(role="user", message="go", agent_mode=AgentMode.TEAM),
            TaskGraphEvent(graph=graph.model_copy(deep=True)),
            TeamTaskEvent(
                graph_id=graph.id,
                task=graph.tasks[0].model_copy(deep=True),
                agent_id="worker-1",
                attempt=1,
            ),
        ],
    )


def test_chat_request_defaults_to_react():
    assert ChatRequest(message="x").mode is AgentMode.REACT


def test_running_team_session_rejects_new_message():
    async def scenario():
        session = running_team_session()
        uow = FakeUow(session)
        service = object.__new__(AgentService)
        service._uow = uow
        with pytest.raises(ConflictError):
            await service.validate_chat_request(
                "session",
                AgentMode.TEAM,
                has_message=True,
            )

    asyncio.run(scenario())


def test_missing_task_registry_marks_team_graph_interrupted():
    async def scenario():
        stored = running_team_session()
        uow = FakeUow(stored)
        service = SessionService(
            uow_factory=lambda: uow,
            sandbox_cls=object,
            task_cls=MissingTaskRegistry,
        )
        session = await service.get_session("session")
        graph = session.get_latest_task_graph()
        assert session.status is SessionStatus.COMPLETED
        assert graph.status is TaskGraphStatus.FAILED
        assert graph.error == "process_interrupted"
        assert graph.task_by_id("a").status is TeamTaskStatus.FAILED
        assert [event.type for event in uow.session.persisted_events] == [
            "task",
            "task_graph",
        ]

    asyncio.run(scenario())
```

在 `api/tests/app/interfaces/endpoints/test_team_chat_schema.py` 直接调用路由函数，证明拒绝发生在 `EventSourceResponse` 创建之前：

```python
import asyncio

import pytest

from app.application.errors.exceptions import ConflictError
from app.domain.models.team import AgentMode
from app.interfaces.endpoints.session_routes import chat
from app.interfaces.schemas.session import ChatRequest


class RejectingAgentService:
    def __init__(self):
        self.validated = []

    async def validate_chat_request(self, session_id, mode, has_message):
        self.validated.append((session_id, mode, has_message))
        raise ConflictError("Team 运行中不接受新消息")

    async def chat(self, **kwargs):
        raise AssertionError("SSE generator must not start after a conflict")
        yield


def test_chat_route_preflights_conflict_before_sse_response():
    async def scenario():
        service = RejectingAgentService()
        with pytest.raises(ConflictError):
            await chat(
                "session",
                ChatRequest(message="new", mode=AgentMode.TEAM),
                service,
            )
        assert service.validated == [("session", AgentMode.TEAM, True)]

    asyncio.run(scenario())
```

- [ ] **Step 2: 取得许可后运行测试并确认失败**

Run: `cd api && pytest tests/app/application/services/test_team_session_rules.py tests/app/interfaces/endpoints/test_team_chat_schema.py -q`

Expected: FAIL，mode/ConflictError/reconcile 尚未实现。

- [ ] **Step 3: 增加 ConflictError 和 ChatRequest.mode**

```python
class ConflictError(AppException):
    def __init__(self, msg: str = "资源状态冲突"):
        super().__init__(status_code=409, code=409, msg=msg)


class ChatRequest(BaseModel):
    message: Optional[str] = None
    attachments: Optional[List[str]] = Field(default_factory=list)
    event_id: Optional[str] = None
    timestamp: Optional[int] = None
    mode: AgentMode = AgentMode.REACT
```

- [ ] **Step 4: 在 Session 上实现事件投影**

```python
def get_latest_agent_mode(self) -> AgentMode:
    for event in reversed(self.events):
        if isinstance(event, MessageEvent) and event.role == "user":
            return event.agent_mode or AgentMode.REACT
    return AgentMode.REACT

def get_latest_task_graph(self) -> Optional[TaskGraph]:
    graph = None
    for event in self.events:
        if isinstance(event, TaskGraphEvent):
            graph = event.graph.model_copy(deep=True)
        elif isinstance(event, TeamTaskEvent) and graph and event.graph_id == graph.id:
            for index, task in enumerate(graph.tasks):
                if task.id == event.task.id:
                    graph.tasks[index] = event.task.model_copy(deep=True)
                    break
    return graph
```

- [ ] **Step 5: 在创建 SSE 响应前执行 409 预检并传递 mode**

```python
# AgentService
async def validate_chat_request(
    self,
    session_id: str,
    mode: AgentMode,
    has_message: bool,
) -> None:
    if not has_message:
        return
    async with self._uow:
        session = await self._uow.session.get_by_id(session_id)
    if (
        session
        and session.status is SessionStatus.RUNNING
        and session.get_latest_agent_mode() is AgentMode.TEAM
    ):
        raise ConflictError("Team 运行中不接受新消息；请先停止当前任务")


# route, before EventSourceResponse
await agent_service.validate_chat_request(session_id, request.mode, bool(request.message))

# pass mode into agent_service.chat inside event_generator
async for event in agent_service.chat(
    session_id=session_id,
    message=request.message,
    attachments=request.attachments,
    latest_event_id=request.event_id,
    timestamp=datetime.fromtimestamp(request.timestamp) if request.timestamp else None,
    mode=request.mode,
):
    sse_event = EventMapper.event_to_sse_event(event)
    if sse_event:
        yield ServerSentEvent(
            event=sse_event.event,
            data=sse_event.data.model_dump_json(),
        )

# AgentService.chat adds `mode: AgentMode = AgentMode.REACT` and creates user event
message_event = MessageEvent(
    role="user",
    message=message,
    attachments=[attachment for attachment in db_attachments if attachment is not None],
    agent_mode=mode,
)
```

`validate_chat_request` 仅在有新 message、Session 为 running 且最近 mode 为 team 时抛 `ConflictError`；空 body 的事件续订不拦截。

- [ ] **Step 6: 实现 SessionService 懒惰中断收敛**

`SessionService` 新增 `task_cls: Type[Task]`。`get_session` 和 `get_all_sessions` 对每个 Session 调用：

```python
async def _reconcile_interrupted_team(self, session: Session) -> Session:
    if session.status is not SessionStatus.RUNNING:
        return session
    if session.get_latest_agent_mode() is not AgentMode.TEAM:
        return session
    if session.task_id and self._task_cls.get(session.task_id):
        return session
    graph = session.get_latest_task_graph()
    if not graph:
        return session
    terminal_events = []
    for task in graph.tasks:
        if task.status in {TeamTaskStatus.RUNNING, TeamTaskStatus.RETRYING}:
            task.status = TeamTaskStatus.FAILED
            task.error = "process_interrupted"
            terminal_events.append(TeamTaskEvent(graph_id=graph.id, task=task, attempt=task.attempt_count))
        elif task.status is TeamTaskStatus.PENDING:
            task.status = TeamTaskStatus.SKIPPED
            task.error = "process_interrupted"
            terminal_events.append(TeamTaskEvent(graph_id=graph.id, task=task, attempt=task.attempt_count))
    graph.status = TaskGraphStatus.FAILED
    graph.error = "process_interrupted"
    terminal_events.append(TaskGraphEvent(graph=graph))
    async with self._uow:
        for event in terminal_events:
            await self._uow.session.add_event(session.id, event)
        await self._uow.session.update_status(session.id, SessionStatus.COMPLETED)
    session.events.extend(terminal_events)
    session.status = SessionStatus.COMPLETED
    return session
```

- [ ] **Step 7: 更新依赖注入**

```python
def get_session_service() -> SessionService:
    return SessionService(
        uow_factory=get_uow,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
    )
```

- [ ] **Step 8: 取得许可后运行 API/Application 测试**

Run: `cd api && pytest tests/app/application/services/test_team_session_rules.py tests/app/interfaces/endpoints/test_team_chat_schema.py -q`

Expected: PASS；HTTP 预检发生在 SSE 创建前，失联 Team Session 收敛为 failed/completed。

- [ ] **Step 9: 提交 API 接入**

```bash
git add api/app/application/errors/exceptions.py api/app/application/services/agent_service.py api/app/application/services/session_service.py api/app/domain/models/session.py api/app/interfaces/schemas/session.py api/app/interfaces/endpoints/session_routes.py api/app/interfaces/service_dependencies.py api/tests/app/application/services/test_team_session_rules.py api/tests/app/interfaces/endpoints/test_team_chat_schema.py
git commit -m "feat: expose team mode through chat API"
```

### Task 10: 建立前端 Team 类型、API 和事件投影

**Files:**

- Modify: `ui/package.json`
- Modify: `ui/package-lock.json`
- Create: `ui/vitest.config.ts`
- Create: `ui/src/test/setup.ts`
- Modify: `ui/src/lib/api/types.ts:1-307`
- Modify: `ui/src/lib/api/session.ts:118-198`
- Modify: `ui/src/lib/session-events.ts:1-321`
- Test: `ui/src/lib/__tests__/session-events.test.ts`

- [ ] **Step 1: 增加纯前端测试依赖和脚本**

Run after implementation authorization: `cd ui && npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom`

Expected file changes: `package.json` 和 `package-lock.json`；不启动服务。

在 scripts 增加：

```json
"test": "vitest run"
```

```typescript
// ui/vitest.config.ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] },
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
});
```

```typescript
// ui/src/test/setup.ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 2: 写 Team Event 投影失败测试**

```typescript
import { describe, expect, it } from "vitest";
import { getLatestTeamProjection } from "@/lib/session-events";
import type { SSEEventData, TaskGraph } from "@/lib/api/types";

const graphFixture: TaskGraph = {
  id: "g",
  title: "research",
  goal: "collect",
  status: "pending",
  tasks: [
    {
      id: "a",
      description: "collect",
      dependencies: [],
      capability: "search",
      success_criteria: "done",
      status: "pending",
      attempt_count: 0,
    },
  ],
};

describe("getLatestTeamProjection", () => {
  it("updates tasks and groups tools by task_id", () => {
    const events = [
      { type: "task_graph", data: { graph: graphFixture } },
      {
        type: "task",
        data: {
          graph_id: "g",
          task: { ...graphFixture.tasks[0], status: "running" },
          attempt: 1,
        },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "c",
          name: "search",
          function: "search_web",
          args: {},
          status: "calling",
          task_id: "a",
        },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "c",
          name: "search",
          function: "search_web",
          args: {},
          status: "called",
          task_id: "a",
        },
      },
    ] as SSEEventData[];
    const projection = getLatestTeamProjection(events);
    expect(projection?.graph.tasks[0].status).toBe("running");
    expect(projection?.toolsByTask.a).toHaveLength(1);
    expect(projection?.toolsByTask.a[0].status).toBe("called");
  });
});
```

- [ ] **Step 3: 取得许可后运行测试并确认失败**

Run: `cd ui && npm test -- src/lib/__tests__/session-events.test.ts`

Expected: FAIL，Team 类型或投影函数不存在。

- [ ] **Step 4: 增加 TypeScript 类型和 Chat mode**

```typescript
export type AgentMode = 'react' | 'team'
export type TeamCapability = 'analysis' | 'search' | 'browser' | 'file_read' | 'file_write' | 'shell' | 'mcp' | 'a2a'
export type TeamTaskStatus = 'pending' | 'running' | 'retrying' | 'completed' | 'failed' | 'skipped' | 'cancelled'
export type TaskGraphStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled'
export type SourceRef = { title: string; url: string; snippet?: string | null }
export type WorkerResult = { success: boolean; summary: string; sources: SourceRef[]; artifacts: string[]; notes: string[] }
export type TeamTask = {
  id: string; description: string; dependencies: string[]; capability: TeamCapability;
  success_criteria: string; status: TeamTaskStatus; assigned_agent_id?: string | null;
  attempt_count: number; result?: WorkerResult | null; error?: string | null;
}
export type TaskGraph = { id: string; title: string; goal: string; tasks: TeamTask[]; status: TaskGraphStatus; error?: string | null }

// ChatParams
mode?: AgentMode

// ToolEvent
tool_call_id?: string
graph_id?: string
task_id?: string
agent_id?: string
attempt?: number

// SSE union additions
| { type: 'task_graph'; data: { graph: TaskGraph; event_id?: string } }
| { type: 'task'; data: { graph_id: string; task: TeamTask; agent_id?: string | null; attempt: number } }
```

- [ ] **Step 5: 实现 Team Event reducer**

```typescript
export type TeamProjection = {
  graph: TaskGraph;
  toolsByTask: Record<string, ToolEvent[]>;
};

export function getLatestTeamProjection(
  events: SSEEventData[],
): TeamProjection | null {
  let graph: TaskGraph | null = null;
  const toolsByTask: Record<string, ToolEvent[]> = {};
  for (const event of events) {
    if (event.type === "task_graph") graph = structuredClone(event.data.graph);
    if (event.type === "task" && graph && event.data.graph_id === graph.id) {
      graph.tasks = graph.tasks.map((task) =>
        task.id === event.data.task.id ? event.data.task : task,
      );
    }
    if (event.type === "tool" && event.data.task_id) {
      const tools = toolsByTask[event.data.task_id] ?? [];
      const index = tools.findIndex(
        (tool) => tool.tool_call_id === event.data.tool_call_id,
      );
      if (index >= 0) tools[index] = event.data;
      else tools.push(event.data);
      toolsByTask[event.data.task_id] = tools;
    }
  }
  return graph ? { graph, toolsByTask } : null;
}
```

`eventsToTimeline` 遇到带 `task_id` 的 Tool 时不再添加独立 TimelineItem；无 task_id 的旧 Tool 继续使用 `lastStepId` fallback。

- [ ] **Step 6: 取得许可后运行前端 reducer 测试**

Run: `cd ui && npm test -- src/lib/__tests__/session-events.test.ts`

Expected: PASS；calling/called 按 tool_call_id 合并，旧 Tool fallback 测试仍通过。

- [ ] **Step 7: 提交前端协议**

```bash
git add ui/package.json ui/package-lock.json ui/vitest.config.ts ui/src/test/setup.ts ui/src/lib/api/types.ts ui/src/lib/api/session.ts ui/src/lib/session-events.ts ui/src/lib/__tests__/session-events.test.ts
git commit -m "feat: add team event projection to UI"
```

### Task 11: 实现模式选择、Team 任务面板并完成纵向验证

**Files:**

- Create: `ui/src/components/agent-mode-selector.tsx`
- Create: `ui/src/components/team-task-panel.tsx`
- Modify: `ui/src/components/chat-input.tsx:11-260`
- Modify: `ui/src/components/session-detail-view.tsx:20-330`
- Modify: `ui/src/hooks/use-session-detail.ts:10-340`
- Modify: `ui/src/app/page.tsx:1-73`
- Modify: `ui/src/app/sessions/[id]/page.tsx:1-72`
- Test: `ui/src/components/__tests__/agent-mode-selector.test.tsx`
- Test: `ui/src/components/__tests__/team-task-panel.test.tsx`
- Modify: `README.md`
- Modify: `api/README.md`

- [ ] **Step 1: 写模式选择和面板失败测试**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentModeSelector } from "@/components/agent-mode-selector";

it("switches from react to team", () => {
  const onChange = vi.fn();
  render(
    <AgentModeSelector value="react" onChange={onChange} disabled={false} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "多 Agent" }));
  expect(onChange).toHaveBeenCalledWith("team");
});
```

`ui/src/components/__tests__/team-task-panel.test.tsx` 使用完整投影覆盖节点详情和工具点击：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TeamTaskPanel } from "@/components/team-task-panel";
import type { ToolEvent } from "@/lib/api/types";
import type { TeamProjection } from "@/lib/session-events";

it("renders DAG task details and forwards nested tool clicks", () => {
  const tool: ToolEvent = {
    tool_call_id: "call-1",
    name: "search",
    function: "search_web",
    args: { query: "multi agent DAG" },
    status: "called",
    task_id: "collect",
  };
  const projection: TeamProjection = {
    graph: {
      id: "graph-1",
      title: "多 Agent 调研",
      goal: "形成结论",
      status: "running",
      tasks: [
        {
          id: "collect",
          description: "收集资料",
          dependencies: [],
          capability: "search",
          success_criteria: "至少一个来源",
          status: "running",
          assigned_agent_id: "worker-1",
          attempt_count: 1,
          result: {
            success: true,
            summary: "已找到资料",
            sources: [
              { title: "官方资料", url: "https://example.com/source" },
            ],
            artifacts: ["/sandbox/report.md"],
            notes: [],
          },
        },
        {
          id: "summarize",
          description: "整理结论",
          dependencies: ["collect"],
          capability: "analysis",
          success_criteria: "形成结论",
          status: "failed",
          assigned_agent_id: "worker-2",
          attempt_count: 2,
          error: "boom",
        },
      ],
    },
    toolsByTask: { collect: [tool] },
  };
  const onToolClick = vi.fn();

  render(
    <TeamTaskPanel projection={projection} onToolClick={onToolClick} />,
  );

  expect(screen.getByText("收集资料")).toBeInTheDocument();
  expect(screen.getByText("依赖：collect")).toBeInTheDocument();
  expect(screen.getByText("Worker：worker-2")).toBeInTheDocument();
  expect(screen.getByText("尝试：2")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("boom");
  expect(screen.getByRole("link", { name: "官方资料" })).toHaveAttribute(
    "href",
    "https://example.com/source",
  );
  expect(screen.getByText("/sandbox/report.md")).toBeInTheDocument();

  fireEvent.click(
    screen.getByRole("button", { name: /正在搜索 multi agent DAG/ }),
  );
  expect(onToolClick).toHaveBeenCalledWith(tool);
});
```

- [ ] **Step 2: 取得许可后运行组件测试并确认失败**

Run: `cd ui && npm test -- src/components/__tests__`

Expected: FAIL，两个组件不存在。

- [ ] **Step 3: 实现 AgentModeSelector**

```tsx
export function AgentModeSelector({
  value,
  onChange,
  disabled,
}: {
  value: AgentMode;
  onChange: (mode: AgentMode) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex rounded-full border p-0.5" aria-label="Agent 模式">
      {(
        [
          ["react", "单 Agent"],
          ["team", "多 Agent"],
        ] as const
      ).map(([mode, label]) => (
        <Button
          key={mode}
          type="button"
          size="sm"
          variant={value === mode ? "default" : "ghost"}
          disabled={disabled}
          onClick={() => onChange(mode)}
          aria-pressed={value === mode}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: 实现 TeamTaskPanel**

组件按 Graph 中原始顺序渲染 Task，复用现有 `ToolUse`，不实现连线画布：

```tsx
"use client";

import { Badge } from "@/components/ui/badge";
import { ToolUse } from "@/components/tool-use";
import type {
  TaskGraphStatus,
  TeamTaskStatus,
  ToolEvent,
} from "@/lib/api/types";
import type { TeamProjection } from "@/lib/session-events";

const TASK_STATUS: Record<
  TeamTaskStatus,
  {
    label: string;
    variant: "default" | "secondary" | "destructive" | "outline";
  }
> = {
  pending: { label: "等待中", variant: "outline" },
  running: { label: "运行中", variant: "default" },
  retrying: { label: "重试中", variant: "secondary" },
  completed: { label: "已完成", variant: "secondary" },
  failed: { label: "失败", variant: "destructive" },
  skipped: { label: "已跳过", variant: "outline" },
  cancelled: { label: "已取消", variant: "outline" },
};

const GRAPH_STATUS: Record<TaskGraphStatus, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

export function TeamTaskPanel({
  projection,
  onToolClick,
}: {
  projection: TeamProjection;
  onToolClick?: (tool: ToolEvent) => void;
}) {
  const { graph, toolsByTask } = projection;

  return (
    <section
      aria-label="多 Agent 任务图"
      className="mb-2 rounded-xl border bg-white p-4"
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-medium text-gray-900">{graph.title}</h3>
          <p className="mt-1 text-sm text-gray-500">{graph.goal}</p>
        </div>
        <Badge variant="outline">{GRAPH_STATUS[graph.status]}</Badge>
      </header>

      <div className="space-y-3">
        {graph.tasks.map((task) => {
          const status = TASK_STATUS[task.status];
          const tools = toolsByTask[task.id] ?? [];
          return (
            <article key={task.id} className="rounded-lg bg-gray-50 p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-gray-400">
                    {task.id}
                  </div>
                  <div className="mt-0.5 text-sm font-medium text-gray-800">
                    {task.description}
                  </div>
                </div>
                <Badge variant={status.variant}>{status.label}</Badge>
              </div>

              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500">
                <span>能力：{task.capability}</span>
                <span>
                  {task.dependencies.length > 0
                    ? `依赖：${task.dependencies.join(", ")}`
                    : "无依赖"}
                </span>
                {task.assigned_agent_id && (
                  <span>Worker：{task.assigned_agent_id}</span>
                )}
                <span>尝试：{task.attempt_count}</span>
              </div>

              {task.error && (
                <p role="alert" className="mt-2 text-sm text-red-600">
                  错误：{task.error}
                </p>
              )}

              {tools.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {tools.map((tool, index) => (
                    <ToolUse
                      key={tool.tool_call_id ?? `${task.id}-${index}`}
                      data={tool}
                      onClick={
                        onToolClick ? () => onToolClick(tool) : undefined
                      }
                    />
                  ))}
                </div>
              )}

              {(task.result?.sources.length ?? 0) > 0 && (
                <div className="mt-3 flex flex-wrap gap-2 text-sm">
                  {task.result?.sources.map((source) => (
                    <a
                      key={source.url}
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 underline"
                    >
                      {source.title}
                    </a>
                  ))}
                </div>
              )}

              {(task.result?.artifacts.length ?? 0) > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-gray-600">
                  {task.result?.artifacts.map((artifact) => (
                    <li key={artifact}>
                      <code>{artifact}</code>
                    </li>
                  ))}
                </ul>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
```

- [ ] **Step 5: 把 mode 串过新会话和详情页**

```typescript
// useSessionDetail result/signature
sendMessage: (message: string, attachmentIds: string[], mode: AgentMode) => Promise<void>

// request
{ message, attachments: attachmentIds, mode }

// home initial payload
const payload = JSON.stringify({ message, attachments, mode })

// detail page decode default
const { message, attachments, mode = 'react' } = JSON.parse(decoded)
```

`SessionDetailView` 持有 `const [mode, setMode] = useState<AgentMode>(initialMode ?? 'react')`；`ChatInput` 接收 `mode/onModeChange`；运行中禁用切换；TeamProjection 存在时显示 `TeamTaskPanel`，否则显示旧 `PlanPanel`。

- [ ] **Step 6: 把 Team Tool 纳入自动预览**

`findLatestTool` 和 Tool 数量计算同时扫描 `projection.toolsByTask`；`resolvedPreviewTool` 先按 `tool_call_id` 搜索 Team Tool，再回退旧 Timeline，保证 Browser/Shell/File 预览行为不丢失。

- [ ] **Step 7: 取得许可后运行组件、reducer、lint 和类型检查**

Run: `cd ui && npm test`

Expected: PASS，所有 Vitest 测试通过。

Run: `cd ui && npm run lint`

Expected: exit 0，无 ESLint error。

Run: `cd ui && npx tsc --noEmit`

Expected: exit 0，无 TypeScript error。

- [ ] **Step 8: 更新用户文档**

在 `README.md` 的功能说明和 `api/README.md` 的 Chat API 说明中加入同一段明确约束：

```markdown
### 多 Agent Team 模式

聊天请求的 `mode` 默认为 `react`；显式传入 `team` 才会启用动态 DAG 多 Agent 编排。Team 模式最多生成 5 个任务节点，同时运行不超过 3 个 Worker，每个失败节点最多重试 1 次。

`analysis`、`search`、`file_read` 节点允许并行；Browser、FileWrite、Shell、MCP、A2A 节点在首期串行独占共享运行环境。Team 运行期间只允许停止，不接受追加消息。

首期不支持 API 进程重启后的任务续跑；系统会在下次读取会话时把失联运行标记为失败。该功能不要求修改 `.env` 或 `api/config.yaml`。
```

- [ ] **Step 9: 取得许可后执行后端定向测试和全量测试**

Run: `cd api && pytest tests/app/domain/models/test_team.py tests/app/domain/services/team tests/app/domain/services/agents/test_team_agents.py tests/app/domain/services/flows/test_team_flow.py tests/app/domain/services/test_agent_task_runner_modes.py tests/app/application/services/test_team_session_rules.py tests/app/interfaces/schemas/test_team_events.py tests/app/interfaces/endpoints/test_team_chat_schema.py -q`

Expected: PASS，所有 Team 定向测试通过。

Run: `cd api && pytest -q`

Expected: PASS；若现有真实 OSS/Docker 测试依赖外部环境，先单独报告其环境失败，不通过修改 Team 代码掩盖。

- [ ] **Step 10: 检查不应发生的改动**

```bash
git status --short
git diff -- .env api/.env api/config.yaml
```

Expected: `.env`、`api/.env`、`api/config.yaml` 无差异；不存在构建产物或测试临时文件被暂存。

- [ ] **Step 11: 提交 UI 和文档**

```bash
git add ui/src/components/agent-mode-selector.tsx ui/src/components/team-task-panel.tsx ui/src/components/chat-input.tsx ui/src/components/session-detail-view.tsx ui/src/hooks/use-session-detail.ts ui/src/app/page.tsx 'ui/src/app/sessions/[id]/page.tsx' ui/src/components/__tests__ README.md api/README.md
git commit -m "feat: expose team DAG orchestration in UI"
```

## 计划自审清单

### 规格覆盖

- 动态 1–5 节点 DAG：Task 1、2、6、7。
- 最多 3 Worker、只读并行、状态工具独占：Task 3、5。
- 全部现有操作能力：Task 3、6、8。
- Worker 临时 Memory 与双层工具鉴权：Task 3。
- 一次重试、依赖跳过、partial/failed/cancelled：Task 2、5、7。
- 来源 URL 防伪与最终链接限制：Task 6。
- 单一事件写入、ToolEvent 确认和准确归属：Task 4、7、8。
- mode 默认兼容、运行冲突、进程中断收敛：Task 8、9。
- UI 模式选择、列表 DAG、刷新恢复和旧 Step fallback：Task 10、11。
- 不改运行配置：执行约束和 Task 11 检查；运行与容器验证按用户后续授权执行。

### 类型一致性

- API/Domain/UI 均只使用 `react | team`。
- Domain/SSE/UI 事件名统一为 `task_graph` 和 `task`。
- Tool 关联字段统一为 `graph_id/task_id/agent_id/attempt`。
- Planner 输出 `PlannedTaskGraph`，运行时使用 `TaskGraph`，LLM 不能写状态字段。
- Worker 最终输出 `WorkerResult`，Synthesizer 输出 `FinalTeamResponse`。
- Graph 终态只包含 completed/partial/failed/cancelled；进程中断使用 failed + `process_interrupted`。

## 执行交接

实际执行使用 `feature/team-dag-orchestration` 分支且不创建 worktree；测试、lint、类型检查、构建、容器和本地验证已获用户统一授权。仍不修改任何 `.env` 或 `api/config.yaml`。
