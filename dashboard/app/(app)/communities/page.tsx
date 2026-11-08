"use client";

/**
 * Communities — the Slack and Discord rooms where a role is posted before it is listed.
 *
 * The honest frame, which this page states rather than implies: the pipeline can read the public
 * DIRECTORIES (name, platform, topic, size, join link) and nothing inside a workspace, because a
 * workspace is private and automating a joined personal account would risk Tejmul's real identity.
 * So the machine ranks the rooms and he joins them. "Joined" is the one field a human sets.
 *
 * UNDERRATED is the ranking, not a filter (jobhunter/communities.py): a 300-member room where the
 * founder posts the role herself beats a 100,000-member room where every post draws 400 applicants
 * within the hour. Size is shown next to the score so the ranking can be argued with.
 */

import { useMemo, useState } from "react";
import { ExternalLink, Search, Users } from "lucide-react";
import { api, useApi, useTaskWatcher, type Community, type CommunityPayload } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { StatTile } from "@/components/signals";
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

/** "4,000" rather than "4000", and "—" rather than a 0 that means "the directory didn't say". */
function members(n: number | null) {
  if (!n) return null;
  return n.toLocaleString("en-IN");
}

/** The whole point of the ranking, in one word per band. */
function sizeBand(n: number | null) {
  if (!n) return { label: "size unknown", tone: "text-ink-3" };
  if (n < 5_000) return { label: "small", tone: "text-ink" };
  if (n < 50_000) return { label: "mid", tone: "text-ink-2" };
  return { label: "crowded", tone: "text-ink-3" };
}

