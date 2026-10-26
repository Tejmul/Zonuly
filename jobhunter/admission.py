"""The admission gate — one door every company comes through, whatever found it.

Before 2026-10-06 each source created companies its own way: a job board inserted
`Company(name=job.company_name)` for every posting, the funding feeds turned headlines
into names ("Swiss startup Jaipur Robotics", "Crusoe reportedly", "Top European funding
rounds"), HN set the apply link as the website (exa.ai on 68 rows), and twins in the same
batch were inserted twice. This module is the one gate they all call now:

    admit(Candidate(...), session) -> Verdict      (ok, reason) also unpacks

It does four things, in order, and says which one decided:

  1. NAME      strip headline furniture ("<place>-based", "<country>'s", "<sector>
               startup", trailing "to"/"reportedly", brackets) and reject what is not a
               company at all — VC funds, accelerators, events, places, job-list
               phrases, sentences.
  2. SITE      a website on a job board, an ATS, a link shortener or a form is not the
               company's site; neither is an apply link whose host does not carry the name.
  3. DEDUP     exact name, then the normalized name key, then the domain — so
               "RevenueCat", "Revenuecat" and "RevenueCat, Inc." are one row.
  4. OVERRATED the targeting rule (Tejmul, 2026-10-06): a famous name, a listed company,
               valued ≥ $5B or raised ≥ $1B is out — "thousands of resumes already
               waiting". Raised $400M–$1B or valued $2–5B is borderline: in only when it
               is visibly hiring AND can clearly pay. Headcount is never a criterion.
               Unknown funding is not disqualifying.

Also here, because every writer needs them: the funding-stage vocabulary
(`normalize_stage`), the company-card funding parser (`parse_funding_card`), and the
India city rule (only Bengaluru and Hyderabad count).

Layering: config + db only. No network, no model. The thresholds live in config.yaml
under `targeting.overrated` and `targeting.india`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from jobhunter import CONFIG

log = logging.getLogger(__name__)

_T = CONFIG.get("targeting") or {}
_O = _T.get("overrated") or {}

MAX_VALUATION_USD_M = float(_O.get("max_valuation_usd_m", 5000))
# names Tejmul has cleared by hand: kept even when listed or large (2026-10-26)
KEEP: list[str] = [str(x) for x in (_O.get("keep") or [])]
MAX_RAISED_USD_M = float(_O.get("max_total_raised_usd_m", 1000))
BORDERLINE_RAISED_USD_M = float(_O.get("borderline_raised_usd_m", 400))
BORDERLINE_VALUATION_USD_M = float(_O.get("borderline_valuation_usd_m", 2000))
BORDERLINE_MIN_ROLES = int(_O.get("borderline_min_open_roles", 3))
BORDERLINE_PAY_BANDS = set(_O.get("borderline_pay_bands") or ["pays", "deep_pockets"])


def _famous_entries() -> list[tuple[str, str]]:
    """(group, entry) for every famous name in config. Reads the old `hyped:` block too,
    so a config that has not been migrated keeps excluding what it excluded before."""
    out: list[tuple[str, str]] = []
    for source in (_O.get("famous") or {}, _T.get("hyped") or {}):
        for group, names in source.items():
            for n in names or []:
                if isinstance(n, str) and n.strip():
                    out.append((group.replace("_", " "), n.strip().lower()))
    return out


FAMOUS = _famous_entries()


# ------------------------------------------------------------------ name keys

# corporate furniture: never part of who a company is
_CORP = {"inc", "llc", "ltd", "limited", "corp", "corporation", "gmbh", "plc", "pvt", "private",
         "co", "the", "sa", "ag", "bv", "oy", "ab", "sas", "srl", "pte", "pty", "kk", "lp", "llp"}
# words a company adds or drops between sources ("Acme", "Acme AI", "Acme Labs")
_SOFT = {"labs", "lab", "technologies", "technology", "ai", "io", "hq", "app", "software",
         "systems", "solutions", "group", "holdings", "global", "india", "online"}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def _tokens(name: str) -> list[str]:
    n = _ascii(name).lower().replace("&", " and ")
    n = re.sub(r"\.(com|ai|io|co|in|dev|app|so|xyz|tech|org|net)\b", " ", n)   # "Octave.energy" keeps .energy
    return [t for t in re.sub(r"[^a-z0-9]+", " ", n).split() if t]


def name_key(name: str) -> str:
    """A company's identity key: lowercase, no punctuation, no spaces, no corporate or
    soft suffix. 'RevenueCat', 'Revenue Cat' and 'RevenueCat, Inc.' are one key."""
    toks = _tokens(name)
    core = [t for t in toks if t not in _CORP and t not in _SOFT]
    if not core:
        core = [t for t in toks if t not in _CORP] or toks
    return "".join(core)


def _famous_tokens(name: str) -> list[str]:
    """Tokens for the famous-name match: corporate and soft words go, but 'ai' stays,
    so 'Scale AI' is 'scale ai' and not 'scale'."""
    soft = _SOFT - {"ai", "io", "app"}
    return [t for t in _tokens(name) if t not in _CORP and t not in soft]


def famous(name: str) -> tuple[str, str] | None:
    """(group, entry) when the name is on the famous list, else None.

    An entry matches the leading words of the name ("google" hits "Google DeepMind",
    not "Googly Eyes"). An entry written "=name" must match the whole name — for
    ambiguous words ("=ramp" is Ramp, not Ramp Network; "=cohere" is not Cohere Health).
    """
    toks = _famous_tokens(name)
    if not toks:
        return None
    joined = " ".join(toks)
    for group, entry in FAMOUS:
        exact = entry.startswith("=")
        e = " ".join(_famous_tokens(entry.lstrip("=")))
        if not e:
            continue
        if joined == e or (not exact and (joined + " ").startswith(e + " ")):
            return group, entry.lstrip("=")
    return None


def kept(name: str) -> bool:
    """On targeting.overrated.keep — matched on leading words like the famous list."""
    toks = " ".join(_famous_tokens(name)) + " "
    return any(toks.startswith(" ".join(_famous_tokens(k)) + " ") for k in KEEP if _famous_tokens(k))


# ------------------------------------------------------------------ name cleanup

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_LEAD = re.compile(r"^(?:exclusive|breaking|report|update|just in|funding(?: alert)?|startup funding|"
                   r"deal(?:s)?|news|watch|scoop|first look)\s*[:\-–—|]\s*", re.I)
_POSSESSIVE = re.compile(r"^\S+['’]s\s+(?=\S)")
# "<anything>-based X", "Robbie Williams-backed X", "IPO-bound X"
_HYPHEN_DESC = re.compile(r"^(?:\S+\s+){0,3}?\S+-(?:based|backed|bound|focused|founded|led|owned|"
                          r"headquartered|born|built|powered)\s+(?=\S)", re.I)
# "<descriptor> startup X", "Lithuanian legaltech X", "Dutch payments technology firm X"
_SECTOR_NOUN = (r"(?:start-?ups?|scale-?ups?|spin-?outs?|firm|company|unicorn|platform|provider|maker|app|"
                r"marketplace|tracker|brand|[a-z]+tech|tech|biotech|fintech|paytech|operator|developer|player)")
# the rest is the name: capitalised, or one lowercase brand word ("German AI startup mika")
_DESC_NOUN = re.compile(rf"^(?i:(?:\S+\s+){{0,5}}?{_SECTOR_NOUN})\s+(?P<rest>[A-Z0-9].*|[a-z][\w:.\-]*\d?[\w:.\-]*)$")
_TRAIL_WORD = re.compile(r"\s+(?:to|has|have|had|is|are|was|were|will|now|reportedly|ipo|says|said|"
                         r"set|sets|plans|eyes|nabs|gets|bags|lands|secures|raises|raised|announces|"
                         r"in|and|with|for|from|of|at|on|by|as)$", re.I)
_TRAIL_CLAUSE = re.compile(r"\s+(?:announces|announced|launches|unveils|reportedly|files for|set to|"
                           r"plans to|in talks|to raise|to acquire|acquires|partners with|valued at|"
                           r"hits|crosses|becomes|joins)\b.*$", re.I)

_GENERIC_REST = {"solutions", "labs", "systems", "group", "ai", "technologies", "software",
                 "inc", "ltd", "global", "holdings", "india", "services"}
_VC_LAST = {"ventures", "venture", "capital", "partners", "fund", "funds", "vc", "vcs", "accelerator",
            "incubator", "angels", "investments", "investors", "syndicate", "lp", "advisors"}
_VC_PHRASE = re.compile(r"\b(venture partners|venture capital|growth equity|private equity|"
                        r"family office|angel network|seed fund|asset management)\b", re.I)
_EVENT = re.compile(r"\b(techsparks|disrupt|summit|conference|conclave|expo|hackathon|awards?|festival|"
                    r"webinar|meetup|demo ?day|tech week|startup week|bootcamp|podcast|newsletter)\b", re.I)
_PLACES = {
    "india", "usa", "us", "uk", "europe", "asia", "africa", "latam", "mena", "cee", "nordics", "emea",
    "apac", "germany", "france", "spain", "italy", "netherlands", "sweden", "norway", "denmark",
    "finland", "switzerland", "austria", "belgium", "poland", "ireland", "israel", "canada", "brazil",
    "mexico", "singapore", "japan", "china", "korea", "australia", "london", "berlin", "paris",
    "munich", "amsterdam", "stockholm", "dublin", "bengaluru", "bangalore", "hyderabad", "mumbai",
    "delhi", "pune", "chennai", "gurgaon", "gurugram", "noida", "siliconvalley", "sanfrancisco",
    "newyork", "nyc", "bayarea", "seattle", "boston", "austin", "toronto", "remote", "worldwide",
    "global", "anywhere", "unitedstates", "unitedkingdom",
}
_NOT_A_COMPANY = {
    "stealth", "stealthstartup", "stealthmode", "stealthco", "stealthcompany", "confidential",
    "undisclosed", "unknown", "na", "none", "null", "various", "multiple", "multiplecompanies",
    "hiring", "werehiring", "wearehiring", "jobs", "careers", "jobboard", "freelance", "freelancer",
    "selfemployed", "contract", "contractor", "consulting", "startup", "astartup", "ourclient",
    "client", "recruiter", "recruitment", "staffing", "hn", "yc", "ycombinator", "company",
    "mycompany", "ourcompany", "tbd", "test", "remotejobs", "startups", "ai", "tech", "labs", "fintech",
    "saas", "spinout", "spinoff",
}
# words that make a "name" a sentence or a list, not a company (headline sources only)
_SENTENCE_WORDS = {
    "top", "biggest", "largest", "newest", "latest", "best", "how", "why", "what", "which", "these",
    "this", "that", "those", "startups", "rounds", "funding", "raises", "raised", "deals", "roundup",
    "weekly", "daily", "news", "report", "left", "million", "billion", "after", "before", "investors",
    "round", "checks", "week", "month", "year", "days", "here", "who", "list", "edition", "recap",
}
_DEMONYM_ONLY = re.compile(r"^(?:[A-Za-z]+(?:an|ish|ese|ch|ic|i|ss)\s+)?(?:start-?up|company|firm|unicorn|"
                           r"scale-?up)s?$", re.I)

# sources that name companies in their own words — boards, directories, lists. Only the
# basic checks apply; headline surgery is for news.
TRUSTED_SOURCES = {"yc", "ats", "greenhouse", "lever", "ashby", "companies.yaml", "list", "manual",
                   "onboard", "wellfound"}


def clean_name(raw: str | None, *, headline: bool = False, source: str | None = None) -> tuple[str | None, str]:
    """(clean name, what was done) — or (None, why it is not a company).

    `headline` is for names cut out of news headlines and directory link text, where the
    company is wrapped in a description ("Milan-based biotech Aptadir Therapeutics").
    """
    if not raw or not raw.strip():
        return None, "empty"
    original = raw
    name = re.sub(r"\s+", " ", raw.replace("’", "'")).strip()
    name = _BRACKETS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–—|:,;'\"")
    name = _LEAD.sub("", name).strip()
    trusted = (source or "") in TRUSTED_SOURCES and not headline

    if not trusted:
        for _ in range(4):
            before = name
            name = _TRAIL_CLAUSE.sub("", name).strip()
            name = _TRAIL_WORD.sub("", name).strip(" ,;:-")
            if headline:
                name = _POSSESSIVE.sub("", name).strip()
                name = _HYPHEN_DESC.sub("", name).strip()
                m = _DESC_NOUN.match(name)
                if m and m.group("rest").strip().lower() not in _GENERIC_REST:
                    name = m.group("rest").strip()
            if name == before:
                break
    name = name.strip(" -–—|:,;'\".")

    if not name or len(name) < 2:
        return None, f"nothing left of '{original[:60]}'"
    if len(name) > 60 or len(name.split()) > 6:
        return None, "too long to be a name"
    if re.search(r"[$€£₹%]|\b\d+(?:\.\d+)?\s*(?:m|mn|bn|b|million|billion|crore|lakh)\b", name, re.I):
        return None, "an amount, not a name"
    key = name_key(name)
    alnum = re.sub(r"[^a-z0-9]", "", _ascii(name).lower())
    if alnum in _NOT_A_COMPANY or key in _NOT_A_COMPANY:
        return None, "a placeholder, not a company"
    if alnum in _PLACES:
        return None, "a place, not a company"
    words = name.split()
    if not trusted:
        if words[-1].lower().strip(".,") in _VC_LAST or _VC_PHRASE.search(name):
            return None, "an investor, not a startup"
        if _EVENT.search(name):
            return None, "an event, not a company"
        if _DEMONYM_ONLY.match(name):
            return None, "a description, not a name"
        if re.match(r"^\d+\s+[a-z]", name):
            return None, "a sentence, not a name"
        if re.search(r"\b(?:jobs|hiring|careers|vacancies|openings)\b", name, re.I):
            return None, "a job-list phrase"
    if headline:
        lowered = {w.lower().strip(".,:;!?") for w in words}
        if lowered & _SENTENCE_WORDS:
            return None, "a headline fragment, not a name"
        lower_words = [w for w in words if w[:1].islower() and w.lower() not in _SOFT]
        if len(words) >= 2 and words[-1][:1].islower() and words[-1].lower() not in _SOFT:
            return None, "ends in a lowercase word — a phrase, not a name"
        if len(lower_words) >= 2 and not re.search(r"[A-Z0-9]", name):
            return None, "no capital letter — a phrase, not a name"
    done = "unchanged" if name == original.strip() else f"cleaned from '{original.strip()[:80]}'"
    return name, done


# ------------------------------------------------------------------ websites

# hosts that are never a company's own site: job boards, ATS boards, forms, shorteners,
# social, press, directories, code hosts. An apply link on one of these says nothing
# about where the company lives.
_BOGUS_HOST = re.compile(
    r"(^|\.)(ashbyhq\.com|greenhouse\.io|grnh\.se|lever\.co|workable\.com|smartrecruiters\.com|"
    r"recruitee\.com|breezy\.hr|bamboohr\.com|jobvite\.com|icims\.com|myworkdayjobs\.com|"
    r"workday\.com|teamtailor\.com|personio\.(?:de|com)|homerun\.co|rippling\.com|dover\.com|"
    r"wellfound\.com|angel\.co|ycombinator\.com|workatastartup\.com|startup\.jobs|join\.com|"
    r"nodesk\.co|weremotedevs\.com|remoteok\.com|remoteok\.io|weworkremotely\.com|otta\.com|"
    r"welcometothejungle\.com|indeed\.com|glassdoor\.\w+|naukri\.com|instahyre\.com|cutshort\.io|"
    r"hirist\.\w+|internshala\.com|linkedin\.com|lnkd\.in|forms\.gle|docs\.google\.com|goo\.gl|"
    r"google\.com|typeform\.com|tally\.so|jotform\.com|airtable\.com|notion\.site|notion\.so|"
    r"bit\.ly|tinyurl\.com|t\.co|calendly\.com|cal\.com|youtube\.com|youtu\.be|github\.com|"
    r"github\.io|gitlab\.com|medium\.com|substack\.com|twitter\.com|x\.com|facebook\.com|"
    r"instagram\.com|wikipedia\.org|crunchbase\.com|pitchbook\.com|dealroom\.co|tracxn\.com|"
    r"techcrunch\.com|producthunt\.com|news\.ycombinator\.com|gmail\.com|outlook\.com|"
    r"hire\.withgoogle\.com|jobs\.\w+|apply\.\w+|careers\.\w+|zoho\.(?:com|in)|freshteam\.com|"
    r"keka\.com|darwinbox\.\w+|wix\.com|wixsite\.com|webflow\.io|vercel\.app|netlify\.app|"
    r"herokuapp\.com|carrd\.co|linktr\.ee|bio\.link|sites\.google\.com|discord\.gg|t\.me|"
    r"finsmes\.com|yourstory\.com|inc42\.com|eu-startups\.com|tech\.eu|sifted\.eu|reuters\.com|"
    r"bloomberg\.com|forbes\.com|businessinsider\.com|venturebeat\.com|bizjournals\.com)$",
    re.I,
)
# hosts where a subdomain belongs to the company even though the parent is shared
_SUBDOMAIN_SITES = re.compile(r"\.(notion\.site|github\.io|vercel\.app|netlify\.app|webflow\.io|carrd\.co)$", re.I)


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^[a-z]+://", "", url.strip(), flags=re.I).split("/")[0].split("?")[0].split("#")[0]
    host = host.split("@")[-1].split(":")[0].lower().removeprefix("www.").strip(".")
    return host or None


def registrable(host: str | None) -> str | None:
    """'jobs.acme.co.uk' -> 'acme.co.uk', 'app.exa.ai' -> 'exa.ai'."""
    if not host:
        return None
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def bogus_host(host: str | None, name: str | None = None) -> bool:
    """Is this host never a company's own site (a board, form, shortener, press, social)?

    Pass the company's `name` where it is known: gitlab.com is a code host for everyone
    except GitLab, and notion.so a page host for everyone except Notion."""
    if not host:
        return True
    if "." not in host or re.fullmatch(r"[\d.]+", host):
        return True
    if not _BOGUS_HOST.search(host):
        return False
    if name:
        reg = registrable(host) or host
        label = re.sub(r"[^a-z0-9]", "", reg.rsplit(".", 1)[0])
        if label and label == name_key(name):
            return False
    return True


def host_fits_name(name: str, host: str | None) -> bool:
    """Does the domain carry the company's name? 'acme.com', 'getacme.io', 'acmehq.com'
    fit "Acme"; 'exa.ai' does not fit "Wonderful". Used where a URL arrived as an apply
    link rather than as 'the company's website'."""
    if not host:
        return False
    reg = registrable(host) or host
    label = re.sub(r"[^a-z0-9]", "", reg.rsplit(".", 1)[0] if "." in reg else reg)
    if _SUBDOMAIN_SITES.search(host):
        label = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    key = name_key(name)
    full = re.sub(r"[^a-z0-9]", "", _ascii(name).lower())
    first = next((t for t in _tokens(name) if t not in _CORP), "")
    for stem in (key, full):
        # the label carries the name ("getacme", "acmehq"), or the name starts with the
        # label ("acmerobotics" on acme.io) — never a label buried mid-name ("spacexai"
        # does not make exa.ai theirs)
        if len(stem) >= 3 and (stem in label or (len(label) >= 3 and stem.startswith(label))):
            return True
    return len(first) >= 4 and first in label


