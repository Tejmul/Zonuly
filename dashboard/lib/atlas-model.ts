/*  The Atlas model.
 *
 *  Atlas used to be a map of the market: 34 industry segments and a hairball of
 *  edges between them. It answered "what is out there", which is not a question
 *  anyone was asking. The question is "given who I am and what I want, which
 *  companies should I put my hours into" — and that is a computation, not a map.
 *
 *  So this is the computation, written down where it can be drawn: your resume and
 *  your intent are the inputs, filters decide who is in the room, seven terms are
 *  the layer that weighs them, segments light up, companies come out ranked. Every edge in the picture is one of these
 *  numbers. Nothing here is a metaphor for a calculation — it IS the calculation,
 *  and the diagram is only its shape.
 *
 *  It is deliberately a transparent linear model rather than anything learned. You
 *  can read why a company scored what it scored, disagree, move a slider, and watch
 *  it change. A learned model would score better and explain nothing, and this
 *  product's whole argument is that a claim you cannot trace is worth nothing.
 */

import { inCity, money, stageLabel } from "@/lib/funding";

export type Company = {
  id: number;
  name: string;
  tier: string | null;
  region: string | null;
  description: string | null;
  ppo_lpa: number | null;
  hiring_status: string | null;
  segment: string;
  segment_label: string;
  roles: number;
  fresher: number;
  anywhere: number;
  leads: number;
  bet: boolean;
  near: boolean;
  missing: string[];
  /** "Bengaluru", "Hyderabad", "Bengaluru · Hyderabad" — the only Indian cities kept. */
  city?: string | null;
  /** "india-star" for the hand-picked ★ India list. */
  list_key?: string | null;
  funding_stage: string | null;
  funding_announced?: string | null;
  funding_amount_usd_m?: number | null;
  funding_total_usd_m?: number | null;
  valuation_usd_m?: number | null;
  pay_power_band?: string | null;
  /** The backend's own targeting model, 0..100 — used here to break ties. */
  priority_score?: number | null;
  priority_why?: string | null;
};

/* ------------------------------------------------------------------ fields */

/** What you want to work on. The numbers are how strongly a field claims a
 *  segment — "Foundation models & labs" is research outright; "Healthcare & bio"
 *  is research-adjacent and scores lower, because most of it is not research. */
export type FieldKey = "research" | "ai" | "infra" | "industry" | "consumer";

export const FIELDS: { key: FieldKey; label: string; blurb: string; weights: Record<string, number> }[] = [
  {
    key: "research",
    label: "Research & deep tech",
    blurb: "Labs, foundation models, robotics, materials, space — where the work is the science.",
    weights: {
      "ai:models": 1, "apps:deeptech": 1, "ai:perception": 0.8, "apps:robotics": 0.8,
      "ai:mlops": 0.7, "ai:inference": 0.65, "infra:hardware": 0.6, "apps:gov": 0.55,
      "apps:climate": 0.5, "apps:health": 0.45, "apps:automotive": 0.4,
    },
  },
  {
    key: "ai",
    label: "AI products",
    blurb: "Agents, inference, evals, AI for coding — shipping models into something people use.",
    weights: {
      "ai:agents": 1, "ai:inference": 0.9, "ai:mlops": 0.9, "dev:code-ai": 0.9,
      "ai:models": 0.8, "ai:perception": 0.75, "dev:tools": 0.5, "infra:data": 0.45,
    },
  },
  {
    key: "infra",
    label: "Infrastructure",
    blurb: "Cloud, data, security, observability, developer platforms — the layer under everything.",
    weights: {
      "infra:cloud": 1, "infra:data": 1, "infra:observability": 1, "infra:security": 0.9,
      "dev:tools": 0.85, "infra:hardware": 0.7, "ai:inference": 0.6,
    },
  },
  {
    key: "industry",
    label: "Applied to an industry",
    blurb: "Fintech, health, legal, logistics — software with a domain wrapped around it.",
    weights: {
      "apps:fintech": 1, "apps:health": 1, "apps:legal": 0.9, "apps:logistics": 0.9,
      "apps:hr": 0.8, "apps:realestate": 0.8, "apps:education": 0.8, "apps:sales": 0.75,
      "apps:software": 0.7, "apps:commerce": 0.7, "apps:agri": 0.6, "apps:climate": 0.6,
    },
  },
  {
    key: "consumer",
    label: "Consumer scale",
    blurb: "Social, media, gaming, commerce — products where the user count is the problem.",
    weights: {
      "apps:consumer": 1, "apps:media": 1, "apps:commerce": 0.85,
      "apps:productivity": 0.7, "apps:education": 0.5,
    },
  },
];

