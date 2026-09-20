"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";
import { Button, Input } from "./ui";

const TAG = /^[a-z0-9][a-z0-9 _-]{0,23}$/;

function tidy(tag: string): string {
  return tag.trim().toLowerCase().replace(/\s+/g, " ").slice(0, 24);
}

function names(values: string[]): string {
  if (values.length < 2) return values.join("");
  return `${values.slice(0, -1).join(", ")} or ${values[values.length - 1]}`;
}

export function reach(known: string[], chosen: string[], noun: string): string {
  if (chosen.length === 0) return "Every device, whatever it is tagged.";

  const missing = chosen.filter((tag) => !known.includes(tag));
  if (missing.length === chosen.length) {
    return `No device carries ${names(missing)}, so this ${noun} reaches nothing yet.`;
  }
  if (missing.length > 0) {
    return `Every device tagged ${names(chosen)}. No device carries ${names(missing)} yet.`;
  }
  return `Every device tagged ${names(chosen)}.`;
}

export function unreached(known: string[], chosen: string[]): boolean {
  return chosen.length > 0 && !chosen.some((tag) => known.includes(tag));
}

export function TagPicker({
  known,
  chosen,
  onChange,
  disabled,
}: {
  known: string[];
  chosen: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const [typed, setTyped] = useState("");

  const options = [...new Set([...known, ...chosen])].sort((a, b) => a.localeCompare(b));
  const candidate = tidy(typed);
  const addable = TAG.test(candidate) && !options.includes(candidate);

  const add = () => {
    if (!addable) return;
    onChange([...chosen, candidate]);
    setTyped("");
  };

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {options.length > 0 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tags">
          {options.map((tag) => {
            const on = chosen.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                disabled={disabled}
                aria-pressed={on}
                onClick={() =>
                  onChange(on ? chosen.filter((entry) => entry !== tag) : [...chosen, tag])
                }
                className={cn(
                  "inline-flex min-h-11 max-w-full items-center rounded-full border px-3.5 text-xs font-medium",
                  "transition-colors duration-[--hy-fast] ease-brand outline-none",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  "disabled:pointer-events-none disabled:opacity-50",
                  on
                    ? "border-accent-line bg-accent-soft text-accent-ink"
                    : "border-line bg-surface text-muted",
                )}
              >
                <span className="min-w-0 truncate">{tag}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="flex items-center gap-2">
        <Input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            add();
          }}
          placeholder={options.length > 0 ? "Another tag" : "kids"}
          aria-label="Add a tag"
          maxLength={24}
          className="max-w-64"
          disabled={disabled}
        />
        <Button size="sm" disabled={disabled || !addable} onClick={add}>
          Add
        </Button>
      </div>
    </div>
  );
}
