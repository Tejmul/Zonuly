"""Is this address real? check-if-email-exists (Reacher) decides, and only `safe` counts.

2026-10-30, Tejmul: "There should be a verified address … if I send the mail, the mail should
be delivered." Guessed addresses (sarvam.ai, kapture.cx) bounced, and the Gmail was blocked.
So contact.email holds an address only when db.EmailCheck says `safe` (db._only_verified_addresses
enforces it on every flush). A verdict is proved by one of:

    reacher    github.com/reacherhq/check-if-email-exists, the CLI binary in tools/reacher/
               (v0.11.9). `is_reachable == "safe"`: the mailbox server accepted the RCPT and
               the domain is not catch-all.
    apify      Apify's LinkedIn email search said deliverable on a non-catch-all domain, or
               the address is on the person's own LinkedIn profile (actors/apify_source.best_email)
    delivered  a cold mail to it went out over a day ago and never bounced

What this machine can and cannot prove (measured 2026-10-30, home IP, port 25 open):
    - Google (gmail.com and every Google Workspace company) never answers an SMTP probe from
      a residential IP, so those addresses come back `unknown` — unless a SOCKS5 proxy with a
      clean IP is configured (contacts.reacher.proxy_host / REACHER_PROXY_* in .env).
    - Zoho answers a REAL mailbox with "554 5.7.1 antispam policy" and Reacher reports that
      as `invalid`. So an `invalid` is re-read with one direct RCPT: only a 5.1.x
      "user unknown" stays invalid (and kills the address); a policy refusal is `unknown`.

Layering: db only (no outreach, no pipeline). Never sends mail.
"""

from __future__ import annotations

import json
import logging
import os
import random
import smtplib
import socket
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import lru_cache

from sqlmodel import col, select

from jobhunter import CONFIG, ROOT, secret
from jobhunter.db import Contact, EmailCheck, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_CONF = (CONFIG.get("contacts") or {}).get("reacher") or {}
BIN = ROOT / (_CONF.get("binary") or "tools/reacher/check_if_email_exists")
# never the sending domain (2026-10-30, Tejmul: the checks must not touch his mail reputation) —
# the tool's own defaults, so no probe carries tejmul.in or any of his inboxes
HELLO = _CONF.get("hello_name", "gmail.com")
FROM = _CONF.get("from_email", "reacher.email@gmail.com")
TIMEOUT = int(_CONF.get("timeout_seconds", 90))
FRESH_DAYS = int(_CONF.get("fresh_days", 30))          # a safe verdict older than this is re-checked before a send
WORKERS = int(_CONF.get("workers", 6))                  # domains probed at once; one address at a time per domain
DOMAIN_GAP = float(_CONF.get("domain_gap_seconds", 2))  # pause between two probes of one domain
GOOGLE_MX = ("google.com", "googlemail.com")
LANE = int(_CONF.get("lane_size", 150))                 # addresses per lane on one big domain

SAFE = "safe"


def _proxy() -> dict:
    host = _CONF.get("proxy_host") or secret("REACHER_PROXY_HOST") or os.environ.get("REACHER_PROXY_HOST")
    if not host:
        return {}
    return {"host": host,
            "port": str(_CONF.get("proxy_port") or secret("REACHER_PROXY_PORT") or "1080"),
            "user": _CONF.get("proxy_username") or secret("REACHER_PROXY_USERNAME") or "",
            "password": secret("REACHER_PROXY_PASSWORD") or ""}


def available() -> bool:
    return BIN.exists()


@lru_cache(maxsize=8192)
def mx_hosts(domain: str) -> tuple[str, ...]:
    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        return tuple(str(r.exchange).rstrip(".").lower() for r in sorted(answers, key=lambda r: r.preference))
    except Exception:  # noqa: BLE001 — NXDOMAIN, no MX, timeouts
        return ()


def checkable_here(domain: str) -> bool:
    """False for Google-hosted domains when no proxy is set: they never answer this IP."""
    if _proxy():
        return True
    hosts = mx_hosts(domain)
    return bool(hosts) and not any(h.endswith(g) for h in hosts[:1] for g in GOOGLE_MX)


def _run(address: str) -> dict:
    cmd = [str(BIN), "--hello-name", HELLO, "--from-email", FROM]
    p = _proxy()
    if p:
        cmd += ["--proxy-host", p["host"], "--proxy-port", p["port"]]
        if p["user"]:
            cmd += ["--proxy-username", p["user"], "--proxy-password", p["password"]]
    out = subprocess.run(cmd + [address], capture_output=True, text=True, timeout=TIMEOUT)
    return json.loads(out.stdout)


