import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Email addresses" };

export default function EmailAddresses() {
  return (
    <Doc slug="email-addresses">
      <Lead>
        A wrong address doesn&apos;t just fail — it bounces, and bounces are how Gmail decides you are a spammer. So the rule
        is short: <strong>an address goes on a lead only when it is proved to reach a real inbox</strong>. Finding an
        address is not proving it, and a guessed address is never kept at all.
      </Lead>

      <H2>Found is not proved</H2>
      <P>
        Every address the people step finds is held first in <C>contact.email_unverified</C>: kept as the input a proof
        needs, never mailed, never shown. It moves to <C>contact.email</C> — the only address the dashboard shows and the
        only one anything is written to — when one of these says so:
      </P>
      <Table
        head={["Proof", "Recorded as", "What it means"]}
        rows={[
          ["An SMTP check", <C key="r">reacher · safe</C>, <>check-if-email-exists (<C key="t">tools/reacher/</C>) opened a conversation with the mailbox server, which accepted the address on a domain that is not catch-all.</>],
          ["The vendor’s check", <C key="a">apify · safe</C>, "An Apify email search returned the address and its own SMTP check said valid, deliverable and not catch-all."],
          ["Delivery", <C key="d">delivered · safe</C>, "A mail to it went out more than a day ago and never bounced."],
        ]}
      />
      <P>
        Anything else is not proof. An address on someone&apos;s own profile is <em>published</em>, and stays unproved until a
        check above passes. <C>risky</C> (a catch-all domain, a full inbox, a role account), <C>invalid</C> and{" "}
        <C>unknown</C> all keep the address off the lead. Whether an address is real is kept internally as{" "}
        <C>contact.email_status</C> (<C>real</C> or <C>fake:&lt;why&gt;</C>); the dashboard shows only real addresses and no
        &ldquo;fake&rdquo; label.
      </P>

      <H2>Enforced at the database</H2>
      <P>
        The rule does not depend on every writer remembering it. A hook runs before every write to the database —{" "}
        <C>db._only_verified_addresses</C> — and checks every new or changed contact:
      </P>
      <Steps>
        <Step title="Was it built rather than found?">
          If the address&apos;s provenance says it was constructed — a pattern source, a <C>pattern-guessed</C> label, a note
          saying &ldquo;email by pattern&rdquo; — it is <strong>refused outright</strong>: not kept on <C>email</C>, not even on{" "}
          <C>email_unverified</C>. The contact is marked <C>rejected:guessed</C> and the refusal is written to{" "}
          <C>extraction_reject</C>, so it is visible, never silent.
        </Step>
        <Step title="Is there a safe verdict?">
          A found address with a <C>safe</C> row in <C>email_check</C> goes on the lead, with the proof&apos;s source beside
          it. Without one it is moved to <C>email_unverified</C> and the contact is marked <C>fake:not-proved</C> (or the
          verdict it got) until a proof arrives.
        </Step>
      </Steps>
      <Callout kind="rule" title="Pattern filling is switched off">
        Building <C>{"{first}.{last}@company"}</C> from a company&apos;s address format used to be the fallback when nothing real
        was held. It is off — in the address step, the contact waterfall and the public-LinkedIn path — and stays off. A
        refused address is not a missing feature; it is the rule working.
      </Callout>

      <H2>Why the rule is this strict</H2>
      <Defs
        items={[
          ["19 Oct 2026", "A company’s confirmed {first}@ format produced four bounces — one of the examples that had confirmed it belonged to someone who had left — and Gmail refused all further mail for a day."],
          ["23 Oct 2026", "At the same company, 23 of 24 addresses written to had been guessed; three bounced and nine were refused as spam."],
          ["30 Oct 2026", "Guessed addresses were mailed again, bounced, and Gmail blocked the inbox. From that day only a proved address goes on a lead."],
          ["2 Nov 2026", "A guessed address is refused at the database hook — not even kept as a hint, because a hint nobody can prove is a bounce waiting to happen."],
        ]}
      />

      <H2>Where proofs come from in practice</H2>
      <P>
        Outbound port 25 is blocked on the operator&apos;s connection, so a local SMTP check cannot hold a conversation with a
        mail server and comes back <C>unknown</C>. Measured on 3 Nov 2026: of 246 proved addresses, 237 were proved by the
        vendor&apos;s check, 8 by delivery and 1 by a local probe. So in practice proofs come from{" "}
        <strong>delivery records and the vendor check</strong>; a long local verification run proves almost nothing while
        looking like progress. A relay or small server with port 25 open would let the local check work as designed. No
        separate paid verification service is used — the cost was ruled out.
      </P>

      <H2>At send time</H2>
      <P>
        A draft is addressed from <C>contact.email</C>, which only ever holds a proved address; the review rules reject
        any draft whose address is a pattern guess all the same. An address that bounces is moved to{" "}
        <C>dead_email</C> and never tried again. See <A href="/docs/outreach">Drafting &amp; the review gate</A> and{" "}
        <A href="/docs/sending">Sending</A>.
      </P>
    </Doc>
  );
}
