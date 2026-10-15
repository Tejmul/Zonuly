"use client";

import { useState } from "react";
import { Ban, Play } from "lucide-react";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { OperatorOnly } from "@/components/access";
import { Button, Empty, ErrorNote, Loading, Panel, PanelHead } from "@/components/ui/primitives";
import { notifyReviewChanged, type Pattern, type Patterns } from "../outreach/_review/review";

/* PATTERNS — "approve the pattern once, not every email" (outreach/patterns.py).
   A pattern is a kind of email (who they are × which market) and the email the user
   approved for it, with his edits; the {{slots}} are filled with each new person's own
   evidence. The switch lives in config.yaml and is shown, never hidden: with it off,
   patterns are kept but every email still waits for him. */

const SLOT_LABEL: Record<string, string> = {
  first_name: "their first name", company: "the company", role: "the role", role_full: "the full role title",
  hook: "the line about them", need: "what the role needs", asks_for: "what the posting asks for",
  proof1: "proof 1", proof2: "proof 2", proof3: "proof 3", topic1: "proof topic",
};

/** The template with its slots set off, so what changes per person is visible at a glance. */
function Template({ text }: { text: string }) {
  const parts = text.split(/(\{\{\w+\}\})/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = /^\{\{(\w+)\}\}$/.exec(p);
        if (!m) return <span key={i}>{p}</span>;
        const key = m[1][0].toLowerCase() + m[1].slice(1);
        return (
          <span key={i} className="rounded-sm border border-line-strong px-1 text-[11px] text-ink" title={`filled per person: ${SLOT_LABEL[key] ?? key}`}>
            {SLOT_LABEL[key] ?? key}
          </span>
        );
      })}
    </>
  );
}

function Row({ p, enabled, onChange }: { p: Pattern; enabled: boolean; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function run(path: string, done: (r: Record<string, number>) => string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<Record<string, number>>(path);
      setMsg(done(r));
      notifyReviewChanged();
      await onChange();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const active = p.status === "active";
  return (
    <li className={`px-4 py-3 ${active ? "" : "opacity-60"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-ink">#{p.id} · {p.label}</span>
        <span className={active ? "chip chip-solid" : "chip chip-strike"}>
          {active ? "active" : p.status === "revoked" ? "revoked" : "replaced by a newer one"}
        </span>
        {p.edited ? <span className="chip text-ink">learned from your edit</span> : null}
        <span className="ml-auto text-xs text-ink-3">
          approved {relTime(p.created_at)}
          {p.revoked_at ? ` · stopped ${relTime(p.revoked_at)}` : ""}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink-2">
        <span className="tnum">{p.auto_approved}</span> auto-approved · <span className="tnum">{p.waiting_to_send}</span> waiting
        to send · <span className="tnum">{p.sent}</span> sent · <span className="tnum">{p.written_for_review}</span> written by it
        and waiting for your review
      </p>
      <details className="mt-1.5 text-xs">
        <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">The template (boxed parts change per person)</summary>
        <div className="mt-1.5 rounded-sm border border-line px-3 py-2">
          <p className="text-sm font-medium text-ink">
            Subject: <Template text={p.subject_template} />
          </p>
          <pre className="mt-1 whitespace-pre-wrap font-sans text-[12px] leading-relaxed text-ink-2">
            <Template text={p.body_template} />
          </pre>
        </div>
      </details>
      {active ? (
        <OperatorOnly>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="danger"
              size="sm"
              disabled={busy}
              onClick={() =>
                run(`/api/outreach/patterns/${p.id}/revoke`, (r) =>
                  `Revoked. ${r.back_to_review ?? 0} unsent email${r.back_to_review === 1 ? "" : "s"} went back to your review.`,
                )
              }
              title="Stop it now: nothing more is approved by it, and what it approved but has not sent goes back to your review"
            >
              <Ban size={13} />
              Revoke
            </Button>
            {enabled && p.drafts_waiting_in_segment > 0 ? (
              <Button
                variant="quiet"
                size="sm"
                disabled={busy}
                onClick={() =>
                  run(`/api/outreach/patterns/${p.id}/apply`, (r) =>
                    `${r.auto_approved ?? 0} approved by the pattern, ${r.left_for_review ?? 0} left for your review (a check did not pass).`,
                  )
                }
                title="Rewrite the drafts already waiting in this kind from the template; approve those that pass every check"
              >
                <Play size={13} />
                Apply to the {p.drafts_waiting_in_segment} draft{p.drafts_waiting_in_segment === 1 ? "" : "s"} waiting
              </Button>
            ) : null}
          </div>
        </OperatorOnly>
      ) : null}
      {msg ? <p className="mt-1 text-xs text-ink">{msg}</p> : null}
    </li>
  );
}

export function PatternsPanel({
  data,
  error,
  loading,
  onChange,
}: {
  data: Patterns | null;
  error: string | null;
  loading: boolean;
  onChange: () => Promise<void>;
}) {
  if (loading) return <Loading label="Loading patterns" />;
  if (error && !data) return <ErrorNote message={error} onRetry={() => void onChange()} />;
  const rows = data?.patterns ?? [];
  return (
    <>
      <div className="mb-4 rounded-sm border border-line bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <p className="font-medium text-ink">Pattern approval is {data?.enabled ? "ON" : "OFF"}.</p>
        {data?.enabled ? (
          <p className="mt-0.5">
            A new draft of a kind you approved is written from your template. If every check passes (the review gate,
            quality {data.min_quality}+, a confirmed-current person, no suppression or cooldown), it is approved for you and
            shows under “Approved, not sent yet” marked with its pattern — undo it there, or revoke the pattern here, any
            time before it sends. At most {data.max_waiting} wait at once. Anything that fails a check waits for you, with
            the reason on it.
          </p>
        ) : (
          <p className="mt-0.5">
            Every email waits for your own approval. Patterns you approve are kept, but nothing is approved for you until
            you set <code className="font-mono">outreach.pattern_approval: true</code> in config.yaml (read live, no
            restart).
          </p>
        )}
        <p className="mt-0.5 text-ink-3">
          To make a pattern: open a draft under “Waiting for your review”, edit it until it reads right, and press “Approve +
          use for every email like this”.
        </p>
      </div>
      <Panel>
        <PanelHead eyebrow="Patterns" title={`${rows.filter((p) => p.status === "active").length} active`} />
        {!rows.length ? (
          <Empty title="No patterns yet" hint="Approve a draft you like with “Approve + use for every email like this”." />
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((p) => (
              <Row key={p.id} p={p} enabled={!!data?.enabled} onChange={onChange} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
