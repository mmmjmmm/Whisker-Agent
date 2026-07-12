export type ScrollMetrics = {
  scrollHeight: number
  clientHeight: number
  scrollTop: number
}

export function isNearScrollBottom(metrics: ScrollMetrics, threshold = 80): boolean {
  return metrics.scrollHeight - metrics.clientHeight - metrics.scrollTop <= threshold
}

export function clampPanelSize(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

export function getNextResizableWidth({
  side,
  pointerX,
  viewportWidth,
  min,
  max,
}: {
  side: 'left' | 'right'
  pointerX: number
  viewportWidth: number
  min: number
  max: number
}): number {
  const rawWidth = side === 'left' ? pointerX : viewportWidth - pointerX
  return clampPanelSize(rawWidth, min, max)
}

export function shouldAutoOpenToolPreview({
  autoPreviewEnabled,
  isRunning,
  vncOpen,
  toolCount,
  previousToolCount,
  hasLatestTool,
}: {
  autoPreviewEnabled: boolean
  isRunning: boolean
  vncOpen: boolean
  toolCount: number
  previousToolCount: number
  hasLatestTool: boolean
}): boolean {
  return (
    autoPreviewEnabled &&
    isRunning &&
    !vncOpen &&
    hasLatestTool &&
    toolCount > previousToolCount
  )
}
