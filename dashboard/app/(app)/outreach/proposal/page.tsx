"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, useApi, useTaskWatcher, type Proposal, type Proposals } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { OperatorOnly } from "@/components/access";
import { Button, Empty, ErrorNote, Loading, Panel, PanelHead, Select } from "@/components/ui/primitives";
import { ProposalDraft, type DraftState } from "../_review/review";

/* PROPOSAL — how the pipeline would write to the best targets, before anything is drafted.
   Proposals are not drafts: nothing on this page is in the review queue or can be sent.
   Each one shows the evidence used, the structure and why, the subject options, the email
   exactly as it would go, the quality score's parts, the gate's flags, and what was
   deliberately left out. Black and white: weight and rules carry the hierarchy.
   2026-10-16: each proposal also says whether it has become a draft and what state that
   draft is in, with the actions next to it — Write draft, Approve, Edit, Reject, Undo —
   so the page answers "is it approved or not, and where do I approve it?". */

type ProposalWithDraft = Proposal & { draft?: DraftState };

const STRUCTURE_LABEL: Record<string, string> = {
  direct_intro: "Who I am · track record · referral ask",
  advice_first: "Advice first, then the role",
  give_first: "Give first, then the role",
  fit_question: "Honest fit question",
  proof_opinion: "Proof of work → their opinion",
  referral_engineer: "Referral ask · engineer",
  founder_direct: "Direct ask · founder",
  recruiter_direct: "Direct ask · recruiter",
};
const HOOK_LABEL: Record<string, string> = {
  person_post: "their post", shared_ground: "shared ground", person_commit: "their commits",
  person_repo: "their repo", person_bio: "their bio", company_launch: "company launch",
  company_funding: "company round", company_product: "company's own description",
};
const PART_LABEL: Record<string, string> = {
  personalisation: "Personalisation", hook: "Hook specificity", length: "Length", one_ask: "One ask",
  readability: "Readability", subject: "Subject", faithfulness: "Faithfulness",
};

function host(url: string | null | undefined) {
  if (!url) return null;
  return String(url).replace(/^https?:\/\//, "").replace(/\/$/, "").slice(0, 60);
}

function Source({ url }: { url: string | null | undefined }) {
  if (!url) return null;
  return /^https?:\/\//.test(url) ? (
    <a href={url} target="_blank" rel="noreferrer" className="text-ink-3 underline underline-offset-2 hover:text-ink">
      {host(url)}
    </a>
  ) : (
    <span className="text-ink-3">{url}</span>
  );
}

function Score({ q }: { q: Proposal["quality"] }) {
  if (!q) return null;
  return (
    <span className={q.verdict === "good" ? "chip chip-solid" : q.verdict === "rewrite" ? "chip chip-strike" : "chip text-ink"}>
      <span className="tnum">{q.score}</span>&nbsp;· {q.verdict}
    </span>
  );
}

