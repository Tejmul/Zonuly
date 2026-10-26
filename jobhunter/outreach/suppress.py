"""Who must never be written to again, and why — bounces, opt-outs, the do-not-contact list,
and the one-ask-per-company cooldown, checked again at approval and at send.

    bounce      a mailer-daemon answered: the address is dead. It is taken off the contact
                (kept in contact.dead_email so no writer stores it again — guardrails and
                the pattern step both read it), the email is marked failed, and nobody is
                retried at that address. The send-ledger slot it used stays spent: it was
                sent. A pattern-guessed address that bounces also counts toward the bounce
                brake (outreach.bounce_brake): too many in a week and no guessed address is
                sent to until they age out.
    opt-out     "unsubscribe", "please don't email me", "stop" — the person AND the company
                are suppressed forever (contact/company.do_not_contact), every draft or
                approved mail to that company is withdrawn, and the reply is filed negative.
    config      outreach.do_not_contact: addresses, whole domains, company names.
    cooldown    one cold ask per company until someone there replies or 30 days pass, and
                never two approved asks to one company waiting at once. The day's target
                companies (focus.py, outreach.focus) may take outreach.focus.per_company each.
    spam block  a bounce saying Gmail refused the mail as spam (5.7.1 "Message blocked"):
                no cold mail for spam_pause_hours, none into that domain / company for
                spam_block_cooldown_days. And per_domain_daily_cap cold mails a day per domain.

The opt-out line every cold mail carries (outreach.opt_out_line) is added here too.

Layering: db + config only; the sender, the tracker and the batch call it.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, Email, Reply, Setting, utcnow

log = logging.getLogger(__name__)

_O = CONFIG.get("outreach") or {}
_DNC = _O.get("do_not_contact") or {}
COMPANY_COOLDOWN_DAYS = int(_O.get("company_cooldown_days", 30))
ASKS_PER_COMPANY = max(1, int(_O.get("asks_per_company", 1)))
#: per company (by name) — 2026-10-17: everyone at Sarvam AI's Bangalore office, the user's call
ASKS_OVERRIDES = {str(k).lower(): int(v) for k, v in (_O.get("asks_per_company_overrides") or {}).items()}
CONTACT_COOLDOWN_DAYS = int(_O.get("contact_cooldown_days", 30))
#: 2026-10-26, Tejmul: a send day goes to a set of target companies, picked the evening
#: before (jobhunter/focus.py); each may take up to this many cold mails that day
FOCUS_PER_COMPANY = int((_O.get("focus") or {}).get("per_company", 3))
FOCUS_GOAL = int((_O.get("focus") or {}).get("asks_per_company", 25))
FOCUS_KEY = "focus:"
from jobhunter.guardrails import FREEMAIL  # noqa: E402
CIRCLE_KEY = FOCUS_KEY + "circle"
OPT_OUT_LINE = str(_O.get("opt_out_line") or "").strip()
#: opt-out lines earlier drafts carry (and replies quote): a body with one of these already
#: has its opt-out line, and a reply quoting one is not asking us to stop
LEGACY_OPT_OUT_LINES = [
    "If this isn't something you can help with, no worries, and if you'd rather I didn't write again, "
    "just reply \"stop\" and I won't.",
]


def opt_out_lines() -> list[str]:
    """The current opt-out line first, then the older ones still found in stored bodies."""
    return [x for x in [OPT_OUT_LINE, *LEGACY_OPT_OUT_LINES] if x]


def has_opt_out(body: str | None) -> bool:
    return any(x in (body or "") for x in opt_out_lines())
_BRAKE = _O.get("bounce_brake") or {}
BRAKE_DAYS = int(_BRAKE.get("window_days", 7))
BRAKE_MAX = int(_BRAKE.get("max_bounces", 2))
#: statuses that mean "an ask to this company is out, or about to go"
_LIVE = ("approved", "sending", "sent", "replied")


# ------------------------------------------------------------------ the do-not-contact list

def _norm_company(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def config_block(*, email: str | None = None, company: Company | None = None) -> str | None:
    """Why config's do_not_contact forbids this address / company, or None."""
    em = (email or "").strip().lower()
    emails = {str(e).strip().lower() for e in (_DNC.get("emails") or [])}
    domains = {str(d).strip().lower().lstrip("@") for d in (_DNC.get("domains") or [])}
    names = {_norm_company(n) for n in (_DNC.get("companies") or [])}
    if em and em in emails:
        return f"{em} is on outreach.do_not_contact.emails"
    dom = em.split("@")[-1] if "@" in em else ""
    if dom and dom in domains:
        return f"{dom} is on outreach.do_not_contact.domains"
    if company is not None:
        if company.domain and company.domain.lower() in domains:
            return f"{company.domain} is on outreach.do_not_contact.domains"
        if _norm_company(company.name) in names:
            return f"{company.name} is on outreach.do_not_contact.companies"
    return None


