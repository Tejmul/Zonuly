import type { Metadata } from "next";
import { C, Callout, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Dashboard tour" };

const PAGES: [string, string, string, string][] = [
  ["Dashboard", "/dashboard", "What we have, how much is done, how much has gone out. The hero number is companies ready to ask.", "—"],
  ["Companies", "/companies", "Every company, in views: Ready to ask · Worth chasing · Hire from anywhere · India — all · Everything. Pay, funding, hiring, tier.", "Add company"],
  ["Company page", "/companies/{id}", "Who they are → the hiring post → the roles → the people to ask → the money. Every number next to its source. Fills itself on open when incomplete.", "Fill · find people · find emails · verify hiring"],
  ["Best bets", "/best-bets", "Roles ranked by the odds of getting selected, per market, each with its top reasons and main blocker.", "Filter by band"],
  ["★ India", "/lists/india-star", "Every Bengaluru and Hyderabad company that clears the guardrails — your hand-picked list first, numbered — in one table with city tabs.", "Find people at the listed companies"],
  ["Atlas", "/atlas", "Your resume on the left, weighted terms, segments lighting up, companies ranked on the right. All state in the URL.", "Sliders and filters"],
  ["Graph", "/graph", "The knowledge graph as a star map — filled stars are complete, rings still filling — plus every unfinished company and what it lacks.", "Fill now (shows why if nothing was found)"],
  ["Leads", "/leads", "People who could refer you: confirmed at the company now, where we found them, how sure we are of the address. Bengaluru first.", "Filter by source, city, confidence"],
  ["Queue", "/queue", "Drafts waiting for review, flags and evidence above the editor. The sidebar badge counts them.", "Edit · approve · reject"],
  ["Outreach", "/outreach", "Every message from draft to reply: stage counts, budget meter, launch checklist, why mail might not be leaving.", "—"],
  ["Email proposal", "/outreach/proposal", "How each email would be written, before anything is drafted: evidence, structure, exact text, quality, flags.", "Write draft · approve · reject"],
  ["Replies", "/replies", "Inbound mail by sentiment. Ambiguous replies land in Neutral.", "Check now"],
  ["Calendar", "/calendar", "Events read out of replies: confirmed solid, proposed outlined, clashes struck through.", "Confirm · reschedule"],
  ["Tracker", "/tracker", "Each email’s lifecycle, row by row, and the full thread on click. “What works” shows reply rates by lever.", "—"],
  ["Scraping health", "/health", "The hourly audit: alerts, field fill rates against 1 h and 24 h ago, guardrail refusals by reason, channel health.", "—"],
  ["Settings", "/settings", "Caps, follow-up days, send window, sources, and the Gmail connection.", "Connect Gmail · change caps"],
  ["Docs", "/docs", "This documentation.", "⌘K to search"],
];

export default function DashboardTour() {
  return (
    <Doc slug="dashboard">
      <Lead>
        The dashboard is the only place you need to look. Its menu follows the workflow: find companies, find the people
        at them, approve the drafts, watch what comes back.
      </Lead>
      <Table head={["Page", "Route", "What it shows", "What you can do"]} mono={[1]} rows={PAGES} />

      <H2>Design language</H2>
      <P>
        Black and white only. State is carried by fill and rule, never colour: a solid chip is a fact, an outlined one a
        claim, a struck-through one excluded. Every figure is set in a tabular mono face so columns line up. Dark mode
        follows your system, or the toggle in the top bar.
      </P>
      <Callout kind="note" title="Public mode">
        With <C>ZONULY_PUBLIC</C> set, the same dashboard becomes a read-only exhibit over a demo snapshot: pages that name
        real people&apos;s mail (Replies, Email proposal) disappear from the menu, and every write is refused by the API.
      </Callout>
    </Doc>
  );
}