function Card({ p, onChange }: { p: ProposalWithDraft; onChange: () => void | Promise<void> }) {
  const [showAlt, setShowAlt] = useState(false);
  return (
    <Panel className="mb-4">
      <PanelHead
        eyebrow={`#${p.rank} · ${p.market} · ${STRUCTURE_LABEL[p.structure.id] ?? p.structure.id}`}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {p.company.name}
            <span className="text-xs font-normal text-ink-3">
              to {p.recipient.name ?? "?"}
              {p.recipient.role ? `, ${p.recipient.role}` : ""}
            </span>
          </span>
        }
        action={p.complete ? <Score q={p.quality} /> : <span className="chip chip-strike">not written</span>}
      />
      {p.complete ? <ProposalDraft proposalId={p.id} draft={p.draft} proposalBody={p.body} onChange={onChange} /> : null}

      {!p.complete ? (
        <div className="px-4 py-3 text-sm text-ink-2">
          <p className="eyebrow">The contract could not be filled, so no email was written</p>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs">
            {p.missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-3">
            A generic email is what this refuses to send. When the evidence grows (a hook, a needs brief, a real
            name), the next run writes it.
          </p>
        </div>
      ) : (
        <div className="grid gap-0 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          {/* ---- evidence */}
          <div className="space-y-4 border-b border-line px-4 py-4 text-xs leading-relaxed lg:border-b-0 lg:border-r">
            <section>
              <p className="eyebrow">Structure</p>
              <p className="mt-1 text-ink">{STRUCTURE_LABEL[p.structure.id] ?? p.structure.id}</p>
              <p className="mt-0.5 text-ink-2">{p.structure.chosen_because}</p>
            </section>
            <section>
              <p className="eyebrow">Recipient</p>
              <p className="mt-1 text-ink-2">
                {p.recipient.name} · {p.recipient.role ?? p.recipient.role_class} · address {p.recipient.address}
                {p.recipient.affiliation ? ` · ${p.recipient.affiliation}` : ""}
              </p>
              {p.recipient.affiliation_evidence ? (
                <p className="mt-0.5 text-ink-3">Works there now: {p.recipient.affiliation_evidence}</p>
              ) : null}
            </section>
            <section>
              <p className="eyebrow">Hook · {HOOK_LABEL[p.hook?.type ?? ""] ?? p.hook?.type}</p>
              <p className="mt-1 text-ink">{p.hook?.sentence}</p>
              <p className="mt-0.5">
                Source: <Source url={p.hook?.source} />
              </p>
              {p.hook_alternatives && p.hook_alternatives.length > 0 ? (
                <button className="mt-1 text-ink-3 underline underline-offset-2 hover:text-ink" onClick={() => setShowAlt((v) => !v)}>
                  {showAlt ? "hide" : "show"} {p.hook_alternatives.length} other hook{p.hook_alternatives.length === 1 ? "" : "s"} on record
                </button>
              ) : null}
              {showAlt ? (
                <ul className="mt-1 space-y-1 text-ink-3">
                  {(p.hook_alternatives ?? []).map((h, i) => (
                    <li key={i}>
                      {HOOK_LABEL[h.type] ?? h.type}: {h.sentence}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
            <section>
              <p className="eyebrow">What they need → the resume line that covers it</p>
              <ul className="mt-1 space-y-2.5">
                {(p.needs ?? []).map((n, i) => (
                  <li key={i}>
                    <p className="text-ink">
                      {n.what} <span className="text-ink-3">({n.status}, {n.kind})</span>
                    </p>
                    {n.quote ? <p className="text-ink-3">“{n.quote.slice(0, 220)}” <Source url={n.url} /></p> : null}
                    <p className="mt-0.5 text-ink-2">→ I {n.proof}</p>
                    <p className="text-ink-3">resume: {n.proof_source}</p>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <p className="eyebrow">Deliberately not claimed</p>
              {(p.not_claimed ?? []).length ? (
                <ul className="mt-1 space-y-1 text-ink-2">
                  {(p.not_claimed ?? []).map((n, i) => (
                    <li key={i}>
                      <span className="text-ink-3">{n.kind}:</span> {n.what}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-ink-3">Nothing the role asks for is missing from the resume.</p>
              )}
            </section>
          </div>

          {/* ---- the email */}
          <div className="space-y-4 px-4 py-4">
            <section>
              <p className="eyebrow">Subject options</p>
              <ul className="mt-1 space-y-0.5 text-sm">
                {(p.subjects ?? []).map((s) => (
                  <li key={s.pattern} className={s.subject === p.subject ? "font-medium text-ink" : "text-ink-3"}>
                    {s.subject === p.subject ? "● " : "○ "}
                    {s.subject} <span className="text-xs text-ink-3">({s.pattern})</span>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <p className="eyebrow">
                The email exactly as it would be sent · {p.words} words in the message · written by {p.writer}
              </p>
              <div className="mt-1.5 rounded-sm border border-line bg-surface px-3 py-3">
                <p className="text-xs text-ink-3">To: {p.recipient.email}</p>
                <p className="text-sm font-medium text-ink">Subject: {p.subject}</p>
                <pre className="mt-2 whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">{p.body}</pre>
              </div>
              {p.model_note ? <p className="mt-1 text-xs text-ink-3">Model: {p.model_note}</p> : null}
            </section>
            <section className="grid gap-4 md:grid-cols-2">
              <div>
                <p className="eyebrow">Quality {p.quality?.score} / 100</p>
                <table className="mt-1 w-full text-xs">
                  <tbody>
                    {Object.entries(p.quality?.parts ?? {}).map(([k, v]) => (
                      <tr key={k} className="border-b border-line last:border-0">
                        <td className="py-1 pr-2 text-ink-2">{PART_LABEL[k] ?? k}</td>
                        <td className="tnum py-1 pr-2 text-right">
                          {v.score}/{v.of}
                        </td>
                        <td className="py-1 text-ink-3">{v.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <p className="eyebrow">Gate flags</p>
                {(p.flags ?? []).length ? (
                  <ul className="mt-1 space-y-1 text-xs text-ink-2">
                    {(p.flags ?? []).map((f, i) => (
                      <li key={i}>
                        <b className="text-ink">{f.kind}</b>: {f.detail}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-ink-3">None.</p>
                )}
              </div>
            </section>
            {p.followup ? (
              <details className="text-xs">
                <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
                  The follow-up after {p.followup.after_days} silent days{p.followup.new_value ? " (adds a second proof)" : ""}
                </summary>
                <pre className="mt-1.5 whitespace-pre-wrap rounded-sm border border-line px-3 py-2 font-sans text-[12px] leading-relaxed text-ink-2">
                  {p.followup.body}
                </pre>
              </details>
            ) : null}
          </div>
        </div>
      )}
    </Panel>
  );
}

export default function ProposalPage() {
  const { data, error, loading, reload } = useApi<Proposals>("/api/outreach/proposals");
  const { tasks, watch, dismiss } = useTaskWatcher();
  const [structure, setStructure] = useState("");

  async function regenerate() {
    const { task_id } = await api.post<{ task_id: string }>("/api/outreach/proposals", { n: 10, use_model: true });
    watch(task_id, () => void reload());
  }

  const all = (data?.items ?? []) as ProposalWithDraft[];
  const items = all.filter((p) => !structure || p.structure.id === structure);
  const s = data?.summary;
  const byState = (...st: DraftState["state"][]) => all.filter((p) => p.complete && st.includes(p.draft?.state ?? "none")).length;

  return (
    <>
      <PageHead
        title="Email proposal"
        description="How each email would be written. A proposal is not a draft: press Write draft to put it in your review queue, then approve, edit or reject it here or in Queue. Nothing is sent until you approve it."
        action={
          <>
            <Select value={structure} onChange={(e) => setStructure(e.target.value)}>
              <option value="">All structures</option>
              {Object.entries(STRUCTURE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
            <OperatorOnly>
              <Button variant="quiet" size="sm" onClick={regenerate}>
                <RefreshCw size={13} />
                Regenerate
              </Button>
            </OperatorOnly>
          </>
        }
      />

      {s ? (
        <p className="mb-4 text-xs text-ink-2">
          <span className="tnum">{s.complete}</span> written · <span className="tnum">{s.incomplete}</span> refused (evidence
          missing) · mean quality <span className="tnum">{s.mean_quality ?? "—"}</span> ·{" "}
          {Object.entries(s.structures)
            .map(([k, v]) => `${STRUCTURE_LABEL[k] ?? k} ${v}`)
            .join(" · ")}{" "}
          · generated {relTime(data?.created_at ?? null)}
        </p>
      ) : null}
      {s ? (
        <p className="mb-4 text-xs text-ink-2">
          Drafts: <span className="tnum">{byState("none")}</span> not written yet ·{" "}
          <span className="tnum font-medium text-ink">{byState("draft", "stale")}</span> waiting for your review ·{" "}
          <span className="tnum">{byState("approved", "approved_pattern", "sending")}</span> approved, not sent ·{" "}
          <span className="tnum">{byState("sent", "replied")}</span> sent ·{" "}
          <span className="tnum">{byState("rejected")}</span> rejected
        </p>
      ) : null}

      {loading && !data ? (
        <Loading label="Loading proposals" />
      ) : error ? (
        <ErrorNote message={error} onRetry={reload} />
      ) : !items.length ? (
        <Panel>
          <Empty
            title="No proposals yet"
            hint="Run `python scripts/run.py outreach proposal`, or Regenerate. It writes ~10 proposals across the structures and never drafts or sends."
          />
        </Panel>
      ) : (
        items.map((p) => <Card key={p.id} p={p} onChange={reload} />)
      )}

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
