import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import CommandPalette, { matching, type Command } from "@/components/CommandPalette";
import { UNTAGGED, filterDevices } from "@/components/DeviceFleet";
import StatusStrip from "@/components/StatusStrip";
import type { DeviceConfig } from "@/lib/types";
import { readFilters, writeFilters } from "@/lib/urlState";
import { mockApi } from "./api-mock";
import { reducedMotion } from "./media";

const noop = () => {};

const device = (over: Partial<DeviceConfig>): DeviceConfig =>
  ({
    id: "laptop",
    name: "Work Laptop",
    tags: [],
    source: "store",
    token: "t",
    dot_hostname: "",
    doh_url: "",
    json_url: "",
    mobileconfig_url: "",
    clients: {},
    rules: [],
    safe_search: false,
    safe_search_skip: [],
    block_mode: "zero",
    block_address: "",
    safe_search_covers: [],
    activity: null,
    ...over,
  }) as DeviceConfig;

const fleet = [
  device({ id: "laptop", name: "Work Laptop", tags: ["work"] }),
  device({ id: "pixel", name: "Pixel", tags: ["personal", "kids"] }),
  device({ id: "tv", name: "Living room TV", tags: [] }),
];

describe("device filtering", () => {
  it("matches on name, id and tag", () => {
    expect(filterDevices(fleet, "pixel", "").map((d) => d.id)).toEqual(["pixel"]);
    expect(filterDevices(fleet, "laptop", "").map((d) => d.id)).toEqual(["laptop"]);
    expect(filterDevices(fleet, "kids", "").map((d) => d.id)).toEqual(["pixel"]);
  });

  it("narrows to one tag", () => {
    expect(filterDevices(fleet, "", "work").map((d) => d.id)).toEqual(["laptop"]);
  });

  it("finds the devices nobody has tagged", () => {
    expect(filterDevices(fleet, "", UNTAGGED).map((d) => d.id)).toEqual(["tv"]);
  });

  it("combines a search with a tag", () => {
    expect(filterDevices(fleet, "pix", "kids").map((d) => d.id)).toEqual(["pixel"]);
    expect(filterDevices(fleet, "laptop", "kids")).toEqual([]);
  });

  it("returns everything when nothing is asked for", () => {
    expect(filterDevices(fleet, "", "")).toHaveLength(3);
  });
});

describe("url filters", () => {
  it("round-trips through the query string without touching the hash", () => {
    window.history.replaceState(null, "", "/#devices");
    writeFilters("log", ["device", "result"], { device: "laptop", result: "blocked" });

    expect(window.location.hash).toBe("#devices");
    expect(readFilters("log", ["device", "result"])).toEqual({
      device: "laptop",
      result: "blocked",
    });
  });

  it("drops a key once it is cleared", () => {
    window.history.replaceState(null, "", "/");
    writeFilters("log", ["device"], { device: "laptop" });
    writeFilters("log", ["device"], { device: "" });

    expect(readFilters("log", ["device"])).toEqual({});
    expect(window.location.search).toBe("");
  });

  it("keeps two sections' filters apart", () => {
    window.history.replaceState(null, "", "/");
    writeFilters("log", ["q"], { q: "ads" });
    writeFilters("devices", ["q"], { q: "pixel" });

    expect(readFilters("log", ["q"])).toEqual({ q: "ads" });
    expect(readFilters("devices", ["q"])).toEqual({ q: "pixel" });
  });
});

describe("command palette", () => {
  const commands: Command[] = [
    { id: "a", label: "Overview", group: "Go to", hint: "pooled allowance", run: noop },
    { id: "b", label: "Query log", group: "Go to", hint: "what the network asked", run: noop },
    { id: "c", label: "Toggle theme", group: "Action", run: noop },
  ];

  it("prefers a name that starts with what was typed", () => {
    expect(matching(commands, "over")[0].label).toBe("Overview");
  });

  it("falls back to the hint", () => {
    expect(matching(commands, "allowance").map((c) => c.label)).toEqual(["Overview"]);
  });

  it("returns everything for an empty query", () => {
    expect(matching(commands, "")).toHaveLength(3);
  });

  it("returns nothing when nothing matches", () => {
    expect(matching(commands, "zzzz")).toEqual([]);
  });

  it("stays closed until the shortcut is pressed", async () => {
    const user = userEvent.setup();
    render(<CommandPalette commands={commands} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("runs the highlighted command on Enter", async () => {
    const user = userEvent.setup();
    let ran = "";
    render(
      <CommandPalette
        commands={[{ id: "x", label: "Devices", group: "Go to", run: () => (ran = "devices") }]}
      />,
    );

    await user.keyboard("{Control>}k{/Control}");
    await user.keyboard("dev{Enter}");

    expect(ran).toBe("devices");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("status strip", () => {
  it("says nominal when nothing is wrong", async () => {
    reducedMotion();
    mockApi({ "/api/health": { status: "ok", uptime_s: 10, attention: [], warnings: [] } });

    render(<StatusStrip />);

    expect(await screen.findByText("Nominal")).toBeInTheDocument();
  });

  it("counts what wants attention and lists it when opened", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      "/api/health": {
        status: "degraded",
        uptime_s: 10,
        attention: ["alpha cannot sign in to AdGuard"],
        warnings: [],
      },
    });

    render(<StatusStrip />);
    await user.click(await screen.findByRole("button", { name: /1 thing/ }));

    expect(screen.getByText("alpha cannot sign in to AdGuard")).toBeInTheDocument();
  });
});
