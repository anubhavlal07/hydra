import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Devices from "@/components/Devices";
import { mockApi } from "./api-mock";
import { devices, health, nativeDns, rewrites } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const routes = (over: Record<string, unknown> = {}) => ({
  "/api/devices": devices(),
  "/api/health": health(),
  "/api/rewrites": rewrites(),
  "/api/native-dns": nativeDns(),
  ...over,
});

describe("Devices", () => {
  it("shows both addresses for a device, unabridged", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    const token = await screen.findByText(
      "https://api.dns.example/dns-query/abcdefgh12345678",
    );
    expect(token).toBeInTheDocument();
    expect(token).toHaveClass("wrap-anywhere");
    expect(screen.getByText("abcdefgh12345678.dot.example")).toBeInTheDocument();
  });

  it("shows the per-device hourly budget the resolver now reports", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText("Upstream budget this hour")).toBeInTheDocument();
    expect(screen.getByText("600 / 2,000")).toBeInTheDocument();
  });

  it("badges each card with this month's queries and switches every card at once", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText("310 queries")).toBeInTheDocument();
    expect(screen.getByText(/40 blocked · 120 from cache/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "All time" }));
    expect(screen.getByText("4,200 queries")).toBeInTheDocument();
    expect(screen.getByText(/900 blocked · 1,800 from cache/)).toBeInTheDocument();
  });

  it("leaves the budget out when no ceiling is set", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/health": health({
          budget: { enabled: false, hourly: 0, denied: 0, devices: [] },
        }),
      }),
    );

    render(<Devices onUnauthorized={noop} />);

    await screen.findByText("abcdefgh12345678.dot.example");
    expect(screen.queryByText("Upstream budget this hour")).not.toBeInTheDocument();
  });

  it("says the list is empty when there are no devices", async () => {
    reducedMotion();
    mockApi(routes({ "/api/devices": devices({ devices: [] }) }));

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText("No devices yet. Add one above to get a URL.")).toBeInTheDocument();
  });

  it("warns loudly when the resolver answers without a token", async () => {
    reducedMotion();
    mockApi(routes({ "/api/devices": devices({ anonymous_allowed: true }) }));

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText(/Anonymous access is currently enabled/)).toBeInTheDocument();
  });

  it("names the device it is adding and shows the URLs it got back", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(
      routes({ "POST /api/devices": { ...devices().devices[0], name: "Pixel 9" } }),
    );

    render(<Devices onUnauthorized={noop} />);
    await user.type(await screen.findByLabelText("Device name"), "Pixel 9");
    await user.click(screen.getByRole("button", { name: /Add device/ }));

    expect(api.calls.find((call) => call.method === "POST")?.body).toEqual({ name: "Pixel 9" });
    expect(await screen.findByText("Pixel 9 added")).toBeInTheDocument();
  });

  it("lists names answered locally and the account fallbacks", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText("nas.home")).toBeInTheDocument();
    expect(screen.getByText("192.168.1.5")).toBeInTheDocument();
    expect(screen.getByText("alpha.dns.adguard.com")).toBeInTheDocument();
    expect(screen.getByText("most headroom")).toBeInTheDocument();
  });
});

