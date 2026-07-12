# WhiskerAgent 多 Agent DAG 编排实现详解

本文档整理 `feature/team-dag-orchestration` 分支在当前工作区中的实际代码实现，内容以 `main` 到当前工作区的源码差异为依据，不以历史设计稿、旧测试或已经删除的中间方案为依据。文档覆盖多 Agent 的总体思想、架构设计、领域模型、DAG 规划、并发调度、工具隔离、事件流转、状态持久化、前端展示、取消与错误处理，并按照一次真实请求的执行顺序进行代码走读。当前工作区相对分支 HEAD 仍有三处未提交业务修改，分别是 Team Worker 默认迭代上限调整为 50、整张图失败时汇总每个任务的具体错误，以及只有真正进入运行态的任务才出现在对话区，因此本文描述的是当前工作区行为，而不只是最后一次提交的行为。

按 `git diff --numstat main` 统计，并排除文档、测试、依赖、锁文件和仓库说明文件，当前多 Agent 功能涉及 30 个业务源码文件，新增 1507 行、删除 138 行。其中后端涉及 23 个文件，新增 1223 行、删除 92 行，前端涉及 7 个文件，新增 284 行、删除 46 行。这个规模包含为了保持单 Agent 行为不变而增加的模式分流、事件模型、任务取消和前端事件投影，不包含此前已经删除的 Team 专用面板、测试代码、证据兜底校验和历史设计文档。

## 一、这套实现要解决的是“显式选择的并行任务编排”，而不是把单 Agent 改造成群聊

当前系统原本只有 `PlannerReActFlow`，它会先生成顺序计划，再由一个 ReAct Agent 逐步调用工具并完成总结。这个分支没有直接改写原有单 Agent 流，而是在聊天请求中增加 `react` 和 `team` 两种显式模式，用户选择 `team` 后才进入新的 DAG 编排路径。这样做的核心目标是让一个复杂目标能够被拆成若干有依赖关系的任务，在依赖允许时并行运行，同时继续复用原系统的 LLM、浏览器、沙箱、搜索、文件、MCP、A2A、Redis Stream、数据库和 SSE 基础设施。

这里的“多 Agent”是逻辑 Agent，而不是多个独立进程、多个容器或多个长期驻留的服务。Planner、Worker 和 Synthesizer 都基于同一个 `BaseAgent` 抽象，每个 Worker 拥有独立的短期 Memory、独立的任务提示和受限的工具集合，但它们仍运行在同一个 API 进程和同一个 `asyncio` 事件循环中。并行由 `asyncio.gather` 实现，Worker 名称如 `worker-1`、`worker-2` 表示当前并发批次中的逻辑执行槽位，并不对应固定线程、进程或可跨请求复用的远程 Agent。

这套方案选择“中心化编排、Worker 无状态执行、单点持久化”的结构。Planner 只负责产生计划，Orchestrator 负责决定哪些任务可以运行，Worker 只负责执行一个节点，Synthesizer 只负责汇总已经产生的结构化结果。Worker 不直接写 Redis 或数据库，而是把 `TaskEvent` 和 `ToolEvent` 放入 TeamFlow 的内存事件队列，之后由既有 `AgentTaskRunner` 顺序写入 Redis 输出流和会话事件表，从而避免多个并发 Worker 同时操作同一会话聚合根。

整体数据流可以概括为下面这条链路。第一条分支保留原有单 Agent，第二条分支创建短生命周期 TeamFlow，两条分支最终都汇合到同一个 Runner、事件存储和 SSE 输出通道。这个设计的关键不是“把所有组件都做成多份”，而是把执行决策隔离在 Flow 层，把传输、持久化和前端协议继续保持统一。

```text
ChatInput 选择 mode
        │
        ▼
POST /api/sessions/{id}/chat
        │
        ▼
AgentService：持久化用户消息并写入 Redis 输入流
        │
        ▼
AgentTaskRunner：读取 agent_mode
        │
        ├── react ──► PlannerReActFlow
        │
        └── team  ──► TeamFlow
                       │
                       ├── TeamPlannerAgent ──► PlannedTaskGraph
                       ├── DAG 校验与构建 ───► TaskGraph
                       ├── TeamOrchestrator ─► 并行或串行 TaskWorker
                       └── TeamSynthesizer ──► 最终 MessageEvent
                                │
                                ▼
                  QueuedEventEmitter 内存队列
                                │
                                ▼
                 AgentTaskRunner 单点写 Redis + 数据库
                                │
                                ▼
                         SSE + 前端事件投影
```

## 二、架构中的四类职责被拆成 Planner、Orchestrator、Worker 和 Synthesizer

`TeamPlannerAgent` 是规划角色，它不持有业务工具，并通过 `_tool_choice = "none"` 明确禁止工具调用。它接收用户目标、已经同步到沙箱的附件路径和上一次 DAG 校验错误，输出严格 JSON 格式的 `PlannedTaskGraph`。Planner 的任务只是描述标题、总体目标、任务节点、依赖关系、能力类型和成功标准，它不能直接输出运行状态、Worker 编号、尝试次数、执行结果或具体工具函数名。

`TeamOrchestrator` 是确定性调度角色，它不调用 LLM，也不理解自然语言任务本身。它根据任务状态和依赖关系计算 ready 集合，根据能力类型决定当前节点是否允许并行，然后为每个节点创建 `TaskWorker` 并管理超时、任务级重试、失败传播和最终图状态。把这部分做成普通 Python 状态机而不是再交给 LLM，可以保证依赖、并发上限和失败传播具有可预测行为。

`TaskWorker` 是节点执行角色，每个实例只负责一个 `TeamTask` 的一次 attempt。它拿到总体目标、当前任务定义、已完成依赖节点的结构化结果和用户附件路径，然后在自己的工具白名单内执行 ReAct 循环。Worker 最终必须返回 `WorkerResult` JSON，包含成功标志、非空摘要、来源列表和产物路径，运行过程中的 ToolEvent 会被补上 graph、task、agent 和 attempt 四类归属信息。

`TeamSynthesizerAgent` 是最终汇总角色，它同样不持有工具并强制 `tool_choice = "none"`。它接收整张已经结束的 `TaskGraph`，其中同时包含成功结果、失败错误、跳过状态和产物路径，然后输出最终消息和附件列表。Synthesizer 不负责补跑任务，也不应新增图中没有出现的事实、来源或附件，因此它处在执行链的最后一步，而不是第二个 Planner。

## 三、领域模型把“规划结果”和“运行时状态”明确分成两层

多 Agent 领域模型集中在 `api/app/domain/models/team.py`，最外层首先定义 `AgentMode`、`TeamCapability`、`TeamTaskStatus` 和 `TaskGraphStatus` 四组枚举。`AgentMode` 只有 `react` 与 `team`，能力枚举包含 analysis、search、browser、file_read、file_write、shell、mcp 和 a2a。任务状态包含 pending、running、retrying、completed、failed、skipped 和 cancelled，图状态则包含 pending、running、completed、partial、failed 和 cancelled。

规划阶段使用 `PlannedTask` 与 `PlannedTaskGraph`，运行阶段使用继承自 `PlannedTask` 的 `TeamTask` 与包含任务数组的 `TaskGraph`。`PlannedTask` 只保存 id、description、dependencies、capability 和 success_criteria，`TeamTask` 在此基础上增加 status、assigned_agent_id、attempt_count、result 和 error。这样的分层意味着 LLM 只能提出“应该做什么”，而 status、Worker 分配、重试次数和执行结果只能由后端状态机写入，避免模型伪造运行态。

`WorkerResult` 是 Worker 与下游任务以及 Synthesizer 之间的结构化数据契约。它要求 `success` 为布尔值、`summary` 至少一个字符，并允许附带 `SourceRef` 列表和 artifacts 路径列表；来源 URL 使用 Pydantic `HttpUrl` 做基本格式校验。当前实现已经删除“来源 URL 必须在工具结果中被后端观察到”的二次白名单校验，因此真实性主要依赖 Worker 提示词约束，而不是运行时证据验证。

这些规划与结果模型都使用 `ConfigDict(extra="forbid")`，模型输出包含未声明字段时会直接校验失败。这个选择能及时暴露 Planner 或 Worker 输出格式漂移，也会让模型供应商差异、JSON 修复结果和提示词遵循程度直接影响成功率。校验失败不会被转换成默认计划或默认结果，而是进入对应的有界重试或错误链路。

### 领域模型源码走读

下面是规划节点、运行时节点和图对象在当前分支中的实际定义。`PlannedTask` 只允许 Planner 填写任务语义，`TeamTask` 通过继承追加运行态，`TaskGraph` 再负责聚合整张图。代码没有给 Planner 一个可以直接填写 status 或 result 的通用字典，这正是“模型提计划、程序管状态”能够成立的类型基础。

```python
class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    capability: TeamCapability
    success_criteria: str = Field(min_length=1)


class PlannedTaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[PlannedTask]


class TeamTask(PlannedTask):
    status: TeamTaskStatus = TeamTaskStatus.PENDING
    assigned_agent_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
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

`model_config = ConfigDict(extra="forbid")` 位于规划模型而不是只放在接口 schema 上，因此 LLM 经过 JSONParser 后仍必须服从领域契约。`dependencies` 默认空数组，使根节点不需要额外输出 null，`capability` 直接使用枚举，使诸如 `web_searcher` 这类未声明角色名无法混入调度。`TeamTask` 的五个新增字段都有确定初值，构图时只需把 `PlannedTask.model_dump()` 展开进去，运行态就从 pending、零次尝试和空结果开始。

`TaskGraph.id` 在后端构造运行时图时生成 UUID，不由 Planner 提供，因此模型不能让两轮执行复用同一个 graph id。`task_by_id()` 找不到节点时直接抛出 KeyError，没有返回空节点或临时对象；合法 DAG 的依赖已经提前验证，所以运行阶段出现 KeyError 代表程序状态错误。这个方法随后被 Orchestrator 用来取得每个 dependency 的 WorkerResult，形成下游 Worker 的结构化输入。

Worker 的输出契约同样直接体现在模型代码中。summary 必须非空，sources 中的 URL 至少通过 `HttpUrl` 格式校验，最终回答也必须有非空 message。下面的模型不包含任意 `data` 字段，因此 Worker 想把完整工具响应或额外控制字段塞进结果时会校验失败。

```python
class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: HttpUrl
    snippet: str | None = None


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    summary: str = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class FinalTeamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)
```

`WorkerResult.success` 与“模型成功返回 JSON”是两件事，前者仍可能为 false，Orchestrator 会把这种业务失败转换成异常并进入 attempt 重试。sources 和 artifacts 默认空列表，因此 analysis 节点不引用外部资料、只做计算时可以合法结束。当前代码只对 URL 语法和字段结构做硬校验，没有保存“这个 URL 对应哪一个 ToolEvent”的引用 id，这与前文所述的提示词级证据约束一致。

## 四、领域事件让 Team 与单 Agent 共用同一条存储和 SSE 协议

原有事件体系新增了 `MessageEvent.agent_mode`、`TaskGraphEvent` 和 `TeamTaskEvent`，同时给 `ToolEvent` 增加 graph_id、task_id、agent_id 与 attempt。`TaskGraphEvent` 携带整张图快照，主要用于初始 DAG、最终 DAG、取消快照和刷新恢复；`TeamTaskEvent` 携带单个任务的状态快照，用于运行、重试、完成、失败和跳过的增量更新。ToolEvent 的四个归属字段则解决并行场景下“这个工具调用属于哪张图、哪个任务、哪个 Worker、哪次重试”的问题。

领域事件仍然进入原有 `Event` 判别联合类型，判别字段继续是 `type`。接口层在 `api/app/interfaces/schemas/event.py` 中增加对应的 SSE 数据模型，并通过既有 `EventMapper` 把领域事件转换成 `{event, data}` 结构。事件 id 和 created_at 仍由公共 `BaseEventData` 负责，因此 Team 事件可以和原有 message、plan、step、tool、wait、error、done 一起被持久化、重放和发送给前端。

`Session.get_latest_agent_mode()` 会从后向前寻找最近一条用户 MessageEvent，并读取其中的 agent_mode。旧会话或没有显式 mode 的消息默认返回 `react`，从而保证历史数据和原有调用方不会自动切换到 Team。这个 mode 同时被用于 Runner 的 Flow 选择和运行中 Team 的追加消息冲突判断，因此它既是 UI 偏好，也是当前一轮任务的执行语义。

### Team 事件源码走读

领域事件的实际代码没有把 Team 状态塞进通用 message content，而是新增两个带固定 discriminator 的事件类。完整图快照和单任务增量分开，使刷新恢复与实时更新不必使用同一种巨大载荷。ToolEvent 上的四个可选字段则让已有工具协议无需复制一份 TeamToolEvent。

```python
class MessageEvent(BaseEvent):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"
    message: str = ""
    attachments: List[File] = Field(default_factory=list)
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


class ToolEvent(BaseEvent):
    type: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    tool_content: Optional[ToolContent] = None
    function_name: str
    function_args: Dict[str, Any]
    function_result: Optional[ToolResult] = None
    status: ToolEventStatus = ToolEventStatus.CALLING
    graph_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    attempt: Optional[int] = None
```

`agent_mode` 放在用户 MessageEvent 上，说明模式属于某一轮用户输入，而不是整个 Session 永久不变的配置。TaskGraphEvent 直接携带 TaskGraph 的值对象，TeamTaskEvent 也携带当时 TeamTask 的快照，因此发事件时必须使用 deep copy，不能把后续仍会原地更新的对象引用当作历史。ToolEvent 的字段保持 Optional，是为了让原有 React Flow 产生的工具事件继续合法，它们没有 graph 或 task 归属也能走同一联合类型。

下面的判别联合是数据库反序列化与 Runner 从 Redis 还原事件时的硬边界。`Field(discriminator="type")` 会根据 JSON 中的 type 直接选择模型，未知类型不会被当成 CommonEvent 自动吞下。此前旧数据库中的 `skill` 事件导致会话加载失败，根因就在于这个联合中不存在对应分支。

```python
Event = Annotated[
    Union[
        PlanEvent,
        TitleEvent,
        StepEvent,
        MessageEvent,
        TaskGraphEvent,
        TeamTaskEvent,
        ToolEvent,
        WaitEvent,
        ErrorEvent,
        DoneEvent,
    ],
    Field(discriminator="type"),
]
```

接口层又为两个 Team 事件声明对应 SSE data 类型，但字段结构保持一一对应。`EventMapper` 从 `AgentSSEEvent` 联合反射出 event 字符串与 data class 的映射，因此把 `TaskGraphSSEEvent` 和 `TeamTaskSSEEvent` 加进联合后，既有映射流程就能发送新事件。这里没有在 route 中写 Team 专用 if/else，说明传输层只认识事件类型，不参与 DAG 业务判断。

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
```

## 五、模式从聊天输入框进入后端，并在创建执行流之前完成显式分流

接口请求模型 `ChatRequest` 在原有 message、attachments、event_id 和 timestamp 之外增加了 `mode`，其类型直接使用领域层的 `AgentMode`。默认值是 `react`，所以旧前端、手写 API 请求或没有升级的客户端不传 mode 时，仍然运行原来的单 Agent。这里没有根据问题复杂度自动判断是否进入 Team，也没有让 Planner 自己决定是否再委派 Agent，选择权始终由用户界面的“单 Agent / 多 Agent”开关明确给出。

`POST /api/sessions/{session_id}/chat` 在创建 `EventSourceResponse` 之前调用 `AgentService.validate_chat_request()`。这一步专门检查“有新消息、会话正在运行、最近一条用户消息是 Team”这一组合，命中时直接抛出 `ConflictError`，由应用异常体系返回 HTTP 409。校验必须放在 SSE 响应对象创建之前，否则 HTTP 状态已经开始以 200 流式返回，前端只能收到流中的 ErrorEvent，无法按普通接口冲突处理。

同一项冲突校验在 `AgentService.chat()` 内又执行一次，这是服务层对调用入口的约束，不是吞掉异常的兜底。路由前置校验负责给 HTTP 客户端正确的 409，服务层校验负责防止未来其他调用方绕过路由直接向运行中的 Team 追加消息。当前产品语义是一个 Team 图执行期间不接受改写目标或插入新指令，用户若要改变任务，必须先停止当前运行，再发送新消息。

当请求带有非空 message 时，`AgentService.chat()` 先加载会话和当前 Task；会话不是 running 或 Task 已不存在时，它会创建新的沙箱、浏览器、`AgentTaskRunner` 和 Redis Stream Task。附件 id 会在工作单元内解析成数据库中的文件对象，随后与用户文本和 mode 一起构造成 `MessageEvent(role="user")`。这个用户事件先写入 Task 的输入 Redis Stream 并获得事件 id，再写入会话事件表，同时将会话状态更新为 running，最后调用 `task.invoke()` 启动后台执行。

当请求没有 message 时，它不是一次新的模型调用，而是前端附着到已有 Task 输出流的恢复请求。`latest_event_id` 会作为 Redis Stream 的读取起点，使断线后的客户端从上次已经收到的事件继续向后消费，而不是重新提交用户问题。服务循环遇到 `DoneEvent`、`ErrorEvent` 或 `WaitEvent` 后结束当前 SSE，这三个事件构成聊天流的终止边界。

`AgentTaskRunner` 真正从输入流取出用户 MessageEvent 后读取 `agent_mode`，然后在 `_run_flow()` 中做显式分支。`react` 复用 Runner 创建时已有的 `PlannerReActFlow`，`team` 则调用 Team Flow 工厂为当前消息创建一个全新的 `TeamFlow`。这种分流保证两个模式共享同一会话、同一沙箱和同一事件基础设施，但不会让 Team 的 Planner、Memory、取消状态或内存队列泄漏到下一轮消息。

下面的伪代码概括了模式选择所在的位置，它强调分支发生在 Flow 层，而不是发生在工具层或前端展示层。实际代码还使用 `contextlib.aclosing` 包住异步事件生成器，确保流提前退出或取消时能够执行清理。由于单 Agent 的实例仍由 Runner 长期持有，Team 的实例按消息新建，所以二者在执行生命周期上也被明确分开。

```python
mode = message_event.agent_mode
if mode is AgentMode.REACT:
    flow = self._react_flow
elif mode is AgentMode.TEAM:
    flow = self._team_flow_factory()

async with aclosing(flow.invoke(message)) as events:
    async for event in events:
        await self._put_and_add_event(event)
```

### 请求入口与 Flow 分流源码走读

真实请求模型只新增了一行 mode，默认值直接落在后端而不是依赖前端补齐。即使旧页面没有发送 mode，Pydantic 也会构造 `AgentMode.REACT`，因此兼容语义由 API 自己保证。attachments 仍然是文件 id 列表，mode 不改变附件上传协议。

```python
class ChatRequest(BaseModel):
    message: Optional[str] = None
    attachments: Optional[List[str]] = Field(default_factory=list)
    event_id: Optional[str] = None
    timestamp: Optional[int] = None
    mode: AgentMode = AgentMode.REACT
```

路由收到请求后，第一条 await 不是创建 EventSourceResponse，而是校验运行中 Team 是否允许追加消息。只有校验通过，内部 `event_generator()` 才调用服务并把每个领域事件映射成 `ServerSentEvent`。这段先后顺序保证 ConflictError 仍能由 FastAPI 的普通异常处理返回 HTTP 409，而不是已经发送 200 响应头后再在流中报错。

