"""Compose: the personalisation contract, the four structures, subject lines, the
deterministic fill and the quality score of one first email to someone who could refer.

Where the rules come from: knowledge/cold-email-playbook.md, and the 2026-10-17 rewrite
(the user: the direct "would you refer me? here's a note you can forward" format reads as a
cold template and won't work; mails must be polite, HUMBLE and not cold). Referring a
stranger risks the referrer's own name, so the first email does not ask for the referral
outright and does not hide the goal either: one specific, checkable fact about THEIR work,
one real thing he did that relates, one genuine small question, then — openly, separately —
the role and "if you think I'd fit, I'd be grateful to know the best way to apply", and an
easy no. 60–110 words, plain text, exactly one question, at most one link in the body (the
role, or something he built with their product). No forwardable note, no resume link and no
bullet list in the first email: those are after-yes / follow-up assets (`forward_note`).

HUMILITY (the user's explicit rule): thank them for their time; say he is early in his
career; tentative words ("might be relevant", "I might be wrong", "if not, that's
completely fine"); never oversell, never grovel, never "Sir/Ma'am", no flattery adjectives.

THE CONTRACT. Every slot is filled from evidence or the draft is not written:

    first_name      a real name, not a handle ("kbysani", "愛してる", "Platform Company" fail)
    recipient_role  what they do there (their headline, or the class the evidence gives)
    company         the company
    target_role     the specific role being asked about (an engineering title on file)
    hook            ONE specific thing, with its source: their post / commit / repo / bio,
                    shared ground, else the company's launch / round / own description
    need + proof    ONE need the needs brief marks COVERED, and the resume line covering it
    resume_link     outreach.user_links "Resume: …" (kept for the after-yes note, never in
                    the first email)
    gift            optional: something he built or used with THEIR product / repo (a resume
                    line naming it, or outreach.compose.gifts) — turns on give_first

A missing slot is returned in `missing` and the caller files "incomplete" instead of a
generic email. Uncovered needs are never claimed (gates.check enforces it).

THE STRUCTURES, chosen by who they are and what the evidence holds:

    advice_first    engineers (the default): their work → his related work → one genuine
                    question → separately, the role link and "if you think I'd fit, I'd be
                    grateful to know the best way to apply" → easy out
    give_first      anyone but a recruiter, when he built / used something with their
                    product or repo: offer it humbly ("might be useful, might be naive") →
                    the role → a soft ask
    fit_question    engineering managers and founders: the role link + one proof + an honest
                    "does your team hire freshers into this role, or should I wait?"
    recruiter_direct  recruiters: a direct but humble ask (is it open to 2027 graduates,
                    and the best way to apply)

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

GIFTS: list[dict] = list(_C.get("gifts") or [])        # [{company, line, link?, offer?}] — written by the user
MAX_PER_COMPANY = int(_C.get("max_per_company", 2))     # people asked at one company in one batch
MAX_PER_ROLE = int(_C.get("max_per_role", 1))           # people asked about one role in one batch

# which structure writes the first email: "direct_intro" (the user's own pattern, 2026-10-17 — the
# default for every recipient) or "auto" (the research-based picker: advice / give / fit / recruiter)
STRUCTURE_MODE = str(_C.get("structure") or "direct_intro")
DIRECT_TEMPLATE = str(_C.get("direct_template") or "").strip()
DIRECT_SIGN_OFF = str(_C.get("direct_sign_off") or "Thank you for your time,")
DIRECT_OPT_OUT = bool(_C.get("direct_opt_out", True))
DIRECT_MAX_WORDS = int(_C.get("direct_max_words", 170))
DIRECT_MIN_WORDS = int(_C.get("direct_min_words", 80))
RECORD_MAX = 4            # track-record items in a direct_intro email (3–4)

STRUCTURES = {
    "direct_intro": {
        "label": "Who I am, my track record, a polite referral ask",
        "for": "everyone (the user's own pattern, the default)",
        "ask_kind": "referral",
        "why": "the user's choice: say who he is, show 3–4 real results that fit the role, ask politely to be "
               "considered for a referral, attach the resume link, and give an honest, easy out",
    },
    "advice_first": {
        "label": "Advice first, then the role",
        "for": "engineers (the default)",
        "ask_kind": "advice",
        "why": "a stranger risks their name by referring; a genuine question about their work, with the role "
               "mentioned openly, lets them offer instead of being asked",
    },
    "give_first": {
        "label": "Give first, then the role",
        "for": "anyone but a recruiter, when he built or used something with their product or repo",
        "ask_kind": "soft",
        "why": "he has something real to offer about their own product; offer it humbly, then mention the role",
    },
    "fit_question": {
        "label": "An honest fit question",
        "for": "engineering managers and founders",
        "ask_kind": "fit",
        "why": "they decide who is hired; the role, one proof and an honest \"do you hire freshers into this, "
               "or should I wait?\"",
    },
    "recruiter_direct": {
        "label": "Direct, humble ask to a recruiter",
        "for": "recruiters and talent partners",
        "ask_kind": "apply",
        "why": "routing candidates is their job: name the role, ask if it's open to 2027 graduates and how to apply",
    },
}
# drafts written before 2026-10-17 carry these names
LEGACY_STRUCTURES = {"proof_opinion": "advice_first", "referral_engineer": "advice_first",
                     "founder_direct": "fit_question"}

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
            return {"line": p["line"], "topic": p.get("topic"), "source_line": evidence, "curated": True}
    clauses = [c.strip() for c in re.split(r";", evidence or "") if c.strip()]
    if not clauses or len(evidence.split(",")) > 12:
        return None                        # a skills list is not proof of work
    best = next((c for c in clauses if re.search(r"\d", c)), clauses[0])
    words = best.split()
    if len(words) > 24:
        best = ",".join(" ".join(words[:24]).split(",")[:-1]) or " ".join(words[:24])
    if best[:1].isupper() and best[1:2].islower():
        best = best[0].lower() + best[1:]
    return {"line": best.rstrip("."), "topic": None, "source_line": evidence, "curated": False}


# resume lines that are true but are not the strongest thing to show an engineer
# (2026-10-17: a DevOps draft pitched "managed DNS configurations for 8 company websites")
_WEAK_PROOF = re.compile(r"\bDNS\b|mentor|technical interviews|annotat|training to|trainer|troubleshooting|"
                         r"student performance", re.I)


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
        # the strongest relevant proof first: covered, a curated phrasing of a real achievement,
        # not a weak line (DNS, mentoring), a number in it, then the need's weight
        scored.append(((n.get("status") != "covered", bool(_WEAK_PROOF.search(p["line"])), not p.get("curated"),
                        not re.search(r"\d", p["line"]), -(n.get("weight") or 0), n.get("kind") != "stated"), n, p))
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


# ------------------------------------------------------------------ the gift (give_first)

_CO_WORDS = re.compile(r"\b(ai|labs?|inc|llc|ltd|limited|pvt|private|technologies|technology|tech|systems|software|"
                       r"hq|corp|corporation|company|co|group|global)\b\.?", re.I)


def _brand(name: str | None) -> str:
    """'Temporal Technologies' -> 'Temporal'; 'Acme AI' -> 'Acme'."""
    return re.sub(r"\s+", " ", _CO_WORDS.sub(" ", name or "")).strip(" .,-")


def _names(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text or "", re.I))


def gifts(person: dict, company: dict, me: dict) -> list[dict]:
    """Something he built or used with THEIR product or repo, from evidence only:
    outreach.compose.gifts (the user's own words, with an optional link and offer), else a
    resume line that names the company's product (or the repo they commit to) and whose
    proof phrasing names it too. A place he worked is shared ground, not a gift."""
    out: list[dict] = []
    cname = company.get("name") or ""
    for g in GIFTS:
        if g.get("company") and g.get("line") and _brand(g["company"]).lower() == _brand(cname).lower():
            out.append({"type": "config", "thing": g.get("thing") or _brand(cname) or cname,
                        "proof": str(g["line"]).rstrip("."), "link": g.get("link"), "offer": g.get("offer"),
                        "source": "outreach.compose.gifts"})
    if out:
        return out
    mine = " | ".join(me.get("shared_places") or []).lower()
    names = []
    brand = _brand(cname)
    if len(brand) >= 4 and brand.lower() not in mine:
        names.append(brand)
    m = re.search(r"committed to [\w.\-]+/([\w.\-]+) on", person.get("affiliation_evidence") or "")
    if m and len(m.group(1)) >= 5 and m.group(1).lower() not in mine:
        names.append(m.group(1))
    for nm in names:
        for line in me.get("work_lines") or []:
            if not _names(line, nm):
                continue
            p = _proof_line(line)
            if p and p.get("curated") and _names(p["line"], nm):
                return [{"type": "used_their_product", "thing": nm, "proof": p["line"], "link": None, "offer": None,
                         "source": line}]
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
    gift: dict | None = None
    link: str | None = None              # the one link the body carries (the role, or the gift's)
    record: list[dict] = field(default_factory=list)   # direct_intro: 3–4 proofs, the role's first
    more_experience: str | None = None   # evidence the role asks for more experience than he has
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


def choose_structure(role_class: str | None, small: bool = False, has_gift: bool = False,
                     mode: str | None = None) -> tuple[str, str]:
    if (mode or STRUCTURE_MODE) == "direct_intro":
        return "direct_intro", ("the user's own pattern, for every recipient: who he is, his strongest results for "
                                "this role, a polite referral ask and the resume link")
    rc = role_class or "other"
    if rc in ("tech_recruiter", "recruiter"):
        return "recruiter_direct", "they are a recruiter: name the role, ask humbly if it's open to 2027 graduates"
    if has_gift:
        return "give_first", ("he built or used something with their own product or repo: offer it humbly, "
                              "then mention the role")
    if rc in ("eng_manager", "founder"):
        return "fit_question", ("they decide who is hired: the role, one proof, and an honest question about "
                                "whether the team hires freshers into it")
    return "advice_first", ("an engineer: a genuine question about their work, with the role mentioned openly, so "
                            "they can offer to help instead of being asked to vouch for a stranger")


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
    gs = gifts(person, company, me) if (c.role_class or "") not in ("recruiter", "tech_recruiter") else []
    c.gift = gs[0] if gs else None
    c.structure, c.structure_why = choose_structure(c.role_class, c.small, bool(c.gift))
    if c.structure == "direct_intro":
        c.link = c.resume_link
        c.record = track_record(c.proofs, me)
        c.more_experience = more_experience(job, brief)
    else:
        c.link = (c.gift or {}).get("link") if c.structure == "give_first" and (c.gift or {}).get("link") \
            else c.target_role_url
    for f in CONTRACT_FIELDS:
        if f == "hook" and c.structure == "direct_intro":
            continue                      # the user's pattern opens with who he is, not a hook
        val = {"need": c.need, "proof": c.proofs[0]["proof"] if c.proofs else None}.get(f, getattr(c, f, None))
        if not val:
            c.missing.append(f)
    return c


_YEARS = re.compile(r"\b(?:[2-9]|1\d)\s*\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs?)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|ii|iii|iv)\b", re.I)


def more_experience(job: dict | None, brief: dict | None) -> str | None:
    """The evidence that the role asks for more experience than a final-year student has:
    the role marked senior, a senior title, or the posting asking for 2+ years. None otherwise."""
    j = job or {}
    if j.get("is_senior"):
        return "the role is marked senior"
    if _SENIOR.search(j.get("title") or ""):
        return f"the title says \"{_SENIOR.search(j['title']).group(0)}\""
    for n in (brief or {}).get("stated") or []:
        m = _YEARS.search(n.get("quote") or "") or _YEARS.search(n.get("text") or "")
        if m:
            return f"the posting asks for {m.group(0)}"
    return None


def resume_proofs(me: dict) -> list[dict]:
    """Every curated proof (outreach.compose.proofs) whose resume line is really on the
    resume (me.work_lines), in config order — the top-up for a direct_intro track record."""
    out = []
    for line in me.get("work_lines") or []:
        p = _proof_line(line)
        if p and p.get("curated"):
            out.append({"proof": p["line"], "topic": p["topic"], "resume_line": line, "need": None})
    order = {p["line"]: i for i, p in enumerate(PROOFS)}
    return sorted(out, key=lambda x: order.get(x["proof"], 999))


def track_record(proofs: list[dict], me: dict, limit: int = RECORD_MAX) -> list[dict]:
    """3–4 concrete results: the ones covering the role's needs first (needs brief, best
    first), then the strongest other lines of the resume. No two that share a number, no
    weak line (DNS, mentoring) while a stronger one is left."""
    out, nums_seen, seen = [], set(), set()
    for p in [*(proofs or []), *resume_proofs(me)]:
        line = p.get("proof") or ""
        nums = frozenset(re.findall(r"\d[\d,]*%?", line))
        if not line or line in seen or (nums and nums & nums_seen) or _WEAK_PROOF.search(line):
            continue
        seen.add(line)
        nums_seen |= nums
        out.append(p)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ subjects

SUBJECT_MAX = 5          # 2–5 words, about THEM or their work; never "Referral request for …"
SUBJECT_PATTERNS = {
    "direct_intro": ["referral_for"],
    "advice_first": ["hook", "role_at"],
    "give_first": ["gift", "hook", "role_at"],
    "fit_question": ["fresher_q", "hook", "role_at"],
    "recruiter_direct": ["grad_role", "role_at"],
}


def hook_subject(c: Contract) -> str | None:
    """2–5 words about the hook: "your voice-sdk commits", "Acme AI's recent launch"."""
    h = c.hook or {}
    a = h.get("anchor") or ""
    short = a.split("/")[-1]
    return {"person_commit": f"your {short} commits", "person_repo": f"your {short} repo",
            "person_post": f"your {a}", "person_bio": f"your work on {a}", "shared_ground": f"fellow {a}",
            "company_launch": f"{c.company}'s recent launch",
            "company_funding": f"{c.company}'s {a}"}.get(h.get("type") or "")


def subject_options(c: Contract, me: dict) -> list[dict]:
    """Every subject the structure's patterns give that passes the gate's subject rules
    and is 2–5 words."""
    role, co = c.target_role or "", c.company or ""
    year = me.get("year") or "2027"
    thing = (c.gift or {}).get("thing")
    make = {
        "hook": hook_subject(c),
        "gift": f"A note on using {thing}" if thing else None,
        "fresher_q": f"Freshers for {role}?" if len(role.split()) <= 3 else f"Freshers at {co}?",
        "grad_role": f"{role}, {year} graduate" if len(role.split()) <= 3 else f"{year} graduate, {co}",
        "role_at": f"{role} at {co}" if len(f"{role} at {co}".split()) <= SUBJECT_MAX else f"{role} opening",
        "referral_for": f"Referral for {role} at {co}" if not gates.subject_problems(f"Referral for {role} at {co}")
        else f"Referral for {role}",
    }
    most = gates.SUBJECT_MAX_WORDS if c.structure == "direct_intro" else SUBJECT_MAX
    out = []
    for pat in SUBJECT_PATTERNS.get(c.structure or "", []):
        s = make.get(pat)
        if s and role and co and len(s.split()) <= most and not gates.subject_problems(s):
            out.append({"pattern": pat, "subject": s})
    if not out and role and co:
        out.append({"pattern": "role_plain", "subject": " ".join(f"{role} opening".split()[:SUBJECT_MAX])[:60]})
    return out


def pick(options: list, key: Any) -> int:
    """A stable rotation over the options (so variants spread evenly and repeat on a rerun)."""
    if not options:
        return -1
    return int(hashlib.sha1(str(key).encode()).hexdigest(), 16) % len(options)


# ------------------------------------------------------------------ the deterministic fill

THANKS = "Thank you for your time."
EASY_OUT = "If not, that's completely fine."
DEFAULT_OFFER = "If it's ever useful, I'm happy to share what I ran into. It might be naive next to what your team knows."


def ask_line(c: Contract) -> str:
    """The one question of the email, per structure."""
    h = c.hook or {}
    if c.structure == "direct_intro":
        return (f"I saw the {c.target_role} opening at {c.company}, and I'd be really grateful if you would "
                f"consider referring me.")
    if c.structure == "give_first":
        return "If you think I might fit, could you point me to the best way to apply?"
    if c.structure == "fit_question":
        return "Does your team hire freshers into this role, or should I wait a year or two?"
    if c.structure == "recruiter_direct":
        return "Could you tell me whether it's open to 2027 graduates, and if so, the best way to apply?"
    if h.get("type") in ("person_commit", "person_repo") and h.get("anchor"):
        return f"In my place, what would you learn next to be useful on a codebase like {h['anchor']}?"
    return "In my place, what would you learn next to be useful on a team like yours?"


def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def _opening(c: Contract) -> str:
    """"the Backend Engineer opening at Acme AI (https://…)" — the link only when it is the body's one link."""
    link = f" ({c.link})" if c.link and c.link == c.target_role_url else ""
    return f"the {c.target_role} opening at {c.company}{link}"


