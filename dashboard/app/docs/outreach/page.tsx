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
        per batch, to its best ready referrer, chosen in this order:
      </P>
      <Steps>
        <Step title="A real address before a guessed one">Anyone whose address is pattern-guessed goes to the back of the line, whatever their role.</Step>
        <Step title="The tech team first">The referral rank, which since 23 Oct 2026 puts engineers ahead of everyone: senior, staff, lead and founding engineers, then software engineers, then founders and CTOs, then engineering managers, technical recruiters and HR (see <A href="/docs/people">People &amp; referrers</A>).</Step>
        <Step title="Then the surest">A verified address, confirmed current employment, a known name.</Step>
      </Steps>
      <P>
        A company gets at most two open asks at once, and nobody is asked twice inside 30 days. And since 23 Oct 2026 a
        guessed company address is <strong>the last resort</strong>: when someone ready to refer at a company already has a
        real address, no address is guessed for anyone there (see <A href="/docs/email-addresses">Email addresses</A>).
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
          ["deliverability", "What a spam filter would see: an attachment, more than two links, a link shortener, link words naming a different site, a greeting without their name, broken characters, the same subject already sent into that company, three or more asks already going there — or a near-copy (80%+ the same) of mail already approved or sent into the same company’s domain"],
          ["near_duplicate", "80% or more the same, phrase for phrase, as mail already approved or sent to other companies. Gmail sees everything you send, so vary the wording"],
          ["affiliation", "The person’s current employment is not confirmed"],
        ]}
      />

      <Callout kind="warn" title="Why the deliverability flags exist">
        On 23 Oct 2026 Gmail refused 9 of 24 asks into one company&apos;s domain as spam (<C>5.7.1 Message blocked</C>) after
        about fifteen near-identical emails went there within hours. A company&apos;s mail server sees every inbox at once and
        files a group of look-alikes as bulk. The flags point at that before you approve; the{" "}
        <A href="/docs/sending">sender</A> also holds mail after a block.
      </Callout>

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
