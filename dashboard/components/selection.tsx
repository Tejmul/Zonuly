"use client";

import { ExternalLink } from "lucide-react";
import type { NeedItem, NeedsBrief, SelectionBand, SelectionWhy } from "@/lib/api";
import { cn } from "@/lib/utils";

/* Selection confidence (jobhunter/confidence.py) and the needs brief (jobhunter/needs.py).
   Black and white only: the band is carried by weight and fill, never by colour. */

export const BAND_LABEL: Record<SelectionBand, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  long_shot: "Long shot",
};

const BAND_STYLE: Record<SelectionBand, string> = {
  high: "bg-ink text-on-ink border-ink",
  medium: "border-ink text-ink",
  low: "border-line-strong text-ink-2",
  long_shot: "border-line text-ink-3 border-dashed",
};

export function BandChip({ band, className }: { band: SelectionBand | null | undefined; className?: string }) {
  if (!band) return <span className="text-xs text-ink-3">—</span>;
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-sm border px-1.5 text-[11px] font-medium whitespace-nowrap",
        BAND_STYLE[band],
        className,
      )}
    >
      {BAND_LABEL[band]}
    </span>
  );
}

export function SelectionScore({
  score,
  band,
  size = "md",
}: {
  score: number | null | undefined;
  band: SelectionBand | null | undefined;
  size?: "md" | "lg";
}) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn("tnum font-semibold text-ink", size === "lg" ? "text-3xl" : "text-base")}>
        {score ?? "––"}
      </span>
      <BandChip band={band} />
    </div>
  );
}

function Source({ url, label = "source" }: { url: string | null | undefined; label?: string }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-0.5 text-[11px] text-ink-3 underline decoration-line underline-offset-2 hover:text-ink"
    >
      {label} <ExternalLink size={10} />
    </a>
  );
}

