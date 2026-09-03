"use client";

import Link from "next/link";
import { useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { api, useApi, useTaskWatcher, type Job } from "@/lib/api";
import { lpa, relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { Odds } from "@/components/signals";
import { OperatorOnly } from "@/components/access";
import {
  Badge,
  Button,
  Empty,
  ErrorNote,
  Input,
  Loading,
  Panel,
  Select,
  Table,
  Td,
  Th,
  Tr,
} from "@/components/ui/primitives";

type Sort = "score" | "salary" | "recent" | "similarity";

export default function RolesPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<Sort>("score");
  const [minScore, setMinScore] = useState("");
  const [minLpa, setMinLpa] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(false);

  const params = new URLSearchParams({ sort, limit: "200" });
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (minScore) params.set("min_score", minScore);
  if (minLpa) params.set("min_lpa", minLpa);
  if (remoteOnly) params.set("remote", "true");

  const { data, error, loading, reload } = useApi<{ total: number; items: Job[] }>(
    `/api/jobs?${params.toString()}`,
  );
  const { tasks, watch, dismiss } = useTaskWatcher();

  async function scoreMore() {
    const { task_id } = await api.post<{ task_id: string }>("/api/pipeline/score?limit=40");
    watch(task_id, () => void reload());
  }

  return (
    <>
      <PageHead
        title="Roles"
        description={
          data ? `${data.total} jobs match these filters.` : "Every job the scrapers kept."
        }
        action={
          <OperatorOnly>
            <Button variant="quiet" size="sm" onClick={scoreMore}>
              Score 40 more
            </Button>
          </OperatorOnly>
        }
      />

      <Panel className="mb-4">
        <div className="flex flex-wrap items-center gap-2 p-3">
          <div className="relative min-w-56 flex-1">
            <Search
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3"
            />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search title or company"
              className="pl-8"
            />
          </div>
          <Select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="score">Sort: shortlist odds</option>
            <option value="salary">Sort: salary</option>
            <option value="recent">Sort: newest</option>
            <option value="similarity">Sort: resume similarity</option>
          </Select>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="high_match">High match</option>
            <option value="scored">Scored</option>
            <option value="new">Unscored</option>
            <option value="applied">Applied</option>
            <option value="ignored">Ignored</option>
          </Select>
          <Input
            value={minScore}
            onChange={(e) => setMinScore(e.target.value.replace(/\D/g, ""))}
            placeholder="Min score"
            className="w-24"
            inputMode="numeric"
          />
          <Input
            value={minLpa}
            onChange={(e) => setMinLpa(e.target.value.replace(/[^\d.]/g, ""))}
            placeholder="Min LPA"
            className="w-24"
            inputMode="decimal"
          />
          <label className="flex cursor-pointer items-center gap-2 px-1 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={remoteOnly}
              onChange={(e) => setRemoteOnly(e.target.checked)}
              className="accent-[var(--color-signal)]"
            />
            Remote only
          </label>
        </div>
      </Panel>

      <Panel>
        {loading && !data ? (
          <Loading label="Loading leads" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !data?.items.length ? (
          <Empty
            title="Nothing matches those filters"
            hint="Try clearing the score or salary floor. Jobs with no stated salary are kept, so a salary filter hides them."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-40">Odds</Th>
                <Th>Role</Th>
                <Th className="w-44">Company</Th>
                <Th className="w-28 text-right">Salary</Th>
                <Th className="w-40">Location</Th>
                <Th className="w-24">Source</Th>
                <Th className="w-24">Seen</Th>
                <Th className="w-10" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((job) => (
                <Tr key={job.id}>
                  <Td>
                    <Odds score={job.match_score} size="sm" />
                  </Td>
                  <Td>
                    <Link
                      href={`/roles/${job.id}`}
                      className="text-ink transition-colors hover:text-ink"
                    >
                      {job.title}
                    </Link>
                    {job.status === "applied" ? (
                      <Badge className="ml-2 border-line-strong bg-surface-2/15 text-ink">applied</Badge>
                    ) : null}
                  </Td>
                  <Td className="text-ink-2">{job.company_name}</Td>
                  <Td className="tnum text-right text-ink">
                    {lpa(job.salary_min_lpa, job.salary_max_lpa) ?? (
                      <span className="text-ink-3">—</span>
                    )}
                  </Td>
                  <Td className="truncate text-xs text-ink-3">
                    {job.remote ? "Remote" : (job.location ?? "—")}
                  </Td>
                  <Td>
                    <Badge>{job.source}</Badge>
                  </Td>
                  <Td className="text-xs text-ink-3">{relTime(job.scraped_at)}</Td>
                  <Td>
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-ink-3 transition-colors hover:text-ink"
                      aria-label={`Open ${job.title} posting`}
                    >
                      <ExternalLink size={13} />
                    </a>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
