"""India discovery, more sources: Bengaluru / Hyderabad companies found by where they
live rather than by a funding headline.

Tejmul, 2026-10-14: "The India companies in Bengaluru and Hyderabad are too few — scrape
them." india.py's sources (funding feeds, FundedIQ, YC, keyless search, ATS boards) were
nearly exhausted, so these read the places that list companies by city:

  github     GitHub organisations whose own profile says Bengaluru / Bangalore /
             Hyderabad (search API, unauthenticated — 10 searches a minute, which is its
             own quota, not the 60/h core one the people step spends), then the org's
             public github.com page for its website. The location is the org's own words
             (own_page). Community / university / meetup / personal orgs and orgs with no
             website are skipped.
  cutshort   Cutshort's public job pages for Bengaluru and Hyderabad, read from the
             server-rendered page data (no JS, no login): each post carries the company's
             type, size, stage, website and the post's city. Service shops, "hiring for a
             client" posts, 1000+ headcount and confidential posters are skipped.
  portfolio  accelerator / incubator / VC portfolio pages (IIIT-Hyderabad CIE, NSRCEL,
             Better Capital, India Quotient, Chiratae, Antler, Kalaari, Stellaris,
             Elevation, Z47, Surge, Lightspeed, Blume) — the company's website off the
             page, then the company's OWN site must name Bengaluru / Hyderabad (a footer
             address, "based in"), or it is not admitted.
  wellfound  Wellfound's role × city pages, read through Jina Reader (its company pages
             sit behind a bot check). The post's own "In office • Bengaluru" line places
             the company; the website is a guessed domain that only counts when the
             site itself names the company.

Every candidate goes through india._admit -> harvest._upsert -> admission.admit (name
cleanup, dedup, famous / overrated) and guardrails.set_city with the quote and its URL;
before that, admission.body_shop (staffing, client posts, IT services) and a large-
enterprise headcount bar. New rows get india_found_at, so the fill queue takes them next.

Polite: one request to a host at least PACE_S apart, a 429 / 503 cools that host for
the rest of the run, nothing logs in. Cursors and queues live in settings so the tick
picks up where the last pass stopped.

Layering: pipeline layer — india, admission, guardrails, research, scrapers below; tick
and the CLI above.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import unescape
from urllib.parse import quote as urlquote, urljoin, urlparse

import httpx

from jobhunter import CONFIG, admission, guardrails
from jobhunter.db import get_session, get_setting, set_setting

log = logging.getLogger(__name__)

_S = (((CONFIG.get("targeting") or {}).get("india") or {}).get("discovery") or {}).get("sources") or {}
PACE_S = float(_S.get("gap_seconds", 3.5))
LARGE = int((CONFIG.get("india_jobs") or {}).get("large_headcount", 5000))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# ================================================================== polite fetching

_last: dict[str, float] = {}
_cool: dict[str, float] = {}


class Cooled(Exception):
    """The host answered 429 / 503 (or a bot wall) — leave it alone for this run."""


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _pace(host: str, gap: float | None = None) -> None:
    t = _last.get(host)
    g = PACE_S if gap is None else gap
    if t is not None and time.time() - t < g:
        time.sleep(g - (time.time() - t))
    _last[host] = time.time()


def get(url: str, *, timeout: float = 20, gap: float | None = None, accept: str = "text/html,*/*;q=0.8",
        headers: dict | None = None) -> httpx.Response | None:
    """One GET, paced per host. None on a network error or a 4xx; raises Cooled on a 429 /
    503 (and cools the host for 20 minutes, or its Retry-After)."""
    host = _host(url)
    if _cool.get(host, 0) > time.time():
        raise Cooled(host)
    _pace(host, gap)
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-IN,en;q=0.9",
                               **(headers or {})})
    except Exception as e:  # noqa: BLE001 — a dead site is an answer, not a crash
        log.debug("get %s: %s", url, e)
        return None
    if r.status_code in (429, 503):
        try:
            wait = min(3600.0, float(r.headers.get("retry-after") or 1200))
        except ValueError:
            wait = 1200.0
        _cool[host] = time.time() + wait
        raise Cooled(f"{host}: HTTP {r.status_code}")
    if r.status_code >= 400:
        return None
    if len(r.content) > 4_000_000:
        return None
    return r


# ================================================================== reading a company's own site

_CITY = re.compile(r"\b(bengaluru|bangalore|hyderabad|secunderabad)\b", re.I)
_PARKED = re.compile(r"(?i)\b(domain (?:is )?for sale|buy this domain|this domain may be for sale|parked free|"
                     r"domain has expired|hugedomains|sedo\.com|dan\.com|godaddy\.com/domain)")
_STRIP = re.compile(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>")
_TAG = re.compile(r"<[^>]+>")


def page_text(html: str) -> str:
    """Visible text with line breaks at block ends — footers kept (that is where the
    office address is)."""
    t = _STRIP.sub(" ", html or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|span|a|td|tr|section|footer|address)>", "\n", t)
    t = unescape(_TAG.sub(" ", t))
    t = re.sub(r"[ \t\r\f\v\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def meta(html: str, *names: str) -> str | None:
    for n in names:
        m = re.search(rf'<meta[^>]+(?:name|property)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)', html or "", re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']{re.escape(n)}["\']', html or "", re.I)
        if m:
            return re.sub(r"\s+", " ", unescape(m.group(1))).strip()
    return None


def title_of(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", unescape(m.group(1))).strip()[:200] if m else None


def city_windows(text: str, *, width: int = 140) -> list[str]:
    """Short passages around each mention of Bengaluru / Hyderabad — the lines an
    address or a 'based in' sentence sits on."""
    out: list[str] = []
    lines = [ln.strip() for ln in (text or "").split("\n")]
    for i, line in enumerate(lines):
        if not line or not _CITY.search(line):
            continue
        if len(line) < 60:
            # a bare "Bengaluru" under an "Our office" heading: the lines around it are the passage
            line = " · ".join(x for x in lines[max(0, i - 2):i + 3] if x and len(x) < 160)
        if len(line) <= 2 * width:
            snip = line
        else:
            m = _CITY.search(line)
            snip = line[max(0, m.start() - width):m.end() + width].strip()
        if snip not in out:
            out.append(snip)
    return out


def city_from_text(text: str, *, name: str, domain: str | None = None) -> str | None:
    """The first passage of a company's own page that places it in Bengaluru / Hyderabad
    (guardrails.india_city, own_page): a passage that also names Delhi or Pune without
    saying where they are based does not count."""
    for w in city_windows(text):
        if guardrails.india_city(w, name=name, domain=domain, own_page=True):
            return w[:400]
    return None


def city_from_markup(html: str, *, name: str, domain: str | None = None) -> str | None:
    """The same, from the page's markup itself — a JSON-LD PostalAddress, a Next.js /
    Framer data blob, an address in an attribute — for sites whose visible text is drawn
    by JavaScript. The passage is de-JSONed so it reads as the address it is."""
    for m in _CITY.finditer(html or ""):
        raw = html[max(0, m.start() - 200):m.end() + 80]
        txt = raw.replace('\\"', '"').replace("\\u0026", "&").replace("\\n", " ")
        txt = unescape(_TAG.sub(" ", txt))
        txt = re.sub(r'"(?:streetAddress|addressLocality|addressRegion|postalCode|addressCountry|address)"\s*:', " ", txt)
        txt = re.sub(r'[{}\[\]"\\]+', " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip(" ,:;")
        if not re.search(r"\d{6}|\b(?:road|rd|street|st|nagar|layout|block|floor|sector|cross|main|colony|"
                         r"tower|park|hub|address|office|based|headquarter\w*|hq|located)\b", txt, re.I):
            continue          # an address or a "based in" line, not a word in a script
        if guardrails.india_city(txt, name=name, domain=domain, own_page=True):
            return txt[:400]
    return None


_SUBPAGES = ("/contact", "/contact-us", "/about", "/about-us")


def own_site(website: str, *, name: str | None = None, pages: int = 4) -> dict:
    """Read the company's own site: homepage first, then a contact / about page until a
    passage names Bengaluru / Hyderabad. {ok, final, name, description, quote, url, why}."""
    out: dict = {"ok": False, "why": "website unreachable"}
    url = website if "://" in website else f"https://{website}"
    tried = 0
    base = None
    for path in ("",) + _SUBPAGES:
        if tried >= pages:
            break
        target = url if not path else urljoin(base or url, path)
        tried += 1
        try:
            r = get(target, timeout=15)
        except Cooled:
            out["why"] = "website rate-limited us"
            return out
        if r is None or "html" not in (r.headers.get("content-type") or "html"):
            if not path:
                return out
            continue
        html = r.text
        if not path:
            base = str(r.url)
            out["final"] = base
            if _PARKED.search(html[:20000]) or guardrails.bot_wall(page_text(html)[:3000]):
                out["why"] = "parked domain / bot wall"
                return out
            site = meta(html, "og:site_name")
            out["title"] = title_of(html)
            out["name"] = site
            out["description"] = meta(html, "description", "og:description")
            out["why"] = "its own site names neither Bengaluru nor Hyderabad"
        text = page_text(html)
        nm, dom = name or out.get("name") or "", _host(base or url)
        q = city_from_text(text, name=nm, domain=dom) or city_from_markup(html, name=nm, domain=dom)
        if q:
            out.update(ok=True, quote=q, url=str(r.url))
            return out
    return out


# ================================================================== shared bookkeeping

_SEEN = "india_src:seen"


def _seen(session, kind: str) -> set[str]:
    try:
        return set(json.loads(get_setting(session, f"{_SEEN}:{kind}", "[]") or "[]"))
    except ValueError:
        return set()


def _mark(session, keys: list[str], kind: str) -> None:
    if not keys:
        return
    have = list(_seen(session, kind))
    s = set(have)
    have += [k for k in keys if k not in s]
    _put(session, f"{_SEEN}:{kind}", have[-30000:])


def _json(session, key: str, default):
    try:
        v = json.loads(get_setting(session, key, "") or "null")
    except ValueError:
        v = None
    return default if v is None else v


def _put(session, key: str, value) -> None:
    for attempt in range(5):
        try:
            set_setting(session, key, json.dumps(value))
            return
        except Exception as e:  # noqa: BLE001 — "database is locked" under the fill workers
            if "locked" not in str(e).lower() or attempt == 4:
                raise
            session.rollback()
            time.sleep(2 + 3 * attempt)


def _known(session, name: str, domain: str | None = None):
    """The existing row and whether it already carries a city (then there is nothing to
    learn from fetching anything)."""
    row = admission.find_existing(session, name, domain)
    return row, bool(row is not None and (row.city or row.status in ("quarantined", "merged")))


def admit(run, cand) -> object | None:
    """india._admit in its own short session, retried when the fill workers hold the lock."""
    from jobhunter import india

    for attempt in range(5):
        try:
            with get_session() as session:
                return india._admit(session, run, cand)
        except Exception as e:  # noqa: BLE001
            if "locked" not in str(e).lower() or attempt == 4:
                raise
            time.sleep(2 + 3 * attempt)
    return None


def _skip(run, kind: str, why: str) -> None:
    run.stat(kind)["seen"] += 1
    run.skip(kind, why)


# ================================================================== source: GitHub organisations

GH_API = "https://api.github.com/search/users"
GH_LOCATIONS = ("Bengaluru", "Bangalore", "Hyderabad", "Secunderabad")
K_GH_CURSOR = "india_src:github_cursor2"    # {"q": i, "page": p}
K_GH_QUEUE = "india_src:github_queue2"      # logins still to read


def gh_queries() -> list[str]:
    """Most-followed first: a company's org is followed, a weekend project's is not
    (2026-10-14: newest-first returned hobby orgs). Each stays under the 1,000-result cap."""
    return [f"type:org location:{loc} repos:>2" for loc in GH_LOCATIONS]


# what an org that is not a company looks like: a school, a club, a meetup, a course
_GH_JUNK = re.compile(
    r"(?i)\b(universit\w*|college|institut\w*|school|students?|student chapter|club|communit(?:y|ies)|meetups?|"
    r"user group|gdg|google developer|developer student|dsc|hackathon|hacktoberfest|devfest|pycon|jsconf|conf\b|"
    r"conference|summit|society|chapter|foundation|ngo|non-?profit|charity|iit\b|iiit\b|nit\b|bits\b|iisc|"
    r"course|bootcamp|tutorials?|workshop|academy|training (?:institute|cent(?:er|re)|programs?|courses?)|classroom|assignments?|batch\b|cohort|hostel|alumni|fan\s?club|"
    r"ieee|acm\b|mlh|open source community|volunteer|government|govt|municipal|police|research group|lab of|"
    r"barcamp|collective|starters|sample apps|personal|portfolio|my projects|freelanc\w*|archive|mirror|forks?\b|test org|testing org|sandbox)\b")
_GH_JUNK_HOST = re.compile(r"(?i)(\.org$|\.edu(\.\w+)?|\.ac\.in|\.edu\.in|\.gov\.in|\.nic\.in|\.res\.in|blogspot\.|"
                           r"wordpress\.com|medium\.com|meetup\.com|linktr\.ee|github\.io|gitbook\.io|"
                           r"\.org\.in|google\.com|youtube\.com|discord|t\.me|facebook\.com|instagram\.com)")
# names IT-services shops give themselves (2026-10-14, Hyderabad orgs)
_GH_SHOP = re.compile(r"(?i)\b(techsoft|soft(?:ech|tech)|infotech|web\s?(?:enrich|solutions|services)|"
                      r"consultancy|consulting|digital (?:solutions|services)|"
                      r"social (?:media|feathers)|tech group|hack for)\b")
# agencies selling development hours (admission.body_shop catches the staffing side)
AGENCY = re.compile(
    r"(?i)\b(software development (?:company|agency|firm|services)|app development (?:company|agency)|"
    r"web development (?:company|agency|services)|(?:digital|creative|marketing|design) agency|"
    r"it (?:services|solutions|consulting)|outsourc\w*|offshore|staff augmentation|dedicated developers|"
    r"hire (?:dedicated )?developers|custom software development|development partner for|we build apps for|"
    r"we build [\w\s&()]{0,40}(?:for|apps|websites|frontends|products for)|(?:gold|premier|consulting|"
    r"implementation|technology) partner|digital transformation|consultancy|consulting services|"
    r"product engineering services|engineering services|software services|staffing|recruitment)\b")


def parse_gh_org(html: str) -> dict:
    """github.com/<org>: display name, website, location, description."""
    out: dict = {}
    m = re.search(r'<h1 class="h2 lh-condensed"[^>]*>\s*([^<]+?)\s*<', html or "")
    if m:
        out["name"] = unescape(m.group(1)).strip()
    m = re.search(r'itemprop="url"[^>]*href="([^"]+)"', html or "") or \
        re.search(r'href="([^"]+)"[^>]*itemprop="url"', html or "")
    if m:
        out["website"] = unescape(m.group(1)).strip()
    m = re.search(r'itemprop="location"[^>]*>([^<]+)<', html or "")
    if m:
        out["location"] = unescape(m.group(1)).strip()
    d = meta(html, "description")
    if d:
        d = re.sub(r"\s*[^.]*? has \d[\d,]* repositor(?:y|ies) available\. Follow their code on GitHub\.?\s*$", "", d).strip()
        out["description"] = d or None
    return out


def gh_junk(login: str, info: dict) -> str | None:
    """Why this org is not a company, or None."""
    site = info.get("website") or ""
    if not site:
        return "no website on its GitHub profile"
    host = _host(site if "://" in site else f"https://{site}")
    if _GH_JUNK_HOST.search(host) or admission.bogus_host(host, info.get("name") or login):
        return "website is not a company site"
    blob = " ".join(filter(None, [login, info.get("name"), info.get("description")]))
    if _GH_JUNK.search(blob.replace("-", " ").replace("_", " ")):
        return "community / university / personal org"
    if AGENCY.search(blob) or admission.body_shop(info.get("name") or login, info.get("description")) or \
            _GH_SHOP.search(info.get("name") or login):
        return "agency / IT services / staffing"
    if not (admission.host_fits_name(info.get("name") or login, host) or admission.host_fits_name(login, host)):
        return "website does not carry the org's name (a person's site)"
    return None


def _gh_search(session, run, kind: str) -> list[str] | None:
    """The next page of org logins; None when the search is exhausted or refused."""
    cur = _json(session, K_GH_CURSOR, {"q": 0, "page": 1})
    qs = gh_queries()
    while cur["q"] < len(qs):
        q = qs[cur["q"]]
        try:
            r = get(f"{GH_API}?q={urlquote(q)}&sort=followers&order=desc&per_page=100&page={cur['page']}",
                    gap=7.0, accept="application/vnd.github+json")
        except Cooled as e:
            run.stat(kind).setdefault("problems", []).append(f"search refused: {e}")
            return None
        if r is None:
            # 422 past the 1,000th result, or a refusal: next query
            cur = {"q": cur["q"] + 1, "page": 1}
            _put(session, K_GH_CURSOR, cur)
            continue
        if r.headers.get("x-ratelimit-remaining") == "0":
            _cool["api.github.com"] = float(r.headers.get("x-ratelimit-reset") or time.time() + 60)
        items = (r.json() or {}).get("items") or []
        logins = [i["login"] for i in items if i.get("type") == "Organization"]
        total = int((r.json() or {}).get("total_count") or 0)
        nxt = cur["page"] + 1
        cur = {"q": cur["q"], "page": nxt} if items and (nxt - 1) * 100 < min(total, 1000) else {"q": cur["q"] + 1, "page": 1}
        _put(session, K_GH_CURSOR, cur)
        if logins:
            return logins
    return None


def from_github(run, *, orgs: int = 25, searches: int = 1, kind: str = "github") -> int:
    """Read up to `orgs` org pages (searching for more when the queue runs dry)."""
    with get_session() as session:
        queue: list[str] = _json(session, K_GH_QUEUE, [])
        seen = _seen(session, kind)
    read = 0
    done: list[str] = []
    try:
        while read < orgs and not run.full:
            if not queue:
                if searches <= 0:
                    break
                searches -= 1
                with get_session() as session:
                    got = _gh_search(session, run, kind)
                if not got:
                    break
                queue = [g for g in got if f"gh:{g.lower()}" not in seen]
                continue
            login = queue.pop(0)
            key = f"gh:{login.lower()}"
            done.append(key)
            with get_session() as session:
                row, has_city = _known(session, login)
            if has_city:
                _skip(run, kind, "already known")
                continue
            try:
                r = get(f"https://github.com/{login}", gap=PACE_S)
            except Cooled as e:
                queue.insert(0, login)
                done.pop()
                run.stat(kind).setdefault("problems", []).append(f"github.com cooled us: {e}")
                break
            read += 1
            if r is None:
                _skip(run, kind, "org page unreadable")
                continue
            info = parse_gh_org(r.text)
            why = gh_junk(login, info)
            if why:
                _skip(run, kind, why)
                continue
            loc = info.get("location") or ""
            if not admission.india_city(loc):
                _skip(run, kind, "profile location is not Bengaluru / Hyderabad")
                continue
            name = info.get("name") or login
            website = info["website"] if "://" in info["website"] else f"https://{info['website']}"
            quote = f"{name} — GitHub organisation github.com/{login}, location: {loc}"
            from jobhunter.india import Cand

            admit(run, Cand(name=name, text=quote, url=f"https://github.com/{login}", kind=kind, headline=False,
                            website=website, website_is_link=False, description=info.get("description"),
                            quote=quote, own_page=True, check_age=False))
    finally:
        with get_session() as session:
            _put(session, K_GH_QUEUE, queue)
            _mark(session, done, kind)
    return read


# ================================================================== source: Cutshort job pages

CS = "https://cutshort.io"
CS_CITIES = {"Bengaluru": "bangalore-bengaluru", "Hyderabad": "hyderabad"}
CS_ROLES = ["startup", "backend-developer", "fullstack-developer", "frontend-developer", "python-developer",
            "java-developer", "nodejs-developer", "react-developer", "android-developer", "ios-developer",
            "flutter-developer", "devops", "datascience", "data-engineer", "golang-developer",
            "machine-learning", "artificial-intelligence", "software-developer", "qa-testing", "cloud"]
CS_MAX_PAGE = 10
K_CS_CURSOR = "india_src:cutshort_cursor"   # {"i": page-list index, "page": n}
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def cs_pages() -> list[str]:
    return [f"{CS}/jobs/{role}-jobs-in-{slug}" for slug in CS_CITIES.values() for role in CS_ROLES]


def _walk(obj, key: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield obj
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


def parse_cutshort(html: str) -> list[dict]:
    """Job posts off a Cutshort list page: company name, alias, type, size, stage,
    founded, website, hiringForClient, and the post's title and locations."""
    m = _NEXT.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out = []
    for post in _walk(data, "companyDetails"):
        cd = post.get("companyDetails") or {}
        if not isinstance(cd, dict) or not cd.get("name"):
            continue
        out.append({
            "name": str(cd["name"]).strip(), "alias": cd.get("alias"), "type": cd.get("type"),
            "size": cd.get("size"), "stage": cd.get("stage"), "founded": cd.get("founded"),
            "website": ((cd.get("links") or {}).get("website") or cd.get("websiteLink")),
            "client": bool(cd.get("hiringForClient")), "title": (post.get("headline") or post.get("title") or "").strip(),
            "locations": [str(x) for x in (post.get("locations") or [])],
            "about": re.sub(r"<[^>]+>", " ", unescape(str(post.get("sanitizedComment") or "")))[:3000],
        })
    return out


