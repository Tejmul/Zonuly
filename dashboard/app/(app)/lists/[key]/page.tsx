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
import { fundingOf, inCity, type City } from "@/lib/funding";
import { rise } from "@/components/anim";
import { CityFilter, FundingCell, HiringCell, PayCell, cityCounts } from "@/components/funding";
import { ErrorNote, Loading } from "@/components/ui/primitives";
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
  // the India ₹20L pay floor (jobhunter/india.py): ok / below / unknown + the quote
  pay_bar?: { state: "ok" | "below" | "unknown" | null; evidence?: string | null; label?: string | null } | null;
  below_bar_reason?: string | null;
  // the complete bar (guardrails.complete_bar) and the parts still missing
  complete?: boolean; missing?: string[];
  // What the pipeline itself has read, next to the report's claims. Newer backends
  // only — every one of these may be missing.
  funding_stage?: string | null; funding_round_label?: string | null; funding_amount_usd_m?: number | null;
  funding_announced?: string | null; funding_investors?: string[] | string | null; valuation_usd_m?: number | null;
  pay_power?: number | { score: number | null; band: string | null; why: string | null } | null;
  pay_power_band?: string | null; hiring_roles?: string[] | string | null;
  priority_score?: number | null; priority_why?: string | null; overrated?: boolean | null; hype_reason?: string | null;
};
type Payload = {
  key: string; name: string; title: string | null; description: string; source: string | null;
  companies: Row[];
  // rows under the India ₹20L floor: hidden from the list itself, shown collapsed below it
  below_bar?: Row[];
  pay_floor_lpa?: number;
  stats: {
    companies: number; with_website: number; with_roles: number; with_people: number; ready: number;
    people: number; reachable: number; hiring_proven: number; by_city: Record<string, number>;
  };
};

const COLS = "grid grid-cols-[2rem_1.25rem_minmax(14rem,2fr)_6rem_9.5rem_7rem_7rem_5rem_7rem_7.5rem_5.5rem] items-center gap-3";

function payOf(c: Row) {
  const pp = c.pay_power;
  if (pp && typeof pp === "object") return pp;
  return { score: typeof pp === "number" ? pp : null, band: c.pay_power_band ?? null, why: null };
}
const MISSING: Record<string, string> = {
  website: "no website", description: "no description", hiring: "no hiring proof", referrer_email: "no referrer email",
};
const SOURCE: Record<string, string> = {
  github: "GitHub", site: "their site", pattern: "email pattern", hunter: "Hunter", "linkedin-public": "LinkedIn (public)",
};