/* ----------------------------------------------------------------- weights */

/** The seven things it weighs. These are the hidden layer, and the only one. */
export type TermKey = "field" | "funded" | "pay" | "remote" | "fresher" | "hiring" | "lead";

export const TERMS: { key: TermKey; label: string; asks: string }[] = [
  { key: "field",   label: "Right kind of work", asks: "Is this the field you said you wanted?" },
  { key: "funded",  label: "Recently funded",    asks: "Did it raise in the last two years — the money that pays new hires?" },
  { key: "pay",     label: "Can pay well",       asks: "Has it published a number, or raised enough to?" },
  { key: "remote",  label: "You could take it",  asks: "Hires from anywhere, or on-site in Bengaluru or Hyderabad?" },
  { key: "fresher", label: "Open to you",        asks: "At least one role that is not senior-only." },
  { key: "hiring",  label: "Really hiring",      asks: "Their own careers page backs the claim." },
  { key: "lead",    label: "Someone to ask",     asks: "A person inside with an address we could check." },
];

export type Weights = Record<TermKey, number>;

/** Tuned against the backend's priority_score (which knows nothing of your field):
 *  with nothing filtered this order agrees with it at Spearman ρ ≈ 0.85, and 60 of
 *  its top 100 are in ours. Money first — a recent round is what pays a new hire —
 *  then whether you could take it. Field is low because the field chips now filter:
 *  inside what is left, field mostly re-states what you already chose. */
export const DEFAULT_WEIGHTS: Weights = {
  field: 0.3, funded: 1, pay: 0.8, remote: 0.5, fresher: 0.5, hiring: 0.4, lead: 0.3,
};

/* ------------------------------------------------------------------- facts
   What the filters and the terms read, derived once per company rather than once
   per keystroke: the normalised round, months since it, the two cities, the text
   the search box looks through. */

export type Facts = {
  c: Company;
  stage: string | null;
  months: number | null;
  blr: boolean;
  hyd: boolean;
  hay: string;
};

/** Months since an ISO-ish date ("2026-03-14", "2026-03", "2026"). A year-only date
 *  is treated as mid-year. Future dates and garbage are null, never a guess. */
export function monthsSince(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso || typeof iso !== "string") return null;
  const m = iso.trim().match(/^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?/);
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), m[2] ? Number(m[2]) - 1 : 5, m[3] ? Number(m[3]) : m[2] ? 15 : 30);
  if (!Number.isFinite(t)) return null;
  const months = (now - t) / (30.44 * 86_400_000);
  return months < -1 ? null : Math.max(0, months);
}

export function factsOf(c: Company, now = Date.now()): Facts {
  return {
    c,
    stage: stageLabel(c.funding_stage),
    months: monthsSince(c.funding_announced, now),
    blr: inCity(c.city, "Bengaluru"),
    hyd: inCity(c.city, "Hyderabad"),
    hay: `${c.name} ${c.description ?? ""} ${c.segment_label} ${c.city ?? ""}`.toLowerCase(),
  };
}

/* ----------------------------------------------------------------- filters
   Filters remove; the sliders only rank what is left. The field chips used to be
   one weight among six, worth a fifth of the score at most, so picking a field
   barely moved the top of the list — it looked like the chips did nothing, because
   they nearly did. Now a field you pick is a door, not a nudge. */

export type Where = "any" | "anywhere" | "bengaluru" | "hyderabad" | "india";
export const WHERE: { key: Where; label: string }[] = [
  { key: "any", label: "Any" },
  { key: "anywhere", label: "Remote from anywhere" },
  { key: "bengaluru", label: "Bengaluru" },
  { key: "hyderabad", label: "Hyderabad" },
  { key: "india", label: "India" },
];

export const STAGES = ["Pre-seed", "Seed", "Series A", "Series B", "Series C", "Series D+"] as const;
export type Stage = (typeof STAGES)[number];

export type Within = 0 | 6 | 12 | 24;
export const WITHIN: { key: Within; label: string }[] = [
  { key: 0, label: "Any" }, { key: 6, label: "6 mo" }, { key: 12, label: "12 mo" }, { key: 24, label: "24 mo" },
];

export type Must = "fresher" | "hiring" | "lead" | "star";
export const MUSTS: { key: Must; label: string }[] = [
  { key: "fresher", label: "Fresher role" },
  { key: "hiring", label: "Hiring verified" },
  { key: "lead", label: "Someone to ask" },
  { key: "star", label: "★ India list" },
];

