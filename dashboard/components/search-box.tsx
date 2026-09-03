"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { TierPill } from "@/components/ui/tier";

/* Global company search, in the top bar. Type a name or a word from the description,
   pick a row (or press Enter for the first), land on the company page. The registry is
   fetched once on first focus and kept; "/" focuses the box from anywhere. */

type Row = { id: number; name: string; tier: string | null; region: string | null; description: string | null; trust?: string };

export function SearchBox() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  async function load() {
    if (rows) return;
    try {
      const d = await api.get<{ companies: Row[] }>("/api/companies/grouped?include_rejects=true");
      setRows(d.companies);
    } catch {
      setRows([]);
    }
  }

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!rows || needle.length < 2) return [];
    const starts = rows.filter((r) => r.name.toLowerCase().startsWith(needle));
    const contains = rows.filter((r) => !r.name.toLowerCase().startsWith(needle) && r.name.toLowerCase().includes(needle));
    const described = rows.filter((r) => !r.name.toLowerCase().includes(needle) && (r.description ?? "").toLowerCase().includes(needle));
    return [...starts, ...contains, ...described].slice(0, 8);
  }, [rows, q]);

  function go(r: Row) {
    setOpen(false);
    setQ("");
    router.push(`/companies/${r.id}`);
  }

  return (
    <div className="relative w-56 max-w-[40vw]">
      <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
      <input
        ref={input}
        value={q}
        onFocus={() => { void load(); setOpen(true); }}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onChange={(e) => { setQ(e.target.value); setCursor(0); setOpen(true); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, hits.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
          if (e.key === "Enter" && hits[cursor]) go(hits[cursor]);
          if (e.key === "Escape") { setOpen(false); input.current?.blur(); }
        }}
        placeholder="Search companies  /"
        className="h-8 w-full rounded-md border border-line bg-surface pr-2 pl-7 text-[12px]
          placeholder:text-ink-3 focus:border-line-strong focus:outline-none"
      />
      {open && q.trim().length >= 2 && (
        <ul className="absolute top-9 right-0 left-0 z-40 max-h-80 overflow-y-auto rounded-md border border-line bg-paper shadow-lg">
          {hits.length === 0 ? (
            <li className="px-3 py-2 text-[12px] text-ink-3">{rows ? "No company matches." : "Loading…"}</li>
          ) : (
            hits.map((r, i) => (
              <li key={r.id}>
                <button
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => go(r)}
                  onMouseEnter={() => setCursor(i)}
                  className={`flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left ${i === cursor ? "bg-surface-2" : ""}`}
                >
                  <TierPill tier={r.tier} />
                  <span className="truncate text-[12.5px] font-medium">{r.name}</span>
                  <span className="marginal ml-auto shrink-0">{r.region ?? ""}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
