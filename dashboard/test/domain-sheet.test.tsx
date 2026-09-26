import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DomainSheetProvider, useOpenDomain } from "@/components/DomainSheet";
import QueryLog from "@/components/QueryLog";
import { mockApi } from "./api-mock";
import {
  blocklistAllowResult,
  deviceRuleEntryResult,
  domainResponse,
  groupRuleEntryResult,
  recent,
  ruleGroups,
} from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const written = { applied_to: ["alpha", "beta"], errors: {}, cache_cleared: 1, in_sync: true };

function withSheet(children: React.ReactNode) {
  return render(<DomainSheetProvider>{children}</DomainSheetProvider>);
}

function Opener({ name, device }: { name: string; device?: string }) {
  const openDomain = useOpenDomain();
  return (
    <button type="button" onClick={() => openDomain(name, { device })}>
      open {name}
    </button>
  );
}

const domainCalls = (api: ReturnType<typeof mockApi>) =>
  api.calls.filter((call) => call.method === "GET" && call.path === "/api/domain");

describe("the domain sheet opens from a log row", () => {
  it("opens from the desktop table's domain cell and keeps the row's Block button", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({ "/api/resolver/recent": recent(), "/api/domain": domainResponse() });

    withSheet(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    expect(document.querySelector("table")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: "Block" }).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "example.com" }));

    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("heading", { name: "example.com" })).toBeInTheDocument();
    expect(within(sheet).getByText("Latency")).toBeInTheDocument();
    expect(window.location.search).toContain("domain=example.com");
    await waitFor(() => expect(domainCalls(api)[0]?.search).toBe("?name=example.com"));
  });

  it("opens from a phone row, with that row's device first", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/resolver/recent": recent(),
      "/api/domain": domainResponse({ name: "budget.example" }),
    });

    withSheet(<QueryLog onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /^budget\.example/ }));

    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText("This query")).toBeInTheDocument();

    const group = (await within(sheet).findByText("On each device")).closest("section")!;
    const names = within(group)
      .getAllByRole("button")
      .map((row) => row.getAttribute("aria-label"));
    expect(names[0]).toMatch(/^Living room TV/);
    expect(
      within(sheet).getByRole("button", { name: "Block on Living room TV" }),
    ).toBeInTheDocument();
  });

  it("reads ?domain= on the first render, so a link or a refresh opens it", async () => {
    window.history.replaceState(null, "", "/?domain=example.com#log");
    const api = mockApi({ "/api/domain": domainResponse() });

    withSheet(<p>page</p>);

    const sheet = screen.getByRole("dialog");
    expect(within(sheet).getByRole("heading", { name: "example.com" })).toBeInTheDocument();
    await waitFor(() => expect(domainCalls(api)).toHaveLength(1));
    expect(within(sheet).queryByText("This query")).not.toBeInTheDocument();
  });

  it("drops ?domain= when it closes", async () => {
    window.history.replaceState(null, "", "/?domain=example.com#log");
    const user = userEvent.setup();
    mockApi({ "/api/domain": domainResponse() });

    withSheet(<p>page</p>);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(window.location.search).not.toContain("domain=");
    expect(window.location.hash).toBe("#log");
  });
});

