import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Shortlist odds share one cool-to-warm ramp everywhere they appear. */
export function scoreTone(score: number | null | undefined) {
  if (score == null) return { text: "text-fog-400", bar: "bg-ink-500", label: "unscored" };
  if (score >= 75) return { text: "text-signal", bar: "bg-signal", label: "strong" };
  if (score >= 60) return { text: "text-jade", bar: "bg-jade", label: "worth a shot" };
  if (score >= 40) return { text: "text-fog-200", bar: "bg-steel", label: "long shot" };
  return { text: "text-fog-400", bar: "bg-ink-500", label: "unlikely" };
}

export function sentimentTone(sentiment: string | null | undefined) {
  switch (sentiment) {
    case "positive":
      return { text: "text-jade", ring: "border-jade-deep", bg: "bg-jade-deep/15" };
    case "negative":
      return { text: "text-clay", ring: "border-clay-deep", bg: "bg-clay-deep/15" };
    case "closed":
      return { text: "text-fog-400", ring: "border-ink-500", bg: "bg-ink-700" };
    default:
      return { text: "text-fog-200", ring: "border-ink-500", bg: "bg-ink-700" };
  }
}

export function confidenceTone(confidence: string | null | undefined) {
  switch (confidence) {
    case "verified":
      return { text: "text-jade", ring: "border-jade-deep", bg: "bg-jade-deep/15" };
    case "pattern-guessed":
      return { text: "text-signal", ring: "border-signal-deep", bg: "bg-signal-deep/12" };
    default:
      return { text: "text-fog-400", ring: "border-ink-500", bg: "bg-ink-700" };
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
