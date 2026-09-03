"use client";

/** A ranked bar list.
 *
 *  Not a chart library: these are counts of long-labelled categories, which read faster
 *  as a ranked list than as any axis-and-legend chart, and stay legible at 320px and in
 *  greyscale. The value is text, so a screen reader gets the same information. */
export function BarList({
  rows, max: forced,
}: { rows: { label: string; value: number }[]; max?: number }) {
  const max = forced ?? Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <p className="py-3 text-[12px] text-ink-3">Nothing here yet.</p>;
  return (
    <ul className="space-y-px">
      {rows.map((r) => (
        <li key={r.label} className="group relative flex items-center gap-3 py-[5px]">
          <span
            aria-hidden
            className="absolute inset-y-0 left-0 -z-10 rounded-[2px] bg-surface-3 transition-[width] duration-500"
            style={{ width: `${(r.value / max) * 100}%` }}
          />
          <span className="truncate pl-2 text-[12px]">{r.label}</span>
          <span className="tnum ml-auto pr-2 text-[12px] text-ink-2">{r.value.toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}