def forward_note(c: Contract, me: dict) -> str:
    """The two lines a referrer can forward — an AFTER-YES asset (sent once they offer to
    help, or in a follow-up), never in the first email."""
    p = _cap(c.proofs[0]["proof"]) if c.proofs else ""
    role = f"the {c.target_role} role at {c.company}" + (f" ({c.target_role_url})" if c.target_role_url else "")
    return (f"\"{me.get('full_name')}, final-year B.Tech CS+AI at {me.get('school')} ({me.get('year')}). {p}. "
            f"Interested in {role}. Resume: {c.resume_link}\"")


_NUMBER_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
OUT_MORE_EXPERIENCE = ("I understand the role asks for more experience than I have, so if it isn't a fit, "
                       "I completely understand.")
OUT_PLAIN = "If it isn't something you can help with, I completely understand."


def out_line(c: Contract) -> str:
    """The honest humility line: "more experience than I have" only when evidence says so."""
    return OUT_MORE_EXPERIENCE if c.more_experience else OUT_PLAIN


def record_paragraph(c: Contract, me: dict) -> str:
    """"I've done four internships so far. I fine-tuned … I also built …" — every item a
    resume line's curated phrasing, the role's needs first."""
    n = me.get("internships") or 0
    head = f"I've done {_NUMBER_WORD.get(n, n)} internships so far. " if n >= 2 else ""
    lead = ["I", "I also", "I", "And I"]
    items = [f"{lead[i]} {p['proof'].rstrip('.')}." for i, p in enumerate(c.record or c.proofs[:1])]
    return head + " ".join(items)