export type Filters = {
  fields: FieldKey[];
  where: Where;
  stages: Stage[];
  within: Within;
  must: Must[];
  q: string;
};

/** A field claims a segment when its affinity is at least this. */
export const FIELD_CUTOFF = 0.4;

type Dim = "field" | "where" | "stage" | "within" | "q" | Must;
type Test = (x: Facts) => boolean;

const fieldTest = (keys: FieldKey[]): Test => (x) => fieldAffinity(x.c.segment, keys) >= FIELD_CUTOFF;
const whereTest = (w: Where): Test =>
  w === "anywhere" ? (x) => x.c.anywhere > 0
  : w === "bengaluru" ? (x) => x.blr
  : w === "hyderabad" ? (x) => x.hyd
  : w === "india" ? (x) => x.blr || x.hyd
  : () => true;
const withinTest = (n: Within): Test => (x) => x.months !== null && x.months <= n;
const MUST_TEST: Record<Must, Test> = {
  fresher: (x) => x.c.fresher > 0,
  hiring: (x) => x.c.hiring_status === "verified",
  lead: (x) => x.c.leads > 0,
  star: (x) => x.c.list_key === "india-star",
};

/** Only the filters that are on — an "Any" costs nothing per company. */
function activeTests(f: Filters): [Dim, Test][] {
  const out: [Dim, Test][] = [];
  if (f.fields.length) out.push(["field", fieldTest(f.fields)]);
  if (f.where !== "any") out.push(["where", whereTest(f.where)]);
  if (f.stages.length) {
    const set = new Set<string>(f.stages);
    out.push(["stage", (x) => x.stage !== null && set.has(x.stage)]);
  }
  if (f.within) out.push(["within", withinTest(f.within)]);
  for (const m of f.must) out.push([m, MUST_TEST[m]]);
  const needle = f.q.trim().toLowerCase();
  if (needle) out.push(["q", (x) => x.hay.includes(needle)]);
  return out;
}

export type Counts = {
  total: number;
  matched: Facts[];
  field: Record<FieldKey, number>;
  where: Record<Where, number>;
  stage: Record<Stage, number>;
  within: Record<Within, number>;
  must: Record<Must, number>;
  /** For each active filter: how many would match without it. Feeds the empty-state hint. */
  without: Partial<Record<Dim, number>>;
};

export const DIM_LABEL: Record<Dim, string> = {
  field: "the field", where: "where", stage: "the stage", within: "funded within", q: "the search",
  fresher: "Fresher role", hiring: "Hiring verified", lead: "Someone to ask", star: "★ India list",
};

/** One pass that answers both "what matches" and "what would each option leave".
 *  A company failing no active filter matches; one failing exactly one filter is in
 *  that filter's "everything else holds" base, which is what each option's count is
 *  taken over. Failing two or more, it cannot be rescued by any single click. */
export function filterAndCount(all: Facts[], f: Filters): Counts {
  const tests = activeTests(f);
  const matched: Facts[] = [];
  const bases = new Map<Dim, Facts[]>();
  for (const x of all) {
    let failed: Dim | null = null;
    let fails = 0;
    for (const [d, t] of tests) {
      if (!t(x)) {
        failed = d;
        if (++fails > 1) break;
      }
    }
    if (fails === 0) matched.push(x);
    else if (fails === 1 && failed) {
      const b = bases.get(failed);
      if (b) b.push(x);
      else bases.set(failed, [x]);
    }
  }
  // the base for a dimension = everything that matches + what fails only on it
  const base = (d: Dim) => {
    const extra = bases.get(d);
    return extra ? matched.concat(extra) : matched;
  };
  const count = (rows: Facts[], t: Test) => {
    let n = 0;
    for (const x of rows) if (t(x)) n++;
    return n;
  };

  const fb = base("field");
  const field = {} as Counts["field"];
  for (const fl of FIELDS) field[fl.key] = count(fb, fieldTest([fl.key]));

  const wb = base("where");
  const where = {} as Counts["where"];
  for (const w of WHERE) where[w.key] = w.key === "any" ? wb.length : count(wb, whereTest(w.key));

  const sb = base("stage");
  const stage = {} as Counts["stage"];
  for (const s of STAGES) stage[s] = count(sb, (x) => x.stage === s);

  const tb = base("within");
  const within = {} as Counts["within"];
  for (const w of WITHIN) within[w.key] = w.key === 0 ? tb.length : count(tb, withinTest(w.key));

  const must = {} as Counts["must"];
  for (const m of MUSTS) must[m.key] = count(base(m.key), MUST_TEST[m.key]);

  const without: Counts["without"] = {};
  for (const [d] of tests) without[d] = base(d).length;

  return { total: all.length, matched, field, where, stage, within, must, without };
}

