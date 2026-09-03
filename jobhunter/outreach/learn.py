"""LEARN — MOTIV §4 step 12: the funnel with numbers, so next month beats this one.

Everything here is derived from rows that already exist (Email, Reply, Contact,
Company, Job); nothing is stored. The question is always the same — of the emails we
sent, which kind got a reply, and which got a *yes* — broken down by the things we
choose upstream: who we asked (role class), what the company can pay (Pay Power),
its segment, its region, where we found it, and how the email was framed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlmodel import select

from jobhunter.db import Company, Contact, Email, Job, Reply, get_session, init_db, utcnow


def _bucket(rows, key):
    out: dict[str, dict] = defaultdict(lambda: {"sent": 0, "replied": 0, "positive": 0})
    for r in rows:
        k = key(r) or "unknown"
        out[k]["sent"] += 1
        if r["replied"]:
            out[k]["replied"] += 1
        if r["positive"]:
            out[k]["positive"] += 1
    return {
        k: {**v, "reply_rate": round(v["replied"] / v["sent"], 3) if v["sent"] else 0.0,
            "yes_rate": round(v["positive"] / v["sent"], 3) if v["sent"] else 0.0}
        for k, v in sorted(out.items(), key=lambda kv: -kv[1]["sent"])
    }


def report(*, days: int | None = None) -> dict:
    """Reply and yes rates by every lever we control, plus the weekly curve."""
    from jobhunter import segments as seg

    init_db()
    with get_session() as session:
        emails = session.exec(select(Email).where(Email.status.in_(["sent", "replied"]))).all()  # type: ignore[attr-defined]
        if days:
            cutoff = utcnow() - timedelta(days=days)
            emails = [e for e in emails if e.sent_at and e.sent_at >= cutoff]
        replies = session.exec(select(Reply)).all()
        contacts = {c.id: c for c in session.exec(select(Contact)).all()}
        companies = {c.id: c for c in session.exec(select(Company)).all()}
        jobs = {j.id: j for j in session.exec(select(Job)).all()}

    by_email: dict[int, list[Reply]] = defaultdict(list)
    for r in replies:
        by_email[r.email_id].append(r)

    rows = []
    for e in emails:
        rs = by_email.get(e.id, [])
        c = contacts.get(e.contact_id)
        co = companies.get(e.company_id)
        j = jobs.get(e.job_id) if e.job_id else None
        segment = seg.classify(co.description if co else None, co.notes if co else None, co.name if co else None)[0].label if co else None
        rows.append({
            "replied": bool(rs), "positive": any(r.sentiment == "positive" for r in rs),
            "closed": any(r.sentiment == "closed" for r in rs),
            "role_class": c.role_class if c else None,
            "address": c.confidence if c else None,
            "pay_power": co.pay_power_band if co else None,
            "segment": segment, "region": co.hq_region if co else None,
            "source": co.hiring_claim_source if co else None,
            "kind": e.kind, "fresher_role": (not j.is_senior) if j else None,
            "anywhere": bool(j.remote_anywhere) if j else None,
            "week": e.sent_at.strftime("%G-W%V") if e.sent_at else None,
            "words": len((e.body or "").split()),
        })

    total = len(rows)
    replied = sum(1 for r in rows if r["replied"])
    positive = sum(1 for r in rows if r["positive"])
    closed = sum(1 for r in rows if r["closed"])
    return {
        "window_days": days, "sent": total, "replied": replied, "positive": positive, "closed": closed,
        "reply_rate": round(replied / total, 3) if total else 0.0,
        "yes_rate": round(positive / total, 3) if total else 0.0,
        "by_who_we_asked": _bucket(rows, lambda r: r["role_class"]),
        "by_address_confidence": _bucket(rows, lambda r: r["address"]),
        "by_pay_power": _bucket(rows, lambda r: r["pay_power"]),
        "by_segment": _bucket(rows, lambda r: r["segment"]),
        "by_region": _bucket(rows, lambda r: r["region"]),
        "by_where_we_found_them": _bucket(rows, lambda r: r["source"]),
        "by_email_kind": _bucket(rows, lambda r: r["kind"]),
        "by_role_level": _bucket(rows, lambda r: None if r["fresher_role"] is None else ("fresher role" if r["fresher_role"] else "senior role")),
        "by_remote_anywhere": _bucket(rows, lambda r: None if r["anywhere"] is None else ("says work from anywhere" if r["anywhere"] else "not stated")),
        "by_length": _bucket(rows, lambda r: "under 100 words" if r["words"] < 100 else "100–140 words" if r["words"] <= 140 else "over 140 words"),
        "by_week": _bucket(rows, lambda r: r["week"]),
        "reading": _reading(total, replied, positive, closed),
    }


def _reading(total: int, replied: int, positive: int, closed: int) -> str:
    if total == 0:
        return "Nothing sent yet. The first 100 sends are the calibration set — expect 5–10 replies and about a third of them 'hiring is done'."
    rr = replied / total
    if rr < 0.03:
        return "Reply rate is under 3%: check the addresses (bounces?), the send window, and whether the emails read as templates."
    if rr < 0.08:
        return "Reply rate is in the expected 5–10% band. Compare the breakdowns above: shift sends toward the buckets with the higher yes rate."
    return "Reply rate is above 8%: keep the framing, and spend the 25 a day on the buckets with the best yes rate."


__all__ = ["report"]
