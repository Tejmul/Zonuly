"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// On Vercel the build sets NEXT_PUBLIC_API_PROXY to "/zapi", a same-origin proxy to the
// production API (next.config.ts); elsewhere the explicit base or the local API.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_PROXY || process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

/* ---------------------------------------------------------------- types */

export type Job = {
  id: number;
  company_id: number | null;
  company_name: string;
  title: string;
  location: string | null;
  remote: boolean;
  url: string;
  source: string;
  posted_at: string | null;
  scraped_at: string | null;
  salary_min_lpa: number | null;
  salary_max_lpa: number | null;
  salary_raw: string | null;
  currency: string | null;
  match_score: number | null;
  embed_sim: number | null;
  status: string;
  reasons: string[];
  verdict: string | null;
  breakdown: Record<string, number>;
  gaps: string[];
  description?: string;
  company?: Company | null;
  contacts?: Contact[];
};

export type Company = {
  id: number;
  name: string;
  website: string | null;
  domain: string | null;
  github_org: string | null;
  email_pattern: string | null;
  notes?: string | null;
  ats?: string | null;
  jobs?: number;
  contacts?: number;
  best_score?: number | null;
  contacts_found_at: string | null;
};

export type Contact = {
  id: number;
  company_id: number;
  company_name: string | null;
  name: string | null;
  role: string | null;
  email: string | null;
  github: string | null;
  linkedin: string | null;
  source: string;
  confidence: string;
  /** Does the evidence place them there now? verified-current | likely-current | unconfirmed | generic */
  affiliation?: string | null;
  affiliation_evidence?: string | null;
  email_kind?: string | null;
  handle?: string | null;
  is_recruiter: boolean;
  research: { hook?: string; shared_ground?: string; notes?: string; repos?: unknown[] } | null;
  researched_at: string | null;
  created_at: string | null;
};

/** GET /api/leads — the People page: one referrer, where they came from and where the
 *  company is. `email` is masked on the public instance. */
export type LeadSource = "linkedin" | "github" | "team" | "talks" | "web";
export type Lead = Contact & {
  channel: LeadSource;
  city: string | null;
  /** Bengaluru | Hyderabad | India | Remote — the referral job's queue bucket */
  bucket: string | null;
  company_star: boolean;
  found_at: string | null;
};
export type LeadsPayload = {
  items: Lead[];
  total: number;
  offset: number;
  limit: number;
  counts: { total: number; linkedin: number; with_email: number; by_source: Record<LeadSource, number> };
  source: string;
  city: string;
  who: string;
};

export type Email = {
  id: number;
  contact_id: number;
  company_id: number;
  job_id: number | null;
  contact_name: string | null;
  contact_role: string | null;
  contact_confidence: string | null;
  contact_affiliation?: string | null;
  company_name: string | null;
  to_email: string;
  subject: string;
  body: string;
  kind: string;
  status: string;
  error: string | null;
  gmail_thread_id: string | null;
  created_at: string | null;
  approved_at: string | null;
  sent_at: string | null;
  followup_sent: boolean;
  review_flags?: { kind: string; detail: string }[];
  evidence?: {
    person?: { evidence?: string | null; hook?: string | null; shared_ground?: string | null; repos?: string | null };
    company?: Record<string, { text: string; source: string | null }>;
    role?: { title?: string | null; where?: string | null; why_it_fits?: string | null };
  } | null;
  expires_at?: string | null;
  stale?: boolean;
  address_confidence?: string | null;
  candidate_id?: number;
  job?: Job | null;
  replies?: Reply[];
  // the composer's record (outreach/compose.py)
  structure?: string | null;
  subject_pattern?: string | null;
  hook_type?: string | null;
  words?: number | null;
  quality?: number | null;
  quality_detail?: Quality | null;
  writer?: string | null;
  polish_pending?: boolean | null;
  contract?: Contract | null;
};

/* ---------------------------------------------------------------- the composer (outreach/compose.py) */

