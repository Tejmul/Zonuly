"use client";

/*  The Atlas network.
 *
 *  Four columns, wired left to right, and the wiring is the arithmetic in
 *  lib/atlas-model.ts rather than an illustration of it:
 *
 *      you            what it weighs        segments          focus on these
 *      ────           ──────────────        ────────          ──────────────
 *      resume    ──▶  right kind of work ──▶ AI agents    ──▶  Vercel
 *      + what         recently funded        deep tech         Baseten
 *      you want       can pay                robotics          …
 *                     you could take it
 *                     open to you
 *                     really hiring
 *                     someone to ask
 *
 *  Every wire's weight is a real number: input→term is the slider you set,
 *  term→segment is that term's mean contribution inside the segment, and
 *  term→company is its contribution to that company's score. Node size is
 *  activation. Hovering a company dims everything that did not feed it, which is
 *  the whole point — the answer to "why this one" is a path you can see.
 *
 *  Motion. It used to re-render the right-hand columns from scratch on every
 *  re-rank: nodes jumped, wires snapped, and a company climbing three places looked
 *  identical to a different company appearing. Now every node and wire is keyed by
 *  what it is, not where it sits. A node moves by a transform transition, so a
 *  climber slides up; a wire tweens its two endpoints, so it stays attached while it
 *  travels; radii and scores tween to their new values; what arrives fades in and
 *  what leaves is cloned at its last position and faded out (SvgExits). Everything
 *  is under half a second and a new change retargets from wherever things are.
 *  Under prefers-reduced-motion every change is instant.
 *
 *  The travelling dots are a dash on a second copy of each live wire. They carry
 *  no information beyond "this wire is live", and they stop dead under
 *  prefers-reduced-motion, where the picture is still complete without them.
 */

import { Component, createRef, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type SVGProps } from "react";
import { gsap } from "gsap";
import { Flip } from "gsap/Flip";
import { TERMS, type FieldKey, type Scored, type Weights } from "@/lib/atlas-model";

gsap.registerPlugin(Flip);

/* The small tweens below (a wire's ends, a radius, a number) run in plain layout
   effects rather than useGSAP: useGSAP's context keeps every tween it ever made
   until unmount, and a dragged slider makes hundreds. Each of these overwrites its
   own previous tween instead, and kills it on unmount. */

type Seg = { segment: string; label: string; heat: number; n: number };

/* The columns are inset far enough that end-anchored labels on the left and
   start-anchored names on the right both have room inside the viewBox. They ran
   off both edges when the first and last columns sat at 80 and 920. */
const W = 1200;
const COLS = [210, 470, 730, 1000];
const TOP = 64;
const GAP = 38;   // room for a label to sit above its node without touching the row above
const SLOTS = 8;  // segments and companies shown; the height is fixed to it so the plate never jumps

const DUR = 0.45;
const EASE = "power2.out";

/** Read at animation time, never at render, so the server and the client agree. */
const still = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const wireOn = (weight: number) => weight > 0.02;