def domain_of(url: str | None) -> str | None:
    """The domain we keep for a website, or None when the host is not a company site."""
    host = host_of(url)
    if not host or bogus_host(host):
        return None
    return host


# ------------------------------------------------------------------ funding stages

STAGES = ["Pre-seed", "Seed", "Series A", "Series B", "Series C", "Series D+", "Public", "Bootstrapped"]
_STAGE_RANK = {"Bootstrapped": 0, "Pre-seed": 1, "Seed": 2, "Series A": 3, "Series B": 4,
               "Series C": 5, "Series D+": 6, "Public": 7}
_LETTER_STAGE = {"a": "Series A", "b": "Series B", "c": "Series C"}


def _series(letter: str) -> str:
    return _LETTER_STAGE.get(letter.lower(), "Series D+")


def normalize_stage(text: str | None) -> str | None:
    """Any way a round is written -> one of STAGES, or None when it names no stage.

    "Series A / YC" -> Series A, "Seed / YC" -> Seed, "Series B / Pre-B" -> Series B,
    "series a/b" -> Series B (the latest one named), "growth" -> Series D+, "IPO" -> Public.
    "strategic", "debt", "grant" say nothing about stage and give None.
    """
    if not text:
        return None
    t = _ascii(str(text)).lower().strip()
    if not t:
        return None
    if t in {s.lower() for s in STAGES}:
        return next(s for s in STAGES if s.lower() == t)
    found: list[str] = []
    if re.search(r"\b(public|ipo|listed on|nasdaq|nyse|nse|bse|publicly traded|post-ipo)\b", t):
        found.append("Public")
    for m in re.finditer(r"\bpre[- ]?series[- ]?([a-k])\b", t):
        letter = m.group(1)
        found.append("Seed" if letter == "a" else _series(chr(ord(letter) - 1)))
    t2 = re.sub(r"\bpre[- ]?series[- ]?[a-k]\b", " ", t)
    for m in re.finditer(r"\bseries[- ]?([a-k])((?:\s*[/&,+-]\s*(?:series\s*)?[a-k]\b)*)", t2):
        found.append(_series(m.group(1)))
        for extra in re.findall(r"[/&,+-]\s*(?:series\s*)?([a-k])\b", m.group(2) or ""):
            found.append(_series(extra))
    for m in re.finditer(r"\bpre[- ]?([a-k])\b", t2):   # "Pre-B" -> the round before B
        letter = m.group(1)
        found.append("Seed" if letter == "a" else _series(chr(ord(letter) - 1)))
    if re.search(r"\b(growth|late[- ]stage|private equity|pe round|mezzanine)\b", t2):
        found.append("Series D+")
    if re.search(r"\bpre[- ]?seed\b|\bangel\b", t2):
        found.append("Pre-seed")
    if re.search(r"(?<!pre-)(?<!pre )(?<!pre)\bseed\b|\byc\b|y combinator|\bseed\+", t2):
        found.append("Seed")
    if re.search(r"\b(bootstrapped|self[- ]funded|founder[- ]funded)\b", t2):
        found.append("Bootstrapped")
    if not found:
        return None
    priced = [s for s in found if s != "Bootstrapped"]
    if priced:
        return max(priced, key=lambda s: _STAGE_RANK[s])
    return "Bootstrapped"


