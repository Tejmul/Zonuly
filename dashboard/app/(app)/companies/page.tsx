"use client";

/*  Companies.
 *
 *  The last version put seven filters in one strip with no hint of what separated them,
 *  a benchmark table above the list, and rows with no column headings — every cell was a
 *  phrase and nothing lined up. Tejmul: "I don't see a difference between each option."
 *
 *  Now: four views, each with a one-line meaning that changes as you click; the special
 *  list gets its own page (★ India); the rows are a real table with headings, and each
 *  cell is a number or a word, not a sentence. The pay benchmark moved into a tooltip on
 *  the pay column and a footnote — it is the answer to a question, not the page.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight, ChevronRight, Search, Star } from "lucide-react";
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
  region: string | null; city?: string | null; list_key?: string | null; list_rank?: number | null;
  tier: string | null; tier_reason: string | null;
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

/* Four views. The meaning is printed under the control, so the difference between them
   is never a guess. */
const VIEWS = [
  { key: "ready", label: "Ready to ask", meaning: "Hiring is proven on their own site and there is a person with a usable address." },
  { key: "targets", label: "Worth chasing", meaning: "Graded tier 1, tier 2 or prospect — pays enough, or hiring is proven and pay is unknown." },
  { key: "anywhere", label: "Hire from anywhere", meaning: "At least one posting says work from any country — the remote-from-India path." },
  // Every India company from every source. Deliberately NOT the ★ list: that one is
  // hand-picked and stays exactly what Tejmul put in it, whatever the scrapers find.
  { key: "india", label: "India — all", meaning: "Every company headquartered in India that any source has found. The ★ India list is separate and hand-picked; it never changes because of a scrape." },
  { key: "all", label: "Everything", meaning: "Every company in the database, including the ones we have only a name for." },
] as const;
type View = (typeof VIEWS)[number]["key"];

const HIRING: Record<string, string> = {
  verified: "proven", role_missing: "not our role", not_authorized: "unproven", unreachable: "unreadable", unchecked: "—",
};
const REGION: Record<string, string> = { us: "US", uk: "UK", de: "DE", nl: "NL", eu: "EU", india: "India", remote: "Remote" };
const PP_BAND: Record<string, string> = { pays: "pays", deep_pockets: "deep pockets", funded: "funded", thin: "thin", unknown: "unknown" };
const PP_RULES =
  "Pay 100 · pays: a posting states ≥ ₹30 L.\n" +
  "85 · deep pockets: $10M+ in 24 months, Series A+ in 30 months, or valued ≥ $50M.\n" +
  "65 · funded: $3–10M in 30 months, or a recent YC batch with 10+ people.\n" +
  "30 · thin: under $2M, pre-seed, or an old raise.\n" +
  "0 · unknown: nothing found yet.";
const SOURCE_LABEL: Record<string, string> = {
  x: "X", hn: "Hacker News", yc: "the YC directory", ats: "their own job board", careers: "their careers page",
  reddit: "Reddit", exa: "web search", greenhouse: "their Greenhouse board", lever: "their Lever board",
  ashby: "their Ashby board", remoteok: "RemoteOK", wwr: "We Work Remotely",
};

function fundingLine(f?: Row["funding"]): string | null {
  if (!f || (!f.stage && !f.announced)) return null;
  const parts: string[] = [];
  if (f.stage) parts.push(f.stage.replace(/\b\w/g, (m) => m.toUpperCase()));
  if (f.amount_usd_m) parts.push(`$${f.amount_usd_m}M`);
  if (f.announced) parts.push(f.announced.slice(0, 7));
  return parts.join(" · ") || null;
}

const GRID = "grid grid-cols-[2rem_1.25rem_2.25rem_minmax(14rem,2fr)_4rem_4.5rem_6rem_5.5rem_7.5rem] items-center gap-3";

export default function CompaniesPage() {
  return (
    <Suspense fallback={<p className="py-8 text-[13px] text-ink-3">Reading the registry…</p>}>
      <Companies />
    </Suspense>
  );
}

