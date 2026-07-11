import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatInput } from "@/components/chat-input";

describe("ChatInput agent mode", () => {
  it("sends the selected mode without persisting it to a session", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<ChatInput onSend={onSend} researchTeamEnabled />);

    await user.click(screen.getByRole("radio", { name: "研究团队" }));
    await user.type(screen.getByRole("textbox"), "compare agents");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onSend).toHaveBeenCalledWith(
      "compare agents",
      [],
      "research_team",
    );
  });

  it("prevents mode changes while a run is active", () => {
    render(<ChatInput isRunning researchTeamEnabled />);

    expect(screen.getByRole("radio", { name: "单 Agent" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "研究团队" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "停止" })).toBeVisible();
  });
});