def _direct(c: Contract, me: dict) -> str:
    if DIRECT_TEMPLATE:                      # the user's own email, word for word (config)
        return DIRECT_TEMPLATE.format(first_name=c.first_name, role=c.target_role, company=c.company,
                                      resume_link=c.resume_link or "").strip()
    school = me.get("school")
    paras = [f"My name is {me.get('first_name')}, and I'm a final-year B.Tech CS+AI student at {school}.",
             record_paragraph(c, me),
             f"{ask_line(c)} {out_line(c)}"]
    if c.resume_link:
        paras.append(f"Resume: {c.resume_link}")
    return f"Hi {c.first_name},\n\n" + "\n\n".join(paras)


def fill(c: Contract, me: dict) -> dict:
    """The message (greeting → thanks) written from the slots alone. Returns {message,
    forward_note}: the drafter adds the opt-out line and the signature. One question,
    one link at most, no bullet list, no resume, no forwardable note."""
    if c.structure == "direct_intro":
        return {"message": _direct(c, me), "forward_note": forward_note(c, me)}
    first = c.first_name
    intro = f"I'm {me.get('first_name')}, a final-year CS+AI student at {me.get('school')}"
    need = c.need or {}
    asks_for = (f"The posting asks for {need['what']}." if need.get("kind") == "stated"
                else f"The posting suggests you need {need.get('what')}.")
    p1 = c.proofs[0]["proof"]
    relevant = f"One thing I've done that might be relevant: I {p1}."
    if c.structure == "give_first":
        g = c.gift or {}
        gift_link = f" ({g['link']})" if g.get("link") and c.link == g.get("link") else ""
        paras = [c.hook["sentence"],
                 f"{intro}, still early in my career. I've used {g.get('thing')} myself: I {g.get('proof')}"
                 f"{gift_link}.",
                 g.get("offer") or DEFAULT_OFFER,
                 f"I'm also interested in {_opening(c)}. {ask_line(c)} {EASY_OUT} {THANKS}"]
    elif c.structure == "fit_question":
        paras = [c.hook["sentence"],
                 f"{intro}. I'm interested in {_opening(c)}. {asks_for} {relevant}",
                 f"I'm still early in my career, so I might be wrong about the fit. {ask_line(c)}",
                 f"Either answer would help. {THANKS}"]
    elif c.structure == "recruiter_direct":
        paras = [c.hook["sentence"],
                 f"{intro}, graduating in {me.get('year') or 2027}. I'd like to apply for {_opening(c)}. "
                 f"{asks_for} {relevant}",
                 f"I'm early in my career, so I may not be what you need. {ask_line(c)}",
                 THANKS]
    else:                                   # advice_first
        paras = [c.hook["sentence"],
                 f"{intro}, still early in my career. {relevant}",
                 ask_line(c),
                 f"I'm also writing because of {_opening(c)}. If you think I'd fit, "
                 f"I'd be grateful to know the best way to apply. {EASY_OUT}",
                 THANKS]
    return {"message": f"Hi {first},\n\n" + "\n\n".join(paras), "forward_note": forward_note(c, me)}


