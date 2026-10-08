"""Any real address at the company, from the open web.

The free waterfall (GitHub commits → their site → pattern → Hunter) leaves a company
with names and no addresses whenever it has no public code and its site hides its
people. Tejmul, 2026-09-19: "if you don't find the company emails … find the HR email
and the founders' emails, or any other engineers who work there."

So: search the web for anything "@their-domain" — the LinkedIn company card, a
press contact, a founder's address on a conference page, careers@ on a job post —
read the handful of pages that carry one, and keep every same-domain address with
whatever name sits next to it. Two real named addresses that agree (or one + their own
page stating the format) confirm the company's pattern (contacts/addresses.py), and the
pattern is then applied to every named person we already hold (the LinkedIn
profiles), which is where the referrers come from. Nothing is invented: an address is
stored only if a page showed it, with the URL it was read from.

Search is the research layer's web search (Exa MCP first — the keyless Agent Reach
path — then the keyed API); pages are read through Jina / direct fetch. Cached 24h.
"""

from __future__ import annotations

import logging
import re

from sqlmodel import col, select

from jobhunter import CONFIG, guardrails
from jobhunter.contacts import patterns, roles
from jobhunter.db import Company, Contact, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_C = CONFIG.get("contacts") or {}
MAX_PAGES = int(_C.get("web_emails_max_pages", 6))
MAX_ADDRESSES = int(_C.get("web_emails_max_addresses", 12))

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_ASSET = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?|ico)$", re.I)
_NOISE = re.compile(r"^(noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster|abuse|webmaster|"
                    r"privacy|legal|dpo|grievance|security|billing|invoice|unsubscribe|bounce)@", re.I)
# a shared inbox that reaches no engineer but does reach the company — kept once, ranked last
_GENERIC = re.compile(r"^(info|hello|hi|hey|contact|contactus|support|sales|team|press|media|marketing|"
                      r"partnerships?|business|admin|office|help|enquiry|enquiries|inquiry|inquiries)@", re.I)
_NAME = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,20})\b")
_NOT_NAMES = {"the", "our", "we", "contact", "email", "get", "all", "read", "learn", "for", "at", "and",
              "chief", "head", "senior", "software", "lead", "co", "founder", "ceo", "cto", "india", "private"}

# what to ask; {name} and {domain} are filled in
QUERIES = [
    '"@{domain}" email',
    "{name} founder CEO CTO contact email",
    "{name} careers HR recruiter email",
    '{name} engineer "@{domain}"',
]
# the pages on their own site most likely to carry a named address
OWN_PATHS = ["/contact", "/contact-us", "/about", "/about-us", "/team", "/careers", "/press", "/privacy-policy", "/terms"]


def _host(url: str | None) -> str:
    return (url or "").replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.").lower()


def _same_domain(email: str, domain: str) -> bool:
    return email.split("@")[-1].lower() == domain.lower()


def _name_from_local(local: str) -> str | None:
    """rahul.sharma / rahul_sharma → Rahul Sharma. A single token is not a name."""
    parts = [p for p in re.split(r"[._\-]", local) if p.isalpha() and len(p) > 1]
    if len(parts) != 2 or any(len(p) > 15 for p in parts):
        return None
    return " ".join(p.capitalize() for p in parts)


def _name_near(text: str, email: str, window: int = 220) -> str | None:
    idx = text.find(email)
    if idx == -1:
        return None
    chunk = text[max(0, idx - window): idx]
    hits = _NAME.findall(chunk)
    for first, last in reversed(hits):
        if first.lower() not in _NOT_NAMES and last.lower() not in _NOT_NAMES:
            return f"{first} {last}"
    return None


def _person_name(text: str, raw: str, email: str) -> str | None:
    """A name for the address only when the address is a person's and the name agrees
    with it: "Rajeev Ranjan" next to hiring@ is the poster, not the mailbox, and
    "Tomales Bay" next to publicrelations@ is an investor in the same paragraph."""
    local = email.split("@")[0]
    if local in roles._ALIAS_ROLE or _GENERIC.match(email):
        return None
    for cand in (_name_near(text, raw), _name_from_local(local)):
        parsed = patterns.normalize_name(cand)
        if parsed and (parsed[0] in local or parsed[1] in local):
            return cand
    return None


def _harvest(text: str, domain: str, where: str, found: dict[str, dict]) -> None:
    for raw in set(_EMAIL.findall(text or "")):
        email = raw.lower().strip(".")
        if _ASSET.search(email) or _NOISE.match(email) or not _same_domain(email, domain):
            continue
        entry = found.setdefault(email, {"where": where, "name": None})
        entry["name"] = entry["name"] or _person_name(text, raw, email)


