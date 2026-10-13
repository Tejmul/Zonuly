"""Needs & gap analysis — what a company actually wants from this hire, and what of it
the resume can honestly answer for.

A posting's requirements list is the visible part. The rest is hidden in the same text
("you'll own…", "first ML hire", "from scratch"), in what the company says it is building
(its own site, its launches, its public repos) and in why it raised money. This module
reads all of that, deterministically first, and produces a NEEDS BRIEF per role:

    stated     requirement bullets, by section (required / responsibility / nice-to-have)
    implied    hidden signals in the JD and the company's own words, each with its quote
    building   what they are building now — description, launches, repos, public posts
    gaps       every need mapped to the resume bullet that covers it, or UNCOVERED
    helps      2–3 sentences, "how hiring Tejmul helps them", built ONLY from covered needs
    angles     suggested hooks for the outreach

Every item carries the quote it was read from and the URL it was read at. Nothing is
invented: a need with no source is not a need, and a covered need names the exact resume
line that covers it. The model (free OpenRouter chain, `cheap` alias) only *synthesises*
the prose of a brief, within a per-tick budget, and its sentences must pass the same
faithfulness check the review gate runs on drafts; when the daily ceiling is hit the
deterministic brief stands and `synthesis` says "pending".

Public sources only: keyless web search (search-result titles and snippets — LinkedIn
posts appear as public results and are never opened through a session), the company's own
pages already on file, and the public GitHub REST API through github_miner's token path
(which never borrows the personal `gh` login).

Layering: db / llm / research / contacts.github_miner / outreach.gates below; read by
confidence.py and the API. The drafter reads the stored brief off the Job row (JSON), it
never imports this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from jobhunter import CONFIG
from jobhunter.db import Company, Job, utcnow
from jobhunter.skills import IMPLIED_BY, SKILLS, skills_in   # the dictionary sits at the normalize layer

log = logging.getLogger(__name__)

_N = CONFIG.get("needs") or {}
BRIEF_VERSION = "needs-v2"
CONTEXT_TTL_DAYS = int(_N.get("context_ttl_days", 14))
BRIEF_TTL_DAYS = int(_N.get("brief_ttl_days", 14))
WEB_RESULTS = int(_N.get("web_results", 6))
SECTION_WEIGHT = {"required": 1.0, "responsibility": 0.8, "nice": 0.4, "general": 0.6, "implied": 0.6}

# Hidden signals: a phrase in the posting (or the company's own words) that implies a need
# it does not state, paired with the resume evidence that would answer it.
TRAITS: dict[str, dict[str, str]] = {
    "zero-to-one": {
        "jd": r"(first (?:engineering|engineer|ml|ai|backend|technical|full[- ]?stack|software)\s*hire|founding (?:engineer|team|member)|"
              r"from scratch|zero[- ]to[- ]one|0[- ]to[- ]1|greenfield|build (?:it|things|the \w+) from the ground up)",
        "need": "someone who builds 0→1 with no one to hand off to",
        "resume": r"(founding team|from scratch|solo|architected|founder)",
    },
    "ownership": {
        "jd": r"(you(?:'|’)ll own|you will own|own (?:the|our) [a-z\- ]{3,40}(?:end[- ]to[- ]end)?|end[- ]to[- ]end ownership|high ownership)",
        "need": "end-to-end ownership of a system",
        "resume": r"(shipped [^.]*solo|led the|architected|end-to-end|redesigned)",
    },
    "ships-fast": {
        "jd": r"(ship(?:ping)? (?:fast|quickly|daily|weekly|to production)|move fast|fast[- ]paced|bias (?:for|to) action|high velocity)",
        "need": "someone who ships to production quickly",
        "resume": r"(shipped|production|deployed)",
    },
    "scale-cost": {
        "jd": r"(scal(?:e|ing) (?:our|the) (?:infra|infrastructure|platform|systems?|backend)|cost[- ](?:efficien|optimi|reduc)|"
              r"reduce (?:cloud |infra(?:structure)? )?costs?|latency|performance (?:optimi|tuning))",
        "need": "someone who keeps infrastructure fast and cheap as it scales",
        "resume": r"(cost[s]? by \d+%|cutting infra cost|latency by \d+%|serverless migration)",
    },
    "customer-facing": {
        "jd": r"(work (?:directly )?with (?:our )?customers|customer[- ]facing|forward[- ]deployed|talk to (?:users|customers)|"
              r"solutions engineer|implementation)",
        "need": "someone who works directly with customers and turns asks into shipped systems",
        "resume": r"(clients|trained? [^.]*learners|mentored|conducted technical interviews)",
    },
    "ai-product": {
        "jd": r"(ai[- ]native|llm[- ]powered|ai[- ]powered|agentic|ai product|generative ai|copilot)",
        "need": "someone who builds LLM-powered product features, not just models",
        "resume": r"(agentic|llms?|rag|fine-tuned|openrouter)",
    },
    "remote-async": {
        "jd": r"(async(?:hronous)?(?:[- ]first)?|written communication|remote[- ]first|fully remote|distributed team)",
        "need": "someone who works well remote and async, in writing",
        "resume": r"·\s*remote\b",
    },
}

# where money is going — a funding announcement's "to …" clause
_FUNDING_USE = re.compile(
    r"(?:will use the (?:funds|capital|money|investment|round)|plans? to use|the (?:funds|capital|money|round|investment) "
    r"(?:will|would) (?:be used to|go (?:to|towards))|to (?:expand|scale|accelerate|hire|grow|build out|double down on|invest in))"
    r"\s+([^.;\n]{8,160})", re.I)
_LAUNCH = re.compile(r"\b(launch(?:es|ed)?|introduc(?:es|ing|ed)|announc(?:es|ing|ed)|now available|unveil(?:s|ed)?|"
                     r"releas(?:es|ed)|changelog|beta|open[- ]sourc(?:es|ed|ing))\b", re.I)
_STACK_LINE = re.compile(r"(our (?:tech )?stack|tech stack|we use|built (?:on|with)|written in|our services use)", re.I)
_PROBLEM = re.compile(r"\b(we(?:'re| are) (?:building|solving|working on|tackling)|our mission is|the problem)\b([^.\n]{10,200})", re.I)

_SECTIONS = [
    ("skip", re.compile(r"\b(benefits|perks|compensation|salary|what we offer|why (?:join|work)|equal (?:employment|opportunity)|"
                        r"eeo|diversity|accommodation|privacy|how to apply|interview process|our values|life at)\b", re.I)),
    ("nice", re.compile(r"\b(nice[- ]to[- ]have|bonus|preferred|pluses?|extra credit|great if|good to have|ideally)\b", re.I)),
    ("required", re.compile(r"\b(requirements?|qualifications?|what you(?:'|’)?ll? (?:need|bring)|you (?:have|bring|are|might be)|"
                            r"about you|must[- ]haves?|who you are|what we(?:'|’)?re looking for|we(?:'|’)?re looking for|"
                            r"skills|experience|ideal candidate|minimum)\b", re.I)),
    ("responsibility", re.compile(r"\b(responsibilit|what you(?:'|’)?ll (?:do|work on|build|be doing)|the role|your role|"
                                  r"day[- ]to[- ]day|in this role|you will|impact|what you(?:'|’)ll|key result|role overview)\b", re.I)),
    ("about", re.compile(r"\b(about (?:us|the company|the team|[A-Z]\w+)|who we are|the company|our mission|what we do|the product)\b", re.I)),
]

_BOILERPLATE = re.compile(r"(sign in|join now|forgot password|cookie policy|user agreement|email or phone|"
                          r"show more|show less|apply now|^\s*password\s*$|ai-powered advice)", re.I)
_SOFT = re.compile(r"\b(communicat|team player|collaborat|passion|curious|self[- ]starter|attention to detail|growth mindset|"
                   r"humble|empath|work ethic|values|culture|english|degree|bachelor|master'?s|b\.?s\.?|phd)\b", re.I)

# ------------------------------------------------------------------ experience requirement

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEARS = re.compile(
    r"(?:(?:minimum|min\.?|at least|over|more than)\s+(?:of\s+)?)?"
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
_YEARS_CTX = re.compile(r"(experience|exp\b|professional|industry|hands[- ]on|working|in (?:a |an )?\w+ role|building|shipping|"
                        r"background|track record|of (?:software|engineering|backend|frontend|ml|ai)|in (?:software|backend|"
                        r"frontend|ml|ai|data|devops|product|full))", re.I)
_YEARS_NOT = re.compile(r"(vesting|vest\b|warranty|founded|years? old|in business|years? ago|anniversary|history|cliff|"
                        r"\bfor \d+ years\b|track record of \d+ years of growth|retention)", re.I)
_FRESHER = re.compile(
    r"(\bfreshers?\b|new[- ]grad(?:uate)?s?|recent (?:college )?grad(?:uate)?s?|entry[- ]level|early[- ]career|"
    r"\b(?:20(?:25|26|27))\s*(?:grad(?:uate)?s?|batch|pass[- ]?outs?)|(?:class|batch) of 20(?:25|26|27)|"
    r"graduat(?:ing|e) in 20(?:25|26|27)|no (?:prior )?experience (?:required|needed)|0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)|"
    r"campus hir|university grad|junior)", re.I)


@dataclass
class Experience:
    min_years: float | None = None     # the smallest years figure the posting asks for, None = not stated
    max_years: float | None = None
    fresher: bool = False              # the posting welcomes freshers / new grads / 2025–2027 batches
    quote: str | None = None
    fresher_quote: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def parse_experience(text: str | None) -> Experience:
    """The experience bar a posting sets, read from its own sentences.

    "0-2 years" → min 0, fresher; "5+ years of professional experience" → min 5;
    "2026 graduates" → fresher. A years figure counts only in an experience context
    ("4-year vesting" is not a bar). When several bars appear, the LOWEST is kept — a
    "Fullstack SWE: 3+ years / Senior: 6+ years" posting is open at 3."""
    out = Experience()
    if not text:
        return out
    body = text[:8000]
    fm = _FRESHER.search(body)
    if fm:
        out.fresher, out.fresher_quote = True, _sentence_around(body, fm.start(), fm.end())
    best: tuple[float, float | None, str] | None = None
    for m in _YEARS.finditer(body):
        sent = _sentence_around(body, m.start(), m.end())
        if _YEARS_NOT.search(sent) or not _YEARS_CTX.search(sent):
            continue
        lo = _num(m.group(1))
        hi = _num(m.group(3)) if m.group(3) else None
        if lo is None or lo > 20 or (hi is not None and hi > 25):
            continue
        if best is None or lo < best[0]:
            best = (lo, hi, sent)
    if best:
        out.min_years, out.max_years, out.quote = best
        if best[0] <= 1 and not out.fresher:
            out.fresher, out.fresher_quote = True, best[2]
    return out


def _num(tok: str | None) -> float | None:
    if tok is None:
        return None
    tok = tok.lower()
    if tok in _WORDNUM:
        return float(_WORDNUM[tok])
    try:
        return float(tok)
    except ValueError:
        return None


def _sentence_around(text: str, start: int, end: int, limit: int = 240) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start))
    rights = [i for i in (text.find(".", end), text.find("\n", end), text.find(";", end)) if i != -1]
    right = min(rights) if rights else len(text)
    s = text[left + 1: right + 1].strip(" \t*-•#:").strip()
    return re.sub(r"\s+", " ", s)[:limit]


# ------------------------------------------------------------------ skills

# ------------------------------------------------------------------ the resume as evidence

@dataclass
class Evidence:
    id: str
    text: str
    where: str          # "AI Product Engineer Intern @ MVRX Labs (YC-P26)"
    kind: str           # bullet | project | header | skills
    skills: list[str] = field(default_factory=list)


def resume_evidence(profile: dict | None = None) -> list[Evidence]:
    """Every resume line that can cover a need: experience bullets, projects, the skills list."""
    if profile is None:
        from jobhunter import resume

        profile = resume.load_profile()
    out: list[Evidence] = []
    for i, e in enumerate(profile.get("experience") or []):
        where = f"{e.get('title')} @ {e.get('company')}"
        for j, h in enumerate(e.get("highlights") or []):
            out.append(Evidence(f"exp{i}.{j}", h, where, "bullet", skills_in(h)))
    for i, p in enumerate(profile.get("projects") or []):
        text = f"{p.get('description') or ''} ({', '.join(p.get('tech') or [])})"
        out.append(Evidence(f"proj{i}", text, f"Project: {p.get('name')}", "project", skills_in(text)))
    # where each internship was done ("MVRX Labs (YC-P26) Buckingham, London, UK· Remote") —
    # the structured profile drops it, the resume's own header line keeps it
    for i, line in enumerate(l.strip() for l in (profile.get("_resume_text") or "").splitlines()):
        if re.search(r"·\s*Remote\b", line) and len(line) < 120:
            out.append(Evidence(f"hdr{i}", line, "Resume (internship location)", "header", skills_in(line)))
    flat = [s for g in (profile.get("skills") or {}).values() if isinstance(g, list) for s in g]
    if flat:
        text = ", ".join(dict.fromkeys(flat))
        out.append(Evidence("skills", text, "Skills section", "skills", skills_in(text)))
    return out


@lru_cache(maxsize=1)
def _default_evidence() -> tuple[Evidence, ...]:
    return tuple(resume_evidence())


def _evidence(profile: dict | None) -> list[Evidence]:
    return resume_evidence(profile) if profile is not None else list(_default_evidence())


_RANK = {"bullet": 0, "project": 1, "header": 1, "skills": 2}


def cover_skill(skill: str, ev: list[Evidence]) -> Evidence | None:
    """The strongest resume line for this skill: a shipped bullet over a project over the
    skills list; for a generic ask ("backend") a line naming concrete work that answers it."""
    hits = [e for e in ev if skill in e.skills]
    if not any(e.kind != "skills" for e in hits):
        implied = [e for e in ev if e.kind != "skills" and set(e.skills) & set(IMPLIED_BY.get(skill, ()))]
        hits = implied + hits
    return min(hits, key=lambda e: _RANK[e.kind]) if hits else None


def resume_skills(profile: dict | None = None) -> set[str]:
    return {s for e in _evidence(profile) for s in e.skills}


# ------------------------------------------------------------------ reading a JD

@dataclass
class Need:
    text: str
    kind: str                 # stated | implied | building
    section: str              # required | responsibility | nice | general | implied
    weight: float
    quote: str
    url: str | None
    source: str               # jd | company-site | funding | web | github
    skills: list[str] = field(default_factory=list)
    trait: str | None = None
    # gap mapping
    status: str = "uncovered"       # covered | partial | uncovered
    evidence: str | None = None     # the exact resume line
    evidence_where: str | None = None
    evidence_id: str | None = None
    covered_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    evidence_skills: list[str] = field(default_factory=list)   # what the quoted line itself answers
    more_evidence: list[dict] = field(default_factory=list)     # other lines covering the rest

    def as_dict(self) -> dict:
        return asdict(self)


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or _BOILERPLATE.search(line):
            continue
        lines.append(line)
    return lines


def _heading(line: str) -> str | None:
    """The section a heading line opens, or None when the line is content."""
    bare = re.sub(r"^[#*\s]+|[*:\s]+$", "", line).strip()
    if not bare or len(bare) > 70:
        return None
    looks = line.rstrip().endswith(":") or line.startswith("#") or line.startswith("**") or bare.isupper() \
        or (len(bare.split()) <= 6 and not bare.endswith("."))
    if not looks:
        return None
    for name, rx in _SECTIONS:
        if rx.search(bare):
            return name
    return None


def sections(text: str) -> list[tuple[str, str]]:
    """(section, item) for every bullet or sentence of a JD, in order.

    Handles the three shapes the boards serve: headed bullet lists, inline
    "Requirements: a; b; c", and one undifferentiated blob (→ sentences, "general")."""
    out: list[tuple[str, str]] = []
    current = "general"
    for line in _clean_lines(text):
        inline = re.match(r"^\**\s*([A-Za-z’' ]{3,40}?)\s*\**\s*:\s*(.{20,})$", line)
        if inline:
            sec = _heading(inline.group(1) + ":")
            if sec:
                for part in re.split(r";\s+|\s+•\s+", inline.group(2)):
                    if len(part.strip()) > 12:
                        out.append((sec, part.strip()))
                current = sec
                continue
        sec = _heading(line)
        if sec:
            current = sec
            continue
        item = re.sub(r"^[\-*•·\d.)\s]+", "", line).strip().strip("*").strip()
        if len(item) < 12:
            continue
        if len(item) > 260:
            for sent in re.split(r"(?<=[.!?])\s+", item):
                if len(sent.strip()) >= 12:
                    out.append((current, sent.strip()))
        else:
            out.append((current, item))
    return out


def _short(text: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def jd_needs(job: Job) -> tuple[list[Need], list[dict]]:
    """(needs, building signals) read from the posting itself."""
    text = job.description or ""
    url = job.url
    needs: list[Need] = []
    building: list[dict] = []
    seen: set[str] = set()
    for sec, item in sections(text):
        if sec in ("skip",):
            continue
        if _YEARS.search(item) and _YEARS_CTX.search(item) and len(skills_in(item)) <= 1:
            continue     # an experience bar is seniority, scored separately — not a need
        skills = skills_in(item)
        if sec == "about":
            if _PROBLEM.search(item) or skills:
                building.append({"text": _short(item, 220), "quote": _short(item, 220), "url": url, "source": "jd"})
            continue
        if _STACK_LINE.search(item) and skills:
            building.append({"text": "stack: " + ", ".join(skills), "quote": _short(item, 220), "url": url, "source": "jd"})
        if not skills:
            # a line with no named skill is a need only when it carries a hidden signal
            # (handled below) — "strong communication" is not a gap anyone can map
            continue
        if _SOFT.search(item) and len(skills) == 0:
            continue
        key = ",".join(skills) + "|" + sec
        if key in seen:
            continue
        seen.add(key)
        needs.append(Need(text=_short(item), kind="stated", section=sec if sec != "about" else "general",
                          weight=SECTION_WEIGHT.get(sec, 0.6), quote=_short(item, 240), url=url, source="jd", skills=skills))
    # hidden signals in the posting
    for name, t in TRAITS.items():
        m = re.search(t["jd"], text[:8000], re.I)
        if m:
            q = _sentence_around(text, m.start(), m.end())
            needs.append(Need(text=t["need"], kind="implied", section="implied", weight=SECTION_WEIGHT["implied"],
                              quote=q, url=url, source="jd", trait=name, skills=skills_in(q)[:4]))
    for m in _PROBLEM.finditer(text[:8000]):
        q = _sentence_around(text, m.start(), m.end())
        building.append({"text": _short(q, 220), "quote": q, "url": url, "source": "jd"})
        if len(building) > 8:
            break
    return needs, _dedupe(building)


def _dedupe(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        k = re.sub(r"\W+", "", (it.get("quote") or it.get("text") or "").lower())[:80]
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


# ------------------------------------------------------------------ gap mapping

_ALTERNATIVES = re.compile(r"\b(or|such as|e\.g\.?|like|similar|any of|one of|and/or)\b", re.I)


def map_gaps(needs: list[Need], profile: dict | None = None) -> list[Need]:
    """Each need → the resume line that covers it, or UNCOVERED. Deterministic.

    A skill need is covered when every skill it names is on the resume (a shipped bullet
    or project = covered; only the skills list = partial); covered in part when some are.
    A trait need is covered only when a bullet matches the trait's resume pattern."""
    ev = _evidence(profile)
    for n in needs:
        if n.trait:
            rx = re.compile(TRAITS[n.trait]["resume"], re.I)
            hit = next((e for e in ev if e.kind in ("bullet", "project", "header") and rx.search(e.text)), None)
            if hit:
                n.status, n.evidence, n.evidence_where, n.evidence_id = "covered", hit.text, hit.where, hit.id
            continue
        if not n.skills:
            continue
        covers = {s: cover_skill(s, ev) for s in n.skills}
        have = [s for s, e in covers.items() if e]
        n.covered_skills, n.missing_skills = have, [s for s in n.skills if s not in have]
        if not have:
            n.status = "uncovered"
            continue
        # the shipped line covering the most of this need's skills; the skills list only
        # when no bullet or project names any of them
        lines = [covers[s] for s in have]
        shipped_lines = [e for e in ev if e.kind != "skills" and e in lines or
                         (e.kind != "skills" and set(e.skills) & set(have))]
        pool = shipped_lines or lines
        best = min(pool, key=lambda e: (-len(set(e.skills) & set(have)), _RANK[e.kind]))
        n.evidence, n.evidence_where, n.evidence_id = best.text, best.where, best.id
        n.evidence_skills = [s for s in have if covers[s] == best or s in best.skills
                             or set(best.skills) & set(IMPLIED_BY.get(s, ()))]
        for s in have:
            e = covers[s]
            if s not in n.evidence_skills and e is not None and e != best \
                    and all(m["id"] != e.id for m in n.more_evidence):
                n.more_evidence.append({"id": e.id, "text": e.text, "where": e.where, "skill": s})
        shipped = all(covers[s].kind != "skills" for s in have)
        if n.missing_skills and len(n.skills) > 1 and _ALTERNATIVES.search(n.quote):
            # "AWS, Azure, or GCP", "LangChain, LlamaIndex or similar": one of them is the ask
            n.missing_skills = []
        n.status = "covered" if (not n.missing_skills and shipped) else "partial"
    return needs


