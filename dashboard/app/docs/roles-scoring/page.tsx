import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, H3, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Roles & scoring" };

export default function RolesScoring() {
  return (
    <Doc slug="roles-scoring">
      <Lead>
        Two different questions get two different scores. <strong>Match</strong>: does this role fit the resume?{" "}
        <strong>Selection odds</strong>: if we ask, how likely is it to land? The second decides where the 25 emails go.
      </Lead>

      <H2>Match score</H2>
      <Steps>
        <Step title="A free lexical gate">
          <C>fit.py</C> measures rare-term-weighted vocabulary overlap between the resume and the description — no model, no
          embeddings. Only roles above the 70th percentile go further. <C>fit-explain JOB_ID</C> shows the reasoning.
        </Step>
        <Step title="A model rubric on the survivors">
          The <C>judge</C> model alias scores each survivor against a rubric and returns reasons and skill gaps.
        </Step>
        <Step title="Hard caps">
          A senior title caps the score at 35; stated pay under ₹30 LPA at 40; a description under 200 characters at 60. A
          score of 65 or more is a <strong>high match</strong>.
        </Step>
      </Steps>
      <Callout kind="rule">A low score orders a role last. It never deletes it.</Callout>

      <H2>Selection odds</H2>
      <P>
        <C>confidence.py</C> computes a deterministic 0–100 per role from weighted signals, with a separate profile for each
        market, then applies blocker caps.
      </P>
      <H3>India profile</H3>
      <Table
        head={["Signal", "Weight"]}
        rows={[["Fresher-friendly", "22"], ["Skills overlap", "16"], ["A referrer exists", "15"], ["Pay", "12"], ["Skill gap", "8"], ["Stage", "8"], ["Recency", "7"], ["City", "6"], ["Hiring proven", "6"]]}
      />
      <H3>Remote profile</H3>
      <P>
        Skills 15, remote terms 14, seniority fit 13, referrer 13, timezone 8, gap 8, international hiring 6, stage 6,
        competition 5, async 4, recency 4, hiring 4.
      </P>
      <H3>Blockers</H3>
      <P>
        Some facts cap the score however good the rest is: needs a visa (10), 5+ years required (30), senior title (35),
        onsite elsewhere (20), hiring not authorised (40), stale posting (40), overrated company (30), below the pay bar (35).
      </P>
      <Table
        head={["Band", "Score"]}
        rows={[["high", "≥ 65"], ["medium", "≥ 48"], ["low", "≥ 30"], ["long shot", "< 30"]]}
      />

      <H2>Needs briefs</H2>
      <P>
        For the top roles, <C>needs.py</C> writes a brief: what the role actually needs, mapped line by line to the resume —
        each need either <strong>covered</strong> by something you did, or marked <strong>UNCOVERED</strong>. Drafts never claim
        an uncovered need.
      </P>
      <P>
        The best role per company rolls up to the company. Every role, by market and band, is the <strong>Roles</strong>{" "}
        tab of <A href="/docs/dashboard">Best bets</A>; on 13 Oct 2026 the first run ranked 1,224 India roles (53 high, 280
        medium) and 1,920 remote roles (28 high, 150 medium).
      </P>
      <P>
        These scores and briefs are what the <A href="/docs/decide">decision brain</A> reads when it asks whether a role is
        open and whether the resume passes a screen — and the uncovered needs across the shortlist become{" "}
        <A href="/docs/upskill">Upskill</A>&apos;s learning plan.
      </P>
    </Doc>
  );
}
