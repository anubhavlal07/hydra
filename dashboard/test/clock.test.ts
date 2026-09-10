import { describe, expect, it } from "vitest";

import { clockLabel, describeWindow, hourLabel, windowSpan } from "@/lib/clock";

describe("clock labels", () => {
  it("names midnight and noon instead of 12:00", () => {
    expect(clockLabel("00:00")).toBe("midnight");
    expect(clockLabel("12:00")).toBe("noon");
    expect(clockLabel("00:30")).toBe("12:30 AM");
    expect(clockLabel("12:45")).toBe("12:45 PM");
    expect(clockLabel("22:00")).toBe("10 PM");
    expect(clockLabel("07:05")).toBe("7:05 AM");
  });

  it("labels the hour picker so 12 AM and 12 PM cannot be confused", () => {
    expect(hourLabel(0)).toBe("12 AM · midnight");
    expect(hourLabel(12)).toBe("12 PM · noon");
    expect(hourLabel(13)).toBe("1 PM");
  });
});

describe("window span", () => {
  it("says a window that starts at noon runs 17 hours into the next day", () => {
    expect(windowSpan("12:00", "05:00")).toBe("noon → 5 AM next day · 17 hours");
  });

  it("says a window from midnight runs 5 hours", () => {
    expect(windowSpan("00:00", "05:00")).toBe("midnight → 5 AM · 5 hours");
  });

  it("does not call a window ending at midnight the next day", () => {
    expect(windowSpan("22:00", "00:00")).toBe("10 PM → midnight · 2 hours");
  });

  it("counts minutes and flags an empty window", () => {
    expect(windowSpan("21:30", "23:00")).toBe("9:30 PM → 11 PM · 1 hour 30 min");
    expect(windowSpan("08:00", "08:00")).toBe("starts and ends together, so never in force");
    expect(windowSpan("", "")).toBe("all day");
  });
});

describe("describeWindow", () => {
  it("reads like the schedule list", () => {
    expect(describeWindow({ days: [4, 0], starts_at: "22:00", ends_at: "07:00" })).toBe(
      "10 PM–7 AM, Mon Fri",
    );
    expect(describeWindow({ days: [], starts_at: "", ends_at: "" })).toBe("every day");
  });
});