def coverage(needs: list[Need]) -> dict:
    """Weighted share of the needs the resume covers (partial counts half)."""
    total = sum(n.weight for n in needs)
    if total <= 0:
        return {"share": None, "covered": 0, "partial": 0, "uncovered": 0, "total": 0}
    got = sum(n.weight * (1.0 if n.status == "covered" else 0.5 if n.status == "partial" else 0.0) for n in needs)
    return {"share": round(got / total, 3),
            "covered": sum(1 for n in needs if n.status == "covered"),
            "partial": sum(1 for n in needs if n.status == "partial"),
            "uncovered": sum(1 for n in needs if n.status == "uncovered"),
            "total": len(needs)}


# ------------------------------------------------------------------ the company's own words

def company_signals(company: Company | None, context: dict | None = None) -> tuple[list[Need], list[dict]]:
    """(implied needs, building signals) from the company row and its cached web context."""
    if company is None:
        return [], []
    needs: list[Need] = []
    building: list[dict] = []
    site = company.website
    if company.description:
        building.append({"text": _short(company.description, 220), "quote": company.description[:300],
                         "url": site, "source": "company-site"})
    if company.story and (company.story_evidence or "").startswith("http"):
        building.append({"text": _short(company.story, 220), "quote": company.story[:300],
                         "url": company.story_evidence, "source": "company-site"})
    texts = [(company.funding_evidence or "", company.funding_evidence if (company.funding_evidence or "").startswith("http") else site, "funding")]
    for item in (context or {}).get("web") or []:
        texts.append((f"{item.get('title') or ''}. {item.get('snippet') or ''}", item.get("url"), "web"))
    for text, url, src in texts:
        for m in _FUNDING_USE.finditer(text):
            use = m.group(1).strip()
            skills = skills_in(use)
            needs.append(Need(text=f"the money is for: {_short(use, 120)}", kind="implied", section="implied",
                              weight=0.5, quote=_short(_sentence_around(text, m.start(), m.end()), 240), url=url,
                              source=src, skills=skills))
    for item in (context or {}).get("web") or []:
        blob = f"{item.get('title') or ''}. {item.get('snippet') or ''}"
        if _LAUNCH.search(blob) or _PROBLEM.search(blob) or skills_in(blob):
            building.append({"text": _short(item.get("title") or blob, 200), "quote": _short(blob, 280),
                             "url": item.get("url"), "source": item.get("kind") or "web",
                             "published": item.get("published")})
    for repo in (context or {}).get("repos") or []:
        desc = repo.get("description") or ""
        building.append({"text": f"public repo {repo.get('name')}" + (f" ({repo['language']})" if repo.get("language") else "")
                         + (f": {_short(desc, 140)}" if desc else ""),
                         "quote": desc or repo.get("name"), "url": repo.get("url"), "source": "github",
                         "skills": skills_in(f"{desc} {repo.get('language') or ''}")})
    return needs, _dedupe(building)


