"""The careers pass — find any company's careers page, and the open roles on it.

The ATS probe guesses Greenhouse / Lever / Ashby slugs from a name; on the ★ India list
that found a board for 6 of 53 companies. The rest were hiring all along, on a careers
page their homepage links to, on Keka or Darwinbox or Zoho Recruit, or in a Notion doc.
So, per company, in cost order:

  1. the board on record (ats + slug), when its provider has public JSON
  2. their homepage — a direct fetch for the raw HTML (links, iframes, embed scripts),
     the research page reader (Jina) when that is blocked or a JS shell
  3. the links on it that say careers / jobs / join us, then the common paths
     (/careers, /jobs, /join, /company/careers …), then a keyless web search
     ("<name> careers", hosted Exa MCP only — never the metered API)
  4. on the careers page: a hosted board it links to or redirects to → that board's
     JSON; else the JobPostings it declares in JSON-LD; else its text, read by the
     `cheap` model in JSON mode — and a title is kept only if it is literally on the page

Roles go through pipeline.persist — the same title, India-city (Bengaluru / Hyderabad /
remote only), seniority, internship and remote-anywhere gates and the same dedup as
every other source. A careers page that lists roles is the company saying it is hiring in
its own words, so it also sets hiring_status=verified, with the titles as evidence.

Failures leave a backoff marker (step "careers"): a network error is retryable (1, 3, 7…
days); a reachable site with no careers page anywhere waits the longest delay.

Layering: pipeline layer — scrapers, research, llm, pipeline and db below; harvest, tick,
api and the CLI above.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import concurrent.futures as cf
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlmodel import col, select

from jobhunter import CONFIG, backoff, pipeline
from jobhunter.db import Company, fill_order, get_session, init_db, utcnow
from jobhunter.research import web
from jobhunter.scrapers import careers_ats as ca
from jobhunter.scrapers.base import HEADERS, RawJob, html_to_text, looks_remote, make_client

log = logging.getLogger(__name__)

_HV = ((CONFIG.get("targeting") or {}).get("hiring_verification") or {})
PATHS: list[str] = list(dict.fromkeys(
    (_HV.get("careers_paths") or ["/careers", "/jobs", "/join-us"]) + ["/join", "/company/careers", "/careers.html"]))
TIMEOUT = 15
STEP = "careers"
MARK = "[careers] read"
REFRESH_DAYS = 14
MAX_CANDIDATES = 6

_HIRING_PAGE = re.compile(r"\b(careers?|open (?:roles|positions)|job openings|we(?:'re| are) hiring|join (?:our|the) team|"
                          r"current openings|apply now|view (?:all )?(?:jobs|openings|roles)|work with us)\b", re.I)
_SOFT_404 = re.compile(r"\b(page not found|404|doesn.t exist|could not be found|no longer available)\b", re.I)
_ROLE_WORD = re.compile(r"\b(engineer|developer|scientist|intern|sde|analyst|manager|designer|architect|lead)\b", re.I)


@dataclass
class Find:
    careers_url: str | None = None
    ats: str | None = None
    slug: str | None = None
    via: str = ""                         # how the page was found: board | link | path | search
    html: str = ""
    final_url: str | None = None
    checked: list[str] = field(default_factory=list)
    errors: int = 0                       # network failures — "could not tell", retryable
    reached: bool = False                 # did anything on their site answer at all


# ------------------------------------------------------------------ fetching

def _fetch(http: httpx.Client, url: str) -> tuple[str, str | None, str]:
    """("ok" | "missing" | "error", final url, html)."""
    try:
        r = http.get(url)
    except Exception as e:  # noqa: BLE001 — network failure: retryable, says nothing about them
        log.debug("careers fetch %s failed: %s", url, e)
        return "error", None, ""
    if r.status_code == 429 or r.status_code >= 500:
        return "error", str(r.url), ""
    if r.status_code >= 400:
        return "missing", str(r.url), ""
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return "missing", str(r.url), ""
    return "ok", str(r.url), r.text


def _site(website: str | None) -> str | None:
    if not website:
        return None
    p = urlparse(website if "://" in website else "https://" + website)
    return f"{p.scheme or 'https'}://{p.hostname}" if p.hostname else None


def _stem(name: str) -> str:
    return re.sub(r"(ai|labs?|hq|inc|tech|technologies|app|india)$", "", re.sub(r"[^a-z0-9]", "", name.lower())) or \
        re.sub(r"[^a-z0-9]", "", name.lower())


def _slug_fits(name: str, slug: str) -> bool:
    """A board found by search is theirs only if its slug carries their name."""
    s = re.sub(r"[^a-z0-9]", "", slug.lower())
    stem = _stem(name)[:6]
    return bool(stem) and (stem in s or s in re.sub(r"[^a-z0-9]", "", name.lower()))


def _same_site(url: str, site: str) -> bool:
    a = (urlparse(url).hostname or "").lower().removeprefix("www.")
    b = (urlparse(site).hostname or "").lower().removeprefix("www.")
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def _looks_careers(final_url: str | None, requested: str, html: str, site: str) -> bool:
    """A 200 is not a careers page: an unknown path often redirects home or renders a 404 shell."""
    text = html_to_text(html, limit=20000)
    if not text or _SOFT_404.search(text[:600]):
        return False
    if final_url and _same_site(final_url, site) and (urlparse(final_url).path or "/").strip("/") == "" \
            and (urlparse(requested).path or "/").strip("/"):
        return False   # redirected to the homepage
    if final_url and ca.detect_ats(final_url):
        return True
    return bool(_HIRING_PAGE.search(text) or ca.detect_ats(html))


def discover(name: str, website: str | None, careers_url: str | None = None, *,
             use_search: bool = True) -> Find:
    """Find the careers page (and the hosted board behind it, if any)."""
    f = Find()
    site = _site(website)
    with httpx.Client(headers={**HEADERS, "Accept": "text/html,*/*;q=0.8"}, timeout=TIMEOUT,
                      follow_redirects=True) as http:
        candidates: list[tuple[str, str]] = []           # (url, via)
        if careers_url and "api." not in (urlparse(careers_url).hostname or ""):
            candidates.append((careers_url, "on record"))
        if site:
            f.checked.append(site)
            kind, final, html = _fetch(http, site)
            if kind == "error":
                f.errors += 1
            page_links: list[tuple[str, str]] = []
            if kind == "ok":
                f.reached = True
                hit = ca.detect_ats(html)
                if hit:
                    f.ats, f.slug, f.via = hit[0], hit[1], "homepage link"
                page_links = ca.links(html, final or site)
            if kind != "ok" or len(page_links) < 5:
                # blocked, or a JS shell with no links: the reader renders it
                page = web.read(site, timeout=30, prefer="jina")
                md = page.get("text") or ""
                if md:
                    f.reached = True
                    page_links += ca.links(md, site)
                    if not f.ats:
                        hit = ca.detect_ats(md)
                        if hit:
                            f.ats, f.slug, f.via = hit[0], hit[1], "homepage link"
            for url, _ in page_links:
                hit = ca.detect_ats(url)
                if hit and not f.ats:
                    f.ats, f.slug, f.via = hit[0], hit[1], "homepage link"
            candidates += [(u, "link") for u in ca.careers_links(page_links, site)]
            candidates += [(site + p, "path") for p in PATHS]

        tried: set[str] = set()
        for url, via in candidates:
            if len(tried) >= MAX_CANDIDATES + len(PATHS):
                break
            key = url.rstrip("/")
            if key in tried:
                continue
            tried.add(key)
            f.checked.append(url)
            kind, final, html = _fetch(http, url)
            if kind == "error":
                f.errors += 1
                continue
            if kind != "ok":
                continue
            f.reached = True
            if not _looks_careers(final, url, html, site or url):
                continue
            f.careers_url, f.final_url, f.html, f.via = final or url, final, html, f.via or via
            if not f.ats:
                hit = ca.detect_ats(final or "") or ca.detect_ats(html)
                if hit:
                    f.ats, f.slug = hit
            return f

        if f.ats:
            # the homepage linked a board but no careers page answered: the board is the page
            f.careers_url = ca.BOARD_PAGES.get(f.ats, "").format(slug=f.slug) or None
            return f

        if use_search and not web.mcp_cooling():
            res = web.search(f"{name} careers jobs", limit=8, only=("exa-mcp",))
            if res.get("error") and not res.get("results"):
                f.errors += 1
            for r in res.get("results") or []:
                url = r.get("url") or ""
                hit = ca.detect_ats(url)
                if hit and _slug_fits(name, hit[1]):
                    f.ats, f.slug, f.via = hit[0], hit[1], "search"
                    f.careers_url = ca.BOARD_PAGES.get(hit[0], url).format(slug=hit[1])
                    return f
                if site and _same_site(url, site) and ca._CAREERS_WORD.search(urlparse(url).path or ""):
                    kind, final, html = _fetch(http, url)
                    f.checked.append(url)
                    if kind == "ok" and _looks_careers(final, url, html, site):
                        f.careers_url, f.final_url, f.html, f.via = final or url, final, html, "search"
                        hit = ca.detect_ats(final or "") or ca.detect_ats(html)
                        if hit:
                            f.ats, f.slug = hit
                        return f
    return f


# ------------------------------------------------------------------ reading the roles

_SYSTEM = """You read the text of one company's careers page and list the open job postings
on it. You copy each job title exactly as written on the page. You never invent a role, a
location or a link: a role that is not written on the page is not listed, and a field the
page does not state is null. Section headings, departments, perks and blog posts are not
roles. JSON only."""

_PROMPT = """Company: {name}
Careers page: {url}
---
{text}
---

