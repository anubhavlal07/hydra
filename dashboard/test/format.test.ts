import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ago,
  approx,
  clockTime,
  compact,
  duration,
  full,
  ms,
  percent,
} from "@/lib/format";

afterEach(() => vi.useRealTimers());

describe("approx", () => {
  it("rounds a list's size the way the presets show it", () => {
    expect(approx(228_217)).toBe("~228k");
    expect(approx(44_936)).toBe("~45k");
    expect(approx(2_291_474)).toBe("~2.3M");
    expect(approx(640)).toBe("~640");
    expect(approx(Number.NaN)).toBe("-");
  });
});

describe("compact", () => {
  it("prints small numbers whole and large ones abbreviated", () => {
    expect(compact(0)).toBe("0");
    expect(compact(999)).toBe("999");
    expect(compact(1000)).toBe("1K");
    expect(compact(48_210)).toBe("48.2K");
    expect(compact(1_500_000)).toBe("1.5M");
  });

  it("rounds rather than truncating below a thousand", () => {
    expect(compact(12.4)).toBe("12");
    expect(compact(12.6)).toBe("13");
  });

  it("gives a dash for anything that is not a number", () => {
    expect(compact(Number.NaN)).toBe("-");
    expect(compact(Number.POSITIVE_INFINITY)).toBe("-");
  });
});

describe("full", () => {
  it("groups thousands", () => {
    expect(full(48_210)).toBe("48,210");
    expect(full(7)).toBe("7");
  });

  it("gives a dash for anything that is not a number", () => {
    expect(full(Number.NaN)).toBe("-");
  });
});

describe("percent", () => {
  it("renders a fraction to one decimal by default", () => {
    expect(percent(0.4237)).toBe("42.4%");
    expect(percent(0.4237, 0)).toBe("42%");
    expect(percent(1)).toBe("100.0%");
  });

  it("gives a dash for anything that is not a number", () => {
    expect(percent(Number.NaN)).toBe("-");
  });
});

describe("ms", () => {
  it("switches to seconds at a thousand milliseconds", () => {
    expect(ms(18)).toBe("18ms");
    expect(ms(999)).toBe("999ms");
    expect(ms(1000)).toBe("1.00s");
    expect(ms(1536)).toBe("1.54s");
  });
});

describe("duration", () => {
  it("steps through seconds, minutes, hours and days", () => {
    expect(duration(45)).toBe("45s");
    expect(duration(90)).toBe("1m");
    expect(duration(3660)).toBe("1h 1m");
    expect(duration(93_600)).toBe("1d 2h");
  });
});

describe("ago", () => {
  it("says never when there is no timestamp", () => {
    expect(ago(null)).toBe("never");
    expect(ago(undefined)).toBe("never");
    expect(ago(0)).toBe("never");
  });

  it("counts back in the largest unit that fits", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-02T00:00:00Z"));
    const now = Date.now() / 1000;

    expect(ago(now - 1)).toBe("just now");
    expect(ago(now - 30)).toBe("30s ago");
    expect(ago(now - 600)).toBe("10m ago");
    expect(ago(now - 7200)).toBe("2h ago");
    expect(ago(now - 172_800)).toBe("2d ago");
  });
});

describe("clockTime", () => {
  it("reads unix seconds, not milliseconds", () => {
    const at = Date.UTC(2024, 0, 1, 12, 34) / 1000;
    expect(clockTime(at)).toBe(
      new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    );
  });
});
