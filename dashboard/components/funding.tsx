"use client";

/*  The money cells: funding, pay power, hiring — and the city switch.
 *
 *  Black and white, so a cell is read by weight and shape. The round is a chip, the
 *  amount is the heaviest thing in the cell, and the date sits underneath in grey —
 *  unless it is within twelve months, when it is printed in ink with the word
 *  "recent", because a recent round is the whole reason a company can pay.
 */

import { CITIES, fundingOf, inCity, money, roundWhen, type City, type FundingSource } from "@/lib/funding";

const DASH = <span className="text-ink-3">—</span>;

export function FundingCell({ row, compact = false }: { row: FundingSource; compact?: boolean }) {
  const f = fundingOf(row);
  const amount = money(f.amountUsdM);
  const val = money(f.valuationUsdM);
  const when = roundWhen(f.announced);
  if (!f.stage && !amount && !when && !val) return DASH;

  const title = [
    f.stage, amount && `raised ${amount}`, when && `announced ${when.label}${when.ago ? ` (${when.ago})` : ""}`,
    val && `valued at ${val}`, f.investors.length ? `investors: ${f.investors.join(", ")}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <span className="flex min-w-0 flex-col gap-0.5" title={title}>
      <span className="flex min-w-0 items-center gap-1.5">
        {f.stage ? <span className="chip shrink-0 !h-[17px] !px-1.5 text-ink">{f.stage}</span> : null}
        {amount ? <span className="tnum text-[12px] font-semibold text-ink">{amount}</span> : null}
      </span>
      {when || val ? (
        <span className="tnum flex min-w-0 items-center gap-1.5 truncate text-[10.5px] text-ink-3">
          {when ? (
            when.recent ? (
              <span className="font-medium text-ink">{compact ? when.label : `${when.label} · recent`}</span>
            ) : (
              <span>{compact ? when.label : `${when.label}${when.ago ? ` · ${when.ago}` : ""}`}</span>
            )
          ) : null}
          {val ? <span className="truncate">val. {val}</span> : null}
        </span>
      ) : null}
    </span>
  );
}

const BAND: Record<string, string> = {
  pays: "pays", deep_pockets: "deep pockets", funded: "funded", thin: "thin", unknown: "unknown",
};

/** Pay power as a five-step ladder: filled ticks for the score, the band in words. */
export function PayCell({
  score, band, why,
}: { score: number | null | undefined; band: string | null | undefined; why?: string | null }) {
  const s = typeof score === "number" && Number.isFinite(score) ? score : null;
  if (s === null && (!band || band === "unknown")) return DASH;
  const steps = s === null ? 0 : s >= 100 ? 5 : s >= 85 ? 4 : s >= 65 ? 3 : s >= 30 ? 2 : 1;
  return (
    <span className="inline-flex items-center justify-end gap-1.5" title={why ?? undefined}>
      <span className="flex items-end gap-[2px]" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={`w-[3px] rounded-[1px] ${i < steps ? "bg-data-1" : "bg-data-5"}`} style={{ height: 5 + i * 2 }} />
        ))}
      </span>
      <span className={`text-[11.5px] ${steps >= 3 ? "font-medium text-ink" : "text-ink-3"}`}>
        {BAND[band ?? ""] ?? band ?? "—"}
      </span>
    </span>
  );
}

const HIRING: Record<string, string> = {
  verified: "proven", role_missing: "not our role", not_authorized: "unproven", unreachable: "unreadable",
  // guardrails.hiring: a careers page with no role title read off it, or a proof gone stale
  careers_only: "page, no roles", needs_recheck: "re-checking",
};

/** Hiring: proven or not, and how many roles their own page lists. */
export function HiringCell({
  status, roles,
}: { status: string | null | undefined; roles?: number | string[] | string | null }) {
  let n: number | null = null;
  if (typeof roles === "number") n = roles;
  else if (Array.isArray(roles)) n = roles.length;
  else if (typeof roles === "string" && roles.trim().startsWith("[")) {
    try { n = (JSON.parse(roles) as unknown[]).length; } catch { n = null; }
  }
  const word = HIRING[status ?? ""];
  if (!word && !n) return DASH;
  const proven = status === "verified";
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-[11.5px]">
      {word ? (
        <span className={proven ? "chip chip-solid !h-[17px] !px-1.5" : status === "not_authorized" ? "text-ink-3 line-through" : "text-ink-3"}>
          {word}
        </span>
      ) : null}
      {n ? <span className="tnum text-ink-2">{n} role{n === 1 ? "" : "s"}</span> : null}
    </span>
  );
}

/** All / Bengaluru / Hyderabad — the only two Indian cities that matter here. */
export function CityFilter({
  value, onChange, counts,
}: { value: City | "all"; onChange: (c: City | "all") => void; counts: Record<City | "all", number> }) {
  return (
    <div className="flex rounded-md border border-line p-px" role="group" aria-label="City">
      {(["all", ...CITIES] as const).map((c) => (
        <button
          key={c}
          onClick={() => onChange(c)}
          aria-pressed={value === c}
          className={`cursor-pointer rounded-[5px] px-3 py-1.5 text-[12px] transition-colors ${
            value === c ? "bg-ink font-medium text-on-ink" : "text-ink-2 hover:text-ink"
          }`}
        >
          {c === "all" ? "All" : c}
          <span className={`tnum ml-1.5 text-[11px] ${value === c ? "opacity-70" : "text-ink-3"}`}>{counts[c]}</span>
        </button>
      ))}
    </div>
  );
}

export function cityCounts<T>(rows: T[], city: (r: T) => string | null | undefined): Record<City | "all", number> {
  const out = { all: rows.length } as Record<City | "all", number>;
  for (const c of CITIES) out[c] = rows.filter((r) => inCity(city(r), c)).length;
  return out;
}
