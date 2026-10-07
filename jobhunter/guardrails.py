"""Guardrails — one validation layer every extraction passes before it is written.

Scraping was never the problem (Tejmul, 2026-10-07); the FORMAT of what came back was. A
description that read "Blacksmith Raises $45M Series B…", a round whose quote never named
the company, 17,000 GitHub committers all labelled "verified", 5,000 "names" that were
logins, info@ offered as a referrer. So every value a scrape finds now passes through one
function per field before it touches a row, and every field that is written carries:

    value · evidence quote · source URL · source channel · fetched_at · confidence

(`company.field_meta`, and one `extraction_write` row each — what the hourly audit
samples). A value the validators refuse is never silently dropped: it becomes an
`extraction_reject` row with the reason, the source and the channel.

The validators (pure, no I/O — the tests call them directly):

    company_name   not a headline fragment, sentence, job-list phrase, VC or place
                   (admission.clean_name), and not another company's name
    description    ≥ 60 chars, about THIS company, not a bot wall / cookie / JS shell /
                   boilerplate / SEO line / job ad / funding headline / industry label,
                   trimmed to ~600 chars, flagged when not English
    funding_*      stage in the one vocabulary; amount 0.05–5,000 $M and a raise (not
                   revenue, a price or a debt facility); date not in the future and not
                   before founding; investors a clean list of names; valuation above the
                   amount and plausible for the stage; the evidence names the company
    hiring         'verified' only with a role title read off their own careers page or
                   ATS (URL kept); older than 30 days → needs_recheck
    person_name    a person's name, not a handle ("Samrat002", "kbysani-affirm")
    email          syntax, no-reply noise refused; company | personal | generic | other
    affiliation    does the evidence place the person there NOW? verified-current |
                   likely-current | unconfirmed | generic. Only the first two are referrers.

Cross-source agreement raises a field's confidence; a conflict keeps the better-evidenced
value and records the loser as a reject with reason "conflict".

Layering: config + db + admission. No network, no model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from jobhunter import CONFIG, admission
from jobhunter.db import Company, Contact, ExtractionReject, ExtractionWrite, utcnow

_G = CONFIG.get("guardrails") or {}
DESC_MIN = int(_G.get("description_min_chars", 60))
DESC_MAX = int(_G.get("description_max_chars", 600))
AMOUNT_MIN_M = float(_G.get("amount_min_usd_m", 0.05))
AMOUNT_MAX_M = float(_G.get("amount_max_usd_m", 5000))
HIRING_STALE_DAYS = int(_G.get("hiring_stale_days", 30))
COMMIT_RECENT_DAYS = int(_G.get("commit_recent_days", 365))
REFERRERS_WANTED = int(_G.get("referrers_per_company", 3))

# the two labels a referrer must carry to be offered anywhere (network, drafting, gate)
READY = ("verified-current", "likely-current")
AFFILIATIONS = ("verified-current", "likely-current", "unconfirmed", "generic")


@dataclass
class Check:
    """One validator's verdict. `value` is what to store (cleaned) when ok."""

    ok: bool
    value: Any = None
    reason: str = ""
    confidence: str = "medium"        # low | medium | high
    flags: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _no(reason: str, value: Any = None) -> Check:
    return Check(False, value, reason)


def _alnum(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", admission._ascii(text or "").lower())


# ------------------------------------------------------------------ "does this text name the company?"

def names_company(text: str | None, name: str, domain: str | None = None) -> bool:
    """The name (its key, or its first distinctive word) or the domain label appears in
    the text. "RevenueCat" is named by "revenuecat.com" and by "RevenueCat, Inc.".
    """
    if not text or not name:
        return False
    blob = _alnum(text)
    key = admission.name_key(name)
    full = _alnum(name)
    if len(key) >= 3 and key in blob:
        return True
    if len(full) >= 3 and full in blob:
        return True
    first = next((t for t in admission._tokens(name) if t not in admission._CORP), "")
    if len(first) >= 4 and re.search(rf"\b{re.escape(first)}\b", admission._ascii(text).lower()):
        return True
    if domain:
        label = _alnum((admission.registrable(domain) or domain).rsplit(".", 1)[0])
        if len(label) >= 4 and label in blob:
            return True
    return False


# ------------------------------------------------------------------ company name

def company_name(raw: str | None, *, source: str | None = None, headline: bool = False,
                 others: dict[str, int] | None = None, self_id: int | None = None) -> Check:
    """admission.clean_name, plus: not another company's name (`others` maps name_key ->
    company id; a hit on a different id is a collision, not this company)."""
    name, how = admission.clean_name(raw, headline=headline, source=source)
    if not name:
        return _no(how, raw)
    if others is not None:
        owner = others.get(admission.name_key(name))
        if owner is not None and owner != self_id:
            return _no(f"another company's name (#{owner})", raw)
    return Check(True, name, how if how != "unchanged" else "")


# ------------------------------------------------------------------ description

# What a bot wall, a CAPTCHA or a JS-only shell hands back instead of the page. Saved as a
# description it read "Just a moment... Checking your browser" on 18 rows (audit P9).
BOT_WALL = re.compile(
    r"(just a moment|checking your browser|attention required|enable javascript|"
    r"verify you are (?:a )?human|are you a robot|captcha|access denied|request blocked|"
    r"pardon our interruption|ddos protection|cf-browser-verification|cloudflare ray id|"
    r"403 forbidden|you have been blocked|unusual traffic|please turn javascript on|"
    r"this site can.t be reached|page not found|404 not found|performing security verification|"
    r"(?:please )?try again later|couldn.t load|something went wrong|an error occurred)",
    re.I,
)
_COOKIE_JS = re.compile(
    r"(we use cookies|this (?:web)?site uses cookies|cookie (?:policy|settings|preferences|consent)|"
    r"accept (?:all )?cookies|javascript (?:is )?(?:required|disabled)|you need to enable|"
    r"requires javascript|browser (?:is )?not supported|update your browser|loading\.\.\.$)", re.I)
_BOILERPLATE = re.compile(
    r"(all rights reserved|©|\bcopyright \d{4}|privacy policy|terms of (?:service|use)|"
    r"^(?:sign in|log ?in|sign up|skip to (?:main )?content)\b|"
    r"\b(?:subscribe to our newsletter)\b)", re.I)
_SEO = re.compile(
    r"(^(?:home\s*[|»:-]|welcome to\b)|\bofficial (?:site|website)\b|^top \d+\b|"
    r"\btop \d+ (?:best|startups?|companies|tools|apps|alternatives)\b|"
    r"\bbest\b.{0,40}\b(?:in|of) 20\d\d\b|click here|read more\b|learn more\s*$|»)", re.I)
_JOB_AD_STRONG = re.compile(
    r"\b(apply now|job description|responsibilities\s*:|requirements\s*:|qualifications\s*:|"
    r"we(?:'re| are) looking for an?\b|years of (?:professional )?experience|"
    r"we(?:'re| are) hiring|join our team as)\b", re.I)
_JOB_AD_SOFT = re.compile(r"\b(full[- ]time|part[- ]time|internship|salary|you will|the role|"
                          r"candidates?|benefits include|equity and)\b", re.I)
_FUNDING_HEADLINE = re.compile(
    r"\b(raises|raised|secures|secured|lands|bags|closes|closed|nabs|announces)\b.{0,60}"
    r"(\$|€|£|₹|\bmillion\b|\bbillion\b|\bseries [a-k]\b|\bseed\b|\bround\b)", re.I)
# a company-directory industry label: "Avendar is a Software Development company."
_INDUSTRY_LABEL = re.compile(r"^[^.]{0,80}?\bis an? (?:(?:[A-Z][\w&,/-]*|and|&)\s*){1,10}(?:company|firm|business)\.?$")
_SUBJECT = re.compile(r"^(?P<subj>[A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})\s+"
                      r"(?:is|are|was|builds|provides|offers|develops|helps|makes|enables|creates|"
                      r"raised|raises|announced|launched)\b")
_EN_WORDS = {"the", "and", "of", "to", "for", "a", "an", "is", "with", "that", "we", "our", "in",
             "on", "your", "by", "from", "it", "their", "you", "are", "platform", "helps", "build"}


def _english(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(1 for ch in letters if ch.isascii()) / len(letters) < 0.85:
        return False
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) >= 10 and sum(1 for w in words if w in _EN_WORDS) / len(words) < 0.04:
        return False
    return True


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end >= DESC_MIN else cut.rsplit(" ", 1)[0] + "…").strip()


