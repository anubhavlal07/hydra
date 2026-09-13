import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Accounts from "@/components/Accounts";
import { mockApi } from "./api-mock";
import { accounts, upstream as upstreamFixture } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

describe("Accounts", () => {
  it("shows each account once, with its allowance, share and upstream detail", async () => {
    reducedMotion();
    mockApi({ "/api/accounts": accounts() });

    render(<Accounts onUnauthorized={noop} />);

    expect(await screen.findByText("alpha@example.com")).toBeInTheDocument();
    expect(screen.getByText("30,000 / 300,000")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "quota used" })).toHaveAttribute(
      "aria-valuenow",
      "10",
    );
    expect(screen.getByText("Queries routed here")).toBeInTheDocument();
    expect(screen.getByText("Remaining")).toBeInTheDocument();
    expect(screen.getByText("Share of traffic")).toBeInTheDocument();
    expect(screen.getAllByText("alpha")).toHaveLength(1);
  });

  it("says nothing is pooled when there are no accounts", async () => {
    reducedMotion();
    mockApi({ "/api/accounts": accounts({ accounts: [] }) });

    render(<Accounts onUnauthorized={noop} />);

    expect(await screen.findByText(/No accounts yet/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Pooled totals/ })).not.toBeInTheDocument();
  });

  it("opens the add form and posts the credentials once", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/accounts": accounts(),
      "POST /api/accounts": {
        account: accounts().accounts[0],
        store: accounts().store,
        serving: true,
      },
    });

    render(<Accounts onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Add account/ }));

    await user.type(screen.getByLabelText("AdGuard account email"), "beta@example.com");
    await user.type(screen.getByLabelText("AdGuard password"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Add to the pool" }));

    const posted = api.calls.find((call) => call.method === "POST");
    expect(posted?.body).toEqual({ username: "beta@example.com", password: "hunter2" });
    expect(await screen.findByText(/alpha is in the pool and serving/)).toBeInTheDocument();
  });

  it("says when a new account's policy could not be synced", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      "/api/accounts": accounts(),
      "POST /api/accounts": {
        account: accounts().accounts[0],
        store: accounts().store,
        serving: true,
        policy_sync: {
          synced: false,
          applied: [],
          left: ["protection"],
          from: [],
          error: "HTTP 500",
        },
      },
    });

    render(<Accounts onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Add account/ }));
    await user.type(screen.getByLabelText("AdGuard account email"), "echo@example.com");
    await user.type(screen.getByLabelText("AdGuard password"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Add to the pool" }));

    expect(await screen.findByText(/Its policy was not synced: HTTP 500/)).toBeInTheDocument();
    expect(screen.getByText(/No agreement on protection/)).toBeInTheDocument();
  });

  it("will not remove the only account left", async () => {
    reducedMotion();
    mockApi({ "/api/accounts": accounts() });

    render(<Accounts onUnauthorized={noop} />);

    expect(await screen.findByRole("button", { name: "Remove" })).toBeDisabled();
  });

  it("explains when accounts are read-only", async () => {
    reducedMotion();
    mockApi({
      "/api/accounts": accounts({
        store: { ...accounts().store, configured: false, editable: false },
      }),
    });

    render(<Accounts onUnauthorized={noop} />);

    expect(await screen.findByText(/HYDRA_ADGUARD_ACCOUNTS/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add account/ })).not.toBeInTheDocument();
  });
});

const spread = () => {
  const one = accounts().accounts[0];
  return accounts({
    accounts: [
      { ...one, label: "gamma", username: "gamma@example.com", requests_used: 288_000 },
      { ...one, label: "alpha", username: "alpha@example.com", requests_used: 122_000 },
      { ...one, label: "delta", username: "delta@example.com", requests_limit: 0 },
      { ...one, label: "beta", username: "beta@example.com", requests_used: 96_000 },
    ],
  });
};

const order2 = () =>
  screen
    .getAllByText(/^(alpha|beta)$/)
    .map((node) => node.textContent)
    .slice(0, 2);

const order = () =>
  screen
    .getAllByText(/^(alpha|beta|gamma|delta)$/)
    .map((node) => node.textContent)
    .slice(0, 4);

describe("Accounts order", () => {
  it("puts the emptiest account first, on desktop", async () => {
    reducedMotion();
    mockApi({ "/api/accounts": spread() });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByText("beta@example.com");

    expect(order()).toEqual(["beta", "alpha", "gamma", "delta"]);
  });

  it("does the same on a phone, and leaves an unreported allowance last", async () => {
    phoneWidth();
    mockApi({ "/api/accounts": spread() });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByRole("button", { name: /beta/ });

    expect(order()).toEqual(["beta", "alpha", "gamma", "delta"]);
  });
});

