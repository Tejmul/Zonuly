"""Who at a company can actually refer us — and in what order we ask them.

MOTIV §4 step 4. A contact list is worthless until it is ordered: a founder at a
40-person startup reads their own inbox, an SDE 2 can file a referral in one click,
a general HR mailbox is where cold mail goes to die. So every contact gets a
`role_class`, a `seniority` and a `referral_rank`, and the outreach queue reads the
rank.

Rules first, model second. The keyword pass is free, deterministic and explains
itself (`role_evidence` holds the text it matched); only the residue it cannot place
goes to the `cheap` OpenRouter alias, and a model answer that is not one of the known
classes is discarded rather than trusted.

No alumni or college targeting: that network is already known personally and does not
need a machine (Tejmul, 2026-09-03).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Contact, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_P = ((CONFIG.get("targeting") or {}).get("people") or {})
_ROLE_CFG = _P.get("roles") or []

# key -> (rank, can refer, human label). Config owns the order; code owns the detection.
RANKS: dict[str, int] = {r["key"]: int(r["rank"]) for r in _ROLE_CFG}
CAN_REFER: dict[str, bool] = {r["key"]: bool(r.get("refer")) for r in _ROLE_CFG}
LABELS: dict[str, str] = {r["key"]: r.get("label", r["key"]) for r in _ROLE_CFG}
CLASSES = list(RANKS) or ["founder", "senior_engineer", "engineer", "eng_manager", "tech_recruiter", "recruiter", "other"]
DEFAULT_RANK = 9

# Order matters: the first pattern that matches wins, so the more specific title —
# "engineering manager" before "engineer", "technical recruiter" before "recruiter" —
# has to come first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("founder", re.compile(
        r"\b(co[-\s]?founder|founder|ceo|chief executive|cto|chief technology officer|"
        r"chief technical officer|chief scientist|chief ai officer)\b", re.I)),
    ("eng_manager", re.compile(
        r"\b(engineering manager|eng manager|em of|head of engineering|head of ai|head of ml|"
        r"head of platform|director of engineering|engineering director|vp of engineering|"
        r"vp engineering|vice president of engineering|tech lead manager|team lead)\b", re.I)),
    ("tech_recruiter", re.compile(
        r"\b(technical recruiter|tech recruiter|engineering recruiter|technical sourcer|"
        r"technical talent|talent partner[- ]engineering|university recruit|campus recruit|"
        r"technical hr|tech hr)\b", re.I)),
    ("senior_engineer", re.compile(
        r"\b((?:senior|sr\.?|staff|principal|lead|distinguished|founding)\s+"
        r"(?:software |backend |frontend |full[- ]?stack |platform |infrastructure |data |ml |ai |research |devops |site reliability )?"
        r"(?:engineer|developer|scientist|sde|swe)"
        r"|sde\s*(?:3|iii|4|iv)\b|l[5-9]\b|architect)\b", re.I)),
    ("engineer", re.compile(
        r"\b(software engineer|software developer|sde\s*[12]?\b|sde\b|swe\b|"
        r"backend engineer|back[- ]end engineer|frontend engineer|front[- ]end engineer|"
        r"full[- ]?stack (?:engineer|developer)|platform engineer|infrastructure engineer|"
        r"devops engineer|site reliability engineer|sre\b|"
        r"ml engineer|machine learning engineer|ai engineer|llm engineer|genai engineer|"
        r"applied (?:ai|ml|scientist)|research engineer|research scientist|data engineer|"
        r"data scientist|mobile engineer|android engineer|ios engineer|"
        r"engineer|developer|programmer)\b", re.I)),
    ("recruiter", re.compile(
        r"\b(recruiter|recruiting|talent acquisition|talent|people ops|people operations|"
        r"human resources|hr\b|hiring|staffing)\b", re.I)),
]

_SENIORITY = [
    ("exec", re.compile(r"\b(ceo|cto|founder|chief|vp|vice president|president|head of|director)\b", re.I)),
    ("staff", re.compile(r"\b(staff|principal|distinguished|fellow|architect|l[6-9]\b)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|lead|sde\s*(?:3|iii)|l5\b)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|intern|graduate|associate|sde\s*(?:1|i)\b|entry[- ]level|new grad)\b", re.I)),
]

# A shared mailbox is a role signal in itself — nobody named "careers" refers anyone.
_ALIAS_ROLE = {
    "careers": "recruiter", "jobs": "recruiter", "hr": "recruiter", "recruiting": "recruiter",
    "recruitment": "recruiter", "talent": "recruiter", "hiring": "recruiter", "people": "recruiter",
    "founders": "founder", "founder": "founder", "ceo": "founder",
}


@dataclass
class RoleVerdict:
    role_class: str = "other"
    seniority: str | None = None
    referral_rank: int = DEFAULT_RANK
    evidence: str | None = None
    method: str = "rules"        # rules | alias | model | default

    @property
    def can_refer(self) -> bool:
        return CAN_REFER.get(self.role_class, False)

    def as_dict(self) -> dict:
        return {
            "role_class": self.role_class, "label": LABELS.get(self.role_class, self.role_class),
            "seniority": self.seniority, "referral_rank": self.referral_rank,
            "evidence": self.evidence, "method": self.method, "can_refer": self.can_refer,
        }


def _seniority_of(text: str) -> str | None:
    for level, pattern in _SENIORITY:
        if pattern.search(text):
            return level
    return None


def classify(role: str | None, *, name: str | None = None, email: str | None = None,
             bio: str | None = None) -> RoleVerdict:
    """Classify one person from whatever public text we hold. Never guesses silently."""
    blob = " ".join(filter(None, [role, bio]))
    if blob.strip():
        for key, pattern in _PATTERNS:
            m = pattern.search(blob)
            if m:
                return RoleVerdict(
                    role_class=key,
                    seniority=_seniority_of(blob),
                    referral_rank=RANKS.get(key, DEFAULT_RANK),
                    evidence=m.group(0)[:120],
                    method="rules",
                )

    local = (email or "").split("@")[0].lower().strip(".") if email else ""
    if local in _ALIAS_ROLE:
        key = _ALIAS_ROLE[local]
        return RoleVerdict(key, None, RANKS.get(key, DEFAULT_RANK), f"address: {local}@", "alias")

    return RoleVerdict("other", _seniority_of(blob) if blob else None, RANKS.get("other", DEFAULT_RANK),
                       None, "default")


# ------------------------------------------------------------------ model residue

_SYSTEM = """You label the job role of one person at a technology company, from public
text only. You never invent a role. If the text does not say what they do, you answer
"other". JSON only."""

_PROMPT = """Person: {name}
Public text about them (bio, title, or profile line):
---
{text}
---

