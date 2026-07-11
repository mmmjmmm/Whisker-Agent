# TeamFlow 动态 DAG 多 Agent 编排最小设计

**状态：** 已批准，待用户审阅书面规格

**日期：** 2026-07-11

**适用仓库：** `mooc-manus`
**首期范围：** 显式触发的本地多 Agent 动态 DAG 编排

## 1. 决策摘要

mooc-manus 将新增一个与现有 `PlannerReActFlow` 并列的 `TeamFlow`。用户通过聊天请求中的 `mode="team"` 显式启用它，默认 `mode="react"` 的行为保持不变。

首期采用以下架构：

- `TeamPlannerAgent` 使用结构化输出生成最多 5 个节点的动态 DAG。
- 确定性的 `TaskGraphValidator` 校验节点、依赖、环和能力类型。
- 普通 Python 组件 `TeamOrchestrator` 负责依赖调度、并发、独占任务、超时、取消和重试。
- 最多 3 个短生命周期同构 `TaskWorker` 并行执行 DAG 节点。
- Worker 是通用执行 Agent，实际工具由节点的 `capability` 和后端 `ToolPolicy` 决定。
- 所有现有操作类工具均可参与 DAG：Search、File、Browser、Shell、MCP 和 A2A。
- `analysis`、`search`、`file_read` 可并行；Browser、文件写入、Shell、MCP 和 A2A 首期独占执行。
- Worker 失败后重试一次；仍失败时保留其他成功结果并生成部分完成答案。
- `SynthesizerAgent` 汇总节点结果、来源、产物及失败项，不增加 Reviewer 循环。
- 并发 Worker 只把事件放入内存队列，由单一 Event Sink 顺序写入 Redis 和 PostgreSQL。
- 前端以任务列表展示 DAG，不实现图形化画布。
- 首期不恢复 API 进程重启时的未完成 DAG；下次读取相关 Session 时将其收敛为失败终态。

首期不引入 LangGraph、OpenAI Agents SDK、Google ADK、CrewAI 或其他完整 Agent 运行时。实现复用当前 Agent、Flow、Tool、TaskRunner、Event、Redis Stream、Session JSONB 和 SSE 抽象。

## 2. 背景与当前状态

当前执行链路是：

```text
Session route
  -> AgentService
  -> AgentTaskRunner
  -> PlannerReActFlow
  -> PlannerAgent
  -> ReActAgent
  -> Plan / Step / Tool / Message / Done events
```

当前系统已包含两个不同职责的 Agent，但执行结构仍是线性的：

- `PlannerAgent` 创建或更新顺序 `Plan`。
- `Plan.get_next_step()` 每次只返回一个未完成步骤。
- `ReActAgent` 连续执行所有步骤，并共享同一份 Session Memory。
- `BaseAgent._invoke_llm()` 将一次模型返回的工具调用截断为一个。
- 前端通过最近活跃的 Step 推断 Tool 归属。
- 一个 Session 共享一个 Sandbox、一个 Playwright 页面和一组 MCP/A2A 客户端。

因此，本功能的核心不是增加更多 Agent 类，而是增加一个真正理解依赖、能够安全并行、事件可正确归属的编排层。

## 3. 目标与非目标

### 3.1 产品目标

- 用户可以显式选择多 Agent 模式。
- Planner 根据实际请求生成动态 DAG，而不是固定角色流水线。
- 无依赖且安全的节点可以真实并行执行。
- 文件、浏览器、Shell、MCP 和 A2A 等现有能力不会因 Team 模式被移除。
- 有状态或有副作用的节点受到保守的独占调度保护。
- 每个 Worker 只拥有完成当前节点所需的最小工具权限。
- UI 能显示 DAG 节点、依赖、Worker、状态、重试和节点内工具调用。
- Worker 局部失败不会掩盖已完成结果。
- 默认 React 模式保持兼容。

### 3.2 首期非目标

