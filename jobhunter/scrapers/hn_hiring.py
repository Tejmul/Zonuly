"""Hacker News "Ask HN: Who is hiring?" — monthly threads via the Algolia API.

Top-level comments are individual job posts, conventionally formatted as
`Company | Role | Location | REMOTE | Full-time | salary | url`.
"""

from __future__ import annotations

import logging
import re

import httpx

from jobhunter.scrapers.base import RawJob, gather_limited, get_json, html_to_text, looks_remote, parse_ts

log = logging.getLogger(__name__)

SOURCE = "hn"
SEARCH = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&query=Ask%20HN%3A%20Who%20is%20hiring%3F&hitsPerPage=8"
)
ITEM = "https://hn.algolia.com/api/v1/items/{id}"
THREADS_TO_READ = 2  # current + previous month

ROLE_HINTS = (
    "engineer", "developer", "sde", "swe", "scientist", "ml ", "ai ", "llm",
    "machine learning", "backend", "back-end", "full stack", "fullstack",
    "python", "research", "infrastructure", "platform", "founding",
)

_SPLIT = re.compile(r"\s*\|\s*")
_YC_TAG = re.compile(r"\s*\((?:YC\s*[SWFX]?\d{2}|yc\s*[sw]\d{2})\)\s*", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_SALARY_RE = re.compile(
    r"(?:\$|₹|€|£|USD|INR|EUR|GBP)\s?\d[\d,.]*\s?(?:k|K|L|LPA|lakh|lpa|million|m)?"
    r"(?:\s*(?:-|–|to)\s*(?:\$|₹|€|£)?\s?\d[\d,.]*\s?(?:k|K|L|LPA|lakh|lpa|million|m)?)?"
)


# ---------------------------------------------------------------- company names

# HN posts are free text, and only some follow the "Company | Role | Location"
# convention. The old fallback stored the first 60 characters of the post as the
# company name, which produced rows like "At Tether (https://tether.io/) we're hiring!
# We envision a w" — unusable in a list, and unusable as a search key for contacts.
# So the name is extracted, and a post we cannot name is dropped rather than stored
# under a sentence.

_LEAD_NOISE = re.compile(
    r"^(?:hey|hi|hello)\s+hn[!,.:\s]+|^(?:at|company:|hiring (?:for )?(?:several )?"
    r"(?:roles\s+)?at|we(?:'|’)?re hiring at|join)\s+",
    re.I,
)
# a title is not a company: "Senior Python Backend Engineer | REMOTE" names no employer
_LOOKS_LIKE_ROLE = re.compile(
    r"\b(engineer|developer|scientist|manager|designer|intern|analyst|architect|"
    r"consultant|lead|director|founding|senior|junior|staff|principal|remote|"
    r"full[- ]?time|part[- ]?time|contract|freelancer|hiring|looking to hire)\b",
    re.I,
)
# hosts that belong to an ATS or a link shortener, not to the company itself
_NOT_A_COMPANY_HOST = re.compile(
    r"(^|\.)(grnh\.se|greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|dover\.com|"
    r"breezy\.hr|recruitee\.com|bamboohr\.com|notion\.site|docs\.google\.com|"
    r"producthunt\.com|linkedin\.com|blob\.core\.windows\.net|bit\.ly|tinyurl\.com)$",
    re.I,
)

_NAME_PATTERNS = [
    # "<role> at <Employer> in <city>" — the employer follows the preposition, but only
    # when what precedes it is actually a role. Without that guard "…learning videos for
    # Education. Our small team…" reads "Education" as the employer.
    # The flag is scoped to the role words on purpose: the NAME must stay case-sensitive,
    # or [A-Z] matches lowercase and "NLnet foundation in" is read as the employer.
    re.compile(r"^[^|\n]{0,45}?\b(?i:engineer|developer|scientist|designer|roles?|"
               r"positions?|jobs?)\b[^|\n]{0,20}?\s+(?i:at|with)\s+"
               r"(?P<name>[A-Z][\w.&\'-]{1,30}(?:\s+[A-Z][\w.&\'-]{1,30}){0,2})\b"),
    # "Air Space Intelligence (https://…) is hiring"  /  "Fathom – AI Notetaker (fathom.ai)"
    re.compile(r"^(?P<name>[^(|\n]{2,45}?)\s*\(\s*(?:https?://|www\.|[a-z0-9-]+\.[a-z]{2,})"),
    # "Hellotext — Senior Full-Stack …"  /  "PrairieLearn (Remote US) — …"
    re.compile(r"^(?P<name>[^—–|\n]{2,45}?)\s*[—–]"),
    # "Tether we're hiring" / "RedLine Solutions is hiring"
    re.compile(r"^(?P<name>[^,|\n]{2,45}?)\s+(?:is|are|we(?:'|’)?re)\s+hiring", re.I),
    # "Company | Role | Location" — only when the first field is not itself a role
    re.compile(r"^(?P<name>[^|\n]{2,45}?)\s*\|"),
    # "PostSilo is a privacy-first company building…"
    re.compile(r"^(?P<name>[A-Z][\w.&'-]{1,30}(?:\s+[A-Z][\w.&'-]{1,30}){0,3})\s+(?:is|builds|makes|provides)\s"),
]

# Last resort before the domain: the run of capitalised words the line opens with.
# Catches "Jukebox Health", "StudyTurtle, an edutainment brand", "Fathom – AI Notetaker".
_LEADING_NAME = re.compile(r"^(?P<name>[A-Z][\w.&\'-]{1,30}(?:\s+[A-Z0-9][\w.&\'-]{1,30}){0,3})")
# a header that opens with a bare domain: "fractile.ai - AI hardware, backed by …"
_LEADING_DOMAIN = re.compile(r"^(?P<host>[a-z0-9][a-z0-9-]{1,40}\.[a-z]{2,10})\b", re.I)


# "careers.reef.pl" is Reef, not Careers — the interesting label is neither the first
# nor the last, so the hosting subdomains come off before anything else is decided.
_SUBDOMAIN = re.compile(r"^(?:www|careers?|jobs|apply|boards?|hire|hiring|work|join|app|about)\.", re.I)
# two-label public suffixes, where the registrable name is the third label from the right
_TWO_PART_TLD = re.compile(r"\.(?:co|com|org|net|ac|gov|edu)\.[a-z]{2}$", re.I)


def _from_domain(url: str | None) -> str | None:
    """"https://airspace-intelligence.com" -> "Airspace Intelligence". Last resort."""
    if not url:
        return None
    host = re.sub(r"^https?://", "", url).split("/")[0].split("?")[0].lower()
    for _ in range(3):                       # careers.eu.acme.com -> acme.com
        stripped = _SUBDOMAIN.sub("", host, count=1)
        if stripped == host:
            break
        host = stripped
    if not host or _NOT_A_COMPANY_HOST.search(host):
        return None
    labels = host.split(".")
    stem = labels[-3] if (_TWO_PART_TLD.search(host) and len(labels) >= 3) else (
        labels[-2] if len(labels) >= 2 else labels[0]
    )
    if len(stem) < 2 or _LOOKS_LIKE_ROLE.search(stem):
        return None
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", stem) if w)[:45] or None


# A capitalised first word is not a name: "We're building…", "There is a massive…".
_NOT_A_NAME = {
    "we", "i", "there", "our", "the", "this", "that", "they", "it", "you", "your",
    "hi", "hey", "hello", "join", "come", "work", "looking", "seeking", "about",
    "rust", "python", "golang", "java", "react", "senior", "junior",
}
# "Early-stage health & wellness startup" describes a company without naming one
_DESCRIBES_NOT_NAMES = re.compile(
    r"\b(startup|company|business|agency|firm|team|foundation|nonprofit|consultancy)$", re.I
)


def _valid_name(name: str | None) -> str | None:
    if not name:
        return None
    # "Karhuno Group / https://DearHiringManager.io" — the name is the part before the link
    name = re.split(r"\s*[/|]\s*(?=https?://|www\.)", name)[0]
    name = _YC_TAG.sub("", name).strip(" -–—|,:.\"'\t")
    if name.lower() in _NOT_A_NAME or _DESCRIBES_NOT_NAMES.search(name):
        return None
    if not (2 <= len(name) <= 45):
        return None
    if _LOOKS_LIKE_ROLE.search(name) or not re.search(r"[A-Za-z]", name):
        return None
    if name.lower().startswith(("http", "www.")):
        return None
    return name


def company_name(header: str, apply_url: str | None = None) -> str | None:
    """The employer behind an HN hiring post, or None when the post never names one."""
    head = _LEAD_NOISE.sub("", (header or "").strip(), count=1)
    m = _LEADING_DOMAIN.match(head)
    if m:
        name = _valid_name(_from_domain(m.group("host")))
        if name:
            return name
    for pattern in _NAME_PATTERNS:
        m = pattern.match(head)
        if m:
            # "Fathom – AI Notetaker" is Fathom; the tagline after the dash is not part
            # of the name, and neither is a trailing parenthetical.
            candidate = re.split(r"\s+[—–]\s+|\s+-\s+", m.group("name"))[0]
            name = _valid_name(candidate) or _valid_name(m.group("name"))
            if name:
                return name
    m = _LEADING_NAME.match(head)
    if m:
        # Guard the loosest pattern against descriptions: "Early-stage health & wellness
        # startup" opens with a capital but names nobody, and only the whole clause —
        # not the captured word — shows that.
        clause = re.split(r"[,;(|]|\s+[—–]\s+", head, maxsplit=1)[0].strip()
        if not _DESCRIBES_NOT_NAMES.search(clause):
            name = _valid_name(m.group("name"))
            if name:
                return name
    return _valid_name(_from_domain(apply_url))


def _parse_comment(c: dict) -> RawJob | None:
    text = html_to_text(c.get("text"))
    if not text or len(text) < 60:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    header = lines[0]
    parts = [p.strip() for p in _SPLIT.split(header) if p.strip()]

    urls_early = _URL_RE.findall(text)
    apply_early = next((u for u in urls_early if not u.startswith("https://news.ycombinator.com")), None)

    if len(parts) >= 2:
        title = parts[1]
        rest = " | ".join(parts[2:])
    else:
        title = next((ln for ln in lines[1:4] if any(h in ln.lower() for h in ROLE_HINTS)), "")
        rest = ""
        if not title:
            return None

    # The employer is extracted from the header (or, failing that, the apply link's
    # domain). A post that never names one is dropped: a row called "I am looking to
    # hire a freelancer for a math research website" is worse than no row.
    company = company_name(header, apply_early)
    if not company:
        return None

    blob = f"{title} {rest} {text[:600]}".lower()
    if not any(h in blob for h in ROLE_HINTS):
        return None

    urls = _URL_RE.findall(text)
    apply_url = next((u for u in urls if not u.startswith("https://news.ycombinator.com")), None)
    sal = _SALARY_RE.search(rest) or _SALARY_RE.search(text[:1200])

    cid = c.get("id") or c.get("objectID")
    return RawJob(
        company_name=company,
        title=title[:140] or "Engineer",
        url=f"https://news.ycombinator.com/item?id={cid}",
        source=SOURCE,
        location=parts[2] if len(parts) > 2 else None,
        remote=looks_remote(rest, text[:400]),
        description=text,
        posted_at=parse_ts(c.get("created_at")),
        salary_raw=sal.group(0) if sal else None,
        company_website=apply_url,
        # the hiring post itself: who said it, where — so "where did you get this?"
        # is answered with a name and a link, never with "the pipeline"
        extra={"apply_url": apply_url, "posted_by": c.get("author"), "post_source": "hn"},
    )


async def _fetch_thread(http: httpx.AsyncClient, story_id: str) -> list[RawJob]:
    data = await get_json(http, ITEM.format(id=story_id))
    if not isinstance(data, dict):
        return []
    out: list[RawJob] = []
    for child in data.get("children") or []:
        if not isinstance(child, dict) or child.get("author") is None:
            continue  # deleted comment
        job = _parse_comment(child)
        if job:
            out.append(job)
    log.info("hn: thread %s -> %d posts", story_id, len(out))
    return out


async def fetch(http: httpx.AsyncClient, companies: list[dict] | None = None) -> list[RawJob]:
    search = await get_json(http, SEARCH)
    if not isinstance(search, dict):
        log.warning("hn: Algolia search failed")
        return []
    hits = [
        h for h in search.get("hits") or []
        if "who is hiring" in (h.get("title") or "").lower()
    ][:THREADS_TO_READ]
    if not hits:
        return []
    results = await gather_limited([_fetch_thread(http, h["objectID"]) for h in hits], limit=2)
    jobs = [j for r in results if r for j in r]
    log.info("hn: %d jobs from %d threads", len(jobs), len(hits))
    return jobs