def _size_floor(size: str | None) -> int:
    nums = [int(n) for n in re.findall(r"\d+", size or "")]
    return nums[0] if nums else 0


def cs_gate(row: dict) -> str | None:
    """Why a Cutshort poster is not a target company, or None."""
    if row["client"]:
        return "posts on behalf of a client"
    if (row.get("type") or "") == "Services":
        return "IT-services company (Cutshort type: Services)"
    if (row.get("type") or "") != "Product":
        # 2026-10-14: "Products & Services" and untyped posters were mostly dev shops
        return "not a product company (Cutshort type: Products & Services / none)"
    if _size_floor(row.get("size")) >= 1000:
        return "large enterprise (1000+ employees)"
    why = admission.body_shop(row["name"], row.get("about"))
    if why:
        return why.split(" (")[0]
    if AGENCY.search(row["name"]) or admission._IT_SERVICES_NAME.search(row["name"]) or \
            ((row.get("type") or "") != "Product" and AGENCY.search(row.get("about") or "")):
        return "agency / IT services / staffing"
    if row.get("founded") and int(row["founded"]) < 2008:
        return "founded before 2008 — not a startup"
    return None


def from_cutshort(run, *, pages: int = 2, kind: str = "cutshort") -> int:
    with get_session() as session:
        cur = _json(session, K_CS_CURSOR, {"i": 0, "page": 1})
    allp = cs_pages()
    done = 0
    handled: set[str] = set()
    while done < pages and not run.full and cur["i"] < len(allp):
        url = allp[cur["i"]] + (f"?page={cur['page']}" if cur["page"] > 1 else "")
        try:
            r = get(url)
        except Cooled as e:
            run.stat(kind).setdefault("problems", []).append(str(e))
            break
        done += 1
        rows = parse_cutshort(r.text) if r is not None else []
        nxt = {"i": cur["i"], "page": cur["page"] + 1} if rows and cur["page"] < CS_MAX_PAGE else {"i": cur["i"] + 1, "page": 1}
        cur = nxt
        for row in rows:
            if run.full:
                break
            k = admission.name_key(row["name"])
            if not k or k in handled:
                continue
            handled.add(k)
            why = cs_gate(row)
            if why:
                _skip(run, kind, why)
                continue
            loc = next((l for l in row["locations"] if admission.india_city(l)), None)
            if not loc:
                _skip(run, kind, "post is not in Bengaluru / Hyderabad")
                continue
            if not row.get("website"):
                _skip(run, kind, "no website on its Cutshort profile")
                continue
            if not admission.host_fits_name(row["name"], admission.host_of(row["website"])):
                _skip(run, kind, "website does not carry the company's name")
                continue
            with get_session() as session:
                _row, has_city = _known(session, row["name"], admission.host_of(row["website"]))
            if has_city:
                _skip(run, kind, "already known")
                continue
            from jobhunter.india import Cand

            quote = f"{row['name']} is hiring {row['title'] or 'engineers'} in {loc} (Cutshort job post)"
            admit(run, Cand(name=row["name"], text=f"{quote}. {row.get('about') or ''}"[:1500], url=f"{CS}/company/{row['alias']}" if row.get("alias") else url,
                            kind=kind, headline=False, website=row["website"], website_is_link=False,
                            quote=quote, own_page=True, check_age=False))
        with get_session() as session:
            _put(session, K_CS_CURSOR, cur)
    return done