def bot_wall(text: str | None) -> bool:
    """Is this page text a bot wall / error shell rather than the company's own words?"""
    if not text:
        return False
    return bool(BOT_WALL.search(text[:1500])) and len(text) < 4000


def description(text: str | None, *, name: str, domain: str | None = None, own_site: bool = False,
                strict: bool = True, known: dict[str, int] | None = None, self_id: int | None = None) -> Check:
    """A description of THIS company in its own or a reader's words.

    `own_site`: read off the company's own pages, where "We build…" needs no name.
    `strict`: a search snippet or a directory blurb must name the company; existing rows
    of unknown origin (revalidation) are only refused when they name someone else.
    """
    if not text or not str(text).strip():
        return _no("empty")
    t = re.sub(r"\s+", " ", str(text)).strip().strip("|").strip()
    t = re.sub(r"\|\s*null$", "", t).strip()
    if bot_wall(t) or BOT_WALL.search(t[:300]):
        return _no("a bot wall or error page, not their words", t)
    if _COOKIE_JS.search(t):
        return _no("cookie / JavaScript banner", t)
    if _BOILERPLATE.search(t):
        return _no("site boilerplate (nav, legal, sign-in)", t)
    if _SEO.search(t) or t.count("|") >= 2 or t.count(" · ") >= 2:
        return _no("SEO line or page title, not a description", t)
    if _JOB_AD_STRONG.search(t) or len(_JOB_AD_SOFT.findall(t)) >= 3:
        return _no("a job ad, not a description of the company", t)
    if _FUNDING_HEADLINE.search(t):
        return _no("a funding headline, not a description", t)
    if _INDUSTRY_LABEL.match(t):
        return _no("an industry label, not a description", t)
    if len(t) < DESC_MIN:
        return _no(f"too short ({len(t)} chars, need {DESC_MIN})", t)
    named = names_company(t, name, domain)
    if not named:
        m = _SUBJECT.match(t)
        subj = m.group("subj") if m else None
        other = bool(subj) and not names_company(subj, name, domain) and \
            subj.split()[0].lower() not in {"we", "our", "the", "it", "this", "a", "an", "your", "every"}
        # on their own site "Cursor is…" is their product; off it, a sentence whose subject
        # is another name is about someone else — for stored rows of unknown origin only
        # when that name is another company we hold (`known`: name_key -> id)
        if other and not own_site:
            owner = (known or {}).get(admission.name_key(subj)) if known is not None else None
            if strict or (owner is not None and owner != self_id):
                return _no(f"about another company ('{subj}')", t)
        if strict and not own_site:
            return _no("does not name the company (not from their own site)", t)
    out = Check(True, _trim(t, DESC_MAX), confidence="high" if (own_site or named) else "low")
    if len(t) > DESC_MAX:
        out.flags.append("trimmed")
    if not _english(t):
        out.flags.append("non-english")
    return out


# ------------------------------------------------------------------ funding

def funding_stage(text: str | None) -> Check:
    stage = admission.normalize_stage(text)
    if not stage:
        return _no(f"'{str(text)[:40]}' is not a stage in the vocabulary", text)
    return Check(True, stage)


_DEBT = re.compile(r"\b(debt|credit facility|credit line|line of credit|loan|venture debt|"
                   r"debt financing|non-dilutive|grant)\b", re.I)
_REVENUE = re.compile(r"\b(revenue|arr|run[- ]rate|gmv|turnover|sales of|bookings|"
                      r"transaction volume|processed)\b", re.I)


def funding_amount(amount: Any, evidence: str | None, *, labelled_debt: bool = False) -> Check:
    """$M raised in one round: 0.05–5,000, and the quote reads as an equity raise."""
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return _no("not a number", amount)
    if not AMOUNT_MIN_M <= amt <= AMOUNT_MAX_M:
        return _no(f"${amt:,.2f}M is outside {AMOUNT_MIN_M}–{AMOUNT_MAX_M:,.0f} $M", amt)
    ev = evidence or ""
    if ev and _DEBT.search(ev) and not labelled_debt and not re.search(r"\bequity\b", ev, re.I):
        return _no("a debt facility or grant, not an equity raise", amt)
    if not admission.credible_amount(amt, evidence):
        return _no("not a raise (revenue, a price or a volume)", amt)
    if ev and _REVENUE.search(ev) and not re.search(r"\b(raised|raises|round|series|seed|funding)\b", ev, re.I):
        return _no("revenue or volume, not a raise", amt)
    return Check(True, round(amt, 3))


def _parse_date(raw: Any) -> tuple[datetime | None, str | None]:
    """(datetime, normalised text) for 'YYYY', 'YYYY-MM', 'YYYY-MM-DD', an ISO stamp or
    an RFC-822 feed date. (None, None) when unreadable."""
    if raw is None:
        return None, None
    if isinstance(raw, datetime):
        return raw, raw.strftime("%Y-%m-%d")
    s = str(raw).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            return None, None
        text = f"{y:04d}" + (f"-{mo:02d}" if m.group(2) else "") + (f"-{d:02d}" if m.group(3) else "")
        return dt, text
    try:
        dt = parsedate_to_datetime(s).replace(tzinfo=None)
        return dt, dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return None, None


