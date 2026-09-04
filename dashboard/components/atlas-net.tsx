"use client";

/*  The Atlas network.
 *
 *  Four columns, wired left to right, and the wiring is the arithmetic in
 *  lib/atlas-model.ts rather than an illustration of it:
 *
 *      you            what it weighs        segments          focus on these
 *      ────           ──────────────        ────────          ──────────────
 *      resume    ──▶  right kind of work ──▶ AI agents    ──▶  Vercel
 *      + what         can pay                deep tech         Baseten
 *      you want       you could take it      robotics          …
 *                     open to you
 *                     really hiring
 *                     someone to ask
 *
 *  Every wire's weight is a real number: input→term is the slider you set,
 *  term→segment is that term's mean contribution inside the segment, and
 *  term→company is its contribution to that company's score. Node size is
 *  activation. Hovering a company dims everything that did not feed it, which is
 *  the whole point — the answer to "why this one" is a path you can see.
 *
 *  The travelling dots are a dash on a second copy of each live wire. They carry
 *  no information beyond "this wire is live", and they stop dead under
 *  prefers-reduced-motion, where the picture is still complete without them.
 */

import { useMemo, useState } from "react";
import { TERMS, type FieldKey, type Scored, type Weights } from "@/lib/atlas-model";

type Seg = { segment: string; label: string; heat: number; n: number };

/* The columns are inset far enough that end-anchored labels on the left and
   start-anchored names on the right both have room inside the viewBox. They ran
   off both edges when the first and last columns sat at 80 and 920. */
const W = 1200;
const COLS = [210, 470, 730, 1000];
const TOP = 64;
const GAP = 30;

