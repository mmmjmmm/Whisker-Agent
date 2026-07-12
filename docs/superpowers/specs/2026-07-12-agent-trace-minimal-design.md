# Agent Trace Minimal Design

## Goal

Build a minimal trace system for developer debugging while exposing basic
observability metrics: error rate, latency, model usage, and token usage.

This design only covers trace. It does not implement eval, replay,
checkpointing, external exporters, or a full APM dashboard.

## Scope

The first version adds a local trace core based on one database table:
`trace_spans`.

Each user message run creates one `trace_id`. The spans produced during that
run are linked by `parent_span_id` and can be rendered as a tree. Trace
summaries and metrics are derived from spans at query time.

## Non-Goals

- No `trace_runs` table in the first version.
- No external LangSmith, Langfuse, Phoenix, or OpenTelemetry exporter.
- No eval dataset, scorer, experiment, or replay feature.
- No checkpoint or sandbox state snapshot.
- No changes to `.env` or other local runtime configuration files.

## Data Model

Add a `trace_spans` table:

```text
id
trace_id
session_id
parent_span_id
span_type
name
status
started_at
ended_at
duration_ms
input
output
error
attributes
```

Field meanings:

- `id`: span id.
- `trace_id`: id shared by all spans in one user message run.
- `session_id`: owning session.
- `parent_span_id`: parent span id, nullable for the root span.
- `span_type`: one of `root`, `flow`, `agent`, `llm`, `tool`, `event`.
- `name`: short readable name, such as `chat`, `planner_react`,
  `team`, `react`, model name, or tool function name.
- `status`: one of `running`, `ok`, `error`.
- `started_at`, `ended_at`, `duration_ms`: timing data.
- `input`: bounded JSON debug input.
- `output`: bounded JSON debug output.
- `error`: bounded JSON error payload.
- `attributes`: bounded JSON metadata, such as model, token usage, tool name,
  event id, graph id, task id, or agent name.

Indexes:

- `(session_id, started_at)`
- `(session_id, trace_id)`
- `(trace_id, parent_span_id)`
- `(span_type)`
- `(status)`

Trace list and metrics are computed from this table. Root span duration is the
preferred trace duration. If a root span is missing, duration falls back to the
earliest `started_at` and latest `ended_at` in that trace.

## Trace Recorder

Add a small `TraceRecorder` abstraction. It owns trace context and hides
repository details from agent code.

Required operations:

```python
span = await recorder.start_span(...)
await recorder.end_span(span, output=..., error=..., attributes=...)
```

Trace recording must never affect agent execution. Recorder failures are logged
as warnings and swallowed. Repository unit tests should still expose repository
bugs directly.

## Instrumentation Points

Instrumentation is limited to existing execution boundaries.

### Root Span

Create a root span in `AgentTaskRunner.invoke()` for each consumed
`MessageEvent`.

- `span_type`: `root`
- `name`: `chat`
- `trace_id`: generated here
- `input`: user message preview, attachment count, agent mode
- `status`: `ok` on normal completion, `error` on error event or exception

### Flow Span

Create a flow span in `AgentTaskRunner._run_flow()`.

- `span_type`: `flow`
- `name`: `planner_react` or `team`
- parent: root span

### LLM Span

Create an LLM span in `BaseAgent._invoke_llm()`.

- `span_type`: `llm`
- `name`: `llm.model_name`
- parent: current flow or agent execution context
- attributes:
  - `agent_name`
  - `model`
  - `temperature`
  - `max_tokens`
  - `tool_count`
  - `response_format`
  - `prompt_tokens`
  - `completion_tokens`
  - `total_tokens`

If the OpenAI-compatible response does not include usage data, token fields stay
null. The system must not estimate token counts.

### Tool Span

Create a tool span in `BaseAgent._invoke_tool()`.

- `span_type`: `tool`
- `name`: called function name
- attributes:
  - `agent_name`
  - `tool_package`
  - `function_name`
  - `success`

Tool execution errors are captured on the span. Existing behavior remains
unchanged: after retries, failed tools still return `ToolResult(success=False)`
for the LLM to handle.

### Event Span

