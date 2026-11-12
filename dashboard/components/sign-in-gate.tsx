"use client";

/*  Sends anyone who is not signed in to /login when the deployed instance requires it.
 *  Not a security boundary — the API refuses the data itself — but without it a visitor
 *  would land on a dashboard of "Sign in to see this" errors.
 */

import { useEffect } from "react";
import { loginHref, signOut, useMe } from "@/lib/auth";

export function SignInGate({ children }: { children: React.ReactNode }) {
  const { data: me } = useMe();
  const blocked = Boolean(me?.auth_required && !me.user);
  useEffect(() => {
    if (blocked) window.location.replace(loginHref());
  }, [blocked]);
  // Until the API has answered we cannot know; render nothing rather than flash data.
  if (!me || blocked) return null;
  return <>{children}</>;
}

export function AccountMenu() {
  const { data: me } = useMe();
  if (!me?.user) return null;
  return (
    <div className="flex items-center gap-2 text-[12.5px]">
      <span className="hidden max-w-[180px] truncate text-ink-3 md:inline" title={me.user.email}>{me.user.email}</span>
      <button type="button" onClick={() => void signOut()} className="rounded-md px-2 py-1.5 text-ink-2 hover:text-ink">
        Sign out
      </button>
    </div>
  );
}