def fetch_context(company: Company, *, web_search: bool = True, github: bool = True) -> dict:
    """Public context for one company: keyless web results and its GitHub org's repos.

    Search results only — titles and snippets as the engine shows them. A LinkedIn or X
    post found this way is read as the public result it is; nothing logs in."""
    out: dict[str, Any] = {"at": utcnow().isoformat(timespec="seconds"), "web": [], "repos": [], "errors": []}
    name = company.name
    if web_search:
        from jobhunter.research import web

        domain = (company.domain or "").strip()
        queries = [
            f'"{name}" {domain} launches OR announces OR introducing OR building'.strip(),
            f'"{name}" engineering OR hiring OR "we are building" site:linkedin.com/posts OR site:x.com',
        ]
        stem = re.sub(r"[^a-z0-9]", "", name.lower())[:10]
        for q in queries:
            try:
                res = web.search(q, limit=WEB_RESULTS, only=web.KEYLESS)
            except Exception as e:  # noqa: BLE001 — context is optional
                out["errors"].append(f"search: {e}"[:160])
                continue
            if res.get("error") and not res.get("results"):
                out["errors"].append(str(res["error"])[:160])
            for r in res.get("results") or []:
                blob = re.sub(r"[^a-z0-9]", "", f"{r.get('title')} {r.get('snippet')} {r.get('url')}".lower())
                if stem and stem not in blob:
                    continue    # a result that never names the company is about someone else
                url = r.get("url") or ""
                kind = "linkedin-post" if "linkedin.com/posts" in url else "x-post" if re.search(r"//(x|twitter)\.com/", url) \
                    else "company-site" if domain and domain in url else "web"
                out["web"].append({"title": (r.get("title") or "")[:200], "snippet": (r.get("snippet") or "")[:400],
                                   "url": url, "published": r.get("published"), "kind": kind})
    if github and company.github_org:
        try:
            import httpx

            from jobhunter.contacts.github_miner import headers

            r = httpx.get(f"https://api.github.com/orgs/{company.github_org}/repos",
                          params={"sort": "pushed", "per_page": 8, "type": "public"}, headers=headers(), timeout=20)
            if r.status_code < 400:
                for item in r.json() or []:
                    if item.get("fork") or item.get("archived"):
                        continue
                    out["repos"].append({"name": item.get("name"), "description": (item.get("description") or "")[:200],
                                         "language": item.get("language"), "url": item.get("html_url"),
                                         "pushed_at": item.get("pushed_at"), "stars": item.get("stargazers_count", 0)})
            else:
                out["errors"].append(f"github {r.status_code}")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"github: {e}"[:160])
    return out


