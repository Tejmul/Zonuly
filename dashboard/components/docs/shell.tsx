"use client";

/* The docs chrome that needs the browser: the section sidebar (and its phone-width
   drawer), the ⌘K search over every page, and the "On this page" list that follows
   the reader's scroll. Everything it lists comes from lib/docs-nav.ts. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Menu, Search, X } from "lucide-react";
import { DOCS, FLAT, docHref } from "@/lib/docs-nav";

function useActiveSlug() {
  const path = usePathname();
  return path === "/docs" ? "" : path.replace(/^\/docs\//, "").replace(/\/$/, "");
}

function NavList({ onPick, roomy = false }: { onPick?: () => void; roomy?: boolean }) {
  const active = useActiveSlug();
  return (
    <nav aria-label="Documentation" className="flex flex-col gap-6">
      {DOCS.map((s) => (
        <div key={s.title}>
          <div className="doc-label mb-2 px-2.5">{s.title}</div>
          <ul className="flex flex-col gap-px border-l border-line">
            {s.pages.map((p) => {
              const on = p.slug === active;
              // the pipeline's stages are a real sequence: stations on the line, and the one
              // human step (the review gate) is the filled one
              const station = s.title === "The pipeline" && p.slug !== "pipeline";
              const human = p.slug === "outreach";
              return (
                <li key={p.slug} className="relative">
                  {station ? (
                    <span
                      aria-hidden
                      title={human ? "The one human step" : undefined}
                      className={`absolute top-1/2 -left-[4px] z-10 h-[7px] w-[7px] -translate-y-1/2 rounded-full border border-ink ${human ? "bg-ink" : "bg-paper"}`}
                    />
                  ) : null}
                  <Link
                    href={docHref(p.slug)}
                    onClick={onPick}
                    aria-current={on ? "page" : undefined}
                    className={`-ml-px flex items-center border-l pr-2 pl-3.5 transition-colors duration-150 ${roomy ? "min-h-11 text-[15px]" : "min-h-9 text-[13px]"} ${
                      on ? "border-ink font-medium text-ink" : "border-transparent text-ink-2 hover:border-line-strong hover:text-ink"
                    }`}
                  >
                    {p.title}
                    {human ? <span className="sr-only"> (the one human step)</span> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Laptop: a sticky column. Phone: hidden — DocsMobileNav opens the same list. */
export function DocsSidebar() {
  return (
    <aside className="sticky top-14 hidden h-[calc(100dvh-3.5rem)] w-[248px] shrink-0 overflow-y-auto border-r border-line px-4 py-8 lg:block">
      <NavList />
    </aside>
  );
}

