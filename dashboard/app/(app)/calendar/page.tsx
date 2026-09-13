"use client";

/*  Calendar.
 *
 *  A "yes" must not get lost. Every call, assessment and interview read out of a reply
 *  lands here with the sentence it came from. It was a list; it is now a month, laid
 *  out the way every calendar people already use is laid out — seven columns, Sunday
 *  first, today ringed, chips on the days — because a list of dates makes you do the
 *  arithmetic and a grid does it for you.
 *
 *  Our system, not Google's: hairlines instead of gridlines, ink instead of green, and
 *  status carried by fill rather than hue. Confirmed is solid, proposed is outlined,
 *  a clash is struck through. A clash never becomes a machine's confirmation — it is
 *  a reschedule drafted into the Queue for you to approve.
 *
 *  Events that carry no date at all cannot go on a grid, so they get a tray beneath
 *  it rather than a guessed square.
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import { CalendarCheck, CalendarX, ChevronLeft, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { api, useApi, type EventRow } from "@/lib/api";
import { OperatorOnly } from "@/components/access";

type CalStatus = {
  enabled: boolean; configured: boolean; authorized: boolean;
  calendar_id: string; timezone: string; hint: string | null;
};

const KIND: Record<string, string> = {
  call: "Call", assessment: "Assessment", interview: "Interview", referral_done: "Referral made", other: "Follow-up",
};
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/* ------------------------------------------------------------------ dates */

/** The API stores naive UTC. A string with no zone is read as UTC, not as local —
 *  reading it as local would shift every Bangalore call by five and a half hours. */
function when(iso: string | null): Date | null {
  if (!iso) return null;
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? null : d;
}

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const sameDay = (a: Date, b: Date) => dayKey(a) === dayKey(b);

/** The 42 cells of a month view: the Sunday on or before the 1st, through six weeks.
 *  Six always, so the grid does not change height as you page through months. */
function cells(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

const fmtTime = (d: Date) =>
  d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).replace(/\s?([AP]M)$/i, (m) => m.trim().toLowerCase());

/** Where an event sits on the grid: its start, else its deadline, else nowhere. */
function anchor(e: EventRow): { date: Date; due: boolean } | null {
  const s = when(e.starts_at);
  if (s) return { date: s, due: false };
  const d = when(e.deadline);
  if (d) return { date: d, due: true };
  return null;
}

/* ------------------------------------------------------------------- page */

