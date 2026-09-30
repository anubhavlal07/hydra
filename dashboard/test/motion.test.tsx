import { render, renderHook, screen, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Counter } from "@/components/ui";
import { scrollToTop, useReducedMotion, useTween } from "@/lib/motion";
import { full } from "@/lib/format";
import { reducedMotion } from "./media";

afterEach(() => vi.restoreAllMocks());

describe("useReducedMotion", () => {
  it("is false by default and true when the media query matches", () => {
    expect(renderHook(() => useReducedMotion()).result.current).toBe(false);

    reducedMotion();
    expect(renderHook(() => useReducedMotion()).result.current).toBe(true);
  });
});

describe("useTween", () => {
  it("animates from zero when motion is allowed", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });

    const { result } = renderHook(() => useTween(48_210));
    expect(result.current).toBe(0);
    expect(frames.length).toBeGreaterThan(0);
  });

  it("renders the final value on the first frame under reduced motion", () => {
    reducedMotion();
    const raf = vi.spyOn(window, "requestAnimationFrame");

    const { result } = renderHook(() => useTween(48_210));

    expect(result.current).toBe(48_210);
    expect(raf).not.toHaveBeenCalled();
  });
});

describe("Counter", () => {
  it("shows the final figure immediately under reduced motion", () => {
    reducedMotion();
    render(<Counter value={48_210} format={full} />);

    expect(screen.getByText("48,210")).toBeInTheDocument();
  });

  it("starts from zero when motion is allowed", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    render(<Counter value={48_210} format={full} />);

    expect(screen.getByText("0")).toBeInTheDocument();
  });
});

describe("scrollToTop", () => {
  it("jumps rather than glides when motion is reduced", () => {
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

    act(() => scrollToTop(true));
    expect(scroll).toHaveBeenLastCalledWith({ top: 0, behavior: "auto" });

    act(() => scrollToTop(false));
    expect(scroll).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });
  });
});
