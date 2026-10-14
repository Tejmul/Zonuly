/*  Where every star sits on the /graph canvas — pure and deterministic.
 *
 *  Nothing here is physics. The same payload always draws the same chart, and a
 *  position means something you can read off the chart:
 *
 *    Atlas (polar, "you" at the centre)
 *      ANGLE   industry. The layers are sectors sized by how many companies they hold,
 *              separated by a small gap where a hairline spoke is drawn; segments are
 *              sub-sectors inside them, sized the same way.
 *      RADIUS  how good a bet: r = RMIN + (1 − RMIN)(1 − score/100), score = best
 *              selection (else priority). The strongest targets sit nearest "you".
 *      A star's angle inside its segment and a ±2.5-point radial jitter come from a
 *      hash of its id; a light collision pass (d3-quadtree, five rounds) then nudges
 *      overlapping stars apart, clamped to their own wedge and to ±6 score points, so
 *      no star ever drifts into another industry or misstates its score by more than
 *      the ring spacing allows.
 *
 *    Transit (the pipeline)
 *      One column per stage, left → right; inside a column the stars are packed in a
 *      grid, strongest bet first (top-left). The cell size is chosen so the fullest
 *      column fits the canvas.
 */

import { quadtree } from "d3-quadtree";
import { MAG_R, type Bucket } from "@/lib/graph-style";

export const TAU = Math.PI * 2;
/** The hole in the middle, where "you" sits. */
export const RMIN = 0.075;
/** Normalised radius (0..1 of the rim) for a score. */
export const uOf = (score: number) => RMIN + (1 - RMIN) * (1 - Math.max(0, Math.min(100, score)) / 100);

const HUB_GAP = (1.3 / 180) * Math.PI;
const SUB_GAP = (0.35 / 180) * Math.PI;
/** The rim radius in px the collision pass assumes; the real rim scales from it. */
const UNIT_PX = 420;

export type Sector = {
  id: string;
  tier: "hub" | "sub";
  hub: string;
  label: string;
  code?: string;
  a0: number;
  a1: number;
};

export type PolarItem = { id: string; hub: string; sub?: string | null; score: number; mag: 0 | 1 | 2 };

export type Polar = {
  sectors: Sector[];
  /** angles of the hairline spokes between hubs */
  spokes: number[];
  /** unit positions (rim = 1), aligned with the items passed in */
  x: Float32Array;
  y: Float32Array;
  /** normalised radius, for the outward sweep on first load */
  r: Float32Array;
};

/** FNV-1a → two uniform floats in [0, 1). Stable across reloads, browsers and machines. */
export function hash2(s: string): [number, number] {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const a = (h >>> 0) / 4294967296;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  h = Math.imul(h, 0x297a2d39);
  h ^= h >>> 15;
  return [a, (h >>> 0) / 4294967296];
}

/** Clockwise from twelve o'clock, as a star chart's right ascension is read round the dial. */
export const polarXY = (a: number, u: number): [number, number] => [Math.sin(a) * u, -Math.cos(a) * u];

