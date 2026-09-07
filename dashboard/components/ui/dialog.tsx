"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-overlay" />
      <DialogPrimitive.Content
        data-slot="dialog"
        className={cn(
          "fixed z-50 flex flex-col gap-3.5 border border-line bg-raised shadow-lg outline-none",
          "top-1/2 left-1/2 max-h-[85dvh] w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl p-5",
          "max-wide:top-auto max-wide:bottom-0 max-wide:left-0 max-wide:w-full max-wide:max-w-none",
          "max-wide:translate-x-0 max-wide:translate-y-0 max-wide:rounded-b-none max-wide:rounded-t-2xl",
          "max-wide:pb-[calc(1.25rem+env(safe-area-inset-bottom))]",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          aria-label="Close"
          className="absolute top-3.5 right-3.5 inline-flex size-8 items-center max-wide:top-2 max-wide:right-2 max-wide:size-11 justify-center rounded-md text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:bg-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <X className="size-4" aria-hidden="true" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1 pr-8", className)} {...props} />;
}

export function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("text-md font-semibold tracking-[-0.01em] text-fg", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn("text-sm leading-snug text-muted", className)}
      {...props}
    />
  );
}

export function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-end gap-2 max-wide:flex-col-reverse max-wide:items-stretch",
        className,
      )}
      {...props}
    />
  );
}