# ================================================================== source: portfolio / incubator pages

# mode "links": the company websites are linked from the index page itself;
# mode "pages": the index links one page per company (prefix), which links the website
PORTFOLIOS: list[dict] = [
    # India-first; the global portfolios (Accel, Antler, Lightspeed) last — their own sites filter them
    {"key": "iiith-cie", "url": "https://cie.iiit.ac.in/startup-portfolio/", "mode": "links"},
    {"key": "nsrcel", "url": "https://nsrcel.org/startups/", "mode": "links"},
    {"key": "better-capital", "url": "https://www.bettercapital.vc/portfolio", "mode": "links"},
    {"key": "india-quotient", "url": "https://www.indiaquotient.in/portfolio", "mode": "links"},
    {"key": "chiratae", "url": "https://www.chiratae.com/companies/", "mode": "links"},
    {"key": "kalaari", "url": "https://www.kalaari.com/portfolio/", "mode": "pages", "prefix": "/portfolio/"},
    {"key": "stellaris", "url": "https://www.stellarisvp.com/portfolio", "mode": "pages", "prefix": "/portfolio/"},
    {"key": "elevation", "url": "https://www.elevationcapital.com/portfolio", "mode": "pages", "prefix": "/portfolio/"},
    {"key": "z47", "url": "https://www.z47.com/portfolio", "mode": "pages", "prefix": "/portfolio/"},
    {"key": "surge", "url": "https://surgeahead.com/companies", "mode": "pages", "prefix": "/companies/"},
    {"key": "accel", "url": "https://www.accel.com/relationships", "mode": "pages", "prefix": "/companies/"},
    {"key": "antler", "url": "https://www.antler.co/portfolio", "mode": "links"},
    {"key": "lightspeed", "url": "https://lsvp.com/portfolio/", "mode": "pages", "prefix": "/company/"},
    # tried 2026-10-14 and not usable without a browser or a login: Startup India search
    # (login wall, its API 403s), T-Hub (no public list), Blume / Together Fund (names
    # only, no links), 100X.VC / Speciale (404), Arka / Venture Highway (not answering),
    # YourNest (empty shell), Karnataka Startup Cell (not answering)
]
K_PF_QUEUE = "india_src:portfolio_queue"     # [{"src", "name", "page"|"website"}]
K_PF_DONE = "india_src:portfolio_indexed"    # portfolio keys already turned into queue items

