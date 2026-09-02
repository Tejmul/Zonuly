"""Composed research tasks — the jobs the JobHunter agent actually asks for.

Everything below is assembled from the single-channel modules; nothing here talks
to a network directly. Two rules hold throughout:

  * **Extract, never invent.** A funding stage or an open role appears only when
    some fetched text said so, and the sentence that said it is carried along as
    `evidence_quote`. Absent evidence, the field stays null.
  * **No downstream coupling.** These functions return records. Scoring them
    against the resume, storing them, writing to the graph or drafting an e-mail
    stays in matcher / kg / outreach, where it already lives.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from jobhunter.research import github as gh
from jobhunter.research import reddit as rd
from jobhunter.research import web
from jobhunter.research import youtube as yt
from jobhunter.research.models import CompanyResearch, FundingSignal, RepoHit

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ extraction

_STAGE_RE = re.compile(
    r"\b(pre-seed|pre seed|seed(?:\s+extension)?|series\s+[a-h]|angel|bridge|"
    r"growth|grant|ipo|debt|strategic)\s*(?:round|funding|financing)?\b",
    re.I,
)
_AMOUNT_RE = re.compile(
    r"([$€£]|\bUSD\s?|\bAED\s?|\bGBP\s?|\bEUR\s?)\s?"
    r"(\d{1,4}(?:[.,]\d{1,3})?)\s*"
    r"(million|billion|m\b|bn\b|b\b|k\b)?",
    re.I,
)
_RAISE_VERB = (
    r"raises?|raised|secur(?:es|ed)|lands?|landed|closes?|closed|nabs?|bags?|"
    r"pulls? in|picks? up|announces?|announced|launch(?:es|ed)? with|"
    r"emerges? from stealth with|comes? out of stealth with|banks?"
)
_HEADLINE_RE = re.compile(rf"^(?P<name>.{{2,80}}?)\s+(?:{_RAISE_VERB})\b", re.I)
_INVESTOR_RE = re.compile(
    # a bare "from" is not enough — "researchers from MIT" is not a funding round,
    # so the loose form has to trail a raise verb in the same sentence
    r"\b(?:led by|backed by|with participation from|investors? includ(?:e|ing)|"
    r"(?:rais(?:e|es|ed)|secur(?:e|es|ed)|clos(?:e|es|ed)|bank(?:s|ed)?|"
    r"launch(?:es|ed)?|emerg(?:es|ed)|fund(?:s|ed|ing))\b[^.]{0,60}?\bfrom)\s+"
    r"(?P<who>[A-Z][A-Za-z0-9&.\-']*(?:\s+[A-Z][A-Za-z0-9&.\-']*){0,3}"
    r"(?:\s*(?:,|,?\s+and)\s*[A-Z][A-Za-z0-9&.\-']*(?:\s+[A-Z][A-Za-z0-9&.\-']*){0,3}){0,5})"
)

# capitalised words the investor pattern can swallow from the start of the next sentence
_NOT_AN_INVESTOR = {
    "this", "that", "the", "it", "they", "we", "he", "she", "there", "his", "her",
    "their", "other", "others", "existing", "new", "several", "both",
}

_MULT = {"billion": 1000.0, "bn": 1000.0, "b": 1000.0, "million": 1.0, "m": 1.0, "k": 0.001}
# rough, and only used to sort/filter — never presented as an exact figure
_TO_USD = {"$": 1.0, "usd": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27, "aed": 0.27}

# words that sit in front of a company name in a headline and are not part of it
_LEAD_NOISE = re.compile(
    r"^(?:exclusive|breaking|report|update|the|a|an|uk|us|usa|uae|dubai|abu dhabi|indian|"
    r"israeli|british|american|emirati|london|nyc|sf|bay area|gcc|mena|europe(?:an)?|"
    r"ai|genai|ml|deeptech|deep tech|fintech|healthtech|climate|robotics|defense|defence|"
    r"[a-z-]+-(?:incubated|backed|founded|based)|startup|start-up|company|firm|scaleup|"
    r"y combinator|yc)\s+",
    re.I,
)
# "Exclusive: Aslan raises …" — a wire-service prefix, not part of the name
_WIRE_PREFIX = re.compile(r"^[A-Za-z][A-Za-z ]{0,14}:\s+")
# "UAE's Stellaria secures …" — the possessive belongs to the geography, not the name
_POSSESSIVE = re.compile(r"^[A-Z][\w.&-]*['’]s\s+")


def extract_funding(text: str, *, url: str | None = None) -> FundingSignal | None:
    """Pull a funding claim out of prose. Returns None when the text does not say."""
    if not text:
        return None
    stage_m = _STAGE_RE.search(text)
    amount_m = _AMOUNT_RE.search(text)
    if not stage_m and not amount_m:
        return None

    amount_raw = amount_usd_m = None
    if amount_m:
        symbol, number, unit = amount_m.group(1), amount_m.group(2), (amount_m.group(3) or "")
        amount_raw = amount_m.group(0).strip()
        try:
            value = float(number.replace(",", ""))
            value *= _MULT.get(unit.lower().strip(), 1.0 if unit else 1.0)
            amount_usd_m = round(value * _TO_USD.get(symbol.strip().lower(), 1.0), 2)
        except ValueError:
            amount_usd_m = None

    investors: list[str] = []
    seen: set[str] = set()
    for m in _INVESTOR_RE.finditer(text[:2000]):
        # the capture can run past the end of the sentence — a newline or a full
        # stop followed by a space ends it ("… led by Y Combinator. This round …")
        who = re.split(r"\.\s", m.group("who").splitlines()[0])[0]
        for name in re.split(r"\s*,\s*|\s+and\s+", who):
            # "…, Benchmark, and Zscaler" leaves a leading "and" on the last piece
            name = re.sub(r"^and\s+", "", name.strip(" .,;:"), flags=re.I).strip()
            key = name.lower()
            if 2 <= len(name) <= 40 and key not in seen and key not in _NOT_AN_INVESTOR:
                seen.add(key)
                investors.append(name)
    del investors[6:]

    anchor = (stage_m or amount_m).start()
    start = max(0, text.rfind(".", 0, anchor) + 1)
    end = text.find(".", anchor)
    quote = text[start : end + 1 if end != -1 else min(len(text), anchor + 300)].strip()

    return FundingSignal(
        stage=stage_m.group(1).lower().replace("  ", " ") if stage_m else None,
        amount_raw=amount_raw,
        amount_usd_m=amount_usd_m,
        investors=investors,
        evidence_url=url,
        evidence_quote=re.sub(r"\s+", " ", quote)[:400] or None,
    )


def extract_company_name(headline: str) -> str | None:
    """The company out of a funding headline, or None if it does not read like one."""
    if not headline:
        return None
    head = re.split(r"\s+[|–—]\s+", headline)[0].strip()
    head = _WIRE_PREFIX.sub("", head, count=1)
    m = _HEADLINE_RE.match(head)
    if not m:
        return None
    name = m.group("name").strip()
    for _ in range(4):  # peel "GCC AI startup ...", "Sequoia-incubated ...", "UAE's ..."
        stripped = _POSSESSIVE.sub("", _LEAD_NOISE.sub("", name, count=1), count=1)
        if stripped == name:
            break
        name = stripped
    name = name.strip(" ,.'\"-")
    if not name or len(name) > 45 or " " in name and len(name.split()) > 5:
        return None
    if name.lower() in {"it", "they", "we", "he", "she", "this", "that", "who", "which"}:
        return None
    return name


_ATS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}


def detect_ats(url: str) -> tuple[str | None, str | None]:
    """(ats, slug) if this URL is a known board — the hand-off to Zonuly's scrapers."""
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None, None
    ats = _ATS.get((parsed.hostname or "").lower())
    if not ats:
        return None, None
    parts = [p for p in (parsed.path or "").split("/") if p]
    return ats, (parts[0] if parts else None)


