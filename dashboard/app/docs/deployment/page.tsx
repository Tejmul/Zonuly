import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Deployment" };

export default function Deployment() {
  return (
    <Doc slug="deployment">
      <Lead>
        The real pipeline runs on your laptop. The deployed site is <strong>a read-only window onto a copy</strong> of what
        it found: the dashboard on Vercel, the API on Zoho Catalyst AppSail, and one snapshot of the database with every
        email address masked inside the file. Nobody there can send, spend or write anything.
      </Lead>

      <H2>The pieces</H2>
      <Table
        head={["Piece", "Where", "Notes"]}
        rows={[
          ["Dashboard (and these docs)", "Vercel", "Every docs page is pre-rendered: it is served as finished HTML and never waits for the API."],
          ["API proxy", <C key="z">/zapi/*</C>, <>Same-origin rewrite on the Vercel domain to the backend (<C key="n">next.config.ts</C>) — no CORS to configure. Reads the backend marks cacheable are kept at the edge for 10 minutes, then served stale while it refreshes.</>],
          ["API", "Catalyst AppSail · Python 3.12 · 1024 MB", <>The Development environment is the live backend; <C key="p">ZONULY_PUBLIC=1</C> refuses every write and every private read.</>],
          ["Data", <C key="d">demo.db.gz</C>, "A stripped, masked snapshot, committed to the repository and unpacked into /tmp when the API starts."],
        ]}
      />
      <Callout kind="note" title="Memory: 1024 MB">
        The AppSail instance was raised from 512 MB to <strong>1024 MB</strong> on 23 Oct
        2026: the app itself peaks around 250 MB, but the unpacked snapshot sits beside it in the same memory, and a deploy at
        512 MB never came up.
      </Callout>

      <H2>The snapshot</H2>
      <P>
        <C>scripts/make_demo_db.py</C> builds <C>demo.db.gz</C> from your working database. Because the file sits in a public
        repository, hiding addresses in the API was not enough — so the file itself is cleaned:
      </P>
      <Table
        head={["What", "How"]}
        rows={[
          ["Every email address", "Replaced, in every text column of every table (the knowledge graph’s search index included), by a stand-in on the same domain: first letter + seven characters of a hash. The site looks the same; no address in the file reaches a real inbox."],
          ["Emptied", "Cached pages, model spend, inbound replies, your Upskill notes and proof links, real interview outcomes, the mixer’s spend ledger, and the scrape audit trail (extraction_write / extraction_reject)."],
          ["On screen", <>The public API also shows addresses masked, as <C key="m">p•••@company.com</C>.</>],
        ]}
      />
      <Code title="refresh the public numbers">{`
python scripts/make_demo_db.py          # -> demo.db.gz
git add demo.db.gz && git commit -m "Refresh the snapshot" && git push
`}</Code>

      <H2>How a push becomes the live site</H2>
      <Steps>
        <Step title="Smoke test the snapshot">
          On every push to <C>main</C>, the <C>snapshot</C> workflow unpacks <C>demo.db.gz</C> and runs{" "}
          <C>scripts/smoke.py</C> against it — a snapshot older than the code&apos;s schema fails here, not in production.
        </Step>
        <Step title="Build the backend">
          <C>scripts/build_catalyst.py</C> assembles <C>deploy/catalyst/appsail/build/</C>: the code, config, lists and
          snapshot, with every dependency fetched as a Linux wheel. Never shipped: <C>.env</C>, <C>secrets/</C>,{" "}
          <C>jobhunter.db</C>, <C>profile/</C>, backups — and not Playwright or the Google API client, which a read-only
          instance cannot reach.
        </Step>
        <Step title="Deploy and check">
          The workflow deploys to Catalyst Development with the <C>CATALYST_TOKEN</C> repository secret, waits for{" "}
          <C>/api/health</C>, and runs the smoke test against the live URL.
        </Step>
        <Step title="The dashboard">The workflow ships only the backend. The dashboard is deployed to Vercel separately; on Vercel its build sets the API base to the <C>/zapi</C> proxy.</Step>
      </Steps>

      <H2>A sleeping backend</H2>
      <P>
        The Development instance sleeps about a minute after its last request. The first visitor after that waits while it
        wakes and unpacks the snapshot — measured at 7.5 s for <C>/api/overview</C> on 23 Oct 2026, and up to half a minute
        right after a deploy. The dashboard retries a read for about 35 seconds before saying it can&apos;t reach the API; the
        docs never wait at all, and the front page&apos;s live figures fill in when they arrive.
      </P>
      <P>
        Where public mode switches things off, see the Public mode note in the{" "}
        <A href="/docs/dashboard">Dashboard tour</A> and the conventions in the <A href="/docs/api">HTTP API</A>.
      </P>
    </Doc>
  );
}