_NOT_COMPANY_HOST = re.compile(
    r"(?i)(linkedin|twitter|x\.com|facebook|instagram|youtube|youtu\.be|medium\.com|crunchbase|apple\.com|"
    r"google|wix|webflow|wordpress|gstatic|cloudflare|jquery|fonts|cdn|hubspot|typeform|calendly|vimeo|"
    r"github\.com|tracxn|jsdelivr|unpkg|schema\.org|w3\.org|gravatar|spotify|bit\.ly|substack|notion|"
    r"airtable|figma|mailchimp|zoom\.us|whatsapp|wa\.me|cookiebot|onetrust|recaptcha|sentry|segment|"
    r"intercom|hotjar|clarity|doubleclick|googletagmanager|addtoany|sharethis|pinterest|reddit|tiktok|"
    r"telegram|threads\.net|glassdoor|wellfound|angel\.co|ycombinator|economictimes|yourstory|inc42|"
    r"techcrunch|forbes|bloomberg|reuters|livemint|moneycontrol|business-standard|thehindu|hindustantimes|"
    r"vccircle|entrackr|wikipedia|intellimize|lnkd\.in|tinyurl|goo\.gl|sanity\.io|peppersquare|"
    r"everything\.design|miranj|devc\.com|faction\.vc|fiscloudservices|maps\.app|issuu|scribd|slideshare|"
    r"podcasts|soundcloud|amazon\.|flipkart\.com|cdninstagram|typekit|vimeocdn|website-files|bootstrapcdn|"
    r"cloudfront|amazonaws|azureedge|shopify|squarespace|framer|zohopublic|forms\.|docs\.)")


