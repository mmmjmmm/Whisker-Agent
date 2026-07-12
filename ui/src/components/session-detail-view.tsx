'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { useRouter } from 'next/navigation'
import { SessionHeader } from '@/components/session-header'
import { ChatInput } from '@/components/chat-input'
import { PlanPanel } from '@/components/plan-panel'
import { ChatMessage } from '@/components/chat-message'
import { FilePreviewPanel } from '@/components/file-preview-panel'
import { ToolPreviewPanel } from '@/components/tool-preview-panel'
import { TracePanel } from '@/components/trace-panel'
import { VNCOverlay } from '@/components/vnc-overlay'
import { useSessionDetail } from '@/hooks/use-session-detail'
import { getToolKind } from '@/components/tool-use/utils'
import {
  eventsToTimeline,
  getLatestPlanFromEvents,
  getLatestTeamPlanFromEvents,
} from '@/lib/session-events'
import type { AgentMode, ToolEvent, FileInfo } from '@/lib/api/types'
import type { AttachmentFile, TimelineItem } from '@/lib/session-events'
import { sessionApi } from '@/lib/api/session'
import {
  getNextResizableWidth,
  isNearScrollBottom,
  shouldAutoOpenToolPreview,
} from '@/lib/ui-layout'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'

export interface SessionDetailViewProps {
  sessionId: string
  initialMessage?: string
  initialAttachments?: string[]
  initialMode?: AgentMode
  hasInitialMessage?: boolean
}

const RIGHT_PANEL_DEFAULT_WIDTH = 600
const RIGHT_PANEL_MIN_WIDTH = 360
const RIGHT_PANEL_MAX_WIDTH = 960
const MAIN_CONTENT_MIN_WIDTH = 360

/**
 * 从 timeline 中找到最后一个非 message 类型的工具事件
 */
function findLatestTool(timeline: TimelineItem[]): ToolEvent | null {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const item = timeline[i]
    if (item.kind === 'tool' && getToolKind(item.data) !== 'message') {
      return item.data
    }
    if (item.kind === 'step' && item.tools.length > 0) {
      for (let j = item.tools.length - 1; j >= 0; j--) {
        if (getToolKind(item.tools[j]) !== 'message') {
          return item.tools[j]
        }
      }
    }
  }
  return null
}

