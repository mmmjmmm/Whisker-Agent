'use client'

import type { LucideIcon } from 'lucide-react'

export interface ToolBadgeProps {
  icon: LucideIcon
  label: string
  onClick?: () => void
}

export function ToolBadge({ icon: Icon, label, onClick }: ToolBadgeProps) {
  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } } : undefined}
      className="font-meta inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 py-1 text-xs text-foreground w-fit max-w-full min-w-0 cursor-pointer hover:border-emphasis hover:bg-accent transition-colors duration-200"
    >
      <span className="shrink-0 flex items-center justify-center text-muted-foreground">
        <Icon size={16} className="shrink-0" />
      </span>
      <span className="truncate max-w-[480px]">{label}</span>
    </div>
  )
}
