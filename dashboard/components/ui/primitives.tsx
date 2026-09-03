"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- button */

type ButtonVariant = "primary" | "default" | "ghost" | "danger" | "quiet";
type ButtonSize = "sm" | "md";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  // amber is reserved for actions that spend something irreversible
  primary:
    "bg-surface-2 text-on-ink hover:bg-surface-2/90 border-line-strong font-medium disabled:bg-surface-3 disabled:text-ink-3 disabled:border-line",
  default: "bg-surface-3 text-ink hover:bg-surface-3 border-line-strong",
  ghost: "bg-transparent text-ink hover:bg-surface-2 hover:text-ink border-transparent",
  quiet: "bg-transparent text-ink-2 hover:text-ink border-line hover:border-line-strong",
  danger: "bg-transparent text-ink hover:bg-surface-2/20 border-line-strong",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

export function Button({
  className,
  variant = "default",
  size = "md",
  asChild = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  asChild?: boolean;
}) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      className={cn(
        "inline-flex items-center justify-center rounded-sm border transition-colors",
        "disabled:pointer-events-none disabled:opacity-60 whitespace-nowrap",
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------- surfaces */

export function Panel({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-sm border border-line bg-surface", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export function PanelHead({
  eyebrow,
  title,
  action,
  className,
}: {
  eyebrow?: string;
  title?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-b border-line px-4 py-3",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow mb-1">{eyebrow}</div> : null}
        {title ? <div className="text-sm font-medium text-ink">{title}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- badge */

export function Badge({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5",
        "font-mono text-[10px] uppercase tracking-wider",
        "border-line-strong bg-surface-2 text-ink-2",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------- inputs */

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-sm border border-line bg-surface px-3 text-sm text-ink",
        "placeholder:text-ink-3 focus:border-line-strong outline-none transition-colors",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-sm border border-line bg-surface p-3 text-sm leading-relaxed text-ink",
        "placeholder:text-ink-3 focus:border-line-strong outline-none transition-colors resize-y",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 rounded-sm border border-line bg-surface px-2.5 text-sm text-ink",
        "outline-none transition-colors focus:border-line-strong",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

/* ---------------------------------------------------------------- states */

export function Empty({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="text-sm text-ink">{title}</div>
      {hint ? <div className="max-w-md text-xs leading-relaxed text-ink-3">{hint}</div> : null}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 px-6 py-16">
      <span className="h-1.5 w-1.5 rounded-full bg-surface-2 pulse-soft" />
      <span className="eyebrow">{label}</span>
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <div className="text-sm text-ink">{message}</div>
      {onRetry ? (
        <Button size="sm" variant="quiet" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- table */

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "sticky top-0 z-10 border-b border-line bg-surface px-3 py-2 text-left",
        "font-mono text-[10px] font-normal uppercase tracking-wider text-ink-3",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-line px-3 py-2.5 align-middle", className)} {...props} />;
}

export function Tr({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("transition-colors hover:bg-surface", className)} {...props} />;
}
