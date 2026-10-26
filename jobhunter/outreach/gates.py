"""The review gate — what a reviewer must see before "yes, send that".

MOTIV §6: nothing is invented. A generic honest email is recoverable; a flattering
fabricated one is not. So every draft is checked against the facts it was allowed to
use, and what the check finds is stored on the draft as `review_flags` and shown in
the queue. The gate never rewrites a sentence — it flags, the human decides.

    faithfulness  every proper noun and number in the body appears in the corpus:
                  the candidate's profile, the person's own evidence, the company's own
                  pages, the names involved. Anything else is "unsupported".
    ai_tell       vocabulary that reads as machine-written
    length        outside the configured word band
    one_ask       more than one question / request
    dashes        an em/en dash survived
    guessed       the address is pattern-guessed, not verified (bounces cost the account)
    affiliation   nothing on record says the person still works there (guardrails.affiliation)
    subject       the subject line breaks the researched rules (2–8 words, ≤60 chars, no
                  "quick question", no fake "Re:", no spam words, no "!")
    readability   a sentence over 30 words, or an average over 22
    blurb         the forwardable blurb is longer than blurb_max_words
    uncovered_claim  the body names a skill the needs brief marked UNCOVERED (the role asks
                  for it, the resume does not have it) — the draft may not claim it
    deliverability  what a spam filter sees (deliverability()): an attachment, more than 2
                  links, a shortener, a nameless greeting, a near-copy (shingle Jaccard
                  >= 0.8) of mail already approved/sent, the same subject or many asks into one domain

Pure functions; no I/O, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobhunter import CONFIG

_G = ((CONFIG.get("outreach") or {}).get("gate") or {})
MIN_WORDS = int(_G.get("min_words", 60))
MAX_WORDS = int(_G.get("max_words", 120))
BLURB_MAX_WORDS = int(_G.get("blurb_max_words", 55))
SUBJECT_MIN_WORDS = int(_G.get("subject_min_words", 2))
SUBJECT_MAX_WORDS = int(_G.get("subject_max_words", 8))
SUBJECT_MAX_CHARS = int(_G.get("subject_max_chars", 60))

# Words that read as machine-written to anyone who receives a lot of mail.
AI_TELLS = [
    "i hope this finds you well", "i hope this email finds you", "delve", "leverage", "leveraging",
    "passionate about", "incredible", "amazing work", "huge fan", "journey", "resonate", "synergy",
    "thrilled", "excited to", "i'm reaching out", "i am reaching out", "cutting-edge", "seamless",
    "robust", "game-changing", "tapestry", "in today's", "landscape", "unlock", "empower",
    "i would love the opportunity", "as an ai", "i believe i would be a great fit", "esteemed",
    "innovative solutions", "dynamic", "reach out to you", "touch base",
    "i hope you're doing well", "i hope you are doing well", "i wanted to reach out", "hope this email finds",
]

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]+")
_PROPER = re.compile(r"\b([A-Z][a-zA-Z0-9'’\-]{2,}(?:\s+[A-Z][a-zA-Z0-9'’\-]{2,}){0,2})\b")
_NUMBER = re.compile(r"(?<![A-Za-z])(\d[\d,]*(?:\.\d+)?\s*(?:%|k|K|M|x|X|\+|LPA|lakh|crore|years?|yrs?|months?)?)")
_ASK = re.compile(r"\?|\b(would you|could you|can you|are you open|any chance|if you(?:'d| would) be)\b", re.I)
_DASH = re.compile(r"[—–]")
# capitalised words that are ordinary English at a sentence start or in a greeting
_COMMON = {
    "hi", "hello", "hey", "dear", "thanks", "thank", "best", "regards", "cheers", "the", "this", "that",
    "these", "those", "i", "i'm", "i've", "i'd", "we", "you", "your", "yours", "it", "its", "if", "in",
    "on", "at", "for", "from", "with", "and", "but", "or", "so", "as", "a", "an", "my", "our", "one",
    "two", "three", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "would", "could", "should", "happy", "no", "yes", "not", "also", "let",
    "please", "since", "when", "while", "after", "before", "over", "under", "here", "there", "what",
    "which", "who", "how", "why", "any", "some", "all", "every", "most", "just", "even", "still",
    "python", "typescript", "javascript", "sql", "aws", "react", "docker", "kubernetes", "postgres",
    "github", "gmail", "google", "meet", "zoom", "calendly", "linkedin", "india", "us", "uk",
    "engineer", "engineering", "software", "ai", "ml", "llm", "rag", "api", "apis", "backend",
    "your", "my", "some", "congrats", "interested", "resume", "posting", "role",
}


@dataclass
class GateResult:
    flags: list[dict] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    words: int = 0

    @property
    def blocked(self) -> bool:
        """Blocked means "read this before approving", not "cannot send"."""
        return any(f["kind"] in ("faithfulness", "affiliation", "uncovered_claim") for f in self.flags)

    def as_list(self) -> list[dict]:
        return self.flags


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower())


def _body_only(body: str, signature: str | None) -> str:
    if signature and signature in body:
        return body[: body.index(signature)]
    return body


def check(body: str, *, corpus: str, signature: str | None = None,
          address_confidence: str | None = None, affiliation: str | None = None,
          affiliation_evidence: str | None = None, forbidden: list[str] | None = None,
          exclude: list[str] | None = None, subject: str | None = None,
          word_band: tuple[int, int] | None = None, roles: list[str] | None = None) -> GateResult:
    """Run every gate over `body` against the facts in `corpus`.

    `forbidden` are skills the needs brief marked uncovered (needs.forbidden_terms): the
    body naming one is a claim the resume cannot back, whatever the corpus contains.
    `word_band` overrides the configured length band (the user's direct_intro pattern
    carries a track record and runs longer). `roles` are the titles of the role asked about:
    naming the role ("the Full stack AI Engineer opening") is not claiming its skills
    (2026-10-26, GoComet: every draft was rejected for "Full-stack")."""
    r = GateResult()
    text = _body_only(body or "", signature)
    norm_corpus = " " + _normalise(corpus) + " "

    # ---- uncovered needs: the role asks for it, the resume does not have it
    if forbidden:
        from jobhunter.skills import pattern

        claims = text
        for title in sorted({t.strip() for t in roles or [] if t and t.strip()}, key=len, reverse=True):
            claims = re.sub(re.escape(title), " ", claims, flags=re.I)
        for skill in forbidden:
            if re.search(pattern(skill), claims, re.I):
                r.forbidden_hits.append(skill)
        if r.forbidden_hits:
            r.flags.append({"kind": "uncovered_claim",
                            "detail": "names what the resume does not cover (needs brief): " + ", ".join(r.forbidden_hits[:6])})

    # ---- faithfulness: proper nouns and numbers must exist in the corpus
    seen: set[str] = set()
    for m in _PROPER.finditer(text):
        # "Tailscale's" is Tailscale; "LLM-backed" is LLM; "I'm" is not a name
        phrase = re.sub(r"[’']s\b", "", m.group(1)).strip()
        phrase = " ".join(w.split("-")[0] if w.lower().startswith(("i'm", "i’m", "i've", "i’ve", "i'd", "i’d")) is False else w for w in phrase.split())
        phrase = re.sub(r"^(?:I'm|I’m|I've|I’ve|I'd|I’d|I'll|I’ll)\s+", "", phrase).strip()
        if not phrase:
            continue
        # "Your Backend Engineer posting": the leading ordinary word is not part of the name
        lead = phrase.split()
        while len(lead) > 1 and lead[0].lower() in _COMMON:
            lead = lead[1:]
        phrase = " ".join(lead)
        words = phrase.split()
        # a capitalised word at the start of a sentence is not a proper noun claim
        start = m.start()
        at_sentence_start = start == 0 or text[max(0, start - 2):start].strip() in ("", ".", "!", "?", ",", "-", "•", ":", "(", "\"")
        if len(words) == 1 and (at_sentence_start or words[0].lower() in _COMMON):
            continue
        key = _normalise(phrase).strip()
        if not key or key in seen or all(w in _COMMON for w in key.split()):
            continue
        seen.add(key)
        # a hyphenated compound counts when its head is known ("LLM-backed" ← "LLM")
        heads = [w.split("-")[0] for w in key.split()]
        if (f" {key} " not in norm_corpus and not all(f" {w} " in norm_corpus for w in key.split())
                and not all(f" {h} " in norm_corpus for h in heads)):
            r.unsupported.append(phrase)
    for m in _NUMBER.finditer(text):
        num = m.group(1).strip()
        key = _normalise(num).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        # the ask itself ("10 minutes", "15 min") is not a claim about anyone
        if re.search(r"\b(min|mins|minutes?)\b", text[m.end(): m.end() + 12], re.I):
            continue
        digits = re.sub(r"[^\d.]", "", num)
        # "27,000+" is 27000 in a corpus that may write it "27,000" or "27000"
        if digits and digits not in re.sub(r"[^\d.\s]", " ", re.sub(r"(?<=\d),(?=\d{3})", "", corpus)):
            r.unsupported.append(num)
    if r.unsupported:
        r.flags.append({"kind": "faithfulness",
                        "detail": "not in the facts this draft was given: " + ", ".join(r.unsupported[:8])})

    # ---- AI-tell vocabulary
    low = text.lower()
    tells = [t for t in AI_TELLS if t in low]
    if tells:
        r.flags.append({"kind": "ai_tell", "detail": "reads machine-written: " + ", ".join(tells[:5])})

    # ---- length: the message itself — the forwardable blurb and the opt-out line are
    # counted apart (`exclude`), they are skimmed, not read
    counted = text
    for seg in exclude or []:
        if seg:
            counted = counted.replace(seg, " ")
    r.words = len(_WORD.findall(counted))
    lo, hi = word_band or (MIN_WORDS, MAX_WORDS)
    if r.words < lo:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — under {lo}, may read as thin"})
    elif r.words > hi:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — over {hi}, a busy engineer stops reading"})

    # ---- one ask
    asks = len(_ASK.findall(text))
    if asks > 2:
        r.flags.append({"kind": "one_ask", "detail": f"{asks} questions/requests — make it one"})

    # ---- readability: short sentences (Boomerang: 3rd-grade reading level +36% replies)
    long_s, avg = readability(counted)
    if long_s or avg > 22:
        r.flags.append({"kind": "readability",
                        "detail": (f"{long_s} sentence(s) over 30 words" if long_s else "") +
                                  (f"{'; ' if long_s else ''}average {avg:.0f} words a sentence" if avg > 22 else "")})

    # ---- subject line
    if subject is not None:
        for problem in subject_problems(subject):
            r.flags.append({"kind": "subject", "detail": problem})

    # ---- the blurb has its own limit
    for seg in exclude or []:
        if seg and seg.lstrip().startswith('"') and len(_WORD.findall(seg)) > BLURB_MAX_WORDS:
            r.flags.append({"kind": "blurb", "detail": f"forwardable blurb is {len(_WORD.findall(seg))} words — over {BLURB_MAX_WORDS}"})

    # ---- dashes
    if _DASH.search(text):
        r.flags.append({"kind": "dashes", "detail": "an em/en dash survived; replace with a comma or a full stop"})

    # ---- the address itself
    if address_confidence and address_confidence != "verified":
        r.flags.append({"kind": "guessed", "detail": f"address is {address_confidence}: it may bounce, and bounces count against the account"})

    # ---- the person: is there evidence they still work there?
    if affiliation is not None and affiliation not in ("verified-current", "likely-current"):
        r.flags.append({"kind": "affiliation",
                        "detail": f"{affiliation}: {affiliation_evidence or 'no evidence they work there now'}"})
    elif affiliation == "likely-current":
        r.flags.append({"kind": "affiliation_likely", "detail": f"likely current: {affiliation_evidence or ''}".strip()})

    return r


# ------------------------------------------------------------------ deliverability
# 2026-10-23 (Sarvam): Gmail refused 9 of 24 asks into sarvam.ai as spam (5.7.1). What a spam
# filter sees before a person does. FLAGS only, like every gate: the human decides.

# 2026-10-26: the circle writes to a company for weeks, three a day. A company's server groups like
# mail arriving together, so the same-domain checks read mail still to go there (it goes the same
# day as this one) and mail sent there in the last SAME_DOMAIN_HOURS — not yesterday's.
SAME_DOMAIN_HOURS = 3
SIMILAR_AT = 0.8          # token-shingle Jaccard over the message at or above this = a near-duplicate
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly",
              "lnkd.in", "shorturl.at", "tiny.cc", "rb.gy", "t.ly", "s.id", "bl.ink", "short.io", "linktr.ee"}
_MDLINK = re.compile(r"\[([^\]\n]{1,80})\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"(?:https?://|www\.)[^\s)<>\]]+", re.I)
_BARE_GREETING = re.compile(r"^\s*(hi|hello|hey|dear)(\s+(there|team|all|sir|madam|sir/madam))?\s*[,!.]?\s*$", re.I)
_MOJIBAKE = re.compile(r"â€|Ã.|Â |\ufffd")


def _host(url: str) -> str:
    u = re.sub(r"^https?://", "", url.strip(), flags=re.I)
    return u.split("/")[0].split("?")[0].lower().removeprefix("www.")


def links_in(body: str) -> list[tuple[str, str]]:
    """(text, url) for every link: "[words](url)" and bare URLs."""
    out = [(m.group(1), m.group(2)) for m in _MDLINK.finditer(body or "")]
    rest = _MDLINK.sub(" ", body or "")
    out += [(m.group(0), m.group(0)) for m in _URL.finditer(rest)]
    return out


def shingles(text: str, k: int = 3) -> set[tuple[str, ...]]:
    toks = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {tuple(toks[i:i + k]) for i in range(max(0, len(toks) - k + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def deliverability(subject: str, body: str, *, first_name: str | None = None, signature: str | None = None,
                   sent: list[dict] | None = None, to_email: str | None = None, attachments: int = 0) -> list[dict]:
    """What makes a one-to-one cold mail look like bulk to Gmail / Google Workspace:
    an attachment, more than 2 links, a shortener, link words that name another site,
    a greeting with no name, mojibake, a body that is a near-copy of mail already sent
    (`sent`: [{to, subject, body}], approved or sent), the same subject already sent into
    the same domain. Each flag is {"kind": "deliverability", "detail": <plain English>}."""
    flags: list[dict] = []

    def flag(detail: str) -> None:
        flags.append({"kind": "deliverability", "detail": detail})

    if attachments:
        flag(f"{attachments} attachment(s): a file on a first mail from a stranger is a classic spam/phishing "
             "signal; link the resume instead")
    links = links_in(body)
    if len(links) > 2:
        flag(f"{len(links)} links: keep a first mail to one or two (resume, LinkedIn)")
    short = sorted({_host(u) for _, u in links if _host(u) in SHORTENERS})
    if short:
        flag("link shortener (" + ", ".join(short) + "): filters treat a hidden destination as phishing; "
             "write the full link")
    for text, url in links:
        if text != url and _URL.fullmatch(text.strip()) and _host(text) != _host(url):
            flag(f'link words "{text}" name a different site than the link goes to ({_host(url)})')
    lines = [x for x in (body or "").splitlines() if x.strip()]
    greeting = lines[0] if lines else ""
    if _BARE_GREETING.match(greeting):
        flag(f'greeting "{greeting.strip()}" has no name: a mail that could go to anyone reads as bulk')
    elif first_name and first_name.lower() not in greeting.lower():
        flag(f"the greeting does not use their name ({first_name})")
    if _MOJIBAKE.search(f"{subject}\n{body}"):
        flag("broken characters (mojibake) in the subject or body")
    message = _body_only(body or "", signature)
    mine = shingles(message)
    from jobhunter.guardrails import FREEMAIL

    dom = (to_email or "").rsplit("@", 1)[-1].lower() if "@" in (to_email or "") else ""
    if dom in FREEMAIL:
        dom = ""          # gmail.com is a thousand people, not one company's mail server
    near, near_here, best, same_subject, same_domain = 0, 0, 0.0, 0, 0
    from datetime import timedelta

    from jobhunter.db import utcnow

    recent = utcnow() - timedelta(hours=SAME_DOMAIN_HOURS)
    for o in sent or []:
        sim = jaccard(mine, shingles(_body_only(o.get("body") or "", signature)))
        # an item with no "at" (not sent yet, or an old caller) always counts
        here = bool(dom and str(o.get("to") or "").lower().endswith("@" + dom)) and (
            o.get("at") is None or o["at"] >= recent)
        best = max(best, sim)
        near += sim >= SIMILAR_AT
        near_here += here and sim >= SIMILAR_AT
        if here:
            same_domain += 1
            same_subject += (o.get("subject") or "").strip().lower() == (subject or "").strip().lower()
    if near_here:
        flag(f"{near_here} mail(s) to {dom} already approved/sent are near-copies of this one (up to {best:.0%} "
             "the same): the company's mail server groups them and files the group as bulk; change the wording")
    elif near:
        # other companies: still worth seeing, but a pattern may carry it (patterns.ALLOWED_FLAGS)
        flags.append({"kind": "near_duplicate",
                      "detail": f"{near} mail(s) already approved/sent elsewhere are up to {best:.0%} the same as "
                                "this one; Gmail sees every mail you send, so vary the wording"})
    if same_subject:
        flag(f'the subject "{subject}" already went to {same_subject} other inbox(es) at {dom}')
    if same_domain >= 3:
        # a note, not a spam signal: the spread over days is enforced where mail is planned and
        # sent (focus.py's daily share, suppress.domain_cap — three a day), 2026-10-26
        flags.append({"kind": "domain_volume",
                      "detail": f"{same_domain} other asks to {dom} are approved or sent: one company's mail "
                                "server sees every inbox, so they go at most three a day"})
    return flags


_SENT = re.compile(r"(?<=[.!?:])\s+|\n+")
_SPAM = re.compile(r"\b(free|guarantee[d]?|urgent|act now|limited time|winner|click here|100%|risk[- ]free|earn|\$\$)\b", re.I)


def sentences(text: str) -> list[str]:
    """Sentences (and bullet lines) of a plain-text body, greeting and sign-off included."""
    return [s.strip(" -•\t") for s in _SENT.split(text or "") if len(_WORD.findall(s)) >= 2]


def readability(text: str) -> tuple[int, float]:
    """(sentences over 30 words, average words per sentence)."""
    lens = [len(_WORD.findall(s)) for s in sentences(text)]
    if not lens:
        return 0, 0.0
    return sum(1 for n in lens if n > 30), sum(lens) / len(lens)


def subject_problems(subject: str) -> list[str]:
    """What breaks the researched subject rules (playbook §1): 2–8 words / ≤60 chars, no
    "quick question" (Hunter: 2.5% vs 2.9% reply), no fake reply/forward prefix on a cold
    mail, no spam words, no exclamation marks, no shouting."""
    s = (subject or "").strip()
    out = []
    n = len(s.split())
    if n < SUBJECT_MIN_WORDS or n > SUBJECT_MAX_WORDS:
        out.append(f"{n} words — keep it {SUBJECT_MIN_WORDS}–{SUBJECT_MAX_WORDS}")
    if len(s) > SUBJECT_MAX_CHARS:
        out.append(f"{len(s)} characters — over {SUBJECT_MAX_CHARS}, it gets cut off on a phone")
    if re.search(r"quick question", s, re.I):
        out.append('"quick question" underperforms (Hunter 2026)')
    if re.match(r"^\s*(re|fwd?)\s*:", s, re.I):
        out.append("a fake Re:/Fwd: on a first mail reads as a trick")
    if "!" in s:
        out.append("no exclamation marks")
    if _SPAM.search(s):
        out.append(f'spam-filter word: "{_SPAM.search(s).group(0)}"')
    if any(len(w) > 4 and w.isupper() and w.isalpha() for w in s.split()):
        out.append("a shouted word in capitals")
    return out


__all__ = ["GateResult", "check", "deliverability", "links_in", "shingles", "jaccard", "SIMILAR_AT", "AI_TELLS", "MIN_WORDS", "MAX_WORDS", "BLURB_MAX_WORDS", "readability",
           "sentences", "subject_problems"]
