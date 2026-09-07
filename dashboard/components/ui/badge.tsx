import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium whitespace-nowrap max-wide:text-xs",
  {
    variants: {
      tone: {
        neutral: "border-line bg-sunken text-muted",
        ok: "border-ok/35 bg-ok-soft text-ok-ink",
        warn: "border-warn/40 bg-warn-soft text-warn-ink",
        danger: "border-danger/35 bg-danger-soft text-danger-ink",
        live: "border-live/35 bg-live-soft text-live-ink",
        accent: "border-accent-line bg-accent-soft text-accent-ink",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

const dotVariants = cva("size-1.5 shrink-0 rounded-full bg-current", {
  variants: { pulse: { true: "hy-pulse", false: "" } },
  defaultVariants: { pulse: false },
});

export interface BadgeProps
  extends React.ComponentProps<"span">,
    VariantProps<typeof badgeVariants> {
  dot?: boolean;
  pulse?: boolean;
}

export function Badge({
  className,
  tone,
  dot = true,
  pulse = false,
  children,
  ...props
}: BadgeProps) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot && <span className={dotVariants({ pulse })} aria-hidden="true" />}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

export { badgeVariants };