Reply with exactly:
{{"roles": [{{"title": "<the job title, copied exactly>",
             "location": "<location text from the page>"|null,
             "apply_url": "<the link to this posting, if the page shows one>"|null,
             "remote": true|false|null}}]}}

If the page lists no open roles, reply {{"roles": []}}."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_#`>|\\\[\]]", " ", text or "")).strip().lower()


def roles_from_text(name: str, url: str, text: str) -> tuple[list[RawJob], str | None]:
    """The model reads the page; every title it returns must appear on the page verbatim
    (case and whitespace aside), or it is dropped. (jobs, error)."""
    from jobhunter import llm

    if not text or not _ROLE_WORD.search(text):
        return [], None
    try:
        data = llm.chat_json(_PROMPT.format(name=name, url=url, text=text[:9000]), _SYSTEM,
                             temperature=0.0, alias="cheap", purpose="careers-roles", default=None)
    except llm.BudgetExceeded as e:
        return [], f"model budget: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"model: {type(e).__name__}"
    if not isinstance(data, dict):
        return [], "model returned no JSON"
    page = _norm(text)
    out: list[RawJob] = []
    seen: set[str] = set()
    for r in data.get("roles") or []:
        if not isinstance(r, dict):
            continue
        title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
        if len(title) < 3 or _norm(title) not in page or title.lower() in seen:
            continue      # not on the page: invented, or mangled — either way not evidence
        seen.add(title.lower())
        loc = r.get("location") if isinstance(r.get("location"), str) and _norm(r["location"]) in page else None
        apply = r.get("apply_url") if isinstance(r.get("apply_url"), str) else None
        if apply and (not apply.startswith("http") or apply not in text):
            apply = None
        out.append(RawJob(
            company_name=name, title=title, url=apply or f"{url.split('#')[0]}#{ca._slug(title)}-{ca._slug(loc or '')}".rstrip("-"),
            source="careers", location=loc, remote=bool(r.get("remote") is True) or looks_remote(loc, title),
        ))
    return out, None


