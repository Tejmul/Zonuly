"use client";

/*  Launch checklist — is everything in place for the first live sends (2026-10-19)?
 *
 *  Reads GET /api/outreach/preflight (operator only): every check red / amber / green
 *  with the exact fix, the warm-up week and what the review queue holds for it, and the
 *  short list of what the user — not the app — has to do. Black and white: a check's
 *  state is a word and a weight, never a colour.
 */

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { ErrorNote, Loading } from "@/components/ui/primitives";

type Check = { id: string; label: string; status: "red" | "amber" | "green"; detail: string; fix: string | null };
type WeekDay = { day: string; weekday: string; send_day: boolean; quota: number };
export type Preflight = {
  at: string;
  launch_day: string;
  status: "red" | "amber" | "green";
  counts: { red: number; amber: number; green: number };
  checks: Check[];
  week: { days: WeekDay[]; week_total: number; approved: number; in_review: number; first_day: string | null; first_day_quota: number };
  user_checklist: string[];
};

const WORD: Record<Check["status"], string> = { red: "Fix", amber: "Look", green: "Ready" };

function Mark({ status }: { status: Check["status"] }) {
  if (status === "red") return <span className="chip chip-solid w-[3.4rem] justify-center">{WORD.red}</span>;
  if (status === "amber") return <span className="chip w-[3.4rem] justify-center">{WORD.amber}</span>;
  return <span className="inline-flex w-[3.4rem] justify-center text-[11.5px] text-ink-3">{WORD.green}</span>;
}

function day(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function LaunchChecklist() {
  const { data, error, loading, reload } = useApi<Preflight>("/api/outreach/preflight");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showGreen, setShowGreen] = useState(false);

  const build = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.post<{ task_id?: string }>("/api/outreach/batch", {});
      setNote(r.task_id ? "Drafting the week's asks. They appear in the review queue as they are written — none is approved until you approve it." : "Nothing to draft.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error && !data) return <section className="plate mb-4"><ErrorNote message={error} onRetry={reload} /></section>;
  if (!data) return <section className="plate mb-4"><Loading label="Checking the launch" rows={4} /></section>;

  const open = data.checks.filter((c) => c.status !== "green");
  const ready = data.checks.filter((c) => c.status === "green");
  const sendDays = data.week.days.filter((d) => d.send_day);

  return (
    <section data-anim className="plate mb-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-[13.5px] font-semibold">Launch checklist · first sends {day(data.launch_day)}</h2>
          <p className="mt-0.5 text-[12px] text-ink-3">
            {data.counts.red} to fix · {data.counts.amber} to look at · {data.counts.green} ready
          </p>
        </div>
        <button type="button" onClick={() => void reload()} className="inline-flex items-center gap-1.5 text-[12px] text-ink-2 hover:text-ink" disabled={loading}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Check again
        </button>
      </div>

      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* ---------------------------------------- what you do */}
        <div className="px-4 py-4 lg:border-r lg:border-line">
          <p className="marginal mb-2">Before Monday, you</p>
          <ol className="space-y-1.5 text-[12.5px] leading-relaxed">
            {data.user_checklist.map((step, i) => (
              <li key={i} className="grid grid-cols-[1.4rem_1fr]">
                <span className="tnum text-ink-3">{i + 1}.</span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </div>

        {/* ---------------------------------------- the warm-up week */}
        <div className="px-4 py-4">
          <p className="marginal mb-2">The warm-up week</p>
          <table className="w-full text-[12px]">
            <tbody>
              {sendDays.map((d) => (
                <tr key={d.day} className="border-b border-line last:border-b-0">
                  <td className="py-1 text-ink-2">{day(d.day)}</td>
                  <td className="tnum py-1 text-right font-medium">{d.quota}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11.5px] leading-snug text-ink-3">
            {data.week.week_total} this week, sent at jittered times between the window&rsquo;s hours. {data.week.approved} approved,{" "}
            {data.week.in_review} waiting for you.
          </p>
          <button type="button" onClick={() => void build()} disabled={busy} className="btn-ink mt-3 w-full justify-center">
            {busy ? "Starting…" : "Draft this week’s asks"}
          </button>
          {note && <p className="mt-2 text-[11.5px] leading-snug text-ink-2">{note}</p>}
        </div>
      </div>

      {/* ---------------------------------------- every check */}
      <ul className="divide-y divide-line border-t border-line">
        {open.map((c) => (
          <li key={c.id} className="grid grid-cols-[3.4rem_minmax(0,1fr)] gap-x-3 px-4 py-2.5">
            <Mark status={c.status} />
            <div className="min-w-0">
              <p className={`text-[12.5px] ${c.status === "red" ? "font-semibold" : "font-medium"}`}>{c.label}</p>
              <p className="mt-0.5 text-[12px] leading-snug text-ink-2">{c.detail}</p>
              {c.fix && <p className="mt-0.5 text-[12px] leading-snug"><span className="text-ink-3">Fix: </span>{c.fix}</p>}
            </div>
          </li>
        ))}
        {showGreen &&
          ready.map((c) => (
            <li key={c.id} className="grid grid-cols-[3.4rem_minmax(0,1fr)] gap-x-3 px-4 py-2">
              <Mark status={c.status} />
              <div className="min-w-0">
                <p className="text-[12.5px]">{c.label}</p>
                <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{c.detail}</p>
              </div>
            </li>
          ))}
      </ul>
      <button
        type="button"
        onClick={() => setShowGreen((v) => !v)}
        className="w-full border-t border-line px-4 py-2 text-left text-[12px] text-ink-3 hover:text-ink"
      >
        {showGreen ? "Hide what is ready" : `Show the ${ready.length} checks that are ready`}
      </button>
    </section>
  );
}