export type QualityPart = { score: number; of: number; detail: string };
export type Quality = { score: number; verdict: "good" | "ok" | "rewrite"; parts: Record<string, QualityPart> };
export type Hook = { type: string; sentence: string; anchor: string; source: string; specificity: number };
export type NeedProof = {
  need: string | null; what: string; kind: string | null; status: string | null; quote: string | null;
  url: string | null; proof: string; topic: string | null; proof_source: string | null; resume_line: string;
};
export type Contract = {
  first_name: string | null; full_name: string | null; recipient_role: string | null; role_class: string | null;
  company: string | null; target_role: string | null; target_role_full: string | null; target_role_url: string | null;
  hook: Hook | null; hook_alternatives: Hook[]; need: NeedProof | null; proofs: NeedProof[];
  resume_link: string | null; structure: string | null; structure_why: string | null; small: boolean;
  missing: string[]; missing_labels: string[];
};
export type NotClaimed = { kind: string; what: string; quote: string | null; url: string | null };

export type Proposal = {
  id: number; run_id: string; created_at: string; rank: number; complete: boolean; missing: string[];
  market: string;
  recipient: { name: string | null; first_name: string | null; role: string | null; role_class: string | null;
    email: string | null; address: string | null; affiliation: string | null; affiliation_evidence: string | null;
    profile: string | null };
  company: { id: number | null; name: string | null; website: string | null; description: string | null;
    funding: string | null; team_size: number | null; list: string | null; small: boolean };
  role: { id: number | null; title: string | null; url: string | null; location: string | null;
    remote_anywhere: boolean | null; selection_score: number | null };
  structure: { id: string; label?: string; for?: string; why?: string; chosen_because: string | null };
  hook: Hook | null; hook_alternatives: Hook[] | null; needs: NeedProof[] | null; not_claimed: NotClaimed[] | null;
  subjects?: { pattern: string; subject: string }[]; subject?: string; subject_pattern?: string;
  body?: string; message?: string; words?: number; quality?: Quality; flags?: { kind: string; detail: string }[];
  writer?: string; model_note?: string | null; template_body?: string; template_quality?: number;
  model_body?: string | null; model_quality?: number | null;
  followup?: { subject: string; body: string; after_days: number; new_value: boolean };
};
export type Proposals = {
  run_id: string | null; created_at: string | null; items: Proposal[];
  summary: { complete: number; incomplete: number; mean_quality: number | null;
    structures: Record<string, number>; markets: Record<string, number> };
};

/* ---------------------------------------------------------------- tracking (outreach/tracker.py) */

export type TrackRow = {
  email_id: number; company_id: number; company_name: string | null; contact_name: string | null;
  contact_email: string; contact_role: string | null; job_title: string | null; job_id: number | null;
  subject: string; status: string; created_at: string | null; approved_at: string | null; sent_at: string | null;
  structure: string | null; quality: number | null; quality_verdict: string | null; writer: string | null;
  address_confidence: string | null;
  stage: string; sentiment: string | null; days_waiting: number | null; followup_due: string | null;
  followups: number; reply_snippet: string | null; replied_at: string | null;
};
export type Track = { counts: Record<string, number>; rows: TrackRow[] };
export type ThreadMessage = {
  type: "ours" | "theirs"; email_id: number; kind?: string; status?: string; to?: string; subject?: string;
  body: string; at: string | null; sent?: boolean; error?: string | null; reply_id?: number; from?: string;
  sentiment?: string | null; reason?: string | null; next?: string | null;
};
export type Thread = {
  email_id: number; company_name: string | null; contact_name: string | null; contact_role: string | null;
  to: string; job_title: string | null; job_url: string | null; structure: string | null;
  subject_pattern: string | null; hook_type: string | null; quality: Quality | null; contract: Contract | null;
  review_flags: { kind: string; detail: string }[] | null; writer: string | null; thread_id: string | null;
  lifecycle: { stage: string; sentiment: string | null; days_waiting: number | null; followup_due: string | null;
    followups: number; reply_snippet: string | null; replied_at: string | null };
  messages: ThreadMessage[];
};

/* ---------------------------------------------------------------- selection confidence (confidence.py / needs.py) */

export type SelectionBand = "high" | "medium" | "low" | "long_shot";
export type Market = "india" | "remote" | "abroad";

