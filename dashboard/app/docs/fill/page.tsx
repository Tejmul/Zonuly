import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Fill & enrichment" };

export default function Fill() {
  return (
    <Doc slug="fill">
      <Lead>
        Fill takes a bare company row and completes it — <strong>with no model and no guessing</strong>. Whatever it
        can&apos;t prove it leaves empty, and writes down why.
      </Lead>

      <H2>What one fill does</H2>
      <Steps>
        <Step title="Read the description">
          Candidates from the homepage&apos;s meta tags, JSON-LD and first paragraphs, then the About page, then the LinkedIn
          About text. Each passes the description guardrail (60–600 characters, about this company, not boilerplate); the
          best one is stored with its source.
        </Step>
        <Step title="Find the LinkedIn page">
          The company&apos;s own homepage usually links its LinkedIn page — that link is kept even when LinkedIn itself is
          unreadable. When the page can be read (as a guest), size band, founding year and a jobs count are added.
        </Step>
        <Step title="Prove hiring">
          <C>careers.run_company()</C> looks for any careers page or board and reads its roles. Any open role on their own
          page counts as proof. See <A href="/docs/hiring-proof">Hiring proof</A>.
        </Step>
        <Step title="Find the funding">
          Round, amount, date and investors from a keyless search, each validated (a real amount, not debt; a date not in
          the future; a quote that names the company).
        </Step>
        <Step title="Record what is missing, and why">
          Every gap gets a reason in <C>fill_state</C> — “roles only on instahyre.com (an aggregator)”, “site blocks
          readers”, “no careers page anywhere on their site” — shown on the dashboard after a fill.
        </Step>
      </Steps>

      <H2>When it runs</H2>
      <Table
        head={["Trigger", "Scope"]}
        rows={[
          ["Every free tick", "Up to 20 companies, ★ list → India → remote; a company is refilled after 7 days (30 once complete)"],
          ["Opening a company page", "That company, unless complete or filled in the last 6 hours"],
          ["Fill now (Graph page)", "That company, now — the result says what is still missing and why"],
          ["fill.backfill_linkedin()", "One-off: the LinkedIn link from every ★ / BLR / HYD homepage (776 found on 19 Oct 2026)"],
        ]}
      />

      <H2>Enrichment with models</H2>
      <P>
        Separately, the paid tick fills what needs reading comprehension — a company&apos;s story, a better description,
        stipend and PPO mentions — through <C>enrich.py</C>, inside the model budget. Pay from levels.fyi is read through
        scrape.do (<C>levels.py</C>) and converted to fresher ₹ LPA.
      </P>

      <H2>Evidence on every field</H2>
      <P>
        Each written value records its evidence, source URL, channel, time and confidence in <C>field_meta</C>. When two
        sources agree, confidence rises; when they disagree, the better-evidenced value wins and the loser is kept as a
        rejection with the reason “conflict”. Nothing is silently overwritten.
      </P>
      <Callout kind="tip" title="Why a fill sometimes finds nothing">
        Most remaining gaps are real absences: roles posted only on aggregators, careers pages with no open role, or sites
        that block readers. Clicking Fill again won&apos;t change those — the reason shown tells you which it is.
      </Callout>
    </Doc>
  );
}