```python
@router.post(path="/{session_id}/chat")
async def chat(
        session_id: str,
        request: ChatRequest,
        agent_service: AgentService = Depends(get_agent_service),
) -> EventSourceResponse:
    await agent_service.validate_chat_request(
        session_id,
        bool(request.message),
    )

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in agent_service.chat(
                session_id=session_id,
                message=request.message,
                attachments=request.attachments,
                latest_event_id=request.event_id,
                timestamp=(
                    datetime.fromtimestamp(request.timestamp)
                    if request.timestamp else None
                ),
                mode=request.mode,
        ):
            sse_event = EventMapper.event_to_sse_event(event)
            if sse_event:
                yield ServerSentEvent(
                    event=sse_event.event,
                    data=sse_event.data.model_dump_json(),
                )

    return EventSourceResponse(event_generator())
```

`bool(request.message)` 让空 body 的恢复流绕过运行中消息冲突，因为它没有提交新指令，只是在消费已有 Task。`mode=request.mode` 原样进入应用服务，不在路由中根据会话状态重新推断。事件生成器逐个 yield，因此 Runner 写到 output stream 的 task、tool、message 和 done 都经同一转换路径到达浏览器。

服务层前置校验的代码只读取 Session，不创建 Task，也不改变状态。它向后扫描最近一轮用户消息得到 mode，而不是相信当前请求声称的 mode；否则客户端可以在一个运行中的 Team 上发送 mode=react 绕过互斥。`has_message=False` 的早退则明确保留断线续传入口。

```python
async def validate_chat_request(
        self,
        session_id: str,
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
```

新消息路径的核心代码先解析附件，再把 mode 写入用户事件。`task.input_stream.put()` 返回的 Redis event id 会覆盖 MessageEvent 初始 UUID，随后同一个 id 被写进 Session events，前端收到的用户消息也由 `yield message_event` 立即显示。只有用户事件和 running 状态已经持久化后才调用 `task.invoke()`，因此后台 Runner 不会在会话尚未记录输入时开始输出结果。

```python
message_event = MessageEvent(
    role="user",
    message=message,
    attachments=[
        attachment
        for attachment in db_attachments
        if attachment is not None
    ],
    agent_mode=mode,
)
event_id = await task.input_stream.put(
    message_event.model_dump_json()
)
message_event.id = event_id
async with self._uow:
    await self._uow.session.add_event(session_id, message_event)
    await self._uow.session.update_status(
        session_id,
        SessionStatus.RUNNING,
    )
await task.invoke()
yield message_event
```

这段代码也解释了 mode 为什么必须属于 MessageEvent：Runner 从 Redis input stream 还原的就是这个事件，不能读取前端组件的临时状态。附件在这里仍是数据库 File 对象，Runner 后面会把它们同步到沙箱并替换成 filepath。Task.invoke() 只负责确保后台执行协程启动，真正选择 React 或 Team 发生在 Runner 消费输入之后。

服务随后从 output stream 按 latest_event_id 续读。每次解析后先把未读数归零，再 yield 给路由；Done、Error 或 Wait 任意一种出现都会 break。`block_ms=0` 在当前消息队列实现中表示阻塞等待新事件，不是循环重新发起模型请求。

```python
while task and not task.done:
    event_id, event_str = await task.output_stream.get(
        start_id=latest_event_id,
        block_ms=0,
    )
    latest_event_id = event_id
    if event_str is None:
        continue

    event = TypeAdapter(Event).validate_json(event_str)
    event.id = event_id

    async with self._uow:
        await self._uow.session.update_unread_message_count(
            session_id,
            0,
        )

    yield event
    if isinstance(
        event,
        (DoneEvent, ErrorEvent, WaitEvent),
    ):
        break
```

这段 while 以 Task 未 done 为外层条件，但真正的正常退出依赖终止事件，所以 Runner 必须在成功、失败和取消路径中至少写入一种终止事件。ErrorEvent 被明确列在 break 类型中，整图失败不会让服务继续等待一个不会到来的 DoneEvent。前端的正常流结束处理与这里的 break 对应，双方都没有重新执行 chat 的业务逻辑。

Runner 在构造时保留一个 React Flow，并保存一个创建 TeamFlow 的 lambda。React Flow 可以延续原有 Memory 和等待语义，Team factory 每次被调用都会创建新的 Planner、Orchestrator、队列和图状态。MCPTool 与 A2ATool 实例被 lambda 闭包捕获，所以两个 Flow 复用连接，但不会复用 TeamFlow 对象。

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
)
```

实际 `_run_flow()` 的 if/elif 是唯一模式分支，未知枚举值直接抛 ValueError。`aclosing()` 包住 `invoke()` 返回的异步生成器，使退出循环时调用生成器的 `aclose()`，进而执行 TeamFlow 的 finally。每个 ToolEvent 在 yield 给外层前先做预览增强，每个 assistant MessageEvent 在 yield 前先同步附件，这两项逻辑对 React 和 Team 完全共用。

```python
async def _run_flow(
        self,
        message: Message,
        mode: AgentMode = AgentMode.REACT,
) -> AsyncGenerator[BaseEvent, None]:
    if not message.message:
        yield ErrorEvent(error="空消息错误")
        return

    if mode is AgentMode.REACT:
        self._active_flow = self._react_flow
    elif mode is AgentMode.TEAM:
        self._active_flow = self._team_flow_factory()
    else:
        raise ValueError(f"不支持的 Agent mode: {mode}")

    async with aclosing(self._active_flow.invoke(message)) as flow_events:
        async for event in flow_events:
            if isinstance(event, ToolEvent):
                await self._handle_tool_event(event)
            elif isinstance(event, MessageEvent):
                await self._sync_message_attachments_to_storage(event)
            yield event
```

这里没有“Team 失败就改跑 React”的 else，也没有根据问题看似简单就把 Team 降级成单 Agent。`self._active_flow` 同时给停止逻辑提供当前 Flow 引用，所以取消时能调用 TeamFlow.cancel_events() 生成节点快照。模式分流之后，两条 Flow 都只向 Runner 暴露异步事件序列，后面的持久化代码不再关心是哪一种 Agent。

## 六、Planner 只产生候选 DAG，真正可执行的图由确定性校验器构建

`TeamPlannerAgent.create_graph()` 把用户目标、附件路径和上一次校验错误序列化成一个 JSON 查询，再交给 `BaseAgent.invoke()`。它的系统提示词要求把目标拆成一到五个节点，每个节点选择恰好一种 capability，并严格按照 title、goal、tasks、id、description、dependencies、capability 和 success_criteria 的结构返回。Planner 使用 `json_object` 输出格式且不暴露工具，因此正常情况下只需要一次 LLM 响应，不会执行搜索、读文件或浏览器操作。

附件在 Planner 阶段表现为同步到沙箱后的路径列表，而不是完整文件内容。Planner 可以据此判断是否需要 file_read、file_write 或 shell 节点，但不能在规划阶段假装已经读过附件，也不能把路径本身当作执行结果。真正读取或修改附件只能发生在获得对应 capability 的 Worker 中，这使“规划意图”和“执行证据”保持分离。

LLM 返回 MessageEvent 后，Planner 先调用既有 JSONParser 把文本解析成 Python 对象，再由 `PlannedTaskGraph.model_validate()` 执行结构校验。只要返回额外字段、缺少必填字段、给出非法 capability 或不符合字段类型，Pydantic 就会直接报错。代码不会用默认任务替换坏计划，也不会悄悄删除陌生字段，因此模型输出问题会原样进入 Planner 的重试和最终错误链路。

结构合法仍不代表 DAG 合法，`build_task_graph()` 会继续执行图约束校验。它首先验证任务数量位于 1 到 `team_max_tasks` 之间，再验证任务 id 全局唯一、节点不能依赖自己、同一节点不能重复声明同一个依赖，并确保每个依赖 id 都真实存在。任何一项失败都会抛出 `TaskGraphError`，而不是自动改名、删除依赖或把非法图降级成顺序列表。

环检测使用 Kahn 拓扑排序，而不是依赖递归深度或只检查直接互相依赖。实现先为所有节点统计入度并建立父节点到子节点的邻接表，然后从入度为零的节点开始逐个出队，每访问一条边就递减子节点入度。最终访问节点数少于总节点数时，说明至少存在一个环，构图以 `cycle detected` 失败。

只有结构校验和 DAG 校验都通过后，`PlannedTaskGraph` 才会被转换成运行时 `TaskGraph`。转换时每个 `PlannedTask` 被复制成 `TeamTask`，运行态字段使用模型定义的初始值，例如 pending、无 assigned_agent_id、attempt_count 为零、无 result 和 error。图本身生成 UUID 作为 graph id，后续任务事件与工具事件都以这个 id 归属到同一次 Team 执行。

TeamFlow 对“计划 JSON 合法但图约束不合法”的情况最多执行两轮 Planner 尝试。第一轮校验失败后，错误文本会作为 `previous_validation_error` 重新交给 Planner，让模型针对重复 id、未知依赖、环或任务数量问题修正原计划；第二轮仍失败时直接产生 Team Planner 错误。这里的重试是明确、有界并且携带失败原因的恢复策略，不是用另一个默认 DAG 隐藏 Planner 缺陷。

当前 Planner 只负责返回结构化图，没有像单 Agent Planner 那样额外输出一段面向用户的计划说明。前端能够通过 `TaskGraphEvent` 在输入框上方展示完整任务清单，但对话正文不会自动出现“我会先搜索什么、再比较什么”的自然语言说明。这个差异是当前实现的真实边界，并不是前端漏渲染某个已经存在的 Planner MessageEvent。

### Planner 与 DAG 校验源码走读

Planner 类本身很短，因为通用的 LLM 调用、Memory 和 JSON 输出都由 BaseAgent 提供。`_tool_choice = "none"` 会原样传给 LLM，`_format = "json_object"` 要求模型返回 JSON 对象，系统提示词则规定允许的 capability 和字段。`create_graph()` 只关注最终 MessageEvent 或 ErrorEvent，不转发 Planner 内部事件给用户。

```python
class TeamPlannerAgent(BaseAgent):
    name = "team_planner"
    _system_prompt = PLANNER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def create_graph(
        self,
        message: Message,
        validation_error: str | None = None,
    ) -> PlannedTaskGraph:
        query = json.dumps(
            {
                "goal": message.message,
                "attachments": message.attachments,
                "previous_validation_error": validation_error,
            },
            ensure_ascii=False,
        )
        async for event in self.invoke(query):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                return PlannedTaskGraph.model_validate(parsed)
        raise RuntimeError("planner produced no graph")
```

Planner 实际系统提示词把任务数、能力枚举和禁止字段写成硬指令。它要求跨能力工作拆节点，意味着“搜索后写文件”不能伪装成一个 search 节点直接调用 write_file。提示词只是帮助模型第一次生成正确候选图，后端的 Pydantic 与 build_task_graph 才是不可绕过的校验。

```python
PLANNER_SYSTEM_PROMPT = """你是 Team Planner。只输出 JSON，不调用工具。
把用户目标拆成 1 到 5 个 DAG 节点。每个节点只能选择一个 capability：
analysis, search, browser, file_read, file_write, shell, mcp, a2a。
跨能力工作必须拆成依赖节点。禁止输出 status、agent_id、attempt、result 或工具函数名。
输出格式：
{"title":"...","goal":"...","tasks":[{"id":"task_1","description":"...","dependencies":[],"capability":"search","success_criteria":"..."}]}
"""
```

当前提示词文字固定写一到五个节点，而配置模型允许 team_max_tasks 调到 20，这两处并非完全动态联动。默认配置同样是 5，所以当前默认行为一致；若部署把上限调高，后端会接受更多节点，但 Planner 提示仍倾向最多五个。代码没有在运行时 format 这个提示词，因此这是配置扩展时需要注意的静态约束。

query 使用 `ensure_ascii=False`，所以中文目标和路径不会被转义成大段 Unicode 序列，模型能直接读取。`previous_validation_error` 第一轮是 null，只有后端校验失败后才带入第二轮；它不是历史对话中的用户消息，也不会被持久化到 Session Memory。收到最终文本后先经过 JSONParser，再用 PlannedTaskGraph 做领域校验，两个阶段任一失败都会离开 `create_graph()`。

TeamFlow 对 Planner 的调用把“可修正的计划错误”和“Planner 运行错误”分开处理。`ValueError` 包含 Pydantic ValidationError 与 TaskGraphError，代码把错误文本保存后继续下一轮；其他 Exception 立即发 ErrorEvent 并 return。for 循环正常完成两次仍未 break 时进入 `else`，因此不会出现第三次隐式 Planner 调用。

```python
validation_error = None
for _ in range(2):
    try:
        planned = await self._planner.create_graph(
            message,
            validation_error,
        )
        self._graph = build_task_graph(
            planned,
            self._team_max_tasks,
        )
        break
    except ValueError as exc:
        validation_error = str(exc)
    except Exception as exc:
        self._done = True
        yield ErrorEvent(error=f"Team Planner 失败: {exc}")
        return
else:
    self._done = True
    yield ErrorEvent(
        error=f"Team Planner 生成无效 DAG: {validation_error}"
    )
    return
