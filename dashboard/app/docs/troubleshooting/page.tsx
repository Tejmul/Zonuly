import type { Metadata } from "next";
import { C, Code, Doc, H2, Lead, P } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Troubleshooting" };

function Issue({ title, symptom, cause, fix }: { title: string; symptom: React.ReactNode; cause: React.ReactNode; fix: React.ReactNode }) {
  return (
    <section className="my-6 overflow-hidden rounded-xl border border-line bg-surface">
      <H2>{title}</H2>
      <dl className="-mt-2 divide-y divide-line border-t border-line text-[13.5px] leading-relaxed">
        {[["Symptom", symptom], ["Cause", cause], ["Fix", fix]].map(([k, v]) => (
          <div key={k as string} className="grid gap-1 px-4 py-3 sm:grid-cols-[110px_1fr]">
            <dt className="doc-label pt-0.5">{k}</dt>
            <dd className="text-ink-2 [&_strong]:text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default function Troubleshooting() {
  return (
    <Doc slug="troubleshooting">
      <Lead>Every failure mode below has happened at least once. Each lists what you see, why, and what to do.</Lead>
      <div className="[&_h2]:mx-4 [&_h2]:mt-4 [&_h2]:mb-4 [&_h2]:text-[18px]">
        <Issue
          title="Gmail refuses mail: 550 5.4.5"
          symptom={<>Emails go to failed with <C>Daily user sending limit exceeded</C> long before 500 a day.</>}
          cause="Gmail’s spam protection on a burst of near-identical mail to one domain, made worse by bounces."
          fix={<>Stop sending (unload <C>com.zonuly.tick.send</C>) and wait about 24 hours. Put refused emails back to approved. Keep <C>front_load: false</C>, <C>max_per_run: 1</C> and the 25 cap — finish a company over several days.</>}
        />
        <Issue
          title="Guessed addresses bounce"
          symptom="“Address not found” replies for firstname@company addresses."
          cause="The company’s pattern doesn’t hold for everyone — newer hires use another format, or an example was a person who left."
          fix={<>Bounced addresses are retired automatically; the brake stops guessed sends after 6 in 7 days. Approve verified addresses first, and watch the <strong>guessed</strong> flag in the Queue.</>}
        />
        <Issue
          title="Sends trickle out hours apart"
          symptom="A handful of emails over a whole afternoon."
          cause="The Mac was asleep — a closed lid sleeps it even with caffeinate — and jobs only run during brief wakes."
          fix="Keep the lid open and on power from 10:00 to 19:00. With max_per_run 1 nothing bursts when it wakes; slots simply wait."
        />
        <Issue
          title="database is locked"
          symptom={<>A command or send fails with <C>sqlite3.OperationalError: database is locked</C>.</>}
          cause="Another job — usually the referral cycle — held a long write transaction past the 30-second busy timeout."
          fix={<>Find the holder with <C>lsof jobhunter.db</C>. Stopping a referral run is safe (it reruns in 15 minutes; SQLite rolls back). Pause <C>com.zonuly.referrals</C> during a manual send.</>}
        />
        <Issue
          title="LinkedIn returns 999 or 429"
          symptom={<><C>actor status</C> shows a route cooling down; people searches find nothing new.</>}
          cause="LinkedIn throttles logged-out visitors from one IP after only a few profile views."
          fix="Let the breaker cool down (it resumes by itself). Don’t rotate IPs or use accounts — use Apify for People tabs instead."
        />
        <Issue
          title="Fill now does nothing"
          symptom="The button turns to Fill again and the company still shows missing parts."
          cause="The fill ran but found nothing new — e.g. roles only on an aggregator, or no careers page."
          fix="Read the line under the company: it names the reason for each missing part. Those gaps are real absences, not bugs."
        />
        <Issue
          title="The dashboard shows nothing"
          symptom="Pages load but every panel errors or is empty."
          cause="The API on :8000 isn’t running, or was started before a backend change."
          fix={<>Start or restart it: <C>python scripts/run.py serve</C>. Check <C>curl localhost:8000/api/health</C>.</>}
        />
        <Issue
          title="Apify says the daily cap is reached"
          symptom={<><C>apify daily cap: $0.148 spent + $0.12 estimated &gt; $0.15</C>.</>}
          cause="The free-credit guard: the day’s budget is used. Nothing was charged."
          fix={<>Wait until midnight, run a smaller <C>--apify-max</C>, or raise <C>actors.apify.daily_usd</C> to spend the free $5 faster.</>}
        />
      </div>
      <P>Still stuck? The hourly audit and the watchdog record most problems before you see them:</P>
      <Code title="diagnose">{`
python scripts/run.py doctor
python scripts/run.py audit show
python scripts/run.py notify watchdog
python scripts/run.py research doctor
`}</Code>
    </Doc>
  );
}
