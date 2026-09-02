"""Resume ingestion: PDF/MD/TXT -> structured profile.json via the local LLM.

The profile is the ground truth every downstream agent reads: the matcher scores
JDs against it, and the drafter mines it for "here's why I'm relevant" lines.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

PROFILE_PATH = ROOT / CONFIG["profile_path"]

SYSTEM = """You are a precise resume parser. You extract facts that are literally present in the resume.
You never invent skills, employers, dates, or numbers. If a field is absent, use null or an empty list.
Reply with JSON only."""

SCHEMA_PROMPT = """Extract this resume into JSON with exactly these keys:

{
  "name": string,
  "email": string|null,
  "phone": string|null,
  "links": [string],                  // github/linkedin/portfolio URLs
  "headline": string,                 // one line, e.g. "Final-year CS+AI student, AI/ML engineer"
  "summary": string,                  // 2-3 sentences, factual, from the resume
  "years_experience": number,         // total professional/internship years, best estimate
  "education": [ {"degree": string, "institution": string, "years": string, "grade": string|null} ],
  "experience": [ {"title": string, "company": string, "dates": string, "highlights": [string]} ],
  "projects": [ {"name": string, "description": string, "tech": [string], "url": string|null} ],
  "skills": {
    "languages": [string],
    "ai_ml": [string],                // frameworks, model work, techniques
    "backend": [string],
    "cloud_devops": [string],
    "databases": [string],
    "other": [string]
  },
  "strengths": [string],              // 3-6 phrases a recruiter would care about, grounded in the resume
  "target_titles": [string]           // job titles this person is a credible candidate for
}

RESUME TEXT:
---
{resume_text}
---

JSON only."""


def read_resume_text(path: Path | None = None) -> str:
    """Extract raw text from the resume file (PDF, Markdown, or plain text)."""
    path = Path(path) if path else ROOT / CONFIG.get("resume_path", "profile/resume.pdf")
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        # tolerate any of resume.pdf/.md/.txt sitting in profile/
        for alt in ("resume.pdf", "resume.md", "resume.txt"):
            cand = ROOT / "profile" / alt
            if cand.exists():
                path = cand
                break
        else:
            raise FileNotFoundError(f"No resume found at {path} (or profile/resume.{{pdf,md,txt}})")

    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages)
        # PDF text layers lose hyperlink targets; pull them from annotations
        urls: list[str] = []
        for page in reader.pages:
            for annot in page.get("/Annots") or []:
                try:
                    uri = (annot.get_object().get("/A") or {}).get("/URI")
                except Exception:  # noqa: BLE001 — malformed annots are common
                    continue
                if uri and uri not in urls:
                    urls.append(uri)
        if urls:
            text += "\n\nLINKS FOUND IN DOCUMENT:\n" + "\n".join(urls)
        return _clean(text)

    return _clean(path.read_text(encoding="utf-8", errors="replace"))


def _clean(text: str) -> str:
    # pypdf sprinkles soft hyphens / odd spacing; collapse the worst of it
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def build_profile(path: Path | None = None, *, save: bool = True) -> dict[str, Any]:
    """Parse the resume into structured profile.json."""
    from jobhunter import llm

    text = read_resume_text(path)
    log.info("Resume text extracted: %d chars", len(text))

    # The resume is truncated rather than chunked: the `cheap` alias has a large
    # context, but a profile is built from the first two pages of a CV anyway.
    prompt = SCHEMA_PROMPT.replace("{resume_text}", text[:9000])
    data = llm.chat_json(
        prompt, SYSTEM, temperature=0.1, num_predict=4096,
        alias="cheap", purpose="resume-parse", default=None,
    )

    if not isinstance(data, dict) or not data.get("name"):
        raise RuntimeError(
            "The model could not parse the resume into a profile — "
            "run `python scripts/run.py doctor` to check the OpenRouter key and budget"
        )

    data["_resume_text"] = text
    data["_source"] = str(path or CONFIG.get("resume_path"))

    if save:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Wrote %s", PROFILE_PATH)
    return data


def load_profile() -> dict[str, Any]:
    """Load profile.json, building it on first use."""
    if not PROFILE_PATH.exists():
        return build_profile()
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def profile_summary(profile: dict[str, Any] | None = None, *, max_chars: int = 2200) -> str:
    """Compact plain-text rendering of the profile, for prompts that score/draft against it."""
    p = profile or load_profile()
    parts: list[str] = []
    parts.append(f"Name: {p.get('name')}")
    if p.get("headline"):
        parts.append(f"Headline: {p['headline']}")
    parts.append(f"Years of experience: {p.get('years_experience', 0)}")
    if p.get("summary"):
        parts.append(f"Summary: {p['summary']}")

    edu = p.get("education") or []
    if edu:
        parts.append("Education: " + "; ".join(f"{e.get('degree')} @ {e.get('institution')} ({e.get('years')})" for e in edu[:2]))

    skills = p.get("skills") or {}
    flat = [s for group in skills.values() if isinstance(group, list) for s in group]
    if flat:
        parts.append("Skills: " + ", ".join(dict.fromkeys(flat))[:700])

    exp = p.get("experience") or []
    if exp:
        parts.append("Experience:")
        for e in exp[:4]:
            hl = "; ".join((e.get("highlights") or [])[:2])
            parts.append(f"  - {e.get('title')} @ {e.get('company')} ({e.get('dates')}): {hl}")

    proj = p.get("projects") or []
    if proj:
        parts.append("Projects:")
        for pr in proj[:4]:
            parts.append(f"  - {pr.get('name')}: {pr.get('description')} [{', '.join(pr.get('tech') or [])}]")

    if p.get("strengths"):
        parts.append("Strengths: " + "; ".join(p["strengths"]))

    return "\n".join(parts)[:max_chars]


def embedding_text(profile: dict[str, Any] | None = None) -> str:
    """Denser, keyword-heavy rendering used for the resume<->JD embedding comparison."""
    p = profile or load_profile()
    skills = p.get("skills") or {}
    flat = [s for group in skills.values() if isinstance(group, list) for s in group]
    bits = [
        p.get("headline") or "",
        p.get("summary") or "",
        " ".join(p.get("target_titles") or []),
        " ".join(dict.fromkeys(flat)),
        " ".join(f"{e.get('title')} {' '.join(e.get('highlights') or [])}" for e in (p.get("experience") or [])),
        " ".join(f"{pr.get('name')} {pr.get('description')} {' '.join(pr.get('tech') or [])}" for pr in (p.get("projects") or [])),
    ]
    return " ".join(b for b in bits if b)[:6000]