def funding_date(raw: Any, *, founded_year: int | None = None, now: datetime | None = None) -> Check:
    dt, text = _parse_date(raw)
    if dt is None:
        return _no("unreadable date", raw)
    now = now or utcnow()
    if dt > now + timedelta(days=1):
        return _no(f"{text} is in the future", raw)
    if dt.year < 1990:
        return _no(f"{text} is implausibly old", raw)
    if founded_year and dt.year < int(founded_year):
        return _no(f"{text} is before the company was founded ({founded_year})", raw)
    return Check(True, text)


_INVESTOR_JUNK = re.compile(
    r"(\$|€|£|₹|%|https?://|@|\b(?:raised|raises|led|leads|participat\w*|including|among|said|"
    r"announced|round|funding|million|billion|investment from|backed by|with|which|who|"
    r"its|their|our|the company)\b)", re.I)
_INVESTOR_GENERIC = {"others", "and others", "angel investors", "angels", "existing investors",
                     "undisclosed", "undisclosed investors", "various", "investors", "several angels",
                     "strategic investors", "family offices", "unnamed investors", "n/a", "none"}


def investors(value: Any) -> Check:
    """A clean list of investor names from a JSON list, a list, or 'A, B and C'."""
    items: list[str] = []
    if value is None:
        return _no("empty")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed if isinstance(parsed, list) else value
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(value, str):
        items = re.split(r"\s*(?:,|;|\band\b|&)\s*", value)
    elif isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, str):
                items.append(v)
    clean: list[str] = []
    dropped: list[str] = []
    for raw in items:
        n = re.sub(r"\s+", " ", raw).strip(" .,'\"")
        n = re.sub(r"^(?:and|by|from)\s+", "", n, flags=re.I)
        n = re.sub(r"\s+and others$", "", n, flags=re.I)
        if not n:
            continue
        low = n.lower()
        if (low in _INVESTOR_GENERIC or len(n) < 2 or len(n) > 60 or len(n.split()) > 6
                or _INVESTOR_JUNK.search(n) or not re.search(r"[A-Za-z]", n)
                or (n[0].islower() and not re.fullmatch(r"[a-z0-9]{2,8}", n))):
            dropped.append(n)
            continue
        if n not in clean:
            clean.append(n)
    if not clean:
        return _no("no investor names, only phrases: " + "; ".join(dropped[:3]), value)
    out = Check(True, clean)
    if dropped:
        out.flags.append("dropped: " + "; ".join(dropped[:5]))
    return out


# valuation ceilings by stage ($M) — above these a "valuation" is a misread or a different company
_VAL_CEILING = {"Pre-seed": 150, "Seed": 600, "Series A": 3000, "Series B": 12000, "Series C": 40000,
                "Series D+": 1_000_000, "Public": 5_000_000, "Bootstrapped": 20000}


def valuation(val: Any, *, amount: float | None = None, total: float | None = None,
              stage: str | None = None, evidence: str | None = None, name: str | None = None,
              domain: str | None = None) -> Check:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return _no("not a number", val)
    if v < 0.5:
        return _no(f"${v}M is not a valuation", v)
    raised = max(x for x in (amount or 0, total or 0, 0))
    if raised and v <= raised:
        return _no(f"valuation ${v:,.0f}M is not above the ${raised:,.1f}M raised", v)
    st = admission.normalize_stage(stage)
    if st and v > _VAL_CEILING.get(st, 1_000_000):
        return _no(f"${v:,.0f}M is implausible for {st}", v)
    if evidence and name and not names_company(evidence, name, domain):
        return _no("the valuation quote does not name the company", v)
    return Check(True, round(v, 2))


_STRUCTURED = re.compile(r"^(?:YC \w+ \d{4} \(yc-oss|- (?:Total Funding|Annual Revenue|Series|Seed|Pre)|"
                         r"\[list\]|hand-picked)", re.I)


def funding_evidence(evidence: str | None, *, name: str, domain: str | None = None,
                     channel: str | None = None) -> Check:
    """The quote a round was read from must name the company — unless it is a structured
    record about this company already (a YC directory row, a company card, the list YAML)."""
    if not evidence or not str(evidence).strip():
        if channel in ("list", "manual", "yc", "feed"):
            return Check(True, None, confidence="medium")
        return _no("no evidence quote")
    ev = str(evidence).strip()
    if names_company(ev, name, domain):
        return Check(True, ev[:400], confidence="high")
    if channel in ("list", "manual", "yc", "card") or _STRUCTURED.match(ev) or \
            re.search(r"\b0M - |\(\d{4}-\d{2}-\d{2}\):|\b(?:round|series [a-k]|seed)\s*\(\d{4}-\d{2}-\d{2}\)", ev, re.I):
        return Check(True, ev[:400], confidence="medium")
    # their own pages speak in the first person: "We have raised $258M", "our $14M seed"
    if re.search(r"\b(?:we(?:'ve| have)? (?:raised|closed|secured|announced)|we're (?:backed|funded)|"
                 r"(?:announcing|closed|close) our|our (?:\$[\d.]+\s*[mbk]?\w*\s+)?(?:seed|series [a-k]|round|investors))\b",
                 ev, re.I) and channel not in ("feed", "exa-api", "exa-mcp", "search"):
        return Check(True, ev[:400], confidence="medium")
    return _no("the funding quote does not name the company", ev[:200])


# ------------------------------------------------------------------ hiring

_ATS_HOST = re.compile(
    r"(^|\.)(greenhouse\.io|grnh\.se|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|"
    r"recruitee\.com|breezy\.hr|bamboohr\.com|keka\.com|darwinbox\.\w+|zohorecruit\.\w+|zoho\.(?:com|in)|"
    r"freshteam\.com|teamtailor\.com|personio\.(?:de|com)|jobvite\.com|icims\.com|myworkdayjobs\.com|"
    r"rippling\.com|dover\.com|homerun\.co|join\.com|pinpointhq\.com|recruiterbox\.com|"
    r"applytojob\.com|jazzhr\.com|workatastartup\.com)$", re.I)
_AGGREGATOR = re.compile(r"(^|\.)(linkedin\.com|indeed\.\w+|glassdoor\.\w+|naukri\.com|instahyre\.com|"
                         r"wellfound\.com|angel\.co|remoteok\.\w+|weworkremotely\.com|otta\.com|"
                         r"cutshort\.io|internshala\.com|foundit\.in|monster\.\w+)$", re.I)


def own_careers_url(url: str | None, *, domain: str | None = None) -> bool:
    """Their own domain, or a hosted ATS board — never an aggregator."""
    host = admission.host_of(url)
    if not host or _AGGREGATOR.search(host):
        return False
    if _ATS_HOST.search(host):
        return True
    if domain:
        reg = admission.registrable(domain) or domain
        return host == domain or host.endswith("." + reg) or host == reg
    return False


