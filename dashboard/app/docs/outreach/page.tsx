import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, Lead, P, Step, Steps } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Drafting & the review gate" };

export default function Outreach() {
  return (
    <Doc slug="outreach">
      <Lead>
        Each email is written for one person, about one role, from evidence — and then <strong>has to pass the review
        gate</strong>. Since 26 Oct 2026 the gate is run by written rules (<C>jobhunter/outreach/autoreview.py</C>): every
        draft is approved or rejected, with its reason. No code path sends an unapproved email.
      </Lead>

      <H2>Who gets an email</H2>
      <P>
        A batch picks targets in order: the ★ list first, then complete companies by selection odds. One ask per company
        per batch, to its best ready referrer, chosen in this order:
      </P>
      <Steps>
        <Step title="A proved address, or nobody">Only someone whose address has been proved can be asked at all — a found but unproved address is never written to, and a guessed one never reaches the database (see <A href="/docs/email-addresses">Email addresses</A>).</Step>
        <Step title="The tech team first">The referral rank, which since 23 Oct 2026 puts engineers ahead of everyone: senior, staff, lead and founding engineers, then software engineers, then founders and CTOs, then engineering managers, technical recruiters and HR (see <A href="/docs/people">People &amp; referrers</A>).</Step>
        <Step title="Then the surest">A verified address, confirmed current employment, a known name.</Step>
      </Steps>
      <P>
        A company gets at most two open asks at once, and nobody is asked twice inside 30 days. Drafts are written by the
        shortlist job as it completes each company, by <C>outreach batch</C>, or from the Email proposal page.
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
          ["guessed", "The address is a pattern guess. Kept as a last check: since 2 Nov 2026 such an address is refused at the database, so no draft should carry one"],
          ["deliverability", "What a spam filter would see: an attachment, more than two links, a link shortener, link words naming a different site, a greeting without their name, broken characters, the same subject already sent into that company, three or more asks already going there — or a near-copy (80%+ the same) of mail already approved or sent into the same company’s domain"],
          ["near_duplicate", "80% or more the same, phrase for phrase, as mail already approved or sent to other companies. Gmail sees everything you send, so vary the wording"],
          ["affiliation", "The person’s current employment is not confirmed"],
        ]}
      />

      <Callout kind="warn" title="Why the deliverability flags exist">
        On 23 Oct 2026 Gmail refused 9 of 24 asks into one company&apos;s domain as spam (<C>5.7.1 Message blocked</C>) after
        about fifteen near-identical emails went there within hours. A company&apos;s mail server sees every inbox at once and
        files a group of look-alikes as bulk. The review rules reject a draft carrying those signals; the{" "}
        <A href="/docs/sending">sender</A> also holds mail after a block.
      </Callout>

      <H2>The review gate</H2>
      <P>
        Every cold draft is read by the review rules, the way the queue used to be read by hand, and is approved or
        rejected. It is approved only when all of it passes:
      </P>
      <Defs
        items={[
          ["The referrer", "Confirmed current at the company, not suppressed (bounced, opted out, blocked)."],
          ["The address", "A real one — never a pattern guess."],
          ["The writing", <>The composer&apos;s quality at least <C key="q">outreach.autoreview.min_quality</C> (75), no blocking flag (faithfulness, uncovered claim, AI tell, incomplete), and no spam signal read again at review time — a near-copy or the same subject into the same company.</>],
          ["The role", "A fresher role: not senior, lead, staff or manager, and not one asking 3+ years."],
          ["The letter", "A greeting by first name, the current resume link, no unfilled template slot."],
        ]}
      />
      <P>
        A rejection is written on the draft and as a line in <C>logs/autoreview.log</C>, and that person is not drafted
        again. A draft only held for now — another ask at the company still open, a pause after a spam block — stays a
        draft for the next run. The review runs at the end of each pass of the shortlist job; it never sends anything.
      </P>
      <Callout kind="rule" title="The loop">
        Each rejection means a stage made a draft it should not have — a senior role picked, a broken template, an address
        it should not trust. The log is read every session and the stage that produced the rejection is fixed, so the fix
        serves everyone after. The gate is never cut: only an approved email can be sent.
      </Callout>
      <P>
        On your own machine the <A href="/docs/dashboard">Queue</A> still shows each draft with its flags and evidence above
        an editor, to edit, approve or reject by hand; what you save is exactly what goes out. Approval refuses an empty
        subject or body, an expired draft (7 days), or a person who is suppressed, cooling down or no longer ready.
      </P>
      <P>
        Commands: <C>outreach batch</C> builds the week&apos;s drafts, <C>outreach proposal</C> shows how it would write without
        drafting, and the Email proposal page shows the evidence, structure and exact text before anything is drafted.
      </P>
    </Doc>
  );
}
