"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";
import {
  Activity,
  Building2,
  Inbox,
  LayoutGrid,
  MessageSquare,
  Send,
  Settings,
  Table2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useApi, type Health } from "@/lib/api";
import { BudgetMeter, StatusDot } from "@/components/signals";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid },
  { href: "/roles", label: "Roles", icon: Table2 },
  { href: "/leads", label: "Leads", icon: Building2 },
  { href: "/queue", label: "Review queue", icon: Inbox },
  { href: "/tracker", label: "Sent tracker", icon: Send },
  { href: "/replies", label: "Replies", icon: MessageSquare },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: health } = useApi<Health>("/api/health");

  return (
    <div className="flex min-h-screen">
      <nav className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r border-line bg-surface">
        <Link href="/" className="border-b border-line px-4 py-4">
          <div className="flex items-baseline gap-1.5">
            <span className="text-base font-semibold tracking-tight text-ink">JobHunter</span>
            <span className="h-1.5 w-1.5 rounded-full bg-surface-2" />
          </div>
          <div className="eyebrow mt-1">Referral pipeline</div>
        </Link>

        <div className="flex flex-1 flex-col gap-0.5 p-2">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm transition-colors",
                  active
                    ? "bg-surface-3 text-ink"
                    : "text-ink-2 hover:bg-surface hover:text-ink",
                )}
              >
                <Icon size={15} strokeWidth={1.75} className={active ? "text-ink" : ""} />
                {item.label}
              </Link>
            );
          })}
        </div>

        <div className="flex flex-col gap-1.5 border-t border-line px-4 py-3">
          <StatusDot ok={!!health?.llm.ok && !!health.llm.model_present} label={health?.llm.model ?? "model"} />
          <StatusDot ok={!!health?.gmail.authorized} label="Gmail" />
          <StatusDot ok={!!health?.scheduler.running} label="Scheduler" />
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-6 border-b border-line bg-paper/95 px-6 backdrop-blur">
          <div className="flex items-center gap-2 text-sm text-ink-2">
            <Activity size={14} className="text-ink-3" />
            <span className="tnum">{health?.counts.jobs ?? "—"}</span>
            <span className="text-ink-3">jobs</span>
            <span className="text-ink-3">/</span>
            <span className="tnum text-ink">{health?.counts.high_match ?? "—"}</span>
            <span className="text-ink-3">high matches</span>
          </div>
          {health ? (
            <BudgetMeter
              cap={health.quota.daily_cap}
              sent={health.quota.sent_today}
              inWindow={health.quota.in_window}
            />
          ) : null}
        </header>
        <main className="min-w-0 flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

export function PageHead({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 text-sm text-ink-3">{description}</p> : null}
      </div>
      {action ? <div className="flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

/** Background work reports here — scrapes and LLM passes run for minutes. */
export function TaskStrip({
  tasks,
  onDismiss,
}: {
  tasks: { id: string; name: string; status: string; error?: string }[];
  onDismiss: (id: string) => void;
}) {
  if (!tasks.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-40 flex w-80 flex-col gap-2">
      {tasks.map((task) => (
        <div
          key={task.id}
          className="tick-in flex items-start gap-2.5 rounded-sm border border-line bg-surface-2 px-3 py-2.5 shadow-lg"
        >
          <span
            className={cn(
              "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
              task.status === "running"
                ? "bg-surface-2 pulse-soft"
                : task.status === "error"
                  ? "bg-surface-2"
                  : "bg-surface-2",
            )}
          />
          <div className="min-w-0 flex-1">
            <div className="font-mono text-xs text-ink">{task.name}</div>
            <div className="mt-0.5 text-xs text-ink-3">
              {task.status === "running"
                ? "Working…"
                : task.status === "error"
                  ? task.error?.slice(0, 90)
                  : "Done"}
            </div>
          </div>
          {task.status !== "running" ? (
            <button
              onClick={() => onDismiss(task.id)}
              className="text-ink-3 transition-colors hover:text-ink"
              aria-label="Dismiss"
            >
              ×
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