def _links(html: str, base: str) -> list[tuple[str, str]]:
    """(absolute url, anchor text) for every link on a page."""
    out = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html or "", re.I | re.S):
        href = urljoin(base, unescape(m.group(1)).strip())
        if href.startswith("http"):
            out.append((href, re.sub(r"\s+", " ", unescape(_TAG.sub(" ", m.group(2)))).strip()))
    return out


def portfolio_items(src: dict, html: str) -> list[dict]:
    """Queue items off a portfolio index: {'src', 'website'} (links mode) or {'src',
    'name', 'page'} (pages mode)."""
    base = src["url"]
    own = _host(base).split(".")[-2] if "." in _host(base) else _host(base)
    items: list[dict] = []
    seen: set[str] = set()
    for href, text in _links(html, base):
        host = _host(href)
        if src["mode"] == "links":
            if own in host or _NOT_COMPANY_HOST.search(host) or admission.bogus_host(host):
                continue
            reg = admission.registrable(host) or host
            if reg in seen:
                continue
            seen.add(reg)
            items.append({"src": src["key"], "website": f"https://{host}", "hint": text[:80] or None})
        else:
            path = urlparse(href).path
            if own not in host or not path.startswith(src["prefix"]):
                continue
            slug = path[len(src["prefix"]):].strip("/")
            if not slug or "/" in slug or slug in seen or slug in ("page", "category", "tag"):
                continue
            seen.add(slug)
            items.append({"src": src["key"], "name": slug.replace("-", " ").title(), "page": href})
    return items


