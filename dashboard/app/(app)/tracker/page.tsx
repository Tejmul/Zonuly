"use client";

import Link from "next/link";
import { useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { api, useApi, useTaskWatcher, type Thread, type Track, type TrackRow } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { SentimentBadge } from "@/components/signals";
import { OperatorOnly } from "@/components/access";
import { Learn } from "@/components/learn";
import { Button, Empty, ErrorNote, Loading, Panel, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";

/* TRACKER — every cold email and where it stands:
   Draft → Approved → Sent → No reply yet (days waiting, follow-up due) →
   Replied (positive / neutral / negative / closed) · Bounced · Opted out.
   A row opens the whole thread: our email exactly as sent, the follow-ups, their replies
   with the sentiment and the classifier's reason. */

const STAGE_LABEL: Record<string, string> = {
  incomplete: "Not written",
  draft: "Draft",
  approved: "Approved",
  sending: "Sending",
  waiting: "No reply yet",
  replied: "Replied",
  bounced: "Bounced",
  opted_out: "Opted out",
  rejected: "Rejected",
  failed: "Failed",
};
const STAGE_CHIP: Record<string, string> = {
  replied: "chip chip-solid",
  waiting: "chip text-ink",
  approved: "chip text-ink",
  bounced: "chip chip-strike",
  opted_out: "chip chip-strike",
  rejected: "chip chip-strike",
  failed: "chip chip-strike",
};

const COUNT_TILES: [string, string][] = [
  ["draft", "Drafts"],
  ["approved", "Approved"],
  ["sent_total", "Sent"],
  ["waiting", "No reply yet"],
  ["reply_positive", "Positive"],
  ["reply_neutral", "Neutral"],
  ["reply_negative", "Negative"],
  ["reply_closed", "Closed"],
  ["bounced", "Bounced"],
  ["opted_out", "Opted out"],
];

function StageCell({ r }: { r: TrackRow }) {
  return (
    <div className="space-y-0.5">
      <span className={STAGE_CHIP[r.stage] ?? "chip text-ink-3"}>{STAGE_LABEL[r.stage] ?? r.stage}</span>
      {r.stage === "waiting" ? (
        <p className="text-[11px] text-ink-3">
          {r.days_waiting ?? 0}d waiting{r.followup_due ? ` · follow-up due ${r.followup_due}` : r.followups ? " · followed up" : ""}
        </p>
      ) : null}
    </div>
  );
}

function ThreadView({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, error, loading } = useApi<Thread>(`/api/outreach/thread/${id}`);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <aside
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-line bg-paper px-5 py-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Thread</p>
            <h2 className="text-base font-semibold text-ink">
              {data?.contact_name ?? "…"} · {data?.company_name ?? ""}
            </h2>
            {data ? (
              <p className="text-xs text-ink-3">
                {data.to} · {data.job_title ?? "no role"}
                {data.structure ? ` · ${data.structure}` : ""}
                {data.quality ? ` · quality ${data.quality.score}` : ""}
                {data.writer ? ` · ${data.writer}` : ""}
              </p>
            ) : null}
          </div>
          <Button variant="quiet" size="sm" onClick={onClose} aria-label="Close">
            <X size={14} />
          </Button>
        </div>
        {loading && !data ? (
          <Loading label="Loading thread" />
        ) : error ? (
          <ErrorNote message={error} />
        ) : data ? (
          <>
            <p className="mb-3 text-xs text-ink-2">
              Now: <b className="text-ink">{STAGE_LABEL[data.lifecycle.stage] ?? data.lifecycle.stage}</b>
              {data.lifecycle.sentiment ? ` · ${data.lifecycle.sentiment}` : ""}
              {data.lifecycle.days_waiting != null ? ` · ${data.lifecycle.days_waiting} days waiting` : ""}
              {data.lifecycle.followup_due ? ` · follow-up due ${data.lifecycle.followup_due}` : ""}
            </p>
            <ol className="space-y-3">
              {data.messages.map((m, i) => (
                <li
                  key={i}
                  className={`rounded-sm border px-3 py-2.5 ${m.type === "theirs" ? "border-line-strong" : "border-line bg-surface"}`}
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-ink-3">
                    {m.type === "ours" ? (
                      <>
                        <b className="text-ink">{m.kind === "followup" ? "Our follow-up" : "Our email"}</b>
                        <span>{m.sent ? `sent ${relTime(m.at)}` : `${m.status} · ${relTime(m.at)}`}</span>
                      </>
                    ) : (
                      <>
                        <b className="text-ink">Reply from {m.from}</b>
                        <SentimentBadge sentiment={m.sentiment} />
                        <span>{relTime(m.at)}</span>
                      </>
                    )}
                  </div>
                  {m.subject ? <p className="text-sm font-medium text-ink">Subject: {m.subject}</p> : null}
                  <pre className="mt-1 whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">{m.body}</pre>
                  {m.type === "theirs" && (m.reason || m.next) ? (
                    <p className="mt-2 border-t border-line pt-1.5 text-xs text-ink-2">
                      {m.reason ? <>Why {m.sentiment}: {m.reason}. </> : null}
                      {m.next ? <>Next: {m.next}</> : null}
                    </p>
                  ) : null}
                  {m.error ? <p className="mt-1 text-xs text-ink-3">{m.error}</p> : null}
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </aside>
    </div>
  );
}

export default function TrackerPage() {
  const [stage, setStage] = useState("");
  const [sentiment, setSentiment] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useApi<Track>("/api/outreach/track");
  const { tasks, watch, dismiss } = useTaskWatcher();

  async function poll() {
    const { task_id } = await api.post<{ task_id: string }>("/api/replies/poll");
    watch(task_id, () => void reload());
  }

  const counts = data?.counts ?? {};
  const rows = (data?.rows ?? []).filter(
    (r) => (!stage || r.stage === stage) && (!sentiment || r.sentiment === sentiment),
  );

  return (
    <>
      <PageHead
        title="Tracker"
        description="Every email and where it stands. Click a row to read the whole thread."
        action={
          <>
            <Select value={stage} onChange={(e) => setStage(e.target.value)}>
              <option value="">Every status</option>
              {Object.entries(STAGE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
            <Select value={sentiment} onChange={(e) => setSentiment(e.target.value)}>
              <option value="">Any reply</option>
              <option value="positive">Positive</option>
              <option value="neutral">Neutral</option>
              <option value="negative">Negative</option>
              <option value="closed">Closed</option>
            </Select>
            <OperatorOnly>
              <Button variant="quiet" size="sm" onClick={poll}>
                <RefreshCw size={13} />
                Check for replies
              </Button>
            </OperatorOnly>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-line bg-line sm:grid-cols-5 xl:grid-cols-10">
        {COUNT_TILES.map(([k, label]) => (
          <div key={k} className="bg-paper px-3 py-2">
            <p className="tnum text-lg font-semibold text-ink">{counts[k] ?? 0}</p>
            <p className="text-[11px] text-ink-3">{label}</p>
          </div>
        ))}
      </div>

      <Panel>
        {loading && !data ? (
          <Loading label="Loading tracker" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !rows.length ? (
          <Empty
            title={data?.rows.length ? "Nothing matches the filter" : "Nothing written yet"}
            hint="Drafts appear here first; approve them in the review queue and they move along the line."
            action={
              <Button asChild size="sm">
                <Link href="/queue">Open review queue</Link>
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-40">Company</Th>
                <Th className="w-44">To</Th>
                <Th>Subject</Th>
                <Th className="w-36">Status</Th>
                <Th className="w-24">Sent</Th>
                <Th className="w-56">Reply</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Tr key={r.email_id} className="cursor-pointer" onClick={() => setOpen(r.email_id)}>
                  <Td className="text-ink">{r.company_name}</Td>
                  <Td>
                    <p className="truncate text-sm text-ink">{r.contact_name ?? r.contact_email}</p>
                    <p className="tnum truncate text-xs text-ink-3">{r.contact_email}</p>
                  </Td>
                  <Td>
                    <p className="truncate text-xs text-ink-2">{r.subject || "—"}</p>
                    <p className="text-[11px] text-ink-3">
                      {r.job_title ?? ""}
                      {r.quality != null ? ` · quality ${r.quality}` : ""}
                      {r.quality_verdict === "rewrite" ? " · rewrite" : ""}
                    </p>
                  </Td>
                  <Td>
                    <StageCell r={r} />
                  </Td>
                  <Td className="text-xs text-ink-3">{r.sent_at ? relTime(r.sent_at) : "—"}</Td>
                  <Td>
                    {r.sentiment ? (
                      <div className="space-y-0.5">
                        <SentimentBadge sentiment={r.sentiment} />
                        <p className="line-clamp-2 text-[11px] text-ink-3">{r.reply_snippet}</p>
                      </div>
                    ) : (
                      <span className="text-xs text-ink-3">—</span>
                    )}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>

      <Learn />

      {open != null ? <ThreadView id={open} onClose={() => setOpen(null)} /> : null}
      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
