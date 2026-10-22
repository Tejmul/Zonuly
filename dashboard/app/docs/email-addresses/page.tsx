import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Email addresses" };

export default function EmailAddresses() {
  return (
    <Doc slug="email-addresses">
      <Lead>
        A wrong address doesn&apos;t just fail — it bounces, and bounces are how Gmail decides you are a spammer. So
        addresses follow a strict order: <strong>real ones first, a confirmed pattern second, nothing invented</strong>.
      </Lead>

      <H2>Where addresses come from</H2>
      <Table
        head={["Source", "Label", "Notes"]}
        rows={[
          ["GitHub commit metadata", "verified", "An engineer’s own commit on the company’s repo. Old commit addresses count only as scraped."],
          ["The company’s own site", "verified", "Named next to an address on their team or contact page."],
          ["The open web", "scraped", "Any @their-domain address a search turns up."],
          ["Hunter.io", "scraped → voted", "Its addresses are voted on like any other; its own pattern claim is never trusted. (On hold: no key.)"],
          ["Confirmed pattern", "pattern-guessed", "Built from the company’s format — see below."],
        ]}
      />

      <H2>Learning a company&apos;s pattern</H2>
      <Steps>
        <Step title="Collect real examples">Only named addresses that were <em>read</em> on the company&apos;s domain — never ones a pattern made.</Step>
        <Step title="Vote">Each example votes for the format that explains it (<C>{"{first}"}</C>, <C>{"{first}.{last}"}</C>, <C>{"{f}{last}"}</C>…). An address two formats could explain is skipped.</Step>
        <Step title="Confirm">A pattern is confirmed only with <strong>at least two agreeing examples</strong> (or one plus the company&apos;s own page stating the format), at least half the votes, and twice the runner-up.</Step>
        <Step title="Apply, carefully">Ready referrers with a real first and last name get the pattern address — only after an MX lookup proves the domain takes mail. No SMTP probing. Labelled <C>pattern-guessed</C>.</Step>
        <Step title="Retract">An address an unconfirmed pattern made is taken back, unless a draft already went to it.</Step>
      </Steps>

      <Callout kind="warn" title="Patterns can go stale">
        On 19 Oct 2026 Sarvam AI&apos;s confirmed <C>{"{first}@"}</C> format produced four bounces — one of the four examples that
        had confirmed it belonged to someone who had since left. That is why guessed addresses have their own daily cap and
        a bounce brake, and why a bounce moves the address to <C>dead_email</C> so it is never tried again.
      </Callout>

      <H2>At send time</H2>
      <P>
        The address&apos;s confidence is frozen onto the email when it is drafted, shown in the review queue, and checked again
        by the sender: guessed addresses stop once the day&apos;s guessed cap is spent or the bounce brake is on. See{" "}
        <A href="/docs/sending">Sending</A>.
      </P>
    </Doc>
  );
}
