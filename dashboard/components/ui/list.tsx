"use client";

import { Check, ChevronRight, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import { InfoTip } from "./tooltip";

export function Group({
  heading,
  action,
  info,
  footer,
  children,
  className,
  level = 2,
}: {
  heading?: React.ReactNode;
  action?: React.ReactNode;
  info?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? "h2" : "h3";

  return (
    <section data-slot="group" className={cn("flex min-w-0 flex-col", className)}>
      {(heading || action || info) && (
        <div className="flex min-w-0 items-center gap-2 px-1 pb-1.5">
          {heading ? (
            <Heading className="m-0 min-w-0 flex-1 truncate text-xs font-medium tracking-[0.06em] text-muted uppercase">
              {heading}
            </Heading>
          ) : (
            <span className="min-w-0 flex-1" />
          )}
          {action && <span className="flex shrink-0 items-center gap-1.5">{action}</span>}
          {info && <InfoTip>{info}</InfoTip>}
        </div>
      )}

      <div className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-surface">
        {children}
      </div>

      {footer && (
        <p className="mt-1.5 mb-0 px-1 text-xs leading-snug text-muted [&_b]:text-fg [&_strong]:text-fg">
          {footer}
        </p>
      )}
    </section>
  );
}

const SEPARATOR =
  "after:pointer-events-none after:absolute after:right-0 after:bottom-0 after:left-4 after:h-px after:bg-line-subtle data-[lead=true]:after:left-12 last:after:hidden";

export function Row({
  title,
  secondary,
  icon,
  value,
  trailing,
  chevron,
  onClick,
  href,
  tone = "default",
  disabled = false,
  mono = false,
  wrap = false,
  label,
  className,
}: {
  title: React.ReactNode;
  secondary?: React.ReactNode;
  icon?: React.ReactNode;
  value?: React.ReactNode;
  trailing?: React.ReactNode;
  chevron?: boolean;
  onClick?: (event: React.MouseEvent<HTMLElement>) => void;
  href?: string;
  tone?: "default" | "danger" | "accent";
  disabled?: boolean;
  mono?: boolean;
  wrap?: boolean;
  label?: string;
  className?: string;
}) {
  const interactive = Boolean(onClick || href);
  const showChevron = chevron ?? (interactive && !trailing);

  const body = (
    <>
      {icon && (
        <span
          aria-hidden="true"
          className={cn(
            "flex size-5 shrink-0 items-center justify-center [&_svg]:size-5",
            tone === "danger" ? "text-danger" : tone === "accent" ? "text-accent" : "text-subtle",
          )}
        >
          {icon}
        </span>
      )}

      <span className="flex min-w-0 flex-1 flex-col">
        <span
          className={cn(
            "min-w-0 text-md",
            wrap ? "wrap-anywhere" : "truncate",
            mono && "font-mono text-xs leading-[1.5]",
            tone === "danger"
              ? "text-danger-ink"
              : tone === "accent" && !icon
                ? "text-accent-ink"
                : "text-fg",
          )}
        >
          {title}
        </span>
        {secondary !== undefined && secondary !== null && secondary !== "" && (
          <span
            className={cn(
              "min-w-0 text-xs text-muted",
              wrap ? "wrap-anywhere" : "truncate",
              mono && !wrap && "font-mono",
            )}
          >
            {secondary}
          </span>
        )}
      </span>

      {value !== undefined && value !== null && value !== "" && (
        <span className="min-w-0 shrink-0 truncate text-right text-xs text-muted tabular-nums">
          {value}
        </span>
      )}

      {trailing && <span className="flex shrink-0 items-center gap-2">{trailing}</span>}

      {showChevron && (
        <ChevronRight className="size-4 shrink-0 text-subtle" aria-hidden="true" />
      )}
    </>
  );

  const shared = cn(
    "relative flex w-full min-w-0 items-center gap-3 px-4 py-2.5 text-left",
    "min-h-12 data-[lines=2]:min-h-15",
    SEPARATOR,
    interactive &&
      "transition-colors duration-[--hy-fast] ease-brand outline-none hover:[@media(hover:hover)]:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent active:bg-hover disabled:pointer-events-none disabled:opacity-50",
    className,
  );

  const marks = {
    "data-slot": "row",
    "data-lead": icon ? "true" : undefined,
    "data-lines": secondary ? "2" : "1",
  } as const;

  if (href) {
    return (
      <a {...marks} href={href} aria-label={label} className={shared}>
        {body}
      </a>
    );
  }

  if (onClick) {
    return (
      <button
        {...marks}
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={shared}
      >
        {body}
      </button>
    );
  }

  return (
    <div {...marks} className={shared}>
      {body}
    </div>
  );
}

export function RowBlock({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-slot="row-block"
      className={cn("relative min-w-0 px-4 py-3", SEPARATOR, className)}
    >
      {children}
    </div>
  );
}

export function CopyRow({
  title,
  value,
  icon,
}: {
  title: React.ReactNode;
  value: string;
  icon?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <Row
      icon={icon}
      title={title}
      secondary={value}
      wrap
      label={copied ? `${title} copied` : `Copy ${title}`}
      onClick={() => {
        void navigator.clipboard
          ?.writeText(value)
          .then(() => setCopied(true))
          .catch(() => {});
      }}
      trailing={
        copied ? (
          <Check className="hy-pop size-4 text-ok-ink" aria-hidden="true" />
        ) : (
          <Copy className="size-4 text-subtle" aria-hidden="true" />
        )
      }
    />
  );
}

export function Chip({
  children,
  onRemove,
  label,
}: {
  children: React.ReactNode;
  onRemove: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onRemove}
      aria-label={label}
      className="inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-2.5 text-xs font-medium text-accent-ink transition-colors duration-[--hy-fast] ease-brand outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <span className="min-w-0 truncate">{children}</span>
      <span aria-hidden="true" className="text-sm leading-none">
        &times;
      </span>
    </button>
  );
}
