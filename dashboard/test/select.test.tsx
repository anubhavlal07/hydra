import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Select, type SelectGroup } from "@/components/ui";

const GROUPS: SelectGroup[] = [
  { options: [{ value: "", label: "All devices" }] },
  {
    label: "Devices",
    options: [
      { value: "laptop", label: "Work Laptop", note: "4.2K" },
      { value: "pixel", label: "Pixel 9", note: "1.9K" },
      { value: "tv", label: "Living room TV" },
    ],
  },
  { label: "Seen, not registered", options: [{ value: "anonymous", label: "anonymous" }] },
];

function Harness({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <Select groups={GROUPS} value={value} onChange={setValue} label="Filter by device" />
      <output data-testid="value">{value === "" ? "(none)" : value}</output>
    </>
  );
}

const trigger = () => screen.getByRole("combobox", { name: "Filter by device", hidden: true });
const chosen = () => screen.getByTestId("value").textContent;
const listbox = () => screen.getByRole("listbox");

describe("Select", () => {
  it("shows the selected option and keeps the menu closed until asked", () => {
    render(<Harness initial="pixel" />);

    expect(trigger()).toHaveTextContent("Pixel 9");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("renders an empty value without handing Radix an empty item value", () => {
    render(<Harness />);

    expect(trigger()).toHaveTextContent("All devices");
    expect(chosen()).toBe("(none)");
  });

  it("lists every option, in its group, with the current one marked", async () => {
    const user = userEvent.setup();
    render(<Harness initial="tv" />);

    await user.click(trigger());

    const options = within(listbox()).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "All devices",
      "Work Laptop4.2K",
      "Pixel 91.9K",
      "Living room TV",
      "anonymous",
    ]);
    expect(within(listbox()).getByRole("option", { selected: true })).toHaveTextContent(
      "Living room TV",
    );
  });

  it("keeps the trigger labelled as a combobox that owns the list", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());

    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    expect(listbox()).toBeInTheDocument();
    expect(listbox()).toHaveAttribute("aria-label", "Filter by device");
  });

  it("opens from the keyboard, moves with the arrows and commits with Enter", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    trigger().focus();
    await user.keyboard("{ArrowDown}");
    expect(listbox()).toBeInTheDocument();

    await user.keyboard("{ArrowDown}{ArrowDown}");
    await user.keyboard("{Enter}");

    expect(chosen()).toBe("pixel");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("closes on Escape without changing anything", async () => {
    const user = userEvent.setup();
    render(<Harness initial="laptop" />);

    trigger().focus();
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Escape}");

    expect(chosen()).toBe("laptop");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("jumps to an option by typing its name", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    trigger().focus();
    await user.keyboard("{ArrowDown}");
    await user.keyboard("liv");
    await user.keyboard("{Enter}");

    expect(chosen()).toBe("tv");
  });

  it("selects by pointer and closes", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());
    await user.click(screen.getByRole("option", { name: /Work Laptop/ }));

    expect(chosen()).toBe("laptop");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes when the pointer goes down outside it", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    render(<Harness />);

    await user.click(trigger());
    expect(listbox()).toBeInTheDocument();

    await user.click(document.body);
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
  });
});
