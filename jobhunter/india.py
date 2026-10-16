"""India: the ₹20 LPA pay floor, and discovery of underrated Bengaluru / Hyderabad startups.

Tejmul, 2026-10-08: "Even in India, the companies which pay less than 20 lakhs, remove
them. As a fresher I need a good amount of companies. Scrape a good amount of underrated
startups in India — Hyderabad and Bengaluru."

PAY FLOOR (targeting.india.min_lpa, ₹20 LPA)
    An India company (Bengaluru / Hyderabad, India HQ, or the ★ India list) is BELOW THE
    BAR when the best evidence of full-time fresher / early-career pay is under the floor.
    The evidence, highest wins: company.ppo_lpa, its non-senior postings' stated full-time
    band (salary_max, else salary_min) and stated PPO, and the hand-picked list's ctc upper
    bound. ANY evidence at or above the floor keeps it. Internship stipends are not
    full-time pay. Unknown pay is not below the bar ("pay unknown"). A figure under
    implausible_below_lpa is a misparse, not evidence.
    Below the bar = company.status "below_pay_bar" with a status_reason quoting the
    evidence — hidden, never deleted, re-checked every tick (new evidence brings it back).

DISCOVERY (tick.india_discovery_per_tick new companies a tick)
    Keyless sources, every candidate through admission.admit (name cleanup, dedup,
    overrated) and admitted ONLY with a quote that places it in Bengaluru or Hyderabad
    (guardrails.india_city — the sentence must name the company and the city):

      rss        Indian funding-news feeds (Inc42, Entrackr, YourStory, ET, Indian Startup
                 News, StartupTalky, BusinessLine, Tech in Asia); an item whose feed text
                 names no city gets its article read through Jina (bounded per run)
      search     DuckDuckGo via Jina Reader (web.KEYLESS) — "<city>-based <sector> startup
                 raises <stage>" rotated by a stored cursor
      directory  funded-startup directory pages per city and city × industry (FundedIQ),
                 read through Jina — each row names the company, its city, round and date
      yc         the yc-oss directory, companies located in Bengaluru / Hyderabad
      ats        careers boards popular in India (Keka, Darwinbox, Zoho Recruit, Cutshort,
                 Instahyre, Wellfound) found by search

    Recently funded (≤ fresh_months) Pre-seed–Series C is preferred: Series D+, private
    equity and debt rounds are skipped, a dated round older than the window is skipped,
    an undated one is kept. Stage, amount, date and investors are written through
    guardrails.set_funding with the quote. Consumer-goods brands (jewellery, apparel,
    food…) with nothing technical in their description are skipped — they hire no
    engineers. New rows get company.india_found_at, which puts them in every fill queue
    right after the ★ list (db.fill_order).

Layering: pipeline layer — admission, guardrails, harvest, research below; tick, api above.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlmodel import col, select

from jobhunter import CONFIG, admission, guardrails
from jobhunter.db import Company, Job, get_session, get_setting, init_db, set_setting, utcnow

log = logging.getLogger(__name__)

_I = ((CONFIG.get("targeting") or {}).get("india") or {})
MIN_LPA = float(_I.get("min_lpa", 20))
IMPLAUSIBLE_LPA = float(_I.get("implausible_below_lpa", 3))
_D = _I.get("discovery") or {}
FRESH_MONTHS = int(_D.get("fresh_months", 24))
BELOW = "below_pay_bar"
LIST_KEY = "india-star"


# ================================================================== the pay floor

@dataclass
class PayEvidence:
    lpa: float
    quote: str
    kind: str          # company | posting | ppo | list


_CTC_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:L|lpa|lakhs?)?\s*(?:–|-|—|to)\s*₹?\s*(\d+(?:\.\d+)?)\s*(?:L\b|lpa|lakhs?)", re.I)
_CTC_ONE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:L\b|lpa|lakhs?)", re.I)


def ctc_upper(ctc: str | None) -> float | None:
    """The upper bound of a list's CTC text, in LPA: "₹6–18L+" -> 18, "₹30–60L (lead AI
    role)" -> 60, "₹12L" -> 12, "verify on the live JD" -> None."""
    if not ctc:
        return None
    m = _CTC_RANGE.search(ctc)
    if m:
        return max(float(m.group(1)), float(m.group(2)))
    nums = [float(x) for x in _CTC_ONE.findall(ctc)]
    return max(nums) if nums else None


_AGGREGATOR = re.compile(r"levels\.fyi|glassdoor|ambitionbox|payscale|6figr|weekday\.works", re.I)


_INDIA_PLACE = re.compile(r"\b(india|bengaluru|bangalore|hyderabad|secunderabad|karnataka|telangana|pune|mumbai|"
                          r"chennai|gurgaon|gurugram|noida|delhi|ncr)\b", re.I)
_INR_TEXT = re.compile(r"(₹|\binr\b|\brs\.?|\blpa\b|\blakhs?\b|\bcrores?\b)", re.I)
_FOREIGN_MONEY = re.compile(r"(\$|€|£|\busd\b|\beur\b|\bgbp\b|\bcad\b|\bsgd\b|\baud\b)", re.I)
_ABROAD = re.compile(r"\b(us|usa|united states|remote - us|uk|united kingdom|canada|europe|germany|singapore|"
                     r"australia|ca|ny|tx|wa|san francisco|new york|seattle|london|berlin|palo alto|"
                     r"mountain view|austin|boston|toronto|dubai|emea|americas)\b", re.I)


def paid_in_india(j) -> bool:
    """False only when the posting plainly is not India pay: a non-rupee currency, a figure
    in $/€/£, or a location outside India. A posting that says nothing about either counts.
    2026-10-17: US postings ("Remote - US, $200,200–$357,500") read as ₹314 LPA and made
    Samsara and Rubrik look like they clear the Bengaluru floor."""
    currency = (getattr(j, "currency", None) or "").upper()
    raw = getattr(j, "salary_raw", None) or ""
    where = getattr(j, "location", None) or ""
    if currency == "INR" or _INR_TEXT.search(raw):
        return True
    if currency or _FOREIGN_MONEY.search(raw):
        return False
    return not where.strip() or bool(_INDIA_PLACE.search(where)) or not _ABROAD.search(where)


