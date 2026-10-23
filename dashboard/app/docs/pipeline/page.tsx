import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, PageCards, Table } from "@/components/docs/prose";
import { PipelineDiagram } from "@/components/docs/diagrams";
import { STAGES } from "@/lib/docs-content";

export const metadata: Metadata = { title: "Pipeline at a glance" };

export default function Pipeline() {
  return (
    <Doc slug="pipeline">
      <Lead>
        A company enters as a name and leaves, if it earns it, as a sent referral ask with a person&apos;s name on it.
        Each stage has one job, writes through the guardrails, and records why when it can&apos;t finish.
      </Lead>
      <PipelineDiagram stages={STAGES} />

      <H2>Who runs each stage</H2>
      <Table
        head={["Stage", "Runs in", "Spends"]}
        rows={[
          ["Discover", "Free tick (rotating steps), paid tick, daily scrape", "Free; Exa and X in the paid tick"],
          ["Fill", "Free tick (20 per tick) · Fill now · opening a company", "Free"],
          ["Hiring proof", "Fill, careers and verify steps", "Free"],
          ["Roles & scoring", "Daily cycle in the API; selection odds every tick", "Model rubric on survivors only"],
          ["Decide", "Every free tick, after the role scores · decide run · the mixer", "Free — arithmetic over fields already held"],
          ["People", "The Apify job (every 30 min): People tabs for the shortlist, tech team only; the free tick’s people step", "Apify inside its daily cap"],
          ["Complete the shortlist", "Shortlist job (every 10 min): one company at a time, Bengaluru first", "Free fill + Exa (Agent Reach); referrals via the Apify job"],
          ["Addresses", "Right after every people step", "Free (MX lookups); Hunter when enabled"],
          ["Draft", "On request: Outreach → batch, or the CLI", "Model polish, optional"],
          ["Review", "You, in the Queue", "Ten minutes"],
          ["Send", "Send job, every 5 minutes", "One email per run, 25 a day"],
          ["Replies & follow-up", "Free tick and the API’s hourly reply cycle", "Cheap model for classification"],
        ]}
      />

      <Callout kind="note" title="Referrals are Apify's job">
        Company and role scraping runs on the free and paid ticks. Referrals come from the Apify job, the shortlist first and
        the tech team only; the free people-search job is off. See <A href="/docs/scheduling">Scheduling</A>.
      </Callout>

      <H2>The order work is done in</H2>
      <P>
        Every queue reads the same ordering (<C>db.fill_order</C>): the ★ India list first, in list order; then
        Bengaluru and Hyderabad companies found in the last 30 days; then companies we know nothing about; then the
        least-complete of the highest-priority companies. Referrals go city by city — Bengaluru, then Hyderabad.
      </P>

      <H2>Stage by stage</H2>
      <PageCards slugs={["discovery", "fill", "hiring-proof", "roles-scoring", "decide", "mixer", "people", "email-addresses", "outreach", "sending", "replies"]} />
      <P>
        For how these are scheduled, see <A href="/docs/scheduling">Scheduling &amp; the tick</A>.
      </P>
    </Doc>
  );
}
