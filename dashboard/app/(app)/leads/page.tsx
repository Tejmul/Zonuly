"use client";

import Link from "next/link";
import { useState } from "react";
import { GitBranch, Mail, UserSearch } from "lucide-react";
import { api, useApi, useTaskWatcher, type Company, type Contact } from "@/lib/api";
import { OperatorOnly } from "@/components/access";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { ConfidenceBadge, Odds } from "@/components/signals";
import {
  Badge,
  Button,
  Empty,
  ErrorNote,
  Loading,
  Panel,
  PanelHead,
  Select,
} from "@/components/ui/primitives";

/** A hook the extractor never found is stored as the string "null", not as an absent
 *  field, so a plain truthiness check renders the word "null" under someone's name. */
function hookOf(research: { hook?: string | null } | null | undefined): string | null {
  const hook = research?.hook?.trim();
  return hook && hook.toLowerCase() !== "null" ? hook : null;
}

export default function LeadsPage() {
  const [selected, setSelected] = useState<number | null>(null);
  const [confidence, setConfidence] = useState("");

  const companies = useApi<Company[]>("/api/companies");
  const contactParams = new URLSearchParams({ limit: "500" });
  if (selected) contactParams.set("company_id", String(selected));
  if (confidence) contactParams.set("confidence", confidence);
  const contacts = useApi<Contact[]>(`/api/contacts?${contactParams.toString()}`);

  const { tasks, watch, dismiss } = useTaskWatcher();

  async function findFor(companyId: number) {
    const { task_id } = await api.post<{ task_id: string }>(
      `/api/companies/${companyId}/find-contacts`,
    );
    watch(task_id, () => {
      void contacts.reload();
      void companies.reload();
    });
  }

  async function draftTo(contactId: number) {
    const { task_id } = await api.post<{ task_id: string }>("/api/emails/draft", {
      contact_id: contactId,
    });
    watch(task_id);
  }

  const withContacts = (companies.data ?? []).filter((c) => (c.contacts ?? 0) > 0);
  const withoutContacts = (companies.data ?? []).filter(
    (c) => (c.contacts ?? 0) === 0 && (c.best_score ?? 0) >= 50,
  );

  return (
    <>
      <PageHead
        title="Leads"
        description="People you could ask for a referral, ranked by how sure we are the address is real."
        action={
          <Select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
            <option value="">All confidence levels</option>
            <option value="verified">Verified only</option>
            <option value="pattern-guessed">Pattern-guessed</option>
            <option value="scraped">Scraped</option>
          </Select>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <div className="flex flex-col gap-4">
          <Panel>
            <PanelHead eyebrow="Filter" title="Companies with contacts" />
            {companies.loading && !companies.data ? (
              <Loading label="Loading companies" />
            ) : (
              <ul className="max-h-96 overflow-y-auto">
                <li>
                  <button
                    onClick={() => setSelected(null)}
                    className={`flex w-full items-center justify-between px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface ${
                      selected === null ? "bg-surface-2 text-ink" : "text-ink-2"
                    }`}
                  >
                    Everyone
                    <span className="tnum text-xs text-ink-3">
                      {withContacts.reduce((n, c) => n + (c.contacts ?? 0), 0)}
                    </span>
                  </button>
                </li>
                {withContacts.map((company) => (
                  <li key={company.id}>
                    <button
                      onClick={() => setSelected(company.id)}
                      className={`flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface ${
                        selected === company.id ? "bg-surface-2 text-ink" : "text-ink-2"
                      }`}
                    >
                      <span className="truncate">{company.name}</span>
                      <span className="tnum shrink-0 text-xs text-ink-3">{company.contacts}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {withoutContacts.length ? (
            <Panel>
              <PanelHead
                eyebrow="Not searched yet"
                title="Good companies, no contacts"
              />
              <ul className="max-h-80 overflow-y-auto divide-y divide-line">
                {withoutContacts.slice(0, 20).map((company) => (
                  <li key={company.id} className="flex items-center gap-2 px-4 py-2.5">
                    <Odds score={company.best_score} size="sm" />
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">
                      {company.name}
                    </span>
                    <Button size="sm" variant="quiet" onClick={() => findFor(company.id)}>
                      <UserSearch size={12} />
                      Find
                    </Button>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>

        <Panel>
          <PanelHead
            eyebrow={selected ? "One company" : "All companies"}
            title={`${contacts.data?.length ?? 0} contacts`}
          />
          {contacts.loading && !contacts.data ? (
            <Loading label="Loading contacts" />
          ) : contacts.error ? (
            <ErrorNote message={contacts.error} onRetry={contacts.reload} />
          ) : !contacts.data?.length ? (
            <Empty
              title="No contacts here yet"
              hint="Pick a company on the left and run Find. GitHub commit metadata is the best free source of real engineer emails, so companies with active public repos give the most."
            />
          ) : (
            <ul className="divide-y divide-line">
              {contacts.data.map((contact) => (
                <li key={contact.id} className="flex items-start gap-4 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm text-ink">
                        {contact.name ?? "Name unknown"}
                      </span>
                      <ConfidenceBadge confidence={contact.confidence} />
                      <Badge>{contact.source}</Badge>
                      {contact.is_recruiter ? (
                        <Badge className="border-line-strong/40 text-ink">recruiter</Badge>
                      ) : null}
                    </div>
                    <div className="tnum mt-1 truncate text-xs text-ink-2">{contact.email}</div>
                    {contact.role ? (
                      <div className="mt-1 line-clamp-2 text-xs text-ink-3">{contact.role}</div>
                    ) : null}
                    {hookOf(contact.research) ? (
                      <div className="mt-2 border-l-2 border-line pl-2.5 text-xs italic text-ink-3">
                        {hookOf(contact.research)}
                      </div>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="hidden text-xs text-ink-3 sm:block">
                      {contact.company_name}
                    </span>
                    {contact.github ? (
                      <a
                        href={contact.github}
                        target="_blank"
                        rel="noreferrer"
                        className="text-ink-3 transition-colors hover:text-ink"
                        aria-label="GitHub profile"
                      >
                        <GitBranch size={14} />
                      </a>
                    ) : null}
                    <OperatorOnly>
                      <Button size="sm" variant="quiet" onClick={() => draftTo(contact.id)}>
                        <Mail size={12} />
                        Draft
                      </Button>
                    </OperatorOnly>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-ink-3">
        <span className="text-ink-2">On confidence:</span> verified means the address came from a
        real commit, a public profile, or a provider that confirmed it. Pattern-guessed means it was
        built from the company&apos;s email pattern and passed an MX check but nothing stronger —
        many mail providers accept every address at the edge, so treat those as likely, not certain.{" "}
        <Link href="/settings" className="underline underline-offset-2 hover:text-ink">
          Add a GitHub token
        </Link>{" "}
        to raise the API limit from 60 to 5,000 an hour.
      </p>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
