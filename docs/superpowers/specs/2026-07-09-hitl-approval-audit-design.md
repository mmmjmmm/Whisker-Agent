# HITL 权限审批、危险操作拦截与审计日志设计

日期：2026-07-09

状态：已完成设计草案，等待用户复核

适用目标：团队内网 / 私有部署场景

## 背景

当前项目是一个通用 agent 系统，已经具备 Planner + ReAct 执行流、工具调用、SSE 事件、会话状态、沙箱、文件同步、浏览器、MCP 与 A2A 工具能力。现有架构中，LLM 生成 tool call 后，`BaseAgent.invoke()` 会发出工具 `calling` 事件，然后立即调用 `tool.invoke()` 执行工具。这个位置缺少统一的风险判定、人工审批和审计闭环。

沙箱仍然是必要的底层隔离，但沙箱不能完全替代 HITL。沙箱主要约束动作发生的位置和资源边界；HITL 约束动作是否符合用户意图、是否允许产生副作用、是否需要留下可追溯记录。尤其是 MCP、A2A、浏览器表单提交、文件同步、shell 命令等操作，可能影响外部系统或用户数据，即使执行环境被隔离，也仍然需要审批和审计。

## 外部方案参考

- OpenAI Agents SDK：工具可以声明需要人工审批；运行遇到敏感工具时暂停，返回 interruption 与可序列化运行状态，审批后恢复同一状态继续执行。参考：https://openai.github.io/openai-agents-python/human_in_the_loop/
- OpenAI Guardrails / Approvals：建议将审批放在有副作用的工具调用侧，例如 shell、文件编辑、MCP 操作；审批生命周期包括暂停、保存状态、批准/拒绝、恢复。参考：https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- LangChain / LangGraph：用 `interrupt_on`、谓词规则与 checkpointer 实现工具调用前中断和持久恢复。参考：https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- AutoGen：区分阻塞式人工输入和持久会话式人工输入；长时间等待更适合保存状态后在下一次运行恢复。参考：https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html
- Microsoft Agent Framework：函数工具可声明 approval requirement，agent run 返回 user input request，调用方负责展示审批请求并把决策传回同一会话。参考：https://learn.microsoft.com/en-us/agent-framework/agents/tools/tool-approval
- Vercel AI SDK：在 tool call 和 execute 之间插入 `needsApproval` gate，前端展示 approve / deny。参考：https://ai-sdk.dev/cookbook/next/human-in-the-loop
- MCP 官方安全建议：涉及用户数据、审计、用户同意、企业访问控制、速率限制时，应引入授权与安全控制。参考：https://modelcontextprotocol.io/docs/tutorials/security/authorization

## 设计目标

1. 所有工具调用在执行前经过统一策略判定。
2. 高风险工具调用在未批准前不得执行。
3. 审批请求、批准、拒绝、执行、失败都写入审计日志。
4. 审批请求可持久化，服务端重启或前端刷新后仍能看到 pending 状态。
5. 批准后执行同一个已保存的 tool call，而不是让模型重新生成一条相似调用。
6. 拒绝后把拒绝结果反馈给模型，让模型调整方案，避免无限重复同一危险动作。
7. 首期不引入完整企业 RBAC / 策略后台，保持实现可落地。

## 非目标

1. 不做多租户计费、外部合规报表、组织级权限后台。
2. 不把策略规则一开始做成复杂 DSL 或可视化配置台。
3. 不替代 Docker sandbox、浏览器隔离、MCP 授权等底层安全能力。
4. 不对所有工具强制人工审批，只拦截有副作用或明显高风险的操作。

## 当前代码落点

### 后端 Agent 执行流

- `api/app/domain/services/agents/base.py`
  - `_invoke_tool()` 目前直接调用 `tool.invoke(tool_name, **arguments)`。
  - `invoke()` 中解析 `tool_call_id / function_name / function_args` 后，先 yield `ToolEvent(CALLING)`，再调用 `_invoke_tool()`。
  - 推荐把策略判定放在解析参数之后、发出 `ToolEvent(CALLING)` 之前。这样需要审批时不会让前端误以为工具已经开始执行。