/** Every term: its weight, what it scored, and the sentence (and link) it was read from. */
export function SelectionBreakdown({ sel }: { sel: SelectionWhy }) {
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end gap-x-6 gap-y-2">
        <SelectionScore score={sel.score} band={sel.band} size="lg" />
        <div className="text-xs text-ink-3">
          {sel.market === "india" ? "India profile" : sel.market === "remote" ? "Remote profile" : "On-site abroad (remote profile, capped)"}
          {sel.cap != null ? ` · raw ${sel.raw}, capped at ${sel.cap}` : ` · raw ${sel.raw}`}
        </div>
      </div>

      {sel.blockers.length ? (
        <div className="mb-4 rounded-sm border border-ink px-3 py-2.5">
          <div className="eyebrow mb-1.5">Blockers — each caps the score</div>
          <ul className="flex flex-col gap-1.5">
            {sel.blockers.map((b, i) => (
              <li key={i} className="text-[13px] text-ink">
                <span className="tnum mr-2 font-medium">≤ {b.cap}</span>
                {b.why}
                {b.quote ? <span className="mt-0.5 block text-xs text-ink-3">“{b.quote}”</span> : null}
                <Source url={b.url} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ul className="flex flex-col divide-y divide-line">
        {sel.terms.map((t) => (
          <li key={t.key} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-start gap-3 py-2">
            <span className="text-xs text-ink-2">{t.label}</span>
            <div className="min-w-0">
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
                <div className="h-full bg-ink" style={{ width: `${Math.round(t.value * 100)}%` }} />
              </div>
              <p className="mt-1 text-xs leading-relaxed text-ink-2">{t.why}</p>
              {t.quote ? <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-3">“{t.quote}”</p> : null}
              <Source url={t.url} />
            </div>
            <span className="tnum text-right text-xs text-ink">
              {t.points}
              <span className="text-ink-3">/{t.weight}</span>
            </span>
          </li>
        ))}
      </ul>
      {sel.notes.length ? <p className="mt-3 text-[11px] text-ink-3">{sel.notes.join(" · ")}</p> : null}
    </div>
  );
}

const STATUS_MARK: Record<NeedItem["status"], { mark: string; label: string; cls: string }> = {
  covered: { mark: "✓", label: "Covered", cls: "text-ink font-medium" },
  partial: { mark: "~", label: "Partly", cls: "text-ink-2" },
  uncovered: { mark: "✗", label: "Uncovered", cls: "text-ink-3 line-through decoration-ink-3/60" },
};

function NeedRow({ n }: { n: NeedItem }) {
  const s = STATUS_MARK[n.status];
  return (
    <li className="grid gap-2 py-3 md:grid-cols-2 md:gap-4">
      <div className="min-w-0">
        <div className="flex items-start gap-2">
          <span className={cn("tnum w-4 shrink-0 text-sm", s.cls)} aria-label={s.label}>
            {s.mark}
          </span>
          <div className="min-w-0">
            <p className="text-[13px] text-ink">
              {n.text}
              {n.kind === "implied" ? <span className="chip ml-2 align-middle">hidden</span> : null}
              {n.section === "nice" ? <span className="chip ml-2 align-middle">nice-to-have</span> : null}
            </p>
            {n.quote && n.quote !== n.text ? (
              <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-3">“{n.quote}”</p>
            ) : null}
            <Source url={n.url} label={n.source === "jd" ? "posting" : n.source} />
          </div>
        </div>
      </div>
      <div className="min-w-0 pl-6 md:pl-0">
        {n.evidence ? (
          <>
            <p className="text-[12px] leading-relaxed text-ink-2">“{n.evidence}”</p>
            <p className="mt-0.5 text-[11px] text-ink-3">
              {n.evidence_where}
              {n.status === "partial" && n.missing_skills.length ? ` · missing ${n.missing_skills.join(", ")}` : ""}
              {n.status === "partial" && !n.missing_skills.length ? " · only listed, not shown in shipped work" : ""}
            </p>
            {n.more_evidence?.map((m) => (
              <p key={m.id} className="mt-1 text-[11px] text-ink-3">
                + {m.skill}: “{m.text.slice(0, 120)}{m.text.length > 120 ? "…" : ""}” ({m.where})
              </p>
            ))}
          </>
        ) : (
          <p className="text-[12px] text-ink-3">
            Not on the resume{n.missing_skills.length || n.skills.length ? ` (${(n.missing_skills.length ? n.missing_skills : n.skills).join(", ")})` : ""} — never claim it.
          </p>
        )}
      </div>
    </li>
  );
}

/** Needs → covered by → resume evidence. Uncovered needs are shown, struck, and listed as never-claim. */
export function NeedsBriefView({ brief }: { brief: NeedsBrief }) {
  const needs = [...brief.implied, ...brief.stated];
  const cov = brief.coverage;
  const helps = brief.helps_model ?? brief.helps;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-xs text-ink-3">
        <span className="tnum text-sm text-ink">
          {cov.share == null ? "—" : `${Math.round(cov.share * 100)}%`} covered
        </span>
        <span className="tnum">
          {cov.covered} covered · {cov.partial} partly · {cov.uncovered} uncovered
        </span>
        <span>
          {brief.stored === false
            ? "read from the posting just now (no stored brief yet)"
            : brief.synthesis === "done"
              ? "synthesised, every sentence checked against the sources"
              : brief.synthesis === "pending"
                ? "deterministic — model synthesis pending (daily ceiling)"
                : "deterministic"}
        </span>
      </div>

      {helps ? (
        <div className="rounded-sm border border-line-strong px-3 py-2.5">
          <div className="eyebrow mb-1">How hiring you helps them — from covered needs only</div>
          <p className="text-[13px] leading-relaxed text-ink">{helps}</p>
        </div>
      ) : null}

      {brief.angles.length ? (
        <div>
          <div className="eyebrow mb-1.5">Outreach angles</div>
          <ul className="flex flex-col gap-1">
            {brief.angles.map((a, i) => (
              <li key={i} className="text-[13px] text-ink-2">
                · {a}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {brief.building.length || brief.model?.building ? (
        <div>
          <div className="eyebrow mb-1.5">What they are building</div>
          {brief.model?.building ? <p className="mb-2 text-[13px] text-ink">{brief.model.building}</p> : null}
          <ul className="flex flex-col gap-1.5">
            {brief.building.slice(0, 6).map((b, i) => (
              <li key={i} className="text-[12px] leading-relaxed text-ink-2">
                {b.text} <Source url={b.url} label={b.source} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <div className="mb-1 grid gap-4 md:grid-cols-2">
          <div className="eyebrow">What they need</div>
          <div className="eyebrow hidden md:block">Covered by — the resume line</div>
        </div>
        {needs.length ? (
          <ul className="divide-y divide-line">
            {needs.map((n, i) => (
              <NeedRow key={i} n={n} />
            ))}
          </ul>
        ) : (
          <p className="py-3 text-[13px] text-ink-3">
            The posting names no stack or hidden signal this reader recognises. Read it below.
          </p>
        )}
      </div>

      {brief.forbidden.length ? (
        <p className="text-xs text-ink-3">
          Never claim: <span className="text-ink-2">{brief.forbidden.join(", ")}</span> — the drafter is told so, and the
          review gate flags a draft that does.
        </p>
      ) : null}
      {brief.experience.min_years != null || brief.experience.fresher ? (
        <p className="text-xs text-ink-3">
          Experience bar:{" "}
          {brief.experience.fresher ? "open to freshers" : `${brief.experience.min_years}+ years`}
          {brief.experience.quote ? ` — “${brief.experience.quote}”` : ""}
        </p>
      ) : null}
    </div>
  );
}