export type RoleRow = {
  id: number;
  title: string;
  company_id: number | null;
  company_name: string;
  location: string | null;
  url: string;
  source: string;
  posted_at: string | null;
  scraped_at: string | null;
  salary_min_lpa: number | null;
  salary_max_lpa: number | null;
  salary_raw: string | null;
  market: Market | null;
  score: number | null;
  band: SelectionBand | null;
  band_label: string | null;
  city: string | null;
  top_reasons: string[];
  main_blocker: string | null;
  cap: number | null;
  has_brief: boolean;
  synthesis: string | null;
  match_score: number | null;
  remote_anywhere: boolean;
};

export type SelectionTerm = {
  key: string; label: string; weight: number; value: number; points: number; why: string;
  quote: string | null; url: string | null;
};
export type SelectionBlocker = { kind: string; cap: number; why: string; quote: string | null; url: string | null };
export type SelectionWhy = {
  market: Market; score: number; raw: number; band: SelectionBand; band_label: string; cap: number | null;
  city: string | null; terms: SelectionTerm[]; blockers: SelectionBlocker[]; top_reasons: string[];
  main_blocker: string | null; notes: string[]; bands: Record<string, number>;
};

export type NeedItem = {
  text: string; kind: "stated" | "implied" | "building"; section: string; weight: number;
  quote: string; url: string | null; source: string; skills: string[]; trait: string | null;
  status: "covered" | "partial" | "uncovered"; evidence: string | null; evidence_where: string | null;
  covered_skills: string[]; missing_skills: string[]; evidence_skills?: string[];
  more_evidence?: { id: string; text: string; where: string; skill: string }[];
};
export type BuildingItem = { text: string; quote: string; url: string | null; source: string; published?: string | null };
export type NeedsBrief = {
  company: string; title: string; url: string;
  experience: { min_years: number | null; max_years: number | null; fresher: boolean; quote: string | null; fresher_quote: string | null };
  stated: NeedItem[]; implied: NeedItem[]; building: BuildingItem[];
  coverage: { share: number | null; covered: number; partial: number; uncovered: number; total: number };
  forbidden: string[]; helps: string | null; helps_model?: string; angles: string[];
  synthesis: string; stored?: boolean; built_at: string;
  model?: { building?: string; implied?: { need: string; quote: string; url: string | null; source: string }[]; rejected?: string[] };
  context?: { web: number; repos: number; errors: string[]; at: string | null };
};
export type RoleBrief = { role: RoleRow; selection: SelectionWhy | null; brief: NeedsBrief; brief_at: string | null };
export type RolesPage = {
  total: number; items: RoleRow[]; bands: Record<string, number>;
  counts: Record<string, Partial<Record<SelectionBand, number>>>;
};

export type EventRow = {
  id: number; kind: string; status: string; company: string | null; company_id: number;
  contact: string | null; email_id: number; starts_at: string | null; ends_at: string | null;
  local: string | null; timezone: string | null; link: string | null; deadline: string | null;
  quote: string | null; needs_action: string | null; conflict_with: number | null;
  calendar_event_id: string | null; created_at: string;
};

export type Reply = {
  id: number;
  email_id: number;
  from_addr: string;
  body: string;
  sentiment: string | null;
  sentiment_reason: string | null;
  received_at: string | null;
  company_name?: string | null;
  contact_name?: string | null;
  subject?: string | null;
};

export type TrackerRow = {
  email_id: number;
  company_id: number;
  company_name: string | null;
  contact_name: string | null;
  contact_email: string;
  contact_confidence: string | null;
  job_title: string | null;
  job_id: number | null;
  match_score: number | null;
  kind: string;
  status: string;
  sent_at: string | null;
  followup_sent: boolean;
  reply_sentiment: string | null;
  reply_count: number;
};

export type Quota = {
  daily_cap: number;
  sent_today: number;
  remaining_today: number;
  send_window: [number, number];
  in_window: boolean;
  gmail: { configured: boolean; authorized: boolean; hint: string };
};

export type Overview = {
  jobs: { jobs: number; embedded: number; scored: number; high_match: number };
  funnel: Record<string, number>;
  quota: Quota;
  by_source: Record<string, number>;
  new_this_week: number;
  companies: number;
  contacts: { total: number; verified: number; by_confidence?: Record<string, number>; by_affiliation?: Record<string, number> };
  top_matches: Job[];
  recent_replies: Reply[];
};

