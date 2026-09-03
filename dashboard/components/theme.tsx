"use client";

import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const KEY = "zonuly-theme";

/** Light/dark toggle. The boot script in the root layout has already applied the
 *  stored choice, so this only has to stay in sync with it — never to set it first. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setReady(true);
  }, []);

  const toggle = useCallback(() => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      /* private mode: the choice just doesn't persist */
    }
    setDark(next);
  }, []);

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={`grid h-9 w-9 cursor-pointer place-items-center rounded-md border border-line
        text-ink-2 transition-colors duration-200 hover:bg-surface-2 hover:text-ink ${className}`}
    >
      {/* Rendered only after mount: the server cannot know which icon is correct,
          and a wrong icon for one frame is worse than none. */}
      {ready ? (dark ? <Sun size={15} /> : <Moon size={15} />) : <span className="h-[15px] w-[15px]" />}
    </button>
  );
}
