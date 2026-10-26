"use client";

import Link from "next/link";

import { Empty, ErrorNote, Loading, Panel, PanelHead } from "@/components/ui/primitives";

/* 2026-10-25, Tejmul: "whatever should go the next day should be scheduled the day before".
   The next send day, worked out from the approved queue with the sender's own rules: when
   each one goes (India slots, the recipient's morning abroad), what waits and why, and how
   many more approvals would fill the day. A preview: an approval or a bounce changes it. */

export type SendPlan = {
  day: string;
  weekday: string;
  cap: number;
  open_slots: number;
  drafts_to_review: number;
  drafts_sendable: number;
  bounce_brake: string | null;
  planned: { email_id?: number; at: string; company?: string | null; to?: string | null; address?: string; tz?: string; their_time?: string; subject?: string }[];
  waiting: { email_id?: number; company?: string | null; to?: string | null; why: string }[];
  targets?: { company_id: number; name: string; ready: number; take: number; drafts?: number; approved?: number; sent?: number }[];
};

export function SendPlanPanel({ data, error, loading }: { data: SendPlan | null; error: string | null; loading: boolean }) {
  if (loading) return <Loading />;
  if (error) return <ErrorNote message={error} />;
  if (!data) return null;
  const fill = Math.min(data.open_slots, data.drafts_sendable);
  return (
    <div className="grid gap-4">
      <Panel>
        <PanelHead
          eyebrow={`${data.weekday} ${data.day}`}
          title={`${data.planned.length} of ${data.cap} planned`}
          action={<span className="text-xs text-ink-2">10:00–19:00 IST · abroad in their own morning · 15+ min apart</span>}
        />
        <div className="px-4 py-3 text-sm text-ink-2">
          {data.open_slots === 0
            ? "The day is full."
            : `${data.open_slots} slots are still open. ${fill > 0 ? `Approve ${fill} of the ${data.drafts_sendable} sendable drafts in “Waiting for your review” to fill them.` : "No sendable draft is waiting yet — the drafter tops the queue up through the day."}`}
          {data.bounce_brake ? <div className="mt-1 text-xs">Bounce brake: {data.bounce_brake}</div> : null}
        </div>
        {data.planned.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-2">
              <tr className="border-t border-line">
                <th className="px-4 py-2 font-normal">Goes at (IST)</th>
                <th className="px-4 py-2 font-normal">Their time</th>
                <th className="px-4 py-2 font-normal">To</th>
                <th className="px-4 py-2 font-normal">Company</th>
                <th className="px-4 py-2 font-normal">Address</th>
              </tr>
            </thead>
            <tbody>
              {data.planned.map((r, i) => (
                <tr key={r.email_id ?? i} className="border-t border-line">
                  <td className="px-4 py-2 tabular-nums text-ink">{r.at.slice(11)}</td>
                  <td className="px-4 py-2 tabular-nums text-ink-2">{r.their_time ?? "—"}{r.tz && r.tz !== "Asia/Kolkata" ? ` · ${r.tz.split("/")[1]?.replace("_", " ")}` : ""}</td>
                  <td className="px-4 py-2 text-ink">{r.to ?? "—"}</td>
                  <td className="px-4 py-2 text-ink-2">{r.company ?? "—"}</td>
                  <td className="px-4 py-2 text-ink-2">{r.address ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty title="Nothing approved for that day yet." />
        )}
      </Panel>
      {data.targets?.length ? (
        <Panel>
          <PanelHead
            eyebrow="Picked the evening before"
            title={`Target companies (${data.targets.length})`}
            action={<span className="text-xs text-ink-2">up to {Math.max(...data.targets.map((t) => t.take))} engineers each</span>}
          />
          <ul className="divide-y divide-line text-sm">
            {data.targets.map((t) => (
              <li key={t.company_id} className="flex items-center justify-between gap-3 px-4 py-2">
                <Link href={`/companies/${t.company_id}`} className="text-ink hover:underline">{t.name}</Link>
                <span className="text-xs tabular-nums text-ink-2">
                  {t.ready} ready · {t.drafts ?? 0} to review · {t.approved ?? 0} approved{t.sent ? ` · ${t.sent} sent` : ""}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      {data.waiting.length ? (
        <Panel>
          <PanelHead title={`Approved but waiting (${data.waiting.length})`} />
          <ul className="divide-y divide-line text-sm">
            {data.waiting.map((r, i) => (
              <li key={r.email_id ?? i} className="px-4 py-2">
                <span className="text-ink">{r.to ?? "—"}</span>
                {r.company ? <span className="text-ink-2"> · {r.company}</span> : null}
                <div className="text-xs text-ink-2">{r.why}</div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
