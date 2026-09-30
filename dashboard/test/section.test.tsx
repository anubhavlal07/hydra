import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Group, Row, Section } from "@/components/ui";
import { phoneWidth } from "./media";

const subject = () => (
  <Section title="Upstream accounts" meta="2 pooled">
    <p>the detail</p>
  </Section>
);

describe("Section", () => {
  it("is open and has no toggle above phone width", () => {
    render(subject());

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("the detail")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Upstream accounts/ })).toBeInTheDocument();
  });

  it("no longer folds at phone width, so no chevron promises a screen it cannot open", () => {
    phoneWidth();
    const { container } = render(subject());

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("the detail")).toBeVisible();
    expect(container.querySelector(".hy-foldable")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("keeps a summary visible beside its heading", () => {
    phoneWidth();
    render(
      <Section title="alpha" summary={<span>30,000 of 300,000</span>} level={3}>
        <p>the detail</p>
      </Section>,
    );

    expect(screen.getByText("30,000 of 300,000")).toBeInTheDocument();
  });
});

describe("what replaced the fold on a phone", () => {
  it("is a group whose chevron genuinely pushes a subscreen", () => {
    phoneWidth();
    let pushed = false;

    render(
      <Group heading="Upstream accounts" footer="Every account in the pool.">
        <Row title="alpha" value="2 pooled" onClick={() => (pushed = true)} />
      </Group>,
    );

    const row = screen.getByRole("button", { name: /alpha/ });
    expect(row.querySelector("svg")).toBeTruthy();

    row.click();
    expect(pushed).toBe(true);
  });
});
