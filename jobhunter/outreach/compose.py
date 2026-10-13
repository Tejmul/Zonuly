"""Compose: the personalisation contract, the four structures, subject lines, the
deterministic fill and the quality score of one cold referral ask.

Where every rule comes from is knowledge/cold-email-playbook.md. In short: open with
one specific thing of THEIRS (their commit, repo, post, launch, round), say who you are
in one clause, give one quantified proof that answers what the role asks for, make ONE
low-friction ask, and make "yes" cheap with a two-line note they can forward and a
resume link. 60–120 words, plain text, short sentences.

THE CONTRACT. Every slot is filled from evidence or the draft is not written:

    first_name      a real name, not a handle ("kbysani", "愛してる", "Platform Company" fail)
    recipient_role  what they do there (their headline, or the class the evidence gives)
    company         the company
    target_role     the specific role being asked about (an engineering title on file)
    hook            ONE specific thing, with its source: their post / commit / repo / bio,
                    shared ground, else the company's launch / round / own description
    need + proof    ONE need the needs brief marks COVERED, and the resume line covering it
    resume_link     outreach.user_links "Resume: …"

A missing slot is returned in `missing` and the caller files "incomplete" instead of a
generic email. Uncovered needs are never claimed (gates.check enforces it).

THE STRUCTURES, chosen by who they are:

    proof_opinion      engineers and founders at small startups: what I've shipped, with
                       numbers, then "what do you think?" (they can offer the referral)
    referral_engineer  engineers at larger companies: one proof, "would you be open to
                       referring me, or pointing me to whoever is hiring?"
    founder_direct     founders at larger companies: "would you consider me for …"
    recruiter_direct   recruiters: "could you put me forward, or is it open to 2027 grads?"

Pure: no DB, no model, no network. drafter.py gathers the inputs and calls the model.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from jobhunter import CONFIG
from jobhunter.outreach import gates

_O = CONFIG.get("outreach") or {}
_C = _O.get("compose") or {}
REWRITE_BELOW = int(_C.get("rewrite_below", 60))
GOOD_FROM = int(_C.get("good_from", 80))
SMALL_TEAM = int(_C.get("small_team", 60))
LEARN_MIN_SENT = int(_C.get("learn_min_sent", 20))
PROOFS: list[dict] = list(_C.get("proofs") or [])

STRUCTURES = {
    "proof_opinion": {
        "label": "Proof of work, then their opinion",
        "for": "engineers and founders at small startups",
        "ask_kind": "opinion",
        "why": "a low-pressure question about shipped work invites a reply and lets them offer the referral",
    },
    "referral_engineer": {
        "label": "Referral ask to an engineer",
        "for": "engineers at larger companies",
        "ask_kind": "referral",
        "why": "an engineer can refer; one proof plus a yes/no ask with an easy out",
    },
    "founder_direct": {
        "label": "Direct ask to a founder",
        "for": "founders at larger startups",
        "ask_kind": "consider",
        "why": "a founder decides; ask to be considered, or pointed to whoever is hiring",
    },
    "recruiter_direct": {
        "label": "Direct ask to a recruiter",
        "for": "recruiters and talent partners",
        "ask_kind": "put-forward",
        "why": "a recruiter routes candidates; name the role and ask to be put forward",
    },
}

# hook types, most specific first, with how specific each one is (feeds the quality score)
HOOK_SPECIFICITY = {
    "person_post": 1.0, "shared_ground": 0.95, "person_commit": 0.9, "person_repo": 0.8, "person_bio": 0.7,
    "company_launch": 0.7, "company_funding": 0.6, "company_product": 0.45,
}
PERSON_HOOKS = {"person_post", "shared_ground", "person_commit", "person_repo", "person_bio"}

CONTRACT_FIELDS = ["first_name", "recipient_role", "company", "target_role", "hook", "need", "proof", "resume_link"]
CONTRACT_LABEL = {
    "first_name": "missing first name (real name, not a handle)", "recipient_role": "missing their role",
    "company": "missing company", "target_role": "missing the role to ask about",
    "hook": "missing hook (nothing specific of theirs or the company's on record)",
    "need": "missing a covered need (needs brief)", "proof": "missing a resume proof for that need",
    "resume_link": "missing resume link (outreach.user_links)",
}

_NOT_NAMES = {
    "platform", "company", "team", "admin", "info", "hr", "careers", "career", "jobs", "job", "support", "hello",
    "contact", "sales", "hiring", "recruiting", "talent", "office", "engineering", "dev", "devs", "bot", "github",
    "noreply", "no-reply", "the", "official", "labs", "inc", "ltd", "pvt", "private", "limited", "people", "founders",
}
_NAME_TOKEN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]*$")
_ROLE_WORDS = re.compile(
    r"engineer|developer|founder|\bcto\b|\bceo\b|\bcoo\b|manager|lead\b|recruit|talent|scientist|architect|head\b|"
    r"director|\bvp\b|\bsde\b|\bswe\b|intern|programmer|researcher|designer|product|staff|principal|technical|"
    r"partner|president|officer|hr\b|people|analyst|consultant|specialist|founding", re.I)
_CLASS_WORD = {"founder": "founder", "engineer": "engineer", "senior_engineer": "senior engineer",
               "eng_manager": "engineering manager", "tech_recruiter": "technical recruiter", "recruiter": "recruiter"}
_SMALL_STAGES = re.compile(r"pre[- ]?seed|seed|angel|series a\b|bootstrap", re.I)
_JUNK_DESC = re.compile(r"read the full story|cookie|javascript|sign in|log in|subscribe|©|privacy|"
                        r"page not found|404|access denied|just a moment|icon:|https?://|\.com\w|streamline", re.I)
# a practice or coursework repo is not "their work" (2026-10-14: a NeetCode-submissions hook)
_PRACTICE_REPO = re.compile(r"leetcode|neetcode|hackerrank|codeforces|submissions?|solutions?|dotfiles|config|homework|"
                            r"assignment|course|tutorial|practice|learn|bootcamp|\.github\.io|resume|portfolio|test", re.I)
REPO_MIN_STARS = int(_C.get("repo_min_stars", 3))
_LAUNCH = re.compile(r"\b(launch(?:es|ed|ing)?|introduc(?:es|ed|ing)|announc(?:es|ed|ing)|unveil(?:s|ed)?|"
                     r"releas(?:es|ed)|now available|ships?|shipped|open[- ]sourc(?:es|ed))\b", re.I)


# ------------------------------------------------------------------ slots from evidence

def first_name(name: str | None) -> str | None:
    """A real first name, or None. Handles, scripts we cannot address, initials-only,
    company/team words and digits all fail — the contract then says "missing first name"."""
    raw = html.unescape(name or "").strip()
    if not raw or re.search(r"[\d_@]", raw):
        return None
    toks = [t for t in re.split(r"[\s,]+", raw) if t]
    if not toks or any(t.lower().strip(".") in _NOT_NAMES for t in toks):
        return None
    if not all(_NAME_TOKEN.match(t) for t in toks):
        return None
    if len(toks) == 1 and (toks[0].islower() or re.search(r"-", toks[0])):
        return None                      # "kbysani", "jdoe-acme": a login
    first = toks[0]
    if len(first.rstrip(".")) < 2 and len(toks) > 1:
        first = toks[1]                  # "J. Ramesh" -> Ramesh
    first = first.rstrip(".")
    if len(first) < 2:
        return None
    if first.isupper() or first.islower():
        first = first.capitalize()
    return first


def recipient_role(role: str | None, role_class: str | None) -> str | None:
    """Their role in their own words (headline / bio, first clause), else the class the
    evidence gave them. None for an unknown "other"."""
    txt = html.unescape(role or "").strip()
    txt = re.sub(r"^(LinkedIn|GitHub)\s*:\s*", "", txt, flags=re.I)
    head = re.split(r"\s*(?:\||•|·|\n|;|\.\s)\s*", txt)[0]
    head = re.split(r"\s+(?:@|at)\s+|\s*@", head, flags=re.I)[0].strip(" .,-–@")
    if head and len(head) <= 70 and _ROLE_WORDS.search(head) and not re.match(r"(commits|address)", head, re.I):
        return head
    return _CLASS_WORD.get(role_class or "")


def short_role(title: str | None) -> str | None:
    """'Backend Engineer - Studio Media Platform' -> 'Backend Engineer'; at most 6 words."""
    if not title:
        return None
    t = re.split(r"\s+[-–|]\s+|\s*\(|,|\s+/\s+", title.strip())[0].strip()
    t = re.sub(r"\s+jobs?$", "", t, flags=re.I)              # "Frontend Developer Jobs"
    head = re.split(r"\s+for\s+", t, flags=re.I)[0]
    if len(head.split()) >= 2:
        t = head                                            # "Founding Engineer for AI Consumer App"
    words = t.split()
    return " ".join(words[:6]) if words else None


def is_small(company: dict) -> bool:
    size = company.get("team_size")
    if size:
        return int(size) <= SMALL_TEAM
    return bool(_SMALL_STAGES.search(company.get("funding_stage") or ""))


def _repo_url(repo: str) -> str:
    return f"https://github.com/{repo}"


def _words(text: str, n: int) -> str:
    w = (text or "").split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def _clean_title(t: str) -> str:
    t = re.sub(r"\s*[|·]\s*(LinkedIn|X|Twitter|YouTube|Medium|TechCrunch|Inc42|YourStory).*$", "", t or "", flags=re.I)
    t = re.sub(r"\s+on (LinkedIn|X)\s*:?.*$", "", t, flags=re.I)
    t = re.sub(r"\s+[|·]\s+[^|·]{1,40}$", "", t)          # "… | Sarvam AI": the site name
    return t.strip(" .:-–\"'")


def hooks(person: dict, company: dict, building: list[dict], me: dict) -> list[dict]:
    """Every hook the evidence supports, most specific first. Each one is
    {type, sentence, anchor, source, specificity}: `sentence` is the email's first line,
    `anchor` the words that must survive any rewrite, `source` where it was read."""
    out: list[dict] = []
    cname = company.get("name") or ""
    pname = (person.get("name") or "").lower()
    xh = (person.get("x_handle") or "").lower().lstrip("@")

    # their own post saying the company is hiring
    by = (company.get("hiring_claim_by") or "").lower().lstrip("@")
    if by and company.get("hiring_claim_source") in ("x", "hn") and (by == xh or (pname and by == pname)):
        where = "X" if company["hiring_claim_source"] == "x" else "Hacker News"
        out.append({"type": "person_post", "anchor": f"{where} post",
                    "sentence": f"I saw your {where} post that {cname} is hiring.",
                    "source": company.get("hiring_claim_url") or where})

    # shared ground: their own text names my school or a place I worked
    theirs = " ".join(filter(None, [person.get("role"), person.get("role_evidence"), person.get("affiliation_evidence"),
                                   person.get("bio")]))
    for place in me.get("shared_places") or []:
        if place and re.search(rf"\b{re.escape(place)}\b", theirs, re.I):
            school = place.lower() in (me.get("school") or "").lower()
            out.append({"type": "shared_ground", "anchor": place,
                        "sentence": (f"I saw you're also from {place}." if school
                                     else f"I saw you were at {place} too; I interned there."),
                        "source": person.get("role_source") or "their public profile"})
            break

    # their commits to the company's own public repos (guardrails recorded the repo + date)
    m = re.search(r"committed to ([\w.\-]+/[\w.\-]+) on (\d{4}-\d{2}-\d{2})", person.get("affiliation_evidence") or "")
    if m:
        out.append({"type": "person_commit", "anchor": m.group(1),
                    "sentence": f"I came across your commits to {m.group(1)} on GitHub.", "source": _repo_url(m.group(1))})

    # their own repos (researcher.github_work): one with a description
    for r in person.get("repos") or []:
        desc = (r.get("description") or "").strip().rstrip(".")
        if (r.get("name") and desc and len(desc.split()) >= 3 and (r.get("stars") or 0) >= REPO_MIN_STARS
                and not _PRACTICE_REPO.search(f"{r['name']} {desc}")):
            out.append({"type": "person_repo", "anchor": r["name"],
                        "sentence": f"I came across your {r['name']} repo on GitHub ({_words(desc, 12).rstrip('…')}).",
                        "source": r.get("url") or "GitHub"})
            break

    # their bio: "React Native Contributor", "maintainer of X"
    bio = html.unescape(" ".join(filter(None, [person.get("role"), person.get("bio")])))
    m = re.search(r"([A-Z][\w.+#]*(?:\s[A-Z][\w.+#]*){0,2})\s+(Contributor|Maintainer)\b", bio) or \
        re.search(r"(contributor|maintainer) (?:to|of|at) ([A-Z][\w.+#]*(?:\s[A-Z][\w.+#]*){0,2})", bio, re.I)
    if m:
        what, verb = (m.group(1), m.group(2)) if m.group(2)[0].isupper() and m.group(2).lower() in ("contributor", "maintainer") \
            else (m.group(2), m.group(1))
        verb = "contribute to" if verb.lower() == "contributor" else "maintain"
        out.append({"type": "person_bio", "anchor": what, "sentence": f"I saw you {verb} {what}.",
                    "source": person.get("role_source") or "their public profile"})

    # the company: a launch they announced (needs-brief building signals with a URL)
    for b in building or []:
        q = b.get("text") or b.get("quote") or ""
        if b.get("source") in ("web", "linkedin-post", "x-post", "company-site") and b.get("url") and _LAUNCH.search(q):
            title = _clean_title(b.get("text") or q)
            if 4 <= len(title.split()) <= 16 and not _JUNK_DESC.search(title):
                out.append({"type": "company_launch", "anchor": _words(title, 4).rstrip("…"),
                            "sentence": f"I saw {cname}'s recent news, \"{title}\".", "source": b["url"]})
                break

    # the company's round, when a real source (not our own list note) states it
    ev = company.get("funding_evidence") or ""
    stage = (company.get("funding_stage") or "").strip()
    if stage and ev and not ev.startswith("★") and re.search(r"rais|fund|round|series|seed|invest", ev, re.I):
        amt = company.get("funding_amount_usd_m")
        # the amount only when the evidence itself states it (a stored total is not this round)
        stated = re.search(r"\$\s?(\d[\d.,]*)\s?(?:M|Mn|million)\b", ev, re.I)
        amount = f"${stated.group(1)}M " if stated else (
            f"${amt:g}M " if amt and re.search(rf"\b{re.escape(f'{amt:g}')}\b", ev) else "")
        out.append({"type": "company_funding", "anchor": stage,
                    "sentence": f"Congrats on {cname}'s {amount}{stage}.", "source": ev if ev.startswith("http") else
                    (company.get("website") or "their announcement")})

    # what the company says it builds (its own site), first sentence
    desc = re.split(r"(?<=[.!?])\s", (company.get("description") or "").strip())[0].strip().rstrip(".")
    if desc and 6 <= len(desc.split()) <= 30 and not _JUNK_DESC.search(desc):
        out.append({"type": "company_product", "anchor": cname,
                    "sentence": f"I read how {cname} describes its work: \"{_words(desc, 16).rstrip('…')}\".",
                    "source": company.get("website") or "their site"})

    for h in out:
        h["specificity"] = HOOK_SPECIFICITY[h["type"]]
    return out


def _proof_line(evidence: str) -> dict | None:
    """The short, true phrasing of one resume line (config outreach.compose.proofs), or a
    trimmed clause of the line itself when no phrasing matches. None for the skills list."""
    for p in PROOFS:
        if p.get("match") and p["match"].lower() in (evidence or "").lower():
            return {"line": p["line"], "topic": p.get("topic"), "source_line": evidence}
    clauses = [c.strip() for c in re.split(r";", evidence or "") if c.strip()]
    if not clauses or len(evidence.split(",")) > 12:
        return None                        # a skills list is not proof of work
    best = next((c for c in clauses if re.search(r"\d", c)), clauses[0])
    words = best.split()
    if len(words) > 24:
        best = ",".join(" ".join(words[:24]).split(",")[:-1]) or " ".join(words[:24])
    if best[:1].isupper() and best[1:2].islower():
        best = best[0].lower() + best[1:]
    return {"line": best.rstrip("."), "topic": None, "source_line": evidence}


def _label(skill: str) -> str:
    """A skill label inside a sentence: "Machine learning" -> "machine learning"; acronyms
    and names ("LLMs", "AWS", "PostgreSQL", "AI agents") stay as they are."""
    w = skill.split()[0] if skill.split() else skill
    if w[:1].isupper() and w[1:].islower() and len(w) > 1 and w.lower() not in ("python", "docker", "kubernetes",
                                                                                "react", "temporal", "terraform"):
        return skill[0].lower() + skill[1:]
    return skill


def needs_and_proofs(brief: dict | None, limit: int = 3) -> list[dict]:
    """Covered needs from the stored needs brief, each mapped to a distinct resume proof,
    best first (covered over partial, heavier, a number in the proof). Skills-list and
    header "evidence" do not count as proof."""
    if not brief:
        return []
    items = [n for n in (brief.get("stated") or []) + (brief.get("implied") or [])
             if n.get("status") in ("covered", "partial") and n.get("evidence")
             and not (n.get("text") or "").startswith("the money is for")
             and (n.get("evidence_where") or "") not in ("Skills section", "Resume (internship location)")]
    out, seen = [], set()
    scored = []
    for n in items:
        p = _proof_line(n["evidence"])
        if not p:
            continue
        scored.append(((n.get("status") != "covered", not re.search(r"\d", p["line"]), -(n.get("weight") or 0),
                        n.get("kind") != "stated"), n, p))
    scored.sort(key=lambda x: x[0])
    nums_seen: set[str] = set()
    for _, n, p in scored:
        nums = frozenset(re.findall(r"\d[\d,]*%?", p["line"]))
        if p["line"] in seen or (nums and nums & nums_seen):
            continue                       # two proofs that both say "95%" read as one
        seen.add(p["line"])
        nums_seen |= nums
        skills = n.get("evidence_skills") or n.get("covered_skills") or []
        if n.get("kind") == "stated" and skills:
            what = " and ".join(_label(x) for x in skills[:2])
        else:
            what = (n.get("text") or "").strip()
        out.append({"need": n.get("text"), "what": _words(what, 10).rstrip("…"), "kind": n.get("kind"),
                    "status": n.get("status"), "quote": n.get("quote"), "url": n.get("url"),
                    "proof": p["line"], "topic": p["topic"], "proof_source": n.get("evidence_where"),
                    "resume_line": n["evidence"]})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ the contract

@dataclass
class Contract:
    first_name: str | None = None
    full_name: str | None = None
    recipient_role: str | None = None
    role_class: str | None = None
    company: str | None = None
    target_role: str | None = None
    target_role_full: str | None = None
    target_role_url: str | None = None
    hook: dict | None = None
    hook_alternatives: list[dict] = field(default_factory=list)
    need: dict | None = None
    proofs: list[dict] = field(default_factory=list)
    resume_link: str | None = None
    structure: str | None = None
    structure_why: str | None = None
    small: bool = False
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict:
        d = asdict(self)
        d["missing_labels"] = [CONTRACT_LABEL[m] for m in self.missing]
        return d


def choose_structure(role_class: str | None, small: bool) -> tuple[str, str]:
    rc = role_class or "other"
    if rc in ("tech_recruiter", "recruiter"):
        return "recruiter_direct", "they are a recruiter: name the role, ask to be put forward"
    if rc in ("engineer", "senior_engineer", "eng_manager", "founder") and small:
        return "proof_opinion", ("a small startup (seed / Series A or a small team): show shipped work and ask "
                                 "their opinion — the low-pressure ask that lets them offer the referral")
    if rc == "founder":
        return "founder_direct", "a founder at a larger startup: ask to be considered for the role"
    return "referral_engineer", "an engineer at a larger company: one proof, a referral ask with an easy out"


def contract(person: dict, company: dict, job: dict | None, brief: dict | None, me: dict) -> Contract:
    """Fill every slot from evidence; record what could not be filled."""
    c = Contract()
    c.first_name = first_name(person.get("name"))
    c.full_name = html.unescape(person.get("name") or "") or None
    c.role_class = person.get("role_class")
    c.recipient_role = recipient_role(person.get("role"), c.role_class)
    c.company = company.get("name")
    if job:
        c.target_role_full = job.get("title")
        c.target_role = short_role(job.get("title"))
        c.target_role_url = job.get("url")
    hs = hooks(person, company, (brief or {}).get("building") or [], me)
    c.hook = hs[0] if hs else None
    c.hook_alternatives = hs[1:4]
    nps = needs_and_proofs(brief)
    c.need = nps[0] if nps else None
    c.proofs = nps
    c.resume_link = me.get("resume_link")
    c.small = is_small(company)
    c.structure, c.structure_why = choose_structure(c.role_class, c.small)
    for f in CONTRACT_FIELDS:
        val = {"need": c.need, "proof": c.proofs[0]["proof"] if c.proofs else None}.get(f, getattr(c, f, None))
        if not val:
            c.missing.append(f)
    return c


# ------------------------------------------------------------------ subjects

SUBJECT_PATTERNS = {
    "referral_engineer": ["referral_q", "bond_role", "name_role"],
    "founder_direct": ["role_q", "bond_role", "name_role"],
    "recruiter_direct": ["bond_role", "role_q", "name_role"],
    "proof_opinion": ["proof_take", "bond_role", "name_role"],
}


def subject_options(c: Contract, me: dict) -> list[dict]:
    """Every subject the structure's patterns give that passes the gate's subject rules."""
    role, co, first = c.target_role or "", c.company or "", c.first_name or ""
    bond = me.get("bond") or "CS+AI '27 grad"
    topic = next((p["topic"] for p in c.proofs if p.get("topic")), None)
    make = {
        "referral_q": f"Referral for {role} at {co}?",
        "role_q": f"{role} at {co}?",
        "bond_role": f"{bond}, {role} at {co}",
        "name_role": f"{first}, {role} at {co}?",
        "proof_take": f"{topic}: your take?" if topic else None,
    }
    out = []
    for pat in SUBJECT_PATTERNS.get(c.structure or "", []):
        s = make.get(pat)
        if s and role and co and not gates.subject_problems(s):
            out.append({"pattern": pat, "subject": s})
    if not out and role and co:
        out.append({"pattern": "role_plain", "subject": f"{role} at {co}"[:60]})
    return out


def pick(options: list, key: Any) -> int:
    """A stable rotation over the options (so variants spread evenly and repeat on a rerun)."""
    if not options:
        return -1
    return int(hashlib.sha1(str(key).encode()).hexdigest(), 16) % len(options)


# ------------------------------------------------------------------ the deterministic fill

def ask_line(c: Contract) -> str:
    return {
        # the role is named in the line before; the ask says "it" (no repetition)
        "referral_engineer": "Would you be open to referring me for it, or pointing me to whoever is hiring?",
        "founder_direct": "Would you be open to considering me for it, or pointing me to whoever is hiring?",
        "recruiter_direct": "Could you put me forward for it, or tell me if it's open to 2027 graduates?",
        "proof_opinion": "What do you think: is this the kind of work your team needs in that role?",
    }[c.structure or "referral_engineer"]


def blurb(c: Contract, me: dict) -> tuple[str, str]:
    """(intro line, the forwardable note in quotes) — two lines a referrer can paste."""
    p = c.proofs[0]["proof"]
    p = p[0].upper() + p[1:]
    note = (f"\"{me.get('full_name')}, final-year B.Tech CS+AI at {me.get('school')} ({me.get('year')}). {p}. "
            f"Interested in the {c.target_role} role at {c.company}. Resume: {c.resume_link}\"")
    intro = ("If you think it's a fit, here's a short note you could forward:" if c.structure == "proof_opinion"
             else "If it's easier, here's a short note you can forward:")
    return intro, note


def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def fill(c: Contract, me: dict) -> dict:
    """The message (greeting → ask) written from the slots alone. Returns
    {message, blurb_intro, blurb}: the drafter adds blurb, opt-out line and signature."""
    first, co, role = c.first_name, c.company, c.target_role
    intro = f"I'm {me.get('first_name')}, a final-year CS+AI student at {me.get('school')}"
    need = c.need or {}
    asks_for = (f"asks for {need['what']}" if need.get("kind") == "stated"
                else f"suggests you need {need.get('what')}")
    p1 = c.proofs[0]["proof"]
    if c.structure == "proof_opinion":
        shown = [p["proof"] for p in c.proofs[:3]]
        bullets = "\n".join(f"- {_cap(p)}" for p in shown)
        mid = f"{intro}. Some things I've built:\n{bullets}"
        close = f"Your {role} posting {asks_for}. {ask_line(c)}"   # names the role; the ask says "that role"
        paras = [c.hook["sentence"], mid, close]
    elif c.structure == "recruiter_direct":
        mid = f"{intro}, graduating in 2027, and I'd like to apply for the {role} role. It {asks_for}, and I {p1}."
        paras = [c.hook["sentence"], mid, ask_line(c)]
    elif c.structure == "founder_direct":
        mid = f"{intro}, and I'd like to be considered for the {role} role. It {asks_for}, and I {p1}."
        paras = [c.hook["sentence"], mid, ask_line(c)]
    else:
        mid = f"{intro}, applying for the {role} role at {co}. The posting {asks_for}, and I {p1}."
        paras = [c.hook["sentence"], mid, ask_line(c)]
    intro_b, note = blurb(c, me)
    return {"message": f"Hi {first},\n\n" + "\n\n".join(paras), "blurb_intro": intro_b, "blurb": note}


def assemble(message: str, blurb_intro: str, note: str, signature: str, opt_out: str | None = None) -> str:
    """The full body exactly as it would be sent: message, the forwardable note, the
    opt-out line, "Thanks," and the signature."""
    parts = [message.strip(), f"{blurb_intro}\n{note}"]
    if opt_out:
        parts.append(opt_out)
    return "\n\n".join(parts) + f"\n\nThanks,\n{signature}"


# ------------------------------------------------------------------ checks on any version

def keeps_contract(message: str, c: Contract) -> list[str]:
    """What a (model) rewrite dropped that the contract requires to be in the message."""
    low = (message or "").lower()
    out = []
    if c.first_name and c.first_name.lower() not in low[:80]:
        out.append("first name in the greeting")
    if c.company and c.company.lower() not in low and c.structure != "proof_opinion" and c.structure != "founder_direct" \
            and c.structure != "recruiter_direct":
        out.append("company name")
    if c.target_role and c.target_role.lower() not in low:
        out.append("role")
    if c.hook and c.hook.get("anchor") and c.hook["anchor"].lower() not in low:
        out.append("hook")
    for p in (c.proofs[:3] if c.structure == "proof_opinion" else c.proofs[:1]):
        nums = re.findall(r"\d[\d,]*%?", p["proof"])
        if nums and not any(n in message for n in nums):
            out.append(f"proof number ({nums[0]})")
    if "?" not in message:
        out.append("the ask")
    return out


def quality(message: str, subject: str, c: Contract, flags: list[dict], words: int, *, has_blurb: bool,
            has_resume: bool) -> dict:
    """0–100 and its parts. Personalisation completeness 30, hook specificity 20, length 15,
    one ask 10, readability 10, subject 5, faithfulness 10. Under REWRITE_BELOW: "rewrite"."""
    kinds = [f["kind"] for f in flags]
    present = [
        bool(c.first_name and c.first_name in message[:80]),
        bool(c.company and (c.company.lower() in message.lower() or c.structure in ("proof_opinion", "founder_direct",
                                                                                      "recruiter_direct"))),
        bool(c.target_role and c.target_role.lower() in message.lower()),
        bool(c.hook and c.hook.get("anchor", "").lower() in message.lower()),
        bool(c.proofs and any(re.findall(r"\d", p["proof"]) and p["proof"][:20].lower() in message.lower()
                              for p in c.proofs) or (c.proofs and c.proofs[0]["proof"][:20].lower() in message.lower())),
        "?" in message,
        has_blurb, has_resume,
    ]
    completeness = round(30 * sum(present) / len(present))
    used_hook = bool(c.hook and c.hook.get("anchor") and c.hook["anchor"].lower() in message.lower())
    hook = round(20 * ((c.hook or {}).get("specificity") or 0)) if used_hook else 0   # only a hook the email uses
    lo, hi = gates.MIN_WORDS, gates.MAX_WORDS
    dist = 0 if lo <= words <= hi else (lo - words if words < lo else words - hi)
    length = max(0, 15 - 3 * (dist // 10 + (1 if dist else 0)))
    asks = len(re.findall(r"\?", message))
    one_ask = 10 if asks == 1 else 6 if asks == 2 else 0
    long_s, avg = gates.readability(message)
    readability = max(0, (10 if avg <= 18 else 6 if avg <= 24 else 2) - 2 * long_s)
    subj = 5 if not gates.subject_problems(subject) else 0
    faith = 10 - 5 * ("faithfulness" in kinds) - 10 * ("uncovered_claim" in kinds) - 3 * ("ai_tell" in kinds) \
        - 2 * ("dashes" in kinds)
    faith = max(0, faith)
    total = completeness + hook + length + one_ask + readability + subj + faith
    verdict = "good" if total >= GOOD_FROM else "ok" if total >= REWRITE_BELOW else "rewrite"
    return {"score": total, "verdict": verdict, "parts": {
        "personalisation": {"score": completeness, "of": 30, "detail": f"{sum(present)} of {len(present)} slots in the email"},
        "hook": {"score": hook, "of": 20, "detail": ((c.hook or {}).get("type") or "none") + ("" if used_hook else " (not in the email)")},
        "length": {"score": length, "of": 15, "detail": f"{words} words (aim {lo}–{hi})"},
        "one_ask": {"score": one_ask, "of": 10, "detail": f"{asks} question mark(s)"},
        "readability": {"score": readability, "of": 10, "detail": f"avg {avg:.0f} words/sentence, {long_s} over 30"},
        "subject": {"score": subj, "of": 5, "detail": "; ".join(gates.subject_problems(subject)) or "passes"},
        "faithfulness": {"score": faith, "of": 10, "detail": ", ".join(k for k in kinds if k in (
            "faithfulness", "uncovered_claim", "ai_tell", "dashes")) or "no flags"},
    }}


def not_claimed(brief: dict | None, c: Contract) -> list[dict]:
    """What the email deliberately leaves out: the role's needs the resume does not cover
    (never claimed), and the hooks that were on record but not used."""
    out = []
    for n in ((brief or {}).get("stated") or []) + ((brief or {}).get("implied") or []):
        if n.get("status") == "uncovered":
            skills = ", ".join(n.get("missing_skills") or n.get("skills") or []) or None
            out.append({"kind": "uncovered need", "what": skills or _words(n.get("text") or "", 14),
                        "quote": _words(n.get("quote") or "", 24), "url": n.get("url")})
    for s in (brief or {}).get("forbidden") or []:
        if not any(s.lower() in (o["what"] or "").lower() for o in out):
            out.append({"kind": "skill not on the resume", "what": s, "quote": None, "url": None})
    seen: set[str] = set()
    out = [o for o in out if not (o["what"].lower() in seen or seen.add(o["what"].lower()))]
    for h in c.hook_alternatives:
        out.append({"kind": "hook not used", "what": h["sentence"], "quote": None, "url": h.get("source")})
    return out[:14]


__all__ = ["Contract", "contract", "hooks", "needs_and_proofs", "first_name", "recipient_role", "short_role",
           "choose_structure", "subject_options", "fill", "assemble", "keeps_contract", "quality", "not_claimed",
           "pick", "STRUCTURES", "CONTRACT_FIELDS", "HOOK_SPECIFICITY", "REWRITE_BELOW", "LEARN_MIN_SENT"]