def careers_page(url: str | None, *, domain: str | None = None) -> Check:
    if not url or not re.match(r"^https?://", str(url)):
        return _no("not a URL", url)
    host = admission.host_of(url)
    if host and _AGGREGATOR.search(host):
        return _no(f"{host} is a job aggregator, not their careers page", url)
    if domain and not own_careers_url(url, domain=domain):
        return Check(True, url, confidence="low", flags=["off-domain"])
    return Check(True, url, confidence="high")


def hiring(status: str | None, *, roles: list[str] | None = None, careers_url: str | None = None,
           checked_at: datetime | None = None, domain: str | None = None,
           now: datetime | None = None) -> Check:
    """'verified' only with a role title read off their own careers page or ATS, with the
    URL kept. A verdict older than HIRING_STALE_DAYS is downgraded to needs_recheck."""
    if status != "verified":
        return Check(True, status)
    titles = [r for r in (roles or []) if isinstance(r, str) and len(r.strip()) >= 3]
    if not titles:
        return Check(False, "careers_only", "verified without a role title on their own page")
    if not careers_url:
        return Check(False, "careers_only", "verified without the careers URL it was read from")
    if not own_careers_url(careers_url, domain=domain):
        return Check(False, "careers_only", f"roles read off {admission.host_of(careers_url)}, not their own page")
    now = now or utcnow()
    if checked_at and checked_at < now - timedelta(days=HIRING_STALE_DAYS):
        return Check(False, "needs_recheck", f"checked {(now - checked_at).days} days ago (> {HIRING_STALE_DAYS})")
    return Check(True, "verified", confidence="high")


# ------------------------------------------------------------------ people

_NAME_WORD = re.compile(r"^[^\W\d_][^\W\d_'’.\-]*(?:[.'’\-][^\W\d_]+)*\.?$")
_CJK = re.compile(r"^[一-鿿぀-ヿ가-힯]{2,4}$")
_NOT_PERSON = {"github", "bot", "admin", "team", "support", "root", "unknown", "user", "dev", "devs",
               "developer", "engineering", "hello", "info", "contact", "careers", "hiring", "hr",
               "recruiting", "sales", "the", "inc", "labs", "ltd", "llc", "technologies", "official",
               "anonymous", "noreply", "test", "null", "none", "staff", "ops", "founders", "office"}


def person_name(raw: str | None) -> Check:
    """A person's name — two or more words of letters (or a CJK name) — not a login."""
    if not raw or not str(raw).strip():
        return _no("empty")
    n = re.sub(r"\s+", " ", str(raw)).strip(" .,;:-_'\"")
    if "@" in n or "http" in n.lower() or "/" in n:
        return _no("an address or a link, not a name", raw)
    n = re.sub(r"\s+", " ", re.sub(r"[^\w\s.'’\-]", " ", n)).strip(" .-")   # emoji and symbols go
    if not n:
        return _no("symbols, not a name", raw)
    if any(ch.isdigit() for ch in n):
        return _no("a handle (digits), not a name", raw)
    if _CJK.match(n):
        return Check(True, n, confidence="medium")
    words = n.split(" ")
    if len(words) < 2:
        return _no("one word — a handle or a first name only", raw)
    if len(words) > 5 or len(n) > 60:
        return _no("too long to be a name", raw)
    if any(not _NAME_WORD.match(w) for w in words):
        return _no("a handle or symbols, not a name", raw)
    if {w.lower().strip(".") for w in words} & _NOT_PERSON:
        return _no("not a person", raw)
    if all(w.islower() for w in words) or all(w.isupper() and len(w) > 2 for w in words):
        # "theo michel" is a name typed in lowercase — keep it, titled
        n = " ".join(w.capitalize() for w in words)
        return Check(True, n, confidence="low", flags=["re-cased"])
    return Check(True, n, confidence="high")


_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_NOISE_LOCAL = re.compile(r"^(noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster|abuse|"
                          r"bounce|unsubscribe|notifications?|example|test)$", re.I)
GENERIC_LOCAL = {
    "info", "hello", "hi", "hey", "hola", "contact", "contactus", "contact-us", "support", "sales", "team",
    "press", "media", "pr", "marketing", "partnerships", "partnership", "partners", "business", "biz",
    "admin", "office", "help", "helpdesk", "enquiry", "enquiries", "inquiry", "inquiries", "careers",
    "career", "jobs", "job", "hr", "recruiting", "recruitment", "recruit", "talent", "hiring", "people",
    "peopleops", "founders", "legal", "privacy", "security", "billing", "accounts", "ops", "operations",
    "connect", "community", "feedback", "mail", "email", "service", "care", "customercare", "invest",
    "investors", "ir", "social", "events", "newsletter", "apply", "general", "webmaster", "dpo",
    "grievance", "compliance", "finance", "payments", "studio", "design", "growth", "success",
}
FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "yahoo.co.in", "yahoo.co.uk", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "protonmail.ch", "pm.me", "fastmail.com", "fastmail.fm", "hey.com", "aol.com",
    "gmx.com", "gmx.de", "gmx.net", "web.de", "yandex.ru", "yandex.com", "mail.ru", "qq.com", "163.com",
    "126.com", "foxmail.com", "sina.com", "naver.com", "hanmail.net", "rediffmail.com", "zoho.com",
    "tutanota.com", "tuta.io", "duck.com", "anonaddy.me", "anonaddy.com", "posteo.de", "hotmail.co.uk",
    "outlook.in", "live.in", "users.noreply.github.com",
}


def email(raw: str | None, *, domain: str | None = None) -> Check:
    """Syntax and noise; value = (address, kind) where kind is company | personal | generic | other."""
    if not raw:
        return _no("empty")
    e = str(raw).strip().strip(".").lower()
    if not _EMAIL.match(e):
        return _no("bad syntax", raw)
    local, host = e.split("@", 1)
    if _NOISE_LOCAL.match(local) or host.endswith("noreply.github.com") or host in ("example.com", "localhost"):
        return _no("a no-reply or relay address", raw)
    if local in GENERIC_LOCAL or local.split("+")[0] in GENERIC_LOCAL:
        return Check(True, (e, "generic"), confidence="high")
    if domain:
        reg = admission.registrable(domain.lower()) or domain.lower()
        if host == domain.lower() or host == reg or host.endswith("." + reg):
            return Check(True, (e, "company"), confidence="high")
    if host in FREEMAIL:
        return Check(True, (e, "personal"), confidence="medium")
    return Check(True, (e, "other"), confidence="low")


_EX = re.compile(r"\b(ex[- ]|former(?:ly)?|previously|alum(?:ni|nus)?|past)\b", re.I)


