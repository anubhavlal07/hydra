"use client";

import { Segmented } from "@/components/ui";
import { PERIODS, setPeriod, usePeriod } from "@/lib/period";
import { cn } from "@/lib/utils";

export function PeriodToggle({ className }: { className?: string }) {
  const period = usePeriod();
  return (
    <Segmented
      items={PERIODS}
      value={period}
      onChange={setPeriod}
      label="Count queries over"
      className={cn("whitespace-nowrap", className)}
    />
  );
}
