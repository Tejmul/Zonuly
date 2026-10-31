# Paperclip integration — orchestrating the agents that work on ZoNuLy

**Status:** installed and bootstrapped 2026-10-31. Paperclip is a developer tool that runs
*beside* the repo; **nothing in `jobhunter/` imports it and nothing at runtime depends on it.**
Same stance as [REA](REA-INTEGRATION.md) and [Agent Reach](AGENT-REACH-INTEGRATION.md).

It does **not** replace the pipeline's conductor. D4 stands: the orchestrator of the *pipeline*
(scrape → score → contacts → draft → autoreview → hold) is deterministic code — `tick.py`, the
scheduler, the rules in `autoreview.py`. Paperclip orchestrates the *people-shaped work on the
repo*: reading the rejections, fixing the stage that produced them, reviewing the fix. That is the
loop CLAUDE.md asks every session to run; Paperclip runs it on a schedule with three Claude Code
agents instead of one chat session.

---

## 1. What Paperclip is

[Paperclip](https://github.com/paperclipai/paperclip) (npm `paperclipai`, MIT) is a Node server +
React UI that runs a *company* of AI agents: an org chart, issues, routines (cron), budgets, an
activity log, and adapters that start each agent as a process — here `claude_local`, i.e. the
`claude` CLI already installed and logged in on this machine. An agent is dormant until something
wakes it (an issue assigned, a comment, a routine firing, "Run now"); each wake is one run with a
turn cap, a timeout and a cost line.

Where it lives — **none of this is in the repo**:

| What | Where |
|---|---|
| config, deployment mode | `~/.paperclip/instances/default/config.json` |
| embedded PostgreSQL (port 54329) | `~/.paperclip/instances/default/db` |
| logs, storage, hourly DB backups | `~/.paperclip/instances/default/{logs,data/storage,data/backups}` |
| secrets master key, JWT secrets | `~/.paperclip/instances/default/{secrets,.env}` |
| each agent's instruction file | `~/.paperclip/instances/default/companies/<cid>/agents/<aid>/instructions/AGENTS.md` |
| UI + API | `http://127.0.0.1:3100` (loopback only, `local_trusted`, no login) |

Requirements, all present: Node 26 (needs ≥ 24.11), the `claude` CLI (2.1.x) signed in, Python 3.
No Docker, no Rust (the npm build ships a prebuilt runner), no API key — the agents use the
Claude subscription login already on the machine.

## 2. What is in the repo

```
tools/paperclip/
  paperclip.sh              start | stop | status | bootstrap | cli …   (pinned paperclipai@2026.1023.0)
  roster.json               company, experimental flags, project, seven agents, four routines — models and budgets live here
  bootstrap.py              creates whatever roster.json names that does not exist yet; writes data/paperclip.json
  agents/COMMON.md          the two problems (2026-11-01) + the hard rules every agent carries + how to talk to Paperclip
  agents/CEO.md             owns the motive; ≤3 measurable GOAL issues a week; the CEO brief for Tejmul
  agents/CTO.md             reads autoreview / tick / reacher logs; ≤4 TECH issues a day with acceptance tests
  agents/MANAGER.md         routes TECH/OPS issues to engineers, one in progress each; chases; gates to the Reviewer
  agents/ENGINEER-SCRAPE.md scrapers/, tick.py, sources — the stall problem
  agents/ENGINEER-LEADS.md  contacts/, Reacher, segregation, CSV/dashboard — proved addresses only
  agents/REVIEWER.md        rules, then honesty of the report, then correctness; APPROVE / CHANGES / REJECT
  agents/DEVOPS.md          launchctl, tick-free.log freshness, locks, proving rate; OPS issues; kickstart tick.free only
data/paperclip.json         this machine's ids (git-ignored)
```

`COMMON.md + <ROLE>.md` is uploaded as the agent's `AGENTS.md` at create time (Paperclip's
`instructionsBundle`), so the rules travel with the agent. Change a rule → edit the file →
`paperclip.sh cli agent terminate <id>` + `agent delete <id> --yes`, then `paperclip.sh bootstrap`.

