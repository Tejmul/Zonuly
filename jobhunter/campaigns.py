"""Campaigns — `/api/campaigns/*`.

A campaign is an outreach run that n8n executes; Zonuly's part is to announce that
it has started. There is no campaign table yet: the id is whatever the caller uses
to track it (or one minted here), and the record of the run lives in n8n.

Sync `def` handlers, like the research router: the call underneath is a blocking
HTTP post, and FastAPI's threadpool keeps it off the event loop.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jobhunter import n8n

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignStartIn(BaseModel):
    campaign_id: str | None = Field(None, min_length=1, max_length=120,
                                    description="Your id for the campaign; minted when omitted")
    campaign_name: str | None = Field(None, max_length=200)
    # anything else the workflow wants (list key, segment, sender...) rides along untouched
    metadata: dict | None = None


@router.get("/status")
def campaigns_status() -> dict:
    """Whether the n8n side is wired up. The URL itself is never returned."""
    return {"n8n": n8n.status()}


@router.post("/start")
def start_campaign(body: CampaignStartIn) -> dict:
    campaign_id = body.campaign_id or uuid.uuid4().hex[:12]
    if not n8n.configured():
        raise HTTPException(503, n8n.status()["hint"])
    try:
        result = n8n.trigger_campaign(campaign_id, body.campaign_name, action="start", extra=body.metadata)
    except n8n.N8nError as e:
        # 502: the failure is upstream of us, and the caller may simply retry
        raise HTTPException(502, f"Campaign {campaign_id} was not started: {e}") from e
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "campaign_name": body.campaign_name,
        "action": "start",
        "n8n": result,
    }