def blocked(session, *, contact: Contact | None, company: Company | None, to_email: str | None = None) -> str | None:
    """The reason no mail may go to this person / company, or None. Checked at draft
    selection, at approval and again inside the send."""
    if company is not None and company.do_not_contact:
        return f"{company.name} asked not to be contacted ({company.dnc_reason or 'do-not-contact'})"
    if contact is not None:
        # "Name @ Company" blocks that person at that company only; a bare name, everywhere
        name = " ".join((contact.name or "").lower().split())
        for entry in _DNC.get("people") or []:
            who, _, where = str(entry).partition("@")
            if name and name == " ".join(who.lower().split()) and (
                    not where.strip() or (company is not None and _norm_company(company.name) == _norm_company(where))):
                return f"{contact.name} is on outreach.do_not_contact.people ({entry})"
        if contact.do_not_contact:
            return f"{contact.name or contact.email or 'this person'} asked not to be contacted ({contact.dnc_reason or 'do-not-contact'})"
        if contact.email_status == "bounced" and not contact.email:
            return f"their address bounced ({contact.dead_email}) — find another before writing"
    addr = (to_email or (contact.email if contact else None) or "").lower()
    if addr and contact is not None and contact.dead_email and addr == contact.dead_email.lower():
        return f"{addr} bounced before"
    if addr and session is not None:
        dead = session.exec(select(Contact.id).where(col(Contact.dead_email) == addr)).first()
        if dead:
            return f"{addr} bounced before"
        dnc = session.exec(select(Contact.id).where(col(Contact.email) == addr,
                                                    Contact.do_not_contact == True)).first()  # noqa: E712
        if dnc:
            return f"{addr} asked not to be contacted"
    return config_block(email=addr or None, company=company)


# ------------------------------------------------------------------ the day's target companies

def _focus_rows(session, since: str, until: str | None = None) -> set[int]:
    import json

    out: set[int] = set()
    stmt = select(Setting).where(col(Setting.key) >= FOCUS_KEY + since,
                                 col(Setting.key) <= FOCUS_KEY + (until or "9"))
    for row in session.exec(stmt).all():
        try:
            out.update(int(x["company_id"]) for x in json.loads(row.value)["targets"])
        except (ValueError, KeyError, TypeError):
            continue
    return out


def focus_companies(session, day: str) -> set[int]:
    """The target companies picked for `day` (YYYY-MM-DD). Written by focus.py."""
    return _focus_rows(session, day, day)


def circle_ids(session) -> set[int]:
    """The companies in the circle now (focus.py): each is written to until FOCUS_GOAL asks."""
    import json

    row = session.get(Setting, CIRCLE_KEY)
    try:
        return {int(x["company_id"]) for x in json.loads(row.value)["companies"]} if row else set()
    except (ValueError, KeyError, TypeError):
        return set()


def focus_ids(session, since: str | None = None) -> set[int]:
    """The circle, and the target companies for `since` (default today) and every later day picked."""
    from datetime import date

    return _focus_rows(session, since or date.today().isoformat(), date.max.isoformat()) | circle_ids(session)


# ------------------------------------------------------------------ cooldown

