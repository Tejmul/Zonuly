"use client";

/*  Scraping health — the hourly audit, on one page.
 *
 *  Tejmul, 2026-10-07: "scraping isn't the problem, the format is — and each hour it
 *  should verify what was scraped, like a guardrail". jobhunter/audit.py runs every hour
 *  under its own LaunchAgent and stores a report; this page reads GET /api/audit and shows
 *  it top-down: an alert first (a stall or a channel failing most of its calls), then how
 *  full every field is and how much that moved in the last hour and day, what the last
 *  hour produced, what the guardrails refused and why, and whether each research channel
 *  is answering. Black and white; the only emphasis is ink on the alert.
 */

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useApi, type AuditPayload, type AuditReport } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { rise } from "@/components/anim";
import { PageHead } from "@/components/shell";
import { Stat } from "@/components/charts";
import { ErrorNote, Loading } from "@/components/ui/primitives";

// the fields, in the order a company is filled, with the words a person would use
const FIELDS: { key: string; label: string; hint: string }[] = [
  { key: "description", label: "Description", hint: "≥ 60 chars, about this company" },
  { key: "funding_stage", label: "Funding stage", hint: "in the one vocabulary" },
  { key: "funding_amount", label: "Amount raised", hint: "a raise, not revenue" },
  { key: "funding_date", label: "Round date", hint: "not in the future" },
  { key: "investors", label: "Investors", hint: "names, not sentences" },
  { key: "valuation", label: "Valuation", hint: "above the raise" },
  { key: "hiring_known", label: "Hiring checked", hint: "their own page read" },
  { key: "hiring_verified", label: "Hiring verified", hint: "a role title on their page" },
  { key: "careers_url", label: "Careers page", hint: "own site or ATS" },
  { key: "has_people", label: "Anyone found", hint: "≥ 1 person" },
  { key: "people_with_email", label: "Anyone with an address", hint: "≥ 1 address" },
  { key: "referrers_3", label: "3 confirmed referrers", hint: "still there, can refer" },
  { key: "referrer_with_email", label: "Confirmed referrer + address", hint: "ready to ask" },
  { key: "complete", label: "Complete", hint: "every part above" },
];

const AFFILIATION: { key: string; label: string }[] = [
  { key: "verified-current", label: "Verified current" },
  { key: "likely-current", label: "Likely current" },
  { key: "unconfirmed", label: "Unconfirmed" },
  { key: "generic", label: "Shared inbox" },
];

