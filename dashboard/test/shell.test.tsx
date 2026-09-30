import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Shell from "@/components/Shell";
import { mockApi } from "./api-mock";
import {
  accounts,
  health,
  overview,
  resolverDetail,
  statsTimeline,
} from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const routes = () => ({
  "/api/overview": overview(),
  "/api/resolver": resolverDetail(),
  "/api/health": health(),
  "/api/stats/timeline": statsTimeline(),
  "/api/accounts": accounts(),
});

const bar = () => screen.getByRole("navigation", { name: "Sections" });

describe("Shell navigation", () => {
  it("offers seven sidebar entries above phone width", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);

    const labels = within(bar())
      .getAllByRole("button")
      .map((button) => button.textContent);

    expect(labels).toEqual([
      "Overview",
      "Accounts",
      "Log",
      "Devices",
      "Rules",
      "Audit",
      "Tools",
      "Theme",
      "Sign out",
    ]);
  });

  it("offers five destinations on a phone, with the rest under More", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);

    const labels = within(bar())
      .getAllByRole("button")
      .map((button) => button.textContent);

    expect(labels).toEqual(["Overview", "Devices", "Log", "Rules", "More"]);
  });

  it("drills from More into a section and comes back to More", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);

    await user.click(within(bar()).getByRole("button", { name: "More" }));
    expect(window.location.hash).toBe("#more");

    await user.click(await screen.findByRole("button", { name: /Accounts/ }));
    await waitFor(() => expect(window.location.hash).toBe("#accounts"));

    const back = screen.getByRole("button", { name: "Back to More" });
    expect(within(bar()).getByRole("button", { name: "More" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await user.click(back);
    await waitFor(() => expect(window.location.hash).toBe("#more"));
  });

  it("keeps every phone nav label at least as large as the body text", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);

    for (const button of within(bar()).getAllByRole("button")) {
      expect(button.className).toContain("max-wide:text-xs");
      expect(button.className).toContain("max-wide:min-h-14");
    }
  });

  it("moves theme and sign out into More, with a confirm before signing out", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);
    await user.click(within(bar()).getByRole("button", { name: "More" }));

    await user.click(await screen.findByRole("button", { name: /Theme/ }));
    await waitFor(() => expect(window.location.hash).toBe("#more/theme"));
    expect(screen.getByRole("heading", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Match system/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back to More" }));
    await waitFor(() => expect(window.location.hash).toBe("#more"));

    await user.click(await screen.findByRole("button", { name: /Sign out/ }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("puts you back where you were when a subscreen is popped", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);
    await user.click(within(bar()).getByRole("button", { name: "More" }));

    Object.defineProperty(window, "scrollY", { configurable: true, value: 420 });

    await user.click(await screen.findByRole("button", { name: /About/ }));
    await waitFor(() => expect(window.location.hash).toBe("#more/about"));

    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    (window.scrollTo as unknown as ReturnType<typeof vi.fn>).mockClear();

    await user.click(screen.getByRole("button", { name: "Back to More" }));
    await waitFor(() => expect(window.location.hash).toBe("#more"));

    expect(window.scrollTo).toHaveBeenCalledWith({ top: 420, behavior: "auto" });
  });

  it("goes to the top when a tab is switched rather than popped", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);
    await user.click(within(bar()).getByRole("button", { name: "More" }));

    Object.defineProperty(window, "scrollY", { configurable: true, value: 420 });
    (window.scrollTo as unknown as ReturnType<typeof vi.fn>).mockClear();

    await user.click(within(bar()).getByRole("button", { name: "Devices" }));
    await waitFor(() => expect(window.location.hash).toBe("#devices"));

    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
  });

  it("leaves theme and sign out off the phone header", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Shell onSignedOut={noop} />);

    expect(screen.queryByRole("button", { name: "Toggle theme" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
