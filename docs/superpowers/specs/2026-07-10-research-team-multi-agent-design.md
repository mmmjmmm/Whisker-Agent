# ResearchTeamFlow 多 Agent 研究系统设计

**状态：** 已批准，待实施计划
**日期：** 2026-07-10
**适用仓库：** `mooc-manus`
**首期范围：** 显式触发的研究与信息整合型多 Agent 流程

## 1. 决策摘要

mooc-manus 将新增一个与现有 `PlannerReActFlow` 并列的 `ResearchTeamFlow`。用户通过 `mode="research_team"` 显式启用它；默认 `mode="react"` 的行为保持不变。

首期采用以下组合架构：

- 模块化单体，不拆微服务。
- 中心编排，不采用自由群聊或无中心 Swarm。
- LLM Planner 生成动态研究 DAG。
- 代码 Orchestrator 负责依赖、并发、预算、超时、重试和状态流转。
- 3 至 5 个短生命周期同构 Worker 并行执行只读研究任务。
- Worker 输出结构化 Claim、Evidence 和 Source，不直接输出不可审计的最终答案。
- Coverage Reviewer 最多触发一轮补充研究。
- Synthesizer 只能使用已验证证据生成报告。
- Citation Verifier 拦截无证据或证据不支持的声明。

首期在现有 FastAPI 进程内使用 `asyncio` 受控并发。Agent Registry、自动模式选择、远程 A2A Worker、跨进程恢复和分布式调度属于后续独立项目。

## 2. 背景与目标

### 2.1 当前状态

当前 Agent 链路为：

```text
Session route
  -> AgentService
  -> AgentTaskRunner
  -> PlannerReActFlow
  -> PlannerAgent
  -> ReActAgent
  -> ToolEvent / MessageEvent / DoneEvent
```

这已经是一个窄义的双 Agent 工作流，但其执行结构是线性的：

- `PlannerAgent` 创建和更新计划。
- `ReActAgent` 通过 `Plan.get_next_step()` 逐步串行执行。
- 同一个 ReAct 上下文连续承担所有步骤。
- 工具事件由前端根据“最近活跃 Step”推断归属。

目标不是简单增加几个 Agent 类，而是建立可受控、可观察、可评测、可逐步平台化的团队执行能力。

### 2.2 首期产品目标

首期聚焦研究与信息整合任务：

- 多方向网页研究。
- 多来源事实收集与交叉验证。
- 产品、方案或观点比较。
- 时效信息整理。
- 基于可信证据生成带引用报告。

首期成功顺序为：

1. 答案质量与证据可追溯性。
2. 任务覆盖完整性。
3. 并行带来的响应速度收益。
4. token 和调用成本可控。

### 2.3 首期非目标

- 不自动判断是否启用多 Agent。
- 不替换或重写 `PlannerReActFlow`。
- 不采用 Agent 自由群聊、投票社会或无中心 Swarm。
- 不并行共享浏览器、Shell、文件写入、MCP 或 A2A。
- 不把远程 A2A Agent 纳入 Worker 调度。
- 不实现跨进程自动恢复。
- 不实现长期 Agent 记忆。
- 不实现完整 DAG 图形化编辑器。
- 不支持运行中的自由 Steering。
- 不以本规格重构所有历史 Session Event 存储。

## 3. 外部调研结论

### 3.1 有效的多 Agent 不是“越多越好”

Anthropic 将 Agentic System 区分为代码预定义路径的 Workflow 和由模型动态决定过程的 Agent，并建议从最简单的可组合模式开始。其常见模式包括并行化、Orchestrator-Workers 和 Evaluator-Optimizer。

Anthropic 的生产 Research 系统采用 Lead Agent、并行 Subagent 和 CitationAgent。其内部研究评测中，多 Agent 相对单 Agent 提升 90.2%，但多 Agent 的 token 使用量约为普通聊天的 15 倍。这说明研究任务适合用更多独立上下文换取覆盖度，但必须设置任务价值门槛与预算。

Google 2026 年对 180 种 Agent 配置的研究进一步说明：

- 中心化协调在可并行任务上相对单 Agent 提升 80.9%。
- 所有多 Agent 结构在强顺序任务上下降 39% 至 70%。
- 无中心独立 Agent 的错误放大可达 17.2 倍，中心化结构约为 4.4 倍。
- 工具越多，协调税越高。

因此，首期只对可拆分、低副作用的研究任务启用中心化并行，保留单 Agent 模式处理简单或强顺序任务。

### 3.2 主流协作拓扑

| 拓扑 | 工作方式 | 优点 | 主要风险 | 本项目判断 |
|---|---|---|---|---|
| 顺序流水线 | Agent A 输出作为 Agent B 输入 | 简单、确定 | 延迟高、无并行 | 作为局部阶段使用 |
| Router | 分类后路由到一个或多个专家 | 轻量、可并行 | 类别和专家需要预定义 | 后续自动模式候选 |
| Supervisor / Workers | 中心 Agent 动态拆解并委派 | 灵活、错误易收敛 | Supervisor 成为瓶颈 | 首期核心模式 |
| Dynamic DAG | 任务节点按依赖执行 | 并发与重试可控 | 状态模型更复杂 | 首期调度模型 |
| Agent as Tool | Manager 将子 Agent 当工具调用 | 边界直观、上下文隔离 | 深层嵌套难观察 | 可用于单个委派点 |
| Evaluator / Optimizer | 生成与评估循环 | 质量提升明显 | 易产生无限循环与高成本 | 限制为一次补充研究 |
| Handoff / Swarm | Agent 自行移交控制权 | 灵活、角色自治 | 路径难预测、共享上下文膨胀 | 首期不采用 |
| 固定角色 SOP | 固定 Researcher/Writer/Reviewer 流程 | 易理解、易演示 | 角色僵化、重复传递上下文 | 不作为运行时核心 |