export default function CommunitiesPage() {
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState("");
  const [topic, setTopic] = useState("");
  const [jobsOnly, setJobsOnly] = useState(false);
  const [joinedOnly, setJoinedOnly] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);

  const params = new URLSearchParams({ limit: "500" });
  if (q) params.set("q", q);
  if (platform) params.set("platform", platform);
  if (topic) params.set("topic", topic);
  if (jobsOnly) params.set("with_jobs", "true");
  if (joinedOnly) params.set("joined", "true");

  const { data, error, loading, reload } = useApi<CommunityPayload>(
    `/api/communities?${params.toString()}`,
  );
  const { tasks, watch, dismiss } = useTaskWatcher();

  const stats = data?.stats;
  const rows = useMemo(() => data?.rows ?? [], [data]);

  async function toggleJoined(c: Community) {
    setBusy(c.id);
    try {
      await api.post(`/api/communities/${c.id}/joined?joined=${!c.joined}`);
      await reload();
    } finally {
      setBusy(null);
    }
  }

  async function harvest() {
    const { task_id } = await api.post<{ task_id: string }>("/api/communities/harvest?pages=12");
    watch(task_id, () => void reload());
  }

  return (
    <>
      <PageHead
        title="Communities"
        description={
          stats
            ? `${stats.total} rooms found in the public directories. ${stats.with_jobs} name a jobs channel; ${stats.joined} joined.`
            : "The Slack and Discord rooms where roles are posted before they are listed."
        }
        action={
          <OperatorOnly>
            <Button variant="quiet" size="sm" onClick={harvest}>
              Find more rooms
            </Button>
          </OperatorOnly>
        }
      />

      {stats ? (
        <Panel className="mb-4">
          <div className="flex flex-wrap gap-0 p-4">
            <StatTile label="Rooms" value={stats.total} hint="from public directories" />
            <StatTile label="Jobs channel" value={stats.with_jobs} hint="the directory says so" />
            <StatTile
              label="Under 5k members"
              value={stats.under_5k}
              hint="small enough to be read"
            />
            <StatTile label="Slack" value={stats.slack} />
            <StatTile label="Discord" value={stats.discord} />
            <StatTile
              label="Join link"
              value={stats.with_join_link}
              hint="you can join by hand now"
            />
            <StatTile label="Joined" value={stats.joined} hint="set by you, never by code" />
          </div>
        </Panel>
      ) : null}

      {/* Said once, plainly, so nobody waits for messages that can never arrive. */}
      <p className="mb-4 max-w-3xl text-xs leading-relaxed text-ink-3">
        Messages inside a Slack or Discord workspace are private — reading them needs an account
        that has joined, and automating one would put your real Slack, LinkedIn and GitHub
        identities at risk. So this page finds and ranks the rooms; you join them with the link.
        Once you are in and a channel has a <em>public</em> job feed, that feed becomes a scraper
        like any other.
      </p>

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
              placeholder="Search name, topic or description"
              className="pl-8"
            />
          </div>
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">All platforms</option>
            {(data?.platforms ?? []).map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
          <Select value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">All topics</option>
            {(data?.all_topics ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
          <label className="flex cursor-pointer items-center gap-2 px-1 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={jobsOnly}
              onChange={(e) => setJobsOnly(e.target.checked)}
              className="accent-[var(--color-signal)]"
            />
            Jobs channel only
          </label>
          <label className="flex cursor-pointer items-center gap-2 px-1 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={joinedOnly}
              onChange={(e) => setJoinedOnly(e.target.checked)}
              className="accent-[var(--color-signal)]"
            />
            Joined only
          </label>
        </div>
      </Panel>

      <Panel>
        {loading && !data ? (
          <Loading label="Loading communities" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !rows.length ? (
          <Empty
            title="No room matches those filters"
            hint="Clear the filters, or use “Find more rooms” to walk more directory pages."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-14 text-right">Rank</Th>
                <Th>Room</Th>
                <Th className="w-24">Platform</Th>
                <Th className="w-36">Topic</Th>
                <Th className="w-32 text-right">Members</Th>
                <Th className="w-28">Jobs</Th>
                <Th className="w-24">Seen</Th>
                <Th className="w-28">Joined</Th>
                <Th className="w-10" />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const band = sizeBand(c.members);
                return (
                  <Tr key={c.id}>
                    <Td className="tnum text-right text-ink-2">{c.score}</Td>
                    <Td>
                      <div className="text-ink">{c.name}</div>
                      {/* The score in the module's own words — never a bare number */}
                      {c.why ? (
                        <div className="mt-0.5 max-w-xl text-xs leading-snug text-ink-3">
                          {c.why}
                        </div>
                      ) : null}
                    </Td>
                    <Td>
                      <Badge>{c.platform ?? "—"}</Badge>
                    </Td>
                    <Td className="truncate text-xs text-ink-2">{c.topic ?? "—"}</Td>
                    <Td className="tnum text-right">
                      <span className={band.tone}>{members(c.members) ?? "—"}</span>
                      <div className="text-xs text-ink-3">{band.label}</div>
                    </Td>
                    <Td>
                      {c.has_jobs ? (
                        <Badge className="border-line-strong bg-surface-2/15 text-ink">
                          jobs channel
                        </Badge>
                      ) : (
                        <span className="text-xs text-ink-3">not named</span>
                      )}
                    </Td>
                    <Td className="text-xs text-ink-3">{relTime(c.last_seen)}</Td>
                    <Td>
                      <OperatorOnly
                        fallback={
                          c.joined ? <Badge className="text-ink">joined</Badge> : <span className="text-xs text-ink-3">—</span>
                        }
                      >
                        <Button
                          variant="quiet"
                          size="sm"
                          disabled={busy === c.id}
                          onClick={() => void toggleJoined(c)}
                        >
                          {c.joined ? "Joined" : "Mark joined"}
                        </Button>
                      </OperatorOnly>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        {c.join_url ? (
                          <a
                            href={c.join_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-ink-3 transition-colors hover:text-ink"
                            aria-label={`Join ${c.name}`}
                          >
                            <Users size={13} />
                          </a>
                        ) : null}
                        {c.url ? (
                          <a
                            href={c.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-ink-3 transition-colors hover:text-ink"
                            aria-label={`Open the directory page for ${c.name}`}
                          >
                            <ExternalLink size={13} />
                          </a>
                        ) : null}
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}