- 不自动判断是否启用 Team 模式。
- 不替换 `PlannerReActFlow`。
- 不创建固定的 Researcher、BrowserAgent、Coder 等长期专家身份。
- 不实现自由群聊、投票社会或无中心 Swarm。
- 不让远程 A2A Agent 直接成为本地 Worker Pool 成员；A2A 首期仍是工具。
- 不实现完整 DAG 图形化编辑器或画布。
- 不实现 Reviewer/Evaluator 反复优化循环。
- 不允许子 Worker 调用 `message_ask_user`。
- 不支持运行中的 Team DAG Steering；运行期间只能停止。
- 不实现 Checkpoint、进程重启恢复、多进程租约或分布式调度。
- 不修改 `.env`、`api/config.yaml` 或其他本地运行配置。

## 4. 外部调研结论

### 4.1 主流编排模式

| 模式 | 工作方式 | 优点 | 风险 | 本项目判断 |
|---|---|---|---|---|
| 顺序流水线 | Agent 依次处理前一 Agent 输出 | 简单、确定 | 无法利用独立任务并行 | 当前已有近似能力 |
| Router | 分类后路由到一个或多个专家 | 轻量、易控 | 类别通常需要预定义 | 适合未来自动模式选择 |
| Supervisor / Agents-as-Tools | 中心 Agent 调用子 Agent | 上下文隔离、委派灵活 | 难严格保证 DAG 和调度规则 | 参考 Agent 边界 |
| Orchestrator-Workers | 中心规划并委派并行 Worker | 适合动态可拆分任务 | 中心协调成本与 token 增长 | 首期核心模式 |
| Graph / Workflow | 节点按依赖、分支和汇合运行 | 并发和错误传播清晰 | 状态模型更复杂 | 首期运行模型 |
| Handoff / Swarm / Group Chat | Agent 自主转交控制或共享对话 | 对话分流灵活 | 路径难预测、上下文膨胀 | 首期不采用 |
| Evaluator-Optimizer | 生成与评估循环 | 可提升质量 | 延迟、成本和无限循环风险 | 首期不采用 |

Anthropic 的生产 Research 系统采用中心 Orchestrator 和并行 Subagents，并强调根据任务复杂度限制 Agent 数量。OpenAI Agents SDK 同时支持 LLM 决定委派和代码控制编排，并明确把结构化输出、顺序链、评价循环和 `asyncio` 并行列为代码编排方式。

Google Research 对 180 种 Agent 配置的研究显示，多 Agent 对可并行任务有效，但强顺序任务会受到显著协调惩罚；中心编排比无中心并行更能抑制错误传播。因此首期仅并行明确安全的节点，并由代码层掌握最终调度权。

### 4.2 主流框架比较

| 框架 | 主要能力 | 对 mooc-manus 的判断 |
|---|---|---|
| OpenAI Agents SDK | Agents-as-Tools、Handoff、代码编排、Guardrail、Tracing | 借鉴边界；接入会替换现有 Agent 循环 |
| LangGraph | Graph、并行、Checkpoint、HITL、恢复 | Phase 2 需要可靠恢复时优先评估 |
| Google ADK | 顺序/并行/循环、协作工作流、A2A | 会重塑当前 Runner 和 Event 层 |
| Microsoft Agent Framework | Sequential、Concurrent、Handoff、Group Chat、Magentic、Graph | 企业能力完整，首期接入范围过大 |
| CrewAI | Sequential、Hierarchical Manager、角色化 Crew | 原型快，但 Crew 生命周期与现有 Flow 重复 |
| Pydantic AI | 强类型委派、程序化 Handoff、Graph、Usage Limits | 借鉴强类型结果和用量限制 |
| Strands Agents | Graph、Swarm、Workflow、A2A | 模式覆盖全面，但会形成第二套运行时 |

### 4.3 框架决策

首期选择原生增量式实现，原因是：

