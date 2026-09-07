import { cn } from "@/lib/utils";

export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <table
      data-slot="table"
      className={cn("w-full border-collapse text-sm max-wide:text-xs", className)}
      {...props}
    />
  );
}

export function THead({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-head" className={cn(className)} {...props} />;
}

export function TBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child>td]:border-0", className)}
      {...props}
    />
  );
}

export function TR({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "transition-colors duration-[--hy-fast] ease-brand hover:[@media(hover:hover)]:bg-hover",
        className,
      )}
      {...props}
    />
  );
}

export function TH({
  className,
  numeric = false,
  ...props
}: React.ComponentProps<"th"> & { numeric?: boolean }) {
  return (
    <th
      scope="col"
      className={cn(
        "border-b border-line pb-2 pr-3 text-left text-2xs font-medium tracking-[0.06em] whitespace-nowrap text-muted uppercase max-wide:text-xs",
        numeric && "pl-4 text-right",
        className,
      )}
      {...props}
    />
  );
}

export function TD({
  className,
  numeric = false,
  ...props
}: React.ComponentProps<"td"> & { numeric?: boolean }) {
  return (
    <td
      className={cn(
        "border-b border-line-subtle py-2 pr-3 align-middle max-wide:pr-2",
        numeric && "pl-4 text-right tabular-nums",
        className,
      )}
      {...props}
    />
  );
}
