import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { installMatchMedia, resetMedia } from "./media";

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", NoopResizeObserver);

Element.prototype.scrollIntoView = vi.fn();
Element.prototype.hasPointerCapture = vi.fn(() => false);
Element.prototype.setPointerCapture = vi.fn();
Element.prototype.releasePointerCapture = vi.fn();

if (!("DOMRect" in globalThis)) {
  vi.stubGlobal("DOMRect", class {});
}

function memoryStorage(): Storage {
  const cells = new Map<string, string>();

  return {
    get length() {
      return cells.size;
    },
    key: (index: number) => [...cells.keys()][index] ?? null,
    getItem: (key: string) => cells.get(key) ?? null,
    setItem: (key: string, value: string) => {
      cells.set(key, String(value));
    },
    removeItem: (key: string) => {
      cells.delete(key);
    },
    clear: () => cells.clear(),
  } as Storage;
}

if (!window.localStorage) {
  vi.stubGlobal("localStorage", memoryStorage());
}

window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;

URL.createObjectURL = vi.fn(() => "blob:hydra-test") as unknown as typeof URL.createObjectURL;
URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
HTMLAnchorElement.prototype.click = vi.fn();

beforeEach(() => {
  localStorage.clear();
  resetMedia();
  installMatchMedia();
  window.history.replaceState(null, "", "/");
});

afterEach(cleanup);