Create a thin event span in `AgentTaskRunner._put_and_add_event()`.

- `span_type`: `event`
- `name`: event type
- attributes:
  - `event_id`
  - `event_type`
  - `tool_call_id` when present
  - `graph_id`, `task_id`, `agent_id`, `attempt` when present

The event span does not store the full event payload. It links trace data to the
existing session event log without duplicating large data.

## Parent-Child Shape

The first version uses this shape:

```text
root(chat)
  flow(planner_react | team)
    llm(...)
    tool(...)
    event(...)
```

The first version does not require exact nesting under plan steps. Existing
event and tool attributes are enough to correlate task, graph, agent, and
attempt data where available.

## API

Add three read-only endpoints:

```text
GET /sessions/{session_id}/traces
GET /sessions/{session_id}/traces/{trace_id}
GET /sessions/{session_id}/trace-metrics
```

### Trace List

`GET /sessions/{session_id}/traces` returns one summary per trace:

```text
trace_id
started_at
ended_at
duration_ms
status
root_input_preview
span_count
error_count
llm_call_count
tool_call_count
models
prompt_tokens
completion_tokens
total_tokens
```

Trace status is `error` when any span in the trace has `status=error`.
Otherwise it is `ok`.

### Trace Detail

`GET /sessions/{session_id}/traces/{trace_id}` returns the span list for that
trace. The frontend builds the tree from `id` and `parent_span_id`.

Span `input`, `output`, `error`, and `attributes` are already bounded by the
recorder before storage.

### Session Metrics

`GET /sessions/{session_id}/trace-metrics` returns:

```text
trace_count
error_trace_count
error_rate
avg_duration_ms
p95_duration_ms
llm_call_count
tool_call_count
total_tokens
models
```

Error rate is trace-based: a trace counts as failed if it contains at least one
error span.

## UI

Add a minimal Trace panel to the session detail view.

The first version includes:

- Trace list with status, latency, error count, models, and token totals.
- Trace detail view with a span tree on the left and selected span details on
  the right.
- Error spans highlighted.
- LLM spans show model and token usage.
- Tool spans show function name and success status.

No charts are required in the first version.

## Truncation and Redaction

Trace input and output are for debugging, not full archival storage.

Rules:

- LLM input stores only current-call message summaries and message count, not
  the full persisted memory.
- LLM output stores assistant content summary, tool call summary, and usage.
- Tool input stores function args after redaction.
- Tool output stores a bounded `ToolResult` summary.
- JSON payload fields are limited to a fixed serialized size, initially 20 KB.
- Truncated payloads include `_truncated: true` and `_original_size`.
- Sensitive keys are redacted recursively when their lowercase key contains:
  `api_key`, `token`, `password`, `secret`, or `authorization`.

## Error Handling

Trace writes are best-effort.

- Recorder exceptions are logged as warnings and swallowed.
- Agent execution, event streaming, and session persistence must continue when
  trace recording fails.
- Span status is `error` when the instrumented operation raises or returns a
  failure result that represents the operation failure.
- Existing business error semantics are not changed.

## Testing

Minimum tests:

- Repository tests:
  - create span
  - end span
  - query by session
  - query by trace
  - aggregate trace list and session metrics
- Recorder tests:
  - normal span
  - exception span
  - redaction
  - truncation
  - recorder failure does not raise
- Agent integration tests with mocked LLM and tools:
  - root, flow, LLM, tool, and event spans are produced
  - original events are unchanged
  - failed tool records an error span without breaking existing `ToolResult`
    behavior
- API tests:
  - trace list response shape
  - trace detail response shape
  - metrics response shape

No test should call a real external LLM.

## Acceptance Criteria

- A completed chat run creates a trace with root, flow, LLM, event, and tool
  spans when tools are used.
- A failed run records at least one error span and appears as failed in trace
  list and metrics.
- Trace list exposes latency, error count, model list, and token totals.
- Session metrics expose trace count, error rate, average latency, p95 latency,
  LLM call count, tool call count, token total, and model list.
- Trace recording failure does not fail the chat run.
- Existing session events and SSE behavior remain compatible.
