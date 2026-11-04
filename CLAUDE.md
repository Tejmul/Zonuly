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
- Never cut: the review gate, the send cap, the confidence labels (`guarantee:*` nodes). Since
  2026-10-26 the review gate is run by rules, not clicks (`jobhunter/outreach/autoreview.py`, on Tejmul's
  instruction): every cold draft is approved or rejected with its reason in `logs/autoreview.log`. Each
  session, read that log's rejections and fix the stage that produced them — that is the loop.
- **Never guess an email address. Ever.** (2026-10-30: guessed addresses were mailed, bounced, and
  Gmail blocked the inbox.) An address goes on a lead only when check-if-email-exists
  (`tools/reacher/`, `jobhunter/contacts/reacher.py`) proves it `safe`, it was delivered to, or the
  person published it AND it was then proved. `db._only_verified_addresses` enforces this on every
  write; anything unproved is kept in `contact.email_unverified`, never mailed, never shown. Pattern
  filling (`addresses.py`, `contacts/__init__` step 5, `linkedin_public`) is switched off — do not
  switch it back on. Real/fake is internal (`contact.email_status` real | fake:<why>); the dashboard
  shows only real addresses and no "fake" label. No Apify for verification (cost). An Apify email
  search address counts as proved only when the vendor's SMTP check says valid + deliverable + not
  catch-all (`best_email` "verified"); one that is only on the person's profile is "published" and
  stays unproved (`linkedin_public._prove` writes the proof before the contact is saved).
- **Focus: Bengaluru + Hyderabad only** (2026-10-31, `focus.cities` in config.yaml). The people step,
  Apify and every fill queue skip or sink everything else; the YC directory is not admitted. Remote
  waits until Bengaluru + Hyderabad are complete (planned end of November) — then empty the list.
- **When Tejmul asks for a CSV** (his mail-merge sheet): `python scripts/export_referrals_csv.py`, in his
  format `company,email,subject,message,name,role,address`. Verified addresses only — a pure list of
  people — unless he asks for unverified (`--with-unproved`, labelled). The verified-sprint batch
  (2026-10-31, `scripts/verified_sprint.py`, Bengaluru + Hyderabad best bets first, held for him) comes
  first; then the other verified Bengaluru / Hyderabad leads by best bets and pay power. Never an address
  already mailed: the Email table or any CSV in `data/merge_sent/` (what he sent from his own sheet —
  save each CSV he sends there). Test rows to tejmulmovin9@gmail.com at the sheet rows he names
  (`--test-at`). Sending stays off until he says the sending part starts.
