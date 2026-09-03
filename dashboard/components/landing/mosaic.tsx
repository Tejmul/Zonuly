"use client";

/*  The face mosaic.
 *
 *  The hero's whole argument in one object: a crowd of engineers, drifting, grey and
 *  anonymous — and then the instrument finds one. Colour returns to exactly one tile at a
 *  time, and it is the only colour anywhere on the page. That is the product: not a list,
 *  one person.
 *
 *  The reticle is not a floating overlay chasing a moving target. Each tile owns its own
 *  set of corner brackets and one tile at a time is marked `data-on`, so the marks travel
 *  with the photograph for free and nothing has to be re-measured per frame.
 */

import { useRef, useState } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { FACES, PORTRAITS } from "./faces";

/* Seeded shuffle, not Math.random: the server and the client must lay out the same grid
   or React throws away the markup it just streamed. */
function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const COLUMNS = 8;
const PER_COLUMN = 9;

const grid = (() => {
  const rand = mulberry32(0x2f0a17);
  const pool = [...FACES];
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return Array.from({ length: COLUMNS }, (_, c) =>
    Array.from({ length: PER_COLUMN }, (_, r) => pool[(c * PER_COLUMN + r) % pool.length]),
  );
})();

/** Only the last four columns are wide enough to matter on a phone. */
const COLUMN_VISIBILITY = [
  "flex", "flex", "flex", "flex",
  "hidden sm:flex", "hidden sm:flex", "hidden xl:flex", "hidden xl:flex",
];

export type Lead = {
  role: string;
  tenure: string;
  source: string;
  proof: string;
};

/* Illustrative records, not real people — no name is ever attached to a face. Each one is
   a shape the pipeline actually produces: a role, how long they have held it, the channel
   that surfaced them, and the sentence that proves they still work there. */
export const SAMPLE_LEADS: Lead[] = [
  { role: "Staff Engineer",    tenure: "4 yrs in seat",  source: "open source", proof: "merged a commit last week" },
  { role: "Engineering Manager", tenure: "2 yrs in seat", source: "LinkedIn",   proof: "careers page names the team" },
  { role: "Founding Engineer", tenure: "6 yrs in seat",  source: "Hacker News", proof: "posted the role themselves" },
  { role: "Infrastructure Lead", tenure: "3 yrs in seat", source: "Reddit",     proof: "answers in r/ExperiencedDevs" },
  { role: "Senior Engineer",   tenure: "18 mo in seat",  source: "open source", proof: "org member on GitHub" },
];