def _user_unknown(address: str, host: str) -> bool | None:
    """Re-read a Reacher `invalid`: True = the server said no such mailbox (5.1.x),
    False = it refused for another reason (policy, reputation), None = could not ask."""
    try:
        s = smtplib.SMTP(host, 25, timeout=20)
        s.ehlo(HELLO)
        s.mail(FROM)
        code, msg = s.rcpt(address)
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass
    except (OSError, smtplib.SMTPException, socket.timeout):
        return None
    text = (msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)).lower()
    if code in (550, 551, 553) and ("5.1." in text or "does not exist" in text or "unknown" in text
                                    or "no such" in text or "not found" in text):
        return True
    return False


def probe(address: str) -> tuple[str, bool | None, str]:
    """(verdict, catch_all, detail) straight from the tool, `invalid` re-read. No DB."""
    address = address.strip().lower()
    domain = address.rsplit("@", 1)[-1]
    if not mx_hosts(domain):
        return "invalid", None, "domain has no mail server (no MX)"
    try:
        d = _run(address)
    except subprocess.TimeoutExpired:
        return "unknown", None, f"no answer in {TIMEOUT}s"
    except (OSError, ValueError) as e:
        return "unknown", None, f"reacher failed: {type(e).__name__}"
    verdict = str(d.get("is_reachable") or "unknown")
    smtp = d.get("smtp") or {}
    catch_all = smtp.get("is_catch_all")
    detail = f"reacher: {verdict}"
    if smtp.get("error"):
        detail += f" ({(smtp['error'].get('message') if isinstance(smtp['error'], dict) else smtp['error'])})"[:200]
    if verdict == "invalid" and d.get("syntax", {}).get("is_valid_syntax", True) and (d.get("mx") or {}).get("accepts_mail"):
        hosts = mx_hosts(domain)
        unknown_user = _user_unknown(address, hosts[0]) if hosts else None
        if unknown_user is not True:
            verdict, detail = "unknown", "reacher said invalid but the server refused on policy, not 'no such user'"
    return verdict, catch_all, detail[:300]


def record(address: str, verdict: str, *, source: str = "reacher", catch_all: bool | None = None,
           detail: str | None = None) -> EmailCheck:
    """Write one verdict in its own committed transaction (so any session's flush sees it).
    A proof from Apify or a delivery is never downgraded by a later `unknown`."""
    address = address.strip().lower()
    with get_session() as s:
        row = s.get(EmailCheck, address)
        if row is not None and row.verdict == SAFE and verdict in ("unknown", "risky") and row.source != "reacher":
            return row
        row = row or EmailCheck(address=address, verdict=verdict)
        row.verdict, row.source, row.catch_all, row.detail, row.checked_at = verdict, source, catch_all, detail, utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


def vouch(address: str, source: str, detail: str | None = None) -> None:
    """A proof that did not come from Reacher (apify | delivered)."""
    if address and "@" in address:
        record(address, SAFE, source=source, detail=detail)


def verdict(address: str) -> EmailCheck | None:
    with get_session() as s:
        return s.get(EmailCheck, (address or "").strip().lower())


def check(address: str, *, max_age_days: int | None = None) -> EmailCheck:
    """The stored verdict when fresh enough, else a new probe."""
    max_age = FRESH_DAYS if max_age_days is None else max_age_days
    row = verdict(address)
    if row is not None and row.checked_at >= utcnow() - timedelta(days=max_age):
        return row
    if row is not None and row.verdict == SAFE and row.source != "reacher":
        return row   # an Apify proof or a delivery does not expire into an unknown
    v, ca, why = probe(address)
    return record(address, v, catch_all=ca, detail=why)


def sendable(address: str) -> tuple[bool, str]:
    """May a cold mail go to this address? Only with a fresh `safe` verdict."""
    row = check(address)
    if row.verdict == SAFE:
        return True, f"verified by {row.source}"
    return False, f"address not verified ({row.verdict}: {row.detail or ''})".strip()


# ------------------------------------------------------------------ the cleanup and the standing job

def promote(session, contact: Contact, address: str, source: str) -> None:
    """A proved address goes onto the lead (the flush hook lets it through: the row is safe)."""
    contact.email, contact.email_unverified, contact.email_verified_by = address, None, source
    if contact.email_status not in ("bounced", "opted_out"):
        contact.email_status = "real"
    contact.confidence = "verified"
    session.add(contact)