# A posting link: a path one level below a jobs/careers root, or an ATS posting URL.
_POSTING_URL = re.compile(
    r"(/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|roles?|opportunities|jobdetails|o|j)/[^/?#]{3,}"
    r"|jobs\.lever\.co/[^/]+/[0-9a-f-]{20,}|/careers/jobdetails/|jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{20,})", re.I)
_TITLE_WORD = re.compile(
    r"\b(engineer(?:ing)?|developer|scientist|intern(?:ship)?|sde|swe|sdet|designer|analyst|manager|architect|lead|"
    r"associate|executive|specialist|consultant|head|officer|devops|sre|qa|tester|programmer|researcher|"
    r"administrator|technician|director|representative|coordinator|strategist|writer|counsel|accountant)\b", re.I)
_NOT_A_TITLE = re.compile(r"\b(view|see|all|apply|open (?:roles|positions)|careers?|jobs? at|join|our|we|you|"
                          r"read more|learn more|life at|team)\b", re.I)
# where a link's text stops being the title: the location / type that boards print after it
_TAIL = re.compile(r"\s*(?:[|·•–—]\s*|\s-\s|\s+(?=(?:Bengaluru|Bangalore|Hyderabad|Remote|Full[- ]?time|Part[- ]?time|"
                   r"Contract|Mumbai|Delhi|New Delhi|Pune|Chennai|Gurgaon|Gurugram|Noida|India|On-?site|Hybrid|"
                   r"Apply|Posted)\b))", re.I)