### 3.3 主流框架对比

| 框架 | 强项 | 限制或现状 | 对 mooc-manus 的定位 |
|---|---|---|---|
| LangGraph | 动态 Graph、并行、Checkpoint、HITL、故障恢复 | 需要引入新的状态和事件运行时 | 第二阶段外部运行时首选 |
| OpenAI Agents SDK | Manager、Handoff、Guardrail、HITL、Tracing、非 OpenAI Provider 接口 | 显式动态 DAG 不是核心抽象 | 可参考 Agent/Tool 边界，不作为首期调度器 |
| Google ADK 2.0 | Graph Workflow、Collaborative Agent、隔离分支、A2A | 接入会重塑现有 Runner 和事件系统 | 平台化与 A2A 阶段重点观察 |
| Microsoft Agent Framework | AutoGen 与 Semantic Kernel 后继，Graph、Checkpoint、A2A | 截至调研时仍为 Public Preview | 不作为当前基础依赖 |
| AutoGen | GroupChat、Selector、Swarm、GraphFlow、事件运行时 | Microsoft 已引导迁移到 Agent Framework | 参考模式，不建议新项目绑定 |
| CrewAI | Role/Crew/Process/Flow 上手快 | Crew/Flow 生命周期与现有系统重复 | 适合原型，不适合本次增量改造 |
| Pydantic AI | 强类型输出、Provider、Graph、Evals、OTel | Graph 多 Agent 层仍偏底层 | 若未来替换 BaseAgent，可优先评估 |
| Strands Agents | Graph、Swarm、Workflow、A2A、Provider 无关 | 新生态且运行时模型与现有系统重叠 | 作为模式和 A2A 集成参考 |
| MetaGPT / CAMEL | 固定 SOP、角色社会、研究模拟 | 更偏垂直 SOP 或研究平台 | 不作为生产运行时基础 |

### 3.4 框架决策

首期不引入完整编排框架，原因如下：

- 当前项目已有 Agent、Tool、Flow、TaskRunner、Event、SSE 和 Sandbox 抽象。
- 目标并发规模只有 3 至 5 个 Worker，`asyncio` 足以满足。
- 直接引入 LangGraph 或 ADK 会形成双重状态、双重事件和双重生命周期。
- 当前最大风险是证据模型、资源隔离和可观测性，而不是缺少 Graph API。

新增能力必须通过领域接口与框架解耦，使未来可以把 Orchestrator 换成 LangGraph 等运行时，而不改变 API、Event 和 Research 数据模型。

## 4. 当前代码可行性评估

### 4.1 可复用基础

- FastAPI 和现有异步调用链可承载并发运行。
- `OpenAILLM` 使用 `AsyncOpenAI`，可以被多个隔离 Agent 实例调用。
- `RedisStreamTask` 已提供输入和输出消息流。
- Domain Event 已通过 Pydantic 判别联合建模。
- Session Event 可以原子追加到 PostgreSQL JSONB。
- Tool 有统一 Schema、查找与执行接口。
- Sandbox、Browser、Search、MCP、A2A 已有领域协议或适配器。
- 前端已有 Plan、Step、Tool 和 SSE 时间线基础。

### 4.2 必须解决的限制

#### Flow 固定

`api/app/domain/services/agent_task_runner.py` 在构造时固定实例化 `PlannerReActFlow`，请求中也没有 Agent Mode。需要把运行模式随 Run Command 传递，并通过 `FlowRouter` 选择 Flow。

#### Memory 键冲突

`BaseAgent` 使用静态 `self.name` 读取和保存 Session Memory。同构 Worker 若共享 `name="worker"` 会互相覆盖。Team Agent 必须支持 Run Scope 或 Ephemeral Memory。

#### 工具权限只有暴露层，没有执行层策略

`BaseAgent._get_available_tools()` 暴露全部 Tool Schema，`_get_tool()` 也能查找所有工具。Team Worker 需要 Schema 过滤与调用前运行时鉴权两道检查。

#### 单一共享浏览器页面

`PlaywrightBrowser` 维护单个 `self.page`，多个 Worker 同时导航会互相覆盖状态。因此浏览器不能进入首期并行工具集。

#### Task Registry 非持久化

Redis 保存消息流，但 `RedisStreamTask._task_registry` 位于进程内。API 重启后无法根据数据库或 Redis 重建正在运行的 Task。首期明确标记为 `interrupted`，第二阶段再实现自动恢复。

#### 前端工具归属依赖时间顺序

`ui/src/lib/session-events.ts` 使用 `lastStepId` 把 Tool 挂到最近 Step。并行事件必须携带 `run_id`、`task_id`、`attempt_id` 和 `agent_id`。

#### A2A 仅为最小自定义客户端

当前 A2A 实现只读取 Agent Card 并调用 `message/send`，没有完整 Task 生命周期、流式订阅、恢复、认证和 Push Notification。后续应使用官方 A2A 1.0 SDK，不继续扩展自定义 JSON-RPC 片段。

#### 测试基础薄弱

当前源代码中的 Agent/Flow 测试覆盖非常有限。多 Agent 实施必须先建立 Fake LLM、Fake Tool、Fake Memory Store 和可控时钟，否则并发与失败分支无法可靠验证。

### 4.3 对旧 TeamFlow 草案的修订

`docs/superpowers/specs/2026-07-09-teamflow-multi-agent-design.md` 的中心编排和 DAG 方向继续保留，但本规格取代其首期实现定义，主要修订为：