def context_for(session, company: Company, *, refresh: bool = False, network: bool = True) -> dict:
    """The company's cached public context, fetched when missing or stale (and allowed)."""
    cached = None
    if company.needs_context:
        try:
            cached = json.loads(company.needs_context)
        except json.JSONDecodeError:
            cached = None
    fresh = company.needs_context_at is not None and (utcnow() - company.needs_context_at).days < CONTEXT_TTL_DAYS
    if cached is not None and fresh and not refresh:
        return cached
    if not network:
        return cached or {}
    ctx = fetch_context(company)
    company.needs_context = json.dumps(ctx)[:20000]
    # a fetch that found nothing because every channel was down is not an answer: leave it
    # unstamped so the next tick tries again (one that found nothing with channels up is)
    empty_by_failure = not ctx["web"] and not ctx["repos"] and ctx["errors"]
    company.needs_context_at = None if empty_by_failure else utcnow()
    session.add(company)
    session.commit()
    return ctx


# ------------------------------------------------------------------ the brief

@lru_cache(maxsize=1)
def _default_first_name() -> str:
    from jobhunter import resume

    return ((resume.load_profile().get("name") or "the candidate").split() or ["the candidate"])[0]


def candidate_first_name(profile: dict | None = None) -> str:
    if profile is None:
        return _default_first_name()
    return ((profile.get("name") or "the candidate").split() or ["the candidate"])[0]


