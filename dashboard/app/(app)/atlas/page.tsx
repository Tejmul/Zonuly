"use client";

/*  Atlas.
 *
 *  It used to be a map of the market — 34 industry segments and a hairball of grey
 *  edges — which answered "what is out there". Nobody was asking that. The question
 *  is "given my resume and what I actually want, where should my hours go", and
 *  that is a computation.
 *
 *  So: your resume on the left, seven weighted terms in the middle, segments lighting
 *  up, companies ranked on the right, and every wire between them a real number out
 *  of lib/atlas-model.ts. Move a slider and watch the ranking move. Hover a company
 *  and the path that produced it stays lit while everything else drops away.
 *
 *  Filters and sliders are different tools now, and the page says so. The user
 *  changed the field and nothing moved — because the field was a fifth of a
 *  weighted mean and most of the top forty tied on everything else. Filters remove
 *  companies, with a count on every option so you can see what a click will leave;
 *  sliders only order what the filters let through. All of it lives in the URL, so
 *  a reload or a pasted link lands on the same list.
 */

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { useApi } from "@/lib/api";
import { AtlasNet, MotionList } from "@/components/atlas-net";
import { FundingCell } from "@/components/funding";
import { ErrorNote, Loading } from "@/components/ui/primitives";
import {
  DEFAULT_WEIGHTS, DIM_LABEL, FIELDS, MUSTS, STAGES, TERMS, WHERE, WITHIN,
  factsOf, filterAndCount, rank, resumeSignals, segmentHeat, suggestedFields,
  type Company, type FieldKey, type Filters, type Must, type Profile, type Stage,
  type TermKey, type Weights, type Where, type Within,
} from "@/lib/atlas-model";

type Atlas = { companies: Company[]; stats: Record<string, number> };

const PAGE = 60;

export default function AtlasPage() {
  // useSearchParams needs a Suspense boundary or the production build refuses a
  // statically prerendered page (node_modules/next/dist/docs, use-search-params).
  return (
    <Suspense fallback={<div className="plate"><Loading label="Reading the atlas" rows={10} /></div>}>
      <AtlasView />
    </Suspense>
  );
}

/* ---------------------------------------------------------------- the URL
   ?f=ai,infra&at=anywhere&stage=seed,series-a&within=12&must=fresher,lead&q=…&w=…
   `f` absent means "what your resume suggests"; `f=none` means "every field". */

type UrlState = {
  fields: FieldKey[] | null; where: Where; stages: Stage[]; within: Within; must: Must[]; q: string; w: Weights;
};

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9+]+/g, "-");
const list = (v: string | null) => (v ? v.split(",").filter(Boolean) : []);

function readUrl(p: URLSearchParams): UrlState {
  const f = p.get("f");
  const fields = f === null ? null : f === "none" ? [] : FIELDS.map((x) => x.key).filter((k) => list(f).includes(k));
  const at = p.get("at");
  const within = Number(p.get("within"));
  const ws = list(p.get("w")).map(Number);
  const w = { ...DEFAULT_WEIGHTS };
  if (ws.length === TERMS.length && ws.every((n) => Number.isFinite(n) && n >= 0 && n <= 1))
    TERMS.forEach((t, i) => { w[t.key] = ws[i]; });
  return {
    fields,
    where: WHERE.some((x) => x.key === at) ? (at as Where) : "any",
    stages: STAGES.filter((s) => list(p.get("stage")).includes(slug(s))),
    within: WITHIN.some((x) => x.key === within) ? (within as Within) : 0,
    must: MUSTS.map((m) => m.key).filter((k) => list(p.get("must")).includes(k)),
    q: p.get("q") ?? "",
    w,
  };
}

function writeUrl(s: UrlState): string {
  const p = new URLSearchParams();
  if (s.fields) p.set("f", s.fields.length ? s.fields.join(",") : "none");
  if (s.where !== "any") p.set("at", s.where);
  if (s.stages.length) p.set("stage", s.stages.map(slug).join(","));
  if (s.within) p.set("within", String(s.within));
  if (s.must.length) p.set("must", s.must.join(","));
  if (s.q.trim()) p.set("q", s.q.trim());
  if (TERMS.some((t) => s.w[t.key] !== DEFAULT_WEIGHTS[t.key])) p.set("w", TERMS.map((t) => s.w[t.key]).join(","));
  return p.toString();
}

