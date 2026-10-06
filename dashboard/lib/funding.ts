/*  Money, rounds and cities, formatted once.
 *
 *  The API is in the middle of a change: older rows carry funding as a nested
 *  `funding: {stage, amount_usd_m, announced}`, newer ones as flat columns, and the
 *  backend is moving `funding_stage` to a fixed vocabulary (Pre-seed … Series D+,
 *  Public, Bootstrapped). Everything here reads either shape and degrades to null —
 *  the cell then shows an em dash, never "null", "undefined" or "NaN".
 */

export type FundingSource = {
  funding_stage?: string | null;
  funding_round_label?: string | null;
  funding_amount_usd_m?: number | string | null;
  funding_announced?: string | null;
  funding_investors?: string[] | string | null;
  valuation_usd_m?: number | string | null;
  funding?: {
    stage?: string | null;
    amount_usd_m?: number | string | null;
    announced?: string | null;
    investors?: string[] | null;
  } | null;
};

export type Funding = {
  stage: string | null;
  amountUsdM: number | null;
  announced: string | null;
  valuationUsdM: number | null;
  investors: string[];
};

function num(v: unknown): number | null {
  const n = typeof v === "string" ? Number(v) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

const STAGE_WORDS: Record<string, string> = {
  preseed: "Pre-seed", "pre-seed": "Pre-seed", pre_seed: "Pre-seed", seed: "Seed",
  series_a: "Series A", series_b: "Series B", series_c: "Series C", series_d: "Series D+",
  "series_d+": "Series D+", series_e: "Series D+", series_f: "Series D+",
  public: "Public", ipo: "Public", bootstrapped: "Bootstrapped",
};

/** The round as a short label. The new vocabulary passes through untouched; older
 *  free-text values ("series_a", "seed") are tidied rather than shown raw. */
export function stageLabel(stage: string | null | undefined): string | null {
  if (!stage || typeof stage !== "string") return null;
  const t = stage.trim();
  if (!t || /^(null|none|unknown|n\/a)$/i.test(t)) return null;
  const k = t.toLowerCase().replace(/\s+/g, "_");
  if (STAGE_WORDS[k]) return STAGE_WORDS[k];
  return t.replace(/_/g, " ").replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

export function fundingOf(r: FundingSource | null | undefined): Funding {
  const nested = r?.funding ?? null;
  const rawInv = r?.funding_investors ?? nested?.investors ?? null;
  let investors: string[] = [];
  if (Array.isArray(rawInv)) investors = rawInv.filter((x) => typeof x === "string" && x);
  else if (typeof rawInv === "string" && rawInv.trim()) {
    try {
      const parsed = JSON.parse(rawInv);
      investors = Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string" && x) : [];
    } catch {
      investors = rawInv.split(",").map((s) => s.trim()).filter(Boolean);
    }
  }
  return {
    stage: stageLabel(r?.funding_round_label) ?? stageLabel(r?.funding_stage ?? nested?.stage),
    amountUsdM: num(r?.funding_amount_usd_m ?? nested?.amount_usd_m),
    announced: (r?.funding_announced ?? nested?.announced) || null,
    valuationUsdM: num(r?.valuation_usd_m),
    investors,
  };
}

/** $850K, $32M, $2.5M, $1.2B. Input is millions of US dollars. */
export function money(usdM: number | null | undefined): string | null {
  if (usdM == null || !Number.isFinite(usdM) || usdM <= 0) return null;
  if (usdM < 1) return `$${Math.round(usdM * 1000)}K`;
  if (usdM >= 1000) {
    const b = usdM / 1000;
    return `$${b >= 10 ? Math.round(b) : trim(b.toFixed(1))}B`;
  }
  return `$${usdM >= 10 ? Math.round(usdM) : trim(usdM.toFixed(1))}M`;
}
const trim = (s: string) => s.replace(/\.0$/, "");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** When a round was announced: "Mar 2026" (or "2025" when only the year is known),
 *  how many months ago, and whether that is recent — twelve months or less. */
export function roundWhen(announced: string | null | undefined): {
  label: string; ago: string | null; recent: boolean;
} | null {
  if (!announced || typeof announced !== "string") return null;
  const m = announced.trim().match(/^(\d{4})(?:-(\d{1,2}))?/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = m[2] ? Number(m[2]) : null;
  if (year < 1990 || year > 2100 || (month !== null && (month < 1 || month > 12))) return null;
  const now = new Date();
  // a year-only date is treated as mid-year: neither flattering nor harsh
  const months = (now.getFullYear() - year) * 12 + (now.getMonth() + 1 - (month ?? 6));
  const ago = months < 1 ? "this month" : months < 24 ? `${months} mo ago` : `${Math.floor(months / 12)} yr ago`;
  return {
    label: month ? `${MONTHS[month - 1]} ${year}` : String(year),
    ago: months >= 0 ? ago : null,
    recent: months >= 0 && months <= 12,
  };
}

/* ------------------------------------------------------------------ cities
   Tejmul, 2026-10-06: in India only Bengaluru and Hyderabad matter. A company whose
   city reads "Bengaluru · Hyderabad" counts for both. */
export const CITIES = ["Bengaluru", "Hyderabad"] as const;
export type City = (typeof CITIES)[number];
const CITY_RE: Record<City, RegExp> = { Bengaluru: /bengaluru|bangalore|blr/i, Hyderabad: /hyderabad|hyd\b/i };

export function inCity(city: string | null | undefined, want: City | "all"): boolean {
  if (want === "all") return true;
  return !!city && CITY_RE[want].test(city);
}
