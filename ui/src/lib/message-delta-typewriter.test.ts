import { describe, expect, it, vi } from 'vitest'
import { createMessageDeltaTypewriter } from './message-delta-typewriter'
import type { SSEEventData } from './api/types'

describe('message delta typewriter', () => {
  it('emits queued message deltas one character at a time', () => {
    vi.useFakeTimers()
    const appended: SSEEventData[] = []
    const typewriter = createMessageDeltaTypewriter(
      (event) => appended.push(event),
      20
    )

    typewriter.enqueue({
      type: 'message_delta',
      data: { stream_id: 'stream-1', role: 'assistant', delta: '你好' },
    })

    expect(appended).toHaveLength(0)
    vi.advanceTimersByTime(20)
    expect(appended).toEqual([
      {
        type: 'message_delta',
        data: { stream_id: 'stream-1', role: 'assistant', delta: '你' },
      },
    ])
    vi.advanceTimersByTime(20)
    expect(appended[1]).toEqual({
      type: 'message_delta',
      data: { stream_id: 'stream-1', role: 'assistant', delta: '好' },
    })

    typewriter.stop()
    vi.useRealTimers()
  })

  it('drops queued characters for a stream when the final message arrives', () => {
    vi.useFakeTimers()
    const appended: SSEEventData[] = []
    const typewriter = createMessageDeltaTypewriter(
      (event) => appended.push(event),
      20
    )

    typewriter.enqueue({
      type: 'message_delta',
      data: { stream_id: 'stream-1', role: 'assistant', delta: '你好' },
    })
    vi.advanceTimersByTime(20)
    typewriter.clearStream('stream-1')
    vi.advanceTimersByTime(20)

    expect(appended).toHaveLength(1)
    typewriter.stop()
    vi.useRealTimers()
  })
})
