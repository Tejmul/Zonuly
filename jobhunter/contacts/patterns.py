"""Email pattern inference and candidate generation.

If we already know one real address at a company (from GitHub commits or the
website), we can infer the pattern and apply it to every other name we found —
no API budget, no guessing from a blank slate.
"""

from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# ordered by how common they are in tech companies
COMMON_PATTERNS = [
    "{first}",
    "{first}.{last}",
    "{f}{last}",
    "{first}{last}",
    "{first}_{last}",
    "{f}.{last}",
    "{first}{l}",
    "{last}{f}",
]

ALL_PATTERNS = COMMON_PATTERNS + ["{last}", "{f}{l}", "{first}-{last}", "{last}.{first}", "{first}.{l}"]


def normalize_name(name: str | None) -> tuple[str, str] | None:
    """'José García-López' -> ('jose', 'garcialopez'). Returns None if unusable."""
    if not name:
        return None
    # strip accents so they match ASCII mailbox conventions
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^A-Za-z\s'\-]", " ", ascii_name)
    parts = [p for p in re.split(r"[\s]+", ascii_name.strip()) if len(p) > 1]
    # drop honorifics and suffixes
    parts = [p for p in parts if p.lower() not in {"dr", "mr", "ms", "mrs", "jr", "sr", "phd", "md", "ii", "iii"}]
    if len(parts) < 2:
        return None
    first = re.sub(r"[^a-z]", "", parts[0].lower())
    last = re.sub(r"[^a-z]", "", parts[-1].lower())
    if not first or not last:
        return None
    return first, last


def render(pattern: str, first: str, last: str, domain: str) -> str:
    local = (
        pattern.replace("{first}", first)
        .replace("{last}", last)
        .replace("{f}", first[:1])
        .replace("{l}", last[:1])
    )
    return f"{local}@{domain}"


def infer_pattern(known_email: str, name: str | None) -> str | None:
    """Given a real address and the person's name, work out the company's pattern."""
    parsed = normalize_name(name)
    if not parsed or "@" not in known_email:
        return None
    first, last = parsed
    local = known_email.split("@")[0].lower()
    for pattern in ALL_PATTERNS:
        candidate = (
            pattern.replace("{first}", first)
            .replace("{last}", last)
            .replace("{f}", first[:1])
            .replace("{l}", last[:1])
        )
        if candidate == local:
            return pattern
    return None


def learn_from_contacts(people: list[dict], domain: str) -> str | None:
    """Vote on the pattern across every known (name, email) pair at this domain."""
    votes: dict[str, int] = {}
    for p in people:
        email, name = p.get("email"), p.get("name")
        if not email or not name or not email.lower().endswith("@" + domain.lower()):
            continue
        pat = infer_pattern(email, name)
        if pat:
            votes[pat] = votes.get(pat, 0) + 1
    if not votes:
        return None
    best = max(votes.items(), key=lambda kv: kv[1])
    log.info("pattern for %s: %s (%d/%d agree)", domain, best[0], best[1], sum(votes.values()))
    return best[0]


def candidates(name: str, domain: str, pattern: str | None = None, *, limit: int = 4) -> list[str]:
    """Candidate addresses for a person, best guess first."""
    parsed = normalize_name(name)
    if not parsed or not domain:
        return []
    first, last = parsed

    if pattern:
        # a known pattern is worth far more than a spread of guesses
        return [render(pattern, first, last, domain)]

    out: list[str] = []
    for pat in COMMON_PATTERNS:
        addr = render(pat, first, last, domain)
        if addr not in out:
            out.append(addr)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ learning with evidence
#
# 2026-10-08 ("emails for ready referrers, without blind guessing"): a pattern is only
# ever USED when the company's own addresses agree on it — at least MIN_AGREE real,
# named addresses on its domain, or one plus the company's own page stating the format
# ("write to firstname.lastname@acme.com"). One vote was enough before, and 513 companies
# carried a pattern read off a single commit address.

from dataclasses import dataclass, field  # noqa: E402

MIN_AGREE = 2
#: the leading pattern must hold at least this share of the matched votes …
MIN_SHARE = 0.5
#: … and at least this many times the runner-up's votes
MIN_LEAD = 2


@dataclass
class PatternVerdict:
    pattern: str | None                 # the confirmed pattern, or None
    status: str                         # confirmed | weak | conflict | none
    agree: int = 0                      # votes for the leading pattern (a stated format counts one)
    matched: int = 0                    # votes over every pattern
    votes: dict = field(default_factory=dict)
    examples: list = field(default_factory=list)   # addresses that agree with the leader
    unmatched: int = 0                  # named addresses on the domain no pattern explains
    stated: str | None = None           # the page that stated the format, if any
    candidate: str | None = None        # the leading pattern even when not confirmed
    reason: str = ""

    def as_dict(self) -> dict:
        return {"pattern": self.pattern, "status": self.status, "agree": self.agree, "matched": self.matched,
                "votes": self.votes, "examples": self.examples[:6], "unmatched": self.unmatched,
                "stated": self.stated, "candidate": self.candidate, "reason": self.reason}


