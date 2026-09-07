"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { full, percent } from "@/lib/format";
import { useTween } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export function Counter({
  value,
  format,
}: {
  value: number;
  format: (value: number) => string;
}) {
  return <>{format(useTween(value))}</>;
}

export function Stat({
  label,
  value,
  sub,
  tone = "default",
  children,
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "hero";
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-slot="stat"
      className={cn(
        "flex min-w-0 flex-col rounded-lg border border-line bg-surface p-4 shadow-xs max-wide:p-3.5",
        className,
      )}
    >
      <div className="text-2xs font-medium tracking-[0.06em] text-muted uppercase max-wide:text-xs">
        {label}
      </div>
      <div
        className={cn(
          "mt-1.5 font-semibold tracking-[-0.02em] text-fg",
          tone === "hero" ? "text-4xl" : "text-3xl max-wide:text-2xl",
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-1.5 text-xs text-muted">{sub}</div>}
      {children && <div className="mt-2.5">{children}</div>}
    </div>
  );
}

const METER_TONES = {
  ok: "var(--hy-chart-1)",
  warn: "var(--hy-warn)",
  bad: "var(--hy-danger)",
} as const;

export function Meter({
  fraction,
  label = "quota used",
  showLabel = true,
}: {
  fraction: number;
  label?: string;
  showLabel?: boolean;
}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0));
  const tone = clamped >= 0.9 ? "bad" : clamped >= 0.75 ? "warn" : "ok";
  const colour = METER_TONES[tone];

  return (
    <>
      <div
        role="meter"
        aria-valuenow={Math.round(clamped * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-1.5 w-full overflow-hidden rounded-full"
        style={{ background: `color-mix(in srgb, ${colour} 16%, var(--hy-chart-track))` }}
      >
        <span
          className="block h-full rounded-full transition-[width,background-color] duration-[--hy-slow] ease-brand"
          style={{ width: `${clamped * 100}%`, background: colour }}
        />
      </div>
      {showLabel && <div className="mt-1.5 text-xs text-muted">{percent(clamped, 1)} used</div>}
    </>
  );
}

export function KeyValue({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-x-4 max-wide:grid-cols-1">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="border-b border-line-subtle py-2 text-xs text-muted max-wide:border-b-0 max-wide:pb-0">
            {key}
          </dt>
          <dd className="m-0 min-w-0 border-b border-line-subtle py-2 text-sm max-wide:pt-0.5">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CopyField({
  value,
  label,
  className,
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <div className={cn("min-w-0", className)}>
      {label && <div className="mb-1.5 text-xs text-muted">{label}</div>}
      <div className="flex items-stretch gap-1.5">
        <code className="min-w-0 flex-1 rounded-md border border-line bg-sunken px-2 py-1.5 font-mono text-2xs leading-[1.55] wrap-anywhere max-wide:text-xs">
          {value}
        </code>
        <Button
          size="icon"
          aria-label={copied ? "Copied to clipboard" : "Copy to clipboard"}
          className={cn("self-stretch", copied && "hy-pop border-ok/45 text-ok-ink")}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
            } catch {}
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  );
}

export function Pager({
  page,
  pages,
  total,
  from,
  to,
  unit,
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  from: number;
  to: number;
  unit: string;
  onPage: (page: number) => void;
}) {
  if (total === 0) return null;

  return (
    <nav
      aria-label={`${unit} pages`}
      className="mt-3 flex flex-wrap items-center gap-2.5 text-xs text-muted"
    >
      <span className="tabular-nums">
        {full(from)}&ndash;{full(to)} of {full(total)} {unit}
      </span>
      <span className="flex-1" />
      <Button size="sm" disabled={page <= 0} onClick={() => onPage(page - 1)}>
        Previous
      </Button>
      <span className="tabular-nums whitespace-nowrap">
        {page + 1} / {pages}
      </span>
      <Button size="sm" disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </nav>
  );
}

export function Mono({ className, ...props }: React.ComponentProps<"span">) {
  return <span className={cn("font-mono text-xs", className)} {...props} />;
}

export function Muted({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("m-0 text-xs text-muted", className)} {...props} />;
}

export function Faint({ className, ...props }: React.ComponentProps<"span">) {
  return <span className={cn("text-xs text-subtle", className)} {...props} />;
}
