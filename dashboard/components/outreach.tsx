"use client";

/*  Pieces of the send pipeline shared by Outreach and Replies. */

import type { OutreachPipeline } from "@/lib/api";

/** A count that may be missing, negative or not a number — always a whole number ≥ 0. */
export const count = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.round(v) : 0);

export const SENTIMENTS = [
  { key: "positive", label: "Positive", hint: "said yes, or asked for more", fill: "var(--data-1)" },
  { key: "neutral", label: "Neutral", hint: "anything ambiguous — yours to judge", fill: "var(--data-3)" },
  { key: "negative", label: "Negative", hint: "said no", fill: "var(--data-5)" },
  { key: "closed", label: "Closed", hint: "role filled, or bounced back", fill: "var(--data-4)" },
] as const;
export type SentimentKey = (typeof SENTIMENTS)[number]["key"];

/** How the replies split: one bar in four greys, and the same four as counted words —
 *  the bar is the shape, the words are the reading. */
export function SentimentSplit({ sentiment }: { sentiment: OutreachPipeline["sentiment"] | undefined }) {
  const total = SENTIMENTS.reduce((n, s) => n + count(sentiment?.[s.key]), 0);
  return (
    <div>
      <div className="bar-track flex h-2.5 overflow-hidden !rounded-[3px]" aria-hidden>
        {total > 0 &&
          SENTIMENTS.map((s) => {
            const n = count(sentiment?.[s.key]);
            return n ? <span key={s.key} className="block h-full" style={{ width: `${(n / total) * 100}%`, background: s.fill }} /> : null;
          })}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        {SENTIMENTS.map((s) => (
          <div key={s.key}>
            <dt className="flex items-center gap-1.5">
              <span className="h-2 w-2 shrink-0 rounded-[2px] border border-line-strong" style={{ background: s.fill }} />
              <span className="text-[12px] font-medium">{s.label}</span>
            </dt>
            <dd className="tnum mt-1 text-[22px] leading-none font-medium tracking-[-0.02em]">{count(sentiment?.[s.key])}</dd>
            <dd className="mt-1 text-[11px] leading-snug text-ink-3">{s.hint}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
