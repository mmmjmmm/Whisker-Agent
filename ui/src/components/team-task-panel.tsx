"use client";

import { ToolUse } from "@/components/tool-use";
import { Badge } from "@/components/ui/badge";
import type {
  TaskGraphStatus,
  TeamTaskStatus,
  ToolEvent,
} from "@/lib/api/types";
import type { TeamProjection } from "@/lib/session-events";

const TASK_STATUS: Record<
  TeamTaskStatus,
  {
    label: string;
    variant: "default" | "secondary" | "destructive" | "outline";
  }
> = {
  pending: { label: "等待中", variant: "outline" },
  running: { label: "运行中", variant: "default" },
  retrying: { label: "重试中", variant: "secondary" },
  completed: { label: "已完成", variant: "secondary" },
  failed: { label: "失败", variant: "destructive" },
  skipped: { label: "已跳过", variant: "outline" },
  cancelled: { label: "已取消", variant: "outline" },
};

const GRAPH_STATUS: Record<TaskGraphStatus, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

export function TeamTaskPanel({
  projection,
  onToolClick,
}: {
  projection: TeamProjection;
  onToolClick?: (tool: ToolEvent) => void;
}) {
  const { graph, toolsByTask } = projection;

  return (
    <section
      aria-label="多 Agent 任务图"
      aria-live="polite"
      className="mb-2 rounded-xl border bg-white p-4"
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-900">{graph.title}</h2>
          <p className="mt-1 text-sm leading-5 text-gray-600">{graph.goal}</p>
        </div>
        <Badge variant="outline">{GRAPH_STATUS[graph.status]}</Badge>
      </header>

      <ol className="max-h-[min(42vh,360px)] space-y-3 overflow-y-auto pr-1">
        {graph.tasks.map((task) => {
          const status = TASK_STATUS[task.status];
          const tools = toolsByTask[task.id] ?? [];
          return (
            <li key={task.id} className="rounded-lg bg-gray-50 p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-gray-500">
                    {task.id}
                  </div>
                  <div className="mt-0.5 text-sm font-medium leading-5 text-gray-800">
                    {task.description}
                  </div>
                </div>
                <Badge variant={status.variant}>{status.label}</Badge>
              </div>

              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-600">
                <span>能力：{task.capability}</span>
                <span>
                  {task.dependencies.length > 0
                    ? `依赖：${task.dependencies.join(", ")}`
                    : "无依赖"}
                </span>
                {task.assigned_agent_id && (
                  <span>Worker：{task.assigned_agent_id}</span>
                )}
                <span>尝试：{task.attempt_count}</span>
              </div>

              {task.error && (
                <p role="alert" className="mt-2 text-sm text-red-700">
                  错误：{task.error}
                </p>
              )}

              {tools.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {tools.map((tool, index) => (
                    <ToolUse
                      key={tool.tool_call_id ?? `${task.id}-${index}`}
                      data={tool}
                      onClick={
                        onToolClick ? () => onToolClick(tool) : undefined
                      }
                    />
                  ))}
                </div>
              )}

              {task.result?.summary && (
                <p className="mt-3 text-sm leading-5 text-gray-700">
                  {task.result.summary}
                </p>
              )}

              {(task.result?.sources.length ?? 0) > 0 && (
                <div className="mt-3 flex flex-wrap gap-2 text-sm">
                  {task.result?.sources.map((source) => (
                    <a
                      key={source.url}
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="break-all text-blue-700 underline underline-offset-2"
                    >
                      {source.title}
                    </a>
                  ))}
                </div>
              )}

              {(task.result?.artifacts.length ?? 0) > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-gray-700">
                  {task.result?.artifacts.map((artifact) => (
                    <li key={artifact} className="break-all">
                      <code>{artifact}</code>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
