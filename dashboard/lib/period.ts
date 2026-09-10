"use client";

import { useSyncExternalStore } from "react";

import type { DeviceActivity, ResolverSummary } from "./types";

export type Period = "month" | "all";

export const PERIODS: readonly { value: Period; label: string }[] = [
  { value: "month", label: "This month" },
  { value: "all", label: "All time" },
];

const KEY = "hydra-period";

const watchers = new Set<() => void>();

function subscribe(onChange: () => void) {
  watchers.add(onChange);
  return () => watchers.delete(onChange);
}

export function storedPeriod(): Period {
  try {
    return localStorage.getItem(KEY) === "all" ? "all" : "month";
  } catch {
    return "month";
  }
}

export function usePeriod(): Period {
  return useSyncExternalStore(subscribe, storedPeriod, () => "month" as Period);
}

export function setPeriod(next: Period) {
  try {
    if (next === "all") localStorage.setItem(KEY, next);
    else localStorage.removeItem(KEY);
  } catch {}
  for (const watcher of [...watchers]) watcher();
}

export interface Counts {
  queries: number;
  blocked: number;
  cached: number;
}

export function deviceCounts(activity: DeviceActivity, period: Period): Counts {
  const source = period === "month" && activity.month ? activity.month : activity;
  return { queries: source.queries, blocked: source.blocked, cached: source.cached };
}

export function resolverCounts(summary: ResolverSummary, period: Period): Counts {
  const source = period === "month" && summary.month ? summary.month : summary;
  return { queries: source.queries, blocked: source.blocked, cached: source.cached };
}
