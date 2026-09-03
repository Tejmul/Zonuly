import { AppNav } from "@/components/app-nav";
import { AccessProvider, DemoBanner } from "@/components/access";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AccessProvider>
      <div className="min-h-dvh">
        <DemoBanner />
        <AppNav />
        <main className="mx-auto max-w-[1400px] px-6 py-8">{children}</main>
      </div>
    </AccessProvider>
  );
}
