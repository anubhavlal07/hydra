"use client";

import { PHONE_QUERY } from "./media";

const NOT_TYPED = [
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
];

function takesTyping(node: EventTarget | null): boolean {
  if (!(node instanceof HTMLElement)) return false;
  if (node.isContentEditable) return true;
  if (node instanceof HTMLTextAreaElement) return true;
  if (node instanceof HTMLInputElement) return !NOT_TYPED.includes(node.type);
  return false;
}

export function watchKeyboard(): () => void {
  const root = document.documentElement;
  const viewport = window.visualViewport ?? null;
  let settle: ReturnType<typeof setTimeout> | null = null;

  const measure = () => {
    if (!viewport) return;
    const overlap = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
    root.style.setProperty("--hy-keyboard", `${Math.round(overlap)}px`);
  };

  const clear = () => {
    delete root.dataset.keyboard;
    root.style.setProperty("--hy-keyboard", "0px");
  };

  const onFocusIn = (event: FocusEvent) => {
    if (!takesTyping(event.target)) return;
    if (!window.matchMedia(PHONE_QUERY).matches) return;

    root.dataset.keyboard = "true";
    const field = event.target as HTMLElement;

    if (settle) clearTimeout(settle);
    settle = setTimeout(() => {
      measure();
      field.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 320);
  };

  const onFocusOut = () => {
    if (settle) clearTimeout(settle);
    settle = setTimeout(() => {
      if (takesTyping(document.activeElement)) return;
      clear();
    }, 120);
  };

  document.addEventListener("focusin", onFocusIn);
  document.addEventListener("focusout", onFocusOut);
  viewport?.addEventListener("resize", measure);
  viewport?.addEventListener("scroll", measure);

  return () => {
    if (settle) clearTimeout(settle);
    document.removeEventListener("focusin", onFocusIn);
    document.removeEventListener("focusout", onFocusOut);
    viewport?.removeEventListener("resize", measure);
    viewport?.removeEventListener("scroll", measure);
    clear();
  };
}
