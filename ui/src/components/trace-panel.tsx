'use client'

import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertCircle, Clock, Database, X } from 'lucide-react'
import { sessionApi } from '@/lib/api/session'
import type {
  TraceDetailData,
  TraceMetrics,
  TraceSpan,
  TraceSummary,
} from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'

export interface TracePanelProps {
  sessionId: string
  onClose: () => void
}

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
          className={`w-full rounded border px-2 py-1.5 text-left text-xs transition-colors ${
            isSelected
              ? 'border-gray-900 bg-gray-900 text-white'
              : 'border-gray-200 bg-white hover:bg-gray-50'
          }`}
          style={{ marginLeft: depth * 12 }}
        >
          <span className={span.status === 'error' ? 'text-red-500' : ''}>
            {span.span_type} · {span.name} · {formatMs(span.duration_ms)}
          </span>
        </button>
        {(children.get(span.id) ?? []).map((child) => renderNode(child, depth + 1))}
      </div>
    )
  }

  return <div className="flex flex-col gap-1">{roots.map((span) => renderNode(span, 0))}</div>
}

export function TracePanel({ sessionId, onClose }: TracePanelProps) {
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [metrics, setMetrics] = useState<TraceMetrics | null>(null)
  const [detail, setDetail] = useState<TraceDetailData | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(null)
  const [loading, setLoading] = useState(true)

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

  return (
    <aside className="flex h-full w-[640px] flex-col border-l border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Activity size={16} />
          <span>Trace</span>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭 Trace">
          <X size={16} />
        </Button>
      </div>

      <div className="grid grid-cols-4 gap-2 border-b p-3 text-xs">
        <div className="rounded border p-2">
          <div className="text-gray-500">错误率</div>
          <div className="font-medium">{metrics ? `${Math.round(metrics.error_rate * 100)}%` : '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">平均耗时</div>
          <div className="font-medium">{metrics ? formatMs(metrics.avg_duration_ms) : '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">Token</div>
          <div className="font-medium">{metrics?.total_tokens ?? '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">模型</div>
          <div className="truncate font-medium">{metrics?.models.join(', ') || '-'}</div>
        </div>
      </div>

      {loading ? (
        <div className="p-4 text-sm text-gray-500">加载中...</div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[220px_1fr]">
          <ScrollArea className="border-r">
            <div className="flex flex-col gap-2 p-2">
              {traces.map((trace) => (
                <button
                  key={trace.trace_id}
                  type="button"
                  onClick={() => loadTrace(trace.trace_id)}
                  className={`rounded border p-2 text-left text-xs hover:bg-gray-50 ${
                    detail?.trace_id === trace.trace_id ? 'border-gray-900' : 'border-gray-200'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">
                      {trace.root_input_preview || trace.trace_id}
                    </span>
                    {trace.status === 'error' ? (
                      <AlertCircle size={13} className="text-red-500" />
                    ) : (
                      <Clock size={13} />
                    )}
                  </div>
                  <div className="mt-1 text-gray-500">
                    {formatMs(trace.duration_ms)} · {trace.error_count} 错误 · {trace.total_tokens} Token
                  </div>
                </button>
              ))}
            </div>
          </ScrollArea>

          <div className="grid min-h-0 grid-rows-[minmax(160px,240px)_1fr]">
            <ScrollArea className="border-b">
              <div className="p-3">
                {detail ? (
                  <SpanTree
                    spans={detail.spans}
                    selectedId={selectedSpan?.id}
                    onSelect={setSelectedSpan}
                  />
                ) : (
                  <div className="text-sm text-gray-500">暂无 Trace</div>
                )}
              </div>
            </ScrollArea>
            <ScrollArea>
              <div className="p-3">
                {selectedSpan ? (
                  <div className="space-y-3 text-xs">
                    <div className="flex items-center gap-2 font-medium">
                      <Database size={14} />
                      <span>{selectedSpan.span_type} · {selectedSpan.name}</span>
                    </div>
                    <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-gray-100">{formatJson({
                      status: selectedSpan.status,
                      duration_ms: selectedSpan.duration_ms,
                      input: selectedSpan.input,
                      output: selectedSpan.output,
                      error: selectedSpan.error,
                      attributes: selectedSpan.attributes,
                    })}</pre>
                  </div>
                ) : (
                  <div className="text-sm text-gray-500">选择一个 span 查看详情</div>
                )}
              </div>
            </ScrollArea>
          </div>
        </div>
      )}
    </aside>
  )
}
