"use client";

/*  Graph — the knowledge graph as a star atlas (2026-10-14).
 *
 *  Tejmul: "Why can't I see the knowledge graph? Show it in the UI, like ChokePoints …
 *  I want to directly see which companies are still pending — what I still need to scrape."
 *  Then: "it feels like a small kid drew something on paper. Make it a lot better."
 *
 *  One job: show where the good bets are and what is still unfinished, at a glance.
 *    Atlas     you at the centre; angle = industry (layers → segments), distance = how
 *              good a bet. Filled stars are complete; rings are still filling.
 *    Pipeline  the same stars, travelled into one column per stage they reached
 *    Project   the context layer on the atlas grammar: built near the centre, open at the rim
 *
 *  Data: /api/graph/map (jobhunter/graphmap.py). Atlas and Pipeline are both fetched so
 *  the switch between them can move every star rather than swap pictures.
 *  Everything that changes what you see is in the URL: ?view=&node=&pending=1&m=&star=1&part=&q=
 */

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { gsap } from "gsap";
import { ArrowUpRight, Loader2, Search, X } from "lucide-react";
import { api, useApi, type Task } from "@/lib/api";
import { GraphCanvas, type Mode } from "@/components/graph-canvas";
import { OperatorOnly } from "@/components/access";
import { ErrorNote } from "@/components/ui/primitives";
import {
  LEGEND, MAG_R, PART_LABEL, DEFAULT_STATE, paintStar, readPalette, styleForNode,
  type Agg, type Bucket, type GraphMap, type LegendEntry, type Leaf, type ViewKey,
} from "@/lib/graph-style";

export default function GraphPage() {
  // useSearchParams needs a Suspense boundary (node_modules/next/dist/docs, use-search-params)
  return (
    <Suspense fallback={<MapFrame><Calm busy>Reading the graph…</Calm></MapFrame>}>
      <GraphView />
    </Suspense>
  );
}

/* ---------------------------------------------------------------- the URL */

type Market = "india" | "remote";
type UrlState = { view: ViewKey; node: string | null; pending: boolean; markets: Market[]; star: boolean; part: string | null; q: string };

const VIEWS: { key: ViewKey; label: string; hint: string; mode: Mode }[] = [
  { key: "market", label: "Atlas", hint: "You at the centre: industry round the dial, the best bets nearest you", mode: "atlas" },
  { key: "pipeline", label: "Pipeline", hint: "How far each company got, one column per stage", mode: "transit" },
  { key: "project", label: "Project", hint: "Stages, features, gaps and decisions from the context layer", mode: "project" },
];

function readUrl(p: URLSearchParams): UrlState {
  const v = p.get("view");
  return {
    view: VIEWS.some((x) => x.key === v) ? (v as ViewKey) : "market",
    node: p.get("node"),
    pending: p.get("pending") === "1",
    markets: (p.get("m") ?? "").split(",").filter((m): m is Market => m === "india" || m === "remote"),
    star: p.get("star") === "1",
    part: p.get("part"),
    q: p.get("q") ?? "",
  };
}

function writeUrl(s: UrlState): string {
  const p = new URLSearchParams();
  if (s.view !== "market") p.set("view", s.view);
  if (s.node) p.set("node", s.node);
  if (s.pending) p.set("pending", "1");
  if (s.markets.length) p.set("m", s.markets.join(","));
  if (s.star) p.set("star", "1");
  if (s.part) p.set("part", s.part);
  if (s.q.trim()) p.set("q", s.q.trim());
  return p.toString();
}

const fmt = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");
const still = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Pending first: ★ in list order, then by priority. */
const pendingOrder = (a: Leaf, b: Leaf) =>
  Number(!!b.star) - Number(!!a.star) || (a.list_rank ?? 9999) - (b.list_rank ?? 9999) || (b.priority ?? 0) - (a.priority ?? 0)
  || a.label.localeCompare(b.label);

/* ---------------------------------------------------------------- the page */

