import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, Lead, P, Step, Steps } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Drafting & the review gate" };

export default function Outreach() {
  return (
    <Doc slug="outreach">
      <Lead>
        Each email is written for one person, about one role, from evidence — and then <strong>waits for you</strong>.
        Drafting is triggered by you, never by a background job, and no code path sends an unapproved email.
      </Lead>

      <H2>Who gets an email</H2>
      <P>
        A batch picks targets in order: the ★ list first, then complete companies by selection odds. One ask per company
        per batch, to its best ready referrer — the highest referral rank, a verified address before a guessed one. A
        company gets at most two open asks at once, and nobody is asked twice inside 30 days.
      </P>

      <H2>How it is written</H2>
      <Steps>
        <Step title="Gather">The person, their role, the company, the role and its needs brief, your relevant work and repositories.</Step>
        <Step title="Fill the contract">A fixed set of slots — who you are, real work that matches their need, a polite referral ask, a humility line, your resume. <strong>A slot that can’t be filled from evidence means no email</strong> (stored as incomplete), not a vaguer one.</Step>
        <Step title="Compose">Your own direct template, rotated across wordings and subjects so no two recipients get identical text; 80–170 words; plain text.</Step>
        <Step title="Score and flag">A quality score (0–100) and review flags. Flags never rewrite — they point.</Step>
      </Steps>

      <H2>Review flags</H2>
      <Defs
        items={[
          ["faithfulness", "A claim not supported by the evidence"],
          ["uncovered_claim", "Claims a need the needs brief marks uncovered"],
          ["ai_tell", "Words that read as machine-written"],
          ["length · one_ask · subject", "Outside the word range, more than one ask, a weak subject"],
          ["guessed", "The address is pattern-guessed — know the risk before approving"],
          ["affiliation", "The person’s current employment is not confirmed"],
        ]}
      />

      <H2>The review gate</H2>
      <P>
        The <A href="/docs/dashboard">Queue</A> shows each draft with its flags and evidence above an editor. You can edit
        freely — what you save is exactly what goes out — then approve or reject. Approval refuses an empty subject or
        body, an expired draft (7 days), or a person who is suppressed, cooling down or no longer ready.
      </P>
      <Callout kind="rule" title="The only human step">
        Approval is the one moment your judgement is needed, and the whole system exists to make it take seconds. Pattern
        approval (approving a kind of email once) exists but is switched off: every email gets its own click.
      </Callout>
      <P>
        Commands: <C>outreach batch</C> builds the week&apos;s drafts, <C>outreach proposal</C> shows how it would write without
        drafting, and the Email proposal page shows the evidence, structure and exact text before anything is drafted.
      </P>
    </Doc>
  );
}
