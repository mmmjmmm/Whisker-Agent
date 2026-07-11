import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ResearchRunPanel } from "@/components/research-run-panel";
import type { ResearchRunView } from "@/lib/research-events";

function createPartialRunView(): ResearchRunView {
  return {
    run: {
      id: "run-1",
      session_id: "session-1",
      mode: "research_team",
      status: "partial",
      goal: "对比主流 Agent 架构",
      usage: {
        llm_calls: 8,
        tool_calls: 12,
        input_tokens: 1200,
        output_tokens: 800,
        total_tokens: 2000,
        worker_attempts: 2,
        elapsed_ms: 4500,
      },
      error: {
        type: "budget_exhausted",
        message: "LLM 调用预算已耗尽",
      },
    },
    taskOrder: ["task-1"],
    tasks: {
      "task-1": {
        id: "task-1",
        run_id: "run-1",
        plan_version: 1,
        task_key: "market",
        description: "调研主流多 Agent 方案",
        objective: "整理架构取舍",
        capability_profile: "research_readonly",
        dependency_ids: [],
        acceptance_criteria: ["至少两个一手来源"],
        source_requirements: {},
        required: true,
        priority: 1,
        status: "completed",
        assigned_agent_id: "worker-1",
        result_summary: "已完成框架对比",
        error: null,
        attempt_count: 1,
        tools: [
          {
            tool_call_id: "tool-1",
            name: "web_search",
            function: "search",
            args: { query: "multi-agent architecture" },
            status: "called",
            run_id: "run-1",
            task_id: "task-1",
          },
        ],
      },
    },
    sources: {
      "source-1": {
        id: "source-1",
        run_id: "run-1",
        canonical_url: "https://example.com/research",
        original_url: "https://example.com/research?ref=agent",
        title: "Multi-agent research",
        domain: "example.com",
        publisher: "Example Lab",
        published_at: "2026-07-01T00:00:00Z",
        retrieved_at: "2026-07-10T00:00:00Z",
        content_type: "text/html",
        content_hash: "hash",
        source_class: "primary",
        metadata: { supported_claim_count: 3 },
      },
    },
    review: {
      approved: false,
      issues: ["定价时间线缺少一手材料"],
      conflicts: [],
      missing_questions: ["证据不足：定价历史"],
      repair_tasks: [],
    },
    usage: null,
  };
}

describe("ResearchRunPanel", () => {
  it("展示部分完成、未完工作和预算原因，不伪装成已完成", () => {
    render(<ResearchRunPanel view={createPartialRunView()} />);

    expect(screen.getByText("部分完成")).toBeVisible();
    expect(screen.getByText("证据不足：定价历史")).toBeVisible();
    expect(screen.getByText("LLM 调用预算已耗尽")).toBeVisible();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("将工具嵌套在所属任务下，并保留可安全检查的来源链接", async () => {
    const user = userEvent.setup();
    const onToolClick = vi.fn();
    render(
      <ResearchRunPanel
        view={createPartialRunView()}
        onToolClick={onToolClick}
      />,
    );

    await user.click(screen.getByRole("button", { name: /调研主流多 Agent 方案/ }));
    await user.click(screen.getByRole("button", { name: /search/ }));
    expect(onToolClick).toHaveBeenCalledWith(
      expect.objectContaining({ task_id: "task-1", tool_call_id: "tool-1" }),
    );

    await user.click(screen.getByRole("tab", { name: "来源" }));
    const sourceLink = screen.getByRole("link", { name: "Multi-agent research" });
    expect(sourceLink).toHaveAttribute("href", "https://example.com/research");
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