def cooldown(session, email: Email) -> str | None:
    """One cold ask per company until a reply or COMPANY_COOLDOWN_DAYS, never two approved
    at once; one cold mail per person per CONTACT_COOLDOWN_DAYS. Follow-ups are exempt —
    they belong to an ask already made."""
    if email.kind != "cold":
        return None
    since = utcnow() - timedelta(days=COMPANY_COOLDOWN_DAYS)
    others = session.exec(select(Email).where(
        Email.company_id == email.company_id, Email.candidate_id == email.candidate_id, Email.kind == "cold",
        col(Email.status).in_(_LIVE), Email.id != email.id)).all()
    mine = (email.approved_at or utcnow(), email.id or 10**9)
    # ASKS_PER_COMPANY open asks may stand at one company at once (2026-10-17: two at Sarvam
    # the same day, the user's call); one more than that waits for a reply or the cooldown
    ahead: list[str] = []
    for o in others:
        if o.status == "sending" or (o.status == "approved" and (o.approved_at or o.created_at, o.id) < mine):
            ahead.append(f"another ask to this company is already approved (email {o.id})")
            continue
        if o.status == "approved":
            continue      # approved after this one: it waits behind this, not the other way round
        when = o.sent_at or o.created_at
        if when and when >= since:
            replied = session.exec(select(Reply.id).where(Reply.email_id == o.id)).first()
            if not replied:
                ahead.append(f"you asked someone at this company on {when:%d %b} and nobody has replied")
    limit = ASKS_PER_COMPANY
    if ASKS_OVERRIDES:
        co = session.get(Company, email.company_id) if email.company_id else None
        limit = ASKS_OVERRIDES.get((co.name if co else "").lower(), ASKS_PER_COMPANY)
    if email.company_id in focus_ids(session):
        limit = max(limit, FOCUS_GOAL)   # the circle: no waiting for a reply; the domain cap spreads it over days
    if len(ahead) >= limit:
        return (f"{ahead[0]} — at most {limit} open ask(s) per company until a reply or "
                f"{COMPANY_COOLDOWN_DAYS} days (outreach.asks_per_company)")
    since_p = utcnow() - timedelta(days=CONTACT_COOLDOWN_DAYS)
    same = session.exec(select(Email).where(
        Email.contact_id == email.contact_id, Email.kind == "cold", col(Email.status).in_(_LIVE),
        Email.id != email.id)).all()
    for o in same:
        when = o.sent_at or o.created_at
        if o.status == "approved" and (o.approved_at or o.created_at, o.id) > mine:
            continue
        if o.status in ("approved", "sending") or (when and when >= since_p):
            return f"this person already has an ask from you (email {o.id}) — {CONTACT_COOLDOWN_DAYS}-day cooldown"
    return None


# ------------------------------------------------------------------ the opt-out line

def with_opt_out(body: str) -> str:
    """The body with outreach.opt_out_line above the sign-off — once. Idempotent."""
    line = OPT_OUT_LINE
    if not line or not body or has_opt_out(body):
        return body
    found = [(body.find(m), m) for m in ("\n\nBest,\n", "\n\nThanks,\n", "\n\nThanks\n", "\n\nRegards,\n",
                                         "\n\nCheers,\n") if m in body]
    if found:
        _, marker = min(found)
        head, tail = body.split(marker, 1)
        return f"{head.rstrip()}\n\n{line}{marker}{tail}"
    return f"{body.rstrip()}\n\n{line}"


# ------------------------------------------------------------------ opt-out replies

_OPT_OUT = [
    re.compile(r"\bun-?subscribe\b", re.I),
    re.compile(r"\bremove me\b", re.I),
    re.compile(r"\btake me off\b", re.I),
    re.compile(r"\bopt(?:ed)?[- ]?out\b", re.I),
    re.compile(r"\bno more (?:e-?mails?|messages?)\b", re.I),
    re.compile(r"\b(?:don'?t|do not|never|stop|quit)\s+(?:e-?mail(?:ing)?|mail(?:ing)?|contact(?:ing)?|messag(?:e|ing)|"
               r"writ(?:e|ing)(?:\s+to)?|spam(?:ming)?|reach(?:ing)?\s+out(?:\s+to)?)\s+(?:me|us|again|here)\b", re.I),
    re.compile(r"\b(?:don'?t|do not)\s+(?:e-?mail|contact|write to|message)\b", re.I),
    re.compile(r"^\s*(?:stop|unsubscribe|remove)\s*[.!]*\s*$", re.I),
]


def is_opt_out(text: str | None) -> str | None:
    """The phrase that asks us to stop, or None. Read on the reply's own words (the
    quoted original stripped), so our own opt-out line never trips it."""
    body = (text or "").strip()
    if not body:
        return None
    for line in opt_out_lines():
        body = body.replace(line, " ")
    for rx in _OPT_OUT:
        m = rx.search(body)
        if m:
            return m.group(0).strip()[:80]
    return None