SIGN_OFF = "Best,"
SIGN_OFF_BY = {"direct_intro": DIRECT_SIGN_OFF}


def assemble(message: str, signature: str, opt_out: str | None = None, structure: str | None = None) -> str:
    """The full body exactly as it would be sent: the message, the opt-out line, the
    sign-off ("Thank you for your time," in the user's pattern) and the signature."""
    parts = [message.strip()]
    if opt_out:
        parts.append(opt_out)
    return "\n\n".join(parts) + f"\n\n{SIGN_OFF_BY.get(structure or '', SIGN_OFF)}\n{signature}"


def word_band(c: Contract | None) -> tuple[int, int]:
    """The message's word band: the user's pattern carries a track record (80–170), the
    research structures are short (gate config, 60–110)."""
    if c is not None and c.structure == "direct_intro":
        return DIRECT_MIN_WORDS, DIRECT_MAX_WORDS
    return gates.MIN_WORDS, gates.MAX_WORDS


def message_of(body: str, opt_lines: list[str] | None = None) -> str:
    """The message part of a whole body: everything before the opt-out line or the sign-off."""
    cut = len(body or "")
    for marker in [*(x for x in (opt_lines or []) if x), f"\n\n{DIRECT_SIGN_OFF}\n", "\n\nThank you for your time,\n", "\n\nBest,\n", "\n\nThanks,\n", "\n\nRegards,\n",
                   "\n\nCheers,\n"]:
        i = (body or "").find(marker)
        if i != -1:
            cut = min(cut, i)
    return (body or "")[:cut].strip()


