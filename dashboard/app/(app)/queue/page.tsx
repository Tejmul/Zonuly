"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Check, Copy, Save, Send, Sparkles, X } from "lucide-react";
import { api, useApi, useTaskWatcher, type Quota } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { AffiliationBadge, ConfidenceBadge } from "@/components/signals";
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
import {
  DraftActions,
  notifyReviewChanged,
  segmentLabel,
  stateOf,
  StateChip,
  type Patterns,
  type ReviewEmail,
} from "../outreach/_review/review";
import { PatternsPanel } from "./patterns";

const FLAG_LABEL: Record<string, string> = {
  faithfulness: "unsupported claim", ai_tell: "reads machine-written", length: "length", one_ask: "more than one ask",
  dashes: "dashes", guessed: "guessed address", conflict: "calendar clash", subject: "subject line",
  readability: "long sentences", blurb: "blurb too long", uncovered_claim: "claims an uncovered need",
  first_mail: "not for a first email", tone: "grovels",
};
const STRUCTURE_LABEL: Record<string, string> = {
  direct_intro: "Who I am · track record · referral ask", advice_first: "Advice first, then the role",
  give_first: "Give first, then the role", fit_question: "Honest fit question",
  proof_opinion: "Proof of work → their opinion", referral_engineer: "Referral ask · engineer",
  founder_direct: "Direct ask · founder", recruiter_direct: "Direct ask · recruiter",
};

/* 2026-10-16: three tabs — what waits for you, what is approved but not sent yet (each
   can be undone, and a pattern's approvals say so), and the patterns themselves.
   `?email=ID` opens that email (the proposal page links here); `?tab=` picks a tab. */
type Tab = "review" | "approved" | "patterns";

/* Evidence fields arrive as plain text from older drafts and as a structured hook
   ({type, anchor, sentence, source, specificity}) from newer ones; show the words either way. */
function asText(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string" || typeof v === "number") return String(v);
  if (typeof v === "object") {
    const o = v as { sentence?: unknown; text?: unknown; anchor?: unknown };
    const t = o.sentence ?? o.text ?? o.anchor;
    if (typeof t === "string") return t;
  }
  return JSON.stringify(v);
}

export default function QueuePage() {
  // useSearchParams needs a Suspense boundary above it, or the whole route renders on the client
  return (
    <Suspense fallback={<Loading label="Loading the review queue" />}>
      <Queue />
    </Suspense>
  );
}