@dataclass
class Affiliation:
    label: str                 # verified-current | likely-current | unconfirmed | generic
    source: str                # org-member | profile-company | team-page | company-address | recent-commit | linkedin-headline | none
    evidence: str


def employer_matches(claim: str | None, name: str, domain: str | None = None) -> bool:
    """Their own words ("@acme", "Acme Inc.", "acme.com") name this company — and do
    not say they used to."""
    if not claim or _EX.search(claim):
        return False
    return names_company(claim.lstrip("@"), name, domain)


def affiliation(*, company_name: str, domain: str | None = None, email_kind: str | None = None,
                email_addr: str | None = None, org_member: bool = False,
                employer_claim: str | None = None, last_commit_at: datetime | None = None,
                commit_repo: str | None = None, team_page: str | None = None,
                linkedin_headline: str | None = None, published_at: str | None = None,
                now: datetime | None = None) -> Affiliation:
    """Does the evidence place this person at this company NOW?

    verified-current  public member of their GitHub org; their own profile names the
                      company; named on the company's own site; a company address with
                      a commit to the company's own repo inside 12 months
    likely-current    a commit to the company's own repo inside 12 months; a public
                      LinkedIn headline naming the company; a company address published
                      on the web
    unconfirmed       anything else — an old commit, a fork, an address at another company
    generic           a shared inbox (info@, careers@) — a company contact, never a referrer
    """
    now = now or utcnow()
    if email_kind == "generic":
        return Affiliation("generic", "none", f"shared inbox {(email_addr or '').split('@')[0]}@".strip())
    recent = bool(last_commit_at and last_commit_at >= now - timedelta(days=COMMIT_RECENT_DAYS))
    when = last_commit_at.strftime("%Y-%m-%d") if last_commit_at else None
    if org_member:
        return Affiliation("verified-current", "org-member", "public member of the company's GitHub org")
    if employer_matches(employer_claim, company_name, domain):
        return Affiliation("verified-current", "profile-company", f"their profile says '{employer_claim[:80]}'")
    if team_page:
        return Affiliation("verified-current", "team-page", f"named on their own site: {team_page[:160]}")
    if email_kind == "company" and recent:
        return Affiliation("verified-current", "company-address",
                           f"company address, committed to {commit_repo or 'their repo'} on {when}")
    if recent:
        return Affiliation("likely-current", "recent-commit", f"committed to {commit_repo or 'their repo'} on {when}")
    if linkedin_headline and names_company(linkedin_headline, company_name, domain) and not _EX.search(linkedin_headline):
        return Affiliation("likely-current", "linkedin-headline", f"public LinkedIn headline: {linkedin_headline[:120]}")
    if email_kind == "company" and published_at:
        return Affiliation("likely-current", "company-address", f"company address published at {published_at[:160]}")
    why = []
    if employer_claim:
        why.append(f"profile says '{employer_claim[:60]}'")
    if email_kind == "other" and email_addr:
        why.append(f"address at {email_addr.split('@')[-1]}")
    if last_commit_at and not recent:
        why.append(f"last commit {when}")
    if email_kind == "company" and not why:
        why.append("company address, no date on it")
    return Affiliation("unconfirmed", "none", "; ".join(why) or "no evidence they work there now")


def address_confidence(*, source: str, email_kind: str | None, last_commit_at: datetime | None = None,
                       smtp: str | None = None, profile_email: bool = False, now: datetime | None = None) -> str:
    """The ADDRESS tier (will it bounce?): verified | pattern-guessed | scraped.

    A commit author address is real when it was used inside 12 months, and a profile's
    public address is current by definition; an old commit address may be dead, so it is
    `scraped` — and so falls under the guessed-address cap, never the open one."""
    if smtp == "verified":
        return "verified"
    if smtp == "pattern-guessed" or source == "pattern":
        return "pattern-guessed"
    if email_kind == "generic":
        return "scraped"
    now = now or utcnow()
    if profile_email:
        return "verified"
    if last_commit_at and last_commit_at >= now - timedelta(days=COMMIT_RECENT_DAYS):
        return "verified"
    return "scraped"


# ------------------------------------------------------------------ writing: provenance and rejects

def reject(session, *, field: str, value: Any, reason: str, company_id: int | None = None,
           contact_id: int | None = None, source: str | None = None, channel: str | None = None) -> None:
    """Keep a refused value. Never raises past the caller's own transaction."""
    session.add(ExtractionReject(
        company_id=company_id, contact_id=contact_id, field=field,
        value=(json.dumps(value, default=str) if not isinstance(value, str) else value)[:600] if value is not None else None,
        reason=reason[:240], source=(source or "")[:400] or None, channel=channel))


