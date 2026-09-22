import { AppNav, Sidebar } from "@/components/app-nav";
import { AccessProvider, DemoBanner } from "@/components/access";
import { SidebarState } from "@/components/frame";
import { Rights } from "@/components/rights";
import { Logo } from "@/components/logo";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AccessProvider>
      <SidebarState>
        <div className="flex min-h-dvh">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <DemoBanner />
            <AppNav />
            <main className="mx-auto w-full max-w-[1480px] flex-1 px-4 py-6 sm:px-6">{children}</main>
            <footer className="border-t border-line">
              <div className="mx-auto flex max-w-[1480px] items-center justify-between px-6 py-3">
                <Logo href={null} size={14} className="text-[11.5px] text-ink-3" wordClassName="font-normal" />
                <Rights />
              </div>
            </footer>
          </div>
        </div>
      </SidebarState>
    </AccessProvider>
  );
}
