"use client";

/*  The graph map, drawn to a canvas — a star atlas with you at the centre.
 *
 *  It used to be a force-directed graph: positions came from physics and meant nothing,
 *  labels collided, and the whole thing read as scribble. Now every position is data
 *  (lib/graph-layout.ts) and every look is styleForNode() (lib/graph-style.ts):
 *
 *    Atlas     polar. Angle = industry (layers as sectors, segments inside them);
 *              distance from "you" = how good a bet (best selection, else priority).
 *              Hairline score rings like a star chart's declination circles.
 *    Pipeline  a transit chart: one column per stage, stars packed strongest-first.
 *    Project   the context layer on the atlas grammar: stages as sectors; built work
 *              near the centre, open work at the rim.
 *
 *  Motion — one orchestrated move, everything else quiet:
 *    · first load: the rings draw in, then the stars appear sweeping outward from "you"
 *    · Atlas ⇄ Pipeline: every star travels from its polar position to its column slot
 *      (and back) in one staggered power3.inOut morph, ~850 ms; rings and spokes cross-
 *      fade out as the columns cross-fade in
 *    · filters fade stars (260 ms); selection zooms smoothly to the star; the shared-
 *      investor "constellation" of the hovered / selected star draws in over 250 ms
 *  Under prefers-reduced-motion every one of those is an instant state.
 *
 *  Performance: one requestAnimationFrame loop that runs only while something moves;
 *  positions precomputed; glyphs are cached Path2D; devicePixelRatio scaling; star
 *  records and label slots are allocated once per payload, not per frame.
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { quadtree, type Quadtree } from "d3-quadtree";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { gsap } from "gsap";
import {
  DEFAULT_STATE, MAG_R, magnitude, paintStar, readPalette, scoreOf, styleForNode,
  type Agg, type GraphMap, type Leaf, type Palette, type StarStyle,
} from "@/lib/graph-style";
import { RMIN, TAU, TRANSIT_HEADER, polarLayout, transitLayout, uOf, type Column, type Polar, type Sector, type Transit } from "@/lib/graph-layout";

export type Mode = "atlas" | "transit" | "project";

export type CanvasProps = {
  mode: Mode;
  atlas: GraphMap | null;        // /api/graph/map?view=market
  transit: GraphMap | null;      // ?view=pipeline
  project: GraphMap | null;      // ?view=project
  shown: Set<string>;            // leaf ids the filters let through
  matches: Set<string> | null;   // search hits; null when no search
  agg: Map<string, Agg>;         // filtered roll-up per hub / segment / stage
  selected: string | null;
  onSelect: (id: string | null) => void;
  summary: string;               // the accessible description of what is drawn
};

export function GraphCanvas(props: CanvasProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const live = useRef({ ...props, hover: null as string | null });
  const engine = useRef<Engine | null>(null);

  // synced before any effect reads it (layout effects run first)
  useLayoutEffect(() => {
    Object.assign(live.current, props);
  });

  useEffect(() => {
    const el = canvas.current;
    const box = wrap.current;
    if (!el || !box) return;
    const eng = new Engine(el, box, live);
    engine.current = eng;
    return () => {
      eng.destroy();
      engine.current = null;
    };
  }, []);

  const { mode, atlas, transit, project, shown, matches, agg, selected } = props;
  useEffect(() => {
    engine.current?.setData();
  }, [mode, atlas, transit, project]);
  useEffect(() => {
    engine.current?.refilter();
  }, [shown, matches, agg]);
  useEffect(() => {
    engine.current?.select(selected);
  }, [selected]);

  return (
    <div ref={wrap} className="absolute inset-0">
      <canvas
        ref={canvas}
        className="block h-full w-full touch-none select-none"
        role="img"
        aria-label={props.summary}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- the engine
   A plain class, not hooks: it owns a zoom behaviour, observers and the rAF loop, and
   tears every one of them down in destroy(). */

type Live = RefObject<CanvasProps & { hover: string | null }>;

type Star = {
  id: string;
  leaf: Leaf;                    // the record for the current mode (label + style)
  la: Leaf | null;               // its atlas / project record
  lt: Leaf | null;               // its transit record
  score: number;
  // atlas: unit polar position (rim = 1) and normalised radius
  ux: number; uy: number; ur: number; hasA: boolean;
  // transit: px position, column, order (0..1)
  tx: number; ty: number; col: number; tord: number; hasT: boolean;
  // where it is drawn now (world px), its radius scale, and the morph's ends
  x: number; y: number; s: number;
  fx: number; fy: number; fs: number;
  gx: number; gy: number; gs: number;
  delay: number;
  sweep: number;                 // 0..1 when it appears in the intro sweep
  vis: number; want: number;     // filter fade
  st: StarStyle;                 // styleForNode at visible = 1; the fade multiplies at paint
  lw: number;                    // measured label width, 0 = not yet
};

type Family = "companies" | "project";

const MORPH_MS = 560;            // each star's own flight
const STAGGER_MS = 300;          // spread across the angle / the columns
const FADE_MS = 260;
const RING_MS = 520;
const SWEEP_MS = 620;
const LINE_MS = 250;
const LABEL_FONT = "500 10.5px";
const MAX_LABELS = 160;

