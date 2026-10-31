"use client";

import { useState } from "react";
import { api, useApi, useTaskWatcher } from "@/lib/api";

/* Interview prep for a company (/api/companies/{id}/interviews): prep points drawn from what
   candidates reported, the company's own word on its process, then every report as written
   (Glassdoor, Reddit, blogs) with its link. Renders an empty state with a scrape button when
   nothing has been gathered yet. */

type Note = {
  id: number;
  kind: "report" | "official" | "prep";
  source: string;
  url: string | null;
  role: string | null;
  outcome: string | null;
  difficulty: string | null;
  experience: string | null;
  title: string | null;
  body: string;
  questions: string[];
  evidence: string | null;
  reported_at: string | null;
};

type Interviews = {
  prep: Note[];
  official: Note[];
  reports: Note[];
  counts: { reports: number; offers: number; by_source: Record<string, number> };
  updated_at: string | null;
};

const SOURCE_LABEL: Record<string, string> = {
  glassdoor: "Glassdoor", reddit: "Reddit", medium: "Medium", github: "GitHub", linkedin: "LinkedIn",
};
const SHOWN = 6;

export function InterviewPrep({ companyId }: { companyId: number }) {
  const { data, reload } = useApi<Interviews>(`/api/companies/${companyId}/interviews`);
  const { watch } = useTaskWatcher();
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<string>("all");
  const [all, setAll] = useState(false);

  async function scrape() {
    setBusy(true);
    try {
      const { task_id } = await api.post<{ task_id: string }>(`/api/companies/${companyId}/interviews/scrape`);
      watch(task_id, () => { setBusy(false); void reload(); });
    } catch {
      setBusy(false);
    }
  }

  const reports = (data?.reports ?? []).filter((r) => source === "all" || r.source === source);
  const shown = all ? reports : reports.slice(0, SHOWN);
  const sources = Object.entries(data?.counts.by_source ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <section data-anim="sec" className="mt-6 plate px-4 py-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Interview prep</h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            {data?.counts.reports
              ? `From ${data.counts.reports} candidate reports (${sources.map(([s, n]) => `${SOURCE_LABEL[s] ?? s} ${n}`).join(" · ")}). Every prep point names the reports behind it.`
              : "What candidates say their interviews were like: Glassdoor, Reddit and blogs."}
          </p>
        </div>
        <button
          type="button"
          onClick={scrape}
          disabled={busy}
          className="ml-auto inline-flex h-8 shrink-0 cursor-pointer items-center rounded-md border border-line px-3 text-[12px] transition-colors hover:bg-surface-2 disabled:cursor-default disabled:opacity-60"
        >
          {busy ? "Scraping…" : data?.counts.reports ? "Scrape again" : "Scrape interviews"}
        </button>
      </div>

      <div className="mt-4">
        {!data?.counts.reports && !data?.prep.length ? (
          <p className="text-[13px] text-ink-3">Nothing gathered yet.</p>
        ) : (
          <>
            {data.prep.length > 0 && (
              <ol className="space-y-3">
                {data.prep.map((p, i) => (
                  <li key={p.id} className="flex gap-3">
                    <span className="tnum w-4 shrink-0 text-[12px] text-ink-3">{i + 1}</span>
                    <div className="min-w-0">
                      <p className="text-[13px] font-medium">{p.title}</p>
                      <p className="mt-0.5 max-w-3xl text-[12.5px] leading-relaxed text-ink-2">{p.body}</p>
                      {p.evidence && <p className="mt-1 text-[11.5px] text-ink-3 [overflow-wrap:anywhere]">from {p.evidence}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            )}

            {data.official.map((o) => (
              <div key={o.id} className="mt-5">
                <p className="marginal">{o.title ?? "In their own words"}</p>
                <p className="mt-1.5 max-w-3xl border-l-2 border-line pl-3 text-[12.5px] leading-relaxed text-ink-2 italic">{o.body}</p>
                {o.url && (
                  <a href={o.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-[11.5px] text-ink-3 underline underline-offset-2">
                    {SOURCE_LABEL[o.source] ?? o.source}
                  </a>
                )}
              </div>
            ))}

            {data.reports.length > 0 && (
              <div className="mt-6">
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="marginal mr-2">Reports</p>
                  {[["all", data.reports.length] as [string, number], ...sources].map(([s, n]) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => { setSource(s); setAll(false); }}
                      className={`chip cursor-pointer ${source === s ? "chip-solid" : ""}`}
                    >
                      {s === "all" ? "All" : SOURCE_LABEL[s] ?? s} {n}
                    </button>
                  ))}
                </div>
                <ul className="mt-3 divide-y divide-line">
                  {shown.map((r) => <Report key={r.id} r={r} />)}
                </ul>
                {reports.length > SHOWN && (
                  <button type="button" onClick={() => setAll(!all)} className="mt-2 cursor-pointer text-xs text-ink-2 hover:text-ink hover:underline">
                    {all ? "Show fewer" : `Show all ${reports.length}`}
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Report({ r }: { r: Note }) {
  const heading = r.role ?? r.title ?? "Report";
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[13px] font-medium">{heading}</span>
        {r.outcome && r.outcome !== "unknown" && (
          <span className={`chip ${r.outcome === "offer" || r.outcome === "declined offer" ? "chip-solid" : ""}`}>{r.outcome}</span>
        )}
        {r.difficulty && <span className="chip">{r.difficulty}</span>}
        <span className="ml-auto text-[11.5px] text-ink-3">
          {r.reported_at ?? ""}
          {r.url && (
            <>
              {" · "}
              <a href={r.url} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-ink">
                {SOURCE_LABEL[r.source] ?? r.source}
              </a>
            </>
          )}
        </span>
      </div>
      {r.role && r.title && <p className="mt-0.5 text-[12px] text-ink-3">{r.title}</p>}
      {r.body && <p className="mt-1 max-w-3xl whitespace-pre-line text-[12.5px] leading-relaxed text-ink-2">{r.body}</p>}
      {r.questions.length > 0 && (
        <ul className="mt-1.5 max-w-3xl list-disc space-y-0.5 pl-5 text-[12px] leading-relaxed text-ink-2">
          {r.questions.map((q, i) => <li key={i} className="[overflow-wrap:anywhere]">{q}</li>)}
        </ul>
      )}
    </li>
  );
}
