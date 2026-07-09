# TeamFlow 多 Agent 设计方案

## 目标

为 mooc-manus 增加第一版可落地的 multi-agent 执行模式。这个模式使用中心编排、DAG 任务图和本地同构 Worker 池，让多个相互独立的任务可以受控并行执行。

现有 `PlannerReActFlow` 继续作为默认单执行 Agent 流程。新的 TeamFlow 通过聊天请求中的 `mode="team"` 显式启用，不自动替换现有行为。

## 第一版范围

第一版只做这些能力：

- 本地同构 Worker 池。
- TeamPlannerAgent 生成带依赖关系的 DAG 任务图。
- 对安全任务类型做受控并行。
- 对每个任务做 Reviewer 审查和有限重试。
- 最后由 SynthesizerAgent 汇总结果。
- SSE 事件中显式暴露 `task_id`、`agent_id`、`agent_profile`，让前端能正确展示并行任务。

A2A 在第一版中仍然作为工具存在，不作为可被调度的 RemoteAgent。也就是说，Worker 可以调用 A2A 工具，但 Orchestrator 不会把某个任务直接派给远程 A2A Agent。

## 非目标

第一版明确不做：

- 不替换现有 `PlannerReActFlow`。
- 不自动判断是否使用 team mode。
- 不创建动态专家 Agent，例如固定的 `BrowserAgent`、`CodingAgent`、`ResearchAgent`。
- 不把 A2A 远程 Agent 纳入调度。
- 不默认并行写文件、Shell、浏览器改写、MCP、A2A 任务。
- 不做完整 DAG 图形化可视化。
- 不加入长期记忆、代码 RAG、LSP。
- 不做复杂的 HITL 审批系统，但设计要给后续审批预留位置。

## 当前架构上下文

当前 mooc-manus 的执行链路是：

```text
Session route
  -> AgentService
  -> AgentTaskRunner
  -> PlannerReActFlow
  -> PlannerAgent
  -> ReActAgent
  -> ToolEvent / MessageEvent / DoneEvent
```

当前实现有几个关键特点：

- `PlannerReActFlow` 是线性的：通过 `Plan.get_next_step()` 找下一个未完成步骤。
- `BaseAgent._invoke_llm()` 会把模型返回的 `tool_calls` 截断为 `tool_calls[:1]`，所以单个 ReActAgent 一次只执行一个工具调用。
- 前端时间线目前通过“最近活跃 step”把工具事件挂到 step 下。并行场景下这个逻辑会混乱，必须改为显式 `task_id` 归属。
- Session events 持久化为 JSONB，并通过 SSE 流式返回。
- `AgentTaskRunner` 负责沙箱初始化、MCP/A2A 初始化、附件同步、工具事件增强、文件同步到对象存储、会话状态更新。

TeamFlow 应该复用这些已有职责，不应该重新实现一套沙箱、文件同步或事件存储。

## 推荐架构

新增一个和 `PlannerReActFlow` 并列的 `TeamFlow`：

```text
ChatRequest(mode="team")
  -> AgentTaskRunner
  -> TeamFlow
  -> TeamPlannerAgent 创建 TaskGraph
  -> TeamOrchestrator 调度 ready tasks
  -> WorkerAgent 池执行任务
  -> ReviewerAgent 审查任务结果
  -> SynthesizerAgent 汇总最终回复
```

这里的 `TeamOrchestrator` 是代码编排层，不是 LLM Agent。它负责：

- 依赖关系判断。
- 并发控制。
- 资源锁。
- 重试。
- 超时。
- 状态流转。
- 事件输出。

LLM Agent 只负责需要模型推理的工作：

- `TeamPlannerAgent`：规划。
- `WorkerAgent`：执行单个任务。
- `ReviewerAgent`：审查单个任务。
- `SynthesizerAgent`：最终汇总。

## 文件设计

新增文件：

```text
api/app/domain/models/task_graph.py
api/app/domain/services/agents/team_planner.py
api/app/domain/services/agents/worker.py
api/app/domain/services/agents/reviewer.py
api/app/domain/services/agents/synthesizer.py
api/app/domain/services/flows/team.py
api/app/domain/services/flows/team_orchestrator.py
api/app/domain/services/flows/tool_policy.py
api/app/domain/services/prompts/team_planner.py
api/app/domain/services/prompts/worker.py
api/app/domain/services/prompts/reviewer.py
api/app/domain/services/prompts/synthesizer.py
```

