"""Referrers named in public — talks, podcasts / YouTube, the company's own blog bylines.

2026-10-14, "leads first": the LinkedIn search finds most people, but a company whose
staff keep LinkedIn quiet still puts its engineers on stage, on podcasts and on its own
blog. Every channel here is public and keyless — no account of the user's is touched:

    youtube   yt-dlp's search (metadata only, no download): a video whose title or
              description names "Sunny Shah - Head of Engineering at Sarvam", with its
              upload date
    talks     the keyless web search (web.KEYLESS) for talks, meetups, conference
              speaker pages and podcast episodes naming someone at the company
    blog      the company's own /blog (and /engineering) read through Jina: author bylines

A person is kept only when the same sentence names them, the company (guardrails.
names_company) and not as a former employer — and the page is DATED inside the last
MAX_AGE_DAYS (an upload date, a year in the result, a dated post). That evidence labels
them likely-current (guardrails.affiliation `public_mention`) with the quote and the URL.
No address is invented: the company's confirmed pattern (contacts/addresses.py) gives
one later, or nobody does.

Layering: db + guardrails + contacts.roles + research (web, youtube backends).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from sqlmodel import select

from jobhunter import CONFIG, guardrails
from jobhunter.db import Company, Contact, get_session, utcnow

log = logging.getLogger(__name__)

_C = CONFIG.get("contacts") or {}
MAX_AGE_DAYS = int(_C.get("public_mention_max_age_days", 400))
MAX_PER_COMPANY = int(_C.get("public_mention_max_per_company", 6))

_NAME = r"(?<![\w@])[A-Z][a-z]{1,15}(?:[ ]+[A-Z][a-z'’\-]{1,20}){1,2}(?![\w])"
_ROLE = (r"(?i:co-?founder(?:\s*(?:&|and)\s*(?:CEO|CTO))?|founder(?:\s*(?:&|and)\s*(?:CEO|CTO))?|CEO|CTO|CPO|COO|"
         r"VP(?:\s+of)?\s+Engineering|Head of (?:Engineering|AI|ML|Data|Platform|Product|Talent)|"
         r"(?:Engineering|Tech(?:nical)?) (?:Manager|Lead)|Director of Engineering|"
         r"(?:Senior |Staff |Principal |Lead |Founding )?(?:Software|Backend|Frontend|Full[- ]?Stack|ML|AI|Data|"
         r"Platform|Infrastructure|Research|DevOps|Machine Learning) (?:Engineer|Scientist)|"
         r"(?:Senior |Staff |Principal |Founding )?Engineer|Developer Advocate|Researcher)")
_SEP = r"[ \t]*(?:,|-|–|—|\||:)[ \t]*"


def _patterns(company_rx: str) -> list[re.Pattern]:
    c = company_rx
    return [
        # Sunny Shah - Head of Engineering at Sarvam / Jane Doe, CTO @ Acme
        re.compile(rf"(?P<name>{_NAME}){_SEP}(?:the\s+)?(?P<role>{_ROLE})\s+(?:at|@|of)\s+(?P<co>{c})", re.M),
        # Jane Doe (CTO, Acme) / Jane Doe (Senior Engineer at Acme)
        re.compile(rf"(?P<name>{_NAME})\s*\(\s*(?P<role>{_ROLE})\s*(?:,|at|@)\s*(?P<co>{c})", re.M),
        # Acme CTO Jane Doe / Acme's Head of Engineering, Jane Doe
        re.compile(rf"(?P<co>{c})(?:'s|’s)?[ ]+(?P<role>{_ROLE}),?[ ]+(?P<name>{_NAME})", re.M),
        # with Jane Doe, co-founder and CTO of Acme
        re.compile(rf"(?i:with|featuring|ft\.?)\s+(?P<name>{_NAME}),\s+(?P<role>{_ROLE})(?:\s+and\s+{_ROLE})?\s+(?:at|of|@)\s+(?P<co>{c})", re.M),
        # Pratyush Kumar and Vivek Raghavan of Acme
        re.compile(rf"(?P<name>{_NAME})\s+and\s+(?P<name2>{_NAME})\s+(?:of|from|at)\s+(?P<co>{c})\b", re.M),
    ]


def _company_rx(name: str) -> str:
    base = re.sub(r"\s+(ai|labs?|inc\.?|technologies|tech|hq|io)$", "", name.strip(), flags=re.I) or name
    return rf"(?i:@?{re.escape(base)}(?:\s?(?:AI|Labs?|Inc\.?|Technologies|Tech|HQ))?)"


_YEAR = re.compile(r"\b(20[12]\d)\b")
_ISO = re.compile(r"\b(20[12]\d)[-/](\d{2})[-/](\d{2})\b")
_MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2},?\s+)?(20[12]\d)\b")


def date_of(text: str, *, now: datetime | None = None) -> datetime | None:
    """The newest date the text states (an ISO date, "Mar 4, 2026", or a bare year as
    1 July of it), never in the future. None when it states none."""
    now = now or utcnow()
    found: list[datetime] = []
    for y, m, d in _ISO.findall(text or ""):
        try:
            found.append(datetime(int(y), int(m), int(d)))
        except ValueError:
            pass
    for mon, _day, y in _MONTH.findall(text or ""):
        try:
            found.append(datetime.strptime(f"{mon[:3]} 1 {y}", "%b %d %Y"))
        except ValueError:
            pass
    if not found:
        for y in _YEAR.findall(text or ""):
            found.append(datetime(int(y), 7, 1) if int(y) < now.year else datetime(int(y), 1, 1))
    found = [d for d in found if d <= now + timedelta(days=1)]
    return max(found) if found else None


def mentions(text: str, company: str, domain: str | None = None, *, url: str = "", when: datetime | None = None,
             now: datetime | None = None) -> list[dict]:
    """People the text names at the company: {name, role, quote, url, dated}. Pure."""
    now = now or utcnow()
    when = when or date_of(text, now=now)
    if when is None or now - when > timedelta(days=MAX_AGE_DAYS):
        return []
    out, seen = [], set()
    for rx in _patterns(_company_rx(company)):
        for m in rx.finditer(text or ""):
            quote = re.sub(r"\s+", " ", m.group(0)).strip()
            if guardrails._EX.search(text[max(0, m.start() - 25): m.end()]):
                continue          # "former CTO of Acme", "ex-Acme"
            if not guardrails.names_company(m.group("co"), company, domain):
                continue
            names = [m.group("name")] + ([m.group("name2")] if "name2" in rx.groupindex and m.group("name2") else [])
            for nm in names:
                nm = nm.strip()
                if not guardrails.person_name(nm) or nm.lower() in seen:
                    continue
                if guardrails.names_company(nm, company, domain):
                    continue      # "Acme Labs" read as a person
                seen.add(nm.lower())
                out.append({"name": nm, "role": (m.groupdict().get("role") or "").strip() or None,
                            "quote": quote[:200], "url": url, "dated": when.strftime("%Y-%m-%d")})
    return out


# ------------------------------------------------------------------ the channels

def _youtube(name: str, domain: str | None, limit: int = 5) -> tuple[list[dict], str | None]:
    from jobhunter.research import backends

    if "yt-dlp" not in backends.candidates("youtube"):
        return [], "yt-dlp not installed"
    p = backends.run("yt-dlp", ["--dump-json", "--no-warnings", "--skip-download", "--playlist-end", str(limit),
                                f"ytsearch{limit}:{name} engineer OR CTO OR founder podcast OR talk"], timeout=90)
    if not p.ok and not p.out.strip():
        return [], (p.err or "yt-dlp returned nothing").strip()[:160]
    found = []
    for line in p.out.splitlines():
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        up = d.get("upload_date")
        when = datetime.strptime(up, "%Y%m%d") if up and len(up) == 8 else None
        text = f"{d.get('title') or ''}\n{(d.get('description') or '')[:1500]}"
        url = d.get("webpage_url") or f"https://www.youtube.com/watch?v={d.get('id')}"
        found += mentions(text, name, domain, url=url, when=when)
    return found, None


def _talks(name: str, domain: str | None) -> tuple[list[dict], str | None]:
    from jobhunter.research import web

    if web.search_down():
        return [], "keyless search cooling down"
    q = f'"{name}" (speaker OR talk OR podcast OR meetup OR keynote) (engineer OR CTO OR founder OR "head of")'
    res = web.search(q, limit=10, only=web.KEYLESS)
    found = []
    for r in res.get("results") or []:
        text = f"{r.get('title') or ''}\n{r.get('snippet') or ''}"
        if "linkedin.com/in/" in (r.get("url") or ""):
            continue          # LinkedIn profiles are the linkedin_public channel's
        found += mentions(text + f"\n{r.get('published') or ''}", name, domain, url=r.get("url") or "")
    return found, (res.get("error") if not res.get("results") else None)


_BYLINE = re.compile(rf"(?:^|\n)[ \t]*(?i:by|written by|author:?|posted by)[ \t]+(?P<name>{_NAME})")


def _blog(name: str, website: str | None, domain: str | None) -> tuple[list[dict], str | None]:
    """Bylines on their own blog: the company's site says this person writes for it."""
    from jobhunter.research import web

    if not website:
        return [], None
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    found = []
    for path in ("/blog", "/engineering"):
        page = web.read(base + path, prefer="jina", timeout=40)
        text = page.get("text") or ""
        if not text or guardrails.bot_wall(text):
            continue
        for m in _BYLINE.finditer(text):
            nm = m.group("name").strip()
            window = text[max(0, m.start() - 300): m.end() + 300]
            when = date_of(window)
            if when is None or utcnow() - when > timedelta(days=MAX_AGE_DAYS):
                continue
            if not guardrails.person_name(nm) or guardrails.names_company(nm, name, domain):
                continue
            found.append({"name": nm, "role": None, "quote": f"{name} blog: {re.sub(r'\s+', ' ', m.group(0)).strip()}"[:200],
                          "url": base + path, "dated": when.strftime("%Y-%m-%d"), "own_site": True})
        if found:
            break
    return found, None


