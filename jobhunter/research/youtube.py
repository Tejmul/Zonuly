"""YouTube search and transcripts, via yt-dlp.

Useful for job hunting in one specific way: founder interviews, launch talks and
conference recordings say what a company is actually building, in the founder's own
words, months before the careers page does. That is the kind of detail a referral
ask can honestly open with.

Zero config — yt-dlp needs no key. It does need a JS runtime (node or deno) for
YouTube; Agent Reach's installer sets that up.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from jobhunter.research import backends, cache
from jobhunter.research.models import Video

log = logging.getLogger(__name__)


def search(query: str, limit: int = 5, *, fresh: bool = False, timeout: int | None = None) -> dict:
    """Search YouTube. Metadata only — no download, no transcript."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    if not fresh:
        hit = cache.get("youtube", query, limit=limit)
        if hit:
            return hit

    if "yt-dlp" not in backends.candidates("youtube"):
        return {
            "query": query,
            "backend": None,
            "results": [],
            "error": "yt-dlp not installed",
            "hint": backends.HINTS["yt-dlp"],
        }

    p = backends.run(
        "yt-dlp",
        [
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-end",
            str(limit),
            f"ytsearch{limit}:{query}",
        ],
        timeout=timeout or 120,
    )
    if not p.ok and not p.out.strip():
        return {
            "query": query,
            "backend": "yt-dlp",
            "results": [],
            "error": (p.err or "yt-dlp returned nothing").strip().splitlines()[0][:200] if p.err else "yt-dlp returned nothing",
        }

    videos: list[Video] = []
    for line in p.out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = d.get("id")
        if not vid:
            continue
        videos.append(
            Video(
                title=d.get("title") or "",
                url=d.get("url") or f"https://www.youtube.com/watch?v={vid}",
                channel=d.get("channel") or d.get("uploader"),
                video_id=vid,
                duration=int(d["duration"]) if isinstance(d.get("duration"), (int, float)) else None,
                views=int(d["view_count"]) if isinstance(d.get("view_count"), (int, float)) else None,
            )
        )

    out = {"query": query, "backend": "yt-dlp", "results": [v.as_dict() for v in videos[:limit]]}
    if videos:
        cache.put("youtube", query, out, backend="yt-dlp", limit=limit)
    return out


_VTT_TS = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_VTT_TAG = re.compile(r"<[^>]+>")


def _vtt_to_text(raw: str, limit: int) -> str:
    """Subtitles to prose: drop cues, tags and the duplicate lines auto-captions emit."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line in ("WEBVTT",) or _VTT_TS.match(line) or line.isdigit():
            continue
        if line.startswith(("Kind:", "Language:", "NOTE ", "STYLE")):
            continue
        line = _VTT_TAG.sub("", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return " ".join(lines)[:limit]


def transcript(url: str, *, lang: str = "en", max_chars: int = 20000, timeout: int | None = None) -> dict:
    """Fetch a video's subtitles as plain text. Never downloads the video itself."""
    url = (url or "").strip()
    if not url:
        return {"url": url, "error": "empty url"}

    hit = cache.get("youtube", f"transcript:{url}", lang=lang)
    if hit:
        return hit

    if "yt-dlp" not in backends.candidates("youtube"):
        return {"url": url, "error": "yt-dlp not installed", "hint": backends.HINTS["yt-dlp"]}

    with tempfile.TemporaryDirectory(prefix="jobhunter-yt-") as tmp:
        p = backends.run(
            "yt-dlp",
            [
                "--write-sub",
                "--write-auto-sub",
                "--sub-lang",
                lang,
                "--sub-format",
                "vtt",
                "--skip-download",
                "--no-warnings",
                "-o",
                str(Path(tmp) / "%(id)s"),
                url,
            ],
            timeout=timeout or 180,
        )
        files = sorted(Path(tmp).glob("*.vtt"))
        if not files:
            return {
                "url": url,
                "error": "no subtitles available",
                "detail": (p.err or "").strip().splitlines()[-1][:200] if (p.err or "").strip() else "",
            }
        try:
            raw = files[0].read_text(encoding="utf-8", errors="replace")
        except OSError as e:  # noqa: BLE001
            return {"url": url, "error": f"could not read subtitles: {e}"}

    text = _vtt_to_text(raw, max_chars)
    out = {"url": url, "backend": "yt-dlp", "lang": lang, "chars": len(text), "text": text}
    if text:
        cache.put("youtube", f"transcript:{url}", out, backend="yt-dlp", lang=lang)
    return out
