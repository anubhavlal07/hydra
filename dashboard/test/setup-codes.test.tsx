import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import jsQR from "jsqr";
import { afterEach, describe, expect, it, vi } from "vitest";

import Devices from "@/components/Devices";
import { SetupCodes } from "@/components/SetupCodes";
import { qrMatrix, qrPath } from "@/lib/qr";
import type { DeviceConfig } from "@/lib/types";
import { mockApi } from "./api-mock";
import { devices, health, nativeDns, rewrites } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

const device = (over: Partial<DeviceConfig> = {}): DeviceConfig => ({
  ...devices().devices[0],
  ...over,
});

const SETUP = "https://api.dns.example/setup/abcdefgh12345678";
const DOT = "abcdefgh12345678.dot.example";
const DOH = "https://api.dns.example/dns-query/abcdefgh12345678";

const SCALE = 4;

function hex(color: string | null): [number, number, number] {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color ?? "");
  if (!match) throw new Error(`not a literal colour: ${color}`);
  return [parseInt(match[1], 16), parseInt(match[2], 16), parseInt(match[3], 16)];
}

function rasterise(svg: SVGSVGElement) {
  const size = Number(svg.getAttribute("viewBox")?.split(" ")[2]);
  const light = hex(svg.querySelector("rect")?.getAttribute("fill") ?? null);
  const shape = svg.querySelector("path");
  const dark = hex(shape?.getAttribute("fill") ?? null);
  const cells = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));

  for (const [, x, y, run] of (shape?.getAttribute("d") ?? "").matchAll(
    /M(\d+) (\d+)h(\d+)v1h-\d+z/g,
  )) {
    for (let step = 0; step < Number(run); step += 1) cells[Number(y)][Number(x) + step] = true;
  }

  const width = size * SCALE;
  const pixels = new Uint8ClampedArray(width * width * 4);
  for (let py = 0; py < width; py += 1) {
    for (let px = 0; px < width; px += 1) {
      const [r, g, b] = cells[Math.floor(py / SCALE)][Math.floor(px / SCALE)] ? dark : light;
      const at = (py * width + px) * 4;
      pixels[at] = r;
      pixels[at + 1] = g;
      pixels[at + 2] = b;
      pixels[at + 3] = 255;
    }
  }
  return { pixels, width };
}

function decode(svg: SVGSVGElement): string | null {
  const { pixels, width } = rasterise(svg);
  return jsQR(pixels, width, width, { inversionAttempts: "dontInvert" })?.data ?? null;
}

const qr = () => screen.getByRole("img", { name: /^QR code/ }) as unknown as SVGSVGElement;

function stubClipboard() {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  return writeText;
}

describe("QR encoding", () => {
  it("draws a matrix a real decoder reads back, quiet zone included", () => {
    for (const text of [SETUP, DOT, DOH]) {
      const { container } = render(
        <svg role="img" aria-label="QR code probe" viewBox={`0 0 ${qrMatrix(text).size + 8} 1`}>
          <rect fill="#ffffff" />
          <path d={qrPath(qrMatrix(text).modules)} fill="#000000" />
        </svg>,
      );
      expect(decode(container.querySelector("svg") as SVGSVGElement)).toBe(text);
      container.remove();
    }
  });

  it("fails to decode when the modules are wrong, so the round trip is not vacuous", () => {
    const { modules } = qrMatrix(DOH);
    const broken = modules.map((row, y) => row.map((cell, x) => (y < 9 && x < 9 ? !cell : cell)));
    const { container } = render(
      <svg role="img" aria-label="QR code broken" viewBox={`0 0 ${modules.length + 8} 1`}>
        <rect fill="#ffffff" />
        <path d={qrPath(broken)} fill="#000000" />
      </svg>,
    );
    expect(decode(container.querySelector("svg") as SVGSVGElement)).toBeNull();
  });
});

