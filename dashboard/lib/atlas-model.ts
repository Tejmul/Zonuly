/*  The Atlas model.
 *
 *  Atlas used to be a map of the market: 34 industry segments and a hairball of
 *  edges between them. It answered "what is out there", which is not a question
 *  anyone was asking. The question is "given who I am and what I want, which
 *  companies should I put my hours into" — and that is a computation, not a map.
 *
 *  So this is the computation, written down where it can be drawn: your resume and
 *  your intent are the inputs, six terms are the layer that weighs them, segments
 *  light up, companies come out ranked. Every edge in the picture is one of these
 *  numbers. Nothing here is a metaphor for a calculation — it IS the calculation,
 *  and the diagram is only its shape.
 *
 *  It is deliberately a transparent linear model rather than anything learned. You
 *  can read why a company scored what it scored, disagree, move a slider, and watch
 *  it change. A learned model would score better and explain nothing, and this
 *  product's whole argument is that a claim you cannot trace is worth nothing.
 */

export type Company = {
  id: number;
  name: string;
  tier: string | null;
  region: string | null;
  description: string | null;
  funding_stage: string | null;
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

/** The six things it weighs. These are the hidden layer, and the only one. */
export type TermKey = "field" | "pay" | "remote" | "fresher" | "hiring" | "lead";

export const TERMS: { key: TermKey; label: string; asks: string }[] = [
  { key: "field",   label: "Right kind of work", asks: "Is this the field you said you wanted?" },
  { key: "pay",     label: "Can pay well",       asks: "Has it published a number, or raised enough to?" },
  { key: "remote",  label: "You could take it",  asks: "Hires from anywhere, or hires in India?" },
  { key: "fresher", label: "Open to you",        asks: "At least one role that is not senior-only." },
  { key: "hiring",  label: "Really hiring",      asks: "Their own careers page backs the claim." },
  { key: "lead",    label: "Someone to ask",     asks: "A person inside with an address we could check." },
];

export type Weights = Record<TermKey, number>;

export const DEFAULT_WEIGHTS: Weights = {
  field: 1, pay: 0.5, remote: 0.8, fresher: 0.9, hiring: 1, lead: 0.7,
};

/* --------------------------------------------------------------- the terms */

/** Each term returns 0..1 with the sentence that justifies it. A term that cannot
 *  be evidenced returns 0 and says so — never a middling guess. */
export function termScores(c: Company, fields: FieldKey[]): Record<TermKey, { v: number; why: string }> {
  const field = fieldAffinity(c.segment, fields);

  const pay =
    c.tier === "tier1" ? 1
    : c.tier === "tier2" ? 0.7
    : c.tier === "prospect" ? 0.4
    : 0.12;

  const remote = c.anywhere > 0 ? 1 : c.region === "india" ? 0.65 : c.region === "remote" ? 0.8 : 0.1;

  const hiring =
    c.hiring_status === "verified" ? 1
    : c.hiring_status === "role_missing" ? 0.45
    : c.hiring_status === "unchecked" ? 0.2
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
    pay: {
      v: pay,
      why:
        c.tier === "tier1" ? "a posting states ₹30 L or more"
        : c.tier === "tier2" ? "pays in the ₹24–30 L band"
        : c.tier === "prospect" ? "pay unpublished, but funded and hiring"
        : "nothing published about pay",
    },
    remote: {
      v: remote,
      why:
        c.anywhere > 0 ? "a posting says work from anywhere"
        : c.region === "india" ? "hiring in India"
        : c.region === "remote" ? "remote, though not explicitly visa-free"
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
        : c.hiring_status === "unchecked" ? "not checked yet"
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
export function scoreCompany(c: Company, fields: FieldKey[], w: Weights): Scored {
  const raw = termScores(c, fields);
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

  return { company: c, score: sum, terms, top };
}

export function rank(companies: Company[], fields: FieldKey[], w: Weights, limit = 40): Scored[] {
  return companies
    .map((c) => scoreCompany(c, fields, w))
    .sort((a, b) => b.score - a.score || b.company.leads - a.company.leads)
    .slice(0, limit);
}

/** How hot each segment is under the current settings — the layer between the
 *  weights and the companies. Averaged over its own companies, not summed, so a
 *  272-company segment does not win by being big. */
export function segmentHeat(
  companies: Company[],
  fields: FieldKey[],
  w: Weights,
): { segment: string; label: string; heat: number; n: number; best: number }[] {
  const acc = new Map<string, { label: string; sum: number; n: number; best: number }>();
  for (const c of companies) {
    const s = scoreCompany(c, fields, w).score;
    const cur = acc.get(c.segment) ?? { label: c.segment_label, sum: 0, n: 0, best: 0 };
    cur.sum += s;
    cur.n += 1;
    cur.best = Math.max(cur.best, s);
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