# ------------------------------------------------------------------ save

def _save(company_id: int, people: list[dict], channel: str) -> int:
    from jobhunter.contacts import roles

    added = 0
    with get_session() as session:
        c = session.get(Company, company_id)
        have = {(p.name or "").lower() for p in session.exec(select(Contact).where(Contact.company_id == company_id)).all()}
        for p in people[:MAX_PER_COMPANY]:
            if p["name"].lower() in have:
                continue
            evidence = f"named as {p.get('role') or 'part of the team'} at {c.name} on {p['url'][:120]} ({p['dated']}): “{p['quote']}”"
            g = guardrails.vet_contact(session, company=c, name=p["name"], email_addr=None, source="public-page",
                                       channel=f"mention:{channel}", url=p["url"], name_source="page",
                                       public_mention=evidence)
            if g is None or not g.get("name") or g["affiliation"] not in guardrails.READY:
                continue
            v = roles.classify(p.get("role"), name=g["name"])
            session.add(Contact(
                company_id=company_id, name=g["name"], role=p.get("role"), source="public-page", confidence="scraped",
                role_class=v.role_class, seniority=v.seniority, referral_rank=v.referral_rank,
                role_evidence=f"{channel}: {p.get('role') or p['quote'][:80]}", classified_at=utcnow(),
                is_recruiter=v.role_class in ("recruiter", "tech_recruiter"),
                research_notes=f"named at {p['url']}", name_source="page", affiliation=g["affiliation"],
                affiliation_source=g["affiliation_source"], affiliation_evidence=g["affiliation_evidence"],
                affiliation_checked_at=g["affiliation_checked_at"]))
            have.add(p["name"].lower())
            added += 1
        session.commit()
    return added


def find(company_id: int, *, channels: tuple[str, ...] = ("youtube", "talks", "blog")) -> dict:
    """Run the public-mention channels for one company; returns what each added."""
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, domain, website = c.name, c.domain, c.website
    out: dict = {"company": name, "added": 0}
    for ch in channels:
        try:
            if ch == "youtube":
                people, err = _youtube(name, domain)
            elif ch == "talks":
                people, err = _talks(name, domain)
            else:
                people, err = _blog(name, website, domain)
        except Exception as e:  # noqa: BLE001 — one channel failing must not end the company
            people, err = [], f"{type(e).__name__}: {str(e)[:120]}"
        n = _save(company_id, people, ch) if people else 0
        out[ch] = {"found": len(people), "added": n, **({"error": err} if err else {})}
        out["added"] += n
    return out


__all__ = ["find", "mentions", "date_of"]
