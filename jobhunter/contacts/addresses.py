"""Addresses for ready referrers, from the company's own confirmed format — never a blind guess.

2026-10-08: 4,042 people the evidence places at a company now, and only 2,428 of them
with an address. Most companies already show us how their addresses are built: commit
and profile addresses on their domain (GitHub), addresses on their own team pages, and
sometimes a careers page that says "write to firstname.lastname@acme.com". So:

    learn    vote over every NAMED address already confirmed on the company's domain —
             not one made by a pattern, not a shared inbox, not a name read off the
             address itself — plus the format their own page states (kept by the
             guardrails as a "format example" reject). patterns.learn_with_evidence decides:
             ≥2 agreeing (or 1 + their page), the leader clearly ahead. The verdict and its
             evidence go on company.email_pattern_evidence; only a confirmed one sets
             company.email_pattern (guardrails.set_email_pattern).
    apply    every ready referrer (guardrails.is_ready) with a real first + last name and
             no address gets the confirmed pattern's address, after an MX lookup on the
             domain. No SMTP probing. Labelled pattern-guessed — the send ledger's 5-a-day
             guessed cap applies to it — with the evidence in research_notes.
    retract  an address an earlier, unconfirmed pattern made (one vote, or none) is taken
             back — kept as a reject — unless a draft already went to it.

Layering: db + guardrails + contacts.patterns/verify (DNS only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import col, select

from jobhunter import guardrails
from jobhunter.contacts import patterns, verify
from jobhunter.db import Company, Contact, Email, ExtractionReject, get_session, init_db

log = logging.getLogger(__name__)

#: sources whose addresses were read, not made: commit / profile, their site, the web, Hunter
_READ_SOURCES = ("github", "site", "web", "hunter", "linkedin-public")


def _pattern_made(p: Contact) -> bool:
    return (p.confidence == "pattern-guessed" or p.source == "pattern"
            or "by pattern" in (p.research_notes or ""))


def _read_on_domain(p: Contact, dom: str) -> str | None:
    """The contact's address when it was READ (not made) on the company's own domain."""
    if not p.email or _pattern_made(p) or p.source not in _READ_SOURCES:
        return None
    e = p.email.lower()
    if e.split("@")[-1] != dom:
        return None
    chk = guardrails.email(e, domain=dom)
    return e if chk and chk.value[1] == "company" else None


def examples(rows: list[Contact], domain: str) -> list[tuple[str, str]]:
    """(name, address) pairs read on the company's domain, with a name not taken from the address.

    Also: an address read with no name beside it (a commit by a handle, an address on a
    page) pairs with the ONE named person at the company whose name it renders under some
    pattern — "rahul.k@" and a LinkedIn "Rahul Kumar" there. Two named people it could be,
    or none, and it is not evidence."""
    out = []
    dom = domain.lower()
    named = [p for p in rows if p.name and (p.name_source or "") != "address" and guardrails.person_name(p.name)
             and patterns.patternable(p.name)]
    for p in rows:
        e = _read_on_domain(p, dom)
        if not e:
            continue
        if p.name and (p.name_source or "") != "address" and guardrails.person_name(p.name):
            out.append((p.name, e))
            continue
        if p.name:
            continue
        owners = {q.name for q in named if patterns.infer_all(e, q.name)}
        if len(owners) == 1:
            out.append((owners.pop(), e))
    return out


def stated(session, company: Company) -> tuple[str, str] | None:
    """(pattern, url) when the company's own page states its address format."""
    if not company.domain:
        return None
    rows = session.exec(select(ExtractionReject).where(
        ExtractionReject.company_id == company.id, ExtractionReject.field == "contact.email",
        col(ExtractionReject.reason).like("a format example%"))).all()
    dom = company.domain.lower()
    for r in rows:
        val = (r.value or "").strip('"').lower()
        if val.split("@")[-1] != dom:
            continue
        src = (r.source or "").lower()
        # the company's own page: the source URL is on their domain (or is their website)
        if src and dom not in src and not (company.website and company.website.lower().split("//")[-1] in src):
            continue
        pat = patterns.stated_pattern(val)
        if pat:
            return pat, r.source or company.website or company.domain
    return None


def learn(session, company: Company, rows: list[Contact] | None = None) -> patterns.PatternVerdict:
    if not company.domain:
        return patterns.PatternVerdict(None, "none", reason="no domain")
    rows = rows if rows is not None else session.exec(
        select(Contact).where(Contact.company_id == company.id)).all()
    return patterns.learn_with_evidence(examples(rows, company.domain), stated=stated(session, company))


@dataclass
class Plan:
    company_id: int
    domain: str | None
    verdict: dict
    pattern_change: str = "unchanged"        # written | agreed | cleared | unchanged
    add: list[tuple[int, str]] = field(default_factory=list)       # (contact id, address)
    retract: list[tuple[int, str, str]] = field(default_factory=list)  # (contact id, address, why)
    skipped: dict = field(default_factory=dict)
    mx_ok: bool | None = None


