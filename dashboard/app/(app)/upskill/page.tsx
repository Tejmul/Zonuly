"use client";

import Link from "next/link";
import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { api, useApi, useTaskWatcher, type UpskillPage, type UpskillSkill, type UpskillStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageHead } from "@/components/shell";
import { Button, Empty, ErrorNote, Input, Loading, Panel, Textarea } from "@/components/ui/primitives";

/* Upskill (jobhunter/upskill.py) as one learning plan: every gap — DSA included — in a single
   numbered list, best payoff first, each said in a plain sentence. The payoff is the brain's own
   arithmetic (how much learning it raises the offers you can expect across your shortlist), shown
   as a word and a bar; the number is in the tooltip. Nothing reaches the profile without a proof
   link and a line in your words. */

const KIND_LABEL = { learn: "New skill", prove: "Show it in a project", dsa: "Practice" } as const;
const KIND_HINT = {
  learn: "missing from your resume",
  prove: "on your resume only as a word in the skills list — no project shows it",
  dsa: "interview practice — LeetCode-style problems",
} as const;
const STATUS: UpskillStatus[] = ["todo", "learning", "done"];
const STATUS_LABEL: Record<UpskillStatus, string> = { todo: "Not started", learning: "Learning", done: "Done" };
const offers = (x: number) => (x >= 0.1 ? x.toFixed(2) : x.toFixed(3));

type PlanItem =
  | { key: string; type: "dsa"; title: string; payoff: number; line: string; status: UpskillStatus; dsa: UpskillPage["dsa"] }
  | { key: string; type: "skill"; title: string; payoff: number; line: string; status: UpskillStatus; skill: UpskillSkill };

function impact(x: number): string {
  return x >= 0.5 ? "Biggest boost" : x >= 0.15 ? "High" : x >= 0.06 ? "Medium" : "Small";
}

function plan(data: UpskillPage): PlanItem[] {
  const items: PlanItem[] = data.skills.map((s) => ({
    key: s.skill,
    type: "skill",
    title: s.skill,
    payoff: s.expected_offers_gained,
    status: s.status,
    skill: s,
    line:
      s.kind === "learn"
        ? `New skill — ${s.companies_n} of your ${data.companies_read} target companies ask for it` +
          (s.required_mentions ? `, ${s.required_mentions}× as a must-have.` : ".")
        : `You list it, but no project on your resume shows it — ${s.companies_n} companies ask for it. Build one small project with it.`,
  }));
  const next = data.dsa.levels[0];
  if (next) {
    items.push({
      key: "DSA",
      type: "dsa",
      title: `DSA practice — get to ${Math.round(next.level * 100)}%`,
      payoff: next.expected_offers_gained,
      status: data.dsa.status,
      dsa: data.dsa,
      line:
        `Raises your odds of clearing the interview at every company on your list` +
        (next.companies_back.length ? `, and brings back ${next.companies_back.map((b) => b.name).join(", ")}.` : "."),
    });
  }
  return items.sort((a, b) => b.payoff - a.payoff);
}

export default function UpskillPageView() {
  const { data, error, loading, reload } = useApi<UpskillPage>("/api/upskill");
  const [open, setOpen] = useState<string | null>(null);

  const items = data ? plan(data) : [];
  const max = Math.max(...items.map((i) => i.payoff), 1e-6);

  return (
    <>
      <PageHead
        title="Upskill"
        description={`What to learn next, best first — ranked by how much each one raises your chances across the ${
          data?.companies_read ?? ""
        } companies you are targeting.`}
      />
      {loading && !data ? (
        <Loading label="Reading what your target companies ask for" />
      ) : error ? (
        <ErrorNote message={error} onRetry={reload} />
      ) : !items.length ? (
        <Empty title="Nothing to learn" hint="Your resume covers what your target companies ask for — or the brain has not run yet." />
      ) : (
        <>
          <p className="mb-4 text-xs leading-relaxed text-ink-3">
            Read from {data?.companies_read} shortlisted companies (Bengaluru, Hyderabad, Remote): their best roles&rsquo; needs
            against your resume. If you worked the whole list today you could expect about{" "}
            <span className="tnum text-ink-2">{offers(data?.expected_offers_now ?? 0)}</span> offers; each item below says how much
            learning it adds to that, best first.
          </p>
          <ol className="flex flex-col gap-2">
            {items.map((it, i) => (
              <PlanRow
                key={it.key}
                n={i + 1}
                it={it}
                width={it.payoff / max}
                open={open === it.key}
                onToggle={() => setOpen(open === it.key ? null : it.key)}
                onChange={reload}
              />
            ))}
          </ol>
          <p className="mt-4 text-xs text-ink-3">
            How the order is decided: for each gap, the same model that ranks your companies works out how much your chance of an
            offer rises at every company that asks for it, and adds those up. It is an estimate, not a promise.
          </p>
        </>
      )}
    </>
  );
}

