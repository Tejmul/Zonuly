"use client";

import { createContext, useCallback, useContext, useSyncExternalStore } from "react";

/* The sidebar's open/closed state, shared between the sidebar itself and the
   hamburger in the top bar. Tejmul, 2026-09-22: a way to see the whole page at once.
   The choice lives in localStorage and is read through useSyncExternalStore, so the
   server renders it open, the first client frame agrees, and the stored value takes
   over without a setState inside an effect. */

const KEY = "zonuly-sidebar";
const EVENT = "zonuly-sidebar-change";

function read(): boolean {
  try {
    return localStorage.getItem(KEY) !== "closed";
  } catch {
    return true;
  }
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

const Ctx = createContext<{ open: boolean; toggle: () => void }>({ open: true, toggle: () => {} });

export function SidebarState({ children }: { children: React.ReactNode }) {
  const open = useSyncExternalStore(subscribe, read, () => true);

  const toggle = useCallback(() => {
    try {
      localStorage.setItem(KEY, read() ? "closed" : "open");
    } catch {
      /* private mode: the choice just doesn't persist */
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return <Ctx.Provider value={{ open, toggle }}>{children}</Ctx.Provider>;
}

export function useSidebar() {
  return useContext(Ctx);
}