Which one of these describes their job? Answer with the key, not a sentence.
  founder          - founder, co-founder, CEO, CTO
  eng_manager      - engineering manager, head/director/VP of engineering
  senior_engineer  - senior, staff, principal, lead or founding engineer
  engineer         - software engineer, SDE, ML/AI engineer, data scientist
  tech_recruiter   - technical recruiter, engineering sourcer, technical HR
  recruiter        - general HR, talent acquisition, people ops
  other            - anything else, or the text does not say

Reply with exactly:
{{"role_class": "<key>", "evidence": "<the words you read it from, quoted from the text>"}}"""


def classify_with_model(name: str | None, text: str) -> RoleVerdict | None:
    """The residue the keyword pass could not place, on the `cheap` OpenRouter alias.

    Returns None on any failure — no key, budget refusal, bad JSON, or a class that is
    not one of ours. A missing label is better than a wrong one.
    """
    if not (text or "").strip():
        return None
    from jobhunter import llm

    try:
        data = llm.chat_json(
            _PROMPT.format(name=name or "unknown", text=text[:1500]),
            _SYSTEM, temperature=0.0, alias="cheap", purpose="role-class", default=None,
        )
    except Exception as e:  # noqa: BLE001 — a classifier is never worth stopping a run for
        log.debug("role model call failed: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    key = str(data.get("role_class") or "").strip().lower()
    if key not in CLASSES:
        return None
    return RoleVerdict(key, _seniority_of(text), RANKS.get(key, DEFAULT_RANK),
                       str(data.get("evidence") or "")[:120] or None, "model")


# ------------------------------------------------------------------ persistence

def classify_contacts(limit: int | None = None, *, only_missing: bool = True,
                      use_model: bool = False) -> dict:
    """Label every contact in the database. Commits per contact; safe to re-run."""
    init_db()
    stats: dict[str, int] = {"scanned": 0, "model_calls": 0}
    with get_session() as session:
        q = select(Contact)
        if only_missing:
            q = q.where(col(Contact.role_class).is_(None))
        if limit:
            q = q.limit(limit)
        for c in session.exec(q).all():
            stats["scanned"] += 1
            verdict = classify(c.role, name=c.name, email=c.email, bio=c.research_notes)
            if verdict.role_class == "other" and use_model:
                text = " ".join(filter(None, [c.role, c.research_notes])).strip()
                if text:   # no text is nothing to ask about — do not spend a call on it
                    stats["model_calls"] += 1
                    better = classify_with_model(c.name, text)
                    if better:
                        verdict = better
                else:
                    stats["no_text"] = stats.get("no_text", 0) + 1
            c.role_class = verdict.role_class
            c.seniority = verdict.seniority
            c.referral_rank = verdict.referral_rank
            c.role_evidence = verdict.evidence
            c.is_recruiter = verdict.role_class in ("recruiter", "tech_recruiter")
            c.classified_at = utcnow()
            session.add(c)
            session.commit()
            stats[verdict.role_class] = stats.get(verdict.role_class, 0) + 1
    return stats


def referral_queue(company_id: int | None = None, limit: int = 50) -> list[dict]:
    """Contacts ordered by who is most likely to actually refer us."""
    init_db()
    with get_session() as session:
        q = select(Contact)
        if company_id:
            q = q.where(Contact.company_id == company_id)
        rows = session.exec(q).all()
    out = [
        {
            "id": c.id, "company_id": c.company_id, "name": c.name, "role": c.role,
            "role_class": c.role_class, "label": LABELS.get(c.role_class or "", c.role_class),
            "seniority": c.seniority, "rank": c.referral_rank or DEFAULT_RANK,
            "email": c.email, "confidence": c.confidence, "evidence": c.role_evidence,
        }
        for c in rows
    ]
    # rank first, then a verified address beats a guessed one — an unreachable
    # founder is worth less than an SDE whose email we actually confirmed
    conf_order = {"verified": 0, "pattern-guessed": 1, "scraped": 2}
    out.sort(key=lambda r: (r["rank"], conf_order.get(r["confidence"] or "", 3), r["name"] or ""))
    return out[:limit]


__all__ = ["RoleVerdict", "classify", "classify_with_model", "classify_contacts",
           "referral_queue", "CLASSES", "RANKS", "LABELS"]
