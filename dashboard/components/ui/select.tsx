"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
  note?: string;
}

export interface SelectGroup {
  label?: string;
  options: SelectOption[];
}

const EMPTY = "__hydra_empty__";

const encode = (value: string) => (value === "" ? EMPTY : value);
const decode = (value: string) => (value === EMPTY ? "" : value);

export function Select({
  groups,
  value,
  onChange,
  label,
  className,
  disabled,
}: {
  groups: SelectGroup[];
  value: string;
  onChange: (value: string) => void;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  const options = groups.flatMap((group) => group.options);
  const chosen = options.find((option) => option.value === value) ?? options[0];

  return (
    <SelectPrimitive.Root
      value={encode(chosen?.value ?? "")}
      onValueChange={(next) => onChange(decode(next))}
      disabled={disabled}
    >
      <SelectPrimitive.Trigger
        aria-label={label}
        className={cn(
          "flex h-9 min-w-0 items-center gap-2 rounded-md border border-line-strong bg-surface px-3 text-sm text-fg transition-colors duration-[--hy-fast] ease-brand outline-none data-[state=open]:border-accent focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50 max-wide:h-11 max-wide:w-full",
          className,
        )}
      >
        <SelectPrimitive.Value className="min-w-0 flex-1 truncate text-left">
          {chosen?.label ?? ""}
        </SelectPrimitive.Value>
        <SelectPrimitive.Icon asChild>
          <ChevronDown
            className="size-4 shrink-0 text-muted transition-transform duration-[--hy-base] ease-brand"
            aria-hidden="true"
          />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>

      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={5}
          collisionPadding={13}
          aria-label={label}
          className="hy-rise z-50 max-h-(--radix-select-content-available-height) min-w-(--radix-select-trigger-width) overflow-hidden rounded-md border border-line-strong bg-raised p-1 shadow-lg"
        >
          <SelectPrimitive.Viewport className="hy-scrollbarless max-h-[min(320px,var(--radix-select-content-available-height))] overflow-y-auto p-0">
            {groups.map((group, index) => (
              <SelectPrimitive.Group key={group.label ?? `group-${index}`}>
                {group.label && (
                  <SelectPrimitive.Label className="px-2.5 pt-2 pb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase max-wide:text-xs">
                    {group.label}
                  </SelectPrimitive.Label>
                )}
                {group.options.map((option) => (
                  <SelectPrimitive.Item
                    key={option.value}
                    value={encode(option.value)}
                    className="relative flex cursor-pointer items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm text-fg outline-none select-none data-[highlighted]:bg-hover data-[state=checked]:font-medium data-[state=checked]:text-accent-ink max-wide:py-2.5"
                  >
                    <SelectPrimitive.ItemText>
                      <span className="min-w-0 truncate">{option.label}</span>
                    </SelectPrimitive.ItemText>
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {option.note && (
                        <span className="text-xs tabular-nums text-muted">{option.note}</span>
                      )}
                      <SelectPrimitive.ItemIndicator>
                        <Check className="size-3.5" aria-hidden="true" />
                      </SelectPrimitive.ItemIndicator>
                    </span>
                  </SelectPrimitive.Item>
                ))}
              </SelectPrimitive.Group>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