def website_from_page(html: str, base: str, name: str) -> str | None:
    """A portfolio company page's link to the company's own site: an outbound link whose
    host carries the company's name."""
    own = _host(base)
    for href, _text in _links(html, base):
        host = _host(href)
        if not host or host == own or own.split(".")[-2] in host or _NOT_COMPANY_HOST.search(host):
            continue
        if admission.host_fits_name(name, host):
            return f"https://{host}"
    return None


def site_name(site: dict, website: str, hint: str | None = None) -> str | None:
    """The company's name off its own homepage: og:site_name, else a title segment that
    the domain carries, else the anchor text that linked it."""
    host = _host(website)
    cands = []
    if site.get("name"):
        cands.append(site["name"])
    t = site.get("title") or ""
    cands += [p.strip() for p in re.split(r"\s+[|\-–—:·•]\s+|\s*[|–—·•]\s*", t) if p.strip()]
    if hint:
        cands.append(hint)
    for c in cands:
        c = re.sub(r"(?i)^(home|welcome to)\s*[-|:]?\s*", "", c).strip()
        if 2 <= len(c) <= 40 and admission.host_fits_name(c, host):
            return c
    return None


def _pf_index(session, run, kind: str) -> list[dict]:
    """Turn the next not-yet-indexed portfolio page into queue items."""
    indexed = set(_json(session, K_PF_DONE, []))
    for src in PORTFOLIOS:
        if src["key"] in indexed:
            continue
        try:
            r = get(src["url"])
        except Cooled as e:
            run.stat(kind).setdefault("problems", []).append(f"{src['key']}: {e}")
            indexed.add(src["key"])
            _put(session, K_PF_DONE, sorted(indexed))
            continue
        indexed.add(src["key"])
        _put(session, K_PF_DONE, sorted(indexed))
        items = portfolio_items(src, r.text) if r is not None else []
        run.stat(kind).setdefault("indexed", {})[src["key"]] = len(items)
        if items:
            return items
    return []