def infer_all(known_email: str, name: str | None) -> list[str]:
    """Every pattern that renders this address from this name (usually one)."""
    parsed = normalize_name(name)
    if not parsed or "@" not in (known_email or ""):
        return []
    first, last = parsed
    local = known_email.split("@")[0].lower()
    out = []
    for pattern in ALL_PATTERNS:
        if render(pattern, first, last, "x").split("@")[0] == local and pattern not in out:
            out.append(pattern)
    return out


_STATED = [
    (re.compile(r"^(first|firstname|fname|given)\.(last|lastname|lname|surname|family)$"), "{first}.{last}"),
    (re.compile(r"^(first|firstname|fname|given)_(last|lastname|lname|surname|family)$"), "{first}_{last}"),
    (re.compile(r"^(first|firstname|fname|given)-(last|lastname|lname|surname|family)$"), "{first}-{last}"),
    (re.compile(r"^(first|firstname|fname|given)(last|lastname|lname|surname|family)$"), "{first}{last}"),
    (re.compile(r"^(f|finitial)\.(last|lastname|surname)$"), "{f}.{last}"),
    (re.compile(r"^(flast|flastname|f(last|lastname))$"), "{f}{last}"),
    (re.compile(r"^(firstl|firstnamel|first\.l)$"), "{first}{l}"),
    (re.compile(r"^(firstname|first)$"), "{first}"),
]


def stated_pattern(example_address: str | None) -> str | None:
    """'firstname.lastname@acme.com' on a page → '{first}.{last}'. None when it is not a
    format example (a real address, john.doe@, your.name@)."""
    if not example_address or "@" not in example_address:
        return None
    local = example_address.split("@")[0].lower().strip()
    for rx, pat in _STATED:
        if rx.match(local):
            return pat
    return None


def learn_with_evidence(examples: list[tuple[str | None, str]], *, stated: tuple[str, str] | None = None,
                        min_agree: int = MIN_AGREE) -> PatternVerdict:
    """Vote over (name, address) pairs already confirmed on the company's domain.

    `stated` is (pattern, url) when the company's own page states its format; it counts as
    one agreeing vote. Confirmed only when the leader has ≥ `min_agree` votes, holds at
    least MIN_SHARE of the matched votes and MIN_LEAD× the runner-up's. A name that
    cannot be patterned (one word, initials only, CJK) is skipped; an address two patterns
    both explain ("li.li@") is ambiguous and skipped."""
    votes: dict[str, int] = {}
    support: dict[str, list[str]] = {}
    unmatched = 0
    seen: set[str] = set()
    for name, addr in examples:
        addr = (addr or "").lower().strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        if not normalize_name(name):
            continue
        pats = infer_all(addr, name)
        if not pats:
            unmatched += 1
            continue
        if len(pats) > 1:
            continue
        votes[pats[0]] = votes.get(pats[0], 0) + 1
        support.setdefault(pats[0], []).append(addr)
    stated_url = None
    if stated and stated[0]:
        votes[stated[0]] = votes.get(stated[0], 0) + 1
        stated_url = stated[1]
    if not votes:
        return PatternVerdict(None, "none", unmatched=unmatched,
                              reason="no named address on the domain matches a known pattern")
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], ALL_PATTERNS.index(kv[0])
                                                   if kv[0] in ALL_PATTERNS else 99))
    lead, n = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    total = sum(votes.values())
    v = PatternVerdict(None, "weak", agree=n, matched=total, votes=dict(ranked), examples=support.get(lead, []),
                       unmatched=unmatched, stated=stated_url if stated and stated[0] == lead else None,
                       candidate=lead)
    if second and (n < MIN_LEAD * second or n / total < MIN_SHARE):
        v.status, v.reason = "conflict", f"addresses disagree: {dict(ranked)}"
        return v
    if n < min_agree:
        v.reason = f"{n} example{'s' if n != 1 else ''} for {lead}; need {min_agree} (or one + their own page)"
        return v
    v.pattern, v.status = lead, "confirmed"
    v.reason = (f"{n} of {total} agree on {lead}" + (f"; their page states it ({stated_url})" if v.stated else ""))
    return v


def patternable(name: str | None) -> bool:
    """A real first + last name the pattern can be rendered from."""
    return normalize_name(name) is not None


def confirmed(company) -> str | None:
    """The company's pattern when its evidence confirms it, else None — every writer that
    renders an address from a pattern reads this, never company.email_pattern directly."""
    import json

    if company is None or not getattr(company, "email_pattern", None):
        return None
    try:
        ev = json.loads(company.email_pattern_evidence or "{}")
    except (TypeError, ValueError):
        return None
    if isinstance(ev, dict) and ev.get("status") == "confirmed" and ev.get("pattern") == company.email_pattern:
        return company.email_pattern
    return None
