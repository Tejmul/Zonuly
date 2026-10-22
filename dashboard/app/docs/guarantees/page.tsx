import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, H3, Lead, P, Table, UL } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Guarantees & guardrails" };

export default function Guarantees() {
  return (
    <Doc slug="guarantees">
      <Lead>
        A tool that writes to strangers from your own inbox has to be trustworthy before it is clever. These are the
        promises ZoNuLy keeps no matter what — and the code that keeps them.
      </Lead>

      <H2>The guarantees</H2>
      <Table
        head={["Guarantee", "What it means", "How it is enforced"]}
        rows={[
          ["Nothing sends without a human", "Every email is approved by you, as written, before it can leave.", <>The sender refuses any email that isn&apos;t <C key="a">approved</C>; approval is set only by the approve route; no scheduled job approves.</>],
          ["Nothing is invented", "No project, number, title or connection that isn’t in the source material.", <>The composer leaves a slot empty rather than guess — and an empty required slot means <strong key="n">no email</strong>. Review flags mark unsupported claims.</>],
          ["25 a day, spent well", "Each send is irreversible and burns reputation; the daily cap is a budget.", <>A send ledger is checked inside the send itself; the cap is visible on every screen.</>],
          ["Confidence is always shown", <>Every address is <C key="v">verified</C>, <C key="p">pattern-guessed</C> or <C key="s">scraped</C>.</>, "The label is never defaulted, merged or hidden; guessed addresses have their own daily cap and a bounce brake."],
          ["One follow-up, at most", "A silent thread gets one polite nudge after five days — never two.", <>A per-thread flag is set when the follow-up is queued, and the follow-up goes back through review.</>],
          ["Data stays local", "Secrets never leave the laptop; every outbound call is logged, priced and capped.", "Keys live in .env (git-ignored). The resume and drafts are sent to OpenRouter models, by design."],
          ["No auto-apply", "No portal applications, no contact-list exports. One person, one role, one ask.", "There is no code path that submits an application."],
        ]}
      />

      <H2>The guardrails layer</H2>
      <P>
        Every value a scraper finds passes through <C>jobhunter/guardrails.py</C> before it is written. Guardrails
        don&apos;t just validate shapes — they decide whether a fact is believable and record why.
      </P>
      <Defs
        items={[
          ["Descriptions", "Must be 60–600 characters, about this company (its name or domain), not a cookie banner, bot wall or generic tagline. Refusals are counted by reason on the Scraping health page."],
          ["Funding amounts", "Between $0.05M and $5,000M; anything else is a misparse."],
          ["Contacts", "vet_contact checks the name is a person, the address is on the company’s domain and not a shared inbox, and attaches an affiliation with its evidence."],
          ["Hiring", "Proof must come from the company’s own careers page or ATS board, and goes stale after 30 days."],
          ["Field evidence", "Each field carries its source, channel and confidence in field_meta — the dashboard shows the sentence a number came from."],
          ["Complete bar", "A company counts as complete only with a website, a real description, hiring proof and a lead with an address."],
        ]}
      />

      <H2>Hard constraints</H2>
      <H3>No local models</H3>
      <P>
        The laptop has 8 GB of RAM and running a local model wrecked it, so every model call goes to OpenRouter behind
        a ledger with per-minute, daily and monthly caps. Nothing in the repo starts a model runtime.
      </P>
      <H3>No personal accounts in scraping</H3>
      <P>
        Your LinkedIn, X and GitHub logins are never used to scrape. LinkedIn is read only through public guest pages,
        paced, with a circuit breaker that stops for hours on the first block. Where a logged-in view is genuinely
        needed (a company&apos;s People tab), it is bought from Apify, whose own accounts carry the risk.
      </P>
      <H3>Never send like spam</H3>
      <UL>
        <li>25 emails a day, spread across 10:00–19:00 — about one every twenty minutes, jittered.</li>
        <li>At most one email per run of the send job, so a laptop waking from sleep never fires a backlog.</li>
        <li>Plain text. No tracking pixels, no link shorteners, no HTML templates.</li>
        <li>A bounce brake: past six bounces in seven days, guessed addresses stop; verified ones still go.</li>
      </UL>
      <Callout kind="warn" title="Learned the hard way">
        On 19 Oct 2026 about thirty near-identical emails went into one company a minute or two apart. Four guessed
        addresses bounced and Gmail refused all further mail for a day (<C>550 5.4.5</C>). The pacing rules above exist
        because of that day. See <A href="/docs/troubleshooting">Troubleshooting</A>.
      </Callout>

      <H2>Permission tiers</H2>
      <P>Every action the system can take belongs to a tier. The higher the tier, the more it needs your explicit grant.</P>
      <Table
        head={["Tier", "Kind of action", "Examples"]}
        rows={[
          ["0", "Local read", "Querying the database, rendering a page"],
          ["1", "Free external read", "Fetching a careers page, a public profile, a search"],
          ["2", "Spend", "An OpenRouter call, an Apify run — each behind a budget check"],
          ["3", "World-write, per item", "Sending one approved email"],
          ["4", "Forbidden", "LinkedIn login, auto-apply, bulk delete, contact export, any local model runtime"],
        ]}
      />
      <Callout kind="rule" title="Nothing is built without a numbered approval">
        Changes to the pipeline are proposed, numbered, and built only once approved. Agents propose; humans commit.
      </Callout>
    </Doc>
  );
}
