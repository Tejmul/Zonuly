"use client";

import { useEffect, useState } from "react";
import { FileText, KeyRound, Save } from "lucide-react";
import { api, useApi, useTaskWatcher, type Health } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { PageHead, TaskStrip } from "@/components/shell";
import { StatusDot } from "@/components/signals";
import {
  Badge,
  Button,
  ErrorNote,
  Input,
  Loading,
  Panel,
  PanelHead,
} from "@/components/ui/primitives";

type Config = {
  search: { min_lpa: number; max_lpa: number; max_yoe: number };
  matching: { high_match_threshold: number; embed_prefilter_percentile: number };
  outreach: {
    daily_send_cap: number;
    followup_after_days: number;
    send_window: [number, number];
    user_name: string;
    user_headline: string;
  };
  sources: Record<string, boolean | number | string>;
};

export default function SettingsPage() {
  const health = useApi<Health>("/api/health");
  const config = useApi<Config>("/api/config");
  const profile = useApi<{ name: string; headline: string; years_experience: number; skills: Record<string, string[]> }>(
    "/api/profile",
  );
  const { tasks, watch, dismiss } = useTaskWatcher();

  const [form, setForm] = useState<Config | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (config.data) setForm(structuredClone(config.data));
  }, [config.data]);

  function set<K extends keyof Config>(section: K, key: string, value: unknown) {
    setForm((prev) =>
      prev ? { ...prev, [section]: { ...prev[section], [key]: value } } : prev,
    );
    setSaved(false);
  }

  async function save() {
    if (!form) return;
    setSaveError(null);
    try {
      await api.patch("/api/config", {
        search: form.search,
        matching: form.matching,
        outreach: form.outreach,
        sources: form.sources,
      });
      setSaved(true);
      void health.reload();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  async function dispatch(path: string, onDone?: () => void) {
    const { task_id } = await api.post<{ task_id: string }>(path);
    watch(task_id, onDone);
  }

  if (health.loading && !health.data) return <Loading label="Checking the system" />;
  if (health.error) return <ErrorNote message={health.error} onRetry={health.reload} />;
  const h = health.data;

  return (
    <>
      <PageHead
        title="Settings"
        description="Thresholds, caps, and the connections the pipeline depends on."
        action={
          <Button variant="primary" size="sm" onClick={save} disabled={!form}>
            <Save size={13} />
            {saved ? "Saved" : "Save changes"}
          </Button>
        }
      />

      {saveError ? (
        <div className="mb-4 rounded-sm border border-line-strong bg-surface-2/15 px-3 py-2 text-sm text-ink">
          {saveError}
        </div>
      ) : null}
      {saved ? (
        <div className="mb-4 rounded-sm border border-line bg-surface px-3 py-2 text-xs text-ink-2">
          Written to config.yaml. Restart the API to pick up model, source, and scheduler changes.
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <PanelHead eyebrow="Connections" title="What's wired up" />
          <div className="flex flex-col gap-3 p-4">
            <Row
              label="Local model"
              value={h?.llm.model}
              ok={!!h?.llm.ok && !!h?.llm.model_present}
              hint={h?.llm.ok ? undefined : "Run `ollama serve`, then `ollama pull qwen3:4b`."}
            />
            <Row
              label="Embeddings"
              value="nomic-embed-text"
              ok={!!h?.llm.embed_present}
              hint={h?.llm.embed_present ? undefined : "Run `ollama pull nomic-embed-text`."}
            />
            <Row
              label="Resume parsed"
              value={profile.data?.name ?? "not parsed"}
              ok={!!h?.profile.exists}
              hint={h?.profile.exists ? undefined : "Drop a PDF at profile/resume.pdf and re-parse."}
            />
            <Row
              label="GitHub token"
              value={h?.github_token ? "configured" : "60 requests/hour"}
              ok={!!h?.github_token}
              hint={
                h?.github_token
                  ? undefined
                  : "Add contacts.github_token to config.yaml to raise the limit to 5,000/hour. Without it you can search roughly three companies an hour."
              }
            />
            <Row
              label="Hunter.io"
              value={h?.hunter.configured ? `${h.hunter.lookups_left} lookups left` : "not configured"}
              ok={!!h?.hunter.configured}
              hint={
                h?.hunter.configured
                  ? undefined
                  : "Optional. 25 free lookups a month, spent only when GitHub and the company site don't reveal an email pattern."
              }
            />
            <Row
              label="Gmail"
              value={h?.gmail.authorized ? "connected" : h?.gmail.configured ? "not authorized" : "no client JSON"}
              ok={!!h?.gmail.authorized}
              hint={h?.gmail.authorized ? undefined : h?.gmail.hint}
            />
            <Row
              label="Scheduler"
              value={h?.scheduler.running ? "running" : "stopped"}
              ok={!!h?.scheduler.running}
            />

            <div className="flex flex-wrap gap-2 border-t border-line pt-3">
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/gmail/authorize")}>
                <KeyRound size={12} />
                Connect Gmail
              </Button>
              <Button
                size="sm"
                variant="quiet"
                onClick={() => dispatch("/api/pipeline/reparse-resume", () => void profile.reload())}
              >
                <FileText size={12} />
                Re-parse resume
              </Button>
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/pipeline/rescore")}>
                Clear all scores
              </Button>
            </div>
            <p className="text-xs leading-relaxed text-ink-3">
              Connect Gmail opens a browser window on the machine running the API. Clearing scores
              wipes every match so the next scoring run redoes them — do that after changing your
              resume or the thresholds below.
            </p>
          </div>
        </Panel>

        <Panel>
          <PanelHead eyebrow="Automation" title="Scheduled runs" />
          <div className="flex flex-col gap-3 p-4">
            {h?.scheduler.jobs.length ? (
              h.scheduler.jobs.map((job) => (
                <div key={job.id} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-ink">{job.name}</span>
                  <span className="tnum text-xs text-ink-3">
                    {job.next_run ? new Date(job.next_run).toLocaleString() : "not scheduled"}
                  </span>
                </div>
              ))
            ) : (
              <p className="text-xs text-ink-3">
                The scheduler runs inside the API process. Start it with{" "}
                <code className="font-mono text-ink-2">python scripts/run.py serve</code>.
              </p>
            )}

            {Object.entries(h?.scheduler.last_runs ?? {}).length ? (
              <div className="border-t border-line pt-3">
                <div className="eyebrow mb-2">Last runs</div>
                {Object.entries(h!.scheduler.last_runs).map(([name, run]) => (
                  <div key={name} className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="text-ink-2">{name}</span>
                    <span className={run.error ? "text-ink" : "text-ink-3"}>
                      {run.error ? run.error.slice(0, 40) : relTime(run.at)}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t border-line pt-3">
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/pipeline/scrape")}>
                Scrape now
              </Button>
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/pipeline/score?limit=40")}>
                Score 40
              </Button>
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/pipeline/salaries?limit=40")}>
                Extract salaries
              </Button>
              <Button size="sm" variant="quiet" onClick={() => dispatch("/api/replies/poll")}>
                Poll replies
              </Button>
            </div>
          </div>
        </Panel>

        {form ? (
          <>
            <Panel>
              <PanelHead eyebrow="What counts as a lead" title="Search and matching" />
              <div className="grid gap-3 p-4 sm:grid-cols-2">
                <Field
                  label="Minimum salary (LPA)"
                  hint="Jobs with no stated salary are always kept."
                  value={form.search.min_lpa}
                  onChange={(v) => set("search", "min_lpa", v)}
                />
                <Field
                  label="Max years experience"
                  hint="Postings asking for more are penalised."
                  value={form.search.max_yoe}
                  onChange={(v) => set("search", "max_yoe", v)}
                />
                <Field
                  label="High-match threshold"
                  hint="Score at or above this triggers a notification."
                  value={form.matching.high_match_threshold}
                  onChange={(v) => set("matching", "high_match_threshold", v)}
                />
                <Field
                  label="Prefilter percentile"
                  hint="Only the top slice by resume similarity reaches the LLM. Raise it to score fewer, better jobs."
                  value={form.matching.embed_prefilter_percentile}
                  onChange={(v) => set("matching", "embed_prefilter_percentile", v)}
                />
              </div>
            </Panel>

            <Panel>
              <PanelHead eyebrow="Protecting your account" title="Outreach limits" />
              <div className="grid gap-3 p-4 sm:grid-cols-2">
                <Field
                  label="Daily send cap"
                  hint="25 is the safe ceiling for a personal Gmail account."
                  value={form.outreach.daily_send_cap}
                  onChange={(v) => set("outreach", "daily_send_cap", v)}
                />
                <Field
                  label="Follow up after (days)"
                  hint="One follow-up per thread, never more."
                  value={form.outreach.followup_after_days}
                  onChange={(v) => set("outreach", "followup_after_days", v)}
                />
                <Field
                  label="Send window starts"
                  value={form.outreach.send_window[0]}
                  onChange={(v) =>
                    set("outreach", "send_window", [v, form.outreach.send_window[1]])
                  }
                />
                <Field
                  label="Send window ends"
                  value={form.outreach.send_window[1]}
                  onChange={(v) =>
                    set("outreach", "send_window", [form.outreach.send_window[0], v])
                  }
                />
                <label className="flex flex-col gap-1.5 sm:col-span-2">
                  <span className="eyebrow">Signature name</span>
                  <Input
                    value={form.outreach.user_name}
                    onChange={(e) => set("outreach", "user_name", e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1.5 sm:col-span-2">
                  <span className="eyebrow">Signature headline</span>
                  <Input
                    value={form.outreach.user_headline}
                    onChange={(e) => set("outreach", "user_headline", e.target.value)}
                  />
                </label>
              </div>
            </Panel>

            <Panel className="xl:col-span-2">
              <PanelHead
                eyebrow="Where to look"
                title="Sources"
                action={
                  <span className="text-xs text-ink-3">
                    Restart the API after changing these
                  </span>
                }
              />
              <div className="flex flex-wrap gap-2 p-4">
                {Object.entries(form.sources)
                  .filter(([, v]) => typeof v === "boolean")
                  .map(([name, enabled]) => (
                    <button
                      key={name}
                      onClick={() => set("sources", name, !enabled)}
                      className={`rounded-sm border px-2.5 py-1.5 font-mono text-xs transition-colors ${
                        enabled
                          ? "border-line-strong bg-surface-2/15 text-ink"
                          : "border-line bg-surface text-ink-3 hover:text-ink"
                      }`}
                    >
                      {name}
                    </button>
                  ))}
              </div>
              <p className="px-4 pb-4 text-xs leading-relaxed text-ink-3">
                Wellfound, Cutshort, and Instahyre drive a real browser through Playwright. They
                carry the Indian salary data the JSON boards don&apos;t, but they&apos;re slow and
                break when those sites change layout. Run{" "}
                <code className="font-mono text-ink-2">
                  uv run playwright install chromium
                </code>{" "}
                before turning them on.
              </p>
            </Panel>
          </>
        ) : null}

        {profile.data ? (
          <Panel className="xl:col-span-2">
            <PanelHead
              eyebrow="What the matcher scores against"
              title={`${profile.data.name} · ${profile.data.years_experience} yrs`}
            />
            <div className="p-4">
              <p className="mb-3 text-sm text-ink-2">{profile.data.headline}</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.values(profile.data.skills ?? {})
                  .flat()
                  .slice(0, 40)
                  .map((skill) => (
                    <Badge key={skill}>{skill}</Badge>
                  ))}
              </div>
            </div>
          </Panel>
        ) : null}
      </div>

      <TaskStrip tasks={tasks} onDismiss={dismiss} />
    </>
  );
}

function Row({
  label,
  value,
  ok,
  hint,
}: {
  label: string;
  value?: string | null;
  ok: boolean;
  hint?: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <StatusDot ok={ok} label={label} />
        <span className="tnum truncate text-xs text-ink-2">{value}</span>
      </div>
      {hint ? <p className="mt-1 pl-3 text-xs leading-relaxed text-ink-3">{hint}</p> : null}
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="eyebrow">{label}</span>
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="tnum"
      />
      {hint ? <span className="text-xs leading-relaxed text-ink-3">{hint}</span> : null}
    </label>
  );
}