def pay_evidence(*, company_ppo: float | None, company_ppo_evidence: str | None,
                 jobs: list, list_ctc: str | None = None) -> list[PayEvidence]:
    """Every piece of full-time pay evidence for one company. `jobs` are Job rows (or any
    object with title, is_senior, is_internship, salary_min_lpa, salary_max_lpa,
    salary_raw, ppo_lpa). A stipend is never here: it is not full-time pay."""
    out: list[PayEvidence] = []

    def add(v: float | None, quote: str, kind: str) -> None:
        if v is not None and float(v) >= IMPLAUSIBLE_LPA:
            out.append(PayEvidence(float(v), quote[:240], kind))

    q = company_ppo_evidence or ""
    if company_ppo is not None and (_AGGREGATOR.search(q) or _INR_TEXT.search(q) or not _FOREIGN_MONEY.search(q)):
        # a stored figure copied from a dollar POSTING is US pay, not evidence for Bengaluru;
        # an aggregator (levels.fyi) quotes Indian pay in dollars, so it stays
        quote = company_ppo_evidence or f"₹{company_ppo:g} LPA on record"
        # a salary aggregator's number is one data point from an unknown level and city —
        # it may keep a company, it may not hide one (InMobi's "Fellow Engineer" at ₹13.8L)
        add(company_ppo, quote, "aggregate" if _AGGREGATOR.search(quote) else "company")
    for j in jobs:
        if getattr(j, "is_senior", False):
            continue
        title = (getattr(j, "title", "") or "a role")[:80]
        if getattr(j, "ppo_lpa", None):
            add(j.ppo_lpa, f"posting states a ₹{j.ppo_lpa:g} LPA PPO for {title}", "ppo")
        if getattr(j, "is_internship", False):
            continue          # the stipend (and an internship 'salary') is not full-time pay
        band = getattr(j, "salary_max_lpa", None) or getattr(j, "salary_min_lpa", None)
        if band and not paid_in_india(j):
            continue          # a San Francisco posting converted to rupees says nothing about Bengaluru pay
        if band:
            raw = getattr(j, "salary_raw", None)
            add(band, f"posting states {raw} for {title}" if raw else f"posting states ₹{band:g} LPA for {title}",
                "posting")
    up = ctc_upper(list_ctc)
    if up is not None:
        add(up, f"list: {list_ctc}", "list")
    return out


def pay_verdict(evidence: list[PayEvidence], *, floor: float = MIN_LPA) -> tuple[str, PayEvidence | None, str]:
    """('ok' | 'below' | 'unknown', the deciding evidence, the sentence). Any evidence at or
    above the floor keeps the company; with none at all the pay is unknown, which keeps it."""
    if not evidence:
        return "unknown", None, "pay unknown — no stated full-time figure"
    best = max(evidence, key=lambda e: e.lpa)
    if best.lpa >= floor:
        return "ok", best, f"pays ≥ ₹{floor:g} LPA: {best.quote}"
    own = [e for e in evidence if e.kind != "aggregate"]
    if not own:
        return "unknown", best, f"pay unknown — only an aggregator figure, not proof: {best.quote}"
    best = max(own, key=lambda e: e.lpa)
    more = f" (best of {len(own)} figures)" if len(own) > 1 else ""
    return "below", best, f"below the ₹{floor:g}L bar: {best.quote}{more}"


def in_scope(c: Company) -> bool:
    """An India company the floor applies to: a Bengaluru / Hyderabad presence, an India HQ
    (India means those two cities), or the ★ India list."""
    return bool(c.city) or (c.hq_region or "") == "india" or c.list_key == LIST_KEY


def _list_ctc(c: Company) -> str | None:
    if not c.list_meta:
        return None
    try:
        return (json.loads(c.list_meta) or {}).get("ctc")
    except (TypeError, ValueError):
        return None


def apply_pay_floor(*, dry_run: bool = False) -> dict:
    """Hide (or bring back) every India company by the pay floor. Never touches a
    quarantined or merged row; never deletes anything. Idempotent — the tick runs it."""
    init_db()
    out: dict = {"floor_lpa": MIN_LPA, "dry_run": dry_run, "in_scope": 0, "ok": 0, "below": 0, "unknown": 0,
                 "hidden_now": 0, "restored": 0, "hidden_list": [], "restored_list": [], "sample_hidden": []}
    with get_session() as session:
        rows = session.exec(select(Company).where(
            col(Company.status).is_(None) | (Company.status == BELOW))).all()
        rows = [c for c in rows if in_scope(c) or c.status == BELOW]
        ids = [c.id for c in rows]
        jobs_by: dict[int, list[Job]] = {}
        for i in range(0, len(ids), 500):
            for j in session.exec(select(Job).where(col(Job.company_id).in_(ids[i:i + 500]))).all():
                jobs_by.setdefault(j.company_id, []).append(j)
        for c in rows:
            if not in_scope(c):
                state, best, why = "unknown", None, "no longer an India company"
            else:
                out["in_scope"] += 1
                ev = pay_evidence(company_ppo=c.ppo_lpa, company_ppo_evidence=c.ppo_evidence,
                                  jobs=jobs_by.get(c.id, []), list_ctc=_list_ctc(c))
                state, best, why = pay_verdict(ev)
                out[state] += 1
            prev = guardrails.meta_of(c, "pay_floor").get("value")
            changed = False
            if state == "below" and c.status != BELOW:
                out["hidden_now"] += 1
                (out["hidden_list"] if c.list_key else out["sample_hidden"]).append(f"{c.name} — {why}")
                c.status, c.status_reason = BELOW, why[:300]
                changed = True
            elif state == "below" and c.status_reason != why[:300]:
                c.status_reason = why[:300]
                changed = True
            elif state != "below" and c.status == BELOW:
                out["restored"] += 1
                out["restored_list"].append(f"{c.name} — {why}")
                c.status, c.status_reason = None, None
                changed = True
            if changed or prev != state:
                guardrails.record(session, c, "pay_floor", state, evidence=best.quote if best else why,
                                  source=best.kind if best else None, channel="pay-floor",
                                  confidence="high" if best and best.kind in ("posting", "ppo") else "medium")
                session.add(c)
        out["sample_hidden"] = out["sample_hidden"][:25]
        if dry_run:
            session.rollback()
        else:
            session.commit()
    log.info("pay floor: %s", {k: v for k, v in out.items() if not isinstance(v, list)})
    return out


