"use client";

/*  Leads — the people who can refer us, LinkedIn first-class (2026-10-14).
 *
 *  GET /api/leads: ready referrers (guardrails.is_ready) with where they came from — a
 *  public LinkedIn search result, GitHub, the company's team page, a talk or a post — the
 *  company's city (Bengaluru first), the affiliation label and the evidence behind it, the
 *  public profile URL, the address with its confidence label, and when they were found.
 *  The LinkedIn link only opens in a new tab: nothing here ever fetches a profile.
 */

import Link from "next/link";
import { useState } from "react";
import { ExternalLink, GitBranch, Mail, Star, UserSearch } from "lucide-react";
import { api, useApi, useTaskWatcher, type Company, type Lead, type LeadsPayload } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { OperatorOnly } from "@/components/access";
import { Stat } from "@/components/charts";
import { PageHead, TaskStrip } from "@/components/shell";
import { AffiliationBadge, ConfidenceBadge, Odds } from "@/components/signals";
import { Badge, Button, Empty, ErrorNote, Loading, Panel, PanelHead, Select } from "@/components/ui/primitives";

const PAGE = 50;

const SOURCES: { value: string; label: string }[] = [
  { value: "all", label: "All sources" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "github", label: "GitHub" },
  { value: "team", label: "Team page" },
  { value: "talks", label: "Talks & posts" },
  { value: "web", label: "Published address" },
];

const CITIES: { value: string; label: string }[] = [
  { value: "all", label: "All places" },
  { value: "bengaluru", label: "Bengaluru" },
  { value: "hyderabad", label: "Hyderabad" },
  { value: "india", label: "Rest of India" },
  { value: "remote", label: "Remote" },
];

const CHANNEL_LABEL: Record<string, string> = {
  linkedin: "LinkedIn",
  github: "GitHub",
  team: "Team page",
  talks: "Talks & posts",
  web: "Published address",
};

