import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, H3, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "People & referrers" };

export default function People() {
  return (
    <Doc slug="people">
      <Lead>
        The product is a <strong>named person who works there now</strong> and could refer you. Finding them — without
        ever logging into your own accounts — is the heart of ZoNuLy, which is why referral work gets most of every cycle.
      </Lead>

      <H2>The goal per company</H2>
      <P>
        Three ready referrers, at least one with a usable address. “Ready” means verified-current or likely-current, a
        person rather than an inbox, not on the do-not-contact list, and with some identity (name, GitHub or LinkedIn).
      </P>

      <H2>Where people are found</H2>
      <Steps>
        <Step title="Founders and team pages">YC&apos;s page lists active founders; the company&apos;s own team and About pages name people next to their roles — the strongest evidence of all.</Step>
        <Step title="One Exa people search">A single metered search per company for current profiles, inside a daily reserve.</Step>
        <Step title="Public LinkedIn, keyless">Search-result titles like “Name – Engineer at Company”, scoped to the company&apos;s city. Profiles are never fetched while logged in.</Step>
        <Step title="GitHub">Members of the company&apos;s GitHub organisation and recent committers to its repositories — engineers by definition, often with a commit address.</Step>
        <Step title="Public mentions">Talks, podcasts and blog bylines that name someone at the company.</Step>
        <Step title="Apify People tab">For high-paying companies still short of referrers: the company&apos;s LinkedIn People tab bought from Apify by its own job every 30 minutes — up to three companies a run, 25 people each, in the company&apos;s city — inside the daily cap of $0.60 (the $19 Starter credit spread over a month).</Step>
      </Steps>

      <H2>The rules</H2>
      <H3>City lock</H3>
      <P>
        For a Bengaluru company only people <strong>based in Bengaluru</strong> are kept; for Hyderabad, only Hyderabad.
        Location means the location on their own profile — never a college, never a search snippet. A person with no known
        location is dropped under the lock. Apify receives the city as a server-side filter, so you don&apos;t pay for
        people in other countries.
      </P>
      <H3>Employment must be current</H3>
      <P>
        A public profile that lists the company with no end date is <C>verified-current</C>; with an end date, the person is{" "}
        <C>former</C> and dropped. A commit alone is not employment. Referrers are re-confirmed after 90 days and
        downgraded if the evidence has gone.
      </P>
      <H3>Ranked by who can actually refer</H3>
      <Table
        head={["Rank", "Role class"]}
        rows={[["1", "Senior, staff, lead or founding engineer"], ["2", "Software engineer, SDE 1–3"], ["3", "Founder, CTO, co-founder"], ["4", "Engineering manager"], ["5", "Technical recruiter / tech HR"], ["6", "General HR / talent"], ["9", "Anyone else — never asked first"]]}
      />
      <P>
        Since 23 Oct 2026 the tech team ranks first: a referral from an engineer on the team you would join carries more than
        one from a founder&apos;s inbox. The order lives in <C>targeting.people.roles</C> in <C>config.yaml</C>.
      </P>

      <H2>The actors layer</H2>
      <P>
        <C>jobhunter/actors/</C> is “our own Apify”: small programs (actors) with a run ledger, datasets, a page cache, and
        per-host politeness. LinkedIn is read only as a logged-out guest, at most one request every 20–45 seconds, and a{" "}
        <strong>circuit breaker</strong> stops every actor on a host the moment it sees a login wall, status 999 or 429.
      </P>
      <Code title="find a company's people">{`
python scripts/run.py actor people 2802 --linkedin https://www.linkedin.com/company/skylotech \\
  --source apify --apify-max 25 --title engineer
`}</Code>
      <P>
        Results are saved as contacts through the guardrails by default and appear on the company page and on{" "}
        <A href="/docs/dashboard">Leads</A>. Use <C>--no-save</C> to only look.
      </P>
      <Callout kind="rule" title="Your accounts are never used">
        No scraper logs in to LinkedIn, X or GitHub as you. A banned account would destroy the very network referrals
        depend on. Where a logged-in view is needed, Apify&apos;s own accounts carry the risk.
      </Callout>
    </Doc>
  );
}
