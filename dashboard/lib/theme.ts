"use client";

import { useSyncExternalStore } from "react";
import { flushSync } from "react-dom";

import { CHROME_BLACK, CHROME_DARK_MEDIA, CHROME_ID } from "./chrome";

export type Theme = "light" | "dark";
export type Preference = Theme | "system";

const KEY = "hydra-theme";
const BLACK_KEY = "hydra-black";

const watchers = new Set<() => void>();

function announce() {
  for (const watcher of [...watchers]) watcher();
}

function subscribe(onChange: () => void) {
  watchers.add(onChange);
  return () => watchers.delete(onChange);
}

export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function storedPreference(): Preference {
  return storedTheme() ?? "system";
}

export function storedBlack(): boolean {
  try {
    return localStorage.getItem(BLACK_KEY) === "1";
  } catch {
    return false;
  }
}

export function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function currentTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

export function usePreference(): Preference {
  return useSyncExternalStore(subscribe, storedPreference, () => "system" as Preference);
}

export function useBlack(): boolean {
  return useSyncExternalStore(subscribe, storedBlack, () => false);
}

function remember(preference: Preference) {
  try {
    if (preference === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, preference);
  } catch {}
  announce();
}

function rememberBlack(black: boolean) {
  try {
    if (black) localStorage.setItem(BLACK_KEY, "1");
    else localStorage.removeItem(BLACK_KEY);
  } catch {}
  announce();
}

function paintChrome(black: boolean) {
  const present = document.getElementById(CHROME_ID);

  if (!black) {
    present?.remove();
    return;
  }
  if (present) return;

  const meta = document.createElement("meta");
  meta.id = CHROME_ID;
  meta.setAttribute("name", "theme-color");
  meta.setAttribute("media", CHROME_DARK_MEDIA);
  meta.setAttribute("content", CHROME_BLACK);
  document.head.insertBefore(meta, document.head.firstChild);
}

function wipe({
  commit,
  origin,
  reduced,
}: {
  commit: () => void;
  origin?: DOMRect;
  reduced: boolean;
}) {
  const root = document.documentElement;
  const start = document.startViewTransition?.bind(document);

  if (reduced || !start) {
    root.classList.add("hy-theme-shift");
    window.setTimeout(() => root.classList.remove("hy-theme-shift"), 260);
    commit();
    return;
  }

  root.classList.add("hy-theme-wipe");
  const running = start(() => flushSync(commit));

  running.ready
    .then(() => {
      const x = origin ? origin.left + origin.width / 2 : window.innerWidth;
      const y = origin ? origin.top + origin.height / 2 : 0;
      const reach = Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y),
      );

      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${reach}px at ${x}px ${y}px)`] },
        {
          duration: 520,
          easing: "cubic-bezier(0.2, 0, 0, 1)",
          pseudoElement: "::view-transition-new(root)",
        },
      );
    })
    .catch(() => {});

  running.finished.finally(() => root.classList.remove("hy-theme-wipe")).catch(() => {});
}

export function wipeTheme({
  next,
  origin,
  reduced,
  apply,
}: {
  next: Theme;
  origin?: DOMRect;
  reduced: boolean;
  apply?: (theme: Theme) => void;
}) {
  wipe({
    origin,
    reduced,
    commit: () => {
      apply?.(next);
      document.documentElement.dataset.theme = next;
      remember(next);
    },
  });
}

export function wipePreference({
  next,
  origin,
  reduced,
  apply,
}: {
  next: Preference;
  origin?: DOMRect;
  reduced: boolean;
  apply?: (theme: Theme) => void;
}) {
  const resolved = next === "system" ? systemTheme() : next;

  wipe({
    origin,
    reduced,
    commit: () => {
      apply?.(resolved);
      const root = document.documentElement;
      if (next === "system") delete root.dataset.theme;
      else root.dataset.theme = resolved;
      remember(next);
    },
  });
}

export function wipeBlack({
  next,
  origin,
  reduced,
}: {
  next: boolean;
  origin?: DOMRect;
  reduced: boolean;
}) {
  wipe({
    origin,
    reduced,
    commit: () => {
      const root = document.documentElement;
      if (next) root.dataset.black = "true";
      else delete root.dataset.black;
      rememberBlack(next);
      paintChrome(next);
    },
  });
}