def from_portfolio(run, *, companies: int = 20, kind: str = "portfolio") -> int:
    """Resolve up to `companies` portfolio entries: website (off the company's portfolio
    page when needed), then the company's own site must name Bengaluru / Hyderabad."""
    with get_session() as session:
        queue: list[dict] = _json(session, K_PF_QUEUE, [])
        seen = _seen(session, kind)
    n = 0
    done: list[str] = []
    try:
        while n < companies and not run.full:
            if not queue:
                with get_session() as session:
                    queue = _pf_index(session, run, kind)
                if not queue:
                    break
                continue
            it = queue.pop(0)
            name = it.get("name")
            website = it.get("website")
            key = f"pf:{_host(website) if website else it.get('page')}"
            if key in seen:
                continue
            done.append(key)
            n += 1
            if not website:
                with get_session() as session:
                    _row, has_city = _known(session, name)
                if has_city:
                    _skip(run, kind, "already known")
                    continue
                try:
                    r = get(it["page"])
                except Cooled:
                    _skip(run, kind, "portfolio page rate-limited us")
                    continue
                website = website_from_page(r.text, it["page"], name) if r is not None else None
                if not website:
                    _skip(run, kind, "no website on its portfolio page")
                    continue
            with get_session() as session:
                _row, has_city = _known(session, name or _host(website).split(".")[0], admission.host_of(website))
            if has_city:
                _skip(run, kind, "already known")
                continue
            site = own_site(website, name=name)
            if not site.get("final"):
                _skip(run, kind, site.get("why") or "website unreachable")
                continue
            name = name if name and admission.host_fits_name(name, _host(site["final"])) else \
                site_name(site, site["final"], it.get("hint")) or name
            if not name:
                _skip(run, kind, "no company name on its own site")
                continue
            if not site.get("ok"):
                _skip(run, kind, site.get("why") or "its own site names neither Bengaluru nor Hyderabad")
                continue
            desc = site.get("description")
            why = admission.body_shop(name, desc) or (AGENCY.search(desc or "") and "agency / IT services / staffing")
            if why:
                _skip(run, kind, why.split(" (")[0])
                continue
            from jobhunter.india import Cand

            quote = site["quote"]
            admit(run, Cand(name=name, text=f"{quote} {desc or ''}"[:1200], url=site["url"], kind=kind,
                            headline=False, website=site["final"], website_is_link=False, description=desc,
                            quote=quote, own_page=True, check_age=False))
            run.stat(kind).setdefault("from", {}).setdefault(it["src"], 0)
            run.stat(kind)["from"][it["src"]] += 1
    finally:
        with get_session() as session:
            _put(session, K_PF_QUEUE, queue)
            _mark(session, done, kind)
    return n


# ================================================================== source: Wellfound role × city pages

WF = "https://wellfound.com"
WF_CITIES = {"Bengaluru": "bangalore", "Hyderabad": "hyderabad"}
WF_ROLES = ["software-engineer", "backend-engineer", "full-stack-engineer", "frontend-engineer",
            "machine-learning-engineer", "data-scientist", "devops-engineer", "mobile-engineer",
            "data-engineer", "artificial-intelligence-engineer", "python-developer", "android-developer"]
K_WF_CURSOR = "india_src:wellfound_cursor"
_WF_CARD = re.compile(r"\]\((https://wellfound\.com/company/([a-z0-9-]+))\)")


def wf_pages() -> list[str]:
    out = [f"{WF}/location/{c}" for c in WF_CITIES.values()]
    return out + [f"{WF}/role/l/{r}/{c}" for c in WF_CITIES.values() for r in WF_ROLES]


