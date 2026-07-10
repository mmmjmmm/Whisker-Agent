# ResearchTeamFlow Multi-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有 PlannerReActFlow 行为的前提下，增加显式触发、可审计、受预算控制的 research_team 多 Agent 研究流程。

**Architecture:** 首期采用模块化单体和进程内 asyncio 调度。ResearchPlannerAgent 生成受约束的研究 DAG，ResearchOrchestrator 管理 3–5 个短生命周期同构 Worker；Worker 只访问无状态只读网页工具并产出结构化 Source、Evidence、Claim，随后经过 Coverage Review、Synthesizer 和 Citation Verifier。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy/Alembic、Redis Streams、现有 LLM/Tool/Flow 抽象、Next.js/React/TypeScript、pytest、Vitest/Testing Library、OpenTelemetry。

---

## 实施约束

- 默认请求仍使用 mode=react，不能改变 PlannerReActFlow 的工具、Memory 和事件兼容行为。
- research_team 只允许 search_web 和新增 web_read；Browser、Shell、文件写入、MCP、A2A 不加入并行 Worker 的 Tool Schema，也不能通过运行时调用绕过策略。
- 首期不编辑 .env、api/config.yaml 或其他本地运行配置；模型角色配置从现有配置读取，没有配置时回退到现有默认模型。
- 首期不运行项目、开发服务、容器或真实互联网验证。实施阶段若要运行测试、迁移检查或前端检查，先取得用户对项目运行操作的明确同意。
- 首期部署限制为单个 API 进程；多进程/多副本要等 Phase 2 的租约、Checkpoint 和 Durable Executor 完成后再开放。
- 所有新增 Domain Event 必须携带 run_id、task_id、attempt_id、agent_id（适用时）和单 Run 递增 sequence_no；旧 React Event 字段保持可空。
- 每个实现任务先写失败测试，再写最小实现；每个任务完成后单独提交。

## 文件地图

