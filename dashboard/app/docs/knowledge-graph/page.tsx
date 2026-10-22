import type { Metadata } from "next";
import { A, C, Callout, Code, Defs, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Knowledge graph" };

export default function KnowledgeGraph() {
  return (
    <Doc slug="knowledge-graph">
      <Lead>
        A project built across many working sessions needs a memory. The knowledge graph holds both the live data and
        the reasoning behind it — decisions, constraints, gaps, failures and dated notes — so the next session starts
        from what is true, not from scratch.
      </Lead>

      <H2>Two layers, one store</H2>
      <Defs
        items={[
          ["Data layer", <>Mirrors of companies, roles, people, emails and replies, plus your profile and skills. Rebuilt by <C key="s">kg sync</C> after every cycle; never edited by hand.</>],
          ["Context layer", <>Seeded from <C key="c">knowledge/context.yaml</C>: the problem, decisions, constraints, guarantees, features, stages, modules, gaps, failure modes and open questions — plus dated notes from <C key="n">kg note</C>.</>],
        ]}
      />
      <P>
        Both live in <C>jobhunter.db</C> as <C>kg_node</C>, <C>kg_edge</C> and an FTS5 index <C>kg_fts</C>. There is no graph
        database (D10); NetworkX loads the graph when analysis needs it.
      </P>

      <H2>The shape of the data</H2>
      <div className="my-6 overflow-x-auto rounded-xl border border-line bg-surface p-5 font-mono text-[12.5px] leading-[2] text-ink-2">
        profile:me ─HIGH_MATCH→ job ─POSTED_BY→ company ←WORKS_AT─ contact ←SENT_TO─ email ←REPLIES_TO─ reply
      </div>
      <P>
        Plus <C>USES_ATS</C>, <C>FOUND_VIA</C>, <C>HAS_SKILL</C>, <C>FUNDED_BY</C>, <C>AFFILIATED_WITH</C>,{" "}
        <C>PREVIOUSLY_AT</C> and <C>IN_CITY</C>. Those power the warm paths on a company page (who you might reach through
        whom) and “similar companies” (shared investors and skills).
      </P>

      <H2>The brief</H2>
      <P>
        <C>knowledge/BRIEF.md</C> is generated from the graph: the problem, live pipeline counts, constraints and guarantees,
        settled decisions, what exists per stage, gaps in build order, failure modes, open questions, the module map and a
        dated session log. It is rewritten daily by the paid tick and on every <C>kg build</C>.
      </P>

      <H2>Everyday use</H2>
      <Code title="terminal">{`
python scripts/run.py kg build                        # reload context + sync data + BRIEF.md + viewer
python scripts/run.py kg search "warmth"              # full-text, both layers
python scripts/run.py kg show feature:warmth-tiers    # a node and its neighbours
python scripts/run.py kg path contact:3 constraint:send-cap-25
python scripts/run.py kg why "Skylo Technologies"     # write to them? to whom? why?
python scripts/run.py kg note "what changed" --about gap:1-send-ledger
`}</Code>
      <Table
        head={["Command", "Answers"]}
        mono={[0]}
        rows={[
          ["kg hubs", "The most load-bearing nodes (betweenness centrality)"],
          ["kg compose", "The best feature per stage across all architecture drafts"],
          ["kg shortlist", "Targets ranked by grade, hiring, skill overlap and reachable people"],
          ["kg expand", "Companies funded by the same investors, two hops out"],
          ["kg guard", "May we write to this person or company now? (cooldowns)"],
          ["kg audit", "Contradictions inside the graph"],
        ]}
      />
      <Callout kind="rule" title="The working rule">
        When a piece of work is finished, record it with <C>kg note … --about &lt;ids&gt;</C>. When a decision changes or a
        feature moves from gap to built, edit its <C>status</C> in <C>context.yaml</C> and run <C>kg build</C>.
      </Callout>
      <P>
        The dashboard&apos;s <A href="/docs/dashboard">Graph</A> page draws the same graph as a star map: distance is bet
        quality, a filled star is a complete company, a ring is one still filling.
      </P>
    </Doc>
  );
}