```

这段结构不是“任何错误都再试一次”：模型服务不可用或 BaseAgent ErrorEvent 转成的 RuntimeError 会走第二个 except，立即终止。只有已经得到候选 JSON、但字段或图结构不合法时，Planner 才有一次携带具体错误的修正机会。构图成功的 break 跳过 for-else，并继续发送 TitleEvent 和初始 TaskGraphEvent。

`build_task_graph()` 的第一部分验证数量与 id，然后在一次循环中同时检查依赖并建立拓扑排序所需的数据结构。`known_ids` 用于 O(1) 判断未知依赖，`indegree` 记录每个节点还有多少父节点，`children` 保存某个父节点完成后应递减哪些子节点。重复依赖必须单独检查，因为简单累加入度会让同一条逻辑边被计算两次。

```python
def build_task_graph(plan: PlannedTaskGraph, max_tasks: int) -> TaskGraph:
    if not 1 <= len(plan.tasks) <= max_tasks:
        raise TaskGraphError(
            f"task count must be between 1 and {max_tasks}"
        )

    task_ids = [task.id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise TaskGraphError("duplicate task id")

    known_ids = set(task_ids)
    indegree = {task_id: 0 for task_id in task_ids}
    children: dict[str, list[str]] = {
        task_id: [] for task_id in task_ids
    }
    for task in plan.tasks:
        if task.id in task.dependencies:
            raise TaskGraphError("self dependency")
        if len(task.dependencies) != len(set(task.dependencies)):
            raise TaskGraphError(
                f"duplicate dependency in task: {task.id}"
            )
        for dependency in task.dependencies:
            if dependency not in known_ids:
                raise TaskGraphError(
                    f"unknown dependency: {dependency}"
                )
            indegree[task.id] += 1
            children[dependency].append(task.id)
```

自依赖在环检测前就用更具体的错误返回，方便 Planner 第二次直接修复对应节点。未知依赖也不会被当作“尚未创建的未来任务”，因为当前图是静态完整计划，所有节点必须一次给出。`children[dependency].append(task.id)` 把 Planner 提供的“子节点列出父节点”形式转换为调度需要的“父节点知道子节点”形式。

第二部分使用 Kahn 算法做全图环检测。队列初始只放入度为零的根节点，每弹出一个节点就把所有出边从逻辑图中移除，子节点入度降为零时再入队。若最终访问数少于任务总数，剩余节点都被某个环困住，代码明确拒绝构图。

```python
queue = deque(
    task_id
    for task_id, degree in indegree.items()
    if degree == 0
)
visited = 0
while queue:
    current = queue.popleft()
    visited += 1
    for child in children[current]:
        indegree[child] -= 1
        if indegree[child] == 0:
            queue.append(child)
if visited != len(task_ids):
    raise TaskGraphError("cycle detected")

return TaskGraph(
    title=plan.title,
    goal=plan.goal,
    tasks=[
        TeamTask(**task.model_dump())
        for task in plan.tasks
    ],
)
```

这里的拓扑排序只用于验证，没有重排 Planner 原始 tasks 数组。保留原顺序使底部任务面板与同级任务的调度优先级稳定，而真正能否运行仍由 dependencies 决定。最后一行把每个 PlannedTask 解包成 TeamTask，运行态字段由默认值生成，Planner 输出不会直接成为可变运行对象。

## 七、能力策略同时决定 Worker 能看到哪些工具，以及哪些节点允许并发

当前实现没有为搜索、浏览器、文件和 Shell 分别创建不同 Worker 类，而是只有一个通用 `TaskWorker`。节点的 capability 由 Planner 选择，`ToolPolicy` 再根据 capability 计算该 Worker 的工具箱和函数白名单。这样保留一套一致的 ReAct 执行逻辑，同时通过后端策略阻止 Worker 越权调用不属于当前节点的工具。

Team Flow 构建时注入的实际工具包括 `FileTool`、`ShellTool`、`BrowserTool`、`SearchTool`、共享的 `MCPTool` 和共享的 `A2ATool`。analysis 节点不获得任何工具，search 节点只获得 `search_web`，file_read 节点只获得 `read_file`、`search_in_file` 和 `find_files`。file_write 节点在三个只读函数之外增加 `write_file` 和 `replace_in_file`，因此写文件任务仍能先查找和读取目标文件。

browser、shell、mcp 和 a2a 能力采用工具箱级授权，即把对应 Tool 实例当前声明的全部函数名加入白名单。这样做是因为这些工具的函数集合由具体实现或外部连接动态提供，不能像 FileTool 一样在静态字典中逐个固定。白名单仍会在实际暴露的 schema 集合上计算，因此不存在于当前 Tool 实例中的函数不会凭空授权。

`BaseAgent` 为这项策略新增了 `allowed_tool_names` 注入点。发送给 LLM 的 tool schemas 会先按白名单过滤，而模型返回工具调用后，运行时 `_get_tool()` 还会再次检查函数名是否获准，避免模型手写一个未暴露函数名绕过 schema。前一层约束模型可见能力，后一层约束实际执行权限，两层共同构成 capability 的执行边界。

并发安全策略与工具可见策略放在同一个 `ToolPolicy` 中，但它们解决的是不同问题。当前只把 analysis、search 和 file_read 标记为 `PARALLEL_SAFE`，因为这些节点不应修改共享沙箱状态，搜索请求之间也没有本地可变资源依赖。browser、file_write、shell、mcp 和 a2a 被视为非并发安全，调度器每次只运行其中一个 ready 节点，以避免共享浏览器页面、文件、终端会话或外部连接发生交叉影响。

这种串行限制基于当前 Tool 实例确实被同一 Team Flow 共享，而不是认为所有浏览器或 Shell 在理论上都不能并行。如果未来每个 Worker 拥有隔离的浏览器上下文、独立沙箱副本或独立 MCP 连接，策略可以进一步放宽；在当前最小实现里没有引入这些成本更高的资源隔离。因而“多个 Agent 并行”准确地说是多个只读或搜索节点可以并行，任何会操作共享可变状态的节点仍由中心调度器串行执行。

`MessageTool` 没有加入 Team 工具集合，所以 Worker 无法调用 `message_notify_user` 或 `message_ask_user`。单 Agent 对话中那些“正在搜索”“已经获取数据，继续查找”的自然语言进度，实际上来自 MessageTool 的工具调用结果，而不是普通 ToolEvent 自动生成的文案。Team 当前只能流式发送任务状态与业务工具事件，因此对话区有任务折叠块和工具明细，却没有与单 Agent 完全相同的自然语言进度消息。

对应代码可以直接看到两个 Flow 的工具数组差异。React 的 tools 中间包含 `MessageTool()`，Team 的 tools 在 SearchTool 后直接进入 MCP 与 A2A，没有 MessageTool。由于 Worker factory 只能从 Team 的这组 tools 里按 capability 取子集，任何 capability 都不可能获得消息函数。

```python
# PlannerReActFlow
tools = [
    FileTool(sandbox=sandbox),
    ShellTool(sandbox=sandbox),
    BrowserTool(browser=browser),
    SearchTool(search_engine=search_engine),
    MessageTool(),
    mcp_tool,
    a2a_tool,
]

# TeamFlow
tools = [
    FileTool(sandbox=sandbox),
    ShellTool(sandbox=sandbox),
    BrowserTool(browser=browser),
    SearchTool(search_engine=search_engine),
    mcp_tool,
    a2a_tool,
]
```

这不是前端过滤，因为 Team 后端从源头就不会产生 function_name 为 message_notify_user 的 ToolEvent。要恢复同类进度，需要先决定哪些 capability 可以发通知、是否允许 ask_user 让并行图进入等待态，并把相应函数纳入 Policy。只把前端写成“每次 search calling 自动显示一句话”会制造模型没有发送过的消息，因此不属于当前实现。

### 工具策略与双层授权源码走读

策略文件先用静态集合精确列出 analysis、search 和文件能力允许的函数。`TOOLBOX_NAMES` 再把 capability 映射到 BaseTool.name，使 `tools_for()` 只把对应工具箱实例交给 Worker。analysis 不在 TOOLBOX_NAMES 中，因此查找结果是空数组，不是把所有工具交给模型后靠提示词要求它别用。

```python
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
    TeamCapability.SEARCH: "search",
    TeamCapability.FILE_READ: "file",
    TeamCapability.FILE_WRITE: "file",
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
```

STATIC_NAMES 使用 frozenset，构建后不会被某个 Worker 在运行中修改。file_write 之所以同时包含读函数，是因为修改文件前通常需要定位和查看内容，而 file_read 集合没有任何写函数。`PARALLEL_SAFE` 与工具名白名单是独立集合，搜索被认为可并发不代表搜索 Worker 能调用浏览器。

`allowed_names()` 还会把静态配置与 Tool 实例真实暴露的 schema 取交集。这样即使策略写着 search_web，但当前 SearchTool 因配置未提供该函数，最终白名单仍是空，不会向模型声明一个无法执行的函数。动态工具箱则遍历当前 schemas 收集全部函数，适配 MCP 与 A2A 的运行时函数集合。

```python
class ToolPolicy:
    def __init__(self, tools: list[BaseTool]):
        self._tools = tools

    def allowed_names(
        self,
        capability: TeamCapability,
    ) -> frozenset[str]:
        scoped_tools = self.tools_for(capability)
        if capability in STATIC_NAMES:
            configured = STATIC_NAMES[capability]
            available = {
                schema["function"]["name"]
                for tool in scoped_tools
                for schema in tool.get_tools()
            }
            return frozenset(configured.intersection(available))

        names: set[str] = set()
        for tool in scoped_tools:
            names.update(
                schema["function"]["name"]
                for schema in tool.get_tools()
            )
        return frozenset(names)

    def tools_for(
        self,
        capability: TeamCapability,
    ) -> list[BaseTool]:
        toolbox_name = TOOLBOX_NAMES.get(capability)
        if toolbox_name is None:
            return []
        return [
            tool for tool in self._tools
            if tool.name == toolbox_name
        ]

    def is_parallel_safe(
        self,
        capability: TeamCapability,
    ) -> bool:
        return capability in PARALLEL_SAFE
```

注意 `tools_for()` 返回的是共享 Tool 对象列表，不是复制新的浏览器或沙箱。正因为 browser、shell、MCP 和 A2A Worker 最终可能指向同一实例，`is_parallel_safe()` 才必须在调度阶段限制它们。策略没有根据任务 description 猜安全性，Planner 选择的 capability 是唯一判定依据。

BaseAgent 构造函数把白名单和 Memory 策略保存下来。传入 `memory=Memory()` 时 `_persist_memory` 为 false，Team 短期对话只留在对象中；原 React Agent 不传 memory 时仍通过 UoW 加载并保存长期 Memory。白名单为 None 表示保持旧行为，空 frozenset 则表示明确不允许任何工具，两者语义不能互换。

```python
self._memory: Optional[Memory] = memory
self._persist_memory = memory is None
self._allowed_tool_names = (
    frozenset(allowed_tool_names)
    if allowed_tool_names is not None
    else None
)
self._tools = tools

async def _ensure_memory(self) -> None:
    if self._memory is not None:
        return
    async with self._uow:
        self._memory = await self._uow.session.get_memory(
            self._session_id,
            self.name,
        )

async def _save_memory(self) -> None:
    if not self._persist_memory:
        return
    async with self._uow:
        await self._uow.session.save_memory(
            self._session_id,
            self.name,
            self._memory,
        )
```

如果 Team Worker 只传空白名单而仍使用持久 Memory，多个 `task_worker` 会因为相同 name 读取同一份会话记忆，这正是注入新 Memory 同时关闭持久化要解决的问题。Planner 和 Synthesizer 也各自获得新 Memory，所以它们的 JSON 指令不会成为 Worker 的历史上下文。React Flow 没有传这两个新参数，因此这段扩展保持向后兼容。

工具权限在发送 schema 和执行函数两个位置检查。`_get_available_tools()` 决定 LLM 看见什么，`_get_tool()` 决定即使模型伪造调用名后端是否执行。后者在遍历工具箱之前抛 PermissionError，使“工具箱里确实有这个函数”也不能绕过 capability 白名单。

```python
def _get_available_tools(self) -> List[Dict[str, Any]]:
    available_tools = []
    for tool in self._tools:
        available_tools.extend(tool.get_tools())
    if self._allowed_tool_names is None:
        return available_tools
    return [
        schema
        for schema in available_tools
        if schema["function"]["name"]
        in self._allowed_tool_names
    ]

def _get_tool(self, tool_name: str) -> BaseTool:
    if (
        self._allowed_tool_names is not None
        and tool_name not in self._allowed_tool_names
    ):
        raise PermissionError(f"工具未授权: {tool_name}")

    for tool in self._tools:
        if tool.has_tool(tool_name):
            return tool
    raise ValueError(f"未知工具: {tool_name}")
```

这两层检查也解释了为什么仅修改 Worker 提示词不能增加文件写权限。必须同时让 ToolPolicy 返回 FileTool、让 allowed_names 包含写函数，并保证实际 FileTool schema 暴露该函数。反过来，MessageTool 没被放入 build_team_flow 的 tools 数组，即使模型知道 `message_notify_user` 这个名字，schema 不可见且运行时也找不到对应工具箱。

## 八、每个 Worker 都是一次性执行上下文，依赖结果通过结构化输入传递

Orchestrator 为某个节点启动 attempt 时，会通过 Worker 工厂创建一个新的 `TaskWorker`。构造参数包含 graph id、完整 `TeamTask`、逻辑 agent id 和 attempt 序号，同时注入当前 capability 对应的工具、工具白名单、JSONParser、LLM 和一份新的 Memory。Worker 名称在类级别仍是 `task_worker`，真正用于前端归属的是事件上的 `worker-1`、`worker-2` 等 agent_id。

Worker 查询不是把整张可变图直接交给模型，而是只包含总体 goal、当前节点的五个规划字段、已完成依赖的 `WorkerResult` 映射和附件路径。当前节点的 status、assigned_agent_id、attempt_count、旧 error 和旧 result 不会进入模型输入，避免运行时控制字段影响模型对业务任务的理解。依赖结果使用 task id 作为键，因此汇总节点可以明确区分每个上游摘要、来源和产物。

每个 attempt 都使用新的内存对象，而且注入 Memory 后 `BaseAgent` 会关闭按 agent name 读写数据库 Memory 的行为。这避免所有并发 Worker 因为类名都叫 `task_worker` 而共享同一份历史消息，也避免 Planner、Worker 和 Synthesizer 的内部 JSON 对话污染原有单 Agent 的长期上下文。代价是任务级重试也会创建全新 Memory，并且当前没有把上一 attempt 的错误显式传入下一 attempt，所以重试有可能重复相同策略。

Worker 内部继续复用原有 ReAct 循环：LLM 可以返回一个或多个获准工具调用，BaseAgent 执行工具并把结果加入短期 Memory，再请求下一轮模型响应。最终普通 MessageEvent 会被 JSONParser 解析并校验成 `WorkerResult`，`success=false` 会由 Orchestrator 转换成任务失败。若 Worker 收到 BaseAgent 的 ErrorEvent、始终没有最终 MessageEvent，或者最终 JSON 不符合模型契约，`execute()` 会抛出异常并进入任务级重试。

Worker 产生的每一个 ToolEvent 都会在离开 Worker 之前补齐 graph_id、task_id、agent_id 和 attempt，然后通过异步 `emit()` 写入 TeamFlow 事件队列。Worker 本身不会把事件写进 Redis，也不会打开数据库事务，更不会直接修改 Session 聚合。并发执行只影响事件到达内存队列的先后顺序，真正对外发布和持久化仍由消费队列的单一 Runner 顺序完成。

当前 `team_max_worker_iterations` 的工作区默认值是 50，它替代 Team Worker 使用的全局单 Agent迭代上限，但并不表示每个正常任务一定会运行 50 轮。每轮可能是一次 LLM 请求，也可能继续执行工具，模型返回最终 WorkerResult 后循环立即结束。这个上限只是防止模型不断调用工具而永远不提交结果；达到上限时 BaseAgent 产生明确的“Agent 迭代超过最大迭代次数”错误，任务再按 Orchestrator 的任务级重试规则处理。

BaseAgent 内还有两种较低层的重试，它们不能与任务 attempt 混为一谈。LLM 调用失败会按照全局 `max_retries` 重试，工具调用失败也会在同一上限内重试并最终形成失败 ToolResult；外层 Orchestrator 默认还允许一次任务重试，也就是最多两个完整 attempt。一次任务的可见 attempt 数、一次 attempt 内的 ReAct 轮数，以及每一轮 LLM 或工具调用的基础设施重试，是三层不同的计数边界。

### Worker 构造与执行源码走读

TeamFlow 先复制 AgentConfig，只覆盖 Worker 的 max_iterations，再定义 Worker factory。每次 factory 调用都会创建新的 TaskWorker 与 Memory，但 `policy.tools_for()` 返回的底层工具实例仍来自当前 Flow 的共享 tools。graph id、agent id、task 和 attempt 都在构造时固定，因此 Worker 发出的每个工具事件可以直接带上不可混淆的执行上下文。

```python
worker_config = agent_config.model_copy(
    update={
        "max_iterations": (
            agent_config.team_max_worker_iterations
        ),
    }
)

def worker_factory(graph_id, agent_id, task, attempt):
    return TaskWorker(
        uow_factory=uow_factory,
        session_id=session_id,
        agent_config=worker_config,
        llm=llm,
        json_parser=json_parser,
        tools=policy.tools_for(task.capability),
        memory=Memory(),
        allowed_tool_names=(
            policy.allowed_names(task.capability)
        ),
        graph_id=graph_id,
        task=task,
        agent_id=agent_id,
        attempt=attempt,
    )
```

`model_copy(update=...)` 不改变注入 Runner 的原始 agent_config，所以 React Flow 继续读取它自己的 max_iterations。TaskWorker 收到的是完整 TeamTask 对象，但后面构造 query 时只挑选规划字段，运行态不会传给 LLM。每次任务重试都会再次调用 factory，因而 attempt=2 与 attempt=1 不共享 Memory。

Worker 的系统提示词要求它只处理一个节点，不改全局图，也不向用户提问。最终输出格式和 WorkerResult 对齐，sources 与 artifacts 的真实性要求目前写在这段提示中。它没有按 capability 动态生成不同角色文案，能力差异来自实际暴露的工具集合。

```python
WORKER_SYSTEM_PROMPT = """你只负责一个 DAG 节点。只能使用已暴露的工具。
不要改变全局计划，不要向用户提问。最后只输出 JSON：
{"success":true,"summary":"...","sources":[{"title":"...","url":"https://...","snippet":"..."}],"artifacts":[]}
sources 只能引用本节点成功工具结果中真实出现的 URL；artifacts 只能引用本节点真实生成或观察到的文件路径。
"""
```

“不要向用户提问”与 Team 没有 MessageTool相互一致，Worker 不会进入单 Agent 的 waiting 交互。提示词允许 success=false，但 summary 仍必须解释失败原因；Orchestrator 会把该 summary 作为异常文本。当前没有把 dependencies 的完整工具轨迹交给 Worker，所以它只能基于 dependency_results 中已压缩的来源与摘要工作。

`TaskWorker.execute()` 先把依赖结果转成 JSON，再进入 BaseAgent.invoke() 的异步事件循环。ToolEvent 会被打标签后 emit，ErrorEvent 变成异常交给 Orchestrator，最终 MessageEvent 则被解析并验证成 WorkerResult。没有最终消息时明确抛 `worker produced no result`，不会返回 success=false 的虚构对象。

```python
async def execute(
    self,
    *,
    goal: str,
    dependency_results: dict[str, WorkerResult],
    attachments: list[str],
    emit: EmitEvent,
) -> WorkerResult:
    query = json.dumps(
        {
            "goal": goal,
            "task": self._task.model_dump(
                mode="json",
                include={
                    "id",
                    "description",
                    "dependencies",
                    "capability",
                    "success_criteria",
                },
            ),
            "dependency_results": {
                key: value.model_dump(mode="json")
                for key, value in dependency_results.items()
            },
            "attachments": attachments,
        },
        ensure_ascii=False,
    )

    async for event in self.invoke(query):
        if isinstance(event, ToolEvent):
            event.graph_id = self._graph_id
            event.task_id = self._task.id
            event.agent_id = self._agent_id
            event.attempt = self._attempt
            await emit(event)
        elif isinstance(event, ErrorEvent):
            raise RuntimeError(event.error)
        elif isinstance(event, MessageEvent):
            parsed = await self._json_parser.invoke(event.message)
            return WorkerResult.model_validate(parsed)
    raise RuntimeError("worker produced no result")
```

`include` 集合是有意的最小输入，不会把上次错误、assigned_agent_id 或 attempt_count 暗示给模型。dependency_results 只包含 Orchestrator 已经确认有 result 的直接依赖，键保留 task id，使分析 Worker 能引用对应上游。附件只是沙箱路径，Worker 是否真正读它们取决于 capability 是否允许文件工具。

BaseAgent 的核心 ReAct 循环决定一次 Worker attempt 内为什么可能产生多次工具调用。首次 `_invoke_llm()` 得到工具调用后，循环依次发 calling ToolEvent、执行工具、发 called ToolEvent，把 ToolResult 作为 tool message 再交给模型。模型不再返回 tool_calls 时跳出循环，最后把 content 包成 MessageEvent，由上面的 TaskWorker 解析成 WorkerResult。

```python
message = await self._invoke_llm(
    [{"role": "user", "content": query}],
    format,
)

for _ in range(self._agent_config.max_iterations):
    if not message or not message.get("tool_calls"):
        break

    tool_messages = []
    for tool_call in message["tool_calls"]:
        if not tool_call.get("function"):
            continue

        tool_call_id = tool_call["id"] or str(uuid.uuid4())
        function_name = tool_call["function"]["name"]
        function_args = await self._json_parser.invoke(
            tool_call["function"]["arguments"]
        )
        tool = self._get_tool(function_name)

        yield ToolEvent(
            tool_call_id=tool_call_id,
            tool_name=tool.name,
            function_name=function_name,
            function_args=function_args,
            status=ToolEventStatus.CALLING,
        )
        result = await self._invoke_tool(
            tool,
            function_name,
            function_args,
        )
        yield ToolEvent(
            tool_call_id=tool_call_id,
            tool_name=tool.name,
            function_name=function_name,
            function_args=function_args,
            function_result=result,
            status=ToolEventStatus.CALLED,
        )
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "function_name": function_name,
            "content": result.model_dump_json(),
        })

    message = await self._invoke_llm(tool_messages)
else:
    yield ErrorEvent(
        error=(
            "Agent迭代超过最大迭代次数: "
            f"{self._agent_config.max_iterations}, 任务处理失败"
        )
    )
```

BaseAgent 当前把单次 LLM 返回的 tool_calls 截断为第一项，所以一个 Worker 的工具调用在自身 ReAct 循环内是顺序的；多 Worker 并行来自 Orchestrator 同时运行多个 BaseAgent。for-else 只有循环耗尽且从未 break 时执行，因此模型第 50 轮仍要求工具就产生迭代上限错误。TaskWorker 收到这个 ErrorEvent 后抛 RuntimeError，Orchestrator 才会把整个节点 attempt 标为失败或 retrying。

LLM 与工具的基础重试发生在循环内部，源码分别如下。LLM 三次都异常后抛 RuntimeError，工具三次都异常后返回 `ToolResult(success=False)` 给模型决定是否调整策略。二者都不会直接增加 TeamTask.attempt_count，因为 attempt 只由更外层 Orchestrator 修改。

```python
for _ in range(self._agent_config.max_retries):
    try:
        message = await self._llm.invoke(
            messages=self._memory.get_messages(),
            tools=self._get_available_tools(),
            response_format=response_format,
            tool_choice=self._tool_choice,
        )
        # 省略响应规范化
        return filtered_message
    except Exception as e:
        error = str(e)
        await asyncio.sleep(self._retry_interval)

raise RuntimeError(
    "调用语言模型失败, 已达到最大重试次数"
    f"({self._agent_config.max_retries}): {error}"
)

for _ in range(self._agent_config.max_retries):
    try:
        return await tool.invoke(tool_name, **arguments)
    except Exception as e:
        err = str(e)
        await asyncio.sleep(self._retry_interval)