def _apply(session, contact: Contact, row: EmailCheck) -> str:
    addr = row.address
    if row.verdict == SAFE:
        promote(session, contact, addr, row.source)
        return "safe"
    if row.verdict == "invalid":
        contact.email_unverified = None
        contact.dead_email = contact.dead_email or addr
        if contact.email_status not in ("bounced", "opted_out"):
            contact.email_status = "fake:invalid"
        contact.research_notes = f"{contact.research_notes or ''}\n{addr}: no such mailbox ({row.detail})".strip()[:2000]
    elif contact.email_status not in ("bounced", "opted_out"):
        contact.email_status = f"fake:{row.verdict}"
    session.add(contact)
    return row.verdict


def check_many(addresses: list[str], *, workers: int | None = None, known: dict | None = None,
               progress=None) -> dict[str, EmailCheck]:
    """Probe many addresses, each verdict recorded in email_check as it lands. Domains run in
    parallel, one address at a time inside a domain; once a domain shows catch-all, the rest
    of its addresses are `risky` without a probe. Touches no lead."""
    by_domain: dict[str, list[str]] = defaultdict(list)
    for a in dict.fromkeys(x.strip().lower() for x in addresses if x and "@" in x):
        by_domain[a.rsplit("@", 1)[-1]].append(a)
    # a big shared provider (gmail.com: 8,684 addresses) is not catch-all and would be one
    # endless lane — split it so it runs alongside everything else
    lanes: dict[str, list[str]] = {}
    for d, addrs in by_domain.items():
        for i in range(0, len(addrs), LANE):
            lanes[f"{d}#{i}" if i else d] = addrs[i:i + LANE]
    by_domain = lanes
    known = known or {}
    results: dict[str, EmailCheck] = {}
    lock = threading.Lock()

    def run_domain(lane: str, addrs: list[str]) -> None:
        domain = lane.split("#")[0]
        catch_all = False
        for a in addrs:
            k = known.get(a)
            if k is not None and k.verdict == SAFE and k.source != "reacher":
                row = k
            elif catch_all:
                row = record(a, "risky", catch_all=True, detail="domain accepts every address (catch-all)")
            elif not mx_hosts(domain):
                row = record(a, "invalid", detail="domain has no mail server (no MX)")
            else:
                v, ca, why = probe(a)
                row = record(a, v, catch_all=ca, detail=why)
                catch_all = bool(ca)
                time.sleep(DOMAIN_GAP + random.random())
            with lock:
                results[a] = row
                if progress:
                    progress(len(results), a, row)

    with ThreadPoolExecutor(max_workers=workers or WORKERS) as pool:
        list(pool.map(lambda kv: run_domain(*kv), list(by_domain.items())))
    return results


def verify_pending(limit: int | None = None, *, workers: int | None = None, include_google: bool = False,
                   recheck_days: int = 7) -> dict:
    """Probe every lead's kept-but-unproved address (contact.email_unverified) and promote
    the safe ones. One address at a time per domain; a domain found catch-all is not probed
    again (every address on it is `risky`). Google-hosted domains are skipped while no proxy
    is configured — they would only come back `unknown`."""
    init_db()
    if not available():
        return {"error": f"check-if-email-exists not installed at {BIN}"}
    with get_session() as s:
        rows = s.exec(select(Contact.id, Contact.email_unverified)
                      .where(col(Contact.email_unverified).is_not(None))
                      .where(col(Contact.do_not_contact).is_not(True))
                      .order_by(col(Contact.referral_rank).asc().nulls_last(), Contact.id)).all()
        known = {r.address: r for r in s.exec(select(EmailCheck)).all()}
    cutoff = utcnow() - timedelta(days=recheck_days)
    by_domain: dict[str, list[str]] = defaultdict(list)
    owners: dict[str, list[int]] = defaultdict(list)
    out = {"addresses": 0, "skipped_google": 0, "skipped_recent": 0, "safe": 0, "risky": 0, "invalid": 0, "unknown": 0}
    for cid, addr in rows:
        addr = addr.strip().lower()
        owners[addr].append(cid)
        if len(owners[addr]) > 1:
            continue
        k = known.get(addr)
        if k is not None and k.checked_at >= cutoff and k.verdict != SAFE:
            out["skipped_recent"] += 1
            continue
        domain = addr.rsplit("@", 1)[-1]
        if not include_google and not checkable_here(domain):
            out["skipped_google"] += 1
            continue
        by_domain[domain].append(addr)
    queue = [a for addrs in by_domain.values() for a in addrs]
    results = check_many(queue[:limit] if limit else queue, workers=workers, known=known)

    with get_session() as s:
        for addr, row in results.items():
            out["addresses"] += 1
            out[row.verdict if row.verdict in out else "unknown"] += 1
            for cid in owners[addr]:
                c = s.get(Contact, cid)
                if c is not None and (c.email_unverified or "").lower() == addr:
                    _apply(s, c, row)
        s.commit()
    log.info("reacher verify_pending: %s", out)
    return out