# ------------------------------------------------------------------ checks on any version

# the old cold-template shape: a forwardable note, a resume link or a bullet list in the
# first email, an outright referral ask, a call ask, the stock openers
_FORWARD = re.compile(r"note (?:you|they) (?:can|could) forward|forwardable|short note you", re.I)
_RESUME_IN_BODY = re.compile(r"resume\s*:|drive\.google|docs\.google|\.pdf\b", re.I)
_BULLETS = re.compile(r"^\s*[-•*]\s+\S", re.M)
_REFER_ME = re.compile(r"\brefer(?:ring)? me\b|\bbe open to referring\b|\breferral for\b", re.I)
_CALL_ASK = re.compile(r"\b\d+[- ]?min(?:ute)?s?\b.{0,12}\b(?:call|chat)\b", re.I)
_STOCK = re.compile(r"i hope this finds you|i'?m reaching out|i am reaching out|quick question|i came across your profile",
                    re.I)
_LINK = re.compile(r"https?://\S+|\b[\w-]+\.(?:com|ai|io|dev|app|co|in|org|net)/\S*", re.I)
GROVEL = re.compile(r"\bsir\b|\bma'?am\b|\bmadam\b|\brespected\b|\bhumbly\b|\bkindly\b|\bbeg\b|\bdesperate|"
                    r"sorry to bother|please help me|do the needful", re.I)
