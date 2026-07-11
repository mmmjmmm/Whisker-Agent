import { describe, expect, it } from "vitest";

import type { SSEEventData } from "@/lib/api/types";
import { eventsToTimeline } from "@/lib/session-events";

describe("eventsToTimeline", () => {
  it("keeps lastStepId fallback for old react events", () => {
    const events = [
      {
        type: "step",
        data: { id: "step-1", status: "running", description: "Search" },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "tool-1",
          name: "search",
          function: "search_web",
          args: {},
          status: "called",
        },
      },
    ] as SSEEventData[];

    const timeline = eventsToTimeline(events);
    const step = timeline.find((item) => item.kind === "step");

    expect(step?.kind).toBe("step");
    if (step?.kind === "step") {
      expect(step.tools).toHaveLength(1);
    }
  });

  it("does not attach explicitly scoped team tools to a react step", () => {
    const events = [
      {
        type: "step",
        data: { id: "step-1", status: "running", description: "Search" },
      },
      {
        type: "tool",
        data: {
          run_id: "run-1",
          task_id: "task-1",
          tool_call_id: "tool-1",
          name: "search",
          function: "search_web",
          args: {},
          status: "called",
        },
      },
    ] as SSEEventData[];

    const timeline = eventsToTimeline(events);
    const step = timeline.find((item) => item.kind === "step");

    if (step?.kind === "step") {
      expect(step.tools).toHaveLength(0);
    }
  });
});