- 当前项目已经拥有 Agent、Flow、Tool、TaskRunner、Event、Redis Stream 和 SSE。
- 最大规模只有 5 个节点和 3 个并发 Worker，Python `asyncio` 足够。
- 首期没有 Checkpoint 或跨进程恢复需求，外部图运行时的核心优势暂时用不上。
- 引入完整框架会产生两套 Memory、Event、生命周期和状态来源。
- 通过清晰的领域模型与接口，未来仍可替换 `TeamOrchestrator`，无需改变 API 和 UI 协议。

## 5. 总体架构

```text
ChatRequest(mode="team")
  -> AgentService
  -> AgentTaskRunner
  -> FlowRouter
  -> TeamFlow
       -> TeamPlannerAgent
       -> TaskGraphValidator
       -> TeamOrchestrator
            -> TaskWorker 1..3
            -> ToolPolicy
            -> EventQueue
       -> SynthesizerAgent
  -> AgentTaskRunner Event Sink
  -> Redis Stream + Session JSONB
  -> SSE EventMapper
  -> Next.js task list
```

架构遵循以下边界：

- LLM 决定任务目标和依赖。
- 确定性代码决定图是否合法、何时运行、是否并行、允许使用哪些工具。
- Worker 只执行单个节点，不决定全局计划。
- Synthesizer 只汇总已经产生的结果，不继续扩展 DAG。
- AgentTaskRunner 仍是 Redis、PostgreSQL、文件同步和 Session 状态的唯一事件出口。

## 6. API 与 Flow 路由

### 6.1 AgentMode

```python
class AgentMode(str, Enum):
    REACT = "react"
    TEAM = "team"
```

`ChatRequest` 增加：

```python
mode: AgentMode = AgentMode.REACT
```

不提供 `auto` 模式。

### 6.2 请求传递

用户 `MessageEvent` 增加可选 `agent_mode` 字段。用户消息入队时写入本轮 mode；Assistant 消息保持为空。`AgentTaskRunner` 从用户事件中读取 mode，并调用：

```text
react -> PlannerReActFlow
team  -> TeamFlow
```

这样无需替换当前 Redis 输入流协议，也能让 mode 随消息进入后台 Task。

### 6.3 运行中消息

- React 模式继续保持现有行为。
- Team 模式运行期间拒绝追加新消息，返回冲突错误。
- 用户可以调用现有停止能力取消 Team 任务。
- 停止完成后可以发起新的 Team 或 React 请求。

为避免新增数据库字段，运行中的模式从 Session 最近一条用户 `MessageEvent.agent_mode` 判断。

### 6.4 进程中断识别

首期部署约束为单个 API 进程。`SessionService` 注入现有 Task 类型，在读取会话列表或详情时执行懒惰收敛：

```text
session.status == running
且最近用户 mode == team
且 session.task_id 在进程内 Task Registry 中不存在
```

满足条件时，系统从历史事件恢复最新 Graph 投影，把运行中节点标记 failed、未开始节点标记 skipped，并把 Graph 标记 failed，错误原因为 `process_interrupted`；随后追加终态事件并把 Session 更新为 completed。该机制只诚实结束失联任务，不尝试续跑。

## 7. 领域模型

### 7.1 枚举

```python
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
```

### 7.2 Planner 输出模型

Planner 不能输出运行时状态、Worker ID、Attempt 或结果，只能输出计划字段：

```python
class PlannedTask(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    capability: TeamCapability
    success_criteria: str


class PlannedTaskGraph(BaseModel):
    title: str
    goal: str
    tasks: list[PlannedTask]
```

校验通过后，由后端生成 Graph ID，并把 PlannedTask 转为运行时 TeamTask。

### 7.3 运行时 TaskGraph