describe("SetupCodes", () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
    delete document.documentElement.dataset.black;
  });

  it("encodes the setup link, the Private DNS hostname and the DoH URL in turn", async () => {
    reducedMotion();
    const user = userEvent.setup();
    render(<SetupCodes device={device()} />);

    expect(screen.getByRole("radio", { name: "Setup link" })).toBeChecked();
    expect(decode(qr())).toBe(SETUP);
    expect(screen.getByText(SETUP)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Private DNS" }));
    expect(qr()).toHaveAccessibleName("QR code: Android Private DNS hostname");
    expect(decode(qr())).toBe(DOT);
    expect(screen.getByText(DOT)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "DoH URL" }));
    expect(decode(qr())).toBe(DOH);
    expect(screen.getByText(DOH)).toBeInTheDocument();
  });

  it("copies whichever value the QR is showing", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const writeText = stubClipboard();
    render(<SetupCodes device={device()} />);

    await user.click(screen.getByRole("radio", { name: "Private DNS" }));
    await user.click(screen.getByRole("button", { name: "Copy to clipboard" }));

    expect(writeText).toHaveBeenCalledWith(DOT);
  });

  it("leads with a prominent QR on a wide screen", () => {
    reducedMotion();
    render(<SetupCodes device={device()} />);

    expect(qr()).toHaveClass("w-48");
    const open = screen.getByRole("link", { name: "Open setup link" });
    expect(open).toHaveAttribute("href", SETUP);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("leads with open and copy on a phone, and fits the QR to the screen", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const writeText = stubClipboard();
    render(<SetupCodes device={device()} />);

    const open = screen.getByRole("link", { name: "Open setup link" });
    expect(open).toHaveAttribute("href", SETUP);
    expect(open).toHaveClass("w-full", "max-wide:h-11");
    expect(qr()).toHaveClass("w-full", "max-w-60");
    expect(screen.getByLabelText("QR code for Work Laptop")).toHaveClass("w-full");

    const order = [open, screen.getByRole("button", { name: "Copy Setup link" }), qr()];
    for (let at = 1; at < order.length; at += 1) {
      expect(order[at - 1].compareDocumentPosition(order[at])).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }

    await user.click(screen.getByRole("button", { name: "Copy Setup link" }));
    expect(writeText).toHaveBeenCalledWith(SETUP);

    await user.click(screen.getByRole("radio", { name: "DoH URL" }));
    expect(screen.queryByRole("link", { name: "Open setup link" })).not.toBeInTheDocument();
    expect(decode(qr())).toBe(DOH);
  });

  it("stays dark on white under the dark and pure black themes", () => {
    reducedMotion();
    document.documentElement.dataset.theme = "dark";
    document.documentElement.dataset.black = "true";
    render(<SetupCodes device={device()} />);

    const svg = qr();
    expect(svg.querySelector("rect")).toHaveAttribute("fill", "#ffffff");
    expect(svg.querySelector("path")).toHaveAttribute("fill", "#000000");
    expect(svg.outerHTML).not.toMatch(/var\(|currentColor/);
    expect(svg).toHaveClass("[forced-color-adjust:none]");

    const box = svg.getAttribute("viewBox")?.split(" ").map(Number) ?? [];
    const first = /^M(\d+) (\d+)/.exec(svg.querySelector("path")?.getAttribute("d") ?? "");
    expect(box[2] - qrMatrix(SETUP).size).toBe(8);
    expect(first?.slice(1).map(Number)).toEqual([4, 4]);
    expect(decode(svg)).toBe(SETUP);
  });

  it("drops the setup link when the resolver does not offer one", () => {
    reducedMotion();
    render(<SetupCodes device={device({ setup_url: undefined, dot_hostname: "" })} />);

    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open setup link" })).not.toBeInTheDocument();
    expect(decode(qr())).toBe(DOH);
  });
});

describe("Where the QR codes live", () => {
  const routes = () => ({
    "/api/devices": devices(),
    "/api/health": health(),
    "/api/rewrites": rewrites(),
    "/api/native-dns": nativeDns(),
  });

  it("opens with a device card's setup instructions on a wide screen", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi(routes());
    render(<Devices onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: "Setup instructions" }));

    expect(decode(qr())).toBe(SETUP);
  });

  it("sits at the top of a device's setup screen on a phone", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi(routes());
    render(<Devices onUnauthorized={noop} />);

    await user.click(await screen.findByRole("button", { name: /Work Laptop/ }));
    await user.click(await screen.findByRole("button", { name: /Setup instructions/ }));

    const group = (await screen.findByRole("heading", { name: "Scan or open" })).closest(
      "section",
    ) as HTMLElement;
    expect(within(group).getByRole("link", { name: "Open setup link" })).toHaveAttribute(
      "href",
      SETUP,
    );
    expect(window.location.hash).not.toContain("abcdefgh12345678");
    expect(decode(qr())).toBe(SETUP);
  });
});
