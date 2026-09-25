import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import RuleGroups from "@/components/RuleGroups";
import { mockApi } from "./api-mock";
import { ruleGroups } from "./fixtures";
import { phoneWidth, reducedMotion } from "./media";

const noop = () => {};

describe("Rule groups", () => {
  it("says who each schedule reaches and when, without opening it", async () => {
    reducedMotion();
    mockApi({ "/api/rule-groups": ruleGroups() });

    render(<RuleGroups onUnauthorized={noop} />);

    expect(await screen.findByText("Bedtime")).toBeInTheDocument();
    expect(
      screen.getByText("2 names · kids · 10 PM–7 AM, Mon Tue Wed Thu Fri"),
    ).toBeInTheDocument();
    expect(screen.getByText("1 name · every device · every day")).toBeInTheDocument();
  });

  it("marks the schedule that is in force right now", async () => {
    reducedMotion();
    mockApi({ "/api/rule-groups": ruleGroups() });

    render(<RuleGroups onUnauthorized={noop} />);
    await screen.findByText("Trackers");

    expect(screen.getAllByText("on now")).toHaveLength(1);
  });

  it("says so when nothing can be changed", async () => {
    reducedMotion();
    mockApi({ "/api/rule-groups": ruleGroups({ editable: false }) });

    render(<RuleGroups onUnauthorized={noop} />);

    expect(await screen.findByText(/schedules can be read but not changed/)).toBeInTheDocument();
  });

  it("says there is nothing yet rather than showing an empty box", async () => {
    reducedMotion();
    mockApi({ "/api/rule-groups": ruleGroups({ groups: [], active: [] }) });

    render(<RuleGroups onUnauthorized={noop} />);

    expect(await screen.findByText(/No schedule yet/)).toBeInTheDocument();
  });
});

describe("Rule groups on a phone", () => {
  it("drills into a schedule and writes back what was changed", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/groups/bedtime"));
    expect(screen.getByRole("heading", { name: "Bedtime" })).toBeInTheDocument();

    const names = screen.getByLabelText("Names");
    await user.clear(names);
    await user.paste("tiktok.com");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toEqual({
        name: "Bedtime",
        rules: ["tiktok.com"],
        tags: ["kids"],
        days: [0, 1, 2, 3, 4],
        starts_at: "22:00",
        ends_at: "07:00",
        enabled: true,
      }),
    );
  });

  it("turns a day off and keeps the rest", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    const days = screen.getByRole("group", { name: "Days" });
    expect(within(days).getByRole("button", { name: "Mon" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(within(days).getByRole("button", { name: "Mon" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { days: number[] }).days,
      ).toEqual([1, 2, 3, 4]),
    );
  });

  it("names a new schedule from what was typed", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/homework-hours": ruleGroups().groups[0],
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a schedule" }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/groups/new"));
    expect(screen.getByRole("heading", { name: "New schedule" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(screen.getByLabelText("Name"), "Homework hours");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.calls.some((call) => call.path === "/api/rule-groups/homework-hours")).toBe(
        true,
      ),
    );
  });

  it("asks before deleting a schedule", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "DELETE /api/rule-groups/bedtime": { removed: true, id: "bedtime" },
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));
    await user.click(await screen.findByRole("button", { name: /Delete schedule/ }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete schedule" }),
    );

    await waitFor(() => expect(api.calls.some((call) => call.method === "DELETE")).toBe(true));
  });
});

describe("Deleting a schedule on a wide screen", () => {
  it("asks before deleting, and Cancel deletes nothing", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "DELETE /api/rule-groups/bedtime": { removed: true, id: "bedtime" },
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await screen.findByText("Bedtime");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await user.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete Bedtime?")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete schedule" }),
    );

    await waitFor(() => expect(api.calls.some((call) => call.method === "DELETE")).toBe(true));
  });
});

describe("Choosing a schedule's hours", () => {
  it("names noon and midnight, and shows how long the window runs", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const night = {
      ...ruleGroups().groups[0],
      starts_at: "12:00",
      ends_at: "05:00",
      when: "12:00–05:00, every day",
      days: [],
    };
    const api = mockApi({
      "/api/rule-groups": ruleGroups({ groups: [night] }),
      "PUT /api/rule-groups/bedtime": night,
    });

    render(<RuleGroups onUnauthorized={noop} />);
    expect(await screen.findByText(/noon–5 AM, every day/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Bedtime/ }));

    expect(screen.getByText(/noon → 5 AM next day · 17 hours/)).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Starts at hour", hidden: true }));
    await user.click(screen.getByRole("option", { name: "12 AM · midnight" }));

    expect(screen.getByText(/midnight → 5 AM · 5 hours/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { starts_at: string })
          .starts_at,
      ).toBe("00:00"),
    );
  });

  it("clears both times when all day is chosen", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });

    render(<RuleGroups onUnauthorized={noop} />);
    await screen.findByText("Bedtime");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await user.click(screen.getByRole("radio", { name: "All day" }));

    expect(screen.queryByRole("group", { name: "Starts at" })).not.toBeInTheDocument();
    expect(screen.getByText(/all day ·/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.calls.find((call) => call.method === "PUT")?.body).toMatchObject({
        starts_at: "",
        ends_at: "",
      }),
    );
  });
});

