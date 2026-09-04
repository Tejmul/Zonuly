"use client";

/*  Atlas.
 *
 *  It used to be a map of the market — 34 industry segments and a hairball of grey
 *  edges — which answered "what is out there". Nobody was asking that. The question
 *  is "given my resume and what I actually want, where should my hours go", and
 *  that is a computation.
 *
 *  So: your resume on the left, six weighted terms in the middle, segments lighting
 *  up, companies ranked on the right, and every wire between them a real number out
 *  of lib/atlas-model.ts. Move a slider and watch the ranking move. Hover a company
 *  and the path that produced it stays lit while everything else drops away.
 *
 *  The old result columns were wrong and the user was right about it: a raw region
 *  code and a roles/leads fraction told you nothing about whether to spend a morning
 *  on a company. The columns now say what it pays, whether you could take it, and
 *  what actually carried its score.
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import { useApi } from "@/lib/api";
import { AtlasNet } from "@/components/atlas-net";
import {
  DEFAULT_WEIGHTS, FIELDS, TERMS, rank, resumeSignals, segmentHeat, suggestedFields,
  type Company, type FieldKey, type Profile, type TermKey, type Weights,
} from "@/lib/atlas-model";

type Atlas = { companies: Company[]; stats: Record<string, number> };

export default function AtlasPage() {
  const net = useApi<Atlas>("/api/network");
  const prof = useApi<Profile>("/api/profile");

  const [fields, setFields] = useState<FieldKey[] | null>(null);
  const [w, setW] = useState<Weights>(DEFAULT_WEIGHTS);
  const [picked, setPicked] = useState<number | null>(null);
  const [q, setQ] = useState("");

  // Until the resume lands, the opening position is read from it rather than
  // guessed — the page should never greet you with an empty board.
  // Both memoised: scoring runs over ~2,000 companies x 6 terms, so a new array
  // identity on every keystroke in the search box would re-rank the whole atlas.
  const suggested = useMemo(() => suggestedFields(prof.data ?? null), [prof.data]);
  const chosen = fields ?? suggested;
  const companies = useMemo(() => net.data?.companies ?? [], [net.data]);

  const ranked = useMemo(() => rank(companies, chosen, w, 60), [companies, chosen, w]);
  const heat = useMemo(() => segmentHeat(companies, chosen, w), [companies, chosen, w]);
  const inputs = useMemo(() => resumeSignals(prof.data ?? null), [prof.data]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return ranked;
    return ranked.filter((r) =>
      `${r.company.name} ${r.company.description ?? ""} ${r.company.segment_label}`.toLowerCase().includes(needle),
    );
  }, [ranked, q]);

  if (net.error) return <p className="py-8 text-[13px] text-ink-3">{net.error}</p>;
  if (!net.data) return <p className="py-8 text-[13px] text-ink-3">Reading the atlas…</p>;

  const toggleField = (k: FieldKey) =>
    setFields((cur) => {
      const base = cur ?? chosen;
      return base.includes(k) ? base.filter((x) => x !== k) : [...base, k];
    });

  const dirty = JSON.stringify(w) !== JSON.stringify(DEFAULT_WEIGHTS) || fields !== null;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
          Atlas
        </h1>
        <p className="mt-1 max-w-[70ch] text-[12.5px] leading-relaxed text-ink-2">
          Your resume goes in on the left. Six things get weighed. Segments light up, and the
          companies worth your hours come out on the right — each one traceable back through
          the wires that produced it. Change what you want and the whole thing recomputes.
        </p>
      </header>

      {/* ------------------------------------------------------------ inputs */}
      <section className="plate p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="eyebrow">What do you want to work on</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {FIELDS.map((f) => {
                const on = chosen.includes(f.key);
                return (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => toggleField(f.key)}
                    aria-pressed={on}
                    title={f.blurb}
                    className={`cursor-pointer rounded-md border px-2.5 py-1.5 text-[12px] transition-colors ${
                      on
                        ? "border-ink bg-ink text-on-ink"
                        : "border-line text-ink-2 hover:border-line-strong hover:text-ink"
                    }`}
                  >
                    {f.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 max-w-[62ch] text-[11px] leading-relaxed text-ink-3">
              {chosen.length
                ? FIELDS.filter((f) => chosen.includes(f.key)).map((f) => f.blurb).join(" ")
                : "Nothing picked, so the field term is neutral and the other five decide."}
            </p>
          </div>

          {dirty && (
            <button
              type="button"
              onClick={() => { setW(DEFAULT_WEIGHTS); setFields(null); }}
              className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 text-[11.5px] text-ink-3 hover:text-ink"
            >
              <RotateCcw size={12} />
              Reset to what your resume suggests
            </button>
          )}
        </div>

        <div className="mt-4 border-t border-line pt-3">
          <p className="eyebrow">How much each thing should count</p>
          <div className="mt-2 grid gap-x-8 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            {TERMS.map((t) => (
              <label key={t.key} className="block cursor-pointer" title={t.asks}>
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-[11.5px] text-ink-2">{t.label}</span>
                  <span className="tnum text-[11px] text-ink-3">{w[t.key].toFixed(2)}</span>
                </span>
                <input
                  type="range" min={0} max={1} step={0.05} value={w[t.key]}
                  onChange={(e) => setW({ ...w, [t.key as TermKey]: Number(e.target.value) })}
                  className="mt-1 w-full cursor-pointer accent-[var(--ink)]"
                  aria-label={`${t.label}: ${t.asks}`}
                />
              </label>
            ))}
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------------- network */}
      <section className="plate p-4">
        <AtlasNet
          inputs={inputs}
          segments={heat}
          ranked={ranked}
          weights={w}
          fields={chosen}
          picked={picked}
          onPick={setPicked}
        />
      </section>

      {/* ----------------------------------------------------------- results */}
      <section className="plate">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <h2 className="text-[13px] font-semibold">
            {shown.length} compan{shown.length === 1 ? "y" : "ies"}, best first
          </h2>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Find one"
            className="h-8 w-48 rounded-sm border border-line bg-surface px-2.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-line-strong"
          />
          <span className="ml-auto text-[11px] text-ink-3">
            Score is the weighted sum above, out of 100
          </span>
        </div>

        <ul className="divide-y divide-line">
          {shown.slice(0, 40).map((r, i) => {
            const c = r.company;
            return (
              <li key={c.id}>
                <Link
                  href={`/companies/${c.id}`}
                  onMouseEnter={() => setPicked(c.id)}
                  className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2 sm:grid-cols-[2rem_14rem_minmax(0,1fr)_auto]"
                >
                  <span className="tnum text-[11px] text-ink-3">{i + 1}</span>

                  <span className="min-w-0">
                    <span className="block truncate text-[12.5px] font-medium">{c.name}</span>
                    <span className="block truncate text-[11px] text-ink-3">{c.segment_label}</span>
                  </span>

                  {/* The columns that matter: what carried the score, not a region code. */}
                  <span className="hidden min-w-0 flex-wrap items-center gap-1.5 sm:flex">
                    {r.top.map((k) => (
                      <span key={k} className="chip">
                        {TERMS.find((t) => t.key === k)?.label}
                      </span>
                    ))}
                    <span className="ml-1 truncate text-[11px] text-ink-3">
                      {r.terms.pay.why} · {r.terms.remote.why}
                    </span>
                  </span>

                  <span className="flex items-center gap-3">
                    <span className="bar-track hidden h-[8px] w-16 sm:block">
                      <span
                        className="bar-mark block h-full"
                        style={{ width: `${Math.max(2, r.score * 100)}%`, background: "var(--data-2)" }}
                      />
                    </span>
                    <span className="tnum w-8 text-right text-[12px]">{Math.round(r.score * 100)}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>

        {shown.length === 0 && (
          <p className="px-4 py-6 text-[12px] text-ink-3">
            Nothing matches. Widen a field or drop a weight to zero.
          </p>
        )}
      </section>
    </div>
  );
}
