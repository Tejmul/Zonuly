# Agent Reach integration — the web-research layer

**Status:** built. Adds `jobhunter/research/`, `/api/research/*`, `python scripts/run.py research …`.
Nothing existing was changed except three additive edits (`config.yaml` gained a `research:`
block, `api.py` gained an `include_router` line, `scripts/run.py` gained a `research` sub-app).

---

## 1. What Agent Reach actually is

[Agent Reach](https://github.com/Panniantong/Agent-Reach) markets itself as "eyes for your
agent — read & search Twitter, Reddit, YouTube, GitHub … one CLI, zero API fees". Reading the
source rather than the README changes the picture. From its own `agent_reach/core.py`:

> *"Agent Reach helps AI agents install and configure upstream platform tools (twitter-cli,
> yt-dlp, mcporter, gh CLI, etc.). After installation, agents call the upstream tools
> directly — no wrapper layer needed."*

Concretely:

| Part | What it really contains |
| --- | --- |
| `agent_reach/cli.py` (87 KB) | `install`, `configure`, `doctor`, `skill`, `uninstall`, `format`, `transcribe`, `check-update`. **No search or read command.** |
| `agent_reach/channels/*.py` (15 files) | Health probes — `can_handle(url)` and `check(config) -> (status, message)`. Only `WebChannel.read()` and `YouTubeChannel.transcribe()` do any fetching. |
| `agent_reach/skill/SKILL.md` | The actual capability: a document telling an agent which **shell command** to run per platform. |
| `agent_reach/integrations/mcp_server.py` | One MCP tool: `get_status`. |

So Agent Reach is **an installer, a doctor, and a routing document**. The data acquisition is
done by the upstream tools it installs.

### Why we did not vendor it

Copying the repo into ZoNuLy would have imported ~3,000 lines of Chinese-language macOS/Homebrew
installer logic, cookie-extraction code for XiaoHongShu, Bilibili and Weibo, and a test suite for
all of it — and gained **zero** acquisition code, because there is none to import. It would also
have forked a dependency that ships breaking changes (`git+…@<sha>` pins live inside its Reddit
channel).

What we kept is the part that is genuinely good, which is the *contract*:

1. **A capability is served by an ordered list of candidate backends** — `backends[0]` preferred,
   the rest fallbacks. Switching backends is reordering a list, not rewriting code.
2. **`which()` is not proof of health.** A stale venv shim resolves on `PATH` and still cannot
   execute. A backend is only "active" once it has really answered.
3. **Honest tiering.** Where no zero-config path exists (Reddit), say so and print the fix
   instead of returning an empty list that reads like "nobody mentioned this company".

Agent Reach stays installed *as a tool*, and remains the recommended way to get the backends onto
a machine and to health-check them:

```bash
agent-reach install --system     # installs mcporter, yt-dlp, node/deno, gh, rdt …
agent-reach doctor --json        # per-platform backend status
```

---

## 2. What ZoNuLy uses

| Capability | Backends, in order | Setup |
| --- | --- | --- |
| **Web search** | `exa-mcp` → `exa-api` | `mcporter` + Exa MCP (zero-key), or `EXA_API_KEY` |
| **Page read** | `jina` → `exa-mcp` → `direct` | none — `r.jina.ai` is public; `direct` is httpx + the existing `scrapers.base.html_to_text` |
| **GitHub** | `gh-cli` → `github-api` | `gh auth login`, or `GITHUB_TOKEN` |
| **Reddit** | `rdt` → `opencli` | login required — Reddit has **no** anonymous path |
| **YouTube** | `yt-dlp` | needs node or deno for YouTube's JS |

Deliberately **not** used: Twitter/X, XiaoHongShu, Bilibili, Xueqiu, V2EX, Xiaoyuzhou, Facebook,
Instagram. They are cookie-backed, region-specific, or irrelevant to an India/remote engineering
job hunt. Adding one later is a row in `DEFAULT_ROUTES` plus a module — the routing already
supports it.

---

## 3. Where it lives in ZoNuLy