| 文件 | 责任 |
|---|---|
| api/app/domain/models/agent_run.py | AgentRun、AgentTask、TaskAttempt、Usage、Budget 和终态 |
| api/app/domain/models/research.py | ResearchPlan、Source、Evidence、Claim、Finding DTO |
| api/app/domain/models/event.py | Team Event Envelope 与 Research Event |
| api/app/domain/services/research/graph.py | DAG 校验、ready-task 和依赖失败传播 |
| api/app/domain/services/research/orchestrator.py | 并发、Attempt、超时、重试、取消、预算和事件汇聚 |
| api/app/domain/services/research/policy.py | Capability Profile 与实际 Tool 的后端映射 |
| api/app/domain/services/research/web_reader.py | HTTP/HTTPS 读取、SSRF、重定向和大小限制 |
| api/app/domain/services/research/evidence.py | Source/Evidence 去重和 ClaimEvidence 建立 |
| api/app/domain/services/agents/research.py | Planner、Worker、Reviewer、Synthesizer、Citation Verifier |
| api/app/domain/services/flows/research_team.py | 高层研究阶段编排 |
| api/app/domain/services/agent_task_runner.py | Run Mode 路由并保持 React 兼容 |
| api/app/application/services/agent_service.py | 创建 Run、活跃 Run 冲突、取消 |
| api/app/interfaces/schemas/session.py | ChatRequest.mode 和 Run API Schema |
| api/app/interfaces/schemas/event.py | Team Event 到 SSE 的映射 |
| api/app/interfaces/endpoints/session_routes.py | Chat、Run/Task/Source 查询和取消 |
| api/alembic/versions/20260710_create_agent_runs.py | 持久化迁移 |
| ui/src/lib/api/types.ts | Mode、Run、Task、Source、Claim、Team Event 类型 |
| ui/src/lib/api/session.ts | Chat Mode、Run 查询和取消客户端 |
| ui/src/lib/session-events.ts | 并行事件关联与旧事件 fallback |
| ui/src/components/chat-input.tsx | 单 Agent/研究团队分段选择 |
| ui/src/components/research-run-panel.tsx | 波次、Task、来源、Review、Usage 展示 |
| api/tests/app/domain/services/research/* | Domain、Graph、Policy、Budget、Evidence、Orchestrator 测试 |
| api/tests/app/interfaces/endpoints/test_research_routes.py | API/SSE/兼容性测试 |
| ui/src/lib/__tests__/session-events.test.ts | 前端并行事件测试 |

### Task 1: 建立 Run、Task、Attempt 和研究证据领域模型

**Files:**
- Create: api/app/domain/models/agent_run.py
- Create: api/app/domain/models/research.py
- Modify: api/app/domain/models/__init__.py
- Create: api/alembic/versions/20260710_create_agent_runs.py
- Create: api/tests/app/domain/models/test_research_models.py

- [ ] **Step 1: 写模型失败测试**

```python
def test_research_plan_rejects_unknown_capability():
    with pytest.raises(ValidationError):
        ResearchPlan.model_validate({"tasks": [{"capability_profile": "shell"}]})

def test_claim_requires_evidence_refs_for_important_claim():
    with pytest.raises(ValidationError):
        ResearchClaim.model_validate({"importance": "important", "evidence_ids": []})
```

- [ ] **Step 2: 运行测试确认失败**

在获得测试运行许可后执行：cd api && pytest tests/app/domain/models/test_research_models.py -q。预期：因新增模型尚不存在而失败。

- [ ] **Step 3: 实现模型和迁移**

定义 AgentMode、RunStatus、TaskStatus、AttemptStatus、Usage、RunBudget、AgentRun、AgentTask、TaskAttempt、CapabilityProfile、ResearchPlan、SourceCandidate、EvidenceCandidate、ClaimCandidate、ResearchSource、EvidenceExcerpt、ResearchClaim 和 ClaimEvidence。AgentRun 增加 heartbeat_at；重要 Claim 没有 Evidence 时拒绝；Repair Task 与初始 Task 共用 max_tasks 上限。

迁移创建 agent_runs、agent_tasks、agent_task_dependencies、agent_task_attempts、research_sources、evidence_excerpts、research_claims、claim_evidence，建立 run/task/attempt 外键和 (run_id, task_id, attempt_number) 唯一约束，不修改现有 Session 表。

- [ ] **Step 4: 运行模型测试和迁移静态检查**

```bash
cd api
pytest tests/app/domain/models/test_research_models.py -q
alembic check
```

预期：模型测试通过；alembic check 报告无待生成迁移。

- [ ] **Step 5: 提交**

```bash
git add api/app/domain/models api/alembic/versions api/tests/app/domain/models/test_research_models.py
git commit -m "feat: add research run and evidence models"
```

### Task 2: 抽象 Memory、Usage 和 ToolPolicy

**Files:**
- Create: api/app/domain/services/research/policy.py
- Create: api/app/domain/services/research/budget.py
- Create: api/app/domain/services/research/memory.py
- Modify: api/app/domain/services/agents/base.py
- Create: api/tests/app/domain/services/research/test_policy.py
- Create: api/tests/app/domain/services/research/test_budget.py
- Create: api/tests/app/domain/services/research/test_memory.py

- [ ] **Step 1: 写隔离、权限和并发预算失败测试**

```python
async def test_worker_profile_exposes_only_readonly_tools():
    policy = ToolPolicy()
    assert policy.allowed_tools("research_readonly") == {"search_web", "web_read"}
    assert policy.authorize("research_readonly", "shell") is False

async def test_budget_reservation_cannot_oversubscribe():
    budget = RunBudgetManager(RunBudget(max_llm_calls=1, max_total_tokens=100))
    results = await asyncio.gather(budget.reserve_llm(tokens=80), budget.reserve_llm(tokens=80))
    assert sum(results) == 1
```

- [ ] **Step 2: 实现策略、预算和 Memory**

ToolPolicy 将 research_readonly 映射到 search_web/web_read，将 analysis 映射到空集合；提供 Schema 过滤和调用前 authorize 两个入口。RunBudgetManager 用单个 Run 级 asyncio.Lock 原子预留调用次数和 LLM token，完成后结算实际用量，失败调用仍计数，超限返回 BudgetExceeded。MemoryStore 提供 SessionMemoryStore、RunMemoryStore、EphemeralMemoryStore；修改 BaseAgent 使 Scope 显式传入，React/Planner 默认继续使用 Session Scope。

- [ ] **Step 3: 运行测试**

```bash
cd api
pytest tests/app/domain/services/research/test_policy.py tests/app/domain/services/research/test_budget.py tests/app/domain/services/research/test_memory.py -q
```

预期：通过且无真实 LLM、Redis、PostgreSQL 或网络依赖。

- [ ] **Step 4: 提交**

```bash
git add api/app/domain/services/research api/app/domain/services/agents api/tests/app/domain/services/research
git commit -m "feat: add research policy memory and budget boundaries"
```

### Task 3: 实现无状态网页读取和证据归一化

**Files:**
- Create: api/app/domain/services/research/web_reader.py
- Create: api/app/domain/services/research/evidence.py
- Create: api/app/domain/external/web_reader.py
- Create: api/app/infrastructure/external/web_reader/http_web_reader.py
- Modify: api/app/domain/services/tools/__init__.py
- Create: api/tests/app/domain/services/research/test_web_reader.py
- Create: api/tests/app/domain/services/research/test_evidence.py

- [ ] **Step 1: 写安全和引用映射失败测试**

```python
async def test_reader_rejects_private_and_metadata_addresses(fake_dns):
    reader = HttpWebReader(resolver=fake_dns)
    with pytest.raises(WebReadPolicyError):
        await reader.read("http://169.254.169.254/latest/meta-data")

async def test_reader_revalidates_redirect_target(fake_http, fake_dns):
    fake_http.redirect("https://public.example", "http://127.0.0.1/admin")
    with pytest.raises(WebReadPolicyError):
        await reader.read("https://public.example")
```

- [ ] **Step 2: 实现 HTTP 读取策略**

只接受 HTTP/HTTPS；解析域名后拒绝回环、私网、链路本地、保留地址和云元数据地址，并在实际连接后再次校验 peer IP。每次重定向都重新校验，限制重定向次数、响应字节数、Content-Type 和总耗时；默认不执行 JavaScript。返回 URL、标题、域名、发布时间、抓取时间、正文哈希和受限文本。

- [ ] **Step 3: 实现 EvidenceNormalizer**

规范化 URL、按 canonical URL/content hash 去重、保存 Source 快照和 Evidence Excerpt，将局部 source_ref/evidence_ref 映射到持久化 ID。网页正文始终作为不可信数据，不能改变 ToolPolicy、预算或 Run Goal。

- [ ] **Step 4: 运行安全和证据测试**

```bash
cd api
pytest tests/app/domain/services/research/test_web_reader.py tests/app/domain/services/research/test_evidence.py -q
```

预期：覆盖私网/元数据地址、DNS 重绑定、重定向、大小限制、去重和引用映射。

- [ ] **Step 5: 提交**

```bash
git add api/app/domain/services/research api/app/domain/external/web_reader.py api/app/infrastructure/external/web_reader api/tests/app/domain/services/research
git commit -m "feat: add safe web reader and evidence normalization"
```

### Task 4: 实现 ResearchPlan 图校验和确定性调度器

**Files:**
- Create: api/app/domain/services/research/graph.py
- Create: api/app/domain/services/research/orchestrator.py
- Create: api/tests/app/domain/services/research/test_graph.py
- Create: api/tests/app/domain/services/research/test_orchestrator.py

- [ ] **Step 1: 写 DAG 和并发失败测试**

```python
def test_graph_rejects_cycle_and_unknown_dependency():
    with pytest.raises(GraphValidationError):
        validate_plan(plan_with_cycle())

async def test_independent_tasks_overlap_but_never_exceed_four_workers():
    probe = ConcurrencyProbe()
    result = await ResearchOrchestrator(max_workers=4).run(plan_with_two_independent_tasks(), probe)
    assert probe.max_seen >= 2
    assert probe.max_seen <= 4
```

- [ ] **Step 2: 实现 GraphValidator**

拒绝空图、重复 ID、未知依赖、自依赖、环、未知 Capability、空 Objective/Acceptance Criteria、超过 max_tasks 或 max_graph_depth 的图。Planner 第一次校验失败时返回结构化错误供一次修复；第二次仍失败则 Run Failed。Repair Task 与初始 Task 累计计数。

- [ ] **Step 3: 实现 ResearchOrchestrator**

用 asyncio.Semaphore(max_workers) 控制 Worker Slot；ready 条件为所有依赖 completed。依赖失败、跳过、取消、超时或中断时，所有直接下游标记 skipped，独立分支继续。每个 Attempt 按 (run_id, task_id, attempt_number) 幂等，临时错误最多重试一次并使用带抖动指数退避；ToolPolicy、SSRF、预算耗尽、用户取消和明确 4xx 不重试。

所有 Worker Event 进入内部 asyncio.Queue，由 Orchestrator 内的 EventSequencer 分配递增 sequence_no；状态事务提交后把有序 Event 交给 Flow 输出。AgentTaskRunner 是应用层唯一 Event Sink，按顺序发布 Redis Output Stream、生成 Session Event 投影并更新兼容字段。Worker 不直接写 Repository、Redis 或 SSE。

- [ ] **Step 4: 运行调度测试**

```bash
cd api
pytest tests/app/domain/services/research/test_graph.py tests/app/domain/services/research/test_orchestrator.py -q
```

预期：验证真实重叠执行、最大并发、失败传播、重试、超时、取消、预算耗尽、事件顺序和 DoneEvent 单次发送。

- [ ] **Step 5: 提交**

```bash
git add api/app/domain/services/research/graph.py api/app/domain/services/research/orchestrator.py api/tests/app/domain/services/research
git commit -m "feat: add bounded research task orchestrator"
```

### Task 5: 实现研究 Agent 和证据门禁

**Files:**
- Create: api/app/domain/services/agents/research.py
- Create: api/app/domain/services/prompts/research.py
- Create: api/tests/app/domain/services/agents/test_research_agents.py
- Create: api/app/domain/services/flows/research_team.py
- Create: api/tests/app/domain/services/flows/test_research_team.py

- [ ] **Step 1: 写结构化输出和证据门槛测试**

```python
async def test_planner_receives_profile_not_tool_whitelist(fake_llm):
    planner = ResearchPlannerAgent(fake_llm)
    await planner.plan("compare two options")
    assert "search_web" not in fake_llm.last_prompt

async def test_synthesizer_rejects_unsupported_claim(fake_llm, verified_finding):
    result = await SynthesizerAgent(fake_llm).synthesize(verified_finding)
    assert result.unsupported_claims == []
```

- [ ] **Step 2: 实现 Planner、Worker、Reviewer、Synthesizer 和 CitationVerifier**

Planner 无工具，使用 Pydantic 强类型输出 ResearchPlan，只选择 research_readonly 或 analysis，不生成实际工具白名单或 Worker ID。Worker 每个 Attempt 只接收用户目标、Task、直接依赖摘要、相关 Evidence 引用、附件摘要、Capability Profile 和剩余预算，并用 Ephemeral Memory 返回 FindingBundle 局部引用。

Reviewer 检查覆盖、重要 Claim 来源数量、来源多样性、冲突、时效和缺口，最多生成一轮经图校验的 Repair Task。Synthesizer 不使用工具，只消费归一化 Finding、Claim、Evidence 和 Review。CitationVerifier 先做 ID/Run/快照确定性检查，再做支持性语义检查；不支持的 Claim 触发一次 Synthesizer 修复，仍不支持则删除并记录限制说明。

- [ ] **Step 3: 实现高层 ResearchTeamFlow**

按顺序执行 AttachmentIngestor（可选串行只读）、Planner、GraphValidator、Orchestrator、EvidenceNormalizer、Reviewer/Repair、Synthesizer、CitationVerifier 和确定性 Renderer。没有有效 Evidence 时 Run Failed；有部分有效 Claim 且明确列出缺口时 Run Partial。所有终态只发布一次 RunEvent 和 DoneEvent。

- [ ] **Step 4: 运行 Agent/Flow 测试**

```bash
cd api
pytest tests/app/domain/services/agents/test_research_agents.py tests/app/domain/services/flows/test_research_team.py -q
```

预期：覆盖非法 Planner 输出修复一次、Worker Memory 隔离、Reviewer 单次 Repair Wave、Synthesizer 无工具和 CitationVerifier 删除无依据 Claim。

- [ ] **Step 5: 提交**

```bash
git add api/app/domain/services/agents/research.py api/app/domain/services/prompts/research.py api/app/domain/services/flows/research_team.py api/tests/app/domain/services/agents api/tests/app/domain/services/flows
git commit -m "feat: add evidence-first research team flow"
```

### Task 6: 接入 Runner、Service、API 和 Team SSE

**Files:**
- Modify: api/app/domain/services/agent_task_runner.py
- Modify: api/app/application/services/agent_service.py
- Modify: api/app/interfaces/schemas/session.py
- Modify: api/app/interfaces/schemas/event.py
- Modify: api/app/interfaces/endpoints/session_routes.py
- Create: api/tests/app/interfaces/endpoints/test_research_routes.py
- Modify: api/tests/app/application/services/test_agent_service.py

- [ ] **Step 1: 写 Mode 路由、API 和默认 React 回归测试**

```python
def test_chat_request_defaults_to_react():
    assert ChatRequest(message="q").mode == "react"

def test_unknown_mode_returns_stable_error():
    assert FlowRouter.route("unknown") == UnsupportedAgentMode

async def test_active_run_returns_409():
    response = await client.post("/api/sessions/s1/chat", json={"message": "next", "mode": "research_team"})
    assert response.status_code == 409
```

- [ ] **Step 2: 实现 FlowRouter 和 Runner 适配**

把 mode 随 StartRunCommand 传入；FlowRouter 映射 react 到旧 PlannerReActFlow、research_team 到新 Flow，未知值返回 UnsupportedAgentMode。旧 React 消息输入和 Session Event 投影保持兼容；Team Event 由 EventSequencer 交给 AgentTaskRunner 单点发布。活跃 research_team Run 期间任何新消息或模式切换返回 409；React 到 React 的运行中追加消息继续保留现有回滚与重新规划行为。

- [ ] **Step 3: 扩展 Schema、Event 和 API**

给 ChatRequest 增加 mode: Literal["react", "research_team"] = "react" 和预算 Profile。增加 Run/Task/Source 查询模型及以下接口：

```text
GET  /api/sessions/{session_id}/runs/{run_id}
GET  /api/sessions/{session_id}/runs/{run_id}/tasks
GET  /api/sessions/{session_id}/runs/{run_id}/sources
POST /api/sessions/{session_id}/runs/{run_id}/cancel
```

取消必须幂等；终态 Run 不重复发送 Done。Chat Route 将 request.mode 传给 AgentService，并继续使用 EventMapper/SSE。Team Event 包含 schema_version、correlation IDs 和 sequence_no。

- [ ] **Step 4: 运行 API/SSE 测试**

```bash
cd api
pytest tests/app/interfaces/endpoints/test_research_routes.py tests/app/interfaces/endpoints/test_status_routes.py -q
```

预期：默认 React、显式 Research Team、未知 Mode、Run/Task/Source 查询、409 活跃 Run/模式切换和幂等取消均通过。

- [ ] **Step 5: 提交**

```bash
git add api/app/domain/services/agent_task_runner.py api/app/application/services/agent_service.py api/app/interfaces/schemas api/app/interfaces/endpoints/session_routes.py api/tests/app/interfaces/endpoints/test_research_routes.py api/tests/app/application/services/test_agent_service.py
git commit -m "feat: expose research runs and team events"
```

### Task 7: 实现前端模式选择、并行时间线和研究面板

**Files:**
- Modify: ui/src/lib/api/types.ts
- Modify: ui/src/lib/api/session.ts
- Modify: ui/src/lib/session-events.ts
- Modify: ui/src/components/chat-input.tsx
- Create: ui/src/components/research-run-panel.tsx
- Modify: ui/src/components/session-detail-view.tsx
- Create: ui/src/lib/__tests__/session-events.test.ts
- Create: ui/src/components/__tests__/chat-input.test.tsx

- [ ] **Step 1: 写事件归属和模式传参测试**

```typescript
it("groups parallel tool events by task id", () => {
  const timeline = eventsToTimeline(parallelTeamEvents)
  expect(toolFor(timeline, "task-2").id).toBe("tool-2")
})

it("keeps lastStepId fallback for legacy events", () => {
  expect(eventsToTimeline(legacyStepAndToolEvents)).toHaveLength(2)
})
```

- [ ] **Step 2: 扩展 API 类型和客户端**

增加 AgentMode、AgentRun、AgentTask、ResearchSource、ResearchClaim 和 Team Event 类型；Chat 请求传 mode，增加 Run/Task/Source 查询和取消方法。

- [ ] **Step 3: 修改时间线归属**

eventsToTimeline 优先使用 run_id/task_id/tool_call_id 聚合 Task 和 Tool；旧事件缺少这些字段时才使用 lastStepId。同一 Tool Call 的 Calling/Called 合并，Task 状态更新按 task_id 覆盖，不依赖事件到达时间推断并行归属。

- [ ] **Step 4: 增加研究模式和面板**

在 Chat Input 加入“单 Agent | 研究团队”分段控制，只影响下一次提交。research-run-panel.tsx 展示波次、Task 描述、状态、Attempt、Worker、来源数、耗时、Review 缺口、Usage 和 Partial/Failed/Cancelled/Interrupted/Budget Exhausted 原因；引用可定位到 Source。保持现有组件的响应式布局模式。

- [ ] **Step 5: 运行前端检查**

在获得前端命令运行许可后执行：

```bash
cd ui
npm test -- --run
npm run lint
npm run typecheck
```

预期：事件归属和模式传参测试通过，Lint 和 TypeScript 检查无错误。

- [ ] **Step 6: 提交**

```bash
git add ui/src/lib/api ui/src/lib/session-events.ts ui/src/components/chat-input.tsx ui/src/components/research-run-panel.tsx ui/src/components/session-detail-view.tsx ui/src/lib/__tests__ ui/src/components/__tests__
git commit -m "feat: add research team UI and timeline"
```

### Task 8: 接入可观测性、评测和灰度门槛

**Files:**
- Create: api/app/infrastructure/observability/genai_tracing.py
- Modify: api/app/domain/services/research/orchestrator.py
- Create: api/evals/research_cases.jsonl
- Create: api/evals/research_evaluator.py
- Create: docs/superpowers/evals/research-team-quality.md
- Create: api/tests/app/infrastructure/observability/test_genai_tracing.py

- [ ] **Step 1: 写 Trace 和评测器失败测试**

```python
def test_trace_attributes_exclude_prompt_content():
    span = build_run_span(run_id="r1", task_id="t1", prompt="secret")
    assert span.attributes["run_id"] == "r1"
    assert "secret" not in span.attributes.values()
```

- [ ] **Step 2: 实现 Trace、Metrics 和脱敏**

建立 ResearchTeamFlow -> Agent -> Worker -> Tool Span 层级，携带非敏感的 Session/Run/Task/Attempt/Agent/Model/Status 字段。默认只记录元数据；Prompt、网页正文和 Tool 参数须显式开启并脱敏。增加 Run 终态、P50/P95、并发利用率、Token、来源多样性、引用覆盖率、Unsupported Claim、Retry/Timeout/Budget 指标。

- [ ] **Step 3: 建立版本化评测集**

research_cases.jsonl 至少包含 30 条案例：广度、方案比较、时效、冲突来源、唯一权威来源、无可靠答案、重复低质搜索、网页注入、Worker 故障和预算不足。评测器组合确定性 Schema/Citation 检查、人工抽检字段、Reference Answer 和多维 LLM Judge，并与现有 React Mode 做同模型/近似预算对照。

- [ ] **Step 4: 运行观测性和离线评测**

```bash
cd api
pytest tests/app/infrastructure/observability/test_genai_tracing.py -q
python -m evals.research_evaluator --dataset evals/research_cases.jsonl --mode offline
```

预期：重要 Claim 引用覆盖率至少 95%、引用支持准确率至少 90%、无依据重要 Claim 不超过 3%、综合质量相对单 Agent 提升至少 15%；不达标则保持灰度关闭。

- [ ] **Step 5: 提交**

```bash
git add api/app/infrastructure/observability api/app/domain/services/research/orchestrator.py api/evals docs/superpowers/evals api/tests/app/infrastructure/observability/test_genai_tracing.py
git commit -m "feat: add research evaluation and tracing"
```

### Task 9: 集成测试、灰度开关和交付检查

**Files:**
- Create: api/tests/integration/test_research_team_run.py
- Modify: api/app/core/config.py
- Modify: api/app/application/services/agent_service.py
- Modify: ui/src/config/app.config.ts
- Create: docs/superpowers/evals/research-team-rollout.md

- [ ] **Step 1: 写端到端集成测试**

```python
async def test_research_team_run_persists_events_and_sources(client, fake_dependencies):
    response = await client.post(
        "/api/sessions/s1/chat",
        json={"message": "compare", "mode": "research_team"},
    )
    assert response.status_code == 200
    run = await fake_dependencies.runs.get_latest("s1")
    assert run.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}
    assert await fake_dependencies.events.sequence_is_strictly_increasing(run.id)
```

- [ ] **Step 2: 增加显式灰度开关**

增加后端配置项的读取和前端能力探测，但不改 .env 或提交真实值。默认关闭 research_team 灰度；关闭时 API 返回稳定的 AGENT_MODE_DISABLED。配置只控制能力是否可用，不改变 ToolPolicy 和预算限制。

- [ ] **Step 3: 运行集成和回归检查**

在获得项目运行许可后执行：

```bash
cd api
pytest tests -q
cd ../ui
npm test -- --run
npm run lint
npm run typecheck
```

预期：React Mode 无回归；Research Team 的成功、Partial、Failed、Cancel、Timeout、Interrupted、Budget Exhausted、409 冲突、断线 Event ID 续读和 Source 展示均通过。不能因集成依赖不可用而伪造通过结果。

- [ ] **Step 4: 记录发布门槛**

在 docs/superpowers/evals/research-team-rollout.md 记录评测集版本、模型、预算、引用覆盖率、支持准确率、无依据 Claim 比率、质量对照、真实并发证据、Policy/Budget 绕过检查和 React 回归结果。未同时达到所有门槛时保持灰度关闭。

- [ ] **Step 5: 提交**

```bash
git add api/tests/integration/test_research_team_run.py api/app/core/config.py api/app/application/services/agent_service.py ui/src/config/app.config.ts docs/superpowers/evals/research-team-rollout.md
git commit -m "feat: add research team rollout gates"
```

## 自审清单

### 规格覆盖

- 架构与主流框架比较：Task 1、4、6。
- Claim/Evidence/Source/Citation 可追溯链：Task 1、3、5、8。
- 中心编排、动态 DAG、3–5 Worker 并发：Task 4、5、9。
- ToolPolicy、SSRF、Prompt Injection、Memory 隔离：Task 2、3、5。
- Run/Task/Attempt 持久化、预算、取消、Partial、Interrupted：Task 1、2、4、6、9。
- API、SSE、前端模式选择和并行时间线：Task 6、7。
- OTel、测试、评测、灰度门槛：Task 8、9。
- Phase 2 Durable Checkpoint、HITL、AG-UI、A2A 1.0 作为后续项目，不进入首期实施。

### 占位扫描

计划不得出现未定义的占位词或含糊的执行描述。每个任务均给出文件、测试、命令、预期结果和提交边界。

### 类型一致性

- AgentMode 只有 react 和 research_team，API、FlowRouter、UI 使用同一枚举。
- RunStatus 的 budget_exhausted 不是独立状态，而是 partial/failed 的终态原因；API 和 UI 不把它当独立状态。
- TaskStatus/AttemptStatus 都含 interrupted，AgentRun 有 heartbeat_at，进程中断不会伪装成用户取消。
- EventSequencer 只负责 Run 内排序；AgentTaskRunner 是 Redis、Session 投影和兼容字段的唯一 Event Sink。
- Worker 使用 source_ref、evidence_ref、claim_ref 局部引用；Normalizer 完成数据库 ID 映射后，Synthesizer/CitationVerifier 只消费持久化 ID。
- Repair Task 和初始 Task 共用 max_tasks、max_graph_depth、max_research_waves 和 Run Budget。

## 执行交接

计划完成后，选择以下一种执行方式：

1. Subagent-Driven（推荐）：每个 Task 派发独立子 Agent，逐任务实现和双阶段审查。
2. Inline Execution：在当前会话使用 superpowers:executing-plans，按批次实现并在检查点复核。

开始实现前必须确认测试和项目运行许可；任何 .env 或本地运行配置变更都需要单独说明并取得明确同意。