describe("Devices on a phone", () => {
  it("is one row per device, with its tags and traffic beneath the name", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /Work Laptop/ });
    expect(row).toHaveAttribute("data-lines", "2");
    expect(within(row).getByText("work · 310 queries")).toBeInTheDocument();
  });

  it("counts this month until all time is asked for, and remembers the choice", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    const { unmount } = render(<Devices onUnauthorized={noop} />);
    const row = await screen.findByRole("button", { name: /Work Laptop/ });
    expect(within(row).getByText("work · 310 queries")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "All time" }));
    expect(within(row).getByText("work · 4,200 queries")).toBeInTheDocument();
    expect(localStorage.getItem("hydra-period")).toBe("all");

    unmount();
    render(<Devices onUnauthorized={noop} />);
    const again = await screen.findByRole("button", { name: /Work Laptop/ });
    expect(within(again).getByText("work · 4,200 queries")).toBeInTheDocument();
  });

  it("falls back to the lifetime count when the resolver sends no month", async () => {
    phoneWidth();
    const base = devices();
    const activity = { ...base.devices[0].activity!, month: undefined };
    mockApi(
      routes({
        "/api/devices": devices({ devices: [{ ...base.devices[0], activity }] }),
      }),
    );

    render(<Devices onUnauthorized={noop} />);
    const row = await screen.findByRole("button", { name: /Work Laptop/ });
    expect(within(row).getByText("work · 4,200 queries")).toBeInTheDocument();
  });

  it("drills into a device for its addresses, rules and delete", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    await waitFor(() => expect(window.location.hash).toBe("#devices/laptop"));
    expect(screen.getByRole("heading", { name: "Work Laptop" })).toBeInTheDocument();
    expect(
      screen.getByText("https://api.dns.example/dns-query/abcdefgh12345678"),
    ).toBeInTheDocument();
    expect(screen.getByText("abcdefgh12345678.dot.example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete device/ })).toBeInTheDocument();
  });

  it("edits a device's rules on a subscreen, saving from the header", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi(routes({ "PUT /api/devices/laptop/rules": { ok: true } }));

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));
    await user.click(await screen.findByRole("button", { name: /^Rules/ }));

    await waitFor(() => expect(window.location.hash).toBe("#devices/laptop/rules"));

    const editor = screen.getByLabelText("Rules for Work Laptop");
    await user.type(editor, "\nads.example");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.calls.some((call) => call.path === "/api/devices/laptop/rules")).toBe(true),
    );
  });

  it("adds a device through a sheet rather than an inline form", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi(
      routes({ "POST /api/devices": { ...devices().devices[0], name: "Pixel 9" } }),
    );

    render(<Devices onUnauthorized={noop} />);
    expect(screen.queryByLabelText("Device name")).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Add device" }));
    const sheet = await screen.findByRole("dialog");

    await user.type(within(sheet).getByLabelText("Device name"), "Pixel 9");
    await user.click(within(sheet).getByRole("button", { name: /Add device/ }));

    expect(api.calls.find((call) => call.method === "POST")?.body).toEqual({ name: "Pixel 9" });
    expect(await screen.findByText("Pixel 9 added")).toBeInTheDocument();
  });

  it("keeps the empty state and the loud warnings at phone width", async () => {
    phoneWidth();
    mockApi(
      routes({
        "/api/devices": devices({ devices: [], anonymous_allowed: true }),
      }),
    );

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText(/Anonymous access is currently enabled/)).toBeInTheDocument();
    expect(screen.getByText("No devices yet. Add one to get a URL.")).toBeInTheDocument();
  });

  it("shows the default ceiling until the device is given one of its own", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi(
      routes({ "PUT /api/devices/laptop/budget": { ...devices().devices[0], hourly_budget: 500 } }),
    );

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    const row = await screen.findByRole("button", { name: /Hourly ceiling/ });
    expect(within(row).getByText("the default for every device")).toBeInTheDocument();
    expect(within(row).getByText("2,000")).toBeInTheDocument();

    await user.click(row);
    const sheet = await screen.findByRole("dialog");
    await user.type(within(sheet).getByLabelText("Hourly ceiling"), "500");
    await user.click(within(sheet).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        hourly_budget: 500,
      }),
    );
  });

  it("measures a device against its own ceiling, not the default", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(
      routes({
        "/api/devices": devices({
          devices: [{ ...devices().devices[0], id: "tv", name: "Living room TV" }],
        }),
      }),
    );

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Living room TV/ }));

    expect(await screen.findByText("500 / 500")).toBeInTheDocument();
  });

  it("offers to forget one device's stored history, and asks first", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi(
      routes({ "DELETE /api/query-log": { removed: 1840, device: "laptop" } }),
    );

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));
    await user.click(await screen.findByRole("button", { name: /Forget its query history/ }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Forget history" }));

    await waitFor(() =>
      expect(
        api.calls.find((call) => call.method === "DELETE")?.search,
      ).toContain("device=laptop"),
    );
    expect(await screen.findByText("1,840 rows dropped")).toBeInTheDocument();
  });

  it("puts rewrites, import and the fallback behind their own rows", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: /Answered here/ }));
    await waitFor(() => expect(window.location.hash).toBe("#devices/rewrites"));
    expect(await screen.findByText("nas.home")).toBeInTheDocument();
  });
});