export default function ListPage() {
  const { key } = useParams<{ key: string }>();
  const root = useRef<HTMLDivElement>(null);
  const { data, error, status, reload } = useApi<Payload>(`/api/lists/${key}`);
  const { tasks, watch, dismiss } = useTaskWatcher();
  const [q, setQ] = useState("");
  const [city, setCity] = useState<City | "all">("all");
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<Set<number | "all">>(new Set());

  const counts = useMemo(() => cityCounts(data?.companies ?? [], (c) => c.city), [data]);
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return data.companies.filter((c) => {
      if (!inCity(c.city, city)) return false;
      if (needle && !`${c.name} ${c.what ?? ""}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, q, city]);

  useGSAP(() => { if (data) rise("[data-row]", { stagger: 0.012, y: 6 }); },
    { scope: root, dependencies: [!!data] });

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

  // A list the server does not have yet is not an error — it has not been published.
  if (status === 404 || (data && !data.companies?.length)) {
    return (
      <div className="plate px-6 py-14 text-center">
        <p className="marginal">Special list</p>
        <p className="mt-3 text-[14px] font-medium">This list hasn&rsquo;t been published to the server yet</p>
        <p className="mx-auto mt-2 max-w-[56ch] text-[12.5px] leading-relaxed text-ink-3">
          The companies are picked by hand in a list file and appear here once that file is on the server
          the dashboard reads from. Everything the scrapers found is already under{" "}
          <Link href="/companies?view=india" className="underline decoration-line underline-offset-2 hover:text-ink">
            Companies → India — all
          </Link>.
        </p>
      </div>
    );
  }
  if (error && !data) return <div className="plate"><ErrorNote message={error} onRetry={reload} /></div>;
  if (!data) return <div className="plate"><Loading label="Reading the list" rows={10} /></div>;
  const s: Payload["stats"] = data.stats ?? {
    companies: data.companies.length, with_website: 0, with_roles: 0, hiring_proven: 0, people: 0,
    with_people: 0, reachable: 0, ready: 0, by_city: {},
  };

  // complete rows first; the rest under "Still being filled" — no hand-picked row disappears
  const shown = rows.filter((c) => c.complete !== false);
  const filling = rows.filter((c) => c.complete === false);
  const renderRow = (c: Row) => {
    const isOpen = open === c.id;
    return (
      <li key={c.id} data-row>
        <div className={`${COLS} px-4 py-2.5 transition-colors hover:bg-surface-2`}>
          <span className="tnum text-right text-[11px] text-ink-3">{c.rank}</span>
          <button onClick={() => setOpen(isOpen ? null : c.id)} aria-expanded={isOpen} aria-label="Details" className="cursor-pointer">
            <ChevronRight size={13} className={`text-ink-3 transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`} />
          </button>
          {/* The name always wins its line: badges sit after it and never push it out, and
              what is still missing gets a line of its own, clipped to this column — the chips
              used to share the name's line, squeeze it to nothing and spill into City. */}
          <button onClick={() => setOpen(isOpen ? null : c.id)} className="min-w-0 cursor-pointer overflow-hidden text-left">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-[4rem] truncate text-[13px] font-medium" title={c.name}>{c.name}</span>
              {c.ready && <span className="chip chip-solid shrink-0">ready</span>}
              {c.category === "large-tech" && <span className="chip shrink-0">large tech</span>}
              {c.overrated && <span className="chip chip-strike shrink-0" title={c.hype_reason ?? "Too well known to be underrated"}>overrated</span>}
              {typeof c.priority_score === "number" && Number.isFinite(c.priority_score) && (
                <span className="chip tnum shrink-0" title={c.priority_why ?? "Priority score"}>P{Math.round(c.priority_score)}</span>
              )}
            </span>
            <span className="block truncate text-[11.5px] text-ink-3">{c.what ?? "—"}</span>
            {(() => {
              const gaps = [
                ...(c.complete === false ? (c.missing ?? []).filter((m) => m !== "website").map((m) => MISSING[m] ?? m) : []),
                ...(!c.website ? ["no website"] : []),
              ];
              return gaps.length ? (
                <span className="mt-0.5 block truncate text-[10.5px] text-ink-3" title={gaps.join(" · ")}>
                  still filling: {gaps.map((g) => g.replace(/^no /, "")).join(" · ")}
                </span>
              ) : null;
            })()}
          </button>
          <span className="truncate text-[12px] text-ink-2">{c.city ?? "—"}</span>
          <span className="min-w-0"><ListFunding c={c} /></span>
          <span className="text-right"><PayCell {...payOf(c)} /></span>
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
          <span className="min-w-0"><HiringCell status={c.hiring_status} roles={c.hiring_roles} /></span>
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
                <p className="mt-3 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic [overflow-wrap:anywhere]">{c.hiring_evidence}</p>
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
  };
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
        <Num label="Companies" n={s.companies} hint={`${counts.Bengaluru} Bengaluru · ${counts.Hyderabad} Hyderabad`} />
        <Num label="Roles found" n={s.with_roles} hint="companies with a role on their board" />
        <Num label="Hiring proven" n={s.hiring_proven} hint="on their own board or careers page" />
        <Num label="People found" n={s.people} hint={`at ${s.with_people} companies`} />
        <Num label="Could refer" n={s.reachable} hint="engineer or founder, with an address" />
        <Num label="Ready to ask" n={s.ready} hero hint="someone to write to today" />
      </section>

      {/* ------------------------------------------------------------ controls */}
      <div className="flex flex-wrap items-center gap-2">
        <CityFilter value={city} onChange={setCity} counts={counts} />
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
        <div className="min-w-[1080px]">
          <div className={`${COLS} border-b border-line px-4 py-2`}>
            <span className="marginal">#</span>
            <span />
            <span className="marginal">Company</span>
            <span className="marginal">City</span>
            <span className="marginal" title="Round · amount · when · valuation. Read by the pipeline where it could; otherwise the report's words, marked 'report'.">Funding</span>
            <span className="marginal text-right" title="unknown → thin → funded → deep pockets → pays">Pay power</span>
            <span className="marginal" title="From the report — a market range, not an offer">CTC</span>
            <span className="marginal text-right">Roles</span>
            <span className="marginal text-right">People</span>
            <span className="marginal">Hiring</span>
            <span />
          </div>
          {shown.length === 0 ? (
            <p className="px-4 py-8 text-[13px] text-ink-3">
              {rows.length ? "No company on this list is complete yet — every one is below, being filled." : "Nothing matches."}
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {shown.map(renderRow)}
            </ul>
          )}
        </div>
      </div>

      {filling.length > 0 && (
        <details className="plate group">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-[12.5px] text-ink-2 hover:text-ink">
            <ChevronRight size={13} className="text-ink-3 transition-transform duration-200 group-open:rotate-90" />
            <span className="font-medium">Still being filled</span>
            <span className="chip tnum">{filling.length}</span>
            <span className="marginal ml-auto">missing a description, hiring proof or a referrer with an address — filled first every tick</span>
          </summary>
          <div className="overflow-x-auto border-t border-line">
            <ul className="min-w-[1080px] divide-y divide-line">{filling.map(renderRow)}</ul>
          </div>
        </details>
      )}

      <BelowBar rows={data.below_bar ?? []} floor={data.pay_floor_lpa ?? 20} />

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

/** List rows whose best evidence of full-time fresher pay is under the India floor —
 *  hidden from the list and from every queue, never deleted, and back on their own when
 *  a posting or the list states a figure at or above it. Collapsed by default. */
function BelowBar({ rows, floor }: { rows: Row[]; floor: number }) {
  if (!rows.length) return null;
  return (
    <details className="plate group">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-[12.5px] text-ink-2 hover:text-ink">
        <ChevronRight size={13} className="text-ink-3 transition-transform duration-200 group-open:rotate-90" />
        <span className="font-medium">Below the ₹{floor}L bar</span>
        <span className="chip tnum">{rows.length}</span>
        <span className="marginal ml-auto">hidden — not chased, not deleted</span>
      </summary>
      <ul className="divide-y divide-line border-t border-line">
        {rows.map((c) => (
          <li key={c.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2">
            <span className="tnum w-6 text-right text-[11px] text-ink-3">{c.rank}</span>
            <Link href={`/companies/${c.id}`} className="text-[13px] font-medium hover:underline">{c.name}</Link>
            <span className="text-[12px] text-ink-3">{c.city ?? "—"}</span>
            <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2" title={c.below_bar_reason ?? ""}>
              {c.below_bar_reason ?? c.pay_bar?.evidence ?? "—"}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The pipeline's own reading when it has one; the report's words, labelled, when not. */
function ListFunding({ c }: { c: Row }) {
  // `funding` on a list row is the report's sentence, not the structured round
  const src = { ...c, funding: null };
  const f = fundingOf(src);
  if (f.stage || f.amountUsdM || f.announced || f.valuationUsdM) return <FundingCell row={src} />;
  if (!c.stage && !c.funding) return <span className="text-ink-3">—</span>;
  return (
    <span className="flex min-w-0 flex-col gap-0.5" title="From the report, not yet read by the pipeline">
      <span className="flex min-w-0 items-center gap-1.5">
        {c.stage ? <span className="chip shrink-0 !h-[17px] !px-1.5 text-ink">{c.stage}</span> : null}
        {c.funding ? <span className="tnum truncate text-[12px] font-semibold">{c.funding}</span> : null}
      </span>
      <span className="text-[10.5px] text-ink-3">report</span>
    </span>
  );
}

function Num({ label, n, hint, hero = false }: { label: string; n: number; hint?: string; hero?: boolean }) {
  return (
    <div className="p-4">
      <p className="marginal">{label}</p>
      <p className={`tnum mt-1.5 leading-none font-medium tracking-[-0.02em] ${hero ? "text-[34px]" : "text-[24px]"}`}>{Number.isFinite(n) ? n.toLocaleString() : "—"}</p>
      {hint ? <p className="mt-1.5 text-[11px] leading-snug text-ink-3">{hint}</p> : null}
    </div>
  );
}
