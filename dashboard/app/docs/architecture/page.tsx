import type { Metadata } from "next";
import { A, C, Callout, Defs, Doc, H2, H3, Lead, P, Table } from "@/components/docs/prose";
import { LayersDiagram } from "@/components/docs/diagrams";

export const metadata: Metadata = { title: "System overview" };

export default function Architecture() {
  return (
    <Doc slug="architecture">
      <Lead>
        One laptop, one SQLite file, one Python package, one dashboard. ZoNuLy is deliberately small: a deterministic
        pipeline that calls models only for narrow tasks, and background jobs that keep every company filling.
      </Lead>

      <H2>The stack</H2>
      <Table
        head={["Layer", "Technology", "Why"]}
        rows={[
          ["Pipeline", "Python 3.12 · httpx · BeautifulSoup · lxml · feedparser", "Scraping and parsing are Python’s home ground (D1)."],
          ["Storage", "SQLite (WAL) via SQLModel", "One file, no server, survives a laptop (D9). Columns are added in place by ensure_columns — no migration tool."],
          ["API", "FastAPI + uvicorn, ~100 routes", "Long work runs on background threads and is polled as a task."],
          ["Scheduling", "macOS launchd agents + in-process APScheduler", "launchd survives sleep and restarts; the API’s scheduler handles the daily scrape and reply checks."],
          ["Models", "OpenRouter (aliases cheap / judge / writer)", "No local model on an 8 GB machine (D18). Every call is budget-checked and costed."],
          ["Graph", "SQLite tables + FTS5 + NetworkX", "The project’s memory in the same file as the data (D10)."],
          ["Dashboard", "Next.js 16 · React 19 · Tailwind 4 · GSAP · d3-force", "A fast, dense, black-and-white instrument over the API."],
          ["Email", "Gmail over SMTP / IMAP with an app password", "Sends from your own address; reads replies back."],
        ]}
      />

      <H2>The layering rule</H2>
      <P>
        Imports flow one way. A lower layer never imports a higher one, which keeps the scrapers testable in isolation
        and lets the API change without touching the pipeline.
      </P>
      <LayersDiagram
        layers={[
          { name: "dashboard", what: "Next.js — talks to the API over HTTP only", modules: ["app/(app)/*", "app/docs/*", "lib/api.ts"] },
          { name: "api", what: "FastAPI routes, background tasks, access guard", modules: ["api.py", "outreach/routes.py", "research/routes.py", "kg/routes_warm.py"] },
          { name: "pipeline · matcher · kg", what: "Orchestration, scoring and the graph", modules: ["tick.py", "pipeline.py", "matcher.py", "targeting.py", "confidence.py", "fill.py", "referrals.py", "kg/"] },
          { name: "scrapers · contacts · outreach · research · actors", what: "Talking to the outside world", modules: ["scrapers/", "contacts/", "outreach/", "research/", "actors/"] },
          { name: "db · llm · normalize", what: "The foundation", modules: ["db.py", "llm.py", "openrouter.py", "normalize.py", "guardrails.py", "skills.py"] },
        ]}
      />
      <Callout kind="note">
        One documented exception: <C>outreach/researcher.py</C> reaches up into <C>kg/warm.py</C> through a lazy, guarded
        import, to use warm paths when writing.
      </Callout>

      <H2>Package map</H2>
      <H3>Core</H3>
      <Defs
        items={[
          [<C key="1">tick.py</C>, "The background pipeline: bounded slices of work run by launchd, each with a budget and a lock."],
          [<C key="2">pipeline.py</C>, "Runs the scraper fleet, filters, de-duplicates and stores roles, admitting each company through the gate."],
          [<C key="3">admission.py</C>, "The one gate for new companies: name clean-up, bogus-site rejection, de-duplication, the overrated rule."],
          [<C key="4">fill.py</C>, "Completes one company without a model: description, LinkedIn page, hiring proof, funding — recording why anything is missing."],
          [<C key="5">referrals.py</C>, "“The main thing”: the people step, the referral cycle, addresses, Hunter and Apify steps, re-verification."],
          [<C key="6">guardrails.py</C>, "The validation layer every extracted value passes before it is written, with provenance."],
          [<C key="7">matcher.py · fit.py</C>, "A free lexical gate, then a model rubric on the survivors, then hard caps."],
          [<C key="8">targeting.py · confidence.py</C>, "Tiers, pay power, priority — and per-role selection odds."],
          [<C key="9">careers.py · hiring_verify.py</C>, "Find any careers page and read its roles; corroborate a hiring claim."],
          [<C key="10">india.py · india_sources.py · india_jobs.py</C>, "Bengaluru / Hyderabad discovery and the India pay floor."],
          [<C key="11">harvest.py · enrich.py · levels.py</C>, "Bulk company discovery; facts and stories; levels.fyi pay."],
          [<C key="12">audit.py · watchdog.py · backoff.py · channels.py</C>, "Self-monitoring: hourly audit, stuck-job alarms, per-company retry backoff, channel health."],
        ]}
      />
      <H3>Subpackages</H3>
      <Table
        head={["Package", "Responsibility"]}
        mono={[0]}
        rows={[
          ["scrapers/", "One module per source, each exposing fetch(http, companies) → RawJob list: Greenhouse, Lever, Ashby, HN, RemoteOK, WWR, YC, Wellfound, careers pages, funding RSS, India boards."],
          ["contacts/", "The people and address waterfall: GitHub commits, team pages, public LinkedIn, public mentions, address patterns, MX checks, Hunter, role classes, affiliation."],
          ["outreach/", "Research → compose → gates → review → ledger → send → track replies → follow up → schedule → learn."],
          ["research/", "Data acquisition only (search, page read, GitHub, Reddit, YouTube), routed across ordered backends with a 24 h cache. Never writes pipeline tables."],
          ["actors/", "“Our own Apify”: LinkedIn guest-page actors with a run ledger, datasets, a page cache and a per-host circuit breaker; plus the Apify People-tab source."],
          ["kg/", "The knowledge graph: store, context loader, data sync, brief writer, analysis, warm paths."],
        ]}
      />

      <H2>External services</H2>
      <Table
        head={["Service", "Used for", "Limit"]}
        rows={[
          ["OpenRouter", "Rubric scoring, role classes, drafting polish, reply classification", "18 req/min · ₹100/day · ₹2,500/month · free models first"],
          ["Jina Reader", "Reading pages; keyless search via DuckDuckGo / Startpage", "Paced; cools down on errors"],
          ["Exa", "Company discovery, funding facts, one people search per company", "100 searches a day"],
          ["GitHub API", "Commit emails, org members, recent committers", "5,000 / hour with a token, reserves kept"],
          ["Apify", "A company’s LinkedIn People tab", "$0.15 / day — inside the free $5 / month"],
          ["Hunter.io", "Learning address patterns", "25 lookups / month (currently off: no key)"],
          ["scrape.do", "levels.fyi pay pages", "120 / day · 1,000 / month"],
          ["Gmail", "Sending and reading replies", "25 / day by our own cap"],
        ]}
      />
      <P>
        Continue with the <A href="/docs/data-model">Data model</A> and <A href="/docs/scheduling">Scheduling</A>.
      </P>
    </Doc>
  );
}
