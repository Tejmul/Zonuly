import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Vocabulary" };

export default function Vocabulary() {
  return (
    <Doc slug="vocabulary">
      <Lead>
        Three nouns carry the whole product — <strong>companies</strong>, <strong>roles</strong> and{" "}
        <strong>leads</strong> — and a small set of labels says how much we know about each.
      </Lead>

      <H2>The three nouns</H2>
      <Defs
        items={[
          ["Company", "An employer we track: website, description, funding, pay power, hiring proof, and the people there. The unit everything else hangs off."],
          ["Role", <>A job posting, scraped from the company&apos;s own board or a public source. In code it is a <C>Job</C>. Roles live inside each company&apos;s page.</>],
          ["Lead", <>A <strong>person</strong> who could refer you — not a company, not a job. Precisely: a contact who works there now (verified or likely current), in a role that can refer, with an address. In code all people are <C>contacts</C>; leads are the ready ones.</>],
        ]}
      />

      <H2>Company tiers</H2>
      <P>Every company gets a grade from <C>targeting.py</C>. The grade orders the work; a low grade is never deleted.</P>
      <Table
        head={["Tier", "Meaning"]}
        rows={[
          ["tier1", "A posting states ₹30 LPA or more for the role we would get."],
          ["tier2", "Stated pay in the second band. Decision D24 sets it at ₹24–30 LPA; the live config currently puts both thresholds at ₹30, so tier 2 is empty until that is changed."],
          ["prospect", "No pay figure, but engineers on staff and hiring verified on their own site — worth chasing."],
          ["reject", "Stated pay below the bar. Kept in the database, never chased."],
          ["unknown", "Not enough evidence yet to grade."],
        ]}
      />
      <P>
        A company can also carry a status that takes it out of the queues: <C>overrated</C> (too famous or too big),{" "}
        <C>below_pay_bar</C> (an India company under the pay floor — hidden, not deleted), <C>quarantined</C> or{" "}
        <C>merged</C> (a duplicate folded into another row).
      </P>

      <H2>Pay power</H2>
      <P>A 0–100 estimate of whether a company <em>can</em> pay well, from what a posting says or what it has raised.</P>
      <Table
        head={["Band", "Score", "When"]}
        rows={[
          ["pays", "100", "A posting states ₹30 L or more."],
          ["deep pockets", "85", "Raised $10M+ in the last 24 months, or Series A+ in 30 months, or valued ≥ $50M."],
          ["funded", "65", "Raised $3–10M in 30 months, or a YC batch in 30 months with 10+ people."],
          ["thin", "30", "Under $2M, pre-seed, or the raise is older than 30 months."],
          ["unknown", "0", "Nothing found yet."],
        ]}
      />
      <Callout kind="note">An India company never rises above <strong>thin</strong> without a stated figure — funding alone doesn&apos;t prove Indian salaries.</Callout>

      <H2>Confidence of an address</H2>
      <P>Every email address carries exactly one of three labels. They are never merged and never defaulted.</P>
      <Defs
        items={[
          [<C key="v">verified</C>, "Seen on the person or company’s own material — a commit, their site, a published address."],
          [<C key="p">pattern-guessed</C>, "Built from the company’s address format, which at least two real addresses must confirm, then checked for a mail server (MX)."],
          [<C key="s">scraped</C>, "Found on the open web, not yet confirmed."],
        ]}
      />

      <H2>Affiliation: do they work there now?</H2>
      <Table
        head={["Status", "Evidence", "Counts as a lead?"]}
        rows={[
          ["verified-current", "Public member of their GitHub org, their own profile names the company with no end date, named on the company’s own site, or a company address plus a recent commit.", "Yes"],
          ["likely-current", "A commit to the company’s repo within 12 months, a LinkedIn headline or People-tab row naming it, a published company address, or a dated mention within 12 months.", "Yes"],
          ["unconfirmed", "Named somewhere; nothing yet says they are there now.", "No"],
          ["former", "Their profile shows the company with an end date.", "No — dropped"],
          ["generic", "A shared inbox (careers@, hello@).", "Never a referrer"],
        ]}
      />

      <H2>Hiring verdicts</H2>
      <Defs
        items={[
          [<C key="1">verified</C>, "An open role on their own careers page or their own ATS board."],
          [<C key="2">role_missing</C>, "A careers page exists but lists no role we could read."],
          [<C key="3">not_authorized</C>, "No corroboration anywhere they control — the hiring claim stands alone (e.g. roles only on an aggregator)."],
          [<C key="4">unreachable</C>, "Their site could not be read (bot wall, down, no website)."],
        ]}
      />

      <H2>“Complete” and “ready to ask”</H2>
      <P>
        A company is <strong>complete</strong> when it has a website, a real description (60+ characters, about this
        company), hiring proof, and at least one lead with an address. Anything missing is listed on the row as
        “still filling: …” with the reason the last attempt recorded. <strong>Ready to ask</strong> is the product:
        hiring proven + a fresher role + remote-anywhere or India + a lead with a usable address.
      </P>

      <H2>Selection bands</H2>
      <P>
        Roles are ranked by the odds of getting selected (<C>confidence.py</C>): <strong>high</strong> ≥ 65,{" "}
        <strong>medium</strong> ≥ 48, <strong>low</strong> ≥ 30, <strong>long shot</strong> below that. See{" "}
        <A href="/docs/roles-scoring">Roles &amp; scoring</A>.
      </P>

      <H2>Replies</H2>
      <P>
        Inbound mail is classified as <C>positive</C>, <C>negative</C>, <C>closed</C> (hiring done / not now) or{" "}
        <C>neutral</C>. Classification is deliberately cautious: anything ambiguous lands in neutral.
      </P>
    </Doc>
  );
}