def plan_company(session, company: Company, *, check_mx: bool = True) -> Plan:
    """What the learn / apply / retract step would do for one company (no writes)."""
    rows = session.exec(select(Contact).where(Contact.company_id == company.id)).all()
    v = learn(session, company, rows)
    plan = Plan(company.id, company.domain, v.as_dict())
    old = company.email_pattern
    if v.status == "confirmed":
        plan.pattern_change = "agreed" if old == v.pattern else "written"
    elif old:
        plan.pattern_change = "cleared"
    taken = {(p.email or "").lower() for p in rows if p.email}
    # an address that bounced is never made again (outreach/suppress.mark_bounced)
    dead = {(p.dead_email or "").lower() for p in rows if getattr(p, "dead_email", None)}
    sent_to = set(session.exec(select(Email.contact_id).where(
        col(Email.contact_id).in_([p.id for p in rows]))).all()) if rows else set()
    pat = v.pattern if v.status == "confirmed" else None
    # retract what an unconfirmed (or a different) pattern made
    for p in rows:
        if not p.email or not _pattern_made(p) or p.id in sent_to:
            continue
        parsed = patterns.normalize_name(p.name)
        expect = patterns.render(pat, *parsed, company.domain) if (pat and parsed and company.domain) else None
        if expect and expect == p.email.lower():
            continue
        why = (f"made by a pattern the addresses do not confirm ({v.reason})" if not pat
               else f"does not follow the confirmed pattern {pat}")
        plan.retract.append((p.id, p.email, why))
        taken.discard(p.email.lower())
    if not pat or not company.domain:
        return plan
    # 2026-10-23, Tejmul: "don't [make] the company emails unless you don't find any mail" — a
    # guessed company address is the last resort (Sarvam: 23 of 24 guessed, 3 bounced, 9 blocked).
    # When someone ready to refer already has a real address (published or verified), guess none.
    real = [p for p in rows if p.email and not _pattern_made(p) and not p.do_not_contact and guardrails.is_ready(p)]
    if real:
        plan.skipped["real_address_held"] = len(real)
        return plan
    for p in rows:
        if p.email and not any(r[0] == p.id for r in plan.retract):
            continue
        if not guardrails.is_ready(p):
            continue
        if not patterns.patternable(p.name) or not guardrails.person_name(p.name):
            plan.skipped["name"] = plan.skipped.get("name", 0) + 1
            continue
        addr = patterns.render(pat, *patterns.normalize_name(p.name), company.domain)
        if any(r[0] == p.id and r[1].lower() == addr for r in plan.retract):
            continue
        if not guardrails.email(addr, domain=company.domain):
            plan.skipped["refused"] = plan.skipped.get("refused", 0) + 1
            continue
        if addr in taken:
            plan.skipped["taken"] = plan.skipped.get("taken", 0) + 1
            continue
        if addr in dead:
            plan.skipped["bounced"] = plan.skipped.get("bounced", 0) + 1
            continue
        taken.add(addr)
        plan.add.append((p.id, addr))
    if plan.add and check_mx:
        plan.mx_ok = bool(verify.mx_hosts(company.domain.lower()))
        if not plan.mx_ok:
            plan.skipped["no_mx"] = len(plan.add)
            plan.add = []
    return plan


def apply_plan(session, company: Company, plan: Plan) -> dict:
    """Write a plan through the guardrails. The caller commits."""
    out = {"pattern": guardrails.set_email_pattern(session, company, plan.verdict), "added": 0, "retracted": 0}
    for cid, addr, why in plan.retract:
        p = session.get(Contact, cid)
        if p is None or (p.email or "").lower() != addr.lower():
            continue
        guardrails.reject(session, company_id=company.id, contact_id=p.id, field="contact.email", value=p.email,
                          reason=f"pattern-guessed address retracted: {why}"[:240], channel="pattern")
        p.email, p.email_kind, p.confidence = None, None, "scraped"
        p.research_notes = f"{p.research_notes or ''}\naddress retracted: {why}".strip()[:2000]
        session.add(p)
        out["retracted"] += 1
    v = plan.verdict
    ev = (f"{v.get('pattern')} — {v.get('agree')} of {v.get('matched')} agree "
          f"({', '.join((v.get('examples') or [])[:3])})" + (f"; stated at {v['stated']}" if v.get("stated") else ""))
    for cid, addr in plan.add:
        p = session.get(Contact, cid)
        if p is None:
            continue
        if guardrails.set_pattern_address(session, p, company, addr, evidence=ev, mx_ok=bool(plan.mx_ok)):
            out["added"] += 1
    return out


def fill(company_id: int, *, dry_run: bool = False) -> dict:
    """Learn, retract and apply for one company, committed."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None or not c.domain:
            return {"company_id": company_id, "skipped": "no domain"}
        plan = plan_company(session, c)
        res = {"company_id": company_id, "status": plan.verdict["status"], "pattern": plan.verdict.get("pattern"),
               "would_add": len(plan.add), "would_retract": len(plan.retract)}
        if dry_run:
            return res
        res.update(apply_plan(session, c, plan))
        session.commit()
    return res


__all__ = ["examples", "stated", "learn", "plan_company", "apply_plan", "fill", "Plan"]