```python
class TeamTask(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    capability: TeamCapability
    success_criteria: str
    status: TeamTaskStatus = TeamTaskStatus.PENDING
    assigned_agent_id: str | None = None
    attempt_count: int = 0
    result: WorkerResult | None = None
    error: str | None = None


class TaskGraph(BaseModel):
    id: str
    title: str
    goal: str
    tasks: list[TeamTask]
    status: TaskGraphStatus = TaskGraphStatus.PENDING
    error: str | None = None
```

### 7.4 WorkerResult

```python
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


class FinalTeamResponse(BaseModel):
    message: str
    attachments: list[str] = Field(default_factory=list)
```

约束：

- URL 只接受 `http` 和 `https`。
- `success=True` 时 `summary` 不能为空。
- 文件写入或 Shell 生成的产物通过 `artifacts` 返回沙箱路径。
- Worker 声明的来源 URL 必须实际出现于本次节点的 Search 结果或 Browser 导航记录中。
- Worker 不直接生成最终用户回复。

## 8. 组件职责

### 8.1 TeamPlannerAgent

- 不使用工具。
- 使用 JSON 结构化输出生成 `PlannedTaskGraph`。
- 每个节点只声明一个主要 capability。
- 跨能力工作必须拆成依赖节点，例如 Search -> Browser -> FileWrite。
- 节点总数不得超过 5。
- 不指定 Worker ID。
- 不指定原始工具函数名。
- 每个节点必须提供可判断的 `success_criteria`。

### 8.2 TaskGraphValidator

确定性校验：

- 节点数为 1 至 5。
- ID 非空且图内唯一。
- 所有 dependency 都引用存在的节点。
- 节点不能依赖自身。
- 图中不存在环。
- capability 属于后端枚举。
- 至少存在一个无依赖入口节点。

校验失败后，把具体错误反馈给 Planner，允许重新生成一次；第二次仍失败则结束本轮。

### 8.3 TeamOrchestrator

它是普通 Python 组件，不是 LLM Agent。职责：

- 计算 ready、blocked 和 terminal 节点。
- 创建短生命周期 Worker。
- 控制最多 3 个并发 Worker。
- 区分并行安全与独占节点。
- 控制单节点超时和一次重试。
- 在依赖失败时传播 `skipped`。
- 在取消时停止所有子协程。
- 汇聚 Worker Event 到单一事件队列。
- 所有节点终态后决定 Graph 的 completed、partial、failed 或 cancelled 状态。

### 8.4 TaskWorker

- 所有 Worker 使用同一个类，没有永久专家身份。
- 每次只执行一个节点。
- 接收原始目标、当前节点、直接依赖结果、附件路径和允许工具。
- 不接收其他 Worker 的完整消息历史。
- 使用隔离的临时 Memory。
- 输出严格的 `WorkerResult`。
- 记录本节点工具调用中真实出现的 URL，拒绝 WorkerResult 中凭空新增的来源。
- 产生的 ToolEvent 必须携带 graph、task、agent 和 attempt 关联字段。

### 8.5 SynthesizerAgent

- 不调用 Browser、Shell、文件写入、MCP 或 A2A。
- 读取所有完成、失败和跳过节点。
- 合并来源并保留 Markdown 链接。
- 汇总所有产物路径，交由现有附件同步链路处理。
- 输出 `FinalTeamResponse`，其附件沿用现有 `Message`/MessageEvent 同步方式。
- 最终回复中的 HTTP 链接必须来自 WorkerResult 的来源集合，不允许 Synthesizer 新增来源 URL。
- 至少一个节点成功时可以生成部分完成答案。
- 所有节点失败时不生成伪造答案。
- 失败后重试一次，不加入 Reviewer 循环。

### 8.6 Event Sink

Worker 并发执行，但不直接写 Redis 或 PostgreSQL。每个 Worker 通过 `emit(event)` 把事件信封放入 TeamFlow 内部的 `asyncio.Queue`；TeamFlow 顺序取出并 yield，现有 AgentTaskRunner 再顺序写入两个存储。