export type Health = {
  ok: boolean;
  llm: { ok: boolean; model: string; model_present: boolean; embed_present: boolean; error?: string; hint?: string };
  gmail: { configured: boolean; authorized: boolean; hint: string };
  quota: Quota;
  hunter: { configured: boolean; lookups_left: number };
  github_token: boolean;
  profile: { exists: boolean; path: string };
  counts: { jobs: number; embedded: number; scored: number; high_match: number };
  scheduler: {
    running: boolean;
    jobs: { id: string; name: string; next_run: string | null }[];
    last_runs: Record<string, { at: string; result: unknown; error: string | null }>;
  };
  /** Absent on older backends, so every read of it is optional. */
  access?: { public: boolean; read_only: boolean; operator_key_set: boolean };
  /** Which database build the API is serving. Newer backends only; tolerate absence. */
  snapshot?: {
    built_at?: string | null;
    git_sha?: string | null;
    schema_ok?: boolean;
    lists?: Record<string, number>;
  } | null;
};

/** GET /api/audit — the hourly scrape audit (jobhunter/audit.py) and its 48-hour history.
 *  Every block is optional: an older backend, or an audit that has not run yet. */
export type ChannelRow = {
  hour: { ok?: number | null; fail?: number | null; cooldowns?: number | null; fail_rate?: number | null };
  day: { ok?: number | null; fail?: number | null; cooldowns?: number | null; fail_rate?: number | null };
  last_ok_at?: string | null;
  last_fail_at?: string | null;
  last_error?: string | null;
};
export type AuditFill = {
  companies: number;
  avg_completeness: number;
  counts: Record<string, number>;
  rates: Record<string, number>;
};
export type AuditReport = {
  at: string;
  status: "ok" | "alert";
  alerts: string[];
  fill: AuditFill;
  delta_hour?: Record<string, number> | null;
  delta_day?: Record<string, number> | null;
  labels?: { affiliation?: Record<string, number>; address?: Record<string, number>; email_kind?: Record<string, number> };
  last_hour: {
    companies_added: number; contacts_added: number; roles_added: number; contacts_relabelled?: number;
    fields_written: number; companies_completed?: number; writes_by_field?: Record<string, number>;
  };
  rejects: { total: number; by_reason: Record<string, number>; by_channel: Record<string, number> };
  rejects_24h?: number;
  sample?: { checked: number; passed: number; failed: { id: number; company_id: number | null; field: string; value: string; why: string; channel: string | null }[] };
  channels: {
    research?: Record<string, ChannelRow>;
    openrouter?: { ok: number; fail: number; fail_rate?: number | null; daily_ceiling_hit?: boolean; last_ok_at?: string | null; last_fail_at?: string | null; last_error?: string | null };
    github_budget?: { limit?: number | null; remaining?: number | null; resets?: string | null; error?: string };
    tools?: Record<string, { status: string; detail: string }>;
    exa_mcp_cooldowns_hour?: number;
  };
  tick?: { ran_last_hour: boolean; last: { at?: string | null; finished?: string | null } | null };
  referrals?: AuditReferrals;
};
/** The referrals block of the hourly audit (jobhunter/referrals.py audit_block). */
export type AuditReferrals = {
  error?: string;
  ready_added?: number;
  ready_added_with_email?: number;
  addresses_found?: { by_pattern: number; by_source: Record<string, number> };
  companies_gained_email?: number | null;
  companies_lost_email?: number | null;
  companies_with_email?: number;
  companies_became_complete?: number;
  people_runs?: { companies: number; linkedin_down: string | null };
  /** 2026-10-14: ready referrers added this hour by channel (linkedin | github | team | talks | web) */
  ready_by_channel?: Record<string, number>;
  linkedin_added?: number;
  emails_found?: number;
  companies_reached_target?: number;
  /** the referral job (com.zonuly.referrals) this hour */
  job?: { cycles: number; companies: number; added_ready: number; emails_found: number; reached_target: number; stopped: string | null };
  reverify?: { confirmed: number; downgraded: number; inconclusive: number; affiliation_rejects: number };
  revalidate?: { checked: number; passed: number; newly_failed: number; recovered: number };
};
/** GET /api/companies/filling — companies not yet complete, with what each is missing. */
export type FillingRow = {
  id: number; name: string; website: string | null; description: string | null;
  city: string | null; region: string | null; list_key: string | null; list_rank: number | null;
  priority_score: number | null; missing: string[]; recheck_failed: Record<string, string>;
  ready_referrers: number; ready_with_email: number; hiring_status: string | null;
};
export type FillingPayload = { companies: FillingRow[]; total: number; missing_counts: Record<string, number> };
export type AuditPayload = {
  last: AuditReport | null;
  history: { at: string; status: string; alerts: string[]; fill: AuditFill; last_hour: AuditReport["last_hour"]; rejects: { total: number } }[];
  agent?: { label: string; every_minutes: number };
};

