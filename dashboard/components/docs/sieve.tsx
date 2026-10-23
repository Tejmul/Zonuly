"use client";

/*  The sieve — the docs' front page in one object.
 *
 *  A plate of 600 points (the companies, roughly) and a ladder of the pipeline's own
 *  live readings. Each rung that lights up lets fewer points through, until one is left
 *  and it gets the ring from the ZoNuLy mark: one person to ask. The landing page makes
 *  the same argument with photographs; this is the instrument's version of it.
 *
 *  Which points survive is fixed by a seeded rank, so server and client draw the same
 *  plate. Reduced motion shows the end state and nothing moves.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";

type Overview = {
  companies_all?: number;
  companies?: number;
  jobs?: { jobs?: number };
  quota?: { daily_cap?: number };
};

// Figures read from /api/overview on 23 Oct 2026 — shown until (or unless) the API answers.
// The page never waits for the API: a sleeping backend can take half a minute to wake.
const FALLBACK = { roles: 8085, companies: 5910, ready: 574, perDay: 25 };
// Past this the label stops saying "reading" and owns up to the dated figures; a late
// answer still replaces them.
const PATIENCE_MS = 12_000;

// Share of the plate still lit at each rung: chosen on a log scale so every step reads.
const KEEP = [1, 0.74, 0.3, 0.07, 0];
const N = 600;
const COLS = 30;
const ROWS = N / COLS;
const STEP_MS = 900;

function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const fmt = (n: number) => n.toLocaleString("en-IN");

export function Sieve() {
  const [nums, setNums] = useState(FALLBACK);
  const [live, setLive] = useState<"reading" | "live" | "dated">("reading");
  const [step, setStep] = useState(0);
  const [run, setRun] = useState(0);
  const plate = useRef<HTMLDivElement>(null);

  // the plate: a jittered grid, each point with a fixed rank — the lowest rank is the one
  const points = useMemo(() => {
    const rnd = mulberry32(19);
    const pts = Array.from({ length: N }, (_, i) => {
      const c = i % COLS, r = Math.floor(i / COLS);
      return {
        x: 10 + c * (380 / (COLS - 1)) + (rnd() - 0.5) * 7,
        y: 10 + r * (230 / (ROWS - 1)) + (rnd() - 0.5) * 7,
        rank: rnd(),
      };
    });
    const one = pts.reduce((a, b, i) => (b.rank < pts[a].rank ? i : a), 0);
    return { pts, one };
  }, []);

  useEffect(() => {
    let alive = true;
    const late = window.setTimeout(() => { if (alive) setLive((l) => (l === "reading" ? "dated" : l)); }, PATIENCE_MS);
    api.get<Overview>("/api/overview").then(
      (o) => {
        if (!alive) return;
        if (!o?.companies_all) { setLive("dated"); return; }
        setNums({
          roles: o.jobs?.jobs ?? FALLBACK.roles,
          companies: o.companies_all,
          ready: o.companies ?? FALLBACK.ready,
          perDay: o.quota?.daily_cap ?? FALLBACK.perDay,
        });
        setLive("live");
      },
      () => { if (alive) setLive("dated"); },
    );
    return () => { alive = false; window.clearTimeout(late); };
  }, []);

  // play once the plate is on screen; reduced motion jumps to the end
  useEffect(() => {
    const el = plate.current;
    if (!el) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const timers: number[] = [];
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      if (still) { timers.push(window.setTimeout(() => setStep(KEEP.length), 0)); return; }
      for (let s = 1; s <= KEEP.length; s++) timers.push(window.setTimeout(() => setStep(s), 500 + s * STEP_MS));
    }, { threshold: 0.35 });
    io.observe(el);
    return () => { io.disconnect(); timers.forEach(clearTimeout); };
  }, [run]);

  const rungs = [
    { n: fmt(nums.roles), label: "roles read", note: "from company-owned boards and careers pages" },
    { n: fmt(nums.companies), label: "companies tracked", note: "graded on funding, pay power and hiring" },
    { n: fmt(nums.ready), label: "complete", note: "hiring proven, and someone there to ask" },
    { n: String(nums.perDay), label: "asks a day", note: "spread through the working day" },
    { n: "1", label: "person, one email", note: "sent only after you approve it" },
  ];
  const done = step >= KEEP.length;
  const keep = KEEP[Math.min(Math.max(step - 1, 0), KEEP.length - 1)];

  return (
    <figure className="grid gap-8 md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] md:items-center md:gap-10">
      <div ref={plate} className="relative">
        <svg viewBox="0 0 400 250" role="img" aria-label={`A plate of ${N} points narrowing to one: ${rungs.map((r) => `${r.n} ${r.label}`).join(", then ")}.`} className="block w-full text-ink">
          <defs>
            <pattern id="sieve-grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M20 0H0V20" fill="none" stroke="var(--grid)" strokeWidth="1" />
            </pattern>
          </defs>
          <rect width="400" height="250" fill="url(#sieve-grid)" />
          {points.pts.map((p, i) => {
            const isOne = i === points.one;
            const lit = step === 0 || (isOne ? true : p.rank < keep);
            return (
              <circle
                key={i}
                cx={p.x}
                cy={p.y}
                r={isOne && done ? 3.4 : 1.7}
                fill="currentColor"
                style={{
                  opacity: lit ? (step === 0 ? 0.55 : 0.9) : 0.08,
                  transition: "opacity 600ms cubic-bezier(0.16,1,0.3,1)",
                }}
              />
            );
          })}
          {/* the ring — the end of the route in the ZoNuLy mark */}
          <circle
            cx={points.pts[points.one].x}
            cy={points.pts[points.one].y}
            r="11"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            style={{ opacity: done ? 1 : 0, transform: done ? "scale(1)" : "scale(2.2)", transformOrigin: `${points.pts[points.one].x}px ${points.pts[points.one].y}px`, transition: "opacity 500ms ease-out, transform 700ms cubic-bezier(0.16,1,0.3,1)" }}
          />
        </svg>
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="doc-label" aria-live="polite">
            {live === "live" ? "Live · from your pipeline" : live === "reading" ? "Figures of 23 Oct 2026 · reading live…" : "Figures of 23 Oct 2026"}
          </span>
          <button
            onClick={() => { setStep(0); setRun((r) => r + 1); }}
            className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-[12px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <RotateCcw size={12} /> Replay
          </button>
        </div>
      </div>

      <ol className="relative border-l border-line" aria-label="From roles to one person">
        {rungs.map((r, i) => {
          const on = step > i;
          const now = step === i + 1 || (done && i === rungs.length - 1);
          return (
            <li key={r.label} className="relative py-2.5 pl-5">
              <span
                aria-hidden
                className={`absolute top-[18px] -left-[4.5px] h-2 w-2 rounded-full border transition-colors duration-500 ${on ? "border-ink bg-ink" : "border-line-strong bg-paper"}`}
              />
              <div className={`flex items-baseline gap-2.5 transition-opacity duration-500 ${on || step === 0 ? "opacity-100" : "opacity-40"}`}>
                <span className={`tnum font-semibold tracking-tight ${i === rungs.length - 1 ? "text-[30px]" : "text-[22px]"} ${now ? "text-ink" : "text-ink-2"}`}>{r.n}</span>
                <span className={`text-[14px] ${now ? "font-medium text-ink" : "text-ink-2"}`}>{r.label}</span>
              </div>
              <div className="text-[12.5px] leading-snug text-ink-muted">{r.note}</div>
            </li>
          );
        })}
      </ol>
    </figure>
  );
}
