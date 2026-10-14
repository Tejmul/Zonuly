"use client";

/*  Graph — the knowledge graph as a map (2026-10-14).
 *
 *  Tejmul: "Why can't I see the knowledge graph? Show it in the UI, like ChokePoints …
 *  I want to directly see which companies are still pending — what I still need to scrape."
 *
 *  Three views of one canvas, all pre-shaped by the API (/api/graph/map, jobhunter/graphmap.py):
 *    Market    industry layers → segments → companies, left to right like the chokepoints
 *              chain; dotted wires join companies that share an investor
 *    Pipeline  Discovered → Described → Hiring proven → Referrer found → Email ready ·
 *              complete → Drafted / sent; each company sits at the furthest stage it reached
 *    Project   the context layer: stages, the features and gaps at each, the rules they serve
 *
 *  Pending is the point: a complete company is a solid dot, one still filling is a hollow
 *  ring whose weight is how much is missing, dashed when nothing has been read yet, and any
 *  layer or segment holding a pending ★ company wears a dashed roll-up ring. Click a company
 *  for what is missing and why; "Fill now" runs the fill pass on it.
 *
 *  Everything that changes what you see is in the URL: ?view=&node=&pending=1&m=&star=1&part=&q=
 */

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { gsap } from "gsap";
import { ArrowUpRight, Loader2, Search, X } from "lucide-react";
import { api, useApi, type Task } from "@/lib/api";
import { GraphCanvas } from "@/components/graph-canvas";
import { OperatorOnly } from "@/components/access";
import { ErrorNote } from "@/components/ui/primitives";
import {
  LEGEND, PART_LABEL, DEFAULT_STATE, paintNode, readPalette, screenRadius, styleForNode,
  type Agg, type Bucket, type GraphMap, type Leaf, type ViewKey,
} from "@/lib/graph-style";

export default function GraphPage() {
  // useSearchParams needs a Suspense boundary (node_modules/next/dist/docs, use-search-params)
  return (
    <Suspense fallback={<MapFrame><Calm>Reading the graph…</Calm></MapFrame>}>
      <GraphView />
    </Suspense>
  );
}

/* ---------------------------------------------------------------- the URL */

type Market = "india" | "remote";
type UrlState = { view: ViewKey; node: string | null; pending: boolean; markets: Market[]; star: boolean; part: string | null; q: string };

