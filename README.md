# ZoNuLy

**A local-first job hunter that finds underrated companies, scores how likely you are to be
selected, finds a real person inside who can refer you, and drafts the ask — then stops and
waits for you to approve it.**

Applying through a careers page puts you in a pile of thousands. A referral from an engineer
who works there skips the pile. ZoNuLy automates everything up to that conversation: which
companies are worth your time, which roles you can actually get, who to ask, and what to say
— with every claim traceable to a source.

```
 discover ──► verify ──► score ──► find referrers ──► understand needs ──► draft ──► YOU APPROVE ──► send ──► track replies
 companies    guardrails  selection   (current staff)    (JD + what they      (evidence-   (review gate)   (capped,   (positive / neutral /
 & roles                  confidence                      are building)        bound)                      warmed-up)  negative, follow-ups)
```

---

## What it does

| Stage | What happens |
|---|---|
| **Discover** | Scrapes funded, *underrated* startups and their open roles: ATS boards (Greenhouse, Lever, Ashby, Keka, Darwinbox, Zoho Recruit, Workable …), company careers pages, funding news, YC, public LinkedIn job postings, India startup directories. |
| **Admission gate** | Every company passes one gate: real name (not a headline fragment), not a duplicate, not famous or overrated (big tech, unicorns, listed companies, valuation ≥ $5B or raised ≥ $1B), right location. |
| **Guardrails** | Every extracted field is validated before it is written — descriptions about *this* company, funding that is actually a raise, hiring verified on the company's own careers page. A refused value is logged with its reason, never silently dropped. |
| **Selection confidence** | Each role gets a 0–100 chance-of-selection score with a band and an itemised, sourced breakdown. Two weight profiles — **India** (Bengaluru / Hyderabad, fresher eligibility, ₹20 LPA pay floor) and **Remote** (remote-anywhere, IST overlap, international hiring) — and hard blockers (visa, 5+ years, senior titles). |
| **Referrers** | Finds people who work there *now*: public GitHub org members and recent committers, company team pages, conference talks, podcasts, blog bylines, public LinkedIn search results. A person counts only with evidence of current affiliation; shared inboxes and format examples (`first.last@`) are refused. |
| **Needs brief** | Reads the JD, the company's site, blog and changelog, what their latest round is for, and engineers' public posts — then maps each need to the exact resume line that covers it, or marks it uncovered. |
| **Drafting** | Writes a short, personalised referral ask from that evidence. The review gate flags any claim the evidence doesn't support and blocks claims about uncovered needs. |
| **Sending** | Only emails you approved. 25/day cap, 5/day for guessed addresses, a warm-up ramp, jittered send times, one ask per company, an opt-out line on every cold email. |
| **Tracking** | Polls Gmail for replies, bounces and opt-outs; classifies replies as positive / neutral / negative / closed; turns "how about Friday 4pm IST?" into a calendar event; drafts one threaded follow-up after 5 silent days. |

---

## Principles that are never traded away

- **A human approves every email.** Nothing sends itself.
- **Hard caps** on how many go out per day, and a warm-up for a fresh sending pattern.
- **Every claim carries its source.** No invented familiarity, no unsupported numbers.
- **None of the user's own accounts is used for scraping** (LinkedIn, X, GitHub, Reddit) — public guest pages and separate tokens only.
- **Nothing is deleted.** Junk is quarantined, duplicates merged, and both can be undone.

---

## Dashboard

A Next.js dashboard (black and white, light and dark) over the FastAPI backend:

- **Overview** — pipeline counts and what moved today
- **Companies** — only *complete* companies (site, description, hiring proof, a confirmed referrer with an email); the rest wait under *Being filled* with what each is missing
- **★ India** — a hand-picked list of Bengaluru and Hyderabad startups, plus a collapsed *Below the ₹20L bar* section
- **Atlas** — filters (field, location, round, funded within, must-haves) and a ranked, explainable network of why each company scored
- **Best bets** — the top roles by selection confidence, India and Remote tabs
- **Leads** — the referrers, with their affiliation evidence
- **Queue** — drafts waiting for review
- **Outreach** — the whole send pipeline, the launch checklist, and every thread with its reply sentiment
- **Health** — hourly audit: fill rates, rejects by reason, channel health, stall alerts

