"use client";

import { useCallback, useEffect, useState } from "react";

export type Filters = Record<string, string>;

export function readFilters(prefix: string, keys: readonly string[]): Filters {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const found: Filters = {};
  for (const key of keys) {
    const value = params.get(`${prefix}.${key}`);
    if (value) found[key] = value;
  }
  return found;
}

export function writeFilters(prefix: string, keys: readonly string[], values: Filters): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  for (const key of keys) {
    const value = values[key];
    if (value) params.set(`${prefix}.${key}`, value);
    else params.delete(`${prefix}.${key}`);
  }

  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
    window.history.replaceState(null, "", next);
  }
}

export function useFilters(prefix: string, keys: readonly string[], initial: Filters) {
  const [filters, setFilters] = useState<Filters>(() => ({
    ...initial,
    ...readFilters(prefix, keys),
  }));

  useEffect(() => {
    writeFilters(prefix, keys, filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefix, filters]);

  const set = useCallback((key: string, value: string) => {
    setFilters((current) => {
      if ((current[key] ?? "") === value) return current;
      return { ...current, [key]: value };
    });
  }, []);

  return { filters, set };
}

export function readParam(key: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(key) ?? "";
}

export function writeParam(key: string, value: string): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (value) params.set(key, value);
  else params.delete(key);

  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
    window.history.replaceState(window.history.state, "", next);
  }
}

export function shareableLink(): string {
  if (typeof window === "undefined") return "";
  return window.location.href;
}
