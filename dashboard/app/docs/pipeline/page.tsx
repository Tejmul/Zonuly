import type { Metadata } from "next";
import { A, C, Doc, H2, Lead, P, PageCards, Table } from "@/components/docs/prose";
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
          ["People", "Referral job (every 15 min) and the free tick’s people step", "Free; one Exa search; Apify inside its cap"],
          ["Addresses", "Right after every people step", "Free (MX lookups); Hunter when enabled"],
          ["Draft", "On request: Outreach → batch, or the CLI", "Model polish, optional"],
          ["Review", "You, in the Queue", "Ten minutes"],
          ["Send", "Send job, every 5 minutes", "One email per run, 25 a day"],
          ["Replies & follow-up", "Free tick and the API’s hourly reply cycle", "Cheap model for classification"],
        ]}
      />

      <H2>The order work is done in</H2>
      <P>
        Every queue reads the same ordering (<C>db.fill_order</C>): the ★ India list first, in list order; then
        Bengaluru and Hyderabad companies found in the last 30 days; then companies we know nothing about; then the
        least-complete of the highest-priority companies. Referrals go city by city — Bengaluru, then Hyderabad.
      </P>

      <H2>Stage by stage</H2>
      <PageCards slugs={["discovery", "fill", "hiring-proof", "roles-scoring", "people", "email-addresses", "outreach", "sending", "replies"]} />
      <P>
        For how these are scheduled, see <A href="/docs/scheduling">Scheduling &amp; the tick</A>.
      </P>
    </Doc>
  );
}