function Companies() {
  const root = useRef<HTMLDivElement>(null);
  const { data, loading, error, reload } = useApi<Payload>("/api/companies/grouped");
  const [q, setQ] = useState("");
  // /companies?view=india opens straight on a view — the ★ page links here that way
  const wanted = useSearchParams().get("view");
  const [view, setView] = useState<View>(
    (VIEWS.some((v) => v.key === wanted) ? (wanted as View) : "ready"),
  );
  const [open, setOpen] = useState<number | null>(null);

  const counts = useMemo(() => {
    const cs = data?.companies ?? [];
    return {
      ready: cs.filter((c) => c.ready).length,
      targets: cs.filter((c) => ["tier1", "tier2", "prospect"].includes(c.tier ?? "")).length,
      anywhere: cs.filter((c) => c.roles.anywhere > 0).length,
      india: cs.filter((c) => c.region === "india").length,
      all: cs.length,
    };
  }, [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return data.companies.filter((c) => {
      if (view === "ready" && !c.ready) return false;
      if (view === "targets" && !["tier1", "tier2", "prospect"].includes(c.tier ?? "")) return false;
      if (view === "anywhere" && !(c.roles.anywhere > 0)) return false;
      if (view === "india" && c.region !== "india") return false;
      if (needle && !`${c.name} ${c.description ?? ""}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, q, view]);

  useGSAP(() => { if (data) rise("[data-row]", { stagger: 0.012, y: 6 }); },
    { scope: root, dependencies: [!!data, view] });

  if (error) return <p className="py-8 text-[13px] text-ink-3">{error}</p>;
  if (loading && !data) return <p className="py-8 text-[13px] text-ink-3">Reading the registry…</p>;
  if (!data) return null;
  const meaning = VIEWS.find((v) => v.key === view)!.meaning;

  return (
    <div ref={root} className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Companies</h1>
          <p className="mt-1 max-w-[62ch] text-[12.5px] leading-relaxed text-ink-2">
            {data.stats.companies.toLocaleString()} companies, one row each. Pick a view; the line under it says what
            the view means.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            href="/lists/india-star"
            className="group inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-[12.5px] font-medium transition-colors hover:bg-surface-2"
          >
            <Star size={13} />
            India — the special list
            {data.stats.on_lists ? <span className="tnum text-ink-3">{data.stats.on_lists}</span> : null}
            <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
          <OperatorOnly><AddCompany onAdded={reload} /></OperatorOnly>
        </div>
      </header>

      {/* ------------------------------------------------------------ the views */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border border-line p-px">
            {VIEWS.map((v) => (
              <button
                key={v.key}
                onClick={() => setView(v.key)}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-[12px] transition-colors ${
                  view === v.key ? "bg-ink font-medium" : "text-ink-2 hover:text-ink"
                }`}
                style={view === v.key ? { color: "var(--on-ink)" } : undefined}
              >
                {v.label}
                <span className={`tnum text-[11px] ${view === v.key ? "opacity-70" : "text-ink-3"}`}>{counts[v.key].toLocaleString()}</span>
              </button>
            ))}
          </div>
          <label className="relative min-w-0 flex-1 sm:max-w-xs">
            <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Find a company"
              className="h-8 w-full rounded-md border border-line bg-surface pr-2 pl-7 text-[12px] placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
            />
          </label>
          <span className="marginal ml-auto">{rows.length.toLocaleString()} shown</span>
        </div>
        <p className="mt-2 text-[12px] text-ink-3">{meaning}</p>
      </div>

      {/* ------------------------------------------------------------ the table */}
      <div className="plate overflow-x-auto">
        <div className="min-w-[860px]">
          <div className={`${GRID} border-b border-line px-4 py-2`}>
            <span className="marginal">#</span>
            <span />
            <span className="marginal" title="tier1 ≥ ₹30 LPA · tier2 ₹24–30 · PRO = pay unknown, hiring proven">Tier</span>
            <span className="marginal">Company</span>
            <span className="marginal">Where</span>
            <span className="marginal text-right" title="open roles · fresher roles">Roles</span>
            <span className="marginal text-right" title="people found · people who could refer">People</span>
            <span className="marginal" title="Proven means their own board or careers page lists open roles">Hiring</span>
            <span className="marginal text-right" title={PP_RULES}>Pay</span>
          </div>
          {rows.length === 0 ? (
            <p className="px-4 py-8 text-[13px] text-ink-3">
              No company in this view. Try another one, or run <code className="text-ink">targets grade</code>.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((c, i) => {
                const isOpen = open === c.id;
                const pp = c.pay_power?.score ?? 0;
                return (
                  <li key={c.id} data-row>
                    <button
                      onClick={() => setOpen(isOpen ? null : c.id)}
                      aria-expanded={isOpen}
                      className={`${GRID} w-full cursor-pointer px-4 py-2.5 text-left transition-colors hover:bg-surface-2`}
                    >
                      <span className="tnum text-right text-[11px] text-ink-3">{i + 1}</span>
                      <ChevronRight size={13} className={`text-ink-3 transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`} />
                      <span><TierPill tier={c.tier} /></span>
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[13px] font-medium">{c.name}</span>
                          {c.list_key && <span className="chip" title="On the ★ India list">★</span>}
                          {c.ready && <span className="chip chip-solid">ready</span>}
                          {c.trust === "bare" && <span className="chip">name only</span>}
                        </span>
                        <span className="block truncate text-[11.5px] text-ink-3">{c.description ?? "—"}</span>
                      </span>
                      <span className="truncate text-[12px] text-ink-2">{c.city ?? REGION[c.region ?? ""] ?? c.region ?? "—"}</span>
                      <span className={`tnum text-right text-[12px] ${c.roles.total ? "" : "text-ink-3"}`}>
                        {c.roles.total ? `${c.roles.total}${c.roles.fresher ? ` · ${c.roles.fresher}` : ""}` : "—"}
                      </span>
                      <span className={`tnum text-right text-[12px] ${c.referrals.total ? "" : "text-ink-3"}`}>
                        {c.referrals.total ? `${c.referrals.total} · ${c.referrals.reachable}` : "—"}
                      </span>
                      <span className={`text-[11.5px] ${c.hiring_status === "verified" ? "font-medium" : "text-ink-3"}`}>
                        {HIRING[c.hiring_status ?? "unchecked"] ?? c.hiring_status}
                      </span>
                      <span
                        className={`tnum truncate text-right text-[11.5px] ${pp >= 65 ? "" : "text-ink-3"}`}
                        title={c.pay_power?.why ?? PP_RULES}
                      >
                        {pp >= 65 ? `${pp} · ${PP_BAND[c.pay_power!.band ?? ""] ?? c.pay_power!.band}` : fundingLine(c.funding) ?? "unknown"}
                      </span>
                    </button>

                    {isOpen && (
                      <div className="border-t border-line bg-surface-2 px-4 py-4">
                        <p className="max-w-3xl text-[12.5px] leading-relaxed text-ink-2">
                          {c.description ?? "No description yet — run targets enrich for this company."}
                        </p>
                        <div className="mt-4 grid gap-5 md:grid-cols-3">
                          <div>
                            <p className="marginal">Roles — {c.roles.total} across {c.roles.families.length} families</p>
                            <ul className="mt-2 space-y-1.5">
                              {c.roles.families.map((f) => (
                                <li key={f.family}>
                                  <div className="flex items-baseline gap-2">
                                    <span className="text-[12px] font-medium">{f.family}</span>
                                    <span className="tnum text-[11px] text-ink-3">{f.count}</span>
                                    {f.fresher > 0 && <span className="chip">{f.fresher} fresher</span>}
                                    {f.anywhere > 0 && <span className="chip">{f.anywhere} anywhere</span>}
                                  </div>
                                  <p className="truncate text-[11.5px] text-ink-3">{f.titles.join(" · ")}</p>
                                </li>
                              ))}
                              {c.roles.families.length === 0 && <li className="text-[12px] text-ink-3">No roles scraped yet.</li>}
                            </ul>
                          </div>
                          <div>
                            <p className="marginal">Who to ask</p>
                            {c.referrals.best ? (
                              <div className="mt-2">
                                <p className="text-[12px]">
                                  <span className="font-medium">{c.referrals.best.name}</span>
                                  <span className="text-ink-3"> — {c.referrals.best.label}</span>
                                </p>
                                <p className="tnum mt-0.5 text-[11.5px] text-ink-3">{c.referrals.best.email} · {c.referrals.best.confidence}</p>
                                <p className="mt-1.5 text-[11.5px] text-ink-3">{c.referrals.reachable} of {c.referrals.total} could refer us.</p>
                              </div>
                            ) : (
                              <p className="mt-2 text-[12px] text-ink-3">Nobody to ask yet.</p>
                            )}
                            {c.hiring_evidence && (
                              <p className="mt-3 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic">{c.hiring_evidence}</p>
                            )}
                          </div>
                          <div>
                            <p className="marginal">Why this grade</p>
                            <p className="mt-2 text-[12px] text-ink-2">{c.tier_reason ?? "Not graded yet."}</p>
                            {c.pay_power?.why && <p className="mt-1.5 text-[11.5px] text-ink-3">{c.pay_power.why}</p>}
                            <p className="marginal mt-3">Hiring post</p>
                            {c.hiring_post ? (
                              <p className="mt-1.5 text-[12px] leading-relaxed">
                                <a href={c.hiring_post.url} target="_blank" rel="noreferrer" className="font-medium underline decoration-line underline-offset-2 hover:decoration-ink">
                                  {c.hiring_post.text ?? c.hiring_post.url}
                                </a>
                                <span className="text-ink-3">
                                  {" "}— {c.hiring_post.by ? `posted by ${c.hiring_post.by}` : "the company itself"}
                                  {c.hiring_post.source ? ` on ${SOURCE_LABEL[c.hiring_post.source] ?? c.hiring_post.source}` : ""}
                                </span>
                              </p>
                            ) : (
                              <p className="mt-1.5 text-[12px] text-ink-3">No post on record.</p>
                            )}
                            <Link
                              href={`/companies/${c.id}`}
                              className="group mt-3 inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[11.5px] font-medium transition-colors hover:bg-paper"
                            >
                              Everything we know
                              <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                            </Link>
                          </div>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <p className="max-w-[80ch] text-[11.5px] leading-relaxed text-ink-3">
        Roles is open roles · fresher roles. People is people found · people who could refer (engineer, EM or founder
        with a usable address). Pay shows the Pay Power score when we can judge it (hover for the rules) and the last
        funding round when we cannot. India needs a stated number to go above Thin — rupee pay does not follow dollar rounds.
      </p>
    </div>
  );
}
