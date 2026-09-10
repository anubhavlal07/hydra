import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Overview from "@/components/Overview";
import { phoneWidth, reducedMotion } from "./media";
import { mockApi } from "./api-mock";
import { health, overview, resolverDetail, statsTimeline } from "./fixtures";

const noop = () => {};

const routes = (over: Record<string, unknown> = {}) => {
  const parts: Record<string, unknown> = {
    "/api/overview": overview(),
    "/api/resolver": resolverDetail(),
    "/api/health": health(),
    "/api/stats/timeline": statsTimeline(),
    ...over,
  };

  return {
    "/api/pulse": {
      overview: parts["/api/overview"],
      resolver: parts["/api/resolver"],
      health: parts["/api/health"],
    },
    "/api/stats/timeline": parts["/api/stats/timeline"],
  };
};

describe("Overview", () => {
  it("leads with the pooled allowance and the accounts behind it", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Pooled allowance left")).toBeInTheDocument();
    expect(screen.getByText("540K")).toBeInTheDocument();
    expect(screen.getByText(/of 600K across 2 accounts/)).toBeInTheDocument();
    expect(screen.getByText("Accounts serving")).toBeInTheDocument();
  });

  it("counts the accounts taking traffic, not the ones merely configured", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({
          accounts: { ...overview().accounts, total: 3, enabled: 2, serving: 2 },
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Accounts serving")).toBeInTheDocument();
    expect(screen.getByText(/1 turned off/)).toBeInTheDocument();
  });

  it("surfaces the stale cache figures the resolver now reports", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Served stale")).toBeInTheDocument();
    expect(screen.getByText("1,842")).toBeInTheDocument();
    expect(screen.getByText(/3,900 live, 310 stale/)).toBeInTheDocument();
  });

  it("counts answers warmed at startup beside the prefetcher", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText(/640 warmed at startup/)).toBeInTheDocument();
  });

  it("reports certificate watch, cache persistence, coalescing and the budget", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Certificate watch")).toBeInTheDocument();
    expect(screen.getByText("Cache persistence")).toBeInTheDocument();
    expect(screen.getByText("In-flight coalescing")).toBeInTheDocument();
    expect(screen.getByText("Per-device budget")).toBeInTheDocument();
    expect(screen.getByText("3,120")).toBeInTheDocument();
    expect(screen.getByText("2,000 / hour")).toBeInTheDocument();
  });

  it("counts Hydra blocklists among the internals", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Hydra blocklists")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
    expect(screen.getByText("184,210")).toBeInTheDocument();
    expect(screen.getByText("7,415")).toBeInTheDocument();
  });

  it("counts the rebind guard's refusals among the internals", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    const heading = await screen.findByText("Rebind guard");
    expect(heading.parentElement).toHaveTextContent("1,205");
    expect(heading.parentElement).toHaveTextContent("plex.direct");
  });

  it("counts the answers CNAME uncloaking blocked among the internals", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    const heading = await screen.findByText("CNAME uncloaking");
    expect(heading.parentElement).toHaveTextContent("318");
    expect(heading.parentElement).toHaveTextContent("on");
  });

  it("says so when the resolver reports no blocklists", async () => {
    reducedMotion();
    mockApi(routes({ "/api/health": health({ blocklists: null }) }));

    render(<Overview onUnauthorized={noop} />);

    const heading = await screen.findByText("Hydra blocklists");
    expect(heading.parentElement).toHaveTextContent("Not reported yet.");
  });

  it("says the ceiling is per device when only a device carries one", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/health": health({
          budget: {
            enabled: true,
            hourly: 0,
            denied: 0,
            devices: [
              {
                device: "moto",
                hourly: 5000,
                remaining: 4800,
                used: 200,
                denied: 0,
                breached_at: null,
              },
            ],
          },
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Per-device budget")).toBeInTheDocument();
    expect(screen.getAllByText("per device").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 / hour")).not.toBeInTheDocument();
  });

  it("leaves a place for a DoQ listener the resolver does not report yet", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("DoQ")).toBeInTheDocument();
    expect(screen.getByText("not reported by this resolver yet")).toBeInTheDocument();
  });

  it("repeats every reason the resolver gives for wanting attention", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({
          attention: ["certificate expires in 6 days", "tv is over its hourly budget"],
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("certificate expires in 6 days")).toBeInTheDocument();
    expect(screen.getByText("tv is over its hourly budget")).toBeInTheDocument();
  });

  it("explains the fallback rather than the attention list when the pool is spent", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({
          attention: ["something else"],
          pool: {
            ...overview().pool,
            spent: true,
            fallback: {
              active: true,
              url: "https://dns.adguard-dns.com/dns-query",
              queries: 12,
              errors: 0,
              latency_ms: 30,
              last_error: "",
            },
          },
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(
      await screen.findByText(/Every account is out of allowance, so filtering is off/),
    ).toBeInTheDocument();
    expect(screen.queryByText("something else")).not.toBeInTheDocument();
  });

  it("says so when nothing has been ranked yet", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({ upstreams: [] }),
        "/api/resolver": resolverDetail({ top_domains: [], top_blocked: [], timeline: [] }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("No queries recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("Nothing blocked yet.")).toBeInTheDocument();
  });

  it("leaves the per-account table to Accounts rather than repeating it", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);
    await screen.findByText("Pooled allowance left");

    expect(screen.queryByRole("heading", { name: /Upstream accounts/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Quota used")).not.toBeInTheDocument();
    expect(screen.getByText("Accounts serving")).toBeInTheDocument();
  });

  it("offers a retry when the overview itself fails", async () => {
    reducedMotion();
    mockApi({});

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the quota saved this month against the allowance, by cause", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Quota saved this month")).toBeInTheDocument();
    expect(screen.getByText("12,480")).toBeInTheDocument();
    expect(screen.getByText("2.1% of allowance")).toBeInTheDocument();
    expect(
      screen.getByText("9.6K cache · 3.1K blocklists · 420 rules · 172 joined in flight · −812 prefetch"),
    ).toBeInTheDocument();
  });

  it("names a single saved query in the singular", async () => {
    reducedMotion();
    const saved = {
      ...overview().saved!,
      by_cause: { blocklist: 1, rule: 1, cache: 1, coalesced: 2 },
      prefetch_cost: 0,
    };
    mockApi(routes({ "/api/overview": overview({ saved }) }));

    render(<Overview onUnauthorized={noop} />);

    expect(
      await screen.findByText("1 cache · 1 blocklist · 1 rule · 2 joined in flight"),
    ).toBeInTheDocument();
  });

  it("says when the saved count only covers this run", async () => {
    reducedMotion();
    const saved = { ...overview().saved!, partial: true, fraction: null, allowance: null };
    mockApi(routes({ "/api/overview": overview({ saved }) }));

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("allowance not reported yet · since restart")).toBeInTheDocument();
  });

  it("leaves the saved stat out when the resolver does not report it", async () => {
    reducedMotion();
    const older: Partial<ReturnType<typeof overview>> = overview();
    delete older.saved;
    mockApi(routes({ "/api/overview": older }));

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Pooled allowance left")).toBeInTheDocument();
    expect(screen.queryByText("Quota saved this month")).not.toBeInTheDocument();
  });

  it("reads an empty month without a breakdown", async () => {
    reducedMotion();
    const saved = {
      ...overview().saved!,
      by_cause: {},
      gross: 0,
      prefetch_cost: 0,
      net: 0,
      fraction: 0,
    };
    mockApi(routes({ "/api/overview": overview({ saved }) }));

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("nothing answered locally yet")).toBeInTheDocument();
    expect(screen.getByText("0.0% of allowance")).toBeInTheDocument();
  });
});