需要修改的已有文件：

```text
api/app/domain/models/app_config.py
api/app/domain/models/event.py
api/app/domain/services/agents/base.py
api/app/domain/services/agent_task_runner.py
api/app/interfaces/schemas/event.py
api/app/interfaces/schemas/session.py
ui/src/lib/api/types.ts
ui/src/lib/session-events.ts
ui/src/components/session-detail-view.tsx
ui/src/components/plan-panel.tsx
ui/src/components/tool-use/index.tsx
```

第一版暂时不新增 `AgentRegistry`。原因是第一版没有 RemoteAgent 调度，本地 worker 是同构池，用配置控制数量即可。`AgentRegistry` 留到第二版接 A2A RemoteAgent 时再做。

## 请求模式

扩展 `ChatRequest`，增加显式模式：

```python
class AgentMode(str, Enum):
    REACT = "react"
    TEAM = "team"


class ChatRequest(BaseModel):
    message: Optional[str] = None
    attachments: Optional[List[str]] = Field(default_factory=list)
    event_id: Optional[str] = None
    timestamp: Optional[int] = None
    mode: AgentMode = AgentMode.REACT
```

第一版不做 `auto`。原因是自动模式选择会让行为不透明，也会增加调试成本。用户或 UI 必须明确选择 team mode。

## Agent 配置

扩展 `AgentConfig`：

```python
class AgentConfig(BaseModel):
    max_iterations: int = Field(default=100, gt=0, lt=1000)
    max_retries: int = Field(default=3, gt=1, lt=10)
    max_search_results: int = Field(default=10, gt=1, lt=30)
    team_enabled: bool = False
    max_workers: int = Field(default=3, ge=1, le=8)
    max_parallel_tasks: int = Field(default=3, ge=1, le=8)
    max_task_retries: int = Field(default=1, ge=0, le=3)
    team_task_timeout_seconds: int = Field(default=300, ge=30, le=1800)
```

默认 `team_enabled=False`，方便灰度。如果用户请求 `mode="team"` 但功能未启用，后端返回 `ErrorEvent`，不执行 TeamFlow。

## TaskGraph 模型

新增 `api/app/domain/models/task_graph.py`。

核心模型：

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEWING = "reviewing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskProfile(str, Enum):
    RESEARCH = "research"
    BROWSER = "browser"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    ANALYSIS = "analysis"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskReview(BaseModel):
    approved: bool
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TaskNode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: Optional[str] = None
    description: str
    profile: TaskProfile = TaskProfile.ANALYSIS
    dependencies: List[str] = Field(default_factory=list)
    parallelizable: bool = True
    allowed_tools: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    review: Optional[TaskReview] = None
    retry_count: int = 0