def opt_out(session, email: Email, *, phrase: str, sender: str | None = None) -> dict:
    """Suppress the person and the company forever; withdraw every pending mail to that
    company. The caller commits."""
    reason = f"opted out {utcnow():%Y-%m-%d}: \"{phrase}\""[:200]
    contact = session.get(Contact, email.contact_id)
    company = session.get(Company, email.company_id)
    touched = []
    if contact is not None:
        contact.do_not_contact, contact.dnc_reason, contact.email_status = True, reason, "opted_out"
        session.add(contact)
        touched.append(contact.id)
    # a colleague who answered on the thread is suppressed too
    addr = (sender or "").split("<")[-1].strip(" >").lower()
    if addr and "@" in addr:
        for p in session.exec(select(Contact).where(col(Contact.email) == addr)).all():
            p.do_not_contact, p.dnc_reason, p.email_status = True, reason, "opted_out"
            session.add(p)
            touched.append(p.id)
    withdrawn = 0
    if company is not None:
        company.do_not_contact, company.dnc_reason = True, reason
        session.add(company)
        for e in session.exec(select(Email).where(Email.company_id == company.id,
                                                  col(Email.status).in_(["draft", "approved"]))).all():
            e.status, e.error = "rejected", f"withdrawn: {reason}"[:500]
            session.add(e)
            withdrawn += 1
    log.warning("opt-out on email %s (%s): %s", email.id, company.name if company else "?", phrase)
    return {"contacts": touched, "company_id": company.id if company else None, "withdrawn": withdrawn}


# ------------------------------------------------------------------ bounces

_ADDR = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def mark_bounced(session, email: Email, text: str | None = None) -> dict:
    """The address is dead: the email failed, the contact loses the address (kept as
    dead_email, never stored again), pending mail to it is withdrawn. The ledger slot it
    used is NOT given back — the mail left. The caller commits."""
    addr = (email.to_email or "").lower()
    email.status = "failed"
    email.error = f"bounced: {(text or '').strip()[:200]}"
    session.add(email)
    out = {"email_id": email.id, "address": addr, "contact_id": email.contact_id, "withdrawn": 0,
           "guessed": (email.address_confidence or "verified") != "verified"}
    contact = session.get(Contact, email.contact_id)
    if contact is not None and (contact.email or "").lower() in (addr, ""):
        contact.dead_email = addr or contact.dead_email
        contact.email = None
        contact.email_status = "bounced"
        contact.research_notes = f"{contact.research_notes or ''}\naddress bounced {utcnow():%Y-%m-%d}: {addr}".strip()[:2000]
        session.add(contact)
    from jobhunter import guardrails

    guardrails.reject(session, company_id=email.company_id, contact_id=email.contact_id, field="contact.email",
                      value=addr, reason=f"bounced ({email.address_confidence or 'unknown'} address)", channel="bounce")
    for e in session.exec(select(Email).where(col(Email.to_email) == email.to_email, Email.id != email.id,
                                              col(Email.status).in_(["draft", "approved"]))).all():
        e.status, e.error = "rejected", f"withdrawn: {addr} bounced"
        session.add(e)
        out["withdrawn"] += 1
    log.warning("bounce: %s (email %s)", addr, email.id)
    return out


def bounced_in_window(session, days: int = BRAKE_DAYS) -> int:
    since = utcnow() - timedelta(days=days)
    rows = session.exec(select(Email).where(Email.status == "failed", col(Email.error).like("bounced%"),
                                            col(Email.sent_at) >= since)).all()
    return len(rows)


def brake(session) -> str | None:
    """Why guessed addresses are paused now (too many bounces this week), or None."""
    n = bounced_in_window(session)
    if BRAKE_MAX and n >= BRAKE_MAX:
        return (f"{n} bounces in the last {BRAKE_DAYS} days — guessed addresses are paused until they age out "
                "(verified addresses still send)")
    return None


# ------------------------------------------------------------------ blocked as spam

#: 2026-10-23 (Sarvam): Gmail answered 9 of 24 mails into sarvam.ai with "** Message blocked **"
#: / 5.7.1 "Message rejected" (support.google.com/mail/answer/69585). That is not a dead
#: address, it is Google calling the mail spam: the whole recipient domain cools down, and
#: no cold mail leaves at all for a while.
SPAM_COOLDOWN_DAYS = int(_O.get("spam_block_cooldown_days", 21))
SPAM_PAUSE_HOURS = int(_O.get("spam_pause_hours", 48))
PER_DOMAIN_DAILY_CAP = int(_O.get("per_domain_daily_cap", 3))
_SPAM_BLOCK = re.compile(r"message blocked|message rejected|has been blocked|\b5\.7\.(?:1|26|28)\b|"
                         r"\bspam\b|unsolicited|policy reasons", re.I)


