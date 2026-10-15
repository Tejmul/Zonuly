"""Referrers named in public WITHOUT a web search — the channels that still work when every
keyless engine returns nothing and LinkedIn guest reads are blocked (2026-10-16).

    yc      the company's own Y Combinator page (ycombinator.com/companies/<slug>, public,
            no login). Its founders list carries `is_active` and each founder's latest YC
            company; a founder is kept only when active AND their latest company is this one.
            The slug comes from the open yc-oss directory, matched on the website's domain —
            never on a name, so a namesake is never read.
    team    the company's own team / about / leadership pages, found from the links on its
            homepage (then the usual paths), read directly and through Jina Reader when the
            direct fetch is a JS shell. A person is a name line followed by a title line with
            a role word — structured Person data (JSON-LD) first. Sections headed advisors /
            investors / board / mentors are skipped, as is any title that places the person
            at another organisation ("Partner at Sequoia", "Former CTO, Flipkart").

Evidence, never invention: a team-page person is `verified-current` / team-page (named on
their own site, guardrails.affiliation); a YC founder is `likely-current` / public-mention
with the YC page and the founder's title as the evidence. Nothing here guesses an address —
the learned company format (contacts/addresses.py) does that later, labelled as a guess.

Every request has a hard timeout and the whole channel a per-company time cap, so the
referral job cannot stall on a hung site.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from sqlmodel import select

from jobhunter import CONFIG, admission, guardrails
from jobhunter.db import Company, Contact, get_session, utcnow

log = logging.getLogger(__name__)

_C = CONFIG.get("contacts") or {}
#: seconds one company may spend in the team-page channel, all pages together
TEAM_BUDGET_S = int(_C.get("team_people_budget_s", 75))
TEAM_MAX_PAGES = int(_C.get("team_people_max_pages", 3))
MAX_PEOPLE = int(_C.get("team_people_max_per_company", 15))
YC_DIRECTORY = "https://yc-oss.github.io/api/companies/all.json"
YC_TTL_H = 24 * 7
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_MAX_BYTES = 3_000_000

# ------------------------------------------------------------------ fetching


def _get(url: str, *, timeout: httpx.Timeout | float = _TIMEOUT, max_bytes: int = _MAX_BYTES) -> tuple[int, str]:
    """(status, body) — a direct GET with a hard timeout and a size cap; (0, '') on failure."""
    try:
        with httpx.Client(headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                                   "Accept-Language": "en-US,en;q=0.9"},
                          timeout=timeout, follow_redirects=True) as cl:
            with cl.stream("GET", url) as r:
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        break
                body = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
                return r.status_code, body
    except Exception as e:  # noqa: BLE001 — a dead site is an answer, not a crash
        log.debug("team_people: GET %s failed: %s", url, e)
        return 0, ""


def _jina(url: str, timeout: float = 30.0) -> str:
    """The page rendered to Markdown by Jina Reader (keyless), or ''."""
    from jobhunter.research import web

    try:
        _, text = web._read_jina(url, int(timeout))
    except Exception as e:  # noqa: BLE001
        log.debug("team_people: jina %s failed: %s", url, e)
        return ""
    return text or ""


# ------------------------------------------------------------------ text

_BLOCK = re.compile(r"</?(?:p|div|li|ul|ol|br|tr|td|th|section|article|header|footer|figure|figcaption|"
                    r"dt|dd|blockquote|table|span|a|strong|b|em|small)\b[^>]*>", re.I)
_HEAD = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)


def html_lines(raw: str) -> list[str]:
    """The page as text lines, block by block; headings prefixed '# ' like Jina's Markdown."""
    t = re.sub(r"<(script|style|noscript|svg|template)\b.*?</\1>", " ", raw or "", flags=re.I | re.S)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = _HEAD.sub(lambda m: "\n# " + re.sub(r"<[^>]+>", " ", m.group(2)) + "\n", t)
    t = _BLOCK.sub("\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return [re.sub(r"\s+", " ", ln).strip() for ln in t.splitlines() if ln.strip()]


def md_lines(md: str) -> list[str]:
    """Jina Markdown as plain lines: links and images unwrapped, emphasis dropped."""
    out = []
    for ln in (md or "").splitlines():
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", ln)
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
        head = s.lstrip().startswith("#")
        s = re.sub(r"[*_`>|]+", " ", s).strip(" #-•\t")
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            out.append(("# " + s) if head else s)
    return out


# ------------------------------------------------------------------ extraction

#: a title line: one of the words a person's role carries
ROLE = re.compile(
    r"\b(co[- ]?founder|founder|ceo|cto|coo|cfo|cpo|cmo|cso|chief|president|vp|vice president|head|"
    r"director|manager|lead|engineer|engineering|developer|scientist|architect|designer|"
    r"product|recruiter|talent|hr|people|operations|marketing|sales|growth|analyst|researcher|"
    r"partner|principal|associate|intern|sde|swe|devops|sre|consultant|specialist|executive|"
    r"officer|owner|managing)\b", re.I)
#: sections and titles of people who do not work there
_NOT_STAFF = re.compile(r"\b(advis[oe]rs?|investors?|backers?|backed|board|mentors?|angels?|"
                        r"customers?|clients?|testimonials?|partners? at|portfolio|alumni|"
                        r"what (?:people|our|they)|said|says)\b", re.I)
_TITLE_NOT = re.compile(r"\b(advisor|adviser|investor|mentor|angel|board member|board of|"
                        r"customer|client|guest|speaker|alumn)", re.I)
#: on the board, not on the team
_BOARD = re.compile(r"\b((?:independent|non[- ]?executive|nominee|additional)\s+director|chair(?:man|person|woman)|"
                    r"board)\b", re.I)
_SPLIT = re.compile(r"\s*(?:,|\bat\b|@|\||–|—|\s-\s|\bfor\b)\s*", re.I)
_SENTENCE = re.compile(r"[.!?]\s+[A-Z]|\b(?:is|are|was|we|our|you|your|with|and has|has been)\b", re.I)


def title_ok(title: str, company: str, domain: str | None) -> bool:
    """A role, short, not a sentence, not an advisor / investor, and not placing them at
    another organisation: every part after a comma / 'at' / '@' is a role word or this
    company's name."""
    t = (title or "").strip()
    if not t or len(t) > 90 or not ROLE.search(t) or "@" in t or "http" in t.lower() or t.endswith(","):
        return False
    if re.search(r"founder.?s office|opening|benefit|apply|hiring|vacanc|career", t, re.I):
        return False
    if _BOARD.search(t) and not re.search(r"\b(founder|ceo|cto|managing director)\b", t, re.I):
        return False
    if _TITLE_NOT.search(t) or guardrails._EX.search(t) or _SENTENCE.search(t):
        return False
    parts = [p for p in _SPLIT.split(t) if p and p.strip()]
    for p in parts[1:]:
        p = p.strip(" .&")
        if not p or ROLE.search(p) or guardrails.names_company(p, company, domain):
            continue
        if re.fullmatch(r"(?:&|and|of|the|india|bengaluru|bangalore|hyderabad|remote)(?:\s+\w+)?", p, re.I):
            continue
        return False
    return True


#: words that sit in navigation, buttons and headings — never in a person's name
_NOT_NAME_WORD = set("""
about access account all and app apply author blog book by calculator call careers case cases center
change click company contact cookie cookies copyright customer customers demo disclosure discover docs
download enterprise events explore faq features follow for free generation get guide help home how in
industries insights integrations join know latest leader leaders learn legal less login logout main
media meet menu mission more new news now of on open our overview partner partners platform policy
pipeline press pricing privacy product products quiz read register report request resources responsible
sales see service services settings show sign solution solutions start started story subscribe support
team terms the to tools trust try us use value values view vision watch we webinar what who why with
work your world
award awards backed benefits business capital card career credit culture current fast featured finance
flexible gold gradation growth health hiring hours hybrid india interest job jobs karnataka bengaluru
bangalore hyderabad mumbai delhi pune chennai life loan loans location money office opening openings
perks position positions remote role roles salary investor investors funding insurance payments
""".split())
_HONORIFIC = re.compile(r"^(?:mr|mrs|ms|miss|dr|prof|shri|smt)\.?\s+|,?\s+(?:ph\.?d|md|mba)\.?$", re.I)


def _name(line: str, company: str, domain: str | None) -> str | None:
    s = line.strip()
    if s.startswith("#"):
        s = s.lstrip("# ").strip()
    for _ in range(2):
        s = _HONORIFIC.sub("", s).strip()
    if {w.lower().strip(".,") for w in s.split()} & _NOT_NAME_WORD:
        return None
    if not s or len(s) > 40 or len(s.split()) > 4 or ROLE.search(s):
        return None
    if not re.match(r"^[A-Z]", s) or guardrails.names_company(s, company, domain):
        return None
    chk = guardrails.person_name(s)
    if not chk or chk.confidence == "low":
        return None
    words = chk.value.split()
    if any(len(w.strip(".")) < 2 and not w.endswith(".") for w in words):
        return None
    return chk.value


def people_from_lines(lines: list[str], company: str, domain: str | None = None) -> list[dict]:
    """{name, title} for every 'Name' line followed (within two lines) by a title line, or
    a single 'Name, Title' / 'Name - Title' line — outside advisor / investor / customer
    sections. Pure, so the tests call it."""
    out: list[dict] = []
    seen: set[str] = set()
    skip_section = False
    n = len(lines)
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            head = ln.lstrip("# ")
            if _NOT_STAFF.search(head):
                skip_section = True
                continue
            if ROLE.search(head) and not _name(ln, company, domain) or re.search(
                    r"\b(team|people|leadership|founders?|management|meet|about|who we are|crew)\b", head, re.I):
                skip_section = False
        if skip_section:
            continue
        name = _name(ln, company, domain)
        title = None
        if name:
            for j in (i + 1, i + 2):
                if j < n and title_ok(lines[j].lstrip("# "), company, domain):
                    title = lines[j].lstrip("# ").strip()
                    break
                if j < n and _name(lines[j], company, domain):
                    break           # the next line is another name: this one has no title
        else:
            m = re.match(r"^([A-Z][\w.'’\- ]{3,40}?)\s*(?:,|\s[-–|]\s)\s*(.{2,90})$", ln)
            if m and not ln.startswith("#"):
                name = _name(m.group(1), company, domain)
                title = m.group(2).strip() if name and title_ok(m.group(2), company, domain) else None
        if not name or not title or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "title": title[:120]})
    return out