事件信封包含一个确认 Future。对于 ToolEvent，Worker 在收到发布确认后才继续下一次 LLM/Tool 循环，避免 AgentTaskRunner 尚未生成浏览器截图或同步文件时，Worker 已经改变共享页面或文件状态。

这样保持“执行并行、记账排队”，避免共享 UoW 被多个协程同时使用，并保证两个存储采用相同的事件调用顺序。它不提供跨 Redis/PostgreSQL 的原子事务；写入失败仍沿用当前 AgentTaskRunner 的错误处理。

## 9. DAG 调度

### 9.1 Ready Task

节点满足以下条件时为 ready：

```text
status == pending
且所有 dependencies.status == completed
```

如果任一依赖为 failed、skipped 或 cancelled，节点变为 skipped。

### 9.2 调度循环

```text
while graph 未终止:
  传播依赖失败产生的 skipped
  计算 ready tasks

  如果存在并行安全 ready tasks:
    选择最多 3 个组成批次
    并行执行整个批次
    等待批次结束
    重新计算图
    continue

  如果存在独占 ready task:
    只执行一个
    重新计算图
    continue

  如果没有运行中节点且仍有 pending:
    将图标记 failed，错误为调度死锁
```

并行安全批次和独占节点不会重叠执行。首期优先正确性和可测试性，不实现复杂读写锁或基于文件路径的细粒度资源锁。

### 9.3 默认限制

新增 `AgentConfig` 默认字段：

```text
team_max_tasks = 5
team_max_workers = 3
team_max_task_retries = 1
team_task_timeout_seconds = 300
team_max_worker_iterations = 20
```

这些字段有代码默认值，不要求修改 `api/config.yaml` 或 `.env`。

## 10. ToolPolicy 与并发安全

### 10.1 Capability 映射

| Capability | 暴露的工具 | 调度策略 |
|---|---|---|
| analysis | 无 | 并行安全 |
| search | `search_web` | 并行安全 |
| file_read | `read_file`、`search_in_file`、`find_files` | 并行安全 |
| browser | 当前全部 BrowserTool 函数 | 独占 |
| file_write | `read_file`、`write_file`、`replace_in_file` | 独占 |
| shell | 当前全部 ShellTool 函数 | 独占 |
| mcp | 当前注册的 MCP 动态工具 | 独占 |
| a2a | Agent Card 查询和远程 Agent 调用 | 独占 |

### 10.2 独占原因

- `PlaywrightBrowser` 在一个实例上维护单一活动 `page`。
- File 与 Shell 共用同一个 Session Sandbox。
- MCP/A2A 客户端在本轮共享连接和清理生命周期。
- 首期无法提前可靠获知每个写任务会修改的具体资源。

### 10.3 两层授权

工具权限不能只靠 Prompt：

1. 构造 LLM 请求时，只暴露当前 capability 的工具 Schema。
2. 执行 tool call 前再次检查函数名是否在后端白名单内。

Planner 不能输出实际工具白名单；`ToolPolicy` 是唯一授权来源。未授权工具不会被执行，相关错误返回 Worker；重复越权使本次 Attempt 失败。

### 10.4 人机消息工具

首期不向 Worker 暴露 `message_ask_user`。多个并发节点等待用户需要可恢复 Checkpoint，超出首期范围。

进度通过 Task/Tool Event 展示，不依赖 `message_notify_user`。如果 Team 请求缺少必要输入，Planner 阶段应返回明确错误，用户补充后重新发起。

## 11. Memory 与上下文隔离

当前 `BaseAgent` 通过 `session_id + agent.name` 加载和保存 Memory。同构 Worker 如果共用固定名称会互相覆盖。

首期为 `BaseAgent` 增加向后兼容的 Memory 选项：

```text
memory: Memory | None
persist_memory: bool = true
memory_key: str | None
```

