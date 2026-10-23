import type { Metadata } from "next";
import { A, C, Callout, Code, Defs, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Upskill" };

export default function Upskill() {
  return (
    <Doc slug="upskill">
      <Lead>
        Upskill (<C>/upskill</C> in the app, <C>jobhunter/upskill.py</C>) is <strong>one ranked learning plan</strong>: what
        the companies you are actually targeting ask for that your resume doesn&apos;t show, ordered by how much learning each
        one would raise your chances. Nothing reaches your profile without proof.
      </Lead>

      <H2>Where the gaps come from</H2>
      <P>
        Not from the job market at large — from your shortlists. For every company the{" "}
        <A href="/docs/decide">decision brain</A> keeps (Bengaluru, Hyderabad, Remote), the needs briefs of its three best
        roles are read against your resume. Each skill they name lands in one of two kinds:
      </P>
      <Defs
        items={[
          [<strong key="l">Learn</strong>, "Missing from the resume altogether."],
          [<strong key="p">Prove</strong>, "On the resume only as a word in the skills list — no shipped project or bullet behind it — so a brief counts it half."],
          [<strong key="d">DSA</strong>, <>Always its own row: what raising your <C>decide.me.dsa_level</C> to 0.5, 0.65 or 0.8 would add, and which parked “after DSA” companies would come back.</>],
        ]}
      />

      <H2>How the order is decided</H2>
      <P>
        For each gap, the brain&apos;s own arithmetic is re-run as if you had shipped a project with that skill: the best
        role&apos;s skills score rises, which moves the screen and the interview loop, which moves the chance of an offer. Summed
        over every shortlisted company that asks for it, that is the gap&apos;s <strong>expected offers gained</strong> — the
        number the list is sorted by.
      </P>
      <Table
        head={["Rank", "Gap", "Kind", "Asked for by", "Expected offers gained"]}
        rows={[
          ["1", "Observability", "learn", "71 companies", "+0.31"],
          ["2", "Python", "prove", "47 companies", "+0.11"],
          ["3", "JavaScript", "prove", "45 companies", "+0.11"],
          ["—", "DSA to 0.5 / 0.65 / 0.8", "dsa", "the whole list", "+2.9 / +5.2 / +6.9"],
        ]}
      />
      <P>
        Read on 23 Oct 2026 from 248 shortlisted companies, where working the whole list was worth about 12.6 offers.
      </P>
      <Callout kind="note" title="An upper estimate">
        Each skill is counted as fully covered on each company&apos;s best role, so the gain is the most it could be. When
        Observability was added on a copy of the database, the shortlist&apos;s expected offers moved by +0.19 against +0.32
        estimated.
      </Callout>

      <H2>Using the page</H2>
      <P>
        Each row shows the gap, its kind, how many companies (and what share of your list) ask for it, how often as a
        must-have, and its payoff bar. Open a row to see <strong>who asks, in their words</strong> (the posting&apos;s own
        sentence, linked), <strong>where it moves your odds most</strong>, and <strong>your progress</strong>.
      </P>
      <Steps>
        <Step title="Mark it learning">So you can see what is in progress.</Step>
        <Step title="Mark it done — with proof">
          Done needs two things: a <strong>proof link</strong> (a repo, a demo, a write-up) and{" "}
          <strong>a line in your own words</strong> about what you built. Without both, “done” is refused.
        </Step>
        <Step title="Add to my profile">
          Only a done skill with proof can be added. A dated backup of <C>profile/profile.json</C> is taken first, then the
          skill is added as a project carrying your line and link. Every stored needs brief is re-checked against the new
          profile, every role re-scored and the brain run again — a few minutes — so the gain shows up in Best bets.
        </Step>
        <Step title="Update the resume PDF yourself">
          The PDF is yours. The page gives the line ready to paste: your words, the skill named, the proof linked.
        </Step>
      </Steps>
      <Callout kind="rule" title="No fabrication">
        Upskill never writes a skill you have not shown. This is the same rule the emails follow: no claim without evidence.
      </Callout>

      <Code title="terminal">{`
python scripts/run.py upskill gaps                     # the plan, best payoff first, and what DSA practice is worth
python scripts/run.py upskill mark Observability learning
python scripts/run.py upskill mark Observability done --proof-url https://github.com/you/thing --note "built …"
python scripts/run.py upskill add Observability        # into profile.json (backed up), then re-score everything
`}</Code>
    </Doc>
  );
}
