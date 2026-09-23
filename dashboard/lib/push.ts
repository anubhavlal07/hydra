"use client";

export type PushState =
  | "unsupported"
  | "denied"
  | "unsubscribed"
  | "subscribed"
  | "unavailable";

export function urlBase64ToUint8Array(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) out[index] = raw.charCodeAt(index);
  return out;
}

export function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function describeSubscription(subscription: PushSubscription) {
  const json = subscription.toJSON() as { keys?: { p256dh?: string; auth?: string } };
  return {
    endpoint: subscription.endpoint,
    p256dh: json.keys?.p256dh ?? "",
    auth: json.keys?.auth ?? "",
  };
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!supported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

interface BraveNavigator {
  brave?: { isBrave?: () => Promise<boolean> };
}

const RETRY_MS = 1500;

function refusedByPushService(cause: unknown): boolean {
  return (
    cause instanceof DOMException &&
    cause.name === "AbortError" &&
    !/permission/i.test(cause.message)
  );
}

async function isBrave(): Promise<boolean> {
  try {
    return (await (navigator as Navigator & BraveNavigator).brave?.isBrave?.()) === true;
  } catch {
    return false;
  }
}

export async function refusalMessage(): Promise<string> {
  return (await isBrave())
    ? "Brave's push service is off. Turn on Use Google services for push messaging in brave://settings/privacy, then try again."
    : "This browser's push service refused the registration. A VPN or firewall blocking it is the usual cause; try again once it can reach it.";
}

export async function subscribe(
  publicKey: string,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((done) => setTimeout(done, ms)),
): Promise<PushSubscription> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notifications were not allowed");

  const registration = await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  if (existing) return existing;

  const options = {
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
  };

  try {
    return await registration.pushManager.subscribe(options);
  } catch (cause) {
    if (!refusedByPushService(cause)) throw cause;
  }

  await wait(RETRY_MS);
  try {
    return await registration.pushManager.subscribe(options);
  } catch (cause) {
    if (!refusedByPushService(cause)) throw cause;
    throw new Error(await refusalMessage());
  }
}

interface HighEntropyHints {
  model?: string;
  platform?: string;
  platformVersion?: string;
}

interface UserAgentData {
  getHighEntropyValues?: (hints: string[]) => Promise<HighEntropyHints>;
}

export function labelFromUserAgent(agent: string): string {
  const inside = agent.match(/\(([^)]*)\)/)?.[1] ?? "";
  const parts = inside.split(";").map((part) => part.trim());

  const android = parts.find((part) => /^Android/i.test(part));
  if (android) {
    const model = parts[parts.length - 1];
    const useless = !model || model === "K" || model === "Mobile" || /^rv:/i.test(model);
    return useless ? "Android device" : model;
  }

  if (parts.some((part) => /iPhone/i.test(part))) return "iPhone";
  if (parts.some((part) => /iPad/i.test(part))) return "iPad";
  if (parts.some((part) => /Windows/i.test(part))) return "Windows PC";
  if (parts.some((part) => /Mac OS X|Macintosh/i.test(part))) return "Mac";
  if (parts.some((part) => /CrOS/i.test(part))) return "Chromebook";
  if (parts.some((part) => /Linux/i.test(part))) return "Linux PC";

  return "this browser";
}

export async function deviceLabel(): Promise<string> {
  if (typeof navigator === "undefined") return "";

  const data = (navigator as Navigator & { userAgentData?: UserAgentData }).userAgentData;
  if (data?.getHighEntropyValues) {
    try {
      const hints = await data.getHighEntropyValues(["model", "platform", "platformVersion"]);
      const model = (hints.model ?? "").trim();
      if (model) return model.slice(0, 60);

      const platform = (hints.platform ?? "").trim();
      if (platform) {
        const version = (hints.platformVersion ?? "").split(".")[0];
        return `${platform}${version ? ` ${version}` : ""}`.slice(0, 60);
      }
    } catch {
      return labelFromUserAgent(navigator.userAgent).slice(0, 60);
    }
  }

  return labelFromUserAgent(navigator.userAgent).slice(0, 60);
}
