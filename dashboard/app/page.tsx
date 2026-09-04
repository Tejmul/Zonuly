"use client";

/*  Landing.
 *
 *  The thesis is in the first screen and nowhere else: a wall of anonymous, grey
 *  engineers, and an instrument that keeps finding one of them. Every other section is
 *  quiet on purpose — the mosaic is the only place this page raises its voice.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type Lenis from "lenis";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import { ArrowRight, CircleSlash, Network, ShieldCheck } from "lucide-react";
import { useApi } from "@/lib/api";
import { ThemeToggle } from "@/components/theme";
import { Rights } from "@/components/rights";
import { FaceMosaic, SAMPLE_LEADS, type Lead } from "@/components/landing/mosaic";

gsap.registerPlugin(ScrollTrigger, useGSAP);

type NetStats = {
  stats: { companies: number; nodes: number; links: number; bets: number; near: number; chokepoints: number; bottlenecks: number };
};

/* Where the leads come from, tagged by what each channel is actually good for rather
   than numbered — the order is not a sequence, the jobs are different. */
const SOURCES = [
  {
    name: "Reddit",
    tag: "who to ask",
    span: "lg:col-span-2",
    body: "r/ExperiencedDevs, r/cscareerquestions and the company’s own subreddit. People say things about their employer here that never make it onto a profile page — who runs the team, what the interview loop is, whether the last two hires stayed.",
  },
  {
    name: "LinkedIn",
    tag: "still there?",
    span: "lg:col-span-2",
    body: "Public profiles only, read to settle one question: does this person still work there, and for how long. A lead we cannot place inside the company today is held back rather than guessed at.",
  },
  {
    name: "Hacker News",
    tag: "openings",
    span: "lg:col-span-2",
    body: "The monthly Who Is Hiring thread, parsed and folded into the atlas without duplicating a role already found elsewhere.",
  },
  {
    name: "Open source",
    tag: "proof of employment",
    span: "lg:col-span-3",
    body: "Commits, org membership and contributor history on GitHub. This is the channel that carries the most weight, because it is the only one where employment is demonstrated instead of asserted: a title on a profile is a claim, a commit merged into the company’s repo last month is evidence. It also tells us what someone actually works on, which is the difference between a cold ask and a specific one.",
  },
  {
    name: "The company’s own site",
    tag: "the arbiter",
    span: "lg:col-span-3",
    body: "Checked last and trusted over everything above it. A board can list a role for months after it is filled, so if the opening is not on the company’s own careers page, ZoNuLy does not count it as open — no matter how many aggregators still carry it.",
  },
];

/* Written as a ledger because that is what they are: fixed limits, not features. */
const LIMITS = [
  { term: "Messages sent without your approval", value: "0" },
  { term: "Outreach cap, per day", value: "25" },
  { term: "Claims shown without a source", value: "0" },
  { term: "Data leaving your machine", value: "none" },
];

const PRINCIPLES = [
  {
    icon: <CircleSlash size={15} />,
    title: "Underrated only",
    body: "Household names are rejected by name. Thousands of applications are already pending there — that is not where the odds are.",
  },
  {
    icon: <ShieldCheck size={15} />,
    title: "Nothing is invented",
    body: "Every number carries the sentence it was read from. A claim the company’s own pages do not back is labelled, not repeated.",
  },
  {
    icon: <Network size={15} />,
    title: "One person, not a list",
    body: "Leads are ranked by how close they sit to the role, and anyone whose employment we cannot show is held back.",
  },
];