return ToolResult(success=False, message=err)
```

这里省略的是 LLM 响应字段规范化，不改变循环或错误边界。工具失败返回 ToolResult 是既有 ReAct 语义，因为模型可能换参数、换查询或根据失败作出最终判断；它不是把失败标成成功。只有最终 WorkerResult.success 为 true，外层节点才会 completed。

## 九、Orchestrator 用 ready 集合驱动 DAG，而不是为所有任务同时创建 Agent

`TeamOrchestrator.run()` 开始时只把图状态改成 running，并不会立刻把所有 pending 节点实例化成 Worker。每轮循环先传播依赖失败，再调用 `ready_tasks()` 计算当前可运行集合；只有状态仍为 pending 且所有 dependencies 都已经 completed 的节点才会进入 ready。这个规则意味着依赖节点即使已经结束但状态是 failed、skipped 或 cancelled，下游也绝不会被误当作可运行任务。

`propagate_skipped()` 会收集 failed、skipped 和 cancelled 节点 id，然后反复扫描 pending 节点。只要一个 pending 节点依赖集合与 blocked 集合相交，它就被标记为 skipped，error 固定为 `dependency_failed`，并继续加入 blocked 集合。循环直到没有新增节点，所以失败能够跨越多层依赖向下传播，而不只跳过直接子节点。

当 ready 集合中存在并发安全节点时，调度器优先选择其中最多 `team_max_workers` 个组成一个批次，当前默认并发上限是 3。它使用 `asyncio.gather()` 等待这一批节点全部结束，然后重新计算 skipped 和 ready，而不是提前把下一层节点塞入正在运行的批次。批次内 ToolEvent 可以交错到达，但每个节点的状态仍保存在各自 `TeamTask` 对象中，不共享 Worker Memory。

如果 ready 集合没有并发安全节点，调度器只取 `ready[0]` 串行执行。即使同时有多个 browser、shell 或 file_write 节点，它们也会按 Planner 输出中的任务数组顺序一个个运行。若 ready 中同时存在安全与非安全节点，安全节点批次会先执行，非安全节点留到下一轮，这是一项确定性的调度优先级，而不是基于任务耗时动态抢占。

调度器给每个批次中的任务分配 `worker-{slot}`，slot 从 1 开始。这个编号表示当前批次中的并发槽，并不保证贯穿整张图全局唯一，因此后续批次可以再次出现 worker-1。事件真正唯一的执行归属需要结合 graph_id、task_id 和 attempt 一起理解，前端也不能只按 agent_id 合并两个不同任务。

节点执行前，Orchestrator 从依赖 id 找到已完成 `TeamTask.result`，构造 dependency_results 后再进入 attempt 循环。每次 attempt 先写 attempt_count、running 状态、清空当前 error，并发出 `TeamTaskEvent`；随后通过 `asyncio.wait_for()` 给整个 Worker 执行设置超时。默认 `team_task_timeout_seconds` 是 300 秒，这个超时覆盖该 attempt 内所有模型轮次和工具调用，而不是给每个单独工具各 300 秒。

Worker 返回 `success=true` 时，结果写入 task.result，状态变成 completed，并再次发出任务事件。WorkerResult 明确返回 `success=false`、抛出异常、产生非法 JSON 或收到 Agent ErrorEvent 时，错误文本写入 task.error；超时则统一记为 `task_timeout`。代码没有用空摘要或默认成功结果绕过失败，只有获得通过模型校验且 success 为真的 WorkerResult 才能完成节点。

任务级 `team_max_task_retries` 当前默认是 1，因此 attempt 范围是 1 到 2。第一次失败后任务状态变成 retrying 并发出事件，第二次失败后变成 failed 并发出最终任务事件；如果配置改成零，就只执行一次。重试是针对整个节点执行重新创建 Worker，不是在原 Memory 上从失败工具调用后继续，也不会改变 DAG 依赖关系或把失败节点改派给另一种 capability。

当 ready 为空时，调度器先检查所有节点是否都处于 completed、failed、skipped 或 cancelled 之一。全部终态意味着图的执行循环正常结束；仍有非终态节点则说明状态机无法继续推进，图状态先记为 failed，graph.error 记为 `scheduler_deadlock`。合法 DAG 理论上不应出现这种死锁，因此这条错误表达运行时状态不一致，而不是用顺序执行掩盖图错误。

循环结束后，`finalize_graph()` 根据节点状态确定图的最终状态。只要存在 cancelled 节点，整图就是 cancelled；全部节点 completed 时是 completed；至少一个完成且同时存在 failed 或 skipped 时是 partial；其余情况是 failed。这个规则允许独立分支部分成功后仍由 Synthesizer 汇总有效结果，同时不会把“所有任务失败”误报成 partial。

### Orchestrator 状态机源码走读

ready 计算没有维护另一份调度队列，而是每轮从图的权威状态重新派生。先收集所有 completed id，再筛选 status 仍为 pending 且 dependencies 全部属于 completed 集合的节点。failed 节点不在 completed 中，因此仅靠 ready 计算不会错误释放其下游。

```python
def ready_tasks(graph: TaskGraph) -> list[TeamTask]:
    completed = {
        task.id
        for task in graph.tasks
        if task.status is TeamTaskStatus.COMPLETED
    }
    return [
        task
        for task in graph.tasks
        if task.status is TeamTaskStatus.PENDING
        and set(task.dependencies).issubset(completed)
    ]
```

这里使用 `issubset()` 同时覆盖根节点和多依赖节点：空依赖永远是 completed 的子集，两个依赖则必须同时出现。返回顺序沿用 graph.tasks 顺序，后面选择 `ready[0]` 时因此具有稳定行为。函数只读图，不创建 Worker，也不改变任务状态。

失败传播单独由 `propagate_skipped()` 完成。初始 blocked 集合包含 failed、skipped 与 cancelled，while 循环反复扫描 pending 节点，把任何依赖 blocked 的节点改成 skipped。新 skipped 节点继续加入 blocked，所以 task_1 失败可以依次跳过 task_2 和依赖 task_2 的 task_3。

```python
def propagate_skipped(graph: TaskGraph) -> list[TeamTask]:
    blocked = {
        task.id
        for task in graph.tasks
        if task.status in {
            TeamTaskStatus.FAILED,
            TeamTaskStatus.SKIPPED,
            TeamTaskStatus.CANCELLED,
        }
    }
    changed: list[TeamTask] = []
    progress = True
    while progress:
        progress = False
        for task in graph.tasks:
            if (
                task.status is TeamTaskStatus.PENDING
                and blocked.intersection(task.dependencies)
            ):
                task.status = TeamTaskStatus.SKIPPED
                task.error = "dependency_failed"
                blocked.add(task.id)
                changed.append(task)
                progress = True
    return changed
```

函数返回 changed，而不是只原地修改图，是为了让 Orchestrator 为每个新 skipped 节点发 TeamTaskEvent。已经 skipped 的节点不会再次加入 changed，因为筛选条件严格要求 pending。错误固定为 dependency_failed，使用户能够区分“节点自己执行失败”和“节点根本未执行”。

单节点执行首先分配逻辑 Worker slot，并从直接依赖中取出非空 result。attempt 循环的上界是 `max_retries + 2`，Python range 右端不包含，所以 max_retries=1 时实际得到 attempt 1 和 2。每次开始都清空旧 error、设置 running 并先 emit，确保前端先建立任务块再收到该 Worker 的 ToolEvent。

```python
async def _execute_task(
    self,
    graph: TaskGraph,
    task: TeamTask,
    slot: int,
    attachments: list[str],
    emit: EmitEvent,
) -> None:
    task.assigned_agent_id = f"worker-{slot}"
    dependency_results = {
        dependency: dependency_task.result
        for dependency in task.dependencies
        if (
            dependency_task := graph.task_by_id(dependency)
        ).result is not None
    }

    for attempt in range(1, self._max_retries + 2):
        task.attempt_count = attempt
        task.status = TeamTaskStatus.RUNNING
        task.error = None
        await self._emit_task(graph, task, emit)
```

Walrus 表达式只调用一次 `task_by_id()`，并把同一个依赖任务的 result 放进字典。理论上 ready 规则已经保证每个依赖 completed 且 result 存在，但这里仍按实际非空 result 组装，Worker 输入不会出现 JSON null 的假结果。assigned_agent_id 在 attempt 循环外设置，所以同一任务的第二次 attempt 沿用同一个逻辑 slot。

真正执行部分用 `asyncio.wait_for()` 包住 Worker.execute()，超时覆盖整个 attempt。Worker 返回 success=false 时主动抛 RuntimeError，使它与 JSON 解析错误、Agent ErrorEvent 和工具链异常进入同一个任务失败分支。成功路径先保存 result，再发 completed 事件并 return，从而不再进入下一 attempt。

```python
try:
    worker = self._worker_factory(
        graph.id,
        task.assigned_agent_id,
        task,
        attempt,
    )
    result = await asyncio.wait_for(
        worker.execute(
            goal=graph.goal,
            dependency_results=dependency_results,
            attachments=attachments,
            emit=emit,
        ),
        timeout=self._timeout_seconds,
    )
    if not result.success:
        raise RuntimeError(
            result.summary or "worker reported failure"
        )
    task.result = result
    task.status = TeamTaskStatus.COMPLETED
    await self._emit_task(graph, task, emit)
    return
except asyncio.CancelledError:
    task.status = TeamTaskStatus.CANCELLED
    task.error = "cancelled"
    raise
except TimeoutError:
    task.error = "task_timeout"
except Exception as exc:
    task.error = str(exc)

if attempt <= self._max_retries:
    task.status = TeamTaskStatus.RETRYING
    await self._emit_task(graph, task, emit)
else:
    task.status = TeamTaskStatus.FAILED
    await self._emit_task(graph, task, emit)
    return
```

CancelledError 被单独捕获后重新抛出，不能落入普通 Exception 再做任务重试。TimeoutError 被规范成稳定的 task_timeout，而其他异常保留实际字符串，最终整图失败汇总会展示它。第一次失败时 emit retrying，下一轮循环开头又 emit running，因此前端能看到状态转换并用 attempt 区分工具记录。

主循环每一轮先传播 skipped 并发事件，再重新计算 ready。没有 ready 时，全部任务终态代表正常收敛，否则写 scheduler_deadlock 后 break。deadlock 分支不挑一个 pending 节点强行执行，因为那会违反已经声明的依赖。

```python
async def run(
    self,
    graph: TaskGraph,
    attachments: list[str],
    emit: EmitEvent,
) -> TaskGraph:
    graph.status = TaskGraphStatus.RUNNING
    terminal_statuses = {
        TeamTaskStatus.COMPLETED,
        TeamTaskStatus.FAILED,
        TeamTaskStatus.SKIPPED,
        TeamTaskStatus.CANCELLED,
    }

    while True:
        for skipped in propagate_skipped(graph):
            await self._emit_task(graph, skipped, emit)

        ready = ready_tasks(graph)
        if not ready:
            if all(
                task.status in terminal_statuses
                for task in graph.tasks
            ):
                break
            graph.status = TaskGraphStatus.FAILED
            graph.error = "scheduler_deadlock"
            break
```

并发分支先筛选 parallel-safe，再截取 max_workers 个节点交给 gather。只要当前 ready 中有安全节点，代码执行完这一批就 continue，重新从失败传播和 ready 计算开始；非安全节点不会与这批混跑。不存在安全节点时只执行 ready[0]，这就是共享 browser、shell 和写文件任务串行化的实际落点。

```python
parallel_safe = [
    task
    for task in ready
    if self._is_parallel_safe(task.capability)
]
if parallel_safe:
    batch = parallel_safe[: self._max_workers]
    await asyncio.gather(
        *(
            self._execute_task(
                graph,
                task,
                slot + 1,
                attachments,
                emit,
            )
            for slot, task in enumerate(batch)
        )
    )
    continue

await self._execute_task(
    graph,
    ready[0],
    1,
    attachments,
    emit,
)
```

`asyncio.gather()` 默认任一协程抛未处理异常时向上抛，但 `_execute_task()` 已把普通 Worker 错误转成任务状态，只有取消或编排器自身异常会穿透。slot 从 enumerate 的零开始值加一，产生 worker-1、worker-2、worker-3。下一批再次 enumerate，所以 agent id 是并发槽标签而不是全图唯一 Worker 主键。

最终状态归约直接读取终态任务，不依赖某个计数器是否正确递增。cancelled 优先级最高，全部完成其次，部分成功要求 completed 大于零且存在 failed/skipped，其余归为 failed。Orchestrator 无论此前因正常终态还是 deadlock 离开循环，都会调用这段函数并返回同一个 graph 对象。

```python
def finalize_graph(graph: TaskGraph) -> TaskGraphStatus:
    completed = sum(
        task.status is TeamTaskStatus.COMPLETED
        for task in graph.tasks
    )
    failed = any(
        task.status in {
            TeamTaskStatus.FAILED,
            TeamTaskStatus.SKIPPED,
        }
        for task in graph.tasks
    )
    cancelled = any(
        task.status is TeamTaskStatus.CANCELLED
        for task in graph.tasks
    )

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

一个图有独立成功分支和失败分支时会 partial，但只有 skipped 而零 completed 时仍 failed。graph.error 不参与这个计算，所以 scheduler_deadlock 之前写入的 failed 可能被终态集合重新归约；正常合法状态下，无法推进的图通常仍会落到 failed。这个实现把“图为什么失败”放在 error，把“图最后是什么状态”放在 status，两者职责分离。

## 十、TeamFlow 用生产者与事件队列把并发执行转换成一个可消费的异步事件流

`TeamFlow.invoke()` 是 Planner、Orchestrator 和 Synthesizer 的组合边界，也是 Runner 眼中唯一的 Team Flow。DAG 构建成功后，它先产生 `TitleEvent` 更新会话标题，再产生一份深拷贝的初始 `TaskGraphEvent`，让前端立即知道完整节点和依赖执行的总体范围。深拷贝很重要，因为 Orchestrator 会继续原地修改运行时图，已经发出的历史事件不能跟着同一对象被静默改写。

Orchestrator 需要一边运行多个 Worker，一边把任务与工具事件持续交给外层异步生成器。为此 `QueuedEventEmitter` 内部维护一个 `asyncio.Queue[BaseEvent | None]`，Worker 和 Orchestrator 调用 `emit()` 充当生产者，TeamFlow 的 while 循环调用 `get()` 充当单一消费者。生产函数被 `asyncio.create_task()` 启动，结束时无论成功、失败还是取消都会在 finally 中关闭 emitter，并向队列放入 `None` 作为结束哨兵。

队列当前没有设置 `maxsize`，因此 `put()` 通常不会因消费者速度而形成背压。正常 Team 规模最多五个节点，事件数量有限，这对最小实现足够简单；如果以后允许大量节点、长时间 Shell 输出或高频浏览器事件，内存队列可能累积过多事件。当前代码没有丢弃、压缩或批量合并 ToolEvent，调用态和完成态都会按产生顺序进入后续链路。

TeamFlow 消费到 `None` 后会 await producer，以便取得 Orchestrator 返回的最终 `TaskGraph`。若 producer 自身抛出未被任务级逻辑处理的异常，Flow 会把图标为 failed、写入 `scheduler_error: ...`，发送最终图快照并产生 `Team 调度失败` ErrorEvent。这个分支用于表达编排器本身崩溃，与某个 Worker 正常失败后导致图 failed 是两条不同错误路径。

正常调度结束后，无论图是 completed、partial 还是 failed，TeamFlow 都先产生第二份完整 `TaskGraphEvent`。初始图用于建立计划，增量 TeamTaskEvent 用于实时更新，最终图用于刷新恢复时重建权威状态，这三类事件合起来避免前端只能依赖局部补丁猜测最后结果。图快照也会被持久化到会话事件表，所以页面刷新后仍能恢复完整任务清单。

completed 和 partial 图会进入 `TeamSynthesizerAgent`。Synthesizer 收到整张图的 JSON，包括每个节点的成功摘要、来源、产物、失败错误和跳过信息，再按照提示词生成 `FinalTeamResponse`；它不持有工具，也不能继续搜索或修改文件。输出中的 attachment 路径会被转换成 `File(filepath=...)`，然后由 Runner 复用原有附件同步逻辑上传到对象存储，最终产生普通 assistant MessageEvent。

Synthesizer 最多尝试两次，每次都由 factory 创建新的 Agent 和新的 Memory。任意异常会记录为 last_error，第二次仍失败时产生 `Team 汇总失败` ErrorEvent，而不会用节点摘要在后端机械拼一个看似完整的答案。这里保留有界重试是为了处理模型 JSON 输出或瞬时调用失败，但不会改变已经结束的 DAG，也不会重新执行 Worker。

当前 Synthesizer 的“不得新增事实、来源或附件”和“必须明确说明失败与跳过节点”主要由系统提示词保证。后端已经去掉把最终来源与 Worker 工具结果逐一比对的运行时校验，也没有在 partial 响应后强制追加一段失败节点清单。这样减少了证据兜底代码和重复文本，但意味着模型若违反提示词，后端不会再次纠正其表述。

整图最终为 failed 时不会调用 Synthesizer，因为没有足够的成功结果可供汇总。当前工作区代码直接构造一个 ErrorEvent，首行为“Team 执行失败”，之后逐项列出所有 `task.error` 非空节点的任务描述与具体错误。由此用户能看到是哪些任务达到迭代上限、超时、输出非法结果或因依赖失败而跳过，而不是只看到抽象的“所有 Team Task 均失败”。

失败汇总只枚举任务级 error，当前没有把 `graph.error` 单独追加进去。一般 Worker 失败和 dependency_failed 都会写到任务上，因此能形成具体列表；如果罕见的 `scheduler_deadlock` 只存在于 graph.error 且没有任务 error，消息可能只剩“Team 执行失败”标题。这个边界应视为当前错误表达仍可改进的地方，而不能误解成调度错误已经被完整展示。

图失败或汇总失败时 Flow 以 ErrorEvent 结束，不再额外发送 DoneEvent；成功或 partial 汇总成功后则发送 DoneEvent。`AgentService.chat()` 把 ErrorEvent 和 DoneEvent 都视为 SSE 终止事件，前端也会把两者都结束为非 running 状态。Done 表示正常完成，Error 表示本轮已经停止但结果失败，两者不能仅凭“流关闭了”互相替代。

### TeamFlow 事件桥接与汇总源码走读

`QueuedEventEmitter` 是一个很小的生产者/消费者桥，close 通过放入 None 结束消费。`_closed` 防止 producer 结束后还有 Worker 悄悄写事件，第二次 close 则保持幂等。Queue 没有 maxsize，所以 emit 的 await 只是异步接口统一，不提供容量背压。

```python
class QueuedEventEmitter:
    def __init__(self):
        self._queue: asyncio.Queue[
            BaseEvent | None
        ] = asyncio.Queue()
        self._closed = False

    async def emit(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("event emitter is closed")
        await self._queue.put(event)

    async def get(self) -> BaseEvent | None:
        return await self._queue.get()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)
```

Orchestrator 的 `emit` 参数实际绑定到这个对象的 emit 方法，因此多个并发 Worker 都写同一个队列。TeamFlow 本身是唯一 get 消费者，取出一条才向 Runner yield 一条，外部始终看见线性事件序列。None 不会向 Runner 传输，它只是 TeamFlow 内部用来知道 producer 已结束。

DAG 构建成功后，Flow 先发标题和初始图，再创建 producer。`produce()` 的 finally 无条件 close emitter，保证 Orchestrator 抛错或被取消时消费者不会永久等在 get。`self._producer` 保存 asyncio Task 引用，既用于执行完成后 await 结果，也用于用户取消时主动 cancel。

```python
yield TitleEvent(title=self._graph.title)
yield TaskGraphEvent(
    graph=self._graph.model_copy(deep=True)
)

emitter = QueuedEventEmitter()

async def produce() -> TaskGraph:
    try:
        return await self._orchestrator.run(
            self._graph,
            message.attachments,
            emitter.emit,
        )
    finally:
        await emitter.close()

self._producer = asyncio.create_task(produce())
```

这里对初始 graph 使用 deep copy，因为随后 produce 会把 `self._graph.tasks` 原地改成 running、completed 等状态。若直接发送同一对象引用，进程内测试或未来其他消费者可能看到历史事件内容随运行态变化。标题先于图发送，Runner 能先更新 Session.title，前端随后建立完整计划面板。

消费循环一直读到 None，然后再 await producer 取得最终图。producer 的普通异常会被转换成 scheduler_error 图快照和 ErrorEvent，CancelledError 则继续向外传播给 Runner 的取消路径。正常返回后再次发送深拷贝的最终图，使页面刷新只依赖持久化事件也能得到最终权威状态。