def segregate() -> dict:
    """Every lead's address is sorted REAL or FAKE — internally only (2026-10-30, Tejmul:
    "segregate them into real or fake … only internally, in the UI it should not show like that").

    REAL   proved: email_check `safe` (Reacher), a cold mail delivered a day ago without a bounce,
           or Apify's verified LinkedIn address. Stays on contact.email, email_status "real".
    FAKE   everything else: guessed, dead (no MX / no such user), a shared inbox, no person
           behind it, or no proof. Moved to contact.email_unverified (kept, never erased),
           email_status "fake:<why>". The lead stays; the dashboard just shows no address.
    Mail still waiting to go to a FAKE address is withdrawn. Re-run after more checks land:
    a newly proved address is promoted."""
    init_db()
    from jobhunter.db import Email

    day_ago = utcnow() - timedelta(days=1)
    out = {"real": 0, "fake": 0, "promoted": 0, "withdrawn": 0}
    why: dict[str, int] = defaultdict(int)
    with get_session() as s:
        bounced = {(e.to_email or "").lower() for e in s.exec(select(Email).where(Email.status == "failed")).all()}
        for e in s.exec(select(Email).where(col(Email.status).in_(("sent", "replied")))).all():
            a = (e.to_email or "").lower()
            if a and a not in bounced and e.sent_at and e.sent_at < day_ago:
                vouch(a, "delivered", detail=f"email {e.id} delivered {e.sent_at:%Y-%m-%d}, no bounce")
        for c in s.exec(select(Contact).where(Contact.source == "linkedin-public", Contact.confidence == "verified",
                                              col(Contact.email).is_not(None))).all():
            if "email by pattern" not in (c.research_notes or ""):
                vouch(c.email, "apify", detail="Apify LinkedIn email search: deliverable, not catch-all")
        checks = {k.address: k for k in s.exec(select(EmailCheck)).all()}
        people = s.exec(select(Contact).where(
            (col(Contact.email).is_not(None) & (Contact.email != "")) | col(Contact.email_unverified).is_not(None))).all()
        for c in people:
            addr = (c.email or c.email_unverified or "").strip().lower()
            k = checks.get(addr)
            if k is not None and k.verdict == SAFE:
                if not c.email:
                    out["promoted"] += 1
                promote(s, c, addr, k.source)
                out["real"] += 1
                continue
            if k is not None and k.verdict == "invalid":
                reason = "dead"
            elif c.confidence == "pattern-guessed" or c.source == "pattern" or "email by pattern" in (c.research_notes or ""):
                reason = "guessed"
            elif c.email_kind == "generic":
                reason = "inbox"
            elif not (c.name or c.handle or c.github or c.linkedin):
                reason = "no-person"
            else:
                reason = "not-proved"
            c.email, c.email_unverified, c.email_verified_by = None, addr, None
            if c.email_status not in ("bounced", "opted_out"):
                c.email_status = f"fake:{reason}"
            s.add(c)
            out["fake"] += 1
            why[reason] += 1
        s.commit()
        real = {a for a, k in checks.items() if k.verdict == SAFE}
        real |= {k.address for k in s.exec(select(EmailCheck).where(EmailCheck.verdict == SAFE)).all()}
        for e in s.exec(select(Email).where(col(Email.status).in_(("draft", "approved", "incomplete")))).all():
            if (e.to_email or "").lower() not in real:
                e.status, e.error = "rejected", "withdrawn: address not proved real (check-if-email-exists)"
                s.add(e)
                out["withdrawn"] += 1
        s.commit()
    out["fake_by_reason"] = dict(why)
    log.info("segregate: %s", out)
    return out


__all__ = ["segregate", "probe", "check", "sendable", "record", "vouch", "verdict", "promote", "verify_pending",
           "checkable_here", "available", "SAFE"]
