"""Fill the company registry from the research layer: what they do, and who funded them.

The grader in `targeting.py` can only judge what it knows, and a scraped job board tells
it almost nothing about the company behind the posting. This module is the other half:
it reads the company's own pages through the research layer and writes back the four
things Tejmul asked the knowledge graph to carry for every company —

    · a short description        · what they actually do
    · the stipend it offers      · what it can pay on a PPO

— then re-grades the company so the tier reflects the new evidence.

Everything degrades. With no web-search backend it reads the company's own site; with
no OpenRouter key it falls back to the page's own first sentence; with neither it still
records what it saw. A field with no source stays null: the house rule is that nothing
is stated more firmly than the evidence supports.
"""

from __future__ import annotations

import json
import logging
import re

from sqlmodel import select

from jobhunter.db import Company, get_session, init_db, utcnow
from jobhunter.research import agent as research_agent
from jobhunter.research import github as gh
from jobhunter.research import web
from jobhunter.targeting import TIER_ORDER, grade_company

log = logging.getLogger(__name__)

# Nav chrome, cookie banners and legal boilerplate are not a description of a business.
_NOT_A_DESCRIPTION = re.compile(
    r"^(home|about|careers?|jobs|blog|docs|documentation|pricing|login|log in|sign up|sign in|"
    r"contact|menu|skip to|search|cookie|privacy|terms|copyright|©|all rights reserved|"
    r"we use cookies|accept|subscribe|newsletter)\b",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _first_real_sentence(text: str, *, title: str | None = None) -> str | None:
    """The first line of a page that reads like a description of the business.

    A page title is not one. "Affirm | Pay over time with flexible payment plans" is
    SEO furniture, and the reader hands it back as the first line of the document, so
    pipe-separated banners and the title itself are skipped in favour of prose.
    """
    title_norm = re.sub(r"\s+", " ", (title or "")).strip().lower()
    for block in (text or "").splitlines():
        line = re.sub(r"\s+", " ", block).strip(" -•|")
        if len(line) < 40 or len(line) > 400 or _NOT_A_DESCRIPTION.search(line):
            continue
        if len(line.split()) < 8:
            continue
        if title_norm and (line.lower() == title_norm or line.lower().startswith(title_norm[:40])):
            continue
        if line.count("|") >= 1 or line.count("·") >= 2:
            continue  # a banner or a breadcrumb, not a sentence
        first = _SENTENCE.split(line)[0].strip()
        return (first if len(first) >= 40 else line)[:300]
    return None


# A funding claim only counts when the sentence is actually about funding. Run over a
# whole homepage, the generic extractor reads "$400" off a pricing table and "growth"
# off a marketing headline — which then rejects a perfectly good company for having
# raised a late-stage round it never raised.
_FUNDING_SENTENCE = re.compile(
    r"\b(raised|raising|has raised|closed (?:a|our|its)|secured|announc\w+ (?:a|our|its) "
    r"(?:\$[\d.]+[mb]?\s*)?(?:seed|series|round|funding)|funding round|series [a-e]\b|"
    r"seed round|led by|backed by|investment from)\b",
    re.I,
)
# Any real round name is kept — including the late ones, because "Series F" is exactly
# the fact that should reject a company. Only non-stages ("growth", "strategic") are dropped.
_STAGE_OK = re.compile(r"^(pre[- ]?seed|seed|series [a-z])$", re.I)


def _funding_from(text: str, *, url: str | None = None) -> dict:
    """A funding signal, or {} — extracted only from sentences that are about funding."""
    if not text:
        return {}
    windows = [
        re.sub(r"\s+", " ", text[max(0, m.start() - 200) : m.end() + 300])
        for m in list(_FUNDING_SENTENCE.finditer(text))[:6]
    ]
    for window in windows:
        signal = research_agent.extract_funding(window, url=url)
        if not signal:
            continue
        data = signal.as_dict()
        stage = (data.get("stage") or "").strip()
        if stage and not _STAGE_OK.match(stage):
            data.pop("stage", None)   # "growth", "strategic" and friends are not our stages
        # "$100,000" on a pricing page is not a $100,000M round: a startup round above
        # $2B is a parse error, and an amount with no round word next to it is a guess
        if data.get("amount_usd_m") and float(data["amount_usd_m"]) > 2000:
            data["amount_usd_m"] = None
        if data.get("stage") or data.get("amount_usd_m"):
            return data
    return {}


_SYSTEM = """You describe what a company does, using only the text you are given. You never
add a fact that is not in the text. If the text does not say what the company does, you say
so. JSON only."""

_PROMPT = """Company: {name}

Text from their own website:
---
{text}
---

In one sentence of at most 25 words, what does this company build or sell? Use only what
the text says. Do not add markets, funding, size or claims that are not written above.

Reply with exactly:
{{"description": "<one sentence>"|null, "evidence": "<the words you based it on, quoted>"|null}}"""


def _model_available() -> bool:
    """Is there a key at all? Asking first turns four logged errors into none."""
    try:
        from jobhunter import openrouter

        return bool(openrouter.configured())
    except Exception:  # noqa: BLE001
        return False


_PROFILE_SYSTEM = """You read text from one company's own website and fill in a profile, using
only what the text says. You never add a fact from memory: no funding, valuation, founder,
university or number that is not written in the text. A field the text does not support is
null. Every filled field carries the words it was read from. JSON only."""

_PROFILE_PROMPT = """Company: {name}

Text from their own website (home and about pages):
---
{text}
---

Reply with exactly this JSON:
{{"description": "<one sentence, at most 25 words, what they build or sell>"|null,
  "description_evidence": "<quoted words>"|null,
  "story": "<the ORIGIN, two to four sentences, in this order: WHY they started (the problem
            or frustration the founders saw), HOW and WHERE it began (a university, a lab, a
            previous job, a side project), WHO started it, and what has happened since.
            ONLY what the text says. If the text has no origin — only what the product does —
            this is null>"|null,
  "story_evidence": "<quoted words>"|null,
  "funding_stage": "pre-seed|seed|series a|series b|series c|..."|null,
  "funding_amount_usd_m": <number in millions of USD>|null,
  "funding_announced": "<YYYY-MM or YYYY>"|null,
  "investors": ["<names written in the text>"],
  "funding_evidence": "<quoted words>"|null,
  "valuation_usd_m": <number in millions of USD, only if the text states a valuation>|null,
  "valuation_evidence": "<quoted words>"|null,
  "team_size": <integer, only if the text states headcount>|null,
  "team_evidence": "<quoted words>"|null}}"""


def _profile_with_model(name: str, text: str) -> dict | None:
    """The whole profile in one call on the `cheap` alias. None on any failure."""
    from jobhunter import llm

    try:
        data = llm.chat_json(
            _PROFILE_PROMPT.format(name=name, text=text[:7000]),
            _PROFILE_SYSTEM, temperature=0.0, alias="cheap", purpose="company-profile", default=None,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("profile model call failed: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    # a claim without its words is dropped — the rule that makes the page safe to repeat
    for field, ev in (("description", "description_evidence"), ("story", "story_evidence"),
                      ("funding_stage", "funding_evidence"), ("funding_amount_usd_m", "funding_evidence"),
                      ("valuation_usd_m", "valuation_evidence"), ("team_size", "team_evidence")):
        if data.get(field) not in (None, "", []) and not (data.get(ev) or "").strip():
            data[field] = None
    return data


_ABOUT_PATHS = ("/about", "/about-us", "/company", "/our-story", "/story", "/team", "/mission")


def _read_about(base: str, *, fresh: bool) -> tuple[str, str | None]:
    """The first About-style page that answers, as text — origin stories live there."""
    from urllib.parse import urlparse

    parsed = urlparse(base if "://" in base else "https://" + base)
    root = f"{parsed.scheme or 'https'}://{parsed.hostname}"
    for path in _ABOUT_PATHS:
        page = web.read(root + path, fresh=fresh, timeout=20, prefer="direct")
        text = page.get("text") or ""
        if len(text) > 300:
            return text, page.get("url") or root + path
    return "", None


def _describe_with_model(name: str, text: str) -> tuple[str | None, str | None]:
    """One line about the company on the `cheap` OpenRouter alias. (None, None) on any failure."""
    from jobhunter import llm

    try:
        data = llm.chat_json(
            _PROMPT.format(name=name, text=text[:4000]),
            _SYSTEM, temperature=0.0, alias="cheap", purpose="company-description", default=None,
        )
    except Exception as e:  # noqa: BLE001 — no key, budget refusal, 429: all non-fatal
        log.debug("description model call failed: %s", e)
        return None, None
    if not isinstance(data, dict):
        return None, None
    desc = (data.get("description") or "").strip() or None
    return (desc[:300] if desc else None), (str(data.get("evidence") or "")[:300] or None)


def enrich_company(company_id: int, *, depth: str = "standard", fresh: bool = False,
                   use_model: bool = True, use_search: bool = True) -> dict:
    """Read one company's own pages and write back description, funding and links."""
    init_db()
    with get_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            return {"error": f"no company {company_id}"}
        name, website = company.name, company.website

    found: dict = {"company": name, "sources": [], "backends": []}

    # 1. the full research agent, when a web-search backend exists at all — and only when
    #    asked: each company costs several Exa searches, and the key is a $10 free tier.
    #    The bulk pass (use_search=False) reads the company's own site and nothing else.
    research: dict = {}
    if use_search and web.search("test", limit=1).get("backend"):
        research = research_agent.company(name, website=website, depth=depth, fresh=fresh)
        found["backends"] += research.get("backends_used") or []
        found["sources"] += research.get("sources") or []

    # 2. their own site, read directly — this works with no search backend and no key
    page_text = ""
    page_title = None
    target = research.get("website") or website
    if target:
        page = web.read(target, fresh=fresh, timeout=30, prefer="direct")
        page_text = page.get("text") or ""
        page_title = page.get("title")
        if page_text:
            found["backends"].append(f"page_read:{page.get('backend')}")
            found["sources"].append(page.get("url") or target)

    # 2b. their About page — the story (where it started, who, what since) lives there
    about_text, about_url = ("", None)
    if target and page_text:
        about_text, about_url = _read_about(target, fresh=fresh)
        if about_text:
            found["sources"].append(about_url)

    # 3. the profile: description, story, funding, valuation, team — one model call over
    #    their own words; the page's first sentence when there is no model
    description = evidence = None
    profile: dict = {}
    if page_text:
        if use_model and _model_available():
            corpus = page_text[:4000] + ("\n\n--- about ---\n" + about_text[:3500] if about_text else "")
            profile = _profile_with_model(name, corpus) or {}
            description, evidence = profile.get("description"), profile.get("description_evidence")
        if not description:
            description = _first_real_sentence(page_text, title=page_title)
            evidence = description
    description = description or research.get("one_liner")

    # 4. funding, from whatever text we hold. Regex + quote, never a guess.
    funding = research.get("funding") or {}
    if not funding and profile.get("funding_stage") or profile.get("funding_amount_usd_m"):
        funding = {
            "stage": (profile.get("funding_stage") or "").lower() or None,
            "amount_usd_m": profile.get("funding_amount_usd_m"),
            "announced": profile.get("funding_announced"),
            "investors": [i for i in (profile.get("investors") or []) if isinstance(i, str)],
            "evidence_quote": profile.get("funding_evidence"),
        }
        stage = funding.get("stage") or ""
        if stage and not _STAGE_OK.match(stage):
            funding["stage"] = None
    if not funding and page_text:
        funding = _funding_from(page_text, url=target)

    with get_session() as session:
        company = session.get(Company, company_id)
        company.description = description or company.description
        company.website = research.get("website") or company.website
        company.domain = research.get("domain") or company.domain
        company.github_org = company.github_org or research.get("github_org") or (
            gh.find_org(name, website=company.website) if not research else None
        )
        company.careers_url = research.get("careers_url") or company.careers_url
        if funding:
            company.funding_stage = funding.get("stage") or company.funding_stage
            company.funding_amount_usd_m = funding.get("amount_usd_m") or company.funding_amount_usd_m
            company.funding_announced = funding.get("announced") or company.funding_announced
            if funding.get("investors"):
                company.funding_investors = json.dumps(funding["investors"])
            company.funding_evidence = funding.get("evidence_quote") or company.funding_evidence
        if profile.get("story"):
            # an origin read from their own About page beats a directory blurb
            directory_story = not (company.story_evidence or "").startswith("http")
            if not company.story or directory_story:
                company.story = str(profile["story"])[:1200]
                company.story_evidence = about_url or target
        if profile.get("valuation_usd_m") is not None:
            try:
                company.valuation_usd_m = float(profile["valuation_usd_m"])
                company.valuation_evidence = str(profile.get("valuation_evidence") or "")[:300]
            except (TypeError, ValueError):
                pass
        if profile.get("team_size") is not None and not company.team_size:
            try:
                company.team_size = int(profile["team_size"])
            except (TypeError, ValueError):
                pass
        if evidence and not company.notes:
            company.notes = f"description read from: {evidence[:200]}"
        # mark the About page as read even when it told no story, so the story pass
        # does not re-read the same silent site every night
        if page_text and "[story]" not in (company.notes or ""):
            company.notes = (company.notes + "\n" if company.notes else "") + (
                f"[story] read {about_url}" if about_url else "[story] no About page found")
        company.contacts_found_at = company.contacts_found_at  # untouched; here for clarity
        session.add(company)
        session.commit()

        # the tier is only as good as the evidence, so re-grade with what we just learned
        company = session.get(Company, company_id)
        g = grade_company(session, company)
        session.commit()

    found.update({
        "story": profile.get("story"),
        "valuation_usd_m": profile.get("valuation_usd_m"),
        "team_size": profile.get("team_size"),
        "description": description,
        "funding": {k: funding.get(k) for k in ("stage", "amount_usd_m", "investors")} if funding else None,
        "tier": g.tier, "tier_reason": g.reason,
        "enriched_at": utcnow().isoformat(timespec="seconds"),
    })
    log.info("enriched %s -> %s", name, g.tier)
    return found


# ------------------------------------------------------------------ company facts from search
#
# Exa's company index carries the LinkedIn-style card for a company — what it does, how
# many people, where, founded when, sometimes the round — as text with a source URL. One
# search per company, inside research.exa_daily_cap, for the companies that matter most
# (roles first). Everything stored is a quote-backed regex read of that text; the model
# is not asked to remember anything.

_EMPLOYS = re.compile(r"\b(?:employs|has|with|team of)\s+(\d{1,5})\s+(?:people|employees|staff)\b", re.I)
_FOUNDED = re.compile(r"\bfounded (?:in )?(\d{4})\b", re.I)
_HQ = re.compile(r"\b(?:headquartered|based|located)\s+in\s+([A-Z][A-Za-z .'-]{2,40}?)(?:[,.;]|\s+(?:and|with|that|which)\b)", re.I)


_MD_NOISE = re.compile(r"(^|\s)#+\s*|\.{3,}|\[[^\]]*\]\([^)]*\)")


def _card_sentence(text: str, name: str) -> str | None:
    """The sentence on a company card that says what they do: the first one that reads
    '<Name> is/builds/provides …', else the first plain sentence long enough to mean something."""
    clean = re.sub(r"\s+", " ", _MD_NOISE.sub(" ", text or "")).strip()
    # the card's heading ("RevenueCat (RevenueCat, Inc.)") is glued to the first sentence
    clean = re.sub(r"^[^.!?]{0,80}?\([^)]*(?:Inc|Ltd|LLC|GmbH|Corp)[^)]*\)\s*", "", clean)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if 30 <= len(s.strip()) <= 300]
    stem = name.lower()[:8]
    generic = re.compile(r"\bis an? [A-Z][A-Za-z &/-]+ company\.?$")   # "is a Software Development company."
    about = [s for s in sentences if stem in s.lower()
             and re.search(r"\b(is|are|builds?|provides?|makes?|offers?|develops?|helps?|enables?)\b", s)]
    for s in about:
        if not generic.search(s):
            return s
    if about:
        return about[0]
    return sentences[0] if sentences else None


def _sane_funding(fund: dict) -> dict:
    """A round read off a card can be mangled ('$20,000' → 20000M). Above $2B for a
    startup round is a parse error, not a fact: keep the stage, drop the number."""
    if fund and fund.get("amount_usd_m") and float(fund["amount_usd_m"]) > 2000:
        fund = {**fund, "amount_usd_m": None}
    return fund


def facts_from_search(company_id: int, *, fresh: bool = False) -> dict:
    """One Exa company-index lookup → description, team size, HQ, founded, funding."""
    from jobhunter.targeting import grade_company, region_of

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, website, domain = c.name, c.website, c.domain
    query = f"{name} company" + (f" {domain}" if domain else "")
    hit = web.search(query, limit=5, category="company", fresh=fresh,
                     include_domains=[domain] if domain else None)
    if hit.get("error") or not hit.get("results"):
        # the domain filter can be too strict for a site behind a different host; retry open
        hit = web.search(query, limit=5, category="company", fresh=fresh)
    if hit.get("error"):
        return {"company": name, "error": hit["error"]}
    results = hit.get("results") or []
    # prefer the hit on their own domain, then the one whose title carries the name
    stem = "".join(ch for ch in name.lower() if ch.isalnum())[:12]
    best = None
    for r in results:
        host = (r.get("url") or "").split("//")[-1].split("/")[0].lower()
        if domain and host.endswith(domain.lower()):
            best = r
            break
    if best is None:
        best = next((r for r in results if stem and stem in "".join(ch for ch in (r.get("title") or "").lower() if ch.isalnum())), None)
    if best is None:
        return {"company": name, "error": "no result about this company", "tried": [r.get("url") for r in results[:3]]}
    text = re.sub(r"\s+", " ", best.get("snippet") or "")
    out: dict = {"company": name, "source": best.get("url"), "text": text[:300]}
    with get_session() as session:
        c = session.get(Company, company_id)
        if not c.description or len(c.description) < 25:
            desc = _card_sentence(text, name)
            if desc:
                c.description = desc
                out["description"] = desc
        m = _EMPLOYS.search(text)
        if m and not c.team_size:
            c.team_size = int(m.group(1))
            out["team_size"] = c.team_size
        m = _FOUNDED.search(text)
        if m:
            out["founded"] = m.group(1)
        m = _HQ.search(text)
        if m:
            out["hq"] = m.group(1).strip()
            c.hq_region = c.hq_region or region_of(m.group(1))
        fund = _sane_funding(_funding_from(text, url=best.get("url")))
        if fund and not c.funding_stage:
            c.funding_stage = fund.get("stage") or c.funding_stage
            c.funding_amount_usd_m = fund.get("amount_usd_m") or c.funding_amount_usd_m
            c.funding_announced = fund.get("announced") or c.funding_announced
            c.funding_evidence = fund.get("evidence_quote") or c.funding_evidence
            out["funding"] = {k: fund.get(k) for k in ("stage", "amount_usd_m", "announced")}
        tag = f"[facts] read from {best.get('url')}"
        if tag not in (c.notes or ""):
            c.notes = (c.notes + "\n" if c.notes else "") + tag
        session.add(c)
        session.commit()
        c = session.get(Company, company_id)
        g = grade_company(session, c)
        session.commit()
        out["tier"] = g.tier
        out["pay_basis"] = g.pay_basis
    return out


def facts_pending(limit: int = 40) -> list[dict]:
    """Companies with roles and no [facts] read yet, best tier first, inside the Exa cap."""
    from jobhunter.db import Job
    from jobhunter.targeting import TIER_ORDER

    init_db()
    with get_session() as session:
        rows = session.exec(select(Company)).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        pending = [c for c in rows if c.tier != "reject" and c.id in with_jobs
                   and "[facts]" not in (c.notes or "")]
        pending.sort(key=lambda c: (0 if not c.description else 1, 0 if not c.team_size else 1,
                                    TIER_ORDER.get(c.tier or "unknown", 9), c.name))
        ids = [c.id for c in pending[:limit]]
    out = []
    for cid in ids:
        r = facts_from_search(cid)
        out.append(r)
        if r.get("error") and "cap" in r["error"]:
            break
    return out


def enrich_pending(limit: int = 10, *, tiers: tuple[str, ...] = ("tier1", "tier2", "prospect", "unknown"),
                   only_missing: bool = True, fresh: bool = False, use_model: bool = True,
                   use_search: bool = True, roles_first: bool = True,
                   missing: str = "description") -> list[dict]:
    """Enrich the companies worth enriching, best tier first. Never touches a `reject`.

    `missing` picks the gap to close: "description" (nothing read yet) or "story" (described,
    but the About page — origin, rounds, valuation, team — never read). `roles_first` puts
    companies that already have scraped roles ahead. Sequential, one commit per company.
    """
    from jobhunter.db import Job

    init_db()
    with get_session() as session:
        rows = session.exec(select(Company)).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all()) if roles_first else set()

        def gap(c: Company) -> bool:
            if not only_missing:
                return True
            if missing == "story":
                # no story, or only a directory blurb (evidence is not a URL): the About
                # page has not been read for the origin yet
                origin_read = "[story]" in (c.notes or "")
                return bool(c.description) and not origin_read and (
                    not c.story or not (c.story_evidence or "").startswith("http"))
            return not c.description

        pending = [
            c for c in rows
            if (c.tier or "unknown") in tiers
            and (c.website or c.domain)
            and gap(c)
        ]
        pending.sort(key=lambda c: (0 if c.id in with_jobs else 1,
                                    TIER_ORDER.get(c.tier or "unknown", 9), c.name))
        ids = [c.id for c in pending[:limit]]
    out = []
    for cid in ids:
        try:
            out.append(enrich_company(cid, fresh=fresh, use_model=use_model, use_search=use_search))
        except Exception as e:  # noqa: BLE001 — one unreadable site must not end the pass
            log.warning("enrich: company %s failed: %s", cid, e)
            out.append({"company_id": cid, "error": str(e)[:160]})
    return out


__all__ = ["enrich_company", "enrich_pending"]