describe("Opening a schedule from the rules screen", () => {
  it("fills the form with what the schedule already holds", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/rule-groups": ruleGroups() });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/groups/bedtime"));
    expect(screen.getByLabelText("Name")).toHaveValue("Bedtime");
    expect(
      screen.getByRole("combobox", { name: "Starts at hour", hidden: true }),
    ).toHaveTextContent("10 PM");
    expect(screen.getByLabelText("Names")).toHaveValue("tiktok.com\ninstagram.com");
  });

  it("opens a blank form for a new schedule", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/rule-groups": ruleGroups() });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a schedule" }));

    await waitFor(() => expect(window.location.hash).toBe("#rules/groups/new"));
    expect(screen.getByRole("heading", { name: "New schedule" })).toBeInTheDocument();
  });
});

describe("Choosing who a schedule reaches", () => {
  it("offers the tags already in use rather than asking you to remember them", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    const tags = screen.getByRole("group", { name: "Tags" });
    expect(within(tags).getByRole("button", { name: "kids" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(tags).getByRole("button", { name: "work" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    await user.click(within(tags).getByRole("button", { name: "work" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { tags: string[] }).tags,
      ).toEqual(["kids", "work"]),
    );
  });

  it("still takes a tag no device carries yet", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups(),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    await user.type(screen.getByLabelText("Add a tag"), "Tablet");
    await user.click(screen.getByRole("button", { name: "Add" }));

    const tags = screen.getByRole("group", { name: "Tags" });
    expect(within(tags).getByRole("button", { name: "tablet" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { tags: string[] }).tags,
      ).toEqual(["kids", "tablet"]),
    );
  });

  it("says when the chosen tags reach no device at all", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({ "/api/rule-groups": ruleGroups({ tags: ["phone"] }) });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    expect(screen.getByText(/No device carries kids/)).toBeInTheDocument();
  });

  it("flags a schedule that reaches nothing in the list", async () => {
    reducedMotion();
    mockApi({ "/api/rule-groups": ruleGroups({ tags: ["phone"] }) });

    render(<RuleGroups onUnauthorized={noop} />);

    expect(await screen.findByText("no devices")).toBeInTheDocument();
  });
});

describe("Turning a schedule on", () => {
  it("writes the moment the switch is flipped, with no Save", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups({
        groups: [{ ...ruleGroups().groups[0], enabled: false }],
        active: [],
      }),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    await waitFor(() =>
      expect(
        (api.calls.find((call) => call.method === "PUT")?.body as { enabled: boolean }).enabled,
      ).toBe(true),
    );
  });

  it("keeps unsaved edits out of that write", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({
      "/api/rule-groups": ruleGroups({
        groups: [{ ...ruleGroups().groups[0], enabled: false }],
        active: [],
      }),
      "PUT /api/rule-groups/bedtime": ruleGroups().groups[0],
    });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));

    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Later");
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    await waitFor(() => expect(api.calls.some((call) => call.method === "PUT")).toBe(true));

    const sent = api.calls.find((call) => call.method === "PUT")?.body as {
      name: string;
      enabled: boolean;
    };
    expect(sent.enabled).toBe(true);
    expect(sent.name).toBe("Bedtime");
    expect(screen.getByLabelText("Name")).toHaveValue("Later");
  });

  it("puts the switch back when the write is refused", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mockApi({
      "/api/rule-groups": ruleGroups({
        groups: [{ ...ruleGroups().groups[0], enabled: false }],
        active: [],
      }),
    });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: /Bedtime/ }));
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    expect(await screen.findByText(/No mock for PUT/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
  });

  it("leaves a schedule that does not exist yet to Save", async () => {
    phoneWidth();
    const user = userEvent.setup();
    const api = mockApi({ "/api/rule-groups": ruleGroups() });
    window.history.replaceState(null, "", "#rules/groups");

    render(<RuleGroups onUnauthorized={noop} />);
    await user.click(await screen.findByRole("button", { name: "Add a schedule" }));
    await user.click(screen.getByRole("switch", { name: "Enabled" }));

    expect(api.calls.some((call) => call.method === "PUT")).toBe(false);
  });
});