describe("What the dashboard is told, not what it assumes", () => {
  it("names the engines safe search covers from the payload, switched off too", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/devices": devices({
          devices: [
            {
              ...devices().devices[0],
              safe_search: false,
              safe_search_covers: ["google", "youtube", "bing", "duckduckgo", "pixiv"],
            },
          ],
        }),
      }),
    );

    render(<Devices onUnauthorized={noop} />);

    expect(
      await screen.findByText(/google, youtube, bing, duckduckgo, pixiv/),
    ).toBeInTheDocument();
  });

  it("shows a client the resolver reports even when it has no copy for it", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/devices": devices({
          devices: [
            {
              ...devices().devices[0],
              clients: {
                ...devices().devices[0].clients,
                freebox: { path: "Somewhere in the router", value: "tls://hydra.example" },
              },
            },
          ],
        }),
      }),
    );

    const user = userEvent.setup();
    render(<Devices onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: "Setup instructions" }));

    expect(await screen.findByRole("button", { name: "freebox" })).toBeInTheDocument();
  });
});

describe("pausing a device", () => {
  it("offers spans to pause for, and calls the resolver with the one picked", async () => {
    reducedMotion();
    const api = mockApi(routes());

    render(<Devices onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    await userEvent.click(await screen.findByRole("button", { name: "Pause filtering for 1h" }));

    await waitFor(() =>
      expect(
        api.calls.some(
          (call) =>
            call.method === "POST" &&
            call.path === "/api/devices/laptop/pause" &&
            (call.body as { minutes: number }).minutes === 60,
        ),
      ).toBe(true),
    );
  });

  it("says when filtering comes back, and offers to bring it back sooner", async () => {
    reducedMotion();
    const paused = devices();
    paused.devices[0].paused_until = 1_700_003_600;
    const api = mockApi({ ...routes(), "/api/devices": paused });

    render(<Devices onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    expect(await screen.findByText(/Filtering paused/)).toBeInTheDocument();
    expect(screen.getByText(/resumes at/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause filtering for 15m" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Resume now" }));

    await waitFor(() =>
      expect(
        api.calls.some(
          (call) => call.method === "DELETE" && call.path === "/api/devices/laptop/pause",
        ),
      ).toBe(true),
    );
  });
});

describe("safe search per platform", () => {
  it("lets one platform out of safe search and keeps the rest", async () => {
    reducedMotion();
    const listed = devices();
    listed.devices[0].safe_search_covers = ["google", "youtube"];
    const api = mockApi({
      ...routes(),
      "/api/devices": listed,
      "PUT /api/devices/laptop/policy": listed.devices[0],
    });

    render(<Devices onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    const youtube = await screen.findByRole("switch", { name: /YouTube/ });
    expect(youtube).toBeChecked();
    expect(screen.getByRole("switch", { name: /Google/ })).toBeChecked();

    await userEvent.click(youtube);

    await waitFor(() =>
      expect(
        api.calls.find(
          (call) => call.method === "PUT" && call.path === "/api/devices/laptop/policy",
        )?.body,
      ).toMatchObject({ safe_search: true, safe_search_skip: ["youtube"] }),
    );
  });
});