HUMILITY = {
    "thanks": re.compile(r"\bthank you\b|\bthanks for\b", re.I),
    "early": re.compile(r"early in my career|still learning|final-year", re.I),
    "tentative": re.compile(r"might be (?:relevant|wrong|naive|useful)|may (?:not )?be (?:naive|what)|completely fine|"
                            r"no worries|if not\b|i might be wrong|either answer|completely understand|"
                            r"isn't a fit|really grateful", re.I),
}


def links(message: str) -> list[str]:
    return _LINK.findall(message or "")


def old_shape(message: str, c: Contract | None = None) -> list[str]:
    """What the message carries that the first email must not (the pre-2026-10-17 template)."""
    out = []
    if _FORWARD.search(message):
        out.append("a forwardable note (send it after they say yes)")
    direct = bool(c and c.structure == "direct_intro")     # the user's pattern: resume + referral ask by design
    if _RESUME_IN_BODY.search(message) and not direct:
        out.append("a resume link (offer it after they reply)")
    if _BULLETS.search(message):
        out.append("a bullet list of resume lines")
    if _REFER_ME.search(message) and not (c and c.structure in ("recruiter_direct", "direct_intro")):
        out.append("an outright referral ask in the first email")
    if _CALL_ASK.search(message):
        out.append("a call ask")
    if _STOCK.search(message):
        out.append(f"a stock phrase (\"{_STOCK.search(message).group(0)}\")")
    if len(links(message)) > 1:
        out.append(f"{len(links(message))} links (one at most)")
    return out


