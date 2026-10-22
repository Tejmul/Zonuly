import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, Lead, P, Step, Steps } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Replies & follow-ups" };

export default function Replies() {
  return (
    <Doc slug="replies">
      <Lead>
        What comes back is read every cycle: bounces are caught, opt-outs honoured forever, replies classified with
        caution, and a “yes” with a time becomes a calendar event.
      </Lead>

      <H2>Reading the inbox</H2>
      <Steps>
        <Step title="Poll">Over IMAP, every free tick and every hour from the API, for the threads we sent.</Step>
        <Step title="Bounces">A delivery failure marks the email failed and moves the address to <C>dead_email</C>, so it is never used again. Bounces feed the bounce brake; they are never classified as replies.</Step>
        <Step title="Opt-outs">A phrase like “please don’t contact me” marks the reply negative and puts the person <em>and</em> the company on do-not-contact for good, withdrawing any pending drafts.</Step>
        <Step title="Classify">Everything else is classified by the cheap model. Anything it can’t parse — or isn’t sure about — is neutral.</Step>
      </Steps>
      <Defs
        items={[
          [<C key="p">positive</C>, "Interest: they will refer, forward, or want to talk."],
          [<C key="n">negative</C>, "A no, or an opt-out."],
          [<C key="c">closed</C>, "Hiring done, not now, wrong team."],
          [<C key="u">neutral</C>, "Ambiguous — read it yourself on the Replies page."],
        ]}
      />

      <H2>One follow-up, at most</H2>
      <P>
        After five quiet days, one follow-up is drafted on the same thread and placed in the review queue like any other
        email — it is never sent without approval, and there is never a second.
      </P>

      <H2>From “yes” to calendar</H2>
      <P>
        A positive reply with a time, a meeting link or a deadline becomes an event (call, assessment or interview). With
        Google Calendar connected it is pushed to your calendar; if it clashes with something already there, a reschedule
        draft lands in the Queue — the machine never confirms a time on its own.
      </P>
      <Callout kind="note">
        The <A href="/docs/dashboard">Tracker</A> shows every email&apos;s lifecycle — draft, approved, sent, replied, bounced,
        opted out — and its “What works” section shows reply rates by wording, subject and role once there is enough data.
      </Callout>
    </Doc>
  );
}
