"use client";

import { Bot, Users } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AgentMode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type AgentModeControlProps = {
  value: AgentMode;
  onChange: (mode: AgentMode) => void;
  researchTeamEnabled: boolean;
  disabled?: boolean;
  className?: string;
};

type ModeOption = {
  value: AgentMode;
  label: string;
  icon: typeof Bot;
};

const options: ModeOption[] = [
  { value: "react", label: "单 Agent", icon: Bot },
  { value: "research_team", label: "研究团队", icon: Users },
];

export function AgentModeControl({
  value,
  onChange,
  researchTeamEnabled,
  disabled = false,
  className,
}: AgentModeControlProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Agent 模式"
      className={cn(
        "inline-flex min-w-0 items-center gap-1 rounded-md border border-gray-200 bg-gray-100 p-1",
        className,
      )}
    >
      {options.map((option) => {
        const unavailable =
          option.value === "research_team" && !researchTeamEnabled;
        const optionDisabled = disabled || unavailable;
        const Icon = option.icon;
        const control = (
          <button
            type="button"
            role="radio"
            aria-label={option.label}
            aria-checked={value === option.value}
            disabled={optionDisabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex min-h-9 items-center justify-center gap-1.5 rounded-sm border px-2.5 text-xs font-medium transition-colors duration-200",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:ring-offset-1",
              value === option.value
                ? "border-gray-300 bg-white text-gray-900 shadow-xs"
                : "border-transparent text-gray-600 hover:bg-white/70 hover:text-gray-900",
              optionDisabled && "cursor-not-allowed opacity-45",
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            <span>{option.label}</span>
          </button>
        );

        if (!unavailable) return <span key={option.value}>{control}</span>;
        return (
          <Tooltip key={option.value}>
            <TooltipTrigger asChild>
              <span className="inline-flex">{control}</span>
            </TooltipTrigger>
            <TooltipContent side="top">暂未开放</TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
