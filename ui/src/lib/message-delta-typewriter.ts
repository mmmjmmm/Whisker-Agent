import type { SSEEventData } from "@/lib/api/types";

type MessageDeltaSSEEvent = Extract<SSEEventData, { type: "message_delta" }>;

export type MessageDeltaTypewriter = {
  enqueue: (event: MessageDeltaSSEEvent) => void;
  clearStream: (streamId: string) => void;
  stop: () => void;
};

export function createMessageDeltaTypewriter(
  append: (event: MessageDeltaSSEEvent) => void,
  intervalMs = 16,
): MessageDeltaTypewriter {
  let queue: MessageDeltaSSEEvent[] = [];
  let timer: ReturnType<typeof setInterval> | null = null;

  const stopTimer = () => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
  };

  const tick = () => {
    const next = queue.shift();
    if (!next) {
      stopTimer();
      return;
    }
    append(next);
  };

  const start = () => {
    if (timer !== null) return;
    timer = setInterval(tick, intervalMs);
  };

  return {
    enqueue(event) {
      for (const char of Array.from(event.data.delta)) {
        queue.push({
          ...event,
          data: {
            ...event.data,
            delta: char,
          },
        });
      }
      if (queue.length > 0) start();
    },
    clearStream(streamId) {
      queue = queue.filter((event) => event.data.stream_id !== streamId);
      if (queue.length === 0) stopTimer();
    },
    stop() {
      queue = [];
      stopTimer();
    },
  };
}
