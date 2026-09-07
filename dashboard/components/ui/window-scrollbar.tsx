"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/lib/media";

export function WindowScrollbar() {
  const coarse = useMediaQuery("(pointer: coarse)");
  const sleep = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [bar, setBar] = useState<{ size: number; offset: number } | null>(null);
  const [awake, setAwake] = useState(false);

  const measure = useCallback(() => {
    const box = window.innerHeight;
    const content = document.documentElement.scrollHeight;
    if (content - box <= 1) {
      setBar(null);
      return;
    }
    const size = Math.max(28, (box / content) * box);
    setBar({ size, offset: (window.scrollY / (content - box)) * (box - size) });
  }, []);

  useEffect(() => {
    const onScroll = () => {
      measure();
      setAwake(true);
      if (sleep.current) clearTimeout(sleep.current);
      sleep.current = setTimeout(() => setAwake(false), 900);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);

    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", measure);
      observer.disconnect();
      if (sleep.current) clearTimeout(sleep.current);
    };
  }, [measure]);

  if (coarse || !bar) return null;

  const drag = (event: React.PointerEvent<HTMLSpanElement>) => {
    event.preventDefault();
    const thumb = event.currentTarget;
    try {
      thumb.setPointerCapture(event.pointerId);
    } catch {}
    thumb.dataset.dragging = "true";

    const down = event.clientY;
    const from = window.scrollY;
    const box = window.innerHeight;
    const content = document.documentElement.scrollHeight;

    const move = (moved: PointerEvent) => {
      const travel = moved.clientY - down;
      window.scrollTo({
        top: from + (travel / (box - bar.size)) * (content - box),
        behavior: "auto",
      });
    };
    const stop = () => {
      delete thumb.dataset.dragging;
      thumb.removeEventListener("pointermove", move);
      thumb.removeEventListener("pointerup", stop);
      thumb.removeEventListener("pointercancel", stop);
    };

    thumb.addEventListener("pointermove", move);
    thumb.addEventListener("pointerup", stop);
    thumb.addEventListener("pointercancel", stop);
  };

  return (
    <div className="hy-window-scroll" data-awake={awake}>
      <div className="hy-window-track" aria-hidden="true">
        <span
          className="hy-window-thumb"
          style={{ height: bar.size, transform: `translateY(${bar.offset}px)` }}
          onPointerDown={drag}
        />
      </div>
    </div>
  );
}