const power3InOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const fmt = (n: number) => n.toLocaleString("en-US");
const now = () => performance.now();

/** The score rings each family draws, and what their tick labels say. */
const GRATICULE: Record<Family, { rings: number[]; ticks: { s: number; text: string }[] }> = {
  companies: { rings: [90, 70, 50, 30], ticks: [90, 70, 50, 30].map((s) => ({ s, text: String(s) })) },
  project: {
    rings: [65, 35],
    ticks: [{ s: 80, text: "BUILT" }, { s: 50, text: "PARTLY" }, { s: 20, text: "OPEN" }],
  },
};

class Engine {
  private ctx: CanvasRenderingContext2D;
  private stars: Star[] = [];
  private byId = new Map<string, Star>();
  private rank: number[] = [];                    // star indices, strongest bet first
  private neigh = new Map<string, string[]>();    // shared-investor constellations
  private family: Family | null = null;
  private mode: Mode = "atlas";
  private polar: Polar | null = null;
  private polarFor: GraphMap | null = null;
  private trans: Transit | null = null;
  private transFor: GraphMap | null = null;
  private sectors: Sector[] = [];
  private seen = { atlas: null as GraphMap | null, transit: null as GraphMap | null, project: null as GraphMap | null };

  private zoomer: ZoomBehavior<HTMLCanvasElement, unknown>;
  private t: ZoomTransform = zoomIdentity;
  private w = 1;
  private h = 1;
  private dpr = 1;
  private cx = 0;
  private cy = 0;
  private R = 1;
  private pal: Palette;
  private sans = "ui-sans-serif, system-ui, sans-serif";
  private mono = "ui-monospace, monospace";
  private still = reduced();

  private raf = 0;
  private last = 0;
  private dead = false;
  private morphAt = -1;                           // when the current morph began
  private morphSpan = 0;
  private introAt = -1;                           // when the rings began to draw
  private chromeA = 1;                            // polar chrome alpha (rings, spokes, sectors)
  private chromeT = 0;                            // transit chrome alpha (columns)
  private chromeFromA = 1;
  private chromeFromT = 0;
  private focusKey: string | null = null;
  private focusAt = 0;
  private pendingFocus: string | null = null;
  private tree: Quadtree<Star> | null = null;
  private tween: gsap.core.Tween | null = null;
  private placed = new Float32Array(MAX_LABELS * 4 + 8);
  private ro: ResizeObserver;
  private mo: MutationObserver;
  private widths = new Map<string, number>();