def pay_label(c: Company) -> dict:
    """What the dashboard shows beside an India company: ok / below / unknown + the quote."""
    m = guardrails.meta_of(c, "pay_floor")
    if not m:
        return {"state": None}
    return {"state": m.get("value"), "evidence": m.get("evidence"),
            "label": {"ok": f"pays ≥ ₹{MIN_LPA:g}L", "below": f"below the ₹{MIN_LPA:g}L bar",
                      "unknown": "pay unknown"}.get(m.get("value") or "", None)}


# ================================================================== discovery: shared parsing

_USD = re.compile(r"(?:\$|usd|us\$)\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(billion|million|thousand|mn|bn|m|b|k)?\b", re.I)
_INR = re.compile(r"(?:₹|rs\.?|inr)\s?(\d+(?:[.,]\d+)?)\s*(crore|cr|lakh|lakhs|l)\b", re.I)
_STAGE_TXT = re.compile(r"\b(pre[- ]?seed|seed(?:\s+plus)?|pre[- ]series\s+[a-c]|series\s+[a-k]|angel round|"
                        r"bridge round|private equity|debt|venture debt|ipo|pre-ipo)\b", re.I)
_SKIP_STAGE = re.compile(r"\b(series\s+[d-k]|private equity|venture debt|debt financing|debt round|pre-ipo|ipo|"
                         r"post-ipo|acquires|acquired|acquisition)\b", re.I)
_RAISE = re.compile(r"\b(raises?|raised|secures?|secured|bags?|bagged|lands?|closes?|closed|nets?|picks? up|"
                    r"gets?|receives?|scores?|snags?|mops? up)\b", re.I)
_NOT_YET = re.compile(r"\b(in talks|plans to|set to|eyes|looking to|aims to raise|to raise)\b", re.I)
_HEAD = re.compile(r"^(?P<name>.{2,70}?)\s+(?:raises?|raised|secures?|secured|bags?|bagged|lands?|closes?|closed|"
                   r"nets?|picks? up|gets?|receives?|scores?|snags?|mops? up)\b", re.I)
_LEDBY = re.compile(r"\b(?:led by|from|backed by|participation (?:from|of))\s+([A-Z][^.;]{2,160})")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_MY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(20\d\d)\b", re.I)
_DATE_DMY = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*,?\s+(20\d\d)\b", re.I)
_URL_DATE = re.compile(r"/(20\d\d)/(\d{1,2})/")
_YEAR = re.compile(r"\b(20[12]\d)\b")

# consumer brands that hire no engineers — skipped unless the text is visibly technical
_NON_TECH = re.compile(
    r"\b(jewell?e?ry|diamonds?|apparel|fashion|clothing|footwear|ethnic wear|innerwear|cosmetics?|skincare|"
    r"beauty|personal care|haircare|fragrance|food and beverage|beverages?|restaurants?|cafes?|pizza|bakery|"
    r"snacks?|dairy|tea brand|coffee brand|spirits|liquor|brewery|hospitality|hotels?|real estate|construction|"
    r"furniture|home decor|mattress|sporting goods|ammunition|pet food|ayurved\w*|nutraceutical|d2c|"
    r"direct-to-consumer|wellness brand|consumer goods|kitchen|cookware|eyewear|toys?|lab-grown|watch(?:es)?|"
    r"luxury|handbags?|perfumes?|clinics?|salons?|gyms?|coaching cent(?:er|re)s?)\b", re.I)
_TECH = re.compile(r"\b(ai|artificial intelligence|software|saas|platform|app|apps|technology|tech|data|cloud|"
                   r"api|developer|machine learning|ml|llm|agentic|automation|analytics|cyber\w*|robot\w*|"
                   r"fintech|internet|information technology|digital|infrastructure|semiconductor|chip|"
                   r"satellite|space|drone|quantum|ev|electric|battery|deeptech|healthtech|edtech|"
                   r"logistics tech|marketplace|b2b)\b", re.I)


def non_tech(text: str | None) -> bool:
    """A consumer-goods brand with nothing technical said about it."""
    t = text or ""
    return bool(_NON_TECH.search(t)) and not _TECH.search(t)


def amount_usd_m(text: str | None) -> float | None:
    """The round's size in $M from "$5M", "$1.3 Mn", "USD 7.5 million", "₹32 Cr", "Rs 3.61 crore"."""
    t = text or ""
    m = _USD.search(t)
    if m:
        num = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        if not unit:
            # "$300,000" is dollars; a bare "$5" next to nothing is read as millions
            unit = "dollars" if num >= 1000 else "m"
        mult = {"billion": 1000, "bn": 1000, "b": 1000, "k": 0.001, "thousand": 0.001, "dollars": 1e-6}.get(unit, 1.0)
        v = num * mult
        return round(v, 3) if 0.01 <= v <= 5000 else None
    m = _INR.search(t)
    if m:
        num = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        inr = num * (1e7 if unit in ("crore", "cr") else 1e5)
        v = inr / 88 / 1e6
        return round(v, 3) if 0.01 <= v <= 5000 else None
    return None


def stage_of(text: str | None) -> str | None:
    m = _STAGE_TXT.search(text or "")
    return admission.normalize_stage(m.group(1)) if m else None


