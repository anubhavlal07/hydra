import { vi } from "vitest";

import { API_BASE } from "@/lib/api";

type Route = (url: URL, init: RequestInit) => unknown;

export interface ApiMock {
  calls: { method: string; path: string; search: string; body: unknown }[];
  fetch: ReturnType<typeof vi.fn>;
}

export function mockApi(routes: Record<string, unknown | Route>): ApiMock {
  const calls: ApiMock["calls"] = [];

  const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const href = String(input);
    const url = new URL(href, "http://localhost");
    const path = href.startsWith(API_BASE) ? url.pathname : url.pathname;
    const method = (init.method ?? "GET").toUpperCase();

    calls.push({
      method,
      path,
      search: url.search,
      body: typeof init.body === "string" ? JSON.parse(init.body) : null,
    });

    const key = `${method} ${path}`;
    const route = key in routes ? routes[key] : routes[path];

    if (route === undefined) {
      return {
        ok: false,
        status: 404,
        headers: new Headers(),
        json: async () => ({ detail: { code: "missing", message: `No mock for ${key}` } }),
      } as unknown as Response;
    }

    const body = typeof route === "function" ? (route as Route)(url, init) : route;

    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => body,
    } as unknown as Response;
  });

  vi.stubGlobal("fetch", fetcher);
  return { calls, fetch: fetcher };
}
