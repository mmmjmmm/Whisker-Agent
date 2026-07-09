# TeamFlow Multi-Agent Design

## Goal

Add a first-version multi-agent execution mode to mooc-manus using a central coordinator, a DAG task graph, and a pool of homogeneous local worker agents. The existing `PlannerReActFlow` remains the default single-executor flow; the new mode is opt-in through chat request mode.

## Scope

This design covers the first production-oriented version of TeamFlow:

- Local homogeneous worker pool.
- DAG planning with explicit dependencies.
- Controlled parallel execution for safe task profiles.
- Per-task review and retry.
- Final synthesis.
- SSE events that expose task and agent identity.

A2A remains available as a tool in this version. Remote A2A agents are not yet first-class schedulable agents.

## Non-Goals

- Do not replace `PlannerReActFlow`.
- Do not auto-select between single-agent and team mode.
- Do not create dynamic expert agents such as fixed `BrowserAgent` or `CodingAgent`.
- Do not make A2A remote agents part of scheduling in v1.
- Do not parallelize write, shell, browser mutation, MCP, or A2A tasks by default.
- Do not build a full DAG visualization in the first UI pass.
- Do not add long-term memory, code RAG, or LSP features as part of this work.

## Existing Architecture Context

The current agent execution path is:

```text
Session route
  -> AgentService
  -> AgentTaskRunner
  -> PlannerReActFlow
  -> PlannerAgent
  -> ReActAgent
  -> Tool events / message events
```

Important current properties:

- `PlannerReActFlow` is linear: it asks `Plan.get_next_step()` for the next unfinished step.
- `BaseAgent._invoke_llm()` currently keeps only the first model tool call with `tool_calls[:1]`.
- Frontend timeline grouping uses the latest active step to attach tool events. That is not sufficient for parallel tasks.
- Session events are persisted as JSONB events and streamed over SSE.
- `AgentTaskRunner` owns sandbox setup, MCP/A2A initialization, file syncing, tool event enrichment, and session status updates.

The new TeamFlow should reuse these responsibilities instead of duplicating sandbox or storage logic.

## Recommended Architecture

Add a new flow beside `PlannerReActFlow`:

```text
ChatRequest(mode="team")
  -> AgentTaskRunner
  -> TeamFlow
  -> TeamPlannerAgent creates TaskGraph
  -> TeamOrchestrator schedules ready task nodes
  -> WorkerAgent pool executes task nodes
  -> ReviewerAgent validates each task result
  -> SynthesizerAgent creates final response
```

`TeamOrchestrator` is code, not an LLM agent. It owns scheduling, dependency resolution, concurrency, locks, retries, timeout handling, and state transitions. LLM agents own reasoning tasks only: planning, executing, reviewing, and synthesizing.

## New Files

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

Existing files to modify:

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

## Request Mode

Extend `ChatRequest` with an explicit mode:

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

The first version does not use `auto`. Users or the UI must choose team mode explicitly.

## Configuration

Extend `AgentConfig`:

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

`team_enabled` protects the feature during rollout. If `mode="team"` is requested while disabled, the backend returns an `ErrorEvent` explaining that team mode is disabled.

## TaskGraph Model

Add `api/app/domain/models/task_graph.py`:

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

Validation requirements:

- Every dependency must refer to an existing task ID.
- The graph must be acyclic.
- `allowed_tools` must be a subset of the tools allowed by the task profile.
- At least one task must exist.
- Task IDs should be stable, short, and unique within the graph. The planner may output IDs such as `task_1`; backend validation enforces uniqueness.

## Tool Policy

Add `tool_policy.py` to centralize profile-to-tool rules and concurrency classification.

Default profile policy:

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

MCP and A2A tools are not enabled by default for parallel tasks. If a planner emits MCP/A2A usage, TeamFlow treats that task as non-parallel unless a later version adds explicit tool metadata for side-effect safety.

Tool enforcement happens twice:

1. Schema exposure: `WorkerAgent` only exposes allowed tool schemas to the model.
2. Runtime enforcement: `_get_tool()` or `WorkerAgent` rejects a tool call whose function name is outside `allowed_tools`.

This prevents the model from calling unauthorized tools even if it fabricates a tool call.

## Homogeneous Worker Pool

Workers are homogeneous:

```text
worker-1
worker-2
worker-3
```

They share the same `WorkerAgent` implementation and prompt. A worker is temporarily specialized by task context:

```text
Agent ID: worker-2
Execution profile: research
Allowed tools: search_web, browser_navigate, browser_view
Current task: Compare pricing claims on the target website.
Dependency results: ...
```

