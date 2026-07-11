import { describe, expect, it } from "vitest";

import type {
  ResearchTask,
  SSEEventData,
} from "@/lib/api/types";
import {
  mergeResearchSnapshot,
  reduceResearchEvents,
} from "@/lib/research-events";

function task(id: string, status: ResearchTask["status"]): ResearchTask {
  return {
    id,
    run_id: "run-1",
    plan_version: id === "repair" ? 2 : 1,
    task_key: id,
    description: id,
    objective: id,
    capability_profile: "research_readonly",
    dependency_ids: [],
    acceptance_criteria: ["evidence"],
    source_requirements: {},
    required: true,
    priority: 1,
    status,
    assigned_agent_id: `worker-${id}`,
    result_summary: status === "completed" ? `done:${id}` : null,
    error: status === "timed_out" ? { type: "TaskTimeout" } : null,
    attempt_count: status === "timed_out" ? 2 : 1,
  };
}

function event(type: SSEEventData["type"], data: unknown): SSEEventData {
  return { type, data } as SSEEventData;
}

describe("research session fixture", () => {
  it("rebuilds an interleaved partial run and preserves task tools on refresh", () => {
    const source = {
      id: "source-1",
      run_id: "run-1",
      canonical_url: "https://example.com/source",
      original_url: "https://example.com/source",
      title: "Source",
      domain: "example.com",
      publisher: null,
      published_at: null,
      retrieved_at: "2026-07-10T00:00:00Z",
      content_type: "text/html",
      content_hash: "hash",
      source_class: "official",
      metadata: {},
    };
    const events: SSEEventData[] = [
      event("run", {
        session_id: "session-1",
        run_id: "run-1",
        sequence_no: 1,
        status: "running",
        goal: "compare agents",
        usage: {},
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 2,
        status: "running",
        task: task("a", "running"),
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 3,
        status: "running",
        task: task("b", "running"),
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 4,
        tool_call_id: "tool-a",
        name: "search",
        function: "search_web",
        args: {},
        status: "calling",
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 5,
        tool_call_id: "tool-b",
        name: "web",
        function: "web_read",
        args: {},
        status: "calling",
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 6,
        tool_call_id: "tool-a",
        name: "search",
        function: "search_web",
        args: {},
        status: "called",
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 7,
        status: "completed",
        task: task("a", "completed"),
      }),
      event("error", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 8,
        scope: "task",
        error: "timeout",
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 9,
        status: "timed_out",
        task: task("b", "timed_out"),
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "repair",
        sequence_no: 10,
        status: "completed",
        task: task("repair", "completed"),
      }),
      event("research_source", {
        run_id: "run-1",
        sequence_no: 11,
        source,
      }),
      event("research_review", {
        run_id: "run-1",
        sequence_no: 12,
        review: {
          approved: false,
          issues: ["worker b timed out"],
          conflicts: [],
          missing_questions: ["pricing history"],
          repair_tasks: [],
        },
      }),
      event("run", {
        session_id: "session-1",
        run_id: "run-1",
        sequence_no: 13,
        status: "partial",
        goal: "compare agents",
        usage: { worker_attempts: 4 },
      }),
      event("done", { run_id: "run-1", sequence_no: 14 }),
    ];

    const projected = reduceResearchEvents(events);
    expect(projected.run?.status).toBe("partial");
    expect(projected.tasks.a.tools[0].status).toBe("called");
    expect(projected.tasks.b.tools[0].task_id).toBe("b");
    expect(projected.tasks.b.status).toBe("timed_out");
    expect(projected.tasks.repair.plan_version).toBe(2);
    expect(projected.review?.missing_questions).toEqual(["pricing history"]);

    const persistedTasks = projected.taskOrder.map((taskId) => {
      const { tools: _tools, ...persisted } = projected.tasks[taskId];
      return persisted;
    });
    const refreshed = mergeResearchSnapshot(
      projected,
      projected.run!,
      persistedTasks,
      [source],
    );

    expect(refreshed.run?.status).toBe("partial");
    expect(refreshed.taskOrder).toEqual(["a", "b", "repair"]);
    expect(refreshed.tasks.a.tools[0].tool_call_id).toBe("tool-a");
    expect(refreshed.tasks.b.tools[0].tool_call_id).toBe("tool-b");
    expect(Object.keys(refreshed.sources)).toEqual(["source-1"]);
  });
});