- 首期从通用 Team 模式收窄为 `research_team`。
- 增加 Claim、Evidence、Source 和 Citation 数据模型。
- 不允许 Planner 直接生成实际工具白名单。
- 不允许共享 Browser 并行。
- 增加 Run、TaskAttempt、预算与 token 用量模型。
- 增加一次受限补充研究，而非固定单轮 DAG。
- 增加 OpenTelemetry、质量评测和发布门槛。
- 明确首期不做自动恢复与远程 A2A 调度。

## 5. 总体架构

```text
ChatRequest(mode="research_team")
  -> AgentService
  -> StartRunCommand
  -> AgentTaskRunner
  -> FlowRouter
  -> ResearchTeamFlow
       -> AttachmentIngestor (可选、串行、只读)
       -> ResearchPlannerAgent
       -> ResearchOrchestrator
            -> ResearchWorker 1..N
            -> EvidenceNormalizer
            -> CoverageReviewerAgent
            -> optional repair wave
       -> SynthesizerAgent
       -> CitationVerifier
       -> FinalReportRenderer
  -> Domain Events
  -> Redis Stream + PostgreSQL
  -> SSE EventMapper
  -> Next.js UI
```

### 5.1 分层边界

#### Interface 层

- 校验 `ChatRequest.mode`。
- 把 Domain Event 映射为 SSE。
- 提供 Run、Task、Source 查询和取消接口。
- 不包含 DAG 调度逻辑。

#### Application 层

- `AgentService` 创建和查询 Agent Run。
- `FlowRouter` 选择 Flow。
- `AgentTaskRunner` 初始化共享基础设施、顺序发布事件、更新 Session 投影。
- 不让多个 Worker 直接写同一个 UoW。

#### Domain 层

- 定义 Run、Task、Attempt、Source、Evidence、Claim 和 Event。
- `ResearchTeamFlow` 组织研究阶段。
- `ResearchOrchestrator` 实现确定性调度。
- Agent 只处理需要模型推理的步骤。
- `ToolPolicy` 执行最小权限策略。

#### Infrastructure 层

- LLM Provider。
- PostgreSQL Repository。
- Redis Stream。
- OSS Source Content Storage。
- Stateless Web Reader。
- OpenTelemetry exporter。

### 5.2 Command 与 Event 分离

当前实现把用户 `MessageEvent` 同时作为队列输入和 UI 事件。Team 模式需要更清晰的控制面：

```text
RunCommand
  StartRunCommand
  CancelRunCommand
```

`StartRunCommand` 至少包含：

```text
command_id
run_id
session_id
mode
message
attachment_ids
requested_at
budget_profile
```

Domain Event 只描述已经发生的事实，不能承担启动或取消命令。旧 React Flow 可以暂时通过适配器把 `StartRunCommand` 转为现有 `Message`，避免一次性重写。

## 6. Agent 与组件职责

### 6.1 FlowRouter

输入：`StartRunCommand`。
输出：所选 `BaseFlow`。

规则：

```text
react         -> PlannerReActFlow
research_team -> ResearchTeamFlow
其他值         -> UnsupportedAgentMode
```

不提供 `auto` 模式。

### 6.2 ResearchTeamFlow

负责高层阶段：

1. 创建 Run。
2. 处理可读取附件并形成初始 Evidence。
3. 调用 Planner。
4. 校验 ResearchPlan。
5. 调用 Orchestrator。
6. 调用 Reviewer，并在需要时追加一次修复波次。
7. 调用 Synthesizer。
8. 调用 Citation Verifier。
9. 发布终态和最终消息。

它不实现底层 ready-task 计算、Semaphore 或资源锁。

### 6.3 ResearchPlannerAgent

- 不使用工具。
- 使用强类型输出生成 `ResearchPlan`。
- 给每个 Task 定义明确目标、依赖、来源要求和验收条件。
- 只能选择后端允许的 `capability_profile`。
- 不决定具体 Worker ID。
- 不决定实际工具白名单。
- 不能生成超过预算上限的 Task 数量或深度。

### 6.4 ResearchOrchestrator

它是普通 Python 组件，不是 LLM Agent。

职责：

- 校验和维护 TaskGraph。
- 计算 ready、blocked 和 terminal Task。
- 分配 Worker Slot。
- 使用 Semaphore 控制并发。
- 控制超时、取消、重试和退避。
- 维护 Run Budget。
- 汇聚 Worker Event。
- 顺序发布 Domain Event。
- 在依赖失败时传播 skipped 状态。

### 6.5 ResearchWorker

- 同构、无永久专家身份。
- 每次 Attempt 只执行一个 Task。
- 使用隔离的 Ephemeral Memory。
- 只看到原始目标、当前 Task、直接依赖结果、相关附件摘要和已知 Evidence 引用。
- 不看到其他并行 Worker 的完整消息或原始上下文。
- 只返回 `FindingBundle`。
- 不直接向用户生成最终答案。

### 6.6 EvidenceNormalizer

确定性代码组件：

- 规范化 URL。
- 校验协议、重定向和目标地址。
- 提取标题、域名、发布时间和抓取时间。
- 计算内容哈希。
- 按 canonical URL 与内容哈希去重。
- 保存正文或快照到 OSS。
- 保存受长度限制的 Evidence Excerpt 到 PostgreSQL。
- 拒绝不存在或无法定位的 Evidence 引用。

### 6.7 CoverageReviewerAgent

检查：

- 用户问题的子问题是否覆盖。
- 重要结论是否有足够来源。
- 是否过度依赖同一域名或二手聚合站。
- 不同来源是否存在冲突。
- 时效性是否符合问题要求。
- 是否存在无法回答但被 Worker 隐藏的问题。

输出只能为：

```text
approved
issues[]
conflicts[]
missing_questions[]
repair_tasks[]
```

