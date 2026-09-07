"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Info } from "lucide-react";

import { useMediaQuery } from "@/lib/media";
import { cn } from "@/lib/utils";

const PANEL =
  "z-50 max-w-[min(20rem,calc(100vw-2rem))] rounded-lg border border-line bg-raised px-2.5 py-2 text-xs leading-snug text-fg shadow-lg";

const TRIGGER =
  "inline-flex size-5 shrink-0 items-center justify-center rounded-full text-subtle transition-colors duration-[--hy-fast] ease-brand outline-none hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-wide:size-7";

export function InfoTip({
  children,
  label = "More information",
  className,
}: {
  children: React.ReactNode;
  label?: string;
  className?: string;
}) {
  const coarse = useMediaQuery("(hover: none)");

  if (coarse) {
    return (
      <PopoverPrimitive.Root>
        <PopoverPrimitive.Trigger aria-label={label} className={cn(TRIGGER, className)}>
          <Info className="size-3.5" aria-hidden="true" />
        </PopoverPrimitive.Trigger>
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            side="top"
            align="center"
            sideOffset={6}
            collisionPadding={16}
            className={PANEL}
          >
            {children}
            <PopoverPrimitive.Arrow className="fill-[var(--hy-border)]" />
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    );
  }

  return (
    <TooltipPrimitive.Provider delayDuration={200} skipDelayDuration={400}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger aria-label={label} className={cn(TRIGGER, className)}>
          <Info className="size-3.5" aria-hidden="true" />
        </TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side="top"
            align="center"
            sideOffset={6}
            collisionPadding={16}
            className={PANEL}
          >
            {children}
            <TooltipPrimitive.Arrow className="fill-[var(--hy-border)]" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
