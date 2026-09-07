"use client";

import * as SwitchPrimitive from "@radix-ui/react-switch";

import { cn } from "@/lib/utils";

export function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-line-strong bg-sunken max-wide:after:absolute max-wide:after:-inset-x-3 max-wide:after:-inset-y-3 max-wide:after:content-[''] transition-colors duration-[--hy-fast] ease-brand outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent data-[state=checked]:border-accent data-[state=checked]:bg-accent disabled:cursor-default disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-3.5 translate-x-0.5 rounded-full bg-white shadow-sm ring-1 ring-black/10 transition-transform duration-[--hy-base] ease-brand data-[state=checked]:translate-x-4" />
    </SwitchPrimitive.Root>
  );
}

export function SwitchField({
  checked,
  onCheckedChange,
  disabled,
  children,
  hint,
  className,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
  children: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={cn(
        "flex min-h-11 cursor-pointer items-center gap-3 select-none has-disabled:cursor-default",
        className,
      )}
    >
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-fg">{children}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}