`repair_tasks` 仍需经过后端图校验。初始 Task 与 Repair Task 的累计数量不能超过 `max_tasks`，Repair Task 也不能绕过深度、Capability 或预算限制。整个 Run 最多追加一轮。

### 6.8 SynthesizerAgent

- 不使用搜索、浏览器或写工具。
- 只能看到通过校验的 Finding、Claim、Evidence 和 Reviewer 结论。
- 输出结构化 `DraftReport`。
- 每个重要声明必须引用 Claim ID 或 Evidence ID。
- 明确呈现冲突、信息缺口和不确定性。
- 不得基于自身知识补充未出现在 Evidence 中的事实。

### 6.9 CitationVerifier

分两层：

1. 确定性校验：引用存在、Source 记录及已保存内容可读取、Evidence 属于当前 Run、Claim 与引用关系完整。
2. 语义校验：Evidence 是否真正支持 Claim，而不只是主题相关。

语义校验输出：

```text
supported
partially_supported
unsupported
```

无支持的 Claim 触发一次 Synthesizer 修复。修复后仍不支持的内容从最终报告删除，并把缺口写入限制说明。

### 6.10 FinalReportRenderer

它是确定性代码组件：

- 把 Claim/Evidence ID 转为 Markdown 链接。
- 合并重复来源。
- 生成来源列表。
- 保持最终文本与已验证 Draft 一致。
- 不调用模型，不新增事实。

## 7. 领域模型

### 7.1 枚举

```text
AgentMode
  react
  research_team

RunStatus
  pending
  planning
  running
  reviewing
  synthesizing
  completed
  partial
  failed
  cancelled
  interrupted

TaskStatus
  pending
  ready
  running
  completed
  failed
  skipped
  cancelled
  timed_out
  interrupted

AttemptStatus
  pending
  running
  completed
  failed
  cancelled
  timed_out
  interrupted

CapabilityProfile
  research_readonly
  analysis

ClaimSupportStatus
  unverified
  supported
  partially_supported
  unsupported
```

### 7.2 AgentRun

```text
id
session_id
mode
status
goal
plan_version
budget_snapshot
usage
error
started_at
finished_at
created_at
updated_at
```

`usage` 至少包含：

```text
llm_calls
tool_calls
input_tokens
output_tokens
total_tokens
worker_attempts
elapsed_ms
```

### 7.3 ResearchPlan

```text
id
run_id
version
title
goal
language
source_strategy
tasks[]
created_at
```

`source_strategy` 包含：

```text
freshness_requirement
preferred_source_types[]
minimum_independent_domains
known_authoritative_sources[]
```

### 7.4 AgentTask

```text
id
run_id
plan_version
description
objective
capability_profile
dependencies[]
acceptance_criteria[]
source_requirements
required
priority
status
assigned_agent_id
result_summary
error
attempt_count
created_at
updated_at
```

### 7.5 TaskAttempt

```text
id
run_id
task_id
attempt_number
agent_id
agent_profile
model_profile
status
usage
error_type
error_message
started_at
finished_at
```

幂等键为：

```text
(run_id, task_id, attempt_number)
```

### 7.6 ResearchSource

```text
id
run_id
canonical_url
original_url
title
domain
publisher
published_at
retrieved_at
content_type
content_hash
object_storage_key
source_class
metadata
```

`source_class` 可以是 `official`、`primary`、`secondary`、`community` 或 `unknown`。它只是审核信号，不能单独替代 Evidence 校验。

### 7.7 EvidenceExcerpt

```text
id
source_id
run_id
locator
excerpt
excerpt_hash
created_at
```

`locator` 可以是标题层级、段落序号、页面位置或其他可复现定位信息。Evidence 必须来自保存过的 Source 内容，不能由模型凭空创建。

### 7.8 ResearchClaim

```text
id
run_id
task_id
text
importance
confidence
caveats[]
support_status
evidence_ids[]
created_at
```

重要 Claim 默认需要两个独立域名。若来源本身是唯一权威一手来源，可以只使用一个，但必须记录原因。

### 7.9 FindingBundle

```text
task_id
summary
source_candidates[]
evidence_candidates[]
claim_candidates[]
unresolved_questions[]
notes[]
```

持久化前使用 Bundle 内局部引用：

```text
source_candidate
  source_ref
  original_url
  title?
  retrieved_at
  metadata

evidence_candidate
  evidence_ref
  source_ref
  locator
  excerpt

claim_candidate
  claim_ref
  text
  importance
  confidence
  caveats[]
  evidence_refs[]
```

Worker 输出先通过 Pydantic Schema，再进入 EvidenceNormalizer。Normalizer 保存 Source 和 Evidence 后，把 `source_ref`、`evidence_ref` 映射为数据库 ID，再创建 ResearchClaim 与 ClaimEvidence 关系。无法解析的局部引用使整个 FindingBundle 校验失败；自由文本不直接成为最终事实。

## 8. Graph 校验与调度

### 8.1 Graph 校验

后端必须拒绝：

- 空 TaskGraph。
- 重复 Task ID。
- 不存在的 Dependency。
- 自依赖。
- 有环图。
- 超过最大 Task 数量。
- 超过最大 Graph 深度。
- 未知 Capability Profile。
- 空 Objective 或空 Acceptance Criteria。
- 明显重复且没有不同研究边界的 Task。

Planner 首次输出非法时，把结构化 Validation Error 返回给 Planner 修复一次。第二次仍非法则 Run 失败。

### 8.2 Ready Task

Task 为 ready 的条件：

```text
status == pending
AND 所有 dependencies.status == completed
```

如果依赖进入 `failed`、`skipped`、`cancelled`、`timed_out` 或 `interrupted`：

