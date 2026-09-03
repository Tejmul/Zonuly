"""Normalization: relevance filters, dedup fingerprints, and salary -> INR LPA.

Salary parsing is regex-first because most boards state pay in a machine-readable
form; the LLM is only asked about the ones regex can't crack, which keeps a 4B
model on an 8 GB Mac from becoming the bottleneck.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from jobhunter import CONFIG

log = logging.getLogger(__name__)

_S = CONFIG["search"]
MIN_LPA = float(_S.get("min_lpa", 24))
MAX_LPA = float(_S.get("max_lpa", 200))

RATES_TO_INR = {
    "INR": 1.0,
    "USD": float(_S.get("usd_to_inr", 88)),
    "EUR": float(_S.get("eur_to_inr", 95)),
    "GBP": float(_S.get("gbp_to_inr", 111)),
    "CAD": float(_S.get("cad_to_inr", 64)),
    "AUD": float(_S.get("aud_to_inr", 58)),
    "SGD": float(_S.get("sgd_to_inr", 65)),
}

LOCATIONS_OK = [l.lower() for l in _S.get("locations_ok", [])]

# ---------------------------------------------------------------- relevance

_ROLE_WORDS = re.compile(
    r"\b(engineer|engineering|developer|programmer|sde|swe|scientist|architect|"
    r"ml|ai|llm|genai|nlp|research|backend|back[- ]end|full[- ]?stack|platform|"
    r"infrastructure|founding)\b",
    re.I,
)
# senior-only / non-engineering / wrong-discipline titles a new grad shouldn't chase
_TITLE_EXCLUDE = re.compile(
    r"\b(director|vp|vice president|head of|chief|principal|staff|distinguished|fellow|"
    r"manager|lead engineer|engineering manager|em\b|"
    r"sales|marketing|recruit|hr\b|people ops|finance|account|legal|counsel|"
    r"designer|design|ux|ui designer|content|writer|copywriter|"
    r"support|success|solutions? (?:consultant|architect)|"
    r"mechanical|electrical|civil|chemical|hardware|firmware|asic|rf\b|optical|"
    r"technician|field|quality|qa manager|"
    r"apprentice|co[- ]?op)\b",
    re.I,
)
# Internships are NOT excluded any more. Most target companies hire freshers through
# a paid internship that converts to a PPO, so an internship with a real stipend is
# the front door, not a distraction (Tejmul, 2026-09-03). What we still drop is the
# unpaid/low-paid kind, and that is decided by the stipend bar in targeting.py, not
# by the title.
_INTERNSHIP_RE = re.compile(r"\b(intern|internship|trainee|industrial training|summer analyst)\b", re.I)
_SENIORITY = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|l[4-9]\b|iii|iv|v\b)\b", re.I)
# We are freshers (Tejmul, 2026-09-03): senior-titled roles are still scraped and kept —
# a company hiring seniors is a company hiring — but they are flagged (Job.is_senior)
# and the queue, counts and page put fresher roles first. The hard drop is opt-in.
EXCLUDE_SENIOR = bool(_S.get("exclude_senior", False))


def is_internship(title: str | None, description: str | None = None) -> bool:
    """Does this posting describe an internship rather than a full-time role?"""
    if title and _INTERNSHIP_RE.search(title):
        return True
    head = (description or "")[:600]
    return bool(head and _INTERNSHIP_RE.search(head) and re.search(r"\bstipend\b", head, re.I))


def title_relevant(title: str) -> bool:
    """Cheap pre-store filter — is this even the right kind of job?"""
    if not title:
        return False
    if _TITLE_EXCLUDE.search(title):
        return False
    if EXCLUDE_SENIOR and is_senior(title):
        return False
    return bool(_ROLE_WORDS.search(title))


def location_ok(location: str | None, remote: bool = False) -> bool:
    if remote:
        return True
    if not location:
        return True  # unknown location: keep, the scorer will judge it
    loc = location.lower()
    return any(ok in loc for ok in LOCATIONS_OK)


def is_senior(title: str) -> bool:
    return bool(_SENIORITY.search(title or ""))


# "Remote from anywhere" — the company says, in its own words, that it hires across borders
# without sponsorship. This is the ideal company for the currency-gap thesis (MOTIV §2), so
# it is a first-class signal, not a note. Matched on location and description; the phrase
# has to be theirs, so a plain "remote" (which usually means remote-in-country) is not enough.
_ANYWHERE = re.compile(
    r"(work from anywhere|remote[- ]?(?:first|only|native)|fully[- ]remote|100%[- ]remote|"
    r"remote\s*[\(\-–—:,]?\s*(?:global|globally|worldwide|world[- ]wide|anywhere|international(?:ly)?|"
    r"any (?:country|location|time ?zone))|anywhere in the world|any(?:where)? in the world|"
    r"globally distributed|distributed team|hire (?:from )?anywhere|hiring (?:globally|worldwide|internationally)|"
    r"time ?zone[- ]agnostic|async[- ]first|no (?:visa|work permit|sponsorship)[^.]{0,30}(?:required|needed|necessary)|"
    r"work from (?:any(?:where| country)|your (?:home )?country)|remote \(?(?:emea|apac|latam|india|asia|europe)\)?)",
    re.I,
)


def remote_anywhere(location: str | None, description: str | None = None) -> bool:
    """Does the posting itself say the role can be done from any country?"""
    if location and _ANYWHERE.search(location):
        return True
    return bool(description) and bool(_ANYWHERE.search(description[:6000]))


# Several boards serve UTF-8 bytes that were already decoded once as cp1252,
# which turns "Kraków" into "KrakÃ³w". These are the tell-tale sequences.
_MOJIBAKE = re.compile(r"[ÃÂÐÑ][\x80-\xbf-¿–—’“”]|â€|Ã©|Ã¶|Ã¼|Ã¡|Ã³")


def fix_mojibake(text: str | None) -> str | None:
    """Repair double-encoded UTF-8. Returns the input unchanged if it isn't broken."""
    if not text or not _MOJIBAKE.search(text):
        return text
    for encoding in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        # only accept the repair if it actually removed the damage
        if not _MOJIBAKE.search(repaired):
            return repaired
    return text


