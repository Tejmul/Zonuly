"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight, ChevronRight, Search } from "lucide-react";
import { useApi } from "@/lib/api";
import { rise } from "@/components/anim";
import { TierPill } from "@/components/ui/tier";
import { AddCompany } from "@/components/add-company";
import { OperatorOnly } from "@/components/access";

type Family = { family: string; count: number; titles: string[]; best_score: number | null; internships: number; fresher: number; anywhere: number };
type Row = {
  id: number; name: string; description: string | null; website: string | null;
  trust?: "complete" | "partial" | "bare";
  pay_basis?: string | null; team_size?: number | null;
  pay_power?: { score: number | null; band: string | null; why: string | null; per_head_usd_k: number | null };
  evidence?: { description: boolean; pay: boolean; funding: boolean; hiring: boolean; post: boolean };
  region: string | null; tier: string | null; tier_reason: string | null;
  ppo_lpa: number | null; stipend_inr_month: number | null;
  funding_stage: string | null; hiring_status: string | null; hiring_evidence: string | null;
  funding?: { stage: string | null; amount_usd_m: number | null; announced: string | null } | null;
  hiring_post: { text: string | null; url: string; by: string | null; source: string | null; at: string | null } | null;
  roles: { total: number; fresher: number; anywhere: number; internships: number; families: Family[] };
  referrals: {
    total: number; reachable: number;
    best: { name: string | null; role_class: string | null; label: string | null; email: string | null; confidence: string | null } | null;
  };
  ready: boolean;
};
type Payload = { companies: Row[]; stats: Record<string, number> };

const FILTERS = [
  { key: "complete", label: "trusted" },
  { key: "targets", label: "worth chasing" },
  { key: "anywhere", label: "hire from anywhere" },
  { key: "research", label: "needs research" },
  { key: "all", label: "all" },
  { key: "verified", label: "hiring proven" },
  { key: "referrals", label: "has a referrer" },
] as const;
type Filter = (typeof FILTERS)[number]["key"];

/* When pay can't be proven, show what they raised and when — "Seed · $12M · Mar 26". */
function fundingLine(f?: { stage: string | null; amount_usd_m: number | null; announced: string | null } | null): string | null {
  if (!f || (!f.stage && !f.announced)) return null;
  const parts: string[] = [];
  if (f.stage) parts.push(f.stage.replace(/\b\w/g, (m) => m.toUpperCase()));
  if (f.amount_usd_m) parts.push(`$${f.amount_usd_m}M`);
  if (f.announced) parts.push(f.announced.length >= 7 ? f.announced.slice(0, 7) : f.announced.slice(0, 4));
  return parts.join(" · ") || null;
}

const PP_SHORT: Record<string, string> = {
  pays: "pays", deep_pockets: "deep pockets", funded: "funded", thin: "thin", unknown: "unknown",
};
/* The benchmark, spelled out once where the numbers are — so a score is never a mystery. */
const PP_BANDS: [string, number, string][] = [
  ["Pays", 100, "a posting states ≥ ₹30 L (they wrote the number)"],
  ["Deep pockets", 85, "raised $10M+ in the last 24 months, or Series A+ in the last 30 months, or valued ≥ $50M"],
  ["Funded", 65, "raised $3–10M in the last 30 months, or a YC batch in the last 30 months with 10+ people"],
  ["Thin", 30, "under $2M, pre-seed, or the raise is older than 30 months"],
  ["Unknown", 0, "nothing found yet — needs research"],
];
const PAY_BASIS_SHORT: Record<string, string> = {
  stated: "pay stated", "funding-strong": "funded, can pay", "funding-weak": "funding thin", none: "pay unknown",
};
const PAY_BASIS_TITLE: Record<string, string> = {
  stated: "A posting states the pay.",
  "funding-strong": "Estimated from funding (round, recency, HQ, team) — not a stated figure.",
  "funding-weak": "Funding found is small, old, unstated or in rupees — needs a stated figure.",
  none: "No pay stated and no funding found yet — needs research.",
};
const SOURCE_LABEL: Record<string, string> = {
  x: "X", hn: "Hacker News", yc: "the YC directory", ats: "their own job board",
  careers: "their careers page", reddit: "Reddit", exa: "web search",
  greenhouse: "their Greenhouse board", lever: "their Lever board", ashby: "their Ashby board",
  remoteok: "RemoteOK", wwr: "We Work Remotely",
};