- 所有依赖该结果的下游 Task 标记为 `skipped`，不能继续保持 `pending`。
- `required` 只决定缺失结果是否必须导致 Run 进入 `partial` 或 `failed`，不改变依赖语义。
- 不依赖该结果的其他分支继续执行。
- Run 最终可能进入 `partial`。

### 8.3 并发模型

```text
worker_semaphore = asyncio.Semaphore(max_workers)
```

首期只有以下 Profile 可并行：

```text
research_readonly -> search_web, web_read
analysis          -> no tools
```

共享 Browser、Shell、文件写入、MCP 和 A2A 不向 Worker 暴露，因此首期不需要为这些资源设计锁。

### 8.4 事件汇聚

Worker 不直接写 Redis、Session Repository 或 SSE。每个 Worker 把 Domain Event 写入 Run 内部 `asyncio.Queue`，Orchestrator 单点消费并：

1. 分配递增 `sequence_no`。
2. 持久化状态。
3. 发布 Redis Output Stream。
4. 生成 Session Event 投影。

这避免并发 UoW 使用、乱序写入和前端归属错误。

## 9. 工具与安全策略

### 9.1 ToolPolicy

Planner 只能选择 Capability Profile，后端通过代码映射工具：

```text
research_readonly:
  search_web
  web_read

analysis:
  no tools
```

双重校验：

1. 构建 LLM Tool Schema 时只暴露允许工具。
2. 执行每次 Tool Call 前再次检查 Profile、Tool Name 和 Run Policy。

模型输出永远不能成为授权依据。

### 9.2 Stateless Web Reader

新增无状态网页读取能力，不复用 Playwright 当前页面：

- 每次调用独立请求状态。
- 只允许 `http` 和 `https`。
- DNS 解析后阻止私网、回环、链路本地、保留地址和云元数据地址。
- 每次重定向重新校验目标。
- 限制重定向次数、响应大小、Content-Type 和总耗时。
- HTML 转换为受长度限制的文本或 Markdown。
- 保存原始响应哈希和必要元数据。
- 默认不执行页面 JavaScript。

需要登录、交互或 JavaScript 渲染的网站不由首期并行 Worker 处理，可由旧 React Flow 或未来隔离 Browser Worker 处理。

### 9.3 Prompt Injection 防护

- 所有网页和附件内容均标记为不可信数据。
- Prompt 明确禁止遵循 Source 中的指令。
- Worker 没有副作用工具，即使受到注入也无法写文件或执行命令。
- Source 内容不写入 Session 长期 Memory。
- Agent 间只传递结构化 Finding 和 Evidence 引用，不传递未经处理的完整对话。
- 外部数据不能改变 System Prompt、ToolPolicy、Budget 或 Run Goal。
- 记录异常工具请求和目标偏移信号。

### 9.4 凭据与日志

- 不在 Agent Context、Event 或 Trace 中传递 API Key。
- 默认不记录完整 Prompt、网页正文或 Tool 敏感参数。
- 内容级追踪只能通过显式开发配置启用，并执行脱敏。
- 仓库当前本地配置存在明文凭据风险；进入对外部署前必须单独完成凭据轮换与配置治理。本规格不修改本地配置文件。

## 10. Memory 与上下文

### 10.1 Memory Policy

引入 Memory Store 抽象：

```text
AgentMemoryStore
  load(memory_key)
  save(memory_key, memory)
```

实现：

```text
SessionMemoryStore   -> 现有 Planner/ReAct，行为不变
RunMemoryStore       -> Planner/Reviewer/Synthesizer 的运行级上下文
EphemeralMemoryStore -> Worker Attempt，运行结束即释放
```

不能再由静态 Agent Name 隐式决定 Memory Scope。

### 10.2 Worker 上下文

每个 Worker 仅接收：

- 原始用户目标。
- 当前 Task Objective。
- Acceptance Criteria。
- 直接依赖 Task 的 Result Summary。
- 与 Task 相关的 Evidence 引用。
- 附件摘要或已提取 Evidence。
- Capability Profile。
- 剩余 Attempt Budget。

Worker 不接收：

- 其他 Worker 的完整消息历史。
- Session 全量历史。
- 不相关 Source 正文。
- 系统凭据。
- 未授权工具定义。

### 10.3 Context 压缩

依赖结果超过限制时，传递结构化 Claim 和 Evidence ID，而不是重复复制全文。大段网页正文保存在 OSS，需要时按 Source ID 读取受限片段。

## 11. 持久化设计

### 11.1 新增表

首期新增：

```text
agent_runs
agent_tasks
agent_task_dependencies
agent_task_attempts
research_sources
evidence_excerpts
research_claims
claim_evidence
```

表名采用通用 Run/Task 与研究专用 Evidence 的组合，既不提前建设完整 Agent Registry，也避免未来把运行数据锁死在研究 Flow。

### 11.2 状态来源

- `agent_runs`、`agent_tasks`、`agent_task_attempts` 是运行状态来源。
- Research Source、Evidence 和 Claim 表是报告证据来源。
- Redis Stream 是实时传输，不是持久化真相来源。
- `sessions.events` 首期继续作为 UI 历史兼容投影。

### 11.3 Session Event 兼容

首期不迁移旧 Event 存储。Team Event 继续追加到 `sessions.events`，但必须带 Run/Task 关联字段。完整任务查询优先读取新表。

当事件量或跨进程需求增长时，再单独设计 append-only `agent_run_events` 表并迁移旧历史接口。

### 11.4 进程中断

首期不自动恢复正在执行的 Coroutine。应用启动或查询遗留 Run 时：

- 将超出心跳窗口仍为运行态的 Run 标记为 `interrupted`。
- 将其非终态 Task 和 Attempt 标记为 `interrupted`，错误类型记录为 `ProcessInterrupted`。
- UI 展示中断原因。
- 用户可以重新发起任务。