def keeps_contract(message: str, c: Contract) -> list[str]:
    """What a (model) rewrite dropped that the contract requires to be in the message, or
    the old template shape it brought back."""
    low = (message or "").lower()
    out = []
    if c.first_name and c.first_name.lower() not in low[:80]:
        out.append("first name in the greeting")
    if c.company and c.company.lower() not in low:
        out.append("company name")
    if c.target_role and c.target_role.lower() not in low:
        out.append("role")
    direct = c.structure == "direct_intro"
    if not direct and c.hook and c.hook.get("anchor") and c.hook["anchor"].lower() not in low:
        out.append("hook")
    shown = [p["proof"] for p in (c.record or c.proofs[:1])] if direct else [
        (c.gift or {}).get("proof") if c.structure == "give_first" and c.gift else (c.proofs[0]["proof"] if c.proofs else "")]
    for proof in shown:
        nums = re.findall(r"\d[\d,]*%?", proof or "")
        if nums and not any(n in message for n in nums):
            out.append(f"proof number ({nums[0]})")
    if c.link and c.link not in message:
        out.append("the resume link" if direct else "the link")
    q = message.count("?")
    if direct:
        if not re.search(r"consider referring me|refer(?:ring)? me", message, re.I):
            out.append("the ask")
        if q > 1:
            out.append("exactly one question")
    elif q == 0:
        out.append("the ask")
    elif q > 1:
        out.append("exactly one question")
    out += old_shape(message, c)
    return out


def humility(message: str) -> dict:
    """Which humility markers the message carries, and any grovelling."""
    found = [k for k, rx in HUMILITY.items() if rx.search(message or "")]
    grovel = [m.group(0) for m in GROVEL.finditer(message or "")]
    return {"markers": found, "grovel": grovel}


