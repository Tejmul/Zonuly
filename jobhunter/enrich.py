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

from sqlmodel import col, select

from jobhunter import CONFIG, admission, backoff
from jobhunter.db import Company, get_session, init_db, utcnow
from jobhunter.research import agent as research_agent
from jobhunter.research import github as gh
from jobhunter.research import web
from jobhunter.targeting import grade_company

log = logging.getLogger(__name__)

# Nav chrome, cookie banners and legal boilerplate are not a description of a business.
_NOT_A_DESCRIPTION = re.compile(
    r"^(home|about|careers?|jobs|blog|docs|documentation|pricing|login|log in|sign up|sign in|"
    r"contact|menu|skip to|search|cookie|privacy|terms|copyright|©|all rights reserved|"
    r"we use cookies|accept|subscribe|newsletter)\b",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# What a bot wall, a CAPTCHA or a JS-only shell hands back instead of the page. Saved as a
# description it read "Just a moment... Checking your browser" on 18 rows (audit P9).
_BOT_WALL = re.compile(
    r"(just a moment|checking your browser|attention required|enable javascript|"
    r"verify you are (?:a )?human|are you a robot|captcha|access denied|request blocked|"
    r"pardon our interruption|ddos protection|cf-browser-verification|cloudflare ray id|"
    r"403 forbidden|you have been blocked|unusual traffic|please turn javascript on|"
    r"this site can.t be reached|page not found|404 not found)",
    re.I,
)


def bot_wall(text: str | None) -> bool:
    """Is this page text a bot wall / error shell rather than the company's own words?"""
    if not text:
        return False
    head = text[:1500]
    return bool(_BOT_WALL.search(head)) and len(text) < 4000


def _first_real_sentence(text: str, *, title: str | None = None) -> str | None:
    """The first line of a page that reads like a description of the business.

    A page title is not one. "Affirm | Pay over time with flexible payment plans" is
    SEO furniture, and the reader hands it back as the first line of the document, so
    pipe-separated banners and the title itself are skipped in favour of prose.
    """
    title_norm = re.sub(r"\s+", " ", (title or "")).strip().lower()
    for block in (text or "").splitlines():
        line = re.sub(r"\s+", " ", block).strip(" -•|")
        if len(line) < 40 or len(line) > 400 or _NOT_A_DESCRIPTION.search(line) or bot_wall(line):
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
    # a company card states the rounds in a fixed shape; read it as such before the
    # generic extractor reads "Annual Revenue: USD 2,000,000" as a $2,000M round
    card = admission.parse_funding_card(text)
    if card.get("stage") or card.get("total_usd_m"):
        quote = re.sub(r"\s+", " ", text)[:300]
        return {"stage": card.get("stage"), "amount_usd_m": card.get("amount_usd_m"),
                "total_usd_m": card.get("total_usd_m"), "announced": card.get("announced"),
                "public": card.get("public"), "evidence_quote": quote}
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
        elif stage:
            data["stage"] = admission.normalize_stage(stage)
        if data.get("amount_usd_m") and not admission.credible_amount(
                float(data["amount_usd_m"]), data.get("evidence_quote") or window):
            data["amount_usd_m"] = None   # revenue or volume, not a raise
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
        log.warning("profile model call failed for %s: %s", name, e)
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
        if len(text) > 300 and not bot_wall(text):
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
        log.warning("description model call failed for %s: %s", name, e)
        return None, None
    if not isinstance(data, dict):
        return None, None
    desc = (data.get("description") or "").strip() or None
    return (desc[:300] if desc else None), (str(data.get("evidence") or "")[:300] or None)


def _write_funding(company: Company, funding: dict) -> None:
    """A round read from a source, onto the row: stage in the one vocabulary, amount only
    when it is a raise, total and valuation when stated, the quote it came from."""
    stage = admission.normalize_stage(funding.get("stage"))
    if funding.get("public"):
        stage = "Public"
    company.funding_stage = stage or company.funding_stage
    amt = funding.get("amount_usd_m")
    if amt and admission.credible_amount(float(amt), funding.get("evidence_quote")):
        company.funding_amount_usd_m = float(amt)
    company.funding_announced = funding.get("announced") or company.funding_announced
    if funding.get("total_usd_m"):
        company.funding_total_usd_m = float(funding["total_usd_m"])
    if funding.get("valuation_usd_m"):
        company.valuation_usd_m = float(funding["valuation_usd_m"])
        company.valuation_evidence = (funding.get("evidence_quote") or "")[:300] or company.valuation_evidence
    if funding.get("investors"):
        company.funding_investors = json.dumps(funding["investors"])
    company.funding_evidence = funding.get("evidence_quote") or company.funding_evidence


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
        if bot_wall(page_text):
            found["bot_wall"] = True
            page_text = ""
        if not page_text:
            found["error"] = "bot wall" if found.get("bot_wall") else (page.get("error") or "site unreadable")
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
    # (was `not funding and A or B`, which let a profile amount overwrite research funding)
    if not funding and (profile.get("funding_stage") or profile.get("funding_amount_usd_m")):
        funding = {
            "stage": (profile.get("funding_stage") or "").lower() or None,
            "amount_usd_m": profile.get("funding_amount_usd_m"),
            "announced": profile.get("funding_announced"),
            "investors": [i for i in (profile.get("investors") or []) if isinstance(i, str)],
            "evidence_quote": profile.get("funding_evidence"),
        }
        stage = funding.get("stage") or ""
        funding["stage"] = admission.normalize_stage(stage) if stage and _STAGE_OK.match(stage) else None
    if not funding and page_text:
        funding = _funding_from(page_text, url=target)

    with get_session() as session:
        company = session.get(Company, company_id)
        if not page_text and not research:
            # nothing was read: leave a marker with backoff so tomorrow's queue starts
            # with someone else, and save nothing that a bot wall said
            backoff.fail(company, "enrich", found.get("error") or "site unreadable")
            session.add(company)
            session.commit()
            found.update({"description": None, "funding": None, "story": None})
            log.info("enrich %s: %s (backoff)", name, found.get("error"))
            return found
        backoff.ok(company, "enrich")
        if company.description and bot_wall(company.description):
            company.description = None   # an earlier pass saved a bot wall
        company.description = description or company.description
        company.website = research.get("website") or company.website
        company.domain = research.get("domain") or company.domain
        company.github_org = company.github_org or research.get("github_org") or (
            gh.find_org(name, website=company.website) if not research else None
        )
        company.careers_url = research.get("careers_url") or company.careers_url
        if funding:
            _write_funding(company, funding)
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


_HIRING_TEXT = re.compile(r"\b(we(?:'re| are) hiring|now hiring|open (?:roles|positions)|join (?:our|the) team|"
                          r"careers? at|is hiring)\b", re.I)


def facts_from_search(company_id: int, *, fresh: bool = False, keyless: bool = False) -> dict:
    """One company-index lookup → description, website, HQ, founded, funding (round,
    amount, date, total, valuation) and a hiring hint. Works on a name alone, so a
    company with no roles and no website is filled too. A miss leaves a backoff marker.

    `keyless` uses only the hosted Exa MCP (no category filter, unmetered) — the hourly
    free tick; otherwise the metered Exa API's company index, inside exa_daily_cap."""
    from jobhunter.targeting import grade_company, region_of

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, website, domain = c.name, c.website, c.domain
    query = f"{name} company" + (f" {domain}" if domain else "")
    if keyless:
        hit = web.search(f"{name} startup funding round", limit=5, fresh=fresh, only=("exa-mcp",))
    else:
        hit = web.search(query, limit=5, category="company", fresh=fresh,
                         include_domains=[domain] if domain else None)
        if hit.get("error") or not hit.get("results"):
            # the domain filter can be too strict for a site behind a different host; retry open
            hit = web.search(query, limit=5, category="company", fresh=fresh)
    if hit.get("error"):
        # the channel failed (cap, 429, no backend) — that says nothing about this
        # company, so no backoff marker; the caller stops the pass
        return {"company": name, "error": hit["error"], "channel": True}
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
        _mark(company_id, "facts", "no result about this company")
        return {"company": name, "error": "no result about this company", "tried": [r.get("url") for r in results[:3]]}
    text = re.sub(r"\s+", " ", best.get("snippet") or "")
    out: dict = {"company": name, "source": best.get("url"), "text": text[:300]}
    with get_session() as session:
        c = session.get(Company, company_id)
        backoff.ok(c, "facts")
        if not c.description or len(c.description) < 25 or bot_wall(c.description):
            desc = _card_sentence(text, name)
            if desc and not bot_wall(desc):
                c.description = desc
                out["description"] = desc
        # the hit on a host that carries the name IS their site: the fix for name-only rows
        host = admission.host_of(best.get("url"))
        if not c.website and host and not admission.bogus_host(host, name) and admission.host_fits_name(name, host):
            c.website, c.domain = f"https://{host}", host
            out["website"] = c.website
        m = _EMPLOYS.search(text)
        if m and not c.team_size:
            c.team_size = int(m.group(1))   # information only — never a criterion
            out["team_size"] = c.team_size
        m = _FOUNDED.search(text)
        if m:
            out["founded"] = m.group(1)
        m = _HQ.search(text)
        if m:
            out["hq"] = m.group(1).strip()
            c.hq_region = c.hq_region or region_of(m.group(1))
            city = admission.india_city(m.group(1))
            if city and not c.city and not c.list_key:
                c.city = city
        fund = _sane_funding(_funding_from(text, url=best.get("url")))
        newer = fund and fund.get("announced") and (not c.funding_announced or str(fund["announced"]) > str(c.funding_announced))
        if fund and (not c.funding_stage or newer or fund.get("total_usd_m")):
            _write_funding(c, fund)
            out["funding"] = {k: fund.get(k) for k in ("stage", "amount_usd_m", "total_usd_m", "announced")}
        elif fund and fund.get("valuation_usd_m") and not c.valuation_usd_m:
            # a hand-picked list already states the round; a stated valuation is still new
            c.valuation_usd_m = float(fund["valuation_usd_m"])
            c.valuation_evidence = f"{(fund.get('evidence_quote') or text)[:240]} — {best.get('url')}"
            out["valuation_usd_m"] = c.valuation_usd_m
        if _HIRING_TEXT.search(text) and not c.hiring_claim_url:
            c.hiring_claim = _HIRING_TEXT.search(text).group(0)
            c.hiring_claim_url = best.get("url")
            c.hiring_claim_source = "exa"
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


# ------------------------------------------------------------------ valuation and headcount
#
# Two keyless searches (hosted Exa MCP only) for the two numbers a funding card rarely
# carries. Read by regex from the result snippets, never by a model, and kept only when
# the sentence the number sits in names the company — the sentence and the URL are stored
# as the evidence. A range ("51-200 employees") is a band, not a headcount: not stored.

_MONEY = (r"(?P<cur>US\$|\$|USD\s?|₹|Rs\.?\s?|INR\s?)\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?"
          r"(?P<unit>lakh crore|billion|bn|b|million|mn|mln|m|crore|cr)\b")
_VALUATION = re.compile(
    # "valuation" needs its verb: a data card's "Funding, Valuation $5.5M" header is followed
    # by the money RAISED, not a valuation
    r"(?:valued\s+(?:at\s+)?|valuation\s+(?:(?:of|at|stood|stands|was|is|pegged|touched|to)\s+){1,2})(?:about\s+|around\s+|nearly\s+|over\s+|roughly\s+|"
    r"more than\s+|close to\s+|almost\s+|~)?" + _MONEY
    + r"|" + _MONEY.replace("?P<", "?P<b_") + r"\s+(?:post[- ]money\s+|pre[- ]money\s+)?valuation", re.I)
_HEADCOUNT = re.compile(
    r"(?<![\d,–-])(?:(?:team|workforce|headcount) of\s+(?:over\s+|about\s+|around\s+|nearly\s+|more than\s+)?"
    r"(?P<a>\d[\d,]*)\+?(?:\s+(?:people|employees|members|staff))?"
    r"|(?:employs|employing|has|with|over|more than|nearly|around|about)\s+(?P<b>\d[\d,]*)\+?\s+"
    r"(?:full[- ]time\s+)?(?:employees|staff|team members)"
    r"|(?P<c>\d[\d,]*)\+?\s+(?:full[- ]time\s+)?employees)(?![\d–-])", re.I)
_USD_TO_INR = float((CONFIG.get("search") or {}).get("usd_to_inr") or 88)


def _usd_m(num: str, unit: str, cur: str) -> float | None:
    try:
        n = float(num.replace(",", ""))
    except ValueError:
        return None
    unit = unit.lower()
    if unit in ("crore", "cr", "lakh crore"):
        inr = n * (1e12 if unit == "lakh crore" else 1e7)
        return round(inr / _USD_TO_INR / 1e6, 1)
    if cur.strip().upper() in ("₹", "RS", "RS.", "INR"):
        return None    # "₹500 million" is rare and ambiguous next to crore — not guessed
    return n * 1000 if unit in ("billion", "bn", "b") else n


def _sentence_at(text: str, start: int, end: int) -> str:
    dot = text.rfind(". ", 0, start)
    a = max(dot + 2 if dot != -1 else 0, start - 220, 0)
    b = text.find(". ", end)
    return re.sub(r"\s+", " ", text[a: b + 1 if b != -1 else end + 160]).strip()[:300]


def valuation_from(text: str, name: str) -> tuple[float, str] | None:
    """(USD millions, the sentence) for the first stated valuation that names the company."""
    stem = admission.name_key(name)[:8]
    for m in _VALUATION.finditer(text or ""):
        g = m.groupdict()
        num, unit, cur = (g["num"], g["unit"], g["cur"]) if g.get("num") else (g["b_num"], g["b_unit"], g["b_cur"])
        sentence = _sentence_at(text, m.start(), m.end())
        if stem and stem not in re.sub(r"[^a-z0-9]", "", sentence.lower()):
            continue
        val = _usd_m(num, unit, cur or "")
        if val and 1 <= val <= 500_000:
            return val, sentence
    return None


def headcount_from(text: str, name: str) -> tuple[int, str] | None:
    """(people, the sentence) for the first stated headcount that names the company."""
    stem = admission.name_key(name)[:8]
    for m in _HEADCOUNT.finditer(text or ""):
        raw = m.group("a") or m.group("b") or m.group("c")
        sentence = _sentence_at(text, m.start(), m.end())
        if stem and stem not in re.sub(r"[^a-z0-9]", "", sentence.lower()):
            continue
        try:
            n = int(raw.replace(",", ""))
        except ValueError:
            continue
        if 2 <= n <= 200_000:
            return n, sentence
    return None


def size_from_search(company_id: int, *, fresh: bool = False) -> dict:
    """Valuation and team size for one company, keyless: two hosted-Exa-MCP searches, read
    by regex, evidence-linked. A channel failure leaves no marker; a miss backs off."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, need_val, need_team = c.name, c.valuation_usd_m is None, c.team_size is None
    out: dict = {"company": name}
    found: dict = {}
    for need, query, reader, key in ((need_val, f"{name} valuation", valuation_from, "valuation"),
                                     (need_team, f"{name} number of employees team size", headcount_from, "team")):
        if not need:
            continue
        hit = web.search(query, limit=6, fresh=fresh, only=("exa-mcp",))
        if hit.get("error") and not hit.get("results"):
            return {**out, "error": hit["error"], "channel": True}
        for r in hit.get("results") or []:
            got = reader(f"{r.get('title') or ''}. {r.get('snippet') or ''}", name)
            if got:
                found[key] = (got[0], f"{got[1]} — {r.get('url')}")
                break
    with get_session() as session:
        c = session.get(Company, company_id)
        if "valuation" in found and c.valuation_usd_m is None:
            c.valuation_usd_m, c.valuation_evidence = float(found["valuation"][0]), found["valuation"][1][:400]
            out["valuation_usd_m"] = c.valuation_usd_m
        if "team" in found and c.team_size is None:
            c.team_size, c.team_size_evidence = int(found["team"][0]), found["team"][1][:400]
            out["team_size"] = c.team_size
        if found:
            backoff.ok(c, "size")
        else:
            backoff.fail(c, "size", "no stated valuation or headcount in the search results")
        session.add(c)
        session.commit()
    return out


def _mark(company_id: int, step: str, error: str) -> None:
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is not None:
            backoff.fail(c, step, error)
            session.add(c)
            session.commit()


def _queue(session, *, need_jobs: bool = False, list_key: str | None = None) -> list[Company]:
    """Active, not overrated, in db.fill_order (list rows, then scrape-first, then
    priority) — the order every fill-in pass reads. A hand-picked list row is kept even
    when graded reject or overrated: the list is the decision. `list_key` narrows to one list."""
    from jobhunter.db import Job, fill_order

    stmt = select(Company).where(col(Company.status).is_(None))
    if list_key:
        stmt = stmt.where(Company.list_key == list_key)
    rows = session.exec(stmt).all()
    rows = [c for c in rows if c.list_key or (c.tier != "reject" and not c.overrated)]
    if need_jobs:
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        rows = [c for c in rows if c.id in with_jobs]
    rows.sort(key=lambda c: (*fill_order(c), c.id))
    return rows


def facts_pending(limit: int = 40, *, keyless: bool = False, roles_only: bool = False,
                  list_key: str | None = None) -> list[dict]:
    """Companies with no [facts] read yet — with or without roles — list rows first,
    skipping any in backoff. Inside the Exa cap unless `keyless`. With `list_key`, a list
    row whose facts were read but still lacks valuation or team size is read again."""
    init_db()
    with get_session() as session:
        def gap(c: Company) -> bool:
            if "[facts]" not in (c.notes or ""):
                return True
            return bool(list_key) and (c.valuation_usd_m is None or c.team_size is None)

        pending = [c for c in _queue(session, need_jobs=roles_only, list_key=list_key)
                   if gap(c) and backoff.due(c, "facts")]
        ids = [c.id for c in pending[:limit]]
        listed = {c.id for c in pending[:limit] if c.list_key}
    out = []
    for cid in ids:
        r = {}
        with get_session() as session:
            c = session.get(Company, cid)
            need_facts = "[facts]" not in (c.notes or "")
            need_size = cid in listed and keyless and (c.valuation_usd_m is None or c.team_size is None) \
                and backoff.due(c, "size")
        if need_facts:
            r = facts_from_search(cid, keyless=keyless)
            if r.get("channel"):
                out.append(r)
                break   # the search channel is capped or rate-limited: stop, mark nobody
        if need_size:
            # list rows: the two numbers a funding card rarely carries, searched for directly
            s = size_from_search(cid)
            if s.get("channel"):
                out.append({**r, **s})
                break
            r = {**s, **r, **{k: s[k] for k in ("valuation_usd_m", "team_size") if s.get(k)}}
        out.append(r)
    return out


def enrich_pending(limit: int = 10, *, tiers: tuple[str, ...] = ("tier1", "tier2", "prospect", "unknown"),
                   only_missing: bool = True, fresh: bool = False, use_model: bool = True,
                   use_search: bool = True, roles_first: bool = True,
                   missing: str = "description") -> list[dict]:
    """Enrich the companies worth enriching, best priority first. Never touches a
    `reject`, an overrated or a quarantined row, or one whose last read failed and is
    still in backoff — so tomorrow does not start with today's unreadable sites.

    `missing` picks the gap to close: "description" (nothing read yet) or "story" (described,
    but the About page — origin, rounds, valuation, team — never read). `roles_first` puts
    companies that already have scraped roles ahead among equal priority.
    """
    from jobhunter.db import Job

    init_db()
    with get_session() as session:
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
            return not c.description or bot_wall(c.description)

        pending = [
            c for c in _queue(session)
            if (c.tier or "unknown") in tiers
            and (c.website or c.domain)
            and gap(c)
            and backoff.due(c, "enrich")
        ]
        pending.sort(key=lambda c: (-(c.priority_score or 0), 0 if c.id in with_jobs else 1, c.id))
        ids = [c.id for c in pending[:limit]]
    out = []
    for cid in ids:
        try:
            out.append(enrich_company(cid, fresh=fresh, use_model=use_model, use_search=use_search))
        except Exception as e:  # noqa: BLE001 — one unreadable site must not end the pass
            log.warning("enrich: company %s failed: %s", cid, e)
            _mark(cid, "enrich", f"{type(e).__name__}: {str(e)[:120]}")
            out.append({"company_id": cid, "error": str(e)[:160]})
    return out


# ------------------------------------------------------------------ find the website
#
# A company that arrived as a name — from a funding headline, a directory, an X post — has
# no website, and without one nothing downstream can run: enrich reads the site, the ATS
# probe guesses from the domain, find_people needs the domain for emails. So: search the
# name (the hosted Exa MCP when it is not rate-limited, the metered Exa API in the paid
# slice), add the obvious domains for the name (acme.com, acme.ai, getacme.com…), fetch
# each and keep the first whose own page names the company and is not a parked or
# for-sale page. A miss leaves a backoff marker, never a guess.

_PARKED = re.compile(r"(domain (?:is )?for sale|buy this domain|this domain (?:may be|is) for sale|"
                     r"domain is parked|parked free|hugedomains|sedo\.com|dan\.com|afternic|"
                     r"godaddy\.com/domainsearch|domain has expired|coming soon)", re.I)
_GUESS_TLDS = (".com", ".ai", ".io", ".co", ".dev", ".app", ".tech")


def _site_candidates(name: str, results: list[dict]) -> list[str]:
    out: list[str] = []
    for r in results:
        host = admission.host_of(r.get("url"))
        if not host or admission.bogus_host(host, name) or not admission.host_fits_name(name, host):
            continue
        root = f"https://{host}"
        if root not in out:
            out.append(root)
    return out[:3]


def _guess_domains(name: str, *, india: bool = False) -> list[str]:
    """The domains a startup called `name` most likely owns. Only for distinctive names
    (a key of six letters or more): 'zero.com' says nothing about a startup called Zero."""
    key = admission.name_key(name)
    full = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(key) < 6 or not key.isascii():
        return []
    tlds = (".in",) + _GUESS_TLDS if india else _GUESS_TLDS
    out = [f"https://{key}{t}" for t in tlds]
    if full != key:
        out += [f"https://{full}{t}" for t in (".com", ".ai", ".io")]
    out += [f"https://get{key}.com", f"https://try{key}.com", f"https://{key}hq.com"]
    return list(dict.fromkeys(out))


async def _verify_site(http, name: str, url: str) -> tuple[str | None, str]:
    """(final url, note) when the page answers and names the company; (None, why) else."""
    try:
        r = await http.get(url)
    except Exception as e:  # noqa: BLE001 — a dead guess is the normal case
        return None, f"unreachable: {type(e).__name__}"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    final = admission.host_of(str(r.url))
    if not final or admission.bogus_host(final, name) or not admission.host_fits_name(name, final):
        return None, f"redirects to {final}"
    page = r.text[:400_000]
    text = re.sub(r"<[^>]+>", " ", page)
    if _PARKED.search(text[:20_000]) or bot_wall(text):
        return None, "parked, for sale or a bot wall"
    stem = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(stem) >= 3 and stem in re.sub(r"[^a-z0-9]", "", text.lower()):
        return f"https://{final}", "named on their own page"
    return None, "page does not name the company"


def find_website(company_id: int, *, keyless: bool = True, use_search: bool = True) -> dict:
    """Find and verify one company's own website. {website} or {error}."""
    import asyncio

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        if c.website:
            return {"company": c.name, "website": c.website, "skipped": "already has one"}
        name = c.name
        india = (c.hq_region == "india")
        hint = (c.description or c.hiring_claim or c.funding_evidence or "")[:80]
    cands: list[str] = []
    search_error = None
    if use_search:
        query = f"{name} official website" + (f" — {hint}" if hint and len(hint) > 20 else "")
        hit = web.search(query, limit=8, only=("exa-mcp",) if keyless else None)
        if hit.get("error") and not hit.get("results"):
            search_error = f"search: {hit['error'][:160]}"
        cands += _site_candidates(name, hit.get("results") or [])
    from_search = set(cands)
    cands += [u for u in _guess_domains(name, india=india) if u not in cands]
    if not cands:
        _mark(company_id, "website", search_error or "name too generic to guess a domain, no search hit")
        return {"company": name, "error": search_error or "no candidate site"}

    async def check() -> list:
        from jobhunter.scrapers.base import gather_limited, make_client

        async with make_client() as http:
            return await gather_limited([_verify_site(http, name, u) for u in cands], limit=6)

    try:
        checks = asyncio.run(check())
    except Exception as e:  # noqa: BLE001
        log.warning("find_website %s: check failed: %s", name, e)
        _mark(company_id, "website", f"check failed: {type(e).__name__}")
        return {"company": name, "error": f"check failed: {e}"}
    # candidates are in preference order (search hits first), so the first good one wins
    good = next(((u, r) for u, r in zip(cands, checks) if r and r[0]), None)
    with get_session() as session:
        c = session.get(Company, company_id)
        if good is None:
            seen = [f"{u}: {r[1]}" for u, r in zip(cands, checks) if r and not r[1].startswith("unreachable")]
            backoff.fail(c, "website", (search_error + "; " if search_error else "") + ("; ".join(seen) or "no candidate answered"))
            session.add(c)
            session.commit()
            return {"company": name, "error": search_error or "no candidate named the company", "tried": len(cands)}
        url, note = good[1]
        host = admission.host_of(url)
        owner = session.exec(select(Company).where(Company.domain == host, Company.id != company_id)).first()
        if owner is not None and admission.name_key(owner.name) != admission.name_key(name):
            backoff.fail(c, "website", f"{host} already belongs to #{owner.id} {owner.name}", retryable=False)
            session.add(c)
            session.commit()
            return {"company": name, "error": f"{host} belongs to {owner.name}"}
        c.website, c.domain = url, host
        backoff.ok(c, "website")
        how = "search hit" if good[0] in from_search else "domain guess"
        tag = f"[website] {url} — {note} ({how})"
        c.notes = _reopen(c.notes)
        c.notes = f"{c.notes}\n{tag}".strip() if c.notes else tag
        # a hunt that ran on the bare name had nothing to go on; the site is a fresh start
        c.contacts_found_at = None
        session.add(c)
        session.commit()
    return {"company": name, "website": url, "note": note}


def _reopen(notes: str | None) -> str | None:
    """Drop the "[ats] probed: no public board" verdict. It was reached by guessing board
    slugs from the bare name; with a verified website the probe can read the careers page
    instead, so the company goes back in its queue."""
    if not notes:
        return notes
    kept = [ln for ln in notes.splitlines() if not ln.startswith("[ats] probed: no public board")]
    return "\n".join(kept) or None


def find_websites(limit: int = 30, *, keyless: bool = True) -> dict:
    """find_website over the name-only companies, best priority first, skipping backoff.
    `keyless` (the hourly free tick) uses only the hosted Exa MCP, domain guesses and
    direct fetches; while the MCP cools down after repeated failures it uses guesses
    only, and search resumes when the cooldown ends."""
    init_db()
    with get_session() as session:
        pending = [c for c in _queue(session)
                   if not c.website and not c.domain and not c.list_key and backoff.due(c, "website")]
        ids = [c.id for c in pending[:limit]]
    out = {"tried": 0, "found": 0, "missed": 0, "errors": 0, "search_off": False, "samples": []}
    for cid in ids:
        out["tried"] += 1
        # search pauses while the hosted MCP cools down after failures, then resumes —
        # an outage costs a few minutes of domain-guess-only, not the rest of the run
        from jobhunter.research import web

        out["search_off"] = web.mcp_cooling()
        try:
            r = find_website(cid, keyless=keyless, use_search=not out["search_off"])
        except Exception as e:  # noqa: BLE001 — one company must not end the pass
            log.warning("find_website %s failed: %s", cid, e)
            _mark(cid, "website", f"{type(e).__name__}: {str(e)[:120]}")
            out["errors"] += 1
            continue
        if r.get("website"):
            out["found"] += 1
            if len(out["samples"]) < 5:
                out["samples"].append(f"{r['company']} -> {r['website']}")
        else:
            out["missed"] += 1

    log.info("find_websites: %s", out)
    return out


__all__ = ["enrich_company", "enrich_pending", "facts_pending", "facts_from_search",
           "find_website", "find_websites", "bot_wall"]
