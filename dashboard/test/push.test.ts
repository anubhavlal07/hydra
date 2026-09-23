import { describe, expect, it, vi } from "vitest";

import { labelFromUserAgent, subscribe, urlBase64ToUint8Array } from "@/lib/push";

describe("device label when client hints are unavailable", () => {
  const label = labelFromUserAgent;

  it("does not report Chrome's frozen Android model as the device name", () => {
    expect(
      label("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/153 Mobile Safari/537.36"),
    ).toBe("Android device");
  });

  it("uses a real model when the agent still carries one", () => {
    expect(
      label("Mozilla/5.0 (Linux; Android 13; moto edge 60) AppleWebKit/537.36 Chrome/119 Mobile"),
    ).toBe("moto edge 60");
  });

  it("never reports a Gecko version string as a device", () => {
    expect(label("Mozilla/5.0 (Android 14; Mobile; rv:109.0) Gecko/124.0 Firefox/124.0")).toBe(
      "Android device",
    );
  });

  it("names Apple devices rather than their OS string", () => {
    expect(label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15")).toBe(
      "iPhone",
    );
    expect(label("Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15")).toBe("iPad");
    expect(label("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15")).toBe("Mac");
  });

  it("names desktops rather than their architecture", () => {
    expect(label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")).toBe("Windows PC");
    expect(label("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")).toBe("Linux PC");
  });

  it("falls back to something rather than nothing", () => {
    expect(label("Mozilla/5.0")).toBe("this browser");
    expect(label("")).toBe("this browser");
  });
});

describe("applicationServerKey encoding", () => {
  it("decodes an unpadded base64url VAPID key to 65 bytes", () => {
    const key =
      "BKM7Z_lQ62KMVmGzYdrzWwhSmNLXifLVqdFnSaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const bytes = urlBase64ToUint8Array(key.slice(0, 87));
    expect(bytes).toBeInstanceOf(Uint8Array);
    expect(bytes.length).toBe(65);
    expect(bytes[0]).toBe(0x04);
  });

  it("handles the - and _ characters base64url uses", () => {
    expect(urlBase64ToUint8Array("-_8")).toEqual(new Uint8Array([251, 255]));
  });
});

describe("subscribing when the push service refuses", () => {
  const refused = () => new DOMException("Registration failed - push service error", "AbortError");
  const wait = () => Promise.resolve();

  function browser(attempts: (() => Promise<unknown>)[], brave = false) {
    const pushManager = {
      getSubscription: vi.fn(async () => null),
      subscribe: vi.fn(() => (attempts.shift() ?? (() => Promise.reject(refused())))()),
    };
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { requestPermission: async () => "granted" },
    });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { ready: Promise.resolve({ pushManager }) },
    });
    Object.defineProperty(navigator, "brave", {
      configurable: true,
      value: brave ? { isBrave: async () => true } : undefined,
    });
    return pushManager;
  }

  it("tries once more, since the first attempt after a permission prompt can fail", async () => {
    const made = { endpoint: "https://push.example/1" };
    const pushManager = browser([() => Promise.reject(refused()), () => Promise.resolve(made)]);

    await expect(subscribe("AAAA", wait)).resolves.toBe(made);
    expect(pushManager.subscribe).toHaveBeenCalledTimes(2);
  });

  it("says where Brave's switch is when Brave refuses twice", async () => {
    browser([], true);
    await expect(subscribe("AAAA", wait)).rejects.toThrow(/brave:\/\/settings\/privacy/);
  });

  it("names a blocked push service rather than echoing the browser's error", async () => {
    browser([]);
    await expect(subscribe("AAAA", wait)).rejects.toThrow(/push service refused the registration/);
  });

  it("leaves a permission refusal alone, though it arrives as the same kind of error", async () => {
    const pushManager = browser([
      () => Promise.reject(new DOMException("Registration failed - permission denied", "AbortError")),
    ]);
    await expect(subscribe("AAAA", wait)).rejects.toThrow("permission denied");
    expect(pushManager.subscribe).toHaveBeenCalledTimes(1);
  });

  it("passes any other failure straight through, without a retry", async () => {
    const pushManager = browser([() => Promise.reject(new DOMException("bad key", "InvalidAccessError"))]);
    await expect(subscribe("AAAA", wait)).rejects.toThrow("bad key");
    expect(pushManager.subscribe).toHaveBeenCalledTimes(1);
  });
});
