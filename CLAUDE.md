# ZoNuLy (JobHunter)

Local-first pipeline that finds engineering roles, scores them honestly against one resume,
finds real humans to ask for a referral, drafts the ask, and **stops at a human approval gate**.
Python 3.12 + SQLite + FastAPI in `jobhunter/`, Next.js dashboard in `dashboard/` (its own git repo).

## Start here — the knowledge graph

**Read [system docs/Motive /MOTIV.md](system%20docs/Motive%20/MOTIV.md) first — the problem statement, motivation, targets and rules, in the users' own words. Then [knowledge/BRIEF.md](knowledge/BRIEF.md).** BRIEF is generated
from the knowledge graph and carries the problem statement, live pipeline counts, the settled
decisions (D1–D10), what exists per stage, the gaps in build order, failure modes, open questions,
and a dated session log. It replaces re-reading the six architecture documents. The graph itself
is documented in [system docs/KNOWLEDGE-GRAPH.md](system%20docs/KNOWLEDGE-GRAPH.md); the index of
every design document is [system docs/README.md](system%20docs/README.md).

The graph lives in `jobhunter.db` (tables `kg_node`, `kg_edge`, `kg_fts`) with two layers:
*data* (mirrors of every table, synced after each pipeline cycle) and *context* (seeded from
[knowledge/context.yaml](knowledge/context.yaml), grown by session notes).

```
python scripts/run.py kg build                       # reload context.yaml + sync data + BRIEF.md + viewer
python scripts/run.py kg search "warmth"             # full-text search, both layers
python scripts/run.py kg show feature:warmth-tiers   # a node and everything it links to
python scripts/run.py kg path contact:3 constraint:send-cap-25
python scripts/run.py kg compose "<problem>"         # best feature per stage across all drafts
python scripts/run.py kg hubs                        # most load-bearing nodes (NetworkX centrality)
python scripts/run.py kg note "what changed" --about gap:1-send-ledger
```

Same operations over HTTP at `/api/kg/*` when the API is running. Open `knowledge/graph.html`
for the visual.

## Working rules

- **When you finish a piece of work, record it**: `kg note "..." --about <ids>` — that is how
  the next session knows what happened. If a decision changes or a feature moves from gap to
  built, edit `knowledge/context.yaml` (the `status:` field) and run `kg build`.
- All design documents live in `system docs/`; **`system docs/FINAL-PLAN-V3.md` is the plan** — built
  from gtm-arch.md, zon-arch.md and CHOKEPOINTS.md; it supersedes FINAL-PLAN-V2 and, on model strategy
  and orchestration, FINAL-PLAN.md. The graph encodes the decisions as `decision:d1`…`decision:d21`.
- **No Ollama, no local model.** It has wrecked the machine. Models are OpenRouter aliases behind a ledger.
- **Nothing is built without a numbered approval** (FINAL-PLAN-V3 §14).
- Never cut: the review gate, the send cap, the confidence labels (`guarantee:*` nodes).
- Layering: `db/llm/normalize` ← `scrapers/contacts/outreach` ← `pipeline/matcher/kg` ← `api` ← `dashboard`.
  Nothing imports upward.
- The machine has 8 GB RAM and no model runtime. Until step 1 of v3 lands, `llm.py` still contains the old
  Ollama client — do not run stages that call it.

## Everyday commands

```
python scripts/run.py doctor          # check Ollama, models, profile, Gmail, tokens
python scripts/run.py scrape | score | find-contacts | draft | send | poll | daily
python scripts/run.py serve           # FastAPI on :8000 (+ scheduler)
cd dashboard && npm run dev           # dashboard on :3000
```

## Web research (Agent Reach backends)

`jobhunter/research/` is the data-acquisition layer: web search (Exa via mcporter), page reading
(Jina Reader), GitHub, Reddit and YouTube. [Agent Reach](https://github.com/Panniantong/Agent-Reach)
is an installer + doctor for those upstream tools, **not** a library — it is not vendored, and
nothing in `jobhunter` imports it. Setup and the full rationale:
[system docs/AGENT-REACH-INTEGRATION.md](system%20docs/AGENT-REACH-INTEGRATION.md).

```
python scripts/run.py research doctor                  # which channels are live + how to fix the rest
python scripts/run.py research web "<describe the ideal page>"
python scripts/run.py research company "Acme AI" --depth deep
python scripts/run.py research startups --topic AI --table
```

It returns records and nothing else: no scoring, no DB writes, no graph, no drafting. Extraction is
evidence-linked (`evidence_quote`) and never invented — a field with no source stays null.
Same operations at `/api/research/*`.
