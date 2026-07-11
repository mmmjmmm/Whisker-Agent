import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionDetail } from "@/hooks/use-session-detail";
import { sessionApi } from "@/lib/api/session";
import type { SessionDetail } from "@/lib/api/types";

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionDetail: vi.fn(),
    getSessionFiles: vi.fn(),
    chat: vi.fn(),
  },
}));

const runningSession: SessionDetail = {
  session_id: "session-1",
  title: "team",
  latest_message: "work",
  latest_message_at: "",
  status: "running",
  unread_message_count: 0,
  events: [],
};

describe("useSessionDetail team stream recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("refreshes server state instead of unlocking after a transport error", async () => {
    let sendError: ((error: Error) => void) | undefined;
    vi.mocked(sessionApi.getSessionDetail).mockResolvedValue(runningSession);
    vi.mocked(sessionApi.getSessionFiles).mockResolvedValue([]);
    vi.mocked(sessionApi.chat).mockImplementation(
      (_sessionId, params, _onEvent, onError) => {
        if (params.message) sendError = onError;
        return vi.fn();
      },
    );

    const { result } = renderHook(() => useSessionDetail("session-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.sendMessage("new", [], "team");
    });
    act(() => {
      sendError?.(new Error("network unavailable"));
    });

    await waitFor(() => {
      expect(sessionApi.getSessionDetail).toHaveBeenCalledTimes(2);
      expect(result.current.session?.status).toBe("running");
    });
  });
});