```
jobhunter/
  db.py  llm.py  normalize.py            <- unchanged
  scrapers/   contacts/   outreach/      <- unchanged
  research/                              <- NEW, same layer as scrapers/contacts
    __init__.py    the whole public surface, one import
    backends.py    routing, Windows-safe subprocess, secrets, doctor
    models.py      SearchResult, Page, RepoHit, RedditPost, Video, FundingSignal, CompanyResearch
    cache.py       TTL cache in jobhunter.db (its own table, db.py untouched)
    web.py         Exa search; Jina / Exa / direct page reading
    github.py      repo + org + people search
    reddit.py      rdt / opencli
    youtube.py     search + subtitle transcripts
    agent.py       composed tasks: research_company(), find_startups(), extractors
    routes.py      APIRouter -> /api/research/*
  pipeline.py  matcher.py  kg/           <- unchanged, and research never imports them
  api.py                                 <- +2 lines (include_router)
```

**The layering rule holds.** `research/` may use `CONFIG`, `db` (for its own cache table) and
`scrapers.base`; it must never import `matcher`, `pipeline`, `kg`, `outreach` or `api`.
Acquisition here, judgement there — scoring, the graph, the resume and outreach are untouched and
unaware of it.

---

## 4. Setup

Everything below is optional; each missing piece degrades one channel and `research doctor`
prints the exact fix.

### Already required by ZoNuLy

```bash
uv sync                     # the project venv (this must be run first — see §7)
```

### Web search (recommended — this is the big one)

```bash
npm install -g mcporter
mcporter config add exa https://mcp.exa.ai/mcp --scope home
mcporter list                          # expect: exa (2 tools)
```

No key needed — mcporter talks to Exa's hosted MCP endpoint. If you would rather use the HTTP
API directly, put `EXA_API_KEY` in `.env` (see `.env.example`) and the `exa-api` fallback
activates on its own.

### GitHub

```bash
winget install GitHub.cli      # or https://cli.github.com
gh auth login
```

Optional: `GITHUB_TOKEN` in `.env` for the API fallback.

### YouTube

```bash
python -m pip install -U "yt-dlp[default]"
winget install OpenJS.NodeJS.LTS       # YouTube needs a JS runtime
```

### Reddit (optional, and honestly the weakest link)

Reddit blocks anonymous `.json` endpoints and closed self-service API registration, so **every**
working backend needs a logged-in session. Agent Reach documents this and so do we:

```bash
pipx install "git+https://github.com/public-clis/rdt-cli.git"
rdt login                              # imports a reddit_session cookie from your browser
rdt status                             # expect authenticated: true
```

Until then `research reddit` returns `error` + `hint`, never a misleadingly empty list.

### Agent Reach itself (optional)

Keep it in its own environment — it is a tool, not a library:

```bash
pipx install "agent-reach @ https://github.com/Panniantong/Agent-Reach/archive/refs/heads/main.zip"
agent-reach doctor --json
```

