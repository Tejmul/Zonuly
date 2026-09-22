"use client";

import { useRef, useState } from "react";
import { Check, ExternalLink, KeyRound, ShieldCheck, Unplug, Upload } from "lucide-react";
import { api, useApi, type GmailSetup, type GmailStep, type Task } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge, Button, ErrorNote, Loading, Panel, PanelHead } from "@/components/ui/primitives";

/**
 * Settings → Email. The whole path from "no credentials" to "mail goes out as this
 * name and address", as numbered steps with the state read from the API — not from
 * memory. Nothing here sends anything: the last step only changes where an
 * *approved* email goes (outbox/ on disk, or Gmail).
 *
 * 2026-09-22: the default path is a Gmail app password (SMTP + IMAP) — no Google
 * Cloud project, no consent screen, no token that expires. The OAuth steps remain
 * for `gmail.auth: oauth`, which the Calendar push still needs.
 */
export function EmailSetup({ watch }: { watch: (taskId: string, onDone?: (t: Task) => void) => void }) {
  const setup = useApi<GmailSetup>("/api/gmail/status");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const s = setup.data;

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await setup.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function savePassword() {
    const pw = password;
    await run("password", async () => {
      await api.post("/api/gmail/app-password", { password: pw });
      await api.post("/api/gmail/verify");
    });
    setPassword("");
  }

  function verify() {
    void run("verify", () => api.post("/api/gmail/verify"));
  }

  async function uploadClient(file: File) {
    await run("upload", async () => {
      let client: unknown;
      try {
        client = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} is not valid JSON.`);
      }
      await api.post("/api/gmail/credentials", { client });
    });
    if (fileRef.current) fileRef.current.value = "";
  }

  function authorize() {
    void run("authorize", async () => {
      const { task_id } = await api.post<{ task_id: string }>("/api/gmail/authorize");
      await new Promise<void>((resolve) =>
        watch(task_id, (t) => {
          if (t.status === "error") setError(t.error ?? "Authorization failed.");
          resolve();
        }),
      );
    });
  }

  function setMode(mode: "dryrun" | "gmail") {
    void run("mode", () => api.patch("/api/config", { outreach: { send_mode: mode } }));
  }

  if (setup.loading && !s) return <Loading label="Checking email" />;
  if (setup.error && !s) return <ErrorNote message={setup.error} onRetry={setup.reload} />;
  if (!s) return null;

  const step = (id: GmailStep["id"]) => s.steps.find((x) => x.id === id)!;
  const quota = s.quota;
  const who = s.display_name && s.address_configured ? `${s.display_name} <${s.address_configured}>` : s.address_configured ?? "not set";

  return (
    <Panel className="xl:col-span-2">
      <PanelHead
        eyebrow="Email"
        title="Who the mail goes out as"
        action={
          <span className="flex items-center gap-2">
            <Badge className={s.send_mode === "gmail" ? "text-ink" : "text-ink-3"}>
              {s.send_mode === "gmail" ? "live · Gmail" : "dry-run · outbox/"}
            </Badge>
            <span className="tnum text-xs text-ink-3">
              {quota.sent_today}/{quota.daily_cap} sent today
            </span>
          </span>
        }
      />

      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_260px]">
        {s.auth === "app_password" ? (
          <ol className="flex flex-col divide-y divide-line">
            {/* 1 — identity */}
            <Step n={1} step={step("address")}>
              <p>
                Every email is signed and sent as <span className="font-mono text-ink">{who}</span>. Both come
                from <code className="font-mono text-ink-2">outreach.user_name</code> and{" "}
                <code className="font-mono text-ink-2">outreach.user_email</code> in config.yaml — change them
                there and restart the API to send as someone else.
              </p>
            </Step>

            {/* 2 — 2-step verification */}
            <Step n={2} step={step("twostep")}>
              <p>
                Signed in to Google as that address, turn on <strong>2-Step Verification</strong>. Google only
                offers app passwords on accounts that have it.
              </p>
              <Links links={[["2-Step Verification", "https://myaccount.google.com/signinoptions/two-step-verification"]]} />
            </Step>

            {/* 3 — app password */}
            <Step n={3} step={step("password")}>
              <p>
                Create an app password (name it anything — <code className="font-mono text-ink-2">ZoNuLy</code>),
                copy the 16 letters Google shows once, and paste them here. It is written to{" "}
                <code className="font-mono text-ink-2">.env</code> (gitignored, mode 600) and never shown again.
              </p>
              <Links links={[["App passwords", "https://myaccount.google.com/apppasswords"]]} />
              <form
                className="mt-2 flex flex-wrap items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (password.replace(/\s+/g, "").length === 16) void savePassword();
                }}
              >
                <label htmlFor="gmail-app-password" className="sr-only">App password</label>
                <input
                  id="gmail-app-password"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="xxxx xxxx xxxx xxxx"
                  className="h-8 w-52 rounded-sm border border-line bg-surface px-2 font-mono text-[12px] tracking-[0.08em] placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
                />
                <Button
                  type="submit"
                  size="sm"
                  variant={s.password_present ? "quiet" : "default"}
                  disabled={busy !== null || password.replace(/\s+/g, "").length !== 16}
                >
                  <KeyRound size={12} />
                  {busy === "password" ? "Saving…" : s.password_present ? "Replace app password" : "Save app password"}
                </Button>
                {s.password_present ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="quiet"
                    disabled={busy !== null}
                    onClick={() => void run("disconnect", () => api.post("/api/gmail/disconnect"))}
                  >
                    <Unplug size={12} />
                    Forget it
                  </Button>
                ) : null}
              </form>
            </Step>

            {/* 4 — verify */}
            <Step n={4} step={step("verify")}>
              <p>
                The API signs in over SMTP (to send) and IMAP (to read replies back into the same
                thread). Nothing is sent by this check.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button size="sm" variant={s.authorized ? "quiet" : "default"} disabled={!s.password_present || busy !== null} onClick={verify}>
                  <ShieldCheck size={12} />
                  {busy === "verify" ? "Signing in…" : s.authorized ? "Check again" : "Check the sign-in"}
                </Button>
                {s.address ? (
                  <span className="text-xs text-ink-2">
                    Google accepts <span className="font-mono text-ink">{s.address}</span>
                  </span>
                ) : null}
              </div>
              {step("verify").problem ? <Problem>{step("verify").problem}</Problem> : null}
            </Step>

            {/* 5 — send mode */}
            <ModeStep n={5} step={step("mode")} s={s} busy={busy} setMode={setMode} />
          </ol>
        ) : (
          <ol className="flex flex-col divide-y divide-line">
            {/* 1 — project + APIs */}
            <Step n={1} step={step("project")}>
              <p>
                In Google Cloud Console, signed in as the sending address, create a project
                (any name — <code className="font-mono text-ink-2">ZoNuLy</code>) and enable two
                APIs in it.
              </p>
              <Links
                links={[
                  ["New project", "https://console.cloud.google.com/projectcreate"],
                  ["Enable Gmail API", "https://console.cloud.google.com/apis/library/gmail.googleapis.com"],
                  ["Enable Calendar API", "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"],
                ]}
              />
            </Step>

            {/* 2 — consent screen */}
            <Step n={2} step={step("consent")}>
              <p>
                Set up the consent screen — app name, your address as support and contact email,
                audience <em>External</em>. Then under <em>Audience → Test users</em> add the
                sending address itself. Without that, Google blocks the sign-in with
                &ldquo;app not verified&rdquo;.
              </p>
              <Links
                links={[
                  ["Consent screen", "https://console.cloud.google.com/auth/overview"],
                  ["Test users", "https://console.cloud.google.com/auth/audience"],
                ]}
              />
            </Step>

            {/* 3 — client JSON */}
            <Step n={3} step={step("client")}>
              <p>
                Under <em>Clients → Create client</em> choose type <strong>Desktop app</strong>,
                download its JSON, and drop it here. It is written to{" "}
                <code className="font-mono text-ink-2">{shortPath(s.credentials_file ?? "")}</code>{" "}
                (gitignored, mode 600) and never shown again.
              </p>
              <Links links={[["Create client", "https://console.cloud.google.com/auth/clients"]]} />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".json,application/json"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void uploadClient(f);
                  }}
                />
                <Button size="sm" variant={s.configured ? "quiet" : "default"} disabled={busy !== null} onClick={() => fileRef.current?.click()}>
                  <Upload size={12} />
                  {s.configured ? "Replace client JSON" : "Choose client JSON"}
                </Button>
                {s.configured ? <span className="tnum text-xs text-ink-3">client {s.client_id_hint}</span> : null}
              </div>
              {s.client_problem ? <Problem>{s.client_problem}</Problem> : null}
            </Step>

            {/* 4 — authorize */}
            <Step n={4} step={step("authorize")}>
              <p>
                A browser window opens on the machine running the API. Sign in as the sending
                address and allow <em>send mail</em>, <em>read mail</em> and <em>calendar events</em>.
                On the &ldquo;Google hasn&apos;t verified this app&rdquo; screen click <em>Continue</em>.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button size="sm" variant={s.authorized ? "quiet" : "default"} disabled={!s.configured || busy !== null} onClick={authorize}>
                  <KeyRound size={12} />
                  {busy === "authorize" ? "Waiting for the browser…" : s.authorized ? "Sign in again" : "Sign in with Google"}
                </Button>
                {s.token_present ? (
                  <Button size="sm" variant="quiet" disabled={busy !== null} onClick={() => void run("disconnect", () => api.post("/api/gmail/disconnect"))}>
                    <Unplug size={12} />
                    Disconnect
                  </Button>
                ) : null}
                {s.address ? (
                  <span className="text-xs text-ink-2">
                    connected as <span className="font-mono text-ink">{s.address}</span>
                  </span>
                ) : null}
              </div>
              {s.token_stale ? <Problem>A token exists but Google rejected it — sign in again.</Problem> : null}
            </Step>

            {/* 5 — send mode */}
            <ModeStep n={5} step={step("mode")} s={s} busy={busy} setMode={setMode} />
          </ol>
        )}

        <aside className="border-t border-line p-4 text-xs lg:border-l lg:border-t-0">
          <div className="eyebrow mb-3">Right now</div>
          <dl className="flex flex-col gap-2.5">
            <Fact label="From" value={who} mono={!!s.address_configured} muted={!s.address_configured} />
            <Fact label="Google accepts" value={s.address ?? "not yet"} mono={!!s.address} muted={!s.address} />
            <Fact label="Reply-to" value={s.reply_to ?? "—"} mono />
            <Fact label="Mode" value={s.send_mode === "gmail" ? "Gmail" : "dry-run"} />
            <Fact label="Cap" value={`${quota.daily_cap} / day`} />
            <Fact
              label="Window"
              value={`${quota.send_window[0]}:00–${quota.send_window[1]}:00${quota.in_window ? "" : " · closed now"}`}
            />
            <Fact label="Calendar" value={s.calendar_enabled ? "on" : "off (needs OAuth)"} />
          </dl>
          <p className="mt-4 leading-relaxed text-ink-3">
            Sent mail, replies, bounces and every &ldquo;yes&rdquo; are recorded in this database as they happen,
            so the Dashboard, Tracker and Replies pages show them live. No sheet, no other tool.
          </p>
          {s.next_step === null ? (
            <p className="mt-3 leading-relaxed text-ink-2">
              Everything is wired. Approved emails now leave via Gmail as {who}, within the cap and window.
            </p>
          ) : null}
        </aside>
      </div>

      {error ? (
        <div className="border-t border-line-strong bg-surface-2/15 px-4 py-2 text-sm text-ink">{error}</div>
      ) : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------- pieces */

function ModeStep({ n, step, s, busy, setMode }: {
  n: number; step: GmailStep; s: GmailSetup; busy: string | null; setMode: (m: "dryrun" | "gmail") => void;
}) {
  return (
    <Step n={n} step={step}>
      <p>
        Where an <strong>approved</strong> email goes. Dry-run writes it to{" "}
        <code className="font-mono text-ink-2">outbox/</code> on disk so the whole loop —
        ledger, cap, send window, statuses — can be exercised without anything leaving the
        machine. Gmail is the real thing. Either way nothing moves until you approve it in
        the queue; there is no other path to send.
      </p>
      <div className="mt-2 inline-flex overflow-hidden rounded-sm border border-line">
        {(["dryrun", "gmail"] as const).map((m) => {
          const active = s.send_mode === m;
          const locked = m === "gmail" && !s.authorized;
          return (
            <button
              key={m}
              disabled={locked || busy !== null || active}
              onClick={() => setMode(m)}
              title={locked ? "Google has to accept the sign-in first" : undefined}
              className={cn(
                "px-3 py-1.5 font-mono text-xs transition-colors",
                active ? "bg-surface-2/15 text-ink" : "text-ink-3 hover:text-ink",
                locked && "cursor-not-allowed opacity-50",
              )}
            >
              {m === "dryrun" ? "dry-run" : "gmail"}
            </button>
          );
        })}
      </div>
    </Step>
  );
}

function Step({ n, step, children }: { n: number; step: GmailStep; children: React.ReactNode }) {
  return (
    <li className="flex gap-3 p-4">
      <span
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] tnum",
          step.done ? "border-line-strong bg-surface-2/15 text-ink" : "border-line text-ink-3",
        )}
        aria-label={step.done ? "done" : "to do"}
      >
        {step.done ? <Check size={11} /> : n}
      </span>
      <div className="min-w-0 flex-1">
        <div className={cn("mb-1 text-sm", step.done ? "text-ink-2" : "text-ink")}>{step.label}</div>
        <div className="text-xs leading-relaxed text-ink-3">{children}</div>
      </div>
    </li>
  );
}

function Links({ links }: { links: [string, string][] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
      {links.map(([label, href]) => (
        <a
          key={href}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-ink-2 underline decoration-line underline-offset-2 hover:text-ink"
        >
          {label}
          <ExternalLink size={10} />
        </a>
      ))}
    </div>
  );
}

function Problem({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 border-l-2 border-line-strong pl-2 text-ink">{children}</p>;
}

function Fact({ label, value, mono, muted }: { label: string; value: string; mono?: boolean; muted?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-3">{label}</dt>
      <dd className={cn("truncate text-right", mono && "font-mono", muted ? "text-ink-3" : "text-ink")}>{value}</dd>
    </div>
  );
}

function shortPath(p: string) {
  const i = p.indexOf("/secrets/");
  return i >= 0 ? p.slice(i + 1) : p;
}