def stage_rank(stage: str | None) -> int | None:
    s = normalize_stage(stage)
    return _STAGE_RANK.get(s) if s else None


# ------------------------------------------------------------------ the company card

_FX = {"usd": 1.0, "eur": 1.08, "gbp": 1.27, "inr": 1 / 88, "cad": 0.73, "aud": 0.66, "sgd": 0.74}
_CARD_TOTAL = re.compile(r"total funding:?\s*(?:(usd|eur|gbp|inr)\s*([\d,]+(?:\.\d+)?)|\$\s*([\d.]+)\s*([mbk])\b)", re.I)
_CARD_REVENUE = re.compile(r"annual revenue:?\s*(usd|eur|gbp|inr)\s*([\d,]+)", re.I)
_CARD_ROUND = re.compile(
    r"(pre[- ]?seed(?: round)?|seed round|seed|angel(?: round)?|series [a-k]|venture round|"
    r"private equity round|growth equity|corporate round|funding round)\s*\((\d{4}-\d{2}-\d{2})\)"
    r"(?::\s*(usd|eur|gbp|inr)\s*([\d,]+(?:\.\d+)?)(?=\s|$|-))?",
    re.I,
)


def _money_m(cur: str, num: str) -> float | None:
    """'USD', '35,591,849' -> 35.6. A number with no thousands separator and a trailing
    decimal ('USD 14.') is a truncated quote, not 14 dollars, and gives None."""
    raw = num.rstrip(".")
    if "," not in raw and float(raw or 0) < 10_000:
        return None
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    return round(val * _FX.get(cur.lower(), 1.0) / 1e6, 2)