def is_spam_block(text: str | None) -> bool:
    """A bounce that says the mail was refused as spam / by policy (not "address not found")."""
    return bool(text and _SPAM_BLOCK.search(text))


def _domain(addr: str | None) -> str:
    return (addr or "").rsplit("@", 1)[-1].strip().lower() if "@" in (addr or "") else ""


def spam_blocks(session, *, hours: float) -> list[Email]:
    since = utcnow() - timedelta(hours=hours)
    rows = session.exec(select(Email).where(Email.status == "failed", col(Email.error).like("bounced%"),
                                            col(Email.sent_at) >= since)).all()
    return [e for e in rows if is_spam_block(e.error)]


def spam_hold(session, email: Email) -> str | None:
    """Why this cold mail must wait because Gmail refused earlier mail as spam, or None:
    any block in the last SPAM_PAUSE_HOURS pauses every cold mail (the account is being
    watched); a block at this company or this recipient domain in the last
    SPAM_COOLDOWN_DAYS holds every ask there. Only ever holds; nothing is rejected."""
    if email.kind != "cold":
        return None
    recent = spam_blocks(session, hours=SPAM_PAUSE_HOURS) if SPAM_PAUSE_HOURS else []
    if recent:
        last = max(recent, key=lambda e: e.sent_at)
        return (f"Gmail refused a mail as spam on {last.sent_at:%d %b %H:%M} UTC ({last.to_email}) — cold mail "
                f"pauses for {SPAM_PAUSE_HOURS}h after a block (outreach.spam_pause_hours)")
    if not SPAM_COOLDOWN_DAYS:
        return None
    dom = _domain(email.to_email)
    for e in spam_blocks(session, hours=24 * SPAM_COOLDOWN_DAYS):
        if e.id != email.id and (e.company_id == email.company_id or (dom and _domain(e.to_email) == dom)):
            return (f"Gmail refused mail to {e.to_email} as spam on {e.sent_at:%d %b} — {dom or 'this company'} "
                    f"cools down for {SPAM_COOLDOWN_DAYS} days (outreach.spam_block_cooldown_days)")
    return None


def domain_cap_for(session, company_id: int | None, day: str) -> int:
    """Cold mails a day into one domain: PER_DOMAIN_DAILY_CAP, or FOCUS_PER_COMPANY when
    that is higher and the company is one of the day's targets."""
    if company_id and FOCUS_PER_COMPANY > PER_DOMAIN_DAILY_CAP and company_id in focus_companies(session, day):
        return FOCUS_PER_COMPANY
    return PER_DOMAIN_DAILY_CAP


def domain_cap(session, email: Email) -> str | None:
    """At most PER_DOMAIN_DAILY_CAP cold mails a day into one recipient domain: a company's
    mail server (sarvam.ai is Google Workspace) sees every inbox, and a dozen near-identical
    asks from one stranger in a day is the cluster it files as bulk."""
    if email.kind != "cold" or not PER_DOMAIN_DAILY_CAP:
        return None
    dom = _domain(email.to_email)
    if not dom or dom in FREEMAIL:
        return None       # gmail.com is a thousand people, not one company's mail server
    from datetime import datetime, timezone

    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight.astimezone(timezone.utc).replace(tzinfo=None)
    rows = session.exec(select(Email.to_email).where(Email.kind == "cold", col(Email.sent_at) >= start,
                                                     col(Email.status).in_(["sent", "replied", "failed", "sending"]),
                                                     Email.id != email.id)).all()
    n = sum(1 for a in rows if _domain(a) == dom)
    cap = domain_cap_for(session, email.company_id, midnight.date().isoformat())
    if n >= cap:
        return (f"{n} cold mails into {dom} already today — at most {cap} a day per domain "
                "(outreach.per_domain_daily_cap); it goes on a later day")
    return None


def dead_addresses(session, company_id: int | None = None) -> set[str]:
    stmt = select(Contact.dead_email).where(col(Contact.dead_email).is_not(None))
    if company_id is not None:
        stmt = stmt.where(Contact.company_id == company_id)
    return {(a or "").lower() for a in session.exec(stmt).all() if a}


__all__ = ["blocked", "config_block", "cooldown", "spam_hold", "domain_cap", "domain_cap_for",
           "focus_companies", "focus_ids", "circle_ids", "is_spam_block", "spam_blocks", "with_opt_out", "has_opt_out", "opt_out_lines", "is_opt_out", "opt_out", "mark_bounced",
           "brake", "bounced_in_window", "dead_addresses", "OPT_OUT_LINE"]
