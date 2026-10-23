import type { Metadata } from "next";
import { A, C, Callout, Code, Doc, H2, Lead, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "HTTP API" };

type R = [string, string, string];
const AREAS: { id: string; title: string; rows: R[] }[] = [
  { id: "companies", title: "Companies", rows: [
    ["GET", "/api/companies", "Filter by tier, hiring, contacts; sort by priority, score, tier, pay or name"],
    ["POST", "/api/companies", "Add a company {name, website?, ats?, ats_slug?, scrape_now}"],
    ["GET", "/api/companies/{id}/detail", "Everything about one company, plus the graph’s verdict"],
    ["GET", "/api/companies/filling", "Incomplete companies and what each is missing"],
    ["GET", "/api/companies/grouped", "Roles grouped by family, with referrers"],
    ["POST", "/api/companies/{id}/fill", "Fill one company now (task); ?auto=true skips complete / recently filled"],
    ["POST", "/api/companies/{id}/find-contacts", "Discover people (task)"],
    ["POST", "/api/companies/{id}/find-emails", "Web addresses, then fill by pattern"],
    ["POST", "/api/companies/{id}/verify-hiring", "Check hiring proof (?fresh)"],
    ["POST", "/api/companies/{id}/enrich", "Read the site and regrade (?fresh)"],
    ["GET", "/api/companies/{id}/dossier", "The research dossier (reports/<name>-dossier.md) as markdown — operator only; 404 when none is written"],
    ["GET", "/api/network", "The company atlas"],
  ]},
  { id: "roles", title: "Roles", rows: [
    ["GET", "/api/jobs", "Search roles: q, status, source, min_score, min_lpa, remote, sort, limit, offset"],
    ["GET / PATCH", "/api/jobs/{id}", "One role / set its status"],
    ["GET", "/api/roles", "Roles by selection odds: market, band, sort"],
    ["GET / POST", "/api/roles/{id}/brief", "Selection breakdown and needs brief / rebuild it"],
  ]},
  { id: "decide", title: "The decision brain", rows: [
    ["GET", "/api/decide", "The ranked shortlist: pool=Bengaluru|Hyderabad|Remote|all, or verdict=after_dsa|skip|… for parked rows; with counts"],
    ["GET", "/api/decide/today", "Today’s company — picked on the first read of the day"],
    ["POST", "/api/decide/today/{status}", "asked | skipped | filled (?note=); a skip picks the next company"],
    ["GET", "/api/decide/company/{id}", "Every question, its chance and evidence, for one company"],
    ["POST", "/api/decide/run", "Decide everything again (task)"],
    ["POST", "/api/decide/outcome", "Record what happened: ?company_id=&stage=replied|referred|interview|offer|rejected|no_response"],
    ["GET", "/api/decide/calibration", "Brier score and log loss against recorded outcomes, and the reply-rate posterior"],
  ]},
  { id: "mixer", title: "The mixer", rows: [
    ["GET", "/api/mixer/plan", "The free funnel, who would be bought next, the estimate and today’s Apify spend — spends nothing"],
    ["POST", "/api/mixer/run", "Buy the next companies inside the mixer’s share and the referral reserve (task; ?limit=)"],
    ["GET", "/api/mixer/history", "Every paid lookup"],
  ]},
  { id: "upskill", title: "Upskill", rows: [
    ["GET", "/api/upskill", "The gaps across the shortlist, ranked by expected offers gained, and the DSA outlook (?refresh)"],
    ["POST", "/api/upskill/{skill}", "{status: todo|learning|done, proof_url, proof_note} — done needs both"],
    ["POST", "/api/upskill/{skill}/add-to-profile", "Add a done skill to profile.json, then re-score (task)"],
  ]},
  { id: "people", title: "People", rows: [
    ["GET", "/api/leads", "The Leads page: source, city, who=ready|all, with_email"],
    ["GET", "/api/contacts", "All people: company_id, confidence, affiliation"],
    ["POST", "/api/contacts/{id}/research", "Research one person"],
  ]},
  { id: "outreach", title: "Drafts, review and sending", rows: [
    ["GET", "/api/emails", "Emails by status and kind"],
    ["POST", "/api/emails/draft · /draft-batch · /draft-bets", "Write drafts (tasks)"],
    ["POST", "/api/emails/{id}/approve", "Approve, optionally with an edited subject and body"],
    ["POST", "/api/emails/{id}/reject", "Reject a draft"],
    ["POST", "/api/outreach/emails/{id}/save · /unapprove", "Save edits / send back to review"],
    ["GET", "/api/outreach/review/count", "Drafts waiting (the sidebar badge)"],
    ["GET", "/api/outreach/pipeline", "Stage counts, sentiment, cap, can-send"],
    ["GET", "/api/outreach/ledger", "Today’s cap, used, guessed cap, send mode"],
    ["GET", "/api/outreach/preflight", "Live-send readiness"],
    ["POST", "/api/emails/{id}/send · /send-approved", "Send now, inside the cap"],
    ["GET / POST", "/api/outreach/proposals", "How each email would be written / regenerate"],
    ["GET", "/api/outreach/track · /thread/{id}", "Every email’s lifecycle / one conversation"],
  ]},
  { id: "replies", title: "Replies and calendar", rows: [
    ["GET", "/api/replies", "Replies by sentiment"],
    ["POST", "/api/replies/poll", "Read Gmail now (task)"],
    ["GET", "/api/tracker · /api/learn", "Sent matrix / what works"],
    ["GET", "/api/events", "Calls, interviews, assessments"],
    ["POST", "/api/events/{id}/confirm · /reschedule · /status", "Act on an event"],
  ]},
  { id: "lists", title: "Lists", rows: [
    ["GET", "/api/lists · /api/lists/{key}", "All lists / one list (★ India includes every BLR/HYD company that clears the guardrails)"],
    ["POST", "/api/lists/{key}/sync · /find-people", "Attach to the database / find referrers (task)"],
  ]},
  { id: "graph", title: "Knowledge graph", rows: [
    ["GET", "/api/graph/map", "The Graph page: view=market | pipeline | project"],
    ["GET", "/api/kg/search · /nodes/{id} · /path · /hubs", "Search, neighbours, paths, centrality"],
    ["GET", "/api/kg/brief", "BRIEF.md as text"],
    ["POST", "/api/kg/notes · /build · /compose", "Record a note / rebuild / compose designs"],
    ["GET", "/api/kg/warm/company/{id} · /similar/{id}", "Warm paths / similar companies"],
  ]},
  { id: "system", title: "System", rows: [
    ["GET", "/api/tasks · /api/tasks/{id}", "Background tasks and their results"],
    ["GET", "/api/health", "LLM, matcher, scheduler and research channels"],
    ["GET", "/api/audit", "Last hourly audit and 48 h of history"],
    ["GET", "/api/overview", "Dashboard counts"],
    ["GET / PATCH", "/api/config", "config.yaml with secrets stripped / write back safe sections"],
    ["GET", "/api/models · /api/models/costs", "OpenRouter status and spend"],
    ["GET / POST", "/api/gmail/*", "Gmail status, credentials, app password, verify, disconnect"],
    ["POST", "/api/pipeline/scrape · score · daily …", "Run a pipeline stage (task)"],
    ["GET", "/api/research/*", "Search, read, company research (operator only)"],
  ]},
];

