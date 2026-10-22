import type { Metadata } from "next";
import { C, Callout, Code, Doc, H2, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "CLI" };

type Row = [string, string, string];
const G: { id: string; title: string; intro: string; rows: Row[] }[] = [
  {
    id: "core", title: "Core", intro: "The pipeline, one stage at a time.",
    rows: [
      ["doctor", "Check every external dependency and say how to fix it", "—"],
      ["profile", "Parse the resume into profile/profile.json", "--path"],
      ["scrape", "Run the scraper fleet and store new roles", "--sources greenhouse,hn_hiring · --verbose"],
      ["score", "Lexical gate on unscored roles, then rubric-score the best", "--limit 40 · --salaries 0"],
      ["find-contacts", "Find people at high-match companies", "--company ID · --limit 5"],
      ["draft", "Queue referral drafts for review", "--contact · --job · --limit 5 · --per-company 2"],
      ["approve EMAIL_ID", "Approve one draft from the terminal", "—"],
      ["send", "Send approved drafts inside the cap and window", "--email-id · --limit · --ignore-window"],
      ["poll", "Read Gmail for replies and classify them", "--followups/--no-followups"],
      ["daily", "One full ingestion: scrape, salaries, score, notify", "—"],
      ["discover", "Probe YC companies for new public ATS boards", "--limit 60"],
      ["add-company NAME", "Find one company’s board, seed it, pull its roles", "--website · --ats · --slug"],
      ["serve", "Start the API and the in-process scheduler", "--host 127.0.0.1 · --port 8000 · --reload"],
      ["fit-explain JOB_ID", "Show why the lexical gate scored a role as it did", "—"],
      ["gmail-verify / gmail-auth", "Test the app password / run the one-time OAuth consent", "—"],
    ],
  },
  {
    id: "tick", title: "tick — the unattended pipeline", intro: "What launchd runs. Each run takes a lock and skips if another is busy.",
    rows: [
      ["tick run", "One bounded slice of work", "--kind free | paid | send | all"],
      ["tick install", "Write and load the LaunchAgents (free, paid, send, referrals, audit)", "—"],
      ["tick uninstall", "Unload and remove them", "—"],
      ["tick status", "Loaded? Last run? What did it do?", "—"],
    ],
  },
  {
    id: "referrals", title: "referrals — people and addresses", intro: "The referral-only cycle and its parts.",
    rows: [
      ["referrals run", "One cycle: Bengaluru (★ first), Hyderabad, other India, remote", "--budget 1200 · --limit · --exclude"],
      ["referrals people", "The people step: GitHub, team pages, web, public LinkedIn", "--limit 50 · --list · --budget 3600"],
      ["referrals patterns", "Learn each company’s address format (2+ agree)", "--apply (default: dry run)"],
      ["referrals reverify", "Re-confirm referrers whose evidence is 90+ days old", "--limit 150"],
      ["referrals revalidate", "Re-check companies’ site, description and careers page", "--limit 30"],
      ["referrals measure", "Ready referrers per company, the ★ list, complete companies", "—"],
    ],
  },
  {
    id: "actor", title: "actor — LinkedIn guest pages and Apify", intro: "Our own Apify-style actors: cached, paced, breaker-guarded, never logged in.",
    rows: [
      ["actor people [COMPANY_ID]", "A company’s People tab, assembled from public sources and verified; city-locked", "--linkedin · --source … · --title · --max 100 · --apify-max 50 · --save (on)"],
      ["actor jobs KEYWORDS", "LinkedIn job search via the logged-out endpoint", "--location · --remote · --days · --experience · --max 25"],
      ["actor company / profile", "Public company pages / public profiles", "--fresh"],
      ["actor employees COMPANY_ID", "Who works there now, verified on each public profile", "--linkedin · --max 15 · --save (on)"],
      ["actor fill", "Fill companies that have no current referrer", "--limit 5 · --apify · --apify-max 25"],
      ["actor apify", "Token present? Today’s spend against the cap", "—"],
      ["actor runs / show RUN_ID", "The run ledger / one run’s dataset", "--limit"],
      ["actor status / reset ROUTE", "Circuit-breaker state per route / clear a cooldown", "—"],
    ],
  },
  {
    id: "outreach", title: "outreach — drafting and sending", intro: "Everything between a lead and a sent email.",
    rows: [
      ["outreach batch", "Build the week’s drafts (★ India first)", "--n · --dry-run · --no-research · --no-model"],
      ["outreach bets", "One gated draft per ready-to-ask company", "--limit 10"],
      ["outreach proposal", "Show how it would write — sends nothing", "--n 10 · --no-model · --show"],
      ["outreach polish", "Model rewrite of template drafts", "--limit 10"],
      ["outreach preflight", "Live-send readiness, red / amber / green, with fixes", "--json · --no-sign-in"],
      ["outreach send / send-due", "Send approved mail / the send job’s 5-minute action", "--limit · --ignore-window"],
      ["outreach ledger / week", "Today’s cap and use / the week’s quotas", "--start"],
      ["outreach followups", "One follow-up draft per silent thread", "--after-days"],
      ["outreach track / learn", "Every email’s status / reply rates by lever", "--status · --sentiment · --days"],
      ["outreach reply / events / confirm", "Record a reply by hand / list events / confirm a time", "—"],
      ["outreach rehearse-week", "Simulate a week on a copy of the database", "--start · --days 9"],
      ["outreach unsuppress", "Clear an opt-out", "--company · --contact"],
    ],
  },
  {
    id: "harvest", title: "harvest — discovery and enrichment", intro: "Finding companies and the facts about them.",
    rows: [
      ["harvest run", "The whole scrape end to end", "--target 500 · --exa/--no-exa"],
      ["harvest yc / exa / x", "Admit companies from YC, Exa’s index, or hiring posts on X", "-q · --per-query"],
      ["harvest probe / roles / careers", "Find ATS boards / pull open roles / find careers pages", "--limit · --concurrency"],
      ["harvest facts / websites / story", "Funding and HQ / sites for name-only rows / home and About pages", "--limit · --keyless"],
      ["harvest verify", "Hiring verification", "--limit 60 · --fresh"],
      ["harvest people / linkedin", "The free referrer waterfall / public LinkedIn leads", "--limit"],
      ["harvest india-jobs", "Bengaluru / Hyderabad companies via their postings", "-s linkedin|jobfound|instahyre · --city"],
      ["harvest priority / scrape-first", "Regrade overrated + priority / flag companies we know nothing about", "--dry-run"],
      ["harvest status", "Counts and progress", "—"],
    ],
  },
  {
    id: "targeting-cmds", title: "targets, india, selection", intro: "Grading companies and ranking roles.",
    rows: [
      ["targets grade", "Grade every company: hyped → reject, then funding, stipend, tiers", "--pay/--no-pay · --limit"],
      ["targets list", "The graded registry", "--tier · --limit 25 · --json"],
      ["targets enrich / verify", "Read a company’s site and regrade / check a hiring claim", "--company-id · --fresh"],
      ["india discover", "Admit new Bengaluru / Hyderabad startups", "--limit 30 · --sources …"],
      ["india pay-floor", "Hide or restore India companies by the pay floor", "--dry-run"],
      ["selection recompute / top", "Re-score changed roles / best bets per market", "--market india|remote|abroad"],
      ["selection brief JOB_ID / briefs", "One role’s breakdown / needs briefs for the top roles", "--top 50"],
    ],
  },
  {
    id: "lists-people", title: "lists, people", intro: "Hand-picked lists and per-person tools.",
    rows: [
      ["lists ls / show / sync", "Lists on disk / one list in order / attach to the database", "[key] · --no-verify"],
      ["lists people", "Boards, roles and referrers for every list company", "[key] · --linkedin-only · --emails-only"],
      ["lists audit", "Re-verify GitHub orgs (a wrong org loses its contacts)", "--dry"],
      ["people classify", "Label contacts by role class", "--all · --model"],
      ["people emails COMPANY_ID", "Real addresses from the web, then fill by pattern", "—"],
      ["people queue", "The referral queue", "--company-id · --limit 25"],
    ],
  },
  {
    id: "kg", title: "kg — the knowledge graph", intro: "Search and record the project’s memory.",
    rows: [
      ["kg build", "Reload context, mirror data, write BRIEF.md and the viewer", "--no-viewer"],
      ["kg search QUERY", "Full-text search across both layers", "--kind · --limit 15"],
      ["kg show NODE", "A node and everything it links to", "--depth 1 · --json"],
      ["kg path A B", "Shortest path between two nodes", "--max-depth 6"],
      ["kg note TEXT", "Record what changed, linked to nodes", "--about … · --tag …"],
      ["kg why COMPANY", "Should we write to them, to whom, and what we know", "--json"],
      ["kg shortlist / expand / guard", "Rank targets / co-funded companies / cooldown check", "—"],
      ["kg hubs / compose / audit / export", "Centrality / best design per stage / contradictions / HTML-JSON-GraphML", "—"],
    ],
  },
  {
    id: "ops-cmds", title: "audit, models, research, notify", intro: "Operating and inspecting.",
    rows: [
      ["audit run / show / status", "One audit now / last report / agent state", "--quiet · --json"],
      ["models status / check / costs", "OpenRouter key, aliases, caps / verify model ids / spend", "--live · --days · --month"],
      ["research doctor / web / read / company", "Research channels: health, search, read a page, research a company", "--depth · --fresh"],
      ["notify test / digest / watchdog", "Notification bot and the watchdog report", "--dry/--send"],
    ],
  },
];

export default function Cli() {
  return (
    <Doc slug="cli">
      <Lead>
        Everything the dashboard does, and more, is a command in <C>scripts/run.py</C> — a Typer app with sixteen
        command groups. Run any command with <C>--help</C> for its full options.
      </Lead>
      <Code title="usage">{`
python scripts/run.py <command> [options]
python scripts/run.py <group> <command> [options]
python scripts/run.py actor people --help
`}</Code>
      <Callout kind="tip">Inside the repo&apos;s virtualenv, use <C>.venv/bin/python scripts/run.py …</C> so the right Python and packages are used.</Callout>
      {G.map((g) => (
        <section key={g.id}>
          <H2 id={g.id}>{g.title}</H2>
          <P>{g.intro}</P>
          <Table head={["Command", "Does", "Key options"]} mono={[0, 2]} rows={g.rows} />
        </section>
      ))}
    </Doc>
  );
}