function PlanRow({ n, it, width, open, onToggle, onChange }: {
  n: number; it: PlanItem; width: number; open: boolean; onToggle: () => void; onChange: () => void;
}) {
  return (
    <li>
      <Panel>
        <button type="button" onClick={onToggle} className="flex w-full cursor-pointer items-start gap-4 px-4 py-3 text-left">
          <span className="tnum w-6 shrink-0 text-lg font-semibold text-ink-3">{n}</span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[14px] font-medium text-ink">{it.title}</span>
              <KindChip kind={it.type === "dsa" ? "dsa" : it.skill.kind} />
              <span className="text-xs text-ink-3">· {STATUS_LABEL[it.status]}{it.type === "skill" && it.skill.in_profile ? " · in your profile" : ""}</span>
            </div>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-2">{it.line}</p>
            {it.type === "skill" ? (
              <p className="mt-0.5 text-xs text-ink-3">
                {it.skill.companies_n} companies ({Math.round(it.skill.share * 100)}% of your list)
                {it.skill.required_mentions ? ` · ${it.skill.required_mentions}× as a must-have` : ""} ·{" "}
                {Object.entries(it.skill.pools).map(([p, c]) => `${p} ${c}`).join(", ")}
              </p>
            ) : (
              <p className="mt-0.5 text-xs text-ink-3">
                Your level today {Math.round(it.dsa.level_now * 100)}% ·{" "}
                {it.dsa.levels.map((l) => `at ${Math.round(l.level * 100)}%: +${offers(l.expected_offers_gained)} offers` +
                  (l.companies_back.length ? ` (${l.companies_back.length} companies back)` : "")).join(" · ")}
              </p>
            )}
          </div>
          <div className="w-32 shrink-0" title="An upper estimate: the skill counted as fully covered at each company that asks for it">
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="font-medium text-ink">{impact(it.payoff)}</span>
              <span className="tnum text-ink-3">+{offers(it.payoff)}</span>
            </div>
            <div className="mt-1 h-1 rounded-sm bg-surface-3">
              <div className="h-1 rounded-sm bg-ink" style={{ width: `${Math.max(3, width * 100)}%` }} />
            </div>
          </div>
        </button>
        {open ? it.type === "skill" ? <SkillDetail s={it.skill} onChange={onChange} /> : <DsaDetail dsa={it.dsa} /> : null}
      </Panel>
    </li>
  );
}

function KindChip({ kind }: { kind: keyof typeof KIND_LABEL }) {
  return (
    <span
      title={KIND_HINT[kind]}
      className={cn(
        "inline-flex h-5 items-center rounded-sm border px-1.5 text-[11px] font-medium whitespace-nowrap",
        kind === "learn" ? "border-ink text-ink" : kind === "dsa" ? "bg-ink text-on-ink border-ink" : "border-dashed border-line-strong text-ink-2",
      )}
    >
      {KIND_LABEL[kind]}
    </span>
  );
}

