"use client";

import { Search } from "lucide-react";

import { cn } from "@/lib/utils";

const fieldBase =
  "w-full min-w-0 rounded-md border border-line-strong bg-surface px-3 text-base text-fg transition-colors duration-[--hy-fast] ease-brand outline-none placeholder:text-subtle focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-default disabled:opacity-50";

export function Input({ className, type = "text", ...props }: React.ComponentProps<"input">) {
  return (
    <input
      data-slot="input"
      type={type}
      className={cn(fieldBase, "h-9 max-wide:h-11", className)}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        fieldBase,
        "hy-scrollbarless resize-y bg-sunken py-2.5 font-mono text-xs leading-[1.65] [tab-size:2]",
        className,
      )}
      {...props}
    />
  );
}

export function SearchInput({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <div className="relative flex min-w-0 flex-1 items-center">
      <Search
        className="pointer-events-none absolute left-2.5 size-3.5 text-subtle"
        aria-hidden="true"
      />
      <input
        data-slot="search"
        type="search"
        className={cn(
          fieldBase,
          "h-9 pl-8 text-sm max-wide:h-11 [&::-webkit-search-cancel-button]:hidden",
          className,
        )}
        {...props}
      />
    </div>
  );
}

export function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn("text-xs font-medium text-muted", className)}
      {...props}
    />
  );
}

export function FieldHint({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("m-0 text-xs text-muted", className)} {...props} />;
}
