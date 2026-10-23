/* The pieces every docs page is written with. Black and white like the rest of the app:
   state is carried by fill, rule and weight, never colour. Server components — only the
   copy button (code-copy.tsx) needs the browser. */

import Link from "next/link";
import { ArrowLeft, ArrowRight, Info, OctagonAlert, ShieldCheck, Lightbulb } from "lucide-react";
import { docBySlug, docHref, FLAT } from "@/lib/docs-nav";
import { CopyButton } from "@/components/docs/code-copy";

const slugify = (s: string) =>
  s.toLowerCase().replace(/[`'"’]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

const textOf = (n: React.ReactNode): string =>
  typeof n === "string" || typeof n === "number" ? String(n)
    : Array.isArray(n) ? n.map(textOf).join("")
      : n && typeof n === "object" && "props" in n ? textOf((n as { props: { children?: React.ReactNode } }).props.children) : "";

/* ------------------------------------------------------------------ the page */

/** A docs page: its section, title and summary from lib/docs-nav.ts, then the body,
 *  then the previous / next pages in reading order. */
export function Doc({ slug, hero, children }: { slug: string; hero?: React.ReactNode; children: React.ReactNode }) {
  const { page, prev, next } = docBySlug(slug);
  return (
    <>
      {hero ? <header className="mb-12 border-b border-line pb-10">{hero}</header> : (
      <header className="mb-10 border-b border-line pb-8">
        <div className="doc-label mb-3">{page?.section}</div>
        <h1 className="font-[family-name:var(--font-display)] text-[34px] leading-[1.08] font-semibold tracking-[-0.035em] sm:text-[40px]">
          {page?.title}
        </h1>
        {page?.blurb ? <p className="mt-3 max-w-[60ch] text-[16px] leading-relaxed text-ink-2">{page.blurb}</p> : null}
      </header>
      )}
      <div className="doc-body">{children}</div>
      <nav aria-label="More pages" className="mt-16 grid gap-3 border-t border-line pt-8 sm:grid-cols-2">
        {prev ? (
          <Link href={docHref(prev.slug)} className="group rounded-xl border border-line p-4 transition-colors hover:border-ink">
            <span className="doc-label flex items-center gap-1.5"><ArrowLeft size={11} /> Previous</span>
            <span className="mt-1.5 block text-[14px] font-medium">{prev.title}</span>
          </Link>
        ) : <span />}
        {next ? (
          <Link href={docHref(next.slug)} className="group rounded-xl border border-line p-4 text-right transition-colors hover:border-ink">
            <span className="doc-label flex items-center justify-end gap-1.5">Next <ArrowRight size={11} /></span>
            <span className="mt-1.5 block text-[14px] font-medium">{next.title}</span>
          </Link>
        ) : null}
      </nav>
    </>
  );
}

/* ------------------------------------------------------------------ type */

export function H2({ children, id }: { children: React.ReactNode; id?: string }) {
  const text = textOf(children);
  const anchor = id ?? slugify(text);
  // data-toc carries the heading's own words: the "#" link inside it is not part of the title
  return (
    <h2 id={anchor} data-toc={text} className="group mt-14 mb-4 scroll-mt-20 font-[family-name:var(--font-display)] text-[24px] leading-tight font-semibold tracking-[-0.025em] first:mt-0">
      {children}
      <a href={`#${anchor}`} aria-label="Link to this section" className="ml-2 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100">#</a>
    </h2>
  );
}

export function H3({ children, id }: { children: React.ReactNode; id?: string }) {
  const text = textOf(children);
  const anchor = id ?? slugify(text);
  return (
    <h3 id={anchor} data-toc={text} className="group mt-9 mb-3 scroll-mt-20 text-[17px] font-semibold tracking-[-0.01em]">
      {children}
      <a href={`#${anchor}`} aria-label="Link to this section" className="ml-2 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100">#</a>
    </h3>
  );
}

export function P({ children }: { children: React.ReactNode }) {
  return <p className="my-4 max-w-[70ch] text-[16px] leading-[1.75] text-ink-2 sm:text-[15px] [&_strong]:font-semibold [&_strong]:text-ink">{children}</p>;
}

export function Lead({ children }: { children: React.ReactNode }) {
  return <p className="my-5 max-w-[66ch] text-[17px] leading-[1.7] text-ink [&_strong]:font-semibold">{children}</p>;
}

export function UL({ children }: { children: React.ReactNode }) {
  return (
    <ul className="my-4 max-w-[70ch] space-y-2 text-[16px] leading-[1.7] text-ink-2 sm:text-[15px] [&>li]:relative [&>li]:pl-5 [&>li]:before:absolute [&>li]:before:top-[0.72em] [&>li]:before:left-1 [&>li]:before:h-[5px] [&>li]:before:w-[5px] [&>li]:before:rounded-full [&>li]:before:bg-ink-3 [&_strong]:font-semibold [&_strong]:text-ink">
      {children}
    </ul>
  );
}

/** Inline code. */
export function C({ children }: { children: React.ReactNode }) {
  return <code className="rounded-[5px] border border-line bg-surface-2 px-1.5 py-px font-mono text-[12.5px] text-ink">{children}</code>;
}

export function A({ href, children }: { href: string; children: React.ReactNode }) {
  const external = /^https?:/.test(href);
  return external ? (
    <a href={href} target="_blank" rel="noreferrer" className="font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">{children}</a>
  ) : (
    <Link href={href} className="font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">{children}</Link>
  );
}

/* ------------------------------------------------------------------ blocks */