_PLACE = re.compile(r"\b(Bengaluru|Bangalore|Hyderabad|Secunderabad|Remote|Mumbai|Delhi|New Delhi|Pune|Chennai|Gurgaon|"
                    r"Gurugram|Noida|Kolkata|Ahmedabad|India|Singapore|Dubai|London|San Francisco|New York|"
                    r"United States|USA|Anywhere|Worldwide)\b", re.I)


def _split_title(text: str) -> tuple[str, str | None]:
    """'Backend Engineer Bengaluru · Full-time' → ('Backend Engineer', 'Bengaluru')."""
    text = re.sub(r"\s+", " ", re.sub(r"[*_#`]", " ", text or "")).strip()
    parts = _TAIL.split(text, maxsplit=1)
    title = parts[0].strip(" -|·•")
    rest = text[len(parts[0]):]
    places = [m.group(1) for m in _PLACE.finditer(rest)]
    return title, (", ".join(dict.fromkeys(places)) or None)


def roles_from_links(page: str, page_url: str, name: str) -> list[RawJob]:
    """Postings read off the page's own links, no model: a link whose URL is a posting
    and whose text is a job title. The title and the URL are both literally on the page."""
    out: list[RawJob] = []
    seen: set[str] = set()
    root = page_url.split("#")[0].rstrip("/")
    for url, text in ca.links(page, page_url):
        clean = url.split("#")[0].rstrip("/")
        if clean == root or not _POSTING_URL.search(url):
            continue
        title, place = _split_title(text)
        if not (3 <= len(title) <= 90) or not _TITLE_WORD.search(title) or _NOT_A_TITLE.search(title) \
                or len(title.split()) > 10 or title.lower() in seen:
            continue
        seen.add(title.lower())
        out.append(RawJob(company_name=name, title=title, url=url, source="careers", location=place,
                          remote=looks_remote(place, title)))
    return out


_LIST_LINE = re.compile(r"^\s*(?:#{2,6}|[-*•]|\d+\.)\s+(.+?)\s*$")


_CARD_META = re.compile(r"^\s*[•·\-*]?\s*(on[- ]?site|remote|hybrid|full[- ]?time|part[- ]?time|internship|contract|"
                        r"entry level|mid level|senior level)\b", re.I)


def roles_from_lines(text: str, page_url: str, name: str) -> list[RawJob]:
    """Last resort without a model: lines that are a job title and nothing else — a
    Markdown heading or bullet, or a job card's first line (a title followed by its
    "• On Site • Bengaluru" meta lines). Short, a title word, no sentence. The page is the URL."""
    out: list[RawJob] = []
    seen: set[str] = set()
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        m = _LIST_LINE.match(line)
        place_hint = None
        if m:
            raw = m.group(1)
        else:
            # a job card: the title, then within three lines its location / work-mode meta
            following = lines[i + 1:i + 4]
            if not any(_CARD_META.search(f) for f in following):
                continue
            raw = line
            places = [p.group(1) for f in following for p in _PLACE.finditer(f)]
            place_hint = ", ".join(dict.fromkeys(places)) or None
        raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
        title, place = _split_title(raw)
        place = place or place_hint
        if not (5 <= len(title) <= 70) or len(title.split()) > 8 or title.endswith((".", ":", "?", "!")) \
                or not _TITLE_WORD.search(title) or _NOT_A_TITLE.search(title) or title.lower() in seen:
            continue
        seen.add(title.lower())
        out.append(RawJob(company_name=name, title=title, url=f"{page_url.split('#')[0]}#{ca._slug(title)}",
                          source="careers", location=place, remote=looks_remote(place, title)))
    return out


