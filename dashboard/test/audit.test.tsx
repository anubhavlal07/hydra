import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import Audit from "@/components/Audit";
import { mockApi } from "./api-mock";
import { auditResponse } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

describe("Audit", () => {
  it("shows who made a change and what it touched", async () => {
    reducedMotion();
    mockApi({ "/api/audit": auditResponse() });

    render(<Audit onUnauthorized={noop} />);

    expect(await screen.findByText("device.remove")).toBeInTheDocument();
    expect(screen.getByText("laptop")).toBeInTheDocument();
    expect(screen.getByText("dashboard")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("opens a row to the address and request id behind it", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({ "/api/audit": auditResponse() });

    render(<Audit onUnauthorized={noop} />);
    await user.click(await screen.findByText("device.remove"));

    expect(screen.getByText("203.0.113.10")).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("sends the filters it is showing to the API", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({ "/api/audit": auditResponse() });

    render(<Audit onUnauthorized={noop} />);
    await screen.findByText("device.remove");
    await user.click(screen.getByRole("radio", { name: "Refused" }));

    const last = api.calls.filter((call) => call.path === "/api/audit").at(-1);
    expect(last?.search).toContain("outcome=denied");
  });

  it("says plainly that live entries do not survive a restart", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({ "/api/audit": auditResponse({ source: "history", entries: [], available: false }) });

    render(<Audit onUnauthorized={noop} />);
    await user.click(await screen.findByRole("radio", { name: "History" }));

    expect(await screen.findByText(/HYDRA_AUDIT_PERSIST/)).toBeInTheDocument();
  });

  it("says when nothing has been recorded", async () => {
    reducedMotion();
    mockApi({ "/api/audit": auditResponse({ entries: [], actions: [] }) });

    render(<Audit onUnauthorized={noop} />);

    expect(await screen.findByText(/Nothing since the resolver last started/)).toBeInTheDocument();
  });
});

describe("Audit paging", () => {
  it("pages older entries in and stops offering more", async () => {
    reducedMotion();
    const user = userEvent.setup();
    mockApi({
      "/api/audit": (url: URL) => {
        const paged = url.searchParams.has("before");
        return !paged
          ? auditResponse({ source: "history", next_before: 5 })
          : auditResponse({
              source: "history",
              next_before: null,
              entries: [
                {
                  id: 4,
                  at: 1_699_999_000,
                  actor: "dashboard",
                  address: "203.0.113.10",
                  action: "cache.clear",
                  target: "",
                  outcome: "ok" as const,
                  status: 200,
                  detail: {},
                  request_id: "older1",
                },
              ],
            });
      },
    });

    render(<Audit onUnauthorized={noop} />);
    await user.click(await screen.findByRole("radio", { name: "History" }));
    await user.click(await screen.findByRole("button", { name: /Load older/ }));

    expect(await screen.findByText("cache.clear")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Load older/ })).not.toBeInTheDocument();
    expect(screen.getByText(/of 2 changes/)).toBeInTheDocument();
  });

  it("explains an empty live tab instead of just saying nothing", async () => {
    reducedMotion();
    mockApi({ "/api/audit": auditResponse({ entries: [], actions: [] }) });

    render(<Audit onUnauthorized={noop} />);

    expect(await screen.findByText(/Nothing since the resolver last started/)).toBeInTheDocument();
  });

  it("reads as rows with the detail in a sheet at phone width", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/audit": auditResponse() });

    render(<Audit onUnauthorized={noop} />);

    const row = await screen.findByRole("button", { name: /device\.remove/ });
    expect(document.querySelector("table")).toBeNull();

    await user.click(row);
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText("203.0.113.10")).toBeInTheDocument();
    expect(within(sheet).getByText("abc123")).toBeInTheDocument();
  });

  it("collapses the audit filters into a sheet on a phone", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({ "/api/audit": auditResponse() });

    render(<Audit onUnauthorized={noop} />);
    await screen.findByRole("button", { name: /device\.remove/ });

    await user.click(screen.getByRole("button", { name: /^Filter/ }));
    const sheet = await screen.findByRole("dialog");
    await user.click(within(sheet).getByRole("radio", { name: "Refused" }));

    const last = api.calls.filter((call) => call.path === "/api/audit").at(-1);
    expect(last?.search).toContain("outcome=denied");
  });

  it("offers the filter vocabulary the server reports, not just what is on screen", async () => {
    reducedMotion();
    mockApi({
      "/api/audit": auditResponse({
        entries: [],
        actions: ["device.remove", "policy.update", "token.create"],
      }),
    });

    render(<Audit onUnauthorized={noop} />);
    expect(await screen.findByRole("combobox", { name: /Filter by action/ })).toBeInTheDocument();
  });
});