def quality(message: str, subject: str, c: Contract, flags: list[dict], words: int, **_legacy) -> dict:
    """0–100 and its parts. Personalisation 20, hook specificity 15 (the user's direct_intro
    pattern has no hook: its 15 are the track record — 3–4 numbered results that fit the
    role), length 10 (60–110; direct_intro 80–170), one ask 10, humility 15, first-email
    shape 10, readability 5, subject 5, faithfulness 10. Under REWRITE_BELOW: "rewrite"."""
    kinds = [f["kind"] for f in flags]
    direct = c.structure == "direct_intro"
    low = (message or "").lower()
    proof = (c.gift or {}).get("proof") if c.structure == "give_first" and c.gift else (
        c.proofs[0]["proof"] if c.proofs else "")
    nums = re.findall(r"\d[\d,]*%?", proof or "")
    present = [
        bool(c.first_name and c.first_name in message[:80]),
        bool(c.company and c.company.lower() in low),
        bool(c.target_role and c.target_role.lower() in low),
        bool(c.resume_link and c.resume_link in message) if direct else
        bool(c.hook and c.hook.get("anchor", "").lower() in low),
        bool(proof and ((nums and any(n in message for n in nums)) or proof[:20].lower() in low)),
    ]
    completeness = round(20 * sum(present) / len(present))
    used_hook = bool(c.hook and c.hook.get("anchor") and c.hook["anchor"].lower() in low)
    hook = round(15 * ((c.hook or {}).get("specificity") or 0)) if used_hook else 0   # only a hook the email uses
    shown = 0
    if direct:
        shown = sum(1 for p in (c.record or []) if (re.findall(r"\d[\d,]*%?", p["proof"]) and any(
            n in message for n in re.findall(r"\d[\d,]*%?", p["proof"]))))
        hook = {0: 0, 1: 5, 2: 10}.get(shown, 15)
    lo, hi = word_band(c)
    dist = 0 if lo <= words <= hi else (lo - words if words < lo else words - hi)
    length = max(0, 10 - 2 * (dist // 10 + (1 if dist else 0)))
    asks = message.count("?")
    one_ask = 10 if asks == 1 else 4 if asks == 2 else 0
    if direct:
        asked = bool(re.search(r"consider referring me|refer(?:ring)? me", message, re.I))
        one_ask = 10 if asked and asks <= 1 else 4 if asked else 0
    hum = humility(message)
    if direct and "thanks" not in hum["markers"]:
        hum["markers"].append("thanks")          # its sign-off is "Thank you for your time," (assemble)
    humble = 0 if hum["grovel"] else {0: 0, 1: 6, 2: 11}.get(len(hum["markers"]), 15)
    shape_bad = old_shape(message, c)
    shape = max(0, 10 - 4 * len(shape_bad))
    long_s, avg = gates.readability(message)
    readability = max(0, (5 if avg <= 16 else 3 if avg <= 22 else 1) - 2 * long_s)
    sp = gates.subject_problems(subject)
    if len((subject or "").split()) > SUBJECT_MAX and not direct:
        sp = [*sp, f"over {SUBJECT_MAX} words"]
    if re.match(r"\s*referral\b", subject or "", re.I) and not direct:
        sp = [*sp, "a \"Referral …\" subject reads as a template"]
    subj = 5 if not sp else 0
    faith = 10 - 5 * ("faithfulness" in kinds) - 10 * ("uncovered_claim" in kinds) - 3 * ("ai_tell" in kinds) \
        - 2 * ("dashes" in kinds)
    faith = max(0, faith)
    total = completeness + hook + length + one_ask + humble + shape + readability + subj + faith
    verdict = "good" if total >= GOOD_FROM else "ok" if total >= REWRITE_BELOW else "rewrite"
    return {"score": total, "verdict": verdict, "parts": {
        "personalisation": {"score": completeness, "of": 20, "detail": f"{sum(present)} of {len(present)} slots in the email"},
        "hook": {"score": hook, "of": 15, "detail": (f"track record: {shown} numbered result(s) in the email" if direct else
                                                    ((c.hook or {}).get("type") or "none") + ("" if used_hook else " (not in the email)"))},
        "length": {"score": length, "of": 10, "detail": f"{words} words (aim {lo}–{hi})"},
        "one_ask": {"score": one_ask, "of": 10, "detail": ("one polite referral ask" if direct and one_ask == 10
                                                          else f"{asks} question mark(s)")},
        "humility": {"score": humble, "of": 15, "detail": (f"grovels: {', '.join(hum['grovel'][:3])}" if hum["grovel"]
                                                            else ", ".join(hum["markers"]) or "no thanks, no 'early in my career', nothing tentative")},
        "shape": {"score": shape, "of": 10, "detail": "; ".join(shape_bad) or "no note, no resume, no bullets, one link"},
        "readability": {"score": readability, "of": 5, "detail": f"avg {avg:.0f} words/sentence, {long_s} over 30"},
        "subject": {"score": subj, "of": 5, "detail": "; ".join(sp) or "passes"},
        "faithfulness": {"score": faith, "of": 10, "detail": ", ".join(k for k in kinds if k in (
            "faithfulness", "uncovered_claim", "ai_tell", "dashes")) or "no flags"},
    }}


def volume(targets: list[dict], *, per_company: int | None = None, per_role: int | None = None) -> list[dict]:
    """Targets ({company_id, job_id, ...}, best first) kept within one batch's limits: at
    most MAX_PER_COMPANY people at one company and MAX_PER_ROLE about one role."""
    pc = MAX_PER_COMPANY if per_company is None else per_company
    pr = MAX_PER_ROLE if per_role is None else per_role
    by_co: dict = {}
    by_role: dict = {}
    out = []
    for t in targets:
        co, job = t.get("company_id"), t.get("job_id")
        if by_co.get(co, 0) >= pc or (job is not None and by_role.get(job, 0) >= pr):
            continue
        by_co[co] = by_co.get(co, 0) + 1
        if job is not None:
            by_role[job] = by_role.get(job, 0) + 1
        out.append(t)
    return out


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


__all__ = ["Contract", "contract", "hooks", "gifts", "needs_and_proofs", "first_name", "recipient_role", "short_role",
           "choose_structure", "subject_options", "hook_subject", "fill", "forward_note", "assemble", "message_of",
           "keeps_contract", "old_shape", "humility", "quality", "not_claimed", "volume", "pick", "STRUCTURES",
           "LEGACY_STRUCTURES", "CONTRACT_FIELDS", "HOOK_SPECIFICITY", "REWRITE_BELOW", "LEARN_MIN_SENT",
           "MAX_PER_COMPANY", "MAX_PER_ROLE"]
