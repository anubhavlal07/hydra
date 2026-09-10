type Matcher = (query: string) => boolean;

let matcher: Matcher = () => false;
const listeners = new Set<() => void>();

export function setMedia(next: Matcher) {
  matcher = next;
  for (const listener of listeners) listener();
}

export function resetMedia() {
  matcher = () => false;
  listeners.clear();
}

export function phoneWidth() {
  setMedia(
    (query) => query.includes("max-width: 760px") || query.includes("prefers-reduced-motion"),
  );
}

export function reducedMotion() {
  setMedia((query) => query.includes("prefers-reduced-motion"));
}

export function installMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      get matches() {
        return matcher(query);
      },
      media: query,
      onchange: null,
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
      addListener: (listener: () => void) => listeners.add(listener),
      removeListener: (listener: () => void) => listeners.delete(listener),
      dispatchEvent: () => false,
    }),
  });
}

export function coarsePointer() {
  setMedia(
    (query) =>
      query.includes("hover: none") ||
      query.includes("pointer: coarse") ||
      query.includes("prefers-reduced-motion"),
  );
}

export function finePointer() {
  setMedia((query) => query.includes("prefers-reduced-motion"));
}
