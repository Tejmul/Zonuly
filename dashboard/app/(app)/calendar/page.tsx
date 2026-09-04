"use client";

import Link from "next/link";
import { useState } from "react";
import { CalendarCheck, CalendarX, ExternalLink, Loader2 } from "lucide-react";
import { api, useApi, type EventRow } from "@/lib/api";
import { OperatorOnly } from "@/components/access";

/* A "yes" must not get lost. Every call, assessment and interview read out of a reply,
   with the sentence it came from; a clash is a draft in the queue, never a machine's
   confirmation. Confirming puts it on Google Calendar once the OAuth client exists. */

type CalStatus = { enabled: boolean; configured: boolean; authorized: boolean; calendar_id: string; timezone: string; hint: string | null };

const KIND: Record<string, string> = { call: "Call", assessment: "Assessment", interview: "Interview", referral_done: "Referral made", other: "Follow-up" };

export default function CalendarPage() {
  const events = useApi<EventRow[]>("/api/events");
  const { data: cal } = useApi<CalStatus>("/api/calendar/status");
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function act(id: number, path: string) {
    setBusy(id);
    setNote(null);
    try {
      const r = await api.post<Record<string, unknown>>(path);
      if (r && "email_id" in r) setNote(`Reschedule drafted into the Queue (email #${String(r.email_id)}).`);
      await events.reload();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const rows = events.data ?? [];
  const conflicts = rows.filter((e) => e.status === "conflict");
  const upcoming = rows.filter((e) => e.status !== "conflict" && e.status !== "cancelled" && e.status !== "done");
  const past = rows.filter((e) => e.status === "done" || e.status === "cancelled");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Calendar</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          Every time someone said yes with a time, a link or a deadline — read from their reply, never guessed.
        </p>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-line pt-2">
          <Stat n={upcoming.length} label="upcoming" />
          <Stat n={conflicts.length} label="clashes" />
          <Stat n={past.length} label="done" />
          <span className="marginal ml-auto">
            {cal?.authorized ? `synced to Google Calendar (${cal.calendar_id}, ${cal.timezone})` : "Google Calendar not connected yet — confirmations are kept here until it is"}
          </span>
        </div>
      </div>

      {note && <p className="rounded-md border border-line bg-surface px-3 py-2 text-[12.5px]">{note}</p>}

      {conflicts.length > 0 && (
        <Section title="Clashes — a reschedule is drafted for you in the Queue" rows={conflicts} busy={busy} onAct={act} />
      )}
      <Section title="Upcoming" rows={upcoming} busy={busy} onAct={act} empty="Nothing scheduled yet. When a reply proposes a time, it appears here and you get a notification." />
      {past.length > 0 && <Section title="Done" rows={past} busy={busy} onAct={act} />}
    </div>
  );
}

function Section({ title, rows, busy, onAct, empty }: {
  title: string; rows: EventRow[]; busy: number | null; onAct: (id: number, path: string) => void; empty?: string;
}) {
  return (
    <div className="plate">
      <div className="border-b border-line px-4 py-3">
        <h2 className="font-[family-name:var(--font-display)] text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-[12.5px] text-ink-3">{empty ?? "Nothing here."}</p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((e) => (
            <li key={e.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="chip">{KIND[e.kind] ?? e.kind}</span>
                <Link href={`/companies/${e.company_id}`} className="text-[13px] font-medium hover:underline">{e.company ?? "—"}</Link>
                <span className="text-[12px] text-ink-3">{e.contact ?? ""}</span>
                <span className="tnum ml-auto text-[12.5px]">{e.local ?? "time not stated"}</span>
                <span className={`chip ${e.status === "conflict" ? "chip-strike" : e.status === "confirmed" ? "chip-solid" : ""}`}>{e.status}</span>
              </div>
              {(e.quote || e.needs_action) && (
                <p className="mt-1.5 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic">
                  {e.quote ?? e.needs_action}
                </p>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {e.link && (
                  <a href={e.link} target="_blank" rel="noreferrer" className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-[11.5px] hover:bg-surface-2">
                    <ExternalLink size={12} /> open link
                  </a>
                )}
                {e.deadline && <span className="marginal">deadline {e.deadline.slice(0, 10)}</span>}
                <OperatorOnly>
                  {e.status === "proposed" && (
                    <button onClick={() => onAct(e.id, `/api/events/${e.id}/confirm`)} disabled={busy === e.id}
                      className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[11.5px] font-medium hover:bg-surface-2 disabled:opacity-60">
                      {busy === e.id ? <Loader2 size={12} className="animate-spin" /> : <CalendarCheck size={12} />} confirm
                    </button>
                  )}
                  {e.status === "conflict" && (
                    <button onClick={() => onAct(e.id, `/api/events/${e.id}/reschedule`)} disabled={busy === e.id}
                      className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[11.5px] font-medium hover:bg-surface-2 disabled:opacity-60">
                      <CalendarX size={12} /> draft the reschedule
                    </button>
                  )}
                  {(e.status === "proposed" || e.status === "confirmed") && (
                    <button onClick={() => onAct(e.id, `/api/events/${e.id}/status?status=done`)} className="marginal cursor-pointer hover:text-ink">mark done</button>
                  )}
                </OperatorOnly>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="tnum text-[13px] font-medium">{n.toLocaleString()}</span>
      <span className="marginal">{label}</span>
    </span>
  );
}
