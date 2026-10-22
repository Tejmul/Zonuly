import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, H3, Lead, P, Table, UL } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Targeting" };

export default function Targeting() {
  return (
    <Doc slug="targeting">
      <Lead>
        Twenty-five emails a day is a hard budget, so the most important thing ZoNuLy decides is <strong>where not to
        spend them</strong>. Targeting is configuration, not code — every threshold below lives in <C>config.yaml</C>{" "}
        under <C>targeting</C>.
      </Lead>

      <H2>Who the roles are for</H2>
      <UL>
        <li><strong>Freshers and early-career engineers</strong> — up to 3 years of experience (<C>max_yoe: 3</C>). Senior roles are kept and flagged, never chased; a senior title caps a role&apos;s score at 35.</li>
        <li><strong>Remote-from-anywhere first.</strong> When a posting itself says “work from anywhere”, “remote — worldwide” or “no visa needed”, the role is marked <C>remote_anywhere</C> and those companies rank first.</li>
        <li><strong>Abroad:</strong> US, UK and Germany (plus the Netherlands and EU), remote from India or on a visa. Visa feasibility is weighed, never assumed — Germany is easiest, then the UK, then the US.</li>
        <li><strong>India: Bengaluru and Hyderabad only.</strong> No other Indian city is a target. A company is admitted to the India set only with a quote that places it in one of the two.</li>
      </UL>

      <H2>Which companies</H2>
      <H3>Underrated and recently funded</H3>
      <P>
        The sweet spot is a startup that raised recently and is hiring, but that most people haven&apos;t heard of — so a
        fresher&apos;s ask isn&apos;t lost in a pile. Funding window: pre-seed to Series B, within 30 months, under $400M
        raised. India discovery prefers rounds in the last 24 months, pre-seed to Series C.
      </P>
      <H3>Never the famous ones</H3>
      <P>
        A named list of hyped companies is excluded word by word (<C>targeting.hyped</C>), along with big tech and IT
        services firms. And the size rule excludes anything valued at $5B+ or that has raised $1B+. A borderline company
        ($400M–$1B raised, or valued $2–5B) stays only while it is actively hiring <em>and</em> pays in the top band.
      </P>
      <Callout kind="rule" title="Never by headcount">
        Employee count never decides anything. A large company that is hiring a lot and paying well can be a good target;
        a tiny one with no money is not. Judge by hiring activity, valuation and pay power.
      </Callout>

      <H2>Pay floors</H2>
      <Table
        head={["Rule", "Value", "What happens below it"]}
        rows={[
          ["Tier 1", "≥ ₹30 LPA stated", "—"],
          ["Tier 2", <>₹24–30 LPA by D24 — but <C key="t2">ppo_tier2_lpa</C> is 30 in config, so currently empty</>, "—"],
          ["Reject", "< ₹24 LPA stated", "Kept in the database, never chased."],
          ["India company floor", <><C key="f">targeting.india.min_lpa</C> = 25</>, "Hidden as below_pay_bar — not deleted; back automatically when a posting states more. Unknown pay is kept."],
          ["Internship stipend", "≥ ₹50,000 / month", "A hard bar — the stipend predicts the full-time offer."],
        ]}
      />
      <P>
        A missing pay figure is <em>not</em> a rejection: a company with engineers and verified hiring becomes a{" "}
        <strong>prospect</strong>. And a low grade only changes the order — it never removes a company or a role.
      </P>

      <H2>People are targeted too</H2>
      <Defs
        items={[
          ["Referral rank", "Founder 1 · senior / staff / founding engineer 2 · SDE / AI-ML engineer 3 · engineering manager 4 · technical recruiter 5 · general HR 6 · anyone else 9."],
          ["City lock", "For a Bengaluru company only people based in Bengaluru are kept; for Hyderabad, only Hyderabad — by the location on their own profile, never a school or a search snippet."],
          ["No alumni targeting", "The people layer targets roles, not colleges."],
          ["One of us per person", "Two candidates never write to the same person in the same month."],
        ]}
      />

      <H2>Where it shows up</H2>
      <P>
        The <A href="/docs/dashboard">★ India</A> page lists every Bengaluru and Hyderabad company that clears these
        rules (the hand-picked ones first). <strong>Companies → Worth chasing</strong> shows tier 1, tier 2 and
        prospects; <strong>Hire from anywhere</strong> the remote-anywhere set; <strong>Best bets</strong> ranks roles
        by selection odds inside the targeted set.
      </P>
    </Doc>
  );
}
