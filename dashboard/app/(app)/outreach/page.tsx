"use client";

/*  Outreach — the send pipeline, end to end.
 *
 *  Tejmul, 2026-10-06: after mail went out, the replies seemed to vanish; nothing on
 *  the dashboard showed where a message was once it left the queue. This page is
 *  that one picture: every stage a message passes through, how many sit in each, how
 *  the replies split, and — first, when it applies — the one reason mail might not be
 *  leaving at all. Counts are public; names are not, so on the public instance the
 *  "recent" list is empty and the page says so instead of looking broken.
 */

import Link from "next/link";
import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { ArrowRight, ChevronRight, MailWarning } from "lucide-react";
import { useApi, type OutreachPipeline } from "@/lib/api";
import { relTime } from "@/lib/utils";
import { rise } from "@/components/anim";
import { useAccess } from "@/components/access";
import { PageHead } from "@/components/shell";
import { BudgetMeter, SentimentBadge } from "@/components/signals";
import { SentimentSplit, count } from "@/components/outreach";
import { ErrorNote, Loading } from "@/components/ui/primitives";
import { LaunchChecklist } from "@/components/launch";

const STATUS: Record<string, string> = {
  approved: "approved, waiting to send", sent: "sent", replied: "replied", failed: "failed to send",
};

