"use client";

/*  The graph map, drawn to a canvas (CHOKEPOINTS §13.3).
 *
 *  4,000 companies as DOM or SVG nodes would crawl, so d3 supplies the force layout and
 *  the zoom maths and every pixel is painted here by hand. Nothing in this file decides
 *  how a node looks — that is styleForNode() in lib/graph-style.ts, which the legend
 *  calls too, so the two can never disagree.
 *
 *  Layout, in two passes:
 *    1. hubs (layers / stages) are pinned left → right, spaced by how much they hold, so
 *       the chain reads like the chokepoints map; segments settle around their hub in a
 *       few hundred synchronous ticks (40 nodes — instant) and are then pinned too;
 *    2. the leaves start AT their hub and are let go: a live simulation pulls each one to
 *       its segment (or its hub, in views without segments) while collision packs them —
 *       that settle is the opening animation.
 *
 *  Level of detail: at the fitted zoom only hubs and segments draw, with their counts;
 *  company dots fade in past a zoom threshold (views without segments show them at once,
 *  since there the hubs ARE the clusters). Labels: hubs always; segments once there is
 *  room; companies only when hovered, selected, or among the top by priority up close.
 *
 *  Motion: the settle; a smooth zoom-to-node on select (GSAP over centre + scale); every
 *  filter change fades and scales nodes in and out over 350 ms; a slow pulse on pending
 *  rings and roll-up rings. Under prefers-reduced-motion: no pulse, no settle on screen
 *  (the layout is ticked off-screen and appears whole), and zoom-to-node jumps.
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { forceCollide, forceSimulation, forceX, forceY, type Simulation, type SimulationNodeDatum } from "d3-force";
import { quadtree, type Quadtree } from "d3-quadtree";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { gsap } from "gsap";
import {
  DEFAULT_STATE, paintNode, readPalette, screenRadius, styleForNode,
  type Agg, type GraphMap, type MapNode, type NodeState, type Palette,
} from "@/lib/graph-style";

type SimNode = SimulationNodeDatum & {
  id: string;
  tier: "hub" | "sub" | "leaf";
  parent: string | null;          // a leaf's segment (or hub); a segment's hub
  hub: string | null;
  ext: number;                    // a hub's cluster radius (world), for spacing and labels
  rc: number;                     // collision radius (world)
  vis: number;                    // current filter fade, tweened toward `want`
  want: number;
  priority: number;
};

const FADE_MS = 350;
const LEAF_PAD = 1.1;
const clusterR = (n: number) => 4.3 * Math.sqrt(n) + 10;
const smooth = (a: number, b: number, x: number) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const fmt = (n: number) => n.toLocaleString("en-US");

export type CanvasProps = {
  data: GraphMap;
  shown: Set<string>;                 // leaf ids the filters let through
  matches: Set<string> | null;        // search hits, null when no search
  agg: Map<string, Agg>;              // filtered roll-up per hub / segment
  selected: string | null;
  onSelect: (id: string | null) => void;
};

export function GraphCanvas({ data, shown, matches, agg, selected, onSelect }: CanvasProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  // Everything the draw loop reads lives in one ref, so a React render never restarts
  // the simulation and the loop never closes over stale props.
  const live = useRef({
    data, shown, matches, agg, selected, onSelect,
    hover: null as string | null,
  });
  // synced before any effect below reads it (layout effects run first)
  useLayoutEffect(() => {
    Object.assign(live.current, { data, shown, matches, agg, selected, onSelect });
  });

  const engine = useRef<Engine | null>(null);
  const snap = useRef<Snap | null>(null);

  /* One engine per payload. A reload of the same view (after "Fill now", or a tick)
     keeps where everything was and the zoom, and only re-settles gently; a view
     change lays out from scratch. */
  useEffect(() => {
    const el = canvas.current;
    const box = wrap.current;
    if (!el || !box) return;
    const eng = new Engine(el, box, live, snap.current);
    engine.current = eng;
    return () => {
      snap.current = eng.snapshot();
      eng.destroy();
      engine.current = null;
    };
  }, [data]);

  /* Filters and search: fade, never re-layout. */
  useEffect(() => {
    engine.current?.refilter();
  }, [shown, matches, agg]);

  useEffect(() => {
    engine.current?.focusOn(selected);
  }, [selected]);

  return (
    <div ref={wrap} className="absolute inset-0">
      <canvas
        ref={canvas}
        className="block h-full w-full touch-none select-none"
        role="img"
        aria-label={`Graph map: ${data.hubs.length} hubs, ${data.subs.length} segments, ${fmt(data.leaves.length)} nodes. ` +
          `${fmt(data.counts.complete)} complete, ${fmt(data.counts.filling)} still filling.`}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- the engine
   A plain class, not hooks: it owns a simulation, a zoom behaviour, observers and a
   requestAnimationFrame loop, and tears every one of them down in destroy(). */

type Snap = { view: string; pos: Map<string, [number, number]>; t: ZoomTransform };

type Live = RefObject<{
  data: GraphMap; shown: Set<string>; matches: Set<string> | null; agg: Map<string, Agg>;
  selected: string | null; onSelect: (id: string | null) => void; hover: string | null;
}>;

class Engine {
  private ctx: CanvasRenderingContext2D;
  private nodes: SimNode[] = [];
  private byId = new Map<string, SimNode>();
  private model = new Map<string, MapNode>();
  private neigh = new Map<string, Set<string>>();
  private sim: Simulation<SimNode, undefined> | null = null;
  private zoomer: ZoomBehavior<HTMLCanvasElement, unknown>;
  private t: ZoomTransform = zoomIdentity;
  private fitK = 1;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private pal: Palette;
  private font = "ui-sans-serif, system-ui, sans-serif";
  private mono = "ui-monospace, monospace";
  private raf = 0;
  private last = 0;
  private settled = false;
  private hasSubs: boolean;
  private tree: Quadtree<SimNode> | null = null;
  private treeAt = -1;
  private ticks = 0;
  private ro: ResizeObserver;
  private mo: MutationObserver;
  private tween: gsap.core.Tween | null = null;
  private pendingFocus: string | null = null;
  private fitted = false;
  private still = reduced();
  private dead = false;

  private warm: Snap | null;

  constructor(private el: HTMLCanvasElement, private box: HTMLDivElement, private live: Live, prev: Snap | null) {
    this.warm = prev && prev.view === live.current.data.view ? prev : null;
    this.ctx = el.getContext("2d")!;
    this.pal = readPalette();
    const cs = getComputedStyle(document.body);
    this.font = cs.fontFamily || this.font;
    this.mono = getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || this.mono;
    this.hasSubs = live.current.data.subs.length > 0;
    this.build();

    this.zoomer = d3zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.05, 40])
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
    this.resize();
    this.pendingFocus = live.current.selected;
    this.start();
  }

  snapshot(): Snap {
    const pos = new Map<string, [number, number]>();
    for (const n of this.nodes) if (n.tier === "leaf") pos.set(n.id, [n.x!, n.y!]);
    return { view: this.live.current.data.view, pos, t: this.t };
  }

  destroy() {
    this.dead = true;
    cancelAnimationFrame(this.raf);
    this.sim?.stop();
    this.tween?.kill();
    this.ro.disconnect();
    this.mo.disconnect();
    select(this.el).on(".zoom", null).on(".map", null);
  }

  /* ------------------------------------------------------------ layout */

  private build() {
    const { data } = this.live.current;
    const leavesUnder = new Map<string, number>();
    for (const l of data.leaves) {
      const p = l.sub || l.hub;
      leavesUnder.set(p, (leavesUnder.get(p) ?? 0) + 1);
    }
    const subsOf = new Map<string, typeof data.subs>();
    for (const s of data.subs) subsOf.set(s.hub!, [...(subsOf.get(s.hub!) ?? []), s]);

    // hubs: row 0 is the chain, spaced by what each holds; row 1 (project rules) sits below
    const row0 = data.hubs.filter((h) => (h.row ?? 0) === 0);
    const row1 = data.hubs.filter((h) => (h.row ?? 0) === 1);
    const hubR = (count: number) => 14 + Math.sqrt(count) * 0.9;
    const ext = (id: string, count: number) => {
      const subs = subsOf.get(id) ?? [];
      if (subs.length) {
        const area = subs.reduce((a, s) => a + (clusterR(s.count) + 8) ** 2, 0);
        return Math.sqrt(area) * 1.25 + hubR(count);
      }
      return clusterR(leavesUnder.get(id) ?? 0) * 1.08 + hubR(count) + 6;
    };
    let x = 0;
    let maxExt = 0;
    row0.forEach((h, i) => {
      const e = ext(h.id, h.count);
      if (i > 0) x += e;
      this.add({ id: h.id, tier: "hub", parent: null, hub: h.id, ext: e, rc: hubR(h.count) + 6, x, y: 0, fx: x, fy: 0 });
      x += e + 70;
      maxExt = Math.max(maxExt, e);
    });
    const width = Math.max(x - 70, 1);
    row1.forEach((h, i) => {
      const hx = row1.length > 1 ? (i / (row1.length - 1)) * width : width / 2;
      const hy = maxExt + 170 + (i % 2) * 46;
      this.add({ id: h.id, tier: "hub", parent: null, hub: h.id, ext: 20, rc: hubR(h.count) + 6, x: hx, y: hy, fx: hx, fy: hy });
    });
    for (const h of data.hubs) this.model.set(h.id, { tier: "hub", id: h.id, data: h, agg: aggOf(h) });

    // segments: settle around their hub, then pin
    if (data.subs.length) {
      data.subs.forEach((s, i) => {
        const hub = this.byId.get(s.hub!)!;
        const a = i * 2.39996;
        this.add({
          id: s.id, tier: "sub", parent: s.hub!, hub: s.hub!, ext: 0,
          rc: clusterR(s.count) + 5, x: hub.x! + Math.cos(a) * 40, y: hub.y! + Math.sin(a) * 40,
        });
        this.model.set(s.id, { tier: "sub", id: s.id, data: s, agg: aggOf(s) });
      });
      const pin = forceSimulation<SimNode>(this.nodes.slice())
        .force("x", forceX<SimNode>((n) => (n.parent ? this.byId.get(n.parent)!.x! : n.x!)).strength(0.08))
        .force("y", forceY<SimNode>((n) => (n.parent ? this.byId.get(n.parent)!.y! : n.y!)).strength(0.08))
        .force("collide", forceCollide<SimNode>((n) => n.rc).iterations(3))
        .stop();
      for (let i = 0; i < 320; i++) pin.tick();
      for (const n of this.nodes) if (n.tier === "sub") { n.fx = n.x; n.fy = n.y; }
    }

    // leaves: born at their hub (the ease-in), pulled to their segment
    data.leaves.forEach((l, i) => {
      const hub = this.byId.get(l.hub);
      if (!hub) return;
      const a = i * 2.39996;
      const j = 3 + (i % 7);
      this.add({
        id: l.id, tier: "leaf", parent: l.sub && this.byId.has(l.sub) ? l.sub : l.hub, hub: l.hub, ext: 0,
        rc: 2.4 + Math.max(0, Math.min(100, l.priority ?? 0)) / 100 * 1.4 + LEAF_PAD,
        x: hub.x! + Math.cos(a) * j, y: hub.y! + Math.sin(a) * j, priority: l.priority ?? 0,
      });
      this.model.set(l.id, { tier: "leaf", id: l.id, data: l });
    });

    // neighbourhoods, for hover: parent links, chain / overlap links, investor and rule links
    const link = (a: string, b: string) => {
      if (!this.byId.has(a) || !this.byId.has(b)) return;
      if (!this.neigh.has(a)) this.neigh.set(a, new Set());
      if (!this.neigh.has(b)) this.neigh.set(b, new Set());
      this.neigh.get(a)!.add(b);
      this.neigh.get(b)!.add(a);
    };
    for (const n of this.nodes) {
      if (n.parent) link(n.id, n.parent);
      if (n.tier === "leaf" && n.hub && n.hub !== n.parent) link(n.id, n.hub);
    }
    for (const l of data.links) link(l.source, l.target);
    this.refilter(true);
  }

  private add(n: Omit<SimNode, "vis" | "want" | "priority"> & { priority?: number }) {
    const node: SimNode = { vis: 0, want: 1, priority: 0, ...n };
    this.nodes.push(node);
    this.byId.set(node.id, node);
  }

  private start() {
    const leaves = this.nodes;
    const sim = forceSimulation<SimNode>(leaves)
      .force("x", forceX<SimNode>((n) => (n.parent ? this.byId.get(n.parent)!.x! : n.x!)).strength(0.07))
      .force("y", forceY<SimNode>((n) => (n.parent ? this.byId.get(n.parent)!.y! : n.y!)).strength(0.07))
      .force("collide", forceCollide<SimNode>((n) => n.rc).iterations(1).strength(0.9))
      .alphaDecay(0.028)
      .velocityDecay(0.38)
      .stop();
    this.sim = sim;
    if (this.warm) {
      // same view, fresh data: start from where every node already was, settle briefly
      for (const n of this.nodes) {
        const p = n.tier === "leaf" ? this.warm.pos.get(n.id) : undefined;
        if (p) { n.x = p[0]; n.y = p[1]; n.vis = n.want; }
      }
      sim.alpha(0.12);
    }
    if (this.still) {
      // reduced motion: the layout is computed off-screen and appears whole
      const chunk = () => {
        if (this.dead) return;
        for (let i = 0; i < 30 && sim.alpha() > sim.alphaMin(); i++) sim.tick();
        this.ticks++;
        if (sim.alpha() > sim.alphaMin()) requestAnimationFrame(chunk);
        else this.settle();
        this.kick();
      };
      requestAnimationFrame(chunk);
    }
    this.kick();
  }

  private settle() {
    this.settled = true;
    for (const n of this.nodes) if (n.tier === "leaf") n.vis = n.want;
    if (this.pendingFocus) {
      const id = this.pendingFocus;
      this.pendingFocus = null;
      this.focusOn(id);
    }
  }

  /* ------------------------------------------------------------ view */

  private resize() {
    const r = this.box.getBoundingClientRect();
    this.w = Math.max(1, r.width);
    this.h = Math.max(1, r.height);
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.el.width = Math.round(this.w * this.dpr);
    this.el.height = Math.round(this.h * this.dpr);
    const k0 = this.fitK;
    this.fitK = this.fit();
    if (!this.fitted && this.warm) {
      this.fitted = true;
      this.zoomer.scaleExtent([this.fitK * 0.5, this.fitK * 24]);
      select(this.el).call(this.zoomer.transform, this.warm.t);
    } else if (!this.fitted) {
      this.fitted = true;
      const b = this.bounds();
      const k = this.fitK;
      const tr = zoomIdentity.translate(this.w / 2 - ((b.x0 + b.x1) / 2) * k, this.h / 2 - ((b.y0 + b.y1) / 2) * k).scale(k);
      this.zoomer.scaleExtent([this.fitK * 0.5, this.fitK * 24]);
      select(this.el).call(this.zoomer.transform, tr);
    } else if (k0 !== this.fitK) {
      this.zoomer.scaleExtent([this.fitK * 0.5, this.fitK * 24]);
    }
    this.kick();
  }

  private bounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes) {
      if (n.tier !== "hub") continue;
      const e = n.ext + 10;
      x0 = Math.min(x0, n.x! - e); x1 = Math.max(x1, n.x! + e);
      y0 = Math.min(y0, n.y! - e - 46); y1 = Math.max(y1, n.y! + e);
    }
    if (!Number.isFinite(x0)) return { x0: -100, y0: -100, x1: 100, y1: 100 };
    return { x0, y0, x1, y1 };
  }

  private fit() {
    const b = this.bounds();
    return Math.max(0.02, Math.min(this.w / (b.x1 - b.x0), this.h / (b.y1 - b.y0)) * 0.92);
  }

  private lod() {
    const k = this.t.k / this.fitK;
    return this.hasSubs ? smooth(1.45, 2.5, k) : smooth(0.45, 0.85, k);
  }

  /* ------------------------------------------------------------ filters, focus */

  refilter(initial = false) {
    const { shown, agg } = this.live.current;
    for (const n of this.nodes) {
      if (n.tier === "leaf") n.want = shown.has(n.id) ? 1 : 0;
      else {
        const m = this.model.get(n.id);
        if (m && m.tier !== "leaf") m.agg = agg.get(n.id) ?? { count: 0, complete: 0, pending: 0, star_pending: 0 };
        n.want = 1;
        if (initial) n.vis = 1;
      }
      if (this.still) n.vis = n.want;
    }
    this.tree = null;
    this.kick();
  }

  focusOn(id: string | null) {
    if (!id) {
      this.kick();
      return;
    }
    const n = this.byId.get(id);
    if (!n) return;
    if (n.tier === "leaf" && !this.settled) {
      this.pendingFocus = id;
      return;
    }
    const k = Math.max(this.t.k, this.fitK * (n.tier === "leaf" ? 5 : n.tier === "sub" ? 3 : 1.6));
    const target = { cx: n.x!, cy: n.y!, k };
    const from = { cx: (this.w / 2 - this.t.x) / this.t.k, cy: (this.h / 2 - this.t.y) / this.t.k, k: this.t.k };
    const apply = () => {
      const tr = zoomIdentity.translate(this.w / 2 - from.cx * from.k, this.h / 2 - from.cy * from.k).scale(from.k);
      select(this.el).call(this.zoomer.transform, tr);
    };
    this.tween?.kill();
    if (this.still) {
      Object.assign(from, target);
      apply();
      return;
    }
    this.tween = gsap.to(from, { ...target, duration: 0.75, ease: "power2.inOut", onUpdate: apply });
  }

  /* ------------------------------------------------------------ pointer */

  private world(e: MouseEvent): [number, number, number, number] {
    const r = this.el.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    const [wx, wy] = this.t.invert([sx, sy]);
    return [sx, sy, wx, wy];
  }

  private hit(e: MouseEvent): string | null {
    const [sx, sy, wx, wy] = this.world(e);
    // hubs and segments first — few, large, always drawn
    let best: string | null = null;
    let bestD = Infinity;
    for (const n of this.nodes) {
      if (n.tier === "leaf") continue;
      const m = this.model.get(n.id)!;
      const r = screenRadius(m, styleForNode(m, DEFAULT_STATE), this.t.k) + 3;
      const d = Math.hypot(this.t.applyX(n.x!) - sx, this.t.applyY(n.y!) - sy);
      if (d <= r && d < bestD) { best = n.id; bestD = d; }
    }
    if (best) return best;
    if (this.lod() < 0.3) return null;
    if (!this.tree || this.treeAt !== this.ticks) {
      this.tree = quadtree<SimNode>().x((n) => n.x!).y((n) => n.y!)
        .addAll(this.nodes.filter((n) => n.tier === "leaf" && n.want > 0));
      this.treeAt = this.ticks;
    }
    const found = this.tree.find(wx, wy, Math.max(6, 8 / this.t.k));
    return found ? found.id : null;
  }

  private move(e: MouseEvent) {
    this.setHover(this.hit(e));
  }

  private setHover(id: string | null) {
    if (this.live.current.hover === id) return;
    this.live.current.hover = id;
    this.el.style.cursor = id ? "pointer" : "grab";
    this.kick();
  }

  private click(e: MouseEvent) {
    const id = this.hit(e);
    this.live.current.onSelect(id);
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
    let busy = false;

    if (this.sim && !this.still && !this.settled) {
      this.sim.tick();
      this.ticks++;
      if (this.sim.alpha() <= this.sim.alphaMin() + 0.004) this.settle();
      else busy = true;
    }
    const step = dt / FADE_MS;
    for (const n of this.nodes) {
      if (n.vis !== n.want) {
        n.vis = n.vis < n.want ? Math.min(n.want, n.vis + step) : Math.max(n.want, n.vis - step);
        busy = true;
      }
    }
    const pulsing = this.draw(ts);
    if (busy) this.kick();
    else if (pulsing && !this.still) {
      // the pulse is slow; ~20 fps is plenty and spares the laptop
      setTimeout(() => this.kick(), 50);
    } else this.last = 0;
  }

  /** Paint everything. Returns whether a pulse is on screen (keeps the loop alive). */
  private draw(ts: number): boolean {
    const { ctx, t, pal } = this;
    const L = this.live.current;
    const lod = this.lod();
    const pulse = this.still ? 0 : (1 - Math.cos((ts / 2600) * Math.PI * 2)) / 2;
    // the selection keeps its wires lit; only hovering dims the rest of the map
    const focusId = L.hover ?? L.selected;
    const dimming = L.hover !== null;
    const near = L.hover ? this.neigh.get(L.hover) ?? new Set<string>() : null;
    const matches = L.matches;

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);

    const stateOf = (n: SimNode): NodeState => {
      let focus: NodeState["focus"] = "none";
      if (L.hover && near) {
        const hubFocus = this.byId.get(L.hover)?.tier === "hub";
        const inHood = n.id === L.hover || near.has(n.id) || (hubFocus && n.hub === L.hover);
        focus = inHood ? "in" : "out";
      }
      return {
        hovered: n.id === L.hover,
        selected: n.id === L.selected,
        focus,
        match: matches && n.tier === "leaf" ? matches.has(n.id) : null,
        visible: n.vis,
        // the selected, the hovered and every search hit show at any zoom
        lod: n.tier === "leaf" ? (n.id === L.selected || n.id === L.hover || matches?.has(n.id) ? 1 : lod) : 1,
        pulse,
      };
    };

    const X = (n: SimNode) => t.applyX(n.x!);
    const Y = (n: SimNode) => t.applyY(n.y!);
    const onScreen = (x: number, y: number, r: number) => x > -r && y > -r && x < this.w + r && y < this.h + r;

    // ---- links
    ctx.lineCap = "round";
    for (const l of L.data.links) {
      const a = this.byId.get(l.source);
      const b = this.byId.get(l.target);
      if (!a || !b) continue;
      const lit = !!focusId && (l.source === focusId || l.target === focusId);
      const dim = dimming && !lit;
      if (l.kind === "chain") {
        const ma = this.model.get(a.id)!;
        const mb = this.model.get(b.id)!;
        const ra = screenRadius(ma, styleForNode(ma, DEFAULT_STATE), t.k) + 4;
        const rb = screenRadius(mb, styleForNode(mb, DEFAULT_STATE), t.k) + 8;
        const ax = X(a), ay = Y(a), bx = X(b), by = Y(b);
        const len = Math.hypot(bx - ax, by - ay) || 1;
        const ux = (bx - ax) / len, uy = (by - ay) / len;
        const x0 = ax + ux * ra, y0 = ay + uy * ra, x1 = bx - ux * rb, y1 = by - uy * rb;
        ctx.globalAlpha = dim ? 0.25 : 0.85;
        ctx.strokeStyle = pal.ink3;
        ctx.lineWidth = 1 + Math.min(3, Math.log10(1 + l.weight));
        ctx.setLineDash(this.still ? [] : [6, 6]);
        ctx.lineDashOffset = this.still ? 0 : -((ts / 60) % 12);
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        ctx.setLineDash([]);
        // arrowhead
        ctx.fillStyle = pal.ink3;
        ctx.beginPath();
        ctx.moveTo(x1 + ux * 6, y1 + uy * 6);
        ctx.lineTo(x1 - uy * 4, y1 + ux * 4);
        ctx.lineTo(x1 + uy * 4, y1 - ux * 4);
        ctx.fill();
        if (L.data.view === "pipeline" && l.weight) {
          ctx.globalAlpha = dim ? 0.3 : 1;
          ctx.font = `10px ${this.mono}`;
          ctx.fillStyle = pal.ink2;
          ctx.textAlign = "center";
          ctx.fillText(`${fmt(l.weight)} got past`, (x0 + x1) / 2, (y0 + y1) / 2 - 7);
        }
        continue;
      }
      if (l.kind === "overlap") {
        ctx.globalAlpha = lit ? 0.7 : dim ? 0.04 : 0.14;
        ctx.strokeStyle = pal.ink3;
        ctx.lineWidth = Math.min(3, 0.6 + Math.log10(l.weight));
        const ax = X(a), ay = Y(a), bx = X(b), by = Y(b);
        const mx = (ax + bx) / 2 - (by - ay) * 0.12, my = (ay + by) / 2 + (bx - ax) * 0.12;
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo(mx, my, bx, by); ctx.stroke();
        continue;
      }
      // investor (company ↔ company) and rule links (feature → constraint): leaf-level detail
      const vis = Math.min(a.vis, b.vis);
      const show = lit ? 1 : lod * vis;
      if (show < 0.03) continue;
      ctx.globalAlpha = (lit ? 0.85 : dim ? 0.04 : 0.22) * show;
      ctx.strokeStyle = pal.ink2;
      ctx.lineWidth = lit ? 1.2 : 0.7;
      ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(X(a), Y(a)); ctx.lineTo(X(b), Y(b)); ctx.stroke();
      ctx.setLineDash([]);
    }

    // the focused leaf's own wires to its segment and hub
    const fn = focusId ? this.byId.get(focusId) : null;
    if (fn && fn.tier === "leaf") {
      ctx.globalAlpha = 0.6;
      ctx.strokeStyle = pal.ink;
      ctx.lineWidth = 1;
      for (const p of [fn.parent, fn.hub]) {
        const pn = p ? this.byId.get(p) : null;
        if (!pn || pn === fn) continue;
        ctx.beginPath(); ctx.moveTo(X(fn), Y(fn)); ctx.lineTo(X(pn), Y(pn)); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // ---- nodes: leaves under, then segments, then hubs
    let pulsing = false;
    const labels: { n: SimNode; m: MapNode; x: number; y: number; r: number; st: ReturnType<typeof styleForNode> }[] = [];
    for (const tier of ["leaf", "sub", "hub"] as const) {
      for (const n of this.nodes) {
        if (n.tier !== tier) continue;
        const m = this.model.get(n.id)!;
        const s = stateOf(n);
        if (tier === "leaf" && ((s.lod * n.vis < 0.01 && !s.selected) || (this.still && !this.settled))) continue;
        const st = styleForNode(m, s);
        const r = screenRadius(m, st, t.k);
        const x = X(n), y = Y(n);
        if (!onScreen(x, y, r + 12)) continue;
        paintNode(ctx, x, y, r, st, pal);
        if (st.ringAlpha < 1 || st.haloAlpha < 1 || (m.tier === "leaf" && m.data.status !== "complete")
          || (m.tier !== "leaf" && m.agg.star_pending > 0)) pulsing = true;
        const focused = st.label === "focus" && s.focus === "in" && focusId !== null && tier !== "leaf";
        if (st.label === "always" || focused || (tier === "sub" && r >= 7)) labels.push({ n, m, x, y, r, st });
      }
    }

    // top leaves by priority, labelled only up close
    if (lod >= 1 && t.k / this.fitK > 5) {
      const top = this.nodes
        .filter((n) => n.tier === "leaf" && n.vis > 0.5 && onScreen(X(n), Y(n), 0))
        .sort((a, b) => b.priority - a.priority)
        .slice(0, 28);
      for (const n of top) {
        if (labels.some((l) => l.n === n)) continue;
        const m = this.model.get(n.id)!;
        const st = styleForNode(m, stateOf(n));
        labels.push({ n, m, x: X(n), y: Y(n), r: screenRadius(m, st, t.k), st });
      }
    }

    // ---- labels last, so nothing paints over them
    for (const { n, m, x, y, r, st } of labels) {
      const alpha = Math.max(0.15, st.alpha);
      if (m.tier === "leaf") this.leafLabel(m, x, y, r, n.id === focusId);
      else if (m.tier === "hub") this.hubLabel(n, m, x, y, r, alpha);
      else this.subLabel(m, x, y, r, alpha, lod);
    }
    ctx.globalAlpha = 1;
    return pulsing && (lod > 0.05 || this.nodes.some((n) => n.tier !== "leaf"));
  }

  private text(s: string, x: number, y: number, font: string, color: string, align: CanvasTextAlign = "center", halo = true) {
    const { ctx } = this;
    ctx.font = font;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    if (halo) {
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = this.pal.surface;
      ctx.lineJoin = "round";
      ctx.strokeText(s, x, y);
    }
    ctx.fillStyle = color;
    ctx.fillText(s, x, y);
  }

  private hubLabel(n: SimNode, m: Extract<MapNode, { tier: "hub" | "sub" }>, x: number, y: number, r: number, alpha: number) {
    const { ctx, pal } = this;
    ctx.globalAlpha = Math.max(0.3, alpha);
    const a = m.agg;
    // the count inside the hub when there is room
    if (r >= 15) this.text(fmt(a.count), x, y, `600 ${Math.min(15, r * 0.55)}px ${this.font}`, pal.ink, "center", false);
    const top = (m.data.row ?? 0) === 1 ? y + r + 14 : y - Math.max(r + 12, n.ext * this.t.k + 10);
    if ((m.data.row ?? 0) === 1) {
      this.text(m.data.label.length > 28 ? `${m.data.label.slice(0, 27)}…` : m.data.label, x, top, `500 10.5px ${this.font}`, pal.ink2);
      return;
    }
    this.text(`${m.data.code ?? ""}`, x, top - 30, `9.5px ${this.mono}`, pal.ink3);
    this.text(m.data.label, x, top - 16, `600 13px ${this.font}`, pal.ink);
    const parts = [`${fmt(a.complete)} complete`, `${fmt(a.pending)} filling`];
    if (a.star_pending) parts.push(`★ ${a.star_pending} pending`);
    this.text(parts.join(" · "), x, top - 2, `10px ${this.mono}`, pal.ink2);
  }

  private subLabel(m: Extract<MapNode, { tier: "hub" | "sub" }>, x: number, y: number, r: number, alpha: number, lod: number) {
    const { pal } = this;
    this.ctx.globalAlpha = Math.max(0.25, alpha);
    const a = m.agg;
    const yy = lod > 0.5 ? y : y + r + 9;
    this.text(m.data.label, x, yy, `500 11px ${this.font}`, pal.ink);
    this.text(`${fmt(a.count)} · ${fmt(a.pending)} filling${a.star_pending ? ` · ★${a.star_pending}` : ""}`, x, yy + 12,
      `9.5px ${this.mono}`, pal.ink2);
  }

  private leafLabel(m: Extract<MapNode, { tier: "leaf" }>, x: number, y: number, r: number, focused: boolean) {
    const { ctx, pal } = this;
    const d = m.data;
    ctx.globalAlpha = 1;
    const name = `${d.star ? "★ " : ""}${d.label}`;
    if (!focused) {
      this.text(name, x + r + 4, y, `500 10.5px ${this.font}`, pal.ink, "left");
      return;
    }
    // the hovered / selected one gets a card: name and what is missing
    const line2 = d.status === "complete"
      ? "complete"
      : `missing ${(d.missing ?? []).map((p) => p.replace("_", " ")).join(", ") || d.feature_status || "—"}`;
    ctx.font = `600 11.5px ${this.font}`;
    const w1 = ctx.measureText(name).width;
    ctx.font = `10px ${this.mono}`;
    const w2 = ctx.measureText(line2).width;
    const w = Math.max(w1, w2) + 16;
    let bx = x + r + 8;
    if (bx + w > this.w - 4) bx = x - r - 8 - w;
    const by = y - 19;
    ctx.fillStyle = pal.surface;
    ctx.strokeStyle = pal.lineStrong;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(bx, by, w, 38, 7);
    ctx.fill();
    ctx.stroke();
    this.text(name, bx + 8, by + 13, `600 11.5px ${this.font}`, pal.ink, "left", false);
    this.text(line2, bx + 8, by + 27, `10px ${this.mono}`, pal.ink2, "left", false);
  }
}

function aggOf(b: { count: number; complete: number; pending: number; star_pending: number }): Agg {
  return { count: b.count, complete: b.complete, pending: b.pending, star_pending: b.star_pending };
}
