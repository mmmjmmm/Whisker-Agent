import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ChatInput } from "@/components/chat-input";


it("exposes mode selection and locks it while running", () => {
  const onModeChange = vi.fn();
  const { rerender } = render(
    <ChatInput
      mode="react"
      onModeChange={onModeChange}
      isRunning={false}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "多 Agent" }));
  expect(onModeChange).toHaveBeenCalledWith("team");

  rerender(
    <ChatInput
      mode="team"
      onModeChange={onModeChange}
      isRunning={true}
    />,
  );
  expect(screen.getByRole("button", { name: "单 Agent" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "多 Agent" })).toBeDisabled();
});
