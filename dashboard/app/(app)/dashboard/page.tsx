"use client";

/*  The dashboard.
 *
 *  2026-09-20, redrawn after the NextAdmin reference Tejmul supplied. The page has
 *  to answer three questions for someone who has never seen the pipeline:
 *
 *    what do we have          — companies, roles, people, and how fast they arrive;
 *    how much has been done   — hiring checked, addresses verified, companies ready;
 *    how much has gone out    — drafted, approved, sent, replied, said yes.
 *
 *  Laid out as an F: four figures across the top, the trend under them, then the
 *  breakdowns, the outreach strip, and the newest roles as a table. Exactly one
 *  hero number — companies ready to ask — because 3,265 companies is the input and
 *  74 is the product. Colour is spent on two series and the status pills; the rest
 *  of the page stays ink on white (or on black: every mark reads a theme variable,
 *  so the toggle changes the charts too).
 */

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight, Briefcase, Building2, Download, Flag, Filter, MoreHorizontal, Search, Users } from "lucide-react";
import { useApi } from "@/lib/api";
import { rise } from "@/components/anim";
import { Delta, Gauge, Ribbon, Stack, Trend, type Row } from "@/components/charts";

type Net = {
  stats: Record<string, number>;
  companies: {
    id: number; name: string; tier: string | null; region: string | null;
    hiring_status: string | null; bet: boolean; near: boolean;
    roles: number; fresher: number; anywhere: number; leads: number; missing: string[];
  }[];
};

type Overview = {
  jobs: { jobs: number; embedded: number; scored: number; high_match: number };
  funnel: Record<string, number>;
  quota: {
    daily_cap: number; sent_today: number; remaining_today: number; send_window: [number, number];
    in_window: boolean; send_mode: string; sender_name?: string | null; sender_address?: string | null;
  };
  by_source: Record<string, number>;
  new_this_week: number;
  companies: number;
  contacts: { total: number; verified: number; by_confidence?: Record<string, number> };
  /* The fields below arrived with the redesign; an older API omits them and the
     panels that need them wait rather than break. */
  weekly?: { weeks: string[]; companies: number[]; jobs: number[]; contacts: number[] };
  deltas?: Record<"companies" | "jobs" | "contacts", { this_week: number; last_week: number }>;
  hiring?: Record<string, number>;
  regions?: Record<string, number>;
  latest_roles?: {
    id: number; company_id: number; company_name: string; title: string; location: string | null;
    source: string; remote_anywhere: boolean; hiring_status: string | null; region: string | null; scraped_at: string | null;
  }[];
  leads?: { with_email: number; companies_with_lead: number; by_role: Record<string, number>; by_source: Record<string, number> };
  as_of?: string;
};

const SOURCE_LABEL: Record<string, string> = {
  ashby: "Ashby", greenhouse: "Greenhouse", lever: "Lever", wellfound: "Wellfound",
  hn: "Hacker News", wwr: "We Work Remotely", remoteok: "RemoteOK", yc: "Y Combinator",
};
const REGION_LABEL: Record<string, string> = {
  us: "United States", remote: "Remote-first", india: "India", eu: "EU (other)", uk: "United Kingdom",
  de: "Germany", nl: "Netherlands", unknown: "Not stated",
};
const HIRING: { key: string; label: string; color: string }[] = [
  { key: "verified", label: "Proven on their own site", color: "var(--series-1)" },
  { key: "not_authorized", label: "Board says so, their site does not", color: "var(--line-strong)" },
  { key: "unchecked", label: "Not checked yet", color: "var(--surface-3)" },
  { key: "role_missing", label: "Hiring, but not this kind of role", color: "var(--ink-3)" },
  { key: "unreachable", label: "Their site could not be read", color: "var(--ink-2)" },
];
const ROLE_LABEL: Record<string, string> = {
  engineer: "Engineers", senior_engineer: "Senior engineers", founder: "Founders", eng_manager: "Engineering managers",
  recruiter: "Recruiters", other: "Other roles", unclassified: "Not classified yet",
};
const LEAD_SOURCE_LABEL: Record<string, string> = {
  github: "GitHub commit history", site: "Their own site", "linkedin-public": "Public LinkedIn pages",
  web: "Open web search", pattern: "Address pattern", hunter: "Hunter.io",
};
const TIER_CHIP: Record<string, string> = { tier1: "₹30 L+", tier2: "₹24–30 L", prospect: "pay unknown" };

