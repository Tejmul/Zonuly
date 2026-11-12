import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Dashboard tour" };

const LOCAL = "On your own machine only. ";

const PAGES: [string, string, string, string][] = [
  ["Dashboard", "/dashboard", "What we have, how much is done, how much has gone out. The hero number is companies ready to ask. The outreach card (sender, sends today) shows on your own machine only.", "—"],
  ["Companies", "/companies", "Opens on All companies: every company tracked, the complete ones first and then the ones still being filled, each with what it lacks — the header gives the total, how many are complete and being filled, and how many were scraped in all. Complete and Being filled are tabs of their own. Over the complete list, views: Ready to ask · Worth chasing · Hire from anywhere · India — all · Everything. Pay, funding, hiring, tier.", "Search · pick a view · add company (own machine)"],
  ["Company page", "/companies/{id}", "Who they are → the hiring post → the roles → the people to ask → the money. Every number next to its source. On your own machine it fills itself on open when incomplete, and a Dossier section appears when one has been written for the company.", "Fill · find people · find emails · verify hiring · read the dossier (own machine)"],
  ["Best bets", "/best-bets", "Opens on Companies: today’s one company, the Bengaluru / Hyderabad / Remote shortlists by chance of an offer (plus After DSA and Skipped), and the Mixer panel. Roles, the second tab, ranks every role by its selection odds per market.", "Filter roles by band · on your own machine: fill it, ask a referrer, mark asked, skip, buy with the mixer"],
  ["Roles", "/roles", "Every scraped role, searchable, sorted by score, salary, recency or similarity — reached from links in the app rather than the menu.", "Search · filter by status"],
  ["Role page", "/roles/{id}", "One role: its selection odds term by term, the needs brief (what they need and what of it you have done), why it scored as it did, and the description.", "Rebuild the brief (own machine)"],
  ["Upskill", "/upskill", "One ranked learning plan: the skills the shortlisted companies ask for that the resume doesn’t show, best payoff first, and what DSA practice is worth. On your own machine it reads your resume; on the deployed site it reads a fresher’s reference resume and has no progress controls (see Upskill).", "Own machine: mark learning / done with proof · add to my profile"],
  ["★ India", "/lists/india-star", "Every Bengaluru and Hyderabad company that clears the guardrails — the hand-picked list first, numbered — in one table with city tabs.", "Find people at the listed companies (own machine)"],
  ["Atlas", "/atlas", "You on the left, weighted terms, segments lighting up, companies ranked on the right. On your own machine the You column is built from your resume; on the deployed site, which has no resume, from your own picks on the page — the kind of work, where, stage and must-haves. All state in the URL.", "Sliders and filters"],
  ["Graph", "/graph", "The knowledge graph as a star map — filled stars are complete, rings still filling — plus every unfinished company and what it lacks. The header reads “N companies tracked of M scraped” when no filter is on.", "Fill now (own machine; shows why if nothing was found)"],
  ["Leads", "/leads", "People who could refer you: confirmed at the company now, where we found them, and an address only once it is proved — none is ever guessed. On the deployed site addresses are masked. Bengaluru first.", "Filter by source, city, confidence · find and draft (own machine)"],
  ["Communities", "/communities", "The Slack and Discord rooms where roles are posted before they are listed, found in public directories and ranked small-and-specific first. Joining is by hand; nothing reads inside a workspace.", "Filter · open the join link · on your own machine: find more rooms, mark joined"],
  ["Queue", "/queue", LOCAL + "Drafts with their flags and evidence above the editor; the review rules approve or reject each one, and a draft can still be edited, approved or rejected here by hand. The sidebar badge counts them.", "Edit · approve · reject"],
  ["Outreach", "/outreach", LOCAL + "Every message from draft to reply: stage counts, budget meter, launch checklist, why mail might not be leaving.", "—"],
  ["Email proposal", "/outreach/proposal", LOCAL + "How each email would be written, before anything is drafted: evidence, structure, exact text, quality, flags.", "Write draft · approve · reject"],
  ["Replies", "/replies", LOCAL + "Inbound mail by sentiment. Ambiguous replies land in Neutral.", "Check now"],
  ["Calendar", "/calendar", LOCAL + "Events read out of replies: confirmed solid, proposed outlined, clashes struck through.", "Confirm · reschedule"],
  ["Tracker", "/tracker", LOCAL + "Each email’s lifecycle, row by row, and the full thread on click. “What works” shows reply rates by lever.", "—"],
  ["Scraping health", "/health", "The hourly audit: alerts, field fill rates against 1 h and 24 h ago, guardrail refusals by reason, channel health.", "—"],
  ["Settings", "/settings", LOCAL + "Caps, follow-up days, send window, sources, and the Gmail connection.", "Connect Gmail · change caps"],
  ["Docs", "/docs", "This documentation.", "⌘K to search"],
];

