"use client";

/*  The dashboard.
 *
 *  The old version was five ranked lists of the same visual weight, which answered
 *  "what is in the database" but never "what is happening" — and its bars were
 *  filled with a surface tint, so they were invisible and the lists were just
 *  numbers.
 *
 *  This one is ordered as an argument, top to bottom:
 *
 *    1. one number  — how many companies are actually ready to ask;
 *    2. two funnels — where everything else went, with the loss at each step named;
 *    3. the gates   — why 1,962 companies becomes 63, one test at a time;
 *    4. the detail  — where roles come from, whether the hiring is real, where and
 *                     what they pay;
 *    5. the list    — the companies you could write to today.
 *
 *  Every panel says in one line what it means. The vocabulary this product uses —
 *  bet, near, chokepoint — is defined by the API, so the glossary at the foot is
 *  the API's own words rather than a second set that can drift out of date.
 */

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight } from "lucide-react";
import { useApi } from "@/lib/api";
import { rise } from "@/components/anim";
import { Bars, Funnel, Gates, Meter, Stat, TableView, type Row } from "@/components/charts";

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

type Overview = {
  jobs: { jobs: number; embedded: number; scored: number; high_match: number };
  funnel: Record<string, number>;
  quota: {
    daily_cap: number; sent_today: number; remaining_today: number;
    send_window: [number, number]; in_window: boolean; send_mode: string;
  };
  by_source: Record<string, number>;
  new_this_week: number;
  companies: number;
  contacts: { total: number; verified: number };
};

const TIER_LABEL: Record<string, string> = {
  tier1: "Tier 1 — ₹30 LPA and up",
  tier2: "Tier 2 — ₹24–30 LPA",
  prospect: "Prospect — pay unknown, hiring proven",
  unknown: "Pay not stated anywhere",
};
const HIRING_LABEL: Record<string, string> = {
  verified: "Proven on their own careers page",
  role_missing: "Hiring, but not this kind of role",
  not_authorized: "Board says so, their site does not",
  unreachable: "Their site could not be read",
  unchecked: "Not checked yet",
};
const SOURCE_LABEL: Record<string, string> = {
  ashby: "Ashby job boards",
  greenhouse: "Greenhouse job boards",
  lever: "Lever job boards",
  hn: "Hacker News — Who Is Hiring",
  wwr: "We Work Remotely",
  remoteok: "RemoteOK",
  yc: "Y Combinator",
};

