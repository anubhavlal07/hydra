import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Blocklists from "@/components/Blocklists";
import Policy from "@/components/Policy";
import QueryLog from "@/components/QueryLog";
import { mockApi } from "./api-mock";
import { blocklistPresets, blocklists, policy, recent, ruleGroups } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const hagezi = () => blocklists().lists[0];

describe("Hydra blocklists on a phone", () => {
  it("lists each one with its size, its tags and when it was fetched", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByRole("heading", { name: "Hydra blocklists" })).toBeInTheDocument();
    expect(screen.getByText("124,301 names · kids, tv · 3h ago")).toBeInTheDocument();
    expect(screen.getByText("5,120 names · every device · 20h ago")).toBeInTheDocument();
    expect(screen.getByText("129,421 of 400,000 names · 3 of 8 lists")).toBeInTheDocument();
  });

  it("shows a failing list's error on its row", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /Broken/ });
    expect(within(row).getByText("HTTP 404 from lists.example.net")).toBeVisible();
  });

  it("says there is nothing yet", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists({ lists: [], total_names: 0 }) });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("No list yet.")).toBeInTheDocument();
  });

  it("opens the editor filled with what the list holds", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /hagezi-pro/ }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/lists/hagezi-pro"));
    expect(screen.getByRole("heading", { name: "hagezi-pro" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("hagezi-pro");
    expect(screen.getByLabelText("URL")).toHaveValue(hagezi().url);
    expect(screen.getByText("1 device")).toBeInTheDocument();
    expect(screen.getByText("124,301")).toBeInTheDocument();
    expect(screen.getByText("3h ago")).toBeInTheDocument();
    expect(screen.getByText("cdn.example.com")).toBeInTheDocument();

    const tags = screen.getByRole("group", { name: "Tags" });
    expect(within(tags).getByRole("button", { name: "kids" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(tags).getByRole("button", { name: "work" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("says no devices when the tags match nothing", async () => {
    phoneWidth();
    mockApi({
      "/api/blocklists": blocklists({
        lists: [{ ...hagezi(), tags: ["tablet"], reaches: [] }],
      }),
    });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("no devices")).toBeInTheDocument();
    expect(screen.getByText(/No device carries tablet/)).toBeInTheDocument();
  });

  it("shows the error inside the editor too", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists/broken");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("HTTP 404 from lists.example.net")).toBeVisible();
    expect(screen.getByText("never fetched")).toBeInTheDocument();
  });

  it("writes the moment the switch is flipped, with the saved fields", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "PUT /api/blocklists/hagezi-pro": { ...hagezi(), enabled: false },
    });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);
    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Later");
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        name: "hagezi-pro",
        url: hagezi().url,
        tags: ["kids", "tv"],
        allow: ["cdn.example.com"],
        enabled: false,
      }),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Later");
  });

  it("puts the switch back when the write is refused", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("switch", { name: "Enabled" }));

    expect(await screen.findByText(/No mock for PUT/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("saves name, URL and tags from the header", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "PUT /api/blocklists/hagezi-pro": hagezi(),
    });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);
    const tags = await screen.findByRole("group", { name: "Tags" });
    await user.click(within(tags).getByRole("button", { name: "work" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { tags: string[] }).tags,
      ).toEqual(["kids", "tv", "work"]),
    );
  });

  it("removes an exception", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "POST /api/blocklists/hagezi-pro/allow": { ...hagezi(), allow: [] },
    });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Remove cdn.example.com" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "POST")?.body).toEqual({
        domain: "cdn.example.com",
        action: "remove",
      }),
    );
  });

  it("refreshes now", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "POST /api/blocklists/broken/refresh": blocklists().lists[2],
    });
    window.history.replaceState(null, "", "#rules/lists/broken");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Refresh now/ }));

    await waitFor(() =>
      expect(api.calls.some((call) => call.path === "/api/blocklists/broken/refresh")).toBe(true),
    );
  });

  it("reads the list every two seconds after a new URL until its fetch lands", async () => {
    phoneWidth();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const broken = blocklists().lists[2];
      const moved = { ...broken, url: "https://lists.example.net/moved.txt", last_error: "" };
      let state = broken;
      const api = mockApi({
        "/api/blocklists": () => blocklists({ lists: [...blocklists().lists.slice(0, 2), state] }),
        "PUT /api/blocklists/broken": () => {
          state = moved;
          return moved;
        },
      });
      window.history.replaceState(null, "", "#rules/lists/broken");

      render(<Blocklists onUnauthorized={noop} />);
      const url = await screen.findByLabelText("URL");
      await user.clear(url);
      await user.type(url, moved.url);
      await user.click(screen.getByRole("button", { name: "Save" }));

      const row = await screen.findByRole("button", { name: /Broken/ });
      expect(await within(row).findByText("fetching…")).toBeInTheDocument();
      expect(within(row).queryByText("HTTP 404 from lists.example.net")).not.toBeInTheDocument();

      const reads = () => api.calls.filter((call) => call.path === "/api/blocklists").length;
      const before = reads();
      await vi.advanceTimersByTimeAsync(2_000);
      await waitFor(() => expect(reads()).toBe(before + 1));
      expect(within(screen.getByRole("button", { name: /Broken/ })).getByText("fetching…")).toBeInTheDocument();

      state = { ...moved, names: 900, loaded: true, fetched_at: Date.now() / 1000 };
      await vi.advanceTimersByTimeAsync(2_000);
      await waitFor(() =>
        expect(
          within(screen.getByRole("button", { name: /Broken/ })).getByText(/^900 names/),
        ).toBeInTheDocument(),
      );

      const settled = reads();
      await vi.advanceTimersByTimeAsync(6_000);
      expect(reads()).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops reading after thirty seconds when the fetch never lands", async () => {
    phoneWidth();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const added = { ...blocklists().lists[2], id: "oisd", name: "OISD", last_error: "" };
      let lists: (typeof added)[] = [];
      const api = mockApi({
        "/api/blocklists": () => blocklists({ lists }),
        "PUT /api/blocklists/oisd": () => {
          lists = [added];
          return added;
        },
      });
      window.history.replaceState(null, "", "#rules/lists");

      render(<Blocklists onUnauthorized={noop} />);
      await user.click(await screen.findByRole("button", { name: "Add a blocklist" }));
      const sheet = await screen.findByRole("dialog");
      await user.type(within(sheet).getByLabelText("Name"), "OISD");
      await user.type(within(sheet).getByLabelText("URL"), "https://small.oisd.nl/domainswild");
      await user.click(within(sheet).getByRole("button", { name: "Add list" }));

      expect(await screen.findByText("fetching…")).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(30_000);
      await waitFor(() => expect(screen.queryByText("fetching…")).not.toBeInTheDocument());

      const reads = () => api.calls.filter((call) => call.path === "/api/blocklists").length;
      const settled = reads();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(reads()).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks before deleting a list", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "DELETE /api/blocklists/hagezi-pro": { removed: true, id: "hagezi-pro" },
    });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Delete list/ }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete list" }),
    );

    await waitFor(() => expect(api.calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  it("adds a list from a sheet opened by the header button", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "PUT /api/blocklists/oisd-small": hagezi(),
    });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a blocklist" }));

    const sheet = await screen.findByRole("dialog");
    const add = within(sheet).getByRole("button", { name: "Add list" });
    expect(add).toBeDisabled();

    await user.type(within(sheet).getByLabelText("Name"), "OISD small");
    await user.type(within(sheet).getByLabelText("URL"), "http://example.com/list.txt");
    expect(within(sheet).getByText("Needs an https:// address.")).toBeInTheDocument();
    expect(add).toBeDisabled();

    await user.clear(within(sheet).getByLabelText("URL"));
    await user.type(within(sheet).getByLabelText("URL"), "https://small.oisd.nl/domainswild");
    await user.click(within(within(sheet).getByRole("group", { name: "Tags" })).getByRole("button", { name: "kids" }));
    await user.click(add);

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        name: "OISD small",
        url: "https://small.oisd.nl/domainswild",
        tags: ["kids"],
        allow: [],
        enabled: true,
      }),
    );
  });

  it("offers nothing to change when the registry is read-only", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists({ editable: false }) });
    window.history.replaceState(null, "", "#rules/lists/hagezi-pro");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText(/lists can be read but not changed/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Delete list/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove cdn.example.com" })).not.toBeInTheDocument();
  });

  it("hides the add button when the registry is read-only", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists({ editable: false }) });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    await screen.findByText("hagezi-pro");
    expect(screen.queryByRole("button", { name: "Add a blocklist" })).not.toBeInTheDocument();
  });
});

