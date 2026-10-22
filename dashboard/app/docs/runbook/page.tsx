import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, Lead, P, Table, UL } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Runbook" };

export default function Runbook() {
  return (
    <Doc slug="runbook">
      <Lead>Day-to-day operation: the ten-minute routine, where to look when something seems off, and how to stop, start and back up safely.</Lead>

      <H2>The daily routine</H2>
      <UL>
        <li><strong>Morning:</strong> open the <strong>Queue</strong>, read each draft, fix lines that don&apos;t sound like you, approve or reject. Ten minutes.</li>
        <li><strong>10:00–19:00:</strong> keep the laptop open and on power — approved mail leaves one email every ~20 minutes.</li>
        <li><strong>Evening:</strong> glance at <strong>Replies</strong> and <strong>Scraping health</strong>.</li>
      </UL>

      <H2>Starting and stopping</H2>
      <Code title="the API">{`
python scripts/run.py serve                               # foreground
nohup .venv/bin/python scripts/run.py serve >> logs/serve.log 2>&1 &   # detached
pkill -f "scripts/run.py serve"                           # stop
`}</Code>
      <P>The API does not reload on code changes; restart it after editing backend code. Check nothing is mid-task first:</P>
      <Code title="before restarting">{`curl -s localhost:8000/api/tasks | jq '[.[] | select(.status=="running") | .name]'`}</Code>
      <Code title="background jobs">{`
python scripts/run.py tick status
launchctl unload ~/Library/LaunchAgents/com.zonuly.tick.send.plist   # pause sending
launchctl load   ~/Library/LaunchAgents/com.zonuly.tick.send.plist   # resume sending
`}</Code>

      <H2>Where to look</H2>
      <Table
        head={["Question", "Look at"]}
        rows={[
          ["Did the last cycle run, and what did it do?", <C key="1">tick status</C>],
          ["Is anything stuck or failing?", <>Scraping health page · <C key="2">audit show</C> · <C key="3">notify watchdog</C></>],
          ["Did emails go out?", <>Tracker page · <C key="4">outreach ledger</C> · <C key="5">logs/tick-send.log</C></>],
          ["Why is a company still empty?", "Graph → Fill now — the result names the reason for each missing part"],
          ["Is LinkedIn blocking us?", <C key="6">actor status</C>],
          ["How much have models cost?", <C key="7">models costs --month</C>],
        ]}
      />
      <Table
        head={["Log", "Written by"]}
        mono={[0]}
        rows={[
          ["logs/tick-free.log · tick-paid.log · tick-send.log", "The three tick kinds (JSON summary per run)"],
          ["logs/referrals.log", "The referral job"],
          ["logs/audit-run.log · audit.log", "The hourly audit"],
          ["logs/serve.log", "The API"],
          ["data/actors/runs.jsonl", "Every actor run"],
        ]}
      />

      <H2>Backups</H2>
      <Code title="copy the live database safely">{`sqlite3 jobhunter.db ".backup data/backups/jobhunter-$(date +%F-%H%M).db"`}</Code>
      <Callout kind="warn">Never commit <C>jobhunter.db</C> or <C>data/backups/</C> — both are git-ignored on purpose, and the repository is public.</Callout>

      <H2>Recording work</H2>
      <P>
        After any change worth remembering: <C>python scripts/run.py kg note &quot;what changed&quot; --about &lt;node ids&gt;</C>.
        See <A href="/docs/knowledge-graph">Knowledge graph</A>.
      </P>
    </Doc>
  );
}
