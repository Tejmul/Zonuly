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

Pure functions; no I/O, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobhunter import CONFIG

_G = ((CONFIG.get("outreach") or {}).get("gate") or {})
MIN_WORDS = int(_G.get("min_words", 70))
MAX_WORDS = int(_G.get("max_words", 160))

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
}


@dataclass
class GateResult:
    flags: list[dict] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    words: int = 0

    @property
    def blocked(self) -> bool:
        """Blocked means "read this before approving", not "cannot send"."""
        return any(f["kind"] in ("faithfulness",) for f in self.flags)

    def as_list(self) -> list[dict]:
        return self.flags


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower())


def _body_only(body: str, signature: str | None) -> str:
    if signature and signature in body:
        return body[: body.index(signature)]
    return body


def check(body: str, *, corpus: str, signature: str | None = None,
          address_confidence: str | None = None) -> GateResult:
    """Run every gate over `body` against the facts in `corpus`."""
    r = GateResult()
    text = _body_only(body or "", signature)
    norm_corpus = " " + _normalise(corpus) + " "

    # ---- faithfulness: proper nouns and numbers must exist in the corpus
    seen: set[str] = set()
    for m in _PROPER.finditer(text):
        # "Tailscale's" is Tailscale; "LLM-backed" is LLM; "I'm" is not a name
        phrase = re.sub(r"[’']s\b", "", m.group(1)).strip()
        phrase = " ".join(w.split("-")[0] if w.lower().startswith(("i'm", "i’m", "i've", "i’ve", "i'd", "i’d")) is False else w for w in phrase.split())
        phrase = re.sub(r"^(?:I'm|I’m|I've|I’ve|I'd|I’d|I'll|I’ll)\s+", "", phrase).strip()
        if not phrase:
            continue
        words = phrase.split()
        # a capitalised word at the start of a sentence is not a proper noun claim
        start = m.start()
        at_sentence_start = start == 0 or text[max(0, start - 2):start].strip() in ("", ".", "!", "?", ",")
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
        if digits and digits not in re.sub(r"[^\d.\s]", " ", corpus):
            r.unsupported.append(num)
    if r.unsupported:
        r.flags.append({"kind": "faithfulness",
                        "detail": "not in the facts this draft was given: " + ", ".join(r.unsupported[:8])})

    # ---- AI-tell vocabulary
    low = text.lower()
    tells = [t for t in AI_TELLS if t in low]
    if tells:
        r.flags.append({"kind": "ai_tell", "detail": "reads machine-written: " + ", ".join(tells[:5])})

    # ---- length
    r.words = len(_WORD.findall(text))
    if r.words < MIN_WORDS:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — under {MIN_WORDS}, may read as thin"})
    elif r.words > MAX_WORDS:
        r.flags.append({"kind": "length", "detail": f"{r.words} words — over {MAX_WORDS}, a busy engineer stops reading"})

    # ---- one ask
    asks = len(_ASK.findall(text))
    if asks > 2:
        r.flags.append({"kind": "one_ask", "detail": f"{asks} questions/requests — make it one"})

    # ---- dashes
    if _DASH.search(text):
        r.flags.append({"kind": "dashes", "detail": "an em/en dash survived; replace with a comma or a full stop"})

    # ---- the address itself
    if address_confidence and address_confidence != "verified":
        r.flags.append({"kind": "guessed", "detail": f"address is {address_confidence}: it may bounce, and bounces count against the account"})

    return r


__all__ = ["GateResult", "check", "AI_TELLS", "MIN_WORDS", "MAX_WORDS"]