/** The value, `ms` after it stopped changing. Dragging a slider or typing a name
 *  then costs one re-rank at the end, not one per pixel or keystroke. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

const REGION: Record<string, string> = {
  us: "US", remote: "Remote", india: "India", eu: "Europe", uk: "UK", de: "Germany", nl: "Netherlands",
};

function place(c: Company): string {
  if (c.anywhere > 0) return "Remote · anywhere";
  if (c.city) return c.city;
  return c.region ? REGION[c.region] ?? c.region.toUpperCase() : "—";
}

function AtlasView() {
  const params = useSearchParams();
  const net = useApi<Atlas>("/api/network");
  const prof = useApi<Profile>("/api/profile");

  // The URL is read once, on arrival; after that the page owns the state and only
  // writes it back. Reading it on every render would race our own writes.
  const [init] = useState(() => readUrl(new URLSearchParams(params.toString())));
  const [fields, setFields] = useState<FieldKey[] | null>(init.fields);
  const [where, setWhere] = useState<Where>(init.where);
  const [stages, setStages] = useState<Stage[]>(init.stages);
  const [within, setWithin] = useState<Within>(init.within);
  const [must, setMust] = useState<Must[]>(init.must);
  const [q, setQ] = useState(init.q);
  const [w, setW] = useState<Weights>(init.w);
  const [picked, setPicked] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE);

  // Until the resume lands, the opening position is read from it rather than
  // guessed — the page should never greet you with an empty board.
  const suggested = useMemo(() => suggestedFields(prof.data ?? null), [prof.data]);
  const chosen = fields ?? suggested;

  /* Shallow: replaceState is wired into the Next router (it keeps useSearchParams
     in sync) without a navigation, a refetch or a new history entry per click. */
  useEffect(() => {
    const qs = writeUrl({ fields, where, stages, within, must, q, w });
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
  }, [fields, where, stages, within, must, q, w]);

  const qd = useDebounced(q, 150);
  const wd = useDebounced(w, 120);

  // Facts once per payload; filters before scoring; scoring once per settled change.
  const facts = useMemo(() => (net.data?.companies ?? []).map((c) => factsOf(c)), [net.data]);
  const filters: Filters = useMemo(
    () => ({ fields: chosen, where, stages, within, must, q: qd }),
    [chosen, where, stages, within, must, qd],
  );
  const counts = useMemo(() => filterAndCount(facts, filters), [facts, filters]);
  const ranked = useMemo(() => rank(counts.matched, chosen, wd), [counts.matched, chosen, wd]);
  const heat = useMemo(() => segmentHeat(ranked), [ranked]);
  const inputs = useMemo(() => resumeSignals(prof.data ?? null), [prof.data]);

  const shown = ranked.slice(0, limit);
  const order = shown.map((r) => r.company.id).join("|");

  if (net.error && !net.data) return <div className="plate"><ErrorNote message={net.error} onRetry={net.reload} /></div>;
  if (!net.data) return <div className="plate"><Loading label="Reading the atlas" rows={10} /></div>;

  // Every filter change starts the list from the top page again.
  const change = <T,>(set: (v: T) => void) => (v: T) => { set(v); setLimit(PAGE); };
  const flip = <T,>(xs: T[], x: T) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x]);

  const toggleField = (k: FieldKey) => change(setFields)(flip(chosen, k));
  const anyFilter = chosen.length > 0 || where !== "any" || stages.length > 0 || within !== 0 || must.length > 0 || q.trim() !== "";
  const clearFilters = () => {
    setFields([]); setWhere("any"); setStages([]); setWithin(0); setMust([]); setQ(""); setLimit(PAGE);
  };
  const dirty = JSON.stringify(w) !== JSON.stringify(DEFAULT_WEIGHTS) || fields !== null;

  /* Which one filter, dropped, would bring back the most. */
  const loosen = (Object.entries(counts.without) as [keyof typeof DIM_LABEL, number][])
    .sort((a, b) => b[1] - a[1])[0];
  const drop = (d: keyof typeof DIM_LABEL) => {
    if (d === "field") setFields([]);
    else if (d === "where") setWhere("any");
    else if (d === "stage") setStages([]);
    else if (d === "within") setWithin(0);
    else if (d === "q") setQ("");
    else setMust((m) => m.filter((x) => x !== d));
    setLimit(PAGE);
  };

  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
          Atlas
        </h1>
        <p className="mt-1 max-w-[70ch] text-[12.5px] leading-relaxed text-ink-2">
          Filters decide which companies are in the room. Seven things get weighed to put
          them in order: your resume goes in on the left, segments light up, and the companies
          worth your hours come out on the right — each one traceable back through the wires
          that produced it.
        </p>
        {typeof net.data?.stats?.being_filled === "number" && net.data.stats.being_filled > 0 && (
          <p className="mt-1.5 text-[12px] text-ink-3">
            Complete companies only.{" "}
            <Link href="/companies?view=filling" className="underline decoration-line underline-offset-2 hover:decoration-ink">
              {net.data.stats.being_filled.toLocaleString()} being filled
            </Link>{" "}
            — each still missing a site, a description, hiring proof or a referrer with an address.
          </p>
        )}
      </header>

      {/* ----------------------------------------------------------- filters */}
      <section className="plate p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="eyebrow">What do you want to work on</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {FIELDS.map((f) => (
                <Chip
                  key={f.key} on={chosen.includes(f.key)} n={counts.field[f.key]}
                  onClick={() => toggleField(f.key)} title={f.blurb}
                >
                  {f.label}
                </Chip>
              ))}
            </div>
            <p className="mt-2 max-w-[62ch] text-[11px] leading-relaxed text-ink-3">
              {chosen.length
                ? `Only companies in these fields. ${FIELDS.filter((f) => chosen.includes(f.key)).map((f) => f.blurb).join(" ")}`
                : "Nothing picked, so every field is in."}
            </p>
          </div>

          {dirty && (
            <button
              type="button"
              onClick={() => { setW(DEFAULT_WEIGHTS); setFields(null); setLimit(PAGE); }}
              className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 text-[11.5px] text-ink-3 hover:text-ink"
            >
              <RotateCcw size={12} />
              Reset to what your resume suggests
            </button>
          )}
        </div>

        <div className="mt-4 grid gap-x-8 gap-y-3 border-t border-line pt-3 lg:grid-cols-2">
          <Row label="Where">
            <Segmented
              value={where} onChange={change(setWhere)}
              options={WHERE.map((x) => ({ key: x.key, label: x.label, n: counts.where[x.key] }))}
            />
          </Row>
          <Row label="Funded within">
            <Segmented
              value={within} onChange={change(setWithin)}
              options={WITHIN.map((x) => ({ key: x.key, label: x.label, n: counts.within[x.key] }))}
            />
          </Row>
          <Row label="Stage">
            {STAGES.map((s) => (
              <Chip key={s} on={stages.includes(s)} n={counts.stage[s]} onClick={() => change(setStages)(flip(stages, s))}>
                {s}
              </Chip>
            ))}
          </Row>
          <Row label="Must have">
            {MUSTS.map((m) => (
              <Chip key={m.key} on={must.includes(m.key)} n={counts.must[m.key]} onClick={() => change(setMust)(flip(must, m.key))}>
                {m.label}
              </Chip>
            ))}
          </Row>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3">
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }}
            placeholder="Find one"
            aria-label="Search companies"
            className="h-8 w-56 rounded-sm border border-line bg-surface px-2.5 text-[12px] outline-none placeholder:text-ink-3 focus:border-line-strong"
          />
          <p className="tnum text-[12px] text-ink-2" aria-live="polite">
            <span className="font-semibold text-ink">{counts.matched.length.toLocaleString()}</span>
            {" "}of {counts.total.toLocaleString()} companies match
          </p>
          {anyFilter && (
            <button
              type="button" onClick={clearFilters}
              className="cursor-pointer text-[11.5px] text-ink-3 underline-offset-2 hover:text-ink hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>

        <div className="mt-4 border-t border-line pt-3">
          <p className="eyebrow">How much each thing should count — orders what the filters leave</p>
          <div className="mt-2 grid gap-x-8 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
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
          weights={wd}
          fields={chosen}
          picked={picked}
          onPick={setPicked}
        />
      </section>

      {/* ----------------------------------------------------------- results */}
      <section className="plate">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <h2 className="text-[13px] font-semibold">
            {ranked.length.toLocaleString()} compan{ranked.length === 1 ? "y" : "ies"}, best first
          </h2>
          <span className="ml-auto text-[11px] text-ink-3">
            Score is the weighted sum above, out of 100
          </span>
        </div>

        <MotionList order={order} className="divide-y divide-line">
          {shown.map((r, i) => {
            const c = r.company;
            return (
              <li key={c.id} data-flip-id={String(c.id)} className="bg-surface">
                <Link
                  href={`/companies/${c.id}`}
                  onMouseEnter={() => setPicked(c.id)}
                  className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2 md:grid-cols-[2rem_minmax(0,13rem)_8.5rem_10.5rem_minmax(0,1fr)_auto]"
                >
                  <span className="tnum text-[11px] text-ink-3">{i + 1}</span>

                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate text-[12.5px] font-medium">{c.name}</span>
                      {c.list_key === "india-star" && <span className="text-[11px] text-ink" title="★ India list">★</span>}
                    </span>
                    <span className="block truncate text-[11px] text-ink-3">{c.segment_label}</span>
                  </span>

                  <span
                    className={`hidden truncate text-[11.5px] md:block ${c.anywhere > 0 ? "font-medium text-ink" : "text-ink-2"}`}
                  >
                    {place(c)}
                  </span>

                  <span className="hidden min-w-0 md:block">
                    <FundingCell row={c} />
                  </span>

                  {/* What carried the score, in words. */}
                  <span className="hidden min-w-0 flex-wrap items-center gap-1.5 md:flex">
                    {r.top.map((k) => (
                      <span key={k} className="chip" title={r.terms[k].why}>
                        {TERMS.find((t) => t.key === k)?.label}
                      </span>
                    ))}
                  </span>

                  <span className="flex items-center gap-3">
                    <span className="bar-track hidden h-[8px] w-16 overflow-hidden sm:block">
                      <span className="atlas-bar" style={{ transform: `scaleX(${Math.max(0.02, r.score)})` }} />
                    </span>
                    <span className="tnum w-8 text-right text-[12px]">{Math.round(r.score * 100)}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </MotionList>

        {ranked.length > shown.length && (
          <div className="border-t border-line px-4 py-3">
            <button
              type="button" onClick={() => setLimit((n) => n + PAGE)}
              className="cursor-pointer rounded-md border border-line px-3 py-1.5 text-[12px] text-ink-2 hover:border-line-strong hover:text-ink"
            >
              Show {Math.min(PAGE, ranked.length - shown.length)} more
              <span className="tnum ml-1.5 text-ink-3">of {(ranked.length - shown.length).toLocaleString()} left</span>
            </button>
          </div>
        )}

        {ranked.length === 0 && (
          <div className="px-4 py-6 text-[12px] text-ink-3">
            <p>Nothing matches every filter at once.</p>
            {loosen && loosen[1] > 0 ? (
              <p className="mt-1">
                Loosen <span className="text-ink">{DIM_LABEL[loosen[0]]}</span> — {loosen[1].toLocaleString()}{" "}
                compan{loosen[1] === 1 ? "y matches" : "ies match"} without it.{" "}
                <button
                  type="button" onClick={() => drop(loosen[0])}
                  className="cursor-pointer text-ink underline underline-offset-2"
                >
                  Drop it
                </button>
              </p>
            ) : (
              <p className="mt-1">No single filter is the problem — clear a few, or start over.</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow">{label}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

/** A toggle with the number it would leave. A zero is shown, and greyed, so a dead
 *  end is visible before you walk into it. */
function Chip({
  on, n, onClick, title, children,
}: { on: boolean; n: number; onClick: () => void; title?: string; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      title={title}
      className={`cursor-pointer rounded-md border px-2.5 py-1.5 text-[12px] transition-colors ${
        on
          ? "border-ink bg-ink text-on-ink"
          : n === 0
            ? "border-line text-ink-3 opacity-60 hover:opacity-100"
            : "border-line text-ink-2 hover:border-line-strong hover:text-ink"
      }`}
    >
      {children}
      <span className={`tnum ml-1.5 text-[11px] ${on ? "opacity-70" : "text-ink-3"}`}>{n.toLocaleString()}</span>
    </button>
  );
}

/** One of several, with counts — the same shape as the city switch on Companies. */
function Segmented<K extends string | number>({
  value, onChange, options,
}: { value: K; onChange: (k: K) => void; options: { key: K; label: string; n: number }[] }) {
  return (
    <div className="flex max-w-full flex-wrap rounded-md border border-line p-px" role="group">
      {options.map((o) => (
        <button
          key={String(o.key)}
          type="button"
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={`cursor-pointer rounded-[5px] px-2.5 py-1.5 text-[12px] transition-colors ${
            value === o.key ? "bg-ink font-medium text-on-ink" : o.n === 0 ? "text-ink-3" : "text-ink-2 hover:text-ink"
          }`}
        >
          {o.label}
          <span className={`tnum ml-1.5 text-[11px] ${value === o.key ? "opacity-70" : "text-ink-3"}`}>
            {o.n.toLocaleString()}
          </span>
        </button>
      ))}
    </div>
  );
}
