"use client";

import { FieldHint, Segmented, Select } from "@/components/ui";
import { fromMinutes, hourLabel, toMinutes, windowSpan } from "@/lib/clock";
import { cn } from "@/lib/utils";

const DEFAULT_START = "22:00";
const DEFAULT_END = "06:00";

const HOURS = Array.from({ length: 24 }, (_, hour) => ({
  value: String(hour),
  label: hourLabel(hour),
}));

function minuteOptions(current: number) {
  const steps = Array.from({ length: 12 }, (_, index) => index * 5);
  if (!steps.includes(current)) steps.push(current);
  return steps
    .sort((a, b) => a - b)
    .map((minute) => ({ value: String(minute), label: `:${String(minute).padStart(2, "0")}` }));
}

function ClockPicker({
  label,
  word,
  value,
  onChange,
  disabled,
}: {
  label: string;
  word: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const total = toMinutes(value) ?? 0;
  const hour = Math.floor(total / 60);
  const minute = total % 60;

  return (
    <div
      role="group"
      aria-label={label}
      className="flex min-w-0 items-center gap-1.5 max-wide:w-full"
    >
      <span className="w-10 shrink-0 text-xs text-muted">{word}</span>
      <Select
        label={`${label} hour`}
        groups={[{ options: HOURS }]}
        value={String(hour)}
        onChange={(next) => onChange(fromMinutes(Number(next) * 60 + minute))}
        disabled={disabled}
        className="w-40 max-wide:w-auto max-wide:flex-1"
      />
      <Select
        label={`${label} minute`}
        groups={[{ options: minuteOptions(minute) }]}
        value={String(minute)}
        onChange={(next) => onChange(fromMinutes(hour * 60 + Number(next)))}
        disabled={disabled}
        className="w-20 max-wide:w-24 max-wide:flex-none"
      />
    </div>
  );
}

export default function TimeRange({
  starts_at,
  ends_at,
  onChange,
  timezone,
  disabled,
}: {
  starts_at: string;
  ends_at: string;
  onChange: (starts_at: string, ends_at: string) => void;
  timezone: string;
  disabled?: boolean;
}) {
  const allDay = toMinutes(starts_at) === null || toMinutes(ends_at) === null;

  return (
    <div className="flex flex-col gap-2">
      <Segmented
        label="Hours"
        items={[
          { value: "all", label: <span className="whitespace-nowrap">All day</span> },
          { value: "hours", label: <span className="whitespace-nowrap">Set hours</span> },
        ]}
        value={allDay ? "all" : "hours"}
        onChange={(next) =>
          next === "all" ? onChange("", "") : onChange(DEFAULT_START, DEFAULT_END)
        }
        className={cn("self-start", disabled && "pointer-events-none opacity-50")}
      />
      {!allDay && (
        <div
          className={cn(
            "flex flex-wrap items-center gap-x-4 gap-y-2",
            "max-wide:flex-col max-wide:items-stretch",
          )}
        >
          <ClockPicker
            label="Starts at"
            word="From"
            value={starts_at}
            onChange={(next) => onChange(next, ends_at)}
            disabled={disabled}
          />
          <ClockPicker
            label="Ends at"
            word="Until"
            value={ends_at}
            onChange={(next) => onChange(starts_at, next)}
            disabled={disabled}
          />
        </div>
      )}
      <FieldHint>
        {windowSpan(starts_at, ends_at)} · {timezone}
      </FieldHint>
    </div>
  );
}
