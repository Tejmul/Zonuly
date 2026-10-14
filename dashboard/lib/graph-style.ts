/*  The graph map's visual contract (CHOKEPOINTS §13.3).
 *
 *  Every visual difference on the /graph canvas funnels through ONE pure function,
 *  styleForNode(). The canvas and the legend both call it, and both paint through
 *  paintNode(), so they can never disagree. Change the look here, never in the render
 *  loop.
 *
 *  Black and white only: state is weight, opacity, rings and dashes — never colour.
 *
 *      complete company      solid ink dot
 *      still filling         hollow ring; ring width = how many parts are missing
 *                            (1–4 of website · description · hiring · referrer email)
 *      no site / no desc.    the ring is dashed — nothing has been read about them yet
 *      on the ★ list         a thin outer halo
 *      hub / segment         ring with a progress arc (share complete); a dashed outer
 *                            "roll-up" ring when a pending ★ company sits under it
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
export type Tier = "hub" | "sub" | "leaf";

/** What the canvas draws: a payload record plus its tier. `agg` is the bucket's roll-up
 *  over the leaves the filters currently let through (so rings follow the filters). */
export type MapNode =
  | { tier: "hub" | "sub"; id: string; data: Bucket; agg: Agg }
  | { tier: "leaf"; id: string; data: Leaf };

export type Agg = { count: number; complete: number; pending: number; star_pending: number };

/** Everything about the moment that can change a node's look. */
export type NodeState = {
  hovered: boolean;
  selected: boolean;
  /** none: nothing is focused · in: in the focused node's neighbourhood · out: dimmed */
  focus: "none" | "in" | "out";
  /** null: no search · true/false: matched or not */
  match: boolean | null;
  /** 0..1 — the filter fade (tweened), 1 = shown */
  visible: number;
  /** 0..1 — leaf level of detail from zoom (hubs and subs ignore it) */
  lod: number;
  /** 0..1 — the slow pulse on pending rings; held at 0 under prefers-reduced-motion */
  pulse: number;
};

export type Ink = "ink" | "ink2" | "ink3" | "surface" | "surface2" | "line";

export type NodeStyle = {
  r: number;                    // world units (screenRadius turns it into pixels)
  fill: Ink | null;
  ring: Ink | null;
  ringWidth: number;            // px
  dash: number[] | null;        // px
  halo: Ink | null;             // an outer ring: ★, the roll-up, or the selection
  haloGap: number;              // px between the node and the halo
  haloWidth: number;
  haloDash: number[] | null;
  progress: number | null;      // 0..1 share complete, drawn as an arc on hubs / subs
  ringAlpha: number;            // multiplies alpha for the ring (the pulse lives here)
  haloAlpha: number;            // multiplies alpha for the halo (the roll-up pulse)
  alpha: number;
  label: "always" | "focus" | "none";
};

export const DEFAULT_STATE: NodeState = {
  hovered: false, selected: false, focus: "none", match: null, visible: 1, lod: 1, pulse: 0,
};

/** THE function. Pure: same node and state, same pixels. */
export function styleForNode(n: MapNode, s: NodeState): NodeStyle {
  let alpha = s.focus === "out" ? 0.12 : 1;
  if (s.match === false) alpha *= 0.18;
  const grow = (s.hovered ? 1.3 : 1) * (0.55 + 0.45 * s.visible);

  if (n.tier !== "leaf") {
    const hub = n.tier === "hub";
    const a = n.agg;
    const rollup = a.star_pending > 0;
    return {
      r: (hub ? 14 + Math.sqrt(a.count) * 0.9 : 5 + Math.sqrt(a.count) * 0.75) * grow,
      fill: hub ? "surface" : "surface2",
      ring: hub ? "ink" : "ink3",
      ringWidth: hub ? 1.5 : 1,
      // an empty chain hub (nothing passes the filters) is dashed; a rule hub never holds leaves
      dash: a.count === 0 && (n.data.row ?? 0) === 0 ? [2, 3] : null,
      halo: s.selected ? "ink" : rollup ? "ink" : null,
      haloGap: s.selected ? 5 : 4,
      haloWidth: s.selected ? 2 : 1.25,
      haloDash: s.selected ? null : [3, 3],
      progress: a.count ? a.complete / a.count : null,
      haloAlpha: s.selected ? 1 : 1 - 0.55 * s.pulse,
      ringAlpha: 1,
      alpha: alpha * Math.max(0.35, s.visible),
      label: hub || s.hovered || s.selected ? "always" : "focus",
    };
  }

  const d = n.data;
  const pending = d.status !== "complete";
  const missing = d.missing?.length ?? (pending ? 1 : 0);
  const thin = !!(d.no_site || d.no_description) || d.kind === "gap";
  const prio = Math.max(0, Math.min(100, d.priority ?? 0)) / 100;
  return {
    r: (2.4 + prio * 1.4) * grow,
    fill: pending ? "surface" : "ink",
    ring: pending ? "ink" : null,
    ringWidth: pending ? 0.6 + 0.55 * Math.min(4, missing) : 0,
    dash: pending && thin ? [1.6, 1.4] : null,
    halo: s.selected ? "ink" : d.star ? "ink2" : null,
    haloGap: s.selected ? 4 : 2,
    haloWidth: s.selected ? 2 : 0.9,
    haloDash: null,
    progress: null,
    haloAlpha: 1,
    ringAlpha: pending ? 1 - 0.45 * s.pulse : 1,
    alpha: alpha * s.lod * s.visible,
    label: s.hovered || s.selected ? "always" : "focus",
  };
}

