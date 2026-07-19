import { describe, expect, it } from 'vitest'
import { eventsToTimeline } from './session-events'
import type { SSEEventData } from './api/types'

describe('session event timeline', () => {
  it('merges assistant message deltas into one temporary message', () => {
    const timeline = eventsToTimeline([
      {
        type: 'message_delta',
        data: { stream_id: 'stream-1', role: 'assistant', delta: '你' },
      },
      {
        type: 'message_delta',
        data: { stream_id: 'stream-1', role: 'assistant', delta: '好' },
      },
    ] as SSEEventData[])

    expect(timeline).toHaveLength(1)
    expect(timeline[0]).toMatchObject({
      kind: 'assistant',
      data: { role: 'assistant', message: '你好', stream_id: 'stream-1' },
    })
  })

  it('replaces a temporary delta message with the final assistant message', () => {
    const timeline = eventsToTimeline([
      {
        type: 'message_delta',
        data: { stream_id: 'stream-1', role: 'assistant', delta: '临时' },
      },
      {
        type: 'message',
        data: {
          role: 'assistant',
          message: '最终内容',
          stream_id: 'stream-1',
        },
      },
    ] as SSEEventData[])

    expect(timeline).toHaveLength(1)
    expect(timeline[0]).toMatchObject({
      kind: 'assistant',
      data: { role: 'assistant', message: '最终内容', stream_id: 'stream-1' },
    })
  })
})
