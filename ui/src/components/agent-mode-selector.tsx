"use client";

import { Button } from "@/components/ui/button";
import type { AgentMode } from "@/lib/api/types";

export function AgentModeSelector({
  value,
  onChange,
  disabled = false,
}: {
  value: AgentMode;
  onChange: (mode: AgentMode) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label="Agent 模式"
      className="flex rounded-full border bg-gray-50 p-0.5"
    >
      {(
        [
          ["react", "单 Agent"],
          ["team", "多 Agent"],
        ] as const
      ).map(([mode, label]) => (
        <Button
          key={mode}
          type="button"
          size="sm"
          variant={value === mode ? "default" : "ghost"}
          className="h-8 rounded-full px-3 text-xs"
          disabled={disabled}
          onClick={() => onChange(mode)}
          aria-pressed={value === mode}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
