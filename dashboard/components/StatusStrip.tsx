"use client";

import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";

import { getHealth } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";

const POLL_MS = 30_000;

export default function StatusStrip({ onUnauthorized }: { onUnauthorized?: () => void }) {
  const [open, setOpen] = useState(false);
  const { data, loading } = useApi(getHealth, [], { pollMs: POLL_MS, onUnauthorized });

  const reasons = data?.attention ?? [];
  const warnings = data?.warnings ?? [];
  const all = [...warnings, ...reasons];
  const unwell = all.length > 0;

  if (loading && !data) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-subtle" aria-live="polite">
        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        <span className="max-wide:hidden">Checking</span>
      </span>
    );
  }

  if (!unwell) {
    return (
      <span
        className="flex items-center gap-1.5 text-xs text-muted"
        title="Nothing is asking for attention"
      >
        <CheckCircle2 className="size-3.5 text-ok" aria-hidden="true" />
        <span className="max-wide:hidden">Nominal</span>
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium max-wide:min-h-11 max-wide:px-3",
          "bg-warn-soft text-warn-ink transition-colors duration-[--hy-fast] ease-brand",
          "outline-none hover:brightness-105 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        )}
      >
        <AlertTriangle className="size-3.5" aria-hidden="true" />
        <span>
          {all.length} {all.length === 1 ? "thing" : "things"}
        </span>
      </button>

      {open && (
        <div
          role="status"
          className="absolute right-0 z-40 mt-1.5 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-line bg-surface p-3 shadow-lg"
        >
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {all.map((reason) => (
              <li key={reason} className="flex gap-2 text-xs leading-snug text-fg">
                <AlertTriangle
                  className="mt-0.5 size-3.5 shrink-0 text-warn-ink"
                  aria-hidden="true"
                />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