_HIRING_RE = re.compile(
    r"(?:\b(?:we(?:'re| are)|is|are)\s+hiring\b|\bnow hiring\b|\bjoin (?:our|the) team\b|"
    r"\bopen (?:roles|positions)\b|\bwe(?:'re| are) (?:growing|expanding) the team\b|"
    # deliberately no bare "careers": on most pages that word is navigation, and a
    # nav link is not evidence that anyone is hiring
    r"\bhiring (?:engineers|for|across)\b|\bjob openings?\b|\bview (?:all )?(?:open )?roles\b)",
    re.I,
)
_ROLE_RE = re.compile(
    r"\b((?:senior |staff |lead |principal |founding |junior |graduate )?"
    r"(?:ai|ml|machine learning|llm|genai|applied ai|research|software|backend|full[- ]stack|"
    r"frontend|platform|infrastructure|data|devops|forward deployed)\s+engineer"
    r"|software development engineer|sde\s*[i1-3]?|research scientist|data scientist)\b",
    re.I,
)


def hiring_signals(text: str, *, limit: int = 6) -> tuple[list[str], list[str]]:
    """(quoted hiring sentences, role titles mentioned). Both quoted from the text."""
    if not text:
        return [], []
    signals: list[str] = []
    for m in _HIRING_RE.finditer(text):
        start = max(0, text.rfind(".", 0, m.start()) + 1)
        end = text.find(".", m.end())
        sentence = re.sub(r"\s+", " ", text[start : end + 1 if end != -1 else m.end() + 160]).strip()
        if 15 < len(sentence) < 320 and sentence not in signals:
            signals.append(sentence)
        if len(signals) >= limit:
            break
    roles: list[str] = []
    for m in _ROLE_RE.finditer(text):
        role = " ".join(w.capitalize() for w in m.group(1).split())
        if role not in roles:
            roles.append(role)
        if len(roles) >= limit:
            break
    return signals, roles