export function FaceMosaic({
  onLead,
  avoid,
}: {
  onLead?: (lead: Lead, n: number) => void;
  /** Selector for everything drawn over the mosaic — headline, record, header. The
   *  reticle lands anywhere on screen that these leave free, which is what puts it
   *  beside the headline on a desktop and above it on a phone without a single
   *  breakpoint being written down. Measured, so it survives a copy edit. */
  avoid?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useGSAP(
    () => {
      const el = root.current;
      if (!el) return;

      const tiles = gsap.utils.toArray<HTMLElement>("[data-tile]", el);
      const portraits = gsap.utils.toArray<HTMLElement>("[data-portrait]", el);
      const tracks = gsap.utils.toArray<HTMLElement>("[data-track]", el);

      const mm = gsap.matchMedia();

      /* Reduced motion keeps the picture and drops every moving part: the grid arrives,
         one face is already lit, and nothing drifts or hunts. */
      mm.add("(prefers-reduced-motion: reduce)", () => {
        gsap.set(tiles, { opacity: 1, scale: 1 });
        portraits[Math.floor(portraits.length * 0.42)]?.setAttribute("data-on", "true");
        onLead?.(SAMPLE_LEADS[0], 1);
        setReady(true);
      });

      mm.add("(prefers-reduced-motion: no-preference)", () => {
        /* Columns drift at their own pace, alternating direction. Each track holds its
           tiles twice, so -50% is exactly one seam-free lap. */
        const drifts = tracks.map((track, i) => {
          const up = i % 2 === 0;
          track.dataset.dir = up ? "up" : "down";
          const tween = gsap.fromTo(
            track,
            { yPercent: up ? 0 : -50 },
            {
              yPercent: up ? -50 : 0,
              duration: 52 + (i % 4) * 11,
              ease: "none",
              repeat: -1,
            },
          );
          tween.progress((i * 0.137) % 1);
          return tween;
        });

        /* Entrance: the field assembles out of nothing, from the middle outwards. */
        const intro = gsap.timeline({ delay: 0.15 });
        intro.fromTo(
          tiles,
          { opacity: 0, scale: 0.7 },
          {
            opacity: 1,
            scale: 1,
            duration: 0.9,
            ease: "expo.out",
            stagger: { each: 0.007, from: "random" },
          },
        );

        /* The hunt. Every beat it re-measures which faces are actually on screen and
           clear of the headline, then locks onto one of them. */
        let current: HTMLElement | null = null;
        let n = 0;

        const pick = () => {
          /* Where a tile is allowed to be found: on screen AND inside the mosaic's own
             visible box. The two are the same on a desktop, but from lg down the mosaic
             is a short band with overflow hidden, and getBoundingClientRect happily
             reports tiles that scrolled out of that band — locking onto one lights up a
             face nobody can see. */
          const frame = el.getBoundingClientRect();
          const box = {
            left: Math.max(8, frame.left),
            top: Math.max(8, frame.top),
            right: Math.min(window.innerWidth - 8, frame.right),
            bottom: Math.min(window.innerHeight - 8, frame.bottom),
          };
          if (box.right <= box.left || box.bottom <= box.top) return null;
          /* document.querySelectorAll, not gsap.utils.toArray: useGSAP's context scopes
             selector strings to the mosaic, and every overlay worth avoiding is drawn
             outside it — the scoped lookup silently returned nothing. */
          const GUTTER = 16;
          const blocked = avoid
            ? Array.from(document.querySelectorAll<HTMLElement>(avoid))
                .map((n) => n.getBoundingClientRect())
                .filter((r) => r.width > 0 && r.height > 0)
                .map((r) => ({
                  left: r.left - GUTTER,
                  right: r.right + GUTTER,
                  top: r.top - GUTTER,
                  bottom: r.bottom + GUTTER,
                }))
            : [];

          const candidates = portraits.filter((t) => {
            const r = t.getBoundingClientRect();
            if (r.width === 0 || t === current) return false;
            /* The tile does not stay put — its column keeps drifting for the whole beat,
               so it is tested where it will have travelled to, not where it is. The
               direction is known per column, so only the leading edge is padded; padding
               both would exclude the entire right-hand side of the hero. A column covers
               half its own height in 52-85s, which is well under a tile per beat. */
            const drift = r.height * 0.8;
            const up = (t.closest("[data-track]") as HTMLElement | null)?.dataset.dir !== "down";
            const top = up ? r.top - drift : r.top;
            const bottom = up ? r.bottom : r.bottom + drift;

            /* Fully on screen: a tile clipped by the fold reads as a rendering fault,
               not a find. */
            if (r.left < box.left || r.right > box.right || top < box.top || bottom > box.bottom) return false;

            /* Whole tile clear of every overlay, not just its centre — a face half
               behind a panel still looks like a mistake. */
            return !blocked.some(
              (b) => r.left < b.right && r.right > b.left && top < b.bottom && bottom > b.top,
            );
          });
          if (!candidates.length) return null;
          return candidates[Math.floor(Math.random() * candidates.length)];
        };

        const lock = () => {
          const next = pick();
          if (!next) return false;
          current?.removeAttribute("data-on");
          next.setAttribute("data-on", "true");
          current = next;
          onLead?.(SAMPLE_LEADS[n % SAMPLE_LEADS.length], n + 1);
          n += 1;

          /* A hard, mechanical snap — the brackets land from outside the frame. */
          const ring = next.querySelector("[data-ring]");
          if (ring) {
            gsap.fromTo(
              ring,
              { opacity: 0, scale: 1.5 },
              {
                opacity: 1,
                scale: 1,
                duration: 0.5,
                ease: "power4.out",
                overwrite: true,
                /* Without this the inline opacity outlives `data-on` and the brackets
                   stay lit on a tile the reticle has already left. */
                clearProps: "all",
              },
            );
          }
          return true;
        };

        /* Self-scheduling rather than a fixed metronome. Sometimes nothing is free — the
           headline and the record between them can cover every candidate for a moment —
           and holding the previous face for a second full beat lets its column carry it
           twice as far as the drift padding allowed for, straight into the copy it was
           picked to avoid. So a failed beat retries almost immediately instead. */
        const BEAT = 3.4;
        let pending: gsap.core.Tween | null = null;
        const run = () => {
          pending = gsap.delayedCall(lock() ? BEAT : 0.5, run);
        };
        pending = gsap.delayedCall(1.9, run);

        /* One slow pass of light across the field, so the grid reads as something being
           looked through rather than a static wall. */
        const scan = gsap.fromTo(
          "[data-scan]",
          { yPercent: -110, opacity: 0 },
          { yPercent: 1100, opacity: 1, duration: 7, ease: "none", repeat: -1, repeatDelay: 5 },
        );

        setReady(true);

        return () => {
          drifts.forEach((d) => d.kill());
          intro.kill();
          pending?.kill();
          scan.kill();
        };
      });

      return () => mm.revert();
    },
    { scope: root, dependencies: [] },
  );

  return (
    <div ref={root} aria-hidden className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-x-0 -top-[12%] flex h-[130%] gap-[6px] px-[6px] sm:gap-2 sm:px-2">
        {grid.map((column, c) => (
          <div key={c} className={`${COLUMN_VISIBILITY[c]} min-w-0 flex-1 flex-col`}>
            <div data-track className="flex flex-col gap-[6px] sm:gap-2 will-change-transform">
              {[...column, ...column].map((face, i) => (
                <figure
                  key={i}
                  data-tile
                  data-portrait={PORTRAITS.has(face) ? "" : undefined}
                  className="tile aspect-square w-full shrink-0"
                >
                  {/* Decorative: the crowd is the message, no single photograph is.
                      Plain <img> on purpose — these are already WebP cropped to exactly
                      the size they render at, so next/image would optimise nothing and
                      add 126 wrappers to the hero's critical path. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={`/faces/${face}.webp`} alt="" loading={c < 4 ? "eager" : "lazy"} width={360} height={360} />
                  <span data-ring className="ring">
                    <i />
                  </span>
                </figure>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div data-scan className="pointer-events-none absolute inset-x-0 top-0 h-px opacity-0 mix-blend-screen"
        style={{ background: "linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent)" }} />

      <div className={`hero-scrim ${ready ? "" : "opacity-100"}`} />
      <div className="hero-grain" />
    </div>
  );
}
