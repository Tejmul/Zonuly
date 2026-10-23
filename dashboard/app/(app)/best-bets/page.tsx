"use client";

import Link from "next/link";
import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { useApi, type RolesPage, type SelectionBand } from "@/lib/api";
import { lpa } from "@/lib/utils";
import { PageHead } from "@/components/shell";
import { BAND_LABEL, BandChip } from "@/components/selection";
import { Empty, ErrorNote, Loading, Panel, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { DecideShortlist, MixerPanel, ShortlistPanel, TodayCard } from "@/components/decide";

/* Best bets: roles ordered by the odds of getting selected (jobhunter/confidence.py).
   One engine, two weight profiles — India (Bengaluru / Hyderabad on-site or hybrid) and
   Remote (anywhere / from India). Each row: score and band, the top three reasons, the
   main blocker. The breakdown and the needs brief are on the role page.
   Companies (the default, 2026-10-23) is the decision brain over those roles (jobhunter/decide.py):
   P(offer) per Bengaluru / Hyderabad company, the shortlist, and one company a day. */

const MARKETS = [
  { key: "india", label: "India", hint: "Bengaluru / Hyderabad, on-site or hybrid" },
  { key: "remote", label: "Remote", hint: "remote-anywhere or remote-from-India" },
] as const;
const BANDS: SelectionBand[] = ["high", "medium", "low", "long_shot"];

const LEVELS = [
  { key: "companies", label: "Companies", hint: "P(offer) per company — the shortlist and today's pick" },
  { key: "roles", label: "Roles", hint: "every role by its selection score" },
] as const;

export default function BestBetsPage() {
  const [level, setLevel] = useState<(typeof LEVELS)[number]["key"]>("companies");
  return (
    <>
      <PageHead
        title="Best bets"
        description={
          level === "companies"
            ? "Where an offer is most likely: P(offer) = role open × interview call × clears the loop × pays, with the odds and an 80% interval. One company a day."
            : "Roles by the odds of getting selected — so the 25 emails a day go where they can land. Every number is itemised on the role page."
        }
      />
      <div role="tablist" aria-label="Level" className="mb-4 inline-flex rounded-sm border border-line p-0.5">
        {LEVELS.map((l) => (
          <button
            key={l.key}
            role="tab"
            type="button"
            aria-selected={level === l.key}
            title={l.hint}
            onClick={() => setLevel(l.key)}
            className={`h-8 cursor-pointer rounded-sm px-3.5 text-[13px] transition-colors ${
              level === l.key ? "bg-ink font-medium text-on-ink" : "text-ink-2 hover:text-ink"
            }`}
          >
            {l.label}
          </button>
        ))}
      </div>
      {level === "companies" ? (
        <>
          <TodayCard />
          <DecideShortlist />
          <ShortlistPanel />
          <MixerPanel />
        </>
      ) : (
        <RolesView />
      )}
    </>
  );
}

function RolesView() {
  const [market, setMarket] = useState<(typeof MARKETS)[number]["key"]>("india");
  const [bands, setBands] = useState<SelectionBand[]>(["high", "medium"]);

  const params = new URLSearchParams({ sort: "selection", market, limit: "100" });
  if (bands.length) params.set("band", bands.join(","));
  const { data, error, loading, reload } = useApi<RolesPage>(`/api/roles?${params.toString()}`);
  const counts = data?.counts?.[market] ?? {};

  function toggle(b: SelectionBand) {
    setBands((cur) => (cur.includes(b) ? cur.filter((x) => x !== b) : [...cur, b]));
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Market" className="inline-flex rounded-sm border border-line p-0.5">
          {MARKETS.map((m) => (
            <button
              key={m.key}
              role="tab"
              type="button"
              aria-selected={market === m.key}
              title={m.hint}
              onClick={() => setMarket(m.key)}
              className={`h-8 cursor-pointer rounded-sm px-3.5 text-[13px] transition-colors ${
                market === m.key ? "bg-ink font-medium text-on-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Bands">
          {BANDS.map((b) => {
            const on = bands.includes(b);
            return (
              <button
                key={b}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(b)}
                className={`inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-sm border px-2.5 text-xs transition-colors ${
                  on ? "border-ink text-ink" : "border-line text-ink-3 hover:text-ink"
                }`}
              >
                {BAND_LABEL[b]}
                <span className="tnum text-ink-3">{counts[b] ?? 0}</span>
              </button>
            );
          })}
        </div>
        <span className="text-xs text-ink-3">{MARKETS.find((m) => m.key === market)?.hint}</span>
      </div>

      <Panel>
        {loading && !data ? (
          <Loading label="Scoring roles" />
        ) : error ? (
          <ErrorNote message={error} onRetry={reload} />
        ) : !data?.items.length ? (
          <Empty
            title={bands.length ? "No roles in these bands yet" : "Pick a band"}
            hint="Scores are recomputed by the tick every few minutes as roles, referrers and hiring checks change. Try including Low, or switch market."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-28">Odds</Th>
                <Th>Role</Th>
                <Th className="w-[34%]">Why it can land</Th>
                <Th className="w-[22%]">Main blocker</Th>
                <Th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <Tr key={r.id}>
                  <Td>
                    <div className="flex flex-col items-start gap-1">
                      <span className="tnum text-base font-semibold text-ink">{r.score}</span>
                      <BandChip band={r.band} />
                    </div>
                  </Td>
                  <Td>
                    <Link href={`/roles/${r.id}`} className="text-[13px] font-medium text-ink hover:underline">
                      {r.title}
                    </Link>
                    <div className="mt-0.5 text-xs text-ink-3">
                      {r.company_id ? (
                        <Link href={`/companies/${r.company_id}`} className="text-ink-2 hover:underline">
                          {r.company_name}
                        </Link>
                      ) : (
                        r.company_name
                      )}
                      {" · "}
                      {r.city ?? r.location ?? (r.market === "remote" ? "Remote" : "—")}
                      {lpa(r.salary_min_lpa, r.salary_max_lpa) ? ` · ${lpa(r.salary_min_lpa, r.salary_max_lpa)} LPA` : ""}
                      {r.has_brief ? " · brief ready" : ""}
                    </div>
                  </Td>
                  <Td>
                    <ul className="flex flex-col gap-0.5">
                      {r.top_reasons.map((x, i) => (
                        <li key={i} className="line-clamp-2 text-xs leading-relaxed text-ink-2">
                          {x}
                        </li>
                      ))}
                      {!r.top_reasons.length ? <li className="text-xs text-ink-3">No strong term yet</li> : null}
                    </ul>
                  </Td>
                  <Td className="text-xs leading-relaxed text-ink-2">
                    {r.main_blocker ? <span className="line-clamp-3">{r.main_blocker}</span> : <span className="text-ink-3">None</span>}
                  </Td>
                  <Td>
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-ink-3 transition-colors hover:text-ink"
                      aria-label={`Open ${r.title} posting`}
                    >
                      <ExternalLink size={13} />
                    </a>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>
      {data && data.total > data.items.length ? (
        <p className="mt-2 text-xs text-ink-3">
          Showing the top {data.items.length} of {data.total}.
        </p>
      ) : null}
    </>
  );
}
