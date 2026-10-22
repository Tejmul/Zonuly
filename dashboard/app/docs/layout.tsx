import type { Metadata } from "next";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme";
import { DocsMobileNav, DocsSearch, DocsSidebar, DocsToc } from "@/components/docs/shell";

export const metadata: Metadata = {
  title: { default: "ZoNuLy Docs", template: "%s · ZoNuLy Docs" },
  description: "How ZoNuLy finds underrated companies, proves they are hiring, finds the person who could refer you, and asks — with a human approval gate.",
};

/* The documentation has its own frame, apart from the app's: a bar with the search,
   the sections down the left, the article in a reading column, and "On this page" on
   the right once the screen is wide enough for three columns. */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-paper">
      <a
        href="#content"
        className="sr-only z-50 rounded-md bg-ink px-3 py-2 text-[13px] font-medium text-on-ink focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-paper/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-3 px-4 sm:gap-5 sm:px-6">
          <Link href="/docs" className="flex shrink-0 items-center gap-2.5">
            <Logo href={null} size={18} className="text-[14px]" />
            <span className="chip">DOCS</span>
          </Link>
          <div className="min-w-0 flex-1">
            <DocsSearch />
          </div>
          <Link
            href="/dashboard"
            className="hidden shrink-0 items-center gap-1 rounded-md px-2.5 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink sm:inline-flex"
          >
            Open the app <ArrowUpRight size={13} />
          </Link>
          <a
            href="https://github.com/Tejmul/Zonuly"
            target="_blank"
            rel="noreferrer"
            className="hidden shrink-0 rounded-md px-2.5 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink md:inline-flex"
          >
            GitHub
          </a>
          <ThemeToggle />
        </div>
      </header>
      <DocsMobileNav />
      <div className="mx-auto flex max-w-[1440px]">
        <DocsSidebar />
        <main className="flex min-w-0 flex-1 justify-center gap-12 px-4 sm:px-8 xl:px-12">
          <article id="content" tabIndex={-1} className="min-w-0 max-w-[780px] flex-1 py-10 outline-none sm:py-14">{children}</article>
          <DocsToc />
        </main>
      </div>
    </div>
  );
}