## 3. The org (Tejmul's design, 2026-11-01)

```
Tejmul (approves, merges, sends)
└── CEO            ceo       claude-opus-5-5     read-only   Mon 08:00 IST       GOAL issues → CTO; CEO brief
    └── CTO        cto       claude-fable-5-1    read-only   07:30 IST daily     TECH issues → Manager; answers ?CTO
        ├── Manager            pm        claude-sonnet-5-5  read-only  08–22 IST every 2h   assigns, chases, gates
        │   ├── Scraper engineer  engineer  claude-fable-5-1  acceptEdits  on assignment  worktree, pytest, branch
        │   └── Leads engineer    engineer  claude-fable-5-1  acceptEdits  on assignment  worktree, pytest+tsc, branch
        ├── Reviewer           qa        claude-opus-5-5    read-only  on review request    APPROVE / CHANGES / REJECT
        └── DevOps             devops    claude-opus-5-5    read-only  every 3h             OPS issues; kickstart tick.free
```

Budgets per month: CEO $25, CTO $30, Manager $20, engineers $40 each, Reviewer $25, DevOps $20
(company cap $150). Heartbeat-on-interval is off for all; `maxConcurrentRuns = 1` each. Models
were chosen by Tejmul; change them in `roster.json`.

What agents do and do not do (his answers, 2026-11-01): agents **fix code only**; the pipeline is
run by `tick.py` under launchd (D4), and DevOps watches it and may `launchctl kickstart` the free
tick when it is loaded but dead — nothing more. The CEO **plans and reports; Tejmul still merges**.
No agent runs a stage against the live DB, verifies an address, or mails.

Project **ZoNuLy pipeline**: primary workspace = this checkout (`local_path`,
`/Users/tejmul/Desktop/ZoNuLy`, `defaultRef: main`); execution policy `isolated_workspace`,
`workspaceStrategy: git_worktree, baseRef: main`. **This only takes effect with the experimental
instance setting `enableIsolatedWorkspaces` on** (`bootstrap.py` sets it; without it the first
Builder ran in the shared main checkout on 2026-10-31 and, correctly, refused to touch anything).
An engineer's issue then runs in a fresh worktree at `.paperclip/worktrees/<ZON-n-title>/` inside
the repo (git-ignored), on a branch of the same name, where `jobhunter.db`, `.env`, `secrets/`,
`logs/` and `outbox/` do not exist. Tejmul reviews the branch and merges.

Routines without a project (CEO, CTO, Manager, DevOps) run in the adapter `cwd` — the main
checkout — in read-only mode, where the logs are.

## 4. Permissions — why no bypass

Paperclip's `claude_local` adapter defaults to `--dangerously-skip-permissions`. We set it
**false** (the auto-mode classifier also refused to create agents with it on, rightly). Instead:

- `--permission-mode default` (CEO, CTO, Manager, Reviewer, DevOps) or `acceptEdits` (the two engineers) via `adapterConfig.extraArgs`.
- Bash runs only when a rule in [.claude/settings.json](../.claude/settings.json) allows it; an
  unallowed command does not run and the agent is told so. The allowlist the agents need:

```json
"Bash(npx -y paperclipai@*)",            "Bash(tools/paperclip/paperclip.sh:*)",
"Bash(pytest:*)",                        "Bash(python -m pytest:*)",
"Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git add:*)", "Bash(git commit:*)",
"Bash(npx tsc:*)",                       "Bash(npx eslint:*)",
"Bash(launchctl list:*)", "Bash(launchctl kickstart -k gui/501/com.zonuly.*)",
"Bash(tail:*)", "Bash(ls:*)", "Bash(pmset -g:*)", "Bash(pwd)"
```

