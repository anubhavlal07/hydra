import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { watchKeyboard } from "@/lib/keyboard";
import { phoneWidth, reducedMotion } from "./media";

let stop: (() => void) | null = null;

const start = () => {
  stop = watchKeyboard();
};

afterEach(() => {
  stop?.();
  stop = null;
});

const Form = () => (
  <form>
    <label htmlFor="rules">Rules</label>
    <textarea id="rules" />
    <button type="submit">Save</button>
  </form>
);

describe("on-screen keyboard", () => {
  it("marks the document while a field is being typed into on a phone", async () => {
    phoneWidth();
    const user = userEvent.setup();
    start();

    render(<Form />);
    expect(document.documentElement.dataset.keyboard).toBeUndefined();

    await user.click(screen.getByLabelText("Rules"));
    expect(document.documentElement.dataset.keyboard).toBe("true");
  });

  it("lets go once focus leaves every field", async () => {
    phoneWidth();
    const user = userEvent.setup();
    start();

    render(<Form />);
    await user.click(screen.getByLabelText("Rules"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(document.documentElement.dataset.keyboard).toBeUndefined();
  });

  it("stays out of the way above phone width, where there is no keyboard to dodge", async () => {
    reducedMotion();
    const user = userEvent.setup();
    start();

    render(<Form />);
    await user.click(screen.getByLabelText("Rules"));

    expect(document.documentElement.dataset.keyboard).toBeUndefined();
  });

  it("ignores a button, which opens no keyboard", async () => {
    phoneWidth();
    const user = userEvent.setup();
    start();

    render(<Form />);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(document.documentElement.dataset.keyboard).toBeUndefined();
  });
});
