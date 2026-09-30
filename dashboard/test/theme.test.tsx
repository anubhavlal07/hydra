import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import More from "@/components/More";
import { mockApi } from "./api-mock";
import { health } from "./fixtures";
import { phoneWidth } from "./media";

const noop = () => {};

const routes = () => ({ "/api/health": health() });

const root = () => document.documentElement;

const chrome = () => document.getElementById("hy-chrome")?.getAttribute("content") ?? null;

const at = (hash: string) => window.history.replaceState(null, "", hash);

const more = () => render(<More onSignOut={noop} onUnauthorized={noop} />);

describe("Theme", () => {
  beforeEach(() => {
    delete root().dataset.theme;
    delete root().dataset.black;
    document.getElementById("hy-chrome")?.remove();
  });

  it("pins a pure black palette for an OLED screen, and the browser chrome with it", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());
    at("#more/theme");

    more();

    const knob = screen.getByRole("switch", { name: "Pure black" });
    expect(knob).not.toBeChecked();

    await user.click(knob);

    await waitFor(() => expect(root().dataset.black).toBe("true"));
    expect(localStorage.getItem("hydra-black")).toBe("1");
    expect(chrome()).toBe("#000000");

    await user.click(screen.getByRole("switch", { name: "Pure black" }));

    await waitFor(() => expect(root().dataset.black).toBeUndefined());
    expect(localStorage.getItem("hydra-black")).toBeNull();
    expect(chrome()).toBeNull();
  });

  it("keeps the black palette remembered under a pinned light theme", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());
    at("#more/theme");

    more();

    await user.click(screen.getByRole("switch", { name: "Pure black" }));
    await waitFor(() => expect(root().dataset.black).toBe("true"));

    await user.click(screen.getByRole("button", { name: /Light/ }));

    await waitFor(() => expect(root().dataset.theme).toBe("light"));
    expect(localStorage.getItem("hydra-black")).toBe("1");
  });

  it("says on the Theme row which palette is in force", async () => {
    phoneWidth();
    localStorage.setItem("hydra-theme", "dark");
    localStorage.setItem("hydra-black", "1");
    mockApi(routes());
    at("#more");

    more();

    expect(
      within(screen.getByRole("button", { name: /Theme/ })).getByText("Dark · black"),
    ).toBeInTheDocument();
  });

  it("leaves the black mark off the Theme row while light is pinned", async () => {
    phoneWidth();
    localStorage.setItem("hydra-theme", "light");
    localStorage.setItem("hydra-black", "1");
    mockApi(routes());
    at("#more");

    more();

    const row = screen.getByRole("button", { name: /Theme/ });
    expect(within(row).getByText("Light")).toBeInTheDocument();
    expect(row.textContent).not.toContain("black");
  });
});