```python
while True:
    event = await emitter.get()
    if event is None:
        break
    yield event

try:
    self._graph = await self._producer
except asyncio.CancelledError:
    raise
except Exception as exc:
    self._graph.status = TaskGraphStatus.FAILED
    self._graph.error = f"scheduler_error: {exc}"
    self._done = True
    yield TaskGraphEvent(
        graph=self._graph.model_copy(deep=True)
    )
    yield ErrorEvent(error=f"Team 调度失败: {exc}")
    return

yield TaskGraphEvent(
    graph=self._graph.model_copy(deep=True)
)
```

TaskEvent 和 ToolEvent 的相对次序由它们进入 Queue 的时刻决定，同一 Worker 中 running 一定先于工具，但并行 Worker 之间可以交错。消费者不按 agent id 排序，也不等待某个任务全部事件发完才处理另一个任务，这正是前端需要 graph/task 元数据的原因。producer 已 close 队列后再 await 可以取到返回值，同时确保队列里的尾部事件已经全部 yield。

图 completed 或 partial 时，Flow 创建 Synthesizer 并最多尝试两次。合法 FinalTeamResponse 被转换成 assistant MessageEvent，其中 attachments 的每个字符串先包装为 File(filepath=path)，Runner 后面再负责同步存储。第二次仍异常才发 Team 汇总失败，失败时没有后端拼接默认答案。

Synthesizer 的系统提示词只有结果汇总职责，并显式禁止工具与新增事实。它要求保留来源 Markdown 链接和说明失败节点，但最终是否做到仍取决于模型输出，因为后端只验证 FinalTeamResponse 的 message 与 attachments 结构。`_tool_choice = "none"` 和空 tools 数组进一步从调用层禁止它补搜资料。

```python
SYNTHESIZER_SYSTEM_PROMPT = """你负责汇总已经完成的 DAG 结果，不调用工具、不新增事实、来源或附件。
明确说明失败和跳过节点，保留来源 Markdown 链接，并只输出 JSON：
{"message":"...","attachments":[]}
"""

class TeamSynthesizerAgent(BaseAgent):
    name = "team_synthesizer"
    _system_prompt = SYNTHESIZER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def synthesize(
        self,
        graph: TaskGraph,
    ) -> FinalTeamResponse:
        async for event in self.invoke(
            graph.model_dump_json()
        ):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(
                    event.message
                )
                return FinalTeamResponse.model_validate(parsed)
        raise RuntimeError(
            "synthesizer produced no response"
        )
```

输入使用整张 graph 的 JSON，所以每个 task 的 description、status、result 和 error 都可见，Synthesizer 不需要读取 Session 历史。它不会把自己的内部 MessageEvent直接向外透传，而是先转成 FinalTeamResponse，TeamFlow 再构造 role=assistant 的正式事件。这样即使模型返回了未声明字段，也会在用户看到之前进入汇总重试。

```python
if self._graph.status in {
    TaskGraphStatus.COMPLETED,
    TaskGraphStatus.PARTIAL,
}:
    last_error = None
    for _ in range(2):
        try:
            final = await (
                self._synthesizer_factory()
                .synthesize(self._graph)
            )
            yield MessageEvent(
                role="assistant",
                message=final.message,
                attachments=[
                    File(filepath=path)
                    for path in final.attachments
                ],
            )
            break
        except Exception as exc:
            last_error = str(exc)
    else:
        self._done = True
        yield ErrorEvent(
            error=f"Team 汇总失败: {last_error}"
        )
        return
```

factory 放在循环里面调用，意味着两次汇总使用两份空白 Memory，不会在第一份非法 JSON 后继续污染上下文。Synthesizer 输入是 `graph.model_dump_json()`，它能看到 partial 中的失败和 skipped 节点，不需要另查数据库。成功 break 只跳出重试循环，代码随后仍会把 Flow 标记 done 并发送 DoneEvent。

全图 failed 的代码直接从任务对象提取具体 error。列表推导只包含 error 非空节点，所以 completed 节点不会出现在失败清单，skipped 的 dependency_failed 会显示出来。当前没有把 graph.error 合并到首行，这正是前文指出 scheduler_deadlock 可能只剩标题的原因。

```python
elif self._graph.status is TaskGraphStatus.FAILED:
    self._done = True
    yield ErrorEvent(
        error="\n".join(
            ["Team 执行失败："]
            + [
                f"- {task.description}：{task.error}"
                for task in self._graph.tasks
                if task.error
            ]
        )
    )
    return

self._done = True
yield DoneEvent()
```

failed 分支 return，因此不会落到最后的 DoneEvent。completed 和 partial 只有在 Synthesizer 已产生 assistant message 后才 Done，前端不会先结束再收到回答。Flow 外层 finally 还会取消尚未结束的 producer 并 gather，保证 SSE 消费者提前退出时后台 Worker 不继续运行。

## 十一、单点事件写入保证并发 Worker 不会同时修改 Redis 和会话聚合

Team Worker 不直接写 Redis 或数据库的核心原因，不是数据库绝对不能承受并发，而是同一个会话需要一个确定的事件序列。多个 Worker 若各自先后写两种存储，很容易出现 Redis 顺序与数据库顺序不一致、同一工具 calling/called 被交叉覆盖、事务失败后只有一侧落盘，或者并发更新未读数和最新消息产生竞争。当前实现把并发限制在“生成事件”阶段，所有外部副作用集中回 `AgentTaskRunner._put_and_add_event()` 顺序执行。

`_put_and_add_event()` 先把事件 JSON 写入 Task 的 Redis output stream，获得 stream event id 后把 id 回填到事件对象，再通过 UoW 把同一事件加入 Session。SSE 消费者理论上可能在数据库事务完成前极短暂地看到 Redis 中的新事件，但事件 id 在两处保持一致，刷新后可以按持久化事件恢复。当前没有把 Redis 与数据库放进分布式事务，因此单点写入减少了竞态，却不等于两种存储具备原子提交保证。

Runner 对所有 Flow 事件执行相同的持久化路径，TeamTaskEvent 和 TaskGraphEvent 没有另开一套仓库。TitleEvent 还会更新 Session.title，assistant MessageEvent 会更新 latest_message 并增加未读计数，WaitEvent 会把会话状态改成 waiting。Team 当前不会产生 WaitEvent，因为没有 MessageTool，但保留这条公共路径使原有 React Flow 行为不受模式扩展影响。

ToolEvent 在落盘之前先经过 `_handle_tool_event()` 做前端展示增强。浏览器完成事件会截取当前页面并上传截图，搜索完成事件提取结构化结果，Shell 事件读取控制台记录，文件事件读取内容并把沙箱文件同步到对象存储，MCP 与 A2A 事件提取外部返回数据。Team 与单 Agent 共用这段增强，所以 Team 的折叠任务块仍能打开浏览器截图、搜索结果、终端输出和文件预览。

工具展示增强被一层异常捕获包围，失败时记录日志但不改变原始工具调用结果，也不让业务节点因为“生成预览失败”重新执行。它属于既有 Runner 的可视化附加处理，不是判断 Worker 成功的依据；Worker 看到的是工具真实返回，前端只是可能缺少截图或内容预览。这个 catch 的目标是隔离非关键展示副作用，与用默认业务结果掩盖 Worker 错误不是同一种兜底。

用户附件在 Runner 开始 Flow 前从对象存储下载并上传到沙箱，成功后 Message 对象只携带沙箱 filepath。Planner 和 Worker 因而看到统一的 `/home/ubuntu/upload/...` 路径，不需要理解数据库文件 id 或对象存储 key。反方向上，Worker 生成的 assistant 附件和文件工具访问路径会由 Runner 同步到对象存储并加入会话文件集合，前端继续使用原有文件接口查看。

MCPTool 和 A2ATool 由 Runner 创建，并在执行任务的同一个 asyncio Task 中初始化和清理。底层 streamable HTTP 使用 anyio cancel scope，初始化与退出若跨 Task 可能抛出运行时错误，所以清理放在 `AgentTaskRunner.invoke()` 的 finally，而不是另起后台清理任务。React Flow 与一轮 Team Flow 共享这些 Tool 实例，这也是 MCP 和 A2A 节点当前必须串行的原因之一。

### Runner 单点持久化源码走读

所有 Flow 事件最终都会执行下面这个方法。第一行写 Redis output stream 并取得真正用于续传的 id，第二步才把带 id 的事件加入数据库 Session。Worker 代码无法引用这个方法，它只持有 emit 回调，因此并发协程没有直接落盘入口。

```python
async def _put_and_add_event(
        self,
        task: Task,
        event: Event,
) -> None:
    event_id = await task.output_stream.put(
        event.model_dump_json()
    )
    event.id = event_id

    async with self._uow:
        await self._uow.session.add_event(
            self._session_id,
            event,
        )
```

Redis 写在前面是因为 event id由 Stream 生成，数据库必须保存同一个 id 才能让历史事件与续传游标对应。两步不在同一事务中，所以它保证顺序而非跨存储原子性。由于 `_run_flow()` 的 async for 在一个 Runner 协程中逐条调用它，即使 Queue 中的事件源自并发 Worker，真正写出时也是串行的。

Runner 消费 Flow 时，每个事件先落盘，再根据事件类型更新 Session 的派生字段。TitleEvent 改标题，assistant MessageEvent 改 latest_message 并增加未读数，WaitEvent 切换 waiting 并立即 return。TaskGraphEvent 和 TeamTaskEvent 不需要特殊分支，因为其完整内容已经由 `_put_and_add_event()` 保存，Session status 在请求开始和最终退出处管理。

```python
async with aclosing(
    self._run_flow(message_obj, mode)
) as flow_events:
    async for event in flow_events:
        await self._put_and_add_event(task, event)

        if isinstance(event, TitleEvent):
            async with self._uow:
                await self._uow.session.update_title(
                    self._session_id,
                    event.title,
                )
        elif isinstance(event, MessageEvent):
            async with self._uow:
                await self._uow.session.update_latest_message(
                    self._session_id,
                    event.message,
                    event.created_at,
                )
                await self._uow.session.increment_unread_message_count(
                    self._session_id
                )
        elif isinstance(event, WaitEvent):
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id,
                    SessionStatus.WAITING,
                )
            return
```

这段顺序意味着 SSE 客户端可能在数据库派生字段更新前看到刚写入 Redis 的事件，但不会看到 calling/called 被数据库以另一顺序保存。Team 的 assistant MessageEvent 也走原 MessageEvent 分支，因此会话列表能显示最终汇总文本。当前 Team 没有 WaitEvent，分支仍保留给 React Flow。

工具预览增强发生在 `_put_and_add_event()` 之前，因为增强后的 tool_content 需要同时进入 Redis 和数据库。下面代码展示 called 事件的部分处理：搜索结果转成 SearchToolContent，文件读取当前沙箱内容并同步对象存储，浏览器则补截图。它不改变 `function_result`，所以预览失败不会改写 Worker 实际拿到的 ToolResult。

```python
async def _handle_tool_event(self, event: ToolEvent) -> None:
    try:
        if event.status == ToolEventStatus.CALLED:
            if event.tool_name == "browser":
                event.tool_content = BrowserToolContent(
                    screenshot=(
                        await self._get_browser_screenshot()
                    ),
                )
            elif event.tool_name == "search":
                search_results: ToolResult[SearchResults] = (
                    event.function_result
                )
                event.tool_content = SearchToolContent(
                    results=search_results.data.results
                )
            elif event.tool_name == "file":
                if "filepath" in event.function_args:
                    filepath = event.function_args["filepath"]
                    file_read_result = (
                        await self._sandbox.read_file(filepath)
                    )
                    file_content = (
                        file_read_result.data or {}
                    ).get("content", "")
                    event.tool_content = FileToolContent(
                        content=file_content
                    )
                    await self._sync_file_to_storage(filepath)
    except Exception as e:
        logger.exception(
            f"AgentTaskRunner生成工具内容失败: {str(e)}"
        )
```

这层 try/except 位于展示增强边界，浏览器截图上传失败时原 ToolEvent 仍可落盘，Worker 的任务成功与否不由截图决定。file 分支不只读内容，还会把访问路径同步到对象存储，这沿用了原项目“工具操作后可在文件面板查看”的行为。MCP、A2A 与 Shell 的其余分支使用同一模式，只是 tool_content 的数据类型不同。

Runner 最外层 invoke 在执行前初始化沙箱、MCP 和 A2A，finally 无条件清理工具。正常跑完输入队列后把 Session 更新为 completed，取消与普通异常各有独立分支。普通异常会被转换成 `AgentTaskRunner出错` ErrorEvent，而不是让 Redis Task 静默结束。

```python
async def invoke(self, task: Task) -> None:
    try:
        await self._sandbox.ensure_sandbox()
        await self._mcp_tool.initialize(self._mcp_config)
        await self._a2a_tool.initialize(self._a2a_config)

        # 省略输入事件消费与 Flow 执行

        async with self._uow:
            await self._uow.session.update_status(
                self._session_id,
                SessionStatus.COMPLETED,
            )
    except asyncio.CancelledError:
        await self._persist_cancellation(task)
        async with self._uow:
            await self._uow.session.update_status(
                self._session_id,
                SessionStatus.COMPLETED,
            )
        raise
    except Exception as e:
        await self._put_and_add_event(
            task,
            ErrorEvent(
                error=f"AgentTaskRunner出错: {str(e)}"
            ),
        )
        async with self._uow:
            await self._uow.session.update_status(
                self._session_id,
                SessionStatus.COMPLETED,
            )
    finally:
        await self._cleanup_tools()
```

CancelledError 必须重新 raise，RedisStreamTask 才知道后台协程确实被取消并进入自己的 finally 清 registry。普通异常已经转成用户可见 ErrorEvent，所以 Runner 不再向上抛同一个异常造成第二条错误。MCP/A2A 清理放在同一个 invoke Task 的 finally，满足 anyio cancel scope 的上下文约束。

## 十二、取消不是简单关闭 SSE，而是先终止执行并持久化取消快照

前端点击停止后调用会话停止接口，底层 `RedisStreamTask.cancel()` 会取消正在运行的 `_execution_task`。运行中 Task 不会立即从进程内 registry 删除，因为 Runner 仍需在 CancelledError 分支生成 Team 取消事件并释放 MCP、A2A 等资源。真正的 registry 清理由 `_execute_task()` 的 finally 回调完成，避免会话先失去 Task 引用而取消链路还未结束。

Runner 捕获 `asyncio.CancelledError` 后调用 `_persist_cancellation()`。若当前活动 Flow 是 TeamFlow，`cancel_events()` 会先取消 producer 并等待其退出，然后把当时仍是 pending、running 或 retrying 的节点标为 cancelled，错误写为 `cancelled_by_user`，逐个生成 TeamTaskEvent。最后它把整图标为 cancelled、产生最终 TaskGraphEvent，Runner 再追加 DoneEvent 并把会话状态更新为 completed。

`TeamFlow.cancel_events()` 记录 active id 发生在等待 producer 取消之前，用于覆盖正在执行和尚未开始的任务。已经 completed 或 failed 的任务不会被改写成 cancelled，因而最终图保留用户停止前已经取得的事实结果。方法的 active status 集合里也包含 cancelled，使 Orchestrator 刚刚捕获取消并标记的节点仍会被统一写成 `cancelled_by_user`。

Task 抽象新增 `wait()`，停止服务在发出 cancel 后可以等待 Runner 完成取消快照、状态更新与 finally 清理。`RedisStreamTask.wait()` 使用 gather 并接收异常，且避免等待当前 Task 自身，从而不会造成自锁。这个等待修复的是“接口已经提示停止成功，但取消事件和会话状态还没来得及落盘”的时序问题。

进程关闭时 `RedisStreamTask.destroy()` 遍历 registry 的快照列表，而不是直接遍历会在完成回调中被修改的字典。每个 Task 先 cancel，再调用 Runner.destroy() 释放沙箱和工具，最后清空 registry。这里仍然是单进程内生命周期管理，没有实现将活动 Task 的调度状态持久化后由其他进程接管。

### 取消链路源码走读

TeamFlow 取消时先记录当时需要取消的 task id，再 cancel producer 并等待它真正退出。先记录 id 是因为 `_execute_task()` 捕获 CancelledError 时可能已经把 running 节点改成 cancelled，等待后再按 pending/running 筛选会漏掉它。完成或失败的节点不在 active_ids 中，所以用户停止不会改写已经确定的历史结果。

```python
async def cancel_events(self) -> list[BaseEvent]:
    if self._graph is None:
        self._done = True
        return []

    active_statuses = {
        TeamTaskStatus.PENDING,
        TeamTaskStatus.RUNNING,
        TeamTaskStatus.RETRYING,
        TeamTaskStatus.CANCELLED,
    }
    active_ids = {
        task.id
        for task in self._graph.tasks
        if task.status in active_statuses
    }
    if (
        self._producer is not None
        and not self._producer.done()
    ):
        self._producer.cancel()
        await asyncio.gather(
            self._producer,
            return_exceptions=True,
        )
```

`return_exceptions=True` 用于等待取消完成而不让 producer 的 CancelledError中断快照生成。active_statuses 包含已 cancelled，正是为了覆盖 producer 在响应取消时先写入的中间状态。graph 还没建立时返回空事件，Runner 仍会追加 DoneEvent结束前端流。

下一段逐个修改 active task，并构造深拷贝的 TeamTaskEvent。随后图状态统一改成 cancelled，错误写 cancelled_by_user，再追加最终 TaskGraphEvent。方法只返回事件列表，不自己写 Redis 或数据库，持久化顺序仍由 Runner 控制。

```python
events: list[BaseEvent] = []
for task in self._graph.tasks:
    if task.id not in active_ids:
        continue
    task.status = TeamTaskStatus.CANCELLED
    task.error = "cancelled_by_user"
    events.append(
        TeamTaskEvent(
            graph_id=self._graph.id,
            task=task.model_copy(deep=True),
            agent_id=task.assigned_agent_id,
            attempt=task.attempt_count,
        )
    )

self._graph.status = TaskGraphStatus.CANCELLED
self._graph.error = "cancelled_by_user"
events.append(
    TaskGraphEvent(
        graph=self._graph.model_copy(deep=True)
    )
)
self._done = True
return events
```

尚未开始的 pending 节点 assigned_agent_id 为 None、attempt 为零，这些值如实保存在取消事件中。正在执行的节点保留当前 worker 和 attempt，方便前端或日志知道停止发生在哪次执行。图事件放在所有任务事件之后，消费方能先更新各任务，再用最终图快照校准全局状态。

Runner 的 `_persist_cancellation()` 按列表顺序逐个调用单点写入方法，最后追加 DoneEvent。DoneEvent 在这里表示“取消流程已经完成并且不会再有事件”，不是把 cancelled 图改成业务成功。Session 随后被更新为 completed，是现有 SessionStatus 没有 cancelled 枚举时的传输层终态表达。

```python
async def _persist_cancellation(self, task: Task) -> None:
    if self._active_flow:
        for cancel_event in (
            await self._active_flow.cancel_events()
        ):
            await self._put_and_add_event(task, cancel_event)
    await self._put_and_add_event(task, DoneEvent())
```

RedisStreamTask.cancel() 只向执行协程发送取消，不在 running 状态立即删除 registry。`wait()` 再等待该协程完成 Runner 的取消持久化和 finally 清理，`_execute_task()` 自己的 finally 最后调用 `_on_task_done()` 删除 registry。这个顺序消除了停止接口返回时 Task 已找不到、但取消事件还未落盘的窗口。

