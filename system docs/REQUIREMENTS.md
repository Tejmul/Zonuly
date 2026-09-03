# ZoNuLy — Requirements & Completion Audit

> **Audited 2026-09-03 against the actual code and database**, 1:1 with
> [`Motive /MOTIV.md`](Motive%20/MOTIV.md). Every status below is backed by a file that exists
> or a table that was queried; nothing is claimed from the plan alone. Method: MOTIV §4's twelve
> steps and §6's nine rules are the requirements; FINAL-PLAN-V3 §14 is the build order that
> closes the gaps.
>
> Statuses: **DONE** (code exists and has run against real data) · **UNPROVEN** (code exists,
> never exercised end-to-end) · **PARTIAL** (some of the requirement) · **MISSING** (no code).
> Regenerate this audit rather than trusting it after the next build step lands.

---

## 0. The verdict

| Measure | Value |
|---|---|
| **Built, by MOTIV's twelve steps (weighted equally)** | **≈ 51%** |
| **Proven working end-to-end** | **≈ 35%** — nothing past DRAFT has ever run: zero emails sent (no Gmail OAuth credentials exist), so SEND → REPLIES → FOLLOW-UP → SCHEDULE → LEARN is theory |
| Rules of §6 honoured in code | 6 of 9 (fabrication is prompt-level, cross-candidate rule has no candidate model, permission system not built) |
| The "done" experience of MOTIV §7 | ≈ 40% — the morning half (jobs found + graded, drafts written, queue to review) works; the afternoon half (send, replies, calendar) does not exist or is untested |

**The single biggest blocker is 30 minutes of setup, not code:** a Google Cloud OAuth client so
one real email can be sent, replied to and classified (v3 step 3). The second is v3 step 0, the
permission gate, which every spend- or send-touching step is supposed to sit behind.

### Scoreboard — MOTIV §4, step by step

