"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, Copy, Pencil, RotateCcw, Undo2, X } from "lucide-react";
import { api, type Email } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { OperatorOnly } from "@/components/access";
import { Button, Input, Textarea } from "@/components/ui/primitives";

/* The review flow, shared by the proposal page and the queue (2026-10-16). The user
   could not tell whether a proposal had become a draft, whether it was approved, or where
   to approve it. So every email carries its state in plain words, the actions sit next to
   it, and a pattern (outreach/patterns.py) can be approved from any draft he likes.
   `_review` is a private folder: the router never serves it as a page. */

export type ReviewEmail = Email & {
  segment?: string | null;
  pattern_id?: number | null;
  approved_by?: string | null;
  pattern_note?: string | null;
};

/** The draft behind a proposal, as proposals.draft_state() returns it. */
export type DraftState = {
  state: "none" | "draft" | "approved" | "approved_pattern" | "sending" | "sent" | "replied" | "rejected"
    | "incomplete" | "failed" | "stale";
  label: string;
  id?: number;
  status?: string;
  subject?: string;
  body?: string;
  approved_by?: string | null;
  pattern_id?: number | null;
  pattern_note?: string | null;
  segment?: string | null;
  segment_label?: string | null;
  quality?: number | null;
  review_flags?: { kind: string; detail: string }[];
  stale?: boolean;
  created_at?: string | null;
  approved_at?: string | null;
  sent_at?: string | null;
};

export type Pattern = {
  id: number;
  segment: string;
  label: string;
  status: "active" | "revoked" | "replaced";
  subject_template: string;
  body_template: string;
  slots: string[];
  source_email_id: number | null;
  edited: boolean;
  created_at: string | null;
  revoked_at: string | null;
  auto_approved: number;
  waiting_to_send: number;
  sent: number;
  written_for_review: number;
  drafts_waiting_in_segment: number;
};

export type Patterns = { enabled: boolean; min_quality: number; max_waiting: number; patterns: Pattern[] };
export type ReviewCount = { waiting: number; approved_unsent: number; auto_approved_unsent: number; pattern_approval: boolean };

const STRUCTURE = {
  proof_opinion: "Proof of work → their opinion (small team)",
  referral_engineer: "Referral ask to an engineer",
  founder_direct: "Direct ask to a founder",
  recruiter_direct: "Direct ask to a recruiter",
} as Record<string, string>;
const MARKET = { india: "India", remote: "Remote", abroad: "Abroad (on-site)" } as Record<string, string>;

/** "referral_engineer:india" -> "Referral ask to an engineer · India" (patterns.segment_label). */
export function segmentLabel(seg: string | null | undefined) {
  if (!seg) return null;
  const [st, mk] = seg.split(":");
  return `${STRUCTURE[st] ?? st} · ${MARKET[mk] ?? mk}`;
}

/** An email's state in the words the proposal page uses (proposals.STATE_LABEL). */
export function stateOf(e: ReviewEmail): { state: DraftState["state"]; label: string } {
  if (e.status === "draft" && e.stale) return { state: "stale", label: "Too old to approve — write it again" };
  if (e.status === "approved" && e.approved_by === "pattern")
    return { state: "approved_pattern", label: "Auto-approved by a pattern — sends in the next slot unless you undo it" };
  const words: Record<string, string> = {
    draft: "Waiting for your review", approved: "Approved — sends in the next slot", sending: "Sending now",
    sent: "Sent", replied: "Sent — they replied", rejected: "Rejected — will not be sent",
    incomplete: "Not written — evidence missing", failed: "Send failed",
  };
  return { state: (e.status as DraftState["state"]) ?? "draft", label: words[e.status] ?? e.status };
}

