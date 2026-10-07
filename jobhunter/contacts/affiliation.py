"""Does this person still work there? — GitHub evidence for a contact's affiliation.

Commit mining filed 17,000 people as "verified" because they once committed to a repo
under the company's org. A commit is not employment: forks carry upstream committers
(Affirm's Flink fork), contractors commit and leave, and an address from 2019 says
nothing about 2026. So for each company with a GitHub org this reads, once a month:

    public members   GET /orgs/{org}/public_members — membership they chose to show
    recent commits   the org's own (non-fork, non-archived) repos pushed in the last
                     12 months, commits since then: who, with which address, when
    profiles         GET /users/{login} for the people we would write to — the display
                     name (a handle is not a name) and the "company" field in their words

and relabels every GitHub-found contact through guardrails.affiliation: org member or
profile naming the company → verified-current; a commit inside 12 months →
likely-current (verified-current with a company address); anything else → unconfirmed.

Authenticated (GITHUB_TOKEN, 5,000/h). The budget is read off every response and the
pass stops while `reserve` calls are left for the hourly people hunt.

Layering: db + guardrails + contacts.github_miner (headers only). Synchronous httpx.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import httpx
from sqlmodel import col, select

from jobhunter import channels, guardrails
from jobhunter.contacts import github_miner
from jobhunter.db import Company, Contact, get_session, init_db, utcnow

log = logging.getLogger(__name__)

API = github_miner.API
MARK = "[affil]"
RECHECK_DAYS = 30
REPOS = 6
_LOGIN = re.compile(r"github\.com/([A-Za-z0-9][A-Za-z0-9\-]{0,38})/?$")


class Budget:
    """GitHub's core budget, read off each response. `ok()` is False once only the
    reserve is left (or the API said 403/429)."""

    def __init__(self, reserve: int = 800):
        self.reserve = reserve
        self.remaining: int | None = None
        self.reset_at: int | None = None
        self.calls = 0
        self.stopped: str | None = None

    def ok(self) -> bool:
        return not self.stopped and (self.remaining is None or self.remaining > self.reserve)


def _get(client: httpx.Client, budget: Budget, url: str) -> object | None:
    if not budget.ok():
        return None
    try:
        r = client.get(url, headers=github_miner.headers(), timeout=30)
    except httpx.HTTPError as e:
        channels.record("github", False, f"{type(e).__name__}: {str(e)[:120]}")
        return None
    budget.calls += 1
    try:
        budget.remaining = int(r.headers.get("x-ratelimit-remaining", budget.remaining or 0))
        budget.reset_at = int(r.headers.get("x-ratelimit-reset", 0)) or budget.reset_at
    except ValueError:
        pass
    if r.status_code in (401, 403, 429):
        budget.stopped = f"GitHub {r.status_code}: {r.text[:120]}"
        channels.record("github", False, budget.stopped)
        return None
    if r.status_code >= 500:
        channels.record("github", False, f"HTTP {r.status_code}")
        return None
    channels.record("github", True)
    if r.status_code >= 400:
        return None          # 404: no such org / user — an answer, not a failure
    try:
        return r.json()
    except ValueError:
        return None


def _when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def org_evidence(client: httpx.Client, budget: Budget, org: str) -> dict:
    """{members: {login}, recent: {login: (when, repo)}, emails: {email: (when, repo)}, repos: [..]}"""
    since = (utcnow() - timedelta(days=guardrails.COMMIT_RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    members: set[str] = set()
    for page in (1, 2, 3):
        data = _get(client, budget, f"{API}/orgs/{org}/public_members?per_page=100&page={page}")
        if not isinstance(data, list) or not data:
            break
        members |= {(m.get("login") or "").lower() for m in data if m.get("login")}
        if len(data) < 100:
            break
    repos = _get(client, budget, f"{API}/orgs/{org}/repos?type=public&sort=pushed&per_page=30")
    if not isinstance(repos, list):    # a user account, not an org
        repos = _get(client, budget, f"{API}/users/{org}/repos?type=owner&sort=pushed&per_page=30")
    own = [r for r in (repos or []) if isinstance(r, dict) and not r.get("fork") and not r.get("archived")
           and (_when(r.get("pushed_at")) or datetime.min) >= utcnow() - timedelta(days=guardrails.COMMIT_RECENT_DAYS)]
    recent: dict[str, tuple[datetime, str]] = {}
    emails: dict[str, tuple[datetime, str]] = {}
    read = []
    for r in own[:REPOS]:
        commits = _get(client, budget, f"{API}/repos/{org}/{r['name']}/commits?since={since}&per_page=100")
        if not isinstance(commits, list):
            continue
        read.append(r["name"])
        for c in commits:
            meta = ((c.get("commit") or {}).get("author") or {})
            when = _when(meta.get("date"))
            if not when:
                continue
            login = ((c.get("author") or {}).get("login") or "").lower()
            em = (meta.get("email") or "").lower()
            repo = f"{org}/{r['name']}"
            if login and (login not in recent or recent[login][0] < when):
                recent[login] = (when, repo)
            if em and (em not in emails or emails[em][0] < when):
                emails[em] = (when, repo)
    return {"members": members, "recent": recent, "emails": emails, "repos": read,
            "forks_skipped": sum(1 for r in (repos or []) if isinstance(r, dict) and r.get("fork"))}


def login_of(contact: Contact) -> str | None:
    if contact.handle:
        return contact.handle
    m = _LOGIN.search((contact.github or "").rstrip("/"))
    return m.group(1) if m else None


def check_company(company_id: int, client: httpx.Client, budget: Budget, *, max_profiles: int = 30,
                  dry_run: bool = False) -> dict:
    """Relabel one company's GitHub contacts from org evidence + profiles."""
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None or not c.github_org:
            return {"company_id": company_id, "skipped": "no github org"}
        org, name, domain = c.github_org, c.name, c.domain
        people = session.exec(select(Contact).where(Contact.company_id == company_id,
                                                    col(Contact.github).is_not(None))).all()
        rows = [(p.id, login_of(p)) for p in people]
    ev = org_evidence(client, budget, org)
    if budget.stopped or (not ev["repos"] and not ev["members"] and not budget.ok()):
        return {"company": name, "stopped": budget.stopped or "budget reserve reached"}
    stats = {"company": name, "org": org, "people": len(rows), "members": 0, "recent": 0, "profiles": 0,
             "names_resolved": 0, "labels": {}, "repos": len(ev["repos"]), "forks_skipped": ev["forks_skipped"]}
    with get_session() as session:
        c = session.get(Company, company_id)
        # people we would write to first: an address, a referring role
        order = sorted(session.exec(select(Contact).where(col(Contact.id).in_([i for i, _ in rows]))).all(),
                       key=lambda p: (p.email is None, p.referral_rank or 9, p.id))
        profiles = 0
        for p in order:
            login = login_of(p)
            if not login:
                continue
            lo = login.lower()
            member = lo in ev["members"]
            when, repo = ev["recent"].get(lo, (None, None))
            if p.email and p.email.lower() in ev["emails"]:
                w2, r2 = ev["emails"][p.email.lower()]
                if not when or w2 > when:
                    when, repo = w2, r2
            claim, profile_email = p.employer_claim, False
            need_name = not guardrails.person_name(p.name)
            if (need_name or not (member or claim)) and profiles < max_profiles and budget.ok():
                u = _get(client, budget, f"{API}/users/{login}")
                profiles += 1
                if isinstance(u, dict):
                    claim = (u.get("company") or "").strip() or claim
                    nm = guardrails.person_name(u.get("name"))
                    if need_name and nm:
                        if not dry_run:
                            p.name, p.name_source = nm.value, "github-profile"
                        stats["names_resolved"] += 1
                    pe = (u.get("email") or "").lower()
                    profile_email = bool(pe and p.email and pe == p.email.lower())
            if need_name and not guardrails.person_name(p.name) and not dry_run and p.name:
                guardrails.reject(session, company_id=company_id, contact_id=p.id, field="contact.name",
                                  value=p.name, reason="a handle, not a name (no display name on GitHub)",
                                  source=f"https://github.com/{login}", channel="github")
                p.name = None
            em = guardrails.email(p.email, domain=domain) if p.email else None
            kind = em.value[1] if em else None
            aff = guardrails.affiliation(company_name=name, domain=domain, email_kind=kind, email_addr=p.email,
                                         org_member=member, employer_claim=claim, last_commit_at=when,
                                         commit_repo=repo)
            stats["members"] += member
            stats["recent"] += bool(when)
            stats["labels"][aff.label] = stats["labels"].get(aff.label, 0) + 1
            if dry_run:
                continue
            p.handle = login
            p.employer_claim = claim
            p.email_kind = kind
            p.last_commit_at = when
            p.affiliation, p.affiliation_source, p.affiliation_evidence = aff.label, aff.source, aff.evidence[:300]
            p.affiliation_checked_at = utcnow()
            if p.email and p.source == "github":
                # the address tier: a commit address is real while it is in use (≤ 12 months)
                p.confidence = guardrails.address_confidence(source="github", email_kind=kind, last_commit_at=when,
                                                             profile_email=profile_email)
            if kind == "generic":
                p.role_class, p.referral_rank = "other", 9
            session.add(p)
        stats["profiles"] = profiles
        if not dry_run:
            today = utcnow().strftime("%Y-%m-%d")
            kept = [ln for ln in (c.notes or "").splitlines() if not ln.startswith(MARK)]
            c.notes = "\n".join(kept + [f"{MARK} {today}: {stats['members']} public members, "
                                        f"{stats['recent']} recent committers of {len(rows)}"]).strip()
            session.add(c)
            session.commit()
    return stats