def _read_roles(f: Find, name: str) -> tuple[list[RawJob], str, str | None]:
    """(jobs, how, error) — the board's JSON, the page's JSON-LD, the posting links on the
    page, the page text read by the model, and last its title-only headings."""
    if f.ats in ca.API_FETCHERS and f.slug:
        async def run():
            async with make_client() as http:
                return await ca.read_board(http, f.ats, f.slug, name)
        status, jobs = asyncio.run(run())
        if status == ca.HIT:
            return list(jobs or []), f"{f.ats} board JSON", None
        if status == ca.ERROR:
            return [], f"{f.ats} board", f"board unreachable: {jobs}"
    url = f.careers_url
    if f.html:
        jobs = ca.jsonld_jobs(f.html, url or "", name)
        if jobs:
            return jobs, "JSON-LD", None
        jobs = roles_from_links(f.html, f.final_url or url or "", name)
        if jobs:
            return jobs, "posting links", None
    if not url:
        return [], "", None
    board = ca.BOARD_PAGES.get(f.ats or "", "").format(slug=f.slug) if f.slug else ""
    if board and board.rstrip("/") != url.rstrip("/"):
        # their page embeds a hosted board (Keka, Darwinbox, Zoho …): the board's own page
        # lists the postings as links, which the reader renders
        bp = web.read(board, timeout=40, prefer="jina")
        jobs = roles_from_links(bp.get("text") or "", board, name)
        if jobs:
            return jobs, f"{f.ats} board page links", None
    page = web.read(url, timeout=40, prefer="jina")
    if page.get("backend") != "jina" and len(page.get("text") or "") < 2500:
        # a cached direct fetch of a JS page is its nav bar; the reader renders the roles
        page = web.read(url, timeout=40, prefer="jina", fresh=True)
    text = page.get("text") or (html_to_text(f.html, limit=12000) if f.html else "")
    if not text:
        return [], "page text", "careers page unreadable"
    jobs = roles_from_links(text, url, name)
    if jobs:
        return jobs, "posting links (reader)", None
    jobs, err = roles_from_text(name, url, text)
    if jobs or not err:
        return jobs, "page text + model", None
    jobs = roles_from_lines(text, url, name)
    if jobs:
        return jobs, "title headings", None
    return [], "page text", err


# ------------------------------------------------------------------ one company

def _read_at(notes: str | None) -> datetime | None:
    m = re.search(r"\[careers\] read (\d{4}-\d{2}-\d{2})", notes or "")
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None
    except ValueError:
        return None


def _note(notes: str | None, line: str) -> str:
    kept = [ln for ln in (notes or "").splitlines() if not ln.startswith(MARK)]
    return "\n".join(kept + [line]).strip()


