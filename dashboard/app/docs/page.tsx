import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, H3, Lead, P, PageCards, UL } from "@/components/docs/prose";
import { Sieve } from "@/components/docs/sieve";
import { PipelineDiagram } from "@/components/docs/diagrams";
import { STAGES } from "@/lib/docs-content";

export const metadata: Metadata = { title: "Overview" };


export default function Overview() {
  return (
    <Doc
      slug=""
      hero={
        <>
          <div className="doc-label mb-4">ZoNuLy · Documentation</div>
          <h1 className="max-w-[16ch] font-[family-name:var(--font-display)] text-[40px] leading-[0.98] font-semibold tracking-[-0.045em] sm:text-[58px]">
            Five thousand companies. One person to ask.
          </h1>
          <div className="mt-10">
            <Sieve />
          </div>
        </>
      }
    >
      <Lead>
        ZoNuLy finds engineering roles at <strong>underrated, recently funded companies</strong>, proves they are
        really hiring, finds <strong>the real people who could refer you</strong>, and writes each of them one honest
        email — then <strong>stops and waits for you</strong>. You spend ten minutes a day approving; it spends the
        other nineteen hours doing the rest.
      </Lead>

      <P>
        It runs on one laptop: a Python pipeline over a single SQLite file, a FastAPI server, a Next.js dashboard,
        and a handful of background jobs that keep filling the gaps around the clock. Models are reached through
        OpenRouter behind a cost ledger; nothing runs a local model. Every fact it shows sits next to the sentence
        it came from, and anything it could not prove is shown as missing — never guessed.
      </P>


      <H2>The pipeline in one picture</H2>
      <P>
        Twelve stages take a company nobody has heard of to a sent referral ask. Eleven are automated. One —
        deciding that <em>this</em> email goes to <em>this</em> person — is yours, and the system is built so that
        decision takes seconds because everything before it is already done.
      </P>
      <PipelineDiagram stages={STAGES} />

      <H2>What makes it different</H2>
      <H3>It looks where others don&apos;t</H3>
      <P>
        Most good early-career roles at funded startups never reach the big job boards — they sit on the company&apos;s
        own Greenhouse, Lever or Ashby page. ZoNuLy reads those boards directly, and deliberately skips the household
        names whose roles get thousands of applicants. See <A href="/docs/targeting">Targeting</A>.
      </P>
      <H3>It asks a person, not a portal</H3>
      <P>
        A cold application is one of eight hundred; a referral from an engineer puts you in a stack of ten. So the
        product isn&apos;t a list of jobs — it is <strong>a company that is hiring, a role you fit, and a named person
        who works there now with an address that will reach them</strong>. The dashboard calls that “ready to ask”.
      </P>
      <H3>It refuses to make things up</H3>
      <P>
        Every description, funding round, address and claim of employment carries its evidence. An email that
        would need a fact the system doesn&apos;t have is not written. An address is labelled <C>verified</C>,{" "}
        <C>pattern-guessed</C> or <C>scraped</C>, and the label is never dropped. See{" "}
        <A href="/docs/guarantees">Guarantees &amp; guardrails</A>.
      </P>

      <Callout kind="rule" title="The one rule above all others">
        Nothing is ever sent without a human approving that exact email. No background job can send an unapproved
        message, and the daily cap holds even when you ask it to go faster.
      </Callout>

      <H2>Where to go next</H2>
      <PageCards slugs={["why", "quickstart", "pipeline", "architecture", "dashboard", "cli"]} />

      <H2>How these docs are organised</H2>
      <UL>
        <li><strong>Start here</strong> — what it is, why it exists, and how to run it.</li>
        <li><strong>Concepts</strong> — the vocabulary, the targeting rules, the guarantees and the settled decisions.</li>
        <li><strong>Architecture</strong> — the stack, the data model, the background schedule and the knowledge graph.</li>
        <li><strong>The pipeline</strong> — one page per stage, from discovery to the calendar.</li>
        <li><strong>Reference</strong> — every CLI command, API endpoint and configuration key.</li>
        <li><strong>Operations</strong> — the runbook and the known failure modes, with fixes.</li>
      </UL>
      <P>Press <C>⌘K</C> anywhere to search every page.</P>
    </Doc>
  );
}
