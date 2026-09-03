"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { rise } from "@/components/anim";
import {
  ArrowLeft, ExternalLink, GitBranch, Loader2, MapPin, ShieldAlert, ShieldCheck, ShieldQuestion, Users,
} from "lucide-react";
import { api, useApi, useTaskWatcher } from "@/lib/api";
import { TierPill } from "@/components/ui/tier";

/* One page per company, in the order you read it: who they are → the hiring post →
   the roles → the people to ask → the money. Every number sits next to the sentence
   it came from, and every action on the page is a button here, not a CLI command. */

type Job = {
  id: number; title: string; location: string | null; remote: boolean; url: string; source: string;
  score: number | null; salary_min_lpa: number | null; salary_max_lpa: number | null;
  is_internship: boolean; stipend_inr_month: number | null; is_senior: boolean; remote_anywhere: boolean;
  posted_at: string | null;
};
type Person = {
  id: number; name: string | null; role: string | null; role_class: string | null; label: string | null;
  rank: number | null; email: string | null; confidence: string | null; evidence: string | null;
  github: string | null; reachable: boolean; source: string;
};
type Detail = {
  id: number; name: string; description: string | null; website: string | null;
  domain: string | null; github_org: string | null; region: string | null; locations: string[];
  segment: { id: string; label: string; layer: string };
  trust?: string; evidence?: Record<string, boolean>;
  story: string | null; story_evidence: string | null;
  valuation_usd_m: number | null; valuation_evidence: string | null; team_size: number | null;
  pay_basis: string | null; pay_basis_evidence: string | null;
  pay_power?: { score: number | null; band: string | null; why: string | null; per_head_usd_k: number | null };
  checks: Record<string, boolean>; bet: boolean; missing: string[];
  hiring_post: { text: string | null; url: string; by: string | null; source: string | null; at: string | null } | null;
  tier: string | null; tier_reason: string | null; underrated: boolean | null; hype_reason: string | null;
  pay: { stipend_inr_month: number | null; stipend_evidence: string | null; ppo_lpa: number | null; ppo_evidence: string | null };
  funding: { stage: string | null; amount_usd_m: number | null; announced: string | null; investors: string[]; evidence: string | null };
  hiring: { status: string | null; evidence: string | null; careers_url: string | null; roles_on_their_page: string[]; checked_at: string | null };
  jobs: Job[];
  people: Person[];
  leads: number;
  graph: { verdict: { act: string; why: string } | null; skills_they_want_that_i_have: string[]; who_to_ask: { name: string; employment: string | null; employment_why: string | null }[] } | null;
};

const SOURCE_LABEL: Record<string, string> = {
  x: "X", hn: "Hacker News", yc: "the YC directory", ats: "their own job board", careers: "their careers page",
  reddit: "Reddit", exa: "web search", greenhouse: "their Greenhouse board", lever: "their Lever board",
  ashby: "their Ashby board", remoteok: "RemoteOK", wwr: "We Work Remotely", hn_hiring: "Hacker News",
};
const REGION_LABEL: Record<string, string> = {
  us: "United States", uk: "United Kingdom", de: "Germany", nl: "Netherlands", eu: "Europe", india: "India", remote: "Remote",
};
const PP_BAND_LABEL: Record<string, string> = {
  pays: "Pays — they wrote the number", deep_pockets: "Deep pockets", funded: "Funded — can pay",
  thin: "Thin — no evidence they can", unknown: "Unknown — needs research",
};
const PP_BANDS: [string, number, string][] = [
  ["Pays", 100, "a posting states ≥ ₹30 L"],
  ["Deep pockets", 85, "raised $10M+ in the last 24 months, or Series A+ in the last 30 months, or valued ≥ $50M"],
  ["Funded", 65, "raised $3–10M in the last 30 months, or a YC batch in the last 30 months with 10+ people"],
  ["Thin", 30, "under $2M, pre-seed, or the raise is older than 30 months"],
  ["Unknown", 0, "nothing found yet"],
];
const PAY_BASIS_LABEL: Record<string, string> = {
  stated: "Yes — a posting states the pay.",
  "funding-strong": "Very likely — judged from funding, not a stated figure. An estimate, shown as one.",
  "funding-weak": "Doubtful — the funding we found is small, old or in rupees. Needs a stated figure.",
  none: "Unknown — no pay stated and no funding found. Needs research before we spend an email on it.",
};
const MISSING_LABEL: Record<string, string> = {
  hiring: "hiring not yet proven on their own site", fresher: "no fresher role scraped",
  location: "no remote-from-anywhere role (and not in India)", lead: "no lead with a usable email",
};