/* --------------------------------------------------------------- the terms */

const PAY: Record<string, { v: number; why: string }> = {
  pays:         { v: 1,    why: "they wrote the number down" },
  deep_pockets: { v: 0.85, why: "deep pockets — raised enough to pay well" },
  funded:       { v: 0.6,  why: "funded, pay unpublished" },
  thin:         { v: 0.25, why: "thin funding, pay likely low" },
  unknown:      { v: 0.1,  why: "nothing known about pay" },
};

function ago(months: number): string {
  const m = Math.round(months);
  if (m < 1) return "this month";
  if (m < 24) return `${m} month${m === 1 ? "" : "s"} ago`;
  const y = Math.floor(m / 12);
  return `${y} years ago`;
}

/** Each term returns 0..1 with the sentence that justifies it. A term that cannot
 *  be evidenced returns 0 and says so — never a middling guess. */
export function termScores(x: Facts, fields: FieldKey[]): Record<TermKey, { v: number; why: string }> {
  const c = x.c;
  const field = fieldAffinity(c.segment, fields);

  let pay = PAY[c.pay_power_band ?? "unknown"] ?? PAY.unknown;
  if (c.tier === "tier1" && pay.v < 0.9) pay = { v: 0.9, why: "a posting states ₹30 L or more" };

  const m = x.months;
  const funded =
    m === null ? 0 : m <= 6 ? 1 : m <= 12 ? 0.8 : m <= 18 ? 0.6 : m <= 24 ? 0.35 : 0.1;
  const fundedWhy =
    m === null
      ? x.stage ? `${x.stage}, no date on the round` : "no round on record"
      : [x.stage, money(c.funding_amount_usd_m), ago(m)].filter(Boolean).join(", ");

  const city = x.blr && x.hyd ? "Bengaluru and Hyderabad" : x.blr ? "Bengaluru" : x.hyd ? "Hyderabad" : null;
  const remote =
    c.anywhere > 0 ? 1 : city ? 0.65 : c.region === "remote" ? 0.8 : c.region === "india" ? 0.3 : 0.1;

  const hiring =
    c.hiring_status === "verified" ? 1
    : c.hiring_status === "role_missing" ? 0.45
    : c.hiring_status == null || c.hiring_status === "unchecked" ? 0.2
    : c.hiring_status === "unreachable" ? 0.15
    : 0;

  return {
    field: {
      v: field,
      why: fields.length
        ? field > 0.05
          ? `${c.segment_label} is close to what you asked for`
          : `${c.segment_label} is not the field you picked`
        : "no field chosen — everything counts equally",
    },
    funded: { v: funded, why: fundedWhy },
    pay,
    remote: {
      v: remote,
      why:
        c.anywhere > 0 ? "a posting says work from anywhere"
        : city ? `on-site in ${city}`
        : c.region === "remote" ? "remote, though not explicitly visa-free"
        : c.region === "india" ? "in India, but not Bengaluru or Hyderabad"
        : "on-site somewhere you would need a visa for",
    },
    fresher: {
      v: c.fresher > 0 ? 1 : 0,
      why: c.fresher > 0 ? `${c.fresher} of ${c.roles} roles open to early career` : "every open role is senior",
    },
    hiring: {
      v: hiring,
      why:
        c.hiring_status === "verified" ? "their own careers page lists it"
        : c.hiring_status === "role_missing" ? "hiring, but not this kind of role"
        : c.hiring_status == null || c.hiring_status === "unchecked" ? "not checked yet"
        : c.hiring_status === "unreachable" ? "their site could not be read"
        : "a board says so, their site does not",
    },
    lead: {
      v: Math.min(1, c.leads / 3),
      why: c.leads ? `${c.leads} ${c.leads === 1 ? "person" : "people"} inside we could reach` : "nobody to ask yet",
    },
  };
}

export function fieldAffinity(segment: string, fields: FieldKey[]): number {
  if (!fields.length) return 0.5; // no field chosen: do not let this term pick winners
  let best = 0;
  for (const key of fields) {
    const f = FIELDS.find((x) => x.key === key);
    best = Math.max(best, f?.weights[segment] ?? 0);
  }
  return best;
}