def parse_funding_card(text: str | None) -> dict:
    """The structured company card Exa returns ("- Total Funding: USD 35,591,849 -
    Series a (2024-11-20): USD 11,000,000 - ...") -> {total_usd_m, stage, amount_usd_m,
    announced, revenue_usd_m, public}. Only what the card states; {} when it is not one."""
    if not text:
        return {}
    out: dict = {}
    m = _CARD_TOTAL.search(text)
    if m:
        if m.group(1):
            out["total_usd_m"] = _money_m(m.group(1), m.group(2))
        else:
            mult = {"m": 1.0, "b": 1000.0, "k": 0.001}[m.group(4).lower()]
            out["total_usd_m"] = round(float(m.group(3)) * mult, 2)
    m = _CARD_REVENUE.search(text)
    if m:
        out["revenue_usd_m"] = _money_m(m.group(1), m.group(2))
    rounds = []
    for r in _CARD_ROUND.finditer(text):
        stage = normalize_stage(r.group(1))
        if not stage and "venture" not in r.group(1).lower():
            continue
        amt = _money_m(r.group(3), r.group(4)) if r.group(3) else None
        rounds.append((r.group(2), stage, amt))
    if rounds:
        rounds.sort(key=lambda x: x[0])
        date, stage, amt = rounds[-1]
        out["announced"] = date
        if stage:
            out["stage"] = stage
        out["amount_usd_m"] = amt
        # the latest round named is the stage, unless an earlier one ranks later (data order)
        staged = [s for _, s, _ in rounds if s]
        if staged:
            out["stage"] = max(staged, key=lambda s: _STAGE_RANK[s])
    if re.search(r"\bipo date:|\bstock symbol:|\b(?:nasdaq|nyse|nse|bse):\s*[A-Z]", text, re.I):
        out["public"] = True
    return {k: v for k, v in out.items() if v is not None}


