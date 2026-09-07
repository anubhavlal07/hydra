"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CircleAlert, Info, Inbox, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "./button";

const alertVariants = cva(
  "hy-rise flex min-w-0 items-start gap-2.5 rounded-md border border-l-[3px] bg-surface p-3 text-sm",
  {
    variants: {
      tone: {
        info: "border-line border-l-accent text-muted",
        warn: "border-line border-l-warn text-muted",
        danger: "border-line border-l-danger text-muted",
      },
    },
    defaultVariants: { tone: "warn" },
  },
);

const ICONS = { info: Info, warn: AlertTriangle, danger: CircleAlert } as const;

export interface AlertProps
  extends React.ComponentProps<"div">,
    VariantProps<typeof alertVariants> {
  icon?: boolean;
}

export function Alert({ className, tone, icon = true, children, ...props }: AlertProps) {
  const Icon = ICONS[tone ?? "warn"];
  return (
    <div data-slot="alert" className={cn(alertVariants({ tone }), className)} {...props}>
      {icon && (
        <Icon
          className={cn(
            "mt-0.5 size-4 shrink-0",
            tone === "danger" ? "text-danger" : tone === "info" ? "text-accent" : "text-warn",
          )}
          aria-hidden="true"
        />
      )}
      <div className="min-w-0 flex-1 [&_b]:text-fg [&_strong]:text-fg">{children}</div>
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <Alert tone="danger" role="alert">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="min-w-0">{error}</span>
        {onRetry && (
          <Button size="sm" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    </Alert>
  );
}

export function PartialNote({ errors }: { errors: Record<string, string> }) {
  const entries = Object.entries(errors ?? {});
  if (entries.length === 0) return null;
  return (
    <Alert tone="warn" className="mb-3">
      Showing partial data. {entries.length} account{entries.length > 1 ? "s" : ""} did not
      answer:{" "}
      {entries.map(([label, message], index) => (
        <span key={label}>
          {index > 0 && ", "}
          <b>{label}</b> ({message})
        </span>
      ))}
    </Alert>
  );
}

export function Empty({
  children,
  icon = true,
  className,
}: {
  children: React.ReactNode;
  icon?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 px-4 py-8 text-center text-sm text-muted",
        className,
      )}
    >
      {icon && <Inbox className="size-5 text-subtle" aria-hidden="true" />}
      <p className="m-0 max-w-prose text-balance">{children}</p>
    </div>
  );
}

export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("hy-shimmer rounded-md", className)}
      {...props}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className="h-3"
          style={{ width: `${100 - index * 12}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      <div className="flex gap-3 border-b border-line pb-2">
        {Array.from({ length: cols }, (_, index) => (
          <Skeleton key={index} className="h-2.5 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex gap-3">
          {Array.from({ length: cols }, (_, col) => (
            <Skeleton key={col} className="h-3.5 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2
      className={cn("hy-spin size-3.5 shrink-0", className)}
      aria-hidden="true"
      data-slot="spinner"
    />
  );
}

export function Toasts({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-60 flex flex-col items-center gap-2 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] max-wide:pb-[calc(5.25rem+env(safe-area-inset-bottom))]"
    >
      {children}
    </div>
  );
}