describe("Overview on a phone", () => {
  it("shows the quota saved this month across the grid", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Quota saved this month")).toBeInTheDocument();
    expect(screen.getByText("12,480")).toBeInTheDocument();
    expect(screen.getByText("2.1% of allowance")).toBeInTheDocument();
    expect(screen.getByText(/−812 prefetch/)).toBeInTheDocument();
  });

  it("leaves the saved stat out on a phone when it is missing", async () => {
    phoneWidth();
    mockApi(routes({ "/api/overview": overview({ saved: null }) }));

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Pooled allowance left")).toBeInTheDocument();
    expect(screen.queryByText("Quota saved this month")).not.toBeInTheDocument();
  });

  it("leads with the pooled allowance as one figure", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Pooled allowance left")).toBeInTheDocument();
    expect(screen.getByText("540K")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "quota used" })).toBeInTheDocument();
  });

  it("gives each reason for attention its own row", async () => {
    phoneWidth();
    mockApi(
      routes({
        "/api/overview": overview({
          attention: ["certificate expires in 6 days", "tv is over its hourly budget"],
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    const first = await screen.findByText("certificate expires in 6 days");
    expect(first.closest('[data-slot="row"]')).toBeTruthy();
    expect(screen.getByText("tv is over its hourly budget")).toBeInTheDocument();
  });

  it("puts the internals and the upstream table one tap away", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: /Resolver internals/ }));
    await waitFor(() => expect(window.location.hash).toBe("#overview/internals"));
    expect(screen.getByText("Certificate watch")).toBeInTheDocument();
    expect(screen.getByText("not reported by this resolver yet")).toBeInTheDocument();
    expect(screen.getByText("Hydra blocklists")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
    expect(screen.getByText("184,210")).toBeInTheDocument();
    expect(screen.getByText("CNAME uncloaking").closest("section")).toHaveTextContent("318");
  });

  it("keeps the rankings behind a row rather than two screens of bars", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /Most queried/ });
    expect(row).toHaveTextContent("graph.facebook.com · 512 queries");
    expect(screen.queryByText("example.com")).not.toBeInTheDocument();

    await user.click(row);
    await waitFor(() => expect(window.location.hash).toBe("#overview/queried"));
    expect(screen.getByRole("heading", { name: "Most queried" })).toBeInTheDocument();
    expect(screen.getByText("example.com")).toBeInTheDocument();
  });

  it("opens the blocked ranking on its own screen", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Most blocked/ }));

    await waitFor(() => expect(window.location.hash).toBe("#overview/blocked"));
    expect(screen.getByRole("heading", { name: "Most blocked" })).toBeInTheDocument();

    await user.hover(screen.getByRole("button", { name: "More information" }));
    expect(await screen.findByText(/indicative/)).toBeInTheDocument();
  });

  it("does not repeat the account list Accounts already carries", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Overview onUnauthorized={noop} />);
    await screen.findByText("Pooled allowance left");

    expect(screen.queryByRole("button", { name: /Upstream accounts/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Share of traffic")).not.toBeInTheDocument();
  });
});

describe("Figures the resolver computes", () => {
  it("shows the headroom the API reports rather than recomputing it", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({
          quota: { ...overview().quota, headroom_days: 42.7 },
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("Days of headroom")).toBeInTheDocument();
    expect(await screen.findByText("42")).toBeInTheDocument();
  });

  it("says so when the resolver cannot project yet", async () => {
    reducedMotion();
    mockApi(
      routes({
        "/api/overview": overview({
          quota: { ...overview().quota, headroom_days: null },
        }),
      }),
    );

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText("nothing spent yet this month")).toBeInTheDocument();
  });
});