export default function LeadsPage() {
  const [source, setSource] = useState("all");
  const [city, setCity] = useState("all");
  const [who, setWho] = useState("ready");
  const [offset, setOffset] = useState(0);

  const params = new URLSearchParams({ source, city, who, offset: String(offset), limit: String(PAGE) });
  const leads = useApi<LeadsPayload>(`/api/leads?${params.toString()}`);
  // /api/companies lists complete companies only, and complete means a referrer is on file, so
  // the companies with no leads have to be asked for explicitly (best-scoring first)
  const companies = useApi<Company[]>("/api/companies?include_incomplete=true&has_contacts=false&sort=score&limit=40");
  const { tasks, watch, dismiss } = useTaskWatcher();

  function pick(set: (v: string) => void) {
    return (e: React.ChangeEvent<HTMLSelectElement>) => {
      set(e.target.value);
      setOffset(0);
    };
  }

  async function findFor(companyId: number) {
    const { task_id } = await api.post<{ task_id: string }>(`/api/companies/${companyId}/find-contacts`);
    watch(task_id, () => {
      void leads.reload();
      void companies.reload();
    });
  }

  async function draftTo(contactId: number) {
    const { task_id } = await api.post<{ task_id: string }>("/api/emails/draft", { contact_id: contactId });
    watch(task_id);
  }

  const data = leads.data;
  const counts = data?.counts;
  const withoutContacts = (companies.data ?? []).filter(
    (c) => (c.contacts ?? 0) === 0 && (c.best_score ?? 0) >= 50,
  );
  const scope = CITIES.find((c) => c.value === city)?.label ?? "All places";

  return (
    <>
      <PageHead
        title="Leads"
        description="People who could refer you: confirmed at the company now, where we found them, and how sure we are of the address. Bengaluru first."
        action={
          <>
            <Select value={source} onChange={pick(setSource)} aria-label="Source">
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                  {counts && s.value !== "all" ? ` (${counts.by_source[s.value as keyof typeof counts.by_source] ?? 0})` : ""}
                </option>
              ))}
            </Select>
            <Select value={city} onChange={pick(setCity)} aria-label="City">
              {CITIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
            <Select value={who} onChange={pick(setWho)} aria-label="Who">
              <option value="ready">Ready referrers</option>
              <option value="all">Every lead found</option>
            </Select>
          </>
        }
      />

      <section className="plate mb-4 grid grid-cols-1 divide-line sm:grid-cols-3 sm:divide-x">
        <Stat label={who === "ready" ? "Ready referrers" : "Leads found"} value={counts?.total ?? 0} hero hint={scope} />
        <Stat label="From LinkedIn" value={counts?.linkedin ?? 0} hint="public search results, headline names the company" />
        <Stat label="With an address" value={counts?.with_email ?? 0} hint="see the label on each" />
      </section>

      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <Panel>
          <PanelHead
            eyebrow={`${SOURCES.find((s) => s.value === source)?.label ?? "All sources"} · ${scope}`}
            title={`${(data?.total ?? 0).toLocaleString()} ${data?.total === 1 ? "lead" : "leads"}`}
          />
          {leads.loading && !data ? (
            <Loading label="Loading leads" />
          ) : leads.error ? (
            <ErrorNote message={leads.error} onRetry={leads.reload} />
          ) : !data?.items.length ? (
            <Empty
              title={source === "linkedin" ? "No LinkedIn referrers here yet" : "Nobody here yet"}
              hint={
                source === "linkedin"
                  ? "The referral job reads public LinkedIn search results every 15 minutes, Bengaluru first. A person appears once their public headline names the company."
                  : "The referral job works through Bengaluru, then Hyderabad, the rest of India and remote companies every 15 minutes. Try another source or place meanwhile."
              }
            />
          ) : (
            <>
              <ul className="divide-y divide-line">
                {data.items.map((lead) => (
                  <LeadRow key={lead.id} lead={lead} onDraft={draftTo} />
                ))}
              </ul>
              {data.total > PAGE ? (
                <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5 text-xs text-ink-3">
                  <span className="tnum">
                    {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total.toLocaleString()}
                  </span>
                  <span className="flex gap-2">
                    <Button size="sm" variant="quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={offset + PAGE >= data.total}
                      onClick={() => setOffset(offset + PAGE)}
                    >
                      Next
                    </Button>
                  </span>
                </div>
              ) : null}
            </>
          )}
        </Panel>

        {withoutContacts.length ? (
          <Panel className="self-start">
            <PanelHead eyebrow="Not searched yet" title="Good companies, no leads" />
            <ul className="max-h-96 divide-y divide-line overflow-y-auto">
              {withoutContacts.slice(0, 20).map((company) => (
                <li key={company.id} className="flex items-center gap-2 px-4 py-2.5">
                  <Odds score={company.best_score} size="sm" />
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{company.name}</span>
                  <OperatorOnly>
                    <Button size="sm" variant="quiet" onClick={() => findFor(company.id)}>
                      <UserSearch size={12} />
                      Find
                    </Button>
                  </OperatorOnly>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>

      <p className="mt-4 text-xs leading-relaxed text-ink-3">
        <span className="text-ink-2">Ready</span> means the evidence places them at the company now — a public member of
        its GitHub org, their own profile or team page naming it, a commit to its repos in the last year, or (likely
        current) a public LinkedIn headline, a talk or a byline naming it. LinkedIn people come from public search
        results only; no account is ever signed in.{" "}
        <span className="text-ink-2">On the address:</span> verified came from a commit, a public profile or a provider
        that confirmed it; guessed was built from the company&apos;s own address format (two agreeing examples) and an MX
        check, and at most five guessed addresses are sent a day.{" "}
        <Link href="/health" className="underline underline-offset-2 hover:text-ink">
          Referrals this hour
        </Link>
      </p>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}

function LeadRow({ lead, onDraft }: { lead: Lead; onDraft: (id: number) => void }) {
  const name = lead.name ?? (lead.handle ? `@${lead.handle}` : "Name unknown");
  return (
    <li className="flex items-start gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink">{name}</span>
          <AffiliationBadge affiliation={lead.affiliation} evidence={lead.affiliation_evidence} />
          <Badge>{CHANNEL_LABEL[lead.channel] ?? lead.channel}</Badge>
          {lead.is_recruiter ? <Badge>recruiter</Badge> : null}
        </div>
        {lead.role ? <div className="mt-1 line-clamp-2 text-xs text-ink-2">{lead.role}</div> : null}
        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 text-xs text-ink-3">
          {lead.company_star ? <Star size={11} aria-label="On the ★ list" /> : null}
          <span>{lead.company_name ?? "—"}</span>
          <span aria-hidden>·</span>
          <span>{lead.city ?? lead.bucket ?? "—"}</span>
          <span aria-hidden>·</span>
          <span title={lead.found_at ?? undefined}>found {relTime(lead.found_at)}</span>
        </div>
        {lead.affiliation_evidence ? (
          <div className="mt-1.5 border-l-2 border-line pl-2.5 text-xs text-ink-3 [overflow-wrap:anywhere]">
            {lead.affiliation_evidence}
          </div>
        ) : null}
        {lead.email ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span className="tnum truncate text-xs text-ink-2">{lead.email}</span>
            <ConfidenceBadge confidence={lead.confidence} />
          </div>
        ) : (
          <div className="mt-1.5 text-xs text-ink-3">No address yet</div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {lead.linkedin ? (
          <a
            href={lead.linkedin}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-ink-3 underline-offset-2 transition-colors hover:text-ink hover:underline"
            aria-label={`${name} on LinkedIn (opens a new tab)`}
          >
            LinkedIn
            <ExternalLink size={11} />
          </a>
        ) : null}
        {lead.github ? (
          <a
            href={lead.github}
            target="_blank"
            rel="noopener noreferrer"
            className="text-ink-3 transition-colors hover:text-ink"
            aria-label="GitHub profile"
          >
            <GitBranch size={14} />
          </a>
        ) : null}
        {lead.email ? (
          <OperatorOnly>
            <Button size="sm" variant="quiet" onClick={() => onDraft(lead.id)}>
              <Mail size={12} />
              Draft
            </Button>
          </OperatorOnly>
        ) : null}
      </div>
    </li>
  );
}