- 现有 Planner/ReAct 不传新参数，行为保持不变。
- Team Planner、Worker 和 Synthesizer 注入新的内存对象并设置 `persist_memory=false`。
- Worker ID 采用 `worker-1`、`worker-2` 等运行时标识，但不作为永久 Memory Key。
- Team Agent 状态不用于重启恢复。

每个 Worker 只获得：

```text
original_goal
current_task
direct_dependency_results
attachment_paths
allowed_capability
allowed_tools
```

不把完整 Session 对话和其他 Worker 工具轨迹复制给每个 Worker，以降低上下文污染和 token 消耗。

## 12. Event 与持久化

### 12.1 新增事件

```python
class TaskGraphEvent(BaseEvent):
    type: Literal["task_graph"] = "task_graph"
    graph: TaskGraph


class TeamTaskEvent(BaseEvent):
    type: Literal["task"] = "task"
    graph_id: str
    task: TeamTask
    agent_id: str | None = None
    attempt: int
```

扩展 `ToolEvent` 的可选字段：

```text
graph_id
task_id
agent_id
attempt
```

React Flow 产生的旧 ToolEvent 保持这些字段为空。

`TaskGraphEvent` 至少在图创建和进入终态时各发送一次；中间节点变化由 `task` 事件表达。

### 12.2 事件顺序

- Worker 将事件写入内存 Queue。
- TeamFlow 是 Queue 的单一消费者。
- TeamFlow yield 后，AgentTaskRunner 完成事件增强与 `_put_and_add_event`，TeamFlow 再确认对应信封。
- Redis Stream ID 提供最终 SSE 顺序。
- PostgreSQL Session JSONB 保存同样的顺序。

### 12.3 首期持久化范围

- 不新增 Run、Task 或 Attempt 数据表。
- Graph 和 Task 状态通过 Session Event 历史保存。
- 前端刷新后通过事件归并恢复最新投影。
- 进程重启导致的在途节点不会自动恢复；下次读取 Session 时按第 6.4 节收敛为 failed/skipped。
- 最大 5 个节点限制了 JSONB 事件增长。

## 13. 错误、重试与取消

### 13.1 Planner

- JSON 无法解析或图校验失败：反馈错误并重新生成一次。
- 第二次仍失败：Graph 为 failed，发送 ErrorEvent。

### 13.2 Worker

- 异常、超时、无效 WorkerResult：进入 retrying。
- 每个节点最多重试一次。
- 重试仍失败：节点 failed，保留最后错误。
- 依赖失败：下游节点 skipped。

### 13.3 Partial

- 全部节点完成：Graph completed。
- 至少一个完成且存在 failed/skipped：Graph partial，Synthesizer 必须明确失败项。
- 没有任何完成节点：Graph failed，不调用 Synthesizer。

### 13.4 取消

- 沿用现有停止 Session 接口。
- Orchestrator 取消所有正在运行的子协程。
- 运行中和待执行节点变为 cancelled。
- Graph 变为 cancelled，不调用 Synthesizer。
- 最终发送终止事件并让 Session 回到 completed。

### 13.5 进程中断

- 不重新执行任何已开始或待执行节点。
- 运行中节点变为 failed，错误为 `process_interrupted`。
- 待执行节点变为 skipped。
- Graph 变为 failed，Session 变为 completed。
- 已经完成的节点结果继续保留在历史中。

## 14. 前端设计

### 14.1 模式选择

ChatInput 增加分段开关：

```text
单 Agent | 多 Agent
```

默认单 Agent。Team 运行中禁用消息输入，只保留停止按钮。

### 14.2 任务列表

不实现完整 DAG 画布。列表展示：

- 节点描述。
- capability。
- dependencies。
- assigned worker。
- pending/running/retrying/completed/failed/skipped/cancelled 状态。
- attempt count。
- 节点下的 Tool 调用。
- 节点结果或错误摘要。
- Worker 返回的来源链接和产物路径。

### 14.3 Timeline 归属

