"use client";

/*  Charts.
 *
 *  Written to one rule that the previous version broke: a data mark has to be
 *  visible. The bars here were filled with --surface-3 on --surface, which is a
 *  contrast ratio of about 1.1:1, so every "chart" in the dashboard rendered as a
 *  plain list of numbers. Marks now come from the ink ramp (--data-1..5).
 *
 *  Two colour rules, and they are not interchangeable:
 *
 *    Nominal categories — sources, regions, statuses — get ONE step for every bar.
 *    Darkening by value would encode length twice and add nothing.
 *
 *    Ordered categories — a funnel, a tier ladder, a cascade of filters — get the
 *    ramp, because the order is real information and the ramp carries it.
 *
 *  Mark specs: bars capped at 22px so the band keeps some air, square where they
 *  leave the baseline and rounded at the data end, a 2px surface gap between
 *  neighbours, hairline gridlines. Every chart has a hover read-out and every
 *  chart's numbers are also plain text, so nothing is gated behind a mark.
 */

import { useId, useState } from "react";

const nf = new Intl.NumberFormat("en-US");

export type Row = { label: string; value: number; note?: string };

/* ------------------------------------------------------------------ bars */

/** Nominal magnitude. One series, one ink step, ranked descending.
 *
 *  Long category names are why this is horizontal: rotated axis labels on a
 *  column chart are unreadable and "Proven on their own page" is not going to
 *  get shorter. */
