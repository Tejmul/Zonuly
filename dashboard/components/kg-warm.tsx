"use client";

/* Warm paths and similar companies — the knowledge graph doing outreach work
   (jobhunter/kg/warm.py, /api/kg/warm/*, /api/kg/similar/*).

   A warm path is a reason this person might answer you, read from their own words:
   their profile names your school or a place you worked, the same login is on file at a
   company you're already talking to, they work with the stack on your resume. Every path
   carries the quote it was read from. Similar companies are graph neighbours — shared
   investors (a small fund counts more than a mega fund) and the skills their jobs ask for.

   Used on the company page (full) and in the Graph page's side panel (compact). */

import Link from "next/link";
import { useApi } from "@/lib/api";

export type WarmPath = {
  type: string; weight: number; fact: string; opener?: string | null;
  evidence: string | null; source: string | null; via: string | null; contact_id?: number;
};
export type WarmPerson = {
  contact_id: number; name: string | null; role: string | null; role_class: string | null;
  affiliation: string | null; has_email: boolean; linkedin: string | null; github: string | null;
  graph_signal: number; signal_why: string[]; paths: WarmPath[];
};
export type Warm = {
  company_id: number; company: string; people: WarmPerson[]; also_writable: WarmPerson[];
  company_paths: WarmPath[]; background: { id: string; label: string; as: string }[]; summary: string;
};
export type Similar = {
  company_id: number; company: string;
  similar: { company_id: number; name: string; description: string | null; tier: string | null; city: string | null;
    stage: string | null; pay_power: number | null; score: number; why: string[]; like: string[] }[];
};

const TYPE_LABEL: Record<string, string> = {
  shared_school: "Your school",
  shared_employer: "Where you worked",
  same_person_engaged: "Known elsewhere",
  previously_at_engaged: "Ex-colleague link",
  shared_stack: "Shared stack",
  alumni_elsewhere: "Alumni elsewhere",
  investor_engaged: "Shared investor",
  same_city: "Same city",
  same_person: "Same person",
  previously_at: "Past employer",
};

function Strength({ w }: { w: number }) {
  // three ticks: ≥0.9 strong, ≥0.3 medium, else a talking point
  const n = w >= 0.9 ? 3 : w >= 0.3 ? 2 : 1;
  return (
    <span className="inline-flex gap-0.5" title={`path weight ${w.toFixed(2)}`} aria-label={`strength ${n} of 3`}>
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-2.5 w-1 rounded-sm ${i < n ? "bg-ink" : "bg-line"}`} />
      ))}
    </span>
  );
}

function PathLine({ p }: { p: WarmPath }) {
  return (
    <li className="text-[12.5px] leading-snug">
      <span className="inline-flex items-center gap-2">
        <Strength w={p.weight} />
        <span className="marginal">{TYPE_LABEL[p.type] ?? p.type}</span>
      </span>
      <span className="mt-0.5 block text-ink-2">{p.fact}</span>
      {p.evidence ? (
        <span className="mt-0.5 block border-l-2 border-line pl-2 text-[11.5px] text-ink-3 italic [overflow-wrap:anywhere]">
          {p.evidence}
          {p.source ? <span className="not-italic"> — {p.source.replace(/_/g, " ")}</span> : null}
        </span>
      ) : null}
    </li>
  );
}

/** The warm paths into one company. `compact` = the Graph panel: top 3 people, no openers. */
export function WarmPaths({ cid, compact = false }: { cid: number; compact?: boolean }) {
  const { data, error, loading } = useApi<Warm>(`/api/kg/warm/company/${cid}`);
  if (error) return <p className="text-[12px] text-ink-3">The graph could not be read: {error}</p>;
  if (loading && !data) return <p className="text-[12px] text-ink-3">Walking the graph…</p>;
  if (!data) return null;
  const people = compact ? data.people.slice(0, 3) : data.people;
  const company = compact ? data.company_paths.slice(0, 2) : data.company_paths;
  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-2">{data.summary}</p>
      {people.length > 0 && (
        <ul className="divide-y divide-line">
          {people.map((r) => (
            <li key={r.contact_id} className="py-2.5">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-[13px] font-medium">{r.name ?? "unnamed"}</span>
                {r.role_class && <span className="text-[12px] text-ink-3">{r.role_class}</span>}
                <span className="chip" title={r.signal_why.join(" · ")}>graph signal {r.graph_signal}</span>
                {!r.has_email && <span className="text-[11.5px] text-ink-3">no address yet</span>}
                {!compact && r.linkedin && (
                  <a href={r.linkedin} target="_blank" rel="noreferrer" className="ml-auto font-mono text-[11px] text-ink-3 hover:text-ink">in</a>
                )}
              </div>
              <ul className="mt-2 space-y-2">
                {(compact ? r.paths.slice(0, 1) : r.paths).map((p, i) => <PathLine key={i} p={p} />)}
              </ul>
              {!compact && r.paths[0]?.opener && (
                <p className="mt-2 text-[12px] text-ink-2">
                  <span className="marginal">opening line, if you use it · </span>{r.paths[0].opener}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {company.length > 0 && (
        <div>
          <h3 className="marginal mb-1.5">Paths through the company</h3>
          <ul className="space-y-2.5">{company.map((p, i) => <PathLine key={i} p={p} />)}</ul>
        </div>
      )}
      {!compact && data.background.length > 0 && (
        <p className="text-[11.5px] text-ink-3">
          Matched against your background: {data.background.map((b) => b.label).join(", ")} — read from profile.json.
          A path is only drawn when their own profile, headline or repos say it.
        </p>
      )}
    </div>
  );
}

/** "If you liked this company": graph neighbours with reasons. */
export function SimilarCompanies({ cid, limit = 6, onPick }: { cid: number; limit?: number; onPick?: (cid: number) => void }) {
  const { data, error, loading } = useApi<Similar>(`/api/kg/similar/${cid}?limit=${limit}`);
  if (error) return <p className="text-[12px] text-ink-3">The graph could not be read: {error}</p>;
  if (loading && !data) return <p className="text-[12px] text-ink-3">Finding neighbours…</p>;
  if (!data) return null;
  if (!data.similar.length) {
    return <p className="text-[12.5px] text-ink-3">No neighbour yet — no shared investor and no company hiring for three or more of the same skills.</p>;
  }
  return (
    <ul className="divide-y divide-line">
      {data.similar.map((s) => (
        <li key={s.company_id} className="py-2.5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            {onPick ? (
              <button onClick={() => onPick(s.company_id)} className="cursor-pointer text-[13px] font-medium underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                {s.name}
              </button>
            ) : (
              <Link href={`/companies/${s.company_id}`} className="text-[13px] font-medium hover:underline">{s.name}</Link>
            )}
            <span className="text-[12px] text-ink-3">{[s.city, s.stage].filter(Boolean).join(" · ")}</span>
            <span className="tnum ml-auto text-[11.5px] text-ink-3" title="similarity score">{s.score.toFixed(2)}</span>
          </div>
          {s.description && <p className="mt-0.5 line-clamp-1 text-[12px] text-ink-2">{s.description}</p>}
          <ul className="mt-1 space-y-0.5 text-[11.5px] text-ink-3">
            {s.why.map((w, i) => <li key={i}>+ {w}</li>)}
          </ul>
        </li>
      ))}
    </ul>
  );
}