```text
如果 ToolEvent.task_id 存在:
  归属到对应 Team Task
否则:
  使用现有 lastStepId 逻辑
```

这样同时兼容 Team 和 React 历史事件。

### 14.4 刷新恢复

前端按事件顺序归并：

- `task_graph` 建立或替换 Graph 快照。
- `task` 更新对应节点。
- `tool` 按 task_id 插入或更新工具记录。
- `message` 显示最终回复。

## 15. 文件边界

建议新增：

```text
api/app/domain/models/team.py
api/app/domain/services/team/graph.py
api/app/domain/services/team/policy.py
api/app/domain/services/team/orchestrator.py
api/app/domain/services/agents/team_planner.py
api/app/domain/services/agents/task_worker.py
api/app/domain/services/agents/team_synthesizer.py
api/app/domain/services/flows/router.py
api/app/domain/services/flows/team.py
api/app/domain/services/prompts/team.py
ui/src/components/team-task-panel.tsx
```

建议修改：

```text
api/app/domain/models/app_config.py
api/app/domain/models/event.py
api/app/domain/models/session.py
api/app/domain/services/agents/base.py
api/app/domain/services/agent_task_runner.py
api/app/application/services/agent_service.py
api/app/application/services/session_service.py
api/app/interfaces/schemas/session.py
api/app/interfaces/schemas/event.py
ui/src/lib/api/types.ts
ui/src/lib/api/session.ts
ui/src/lib/session-events.ts
ui/src/components/chat-input.tsx
ui/src/components/session-detail-view.tsx
ui/package.json
```

首期不新增 Alembic 迁移。

## 16. 测试策略

### 16.1 测试基础

Agent 和 Orchestrator 测试使用：

- Fake LLM：按预设顺序返回 Graph、Tool Call 或 WorkerResult。
- Fake Tool：记录开始、结束和并发重叠情况。
- Fake Event Sink：记录事件顺序。
- 可控超时 Worker：验证超时、取消和重试。

不使用真实 LLM、真实浏览器、真实 MCP/A2A 或外部服务完成单元测试。

### 16.2 后端单元测试

- Graph 拒绝空图、超过 5 个节点、重复 ID、未知依赖和环。
- Planner 只可输出后端定义的 capability。
- ToolPolicy 正确映射 Schema。
- 执行层拒绝未授权工具。
- Worker Memory 互相隔离。
- ready-task 计算和依赖失败传播正确。
- 三个并行安全节点在 Fake Barrier 上确实重叠执行。
- 独占节点不与任何其他节点重叠。
- Worker 失败只重试一次。
- Worker 和 Synthesizer 不能返回未在工具结果中出现的来源 URL。
- partial、failed、cancelled 终态正确。
- 事件包含 graph_id、task_id、agent_id 和 attempt。
- Event Queue 保持单一写入路径。
- ToolEvent 发布确认会阻止 Worker 在事件增强前改变共享资源。

### 16.3 后端集成测试

- `mode="team"` 产生 task_graph、task、tool、message、done 事件。
- `mode="react"` 仍使用 PlannerReActFlow。
- Team 运行期间追加消息返回冲突。
- 停止 Team 任务会取消子 Worker。
- Session 详情能返回已持久化 Team Event。
- 读取失联的运行中 Team Session 会把它收敛为 process_interrupted 失败终态。

### 16.4 前端测试

- 模式开关正确传递 `mode`。
- Team Event 能归并成任务列表。
- 带 task_id 的 Tool 正确挂到对应节点。
- 无 task_id 的旧 Tool 仍走 Step fallback。
- retrying、failed、skipped、cancelled 状态正确显示。
- 刷新重放事件后投影一致。

前端测试需要在 `ui/package.json` 增加轻量测试脚本和 Vitest；不改变开发服务或生产启动配置。

## 17. 验收标准

