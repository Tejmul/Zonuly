import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, H3, Lead, P, Status, Table, UL } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Discovery" };

export default function Discovery() {
  return (
    <Doc slug="discovery">
      <Lead>
        Discovery answers one question: <strong>which companies exist that we should know about?</strong> It reads the
        places early-stage startups actually post — their own job boards — and admits a company only through one gate.
      </Lead>

      <H2>Sources</H2>
      <Table
        head={["Source", "How it is read", "Status"]}
        rows={[
          ["Greenhouse · Lever · Ashby", "Their public board JSON APIs, by company slug", <Status key="1" s="built" />],
          ["Workable · Breezy · Recruitee · Keka · Darwinbox …", "Careers pages recognised and parsed by careers.py", <Status key="2" s="built" />],
          ["Y Combinator directory", "The yc-oss mirror, then each company’s board", <Status key="3" s="built" />],
          ["Wellfound", "The page’s embedded data", <Status key="4" s="built" />],
          ["Hacker News “Who is hiring”", "Algolia API", <Status key="5" s="built" />],
          ["RemoteOK · We Work Remotely", "API / RSS", <Status key="6" s="built" />],
          ["Funding news", "RSS feeds → admission candidates", <Status key="7" s="built" />],
          ["Exa company search", "Metered, in the paid tick; keyless variant when the free queues are empty", <Status key="8" s="built" />],
          ["India sources", "Funding RSS, directories, GitHub orgs by city, Cutshort, VC portfolios, LinkedIn guest job search", <Status key="9" s="built" />],
          ["X hiring posts", "Only through a throwaway session; off by default", <Status key="10" s="off" />],
        ]}
      />
      <P>
        Of 8,000 roles on record, the largest sources are Ashby (2,185), careers pages (1,706), Greenhouse (1,654) and
        Wellfound (1,296) — company-owned boards, exactly where the hidden jobs are.
      </P>

      <H2>The admission gate</H2>
      <P>Every new company — from any source — passes <C>admission.admit()</C>:</P>
      <UL>
        <li><strong>Clean the name</strong> and reject bogus websites (link shorteners, social profiles, aggregators).</li>
        <li><strong>De-duplicate</strong> by exact name, normalised name key, and domain.</li>
        <li><strong>Apply the overrated rule</strong>: famous, listed, valued at $5B+, or raised $1B+ → excluded. Borderline companies stay only while visibly hiring and able to pay.</li>
      </UL>
      <P>A rejected company&apos;s postings are dropped and counted, not stored.</P>

      <H2>India discovery</H2>
      <P>
        India is restricted to Bengaluru and Hyderabad, and a company is admitted only with <strong>a quote that places it
        in one of them</strong>. New finds get <C>india_found_at</C>, which moves them to the front of the fill queue for 30
        days. A posting-led channel (<C>india_jobs.py</C>) runs every two hours: it reads LinkedIn&apos;s logged-out job search
        and Indian boards, filters out body shops and giants, and admits the employers.
      </P>
      <H3>The pay floor</H3>
      <P>
        An India company whose best full-time pay evidence is under <C>targeting.india.min_lpa</C> (₹20 LPA) is hidden as{" "}
        <C>below_pay_bar</C> — not deleted — and comes back by itself when a posting states more.
      </P>

      <H2>Roles from boards</H2>
      <P>
        <C>pipeline.scrape()</C> runs every enabled scraper concurrently, keeps roles whose title and location fit, removes
        duplicates by fingerprint (keeping the richest description), parses salary into ₹ LPA, and upserts by URL. The
        first posting from a company becomes its recorded hiring claim.
      </P>
      <Callout kind="note">
        Want a company the scrapers haven&apos;t found? <strong>Companies → Add company</strong>, or{" "}
        <C>python scripts/run.py add-company &quot;Acme&quot; --website acme.com</C>. It finds the board and pulls the roles. Next:{" "}
        <A href="/docs/fill">Fill &amp; enrichment</A>.
      </Callout>
    </Doc>
  );
}
