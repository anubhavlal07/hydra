"use client";

import * as AccordionPrimitive from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export const Accordion = AccordionPrimitive.Root;

export function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item
      className={cn("border-b border-line-subtle last:border-b-0", className)}
      {...props}
    />
  );
}

export function AccordionTrigger({
  className,
  children,
  meta,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger> & { meta?: React.ReactNode }) {
  return (
    <AccordionPrimitive.Header className="flex">
      <AccordionPrimitive.Trigger
        className={cn(
          "group flex min-h-11 flex-1 items-center gap-2 py-2 text-left text-sm font-medium text-fg",
          "transition-colors duration-[--hy-fast] ease-brand outline-none hover:text-accent-ink",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          className,
        )}
        {...props}
      >
        <span className="min-w-0 flex-1">{children}</span>
        {meta}
        <ChevronDown
          className="size-4 shrink-0 text-subtle transition-transform duration-[--hy-base] ease-brand group-data-[state=open]:rotate-180"
          aria-hidden="true"
        />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

export function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      className="overflow-hidden data-[state=closed]:animate-[hy-collapse_var(--hy-base)_var(--hy-ease)] data-[state=open]:animate-[hy-expand_var(--hy-base)_var(--hy-ease)]"
      {...props}
    >
      <div className={cn("pb-3 text-sm text-muted", className)}>{children}</div>
    </AccordionPrimitive.Content>
  );
}