const CALLOUT = {
  note: { icon: Info, label: "Note" },
  tip: { icon: Lightbulb, label: "Tip" },
  rule: { icon: ShieldCheck, label: "Rule" },
  warn: { icon: OctagonAlert, label: "Careful" },
} as const;

export function Callout({ kind = "note", title, children }: { kind?: keyof typeof CALLOUT; title?: string; children: React.ReactNode }) {
  const { icon: Icon, label } = CALLOUT[kind];
  const strong = kind === "rule" || kind === "warn";
  return (
    <div className={`my-6 flex gap-3 rounded-xl border p-4 ${strong ? "border-ink bg-surface" : "border-line bg-surface-2"}`}>
      <Icon size={17} strokeWidth={1.9} className="mt-0.5 shrink-0 text-ink" />
      <div className="min-w-0 text-[14px] leading-[1.7] text-ink-2 [&_strong]:font-semibold [&_strong]:text-ink [&>p]:mt-1">
        <div className="mb-0.5 text-[13px] font-semibold text-ink">{title ?? label}</div>
        {children}
      </div>
    </div>
  );
}

export function Code({ children, title, lang = "bash" }: { children: string; title?: string; lang?: string }) {
  const text = children.replace(/^\n/, "").replace(/\s+$/, "");
  return (
    <div className="group my-5 overflow-hidden rounded-xl border border-line bg-surface">
      <div className="flex h-9 items-center justify-between border-b border-line bg-surface-2 px-3.5">
        <span className="doc-label">{title ?? lang}</span>
        <CopyButton text={text} />
      </div>
      <pre className="overflow-x-auto px-4 py-3.5 font-mono text-[12.5px] leading-[1.75] text-ink"><code>{text}</code></pre>
    </div>
  );
}

/** A table. `head` is the column titles; each row is the cells, which may be nodes. */
export function Table({ head, rows, mono = [] }: { head: string[]; rows: React.ReactNode[][]; mono?: number[] }) {
  return (
    <div className="my-6 overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[520px] border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-line bg-surface-2">
            {head.map((h) => <th key={h} className="doc-label px-4 py-2.5 font-normal">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {rows.map((r, i) => (
            <tr key={i} className="align-top">
              {r.map((cell, j) => (
                <td key={j} className={`px-4 py-2.5 leading-relaxed ${j === 0 ? "font-medium text-ink" : "text-ink-2"} ${mono.includes(j) ? "font-mono text-[12px]" : ""}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Numbered steps, each a title and a body. */
export function Steps({ children }: { children: React.ReactNode }) {
  return <ol className="my-6 [counter-reset:step]">{children}</ol>;
}

export function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className="relative border-l border-line pb-7 pl-9 [counter-increment:step] last:border-transparent last:pb-0 before:absolute before:-left-[13px] before:top-0 before:grid before:h-[26px] before:w-[26px] before:place-items-center before:rounded-full before:border before:border-ink before:bg-surface before:font-mono before:text-[11px] before:text-ink before:content-[counter(step)]">
      <div className="text-[14.5px] font-semibold leading-[26px]">{title}</div>
      <div className="mt-1 text-[14px] leading-[1.7] text-ink-2 [&_strong]:text-ink">{children}</div>
    </li>
  );
}

/** A grid of links to other pages, each with its summary from the nav. */
export function PageCards({ slugs }: { slugs: string[] }) {
  return (
    <div className="my-6 grid gap-3 sm:grid-cols-2">
      {slugs.map((s) => {
        const p = FLAT.find((x) => x.slug === s);
        if (!p) return null;
        return (
          <Link key={s} href={docHref(s)} className="group rounded-xl border border-line bg-surface p-4 transition-colors hover:border-ink">
            <span className="doc-label">{p.section}</span>
            <span className="mt-1.5 flex items-center gap-1.5 text-[14.5px] font-semibold">
              {p.title}
              <ArrowRight size={13} className="text-ink-muted transition-transform group-hover:translate-x-0.5" />
            </span>
            <span className="mt-1 block text-[12.5px] leading-relaxed text-ink-2">{p.blurb}</span>
          </Link>
        );
      })}
    </div>
  );
}

/** A row of figures: big number, label, a line of context. */
export function Stats({ items }: { items: { n: string; label: string; hint?: string }[] }) {
  return (
    <div className="my-6 grid grid-cols-2 overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4 [&>*]:bg-surface" style={{ gap: 1 }}>
      {items.map((s) => (
        <div key={s.label} className="p-4">
          <div className="doc-label">{s.label}</div>
          <div className="tnum mt-2 text-[26px] leading-none font-semibold tracking-tight">{s.n}</div>
          {s.hint ? <div className="mt-2 text-[11.5px] leading-snug text-ink-muted">{s.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

/** Built / partly built / planned — the status of a feature, by fill. */
export function Status({ s }: { s: "built" | "partial" | "gap" | "off" }) {
  const map = { built: "chip chip-solid", partial: "chip", gap: "chip", off: "chip chip-strike" } as const;
  const label = { built: "built", partial: "partial", gap: "planned", off: "off" }[s];
  return <span className={map[s]}>{label}</span>;
}

/** Term → meaning pairs, for vocabularies and option lists. */
export function Defs({ items }: { items: [React.ReactNode, React.ReactNode][] }) {
  return (
    <dl className="my-6 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
      {items.map(([t, d], i) => (
        <div key={i} className="grid gap-1 px-4 py-3 sm:grid-cols-[200px_1fr] sm:gap-4">
          <dt className="text-[13.5px] font-semibold text-ink">{t}</dt>
          <dd className="text-[13.5px] leading-relaxed text-ink-2 [&_strong]:text-ink">{d}</dd>
        </div>
      ))}
    </dl>
  );
}