describe("Hydra blocklists on the desktop", () => {
  it("shows each list with its status and its error in the open", async () => {
    reducedMotion();
    mockApi({ "/api/blocklists": blocklists() });

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("Hydra blocklists")).toBeInTheDocument();
    expect(screen.getByText("124,301 names · kids, tv · 3h ago")).toBeInTheDocument();
    expect(screen.getByText("HTTP 404 from lists.example.net")).toBeVisible();
    expect(screen.getByText("failing")).toBeInTheDocument();
  });

  it("says there is nothing yet", async () => {
    reducedMotion();
    mockApi({ "/api/blocklists": blocklists({ lists: [], total_names: 0 }) });

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("No list yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add a blocklist/ })).toBeInTheDocument();
  });

  it("asks before deleting a list", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "DELETE /api/blocklists/hagezi-pro": { removed: true, id: "hagezi-pro" },
    });

    render(<Blocklists onUnauthorized={noop} />);
    await screen.findByText("hagezi-pro");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await user.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete hagezi-pro?")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete list" }),
    );

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "DELETE")?.path).toBe(
        "/api/blocklists/hagezi-pro",
      ),
    );
  });

  it("writes the switch immediately from the open row", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "PUT /api/blocklists/hagezi-pro": { ...hagezi(), enabled: false },
    });

    render(<Blocklists onUnauthorized={noop} />);
    await screen.findByText("hagezi-pro");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { enabled: boolean }).enabled,
      ).toBe(false),
    );
  });
});