const pct = (v: number | undefined | null) => `${((v ?? 0) * 100).toFixed(1)}%`;
const pts = (v: number | undefined | null) =>
  v === undefined || v === null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v * 100).toFixed(1)}`;

export default function HealthPage() {
  const root = useRef<HTMLDivElement>(null);
  const { data, error, status, reload } = useApi<AuditPayload>("/api/audit");
  const r = data?.last ?? null;

  useGSAP(() => { if (r) rise("[data-anim]", { stagger: 0.04 }); }, { scope: root, dependencies: [!!r] });

  const head = (
    <PageHead
      title="Scraping health"
      description="Every hour the audit checks what the scrapers wrote: how full each field is, what the guardrails refused, and whether every channel is answering."
    />
  );

  if (status === 404) {
    return (
      <>
        {head}
        <div className="plate px-6 py-14 text-center">
          <p className="text-[14px] font-medium">This server doesn&rsquo;t report the audit yet</p>
          <p className="mx-auto mt-2 max-w-[54ch] text-[12.5px] leading-relaxed text-ink-3">
            It will appear here once the API is restarted on the new code.
          </p>
        </div>
      </>
    );
  }
  if (error && !data) return <>{head}<div className="plate"><ErrorNote message={error} onRetry={reload} /></div></>;
  if (!data) return <>{head}<div className="plate"><Loading label="Reading the last audit" rows={6} /></div></>;
  if (!r) {
    return (
      <>
        {head}
        <div className="plate px-6 py-14 text-center">
          <p className="text-[14px] font-medium">No audit has run yet</p>
          <p className="mx-auto mt-2 max-w-[60ch] text-[12.5px] leading-relaxed text-ink-3">
            It runs every hour once its agent is installed. To run one now:{" "}
            <code className="tnum rounded bg-surface-2 px-1.5 py-0.5 text-[12px]">python scripts/run.py audit run</code>
          </p>
        </div>
      </>
    );
  }

  return (
    <div ref={root}>
      {head}
      <Banner r={r} every={data.agent?.every_minutes ?? 60} hours={data.history.length} />
      <Throughput r={r} />
      <Referrals r={r} />
      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <FillRates r={r} />
        <div className="flex min-w-0 flex-col gap-4">
          <People r={r} />
          <Rejects r={r} />
        </div>
      </div>
      <Channels r={r} />
      <Sample r={r} />
    </div>
  );
}

/* ------------------------------------------------------------------ alert first */

function Banner({ r, every, hours }: { r: AuditReport; every: number; hours: number }) {
  const alert = r.alerts?.length > 0;
  return (
    <section
      data-anim
      role={alert ? "alert" : undefined}
      className={`plate mb-4 flex flex-wrap items-start gap-3 px-4 py-3.5 ${alert ? "border-ink" : ""}`}
    >
      {alert ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-ink" /> : <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-ink-2" />}
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium">
          {alert ? "Scraping needs a look" : "Scraping is filling fields and every alerted channel is answering"}
        </p>
        {alert ? (
          <ul className="mt-1 space-y-0.5 text-[12.5px] leading-relaxed text-ink-2">
            {r.alerts.map((a) => <li key={a}>{a}</li>)}
          </ul>
        ) : null}
        <p className="mt-1 text-[11.5px] text-ink-3">
          Audited {relTime(r.at)} · runs every {every} min · {hours} hour{hours === 1 ? "" : "s"} of history ·{" "}
          tick {r.tick?.ran_last_hour ? `ran ${relTime(r.tick?.last?.finished ?? r.tick?.last?.at)}` : "did not run in the last hour"}
        </p>
      </div>
      <div className="text-right">
        <p className="marginal">Average completeness</p>
        <p className="tnum text-[26px] leading-none font-medium tracking-[-0.02em]">{r.fill.avg_completeness.toFixed(1)}%</p>
        <p className="mt-1 text-[11px] text-ink-3">{r.fill.companies.toLocaleString()} active companies</p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ last hour */

function Throughput({ r }: { r: AuditReport }) {
  const h = r.last_hour;
  return (
    <section data-anim className="plate">
      <div className="border-b border-line px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">The last hour</h2>
      </div>
      <div className="grid grid-cols-2 divide-line sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
        <Stat label="Fields written" value={h.fields_written} hero hint="passed the guardrails" />
        <Stat label="Companies added" value={h.companies_added} />
        <Stat label="People added" value={h.contacts_added} />
        <Stat label="Roles added" value={h.roles_added} />
        <Stat label="People re-checked" value={h.contacts_relabelled ?? 0} hint="affiliation" />
        <Stat label="Refused" value={r.rejects.total} hint={r.rejects_24h !== undefined ? `${r.rejects_24h.toLocaleString()} in 24h` : undefined} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ referrals (the main thing) */

function Referrals({ r }: { r: AuditReport }) {
  const x = r.referrals;
  if (!x) return null;
  if (x.error) {
    return (
      <section data-anim className="plate mt-4 px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">Referrals</h2>
        <p className="mt-1 text-[12px] text-ink-3">This hour&rsquo;s referral block could not be read: {x.error}</p>
      </section>
    );
  }
  const bySource = Object.entries(x.addresses_found?.by_source ?? {});
  const rv = x.reverify;
  const rc = x.revalidate;
  return (
    <section data-anim className="plate mt-4">
      <div className="flex flex-wrap items-baseline gap-x-3 border-b border-line px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">Referrals in the last hour</h2>
        <p className="text-[12px] text-ink-3">
          {(x.companies_with_email ?? 0).toLocaleString()} companies have a referrer confirmed there now with an address.
          {x.people_runs?.linkedin_down ? ` LinkedIn search paused: ${x.people_runs.linkedin_down}.` : ""}
        </p>
      </div>
      <div className="grid grid-cols-2 divide-line sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
        <Stat label="Ready referrers added" value={x.ready_added ?? 0} hero hint={`${x.ready_added_with_email ?? 0} with an address`} />
        <Stat label="Addresses by pattern" value={x.addresses_found?.by_pattern ?? 0} hint="confirmed format, MX-checked" />
        <Stat label="Gained an address" value={x.companies_gained_email ?? 0}
          hint={x.companies_gained_email == null ? "from the next audit" : "companies, 0 → ≥1"} />
        <Stat label="Became complete" value={x.companies_became_complete ?? 0} hint="now shown" />
        <Stat label="Re-confirmed" value={rv?.confirmed ?? 0} hint={`${rv?.downgraded ?? 0} downgraded · ${rv?.inconclusive ?? 0} unclear`} />
        <Stat label="Companies re-checked" value={rc?.checked ?? 0} hint={`${rc?.newly_failed ?? 0} failed · ${rc?.recovered ?? 0} recovered`} />
      </div>
      {bySource.length > 0 && (
        <p className="border-t border-line px-4 py-2.5 text-[12px] text-ink-3">
          Addresses on new ready referrers, by source: {bySource.map(([k, n]) => `${k} ${n}`).join(" · ")}
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ fill rates */

function FillRates({ r }: { r: AuditReport }) {
  const dh = r.delta_hour ?? null;
  const dd = r.delta_day ?? null;
  return (
    <section data-anim className="plate min-w-0">
      <div className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">How full each field is</h2>
        <span className="text-[11.5px] text-ink-3">points vs 1h · 24h ago</span>
      </div>
      <ul className="divide-y divide-line">
        {FIELDS.map((f) => {
          const v = r.fill.rates[f.key] ?? 0;
          const n = r.fill.counts[f.key] ?? 0;
          return (
            <li key={f.key} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_3.5rem] items-center gap-x-3 px-4 py-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_4rem_7.5rem]">
              <span className="min-w-0">
                <span className="block truncate text-[12.5px] font-medium">{f.label}</span>
                <span className="block truncate text-[11px] text-ink-3">{f.hint}</span>
              </span>
              <span className="bar-track block h-[8px] w-full" aria-hidden>
                <span className="bar-mark block h-full" style={{ width: `${Math.max(v * 100, n > 0 ? 1 : 0)}%`, background: "var(--data-1)" }} />
              </span>
              <span className="tnum text-right text-[12.5px] tabular-nums" title={`${n.toLocaleString()} of ${r.fill.companies.toLocaleString()}`}>
                {pct(v)}
              </span>
              <span className="tnum col-span-3 text-right text-[11.5px] text-ink-3 tabular-nums sm:col-span-1">
                {pts(dh?.[f.key])} · {pts(dd?.[f.key])}
              </span>
            </li>
          );
        })}
      </ul>
      {!dh ? (
        <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">
          The deltas appear once there is an audit from an hour (and a day) ago to compare with.
        </p>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ people: who is still there */

function People({ r }: { r: AuditReport }) {
  const aff = r.labels?.affiliation ?? {};
  const total = Object.values(aff).reduce((a, b) => a + b, 0);
  return (
    <section data-anim className="plate px-4 py-4">
      <h2 className="text-[13.5px] font-semibold">Is each person still there?</h2>
      <p className="mt-0.5 mb-3 text-[12px] text-ink-3">
        Only the first two are offered as referrers — drafting, the review gate and sending all check.
      </p>
      {total ? (
        <dl className="grid grid-cols-[minmax(0,1fr)_auto_3.5rem] items-center gap-x-3 gap-y-1.5 text-[12.5px]">
          {AFFILIATION.map((a) => {
            const n = aff[a.key] ?? 0;
            return (
              <div key={a.key} className="contents">
                <dt className={a.key.endsWith("current") ? "font-medium" : "text-ink-2"}>{a.label}</dt>
                <dd className="tnum text-right tabular-nums">{n.toLocaleString()}</dd>
                <dd className="tnum text-right text-ink-3 tabular-nums">{((n / total) * 100).toFixed(1)}%</dd>
              </div>
            );
          })}
        </dl>
      ) : (
        <p className="text-[12px] text-ink-3">No labelled people yet.</p>
      )}
      <Link href="/leads" className="mt-3 inline-block text-[12px] text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
        See the confirmed ones
      </Link>
    </section>
  );
}

/* ------------------------------------------------------------------ what was refused */

function Rejects({ r }: { r: AuditReport }) {
  const reasons = Object.entries(r.rejects.by_reason ?? {}).slice(0, 10);
  const channels = Object.entries(r.rejects.by_channel ?? {}).slice(0, 8);
  const max = Math.max(1, ...reasons.map(([, n]) => n));
  return (
    <section data-anim className="plate px-4 py-4">
      <h2 className="text-[13.5px] font-semibold">What the guardrails refused</h2>
      <p className="mt-0.5 mb-3 text-[12px] text-ink-3">
        Last hour, by reason. Nothing is dropped silently: every refused value is kept with its source.
      </p>
      {reasons.length ? (
        <ul className="space-y-1.5">
          {reasons.map(([why, n]) => (
            <li key={why} className="grid grid-cols-[minmax(0,1fr)_4rem_3rem] items-center gap-x-3 text-[12px]">
              <span className="min-w-0 truncate text-ink-2" title={why}>{why}</span>
              <span className="bar-track block h-[6px]" aria-hidden>
                <span className="bar-mark block h-full" style={{ width: `${(n / max) * 100}%`, background: "var(--data-2)" }} />
              </span>
              <span className="tnum text-right tabular-nums">{n.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12px] text-ink-3">Nothing refused in the last hour.</p>
      )}
      {channels.length ? (
        <p className="mt-3 text-[11.5px] text-ink-3">
          By channel: {channels.map(([c, n]) => `${c} ${n.toLocaleString()}`).join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ channels */

function Channels({ r }: { r: AuditReport }) {
  const research = Object.entries(r.channels.research ?? {});
  const o = r.channels.openrouter;
  const gh = r.channels.github_budget;
  const tools = Object.entries(r.channels.tools ?? {});
  return (
    <section data-anim className="plate mt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">Is every channel answering?</h2>
        <span className="text-[11.5px] text-ink-3">
          Exa MCP cooldowns this hour: {r.channels.exa_mcp_cooldowns_hour ?? 0}
          {gh && gh.remaining !== undefined && gh.remaining !== null ? ` · GitHub ${gh.remaining.toLocaleString()} / ${gh.limit?.toLocaleString()} left` : ""}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line text-left text-[11px] tracking-wide text-ink-3 uppercase">
              <th className="px-4 py-2 font-medium">Channel</th>
              <th className="px-2 py-2 text-right font-medium">Hour ok / fail</th>
              <th className="px-2 py-2 text-right font-medium">24h fail rate</th>
              <th className="px-2 py-2 font-medium">Last success</th>
              <th className="px-4 py-2 font-medium">Last failure</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {research.map(([name, c]) => {
              const bad = (c.hour.fail_rate ?? 0) > 0.8 && (c.hour.ok ?? 0) + (c.hour.fail ?? 0) >= 8;
              return (
                <tr key={name}>
                  <td className="px-4 py-2 font-medium">{name}{bad ? <span className="chip chip-solid ml-2">failing</span> : null}</td>
                  <td className="tnum px-2 py-2 text-right tabular-nums">{c.hour.ok ?? 0} / {c.hour.fail ?? 0}</td>
                  <td className="tnum px-2 py-2 text-right tabular-nums">{c.day.fail_rate === null || c.day.fail_rate === undefined ? "—" : pct(c.day.fail_rate)}</td>
                  <td className="px-2 py-2 text-ink-2">{relTime(c.last_ok_at)}</td>
                  <td className="max-w-[28rem] px-4 py-2 text-ink-3">
                    <span className="block truncate" title={c.last_error ?? undefined}>
                      {c.last_fail_at ? `${relTime(c.last_fail_at)} — ${c.last_error ?? ""}` : "—"}
                    </span>
                  </td>
                </tr>
              );
            })}
            {o ? (
              <tr>
                <td className="px-4 py-2 font-medium">
                  openrouter{o.daily_ceiling_hit ? <span className="chip chip-solid ml-2">daily ceiling hit</span> : null}
                </td>
                <td className="tnum px-2 py-2 text-right tabular-nums">{o.ok} / {o.fail}</td>
                <td className="tnum px-2 py-2 text-right tabular-nums">{o.fail_rate === null || o.fail_rate === undefined ? "—" : pct(o.fail_rate)}</td>
                <td className="px-2 py-2 text-ink-2">{relTime(o.last_ok_at)}</td>
                <td className="max-w-[28rem] px-4 py-2 text-ink-3">
                  <span className="block truncate" title={o.last_error ?? undefined}>
                    {o.last_fail_at ? `${relTime(o.last_fail_at)} — ${o.last_error ?? ""}` : "—"}
                  </span>
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {!research.length ? (
        <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">No channel has recorded a call yet.</p>
      ) : null}
      {tools.length ? (
        <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">
          Agent Reach tools: {tools.map(([t, v]) => `${t} ${v.status === "ok" ? "ok" : v.status}`).join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ the sample */

function Sample({ r }: { r: AuditReport }) {
  const s = r.sample;
  if (!s) return null;
  return (
    <section data-anim className="plate mt-4 px-4 py-4">
      <h2 className="text-[13.5px] font-semibold">A sample of the last hour, re-checked</h2>
      <p className="mt-0.5 text-[12px] text-ink-3">
        {s.checked
          ? `${s.passed} of ${s.checked} values written in the last hour still pass today's validators.`
          : "Nothing was written in the last hour to sample."}
      </p>
      {s.failed.length ? (
        <ul className="mt-3 divide-y divide-line text-[12px]">
          {s.failed.map((f) => (
            <li key={f.id} className="flex flex-wrap gap-x-3 gap-y-0.5 py-1.5">
              {f.company_id ? (
                <Link href={`/companies/${f.company_id}`} className="font-medium underline decoration-line underline-offset-2">#{f.company_id}</Link>
              ) : null}
              <span className="text-ink-2">{f.field}</span>
              <span className="min-w-0 flex-1 truncate text-ink-3" title={f.value}>{f.value}</span>
              <span className="text-ink">{f.why}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