自动从成功节点恢复属于第二阶段。

## 12. Event 与 SSE

### 12.1 Event Envelope

所有 Team Event 包含：

```text
event_id
event_type
schema_version
session_id
run_id
task_id?
attempt_id?
agent_id?
parent_event_id?
sequence_no
created_at
payload
```

`sequence_no` 是单 Run 内由 Orchestrator 分配的严格递增序号。时间戳不作为唯一排序依据。

### 12.2 Event 类型

```text
run
research_plan
research_task
research_source
research_review
research_usage
tool
message
error
done
```

状态变化放在 Payload 的 `status` 字段中，不为每种状态创建不同 Event Type。

### 12.3 关键事件

#### RunEvent

```text
run_id
mode
status
goal
usage
error?
```

#### ResearchTaskEvent

```text
task
status
agent_id?
attempt_id?
progress?
```

#### ResearchSourceEvent

```text
task_id
source_id
title
url
domain
published_at?
```

#### ResearchReviewEvent

```text
approved
issues[]
conflicts[]
missing_questions[]
repair_plan_version?
```

#### ResearchUsageEvent

```text
budget
usage
remaining
```

#### ToolEvent 扩展

```text
run_id
task_id
attempt_id
agent_id
agent_profile
```

旧 React ToolEvent 允许这些字段为空，前端保留旧逻辑作为兼容分支。

### 12.4 终态规则

- 每个 Run 只发送一次终态 RunEvent。
- 每个 Run 只发送一次 `DoneEvent`。
- `ErrorEvent` 不自动代表整个 Run 失败；它必须带 Scope。
- Run 终态由 `completed`、`partial`、`failed`、`cancelled` 或 `interrupted` 表示。

## 13. API 设计

### 13.1 ChatRequest

```text
message
attachments[]
event_id?
timestamp?
mode = react | research_team
budget_profile? = default
```

`mode` 默认 `react`，保持向后兼容。

### 13.2 Run 查询

```text
GET /api/sessions/{session_id}/runs/{run_id}
GET /api/sessions/{session_id}/runs/{run_id}/tasks
GET /api/sessions/{session_id}/runs/{run_id}/sources
```

### 13.3 取消

```text
POST /api/sessions/{session_id}/runs/{run_id}/cancel
```

取消必须幂等。已经终态的 Run 返回当前终态，不重复触发 Done。

### 13.4 活跃 Run 冲突

首期每个 Session 同一时间只允许一个活跃 Run。运行中再次提交普通消息返回：

```text
409 RUN_ALREADY_ACTIVE
```

响应包含当前 `run_id` 和状态。UI 提供等待或取消选项，不隐式停止当前研究。

## 14. 前端设计

### 14.1 模式选择

Chat Input 使用分段控制：

```text
单 Agent | 研究团队
```

选择只影响下一次提交，不改变 Session 永久配置。

### 14.2 研究进度

首期不画完整 DAG。按研究波次和拓扑顺序展示 Task：

- Task 描述。
- 状态。
- Worker ID。
- Attempt 次数。
- 来源数量。
- 耗时。
- 嵌套 Tool Event。

### 14.3 来源与引用

来源面板展示：

- 标题与域名。
- 来源分类。
- 发布时间与抓取时间。
- 支持的 Claim。
- 打开原始链接。

最终答案中的引用与 Source ID 对应，不能只展示不可验证的数字脚注。

### 14.4 状态展示

必须区分 Run 终态：

```text
completed
partial
failed
cancelled
interrupted
```

`budget_exhausted` 是 `partial` 或 `failed` 的终态原因，不是独立 RunStatus；UI 必须单独展示该原因。

`partial` 需要显示缺失任务或证据不足，不得使用成功样式掩盖。

### 14.5 Timeline 聚合

新事件按 `run_id/task_id/tool_call_id` 聚合。只有缺少这些字段的旧事件才使用 `lastStepId` fallback。

### 14.6 协议演进

首期保留自定义 SSE，但 Event Envelope 应能映射到 AG-UI 的 Run、Step、Tool、State Snapshot/Delta 和 Interrupt 模型。完整 AG-UI 适配属于第二阶段。

## 15. Budget 与模型策略

### 15.1 默认预算

```text
max_workers                 = 4
max_tasks                   = 8
max_graph_depth             = 3
max_research_waves          = 2
max_attempts_per_task       = 2
task_timeout_seconds        = 180
run_timeout_seconds         = 900
max_llm_calls               = 24
max_tool_calls              = 60
max_total_tokens            = 150000
```

这些值可配置，但创建 Run 时必须保存不可变预算快照。

### 15.2 预算执行

- 每次 LLM 和 Tool 调用前，在 Run 级锁内原子预留调用次数和 token 上限。
- 调用完成后按实际 Usage 结算并释放未使用的 token 预留；失败调用仍计入调用次数。
- 并行 Worker 共享同一 Run Budget Manager，不能各自维护预算副本。
- 达到硬限制后不启动新 Task。
- 已完成 Evidence 保留，Run 进入 `partial` 或 `failed`。
- 预算耗尽是明确错误类型，不作为普通模型失败重试。

### 15.3 LLM 返回模型

现有 LLM 接口只返回 Assistant Message，不保留 Usage。实施时需要使用类型化结果：

```text
LLMInvocationResult
  message
  model
  provider_request_id?
  finish_reason?
  usage
```

旧 Agent 可以通过兼容适配器继续读取 `message`。

### 15.4 角色模型配置

支持：

```text
default_model
planner_model
worker_model
reviewer_model
synthesizer_model
```

未配置角色自动回退到 `default_model`。首期可以全部使用现有模型，不要求立即修改本地配置。

## 16. 错误处理

### 16.1 错误分类