(PyPI's `agent-reach` is stale at 0.1.0; the GitHub archive is 1.5.0.) A `research` extra exists
in `pyproject.toml` for anyone who wants it inside the project venv instead:
`uv sync --extra research`.

### Secrets

`cp .env.example .env` and fill in only what you need. `.env` is gitignored. Nothing secret ever
goes in `config.yaml`, gets logged, or gets passed as a command-line argument — secrets reach
child processes through the environment, which `ps` and Task Manager cannot read.

---

## 5. Using it

### CLI

```bash
python scripts/run.py research doctor
python scripts/run.py research web "seed-stage AI infrastructure startups hiring in London"
python scripts/run.py research read https://example.com/careers --text
python scripts/run.py research github "llm evaluation framework"
python scripts/run.py research reddit "what is it like working at <company>"
python scripts/run.py research youtube "<founder> interview"
python scripts/run.py research company "Acme AI" --depth deep
python scripts/run.py research startups --topic AI --table
python scripts/run.py research cache --purge
```

### Python

```python
from jobhunter import research

research.search_web("AI startups that raised a seed round in the UAE", limit=8)
research.read_page("https://acme.ai/careers")
research.search_github("retrieval evaluation", limit=10)
research.research_company("Acme AI", depth="deep")
research.find_startups(topic="AI", regions=["United States", "United Kingdom"], enrich=5)
research.doctor()
```

Every function returns a plain `dict` and **never raises because a backend is down** — a missing
channel comes back as `{"results": [], "error": ..., "hint": ...}`, which a caller can act on.

### HTTP (with `python scripts/run.py serve`)

```
GET  /api/research/doctor
GET  /api/research/search?q=…&limit=8
GET  /api/research/read?url=…
GET  /api/research/github?q=…
GET  /api/research/reddit?q=…&subreddit=…
GET  /api/research/youtube?q=…
GET  /api/research/youtube/transcript?url=…
GET  /api/research/company?name=…&depth=quick|standard|deep
POST /api/research/startups   {"topic":"AI","regions":[…],"limit":10,"enrich":5}
POST /api/research/cache/purge
```

---

## 6. Design decisions worth knowing

**Extract, never invent.** `agent.py` derives funding stage, amount, investors, hiring signals
and role titles with regexes over fetched text, and carries the sentence that justified each
claim in `evidence_quote` / `evidence_url`. When the text does not say, the field is `null` — it
is never filled by an LLM guess. Records carry `confidence: verified | scraped | inferred`; a
company known only from a headline stays `inferred`. This is the same rule the outreach drafter
already follows, and it is why the research layer does not call `llm.py` at all (which is also
what keeps it usable while `llm.py` still holds the old Ollama client).

**Caching.** Search results, pages and company profiles are cached for 24 h in a
`research_cache` table inside `jobhunter.db`, created by `research/cache.py` itself with
`CREATE TABLE IF NOT EXISTS` — exactly how `kg/store.py` owns its tables, and the reason `db.py`
needed no edit. `--fresh` bypasses it; `research cache --purge` clears stale rows.

**URL safety.** Every URL handed to the page reader goes through `web.public_http_url()`, which
rejects non-http schemes, `localhost`, `*.local` and private/loopback/link-local addresses. These
URLs come out of search results — untrusted input — and without the check the reader would be a
way to fetch `169.254.169.254` or read local files.

**Windows.** Executables are resolved with `shutil.which` (PATHEXT-aware, so node's `mcporter.CMD`
is found) and then executed by absolute path with `shell=False` — nothing to quote, nothing to
escape, no shell injection surface. Child processes get `PYTHONIOENCODING=utf-8` and
`PYTHONUTF8=1`, because several of these CLIs print box-drawing characters and emoji and die on
the default Windows code page (`rdt --help` does exactly that without it).

**The ATS hand-off.** `agent.detect_ats()` recognises Greenhouse / Lever / Ashby board URLs in
search results and returns `(ats, slug)` — the same two fields `Company.ats` / `Company.ats_slug`
already use, so a discovered board plugs straight into the existing scrapers. Wiring that write
is deliberately *not* done here: acquisition returns records, the pipeline decides what to store.

---

## 7. Known state on this machine (2026-09-02)

`uv sync` had never been run on this checkout — `.venv` existed but was empty. It has been run.

Live-verified working: Exa via mcporter (search + fetch), Jina Reader, `gh` CLI, `yt-dlp`.
`rdt` is installed but **not logged in** (`rdt status` → `authenticated: false`), so Reddit
returns its hint until `rdt login` is run. `opencli` is not installed (desktop-only, optional).

End-to-end test — `research startups --topic AI --regions "United States,United Kingdom,United
Arab Emirates" --enrich 6` returned 8 companies from 6 searches in ~50 s (cached) with company
name, website, funding stage and amount, investors and, where the site exposed one, the careers
URL and the role titles on it. Companies whose own site could not be corroborated came back
`confidence: inferred` with `website: null` rather than a guess.

**Unrelated pre-existing bug found while testing:** `app.openapi()` raises
`PydanticUserError: CompanyUpdateIn is not fully defined` under FastAPI 0.141 + pydantic 2.13,
which breaks `/docs` and `/openapi.json` for the whole API. Confirmed present with the research
router removed, so it predates this work; the routes themselves serve fine (verified with
`TestClient`). A `CompanyUpdateIn.model_rebuild()` after the class, or moving it above the
handler, should fix it — left alone here because it is outside this change.

---

## 8. Next steps this deliberately did not take

- Writing discovered companies into the `company` table (needs a numbered approval per
  FINAL-PLAN-V3 §14 — acquisition currently returns records only).
- Mirroring research findings into the knowledge graph as `company:*` context nodes.
- Feeding `research_company()` output into `outreach/researcher.py` as a second hook source
  alongside GitHub repos.
- LinkedIn. Agent Reach routes it through an MCP that requires a login session; the honest
  fallback is Jina Reader on a public profile URL, which is thin.