function GraphView() {
  const params = useSearchParams();
  const [init] = useState(() => readUrl(new URLSearchParams(params.toString())));
  const [view, setView] = useState<ViewKey>(init.view);
  const [node, setNode] = useState<string | null>(init.node);
  const [pending, setPending] = useState(init.pending);
  const [markets, setMarkets] = useState<Market[]>(init.markets);
  const [star, setStar] = useState(init.star);
  const [part, setPart] = useState<string | null>(init.part);
  const [q, setQ] = useState(init.q);

  const isCompanies = view !== "project";
  // both company payloads, so Atlas ⇄ Pipeline can move the stars rather than swap pictures
  const market = useApi<GraphMap>(isCompanies ? "/api/graph/map?view=market" : null);
  const pipe = useApi<GraphMap>(isCompanies ? "/api/graph/map?view=pipeline" : null);
  const proj = useApi<GraphMap>(isCompanies ? null : "/api/graph/map?view=project");
  const current = view === "market" ? market : view === "pipeline" ? pipe : proj;
  // the payload held may still be another view's while this one loads
  const data = current.data && current.data.view === view ? current.data : null;
  const atlasData = market.data?.view === "market" ? market.data : null;
  const transitData = pipe.data?.view === "pipeline" ? pipe.data : null;
  const projectData = proj.data?.view === "project" ? proj.data : null;

  useEffect(() => {
    const qs = writeUrl({ view, node, pending, markets, star, part, q });
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
  }, [view, node, pending, markets, star, part, q]);

  // Escape lets go of the selection
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target instanceof HTMLInputElement)) setNode(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---- filters: which leaves are shown, and the roll-up of what is shown
  const shownLeaves = useMemo(() => {
    if (!data) return [];
    return data.leaves.filter((l) => {
      if (pending && l.status === "complete") return false;
      if (part && !(l.missing ?? []).includes(part)) return false;
      if (isCompanies && star && !l.star) return false;
      if (isCompanies && markets.length && !markets.includes(l.market as Market)) return false;
      return true;
    });
  }, [data, pending, part, star, markets, isCompanies]);
  const shown = useMemo(() => new Set(shownLeaves.map((l) => l.id)), [shownLeaves]);

  const agg = useMemo(() => {
    const out = new Map<string, Agg>();
    if (!data) return out;
    for (const b of [...data.hubs, ...data.subs]) out.set(b.id, { count: 0, complete: 0, pending: 0, star_pending: 0 });
    for (const l of shownLeaves) {
      for (const id of [l.hub, l.sub]) {
        const a = id ? out.get(id) : undefined;
        if (!a) continue;
        a.count++;
        if (l.status === "complete") a.complete++;
        else {
          a.pending++;
          if (l.star) a.star_pending++;
        }
      }
    }
    return out;
  }, [data, shownLeaves]);

  const qd = useDebounced(q, 160);
  const matches = useMemo(() => {
    const s = qd.trim().toLowerCase();
    if (!s || !data) return null;
    return new Set(data.leaves.filter((l) => l.label.toLowerCase().includes(s)).map((l) => l.id));
  }, [qd, data]);

  const counts = useMemo(() => {
    const pend = shownLeaves.filter((l) => l.status !== "complete");
    const miss: Record<string, number> = {};
    for (const l of pend) for (const p of l.missing ?? []) miss[p] = (miss[p] ?? 0) + 1;
    return {
      total: shownLeaves.length, complete: shownLeaves.length - pend.length, filling: pend.length, miss,
      star: pend.filter((l) => l.star).length, india: pend.filter((l) => l.market === "india").length,
    };
  }, [shownLeaves]);

  const reloadMarket = market.reload;
  const reloadPipe = pipe.reload;
  const reloadAll = useCallback(() => { void reloadMarket(); void reloadPipe(); }, [reloadMarket, reloadPipe]);
  const fill = useFill(reloadAll);

  const changeView = (v: ViewKey) => {
    if (v === view) return;
    const bothCompanies = v !== "project" && isCompanies;
    setView(v);
    // a company stays picked across Atlas ⇄ Pipeline — it is the same star
    if (!bothCompanies || !node?.startsWith("company:")) setNode(null);
    if (!bothCompanies) setPart(null);
  };
  const flip = (m: Market) => setMarkets((xs) => (xs.includes(m) ? xs.filter((x) => x !== m) : [...xs, m]));
  const anyFilter = pending || star || markets.length > 0 || part !== null;

  const searchFirst = () => {
    if (!matches || !data) return;
    const hit = data.leaves.filter((l) => matches.has(l.id)).sort(pendingOrder)[0];
    if (hit) setNode(hit.id);
  };

  const byId = useMemo(() => {
    const m = new Map<string, { kind: "leaf"; v: Leaf } | { kind: "bucket"; v: Bucket; tier: "hub" | "sub" }>();
    if (!data) return m;
    for (const h of data.hubs) m.set(h.id, { kind: "bucket", v: h, tier: "hub" });
    for (const s of data.subs) m.set(s.id, { kind: "bucket", v: s, tier: "sub" });
    for (const l of data.leaves) m.set(l.id, { kind: "leaf", v: l });
    return m;
  }, [data]);
  const picked = node ? byId.get(node) ?? null : null;

  const noun = isCompanies ? "companies" : "items";
  const summary = !data
    ? "Reading the graph…"
    : isCompanies
      ? [`${fmt(counts.total)} ${noun}`, `${fmt(counts.complete)} complete`,
        counts.india ? `${fmt(counts.india)} India still filling` : `${fmt(counts.filling)} still filling`,
        counts.star ? `${fmt(counts.star)} ★ still filling` : ""].filter(Boolean).join(" · ")
      : `${fmt(counts.total)} ${noun} · ${fmt(counts.complete)} built or adopted · ${fmt(counts.filling)} still to build`;
  const mode = VIEWS.find((v) => v.key === view)!.mode;
  const aria = `${VIEWS.find((v) => v.key === view)!.label} chart. ${summary}. ${
    mode === "atlas" ? "Industries run round the dial; the nearer a company is to the centre, the stronger the bet."
      : mode === "transit" ? "One column per pipeline stage, strongest bet first."
        : "Stages round the dial; built work near the centre, open work at the rim."
  } The list beside the chart holds the same items.`;

  return (
    <div className="flex flex-col gap-3">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Graph</h1>
          <p className="tnum mt-1 text-[12px] text-ink-2" aria-live="polite">{summary}</p>
        </div>
        <div role="tablist" aria-label="Chart" className="flex rounded-lg border border-line bg-surface p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              role="tab"
              aria-selected={view === v.key}
              title={v.hint}
              onClick={() => changeView(v.key)}
              className={`h-7 cursor-pointer rounded-md px-3 text-[12.5px] font-medium transition-colors ${
                view === v.key ? "bg-ink text-on-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </header>

      {/* ---- the toolbar: filters and search */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <Toggle on={pending} onClick={() => setPending(!pending)}>Show only unfinished</Toggle>
        {isCompanies ? (
          <>
            <Toggle on={markets.includes("india")} onClick={() => flip("india")}>India</Toggle>
            <Toggle on={markets.includes("remote")} onClick={() => flip("remote")}>Remote</Toggle>
            <Toggle on={star} onClick={() => setStar(!star)}>★ list</Toggle>
          </>
        ) : null}
        {data ? (
          <div role="group" aria-label="Show only those missing" className="flex flex-wrap items-center gap-1.5">
            <span className="marginal ml-1 mr-0.5">Missing</span>
            {data.parts.map((p) => (
              <button
                key={p.key}
                aria-pressed={part === p.key}
                onClick={() => setPart(part === p.key ? null : p.key)}
                className={`h-7 cursor-pointer rounded-md border px-2 text-[12px] transition-colors ${
                  part === p.key ? "border-ink bg-ink text-on-ink" : "border-transparent text-ink-2 hover:border-line hover:text-ink"
                }`}
              >
                {PART_LABEL[p.key] ?? p.label} <span className="tnum text-[11px] opacity-70">{fmt(counts.miss[p.key] ?? 0)}</span>
              </button>
            ))}
          </div>
        ) : null}
        {anyFilter ? (
          <button
            onClick={() => { setPending(false); setMarkets([]); setStar(false); setPart(null); }}
            className="h-7 cursor-pointer px-1.5 text-[12px] text-ink-3 underline-offset-2 hover:text-ink hover:underline"
          >
            Clear filters
          </button>
        ) : null}
        <label className="relative w-full sm:ml-auto sm:w-[240px]">
          <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") searchFirst(); }}
            placeholder={isCompanies ? "Find a company — Enter to go there" : "Find a feature or gap"}
            aria-label="Search the chart"
            className="h-8 w-full rounded-lg border border-line bg-surface pr-16 pl-8 text-[12.5px] outline-none placeholder:text-ink-3 focus:border-ink"
          />
          {matches ? (
            <span className="tnum pointer-events-none absolute top-1/2 right-7 -translate-y-1/2 text-[10.5px] text-ink-3">{fmt(matches.size)}</span>
          ) : null}
          {q ? (
            <button aria-label="Clear search" onClick={() => setQ("")} className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer text-ink-3 hover:text-ink">
              <X size={14} />
            </button>
          ) : null}
        </label>
      </div>

      {/* ---- the chart + the panel */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <MapFrame>
            {data ? (
              data.leaves.length === 0 && data.hubs.length === 0 ? (
                <Calm>
                  {view === "project"
                    ? "The context layer is empty. Run `python scripts/run.py kg build` to load knowledge/context.yaml."
                    : "No companies yet. The tick fills this chart as it scrapes."}
                </Calm>
              ) : (
                <>
                  <GraphCanvas
                    mode={mode}
                    atlas={atlasData}
                    transit={transitData}
                    project={projectData}
                    shown={shown}
                    matches={matches}
                    agg={agg}
                    selected={node}
                    onSelect={setNode}
                    summary={aria}
                  />
                  {shownLeaves.length === 0 ? (
                    <div className="pointer-events-none absolute inset-x-0 top-4 flex justify-center">
                      <span className="rounded-md border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2">Nothing passes these filters.</span>
                    </div>
                  ) : null}
                </>
              )
            ) : current.error ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <ErrorNote message={current.error} onRetry={current.reload} title="The graph can’t be loaded right now" />
              </div>
            ) : (
              <Calm busy>Reading the graph…</Calm>
            )}
            {current.loading && data ? (
              <span className="absolute top-3 right-3 flex items-center gap-1.5 text-[11px] text-ink-3">
                <Loader2 size={12} className="animate-spin motion-reduce:animate-none" /> refreshing
              </span>
            ) : null}
          </MapFrame>
          <Legend mode={mode} />
        </div>

        <Panel open={!!picked} onClose={() => setNode(null)} panelKey={node ?? "overview"}>
          {!data ? (
            <p className="text-[12.5px] text-ink-3">Waiting for the chart…</p>
          ) : picked?.kind === "leaf" ? (
            picked.v.kind === "company" ? (
              <CompanyCard leaf={picked.v} data={data} fill={fill} onPick={setNode} />
            ) : (
              <ProjectCard leaf={picked.v} data={data} onPick={setNode} />
            )
          ) : picked?.kind === "bucket" ? (
            <BucketCard
              bucket={picked.v}
              tier={picked.tier}
              agg={agg.get(picked.v.id)}
              leaves={shownLeaves.filter((l) => l.hub === picked.v.id || l.sub === picked.v.id)}
              companies={isCompanies}
              fill={fill}
              onPick={setNode}
            />
          ) : (
            <Overview data={data} agg={agg} leaves={shownLeaves} companies={isCompanies} fill={fill} onPick={setNode} />
          )}
        </Panel>
      </div>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/* ---------------------------------------------------------------- fill now */

type FillResult = { companies?: number; missing?: Record<string, number>; errors?: number; error_samples?: string[]; seconds?: number; linkedin?: string; stopped?: string };
type FillState = { task: string | null; status: "starting" | "running" | "done" | "error"; started: number; result?: FillResult; error?: string };
type Fill = { state: Record<number, FillState>; start: (cid: number) => void };

function useFill(onDone: () => void): Fill {
  const [state, setState] = useState<Record<number, FillState>>({});
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((t) => clearTimeout(t)), []);

  const start = useCallback((cid: number) => {
    setState((s) => ({ ...s, [cid]: { task: null, status: "starting", started: Date.now() } }));
    api.post<{ task_id: string }>(`/api/companies/${cid}/fill`).then(
      ({ task_id }) => {
        setState((s) => ({ ...s, [cid]: { ...s[cid], task: task_id, status: "running" } }));
        const poll = () => {
          api.get<Task>(`/api/tasks/${task_id}`).then(
            (t) => {
              if (t.status === "running") {
                timers.current.push(window.setTimeout(poll, 2500));
                return;
              }
              setState((s) => ({
                ...s,
                [cid]: { ...s[cid], status: t.status === "done" ? "done" : "error", result: t.result as FillResult, error: t.error },
              }));
              onDone();
            },
            (e: unknown) => setState((s) => ({ ...s, [cid]: { ...s[cid], status: "error", error: String(e instanceof Error ? e.message : e) } })),
          );
        };
        timers.current.push(window.setTimeout(poll, 1500));
      },
      (e: unknown) => setState((s) => ({ ...s, [cid]: { ...s[cid], status: "error", error: String(e instanceof Error ? e.message : e) } })),
    );
  }, [onDone]);

  return { state, start };
}

function FillButton({ cid, fill, size = "md" }: { cid: number; fill: Fill; size?: "sm" | "md" }) {
  const st = fill.state[cid];
  const busy = st?.status === "starting" || st?.status === "running";
  // the clock is state, ticked by the interval, so render stays pure
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [busy]);
  const secs = st && now > st.started ? Math.round((now - st.started) / 1000) : 0;
  return (
    <OperatorOnly>
      <div className={size === "sm" ? "shrink-0" : "space-y-1.5"}>
        <button
          onClick={(e) => { e.stopPropagation(); fill.start(cid); }}
          disabled={busy}
          className={
            size === "sm"
              ? "inline-flex h-6 cursor-pointer items-center gap-1 rounded-md border border-line-strong px-2 text-[11px] font-medium hover:border-ink disabled:cursor-default disabled:opacity-60"
              : "btn-ink w-full disabled:cursor-default disabled:opacity-60"
          }
        >
          {busy ? <Loader2 size={size === "sm" ? 11 : 14} className="animate-spin motion-reduce:animate-none" /> : null}
          {busy ? `Filling ${secs}s` : st?.status === "done" ? "Fill again" : "Fill now"}
        </button>
        {size === "md" && st ? <FillOutcome st={st} /> : null}
      </div>
    </OperatorOnly>
  );
}

function FillOutcome({ st }: { st: FillState }) {
  if (st.status === "starting" || st.status === "running")
    return <p className="text-[11.5px] leading-relaxed text-ink-3">Reading their site, LinkedIn, careers page and people. This takes a minute or two; the map refreshes when it lands.</p>;
  if (st.status === "error") return <p className="text-[11.5px] text-ink-2">Fill failed: {st.error ?? "unknown error"}</p>;
  const r = st.result ?? {};
  const left = Object.keys(r.missing ?? {});
  return (
    <p className="text-[11.5px] leading-relaxed text-ink-2">
      Filled in {r.seconds ?? "?"}s.{" "}
      {r.errors ? `It hit an error: ${r.error_samples?.[0] ?? ""}` : left.length ? `Still missing: ${left.map((k) => PART_LABEL[k] ?? k).join(", ")}.` : "Nothing left missing."}
      {r.stopped ? ` Stopped: ${r.stopped}.` : ""}
    </p>
  );
}

/* ---------------------------------------------------------------- panel cards
   Typography, not boxes: a kicker, a name, a sentence of state, hairline-ruled facts. */

function CompanyCard({ leaf, data, fill, onPick }: { leaf: Leaf; data: GraphMap; fill: Fill; onPick: (id: string) => void }) {
  const hub = data.hubs.find((h) => h.id === leaf.hub);
  const sub = data.subs.find((s) => s.id === leaf.sub);
  const peers = data.investors.filter((i) => i.companies.includes(leaf.id));
  const names = new Map(data.leaves.map((l) => [l.id, l.label]));
  const pending = leaf.status !== "complete";
  const miss = leaf.missing ?? [];
  const where = leaf.city ?? (leaf.market === "india" ? "India" : leaf.market === "remote" ? "Remote" : leaf.market === "abroad" ? "Abroad" : null);
  return (
    <div className="space-y-5">
      <div>
        <div className="marginal">{[hub?.label, sub?.label].filter(Boolean).join(" · ")}</div>
        <h2 className="mt-1.5 text-[17px] font-semibold tracking-[-0.01em]">
          {leaf.star ? <span title="On the ★ India list">★ </span> : null}{leaf.label}
        </h2>
        <p className="mt-1 text-[12.5px] text-ink-2">
          {pending ? `Still filling — ${miss.length} of 4 parts missing` : "Complete — ready for a referral ask"}
          {where ? ` · ${where}` : ""}{leaf.drafted ? " · drafted" : ""}
        </p>
      </div>

      {pending ? (
        <section>
          <h3 className="marginal mb-2">What is missing</h3>
          <ul className="space-y-2.5">
            {miss.map((p) => (
              <li key={p} className="text-[12.5px] leading-snug">
                <span className="font-medium">{PART_LABEL[p] ?? p}</span>
                <span className="block text-[11.5px] text-ink-3">
                  {leaf.reasons?.[p] ?? (leaf.filled_at ? "no reason recorded by the fill pass" : "not looked at yet — the fill pass has not reached this company")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {leaf.cid && pending ? <FillButton cid={leaf.cid} fill={fill} /> : null}

      <dl className="divide-y divide-line border-y border-line text-[12px]">
        <Fact k="Bet strength" v={leaf.best_selection != null ? `${leaf.best_selection}${leaf.best_selection_band ? ` · ${leaf.best_selection_band}` : ""}` : "—"} />
        <Fact k="Priority" v={leaf.priority ?? "—"} />
        <Fact k="Referrers ready" v={`${fmt(leaf.referrers)} · ${fmt(leaf.referrers_email)} with email`} />
        <Fact k="Roles" v={`${fmt(leaf.roles)} stored${leaf.open_roles != null ? ` · ${fmt(leaf.open_roles)} open` : ""}`} />
        <Fact k="Last filled" v={leaf.filled_at ? leaf.filled_at.replace("T", " ").slice(0, 16) : "never"} />
      </dl>

      {leaf.notes && Object.keys(leaf.notes).length ? (
        <section>
          <h3 className="marginal mb-1.5">Also noted</h3>
          <ul className="space-y-1">
            {Object.entries(leaf.notes).map(([k, v]) => (
              <li key={k} className="text-[11.5px] text-ink-2"><span className="text-ink">{k}</span> — {v}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {peers.length ? (
        <section>
          <h3 className="marginal mb-1.5">Shares an investor with</h3>
          <ul className="space-y-1">
            {peers.map((i) => (
              <li key={i.id} className="text-[12px]">
                <span className="text-ink-2">{i.label}: </span>
                {i.companies.filter((c) => c !== leaf.id).map((c, n) => (
                  <span key={c}>
                    {n ? ", " : ""}
                    <button onClick={() => onPick(c)} className="cursor-pointer underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                      {names.get(c) ?? c}
                    </button>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="space-y-2">
        {leaf.cid && !pending ? <FillButton cid={leaf.cid} fill={fill} /> : null}
        {leaf.cid ? (
          <Link href={`/companies/${leaf.cid}`} className="inline-flex items-center gap-1 text-[12.5px] font-medium underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Open the company page <ArrowUpRight size={13} />
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function ProjectCard({ leaf, data, onPick }: { leaf: Leaf; data: GraphMap; onPick: (id: string) => void }) {
  const done = leaf.status === "complete";
  const names = new Map<string, string>([...data.hubs.map((h) => [h.id, h.label] as const), ...data.leaves.map((l) => [l.id, l.label] as const)]);
  const rel = data.links.filter((l) => l.kind !== "chain" && (l.source === leaf.id || l.target === leaf.id));
  return (
    <div className="space-y-4">
      <div>
        <div className="marginal">{leaf.kind} · {names.get(leaf.hub) ?? leaf.hub}</div>
        <h2 className="mt-1.5 text-[17px] font-semibold tracking-[-0.01em]">{leaf.label}</h2>
        <p className="mt-1 text-[12.5px] text-ink-2">
          {leaf.kind === "decision" ? "Adopted" : done ? "Built" : PART_LABEL[leaf.feature_status ?? ""] ?? leaf.feature_status ?? "Open"}
        </p>
      </div>
      {leaf.summary ? <p className="text-[12.5px] leading-relaxed text-ink-2">{leaf.summary}</p> : null}
      {rel.length ? (
        <section>
          <h3 className="marginal mb-1.5">Linked to</h3>
          <ul className="space-y-1 text-[12px]">
            {rel.map((l) => {
              const other = l.source === leaf.id ? l.target : l.source;
              const isLeaf = data.leaves.some((x) => x.id === other);
              return (
                <li key={`${l.kind}-${other}`}>
                  <span className="text-ink-3">{l.kind.replace("_", " ")} · </span>
                  {isLeaf ? (
                    <button onClick={() => onPick(other)} className="cursor-pointer underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                      {names.get(other) ?? other}
                    </button>
                  ) : names.get(other) ?? other}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      <p className="text-[11px] text-ink-3">
        From knowledge/context.yaml. <code className="tnum">python scripts/run.py kg show {leaf.id}</code> lists everything it links to.
      </p>
    </div>
  );
}

function BucketCard({
  bucket, tier, agg, leaves, companies, fill, onPick,
}: {
  bucket: Bucket; tier: "hub" | "sub"; agg?: Agg; leaves: Leaf[]; companies: boolean; fill: Fill; onPick: (id: string) => void;
}) {
  const pend = leaves.filter((l) => l.status !== "complete").sort(pendingOrder);
  const a = agg ?? { count: 0, complete: 0, pending: 0, star_pending: 0 };
  const miss: Record<string, number> = {};
  for (const l of pend) for (const p of l.missing ?? []) miss[p] = (miss[p] ?? 0) + 1;
  return (
    <div className="space-y-4">
      <div>
        <div className="marginal">{tier === "hub" ? bucket.code ?? "Layer" : "Segment"}</div>
        <h2 className="mt-1.5 text-[17px] font-semibold tracking-[-0.01em]">{bucket.label}</h2>
        {bucket.blurb ? <p className="mt-1 text-[12px] leading-relaxed text-ink-2">{bucket.blurb}</p> : null}
      </div>
      <p className="tnum text-[12px] text-ink-2">
        {fmt(a.count)} shown · {fmt(a.complete)} complete · {fmt(a.pending)} {companies ? "still filling" : "not built"}
        {a.star_pending ? ` · ★ ${fmt(a.star_pending)} pending` : ""}
      </p>
      {Object.keys(miss).length ? (
        <p className="text-[11.5px] leading-relaxed text-ink-3">
          Missing: {Object.entries(miss).sort((x, y) => y[1] - x[1]).map(([k, n]) => `${PART_LABEL[k] ?? k} ${fmt(n)}`).join(" · ")}
        </p>
      ) : null}
      <PendingList title={companies ? "Unfinished here — ★ first, then priority" : "Not built here"} leaves={pend} fill={companies ? fill : null} onPick={onPick} />
    </div>
  );
}

function Overview({ data, agg, leaves, companies, fill, onPick }: {
  data: GraphMap; agg: Map<string, Agg>; leaves: Leaf[]; companies: boolean; fill: Fill; onPick: (id: string) => void;
}) {
  const pend = leaves.filter((l) => l.status !== "complete").sort(pendingOrder);
  const sectors = data.hubs.filter((h) => (h.row ?? 0) === 0);
  return (
    <div className="space-y-5">
      <div>
        <div className="marginal">Overview · {data.generated_at.replace("T", " ").slice(0, 16)}</div>
        <h2 className="mt-1.5 text-[17px] font-semibold tracking-[-0.01em]">
          {companies ? "What is still unfinished" : "What is still to build"}
        </h2>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-2">
          {companies
            ? "Pick a star, a sector, or a name below. Unfinished companies are listed ★ India first, then by priority."
            : "Features not built yet and open gaps, from the context layer."}
        </p>
      </div>
      <section>
        <h3 className="marginal mb-1.5">{data.view === "pipeline" ? "Stages" : data.view === "project" ? "Stages" : "Industries"}</h3>
        <ul className="divide-y divide-line border-y border-line">
          {sectors.map((h) => {
            const a = agg.get(h.id);
            return (
              <li key={h.id}>
                <button onClick={() => onPick(h.id)} className="flex w-full cursor-pointer items-baseline gap-2 py-1.5 text-left text-[12.5px] hover:text-ink">
                  <span className="min-w-0 flex-1 truncate">{h.label}</span>
                  {a?.star_pending ? <span className="tnum text-[11px] text-ink-2">★{a.star_pending}</span> : null}
                  <span className="tnum text-[11px] text-ink-3">{fmt(a?.count ?? 0)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <PendingList title={`${fmt(pend.length)} unfinished`} leaves={pend} fill={companies ? fill : null} onPick={onPick} />
    </div>
  );
}

function PendingList({ title, leaves, fill, onPick }: { title: string; leaves: Leaf[]; fill: Fill | null; onPick: (id: string) => void }) {
  const [n, setN] = useState(40);
  if (!leaves.length) return <p className="text-[12px] text-ink-3">Nothing unfinished here.</p>;
  return (
    <section>
      <h3 className="marginal mb-1.5">{title}</h3>
      <ul className="divide-y divide-line border-y border-line">
        {leaves.slice(0, n).map((l) => (
          <li key={l.id} className="flex items-center gap-2 py-1.5">
            <button onClick={() => onPick(l.id)} className="min-w-0 flex-1 cursor-pointer text-left">
              <span className="block truncate text-[12.5px] font-medium">{l.star ? "★ " : ""}{l.label}</span>
              <span className="block truncate text-[11px] text-ink-3">
                {(l.missing ?? []).map((p) => PART_LABEL[p] ?? p).join(" · ") || l.feature_status}
                {l.priority != null ? ` · p${l.priority}` : ""}
              </span>
            </button>
            {fill && l.cid ? <FillButton cid={l.cid} fill={fill} size="sm" /> : null}
          </li>
        ))}
      </ul>
      {leaves.length > n ? (
        <button onClick={() => setN(n + 80)} className="mt-2 cursor-pointer text-[12px] text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          Show more ({fmt(leaves.length - n)} left)
        </button>
      ) : null}
    </section>
  );
}

/* ---------------------------------------------------------------- frame, panel, small parts */

function MapFrame({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-[calc(100dvh-240px)] max-h-[960px] min-h-[420px] overflow-hidden rounded-xl border border-line bg-surface">
      {children}
    </div>
  );
}

function Calm({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <div role="status" aria-live="polite" className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
      {busy ? (
        <div aria-hidden className="flex gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <span key={i} className="h-2.5 w-2.5 animate-pulse rounded-full border border-ink-3 motion-reduce:animate-none" style={{ animationDelay: `${i * 140}ms` }} />
          ))}
        </div>
      ) : null}
      <p className="max-w-[52ch] text-[12.5px] text-ink-2">{children}</p>
    </div>
  );
}

/** Beside the chart on a laptop; a bottom sheet on a phone, only while something is picked. */
function Panel({ open, onClose, panelKey, children }: { open: boolean; onClose: () => void; panelKey: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || still()) return;
    gsap.fromTo(ref.current, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.25, ease: "power2.out" });
  }, [panelKey]);
  return (
    <aside
      aria-label="Details"
      className={`${open ? "fixed" : "hidden"} inset-x-0 bottom-0 z-40 max-h-[62dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface px-4 pt-3 pb-6
        lg:static lg:block lg:max-h-[calc(100dvh-240px)] lg:rounded-none lg:border-t-0 lg:border-l lg:bg-transparent lg:px-0 lg:pt-0 lg:pl-5`}
    >
      <div className="mb-1 flex justify-end">
        {open ? (
          <button onClick={onClose} aria-label="Close details" className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-ink-3 hover:bg-surface-2 hover:text-ink">
            <X size={15} />
          </button>
        ) : <span className="h-7" />}
      </div>
      <div ref={ref}>{children}</div>
    </aside>
  );
}

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      aria-pressed={on}
      onClick={onClick}
      className={`h-7 cursor-pointer rounded-md border px-2.5 text-[12px] font-medium transition-colors ${
        on ? "border-ink bg-ink text-on-ink" : "border-line bg-surface text-ink-2 hover:border-line-strong hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="text-ink-3">{k}</dt>
      <dd className="tnum text-right text-[12px]">{v}</dd>
    </div>
  );
}

/* The key: every glyph painted by styleForNode + paintStar, the canvas's own pair. */
function Legend({ mode }: { mode: Mode }) {
  const companies = mode !== "project";
  return (
    <ul aria-label="Key" className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-ink-2">
      {LEGEND.filter((e) => companies || !e.companiesOnly).map((e) => (
        <li key={e.key} className="flex items-center gap-1.5">
          <Swatch entry={e} />
          {!companies && e.key === "complete" ? "Built or adopted" : !companies && e.key === "filling" ? "Not built yet" : e.label}
        </li>
      ))}
      <li className="text-ink-3">
        {mode === "atlas" ? "Nearer the centre = stronger bet" : mode === "transit" ? "Columns = furthest stage reached · strongest first" : "Nearer the centre = built"}
        <span className="hidden sm:inline"> · scroll to zoom, drag to pan</span>
      </li>
    </ul>
  );
}

function Swatch({ entry }: { entry: LegendEntry }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const step = 13;
  const width = entry.leaves.length * step + 5;
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const paint = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const h = 18;
      c.width = width * dpr;
      c.height = h * dpr;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, h);
      const pal = readPalette();
      entry.leaves.forEach((l, i) => {
        const st = styleForNode(l, DEFAULT_STATE);
        paintStar(ctx, 2.5 + step / 2 + i * step, h / 2, st.r * (entry.key === "mag" ? 1.3 : MAG_R[2] / st.r * 1.1), st, pal, dpr);
      });
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    };
    paint();
    const mo = new MutationObserver(paint);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    return () => mo.disconnect();
  }, [entry, width]);
  return <canvas ref={ref} aria-hidden style={{ width, height: 18 }} className="shrink-0" />;
}
