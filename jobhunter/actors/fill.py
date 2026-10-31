"""Fill the unfilled — run company-people over every company with no current referrer and
store what it finds, so the dashboard shows people, addresses and who can refer.

    unfilled()       active companies (status null, tier not reject) with zero contacts that
                     roles.good_referrer_counts counts — the same "can refer + there now"
                     test the referral job and the dashboard use — in db.people_order
    fill_company()   company-people → store:
                       LinkedIn people (verified/likely-current)  contacts.linkedin_public._save
                       team-page people                           contacts.team_people._save
                       YC founders                                contacts.team_people._save
                     every write through guardrails; then contacts.addresses.fill applies
                     the company's learned address format (MX-checked, labelled a guess)
    fill()           the loop, with a per-company summary and the referrer count after

Nothing here talks to the network itself — the actor does, paced and breaker-guarded.
When LinkedIn walls a route the other sources keep filling.
"""

from __future__ import annotations

import logging

from sqlmodel import col, select

from jobhunter.db import Company, Contact, get_session, init_db, people_order

log = logging.getLogger(__name__)


def unfilled(limit: int | None = None) -> list[int]:
    from jobhunter.contacts import roles

    init_db()
    with get_session() as s:
        rows = s.exec(select(Company).where(col(Company.status).is_(None),
                                            col(Company.tier).is_distinct_from("reject"))).all()
        good = roles.good_referrer_counts(s, [c.id for c in rows])
        todo = sorted((c for c in rows if not good.get(c.id)), key=people_order)
        return [c.id for c in todo][: limit or None]


def fill_company(company_id: int, *, sources: list[str] | None = None, max_verify: int = 10,
                 apify: bool = False, apify_max: int = 25, apify_mode: str = "short",
                 titles: list[str] | None = None) -> dict:
    from jobhunter import actors
    from jobhunter.contacts import addresses, roles
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.contacts import team_people

    init_db()
    with get_session() as s:
        c = s.get(Company, company_id)
        if c is None:
            return {"company_id": company_id, "error": "no such company"}
        inp = dict(company=c.name, domain=c.domain, website=c.website, city=c.city, github_org=c.github_org,
                   linkedin=c.linkedin_url)
        known = [{"name": k.name, "role": k.role, "linkedin": k.linkedin, "github": k.github, "confidence": k.confidence}
                 for k in s.exec(select(Contact).where(Contact.company_id == company_id)).all()]
    src = list(sources or ("known", "linkedin-company", "search", "jobs", "team", "yc"))
    if apify and "apify" not in src:
        src.append("apify")
    row = actors.run("company-people", **inp, sources=src, known=known, max_verify=max_verify,
                     apify_max=apify_max, apify_mode=apify_mode, titles=titles)
    people = actors.read_dataset(row["run_id"])
    fresh = [p for p in people if "known" not in p["sources"]]
    # a LinkedIn person is stored only on evidence stronger than a search headline: their
    # profile linked to the company's page, the company's job (hiring team), its YC page,
    # or Apify's People-tab row. 2026-10-18: headline-only hits for "Braintrust" were
    # namesakes — eight wrong contacts, removed the same minute.
    strong = {"jobs", "yc", "apify"}
    li = [{"name": p["name"], "url": p["linkedin_url"], "role": p["title"] or p["headline"] or "",
           "headline": p["headline"] or p["title"], "email": p.get("email"), "email_status": p.get("email_status")}
          for p in fresh if p["linkedin_url"] and (p["status"] == "verified-current"
                                                    or (p["status"] == "likely-current" and strong & set(p["sources"])))]
    # 2026-10-31: someone already on file whom the People tab returned WITH an address — the address
    # was paid for; _save attaches it to the person we hold (it skipped them as "known" before)
    li += [{"name": p["name"], "url": p["linkedin_url"], "role": p["title"] or p["headline"] or "",
            "headline": p["headline"] or p["title"], "email": p.get("email"), "email_status": p.get("email_status")}
           for p in people if "known" in p["sources"] and "apify" in p["sources"] and p.get("email") and p["name"]]
    team = [{"name": p["name"], "title": p["title"] or "", "url": next(
                (e["url"] for e in p["evidence"] if e["source"] == "team"), None)}
            for p in fresh if "team" in p["sources"] and not p["linkedin_url"]]
    yc = [{"name": p["name"], "title": p["title"] or "Founder", "linkedin": p["linkedin_url"],
           "url": next((e["url"] for e in p["evidence"] if e["source"] == "yc"), None)}
          for p in fresh if "yc" in p["sources"]]
    saved = {"linkedin": lp._save(company_id, li, "actor:company-people") if li else 0}
    if team:
        saved["team"] = team_people._save(company_id, team, kind="team").get("added", 0)
    if yc:
        saved["yc"] = team_people._save(company_id, yc, kind="yc").get("added", 0)
    try:
        addr = addresses.fill(company_id)
    except Exception as e:  # noqa: BLE001
        addr = {"error": str(e)[:120]}
    with get_session() as s:
        after = roles.good_referrer_counts(s, [company_id]).get(company_id, 0)
    return {"company_id": company_id, "company": inp["company"], "run_id": row["run_id"], "status": row["status"],
            "found": len(people), "new": len(fresh), "saved": saved, "addresses": addr,
            "referrers_now": after, "sources": row["stats"].get("sources"),
            "linkedin": row["stats"].get("resolved_linkedin")}


def fill(limit: int = 10, **kw) -> list[dict]:
    out = []
    for cid in unfilled(limit):
        r = fill_company(cid, **kw)
        log.info("fill: %s → found %s, saved %s, referrers now %s", r.get("company"), r.get("found"),
                 r.get("saved"), r.get("referrers_now"))
        out.append(r)
    return out
