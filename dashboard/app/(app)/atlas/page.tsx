"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowRight, Search, X } from "lucide-react";
import { useApi } from "@/lib/api";
import { TierPill } from "@/components/ui/tier";
import { AtlasCanvas } from "@/components/atlas-canvas";

/* The Atlas is a filter. The map shows the market (industry-chain layers, segments sized by
   companies); clicking segments adds them to the filter, the switches narrow further, and
   the box underneath always shows exactly the companies that match — one click to open. */

type NodeT = {
  id: string; layer: string; label: string; note: string | null;
  count: number; bets: number; near: number; leads: number; fresher: number; anywhere: number; roles: number;
  chokepoint: boolean; bottleneck: boolean; company_ids: number[];
};
type CompanyT = {
  id: number; name: string; tier: string | null; region: string | null;
  description: string | null; website: string | null; funding_stage: string | null;
  ppo_lpa: number | null; stipend_inr_month: number | null; hiring_status: string | null;
  segment: string; segment_label: string;
  roles: number; fresher: number; anywhere: number; leads: number;
  bet: boolean; near: boolean; missing: string[];
  hiring_post: { url: string; by: string | null; source: string | null } | null;
};
type Atlas = {
  stages: { key: string; code: string; label: string; blurb: string; nodes: number }[];
  nodes: NodeT[];
  edges: { source: string; target: string; weight: number }[];
  companies: CompanyT[];
  stats: Record<string, number>;
  definitions: Record<string, string>;
};
type Mode = "layers" | "chokepoints" | "bottlenecks";

const REGIONS: [string, string][] = [
  ["", "any region"], ["us", "US"], ["uk", "UK"], ["de", "Germany"], ["eu", "Europe"], ["nl", "Netherlands"],
  ["india", "India"], ["remote", "Remote"],
];
const SWITCHES: { key: keyof Filters; label: string; hint: string }[] = [
  { key: "hiring", label: "hiring proven", hint: "their own board or careers page lists open roles" },
  { key: "fresher", label: "fresher roles", hint: "at least one role that is not senior/staff/lead" },
  { key: "anywhere", label: "hire from anywhere", hint: "a posting says work from any country / no visa" },
  { key: "lead", label: "has a lead", hint: "someone with a usable email who can refer" },
  { key: "bets", label: "ready to ask", hint: "all four line up" },
];
type Filters = { hiring: boolean; fresher: boolean; anywhere: boolean; lead: boolean; bets: boolean };
const MISSING_SHORT: Record<string, string> = { hiring: "hiring", fresher: "fresher role", location: "remote-anywhere", lead: "lead" };

