import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, H3, Lead, P, Table } from "@/components/docs/prose";
import { Timeline } from "@/components/docs/diagrams";

export const metadata: Metadata = { title: "Scheduling & the tick" };

export default function Scheduling() {
  return (
    <Doc slug="scheduling">
      <Lead>
        The pipeline runs itself. Six macOS LaunchAgents start small, bounded slices of work on a timer; each takes a
        lock, spends a time budget, records what it did, and exits. Nothing runs forever, so nothing hangs forever.
      </Lead>

      <Callout kind="note" title="Who does what, since 23 Oct 2026">
        Company and role scraping runs on its own again (<C>tick.scrapers_frozen: false</C>, <C>scheduler.daily_scrape: true</C>).
        <strong>Referrals are Apify&apos;s job</strong>: the Apify job buys LinkedIn People tabs for the shortlist, tech team only,
        and the free people-search job stays off (<C>tick.free_referral_job: false</C>). The shortlist job completes one
        shortlisted company every 10 minutes. To pause all scraping, set <C>tick.scrapers_frozen: true</C> and run{" "}
        <C>tick install</C>.
      </Callout>

      <H2>The background jobs</H2>
      <Table
        head={["Agent", "Command", "Schedule", "Lock"]}
        mono={[0, 1, 3]}
        rows={[
          ["com.zonuly.tick.free", "tick run --kind free", "every 10 min, and at login", "data/tick.lock"],
          ["com.zonuly.tick.paid", "tick run --kind paid", "06:15 daily", "data/tick.lock"],
          ["com.zonuly.tick.send", "tick run --kind send", "every 5 min", "data/send.lock"],
          ["com.zonuly.audit", "audit run", "hourly", "—"],
          ["com.zonuly.apify", "tick run --kind apify", "every 30 min, and at login", "data/apify.lock"],
          ["com.zonuly.shortlist", "tick run --kind shortlist", "every 10 min, and at login", "data/shortlist.lock"],
          ["com.zonuly.referrals", "referrals run", "off (tick.free_referral_job: false)", "data/referrals.lock"],
        ]}
      />
      <P>
        A job that finds its lock held exits at once with <C>{`{"skipped": "another tick is running"}`}</C>. Sending has its
        own lock so it never waits behind a 40-minute free tick. Logs go to <C>logs/tick-*.log</C>,{" "}
        <C>logs/apify.log</C>, <C>logs/shortlist.log</C> and <C>logs/audit-run.log</C>.
      </P>

      <H2>Inside one free tick</H2>
      <P>
        The free tick has a 40-minute budget (<C>tick.budget_seconds: 2400</C>), split 70 / 30 between finding people
        and working on companies. Every step runs inside its own error boundary — one failing stage never ends the tick.
      </P>
      <Timeline
        caption="A free tick, in order. Bold steps are the referral work that gets most of the time."
        items={[
          { at: "1", label: "Read replies from Gmail; draft follow-ups into review" },
          { at: "2", label: "Full board scrape — once per day" },
          { at: "3", label: "Hygiene: merge duplicates, quarantine junk" },
          { at: "4", label: "Guard: expire stale hiring proof, recompute completeness" },
          { at: "5", label: "Refresh priority scores" },
          { at: "6", label: "People: find referrers at the next companies in the queue", strong: true },
          { at: "7", label: "Addresses: learn patterns, address ready referrers (MX-checked)", strong: true },
          { at: "8", label: "Hunter (2 lookups) and one Apify People tab, inside their budgets", strong: true },
          { at: "9", label: "Re-verify referrers whose evidence is 90+ days old", strong: true },
          { at: "10", label: "Fill: complete up to 20 companies (★ list → India → remote)" },
          { at: "11", label: "Rotating company steps: funding RSS, websites, boards, careers, revalidate, verify, YC, India discovery" },
          { at: "12", label: "Cheap tail: pay extraction, grading, pay floor, selection odds, the decision brain, the complete bar" },
        ]}
      />
      <H3>Rotation and saturation</H3>
      <P>
        Company steps take turns through a saved cursor, so each gets its share over successive ticks. When the fill steps
        find almost nothing left to do, the tick spends the spare time discovering new companies instead.
      </P>

      <H2>The paid tick</H2>
      <P>
        Once a day at 06:15 (and in hourly slices) the paid tick spends the metered budgets — Exa, scrape.do and model
        calls — <strong>paced through the day</strong>: by hour <em>h</em>, at most <C>cap × (h+1) / 24</C> of a daily cap
        may be used. It discovers companies, fills facts, writes descriptions and stories, reads levels.fyi, regrades
        everything, and rebuilds the knowledge-graph brief.
      </P>

      <H2>The free referral job (off)</H2>
      <P>
        Off since 23 Oct 2026: referrals come from Apify. When <C>tick.free_referral_job</C> is true, every 15 minutes a
        20-minute cycle works only on people, city by city: Bengaluru (★ list first), then
        Hyderabad. It <strong>claims</strong> the companies it is working on, so the free tick&apos;s people step never searches
        the same company at the same time. Per company: YC founders and team pages → one Exa people search → public LinkedIn
        (city-scoped) → GitHub → public mentions → web addresses → address fill.
      </P>

      <H2>The Apify job</H2>
      <P>
        The referral engine. Every 30 minutes, it buys LinkedIn People tabs from Apify for up to three companies — the decision
        brain&apos;s shortlist first (Bengaluru by rank, then Hyderabad, then Remote), any company with fewer than two engineers
        on file — searching only engineering titles (<C>actors.apify.tech_titles</C>), since Apify bills per profile — 25
        people per company, in the company&apos;s own city, each company bought once, inside the day&apos;s Apify cap (<C>actors.apify.daily_usd: 0.60</C>, the
        $19 Starter credit spread over a month), never overlapping itself. Each run first reads up to 30 homepages, for free,
        for the company&apos;s LinkedIn page, because a People tab can only be bought for a company whose page is known. When
        the day&apos;s money is spent, it stops until midnight. <A href="/docs/mixer">The mixer</A> draws on the same cap, and
        always leaves $0.30 of it for this job.
      </P>

      <H2>The send job</H2>
      <P>
        Every 5 minutes, it sends <strong>at most one</strong> approved email whose time slot has come. The day&apos;s 25 slots
        are spread across 10:00–19:00 with jitter, the same for every run that day. Details in{" "}
        <A href="/docs/sending">Sending &amp; deliverability</A>.
      </P>

      <H2>The API&apos;s own scheduler</H2>
      <P>
        When the API is running (<C>scripts/run.py serve</C>), APScheduler adds two jobs: the <strong>daily cycle</strong> at
        08:00 (scrape if the tick hasn&apos;t, salaries, lexical prefilter, rubric scoring, notifications, graph sync) — off
        while <C>scheduler.daily_scrape</C> is false — and a <strong>reply cycle</strong> every hour. Rubric scoring with the
        model runs only here, not in the tick. The mixer is never scheduled anywhere: it spends only when asked.
      </P>

      <Code title="inspect and control">{`
python scripts/run.py tick status            # loaded? last run? what it did
launchctl list | grep zonuly                 # what macOS has loaded
launchctl unload ~/Library/LaunchAgents/com.zonuly.tick.send.plist   # pause sending
launchctl load   ~/Library/LaunchAgents/com.zonuly.tick.send.plist   # resume
`}</Code>
      <Callout kind="warn" title="The laptop must be awake">
        launchd can&apos;t run jobs while the Mac sleeps, and closing the lid sleeps it even with <C>caffeinate</C> running. Keep
        it open and on power during the send window, or sends wait until it wakes.
      </Callout>
    </Doc>
  );
}
