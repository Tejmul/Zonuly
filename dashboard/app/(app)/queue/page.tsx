"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check, Send, Sparkles, X } from "lucide-react";
import { api, useApi, useTaskWatcher, type Email, type Quota } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { ConfidenceBadge } from "@/components/signals";
import { OperatorOnly } from "@/components/access";
import {
  Badge,
  Button,
  Empty,
  ErrorNote,
  Input,
  Loading,
  Panel,
  PanelHead,
  Textarea,
} from "@/components/ui/primitives";

const FLAG_LABEL: Record<string, string> = {
  faithfulness: "unsupported claim", ai_tell: "reads machine-written", length: "length", one_ask: "more than one ask",
  dashes: "dashes", guessed: "guessed address", conflict: "calendar clash",
};

export default function QueuePage() {
  const drafts = useApi<Email[]>("/api/emails?status=draft");
  const approved = useApi<Email[]>("/api/emails?status=approved");
  const { data: quota } = useApi<Quota>("/api/gmail/status");
  const { tasks, watch, dismiss } = useTaskWatcher();

  const [activeId, setActiveId] = useState<number | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const list = drafts.data ?? [];
  const active = list.find((e) => e.id === activeId) ?? list[0] ?? null;

  useEffect(() => {
    if (active) {
      setSubject(active.subject);
      setBody(active.body);
    }
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(kind: "approve" | "reject") {
    if (!active) return;
    setBusy(true);
    setNotice(null);
    try {
      if (kind === "approve") {
        await api.post(`/api/emails/${active.id}/approve`, { subject, body });
      } else {
        await api.post(`/api/emails/${active.id}/reject`);
      }
      setActiveId(null);
      await Promise.all([drafts.reload(), approved.reload()]);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function generateDrafts() {
    // the Atlas's bets: hiring proven, a fresher role, remote-from-anywhere, a lead — one each
    const { task_id } = await api.post<{ task_id: string }>("/api/emails/draft-bets?limit=5");
    watch(task_id, () => void drafts.reload());
  }

  async function sendApproved() {
    setNotice(null);
    const { task_id } = await api.post<{ task_id: string }>("/api/emails/send-approved");
    watch(task_id, () => {
      void approved.reload();
    });
  }

  const dirty = active ? subject !== active.subject || body !== active.body : false;

  return (
    <>
      <PageHead
        title="Review queue"
        description="Nothing is sent until you approve it. Edit freely — what you save is what goes out."
        action={
          <>
            <OperatorOnly>
              <Button variant="quiet" size="sm" onClick={generateDrafts}>
                <Sparkles size={13} />
                Draft 5 more
              </Button>
            </OperatorOnly>
            {(approved.data?.length ?? 0) > 0 ? (
              <OperatorOnly>
                <Button variant="primary" size="sm" onClick={sendApproved}>
                  <Send size={13} />
                  Send {approved.data?.length} approved
                </Button>
              </OperatorOnly>
            ) : null}
          </>
        }
      />

      {notice ? (
        <div className="mb-4 rounded-sm border border-line-strong bg-surface-2/15 px-3 py-2 text-sm text-ink">
          {notice}
        </div>
      ) : null}

      {quota && !quota.gmail?.authorized ? (
        <div className="mb-4 rounded-sm border border-line bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink-2">
          Gmail isn&apos;t connected. You can still draft and approve — approved mail waits in the
          queue until you{" "}
          <Link href="/settings" className="text-ink underline underline-offset-2">
            connect Gmail
          </Link>
          .
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Panel className="max-h-[calc(100vh-12rem)] overflow-y-auto">
          <PanelHead
            eyebrow="Waiting on you"
            title={`${list.length} draft${list.length === 1 ? "" : "s"}`}
          />
          {drafts.loading && !drafts.data ? (
            <Loading label="Loading drafts" />
          ) : drafts.error ? (
            <ErrorNote message={drafts.error} onRetry={drafts.reload} />
          ) : !list.length ? (
            <Empty
              title="Queue is clear"
              hint="Draft referral asks from a job's contact list, or generate a batch for your highest-scoring companies."
              action={
                <OperatorOnly>
                  <Button size="sm" onClick={generateDrafts}>
                    Draft 5 more
                  </Button>
                </OperatorOnly>
              }
            />
          ) : (
            <ul className="divide-y divide-line">
              {list.map((email) => (
                <li key={email.id}>
                  <button
                    onClick={() => setActiveId(email.id)}
                    className={`w-full px-4 py-3 text-left transition-colors hover:bg-surface ${
                      active?.id === email.id ? "bg-surface-2" : ""
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm text-ink">
                        {email.contact_name ?? email.to_email}
                      </span>
                      {email.kind === "followup" ? (
                        <Badge className="border-line-strong/40 text-ink">follow-up</Badge>
                      ) : null}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-ink-3">{email.company_name}</div>
                    <div className="mt-1.5 truncate text-xs text-ink-2">{email.subject}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {active ? (
          <Panel className="flex flex-col">
            <PanelHead
              eyebrow="Editing"
              title={
                <span className="flex flex-wrap items-center gap-2">
                  {active.contact_name ?? "Unknown"}
                  <span className="tnum text-xs font-normal text-ink-3">{active.to_email}</span>
                  <ConfidenceBadge confidence={active.contact_confidence} />
                </span>
              }
              action={
                <span className="text-xs text-ink-3">drafted {relTime(active.created_at)}</span>
              }
            />

            <div className="flex flex-col gap-3 p-4">
              {active.contact_role ? (
                <p className="text-xs leading-relaxed text-ink-3">{active.contact_role}</p>
              ) : null}

              {/* The gate's findings: what a reviewer must see before "yes, send that". */}
              {(active.stale || (active.review_flags?.length ?? 0) > 0) && (
                <div className="rounded-sm border border-line bg-surface px-3 py-2.5">
                  <p className="eyebrow">Read before approving</p>
                  <ul className="mt-1.5 space-y-1 text-xs leading-relaxed text-ink-2">
                    {active.stale && (
                      <li><b className="text-ink">stale</b> — older than the expiry window; re-draft rather than approve, the facts may have changed.</li>
                    )}
                    {(active.review_flags ?? []).map((f, i) => (
                      <li key={i}><b className="text-ink">{FLAG_LABEL[f.kind] ?? f.kind}</b> — {f.detail}</li>
                    ))}
                  </ul>
                </div>
              )}
              {active.evidence && (
                <details className="text-xs text-ink-2">
                  <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
                    The facts this draft was allowed to use
                  </summary>
                  <div className="mt-2 space-y-1.5 leading-relaxed">
                    {active.evidence.person?.evidence && <p><b>Them:</b> {active.evidence.person.evidence}</p>}
                    {active.evidence.person?.hook && <p><b>Hook:</b> {active.evidence.person.hook}</p>}
                    {active.evidence.person?.shared_ground && <p><b>Shared ground:</b> {active.evidence.person.shared_ground}</p>}
                    {active.evidence.company && Object.entries(active.evidence.company).map(([k, v]) => (
                      <p key={k}><b>{k.replace("_", " ")}:</b> {v.text}{v.source ? <span className="text-ink-3"> · {String(v.source).replace(/^https?:\/\//, "").slice(0, 50)}</span> : null}</p>
                    ))}
                    {active.evidence.role?.title && <p><b>Role:</b> {active.evidence.role.title}{active.evidence.role.where ? ` (${active.evidence.role.where})` : ""}</p>}
                  </div>
                </details>
              )}

              <label className="flex flex-col gap-1.5">
                <span className="eyebrow">Subject</span>
                <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="eyebrow">Body</span>
                <Textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={18}
                  className="font-sans"
                />
              </label>

              <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                {/* Shown disabled rather than hidden: the gate every draft has to pass
                    through is the point of the page, and an empty toolbar does not say
                    that a gate exists. */}
                <OperatorOnly
                  fallback={
                    <>
                      <Button variant="primary" disabled title="Read-only demonstration">
                        <Check size={14} />
                        Approve
                      </Button>
                      <Button variant="danger" disabled title="Read-only demonstration">
                        <X size={14} />
                        Reject
                      </Button>
                    </>
                  }
                >
                  <Button variant="primary" onClick={() => act("approve")} disabled={busy}>
                    <Check size={14} />
                    {dirty ? "Save and approve" : "Approve"}
                  </Button>
                  <Button variant="danger" onClick={() => act("reject")} disabled={busy}>
                    <X size={14} />
                    Reject
                  </Button>
                </OperatorOnly>
                {active.job_id ? (
                  <Button asChild variant="quiet" size="sm">
                    <Link href={`/roles/${active.job_id}`}>See the job</Link>
                  </Button>
                ) : null}
                <span className="ml-auto text-xs text-ink-3">
                  {body.trim().split(/\s+/).filter(Boolean).length} words
                </span>
              </div>
            </div>
          </Panel>
        ) : (
          <Panel>
            <Empty
              title="Pick a draft to review"
              hint="Every email here was written against the person's public work and your resume. Read it as if you were the recipient — if a line isn't true, cut it."
            />
          </Panel>
        )}
      </div>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
