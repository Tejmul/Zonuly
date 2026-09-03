# ZoNuLy — system docs

Every design document for the project, in reading order. When two disagree,
**FINAL-PLAN-V3.md wins.**

| Read | File | What it is |
|---|---|---|
| 1st | [Motive /MOTIV.md](Motive%20/MOTIV.md) | **The problem statement and motivation, in the users' own words** — the pay arbitrage, the targets, the twelve-step process, the funnel numbers, the rules. |
| 2nd | [FINAL-PLAN-V3.md](FINAL-PLAN-V3.md) | **The plan.** Built from gtm-arch, zon-arch and CHOKEPOINTS: OpenRouter-only model layer, pydantic-ai agents that can only propose, faithfulness + publish gates, three scraping channels behind a staging gate, launchd ticks, Telegram review, calendar sync, permission system, 13-step build order. |
| 3rd | [FINAL-PLAN-V2.md](FINAL-PLAN-V2.md) | Superseded by v3. Kept for the restated-problem analysis (§0) and the tool/permission tables it introduced. |
| 4th | [FINAL-PLAN.md](FINAL-PLAN.md) | Superseded on model strategy and orchestration. Still the record of D1, D5–D9 and what the running code proved. |
| 5th | [REQUIREMENTS.md](REQUIREMENTS.md) | **The completion audit** — MOTIV 1:1 against the code: per-step percentages, FR/NFR status with evidence, the missing documents, priority order. Regenerated after every approved step. |
| 6th | [KNOWLEDGE-GRAPH.md](KNOWLEDGE-GRAPH.md) | The knowledge graph (D10) — how data and context are stored together, the CLI/API, `compose`, and how to keep it true. |
| ref | [IDEA.md](IDEA.md) | The original narrative brief (2026-08-01). MOTIV.md supersedes it on targets. |
| input | [CHOKEPOINTS.md](CHOKEPOINTS.md) | A keyless scraper run at the source's rate limit, 24/7. Source of the launchd tick, staging/promote gate, freshness alarm and the "bugs already paid for" table. |
| input | [gtm-arch.md](gtm-arch.md) | gtm-os — Apify no-cookies LinkedIn actors with costs, budget gates and caches; the untrusted-content wrapper; HITL. |
| input | [zon-arch.md](zon-arch.md) | The MVRX portal — calendar sync + meeting notifier, the Apify response cache. |
| ref | [PLAN.md](PLAN.md) | The original build plan the code was scaffolded from (2026-07-31). |
| draft | [JOBHUNTER-ARCHITECTURE.md](JOBHUNTER-ARCHITECTURE.md) | TypeScript/Drizzle draft; lessons copied from and fixed in the MVRX portal. Superseded, still the source of the send-ledger, fabrication-validator and tests-first ideas. |
| draft | [JOBHUNTER-TOOLS.md](JOBHUNTER-TOOLS.md) | Services and pricing; the email-finding waterfall; the **warmth ladder** (GAP-3). Superseded. |
| draft | [MULTI-AGENT-ORCHESTRATION.md](MULTI-AGENT-ORCHESTRATION.md) | Conductor / supervisors / workers borrowed from gtm-os. The hierarchy was dropped (D4); the ledger, evidence spans, no-auto-reject and draft-expiry ideas survived. Superseded. |
| draft | [GEMINI.md](GEMINI.md) | Sequential typed agents, tool-loop and schema guardrails, the clickable Provenance Panel. |

## Generated, not written

| File | |
|---|---|
| [../knowledge/BRIEF.md](../knowledge/BRIEF.md) | The context handoff. Rendered from the knowledge graph by `python scripts/run.py kg brief`. **Read this at the start of every session.** |
| [../knowledge/context.yaml](../knowledge/context.yaml) | The graph's context seed — every document above, decomposed into features, decisions, gaps, constraints. Edit this when the project's understanding changes. |
| `../knowledge/graph.html` | Standalone force-layout viewer of the whole graph. |

## How the documents relate

```
IDEA.md ──────────────────────────────────────────────────┐
PLAN.md ─────────► code ◄──── JOBHUNTER-ARCHITECTURE.md   │
                     │  ◄──── JOBHUNTER-TOOLS.md          │  all decomposed into
                     │  ◄──── MULTI-AGENT-ORCHESTRATION.md│  knowledge/context.yaml
                     │  ◄──── GEMINI.md                   │
                     ▼                                    │
               FINAL-PLAN.md  (reconciles, D1–D10) ───────┤
                     │                                    │
   system docs/Motive /MOTIV.md (restated problem)                  │
   CHOKEPOINTS.md · gtm-arch.md · zon-arch.md (inputs)    │
                     │                                    │
                     ▼                                    │
             FINAL-PLAN-V2.md (superseded) ───────────────┤
                     │                                    │
                     ▼                                    │
             FINAL-PLAN-V3.md (THE PLAN, D18–D21) ────────┤
                     │                                    │
                     ▼                                    ▼
             KNOWLEDGE-GRAPH.md          knowledge graph ──► BRIEF.md
```

In the graph these are `arch:idea`, `arch:plan`, `arch:architecture`, `arch:tools`,
`arch:orchestration`, `arch:gemini`, `arch:final`, `arch:chokepoints`, `arch:gtm`, `arch:zon`, `arch:plan-v2`, `arch:plan-v3`; `kg show arch:tools` lists every feature a
document proposed and what became of it.
