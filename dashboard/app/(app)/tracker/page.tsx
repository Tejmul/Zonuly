"use client";

import Link from "next/link";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, useApi, useTaskWatcher, type TrackerRow } from "@/lib/api";
import { cn, relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { ConfidenceBadge, Odds, SentimentBadge } from "@/components/signals";
import { OperatorOnly } from "@/components/access";
import {
  Badge,
  Button,
  Empty,
  ErrorNote,
  Loading,
  Panel,
  Select,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui/primitives";

const STATUS_TONE: Record<string, string> = {
  approved: "text-ink border-line-strong/40",
  sent: "text-ink border-line-strong",
  replied: "text-ink border-line-strong",
  failed: "text-ink border-line-strong",
};

export default function TrackerPage() {
  const [filter, setFilter] = useState("");
  const { data, error, loading, reload } = useApi<TrackerRow[]>("/api/tracker");
  const { tasks, watch, dismiss } = useTaskWatcher();

  async function poll() {
    const { task_id } = await api.post<{ task_id: string }>("/api/replies/poll");
    watch(task_id, () => void reload());
  }

  const rows = (data ?? []).filter((r) => !filter || r.status === filter);

  // one row per company keeps the "have I already hit this company?" question answerable
  const byCompany = new Map<string, TrackerRow[]>();
  for (const row of rows) {
    const key = row.company_name ?? "Unknown";
    byCompany.set(key, [...(byCompany.get(key) ?? []), row]);
  }

  return (
    <>
      <PageHead
        title="Sent tracker"
        description="Every approved and sent email, grouped by company so you can see who you've already reached."
        action={
          <>
            <Select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All</option>
              <option value="approved">Approved, not sent</option>
              <option value="sent">Sent</option>
              <option value="replied">Replied</option>
              <option value="failed">Failed</option>
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

      <Panel>
        {loading && !data ? (
          <Loading label="Loading tracker" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !rows.length ? (
          <Empty
            title="Nothing sent yet"
            hint="Approve drafts in the review queue and they appear here with their thread status."
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
                <Th className="w-44">Company</Th>
                <Th className="w-48">Contacted</Th>
                <Th>Role referenced</Th>
                <Th className="w-32">Odds</Th>
                <Th className="w-24">Status</Th>
                <Th className="w-28">Sent</Th>
                <Th className="w-32">Reply</Th>
              </tr>
            </thead>
            <tbody>
              {[...byCompany.entries()].map(([company, companyRows]) =>
                companyRows.map((row, i) => (
                  <Tr key={row.email_id}>
                    <Td className="text-ink">
                      {i === 0 ? (
                        company
                      ) : (
                        <span className="text-ink-3" aria-hidden>
                          ↳
                        </span>
                      )}
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm text-ink">
                          {row.contact_name ?? row.contact_email}
                        </span>
                        <ConfidenceBadge confidence={row.contact_confidence} />
                      </div>
                      <div className="tnum mt-0.5 truncate text-xs text-ink-3">
                        {row.contact_email}
                      </div>
                    </Td>
                    <Td className="text-xs text-ink-2">
                      {row.job_id ? (
                        <Link href={`/roles/${row.job_id}`} className="hover:text-ink">
                          {row.job_title}
                        </Link>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                      {row.kind === "followup" ? (
                        <Badge className="ml-2 border-line-strong/40 text-ink">follow-up</Badge>
                      ) : null}
                    </Td>
                    <Td>
                      <Odds score={row.match_score} size="sm" />
                    </Td>
                    <Td>
                      <Badge className={cn("bg-transparent", STATUS_TONE[row.status])}>
                        {row.status}
                      </Badge>
                    </Td>
                    <Td className="text-xs text-ink-3">{relTime(row.sent_at)}</Td>
                    <Td>
                      {row.reply_count ? (
                        <SentimentBadge sentiment={row.reply_sentiment} />
                      ) : row.followup_sent ? (
                        <span className="text-xs text-ink-3">followed up</span>
                      ) : (
                        <span className="text-xs text-ink-3">—</span>
                      )}
                    </Td>
                  </Tr>
                )),
              )}
            </tbody>
          </Table>
        )}
      </Panel>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
