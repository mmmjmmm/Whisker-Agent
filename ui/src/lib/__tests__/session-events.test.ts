import { describe, expect, it } from "vitest";

import {
  eventsToTimeline,
  getLatestTeamProjection,
} from "@/lib/session-events";
import type { SSEEventData, TaskGraph } from "@/lib/api/types";

const graphFixture: TaskGraph = {
  id: "g",
  title: "research",
  goal: "collect",
  status: "pending",
  tasks: [
    {
      id: "a",
      description: "collect",
      dependencies: [],
      capability: "search",
      success_criteria: "done",
      status: "pending",
      attempt_count: 0,
    },
  ],
};

describe("getLatestTeamProjection", () => {
  it("updates tasks and merges calling/called tools by task_id", () => {
    const events = [
      { type: "task_graph", data: { graph: graphFixture } },
      {
        type: "task",
        data: {
          graph_id: "g",
          task: { ...graphFixture.tasks[0], status: "running" },
          attempt: 1,
        },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "c",
          name: "search",
          function: "search_web",
          args: {},
          status: "calling",
          graph_id: "g",
          task_id: "a",
        },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "c",
          name: "search",
          function: "search_web",
          args: {},
          status: "called",
          graph_id: "g",
          task_id: "a",
        },
      },
    ] as SSEEventData[];

    const projection = getLatestTeamProjection(events);

    expect(projection?.graph.tasks[0].status).toBe("running");
    expect(projection?.toolsByTask.a).toHaveLength(1);
    expect(projection?.toolsByTask.a[0].status).toBe("called");
    expect(eventsToTimeline(events)).toHaveLength(0);
  });

  it("resets projection when a newer graph starts", () => {
    const newer = { ...graphFixture, id: "g2", title: "new" };
    const events = [
      { type: "task_graph", data: { graph: graphFixture } },
      {
        type: "tool",
        data: {
          tool_call_id: "old",
          name: "search",
          function: "search_web",
          args: {},
          task_id: "a",
          graph_id: "g",
        },
      },
      { type: "task_graph", data: { graph: newer } },
    ] as SSEEventData[];

    const projection = getLatestTeamProjection(events);

    expect(projection?.graph.id).toBe("g2");
    expect(projection?.toolsByTask).toEqual({});
  });

  it("keeps legacy tools without task_id in the old timeline", () => {
    const events = [
      {
        type: "tool",
        data: {
          tool_call_id: "legacy",
          name: "search",
          function: "search_web",
          args: { query: "legacy" },
          status: "called",
        },
      },
    ] as SSEEventData[];

    expect(eventsToTimeline(events)).toHaveLength(1);
    expect(getLatestTeamProjection(events)).toBeNull();
  });

  it("clears an old team graph when a new user turn starts", () => {
    const events = [
      { type: "task_graph", data: { graph: graphFixture } },
      {
        type: "message",
        data: {
          role: "user",
          message: "switch back",
          agent_mode: "react",
        },
      },
    ] as SSEEventData[];

    expect(getLatestTeamProjection(events)).toBeNull();
  });
});