class TaskGraph(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    goal: str = ""
    language: str = ""
    tasks: List[TaskNode] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    message: str = ""
```

后端必须做图校验：

- 每个 dependency 必须引用存在的 task ID。
- 图不能有环。
- 至少存在一个 task。
- task ID 在图内必须唯一。
- `allowed_tools` 必须是该 `profile` 允许的工具子集。

Planner 可以输出 `task_1`、`task_2` 这类短 ID。后端只要求图内唯一，不要求 UUID。

## 工具策略

新增 `tool_policy.py`，统一管理 profile 到工具白名单、并发策略的映射。

第一版默认策略：

```text
research:
  allowed: search_web, browser_navigate, browser_view
  parallel: yes

file_read:
  allowed: read_file, search_in_file, find_files
  parallel: yes

analysis:
  allowed: none
  parallel: yes

browser:
  allowed: browser_view, browser_navigate, browser_click, browser_input,
           browser_scroll_up, browser_scroll_down, browser_press_key,
           browser_select_option
  parallel: no

file_write:
  allowed: write_file, replace_in_file
  parallel: no

shell:
  allowed: shell_execute, shell_read_output, shell_wait_process,
           shell_write_input, shell_kill_process
  parallel: no
```

MCP 和 A2A 第一版不进入默认并行白名单。如果 planner 生成了 MCP/A2A 工具使用，TeamFlow 默认把这个 task 视为不可并行，除非后续版本为工具增加明确的副作用元数据。

工具权限要做两层校验：

1. 暴露 schema 时，只把 `allowed_tools` 中的工具 schema 给模型。
2. 执行工具时，再检查一次工具名是否在 `allowed_tools` 中。

第二层校验很重要，因为模型可能伪造一个没有暴露的 tool call。

## 同构 Worker 池

Worker 数量由配置控制，例如：

```text
max_workers = 3
max_parallel_tasks = 3
max_task_retries = 1
```

Worker 本身同构：

```text
worker-1
worker-2
worker-3
```

它们都使用同一个 `WorkerAgent` 类和同一套 Worker prompt。区别只在每次执行任务时注入的 profile、工具白名单和上下文不同。

示例：

```text
Agent ID: worker-2
Execution profile: research
Allowed tools: search_web, browser_navigate, browser_view
Current task: 调研目标网站上的价格信息
Dependency results: ...
```

同一个 `worker-2` 上一轮可以执行 `research` 任务，下一轮可以执行 `file_read` 任务。worker 没有永久专家身份。

## Agent 职责

### TeamPlannerAgent

职责：

- 继承 `BaseAgent`。
- `tool_choice="none"`。
- 只输出严格的 `TaskGraph` JSON。
- 不调用工具。
- 为每个 task 输出 dependencies、profile、parallelizable、allowed_tools、risk_level。
- 任务粒度要适中：能独立执行，但不能碎到过度消耗调度和 token。

### WorkerAgent

职责：

- 继承 `BaseAgent`。
- 一次只执行一个 `TaskNode`。
- 接收当前任务、原始用户目标、依赖结果、附件路径、profile、allowed_tools。
- 只暴露当前任务允许的工具。
- 输出结构化 JSON：

```json
{
  "success": true,
  "result": "任务执行结果文本",
  "artifacts": [],
  "notes": []
}
```

### ReviewerAgent

职责：

- 审查单个 task 的结果。
- 第一版默认不使用写工具。
- 可选择允许只读工具，例如读取文件、查看浏览器当前状态、读取 shell 输出。
- 输出严格 JSON：

```json
{
  "approved": true,
  "issues": [],
  "suggestions": [],
  "confidence": 0.82
}
```

如果 Reviewer 输出无法解析为 JSON，TeamFlow 保守处理为不通过。如果还有重试次数，则把解析失败作为反馈交给 Worker 重试。

### SynthesizerAgent

职责：

- 根据原始目标、TaskGraph、已完成任务结果、失败/跳过任务、附件产物生成最终回复。
- 不继续执行任务。
- 不调用有副作用工具。

## TeamFlow 职责

`TeamFlow` 是 `AgentTaskRunner` 调用的新 flow。

职责：

- 检查 `agent_config.team_enabled`。
- 调用 `TeamPlannerAgent` 创建 TaskGraph。
- 校验 TaskGraph。
- 发送 `TaskGraphEvent`。
- 创建并运行 `TeamOrchestrator`。
- 流式输出 task、tool、review、retry、message、error、done 事件。
- 所有 task 终态后调用 `SynthesizerAgent`。

`TeamFlow` 不应该包含底层调度细节。底层调度放在 `TeamOrchestrator`。

## TeamOrchestrator 调度逻辑

Orchestrator 循环直到所有任务进入终态：

```text
1. 找 ready tasks：
   status == pending，并且所有 dependencies 都是 completed。

2. 如果某个 pending task 的依赖已经 failed/skipped：
   将该 task 标记为 skipped。

3. 将 ready tasks 分组：
   可安全并行：research、file_read、analysis。
   必须串行：browser、file_write、shell、MCP/A2A。

4. 对安全任务批次做受控并行：
   并发数 = min(max_workers, max_parallel_tasks, ready_safe_task_count)

5. 对串行任务按资源锁逐个执行。

6. 每个 task 执行完成后进入 review。

7. review 不通过则 retry。

8. retry 超限则 failed。
```

建议并发原语：

```python
worker_semaphore = asyncio.Semaphore(agent_config.max_workers)
parallel_task_semaphore = asyncio.Semaphore(agent_config.max_parallel_tasks)
browser_lock = asyncio.Lock()
file_write_lock = asyncio.Lock()
shell_lock = asyncio.Lock()
external_tool_lock = asyncio.Lock()
```

第一版使用全局锁，不做 path-level file lock。后续如果需要，可以再对 `file_write` 增加基于路径的锁。

## 事件模型

新增领域事件：

```python
class TaskGraphEvent(BaseEvent):
    type: Literal["task_graph"] = "task_graph"
    graph: TaskGraph
    status: Literal["created", "updated", "completed"] = "created"


class TaskEvent(BaseEvent):
    type: Literal["task"] = "task"
    task: TaskNode
    status: TaskStatus
    agent_id: Optional[str] = None
    agent_profile: Optional[str] = None


class TaskReviewEvent(BaseEvent):
    type: Literal["task_review"] = "task_review"
    task_id: str
    agent_id: str = "reviewer"
    review: TaskReview


class TaskRetryEvent(BaseEvent):
    type: Literal["task_retry"] = "task_retry"
    task_id: str
    retry_count: int
    feedback: str
```

扩展 `ToolEvent`，增加可选元数据：

```python
task_id: Optional[str] = None
agent_id: Optional[str] = None
agent_profile: Optional[str] = None
```

所有 Worker 发出的工具事件都必须带这些字段。并行执行时，不能再依赖“最近 step”判断工具归属。

## SSE Schema 和前端展示

后端 schema 需要新增：

- `task_graph` 事件数据。
- `task` 事件数据。
- `task_review` 事件数据。
- `task_retry` 事件数据。
- `ToolEventData` 增加 `task_id`、`agent_id`、`agent_profile`。

前端类型需要新增：

```text
SSEEventType:
  task_graph
  task
  task_review
  task_retry

类型:
  TaskGraph
  TaskNode
  TaskReview
```

前端时间线新增：

```text
TimelineItem kind:
  task
  task_review
  task_retry
```

工具归属规则：

```text
如果 tool.task_id 存在：
  挂到对应 task 下。
否则：
  走旧的 lastStepId 逻辑，兼容 PlannerReActFlow。
```

第一版 UI 不做完整 DAG 图。`plan-panel` 可以按拓扑顺序展示 task 列表，字段包括：

- task 描述。
- task 状态。
- assigned worker ID。
- profile。
- retry count。
- nested tools。
- review 结果。

## AgentTaskRunner 接入

当前 `AgentTaskRunner` 在构造时固定创建 `PlannerReActFlow`。TeamFlow 需要按请求模式选择。

推荐做法：在 `_run_flow(message, mode)` 中懒加载选择 flow：

```python
if mode == AgentMode.TEAM:
    flow = TeamFlow(...)
else:
    flow = self._planner_react_flow
```

原因：

- `AgentTaskRunner` 创建时还不知道当前输入消息的 mode。
- 不需要为每个 task runner 永远创建 TeamFlow。
- 保留现有 react 模式的行为。

`AgentTaskRunner` 仍然负责：

- sandbox 初始化。
- MCP/A2A 初始化。
- 工具事件增强。
- 文件同步到 sandbox。
- 文件同步到对象存储。
- 持久化 session events。
- 更新 session title/latest message/status。

## SharedContext

不要让所有 worker 共享完整对话历史。共享上下文应该是结构化的：

```text
original_user_goal
attachments
sandbox_upload_paths
task_results_by_id
important_tool_outputs
artifacts
failed_or_skipped_tasks
```

每个 Worker 只拿这些内容：

- 原始目标。
- 当前 task。
- 直接依赖 task 的结果。
- 相关附件。
- profile。
- allowed_tools。

这样可以降低 token 使用，减少不同任务之间的上下文污染。

## 失败处理

Planner 阶段：

- JSON 解析失败：发送 `ErrorEvent`，停止。
- TaskGraph 校验失败：发送 `ErrorEvent`，停止。
- 空 TaskGraph：发送 `ErrorEvent`，停止。

Task 阶段：

- Worker 执行异常：task 标记为 `failed`。
- Reviewer 不通过：task 标记为 `retrying`，带 feedback 重试。
- 重试耗尽：task 标记为 `failed`，如果有最后一次结果则保留在 `result`。
- 依赖失败或跳过：下游 task 标记为 `skipped`。

取消：

- 沿用现有 stop session 能力。
- 正在运行的 worker task 尽量发送终态 task 事件。
- Flow 结束时发送 `DoneEvent`。

超时：

- 每个 task 用 `asyncio.wait_for(..., team_task_timeout_seconds)` 包裹。
- 超时后 task 标记为 `failed`，错误信息明确写超时。

Reviewer JSON 解析失败：

- 默认视为不通过。
- feedback 写入：`Reviewer 输出无法解析为 review JSON，请重新执行并产生可审查结果。`
- 如果还有重试次数，则重试。

## 安全策略

第一版先做工具白名单和并发限制，不解决所有高风险操作。

安全规则：

- 只有 `research`、`file_read`、`analysis` 默认可并行。
- `file_write`、`shell`、`browser`、MCP、A2A 默认串行。
- 工具调用必须做运行时授权校验。
- `message_ask_user` 保留现有等待用户机制。
- 后续加入 HITL 审批后，再考虑放宽写文件、Shell、浏览器改写的并行能力。

## 落地阶段

### Phase 1：后端核心链路

- 新增 TaskGraph 模型。
- 新增 Team 相关事件。
- 新增 TeamPlannerAgent、WorkerAgent、ReviewerAgent、SynthesizerAgent。
- 新增 TeamFlow 和 TeamOrchestrator。
- 增加工具白名单过滤。
- Team mode 默认关闭。

### Phase 2：前端最小可视化

- 增加 team mode 请求参数。
- 增加 task/timeline 类型。
- 按 task_id 聚合工具事件。
- 在 plan panel 或 timeline 展示 worker、profile、task status、review/retry。

### Phase 3：增强能力

- A2A RemoteAgent 调度。
- AgentRegistry。
- 更完整 DAG 可视化。
- 更细粒度资源锁。
- 工具副作用元数据。
- HITL 审批。

## 测试策略

后端单元测试：

- TaskGraph 校验拒绝不存在的 dependency。
- TaskGraph 校验拒绝有环图。
- Tool policy 正确映射 profile 到 allowed tools。
- WorkerAgent 只暴露 allowed tools。
- 运行时调用未授权工具会被拒绝。
- Orchestrator 能正确识别 ready tasks。
- Orchestrator 能在 max_workers 限制下并行执行安全任务。
- Orchestrator 会串行执行 browser/file_write/shell。
- Reviewer 拒绝会触发 retry。
- retry 超限会将 task 标记为 failed。
- dependency failed 会让下游 task skipped。
- ToolEvent 会携带 task_id 和 agent_id。
- EventMapper 能把新增事件转换为 SSE。

后端集成测试：

- team mode 请求会产生 task_graph、task、task_review、message、done 事件。
- react mode 仍然使用现有 PlannerReActFlow。
- team_enabled=false 时，请求 team mode 返回 ErrorEvent 且不执行 worker。

前端测试：

- 新 SSE 事件类型能 normalize。
- 带 `task_id` 的 ToolEvent 会挂到对应 task。
- 没有 `task_id` 的旧 ToolEvent 仍使用 fallback 逻辑。
- task timeline 能展示 status、worker ID、profile、retry count。

## 验收标准

- 现有 react mode 行为不变。
- team mode 能创建 TaskGraph。
- 至少两个相互独立的 `research` 或 `file_read` task 可以并行执行。
- `browser`、`file_write`、`shell` task 不会并行执行。
- Worker 产生的 ToolEvent 都包含 `task_id` 和 `agent_id`。
- Reviewer 拒绝后会按配置触发 retry。
- 最终回复能说明 completed、failed、skipped tasks。
- 刷新 session detail 后，已持久化的 team events 能正确展示。
- `team_enabled=false` 时功能可以关闭。

