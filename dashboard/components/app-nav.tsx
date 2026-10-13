"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Building2, Calendar, Crosshair, Globe, Inbox, LayoutGrid, Mail, MessageSquare, PanelLeft, Send, Settings, Star, Users, Workflow,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme";
import { SearchBox } from "@/components/search-box";
import { useAccess } from "@/components/access";
import { useSidebar } from "@/components/frame";
import { Logo } from "@/components/logo";
import { useApi, type GmailSetup } from "@/lib/api";

// The order is the workflow: find companies (list or map) → the people at them →
// approve the drafts → watch what comes back. Roles live inside each company page.
const LINKS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutGrid },
  { href: "/companies", label: "Companies", icon: Building2 },
  // roles by the odds of getting selected — India and Remote (confidence.py)
  { href: "/best-bets", label: "Best bets", icon: Crosshair },
  // The hand-picked list — a page of its own so it is never one filter among seven
  { href: "/lists/india-star", label: "★ India", icon: Star },
  { href: "/atlas", label: "Atlas", icon: Globe },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/queue", label: "Queue", icon: Inbox },
  // The send pipeline end to end — counts are public, so it is offered everywhere
  { href: "/outreach", label: "Outreach", icon: Workflow },
  // Replies is real inbound mail from real people; the public instance refuses it, so
  // the tab is not offered there rather than leading to a 403.
  { href: "/replies", label: "Replies", icon: MessageSquare, operatorOnly: true },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/tracker", label: "Tracker", icon: Send },
  // the hourly audit: is the scraping still filling fields, and is every channel answering?
  { href: "/health", label: "Scraping health", icon: Activity },
];

function useLinks() {
  const path = usePathname();
  const { publicInstance } = useAccess();
  return LINKS.filter((l) => !(l.operatorOnly && publicInstance)).map((l) => ({
    ...l,
    // /companies/12 belongs to the atlas, so the atlas tab stays lit there
    active: path === l.href || path.startsWith(l.href + "/") || (l.href === "/companies" && path.startsWith("/companies")),
  }));
}

/* 2026-09-20: the nav moved from a row across the top to a sidebar, after the
   NextAdmin reference. On a laptop the sidebar holds the nine sections with icons
   and the sending status; below `lg` the same links go back to a scrolling row,
   because a 390px phone has no room for a column.
   2026-09-22: the hamburger in the top bar hides the sidebar so the page can be seen
   whole; black and white only — the active item is a grey pill with ink text. */

export function Sidebar() {
  const links = useLinks();
  const onSettings = usePathname().startsWith("/settings");
  const { publicInstance } = useAccess();
  const { open } = useSidebar();
  const gmail = useApi<GmailSetup>(publicInstance ? null : "/api/gmail/status");
  const live = gmail.data?.send_mode === "gmail" && gmail.data.authorized;

  if (!open) return null;

  return (
    <aside className="sticky top-0 hidden h-dvh w-[236px] shrink-0 flex-col gap-6 overflow-y-auto border-r border-line bg-surface px-3.5 py-5 lg:flex">
      <Logo size={18} className="px-1.5 text-[15px]" />
      <nav aria-label="Sections" className="flex flex-col gap-0.5">
        <div className="marginal px-2.5 pb-2">Main menu</div>
        {links.map((l) => {
          const Icon = l.icon;
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-current={l.active ? "page" : undefined}
              className={`flex h-9 cursor-pointer items-center gap-2.5 rounded-lg px-2.5 text-[13.5px] font-medium transition-colors duration-150 ${
                l.active ? "bg-surface-2 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
              }`}
            >
              <Icon size={16} strokeWidth={1.8} className="shrink-0" />
              {l.label}
            </Link>
          );
        })}
        <Link
          href="/settings"
          aria-current={onSettings ? "page" : undefined}
          className={`mt-2 flex h-9 cursor-pointer items-center gap-2.5 rounded-lg px-2.5 text-[13.5px] font-medium transition-colors ${
            onSettings ? "bg-surface-2 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
          }`}
        >
          <Settings size={16} strokeWidth={1.8} className="shrink-0" />
          Settings
        </Link>
      </nav>

      {!publicInstance ? (
        <div className="mt-auto rounded-xl border border-line p-3.5">
          <h3 className="flex items-center gap-2 text-[13px] font-semibold">
            <Mail size={14} className="text-ink-2" />
            {live ? "Sending is live" : "Sending is in dry run"}
          </h3>
          <p className="mt-1 mb-3 text-[12px] leading-relaxed text-ink-2">
            {live
              ? `Approved emails leave from ${gmail.data?.address ?? "your Gmail"} inside the daily cap.`
              : "Drafts are written and gated, but nothing leaves the machine until Gmail is connected."}
          </p>
          <Link href="/settings" className="btn-ink w-full">
            {live ? "Sending settings" : "Connect Gmail"}
          </Link>
        </div>
      ) : null}
    </aside>
  );
}

export function AppNav() {
  const links = useLinks();
  const { open, toggle } = useSidebar();

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
      <div className="px-4 sm:px-6">
        <div className="flex h-14 items-center gap-3 sm:gap-4">
          <button
            type="button"
            onClick={toggle}
            aria-pressed={open}
            aria-label={open ? "Hide the menu" : "Show the menu"}
            title={open ? "Hide the menu" : "Show the menu"}
            className="hidden h-9 w-9 shrink-0 cursor-pointer place-items-center rounded-md border border-line text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink lg:grid"
          >
            <PanelLeft size={15} />
          </button>
          <Logo size={18} className={`shrink-0 text-[13px] ${open ? "lg:hidden" : ""}`} />
          <div className="min-w-0 flex-1">
            <SearchBox />
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Link
              href="/settings"
              className={`cursor-pointer rounded-md px-2 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink sm:px-3 ${open ? "lg:hidden" : ""}`}
            >
              Settings
            </Link>
            <ThemeToggle />
          </div>
        </div>

        {/* Below lg — or whenever the sidebar is hidden — the sections are a row of
            their own: sharing the top bar with the search field gave every link too
            little room. */}
        <nav
          aria-label="Sections"
          className={`scrollbar-none -mx-4 flex items-center gap-1 overflow-x-auto px-4 pb-2 ${open ? "lg:hidden" : ""}`}
        >
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              aria-current={l.active ? "page" : undefined}
              className={`shrink-0 cursor-pointer rounded-md px-3 py-1.5 text-[13px] transition-colors duration-150 ${
                l.active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
