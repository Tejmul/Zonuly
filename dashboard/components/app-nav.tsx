"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme";
import { SearchBox } from "@/components/search-box";

// The order is the workflow: find companies (list or map) → the people at them →
// approve the drafts → watch what comes back. Roles live inside each company page.
const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/companies", label: "Companies" },
  { href: "/atlas", label: "Atlas" },
  { href: "/leads", label: "Leads" },
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/tracker", label: "Tracker" },
];

export function AppNav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-6">
        <Link href="/" className="font-mono text-[13px] font-semibold tracking-tight">
          ZoNuLy
        </Link>
        <nav className="flex items-center gap-1 overflow-x-auto">
          {LINKS.map((l) => {
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
                className={`cursor-pointer rounded-md px-3 py-1.5 text-[13px] transition-colors duration-150 ${
                  active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <SearchBox />
          <Link
            href="/settings"
            className="cursor-pointer rounded-md px-3 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink"
          >
            Settings
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