def deterministic_helps(needs: list[Need], company: str, who: str) -> str | None:
    """2–3 sentences built ONLY from covered needs: their words, then the resume line."""
    covered = sorted((n for n in needs if n.status == "covered" and n.evidence),
                     key=lambda n: (-n.weight, n.kind != "stated"))
    if not covered:
        return None
    parts, used = [], set()
    for n in covered:
        if n.evidence_id in used:
            continue
        used.add(n.evidence_id)
        what = ", ".join(n.evidence_skills) if n.evidence_skills else n.text
        parts.append(f"{company} needs {what}; {who} has done it: \"{_short(n.evidence, 140)}\" ({n.evidence_where}).")
        if len(parts) == 3:
            break
    return " ".join(parts)


def deterministic_angles(needs: list[Need], building: list[dict]) -> list[str]:
    out = []
    covered = [n for n in needs if n.status == "covered" and n.evidence]
    implied = [n for n in covered if n.kind == "implied"]
    if implied:
        n = implied[0]
        out.append(f"Lead with the hidden need ({n.text}): their words \"{_short(n.quote, 100)}\", "
                   f"your proof \"{_short(n.evidence, 100)}\".")
    stated = sorted((n for n in covered if n.kind == "stated"), key=lambda n: -n.weight)
    if stated:
        n = stated[0]
        out.append(f"Map their top requirement ({', '.join(n.evidence_skills) or n.text}) to \"{_short(n.evidence, 100)}\".")
    launch = next((b for b in building if b.get("source") in ("web", "linkedin-post", "x-post", "company-site")
                   and _LAUNCH.search(b.get("quote") or "")), None)
    if launch:
        out.append(f"Reference what they just shipped: \"{_short(launch['quote'], 110)}\".")
    return out[:3]


