# 执行层 Trace 层级设计

## 目标

把当前 `root -> flow -> llm/tool/event` 的扁平 Trace 升级为按实际执行实体组织的树，使 Planner、Agent、逻辑任务、重试和终止原因可以直接定位。

## Span 层级

React 模式：

```text
root: chat
└─ flow: planner_react
   ├─ agent: planner.create_plan
   │  └─ llm / tool
   ├─ task: plan.step
   │  └─ agent: react.execute_step
   │     └─ llm / tool
   ├─ agent: planner.update_plan
   │  └─ llm / tool
   └─ agent: react.summarize
      └─ llm / tool
```

Team 模式：

```text
root: chat
└─ flow: team
   ├─ agent: team_planner.create_graph
   │  └─ llm / tool
   ├─ task: team.task
   │  ├─ agent: task_worker.execute (attempt=1)
   │  │  └─ llm / tool
   │  └─ agent: task_worker.execute (attempt=2)
   │     └─ llm / tool
   └─ agent: team_synthesizer.synthesize
      └─ llm / tool
```

`TASK` 表示一个逻辑任务。Team Task 从第一次进入 `RUNNING` 开始，跨越全部重试，在最终 `COMPLETED`、`FAILED` 或 `CANCELLED` 时结束。React Plan Step 每次实际执行对应一个 Task span。依赖等待不计入 duration，从未执行的 `SKIPPED` task 不创建 span。

## 类型、名称与数据

- 新增 `TraceSpanType.TASK`。
- Planner、Worker、Synthesizer 和 ReAct 均使用 `TraceSpanType.AGENT`，不新增角色专用类型。
- Span name 使用稳定操作名：`planner.create_plan`、`plan.step`、`react.execute_step`、`planner.update_plan`、`react.summarize`、`team_planner.create_graph`、`team.task`、`task_worker.execute`、`team_synthesizer.synthesize`。
- 动态的 `graph_id`、`task_id`、`step_id`、描述、角色、operation、capability、attempt 和 max_attempts 放入 attributes。
- Task/Agent input 和 output 保存受限的结构化领域数据，继续经过现有敏感字段脱敏和 20KB 截断；不复制完整 prompt、memory 或上下文消息。
- 每次 Planner、Synthesizer、Worker、LLM 和 Tool 重试都创建独立 span，并记录 attempt/max_attempts。失败尝试保持 `error`，不会被后续成功覆盖。

## 结束状态

- 保留 `running / ok / error`，新增 `waiting / cancelled`。
- React 通过 `message_ask_user` 暂停时，本轮 active task/agent/flow/root 结束为 `waiting`；用户回复后产生新 trace，并通过相同 step_id 关联。
- asyncio 取消或新消息替换旧执行时，active task/agent/flow/root 结束为 `cancelled`。后一种情况记录 `cancellation_reason=superseded_by_new_input`。
- Team DAG 为 `partial` 但成功生成最终汇总时，root 为 `ok`，同时记录 `graph_status=partial`；失败子 span 和 error_count 保留。

## 汇总语义

- TraceSummary.status 以 root status 为准。
- 仅最终 root 为 `error` 的 trace 计入 error_rate。
- waiting/cancelled 不计入 error_rate。
- error_count 仍统计全部失败子 span，因此能看到被成功重试恢复的错误。
- 对没有 root 的历史数据保留旧的聚合回退。

## Event 兼容

新 trace 不再为 session event 创建 span。业务事件仍由原 session event 仓库持久化。为保证历史 trace 可读取，后端枚举和前端联合类型继续保留 `event`，不删除或回填现有记录。

## Trace 面板

- Trace 树支持节点展开/收起。
- root/flow 默认展开，task/agent 默认折叠。
- Task 节点显示动态 task/step ID 和截断后的描述，稳定操作名仍保存在 `name`。
- error/waiting/cancelled 使用清晰的状态图标和样式。
- 不增加筛选、搜索、瀑布图或外部 Trace exporter。

## 验收

- React 和 Team 的 LLM/Tool span 均挂在正确 Agent 下，Worker Agent 挂在正确 Task 下。
- Team 并发任务互为 flow 的兄弟节点，不串错 parent_span_id。
- 重试、等待、取消和 partial 的状态符合上述定义。
- 新 trace 不产生 event span，历史 event span 仍可反序列化和展示。
- 后端定向测试、完整 pytest、前端契约测试、lint/build 全部通过。
