import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import QueryLog from "@/components/QueryLog";
import { AnsweredBy } from "@/components/QueryLog";
import { DomainSheetProvider } from "@/components/DomainSheet";
import type { LiveQuery } from "@/lib/types";
import { mockApi } from "./api-mock";
import { domainResponse, firstSeenResponse, logDevices, recent } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const query = (over: Partial<LiveQuery> = {}): LiveQuery => ({
  at: 1_700_000_000,
  device: "laptop",
  name: "example.com",
  type: "A",
  rcode: "NOERROR",
  cached: false,
  blocked: false,
  upstream: "alpha",
  latency_ms: 20,
  ...over,
});

const openDevicePicker = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole("combobox", { name: "Filter by device", hidden: true }));
  return screen.getByRole("listbox");
};

describe("QueryLog device list", () => {
  it("comes from the registry, so a device with no rows on screen is still listed", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    const list = await openDevicePicker(user);
    const labels = within(list).getAllByRole("option").map((option) => option.textContent);

    expect(labels[0]).toBe("All devices");
    expect(labels.join(" ")).toContain("Living room TV");
    expect(labels.join(" ")).toContain("Pixel 9");
    expect(labels.join(" ")).toContain("anonymous");
  });

  it("survives selecting a device, even when the rows narrow to one", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/resolver/recent": (url: URL) => {
        const device = url.searchParams.get("device");
        const all = recent();
        return {
          ...all,
          queries: device ? all.queries.filter((row) => row.device === device) : all.queries,
          devices: logDevices,
        };
      },
    });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    await user.click(await within(await openDevicePicker(user)).findByRole("option", { name: /Pixel 9/ }));

    await waitFor(() =>
      expect(api.calls.some((call) => call.search.includes("device=pixel"))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("example.com")).not.toBeInTheDocument());

    const list = await openDevicePicker(user);
    const labels = within(list).getAllByRole("option").map((option) => option.textContent);
    expect(labels.join(" ")).toContain("Work Laptop");
    expect(labels.join(" ")).toContain("Living room TV");
    expect(labels.join(" ")).toContain("anonymous");
  });

  it("separates registered devices from ones only the traffic has heard of", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");
    await openDevicePicker(user);

    expect(screen.getByText("Devices")).toBeInTheDocument();
    expect(screen.getByText("Seen, not registered")).toBeInTheDocument();
  });
});

describe("QueryLog rows", () => {
  it("renders upstream labels it has never seen before rather than assuming a known set", async () => {
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    expect(screen.getByText("stale (upstream)")).toBeInTheDocument();
    expect(screen.getByText("stale (budget)")).toBeInTheDocument();
    expect(screen.getByText("device budget")).toBeInTheDocument();
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });

  it("pages at 25 rows", async () => {
    const queries = Array.from({ length: 60 }, (_, index) =>
      query({ at: 1_700_000_000 + index, name: `row-${index}.example` }),
    );
    mockApi({ "/api/resolver/recent": recent({ queries }) });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("row-0.example");

    expect(screen.getByText("row-24.example")).toBeInTheDocument();
    expect(screen.queryByText("row-25.example")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("row-25.example")).toBeInTheDocument();
    expect(screen.queryByText("row-24.example")).not.toBeInTheDocument();
  });

  it("says what is empty and why when a filter matches nothing", async () => {
    mockApi({ "/api/resolver/recent": recent({ queries: [] }) });

    render(<QueryLog onUnauthorized={noop} />);
    expect(await screen.findByText(/Point a device at the resolver/)).toBeInTheDocument();
  });
});

