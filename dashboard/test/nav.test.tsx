import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BackButton, ScreenHeader } from "@/components/ui";
import { parseRoute, routeHash, useNav, writeRoute } from "@/lib/nav";

function Probe() {
  const { route, go, open, back } = useNav();

  return (
    <div>
      <span data-testid="where">{`${route.section}:${route.path.join("/")}`}</span>
      <button type="button" onClick={() => go("devices")}>
        Devices
      </button>
      <button type="button" onClick={() => open("laptop")}>
        Open laptop
      </button>
      <BackButton label="Back to Devices" onClick={back} />
    </div>
  );
}

describe("routes", () => {
  it("reads a section and its subscreen path out of the hash", () => {
    expect(parseRoute("#devices")).toEqual({ section: "devices", path: [] });
    expect(parseRoute("#devices/laptop")).toEqual({ section: "devices", path: ["laptop"] });
    expect(parseRoute("")).toEqual({ section: "", path: [] });
  });

  it("round-trips an id that needs escaping", () => {
    expect(parseRoute(routeHash("accounts", ["a@b.com"]))).toEqual({
      section: "accounts",
      path: ["a@b.com"],
    });
  });
});

describe("drill-down navigation", () => {
  it("pushes a subscreen and pops back to its parent, keeping history in step", async () => {
    const user = userEvent.setup();
    render(<Probe />);

    await user.click(screen.getByRole("button", { name: "Devices" }));
    expect(window.location.hash).toBe("#devices");
    expect(screen.getByTestId("where")).toHaveTextContent("devices:");

    await user.click(screen.getByRole("button", { name: "Open laptop" }));
    expect(window.location.hash).toBe("#devices/laptop");
    expect(screen.getByTestId("where")).toHaveTextContent("devices:laptop");

    await user.click(screen.getByRole("button", { name: "Back to Devices" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(window.location.hash).toBe("#devices");
  });

  it("falls back to the parent route when there is no history to pop", async () => {
    writeRoute("devices", ["laptop"], true);
    render(<Probe />);

    expect(screen.getByTestId("where")).toHaveTextContent("devices:laptop");

    await userEvent.setup().click(screen.getByRole("button", { name: "Back to Devices" }));
    expect(window.location.hash).toBe("#devices");
  });
});

describe("ScreenHeader", () => {
  it("renders its own title when no shell slot is offered", () => {
    render(<ScreenHeader title="Work Laptop" actions={<button type="button">Edit</button>} />);

    expect(screen.getByRole("heading", { name: "Work Laptop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });
});
