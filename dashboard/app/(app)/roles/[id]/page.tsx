"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, GitBranch, Mail, UserSearch } from "lucide-react";
import { api, useApi, useTaskWatcher, type Job } from "@/lib/api";
import { lpa, relTime } from "@/lib/utils";
import { TaskStrip } from "@/components/shell";
import { ConfidenceBadge, Odds } from "@/components/signals";
import {
  Badge,
  Button,
  Empty,
  ErrorNote,
  Loading,
  Panel,
  PanelHead,
} from "@/components/ui/primitives";

const BREAKDOWN_LABELS: Record<string, string> = {
  skills_overlap: "Skills overlap",
  experience_fit: "Experience fit",
  early_career_friendly: "Early-career friendly",
  domain_fit: "Domain fit",
};

interface CompanyNotes {
  type?: string;
  text?: string;
  funding_tier?: string;
  lead_investors?: string;
  runway?: string;
  headcount?: string;
}

function parseNotes(raw: string | null): CompanyNotes {
  if (!raw) return { type: "", text: "", funding_tier: "", lead_investors: "", runway: "", headcount: "" };
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return {
        type: parsed.type || "",
        text: parsed.text || "",
        funding_tier: parsed.funding_tier || "",
        lead_investors: parsed.lead_investors || "",
        runway: parsed.runway || "",
        headcount: parsed.headcount || "",
      };
    }
  } catch (e) {
    return { type: "", text: raw, funding_tier: "", lead_investors: "", runway: "", headcount: "" };
  }
  return { type: "", text: "", funding_tier: "", lead_investors: "", runway: "", headcount: "" };
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const { data: job, error, loading, reload } = useApi<Job>(id ? `/api/jobs/${id}` : null);
  const { tasks, watch, dismiss } = useTaskWatcher();

  const [companyType, setCompanyType] = useState("");
  const [notesText, setNotesText] = useState("");
  const [fundingTier, setFundingTier] = useState("");
  const [leadInvestors, setLeadInvestors] = useState("");
  const [runway, setRunway] = useState("");
  const [headcount, setHeadcount] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);

  useEffect(() => {
    if (job?.company) {
      const parsed = parseNotes(job.company.notes ?? null);
      setCompanyType(parsed.type || "");
      setNotesText(parsed.text || "");
      setFundingTier(parsed.funding_tier || "");
      setLeadInvestors(parsed.lead_investors || "");
      setRunway(parsed.runway || "");
      setHeadcount(parsed.headcount || "");
    }
  }, [job?.company?.id, job?.company?.notes]);

  async function saveCompanyNotes() {
    if (!job?.company_id) return;
    setSavingNotes(true);
    try {
      const payload: CompanyNotes = {
        type: companyType,
        text: notesText,
        funding_tier: fundingTier,
        lead_investors: leadInvestors,
        runway: runway,
        headcount: headcount,
      };
      await api.patch(`/api/companies/${job.company_id}`, {
        notes: JSON.stringify(payload),
      });
      void reload();
    } catch (e) {
      console.error("Failed to save company notes:", e);
    } finally {
      setSavingNotes(false);
    }
  }

  async function findContacts() {
    if (!job?.company_id) return;
    const { task_id } = await api.post<{ task_id: string }>(
      `/api/companies/${job.company_id}/find-contacts`,
    );
    watch(task_id, () => void reload());
  }

  async function draftTo(contactId: number) {
    const { task_id } = await api.post<{ task_id: string }>("/api/emails/draft", {
      contact_id: contactId,
      job_id: job?.id ?? null,
    });
    watch(task_id, () => void reload());
  }

  async function markApplied() {
    if (!job) return;
    await api.patch(`/api/jobs/${job.id}`, {
      status: job.status === "applied" ? "high_match" : "applied",
    });
    void reload();
  }

  if (loading && !job) return <Loading label="Loading job" />;
  if (error) return <ErrorNote message={error} onRetry={reload} />;
  if (!job) return null;

  const pay = lpa(job.salary_min_lpa, job.salary_max_lpa);
  const breakdown = Object.entries(job.breakdown ?? {});
  const companyNotes = parseNotes(job.company?.notes ?? null);

  return (
    <>
      <Link
        href="/roles"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-ink-3 transition-colors hover:text-ink"
      >
        <ArrowLeft size={13} /> All leads
      </Link>

      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">{job.title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-ink-3">
            <span className="text-ink">{job.company_name}</span>
            <span className="text-ink-3">·</span>
            <span>{job.remote ? "Remote" : (job.location ?? "Location not stated")}</span>
            {pay ? (
              <>
                <span className="text-ink-3">·</span>
                <span className="tnum text-ink">{pay} LPA</span>
              </>
            ) : null}
            <Badge>{job.source}</Badge>
            {companyNotes.type ? (
              <span className="inline-flex items-center rounded-sm border border-violet-800/60 bg-violet-950/30 px-2 py-0.5 text-xs font-medium text-violet-300">
                {companyNotes.type}
              </span>
            ) : null}
            {companyNotes.funding_tier ? (
              <span className="inline-flex items-center rounded-sm border border-fuchsia-800/60 bg-fuchsia-950/30 px-2 py-0.5 text-xs font-medium text-fuchsia-300">
                {companyNotes.funding_tier}
              </span>
            ) : null}
            {companyNotes.runway ? (
              <span className="inline-flex items-center rounded-sm border border-purple-800/60 bg-purple-950/30 px-2 py-0.5 text-xs font-medium text-purple-300">
                Runway: {companyNotes.runway}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant={job.status === "applied" ? "default" : "quiet"} size="sm" onClick={markApplied}>
            {job.status === "applied" ? "Applied ✓" : "Mark applied"}
          </Button>
          <Button asChild size="sm" variant="quiet">
            <a href={job.url} target="_blank" rel="noreferrer">
              Open posting <ExternalLink size={13} />
            </a>
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="flex flex-col gap-4 xl:col-span-2">
          <Panel>
            <PanelHead eyebrow="The read" title="Why this scored the way it did" />
            <div className="p-4">
              <div className="mb-4 flex items-center gap-4">
                <Odds score={job.match_score} size="lg" showLabel />
              </div>
              {job.verdict ? (
                <p className="mb-4 text-sm leading-relaxed text-ink">{job.verdict}</p>
              ) : null}

              {breakdown.length ? (
                <div className="mb-4 grid gap-2 sm:grid-cols-2">
                  {breakdown.map(([key, value]) => (
                    <div key={key} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 text-xs text-ink-3">
                        {BREAKDOWN_LABELS[key] ?? key}
                      </span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                        <div className="h-full bg-surface-2" style={{ width: `${value}%` }} />
                      </div>
                      <span className="tnum w-8 text-right text-xs text-ink-2">{value}</span>
                    </div>
                  ))}
                </div>
              ) : null}

              {job.reasons?.length ? (
                <div className="mb-4">
                  <div className="eyebrow mb-2">In your favour</div>
                  <ul className="flex flex-col gap-1.5">
                    {job.reasons.map((reason, i) => (
                      <li key={i} className="flex gap-2 text-sm text-ink">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-surface-2" />
                        {reason}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {job.gaps?.length ? (
                <div>
                  <div className="eyebrow mb-2">Gaps to expect questions on</div>
                  <ul className="flex flex-col gap-1.5">
                    {job.gaps.map((gap, i) => (
                      <li key={i} className="flex gap-2 text-sm text-ink-2">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-surface-2" />
                        {gap}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <PanelHead
              eyebrow="Source text"
              title="Job description"
              action={
                job.salary_raw ? (
                  <span className="tnum text-xs text-ink-3">Pay read from: {job.salary_raw}</span>
                ) : null
              }
            />
            <div className="max-h-[32rem] overflow-y-auto p-4">
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink-2">
                {job.description || "No description was captured for this posting."}
              </pre>
            </div>
          </Panel>
        </div>

        <div className="flex flex-col gap-4">
          <Panel>
            <PanelHead
              eyebrow="Who to ask"
              title="Leads at this company"
              action={
                <Button size="sm" variant="quiet" onClick={findContacts}>
                  <UserSearch size={13} />
                  Find
                </Button>
              }
            />
            {!job.contacts?.length ? (
              <Empty
                title="No contacts yet"
                hint="Finding contacts mines the company's public GitHub commits for real engineer emails, then their website for recruiter addresses."
              />
            ) : (
              <ul className="divide-y divide-line">
                {job.contacts.map((contact) => (
                  <li key={contact.id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm text-ink">
                          {contact.name ?? contact.email}
                        </div>
                        <div className="tnum mt-0.5 truncate text-xs text-ink-3">
                          {contact.email}
                        </div>
                        {contact.role ? (
                          <div className="mt-1 line-clamp-2 text-xs text-ink-3">{contact.role}</div>
                        ) : null}
                      </div>
                      <ConfidenceBadge confidence={contact.confidence} />
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      <Button size="sm" variant="quiet" onClick={() => draftTo(contact.id)}>
                        <Mail size={12} />
                        Draft ask
                      </Button>
                      {contact.github ? (
                        <a
                          href={contact.github}
                          target="_blank"
                          rel="noreferrer"
                          className="text-ink-3 transition-colors hover:text-ink"
                          aria-label="GitHub profile"
                        >
                          <GitBranch size={13} />
                        </a>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel className="border-violet-900/50">
            <PanelHead 
              eyebrow="Strategic Chokepoint (Human-in-the-Loop)" 
              title={<span className="text-violet-300 font-semibold flex items-center gap-1.5">Company Context & VC Tracker</span>} 
            />
            <div className="flex flex-col gap-3.5 p-4 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1.5">
                  <span className="eyebrow text-[10px] text-violet-400">Classification</span>
                  <select
                    value={companyType}
                    onChange={(e) => setCompanyType(e.target.value)}
                    className="rounded-sm border border-line bg-paper px-2 py-1.5 text-xs text-ink focus:border-violet-500 outline-none cursor-pointer"
                  >
                    <option value="">Unclassified</option>
                    <option value="Series A AI Startup">Series A AI Startup</option>
                    <option value="Big Tech / High Payer">Big Tech / High Payer</option>
                    <option value="Bootstrapped / Small Team">Bootstrapped / Small Team</option>
                    <option value="YC Company">YC Company</option>
                    <option value="Agency / Consult">Agency / Consult</option>
                  </select>
                </label>

                <label className="flex flex-col gap-1.5">
                  <span className="eyebrow text-[10px] text-violet-400">Funding Tier</span>
                  <select
                    value={fundingTier}
                    onChange={(e) => setFundingTier(e.target.value)}
                    className="rounded-sm border border-line bg-paper px-2 py-1.5 text-xs text-ink focus:border-violet-500 outline-none cursor-pointer"
                  >
                    <option value="">Unspecified</option>
                    <option value="Seed">Seed Stage</option>
                    <option value="Series A">Series A</option>
                    <option value="Series B">Series B</option>
                    <option value="Series C+">Series C+</option>
                    <option value="Bootstrapped">Bootstrapped</option>
                    <option value="Self-Sustaining">Self-Sustaining</option>
                  </select>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1.5">
                  <span className="eyebrow text-[10px] text-violet-400">Runway Runway</span>
                  <select
                    value={runway}
                    onChange={(e) => setRunway(e.target.value)}
                    className="rounded-sm border border-line bg-paper px-2 py-1.5 text-xs text-ink focus:border-violet-500 outline-none cursor-pointer"
                  >
                    <option value="">Unspecified</option>
                    <option value="Critical (<6mo)">{"Critical (<6mo)"}</option>
                    <option value="Medium (6-18mo)">Medium (6-18mo)</option>
                    <option value="Stable (18-36mo)">Stable (18-36mo)</option>
                    <option value="Evergreen / Profit">Evergreen / Profit</option>
                  </select>
                </label>

                <label className="flex flex-col gap-1.5">
                  <span className="eyebrow text-[10px] text-violet-400">Headcount scale</span>
                  <select
                    value={headcount}
                    onChange={(e) => setHeadcount(e.target.value)}
                    className="rounded-sm border border-line bg-paper px-2 py-1.5 text-xs text-ink focus:border-violet-500 outline-none cursor-pointer"
                  >
                    <option value="">Unspecified</option>
                    <option value="1-10 (Founding)">1-10 (Founding)</option>
                    <option value="11-50">11-50 (Early)</option>
                    <option value="51-200">51-200 (Scale)</option>
                    <option value="200+">200+ (Large)</option>
                  </select>
                </label>
              </div>

              <label className="flex flex-col gap-1.5">
                <span className="eyebrow text-[10px] text-violet-400">Lead Investors</span>
                <input
                  type="text"
                  value={leadInvestors}
                  onChange={(e) => setLeadInvestors(e.target.value)}
                  placeholder="Sequoia, Founders Fund, Y Combinator, etc."
                  className="rounded-sm border border-line bg-paper px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-3 focus:border-violet-500 outline-none"
                />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="eyebrow text-[10px] text-violet-400">Private Notes & Strategic Insights</span>
                <textarea
                  value={notesText}
                  onChange={(e) => setNotesText(e.target.value)}
                  placeholder="Enter custom context, scheduled interviews, specific contact instructions, or shared connections..."
                  rows={4}
                  className="rounded-sm border border-line bg-paper p-2 text-xs text-ink placeholder:text-ink-3 focus:border-violet-500 outline-none resize-none"
                />
              </label>

              <Button
                size="sm"
                variant="primary"
                onClick={saveCompanyNotes}
                disabled={savingNotes}
                className="mt-1 font-medium bg-violet-600 border-violet-500 text-white hover:bg-violet-500"
              >
                {savingNotes ? "Saving Profile..." : "Save Strategic Context"}
              </Button>
            </div>
          </Panel>

          <Panel>
            <PanelHead eyebrow="Provenance" title="Company" />
            <dl className="flex flex-col gap-2 p-4 text-xs">
              {[
                ["Website", job.company?.website],
                ["Email domain", job.company?.domain],
                ["GitHub org", job.company?.github_org],
                ["Email pattern", job.company?.email_pattern],
                ["Leads last found", relTime(job.company?.contacts_found_at)],
                ["Resume similarity", job.embed_sim?.toFixed(3)],
                ["First seen", relTime(job.scraped_at)],
              ].map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between gap-3">
                  <dt className="text-ink-3">{label}</dt>
                  <dd className="tnum truncate text-right text-ink">{value || "—"}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