describe("what the sheet says", () => {
  it("lists each device's deciding layer and who has been asking", async () => {
    mockApi({ "/api/domain": domainResponse() });

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));

    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("On each device");

    expect(within(sheet).getByText("blocklist hagezi-pro")).toBeInTheDocument();
    expect(within(sheet).getByText("allowed by @@ device")).toBeInTheDocument();
    expect(within(sheet).getByText("AdGuard")).toBeInTheDocument();
    expect(within(sheet).getByText(/^412 queries · last /)).toBeInTheDocument();
    expect(within(sheet).getByText(/first seen/)).toBeInTheDocument();
    expect(within(sheet).getByText("Hydra blocklists")).toBeInTheDocument();
  });

  it("says when the rebind guard refused a device's last answer", async () => {
    const base = domainResponse();
    mockApi({
      "/api/domain": domainResponse({
        askers: [{ ...base.askers[1], last_upstream: "rebind guard → 0.0.0.0" }],
      }),
    });

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));

    const sheet = await screen.findByRole("dialog");
    expect(
      await within(sheet).findByText(/^18 queries · last .* · refused by the rebind guard$/),
    ).toBeInTheDocument();
  });

  it.each([
    ["desktop", reducedMotion],
    ["phone", phoneWidth],
  ])("says which CNAME target blocked a device's last answer on %s", async (_, width) => {
    width();
    const base = domainResponse();
    mockApi({
      "/api/domain": domainResponse({
        askers: [{ ...base.askers[1], last_upstream: "cname collect.tracker.net · blocklist Ads → 0.0.0.0" }],
      }),
    });

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));

    const sheet = await screen.findByRole("dialog");
    expect(
      await within(sheet).findByText(
        /^18 queries · last .* · blocked by cname collect\.tracker\.net · blocklist Ads$/,
      ),
    ).toBeInTheDocument();
  });

  it("says how a machine-generated name looks, and says nothing for an ordinary one", async () => {
    mockApi({
      "/api/domain": domainResponse({
        name: "a1b2c3d4e5f6g7h8.top",
        suspicion: { score: 100, reasons: ["entropy", "tld"], threshold: 50 },
      }),
    });

    const first = withSheet(<Opener name="a1b2c3d4e5f6g7h8.top" />);
    await userEvent.click(screen.getByRole("button", { name: "open a1b2c3d4e5f6g7h8.top" }));

    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("Suspicion 100")).toBeInTheDocument();
    expect(within(sheet).getByText("random-looking name, often-abused TLD")).toBeInTheDocument();
    expect(within(sheet).getByText("suspicious")).toBeInTheDocument();
    first.unmount();

    mockApi({ "/api/domain": domainResponse() });
    window.history.replaceState(null, "", "/");
    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));
    const plain = await screen.findByRole("dialog");
    await within(plain).findByText("On each device");
    expect(within(plain).queryByText("How it looks")).not.toBeInTheDocument();
  });

  it("never asks the resolver to resolve the name", async () => {
    const api = mockApi({ "/api/domain": domainResponse() });

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));
    await screen.findByText("On each device");

    expect(api.calls.some((call) => call.path === "/api/lookup")).toBe(false);
  });

  it("shows the error inside the sheet when /api/domain fails", async () => {
    mockApi({});

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));

    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByRole("alert")).toHaveTextContent("No mock for GET /api/domain");
    expect(within(sheet).getByRole("button", { name: "Block" })).toBeInTheDocument();
  });
});

const ruledBy = (rule: string) =>
  domainResponse({
    name: "ads.example.com",
    devices: domainResponse().devices.map((device) =>
      device.id === "laptop"
        ? {
            ...device,
            decision: {
              layer: "device rule",
              label: "device rule",
              blocked: true,
              source: null,
              rule,
            },
            blocked: true,
          }
        : device,
    ),
  });

