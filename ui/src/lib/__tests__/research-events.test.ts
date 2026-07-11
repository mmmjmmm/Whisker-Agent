import { describe, expect, it } from "vitest";

import type { ResearchTask, SSEEventData } from "@/lib/api/types";
import { reduceResearchEvents } from "@/lib/research-events";

function task(id: string): ResearchTask {
  return {
    id,
    run_id: "run-1",
    plan_version: 1,
    task_key: id,
    description: `Research ${id}`,
    objective: `Research ${id}`,
    capability_profile: "research_readonly",
    dependency_ids: [],
    acceptance_criteria: ["evidence"],
    source_requirements: {},
    required: true,
    priority: 0,
    status: "running",
    assigned_agent_id: `${id}-worker`,
    result_summary: null,
    error: null,
    attempt_count: 1,
  };
}

function event(type: SSEEventData["type"], data: unknown): SSEEventData {
  return { type, data } as SSEEventData;
}

describe("reduceResearchEvents", () => {
  it("groups interleaved tools by explicit task id", () => {
    const state = reduceResearchEvents([
      event("research_task", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 1,
        status: "running",
        task: task("a"),
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 2,
        status: "running",
        task: task("b"),
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 3,
        tool_call_id: "tool-a",
        status: "calling",
        name: "search",
        function: "search_web",
        args: {},
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 4,
        tool_call_id: "tool-b",
        status: "calling",
        name: "search",
        function: "search_web",
        args: {},
      }),
      event("tool", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 5,
        tool_call_id: "tool-a",
        status: "called",
        name: "search",
        function: "search_web",
        args: {},
      }),
    ]);

    expect(state.tasks.a.tools.map((tool) => tool.tool_call_id)).toEqual([
      "tool-a",
    ]);
    expect(state.tasks.b.tools.map((tool) => tool.tool_call_id)).toEqual([
      "tool-b",
    ]);
    expect(state.tasks.a.tools[0].status).toBe("called");
  });

  it("orders task discovery by sequence number", () => {
    const state = reduceResearchEvents([
      event("research_task", {
        run_id: "run-1",
        task_id: "b",
        sequence_no: 9,
        status: "running",
        task: task("b"),
      }),
      event("research_task", {
        run_id: "run-1",
        task_id: "a",
        sequence_no: 3,
        status: "running",
        task: task("a"),
      }),
    ]);

    expect(state.taskOrder).toEqual(["a", "b"]);
  });
});
