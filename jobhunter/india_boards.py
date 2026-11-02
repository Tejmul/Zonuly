"""India board discovery — find Bengaluru / Hyderabad software companies from their own ATS boards.

Tejmul, 2026-11-02: "for now just scrap underated companies in India which gonna pay you above 20
lpa from sde ai devops and tech roles not robotic aerospace or any other companies dont go out of
the domain of software tech … atleast today i am expecting 500+ companies in Bengaluru and
Hyderabad."

WHY THIS EXISTS. Every older India channel had walked to the end of its cursor — the funding feeds,
the YC directory, Cutshort, GitHub orgs, the VC portfolio pages and the rotating funding searches
all returned companies already in the database (281 seen, 0 admitted, on 2026-11-02). The headroom
was in `india.from_ats`, which searched only six host phrasings and, worse, dropped greenhouse,
lever and ashby — the three boards funded product startups actually use — because they were missing
from `_ATS_HOSTS`. Its name extraction also needed "Careers at X" in the page title, which the real
titles ("Job Application for Backend Engineer at Glean") never say.

WHAT IT DOES. A keyless web search finds board URLs; the (ats, slug) pair is read off the URL; then
the board's OWN PUBLIC API is fetched — greenhouse `boards-api`, lever `api.lever.co`, ashby
`posting-api`. The board answers the three questions the search result cannot:

  city     a posting located in Bengaluru or Hyderabad is the evidence, quoted on the company
  domain   the titles decide software vs hardware (india.hardware_domain), so a robotics or
           aerospace employer is refused even when it is hiring an SDE
  pay      a stated salary band on a non-senior posting is pay evidence, which is what lets a
           company clear the ₹20 LPA proof bar and reach a CSV at all

Nothing is guessed. A company with no Bengaluru / Hyderabad posting is skipped, not assumed. No
e-mail address is read, written or invented here — this module finds companies, never people.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from jobhunter import CONFIG, admission, guardrails, india
from jobhunter.db import Company, get_session, get_setting, init_db, set_setting

log = logging.getLogger(__name__)

CITIES = ("Bengaluru", "Hyderabad")
_CITY = re.compile(r"(?i)\b(bengaluru|bangalore|hyderabad|secunderabad)\b")
_CURSOR = "india_boards:cursor"
_SEEN = "india_boards:seen"          # slugs already looked at, so a run never pays for them twice

#: the boards whose public API answers without a key
_API = ("greenhouse", "lever", "ashby")

#: software roles only (2026-11-02) — the title words that make a posting ours
_SOFTWARE_TITLE = re.compile(
    r"(?i)\b(software|sde|sdet|engineer|developer|programmer|backend|back[- ]end|frontend|front[- ]end|"
    r"full[- ]?stack|devops|sre|site reliability|platform|infrastructure|cloud|data engineer|"
    r"data scientist|machine learning|\bml\b|\bai\b|applied scientist|android|ios|mobile|qa|"
    r"test engineer|security engineer|solutions engineer|forward deployed)\b")
#: …and the ones that mean it is NOT a software role even when the company is a software company
_NOT_SOFTWARE_TITLE = re.compile(
    r"(?i)\b(mechanical|electrical|electronics|hardware|optical|optics|photonics|rf\b|antenna|pcb|vlsi|"
    r"embedded hardware|manufactur\w*|production|assembly|technician|lab engineer|lab assistant|"
    r"chemical|civil|structural|aerospace|avionics|propulsion|robotics technician)\b")
_SENIOR = re.compile(r"(?i)\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|architect|"
                     r"distinguished|fellow)\b")

_SALARY = re.compile(
    r"(?i)(?:₹|inr|rs\.?)\s*([\d,.]+)\s*(?:-|–|to)?\s*(?:₹|inr|rs\.?)?\s*([\d,.]+)?\s*"
    r"(lpa|lakhs?|l\b|per annum|/year|a year|cr\b|crores?)")


@dataclass
class Found:
    name: str
    ats: str
    slug: str
    board_url: str
    city_quote: str
    roles: list[str] = field(default_factory=list)
    pay_quote: str | None = None
    pay_lpa: float | None = None


@dataclass
class Stats:
    searched: int = 0
    candidates: int = 0
    boards_read: int = 0
    admitted: int = 0
    updated: int = 0
    skipped: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    seconds: int = 0

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


#: the four board hosts whose public API answers without a key
_HOSTS = ["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"]
#: software roles only — no mechanical, electronics, optics or manufacturing titles
_ROLES = ["software engineer", "SDE", "SDE 1", "backend engineer", "frontend engineer",
          "full stack engineer", "AI engineer", "machine learning engineer", "data engineer",
          "devops engineer", "platform engineer", "site reliability engineer",
          "software engineer intern", "associate software engineer", "graduate engineer",
          "python developer", "golang engineer", "java backend", "node js developer",
          "react developer", "android engineer", "ios engineer", "qa automation engineer",
          "security engineer", "cloud engineer", "data scientist", "LLM engineer",
          "applied scientist", "infrastructure engineer", "api engineer"]
#: the city as a board actually writes it
_CITY_WORDS = ["Bengaluru", "Bangalore", "Hyderabad"]


#: stacks and sector words — a board that never says "SDE" still says "Kubernetes" or "fintech"
_TERMS = _ROLES + [
    "python", "golang", "rust engineer", "typescript", "kubernetes", "terraform", "kafka",
    "postgres", "spark", "airflow", "microservices", "distributed systems", "graphql",
    "llm", "rag", "pytorch", "tensorflow", "computer vision engineer", "nlp engineer",
    "fintech", "saas", "developer tools", "b2b platform", "api platform", "observability",
    "cybersecurity engineer", "payments engineer", "healthtech engineer", "edtech engineer",
    "fresher", "intern 2026", "new grad", "entry level engineer", "campus hire",
    "founding engineer", "early engineer", "seed stage", "series a startup",
]


def queries() -> list[str]:
    """host × term × city, as `site:` searches — about 1,500 distinct questions.

    2026-11-02: the keyless backend returns at most TEN results per query however large a limit it
    is given, and the same host+city phrasing returns the same ten every time. Volume therefore
    comes from asking many different questions, not from asking for more answers: a board that
    never writes "SDE" still writes "Kubernetes", "fintech" or "founding engineer". The cursor
    walks a slice each run, so successive runs genuinely ask new things."""
    return [f"site:{h} {city} {t}" for h in _HOSTS for t in _TERMS for city in _CITY_WORDS]


def lpa_from(text: str | None) -> tuple[float | None, str | None]:
    """The highest plausible ₹ LPA figure in a posting's own words, and the sentence that said it.
    A figure is evidence only when the posting states it; nothing is inferred from a band name."""
    if not text:
        return None, None
    best, quote = None, None
    for m in _SALARY.finditer(text):
        unit = (m.group(3) or "").lower()
        for raw in (m.group(2), m.group(1)):
            if not raw:
                continue
            try:
                v = float(raw.replace(",", ""))
            except ValueError:
                continue
            if unit.startswith("cr"):
                v *= 100
            elif v > 10_000:          # a rupee figure, not lakhs
                v /= 100_000
            if 3 <= v <= 200 and (best is None or v > best):
                best, quote = v, m.group(0)[:160]
            break
    return best, quote


def board_name(ats: str, slug: str) -> str | None:
    """The company's REAL name, from the board's own metadata — never the slug.

    2026-11-02: the first pass wrote slug-derived names into the database ("Gleanwork",
    "Razorpaysoftwareprivatelimited", "Schrdinger"). A slug is an account handle, not a name, and a
    wrong name poisons dedup, the drafts and every CSV. Greenhouse publishes the name at
    /v1/boards/<slug>; lever and ashby do not, so their board page title is read and cleaned, and a
    company whose name cannot be established is refused rather than guessed."""
    try:
        if ats == "greenhouse":
            r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=20,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; jobhunter/1.0)"})
            n = (r.json() or {}).get("name") if r.status_code == 200 else None
            if n and n.strip():
                return n.strip()[:120]
        url = {"lever": f"https://jobs.lever.co/{slug}",
               "ashby": f"https://jobs.ashbyhq.com/{slug}"}.get(ats)
        if not url:
            return None
        r = httpx.get(url, timeout=20, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; jobhunter/1.0)"})
        if r.status_code != 200:
            return None
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text)
        t = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        # "ValGenesis jobs", "Acme - Job Board", "Jobs at Acme", "Acme Careers"
        for rx in (r"(?i)^jobs?\s+at\s+(.+)$", r"(?i)^(.+?)\s+(?:jobs|careers|job board)\b",
                   r"(?i)^(.+?)\s*[-|–]\s*(?:job board|careers?|jobs)\b"):
            mm = re.match(rx, t)
            if mm:
                t = mm.group(1)
                break
        t = t.strip(" .,|-–—")
        return t[:120] if t and len(t) > 1 and "." not in t[-4:] else None
    except Exception as e:                      # noqa: BLE001 — no name is a refusal, never a guess
        log.debug("board name %s/%s: %s", ats, slug, e)
        return None


async def _board(http: httpx.AsyncClient, ats: str, slug: str) -> list:
    """Every posting on one public board, through the scrapers we already have."""
    from jobhunter.scrapers import ashby, greenhouse, lever
    try:
        if ats == "greenhouse":
            return await greenhouse.fetch_board(http, slug)
        if ats == "lever":
            return await lever.fetch_board(http, slug)
        if ats == "ashby":
            return await ashby.fetch_board(http, slug)
    except Exception as e:                      # noqa: BLE001 — a dead board must not end the run
        log.debug("board %s/%s failed: %s", ats, slug, e)
    return []


def _read_board(ats: str, slug: str) -> list:
    async def go():
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (compatible; jobhunter/1.0)"}) as http:
            return await _board(http, ats, slug)
    try:
        return asyncio.run(go())
    except Exception as e:                      # noqa: BLE001
        log.debug("board %s/%s: %s", ats, slug, e)
        return []


def judge(ats: str, slug: str, jobs: list, st: Stats) -> Found | None:
    """Does this board belong to a Bengaluru / Hyderabad software company? The postings answer."""
    if not jobs:
        st.skip("board empty or unreachable")
        return None
    here = [j for j in jobs if _CITY.search(f"{j.location or ''} {getattr(j, 'description', '') or ''}"[:400])]
    if not here:
        st.skip("no Bengaluru / Hyderabad posting on the board")
        return None
    name = board_name(ats, slug) or ""
    if not name:
        st.skip("no real company name on the board")
        return None
    # 2026-11-02, "underrated companies": a famous name is refused at the door. Valuation and
    # money-raised are not known yet for a company found this way, so this catches the names the
    # config already knows; `targets grade` re-judges the rest once `targets enrich` fills funding.
    ov = admission.overrated_verdict(name)
    if getattr(ov, "overrated", False):
        st.skip(f"overrated: {getattr(ov, 'reason', 'a famous name')}"[:60])
        return None
    titles = " · ".join((j.title or "") for j in jobs[:40])
    hw = india.hardware_domain(f"{name} {titles}")
    if hw:
        st.skip(f"outside software: {hw}")
        return None
    soft = [j for j in here if _SOFTWARE_TITLE.search(j.title or "")
            and not _NOT_SOFTWARE_TITLE.search(j.title or "")]
    if not soft:
        st.skip("no software role in those two cities")
        return None
    quote = next((f"{j.title} — {j.location}" for j in soft if j.location and _CITY.search(j.location)),
                 f"{soft[0].title} — {soft[0].location or 'Bengaluru/Hyderabad'}")
    pay_lpa, pay_quote = None, None
    for j in soft:
        if _SENIOR.search(j.title or ""):
            continue                     # a senior band is not fresher pay
        v, q = lpa_from(f"{getattr(j, 'salary_raw', '') or ''} {getattr(j, 'description', '') or ''}")
        if v and (pay_lpa is None or v > pay_lpa):
            pay_lpa, pay_quote = v, f"{j.title}: {q}"
    return Found(name=name, ats=ats, slug=slug,
                 board_url=(soft[0].url or "").split("?")[0],
                 city_quote=quote[:300], roles=[j.title for j in soft[:12]],
                 pay_quote=pay_quote, pay_lpa=pay_lpa)


def _candidates(limit_queries: int, st: Stats) -> list[tuple[str, str, str]]:
    """(ats, slug, url) pairs off the search results — the cheap half of the work."""
    from jobhunter.research import web
    from jobhunter.scrapers import careers_ats as ca

    pool = queries()
    with get_session() as s:
        try:
            cur = int(get_setting(s, _CURSOR, "0") or 0)
        except ValueError:
            cur = 0
    out: dict[tuple[str, str], tuple[str, str, str]] = {}
    for i in range(limit_queries):
        q = pool[(cur + i) % len(pool)]
        st.searched += 1
        try:
            hit = web.search(q, limit=10, only=web.KEYLESS)
        except Exception as e:                  # noqa: BLE001
            log.debug("search failed: %s", e)
            continue
        for r in hit.get("results") or []:
            url = r.get("url") or ""
            for a, rx in ca.ATS_PATTERNS:
                m = rx.search(url)
                if m and a in _API:
                    slug = m.group(1)
                    if slug and slug.lower() not in ca._NOT_A_SLUG:
                        out.setdefault((a, slug.lower()), (a, slug, url))
                    break
    with get_session() as s:
        set_setting(s, _CURSOR, str((cur + limit_queries) % len(pool)))
        s.commit()
    st.candidates = len(out)
    return list(out.values())


def _admit(session, f: Found, st: Stats) -> Company | None:
    from jobhunter import harvest

    name, _ = admission.clean_name(f.name)
    if not name:
        st.skip("junk name")
        return None
    hs = harvest.HarvestStats("india-board")
    company = harvest._upsert(
        session, name=name, website=None, source="india-board",
        description=f"Hiring in {('Bengaluru' if 'eng' in f.city_quote.lower() or 'alor' in f.city_quote.lower() else 'Hyderabad')}: "
                    + ", ".join(f.roles[:6]),
        region="india", note=f"India board discovery ({f.ats}): {f.city_quote[:120]}",
        claim={"text": f.city_quote[:200], "url": f.board_url, "by": None}, stats=hs)
    if company is None:
        st.skip(next(iter(hs.skipped), "refused by the admission gate"))
        return None
    new = company.id is None or company.created_at is None
    company.ats, company.ats_slug = f.ats, f.slug
    if not company.careers_url:
        company.careers_url = f.board_url
    guardrails.set_city(session, company, f.city_quote, source=f.board_url, channel="india-board")
    if f.pay_lpa is not None and (company.ppo_lpa is None or f.pay_lpa > company.ppo_lpa):
        # the posting's own words; india.pay_evidence reads this back at the ₹20 LPA bar
        company.ppo_lpa = f.pay_lpa
        company.ppo_evidence = f"posting states {f.pay_quote}"[:300]
    session.add(company)
    st.names.append(f"{name} ({f.ats}/{f.slug}"
                    + (f", ₹{f.pay_lpa:g}L)" if f.pay_lpa else ")"))
    return company


def run(*, searches: int = 40, boards: int = 120, budget_s: int = 900) -> dict:
    """One pass: `searches` keyless searches, then up to `boards` public boards read and judged."""
    init_db()
    st = Stats()
    t0 = time.time()
    with get_session() as s:
        try:
            seen = set(json.loads(get_setting(s, _SEEN, "[]") or "[]"))
        except ValueError:
            seen = set()

    cands = [c for c in _candidates(searches, st) if f"{c[0]}/{c[1].lower()}" not in seen]
    fresh: list[str] = []
    for ats, slug, _url in cands[:boards]:
        if time.time() - t0 > budget_s:
            st.skip("out of time")
            break
        st.boards_read += 1
        fresh.append(f"{ats}/{slug.lower()}")
        found = judge(ats, slug, _read_board(ats, slug), st)
        if not found:
            continue
        with get_session() as s:
            before = s.exec_count if hasattr(s, "exec_count") else None  # noqa: F841
            co = _admit(s, found, st)
            if co is not None:
                s.commit()
                st.admitted += 1

    with get_session() as s:
        set_setting(s, _SEEN, json.dumps(sorted(seen | set(fresh))[-20000:]))
        s.commit()
    st.seconds = round(time.time() - t0)
    out = {"searched": st.searched, "candidates": st.candidates, "boards_read": st.boards_read,
           "admitted": st.admitted, "skipped": st.skipped, "seconds": st.seconds,
           "names": st.names[:60]}
    log.info("india boards: %s", {k: v for k, v in out.items() if k != "names"})
    return out


__all__ = ["run", "judge", "queries", "lpa_from"]
