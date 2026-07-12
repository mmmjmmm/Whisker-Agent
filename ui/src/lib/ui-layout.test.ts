import { describe, expect, it } from 'vitest'
import {
  clampPanelSize,
  getNextResizableWidth,
  isNearScrollBottom,
  shouldAutoOpenToolPreview,
} from './ui-layout'

describe('ui layout helpers', () => {
  it('detects whether a scroll container is still near the bottom', () => {
    expect(
      isNearScrollBottom({ scrollHeight: 1000, clientHeight: 300, scrollTop: 690 }, 16)
    ).toBe(true)
    expect(
      isNearScrollBottom({ scrollHeight: 1000, clientHeight: 300, scrollTop: 620 }, 16)
    ).toBe(false)
  })

  it('clamps panel sizes to the configured bounds', () => {
    expect(clampPanelSize(120, 240, 900)).toBe(240)
    expect(clampPanelSize(520, 240, 900)).toBe(520)
    expect(clampPanelSize(1200, 240, 900)).toBe(900)
  })

  it('calculates left and right panel widths from the dragged edge', () => {
    expect(
      getNextResizableWidth({ side: 'left', pointerX: 320, viewportWidth: 1200, min: 240, max: 480 })
    ).toBe(320)
    expect(
      getNextResizableWidth({ side: 'right', pointerX: 760, viewportWidth: 1200, min: 320, max: 900 })
    ).toBe(440)
  })

  it('only auto opens tool preview while the user controlled switch is enabled', () => {
    expect(
      shouldAutoOpenToolPreview({
        autoPreviewEnabled: true,
        isRunning: true,
        vncOpen: false,
        toolCount: 3,
        previousToolCount: 2,
        hasLatestTool: true,
      })
    ).toBe(true)

    expect(
      shouldAutoOpenToolPreview({
        autoPreviewEnabled: false,
        isRunning: true,
        vncOpen: false,
        toolCount: 3,
        previousToolCount: 2,
        hasLatestTool: true,
      })
    ).toBe(false)
  })
})
