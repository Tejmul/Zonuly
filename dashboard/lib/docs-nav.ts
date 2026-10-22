/* The documentation's table of contents — the one place a page's title, section and
   one-line summary live. The sidebar, the search, each page's header and the
   previous / next links all read from here, so adding a page is one entry plus its
   folder under app/docs/. */

export type DocPage = {
  slug: string;          // "" is the overview at /docs
  title: string;
  blurb: string;         // one sentence: shown under the title and in search
  keywords?: string;     // extra words the search should match
};

export type DocSection = { title: string; pages: DocPage[] };

export const DOCS: DocSection[] = [
  {
    title: "Start here",
    pages: [
      { slug: "", title: "Overview", blurb: "What ZoNuLy is, what it does every day, and how the pieces fit.", keywords: "intro home about" },
      { slug: "why", title: "Why ZoNuLy exists", blurb: "The problem, the bet behind it, and the rules it never breaks.", keywords: "motivation problem referral fresher" },
      { slug: "quickstart", title: "Quickstart", blurb: "Install, run the API and the dashboard, and turn on the schedule.", keywords: "install setup run serve npm uv launchd" },
    ],
  },
  {
    title: "Concepts",
    pages: [
      { slug: "vocabulary", title: "Vocabulary", blurb: "Companies, roles, leads — and the labels every row carries.", keywords: "glossary terms tier pay power confidence affiliation complete" },
      { slug: "targeting", title: "Targeting", blurb: "Which companies and roles are worth a referral ask, and which never are.", keywords: "fresher remote india bengaluru hyderabad underrated funded overrated pay floor" },
      { slug: "guarantees", title: "Guarantees & guardrails", blurb: "The review gate, the send cap, evidence on every field — what is never cut.", keywords: "safety review gate cap no fabrication evidence" },
      { slug: "decisions", title: "Design decisions", blurb: "The settled decisions, numbered, and the reasoning that settled them.", keywords: "d1 d21 adr architecture decision" },
    ],
  },
  {
    title: "Architecture",
    pages: [
      { slug: "architecture", title: "System overview", blurb: "The stack, the layers, and what calls what.", keywords: "stack python fastapi sqlite nextjs layering modules" },
      { slug: "data-model", title: "Data model", blurb: "The tables behind every page and the fields that matter.", keywords: "database sqlite schema company job contact email reply" },
      { slug: "scheduling", title: "Scheduling & the tick", blurb: "The background jobs, what one cycle does, and how work is budgeted.", keywords: "launchd tick cron scheduler budget lock" },
      { slug: "knowledge-graph", title: "Knowledge graph", blurb: "The project's memory: data mirrors, context, and the generated brief.", keywords: "kg graph brief context nodes edges" },
    ],
  },
  {
    title: "The pipeline",
    pages: [
      { slug: "pipeline", title: "Pipeline at a glance", blurb: "From an unknown company to a sent referral ask, stage by stage.", keywords: "flow stages end to end" },
      { slug: "discovery", title: "Discovery", blurb: "Where companies come from, and the gate they pass to get in.", keywords: "scrape sources yc wellfound greenhouse lever ashby india harvest" },
      { slug: "fill", title: "Fill & enrichment", blurb: "Description, LinkedIn page, funding and pay — completed with evidence.", keywords: "enrich fill description funding linkedin backfill" },
      { slug: "hiring-proof", title: "Hiring proof", blurb: "Proving a company is really hiring, on its own site or board.", keywords: "careers verify ats board jobs" },
      { slug: "roles-scoring", title: "Roles & scoring", blurb: "Scraping roles, matching them to the resume, and ranking the odds.", keywords: "match score selection confidence best bets fit" },
      { slug: "people", title: "People & referrers", blurb: "Finding the humans who could refer you — without your accounts.", keywords: "leads referrers github linkedin apify actors city" },
      { slug: "email-addresses", title: "Email addresses", blurb: "Real addresses first, confirmed patterns second, never invented.", keywords: "pattern hunter mx verify guessed" },
      { slug: "outreach", title: "Drafting & the review gate", blurb: "How each ask is written, and why nothing leaves without your approval.", keywords: "draft email template review approve queue" },
      { slug: "sending", title: "Sending & deliverability", blurb: "Gmail, the daily cap, pacing like a person, and the bounce brake.", keywords: "send gmail cap pacing warmup bounce spam" },
      { slug: "replies", title: "Replies & follow-ups", blurb: "Reading what comes back, classifying it, and following up once.", keywords: "reply poll bounce follow-up calendar" },
    ],
  },
  {
    title: "Using the dashboard",
    pages: [
      { slug: "dashboard", title: "Dashboard tour", blurb: "Every page in the app, what it shows, and what you can do there.", keywords: "ui pages companies leads queue graph atlas" },
    ],
  },
  {
    title: "Reference",
    pages: [
      { slug: "cli", title: "CLI", blurb: "Every command in scripts/run.py.", keywords: "command line typer run.py" },
      { slug: "api", title: "HTTP API", blurb: "Every endpoint the dashboard (and you) can call.", keywords: "rest fastapi endpoints routes" },
      { slug: "configuration", title: "Configuration", blurb: "config.yaml, section by section, and the environment variables.", keywords: "config yaml settings env secrets" },
    ],
  },
  {
    title: "Operations",
    pages: [
      { slug: "runbook", title: "Runbook", blurb: "Day-to-day operation: logs, restarts, pausing, backups.", keywords: "ops logs restart pause backup" },
      { slug: "troubleshooting", title: "Troubleshooting", blurb: "Known failure modes and exactly what to do about each.", keywords: "error locked gmail 5.4.5 999 sleep blocked" },
    ],
  },
];

export const FLAT: (DocPage & { section: string })[] = DOCS.flatMap((s) =>
  s.pages.map((p) => ({ ...p, section: s.title })),
);

export const docHref = (slug: string) => (slug ? `/docs/${slug}` : "/docs");

export function docBySlug(slug: string) {
  const i = FLAT.findIndex((p) => p.slug === slug);
  return { page: FLAT[i], prev: i > 0 ? FLAT[i - 1] : null, next: i >= 0 && i < FLAT.length - 1 ? FLAT[i + 1] : null };
}