def run_company(company_id: int, *, use_search: bool = True) -> dict:
    """Find one company's careers page and roles; write what was found."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, website, careers_url, ats, slug = c.name, c.website, c.careers_url, c.ats, c.ats_slug

    out: dict = {"id": company_id, "company": name}
    f = Find()
    jobs: list[RawJob] = []
    how, err = "", None
    # 1. the board on record, if its provider answers with JSON
    if ats in ca.API_FETCHERS and slug and _slug_fits(name, slug):
        f = Find(ats=ats, slug=slug, via="board on record",
                 careers_url=careers_url if careers_url and "api." not in careers_url else ca.BOARD_PAGES[ats].format(slug=slug))
        jobs, how, err = _read_roles(f, name)
        if not jobs and not err:
            f = Find()          # a dead slug (a guess that once matched): look for the real page
    if not f.careers_url:
        f = discover(name, website, careers_url, use_search=use_search)
        jobs, how, err = _read_roles(f, name) if f.careers_url else ([], "", None)

    out.update({"careers_url": f.careers_url, "ats": f.ats, "via": f.via, "how": how, "found": len(jobs)})
    kept = {}
    if jobs:
        for j in jobs:
            j.company_name, j.company_website = name, website
        stats = pipeline.persist(jobs, pipeline.ScrapeStats(scraped=len(jobs), per_source={"careers": len(jobs)}))
        kept = {"kept": stats.kept, "inserted": stats.inserted, "updated": stats.updated}
        out.update(kept)

    today = utcnow().strftime("%Y-%m-%d")
    with get_session() as session:
        c = session.get(Company, company_id)
        if f.careers_url:
            c.careers_url = f.careers_url
            if f.ats and (not c.ats or c.ats != f.ats and f.via != "board on record"):
                c.ats, c.ats_slug = f.ats, f.slug
            if jobs:
                titles = [j.title for j in jobs][:40]
                c.hiring_status = "verified"
                c.hiring_evidence = f"{len(jobs)} open role(s) listed on their own careers page ({how}): {', '.join(titles[:5])}"[:300]
                c.hiring_roles = json.dumps(titles)
                c.hiring_checked_at = utcnow()
                if not c.hiring_claim_url or c.hiring_claim_source in ("yc", None):
                    c.hiring_claim, c.hiring_claim_url = f"{len(jobs)} open role(s) on their careers page", f.careers_url
                    c.hiring_claim_by, c.hiring_claim_source, c.hiring_claim_at = None, "careers", utcnow()
            if err:
                # the page was found but not read (model out for the day, board down):
                # retry soon, and no "read" marker — it has not been
                backoff.fail(c, STEP, err, retryable=True)
                out["error"] = err
            else:
                backoff.ok(c, STEP)
                c.notes = _note(c.notes, f"{MARK} {today}: {f.careers_url} — {len(jobs)} role(s) via {how or f.via}")
        else:
            # nothing answered at all → our failure, retry soon; their site answered but no
            # careers page anywhere → a definitive miss, the longest wait
            retryable = f.errors > 0 and not f.reached or not website
            why = "no careers page found" if f.reached else (
                "site unreachable" if website else "no website on file")
            backoff.fail(c, STEP, f"{why} ({len(f.checked)} url(s) checked)", retryable=retryable)
            c.notes = _note(c.notes, f"{MARK} {today}: none — {why}")
            out["error"] = why
        session.add(c)
        session.commit()
    log.info("careers: %s", {k: v for k, v in out.items() if k != "id"})
    return out


# ------------------------------------------------------------------ the pass

def pending(limit: int | None = 40, *, list_key: str | None = None, force: bool = False,
            company_ids: list[int] | None = None, refresh_days: int = REFRESH_DAYS) -> list[int]:
    """Who to read, in db.fill_order: list rows first, then scrape-first, then priority.
    Skips rows read in the last `refresh_days` and rows in backoff, unless `force`."""
    init_db()
    cutoff = utcnow() - timedelta(days=refresh_days)
    with get_session() as session:
        stmt = select(Company).where(col(Company.status).is_(None))
        if list_key:
            stmt = stmt.where(Company.list_key == list_key)
        if company_ids:
            stmt = stmt.where(col(Company.id).in_(company_ids))
        rows = [c for c in session.exec(stmt).all()
                if (c.list_key or (c.tier != "reject" and not c.overrated))
                and (c.website or c.domain or c.ats_slug)]
        if not force:
            rows = [c for c in rows if backoff.due(c, STEP)
                    and not ((_read_at(c.notes) or datetime.min) >= cutoff)]
        rows.sort(key=lambda c: (*fill_order(c), c.id))
        ids = [c.id for c in rows]
    return ids[:limit] if limit else ids


def run(limit: int | None = 40, *, list_key: str | None = None, force: bool = False,
        company_ids: list[int] | None = None, concurrency: int = 4, use_search: bool = True) -> dict:
    """The careers pass over `pending(...)`. Each company commits on its own."""
    ids = pending(limit, list_key=list_key, force=force, company_ids=company_ids)
    out: dict = {"companies": len(ids), "careers_found": 0, "with_roles": 0, "roles_found": 0,
                 "roles_inserted": 0, "ats": {}, "via": {}, "failed": {}, "lines": []}

    def one(cid: int) -> dict:
        try:
            return run_company(cid, use_search=use_search)
        except Exception as e:  # noqa: BLE001 — one company must not end the pass
            log.exception("careers: company %s failed", cid)
            return {"id": cid, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for r in ex.map(one, ids):
            out["lines"].append(r)
            if r.get("careers_url"):
                out["careers_found"] += 1
                out["via"][r.get("via") or "?"] = out["via"].get(r.get("via") or "?", 0) + 1
            if r.get("ats"):
                out["ats"][r["ats"]] = out["ats"].get(r["ats"], 0) + 1
            if r.get("found"):
                out["with_roles"] += 1
                out["roles_found"] += r["found"]
                out["roles_inserted"] += r.get("inserted", 0)
            if r.get("error"):
                key = r["error"].split(":")[0][:40]
                out["failed"][key] = out["failed"].get(key, 0) + 1
    log.info("careers pass: %s", {k: v for k, v in out.items() if k != "lines"})
    return out


__all__ = ["discover", "roles_from_text", "run_company", "run", "pending", "Find"]