def date_of(text: str | None, url: str | None = None) -> str | None:
    """'YYYY-MM' from "March 10, 2026", "21st January 2026", "Sep 2026" or a /2026/08/ URL."""
    for rx in (_DATE_MY, _DATE_DMY):
        m = rx.search(text or "")
        if m:
            return f"{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}"
    m = _URL_DATE.search(url or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None


def too_old(announced: str | None, text: str | None = None) -> bool:
    """A dated round older than the window. Undated: the newest year in the text decides,
    and with no year at all the candidate is kept (preferred, not required)."""
    age = admission.months_old(announced) if announced else None
    if age is None:
        years = [int(y) for y in _YEAR.findall(text or "")]
        if not years:
            return False
        age = admission.months_old(f"{max(years)}-12")
    return age is not None and age > FRESH_MONTHS


def investors_of(text: str | None) -> list[str]:
    m = _LEDBY.search(text or "")
    if not m:
        return []
    chunk = re.split(r"\b(?:to |for |in a |in its |at a |, which|; |\. )", m.group(1))[0]
    parts = re.split(r",\s*|\s+and\s+|\s+&\s+(?=[A-Z])", chunk)
    out = [p.strip(" .") for p in parts if 2 < len(p.strip()) < 60 and p.strip()[:1].isupper()]
    # "from India", "from Bengaluru", "from existing investors" are not investors
    out = [p for p in out if not admission.india_city(p) and not admission.india_other_city(p)
           and p.lower() not in {"india", "the us", "us", "usa", "singapore", "dubai", "uae", "existing investors"}
           and not re.search(r"\b(?:its|existing|angel investors|investors such as)\b", p, re.I)]
    return out[:6]


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])|\s+\|\s+|\n+")


def city_quote(text: str | None, name: str, domain: str | None = None) -> str | None:
    """The first sentence that names the company AND Bengaluru / Hyderabad — the evidence
    quote. None when no single sentence does both."""
    for s in _SENT.split(text or ""):
        s = s.strip()
        if s and admission.india_city(s) and guardrails.names_company(s, name, domain):
            return s[:400]
    return None


def headline_name(title: str | None) -> str | None:
    """'Bengaluru-based Vecton AI raises ₹6 Cr…' -> 'Vecton AI' (cleaned by the admission gate)."""
    t = re.sub(r"^(exclusive|breaking|funding alert|startup funding)\s*[:\-|]\s*", "", (title or "").strip(), flags=re.I)
    if _NOT_YET.search(t):
        return None
    m = _HEAD.match(t)
    if not m:
        return None
    raw = m.group("name").strip(" ,'\"“”‘’")
    name, _ = admission.clean_name(raw, headline=True)
    if name and not set(re.findall(r"[a-z]+", name.lower())) - _GENERIC_WORDS:
        return None      # "Bangalore AI Startup" is a description, not a company
    return name


_GENERIC_WORDS = set(admission.INDIA_CITIES) | {
    "india", "indian", "ai", "startup", "startups", "tech", "the", "a", "an", "based", "agentic", "saas", "fintech",
    "healthtech", "edtech", "deeptech", "company", "firm", "platform", "genai", "voice", "b2b", "robotics", "spacetech"}


# ================================================================== discovery: admitting one candidate

@dataclass
class Cand:
    name: str
    text: str                       # everything the source said (title + snippet / row / article lead)
    url: str | None                 # where it was read
    kind: str                       # rss | search | directory | yc | ats
    headline: bool = True
    website: str | None = None
    website_is_link: bool = True
    description: str | None = None
    announced: str | None = None
    stage: str | None = None
    amount_usd_m: float | None = None
    careers_url: str | None = None
    own_page: bool = False
    quote: str | None = None        # set when the source already isolated the city sentence
    check_age: bool = True          # False: an undated source (a YC batch) — recency is preferred, not required


_CHANNEL = {"rss": "feed", "search": "jina-ddg", "directory": "jina", "yc": "yc", "ats": "jina-ddg",
            # india_sources.py (2026-10-14)
            "github": "github-web", "cutshort": "cutshort", "portfolio": "direct", "wellfound": "jina"}
# sources whose quote is a profile / an address, not "they are hiring" — no hiring claim
_NOT_A_HIRING_CLAIM = {"yc", "github", "portfolio"}


class Run:
    """One discovery pass: counts per source, names already handled in this pass, and
    the ids of the companies it admitted."""

    def __init__(self, limit: int):
        self.limit = limit
        self.by_source: dict[str, dict] = {}
        self.new_ids: list[int] = []
        self.updated_ids: list[int] = []
        self.done: set[str] = set()

    def stat(self, kind: str) -> dict:
        return self.by_source.setdefault(kind, {"seen": 0, "admitted": 0, "updated": 0, "skipped": {}})

    def skip(self, kind: str, why: str) -> None:
        s = self.stat(kind)["skipped"]
        s[why] = s.get(why, 0) + 1

    @property
    def full(self) -> bool:
        return len(self.new_ids) >= self.limit


