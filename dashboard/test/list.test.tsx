import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Chip, CopyRow, Group, Row, RowBlock, Switch } from "@/components/ui";
import { coarsePointer } from "./media";

describe("Group", () => {
  it("puts its heading above the rows and its explanation below them", () => {
    const { container } = render(
      <Group heading="Filtering" footer="Written to every account at once.">
        <Row title="Custom rules" value="12" onClick={() => {}} />
      </Group>,
    );

    const group = container.querySelector('[data-slot="group"]')!;
    const heading = screen.getByRole("heading", { name: "Filtering" });
    const footer = screen.getByText("Written to every account at once.");
    const rows = group.querySelector('[data-slot="row"]')!;

    expect(heading.compareDocumentPosition(rows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rows.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("tucks an explanation behind a tip instead of printing it under the rows", async () => {
    coarsePointer();
    const user = userEvent.setup();

    render(
      <Group heading="Backup" info="Accounts live in a file on the host.">
        <Row title="Download a backup" onClick={() => {}} />
      </Group>,
    );

    expect(screen.queryByText("Accounts live in a file on the host.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "More information" }));
    expect(await screen.findByText("Accounts live in a file on the host.")).toBeInTheDocument();
  });

  it("carries a tip even with no heading to hang it on", () => {
    coarsePointer();

    render(
      <Group info="Restoring adds what is missing.">
        <Row title="Restore" onClick={() => {}} />
      </Group>,
    );

    expect(screen.getByRole("button", { name: "More information" })).toBeInTheDocument();
  });

  it("draws one bordered surface, so a group never sits inside another", () => {
    const { container } = render(
      <Group heading="Pool">
        <Row title="Accounts" onClick={() => {}} />
      </Group>,
    );

    expect(container.querySelectorAll(".border-line")).toHaveLength(1);
  });
});

describe("Row", () => {
  it("meets the 44px tap target floor", () => {
    render(
      <Group>
        <Row title="Blocklists" onClick={() => {}} />
      </Group>,
    );

    expect(screen.getByRole("button", { name: "Blocklists" })).toHaveClass("min-h-12");
  });

  it("is a button that navigates when it drills down", async () => {
    const opened = vi.fn();
    render(
      <Group>
        <Row title="Custom rules" value="12 rules" onClick={opened} />
      </Group>,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: /Custom rules/ }));
    expect(opened).toHaveBeenCalledOnce();
  });

  it("never shows a switch and a chevron in the same row", () => {
    const { container } = render(
      <Group>
        <Row title="Safe browsing" trailing={<Switch checked aria-label="Safe browsing" />} />
        <Row title="Blocklists" onClick={() => {}} />
      </Group>,
    );

    const rows = container.querySelectorAll('[data-slot="row"]');
    expect(rows[0].querySelector('[data-slot="switch"]')).toBeTruthy();
    expect(rows[0].querySelectorAll("svg")).toHaveLength(0);
    expect(rows[1].querySelectorAll("svg")).toHaveLength(1);
  });

  it("carries its state on a second line rather than a second column", () => {
    render(
      <Group>
        <Row title="Blocklists" secondary="2 of 4 accounts disagree" onClick={() => {}} />
      </Group>,
    );

    const row = screen.getByRole("button", { name: /Blocklists/ });
    expect(row).toHaveAttribute("data-lines", "2");
    expect(row).toHaveClass("data-[lines=2]:min-h-15");
    expect(screen.getByText("2 of 4 accounts disagree")).toBeInTheDocument();
  });

  it("insets the separator to the text when a row has a leading icon", () => {
    render(
      <Group>
        <Row icon={<span />} title="Lookup" onClick={() => {}} />
      </Group>,
    );

    expect(screen.getByRole("button", { name: "Lookup" })).toHaveAttribute("data-lead", "true");
  });

  it("colours an action row's title when it has no icon to carry the accent", () => {
    render(
      <Group>
        <Row tone="accent" title="Sync accounts" chevron={false} onClick={() => {}} />
        <Row tone="accent" icon={<span />} title="Add a device" onClick={() => {}} />
      </Group>,
    );

    expect(screen.getByText("Sync accounts")).toHaveClass("text-accent-ink");
    expect(screen.getByText("Add a device")).toHaveClass("text-fg");
  });

  it("keeps a block of free content reachable inside a group", () => {
    render(
      <Group>
        <RowBlock>Device storage is not responding.</RowBlock>
      </Group>,
    );

    expect(screen.getByText("Device storage is not responding.")).toBeInTheDocument();
  });
});

describe("CopyRow", () => {
  it("copies the value it shows and says it did", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <Group>
        <CopyRow title="DoH" value="https://dns.example/dns-query/abc" />
      </Group>,
    );

    await user.click(screen.getByRole("button", { name: "Copy DoH" }));

    expect(writeText).toHaveBeenCalledWith("https://dns.example/dns-query/abc");
    expect(await screen.findByRole("button", { name: "DoH copied" })).toBeInTheDocument();
  });

  it("wraps the value rather than clipping the tail that identifies it", () => {
    render(
      <Group>
        <CopyRow title="DoH" value="https://dns.example/dns-query/abcdefgh12345678" />
      </Group>,
    );

    expect(screen.getByText("https://dns.example/dns-query/abcdefgh12345678")).toHaveClass(
      "wrap-anywhere",
    );
  });
});

describe("Chip", () => {
  it("removes the filter it stands for", async () => {
    const removed = vi.fn();
    render(
      <Chip label="Clear blocked filter" onRemove={removed}>
        blocked
      </Chip>,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "Clear blocked filter" }));
    expect(removed).toHaveBeenCalledOnce();
  });
});
