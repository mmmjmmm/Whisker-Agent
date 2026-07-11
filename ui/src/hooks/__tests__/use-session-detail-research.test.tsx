import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionDetail } from "@/hooks/use-session-detail";
import type { SSEEventData, SessionDetail } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({
  onEvent: null as ((event: SSEEventData) => void) | null,
  getSessionDetail: vi.fn(),
  getSessionFiles: vi.fn(),
  getRun: vi.fn(),
  getRunTasks: vi.fn(),
  getRunSources: vi.fn(),
  chat: vi.fn(),
}));

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionDetail: mocks.getSessionDetail,
    getSessionFiles: mocks.getSessionFiles,
    getRun: mocks.getRun,
    getRunTasks: mocks.getRunTasks,
    getRunSources: mocks.getRunSources,
    chat: mocks.chat,
  },
}));

function createSession(events: SSEEventData[] = []): SessionDetail {
  return {
    session_id: "session-1",
    title: "Research",
    latest_message: "",
    latest_message_at: "2026-07-10T00:00:00Z",
    status: "running",
    unread_message_count: 0,
    events,
  };
}

describe("useSessionDetail research team state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.onEvent = null;
    mocks.getSessionDetail.mockResolvedValue(createSession());
    mocks.getSessionFiles.mockResolvedValue([]);
    mocks.getRun.mockResolvedValue(null);
    mocks.getRunTasks.mockResolvedValue([]);
    mocks.getRunSources.mockResolvedValue([]);
    mocks.chat.mockImplementation(
      (_sessionId: string, _params: unknown, onEvent: (event: SSEEventData) => void) => {
        mocks.onEvent = onEvent;
        return vi.fn();
      },
    );
  });

  it("任务级错误不会结束整个研究运行", async () => {
    const { result } = renderHook(() => useSessionDetail("session-1"));

    await waitFor(() => expect(mocks.onEvent).not.toBeNull());
    act(() => {
      mocks.onEvent?.({
        type: "error",
        data: {
          event_id: "event-1",
          session_id: "session-1",
          run_id: "run-1",
          task_id: "task-1",
          scope: "task",
          error: "timeout",
        },
      });
    });

    expect(result.current.session?.status).toBe("running");
  });

  it("刷新时用专用查询覆盖事件投影", async () => {
    const runEvent: SSEEventData = {
      type: "run",
      data: {
        event_id: "event-run",
        session_id: "session-1",
        run_id: "run-1",
        status: "running",
        goal: "Research",
        usage: {},
      },
    };
    mocks.getSessionDetail.mockResolvedValue(createSession([runEvent]));
    mocks.getRun.mockResolvedValue({
      id: "run-1",
      session_id: "session-1",
      mode: "research_team",
      status: "partial",
      goal: "Research",
      usage: {
        llm_calls: 1,
        tool_calls: 1,
        input_tokens: 10,
        output_tokens: 10,
        total_tokens: 20,
        worker_attempts: 1,
        elapsed_ms: 50,
      },
      error: null,
    });
    mocks.getRunTasks.mockResolvedValue([]);
    mocks.getRunSources.mockResolvedValue([
      {
        id: "source-1",
        run_id: "run-1",
        canonical_url: "https://example.com/source",
        original_url: "https://example.com/source",
        title: "Source",
        domain: "example.com",
        publisher: null,
        published_at: null,
        retrieved_at: "2026-07-10T00:00:00Z",
        content_type: "text/html",
        content_hash: "hash",
        source_class: "primary",
        metadata: {},
      },
    ]);

    const { result } = renderHook(() => useSessionDetail("session-1"));

    await waitFor(() => {
      expect(result.current.researchRun?.run?.status).toBe("partial");
    });
    expect(Object.keys(result.current.researchRun?.sources ?? {})).toEqual([
      "source-1",
    ]);
    expect(mocks.getRunTasks).toHaveBeenCalledWith("session-1", "run-1");
    expect(mocks.getRunSources).toHaveBeenCalledWith("session-1", "run-1");
  });
});