- `api/app/domain/services/agents/react.py`
  - 目前仅对 `message_ask_user` 做了等待逻辑。
  - 新增审批事件后，ReActAgent 应透传 `ApprovalRequestEvent` 和 `WaitEvent`，不要把审批和普通用户提问混在同一个工具里。

- `api/app/domain/services/agent_task_runner.py`
  - 已经支持 `WaitEvent` 将会话置为 `SessionStatus.WAITING` 并 return。
  - 该机制可复用，但审批自身需要独立事件与数据库记录。

- `api/app/application/services/agent_service.py`
  - SSE 会在收到 `DoneEvent / ErrorEvent / WaitEvent` 后结束本轮输出。
  - 新增 `ApprovalRequestEvent` 后，仍由 `WaitEvent` 负责结束流。

### 前端事件流

- `ui/src/hooks/use-session-detail.ts`
  - 当前已在收到 `wait` 事件时把会话置为 `waiting`。
  - 新增 approval 事件后，前端应渲染审批卡片，用户通过按钮提交批准或拒绝。

- `ui/src/lib/api/types.ts` 与 `ui/src/lib/session-events.ts`
  - 需要新增 approval 事件类型，并让 timeline 能展示审批请求与审批结果。

## 推荐方案

采用“持久化审批中断”方案：

```text
LLM tool call
  -> parse args
  -> ToolPolicyService.evaluate()
     -> allow: execute tool
     -> deny: block and write tool result
     -> require_approval: persist approval, emit approval event, emit wait event
  -> user approve/reject
  -> resume saved tool call
  -> write audit logs
  -> continue agent loop
```

这个方案最贴合当前项目：已有会话状态、事件流、数据库、工具抽象和等待机制；需要新增的能力集中在策略服务、审批服务、审计服务、事件模型和恢复入口。

## 新增领域模型

### 枚举

```text
ToolRiskLevel = low | medium | high | critical
ToolPolicyAction = allow | deny | require_approval
ApprovalStatus = pending | approved | rejected | expired | cancelled | executed | failed
ApprovalDecision = approve | reject
AuditActorType = user | agent | system | policy
AuditAction =
  tool_call_requested
  tool_call_allowed
  tool_call_denied
  approval_requested
  approval_approved
  approval_rejected
  approval_expired
  tool_executed
  tool_failed
```

### `tool_approvals` 表

字段建议：

- `id`
- `session_id`
- `task_id`
- `agent_name`
- `tool_call_id`
- `tool_name`
- `function_name`
- `args_redacted`
- `args_hash`
- `risk_level`
- `reason`
- `status`
- `requested_at`
- `decided_at`
- `decided_by`
- `decision`
- `decision_comment`
- `expires_at`
- `policy_snapshot`
- `executed_at`
- `result_summary`
- `created_at`
- `updated_at`

关键约束：

- `id` 为审批主键。
- `tool_call_id` 保留模型原始工具调用 ID，批准后必须用同一个 ID 写回工具结果。
- `args_hash` 用于判断审批时参数未被篡改。
- `policy_snapshot` 保存命中的策略版本，避免日后规则变化导致审计无法解释。

### `audit_logs` 表

字段建议：

- `id`
- `trace_id`
- `session_id`
- `task_id`
- `approval_id`
- `actor_type`
- `actor_id`
- `action`
- `tool_name`
- `function_name`
- `args_redacted`
- `risk_level`
- `status`
- `metadata`
- `created_at`

关键约束：

- 审计日志只追加，不在业务流中覆盖修改。
- 不存储完整 secret、token、cookie、私钥、完整文件内容。
- 参数原文需要脱敏后再写入。

## 新增服务

### `ToolPolicyService`

职责：

- 接收工具调用上下文。
- 根据工具名、函数名、参数、会话上下文判断风险。
- 返回 `allow / deny / require_approval`、风险等级、原因、允许的用户决策。

接口形态：

```text
evaluate(context: ToolCallContext) -> ToolPolicyDecision
```

`ToolCallContext` 包含：