export function polarLayout(hubs: Bucket[], subs: Bucket[], items: PolarItem[]): Polar {
  const n = items.length;
  const hubCount = new Map<string, number>();
  const subCount = new Map<string, number>();
  for (const it of items) {
    hubCount.set(it.hub, (hubCount.get(it.hub) ?? 0) + 1);
    if (it.sub) subCount.set(it.sub, (subCount.get(it.sub) ?? 0) + 1);
  }
  const chain = hubs.filter((h) => (h.row ?? 0) === 0);
  const total = Math.max(1, n);
  const weight = (id: string) => Math.max(hubCount.get(id) ?? 0, total * 0.014);
  const W = chain.reduce((a, h) => a + weight(h.id), 0) || 1;
  const avail = TAU - chain.length * HUB_GAP;

  const sectors: Sector[] = [];
  const spokes: number[] = [];
  const span = new Map<string, [number, number]>();
  let cursor = 0;
  for (const h of chain) {
    spokes.push(cursor);
    const a0 = cursor + HUB_GAP / 2;
    const a1 = a0 + (avail * weight(h.id)) / W;
    cursor = a1 + HUB_GAP / 2;
    sectors.push({ id: h.id, tier: "hub", hub: h.id, label: h.label, code: h.code, a0, a1 });
    span.set(h.id, [a0, a1]);
    // segments inside the hub, in the payload's order (largest first)
    const mine = subs.filter((s) => s.hub === h.id && (subCount.get(s.id) ?? 0) > 0);
    const inHub = hubCount.get(h.id) ?? 0;
    const loose = inHub - mine.reduce((a, s) => a + (subCount.get(s.id) ?? 0), 0);
    const parts = mine.length + (loose > 0 ? 1 : 0);
    const free = Math.max(0, a1 - a0 - Math.max(0, parts - 1) * SUB_GAP);
    let c = a0;
    for (const s of mine) {
      const w = (free * (subCount.get(s.id) ?? 0)) / Math.max(1, inHub);
      sectors.push({ id: s.id, tier: "sub", hub: h.id, label: s.label, a0: c, a1: c + w });
      span.set(s.id, [c, c + w]);
      c += w + SUB_GAP;
    }
    if (loose > 0) span.set(`${h.id}#loose`, [c, a1]);
  }

  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const r = new Float32Array(n);
  const lo = new Float32Array(n);
  const hi = new Float32Array(n);
  const u0 = new Float32Array(n);
  const rad = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const it = items[i];
    const sp = (it.sub ? span.get(it.sub) : undefined) ?? span.get(`${it.hub}#loose`) ?? span.get(it.hub) ?? [0, TAU];
    const pad = Math.min((sp[1] - sp[0]) * 0.1, 0.006);
    lo[i] = sp[0] + pad;
    hi[i] = Math.max(lo[i], sp[1] - pad);
    const [h1, h2] = hash2(it.id);
    const a = lo[i] + h1 * (hi[i] - lo[i]);
    u0[i] = uOf(it.score);
    const u = uOf(it.score + (h2 - 0.5) * 5);
    [x[i], y[i]] = polarXY(a, u);
    rad[i] = (MAG_R[it.mag] + 0.45) / UNIT_PX;
  }

  // the light collision pass: nudge, never fly
  const band = (1 - RMIN) * 0.06;
  const reachMax = (MAG_R[2] * 2 + 0.9) / UNIT_PX;
  const idx = Array.from({ length: n }, (_, i) => i);
  for (let round = 0; round < 5; round++) {
    const tree = quadtree<number>().x((i) => x[i]).y((i) => y[i]).addAll(idx);
    for (let i = 0; i < n; i++) {
      const xi = x[i];
      const yi = y[i];
      tree.visit((node, x0, y0, x1, y1) => {
        if (!("length" in node)) {
          let leaf: typeof node | undefined = node;
          do {
            const j = leaf.data;
            if (j > i) {
              const dx = x[i] - x[j];
              const dy = y[i] - y[j];
              const min = rad[i] + rad[j];
              const d2 = dx * dx + dy * dy;
              if (d2 < min * min) {
                const d = Math.sqrt(d2) || 1e-6;
                const push = ((min - d) / d) * 0.5;
                const px = d2 ? dx * push : ((i % 7) - 3) * 1e-4;
                const py = d2 ? dy * push : ((j % 5) - 2) * 1e-4;
                x[i] += px; y[i] += py;
                x[j] -= px; y[j] -= py;
              }
            }
            leaf = leaf.next;
          } while (leaf);
        }
        return x0 > xi + reachMax || x1 < xi - reachMax || y0 > yi + reachMax || y1 < yi - reachMax;
      });
    }
    // clamp every star back into its own wedge and score band
    for (let i = 0; i < n; i++) {
      let a = Math.atan2(x[i], -y[i]);
      if (a < 0) a += TAU;
      if (a < lo[i] || a > hi[i]) {
        const dLo = Math.min(Math.abs(a - lo[i]), TAU - Math.abs(a - lo[i]));
        const dHi = Math.min(Math.abs(a - hi[i]), TAU - Math.abs(a - hi[i]));
        a = dLo < dHi ? lo[i] : hi[i];
      }
      const u = Math.max(Math.max(RMIN + 0.01, u0[i] - band), Math.min(Math.min(1, u0[i] + band), Math.hypot(x[i], y[i])));
      [x[i], y[i]] = polarXY(a, u);
    }
  }
  for (let i = 0; i < n; i++) r[i] = Math.hypot(x[i], y[i]);
  return { sectors, spokes, x, y, r };
}

/* ---------------------------------------------------------------- transit */

export type Column = { id: string; label: string; code?: string; x0: number; x1: number };

export type Transit = {
  columns: Column[];
  x: Float32Array;
  y: Float32Array;
  /** 0..1 left → right, then top → bottom inside a column, for the stagger and the sweep */
  order: Float32Array;
  cell: number;
  header: number;
};

export const TRANSIT_HEADER = 62;

/** `stage[i]` is item i's column; `rank` lists item indices strongest bet first. */
export function transitLayout(w: number, h: number, stages: Bucket[], stage: Int16Array, rank: number[]): Transit {
  const n = stage.length;
  const pad = w < 560 ? 6 : 14;
  const nc = Math.max(1, stages.length);
  const colW = (w - pad * 2) / nc;
  const columns: Column[] = stages.map((s, c) => ({ id: s.id, label: s.label, code: s.code, x0: pad + c * colW, x1: pad + (c + 1) * colW }));
  const per = new Array<number>(nc).fill(0);
  for (let i = 0; i < n; i++) if (stage[i] >= 0) per[stage[i]]++;
  const most = Math.max(1, ...per);
  const inner = Math.max(8, colW - (w < 560 ? 6 : 16));
  const availH = Math.max(40, h - TRANSIT_HEADER - 14);
  let cell = Math.max(3, Math.min(14, Math.sqrt((inner * availH) / most)));
  for (let guard = 0; guard < 80; guard++) {
    const cols = Math.max(1, Math.floor(inner / cell));
    if (Math.ceil(most / cols) * cell <= availH || cell <= 3) break;
    cell -= 0.2;
  }
  const gridCols = Math.max(1, Math.floor(inner / cell));
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const order = new Float32Array(n);
  const slot = new Array<number>(nc).fill(0);
  for (const i of rank) {
    const c = stage[i];
    if (c < 0) continue;
    const k = slot[c]++;
    const col = columns[c];
    const used = Math.min(gridCols, Math.max(1, per[c])) * cell;
    const left = col.x0 + (col.x1 - col.x0 - Math.min(used, gridCols * cell)) / 2;
    x[i] = left + ((k % gridCols) + 0.5) * cell;
    y[i] = TRANSIT_HEADER + (Math.floor(k / gridCols) + 0.5) * cell;
    order[i] = (c + Math.min(1, k / Math.max(1, per[c]))) / nc;
  }
  return { columns, x, y, order, cell, header: TRANSIT_HEADER };
}
