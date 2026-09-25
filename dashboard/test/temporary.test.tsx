import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Devices from "@/components/Devices";
import { DomainSheetProvider, useOpenDomain } from "@/components/DomainSheet";
import { readTemporary, splitRules, timeLeft } from "@/lib/temporary";
import { mockApi } from "./api-mock";
import {
  deviceRuleEntryResult,
  devices,
  domainResponse,
  health,
  nativeDns,
  rewrites,
} from "./fixtures";
import { phoneWidth } from "./media";

const noop = () => {};

const stampIn = (seconds: number) =>
  `${new Date(Math.floor(Date.now() / 1000 + seconds) * 1000).toISOString().slice(0, 19)}Z`;

function Opener({ name, device }: { name: string; device?: string }) {
  const openDomain = useOpenDomain();
  return (
    <button type="button" onClick={() => openDomain(name, { device })}>
      open {name}
    </button>
  );
}

const temporarilyAllowed = () =>
  domainResponse({
    devices: domainResponse().devices.map((device) =>
      device.id === "tv"
        ? {
            ...device,
            decision: {
              layer: "allowed",
              label: "allowed by @@ device",
              blocked: false,
              source: null,
              rule: "example.com",
              expires_at: Date.now() / 1000 + 43 * 60 + 20,
            },
            blocked: false,
          }
        : device,
    ),
  });

describe("temporary entry helpers", () => {
  it("reads a line with an expiry and splits it from the permanent ones", () => {
    const line = `@@ads.example$expires=${stampIn(3600)}`;
    expect(readTemporary(line)).toMatchObject({
      domain: "ads.example",
      allow: true,
      line,
    });
    expect(readTemporary("ads.example")).toBeNull();

    const { permanent, temporary } = splitRules([
      "keep.example",
      line,
      `gone.example$expires=${stampIn(-60)}`,
    ]);
    expect(permanent).toEqual(["keep.example"]);
    expect(temporary.map((entry) => entry.domain)).toEqual(["ads.example"]);
  });

  it("says how long is left", () => {
    const now = 1_790_000_000;
    expect(timeLeft(now + 43 * 60, now)).toBe("43m left");
    expect(timeLeft(now + 7 * 3600 + 12 * 60, now)).toBe("7h 12m left");
    expect(timeLeft(now + 20, now)).toBe("1m left");
    expect(timeLeft(now - 1, now)).toBe("lapsed");
  });
});

describe("a duration on the domain sheet", () => {
  it("allows on a device for an hour, sending the minutes", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "/api/domain": domainResponse(),
      "POST /api/devices/laptop/rules/entry": deviceRuleEntryResult("laptop", []),
    });

    render(
      <DomainSheetProvider>
        <Opener name="example.com" device="laptop" />
      </DomainSheetProvider>,
    );
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("On each device");

    await user.click(within(sheet).getByRole("radio", { name: "1h" }));
    await user.click(
      within(sheet).getByRole("button", {
        name: "Allow on Work Laptop for 1 hour",
      }),
    );

    await waitFor(() =>
      expect(
        api.calls.find((call) => call.path === "/api/devices/laptop/rules/entry")?.body,
      ).toEqual({ domain: "example.com", action: "allow", minutes: 60 }),
    );
  });

  it("shows the time left and still offers the permanent write and clear", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/domain": temporarilyAllowed() });

    render(
      <DomainSheetProvider>
        <Opener name="example.com" device="tv" />
      </DomainSheetProvider>,
    );
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    const sheet = await screen.findByRole("dialog");

    expect(await within(sheet).findByText("allowed by @@ device · 43m left")).toBeInTheDocument();
    expect(
      within(sheet).getByRole("button", { name: "Allow on Living room TV" }),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByRole("button", { name: "Clear on Living room TV" }),
    ).toBeInTheDocument();
  });

  it("keeps the duration choice at the 44px floor on a phone", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/domain": domainResponse() });

    render(
      <DomainSheetProvider>
        <Opener name="example.com" />
      </DomainSheetProvider>,
    );
    await user.click(screen.getByRole("button", { name: "open example.com" }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("On each device");

    for (const option of within(sheet).getAllByRole("radio")) {
      expect(option).toHaveClass("max-wide:h-11");
    }
  });
});

describe("temporary entries on the device screen", () => {
  it("lists them with their time left, keeps them out of the textarea and on save", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const line = `@@video.example$expires=${stampIn(3 * 3600 + 120)}`;
    const base = devices();
    const api = mockApi({
      "/api/devices": {
        ...base,
        devices: base.devices.map((device) =>
          device.id === "laptop"
            ? {
                ...device,
                rules: ["tracking.example", line, `old.example$expires=${stampIn(-5)}`],
              }
            : device,
        ),
      },
      "/api/health": health(),
      "/api/rewrites": rewrites(),
      "/api/native-dns": nativeDns(),
      "PUT /api/devices/laptop/rules": { ok: true },
    });

    render(<Devices onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));
    await user.click(await screen.findByRole("button", { name: /^Rules/ }));

    const editor = screen.getByLabelText("Rules for Work Laptop");
    expect(editor).toHaveValue("tracking.example");
    expect(screen.getByText("video.example")).toBeInTheDocument();
    expect(screen.getByText(/^allowed · 3h [12]m left$/)).toBeInTheDocument();
    expect(screen.queryByText("old.example")).not.toBeInTheDocument();

    await user.type(editor, "\nads.example");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        api.calls.find((call) => call.method === "PUT" && call.path === "/api/devices/laptop/rules")
          ?.body,
      ).toEqual({ rules: ["tracking.example", "ads.example", line] }),
    );
  });

  for (const phone of [true, false]) {
    it(`removes one entry through the locked entry route on ${phone ? "a phone" : "a desktop"}`, async () => {
      if (phone) phoneWidth();
      const user = userEvent.setup();
      const line = `@@video.example$expires=${stampIn(3600)}`;
      const base = devices();
      const api = mockApi({
        "/api/devices": {
          ...base,
          devices: base.devices.map((device) =>
            device.id === "laptop"
              ? { ...device, rules: ["video.example", "tracking.example", line] }
              : device,
          ),
        },
        "/api/health": health(),
        "/api/rewrites": rewrites(),
        "/api/native-dns": nativeDns(),
        "POST /api/devices/laptop/rules/entry": deviceRuleEntryResult("laptop", [
          "video.example",
          "tracking.example",
        ]),
      });

      render(<Devices onUnauthorized={noop} />);
      if (phone) await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));
      await user.click(await screen.findByRole("button", { name: /^Rules/ }));
      await user.type(screen.getByLabelText("Rules for Work Laptop"), " unsaved.example");
      await user.click(screen.getByRole("button", { name: "Remove video.example" }));

      await waitFor(() =>
        expect(
          api.calls.find((call) => call.path === "/api/devices/laptop/rules/entry")?.body,
        ).toEqual({ domain: "video.example", action: "clear", temporary: true }),
      );
      expect(api.calls.some((call) => call.method === "PUT")).toBe(false);
    });
  }
});
