/*  The ZoNuLy mark — "Threshold".
 *
 *  A solid tile — the square the hero's crowd is made of — with the referral's route
 *  cut straight through it as a Z, and the route ends in a person: the ring is where
 *  it lands. Negative space, so it is one shape at every size and survives a 16px tab;
 *  currentColor, so the same component is black on white and white on black.
 *
 *  The other four candidates (reticle · seven · door · bar) are kept here so switching
 *  is one prop; the side-by-side sheet is /brand/logo-candidates.html.
 */

import Link from "next/link";
import { useId } from "react";
import { cn } from "@/lib/utils";

export type MarkVariant = "threshold" | "reticle" | "seven" | "door" | "bar";
export const DEFAULT_MARK: MarkVariant = "threshold";

/** The glyph alone. `size` is the rendered box in px; every drawing sits on a 32-unit grid. */
export function Mark({
  size = 20,
  variant = DEFAULT_MARK,
  className,
  title = "ZoNuLy",
}: {
  size?: number;
  variant?: MarkVariant;
  className?: string;
  title?: string;
}) {
  const id = useId();
  const common = { width: size, height: size, viewBox: "0 0 32 32", role: "img" as const, "aria-label": title,
                   className: cn("logo-mark shrink-0", className) };

  if (variant === "threshold") {
    const mask = `zm${id.replace(/[^a-zA-Z0-9]/g, "")}`;
    return (
      <svg {...common} fill="none">
        <mask id={mask}>
          <rect width="32" height="32" fill="#fff" />
          {/* the route, cut out of the tile */}
          <path d="M8.5 9.5H22.5L9.5 22.5H19" stroke="#000" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
          {/* where it lands: a person */}
          <circle cx="23.2" cy="22.5" r="4" fill="#000" />
          <circle cx="23.2" cy="22.5" r="1.7" fill="#fff" />
        </mask>
        <rect width="32" height="32" rx="9" fill="currentColor" mask={`url(#${mask})`} />
      </svg>
    );
  }
  if (variant === "reticle") {
    return (
      <svg {...common} fill="none">
        <path d="M2.2 9V4.2a2 2 0 0 1 2-2H9M23 2.2h4.8a2 2 0 0 1 2 2V9M29.8 23v4.8a2 2 0 0 1-2 2H23M9 29.8H4.2a2 2 0 0 1-2-2V23" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
        <rect x="9.2" y="9.2" width="6.2" height="6.2" rx="1.3" stroke="currentColor" strokeWidth="2.2" />
        <rect x="16.6" y="9.2" width="6.2" height="6.2" rx="1.3" stroke="currentColor" strokeWidth="2.2" />
        <rect x="9.2" y="16.6" width="6.2" height="6.2" rx="1.3" stroke="currentColor" strokeWidth="2.2" />
        <rect x="16.6" y="16.6" width="6.2" height="6.2" rx="1.3" fill="currentColor" />
      </svg>
    );
  }
  if (variant === "seven") {
    return (
      <svg {...common} fill="currentColor">
        <circle cx="6" cy="6" r="2.8" /><circle cx="16" cy="6" r="2.8" /><circle cx="26" cy="6" r="2.8" />
        <circle cx="16" cy="16" r="2.8" />
        <circle cx="6" cy="26" r="2.8" /><circle cx="16" cy="26" r="2.8" />
        <circle cx="26" cy="26" r="4.4" fill="none" stroke="currentColor" strokeWidth="2.4" /><circle cx="26" cy="26" r="1.6" />
      </svg>
    );
  }
  if (variant === "door") {
    return (
      <svg {...common} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 7H25L8 24H15.5" />
        <path d="M13.5 20.2L17.8 24L13.5 27.8" />
        <path d="M21 16.5H28.5V29H21" />
      </svg>
    );
  }
  return (
    <svg {...common} fill="none">
      <path d="M4.5 7.5H27L6 24.5H17.5" stroke="currentColor" strokeWidth="6" strokeLinejoin="miter" />
      <rect x="21" y="19" width="7" height="7" rx="1.6" fill="currentColor" />
    </svg>
  );
}

/** Mark + wordmark. The wordmark is set in the display face — the same voice as the
 *  headline — rather than the chrome's mono, so the lockup reads as a name, not a label. */
export function Logo({
  href = "/",
  size = 20,
  variant = DEFAULT_MARK,
  className,
  wordClassName,
}: {
  href?: string | null;
  size?: number;
  variant?: MarkVariant;
  className?: string;
  wordClassName?: string;
}) {
  const body = (
    <>
      <Mark size={size} variant={variant} />
      <span className={cn("font-[family-name:var(--font-display)] font-semibold tracking-[-0.03em]", wordClassName)}>
        ZoNuLy
      </span>
    </>
  );
  const cls = cn("group inline-flex items-center gap-2", className);
  return href ? (
    <Link href={href} className={cls} aria-label="ZoNuLy home">
      {body}
    </Link>
  ) : (
    <span className={cls}>{body}</span>
  );
}
