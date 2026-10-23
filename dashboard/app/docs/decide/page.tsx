import type { Metadata } from "next";
import { A, C, Callout, Code, Defs, Doc, H2, H3, Lead, P, Stats, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "The decision brain" };

export default function Decide() {
  return (
    <Doc slug="decide">
      <Lead>
        Thousands of companies are too many to work on. The decision brain (<C>jobhunter/decide.py</C>) asks five plain
        questions about every Bengaluru, Hyderabad and remote company, turns the answers into{" "}
        <strong>one chance of an offer</strong>, keeps the best of them on a shortlist, and gives you{" "}
        <strong>one company a day</strong> — fill it, or ask its referrer.
      </Lead>

      <H2>What the number means</H2>
      <P>
        Best bets shows each company as, for example, <strong>“19% chance of an offer — about 1 in 5 companies like
        this”</strong>. Read it literally: if you worked on five companies that look like this one, from the first email to
        the last interview, you would expect about one offer among them. The “1 in N” is simply one divided by the chance,
        rounded.
      </P>
      <P>
        Hover the number and it gives the range it is fairly sure of — “most likely 19%; we are fairly sure it is between 7%
        and 32%” (an 80% interval). Beside it: how many times better this company&apos;s odds are than the typical company on
        the same shortlist (“4.1× better than the typical company on your list”), and how DSA-heavy their interviews look.
      </P>
      <Stats
        items={[
          { n: "19%", label: "Scalio · Bengaluru", hint: "about 1 in 5 · 7–32% · 4.1× the list" },
          { n: "18%", label: "SerpApi · Remote", hint: "about 1 in 5 · 7–32% · 5.8× the list" },
          { n: "17%", label: "Ctruh · Bengaluru", hint: "about 1 in 6 · 7–29% · 3.8× the list" },
          { n: "250", label: "on the shortlists", hint: "Bengaluru 100 · Hyderabad 50 · Remote 100" },
        ]}
      />
      <P>The top of the shortlists as decided on 23 Oct 2026; the live figures are on Best bets.</P>

      <H2>The five questions</H2>
      <P>
        Each company gets the same five yes/no questions. Every answer is a probability worked out from fields the pipeline
        already holds, with the evidence quoted — no model is asked to guess.
      </P>
      <Table
        head={["Question", "In plain words", "Read from"]}
        rows={[
          ["open", "Is a role open right now that a 2027 graduate can apply for?", "Its scored roles — the more fresher roles, the surer"],
          ["reach", "Will a referral ask get a reply?", "A reply rate learned from your own sent mail, over up to three asks, and whether a referrer is ready"],
          ["screen", "Does the resume pass a cold screen?", "How well your skills cover the best role, its gaps, and company size"],
          ["loop", "Do you clear their interview loop?", "Your DSA level against how DSA-heavy their interviews look, plus skills fit"],
          ["pay", "Will they pay at least your floor?", "Stated pay, PPO, or the company’s pay power"],
        ]}
      />
      <H3>How they combine</H3>
      <P>
        A referral matters most at the screen: it multiplies the screen&apos;s odds by six. So the chance of an interview call
        is <em>(reply × screen with a referral) + (no reply × cold screen)</em>, and
      </P>
      <Code title="the formula" lang="text">{`
chance of an offer = a role is open × you get an interview call × you clear the loop × they pay your minimum
`}</Code>
      <P>
        Best bets draws those four as bars and marks the <strong>weakest</strong> one — usually the place a fill, a referrer
        or practice would move the number most. Each answer is held as a range rather than a single value (stronger
        evidence, narrower range); the brain samples 4,000 times, seeded per company so the same inputs always give the
        same answer, and reports the middle and the 80% band.
      </P>

      <H2>The four verdicts</H2>
      <Defs
        items={[
          [<strong key="t">Target</strong>, "Enough is known and someone is ready to ask. Write to them."],
          [<strong key="f">Fill first</strong>, <>Too little is known to decide — fewer than 55% of the seven deciding fields (description, hiring checked, size or stage, pay evidence, a scored role, people searched, a ready referrer) — or nobody is ready to ask yet. A remote company whose pay is unknown is also fill-first. The brain abstains rather than guesses: fill it, then decide.</>],
          [<strong key="a">After DSA</strong>, <>India only. Their interviews are DSA-heavy (intensity 0.60 or more) and well above your level (more than 0.15 over it), and they pay at the top (₹35 LPA or more) or the loop is extreme (0.80+). Parked, not dropped: raise <C>decide.me.dsa_level</C> as you practise and they come back on the next run.</>],
          [<strong key="s">Skip</strong>, <>A guardrail fails: quarantined, do-not-contact, overrated, a rejected tier, or below the pay bar. In India, a culture rating under 3.0/5 on at least ten AmbitionBox reviews (read by <A href="/docs/mixer">the mixer</A>). For remote, thin pay. A hiring claim their own site doesn&apos;t back is <em>not</em> a skip — it just makes “a role is open” unlikely until a fill re-checks it.</>],
        ]}
      />
      <Callout kind="note" title="Remote is judged differently">
        A remote company is one with a role open to someone in India (remote-anywhere or remote-from-India), wherever it is
        based. Remote work is itself the culture bar, and a few DSA rounds are fine — DSA only lowers the loop answer there.
        The one hard bar for remote is pay: thin pay is a skip, unknown pay is fill-first.
      </Callout>

      <H2>The shortlists</H2>
      <P>
        Among <em>target</em> and <em>fill first</em>, the best by chance of an offer are kept per pool:{" "}
        <strong>Bengaluru 100, Hyderabad 50, Remote 100</strong> (<C>decide.shortlist</C>). On Best bets → Companies the tabs
        are Bengaluru, Hyderabad, Remote, After DSA and Skipped; the parked tabs show why each company is parked. On 23 Oct
        2026 the brain had decided 2,479 companies:
      </P>
      <Table
        head={["Pool", "Target", "Fill first", "After DSA", "Skip", "Shortlisted"]}
        rows={[
          ["Bengaluru", "136", "1,106", "7", "179", "100"],
          ["Hyderabad", "10", "278", "1", "71", "50"],
          ["Remote", "109", "485", "0", "97", "100"],
        ]}
      />

      <H2>One company a day</H2>
      <Steps>
        <Step title="Picked on the first look of the day">
          The first time Best bets (or <C>decide today</C>) is opened on a day — Indian time — the best shortlisted company is
          picked, skipping any picked in the last 30 days and any you are already writing to.
        </Step>
        <Step title="Fill it, or ask">
          A <em>target</em> pick says <strong>Ask a referrer</strong> and opens the company page. A <em>fill first</em> pick
          says <strong>Fill it</strong>: description, hiring proof, facts and referrers are filled (a few minutes), then the
          brain decides it again and the card updates.
        </Step>
        <Step title="Mark it">
          <strong>Mark asked</strong> when you have written. <strong>Skip</strong> picks the next company on the shortlist
          for today. What the brain said at the moment of the pick is frozen, so it can be scored later.
        </Step>
      </Steps>

      <H2>Your DSA level</H2>
      <P>
        How DSA-heavy a company&apos;s interviews are (0–100%) is read from its own postings and description — LeetCode,
        HackerRank, “data structures and algorithms” push it up; a posting that says it cares less about LeetCode pulls it
        down — and, when the text says nothing, from size, stage and money. Your own level is one number in{" "}
        <C>config.yaml</C>:
      </P>
      <Code title="config.yaml" lang="yaml">{`
decide:
  me:
    dsa_level: 0.35   # 0..1, your interview-DSA ability today — raise it as you practise
`}</Code>
      <P>
        <A href="/docs/upskill">Upskill</A> shows what raising it to 0.5, 0.65 or 0.8 would be worth, and which parked
        companies would come back.
      </P>

      <H2>Recording what happened, and checking the brain</H2>
      <P>
        The brain gets less wrong only if it hears how things turned out. Record each company&apos;s outcome from the
        terminal (there is no dashboard button for this yet):
      </P>
      <Code title="terminal">{`
python scripts/run.py decide outcome 2802 replied     # replied | referred | interview | offer | rejected | no_response
python scripts/run.py decide calibrate                 # how good its numbers have been
`}</Code>
      <P>
        <C>calibrate</C> compares what the brain said with what happened, using proper scoring rules (the Brier score and log
        loss) for two things: did a written-to company reply, and did a finished story end in an offer. Under twenty
        outcomes it says <em>too few to judge</em>; after that, whether it beats simply guessing the average. The reply rate
        itself starts at 10% and is updated by every cold email sent and every positive reply.
      </P>

      <H2>When it runs</H2>
      <P>
        Every free tick decides all the companies again after the role scores, and the shortlist job re-decides after each
        company it completes. You can also run it any time — <C>decide run</C>, a Fill from today&apos;s card, or a mixer run —
        and the shortlist shows when it last ran.
      </P>
      <Code title="terminal">{`
python scripts/run.py decide run                       # decide everything, re-rank the shortlists
python scripts/run.py decide top --pool Bengaluru      # the shortlist; --verdict after_dsa | skip for the parked
python scripts/run.py decide show 2802                 # every question, its chance and its evidence
python scripts/run.py decide today                     # today's company; --asked, or --skip "reason"
python scripts/run.py decide fill                      # fill today's company completely, then decide it again
`}</Code>
      <Callout kind="note" title="Where the method comes from">
        The approach — typed yes/no questions, each answered with a probability, abstaining when the evidence is thin, and
        scored against outcomes — is borrowed from Laya (github.com/NandhaKishorM/laya). Laya answers with a 421M-parameter
        model; this machine runs no local model, so here every answer comes from fields already in the database.
      </Callout>
    </Doc>
  );
}
