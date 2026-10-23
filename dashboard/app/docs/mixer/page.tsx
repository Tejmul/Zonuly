import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";
import { Funnel } from "@/components/docs/diagrams";

export const metadata: Metadata = { title: "The mixer" };

export default function Mixer() {
  return (
    <Doc slug="mixer">
      <Lead>
        <strong>Decide first, then scrape.</strong> The mixer (<C>jobhunter/mixer.py</C>) narrows the companies with free
        checks, and only the few that survive — and still lack something only a paid lookup can tell — get their job ads
        bought from Apify. It never runs on a schedule: it spends only when you press the button or run the command.
      </Lead>

      <H2>The free layers</H2>
      <P>Nothing is bought until a company has passed all of these, in order. Each one costs nothing.</P>
      <Table
        head={["Layer", "Passes when"]}
        rows={[
          ["1 · Pay power", "Its pay power is pays, deep pockets or funded — or it states pay (or a PPO) at the India floor"],
          ["2 · Location", "It sits in a decision pool: Bengaluru, Hyderabad, or Remote (a role open to someone in India)"],
          ["3 · No DSA", "India only: how DSA-heavy its interviews look is under 0.5. Remote tolerates DSA"],
          ["4 · The brain", <>The <A key="d" href="/docs/decide">decision brain</A> says target or fill first</>],
          ["5 · Not bought lately", "It was not bought in the last 14 days"],
          ["6 · Needs paid data", "Something is genuinely missing: fresh ads, stated pay, or (in India) a culture rating"],
        ]}
      />
      <P>The funnel as it stood on 23 Oct 2026:</P>
      <Funnel
        rows={[
          { label: "Decided by the brain", n: "2,479" },
          { label: "Pay power passes", n: "593" },
          { label: "In a pool", n: "593" },
          { label: "No DSA (India)", n: "572" },
          { label: "Target or fill first", n: "495" },
          { label: "Not bought in 14 days", n: "494" },
          { label: "Still needs paid data", n: "309" },
        ]}
      />
      <P>
        Survivors are queued best chance of an offer first, taking Bengaluru, Remote and Hyderabad in turn so each pool gets
        its share. A run buys the next two (<C>mixer.per_run</C>).
      </P>

      <H2>The paid layers</H2>
      <Steps>
        <Step title="LinkedIn ads">
          Every LinkedIn job ad from the company, in its own city (for Remote, ads open to India). If the company has no ad
          there, it stops here — nothing more is bought for it. The ads also name the company&apos;s LinkedIn page, which is
          kept (see <A href="/docs/fill">Fill</A>).
        </Step>
        <Step title="Naukri ads (India only)">
          Bought only while pay or culture is still unknown. Naukri ads state pay more often, and carry the company&apos;s
          AmbitionBox rating — the culture signal the brain reads (under 3.0/5 on ten or more reviews is a skip in India).
        </Step>
        <Step title="Back through the brain">
          Every ad is matched to the company by name before it is kept (a keyword search returns other employers too), goes
          through the same relevance gate and store as any scraper, then the roles are re-scored and the brain decides
          again. The run reports each company&apos;s chance of an offer before and after.
        </Step>
      </Steps>

      <H2>The money</H2>
      <Table
        head={["Setting", "Value", "Meaning"]}
        mono={[0, 1]}
        rows={[
          ["mixer.daily_usd", "$0.05", "The mixer’s own share of a day’s Apify spend — about a cent a company"],
          ["mixer.referral_reserve_usd", "$0.30", "It refuses to buy unless this much of today’s Apify cap stays free for referrals afterwards"],
          ["actors.apify.daily_usd", "$0.60", "The whole day’s Apify cap, shared with the People-tab job"],
          ["mixer.per_run · refresh_days", "2 · 14", "Companies per run · days before a company may be bought again"],
        ]}
      />
      <Callout kind="rule" title="Referrals come first">
        Apify is paid for to find the people who can refer you. The mixer gets a small slice, and the reserve means it can
        never spend the money the People-tab job needs that day. When the reserve would be touched, the <em>Buy next</em>
        button is disabled and the panel reads “not buying today: referrals first”. Every lookup goes into the same Apify ledger and 24-hour cache.
      </Callout>

      <H2>Where to use it</H2>
      <P>
        <strong>Best bets → Companies</strong> has a <em>Mixer</em> panel: the layer-by-layer funnel with a sample of who
        each layer dropped and why, the next companies it would buy, a <em>Buy next N (≈ $cost)</em> button, and today&apos;s
        spend against the mixer&apos;s share and the reserve.
      </P>
      <Code title="terminal">{`
python scripts/run.py mixer plan        # the funnel and who would be bought next — spends nothing
python scripts/run.py mixer run         # buy the next companies, re-score, decide again
python scripts/run.py mixer run --company 2802 --company 3110   # buy these instead of the plan
python scripts/run.py mixer history     # every paid lookup: found, matched, saved, cost
`}</Code>
    </Doc>
  );
}
