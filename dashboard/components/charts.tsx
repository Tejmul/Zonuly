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
