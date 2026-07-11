import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AgentModeControl } from "@/components/agent-mode-control";

describe("AgentModeControl", () => {
  it("disables research mode when backend capability is off", () => {
    render(
      <AgentModeControl
        value="react"
        onChange={vi.fn()}
        researchTeamEnabled={false}
      />,
    );

    expect(screen.getByRole("radio", { name: "研究团队" })).toBeDisabled();
  });

  it("selects research mode when available", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <AgentModeControl
        value="react"
        onChange={onChange}
        researchTeamEnabled
      />,
    );

    await user.click(screen.getByRole("radio", { name: "研究团队" }));

    expect(onChange).toHaveBeenCalledWith("research_team");
  });
});