export default function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const { data } = useApi<NetStats>("/api/network");
  const s = data?.stats;

  const [lead, setLead] = useState<{ lead: Lead; n: number }>({ lead: SAMPLE_LEADS[0], n: 1 });

  /* Smooth scroll is scoped to this page only. The dashboard is a working instrument and
     wants the operating system's scrolling, not a designed version of it. */
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let raf = 0;
    let cancelled = false;
    let lenis: Lenis | null = null;

    import("lenis").then(({ default: Ctor }) => {
      if (cancelled) return;
      lenis = new Ctor({ duration: 1.05, smoothWheel: true });
      // ScrollTrigger reads the scroll position itself, so it has to be told when
      // Lenis moves it rather than waiting for a native scroll event.
      lenis.on("scroll", ScrollTrigger.update);
      const loop = (t: number) => {
        lenis?.raf(t);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      lenis?.destroy();
    };
  }, []);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();

      mm.add("(prefers-reduced-motion: no-preference)", () => {
        /* One orchestrated arrival: the headline climbs out of its slots, then everything
           it supports follows. `fromTo` with clearProps, never `from` — a fetch resolving
           mid-flight must not be able to strand anything at opacity 0. */
        const intro = gsap.timeline({ delay: 0.35 });

        intro
          .fromTo(
            "[data-word]",
            { yPercent: 108 },
            { yPercent: 0, duration: 1.15, ease: "expo.out", stagger: 0.055, clearProps: "transform" },
          )
          .fromTo(
            "[data-hero-follow]",
            { opacity: 0, y: 16 },
            { opacity: 1, y: 0, duration: 0.8, ease: "power3.out", stagger: 0.08, clearProps: "all" },
            "-=0.75",
          )
          .fromTo(
            "[data-record]",
            { opacity: 0, clipPath: "inset(0 0 100% 0)" },
            { opacity: 1, clipPath: "inset(0 0 0% 0)", duration: 0.7, ease: "power3.out", clearProps: "all" },
            "-=0.4",
          );

        /* The crowd sinks a little slower than the page, so the hero has depth without
           anything actually being pinned. One pinned section per page is the budget, and
           this page spends it on nothing. */
        const par = gsap.to("[data-parallax]", {
          yPercent: 12,
          ease: "none",
          scrollTrigger: { trigger: "[data-hero]", start: "top top", end: "bottom top", scrub: 0.6 },
        });

        const reveals = gsap.utils.toArray<HTMLElement>("[data-reveal]").map((el) =>
          gsap.fromTo(
            el,
            { opacity: 0, y: 22 },
            {
              opacity: 1,
              y: 0,
              duration: 0.7,
              ease: "power3.out",
              clearProps: "all",
              scrollTrigger: { trigger: el, start: "top 88%" },
            },
          ),
        );

        return () => {
          intro.kill();
          par.scrollTrigger?.kill();
          par.kill();
          reveals.forEach((r) => {
            r.scrollTrigger?.kill();
            r.kill();
          });
        };
      });

      return () => mm.revert();
    },
    { scope: root },
  );

  return (
    <div ref={root} className="min-h-dvh">
      <SiteHeader />

      {/* ------------------------------------------------------------- hero */}
      <section data-hero className="hero relative flex min-h-[92svh] flex-col justify-end overflow-hidden lg:min-h-[100svh]">
        {/* On a phone the crowd gets a band of its own above the copy rather than sitting
            behind it: text over faces is unreadable at any scrim strength that still
            shows a face, and a reticle with nowhere clear to land has nothing to say.
            From lg up there is room for both and the mosaic fills the hero. */}
        <div data-parallax className="absolute inset-x-0 top-0 h-[38svh] lg:inset-0 lg:h-auto lg:-bottom-[12%]">
          <FaceMosaic onLead={(lead, n) => setLead({ lead, n })} avoid="[data-hero-copy],[data-record],[data-site-header]" />
        </div>

        <div className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-[41svh] pb-14 sm:pb-20 lg:grid lg:grid-cols-12 lg:items-end lg:pt-32 lg:pb-24">
          <div data-hero-copy className="lg:col-span-7">
            <p data-hero-follow className="eyebrow">The referral atlas</p>

            <h1 className="mt-5 max-w-[15ch] font-[family-name:var(--font-display)] text-[40px] leading-[0.98] font-semibold tracking-[-0.04em] text-balance sm:text-[58px] lg:text-[68px]">
              {"One of these people can walk your resume in.".split(" ").map((word, i) => (
                <span key={i} className="slot mr-[0.24em]">
                  <span data-word>{word}</span>
                </span>
              ))}
            </h1>

            <p data-hero-follow className="mt-6 max-w-[52ch] text-[14.5px] leading-[1.65] text-[var(--ground-ink-2)]">
              ZoNuLy reads Reddit, LinkedIn, open-source commits and the companies’ own
              careers pages. It finds funded companies you have not heard of, proves the
              hiring is real, and names the one person inside who could refer you. You
              approve every message before it leaves.
            </p>

            <div data-hero-follow className="mt-9 flex flex-wrap items-center gap-3">
              <Link
                href="/dashboard"
                className="group inline-flex h-11 cursor-pointer items-center gap-2 rounded-md bg-white px-6 text-[13px] font-medium text-[#08080a] transition-transform duration-200 hover:-translate-y-px"
              >
                Open the dashboard
                <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-0.5" />
              </Link>
              <Link
                href="#sources"
                className="inline-flex h-11 cursor-pointer items-center rounded-md border border-[var(--ground-line)] px-6 text-[13px] font-medium text-white transition-colors duration-200 hover:bg-white/8"
              >
                Where it looks
              </Link>
            </div>
          </div>

          {/* The reticle's read-out. Deliberately the shape of a real record and
              deliberately anonymous — a role and its proof, never a name. */}
          <div className="mt-14 hidden lg:col-span-5 lg:mt-0 lg:block lg:pl-10">
            <div data-record className="record plate ml-auto max-w-[19rem] p-4">
              <div className="flex items-baseline justify-between">
                <span className="eyebrow">Sample record</span>
                <span className="tnum text-[10px] text-[#9a9aa4]">{String(lead.n).padStart(3, "0")}</span>
              </div>
              <p className="mt-3 text-[15px] leading-tight font-medium">{lead.lead.role}</p>
              <div className="ledger mt-3 text-[#a3a3ad]">
                <span className="text-[#8d8d96]">tenure</span>
                <span>{lead.lead.tenure}</span>
                <span className="text-[#8d8d96]">found on</span>
                <span>{lead.lead.source}</span>
                <span className="text-[#8d8d96]">proof</span>
                <span className="text-white">{lead.lead.proof}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="relative z-10 border-t border-[var(--ground-line)]">
          <dl className="mx-auto grid max-w-6xl grid-cols-2 gap-y-7 px-6 py-8 sm:grid-cols-4">
            <Stat label="Companies mapped" value={s?.companies} />
            <Stat label="Chokepoints" value={s?.chokepoints} />
            <Stat label="Signals linked" value={s?.links} />
            <Stat label="Ready to ask" value={s?.bets} />
          </dl>
        </div>
      </section>

      {/* ---------------------------------------------------------- sources */}
      <section id="sources" className="mx-auto max-w-6xl scroll-mt-20 px-6 py-24 sm:py-32">
        <p data-reveal className="eyebrow">Where the leads come from</p>
        <h2
          data-reveal
          className="mt-4 max-w-[22ch] font-[family-name:var(--font-display)] text-[30px] leading-[1.06] font-semibold tracking-[-0.035em] text-balance sm:text-[40px]"
        >
          Five public sources, each one asked a different question.
        </h2>
        <p data-reveal className="mt-5 max-w-[58ch] text-[14px] leading-relaxed text-ink-2">
          Nothing here is bought, and nothing is behind a login. The value is not in having
          the data — anyone can read these — it is in what has to agree before a lead is
          shown to you at all.
        </p>

        <div className="mt-12 grid gap-px border border-line bg-line sm:grid-cols-2 lg:grid-cols-6">
          {SOURCES.map((src) => (
            <article key={src.name} data-reveal className={`source ${src.span}`}>
              <div className="flex items-baseline justify-between gap-4">
                <h3 className="text-[15px] font-semibold tracking-tight">{src.name}</h3>
                <span className="idx shrink-0">{src.tag}</span>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-ink-2">{src.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------- principles */}
      <section className="border-y border-line bg-surface-2/40 py-16 sm:py-20">
        <div className="mx-auto grid max-w-6xl gap-10 px-6 sm:grid-cols-3 sm:gap-0 sm:divide-x sm:divide-line">
          {PRINCIPLES.map((p) => (
            <div key={p.title} data-reveal className="sm:px-8 sm:first:pl-0 sm:last:pr-0">
              <span className="text-ink-3">{p.icon}</span>
              <h3 className="mt-4 text-[14px] font-semibold tracking-tight">{p.title}</h3>
              <p className="mt-2 text-[13px] leading-relaxed text-ink-2">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* -------------------------------------------------------------- end */}
      <section className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
        <div className="lg:grid lg:grid-cols-12 lg:items-end lg:gap-6">
        <div className="lg:col-span-7">
        <h2
          data-reveal
          className="max-w-[18ch] font-[family-name:var(--font-display)] text-[30px] leading-[1.04] font-semibold tracking-[-0.035em] text-balance sm:text-[44px]"
        >
          It runs on your machine. Nothing sends itself.
        </h2>
        <p data-reveal className="mt-5 max-w-[52ch] text-[14px] leading-relaxed text-ink-2">
          No account, no upload, no queue on somebody else’s server. Every draft stops at a
          review gate with your name on it, and there is a hard cap on how many go out in a
          day — because the fastest way to burn a network is to mail all of it at once.
        </p>
        <div data-reveal className="mt-9 flex flex-wrap items-center gap-3">
          <Link
            href="/dashboard"
            className="group inline-flex h-11 cursor-pointer items-center gap-2 rounded-md bg-ink px-6 text-[13px] font-medium text-on-ink transition-opacity duration-200 hover:opacity-85"
          >
            Open the dashboard
            <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-0.5" />
          </Link>
          <Link
            href="/atlas"
            className="inline-flex h-11 cursor-pointer items-center gap-2 rounded-md border border-line px-6 text-[13px] font-medium text-ink transition-colors duration-200 hover:bg-surface-2"
          >
            <Network size={15} />
            See the atlas
          </Link>
        </div>
        </div>

        {/* The three limits the product is not allowed to trade away. They belong on the
            page for the same reason they are in the code: they are the promise. */}
        <dl data-reveal className="lg:col-span-5 lg:pl-10">
          {LIMITS.map((l) => (
            <div key={l.term} className="flex justify-between gap-6 border-b border-line py-4 first:border-t">
              <dt className="text-[13px] font-medium">{l.term}</dt>
              <dd className="tnum shrink-0 text-[12px] text-ink-3">{l.value}</dd>
            </div>
          ))}
        </dl>
        </div>
      </section>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-10">
          <span className="font-mono text-[12px] font-semibold tracking-tight">ZoNuLy</span>
          <p className="max-w-[52ch] text-[11.5px] leading-relaxed text-ink-3">
            Photographs are of engineers at public startup events, shown unnamed. They
            illustrate the crowd the atlas searches, not the people in it.
          </p>
          <Rights className="w-full text-right sm:w-auto" />
        </div>
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------------ header */

function SiteHeader() {
  const [solid, setSolid] = useState(false);

  useEffect(() => {
    const onScroll = () => setSolid(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      data-site-header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        solid ? "border-b border-line bg-paper/85 backdrop-blur-xl" : "border-b border-transparent"
      }`}
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className={`font-mono text-[13px] font-semibold tracking-tight ${solid ? "text-ink" : "text-white"}`}>
          ZoNuLy
        </Link>
        <div className={`flex items-center gap-1 ${solid ? "text-ink" : "hero-chrome text-white"}`}>
          <Link
            href="#sources"
            className="hidden h-9 cursor-pointer items-center rounded-md px-3 text-[13px] font-medium opacity-80 transition-opacity hover:opacity-100 sm:inline-flex"
          >
            Where it looks
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

/* ------------------------------------------------------------------- stats */

function Stat({ label, value }: { label: string; value: number | undefined }) {
  const el = useRef<HTMLElement>(null);

  /* Counts up once, when the real number lands. An em-dash until then, so the row never
     claims a total it does not have yet. */
  useGSAP(
    () => {
      if (value === undefined || !el.current) return;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        el.current.textContent = value.toLocaleString();
        return;
      }
      const box = { n: 0 };
      gsap.to(box, {
        n: value,
        duration: 1.2,
        ease: "power2.out",
        onUpdate: () => {
          if (el.current) el.current.textContent = Math.round(box.n).toLocaleString();
        },
      });
    },
    { dependencies: [value] },
  );

  return (
    <div>
      <dd className="tnum text-[24px] leading-none font-medium tracking-[-0.02em] sm:text-[28px]">
        {value === undefined ? <span className="text-[#5a5a62]">—</span> : <span ref={el}>0</span>}
      </dd>
      <dt className="marginal mt-2.5">{label}</dt>
    </div>
  );
}