export function AtlasNet({
  inputs, segments, ranked, weights, fields, onPick, picked,
}: {
  inputs: { label: string; detail: string }[];
  segments: Seg[];
  ranked: Scored[];
  weights: Weights;
  fields: FieldKey[];
  picked: number | null;
  onPick: (id: number | null) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const active = hover ?? picked;

  const segs = segments.slice(0, SLOTS);
  const outs = ranked.slice(0, SLOTS);
  const ins = inputs.slice(0, 4);

  const rows = Math.max(ins.length, TERMS.length, SLOTS);
  const H = TOP + rows * GAP + 16;

  const y = (i: number, n: number) => TOP + (rows - n) * (GAP / 2) + i * GAP;

  /* Which company is under the cursor, and everything that fed it. Cheap enough
     not to memoise: eight companies. */
  const hit = active === null ? undefined : outs.find((o) => o.company.id === active);
  const focus = hit ? { scored: hit, segment: hit.company.segment, terms: new Set(hit.top) } : null;

  const dim = (on: boolean) => (focus && !on ? 0.12 : 1);

  /* Everything that can leave, by key — SvgExits clones what drops out of this. */
  const exits: string[] = [];
  for (const s of segs) exits.push(`s:${s.segment}`);
  for (const t of TERMS)
    for (const s of segs) if (wireOn(weights[t.key] * s.heat)) exits.push(`ts:${t.key}:${s.segment}`);
  for (const o of outs) {
    exits.push(`c:${o.company.id}`);
    if (segs.some((s) => s.segment === o.company.segment) && wireOn(o.score))
      exits.push(`sc:${o.company.segment}:${o.company.id}`);
  }

  return (
    <div className="relative">
      {/* Scrolls rather than shrinks: at 390px a 1200-unit viewBox would render the
          labels about three pixels tall, which is not a smaller chart, it is no
          chart. Wide content gets its own scroll container. */}
      <div className="-mx-1 overflow-x-auto px-1">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full min-w-[860px]"
        style={{ height: "auto" }}
        role="img"
        aria-label="How your resume and your priorities turn into a ranked list of companies"
      >
        <Heads />

        <SvgExits keys={exits.join("|")}>
          {/* -------- wires: you -> terms ---------------------------------- */}
          {ins.map((_, i) =>
            TERMS.map((t, j) => {
              const on = !focus || focus.terms.has(t.key);
              return (
                <Wire
                  key={`i${i}-${t.key}`}
                  x1={COLS[0]} y1={y(i, ins.length)} x2={COLS[1]} y2={y(j, TERMS.length)}
                  weight={weights[t.key]} opacity={dim(on)} live={on && weights[t.key] > 0.6}
                />
              );
            }),
          )}

          {/* -------- wires: terms -> segments ----------------------------- */}
          {TERMS.map((t, j) =>
            segs.map((s, k) => {
              const on = !focus || (focus.terms.has(t.key) && focus.segment === s.segment);
              return (
                <Wire
                  key={`ts:${t.key}:${s.segment}`} exit={`ts:${t.key}:${s.segment}`}
                  x1={COLS[1]} y1={y(j, TERMS.length)} x2={COLS[2]} y2={y(k, segs.length)}
                  weight={weights[t.key] * s.heat} opacity={dim(on)} live={on && s.heat > 0.55}
                />
              );
            }),
          )}

          {/* -------- wires: segments -> companies ------------------------- */}
          {segs.map((s, k) =>
            outs.map((o, m) => {
              if (o.company.segment !== s.segment) return null;
              const on = !focus || focus.scored.company.id === o.company.id;
              return (
                <Wire
                  key={`sc:${s.segment}:${o.company.id}`} exit={`sc:${s.segment}:${o.company.id}`}
                  x1={COLS[2]} y1={y(k, segs.length)} x2={COLS[3]} y2={y(m, outs.length)}
                  weight={o.score} opacity={dim(on)} live={on && o.score > 0.5}
                />
              );
            }),
          )}

          {/* -------- nodes ------------------------------------------------ */}
          {ins.map((n, i) => (
            <Node
              key={n.label} x={COLS[0]} y={y(i, ins.length)} r={5} anchor="end"
              label={n.label} sub={n.detail} opacity={dim(true)}
            />
          ))}

          {TERMS.map((t, j) => (
            <Node
              key={t.key} x={COLS[1]} y={y(j, TERMS.length)}
              r={3 + weights[t.key] * 5} anchor="middle" above
              label={t.label}
              opacity={dim(!focus || focus.terms.has(t.key))}
            />
          ))}

          {/* A segment or company row sits at y=0 inside a group that is translated
              to its slot, so a re-rank is a transform transition, not a re-mount. */}
          {segs.map((s, k) => (
            <g
              key={s.segment} data-exit={`s:${s.segment}`}
              className="atlas-move" style={{ transform: `translate(0px, ${y(k, segs.length)}px)` }}
            >
              <g className="atlas-enter">
                <Node
                  x={COLS[2]} y={0} r={3 + s.heat * 6} anchor="middle" above
                  label={s.label}
                  opacity={dim(!focus || focus.segment === s.segment)}
                />
              </g>
            </g>
          ))}

          {outs.map((o, m) => (
            <g
              key={o.company.id} data-exit={`c:${o.company.id}`}
              className="atlas-move cursor-pointer"
              style={{ transform: `translate(0px, ${y(m, outs.length)}px)` }}
              onMouseEnter={() => setHover(o.company.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onPick(picked === o.company.id ? null : o.company.id)}
            >
              <g className="atlas-enter">
                {/* a fat invisible target, so a 6px dot is still easy to hit */}
                <rect x={COLS[3] - 14} y={-13} width={W - COLS[3] + 14} height={26} fill="transparent" />
                <Node
                  x={COLS[3]} y={0} r={3 + o.score * 6} anchor="start"
                  label={o.company.name} value={Math.round(o.score * 100)} valueX={W - 6}
                  opacity={dim(!focus || focus.scored.company.id === o.company.id)}
                  strong={picked === o.company.id}
                />
              </g>
            </g>
          ))}
        </SvgExits>
      </svg>
      </div>

      {/* The read-out. A picture that cannot say why is just a picture. */}
      <div className="border-t border-line px-1 pt-3">
        {focus ? (
          <Why scored={focus.scored} weights={weights} fields={fields} />
        ) : (
          <p className="text-[11.5px] text-ink-3">
            Hover a company on the right to trace what lit it up. Node size is how strongly
            each thing fired; wire weight is how much it contributed.
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function Heads() {
  const heads = ["You", "What it weighs", "Segments", "Focus on these"];
  return (
    <>
      {heads.map((h, i) => (
        <text
          key={h} x={COLS[i]} y={28}
          textAnchor={i === 0 ? "end" : i === 3 ? "start" : "middle"}
          className="fill-[var(--ink-3)]"
          style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", fontFamily: "var(--font-mono)" }}
        >
          {h}
        </text>
      ))}
    </>
  );
}

const curve = (x1: number, y1: number, x2: number, y2: number) => {
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
};

/** A wire whose `d` is never rendered by React: it is painted from a pair of
 *  endpoints that tween, so when either end moves the curve travels with it rather
 *  than snapping. One tween per wire, overwritten by the next change. */
function Wire({
  x1, y1, x2, y2, weight, opacity, live, exit,
}: {
  x1: number; y1: number; x2: number; y2: number;
  weight: number; opacity: number; live: boolean; exit?: string;
}) {
  const ref = useRef<SVGGElement>(null);
  const ends = useRef<{ a: number; b: number } | null>(null);
  const target = useRef<string>("");
  const shown = wireOn(weight);

  useLayoutEffect(() => {
    const g = ref.current;
    if (!g) return;
    const paint = () => {
      const e = ends.current!;
      const d = curve(x1, e.a, x2, e.b);
      for (const p of g.querySelectorAll("path")) p.setAttribute("d", d);
    };
    const key = `${y1}|${y2}`;
    if (!ends.current || still()) {
      if (ends.current) gsap.killTweensOf(ends.current);
      ends.current = { a: y1, b: y2 };
    } else if (key !== target.current) {
      gsap.to(ends.current, { a: y1, b: y2, duration: DUR, ease: EASE, overwrite: true, onUpdate: paint });
    }
    target.current = key;
    paint(); // a pulse path that just mounted needs a `d` before the first frame
  }, [x1, y1, x2, y2, live, shown]);
  useEffect(() => () => { if (ends.current) gsap.killTweensOf(ends.current); }, []);

  if (!shown) return null;
  return (
    <g ref={ref} data-exit={exit} className="atlas-enter atlas-fade" style={{ opacity }}>
      <path
        fill="none" stroke="var(--data-4)" className="atlas-wire"
        style={{ strokeWidth: 0.4 + weight * 1.6, strokeOpacity: 0.15 + weight * 0.5 }}
      />
      {live && (
        <path
          fill="none" stroke="var(--data-1)" strokeWidth={1.6} strokeLinecap="round"
          className="atlas-pulse"
          // staggered off the wire's own geometry, so the dots do not march in lockstep
          style={{ animationDelay: `${((y1 * 7 + y2 * 13) % 260) / 100}s` }}
        />
      )}
    </g>
  );
}

/** A circle whose radius tweens. `r` is set imperatively, before paint. */
function Dot({ r, ...rest }: { r: number } & Omit<SVGProps<SVGCircleElement>, "r" | "ref">) {
  const ref = useRef<SVGCircleElement>(null);
  const seen = useRef(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!seen.current || still()) {
      gsap.killTweensOf(el);
      gsap.set(el, { attr: { r } });
      seen.current = true;
    } else {
      gsap.to(el, { attr: { r }, duration: DUR, ease: EASE, overwrite: true });
    }
  }, [r]);
  useEffect(() => {
    const el = ref.current;
    return () => { if (el) gsap.killTweensOf(el); };
  }, []);
  return <circle ref={ref} {...rest} />;
}

/** A number that counts to its new value. The text is written imperatively — React
 *  owns the element, never its text node — so React and the tween cannot fight. */
function Count({ value, ...rest }: { value: number } & Omit<SVGProps<SVGTextElement>, "ref" | "children">) {
  const ref = useRef<SVGTextElement>(null);
  const cur = useRef<{ v: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const write = () => { el.textContent = String(Math.round(cur.current!.v)); };
    if (!cur.current || still()) {
      if (cur.current) gsap.killTweensOf(cur.current);
      cur.current = { v: value };
      write();
    } else {
      gsap.to(cur.current, { v: value, duration: DUR, ease: EASE, overwrite: true, onUpdate: write });
    }
  }, [value]);
  useEffect(() => () => { if (cur.current) gsap.killTweensOf(cur.current); }, []);
  return <text ref={ref} {...rest} />;
}

function Node({
  x, y, r, label, sub, value, valueX, anchor, opacity, strong = false, above = false,
}: {
  x: number; y: number; r: number; label: string; sub?: string; value?: number;
  /** Put the label over the node instead of beside it. The first and last columns
   *  only have wires on one side, so their labels sit safely in the clear space on
   *  the other. A middle column has wires on both sides and no clear side at all —
   *  the only space a wire never occupies is directly above the node. */
  above?: boolean;
  /** Absolute x for the number, so scores line up in one column instead of
   *  floating beside nodes of different sizes and colliding with the wires. */
  valueX?: number;
  anchor: "start" | "middle" | "end"; opacity: number; strong?: boolean;
}) {
  // Labels are placed off the largest radius the node can reach, not the current
  // one, so a tweening dot never drags its label through a re-layout.
  const pad = 9 + 8;
  const tx = above ? x : anchor === "end" ? x - pad : x + pad;
  const textAnchor = above ? "middle" : anchor === "end" ? "end" : "start";
  const ty = above ? y - r - 7 : y - (sub ? 2 : -3.5);
  /* A stroke of the surface colour painted under the glyphs. Wires pass behind the
     labels already, but "behind" is not "legible" when thirty of them cross at
     once, so the text carries its own clearance. */
  const halo = { paintOrder: "stroke fill", stroke: "var(--surface)", strokeWidth: 3.5, strokeLinejoin: "round" } as const;
  return (
    <g className="atlas-fade" style={{ opacity }}>
      <Dot cx={x} cy={y} r={r} fill={strong ? "var(--data-1)" : "var(--data-2)"} className="atlas-pop" />
      <Dot cx={x} cy={y} r={r + 3} fill="none" stroke="var(--surface)" strokeWidth={2} />
      <text
        x={tx} y={ty} textAnchor={textAnchor}
        className="fill-[var(--ink)]" style={{ fontSize: 11.5, ...halo }}
      >
        {label.length > 34 ? label.slice(0, 33) + "…" : label}
      </text>
      {sub && (
        <text x={tx} y={y + 10} textAnchor={textAnchor} className="fill-[var(--ink-3)]" style={{ fontSize: 9.5, ...halo }}>
          {sub.length > 34 ? sub.slice(0, 33) + "…" : sub}
        </text>
      )}
      {value !== undefined && (
        <Count
          value={value}
          x={valueX ?? (anchor === "end" ? x + pad : x - pad)} y={y + 3.5}
          textAnchor={valueX !== undefined ? "end" : anchor === "end" ? "start" : "end"}
          className="fill-[var(--ink-3)]"
          style={{ fontSize: 9.5, fontFamily: "var(--font-mono)" }}
        />
      )}
    </g>
  );
}

/* ------------------------------------------------------------------- exits
   React removes a node the moment it leaves the list, so there is nothing left to
   fade. These two classes use the one lifecycle that sees the DOM before a commit
   (getSnapshotBeforeUpdate) to clone whatever is about to go, then lay the clone
   back down where it was and fade it out — outside React's tree, so React never
   reconciles it. A class here is not nostalgia: there is no hook for "before". */

function stripEnter(el: Element) {
  for (const n of [el, ...el.querySelectorAll(".atlas-enter, .atlas-pop")])
    n.classList.remove("atlas-enter", "atlas-pop");
}

class SvgExits extends Component<{ keys: string; children: ReactNode }> {
  root = createRef<SVGGElement>();
  ghosts = createRef<SVGGElement>();

  getSnapshotBeforeUpdate(prev: { keys: string }): Element[] | null {
    const root = this.root.current;
    if (prev.keys === this.props.keys || !root || still()) return null;
    const next = new Set(this.props.keys.split("|"));
    const out: Element[] = [];
    for (const el of root.querySelectorAll<SVGElement>("[data-exit]")) {
      const k = el.dataset.exit;
      if (k && !next.has(k)) {
        const clone = el.cloneNode(true) as SVGElement;
        stripEnter(clone);
        clone.removeAttribute("data-exit");
        out.push(clone);
      }
    }
    return out.length ? out : null;
  }

  componentDidUpdate(_p: unknown, _s: unknown, snap: Element[] | null) {
    const layer = this.ghosts.current;
    if (!snap || !layer) return;
    for (const clone of snap) {
      layer.appendChild(clone);
      const dots = clone.querySelectorAll("circle");
      if (dots.length) gsap.to(dots, { scale: 0.4, transformOrigin: "50% 50%", duration: 0.28, ease: "power1.in" });
      gsap.to(clone, { opacity: 0, duration: 0.28, ease: "power1.in", onComplete: () => clone.remove() });
    }
  }

  componentWillUnmount() {
    const layer = this.ghosts.current;
    if (!layer) return;
    for (const c of [...layer.children]) {
      gsap.killTweensOf([c, ...c.querySelectorAll("circle")]);
      c.remove();
    }
  }

  render() {
    return (
      <>
        <g ref={this.root}>{this.props.children}</g>
        <g ref={this.ghosts} aria-hidden style={{ pointerEvents: "none" }} />
      </>
    );
  }
}

/** The results list. Rows carry `data-flip-id`; `order` is their ids joined. On a
 *  new order, rows that stay glide to their new place (GSAP Flip), rows that arrive
 *  fade up, rows that go are cloned where they were and fade out. Only rows on
 *  screen are animated — the rest just take their place. */
export class MotionList extends Component<{ order: string; className?: string; children: ReactNode }> {
  list = createRef<HTMLUListElement>();
  ghosts = createRef<HTMLDivElement>();
  tl: gsap.core.Timeline | null = null;

  getSnapshotBeforeUpdate(prev: { order: string }) {
    const ul = this.list.current;
    if (prev.order === this.props.order || !ul || still()) return null;
    const rows = [...ul.querySelectorAll<HTMLElement>(":scope > [data-flip-id]")];
    const next = new Set(this.props.order.split("|"));
    const top = ul.getBoundingClientRect().top;
    const vh = window.innerHeight;
    const seen = new Set<string>();
    const leaving: { clone: HTMLElement; top: number; height: number }[] = [];
    for (const el of rows) {
      const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) continue;
      const id = el.dataset.flipId ?? "";
      seen.add(id);
      if (!next.has(id)) {
        const clone = el.cloneNode(true) as HTMLElement;
        clone.removeAttribute("data-flip-id");
        leaving.push({ clone, top: r.top - top, height: r.height });
      }
    }
    return { state: Flip.getState(rows), seen, leaving };
  }

  componentDidUpdate(
    _p: unknown,
    _s: unknown,
    snap: { state: Flip.FlipState; seen: Set<string>; leaving: { clone: HTMLElement; top: number; height: number }[] } | null,
  ) {
    const ul = this.list.current;
    if (!snap || !ul) return;

    // A change mid-flight finishes the old one first; the snapshot above already
    // holds where the rows visibly were, so the new move starts from there.
    this.tl?.progress(1).kill();

    const vh = window.innerHeight;
    const targets = [...ul.querySelectorAll<HTMLElement>(":scope > [data-flip-id]")].filter((el) => {
      if (snap.seen.has(el.dataset.flipId ?? "")) return true;
      const r = el.getBoundingClientRect();
      return r.bottom >= 0 && r.top <= vh;
    });

    this.tl = Flip.from(snap.state, {
      targets,
      duration: DUR,
      ease: EASE,
      simple: true,
      stagger: { amount: Math.min(0.3, targets.length * 0.012) },
      onEnter: (els) =>
        gsap.fromTo(
          els,
          { opacity: 0, y: 8 },
          { opacity: 1, y: 0, duration: 0.35, ease: EASE, stagger: { amount: Math.min(0.2, els.length * 0.015) }, clearProps: "opacity,transform" },
        ),
    });

    const layer = this.ghosts.current;
    if (!layer) return;
    for (const g of snap.leaving) {
      const c = g.clone;
      Object.assign(c.style, {
        position: "absolute", left: "0", right: "0", top: `${g.top}px`, height: `${g.height}px`,
        transform: "none", translate: "none", opacity: "1", overflow: "hidden",
      });
      layer.appendChild(c);
      gsap.to(c, { opacity: 0, height: 0, duration: 0.3, ease: "power1.in", onComplete: () => c.remove() });
    }
  }

  componentWillUnmount() {
    this.tl?.kill();
    const layer = this.ghosts.current;
    if (layer) for (const c of [...layer.children]) { gsap.killTweensOf(c); c.remove(); }
  }

  render() {
    return (
      <div className="relative">
        <ul ref={this.list} className={this.props.className}>{this.props.children}</ul>
        <div ref={this.ghosts} aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden" />
      </div>
    );
  }
}

function Why({ scored, weights }: { scored: Scored; weights: Weights; fields: FieldKey[] }) {
  const c = scored.company;
  const ordered = [...TERMS].sort(
    (a, b) => scored.terms[b.key].contribution - scored.terms[a.key].contribution,
  );
  const max = Math.max(...ordered.map((t) => scored.terms[t.key].contribution), 0.001);

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[13px] font-medium text-ink">{c.name}</span>
        <span className="tnum text-[12px] text-ink">{Math.round(scored.score * 100)}<span className="text-ink-3">/100</span></span>
        <span className="text-[11.5px] text-ink-3">{c.segment_label}</span>
        <a href={`/companies/${c.id}`} className="ml-auto text-[11.5px] text-ink underline-offset-2 hover:underline">
          Open →
        </a>
      </div>
      <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {ordered.map((t) => {
          const term = scored.terms[t.key];
          return (
            <li key={t.key} className="flex items-baseline gap-2 text-[11px]">
              <span className="w-[7.5rem] shrink-0 text-ink-2">{t.label}</span>
              <span className="bar-track relative h-[6px] w-14 shrink-0">
                <span
                  className="atlas-bar absolute inset-y-0 left-0 w-full"
                  style={{
                    transform: `scaleX(${term.contribution / max})`,
                    background: weights[t.key] > 0.01 ? "var(--data-2)" : "var(--data-5)",
                  }}
                />
              </span>
              <span className="min-w-0 flex-1 truncate text-ink-3" title={term.why}>{term.why}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