def _meta(company: Company) -> dict:
    try:
        data = json.loads(company.field_meta or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def meta_of(company: Company, name: str) -> dict:
    return _meta(company).get(name) or {}


def _rank(entry: dict) -> tuple:
    """How well-evidenced a value is: confidence, then how many sources agree, then newer."""
    conf = {"high": 2, "medium": 1, "low": 0}.get(entry.get("confidence") or "medium", 1)
    return (conf, int(entry.get("sources") or 1), str(entry.get("at") or ""))


def record(session, company: Company, name: str, value: Any, *, evidence: str | None = None,
           source: str | None = None, channel: str | None = None, confidence: str = "medium",
           flags: list[str] | None = None, contact_id: int | None = None) -> None:
    """Note a written value's provenance on the row and in extraction_write."""
    meta = _meta(company)
    meta[name] = {"value": value if not isinstance(value, float) else round(value, 3),
                  "evidence": (evidence or "")[:300] or None, "source": (source or "")[:300] or None,
                  "channel": channel, "at": utcnow().isoformat(timespec="seconds"),
                  "confidence": confidence, "sources": 1, **({"flags": flags} if flags else {})}
    company.field_meta = json.dumps(meta, default=str)
    session.add(ExtractionWrite(
        company_id=company.id, contact_id=contact_id, field=name,
        value=str(value)[:600] if value is not None else None, evidence=(evidence or "")[:400] or None,
        source=(source or "")[:400] or None, channel=channel, confidence=confidence))


def _agree(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 0.1 * max(abs(float(a)), abs(float(b)), 0.01)
    return str(a).strip().lower() == str(b).strip().lower()


def put(session, company: Company, name: str, value: Any, *, attr: str | None = None,
        evidence: str | None = None, source: str | None = None, channel: str | None = None,
        confidence: str = "medium", flags: list[str] | None = None, replace: bool = False) -> str:
    """Write one validated value with cross-source reconciliation.

    empty field      → written                                   ("written")
    same value       → the field's confidence goes up a source   ("agreed")
    different value  → `replace` (a newer round) or the better-evidenced of the two
                       wins; the loser is kept as a reject "conflict" ("replaced"/"kept")
    """
    attr = attr or name
    old = getattr(company, attr)
    if old in (None, "", "[]"):
        setattr(company, attr, value)
        record(session, company, name, value, evidence=evidence, source=source, channel=channel,
               confidence=confidence, flags=flags)
        return "written"
    if _agree(old, value):
        meta = _meta(company)
        entry = meta.get(name) or {"value": old, "confidence": "medium", "sources": 1}
        entry["sources"] = int(entry.get("sources") or 1) + 1
        if entry["sources"] >= 2 and entry.get("confidence") != "high":
            entry["confidence"] = "high"
        entry.setdefault("agreed", [])
        if channel and channel not in entry["agreed"]:
            entry["agreed"] = (entry["agreed"] + [channel])[-5:]
        meta[name] = entry
        company.field_meta = json.dumps(meta, default=str)
        return "agreed"
    new_entry = {"confidence": confidence, "sources": 1, "at": utcnow().isoformat()}
    old_entry = _meta(company).get(name) or {"confidence": "medium", "sources": 1, "at": ""}
    if replace or _rank(new_entry) > _rank(old_entry):
        reject(session, company_id=company.id, field=name, value=old,
               reason=f"conflict: replaced by {str(value)[:60]} ({'newer' if replace else 'better evidenced'})",
               source=old_entry.get("source"), channel=old_entry.get("channel"))
        setattr(company, attr, value)
        record(session, company, name, value, evidence=evidence, source=source, channel=channel,
               confidence=confidence, flags=flags)
        return "replaced"
    reject(session, company_id=company.id, field=name, value=value,
           reason=f"conflict: kept {str(old)[:60]} (better evidenced)", source=source, channel=channel)
    return "kept"


# ------------------------------------------------------------------ field writers the scrapers call

def set_description(session, company: Company, text: str | None, *, source: str | None = None,
                    channel: str | None = None, own_site: bool = False, strict: bool = True,
                    replace: bool = False) -> bool:
    """Validate and write a description. True when it was written (or agreed)."""
    if not text:
        return False
    chk = description(text, name=company.name, domain=company.domain, own_site=own_site, strict=strict)
    if not chk:
        reject(session, company_id=company.id, field="description", value=text, reason=chk.reason,
               source=source, channel=channel)
        return False
    cur = company.description
    cur_chk = description(cur, name=company.name, domain=company.domain, strict=False) if cur else None
    cur_ok = bool(cur_chk)
    if cur_ok and not replace:
        return False      # a good one is already there; first good value stands
    if cur and not cur_ok:
        # the value on the row would not pass today: kept as a reject, replaced
        reject(session, company_id=company.id, field="description", value=cur, reason=cur_chk.reason,
               source="on record", channel=(meta_of(company, "description") or {}).get("channel"))
        company.description = None
    out = put(session, company, "description", chk.value, evidence=chk.value, source=source, channel=channel,
              confidence=chk.confidence, flags=chk.flags, replace=True)
    return out in ("written", "replaced")


def set_funding(session, company: Company, fund: dict, *, source: str | None = None,
                channel: str | None = None, fill_only: bool = False) -> dict:
    """Validate a round {stage, amount_usd_m, total_usd_m, announced, investors,
    valuation_usd_m, evidence|evidence_quote, public} and write what passes.

    A round announced later than the one on record replaces stage/amount/date (that is a
    new round, not a conflict); otherwise blanks are filled and disagreements reconciled.
    `fill_only` never replaces a value already on the row (the hand-picked list's)."""
    out: dict = {"written": [], "rejected": []}
    if not fund:
        return out
    evidence = fund.get("evidence_quote") or fund.get("evidence")
    ev = funding_evidence(evidence, name=company.name, domain=company.domain, channel=channel)
    if not ev:
        reject(session, company_id=company.id, field="funding", value={k: v for k, v in fund.items() if v},
               reason=ev.reason, source=source, channel=channel)
        out["rejected"].append(("funding", ev.reason))
        return out
    conf = ev.confidence

    def bad(name: str, value: Any, reason: str) -> None:
        reject(session, company_id=company.id, field=name, value=value, reason=reason, source=source, channel=channel)
        out["rejected"].append((name, reason))

    date = None
    if fund.get("announced"):
        d = funding_date(fund["announced"], founded_year=company.founded_year)
        if d:
            date = d.value
        else:
            bad("funding_announced", fund["announced"], d.reason)
    newer = bool(date and company.funding_announced and str(date) > str(company.funding_announced)
                 and not fill_only)
    # an earlier round than the one on record says nothing about the current one
    older = bool(date and company.funding_announced and str(date)[:7] < str(company.funding_announced)[:7])
    stage = None
    if fund.get("public"):
        stage = "Public"
    elif fund.get("stage"):
        s = funding_stage(fund["stage"])
        if s:
            stage = s.value
        else:
            bad("funding_stage", fund["stage"], s.reason)
    amount = None
    if fund.get("amount_usd_m") not in (None, ""):
        a = funding_amount(fund["amount_usd_m"], evidence, labelled_debt="debt" in str(fund.get("stage") or "").lower())
        if a:
            amount = a.value
        else:
            bad("funding_amount_usd_m", fund["amount_usd_m"], a.reason)

    def write(name: str, value: Any, attr: str | None = None, *, round_field: bool = False) -> None:
        if value is None:
            return
        cur = getattr(company, attr or name)
        if (fill_only or (older and round_field)) and cur not in (None, "", "[]"):
            return
        r = put(session, company, name, value, attr=attr, evidence=evidence, source=source,
                channel=channel, confidence=conf, replace=newer and round_field)
        if r in ("written", "replaced", "agreed"):
            out["written"].append(name)

    if newer:
        # a later round: the old stage/amount belonged to the previous one
        if stage is None:
            company.funding_stage = None
        if amount is None:
            company.funding_amount_usd_m = None
    write("funding_stage", stage, round_field=True)
    write("funding_amount_usd_m", amount, round_field=True)
    write("funding_announced", date, round_field=True)
    if any(n in out["written"] for n in ("funding_stage", "funding_amount_usd_m", "funding_announced")):
        if newer or not company.funding_evidence:
            company.funding_evidence = (evidence or company.funding_evidence or "")[:600] or None
    if fund.get("total_usd_m") not in (None, ""):
        try:
            total = float(fund["total_usd_m"])
            if AMOUNT_MIN_M <= total <= 20000:
                write("funding_total_usd_m", round(total, 3))
            else:
                bad("funding_total_usd_m", total, f"${total:,.0f}M total is implausible")
        except (TypeError, ValueError):
            bad("funding_total_usd_m", fund["total_usd_m"], "not a number")
    if fund.get("investors"):
        inv = investors(fund["investors"])
        if inv:
            write("funding_investors", json.dumps(inv.value))
        else:
            bad("funding_investors", fund["investors"], inv.reason)
    if fund.get("valuation_usd_m") not in (None, ""):
        v = valuation(fund["valuation_usd_m"], amount=amount or company.funding_amount_usd_m,
                      total=company.funding_total_usd_m, stage=stage or company.funding_stage,
                      evidence=fund.get("valuation_evidence") or evidence, name=company.name, domain=company.domain)
        if v:
            write("valuation_usd_m", v.value)
            if "valuation_usd_m" in out["written"]:
                company.valuation_evidence = (fund.get("valuation_evidence") or evidence or "")[:400] or company.valuation_evidence
        else:
            bad("valuation_usd_m", fund["valuation_usd_m"], v.reason)
    return out


def set_valuation(session, company: Company, value: Any, evidence: str | None, *, source: str | None = None,
                  channel: str | None = None) -> bool:
    v = valuation(value, amount=company.funding_amount_usd_m, total=company.funding_total_usd_m,
                  stage=company.funding_stage, evidence=evidence, name=company.name, domain=company.domain)
    if not v:
        reject(session, company_id=company.id, field="valuation_usd_m", value=value, reason=v.reason,
               source=source, channel=channel)
        return False
    r = put(session, company, "valuation_usd_m", v.value, evidence=evidence, source=source, channel=channel,
            confidence="high" if evidence else "medium")
    if r in ("written", "replaced"):
        company.valuation_evidence = (evidence or "")[:400] or None
    return r in ("written", "replaced", "agreed")


def set_hiring(session, company: Company, status: str, *, roles: list[str] | None, careers_url: str | None,
               evidence: str | None, source: str | None = None, channel: str | None = None) -> str:
    """Write a hiring verdict through the hiring validator. Returns the status stored."""
    chk = hiring(status, roles=roles, careers_url=careers_url, checked_at=utcnow(), domain=company.domain)
    final = chk.value
    if not chk.ok:
        reject(session, company_id=company.id, field="hiring_status", value=status, reason=chk.reason,
               source=careers_url or source, channel=channel)
    if careers_url:
        cu = careers_page(careers_url, domain=company.domain)
        if cu:
            company.careers_url = cu.value
        else:
            reject(session, company_id=company.id, field="careers_url", value=careers_url, reason=cu.reason,
                   source=source, channel=channel)
    company.hiring_status = final
    company.hiring_evidence = evidence
    company.hiring_roles = json.dumps(roles) if roles else None
    company.hiring_checked_at = utcnow()
    record(session, company, "hiring_status", final, evidence=evidence, source=careers_url or source,
           channel=channel, confidence=chk.confidence if chk.ok else "low")
    return final


# ------------------------------------------------------------------ contacts

def vet_contact(session, *, company: Company, name: str | None, email_addr: str | None, source: str,
                channel: str | None = None, url: str | None = None, handle: str | None = None,
                name_source: str | None = None, org_member: bool = False, employer_claim: str | None = None,
                last_commit_at: datetime | None = None, commit_repo: str | None = None,
                team_page: str | None = None, linkedin_headline: str | None = None,
                published_at: str | None = None, smtp: str | None = None,
                profile_email: bool = False) -> dict | None:
    """The guarded fields for a new Contact, or None when nothing usable is left (no
    valid address AND no valid name). Refused parts become extraction_reject rows."""
    out: dict = {"handle": handle, "name_source": name_source}
    nm = person_name(name) if name else None
    if nm is not None and not nm:
        reject(session, company_id=company.id, field="contact.name", value=name, reason=nm.reason,
               source=url, channel=channel or source)
        if not handle and name and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_.]{0,38}", name.strip()):
            out["handle"] = name.strip()      # the login it was, kept apart
    out["name"] = nm.value if nm else None
    kind = None
    if email_addr:
        em = email(email_addr, domain=company.domain)
        if em:
            out["email"], kind = em.value
        else:
            reject(session, company_id=company.id, field="contact.email", value=email_addr, reason=em.reason,
                   source=url, channel=channel or source)
            out["email"] = None
    else:
        out["email"] = None
    if not out["email"] and not out["name"]:
        return None
    out["email_kind"] = kind
    aff = affiliation(company_name=company.name, domain=company.domain, email_kind=kind,
                      email_addr=out["email"], org_member=org_member, employer_claim=employer_claim,
                      last_commit_at=last_commit_at, commit_repo=commit_repo, team_page=team_page,
                      linkedin_headline=linkedin_headline, published_at=published_at)
    out.update({"affiliation": aff.label, "affiliation_source": aff.source,
                "affiliation_evidence": aff.evidence[:300], "affiliation_checked_at": utcnow(),
                "last_commit_at": last_commit_at})
    if out["email"]:
        out["confidence"] = address_confidence(source=source, email_kind=kind, last_commit_at=last_commit_at,
                                               smtp=smtp, profile_email=profile_email)
    else:
        out["confidence"] = "scraped"
    if kind == "generic":
        # a shared inbox reaches the company, never a person who can refer
        out["role_class"], out["referral_rank"] = "other", 9
    return out


_GH_LOGIN = re.compile(r"github\.com/([A-Za-z0-9][A-Za-z0-9\-]{0,38})/?$")
_PUBLISHED_AT = re.compile(r"address published at (\S+)")


def relabel_contact(p: Contact, company: Company | None) -> dict:
    """The guarded labels for a stored contact, from the evidence already on the row."""
    out: dict = {}
    domain = company.domain if company else None
    cname = company.name if company else ""
    login = None
    m = _GH_LOGIN.search((p.github or "").rstrip("/"))
    if m:
        login = m.group(1)
    if p.name:
        nm = person_name(p.name)
        if not nm:
            out["name"] = None
            out["_name_reject"] = nm.reason
            out["handle"] = login or (p.name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_.]{0,38}", p.name) else None)
        elif nm.value != p.name:
            out["name"] = nm.value
    if login and not p.handle:
        out.setdefault("handle", login)
    kind = None
    if p.email:
        em = email(p.email, domain=domain)
        if not em:
            out["email"] = None
            out["_email_reject"] = em.reason
        else:
            kind = em.value[1]
    out["email_kind"] = kind
    notes = p.research_notes or ""
    published = _PUBLISHED_AT.search(notes)
    published_at = published.group(1) if published else None
    on_site = bool(published_at and domain and (admission.host_of(published_at) or "").endswith(domain))
    name_ok = bool((out.get("name", p.name)))
    team_page = None
    if p.source == "site" and name_ok:
        team_page = company.website if company else "their site"
    elif p.source == "web" and on_site and name_ok:
        team_page = published_at
    if p.source == "site" and not published_at:
        published_at = company.website if company else None
    headline = p.role if (p.source == "linkedin-public" or (p.linkedin and not p.github)) else None
    aff = affiliation(company_name=cname, domain=domain, email_kind=kind, email_addr=p.email,
                      employer_claim=p.employer_claim, last_commit_at=p.last_commit_at,
                      team_page=team_page, linkedin_headline=headline,
                      published_at=published_at if kind == "company" else None)
    # a label the GitHub pass already proved (membership, a dated commit) is not lowered
    keep = p.affiliation in READY and p.affiliation_source in ("org-member", "recent-commit",
                                                               "company-address", "profile-company")
    if not keep:
        out.update({"affiliation": aff.label, "affiliation_source": aff.source,
                    "affiliation_evidence": aff.evidence[:300]})
    if p.email and p.source == "github" and "email" not in out:
        out["confidence"] = address_confidence(source="github", email_kind=kind, last_commit_at=p.last_commit_at)
    if kind == "generic":
        out["role_class"], out["referral_rank"] = "other", 9
    return out


def is_ready(contact: Contact) -> bool:
    """May this person be offered as a referrer? Confirmed current, not an inbox."""
    return (contact.affiliation in READY) and contact.email_kind != "generic"


# ------------------------------------------------------------------ completeness

PARTS = ("description", "funding_stage", "funding_amount", "funding_date", "investors", "valuation",
         "hiring_verified", "careers_url", "referrers", "referrer_email")


def completeness(c: Company, *, referrers: int = 0, referrers_with_email: int = 0) -> tuple[int, list[str]]:
    """(0–100, the parts still missing). Every part weighs the same."""
    have = {
        "description": bool(c.description and len(c.description) >= DESC_MIN),
        "funding_stage": bool(c.funding_stage),
        "funding_amount": bool(c.funding_amount_usd_m or c.funding_total_usd_m),
        "funding_date": bool(c.funding_announced),
        "investors": bool(c.funding_investors and c.funding_investors not in ("[]", "")),
        "valuation": bool(c.valuation_usd_m),
        "hiring_verified": c.hiring_status == "verified",
        "careers_url": bool(c.careers_url),
        "referrers": referrers >= REFERRERS_WANTED,
        "referrer_email": referrers_with_email >= 1,
    }
    missing = [p for p in PARTS if not have[p]]
    return round(100 * (len(PARTS) - len(missing)) / len(PARTS)), missing


def _can_refer_classes() -> list[str]:
    roles = ((CONFIG.get("targeting") or {}).get("people") or {}).get("roles") or []
    keys = [r["key"] for r in roles if r.get("refer")]
    return keys or ["founder", "senior_engineer", "engineer", "eng_manager", "tech_recruiter", "recruiter"]


def referrer_counts(session) -> dict[int, tuple[int, int]]:
    """company_id -> (confirmed-current referrers, of them with an address)."""
    from sqlmodel import col, select

    rows = session.exec(select(Contact.company_id, Contact.email).where(
        col(Contact.affiliation).in_(READY), col(Contact.role_class).in_(_can_refer_classes()),
        (col(Contact.email_kind).is_(None)) | (Contact.email_kind != "generic"))).all()
    out: dict[int, list[int]] = {}
    for cid, em in rows:
        e = out.setdefault(cid, [0, 0])
        e[0] += 1
        e[1] += 1 if em else 0
    return {k: (v[0], v[1]) for k, v in out.items()}


def refresh_completeness(session=None) -> dict:
    """Recompute completeness for every active company. Cheap: one pass, one aggregate."""
    from sqlmodel import col, select

    from jobhunter.db import get_session

    own = session is None
    session = session or get_session()
    try:
        counts = referrer_counts(session)
        changed = complete = 0
        for c in session.exec(select(Company).where(col(Company.status).is_(None))).all():
            r, re_ = counts.get(c.id, (0, 0))
            score, missing = completeness(c, referrers=r, referrers_with_email=re_)
            miss = json.dumps(missing)
            if c.completeness != score or c.completeness_missing != miss:
                c.completeness, c.completeness_missing = score, miss
                session.add(c)
                changed += 1
            complete += score == 100
        session.commit()
        return {"changed": changed, "complete": complete}
    finally:
        if own:
            session.close()


def sweep_stale_hiring(session) -> int:
    """'verified' older than HIRING_STALE_DAYS → needs_recheck (hiring_verify re-reads it)."""
    from sqlmodel import col, select

    cutoff = utcnow() - timedelta(days=HIRING_STALE_DAYS)
    n = 0
    for c in session.exec(select(Company).where(Company.hiring_status == "verified",
                                                col(Company.hiring_checked_at) < cutoff)).all():
        reject(session, company_id=c.id, field="hiring_status", value="verified",
               reason=f"stale: checked {c.hiring_checked_at:%Y-%m-%d} (> {HIRING_STALE_DAYS} days) — needs_recheck",
               source=c.careers_url, channel="guard")
        c.hiring_status = "needs_recheck"
        session.add(c)
        n += 1
    session.commit()
    return n


def label_unlabelled(session, limit: int = 2000) -> int:
    """Any contact a writer saved without the guardrails' labels (an old process, a manual
    insert) gets them from the evidence on its row — never left unjudged."""
    from sqlmodel import col, select

    rows = session.exec(select(Contact).where(col(Contact.affiliation).is_(None)).limit(limit)).all()
    for p in rows:
        plan = relabel_contact(p, session.get(Company, p.company_id))
        if "_name_reject" in plan:
            reject(session, company_id=p.company_id, contact_id=p.id, field="contact.name", value=p.name,
                   reason=plan["_name_reject"], source=p.github or p.linkedin, channel=p.source)
        if "_email_reject" in plan:
            reject(session, company_id=p.company_id, contact_id=p.id, field="contact.email", value=p.email,
                   reason=plan["_email_reject"], channel=p.source)
        for k, v in plan.items():
            if not k.startswith("_"):
                setattr(p, k, v)
        p.affiliation_checked_at = utcnow()
        session.add(p)
    session.commit()
    return len(rows)


def sweep() -> dict:
    """The guard step each tick runs first: stale hiring downgraded, unlabelled contacts
    labelled, completeness fresh — so every fill queue orders by what is missing now."""
    from jobhunter.db import get_session, init_db

    init_db()
    with get_session() as session:
        stale = sweep_stale_hiring(session)
        labelled = label_unlabelled(session)
        out = refresh_completeness(session)
    return {"hiring_stale": stale, "contacts_labelled": labelled, **out}


__all__ = [
    "Check", "Affiliation", "READY", "AFFILIATIONS", "PARTS", "names_company", "company_name", "description",
    "bot_wall", "funding_stage", "funding_amount", "funding_date", "investors", "valuation", "funding_evidence",
    "careers_page", "own_careers_url", "hiring", "person_name", "email", "affiliation", "employer_matches",
    "address_confidence", "reject", "record", "put", "set_description", "set_funding", "set_valuation",
    "set_hiring", "vet_contact", "is_ready", "completeness", "refresh_completeness", "referrer_counts",
    "sweep", "sweep_stale_hiring", "relabel_contact", "label_unlabelled",
]
