#!/usr/bin/env python3
"""Draw the pictures in the README — in the dashboard's own language.

The dashboard is monochrome by instruction: white or black, a hairline-framed plate as the
one structural device, weight and position doing the work a palette would. These figures
follow the same brief, so the README looks like the product and not like a template.

Each figure is written twice — a light and a dark file — and the README picks one with a
<picture> element on prefers-color-scheme. Numbers live in NUMBERS below; refresh them from
the live database and re-run:

    python docs/assets/make.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Read from jobhunter.db on 1 Nov 2026. Keep in step with the table in README.md.
NUMBERS = {
    "date": "1 Nov 2026",
    "leads": 45_867,
    "current": 21_545,        # affiliation verified-current + likely-current
    "found": 31_369,          # contact.email_unverified set — kept, never shown, never mailed
    "proved": 248,            # contact.email_status = real
    "companies": 6_512,
    "bengaluru": 1_760,
    "hyderabad": 545,
    "roles": 10_354,
    "fresher": 6_737,         # not is_senior
    "best_bets": 250,
    "checks": 18_269,
    "nodes": 63_982,
    "edges": 205_776,
}

THEMES = {
    # the dashboard's zinc scale, with the plate edge it settled on (2.4:1 — visible, not loud)
    "light": dict(ink="#09090b", ink2="#52525b", ink3="#8e8e97", edge="#a6a6af", line="#e3e3e6",
                  soft="#f4f4f5", mid="#e4e4e8", paper="#ffffff"),
    "dark": dict(ink="#fafafa", ink2="#a1a1aa", ink3="#8b8b94", edge="#51515c", line="#2a2a30",
                 soft="#1a1a1e", mid="#2b2b31", paper="#0d1117"),
}

SANS = '"Inter Tight", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'
DISPLAY = '"Bricolage Grotesque", "Inter Tight", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'
MONO = '"Azeret Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace'


def n(v: int) -> str:
    return f"{v:,}"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Canvas:
    def __init__(self, w: int, h: int, t: dict, label: str):
        self.w, self.h, self.t = w, h, t
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{esc(label)}" font-family=\'{SANS}\'>',
        ]

    def add(self, s: str) -> None:
        self.parts.append("  " + s)

    def text(self, x, y, s, size=14, fill=None, weight=400, anchor="start", family=None, spacing=None, opacity=None):
        fill = fill or self.t["ink"]
        extra = f" font-family='{family}'" if family else ""
        extra += f' letter-spacing="{spacing}"' if spacing is not None else ""
        extra += f' fill-opacity="{opacity}"' if opacity is not None else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                 f'text-anchor="{anchor}"{extra}>{esc(s)}</text>')

    def plate(self, x, y, w, h, fill=None, stroke=None, sw=1, rx=0):
        fill = fill or "none"
        stroke = stroke or self.t["edge"]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def rect(self, x, y, w, h, fill):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>')

    def line(self, x1, y1, x2, y2, stroke=None, sw=1, dash=None, marker=False, cap="butt"):
        stroke = stroke or self.t["edge"]
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += ' marker-end="url(#arrow)"' if marker else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}" '
                 f'stroke-linecap="{cap}"{extra}/>')

    def path(self, d, stroke=None, sw=1, fill="none", marker=False, cap="round", join="round", dash=None):
        stroke = stroke or self.t["ink"]
        extra = ' marker-end="url(#arrow)"' if marker else ""
        extra += f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
                 f'stroke-linecap="{cap}" stroke-linejoin="{join}"{extra}/>')

    def circle(self, cx, cy, r, fill, stroke="none", sw=0):
        self.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def arrow_defs(self, color=None):
        color = color or self.t["ink2"]
        self.add('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                 f'orient="auto-start-reverse"><path d="M1 1.5L9 5L1 8.5z" fill="{color}"/></marker></defs>')

    def mark(self, x, y, size):
        """The Threshold mark from dashboard/app/icon.svg: a tile with the route cut through it,
        ending in a person. Drawn in ink on the paper, at `size` px."""
        s = size / 32
        self.add(f'<g transform="translate({x} {y}) scale({s})">'
                 '<mask id="mark"><rect width="32" height="32" fill="#fff"/>'
                 '<path d="M8.5 9.5H22.5L9.5 22.5H19" fill="none" stroke="#000" stroke-width="3.4" '
                 'stroke-linecap="round" stroke-linejoin="round"/>'
                 '<circle cx="23.2" cy="22.5" r="4" fill="#000"/><circle cx="23.2" cy="22.5" r="1.7" fill="#fff"/></mask>'
                 f'<rect width="32" height="32" rx="9" fill="{self.t["ink"]}" mask="url(#mark)"/></g>')

    def write(self, path: Path) -> None:
        self.parts.append("</svg>\n")
        path.write_text("\n".join(self.parts))


# ----------------------------------------------------------------------------- banner

def banner(t: dict) -> Canvas:
    c = Canvas(1200, 260, t, "ZoNuLy: from a funded company to a person who works there, with the address proved on the way")
    c.plate(0.5, 0.5, 1199, 259, rx=6)

    # the mark and the wordmark share a baseline
    c.mark(48, 56, 60)
    c.text(126, 106, "ZoNuLy", size=62, weight=800, family=DISPLAY, spacing="-2.5")
    c.text(50, 160, "Finds underrated companies hiring freshers, scores your odds,", size=20, fill=t["ink2"])
    c.text(50, 188, "finds a real person inside, and writes the referral ask.", size=20, fill=t["ink2"])
    c.text(50, 226, "local-first  ·  evidence-bound  ·  never guesses an email address", size=13, fill=t["ink3"], family=MONO)

    # the route, at mechanism scale: the same Z the mark carries, with its four stops named
    x0, x1, y0, y1 = 700, 1112, 78, 182
    xk = 748                      # where the diagonal lands on the bottom run
    xp = 1112                     # the person
    c.path(f"M{x0} {y0}H{x1}L{xk} {y1}H{xp - 30}", stroke=t["ink"], sw=6)
    c.circle(xp, y1, 15, t["ink"])
    c.circle(xp, y1, 6, t["paper"])
    for x, y in ((x0, y0), (x1, y0), (xk, y1)):
        c.circle(x, y, 7, t["ink"])
        c.circle(x, y, 3, t["paper"])

    def stop(num, x, y, lines, anchor):
        c.text(x, y, num, size=11, fill=t["ink3"], family=MONO, anchor=anchor)
        for i, s in enumerate(lines):
            c.text(x, y + 17 + i * 16, s, size=13, fill=t["ink2"], anchor=anchor)

    stop("01  COMPANY", x0, 36, ["funded, not famous,", "pays a fresher ≥ ₹20 LPA"], "start")
    stop("02  ROLE", x1, 36, ["0–100 odds of selection,", "with the sources"], "end")
    stop("03  PROOF", xk - 12, 206, ["proved by SMTP, or nothing"], "start")
    stop("04  PERSON", xp + 16, 206, ["works there now — the ask"], "end")
    return c


# ---------------------------------------------------------------------------- numbers

def numbers(t: dict) -> Canvas:
    N = NUMBERS
    c = Canvas(1200, 372, t, f"Of {n(N['leads'])} people found, {n(N['proved'])} have a proved address a mail may go to; "
                             f"{n(N['found'])} found addresses are kept unverified and never mailed")
    L, W = 40, 1120                      # the bar is the whole population
    scale = W / N["leads"]

    c.text(L, 34, "PEOPLE", size=11, fill=t["ink3"], family=MONO, spacing="1")
    c.text(1160, 34, f"live database · {N['date']}", size=12, fill=t["ink3"], family=MONO, anchor="end")

    # bar 1: everyone found, and the part with evidence of working there now
    y = 52
    c.plate(L + 0.5, y + 0.5, W - 1, 30, fill=t["soft"], stroke=t["edge"])
    cw = round(N["current"] * scale)
    c.rect(L + 1, y + 1, cw - 1, 29, t["mid"])
    c.text(L + 12, y + 21, n(N["leads"]), size=15, weight=700)
    c.text(L + 12 + 70, y + 21, "people at target companies, each with where they were found", size=14, fill=t["ink2"])
    c.line(L + cw, y + 31, L + cw, y + 46, stroke=t["ink2"])
    c.text(L + cw - 6, y + 60, f"{n(N['current'])} work there now — profile, team page, org membership or live mailbox on file",
           size=13, fill=t["ink2"], anchor="end")

    # bar 2: the same population, by what is known about the address
    y = 162
    c.text(L, y - 12, "ADDRESSES", size=11, fill=t["ink3"], family=MONO, spacing="1")
    c.plate(L + 0.5, y + 0.5, W - 1, 30, fill=t["soft"], stroke=t["edge"])
    fw = round(N["found"] * scale)
    pw = max(4, round(N["proved"] * scale))        # 248 of 45,867 is six pixels wide — that is the picture
    c.rect(L + 1, y + 1, fw - 1, 29, t["mid"])
    c.rect(L + 1, y + 1, pw, 29, t["ink"])
    c.text(L + pw + 12, y + 21, n(N["proved"]), size=15, weight=700)
    c.text(L + pw + 12 + 44, y + 21, "proved — the only addresses a mail may go to", size=14, fill=t["ink2"])
    c.line(L + fw, y + 31, L + fw, y + 46, stroke=t["ink2"])
    c.text(L + fw - 6, y + 60, f"{n(N['found'])} addresses found on pages, commits and profiles — kept unverified, never shown, never mailed",
           size=13, fill=t["ink2"], anchor="end")
    c.text(L + fw + 8, y + 60, f"{n(N['leads'] - N['found'])} with no address yet", size=13, fill=t["ink3"])

    # the rest of the exhibit, as a row of figures
    y = 284
    c.line(L, y - 30, L + W, y - 30, stroke=t["line"])
    cols = [
        (n(N["companies"]), "companies", f"{n(N['bengaluru'])} Bengaluru · {n(N['hyderabad'])} Hyderabad"),
        (n(N["roles"]), "open roles", f"{n(N['fresher'])} of them not senior"),
        (n(N["best_bets"]), "best bets", "the decision brain's shortlist"),
        (n(N["checks"]), "address checks", "every SMTP verdict, cached"),
        (n(N["nodes"]), "graph nodes", f"{n(N['edges'])} edges · every table and decision"),
    ]
    step = W / len(cols)
    for i, (big, lab, sub) in enumerate(cols):
        x = L + round(i * step)
        if i:
            c.line(x - 18, y - 12, x - 18, y + 46, stroke=t["line"])
        c.text(x, y + 14, big, size=30, weight=600, family=DISPLAY, spacing="-1")
        c.text(x, y + 36, lab, size=13, weight=600)
        c.text(x, y + 56, sub, size=12.5, fill=t["ink3"])
    return c


# --------------------------------------------------------------------------- pipeline

def pipeline(t: dict) -> Canvas:
    c = Canvas(1200, 362, t, "The pipeline: discover, admit, score, find referrers, prove the address, draft, review, send; "
                             "three gates drop what fails, with the reason logged")
    c.arrow_defs()
    stages = [
        ("Discover", False, "ATS boards, careers pages, funding news"),
        ("Admit", True, "one gate for every company"),
        ("Score", False, "selection odds, 0–100, itemised"),
        ("Referrers", False, "people who work there now"),
        ("Prove", True, "SMTP check on every address"),
        ("Draft", False, "needs brief → the ask, in your voice"),
        ("Review", True, "rules approve or reject, with reason"),
        ("Send", False, "25 a day, spread out, one per company"),
    ]
    moves = ["company", "company + roles", "role · odds", "person + evidence", "proved address", "draft", "approved", ""]
    drops = {1: "refused: famous, body shop, < ₹20 LPA, wrong city",
             4: "unproved: kept, never shown, never mailed",
             6: "rejected: the reason goes to autoreview.log"}

    L, R, Y = 70, 1130, 150
    step = (R - L) / (len(stages) - 1)
    xs = [L + round(i * step) for i in range(len(stages))]

    c.text(40, 34, "PIPELINE", size=11, fill=t["ink3"], family=MONO, spacing="1")
    c.text(1160, 34, "tick every 10 min · launchd", size=12, fill=t["ink3"], family=MONO, anchor="end")

    # the track
    for i in range(len(xs) - 1):
        c.line(xs[i] + 12, Y, xs[i + 1] - 14, Y, stroke=t["ink2"], sw=1.5, marker=True)
        if moves[i]:
            c.text((xs[i] + xs[i + 1]) / 2, Y + 24, moves[i], size=12, fill=t["ink2"], family=MONO, anchor="middle")

    for (name, gate, sub), x in zip(stages, xs):
        if gate:   # a gate is a filled plate, turned on its corner
            c.path(f"M{x} {Y-12}L{x+12} {Y}L{x} {Y+12}L{x-12} {Y}Z", stroke=t["ink"], sw=1.5, fill=t["ink"])
        else:
            c.plate(x - 10, Y - 10, 20, 20, fill=t["paper"], stroke=t["ink"], sw=1.5)
        c.text(x, Y - 56, name, size=16, weight=700, anchor="middle", family=DISPLAY)
        # the one-line description, wrapped by hand at the space nearest the middle
        words = sub.split(" ")
        cut = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(sub) / 2))
        c.text(x, Y - 36, " ".join(words[:cut]), size=12.5, fill=t["ink2"], anchor="middle")
        c.text(x, Y - 21, " ".join(words[cut:]), size=12.5, fill=t["ink2"], anchor="middle")

    # what leaves the track at each gate
    for i, why in drops.items():
        x = xs[i]
        c.line(x, Y + 14, x, Y + 74, stroke=t["ink2"], sw=1.5, dash="3 3", marker=True)
        c.text(x, Y + 96, why, size=12.5, fill=t["ink2"], anchor="middle")

    # replies come back in
    xe = xs[-1]
    c.path(f"M{xe} {Y+14}V{Y+126}H{xs[5]}V{Y+16}", stroke=t["ink3"], sw=1.2, fill="none", dash="4 3", marker=True)
    c.text((xe + xs[5]) / 2, Y + 146, "replies read back in: sentiment, a calendar event, one follow-up after silence", size=12, fill=t["ink3"], anchor="middle")

    c.line(40, 322, 1160, 322, stroke=t["line"])
    c.text(40, 346, "Every write passes a validator for its field; a refused value is logged with its reason. The orchestrator is code, not a model.",
           size=12.5, fill=t["ink3"])
    return c


# ----------------------------------------------------------------------- architecture

def architecture(t: dict) -> Canvas:
    c = Canvas(1200, 400, t, "Five layers, imports only point downward: dashboard, api, pipeline and knowledge graph, "
                             "scrapers and contacts, database with guardrails; launchd drives the pipeline, "
                             "the public instance serves a read-only snapshot")
    c.arrow_defs()
    layers = [
        ("dashboard", "Next.js · 51 pages · monochrome, light and dark", ":3000"),
        ("api", "FastAPI · read-only in public snapshot mode", ":8000"),
        ("pipeline · matcher · kg", "the tick · decision brain · selection odds · the graph", "tick.py"),
        ("scrapers · contacts · outreach", "sources · referrers with evidence · address proof · drafts · review", ""),
        ("db · llm · normalize", "SQLite (WAL) · validators · proved-only write hook", "jobhunter.db"),
    ]
    L, W, H, GAP, Y0 = 40, 720, 48, 20, 56
    c.text(40, 34, "LAYERS — NOTHING IMPORTS UPWARD", size=11, fill=t["ink3"], family=MONO, spacing="1")

    ys = []
    for i, (name, desc, tag) in enumerate(layers):
        y = Y0 + i * (H + GAP)
        ys.append(y)
        last = i == len(layers) - 1
        c.plate(L + 0.5, y + 0.5, W - 1, H - 1, fill=t["ink"] if last else t["paper"], stroke=t["ink"] if last else t["edge"])
        fg, fg2 = (t["paper"], t["paper"]) if last else (t["ink"], t["ink2"])
        c.text(L + 16, y + 30, name, size=16, weight=700, fill=fg, family=DISPLAY)
        c.text(L + 296, y + 30, desc, size=13, fill=fg2)
        if tag:
            c.text(L + W - 14, y + 30, tag, size=12, fill=fg2, family=MONO, anchor="end")
        if not last:
            c.line(L + 60, y + H, L + 60, y + H + GAP - 2, stroke=t["ink2"], sw=1.5, marker=True)
        if i == 0:
            c.text(L + 70, y + H + 14, "imports", size=10.5, fill=t["ink3"], family=MONO)

    # what talks to the stack from outside, at the layer it touches
    X = L + W + 60
    c.text(X, 34, "OUTSIDE", size=11, fill=t["ink3"], family=MONO, spacing="1")

    def side(i, lines, label):
        y = ys[i] + H / 2
        c.line(L + W, y, X - 10, y, stroke=t["ink2"], sw=1.2, dash="3 3")
        c.text(X - 12, y - 6, label, size=10.5, fill=t["ink3"], family=MONO, anchor="end")
        for k, s in enumerate(lines):
            c.text(X, y - (len(lines) - 1) * 8 + k * 16 + 5, s, size=13, fill=t["ink2"])

    side(0, ["the browser"], "")
    side(1, ["Vercel proxies to it · Catalyst serves demo.db,", "a stamped snapshot with the private tables emptied"], "public")
    side(2, ["launchd: tick every 10 min, paced send agent,", "hourly audit — the orchestrator is code (D4)"], "schedule")
    side(3, ["ATS boards · careers pages · funding feeds · GitHub", "Apify behind a ledger and a daily cap", "Reacher (check-if-email-exists) for address proof"], "sources")
    side(4, ["OpenRouter aliases behind a spend ledger —", "no local model runtime"], "models")
    return c


def main() -> None:
    for theme, t in THEMES.items():
        suffix = "" if theme == "light" else "-dark"
        for name, draw in (("banner", banner), ("numbers", numbers), ("pipeline", pipeline), ("architecture", architecture)):
            out = HERE / f"{name}{suffix}.svg"
            draw(t).write(out)
            print(out.relative_to(HERE.parent.parent))


if __name__ == "__main__":
    main()