describe("acting from the sheet", () => {
  it("blocks on the row's device through the entry route, then reads the domain again", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/devices/laptop/rules/entry": deviceRuleEntryResult(),
    });

    withSheet(<Opener name="example.com" device="laptop" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await user.click(await screen.findByRole("button", { name: "Block on Work Laptop" }));

    await waitFor(() => expect(domainCalls(api)).toHaveLength(2));
    const write = api.calls.find((call) => call.path === "/api/devices/laptop/rules/entry");
    expect(write?.method).toBe("POST");
    expect(write?.body).toEqual({ domain: "example.com", action: "block" });
    expect(api.calls.indexOf(write!)).toBeLessThan(api.calls.indexOf(domainCalls(api)[1]));
  });

  it("allows on another device once that device is picked", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/devices/tv/rules/entry": deviceRuleEntryResult("tv", ["@@example.com"]),
    });

    withSheet(<Opener name="example.com" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await user.click(await screen.findByRole("button", { name: /^Living room TV/ }));
    await user.click(screen.getByRole("button", { name: "Allow on Living room TV" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.path === "/api/devices/tv/rules/entry")?.body).toEqual({
        domain: "example.com",
        action: "allow",
      }),
    );
  });

  it("clears a device's own entry for the name", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/devices/pixel/rules/entry": deviceRuleEntryResult("pixel", []),
    });

    withSheet(<Opener name="example.com" device="pixel" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await user.click(await screen.findByRole("button", { name: "Clear on Pixel 9" }));

    expect(screen.queryByRole("button", { name: "Allow on Pixel 9" })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        api.calls.find((call) => call.path === "/api/devices/pixel/rules/entry")?.body,
      ).toEqual({ domain: "example.com", action: "clear" }),
    );
  });

  it("offers Allow, not Clear, when a parent rule blocks the name", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/domain": ruledBy("example.com") });

    withSheet(<Opener name="ads.example.com" device="laptop" />);
    await user.click(screen.getByRole("button", { name: "open ads.example.com" }));

    expect(await screen.findByRole("button", { name: "Allow on Work Laptop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear on Work Laptop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Block on Work Laptop" })).not.toBeInTheDocument();
  });

  it("offers Clear when the device's rule is the name itself", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/domain": ruledBy("ads.example.com") });

    withSheet(<Opener name="ads.example.com" device="laptop" />);
    await user.click(screen.getByRole("button", { name: "open ads.example.com" }));

    expect(await screen.findByRole("button", { name: "Clear on Work Laptop" })).toBeInTheDocument();
  });

  it("blocks for a tag by picking an existing schedule", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "/api/rule-groups": ruleGroups(),
      "POST /api/rule-groups/bedtime/rules/entry": groupRuleEntryResult(),
    });

    withSheet(<Opener name="example.com" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await user.click(await screen.findByRole("button", { name: "Block for a tag…" }));
    await user.click(await screen.findByRole("button", { name: "Block for Bedtime" }));

    await waitFor(() => expect(domainCalls(api)).toHaveLength(2));
    expect(
      api.calls.find((call) => call.path === "/api/rule-groups/bedtime/rules/entry")?.body,
    ).toEqual({ domain: "example.com", action: "block" });
  });

  it("allows a name despite a Hydra blocklist", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/blocklists/hagezi-pro/allow": blocklistAllowResult(),
    });

    withSheet(<Opener name="example.com" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await user.click(await screen.findByRole("button", { name: "Allow despite this list" }));

    await waitFor(() => expect(domainCalls(api)).toHaveLength(2));
    expect(api.calls.find((call) => call.path === "/api/blocklists/hagezi-pro/allow")?.body).toEqual(
      { domain: "example.com", action: "add" },
    );
  });

  it("still blocks everywhere through changeRule", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/policy/rule": { ...written, domain: "example.com", action: "block" },
    });

    withSheet(<Opener name="example.com" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await screen.findByText("On each device");
    await user.click(screen.getByRole("button", { name: "Block" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.path === "/api/policy/rule")?.body).toEqual({
        domain: "example.com",
        action: "block",
      }),
    );
    expect(await screen.findByText("blocked everywhere")).toBeInTheDocument();
  });

  it("says when the everywhere write missed an account", async () => {
    const user = userEvent.setup();
    mockApi({
      "/api/domain": domainResponse(),
      "POST /api/policy/rule": {
        ...written,
        applied_to: ["alpha"],
        errors: { beta: "timeout" },
        domain: "example.com",
        action: "block",
      },
    });

    withSheet(<Opener name="example.com" />);
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    await screen.findByText("On each device");
    await user.click(screen.getByRole("button", { name: "Block" }));

    const sheet = screen.getByRole("dialog");
    expect(await within(sheet).findByText(/Showing partial data/)).toBeInTheDocument();
    expect(within(sheet).getByText("beta")).toBeInTheDocument();
  });

  it("hides the scoped writes when the registry is read-only", async () => {
    mockApi({ "/api/domain": domainResponse({ editable: false }) });

    withSheet(<Opener name="example.com" />);
    await userEvent.click(screen.getByRole("button", { name: "open example.com" }));
    await screen.findByText("On each device");

    expect(screen.queryByRole("button", { name: /^Block on / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow despite this list" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Block" })).toBeInTheDocument();
  });
});

describe("the domain sheet on a phone", () => {
  it("keeps every row at the 44px floor", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/resolver/recent": recent(),
      "/api/domain": domainResponse(),
      "/api/rule-groups": ruleGroups(),
    });

    withSheet(<QueryLog onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /^example\.com/ }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("On each device");
    await user.click(within(sheet).getByRole("button", { name: "Block for a tag…" }));
    await within(sheet).findByRole("button", { name: "Block for Bedtime" });

    const rows = sheet.querySelectorAll('[data-slot="row"]');
    expect(rows.length).toBeGreaterThan(6);
    for (const row of rows) expect(row).toHaveClass("min-h-12");

    const detail = sheet.querySelectorAll("dl > div");
    expect(detail.length).toBeGreaterThan(0);
    for (const line of detail) expect(line).toHaveClass("min-h-11");

    const everywhere = within(sheet).getByRole("button", { name: "Block" });
    expect(everywhere).toHaveClass("max-wide:h-11", "max-wide:min-h-11");
    expect(everywhere).not.toHaveClass("max-wide:h-9");
    expect(everywhere).toHaveClass("h-7");

    const close = within(sheet).getByRole("button", { name: "Close" });
    expect(close).toHaveClass("max-wide:size-11");
    expect(close).toHaveClass("size-8");
  });

  it("opens with focus on the sheet, not on a write", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/resolver/recent": recent(),
      "/api/domain": domainResponse(),
      "/api/rule-groups": ruleGroups(),
    });

    withSheet(<QueryLog onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /^example\.com/ }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("On each device");

    expect(document.activeElement).toBe(sheet);
    await user.keyboard("{Enter}");
    expect(api.calls.some((call) => call.method !== "GET")).toBe(false);
  });
});