Workers do not own permanent roles such as browser worker or file worker. This keeps scheduling simple and avoids idle fixed-role workers.

## Agent Responsibilities

### TeamPlannerAgent

- Inherits `BaseAgent`.
- Uses `tool_choice="none"`.
- Outputs a strict `TaskGraph` JSON object.
- Does not execute tools.
- Must include dependencies and profiles for each task.
- Must keep tasks coarse enough to be useful but small enough to execute independently.

### WorkerAgent

- Inherits `BaseAgent`.
- Executes one `TaskNode`.
- Receives:
  - original user goal,
  - current task,
  - task profile,
  - allowed tools,
  - dependency task results,
  - attachments and relevant sandbox file paths.
- Returns structured JSON:

```json
{
  "success": true,
  "result": "Task result text",
  "artifacts": [],
  "notes": []
}
```

### ReviewerAgent

- Reviews one task result.
- First version may run without tools, or only with read-only tools when the task produced files or browser output.
- Outputs strict JSON:

```json
{
  "approved": true,
  "issues": [],
  "suggestions": [],
  "confidence": 0.82
}
```

If reviewer JSON parsing fails, TeamFlow treats the review as not approved and allows one retry when `max_task_retries > 0`.

### SynthesizerAgent

- Produces the final user-facing response from:
  - original goal,
  - task graph,
  - completed task results,
  - failed/skipped task summaries,
  - attachments/artifacts.
- Does not continue task execution.

## TeamFlow Responsibilities

`TeamFlow` is the flow implementation invoked by `AgentTaskRunner`.

Responsibilities:

- Check `agent_config.team_enabled`.
- Run `TeamPlannerAgent`.
- Validate `TaskGraph`.
- Emit `TaskGraphEvent`.
- Instantiate `TeamOrchestrator`.
- Stream all task, tool, review, retry, message, error, and done events.
- Run final synthesis when every task is terminal.

`TeamFlow` should not contain low-level scheduling details. That belongs in `TeamOrchestrator`.

## TeamOrchestrator Scheduling

The orchestrator loops until all tasks are terminal:

```text
1. Find ready tasks:
   status == pending and every dependency is completed.
2. Mark tasks whose dependencies failed or skipped as skipped.
3. Split ready tasks by safety:
   safe parallel profiles: research, file_read, analysis.
   serialized profiles: browser, file_write, shell, MCP/A2A.
4. Execute a safe batch with bounded concurrency:
   min(max_workers, max_parallel_tasks, number of ready safe tasks).
5. Execute serialized tasks one by one behind resource locks.
6. Review each task result.
7. Retry rejected tasks up to max_task_retries.
8. Emit status events for every transition.
```

Concurrency primitives:

```python
worker_semaphore = asyncio.Semaphore(agent_config.max_workers)
parallel_task_semaphore = asyncio.Semaphore(agent_config.max_parallel_tasks)
browser_lock = asyncio.Lock()
file_write_lock = asyncio.Lock()
shell_lock = asyncio.Lock()
external_tool_lock = asyncio.Lock()
```

The first version uses global locks instead of path-level locks. Path-level file locks can be added later.

## Event Model

Add domain events:

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

Extend `ToolEvent` with optional metadata:

```python
task_id: Optional[str] = None
agent_id: Optional[str] = None
agent_profile: Optional[str] = None
```

All events emitted from a worker task must include task and agent identity. This is required because parallel task execution makes "attach to latest step" incorrect.

## SSE Schema and Frontend

Backend schema additions:

- Add SSE data classes for `task_graph`, `task`, `task_review`, and `task_retry`.
- Extend `ToolEventData` with `task_id`, `agent_id`, and `agent_profile`.

Frontend additions:

- Extend `SSEEventType` with:
  - `task_graph`
  - `task`
  - `task_review`
  - `task_retry`
- Add types for `TaskGraph`, `TaskNode`, `TaskReview`.
- Add timeline item kinds:
  - `task`
  - `task_review`
  - `task_retry`

Frontend grouping rule:

```text
If tool.task_id exists:
  attach the tool event to the matching task item.
Else:
  fall back to the existing lastStepId behavior for legacy PlannerReActFlow events.
```

The first UI pass should show task rows in topological order with:

- task description,
- status,
- assigned worker ID,
- profile,
- retry count,
- nested tools,
- review result.

The existing plan panel can render a task graph as a topologically sorted list. Full node-edge DAG visualization is deferred.

## AgentTaskRunner Integration

`AgentTaskRunner` should construct both flow dependencies but instantiate the active flow based on request mode. Since the current runner is created before an input message is popped, there are two viable implementation paths:

1. Store the mode on input `MessageEvent` or `Message` and lazily create the flow in `_run_flow()`.
2. Keep a default `PlannerReActFlow`, and create `TeamFlow` only when a team-mode message arrives.

Recommended path: lazy flow selection in `_run_flow(message, mode)`.

```python
if mode == AgentMode.TEAM:
    flow = TeamFlow(...)
else:
    flow = self._planner_react_flow
```

`AgentTaskRunner` keeps ownership of:

- sandbox setup,
- MCP/A2A initialization,
- tool event enrichment,
- file sync to sandbox and storage,
- persisted session events,
- session title/latest message/status updates.

## Shared Context

Do not share the full conversation history across workers. Use a structured shared context:

```text
original_user_goal
attachments
sandbox_upload_paths
task_results_by_id
important_tool_outputs
artifacts
failed_or_skipped_tasks
```

Each worker receives only:

- original goal,
- current task,
- direct dependency results,
- relevant attachments,
- allowed tools/profile.

This keeps token usage bounded and reduces cross-task contamination.

## Failure Handling

Planner failures:

- JSON parse failure: emit `ErrorEvent`, stop.
- Graph validation failure: emit `ErrorEvent`, stop.
- Empty graph: emit `ErrorEvent`, stop.

Task failures:

- Worker error: mark task `failed`.
- Reviewer rejection: mark task `retrying`, retry with feedback until `max_task_retries`.
- Retry exhaustion: mark task `failed`, keep last result in `result` when available.
- Dependency failed/skipped: mark downstream task `skipped`.

Cancellation:

- Existing session stop should cancel running tasks.
- Running worker tasks should emit terminal task events where possible.
- Flow ends with `DoneEvent`.

Timeout:

- Each task execution is wrapped with `asyncio.wait_for(..., team_task_timeout_seconds)`.
- Timeout marks the task `failed` with an explicit timeout error.

Reviewer parse failure:

- Treat as rejection.
- Feedback: `Reviewer output could not be parsed as review JSON. Re-evaluate and produce a valid task result.`
- Retry if retries remain.

## Security and Safety

The first version adds tool allowlists but does not fully solve high-risk operations. For safety:

- Only read-only and analysis profiles run in parallel.
- `file_write`, `shell`, `browser`, MCP, and A2A tasks are serialized.
- Runtime tool authorization checks are mandatory.
- Existing `message_ask_user` behavior remains available inside workers.
- A future HITL approval system should be added before allowing broad parallel write/shell/browser operations.

## Rollout

Phase 1:

- Add models, events, schemas, prompts.
- Add TeamPlannerAgent, WorkerAgent, ReviewerAgent, SynthesizerAgent.
- Implement TeamFlow and TeamOrchestrator.
- Keep UI minimal with topological task list.
- Team mode disabled by default.

Phase 2:

- Enable team mode through settings and chat request mode.
- Add frontend affordance to choose team mode.
- Improve timeline grouping by task ID.

Phase 3:

- Add RemoteA2AAgent scheduling through AgentRegistry.
- Add richer DAG visualization.
- Add more granular locks and tool safety metadata.

## Testing Strategy

Backend unit tests:

- TaskGraph validation rejects missing dependencies.
- TaskGraph validation rejects cycles.
- Tool policy maps profiles to allowed tools.
- WorkerAgent exposes only allowed tools.
- Runtime tool call outside allowed tools is rejected.
- Orchestrator identifies ready tasks correctly.
- Orchestrator runs safe ready tasks concurrently with bounded max workers.
- Orchestrator serializes browser/file_write/shell profiles.
- Reviewer rejection causes retry.
- Retry exhaustion marks task failed.
- Failed dependency marks downstream task skipped.
- ToolEvent metadata includes task and agent identity.
- Event mapper converts new events to SSE events.

Backend integration tests:

- Team mode request produces task graph, task events, review events, message event, and done event.
- React mode still uses existing PlannerReActFlow.
- Team mode disabled returns an ErrorEvent and does not execute workers.

Frontend tests:

- New SSE event types normalize correctly.
- Tool events with `task_id` attach to the matching task.
- Legacy tool events still use previous fallback grouping.
- Task timeline renders status, worker ID, profile, and retry count.

## Acceptance Criteria

- Existing react mode behavior remains unchanged.
- A team-mode request can create a task graph and execute at least two independent read-only/research tasks in parallel.
- Serialized profiles do not run concurrently.
- Every worker-emitted tool event includes `task_id` and `agent_id`.
- Reviewer rejection triggers a retry up to configured limit.
- Final response summarizes completed, failed, and skipped tasks.
- Session detail reload shows persisted team events correctly.
- The feature can be disabled through `team_enabled`.