def fingerprint(company: str, title: str) -> str:
    """Stable key for the same role posted to several sources."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
    # strip location/req-id suffixes boards tack onto titles
    t = re.sub(r"\s*[\(\[\-–|].*$", "", title or "")
    return hashlib.sha1(f"{norm(company)}|{norm(t)}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- salary

@dataclass
class Salary:
    min_lpa: float | None = None
    max_lpa: float | None = None
    currency: str | None = None
    raw: str | None = None

    def ok(self) -> bool:
        return self.min_lpa is not None or self.max_lpa is not None


_SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "₨": "INR"}
_CODES = {
    "usd": "USD", "us$": "USD", "inr": "INR", "rs": "INR", "rs.": "INR", "eur": "EUR",
    "gbp": "GBP", "cad": "CAD", "aud": "AUD", "sgd": "SGD", "c$": "CAD", "a$": "AUD", "s$": "SGD",
}
_CUR_PAT = r"(?:\$|₹|€|£|₨|US\$|C\$|A\$|S\$|USD|INR|EUR|GBP|CAD|AUD|SGD|Rs\.?)"
_UNIT_PAT = r"(?:k|K|m|M|mn|MM|million|l|L|lac|lacs|lakh|lakhs|LPA|lpa|cr|crore|crores)"
# comma-grouped form first (185,000 / 30,00,000), then plain digits — the grouped
# alternative must require a comma or "120000" would match as just "120"
_NUM_PAT = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"

_MONEY = re.compile(
    rf"(?P<cur>{_CUR_PAT})?\s*(?P<num>{_NUM_PAT})\s*(?P<unit>{_UNIT_PAT})?\s*(?P<curafter>{_CUR_PAT})?\b",
    re.I,
)
_RANGE = re.compile(
    rf"(?P<cur1>{_CUR_PAT})?\s*(?P<n1>{_NUM_PAT})\s*(?P<u1>{_UNIT_PAT})?\s*"
    rf"(?:-|–|—|to|up to|and)\s*"
    rf"(?P<cur2>{_CUR_PAT})?\s*(?P<n2>{_NUM_PAT})\s*(?P<u2>{_UNIT_PAT})?\s*(?P<curafter>{_CUR_PAT})?",
    re.I,
)

_PER_MONTH = re.compile(r"\b(per month|/\s*mo(?:nth)?\b|monthly|p\.?m\.?)\b", re.I)
_PER_HOUR = re.compile(r"\b(per hour|/\s*h(?:ou)?r\b|hourly|/hr)\b", re.I)
_COMP_CUE = re.compile(
    r"\b(salary|compensation|pay|package|ctc|base|remuneration|stipend|offer|band|range|lpa)\b", re.I
)
_INR_CUE = re.compile(r"(₹|\bINR\b|\bRs\.?\b|\bLPA\b|\blakhs?\b|\blacs?\b|\bcrores?\b)", re.I)
_NOT_MONEY = re.compile(
    r"\b401\s*\(?\s*k\s*\)?\b|\b403\s*\(?\s*b\s*\)?\b|\b24\s*/\s*7\b|\b(?:19|20)\d{2}\b(?!\s*(?:k|K|m|M|lpa|LPA))",
    re.I,
)


def _to_number(num: str) -> float | None:
    try:
        return float(num.replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _annual_lpa(value: float, unit: str | None, currency: str, period: str) -> float | None:
    """Convert one parsed money token to INR lakhs per annum."""
    u = (unit or "").lower()
    if u in ("k",):
        value *= 1_000
    elif u in ("m", "mn", "mm", "million"):
        value *= 1_000_000
    elif u in ("l", "lac", "lacs", "lakh", "lakhs", "lpa"):
        value *= 100_000
        currency = "INR"
    elif u in ("cr", "crore", "crores"):
        value *= 10_000_000
        currency = "INR"

    if period == "month":
        value *= 12
    elif period == "hour":
        value *= 2080

    inr = value * RATES_TO_INR.get(currency, RATES_TO_INR["USD"])
    lpa = inr / 100_000
    # sanity band: below ~2 LPA it's not an annual salary, above ~2000 LPA it's a typo or a valuation
    return lpa if 2.0 <= lpa <= 2000.0 else None


def _currency_of(tok: str | None, context: str) -> str:
    if tok:
        t = tok.strip().lower()
        if t in _SYMBOLS:
            return _SYMBOLS[t]
        if tok.strip() in _SYMBOLS:
            return _SYMBOLS[tok.strip()]
        if t in _CODES:
            return _CODES[t]
    return "INR" if _INR_CUE.search(context) else "USD"


def parse_salary(*texts: str | None) -> Salary:
    """Regex salary extraction over (salary_raw, title, description) in priority order."""
    for text in texts:
        if not text:
            continue
        sal = _parse_one(text)
        if sal.ok():
            return sal
    return Salary()


def _parse_one(text: str) -> Salary:
    # only look at compensation-ish neighbourhoods of long documents
    windows: list[str] = []
    if len(text) > 400:
        for m in _COMP_CUE.finditer(text):
            windows.append(text[max(0, m.start() - 120) : m.start() + 220])
        if not windows:
            return Salary()
    else:
        windows = [text]

    for win in windows[:12]:
        # "401(k)" is a retirement plan, not a $401k salary; "24/7", "365 days" and
        # years are numbers with units that are not money either
        win = _NOT_MONEY.sub(" ", win)
        period = "month" if _PER_MONTH.search(win) else "hour" if _PER_HOUR.search(win) else "year"

        m = _RANGE.search(win)
        if m and (m.group("cur1") or m.group("cur2") or m.group("u1") or m.group("u2") or m.group("curafter")):
            cur = _currency_of(m.group("cur1") or m.group("cur2") or m.group("curafter"), win)
            n1, n2 = _to_number(m.group("n1")), _to_number(m.group("n2"))
            # "120-180k" — the unit on the second number applies to both
            u1 = m.group("u1") or m.group("u2")
            u2 = m.group("u2") or m.group("u1")
            lo = _annual_lpa(n1, u1, cur, period) if n1 is not None else None
            hi = _annual_lpa(n2, u2, cur, period) if n2 is not None else None
            if lo and hi and lo <= hi:
                return Salary(round(lo, 1), round(hi, 1), cur, m.group(0).strip()[:120])
            if lo or hi:
                v = lo or hi
                return Salary(round(v, 1), round(v, 1), cur, m.group(0).strip()[:120])

        for m in _MONEY.finditer(win):
            if not (m.group("cur") or m.group("unit") or m.group("curafter")):
                continue
            n = _to_number(m.group("num"))
            if n is None:
                continue
            cur = _currency_of(m.group("cur") or m.group("curafter"), win)
            v = _annual_lpa(n, m.group("unit"), cur, period)
            if v:
                return Salary(round(v, 1), round(v, 1), cur, m.group(0).strip()[:120])
    return Salary()


# ---------------------------------------------------------------- LLM fallback

_SALARY_SYSTEM = """You extract compensation from job postings. You only report numbers that
actually appear in the text. If the posting states no pay figure, you say so. JSON only."""

_SALARY_PROMPT = """Job title: {title}
Company: {company}
Location: {location}

