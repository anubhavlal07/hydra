import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Devices from "@/components/Devices";
import Overview from "@/components/Overview";
import StatusStrip from "@/components/StatusStrip";
import { quietFor } from "@/lib/format";
import { mockApi } from "./api-mock";
import {
  devices,
  health,
  nativeDns,
  overview,
  resolverDetail,
  rewrites,
  statsTimeline,
} from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};
const QUIET =
  "Work Laptop has stopped querying Hydra; its Private DNS or profile may be off";

const sevenHoursAgo = () => Date.now() / 1000 - 7 * 3600 - 120;

const quietFleet = () => {
  const base = devices();
  return devices({ devices: [{ ...base.devices[0], quiet_since: sevenHoursAgo() }] });
};

const routes = (over: Record<string, unknown> = {}) => ({
  "/api/devices": quietFleet(),
  "/api/health": health({ attention: [QUIET] }),
  "/api/rewrites": rewrites(),
  "/api/native-dns": nativeDns(),
  ...over,
});

describe("quietFor", () => {
  it("names the gap in the largest whole unit", () => {
    const now = 1_700_000_000;
    expect(quietFor(now - 90, now)).toBe("Quiet for 1m");
    expect(quietFor(now - 25 * 60, now)).toBe("Quiet for 25m");
    expect(quietFor(now - 7 * 3600 - 59, now)).toBe("Quiet for 7h");
    expect(quietFor(now - 3 * 86400, now)).toBe("Quiet for 3d");
  });
});

describe("a quiet device", () => {
  it("is badged on its card", async () => {
    reducedMotion();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    expect(await screen.findByText("Quiet for 7h")).toBeInTheDocument();
    expect(screen.getByText("310 queries")).toBeInTheDocument();
  });

  it("is not badged once it queries again", async () => {
    reducedMotion();
    mockApi(routes({ "/api/devices": devices() }));

    render(<Devices onUnauthorized={noop} />);

    await screen.findByText("310 queries");
    expect(screen.queryByText(/Quiet for/)).not.toBeInTheDocument();
  });

  it("leads its row on a phone", async () => {
    phoneWidth();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /Work Laptop/ });
    expect(within(row).getByText("Quiet for 7h · work · 310 queries")).toBeInTheDocument();
  });

  it("says so at the top of its screen on a phone", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));

    await waitFor(() => expect(window.location.hash).toBe("#devices/laptop"));
    expect(screen.getByText("Quiet for 7h.")).toBeInTheDocument();
    expect(screen.getByText(/Last query at/)).toBeInTheDocument();
  });

  it("is one of the things the status strip lists", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({ "/api/health": health({ attention: [QUIET] }) });

    render(<StatusStrip />);
    await user.click(await screen.findByRole("button", { name: /1 thing/ }));

    expect(screen.getByText(QUIET)).toBeInTheDocument();
  });

  it("is one of the reasons the overview gives, on a phone", async () => {
    phoneWidth();
    const parts = {
      overview: overview({ attention: [QUIET] }),
      resolver: resolverDetail(),
      health: health(),
    };
    mockApi({
      "/api/overview": parts.overview,
      "/api/resolver": parts.resolver,
      "/api/health": parts.health,
      "/api/pulse": parts,
      "/api/stats/timeline": statsTimeline(),
    });

    render(<Overview onUnauthorized={noop} />);

    expect(await screen.findByText(QUIET)).toBeInTheDocument();
  });
});
