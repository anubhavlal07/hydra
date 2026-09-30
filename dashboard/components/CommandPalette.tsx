"use client";

import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { Scroller } from "./ui";

export interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
}

function score(command: Command, needle: string): number {
  if (!needle) return 1;
  const label = command.label.toLowerCase();
  const hint = (command.hint ?? "").toLowerCase();

  if (label.startsWith(needle)) return 3;
  if (label.includes(needle)) return 2;
  if (hint.includes(needle)) return 1;
  return 0;
}

export function matching(commands: Command[], query: string): Command[] {
  const needle = query.trim().toLowerCase();
  return commands
    .map((command) => ({ command, rank: score(command, needle) }))
    .filter((entry) => entry.rank > 0)
    .sort((a, b) => b.rank - a.rank)
    .map((entry) => entry.command)
    .slice(0, 12);
}

export default function CommandPalette({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => matching(commands, query), [commands, query]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
        setQuery("");
        setHighlighted(0);
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => setHighlighted(0), [query]);

  if (!open) return null;

  const choose = (command: Command | undefined) => {
    if (!command) return;
    setOpen(false);
    command.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((current) => Math.min(results.length - 1, current + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((current) => Math.max(0, current - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(results[highlighted]);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-overlay p-4 pt-[12vh]"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-surface shadow-lg"
      >
        <div className="flex items-center gap-2 border-b border-line px-3.5">
          <Search className="size-4 shrink-0 text-subtle" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Jump to a section, device or action"
            aria-label="Search commands"
            aria-controls="hy-command-list"
            role="combobox"
            aria-expanded="true"
            className="min-h-11 w-full border-0 bg-transparent text-base text-fg outline-none placeholder:text-subtle"
          />
        </div>

        <Scroller className="max-h-[50vh]">
          <ul id="hy-command-list" role="listbox" className="m-0 list-none p-1.5">
            {results.length === 0 && (
              <li className="px-2.5 py-3 text-sm text-muted">Nothing matches that.</li>
            )}
            {results.map((command, index) => (
              <li key={command.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === highlighted}
                  onMouseEnter={() => setHighlighted(index)}
                  onClick={() => choose(command)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm outline-none",
                    index === highlighted ? "bg-accent-soft text-accent-ink" : "text-fg",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{command.label}</span>
                  <span className="shrink-0 text-xs text-subtle">{command.group}</span>
                </button>
              </li>
            ))}
          </ul>
        </Scroller>
      </div>
    </div>
  );
}