```python
def cancel(self) -> bool:
    if not self.done:
        self._execution_task.cancel()
        return True

    self._cleanup_registry()
    return True

async def wait(self) -> None:
    execution_task = self._execution_task
    if (
        execution_task is None
        or execution_task is asyncio.current_task()
    ):
        return
    await asyncio.gather(
        execution_task,
        return_exceptions=True,
    )

async def _execute_task(self) -> None:
    try:
        await self._task_runner.invoke(self)
    except asyncio.CancelledError:
        raise
    finally:
        self._on_task_done()
```

`execution_task is asyncio.current_task()` 防止执行协程在内部错误路径等待自己。gather 接收异常，是因为调用方关心取消清理完成，而异常已经由 Runner 或日志链路处理。registry 仍然只存在于当前 Python 进程，这段代码没有增加跨进程恢复能力。

## 十三、前端把同一组 Team 事件投影成两个用途不同的界面区域

Team UI 没有新增一个与原聊天割裂的“大型多 Agent 控制台”，而是复用原有 `PlanPanel`、`ChatMessage`、`StepBlock` 和工具预览组件。同一批后端事件被做成两种投影：输入框上方的底部面板展示完整 DAG 的全局进度，对话区只展示已经真正开始执行的任务及其工具调用。前者回答“总共有几项、完成了几项”，后者回答“当前哪些 Worker 正在做什么”。

`getLatestTeamPlanFromEvents()` 从事件头向后扫描，每遇到新的用户消息就清空上一轮 graph 上下文。遇到 TaskGraphEvent 时，它把图中所有任务转换成 `PlanStep[]`，所以尚未执行的 pending 节点也会立即出现在底部面板；后续同 graph id 的 TeamTaskEvent 再按 task id更新状态。`SessionDetailView` 最终使用 `teamPlanSteps ?? planSteps`，Team 有图时展示 Team 计划，否则继续展示单 Agent 的 PlanEvent 步骤。

底部 `PlanPanel` 沿用原组件的折叠交互，收起时显示第一项描述和“完成数 / 总数”，展开时列出所有节点。完成数只统计 status 为 completed 的步骤，其他状态统一显示 Clock 图标，因此 failed、skipped 和 cancelled 目前不会在底部获得专用失败图标。面板描述的是任务清单进度，不承载工具明细，浏览器、搜索、文件和 Shell 操作仍留在对话区。

`eventsToTimeline()` 为 Team 维护 `Map<graphId:taskId, timelineIndex>`，它不会因初始 TaskGraphEvent 为每个 pending 节点创建对话块。只有 TeamTaskEvent 状态进入 running 或 retrying 时，`upsertTeamStep()` 才允许创建 `StepBlock`；completed、failed 或 cancelled 事件只更新已经存在的块，skipped 节点因为从未运行通常不会出现在对话正文。这样 Planner 一开始生成的 task_3 不会提前占据聊天区，必须等依赖完成、task_3 真正启动后才追加到对话。

并行批次中的 worker-1 和 worker-2 会各自产生 running TeamTaskEvent，因此二者到达时分别创建两个折叠任务块。调度器以后启动依赖节点 task_3 时，新的 running 事件再把第三个块追加到时间线，历史的前两个块保留在原位置。Map 的键包含 graph id 和 task id，不依赖可复用的 worker slot，从而不会把下一批 worker-1 的任务错误合并到上一批任务。

带 task_id 的 ToolEvent 只会寻找对应 Team Step，不会走单 Agent 的 lastStepId 归属逻辑。calling 与 called 使用 `tool_call_id + attempt` 查找同一工具项，状态更新会替换原项；attempt 不同则保留成两条记录，用户能够看到任务重试前后的实际工具调用。若工具事件先于对应 running 事件到达，当前逻辑找不到 Step 时会直接结束该 tool 分支而不创建独立工具项，不过正常后端顺序总是先 emit running task 再执行 Worker。

每条新用户 MessageEvent 都会清空 Team Step Map，以免同名 task id 跨轮次互相更新，但已经写入 `list` 的历史时间线不会被删除。由此同一会话连续执行两轮 Team 时，上一轮任务块和最终回答仍保留，新一轮从新的用户气泡后重新建立 task 映射。图 id 提供后端唯一性，用户消息边界提供前端视觉轮次边界，两者共同避免事件混线。

StepBlock 直接复用单 Agent 原有组件，默认展开并在任务标题下嵌套 ToolRow。工具点击仍进入既有 `ToolPreviewPanel`，文件点击进入文件预览，运行中新增工具时详情页还会自动选中最新工具并滚动到末尾。当前组件虽然计算了 `isCompleted`，但图标实现始终绘制灰底 CheckIcon，running、failed 与 completed 暂时没有准确的视觉差异，这属于当前 UI 的已知细节。

Team Task 的 `task.error` 没有被转换进 StepEvent 数据模型，因此失败任务块本身只改变 status，不显示详细错误正文。整图失败时，最终 ErrorEvent 会在对话区渲染汇总后的任务描述与错误；partial 图则依赖 Synthesizer 在最终 assistant 消息中说明失败和跳过。底部面板和任务块负责状态导航，最终消息负责面向用户解释结果，这三个层次目前没有完全统一错误展示。

### 前端双投影源码走读

后端七种任务状态先被压缩到既有 UI 的三种执行状态。retrying 对用户仍表示正在运行，skipped 与 cancelled 暂时都映射为 failed。这个映射同时被对话 Step 和底部 PlanStep 使用，所以两处不会对同一任务给出相反状态。

```typescript
const TEAM_TASK_STATUS: Record<TeamTask["status"], ExecutionStatus> = {
  pending: "pending",
  running: "running",
  retrying: "running",
  completed: "completed",
  failed: "failed",
  skipped: "failed",
  cancelled: "failed",
};

export function teamTaskStatusToExecutionStatus(
  status: TeamTask["status"],
): ExecutionStatus {
  return TEAM_TASK_STATUS[status];
}
```

类型声明使用 `Record<TeamTask["status"], ...>`，后端以后新增状态而前端未补映射时 TypeScript 会在静态检查阶段提示缺项。当前 UI 没有 retrying、skipped 或 cancelled 的独立图标，因此这里做的是展示降维，不会改写原始 SSE event。原事件仍保存在 events 数组，未来组件可以直接读取更细状态。

时间线投影内部用 `teamStepIndexes` 记录每个图节点在 list 中的位置。`upsertTeamStep()` 的 create 参数是本次修正的关键：没有已有 Step 且 create=false 时直接 return，已有 Step 时无论 create 值都更新状态。这样 completed/failed 事件可以关闭已经运行过的块，却不能让从未执行的 skipped 或初始 pending 节点突然出现在对话区。

```typescript
const teamStepIndexes = new Map<string, number>();

const upsertTeamStep = (graphId: string, task: TeamTask, create: boolean) => {
  const key = `${graphId}:${task.id}`;
  const data: StepEvent = {
    id: key,
    description: task.description,
    status: teamTaskStatusToExecutionStatus(task.status),
  };
  const existingIndex = teamStepIndexes.get(key);
  if (existingIndex === undefined) {
    if (!create) return;
    teamStepIndexes.set(key, list.length);
    list.push({
      kind: "step",
      id: stableId("team-step", stepIndex++, key),
      data,
      tools: [],
    });
    return;
  }
  const existing = list[existingIndex];
  if (existing.kind === "step") {
    list[existingIndex] = { ...existing, data };
  }
};
```

Map 键使用 graphId:taskId，而 StepEvent.id 也使用同一个组合，避免两轮 Team 都有 task_1 时冲突。创建时保存 `list.length` 是因为 Step 被立即 push 到当前位置，后续工具事件可以 O(1) 找到它。更新时 `{ ...existing, data }` 保留 existing.tools，所以 running 变 completed 不会清空已经展示的搜索或浏览器操作。

事件 switch 明确忽略 task_graph 对话渲染，只让 task 事件决定 Step。create 条件只有 running 或 retrying，completed、failed、cancelled 只更新现有块。新用户消息会清空 Map 但不会清空 list，所以历史轮次仍留在聊天正文。

```typescript
case "message": {
  const msg = ev.data as ChatMessage;
  if (msg.role === "user") {
    lastStepId = null;
    teamStepIndexes.clear();
    list.push({
      kind: "user",
      id: stableId(
        "user",
        messageIndex++,
        String(list.length),
      ),
      data: msg,
    });
  }
  // 省略 assistant 与附件投影
  break;
}

case "task_graph":
  break;

case "task": {
  const status = ev.data.task.status;
  upsertTeamStep(
    ev.data.graph_id,
    ev.data.task,
    status === "running" || status === "retrying",
  );
  break;
}
```

这段代码正面实现了“worker-1、worker-2 运行时先展示 1、2，task_3 真正运行时再加 3”。初始 TaskGraphEvent 即使包含五个 pending 节点，在这个 switch 中也不会循环创建 Step。若一个节点因上游失败直接从 pending 变 skipped，它不会创建对话块，但仍会在底部面板变成 failed。

带 task_id 的工具事件走 Team 专用归属分支，并在完成后直接 break，不再尝试单 Agent 的 lastStepId。calling/called 查找键由 `tool_call_id` 和 `attempt` 共同组成，同一次 attempt 更新原项，不同 attempt 新增记录。工具事件包含 task_id 却找不到 Team Step 时也会 break，因此不会错误显示成顶层独立工具。

```typescript
case "tool": {
  const tool = ev.data as ToolEvent;
  if (tool.task_id) {
    const teamStepIndex = tool.graph_id
      ? teamStepIndexes.get(
          `${tool.graph_id}:${tool.task_id}`
        )
      : undefined;
    if (teamStepIndex !== undefined) {
      const teamStep = list[teamStepIndex];
      if (teamStep.kind === "step") {
        const existingToolIndex = tool.tool_call_id
          ? teamStep.tools.findIndex(
              (item) =>
                item.tool_call_id === tool.tool_call_id &&
                item.attempt === tool.attempt,
            )
          : -1;
        const tools = [...teamStep.tools];
        if (existingToolIndex >= 0) {
          tools[existingToolIndex] = tool;
        } else {
          tools.push(tool);
        }
        list[teamStepIndex] = { ...teamStep, tools };
      }
    }
    break;
  }

  // 后续继续处理单 Agent 工具归属
}
```

正常后端顺序总是 running TeamTaskEvent 先入队，Worker 随后才 emit ToolEvent，所以 Step 理应先存在。attempt 条件防止 task 重试时复用同一模型 tool_call_id 导致第二次记录覆盖第一次。使用数组浅拷贝与对象展开保持 React state 的不可变更新习惯，Memo 依赖 events 变化后能重新渲染。

底部任务面板使用完全独立的投影函数。它在 TaskGraphEvent 到来时一次创建所有 PlanStep，随后用同 graph id 的 TeamTaskEvent 覆盖单项状态。每遇到新用户消息就把 graphId 和 steps 置空，确保返回的是最新一轮 Team 计划。

```typescript
export function getLatestTeamPlanFromEvents(
  events: SSEEventData[],
): PlanStep[] | null {
  let graphId: string | null = null;
  let steps: PlanStep[] | null = null;

  for (const event of events) {
    if (event.type === "message" && event.data.role === "user") {
      graphId = null;
      steps = null;
      continue;
    }

    if (event.type === "task_graph") {
      graphId = event.data.graph.id;
      steps = event.data.graph.tasks.map((task) => ({
        id: task.id,
        description: task.description,
        status: teamTaskStatusToExecutionStatus(task.status),
      }));
      continue;
    }

    if (event.type === "task" && steps && event.data.graph_id === graphId) {
      steps = steps.map((step) =>
        step.id === event.data.task.id
          ? {
              id: event.data.task.id,
              description: event.data.task.description,
              status: teamTaskStatusToExecutionStatus(event.data.task.status),
            }
          : step,
      );
    }
  }

  return steps;
}
```

最终 TaskGraphEvent 会重新覆盖整个 steps 数组，所以即使中间某个增量事件因断线没收到，恢复历史后仍能用最终快照校准。图 id检查阻止迟到的旧任务事件更新新一轮计划。函数返回 null 而不是空数组用来表达“当前轮不是 Team 或尚未有 Team 图”，详情页据此回退到原 PlanEvent。

PlanPanel 收到的是已经投影好的 PlanStep，不认识 graph id、dependency 或 Worker。它只统计 completed 状态，收起时展示第一项与计数，展开后逐项选择 Check 或 Clock。failed、skipped、cancelled 已映射为 failed，但这里非 completed 一律走 Clock，这就是失败任务当前仍显示时钟的具体代码原因。

```tsx
const completedCount = steps.filter(
  (step) => step.status === "completed",
).length;
const totalCount = steps.length;

<span className="text-xs text-gray-500">
  {completedCount} / {totalCount}
</span>;

{
  steps.map((step) => (
    <div key={step.id} className="flex items-center">
      {step.status === "completed" ? <Check size={16} /> : <Clock size={16} />}
      <div className="text-sm truncate">{step.description}</div>
    </div>
  ));
}
```

底部面板没有把 pending 任务移除，因为它的目的就是从初始图展示完整总量。完成计数也不把 failed 当作“已处理”，因此整图结束但有失败时可能显示 2/3 而不是 3/3。这个行为与用户关注的“完成数”一致，但若未来要展示“已终态数”，需要单独调整计数语义。

`SessionDetailView` 同时计算两个投影，但交给不同组件。timeline 逐项渲染 ChatMessage，Team plan 优先交给底部 PlanPanel，输入框紧跟其后。这里没有把 teamPlanSteps map 成 ChatMessage，这正是两个区域分离的最终组件落点。

```tsx
const timeline = useMemo(
  () => eventsToTimeline(events),
  [events],
);
const planSteps = useMemo(
  () => getLatestPlanFromEvents(events),
  [events],
);
const teamPlanSteps = useMemo(
  () => getLatestTeamPlanFromEvents(events),
  [events],
);

// 对话滚动区
{timeline.map((item) => (
  <ChatMessage
    key={item.id}
    item={item}
    onViewAllFiles={handleViewAllFiles}
    onFileClick={handleFileClick}
    onToolClick={handleToolClick}
  />
))}

// 输入框上方固定区域
<PlanPanel
  className="mb-2"
  steps={teamPlanSteps ?? planSteps}
/>
<ChatInput
  onSend={handleSend}
  sessionId={sessionId}
  isRunning={session?.status === "running"}
  onStop={handleStop}
  mode={mode}
  onModeChange={setModeOverride}
/>
```

`??` 只在 teamPlanSteps 为 null 时回退，合法 Team 图即使后来所有节点失败也仍是数组，不会错误展示旧单 Agent plan。ChatMessage 接到 kind=step 后复用 StepBlock，工具数组就在同一个时间线项里。PlanPanel 与输入框位于 flex-shrink-0 的底部固定区域，所以其职责一直是总进度，而非操作日志。

StepBlock 自己只负责展开标题与嵌套工具。标题单击切换 expanded，tools 非空时逐个渲染 ToolUse，ToolUse 再根据 tool_name 打开搜索、浏览器、文件或 Shell 预览。当前 `isCompleted` 没有参与 className 或图标选择，因此下面代码确实对所有状态画相同灰色 CheckIcon。

```tsx
function StepBlock({ stepItem, onToolClick }: Props) {
  const [expanded, setExpanded] = useState(true);
  const { data, tools } = stepItem;
  const isCompleted = data.status === "completed";

  return (
    <div className="flex flex-col mt-3">
      <div role="button" tabIndex={0} onClick={() => setExpanded(!expanded)}>
        <div className="flex flex-row gap-2">
          <div className="w-4 h-4 border rounded-[15px] bg-gray-300">
            <CheckIcon className="text-white" size={10} />
          </div>
          <div>{data.description}</div>
          <ChevronDown className={cn(expanded && "rotate-180")} />
        </div>
      </div>

      {expanded && tools.length > 0 && (
        <div className="flex flex-col gap-3">
          {tools.map((tool, idx) => (
            <ToolRow
              key={`${data.id}-tool-${idx}`}
              timeLabel={getToolTimeLabel(tool)}
            >
              <ToolUse
                data={tool}
                onClick={onToolClick ? () => onToolClick(tool) : undefined}
              />
            </ToolRow>
          ))}
        </div>
      )}
    </div>
  );
}
```

这段节选省略了样式和键盘事件，但保留了决定状态呈现与工具嵌套的逻辑。`isCompleted` 当前是未使用变量，这不是文档推测，而是实际组件行为。任务错误也不在 data 中，所以 StepBlock 无法自行显示 task.error，失败详情只能来自后续 ErrorEvent 或 Synthesizer 消息。

## 十四、模式选择和 SSE 生命周期在前端被明确绑定到当前会话轮次

首页 `ChatInput` 增加“单 Agent”和“多 Agent”两个内联按钮，初始 mode 为 react。创建空会话后，首页把 message、attachment ids 和 mode 一起序列化成 JSON，再经 URI 编码与 Base64 放进详情页的 `init` 查询参数。详情页解码时只接受精确的 `team`，其他值统一解释为 react，避免任意字符串进入 API 的枚举字段。

`SessionDetailView` 会从后向前寻找最近一条用户消息的 agent_mode，得到 persistedMode。实际输入框 mode 使用 `modeOverride ?? persistedMode ?? 'react'`，因此用户刚在当前页面选择的模式优先，其次沿用最近一轮持久化模式，最后才是兼容旧会话的 react。模式按钮在会话 running、正在发送或整体 disabled 时不可点击，防止界面显示的模式与已经提交给后端的模式发生变化。

当正在运行的模式是 Team 时，`teamLocked` 会禁用文本框与附件上传。无论当前是 React 还是 Team，只要会话 running，发送按钮都会替换成停止按钮；Team 额外锁定输入是为了匹配后端 409 语义。当前 React 的等待与追加消息能力仍走原有逻辑，Team 不支持执行中问答或动态重规划。

`useSessionDetail()` 明确区分 empty stream 和 message stream。empty stream 用于页面进入已有未完成会话时，以空 body 和 last event id 附着到当前 Task；message stream 用于用户新发送一条消息，同时提交文本、附件和 mode。发送前会停止 empty stream，创建 message stream 前也会清理已有 message stream，因此同一 Hook 不应该同时维持两个 `/chat` 连接。

每次归一化事件后，Hook 会从 data.event_id 更新 `lastEventIdRef`，页面刷新获取历史事件时也会从最后一个事件恢复这个游标。TaskGraph pending/running、TeamTask running/retrying、单 Agent Step running 都会把本地会话状态设置为 running，DoneEvent 设置 completed，ErrorEvent 也结束本轮运行。这个本地状态驱动输入框禁用、停止按钮、加载提示和是否需要开启 empty stream。

消息流收到 DoneEvent 时会清除 streaming 标记、重置发送态并主动清理当前连接。底层 SSE 客户端用 `SSE_STREAM_END` 表示服务器正常关闭时，Hook 只做同样的清理，不再递归建立新 `/chat`；AbortError 也被视为主动取消。只有真实网络或协议错误才写入 error 状态，因此此前“stream 和 chat 接口不停发送”的根因不再通过无限重连延续。

需要区分聊天流与左侧会话列表流。聊天详情的 `/sessions/{id}/chat` 不会在正常结束后重连，而 `SessionsProvider` 使用的 `/sessions/stream` 是独立的会话列表更新通道，仍保留最多十次指数退避重试。浏览器网络面板看到 `/sessions/stream` 长连接或重试，并不等于同一个 chat 请求正在反复提交用户消息。

### 模式传递与 SSE 连接源码走读