_RAISE_WORDS = re.compile(r"\b(raised|raises|raising|total funding|in funding|funding round|series [a-k]|"
                          r"seed|round|valuation|valued|investment|invested|backed)\b", re.I)


def credible_amount(amount_usd_m: float | None, evidence: str | None) -> bool:
    """Is this amount a raise, and not revenue, transaction volume or a pricing table?"""
    if not amount_usd_m:
        return False
    if not evidence:
        return True   # a structured source with no quote (a feed amount) is taken as stated
    ev = evidence.lower()
    rev = _CARD_REVENUE.search(evidence)
    if rev and abs((_money_m(rev.group(1), rev.group(2)) or -1) - amount_usd_m) < 0.05:
        return False   # the number IS the annual revenue line
    return bool(_RAISE_WORDS.search(ev))


def funding_facts(*, stage: str | None, amount_usd_m: float | None, total_usd_m: float | None,
                  announced: str | None, valuation_usd_m: float | None, evidence: str | None) -> dict:
    """The funding a company row actually supports, repaired: card totals read, revenue
    misread as a round dropped, the stage normalised. What every judge reads."""
    card = parse_funding_card(evidence)
    # a company card states its rounds; an amount stored next to one was read off the card
    # by a generic extractor (often the revenue line), so the card's own figure wins
    is_card = bool(card.get("total_usd_m") or card.get("stage") or card.get("revenue_usd_m"))
    amt = None if is_card else (amount_usd_m if credible_amount(amount_usd_m, evidence) else None)
    if amt and amt > 2000 and not card.get("total_usd_m"):
        amt = None   # a single startup round above $2B is a parse error
    total = total_usd_m or card.get("total_usd_m")
    st = normalize_stage(stage)
    if card.get("stage") and (not st or (stage_rank(card["stage"]) or 0) > (stage_rank(st) or 0)):
        st = card["stage"]
    if card.get("public"):
        st = "Public"
    return {
        "stage": st,
        "amount_usd_m": card.get("amount_usd_m") or amt,
        "total_usd_m": total,
        "announced": card.get("announced") if (card.get("announced") and (not announced or card["announced"] > str(announced))) else announced,
        "valuation_usd_m": valuation_usd_m,
        "public": st == "Public",
    }