/** GET /api/outreach/pipeline — the send pipeline end to end, counts first. On the
 *  public instance `recent` is empty: the counts are public, the people are not. */
export type OutreachPipeline = {
  stages: Partial<Record<"drafted" | "in_review" | "approved" | "rejected" | "sent" | "failed" | "bounced" | "replied", number>>;
  sentiment: Partial<Record<"positive" | "neutral" | "negative" | "closed", number>>;
  meetings?: number;
  today?: { cap?: number; used?: number; guessed_cap?: number | null; guessed_used?: number | null };
  send_mode: string;
  gmail?: { connected: boolean; method: string | null; last_poll_at: string | null; last_poll_error: string | null };
  n8n?: { configured: boolean };
  recent?: {
    email_id: number; company_name: string | null; contact_name: string | null; status: string;
    sent_at: string | null; reply_sentiment: string | null; reply_at: string | null;
  }[];
};

export type GmailStep = {
  id: "address" | "twostep" | "password" | "verify" | "project" | "consent" | "client" | "authorize" | "mode";
  done: boolean;
  label: string;
  problem?: string | null;
};

/** GET /api/gmail/status — the whole Settings → Email setup flow. */
export type GmailSetup = {
  /** app_password: SMTP/IMAP with a Gmail app password; oauth: the Gmail API client. */
  auth: "app_password" | "oauth";
  configured: boolean;
  authorized: boolean;
  /** outreach.user_name / user_email from config.yaml — what the mail says it is from */
  display_name: string | null;
  address_configured: string | null;
  password_present?: boolean;
  credentials_file?: string;
  token_file?: string;
  client_present?: boolean;
  client_problem?: string | null;
  client_id_hint?: string | null;
  token_present?: boolean;
  token_stale?: boolean;
  /** the address Google actually accepted, once it has */
  address: string | null;
  reply_to: string | null;
  send_mode: "dryrun" | "gmail";
  calendar_enabled: boolean;
  steps: GmailStep[];
  next_step: GmailStep["id"] | null;
  quota: Quota;
};

export type Task = {
  id: string;
  name: string;
  status: "running" | "done" | "error";
  started: string;
  finished?: string;
  result?: unknown;
  error?: string;
};

/* ---------------------------------------------------------------- fetch */

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/* A hosted backend that scales to zero (Catalyst AppSail) answers its first request after
   a quiet spell with a gateway error that carries no CORS header, which the browser
   reports as a network failure. Reads against a remote API therefore get two more tries
   (2 s, then 5 s) before the page says it cannot reach it; writes are never retried, and
   a request the page has already abandoned is not either. */
const WAKE_RETRIES_MS = [2000, 5000];
const sleep = (ms: number, signal?: AbortSignal | null) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
  });