Deliberately **not** allowed: `git push`, `git merge`, `git stash`, `python scripts/run.py` (the
pipeline stages — `send`, `poll`, `daily` mail), anything that opens `jobhunter.db`.

The repo's `CLAUDE.md` is read by every agent automatically (it is the cwd), so the project rules
apply twice: once as CLAUDE.md, once as the agent's AGENTS.md.

## 5. Everyday use

```bash
tools/paperclip/paperclip.sh start                 # or: npx -y paperclipai@2026.1023.0 run
tools/paperclip/paperclip.sh status                # health, agents + spend, routine, open issues
open http://127.0.0.1:3100                         # the org chart, issues, activity, costs

# file work for the Builder by hand
tools/paperclip/paperclip.sh cli issue create -C <company-id> --project-id <project-id> \
  --title "autoreview rejects drafts that …" --priority high --assignee-agent-id <builder-id>
tools/paperclip/paperclip.sh cli issue list -C <company-id> --status todo,in_progress,in_review
tools/paperclip/paperclip.sh cli agent wake <lead-id> -C <company-id> --reason "read the loop now"
tools/paperclip/paperclip.sh cli run list -C <company-id>        # heartbeat runs, cost, transcript
tools/paperclip/paperclip.sh stop
```

Ids are in `data/paperclip.json`. Telemetry is off in the launcher's environment
(`PAPERCLIP_TELEMETRY_DISABLED=1`); the `onboard` default wrote `telemetry.enabled: true` into
`config.json` — flip it there too if you start Paperclip any other way.

When a Builder branch is merged, **the human runs `kg note`** (the agents have no DB and must
not) — the Builder's comment ends with the one line to paste.

## 6. What this deliberately does not do

- Does not run pipeline stages. Agents never scrape, score, draft, verify addresses or mail.
- Does not expose anything: loopback only, no auth, no Tailscale. Keep it that way until needed.
- Does not use Paperclip's own `AGENTS.md`-in-repo convention — the rules live in
  `tools/paperclip/agents/` so they are reviewed like code.
- No numbered approval was taken for anything *built* by the agents: a Builder branch is a
  proposal until Tejmul merges it (FINAL-PLAN-V3 §14 is unchanged).

## 7. Verified on this machine

**2026-10-31** — `onboard --yes` → embedded Postgres, `local_trusted`, loopback :3100. First org
(Lead / Builder / Reviewer) bootstrapped. **The Lead's first run worked**: Opus, 5 min 42 s, 218k
in / 28k out tokens ($2.69-equivalent, `subscription_included`); it tried `curl` and `env`, was
refused, switched to `paperclip.sh cli`, read BRIEF + autoreview.log and filed ZON-1 and ZON-2.
The Builder's six runs each lasted ~15 s: its workspace was the shared main checkout (the
experimental flag was off), rule 9 stopped it, and `$PAPERCLIP_*` expansion was refused, so it
could not even comment. Both findings are fixed in §3 / COMMON.md.

**2026-11-01** — Tejmul's org (CEO / CTO / Manager / 2 engineers / Reviewer / DevOps) created;
`enableIsolatedWorkspaces` + `…ByDefault` set; ZON-1/2 reassigned to the Leads engineer; old
agents terminated and deleted. Engineers and DevOps **paused** until the §4 allowlist carries the
DevOps commands and Tejmul says go. **Not yet verified:** an engineer run in a real worktree.

What the evidence said about the two problems (for the CTO's first run): `tick-free.log` —
8,379 ConnectError, 5,603 skipped, 355 `OperationalError` (152 × `database is locked`), 245 × 429,
67 × "another tick is running", 9,971 `web_emails: search failed … cooling down`; 130–300 jobs/day.
`contact.email_status`: real 246, fake 30,406 (dead 17,043, not-proved 11,743); Reacher audit
running at ~3 addresses/20 s with most results `unknown (io: incomplete)`.
