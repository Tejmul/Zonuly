"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Minus, Plus, Scan } from "lucide-react";

export type NodeT = {
  id: string; layer: string; label: string; note: string | null;
  count: number; bets: number; near: number; leads: number; fresher: number; anywhere: number; roles: number;
  chokepoint: boolean; bottleneck: boolean; company_ids: number[];
};
export type StageT = { key: string; code: string; label: string; blurb: string; nodes: number };
export type EdgeT = { source: string; target: string; weight: number };

/* Layout constants. The canvas is drawn once in its own coordinate space and then
   fitted by the viewBox, so it never clips and never needs a horizontal scrollbar —
   the whole map is always on screen, which is the entire point of a map. */
const COL = 236;
const NUM_GUTTER = 30;   // reserved for the count, so labels can never collide
const PAD_X = 128;
const HUB_Y = 74;
const TOP = 168;
const ROW = 26;
const CAP = 14;

type Placed = NodeT & { x: number; y: number; r: number };

export function AtlasCanvas({
  stages, nodes, edges, mode, query, picked, onToggle,
}: {
  stages: StageT[]; nodes: NodeT[]; edges: EdgeT[];
  mode: "layers" | "chokepoints" | "bottlenecks"; query: string;
  picked: string[]; onToggle: (id: string) => void;
}) {
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const q = query.trim().toLowerCase();
  const pickedSet = new Set(picked);
  const focus = hover ?? (picked.length === 1 ? picked[0] : null);

  const { placed, byId, width, height, hubs } = useMemo(() => {
    const maxCount = Math.max(1, ...nodes.map((n) => n.count));
    const placed: Placed[] = [];
    const hubs: { code: string; label: string; blurb: string; key: string; x: number; hidden: number }[] = [];
    let tallest = 0;

    stages.forEach((stage, i) => {
      const all = nodes.filter((n) => n.layer === stage.key);
      const open = expanded[stage.key];
      const shown = open ? all : all.slice(0, CAP);
      const x = PAD_X + i * COL;
      hubs.push({ ...stage, x, hidden: all.length - shown.length });
      shown.forEach((n, j) => {
        placed.push({
          ...n,
          x,
          y: TOP + j * ROW,
          // area, not diameter, tracks the count — a 250-company node should not be
          // fifty times the ink of a 5-company one
          r: 3 + Math.sqrt(n.count / maxCount) * 7,
        });
      });
      tallest = Math.max(tallest, shown.length);
    });

    const byId = new Map(placed.map((p) => [p.id, p]));
    return {
      placed, byId, hubs,
      width: PAD_X * 2 + (stages.length - 1) * COL,
      height: TOP + tallest * ROW + 18,
    };
  }, [stages, nodes, expanded]);

  const dim = useCallback(
    (n: NodeT) =>
      (mode === "chokepoints" && !n.chokepoint) ||
      (mode === "bottlenecks" && !n.bottleneck) ||
      (q.length > 1 && !n.label.toLowerCase().includes(q)),
    [mode, q],
  );

  const lines = useMemo(
    () =>
      edges.flatMap((e) => {
        const a = byId.get(e.source);
        const b = byId.get(e.target);
        if (!a || !b) return [];
        const on = focus === e.source || focus === e.target;
        // left-to-right always, so the S-curve leaves the node on the correct side
        const [l, r] = a.x <= b.x ? [a, b] : [b, a];
        const dx = Math.max(60, (r.x - l.x) * 0.62);
        const same = l.x === r.x;
        return [{
          key: `${e.source}|${e.target}`,
          d: same
            ? `M${l.x + l.r + 3},${l.y} C${l.x + 40},${l.y} ${l.x + 40},${r.y} ${r.x + r.r + 3},${r.y}`
            : `M${l.x + l.r + 3},${l.y} C${l.x + dx},${l.y} ${r.x - dx},${r.y} ${r.x - r.r - 3},${r.y}`,
          on,
          w: Math.min(3, 0.5 + Math.log2(e.weight)),
        }];
      }),
    [edges, byId, focus],
  );

  const zoom = (dk: number) => setView((v) => ({ ...v, k: Math.min(3, Math.max(0.5, v.k + dk)) }));
  const fit = () => setView({ k: 1, x: 0, y: 0 });

  return (
    <div className="plate relative overflow-hidden">
      <span className="tick" />

      <div className="absolute top-3 right-3 z-10 flex gap-1">
        {[
          { icon: <Plus size={13} />, fn: () => zoom(0.25), label: "Zoom in" },
          { icon: <Minus size={13} />, fn: () => zoom(-0.25), label: "Zoom out" },
          { icon: <Scan size={13} />, fn: fit, label: "Fit" },
        ].map((b) => (
          <button
            key={b.label}
            onClick={b.fn}
            aria-label={b.label}
            className="grid h-7 w-7 cursor-pointer place-items-center rounded border border-line
              bg-surface text-ink-2 transition-colors hover:text-ink"
          >
            {b.icon}
          </button>
        ))}
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="graticule block w-full cursor-grab touch-none select-none active:cursor-grabbing"
        style={{ height: "clamp(420px, 62vh, 720px)" }}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
          (e.target as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          // read the drag origin now: the state updater runs later, and pointer-up
          // may have cleared the ref by then (the "reading 'vx' of null" crash)
          const d = drag.current;
          if (!d) return;
          const s = width / e.currentTarget.getBoundingClientRect().width;
          const { clientX, clientY } = e;
          setView((v) => ({
            ...v,
            x: d.vx + (clientX - d.x) * s,
            y: d.vy + (clientY - d.y) * s,
          }));
        }}
        onPointerUp={() => { drag.current = null; }}
        onPointerLeave={() => { drag.current = null; setHover(null); }}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {/* links first: they are the ground the nodes sit on */}
          <g fill="none">
            {lines.map((l) => (
              <path
                key={l.key}
                d={l.d}
                stroke={l.on ? "var(--ink)" : "var(--line-strong)"}
                strokeWidth={l.on ? l.w + 0.6 : l.w * 0.5}
                opacity={l.on ? 0.95 : focus ? 0.04 : 0.35}
              />
            ))}
          </g>

          {/* layer hubs */}
          {hubs.map((h) => (
            <g key={h.key}>
              <circle cx={h.x} cy={HUB_Y} r={17} fill="var(--ink)" />
              <text
                x={h.x} y={HUB_Y + 4} textAnchor="middle"
                className="tnum" fontSize="11" fontWeight="600" fill="var(--on-ink)"
              >
                {h.code}
              </text>
              <text
                x={h.x} y={HUB_Y + 36} textAnchor="middle"
                className="marginal" fontSize="9.5" fill="var(--ink)"
                letterSpacing="1.4"
              >
                {h.label.toUpperCase()}
              </text>
              <foreignObject x={h.x - COL / 2 + 12} y={HUB_Y + 44} width={COL - 24} height={38}>
                <p className="text-center text-[10px] leading-snug text-ink-3">{h.blurb}</p>
              </foreignObject>
              {h.hidden > 0 && (
                <text
                  x={h.x} y={TOP + CAP * ROW + 6} textAnchor="middle"
                  className="marginal cursor-pointer" fontSize="9" fill="var(--ink-3)"
                  onClick={() => setExpanded((e) => ({ ...e, [h.key]: true }))}
                >
                  +{h.hidden} MORE
                </text>
              )}
            </g>
          ))}

          {/* nodes */}
          {placed.map((n) => {
            const faded = dim(n);
            const on = pickedSet.has(n.id);
            const lit = focus === n.id;
            return (
              <g
                key={n.id}
                opacity={faded ? 0.18 : 1}
                className="cursor-pointer"
                onMouseEnter={() => setHover(n.id)}
                onMouseLeave={() => setHover(null)}
                onClick={() => onToggle(n.id)}
              >
                <title>{`${n.label} — ${n.count} companies · ${n.bets} bets · ${n.near} near`}</title>
                {/* a chokepoint wears a ring: someone in here is ready to be asked */}
                {n.chokepoint && (
                  <circle cx={n.x} cy={n.y} r={n.r + 4} fill="none"
                    stroke="var(--ink)" strokeWidth={1.1} opacity={0.7} />
                )}
                <circle
                  cx={n.x} cy={n.y} r={n.r}
                  fill={n.bottleneck ? "var(--paper)" : "var(--ink)"}
                  stroke="var(--ink)"
                  strokeWidth={n.bottleneck ? 1.6 : 0}
                />
                {(lit || on) && (
                  <circle cx={n.x} cy={n.y} r={n.r + 8} fill="none"
                    stroke="var(--ink)" strokeWidth={0.7} opacity={0.35} />
                )}
                <text
                  x={n.x + n.r + 7} y={n.y + 3.4}
                  fontSize="10.5"
                  fill={on || lit ? "var(--ink)" : "var(--ink-2)"}
                  fontWeight={on ? 600 : 400}
                >
                  {trim(n.label, 21)}
                </text>
                <text
                  x={n.x + COL - NUM_GUTTER - 34} y={n.y + 3.4} textAnchor="end"
                  className="tnum" fontSize="9.5" fill="var(--ink-3)"
                >
                  {n.bets ? `${n.bets}/${n.count}` : n.count}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-4 py-2">
        <Legend><circle cx="5" cy="5" r="4" fill="var(--ink)" /></Legend>
        <span className="marginal">size = companies · bets/companies</span>
        <Legend>
          <circle cx="5" cy="5" r="3" fill="var(--ink)" />
          <circle cx="5" cy="5" r="5.5" fill="none" stroke="var(--ink)" strokeWidth="0.8" />
        </Legend>
        <span className="marginal">chokepoint — has a bet</span>
        <Legend><circle cx="5" cy="5" r="4" fill="var(--paper)" stroke="var(--ink)" strokeWidth="1.4" /></Legend>
        <span className="marginal">bottleneck — near-bets, no lead yet</span>
        <span className="marginal ml-auto hidden sm:block">click a segment to filter · drag to pan · +/− to zoom</span>
      </div>
    </div>
  );
}

function Legend({ children }: { children: React.ReactNode }) {
  return <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden>{children}</svg>;
}

function trim(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}
