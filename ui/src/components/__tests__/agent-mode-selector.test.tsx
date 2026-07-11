import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { AgentModeSelector } from "@/components/agent-mode-selector";


it("switches from react to team", () => {
  const onChange = vi.fn();
  render(
    <AgentModeSelector value="react" onChange={onChange} disabled={false} />,
  );

  fireEvent.click(screen.getByRole("button", { name: "多 Agent" }));

  expect(onChange).toHaveBeenCalledWith("team");
  expect(screen.getByRole("button", { name: "单 Agent" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});


it("disables both mode choices while a task is running", () => {
  render(
    <AgentModeSelector value="team" onChange={vi.fn()} disabled={true} />,
  );

  expect(screen.getByRole("button", { name: "单 Agent" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "多 Agent" })).toBeDisabled();
});