SYNTH_SYSTEM = """You write a short, factual hiring brief. You may only restate facts you are given:
the company's own quotes and the candidate's resume lines. Never add a number, product, employer,
technology or claim that is not in the input. If the input is thin, say less. Reply with JSON only."""

SYNTH_PROMPT = """ROLE: {title} at {company}

WHAT THEY ARE BUILDING (their words, numbered):
{building}

NEEDS THE CANDIDATE COVERS (need -> their quote -> the candidate's resume line):
{covered}

NEEDS THE CANDIDATE DOES NOT COVER (never claim these):
{uncovered}

Write:
- "building": one sentence, what they are building now, from the numbered quotes only.
- "implied": up to 3 needs the posting does not state but the quotes imply, each citing the quote number it rests on.
- "helps": 2-3 sentences on how hiring {who} helps them, using ONLY the covered needs and the resume lines given.
- "angles": 1-2 outreach angles, each one sentence, using only covered needs.

Reply with exactly:
{{"building": "...", "implied": [{{"need": "...", "quote": <number>}}], "helps": "...", "angles": ["..."]}}"""


def _corpus(company: str, who: str, needs: list[Need], building: list[dict], profile: dict | None) -> str:
    from jobhunter import resume

    p = profile or resume.load_profile()
    bits = [company, who, p.get("name") or "", resume.profile_summary(p, max_chars=4000)]
    bits += [e.text + " " + e.where for e in _evidence(profile)]
    bits += [n.quote + " " + (n.evidence or "") + " " + " ".join(n.covered_skills) for n in needs if n.status != "uncovered"]
    bits += [b.get("quote") or "" for b in building]
    return "\n".join(bits)


