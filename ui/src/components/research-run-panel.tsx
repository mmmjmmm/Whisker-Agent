"use client";

import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDashed,
  Clock3,
  Loader2,
  Search,
  Users,
  XCircle,
} from "lucide-react";

import { ResearchSourceList } from "@/components/research-source-list";
import type {
  ResearchTaskStatus,
  RunStatus,
  ToolEvent,
} from "@/lib/api/types";
import type { ResearchRunView, ResearchTaskView } from "@/lib/research-events";
import { cn } from "@/lib/utils";

type ResearchRunPanelProps = {
  view: ResearchRunView;
  onToolClick?: (tool: ToolEvent) => void;
  className?: string;
};

type StatusPresentation = {
  label: string;
  className: string;
  icon: typeof Circle;
  spinning?: boolean;
};

const RUN_STATUS: Record<RunStatus, StatusPresentation> = {
  pending: {
    label: "待启动",
    className: "text-gray-600",
    icon: CircleDashed,
  },
  planning: {
    label: "规划中",
    className: "text-blue-700",
    icon: Loader2,
    spinning: true,
  },
  running: {
    label: "研究中",
    className: "text-blue-700",
    icon: Loader2,
    spinning: true,
  },
  reviewing: {
    label: "审核中",
    className: "text-blue-700",
    icon: Search,
  },
  synthesizing: {
    label: "整理报告",
    className: "text-blue-700",
    icon: Loader2,
    spinning: true,
  },
  completed: {
    label: "已完成",
    className: "text-emerald-700",
    icon: CheckCircle2,
  },
  partial: {
    label: "部分完成",
    className: "text-amber-700",
    icon: AlertCircle,
  },
  failed: {
    label: "失败",
    className: "text-red-700",
    icon: XCircle,
  },
  cancelled: {
    label: "已取消",
    className: "text-gray-600",
    icon: XCircle,
  },
  interrupted: {
    label: "已中断",
    className: "text-amber-700",
    icon: AlertCircle,
  },
};

const TASK_STATUS: Record<ResearchTaskStatus, StatusPresentation> = {
  pending: { label: "待处理", className: "text-gray-500", icon: Circle },
  ready: { label: "就绪", className: "text-gray-600", icon: CircleDashed },
  running: {
    label: "进行中",
    className: "text-blue-700",
    icon: Loader2,
    spinning: true,
  },
  completed: {
    label: "完成",
    className: "text-emerald-700",
    icon: CheckCircle2,
  },
  failed: { label: "失败", className: "text-red-700", icon: XCircle },
  skipped: { label: "跳过", className: "text-gray-500", icon: CircleDashed },
  cancelled: { label: "已取消", className: "text-gray-600", icon: XCircle },
  timed_out: { label: "超时", className: "text-amber-700", icon: Clock3 },
  interrupted: {
    label: "已中断",
    className: "text-amber-700",
    icon: AlertCircle,
  },
};

function StatusLabel({ status }: { status: StatusPresentation }) {
  const Icon = status.icon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-xs font-medium",
        status.className,
      )}
    >
      <Icon
        className={cn("size-3.5", status.spinning && "animate-spin")}
        aria-hidden="true"
      />
      {status.label}
    </span>
  );
}

