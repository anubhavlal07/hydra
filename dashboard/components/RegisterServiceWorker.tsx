"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import { Button, Toasts } from "./ui";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISSED_KEY = "hydra-install-dismissed";

export default function RegisterServiceWorker() {
  const [waiting, setWaiting] = useState<ServiceWorker | null>(null);
  const [installEvent, setInstallEvent] = useState<InstallPromptEvent | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;

    const register = async () => {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });

        if (reg.waiting) setWaiting(reg.waiting);
        reg.addEventListener("updatefound", () => {
          const next = reg.installing;
          if (!next) return;
          next.addEventListener("statechange", () => {
            if (next.state === "installed" && navigator.serviceWorker.controller) {
              setWaiting(next);
            }
          });
        });
      } catch {}
    };

    if (document.readyState === "complete") void register();
    else window.addEventListener("load", () => void register(), { once: true });
  }, []);

  useEffect(() => {
    const onPrompt = (event: Event) => {
      event.preventDefault();
      try {
        if (localStorage.getItem(DISMISSED_KEY)) return;
      } catch {}
      setInstallEvent(event as InstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  const applyUpdate = () => {
    waiting?.postMessage("skip-waiting");
    navigator.serviceWorker.addEventListener("controllerchange", () => window.location.reload(), {
      once: true,
    });
  };

  const install = async () => {
    if (!installEvent) return;
    await installEvent.prompt();
    await installEvent.userChoice;
    setInstallEvent(null);
  };

  const dismissInstall = () => {
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {}

    setLeaving(true);
    window.setTimeout(() => {
      setInstallEvent(null);
      setLeaving(false);
    }, 200);
  };

  if (!waiting && !installEvent) return null;

  return (
    <Toasts>
      <div
        className={cn(
          "pointer-events-auto flex max-w-[min(32rem,calc(100vw-2rem))] flex-wrap items-center justify-center gap-2.5 rounded-lg border border-line-strong bg-raised px-3.5 py-3 text-sm shadow-lg",
          leaving ? "hy-toast-out" : "hy-toast-in",
        )}
      >
        {waiting ? (
          <>
            <span>A new version is ready.</span>
            <Button variant="primary" size="sm" onClick={applyUpdate}>
              Refresh
            </Button>
          </>
        ) : (
          <>
            <span>Install Hydra for full-screen access.</span>
            <Button variant="primary" size="sm" onClick={() => void install()}>
              Install
            </Button>
            <Button size="sm" onClick={dismissInstall}>
              Not now
            </Button>
          </>
        )}
      </div>
    </Toasts>
  );
}
