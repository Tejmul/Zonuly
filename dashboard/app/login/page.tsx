"use client";

/*  Sign in. One page for signing up and signing in — the first successful sign-in creates
 *  the account. Two ways: a 6-digit code emailed from tejmul.in, or Google.
 */

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { googleHref, useMe } from "@/lib/auth";
import { Logo } from "@/components/logo";
import { Button, Input } from "@/components/ui/primitives";

type Start = { challenge: string; email: string; dev_code?: string };

function safeNext(raw: string | null) {
  return raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : "/dashboard";
}

function LoginForm() {
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const signup = params.get("mode") === "signup";
  const { data: me } = useMe();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState<Start | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(params.get("error"));

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<Start>("/api/auth/email/start", { email });
      setSent(r);
      if (r.dev_code) setCode(r.dev_code); // local only: the API never returns it when deployed
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    if (!sent) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/email/verify", { challenge: sent.challenge, code });
      window.location.assign(next); // a full load, so every page re-asks with the new session
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const google = me?.methods.google ?? false;
  const emailOn = me?.methods.email ?? true;

  return (
    <div className="w-full max-w-[380px]">
      <Logo size={18} />
      <h1 className="mt-8 font-[family-name:var(--font-display)] text-[26px] leading-tight font-semibold tracking-[-0.03em]">
        {sent ? "Check your email" : signup ? "Create your ZoNuLy account" : "Sign in to ZoNuLy"}
      </h1>
      <p className="mt-2 text-[14px] leading-relaxed text-ink-2">
        {sent
          ? `We sent a 6-digit code to ${sent.email}. It works for 10 minutes.`
          : signup
            ? "Use Google or your email. We'll send a code to prove it's you. No password needed."
            : "Welcome back. Use Google or get a code by email."}
      </p>

      {me?.user && !sent && (
        <p className="mt-6 text-[13px] text-ink-2">
          You&rsquo;re signed in as {me.user.email}.{" "}
          <Link href={next} className="text-ink underline underline-offset-2">Continue</Link>
        </p>
      )}

      {!sent && google && (
        <>
          <Button asChild variant="default" className="mt-8 h-10 w-full">
            <a href={googleHref(next)}>Continue with Google</a>
          </Button>
          {emailOn && (
            <div className="my-6 flex items-center gap-3 text-[11px] tracking-[0.08em] text-ink-3 uppercase">
              <span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" />
            </div>
          )}
        </>
      )}

      {!sent && emailOn && (
        <form onSubmit={sendCode} className={`flex flex-col gap-3 ${google ? "" : "mt-8"}`}>
          <label className="text-[12px] text-ink-2" htmlFor="email">Email</label>
          <Input
            id="email" type="email" autoComplete="email" required autoFocus
            placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)}
            className="h-10"
          />
          <Button type="submit" variant="primary" className="h-10" disabled={busy || !email}>
            {busy ? "Sending…" : "Email me a code"}
          </Button>
        </form>
      )}

      {sent && (
        <form onSubmit={verify} className="mt-8 flex flex-col gap-3">
          <label className="text-[12px] text-ink-2" htmlFor="code">6-digit code</label>
          <Input
            id="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus maxLength={6}
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className="h-11 text-center font-mono text-[20px] tracking-[0.4em]"
          />
          <Button type="submit" variant="primary" className="h-10" disabled={busy || code.length !== 6}>
            {busy ? "Checking…" : "Sign in"}
          </Button>
          <button
            type="button" className="text-left text-[12.5px] text-ink-2 hover:text-ink"
            onClick={() => { setSent(null); setCode(""); setError(null); }}
          >
            Use a different email
          </button>
        </form>
      )}

      {error && <p role="alert" className="mt-4 text-[13px] text-[var(--bad-ink)]">{error}</p>}

      {!sent && (
        <p className="mt-6 text-[13px] text-ink-2">
          {signup ? "Already have an account? " : "New to ZoNuLy? "}
          <Link
            href={`/login?${signup ? "" : "mode=signup&"}next=${encodeURIComponent(next)}`}
            className="text-ink underline underline-offset-2"
          >
            {signup ? "Sign in" : "Create an account"}
          </Link>
        </p>
      )}

      <p className="mt-10 text-[12px] text-ink-3">
        <Link href="/" className="hover:text-ink">← Back to the home page</Link>
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-surface px-4 py-16">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
