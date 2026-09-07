"use client";

import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";

import { cn } from "@/lib/utils";

export function Scroller({
  className,
  style,
  children,
  orientation = "both",
}: {
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
  orientation?: "both" | "vertical" | "horizontal";
}) {
  return (
    <ScrollAreaPrimitive.Root
      type="hover"
      scrollHideDelay={900}
      className={cn("relative min-w-0 overflow-hidden", className)}
    >
      <ScrollAreaPrimitive.Viewport
        data-slot="scroll-viewport"
        className="size-full max-h-[inherit] rounded-[inherit] outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        style={style}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      {orientation !== "horizontal" && <Bar orientation="vertical" />}
      {orientation !== "vertical" && <Bar orientation="horizontal" />}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}

function Bar({ orientation }: { orientation: "vertical" | "horizontal" }) {
  return (
    <ScrollAreaPrimitive.Scrollbar
      orientation={orientation}
      className={cn(
        "flex touch-none select-none p-0.5 transition-opacity duration-[--hy-base] ease-brand",
        orientation === "vertical" ? "h-full w-2.5" : "h-2.5 w-full flex-col",
      )}
    >
      <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-subtle transition-colors duration-[--hy-fast] ease-brand hover:bg-muted data-[state=visible]:bg-subtle" />
    </ScrollAreaPrimitive.Scrollbar>
  );
}
