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
    "bg-ink text-on-ink hover:opacity-90 border-ink font-medium disabled:bg-surface-3 disabled:text-ink-3 disabled:border-line",
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

/** Loading is drawn as the shape of what is coming — a few grey rows — rather than a
 *  word on an empty page, so the layout does not jump when the data lands. */
export function Loading({ label = "Loading", rows = 5 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="px-4 py-5">
      <span className="sr-only">{label}</span>
      <Skeleton rows={rows} />
    </div>
  );
}

export function Skeleton({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div aria-hidden className={cn("flex flex-col gap-3", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="h-3 w-8 shrink-0 animate-pulse rounded-sm bg-surface-3" />
          <span
            className="h-3 animate-pulse rounded-sm bg-surface-3"
            style={{ width: `${[62, 48, 71, 55, 40, 66, 52][i % 7]}%` }}
          />
          <span className="ml-auto h-3 w-14 shrink-0 animate-pulse rounded-sm bg-surface-3" />
        </div>
      ))}
    </div>
  );
}

/** A failed request, said calmly. The headline is the same whatever went wrong — the
 *  page is not broken, the data is just not here yet — and the specific reason sits
 *  underneath in small type for whoever has to fix it. */
export function ErrorNote({
  message,
  onRetry,
  title = "This can\u2019t be loaded right now",
}: {
  message: string;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div role="status" className="flex flex-col items-center gap-2 px-6 py-14 text-center">
      <div className="text-[13.5px] font-medium text-ink">{title}</div>
      <div className="max-w-[56ch] text-[12px] leading-relaxed text-ink-3">{message}</div>
      {onRetry ? (
        <Button size="sm" variant="quiet" className="mt-2" onClick={onRetry}>
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
