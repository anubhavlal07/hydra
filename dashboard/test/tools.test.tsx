import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Tools from "@/components/Tools";
import { mockApi } from "./api-mock";
import {
  alertStatus,
  backup,
  digestStatus,
  lookupResult,
  tokenStore,
  topRows,
} from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const routes = (over: Record<string, unknown> = {}) => ({
  "/api/stats/top": topRows(),
  "/api/tokens": { tokens: [], store: tokenStore(), scopes: ["read", "metrics"] },
  "/api/alerts": { alerts: alertStatus(), webhook_set: false, digest: digestStatus() },
  ...over,
});

describe("Tools", () => {
  it("resolves a name through the real path and shows the records", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(routes({ "POST /api/lookup": lookupResult() }));

    render(<Tools onUnauthorized={noop} />);
    await user.type(screen.getByLabelText("Domain name"), "example.com");
    await user.click(screen.getByRole("button", { name: /Resolve/ }));

    expect(api.calls.find((call) => call.method === "POST")?.body).toEqual({
      name: "example.com",
      type: "A",
      dnssec: false,
    });
    expect(await screen.findByText("93.184.216.34")).toBeInTheDocument();
    expect(screen.getByText("via alpha")).toBeInTheDocument();
  });

  it("says when a lookup returned nothing", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi(
      routes({
        "POST /api/lookup": lookupResult({
          answer: { StatusName: "NXDOMAIN", Answer: [], Authority: [] },
          rcode: "NXDOMAIN",
        }),
      }),
    );

    render(<Tools onUnauthorized={noop} />);
    await user.type(screen.getByLabelText("Domain name"), "nothing.example");
    await user.click(screen.getByRole("button", { name: /Resolve/ }));

    expect(await screen.findByText("No records returned.")).toBeInTheDocument();
  });

  it("ranks what AdGuard saw, and says when it saw nothing", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByText("doubleclick.net")).toBeInTheDocument();
    expect(screen.getByText("310")).toBeInTheDocument();
  });

  it("says when AdGuard reported nothing for the window", async () => {
    reducedMotion();
    mockApi(routes({ "/api/stats/top": { rows: [], errors: {} } }));

    render(<Tools onUnauthorized={noop} />);

    expect(
      await screen.findByText("AdGuard reported nothing for this window."),
    ).toBeInTheDocument();
  });

  it("asks before clearing the cache, then reports how much went", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(routes({ "POST /api/cache/clear": { cleared: 4210 } }));

    render(<Tools onUnauthorized={noop} />);
    await user.click(screen.getByRole("button", { name: "Clear cache" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls.some((call) => call.path === "/api/cache/clear")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Clear cache" }));
    expect(await screen.findByText("4,210 dropped")).toBeInTheDocument();
  });

  it("leaves the cache alone when the confirm is dismissed", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(routes({ "POST /api/cache/clear": { cleared: 4210 } }));

    render(<Tools onUnauthorized={noop} />);
    await user.click(screen.getByRole("button", { name: "Clear cache" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(api.calls.some((call) => call.path === "/api/cache/clear")).toBe(false);
  });

  it("downloads a backup through the real endpoint", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(routes({ "/api/backup": backup() }));

    render(<Tools onUnauthorized={noop} />);
    await user.click(screen.getByRole("button", { name: /Download/ }));

    expect(await screen.findByText(/1 devices, 1 accounts/)).toBeInTheDocument();
    expect(api.calls.some((call) => call.path === "/api/backup")).toBe(true);
  });

  it("leaves the credentials out when asked to", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(routes({ "/api/backup": backup({ accounts: [] }) }));

    render(<Tools onUnauthorized={noop} />);
    await user.click(screen.getByRole("switch", { name: /Include credentials/ }));
    await user.click(screen.getByRole("button", { name: /Download/ }));

    await screen.findByText(/no credentials/);
    expect(api.calls.find((call) => call.path === "/api/backup")?.search).toContain(
      "accounts=false",
    );
  });

  it("says machine tokens need the registry before offering to make one", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/tokens": { tokens: [], store: tokenStore({ configured: false }), scopes: [] },
      }),
    );

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByText(/HYDRA_STORE_URL/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument();
  });

  it("shows a new token once, and says that is the only time", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi(
      routes({
        "POST /api/tokens": {
          token: { id: "abcd1234", name: "prometheus", scope: "metrics" },
          secret: "hyd_secretvalue",
        },
      }),
    );

    render(<Tools onUnauthorized={noop} />);
    await user.type(await screen.findByLabelText("Token name"), "prometheus");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("hyd_secretvalue")).toBeInTheDocument();
    expect(screen.getByText(/not shown again/)).toBeInTheDocument();
  });

  it("offers the scopes the resolver reports, not a list of its own", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(
      routes({
        "/api/tokens": {
          tokens: [],
          store: tokenStore({ scopes: ["read", "metrics", "admin"] }),
          scopes: ["read", "metrics", "admin"],
        },
        "POST /api/tokens": {
          token: { id: "abcd1234", name: "ops", scope: "admin" },
          secret: "hyd_secretvalue",
        },
      }),
    );

    render(<Tools onUnauthorized={noop} />);
    await screen.findByLabelText("Token name");

    expect(screen.getByRole("radio", { name: "Admin" })).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Admin" }));
    await user.type(screen.getByLabelText("Token name"), "ops");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "POST")?.body as { scope: string }).scope,
      ).toBe("admin"),
    );
  });

  it("says nothing is alerting until a webhook is set", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByText(/HYDRA_ALERT_WEBHOOK/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send a test alert/ })).not.toBeInTheDocument();
  });

  it("offers every export the API can stream", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Tools onUnauthorized={noop} />);

    expect(screen.getByRole("link", { name: "Live log, CSV" })).toHaveAttribute(
      "href",
      expect.stringContaining("source=live&format=csv"),
    );
    expect(screen.getByRole("link", { name: "Query history, CSV" })).toHaveAttribute(
      "href",
      expect.stringContaining("source=history"),
    );
    expect(screen.getByRole("link", { name: "Devices, CSV" })).toBeInTheDocument();
  });
});