export type Scored = {
  company: Company;
  score: number;
  terms: Record<TermKey, { v: number; why: string; contribution: number }>;
  top: TermKey[];
};

/** Weighted mean, so the score stays 0..1 however the sliders are set and two
 *  different weightings stay comparable. */
export function scoreCompany(x: Facts, fields: FieldKey[], w: Weights): Scored {
  const raw = termScores(x, fields);
  const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;

  const terms = {} as Scored["terms"];
  let sum = 0;
  for (const t of TERMS) {
    const contribution = (raw[t.key].v * w[t.key]) / total;
    terms[t.key] = { ...raw[t.key], contribution };
    sum += contribution;
  }

  const top = [...TERMS]
    .sort((a, b) => terms[b.key].contribution - terms[a.key].contribution)
    .slice(0, 2)
    .map((t) => t.key);

  return { company: x.c, score: sum, terms, top };
}

/** Everything that passed the filters, best first. Ties — and with integer-ish
 *  terms there are many — break on the backend's priority score, then on leads,
 *  then on id, so the order is decided rather than inherited from the input. */
export function rank(rows: Facts[], fields: FieldKey[], w: Weights): Scored[] {
  return rows
    .map((x) => scoreCompany(x, fields, w))
    .sort(
      (a, b) =>
        b.score - a.score ||
        (b.company.priority_score ?? 0) - (a.company.priority_score ?? 0) ||
        b.company.leads - a.company.leads ||
        a.company.id - b.company.id,
    );
}

/** How hot each segment is under the current settings — the layer between the
 *  weights and the companies. Averaged over its own companies, not summed, so a
 *  272-company segment does not win by being big. Read off the already-ranked
 *  list, so nothing is scored twice. */
export function segmentHeat(
  ranked: Scored[],
): { segment: string; label: string; heat: number; n: number; best: number }[] {
  const acc = new Map<string, { label: string; sum: number; n: number; best: number }>();
  for (const r of ranked) {
    const c = r.company;
    const cur = acc.get(c.segment) ?? { label: c.segment_label, sum: 0, n: 0, best: 0 };
    cur.sum += r.score;
    cur.n += 1;
    cur.best = Math.max(cur.best, r.score);
    acc.set(c.segment, cur);
  }
  return [...acc.entries()]
    .map(([segment, v]) => ({ segment, label: v.label, heat: v.sum / v.n, n: v.n, best: v.best }))
    .sort((a, b) => b.heat - a.heat);
}

/* ------------------------------------------------------------ the resume in */

export type Profile = {
  headline?: string;
  years_experience?: number;
  skills?: Record<string, string[]>;
  strengths?: string[];
  target_titles?: string[];
};

/** What of the resume actually reaches the model. Being straight about this
 *  matters: the resume shapes the default field and the fresher term, and the
 *  rest of it is read by the scorer per role, not here. */
export function resumeSignals(p: Profile | null): { label: string; detail: string }[] {
  if (!p) return [];
  const out: { label: string; detail: string }[] = [];
  const skills = Object.values(p.skills ?? {}).flat();
  if (skills.length) out.push({ label: "Skills", detail: skills.slice(0, 6).join(" · ") });
  if (p.strengths?.length) out.push({ label: "Strengths", detail: p.strengths.slice(0, 3).join(" · ") });
  if (p.target_titles?.length) out.push({ label: "Titles you want", detail: p.target_titles.slice(0, 3).join(" · ") });
  if (p.years_experience != null)
    out.push({
      label: "Experience",
      detail: `${p.years_experience} year${p.years_experience === 1 ? "" : "s"} — early career, so "open to you" is weighted high`,
    });
  return out;
}

/** The field your resume points at, used as the opening position so the page is
 *  never empty on arrival. You can override it; it is a starting guess, not a verdict. */
export function suggestedFields(p: Profile | null): FieldKey[] {
  const hay = [
    p?.headline ?? "",
    ...(p?.strengths ?? []),
    ...(p?.target_titles ?? []),
    ...Object.values(p?.skills ?? {}).flat(),
  ]
    .join(" ")
    .toLowerCase();

  const picks: FieldKey[] = [];
  if (/\b(ai|ml|llm|genai|machine learning|nlp)\b/.test(hay)) picks.push("ai");
  if (/\b(research|phd|paper|robotics|physics|materials)\b/.test(hay)) picks.push("research");
  if (/\b(infra|serverless|cloud|aws|kubernetes|platform|devops)\b/.test(hay)) picks.push("infra");
  return picks.length ? picks.slice(0, 2) : ["ai"];
}
