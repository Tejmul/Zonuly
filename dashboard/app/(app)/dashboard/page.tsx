"use client";

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight } from "lucide-react";
import { useApi } from "@/lib/api";
import { rise } from "@/components/anim";
import { BarList } from "@/components/charts";

/* Reads the market map (/api/network): segments as nodes, companies with their checks. */
type Net = {
  stages: { key: string; code: string; label: string; blurb: string; nodes: number }[];
  nodes: { id: string; layer: string; label: string; count: number; bets: number; near: number; chokepoint: boolean; bottleneck: boolean }[];
  companies: {
    id: number; name: string; tier: string | null; region: string | null; ppo_lpa: number | null;
    hiring_status: string | null; description: string | null; bet: boolean; near: boolean;
    roles: number; fresher: number; anywhere: number; leads: number; missing: string[];
  }[];
  stats: Record<string, number>;
  definitions: Record<string, string>;
};

const TIER_LABEL: Record<string, string> = {
  tier1: "Tier 1 — ₹30 LPA and up",
  tier2: "Tier 2 — ₹24–30 LPA",
  prospect: "Prospect — engineers, hiring proven",
  unknown: "Pay not stated",
};
const MISSING_LABEL: Record<string, string> = {
  hiring: "hiring not proven on their site", fresher: "no fresher role", location: "not remote-from-anywhere", lead: "no lead yet",
};
const HIRING_LABEL: Record<string, string> = {
  verified: "Proven on their own page",
  role_missing: "Hiring, but other roles",
  not_authorized: "Claim not backed",
  unreachable: "Could not check",
  unchecked: "Not checked yet",
};

export default function Dashboard() {
  const root = useRef<HTMLDivElement>(null);
  const { data, loading, error } = useApi<Net>("/api/network");

  useGSAP(() => { if (data) rise("[data-plate]", { stagger: 0.05 }); },
    { scope: root, dependencies: [!!data] });

  if (error) return <p className="py-8 text-[13px] text-ink-3">{error}</p>;
  if (loading && !data) return <p className="py-8 text-[13px] text-ink-3">Reading the pipeline…</p>;
  if (!data) return null;

  const { stats, nodes, companies } = data;
  const byTier = countBy(companies, (c) => c.tier ?? "unknown");
  const byRegion = countBy(companies.filter((c) => c.region), (c) => c.region!.toUpperCase());
  const byHiring = countBy(companies, (c) => c.hiring_status ?? "unchecked");
  const targets = companies.filter((c) => ["tier1", "tier2", "prospect"].includes(c.tier ?? "")).length;
  const anywhere = companies.filter((c) => c.anywhere > 0).length;
  // what is missing across the near-bets: the machine's work queue, by kind
  const byMissing = countBy(companies.filter((c) => c.near).flatMap((c) => c.missing), (m) => m);
  const bottlenecks = nodes.filter((n) => n.bottleneck || (n.near > 0 && n.bets === 0)).sort((a, b) => b.near - a.near).slice(0, 8);
  const top = [...companies.filter((c) => c.bet), ...companies.filter((c) => !c.bet && c.near && c.hiring_status === "verified")].slice(0, 8);

  return (
    <div ref={root} className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
            What the pipeline found
          </h1>
          <p className="mt-0.5 text-[12.5px] text-ink-2">
            <span className="tnum">{(stats.companies ?? 0).toLocaleString()}</span> companies in{" "}
            <span className="tnum">{stats.nodes ?? 0}</span> market segments, graded on what they can pay
            and whether the hiring is real.
          </p>
        </div>
        <Link
          href="/companies"
          className="group inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-md
            bg-ink px-3 text-[12.5px] font-medium text-on-ink transition-opacity hover:opacity-85"
        >
          Go to companies
          <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>

      <section data-plate className="plate grid grid-cols-2 divide-line lg:grid-cols-4 lg:divide-x">
        <span className="tick" />
        <Metric label="Worth chasing" value={targets} hint="tier 1 and prospects" />
        <Metric label="Hiring proven" value={byHiring.verified ?? 0} hint="their own page backs it" />
        <Metric label="Hire from anywhere" value={anywhere} hint="a posting says so" />
        <Metric label="Ready to ask" value={stats.bets ?? 0} hint={`${stats.near ?? 0} one step away`} />
      </section>

      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Plate className="lg:col-span-2" code="A" title="What kind of companies" sub="Graded by what they can pay">
          <BarList rows={rank(byTier, TIER_LABEL)} />
        </Plate>
        <Plate code="B" title="Where they are" sub="The currency gap is the thesis">
          <BarList rows={rank(byRegion)} />
        </Plate>
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Plate code="C" title="Is the hiring real" sub="Checked against pages they control">
          <BarList rows={rank(byHiring, HIRING_LABEL)} />
        </Plate>

        <Plate className="lg:col-span-2" code="D" title="What the near-bets are missing" sub={data.definitions.near}>
          <BarList rows={rank(byMissing, MISSING_LABEL)} />
          {bottlenecks.length > 0 && (
            <ul className="mt-3 divide-y divide-line border-t border-line">
              {bottlenecks.map((n) => (
                <li key={n.id} className="flex items-center gap-3 py-2">
                  <span className="chip">bottleneck</span>
                  <span className="text-[12.5px]">{n.label}</span>
                  <span className="tnum ml-auto text-[12px] text-ink-2">{n.near} near · {n.count} companies</span>
                </li>
              ))}
            </ul>
          )}
        </Plate>
      </div>

      <Plate code="E" title="Ready to ask" sub="Hiring proven, a fresher role, remote-from-anywhere or India, and a lead — then the ones one step away">
        {top.length === 0 ? (
          <p className="py-3 text-[12px] text-ink-3">Nothing qualifies yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {top.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/companies/${c.id}`}
                  className="flex items-center gap-3 py-2 transition-colors hover:bg-surface-2"
                >
                  <span className="w-40 shrink-0 truncate text-[12.5px] font-medium">{c.name}</span>
                  <span className="hidden flex-1 truncate text-[12px] text-ink-3 sm:block">
                    {c.description ?? "—"}
                  </span>
                  {c.bet && <span className="chip shrink-0">ready</span>}
                  <span className="marginal shrink-0">{c.region ?? "—"}</span>
                  <span className="tnum w-24 shrink-0 text-right text-[12px] text-ink-3">
                    {c.fresher}/{c.roles} roles · {c.leads} lead{c.leads === 1 ? "" : "s"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Plate>
    </div>
  );
}

function rank(counts: Record<string, number>, labels?: Record<string, string>) {
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([k, v]) => ({ label: labels?.[k] ?? k, value: v }));
}

function countBy<T>(rows: T[], key: (r: T) => string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[key(r)] = (out[key(r)] ?? 0) + 1;
  return out;
}

function Metric({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="p-4">
      <p className="marginal">{label}</p>
      <p className="tnum mt-1.5 text-[28px] leading-none font-medium tracking-[-0.02em]">
        {(value ?? 0).toLocaleString()}
      </p>
      <p className="mt-1.5 text-[11.5px] text-ink-3">{hint}</p>
    </div>
  );
}

function Plate({
  code, title, sub, children, className = "",
}: { code: string; title: string; sub?: string; children: React.ReactNode; className?: string }) {
  return (
    <section data-plate className={`plate px-4 py-3.5 ${className}`}>
      <span className="tick" />
      <div className="flex items-baseline gap-2">
        <span className="tnum text-[10px] text-ink-3">{code}</span>
        <h2 className="text-[13px] font-semibold tracking-[-0.01em]">{title}</h2>
      </div>
      {sub && <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{sub}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}