export default function Api() {
  return (
    <Doc slug="api">
      <Lead>
        The dashboard is a client of a FastAPI server on <C>127.0.0.1:8000</C>. Everything it shows comes from these
        endpoints, and anything slow runs as a background task you can poll.
      </Lead>

      <H2>Conventions</H2>
      <Table
        head={["Convention", "Detail"]}
        rows={[
          ["Interactive docs", <>OpenAPI at <C key="d">http://127.0.0.1:8000/docs</C></>],
          ["Long jobs", <>Return <C key="t">{`{"task_id": "…"}`}</C>; poll <C key="p">GET /api/tasks/{"{id}"}</C> until status is done or error.</>],
          ["Public mode", <>With <C key="z">ZONULY_PUBLIC</C> set, every write is refused and operator areas (replies, config, Gmail, research, tasks) are closed. <C key="h">/api/health</C> stays open.</>],
          ["Operator key", <>An <C key="x">X-API-Key</C> header matching <C key="k">ZONULY_API_KEY</C> lifts the public restrictions.</>],
          ["CORS", "localhost dev ports, the LAN, and *.vercel.app; override with ZONULY_CORS_ORIGINS."],
        ]}
      />
      <Code title="example: fill one company and wait">{`
TASK=$(curl -s -X POST localhost:8000/api/companies/2802/fill | jq -r .task_id)
curl -s localhost:8000/api/tasks/$TASK | jq '.status, .result.why'
`}</Code>

      {AREAS.map((a) => (
        <section key={a.id}>
          <H2 id={a.id}>{a.title}</H2>
          <Table head={["Method", "Path", "Does"]} mono={[0, 1]} rows={a.rows} />
        </section>
      ))}
      <Callout kind="note">Routers are mounted from <C>api.py</C>, <C>outreach/routes.py</C>, <C>research/routes.py</C>, <C>kg/routes_warm.py</C> and <C>campaigns.py</C>. See also the <A href="/docs/cli">CLI</A>, which reaches the same functions.</Callout>
    </Doc>
  );
}
