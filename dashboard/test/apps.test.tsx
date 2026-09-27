import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import Apps, { forgetCatalog } from "@/components/Apps";
import { mockApi } from "./api-mock";
import { appCatalog, apps } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

beforeEach(() => forgetCatalog());

describe("Apps on a phone", () => {
  it("lists each blocked app with who it reaches and when", async () => {
    phoneWidth();
    mockApi({ "/api/apps": apps(), "/api/apps/catalog": appCatalog() });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);

    expect(await screen.findByRole("heading", { name: "Apps" })).toBeInTheDocument();
    expect(screen.getByText(/kids · 9 PM–7 AM, every day/)).toBeInTheDocument();
    expect(screen.getByText("every device · always")).toBeInTheDocument();
    expect(screen.getByText("on now")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Block YouTube" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Block TikTok" })).not.toBeChecked();
  });

  it("offers the apps seen on the network that are not blocked yet", async () => {
    phoneWidth();
    mockApi({ "/api/apps": apps(), "/api/apps/catalog": appCatalog() });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);

    const seen = await screen.findByRole("heading", { name: "Seen on your network" });
    const group = seen.closest("section") as HTMLElement;
    expect(within(group).getByText("Roblox")).toBeInTheDocument();
    expect(within(group).getByText(/2 devices · 5m ago/)).toBeInTheDocument();
    expect(within(group).queryByText("YouTube")).not.toBeInTheDocument();
  });

  it("switches a block off without forgetting its devices and hours", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/apps": apps(),
      "/api/apps/catalog": appCatalog(),
      "PUT /api/apps/youtube": apps().blocks[0],
    });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);
    await user.click(await screen.findByRole("switch", { name: "Block YouTube" }));

    await waitFor(() => expect(api.calls.some((call) => call.method === "PUT")).toBe(true));
    const put = api.calls.find((call) => call.method === "PUT");
    expect(put?.path).toBe("/api/apps/youtube");
    expect(put?.body).toEqual({
      tags: ["kids"],
      days: [],
      starts_at: "21:00",
      ends_at: "07:00",
      enabled: false,
    });
  });

  it("opens the editor from a row and saves a schedule", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/apps": apps(),
      "/api/apps/catalog": appCatalog(),
      "PUT /api/apps/tiktok": apps().blocks[1],
    });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /TikTok/ }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/apps/tiktok"));
    expect(screen.getByRole("heading", { name: "TikTok" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Scheduled" }));
    await user.click(screen.getByRole("button", { name: "Sat" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.calls.some((call) => call.method === "PUT")).toBe(true));
    expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
      tags: [],
      days: [5],
      starts_at: "21:00",
      ends_at: "07:00",
      enabled: false,
    });
  });

  it("says an app is not blocked when its editor is opened directly", async () => {
    phoneWidth();
    mockApi({ "/api/apps": apps(), "/api/apps/catalog": appCatalog() });
    window.history.replaceState(null, "", "#rules/apps/discord");

    render(<Apps onUnauthorized={noop} />);

    expect(await screen.findByText("This app is not blocked.")).toBeInTheDocument();
  });
});

describe("The app catalog", () => {
  it("searches, filters by category and pages the list", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/apps": apps(), "/api/apps/catalog": appCatalog() });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Block an app" }));

    const sheet = await screen.findByRole("dialog");
    const list = () => sheet.querySelector('[data-slot="catalog-list"]') as HTMLElement;
    expect(within(list()).getAllByRole("switch")).toHaveLength(24);
    expect(within(sheet).getByText("24 of 34")).toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "Show more" }));
    expect(within(list()).getAllByRole("switch")).toHaveLength(34);

    await user.click(within(sheet).getByRole("button", { name: /^Social/ }));
    expect(within(list()).getAllByRole("switch")).toHaveLength(2);

    await user.type(within(sheet).getByRole("searchbox", { name: "Search apps" }), "tik");
    expect(within(list()).getByText("TikTok")).toBeInTheDocument();
    expect(within(list()).queryByText("Discord")).not.toBeInTheDocument();

    await user.clear(within(sheet).getByRole("searchbox", { name: "Search apps" }));
    await user.type(within(sheet).getByRole("searchbox", { name: "Search apps" }), "zzz");
    expect(within(sheet).getByText("No app matches.")).toBeInTheDocument();
  });

  it("blocks an app everywhere when it is switched on, and removes it when switched off", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/apps": apps(),
      "/api/apps/catalog": appCatalog(),
      "PUT /api/apps/discord": { ...apps().blocks[1], id: "discord", name: "Discord" },
      "DELETE /api/apps/youtube": { removed: true, id: "youtube" },
    });

    render(<Apps onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Block an app" }));
    const sheet = await screen.findByRole("dialog");

    await user.click(within(sheet).getByRole("switch", { name: "Block Discord" }));
    await user.click(within(sheet).getByRole("switch", { name: "Block YouTube" }));

    await waitFor(() => expect(api.calls.filter((c) => c.method !== "GET")).toHaveLength(2));
    const put = api.calls.find((call) => call.method === "PUT");
    expect(put?.path).toBe("/api/apps/discord");
    expect(put?.body).toEqual({ tags: [], days: [], starts_at: "", ends_at: "", enabled: true });
    expect(api.calls.find((call) => call.method === "DELETE")?.path).toBe("/api/apps/youtube");
  });

  it("starts on the apps seen on the network when asked to show them all", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const seen = Array.from({ length: 7 }, (_, index) => ({
      id: `game${index}`,
      devices: 1,
      last_at: Date.now() / 1000 - index * 60,
    }));
    mockApi({ "/api/apps": apps({ seen }), "/api/apps/catalog": appCatalog() });
    window.history.replaceState(null, "", "#rules/apps");

    render(<Apps onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Show all 7" }));

    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("button", { name: /^Seen/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const list = sheet.querySelector('[data-slot="catalog-list"]') as HTMLElement;
    expect(within(list).getAllByRole("switch")).toHaveLength(7);
  });
});

describe("Apps on a desktop", () => {
  it("shows the card with each block and opens its editor inline", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({ "/api/apps": apps(), "/api/apps/catalog": appCatalog() });

    render(<Apps onUnauthorized={noop} />);

    expect(await screen.findByText("2 blocked · 34 in the catalog")).toBeInTheDocument();
    expect(screen.getByText("kids · 9 PM–7 AM, every day · 1,204 answers")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    expect(screen.getByRole("radio", { name: "Scheduled" })).toBeChecked();
    expect(screen.getByText(/Living room TV/)).toBeInTheDocument();
  });

  it("is read-only without a writable registry", async () => {
    reducedMotion();
    mockApi({ "/api/apps": apps({ editable: false }), "/api/apps/catalog": appCatalog() });

    render(<Apps onUnauthorized={noop} />);

    expect(await screen.findByText(/can be read but not changed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Block an app/ })).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Block YouTube" })).toBeDisabled();
  });
});