export default function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error, reload } = useApi<Detail>(`/api/companies/${id}/detail`);
  const { tasks, watch } = useTaskWatcher();
  const [busy, setBusy] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useGSAP(() => { if (data) rise("[data-anim='sec']", { stagger: 0.045 }); },
    { scope: root, dependencies: [!!data] });

  async function run(label: string, path: string, body?: unknown) {
    setBusy(label);
    try {
      const { task_id } = await api.post<{ task_id: string }>(path, body);
      watch(task_id, () => { setBusy(null); void reload(); });
    } catch {
      setBusy(null);
    }
  }

  if (error) return <p className="text-[13px] text-ink-3">{error}</p>;
  if (loading && !data) return <p className="text-sm text-ink-3">Reading the file…</p>;
  if (!data) return null;

  const fresher = data.jobs.filter((j) => !j.is_senior);
  const senior = data.jobs.filter((j) => j.is_senior);
  const leads = data.people.filter((p) => p.reachable);
  const others = data.people.filter((p) => !p.reachable);
  const lastTask = tasks[0];

  return (
    <div ref={root} className="mx-auto max-w-4xl">
      <Link href="/companies" className="inline-flex cursor-pointer items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink">
        <ArrowLeft size={14} /> Companies
      </Link>

      {/* ---------------------------------------------------------- 1. the company */}
      <header data-anim="sec" className="mt-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight">{data.name}</h1>
          {data.tier && <TierPill tier={data.tier} />}
          {data.bet && <span className="chip chip-solid">ready to ask</span>}
        </div>
        <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <MapPin size={13} className="text-ink-3" />
            {data.region ? REGION_LABEL[data.region] ?? data.region.toUpperCase() : "Location unstated"}
            {data.locations.length > 0 && <span className="text-ink-3"> · roles in {data.locations.slice(0, 3).join(", ")}</span>}
          </span>
          <span className="text-ink-3">{data.segment.label}</span>
          {data.funding.stage && <span className="text-ink-3">{data.funding.stage}{data.funding.amount_usd_m ? ` · $${data.funding.amount_usd_m}M` : ""}</span>}
        </p>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-ink-2">
          {data.description ?? "No description yet."}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {data.website && <OutLink href={data.website} icon={<ExternalLink size={13} />}>Website</OutLink>}
          {data.hiring.careers_url && <OutLink href={data.hiring.careers_url} icon={<ExternalLink size={13} />}>Careers</OutLink>}
          {data.github_org && <OutLink href={`https://github.com/${data.github_org}`} icon={<GitBranch size={13} />}>GitHub</OutLink>}
          {!data.description && (
            <ActionButton busy={busy === "enrich"} onClick={() => run("enrich", `/api/companies/${data.id}/enrich`)}>
              Read their site
            </ActionButton>
          )}
          {data.team_size ? <span className="marginal self-center">{data.team_size} people</span> : null}
        </div>
      </header>

      {/* ---------------------------------------------------------- what is missing */}
      {!data.bet && (
        <section data-anim="sec" className="mt-6 plate bg-surface-2 px-4 py-3.5">
          <p className="eyebrow">Before we write to anyone here</p>
          <ul className="mt-2 space-y-1 text-[13px] text-ink-2">
            {data.missing.map((m) => <li key={m}>· {MISSING_LABEL[m] ?? m}</li>)}
          </ul>
          {data.graph?.verdict && (
            <p className="mt-2 text-[12.5px] leading-relaxed text-ink-3">{data.graph.verdict.why}</p>
          )}
        </section>
      )}

      {/* ---------------------------------------------------------- 2. the hiring post */}
      <Section title="The hiring post" sub="Where we saw that they are hiring — the thing to show if anyone asks">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1">
            {data.hiring_post ? (
              <p className="text-[13px] leading-relaxed">
                <a href={data.hiring_post.url} target="_blank" rel="noreferrer" className="font-medium underline decoration-line underline-offset-2 hover:decoration-ink">
                  {data.hiring_post.text ?? data.hiring_post.url}
                </a>
                <span className="block text-[12px] text-ink-3">
                  {data.hiring_post.by ? `posted by ${data.hiring_post.by}` : "the company itself"}
                  {data.hiring_post.source ? ` on ${SOURCE_LABEL[data.hiring_post.source] ?? data.hiring_post.source}` : ""}
                  {data.hiring_post.at ? ` · ${data.hiring_post.at.slice(0, 10)}` : ""}
                </span>
              </p>
            ) : (
              <p className="text-[13px] text-ink-3">No post on record yet.</p>
            )}
          </div>
          <div className="shrink-0">
            <HiringVerdict hiring={data.hiring} />
          </div>
        </div>
        {data.hiring.evidence && <Quote>{data.hiring.evidence}</Quote>}
        <div className="mt-3">
          <ActionButton busy={busy === "verify"} onClick={() => run("verify", `/api/companies/${data.id}/verify-hiring?fresh=true`)}>
            Re-check on their site
          </ActionButton>
        </div>
      </Section>

      {/* ---------------------------------------------------------- 3. the roles */}
      <Section
        title={`Roles (${fresher.length} fresher · ${senior.length} senior)`}
        sub="Fresher roles first. Senior titles are kept so you can see they are hiring, not to apply to."
      >
        {data.jobs.length === 0 ? (
          <p className="text-[13px] text-ink-3">None scraped yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {[...fresher, ...senior].slice(0, 20).map((j) => (
              <li key={j.id} className="flex items-center gap-3 py-2.5">
                <a href={j.url} target="_blank" rel="noreferrer" className={`truncate text-[13px] hover:underline ${j.is_senior ? "text-ink-3" : ""}`}>
                  {j.title}
                </a>
                {j.remote_anywhere && <span className="chip shrink-0">from anywhere</span>}
                {j.is_internship && <span className="chip shrink-0">intern</span>}
                {j.is_senior && <span className="chip shrink-0">senior</span>}
                <span className="ml-auto shrink-0 text-[12px] text-ink-3">{j.location ?? (j.remote ? "Remote" : "—")}</span>
                {j.salary_min_lpa != null && (
                  <span className="tnum shrink-0 text-[12px] text-ink-2">₹{Math.round(j.salary_min_lpa)}{j.salary_max_lpa ? `–${Math.round(j.salary_max_lpa)}` : ""}L</span>
                )}
                {j.score != null && <span className="tnum shrink-0 text-[12px] text-ink-2">{j.score}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* ---------------------------------------------------------- 4. the leads */}
      <Section
        title={`Leads to reach (${leads.length})`}
        sub="People here we can actually write to, best referrer first. Drafts go to the Queue for your approval."
        action={
          <ActionButton busy={busy === "people"} onClick={() => run("people", `/api/companies/${data.id}/find-contacts`)}>
            <Users size={13} /> Find leads
          </ActionButton>
        }
      >
        {leads.length === 0 ? (
          <p className="text-[13px] text-ink-3">
            {data.people.length ? "People on file, but none with a usable address yet." : "Nobody found yet — press Find leads."}
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {leads.slice(0, 15).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
                <span className="text-[13px] font-medium">{p.name ?? "unnamed"}</span>
                <span className="text-[12px] text-ink-3">{p.label ?? p.role_class}</span>
                <span className="tnum text-[12px] text-ink-2">{p.email}</span>
                <span className="chip">{p.confidence}</span>
                <span className="ml-auto flex items-center gap-2">
                  {p.github && <a href={p.github} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink"><GitBranch size={13} /></a>}
                  <ActionButton
                    busy={busy === `draft:${p.id}`}
                    onClick={() => run(`draft:${p.id}`, "/api/emails/draft", { contact_id: p.id, job_id: fresher[0]?.id ?? data.jobs[0]?.id ?? null })}
                  >
                    Draft email
                  </ActionButton>
                </span>
                {p.evidence && <span className="w-full text-[11.5px] text-ink-3">{p.evidence}</span>}
              </li>
            ))}
          </ul>
        )}
        {others.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-[12px] text-ink-3">{others.length} more on file without a usable address</summary>
            <ul className="mt-2 divide-y divide-line">
              {others.slice(0, 20).map((p) => (
                <li key={p.id} className="flex items-center gap-3 py-2 text-[12.5px] text-ink-3">
                  <span>{p.name ?? "unnamed"}</span><span>{p.label ?? p.role_class}</span>
                  <span className="ml-auto">{p.email ?? "no address"}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </Section>

      {/* ---------------------------------------------------------- 5. the story */}
      <Section
        title={data.story_evidence?.startsWith("http") ? "How they started" : "What they say they do"}
        sub={data.story_evidence?.startsWith("http")
          ? "Why they started, how and where it began, who — read from their own About page, never invented"
          : "From the YC directory. Their About page has not been read for the origin story yet — press the button."}
        action={
          <ActionButton busy={busy === "enrich"} onClick={() => run("enrich", `/api/companies/${data.id}/enrich?fresh=true`)}>
            Read their site again
          </ActionButton>
        }
      >
        {data.story ? (
          <>
            <p className="max-w-3xl text-[13px] leading-relaxed text-ink-2">{data.story}</p>
            {data.story_evidence && (
              <p className="mt-2 text-[11.5px] text-ink-3">
                read from{" "}
                {data.story_evidence.startsWith("http")
                  ? <a href={data.story_evidence} target="_blank" rel="noreferrer" className="underline underline-offset-2">{data.story_evidence.replace(/^https?:\/\//, "").slice(0, 60)}</a>
                  : data.story_evidence}
              </p>
            )}
          </>
        ) : (
          <p className="text-[13px] text-ink-3">Not read yet — nothing on their site or in the directory told the story.</p>
        )}
        <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2">
          <div><dt className="marginal">team</dt><dd className="tnum mt-0.5 text-[12.5px]">{data.team_size ? `${data.team_size} people` : "—"}</dd></div>
          <div><dt className="marginal">valuation</dt><dd className="tnum mt-0.5 text-[12.5px]">{data.valuation_usd_m ? `$${data.valuation_usd_m}M (stated)` : "not stated anywhere we read"}</dd></div>
          <div><dt className="marginal">investors</dt><dd className="mt-0.5 text-[12.5px]">{data.funding.investors.length ? data.funding.investors.join(", ") : "—"}</dd></div>
        </dl>
        {data.valuation_evidence && <Quote>{data.valuation_evidence}</Quote>}
      </Section>

      {/* ---------------------------------------------------------- 6. the money */}
      <Section
        title="Can they pay a fresher ₹30–40 L? — Pay Power"
        sub="The benchmark: they wrote the number (100) · deep pockets (85) · funded (65) · thin (30) · unknown (0). The sentence below is what decided it."
      >
        {data.pay_power?.band && (
          <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className={`tnum text-3xl font-semibold ${(data.pay_power.score ?? 0) >= 65 ? "" : "text-ink-3"}`}>
              {data.pay_power.score}
            </span>
            <div className="min-w-0">
              <p className="text-[13px] font-medium">{PP_BAND_LABEL[data.pay_power.band] ?? data.pay_power.band}</p>
              <p className="text-[12.5px] leading-relaxed text-ink-2">{data.pay_power.why}</p>
              {data.pay_power.per_head_usd_k ? (
                <p className="mt-0.5 text-[11.5px] text-ink-3">≈ ${data.pay_power.per_head_usd_k.toLocaleString()}k raised per person on the team</p>
              ) : null}
            </div>
          </div>
        )}
        <details className="mb-4 text-[12px] text-ink-2">
          <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">How the score is decided</summary>
          <ul className="mt-2 space-y-1">
            {PP_BANDS.map(([band, score, rule]) => (
              <li key={band}><span className="tnum font-medium">{score}</span> <span className="font-medium">{band}</span> — {rule}</li>
            ))}
            <li className="text-ink-3">India needs the stated number to go above Thin. Hyped names and teams over 200 are out regardless.</li>
          </ul>
        </details>
        <dl className="grid gap-5 sm:grid-cols-3">
          <Evidence
            label="Full-time package"
            value={data.pay.ppo_lpa ? `₹${data.pay.ppo_lpa} LPA` : "Not stated"}
            quote={data.pay.ppo_evidence}
          />
          <Evidence
            label="Internship stipend"
            value={data.pay.stipend_inr_month ? `₹${data.pay.stipend_inr_month.toLocaleString()} / month` : "Not stated"}
            quote={data.pay.stipend_evidence}
          />
          <Evidence
            label="Funding"
            value={data.funding.stage ? `${data.funding.stage}${data.funding.amount_usd_m ? ` · $${data.funding.amount_usd_m}M` : ""}` : "Not stated"}
            quote={data.funding.evidence}
          />
        </dl>
        {data.underrated === false && data.hype_reason && (
          <p className="mt-4 rounded-md border border-line px-3 py-2 text-[12px] text-ink-2">{data.hype_reason}</p>
        )}
      </Section>

      {lastTask && (
        <p className="mt-4 text-[12px] text-ink-3">
          {lastTask.status === "running" ? <Loader2 size={12} className="mr-1 inline animate-spin" /> : null}
          {lastTask.name}: {lastTask.status}{lastTask.error ? ` — ${lastTask.error}` : ""}
        </p>
      )}
    </div>
  );
}

function HiringVerdict({ hiring }: { hiring: Detail["hiring"] }) {
  const map: Record<string, { icon: React.ReactNode; text: string; cls: string }> = {
    verified: { icon: <ShieldCheck size={15} />, text: "Proven on their own site", cls: "chip-solid" },
    role_missing: { icon: <ShieldQuestion size={15} />, text: "Hiring — but not this role", cls: "" },
    not_authorized: { icon: <ShieldAlert size={15} />, text: "Their site does not back it", cls: "chip-strike" },
    unreachable: { icon: <ShieldQuestion size={15} />, text: "Could not reach their site", cls: "" },
  };
  const v = map[hiring.status ?? ""] ?? { icon: <ShieldQuestion size={15} />, text: "Not checked yet", cls: "" };
  return <div className={`chip !h-auto !py-1.5 ${v.cls}`}>{v.icon} {v.text}</div>;
}

function Section({
  title, sub, action, children,
}: { title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section data-anim="sec" className="mt-6 plate px-4 py-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {sub && <p className="mt-1 text-xs leading-relaxed text-ink-3">{sub}</p>}
        </div>
        {action && <div className="ml-auto shrink-0">{action}</div>}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Evidence({ label, value, quote }: { label: string; value: string; quote: string | null }) {
  return (
    <div>
      <dt className="text-[12px] text-ink-3">{label}</dt>
      <dd className="tnum mt-1 text-[15px] font-medium">{value}</dd>
      {quote && <Quote>{quote}</Quote>}
    </div>
  );
}

/** Every number on this page can be traced to a sentence someone actually wrote. */
function Quote({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-2 border-l-2 border-line pl-3 text-[12px] leading-relaxed text-ink-3 italic">{children}</p>
  );
}

function OutLink({ href, icon, children }: { href: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line px-3
        text-[12px] transition-colors hover:bg-surface-2"
    >
      {icon} {children}
    </a>
  );
}

function ActionButton({ busy, onClick, children }: { busy: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-3
        text-[12px] font-medium transition-colors hover:bg-surface-2 disabled:cursor-wait disabled:opacity-60"
    >
      {busy ? <Loader2 size={13} className="animate-spin" /> : null}
      {children}
    </button>
  );
}
