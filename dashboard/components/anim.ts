"use client";

import { gsap } from "gsap";

/** Entrance animations, made safe to interrupt.
 *
 *  `gsap.from` writes inline styles immediately and leaves them behind if the tween is
 *  interrupted — which is exactly what happened when a fetch resolved mid-animation and
 *  re-rendered the page: elements were stranded at opacity 0 and the whole screen looked
 *  washed out until a hard refresh. `fromTo` with `clearProps` cannot strand anything,
 *  because the end state is explicit and every inline style is removed on completion. */
export function rise(
  selector: string,
  opts: { stagger?: number; y?: number; limit?: number } = {},
) {
  /* Bounded. A per-item stagger over the whole Companies table — four thousand rows at
     12ms each — meant the last rows were still invisible 48 seconds later, and anyone
     who scrolled saw an empty table. Only the first `limit` items (about a screenful)
     take part; everything after them is simply there. The whole cascade also has a
     ceiling of ~0.45s however many items there are. Selector text is scoped by the
     surrounding useGSAP context, so this only ever sees the calling page's elements. */
  const all = gsap.utils.toArray<HTMLElement>(selector);
  const items = all.slice(0, opts.limit ?? 24);
  if (!items.length) return null;
  const each = opts.stagger ?? 0.04;
  return gsap.fromTo(
    items,
    { opacity: 0, y: opts.y ?? 10 },
    {
      opacity: 1,
      y: 0,
      duration: 0.4,
      ease: "power2.out",
      stagger: { each: Math.min(each, 0.45 / items.length) },
      clearProps: "opacity,transform",
      overwrite: "auto",
    },
  );
}