describe("QueryLog on a phone", () => {
  it("makes each query a two-line row instead of a sideways table", async () => {
    phoneWidth();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /example\.com/ });
    expect(row).toHaveAttribute("data-lines", "2");
    expect(within(row).getByText(/laptop · alpha/)).toBeInTheDocument();
    expect(within(row).getByText("Allowed")).toBeInTheDocument();
    expect(document.querySelector("table")).toBeNull();
  });

  it("collapses the filters into a sheet and shows the active ones as chips", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    expect(screen.queryByLabelText("Search domains")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Filter/ }));
    const sheet = await screen.findByRole("dialog");
    await user.click(within(sheet).getByRole("radio", { name: "Blocked" }));
    await user.click(within(sheet).getByRole("button", { name: "Show results" }));

    expect(await screen.findByRole("button", { name: "Clear blocked" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear blocked" }));
    expect(screen.queryByRole("button", { name: "Clear blocked" })).not.toBeInTheDocument();
  });

  it("keeps type, latency and the block action one tap away", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/resolver/recent": recent() });

    render(
      <DomainSheetProvider>
        <QueryLog onUnauthorized={noop} />
      </DomainSheetProvider>,
    );
    await user.click(await screen.findByRole("button", { name: /example\.com/ }));

    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText("Latency")).toBeInTheDocument();
    expect(within(sheet).getByText("21ms")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Block" })).toBeInTheDocument();
  });

  it("names the CNAME target and the list on a phone row", async () => {
    phoneWidth();
    mockApi({
      "/api/resolver/recent": recent({
        queries: [
          {
            at: 1_700_000_000,
            device: "tv",
            name: "metrics.shop.example",
            type: "A",
            rcode: "NOERROR",
            cached: true,
            blocked: true,
            upstream: "cname collect.tracker.net · blocklist Ads → 0.0.0.0",
            latency_ms: 1,
          },
        ],
      }),
    });

    render(<QueryLog onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /metrics\.shop\.example/ });
    expect(within(row).getByText("Blocked")).toBeInTheDocument();
    expect(within(row).getByText(/cname collect\.tracker\.net · blocklist Ads/)).toBeInTheDocument();
  });

  it("still says what is empty and why at phone width", async () => {
    phoneWidth();
    mockApi({ "/api/resolver/recent": recent({ queries: [] }) });

    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText(/Point a device at the resolver/)).toBeInTheDocument();
  });
});

