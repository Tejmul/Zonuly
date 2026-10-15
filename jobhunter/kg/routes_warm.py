"""/api/kg/warm/* and /api/kg/similar/* — the graph's outreach answers over HTTP (kg/warm.py).

A separate router so api.py gains one include_router line. All GET, read-only, no
network, no model; contact email addresses are never in these payloads (only
`has_email`), so a public instance exposes nothing the company page doesn't already.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/kg", tags=["knowledge graph"])


def _or_404(out: dict) -> dict:
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@router.get("/warm/company/{company_id}")
def warm_company(company_id: int, people: int = Query(12, ge=1, le=50)) -> dict:
    """Warm paths into one company: people with a link to your background (ranked by
    graph signal) and company-level paths (alumni elsewhere, shared investors, city)."""
    from jobhunter.kg import warm

    return _or_404(warm.warm_paths(company_id, people=people))


@router.get("/warm/contact/{contact_id}")
def warm_contact(contact_id: int) -> dict:
    """Warm paths and the graph referrer signal for one person."""
    from jobhunter.kg import warm

    return _or_404(warm.contact_paths(contact_id))


@router.get("/similar/{company_id}")
def similar(company_id: int, limit: int = Query(8, ge=1, le=40)) -> dict:
    """"If you liked this company": graph neighbours by shared investors and skills."""
    from jobhunter.kg import warm

    return _or_404(warm.similar_companies(company_id, limit=limit))


@router.get("/next-companies")
def next_companies(limit: int = Query(12, ge=1, le=50), seed: list[int] | None = Query(None)) -> dict:
    """The next companies to work on — similar to the ones you're already engaged with."""
    from jobhunter.kg import warm

    return warm.next_companies(seeds=seed, limit=limit)