export default function Dashboard() {
  const root = useRef<HTMLDivElement>(null);
  const net = useApi<Net>("/api/network");
  const ov = useApi<Overview>("/api/overview");

  useGSAP(() => { if (net.data) rise("[data-plate]", { stagger: 0.04 }); },
    { scope: root, dependencies: [!!net.data] });

  if (net.error) return <p className="py-8 text-[13px] text-ink-3">{net.error}</p>;
  if (!net.data) return <p className="py-8 text-[13px] text-ink-3">Reading the pipeline…</p>;

  const { stats, companies, definitions } = net.data;
  const o = ov.data;

  /* --- the gates: why the big number becomes the small one ---------------- */
  const hiringProven = companies.filter((c) => c.hiring_status === "verified").length;
  const hasFresher = companies.filter((c) => c.fresher > 0).length;
  const anywhere = companies.filter((c) => c.anywhere > 0).length;
  const hasLead = companies.filter((c) => c.leads > 0).length;

  const byTier = rank(countBy(companies, (c) => c.tier ?? "unknown"), TIER_LABEL);
  const byRegion = rank(countBy(companies.filter((c) => c.region), (c) => c.region!.toUpperCase()));
  const byHiring = rank(countBy(companies, (c) => c.hiring_status ?? "unchecked"), HIRING_LABEL);
  const bySource = o ? rank(o.by_source, SOURCE_LABEL) : [];

  const ready = companies.filter((c) => c.bet);
  const readyCount = stats.bets ?? ready.length;
  const nearly = companies.filter((c) => !c.bet && c.near);
  const top = [...ready, ...nearly.filter((c) => c.hiring_status === "verified")].slice(0, 10);

  return (
    <div ref={root} className="space-y-5">
      {/* ---------------------------------------------------------- headline */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">
            What the pipeline found
          </h1>
          <p className="mt-1 max-w-[62ch] text-[12.5px] leading-relaxed text-ink-2">
            Everything below is one machine running left to right: it collects job postings,
            keeps the ones that fit you, checks whether the company is really hiring, finds a
            person inside who could refer you, and stops there — waiting for you to approve
            each message.
          </p>
        </div>
        <Link
          href="/companies"
          className="group inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-md
            bg-ink px-3 text-[12.5px] font-medium text-on-ink transition-opacity hover:opacity-85"
        >
          Browse all companies
          <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
        </Link>
      </header>

      {/* ------------------------------------------------------------ the KPIs */}
      <section data-plate className="plate grid grid-cols-2 divide-line lg:grid-cols-4 lg:divide-x">
        <Stat
          hero
          label="Ready to ask today"
          value={stats.bets ?? 0}
          hint="Companies that pass every check and have a person to write to"
        />
        <Stat
          label="One check away"
          value={stats.near ?? 0}
          hint="Pass three of the four; the missing one is named on each company"
        />
        <Stat
          label="Companies mapped"
          value={stats.companies ?? 0}
          hint={o ? `${nf(o.new_this_week)} roles arrived this week` : "across 34 market segments"}
        />
        <Stat
          label="People found"
          value={o?.contacts.total ?? 0}
          hint={o ? `${nf(o.contacts.verified)} with an address we could verify` : undefined}
        />
      </section>

      {/* ----------------------------------------------------------- funnels */}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Plate
          title="From job posting to a role worth applying to"
          sub="Every posting collected, and where the rest fell away."
        >
          {o ? (
            <>
              <Funnel
                unit="roles"
                stages={[
                  { label: "Collected", value: o.jobs.jobs, explain: "Pulled from seven public job boards and Hacker News." },
                  { label: "Read and indexed", value: o.jobs.embedded, explain: "Turned into something comparable against your resume." },
                  { label: "Scored against your resume", value: o.jobs.scored, explain: "Graded for how well the requirements actually match you." },
                  { label: "Worth applying to", value: o.jobs.high_match, explain: "Scored high enough to be worth your time." },
                ]}
              />
              <TableView
                caption="Roles at each stage of matching"
                unit=" roles"
                rows={[
                  { label: "Collected", value: o.jobs.jobs },
                  { label: "Read and indexed", value: o.jobs.embedded },
                  { label: "Scored", value: o.jobs.scored },
                  { label: "Worth applying to", value: o.jobs.high_match },
                ]}
              />
            </>
          ) : (
            <p className="py-3 text-[12px] text-ink-3">Waiting for the pipeline…</p>
          )}
        </Plate>

        <Plate
          title="From a written message to a reply"
          sub="Nothing moves down this list without you clicking approve."
        >
          {o ? (
            <>
              {/* Starts at the pool the drafts are drawn from, not at the first draft.
                  A funnel normalised to its own first stage draws one draft as a
                  full-width bar, which reads as volume when the truth is that almost
                  nothing has been written yet. */}
              <Funnel
                unit=""
                stages={[
                  { label: "Ready to ask", value: readyCount, explain: "Companies that cleared all four checks — the pool every draft is drawn from." },
                  { label: "Drafted", value: o.funnel.drafted ?? 0, explain: "Written, with the evidence it was based on attached." },
                  { label: "You approved", value: o.funnel.approved ?? 0, explain: "The gate. Nothing is sent that you have not read." },
                  { label: "Sent", value: o.funnel.sent ?? 0, explain: "Delivered from your own Gmail, inside the sending window." },
                  { label: "Replied", value: o.funnel.replied ?? 0, explain: "Anyone who wrote back, whatever they said." },
                  { label: "Said yes", value: o.funnel.positive ?? 0, explain: "Agreed to refer you or to talk." },
                ]}
              />
              <div className="mt-4 border-t border-line pt-3">
                <Meter
                  used={o.quota.sent_today}
                  cap={o.quota.daily_cap}
                  label="Sent today"
                  foot={`Hard cap of ${o.quota.daily_cap} a day, between ${hour(o.quota.send_window[0])} and ${hour(
                    o.quota.send_window[1],
                  )}. ${
                    o.quota.send_mode === "dryrun"
                      ? "Currently in dry run — messages are written and recorded, but nothing leaves."
                      : o.quota.in_window
                        ? "Inside the window now."
                        : "Outside the window right now."
                  }`}
                />
              </div>
            </>
          ) : (
            <p className="py-3 text-[12px] text-ink-3">Waiting for the pipeline…</p>
          )}
        </Plate>
      </div>

      {/* ------------------------------------------------------------- gates */}
      <div className="grid items-start gap-4 lg:grid-cols-5">
        <Plate
          className="lg:col-span-2"
          title={`Why ${nf(stats.companies ?? 0)} companies becomes ${nf(stats.bets ?? 0)}`}
          sub="Four tests, each run against every company. A company has to pass all four before it is worth writing to."
        >
          <Gates
            start={stats.companies ?? 0}
            passed={stats.bets ?? 0}
            gates={[
              { label: "The hiring is real", value: hiringProven, explain: "Their own careers page lists the role — not just a job board." },
              { label: "There is a role for you", value: hasFresher, explain: "At least one opening open to someone early in their career." },
              { label: "You could actually take it", value: anywhere, explain: "Hires from anywhere, or is hiring in India." },
              { label: "Someone could refer you", value: hasLead, explain: "A person inside, with an address we could check." },
            ]}
          />
        </Plate>

        <Plate
          className="lg:col-span-3"
          title="Is the hiring real?"
          sub="A job board will carry a role for months after it is filled. This is the same list checked against the page the company controls."
        >
          <Bars rows={byHiring} total={companies.length} emphasise={HIRING_LABEL.verified} />
          <TableView caption="Companies by hiring verification status" rows={byHiring} />
        </Plate>
      </div>

      {/* ------------------------------------------------------------ detail */}
      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Plate title="Where the roles came from" sub="Public job boards only. Nothing here is bought.">
          <Bars rows={bySource} total={o ? sum(Object.values(o.by_source)) : undefined} />
          <TableView caption="Roles collected per source" rows={bySource} />
        </Plate>

        <Plate title="What they can pay" sub="Graded from what the company has published. Most say nothing, which is not held against them.">
          <Bars rows={byTier} total={companies.length} />
          <TableView caption="Companies by pay tier" rows={byTier} />
        </Plate>

        <Plate title="Where they are" sub="The gap between what a role pays there and costs here is the whole reason to look abroad.">
          <Bars rows={byRegion} total={companies.filter((c) => c.region).length} />
          <TableView caption="Companies by region" rows={byRegion} />
        </Plate>
      </div>

      {/* -------------------------------------------------------- ready list */}
      <Plate
        title="The companies you could write to today"
        sub="Every one of these passed all four tests. The ones marked ready are complete; the rest are the strongest of the near misses."
      >
        {top.length === 0 ? (
          <p className="py-3 text-[12px] text-ink-3">Nothing qualifies yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {top.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/companies/${c.id}`}
                  className="flex items-center gap-3 py-2.5 transition-colors hover:bg-surface-2"
                >
                  <span className="w-36 shrink-0 truncate text-[12.5px] font-medium">{c.name}</span>
                  <span className="hidden flex-1 truncate text-[12px] text-ink-3 sm:block">
                    {c.description ?? "—"}
                  </span>
                  {c.bet ? (
                    <span className="chip chip-solid shrink-0">ready</span>
                  ) : (
                    <span className="chip shrink-0">{c.missing[0] ?? "near"}</span>
                  )}
                  <span className="marginal shrink-0">{c.region ?? "—"}</span>
                  <span className="tnum w-32 shrink-0 text-right text-[11.5px] text-ink-3">
                    {c.fresher} of {c.roles} roles · {c.leads} lead{c.leads === 1 ? "" : "s"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Plate>

      {/* ---------------------------------------------------------- glossary */}
      <Plate title="What these words mean" sub="The four terms this dashboard and the atlas use, in the pipeline's own definitions.">
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {Object.entries(definitions).map(([term, meaning]) => (
            <div key={term}>
              <dt className="text-[12px] font-medium text-ink">{term}</dt>
              <dd className="mt-0.5 text-[11.5px] leading-relaxed text-ink-3">{meaning}</dd>
            </div>
          ))}
        </dl>
      </Plate>
    </div>
  );
}

/* ------------------------------------------------------------------ bits */

function Plate({
  title, sub, children, className = "",
}: { title: string; sub?: string; children: React.ReactNode; className?: string }) {
  return (
    <section data-plate className={`plate px-4 py-4 ${className}`}>
      <h2 className="text-[13px] font-semibold tracking-[-0.01em]">{title}</h2>
      {sub && <p className="mt-1 max-w-[70ch] text-[11.5px] leading-relaxed text-ink-3">{sub}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function rank(counts: Record<string, number>, labels?: Record<string, string>): Row[] {
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

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const nf = (n: number) => n.toLocaleString("en-US");
const hour = (h: number) => `${((h + 11) % 12) + 1}${h < 12 ? "am" : "pm"}`;
