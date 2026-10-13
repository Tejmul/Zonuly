"use client";

import { useApi } from "@/lib/api";
import { Skeleton } from "@/components/ui/primitives";

/* LEARN — the funnel with numbers. Reply and yes rates by every lever we control, so
   the next month's 25-a-day goes where the last month's answers came from. */

type Bucket = Record<string, { sent: number; replied: number; positive: number; reply_rate: number; yes_rate: number }>;
type VBucket = Record<string, { sent: number; replied: number; positive: number; reply_rate: number | null;
  yes_rate: number | null; enough: boolean; note: string | null }>;
type Variants = { min_sent: number } & Record<string, VBucket | number>;
type Report = {
  sent: number; replied: number; positive: number; closed: number; reply_rate: number; yes_rate: number;
  reading: string;
  by_who_we_asked: Bucket; by_pay_power: Bucket; by_segment: Bucket; by_region: Bucket;
  by_where_we_found_them: Bucket; by_email_kind: Bucket; by_role_level: Bucket; by_remote_anywhere: Bucket;
  by_length: Bucket; by_address_confidence: Bucket; by_week: Bucket;
  variants?: Variants;
};

const VARIANT_SECTIONS: [string, string][] = [
  ["by_structure", "Structure"], ["by_subject_pattern", "Subject pattern"], ["by_hook_type", "Hook"],
  ["by_send_hour", "Send hour"], ["by_quality", "Quality"], ["by_writer", "Model vs template"],
];

const SECTIONS: [keyof Report, string][] = [
  ["by_who_we_asked", "Who we asked"], ["by_pay_power", "What they can pay (Pay Power)"], ["by_segment", "Segment"],
  ["by_region", "Region"], ["by_where_we_found_them", "Where we found them"], ["by_role_level", "Role level"],
  ["by_remote_anywhere", "Remote from anywhere"], ["by_length", "Email length"], ["by_address_confidence", "Address"],
  ["by_email_kind", "Cold vs follow-up"], ["by_week", "By week"],
];

export function Learn() {
  const { data, error } = useApi<Report>("/api/learn");
  // Learn is a footnote under the tracker: when its numbers are not there it says
  // nothing rather than putting a second error on the page.
  if (error && !data) return null;
  if (!data) return <Skeleton rows={3} className="py-4" />;
  const pct = (x: number) => `${Math.round(x * 100)}%`;

  return (
    <div className="plate mt-4">
      <div className="border-b border-line px-4 py-3">
        <h2 className="font-[family-name:var(--font-display)] text-[15px] font-semibold tracking-[-0.01em]">What works</h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          <span className="tnum">{data.sent}</span> sent · <span className="tnum">{data.replied}</span> replied ({pct(data.reply_rate)}) ·{" "}
          <span className="tnum">{data.positive}</span> yes ({pct(data.yes_rate)}) · <span className="tnum">{data.closed}</span> hiring done
        </p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-ink-3">{data.reading}</p>
      </div>
      {data.variants ? (
        <div className="border-b border-line px-4 py-3">
          <p className="marginal">Which writing works — a rate shows after {data.variants.min_sent} sends of a variant</p>
          <div className="mt-2 grid gap-x-6 gap-y-3 md:grid-cols-2 xl:grid-cols-3">
            {VARIANT_SECTIONS.map(([key, label]) => {
              const b = (data.variants?.[key] ?? {}) as VBucket;
              const rows = Object.entries(b);
              return (
                <div key={key}>
                  <p className="text-[11px] text-ink-3">{label}</p>
                  {rows.length ? (
                    <table className="mt-1 w-full text-[12px]">
                      <tbody>
                        {rows.slice(0, 6).map(([k, v]) => (
                          <tr key={k} className="border-b border-line last:border-0">
                            <td className="py-1 pr-2 text-ink-2">{k}</td>
                            <td className="tnum py-1 pr-2 text-right text-ink-3">{v.sent}</td>
                            {v.enough ? (
                              <>
                                <td className="tnum py-1 pr-2 text-right">{pct(v.reply_rate ?? 0)}</td>
                                <td className="tnum py-1 text-right">{pct(v.yes_rate ?? 0)} yes</td>
                              </>
                            ) : (
                              <td colSpan={2} className="py-1 text-right text-ink-3">not enough data yet</td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="mt-1 text-[12px] text-ink-3">not enough data yet</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {data.sent === 0 ? null : (
        <div className="grid gap-x-6 gap-y-4 px-4 py-4 md:grid-cols-2 xl:grid-cols-3">
          {SECTIONS.map(([key, label]) => {
            const b = data[key] as Bucket;
            const rows = Object.entries(b ?? {});
            if (!rows.length) return null;
            return (
              <div key={key}>
                <p className="marginal">{label}</p>
                <table className="mt-1.5 w-full text-[12px]">
                  <tbody>
                    {rows.slice(0, 8).map(([k, v]) => (
                      <tr key={k} className="border-b border-line last:border-0">
                        <td className="py-1 pr-2 text-ink-2">{k}</td>
                        <td className="tnum py-1 pr-2 text-right text-ink-3">{v.sent}</td>
                        <td className="tnum py-1 pr-2 text-right">{pct(v.reply_rate)}</td>
                        <td className={`tnum py-1 text-right ${v.yes_rate > 0 ? "font-medium" : "text-ink-3"}`}>{pct(v.yes_rate)} yes</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
