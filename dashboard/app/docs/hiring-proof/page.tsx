import type { Metadata } from "next";
import { C, Callout, Defs, Doc, H2, Lead, P, Step, Steps } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Hiring proof" };

export default function HiringProof() {
  return (
    <Doc slug="hiring-proof">
      <Lead>
        A referral ask to a company that isn&apos;t hiring wastes one of the day&apos;s 25 emails. So a company counts as
        hiring only when <strong>its own careers page or its own job board</strong> lists an open role.
      </Lead>

      <H2>How it is checked</H2>
      <Steps>
        <Step title="The ATS board">If the company has a Greenhouse, Lever or Ashby board, read its titles from the JSON API.</Step>
        <Step title="The careers page">Otherwise try the careers paths on its own domain (<C>/careers</C>, <C>/jobs</C>, …), then homepage links, then a keyless search for the page. Hosted boards like Keka, Darwinbox and Zoho Recruit are recognised.</Step>
        <Step title="Read the roles">Board JSON first, then JSON-LD job postings, then — only for literal titles — the cheap model.</Step>
        <Step title="Match the claim">If a posting elsewhere claimed a role, check that title against what their own page lists.</Step>
      </Steps>

      <H2>Verdicts</H2>
      <Defs
        items={[
          [<C key="v">verified</C>, "An open role on their own page or board. Goes stale after 30 days and is re-checked."],
          [<C key="r">role_missing</C>, "A careers page exists, but no role could be read from it."],
          [<C key="n">not_authorized</C>, "Nothing they control backs the claim — e.g. roles appear only on an aggregator like Instahyre or Naukri."],
          [<C key="u">unreachable</C>, "Their site couldn’t be read: down, a bot wall, or no website on file."],
        ]}
      />
      <P>
        Relevant open roles found during the check are stored as roles, so hiring proof also feeds the role list and the
        selection odds.
      </P>
      <Callout kind="rule" title="Why aggregators don’t count">
        A listing on a job aggregator can be months old or posted by a third party. The company&apos;s own page is the only
        source that says “we are hiring now” in its own words.
      </Callout>
    </Doc>
  );
}
