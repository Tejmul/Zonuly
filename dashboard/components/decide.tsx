"use client";

import Link from "next/link";
import { useState } from "react";
import {
  api,
  useApi,
  useTaskWatcher,
  type DecidePage,
  type DecideRow,
  type DecideStage,
  type DecideToday,
  type DecideVerdict,
  type MixerPlan,
  type ShortlistProgress,
  type ShortlistReferrals,
  API_BASE,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button, Empty, ErrorNote, Loading, Panel, PanelHead, Table, Td, Th, Tr } from "@/components/ui/primitives";

/* The decision brain (jobhunter/decide.py). One company a day off a shortlist of the best
   Bengaluru / Hyderabad companies by P(offer) = open · call · loop · pay, each a probability
   with its evidence. Black and white like the role bands: the verdict is carried by fill and
   border, never colour. */

const VERDICT_STYLE: Record<DecideVerdict, string> = {
  target: "bg-ink text-on-ink border-ink",
  fill_first: "border-ink text-ink",
  after_dsa: "border-line-strong text-ink-2 border-dashed",
  skip: "border-line text-ink-3",
};

export function VerdictChip({ verdict, label }: { verdict: DecideVerdict; label: string }) {
  return (
    <span className={cn("inline-flex h-5 items-center rounded-sm border px-1.5 text-[11px] font-medium whitespace-nowrap", VERDICT_STYLE[verdict])}>
      {label}
    </span>
  );
}

const pct = (p: number) => (p >= 0.1 ? `${Math.round(p * 100)}%` : `${(p * 100).toFixed(1)}%`);
const STAGES: { key: DecideStage; short: string }[] = [
  { key: "open", short: "A fresher role is open" },
  { key: "call", short: "You get an interview" },
  { key: "loop", short: "You pass their interviews" },
  { key: "pay", short: "They pay your minimum" },
];

function Odds({ d, big = false }: { d: DecideRow; big?: boolean }) {
  return (
    <div className="flex flex-col items-start gap-0.5">
      <span className={cn("tnum font-semibold text-ink", big ? "text-3xl" : "text-base")}>{pct(d.p_offer)}</span>
      <span
        className="text-[11px] leading-snug text-ink-3"
        title={`Most likely ${pct(d.p_offer)}; we are fairly sure it is between ${pct(d.p_lo)} and ${pct(d.p_hi)}.`}
      >
        chance of an offer{d.one_in ? ` — about 1 in ${d.one_in} companies like this` : ""}
      </span>
    </div>
  );
}