function TaskRow({
  task,
  expanded,
  onToggle,
  onToolClick,
}: {
  task: ResearchTaskView;
  expanded: boolean;
  onToggle: () => void;
  onToolClick?: (tool: ToolEvent) => void;
}) {
  const taskStatus = TASK_STATUS[task.status];
  const hasDetails = Boolean(task.result_summary || task.error || task.tools.length);

  return (
    <div className="min-w-0 border-b border-gray-100 last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        disabled={!hasDetails}
        aria-expanded={hasDetails ? expanded : undefined}
        className="flex w-full min-w-0 items-start gap-2 py-2.5 text-left disabled:cursor-default"
      >
        {hasDetails ? (
          expanded ? (
            <ChevronDown className="mt-0.5 size-4 shrink-0 text-gray-400" />
          ) : (
            <ChevronRight className="mt-0.5 size-4 shrink-0 text-gray-400" />
          )
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1">
            <span className="min-w-0 break-words text-sm font-medium text-gray-900">
              {task.description}
            </span>
            <StatusLabel status={taskStatus} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500">
            {task.assigned_agent_id && <span>{task.assigned_agent_id}</span>}
            <span>尝试 {task.attempt_count}</span>
            {task.tools.length > 0 && <span>工具 {task.tools.length}</span>}
          </div>
        </div>
      </button>

      {expanded && hasDetails && (
        <div className="mb-2.5 ml-6 min-w-0 border-l border-gray-200 pl-3">
          {task.result_summary && (
            <p className="break-words pb-2 text-xs leading-5 text-gray-600">
              {task.result_summary}
            </p>
          )}
          {task.error?.message && (
            <p className="break-words pb-2 text-xs leading-5 text-red-700">
              {task.error.message}
            </p>
          )}
          {task.tools.map((tool) => (
            <button
              key={tool.tool_call_id ?? `${tool.function}-${tool.status}`}
              type="button"
              onClick={() => onToolClick?.(tool)}
              className="flex w-full min-w-0 items-center justify-between gap-2 py-1.5 text-left text-xs text-gray-600 hover:text-gray-950 disabled:cursor-default"
              disabled={!onToolClick}
            >
              <span className="min-w-0 truncate font-mono">{tool.function}</span>
              <span className="shrink-0 text-gray-400">
                {tool.status === "called" ? "已返回" : "调用中"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ResearchRunPanel({
  view,
  onToolClick,
  className,
}: ResearchRunPanelProps) {
  const [activeTab, setActiveTab] = useState<"tasks" | "sources">("tasks");
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const run = view.run;

  const tasks = useMemo(
    () =>
      view.taskOrder
        .map((taskId) => view.tasks[taskId])
        .filter((task): task is ResearchTaskView => Boolean(task)),
    [view.taskOrder, view.tasks],
  );
  const sources = useMemo(() => Object.values(view.sources), [view.sources]);

  if (!run) return null;

  const status = RUN_STATUS[run.status];
  const completedTasks = tasks.filter((task) => task.status === "completed").length;
  const notices = Array.from(
    new Set([
      ...(view.review?.missing_questions ?? []),
      ...(view.review?.issues ?? []),
    ]),
  );

  const toggleTask = (taskId: string) => {
    setExpandedTasks((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  return (
    <section
      aria-label="研究团队进度"
      className={cn(
        "w-full min-w-0 overflow-hidden border-y border-gray-200 bg-white px-3 py-3",
        className,
      )}
    >
      <header className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <Users className="size-4 shrink-0 text-gray-500" aria-hidden="true" />
            <h2 className="truncate text-sm font-semibold text-gray-950">
              研究团队
            </h2>
          </div>
          <p className="mt-1 line-clamp-2 break-words text-xs leading-5 text-gray-500">
            {run.goal}
          </p>
        </div>
        <StatusLabel status={status} />
      </header>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500">
        <span>任务 {completedTasks}/{tasks.length}</span>
        <span>来源 {sources.length}</span>
        {run.usage.elapsed_ms > 0 && (
          <span>{Math.max(1, Math.round(run.usage.elapsed_ms / 1000))} 秒</span>
        )}
      </div>

      {(run.error?.message || notices.length > 0) && (
        <div className="mt-2 border-l-2 border-amber-400 pl-2 text-xs leading-5 text-gray-700">
          {run.error?.message && <p className="break-words">{run.error.message}</p>}
          {notices.map((notice) => (
            <p key={notice} className="break-words">
              {notice}
            </p>
          ))}
        </div>
      )}

      <div
        role="tablist"
        aria-label="研究运行详情"
        className="mt-3 flex border-b border-gray-200"
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "tasks"}
          onClick={() => setActiveTab("tasks")}
          className={cn(
            "border-b-2 px-3 py-1.5 text-xs font-medium",
            activeTab === "tasks"
              ? "border-gray-900 text-gray-950"
              : "border-transparent text-gray-500 hover:text-gray-800",
          )}
        >
          任务
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "sources"}
          onClick={() => setActiveTab("sources")}
          className={cn(
            "border-b-2 px-3 py-1.5 text-xs font-medium",
            activeTab === "sources"
              ? "border-gray-900 text-gray-950"
              : "border-transparent text-gray-500 hover:text-gray-800",
          )}
        >
          来源
        </button>
      </div>

      <div
        role="tabpanel"
        className="max-h-52 min-w-0 overflow-y-auto overflow-x-hidden"
      >
        {activeTab === "tasks" ? (
          tasks.length > 0 ? (
            tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                expanded={expandedTasks.has(task.id)}
                onToggle={() => toggleTask(task.id)}
                onToolClick={onToolClick}
              />
            ))
          ) : (
            <p className="py-5 text-center text-sm text-gray-500">
              正在生成研究计划
            </p>
          )
        ) : (
          <ResearchSourceList sources={sources} />
        )}
      </div>
    </section>
  );
}
