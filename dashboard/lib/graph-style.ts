/*  The graph map's visual contract (CHOKEPOINTS §13.3).
 *
 *  Every visual difference between two stars on the /graph canvas funnels through ONE
 *  pure function, styleForNode(). The canvas and the legend both call it, and both paint
 *  through paintStar(), so they can never disagree. Change the look here, never in the
 *  render loop.
 *
 *  Black and white only: state is fill, stroke weight, opacity and dash — never colour.
 *
 *      complete              filled ink dot
 *      still filling         hollow ring; the more parts are missing (of website ·
 *                            description · hiring · referrer email) the fainter and
 *                            thinner the ring — one missing is a near-solid outline
 *      nothing read yet      the ring is dashed (no site or no description, or all four
 *                            parts missing) — the fill pass has nothing to stand on
 *      on the ★ list         four short ticks around the star, like a reticle
 *      magnitude             three sizes from priority, not a continuous scale
 *
 *  Where a star sits is not style — it is data (lib/graph-layout.ts): distance from
 *  "you" is how good a bet it is.
 *
 *  The payload these read is pinned by tests/test_graphmap.py
 *  (test_leaf_contract_is_what_style_for_node_reads).
 */

export type Status = "complete" | "filling";

export type Leaf = {
  id: string;
  kind: "company" | "feature" | "gap" | "decision";
  label: string;
  hub: string;
  sub?: string | null;
  status: Status;
  missing?: string[];
  reasons?: Record<string, string>;
  notes?: Record<string, string>;
  // companies
  cid?: number;
  market?: "india" | "remote" | "abroad" | null;
  city?: string;
  star?: boolean;
  list_rank?: number;
  no_site?: boolean;
  no_description?: boolean;
  priority?: number | null;
  best_selection?: number;
  best_selection_band?: string;
  referrers?: number;
  referrers_email?: number;
  roles?: number;
  open_roles?: number;
  drafted?: boolean;
  filled_at?: string;
  stage?: number;
  // project
  summary?: string | null;
  feature_status?: string;
};

export type Bucket = {
  id: string;
  label: string;
  count: number;
  complete: number;
  pending: number;
  star_pending: number;
  india_pending: number;
  missing: Record<string, number>;
  code?: string;
  blurb?: string;
  order?: number;
  row?: number;
  hub?: string;
  reached?: number;
  kind?: string;
};

export type Link = { source: string; target: string; kind: string; weight: number; label?: string };

export type GraphMap = {
  view: ViewKey;
  views: { key: ViewKey; label: string }[];
  parts: { key: string; label: string }[];
  hubs: Bucket[];
  subs: Bucket[];
  leaves: Leaf[];
  links: Link[];
  investors: { id: string; label: string; companies: string[] }[];
  counts: {
    total: number; complete: number; filling: number; missing: Record<string, number>;
    star_pending: number; india_pending: number; remote_pending: number; never_filled: number;
  };
  generated_at: string;
};

export type ViewKey = "market" | "pipeline" | "project";

/** A bucket's roll-up over the leaves the filters currently let through. */
export type Agg = { count: number; complete: number; pending: number; star_pending: number };

/** Everything about the moment that can change a star's look. */
export type StarState = {
  hovered: boolean;
  selected: boolean;
  /** null: no search · true/false: matched or not */
  match: boolean | null;
  /** 0..1 — the filter fade (tweened), 1 = shown */
  visible: number;
};

export const DEFAULT_STATE: StarState = { hovered: false, selected: false, match: null, visible: 1 };

export type StarStyle = {
  mag: 0 | 1 | 2;
  r: number;                    // px at the home zoom
  fill: boolean;                // solid ink: complete
  ring: boolean;                // hollow outline: still filling
  ringWidth: number;            // px
  ringAlpha: number;            // multiplies alpha for the ring
  dash: readonly number[] | null;
  tick: boolean;                // the ★ reticle
  halo: boolean;                // the selection ring
  alpha: number;
};

/** Magnitudes — three steps, so a size difference always means a priority band. */
export const MAG_R = [1.55, 2.25, 3.05] as const;
const DASH = [1.5, 1.6] as const;
const RING_ALPHA = [1, 0.95, 0.74, 0.54, 0.38];
const RING_WIDTH = [0, 1.25, 1.1, 0.95, 0.85];

/** How good a bet, 0..100: the best-selection score, else priority. Project leaves
 *  have no score; done things sit near the centre, open ones at the rim. */
export function scoreOf(l: Leaf): number {
  if (l.kind !== "company") {
    if (l.status === "complete") return 80;
    return l.feature_status === "partial" ? 50 : 20;
  }
  const s = l.best_selection ?? l.priority ?? 0;
  return Math.max(0, Math.min(100, s));
}

export function magnitude(l: Leaf): 0 | 1 | 2 {
  if (l.kind !== "company") return l.kind === "decision" ? 0 : 1;
  const p = l.priority ?? 0;
  return p >= 60 ? 2 : p >= 25 ? 1 : 0;
}

/** THE function. Pure: same leaf and state, same pixels. */
export function styleForNode(l: Leaf, s: StarState): StarStyle {
  const pending = l.status !== "complete";
  const miss = pending ? Math.max(1, Math.min(4, l.missing?.length ?? 1)) : 0;
  const bare = pending && (!!l.no_site || !!l.no_description || miss >= 4 || l.kind === "gap");
  const mag = magnitude(l);
  const grow = s.hovered || s.selected ? 1.4 : 1;
  return {
    mag,
    r: MAG_R[mag] * grow,
    fill: !pending,
    ring: pending,
    ringWidth: RING_WIDTH[miss],
    ringAlpha: RING_ALPHA[miss],
    dash: bare ? DASH : null,
    tick: !!l.star,
    halo: s.selected,
    alpha: s.visible * (s.match === false ? 0.13 : 1),
  };
}