# ------------------------------------------------------------------ overrated

@dataclass
class Overrated:
    overrated: bool
    reason: str
    borderline: bool = False


def overrated_verdict(name: str, *, stage: str | None = None, amount_usd_m: float | None = None,
                      total_usd_m: float | None = None, valuation_usd_m: float | None = None,
                      evidence: str | None = None, hiring_verified: bool = False,
                      open_roles: int = 0, pay_band: str | None = None,
                      announced: str | None = None) -> Overrated:
    """Is this company past the point where our application is one of a thousand?

    Judged on fame, listing, valuation and money raised — never on headcount. A
    borderline company (raised $400M–$1B, or valued $2–5B) stays in only while it is
    visibly hiring and can clearly pay: Tejmul — "if the company is actively hiring a lot
    of people and paying really well, it's also a good fit".
    """
    if kept(name):
        return Overrated(False, "on the keep list (targeting.overrated.keep) — cleared by hand")
    hit = famous(name)
    if hit:
        return Overrated(True, f"famous ({hit[0]}: '{hit[1]}') — thousands of applications already waiting")
    f = funding_facts(stage=stage, amount_usd_m=amount_usd_m, total_usd_m=total_usd_m,
                      announced=announced, valuation_usd_m=valuation_usd_m, evidence=evidence)
    if f["public"]:
        return Overrated(True, "publicly listed — a household employer, not an underrated one")
    val = f["valuation_usd_m"]
    raised = max(x for x in (f["total_usd_m"] or 0, f["amount_usd_m"] or 0))
    if val and val >= MAX_VALUATION_USD_M:
        return Overrated(True, f"valued at ${val:,.0f}M (≥ ${MAX_VALUATION_USD_M:,.0f}M)")
    if raised and raised >= MAX_RAISED_USD_M:
        return Overrated(True, f"raised ${raised:,.0f}M (≥ ${MAX_RAISED_USD_M:,.0f}M)")
    border = []
    if raised and raised >= BORDERLINE_RAISED_USD_M:
        border.append(f"raised ${raised:,.0f}M")
    if val and val >= BORDERLINE_VALUATION_USD_M:
        border.append(f"valued at ${val:,.0f}M")
    if border:
        hiring = hiring_verified or open_roles >= BORDERLINE_MIN_ROLES
        pays = (pay_band or "") in BORDERLINE_PAY_BANDS
        what = " and ".join(border)
        if hiring and pays:
            how = "hiring verified" if hiring_verified else f"{open_roles} open roles"
            return Overrated(False, f"borderline ({what}) — kept: {how} and pay power '{pay_band}'", True)
        missing = [m for m, ok in (("not visibly hiring", hiring), ("pay power not high", pays)) if not ok]
        return Overrated(True, f"borderline ({what}) — {', '.join(missing)}", True)
    known = [p for p in (f["stage"], f"raised ${raised:,.1f}M" if raised else None,
                         f"valued ${val:,.0f}M" if val else None) if p]
    return Overrated(False, "not famous; " + (", ".join(known) if known else "funding unknown — not disqualifying"))


# ------------------------------------------------------------------ India: two cities only

_I = _T.get("india") or {}
INDIA_CITIES: dict[str, str] = {}
for _city, _aliases in (_I.get("cities") or {"Bengaluru": ["bengaluru", "bangalore", "blr"],
                                             "Hyderabad": ["hyderabad", "secunderabad"]}).items():
    for _a in _aliases:
        INDIA_CITIES[_a.lower()] = _city
INDIA_OTHER_CITIES: list[str] = [c.lower() for c in (_I.get("other_cities") or [])]


def india_city(*texts: str | None) -> str | None:
    """'Bengaluru', 'Hyderabad', 'Bengaluru · Hyderabad' or None, from any location text."""
    blob = " ".join(t.lower() for t in texts if t)
    found = []
    for alias, city in INDIA_CITIES.items():
        if re.search(rf"\b{re.escape(alias)}\b", blob) and city not in found:
            found.append(city)
    return " · ".join(sorted(found)) if found else None


def india_other_city(*texts: str | None) -> str | None:
    """The first non-target Indian city named (Delhi, Gurugram, Mumbai, Pune…), or None."""
    blob = " ".join(t.lower() for t in texts if t)
    for c in INDIA_OTHER_CITIES:
        if re.search(rf"\b{re.escape(c)}\b", blob):
            return c.title()
    return None


