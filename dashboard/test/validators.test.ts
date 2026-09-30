import { beforeEach, describe, expect, it, vi } from "vitest";

import { forgetValidators, getDevices, logout } from "@/lib/api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, etag?: string) {
  return {
    status: 200,
    ok: true,
    headers: new Headers(etag ? { etag } : {}),
    json: async () => body,
  } as unknown as Response;
}

function notModified() {
  return {
    status: 304,
    ok: false,
    headers: new Headers(),
    json: async () => {
      throw new Error("a 304 carries no body");
    },
  } as unknown as Response;
}

function sentHeaders(call: number): Headers {
  return new Headers(fetchMock.mock.calls[call][1].headers as HeadersInit);
}

beforeEach(() => {
  forgetValidators();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("conditional polling", () => {
  it("sends no validator until the server has given one", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }));
    await getDevices();
    expect(sentHeaders(0).has("if-none-match")).toBe(false);
  });

  it("replays the remembered payload when the server says 304", async () => {
    const payload = { devices: [{ id: "laptop" }] };
    fetchMock.mockResolvedValueOnce(jsonResponse(payload, '"abc"'));
    fetchMock.mockResolvedValueOnce(notModified());

    const first = await getDevices();
    const second = await getDevices();

    expect(sentHeaders(1).get("if-none-match")).toBe('"abc"');
    expect(second).toEqual(first);
    expect(second).toBe(first);
  });

  it("takes the new body when the validator has moved on", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }, '"one"'));
    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [{ id: "tv" }] }, '"two"'));

    await getDevices();
    const second = await getDevices();

    expect(second).toEqual({ devices: [{ id: "tv" }] });

    fetchMock.mockResolvedValueOnce(notModified());
    await getDevices();
    expect(sentHeaders(2).get("if-none-match")).toBe('"two"');
  });

  it("forgets everything on the way out, so the next sign-in starts clean", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }, '"abc"'));
    await getDevices();

    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await logout();

    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }, '"abc"'));
    await getDevices();
    expect(sentHeaders(2).has("if-none-match")).toBe(false);
  });

  it("drops the validator when a request is refused", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }, '"abc"'));
    await getDevices();

    fetchMock.mockResolvedValueOnce({
      status: 401,
      ok: false,
      headers: new Headers(),
      json: async () => ({ detail: { code: "unauthorized", message: "no" } }),
    } as unknown as Response);
    await expect(getDevices()).rejects.toMatchObject({ status: 401 });

    fetchMock.mockResolvedValueOnce(jsonResponse({ devices: [] }, '"abc"'));
    await getDevices();
    expect(sentHeaders(2).has("if-none-match")).toBe(false);
  });
});