export default function CalendarPage() {
  const events = useApi<EventRow[]>("/api/events");
  const { data: cal } = useApi<CalStatus>("/api/calendar/status");

  const today = useMemo(() => new Date(), []);
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [selected, setSelected] = useState<Date | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const rows = useMemo(() => events.data ?? [], [events.data]);

  const { byDay, undated } = useMemo(() => {
    const byDay = new Map<string, { e: EventRow; date: Date; due: boolean }[]>();
    const undated: EventRow[] = [];
    for (const e of rows) {
      const a = anchor(e);
      if (!a) { undated.push(e); continue; }
      const k = dayKey(a.date);
      (byDay.get(k) ?? byDay.set(k, []).get(k)!).push({ e, ...a });
    }
    for (const list of byDay.values()) list.sort((x, y) => x.date.getTime() - y.date.getTime());
    return { byDay, undated };
  }, [rows]);

  const conflicts = rows.filter((e) => e.status === "conflict");
  const upcoming = rows
    .map((e) => ({ e, a: anchor(e) }))
    .filter(({ e, a }) => a && a.date >= today && !["conflict", "cancelled", "done"].includes(e.status))
    .sort((x, y) => x.a!.date.getTime() - y.a!.date.getTime());
  const done = rows.filter((e) => e.status === "done" || e.status === "cancelled");

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

  const grid = cells(month);
  const shift = (n: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));
  const goToday = () => { setMonth(new Date(today.getFullYear(), today.getMonth(), 1)); setSelected(today); };

  const panelRows = selected
    ? (byDay.get(dayKey(selected)) ?? []).map((x) => x.e)
    : upcoming.slice(0, 6).map((x) => x.e);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-[-0.02em]">Calendar</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          Every time someone said yes with a time, a link or a deadline — read from their reply, never guessed.
        </p>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-line pt-2">
          <Stat n={upcoming.length} label="upcoming" />
          <Stat n={conflicts.length} label="clashes" />
          <Stat n={done.length} label="done" />
          <span className="marginal ml-auto">
            {cal?.authorized
              ? `synced to Google Calendar (${cal.calendar_id}, ${cal.timezone})`
              : "Google Calendar not connected — confirmations are kept here until it is"}
          </span>
        </div>
      </header>

      {note && <p className="rounded-md border border-line bg-surface px-3 py-2 text-[12.5px]">{note}</p>}

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* ------------------------------------------------------------ month */}
        <section className="plate overflow-hidden">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
            <button
              type="button"
              onClick={goToday}
              className="h-8 cursor-pointer rounded-md border border-line px-3 text-[12.5px] font-medium hover:bg-surface-2"
            >
              Today
            </button>
            <button type="button" onClick={() => shift(-1)} aria-label="Previous month"
              className="grid h-8 w-8 cursor-pointer place-items-center rounded-md text-ink-2 hover:bg-surface-2 hover:text-ink">
              <ChevronLeft size={15} />
            </button>
            <button type="button" onClick={() => shift(1)} aria-label="Next month"
              className="grid h-8 w-8 cursor-pointer place-items-center rounded-md text-ink-2 hover:bg-surface-2 hover:text-ink">
              <ChevronRight size={15} />
            </button>
            <h2 className="ml-1 font-[family-name:var(--font-display)] text-[17px] font-semibold tracking-[-0.02em]">
              {MONTHS[month.getMonth()]} <span className="tnum font-medium text-ink-2">{month.getFullYear()}</span>
            </h2>
          </div>

          <div className="grid grid-cols-7 border-b border-line">
            {DOW.map((d) => (
              <div key={d} className="marginal py-2 text-center">{d}</div>
            ))}
          </div>

          <div className="grid grid-cols-7">
            {grid.map((d, i) => {
              const inMonth = d.getMonth() === month.getMonth();
              const isToday = sameDay(d, today);
              const isSel = selected ? sameDay(d, selected) : false;
              const items = byDay.get(dayKey(d)) ?? [];
              const shown = items.slice(0, 3);
              const more = items.length - shown.length;
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => setSelected(isSel ? null : d)}
                  aria-pressed={isSel}
                  aria-label={`${d.toDateString()}${items.length ? `, ${items.length} event${items.length === 1 ? "" : "s"}` : ""}`}
                  className={`group relative flex min-h-[4.6rem] cursor-pointer flex-col items-stretch border-b border-r border-line p-1 text-left transition-colors sm:min-h-[6.4rem] sm:p-1.5
                    ${i % 7 === 6 ? "border-r-0" : ""} ${i >= 35 ? "border-b-0" : ""}
                    ${inMonth ? "bg-surface" : "bg-surface-2/40"} ${isSel ? "bg-surface-2" : "hover:bg-surface-2/60"}`}
                >
                  <span className="flex items-center justify-between">
                    <span
                      className={`tnum grid h-6 min-w-6 place-items-center rounded-full px-1 text-[12px] leading-none
                        ${isToday ? "bg-ink font-semibold text-on-ink" : inMonth ? "text-ink" : "text-ink-3"}`}
                    >
                      {d.getDate() === 1 && !isToday ? (
                        <>
                          <span className="hidden sm:inline">{MONTHS[d.getMonth()].slice(0, 3)} </span>
                          {d.getDate()}
                        </>
                      ) : d.getDate()}
                    </span>
                  </span>

                  {/* Chips need about 120px to say anything; a phone column is 50. Below sm
                      each event is a dot - filled for confirmed, hollow for proposed, a
                      dash for a clash - and the day panel carries the words. */}
                  <span className="mt-1 hidden flex-col gap-[3px] sm:flex">
                    {shown.map(({ e, date, due }) => (
                      <Chip key={e.id} e={e} time={due ? "due" : e.starts_at ? fmtTime(date) : null} />
                    ))}
                    {more > 0 && <span className="marginal pl-1">+{more} more</span>}
                  </span>
                  <span className="mt-1 flex flex-wrap gap-1 pl-1 sm:hidden" aria-hidden>
                    {items.slice(0, 6).map(({ e }) => <Dot key={e.id} status={e.status} />)}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Events with no time and no deadline cannot be drawn on a day. */}
          {undated.length > 0 && (
            <div className="border-t border-line px-3 py-2.5">
              <p className="marginal mb-1.5">Not yet placed — no time or deadline in the reply</p>
              <div className="flex flex-wrap gap-1.5">
                {undated.map((e) => <Chip key={e.id} e={e} time={null} wide />)}
              </div>
            </div>
          )}
        </section>

        {/* ------------------------------------------------------------ panel */}
        <aside className="plate">
          <div className="flex items-baseline justify-between border-b border-line px-4 py-3">
            <h2 className="text-[13px] font-semibold">
              {selected
                ? selected.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })
                : "Next up"}
            </h2>
            {selected && (
              <button type="button" onClick={() => setSelected(null)} className="marginal cursor-pointer hover:text-ink">
                show next up
              </button>
            )}
          </div>

          {panelRows.length === 0 ? (
            <p className="px-4 py-6 text-[12px] leading-relaxed text-ink-3">
              {selected
                ? "Nothing on this day."
                : rows.length === 0
                  ? "Nothing scheduled yet. When a reply proposes a time, it appears on the grid and you get a notification."
                  : "Nothing upcoming."}
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {panelRows.map((e) => <Detail key={e.id} e={e} busy={busy} onAct={act} />)}
            </ul>
          )}

          {conflicts.length > 0 && !selected && (
            <div className="border-t border-line px-4 py-3">
              <p className="marginal">Clashes</p>
              <p className="mt-1 text-[11.5px] leading-relaxed text-ink-3">
                {conflicts.length} event{conflicts.length === 1 ? "" : "s"} on a taken slot. A reschedule is drafted into
                the Queue for each — nothing is confirmed to the other side by the machine.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- pieces */

function Dot({ status }: { status: string }) {
  if (status === "conflict") return <span className="mt-[3px] h-[2px] w-[9px] rounded-full bg-ink-3" />;
  const filled = status === "confirmed";
  const faded = status === "done" || status === "cancelled";
  return (
    <span
      className={`h-[7px] w-[7px] rounded-full border ${
        filled ? "border-ink bg-ink" : faded ? "border-line" : "border-ink-2"
      }`}
    />
  );
}

/** A day chip. Status is fill, not colour: solid for confirmed, outlined for proposed,
 *  struck for a clash, faded for done. */
function Chip({ e, time, wide = false }: { e: EventRow; time: string | null; wide?: boolean }) {
  const tone =
    e.status === "confirmed" ? "bg-ink text-on-ink border-ink"
    : e.status === "conflict" ? "border-line-strong text-ink-2 line-through decoration-ink-3"
    : e.status === "done" || e.status === "cancelled" ? "border-line text-ink-3"
    : "border-line-strong text-ink";
  return (
    <span
      title={`${KIND[e.kind] ?? e.kind} · ${e.company ?? ""} · ${e.status}`}
      className={`flex items-center gap-1 rounded-[3px] border px-1.5 py-[2px] text-[11px] leading-tight ${tone} ${wide ? "" : "min-w-0"}`}
    >
      {time && <span className="tnum shrink-0 text-[10px] opacity-80">{time}</span>}
      <span className="truncate">{e.company ?? KIND[e.kind] ?? e.kind}</span>
    </span>
  );
}

function Detail({ e, busy, onAct }: { e: EventRow; busy: number | null; onAct: (id: number, path: string) => void }) {
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="chip">{KIND[e.kind] ?? e.kind}</span>
        <span className={`chip ${e.status === "conflict" ? "chip-strike" : e.status === "confirmed" ? "chip-solid" : ""}`}>{e.status}</span>
        <span className="tnum ml-auto text-[12px]">{e.local ?? "time not stated"}</span>
      </div>
      <Link href={`/companies/${e.company_id}`} className="mt-1.5 block text-[13px] font-medium hover:underline">
        {e.company ?? "—"}
      </Link>
      {e.contact && <p className="text-[11.5px] text-ink-3">{e.contact}</p>}
      {(e.quote || e.needs_action) && (
        <p className="mt-1.5 border-l-2 border-line pl-2.5 text-[11.5px] leading-relaxed text-ink-3 italic">
          {e.quote ?? e.needs_action}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {e.link && (
          <a href={e.link} target="_blank" rel="noreferrer"
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-[11.5px] hover:bg-surface-2">
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
            <button onClick={() => onAct(e.id, `/api/events/${e.id}/status?status=done`)} className="marginal cursor-pointer hover:text-ink">
              mark done
            </button>
          )}
        </OperatorOnly>
      </div>
    </li>
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
