"use client";

/*  Who is signed in, as the page sees it.
 *
 *  The API enforces the gate (jobhunter/access.py answers 401 to a request without a
 *  valid session); this only keeps the page from showing a dashboard full of errors to
 *  someone who has not signed in, by sending them to /login first.
 */

import { API_BASE, api, useApi } from "@/lib/api";

export type Me = {
  auth_required: boolean;
  user: { id: string; email: string } | null;
  methods: { email: boolean; google: boolean };
  turnstile_site_key: string | null;
};

export function useMe() {
  return useApi<Me>("/api/auth/me");
}

export function loginHref(next?: string) {
  const here = next ?? (typeof window !== "undefined" ? window.location.pathname + window.location.search : "/dashboard");
  return `/login?next=${encodeURIComponent(here)}`;
}

export function googleHref(next: string) {
  return `${API_BASE}/api/auth/google/start?next=${encodeURIComponent(next)}`;
}

export async function signOut() {
  try {
    await api.post("/api/auth/logout");
  } finally {
    window.location.assign("/");
  }
}
