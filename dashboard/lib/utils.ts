import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Shortlist odds share one cool-to-warm ramp everywhere they appear. */
/* Tone helpers.
 *
 * These returned classes from a colour system this app no longer has — bg-signal,
 * bg-jade, text-fog-400, bg-steel. None of those tokens exist in globals.css any
 * more, so Tailwind emitted no rule for them and every score meter, badge and bar
 * they touched rendered with no fill at all. That is most of why the dashboard was
 * unreadable. They now return tokens that exist.
 *
 * With no hue to spend, state is carried the way the rest of this system carries
 * it: by weight and fill depth, from the same ink ramp the charts use.
 */

export function scoreTone(score: number | null | undefined) {
  if (score == null) return { text: "text-ink-3", bar: "bg-data-5", label: "unscored" };
  if (score >= 75) return { text: "text-ink", bar: "bg-data-1", label: "strong" };
  if (score >= 60) return { text: "text-ink", bar: "bg-data-2", label: "worth a shot" };
  if (score >= 40) return { text: "text-ink-2", bar: "bg-data-3", label: "long shot" };
  return { text: "text-ink-3", bar: "bg-data-4", label: "unlikely" };
}

export function sentimentTone(sentiment: string | null | undefined) {
  switch (sentiment) {
    case "positive":
      // The only filled badge in the app. A reply that says yes is the one event
      // worth interrupting a scan for, so it is the one thing drawn in solid ink.
      return { text: "text-on-ink", ring: "border-ink", bg: "bg-ink" };
    case "negative":
      return { text: "text-ink-2", ring: "border-line-strong", bg: "bg-surface-2" };
    case "closed":
      return { text: "text-ink-3", ring: "border-line", bg: "bg-surface-2" };
    default:
      return { text: "text-ink-2", ring: "border-line", bg: "bg-surface" };
  }
}

export function confidenceTone(confidence: string | null | undefined) {
  switch (confidence) {
    case "verified":
      return { text: "text-ink", ring: "border-line-strong", bg: "bg-surface-2" };
    case "pattern-guessed":
      return { text: "text-ink-2", ring: "border-line", bg: "bg-surface" };
    default:
      return { text: "text-ink-3", ring: "border-line", bg: "bg-surface" };
  }
}

export function lpa(min?: number | null, max?: number | null) {
  if (min == null && max == null) return null;
  if (min != null && max != null && Math.abs(min - max) > 0.5) {
    return `${Math.round(min)}–${Math.round(max)}`;
  }
  return `${Math.round((max ?? min)!)}`;
}

export function relTime(iso?: string | null) {
  if (!iso) return "—";
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (Number.isNaN(mins)) return "—";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 31) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}