POSTING (excerpt):
---
{text}
---

Extract the annual base salary range. Rules:
- Only use figures explicitly present. Never estimate or guess a market rate.
- "competitive", "market rate", equity-only, or no figure -> found = false.
- Convert to annual (multiply monthly by 12, hourly by 2080).
- Report the number and currency AS WRITTEN, not converted.

Reply with exactly:
{{"found": true|false, "min": number|null, "max": number|null, "currency": "USD"|"INR"|"EUR"|"GBP"|"CAD"|"AUD"|"SGD"|null, "raw": "the exact text you read it from"|null}}"""


def llm_salary(title: str, company: str, location: str | None, description: str | None) -> Salary:
    """Ask the local model only when regex found nothing but the text talks about pay."""
    from jobhunter import llm

    text = (description or "")[:6000]
    if not _COMP_CUE.search(text):
        return Salary()

    data = llm.chat_json(
        _SALARY_PROMPT.format(
            title=title, company=company, location=location or "unspecified", text=text
        ),
        _SALARY_SYSTEM,
        temperature=0.0,
        alias="cheap",
        purpose="salary",
        default=None,
    )
    if not isinstance(data, dict) or not data.get("found"):
        return Salary()

    cur = (data.get("currency") or "USD").upper()
    if cur not in RATES_TO_INR:
        cur = "USD"
    lo_raw, hi_raw = data.get("min"), data.get("max")
    lo = _annual_lpa(float(lo_raw), None, cur, "year") if isinstance(lo_raw, (int, float)) else None
    hi = _annual_lpa(float(hi_raw), None, cur, "year") if isinstance(hi_raw, (int, float)) else None
    if not (lo or hi):
        return Salary()
    lo = lo or hi
    hi = hi or lo
    return Salary(round(lo, 1), round(hi, 1), cur, (data.get("raw") or "")[:120] or None)


def salary_in_range(sal: Salary) -> bool:
    """Does this pay meet the user's floor? Unknown salary passes — we don't discard on silence."""
    if not sal.ok():
        return True
    top = sal.max_lpa or sal.min_lpa or 0
    return top >= MIN_LPA


# ---------------------------------------------------------------- stipend & PPO

_STIPEND_CUE = re.compile(r"\b(stipend|intern(?:ship)? (?:pay|compensation|salary)|paid internship)\b", re.I)
_PPO_CUE = re.compile(
    r"\b(ppo|pre[- ]?placement offer|full[- ]time offer|on conversion|convert(?:s|ed|ing)? to (?:a )?full[- ]time|"
    r"post[- ]conversion|conversion (?:ctc|package|salary)|return offer)\b",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?\n])\s+")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class PayFacts:
    """The two numbers company targeting runs on, each with the sentence it came from.

    Null means "the posting does not say" — never zero, never a guess. A company with
    no stated pay grades `unknown` and waits for research; it is not rejected.
    """

    stipend_inr_month: int | None = None
    stipend_evidence: str | None = None
    ppo_lpa: float | None = None
    ppo_evidence: str | None = None
    ppo_source: str | None = None      # "ppo" (stated as a conversion offer) | "salary" (the full-time band)


def _sentences(text: str, limit: int = 400) -> list[str]:
    out = []
    for raw in _SENT_SPLIT.split(text):
        sent = _WHITESPACE.sub(" ", raw).strip()
        if 8 < len(sent) < 400:
            out.append(sent)
        if len(out) >= limit:
            break
    return out


def _monthly_inr(sentence: str) -> tuple[int, str] | None:
    """(INR per month, the money token) from a sentence, or None."""
    monthly = bool(_PER_MONTH.search(sentence))
    for m in _MONEY.finditer(sentence):
        if not (m.group("cur") or m.group("unit") or m.group("curafter")):
            continue
        n = _to_number(m.group("num"))
        if n is None:
            continue
        cur = _currency_of(m.group("cur") or m.group("curafter"), sentence)
        unit = (m.group("unit") or "").lower()
        value = n
        if unit == "k":
            value *= 1_000
        elif unit in ("l", "lac", "lacs", "lakh", "lakhs", "lpa"):
            value *= 100_000
            cur = "INR"
        elif unit in ("m", "mn", "mm", "million"):
            value *= 1_000_000
        inr = value * RATES_TO_INR.get(cur, RATES_TO_INR["INR"])
        if not monthly:
            # an annual figure in a stipend sentence ("₹6,00,000 per year internship")
            if inr >= 600_000:
                inr /= 12
            else:
                continue  # a bare number with no period is not a stipend claim
        # sanity band: ₹2k–₹10L a month. Outside it, the number is not a stipend.
        if 2_000 <= inr <= 1_000_000:
            return int(round(inr)), m.group(0).strip()[:60]
    return None


def parse_pay(*texts: str | None) -> PayFacts:
    """Read the internship stipend and the PPO / full-time package out of a posting.

    Regex only and evidence-linked: every number carries the sentence it was read
    from, so the dashboard and the knowledge graph can show *why* a company is tier1.
    """
    facts = PayFacts()
    for text in texts:
        if not text:
            continue
        for sent in _sentences(text):
            if facts.stipend_inr_month is None and _STIPEND_CUE.search(sent):
                hit = _monthly_inr(sent)
                if hit:
                    facts.stipend_inr_month, _ = hit
                    facts.stipend_evidence = sent[:300]
            if facts.ppo_lpa is None and _PPO_CUE.search(sent):
                sal = _parse_one(sent)
                if sal.ok():
                    facts.ppo_lpa = sal.max_lpa or sal.min_lpa
                    facts.ppo_evidence = sent[:300]
                    facts.ppo_source = "ppo"
            if facts.stipend_inr_month is not None and facts.ppo_lpa is not None:
                return facts
    return facts