function StageBars({ d }: { d: DecideRow }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-4">
      {STAGES.map((s) => {
        const p = d.stages[s.key];
        const weak = d.weakest === s.key || (s.key === "call" && (d.weakest === "reach" || d.weakest === "screen"));
        return (
          <div key={s.key} className="min-w-0">
            <div className="flex items-baseline justify-between gap-2 text-[11px]">
              <span className={cn("truncate", weak ? "font-medium text-ink" : "text-ink-3")}>{s.short}{weak ? " ·  weakest" : ""}</span>
              <span className="tnum text-ink-2">{pct(p)}</span>
            </div>
            <div className="mt-0.5 h-1 rounded-sm bg-surface-3">
              <div className="h-1 rounded-sm bg-ink" style={{ width: `${Math.max(2, p * 100)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- today */

export function TodayCard() {
  const { data, error, loading, reload } = useApi<DecideToday>("/api/decide/today");
  const { watch } = useTaskWatcher();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const pick = data?.pick;
  const d = pick?.decision;

  async function mark(status: "asked" | "skipped") {
    setBusy(status);
    try {
      await api.post(`/api/decide/today/${status}`);
      reload();
    } finally {
      setBusy(null);
    }
  }

  async function fill(cid: number) {
    setBusy("fill");
    setMsg("Filling — description, hiring proof, facts and referrers. A few minutes.");
    try {
      const r = await api.post<{ task_id: string | null; skipped?: string }>(`/api/companies/${cid}/fill`);
      if (!r.task_id) {
        setMsg(r.skipped ? `Not filled: ${r.skipped}` : "Not filled");
        setBusy(null);
        return;
      }
      watch(r.task_id, async () => {
        const t = await api.post<{ task_id: string }>("/api/decide/run");
        watch(t.task_id, () => {
          setMsg("Filled and decided again.");
          setBusy(null);
          reload();
        });
      });
    } catch (e) {
      setMsg(String(e));
      setBusy(null);
    }
  }

  return (
    <Panel className="mb-5">
      <PanelHead eyebrow={`Today · ${data?.day ?? ""}`} title="One company a day" />
      <div className="px-4 py-4">
        {loading && !data ? (
          <Loading label="Picking today's company" rows={2} />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !pick || !d ? (
          <Empty title="Nothing to pick" hint="Run the brain (python scripts/run.py decide run), or every shortlisted company was picked in the last 30 days." />
        ) : (
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:gap-8">
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/companies/${d.company_id}`} className="text-lg font-semibold text-ink hover:underline">
                  {d.name}
                </Link>
                <span className="text-xs text-ink-3">{d.city} · #{d.rank ?? "–"} in {d.pool}</span>
                <VerdictChip verdict={d.verdict} label={d.verdict_label} />
                {pick.status !== "picked" ? <span className="text-xs text-ink-3">· {pick.status}</span> : null}
              </div>
              <p className="text-[13px] leading-relaxed text-ink-2">{d.reason}</p>
              <StageBars d={d} />
              {d.questions ? (
                <ul className="flex flex-col gap-1 border-t border-line pt-3">
                  {Object.entries(d.questions).map(([k, q]) => (
                    <li key={k} className="grid grid-cols-[minmax(0,13rem)_3rem_minmax(0,1fr)] gap-3 text-xs">
                      <span className="text-ink-2">{q.label}</span>
                      <span className="tnum text-ink">{pct(q.p)}</span>
                      <span className="line-clamp-2 text-ink-3">{q.why}{q.fill_fixes ? " — a fill can change this" : ""}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
            <div className="flex shrink-0 flex-col items-start gap-3 lg:w-56">
              <Odds d={d} big />
              <span className="text-xs text-ink-3">
                {d.lift ? `${d.lift}× better than the typical company on your list` : null}
                {" · "}how DSA-heavy their interviews are: {Math.round(d.dsa * 100)}%
              </span>
              <div className="flex flex-wrap gap-2">
                {pick.action === "fill" && pick.status === "picked" ? (
                  <Button variant="primary" size="sm" disabled={!!busy} onClick={() => fill(d.company_id)}>
                    {busy === "fill" ? "Filling…" : "Fill it"}
                  </Button>
                ) : (
                  <Button variant="primary" size="sm" asChild>
                    <Link href={`/companies/${d.company_id}`}>Ask a referrer</Link>
                  </Button>
                )}
                {pick.status !== "asked" ? (
                  <Button variant="quiet" size="sm" disabled={!!busy} onClick={() => mark("asked")}>
                    Mark asked
                  </Button>
                ) : null}
                <Button variant="quiet" size="sm" disabled={!!busy} onClick={() => mark("skipped")}>
                  Skip
                </Button>
              </div>
              {msg ? <p className="text-xs text-ink-3">{msg}</p> : null}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------------- the shortlist */

const VIEWS = [
  { key: "Bengaluru", label: "Bengaluru", hint: "the shortlist, best P(offer) first" },
  { key: "Hyderabad", label: "Hyderabad", hint: "the shortlist, best P(offer) first" },
  { key: "Remote", label: "Remote", hint: "US / UK / anywhere, open to India — DSA tolerated, pay must be good" },
  { key: "after_dsa", label: "After DSA", hint: "DSA-heavy loops at the top of the pay range — back when your DSA level rises" },
  { key: "skip", label: "Skipped", hint: "a guardrail fails" },
] as const;

export function DecideShortlist() {
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("Bengaluru");
  const parked = view === "after_dsa" || view === "skip";
  const path = parked ? `/api/decide?verdict=${view}&limit=300` : `/api/decide?pool=${view}&limit=200`;
  const { data, error, loading, reload } = useApi<DecidePage>(path);

  const count = (k: string) => {
    if (!data) return null;
    if (k === "Bengaluru" || k === "Hyderabad" || k === "Remote") return data.counts[k]?.shortlist ?? 0;
    return Object.values(data.counts).reduce((n, c) => n + (c[k as DecideVerdict] ?? 0), 0);
  };

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Shortlist" className="inline-flex rounded-sm border border-line p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              role="tab"
              type="button"
              aria-selected={view === v.key}
              title={v.hint}
              onClick={() => setView(v.key)}
              className={`h-8 cursor-pointer rounded-sm px-3.5 text-[13px] transition-colors ${
                view === v.key ? "bg-ink font-medium text-on-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              {v.label} <span className="tnum opacity-70">{count(v.key) ?? ""}</span>
            </button>
          ))}
        </div>
        <span className="text-xs text-ink-3">
          {VIEWS.find((v) => v.key === view)?.hint}
          {data ? ` · your DSA level ${Math.round(data.dsa_level * 100)}%` : ""}
        </span>
      </div>
      <Panel>
        {loading && !data ? (
          <Loading label="Deciding" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !data?.items.length ? (
          <Empty title="Nothing here yet" hint="The tick decides every Bengaluru / Hyderabad company after the role scores; or run python scripts/run.py decide run." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-10">#</Th>
                <Th className="w-40">Chance of an offer</Th>
                <Th>Company</Th>
                <Th className="w-[36%]">{parked ? "Why parked" : "Stages"}</Th>
                <Th className="w-20">DSA</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((d) => (
                <Tr key={d.company_id}>
                  <Td className="tnum text-xs text-ink-3">{d.rank ?? "–"}</Td>
                  <Td>
                    <Odds d={d} />
                  </Td>
                  <Td>
                    <div className="flex flex-wrap items-center gap-2">
                      <Link href={`/companies/${d.company_id}`} className="text-[13px] font-medium text-ink hover:underline">
                        {d.name}
                      </Link>
                      <VerdictChip verdict={d.verdict} label={d.verdict_label} />
                    </div>
                    <div className="mt-0.5 line-clamp-2 text-xs text-ink-3">
                      {d.city}
                      {d.lift ? ` · ${d.lift}× better than the typical company on your list` : ""}
                      {d.culture ? ` · culture ${d.culture.rating}/5 (${d.culture.reviews})` : ""}
                      {d.best_job_title ? ` · best role: ${d.best_job_title}` : ""}
                      {!parked && d.verdict === "fill_first" ? ` · ${d.reason}` : ""}
                    </div>
                  </Td>
                  <Td>{parked ? <span className="text-xs leading-relaxed text-ink-2">{d.reason}</span> : <StageBars d={d} />}</Td>
                  <Td className="tnum text-xs text-ink-2">{Math.round(d.dsa * 100)}%</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

/* ---------------------------------------------------------------- the mixer */

export function MixerPanel() {
  const { data, error, loading, reload } = useApi<MixerPlan>("/api/mixer/plan");
  const { watch } = useTaskWatcher();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function buy() {
    setBusy(true);
    setMsg("Buying ads for the next companies, then deciding again.");
    try {
      const r = await api.post<{ task_id: string }>("/api/mixer/run");
      watch(r.task_id, (t) => {
        const res = t.result as { cost_usd?: number; jobs_saved?: number } | null;
        setMsg(res ? `Done: ${res.jobs_saved ?? 0} ads saved for $${res.cost_usd ?? 0}.` : `Finished: ${t.status}`);
        setBusy(false);
        reload();
      });
    } catch (e) {
      setMsg(String(e));
      setBusy(false);
    }
  }

  return (
    <Panel className="mt-5">
      <PanelHead
        eyebrow="Mixer · decide, then scrape"
        title="Apify only where data is missing — referrals keep the budget"
        action={
          data ? (
            <Button variant="quiet" size="sm" disabled={busy || !data.can_spend_now || !data.next.length} onClick={buy}
              title={data.can_spend_now ? undefined : "Referrals first: today's remaining Apify money is kept for them"}>
              {busy ? "Buying…" : `Buy next ${data.next.length} (≈ $${data.estimate_usd})`}
            </Button>
          ) : null
        }
      />
      <div className="px-4 py-3">
        {loading && !data ? (
          <Loading label="Planning" rows={3} />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : data ? (
          <div className="flex flex-col gap-3">
            <ol className="flex flex-col gap-1">
              {data.layers.map((l) => (
                <li key={l.layer} className="grid grid-cols-[minmax(0,12rem)_6rem_minmax(0,1fr)] gap-3 text-xs">
                  <span className="text-ink-2">{l.layer}</span>
                  <span className="tnum text-ink">
                    {l.in} → {l.out}
                  </span>
                  <span className="truncate text-ink-3">{l.dropped_sample.slice(0, 2).join("; ")}</span>
                </li>
              ))}
            </ol>
            <p className="text-xs text-ink-2">
              Next: {data.next.map((x) => `${x.name} (${x.pool})`).join(", ") || "nobody needs buying"}
            </p>
            <p className="tnum text-xs text-ink-3">
              Apify today ${data.spent_today_usd} of ${data.apify_daily_usd} · mixer ${data.mixer_spent_today_usd} of $
              {data.mixer_daily_usd} · ${data.referral_reserve_usd} kept for referrals
              {data.can_spend_now ? "" : " — not buying today: referrals first"}
            </p>
            {msg ? <p className="text-xs text-ink-3">{msg}</p> : null}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------------- completing the shortlist */

export function ShortlistPanel() {
  const { data, error, loading, reload } = useApi<ShortlistProgress>("/api/shortlist/progress");
  const [pool, setPool] = useState<string | null>(null);
  const shown = pool ?? data?.next?.pool ?? data?.order[0] ?? "Bengaluru";
  const rows = (data?.pools[shown]?.rows ?? []).filter((r) => r.status !== "waiting");

  return (
    <Panel className="mt-5">
      <PanelHead
        eyebrow="Completing the shortlist · one company at a time"
        title={data?.next ? `Next: ${data.next.name} (${data.next.pool} #${data.next.rank})` : "Every shortlisted company has had its pass"}
        action={
          <Button variant="quiet" size="sm" onClick={reload}>
            Refresh
          </Button>
        }
      />
      <div className="px-4 py-3">
        {loading && !data ? (
          <Loading label="Reading progress" rows={3} />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : data ? (
          <div className="flex flex-col gap-3">
            <div className="grid gap-3 sm:grid-cols-3">
              {data.order.map((p) => {
                const x = data.pools[p];
                const pct = x.total ? ((x.complete + x.partial) / x.total) * 100 : 0;
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPool(p)}
                    className={cn("cursor-pointer rounded-sm border px-3 py-2 text-left transition-colors",
                      shown === p ? "border-ink" : "border-line hover:border-line-strong")}
                  >
                    <div className="flex items-baseline justify-between text-xs">
                      <span className="font-medium text-ink">{p}</span>
                      <span className="tnum text-ink-3">{x.complete + x.partial} / {x.total} done</span>
                    </div>
                    <div className="mt-1.5 h-1 rounded-sm bg-surface-3">
                      <div className="h-1 rounded-sm bg-ink" style={{ width: `${Math.max(1, pct)}%` }} />
                    </div>
                    <div className="tnum mt-1 text-[11px] text-ink-3">
                      {x.complete} complete · {x.partial} with gaps · {x.waiting} waiting
                    </div>
                  </button>
                );
              })}
            </div>
            {rows.length ? (
              <ul className="flex flex-col divide-y divide-line">
                {rows.map((r) => (
                  <li key={r.company_id} className="grid gap-1 py-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] sm:gap-4">
                    <div className="text-xs">
                      <span className="tnum text-ink-3">#{r.rank} </span>
                      <Link href={`/companies/${r.company_id}`} className="font-medium text-ink hover:underline">
                        {r.name}
                      </Link>
                      <span className="text-ink-3"> · {r.status === "complete" ? "complete" : "has gaps"}</span>
                    </div>
                    <ul className="flex flex-col gap-0.5">
                      {Object.entries(r.missing ?? {}).map(([part, why]) => (
                        <li key={part} className="text-xs leading-relaxed">
                          <span className="text-ink-2">{data.parts[part] ?? part}:</span> <span className="text-ink-3">{why}</span>
                        </li>
                      ))}
                      {!Object.keys(r.missing ?? {}).length ? <li className="text-xs text-ink-3">Every part of the card is filled.</li> : null}
                    </ul>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ink-3">Nothing in {shown} has had its pass yet; the job takes one company every 10 minutes, Bengaluru first.</p>
            )}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------------- the leads list */

const CLASS_LABEL: Record<string, string> = {
  senior_engineer: "Senior engineer", engineer: "Engineer", eng_manager: "Eng manager", founder: "Founder",
  tech_recruiter: "Tech recruiter", recruiter: "Recruiter",
};

export function ReferralsPanel() {
  const [pool, setPool] = useState("Bengaluru");
  const [tech, setTech] = useState(true);
  const { data, error, loading, reload } = useApi<ShortlistReferrals>(`/api/shortlist/referrals?pool=${pool}&tech_only=${tech}`);
  const csv = `${API_BASE}/api/shortlist/referrals?pool=${pool}&tech_only=${tech}&format=csv`;
  return (
    <Panel className="mt-5">
      <PanelHead
        eyebrow="Referrals for your shortlist"
        title={data ? `${data.totals.engineers} engineers · ${data.totals.real_address} with a real address · ${data.totals.people} people at ${data.totals.companies} companies` : "Referrals"}
        action={<a href={csv} className="text-xs text-ink-2 underline decoration-line-strong underline-offset-2 hover:text-ink">Download CSV</a>}
      />
      <div className="px-4 py-3">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div role="tablist" className="inline-flex rounded-sm border border-line p-0.5">
            {["Bengaluru", "Hyderabad", "Remote", "all"].map((p) => (
              <button key={p} type="button" role="tab" aria-selected={pool === p} onClick={() => setPool(p)}
                className={`h-7 cursor-pointer rounded-sm px-3 text-xs ${pool === p ? "bg-ink font-medium text-on-ink" : "text-ink-2 hover:text-ink"}`}>
                {p === "all" ? "All" : p}
              </button>
            ))}
          </div>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-2">
            <input type="checkbox" checked={tech} onChange={(e) => setTech(e.target.checked)} /> Tech team only
          </label>
        </div>
        {loading && !data ? (
          <Loading label="Reading referrals" rows={4} />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {(data?.companies ?? []).map((c) => (
              <li key={c.company_id}>
                <details>
                  <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-3 py-2 text-xs">
                    <span className="tnum text-ink-3">#{c.rank}</span>
                    <span className="font-medium text-ink">{c.name}</span>
                    <span className="text-ink-3">{c.pool}</span>
                    <span className="tnum text-ink-2">{c.people.length} people · {c.engineers} engineers · {c.with_address} with an address</span>
                    <Link href={`/companies/${c.company_id}`} className="ml-auto text-ink-3 hover:text-ink hover:underline">company page</Link>
                  </summary>
                  {c.people.length ? (
                    <ul className="mb-2 ml-6 flex flex-col gap-1">
                      {c.people.map((p) => (
                        <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
                          <span className="font-medium text-ink">{p.name ?? "unnamed"}</span>
                          <span className="text-ink-3">{CLASS_LABEL[p.role_class] ?? p.role_class}{p.current ? "" : " · not confirmed current"}</span>
                          {p.email ? (
                            <span className="tnum text-ink-2">{p.email} <span className="text-ink-3">({p.address})</span></span>
                          ) : (
                            <span className="text-ink-3">no address</span>
                          )}
                          <span className="ml-auto flex items-center gap-2">
                            {p.x ? <a href={`https://x.com/${p.x}`} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink">@{p.x}</a> : null}
                            {p.github ? <a href={p.github} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink">GitHub</a> : null}
                            {p.linkedin ? (
                              <a href={p.linkedin} target="_blank" rel="noreferrer"
                                className="rounded-sm border border-line px-1.5 py-0.5 text-[11px] text-ink-2 hover:border-ink hover:text-ink">LinkedIn</a>
                            ) : null}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mb-2 ml-6 text-xs text-ink-3">No {tech ? "engineers" : "people"} on file yet — the Apify referral job gets to it in Best bets order.</p>
                  )}
                </details>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}
