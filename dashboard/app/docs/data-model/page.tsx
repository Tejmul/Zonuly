import type { Metadata } from "next";
import { C, Callout, Doc, H2, H3, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Data model" };

export default function DataModel() {
  return (
    <Doc slug="data-model">
      <Lead>
        Everything lives in one SQLite file, <C>jobhunter.db</C>, in WAL mode with a 30-second busy timeout. Rows are
        never deleted — they are hidden, merged or quarantined, so history is never lost.
      </Lead>

      <H2>How the tables relate</H2>
      <div className="my-6 overflow-x-auto rounded-xl border border-line bg-surface p-5 font-mono text-[12.5px] leading-[2] text-ink-2">
        <div><span className="font-semibold text-ink">company</span> ─┬─&lt; <span className="font-semibold text-ink">job</span>           roles it posts</div>
        <div>{"          "}├─&lt; <span className="font-semibold text-ink">contact</span>       people who work there</div>
        <div>{"          "}│      └─&lt; <span className="font-semibold text-ink">email</span>   asks written to them</div>
        <div>{"          "}│             ├─&lt; <span className="font-semibold text-ink">reply</span>   what came back</div>
        <div>{"          "}│             └─&lt; <span className="font-semibold text-ink">event</span>   a “yes” with a time</div>
        <div>{"          "}└── field_meta     evidence for every field</div>
      </div>

      <H2>company</H2>
      <P>The centre of everything. Its columns group into a few concerns:</P>
      <Table
        head={["Group", "Key fields"]}
        mono={[1]}
        rows={[
          ["Identity", "name (unique) · website · domain · github_org · ats · ats_slug · linkedin_url · city"],
          ["Targeting", "tier · tier_reason · overrated · priority_score · pay_power · pay_power_band · money_per_head_usd_k"],
          ["Funding", "funding_stage · funding_amount_usd_m · funding_announced · funding_investors · valuation_usd_m · team_size"],
          ["Hiring", "careers_url · hiring_status · hiring_claim(_by/_source/_at) · hiring_roles · hiring_checked_at"],
          ["Addresses", "email_pattern (only when confirmed) · email_pattern_evidence"],
          ["Lifecycle", "status (null = active | quarantined | merged | below_pay_bar) · merged_into · retry_state · do_not_contact"],
          ["Completeness", "complete · complete_missing · completeness · fill_state (why parts are missing) · filled_at"],
          ["Lists", "list_key · list_rank · list_meta (the ★ India hand-picked list)"],
          ["Evidence", "field_meta — JSON per field: value, evidence, source, channel, time, confidence, agreeing sources"],
        ]}
      />

      <H2>job</H2>
      <Table
        head={["Group", "Key fields"]}
        mono={[1]}
        rows={[
          ["Posting", "company_id · title · location · remote · url (unique) · fingerprint · source · description · posted_at"],
          ["Pay", "salary_min_lpa · salary_max_lpa · is_internship · stipend_inr_month · ppo_lpa"],
          ["Flags", "is_senior · remote_anywhere"],
          ["Matching", "embed_sim (the lexical gate score — the name is historical) · match_score · match_reasons · skill_gaps · status"],
          ["Selection", "selection_score · selection_band · selection_market · selection_why · needs_brief"],
        ]}
      />

      <H2>contact</H2>
      <Table
        head={["Group", "Key fields"]}
        mono={[1]}
        rows={[
          ["Identity", "company_id · name · role · email · github · linkedin · x_handle · handle"],
          ["Address", "confidence (verified | pattern-guessed | scraped) · email_kind · source"],
          ["Role", "role_class · seniority · referral_rank (1 = ask first) · role_evidence"],
          ["Employment", "affiliation (verified-current | likely-current | unconfirmed | generic) · affiliation_evidence · last_commit_at"],
          ["Suppression", "email_status (bounced | opted_out) · dead_email · do_not_contact"],
        ]}
      />

      <H2>email, reply, event</H2>
      <Table
        head={["Table", "What a row is", "Key fields"]}
        mono={[2]}
        rows={[
          ["email", "One ask (or follow-up) to one person", "status (draft | incomplete | approved | sending | sent | replied | rejected | failed) · subject · body · review_flags · evidence · address_confidence · quality · expires_at · gmail_thread_id"],
          ["reply", "One inbound message on a thread", "email_id · from_addr · body · sentiment · sentiment_reason"],
          ["event", "A call, assessment or interview from a reply", "starts_at · link · deadline · quote · status · calendar_event_id"],
        ]}
      />

      <H2>Ledgers and support tables</H2>
      <Table
        head={["Table", "Purpose"]}
        mono={[0]}
        rows={[
          ["sendledger", "One row per candidate per day: cap, used, guessed cap, guessed used. Reserved atomically before a mail leaves."],
          ["llm_call", "Every model call: alias, model, purpose, tokens, cost in USD and INR, latency, success."],
          ["extraction_write / extraction_reject", "Every value guardrails accepted (with provenance) or refused (with the reason)."],
          ["channel_hour", "Success and failure per channel per hour — feeds the Scraping health page."],
          ["research_cache", "Search and page-read results, 24-hour TTL."],
          ["email_proposal · email_pattern", "How an email would be written (never sent) · approved templates."],
          ["candidate", "Per-person identity, mailbox and cap — two candidates, one pipeline."],
          ["setting", "Runtime state: last tick per kind, cursors, referral claims, the pause flag, audit history."],
          ["kg_node · kg_edge · kg_fts", "The knowledge graph and its full-text index."],
        ]}
      />
      <H3>Schema changes</H3>
      <P>
        There are no migration files. When a model declares a column the file lacks, <C>ensure_columns()</C> adds it with{" "}
        <C>ALTER TABLE … ADD COLUMN</C> at startup — so pulling new code is enough.
      </P>
      <Callout kind="warn" title="Never let git touch the live database">
        <C>jobhunter.db</C> is git-ignored for a reason: an autostash once wiped running writes. Back it up by copying to{" "}
        <C>data/backups/</C> (also ignored), never by committing it.
      </Callout>
    </Doc>
  );
}
