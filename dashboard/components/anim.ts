"use client";

import { gsap } from "gsap";

/** Entrance animations, made safe to interrupt.
 *
 *  `gsap.from` writes inline styles immediately and leaves them behind if the tween is
 *  interrupted — which is exactly what happened when a fetch resolved mid-animation and
 *  re-rendered the page: elements were stranded at opacity 0 and the whole screen looked
 *  washed out until a hard refresh. `fromTo` with `clearProps` cannot strand anything,
 *  because the end state is explicit and every inline style is removed on completion. */
export function rise(selector: string, opts: { stagger?: number; y?: number } = {}) {
  return gsap.fromTo(
    selector,
    { opacity: 0, y: opts.y ?? 10 },
    {
      opacity: 1,
      y: 0,
      duration: 0.4,
      ease: "power2.out",
      stagger: opts.stagger ?? 0.04,
      clearProps: "all",
      overwrite: "auto",
    },
  );
}