export default function Dashboard() {
  const root = useRef<HTMLDivElement>(null);
  const net = useApi<Net>("/api/network");
  const ov = useApi<Overview>("/api/overview");

  useGSAP(() => { if (ov.data) rise("[data-card]", { stagger: 0.04 }); },
    { scope: root, dependencies: [!!ov.data] });

  if (ov.error) return <p className="py-8 text-[13px] text-ink-3">{ov.error}</p>;
  if (!ov.data) return <p className="py-8 text-[13px] text-ink-3">Reading the pipeline…</p>;

  const o = ov.data;
  const stats = net.data?.stats ?? {};
  const ready = (net.data?.companies ?? []).filter((c) => c.bet)
    .sort((a, b) => (b.anywhere > 0 ? 1 : 0) - (a.anywhere > 0 ? 1 : 0) || b.leads - a.leads || b.fresher - a.fresher);
  const readyCount = stats.bets ?? ready.length;
  const verifiedRatio = o.contacts.total ? o.contacts.verified / o.contacts.total : 0;
  const conf = o.contacts.by_confidence ?? {};
  const weekly = o.weekly;
  const weekLabels = weekly ? weekly.weeks.map(weekLabel) : [];
  const d = o.deltas;
  const f = o.funnel;
  const q = o.quota;
  const senderLine = q.sender_name && q.sender_address ? `${q.sender_name} <${q.sender_address}>` : q.sender_address ?? null;
  const asOf = o.as_of ? new Date(o.as_of + "Z") : null;

  const bySource: Row[] = rank(o.by_source, SOURCE_LABEL);
  const byRegion: Row[] = o.regions ? rank(o.regions, REGION_LABEL).filter((r) => r.label !== "Not stated") : [];
  const leads = o.leads;
  const leadsByRole: Row[] = leads ? rank(leads.by_role, ROLE_LABEL) : [];
  const leadsBySource: Row[] = leads ? rank(leads.by_source, LEAD_SOURCE_LABEL) : [];
  const hiring = o.hiring
    ? HIRING.map((h) => ({ label: h.label, value: o.hiring?.[h.key] ?? 0, color: h.color })).filter((s) => s.value > 0)
    : [];

  return (
    <div ref={root} className="flex flex-col gap-4">
      {/* ---------------------------------------------------------- head */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">
            What the pipeline found
          </h1>
          <p className="mt-1 max-w-[64ch] text-[13px] leading-relaxed text-ink-2">
            One machine, left to right: collect roles from public boards, keep the ones that fit you,
            check the company is really hiring, find a person inside who could refer you, then stop
            and wait for your approval.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Link
            href="/companies"
            className="btn-ink group"
          >
            Browse all companies
            <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
          {asOf ? <span className="marginal normal-case tracking-[0.06em]">Data as of {fmtAsOf(asOf)}</span> : null}
        </div>
      </header>

      {/* ---------------------------------------------------------- KPIs */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Headline numbers">
        <Kpi
          hero
          icon={<Flag size={20} />}
          tint="highlight"
          value={readyCount}
          label="Ready to ask today"
          trail={<span className="text-[12.5px] text-ink-3">{nf(stats.near ?? 0)} one check away</span>}
        />
        <Kpi
          icon={<Building2 size={20} />}
          tint="stone"
          value={o.companies}
          label="Companies mapped"
          trail={d ? <Delta now={d.companies.this_week} before={d.companies.last_week} /> : null}
        />
        <Kpi
          icon={<Briefcase size={20} />}
          tint="stone"
          value={o.jobs.jobs}
          label="Roles collected"
          trail={d ? <Delta now={d.jobs.this_week} before={d.jobs.last_week} /> : null}
        />
        <Kpi
          icon={<Users size={20} />}
          tint="stone"
          value={o.contacts.total}
          label="People who could refer you"
          trail={d ? <Delta now={d.contacts.this_week} before={d.contacts.last_week} /> : null}
        />
      </section>

      {/* --------------------------------------------------------- trend */}
      <Card>
        <div className="mb-3 flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-wrap gap-7">
            <SeriesHead color="var(--series-1)" name="Companies added" now={weekly ? last(weekly.companies) : o.companies} delta={d?.companies} />
            <SeriesHead color="var(--series-2)" dashed name="Roles collected" now={weekly ? last(weekly.jobs) : o.new_this_week} delta={d?.jobs} />
          </div>
          <span className="inline-flex h-9 items-center rounded-lg bg-surface-2 px-3 text-[12.5px] font-medium text-ink-2">Weekly · last 8 weeks</span>
        </div>
        {weekly ? (
          <Trend
            labels={weekLabels}
            series={[
              { name: "Companies added", data: weekly.companies, color: "var(--series-1)" },
              { name: "Roles collected", data: weekly.jobs, color: "var(--series-2)", dashed: true },
            ]}
            ariaLabel="Companies added and roles collected per week, last eight weeks"
          />
        ) : (
          <p className="py-6 text-[12.5px] text-ink-3">The weekly series arrives after the API restarts.</p>
        )}
        <p className="mt-2 text-[12.5px] text-ink-3">
          Weeks with nothing are weeks the machine was off. Hover or use the arrow keys to read a week.
        </p>
      </Card>

      {/* ------------------------------------------------- gauge + ready */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-5">
          <CardHead title="Addresses you can trust" sub="A bounce counts against your Gmail, so only verified addresses are sent to by default." />
          <Gauge ratio={verifiedRatio} label="Verified" ariaLabel={`${Math.round(verifiedRatio * 100)}% of people found have a verified address`} />
          <div className="mt-4 grid grid-cols-3 rounded-lg bg-surface-2 px-1.5 py-3 text-center">
            <Triple label="All people" value={o.contacts.total} />
            <Triple label="Scraped, unchecked" value={conf.scraped ?? o.contacts.total - o.contacts.verified - (conf["pattern-guessed"] ?? 0)} divider />
            <Triple label="Pattern-guessed" value={conf["pattern-guessed"] ?? 0} divider />
          </div>
        </Card>

        <Card className="lg:col-span-7">
          <CardHead title="Ready to ask" sub="Hiring proven on their own site, a fresher role, hires from anywhere or India, and a person to write to." />
          {net.error ? (
            <p className="py-3 text-[12.5px] text-ink-3">{net.error}</p>
          ) : !net.data ? (
            <p className="py-3 text-[12.5px] text-ink-3">Reading the atlas…</p>
          ) : ready.length === 0 ? (
            <p className="py-3 text-[12.5px] text-ink-3">Nothing qualifies yet.</p>
          ) : (
            <ul className="flex flex-col">
              {ready.slice(0, 5).map((c, i) => (
                <li key={c.id} className={`grid grid-cols-[40px_1fr_auto] items-center gap-3.5 py-3 ${i ? "border-t border-line" : "pt-1"}`}>
                  <span className={`grid h-10 w-10 place-items-center rounded-full text-[13px] font-semibold ${i === 0 ? "bg-highlight text-highlight-ink" : "bg-surface-2 text-ink-2"}`}>
                    {c.name.slice(0, 2)}
                  </span>
                  <div className="min-w-0">
                    <Link href={`/companies/${c.id}`} className="text-[14px] font-semibold hover:underline">{c.name}</Link>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-3">
                      <span>{c.fresher} fresher role{c.fresher === 1 ? "" : "s"} · {c.leads} lead{c.leads === 1 ? "" : "s"}</span>
                      {c.anywhere > 0 ? <Chip accent>{c.anywhere > 1 ? `${c.anywhere} anywhere` : "anywhere"}</Chip> : null}
                      {c.region ? <Chip>{c.region.toUpperCase()}</Chip> : null}
                      {c.tier && TIER_CHIP[c.tier] ? <Chip>{TIER_CHIP[c.tier]}</Chip> : null}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="tnum text-[14px] font-semibold">{c.leads}</div>
                    <div className="text-[12px] text-ink-3">leads</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {ready.length > 5 ? (
            <p className="mt-3 text-[12.5px] text-ink-3">
              {nf(ready.length - 5)} more ·{" "}
              <Link href="/companies" className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">Open all {nf(readyCount)} in Companies →</Link>
            </p>
          ) : null}
        </Card>
      </section>

      {/* -------------------------------------------------- breakdowns */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <CardHead title="Where the roles came from" sub="Public job boards only. Nothing here is bought." />
          <Ribbon rows={bySource} head={["Source", "Roles"]} />
        </Card>
        <Card>
          <CardHead title="Where the companies are" sub="The gap between what a role pays there and costs here is the reason to look abroad." />
          {byRegion.length ? <Ribbon rows={byRegion} head={["Region", "Companies"]} /> : <Waiting />}
        </Card>
        <Card>
          <CardHead title="Is the hiring real?" sub="A board carries a role for months after it is filled. Each company is checked against the page it controls." />
          {hiring.length ? (
            <Stack segments={hiring} ariaLabel={`Of ${nf(o.companies)} companies, ${nf(o.hiring?.verified ?? 0)} have hiring proven on their own site`} />
          ) : <Waiting />}
        </Card>
      </section>

      {/* ------------------------------------------------------- leads */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-12" aria-label="Leads">
        <Card className="lg:col-span-4">
          <CardHead title="Who could refer you" sub="Leads are people, not companies: someone inside who can put your name forward." />
          {leads ? (
            <>
              <div className="tnum font-[family-name:var(--font-display)] text-[44px] font-semibold leading-none tracking-[-0.03em]">
                {nf(o.contacts.total)}
              </div>
              <div className="mt-2.5 font-medium text-ink-2">people found so far</div>
              <dl className="mt-4 grid grid-cols-2 gap-3 rounded-lg bg-surface-2 px-3.5 py-3">
                <div>
                  <dt className="text-[12px] text-ink-2">With an address</dt>
                  <dd className="tnum mt-0.5 text-[18px] font-semibold">{nf(leads.with_email)}</dd>
                </div>
                <div className="border-l border-line-strong pl-3">
                  <dt className="text-[12px] text-ink-2">Companies with someone to ask</dt>
                  <dd className="tnum mt-0.5 text-[18px] font-semibold">{nf(leads.companies_with_lead)}</dd>
                </div>
              </dl>
              <Link href="/leads" className="mt-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
                Open all leads <ArrowRight size={13} />
              </Link>
            </>
          ) : <Waiting />}
        </Card>
        <Card className="lg:col-span-4">
          <CardHead title="Who they are" sub="Engineers refer engineers; founders and managers decide. Recruiters are kept but ranked last." />
          {leadsByRole.length ? <Ribbon rows={leadsByRole} head={["Role", "People"]} /> : <Waiting />}
        </Card>
        <Card className="lg:col-span-4">
          <CardHead title="How we found them" sub="Commit history in the company's own repositories is evidence of employment; a bio is not." />
          {leadsBySource.length ? <Ribbon rows={leadsBySource} head={["Found via", "People"]} /> : <Waiting />}
        </Card>
      </section>

      {/* ---------------------------------------------------- outreach */}
      <Card>
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">From a draft to a yes</h2>
            <p className="mt-1 max-w-[60ch] text-[12.5px] text-ink-3">
              Nothing moves right without you clicking approve. Replies are read back from the same Gmail thread and classified.
            </p>
            {senderLine ? <p className="mt-1 text-[12.5px] text-ink-2">Sends as <span className="font-medium text-ink">{senderLine}</span></p> : null}
          </div>
          {q.send_mode === "gmail" ? <span className="pill pill-good">Sending live</span> : <span className="pill pill-warn">Dry run</span>}
        </div>
        <div className="grid grid-cols-2 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
          <Stage n={readyCount} label="Ready" explain="The pool every draft is drawn from" first accent />
          <Stage n={f.drafted ?? 0} label="Drafted" explain="Written with its evidence attached" />
          <Stage n={f.approved ?? 0} label="Approved" explain="The gate: you read it first" />
          <Stage n={f.sent ?? 0} label="Sent" explain="From your own Gmail, in the window" />
          <Stage n={f.replied ?? 0} label="Replied" explain="Positive · declined · closed · neutral" />
          <Stage n={f.positive ?? 0} label="Said yes" explain="Agreed to refer, or to talk" />
        </div>
        <div className="mt-4 grid grid-cols-1 items-center gap-4 border-t border-line pt-4 sm:grid-cols-[1fr_auto]">
          <div>
            <div className="mb-1.5 flex justify-between text-[12.5px] text-ink-2">
              <span><b className="font-semibold text-ink">{nf(q.sent_today)}</b> of {q.daily_cap} sent today</span>
              <span>Window {hour(q.send_window[0])} – {hour(q.send_window[1])}{q.in_window ? " · open now" : ""}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3">
              <div className="h-full rounded-full bg-ink" style={{ width: `${q.daily_cap ? Math.min(100, (q.sent_today / q.daily_cap) * 100) : 0}%` }} />
            </div>
          </div>
          <Link href="/queue" className="btn-ink">
            Review the queue
          </Link>
        </div>
      </Card>

      {/* ------------------------------------------------------- table */}
      <Card>
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Latest fresher roles at companies proven to be hiring</h2>
            <p className="mt-1 text-[12.5px] text-ink-3">Arrived in the last scrape. Senior roles are kept but not shown here.</p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/companies" className="inline-flex h-[34px] items-center gap-2 rounded-lg bg-surface-2 px-3 text-[12.5px] text-ink-2 transition-colors hover:text-ink">
              <Search size={14} /> Search companies
            </Link>
            <span className="grid h-[34px] w-[34px] place-items-center rounded-lg bg-surface-2 text-ink-3" title="Filters live on the Companies page"><Filter size={14} /></span>
            <a href="/api/jobs?sort=recent&limit=500" className="grid h-[34px] w-[34px] place-items-center rounded-lg bg-surface-2 text-ink-2 hover:text-ink" aria-label="Download the newest roles as JSON"><Download size={14} /></a>
          </div>
        </div>
        {o.latest_roles ? (
          <div className="-mx-5 overflow-x-auto px-5">
            <table className="w-full min-w-[820px] border-collapse">
              <thead>
                <tr className="text-left text-[12.5px] font-medium text-ink-2">
                  <Th>Company</Th><Th>Role</Th><Th>Location</Th><Th>Source</Th><Th>Hiring</Th><Th>Can you take it?</Th><Th>Arrived</Th><Th />
                </tr>
              </thead>
              <tbody>
                {o.latest_roles.map((r) => (
                  <tr key={r.id} className="border-t border-line transition-colors hover:bg-surface-2">
                    <td className="px-2.5 py-3 font-semibold"><Link href={`/companies/${r.company_id}`} className="hover:underline">{r.company_name}</Link></td>
                    <td className="max-w-[300px] px-2.5 py-3">{r.title}</td>
                    <td className="px-2.5 py-3 text-ink-2">{r.location ?? "—"}</td>
                    <td className="px-2.5 py-3"><span className="font-mono text-[11px] text-ink-2">{r.source}</span></td>
                    <td className="px-2.5 py-3"><span className={`pill ${r.hiring_status === "verified" ? "pill-good" : "pill-muted"}`}>{r.hiring_status === "verified" ? "Proven" : "Unverified"}</span></td>
                    <td className="px-2.5 py-3"><span className={`pill ${r.remote_anywhere ? "pill-note" : "pill-muted"}`}>{r.remote_anywhere ? "From anywhere" : "On site or unstated"}</span></td>
                    <td className="px-2.5 py-3 text-ink-2">{relDay(r.scraped_at)}</td>
                    <td className="px-2.5 py-3 text-right">
                      <Link href={`/companies/${r.company_id}`} className="inline-grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-3 hover:text-ink" aria-label={`Open ${r.company_name}`}><MoreHorizontal size={16} /></Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Waiting />}
      </Card>

      <footer className="flex flex-wrap gap-x-5 gap-y-1 pt-1 text-[12px] text-ink-3">
        <span>Roles: Ashby · Greenhouse · Lever · Wellfound · Hacker News · We Work Remotely · RemoteOK · Y Combinator</span>
        <span>People: GitHub · Hunter.io · company sites · X</span>
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------------ bits */

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section data-card className={`card min-w-0 px-5 py-[18px] ${className}`}>{children}</section>;
}

function CardHead({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-3.5">
      <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
      {sub ? <p className="mt-1 max-w-[60ch] text-[12.5px] text-ink-3">{sub}</p> : null}
    </div>
  );
}

const TINT: Record<string, string> = {
  highlight: "bg-highlight text-highlight-ink",
  stone: "bg-surface-2 text-ink-2",
};

function Kpi({ icon, tint, value, label, trail, hero = false }: {
  icon: React.ReactNode; tint: keyof typeof TINT; value: number; label: string; trail?: React.ReactNode; hero?: boolean;
}) {
  return (
    <div data-card className="card flex flex-col gap-3.5 px-5 py-[18px]">
      <span className={`grid h-[42px] w-[42px] place-items-center rounded-[11px] ${TINT[tint]}`}>{icon}</span>
      <div>
        <div className={`tnum font-[family-name:var(--font-display)] font-semibold leading-none tracking-[-0.03em] ${hero ? "text-[44px]" : "text-[30px]"}`}>
          {nf(value)}
        </div>
        <div className="mt-2.5 flex flex-wrap items-baseline justify-between gap-x-2.5 gap-y-1">
          <span className="font-medium text-ink-2">{label}</span>
          {trail}
        </div>
      </div>
    </div>
  );
}

function SeriesHead({ color, name, now, delta, dashed = false }: { color: string; name: string; now: number; delta?: { this_week: number; last_week: number }; dashed?: boolean }) {
  return (
    <div>
      <div className="flex items-center gap-2 font-medium text-ink-2">
        <span className="h-2.5 w-2.5 rounded-[3px]" style={dashed ? { border: `2px dashed ${color}` } : { background: color }} />
        {name}
      </div>
      <div className="mt-1 flex items-baseline gap-2.5">
        <span className="tnum font-[family-name:var(--font-display)] text-[26px] font-semibold tracking-[-0.03em]">{nf(now)}</span>
        {delta ? <Delta now={delta.this_week} before={delta.last_week} unit="this week" /> : null}
      </div>
    </div>
  );
}

function Triple({ label, value, divider = false }: { label: string; value: number; divider?: boolean }) {
  return (
    <div className={divider ? "border-l border-line-strong" : ""}>
      <div className="text-[12px] text-ink-2">{label}</div>
      <div className="tnum mt-0.5 text-[18px] font-semibold">{nf(value)}</div>
    </div>
  );
}

function Chip({ children, accent = false }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span className={`inline-flex h-[18px] items-center rounded-[5px] px-1.5 font-mono text-[10.5px] tracking-[0.03em] whitespace-nowrap ${accent ? "bg-series-1-wash text-ink" : "bg-surface-2 text-ink-2"}`}>
      {children}
    </span>
  );
}

function Stage({ n, label, explain, first = false, accent = false }: { n: number; label: string; explain: string; first?: boolean; accent?: boolean }) {
  return (
    <div className={`py-2 ${first ? "" : "border-l border-line pl-3.5"}`}>
      <div className={`tnum font-[family-name:var(--font-display)] text-[26px] font-semibold leading-none tracking-[-0.03em] ${accent ? "text-series-1" : ""}`}>{nf(n)}</div>
      <div className="mt-1.5 font-medium">{label}</div>
      <div className="mt-0.5 text-[12px] text-ink-3">{explain}</div>
    </div>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th className="px-2.5 pb-2 whitespace-nowrap">{children}</th>;
}

function Waiting() {
  return <p className="py-3 text-[12.5px] text-ink-3">Arrives after the API restarts.</p>;
}

function rank(counts: Record<string, number>, labels: Record<string, string>): Row[] {
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([k, v]) => ({ label: labels[k] ?? k, value: v }));
}

const nf = (n: number) => n.toLocaleString("en-US");
const last = (xs: number[]) => xs[xs.length - 1] ?? 0;
const hour = (h: number) => `${((h + 11) % 12) + 1} ${h < 12 ? "am" : "pm"}`;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function weekLabel(iso: string) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

function fmtAsOf(d: Date) {
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function relDay(iso: string | null) {
  if (!iso) return "—";
  const t = new Date(iso + (iso.endsWith("Z") ? "" : "Z"));
  const days = Math.floor((Date.now() - t.getTime()) / 86_400_000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : `${days} days ago`;
}
