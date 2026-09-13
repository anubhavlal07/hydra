import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Policy, { BlockButton } from "@/components/Policy";
import { mockApi } from "./api-mock";
import { driftingPolicy, policy, ruleGroups } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const written = {
  applied_to: ["alpha", "beta"],
  errors: {},
  cache_cleared: 12,
  in_sync: true,
};

describe("Policy", () => {
  it("shows agreement across accounts rather than per-account values", async () => {
    reducedMotion();
    mockApi({ "/api/policy": policy(), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText("Agreement across 2 accounts")).toBeInTheDocument();
    expect(screen.getByText("Filtering enabled")).toBeInTheDocument();
    expect(screen.getByText("AdGuard blocklists")).toBeInTheDocument();
    expect(screen.getByText("2 settings differ")).toBeInTheDocument();
  });

  it("names the rules that are not on every account", async () => {
    reducedMotion();
    mockApi({ "/api/policy": policy(), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText("Rules that are not everywhere")).toBeInTheDocument();
    expect(screen.getByText("||ads.example^")).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("writes the whole rule list to every account", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/policy": policy(),
      "/api/rule-groups": ruleGroups(),
      "PUT /api/policy": written,
    });

    render(<Policy onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Save to all 2 accounts" }));

    expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
      rules: ["||doubleclick.net^", "||ads.example^"],
      rules_enabled: true,
    });
    expect(await screen.findByText(/Rules saved on all 2 accounts/)).toBeInTheDocument();
  });

  it("offers one button to switch filtering on everywhere it is off", async () => {
    reducedMotion();
    mockApi({
      "/api/policy": policy({
        policy: { ...policy().policy!, unprotected_accounts: ["beta"] },
      }),
    });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText(/Filtering is switched off on beta/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Turn filtering on everywhere" }),
    ).toBeInTheDocument();
  });

  it("says there is nothing to show when no account answered", async () => {
    reducedMotion();
    mockApi({ "/api/policy": policy({ policy: null, errors: { alpha: "timed out" } }) });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText(/No account answered with its filtering settings/)).toBeInTheDocument();
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });
});

describe("Policy on a phone", () => {
  it("reads as a settings screen, one idea per row", async () => {
    phoneWidth();
    mockApi({ "/api/policy": policy(), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByRole("heading", { name: /Applied to all 2 accounts/ })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Filtering" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Safe browsing" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Parental control" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Blocklists switch" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Custom rules switch" })).toBeInTheDocument();
    expect(screen.getAllByText("same everywhere")).toHaveLength(5);
    expect(screen.getByText("differs — 1 of 2")).toBeInTheDocument();
  });

  it("puts the rules editor behind a drill-down, with Save in the header", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/policy": policy(),
      "/api/rule-groups": ruleGroups(),
      "PUT /api/policy": { applied_to: ["alpha", "beta"], errors: {}, cache_cleared: 0, in_sync: true },
    });

    render(<Policy onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: /Custom rules/ }));
    await waitFor(() => expect(window.location.hash).toBe("#rules/custom"));

    expect(screen.getByRole("heading", { name: "Custom rules" })).toBeInTheDocument();
    const editor = screen.getByLabelText("Custom filtering rules");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(editor, "\n||tracking.example^");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
      rules: ["||doubleclick.net^", "||ads.example^", "||tracking.example^"],
      rules_enabled: true,
    });
  });

  it("keeps the rules that are not everywhere inside the editor", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/policy": policy(), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Custom rules/ }));

    expect(await screen.findByText("||ads.example^")).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("still shows a partial failure at phone width", async () => {
    phoneWidth();
    mockApi({ "/api/policy": policy({ errors: { beta: "timed out" } }) });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText(/did not answer/)).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("drills into one blocklist set, naming only the accounts a list is missing from", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/policy": policy(), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /AdGuard blocklists/ }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/blocklists"));

    expect(screen.getAllByText("AdGuard DNS filter")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "alpha" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "beta" })).not.toBeInTheDocument();

    expect(screen.getByText("Phishing URL Blocklist")).toBeInTheDocument();
    expect(screen.getByText("missing from alpha")).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: /Use the same blocklists everywhere/ }),
    ).toBeInTheDocument();
  });
});

const synced = {
  applied_to: ["alpha", "bravo", "charlie", "echo"],
  errors: {},
  cache_cleared: 4,
  in_sync: true,
  repaired: { echo: ["user rules", "filter lists switch"] },
  left: [],
  read_errors: {},
};

