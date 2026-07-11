# Research Team 发布评测

该目录提供固定 30 案例的 React/Research Team 对照评测和硬发布门槛。评测脚本不启动服务、不修改配置，也不会自动开启 `research_team_enabled`。

## 1. 采集同模型对照数据

只在用户明确授权且 API 已由部署者启动时运行。`AUTHORIZED_BASE_URL` 是临时传入的授权地址，不得写入仓库配置。

```bash
uv run python evals/run_research_eval.py collect \
  --base-url "$AUTHORIZED_BASE_URL" \
  --mode react \
  --model-profile default \
  --dataset evals/research_cases.jsonl \
  --output tmp/react-artifacts.jsonl
```

```bash
uv run python evals/run_research_eval.py collect \
  --base-url "$AUTHORIZED_BASE_URL" \
  --mode research_team \
  --model-profile default \
  --dataset evals/research_cases.jsonl \
  --output tmp/research-team-artifacts.jsonl
```

HTTP collector 只收集对外 API/SSE 可见数据，不读取 Source 正文、Prompt、工具参数或凭据。在评分前，评测执行器或审核人必须补齐 artifact 中的结构化 `claims`、`covered_topics` 和 `judge_scores`。缺失时指标保持低分，门槛不会误通过。

## 2. 离线评分

```bash
uv run python evals/run_research_eval.py score \
  --dataset evals/research_cases.jsonl \
  --baseline tmp/react-artifacts.jsonl \
  --candidate tmp/research-team-artifacts.jsonl \
  --output tmp/research-eval-report.json
```

脚本组合确定性 Citation/Source/Topic 指标与同模型 Judge 分数，并输出每类至少 20% 的人工抽检清单。任一硬门槛失败时退出码为 1，报告会列出失败字段。

## 3. 人工抽检与上线签字

按报告的 `manual_review_case_ids` 逐项核对事实、引用原文、冲突处理和失败披露。上线前还必须确认：

- 所有硬门槛通过，且人工抽检已签字。
- 部署为单 API 进程，与当前启动恢复语义一致。
- 生产凭据已轮换，评测 artifact 不含 secret。
- OTel exporter 未启用 Prompt、Message、Source 正文、Tool arguments/result 内容采集。
- `research_team_enabled` 在证据齐全前继续保持 `false`。