export function SessionDetailView({ sessionId, initialMessage, initialAttachments, initialMode, hasInitialMessage }: SessionDetailViewProps) {
  const router = useRouter()
  const {
    session,
    files,
    events,
    loading,
    error,
    refresh,
    refreshFiles,
    sendMessage,
    streaming,
  } = useSessionDetail(sessionId, hasInitialMessage)

  const timeline = useMemo(() => eventsToTimeline(events), [events])
  const planSteps = useMemo(() => getLatestPlanFromEvents(events), [events])
  const teamPlanSteps = useMemo(() => getLatestTeamPlanFromEvents(events), [events])
  const persistedMode = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index--) {
      const event = events[index]
      if (event.type === 'message' && event.data.role === 'user') {
        return event.data.agent_mode ?? null
      }
    }
    return null
  }, [events])

  const [modeOverride, setModeOverride] = useState<AgentMode | null>(initialMode ?? null)
  const mode = modeOverride ?? persistedMode ?? 'react'
  const [fileListOpen, setFileListOpen] = useState(false)
  const [previewFile, setPreviewFile] = useState<AttachmentFile | null>(null)
  const [previewTool, setPreviewTool] = useState<ToolEvent | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)
  const [vncOpen, setVncOpen] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(RIGHT_PANEL_DEFAULT_WIDTH)
  const [autoPreviewEnabled, setAutoPreviewEnabled] = useState(true)
  const [previewToolSelectionCount, setPreviewToolSelectionCount] = useState(0)
  const initialMessageSentRef = useRef(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const isChatPinnedToBottomRef = useRef(true)

  const latestTool = useMemo(() => findLatestTool(timeline), [timeline])
  const toolCount = useMemo(() => timeline.reduce((n, item) => {
    if (item.kind === 'tool') return n + 1
    if (item.kind === 'step') return n + item.tools.length
    return n
  }, 0), [timeline])
  const activePreviewTool = useMemo(() => {
    if (previewFile) return null
    if (shouldAutoOpenToolPreview({
      autoPreviewEnabled,
      isRunning: session?.status === 'running',
      vncOpen,
      toolCount,
      previousToolCount: previewTool ? previewToolSelectionCount : 0,
      hasLatestTool: latestTool !== null,
    })) {
      return latestTool
    }
    return previewTool
  }, [
    autoPreviewEnabled,
    latestTool,
    previewFile,
    previewTool,
    previewToolSelectionCount,
    session?.status,
    toolCount,
    vncOpen,
  ])

  const hasPreview = previewFile !== null || activePreviewTool !== null

  /**
   * 将 previewTool 解析为 timeline 中最新版本的工具对象。
   * 自动跟踪设置 previewTool 时工具事件可能尚无 content（如截图），
   * 后续 SSE 更新后 timeline 中对象已刷新但 state 仍为旧引用。
   * 通过 tool_call_id 匹配获取最新版本。
   */
  const resolvedPreviewTool = useMemo(() => {
    if (!activePreviewTool) return null
    const id = (activePreviewTool as { tool_call_id?: string }).tool_call_id
    if (!id) return activePreviewTool

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i]
      if (item.kind === 'tool' && (item.data as { tool_call_id?: string }).tool_call_id === id) {
        return item.data
      }
      if (item.kind === 'step') {
        for (const t of item.tools) {
          if ((t as { tool_call_id?: string }).tool_call_id === id) return t
        }
      }
    }
    return activePreviewTool
  }, [activePreviewTool, timeline])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const container = scrollContainerRef.current
    if (!container) return
    isChatPinnedToBottomRef.current = true
    container.scrollTo({ top: container.scrollHeight, behavior })
  }, [])

  const updateChatPinnedState = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return
    isChatPinnedToBottomRef.current = isNearScrollBottom(container)
  }, [])

  const handleRightPanelResizeStart = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault()

    const target = event.currentTarget
    const pointerId = event.pointerId
    target.setPointerCapture?.(pointerId)
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const maxWidth = Math.max(
        RIGHT_PANEL_MIN_WIDTH,
        Math.min(RIGHT_PANEL_MAX_WIDTH, window.innerWidth - MAIN_CONTENT_MIN_WIDTH)
      )
      setRightPanelWidth(
        getNextResizableWidth({
          side: 'right',
          pointerX: moveEvent.clientX,
          viewportWidth: window.innerWidth,
          min: RIGHT_PANEL_MIN_WIDTH,
          max: maxWidth,
        })
      )
    }

    const cleanup = () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', cleanup)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      try {
        target.releasePointerCapture?.(pointerId)
      } catch {
        // Pointer capture may already be released by the browser.
      }
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', cleanup)
  }, [])

  useEffect(() => {
    if (!isChatPinnedToBottomRef.current) return
    const frame = requestAnimationFrame(() => scrollToBottom('auto'))
    return () => cancelAnimationFrame(frame)
  }, [events.length, scrollToBottom])

  useEffect(() => {
    if (
      initialMessage &&
      !initialMessageSentRef.current &&
      session &&
      !loading &&
      !streaming
    ) {
      initialMessageSentRef.current = true
      sendMessage(initialMessage, initialAttachments || [], mode)
        .then(() => {
          setTimeout(() => {
            router.replace(`/sessions/${sessionId}`)
          }, 100)
        })
        .catch((e) => {
          toast.error(e instanceof Error ? e.message : '发送消息失败')
        })
    }
  }, [initialMessage, initialAttachments, mode, session, loading, streaming, sendMessage, sessionId, router])

  const handleSend = useCallback(
    async (message: string, uploadedFiles: FileInfo[]) => {
      try {
        scrollToBottom('smooth')
        const attachmentIds = uploadedFiles.map((f) => f.id)
        await sendMessage(message, attachmentIds, mode)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : '发送失败，请重试')
        throw e
      }
    },
    [mode, scrollToBottom, sendMessage]
  )

  const handleViewAllFiles = useCallback(() => {
    refreshFiles()
    setFileListOpen(true)
  }, [refreshFiles])

  const handleFileClick = useCallback((file: AttachmentFile) => {
    setAutoPreviewEnabled(true)
    setPreviewFile(file)
    setPreviewTool(null)
    setTraceOpen(false)
  }, [])

  const handleToolClick = useCallback((tool: ToolEvent) => {
    const kind = getToolKind(tool)
    if (kind === 'message') return
    setAutoPreviewEnabled(true)
    setPreviewTool(tool)
    setPreviewToolSelectionCount(toolCount)
    setPreviewFile(null)
    setTraceOpen(false)
  }, [toolCount])

  const handleClosePreview = useCallback(() => {
    setAutoPreviewEnabled(false)
    setPreviewFile(null)
    setPreviewTool(null)
  }, [])

  const handleTraceOpen = useCallback(() => {
    setAutoPreviewEnabled(false)
    setPreviewFile(null)
    setPreviewTool(null)
    setTraceOpen(true)
  }, [])

  const handleTraceClose = useCallback(() => {
    setTraceOpen(false)
  }, [])

  const handleAutoPreviewChange = useCallback((enabled: boolean) => {
    setAutoPreviewEnabled(enabled)
    if (!enabled) {
      setPreviewFile(null)
      setPreviewTool(null)
      return
    }

    const latest = findLatestTool(timeline)
    if (latest) {
      setPreviewTool(latest)
      setPreviewToolSelectionCount(toolCount)
      setPreviewFile(null)
      setTraceOpen(false)
    }
  }, [timeline, toolCount])

  const handleJumpToLatest = useCallback(() => {
    const latest = findLatestTool(timeline)
    if (latest) {
      setAutoPreviewEnabled(true)
      setPreviewTool(latest)
      setPreviewToolSelectionCount(toolCount)
      setPreviewFile(null)
    }
    scrollToBottom('smooth')
  }, [scrollToBottom, timeline, toolCount])

  const handleOpenVNC = useCallback(() => {
    setVncOpen(true)
  }, [])

  const handleCloseVNC = useCallback(() => {
    setVncOpen(false)
    // 关闭 VNC 后跳转到最新工具
    const latest = findLatestTool(timeline)
    if (latest && session?.status === 'running' && autoPreviewEnabled) {
      setPreviewTool(latest)
      setPreviewToolSelectionCount(toolCount)
      setPreviewFile(null)
      setTimeout(() => scrollToBottom('smooth'), 100)
    }
  }, [autoPreviewEnabled, scrollToBottom, timeline, toolCount, session?.status])

  const handleStop = useCallback(async () => {
    if (!session) return
    try {
      await sessionApi.stopSession(sessionId)
      toast.success('任务已停止')
      refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '停止任务失败')
    }
  }, [session, sessionId, refresh])

  if (loading && !session) {
    return (
      <div className="relative flex flex-col h-full flex-1 min-w-0 px-4 items-center justify-center">
        {hasInitialMessage ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            <span>正在思考中...</span>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">加载中...</p>
        )}
      </div>
    )
  }

  if (error && !session) {
    return (
      <div className="relative flex flex-col h-full flex-1 min-w-0 px-4 items-center justify-center gap-2">
        <p className="text-sm text-red-600">{error.message}</p>
        <button
          type="button"
          onClick={() => refresh()}
          className="text-sm text-emphasis underline"
        >
          重试
        </button>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="relative flex flex-col h-full flex-1 min-w-0 px-4 items-center justify-center">
        <p className="text-sm text-muted-foreground">未找到该任务</p>
      </div>
    )
  }

  return (
    <>
      <div className="flex flex-row h-screen w-full overflow-hidden">
        {/* 主内容区 */}
        <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
          <div className={`flex flex-col h-full mx-auto w-full min-w-0 px-4 ${hasPreview ? '' : 'max-w-[768px]'}`}>
            <div className="flex-shrink-0">
              <SessionHeader
                title={session.title}
                files={files}
                fileListOpen={fileListOpen}
                onFileListOpenChange={setFileListOpen}
                onFetchFiles={refreshFiles}
                onFileClick={handleFileClick}
                onTraceOpen={handleTraceOpen}
                autoPreviewEnabled={autoPreviewEnabled}
                onAutoPreviewChange={handleAutoPreviewChange}
              />
            </div>

            <div ref={scrollContainerRef} className="flex-1 overflow-y-auto" onScroll={updateChatPinnedState}>
              <div className="flex flex-col w-full gap-3 pt-3">
                {timeline.length === 0 && !streaming && !hasInitialMessage && (
                  <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                    暂无对话记录，在下方输入任务或提问
                  </div>
                )}
                {timeline.map((item) => (
                  <ChatMessage
                    key={item.id}
                    item={item}
                    onViewAllFiles={handleViewAllFiles}
                    onFileClick={handleFileClick}
                    onToolClick={handleToolClick}
                  />
                ))}

                {(session?.status === 'running' || hasInitialMessage) && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground py-3">
                    <Loader2 className="size-4 animate-spin" />
                    <span>正在思考中...</span>
                  </div>
                )}

                <div className="h-[140px]" />
              </div>
            </div>

            <div className="flex-shrink-0 border-t border-dashed border-border bg-background py-4">
              <PlanPanel
                className="mb-2"
                steps={teamPlanSteps ?? planSteps}
              />
              <ChatInput
                onSend={handleSend}
                sessionId={sessionId}
                isRunning={session?.status === 'running'}
                onStop={handleStop}
                mode={mode}
                onModeChange={setModeOverride}
              />
            </div>
          </div>
        </div>

        {/* 文件预览面板 */}
        {previewFile && (
          <div
            className="relative flex-shrink-0 h-full animate-in slide-in-from-right duration-300"
            style={{ width: rightPanelWidth, maxWidth: '100vw' }}
          >
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="调整右侧面板宽度"
              onPointerDown={handleRightPanelResizeStart}
              className="absolute inset-y-0 left-0 z-30 w-2 -translate-x-1/2 cursor-col-resize"
            >
              <div className="mx-auto h-full w-px bg-transparent transition-colors hover:bg-gray-300" />
            </div>
            <FilePreviewPanel file={previewFile} onClose={handleClosePreview} />
          </div>
        )}

        {/* 工具预览面板 */}
        {resolvedPreviewTool && (
          <div
            className="relative flex-shrink-0 h-full animate-in slide-in-from-right duration-300"
            style={{ width: rightPanelWidth, maxWidth: '100vw' }}
          >
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="调整右侧面板宽度"
              onPointerDown={handleRightPanelResizeStart}
              className="absolute inset-y-0 left-0 z-30 w-2 -translate-x-1/2 cursor-col-resize"
            >
              <div className="mx-auto h-full w-px bg-transparent transition-colors hover:bg-gray-300" />
            </div>
            <ToolPreviewPanel
              tool={resolvedPreviewTool}
              onClose={handleClosePreview}
              onJumpToLatest={handleJumpToLatest}
              onOpenVNC={getToolKind(resolvedPreviewTool) === 'browser' ? handleOpenVNC : undefined}
            />
          </div>
        )}

        {traceOpen && (
          <TracePanel sessionId={sessionId} onClose={handleTraceClose} />
        )}
      </div>

      {/* noVNC 全屏远程桌面覆盖层 */}
      {vncOpen && (
        <VNCOverlay sessionId={sessionId} onClose={handleCloseVNC} />
      )}
    </>
  )
}