function Queue() {
  const params = useSearchParams();
  const wanted = Number(params.get("email")) || null;
  const drafts = useApi<ReviewEmail[]>("/api/emails?status=draft");
  const approved = useApi<ReviewEmail[]>("/api/emails?status=approved");
  const incomplete = useApi<ReviewEmail[]>("/api/emails?status=incomplete");
  const patterns = useApi<Patterns>("/api/outreach/patterns");
  // /api/gmail/status answers the setup shape: `authorized` at the top, not under `gmail`
  const { data: quota } = useApi<Quota & { authorized?: boolean }>("/api/gmail/status");
  const { tasks, watch, dismiss } = useTaskWatcher();

  const [tab, setTab] = useState<Tab>(
    (["review", "approved", "patterns"] as Tab[]).find((t) => t === params.get("tab")) ?? "review",
  );
  const [activeId, setActiveId] = useState<number | null>(wanted);
  // a link to an email that is already approved opens the Approved tab, once
  const [routed, setRouted] = useState(false);
  if (!routed && wanted && approved.data && drafts.data) {
    setRouted(true);
    if (!drafts.data.some((e) => e.id === wanted) && approved.data.some((e) => e.id === wanted)) setTab("approved");
  }
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeErr, setNoticeErr] = useState(false);

  const list = drafts.data ?? [];
  const active = list.find((e) => e.id === activeId) ?? list[0] ?? null;

  // A different draft selected: load its text into the editor. Adjusted during
  // render against the last id seen rather than in an effect, so the editor never
  // shows the previous draft's text for a frame.
  const [loadedId, setLoadedId] = useState<number | null>(null);
  if (active && active.id !== loadedId) {
    setLoadedId(active.id);
    setSubject(active.subject);
    setBody(active.body);
  }

  async function act(kind: "approve" | "reject" | "save" | "pattern") {
    if (!active) return;
    setBusy(true);
    setNotice(null);
    setNoticeErr(false);
    try {
      if (kind === "approve") {
        await api.post(`/api/emails/${active.id}/approve`, { subject, body });
        setNotice(`Approved: ${active.contact_name ?? active.to_email} — sends in the next slot. Undo it under "Approved, not sent".`);
      } else if (kind === "reject") {
        await api.post(`/api/emails/${active.id}/reject`);
        setNotice(`Rejected: the email to ${active.contact_name ?? active.to_email} will not be sent.`);
      } else if (kind === "save") {
        await api.post(`/api/outreach/emails/${active.id}/save`, { subject, body });
        setNotice("Edit saved. The draft is still waiting for your review.");
      } else {
        await api.post(`/api/outreach/patterns/from-email/${active.id}`, { subject, body });
        setNotice(
          `Approved, and saved as the pattern for ${segmentLabel(active.segment) ?? "this kind of email"}.` +
            (patterns.data?.enabled
              ? " Later drafts of this kind that pass every check will be approved for you."
              : " Pattern approval is off, so later drafts still wait for you — see the Patterns tab."),
        );
      }
      if (kind !== "save") setActiveId(null);
      notifyReviewChanged();
      await Promise.all([drafts.reload(), approved.reload(), patterns.reload()]);
      if (kind === "save") setLoadedId(null);
    } catch (e) {
      // 2026-10-17: a refused click used to show only at the top of the page, out of sight
      // of the button, and the list stayed stale — say why right here and reload
      setNoticeErr(true);
      setNotice(`Not done: ${e instanceof Error ? e.message : String(e)}`);
      await Promise.all([drafts.reload(), approved.reload()]);
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
        description="Nothing is sent until it is approved — by you, or by a pattern you approved (each one can be undone until it sends). Edit freely: what you save is what goes out."
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
        <div role={noticeErr ? "alert" : "status"}
          className={`mb-4 rounded-sm border px-3 py-2 text-sm ${noticeErr ? "border-ink bg-[var(--warn-wash)] font-medium text-[var(--warn-ink)]" : "border-line-strong bg-surface-2/15 text-ink"}`}>
          {notice}
        </div>
      ) : null}

      {quota && !(quota.gmail?.authorized ?? quota.authorized) ? (
        <div className="mb-4 rounded-sm border border-line bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink-2">
          Gmail isn&apos;t connected. You can still draft and approve — approved mail waits in the
          queue until you{" "}
          <Link href="/settings" className="text-ink underline underline-offset-2">
            connect Gmail
          </Link>
          .
        </div>
      ) : null}

      <div role="tablist" aria-label="Review queue" className="mb-4 flex flex-wrap gap-1 border-b border-line">
        {(
          [
            ["review", `Waiting for your review (${list.length})`],
            ["approved", `Approved, not sent yet (${approved.data?.length ?? 0})`],
            ["patterns", `Patterns (${(patterns.data?.patterns ?? []).filter((p) => p.status === "active").length} active)`],
          ] as [Tab, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            role="tab"
            aria-selected={tab === k}
            onClick={() => setTab(k)}
            className={`-mb-px cursor-pointer border-b-2 px-3 py-2 text-[13px] transition-colors ${
              tab === k ? "border-ink font-medium text-ink" : "border-transparent text-ink-2 hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "approved" ? (
        <ApprovedList rows={approved.data ?? []} loading={approved.loading && !approved.data} focus={wanted}
          onChange={async () => { await Promise.all([drafts.reload(), approved.reload(), patterns.reload()]); }} />
      ) : null}

      {tab === "patterns" ? (
        <PatternsPanel data={patterns.data} error={patterns.error} loading={patterns.loading && !patterns.data}
          onChange={async () => { await Promise.all([drafts.reload(), approved.reload(), patterns.reload()]); }} />
      ) : null}

      {tab === "review" ? (
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Panel className="max-h-[calc(100vh-12rem)] overflow-y-auto">
          <PanelHead
            eyebrow="Waiting for your review"
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
                    <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-ink-3">
                      <span className="truncate">{email.company_name}</span>
                      {email.quality != null ? (
                        <span className={email.quality_detail?.verdict === "rewrite" ? "chip chip-strike" : "chip text-ink"}>
                          <span className="tnum">{email.quality}</span>
                          {email.quality_detail?.verdict === "rewrite" ? " · rewrite" : ""}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1.5 truncate text-xs text-ink-2">{email.subject}</div>
                    {email.pattern_id ? (
                      <div className="mt-1 truncate text-[11px] text-ink-3">written from pattern #{email.pattern_id}</div>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {active ? (
          <Panel className="flex flex-col">
            <PanelHead
              eyebrow={`Waiting for your review${active.segment ? ` · ${segmentLabel(active.segment)}` : ""}`}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  {active.contact_name ?? "Unknown"}
                  <span className="tnum text-xs font-normal text-ink-3">{active.to_email}</span>
                  <AffiliationBadge affiliation={active.contact_affiliation} />
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
              {active.pattern_note ? (
                <p className="rounded-sm border border-line px-3 py-2 text-xs leading-relaxed text-ink-2">
                  <b className="text-ink">Pattern:</b> {active.pattern_note}
                </p>
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
              {active.contract ? (
                <div className="rounded-sm border border-line px-3 py-2.5 text-xs leading-relaxed text-ink-2">
                  <p className="eyebrow">
                    {STRUCTURE_LABEL[active.structure ?? ""] ?? active.structure} · quality {active.quality ?? "—"}
                    {active.quality_detail ? ` (${active.quality_detail.verdict})` : ""} · {active.writer}
                    {active.polish_pending ? " · waiting for the model to polish" : ""}
                  </p>
                  <p className="mt-1"><b className="text-ink">Why this structure:</b> {active.contract.structure_why}</p>
                  {active.contract.hook ? (
                    <p><b className="text-ink">Hook ({active.contract.hook.type}):</b> {active.contract.hook.sentence}{" "}
                      <span className="text-ink-3">· {String(active.contract.hook.source).replace(/^https?:\/\//, "").slice(0, 60)}</span></p>
                  ) : null}
                  {active.contract.proofs.slice(0, 3).map((n, i) => (
                    <p key={i}><b className="text-ink">Need → proof:</b> {n.what} → I {n.proof}</p>
                  ))}
                  {active.quality_detail ? (
                    <p className="mt-1 text-ink-3">
                      {Object.entries(active.quality_detail.parts).map(([k, v]) => `${k} ${v.score}/${v.of}`).join(" · ")}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {active.evidence && (
                <details className="text-xs text-ink-2">
                  <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
                    The facts this draft was allowed to use
                  </summary>
                  <div className="mt-2 space-y-1.5 leading-relaxed">
                    {active.evidence.person?.evidence && <p><b>Them:</b> {asText(active.evidence.person.evidence)}</p>}
                    {active.evidence.person?.hook && <p><b>Hook:</b> {asText(active.evidence.person.hook)}</p>}
                    {active.evidence.person?.shared_ground && <p><b>Shared ground:</b> {asText(active.evidence.person.shared_ground)}</p>}
                    {active.evidence.company && Object.entries(active.evidence.company).filter(([, v]) => v != null).map(([k, v]) => (
                      <p key={k}><b>{k.replace("_", " ")}:</b> {asText(v?.text)}{v?.source ? <span className="text-ink-3"> · {String(v.source).replace(/^https?:\/\//, "").slice(0, 50)}</span> : null}</p>
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
                  {dirty ? (
                    <Button variant="quiet" size="sm" onClick={() => act("save")} disabled={busy} title="Keep your edit without approving">
                      <Save size={13} />
                      Save edit
                    </Button>
                  ) : null}
                  {active.kind === "cold" && active.contract ? (
                    <Button
                      variant="quiet"
                      size="sm"
                      onClick={() => act("pattern")}
                      disabled={busy}
                      title={`Approve this email and use it, with your edits, as the template for every ${segmentLabel(active.segment) ?? "email of this kind"}`}
                    >
                      <Copy size={13} />
                      Approve + use for every email like this
                    </Button>
                  ) : null}
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
              {notice ? (
                <p role={noticeErr ? "alert" : "status"}
                  className={`mt-2 text-sm ${noticeErr ? "rounded-sm border border-ink bg-[var(--warn-wash)] px-2 py-1.5 font-medium text-[var(--warn-ink)]" : "text-ink-2"}`}>
                  {notice}
                </p>
              ) : null}
              {active.kind === "cold" && active.contract ? (
                <p className="text-[11px] leading-relaxed text-ink-3">
                  Approve sends this one email in the next slot. “Approve + use for every email like this” also saves it,
                  with your edits, as the template for <b className="text-ink-2">{segmentLabel(active.segment) ?? "this kind of email"}</b>
                  {patterns.data?.enabled
                    ? ": later drafts of this kind that pass every check are approved for you, and each can be undone until it sends."
                    : ". Pattern approval is off (outreach.pattern_approval in config.yaml), so later drafts still wait for you."}
                </p>
              ) : null}
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
      ) : null}

      {tab === "review" && (incomplete.data?.length ?? 0) > 0 ? (
        <Panel className="mt-4">
          <PanelHead
            eyebrow="Not written"
            title={`${incomplete.data?.length} target${incomplete.data?.length === 1 ? "" : "s"} missing evidence`}
          />
          <p className="px-4 pt-2 text-xs text-ink-3">
            The personalisation contract could not be filled, so no generic email was written. The next batch retries
            them when the evidence has grown.
          </p>
          <ul className="divide-y divide-line">
            {(incomplete.data ?? []).map((e) => (
              <li key={e.id} className="px-4 py-2.5 text-xs">
                <span className="text-ink">{e.company_name}</span> · {e.contact_name ?? e.to_email}
                <span className="ml-2 text-ink-3">{(e.review_flags ?? []).map((f) => f.detail).join(" · ")}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}

/** Approved and not sent yet: who approved each (you, or a pattern), and Undo. */
function ApprovedList({
  rows,
  loading,
  focus,
  onChange,
}: {
  rows: ReviewEmail[];
  loading: boolean;
  focus: number | null;
  onChange: () => Promise<void>;
}) {
  if (loading) return <Loading label="Loading approved emails" />;
  if (!rows.length)
    return (
      <Panel>
        <Empty title="Nothing approved and waiting" hint="Approved emails wait here until the send agent takes their slot. Each one can be undone until then." />
      </Panel>
    );
  return (
    <Panel>
      <PanelHead
        eyebrow="Approved, not sent yet"
        title={`${rows.length} email${rows.length === 1 ? "" : "s"} waiting for a send slot`}
      />
      <p className="px-4 pt-2 text-xs text-ink-3">
        The send agent takes these inside the daily cap and the send window. Until it does, Undo approval puts any of them
        back in your review queue.
      </p>
      <ul className="divide-y divide-line">
        {rows.map((e) => {
          const st = stateOf(e);
          return (
            <li key={e.id} className={`px-4 py-3 ${focus === e.id ? "bg-surface-2" : ""}`}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-ink">{e.contact_name ?? e.to_email}</span>
                <span className="text-xs text-ink-3">{e.company_name}</span>
                <StateChip state={st.state} label={e.approved_by === "pattern" ? `Auto-approved by pattern #${e.pattern_id}` : "Approved by you"} />
                <span className="ml-auto text-xs text-ink-3">approved {relTime(e.approved_at)}</span>
              </div>
              <p className="mt-1 text-xs text-ink-2">{e.subject}</p>
              {e.pattern_note ? <p className="mt-0.5 text-[11px] text-ink-3">{e.pattern_note}</p> : null}
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">The email as it will go</summary>
                <pre className="mt-1.5 whitespace-pre-wrap rounded-sm border border-line px-3 py-2 font-sans text-[12px] leading-relaxed text-ink-2">
                  {e.body}
                </pre>
              </details>
              <div className="mt-2">
                <DraftActions compact draft={{ id: e.id, state: st.state, subject: e.subject, body: e.body, segment: e.segment }} onChange={onChange} />
              </div>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
