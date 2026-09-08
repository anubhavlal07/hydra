"use client";

import { createContext, useContext, useMemo, useSyncExternalStore } from "react";

export interface Route {
  section: string;
  path: string[];
}

const listeners = new Set<() => void>();

function announce() {
  for (const listener of [...listeners]) listener();
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("hashchange", onChange);
    window.removeEventListener("popstate", onChange);
  };
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#/, "").split("/").filter(Boolean).map(decode);
  return { section: parts[0] ?? "", path: parts.slice(1) };
}

export function routeHash(section: string, path: readonly string[] = []): string {
  return `#${[section, ...path].map(encodeURIComponent).join("/")}`;
}

export function historyDepth(): number {
  const state = window.history.state as { hy?: number } | null;
  return typeof state?.hy === "number" ? state.hy : 0;
}

export function writeRoute(section: string, path: readonly string[] = [], replace = false) {
  const hash = routeHash(section, path);
  if (window.location.hash === hash) return;

  if (replace) window.history.replaceState({ hy: historyDepth() }, "", hash);
  else window.history.pushState({ hy: historyDepth() + 1 }, "", hash);

  announce();
}

export function useHash(): string {
  return useSyncExternalStore(
    subscribe,
    () => window.location.hash,
    () => "",
  );
}

export interface Nav {
  route: Route;
  go: (section: string, path?: readonly string[]) => void;
  open: (...path: string[]) => void;
  back: () => void;
}

export const NavContext = createContext<Nav | null>(null);

export function useNav(): Nav {
  const provided = useContext(NavContext);
  const hash = useHash();

  const loose = useMemo<Nav>(() => {
    const route = parseRoute(hash);
    return {
      route,
      go: (section, path = []) => writeRoute(section, path),
      open: (...path) => writeRoute(route.section, [...route.path, ...path]),
      back: () => {
        if (historyDepth() > 0) {
          window.history.back();
          return;
        }
        writeRoute(route.section, route.path.slice(0, -1), true);
      },
    };
  }, [hash]);

  return provided ?? loose;
}

export interface Section {
  path: string[];
  open: (...path: string[]) => void;
  go: (section: string, path?: readonly string[]) => void;
  back: () => void;
}

export function useSection(section: string): Section {
  const nav = useNav();
  const path = nav.route.section === section ? nav.route.path : [];

  return {
    path,
    open: (...next) => nav.go(section, [...path, ...next]),
    go: nav.go,
    back: nav.back,
  };
}
