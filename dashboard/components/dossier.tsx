"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useApi } from "@/lib/api";

/* The research dossier for a company (reports/<name>-dossier.md, served by
   /api/companies/{id}/dossier): who to ask, where to apply, how to crack it, each fact with its
   source. Renders nothing when no dossier has been written. Collapsed past the first screen so
   the rest of the company page stays reachable. */

type DossierDoc = { markdown: string; path: string; updated_at: string };

/* react-markdown hands every component its AST `node`; it must not reach the DOM. */
function dom<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const rest = { ...props };
  delete rest.node;
  return rest;
}

export function Dossier({ companyId }: { companyId: number }) {
  const { data } = useApi<DossierDoc>(`/api/companies/${companyId}/dossier`);
  const [open, setOpen] = useState(false);
  if (!data?.markdown) return null;
  return (
    <section data-anim="sec" className="mt-6 plate px-4 py-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Dossier</h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            Who to ask, where to apply, how to crack it — every fact with its source. Updated {data.updated_at.replace("T", " ")}.
          </p>
        </div>
        <button type="button" onClick={() => setOpen(!open)} className="ml-auto shrink-0 cursor-pointer text-xs text-ink-2 hover:text-ink hover:underline">
          {open ? "Collapse" : "Read all"}
        </button>
      </div>
      <div className={`dossier relative mt-4 overflow-hidden ${open ? "" : "max-h-[34rem]"}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: () => null,
            h2: (p) => <h3 className="mt-5 mb-2 text-[13px] font-semibold text-ink" {...dom(p)} />,
            h3: (p) => <h4 className="mt-4 mb-1.5 text-[12.5px] font-semibold text-ink" {...dom(p)} />,
            p: (p) => <p className="my-2 text-[12.5px] leading-relaxed text-ink-2" {...dom(p)} />,
            ul: (p) => <ul className="my-2 list-disc pl-5 text-[12.5px] leading-relaxed text-ink-2" {...dom(p)} />,
            ol: (p) => <ol className="my-2 list-decimal pl-5 text-[12.5px] leading-relaxed text-ink-2" {...dom(p)} />,
            a: (p) => <a className="underline decoration-line-strong underline-offset-2 hover:text-ink" target="_blank" rel="noreferrer" {...dom(p)} />,
            strong: (p) => <strong className="font-semibold text-ink" {...dom(p)} />,
            hr: () => <hr className="my-4 border-line" />,
            code: (p) => <code className="rounded-sm bg-surface-2 px-1 font-mono text-[11.5px]" {...dom(p)} />,
            table: (p) => (
              <div className="my-3 overflow-x-auto">
                <table className="w-full border-collapse text-left text-[12px]" {...dom(p)} />
              </div>
            ),
            th: (p) => <th className="border-b border-line-strong px-2 py-1.5 font-medium text-ink" {...dom(p)} />,
            td: (p) => <td className="border-b border-line px-2 py-1.5 align-top text-ink-2" {...dom(p)} />,
          }}
        >
          {data.markdown}
        </ReactMarkdown>
        {!open ? <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface to-transparent" /> : null}
      </div>
    </section>
  );
}
