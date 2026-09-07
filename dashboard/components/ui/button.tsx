"use client";

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md border font-medium whitespace-nowrap select-none transition-[background-color,border-color,color,opacity,transform] duration-[--hy-fast] ease-brand outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:translate-y-px disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-line-strong bg-surface text-fg hover:bg-hover",
        primary:
          "border-accent bg-accent text-accent-fg hover:brightness-110 dark:hover:brightness-95",
        subtle: "border-transparent bg-sunken text-fg hover:bg-hover",
        ghost: "border-transparent bg-transparent text-muted hover:bg-hover hover:text-fg",
        danger: "border-line-strong bg-surface text-fg hover:border-danger hover:text-danger-ink",
        destructive:
          "border-danger bg-danger text-white hover:brightness-110 dark:hover:brightness-95",
        link: "h-auto border-transparent bg-transparent p-0 text-accent-ink underline underline-offset-2 hover:brightness-110",
      },
      size: {
        xs: "h-7 min-h-7 px-2 text-2xs [&_svg]:size-3.5 max-wide:h-9 max-wide:min-h-9 max-wide:px-2.5 max-wide:text-xs",
        sm: "h-8 px-2.5 text-xs [&_svg]:size-3.5 max-wide:h-11 max-wide:px-3",
        md: "h-9 px-3.5 text-sm [&_svg]:size-4 max-wide:h-11",
        lg: "h-10 px-4 text-base [&_svg]:size-4 max-wide:h-12",
        icon: "size-9 p-0 [&_svg]:size-4 max-wide:size-11",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({
  className,
  variant,
  size,
  asChild = false,
  type,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...(asChild ? {} : { type: type ?? "button" })}
      {...props}
    />
  );
}

export { buttonVariants };
