"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";

import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex min-w-0 items-center gap-0.5 rounded-md border border-line-strong bg-surface p-0.5 max-wide:flex max-wide:w-full",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "inline-flex h-8 min-w-0 flex-1 items-center justify-center gap-1.5 rounded-[5px] px-3 text-xs font-medium whitespace-nowrap text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent data-[state=active]:bg-accent-soft data-[state=active]:text-accent-ink max-wide:h-11",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      className={cn("hy-rise mt-3.5 min-w-0 outline-none", className)}
      {...props}
    />
  );
}