def find(company_id: int, *, max_pages: int = MAX_PAGES, fresh: bool = False) -> dict:
    """Search, read, harvest, attach, learn. Returns what was found and where."""
    from jobhunter.research import web

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, domain, website, pattern = c.name, c.domain, c.website, c.email_pattern
        rows = session.exec(select(Contact).where(Contact.company_id == company_id)).all()
        existing = {(p.email or "").lower() for p in rows if p.email}
        nameless = [(p.id, p.name) for p in rows if p.name and not p.email]
    if not domain:
        return {"company": name, "error": "no domain — nothing to match addresses against"}

    found: dict[str, dict] = {}
    to_read: list[str] = []
    seen_urls: set[str] = set()

    def queue(url: str) -> None:
        url = (url or "").split("#")[0]
        if url and url not in seen_urls:
            seen_urls.add(url)
            to_read.append(url)

    # 1. their own pages first — cheap, and the address there is theirs by definition
    if website:
        base = website.rstrip("/")
        for path in OWN_PATHS:
            queue(base + path)

    # 2. what the web says: addresses straight from the snippets, pages worth reading after
    searched = 0
    for q in QUERIES:
        res = web.search(q.format(name=name, domain=domain), limit=8, fresh=fresh, only=web.KEYLESS)
        if res.get("error") and not res.get("results"):
            log.info("web_emails: search failed for %s: %s", name, res["error"][:120])
            continue
        searched += 1
        for r in res.get("results") or []:
            snippet = f"{r.get('title') or ''}\n{r.get('snippet') or ''}"
            _harvest(snippet, domain, r.get("url") or "search", found)
            url = r.get("url") or ""
            if f"@{domain}" in snippet.lower() or _host(url) == domain or "linkedin.com/company/" in url:
                queue(url)

    # 3. read the pages most likely to carry an address, own site first
    own = [u for u in to_read if _host(u) == domain]
    other = [u for u in to_read if _host(u) != domain]
    read = 0
    for url in (own + other)[: max_pages + len(OWN_PATHS)]:
        if read >= max_pages and _host(url) != domain:
            break
        page = web.read(url, max_chars=40_000, fresh=fresh, prefer="direct" if _host(url) == domain else None)
        text = page.get("text") or ""
        if not text:
            continue
        read += 1
        _harvest(text, domain, url, found)
        if len(found) >= MAX_ADDRESSES:
            break

    # 4. persist: attach to a named person we already hold, else a new row; one generic inbox at most
    stats = {"company": name, "searched": searched, "pages_read": read, "addresses": len(found),
             "attached": 0, "people": 0, "inbox": 0, "pattern": pattern, "found": []}
    generic_kept = False
    with get_session() as session:
        for email, info in sorted(found.items(), key=lambda kv: (bool(_GENERIC.match(kv[0])), kv[0])):
            if email in existing:
                continue
            local = email.split("@")[0]
            stats["found"].append({"email": email, "name": info["name"], "where": info["where"]})
            generic = bool(_GENERIC.match(email))
            if generic:
                if generic_kept:
                    continue
                generic_kept = True

            # a person we already know by name? then this is their address
            matched_id = None
            if info["name"]:
                want = patterns.normalize_name(info["name"])
                for cid, cname in nameless:
                    if want and patterns.normalize_name(cname) == want:
                        matched_id = cid
                        break
            if matched_id is None:
                for cid, cname in nameless:
                    if email in patterns.candidates(cname, domain, None, limit=8):
                        matched_id = cid
                        break
            if matched_id is not None:
                p = session.get(Contact, matched_id)
                if p and not p.email:
                    p.email = email
                    p.confidence = "scraped"
                    p.email_kind = "company"
                    p.research_notes = f"{p.research_notes or ''}\naddress published at {info['where']}".strip()
                    if p.affiliation not in guardrails.READY:
                        # their company address, published: a reason to think they are there
                        on_site = _host(info["where"]) == domain
                        aff = guardrails.affiliation(
                            company_name=name, domain=domain, email_kind="company", email_addr=email,
                            team_page=info["where"] if on_site else None, published_at=info["where"],
                            linkedin_headline=p.role if p.source == "linkedin-public" else None)
                        p.affiliation, p.affiliation_source, p.affiliation_evidence = aff.label, aff.source, aff.evidence[:300]
                        p.affiliation_checked_at = utcnow()
                    session.add(p)
                    nameless = [(i, n) for i, n in nameless if i != matched_id]
                    stats["attached"] += 1
                    existing.add(email)
                    continue

            company_row = session.get(Company, company_id)
            on_site = _host(info["where"]) == domain
            g = guardrails.vet_contact(session, company=company_row, name=info["name"], email_addr=email,
                                       source="web", channel="web", url=info["where"],
                                       name_source="page" if info["name"] else None,
                                       team_page=info["where"] if (on_site and info["name"]) else None,
                                       published_at=info["where"])
            if g is None or not g.get("email"):
                continue
            verdict = roles.classify(None, name=g.get("name"), email=email)
            if generic:
                verdict = roles.RoleVerdict("other", None, 9, f"shared inbox {local}@", "alias")
            role = "General inbox" if generic else ("Recruiting / HR" if verdict.role_class in ("recruiter", "tech_recruiter") else None)
            session.add(Contact(
                company_id=company_id, name=g.get("name"), role=role, email=g["email"],
                source="web", confidence=g["confidence"],
                is_recruiter=verdict.role_class in ("recruiter", "tech_recruiter"),
                role_class=verdict.role_class, seniority=verdict.seniority,
                referral_rank=verdict.referral_rank, role_evidence=verdict.evidence or f"address: {local}@",
                classified_at=utcnow(),
                research_notes=f"address published at {info['where']}",
                name_source=g.get("name_source"), email_kind=g.get("email_kind"), affiliation=g["affiliation"],
                affiliation_source=g["affiliation_source"], affiliation_evidence=g["affiliation_evidence"],
                affiliation_checked_at=g["affiliation_checked_at"],
            ))
            existing.add(email)
            stats["inbox" if generic else "people"] += 1

        session.commit()
    # 5. learn the format from every named address now on record — confirmed only with
    # ≥2 agreeing (or one + their own page) — and address the ready referrers by it
    from jobhunter.contacts import addresses

    try:
        res = addresses.fill(company_id)
        stats["pattern"] = res.get("pattern")
        stats["by_pattern"] = res.get("added", 0)
    except Exception as e:  # noqa: BLE001
        log.warning("web_emails: pattern fill for %s failed: %s", name, e)

    log.info("web_emails: %s -> %d addresses (%d attached, %d people, %d inbox) pattern=%s",
             name, len(found), stats["attached"], stats["people"], stats["inbox"], stats["pattern"])
    return stats