export default function CompaniesPage() {
  const root = useRef<HTMLDivElement>(null);
  const { data, loading, error, reload } = useApi<Payload>("/api/companies/grouped");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("complete");
  const [open, setOpen] = useState<number | null>(null);

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return data.companies.filter((c) => {
      // an API process older than the `trust` field: treat every row as complete so the
      // default filter still shows something instead of an empty page
      const trust = c.trust ?? "complete";
      if (filter === "complete" && trust !== "complete") return false;
      if (filter === "targets" && !["tier1", "tier2", "prospect"].includes(c.tier ?? "")) return false;
      if (filter === "anywhere" && !(c.roles.anywhere ?? 0)) return false;
      if (filter === "research" && trust === "complete") return false;
      if (filter === "verified" && c.hiring_status !== "verified") return false;
      if (filter === "referrals" && !c.referrals.reachable) return false;
      if (needle && !`${c.name} ${c.description ?? ""}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, q, filter]);

  useGSAP(() => { if (data) rise("[data-row]", { stagger: 0.015, y: 6 }); },
    { scope: root, dependencies: [!!data, filter] });

  if (error) return <p className="py-8 text-[13px] text-ink-3">{error}</p>;
  if (loading && !data) return <p className="py-8 text-[13px] text-ink-3">Reading the registry…</p>;
  if (!data) return null;

  return (
    <div ref={root} className="space-y-4">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
              Companies
            </h1>
            <p className="mt-0.5 text-[12.5px] text-ink-2">
              One row per company. A company hiring fifteen people is one company — its roles
              are grouped underneath it.
            </p>
          </div>
          {/* Writes to companies.yaml and pulls a board, so it is not offered on the
              public instance — where the API would refuse it anyway. */}
          <OperatorOnly>
            <div className="shrink-0">
              <AddCompany onAdded={reload} />
            </div>
          </OperatorOnly>
        </div>
        <details className="mt-2 text-[12px] text-ink-2">
          <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
            Pay Power — the benchmark behind the score in the last column
          </summary>
          <table className="mt-2 text-[12px]">
            <tbody>
              {PP_BANDS.map(([band, score, rule]) => (
                <tr key={band} className="align-top">
                  <td className="tnum pr-3 py-0.5 text-right font-medium">{score}</td>
                  <td className="pr-3 py-0.5 font-medium">{band}</td>
                  <td className="py-0.5 text-ink-2">{rule}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1.5 text-ink-3">
            India needs the stated number to go above Thin (rupee pay does not follow dollar rounds). Hyped names and teams over 200 are out regardless — that is a different question.
          </p>
        </details>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-line pt-2">
          <Stat n={data.stats.companies} label="companies" />
          <Stat n={data.stats.complete} label="trusted (read + pay + hiring)" />
          <Stat n={data.stats.bare} label="name only" />
          <Stat n={data.stats.roles} label="roles across them" />
          <Stat n={data.stats.fresher_roles} label="fresher roles" />
          <Stat n={data.stats.with_anywhere_roles} label="hire from anywhere" />
          <Stat n={data.stats.with_referrals} label="with a referrer" />
          <Stat n={data.stats.ready} label="ready to write to" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Find a company"
            className="h-8 w-full rounded-md border border-line bg-surface pr-2 pl-7 text-[12px]
              placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
          />
        </label>
        <div className="flex rounded-md border border-line p-px">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`marginal cursor-pointer rounded-[5px] px-2.5 py-1.5 transition-colors ${
                filter === f.key ? "bg-ink" : "hover:text-ink"
              }`}
              style={filter === f.key ? { color: "var(--on-ink)" } : undefined}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="marginal ml-auto">{rows.length} shown</span>
      </div>

      <div className="plate">
        <span className="tick" />
        {rows.length === 0 ? (
          <p className="px-4 py-8 text-[13px] text-ink-3">
            No company matches. Loosen the filter, or run{" "}
            <code className="text-ink">targets grade</code>.
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((c, i) => (
              <li key={c.id} data-row>
                <button
                  onClick={() => setOpen(open === c.id ? null : c.id)}
                  aria-expanded={open === c.id}
                  className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-left
                    transition-colors hover:bg-surface-2"
                >
                  <span className="tnum w-8 shrink-0 text-right text-[11px] text-ink-3">{i + 1}</span>
                  <ChevronRight
                    size={13}
                    className={`shrink-0 text-ink-3 transition-transform duration-200 ${open === c.id ? "rotate-90" : ""}`}
                  />
                  <TierPill tier={c.tier} />
                  <span className="w-44 shrink-0 truncate text-[13px] font-medium">{c.name}</span>
                  {/* `trust`/`evidence` arrive from the new API; an older process omits them */}
                  {c.trust && c.trust !== "complete" && (
                    <span className="chip shrink-0" title={`missing: ${Object.entries(c.evidence ?? {}).filter(([, v]) => !v).map(([k]) => k).join(", ")}`}>
                      {c.trust === "bare" ? "name only" : "partly read"}
                    </span>
                  )}
                  <span className="hidden flex-1 truncate text-[12px] text-ink-3 lg:block">
                    {c.description ?? "—"}
                  </span>
                  <Cell v={`${c.roles.fresher} fresher · ${c.roles.total} role${c.roles.total === 1 ? "" : "s"}${c.roles.anywhere ? " · anywhere" : ""}`} />
                  <Cell v={c.referrals.reachable ? `${c.referrals.reachable} to ask` : "no one yet"} />
                  <Cell v={c.hiring_status === "verified" ? "hiring proven" : c.hiring_status ?? "unchecked"} />
                  {/* Pay if we can prove it; otherwise the funding raised, with the date, as the fallback */}
                  <span
                    className={`tnum w-32 shrink-0 text-right text-[11.5px] ${(c.pay_power?.score ?? 0) >= 65 ? "" : "text-ink-3"}`}
                    title={c.pay_power?.why ?? PAY_BASIS_TITLE[c.pay_basis ?? "none"]}
                  >
                    {(c.pay_power?.score ?? 0) >= 65
                      ? `${c.pay_power!.score} · ${PP_SHORT[c.pay_power!.band ?? ""] ?? c.pay_power!.band}`
                      : fundingLine(c.funding) ?? PAY_BASIS_SHORT[c.pay_basis ?? "none"]}
                  </span>
                </button>

                {open === c.id && (
                  <div className="border-t border-line bg-surface-2 px-4 py-4">
                    <p className="max-w-3xl text-[12.5px] leading-relaxed text-ink-2">
                      {c.description ?? "No description yet — run targets enrich for this company."}
                    </p>

                    <div className="mt-4 grid gap-5 md:grid-cols-2">
                      <div>
                        <p className="marginal">
                          Roles — {c.roles.total} across {c.roles.families.length} families
                        </p>
                        <ul className="mt-2 space-y-1.5">
                          {c.roles.families.map((f) => (
                            <li key={f.family}>
                              <div className="flex items-baseline gap-2">
                                <span className="text-[12px] font-medium">{f.family}</span>
                                <span className="tnum text-[11px] text-ink-3">{f.count}</span>
                                {f.fresher > 0 && <span className="chip">{f.fresher} fresher</span>}
                                {f.anywhere > 0 && <span className="chip">{f.anywhere} from anywhere</span>}
                                {f.internships > 0 && <span className="chip">{f.internships} intern</span>}
                              </div>
                              <p className="truncate text-[11.5px] text-ink-3">{f.titles.join(" · ")}</p>
                            </li>
                          ))}
                          {c.roles.families.length === 0 && (
                            <li className="text-[12px] text-ink-3">No roles scraped yet.</li>
                          )}
                        </ul>
                      </div>

                      <div>
                        <p className="marginal">Referrals</p>
                        {c.referrals.best ? (
                          <div className="mt-2">
                            <p className="text-[12px]">
                              Ask <span className="font-medium">{c.referrals.best.name}</span>
                              <span className="text-ink-3"> — {c.referrals.best.label}</span>
                            </p>
                            <p className="tnum mt-0.5 text-[11.5px] text-ink-3">{c.referrals.best.email}</p>
                            <p className="mt-1.5 text-[11.5px] text-ink-3">
                              {c.referrals.reachable} of {c.referrals.total} contacts could refer us.
                            </p>
                          </div>
                        ) : (
                          <p className="mt-2 text-[12px] text-ink-3">
                            Nobody to ask yet — run <code className="text-ink">find-contacts</code> for
                            this company.
                          </p>
                        )}
                        {c.hiring_evidence && (
                          <p className="mt-3 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic">
                            {c.hiring_evidence}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* the hiring post on record — "where did you get this?" answered with a name and a link */}
                    <div className="mt-4">
                      <p className="marginal">Hiring post</p>
                      {c.hiring_post ? (
                        <p className="mt-1.5 text-[12px] leading-relaxed">
                          <a
                            href={c.hiring_post.url}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium underline decoration-line underline-offset-2 hover:decoration-ink"
                          >
                            {c.hiring_post.text ?? c.hiring_post.url}
                          </a>
                          <span className="text-ink-3">
                            {" "}— {c.hiring_post.by ? `posted by ${c.hiring_post.by}` : "the company itself"}
                            {c.hiring_post.source ? ` on ${SOURCE_LABEL[c.hiring_post.source] ?? c.hiring_post.source}` : ""}
                            {c.hiring_post.at ? ` · ${c.hiring_post.at.slice(0, 10)}` : ""}
                          </span>
                        </p>
                      ) : (
                        <p className="mt-1.5 text-[12px] text-ink-3">No post on record yet.</p>
                      )}
                    </div>

                    <Link
                      href={`/companies/${c.id}`}
                      className="group mt-4 inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md
                        border border-line bg-surface px-2.5 text-[11.5px] font-medium transition-colors hover:bg-paper"
                    >
                      View details
                      <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="tnum text-[13px] font-medium">{(n ?? 0).toLocaleString()}</span>
      <span className="marginal">{label}</span>
    </span>
  );
}

function Cell({ v }: { v: string }) {
  return <span className="marginal hidden w-24 shrink-0 truncate sm:block">{v}</span>;
}
