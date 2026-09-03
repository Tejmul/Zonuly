"use client";

/*  Read-only mode, as the browser sees it.
 *
 *  The API is the thing that actually enforces this — see jobhunter/access.py, where a
 *  middleware refuses every write before it reaches a route. Nothing here is a security
 *  boundary and none of it should be mistaken for one. Its whole job is to stop the
 *  interface lying: a page that offers a Send button which then returns 403 reads as
 *  broken software, when in fact it is working exactly as intended.
 */

import { createContext, useContext } from "react";
import { useApi, type Health } from "@/lib/api";

type Access = { ready: boolean; publicInstance: boolean };

const Ctx = createContext<Access>({ ready: false, publicInstance: false });

export function AccessProvider({ children }: { children: React.ReactNode }) {
  const { data, loading } = useApi<Health>("/api/health");
  return (
    <Ctx.Provider value={{ ready: !loading, publicInstance: Boolean(data?.access?.public) }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAccess() {
  return useContext(Ctx);
}

/** Wraps anything that performs an action. On a public instance it renders `fallback`,
 *  which is nothing unless the control is worth showing in a disabled state — the
 *  approval gate is the product's whole argument, so on the queue it stays visible. */
export function OperatorOnly({
  children,
  fallback = null,
}: {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { publicInstance } = useAccess();
  return publicInstance ? <>{fallback}</> : <>{children}</>;
}

export function DemoBanner() {
  const { publicInstance } = useAccess();
  if (!publicInstance) return null;

  return (
    <div className="border-b border-line bg-surface-2">
      <p className="mx-auto max-w-[1400px] px-6 py-2 text-[12px] leading-relaxed text-ink-2">
        <span className="font-medium text-ink">Read-only demonstration.</span>{" "}
        Real companies and real leads, from a snapshot of the pipeline&rsquo;s own database.
        Nothing here can be edited or sent, and lead email addresses are masked — the
        people in this data did not ask to be on a public page.
      </p>
    </div>
  );
}
