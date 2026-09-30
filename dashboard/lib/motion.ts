"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

function watchReduced(onChange: () => void) {
  const query = window.matchMedia(REDUCED);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    watchReduced,
    () => window.matchMedia(REDUCED).matches,
    () => false,
  );
}

const easeOut = (t: number) => 1 - (1 - t) ** 3;

export function useTween(target: number, duration = 850): number {
  const reduced = useReducedMotion();

  const [value, setValue] = useState(reduced ? target : 0);
  const from = useRef(value);

  useEffect(() => {
    if (reduced || !Number.isFinite(target)) {
      from.current = target;
      setValue(target);
      return;
    }
    const origin = from.current;
    if (origin === target) return;

    let frame = 0;

    let start = 0;
    const step = (now: number) => {
      if (!start) start = now;
      const t = Math.min(1, Math.max(0, (now - start) / duration));
      const next = t >= 1 ? target : origin + (target - origin) * easeOut(t);
      from.current = next;
      setValue(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, duration, reduced]);

  return value;
}

export function scrollToTop(reduced: boolean) {
  window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
}
