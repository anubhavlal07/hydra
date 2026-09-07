"use client";

import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export type SegmentedItem<T extends string> = T | { value: T; label: React.ReactNode };

export function Segmented<T extends string>({
  items,
  value,
  onChange,
  label,
  className,
}: {
  items: readonly SegmentedItem<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState<{ left: number; width: number } | null>(null);

  const options = items.map((item) =>
    typeof item === "string" ? { value: item as T, label: item as React.ReactNode } : item,
  );

  useEffect(() => {
    const root = box.current;
    if (!root) return;

    const measure = () => {
      const active = root.querySelector<HTMLElement>('[data-state="on"]');
      setPill(active ? { left: active.offsetLeft, width: active.offsetWidth } : null);
    };
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [value, items]);

  return (
    <ToggleGroupPrimitive.Root
      ref={box}
      type="single"
      value={value}
      onValueChange={(next) => {
        if (next) onChange(next as T);
      }}
      aria-label={label}
      data-ready={pill !== null}
      className={cn(
        "relative inline-flex shrink-0 rounded-md border border-line-strong bg-surface p-0.5",
        className,
      )}
    >
      {pill && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-0.5 bottom-0.5 left-0 rounded-[5px] bg-accent-soft transition-[transform,width] duration-[--hy-base] ease-brand"
          style={{ transform: `translateX(${pill.left - 2}px)`, width: pill.width }}
        />
      )}
      {options.map((option) => (
        <ToggleGroupPrimitive.Item
          key={option.value}
          value={option.value}
          className="relative z-1 inline-flex h-7 min-w-0 flex-auto items-center justify-center rounded-[5px] px-2.5 text-xs whitespace-nowrap font-medium text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent data-[state=on]:text-accent-ink max-wide:h-11 max-wide:flex-1 max-wide:px-2"
        >
          {option.label}
        </ToggleGroupPrimitive.Item>
      ))}
    </ToggleGroupPrimitive.Root>
  );
}
