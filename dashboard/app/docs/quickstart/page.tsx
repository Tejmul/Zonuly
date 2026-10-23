import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Quickstart" };

export default function Quickstart() {
  return (
    <Doc slug="quickstart">
      <Lead>From a fresh clone to a running pipeline in about fifteen minutes. You need macOS, Python 3.12, Node 20+ and{" "}
        <C>uv</C>.</Lead>

      <Steps>
        <Step title="Install the Python side">
          The package is <C>jobhunter</C>, pinned to Python 3.12.
          <Code title="terminal">{`
git clone https://github.com/Tejmul/Zonuly.git && cd Zonuly
uv sync                      # or: python -m venv .venv && pip install -e .
uv sync --extra research     # optional: the web-research backends
`}</Code>
        </Step>
        <Step title="Add your keys">
          Copy the template and fill in what you have. Only <C>OPENROUTER_API_KEY</C> is required to start; everything
          else switches a channel on. The file is git-ignored.
          <Code title="terminal">{`cp .env.example .env`}</Code>
          See <A href="/docs/configuration#environment-variables">Environment variables</A> for every name.
        </Step>
        <Step title="Check the machine">
          <C>doctor</C> checks the OpenRouter key and model aliases, today&apos;s budget, the parsed resume, GitHub, Hunter and
          Gmail — and says how to fix whatever is missing.
          <Code title="terminal">{`python scripts/run.py doctor`}</Code>
        </Step>
        <Step title="Teach it your resume">
          Put your resume at <C>profile/resume.pdf</C> and parse it into <C>profile/profile.json</C> — the profile every
          score and every email is built from.
          <Code title="terminal">{`python scripts/run.py profile`}</Code>
        </Step>
        <Step title="Start the API">
          FastAPI on <C>127.0.0.1:8000</C>, with interactive API docs at <C>/docs</C>. It also starts an in-process
          scheduler (daily scrape at 08:00, reply check every hour).
          <Code title="terminal">{`python scripts/run.py serve`}</Code>
        </Step>
        <Step title="Start the dashboard">
          <Code title="terminal">{`
cd dashboard
npm install
npm run dev                  # http://localhost:3000
`}</Code>
        </Step>
        <Step title="Fill it once by hand">
          A first scrape and score so the dashboard has something to show.
          <Code title="terminal">{`
python scripts/run.py scrape
python scripts/run.py score --limit 40
`}</Code>
        </Step>
        <Step title="Turn on the schedule">
          Installs the background jobs as macOS LaunchAgents, so the pipeline keeps working without a terminal open.
          <Code title="terminal">{`
python scripts/run.py tick install
python scripts/run.py tick status
`}</Code>
        </Step>
      </Steps>

      <H2>What is now running</H2>
      <Table
        head={["Job", "When", "Does"]}
        mono={[0]}
        rows={[
          ["com.zonuly.tick.free", "every 10 min", "The free work: replies, people, addresses, fill, hiring proof, discovery"],
          ["com.zonuly.tick.paid", "06:15 daily", "The model work inside the OpenRouter budget"],
          ["com.zonuly.tick.send", "every 5 min", "Sends at most one approved email whose slot is due"],
          ["com.zonuly.apify", "every 30 min", "Referrals: LinkedIn People tabs for the shortlist, tech team only"],
          ["com.zonuly.shortlist", "every 10 min", "Completes one shortlisted company at a time, Bengaluru first"],
          ["com.zonuly.audit", "hourly", "Checks every field is still filling and every channel answers"],
          ["com.zonuly.apify", "every 30 min", "Buys LinkedIn People tabs for companies short of referrers, inside the daily Apify cap"],
        ]}
      />
      <Callout kind="note" title="Referrals are Apify's job">
        Since 23 Oct 2026 the free people-search job is off (<C>tick.free_referral_job: false</C>): referrals come from the Apify
        job, the shortlist first and the tech team only. To pause all scraping, set <C>tick.scrapers_frozen: true</C>. See{" "}
        <A href="/docs/scheduling">Scheduling</A>.
      </Callout>
      <P>Details in <A href="/docs/scheduling">Scheduling &amp; the tick</A>.</P>

      <H2>Your first decision</H2>
      <P>
        Once roles are scored, let the brain rank the companies and open <strong>Best bets</strong>: today&apos;s company is
        at the top. See <A href="/docs/decide">The decision brain</A>.
      </P>
      <Code title="terminal">{`python scripts/run.py decide run`}</Code>

      <Callout kind="warn" title="Before you send anything real">
        Sending starts in dry-run (emails written to <C>outbox/</C>). Connect Gmail in <strong>Settings → Email</strong>,
        run <C>python scripts/run.py outreach preflight</C>, and only then set <C>outreach.send_mode: gmail</C>. Keep the
        laptop awake and on power during the send window, 10:00–19:00.
      </Callout>
    </Doc>
  );
}