describe("Blocklist presets", () => {
  const pro = () => blocklistPresets().presets[0];

  it("fills the sheet from a preset in one tap and adds it", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "/api/blocklists/presets": blocklistPresets(),
      "PUT /api/blocklists/hagezi-pro-2": hagezi(),
    });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a blocklist" }));
    const sheet = await screen.findByRole("dialog");
    const presets = await within(sheet).findByRole("group", { name: "Presets" });

    const choice = within(presets).getByRole("button", { name: "HaGeZi Pro, ~228k names" });
    expect(choice).toHaveAttribute("aria-pressed", "false");
    await user.click(choice);

    expect(within(sheet).getByLabelText("Name")).toHaveValue("HaGeZi Pro");
    expect(within(sheet).getByLabelText("URL")).toHaveValue(pro().url);
    expect(choice).toHaveAttribute("aria-pressed", "true");

    await user.click(within(sheet).getByRole("button", { name: "Add list" }));
    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        name: "HaGeZi Pro",
        url: pro().url,
        tags: [],
        allow: [],
        enabled: true,
      }),
    );
  });

  it("disables a preset that would not fit or is already added, and says why", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/blocklists": blocklists(),
      "/api/blocklists/presets": blocklistPresets(),
    });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a blocklist" }));
    const presets = await screen.findByRole("group", { name: "Presets" });

    const tif = within(presets).getByRole("button", { name: /^HaGeZi TIF/ });
    expect(tif).toBeDisabled();
    expect(within(tif).getByText("~2.3M names · 38 MiB, over HYDRA_BLOCKLIST_MAX_BYTES")).toBeVisible();
    const oisd = within(presets).getByRole("button", { name: "OISD small, ~56k names, already added" });
    expect(oisd).toBeDisabled();
  });

  it("still adds by hand when the catalog cannot be read", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a blocklist" }));
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByLabelText("URL")).toBeInTheDocument();
    expect(within(sheet).queryByRole("group", { name: "Presets" })).not.toBeInTheDocument();
  });

  it("fills the desktop form from a preset", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/blocklists": blocklists(),
      "/api/blocklists/presets": blocklistPresets(),
      "PUT /api/blocklists/hagezi-pro-2": hagezi(),
    });

    render(<Blocklists onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Add a blocklist/ }));
    const presets = await screen.findByRole("group", { name: "Presets" });
    await user.click(within(presets).getByRole("button", { name: "HaGeZi Pro, ~228k names" }));

    expect(screen.getByLabelText("List name")).toHaveValue("HaGeZi Pro");
    expect(screen.getByLabelText("List URL")).toHaveValue(pro().url);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.path).toBe(
        "/api/blocklists/hagezi-pro-2",
      ),
    );
  });
});