describe("An account that filters differently", () => {
  it("names it on the desktop and syncs every account in one click", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/policy": driftingPolicy(),
      "/api/rule-groups": ruleGroups(),
      "POST /api/policy/sync": synced,
    });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText(/filters differently: user rules, filter lists switch/)).toBeInTheDocument();
    expect(screen.getByText("Blocklists switch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sync accounts" }));

    expect(api.calls.some((call) => call.method === "POST" && call.path === "/api/policy/sync")).toBe(true);
    expect(await screen.findByText(/Accounts synced on all 4 accounts/)).toBeInTheDocument();
  });

  it("says which settings had no majority after a sync", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      "/api/policy": driftingPolicy(),
      "/api/rule-groups": ruleGroups(),
      "POST /api/policy/sync": { ...synced, left: ["protection"] },
    });

    render(<Policy onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Sync accounts" }));

    expect(await screen.findByText(/no majority on protection/)).toBeInTheDocument();
  });

  it("stays out of the way when every account agrees", async () => {
    reducedMotion();
    const agreed = driftingPolicy();
    agreed.policy = { ...agreed.policy!, outliers: {}, split: [], in_sync: true, drifting: [] };
    mockApi({ "/api/policy": agreed, "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText("Agreement across 4 accounts")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync accounts" })).not.toBeInTheDocument();
  });

  it("gets its own group on a phone, with a full-height sync row", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/policy": driftingPolicy(),
      "/api/rule-groups": ruleGroups(),
      "POST /api/policy/sync": synced,
    });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByRole("heading", { name: "Out of step" })).toBeInTheDocument();
    expect(screen.getByText("echo")).toBeInTheDocument();
    expect(screen.getByText("user rules, filter lists switch")).toBeInTheDocument();
    expect(screen.getByText("differs")).toBeInTheDocument();

    const sync = screen.getByRole("button", { name: /Sync accounts/ });
    expect(sync.className).toMatch(/min-h-1[1-9]/);
    await user.click(sync);

    expect(api.calls.some((call) => call.method === "POST" && call.path === "/api/policy/sync")).toBe(true);
    expect(await screen.findByText(/Accounts synced on all 4 accounts/)).toBeInTheDocument();
  });
});

describe("BlockButton", () => {
  it("blocks a domain on every account and says so", async () => {
    const user = userEvent.setup();
    const api = mockApi({ "POST /api/policy/rule": { ...written, domain: "ads.example", action: "block" } });

    render(<BlockButton domain="ads.example" blocked={false} />);
    const button = screen.getByRole("button", { name: "Block" });
    expect(button).toHaveAttribute("title", "Block ads.example on every account");
    await user.click(button);

    expect(api.calls[0].body).toEqual({ domain: "ads.example", action: "block" });
    expect(await screen.findByText("blocked everywhere")).toBeInTheDocument();
  });

  it("offers to allow a domain that is already blocked", async () => {
    const user = userEvent.setup();
    const api = mockApi({ "POST /api/policy/rule": { ...written, domain: "ads.example", action: "allow" } });

    render(<BlockButton domain="ads.example" blocked />);
    const button = screen.getByRole("button", { name: "Allow" });
    expect(button).toHaveAttribute("title", "Allow ads.example on every account");
    await user.click(button);

    expect(api.calls[0].body).toEqual({ domain: "ads.example", action: "allow" });
    expect(await screen.findByText("allowed everywhere")).toBeInTheDocument();
  });
});

describe("The rule cap comes from the resolver", () => {
  it("counts against the cap the API reports, not a number of its own", async () => {
    reducedMotion();
    mockApi({ "/api/policy": policy({ rules_max: 3 }), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText("2 / 3")).toBeInTheDocument();
  });

  it("refuses to save past that cap and says which cap it is", async () => {
    reducedMotion();
    mockApi({ "/api/policy": policy({ rules_max: 1 }), "/api/rule-groups": ruleGroups() });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText(/AdGuard allows 1 rules per account/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save to all/ })).toBeDisabled();
  });
});

describe("YouTube restricted mode on the accounts", () => {
  it("turns it off on every account in one write", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const both = { alpha: true, beta: true };
    const api = mockApi({
      "/api/policy": policy({
        policy: {
          ...policy().policy!,
          youtube_safe_search: { value: true, in_sync: true, per_account: both },
          engines_safe_search: { value: true, in_sync: true, per_account: both },
        },
      }),
      "/api/rule-groups": ruleGroups(),
      "PUT /api/policy": written,
    });

    render(<Policy onUnauthorized={noop} />);

    const youtube = await screen.findByRole("switch", { name: "YouTube restricted mode" });
    expect(youtube).toBeChecked();
    expect(screen.getByRole("switch", { name: "Search engine safe search" })).toBeChecked();

    await user.click(youtube);

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        youtube_safe_search: false,
      }),
    );
  });
});