export function AtlasNet({
  inputs, segments, ranked, weights, fields, onPick, picked,
}: {
  inputs: { label: string; detail: string }[];
  segments: Seg[];
  ranked: Scored[];
  weights: Weights;
  fields: FieldKey[];
  picked: number | null;
  onPick: (id: number | null) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const active = hover ?? picked;

  const segs = segments.slice(0, 8);
  const outs = ranked.slice(0, 8);
  const ins = inputs.slice(0, 4);

  const rows = Math.max(ins.length, TERMS.length, segs.length, outs.length);
  const H = TOP + rows * GAP + 40;

  const y = (i: number, n: number) => TOP + (rows - n) * (GAP / 2) + i * GAP;

  /* Which company is under the cursor, and everything that fed it. */
  const focus = useMemo(() => {
    if (active === null) return null;
    const s = outs.find((o) => o.company.id === active);
    if (!s) return null;
    return { scored: s, segment: s.company.segment, terms: new Set(s.top) };
  }, [active, outs]);

  const dim = (on: boolean) => (focus && !on ? 0.12 : 1);

  return (
    <div className="relative">
      {/* Scrolls rather than shrinks: at 390px a 1200-unit viewBox would render the
          labels about three pixels tall, which is not a smaller chart, it is no
          chart. Wide content gets its own scroll container. */}
      <div className="-mx-1 overflow-x-auto px-1">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full min-w-[860px]"
        style={{ height: "auto" }}
        role="img"
        aria-label="How your resume and your priorities turn into a ranked list of companies"
      >
        <Heads />

        {/* -------- wires: you -> terms ------------------------------------ */}
        {ins.map((_, i) =>
          TERMS.map((t, j) => {
            const on = !focus || focus.terms.has(t.key);
            return (
              <Wire
                key={`i${i}-${t.key}`}
                x1={COLS[0]} y1={y(i, ins.length)} x2={COLS[1]} y2={y(j, TERMS.length)}
                weight={weights[t.key]} opacity={dim(on)} live={on && weights[t.key] > 0.6}
              />
            );
          }),
        )}

        {/* -------- wires: terms -> segments ------------------------------- */}
        {TERMS.map((t, j) =>
          segs.map((s, k) => {
            const on = !focus || (focus.terms.has(t.key) && focus.segment === s.segment);
            return (
              <Wire
                key={`t${t.key}-${s.segment}`}
                x1={COLS[1]} y1={y(j, TERMS.length)} x2={COLS[2]} y2={y(k, segs.length)}
                weight={weights[t.key] * s.heat} opacity={dim(on)} live={on && s.heat > 0.55}
              />
            );
          }),
        )}

        {/* -------- wires: segments -> companies --------------------------- */}
        {segs.map((s, k) =>
          outs.map((o, m) => {
            if (o.company.segment !== s.segment) return null;
            const on = !focus || focus.scored.company.id === o.company.id;
            return (
              <Wire
                key={`s${s.segment}-${o.company.id}`}
                x1={COLS[2]} y1={y(k, segs.length)} x2={COLS[3]} y2={y(m, outs.length)}
                weight={o.score} opacity={dim(on)} live={on && o.score > 0.5}
              />
            );
          }),
        )}

        {/* -------- nodes -------------------------------------------------- */}
        {ins.map((n, i) => (
          <Node
            key={n.label} x={COLS[0]} y={y(i, ins.length)} r={5} anchor="end"
            label={n.label} sub={n.detail} opacity={dim(true)}
          />
        ))}

        {TERMS.map((t, j) => (
          <Node
            key={t.key} x={COLS[1]} y={y(j, TERMS.length)}
            r={3 + weights[t.key] * 5} anchor="middle"
            label={t.label}
            opacity={dim(!focus || focus.terms.has(t.key))}
          />
        ))}

        {segs.map((s, k) => (
          <Node
            key={s.segment} x={COLS[2]} y={y(k, segs.length)}
            r={3 + s.heat * 6} anchor="middle"
            label={s.label}
            opacity={dim(!focus || focus.segment === s.segment)}
          />
        ))}

        {outs.map((o, m) => (
          <g
            key={o.company.id}
            onMouseEnter={() => setHover(o.company.id)}
            onMouseLeave={() => setHover(null)}
            onClick={() => onPick(picked === o.company.id ? null : o.company.id)}
            className="cursor-pointer"
          >
            {/* a fat invisible target, so a 6px dot is still easy to hit */}
            <rect x={COLS[3] - 14} y={y(m, outs.length) - 13} width={W - COLS[3] + 14} height={26} fill="transparent" />
            <Node
              x={COLS[3]} y={y(m, outs.length)} r={3 + o.score * 6} anchor="start"
              label={o.company.name} value={`${Math.round(o.score * 100)}`} valueX={W - 6}
              opacity={dim(!focus || focus.scored.company.id === o.company.id)}
              strong={picked === o.company.id}
            />
          </g>
        ))}
      </svg>
      </div>

      {/* The read-out. A picture that cannot say why is just a picture. */}
      <div className="border-t border-line px-1 pt-3">
        {focus ? (
          <Why scored={focus.scored} weights={weights} fields={fields} />
        ) : (
          <p className="text-[11.5px] text-ink-3">
            Hover a company on the right to trace what lit it up. Node size is how strongly
            each thing fired; wire weight is how much it contributed.
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function Heads() {
  const heads = ["You", "What it weighs", "Segments", "Focus on these"];
  return (
    <>
      {heads.map((h, i) => (
        <text
          key={h} x={COLS[i]} y={28}
          textAnchor={i === 0 ? "end" : i === 3 ? "start" : "middle"}
          className="fill-[var(--ink-3)]"
          style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", fontFamily: "var(--font-mono)" }}
        >
          {h}
        </text>
      ))}
    </>
  );
}

function Wire({
  x1, y1, x2, y2, weight, opacity, live,
}: {
  x1: number; y1: number; x2: number; y2: number;
  weight: number; opacity: number; live: boolean;
}) {
  if (weight <= 0.02) return null;
  const mx = (x1 + x2) / 2;
  const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
  return (
    <g style={{ opacity }}>
      <path d={d} fill="none" stroke="var(--data-4)" strokeWidth={0.4 + weight * 1.6} strokeOpacity={0.15 + weight * 0.5} />
      {live && (
        <path
          d={d} fill="none" stroke="var(--data-1)" strokeWidth={1.6} strokeLinecap="round"
          className="atlas-pulse"
          // staggered off the wire's own geometry, so the dots do not march in lockstep
          style={{ animationDelay: `${((y1 * 7 + y2 * 13) % 260) / 100}s` }}
        />
      )}
    </g>
  );
}

function Node({
  x, y, r, label, sub, value, valueX, anchor, opacity, strong = false,
}: {
  x: number; y: number; r: number; label: string; sub?: string; value?: string;
  /** Absolute x for the number, so scores line up in one column instead of
   *  floating beside nodes of different sizes and colliding with the wires. */
  valueX?: number;
  anchor: "start" | "middle" | "end"; opacity: number; strong?: boolean;
}) {
  const pad = r + 8;
  const tx = anchor === "end" ? x - pad : anchor === "start" ? x + pad : x + pad;
  const textAnchor = anchor === "end" ? "end" : "start";
  return (
    <g style={{ opacity }}>
      <circle cx={x} cy={y} r={r} fill={strong ? "var(--data-1)" : "var(--data-2)"} />
      <circle cx={x} cy={y} r={r + 3} fill="none" stroke="var(--surface)" strokeWidth={2} />
      <text
        x={tx} y={y - (sub ? 2 : -3.5)} textAnchor={textAnchor}
        className="fill-[var(--ink)]" style={{ fontSize: 11.5 }}
      >
        {label.length > 26 ? label.slice(0, 25) + "…" : label}
      </text>
      {sub && (
        <text x={tx} y={y + 10} textAnchor={textAnchor} className="fill-[var(--ink-3)]" style={{ fontSize: 9.5 }}>
          {sub.length > 34 ? sub.slice(0, 33) + "…" : sub}
        </text>
      )}
      {value && (
        <text
          x={valueX ?? (anchor === "end" ? x + pad : x - pad)} y={y + 3.5}
          textAnchor={valueX !== undefined ? "end" : anchor === "end" ? "start" : "end"}
          className="fill-[var(--ink-3)]"
          style={{ fontSize: 9.5, fontFamily: "var(--font-mono)" }}
        >
          {value}
        </text>
      )}
    </g>
  );
}

function Why({ scored, weights }: { scored: Scored; weights: Weights; fields: FieldKey[] }) {
  const c = scored.company;
  const ordered = [...TERMS].sort(
    (a, b) => scored.terms[b.key].contribution - scored.terms[a.key].contribution,
  );
  const max = Math.max(...ordered.map((t) => scored.terms[t.key].contribution), 0.001);

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[13px] font-medium text-ink">{c.name}</span>
        <span className="tnum text-[12px] text-ink">{Math.round(scored.score * 100)}<span className="text-ink-3">/100</span></span>
        <span className="text-[11.5px] text-ink-3">{c.segment_label}</span>
        <a href={`/companies/${c.id}`} className="ml-auto text-[11.5px] text-ink underline-offset-2 hover:underline">
          Open →
        </a>
      </div>
      <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {ordered.map((t) => {
          const term = scored.terms[t.key];
          return (
            <li key={t.key} className="flex items-baseline gap-2 text-[11px]">
              <span className="w-[7.5rem] shrink-0 text-ink-2">{t.label}</span>
              <span className="bar-track relative h-[6px] w-14 shrink-0">
                <span
                  className="bar-mark absolute inset-y-0 left-0"
                  style={{
                    width: `${(term.contribution / max) * 100}%`,
                    background: weights[t.key] > 0.01 ? "var(--data-2)" : "var(--data-5)",
                  }}
                />
              </span>
              <span className="min-w-0 flex-1 truncate text-ink-3" title={term.why}>{term.why}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