export default function OutreachPage() {
  const root = useRef<HTMLDivElement>(null);
  const { publicInstance } = useAccess();
  const { data, error, status, reload } = useApi<OutreachPipeline>("/api/outreach/pipeline");

  useGSAP(() => { if (data) rise("[data-anim]", { stagger: 0.05 }); }, { scope: root, dependencies: [!!data] });

  const head = (
    <PageHead
      title="Outreach"
      description="Every message from draft to reply, in the order it happens. Nothing leaves without your approval, and never more than the daily cap."
    />
  );

  if (status === 404) {
    return (
      <>
        {head}
        <div className="plate px-6 py-14 text-center">
          <p className="text-[14px] font-medium">This server doesn&rsquo;t report the outreach pipeline yet</p>
          <p className="mx-auto mt-2 max-w-[54ch] text-[12.5px] leading-relaxed text-ink-3">
            It will appear here once the API is updated. Until then, sent mail is on the{" "}
            <Link href="/tracker" className="underline decoration-line underline-offset-2 hover:text-ink">tracker</Link>.
          </p>
        </div>
      </>
    );
  }
  if (error && !data) return <>{head}<div className="plate"><ErrorNote message={error} onRetry={reload} /></div></>;
  if (!data) return <>{head}<div className="plate"><Loading label="Reading the pipeline" rows={6} /></div></>;

  const st = data.stages ?? {};
  const dry = data.send_mode === "dryrun";
  const connected = !!data.gmail?.connected;
  const sent = count(st.sent);
  const recent = data.recent ?? [];
  const cap = count(data.today?.cap);

  const STAGES = [
    { key: "drafted", label: "Drafted", n: count(st.drafted), hint: "written by the model, every one" },
    { key: "in_review", label: "In review", n: count(st.in_review), hint: "waiting at the approval gate", href: "/queue", gate: true },
    { key: "approved", label: "Approved", n: count(st.approved), hint: "you said yes; queued for the send window" },
    { key: "sent", label: "Sent", n: sent, hint: "left your Gmail" },
    { key: "replied", label: "Replied", n: count(st.replied), hint: "someone wrote back", href: publicInstance ? undefined : "/replies" },
    { key: "meetings", label: "Meeting booked", n: count(data.meetings), hint: "a time on the calendar", href: "/calendar" },
  ];

  return (
    <div ref={root}>
      {head}

      {/* -------------------------------------------- why mail might not be leaving */}
      {(dry || !connected) && !publicInstance && (
        <section data-anim className="plate mb-4 flex flex-wrap items-start gap-3 px-4 py-3.5">
          <MailWarning size={16} className="mt-0.5 shrink-0 text-ink" />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium">
              {!connected ? "Gmail isn’t connected, so nothing can be sent or read back yet." : "Sending is in dry run."}
            </p>
            <p className="mt-0.5 max-w-[70ch] text-[12.5px] leading-relaxed text-ink-2">
              {!connected
                ? "Drafts are still written and still wait for your approval. Connect your Gmail with an app password and approved mail goes out inside the daily cap — and replies start appearing here."
                : "Approved messages are marked as sent but never actually leave your machine. Switch to live sending when you are ready."}
            </p>
          </div>
          <Link href="/settings" className="btn-ink shrink-0">
            Email setup <ArrowRight size={13} />
          </Link>
        </section>
      )}

      {/* -------------------------------------------- launch: what is ready, what you do */}
      {!publicInstance && <LaunchChecklist />}

      {/* -------------------------------------------- the stage strip */}
      <section data-anim className="plate overflow-x-auto">
        <ol className="grid min-w-[760px] grid-cols-6">
          {STAGES.map((s, i) => {
            const body = (
              <>
                <span className="marginal flex items-center gap-1">
                  <span className="tnum">{String(i + 1).padStart(2, "0")}</span> {s.label}
                </span>
                <span className={`tnum mt-2 block text-[30px] leading-none font-medium tracking-[-0.03em] ${s.n ? "" : "text-ink-3"}`}>
                  {s.n.toLocaleString()}
                </span>
                <span className="mt-2 block text-[11.5px] leading-snug text-ink-3">{s.hint}</span>
                {s.gate && s.n > 0 ? <span className="chip chip-solid mt-2">needs you</span> : null}
              </>
            );
            return (
              <li key={s.key} className="relative border-l border-line first:border-l-0">
                {s.href ? (
                  <Link href={s.href} className="group block h-full px-4 py-4 transition-colors hover:bg-surface-2">{body}</Link>
                ) : (
                  <div className="h-full px-4 py-4">{body}</div>
                )}
                {i < STAGES.length - 1 && (
                  <ChevronRight size={14} className="absolute top-1/2 -right-[7px] z-10 -translate-y-1/2 rounded-full bg-surface text-ink-3" aria-hidden />
                )}
              </li>
            );
          })}
        </ol>
        {(count(st.failed) > 0 || count(st.bounced) > 0 || count(st.rejected) > 0) && (
          <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">
            Off the path:{" "}
            {[
              count(st.rejected) && `${count(st.rejected)} rejected at review`,
              count(st.failed) && `${count(st.failed)} failed to send`,
              count(st.bounced) && `${count(st.bounced)} bounced`,
            ].filter(Boolean).join(" · ")}
          </p>
        )}
      </section>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        {/* ------------------------------------------ how the replies split */}
        <section data-anim className="plate px-4 py-4 lg:col-span-2">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-[13.5px] font-semibold">How people answered</h2>
            {!publicInstance && (
              <Link href="/replies" className="text-[12px] text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
                Read the replies
              </Link>
            )}
          </div>
          <p className="mt-0.5 mb-4 text-[12px] text-ink-3">
            {sent ? `Out of ${sent.toLocaleString()} sent.` : "Nothing sent yet — replies are counted here as they arrive."}
          </p>
          <SentimentSplit sentiment={data.sentiment} />
        </section>

        {/* ------------------------------------------ today and the inbox */}
        <section data-anim className="plate px-4 py-4">
          <h2 className="text-[13.5px] font-semibold">Today</h2>
          <div className="mt-3">
            {cap ? (
              <BudgetMeter cap={cap} sent={Math.min(cap, count(data.today?.used))} inWindow />
            ) : (
              <span className="text-[12px] text-ink-3">No daily cap reported.</span>
            )}
          </div>
          {data.today?.guessed_cap ? (
            <p className="mt-2 text-[11.5px] text-ink-3">
              {count(data.today.guessed_used)} of {count(data.today.guessed_cap)} to guessed addresses
            </p>
          ) : null}
          <dl className="mt-4 grid grid-cols-[6.5rem_1fr] gap-y-1.5 text-[12px]">
            <dt className="text-ink-3">Sending</dt>
            <dd className="font-medium">{dry ? "dry run" : connected ? "live" : "not connected"}</dd>
            <dt className="text-ink-3">Inbox checked</dt>
            <dd>{data.gmail?.last_poll_at ? relTime(data.gmail.last_poll_at) : "not yet"}</dd>
            {data.gmail?.last_poll_error && !publicInstance ? (
              <>
                <dt className="text-ink-3">Last check</dt>
                <dd className="text-ink-2">{data.gmail.last_poll_error.slice(0, 140)}</dd>
              </>
            ) : null}
          </dl>
        </section>
      </div>

      {/* -------------------------------------------- the latest messages */}
      <section data-anim className="plate mt-4">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-[13.5px] font-semibold">Latest messages</h2>
        </div>
        {recent.length ? (
          <ul className="divide-y divide-line">
            {recent.map((r) => (
              <li key={r.email_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-2.5 sm:grid-cols-[minmax(0,1.4fr)_9rem_6rem_7rem]">
                <span className="min-w-0 truncate text-[13px]">
                  <span className="font-medium">{r.company_name ?? "Unknown company"}</span>
                  {r.contact_name ? <span className="text-ink-3"> · {r.contact_name}</span> : null}
                </span>
                <span className="text-[12px] text-ink-2">{STATUS[r.status] ?? r.status}</span>
                <span className="tnum text-[12px] text-ink-3">{r.sent_at ? relTime(r.sent_at) : "—"}</span>
                <span className="justify-self-start sm:justify-self-end">
                  {r.reply_sentiment || r.reply_at ? <SentimentBadge sentiment={r.reply_sentiment} /> : <span className="text-[12px] text-ink-3">no reply yet</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : publicInstance ? (
          <p className="px-4 py-8 text-center text-[12.5px] text-ink-3">
            Who was written to stays private on this public copy — the counts above are the live ones.
          </p>
        ) : (
          <div className="px-4 py-10 text-center">
            <p className="text-[13.5px] font-medium">Nothing sent yet</p>
            <p className="mx-auto mt-1 max-w-[52ch] text-[12.5px] text-ink-3">
              {count(st.in_review)
                ? `${count(st.in_review)} draft${count(st.in_review) === 1 ? " is" : "s are"} waiting for your approval.`
                : "Draft an email from a company page; it waits in the review queue for your approval."}
            </p>
            <Link href="/queue" className="btn-ink mt-4">Open the review queue <ArrowRight size={13} /></Link>
          </div>
        )}
      </section>
    </div>
  );
}