export type Palette = {
  ink: string; ink2: string; ink3: string; surface: string; surface2: string;
  line: string; lineStrong: string; paper: string;
};

/** The canvas cannot read CSS variables, so they are read here at draw time. A token
 *  added to globals.css must be added here too, or the canvas falls back silently. */
export function readPalette(el: Element = document.documentElement): Palette {
  const cs = getComputedStyle(el);
  const v = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb;
  return {
    ink: v("--ink", "#09090b"),
    ink2: v("--ink-2", "#52525b"),
    ink3: v("--ink-3", "#8e8e97"),
    surface: v("--surface", "#ffffff"),
    surface2: v("--surface-2", "#f4f4f5"),
    line: v("--line", "#e3e3e6"),
    lineStrong: v("--line-strong", "#c9c9cd"),
    paper: v("--paper", "#f4f4f5"),
  };
}

/* ---------------------------------------------------------------- glyphs
   Path2D, built once per quantised radius and reused every frame: a star is drawn by
   moving the origin to it and stroking or filling a cached path — no per-frame
   allocation, whatever the star count. */

const TAU = Math.PI * 2;
const circles = new Map<number, Path2D>();
const ticks = new Map<number, Path2D>();
const q = (r: number) => Math.round(r * 4) / 4;

function circleAt(r: number): Path2D {
  const k = q(r);
  let p = circles.get(k);
  if (!p) {
    p = new Path2D();
    p.arc(0, 0, k, 0, TAU);
    circles.set(k, p);
  }
  return p;
}

function tickAt(r: number): Path2D {
  const k = q(r);
  let p = ticks.get(k);
  if (!p) {
    p = new Path2D();
    const a = k + 1.8;
    const b = a + 2.6;
    p.moveTo(0, -a); p.lineTo(0, -b);
    p.moveTo(a, 0); p.lineTo(b, 0);
    p.moveTo(0, a); p.lineTo(0, b);
    p.moveTo(-a, 0); p.lineTo(-b, 0);
    ticks.set(k, p);
  }
  return p;
}

/** Paint one star at screen (x, y) with screen radius r. `dpr` is the canvas's device
 *  pixel ratio — the transform is set here so the cached paths draw at the origin.
 *  `fade` is motion, not style: the canvas's filter fade and intro sweep (0..1).
 *  Shared by the canvas and the legend. */
export function paintStar(
  ctx: CanvasRenderingContext2D, x: number, y: number, r: number, st: StarStyle, pal: Palette, dpr: number, fade = 1,
) {
  const alpha = st.alpha * fade;
  if (alpha <= 0.004) return;
  ctx.setTransform(dpr, 0, 0, dpr, dpr * x, dpr * y);
  const body = circleAt(r);
  if (st.fill) {
    ctx.globalAlpha = alpha;
    ctx.fillStyle = pal.ink;
    ctx.fill(body);
  } else if (st.ring) {
    ctx.globalAlpha = alpha * st.ringAlpha;
    ctx.strokeStyle = pal.ink;
    ctx.lineWidth = st.ringWidth;
    if (st.dash) ctx.setLineDash(st.dash as number[]);
    ctx.stroke(body);
    if (st.dash) ctx.setLineDash([]);
  }
  if (st.tick || st.halo) {
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = pal.ink;
    if (st.tick) {
      ctx.lineWidth = 0.9;
      ctx.stroke(tickAt(r));
    }
    if (st.halo) {
      ctx.lineWidth = 1;
      ctx.stroke(circleAt(r + (st.tick ? 6 : 4.5)));
    }
  }
  ctx.globalAlpha = 1;
}

/* ---------------------------------------------------------------- the legend
   Generated from styleForNode itself: each entry is one or more sample leaves. */

const sample = (over: Partial<Leaf>): Leaf =>
  ({ id: "x", kind: "company", label: "", hub: "", status: "filling", priority: 40, ...over });

export type LegendEntry = { key: string; label: string; leaves: Leaf[]; companiesOnly?: boolean };

export const LEGEND: LegendEntry[] = [
  { key: "complete", label: "Complete", leaves: [sample({ status: "complete", missing: [] })] },
  {
    key: "filling", label: "Still filling — fainter = more missing",
    leaves: [
      sample({ missing: ["referrer_email"] }),
      sample({ missing: ["hiring", "referrer_email"] }),
      sample({ missing: ["description", "hiring", "referrer_email"] }),
    ],
  },
  {
    key: "bare", label: "Nothing read yet", companiesOnly: true,
    leaves: [sample({ missing: ["website", "description", "hiring", "referrer_email"], no_site: true, no_description: true })],
  },
  { key: "star", label: "★ India list", companiesOnly: true, leaves: [sample({ status: "complete", star: true })] },
  {
    key: "mag", label: "Size = priority", companiesOnly: true,
    leaves: [sample({ status: "complete", priority: 10 }), sample({ status: "complete", priority: 40 }), sample({ status: "complete", priority: 80 })],
  },
];

export const PART_LABEL: Record<string, string> = {
  website: "Website",
  description: "Description",
  hiring: "Hiring proof",
  referrer_email: "Referrer email",
  gap: "Gap",
  partial: "Partly built",
  open: "Not started",
};
