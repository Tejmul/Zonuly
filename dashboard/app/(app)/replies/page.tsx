"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, useApi, useTaskWatcher, type Reply } from "@/lib/api";
import { cn, relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { SentimentBadge } from "@/components/signals";
import {
  Button,
  Empty,
  ErrorNote,
  Loading,
  Panel,
  PanelHead,
} from "@/components/ui/primitives";

const TABS = [
  { key: "", label: "Everything" },
  { key: "positive", label: "Positive" },
  { key: "negative,closed", label: "Declined & closed" },
  { key: "neutral", label: "Neutral" },
] as const;

export default function RepliesPage() {
  const [tab, setTab] = useState<string>("");
  const { data, error, loading, reload } = useApi<Reply[]>("/api/replies?limit=300");
  const { tasks, watch, dismiss } = useTaskWatcher();

  async function poll() {
    const { task_id } = await api.post<{ task_id: string }>("/api/replies/poll");
    watch(task_id, () => void reload());
  }

  const wanted = tab ? tab.split(",") : null;
  const rows = (data ?? []).filter((r) => !wanted || wanted.includes(r.sentiment ?? "neutral"));

  const counts = (data ?? []).reduce<Record<string, number>>((acc, r) => {
    const key = r.sentiment ?? "neutral";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <PageHead
        title="Replies"
        description="What came back, sorted by what it means for you. Classification is deliberately cautious — anything ambiguous lands in Neutral."
        action={
          <Button variant="quiet" size="sm" onClick={poll}>
            <RefreshCw size={13} />
            Check now
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => {
          const n = t.key
            ? t.key.split(",").reduce((sum, k) => sum + (counts[k] ?? 0), 0)
            : (data?.length ?? 0);
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
                tab === t.key
                  ? "border-line-strong text-ink"
                  : "border-transparent text-ink-3 hover:text-ink",
              )}
            >
              {t.label}
              <span className="tnum ml-2 text-xs text-ink-3">{n}</span>
            </button>
          );
        })}
      </div>

      {loading && !data ? (
        <Loading label="Loading replies" />
      ) : error ? (
        <ErrorNote message={error} onRetry={reload} />
      ) : !rows.length ? (
        <Panel>
          <Empty
            title={tab ? "Nothing in this bucket" : "No replies yet"}
            hint="The tracker polls your Gmail threads every hour and classifies each new inbound message. Anything it can't read confidently is filed as Neutral for you to judge."
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