```text
ValidationError
PolicyViolation
BudgetExceeded
ModelRateLimited
ModelTimeout
ModelOutputInvalid
ToolTransientError
ToolPermanentError
TaskTimeout
RunCancelled
InfrastructureUnavailable
ProcessInterrupted
```

### 16.2 重试规则

允许重试一次：

- 模型限流。
- 临时网络错误。
- 只读 Tool 的临时 5xx。
- 首次结构化输出解析失败。

禁止重试：

- ToolPolicy 越权。
- SSRF 或非法 URL。
- 预算耗尽。
- 用户取消。
- 明确 4xx 或权限错误。
- 已知不支持的内容类型。

退避使用带抖动的指数退避，并受 Task Timeout 约束。

### 16.3 Partial 语义

满足以下条件时允许 `partial`：

- 至少有一个经过验证的重要 Claim。
- 最终报告明确列出失败、跳过或缺失项。
- Citation Verifier 已删除无支持内容。

没有有效 Evidence 时必须 `failed`，不能生成凭模型常识回答的报告。

### 16.4 取消

- 取消信号传播到所有 Worker Task。
- 等待子任务完成清理或达到取消超时。
- 非终态 Task 标为 `cancelled`。
- 保存已取得的 Source，但不生成误导性完成报告。
- 只发一次终态事件和 DoneEvent。

## 17. 可观测性

### 17.1 Trace 结构

采用 OpenTelemetry GenAI Semantic Conventions：

```text
invoke_workflow ResearchTeamFlow
  invoke_agent ResearchPlanner
  invoke_agent ResearchWorker
    execute_tool search_web
    execute_tool web_read
  invoke_agent CoverageReviewer
  invoke_agent Synthesizer
  invoke_agent CitationVerifier
```

所有 Span 携带非敏感关联信息：

```text
session_id
run_id
task_id?
attempt_id?
agent_id
agent_profile
model
status
```

### 17.2 Metrics

- Run completed、partial、failed、cancelled 比率。
- P50/P95 Run 和 Task 耗时。
- 各角色 LLM Calls 与 token。
- Tool Calls、错误和延迟。
- Worker 并发利用率。
- 平均 Task 数和研究波次数。
- Source 数、独立域名数和去重率。
- Claim 引用覆盖率。
- Unsupported Claim 比率。
- Retry、Timeout 和 Budget Exhausted 比率。

### 17.3 内容隐私

默认只采集元数据。Prompt、模型输出、网页正文和 Tool 参数需要显式开发开关，并先执行脱敏。

## 18. 测试策略

### 18.1 测试基础设施

先提供：

- `FakeLLM`：按调用顺序返回预设结构化结果。
- `FakeSearchEngine`：返回确定来源。
- `FakeWebReader`：返回固定正文和元数据。
- `FakeMemoryStore`。
- `FakeClock` 或可注入时钟。
- 可观测并发数的 Worker Probe。

测试不依赖真实模型、互联网、Redis 或 PostgreSQL，除非明确属于集成测试。

### 18.2 单元测试

- Graph 缺失依赖、重复 ID、自依赖和环。
- Graph Task 数量与深度限制。
- ready-task 计算。
- 依赖失败传播。
- Semaphore 最大并发。
- Tool Schema 过滤。
- 未授权 Tool 的运行时拒绝。
- URL 规范化与 SSRF 防护。
- Redirect 二次校验。
- Source 去重和 Content Hash。
- Claim/Evidence 关系校验。
- Citation Support 状态转换。
- Budget 并发原子扣减。
- Event sequence 与 Correlation ID。
- 幂等取消和 DoneEvent 单次发送。

### 18.3 组件测试

- Planner -> Worker -> Reviewer -> Synthesizer 成功链路。
- 两个独立 Task 真正重叠执行。
- Worker 超时后重试。
- 一个 Required Task 失败后生成 Partial。
- 所有 Task 无 Evidence 时 Run Failed。
- Reviewer 只允许一个 Repair Wave。
- Synthesizer 无依据 Claim 被删除。
- 取消传播到所有 Worker。
- Event Queue 在并发完成顺序下仍保持一致关联。

### 18.4 集成测试

- Team Run 状态写入 PostgreSQL。
- Redis Output Stream 和 SSE 顺序一致。
- 断线后从 Event ID 继续读取。
- Session 刷新后恢复 Run、Task 和 Source 展示。
- React Mode 仍使用原 Flow。
- 未知 Mode 返回稳定错误。
- Active Run 冲突返回 409。
- OSS Source Content 保存和读取。

### 18.5 前端测试

- 模式选择正确传参。
- Task Event 创建与更新。
- Tool 按 Task ID 归属。
- 同一 Tool Call 的 Calling/Called 合并。
- 旧 Event fallback。
- Partial、Failed、Cancelled、Interrupted 展示。
- Source 与 Claim 双向定位。
- 引用链接正确。

## 19. 研究质量评测

### 19.1 评测集

建立 30 至 50 条版本化研究评测集，覆盖：

- 多方向广度研究。
- 多产品或多方案比较。
- 时效性问题。
- 来源互相矛盾。
- 单一权威来源。
- 无可靠答案。
- 搜索结果重复或低质量。
- 网页 Prompt Injection。
- 一个 Worker 故障。
- 预算不足场景。

### 19.2 评测方法

不能只依赖单一 LLM Judge。组合：

- 确定性 Schema 和 Citation 检查。
- Claim 与 Evidence 人工抽检。
- 稳定问题的 Reference Answer。
- 多维 LLM Judge：正确性、覆盖度、冲突处理、表达质量。
- Source Diversity 和 Authority 指标。
- 与现有单 Agent 的同模型、近似预算对照。

### 19.3 发布门槛