首页发送前把 mode 与 message、attachments 放进同一个 payload。创建 Session 的接口仍只创建空会话，真正聊天请求发生在跳转后的详情页，这避免首页同时持有一个即将卸载的 SSE。Base64 只用于安全携带初始参数，不是安全加密或后端持久化。

```tsx
const [mode, setMode] = useState<AgentMode>("react");

const handleSend = async (message: string, files: FileInfo[]) => {
  const session = await sessionApi.createSession();
  const sessionId = session.session_id;
  const attachments = files.map((file) => file.id);
  const payload = JSON.stringify({
    message,
    attachments,
    mode,
  });
  const encoded = btoa(encodeURIComponent(payload));
  router.push(`/sessions/${sessionId}?init=${encoded}`);
};
```

详情页解码时只把精确字符串 team 保留为 team，其余值回到 react。这个校验发生在前端，但后端 ChatRequest 枚举仍是最终类型边界。initialMode 与 initialMessage 同时写入一次性的 sessionData，SessionDetailView 第一次自动发送时能够使用用户在首页选择的模式。

```tsx
const decoded = decodeURIComponent(atob(initParam));
const { message, attachments, mode } = JSON.parse(decoded);

setSessionData({
  id: p.id,
  initialMessage: message,
  initialAttachments: attachments,
  initialMode: mode === "team" ? "team" : "react",
  hasInitialMessage: true,
});
```

详情组件从历史 events 反向找最近一条用户消息，恢复持久化 mode。`modeOverride` 保存当前页面尚未发送的用户选择，优先级高于历史值；新建会话的 initialMode 也先放入 override。这个组合使刷新旧 Team 会话后按钮仍显示多 Agent，同时允许任务结束后切换下一轮模式。

```tsx
const persistedMode = useMemo(() => {
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    if (event.type === "message" && event.data.role === "user") {
      return event.data.agent_mode ?? null;
    }
  }
  return null;
}, [events]);

const [modeOverride, setModeOverride] = useState<AgentMode | null>(
  initialMode ?? null,
);
const mode = modeOverride ?? persistedMode ?? "react";
```

ChatInput 的 `teamLocked` 只在 running 且当前 mode=team 时锁文本和上传，而模式按钮对任何 running 会话都禁用。发送区域只要 isRunning 就显示停止按钮，不再留下第二个发送入口。按钮 map 使用字面量 tuple，onModeChange 的参数因此保持 AgentMode 类型。

```tsx
const teamLocked = isRunning && mode === "team";

<textarea
  value={inputValue}
  onChange={handleInputChange}
  disabled={sending || disabled || teamLocked}
/>;

{
  (
    [
      ["react", "单 Agent"],
      ["team", "多 Agent"],
    ] as const
  ).map(([value, label]) => (
    <Button
      key={value}
      disabled={isRunning || sending || disabled}
      onClick={() => onModeChange(value)}
      aria-pressed={mode === value}
    >
      {label}
    </Button>
  ));
}

{
  isRunning ? (
    <Button onClick={onStop} aria-label="停止任务">
      <Pause className="size-4" />
    </Button>
  ) : (
    <Button onClick={handleSend} aria-label="发送消息">
      <ArrowUp />
    </Button>
  );
}
```

这里的 UI 锁不是唯一保护，后端仍有 409 校验，因此用户绕过 disabled 也不能向 running Team 插入消息。React running 时 textarea 没有因 teamLocked 禁用，保留原有交互，但 mode 切换按钮依然锁住，已经运行的 Flow 不会中途变种。停止按钮直接调用 SessionDetailView.handleStop，与当前输入内容无关。

事件进入 Hook 后，task_graph 和 task 的活动状态会把 Session 本地状态改成 running，Done 与 Error 都结束本轮。这里没有等待 assistant message 才设 completed，因为全图失败根本不会产生 assistant message。状态更新只影响页面控制，不会反向写数据库，后端 Session status 仍由 AgentService 与 Runner 管理。

```typescript
if (
  evToAppend.type === "task_graph" &&
  (evToAppend.data.graph.status === "pending" ||
    evToAppend.data.graph.status === "running")
) {
  setSession((prev) => (prev ? { ...prev, status: "running" } : null));
}

if (
  evToAppend.type === "task" &&
  (evToAppend.data.task.status === "running" ||
    evToAppend.data.task.status === "retrying")
) {
  setSession((prev) => (prev ? { ...prev, status: "running" } : null));
}

if (evToAppend.type === "done") {
  setSession((prev) => (prev ? { ...prev, status: "completed" } : null));
}

if (evToAppend.type === "error") {
  setSession((prev) => (prev ? { ...prev, status: "completed" } : null));
}
```

partial 图在 TaskGraphEvent 中不是 running，但此前 Session 已经处于 running，直到最终 Done 才 completed。failed 图也通过随后的 ErrorEvent完成状态切换。前端 SessionStatus 没有 failed 或 cancelled，所以这两种终态都表现为 completed 加时间线错误或取消图状态。

Hook 为两个 SSE 所有者分别保存 cleanup ref，并用 `isSendMessageRef` 防止状态 effect 在发送期间启动 empty stream。页面进入已有未完成 Session 时，empty stream 只提交 event_id；用户主动发送时先 stopEmptyStream，再清理旧 message stream。两个连接不会因为共享一个 ref 而互相覆盖 cleanup 函数。

```typescript
const emptyStreamCleanupRef = useRef<(() => void) | null>(null);
const messageStreamCleanupRef = useRef<(() => void) | null>(null);
const isSendMessageRef = useRef(false);
const lastEventIdRef = useRef<string | null>(null);

const startEmptyStream = useCallback(() => {
  if (!sessionId) return;
  if (emptyStreamCleanupRef.current) {
    emptyStreamCleanupRef.current();
    emptyStreamCleanupRef.current = null;
  }
  emptyStreamCleanupRef.current = sessionApi.chat(
    sessionId,
    { event_id: lastEventIdRef.current || undefined },
    (ev) => appendEvent(ev),
    (err) => {
      if (err.name === "AbortError") return;
      if (err.message === "SSE_STREAM_END") {
        emptyStreamCleanupRef.current = null;
        return;
      }
      emptyStreamCleanupRef.current = null;
      setError(err);
    },
  );
}, [sessionId, appendEvent]);
```

关键变化在 `SSE_STREAM_END` 分支：它只清空 ref 并 return，没有 setTimeout，也没有再次调用 startEmptyStream。真正需要 empty stream 的时机由另一个 effect 根据 Session 未完成、当前没有 message stream、没有 skip 标记统一判断。这样一个正常结束回调不会自己制造下一条请求。

发送函数在建立 message stream 前切断 empty stream，并把 mode 放进请求 body。DoneEvent 会清 streaming 和发送标记，再主动 abort/cleanup 当前连接；自然关闭的 SSE_STREAM_END 只执行状态清理，不打开 empty stream。真实错误写入 error 并关闭当前流，也不会自动重复提交同一 message。

```typescript
const sendMessage = useCallback(
  async (message: string, attachmentIds: string[], mode: AgentMode) => {
    if (!sessionId) return;
    stopEmptyStream();

    if (messageStreamCleanupRef.current) {
      messageStreamCleanupRef.current();
      messageStreamCleanupRef.current = null;
    }

    isSendMessageRef.current = true;
    setStreaming(true);
    setSession((prev) => (prev ? { ...prev, status: "running" } : null));

    const onEvent = (ev: SSEEventData) => {
      appendEvent(ev);
      if (ev.type === "done") {
        setStreaming(false);
        isSendMessageRef.current = false;
        if (messageStreamCleanupRef.current) {
          messageStreamCleanupRef.current();
          messageStreamCleanupRef.current = null;
        }
      }
    };

    messageStreamCleanupRef.current = sessionApi.chat(
      sessionId,
      { message, attachments: attachmentIds, mode },
      onEvent,
      (err) => {
        if (err.name === "AbortError") {
          setStreaming(false);
          isSendMessageRef.current = false;
          return;
        }
        if (err.message === "SSE_STREAM_END") {
          setStreaming(false);
          isSendMessageRef.current = false;
          messageStreamCleanupRef.current = null;
          return;
        }
        setError(err);
        setStreaming(false);
        isSendMessageRef.current = false;
        if (messageStreamCleanupRef.current) {
          messageStreamCleanupRef.current();
          messageStreamCleanupRef.current = null;
        }
      },
    );
  },
  [sessionId, appendEvent, stopEmptyStream],
);
```

ErrorEvent 不是 DoneEvent，因此 onEvent 不会在这里主动 cleanup，但后端在 ErrorEvent 后关闭 SSE，随后的 SSE_STREAM_END 会完成清理。`appendEvent()` 已在收到 ErrorEvent 时把本地 Session 标成 completed，所以状态 effect 不会再开 empty stream。这个闭环同时依赖后端错误事件为终止事件和前端正常结束不重连，两侧缺一都会出现连接残留。

最后事件 id在每次 append 时更新，并在 refresh 历史数据后从最后一条事件恢复。empty stream 把它放进 event_id，AgentService 再用它作为 output stream 的 `start_id`，因此只是续读，不是重跑 Flow。这个游标属于事件传输层，与 TeamTask.attempt 或 graph id没有计数关系。

```typescript
const eventId = (evToAppend.data as { event_id?: string })?.event_id;
if (eventId) {
  lastEventIdRef.current = eventId;
}

setEvents((prev) => [...prev, evToAppend]);
```

事件先更新游标再进入 React state，下一次意外断线恢复时不会重复请求已经处理的最后一条。当前数组采用追加而不是按 event id 去重，因此底层续传边界必须正确，服务端从 last id之后返回。Team 和 React 共用这一传输机制，新增 DAG 没有另建 WebSocket 或轮询接口。

## 十五、这套方案在常见多 Agent 架构中属于“中心化 Planner + 静态 DAG + 短生命周期 Worker”

常见的多 Agent 实现大致可以按协作方式分成几类。Supervisor 或 Manager 模式由一个主 Agent不断判断下一位执行者，群聊模式让多个角色轮流发言，handoff 或 swarm 模式允许当前 Agent 把控制权交给另一个 Agent，工作流模式则先生成或声明一张图，再由确定性引擎推进节点。当前分支选择最后一种，因为用户目标天然可以拆成有依赖的任务，而且产品需要明确展示任务总数、当前节点、并发进度与失败传播。

Planner 在这里确实需要编排 DAG，但 DAG 不等于必须引入一个可视化图库或外部工作流平台。代码里的 `dependencies` 数组就是有向边，`build_task_graph()` 完成拓扑合法性校验，`ready_tasks()` 与 Orchestrator 状态机负责运行图，前端再把图投影成任务面板。换言之，DAG 是领域数据结构和调度语义，不要求后端先画出图形，也不要求前端采用节点连线编辑器。

与动态 Supervisor 相比，静态 DAG 的优点是并发机会在运行前已经明确，节点何时 ready 可以由代码计算，失败影响哪些下游也可以确定传播。它的限制是运行中不会让一个 Worker 新增节点、修改依赖或根据新发现动态重规划，Planner 的第一次有效图将保持到本轮结束。当前实现更适合搜索对比、分片调研、独立文件读取后汇总等目标，不适合需要持续协商、角色辩论或探索中不断改变任务结构的场景。

与群聊式多 Agent 相比，当前 Worker 之间不会互相发送自然语言消息，也没有所有 Agent 共同追加内容的共享对话黑板。一个下游 Worker 只能通过 `dependency_results` 读取已经完成上游的结构化 WorkerResult，不会看到其他 Worker 的完整 ReAct 轨迹或私有 Memory。这样减少上下文膨胀和互相干扰，也使来源和产物归属清晰，但牺牲了横向讨论和中途协商能力。

与独立 Actor 或分布式队列架构相比，当前 Worker 不是可跨机器调度的实体。它们没有自己的持久化邮箱、租约、心跳、幂等键或恢复游标，只是 TeamFlow 生命周期中的 Python 对象和协程。选择同进程实现是为了最小化改动并复用已有工具实例，代价是不能靠增加 API 副本自动扩展同一张图，也不能在进程重启后从节点级检查点继续。

与完全预定义的工作流相比，这套方案仍保留 LLM Planner 的灵活性。节点结构不是开发者针对每类问题手写，用户的任意目标都可以被模型转换成一至五个能力节点；确定性代码只控制图是否合法和如何执行。它因此是一种“概率性规划、确定性调度、概率性执行与汇总”的混合架构，边界分别落在 Planner、Orchestrator、Worker 和 Synthesizer 四个组件上。

## 十六、一次典型请求会按照用户消息、图事件、任务事件、工具事件和汇总消息推进

以“与最高的建筑相比，埃菲尔铁塔有多高”为例，用户在输入框选择多 Agent 后发送消息。首页创建会话并把 mode=team 带到详情页，详情页发起带 message 的 chat SSE，AgentService 将用户 MessageEvent 写入 Redis 输入流和数据库。前端收到这条用户事件后建立新一轮时间线，后端 Runner 随后从输入流取出同一事件并创建新的 TeamFlow。

Planner 可能生成三个节点：task_1 搜索埃菲尔铁塔当前高度，task_2 搜索世界最高建筑及高度，task_3 依赖前两项并计算差值。前两个节点 capability 为 search 且 dependencies 为空，第三个节点 capability 为 analysis 且 dependencies 为 task_1 和 task_2。`build_task_graph()` 验证三个 id、两条依赖和无环条件后，生成 pending TaskGraph。

候选图可以简化为下面的 JSON，实际 Planner 输出还必须满足 Pydantic 的严格字段约束。这里的两条边都是从搜索节点指向分析节点，因此 task_3 在前两个结果都 completed 之前不可能 ready。任务数组顺序决定同优先级节点的稳定顺序，但依赖关系而不是数组位置决定能否执行。

```json
{
  "title": "埃菲尔铁塔与最高建筑高度比较",
  "goal": "与最高的建筑相比，埃菲尔铁塔有多高？",
  "tasks": [
    {
      "id": "task_1",
      "description": "搜索埃菲尔铁塔当前高度",
      "dependencies": [],
      "capability": "search",
      "success_criteria": "获得可靠的米制高度"
    },
    {
      "id": "task_2",
      "description": "搜索世界最高建筑及其高度",
      "dependencies": [],
      "capability": "search",
      "success_criteria": "获得建筑名称和米制高度"
    },
    {
      "id": "task_3",
      "description": "比较两者并计算高度差",
      "dependencies": ["task_1", "task_2"],
      "capability": "analysis",
      "success_criteria": "给出差值和清晰结论"
    }
  ]
}
```

TeamFlow 首先发送 TitleEvent 和初始 TaskGraphEvent。底部 PlanPanel 此时立即显示三项和 0/3，因为它面向整张计划；对话区不会根据图快照创建三个任务块，因为此时没有任何节点真正运行。Orchestrator 计算 ready 得到 task_1 与 task_2，二者都是并发安全的 search，且数量不超过默认三 Worker 上限，所以一起进入第一个 gather 批次。

Orchestrator 先后为两个节点写 running 状态并发出 TeamTaskEvent，再分别创建 worker-1 和 worker-2。前端收到这两个 running 事件后，才在用户消息下方追加两个可折叠 StepBlock，底部面板也把对应状态更新为 running。每个 Worker 调用 search_web 时，calling 和 called ToolEvent 根据自己的 task id进入对应折叠块，不会全部堆在输入框上方的 PlanPanel 中。

搜索 Worker 可能进行不止一次查询，因为 ReAct 模型会检查结果是否满足 success_criteria。每次工具调用都算该 attempt 内的一轮交互，而不是新建一个 DAG 节点；只有模型最终输出合法 WorkerResult 后，该 task 才会 completed。若 Bing 返回与查询不相关的结果，模型可能继续换关键词搜索，直到得到足够数据、主动返回失败结果、触发超时或达到 Worker 迭代上限。

假设 task_1 与 task_2 都成功，Orchestrator 会持久化两个 completed TeamTaskEvent，并重新进入调度循环。此时 task_3 的两个依赖都在 completed 集合中，它第一次进入 ready；analysis 是并发安全能力，但本轮只有它一个节点，因此创建新的 worker-1 并发送 running 事件。前端这时才把第三个任务块加到对话区，完全符合“正在执行哪个任务才展示哪个任务”的增量显示要求。

task_3 不获得搜索、浏览器或文件工具，它只能根据 dependency_results 中的两个结构化摘要进行计算。成功后它返回包含差值和比较结论的 WorkerResult，Orchestrator 将整图 finalize 为 completed，并由 TeamFlow 发送最终 TaskGraphEvent。Synthesizer 读取三项结果，组织带来源链接的自然语言回答，Runner 持久化 assistant MessageEvent，最后发送 DoneEvent 并把会话状态更新为 completed。

如果 task_1 成功而 task_2 两次 attempt 都失败，task_3 会被传播成 skipped，并写入 dependency_failed。因为至少一个节点 completed 且另有 failed/skipped，整图状态是 partial，Synthesizer 仍会根据成功结果回答，同时按提示词说明无法完成完整比较。若 task_1 与 task_2 都失败，task_3 同样 skipped，但没有任何 completed 节点，整图状态是 failed，前端最终显示逐任务具体错误而不是调用 Synthesizer。

## 十七、必须区分五层“次数”，否则很容易误判多 Agent 在无限迭代

第一层是 Planner DAG 校验次数，固定最多两次。只有 `ValueError` 及其子类表示的结构或 DAG 校验失败会把 validation_error 反馈给 Planner 再来一轮，普通运行异常直接终止为 Team Planner 失败。Planner 没有工具，所以这里的两次是两次候选图生成，不是搜索或业务任务重试。

第二层是单次 BaseAgent 调用语言模型的基础设施重试，使用全局 `AgentConfig.max_retries`，当前默认值为 3。它处理模型接口异常等调用失败，同一轮 ReAct 的业务上下文不因此变成新的 task attempt。达到上限后 BaseAgent 抛出明确错误，再由它所在的 Planner、Worker 或 Synthesizer 边界决定是否有更外层重试。

第三层是单个工具调用的重试，同样使用 BaseAgent 的全局 max_retries。工具连续失败后会形成失败 ToolResult 返回给模型，模型仍可能根据失败内容调整下一步，而不是立即把整个 Task 标为 failed。工具重试次数不显示在 TeamTask.attempt_count 中，因此不能从界面的“第 1 次任务尝试”推断底层 HTTP 请求只发生过一次。

第四层是 Worker ReAct 迭代上限，Team 通过复制 AgentConfig 把 max_iterations 改为 `team_max_worker_iterations`，当前工作区默认 50。一次迭代通常对应一次模型决策，可能发起工具，也可能输出最终 WorkerResult；模型一旦给出最终结果就立刻结束，不会固定跑满 50。达到 50 才会产生“Agent迭代超过最大迭代次数: 50”的错误，早期日志若显示上限 6，说明运行的仍是旧配置或旧镜像，不代表当前源码值。

第五层是 Orchestrator 的任务 attempt，默认 `team_max_task_retries=1` 表示初次执行加一次重试，总计最多两次。每次都会创建全新的 TaskWorker 和 Memory，并再次受 300 秒整体超时限制；所以最坏情况下一个节点可以经历两个完整的 ReAct 执行。任务 attempt 是前端 ToolEvent 上的 attempt 字段，也是 TeamTask.attempt_count 的来源。