async function fetchWaking(url: string, init: RequestInit): Promise<Response> {
  const local = API_BASE.includes("127.0.0.1") || API_BASE.includes("localhost");
  const retry = !local && (init.method ?? "GET").toUpperCase() === "GET";
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, init);
      if (retry && attempt < WAKE_RETRIES_MS.length && [502, 503, 504].includes(res.status)) {
        await sleep(WAKE_RETRIES_MS[attempt], init.signal);
        continue;
      }
      return res;
    } catch (e) {
      if (init.signal?.aborted || !retry || attempt >= WAKE_RETRIES_MS.length) throw e;
      await sleep(WAKE_RETRIES_MS[attempt], init.signal);
    }
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetchWaking(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch (e) {
    if (init?.signal?.aborted) throw e;
    // Name the base it tried. On a deployed page, a local base means the build had no
    // NEXT_PUBLIC_API_BASE — the page is asking the viewer's own laptop for the API.
    const local = API_BASE.includes("127.0.0.1") || API_BASE.includes("localhost");
    const here = typeof window !== "undefined" ? window.location.hostname : "";
    const deployed = here !== "" && !/^(localhost|127\.0\.0\.1)$/.test(here);
    throw new ApiError(
      local && deployed
        ? `This deployed page is looking for the API at ${API_BASE} — on your own machine. ` +
          "Set NEXT_PUBLIC_API_BASE to the deployed API's URL in the hosting project's environment and redeploy (it is baked in at build time)."
        : local
          ? `Can't reach the API at ${API_BASE}. Start it with \`python scripts/run.py serve\`.`
          : `Can't reach the API at ${API_BASE} after three tries. It may still be waking up — reload in a minute. ` +
            `If it keeps failing, it is down or refuses this page's origin (` +
            `${typeof window !== "undefined" ? window.location.origin : "this origin"}; set ZONULY_CORS_ORIGINS on the API).`,
      0,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* response wasn't JSON */
    }
    throw new ApiError(String(detail), res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

/* ---------------------------------------------------------------- hooks */

type Slot<T> = { key: string | null; data: T | null; error: string | null; status: number | null };

/** GET a path and keep it fresh.
 *
 *  Two things this used to get wrong. A slow response for an old path could land after
 *  a fast one for the new path and overwrite it — the table showed the previous filter's
 *  rows under the new filter's heading. And it set loading state synchronously inside an
 *  effect, which React 19 flags as a cascading render. Now every request is numbered and
 *  aborted when superseded, only the latest may write, and "loading" is derived: the
 *  page is loading whenever the result it holds is not for the request it last made. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const key = path === null ? null : [path, ...deps.map(String)].join("\u0000");
  const [slot, setSlot] = useState<Slot<T>>({ key: null, data: null, error: null, status: null });
  const [refreshing, setRefreshing] = useState(false);
  const seq = useRef(0);
  const inflight = useRef<AbortController | null>(null);

  const run = useCallback((p: string, k: string): Promise<void> => {
    inflight.current?.abort();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const id = ++seq.current;
    return request<T>(p, { signal: ctrl.signal })
      .then(
        (data) => {
          if (id === seq.current) setSlot({ key: k, data, error: null, status: 200 });
        },
        (e: unknown) => {
          // A superseded or unmounted request says nothing about the current one.
          if (id !== seq.current || ctrl.signal.aborted) return;
          setSlot((prev) => ({
            key: k,
            data: prev.data,
            error: e instanceof Error ? e.message : String(e),
            status: e instanceof ApiError ? e.status : 0,
          }));
        },
      )
      .finally(() => {
        if (id === seq.current) setRefreshing(false);
      });
  }, []);

  useEffect(() => {
    if (path === null || key === null) return;
    void run(path, key);
    return () => inflight.current?.abort();
  }, [path, key, run]);

  const reload = useCallback(() => {
    if (path === null || key === null) return Promise.resolve();
    setRefreshing(true);
    return run(path, key);
  }, [path, key, run]);

  const loading = key !== null && (slot.key !== key || refreshing);
  return { data: slot.data, error: slot.key === key ? slot.error : null, status: slot.status, loading, reload };
}

/**
 * Poll a background task until it settles. Scrapes and LLM passes take minutes,
 * so the UI dispatches and watches rather than holding a request open.
 */
export function useTaskWatcher() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const watching = useRef(new Set<string>());

  const watch = useCallback((taskId: string, onDone?: (task: Task) => void) => {
    if (watching.current.has(taskId)) return;
    watching.current.add(taskId);

    const tick = async () => {
      try {
        const task = await api.get<Task>(`/api/tasks/${taskId}`);
        setTasks((prev) => [task, ...prev.filter((t) => t.id !== task.id)].slice(0, 8));
        if (task.status === "running") {
          setTimeout(tick, 2000);
        } else {
          watching.current.delete(taskId);
          onDone?.(task);
        }
      } catch {
        watching.current.delete(taskId);
      }
    };
    void tick();
  }, []);

  const dismiss = useCallback((id: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { tasks, watch, dismiss };
}