def _admit(session, run: Run, c: Cand) -> Company | None:
    from jobhunter import harvest

    st = run.stat(c.kind)
    st["seen"] += 1
    name, _how = admission.clean_name(c.name, headline=c.headline, source=c.kind)
    if not name:
        run.skip(c.kind, "junk name")
        return None
    key = admission.name_key(name)
    if not key or key in run.done:
        run.skip(c.kind, "already handled this run")
        return None
    run.done.add(key)
    blob = f"{c.text} {c.description or ''}"
    if non_tech(blob):
        run.skip(c.kind, "consumer brand, not an engineering employer")
        return None
    stage = c.stage or stage_of(c.text)
    if _SKIP_STAGE.search(c.text or "") and stage not in ("Pre-seed", "Seed", "Series A", "Series B", "Series C"):
        run.skip(c.kind, "late stage / PE / debt / IPO")
        return None
    if stage == "Series D+":
        run.skip(c.kind, "late stage / PE / debt / IPO")
        return None
    announced = c.announced or date_of(c.text, c.url)
    if c.check_age and too_old(announced, c.text if not c.announced else None):
        run.skip(c.kind, f"round older than {FRESH_MONTHS} months")
        return None
    domain = admission.host_of(c.website) if c.website else None
    quote = c.quote or city_quote(c.text, name, domain)
    if not quote and c.own_page:
        # read off the company's own board: any sentence naming the city is about them
        quote = next((s.strip()[:400] for s in _SENT.split(c.text or "") if admission.india_city(s)), None)
    chk = guardrails.india_city(quote, name=name, domain=domain, own_page=c.own_page)
    if not chk:
        run.skip(c.kind, "no Bengaluru/Hyderabad evidence")
        return None

    existing = admission.find_existing(session, name, domain)
    fund = None
    amount = c.amount_usd_m if c.amount_usd_m is not None else amount_usd_m(c.text)
    if stage or amount or announced:
        inv = investors_of(c.text)
        # the round's quote: the city sentence when it carries the money, else the whole headline
        fund = {"stage": stage, "amount_usd_m": amount, "announced": announced,
                "evidence": (quote if _money_in(quote) else c.text)[:400],
                **({"investors": inv} if inv else {})}
    hs = harvest.HarvestStats(f"india-{c.kind}")
    company = harvest._upsert(
        session, name=name, website=c.website, source=f"india-{c.kind}", description=c.description,
        region="india", note=f"India discovery ({c.kind}): {quote[:120]}", funding=fund,
        claim={"text": quote[:200], "url": c.url, "by": None} if c.kind not in _NOT_A_HIRING_CLAIM else None,
        stats=hs, headline=c.headline, website_is_link=c.website_is_link)
    if company is None:
        why = next(iter(hs.skipped), "refused by the admission gate")
        run.skip(c.kind, why)
        return None
    if company.list_key is None:
        guardrails.set_city(session, company, quote, source=c.url, channel=_CHANNEL.get(c.kind), own_page=c.own_page)
    if c.careers_url and not company.careers_url and guardrails.careers_page(c.careers_url, domain=company.domain):
        company.careers_url = c.careers_url
    if existing is None and hs.admitted:
        company.india_found_at = utcnow()
        company.india_source = c.kind
        st["admitted"] += 1
        run.new_ids.append(company.id)
    else:
        st["updated"] += 1
        run.updated_ids.append(company.id)
    session.add(company)
    session.commit()
    return company


def _money_in(text: str | None) -> bool:
    return bool(_USD.search(text or "") or _INR.search(text or ""))


# ================================================================== source: Indian funding RSS

INDIA_FEEDS = [
    "https://inc42.com/buzz/feed/",
    "https://entrackr.com/rss",
    "https://yourstory.com/category/funding/feed",
    "https://economictimes.indiatimes.com/tech/funding/rssfeeds/78570550.cms",
    "https://economictimes.indiatimes.com/tech/startups/rssfeeds/78570540.cms",
    "https://www.indianstartupnews.com/rss",
    "https://startuptalky.com/rss/",
    "https://www.thehindubusinessline.com/info-tech/feeder/default.rss",
    "https://www.techinasia.com/feed",
    # tried 2026-10-08 and not answering a plain fetch: vccircle.com (500), moneycontrol
    # startup feed (empty), finsmes India (403)
]
# WordPress feeds that page (?paged=N) — the backfill walks them back through the window
PAGED_FEEDS = ["https://inc42.com/buzz/feed/", "https://yourstory.com/category/funding/feed"]
_SEEN_KEY = "india:seen"
_RSS_AT = "india:rss_at"


def _seen(session) -> set[str]:
    try:
        return set(json.loads(get_setting(session, _SEEN_KEY, "[]") or "[]"))
    except ValueError:
        return set()


def _save_seen(session, seen: set[str], new: list[str]) -> None:
    keep = (list(seen) + new)[-6000:]
    set_setting(session, _SEEN_KEY, json.dumps(keep))


def _feed_items(url: str) -> list[dict]:
    import feedparser
    import httpx

    from jobhunter.scrapers.base import HEADERS

    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers=HEADERS)
        if r.status_code >= 400:
            return []
        d = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001 — one dead feed must not end the pass
        log.warning("india rss %s: %s", url, e)
        return []
    out = []
    for e in d.entries:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
        content = " ".join(re.sub(r"<[^>]+>", " ", x.get("value", "")) for x in (e.get("content") or []))
        text = re.sub(r"\s+", " ", f"{e.get('title', '')}. {summary} {content[:2500]}").strip()
        ann = None
        for k in ("published_parsed", "updated_parsed"):
            if e.get(k):
                ann = datetime(*e[k][:3]).strftime("%Y-%m-%d")
                break
        out.append({"title": e.get("title", ""), "text": text, "url": e.get("link"), "announced": ann})
    return out


def from_rss(session, run: Run, *, pages: int = 1, reads: int = 10, force: bool = False) -> None:
    """Funding headlines from the Indian feeds. An item whose feed text names no target
    city has its article read (up to `reads` a run) — the quote must come from the article."""
    from jobhunter.research import web

    last = get_setting(session, _RSS_AT, "")
    if not force and last and (utcnow() - datetime.fromisoformat(last)) < timedelta(minutes=55):
        return
    seen = _seen(session)
    new_seen: list[str] = []
    urls = list(INDIA_FEEDS) + [f"{u}?paged={p}" for u in PAGED_FEEDS for p in range(2, pages + 1)]
    for feed in urls:
        if run.full:
            break
        for it in _feed_items(feed):
            if run.full:
                break
            key = it["url"] or it["title"]
            if key in seen or key in new_seen:
                continue
            name = headline_name(it["title"])
            if not name or not _RAISE.search(it["title"]):
                new_seen.append(key)
                continue
            text = it["text"]
            if not city_quote(text, name) and reads > 0 and it["url"]:
                if admission.india_other_city(text) and not admission.india_city(text):
                    new_seen.append(key)
                    run.stat("rss")["seen"] += 1
                    run.skip("rss", "another Indian city")
                    continue
                reads -= 1
                page = web.read(it["url"], max_chars=5000)
                if page.get("text"):
                    text = f"{it['title']}. " + re.sub(r"\s+", " ", page["text"])[:5000]
            new_seen.append(key)
            _admit(session, run, Cand(name=name, text=text, url=it["url"], kind="rss",
                                      announced=it["announced"]))
    _save_seen(session, seen, new_seen)
    set_setting(session, _RSS_AT, utcnow().isoformat(timespec="seconds"))


