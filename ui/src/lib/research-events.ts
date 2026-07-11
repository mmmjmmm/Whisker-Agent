import type {
  AgentRun,
  ResearchReview,
  ResearchSource,
  ResearchTask,
  ResearchUsage,
  RunUsage,
  SSEEventData,
  ToolEvent,
} from "@/lib/api/types";

export type ResearchTaskView = ResearchTask & { tools: ToolEvent[] };

export type ResearchRunView = {
  run: AgentRun | null;
  taskOrder: string[];
  tasks: Record<string, ResearchTaskView>;
  sources: Record<string, ResearchSource>;
  review: ResearchReview | null;
  usage: ResearchUsage | null;
};

export function emptyResearchRunView(): ResearchRunView {
  return {
    run: null,
    taskOrder: [],
    tasks: {},
    sources: {},
    review: null,
    usage: null,
  };
}

function eventSequence(event: SSEEventData): number | null {
  const sequence = (event.data as { sequence_no?: unknown }).sequence_no;
  return typeof sequence === "number" ? sequence : null;
}

function emptyUsage(): RunUsage {
  return {
    llm_calls: 0,
    tool_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    worker_attempts: 0,
    elapsed_ms: 0,
  };
}

export function reduceResearchEvents(events: SSEEventData[]): ResearchRunView {
  const view = emptyResearchRunView();
  const ordered = events
    .map((event, arrival) => ({ event, arrival }))
    .sort((left, right) => {
      const leftSequence = eventSequence(left.event);
      const rightSequence = eventSequence(right.event);
      if (leftSequence !== null && rightSequence !== null) {
        return leftSequence - rightSequence || left.arrival - right.arrival;
      }
      return left.arrival - right.arrival;
    });

  for (const { event } of ordered) {
    if (event.type === "run") {
      const data = event.data;
      const runId = data.run_id ?? view.run?.id;
      const sessionId = data.session_id ?? view.run?.session_id;
      if (runId && sessionId) {
        view.run = {
          id: runId,
          session_id: sessionId,
          mode: "research_team",
          status: data.status,
          goal: data.goal,
          usage: { ...emptyUsage(), ...view.run?.usage, ...data.usage },
          error: data.error ?? null,
        };
      }
      continue;
    }

    if (event.type === "research_task") {
      const task = { ...event.data.task, status: event.data.status };
      const existing = view.tasks[task.id];
      if (!existing) view.taskOrder.push(task.id);
      view.tasks[task.id] = {
        ...task,
        tools: existing?.tools ?? [],
      };
      continue;
    }

    if (event.type === "tool") {
      const tool = event.data as ToolEvent;
      const taskId = tool.task_id;
      if (!taskId || !view.tasks[taskId]) continue;
      const tools = [...view.tasks[taskId].tools];
      const existingIndex = tool.tool_call_id
        ? tools.findIndex((item) => item.tool_call_id === tool.tool_call_id)
        : -1;
      if (existingIndex >= 0) tools[existingIndex] = tool;
      else tools.push(tool);
      view.tasks[taskId] = { ...view.tasks[taskId], tools };
      continue;
    }

    if (event.type === "research_source") {
      view.sources[event.data.source.id] = event.data.source;
      continue;
    }

    if (event.type === "research_review") {
      view.review = event.data.review;
      continue;
    }

    if (event.type === "research_usage") {
      view.usage = event.data;
      continue;
    }

    if (event.type === "error" && event.data.task_id) {
      const task = view.tasks[event.data.task_id];
      if (task) {
        view.tasks[event.data.task_id] = {
          ...task,
          status: "failed",
          error: { type: "TaskError", message: event.data.error },
        };
      }
    }
  }

  return view;
}
