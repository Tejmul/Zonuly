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
          exclude: list[str] | None = None, subject: str | None = None) -> GateResult:
    """Run every gate over `body` against the facts in `corpus`.

    `forbidden` are skills the needs brief marked uncovered (needs.forbidden_terms): the
    body naming one is a claim the resume cannot back, whatever the corpus contains."""
    r = GateResult()
    text = _body_only(body or "", signature)
    norm_corpus = " " + _normalise(corpus) + " "

    # ---- uncovered needs: the role asks for it, the resume does not have it
    if forbidden:
        from jobhunter.skills import pattern

        for skill in forbidden:
            if re.search(pattern(skill), text, re.I):
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
    if r.words < MIN_WORDS:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — under {MIN_WORDS}, may read as thin"})
    elif r.words > MAX_WORDS:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — over {MAX_WORDS}, a busy engineer stops reading"})

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


__all__ = ["GateResult", "check", "AI_TELLS", "MIN_WORDS", "MAX_WORDS", "BLURB_MAX_WORDS", "readability",
           "sentences", "subject_problems"]