def parse_wellfound(md: str) -> list[dict]:
    """Company cards off a Wellfound list page (Jina Markdown): slug, name, blurb, size,
    and the city lines of its roles ("In office • Bengaluru")."""
    out: list[dict] = []
    parts = _WF_CARD.split(md or "")
    # parts: [pre, url, slug, body, url, slug, body, ...]
    for i in range(1, len(parts) - 2, 3):
        url, slug, body = parts[i], parts[i + 1], parts[i + 2]
        m = re.search(r"Image \d+: (.+?) company logo", parts[i - 1][-400:] if i - 1 >= 0 else "")
        name = (m.group(1).strip(" |") if m else None)
        if not name:
            continue
        lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        blurb = lines[0] if lines else ""
        size = re.search(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s+Employees|(\d[\d,]*)\+\s+Employees", blurb)
        where = [ln for ln in lines if "•" in ln and admission.india_city(ln) and re.search(r"(?i)office|remote|onsite|hybrid", ln)]
        pay = [ln for ln in lines if re.search(r"₹\s?\d", ln)]
        out.append({"slug": slug, "url": url, "name": name, "blurb": re.sub(r"\d[\d,]*\s*-\s*\d[\d,]*\s+Employees|\d[\d,]*\+\s+Employees", "", blurb).strip(),
                    "headcount": int((size.group(1) or size.group(3)).replace(",", "")) if size else None,
                    "where": where, "pay": pay[:3]})
    return out


def guess_sites(name: str, slug: str) -> list[str]:
    """Domains a company called `name` (Wellfound slug `slug`) is likely to own."""
    key = admission.name_key(name) or ""
    full = re.sub(r"[^a-z0-9]", "", admission._ascii(name).lower())
    parts = slug.split("-")
    out = []
    if len(parts) >= 2 and parts[-1] in ("ai", "io", "in", "co", "app", "dev", "tech", "so"):
        out.append(f"{''.join(parts[:-1])}.{parts[-1]}")
    for stem in dict.fromkeys([full, key, slug.replace("-", "")]):
        if len(stem) >= 3:
            out += [f"{stem}.com", f"{stem}.ai", f"{stem}.in", f"{stem}.io", f"{stem}.co"]
    return list(dict.fromkeys(out))[:7]


def _jina(url: str) -> str | None:
    from jobhunter.research import web

    try:
        r = get(web.JINA + url, gap=6.0, accept="text/plain", timeout=60, headers={"User-Agent": web.JINA_UA})
    except Cooled:
        raise
    if r is None:
        return None
    body = r.text or ""
    i = body.find("Markdown Content:")
    return body[i + len("Markdown Content:"):] if i >= 0 else body


def from_wellfound(run, *, pages: int = 1, kind: str = "wellfound") -> int:
    with get_session() as session:
        cur = int(_json(session, K_WF_CURSOR, 0))
    allp = wf_pages()
    done = 0
    while done < pages and not run.full and cur < len(allp):
        url = allp[cur]
        try:
            md = _jina(url)
        except Cooled as e:
            run.stat(kind).setdefault("problems", []).append(f"jina: {e}")
            break
        done += 1
        cur += 1
        if not md or guardrails.bot_wall(md[:2000]):
            run.stat(kind).setdefault("problems", []).append(f"{url}: empty or bot wall")
            continue
        for card in parse_wellfound(md):
            if run.full:
                break
            name = card["name"]
            if not card["where"]:
                _skip(run, kind, "no role in Bengaluru / Hyderabad")
                continue
            if card["headcount"] and card["headcount"] >= LARGE:
                _skip(run, kind, f"large enterprise ({LARGE:,}+ employees)")
                continue
            why = admission.body_shop(name, card["blurb"]) or (AGENCY.search(card["blurb"]) and "agency / IT services / staffing")
            if why:
                _skip(run, kind, why.split(" (")[0])
                continue
            with get_session() as session:
                _row, has_city = _known(session, name)
            if has_city:
                _skip(run, kind, "already known")
                continue
            website = None
            for dom in guess_sites(name, card["slug"]):
                s = own_site(f"https://{dom}", name=name, pages=1)
                final = s.get("final")
                blob = " ".join(filter(None, [s.get("title"), s.get("name"), s.get("description")]))
                if final and admission.host_fits_name(name, _host(final)) and guardrails.names_company(blob, name):
                    website = final
                    break
            if not website:
                _skip(run, kind, "no website found for it")
                continue
            from jobhunter.india import Cand

            quote = f"{name} is hiring on Wellfound — {card['where'][0]}"
            admit(run, Cand(name=name, text=f"{quote}. {card['blurb']}", url=card["url"], kind=kind, headline=False,
                            website=website, website_is_link=False, quote=quote, own_page=True, check_age=False))
        with get_session() as session:
            _put(session, K_WF_CURSOR, cur)
    return done


__all__ = ["from_github", "from_cutshort", "from_portfolio", "from_wellfound", "parse_gh_org", "gh_junk",
           "parse_cutshort", "cs_gate", "portfolio_items", "website_from_page", "parse_wellfound",
           "own_site", "city_from_text", "page_text", "site_name", "guess_sites", "PORTFOLIOS"]