- `session_id`
- `agent_name`
- `tool_call_id`
- `tool_name`
- `function_name`
- `function_args`
- `message_context_summary`

`ToolPolicyDecision` 包含：

- `action`
- `risk_level`
- `reason`
- `matched_rules`
- `allowed_decisions`
- `redacted_args`
- `args_hash`

### `ToolApprovalService`

职责：

- 创建 pending 审批。
- 查询会话 pending 审批。
- 处理 approve / reject。
- 校验状态幂等性。
- 提供恢复所需的原始 tool call 信息。

接口形态：

```text
create_pending(context, decision) -> ToolApproval
decide(approval_id, user_id, decision, comment) -> ToolApproval
get_pending(session_id) -> list[ToolApproval]
mark_executed(approval_id, result_summary)
mark_failed(approval_id, error_summary)
```

### `AuditLogService`

职责：

- 对 tool call、策略判定、审批、执行结果写入审计日志。
- 提供按 session 查询审计记录的接口。

接口形态：

```text
record(action, context, metadata)
list_by_session(session_id, filters)
```

### `SensitiveDataRedactor`

职责：

- 对命令、URL、header、cookie、token、文件路径、文件内容摘要进行脱敏。
- 生成稳定 hash，支持审计对比。

脱敏规则：

- `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` 等字段隐藏值。
- 命令中形如 `sk-...`、`AKIA...`、私钥块、JWT 的片段替换为 `[REDACTED]`。
- 文件内容不完整入库，只保存前后摘要、长度、hash。

## 新增事件

### `ApprovalRequestEvent`

领域事件：

```text
type = "approval"
approval_id
tool_call_id
tool_name
function_name
args_redacted
risk_level
reason
allowed_decisions
expires_at
status = "pending"
```

SSE 事件：

```text
event = "approval"
data = ApprovalEventData
```

说明：

- `ApprovalRequestEvent` 用来展示审批信息。
- `WaitEvent` 继续表示本轮 agent 流结束并等待人类输入。
- 不复用 `message_ask_user`，因为审批是安全边界，不是普通澄清问题。

## API 设计

### 查询 pending 审批

```http
GET /api/sessions/{session_id}/approvals/pending
```

返回当前会话所有 pending 审批。前端刷新后可重新渲染审批卡片。

### 提交审批决策

```http
POST /api/sessions/{session_id}/approvals/{approval_id}/decide
```

请求体：

```json
{
  "decision": "approve",
  "comment": "允许执行该命令"
}
```

或：

```json
{
  "decision": "reject",
  "comment": "不要删除该目录"
}
```

行为：

- 校验审批属于该 session。
- 校验审批仍为 pending。
- 写入审批结果与审计日志。
- 向任务输入流写入内部 `ApprovalDecisionEvent` 或触发恢复服务。
- 返回更新后的审批对象。

### 查询审计日志

```http
GET /api/sessions/{session_id}/audit-logs
```

支持按 action、risk_level、tool_name、created_at 范围过滤。

## 执行流程

### Allow 流程

1. LLM 生成 tool call。
2. `BaseAgent.invoke()` 解析参数。
3. 写 `tool_call_requested` 审计。
4. `ToolPolicyService.evaluate()` 返回 `allow`。
5. 写 `tool_call_allowed` 审计。
6. yield `ToolEvent(CALLING)`。
7. 调用 `_invoke_tool()`。
8. yield `ToolEvent(CALLED)`。
9. 写 `tool_executed` 或 `tool_failed` 审计。
10. 工具结果写回 memory，继续 LLM 汇总。

### Deny 流程

1. LLM 生成 tool call。
2. 策略返回 `deny`。
3. 不 yield `ToolEvent(CALLING)`，不执行工具。
4. 写 `tool_call_denied` 审计。
5. 生成 `ToolResult(success=False, message=...)`。
6. 将该 tool result 写回 memory，提示模型该动作被策略拦截。
7. 继续 LLM，让模型选择安全替代方案。

### Require Approval 流程

1. LLM 生成 tool call。
2. 策略返回 `require_approval`。
3. 不执行工具。
4. 创建 `tool_approvals` pending 记录。
5. 写 `approval_requested` 审计。
6. yield `ApprovalRequestEvent`。
7. yield `WaitEvent`。
8. `AgentTaskRunner` 将 session 置为 `WAITING` 并结束本轮流。

