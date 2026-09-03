import { AppNav } from "@/components/app-nav";
import { AccessProvider, DemoBanner } from "@/components/access";
import { Rights } from "@/components/rights";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AccessProvider>
      <div className="flex min-h-dvh flex-col">
        <DemoBanner />
        <AppNav />
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-8">{children}</main>
        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3">
            <span className="font-mono text-[11.5px] text-ink-3">ZoNuLy</span>
            <Rights />
          </div>
        </footer>
      </div>
    </AccessProvider>
  );
}