export default function AtlasPage() {
  const { data, loading, error } = useApi<Atlas>("/api/network");
  const [mode, setMode] = useState<Mode>("layers");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string[]>([]);          // selected segment ids
  const [region, setRegion] = useState("");
  const [tier, setTier] = useState("");
  const [f, setF] = useState<Filters>({ hiring: false, fresher: false, anywhere: false, lead: false, bets: false });
  const [find, setFind] = useState("");

  const results = useMemo(() => {
    if (!data) return [];
    const segs = new Set(picked);
    const needle = find.trim().toLowerCase();
    return data.companies.filter((c) => {
      if (segs.size && !segs.has(c.segment)) return false;
      if (region && c.region !== region) return false;
      if (tier && c.tier !== tier) return false;
      if (f.hiring && !["verified", "role_missing"].includes(c.hiring_status ?? "")) return false;
      if (f.fresher && !c.fresher) return false;
      if (f.anywhere && !c.anywhere) return false;
      if (f.lead && !c.leads) return false;
      if (f.bets && !c.bet) return false;
      if (needle && !`${c.name} ${c.description ?? ""}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, picked, region, tier, f, find]);

  if (error) return <Note>{error}</Note>;
  if (loading && !data) return <Note>Drawing the map…</Note>;
  if (!data) return null;

  const active = picked.length + (region ? 1 : 0) + (tier ? 1 : 0) + Object.values(f).filter(Boolean).length + (find ? 1 : 0);
  const pickedNodes = data.nodes.filter((n) => picked.includes(n.id));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const clear = () => { setPicked([]); setRegion(""); setTier(""); setF({ hiring: false, fresher: false, anywhere: false, lead: false, bets: false }); setFind(""); };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Atlas</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          Click segments on the map to filter, flip the switches, and the companies that match appear in the box below.
        </p>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-line pt-2">
          {([["companies", data.stats.companies], ["segments", data.stats.nodes], ["ready to ask", data.stats.bets],
            ["one step away", data.stats.near], ["chokepoints", data.stats.chokepoints]] as [string, number][]).map(([l, v]) => (
            <span key={l} className="flex items-baseline gap-1.5">
              <span className="tnum text-[13px] font-medium">{(v ?? 0).toLocaleString()}</span>
              <span className="marginal">{l}</span>
            </span>
          ))}
        </div>
      </div>

      {/* ------------------------------------------------------------ filters */}
      <div className="plate px-4 py-3">
        <span className="tick" />
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-0 flex-1 sm:max-w-[200px]">
            <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a segment on the map"
              className="h-8 w-full rounded-md border border-line bg-surface pr-2 pl-7 text-[12px] placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
            />
          </label>
          <select value={region} onChange={(e) => setRegion(e.target.value)} className="h-8 rounded-md border border-line bg-surface px-2 text-[12px]">
            {REGIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <select value={tier} onChange={(e) => setTier(e.target.value)} className="h-8 rounded-md border border-line bg-surface px-2 text-[12px]">
            <option value="">any grade</option>
            <option value="tier1">tier 1</option>
            <option value="tier2">tier 2</option>
            <option value="prospect">prospect</option>
            <option value="unknown">pay unknown</option>
          </select>
          {SWITCHES.map((s) => (
            <button
              key={s.key}
              title={s.hint}
              onClick={() => setF((x) => ({ ...x, [s.key]: !x[s.key] }))}
              className={`marginal cursor-pointer rounded-md border px-2.5 py-1.5 transition-colors ${
                f[s.key] ? "border-ink bg-ink" : "border-line hover:text-ink"
              }`}
              style={f[s.key] ? { color: "var(--on-ink)" } : undefined}
            >
              {s.label}
            </button>
          ))}
          <div className="ml-auto flex rounded-md border border-line p-px">
            {(["layers", "chokepoints", "bottlenecks"] as Mode[]).map((m) => (
              <button
                key={m}
                title={m === "layers" ? "show everything" : data.definitions[m === "chokepoints" ? "chokepoint" : "bottleneck"]}
                onClick={() => setMode(m)}
                className={`marginal cursor-pointer rounded-[5px] px-2.5 py-1.5 transition-colors ${mode === m ? "bg-ink" : "hover:text-ink"}`}
                style={mode === m ? { color: "var(--on-ink)" } : undefined}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
        {pickedNodes.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="marginal">segments:</span>
            {pickedNodes.map((n) => (
              <button key={n.id} onClick={() => toggle(n.id)} className="chip cursor-pointer hover:bg-surface-2">
                {n.label} <X size={11} />
              </button>
            ))}
          </div>
        )}
      </div>

      <AtlasCanvas
        stages={data.stages}
        nodes={data.nodes}
        edges={data.edges}
        mode={mode}
        query={query}
        picked={picked}
        onToggle={toggle}
      />

      {/* ------------------------------------------------------------ results */}
      <div className="plate">
        <span className="tick" />
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-[15px] font-semibold tracking-[-0.01em]">
            <span className="tnum">{results.length.toLocaleString()}</span> {results.length === 1 ? "company" : "companies"}
            {active ? " match" : ""}
          </h2>
          <label className="relative min-w-0 flex-1 sm:max-w-[220px]">
            <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
            <input
              value={find}
              onChange={(e) => setFind(e.target.value)}
              placeholder="Find a company in the results"
              className="h-8 w-full rounded-md border border-line bg-surface pr-2 pl-7 text-[12px] placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
            />
          </label>
          {active > 0 && (
            <button onClick={clear} className="marginal ml-auto cursor-pointer hover:text-ink">clear filters</button>
          )}
        </div>
        {results.length === 0 ? (
          <p className="px-4 py-8 text-[13px] text-ink-3">Nothing matches. Loosen a switch or clear the segments.</p>
        ) : (
          <ul className="max-h-[32rem] divide-y divide-line overflow-y-auto">
            {results.slice(0, 300).map((c, i) => (
              <li key={c.id}>
                <Link
                  href={`/companies/${c.id}`}
                  className="group flex items-center gap-2.5 px-4 py-2 transition-colors hover:bg-surface-2"
                >
                  <span className="tnum w-7 shrink-0 text-right text-[11px] text-ink-3">{i + 1}</span>
                  <TierPill tier={c.tier} />
                  <span className="truncate text-[12.5px] font-medium">{c.name}</span>
                  {c.bet && <span className="chip shrink-0">ready</span>}
                  {c.anywhere > 0 && <span className="chip shrink-0">anywhere</span>}
                  <span className="hidden flex-1 truncate text-[12px] text-ink-3 lg:block">{c.description ?? c.segment_label}</span>
                  <span className="marginal shrink-0">{c.region ?? "—"}</span>
                  <span className="tnum w-16 shrink-0 text-right text-[11px] text-ink-3">{c.fresher}/{c.roles} roles</span>
                  <span className="tnum w-14 shrink-0 text-right text-[11px] text-ink-3">{c.leads} lead{c.leads === 1 ? "" : "s"}</span>
                  {!c.bet && c.missing.length > 0 && (
                    <span className="hidden w-40 shrink-0 truncate text-[11px] text-ink-3 xl:block">
                      needs {c.missing.map((m) => MISSING_SHORT[m] ?? m).join(", ")}
                    </span>
                  )}
                  <ArrowRight size={12} className="shrink-0 text-ink-3 transition-transform group-hover:translate-x-0.5" />
                </Link>
              </li>
            ))}
          </ul>
        )}
        {results.length > 300 && (
          <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">Showing the first 300 — add a filter to narrow it.</p>
        )}
      </div>
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="py-8 text-[13px] text-ink-3">{children}</p>;
}
