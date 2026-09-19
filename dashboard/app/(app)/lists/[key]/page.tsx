"use client";

/*  A hand-picked list, on a page of its own.
 *
 *  The Companies page answers "what did the pipeline find". This page answers "how are
 *  *my* companies doing" — the ones named in data/lists/<key>.yaml, in the order the
 *  list gives them, and nothing else. Two kinds of fact sit side by side and are
 *  labelled apart: what the list claims (stage, raised, CTC — a report's words) and
 *  what the pipeline has read since (roles on their board, people found, hiring proof).
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight, ChevronRight, Loader2, Search, UserSearch } from "lucide-react";
import { api, useApi, useTaskWatcher } from "@/lib/api";
import { rise } from "@/components/anim";
import { OperatorOnly } from "@/components/access";
import { TaskStrip } from "@/components/shell";

type Row = {
  id: number; rank: number; name: string; city: string | null; what: string | null;
  website: string | null; careers_url: string | null; website_note: string | null;
  stage: string | null; funding: string | null; size: string | null;
  ctc: string | null; ctc_kind: "indicative" | "reported" | "unknown" | null; category: string | null;
  tier: string | null; hiring_status: string | null; hiring_evidence: string | null;
  ats: string | null; email_pattern: string | null;
  roles: { total: number; fresher: number; titles: string[]; best_score: number | null };
  people: {
    total: number; reachable: number; verified: number; by_source: Record<string, number>;
    best: { id: number; name: string | null; email: string | null; confidence: string | null; label: string | null } | null;
  };
  searched_at: string | null;
  ready: boolean;
};
type Payload = {
  key: string; name: string; title: string | null; description: string; source: string | null;
  companies: Row[];
  stats: {
    companies: number; with_website: number; with_roles: number; with_people: number; ready: number;
    people: number; reachable: number; hiring_proven: number; by_city: Record<string, number>;
  };
};

const HIRING: Record<string, string> = {
  verified: "proven", role_missing: "hiring, not our role", not_authorized: "unproven",
  unreachable: "site unreadable", unchecked: "not checked",
};
const SOURCE: Record<string, string> = {
  github: "GitHub", site: "their site", pattern: "email pattern", hunter: "Hunter", "linkedin-public": "LinkedIn (public)",
};

export default function ListPage() {
  const { key } = useParams<{ key: string }>();
  const root = useRef<HTMLDivElement>(null);
  const { data, loading, error, reload } = useApi<Payload>(`/api/lists/${key}`);
  const { tasks, watch, dismiss } = useTaskWatcher();
  const [q, setQ] = useState("");
  const [city, setCity] = useState<string>("all");
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<Set<number | "all">>(new Set());

  const cities = useMemo(() => Object.keys(data?.stats.by_city ?? {}).sort(), [data]);
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return data.companies.filter((c) => {
      if (city !== "all" && !(c.city ?? "").includes(city)) return false;
      if (needle && !`${c.name} ${c.what ?? ""}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, q, city]);

  useGSAP(() => { if (data) rise("[data-row]", { stagger: 0.012, y: 6 }); },
    { scope: root, dependencies: [!!data, city] });

  function mark(id: number | "all", on: boolean) {
    setBusy((prev) => { const n = new Set(prev); if (on) n.add(id); else n.delete(id); return n; });
  }
  async function findAll() {
    mark("all", true);
    const { task_id } = await api.post<{ task_id: string }>(`/api/lists/${key}/find-people`);
    watch(task_id, () => { mark("all", false); void reload(); });
  }
  async function findOne(id: number) {
    mark(id, true);
    const { task_id } = await api.post<{ task_id: string }>(`/api/companies/${id}/find-contacts`);
    watch(task_id, () => { mark(id, false); void reload(); });
  }
  async function resync() {
    const { task_id } = await api.post<{ task_id: string }>(`/api/lists/${key}/sync`);
    watch(task_id, () => void reload());
  }

  if (error) return <p className="py-8 text-[13px] text-ink-3">{error}</p>;
  if (loading && !data) return <p className="py-8 text-[13px] text-ink-3">Reading the list…</p>;
  if (!data) return null;
  const s = data.stats;

  return (
    <div ref={root} className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="marginal">Special list</p>
          <h1 className="mt-1 font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
            {data.name}{data.title ? <span className="font-normal text-ink-2"> — {data.title}</span> : null}
          </h1>
          <p className="mt-1 max-w-[64ch] text-[12.5px] leading-relaxed text-ink-2">{data.description}</p>
          {/* the one rule of this page: nothing joins it because a scraper found it */}
          <p className="mt-1.5 text-[12px] text-ink-3">
            Hand-picked only — exactly the {s.companies} companies in the list file, whatever the scrapers find.
            Every other India company is under{" "}
            <Link href="/companies?view=india" className="underline decoration-line underline-offset-2 hover:decoration-ink hover:text-ink">
              Companies → India — all
            </Link>.
          </p>
        </div>
        <OperatorOnly>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={resync}
              className="h-8 cursor-pointer rounded-md border border-line bg-surface px-3 text-[12px] text-ink-2 transition-colors hover:text-ink"
            >
              Re-read list
            </button>
            <button
              onClick={findAll}
              disabled={busy.has("all")}
              className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md bg-ink px-3 text-[12.5px] font-medium text-on-ink transition-opacity hover:opacity-85 disabled:opacity-50"
            >
              {busy.has("all") ? <Loader2 size={13} className="animate-spin" /> : <UserSearch size={13} />}
              Find people at all {s.companies}
            </button>
          </div>
        </OperatorOnly>
      </header>

      {/* ------------------------------------------------------------ the numbers */}
      <section className="plate grid grid-cols-2 divide-line sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
        <Num label="Companies" n={s.companies} hint={Object.entries(s.by_city).map(([c, n]) => `${n} ${c}`).join(" · ")} />
        <Num label="Roles found" n={s.with_roles} hint="companies with a role on their board" />
        <Num label="Hiring proven" n={s.hiring_proven} hint="on their own board or careers page" />
        <Num label="People found" n={s.people} hint={`at ${s.with_people} companies`} />
        <Num label="Could refer" n={s.reachable} hint="engineer or founder, with an address" />
        <Num label="Ready to ask" n={s.ready} hero hint="someone to write to today" />
      </section>

      {/* ------------------------------------------------------------ controls */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-line p-px">
          {["all", ...cities].map((c) => (
            <button
              key={c}
              onClick={() => setCity(c)}
              className={`cursor-pointer rounded-[5px] px-3 py-1.5 text-[12px] transition-colors ${
                city === c ? "bg-ink font-medium" : "text-ink-2 hover:text-ink"
              }`}
              style={city === c ? { color: "var(--on-ink)" } : undefined}
            >
              {c === "all" ? `All ${s.companies}` : `${c} ${s.by_city[c] ?? ""}`}
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
        <span className="marginal ml-auto">{rows.length} shown</span>
      </div>

      {/* ------------------------------------------------------------ the table */}
      <div className="plate overflow-x-auto">
        <div className="min-w-[880px]">
          <div className="grid grid-cols-[2rem_1.25rem_minmax(14rem,2fr)_5.5rem_6.5rem_6rem_7.5rem_5rem_7rem_6rem_5.5rem] items-center gap-3 border-b border-line px-4 py-2">
            <span className="marginal">#</span>
            <span />
            <span className="marginal">Company</span>
            <span className="marginal">City</span>
            <span className="marginal" title="From the report">Stage</span>
            <span className="marginal" title="From the report">Raised</span>
            <span className="marginal" title="From the report — a market range, not an offer">CTC</span>
            <span className="marginal text-right">Roles</span>
            <span className="marginal text-right">People</span>
            <span className="marginal">Hiring</span>
            <span />
          </div>
          {rows.length === 0 ? (
            <p className="px-4 py-8 text-[13px] text-ink-3">Nothing matches.</p>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((c) => {
                const isOpen = open === c.id;
                return (
                  <li key={c.id} data-row>
                    <div
                      className="grid grid-cols-[2rem_1.25rem_minmax(14rem,2fr)_5.5rem_6.5rem_6rem_7.5rem_5rem_7rem_6rem_5.5rem] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2"
                    >
                      <span className="tnum text-right text-[11px] text-ink-3">{c.rank}</span>
                      <button onClick={() => setOpen(isOpen ? null : c.id)} aria-expanded={isOpen} aria-label="Details" className="cursor-pointer">
                        <ChevronRight size={13} className={`text-ink-3 transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`} />
                      </button>
                      <button onClick={() => setOpen(isOpen ? null : c.id)} className="min-w-0 cursor-pointer text-left">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[13px] font-medium">{c.name}</span>
                          {c.ready && <span className="chip chip-solid">ready</span>}
                          {c.category === "large-tech" && <span className="chip">large tech</span>}
                          {!c.website && <span className="chip" title={c.website_note ?? ""}>no website</span>}
                        </span>
                        <span className="block truncate text-[11.5px] text-ink-3">{c.what ?? "—"}</span>
                      </button>
                      <span className="truncate text-[12px] text-ink-2">{c.city ?? "—"}</span>
                      <span className="truncate text-[12px] text-ink-2">{c.stage ?? "—"}</span>
                      <span className="tnum truncate text-[11.5px] text-ink-2">{c.funding ?? "—"}</span>
                      <span
                        className="tnum truncate text-[11.5px] text-ink-2"
                        title={c.ctc_kind === "reported" ? "A publicly posted, role-specific figure" : c.ctc_kind === "indicative" ? "Indicative market range from the report — not the company's promise" : ""}
                      >
                        {c.ctc ? `${c.ctc_kind === "indicative" ? "~" : ""}${c.ctc}` : "—"}
                      </span>
                      <span className={`tnum text-right text-[12px] ${c.roles.total ? "" : "text-ink-3"}`}>
                        {c.roles.total ? `${c.roles.total}${c.roles.fresher ? ` · ${c.roles.fresher} fr` : ""}` : "—"}
                      </span>
                      <span className={`tnum text-right text-[12px] ${c.people.total ? "" : "text-ink-3"}`}>
                        {c.people.total ? `${c.people.total} · ${c.people.reachable} ask` : "—"}
                      </span>
                      <span className={`text-[11.5px] ${c.hiring_status === "verified" ? "font-medium" : "text-ink-3"}`}>
                        {HIRING[c.hiring_status ?? "unchecked"] ?? c.hiring_status}
                      </span>
                      <OperatorOnly>
                        <button
                          onClick={() => findOne(c.id)}
                          disabled={busy.has(c.id) || busy.has("all")}
                          className="inline-flex h-7 cursor-pointer items-center gap-1 justify-self-end rounded-md border border-line bg-surface px-2 text-[11px] text-ink-2 transition-colors hover:text-ink disabled:opacity-50"
                          title="GitHub commits → their site → email pattern → Hunter"
                        >
                          {busy.has(c.id) ? <Loader2 size={11} className="animate-spin" /> : <UserSearch size={11} />}
                          people
                        </button>
                      </OperatorOnly>
                    </div>

                    {isOpen && (
                      <div className="grid gap-5 border-t border-line bg-surface-2 px-4 py-4 md:grid-cols-3">
                        <div>
                          <p className="marginal">Who to ask</p>
                          {c.people.best ? (
                            <div className="mt-2 text-[12px]">
                              <p><span className="font-medium">{c.people.best.name ?? "unnamed"}</span>
                                <span className="text-ink-3"> — {c.people.best.label}</span></p>
                              <p className="tnum mt-0.5 text-[11.5px] text-ink-3">{c.people.best.email} · {c.people.best.confidence}</p>
                              <p className="mt-1.5 text-[11.5px] text-ink-3">
                                {c.people.reachable} of {c.people.total} could refer · {c.people.verified} verified addresses
                              </p>
                            </div>
                          ) : (
                            <p className="mt-2 text-[12px] text-ink-3">
                              {c.people.total ? `${c.people.total} found, none with a usable address yet.` : "Nobody found yet."}
                            </p>
                          )}
                          {Object.keys(c.people.by_source).length > 0 && (
                            <p className="mt-2 text-[11px] text-ink-3">
                              {Object.entries(c.people.by_source).map(([k, n]) => `${n} via ${SOURCE[k] ?? k}`).join(" · ")}
                            </p>
                          )}
                          {c.email_pattern && <p className="tnum mt-1 text-[11px] text-ink-3">pattern {c.email_pattern}</p>}
                        </div>
                        <div>
                          <p className="marginal">Roles on their board</p>
                          {c.roles.titles.length ? (
                            <ul className="mt-2 space-y-1 text-[12px]">
                              {c.roles.titles.map((t) => <li key={t} className="truncate">{t}</li>)}
                              {c.roles.total > c.roles.titles.length && (
                                <li className="text-ink-3">+{c.roles.total - c.roles.titles.length} more</li>
                              )}
                            </ul>
                          ) : (
                            <p className="mt-2 text-[12px] text-ink-3">
                              {c.ats ? "Board found, no fitting roles on it." : "No public job board found yet."}
                            </p>
                          )}
                          {c.hiring_evidence && (
                            <p className="mt-3 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic">{c.hiring_evidence}</p>
                          )}
                        </div>
                        <div>
                          <p className="marginal">From the report</p>
                          <dl className="mt-2 grid grid-cols-[5rem_1fr] gap-y-1 text-[12px]">
                            <dt className="text-ink-3">Stage</dt><dd>{c.stage ?? "—"}</dd>
                            <dt className="text-ink-3">Raised</dt><dd>{c.funding ?? "—"}</dd>
                            <dt className="text-ink-3">Size</dt><dd>{c.size ?? "not verified"}</dd>
                            <dt className="text-ink-3">CTC</dt><dd>{c.ctc ?? "—"}{c.ctc_kind === "indicative" ? <span className="text-ink-3"> (indicative)</span> : c.ctc_kind === "reported" ? <span className="text-ink-3"> (reported)</span> : null}</dd>
                          </dl>
                          <div className="mt-3 flex flex-wrap gap-2">
                            {c.website && (
                              <a href={c.website} target="_blank" rel="noreferrer" className="text-[11.5px] underline decoration-line underline-offset-2 hover:decoration-ink">
                                {c.website.replace(/^https?:\/\/(www\.)?/, "")}
                              </a>
                            )}
                            {c.careers_url && (
                              <a href={c.careers_url} target="_blank" rel="noreferrer" className="text-[11.5px] underline decoration-line underline-offset-2 hover:decoration-ink">careers</a>
                            )}
                          </div>
                          <Link
                            href={`/companies/${c.id}`}
                            className="group mt-3 inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[11.5px] font-medium transition-colors hover:bg-paper"
                          >
                            Everything we know
                            <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                          </Link>
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
        Stage, raised and CTC are the report&apos;s words{data.source ? ` (${data.source})` : ""}, not the pipeline&apos;s.
        A CTC marked ~ is an indicative market range; unmarked is a publicly reported role figure. Roles, people and
        hiring proof are read from the company&apos;s own board, site and public code. A guessed website is only kept
        when the page names the company.
      </p>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </div>
  );
}

function Num({ label, n, hint, hero = false }: { label: string; n: number; hint?: string; hero?: boolean }) {
  return (
    <div className="p-4">
      <p className="marginal">{label}</p>
      <p className={`tnum mt-1.5 leading-none font-medium tracking-[-0.02em] ${hero ? "text-[34px]" : "text-[24px]"}`}>{n.toLocaleString()}</p>
      {hint ? <p className="mt-1.5 text-[11px] leading-snug text-ink-3">{hint}</p> : null}
    </div>
  );
}