---

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLModel on SQLite (WAL), APScheduler, httpx
- **Frontend:** Next.js, React, Tailwind, GSAP
- **Models:** OpenRouter (free-model fallback chains, a spend ledger and daily caps). No local model runtime.
- **Research channels:** Jina Reader, DuckDuckGo and Startpage read through Jina, Exa (hosted MCP via mcporter), GitHub API, yt-dlp — installed with [Agent Reach](https://github.com/Panniantong/Agent-Reach)
- **Mail:** Gmail over SMTP / IMAP with an app password
- **Scheduling:** macOS launchd — a 10-minute tick, a paced send agent, an hourly audit
- **Knowledge graph:** every table mirrored into a graph with full-text search (NetworkX), plus a generated project brief

---

## Getting started

```bash
# backend
uv sync                                   # or: python -m venv .venv && pip install -e .
cp .env.example .env                      # add OPENROUTER_API_KEY, GMAIL_APP_PASSWORD, …
python scripts/run.py doctor              # check models, budget, Gmail, tokens
python scripts/run.py profile             # parse your resume (profile/resume.pdf) into profile.json
python scripts/run.py serve               # API on :8000

# dashboard
cd dashboard && npm install && npm run dev   # http://localhost:3000

# run it on a schedule (macOS)
python scripts/run.py tick install        # 10-minute tick, send agent, hourly audit
```

### Everyday commands

```bash
python scripts/run.py research doctor     # which research channels are live
python scripts/run.py harvest careers --list india-star
python scripts/run.py selection top       # best roles by selection confidence
python scripts/run.py outreach preflight  # launch checklist, red / amber / green with the fix
python scripts/run.py outreach proposal   # preview how emails would be written — sends nothing
python scripts/run.py outreach batch      # draft the week's asks into the review queue
python scripts/run.py audit run           # the hourly health report, now
python scripts/run.py kg search "warmth"  # search the project knowledge graph
```

### Configuration

Everything tunable lives in [`config.yaml`](config.yaml): targeting (famous-names list, funding
and valuation ceilings, India cities and the ₹20 LPA floor), selection weights per market, send
caps and the warm-up ramp, tick budgets, model aliases. Secrets live only in `.env`.

---

## Project layout

```
jobhunter/
  scrapers/      ATS boards, careers pages, funding feeds, India job postings
  contacts/      referrer discovery, affiliation evidence, email formats
  research/      the research channels (search, page reading, GitHub, YouTube)
  outreach/      drafting, the review gate, sending, warm-up, tracking, follow-ups
  admission.py   the one gate every company passes
  guardrails.py  one validator per field, called by every write path
  confidence.py  selection confidence (India / Remote)
  needs.py       needs briefs: what they want, mapped to the resume
  tick.py        the scheduled run: leads first, companies after
  audit.py       the hourly health report
  api.py         FastAPI
dashboard/       Next.js dashboard
scripts/         CLI (run.py), snapshot builder, smoke test, cleanup tools
knowledge/       knowledge graph context and the generated project brief
tests/           pytest
```

---

## Deploying a read-only demo

The public instance serves a snapshot, not the live database: `scripts/make_demo_db.py` strips
private tables (cached pages, model spend, inbound mail), stamps the snapshot with its commit
and schema, and refuses to write it if a list is incomplete. CI rebuilds the environment from
the lockfile and runs `scripts/smoke.py` against it on every push.

```bash
python scripts/make_demo_db.py            # -> demo.db.gz
python scripts/smoke.py --db demo.db      # or against a deployed URL
```

---

Built by **Tejmul Movin**.
