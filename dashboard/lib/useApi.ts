"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

interface Options {
  pollMs?: number;

  onUnauthorized?: () => void;
  enabled?: boolean;
}

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refreshing: boolean;
  refresh: () => void;
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  { pollMs, onUnauthorized, enabled = true }: Options = {},
): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;

  const load = useCallback(async (isRefresh: boolean) => {
    if (isRefresh) setRefreshing(true);
    try {
      const next = await fetcherRef.current();
      setData(next);
      setError(null);
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        unauthorizedRef.current?.();
        return;
      }
      setError(cause instanceof Error ? cause.message : "Something went wrong");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setInterval> | null = null;

    setLoading(true);
    void load(false);

    const start = () => {
      if (!pollMs || timer) return;
      timer = setInterval(() => void load(true), pollMs);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        void load(true);
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pollMs, load, ...deps]);

  return { data, error, loading, refreshing, refresh: () => void load(true) };
}
