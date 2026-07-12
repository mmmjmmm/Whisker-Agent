'use client'

import { BookOpen } from 'lucide-react'
import { ToolBadge } from './tool-badge'

export interface SkillToolProps {
  label: string
  onClick?: () => void
}

export function SkillTool({ label, onClick }: SkillToolProps) {
  return <ToolBadge icon={BookOpen} label={label} onClick={onClick} />
}