# ------------------------------------------------------------------ body shops (2026-10-13)
# A job board posting is often not the employer's: a staffing or recruitment agency posts
# "for a client", or the company is hidden ("Confidential"). Neither is a company to chase,
# and an IT-services body shop is not the underrated product company this is for.

_CONFIDENTIAL = re.compile(r"(?i)^\s*(?:confidential|undisclosed|stealth(?: mode)?(?: startup| company)?|"
                           r"hiring (?:for|on behalf of)|a (?:leading|top|reputed)\b|(?:leading|top|reputed) \w+ (?:company|firm|startup)|"
                           r"(?:our|a) client|client of)\b")
_STAFFING_NAME = re.compile(
    r"(?i)\b(staffing|recruit\w*|manpower|placements?|headhunt\w*|talent (?:solutions|acquisition|hub|partners)|"
    r"hr (?:solutions|services|consult\w*)|human resources?|workforce|outsourc\w*|careers? (?:pvt|private)|"
    r"jobs? (?:hub|pvt|private)|consultants?)\b")
# platforms and agencies that post roles for their clients under their own name
_STAFFING_KNOWN = re.compile(
    r"(?i)^(?:weekday|uplers|collabera|randstad|teamlease|adecco|abc consultants|michael page|xpheno|quess|ciel hr|"
    r"careernet|antal|kelly services|manpowergroup|turing|toptal|crossover|instahyre|cutshort|hirist|iimjobs|"
    r"naukri|foundit|scaler|talent500|hireginie|hirequotient|recruitcrm|taggd|alp consulting|people prime|"
    r"peopleprime|zyoin|elevate talent|spectraforce|mynd integrated|v support solutions|itc infotech|"
    r"tekpillar|tekwissen|insight global|robert half|hays|allegis|aston carter|teksystems)\b")
_CLIENT_POST = re.compile(
    r"(?i)\b(hiring for (?:one of )?(?:our|a|its|their) (?:esteemed |reputed |leading )?(?:client|customer)s?|"
    r"on behalf of (?:our|a|one of our|its) (?:client|customer)|(?:our|a) client(?:,| is| -| based| in)|"
    r"for (?:a|our) (?:leading|reputed|top|esteemed) client|client of ours|(?:our|the) client (?:company|organi[sz]ation)|"
    r"we are a (?:staffing|recruitment|recruiting) (?:firm|agency|company))")
_STAFFING_INDUSTRY = re.compile(r"(?i)\b(staffing|recruit\w*|human resources services|employment (?:placement|services))\b")
_IT_SERVICES_NAME = re.compile(r"(?i)\b(infotech|info ?systems|infosystems|consultancy|consulting|softech|soft solutions|"
                               r"it solutions|it services|technologies pvt|global services|business solutions)\b")
_IT_SERVICES_TEXT = re.compile(r"(?i)\b((?:it|software|technology|tech) (?:services|outsourcing|consulting) "
                               r"(?:company|firm|provider|partner|organi[sz]ation)|offshore development|"
                               r"staff augmentation|body ?shop|onsite[- ]offshore)\b")


def body_shop(name: str | None, text: str | None = None, *, industry: str | None = None,
              headcount: int | None = None) -> str | None:
    """Why this poster is not a company to chase — 'confidential poster', 'staffing /
    recruitment agency', 'posts on behalf of a client', 'IT-services body shop' — or None."""
    n = (name or "").strip()
    if not n or _CONFIDENTIAL.search(n):
        return "confidential poster"
    if _STAFFING_KNOWN.search(n) or _STAFFING_NAME.search(n):
        return "staffing / recruitment agency"
    if industry and _STAFFING_INDUSTRY.search(industry):
        return f"staffing / recruitment agency (LinkedIn industry: {industry.strip()[:60]})"
    if text and _CLIENT_POST.search(text[:8000]):
        return "posts on behalf of a client"
    it_industry = bool(industry and re.search(r"(?i)it services|it consulting|outsourcing", industry))
    if it_industry and _IT_SERVICES_NAME.search(n):
        return "IT-services body shop"
    if text and _IT_SERVICES_TEXT.search(text[:8000]) and (headcount or 0) >= 500:
        return "IT-services body shop"
    return None


# ------------------------------------------------------------------ dedup

def _index(session) -> dict[str, int]:
    """name_key -> company id, built once per session and kept on the session itself, so
    two threads never share it and a new session always sees the current table."""
    from sqlmodel import select

    from jobhunter.db import Company

    idx = session.info.get("admission_index")
    if idx is None:
        idx = {}
        for cid, cname, ckey, merged in session.exec(
                select(Company.id, Company.name, Company.name_key, Company.merged_into)).all():
            k = ckey or name_key(cname)
            if k and (k not in idx or merged is None):
                idx.setdefault(k, merged or cid)
        session.info["admission_index"] = idx
    return idx


def remember(session, company) -> None:
    """Put a just-flushed row into the session's index, so a twin later in the same
    batch finds it (the old code reserved id -1 here and then inserted the twin anyway)."""
    if company.id is None:
        session.flush()
    key = company.name_key or name_key(company.name)
    _index(session)[key] = company.id