_CAREERS_HINT = re.compile(r"/(careers?|jobs|join-?us|work-with-us|open-roles|hiring)\b", re.I)
_NOT_A_SITE = re.compile(
    r"(techcrunch|crunchbase|linkedin|twitter|x\.com|facebook|bloomberg|reuters|forbes|"
    r"businessinsider|medium|substack|youtube|wikipedia|pitchbook|dealroom|prnewswire|"
    r"businesswire|yahoo|cnbc|gulfnews|thenationalnews|wamda|menabytes|entrackr|inc42|"
    r"economictimes|livemint|sifted|eu-startups|tech\.eu|axios|theinformation)\.",
    re.I,
)


def looks_official(url: str, company: str) -> bool:
    """Is this URL plausibly the company's own site rather than press coverage?"""
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    if not host or _NOT_A_SITE.search(host):
        return False
    stem = "".join(ch for ch in company.lower() if ch.isalnum())
    return bool(stem) and stem[:12] in host.replace("-", "").replace(".", "")


# ------------------------------------------------------------------ company


def company(
    name: str,
    *,
    website: str | None = None,
    depth: str = "standard",
    fresh: bool = False,
) -> dict:
    """Everything the acquisition layer can find about one company.

    depth: "quick"    — one web search, nothing fetched
           "standard" — + resolve the site, read it and its careers page, + GitHub
           "deep"     — + Reddit chatter and founder videos
    """
    name = (name or "").strip()
    if not name:
        return {"error": "empty company name"}

    out = CompanyResearch(name=name, website=website, researched_at=datetime.now(timezone.utc))
    used: list[str] = []

    hits = web.search(f"{name} company official website what it builds", limit=6, fresh=fresh)
    if hits.get("backend"):
        used.append(f"web_search:{hits['backend']}")
    results = hits.get("results") or []
    for r in results:
        out.sources.append(r["url"])
        if not out.website and looks_official(r["url"], name):
            parsed = urlparse(r["url"])
            out.website = f"{parsed.scheme}://{parsed.hostname}"
            out.domain = (parsed.hostname or "").removeprefix("www.")
        if not out.one_liner and r.get("snippet"):
            out.one_liner = re.sub(r"\s+", " ", r["snippet"])[:300]
        ats, slug = detect_ats(r["url"])
        if ats and slug:
            out.careers_url = r["url"]
            out.hiring_signals.append(f"{ats} board: {slug}")
        elif not out.careers_url and _CAREERS_HINT.search(r["url"]) and looks_official(r["url"], name):
            out.careers_url = r["url"]

    corpus = "\n".join(f"{r.get('title', '')}\n{r.get('snippet', '')}" for r in results)
    out.funding = extract_funding(corpus, url=results[0]["url"] if results else None)

    if depth in ("standard", "deep") and not out.website:
        # Exa's company category returns the company's own page rather than coverage
        # of it, which is exactly what the first search tends to miss for a startup
        # whose name does not appear in its domain.
        more = web.search(f"category:company {name}", limit=4, fresh=fresh)
        for r in more.get("results") or []:
            if looks_official(r["url"], name):
                parsed = urlparse(r["url"])
                out.website = f"{parsed.scheme}://{parsed.hostname}"
                out.domain = (parsed.hostname or "").removeprefix("www.")
                out.sources.append(r["url"])
                used.append(f"web_search:{more.get('backend')}")
                break

    if depth in ("standard", "deep"):
        target = out.website or (results[0]["url"] if results else None)
        if target:
            page = web.read(target, fresh=fresh, timeout=45)
            if page.get("text"):
                used.append(f"page_read:{page.get('backend')}")
                out.sources.append(page["url"])
                signals, roles = hiring_signals(page["text"])
                out.hiring_signals += [s for s in signals if s not in out.hiring_signals]
                out.open_roles += [r for r in roles if r not in out.open_roles]
                if not out.one_liner:
                    out.one_liner = re.sub(r"\s+", " ", page["text"])[:300]

        org = gh.find_org(name, website=out.website)
        if org:
            out.github_org = org
            repos = gh.org_repos(org, limit=6, fresh=fresh)
            if repos.get("results"):
                used.append(f"github:{repos.get('backend')}")
                out.repos = [RepoHit(**{k: v for k, v in r.items() if k in RepoHit.__annotations__}) for r in repos["results"]]

        # the careers page is the whole point of this system, so it is not a
        # "deep" extra — it runs whenever we have a domain to try it on
        if _careers_page(out, fresh=fresh):
            used.append("page_read:careers")

    if depth == "deep":
        chatter = rd.search(f"{name} startup", limit=5, fresh=fresh)
        if chatter.get("results"):
            used.append(f"reddit:{chatter.get('backend')}")
            out.reddit = [
                rd.RedditPost(**{k: v for k, v in p.items() if k in rd.RedditPost.__annotations__})
                for p in chatter["results"]
            ]

        videos = yt.search(f"{name} founder interview", limit=3, fresh=fresh)
        if videos.get("results"):
            used.append(f"youtube:{videos.get('backend')}")
            out.videos = [
                yt.Video(**{k: v for k, v in v_.items() if k in yt.Video.__annotations__})
                for v_ in videos["results"]
            ]

    out.backends_used = used
    out.confidence = "scraped" if out.website else "inferred"
    payload = out.as_dict()
    if not results:
        payload["error"] = hits.get("error", "nothing found")
        payload["hint"] = hits.get("hint", "")
    return payload


