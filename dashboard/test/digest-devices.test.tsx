import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DomainSheetProvider } from "@/components/DomainSheet";
import DigestDevices from "@/components/DigestDevices";
import Tools, { TOOL_SCREENS } from "@/components/Tools";
import { Alerting } from "@/components/ToolsOps";
import { mockApi } from "./api-mock";
import {
  alertStatus,
  digestDevices,
  digestStatus,
  domainResponse,
  tokenStore,
  topRows,
} from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const domainCalls = (api: ReturnType<typeof mockApi>) =>
  api.calls.filter(
    (call) => call.method === "GET" && call.path === "/api/domain",
  );

function withSheet(children: React.ReactNode) {
  return render(<DomainSheetProvider>{children}</DomainSheetProvider>);
}

describe("the per-device digest", () => {
  it("lays each device out on desktop and opens a blocked name in the domain sheet", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/digest/devices": digestDevices(),
      "/api/domain": domainResponse({ name: "ads.example" }),
    });

    withSheet(<DigestDevices onUnauthorized={noop} />);

    const pixel = await screen.findByRole("region", { name: "Pixel" });
    expect(screen.getByText("Devices this week")).toBeInTheDocument();
    expect(screen.getByText("3 days")).toBeInTheDocument();
    expect(
      within(pixel).getByText("820 queries · 41 blocked · 2 new"),
    ).toBeInTheDocument();
    expect(within(pixel).getByText("blocklist Ads")).toBeInTheDocument();
    expect(within(pixel).getByText("AdGuard")).toBeInTheDocument();
    expect(within(pixel).getByText("schedule Bedtime")).toBeInTheDocument();
    expect(within(pixel).getByText("rebind guard")).toBeInTheDocument();
    expect(
      within(pixel).getByText("cname collect.tracker.net · blocklist Ads"),
    ).toBeInTheDocument();
    expect(
      within(pixel).getByRole("button", { name: "shop.example" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 active devices/)).toBeInTheDocument();

    const laptop = screen.getByRole("region", { name: "Work Laptop" });
    expect(within(laptop).getAllByText("none")).toHaveLength(2);

    await user.click(
      within(pixel).getByRole("button", { name: "ads.example" }),
    );

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(domainCalls(api)[0]?.search).toBe("?name=ads.example"),
    );
  });

  it("lists each device as a group on a phone, and a row opens the domain sheet", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/digest/devices": digestDevices(),
      "/api/domain": domainResponse({ name: "news.example" }),
    });

    withSheet(<DigestDevices onUnauthorized={noop} />);

    expect(await screen.findByText("Pixel")).toBeInTheDocument();
    expect(screen.queryByText("Devices this week")).toBeNull();
    expect(
      screen.getByText("Nothing new and nothing blocked"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /^news\.example, new/ }),
    );

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(domainCalls(api)[0]?.search).toBe("?name=news.example"),
    );
  });

  it("says when no device asked for anything", async () => {
    reducedMotion();
    mockApi({
      "/api/digest/devices": digestDevices({ devices: [], active: 0 }),
    });

    withSheet(<DigestDevices onUnauthorized={noop} />);

    expect(
      await screen.findByText(
        "No device has asked for anything since the last digest.",
      ),
    ).toBeInTheDocument();
  });

  it("sits on the desktop Tools page beside the rankings", async () => {
    reducedMotion();
    mockApi({
      "/api/stats/top": topRows(),
      "/api/tokens": {
        tokens: [],
        store: tokenStore(),
        scopes: ["read", "metrics"],
      },
      "/api/alerts": {
        alerts: alertStatus(),
        webhook_set: false,
        digest: digestStatus(),
      },
      "/api/digest/devices": digestDevices(),
    });

    withSheet(<Tools onUnauthorized={noop} />);

    expect(await screen.findByText("Devices this week")).toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "Pixel" }),
    ).toBeInTheDocument();
  });

  it("is a screen of its own on a phone, reached from the digest's group", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/alerts": {
        alerts: alertStatus(),
        webhook_set: false,
        digest: digestStatus({ enabled: true }),
      },
    });
    window.location.hash = "#tools/alerting";

    render(<Alerting onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /By device/ }));

    expect(window.location.hash).toBe("#tools/digest");
    expect(TOOL_SCREENS.some((entry) => entry.key === "digest")).toBe(true);
  });
});
