'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
} from 'react'
import { Activity, AlertCircle, Clock, Database, X } from 'lucide-react'
import { sessionApi } from '@/lib/api/session'
import type {
  TraceDetailData,
  TraceMetrics,
  TraceSpan,
  TraceSummary,
} from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog'
import { clampPanelSize } from '@/lib/ui-layout'

export interface TracePanelProps {
  sessionId: string
  onClose: () => void
}

const TRACE_LIST_DEFAULT_WIDTH = 280
const TRACE_LIST_MIN_WIDTH = 200
const TRACE_LIST_MAX_WIDTH = 520
const SPAN_TREE_DEFAULT_HEIGHT = 260
const SPAN_TREE_MIN_HEIGHT = 140
const SPAN_TREE_MAX_HEIGHT = 560

function formatMs(value?: number | null) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${Math.round(value)}ms`
  return `${(value / 1000).toFixed(2)}s`
}

function formatJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function buildChildren(spans: TraceSpan[]) {
  const map = new Map<string | null, TraceSpan[]>()
  for (const span of spans) {
    const key = span.parent_span_id ?? null
    const list = map.get(key) ?? []
    list.push(span)
    map.set(key, list)
  }
  return map
}

function SpanTree({
  spans,
  selectedId,
  onSelect,
}: {
  spans: TraceSpan[]
  selectedId?: string
  onSelect: (span: TraceSpan) => void
}) {
  const children = useMemo(() => buildChildren(spans), [spans])
  const spanIds = useMemo(() => new Set(spans.map((span) => span.id)), [spans])
  const roots = spans.filter((span) => {
    return !span.parent_span_id || !spanIds.has(span.parent_span_id)
  })

  const renderNode = (span: TraceSpan, depth: number) => {
    const isSelected = span.id === selectedId
    return (
      <div key={span.id}>
        <button
          type="button"
          onClick={() => onSelect(span)}
          className={`w-full rounded-md border px-2 py-1.5 text-left text-xs transition-colors duration-200 ${
            isSelected
              ? 'border-emphasis bg-primary text-primary-foreground'
              : 'border-border bg-card hover:bg-secondary'
          }`}
          style={{ paddingLeft: 8 + depth * 14 }}
        >
          <span className={`block min-w-0 break-words ${span.status === 'error' ? 'text-red-500' : ''}`}>
            {span.span_type} · {span.name} · {formatMs(span.duration_ms)}
          </span>
        </button>
        {(children.get(span.id) ?? []).map((child) => renderNode(child, depth + 1))}
      </div>
    )
  }

  return <div className="flex min-w-0 flex-col gap-1">{roots.map((span) => renderNode(span, 0))}</div>
}

export function TracePanel({ sessionId, onClose }: TracePanelProps) {
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [metrics, setMetrics] = useState<TraceMetrics | null>(null)
  const [detail, setDetail] = useState<TraceDetailData | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(null)
  const [loading, setLoading] = useState(true)
  const [traceListWidth, setTraceListWidth] = useState(TRACE_LIST_DEFAULT_WIDTH)
  const [spanTreeHeight, setSpanTreeHeight] = useState(SPAN_TREE_DEFAULT_HEIGHT)
  const traceBodyRef = useRef<HTMLDivElement>(null)
  const traceDetailRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoading(true)
      const [traceList, traceMetrics] = await Promise.all([
        sessionApi.getSessionTraces(sessionId),
        sessionApi.getSessionTraceMetrics(sessionId),
      ])
      if (cancelled) return

      setTraces(traceList.traces)
      setMetrics(traceMetrics)

      if (traceList.traces[0]) {
        const traceDetail = await sessionApi.getSessionTraceDetail(
          sessionId,
          traceList.traces[0].trace_id
        )
        if (cancelled) return
        setDetail(traceDetail)
        setSelectedSpan(traceDetail.spans[0] ?? null)
      } else {
        setDetail(null)
        setSelectedSpan(null)
      }
      setLoading(false)
    }

    load().catch(() => {
      if (!cancelled) setLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [sessionId])

  const loadTrace = async (traceId: string) => {
    const traceDetail = await sessionApi.getSessionTraceDetail(sessionId, traceId)
    setDetail(traceDetail)
    setSelectedSpan(traceDetail.spans[0] ?? null)
  }

  const handleTraceListResizeStart = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault()

    const container = traceBodyRef.current
    const target = event.currentTarget
    if (!container) return

    const pointerId = event.pointerId
    target.setPointerCapture?.(pointerId)
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const rect = container.getBoundingClientRect()
      const maxWidth = Math.min(TRACE_LIST_MAX_WIDTH, Math.max(TRACE_LIST_MIN_WIDTH, rect.width - 360))
      setTraceListWidth(clampPanelSize(moveEvent.clientX - rect.left, TRACE_LIST_MIN_WIDTH, maxWidth))
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

  const handleSpanTreeResizeStart = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault()

    const container = traceDetailRef.current
    const target = event.currentTarget
    if (!container) return

    const pointerId = event.pointerId
    target.setPointerCapture?.(pointerId)
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const rect = container.getBoundingClientRect()
      const maxHeight = Math.min(SPAN_TREE_MAX_HEIGHT, Math.max(SPAN_TREE_MIN_HEIGHT, rect.height - 180))
      setSpanTreeHeight(clampPanelSize(moveEvent.clientY - rect.top, SPAN_TREE_MIN_HEIGHT, maxHeight))
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

  return (
    <Dialog open onOpenChange={(open) => {
      if (!open) onClose()
    }}>
      <DialogContent
        showCloseButton={false}
        className="runner-trace-shell resize-none gap-0 overflow-hidden p-0 sm:resize"
        style={{
          width: '1200px',
          maxWidth: 'calc(100vw - 2rem)',
          height: '960px',
          maxHeight: '90vh',
        }}
      >
        <DialogTitle className="sr-only">Trace</DialogTitle>
        <div className="flex h-full min-h-0 flex-col bg-card">
          <div className="runner-panel-header flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Activity size={16} />
              <span>Trace</span>
            </div>
            <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭 Trace">
              <X size={16} />
            </Button>
          </div>

          <div className="grid grid-cols-2 gap-2 border-b p-3 text-xs md:grid-cols-4">
            <div className="runner-metric min-w-0 p-2 font-meta">
              <div className="text-muted-foreground">错误率</div>
              <div className="font-medium">{metrics ? `${Math.round(metrics.error_rate * 100)}%` : '-'}</div>
            </div>
            <div className="runner-metric min-w-0 p-2 font-meta">
              <div className="text-muted-foreground">平均耗时</div>
              <div className="font-medium">{metrics ? formatMs(metrics.avg_duration_ms) : '-'}</div>
            </div>
            <div className="runner-metric min-w-0 p-2 font-meta">
              <div className="text-muted-foreground">Token</div>
              <div className="font-medium">{metrics?.total_tokens ?? '-'}</div>
            </div>
            <div className="runner-metric min-w-0 p-2 font-meta">
              <div className="text-muted-foreground">模型</div>
              <div className="truncate font-medium">{metrics?.models.join(', ') || '-'}</div>
            </div>
          </div>

          {loading ? (
            <div className="p-4 text-sm text-muted-foreground">加载中...</div>
          ) : (
            <div
              ref={traceBodyRef}
              className="runner-trace-body grid min-h-0 min-w-0 flex-1 grid-cols-1 grid-rows-[minmax(96px,24%)_minmax(0,1fr)] bg-card sm:grid-cols-[var(--trace-list-width)_6px_minmax(0,1fr)] sm:grid-rows-1"
              style={{ '--trace-list-width': `${traceListWidth}px` } as CSSProperties}
            >
              <div className="runner-trace-list min-h-0 min-w-0 overflow-auto border-b sm:border-r sm:border-b-0">
                <div className="flex min-w-0 flex-col gap-2 p-2">
                  {traces.map((trace) => (
                    <button
                      key={trace.trace_id}
                      type="button"
                      onClick={() => loadTrace(trace.trace_id)}
                      className={`min-w-0 rounded-md border p-2 text-left text-xs transition-colors duration-200 hover:bg-secondary ${
                        detail?.trace_id === trace.trace_id
                          ? 'border-emphasis bg-secondary'
                          : 'border-border bg-card'
                      }`}
                    >
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <span className="min-w-0 truncate font-medium">
                          {trace.root_input_preview || trace.trace_id}
                        </span>
                        {trace.status === 'error' ? (
                          <AlertCircle size={13} className="shrink-0 text-red-500" />
                        ) : (
                          <Clock size={13} className="shrink-0" />
                        )}
                      </div>
                      <div className="font-meta mt-1 break-words text-muted-foreground">
                        {formatMs(trace.duration_ms)} · {trace.error_count} 错误 · {trace.total_tokens} Token
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="调整 Trace 列表宽度"
                onPointerDown={handleTraceListResizeStart}
                className="runner-trace-list-resizer hidden cursor-col-resize bg-transparent transition-colors hover:bg-emphasis/30 sm:block"
              />

              <div
                ref={traceDetailRef}
                className="runner-trace-detail grid min-h-0 min-w-0"
                style={{ gridTemplateRows: `${spanTreeHeight}px 6px minmax(0, 1fr)` }}
              >
                <div className="min-h-0 overflow-auto border-b">
                  <div className="min-w-0 p-3">
                    {detail ? (
                      <SpanTree
                        spans={detail.spans}
                        selectedId={selectedSpan?.id}
                        onSelect={setSelectedSpan}
                      />
                    ) : (
                      <div className="text-sm text-muted-foreground">暂无 Trace</div>
                    )}
                  </div>
                </div>

                <div
                  role="separator"
                  aria-orientation="horizontal"
                  aria-label="调整 Span 详情高度"
                  onPointerDown={handleSpanTreeResizeStart}
                  className="cursor-row-resize bg-transparent transition-colors hover:bg-emphasis/30"
                />

                <div className="min-h-0 overflow-auto">
                  <div className="min-w-0 p-3">
                    {selectedSpan ? (
                      <div className="space-y-3 text-xs">
                        <div className="flex min-w-0 items-center gap-2 font-medium">
                          <Database size={14} className="shrink-0" />
                          <span className="min-w-0 break-words">{selectedSpan.span_type} · {selectedSpan.name}</span>
                        </div>
                        <pre className="font-meta min-w-0 whitespace-pre-wrap break-words rounded-md border border-neutral-700 bg-neutral-950 p-3 text-neutral-100">
                          {formatJson({
                            status: selectedSpan.status,
                            duration_ms: selectedSpan.duration_ms,
                            input: selectedSpan.input,
                            output: selectedSpan.output,
                            error: selectedSpan.error,
                            attributes: selectedSpan.attributes,
                          })}
                        </pre>
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">选择一个 span 查看详情</div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