/** Black and white: the state is a chip; solid = it will go, struck = it will not. */
export function StateChip({ state, label }: { state: DraftState["state"]; label: string }) {
  const cls =
    state === "approved" || state === "approved_pattern" || state === "sending" || state === "sent" || state === "replied"
      ? "chip chip-solid"
      : state === "rejected" || state === "incomplete" || state === "failed" || state === "stale"
        ? "chip chip-strike"
        : "chip text-ink";
  return <span className={cls}>{label}</span>;
}

/** Tell the nav's "waiting for you" count to refresh now rather than on its next poll. */
export const REVIEW_CHANGED = "zonuly:review-changed";
export function notifyReviewChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(REVIEW_CHANGED));
}

function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

/** One draft's actions, in place: Approve, Edit (saved), Reject, Approve as a pattern,
 *  Undo an approval. `onChange` reloads whatever list the caller shows. */
export function DraftActions({
  draft,
  onChange,
  compact = false,
}: {
  draft: { id: number; state: DraftState["state"]; subject: string; body: string; segment?: string | null };
  onChange: () => void | Promise<void>;
  compact?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function startEdit() {
    setSubject(draft.subject);
    setBody(draft.body);
    setEditing(true);
  }

  async function run(fn: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setEditing(false);
      if (done) setMsg(done);
      notifyReviewChanged();
      await onChange();
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const edits = editing ? { subject, body } : {};
  const approve = () => run(() => api.post(`/api/emails/${draft.id}/approve`, edits));
  const reject = () => run(() => api.post(`/api/emails/${draft.id}/reject`));
  const save = () => run(() => api.post(`/api/outreach/emails/${draft.id}/save`, { subject, body }), "Saved. Still waiting for your review.");
  const undo = () => run(() => api.post(`/api/outreach/emails/${draft.id}/unapprove`), "Back in your review queue.");
  const asPattern = () =>
    run(
      () => api.post(`/api/outreach/patterns/from-email/${draft.id}`, edits),
      "Approved, and saved as the pattern for this kind of email. See Queue → Patterns.",
    );

  const reviewable = draft.state === "draft" || draft.state === "rejected";
  const seg = segmentLabel(draft.segment);

  return (
    <div className="flex flex-col gap-2">
      {editing ? (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1">
            <span className="eyebrow">Subject</span>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="eyebrow">Body</span>
            <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={compact ? 12 : 16} className="font-sans" />
          </label>
        </div>
      ) : null}
      <OperatorOnly>
        <div className="flex flex-wrap items-center gap-2">
          {reviewable ? (
            <>
              <Button variant="primary" size="sm" onClick={approve} disabled={busy} title="Approve this one email: it goes out in the next send slot">
                <Check size={13} />
                {editing ? "Save and approve" : "Approve"}
              </Button>
              {editing ? (
                <>
                  <Button size="sm" onClick={save} disabled={busy} title="Keep your edit; the draft keeps waiting for your review">
                    Save edit
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button variant="quiet" size="sm" onClick={startEdit} disabled={busy}>
                  <Pencil size={13} />
                  Edit
                </Button>
              )}
              {draft.state === "draft" ? (
                <Button variant="danger" size="sm" onClick={reject} disabled={busy} title="It will not be sent">
                  <X size={13} />
                  Reject
                </Button>
              ) : null}
              <Button
                variant="quiet"
                size="sm"
                onClick={asPattern}
                disabled={busy}
                title={`Approve this email and use it (with your edits) as the template for every ${seg ?? "email of this kind"}`}
              >
                <Copy size={13} />
                Approve + use for every email like this
              </Button>
            </>
          ) : null}
          {draft.state === "approved" || draft.state === "approved_pattern" ? (
            <Button variant="quiet" size="sm" onClick={undo} disabled={busy} title="Back to your review queue; it will not send">
              <Undo2 size={13} />
              Undo approval
            </Button>
          ) : null}
          {draft.state === "stale" ? (
            <Button variant="danger" size="sm" onClick={reject} disabled={busy} title="Reject the old draft so a fresh one can be written">
              <RotateCcw size={13} />
              Discard old draft
            </Button>
          ) : null}
        </div>
      </OperatorOnly>
      {reviewable && seg && !compact ? (
        <p className="text-[11px] leading-relaxed text-ink-3">
          “Approve + use for every email like this” saves this email, with your edits, as the template for{" "}
          <b className="text-ink-2">{seg}</b>. With pattern approval switched on, later drafts of this kind that pass every
          check are approved for you, marked as such, and can be undone until they send.
        </p>
      ) : null}
      {msg ? <p className="text-xs text-ink">{msg}</p> : null}
    </div>
  );
}

/** The state line and actions for the draft behind one proposal. */
export function ProposalDraft({
  proposalId,
  draft,
  proposalBody,
  onChange,
}: {
  proposalId: number;
  draft: DraftState | undefined;
  proposalBody?: string | null;
  onChange: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const d = draft ?? { state: "none" as const, label: "No draft yet — this is only a proposal" };

  async function write() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<{ error?: string }>(`/api/outreach/proposals/${proposalId}/draft`);
      if (r?.error) setMsg(r.error);
      notifyReviewChanged();
      await onChange();
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-b border-line bg-surface-2/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="eyebrow">Draft</span>
        <StateChip state={d.state} label={d.label} />
        {d.id ? (
          <Link href={`/queue?email=${d.id}`} className="text-ink-3 underline underline-offset-2 hover:text-ink">
            open in the review queue
          </Link>
        ) : null}
        {d.state === "sent" || d.state === "replied" ? (
          <Link href="/tracker" className="text-ink-3 underline underline-offset-2 hover:text-ink">
            see it in the tracker
          </Link>
        ) : null}
        {d.approved_at && (d.state === "approved" || d.state === "approved_pattern") ? (
          <span className="text-ink-3">approved {relTime(d.approved_at)}</span>
        ) : d.sent_at ? (
          <span className="text-ink-3">sent {relTime(d.sent_at)}</span>
        ) : d.created_at ? (
          <span className="text-ink-3">drafted {relTime(d.created_at)}</span>
        ) : null}
      </div>
      {d.pattern_note ? <p className="mt-1 text-xs text-ink-2">{d.pattern_note}</p> : null}

      {d.state === "none" || d.state === "rejected" || d.state === "incomplete" ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <OperatorOnly>
            <Button variant={d.state === "none" ? "primary" : "quiet"} size="sm" onClick={write} disabled={busy}>
              {busy ? "Writing…" : d.state === "none" ? "Write draft" : "Write a new draft"}
            </Button>
          </OperatorOnly>
          <span className="text-xs text-ink-3">
            {d.state === "none"
              ? "Puts this exact email in your review queue. Nothing is sent until you approve it."
              : d.state === "rejected"
                ? "You rejected the last draft. You can still approve it below, or write a new one."
                : "The last attempt had too little evidence."}
          </span>
        </div>
      ) : null}

      {d.id && d.state !== "none" && d.state !== "incomplete" && d.state !== "sent" && d.state !== "replied" && d.state !== "sending" ? (
        <div className="mt-2">
          {d.body && proposalBody && d.body.trim() !== proposalBody.trim() ? (
            <details className="mb-2 text-xs">
              <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
                The draft differs from the proposal below — show the draft as it stands
              </summary>
              <div className="mt-1.5 rounded-sm border border-line px-3 py-2">
                <p className="text-sm font-medium text-ink">Subject: {d.subject}</p>
                <pre className="mt-1 whitespace-pre-wrap font-sans text-[12px] leading-relaxed text-ink-2">{d.body}</pre>
              </div>
            </details>
          ) : null}
          <DraftActions
            key={`${d.id}:${d.status}`}
            draft={{ id: d.id, state: d.state, subject: d.subject ?? "", body: d.body ?? "", segment: d.segment }}
            onChange={onChange}
          />
        </div>
      ) : null}
      {msg ? <p className="mt-1 text-xs text-ink">{msg}</p> : null}
    </div>
  );
}
