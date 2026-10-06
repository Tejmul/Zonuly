"use client";

import Link from "next/link";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, useApi, useTaskWatcher, type OutreachPipeline, type Reply } from "@/lib/api";
import { cn, relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { SentimentBadge } from "@/components/signals";
import { OperatorOnly, useAccess } from "@/components/access";
import { SENTIMENTS, SentimentSplit, count, type SentimentKey } from "@/components/outreach";
import {
  Button,
  Empty,
  ErrorNote,
  Loading,
  Panel,
  PanelHead,
} from "@/components/ui/primitives";

/* Tabs filter on the server (/api/replies?sentiment=), and their counts come from the
   pipeline summary — so a tab's number is right before its list has been fetched, and
   on the public instance, where the replies themselves are private, the counts still
   show instead of an error. */
const TABS: { key: SentimentKey | ""; label: string }[] = [
  ...SENTIMENTS.map((s) => ({ key: s.key, label: s.label })),
  { key: "", label: "All" },
];

export default function RepliesPage() {
  const { publicInstance, ready } = useAccess();
  const [tab, setTab] = useState<SentimentKey | "">("");
  const pipe = useApi<OutreachPipeline>("/api/outreach/pipeline");
  const list = useApi<Reply[]>(
    !ready || publicInstance ? null : `/api/replies?limit=300${tab ? `&sentiment=${tab}` : ""}`,
  );
  const { tasks, watch, dismiss } = useTaskWatcher();

  async function poll() {
    const { task_id } = await api.post<{ task_id: string }>("/api/replies/poll");
    watch(task_id, () => {
      void list.reload();
      void pipe.reload();
    });
  }

  const sentiment = pipe.data?.sentiment;
  const total = SENTIMENTS.reduce((n, s) => n + count(sentiment?.[s.key]), 0);
  const tabCount = (k: SentimentKey | "") => (pipe.data ? (k ? count(sentiment?.[k]) : total) : null);

  const head = (
    <PageHead
      title="Replies"
      description="What came back, sorted by what it means for you. Classification is deliberately cautious — anything ambiguous lands in Neutral."
      action={
        <OperatorOnly>
          <Button variant="quiet" size="sm" onClick={poll}>
            <RefreshCw size={13} />
            Check now
          </Button>
        </OperatorOnly>
      }
    />
  );

  /* Public instance: the messages are private, the tally is not. */
  if (publicInstance) {
    return (
      <>
        {head}
        <Panel className="p-4">
          {pipe.data ? (
            <SentimentSplit sentiment={sentiment} />
          ) : pipe.error ? (
            <ErrorNote message={pipe.error} onRetry={pipe.reload} />
          ) : (
            <Loading label="Counting replies" rows={3} />
          )}
          <p className="mt-4 border-t border-line pt-3 text-[12px] text-ink-3">
            The replies themselves are private on this public copy — these are the live counts.{" "}
            <Link href="/outreach" className="underline decoration-line underline-offset-2 hover:text-ink">See the whole pipeline</Link>.
          </p>
        </Panel>
      </>
    );
  }

  const rows = list.data ?? [];

  return (
    <>
      {head}

      <div className="mb-4 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => {
          const n = tabCount(t.key);
          return (
            <button
              key={t.key || "all"}
              onClick={() => setTab(t.key)}
              aria-pressed={tab === t.key}
              className={cn(
                "-mb-px cursor-pointer border-b-2 px-3 py-2 text-sm transition-colors",
                tab === t.key ? "border-ink font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink",
              )}
            >
              {t.label}
              <span className="tnum ml-2 text-xs text-ink-3">{n ?? "—"}</span>
            </button>
          );
        })}
      </div>

      {list.error && !list.loading ? (
        <Panel>
          <ErrorNote message={list.error} onRetry={list.reload} />
        </Panel>
      ) : !ready || list.loading ? (
        <Panel>
          <Loading label="Loading replies" />
        </Panel>
      ) : !rows.length ? (
        <Panel>
          <Empty
            title={tab ? `No ${tab} replies yet` : "No replies yet"}
            hint={
              count(pipe.data?.stages?.sent)
                ? "Your inbox is checked every hour and each new reply is classified. Anything it can't read confidently is filed as Neutral for you to judge."
                : "Nothing has been sent yet. Approve a draft in the review queue; replies appear here once people write back."
            }
            action={
              <Button asChild size="sm" variant="quiet">
                <Link href="/outreach">See the whole pipeline</Link>
              </Button>
            }
          />
        </Panel>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((reply) => (
            <Panel key={reply.id}>
              <PanelHead
                eyebrow={reply.from_addr}
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    <SentimentBadge sentiment={reply.sentiment} />
                    <span>{reply.company_name ?? "Unknown company"}</span>
                    {reply.contact_name ? (
                      <span className="text-xs font-normal text-ink-3">
                        · {reply.contact_name}
                      </span>
                    ) : null}
                  </span>
                }
                action={
                  <span className="text-xs text-ink-3">{relTime(reply.received_at)}</span>
                }
              />
              <div className="p-4">
                {reply.sentiment_reason ? (
                  <p className="mb-3 border-l-2 border-line pl-3 text-xs leading-relaxed text-ink-3">
                    {reply.sentiment_reason}
                  </p>
                ) : null}
                {reply.subject ? (
                  <div className="mb-2 text-xs text-ink-3">
                    In reply to: <span className="text-ink-2">{reply.subject}</span>
                  </div>
                ) : null}
                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink">
                  {reply.body}
                </pre>
              </div>
            </Panel>
          ))}
        </div>
      )}

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
