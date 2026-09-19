import { AppNav } from "@/components/app-nav";
import { AccessProvider, DemoBanner } from "@/components/access";
import { Rights } from "@/components/rights";
import { Logo } from "@/components/logo";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AccessProvider>
      <div className="flex min-h-dvh flex-col">
        <DemoBanner />
        <AppNav />
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-8">{children}</main>
        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3">
            <Logo href={null} size={14} className="text-[11.5px] text-ink-3" wordClassName="font-normal" />
            <Rights />
          </div>
        </footer>
      </div>
    </AccessProvider>
  );
}