export default function DashboardTour() {
  return (
    <Doc slug="dashboard">
      <Lead>
        The dashboard is the only place you need to look. Its menu follows the workflow: find companies, find the people
        at them, write the asks, watch what comes back. On the deployed site you see it after signing in (see{" "}
        <A href="/docs/accounts">Sign in &amp; accounts</A>), read-only and without the cold-email pages; rows marked{" "}
        <em>on your own machine only</em> are the operator&apos;s outbox and exist only where the pipeline runs.
      </Lead>
      <Table
        head={["Page", "Route", "What it shows", "What you can do"]}
        mono={[1]}
        rows={PAGES.map(([page, route, what, can]) => [
          page,
          // every route that needs no id opens straight from here
          route.includes("{") ? route : <A key={route} href={route}>{route}</A>,
          what,
          can,
        ])}
      />

      <H2>Best bets, in detail</H2>
      <P>
        The <strong>Companies</strong> tab is the <A href="/docs/decide">decision brain</A>: a card for today&apos;s company
        with its chance of an offer, the four stage bars with the weakest marked, the five questions and their evidence; under
        it the shortlists; then the <A href="/docs/mixer">Mixer</A> panel. The <strong>Roles</strong> tab is the older view —
        every role in India or Remote by its selection score, with its top reasons and main blocker — described in{" "}
        <A href="/docs/roles-scoring">Roles &amp; scoring</A>.
      </P>

      <H2>The Dossier</H2>
      <P>
        For a company you have researched by hand, a write-up in <C>reports/&lt;company&gt;-dossier.md</C> — who to ask,
        where to apply, how to crack it, every fact with its source — shows on its company page as a <strong>Dossier</strong>{" "}
        section, collapsed after the first screen (<strong>Read all</strong> opens it). It is matched to the company by name.
        Notes between <C>&lt;!-- internal --&gt;</C> and <C>&lt;!-- /internal --&gt;</C> stay in the file. It is{" "}
        <strong>operator only</strong>: it names people and their addresses, so the public site never serves it and the
        section simply doesn&apos;t appear there.
      </P>

      <H2>Design language</H2>
      <P>
        Black and white only. State is carried by fill and rule, never colour: a solid chip is a fact, an outlined one a
        claim, a struck-through one excluded. Every figure is set in a tabular mono face so columns line up. Dark mode
        follows your system, or the toggle in the top bar.
      </P>
      <Callout kind="note" title="Public mode">
        With <C>ZONULY_PUBLIC</C> set, the same dashboard becomes a read-only copy over a masked snapshot: the cold-email
        pages (Queue, Outreach, Email proposal, Replies, Calendar, Tracker) and Settings disappear from the menu, buttons
        that would write are not offered, addresses are masked, and every write is refused by the API. With{" "}
        <C>ZONULY_AUTH=1</C> as well — as on <C>zonuly.tejmul.in</C> since 12 Nov 2026 — every page but the landing page
        needs a signed-in account.
      </Callout>
    </Doc>
  );
}
