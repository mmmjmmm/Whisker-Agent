# Execution Span Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat trace rows with a correct root/flow/task/agent/llm/tool execution hierarchy for React and Team modes.

**Architecture:** Deepen `TraceRecorder` so callers use one status-aware span interface for normal completion, failure, waiting, cancellation, and explicit parenting. Domain orchestration owns Task spans, Agent public operations own Agent spans, and existing LLM/Tool calls remain leaf spans. The trace reader derives final status from the root; the frontend renders the resulting hierarchy as a collapsible tree.

**Tech Stack:** Python 3.12, asyncio/contextvars, Pydantic v2, pytest, React 19, TypeScript, lucide-react, Node contract tests.

---

### Task 1: Trace domain and recorder semantics

**Files:**
- Modify: `api/app/domain/models/trace.py`
- Modify: `api/app/domain/services/tracing/recorder.py`
- Test: `api/tests/app/domain/models/test_trace.py`
- Test: `api/tests/app/domain/services/tracing/test_recorder.py`

- [ ] Add failing tests proving `task`, `waiting`, and `cancelled` deserialize and that `end_span(status=...)` persists explicit non-error outcomes.
- [ ] Add a failing concurrent-parent test in which two child asyncio tasks inherit the same flow and create independent Task/Agent branches.
- [ ] Run the focused tests and verify failures are caused by the missing enum values/interface.
- [ ] Add `TASK`, `WAITING`, and `CANCELLED`; extend `end_span` with an explicit status and reject `RUNNING` as a terminal status.
- [ ] Keep the ContextVar implementation private to the recorder and preserve explicit `parent_span_id` support.
- [ ] Run the focused tests until green.

### Task 2: Root summary semantics

**Files:**
- Modify: `api/app/application/services/trace_service.py`
- Test: `api/tests/app/application/services/test_trace_service.py`

- [ ] Add failing tests showing a successful root with failed retry children remains `ok`, and waiting/cancelled roots are preserved without increasing error rate.
- [ ] Run the tests and verify the current any-child-error aggregation fails them.
- [ ] Derive summary status from root; retain any-child-error fallback only for legacy traces without root.
- [ ] Run trace service tests until green.

### Task 3: Agent operation spans

**Files:**
- Modify: `api/app/domain/services/agents/base.py`
- Modify: `api/app/domain/services/agents/planner.py`
- Modify: `api/app/domain/services/agents/react.py`
- Modify: `api/app/domain/services/agents/team_planner.py`
- Modify: `api/app/domain/services/agents/task_worker.py`
- Modify: `api/app/domain/services/agents/team_synthesizer.py`
- Test: `api/tests/app/domain/services/agents/test_agent_tracing.py`

- [ ] Add focused fake-recorder tests proving public Agent operations create stable Agent names, capture structured output, and mark parse/validation errors on the Agent span.
- [ ] Add tests proving every LLM/Tool retry records attempt/max_attempts and tool_call_id.
- [ ] Add a waiting test for `react.execute_step` and a generator-close cancellation test.
- [ ] Run focused tests and verify they fail because Agent spans do not exist.
- [ ] Add a small protected BaseAgent tracing interface used by each public operation; do not create an extra span around generic `BaseAgent.invoke`.
- [ ] Pass operation attempt metadata from Team retry call sites and keep LLM/Tool leaves under the active Agent span.
- [ ] Run focused tests until green.

### Task 4: React and Team Task spans

**Files:**
- Modify: `api/app/domain/services/flows/planner_react.py`
- Modify: `api/app/domain/services/flows/team.py`
- Modify: `api/app/domain/services/team/orchestrator.py`
- Modify: `api/app/domain/services/agent_task_runner.py`
- Test: `api/tests/app/domain/services/flows/test_planner_react_tracing.py`
- Test: `api/tests/app/domain/services/team/test_orchestrator_tracing.py`
- Test: `api/tests/app/domain/services/test_agent_task_runner_tracing.py`

- [ ] Add failing React tests for `flow -> task(plan.step) -> agent(react.execute_step)` and waiting propagation.
- [ ] Add failing Team tests for one logical Task span across failed/successful Worker attempts and for parallel sibling parents.
- [ ] Add failing Runner tests for root/flow cancellation on asyncio cancellation and superseding input.
- [ ] Run focused tests and verify expected hierarchy/status failures.
- [ ] Inject TraceRecorder into TeamOrchestrator, create one Task scope around its attempt loop, and explicitly finalize task output/status.
- [ ] Create React Task scopes around each actual step execution and propagate WaitEvent.
- [ ] Propagate waiting/cancelled/root output through Runner and remove `_put_and_add_event` Event span creation.
- [ ] Keep `TraceSpanType.EVENT` readable for historical data.
- [ ] Run focused tests until green.

### Task 5: Collapsible Trace tree

**Files:**
- Modify: `ui/src/lib/api/types.ts`
- Modify: `ui/src/components/trace-panel.tsx`
- Modify: `ui/tests/runner-style-contract.test.mjs`

- [ ] Add failing contract assertions for `task`, `waiting`, `cancelled`, lucide chevrons, collapsed state, and Task context labels.
- [ ] Run `node --test tests/runner-style-contract.test.mjs` and verify the new assertions fail.
- [ ] Update frontend trace unions without removing historical `event`.
- [ ] Add stable expand/collapse state: root/flow expanded, task/agent collapsed, and status-specific icons/styles.
- [ ] Render Task ID/description from attributes while retaining the stable span name in detail.
- [ ] Run the contract test until green.

### Task 6: Verification

**Files:**
- No production changes unless verification exposes a root-cause defect.

- [ ] Run focused trace tests: `cd api && uv run pytest tests/app/domain/models/test_trace.py tests/app/domain/services/tracing/test_recorder.py tests/app/application/services/test_trace_service.py tests/app/domain/services/test_agent_task_runner_tracing.py -q`.
- [ ] Run all backend tests: `cd api && uv run pytest -q`.
- [ ] Run frontend tests: `cd ui && node --test tests/*.test.mjs`.
- [ ] Run frontend lint: `cd ui && npm run lint`.
- [ ] Run frontend production build: `cd ui && npm run build`.
- [ ] Inspect `git diff --check`, `git status --short`, and the final diff; do not include unrelated `docs/agent-skills-implementation.md` or `docs/traces/` changes.