```text
重要 Claim 引用覆盖率          >= 95%
引用实际支持 Claim 的准确率    >= 90%
无依据重要 Claim 比率          <= 3%
综合研究质量相对单 Agent       提升 >= 15%
至少两个独立 Task 真实并发执行
预算、并发和 ToolPolicy 无绕过
React Mode 无行为回归
取消、超时和 Partial 均能收敛
```

质量提升未达标时不得仅凭“多个 Agent 已运行”验收。

## 20. 分阶段交付

### Phase 1A：后端核心与测试

- Run、Task、Attempt、Source、Evidence、Claim 模型与迁移。
- Memory Store 抽象。
- LLM Usage 结果模型。
- Stateless Web Reader。
- ToolPolicy。
- Planner、Worker、Reviewer、Synthesizer、Citation Verifier。
- ResearchOrchestrator。
- Fake 基础设施与单元/组件测试。

### Phase 1B：API、SSE 与 UI

- `research_team` 请求模式。
- Run 查询和取消 API。
- Team Domain Event 和 SSE Mapping。
- 前端模式选择。
- Task、Source、Review 和 Usage 展示。
- 旧事件兼容。

### Phase 1C：评测与灰度

- 研究评测集。
- OpenTelemetry。
- 内部 Feature Flag。
- 预算与 Prompt 调优。
- 与 React Mode 对照评测。
- 达到发布门槛后逐步开放。

### Phase 2：可靠运行与平台基础

单独设计：

- Durable Checkpoint 与 Resume。
- HITL Approval。
- Append-only Run Event Store。
- AG-UI Adapter。
- Agent Registry 与 Capability Discovery。
- 自动模式建议，但仍允许用户覆盖。

### Phase 3：远程与分布式

单独设计：

- 官方 A2A 1.0 Python SDK。
- Remote Agent Card、认证、Task、Streaming 和 Resume。
- Local/Remote Agent Executor 统一接口。
- 独立 Worker Service。
- 租约、心跳、幂等和故障恢复。
- 跨机器配额与成本治理。

## 21. 验收标准

- `mode` 默认为 `react`，现有行为不变。
- 用户可以显式选择 `research_team`。
- Research Planner 能生成并通过校验的 DAG。
- 至少两个无依赖研究 Task 能在并发上限内同时运行。
- Worker 之间 Memory 隔离。
- Worker 不能调用未授权 Tool。
- Browser、Shell、写文件、MCP 和 A2A 不进入并行工具集。
- 每个重要 Claim 可追溯到 Source 和 Evidence Excerpt。
- 无支持 Claim 不进入最终报告。
- Reviewer 最多触发一个 Repair Wave。
- Run Budget 能限制 Agent、LLM 和 Tool 扩张。
- Worker Event 都有 Run、Task、Attempt 和 Agent 关联。
- 前端刷新后能正确显示 Run、Task、Source 和最终报告。
- Worker 失败时系统能生成诚实的 Partial 或明确 Failed。
- Cancel、Timeout 和 Budget Exhausted 都有稳定终态。
- 质量评测达到第 19.3 节门槛。

## 22. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Planner 拆分重复或遗漏 | 浪费成本、报告缺失 | Graph 校验、明确边界、Coverage Review |
| 多 Agent token 激增 | 成本不可控 | Run Budget、最多两轮、角色模型配置 |
| Worker 来源低质量 | 错误结论 | Source Strategy、独立域名、Evidence 校验 |
| 网页 Prompt Injection | 目标偏移或越权 | 不可信内容边界、只读工具、运行时鉴权 |
| 共享资源竞态 | 页面或文件状态污染 | 首期只并行无状态工具 |
| 并发事件乱序 | UI 错配、审计困难 | 单点事件队列、sequence_no、关联 ID |
| Worker Memory 冲突 | 上下文污染 | Ephemeral Memory Store |
| Reviewer 无限返工 | 延迟和成本失控 | 最多一个 Repair Wave |
| 进程重启丢失运行 | 用户任务中断 | 首期标记 interrupted，Phase 2 checkpoint |
| Session JSONB 事件膨胀 | 查询和更新变慢 | 首期限制事件粒度，Phase 2 独立 Event Store |
| 自定义 A2A 漂移 | 协议不兼容 | Phase 3 使用官方 A2A 1.0 SDK |
| 只看 Agent 数量验收 | 形式完成但质量无提升 | 固定评测集和发布门槛 |

## 23. 参考资料

- Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Google Research, [Towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- LangChain, [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- LangChain, [Multi-agent Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- LangChain, [Custom Workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)
- OpenAI, [Agents SDK Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- OpenAI, [Agents SDK Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- Google, [ADK Collaborative workflows](https://adk.dev/workflows/collaboration/)
- Google, [ADK Graph-based workflows](https://adk.dev/graphs/)
- Microsoft, [Agent Framework Overview](https://learn.microsoft.com/en-us/agent-framework/overview/)
- CrewAI, [Processes](https://docs.crewai.com/en/concepts/processes)
- Pydantic, [Multi-Agent Patterns](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)
- Strands Agents, [Multi-agent Patterns](https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/)
- A2A Project, [A2A 1.0 Specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)
- AG-UI, [Events](https://docs.ag-ui.com/concepts/events)
- OpenTelemetry, [GenAI Observability](https://opentelemetry.io/blog/2026/genai-observability/)
- OWASP, [AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
- Cemri et al., [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)

## 24. 最终决策

首期选择原生增量式 `ResearchTeamFlow`，不引入完整外部编排框架。实现必须以领域模型、协议边界、证据质量和可观测性为中心，而不是以 Agent 数量为中心。

只有在研究质量、引用准确性、并发真实性、预算控制和旧模式兼容性同时达到验收门槛时，首期才算完成。