describe("AnsweredBy", () => {
  it("reads cache when nothing else answered", () => {
    render(<AnsweredBy cached upstream="" />);
    expect(screen.getByText("cache")).toBeInTheDocument();
  });

  it("keeps an unknown upstream verbatim", () => {
    render(<AnsweredBy cached={false} upstream="stale (something new)" />);
    expect(screen.getByText("stale (something new)")).toBeInTheDocument();
  });

  it("reads a rebind refusal as a block, and keeps the cache badge", () => {
    render(<AnsweredBy cached upstream="rebind guard → 0.0.0.0" />);
    const label = screen.getByText("rebind guard → 0.0.0.0");
    expect(label.closest("[data-slot=badge]")).toHaveAttribute(
      "title",
      "A public name answered with a private address",
    );
    expect(screen.getByText("cache")).toBeInTheDocument();
  });

  it("reads a cloaked CNAME block as a block, and keeps the cache badge", () => {
    render(<AnsweredBy cached upstream="cname collect.tracker.net · blocklist Ads → 0.0.0.0" />);
    const label = screen.getByText("cname collect.tracker.net · blocklist Ads → 0.0.0.0");
    expect(label.closest("[data-slot=badge]")).toHaveAttribute(
      "title",
      "The answer pointed at a blocked name",
    );
    expect(screen.getByText("cache")).toBeInTheDocument();
  });

  it("shows an em dash when there is nothing to say", () => {
    render(<AnsweredBy cached={false} upstream={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("How big the live buffer is", () => {
  it("quotes the capacity the resolver reports, not a number of its own", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/resolver/recent": recent({ capacity: 42, queries: [] }) });

    render(<QueryLog onUnauthorized={noop} />);

    await user.type(await screen.findByLabelText("Search domains"), "nothing-matches");

    expect(await screen.findByText(/It holds the last 42 queries/)).toBeInTheDocument();
  });
});

const firstSeen = (names: { at: number; device: string; name: string }[], tracked = 40) => ({
  "/api/resolver/recent": recent(),
  "/api/resolver/first-seen": {
    names,
    watching: true,
    tracking: {
      counted: names.length,
      devices: 2,
      tracked,
      capacity: 40_000,
      warmup_s: 3600,
      recent: names.length,
    },
    devices: logDevices,
  },
});

describe("which log you were reading", () => {
  it("survives a refresh, because the tab is in the URL", async () => {
    reducedMotion();
    mockApi(firstSeen([{ at: 1_700_000_000, device: "laptop", name: "brand-new.example" }]));

    const first = render(<QueryLog onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("tab", { name: "New" }));
    await screen.findByText("brand-new.example");

    expect(window.location.search).toContain("log.source=new");

    first.unmount();
    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText("brand-new.example")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "New" })).toHaveAttribute("aria-selected", "true");
  });

  it("opens Live when the URL names a log that does not exist", async () => {
    window.history.replaceState(null, "", "/?log.source=nonsense");
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText("example.com")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live" })).toHaveAttribute("aria-selected", "true");
  });

  it("asks the resolver for the search the URL carries, not for everything", async () => {
    window.history.replaceState(null, "", "/?log.q=ads");
    const api = mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);
    await screen.findByText("example.com");

    expect(await screen.findByLabelText("Search domains")).toHaveValue("ads");
    expect(api.calls.every((call) => call.search.includes("q=ads"))).toBe(true);
  });
});

describe("names asked for the first time", () => {
  it("lists what a device has just met, newest first", async () => {
    reducedMotion();
    mockApi(
      firstSeen([
        { at: 1_700_000_100, device: "laptop", name: "brand-new.example" },
        { at: 1_700_000_000, device: "phone", name: "older.example" },
      ]),
    );

    render(<QueryLog onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("tab", { name: "New" }));

    expect(await screen.findByText("brand-new.example")).toBeInTheDocument();
    expect(screen.getByText("older.example")).toBeInTheDocument();
    expect(screen.getByText(/40 names remembered across 2 devices/)).toBeInTheDocument();
  });

  it("pages at 25 rows", async () => {
    reducedMotion();
    const names = Array.from({ length: 60 }, (_, index) => ({
      at: 1_700_000_000 + index,
      device: "laptop",
      name: `new-${index}.example`,
    }));
    mockApi(firstSeen(names));

    render(<QueryLog onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("tab", { name: "New" }));

    expect(await screen.findByText("new-0.example")).toBeInTheDocument();
    expect(screen.getByText("new-24.example")).toBeInTheDocument();
    expect(screen.queryByText("new-25.example")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("new-25.example")).toBeInTheDocument();
    expect(screen.queryByText("new-24.example")).not.toBeInTheDocument();
  });

  it("explains the quiet hour rather than looking broken", async () => {
    reducedMotion();
    mockApi(firstSeen([], 0));

    render(<QueryLog onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("tab", { name: "New" }));

    expect(await screen.findByText(/watched quietly for an hour first/)).toBeInTheDocument();
  });
});

const suspiciousRoutes = () => ({
  "/api/resolver/recent": recent(),
  "/api/resolver/first-seen": (url: URL) => {
    const all = firstSeenResponse();
    return url.searchParams.get("suspicious") === "true"
      ? { ...all, names: all.names.filter((row) => row.suspicious) }
      : all;
  },
});

const firstSeenCalls = (api: ReturnType<typeof mockApi>) =>
  api.calls.filter((call) => call.path === "/api/resolver/first-seen");

describe("suspicious first-seen names", () => {
  it("marks a machine-generated name and leaves an ordinary one alone", async () => {
    reducedMotion();
    mockApi(suspiciousRoutes());

    render(<QueryLog onUnauthorized={noop} />);
    await userEvent.click(await screen.findByRole("tab", { name: "New" }));

    const odd = (await screen.findByText("xjw4k2qzp9v.com")).closest("tr")!;
    const badge = within(odd).getByText("suspicious 80").closest("[data-slot=badge]")!;
    expect(badge).toHaveAttribute(
      "title",
      "random-looking name, digits mixed into letters, long run without vowels",
    );

    const plain = screen.getByText("fonts.gstatic.com").closest("tr")!;
    expect(within(plain).queryByText(/suspicious/)).not.toBeInTheDocument();
  });

  it("narrows to suspicious names, and the filter lives in the URL", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi(suspiciousRoutes());

    const first = render(<QueryLog onUnauthorized={noop} />);
    await user.click(await screen.findByRole("tab", { name: "New" }));
    await screen.findByText("fonts.gstatic.com");
    expect(firstSeenCalls(api)[0].search).not.toContain("suspicious");

    await user.click(screen.getByRole("radio", { name: "Suspicious" }));

    await waitFor(() => expect(screen.queryByText("fonts.gstatic.com")).not.toBeInTheDocument());
    expect(screen.getByText("xjw4k2qzp9v.com")).toBeInTheDocument();
    expect(window.location.search).toContain("new.show=suspicious");
    expect(firstSeenCalls(api).at(-1)?.search).toContain("suspicious=true");

    first.unmount();
    const before = firstSeenCalls(api).length;
    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText("xjw4k2qzp9v.com")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Suspicious" })).toHaveAttribute("aria-checked", "true");
    expect(firstSeenCalls(api)[before].search).toContain("suspicious=true");
  });

  it("says so when nothing new looks suspicious", async () => {
    reducedMotion();
    window.history.replaceState(null, "", "/?log.source=new&new.show=suspicious");
    mockApi({
      "/api/resolver/recent": recent(),
      "/api/resolver/first-seen": firstSeenResponse({ names: [] }),
    });

    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText("Nothing new looks machine-generated.")).toBeInTheDocument();
  });

  it("puts the reasons in the row's detail on the domain sheet", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      ...suspiciousRoutes(),
      "/api/domain": domainResponse({
        name: "xjw4k2qzp9v.com",
        suspicion: { score: 80, reasons: ["entropy", "digits", "consonants"], threshold: 50 },
      }),
    });

    render(
      <DomainSheetProvider>
        <QueryLog onUnauthorized={noop} />
      </DomainSheetProvider>,
    );
    await user.click(await screen.findByRole("tab", { name: "New" }));
    await user.click(await screen.findByRole("button", { name: "xjw4k2qzp9v.com" }));

    const sheet = await screen.findByRole("dialog");
    expect(
      within(sheet).getByText(
        "80 · random-looking name, digits mixed into letters, long run without vowels",
      ),
    ).toBeInTheDocument();
    expect(await within(sheet).findByText("Suspicion 80")).toBeInTheDocument();
    expect(within(sheet).getByText("How it looks")).toBeInTheDocument();
  });

  it("shows the marker, the reasons and the filter at phone width", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi(suspiciousRoutes());

    render(<QueryLog onUnauthorized={noop} />);
    await user.click(await screen.findByRole("tab", { name: "New" }));

    const odd = await screen.findByRole("button", { name: /^xjw4k2qzp9v\.com/ });
    expect(within(odd).getByText("suspicious 80")).toBeInTheDocument();
    expect(odd).toHaveAccessibleName(/random-looking name/);
    const plain = screen.getByRole("button", { name: /^fonts\.gstatic\.com/ });
    expect(within(plain).getByText("new")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Filter/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: "Suspicious" }));
    await user.click(within(dialog).getByRole("button", { name: "Show results" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^fonts\.gstatic\.com/ })).not.toBeInTheDocument(),
    );
    expect(window.location.search).toContain("new.show=suspicious");
    expect(firstSeenCalls(api).at(-1)?.search).toContain("suspicious=true");

    await user.click(screen.getByRole("button", { name: "Clear suspicious" }));
    await waitFor(() => expect(window.location.search).not.toContain("new.show"));
  });
});
