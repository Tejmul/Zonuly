/* Diagrams for the docs, drawn in HTML rather than images so they reflow at phone width,
   follow the theme, and stay text a reader can select. Ink and rule only. */

import { ArrowDown, ArrowRight, User } from "lucide-react";

type Stage = { n: string; title: string; note: string; human?: boolean };

/** The pipeline as a flowing row of numbered stages; the one human step is solid ink. */
export function PipelineDiagram({ stages }: { stages: Stage[] }) {
  return (
    <figure className="my-8">
      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((s, i) => (
          <li
            key={s.n}
            className={`relative flex gap-3 rounded-xl border p-3.5 ${
              s.human ? "border-ink bg-ink text-on-ink" : "border-line bg-surface"
            }`}
          >
            <span className={`tnum mt-px text-[11px] ${s.human ? "opacity-70" : "text-ink-muted"}`}>{s.n}</span>
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-[13.5px] font-semibold">
                {s.human ? <User size={13} /> : null}
                {s.title}
              </span>
              <span className={`mt-0.5 block text-[12px] leading-snug ${s.human ? "opacity-80" : "text-ink-2"}`}>{s.note}</span>
            </span>
            {i < stages.length - 1 ? (
              <ArrowRight size={12} className="absolute top-1/2 -right-[11px] z-10 hidden -translate-y-1/2 rounded-full bg-paper text-ink-muted lg:block" />
            ) : null}
          </li>
        ))}
      </ol>
      <figcaption className="mt-3 text-[12px] text-ink-muted">
        Read left to right, top to bottom. The solid step is the only one a person performs.
      </figcaption>
    </figure>
  );
}

/** The layering rule: each band may import only the bands below it. */
export function LayersDiagram({ layers }: { layers: { name: string; what: string; modules: string[] }[] }) {
  return (
    <figure className="my-8">
      <div className="flex flex-col items-stretch">
        {layers.map((l, i) => (
          <div key={l.name} className="flex flex-col items-center">
            <div className="w-full rounded-xl border border-line bg-surface p-4" style={{ marginInline: `${i * 2}%`, width: `${100 - i * 4}%` }}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[14px] font-semibold">{l.name}</span>
                <span className="text-[12px] text-ink-muted">{l.what}</span>
              </div>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {l.modules.map((m) => <span key={m} className="chip">{m}</span>)}
              </div>
            </div>
            {i < layers.length - 1 ? <ArrowDown size={15} className="my-1 text-ink-muted" /> : null}
          </div>
        ))}
      </div>
      <figcaption className="mt-3 text-[12px] text-ink-muted">
        Arrows point the way imports go. Nothing imports upward, so a lower layer never knows the dashboard exists.
      </figcaption>
    </figure>
  );
}

/** A timeline: labelled marks along a line (a tick's steps, a day's send slots). */
export function Timeline({ items, caption }: { items: { at: string; label: string; strong?: boolean }[]; caption?: string }) {
  return (
    <figure className="my-8">
      <ol className="relative ml-2 border-l border-line">
        {items.map((it, i) => (
          <li key={i} className="relative pb-5 pl-6 last:pb-0">
            <span className={`absolute top-1.5 -left-[5px] h-[9px] w-[9px] rounded-full border ${it.strong ? "border-ink bg-ink" : "border-ink-3 bg-surface"}`} />
            <span className="tnum mr-3 text-[11.5px] text-ink-muted">{it.at}</span>
            <span className={`text-[13.5px] ${it.strong ? "font-semibold text-ink" : "text-ink-2"}`}>{it.label}</span>
          </li>
        ))}
      </ol>
      {caption ? <figcaption className="mt-3 text-[12px] text-ink-muted">{caption}</figcaption> : null}
    </figure>
  );
}

/** A funnel: each row narrower than the last, with its number. */
export function Funnel({ rows }: { rows: { label: string; n: string }[] }) {
  return (
    <figure className="my-8 space-y-1.5">
      {rows.map((r, i) => (
        <div key={r.label} className="mx-auto flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-2.5"
             style={{ width: `${100 - i * (60 / Math.max(1, rows.length - 1))}%`, minWidth: 220 }}>
          <span className="text-[13px] text-ink-2">{r.label}</span>
          <span className="tnum text-[14px] font-semibold">{r.n}</span>
        </div>
      ))}
    </figure>
  );
}