# ================================================================== source: keyless web search

CITIES = ["Bengaluru", "Hyderabad", "Bangalore"]
SECTORS = ["AI", "agentic AI", "SaaS", "fintech", "developer tools", "healthtech", "deeptech", "cybersecurity",
           "robotics", "spacetech", "EV", "edtech", "logistics", "climate tech", "semiconductor", "data analytics",
           "B2B", "insurtech", "HR tech", "gaming", "voice AI", "legal tech", "proptech", "agritech", "drone",
           "quantum", "biotech", "martech", "AI infrastructure", "enterprise software", "computer vision",
           "supply chain", "wealthtech", "payments", "GenAI"]
STAGES = ["pre-seed", "seed", "Series A", "Series B"]
_TEMPLATES = ["{city}-based {sector} startup raises {stage} funding",
              "{sector} startup {city} raises {stage} round {year}",
              "{city} {sector} startup secures {stage} funding {year}",
              # 2026-10-14: more phrasings, so the rotation reaches articles the first three miss
              "{city} {sector} startup {stage} round {year}",
              "{sector} startup from {city} bags {stage} funding {year}"]
_SEARCH_CURSOR = "india:search_cursor"


def search_queries() -> list[str]:
    """The rotating pool: every city × sector × stage, three phrasings, this year and last."""
    year = datetime.now().year
    out = []
    # sector varies fastest: neighbouring queries return different articles
    for i, (sector, stage, city) in enumerate(
            (s, st, c) for st in STAGES for c in CITIES for s in SECTORS):
        t = _TEMPLATES[i % len(_TEMPLATES)]
        out.append(t.format(city=city, sector=sector, stage=stage, year=year if i % 2 == 0 else year - 1))
    return out


def from_search(session, run: Run, *, queries: int = 6) -> int:
    """DuckDuckGo via Jina (web.KEYLESS). Each hit's title must be a raise headline; its
    title + snippet must name the company and Bengaluru / Hyderabad in one sentence."""
    from jobhunter.research import web

    pool = search_queries()
    try:
        cursor = int(get_setting(session, _SEARCH_CURSOR, "0") or 0)
    except ValueError:
        cursor = 0
    asked = 0
    while asked < queries and not run.full:
        q = pool[(cursor + asked) % len(pool)]
        asked += 1
        hit = web.search(q, limit=10, only=web.KEYLESS)
        if hit.get("error") and not hit.get("results"):
            run.skip("search", f"search error: {hit['error'][:50]}")
            if web.search_down():
                break
            continue
        for r in hit.get("results") or []:
            if run.full:
                break
            title = r.get("title") or ""
            name = headline_name(re.split(r"\s+[|–—]\s+|\s+-\s+(?=[A-Z][\w.]+(?:\.\w+)?\s*$)", title)[0])
            if not name:
                continue
            text = f"{title}. {r.get('snippet') or ''}"
            _admit(session, run, Cand(name=name, text=text, url=r.get("url"), kind="search"))
    set_setting(session, _SEARCH_CURSOR, str((cursor + asked) % len(pool)))
    return asked


# ================================================================== source: funded-startup directory pages

DIRECTORY_CITIES = ["https://fundediq.co/funded-startups-bengaluru/", "https://fundediq.co/funded-startups-hyderabad/",
                    "https://fundediq.co/funded-startups-bangalore/", "https://fundediq.co/funded-startups-secunderabad/",
                    # state pages: only rows located in the two cities are taken (_DIR_PARSE)
                    "https://fundediq.co/funded-startups-karnataka/", "https://fundediq.co/funded-startups-telangana/"]
_DIR_PAGES = "india:dir_pages"
_DIR_CURSOR = "india:dir_cursor"
_DIR_ROW = re.compile(r"\[(?:[A-Z0-9] )?([^\[\]]{10,700}?)\]\((https://fundediq\.co/([a-z0-9-]+)-funding/)\)")
_DIR_PARSE = re.compile(
    r"^(?P<name>.+?)\s+(?P<loc>(?:Bengaluru|Bangalore|Hyderabad|Secunderabad)[^·]{0,40}?,\s*India)\s+·\s+(?P<rest>.+?)\s+"
    r"(?P<stage>[A-Z][A-Za-z -]{1,30}?)·(?P<mon>[A-Z][a-z]{2}) (?P<yr>20\d\d)\s+(?P<amt>\$[\d.]+[kMB]?|Undisclosed)\s*$")
_DIR_INDUSTRY = re.compile(r"\((https://fundediq\.co/funded-startups-[a-z0-9-]+-"
                           r"(?:bengaluru|bangalore|hyderabad|secunderabad|karnataka|telangana)/)\)")
_TLD2 = ("co-in", "org-in", "net-in", "co-uk", "com-au", "com-sg")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", admission._ascii(text).lower()).strip("-")


def domain_from_slug(name: str, slug: str) -> str | None:
    """FundedIQ's row slug is '<name>-<domain with dots as dashes>':
    'august-ai-meetaugust-ai' -> 'meetaugust.ai'. None when it does not split cleanly."""
    ns = _slug(name)
    if not ns or not slug.startswith(ns + "-"):
        return None
    rest = slug[len(ns) + 1:]
    for t in _TLD2:
        if rest.endswith("-" + t):
            base = rest[: -len(t) - 1]
            return f"{base}.{t.replace('-', '.')}" if base else None
    if "-" not in rest:
        return None
    base, tld = rest.rsplit("-", 1)
    return f"{base}.{tld}" if base and 2 <= len(tld) <= 6 else None


