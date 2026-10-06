"use client";

import { useCallback, useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

const KEY = "zonuly-theme";

/* The theme lives on <html class="dark">, put there before first paint by the boot
   script in the root layout. Every toggle on the page reads that one class through
   useSyncExternalStore, so two toggles (the header's and the hero's) can never
   disagree, and nothing calls setState inside an effect to find out. */

function subscribe(cb: () => void) {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => mo.disconnect();
}
const isDark = () => document.documentElement.classList.contains("dark");

function apply(next: boolean) {
  document.documentElement.classList.toggle("dark", next);
  try {
    localStorage.setItem(KEY, next ? "dark" : "light");
  } catch {
    /* private mode: the choice just doesn't persist */
  }
}

/** Light/dark toggle.
 *
 *  The switch used to be every element running its own colour transition — 150ms
 *  here, 300ms there, 750ms on the photographs — so the page changed in patches and
 *  looked like it was failing to repaint. Now it is one deliberate move: the browser
 *  snapshots the old page, the new theme is applied in a single frame with every
 *  per-element transition suppressed, and the new page is revealed as a circle that
 *  grows out of the button. Without View Transitions it is an instant, clean swap;
 *  with reduced motion it is the same instant swap. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const dark = useSyncExternalStore(subscribe, isDark, () => null);

  const toggle = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    const next = !isDark();
    const root = document.documentElement;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // .theme-switching turns every transition off for the frame the class flips in,
    // so nothing fades on its own clock underneath the reveal.
    // The icon re-renders off the MutationObserver, a task later — waiting one tick
    // lets the snapshot of the new page include the new icon.
    const swap = () => {
      root.classList.add("theme-switching");
      apply(next);
      return new Promise<void>((done) => setTimeout(done, 0));
    };
    const settle = () => requestAnimationFrame(() => root.classList.remove("theme-switching"));

    if (still || typeof document.startViewTransition !== "function") {
      void swap();
      // force the new styles to resolve with transitions still off
      void root.offsetWidth;
      settle();
      return;
    }

    const r = e.currentTarget.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));

    const vt = document.startViewTransition(swap);
    vt.ready
      .then(() => {
        root.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 620, easing: "cubic-bezier(0.65, 0, 0.35, 1)", pseudoElement: "::view-transition-new(root)" },
        );
      })
      .catch(() => {});
    vt.finished.finally(settle);
  }, []);

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={`grid h-9 w-9 cursor-pointer place-items-center rounded-md border border-line
        text-ink-2 transition-colors duration-200 hover:bg-surface-2 hover:text-ink ${className}`}
    >
      {/* null on the server: it cannot know which icon is correct, and a wrong icon
          for one frame is worse than none. */}
      {dark === null ? (
        <span className="h-[15px] w-[15px]" />
      ) : (
        <span key={dark ? "sun" : "moon"} className="theme-icon grid place-items-center">
          {dark ? <Sun size={15} /> : <Moon size={15} />}
        </span>
      )}
    </button>
  );
}