def people_from_jsonld(raw: str, company: str, domain: str | None = None) -> list[dict]:
    """schema.org Person entries (name + jobTitle) in the page's JSON-LD."""
    out: list[dict] = []
    for blob in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", raw or "", re.I | re.S):
        try:
            data = json.loads(blob.strip())
        except Exception:  # noqa: BLE001
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
                if str(d.get("@type", "")).lower() == "person" and d.get("name") and d.get("jobTitle"):
                    nm = _name(str(d["name"]), company, domain)
                    t = str(d["jobTitle"])
                    if nm and title_ok(t, company, domain):
                        out.append({"name": nm, "title": t[:120]})
    return out


# ------------------------------------------------------------------ finding the pages

_TEAM_LINK = re.compile(r"\b(team|leadership|people|founders?|management|our[- ]story|who[- ]we[- ]are|about)\b", re.I)
_NOT_TEAM_LINK = re.compile(r"(career|job|blog|news|press|event|webinar|case[- ]stud|privacy|terms|login|signup|"
                            r"customer|partner|investor|\.pdf|#)", re.I)
DEFAULT_PATHS = ("/team", "/about", "/about-us", "/leadership", "/our-team", "/people", "/company")


def team_links(home_html: str, base: str) -> list[str]:
    """Same-site links on the homepage that look like a team / about page, best first."""
    host = (urlparse(base).hostname or "").removeprefix("www.")
    found: dict[str, int] = {}
    for href, text in re.findall(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", home_html or "", re.I | re.S):
        label = re.sub(r"<[^>]+>", " ", text)
        url = urljoin(base + "/", href.strip())
        u = urlparse(url)
        if u.scheme not in ("http", "https") or (u.hostname or "").removeprefix("www.") != host:
            continue
        path = u.path.rstrip("/").lower()
        if not path or _NOT_TEAM_LINK.search(path):
            continue
        hay = f"{path} {label}"
        if not _TEAM_LINK.search(hay):
            continue
        rank = 0 if re.search(r"team|leadership|people|founder|management", hay, re.I) else 1
        clean = f"{u.scheme}://{u.hostname}{u.path.rstrip('/')}"
        found[clean] = min(rank, found.get(clean, 9))
    return sorted(found, key=lambda k: (found[k], len(k)))


def _base(website: str) -> str:
    w = website.strip().split("?")[0].split("#")[0].rstrip("/")
    if not w.startswith("http"):
        w = "https://" + w
    u = urlparse(w)
    return f"{u.scheme}://{u.hostname}"


def read_team(website: str, company: str, domain: str | None = None, *,
              budget_s: int = TEAM_BUDGET_S, max_pages: int = TEAM_MAX_PAGES) -> dict:
    """{people: [{name, title, url}], pages: [...], requests: n} from the company's own site."""
    t0 = time.time()
    base = _base(website)
    out: dict = {"people": [], "pages": [], "requests": 0}
    status, home = _get(base)
    out["requests"] += 1
    if status >= 400 or not home:
        out["error"] = f"homepage HTTP {status or 'failed'}"
        return out
    final_host = re.search(r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"'](https?://[^/\"']+)", home, re.I)
    if final_host:
        b2 = _base(final_host.group(1))
        if (urlparse(b2).hostname or "").removeprefix("www.") == (urlparse(base).hostname or "").removeprefix("www."):
            base = b2
    candidates = team_links(home, base)
    candidates += [base + p for p in DEFAULT_PATHS if base + p not in candidates]
    seen: dict[str, dict] = {}
    read = 0
    for url in candidates:
        if read >= max_pages or time.time() - t0 > budget_s or len(candidates) == 0:
            break
        status, raw = _get(url)
        out["requests"] += 1
        if status >= 400 or not raw:
            continue
        read += 1
        found = people_from_jsonld(raw, company, domain) + people_from_lines(html_lines(raw), company, domain)
        if len(found) < 2 and time.time() - t0 < budget_s - 30:
            # a JS-rendered page: the direct fetch saw a shell — read it rendered
            md = _jina(url, timeout=min(30, max(5, budget_s - (time.time() - t0))))
            out["requests"] += 1
            if md:
                found += people_from_lines(md_lines(md), company, domain)
        hit = 0
        for p in found:
            if p["name"].lower() not in seen:
                seen[p["name"].lower()] = {**p, "url": url}
                hit += 1
        out["pages"].append({"url": url, "people": hit})
        if len(seen) >= MAX_PEOPLE:
            break
    out["people"] = list(seen.values())[:MAX_PEOPLE]
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ Y Combinator


def _reg(url_or_host: str | None) -> str | None:
    h = admission.host_of(url_or_host or "") or (url_or_host or "").lower().removeprefix("www.")
    return (admission.registrable(h) or h or None) if h else None


_yc_map: dict | None = None


def yc_directory() -> dict[str, str]:
    """registrable domain -> YC slug, from the open yc-oss mirror (cached a week)."""
    global _yc_map
    if _yc_map is not None:
        return _yc_map
    from jobhunter.research import cache

    hit = cache.get("yc-directory", "domains", ttl_hours=YC_TTL_H)
    if hit and hit.get("map"):
        _yc_map = hit["map"]
        return _yc_map
    status, body = _get(YC_DIRECTORY, timeout=httpx.Timeout(60.0, connect=10.0), max_bytes=40_000_000)
    m: dict[str, str] = {}
    if status == 200 and body:
        try:
            for c in json.loads(body):
                d = _reg(c.get("website"))
                if d and c.get("slug") and (c.get("status") or "Active") != "Inactive":
                    m.setdefault(d, c["slug"])
        except Exception as e:  # noqa: BLE001
            log.warning("yc directory unreadable: %s", e)
    if m:
        cache.put("yc-directory", "domains", {"map": m}, backend="yc-oss")
    _yc_map = m
    return m


def yc_slug(website: str | None, domain: str | None) -> str | None:
    for d in (_reg(website), _reg(domain)):
        if d and d not in ("ycombinator.com", "github.com", "linkedin.com"):
            s = yc_directory().get(d)
            if s:
                return s
    return None


def yc_founders(slug: str, company: str) -> dict:
    """{people: [{name, title, linkedin, url}], status} from ycombinator.com/companies/<slug>."""
    url = f"https://www.ycombinator.com/companies/{slug}"
    status, raw = _get(url)
    if status != 200 or not raw:
        return {"people": [], "error": f"HTTP {status or 'failed'}", "url": url}
    m = re.search(r'data-page="([^"]+)"', raw)
    if not m:
        return {"people": [], "error": "no page data", "url": url}
    try:
        co = json.loads(_html.unescape(m.group(1)))["props"]["company"]
    except Exception:  # noqa: BLE001
        return {"people": [], "error": "page data unreadable", "url": url}
    return {"people": founders_from_yc(co, slug), "status": co.get("ycdc_status"), "url": url,
            "website": co.get("website")}


def founders_from_yc(co: dict, slug: str) -> list[dict]:
    """Active founders whose latest YC company is this one. Pure, so the tests call it."""
    if (co.get("ycdc_status") or "Active") not in ("Active", "Public"):
        return []
    out = []
    for f in co.get("founders") or []:
        latest = ((f.get("latest_yc_company") or {}).get("href") or "").rstrip("/").rsplit("/", 1)[-1]
        if not f.get("is_active") or not f.get("full_name") or (latest and latest != slug):
            continue
        out.append({"name": f["full_name"].strip(), "title": (f.get("title") or "Founder").strip()[:120],
                    "linkedin": (f.get("linkedin_url") or "").split("?")[0] or None})
    return out


# ------------------------------------------------------------------ saving


def _save(cid: int, people: list[dict], *, kind: str) -> dict:
    """Store new people (or confirm an unconfirmed row of the same name) through the guardrails."""
    from jobhunter.contacts import roles

    added = confirmed = 0
    with get_session() as session:
        c = session.get(Company, cid)
        if c is None:
            return {"added": 0, "confirmed": 0}
        rows = session.exec(select(Contact).where(Contact.company_id == cid)).all()
        by_name = {(k.name or "").lower(): k for k in rows if k.name}
        by_li = {(k.linkedin or "").rstrip("/").lower(): k for k in rows if k.linkedin}
        for p in people:
            url = p["url"]
            if kind == "yc":
                mention = f"{p['name']}, {p['title']} of {c.name} — an active founder on {c.name}'s YC page {url}"
                g = guardrails.vet_contact(session, company=c, name=p["name"], email_addr=None, source="yc-page",
                                           channel="yc", url=url, name_source="page", public_mention=mention)
            else:
                g = guardrails.vet_contact(session, company=c, name=p["name"], email_addr=None, source="site",
                                           channel="team-page", url=url, name_source="page", team_page=url)
            if g is None or not g.get("name") or g["affiliation"] not in guardrails.READY:
                continue
            old = by_name.get(g["name"].lower()) or (by_li.get(p["linkedin"].rstrip("/").lower())
                                                     if p.get("linkedin") else None)
            if old is not None:
                if old.affiliation in guardrails.READY:
                    continue
                if old.affiliation == "generic":
                    continue
                old.affiliation, old.affiliation_source = g["affiliation"], g["affiliation_source"]
                old.affiliation_evidence, old.affiliation_checked_at = g["affiliation_evidence"], utcnow()
                old.role = old.role or p["title"]
                old.linkedin = old.linkedin or p.get("linkedin")
                session.add(old)
                confirmed += 1
                continue
            v = roles.classify(p["title"], name=g["name"])
            row = Contact(
                company_id=cid, name=g["name"], role=p["title"], linkedin=p.get("linkedin"),
                source="yc-page" if kind == "yc" else "site", confidence="scraped",
                role_class=v.role_class, seniority=v.seniority, referral_rank=v.referral_rank,
                role_evidence=f"{'YC page' if kind == 'yc' else 'team page'}: {p['title']}"[:300],
                is_recruiter=v.role_class in ("recruiter", "tech_recruiter"), classified_at=utcnow(),
                research_notes=f"named at {url}" if kind == "team" else f"active founder on {url}",
                name_source="page", affiliation=g["affiliation"], affiliation_source=g["affiliation_source"],
                affiliation_evidence=g["affiliation_evidence"], affiliation_checked_at=g["affiliation_checked_at"])
            session.add(row)
            by_name[g["name"].lower()] = row
            added += 1
        c.contacts_found_at = utcnow()
        session.add(c)
        session.commit()
    return {"added": added, "confirmed": confirmed}


def find_yc(cid: int) -> dict:
    """The company's active YC founders, when its domain is in the YC directory."""
    with get_session() as session:
        c = session.get(Company, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        name, website, domain = c.name, c.website, c.domain
    slug = yc_slug(website, domain)
    if not slug:
        return {"company": name, "kind": "yc", "skipped": "not in the YC directory", "requests": 0}
    res = yc_founders(slug, name)
    people = [{**p, "url": res["url"]} for p in res.get("people") or []]
    saved = _save(cid, people, kind="yc") if people else {"added": 0, "confirmed": 0}
    return {"company": name, "kind": "yc", "slug": slug, "found": len(people), "requests": 1,
            **saved, **({"error": res["error"]} if res.get("error") else {})}


def find_team(cid: int, *, budget_s: int = TEAM_BUDGET_S) -> dict:
    """People named with a title on the company's own team / about pages."""
    with get_session() as session:
        c = session.get(Company, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        name, website, domain = c.name, c.website or (f"https://{c.domain}" if c.domain else None), c.domain
    if not website:
        return {"company": name, "kind": "team", "skipped": "no website", "requests": 0}
    res = read_team(website, name, domain, budget_s=budget_s)
    saved = _save(cid, res["people"], kind="team") if res["people"] else {"added": 0, "confirmed": 0}
    return {"company": name, "kind": "team", "found": len(res["people"]), "requests": res["requests"],
            "pages": res["pages"], "seconds": res.get("seconds"), **saved,
            **({"error": res["error"]} if res.get("error") else {})}


__all__ = ["find_yc", "find_team", "people_from_lines", "people_from_jsonld", "founders_from_yc", "title_ok",
           "team_links", "html_lines", "md_lines", "read_team", "yc_slug"]