def parse_directory(text: str) -> list[dict]:
    """Rows of a directory page: name, city text, industries + what they do, stage, month, amount."""
    out = []
    for inner, url, slug in _DIR_ROW.findall(text or ""):
        row = re.sub(r"\s+", " ", inner).strip()
        m = _DIR_PARSE.match(row)
        if not m:
            continue
        name = m.group("name").strip()
        rest = m.group("rest")
        stage_raw = m.group("stage").strip()
        amt = m.group("amt")
        out.append({
            "name": name, "row": row, "url": url, "loc": m.group("loc"),
            "what": rest, "stage_raw": stage_raw,
            "announced": f"{m.group('yr')}-{_MONTHS[m.group('mon').lower()[:3]]:02d}",
            "amount_usd_m": amount_usd_m(amt) if amt != "Undisclosed" else None,
            "domain": domain_from_slug(name, slug),
        })
    return out


def _dir_pages(session) -> list[str]:
    from jobhunter.research import web

    try:
        cached = json.loads(get_setting(session, _DIR_PAGES, "{}") or "{}")
    except ValueError:
        cached = {}
    if cached.get("day") == datetime.now().strftime("%Y-%m-%d") and cached.get("pages"):
        return cached["pages"]
    pages = list(DIRECTORY_CITIES)
    for u in DIRECTORY_CITIES:
        page = web.read(u, max_chars=120000)
        for link in _DIR_INDUSTRY.findall(page.get("text") or ""):
            if link not in pages:
                pages.append(link)
    set_setting(session, _DIR_PAGES, json.dumps({"day": datetime.now().strftime("%Y-%m-%d"), "pages": pages}))
    return pages


def from_directory(session, run: Run, *, pages: int = 1) -> int:
    from jobhunter.research import web

    allp = _dir_pages(session)
    try:
        cursor = int(get_setting(session, _DIR_CURSOR, "0") or 0)
    except ValueError:
        cursor = 0
    done = 0
    while done < pages and not run.full and allp:
        url = allp[(cursor + done) % len(allp)]
        done += 1
        page = web.read(url, max_chars=120000)
        for r in parse_directory(page.get("text") or ""):
            if run.full:
                break
            sr = r["stage_raw"].lower()
            if any(w in sr for w in ("private equity", "debt", "ipo", "series d", "series e", "series f",
                                     "series g", "secondary", "corporate round", "grant")):
                run.stat("directory")["seen"] += 1
                run.skip("directory", "late stage / PE / debt / IPO")
                continue
            stage = admission.normalize_stage(r["stage_raw"]) if "unknown" not in sr else None
            if sr.startswith("angel"):
                stage = "Pre-seed"
            what = re.sub(r"^[^·]*?(?=[A-Z][\w.&'-]* (?:is|are|was|provides|operates|builds|offers|develops|creates|helps|"
                          r"enables|makes|designs|delivers|uses|specializes|focuses)\b)", "", r["what"]).strip()
            _admit(session, run, Cand(
                name=r["name"], text=r["row"], url=r["url"], kind="directory", headline=False,
                website=f"https://{r['domain']}" if r["domain"] else None, website_is_link=True,
                description=what if len(what) >= 60 and not what.endswith("…") else None,
                announced=r["announced"], stage=stage, amount_usd_m=r["amount_usd_m"], quote=r["row"][:400]))
    set_setting(session, _DIR_CURSOR, str((cursor + done) % max(1, len(allp))))
    return done


# ================================================================== source: YC directory (India)

_YC_AT = "india:yc_day"
# older batches are kept (a YC company is a startup for years) but not before this one
YC_MIN_BATCH = "2019-01-01"


def from_yc(session, run: Run, *, force: bool = False) -> None:
    """Active, private YC companies located in Bengaluru / Hyderabad (the yc-oss mirror)."""
    import httpx

    from jobhunter import harvest

    today = datetime.now().strftime("%Y-%m-%d")
    if not force and get_setting(session, _YC_AT, "") == today:
        return
    try:
        data = httpx.get(harvest.YC_DIRECTORY, timeout=90).json()
    except Exception as e:  # noqa: BLE001
        run.skip("yc", f"directory unavailable: {type(e).__name__}")
        return
    rows = []
    for c in data if isinstance(data, list) else []:
        locs = c.get("all_locations")
        locs = locs if isinstance(locs, str) else "; ".join(locs or [])
        if not admission.india_city(locs):
            continue
        if (c.get("status") or "").lower() != "active" or (c.get("stage") or "").lower() in ("public", "acquired"):
            run.stat("yc")["seen"] += 1
            run.skip("yc", "inactive / public / acquired")
            continue
        rows.append((harvest.batch_date(c.get("batch")) or "0000", c, locs))
    rows.sort(key=lambda r: r[0], reverse=True)
    for announced, c, locs in rows:
        if run.full:
            break
        if announced < YC_MIN_BATCH:
            run.stat("yc")["seen"] += 1
            run.skip("yc", f"YC batch before {YC_MIN_BATCH[:4]}")
            continue
        age = admission.months_old(announced) if announced != "0000" else None
        fresh = age is not None and age <= FRESH_MONTHS
        name = str(c["name"]).strip()
        quote = f"{name} (YC {c.get('batch')}) — location: {locs}"
        _admit(session, run, Cand(
            name=name, text=quote, url=c.get("url") or f"https://www.ycombinator.com/companies/{c.get('slug')}",
            kind="yc", headline=False, website=c.get("website"), website_is_link=False,
            description=c.get("one_liner"), announced=announced[:7] if fresh else None,
            stage="Seed" if fresh else None, quote=quote, own_page=True, check_age=False))
    set_setting(session, _YC_AT, today)


# ================================================================== source: Indian careers / ATS boards

_ATS_QUERIES = ["{city} startup careers keka.com software engineer",
                "{city} startup jobs darwinbox careers engineer",
                "{city} startup careers zohorecruit software developer",
                "cutshort.io company {city} startup hiring engineers",
                "instahyre jobs-at {city} startup software engineer",
                "wellfound company {city} startup hiring software engineer"]
