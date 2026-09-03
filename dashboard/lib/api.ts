"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

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
  is_recruiter: boolean;
  research: { hook?: string; shared_ground?: string; notes?: string; repos?: unknown[] } | null;
  researched_at: string | null;
  created_at: string | null;
};

export type Email = {
  id: number;
  contact_id: number;
  company_id: number;
  job_id: number | null;
  contact_name: string | null;
  contact_role: string | null;
  contact_confidence: string | null;
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
  job?: Job | null;
  replies?: Reply[];
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
  contacts: { total: number; verified: number };
  top_matches: Job[];
  recent_replies: Reply[];
};

export type Health = {
  ok: boolean;
  llm: { ok: boolean; model: string; model_present: boolean; embed_present: boolean; error?: string };
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      "Can't reach the API. Start it with `python scripts/run.py serve`.",
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

export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const result = await api.get<T>(path);
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    alive.current = true;
    void reload();
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  return { data, error, loading, reload };
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