function DsaDetail({ dsa }: { dsa: UpskillPage["dsa"] }) {
  return (
    <div className="border-t border-line px-4 py-3">
      <p className="mb-2 text-xs text-ink-2">Your DSA level today is {Math.round(dsa.level_now * 100)}%. What each step up is worth:</p>
      <ul className="flex flex-col gap-1">
        {dsa.levels.map((l) => (
          <li key={l.level} className="grid grid-cols-[4rem_7rem_minmax(0,1fr)] gap-3 text-xs">
            <span className="text-ink">{Math.round(l.level * 100)}%</span>
            <span className="text-ink-2">{impact(l.expected_offers_gained)}</span>
            <span className="text-ink-3">
              {l.companies_back.length ? `brings back ${l.companies_back.map((b) => b.name).join(", ")}` : "no parked company comes back"}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-ink-3">
        When you have practised, raise <code>decide.me.dsa_level</code> in config.yaml; everything re-ranks on the next run.
      </p>
    </div>
  );
}

function SkillDetail({ s, onChange }: { s: UpskillSkill; onChange: () => void }) {
  const [url, setUrl] = useState(s.proof_url ?? "");
  const [note, setNote] = useState(s.proof_note ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const { watch } = useTaskWatcher();

  async function mark(status: UpskillStatus) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<{ resume_line: string | null }>(`/api/upskill/${encodeURIComponent(s.skill)}`, {
        status,
        proof_url: url || null,
        proof_note: note || null,
      });
      setMsg(r.resume_line ? `For your resume PDF: ${r.resume_line}` : `Marked ${STATUS_LABEL[status].toLowerCase()}.`);
      onChange();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addToProfile() {
    setBusy(true);
    setMsg("Adding to your profile, then re-scoring every role and company — a few minutes.");
    try {
      const r = await api.post<{ task_id: string }>(`/api/upskill/${encodeURIComponent(s.skill)}/add-to-profile`);
      watch(r.task_id, (t) => {
        setMsg(t.status === "done" ? "In your profile; scores updated." : `Finished: ${t.status}`);
        setBusy(false);
        onChange();
      });
    } catch (e) {
      setMsg(String(e));
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 border-t border-line px-4 py-3 lg:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-3">
        <div>
          <div className="eyebrow mb-1">Who asks, in their words</div>
          <ul className="flex flex-col gap-2">
            {s.quotes.map((q, i) => (
              <li key={i} className="text-xs leading-relaxed">
                <Link href={`/companies/${q.company_id}`} className="font-medium text-ink hover:underline">
                  {q.company}
                </Link>
                <span className="text-ink-3"> · {q.role}</span>
                {q.url ? (
                  <a href={q.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex text-ink-3 hover:text-ink" aria-label="Open posting">
                    <ExternalLink size={11} />
                  </a>
                ) : null}
                {q.quote ? <p className="mt-0.5 line-clamp-3 text-ink-2">&ldquo;{q.quote}&rdquo;</p> : null}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="eyebrow mb-1">Where it moves your odds most</div>
          <ul className="flex flex-col gap-0.5">
            {s.companies.slice(0, 8).map((c) => (
              <li key={c.company_id} className="flex justify-between gap-3 text-xs">
                <Link href={`/companies/${c.company_id}`} className="truncate text-ink-2 hover:underline">
                  {c.name} <span className="text-ink-3">· {c.pool}</span>
                </Link>
                <span className="tnum shrink-0 text-ink-3">{c.gain > 0 ? `+${(c.gain * 100).toFixed(2)} pts` : "—"}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <div className="eyebrow">Your progress</div>
        <div className="flex flex-wrap gap-2">
          {STATUS.filter((x) => x !== "done").map((x) => (
            <Button key={x} size="sm" variant={s.status === x ? "primary" : "quiet"} disabled={busy} onClick={() => mark(x)}>
              {STATUS_LABEL[x]}
            </Button>
          ))}
        </div>
        <p className="text-xs text-ink-3">Done needs proof — the link and what you built, in your own words. Nothing is added for you.</p>
        <Input placeholder="Proof link: repo, demo, write-up" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Textarea
          placeholder={`What you built with ${s.skill}, in one line — this becomes the resume line`}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={s.status === "done" ? "primary" : "default"} disabled={busy || !url || !note} onClick={() => mark("done")}>
            Mark done
          </Button>
          <Button size="sm" variant="quiet" disabled={busy || s.status !== "done" || s.in_profile} onClick={addToProfile}
            title="Adds it to profile/profile.json (backed up first) and re-scores everything">
            {s.in_profile ? "In your profile" : "Add to my profile"}
          </Button>
        </div>
        {msg ? <p className="text-xs leading-relaxed text-ink-2">{msg}</p> : null}
      </div>
    </div>
  );
}