describe("Accounts on a phone", () => {
  it("names each account once, with what is left and its share on one line", async () => {
    phoneWidth();
    mockApi({ "/api/accounts": accounts() });

    render(<Accounts onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /alpha/ });
    expect(within(row).getByText("270K left of 300K · 100.0% of traffic")).toBeInTheDocument();

    expect(screen.getAllByText("alpha")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Pooled totals" })).not.toBeInTheDocument();
  });

  it("drills into an account for its upstream detail and its removal", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/accounts": accounts() });

    render(<Accounts onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /alpha/ }));

    await waitFor(() => expect(window.location.hash).toBe("#accounts/alpha"));
    expect(screen.getByRole("heading", { name: "alpha" })).toBeInTheDocument();
    expect(screen.getByText("Queries routed here")).toBeInTheDocument();
    expect(screen.getByText("alpha@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
  });

  it("adds an account through a sheet", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/accounts": accounts(),
      "POST /api/accounts": {
        account: accounts().accounts[0],
        store: accounts().store,
        serving: true,
      },
    });

    render(<Accounts onUnauthorized={noop} />);
    expect(screen.queryByLabelText("AdGuard password")).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const sheet = await screen.findByRole("dialog");

    await user.type(within(sheet).getByLabelText("AdGuard account email"), "beta@example.com");
    await user.type(within(sheet).getByLabelText("AdGuard password"), "hunter2");
    await user.click(within(sheet).getByRole("button", { name: "Add to the pool" }));

    expect(api.calls.find((call) => call.method === "POST")?.body).toEqual({
      username: "beta@example.com",
      password: "hunter2",
    });
  });
});

const pair = (over: Partial<import("@/lib/types").AccountRow> = {}) => {
  const one = accounts().accounts[0];
  return accounts({
    accounts: [
      {
        ...one,
        label: "alpha",
        username: "alpha@example.com",
        upstream: upstreamFixture("alpha", { share: 0.6 }),
      },
      {
        ...one,
        label: "beta",
        username: "beta@example.com",
        requests_used: 200_000,
        upstream: upstreamFixture("beta", { share: 0.4 }),
        ...over,
      },
    ],
    store: { ...accounts().store, count: 2, enabled_count: 2 },
  });
};

describe("Accounts rotation", () => {
  it("turns an account off through the API and keeps the rest serving", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/accounts": pair(),
      "PATCH /api/accounts/beta": { label: "beta", enabled: false, store: pair().store, accounts: [] },
    });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByText("beta@example.com");

    await user.click(screen.getByRole("switch", { name: "Send traffic to beta" }));

    const patched = api.calls.find((call) => call.method === "PATCH");
    expect(patched?.path).toBe("/api/accounts/beta");
    expect(patched?.body).toEqual({ enabled: false });
  });

  it("shows a disabled account as off, out of the rotation, and last", async () => {
    reducedMotion();
    mockApi({
      "/api/accounts": pair({ enabled: false }),
    });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByText("beta@example.com");

    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.getByText("not taking traffic")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Send traffic to beta" })).not.toBeChecked();
    expect(order2()).toEqual(["alpha", "beta"]);
  });

  it("splits the share over the accounts taking traffic, not the query history", async () => {
    reducedMotion();
    mockApi({
      "/api/accounts": pair({
        enabled: false,
        upstream: upstreamFixture("beta", { enabled: false, share: 0, queries: 90_000 }),
      }),
    });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByText("beta@example.com");

    expect(screen.getByText("60.0%")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(screen.queryByText("40.0%")).not.toBeInTheDocument();
  });

  it("will not let the last account taking traffic be turned off", async () => {
    reducedMotion();
    mockApi({ "/api/accounts": accounts() });

    render(<Accounts onUnauthorized={noop} />);

    expect(await screen.findByRole("switch", { name: "Send traffic to alpha" })).toBeDisabled();
  });

  it("offers no switch when the pool is read-only", async () => {
    reducedMotion();
    mockApi({
      "/api/accounts": accounts({
        store: { ...accounts().store, configured: false, editable: false },
      }),
    });

    render(<Accounts onUnauthorized={noop} />);

    await screen.findByText(/HYDRA_ADGUARD_ACCOUNTS/);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("surfaces a failed toggle instead of pretending it worked", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      "/api/accounts": pair(),
      "PATCH /api/accounts/beta": () => {
        throw new Error("nope");
      },
    });

    render(<Accounts onUnauthorized={noop} />);
    await screen.findByText("beta@example.com");

    await user.click(screen.getByRole("switch", { name: "Send traffic to beta" }));

    expect(await screen.findByText(/Could not reach the resolver API/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Send traffic to beta" })).toBeChecked();
  });
});

describe("Accounts rotation on a phone", () => {
  it("turns an account off from its own screen", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/accounts": pair(),
      "PATCH /api/accounts/beta": { label: "beta", enabled: false, store: pair().store, accounts: [] },
    });

    render(<Accounts onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /beta/ }));

    await user.click(await screen.findByRole("switch", { name: "Send traffic to beta" }));

    const patched = api.calls.find((call) => call.method === "PATCH");
    expect(patched?.path).toBe("/api/accounts/beta");
    expect(patched?.body).toEqual({ enabled: false });
  });

  it("marks a disabled account in the list without opening it", async () => {
    phoneWidth();
    mockApi({ "/api/accounts": pair({ enabled: false }) });

    render(<Accounts onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /beta/ });
    expect(within(row).getByText("off")).toBeInTheDocument();
    expect(within(row).getByText(/not taking traffic/)).toBeInTheDocument();
  });
});
