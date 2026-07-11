import { describe, expect, it } from "vitest";

describe("frontend test support", () => {
  it("uses jsdom", () => {
    expect(document.createElement("div")).toBeInstanceOf(HTMLElement);
  });
});
