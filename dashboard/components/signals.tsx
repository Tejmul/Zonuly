"use client";

import * as React from "react";
import { cn, scoreTone, sentimentTone, confidenceTone } from "@/lib/utils";
import { Badge } from "@/components/ui/primitives";

/**
 * The odds meter. Every shortlist probability in the app renders through this,
 * so scanning a table reads as a field of odds rather than a column of numbers.
 * Ten segments, because the useful judgement is "roughly how likely", not the
 * second decimal place.
 */
export function Odds({
  score,
  size = "md",
  showLabel = false,
}: {
  score: number | null | undefined;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
}) {
  const tone = scoreTone(score);
  const filled = score == null ? 0 : Math.round(score / 10);
  const heights = { sm: "h-2", md: "h-3", lg: "h-5" };
  const widths = { sm: "w-[3px]", md: "w-[3px]", lg: "w-[5px]" };
  const nums = { sm: "text-xs", md: "text-sm", lg: "text-2xl" };

  return (
    <div className="flex items-center gap-2">
      <span className={cn("tnum tabular-nums", nums[size], tone.text)}>
        {score == null ? "––" : score}
      </span>
      <div className="flex items-end gap-[2px]" aria-hidden>
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={cn(
              "rounded-[1px] transition-colors",
              widths[size],
              heights[size],
              i < filled ? tone.bar : "bg-surface-3",
            )}
          />
        ))}
      </div>
      {showLabel ? <span className="text-xs text-ink-3">{tone.label}</span> : null}
      <span className="sr-only">
        {score == null ? "Not scored yet" : `${score} out of 100 shortlist odds, ${tone.label}`}
      </span>
    </div>
  );
}

/**
 * The send budget. Gmail deliverability caps this at 25 a day and every send is
 * irreversible, so the constraint lives in the top bar rather than in settings:
 * discrete ticks that visibly deplete.
 */
export function BudgetMeter({
  cap,
  sent,
  inWindow,
  compact = false,
}: {
  cap: number;
  sent: number;
  inWindow: boolean;
  compact?: boolean;
}) {
  const remaining = Math.max(0, cap - sent);
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex items-center gap-[2px]" aria-hidden>
        {Array.from({ length: cap }, (_, i) => (
          <span
            key={i}
            className={cn(
              "h-3.5 w-[2px] rounded-[1px]",
              i < sent ? "bg-surface-3" : inWindow ? "bg-surface-2" : "bg-surface-2/40",
            )}
          />
        ))}
      </div>
      <div className="flex items-baseline gap-1">
        <span className="tnum text-sm text-ink">{remaining}</span>
        {!compact ? <span className="eyebrow">sends left today</span> : null}
      </div>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "signal" | "jade" | "clay";
}) {
  const tones = {
    default: "text-ink",
    signal: "text-ink",
    jade: "text-ink",
    clay: "text-ink",
  };
  return (
    <div className="border-l border-line px-4 py-3 first:border-l-0 first:pl-0">
      <div className="eyebrow mb-1.5">{label}</div>
      <div className={cn("tnum text-2xl leading-none", tones[tone])}>{value}</div>
      {hint ? <div className="mt-1.5 text-xs text-ink-3">{hint}</div> : null}
    </div>
  );
}

/**
 * The funnel. Each stage is a bar proportional to the widest stage, so the drop
 * from "drafted" to "positive reply" is legible as a shape.
 */
export function Funnel({ stages }: { stages: { label: string; value: number; tone?: string }[] }) {
  const max = Math.max(1, ...stages.map((s) => s.value));
  return (
    <div className="flex flex-col gap-2.5">
      {stages.map((stage) => (
        <div key={stage.label} className="flex items-center gap-3">
          <div className="w-24 shrink-0 text-right text-xs text-ink-2">{stage.label}</div>
          <div className="h-5 flex-1 overflow-hidden rounded-[2px] bg-surface-2">
            <div
              className={cn("h-full transition-all", stage.tone ?? "bg-surface-2")}
              style={{ width: `${Math.max(stage.value === 0 ? 0 : 2, (stage.value / max) * 100)}%` }}
            />
          </div>
          <div className="tnum w-8 shrink-0 text-sm text-ink">{stage.value}</div>
        </div>
      ))}
    </div>
  );
}

export function SentimentBadge({ sentiment }: { sentiment: string | null | undefined }) {
  const tone = sentimentTone(sentiment);
  return (
    <Badge className={cn(tone.text, tone.ring, tone.bg)}>{sentiment ?? "unread"}</Badge>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: string | null | undefined }) {
  const tone = confidenceTone(confidence);
  const label = confidence === "pattern-guessed" ? "guessed" : confidence ?? "unknown";
  return <Badge className={cn(tone.text, tone.ring, tone.bg)}>{label}</Badge>;
}

export function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      <span className={cn("h-1.5 w-1.5 rounded-full", ok ? "bg-surface-2" : "bg-surface-2")} />
      {label}
    </span>
  );
}
