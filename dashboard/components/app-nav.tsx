"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme";
import { SearchBox } from "@/components/search-box";
import { useAccess } from "@/components/access";

// The order is the workflow: find companies (list or map) → the people at them →
// approve the drafts → watch what comes back. Roles live inside each company page.
const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/companies", label: "Companies" },
  { href: "/atlas", label: "Atlas" },
  { href: "/leads", label: "Leads" },
  { href: "/queue", label: "Queue" },
  // Replies is real inbound mail from real people; the public instance refuses it, so
  // the tab is not offered there rather than leading to a 403.
  { href: "/replies", label: "Replies", operatorOnly: true },
  { href: "/calendar", label: "Calendar" },
  { href: "/tracker", label: "Tracker" },
];

export function AppNav() {
  const path = usePathname();
  const { publicInstance } = useAccess();
  const links = LINKS.filter((l) => !(l.operatorOnly && publicInstance));

  /* Eight links, a search field and two controls do not fit on a 390px row. Sharing
     the space just gives every one of them too little — the links ended up in a
     10px scroller nobody would find. Below `sm` the links get a row of their own. */
  const list = (
    <>
      {links.map((l) => {
        // /companies/12 belongs to the atlas, so the atlas tab stays lit there
        const active =
          path === l.href ||
          path.startsWith(l.href + "/") ||
          (l.href === "/companies" && path.startsWith("/companies"));
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={active ? "page" : undefined}
            className={`shrink-0 cursor-pointer rounded-md px-3 py-1.5 text-[13px] transition-colors duration-150 ${
              active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"
            }`}
          >
            {l.label}
          </Link>
        );
      })}
    </>
  );

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
      <div className="mx-auto max-w-[1400px] px-4 sm:px-6">
        <div className="flex h-14 items-center gap-3 sm:gap-6">
          <Link href="/" className="shrink-0 font-mono text-[13px] font-semibold tracking-tight">
            ZoNuLy
          </Link>
          {/* min-w-0 is load-bearing: a flex child defaults to min-width:auto, so
              without it the nav refuses to shrink and the row overflows. */}
          <nav aria-label="Sections" className="scrollbar-none hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto sm:flex">
            {list}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2 sm:ml-0">
            <SearchBox />
            <Link
              href="/settings"
              className="cursor-pointer rounded-md px-2 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink sm:px-3"
            >
              Settings
            </Link>
            <ThemeToggle />
          </div>
        </div>

        <nav
          aria-label="Sections"
          className="scrollbar-none -mx-4 flex items-center gap-1 overflow-x-auto px-4 pb-2 sm:hidden"
        >
          {list}
        </nav>
      </div>
    </header>
  );
}
