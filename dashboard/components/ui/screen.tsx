"use client";

import { ChevronLeft } from "lucide-react";
import { createContext, useContext } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

export interface HeaderSlots {
  title: HTMLElement | null;
  actions: HTMLElement | null;
}

export const HeaderSlotContext = createContext<HeaderSlots>({ title: null, actions: null });

export function ScreenHeader({
  title,
  actions,
}: {
  title?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const slots = useContext(HeaderSlotContext);

  const heading = title !== undefined && (
    <h1
      data-slot="screen-title"
      className="m-0 min-w-0 flex-1 truncate text-lg font-semibold tracking-[-0.015em] text-fg"
    >
      {title}
    </h1>
  );

  if (!slots.title && !slots.actions) {
    if (!heading && !actions) return null;
    return (
      <div data-slot="screen-header" className="mb-3 flex min-w-0 items-center gap-2">
        {heading}
        {actions && <span className="flex shrink-0 items-center gap-2">{actions}</span>}
      </div>
    );
  }

  return (
    <>
      {heading && slots.title && createPortal(heading, slots.title)}
      {actions && slots.actions && createPortal(actions, slots.actions)}
    </>
  );
}

export function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      data-slot="back"
      className={cn(
        "-ml-2.5 inline-flex size-11 shrink-0 items-center justify-center rounded-md text-accent-ink",
        "transition-colors duration-[--hy-fast] ease-brand outline-none",
        "hover:[@media(hover:hover)]:bg-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:opacity-60",
      )}
    >
      <ChevronLeft className="size-6" aria-hidden="true" />
    </button>
  );
}

export function Screen({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div data-slot="screen" className={cn("flex min-w-0 flex-col gap-5", className)}>
      {children}
    </div>
  );
}