describe("Overlap between lists", () => {
  const overlapping = (proEnabled = true) => {
    const [pro, trackers, broken] = blocklists().lists;
    return blocklists({
      lists: [
        {
          ...pro,
          enabled: proEnabled,
          overlap: [
            { id: "trackers", shared: 4_700 },
            { id: "broken", shared: 0 },
          ],
          unique: 119_601,
        },
        { ...trackers, overlap: [{ id: "hagezi-pro", shared: 4_700 }], unique: 420 },
        broken,
      ],
    });
  };

  it("says in the editor how much a list shares and how much only it holds", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": overlapping() });
    window.history.replaceState(null, "", "#rules/lists/trackers");

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("shares 91% with hagezi-pro · 420 unique")).toBeInTheDocument();
  });

  it("flags a list another enabled list almost wholly covers", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": overlapping() });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    const trackers = await screen.findByRole("button", { name: /Trackers/ });
    expect(within(trackers).getByText("covered")).toBeInTheDocument();
    expect(within(trackers).getByTitle("91% of its names are in hagezi-pro")).toBeInTheDocument();
    const pro = screen.getByRole("button", { name: /hagezi-pro/ });
    expect(within(pro).queryByText("covered")).not.toBeInTheDocument();
  });

  it("does not flag a list whose cover is switched off", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": overlapping(false) });
    window.history.replaceState(null, "", "#rules/lists");

    render(<Blocklists onUnauthorized={noop} />);

    const trackers = await screen.findByRole("button", { name: /Trackers/ });
    expect(within(trackers).queryByText("covered")).not.toBeInTheDocument();
  });

  it("shows the same on the desktop", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({ "/api/blocklists": overlapping() });

    render(<Blocklists onUnauthorized={noop} />);

    expect(await screen.findByText("covered")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    expect(screen.getByText("shares 3% with Trackers · 119,601 unique")).toBeInTheDocument();
  });

  it("says nothing before the overlap is measured", async () => {
    phoneWidth();
    mockApi({ "/api/blocklists": blocklists() });
    window.history.replaceState(null, "", "#rules/lists/trackers");

    render(<Blocklists onUnauthorized={noop} />);

    await screen.findByText("Answers given");
    expect(screen.queryByText("Overlap")).not.toBeInTheDocument();
  });
});

describe("The two kinds of blocklist on the Rules page", () => {
  it("names both on a phone, and drills into Hydra's", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/policy": policy(),
      "/api/rule-groups": ruleGroups(),
      "/api/blocklists": blocklists(),
    });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByRole("button", { name: /AdGuard blocklists/ })).toBeInTheDocument();
    const hydra = await screen.findByRole("button", { name: /Hydra blocklists/ });
    await within(hydra).findByText("3 lists");
    expect(within(hydra).getByText("1 list is failing")).toBeInTheDocument();

    await user.click(hydra);
    await waitFor(() => expect(window.location.hash).toBe("#rules/lists"));
    expect(await screen.findByText("124,301 names · kids, tv · 3h ago")).toBeInTheDocument();
  });

  it("names both on the desktop", async () => {
    reducedMotion();
    mockApi({
      "/api/policy": policy(),
      "/api/rule-groups": ruleGroups(),
      "/api/blocklists": blocklists(),
    });

    render(<Policy onUnauthorized={noop} />);

    expect(await screen.findByText("AdGuard blocklists")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Hydra blocklists" })).toBeInTheDocument();
  });
});

describe("A blocklist answer in the log", () => {
  it("reads as the list that answered, in the table", async () => {
    reducedMotion();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);

    expect(await screen.findByText("blocklist hagezi-pro → 0.0.0.0")).toBeInTheDocument();
  });

  it("reads as the list that answered, on a phone row", async () => {
    phoneWidth();
    mockApi({ "/api/resolver/recent": recent() });

    render(<QueryLog onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /ads\.tracker\.example/ });
    expect(row).toHaveAccessibleName(/blocklist hagezi-pro → 0\.0\.0\.0/);
  });
});