export function Bars({
  rows,
  max: forced,
  total,
  unit = "",
  emphasise,
}: {
  rows: Row[];
  max?: number;
  /** When given, the hover read-out can express each bar as a share of it. */
  total?: number;
  unit?: string;
  /** Label of the one row the story is about; the rest recede. */
  emphasise?: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  if (!rows.length) return <Nothing />;
  const max = forced ?? Math.max(1, ...rows.map((r) => r.value));

  return (
    <ul className="flex flex-col gap-[2px]">
      {rows.map((r) => {
        const pct = (r.value / max) * 100;
        const share = total ? (r.value / total) * 100 : null;
        const lit = !emphasise || emphasise === r.label;
        return (
          <li
            key={r.label}
            className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 py-1"
            onMouseEnter={() => setHover(r.label)}
            onMouseLeave={() => setHover(null)}
          >
            <div className="flex min-w-0 items-center gap-3">
              <span className="w-[42%] shrink-0 truncate text-[12px] text-ink-2" title={r.label}>
                {r.label}
              </span>
              {/* The track is the full band; the mark is the value. Keeping the
                  track visible is what makes "small" read as small rather than
                  as a rendering failure. */}
              <span className="bar-track relative h-[10px] min-w-0 flex-1">
                <span
                  className="bar-mark absolute inset-y-0 left-0"
                  style={{
                    width: `${Math.max(pct, r.value > 0 ? 1.5 : 0)}%`,
                    background: lit ? "var(--data-2)" : "var(--data-5)",
                  }}
                />
              </span>
            </div>
            <span className="tnum shrink-0 text-right text-[12px] text-ink tabular-nums">
              {hover === r.label && share !== null
                ? `${share.toFixed(1)}%`
                : `${nf.format(r.value)}${unit}`}
            </span>
            {r.note ? (
              <span className="col-span-2 -mt-0.5 pl-[calc(42%+0.75rem)] text-[11px] text-ink-3">
                {r.note}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/* ---------------------------------------------------------------- funnel */

export type Stage = { label: string; value: number; explain: string };

/** An ordered cascade: each stage as a share of the first, with the loss between
 *  stages named rather than left to be inferred from two bar lengths.
 *
 *  The drop is the whole point of a funnel, so it gets its own line of type. A
 *  funnel that only draws the survivors makes the reader do subtraction. */
export function Funnel({ stages, unit }: { stages: Stage[]; unit: string }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!stages.length) return <Nothing />;
  const first = Math.max(1, stages[0].value);
  const steps = ["var(--data-1)", "var(--data-2)", "var(--data-3)", "var(--data-4)", "var(--data-5)"];

  return (
    <ol className="flex flex-col">
      {stages.map((s, i) => {
        const pct = (s.value / first) * 100;
        const prev = i > 0 ? stages[i - 1].value : null;
        const lost = prev !== null ? prev - s.value : null;
        const kept = prev ? (s.value / prev) * 100 : 100;
        return (
          <li key={s.label}>
            {lost !== null && (
              <div className="flex items-center gap-2 py-1 pl-1">
                <span className="h-3 w-px bg-line-strong" />
                <span className="text-[11px] text-ink-3">
                  {lost > 0 ? (
                    <>
                      <span className="tnum">{nf.format(lost)}</span> dropped here ·{" "}
                      <span className="tnum">{kept.toFixed(kept < 10 ? 1 : 0)}%</span> carried through
                    </>
                  ) : (
                    <>all carried through</>
                  )}
                </span>
              </div>
            )}
            <button
              type="button"
              onClick={() => setOpen(open === i ? null : i)}
              aria-expanded={open === i}
              className="w-full cursor-pointer text-left"
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[12.5px] font-medium text-ink">{s.label}</span>
                <span className="tnum text-[13px] text-ink tabular-nums">
                  {nf.format(s.value)}
                  <span className="ml-1 text-[11px] text-ink-3">{unit}</span>
                </span>
              </div>
              <span className="bar-track mt-1 block h-[14px] w-full">
                <span
                  className="bar-mark block h-full"
                  style={{
                    width: `${Math.max(pct, s.value > 0 ? 1.5 : 0)}%`,
                    background: steps[Math.min(i, steps.length - 1)],
                  }}
                />
              </span>
              <span className="mt-1 block text-[11px] leading-snug text-ink-3">{s.explain}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/* ----------------------------------------------------------------- gates */

export type Gate = { label: string; value: number; explain: string };

/** Why a big number becomes a small one. Each row is a test, drawn against the
 *  same starting population, so the reader sees which test is doing the cutting
 *  instead of being told a total they have to trust. */
export function Gates({ start, gates, passed }: { start: number; gates: Gate[]; passed: number }) {
  return (
    <div>
      <ul className="flex flex-col gap-2.5">
        {gates.map((g) => {
          const pct = start ? (g.value / start) * 100 : 0;
          return (
            <li key={g.label}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[12px] text-ink-2">{g.label}</span>
                <span className="tnum text-[12px] text-ink tabular-nums">
                  {nf.format(g.value)}
                  <span className="ml-1.5 text-[11px] text-ink-3">{pct.toFixed(0)}%</span>
                </span>
              </div>
              <span className="bar-track mt-1 block h-[8px] w-full">
                <span
                  className="bar-mark block h-full"
                  style={{ width: `${Math.max(pct, g.value > 0 ? 1 : 0)}%`, background: "var(--data-3)" }}
                />
              </span>
              <span className="mt-0.5 block text-[11px] text-ink-3">{g.explain}</span>
            </li>
          );
        })}
      </ul>

      <div className="mt-4 flex items-baseline justify-between gap-3 border-t border-line pt-3">
        <span className="text-[12.5px] font-medium text-ink">All four at once</span>
        <span className="tnum text-[15px] font-medium text-ink tabular-nums">{nf.format(passed)}</span>
      </div>
      <span className="bar-track mt-1 block h-[10px] w-full">
        <span
          className="bar-mark block h-full"
          style={{
            width: `${Math.max(start ? (passed / start) * 100 : 0, passed > 0 ? 1 : 0)}%`,
            background: "var(--data-1)",
          }}
        />
      </span>
    </div>
  );
}

/* ----------------------------------------------------------------- meter */

/** A single ratio against a hard limit. The track is a lighter step of the same
 *  ramp, so the state reads across the whole bar rather than only where it is
 *  filled. */
export function Meter({
  used,
  cap,
  label,
  foot,
}: {
  used: number;
  cap: number;
  label: string;
  foot?: string;
}) {
  const pct = cap ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12px] text-ink-2">{label}</span>
        <span className="tnum text-[12px] text-ink tabular-nums">
          {nf.format(used)} / {nf.format(cap)}
        </span>
      </div>
      <span className="bar-track mt-1.5 block h-[10px] w-full">
        <span
          className="bar-mark block h-full"
          style={{ width: `${Math.max(pct, used > 0 ? 2 : 0)}%`, background: "var(--data-1)" }}
        />
      </span>
      {foot ? <p className="mt-1.5 text-[11px] text-ink-3">{foot}</p> : null}
    </div>
  );
}

/* -------------------------------------------------------------- stat row */

export function Stat({
  label,
  value,
  hint,
  hero = false,
}: {
  label: string;
  value: number | string;
  hint?: string;
  /** Exactly one per view. */
  hero?: boolean;
}) {
  return (
    <div className="p-4">
      <p className="marginal">{label}</p>
      <p
        className={`tnum mt-1.5 leading-none font-medium tracking-[-0.02em] tabular-nums ${
          hero ? "text-[40px]" : "text-[26px]"
        }`}
      >
        {typeof value === "number" ? nf.format(value) : value}
      </p>
      {hint ? <p className="mt-1.5 text-[11.5px] leading-snug text-ink-3">{hint}</p> : null}
    </div>
  );
}

/* ------------------------------------------------------------ table view */

/** Every chart on the page can be read as numbers. Charts are a convenience, not
 *  the only way in — a screen reader, a print-out and a colour-blind reader all
 *  land here. */
export function TableView({ caption, rows, unit = "" }: { caption: string; rows: Row[]; unit?: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={id}
        className="cursor-pointer text-[11px] text-ink-3 underline-offset-2 hover:text-ink hover:underline"
      >
        {open ? "Hide the numbers" : "Show the numbers"}
      </button>
      <div id={id} hidden={!open} className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-[11.5px]">
          <caption className="sr-only">{caption}</caption>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-line last:border-0">
                <th scope="row" className="py-1 pr-3 text-left font-normal text-ink-2">
                  {r.label}
                </th>
                <td className="tnum py-1 text-right tabular-nums text-ink">
                  {nf.format(r.value)}
                  {unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Nothing() {
  return <p className="py-3 text-[12px] text-ink-3">Nothing here yet.</p>;
}

/** Kept so older imports keep working; new code should use `Bars`. */
export const BarList = Bars;

/* ================================================================== 2026-09-20
 *  The dashboard follows the NextAdmin reference: a two-series trend, a tick gauge,
 *  label-inside ranked bars, an emphasis stack and status pills. Colour arrives
 *  here for the first time — the accent and the second series were validated for
 *  colour-blind separation (see globals.css). Text never wears a series colour.
 */

/* ------------------------------------------------------------------ trend */

export type Series = { name: string; data: number[]; color: string; /** dashed line, no fill: the second series in a monochrome pair */ dashed?: boolean };

/** Two (or one) series over the same weeks, one axis, a crosshair that finds the
 *  nearest week and reads every series out at once. The end value of each series
 *  is labelled directly; the axis carries the rest. */
export function Trend({
  labels,
  series,
  ariaLabel,
}: {
  labels: string[];
  series: Series[];
  ariaLabel: string;
}) {
  const [at, setAt] = useState<number | null>(null);
  const W = 1200, H = 320, L = 56, R = 24, T = 18, B = 34;
  const raw = Math.max(1, ...series.flatMap((s) => s.data));
  const step = niceStep(raw);
  const max = Math.ceil(raw / step) * step;
  const ticks = Array.from({ length: max / step + 1 }, (_, i) => i * step);
  const n = Math.max(2, labels.length);
  const x = (i: number) => L + (i * (W - L - R)) / (n - 1);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);

  // Catmull-Rom → cubic Bézier; control points clamped to the baseline so a run of
  // zeros never dips below it.
  const smooth = (pts: [number, number][]) => {
    let d = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(i - 1, 0)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(i + 2, pts.length - 1)];
      const c1y = Math.min(y(0), p1[1] + (p2[1] - p0[1]) / 6);
      const c2y = Math.min(y(0), p2[1] - (p3[1] - p1[1]) / 6);
      d += ` C ${p1[0] + (p2[0] - p0[0]) / 6} ${c1y}, ${p2[0] - (p3[0] - p1[0]) / 6} ${c2y}, ${p2[0]} ${p2[1]}`;
    }
    return d;
  };

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = L + ((e.clientX - box.left) / box.width) * (W - L - R);
    let best = 0, dist = Infinity;
    labels.forEach((_, i) => { const d = Math.abs(x(i) - px); if (d < dist) { dist = d; best = i; } });
    setAt(best);
  };
  const onKey = (e: React.KeyboardEvent<SVGRectElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    setAt((cur) => {
      const c = cur ?? labels.length - 1;
      return e.key === "ArrowLeft" ? Math.max(0, c - 1) : Math.min(labels.length - 1, c + 1);
    });
  };

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={ariaLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={L - 10} y={y(t) + 4} textAnchor="end" fill="var(--ink-3)" fontSize={12} fontFamily="var(--font-mono)">
              {nf.format(t)}
            </text>
          </g>
        ))}
        {labels.map((l, i) => (
          <text key={l + i} x={x(i)} y={H - 10} textAnchor="middle" fill="var(--ink-3)" fontSize={12} fontFamily="var(--font-sans)">
            {l}
          </text>
        ))}
        {series.map((s, si) => {
          const pts = s.data.map((v, i) => [x(i), y(v)] as [number, number]);
          const line = smooth(pts);
          const last = pts[pts.length - 1];
          // the lower series labels below its end dot, the upper above, so two ends
          // that finish close together never overprint
          const lowest = series.every((o) => o.data[o.data.length - 1] >= s.data[s.data.length - 1]);
          return (
            <g key={s.name}>
              {s.dashed ? null : <path d={`${line} L ${x(n - 1)} ${y(0)} L ${x(0)} ${y(0)} Z`} fill={s.color} fillOpacity={0.07} />}
              <path d={line} fill="none" stroke={s.color} strokeWidth={s.dashed ? 1.5 : 2} strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={last[0]} cy={last[1]} r={5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              <text x={last[0] - 8} y={lowest && series.length > 1 ? last[1] + 20 : last[1] - 12} textAnchor="end" fill="var(--ink)" fontSize={12} fontWeight={600} fontFamily="var(--font-mono)">
                {nf.format(s.data[s.data.length - 1])}
              </text>
              {at !== null ? (
                <circle cx={x(at)} cy={y(s.data[at] ?? 0)} r={5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ) : null}
              {si === 0 && at !== null ? (
                <line x1={x(at)} x2={x(at)} y1={T} y2={y(0)} stroke="var(--line-strong)" strokeWidth={1} />
              ) : null}
            </g>
          );
        })}
        <rect
          x={L} y={T} width={W - L - R} height={H - T - B} fill="transparent" tabIndex={0}
          onPointerMove={onMove} onPointerLeave={() => setAt(null)}
          onFocus={() => setAt(labels.length - 1)} onBlur={() => setAt(null)} onKeyDown={onKey}
        />
      </svg>
      {at !== null ? (
        <div
          role="status"
          className="card pointer-events-none absolute top-2 min-w-[160px] px-2.5 py-2 text-[12.5px]"
          style={{ left: `min(calc(${(x(at) / W) * 100}% + 14px), calc(100% - 172px))` }}
        >
          <div className="mb-1 text-[11.5px] text-ink-3">Week of {labels[at]}</div>
          {series.map((s) => (
            <div key={s.name} className="flex items-center gap-2">
              <span className="h-[2px] w-3 rounded-full" style={{ background: s.color, opacity: s.dashed ? 0.6 : 1 }} />
              <span className="text-ink-2">{s.name}</span>
              <span className="tnum ml-auto font-semibold text-ink">{nf.format(s.data[at] ?? 0)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function niceStep(max: number): number {
  const rough = max / 4;
  const mag = 10 ** Math.floor(Math.log10(Math.max(1, rough)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= rough) return m * mag;
  return 10 * mag;
}

/* ------------------------------------------------------------------ gauge */

/** A single ratio, drawn as a half-ring of ticks. The centre carries the number in
 *  text, so the ring is never the only way to read it. */
export function Gauge({ ratio, label, ariaLabel }: { ratio: number; label: string; ariaLabel: string }) {
  const N = 40, filled = Math.round(Math.max(0, Math.min(1, ratio)) * N);
  const cx = 150, cy = 150, r1 = 100, r2 = 138;
  return (
    <div className="relative grid place-items-center pt-2">
      <svg viewBox="0 0 300 162" className="h-auto w-full max-w-[300px]" role="img" aria-label={ariaLabel}>
        {Array.from({ length: N }, (_, i) => {
          const a = Math.PI - (i / (N - 1)) * Math.PI, ca = Math.cos(a), sa = Math.sin(a);
          return (
            <line
              key={i}
              x1={cx + r1 * ca} y1={cy - r1 * sa} x2={cx + r2 * ca} y2={cy - r2 * sa}
              stroke={i < filled ? "var(--series-1)" : "var(--surface-3)"} strokeWidth={6} strokeLinecap="round"
            />
          );
        })}
      </svg>
      <div className="absolute inset-x-0 bottom-1.5 text-center">
        <div className="tnum font-[family-name:var(--font-display)] text-[34px] font-semibold leading-none tracking-[-0.03em]">
          {Math.round(ratio * 100)}%
        </div>
        <div className="mt-1 text-ink-2">{label}</div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- ribbon */

/** Ranked bars with the label inside the bar, as in the reference's traffic-sources
 *  panel. One wash for every bar: the length is the only encoding. */
export function Ribbon({ rows, head }: { rows: Row[]; head: [string, string] }) {
  if (!rows.length) return <Nothing />;
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="flex flex-col gap-2">
      <div className="mb-0.5 flex justify-between text-[12.5px] text-ink-3">
        <span>{head[0]}</span><span>{head[1]}</span>
      </div>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[minmax(0,1fr)_56px] items-center gap-3">
          <div className="h-6 min-w-0">
            <div
              className="flex h-full min-w-fit items-center rounded-md bg-surface-2 px-2.5 text-[13px] font-medium whitespace-nowrap transition-colors hover:bg-surface-3"
              style={{ width: `${Math.max(8, (r.value / max) * 100)}%` }}
              title={`${r.label}: ${nf.format(r.value)}`}
            >
              {r.label}
            </div>
          </div>
          <div className="tnum text-right font-semibold">{nf.format(r.value)}</div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ stack */

export type Segment = { label: string; value: number; color: string };

/** Part-to-whole with one segment emphasised: the one the story is about takes the
 *  accent, the rest step down through the greys. 2px surface gaps, no strokes. */
export function Stack({ segments, ariaLabel }: { segments: Segment[]; ariaLabel: string }) {
  const total = Math.max(1, segments.reduce((a, s) => a + s.value, 0));
  return (
    <div>
      <div className="mt-1 flex h-[22px] gap-[2px] overflow-hidden rounded-md" role="img" aria-label={ariaLabel}>
        {segments.map((s) => (
          <span key={s.label} className="block h-full" style={{ width: `${(s.value / total) * 100}%`, background: s.color }} title={`${s.label}: ${nf.format(s.value)}`} />
        ))}
      </div>
      <ul className="mt-3 flex flex-col gap-2">
        {segments.map((s) => (
          <li key={s.label} className="grid grid-cols-[10px_1fr_auto_36px] items-center gap-2.5 text-[12.5px]">
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
            <span className="text-ink-2">{s.label}</span>
            <span className="tnum font-semibold">{nf.format(s.value)}</span>
            <span className="tnum text-right text-ink-3">{Math.round((s.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ delta */

/** Signed change against a named period. Direction is carried by the arrow and the
 *  words as well as the colour. */
export function Delta({ now, before, unit = "this week" }: { now: number; before: number; unit?: string }) {
  if (!now && !before) return <span className="text-[12.5px] text-ink-3">no change</span>;
  const up = now >= before;
  const ratio = before > 0 ? now / before : null;
  return (
    <span className={`inline-flex items-center gap-1 text-[12.5px] font-medium whitespace-nowrap ${up ? "text-ink" : "text-ink-2"}`}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {up ? <path d="M12 19V5M6 11l6-6 6 6" /> : <path d="M12 5v14M6 13l6 6 6-6" />}
      </svg>
      {nf.format(now)} {unit}
      {ratio !== null && ratio !== 1 ? <span className="text-ink-3">· {ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}× last</span> : null}
    </span>
  );
}
