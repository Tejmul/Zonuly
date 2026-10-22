import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";
import { Timeline } from "@/components/docs/diagrams";

export const metadata: Metadata = { title: "Sending & deliverability" };

export default function Sending() {
  return (
    <Doc slug="sending">
      <Lead>
        Approved emails leave from your own Gmail, <strong>one at a time, spread through the working day</strong>, the
        way a person sends them. Deliverability is the real limit on the whole system, so sending is the most guarded stage.
      </Lead>

      <H2>The rules</H2>
      <Table
        head={["Setting", "Value", "Why"]}
        mono={[0]}
        rows={[
          ["daily_send_cap", "25", "A personal Gmail sends cold mail safely at about this rate."],
          ["send_window", "10:00–19:00, Mon–Fri", "People at their desks; no cold mail at weekends."],
          ["pacing.front_load", "false", "Spread across the window — never a burst at 10:00."],
          ["pacing.max_per_run", "1", "A laptop waking from sleep never fires a backlog."],
          ["pacing.jitter_fraction", "0.35", "Slot times vary, so it doesn’t look automated."],
          ["guessed_daily_cap", "25", "A separate allowance for pattern-guessed addresses."],
          ["bounce_brake", "6 in 7 days", "Past this, guessed addresses stop; verified ones still go."],
          ["contact / company cooldown", "30 days", "Nobody is asked twice in a month."],
        ]}
      />

      <H2>A sending day</H2>
      <Timeline
        caption="The 25 slots for Tue 20 Oct 2026, as computed by warmup.slots(). Every run of the send job agrees on them."
        items={[
          { at: "10:16", label: "first email" }, { at: "10:39", label: "" }, { at: "10:48", label: "" },
          { at: "11:20", label: "" }, { at: "…", label: "about one every 20 minutes, jittered" },
          { at: "18:26", label: "" }, { at: "18:48", label: "25th — the last of the day", strong: true },
        ].map((x) => ({ ...x, label: x.label || "email" }))}
      />

      <H2>What happens to one email</H2>
      <Steps>
        <Step title="Is a slot due?">Every 5 minutes the send job asks how many slots have passed today, minus how many were used, capped at one per run.</Step>
        <Step title="Pick the next approved email">Follow-ups first, then cold asks. A guessed address is skipped if the guessed cap is spent or the brake is on.</Step>
        <Step title="Re-check the person">Still ready, not suppressed, not cooling down — things can change between approval and sending.</Step>
        <Step title="Claim and reserve">Atomically move the email from approved to sending, and reserve one unit of today&apos;s cap in the ledger <em>before</em> the mail leaves.</Step>
        <Step title="Send">Over Gmail SMTP with your app password (or, in dry-run mode, written to <C>outbox/</C>). On failure the reservation is released and the email marked failed.</Step>
      </Steps>

      <Callout kind="warn" title="What happens if you push it">
        On 19 Oct 2026, with the old settings (cap 60, front-loaded, 40–90 s apart), about thirty near-identical emails went
        into one company within an hour. Four guessed addresses bounced and Gmail refused all further mail with{" "}
        <C>550 5.4.5 Daily user sending limit exceeded</C> after 24 sends. The rules above are the fix. Finish a company over
        more days — never by raising the cap.
      </Callout>

      <H2>Pausing and checking</H2>
      <P>
        <C>outreach preflight</C> reports live-send readiness (red / amber / green with fixes); <C>outreach ledger</C> shows
        today&apos;s use. To pause sending, unload <C>com.zonuly.tick.send</C> (see <A href="/docs/runbook">Runbook</A>) or reply
        “pause” to a notification email when the bot is on.
      </P>
    </Doc>
  );
}