/** A leaf's world radius becomes pixels with the zoom, but never vanishes or balloons. */
export function screenRadius(n: MapNode, st: NodeStyle, k: number): number {
  if (n.tier === "leaf") return Math.max(1.6, Math.min(st.r * 4, st.r * k));
  return Math.max(n.tier === "hub" ? 9 : 3.5, st.r * k);
}

export type Palette = Record<Ink, string> & { paper: string; lineStrong: string };

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

/** Paint one node at screen (x, y) with screen radius r. Shared by the canvas and the legend. */
export function paintNode(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, st: NodeStyle, pal: Palette) {
  if (st.alpha <= 0.004) return;
  ctx.globalAlpha = st.alpha;
  if (st.halo) {
    ctx.globalAlpha = st.alpha * st.haloAlpha;
    ctx.beginPath();
    ctx.arc(x, y, r + st.haloGap, 0, Math.PI * 2);
    ctx.setLineDash(st.haloDash ?? []);
    ctx.lineWidth = st.haloWidth;
    ctx.strokeStyle = pal[st.halo];
    ctx.stroke();
  }
  ctx.globalAlpha = st.alpha;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  if (st.fill) {
    ctx.fillStyle = pal[st.fill];
    ctx.fill();
  }
  if (st.ring && st.ringWidth > 0) {
    ctx.globalAlpha = st.alpha * st.ringAlpha;
    ctx.setLineDash(st.dash ?? []);
    ctx.lineWidth = st.ringWidth;
    ctx.strokeStyle = st.progress !== null ? pal.lineStrong : pal[st.ring];
    ctx.stroke();
  }
  if (st.progress !== null && st.progress > 0 && st.ring) {
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(x, y, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, st.progress));
    ctx.lineWidth = st.ringWidth + 1.5;
    ctx.strokeStyle = pal[st.ring];
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

/* ---------------------------------------------------------------- the legend
   Generated from styleForNode itself: each entry is a sample node in a sample state. */

const sampleLeaf = (over: Partial<Leaf>): MapNode => ({
  tier: "leaf", id: "x",
  data: { id: "x", kind: "company", label: "", hub: "", status: "filling", priority: 40, ...over },
});
const sampleBucket = (tier: "hub" | "sub", star: number): MapNode => {
  const agg = { count: 36, complete: 12, pending: 24, star_pending: star };
  return {
    tier, id: "b", agg,
    data: { id: "b", label: "", ...agg, india_pending: 0, missing: {} },
  };
};

export const LEGEND: { key: string; label: string; node: MapNode; state?: Partial<NodeState> }[] = [
  { key: "complete", label: "Complete", node: sampleLeaf({ status: "complete", missing: [] }) },
  { key: "one", label: "Filling · 1 part missing", node: sampleLeaf({ missing: ["referrer_email"] }) },
  { key: "three", label: "Filling · 3 parts missing", node: sampleLeaf({ missing: ["description", "hiring", "referrer_email"] }) },
  { key: "bare", label: "No site or description yet", node: sampleLeaf({ missing: ["website", "description"], no_site: true, no_description: true }) },
  { key: "star", label: "★ India list", node: sampleLeaf({ missing: ["hiring"], star: true }) },
  { key: "hub", label: "Layer / stage · arc = share complete", node: sampleBucket("hub", 0) },
  { key: "rollup", label: "Holds a pending ★ company", node: sampleBucket("sub", 1) },
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
