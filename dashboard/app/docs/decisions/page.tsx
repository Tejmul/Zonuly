import type { Metadata } from "next";
import { C, Callout, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Design decisions" };

const D: [string, string, string, string?][] = [
  ["D1", "Python, not TypeScript", "The pipeline is Python; the dashboard is Next.js talking to it over REST."],
  ["D2", "A small local model (qwen3:4b)", "Superseded.", "reversed by D18"],
  ["D3", "No hosted model — local wins", "Superseded.", "reversed by D18"],
  ["D4", "A deterministic orchestrator", "The conductor is plain code. No LLM supervisor decides what runs next."],
  ["D5", "GitHub commit emails, but skip the code-less", "The spike was bimodal: ~100% hit rate where a company has public code, 0% without. Code-less companies are skipped, not guessed."],
  ["D6", "Free tiers only", "Hunter’s 25 lookups a month are spent on learning address patterns, not single people."],
  ["D7", "No SMTP probing", "Addresses are checked by MX lookup only; RCPT probing is off."],
  ["D8", "Three confidence tiers, never collapsed", "verified / pattern-guessed / scraped travel with every address."],
  ["D9", "SQLite, one file", "No server database, no vector extension."],
  ["D10", "The knowledge graph lives in SQLite", "Nodes, edges and full-text search in the same file, analysed with NetworkX — not a graph database."],
  ["D11", "SQLite stays; CSV is an interface", "CSV is for export and import, never the store."],
  ["D12", "LinkedIn only through no-cookies channels", "Public guest pages and Apify’s no-cookies actors under a daily budget. Your own LinkedIn is used by hand only."],
  ["D13", "“Multi-agent” = narrow workers", "Small LLM workers under a deterministic conductor. None can send."],
  ["D14", "Regions and pay floors are config", "And the scorer knows visa feasibility: Germany, then the UK, then the US."],
  ["D15", "Two candidates, one pipeline", "Shared companies and people; separate resumes, drafts and inboxes; one of them per contact per 30 days."],
  ["D16", "OpenRouter for writing tasks", "Superseded in part.", "revised by D18"],
  ["D17", "Nothing runs without permission", "The permission gate is build step zero."],
  ["D18", "No local model, ever", "OpenRouter is the only provider, through aliases (cheap, judge, writer), one cost row per call, caps, and a model-free lexical fit at the wide end of the funnel."],
  ["D19", "Agents can only propose", "Workers write fact or draft proposals; capabilities and budgets are enforced by the runtime."],
  ["D20", "Three scraping channels, one gate", "A polite keyless core, Apify no-cookies, and research search — behind one staging area and one audit, driven by the launchd tick."],
  ["D21", "A second review surface", "Telegram for approvals, with atomic pending → processing claims."],
  ["D22", "Underrated companies only", "Hyped names are rejected by name. If everybody knows them, a fresher’s ask is lost in the pile."],
  ["D23", "₹50k/month stipend is a hard bar", "Because the stipend predicts the full-time offer."],
  ["D24", "Pay tiers", "tier1 ≥ ₹30 LPA, tier2 ₹24–30 LPA, reject below — rejected companies stay in the database."],
  ["D25", "No stated pay is not a rejection", "Engineers on staff plus verified hiring make a company a prospect."],
  ["D26", "No alumni targeting", "The people layer targets roles, not colleges."],
];

export default function Decisions() {
  return (
    <Doc slug="decisions">
      <Lead>
        Decisions are numbered so they can be cited, and settled so they aren&apos;t re-argued every session. Each lives
        in the knowledge graph as <C>decision:dN</C>, linked to the features it shaped.
      </Lead>
      <P>
        A superseded decision is kept, not deleted: the reason it was reversed is part of the record. The biggest reversal
        is the model strategy — the project began on a local model and moved entirely to hosted models (D18) after local
        inference made the 8 GB laptop unusable.
      </P>

      <H2>The register</H2>
      <Table
        head={["#", "Decision", "In one line"]}
        mono={[0]}
        rows={D.map(([id, title, line, status]) => [
          id,
          status ? <span key={id} className="text-ink-muted line-through decoration-ink-3">{title}</span> : title,
          status ? <span key={`${id}s`}>{line} <span className="chip">{status}</span></span> : line,
        ])}
      />

      <H2>Reading a decision in the graph</H2>
      <P>Every decision links to what it constrains. To see one with its neighbours:</P>
      <pre className="my-4 overflow-x-auto rounded-xl border border-line bg-surface px-4 py-3 font-mono text-[12.5px]">python scripts/run.py kg show decision:d18</pre>
      <Callout kind="note" title="Where they come from">
        The register is generated from <C>knowledge/context.yaml</C> into <C>knowledge/BRIEF.md</C>. The plan that
        produced most of them is <C>system docs/FINAL-PLAN-V3.md</C>.
      </Callout>
    </Doc>
  );
}