const VIEWS: { key: ViewKey; label: string; hint: string }[] = [
  { key: "market", label: "Market", hint: "Industry layers → segments → companies" },
  { key: "pipeline", label: "Pipeline", hint: "How far each company got, stage by stage" },
  { key: "project", label: "Project", hint: "Stages, features, gaps and the rules they serve" },
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

  const map = useApi<GraphMap>(`/api/graph/map?view=${view}`);
  // the payload held may still be the previous view's while the new one loads
  const data = map.data && map.data.view === view ? map.data : null;

  useEffect(() => {
    const qs = writeUrl({ view, node, pending, markets, star, part, q });
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
  }, [view, node, pending, markets, star, part, q]);

  const isCompanies = view !== "project";

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
      never: pend.filter((l) => l.kind === "company" && !l.filled_at).length,
    };
  }, [shownLeaves]);

  const fill = useFill(map.reload);

  const changeView = (v: ViewKey) => {
    if (v === view) return;
    setView(v);
    setNode(null);
    setPart(null);
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

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Graph</h1>
          <p className="mt-1 max-w-[76ch] text-[12.5px] leading-relaxed text-ink-2">
            The knowledge graph as a map. Solid dots are complete; hollow rings are still being filled — the
            heavier the ring, the more is missing, dashed when nothing has been read yet. A dashed ring around a
            layer or segment means a ★ India company under it is still pending. Click anything.
          </p>
        </div>
        <div role="tablist" aria-label="View" className="flex rounded-lg border border-line bg-surface p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              role="tab"
              aria-selected={view === v.key}
              title={v.hint}
              onClick={() => changeView(v.key)}
              className={`h-8 cursor-pointer rounded-md px-3 text-[12.5px] font-medium transition-colors ${
                view === v.key ? "bg-ink text-on-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </header>

      {/* ---- the toolbar: filters and search */}
      <div className="flex flex-wrap items-center gap-2">
        <Toggle on={pending} onClick={() => setPending(!pending)}>Only pending</Toggle>
        {isCompanies ? (
          <>
            <Toggle on={markets.includes("india")} onClick={() => flip("india")}>India</Toggle>
            <Toggle on={markets.includes("remote")} onClick={() => flip("remote")}>Remote</Toggle>
            <Toggle on={star} onClick={() => setStar(!star)}>★ list</Toggle>
          </>
        ) : null}
        {anyFilter ? (
          <button
            onClick={() => { setPending(false); setMarkets([]); setStar(false); setPart(null); }}
            className="h-8 cursor-pointer px-2 text-[12px] text-ink-3 underline-offset-2 hover:text-ink hover:underline"
          >
            Clear filters
          </button>
        ) : null}
        <label className="relative ml-auto w-full sm:w-[260px]">
          <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") searchFirst(); }}
            placeholder={isCompanies ? "Find a company — Enter to fly there" : "Find a feature or gap"}
            aria-label="Search the map"
            className="h-8 w-full rounded-lg border border-line bg-surface pr-8 pl-8 text-[12.5px] outline-none placeholder:text-ink-3 focus:border-ink"
          />
          {q ? (
            <button aria-label="Clear search" onClick={() => setQ("")} className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer text-ink-3 hover:text-ink">
              <X size={14} />
            </button>
          ) : null}
          {matches ? (
            <span className="absolute -bottom-4 left-1 text-[10.5px] text-ink-3">{fmt(matches.size)} match{matches.size === 1 ? "" : "es"}</span>
          ) : null}
        </label>
      </div>

      {/* ---- the counts strip: complete / filling / by missing part (click a part to see only those) */}
      {data ? (
        <div className="flex flex-wrap items-stretch gap-px overflow-hidden rounded-xl border border-line bg-line">
          <Stat label="Complete" value={counts.complete} total={counts.total} />
          <Stat label={isCompanies ? "Still filling" : "Not built"} value={counts.filling} total={counts.total} strong />
          {data.parts.map((p) => (
            <Stat
              key={p.key}
              label={`No ${PART_LABEL[p.key]?.toLowerCase() ?? p.label.toLowerCase()}`}
              value={counts.miss[p.key] ?? 0}
              total={counts.total}
              on={part === p.key}
              onClick={() => setPart(part === p.key ? null : p.key)}
            />
          ))}
          {isCompanies ? (
            <>
              <Stat label="★ pending" value={counts.star} on={star && pending} onClick={() => { setStar(true); setPending(true); }} />
              <Stat label="India pending" value={counts.india} />
              <Stat label="Never filled" value={counts.never} />
            </>
          ) : null}
        </div>
      ) : null}

      {/* ---- map + panel */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <MapFrame>
          {data ? (
            data.leaves.length === 0 && data.hubs.length === 0 ? (
              <Calm>
                {view === "project"
                  ? "The context layer is empty. Run `python scripts/run.py kg build` to load knowledge/context.yaml."
                  : "No companies yet. The tick fills this map as it scrapes."}
              </Calm>
            ) : (
              <>
                <GraphCanvas data={data} shown={shown} matches={matches} agg={agg} selected={node} onSelect={setNode} />
                <Legend view={view} />
                {shownLeaves.length === 0 ? (
                  <div className="pointer-events-none absolute inset-x-0 top-4 flex justify-center">
                    <span className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2">
                      Nothing passes these filters.
                    </span>
                  </div>
                ) : null}
              </>
            )
          ) : map.error ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <ErrorNote message={map.error} onRetry={map.reload} title="The graph can’t be loaded right now" />
            </div>
          ) : (
            <Calm busy>Reading the graph…</Calm>
          )}
          {map.loading && data ? (
            <span className="absolute top-3 right-3 flex items-center gap-1.5 text-[11px] text-ink-3">
              <Loader2 size={12} className="animate-spin motion-reduce:animate-none" /> refreshing
            </span>
          ) : null}
        </MapFrame>

        <Panel open={!!picked} onClose={() => setNode(null)} panelKey={node ?? "overview"}>
          {!data ? (
            <p className="text-[12.5px] text-ink-3">Waiting for the map…</p>
          ) : picked?.kind === "leaf" ? (
            picked.v.kind === "company" ? (
              <CompanyCard leaf={picked.v} data={data} fill={fill} onPick={setNode} />
            ) : (
              <ProjectCard leaf={picked.v} />
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
            <Overview data={data} leaves={shownLeaves} companies={isCompanies} fill={fill} onPick={setNode} />
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

/* ---------------------------------------------------------------- panel cards */

function CompanyCard({ leaf, data, fill, onPick }: { leaf: Leaf; data: GraphMap; fill: Fill; onPick: (id: string) => void }) {
  const hub = data.hubs.find((h) => h.id === leaf.hub);
  const sub = data.subs.find((s) => s.id === leaf.sub);
  const peers = data.investors.filter((i) => i.companies.includes(leaf.id));
  const names = new Map(data.leaves.map((l) => [l.id, l.label]));
  const pending = leaf.status !== "complete";
  return (
    <div className="space-y-4">
      <div>
        <div className="marginal">{[hub?.label, sub?.label].filter(Boolean).join(" · ")}</div>
        <h2 className="mt-1 text-[16px] font-semibold tracking-[-0.01em]">
          {leaf.star ? <span title="On the ★ India list">★ </span> : null}{leaf.label}
        </h2>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className={pending ? "pill pill-warn" : "pill pill-good"}>{pending ? "Still filling" : "Complete"}</span>
          {leaf.market ? <span className="pill pill-muted">{leaf.city ?? (leaf.market === "india" ? "India" : leaf.market === "remote" ? "Remote" : "Abroad")}</span> : null}
          {leaf.drafted ? <span className="pill pill-note">Drafted</span> : null}
        </div>
      </div>

      {pending ? (
        <section>
          <h3 className="marginal mb-1.5">What is missing</h3>
          <ul className="space-y-2">
            {(leaf.missing ?? []).map((p) => (
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

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line text-[12px]">
        <Fact k="Priority" v={leaf.priority ?? "—"} />
        <Fact k="Best selection" v={leaf.best_selection != null ? `${leaf.best_selection}${leaf.best_selection_band ? ` · ${leaf.best_selection_band}` : ""}` : "—"} />
        <Fact k="Referrers ready" v={`${fmt(leaf.referrers)} · ${fmt(leaf.referrers_email)} with email`} />
        <Fact k="Roles" v={`${fmt(leaf.roles)} stored${leaf.open_roles != null ? ` · ${fmt(leaf.open_roles)} open` : ""}`} />
      </dl>
      <p className="text-[11px] text-ink-3">
        {leaf.filled_at ? `Last filled ${leaf.filled_at.replace("T", " ").slice(0, 16)}` : "Never filled"}
      </p>

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
        {leaf.cid ? <FillButton cid={leaf.cid} fill={fill} /> : null}
        {leaf.cid ? (
          <Link href={`/companies/${leaf.cid}`} className="flex h-9 items-center justify-center gap-1 rounded-[9px] border border-line text-[13px] font-medium hover:border-ink">
            Company page <ArrowUpRight size={14} />
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function ProjectCard({ leaf }: { leaf: Leaf }) {
  const done = leaf.status === "complete";
  return (
    <div className="space-y-3">
      <div className="marginal">{leaf.kind} · {leaf.id}</div>
      <h2 className="text-[16px] font-semibold tracking-[-0.01em]">{leaf.label}</h2>
      <span className={done ? "pill pill-good" : "pill pill-warn"}>
        {leaf.kind === "decision" ? "Adopted" : done ? "Built" : PART_LABEL[leaf.feature_status ?? ""] ?? leaf.feature_status ?? "Open"}
      </span>
      {leaf.summary ? <p className="text-[12.5px] leading-relaxed text-ink-2">{leaf.summary}</p> : null}
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
        <div className="marginal">{tier === "hub" ? bucket.code ?? "Hub" : "Segment"}</div>
        <h2 className="mt-1 text-[16px] font-semibold tracking-[-0.01em]">{bucket.label}</h2>
        {bucket.blurb ? <p className="mt-1 text-[12px] leading-relaxed text-ink-2">{bucket.blurb}</p> : null}
      </div>
      <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line text-[12px]">
        <Fact k="Shown" v={fmt(a.count)} />
        <Fact k="Complete" v={fmt(a.complete)} />
        <Fact k={companies ? "Filling" : "Not built"} v={fmt(a.pending)} />
      </dl>
      {Object.keys(miss).length ? (
        <p className="text-[11.5px] leading-relaxed text-ink-2">
          Missing: {Object.entries(miss).sort((x, y) => y[1] - x[1]).map(([k, n]) => `${PART_LABEL[k] ?? k} ${fmt(n)}`).join(" · ")}
        </p>
      ) : null}
      <PendingList title={companies ? "Pending here — ★ first, then priority" : "Not built here"} leaves={pend} fill={companies ? fill : null} onPick={onPick} />
    </div>
  );
}

function Overview({ data, leaves, companies, fill, onPick }: { data: GraphMap; leaves: Leaf[]; companies: boolean; fill: Fill; onPick: (id: string) => void }) {
  const pend = leaves.filter((l) => l.status !== "complete").sort(pendingOrder);
  return (
    <div className="space-y-4">
      <div>
        <div className="marginal">Overview · {data.generated_at.replace("T", " ").slice(0, 16)}</div>
        <h2 className="mt-1 text-[16px] font-semibold tracking-[-0.01em]">
          {companies ? "What still needs scraping" : "What is still to build"}
        </h2>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-2">
          {companies
            ? "Pending companies under the current filters, ★ India list first, then by priority. Click one to fly to it."
            : "Features not built yet and open gaps, from the context layer."}
        </p>
      </div>
      <PendingList title={`${fmt(pend.length)} pending`} leaves={pend} fill={companies ? fill : null} onPick={onPick} />
    </div>
  );
}

function PendingList({ title, leaves, fill, onPick }: { title: string; leaves: Leaf[]; fill: Fill | null; onPick: (id: string) => void }) {
  const [n, setN] = useState(40);
  if (!leaves.length) return <p className="text-[12px] text-ink-3">Nothing pending here.</p>;
  return (
    <section>
      <h3 className="marginal mb-1.5">{title}</h3>
      <ul className="divide-y divide-line rounded-lg border border-line">
        {leaves.slice(0, n).map((l) => (
          <li key={l.id} className="flex items-center gap-2 px-2.5 py-2">
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
    <div className="relative h-[min(76dvh,820px)] min-h-[440px] overflow-hidden rounded-xl border border-line bg-surface">
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

/** Beside the map on a laptop; a bottom sheet on a phone, only while something is picked. */
function Panel({ open, onClose, panelKey, children }: { open: boolean; onClose: () => void; panelKey: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || still()) return;
    gsap.fromTo(ref.current, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.25, ease: "power2.out" });
  }, [panelKey]);
  return (
    <aside
      aria-label="Details"
      className={`${open ? "fixed" : "hidden"} inset-x-0 bottom-0 z-40 max-h-[62dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface px-4 pt-3 pb-6 shadow-[0_-8px_24px_rgb(0_0_0/0.12)]
        lg:static lg:block lg:max-h-[min(76dvh,820px)] lg:min-h-[440px] lg:rounded-xl lg:border lg:pt-4 lg:shadow-none`}
    >
      <div className="mb-2 flex justify-end">
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
      className={`h-8 cursor-pointer rounded-lg border px-3 text-[12.5px] font-medium transition-colors ${
        on ? "border-ink bg-ink text-on-ink" : "border-line bg-surface text-ink-2 hover:border-line-strong hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function Stat({ label, value, total, strong, on, onClick }: { label: string; value: number; total?: number; strong?: boolean; on?: boolean; onClick?: () => void }) {
  const body = (
    <>
      <span className="marginal block">{label}</span>
      <span className={`tnum mt-0.5 block text-[15px] ${strong ? "font-semibold" : "font-medium"}`}>{fmt(value)}</span>
      {total ? (
        <span aria-hidden className="mt-1.5 block h-[3px] w-full bg-surface-3">
          <span className="block h-full bg-ink" style={{ width: `${Math.round((value / Math.max(1, total)) * 100)}%` }} />
        </span>
      ) : null}
    </>
  );
  const cls = `min-w-[112px] flex-1 px-3 py-2 text-left ${on ? "bg-surface-3" : "bg-surface"}`;
  return onClick ? (
    <button aria-pressed={on} onClick={onClick} className={`${cls} cursor-pointer hover:bg-surface-2`}>{body}</button>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="bg-surface px-2.5 py-2">
      <dt className="marginal">{k}</dt>
      <dd className="tnum mt-0.5 text-[12.5px]">{v}</dd>
    </div>
  );
}

/* The legend: every swatch painted by styleForNode + paintNode, the canvas's own pair. */
function Legend({ view }: { view: ViewKey }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="absolute bottom-3 left-3 max-w-[calc(100%-24px)] rounded-lg border border-line bg-surface/95 px-3 py-2 text-[11px] backdrop-blur-sm">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="marginal cursor-pointer hover:text-ink">
        Legend {open ? "–" : "+"}
      </button>
      {open ? (
        <ul className="mt-1.5 grid gap-1">
          {LEGEND.filter((e) => view !== "project" || !["star", "bare"].includes(e.key)).map((e) => (
            <li key={e.key} className="flex items-center gap-2 text-ink-2">
              <Swatch entry={e} />
              {view === "project" && e.key === "complete" ? "Built / adopted" : e.label}
            </li>
          ))}
          <li className="pt-1 text-ink-3">Scroll to zoom · drag to pan · dots appear as you zoom in</li>
        </ul>
      ) : null}
    </div>
  );
}

function Swatch({ entry }: { entry: (typeof LEGEND)[number] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const paint = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const size = 22;
      c.width = size * dpr;
      c.height = size * dpr;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);
      const st = styleForNode(entry.node, { ...DEFAULT_STATE, ...entry.state });
      // leaves at a readable zoom; hubs and segments at a fixed small size
      const r = entry.node.tier === "leaf" ? screenRadius(entry.node, st, 1.6) : 6;
      paintNode(ctx, size / 2, size / 2, r, { ...st, haloGap: Math.min(st.haloGap, 3) }, readPalette());
    };
    paint();
    const mo = new MutationObserver(paint);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    return () => mo.disconnect();
  }, [entry]);
  return <canvas ref={ref} aria-hidden className="h-[22px] w-[22px] shrink-0" />;
}