Synthesizer 另外有最多两次生成机会，但它不算 Worker task attempt，也不改变节点状态。聊天 SSE 本身在正常关闭后不重试，左侧会话列表 SSE 才有独立的网络重连策略。把这些边界拆开后可以看到，系统有多个有限重试层，但没有任何一层设计成无条件无限循环。

## 十八、错误处理遵循“在哪一层发生，就由哪一层转换成可观察状态”

API 入口错误在建立流之前返回普通 HTTP 错误，例如运行中的 Team 收到新消息时返回 409。进入 AgentService.chat() 后出现的异常会被转换为 ErrorEvent并尝试加入会话事件，这保证已经开始消费 SSE 的客户端能在统一协议中看到错误。服务层的最终 unread 重置在独立 asyncio Task 中执行，是为了避免客户端断开时 anyio cancel scope中断数据库清理。

Planner 错误分为两类，非法计划在两次校验机会后显示“Team Planner 生成无效 DAG”，模型调用、JSONParser 或其他运行错误显示“Team Planner 失败”。这两类都发生在 TaskGraph 建立之前，所以不会伪造一张 failed 图或 pending 任务列表。用户能够从错误文本判断是计划约束不满足，还是 Planner 本身没有成功返回。

Worker 错误首先落到具体 TeamTask.error，并通过 retrying 或 failed TeamTaskEvent 对外可见。超时固定为 task_timeout，用户取消由取消路径改为 cancelled_by_user，依赖传播写 dependency_failed，其余异常保留实际字符串。任务失败不会在 Orchestrator 中被 catch 后改成成功摘要，图状态只根据真实终态计算。

调度器未预期异常由 TeamFlow 转换成 scheduler_error 图错误和 `Team 调度失败` ErrorEvent。合法图却没有 ready 且仍有非终态节点时，Orchestrator 自己写 scheduler_deadlock；这是状态机一致性错误，不会自动串行执行 pending 节点。两条路径都让图终止，避免生产协程挂住而 SSE 永远不结束。

Synthesizer 错误只影响最终表达，不会回滚已经完成的 TaskGraph。两次汇总均失败时，用户收到 `Team 汇总失败`，已完成节点及工具事件仍保存在会话历史中，可以用于人工排查。当前没有自动把 Worker summary 拼接成回答，因为那会把结构化中间结果冒充经过汇总的最终答复。

最终 ErrorEvent 与 DoneEvent 都会终止 chat 读取循环，前端也会清除 streaming。区别是 ErrorEvent 会成为时间线中的红色错误消息，DoneEvent 只负责结束状态而不额外渲染内容。流自然关闭的 `SSE_STREAM_END` 也只清理连接，不能被当成新的发送触发器。

## 十九、此前暴露的几个问题分别来自进度工具、搜索质量、旧数据和运行版本不一致

“为什么除了工具调用之外的进度 message 没了”并不是 SSE 丢消息，而是 Team 工具集合从未加入 MessageTool。单 Agent 能显示“正在搜索”“改用浏览器访问官网”等文字，是因为模型显式调用 `message_notify_user`，前端再把这类工具事件渲染成消息样式。Team Worker 目前只能调用业务工具，所以后端根本没有产生这些进度事件，若要补齐必须正式授权并接入 MessageTool，而不能在前端凭搜索工具状态猜文案。

“搜索很多次仍然失败”首先要区分编排错误与搜索结果质量。日志中 search_web 对明确问题返回 Bing 首页、词典或无关页面，Worker 因 success_criteria 尚未满足而继续换关键词，这是 ReAct 在处理低质量工具结果，不是 DAG 重复创建 task。随后达到迭代上限并进入第二个 task attempt，才会看到 attempt=2；当前错误汇总会把最终迭代上限写到对应任务描述后面。

`unobserved source URLs: ['https://github.com/trending']` 来自此前存在的运行时来源白名单校验，它要求 Worker 最终 sources 中的每个 URL 都曾被后端从工具结果观察到。当前分支已经删除这项二次校验，只保留 Worker 提示词中“sources 只能引用成功工具结果真实出现的 URL”的要求，因此当前源码不再主动抛出这条错误。代价是后端不再以代码证明来源归属，模型是否如实引用需要依赖提示词和人工观察。

`GET /api/sessions` 的 500 曾与数据库里遗留的旧 `skill` 类型事件有关。当前领域 `Event` 判别联合不包含 skill，仓库加载 Session events 时若遇到这种旧行，Pydantic 无法反序列化整条会话，最终会让会话列表接口失败；清空数据库与 Redis 能移除这批不兼容数据。当前代码没有增加 skill 兼容模型、数据迁移或跳过坏行逻辑，因此这次处理是环境数据清理，不是源码层的历史数据迁移方案。

聊天 stream 与 chat 请求不断出现的直接代码原因，是详情 Hook 在 Done 或 `SSE_STREAM_END` 后又启动 empty stream，而 empty stream 正常结束后也延迟自我重连。当前实现删除了这些正常结束后的递归重连，只允许 session 状态 effect 在进入一个确实未完成的既有会话时建立一次 empty stream。message stream 与 empty stream 还使用两个 cleanup ref 并在发送前互斥关闭，从连接所有权上消除了同一详情页反复开流的循环。

“单 Agent 是否与多 Agent 完全分开”的准确答案是执行 Flow 分开，基础设施共享。React 继续使用 `PlannerReActFlow`、原有 Plan/Step/MessageTool 行为和持久 Memory，Team 使用独立 Planner、DAG Orchestrator、短期 Worker Memory 与 Synthesizer；二者不会在同一消息中互相委派。Runner、沙箱、浏览器、搜索、文件存储、MCP/A2A、Redis、数据库、SSE 和前端通用组件继续复用，这是避免复制整套系统的有意设计。

源码配置与已经启动的容器不是自动同步关系，尤其当前 compose 运行方式没有把后端源目录 bind mount 到容器。工作区把 Team Worker 上限从分支提交中的 20 调为 50，而更早日志曾显示 6，这说明运行实例来自更旧的构建或配置。要验证当前源码行为需要重新构建并启动对应环境，但本文编写过程遵照要求没有执行构建、容器或本地服务验证。

## 二十、当前配置项给图规模、并发、重试、超时和 Worker 循环设置了明确上限

Team 的五项配置都放在 `AgentConfig`，没有新增 `.env` 文件或另一套配置加载器。Pydantic 同时约束默认值和可配置范围，避免把任务数、并发数或超时设成零和无穷大。当前工作区默认值可以用下面这段代码表示，具体部署仍可能通过既有配置机制覆盖。

```python
team_max_tasks = 5                 # 合法范围 1..20
team_max_workers = 3               # 合法范围 1..8
team_max_task_retries = 1          # 合法范围 0..3
team_task_timeout_seconds = 300    # 合法范围 30..1800
team_max_worker_iterations = 50    # 合法范围 1..100
```

`team_max_tasks` 同时影响 Planner 提示中的建议和后端构图硬限制，真正权威的是后端校验。`team_max_workers` 只限制同一并发安全批次，不会让三个 browser 节点并行；`team_max_task_retries` 表示失败后的重试次数，不包含首次 attempt。超时针对一次完整 attempt，迭代上限针对一次 attempt 内的 ReAct 决策循环，二者哪个先到就由哪个终止该 attempt。

全局 `max_retries=3` 仍作用于 Planner、Worker 和 Synthesizer 内部的 LLM 与工具调用，单 Agent 的 `max_iterations=100` 也保持不变。Team 只通过 `agent_config.model_copy()` 为 Worker 覆盖 max_iterations，不会修改原对象或连带改变 React Agent。因而切换到多 Agent 后看到的上限变化是局部配置，不是对单 Agent 行为的全局重构。

## 二十一、当前最小实现明确没有解决分布式恢复、动态重规划和强证据校验

Task registry 是 `RedisStreamTask` 类上的进程内字典，Redis 只保存输入输出事件流，不保存可重建的 Python Runner、活动 Flow 或 producer 协程。API 进程重启后，数据库 Session 仍可能标为 running，Redis 中也可能保留事件，但 `_task_registry` 已经丢失，`Task.get(task_id)` 无法恢复执行。当前没有启动时对账、租约超时、任务认领或从 TaskGraphEvent 重放调度的机制。

系统也没有面向多副本部署的分布式锁。同一 Session 的活动 Task 依靠当前进程 registry 和应用服务检查来约束，多个 API 进程若没有粘性路由与共享任务所有权，可能无法在另一个进程找到 Task。要演进成生产级分布式编排，需要把图状态、节点租约、attempt、幂等键和 Worker 队列持久化，而不是简单让 Worker 直接写同一 Redis Stream。

DAG 在本轮开始后保持静态，Worker 不能新增任务、改依赖、请求 Planner 修订或把任务交给另一个 capability。Team 运行中也不接受用户消息，所以没有 human-in-the-loop 的暂停、澄清和恢复。若问题本身必须边探索边改变计划，当前系统只能结束本轮后由用户发起新一轮，而不能在原 graph id 下动态扩图。

并发控制以 capability 为粒度，而不是以实际资源实例为粒度。两个 file_read 节点会并发读取同一沙箱，如果外部过程同时修改文件，仍可能读到不同版本；search 并发也受同一个搜索供应商限流和质量影响。策略只表达当前工程中的保守安全边界，没有实现资源锁、路径锁、浏览器 context 池或每 Worker 独立沙箱。

QueuedEventEmitter 没有背压和持久化，API 进程崩溃会丢失尚未被 Runner 消费的内存事件。Runner 的 Redis 后写数据库顺序也不是跨存储原子事务，任何一侧故障都可能留下短暂或永久不一致。当前最大五节点限制降低了风险，但没有从机制上提供 exactly-once 事件投递。

Worker 重试不会携带上一 attempt 的错误、已尝试查询或工具轨迹。它使用同一节点定义和同一依赖结果重新开始，因此模型可能再次采用相同搜索关键词并重现失败；当前也没有按错误类型决定是否值得重试。更精细的实现可以把 previous_attempt_error 和摘要传给新 Worker，但这属于后续设计，不在当前最小版本中。

来源和附件完整性主要依赖提示词。SourceRef 只校验 URL 格式，FinalTeamResponse 也只校验消息与附件字段结构，后端不再核对 sources 和 artifacts 是否确实来自该 attempt 的 ToolEvent。这样避免因工具事件提取不完整误杀有效结果，但也允许模型在违反提示词时提交未观察来源或路径。

前端没有绘制节点连线、依赖拓扑或甘特图，底部面板只是按任务数组顺序列出状态。对话区 StepBlock 目前也没有准确区分运行、完成和失败图标，任务级 error 不在折叠块内展示，自然语言进度 MessageTool尚未接入。当前 UI 已满足“完整任务在底部、已启动任务逐个进入对话并包含工具操作”的核心要求，但状态表达仍有进一步打磨空间。

历史数据库事件兼容没有迁移策略，当前 Event union 只接受当前声明的事件种类。旧版本写入的未知判别类型会在会话反序列化时造成失败，清库只能用于开发环境，不能作为正式升级方案。生产演进需要事件 schema 版本、迁移脚本或明确的旧事件适配器，同时仍应让真正损坏的数据可观察，而不是静默吞掉。

## 二十二、后端 23 个业务文件分别承担模型、编排、执行、传输和生命周期职责

`api/app/domain/models/team.py` 是新增功能的领域中心，定义模式、能力、任务与图状态、规划 DTO、运行时图、WorkerResult、SourceRef 和 FinalTeamResponse。`api/app/domain/models/event.py` 把 TaskGraphEvent、TeamTaskEvent、mode 与工具归属元数据纳入统一事件协议，`api/app/domain/models/session.py` 则负责从历史用户消息恢复最近 mode。`api/app/domain/models/app_config.py` 提供五项 Team 边界配置，这四个文件共同决定什么数据可以合法进入系统。

`api/app/domain/services/agents/team_planner.py` 负责把用户 Message 转成 PlannedTaskGraph，`task_worker.py` 负责执行一个节点的一个 attempt，`team_synthesizer.py` 负责把终态图转成最终回答。三者都继承 `BaseAgent`，但 Planner 和 Synthesizer 无工具，Worker 的工具由 capability 决定。`api/app/domain/services/agents/base.py` 增加可注入 Memory、关闭共享 Memory 持久化和工具白名单支持，同时保持未传这些参数的原 React Agent 走原行为。

`api/app/domain/services/prompts/team.py` 集中保存三类角色提示词，规定 Planner 的 DAG JSON、Worker 的结果契约和 Synthesizer 的汇总边界。`api/app/domain/services/team/graph.py` 实现构图、拓扑校验、ready 计算、skip 传播和最终状态归约，`orchestrator.py` 实现批次调度、attempt、超时和任务事件。`policy.py` 把 capability 映射到工具函数与并发安全类别，`team/__init__.py` 只是建立包边界。

`api/app/domain/services/flows/team.py` 是新增代码量最大的文件，它组装工具、Policy、Planner、Worker factory、Orchestrator 和 Synthesizer factory，并管理内存事件队列、最终图、错误和取消。`api/app/domain/services/flows/base.py` 新增默认 `cancel_events()`，让旧 Flow 不必实现 Team 取消快照也能保持多态。这个默认方法返回空列表，真正的 Team 取消语义由 TeamFlow 覆盖。

`api/app/domain/services/agent_task_runner.py` 把 mode 分流放进已有执行器，并继续承担附件同步、ToolEvent增强、Redis 与数据库顺序写入、Session 元数据更新和 MCP/A2A 生命周期。`api/app/domain/external/task.py` 给 Task 协议增加 wait，`api/app/infrastructure/external/task/redis_stream_task.py` 延后运行中任务的 registry 清理并实现取消等待。三者一起保证停止请求不是只设置一个布尔值，而是能够等待取消事件落盘和资源退出。

`api/app/application/services/agent_service.py` 负责 Team 运行中消息冲突、用户消息 mode 持久化、Task 创建、输入流写入和输出流消费。`api/app/application/errors/exceptions.py` 新增 HTTP 409 的 ConflictError，`api/app/interfaces/endpoints/session_routes.py` 在创建 SSE 前调用冲突校验。`api/app/interfaces/schemas/session.py` 把 mode 加入 ChatRequest，`api/app/interfaces/schemas/event.py` 把领域 Team 事件映射成对外 SSE schema，这五个文件形成应用与接口入口。

## 二十三、前端 7 个业务文件完成模式传递、事件类型、连接管理和双层展示

`ui/src/lib/api/types.ts` 是前端协议基线，新增 AgentMode、TeamTaskStatus、TaskGraphStatus、TaskGraph、TeamTask、两类 SSE 事件和 ToolEvent 归属字段。它还在 ChatParams 与 ChatMessage 中加入 mode，并暴露 Team 配置的可选类型。后续 Hook 和组件都从这里取得判别联合，避免用任意字符串判断 Team 事件。

`ui/src/app/page.tsx` 在首页保存 mode，并把它与初始文本和附件一起编码到 URL。`ui/src/app/sessions/[id]/page.tsx` 解码 payload、校验 team 值并把 initialMode 交给详情组件。`ui/src/components/chat-input.tsx` 渲染模式按钮、Team 运行锁和统一停止按钮，因此模式选择从创建会话到真正 POST chat 之间不会丢失。

`ui/src/hooks/use-session-detail.ts` 管理历史事件、会话状态、最后事件 id、empty stream 与 message stream 的互斥生命周期。它把 mode 加入 sendMessage 参数，识别图与任务运行态，并删除正常流结束后的递归重连。这个文件解决的是请求所有权和状态同步，不负责决定某个事件在页面上的视觉位置。

`ui/src/lib/session-events.ts` 是纯事件投影层，一条路径把最新 TaskGraph 与 Task 更新归并成底部 PlanStep，另一条路径只把 running/retrying TeamTask 及其工具归并进聊天时间线。它使用 graphId:taskId 管理任务块，用 tool_call_id 与 attempt 管理工具状态更新，并在新用户消息处重置归属上下文。`ui/src/components/session-detail-view.tsx` 调用这两条投影，把前者交给 PlanPanel、后者交给 ChatMessage，同时恢复 mode、处理发送和保持原有工具预览。

这里没有新增 Team 专用展示组件，`PlanPanel`、`ChatMessage`、`StepBlock` 和 ToolPreviewPanel 都是复用的既有组件，因此它们没有计入本分支修改文件。复用保证单 Agent 与多 Agent 每个 step 的视觉结构一致，也解释了为什么现有 StepBlock 图标语义会同时限制 Team 展示。后续若改善运行、失败图标或任务 error，可优先扩展通用 StepEvent/StepBlock，而不是再创建一套只服务 Team 的面板。

## 二十四、代码规模、工作区状态和本文的验证范围

以 `main` 到当前工作区执行 `git diff --numstat`，并排除 docs、测试文件、依赖与锁文件后，业务源码共 30 个文件，新增 1507 行、删除 138 行。后端是 23 个文件，新增 1223 行、删除 92 行；前端是 7 个文件，新增 284 行、删除 46 行。这个统计是物理差异行数，不把一行内部修改拆成语义操作，也不把本文档计入有效业务代码。

当前分支名是 `feature/team-dag-orchestration`，工作区相对当前 HEAD 有三处尚未提交的业务修改。它们分别是把 Team Worker 默认迭代上限从 20 调到 50、整图失败时聚合所有 task.error，以及对话区只在任务 running/retrying 后创建 Team Step；本文档本身也是新增文件。其余实现已经存在于该分支提交历史，本文所有行为描述以当前工作区文件为准。

本文编写期间只进行了源码阅读、git 静态差异统计和 Markdown 编辑，没有运行单元测试、集成测试、构建命令、开发服务、容器编排或本地 HTTP 验证，也没有修改 `.env`。因此文档能够确认代码结构与静态控制流，但不宣称当前环境已经完成运行验证。搜索供应商实际质量、模型是否稳定遵循 JSON、SSE 在具体浏览器中的表现和容器内配置值，仍应由用户按当前源码重新部署后手动验证。

当前分支没有为这轮整理继续添加测试文件，统计也明确排除了历史测试与已删除测试。对于这个最小实现，最重要的手工验证顺序是确认 mode 正确进入用户事件、初始图只出现在底部、并行 running 任务逐个出现在对话、依赖任务稍后追加、工具归属正确，以及 Done 或 Error 后 chat 不再重连。若手工验证出现偏差，应优先沿事件序列定位是后端未产生、SSE 未传输，还是前端投影未展示，而不是增加默认成功结果掩盖错误。

## 二十五、总结

当前多 Agent 功能的本质，是在不破坏原有 PlannerReActFlow 的前提下，新增一条用户显式选择的 DAG 执行路径。LLM 负责生成计划、执行节点和汇总结果，普通 Python 状态机负责校验依赖、控制并发、管理重试与超时、传播失败并决定图状态。所有 Worker 通过内存队列上报事件，再由 Runner 单点写 Redis 与数据库，前端则把同一事件流分别投影成底部完整计划和对话区增量执行过程。

这个设计已经覆盖最小多 Agent 所需的核心闭环：模式入口、DAG、并行 Worker、工具能力、结构化依赖结果、重试、取消、部分成功、错误详情、持久化恢复和 UI 展示。它没有把逻辑 Agent包装成独立服务，也没有引入分布式工作流平台、动态重规划、共享黑板或强证据系统，这些都被明确留在当前范围之外。判断实现是否正确的核心标准不是“Agent 数量看起来多”，而是依赖只在完成后解锁、共享可变工具不并发、任务事件归属不混乱、终止事件能关闭流，以及失败原因能够到达用户。