_ATS_CURSOR = "india:ats_cursor"
# IT-services / staffing shops are not the underrated product startups this is for
_IT_SERVICES = re.compile(r"(?i)\b(services|solutions|consult\w*|infotech|technologies pvt|staffing|outsourc\w*|"
                          r"systems pvt|software pvt|pvt\.? ltd|private limited)\b")
_ATS_HOSTS = ("keka", "darwinbox", "zohorecruit", "cutshort", "instahyre", "wellfound")
_ATS_TITLE_NOISE = re.compile(r"(?i)\b(careers?|jobs?|openings|hiring|work with us|join us|current|opportunities|"
                              r"at|in|keka|darwinbox|zoho recruit|cutshort|instahyre|wellfound|page)\b|[|:–—\-]")


def from_ats(session, run: Run, *, queries: int = 1) -> int:
    from jobhunter.research import web
    from jobhunter.scrapers import careers_ats as ca

    pool = [q.format(city=c) for q in _ATS_QUERIES for c in ("Bengaluru", "Hyderabad")]
    try:
        cursor = int(get_setting(session, _ATS_CURSOR, "0") or 0)
    except ValueError:
        cursor = 0
    asked = 0
    while asked < queries and not run.full:
        q = pool[(cursor + asked) % len(pool)]
        asked += 1
        hit = web.search(q, limit=10, only=web.KEYLESS)
        for r in hit.get("results") or []:
            url = r.get("url") or ""
            ats = slug = None
            for a, rx in ca.ATS_PATTERNS:
                m = rx.search(url)
                if m and a in _ATS_HOSTS:
                    ats, slug = a, m.group(1)
                    break
            if not ats or slug.lower() in ca._NOT_A_SLUG:
                continue
            title = r.get("title") or ""
            own = ats in ("keka", "darwinbox", "zohorecruit")
            # only a board page that names its company ("Careers at Acme", "Acme Careers");
            # a job-titled hit names a role, and a slug is not a name (2026-10-08: the
            # slug fallback admitted "gravitixtechsolutions.zohorecruit")
            m = re.search(r"(?i)careers? (?:at|@)\s+(.+?)(?:\s+[-|–]|$)", title) or \
                re.search(r"(?i)^(.+?)\s+(?:careers|jobs)\b", title)
            name = (m.group(1) if m else "").strip(" .,")
            if (not name or len(name) > 40 or "." in name or _IT_SERVICES.search(name)
                    or not guardrails.names_company(f"{slug} {url}", name)):
                continue
            _admit(session, run, Cand(name=name, text=f"{title}. {r.get('snippet') or ''}", url=url, kind="ats",
                                      headline=False, careers_url=url if own else None, own_page=own))
    set_setting(session, _ATS_CURSOR, str((cursor + asked) % len(pool)))
    return asked


# ================================================================== the step

# per-pass work for the india_sources.py sources (config targeting.india.discovery.sources)
_SRC = _D.get("sources") or {}
SOURCES = tuple(_SRC.get("order") or ("rss", "directory", "yc", "github", "cutshort", "portfolio", "search",
                                      "wellfound", "ats"))


def discover(limit: int | None = None, *, searches: int = 6, dir_pages: int = 1, rss_reads: int = 10,
             rss_pages: int = 1, ats_queries: int = 1, budget_s: int = 420, force: bool = False,
             sources: tuple[str, ...] = SOURCES, github_orgs: int | None = None,
             github_searches: int | None = None, cutshort_pages: int | None = None,
             portfolio_companies: int | None = None, wellfound_pages: int | None = None) -> dict:
    """One India discovery pass: up to `limit` NEW Bengaluru / Hyderabad companies, in
    source order — the feeds (hourly), one directory page, YC (daily), GitHub orgs,
    Cutshort posts, portfolio pages, search, Wellfound, ATS boards."""
    from jobhunter import india_sources as more

    init_db()
    limit = int(limit if limit is not None else (CONFIG.get("tick") or {}).get("india_discovery_per_tick", 30))
    run = Run(limit)
    t0 = time.time()

    def _n(v, key, default):
        return int(v if v is not None else _SRC.get(key, default))

    steps = {
        "rss": lambda s: from_rss(s, run, pages=rss_pages, reads=rss_reads, force=force),
        "directory": lambda s: from_directory(s, run, pages=dir_pages),
        "yc": lambda s: from_yc(s, run, force=force),
        "search": lambda s: from_search(s, run, queries=searches),
        "ats": lambda s: from_ats(s, run, queries=ats_queries),
        "github": lambda s: more.from_github(run, orgs=_n(github_orgs, "github_orgs", 25),
                                             searches=_n(github_searches, "github_searches", 1)),
        "cutshort": lambda s: more.from_cutshort(run, pages=_n(cutshort_pages, "cutshort_pages", 2)),
        "portfolio": lambda s: more.from_portfolio(run, companies=_n(portfolio_companies, "portfolio_companies", 20)),
        "wellfound": lambda s: more.from_wellfound(run, pages=_n(wellfound_pages, "wellfound_pages", 1)),
    }
    errors = {}
    for name in sources:
        if run.full or time.time() - t0 > budget_s:
            break
        if name not in steps:
            continue
        try:
            with get_session() as session:
                steps[name](session)
        except Exception as e:  # noqa: BLE001 — one source failing must not end the pass
            log.exception("india discovery %s failed", name)
            errors[name] = f"{type(e).__name__}: {str(e)[:160]}"
    out = {"limit": limit, "admitted": len(run.new_ids), "updated": len(run.updated_ids),
           "by_source": run.by_source, "new_ids": run.new_ids, "seconds": round(time.time() - t0)}
    if errors:
        out["errors"] = errors
    log.info("india discovery: %s", {k: v for k, v in out.items() if k != "new_ids"})
    return out


__all__ = ["apply_pay_floor", "pay_evidence", "pay_verdict", "pay_label", "ctc_upper", "in_scope",
           "discover", "parse_directory", "domain_from_slug", "city_quote", "headline_name", "amount_usd_m",
           "BELOW", "MIN_LPA"]