def find_existing(session, name: str, domain: str | None = None):
    """Exact name, then normalized name key, then domain (when the name fits it).
    A merged row resolves to the row it was merged into."""
    from sqlmodel import select

    from jobhunter.db import Company

    row = session.exec(select(Company).where(Company.name == name)).first()
    if row is None:
        key = name_key(name)
        cid = _index(session).get(key) if key else None
        row = session.get(Company, cid) if cid else None
    if row is None and domain and not bogus_host(domain, name) and host_fits_name(name, domain):
        reg = registrable(domain)
        row = session.exec(select(Company).where(Company.domain == domain)).first() or (
            session.exec(select(Company).where(Company.domain == reg)).first() if reg != domain else None)
    for _ in range(4):
        if row is not None and row.merged_into:
            row = session.get(Company, row.merged_into)
    return row


# ------------------------------------------------------------------ the gate

@dataclass
class Candidate:
    """A company some source wants to create or update."""

    name: str
    source: str = ""
    website: str | None = None
    website_is_link: bool = False     # an apply/post link, not "their website"
    headline: bool = False            # the name was cut out of a headline or link text
    stage: str | None = None
    amount_usd_m: float | None = None
    total_usd_m: float | None = None
    valuation_usd_m: float | None = None
    announced: str | None = None
    evidence: str | None = None
    open_roles: int = 0
    hiring_verified: bool = False
    pay_band: str | None = None


@dataclass
class Verdict:
    ok: bool
    reason: str
    name: str | None = None           # the cleaned name to store
    website: str | None = None        # the website to store (None when it was bogus)
    domain: str | None = None
    existing: object | None = None    # the Company row this candidate is
    overrated: bool | None = None
    borderline: bool = False
    notes: list[str] = field(default_factory=list)

    def __iter__(self):
        yield self.ok
        yield self.reason


def admit(cand: Candidate, session=None) -> Verdict:
    """The one gate. ok=False means: do not create this company, and do not store its jobs."""
    name, how = clean_name(cand.name, headline=cand.headline, source=cand.source)
    if not name:
        return Verdict(False, f"junk name '{(cand.name or '')[:60]}': {how}")
    v = Verdict(True, "admitted", name=name)
    if how != "unchanged":
        v.notes.append(how)

    host = host_of(cand.website)
    if host:
        if bogus_host(host, name):
            v.notes.append(f"website {host} is a board/form/shortener — dropped")
        elif cand.website_is_link and not host_fits_name(name, host):
            v.notes.append(f"link {host} does not carry the name — not taken as their website")
        else:
            v.website = cand.website if "://" in (cand.website or "") else f"https://{host}"
            v.domain = host

    row = find_existing(session, name, v.domain) if session is not None else None
    v.existing = row
    if row is not None and row.list_key:
        # the ★ list is the decision: its companies are never turned away, famous or not
        v.reason = f"on the hand-picked list '{row.list_key}'"
        return v

    hit = famous(name)
    if hit:
        v.ok, v.overrated = False, True
        v.reason = f"overrated: famous ({hit[0]}: '{hit[1]}')"
        return v

    if row is not None:
        if row.status == "quarantined":
            v.ok, v.reason = False, f"quarantined: {row.status_reason or 'junk'}"
            return v
        if row.overrated:
            v.ok, v.overrated = False, True
            v.reason = f"overrated: {row.overrated_reason or 'marked overrated'}"
            return v

    from sqlmodel import func, select

    roles = cand.open_roles
    verified = cand.hiring_verified
    if row is not None and session is not None:
        from jobhunter.db import Job

        roles += session.exec(select(func.count()).select_from(Job).where(Job.company_id == row.id)).one()
        verified = verified or row.hiring_status == "verified"
    o = overrated_verdict(
        name,
        stage=cand.stage or (row.funding_stage if row is not None else None),
        amount_usd_m=cand.amount_usd_m or (row.funding_amount_usd_m if row is not None else None),
        total_usd_m=cand.total_usd_m or (row.funding_total_usd_m if row is not None else None),
        valuation_usd_m=cand.valuation_usd_m or (row.valuation_usd_m if row is not None else None),
        evidence=cand.evidence or (row.funding_evidence if row is not None else None),
        hiring_verified=verified, open_roles=roles,
        pay_band=cand.pay_band or (row.pay_power_band if row is not None else None),
    )
    v.overrated, v.borderline = o.overrated, o.borderline
    if o.overrated:
        v.ok, v.reason = False, f"overrated: {o.reason}"
        return v
    v.reason = "existing row" if row is not None else "new company"
    return v


def stamp(company, verdict: Verdict | None = None) -> None:
    """Write the identity fields every admitted row carries."""
    company.name_key = name_key(company.name)
    if verdict is not None and verdict.notes:
        tag = "[admit] " + "; ".join(verdict.notes)[:240]
        if tag not in (company.notes or ""):
            company.notes = f"{company.notes}\n{tag}".strip() if company.notes else tag


def months_old(date: str | None) -> int | None:
    """Age in months of 'YYYY-MM-DD', 'YYYY-MM' or 'YYYY'."""
    if not date:
        return None
    m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?", str(date))
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2) or 6), 1)
    except ValueError:
        return None
    from jobhunter.db import utcnow

    now = utcnow()
    return max(0, (now.year - d.year) * 12 + now.month - d.month)


__all__ = [
    "Candidate", "Verdict", "Overrated", "admit", "stamp", "clean_name", "name_key", "famous",
    "normalize_stage", "stage_rank", "parse_funding_card", "funding_facts", "credible_amount",
    "overrated_verdict", "host_of", "registrable", "bogus_host", "host_fits_name", "domain_of",
    "india_city", "india_other_city", "find_existing", "remember", "STAGES",
]
