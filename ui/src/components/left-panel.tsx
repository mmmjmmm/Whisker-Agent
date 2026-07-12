'use client'

import {useRouter} from 'next/navigation'
import {Sidebar, SidebarContent, SidebarHeader, SidebarTrigger} from '@/components/ui/sidebar'
import {Button} from '@/components/ui/button'
import {Plus} from 'lucide-react'
import {Kbd, KbdGroup} from '@/components/ui/kbd'
import {SessionList} from '@/components/session-list'

export function LeftPanel() {
  const router = useRouter()

  return (
    <Sidebar>
      {/* 顶部的切换按钮 */}
      <SidebarHeader className="runner-grid min-h-14 border-b border-sidebar-border">
        <SidebarTrigger className="cursor-pointer"/>
      </SidebarHeader>
      {/* 中间内容 */}
      <SidebarContent className="p-2.5">
        {/* 新建任务 */}
        <Button
          className="mb-3 w-full cursor-pointer justify-start border border-foreground"
          onClick={() => router.push('/')}
        >
          <Plus/>
          新建任务
          <KbdGroup className="ml-auto">
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </KbdGroup>
        </Button>
        {/* 会话列表 */}
        <SessionList/>
      </SidebarContent>
    </Sidebar>
  )
}
