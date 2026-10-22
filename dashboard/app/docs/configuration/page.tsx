import type { Metadata } from "next";
import { C, Callout, Doc, H2, H3, Lead, P, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Configuration" };

export default function Configuration() {
  return (
    <Doc slug="configuration">
      <Lead>
        Behaviour lives in <C>config.yaml</C>; secrets live in <C>.env</C>. The config is committed and contains no secrets;
        <C>.env</C> is git-ignored. Most values are read live — background jobs pick up a change on their next run; the API
        needs a restart.
      </Lead>

      <H2>config.yaml, section by section</H2>
      <H3>outreach — sending</H3>
      <Table
        head={["Key", "Value", "Meaning"]}
        mono={[0, 1]}
        rows={[
          ["send_mode", "gmail", "Live sending; the alternative writes .eml files to outbox/ (dry run)"],
          ["daily_send_cap", "25", "Emails per day, all kinds"],
          ["guessed_daily_cap", "25", "Of those, to pattern-guessed addresses"],
          ["send_window / send_days", "[10, 19] / mon–fri", "Local hours and days mail may leave"],
          ["pacing", "max_per_run 1 · front_load false · jitter 0.35", "One per run, spread across the window"],
          ["warmup", "first 25 · step 5 · every 2", "Quota = min(cap, first + step × (day ÷ every))"],
          ["bounce_brake", "window_days 7 · max_bounces 6", "Pause guessed addresses after too many bounces"],
          ["followup_after_days / max_followups", "5 / 1", "One follow-up after five quiet days"],
          ["asks_per_company · contact/company_cooldown_days", "2 · 30 / 30", "Open asks per company; days before asking again"],
          ["draft_expiry_days", "7", "Drafts older than this can’t be approved"],
          ["gate", "60–110 words · subject 2–8 words", "Review-flag thresholds"],
          ["compose", "structure direct_intro · 80–170 words", "Your template, its wordings and subjects"],
          ["pattern_approval", "false", "Every email needs its own approval"],
          ["do_not_contact", "emails · domains · companies", "Never written to, whatever the pipeline finds"],
        ]}
      />
      <H3>targeting — who is worth it</H3>
      <Table
        head={["Key", "Value", "Meaning"]}
        mono={[0, 1]}
        rows={[
          ["funding", "pre-seed → Series B · 30 months · ≤ $400M", "The funding window"],
          ["overrated", "valuation ≥ $5B or raised ≥ $1B", "Excluded; plus the named famous lists"],
          ["india.min_lpa", "25", "India pay floor (below → below_pay_bar)"],
          ["india.cities", "Bengaluru · Hyderabad", "The only India targets"],
          ["stipend_min_inr_month", "50000", "Internship stipend bar"],
          ["ppo_tier1_lpa / ppo_tier2_lpa", "30 / 30", "Tier thresholds"],
          ["priority.weights", "funding 35 · hiring 35 · pay 20 · valuation 10", "Priority score"],
          ["people.max_per_company", "30", "People kept per company"],
        ]}
      />
      <H3>tick, referrals, actors — the background work</H3>
      <Table
        head={["Key", "Value", "Meaning"]}
        mono={[0, 1]}
        rows={[
          ["tick.free_interval_seconds", "600", "Free tick every 10 minutes"],
          ["tick.budget_seconds · share", "2400 · leads 0.7 / companies 0.3", "Time budget per free tick and its split"],
          ["tick.paid_hour / paid_minute", "6 / 15", "Daily paid tick"],
          ["tick.fill_per_tick · fill_budget_seconds", "20 · 900", "Companies filled per tick"],
          ["tick.people_per_tick · verify_per_tick", "150 · 120", "Per-tick limits"],
          ["referrals.interval_seconds · cycle_seconds", "900 · 1200", "Referral job: every 15 min, 20 min each"],
          ["referrals.only_buckets", "[Bengaluru, Hyderabad]", "Cities the referral job works"],
          ["actors.apify.daily_usd · cache_hours", "0.15 · 24", "Apify spend per day (inside the free $5/month)"],
        ]}
      />
      <H3>openrouter, research, scoring</H3>
      <Table
        head={["Key", "Value", "Meaning"]}
        mono={[0, 1]}
        rows={[
          ["openrouter.aliases", "cheap · judge · writer", "Ranked chains of free models; a call falls through on failure"],
          ["openrouter caps", "18 req/min · ₹100/day · ₹2,500/month", "Checked before every call"],
          ["research.exa_daily_cap", "100", "Exa searches per day"],
          ["research.scrapedo_daily / monthly", "120 / 1000", "scrape.do budget"],
          ["research.x_session_enabled", "false", "Logged-in X search stays off"],
          ["matching.high_match_threshold", "65", "A high match"],
          ["confidence.bands", "high 65 · medium 48 · low 30", "Selection bands"],
          ["scheduler.scrape_hour / reply_poll_minutes", "8 / 60", "The API’s own scheduler"],
          ["api.host / api.port", "127.0.0.1 / 8000", "Where the API listens"],
        ]}
      />
      <Callout kind="note">
        Some sections are read with built-in defaults and are absent from the file: <C>guardrails</C> (description 60–600
        characters, hiring stale after 30 days, 3 referrers per company), <C>audit</C> and <C>fill</C> (refill after 7 days,
        30 once complete).
      </Callout>

      <H2>Environment variables</H2>
      <P>Read from the process environment, with <C>.env</C> at the repo root folded in first (values already set win). Names only — never commit values.</P>
      <Table
        head={["Variable", "Needed for"]}
        mono={[0]}
        rows={[
          ["OPENROUTER_API_KEY", "Every model call — required"],
          ["GMAIL_APP_PASSWORD", "Sending (SMTP) and reading replies (IMAP)"],
          ["GITHUB_TOKEN", "GitHub API at 5,000 / hour instead of 60"],
          ["EXA_API_KEY", "The metered Exa fallback"],
          ["APIFY_TOKEN", "Buying People tabs from Apify"],
          ["HUNTER_API_KEY", "Hunter.io pattern lookups"],
          ["SCRAPEDO_TOKEN", "scrape.do reads (levels.fyi)"],
          ["X_AUTH_TOKEN · X_CT0", "A throwaway X session — only with x_session_enabled"],
          ["ACTOR_PROXY", "Optional proxy for actor fetches"],
          ["ZONULY_PUBLIC · ZONULY_API_KEY", "Read-only public mode · the operator key that lifts it"],
          ["ZONULY_DB_PATH · ZONULY_CORS_ORIGINS · ZONULY_HOST · PORT", "Deployment overrides"],
        ]}
      />
      <Callout kind="warn" title="A known mismatch">
        The scrape.do backend&apos;s health check looks only for a variable named <C>Scrape_dog</C>, while the reader accepts{" "}
        <C>SCRAPEDO_TOKEN</C>. If scrape.do shows as unavailable in <C>research doctor</C>, that is why.
      </Callout>
    </Doc>
  );
}
