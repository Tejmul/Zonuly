# REA integration — reverse engineering for the scraping layer

**Status:** registered 2026-10-18. REA is a developer tool wired into Claude Code for this
repository; **nothing in `jobhunter/` imports it and nothing at runtime depends on it.** Same
stance as [Agent Reach](AGENT-REACH-INTEGRATION.md): an installer and a set of tools the agent
calls, not a library.

---

## 1. What REA is

[REA — Reverse Engineer Anything](https://github.com/morluto/rea) (MIT, TypeScript, npm
`rea-agents`) is one MCP server that gives a coding agent reverse-engineering tools: native
binaries through Hopper/Ghidra/IDA, JavaScript/Electron apps, .NET, Android, websites over the
Chrome DevTools Protocol, HAR/SAZ network captures, SQLite snapshots, packet captures, firmware.
Every result carries its evidence and its limitations. Analysis runs locally.

## 2. Why ZoNuLy wants it — and only for this

ZoNuLy's hardest recurring job is **adding a source**: a careers page that renders from an
undocumented JSON endpoint, a job board whose listing page is a React bundle, an ATS variant that
Ashby/Greenhouse/Lever parsers do not cover. Today that is done by hand in DevTools. REA makes it
an agent task with evidence:

| Job | REA tool (MCP / CLI) | What comes back |
|---|---|---|
| Find the JSON API behind a careers page | `inspect_web_page` / `rea inspect-web-page` on a page open in a Chrome you own (remote debugging on) | DOM structure, scripts, resources, the network requests the page made in the attach window |
| Read a board's bundle for route and endpoint candidates | `analyze_web_bundle` / `rea analyze-web-bundle` | chunk edges, route/endpoint candidates, vendor fingerprints — parsed, never executed |
| Replay a captured session offline | `inspect_web_network_capture` / `rea inspect-web-network-capture session.har har` | requests, responses, payloads, by record — recorded URLs are never fetched |
| Understand our own database or the committed demo snapshot | `inspect_sqlite_database` / `rea inspect-sqlite-database <copy>.db` | tables, columns, indexes, views, selected rows |
| Compare two captures of the same board (did the API change?) | `compare_web_captures` | artifact-level differences |

Not in scope, by the project's own rules:

- **No analysis of anyone's logged-in session.** [No personal accounts in scraping] — REA attaches
  to a browser *you* own; use a throwaway profile or a clean Chrome with `--remote-debugging-port`,
  never your real LinkedIn/X/GitHub session.
- **No native binary work is needed here.** Hopper/Ghidra are not installed and REA does not need
  them for websites, captures or SQLite.
- **Not a scraper.** REA tells you how a site works; the scraper that results is written in
  `jobhunter/scrapers/` like every other one, behind the admission gate and the source health
  circuit breaker.

## 3. How it is wired

Registered for **this directory only** (REA's own recommendation; `--scope project` would write a
committable `.mcp.json`):

```bash
claude mcp add --scope local rea -- npx -y rea-agents@6.5.0 mcp      # done 2026-10-18
claude mcp list                                                        # from this directory
```

The entry lives under `projects["/Users/tejmul/Desktop/ZoNuLy"].mcpServers.rea` in
`~/.claude.json`. Other projects do not load it. The project's
[.claude/settings.json](../.claude/settings.json) allows `mcp__rea` and `npx -y rea-agents@*`
so the tools run without a prompt per call.

Requirements on this machine: Node 26 (REA needs 22.19+/24.11+/26+; SQLite inspection needs
24.15+/26+) — present. A Chrome-family browser for website observation.

### Update

Local-scope registrations are pinned to the version in the command and are **not** touched by
`rea update`. To move:

```bash
claude mcp remove --scope local rea
claude mcp add --scope local rea -- npx -y rea-agents@<new> mcp
```

### Remove

```bash
claude mcp remove --scope local rea
```

Optional, machine-wide instead: `npx rea-agents setup` registers REA for every project and installs
its workflow skill under `~/.claude/skills/reverse-engineer-anything`. Not done; the local scope is
enough for this repository.

## 4. The workflow for a new source

1. Open the board or careers page in a Chrome started with `--remote-debugging-port=9222`
   (a fresh profile). Trigger the listing, a filter, "load more".
2. Ask the agent: *"Using REA, inspect the page at <url> and tell me which request returns the
   job listings, its parameters, and the fields in its response. Show the evidence."*
   REA runs `list_browser_targets` → `inspect_web_page` (→ `analyze_web_bundle` if the endpoint
   is built client-side).
3. The agent writes the scraper from the evidence, in the shape of
   [jobhunter/scrapers](../jobhunter/scrapers/) (fingerprint, salary fields, `remote_anywhere`,
   `is_senior`), adds it to `config.yaml → sources`, and runs it once behind `--dry-run`.
4. Record it: `python scripts/run.py kg note "…" --about feature:ats-json-scrapers,tool:rea`.

Keep the evidence IDs REA returns in the note; `export_evidence_bundle` saves them if the source
later breaks and the capture needs comparing.

## 5. Verified on this machine (2026-10-18)

- `claude mcp list` from this directory: `rea … ✔ Connected`.
- `rea capabilities --json` runs on Node 26 (native-macos and artifact-graph providers available).
- `rea inspect-sqlite-database` returns the engine, schema (with completeness), selected rows and
  a SHA-256 of the file — **on a small sample**. Two limits found the hard way: it refuses a
  database + WAL over **256 MiB** (`jobhunter.db` is 511 MiB) and times out at **30 s** on
  `demo.db` (198 MiB). Build a sample first and inspect that:

  ```bash
  sqlite3 demo.db "attach '/tmp/sample.db' as s;
    create table s.company as select id,name,domain,hq_region,hiring_status from company limit 300;"
  rea inspect-sqlite-database /tmp/sample.db --table company --row-limit 5 --json
  ```

  Never point it at the live `jobhunter.db`: the pipeline writes to it and a copied live pair is
  not an atomic snapshot anyway.