export function DocsMobileNav() {
  const [open, setOpen] = useState(false);
  const active = useActiveSlug();
  const page = FLAT.find((p) => p.slug === active);
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    if (open) window.addEventListener("keydown", esc);
    return () => { document.body.style.overflow = ""; window.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="border-b border-line lg:hidden">
      <button
        onClick={() => setOpen(true)}
        className="flex h-11 w-full cursor-pointer items-center gap-2 px-4 text-left text-[13px] text-ink-2"
        aria-expanded={open}
      >
        <Menu size={15} />
        <span className="text-ink-muted">{page?.section}</span>
        <span className="text-ink-muted">/</span>
        <span className="truncate font-medium text-ink">{page?.title ?? "Docs"}</span>
      </button>
      {open ? (
        <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true" aria-label="Documentation menu">
          <button aria-label="Close the menu" tabIndex={-1} className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="relative h-full w-[86%] max-w-[320px] overflow-y-auto border-r border-line bg-surface px-4 py-5">
            <button
              onClick={() => setOpen(false)}
              aria-label="Close the menu"
              autoFocus
              className="mb-5 grid h-11 w-11 cursor-pointer place-items-center rounded-md border border-line text-ink-2"
            >
              <X size={15} />
            </button>
            {/* the header's "Open the app" is hidden at phone width — the way back lives here */}
            <Link
              href="/dashboard"
              onClick={() => setOpen(false)}
              className="mb-6 flex min-h-11 items-center gap-2 rounded-md border border-line px-3 text-[15px] font-medium text-ink transition-colors hover:border-ink"
            >
              <ArrowLeft size={15} /> Back to the app
            </Link>
            <NavList onPick={() => setOpen(false)} roomy />
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** ⌘K / Ctrl-K (or "/") opens a filter over every page's title, summary and keywords. */
export function DocsSearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const router = useRouter();
  const show = () => { setQ(""); setSel(0); setOpen(true); };

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, [contenteditable]");
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setQ("");
        setSel(0);
        setOpen(true);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  const hits = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return FLAT;
    return FLAT.filter((p) => {
      const hay = `${p.title} ${p.blurb} ${p.section} ${p.keywords ?? ""}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [q]);

  const go = (slug: string) => {
    setOpen(false);
    router.push(docHref(slug));
  };
  // arrow keys move the highlight; keep it in view when the list is longer than the box
  useEffect(() => {
    if (open) document.querySelector(`[data-hit="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel, open]);

  return (
    <>
      <button
        onClick={show}
        className="flex h-9 w-full max-w-[320px] cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-3 text-[13px] text-ink-muted transition-colors hover:border-line-strong"
      >
        <Search size={14} />
        <span className="flex-1 text-left">Search the docs</span>
        <kbd className="tnum hidden rounded border border-line px-1.5 text-[10px] text-ink-muted sm:inline">⌘K</kbd>
      </button>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="Search the docs">
          <button aria-label="Close search" tabIndex={-1} className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={() => setOpen(false)} />
          <div className="relative w-full max-w-[560px] overflow-hidden rounded-xl border border-line bg-surface shadow-2xl">
            <div className="flex items-center gap-2 border-b border-line px-4">
              <Search size={15} className="text-ink-muted" />
              <input
                autoFocus
                aria-label="Search the docs"
                value={q}
                onChange={(e) => { setQ(e.target.value); setSel(0); }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)); }
                  if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
                  if (e.key === "Enter" && hits[sel]) go(hits[sel].slug);
                }}
                placeholder="Sending pacing, Apify, review gate…"
                className="h-12 min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-ink-muted"
              />
              <kbd className="tnum rounded border border-line px-1.5 text-[10px] text-ink-muted">esc</kbd>
            </div>
            <ul className="max-h-[52vh] overflow-y-auto p-1.5">
              {hits.length === 0 ? (
                <li className="px-3 py-6 text-center text-[13px] text-ink-2">
                  No page matches “{q}”. Try{" "}
                  {["sending", "apify", "review gate", "config"].map((w, i) => (
                    <span key={w}>
                      {i ? ", " : ""}
                      <button onClick={() => { setQ(w); setSel(0); }} className="cursor-pointer font-medium text-ink underline underline-offset-2">{w}</button>
                    </span>
                  ))}
                  .
                </li>
              ) : null}
              {hits.map((p, i) => (
                <li key={p.slug}>
                  {/* a real link: it can be opened in a new tab, and it reads as one to a screen reader */}
                  <Link
                    href={docHref(p.slug)}
                    data-hit={i}
                    onMouseEnter={() => setSel(i)}
                    onClick={() => setOpen(false)}
                    className={`flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-left ${i === sel ? "bg-surface-2" : ""}`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline gap-2">
                        <span className="text-[13.5px] font-medium">{p.title}</span>
                        <span className="doc-label">{p.section}</span>
                      </span>
                      <span className="block truncate text-[12px] text-ink-2">{p.blurb}</span>
                    </span>
                    {i === sel ? <ArrowRight size={14} className="shrink-0 text-ink-muted" /> : null}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}

/** "On this page": the article's h2 / h3 (any heading with data-toc), the one in view lit. */
export function DocsToc() {
  const path = usePathname();
  const [items, setItems] = useState<{ id: string; text: string; level: number }[]>([]);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const hs = Array.from(document.querySelectorAll<HTMLElement>("article [data-toc]"));
    const raf = requestAnimationFrame(() =>
      setItems(hs.map((h) => ({
        id: h.id,
        // the heading's own words (data-toc), never the "#" section link that sits inside it
        text: h.dataset.toc || (h.textContent ?? "").replace(/#$/, "").trim(),
        level: h.tagName === "H3" ? 3 : 2,
      }))));
    if (!hs.length) return () => cancelAnimationFrame(raf);
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => seen.set(e.target.id, e.isIntersecting));
        const first = hs.find((h) => seen.get(h.id));
        if (first) setCurrent(first.id);
      },
      { rootMargin: "-72px 0px -60% 0px" },
    );
    hs.forEach((h) => io.observe(h));
    return () => { cancelAnimationFrame(raf); io.disconnect(); };
  }, [path]);

  if (items.length < 2) return <div className="hidden w-[200px] shrink-0 xl:block" />;
  return (
    <aside className="sticky top-14 hidden h-[calc(100dvh-3.5rem)] w-[200px] shrink-0 overflow-y-auto py-10 xl:block">
      <div className="doc-label mb-3">On this page</div>
      <ul className="flex flex-col gap-1 border-l border-line text-[12.5px]">
        {items.map((it) => (
          <li key={it.id} className={it.level === 3 ? "pl-2.5" : ""}>
            <a
              href={`#${it.id}`}
              aria-current={current === it.id ? "location" : undefined}
              className={`-ml-px block border-l py-0.5 pl-3 leading-snug transition-colors ${current === it.id ? "border-ink font-medium text-ink" : "border-transparent text-ink-muted hover:text-ink"}`}
            >
              {it.text}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
