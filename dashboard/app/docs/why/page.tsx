import type { Metadata } from "next";
import { A, Callout, Doc, H2, Lead, P, Table, UL } from "@/components/docs/prose";
import { Funnel } from "@/components/docs/diagrams";

export const metadata: Metadata = { title: "Why ZoNuLy exists" };

function Quote({ children }: { children: React.ReactNode }) {
  return (
    <blockquote className="my-6 border-l-2 border-ink pl-5 text-[16px] leading-[1.7] text-ink">
      {children}
    </blockquote>
  );
}

export default function Why() {
  return (
    <Doc slug="why">
      <Lead>
        ZoNuLy was built by final-year B.Tech (CS + AI) students at Newton School of Technology, class of 2027, who
        decided not to depend on campus placement and to find their own way into well-paid engineering roles.
      </Lead>
      <P>
        Their work is shipped work — fine-tuning on rented GPUs, a database migration that cut infrastructure cost by
        95%, a founding-team AWS build, a retrieval chatbot, a freelance marketplace with a thousand members. What they
        are not is competitive programmers, and the hiring processes at famous companies are built around exactly that.
      </P>

      <H2>The opportunity</H2>
      <Quote>
        An engineer sitting in an office in the US, UK or Germany costs the employer ₹1.5–2 crore a year all-in. The
        same company can hire an equally capable engineer working from India for ₹20–50 lakh, and it is a rounding
        error to them.
      </Quote>
      <P>
        That gap is not exploited by the household names — they are hyped, oversubscribed and gated by algorithm
        puzzles. It is exploited by <strong>recently funded startups, seed to Series B</strong>, which have money,
        urgency and small teams. And those companies mostly <strong>don&apos;t post on the big job boards</strong>:
        they post on their own careers page and wait.
      </P>

      <H2>Four problems, one pipeline</H2>
      <Table
        head={["Problem", "What it means", "How ZoNuLy answers it"]}
        rows={[
          ["We never see most of the good jobs", "The roles live on company-owned ATS pages, not on LinkedIn or Naukri.", <>Reads Greenhouse, Lever, Ashby, Workable and careers pages directly. <A href="/docs/discovery">Discovery</A></>],
          ["We can’t tell which are worth our time", "“AI Engineer” can mean eight years of ML or a founding role a strong student fits.", <>Scores every role against the resume, with the reasons. <A href="/docs/roles-scoring">Roles &amp; scoring</A></>],
          ["Cold applications go nowhere", "A portal submission is one of 800. A referral, even a weak one, puts you in a stack of ten.", <>Finds the engineers and founders who could refer you. <A href="/docs/people">People</A></>],
          ["Referrals don’t scale by hand", "Twenty minutes per company, per person — and there is coursework.", <>Does the nineteen minutes; leaves you the ten seconds. <A href="/docs/outreach">Review gate</A></>],
        ]}
      />
      <Quote>
        Our judgement matters at exactly one moment — “yes, send that email to that person” — and that takes ten
        seconds if the other nineteen minutes are done.
      </Quote>

      <H2>What “done” looks like</H2>
      <P>
        You open the app once a day. Overnight it found companies you had never heard of, with an honest grade and a
        sentence explaining it; found real people at the good ones with their role labelled; and wrote the emails. You
        read the queue, fix the lines that don&apos;t sound like you, and approve. That is ten minutes.
      </P>

      <H2>The arithmetic</H2>
      <P>
        Volume is bounded by what a personal Gmail can send without being treated as a spammer — about 25 a day. That
        is why <strong>who</strong> it sends to matters far more than how many companies it finds. Per person, per month:
      </P>
      <Funnel
        rows={[
          { label: "Referral asks sent (25 a day × ~20 working days)", n: "~500" },
          { label: "Replies (5–10%)", n: "25–50" },
          { label: "Positive replies", n: "8–20" },
          { label: "Interviews", n: "3–8" },
        ]}
      />

      <H2>What it will never do</H2>
      <UL>
        <li><strong>Auto-apply.</strong> No portal spam, no bulk applications. One person, one role, one ask.</li>
        <li><strong>Send without you.</strong> Every email passes the review gate. See <A href="/docs/guarantees">Guarantees</A>.</li>
        <li><strong>Invent anything.</strong> No project, number or connection that isn&apos;t in the source material.</li>
        <li><strong>Touch your accounts to scrape.</strong> Your LinkedIn, X and GitHub stay yours; a ban would destroy the network referrals depend on.</li>
        <li><strong>Send like spam.</strong> 25 a day, spread through the working day, at most three into one company, written like a person writes in Gmail, no tracking pixels.</li>
      </UL>

      <Callout kind="note" title="The budget">
        It is meant to cost a student little — ₹1,000–2,500 a month at most. Models come through OpenRouter, free models
        first, behind a cost ledger. Apify is on its Starter plan, $19 of credit a month, spent evenly at
        $0.60 a day and kept for finding referrers first. Every paid call passes a budget check before it is made.
      </Callout>
    </Doc>
  );
}
