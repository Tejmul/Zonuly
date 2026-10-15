"""HTTP surface for reviewing drafts and approving patterns — `/api/outreach/*`.

A separate APIRouter so `api.py` only gains an include_router line (like research). The
per-email approve / reject routes stay where they were (/api/emails/{id}/approve|reject);
these add what the review flow was missing (2026-10-16): a proposal turned into a draft,
an edit saved without approving, an approval undone, how many drafts wait on the user,
and the patterns (outreach/patterns.py) — approve a kind of email once, revoke it.

Writes are refused on the public instance by the access middleware, like every POST.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jobhunter.outreach import patterns

router = APIRouter(prefix="/api/outreach", tags=["review"])


def _ok(result: dict) -> dict:
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class TextIn(BaseModel):
    subject: str | None = None
    body: str | None = None


@router.get("/review/count")
def review_count() -> dict:
    """Drafts waiting for the user, and approved mail he can still undo (the nav badge)."""
    return patterns.review_counts()


@router.post("/proposals/{proposal_id}/draft")
def proposal_to_draft(proposal_id: int) -> dict:
    """The proposal's exact email into the review queue as a draft (never approved).
    No model call and no network: fast enough to answer inline."""
    from jobhunter import proposals

    r = proposals.to_draft(proposal_id)
    if "error" in r and not r.get("email_id"):
        raise HTTPException(400, r["error"])
    return r


@router.post("/emails/{email_id}/save")
def save_draft(email_id: int, payload: TextIn) -> dict:
    return _ok(patterns.save(email_id, subject=payload.subject, body=payload.body))


@router.post("/emails/{email_id}/unapprove")
def unapprove(email_id: int) -> dict:
    """Back to review before the send agent takes it — a pattern's approval or your own."""
    return _ok(patterns.unapprove(email_id))


@router.get("/patterns")
def list_patterns() -> dict:
    return patterns.list_patterns()


@router.post("/patterns/from-email/{email_id}")
def approve_pattern(email_id: int, payload: TextIn | None = None) -> dict:
    """Approve this draft (with the edits sent) AND make it the pattern for its kind."""
    payload = payload or TextIn()
    return _ok(patterns.approve_from(email_id, subject=payload.subject, body=payload.body))


@router.post("/patterns/{pattern_id}/revoke")
def revoke_pattern(pattern_id: int) -> dict:
    return _ok(patterns.revoke(pattern_id))


@router.post("/patterns/{pattern_id}/apply")
def apply_pattern(pattern_id: int) -> dict:
    """Run the pattern over the drafts already waiting in its segment (no model call)."""
    return _ok(patterns.apply_to_waiting(pattern_id))