def _careers_page(out: CompanyResearch, *, fresh: bool) -> bool:
    """Try the usual careers paths on the company's own domain."""
    if not out.website:
        return False
    for path in ("/careers", "/jobs", "/company/careers"):  # bounded: each try is a fetch
        # cheap backend and a short timeout on purpose: most of these paths do not
        # exist, and no careers page is worth 90 seconds of the caller's time
        page = web.read(out.website.rstrip("/") + path, fresh=fresh, timeout=20, prefer="direct")
        if not page.get("text"):
            continue
        signals, roles = hiring_signals(page["text"], limit=8)
        if not signals and not roles:
            continue
        out.careers_url = out.careers_url or page["url"]
        out.hiring_signals += [s for s in signals if s not in out.hiring_signals]
        out.open_roles += [r for r in roles if r not in out.open_roles]
        out.sources.append(page["url"])
        return True
    return False


# ------------------------------------------------------------------ startups

DEFAULT_REGIONS = ["United States", "United Kingdom", "United Arab Emirates"]

_QUERY_TEMPLATES = [
    "news article about an AI startup in {region} that just raised a {stage} round",
    "{region} AI startup funding announcement, recently funded, hiring engineers",
]


def startups(
    topic: str = "AI",
    *,
    regions: list[str] | None = None,
    stages: list[str] | None = None,
    limit: int = 10,
    per_query: int = 8,
    enrich: int = 5,
    fresh: bool = False,
) -> dict:
    """Recently funded startups, with funding and hiring signals attached.

    Runs one semantic search per region/stage, extracts the company and the funding
    claim from each headline, dedups, then researches the top `enrich` of them for
    website, GitHub and hiring information.
    """
    regions = regions or DEFAULT_REGIONS
    stages = stages or ["seed or Series A"]

    found: dict[str, dict] = {}
    queries: list[str] = []
    errors: list[str] = []

    for region in regions:
        for stage in stages:
            for template in _QUERY_TEMPLATES:
                query = template.format(region=region, stage=stage).replace("AI ", f"{topic} ")
                queries.append(query)
                hits = web.search(query, limit=per_query, fresh=fresh)
                if hits.get("error"):
                    errors.append(hits["error"])
                    continue
                for r in hits.get("results") or []:
                    name = extract_company_name(r.get("title", ""))
                    if not name:
                        continue
                    key = "".join(ch for ch in name.lower() if ch.isalnum())
                    text = f"{r.get('title', '')}. {r.get('snippet', '')}"
                    funding = extract_funding(text, url=r.get("url"))
                    record = found.setdefault(
                        key,
                        {
                            "name": name,
                            "region": region,
                            "announcement_url": r.get("url"),
                            "announcement_title": r.get("title"),
                            "published": r.get("published"),
                            "funding": funding.as_dict() if funding else None,
                            "sources": [],
                        },
                    )
                    if r.get("url") and r["url"] not in record["sources"]:
                        record["sources"].append(r["url"])
                    if not record.get("funding") and funding:
                        record["funding"] = funding.as_dict()

    ranked = sorted(
        found.values(),
        key=lambda c: ((c.get("funding") or {}).get("amount_usd_m") or 0),
        reverse=True,
    )[:limit]

    for record in ranked[: max(0, enrich)]:
        detail = company(record["name"], depth="standard", fresh=fresh)
        record["website"] = detail.get("website")
        record["domain"] = detail.get("domain")
        record["one_liner"] = detail.get("one_liner")
        record["github_org"] = detail.get("github_org")
        record["careers_url"] = detail.get("careers_url")
        record["hiring_signals"] = detail.get("hiring_signals", [])
        record["open_roles"] = detail.get("open_roles", [])
        record["repos"] = detail.get("repos", [])
        for url in detail.get("sources", []) or []:
            if url not in record["sources"]:
                record["sources"].append(url)
        record["confidence"] = detail.get("confidence", "inferred")

    for record in ranked[max(0, enrich):]:
        record["confidence"] = "inferred"      # headline only, nothing corroborated

    return {
        "topic": topic,
        "regions": regions,
        "queries": queries,
        "found": len(found),
        "enriched": min(enrich, len(ranked)),
        "companies": ranked,
        "errors": sorted(set(errors)),
    }