| # | MOTIV step | % | Status | Evidence for what exists | What is missing |
|---|---|---|---|---|---|
| 1 | COMPANIES | 70 | PARTIAL | 7 scrapers (`jobhunter/scrapers/`), 94 live-verified boards in `companies.yaml`, 263 companies / 1,100 jobs in the DB | Funding-round RSS feeds; **US/UK/DE region targeting** (`search.locations_ok` is still an India+remote allowlist — the thesis's geography is not encoded); staging → audit → promote gate |
| 2 | JOBS + GRADE | 75 | PARTIAL | Model-free lexical fit (`jobhunter/fit.py`, IDF-weighted, measured over the 1,100 postings); `judge`-alias rubric with 4 dimensions + hard-rule caps (`matcher.py:162`); 102 scored, 26 high-match | The `location_feasibility` 5th dimension (D14); the `cheap` triage stage between fit and judge; per-candidate scores (`job_score` table) |
| 3 | PEOPLE | 55 | PARTIAL | GitHub commit mining — 25/25 verified emails (`contacts/github_miner.py`); site scraper, pattern inference, Hunter free tier, MX check, waterfall in `contacts/__init__.py` | The 10–30-per-company promise for companies **without** public code: Apify no-cookies employee harvest (D12) is unbuilt, so coverage is bimodal (100% for OSS companies, ~0% otherwise) |
| 4 | ROLES | 10 | MISSING | Only an `is_recruiter` keyword hint on insert | The classifier itself — `role_class` (SDE1/2/3 · staff · EM · founder · recruiter), `seniority`, and referral-ability ordering of the queue |
| 5 | RESEARCH | 60 | PARTIAL | Per-contact hook researcher on the `judge` alias (`outreach/researcher.py`); **new**: the Agent-Reach research layer — Exa/Jina/GitHub/Reddit/YouTube with `evidence_quote` on every extracted field (`jobhunter/research/`) | The research layer is deliberately standalone (no DB writes) — not yet wired into contact research; the `<INGESTED_UNTRUSTED_CONTENT>` trust boundary (grep finds none) |
| 6 | DRAFT | 65 | PARTIAL | Drafter on the `writer` alias (`drafter.py:199`), dup-guard, em-dash strip, signature, 90–130 words | Claims-with-evidence-spans, the faithfulness + publish gates (fabrication is still only a prompt), humanisation lint, `candidate_id` |
| 7 | REVIEW | 60 | PARTIAL | Review queue in the dashboard; approve/edit/reject via API (`api.py`), approve is the only path to `status='approved'` | Reviewer/approval-assistant flags on drafts; Telegram cards; 7-day draft expiry |
| 8 | SEND | 50 | **UNPROVEN** | `sender.py`: cap by count, send window, stagger, refuses non-approved | **No Gmail OAuth credentials exist (`secrets/` is empty) — zero emails have ever been sent**; the transactional send ledger (GAP-1); the guessed-address cap of 5 |
| 9 | REPLIES | 70 | UNPROVEN | Thread polling + conservative classifier on `cheap` (`tracker.py:102`) | Never run against real mail (blocked by step 8) |
| 10 | FOLLOW-UP | 80 | UNPROVEN | One-follow-up guard set at queue time (`drafter.py`), 5-day cutoff | Same — never exercised |
| 11 | SCHEDULE | 0 | MISSING | — | Everything: schedule-extractor agent, `event` table, Google Calendar sync (`syncToken`/410 flow), conflict → reschedule draft, notifications |
| 12 | LEARN | 20 | PARTIAL | `tracker.funnel()` counts; the knowledge graph records decisions and state | `outcomes` table, reply-rate breakdowns by role/stage/framing, calibration of the score |

### Rules of MOTIV §6, in code

| Rule | Status | Evidence / gap |
|---|---|---|
| Nothing sends without a human | **DONE** | `sender.send_email` refuses anything not `approved`; no scheduled job calls send |
| Nothing is invented | PARTIAL | Prompt-level only; research layer's `evidence_quote` is the right shape but drafts carry no spans and no gate |
| No logged-in LinkedIn automation | **DONE** | No LinkedIn code path exists at all |
| 25/day, irreversibly spent | PARTIAL | A count since midnight, not a ledger row locked in the send transaction (GAP-1) |
| One follow-up, never two | **DONE** (unproven) | `followup_sent` set at queue time |
| Never both of us to one person in a month | **MISSING** | There is no `candidate` model at all — the pipeline is single-user today |
| Cheap, with caps | **DONE** | `openrouter.py`: `free_only: true` (a paid model raises `NotFree`), pre-flight budget gate ₹100/day · ₹2,500/month, a cost row per call, `models costs` CLI |
| No local model, ever | **DONE** | Ollama fully removed — `grep -ri ollama jobhunter scripts config.yaml` returns nothing; `llm.py` is an adapter over `openrouter.py` |
| Nothing without permission (tiers, grants, requests) | **MISSING** | Config caps exist; the permission gate itself — `permissions.yaml`, tiers, `approval_request`, `action_log`, pending/grant/deny — is v3 step 0, unbuilt |

---

## 1. Functional requirements (traceable to v3 §14 steps)

`[D]` done · `[U]` unproven · `[P]` partial · `[M]` missing. **AC** = acceptance criterion.

### FR-1 Companies (v3 step 7)
- **FR-1.1 [D]** Scrape Greenhouse/Lever/Ashby/HN/RemoteOK/WWR/YC into a deduped `job` table. *AC: a full run inserts >0 from ≥5 sources; same role on ATS + aggregator is one row.*
- **FR-1.2 [M]** Region targeting: `search.regions` with per-region floors (US/UK/DE any well-paid; India ≥ ₹30 LPA) replacing the flat location allowlist. *AC: a ₹20 LPA Bengaluru on-site role is filtered; a Berlin role is kept.*
- **FR-1.3 [M]** Funding-round RSS → extracted names → ATS probe → **staging**, promoted only through an audit. *AC: `discover` never writes `companies.yaml` directly; `promote --apply` refuses on a failing audit.*
- **FR-1.4 [M]** Source freshness alarm: a source at 0 twice running is reported as a failure. *AC: appears in the morning report / doctor.*

### FR-2 Grading (v3 step 2)
- **FR-2.1 [D]** Free lexical-fit gate over every posting (`fit.py`); explainable via `fit-explain <job-id>`.
- **FR-2.2 [M]** `cheap` triage between fit and judge: worth-scoring / region / remote / sponsorship-stated. *AC: judge volume drops ≥ 50% at equal high-match recall on the fixture set.*
- **FR-2.3 [D]** `judge` rubric: score 0–100, 4 dimensions, reasons, gaps, hard-rule caps.
- **FR-2.4 [M]** 5th dimension `location_feasibility` + hard cap 45 for US on-site with no sponsorship statement. *AC: rubric output includes the dimension citing the JD sentence.*
- **FR-2.5 [M]** Low grade still sendable: queue ordered by grade, ≥ 40 auto-draftable, < 40 by hand. *AC: a 35-grade job can be drafted only via explicit contact/job id.*

### FR-3 People (v3 step 8)
- **FR-3.1 [D]** GitHub commit mining → verified emails; site scrape; pattern inference (MX-checked, labelled); Hunter for patterns only; three confidence tiers never defaulted.
- **FR-3.2 [M]** Apify no-cookies employee harvest for graded companies with no GitHub org: pinned actor (`~` id), 30-day cache keyed (actor, version, input-hash), pre-flight budget ≤ $0.50/day, `?maxCharge`, shape-mismatch non-retryable. *AC: first company ≤ $0.20; identical second call is a cache hit; over-cap creates an approval request.*
- **FR-3.3 [M]** Cap 30 people per company (anti-contact-list rule).

### FR-4 Roles (v3 step 8)
- **FR-4.1 [M]** `contact.role_class` + `seniority`: keyword rules first, `cheap` residue, `unknown` kept never dropped. *AC: the 25 existing contacts classified and spot-checked.*
- **FR-4.2 [M]** Referral-ability ordering: engineer/EM verified-email first, founders next, recruiters last.

### FR-5 Research (v3 steps 5, 8)
- **FR-5.1 [D]** Per-contact hook with "null rather than invented" (`researcher.py`, `judge` alias).
- **FR-5.2 [D]** Standalone research layer: `research web|read|github|company|startups`, ordered backends with health-checked fallbacks, `evidence_quote` on every extracted field, `/api/research/*`.
- **FR-5.3 [M]** Wire FR-5.2 into contact/company research so its records become `artifact` rows the drafter can cite.
- **FR-5.4 [M]** Trust boundary: every fetched text enters prompts wrapped as untrusted content.

### FR-6 Drafting (v3 step 5)
- **FR-6.1 [D]** `writer`-alias drafts, one ask, 90–130 words, dup-guard per contact.
- **FR-6.2 [M]** Claims carry evidence spans; faithfulness gate (span exists → span supports) strips, never rewrites; publish gate blocks any proper noun/number without a source. *AC: a draft naming a repo the researcher never saw is stripped to generic and labelled.*
- **FR-6.3 [M]** Reviewer flags (`review_flags`) on every draft: fabrication risk, AI-tell vocabulary, length, one-ask. Human still approves.

### FR-7 Review (v3 steps 5, 6)
- **FR-7.1 [D]** Dashboard queue; approve / edit+approve / reject; approve is the only path to `approved`.
- **FR-7.2 [M]** Telegram cards with atomic `pending→processing` claim (double-tap safe); Edit-by-reply; decisions recorded by the API only.
- **FR-7.3 [M]** Draft expiry at 7 days.

### FR-8 Send (v3 steps 3, 4)
- **FR-8.1 [U]** Gmail API send, cap, 10:00–19:00 window, 45–210 s stagger, plain text. **Blocked on OAuth credentials — never run.** *AC: one email to a second inbox, replied, classified, threaded.*
- **FR-8.2 [M]** `send_budget(candidate, date, cap, used, guessed_used)` locked and decremented **inside** the send transaction; guessed cap 5.

### FR-9/10 Replies & follow-up (v3 step 3 proves them)
- **FR-9.1 [U]** Poll threads, classify conservatively (`neutral` on doubt), strip quoted text.
- **FR-10.1 [U]** Exactly one follow-up after 5 silent days, queued through review.

### FR-11 Schedule (v3 step 10) — all MISSING
- **FR-11.1 [M]** Schedule-extractor: positive reply → `{intent, proposed_times, tz, link, deadline, needs_action}` (both timezones; today's date injected).
- **FR-11.2 [M]** `event` table; Google Calendar via the same OAuth (`syncToken`, 410 → ±7-day resync); notify once via `notified_at`.
- **FR-11.3 [M]** Conflict → reschedule draft into the review queue; machine never confirms to the other party.

### FR-12 Learn (v3 step 12)
- **FR-12.1 [P]** Funnel counts exist; **[M]** `outcomes` table, breakdowns by role/stage/framing, monthly calibration *proposal*.

### FR-13 Two candidates (v3 step 9) — all MISSING
- **FR-13.1 [M]** `candidate` table; per-candidate profile, Gmail, caps, scores (`job_score`), drafts.
- **FR-13.2 [M]** Cross-candidate cooldown: one of us per contact per 30 days, one per company per role — enforced in the drafter.

---

## 2. Platform / non-functional requirements

| ID | Requirement | Status | Evidence / v3 step |
|---|---|---|---|
| NFR-1 | OpenRouter-only model layer: aliases, `free_only`, pre-flight ₹ caps, cost row per call, client-side RPM spacing, think-stripping, no client retries | **DONE** | `openrouter.py`, `config.yaml`, `models status/costs/check` |
| NFR-2 | No local model; no Ollama residue | **DONE** | grep-clean |
| NFR-3 | Secrets only in the environment / gitignored `.env`; `jobhunter.secret()` the single entry | **DONE** | `.env.example`, `__init__.py` |
| NFR-4 | Permission gate: tiers 0–4, `permissions.yaml` grants, `approval_request`, `action_log`, pending/grant/deny CLI + dashboard tab | **MISSING** | v3 step 0 — build first |
| NFR-5 | Durable run ledger (`agent_run` with phases, tokens, ₹, `parent_run_id`) | **MISSING** (model-call ledger exists; per-run ledger does not) | v3 step 0 |
| NFR-6 | `budgets.py` — every constant in one file, pinned by tests; no `AUTO_REJECT` symbol | **MISSING** (constants live in `config.yaml`, unpinned) | v3 step 0 |
| NFR-7 | Politeness core: one `fetch.py` for all free-source HTTP (robots, per-host rate, disk cache, UA, bounds) + CI grep | **PARTIAL** (research layer caches+rates its own calls; scrapers still use per-module clients) | v3 step 7 |
| NFR-8 | `tick.py` under launchd, `fcntl`-locked, bounded hourly slices; APScheduler removed | **MISSING** (APScheduler still schedules `daily_cycle`) | v3 step 7 |
| NFR-9 | Agent runtime: pydantic-ai, capability enforcement, budget triple, `EXECUTE_OUTREACH` held by nobody, depth ≤ 2 | **MISSING** (typed prompts exist; runtime enforcement does not) | v3 step 1b |
| NFR-10 | Kill-hardened stages (fsync per item, resume skips done ids) | **PARTIAL** (per-item commits in scoring/persist; paid research calls cached) | v3 step 7 |
| NFR-11 | Tests + CI guardrails (fixtures for salary/scoring/scrapers; the greps of v3 §11) | **MISSING** — there are zero tests | v3 step 12 |
| NFR-12 | Knowledge graph kept true (statuses, notes, BRIEF) | **DONE** (this audit re-syncs it) | `jobhunter/kg/` |

---

## 3. What should be added beyond code — the missing documents

1. **This file** — now exists; regenerate the §0 scoreboard after every approved step.
2. **RUNBOOK.md** — the daily 10 minutes as an operator's checklist (morning report → queue →
   approve → done), plus "a source went to 0", "budget cap hit", "OpenRouter 429s", "restore
   the DB". Write it when step 3 (first real send) lands, from what actually happened.
3. **TEST-PLAN.md** — the ~20 hand-labelled scoring fixtures, the salary fixture table, recorded
   scraper JSON; which greps run in CI. (v3 step 12; currently zero tests protect anything.)
4. **A `secrets/README.md`** — the exact Google Cloud console clicks for the Gmail/Calendar
   OAuth client, since it blocks the whole back half of the pipeline.
5. **DATA-POLICY.md** — one page: what is stored about real people (contacts, emails), retention,
   the delete path, and the DPDP note from FINAL-PLAN §13 — before the first real send, not after.

---

## 4. Priority order to close the gaps

Exactly FINAL-PLAN-V3 §14, with the audit's emphasis:

1. **Step 3 first in spirit** — the Gmail OAuth client (setup, ≈30 min) so one real email
   proves SEND → REPLY → CLASSIFY. Everything after DRAFT is unproven until this.
2. **Step 0** — permission gate + run ledger (NFR-4/5/6): the frame every later step bolts into.
3. **Step 4** — transactional send ledger + guessed cap + expiry, before real volume.
4. **Step 5** — proposals + faithfulness/publish gates (FR-6.2/6.3): before volume, one invented
   claim is worse than fifty generic emails.
5. **Step 2 completion** — triage + feasibility dimension + region targeting (FR-1.2, 2.2, 2.4):
   the queue starts reflecting the actual US/UK/DE thesis.
6. Then steps 6–12 as numbered (Telegram, scraping engine, people+roles, candidates, calendar,
   warmth, tests).
