import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  InfoTip,
  KeyValue,
  WindowScrollbar,
} from "@/components/ui";
import { coarsePointer, finePointer, phoneWidth } from "./media";

describe("InfoTip", () => {
  it("opens on tap where there is no hover", async () => {
    coarsePointer();
    const user = userEvent.setup();

    render(
      <InfoTip label="Why this costs something">
        Every name is fetched again.
      </InfoTip>,
    );

    const trigger = screen.getByRole("button", {
      name: "Why this costs something",
    });
    expect(
      screen.queryByText("Every name is fetched again."),
    ).not.toBeInTheDocument();

    await user.click(trigger);
    expect(
      await screen.findByText("Every name is fetched again."),
    ).toBeInTheDocument();
  });

  it("is a dialog popup on touch, so it is reachable without a pointer", () => {
    coarsePointer();

    render(<InfoTip label="Details">Body</InfoTip>);

    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute(
      "aria-haspopup",
      "dialog",
    );
  });

  it("stays a hover tooltip where a pointer exists", () => {
    finePointer();

    render(<InfoTip label="Details">Body</InfoTip>);

    const trigger = screen.getByRole("button", { name: "Details" });
    expect(trigger).not.toHaveAttribute("aria-haspopup", "dialog");
  });

  it("carries a label so the icon is not the only cue", () => {
    finePointer();

    render(<InfoTip label="What a backup holds">Body</InfoTip>);

    expect(
      screen.getByRole("button", { name: "What a backup holds" }),
    ).toBeInTheDocument();
  });
});

describe("Accordion", () => {
  const Sample = () => (
    <Accordion type="single" collapsible>
      <AccordionItem value="one">
        <AccordionTrigger>First</AccordionTrigger>
        <AccordionContent>First body</AccordionContent>
      </AccordionItem>
      <AccordionItem value="two">
        <AccordionTrigger>Second</AccordionTrigger>
        <AccordionContent>Second body</AccordionContent>
      </AccordionItem>
    </Accordion>
  );

  it("starts closed and opens the panel that was asked for", async () => {
    finePointer();
    const user = userEvent.setup();

    render(<Sample />);
    expect(screen.queryByText("First body")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "First" }));
    expect(await screen.findByText("First body")).toBeInTheDocument();
    expect(screen.queryByText("Second body")).not.toBeInTheDocument();
  });

  it("marks the open panel for assistive tech", async () => {
    finePointer();
    const user = userEvent.setup();

    render(<Sample />);
    const trigger = screen.getByRole("button", { name: "First" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});

describe("WindowScrollbar", () => {
  const tall = () =>
    Object.defineProperty(document.documentElement, "scrollHeight", {
      configurable: true,
      value: 4000,
    });

  it("draws a thumb where there is a pointer to drag it with", () => {
    finePointer();
    tall();

    const { container } = render(<WindowScrollbar />);
    expect(container.querySelector(".hy-window-thumb")).toBeTruthy();
  });

  it("stands down entirely on a touch viewport, which has no use for one", () => {
    coarsePointer();
    tall();

    const { container } = render(<WindowScrollbar />);
    expect(container.querySelector(".hy-window-thumb")).toBeNull();
  });
});

describe("KeyValue", () => {
  it("stacks its label above its value at phone width", () => {
    phoneWidth();
    const { container } = render(<KeyValue rows={[["Path", "/var/lib/hydra/cache.json"]]} />);

    expect(container.querySelector("dl")).toHaveClass("max-wide:grid-cols-1");
    expect(screen.getByText("/var/lib/hydra/cache.json")).toBeInTheDocument();
  });
});