describe("Tools on a phone", () => {
  it("hands a bare Tools route back to More rather than listing it twice", async () => {
    phoneWidth();
    window.history.replaceState(null, "", "#tools");
    mockApi(routes());

    render(<Tools onUnauthorized={noop} />);

    await waitFor(() => expect(window.location.hash).toBe("#more"));
    expect(screen.queryByRole("heading", { name: "Diagnostics" })).not.toBeInTheDocument();
  });

  it("opens one tool at a time, with its own title", async () => {
    phoneWidth();
    const user = userEvent.setup();
    window.history.replaceState(null, "", "#tools/lookup");
    mockApi(routes({ "POST /api/lookup": lookupResult() }));

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByRole("heading", { name: "Lookup" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Domain name"), "example.com");
    await user.click(screen.getByRole("button", { name: /Resolve/ }));

    expect(await screen.findByText("93.184.216.34")).toBeInTheDocument();
  });

  it("still keeps every export reachable at phone width", async () => {
    phoneWidth();
    window.history.replaceState(null, "", "#tools/exports");
    mockApi(routes());

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByRole("link", { name: "Live log, CSV" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Devices, CSV" })).toBeInTheDocument();
  });
});

describe("the weekly digest", () => {
  it("says when the next one lands and offers to send one now", async () => {
    reducedMotion();
    const api = mockApi({
      ...routes(),
      "/api/alerts": {
        alerts: alertStatus(),
        webhook_set: false,
        digest: digestStatus({ enabled: true, sent: 3 }),
      },
      "POST /api/alerts/digest": { delivered: 2, payload: { body: "x", reasons: [] } },
    });

    render(<Tools onUnauthorized={noop} />);

    expect(await screen.findByText("Weekly digest")).toBeInTheDocument();
    expect(screen.getByText("Mon at 09:00")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Send one now/ }));

    await waitFor(() =>
      expect(
        api.calls.some(
          (call) => call.method === "POST" && call.path === "/api/alerts/digest",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Sent to 2 devices")).toBeInTheDocument();
  });

  it("says plainly when nothing is subscribed", async () => {
    reducedMotion();
    mockApi({
      ...routes(),
      "/api/alerts": { alerts: alertStatus(), webhook_set: false, digest: digestStatus() },
      "POST /api/alerts/digest": { delivered: 0, payload: { body: "x", reasons: [] } },
    });

    render(<Tools onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("button", { name: /Send one now/ }));

    expect(await screen.findByText("Nothing is subscribed to push yet")).toBeInTheDocument();
  });
});
