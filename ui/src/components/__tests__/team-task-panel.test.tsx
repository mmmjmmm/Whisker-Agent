import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { TeamTaskPanel } from "@/components/team-task-panel";
import type { ToolEvent } from "@/lib/api/types";
import type { TeamProjection } from "@/lib/session-events";


it("renders DAG task details and forwards nested tool clicks", () => {
  const tool: ToolEvent = {
    tool_call_id: "call-1",
    name: "search",
    function: "search_web",
    args: { query: "multi agent DAG" },
    status: "called",
    task_id: "collect",
  };
  const projection: TeamProjection = {
    graph: {
      id: "graph-1",
      title: "多 Agent 调研",
      goal: "形成结论",
      status: "running",
      tasks: [
        {
          id: "collect",
          description: "收集资料",
          dependencies: [],
          capability: "search",
          success_criteria: "至少一个来源",
          status: "running",
          assigned_agent_id: "worker-1",
          attempt_count: 1,
          result: {
            success: true,
            summary: "已找到资料",
            sources: [
              { title: "官方资料", url: "https://example.com/source" },
            ],
            artifacts: ["/sandbox/report.md"],
            notes: [],
          },
        },
        {
          id: "summarize",
          description: "整理结论",
          dependencies: ["collect"],
          capability: "analysis",
          success_criteria: "形成结论",
          status: "failed",
          assigned_agent_id: "worker-2",
          attempt_count: 2,
          error: "boom",
        },
      ],
    },
    toolsByTask: { collect: [tool] },
  };
  const onToolClick = vi.fn();

  render(
    <TeamTaskPanel projection={projection} onToolClick={onToolClick} />,
  );

  expect(screen.getByText("收集资料")).toBeInTheDocument();
  expect(screen.getByText("依赖：collect")).toBeInTheDocument();
  expect(screen.getByText("Worker：worker-2")).toBeInTheDocument();
  expect(screen.getByText("尝试：2")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("boom");
  expect(screen.getByRole("link", { name: "官方资料" })).toHaveAttribute(
    "href",
    "https://example.com/source",
  );
  expect(screen.getByText("/sandbox/report.md")).toBeInTheDocument();

  fireEvent.click(
    screen.getByRole("button", { name: /正在搜索 multi agent DAG/ }),
  );
  expect(onToolClick).toHaveBeenCalledWith(tool);
});