def synthesize(title: str, company: str, who: str, needs: list[Need], building: list[dict],
               profile: dict | None = None) -> tuple[dict | None, str]:
    """(model synthesis or None, status). Status: done | pending | rejected | skipped.

    pending = the model was unavailable or over its ceiling — the deterministic brief stands
    and the next tick tries again. rejected = it answered, but a sentence named something the
    sources do not, so it was dropped (the gate strips, it never rewrites)."""
    from jobhunter import llm
    from jobhunter.outreach import gates

    covered = [n for n in needs if n.status in ("covered", "partial") and n.evidence]
    if not covered:
        return None, "skipped"
    uncovered = [n for n in needs if n.status == "uncovered"]
    numbered = building[:10]
    prompt = SYNTH_PROMPT.format(
        title=title, company=company, who=who,
        building="\n".join(f"{i + 1}. {b.get('quote')}" for i, b in enumerate(numbered)) or "(none on file)",
        covered="\n".join(f"- {n.text} -> \"{_short(n.quote, 160)}\" -> \"{n.evidence}\"" for n in covered[:10]),
        uncovered="\n".join(f"- {n.text}" + (f" ({', '.join(n.missing_skills or n.skills)})" if n.skills else "")
                            for n in uncovered[:10]) or "(none)",
    )
    try:
        data = llm.chat_json(prompt, SYNTH_SYSTEM, temperature=0.2, num_predict=700, alias="cheap",
                             purpose="needs-brief", default=None)
    except llm.BudgetExceeded:
        return None, "pending"
    except llm.LLMUnavailable:
        return None, "pending"
    if not isinstance(data, dict):
        return None, "pending"
    corpus = _corpus(company, who, needs, numbered, profile)
    forbidden = forbidden_terms(needs, profile)
    out: dict = {"implied": []}
    rejected = []
    for key in ("building", "helps"):
        text = str(data.get(key) or "").strip()
        if not text:
            continue
        verdict = gates.check(text, corpus=corpus, forbidden=forbidden)
        bad = verdict.unsupported + verdict.forbidden_hits
        if bad:
            rejected.append(f"{key}: {', '.join(bad[:4])}")
        else:
            out[key] = text[:700]
    angles = []
    for a in (data.get("angles") or [])[:2]:
        a = str(a).strip()
        v = gates.check(a, corpus=corpus, forbidden=forbidden)
        if a and not v.unsupported and not v.forbidden_hits:
            angles.append(a[:300])
    out["angles"] = angles
    for imp in (data.get("implied") or [])[:3]:
        if not isinstance(imp, dict):
            continue
        try:
            idx = int(imp.get("quote")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(numbered) and imp.get("need"):
            src = numbered[idx]
            out["implied"].append({"need": str(imp["need"])[:200], "quote": src.get("quote"), "url": src.get("url"),
                                   "source": src.get("source")})
    if rejected:
        out["rejected"] = rejected
    if not out.get("helps") and not out.get("building") and not angles:
        return out, "rejected"
    return out, "done"


def forbidden_terms(needs: list[Need], profile: dict | None = None) -> list[str]:
    """Skills a brief or a draft must never claim: named by an uncovered need, absent from the resume."""
    have = resume_skills(profile)
    out: list[str] = []
    for n in needs:
        if n.status == "covered":
            continue
        for s in (n.missing_skills or ([] if n.status == "partial" else n.skills)):
            if s not in have and s not in out:
                out.append(s)
    return out


def build_brief(job: Job, company: Company | None, *, context: dict | None = None, synth: bool = False,
                profile: dict | None = None) -> dict:
    """The needs brief for one role. Deterministic unless `synth`; never raises on a dead model."""
    jneeds, jbuild = jd_needs(job)
    cneeds, cbuild = company_signals(company, context)
    needs = map_gaps(jneeds + cneeds, profile)
    building = _dedupe(jbuild + cbuild)
    exp = parse_experience(job.description)
    who = candidate_first_name(profile)
    cname = company.name if company else job.company_name
    cov = coverage(needs)
    brief: dict[str, Any] = {
        "version": BRIEF_VERSION,
        "job_id": job.id, "company": cname, "title": job.title, "url": job.url,
        "experience": exp.as_dict(),
        "stated": [n.as_dict() for n in needs if n.kind == "stated"],
        "implied": [n.as_dict() for n in needs if n.kind == "implied"],
        "building": building[:12],
        "coverage": cov,
        "forbidden": forbidden_terms(needs, profile),
        "helps": deterministic_helps(needs, cname, who),
        "angles": deterministic_angles(needs, building),
        "synthesis": "none",
        "context": {"web": len((context or {}).get("web") or []), "repos": len((context or {}).get("repos") or []),
                    "errors": (context or {}).get("errors") or [], "at": (context or {}).get("at")},
        "built_at": utcnow().isoformat(timespec="seconds"),
    }
    if synth:
        s, status = synthesize(job.title, cname, who, needs, building, profile)
        brief["synthesis"] = status
        if s:
            brief["model"] = s
            if s.get("helps"):
                brief["helps_model"] = s["helps"]
            if s.get("angles"):
                brief["angles"] = (s["angles"] + brief["angles"])[:3]
    return brief


def brief_for(job: Job, company: Company | None) -> dict:
    """The stored brief if there is one, else a deterministic one built now (no network, no model)."""
    if job.needs_brief:
        try:
            return json.loads(job.needs_brief)
        except json.JSONDecodeError:
            pass
    ctx = None
    if company is not None and company.needs_context:
        try:
            ctx = json.loads(company.needs_context)
        except json.JSONDecodeError:
            ctx = None
    b = build_brief(job, company, context=ctx, synth=False)
    b["stored"] = False
    return b


def store_brief(session, job: Job, brief: dict) -> None:
    job.needs_brief = json.dumps(brief, default=str)[:60000]
    job.needs_brief_at = utcnow()
    job.needs_synthesis = brief.get("synthesis")
    session.add(job)


def jd_hash(job: Job) -> str:
    return hashlib.sha1(f"{job.title}|{(job.description or '')[:8000]}".encode()).hexdigest()[:12]


__all__ = [
    "SKILLS", "TRAITS", "Need", "Evidence", "Experience", "parse_experience", "skills_in", "resume_evidence",
    "sections", "jd_needs", "map_gaps", "coverage", "company_signals", "fetch_context", "context_for",
    "build_brief", "brief_for", "store_brief", "synthesize", "forbidden_terms", "deterministic_helps",
]