  constructor(private el: HTMLCanvasElement, private box: HTMLDivElement, private live: Live) {
    this.ctx = el.getContext("2d")!;
    this.pal = readPalette();
    this.sans = getComputedStyle(document.body).fontFamily || this.sans;
    this.mono = getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || this.mono;

    this.zoomer = d3zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.8, 30])
      .on("zoom", (e: { transform: ZoomTransform }) => {
        this.t = e.transform;
        this.kick();
      });
    const sel = select(this.el);
    sel.call(this.zoomer).on("dblclick.zoom", null);
    sel.on("click.map", (e: MouseEvent) => this.click(e));
    sel.on("mousemove.map", (e: MouseEvent) => this.move(e));
    sel.on("mouseleave.map", () => this.setHover(null));

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.box);
    // the theme is a class on <html>; the canvas re-reads its tokens when it flips
    this.mo = new MutationObserver(() => {
      this.pal = readPalette();
      this.kick();
    });
    this.mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    this.measureBox();
    this.setData();
    this.select(live.current.selected);
    // webfonts change label widths once they land
    document.fonts?.ready.then(() => {
      if (this.dead) return;
      this.widths.clear();
      for (const s of this.stars) s.lw = 0;
      this.kick();
    });
  }

  destroy() {
    this.dead = true;
    cancelAnimationFrame(this.raf);
    this.tween?.kill();
    this.ro.disconnect();
    this.mo.disconnect();
    select(this.el).on(".zoom", null).on(".map", null);
  }

  /* ------------------------------------------------------------ data */

  setData() {
    const L = this.live.current;
    const fam: Family = L.mode === "project" ? "project" : "companies";
    const prevMode = this.mode;
    const famChanged = fam !== this.family;
    const payloadChanged = L.atlas !== this.seen.atlas || L.transit !== this.seen.transit || L.project !== this.seen.project;
    this.seen = { atlas: L.atlas, transit: L.transit, project: L.project };
    this.mode = L.mode;

    if (famChanged || payloadChanged) this.build(fam, famChanged);
    this.family = fam;
    this.layout();

    const modeChanged = prevMode !== L.mode && !famChanged;
    if (famChanged) {
      this.place(false);
      this.startIntro();
    } else if (modeChanged) {
      this.place(true, L.mode === "transit" ? "angle" : "column");
      this.resetZoom();
    } else {
      this.place(payloadChanged && !this.still, "none");
    }
    this.refilter();
  }

  /** Stars for the family: the union of the payloads' leaves, carried over by id so a
   *  reload (after "Fill now", or a tick) keeps every star where it was. */
  private build(fam: Family, fresh: boolean) {
    const L = this.live.current;
    const old = this.byId;
    const recs = new Map<string, { la: Leaf | null; lt: Leaf | null }>();
    if (fam === "project") {
      for (const l of L.project?.leaves ?? []) recs.set(l.id, { la: l, lt: null });
    } else {
      for (const l of L.atlas?.leaves ?? []) recs.set(l.id, { la: l, lt: null });
      for (const l of L.transit?.leaves ?? []) {
        const r = recs.get(l.id);
        if (r) r.lt = l;
        else recs.set(l.id, { la: null, lt: l });
      }
    }
    const stars: Star[] = [];
    this.byId = new Map();
    for (const [id, { la, lt }] of recs) {
      const leaf = (la ?? lt)!;
      const prev = fresh ? undefined : old.get(id);
      const s: Star = {
        id, leaf, la, lt, score: scoreOf(leaf),
        ux: 0, uy: 0, ur: 1, hasA: false, tx: 0, ty: 0, col: -1, tord: 0, hasT: false,
        x: prev?.x ?? 0, y: prev?.y ?? 0, s: prev?.s ?? 1,
        fx: 0, fy: 0, fs: 1, gx: 0, gy: 0, gs: 1, delay: 0, sweep: 0,
        vis: prev?.vis ?? 0, want: 1, st: styleForNode(leaf, DEFAULT_STATE), lw: prev?.lw ?? 0,
      };
      stars.push(s);
      this.byId.set(id, s);
    }
    this.stars = stars;
    this.rank = stars.map((_, i) => i).sort((a, b) => stars[b].score - stars[a].score
      || (stars[b].leaf.priority ?? 0) - (stars[a].leaf.priority ?? 0) || stars[a].id.localeCompare(stars[b].id));

    // constellations: companies that share an investor; project leaves joined by a link
    this.neigh = new Map();
    const join = (a: string, b: string) => {
      if (a === b || !this.byId.has(a) || !this.byId.has(b)) return;
      const xs = this.neigh.get(a) ?? [];
      if (!xs.includes(b)) xs.push(b);
      this.neigh.set(a, xs);
    };
    if (fam === "companies") {
      for (const inv of L.atlas?.investors ?? []) for (const a of inv.companies) for (const b of inv.companies) join(a, b);
    } else {
      for (const l of L.project?.links ?? []) { join(l.source, l.target); join(l.target, l.source); }
    }
    this.polarFor = null;
    this.transFor = null;
    this.tree = null;
  }

  /** Recompute positions that depend on the payload (polar, in unit space) and on the
   *  canvas size (the transit grid). */
  private layout() {
    const L = this.live.current;
    const polarSrc = this.family === "project" ? L.project : L.atlas;
    if (polarSrc && polarSrc !== this.polarFor) {
      const withA = this.stars.filter((s) => s.la);
      const p = polarLayout(polarSrc.hubs, polarSrc.subs, withA.map((s) => ({
        id: s.id, hub: s.la!.hub, sub: s.la!.sub, score: s.score, mag: magnitude(s.la!),
      })));
      withA.forEach((s, i) => {
        s.ux = p.x[i]; s.uy = p.y[i]; s.ur = p.r[i]; s.hasA = true;
      });
      this.polar = p;
      this.sectors = p.sectors;
      this.polarFor = polarSrc;
    } else if (!polarSrc) {
      this.polar = null;
      this.sectors = [];
    }
    const tSrc = this.family === "companies" ? L.transit : null;
    if (tSrc) {
      const stages = tSrc.hubs;
      const at = new Map(stages.map((h, i) => [h.id, i]));
      const stage = new Int16Array(this.stars.length).fill(-1);
      this.stars.forEach((s, i) => {
        if (s.lt) stage[i] = s.lt.stage ?? at.get(s.lt.hub) ?? -1;
      });
      const tr = transitLayout(this.w, this.h, stages, stage, this.rank);
      this.stars.forEach((s, i) => {
        s.hasT = stage[i] >= 0;
        s.col = stage[i];
        s.tx = tr.x[i]; s.ty = tr.y[i]; s.tord = tr.order[i];
      });
      this.trans = tr;
      this.transFor = tSrc;
    } else {
      this.trans = null;
    }
  }

  private transitScale() {
    return this.trans ? Math.min(1, (this.trans.cell * 0.42) / MAG_R[2]) : 1;
  }

  /** Point every star at its slot for the current mode — instantly or as the morph. */
  private place(animate: boolean, stagger: "angle" | "column" | "none" = "none") {
    const transit = this.mode === "transit";
    const ts = this.transitScale();
    const go = animate && !this.still;
    for (const s of this.stars) {
      s.leaf = (transit ? s.lt ?? s.la : s.la ?? s.lt)!;
      const has = transit ? s.hasT : s.hasA;
      s.fx = s.x; s.fy = s.y; s.fs = s.s;
      if (has) {
        s.gx = transit ? s.tx : this.cx + s.ux * this.R;
        s.gy = transit ? s.ty : this.cy + s.uy * this.R;
        s.gs = transit ? ts : 1;
      } else {
        s.gx = s.x; s.gy = s.y; s.gs = s.s;
      }
      s.sweep = transit ? s.tord : s.ur;
      s.delay = stagger === "angle"
        ? (((Math.atan2(s.ux, -s.uy) + TAU) % TAU) / TAU) * STAGGER_MS
        : stagger === "column" ? s.tord * STAGGER_MS : 0;
      if (!go || (s.fx === 0 && s.fy === 0)) {
        s.x = s.gx; s.y = s.gy; s.s = s.gs;
      }
    }
    this.chromeFromA = this.chromeA;
    this.chromeFromT = this.chromeT;
    if (go) {
      this.morphAt = now();
      this.morphSpan = MORPH_MS + (stagger === "none" ? 0 : STAGGER_MS);
    } else {
      this.morphAt = -1;
      this.chromeA = transit ? 0 : 1;
      this.chromeT = transit ? 1 : 0;
    }
    this.restyle();
    this.tree = null;
    this.kick();
  }

  private startIntro() {
    this.introAt = this.still ? -1 : now();
    if (!this.still) for (const s of this.stars) s.vis = 0;
    this.t = zoomIdentity;
    select(this.el).call(this.zoomer.transform, zoomIdentity);
  }

  private resetZoom() {
    if (this.t.k === 1 && this.t.x === 0 && this.t.y === 0) return;
    this.tweenTo(zoomIdentity, (MORPH_MS + STAGGER_MS) / 1000);
  }

  /* ------------------------------------------------------------ filters, state, focus */

  refilter() {
    const { shown } = this.live.current;
    const transit = this.mode === "transit";
    for (const s of this.stars) {
      const has = transit ? s.hasT : s.hasA;
      s.want = has && shown.has(s.id) ? 1 : 0;
      if (this.still) s.vis = s.want;
    }
    this.restyle();
    this.tree = null;
    this.kick();
  }

  /** styleForNode per star, at full visibility; recomputed only when state changes. */
  private restyle(only?: (string | null)[]) {
    const L = this.live.current;
    const one = (s: Star) => {
      s.st = styleForNode(s.leaf, {
        hovered: s.id === L.hover,
        selected: s.id === L.selected,
        match: L.matches ? L.matches.has(s.id) : null,
        visible: 1,
      });
    };
    if (only) {
      for (const id of only) {
        const s = id ? this.byId.get(id) : undefined;
        if (s) one(s);
      }
    } else for (const s of this.stars) one(s);
  }

  private prevSelected: string | null = null;

  select(id: string | null) {
    this.restyle([this.prevSelected, id]);
    this.prevSelected = id;
    this.focus();
    if (!id) {
      this.kick();
      return;
    }
    if (this.busy()) {
      this.pendingFocus = id;
      return;
    }
    this.flyTo(id);
  }

  private flyTo(id: string) {
    const s = this.byId.get(id);
    if (s) {
      const k = Math.max(this.t.k, 3);
      this.tweenTo(zoomIdentity.translate(this.w / 2 - s.gx * k, this.h / 2 - s.gy * k).scale(k), 0.75);
      return;
    }
    const sec = this.mode !== "transit" ? this.sectors.find((x) => x.id === id) : undefined;
    if (sec && sec.tier === "sub") {
      const a = (sec.a0 + sec.a1) / 2;
      const px = this.cx + Math.sin(a) * this.R * 0.78;
      const py = this.cy - Math.cos(a) * this.R * 0.78;
      const k = Math.max(this.t.k, 2);
      this.tweenTo(zoomIdentity.translate(this.w / 2 - px * k, this.h / 2 - py * k).scale(k), 0.75);
      return;
    }
    this.kick();
  }

  private tweenTo(target: ZoomTransform, seconds: number) {
    const from = { x: this.t.x, y: this.t.y, k: this.t.k };
    const apply = () => select(this.el).call(this.zoomer.transform, zoomIdentity.translate(from.x, from.y).scale(from.k));
    this.tween?.kill();
    if (this.still) {
      Object.assign(from, { x: target.x, y: target.y, k: target.k });
      apply();
      return;
    }
    this.tween = gsap.to(from, { x: target.x, y: target.y, k: target.k, duration: seconds, ease: "power2.inOut", onUpdate: apply });
  }

  /** The constellation belongs to the hovered star, else the selected one. */
  private focus() {
    const L = this.live.current;
    const key = L.hover ?? L.selected;
    if (key !== this.focusKey) {
      this.focusKey = key;
      this.focusAt = this.still ? -Infinity : now();
    }
  }

  private busy() {
    return this.morphAt >= 0 || this.introAt >= 0;
  }

  /* ------------------------------------------------------------ geometry */

  private measureBox() {
    const r = this.box.getBoundingClientRect();
    this.w = Math.max(1, r.width);
    this.h = Math.max(1, r.height);
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.el.width = Math.round(this.w * this.dpr);
    this.el.height = Math.round(this.h * this.dpr);
    const margin = this.w < 560 ? 30 : 46;     // room outside the rim for the sector labels
    this.cx = this.w / 2;
    this.cy = this.h / 2;
    this.R = Math.max(40, Math.min(this.w, this.h) / 2 - margin);
  }

  private resize() {
    const w0 = this.w, h0 = this.h;
    this.measureBox();
    if (w0 === this.w && h0 === this.h) return;
    this.transFor = null;
    this.layout();
    this.place(false);
    this.kick();
  }

  /* ------------------------------------------------------------ pointer */

  private hit(e: MouseEvent): string | null {
    const r = this.el.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    if (this.busy()) return null;
    const [wx, wy] = this.t.invert([sx, sy]);
    if (!this.tree) {
      const transit = this.mode === "transit";
      this.tree = quadtree<Star>().x((s) => s.x).y((s) => s.y)
        .addAll(this.stars.filter((s) => s.want > 0 && (transit ? s.hasT : s.hasA)));
    }
    const found = this.tree.find(wx, wy, 9 / this.t.k);   // 9 screen px
    if (found) return found.id;
    if (this.mode === "transit") {
      if (!this.trans || wy > TRANSIT_HEADER) return null;
      return this.trans.columns.find((c) => wx >= c.x0 && wx < c.x1)?.id ?? null;
    }
    // the band of sector labels just outside the rim
    const ox = this.t.applyX(this.cx), oy = this.t.applyY(this.cy);
    const rim = this.R * this.t.k;
    const d = Math.hypot(sx - ox, sy - oy);
    if (d < rim + 2 || d > rim + 40) return null;
    let a = Math.atan2(sx - ox, -(sy - oy));
    if (a < 0) a += TAU;
    const tier = d < rim + 17 ? "sub" : "hub";
    return this.sectors.find((s) => s.tier === tier && a >= s.a0 && a <= s.a1)?.id
      ?? this.sectors.find((s) => s.tier === "hub" && a >= s.a0 && a <= s.a1)?.id ?? null;
  }

  private move(e: MouseEvent) {
    this.setHover(this.hit(e));
  }

  private setHover(id: string | null) {
    const L = this.live.current;
    if (L.hover === id) return;
    const prev = L.hover;
    L.hover = id;
    this.el.style.cursor = id ? "pointer" : "grab";
    this.restyle([prev, id]);
    this.focus();
    this.kick();
  }

  private click(e: MouseEvent) {
    this.live.current.onSelect(this.hit(e));
  }

  /* ------------------------------------------------------------ the loop */

  kick() {
    if (this.dead || this.raf) return;
    this.raf = requestAnimationFrame((ts) => this.frame(ts));
  }

  private frame(ts: number) {
    this.raf = 0;
    const dt = this.last ? Math.min(64, ts - this.last) : 16;
    this.last = ts;
    const tnow = now();
    let moving = false;

    // the morph
    if (this.morphAt >= 0) {
      const el = tnow - this.morphAt;
      for (const s of this.stars) {
        const e = power3InOut(clamp01((el - s.delay) / MORPH_MS));
        s.x = s.fx + (s.gx - s.fx) * e;
        s.y = s.fy + (s.gy - s.fy) * e;
        s.s = s.fs + (s.gs - s.fs) * e;
      }
      const p = power3InOut(clamp01(el / this.morphSpan));
      const transit = this.mode === "transit";
      this.chromeA = this.chromeFromA + ((transit ? 0 : 1) - this.chromeFromA) * p;
      this.chromeT = this.chromeFromT + ((transit ? 1 : 0) - this.chromeFromT) * p;
      if (el >= this.morphSpan) {
        this.morphAt = -1;
        this.tree = null;
        this.afterMotion();
      } else moving = true;
    }

    // the filter fade (held still while the intro sweep reveals the stars)
    const intro = this.introAt >= 0 ? tnow - this.introAt : Infinity;
    if (intro === Infinity) {
      const step = dt / FADE_MS;
      for (const s of this.stars) {
        if (s.vis !== s.want) {
          s.vis = s.vis < s.want ? Math.min(s.want, s.vis + step) : Math.max(s.want, s.vis - step);
          moving = true;
        }
      }
    } else if (intro > RING_MS * 0.6 + SWEEP_MS + 120) {
      this.introAt = -1;
      for (const s of this.stars) s.vis = s.want;
      this.afterMotion();
    } else moving = true;

    if (this.focusKey && tnow - this.focusAt < LINE_MS + 20) moving = true;

    this.draw(tnow, intro);
    if (moving) this.kick();
    else this.last = 0;
  }

  private afterMotion() {
    if (this.pendingFocus && !this.busy()) {
      const id = this.pendingFocus;
      this.pendingFocus = null;
      if (id === this.live.current.selected) this.flyTo(id);
    }
  }

  /* ------------------------------------------------------------ drawing */

  private draw(tnow: number, intro: number) {
    const { ctx, t } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    const ringP = intro === Infinity ? 1 : power3InOut(clamp01(intro / RING_MS));
    const sweepP = intro === Infinity ? 1 : clamp01((intro - RING_MS * 0.6) / SWEEP_MS);

    if (this.chromeA > 0.01 && this.polar) this.drawPolarChrome(this.chromeA, ringP);
    if (this.chromeT > 0.01 && this.trans) this.drawTransitChrome(this.chromeT, ringP);
    this.drawConstellation(tnow);

    // stars
    const k = t.k;
    const grow = Math.min(2.4, Math.sqrt(k));
    const sel = this.live.current.selected;
    for (const s of this.stars) {
      let fade = s.vis;
      if (sweepP < 1) fade = s.want * clamp01((sweepP * 1.25 - s.sweep) / 0.25);
      if (fade < 0.01 && s.id !== sel) continue;
      const sx = t.x + s.x * k;
      const sy = t.y + s.y * k;
      const r = s.st.r * s.s * grow;
      if (sx < -12 || sy < -12 || sx > this.w + 12 || sy > this.h + 12) continue;
      paintStar(ctx, sx, sy, r, s.st, this.pal, this.dpr, Math.max(fade, s.id === sel ? 1 : 0));
    }
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (sweepP >= 1 && this.morphAt < 0) this.drawLabels(grow);
    ctx.globalAlpha = 1;
  }

  /** Tracked letter-spacing for the mono caps, where the browser supports it. */
  private spaced(on: boolean) {
    const c = this.ctx as CanvasRenderingContext2D & { letterSpacing?: string };
    if ("letterSpacing" in c) c.letterSpacing = on ? "0.14em" : "0px";
  }

  private width(text: string, font: string, spaced: boolean): number {
    const key = `${font}|${spaced ? 1 : 0}|${text}`;
    let w = this.widths.get(key);
    if (w === undefined) {
      this.ctx.font = font;
      this.spaced(spaced);
      w = this.ctx.measureText(text).width;
      this.spaced(false);
      this.widths.set(key, w);
    }
    return w;
  }

  private drawPolarChrome(alpha: number, ringP: number) {
    const { ctx, t, pal } = this;
    const L = this.live.current;
    const ox = t.applyX(this.cx), oy = t.applyY(this.cy);
    const rim = this.R * t.k;
    const g = GRATICULE[this.family ?? "companies"];
    const start = -Math.PI / 2;
    const sweep = TAU * ringP;
    ctx.lineCap = "butt";

    // the selected sector: a faint wash over its wedge
    const selSec = L.selected ? this.sectors.find((s) => s.id === L.selected) : undefined;
    const hovSec = L.hover ? this.sectors.find((s) => s.id === L.hover) : undefined;
    for (const sec of [selSec, hovSec]) {
      if (!sec) continue;
      ctx.globalAlpha = alpha * (sec === selSec ? 0.06 : 0.035);
      ctx.fillStyle = pal.ink;
      ctx.beginPath();
      ctx.arc(ox, oy, rim, start + sec.a0, start + sec.a1);
      ctx.arc(ox, oy, rim * RMIN, start + sec.a1, start + sec.a0, true);
      ctx.closePath();
      ctx.fill();
    }

    // graticule: the score rings, then the rim a step stronger
    ctx.lineWidth = 1;
    ctx.strokeStyle = pal.line;
    ctx.globalAlpha = alpha;
    for (const s of g.rings) {
      ctx.beginPath();
      ctx.arc(ox, oy, uOf(s) * rim, start, start + sweep);
      ctx.stroke();
    }
    ctx.strokeStyle = pal.lineStrong;
    ctx.beginPath();
    ctx.arc(ox, oy, rim, start, start + sweep);
    ctx.stroke();

    // spokes between the layers, grown outward with the rings
    if (this.polar) {
      ctx.strokeStyle = pal.lineStrong;
      ctx.globalAlpha = alpha * 0.9;
      ctx.beginPath();
      for (const a of this.polar.spokes) {
        const r0 = rim * RMIN * 1.6;
        const r1 = r0 + (rim + 8 - r0) * ringP;
        ctx.moveTo(ox + Math.sin(a) * r0, oy - Math.cos(a) * r0);
        ctx.lineTo(ox + Math.sin(a) * r1, oy - Math.cos(a) * r1);
      }
      ctx.stroke();
      // a short tick at every segment boundary, just outside the rim
      ctx.strokeStyle = pal.line;
      ctx.beginPath();
      for (const s of this.sectors) {
        if (s.tier !== "sub") continue;
        ctx.moveTo(ox + Math.sin(s.a0) * rim, oy - Math.cos(s.a0) * rim);
        ctx.lineTo(ox + Math.sin(s.a0) * (rim + 5), oy - Math.cos(s.a0) * (rim + 5));
      }
      ctx.globalAlpha = alpha * ringP;
      ctx.stroke();
    }

    // tick labels up the twelve-o'clock axis, in the gap between the first and last layer
    ctx.globalAlpha = alpha * ringP;
    ctx.fillStyle = pal.ink3;
    ctx.font = `9px ${this.mono}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    for (const tk of g.ticks) {
      const y = oy - uOf(tk.s) * rim;
      if (y < 4 || y > this.h) continue;
      ctx.fillText(tk.text, ox + 4, y - 2);
    }

    // "you"
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = pal.ink;
    ctx.fillStyle = pal.ink;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(ox, oy, 2, 0, TAU);
    ctx.fill();
    ctx.beginPath();
    for (const [dx, dy] of [[0, -1], [1, 0], [0, 1], [-1, 0]] as const) {
      ctx.moveTo(ox + dx * 5, oy + dy * 5);
      ctx.lineTo(ox + dx * 9, oy + dy * 9);
    }
    ctx.stroke();
    ctx.font = `9px ${this.mono}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = pal.ink2;
    ctx.fillText("you", ox, oy + 12);

    // sector labels, tangential, outside the rim; a label that does not fit its arc is
    // shortened or left out, so two never touch
    ctx.globalAlpha = alpha * ringP;
    ctx.textBaseline = "middle";
    const hubFont = `500 9.5px ${this.mono}`;
    const subFont = `8.5px ${this.mono}`;
    for (const sec of this.sectors) {
      const hub = sec.tier === "hub";
      const rad = rim + (hub ? 30 : 13);
      const arc = (sec.a1 - sec.a0) * rad - 6;
      const a = (sec.a0 + sec.a1) / 2;
      const px = ox + Math.sin(a) * rad;
      const py = oy - Math.cos(a) * rad;
      if (px < -60 || py < -20 || px > this.w + 60 || py > this.h + 20) continue;
      const ag = L.agg.get(sec.id);
      const star = ag && ag.star_pending ? `  ★${ag.star_pending}` : "";
      const font = hub ? hubFont : subFont;
      const options = hub
        ? [`${sec.label.toUpperCase()} · ${fmt(ag?.count ?? 0)}${star}`, `${sec.label.toUpperCase()}${star}`, sec.code ?? ""]
        : [`${sec.label.toUpperCase()}${star}`, `${sec.label.split(/[ ,&]/)[0].toUpperCase()}${star}`];
      const text = options.find((o) => o && this.width(o, font, true) <= arc);
      if (!text) continue;
      const on = sec.id === L.selected || sec.id === L.hover;
      ctx.save();
      ctx.translate(px, py);
      const flip = a > Math.PI / 2 && a < (Math.PI * 3) / 2;
      ctx.rotate(flip ? a - Math.PI : a);
      ctx.font = on ? `600 ${hub ? 9.5 : 8.5}px ${this.mono}` : font;
      ctx.fillStyle = on ? pal.ink : hub ? pal.ink2 : pal.ink3;
      ctx.textAlign = "center";
      this.spaced(true);
      ctx.fillText(text, 0, 0);
      this.spaced(false);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }

  private drawTransitChrome(alpha: number, ringP: number) {
    const { ctx, t, pal } = this;
    const L = this.live.current;
    const tr = this.trans!;
    const top = t.applyY(6);
    const bottom = t.applyY(this.h - 6);
    ctx.lineWidth = 1;
    tr.columns.forEach((c: Column, i) => {
      const x0 = t.applyX(c.x0), x1 = t.applyX(c.x1);
      if (c.id === L.selected || c.id === L.hover) {
        ctx.globalAlpha = alpha * (c.id === L.selected ? 0.05 : 0.03);
        ctx.fillStyle = pal.ink;
        ctx.fillRect(x0, top, x1 - x0, bottom - top);
      }
      // the column rule, drawn down the page with the intro
      if (i > 0) {
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = pal.line;
        ctx.beginPath();
        ctx.moveTo(Math.round(x0) + 0.5, top);
        ctx.lineTo(Math.round(x0) + 0.5, top + (bottom - top) * ringP);
        ctx.stroke();
      }
      const ag = L.agg.get(c.id);
      const cw = x1 - x0 - 10;
      const cxm = x0 + 7;
      ctx.globalAlpha = alpha * ringP;
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillStyle = pal.ink;
      ctx.font = `500 ${cw < 70 ? 13 : 17}px ${this.mono}`;
      ctx.fillText(fmt(ag?.count ?? 0), cxm, t.applyY(26));
      const font = `8.5px ${this.mono}`;
      const name = c.label.toUpperCase();
      const label = [name, name.split(" ")[0], c.code ?? ""].find((o) => o && this.width(o, font, true) <= cw) ?? "";
      ctx.font = font;
      ctx.fillStyle = pal.ink3;
      this.spaced(true);
      ctx.fillText(label, cxm, t.applyY(41));
      if (ag?.star_pending) {
        ctx.fillStyle = pal.ink2;
        ctx.fillText(`★${ag.star_pending} PENDING`.slice(0, Math.max(3, Math.floor(cw / 7))), cxm, t.applyY(53));
      }
      this.spaced(false);
    });
    ctx.globalAlpha = 1;
  }

  /** The shared-investor lines of the hovered / selected star, drawn in along their length. */
  private drawConstellation(tnow: number) {
    const key = this.focusKey;
    if (!key) return;
    const from = this.byId.get(key);
    const to = this.neigh.get(key);
    if (!from || !to || from.vis < 0.5) return;
    const { ctx, t, pal } = this;
    const p = 1 - Math.pow(1 - clamp01((tnow - this.focusAt) / LINE_MS), 3);
    const ax = t.x + from.x * t.k, ay = t.y + from.y * t.k;
    ctx.strokeStyle = pal.ink2;
    ctx.lineWidth = 0.9;
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    for (const id of to) {
      const b = this.byId.get(id);
      if (!b || b.vis < 0.3) continue;
      const bx = t.x + b.x * t.k, by = t.y + b.y * t.k;
      ctx.moveTo(ax, ay);
      ctx.lineTo(ax + (bx - ax) * p, ay + (by - ay) * p);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  /** Names: the hovered / selected star gets a card; then the strongest bets (or the search
   *  hits) as plain labels, as many as the zoom allows, none overlapping. */
  private drawLabels(grow: number) {
    const { ctx, t, pal } = this;
    const L = this.live.current;
    const placed = this.placed;
    let n = 0;
    const fits = (x: number, y: number, w: number, h: number) => {
      if (x < 2 || y < 2 || x + w > this.w - 2 || y + h > this.h - 2) return false;
      for (let i = 0; i < n; i++) {
        const o = i * 4;
        if (x < placed[o] + placed[o + 2] && x + w > placed[o] && y < placed[o + 1] + placed[o + 3] && y + h > placed[o + 1]) return false;
      }
      return true;
    };
    const take = (x: number, y: number, w: number, h: number) => {
      if (n >= MAX_LABELS) return;
      const o = n * 4;
      placed[o] = x; placed[o + 1] = y; placed[o + 2] = w; placed[o + 3] = h;
      n++;
    };
    // "you" and the twelve-o'clock tick labels are reserved
    take(t.applyX(this.cx) - 14, t.applyY(this.cy) - 10, 28, 34);

    for (const id of [L.hover, L.selected]) {
      const s = id ? this.byId.get(id) : undefined;
      if (!s || (s.vis < 0.2 && id !== L.selected)) continue;
      if (id === L.selected && L.hover === L.selected) break;
      this.card(s, grow, fits, take);
    }

    const transit = this.mode === "transit";
    const k = t.k;
    const limit = transit ? (k >= 2.2 ? Math.round(5 * k) : 0) : Math.min(140, Math.round(12 * Math.pow(k, 1.6)));
    if (!limit) return;
    const font = `${LABEL_FONT} ${this.sans}`;
    ctx.font = font;
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.lineJoin = "round";
    let shown = 0;
    const matches = L.matches;
    for (let pass = matches ? 0 : 1; pass < 2 && shown < limit; pass++) {
      for (const i of this.rank) {
        if (shown >= limit) break;
        const s = this.stars[i];
        if (s.vis < 0.7 || s.id === L.hover || s.id === L.selected) continue;
        if (pass === 0 ? !matches!.has(s.id) : matches && !matches.has(s.id)) continue;
        const sx = t.x + s.x * k, sy = t.y + s.y * k;
        if (sx < 0 || sy < 0 || sx > this.w || sy > this.h) continue;
        if (!s.lw) s.lw = this.width(s.leaf.label, font, false);
        const r = s.st.r * s.s * grow;
        const h = 12;
        let x = sx + r + 4;
        if (!fits(x, sy - h / 2, s.lw, h)) {
          x = sx - r - 4 - s.lw;
          if (!fits(x, sy - h / 2, s.lw, h)) continue;
        }
        take(x, sy - h / 2, s.lw, h);
        ctx.globalAlpha = 1;
        ctx.lineWidth = 3;
        ctx.strokeStyle = pal.surface;
        ctx.strokeText(s.leaf.label, x, sy);
        ctx.fillStyle = s.leaf.status === "complete" ? pal.ink : pal.ink2;
        ctx.fillText(s.leaf.label, x, sy);
        shown++;
      }
    }
  }

  private card(
    s: Star, grow: number,
    fits: (x: number, y: number, w: number, h: number) => boolean,
    take: (x: number, y: number, w: number, h: number) => void,
  ) {
    const { ctx, t, pal } = this;
    const d = s.leaf;
    const sx = t.x + s.x * t.k, sy = t.y + s.y * t.k;
    const r = s.st.r * s.s * grow + 6;
    const name = `${d.star ? "★ " : ""}${d.label}`;
    const line2 = d.status === "complete"
      ? `complete · ${d.kind === "company" ? `bet ${Math.round(s.score)}` : d.kind}`
      : `missing ${(d.missing ?? []).map((p) => p.replace("_", " ")).join(", ") || d.feature_status || "—"}`;
    const f1 = `600 11.5px ${this.sans}`;
    const f2 = `10px ${this.mono}`;
    const w = Math.max(this.width(name, f1, false), this.width(line2, f2, false)) + 16;
    const h = 38;
    let bx = sx + r + 4;
    let by = sy - h / 2;
    if (!fits(bx, by, w, h)) bx = sx - r - 4 - w;
    bx = Math.max(4, Math.min(this.w - w - 4, bx));
    by = Math.max(4, Math.min(this.h - h - 4, by));
    take(bx, by, w, h);
    ctx.globalAlpha = 1;
    ctx.fillStyle = pal.surface;
    ctx.strokeStyle = pal.lineStrong;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(bx + 0.5, by + 0.5, w, h, 6);
    ctx.fill();
    ctx.stroke();
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.font = f1;
    ctx.fillStyle = pal.ink;
    ctx.fillText(name, bx + 8, by + 13);
    ctx.font = f2;
    ctx.fillStyle = pal.ink2;
    ctx.fillText(line2, bx + 8, by + 27);
  }
}