### Approve Resume 流程

1. 用户点击批准。
2. 后端将审批状态改为 `approved`。
3. 写 `approval_approved` 审计。
4. 读取审批记录保存的 `tool_call_id / function_name / args`。
5. 通过恢复入口执行原工具。
6. 以同一个 `tool_call_id` 写回 tool message。
7. 写 `tool_executed` 审计。
8. 继续 agent loop，让模型基于工具结果总结。

### Reject Resume 流程

1. 用户点击拒绝。
2. 后端将审批状态改为 `rejected`。
3. 写 `approval_rejected` 审计。
4. 生成工具失败结果：

```json
{
  "success": false,
  "message": "用户拒绝该操作。不要重复尝试同一工具调用，除非用户重新明确授权。"
}
```

5. 将拒绝结果作为 tool message 写回 memory。
6. 继续 agent loop，让模型解释原因或给替代方案。

## 恢复能力设计

首期采用“审批记录 + 会话 memory + task input stream”的恢复方式：

- 审批创建时，保存完整 tool call 的安全副本。
- 用户审批后，后端通过内部事件触发同一 session 的任务继续。
- 如果当前进程仍持有 task，则直接恢复。
- 如果进程已重启，仍能展示 pending 审批和审计记录；恢复能力依赖现有 task registry 的持久化程度。

需要明确的限制：

- 当前项目任务 registry 偏进程内，首期不能承诺跨进程精确恢复整个 async generator 栈。
- 设计上保留 `resume_approval()` 边界，后续可将 task runner 改造成 durable runner。
- 即使跨进程恢复暂时不完整，也必须保证未批准的工具不会执行，审批与审计记录不会丢失。

## 策略规则

### 默认 allow

- 搜索工具。
- 只读文件，例如 read / list。
- 普通浏览器打开页面、读取页面状态。
- MCP / A2A 中明显只读的 get / list / read / search。
- message notify / ask user。

### 默认 require approval

Shell：

- `rm`, `rmdir`, `mv`, `chmod`, `chown`, `sudo`
- `docker`, `ssh`, `scp`, `nc`
- `curl | sh`, `wget | bash`
- `pip install`, `npm install`, `pnpm add`, `brew install`, `apt install`
- `git push`, `git reset`, `git clean`
- `kill -9`
- 长时间后台任务或端口监听命令

File：

- 写入、覆盖、删除、移动文件。
- 修改 `.env`、配置文件、lockfile、密钥文件。
- 对项目外路径写入。
- 大文件生成或批量删除。

Browser：

- 提交表单。
- 点击包含 delete / remove / submit / send / pay / checkout / publish 等语义的按钮。
- 登录、支付、发消息、发邮件、发布内容。
- 执行浏览器控制台脚本。

MCP / A2A：

- create / update / delete / send / post / deploy / publish / invite / grant / revoke。
- 操作外部系统的写接口。
- 未知 MCP server 暴露的非只读工具。

### 默认 deny

- 访问 Docker socket。
- 访问云 metadata 地址，例如 `169.254.169.254`。
- 明显外传 secret、token、cookie、私钥。
- fork bomb、格式化磁盘、破坏系统文件。
- 访问宿主机敏感路径。

## 前端设计

新增审批卡片：

- 展示工具名、函数名、风险等级、原因、参数摘要。
- 提供批准、拒绝按钮。
- 可选填写备注。
- pending 状态下按钮可用。
- approved / rejected / expired 状态下只读展示。

交互规则：

- 收到 `approval` 事件时插入时间线。
- 收到后续 `wait` 事件时 session 进入 waiting。
- 用户提交决策后调用审批接口。
- 审批提交成功后，重新打开会话 SSE，等待恢复后的工具事件和模型消息。

## 错误处理

