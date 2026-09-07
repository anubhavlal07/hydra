"use client";

import { cn } from "@/lib/utils";
import { InfoTip } from "./tooltip";

export function Section({
  title,
  description,
  info,
  meta,
  metaOn = "phone",
  summary,
  actions,
  children,
  level = 2,
  variant = "card",
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  info?: React.ReactNode;
  meta?: React.ReactNode;
  metaOn?: "phone" | "all";
  summary?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  level?: 2 | 3;
  variant?: "card" | "plain";
  className?: string;
}) {
  const Heading = level === 2 ? "h2" : "h3";

  const headingClass = cn(
    "m-0 flex w-full min-w-0 items-center gap-2.5 text-left",
    level === 2
      ? "text-md font-semibold tracking-[-0.011em] text-fg"
      : "text-base font-semibold tracking-[-0.008em] text-fg",
  );

  return (
    <section
      className={cn(
        "flex min-w-0 flex-col",
        variant === "card" && "rounded-lg border border-line bg-surface p-4 shadow-xs max-wide:p-3.5",
        className,
      )}
    >
      <div
        className={cn(
          "flex min-w-0 items-start gap-3",
          variant === "plain" &&
            "max-wide:rounded-lg max-wide:border max-wide:border-line max-wide:bg-surface max-wide:p-3.5 max-wide:shadow-xs",
        )}
      >
        <div className="min-w-0 flex-1">
          <Heading className={headingClass}>
            <span className="flex w-full min-w-0 items-center gap-2.5">
              <span className="min-w-0 truncate">{title}</span>
              <span className="flex-1" />
              {meta && (
                <span
                  className={cn(
                    "hidden shrink-0 items-center gap-1.5 text-xs font-normal tracking-normal normal-case text-muted max-wide:inline-flex",
                    metaOn === "all" && "inline-flex",
                  )}
                >
                  {meta}
                </span>
              )}
            </span>
          </Heading>
        </div>
        {info && (
          <span className="mt-0.5 shrink-0">
            <InfoTip>{info}</InfoTip>
          </span>
        )}
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>

      {summary && <div className={cn(level === 3 ? "mt-2" : "mt-3")}>{summary}</div>}

      <div className="min-w-0 pt-3">
        {description && <p className="mt-0 mb-3 max-w-prose text-xs text-muted">{description}</p>}
        {children}
      </div>
    </section>
  );
}