- 默认 mode 为 react，现有行为不变。
- 用户可以显式选择 team。
- Planner 能生成包含依赖的 1 至 5 节点 DAG。
- Validator 拒绝无效 Graph。
- 至少两个无依赖的 analysis、search 或 file_read 节点可以真实并行。
- 最大 Worker 并发数为 3。
- Browser、FileWrite、Shell、MCP、A2A 节点独占执行。
- 所有当前操作类工具都可通过对应 capability 参与 DAG。
- Worker 无法调用当前 capability 未授权的工具。
- Worker Memory 相互隔离。
- Worker 失败自动重试一次，之后产生 failed/skip/partial 语义。
- 最终答案保留 Worker 返回的来源 URL。
- 文件或 Shell 产物可以进入最终附件同步链路。
- Tool Event 明确归属 graph/task/agent/attempt。
- 前端能以任务列表显示 DAG 进度。
- 刷新后能从 Session Event 恢复已记录状态。
- Team 运行中不能追加消息，但可以停止。
- 进程重启后的失联 Team Session 在下次读取时被标记为失败，不会永久显示运行中。
- 首期文档不宣称支持进程重启恢复或多实例调度。

## 18. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Planner 生成重复或错误依赖 | 图无法执行 | 确定性校验并允许一次重规划 |
| Worker 越权调用工具 | 安全和副作用风险 | Schema 过滤加执行前二次鉴权 |
| 浏览器或沙箱状态竞争 | 页面、文件或进程相互污染 | 有状态和写任务首期全局独占 |
| 多 Agent token 快速增长 | 成本失控 | 最多 5 节点、3 并发、20 次 Worker 迭代 |
| Worker Memory 污染 | 结果互相干扰 | 每节点独立临时 Memory |
| 并发事件乱序 | UI 错配、刷新不一致 | 内存 Queue、发布确认和单一 Event Sink |
| Worker 局部失败 | 整体结果缺失 | 一次重试、依赖跳过、部分完成汇总 |
| 进程中断 | 在途 DAG 丢失或永久显示运行中 | 下次读取时收敛为失败，后续引入 Checkpoint |
| 自由用户输入改变 DAG | 运行状态不可预测 | Team 运行中禁止追加消息 |

## 19. 后续阶段

后续能力必须单独设计，不进入本次最小实现：

- Checkpoint、恢复和幂等 Attempt。
- 多 API 实例下的租约、心跳和抢占。
- Worker 独立 Sandbox 和 Browser Context，从而放宽并行限制。
- 基于资源路径的读写锁。
- Reviewer/Evaluator 补充执行波次。
- Worker 内 `message_ask_user` 与可恢复 HITL。
- Agent Registry 和自动模式选择。
- 使用正式 A2A SDK 把远程 Agent 纳入调度池。
- 图形化 DAG 画布。

## 20. 参考资料

- Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Google Research, [Towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- OpenAI, [Agents SDK Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- LangChain, [Multi-agent Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- LangChain, [Custom Workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)
- LangChain, [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- Google, [ADK Multi-Agent Workflows](https://adk-labs.github.io/adk-docs/agents/multi-agents/)
- Microsoft, [Agent Framework Overview](https://learn.microsoft.com/en-us/agent-framework/overview/)
- CrewAI, [Processes](https://docs.crewai.com/en/concepts/processes)
- Pydantic, [Multi-Agent Patterns](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)
- Strands Agents, [Multi-agent Patterns](https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/)
- A2A Protocol, [Official Specification](https://a2a-protocol.org/latest/specification/)
- Cemri et al., [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)

## 21. 最终决策

首期实现原生 `TeamFlow`：由 LLM Planner 生成动态 DAG，由代码 Orchestrator 做安全、可测试的确定性调度，由通用 Worker 执行全部现有操作能力，由 Synthesizer 汇总结果。

成功标准不是 Agent 数量，而是：真实并行、依赖正确、工具不丢失、权限最小、事件可归属、局部失败可解释，以及 React 模式兼容。
