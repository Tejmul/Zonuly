"use client";

/*  Read-only mode, as the browser sees it.
 *
 *  The API is the thing that actually enforces this — see jobhunter/access.py, where a
 *  middleware refuses every write before it reaches a route. Nothing here is a security
 *  boundary and none of it should be mistaken for one. Its whole job is to stop the
 *  interface lying: a page that offers a Send button which then returns 403 reads as
 *  broken software, when in fact it is working exactly as intended.
 */

import { createContext, useContext, useEffect } from "react";
import { prefetch, useApi, type Health } from "@/lib/api";

type Access = { ready: boolean; publicInstance: boolean; snapshot: Health["snapshot"] | null };

/** Exactly the paths the main pages ask for — a different query string is a different entry. */
const WARM_PATHS = [
  "/api/companies/grouped",
  "/api/network",
  "/api/lists/india-star",
  "/api/graph/map?view=market",
  "/api/graph/map?view=pipeline",
  "/api/leads?source=all&city=all&who=ready&offset=0&limit=50",
  "/api/companies?include_incomplete=true&has_contacts=false&sort=score&limit=40",
  "/api/overview",
];

const Ctx = createContext<Access>({ ready: false, publicInstance: false, snapshot: null });

export function AccessProvider({ children }: { children: React.ReactNode }) {
  const { data, loading } = useApi<Health>("/api/health");
  const publicInstance = Boolean(data?.access?.public);
  // On the public instance the data is a snapshot that never moves, so once the API has
  // answered, the main pages' data is fetched in the background: the next click is instant.
  useEffect(() => {
    if (publicInstance) void prefetch(WARM_PATHS);
  }, [publicInstance]);
  return (
    <Ctx.Provider value={{ ready: !loading, publicInstance, snapshot: data?.snapshot ?? null }}>
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
  const { publicInstance, snapshot } = useAccess();
  // Only an explicit `schema_ok: false` is news. An older API without the snapshot
  // block, or one that is still loading, says nothing.
  const stale = snapshot?.schema_ok === false;
  if (!publicInstance && !stale) return null;

  return (
    <>
      {publicInstance ? <DemoNote /> : null}
      {stale ? (
        <div className="border-b border-line">
          <p className="mx-auto max-w-[1400px] px-6 py-1.5 text-[11.5px] text-ink-3">
            The data on the server is from an older build than this page
            {snapshot?.built_at ? ` (built ${snapshot.built_at.slice(0, 10)})` : ""} — a few columns may read as
            empty until it is refreshed.
          </p>
        </div>
      ) : null}
    </>
  );
}

function DemoNote() {
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
