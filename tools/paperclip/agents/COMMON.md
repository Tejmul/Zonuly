# Rules for every ZoNuLy agent run by Paperclip

You are a Claude Code process started by Paperclip (http://127.0.0.1:3100) on Tejmul's laptop.
You are headless: nobody is watching, nothing you would normally be asked permission for will be
granted — it is simply refused — and the only things that reach a human are the comment you leave on
your issue and the branch you leave behind. Read `CLAUDE.md` and `knowledge/BRIEF.md` first.

## The mandate

Read `tools/paperclip/MANDATE.md` — Tejmul's goal in his words (get placed; ~1,000 real, verified
addresses a day at motive companies; he sends; free tools only; numbered CSV batches). Every goal,
issue and fix traces back to it.

## The two problems this company exists to fix (Tejmul, 2026-11-01)

1. **Scraping is slow and stalls.** 130–300 jobs a day; the hourly tick is eaten by `web_emails`
   searches failing in loops (Jina/Exa "cooling down", thousands of ConnectErrors, 429s) and by
   `sqlite3.OperationalError: database is locked` when the tick, the Reacher audit and the API
   write at once. The pipeline must run until it is done, not stop out of nowhere.
2. **Unproved addresses got mailed and Gmail blocked the inbox.** The segregation now exists
   (`contact.email_status` real | fake:<why>; 246 real vs ~30,000 fake on 2026-11-01). What is
   missing is throughput: proving with check-if-email-exists is slow, and anything not `real`
   must never reach a lead, a CSV or a send.

## Hard rules — these are the project's, not Paperclip's

1. **Never mail anything.** Do not run `send`, `poll`, `daily`, `verified_sprint`, or any
   Gmail/SMTP code path. Sending is off until Tejmul says it starts.
2. **Never guess, pattern-fill, write or print an email address.** Only check-if-email-exists
   proofs an address (`jobhunter/contacts/reacher.py`). `db._only_verified_addresses` is law.
3. **Never touch the live database.** `jobhunter.db` is untracked and lives only in the main
   checkout. Do not open it, copy it, or point code at it. Tests use their own fixtures.
   (DevOps may read logs and launchd state; nobody writes to the DB.)
4. **Never start Ollama or any local model.** OpenRouter aliases only, behind the ledger.
5. **Never cut** the review gate, the 25/day send cap, or the confidence labels.
6. **Focus is Bengaluru + Hyderabad.** Do not widen `focus.cities`, admit remote, or the YC list.
7. **No personal accounts in scraping.** Guest pages or throwaway credentials only.
8. **Layering**: `db/llm/normalize` ← `scrapers/contacts/outreach` ← `pipeline/matcher/kg` ←
   `api` ← `dashboard`. Nothing imports upward.
9. **Git**: engineers work only inside the worktree Paperclip provisioned for the issue (check
   `pwd` — it is under `…/ZoNuLy/.paperclip/worktrees/<issue>/`, never the checkout root itself). If you find
   yourself in the main checkout, read only, do not edit, and say so in your comment. Commit on
   your branch. **Never** `git push`, never merge into `main`, never `git stash`. No
   `Co-Authored-By` / `Generated-with` trailers.
10. **Secrets** (`.env`, `secrets/`, tokens) are not yours. Do not look for or recreate them.
11. **Verify in code, not a browser**: `pytest`, `npx tsc --noEmit`, `npx eslint`. No Playwright.
12. **Tejmul merges.** Nothing you build is "done" until he merges it. No agent approves a merge.

## How to talk to Paperclip

`curl` and `$PAPERCLIP_*` variables are **not available** in your shell (the allowlist refuses
them). The one route is the repo's launcher, which reads your run's credentials itself:

```
tools/paperclip/paperclip.sh cli issue list -C $PAPERCLIP_COMPANY_ID   # ← no: variables are refused
tools/paperclip/paperclip.sh cli issue list                             # ← yes: company comes from your env
tools/paperclip/paperclip.sh cli issue comment <ZON-n> --body "..."
tools/paperclip/paperclip.sh cli issue create --title "..." --description "..." --priority high \
    --assignee-agent-id <id> --project-id <id> --standalone
tools/paperclip/paperclip.sh cli issue update <ZON-n> --status in_review --assignee-agent-id <id>
tools/paperclip/paperclip.sh cli agent list
```

Every issue-bound run **must** end with one comment on the issue (Paperclip enforces it).

## The report every run ends with

```
Did: <what changed, file:line — or "nothing", and why>
Verified: <the exact command and its result — paste failing output if it failed>
Not done / needs Tejmul: <blocked, risky, or a decision only he can make>
Branch: <name>   Commit: <sha>          (engineers only)
Note for the graph: <one line for `kg note` — the human runs it when merging>
```

Say plainly when something failed. Never claim a test passed unless you saw it pass. If an action
would need permission, do not do it — write it under "needs Tejmul".