1. 审批已被处理：返回当前审批状态，前端展示只读结果。
2. 审批过期：拒绝执行工具，写 `approval_expired` 审计。
3. 工具执行失败：写 `tool_failed` 审计，将失败结果回填给模型。
4. 审批记录丢失：不执行工具，返回错误事件并写审计。
5. 参数 hash 不匹配：拒绝执行工具，写高风险审计。
6. 前端断线：审批记录已持久化，刷新后通过 pending 接口恢复展示。

## 测试计划

### 单元测试

- `ToolPolicyService` 对 shell / file / browser / MCP / A2A 的 allow、require approval、deny 分类。
- `SensitiveDataRedactor` 对 token、cookie、JWT、私钥、URL query 的脱敏。
- `ToolApprovalService` 的 create、approve、reject、expire、幂等处理。
- `AuditLogService` 的追加记录与查询过滤。

### 集成测试

- allow 工具调用会正常执行。
- deny 工具调用不会执行，并将失败结果写回模型上下文。
- require approval 工具调用不会执行，产生 approval event 和 wait event。
- approve 后执行同一个 tool call。
- reject 后不执行工具，并给模型拒绝结果。
- 前端刷新后仍能查询 pending 审批。

### 回归测试

- `message_ask_user` 的现有等待流程不受影响。
- 普通只读工具不被错误打断。
- SSE 在 `wait` 后仍正常结束本轮流。
- 会话状态在 running / waiting / completed 间保持一致。

## 分阶段落地

### 第一阶段：拦截与可见审批

- 新增枚举、事件、数据库表、repository。
- 新增 `ToolPolicyService`、`ToolApprovalService`、`AuditLogService`、`SensitiveDataRedactor`。
- 在 `BaseAgent.invoke()` 工具执行前接入策略。
- 新增审批 SSE schema。
- 新增 pending 查询与 decide API。
- 前端展示审批卡片。

验收：危险工具未批准前不会执行；用户能看到审批卡片；审批和工具请求有审计记录。

### 第二阶段：恢复执行

- 新增内部 `ApprovalDecisionEvent` 或等价恢复入口。
- 在 flow / agent 层实现 `resume_approval()`。
- approve 后执行保存的 tool call。
- reject 后写拒绝 tool result。
- 恢复后继续 LLM 汇总。

验收：批准后继续同一轮任务；拒绝后模型给出替代方案；不会重复执行未批准工具。

### 第三阶段：策略完善与运维能力

- 扩充规则覆盖。
- 审批过期机制。
- 审计查询页面。
- MCP server / function 粒度策略。
- 可选增加 workspace / user / role 级别的策略覆盖。

验收：团队内可追踪谁在何时批准了什么；高风险行为有默认保护；常规读操作不被干扰。

## 验收标准

1. 高风险 shell / file / browser / MCP / A2A 调用必须先产生审批，未批准前不执行。
2. 批准后执行审批记录中保存的同一 tool call。
3. 拒绝后工具不执行，模型收到拒绝结果。
4. 审计日志覆盖请求、策略、审批、执行、失败。
5. 审计参数经过脱敏，不泄露 secret。
6. 前端能在刷新后恢复 pending 审批卡片。
7. `message_ask_user` 保持原行为。
8. 普通只读工具不需要审批。

## 主要风险与缓解

风险：恢复同一个 async generator 栈实现复杂。

缓解：首期用审批记录保存 tool call，并显式实现 `resume_approval()`；跨进程 durable runner 放在后续演进中。

风险：规则过严会降低 agent 可用性。

缓解：默认只拦截有副作用和高风险动作；只读工具默认放行；审计可帮助后续调参。

风险：审计日志泄露敏感信息。

缓解：所有参数入库前脱敏，文件内容只存摘要和 hash。

风险：模型在拒绝后重复请求同一危险动作。

缓解：拒绝结果以 tool message 写回 memory，并明确要求模型不要重复尝试同一动作，除非用户重新授权。

## 自检结论

- 范围聚焦在团队内网 / 私有部署，不包含完整企业权限平台。
- 架构边界清晰：策略、审批、审计、脱敏分别独立。
- 执行路径覆盖 allow、deny、require approval、approve resume、reject resume。
- 已明确当前 task runner 持久恢复限制，并给出渐进落地路径。