- **A CSV carries only companies with PROVED ₹20 LPA+ pay** (2026-11-02, after he cold-mailed OrbitAID
  Aerospace and CynLr and found they pay far less: *"i was sending cold dms now i see the companies are
  not even paying that much"*). The gate is `jobhunter/india.pay_proved(company)` — the pay-floor verdict
  "ok", or `company.ppo_lpa >= 20`. **No pay figure at all is not proof.** Of 2,064 Bengaluru/Hyderabad
  companies only 209 clear it, and that is the point. `--any-pay` overrides, and he must ask for it.
  The companies themselves are **not hidden**: *"do not make it hidden just keep it just dont give me
  when i ask for csv"*, *"you can show the companies in the UI"* — they stay active, scraped, and on the
  ★ India page. Only the mailing list is gated. Proving pay for a held company (a posting with a stated
  band, a levels.fyi figure) is the way to bring it back, and that is the work.
- **A sheet he has already DM'd is never offered again** (2026-11-02: *"i am also automating stuff so i
  will give csvs of contacts which dms are already sent to"*). Feed it in:
  `python scripts/export_referrals_csv.py --sent <file.csv>` — repeatable, reads any column layout,
  stamps every matching lead `contact.mailed_by_tejmul_at` (the lock that survives a re-scrape) and
  files the sheet in `data/merge_sent/` (the lock for addresses with no lead row). Run it the moment he
  sends a sheet, before writing him a new CSV.
- **Sent and available are segregated, and `--sent-list` shows it.** SENT = an address written to, by
  the pipeline (an `Email` row) or by him (`contact.mailed_by_tejmul_at`); it leaves every future CSV
  forever. AVAILABLE = a proved-real address nobody has written to. `--sent-list` prints both counts and
  writes the register `data/merge_sent/_sent_register.csv` (address, name, company, sent by, when).
  **`data/merge_sent/` means SENT, nothing else** — a CSV that was generated but never mailed belongs in
  `data/csv_generated/`. 2026-11-02: he had sent only 177 (63 by the system, 114 from his sheet of
  2026-11-02); two generated sheets had been mis-filed as sent and 2,399 leads were wrongly marked, then
  cleared. **Never assume a sheet was sent because he pasted it — ask, or check the Email table.**
- **The hand-off is numbered** (2026-11-01, `jobhunter/outreach/handoff.py`): `--batch` writes the next
  `data/csv/CSV<n>.csv` — proved-real addresses only (`contact.email_status == "real"`), never a lead
  from an earlier batch — and records it (`csv_batch`, `csv_batch_contact`). When he has mailed it,
  `--done CSV<n>` stamps the batch and marks its people `contact.mailed_by_tejmul_at`: mailed forever,
  offered in no later CSV. The leads page shows the batch and that state, read-only.
- **Software tech only, India** (2026-11-02: *"from sde ai devops and tech roles not robotic
  aerospace or any other companies dont go out of the domain of software tech"*). `targeting.india.
  software_only` + `india.hardware_domain()` refuse a company whose words read aerospace, robotics,
  optics, chips, EV, battery, bio or defence, unless a strong software signal (SaaS, developer tools,
  LLM platform) outweighs it. OrbitAID Aerospace and CynLr are the two that got through before it.
- **Company discovery is `jobhunter/india_boards.py`** (2026-11-02). Every older India channel had
  walked to the end of its cursor — 281 companies seen, 0 new. This one searches the public board
  hosts (`site:boards.greenhouse.io Bengaluru …`, 816 queries) and then reads the board's **own
  public API**, which proves the city (a posting located there), the domain (the titles) and often
  the pay (a stated band). `python -c "from jobhunter import india_boards as ib; ib.run()"`.
  The real company name comes from the board metadata, never the slug — a slug is an account handle,
  and slug names created five duplicates of companies we already had on the first pass.
- **NO GUESSED ADDRESS ENTERS THE DATABASE** (2026-11-02: *"i dont need guessed mails i only need
  verified mails okk very very strict guardrial … no fake emails entering my database"*).
  `db.looks_guessed()` + `db._no_guessed_addresses()` run in the `before_flush` hook: an address
  whose provenance says it was constructed (`source=pattern`, confidence `pattern-guessed`, a
  research note saying "email by pattern") is refused outright — not on `contact.email`, **not even
  on `email_unverified`** — the contact is marked `email_status="rejected:guessed"` and the refusal
  is written to `extraction_reject`. An address genuinely *found* (a commit, a profile, a page) is
  still kept in `email_unverified` for Reacher to prove; that is the input verification needs.
- **Communities are found publicly, never read from inside** (2026-11-03: *"there are hell lot of
  oppurtunities i see in slack channels and communities scrap them too"*). `jobhunter/communities.py`
  walks the public directories (The Hive Index) and fills the `community` table — platform, topic,
  members, job-channel flag, join link, and a score that prefers small and specific over large and
  famous. Messages **inside** a Slack workspace are private: reading them needs a joined account, and
  automating one breaks Slack's terms and the no-personal-accounts rule. The module finds the rooms;
  Tejmul joins them by hand. A channel with a **public** job feed is then scrapeable like any other
  source, in `jobhunter/scrapers/`, not here.
- **No DSA cap** (2026-11-02: *"remove the cap of dsa we are in need of companies now"*). A DSA-heavy
  interview loop no longer parks a company (`decide.dsa.park: false`) and no longer drops it from the
  mixer (`mixer.max_dsa: 1.0`). DSA still lowers the `loop` probability, so those companies rank lower —
  they are simply never taken off the board. Pay is the bar; DSA is not.
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

## Reverse engineering a source (REA)

[REA](https://github.com/morluto/rea) is registered as an MCP server for this directory only
(`claude mcp add --scope local rea -- npx -y rea-agents@6.5.0 mcp`). Use it to find the JSON
endpoint behind a careers page or a board's bundle before writing a scraper, to read a HAR offline,
or to inspect a *copy* of the database. It is a developer tool: nothing in `jobhunter` imports it,
and it never attaches to a browser signed in to a personal account. Setup, workflow and limits:
[system docs/REA-INTEGRATION.md](system%20docs/REA-INTEGRATION.md).

## Orchestrating agents (Paperclip)

[Paperclip](https://github.com/paperclipai/paperclip) runs the *developer* agents that work on this
repo — a Lead that reads `logs/autoreview.log` and files issues, a Builder that fixes one issue per run
in an isolated git worktree, a Reviewer that checks the rules before correctness. It is a standalone
app (Node + embedded Postgres, loopback :3100, all state in `~/.paperclip`); nothing in `jobhunter`
imports it, and it does **not** replace the deterministic pipeline conductor (D4 — the orchestrator of
the pipeline is code, not a model). Agents run without the permission bypass: edits inside their
worktree only, Bash limited to the allowlist in `.claude/settings.json`, never push, never merge,
never mail. Setup, roster and the rules they carry: [system docs/PAPERCLIP-INTEGRATION.md](system%20docs/PAPERCLIP-INTEGRATION.md).

```
tools/paperclip/paperclip.sh start | stop | status   # the server; UI at http://127.0.0.1:3100
tools/paperclip/paperclip.sh bootstrap               # company, agents, project, routine (idempotent)
tools/paperclip/paperclip.sh cli issue create -C <company> --title "..." --assignee-agent-id <builder>
```