def _checked_at(notes: str | None) -> datetime | None:
    m = re.search(r"\[affil\] (\d{4}-\d{2}-\d{2})", notes or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None


def pending(limit: int | None = None) -> list[int]:
    """Companies with a GitHub org and GitHub contacts not checked in RECHECK_DAYS —
    in db.fill_order: the ★ list first, then the least-complete high-priority rows."""
    from jobhunter.db import fill_order

    init_db()
    cutoff = utcnow() - timedelta(days=RECHECK_DAYS)
    with get_session() as session:
        with_gh = set(session.exec(select(Contact.company_id).where(col(Contact.github).is_not(None)).distinct()).all())
        rows = [c for c in session.exec(select(Company).where(col(Company.status).is_(None),
                                                              col(Company.github_org).is_not(None))).all()
                if c.id in with_gh and (_checked_at(c.notes) or datetime.min) < cutoff]
        rows.sort(key=lambda c: (*fill_order(c), c.id))
        ids = [c.id for c in rows]
    return ids[:limit] if limit else ids


def run(limit: int | None = None, *, reserve: int = 800, max_profiles: int = 30, dry_run: bool = False,
        company_ids: list[int] | None = None) -> dict:
    """The pass over `pending()` until the budget reserve. Each company commits alone."""
    ids = company_ids or pending(limit)
    budget = Budget(reserve)
    out = {"companies": 0, "people": 0, "members": 0, "recent": 0, "names_resolved": 0, "profiles": 0,
           "labels": {}, "stopped": None, "pending": len(ids)}
    with httpx.Client(follow_redirects=True) as client:
        for cid in ids:
            if not budget.ok():
                out["stopped"] = budget.stopped or f"budget reserve ({reserve}) reached"
                break
            try:
                s = check_company(cid, client, budget, max_profiles=max_profiles, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001 — one company must not end the pass
                log.warning("affiliation: company %s failed: %s", cid, e)
                continue
            if s.get("stopped"):
                out["stopped"] = s["stopped"]
                break
            if s.get("skipped"):
                continue
            out["companies"] += 1
            for k in ("people", "members", "recent", "names_resolved", "profiles"):
                out[k] += s.get(k, 0)
            for k, v in (s.get("labels") or {}).items():
                out["labels"][k] = out["labels"].get(k, 0) + v
    out["github_calls"] = budget.calls
    out["github_remaining"] = budget.remaining
    log.info("affiliation pass: %s", out)
    return out


__all__ = ["run", "pending", "check_company", "org_evidence", "Budget"]
