"""Read-only audit of every address on a lead: which are real, which were made up, which are
duplicates, and which people still need their real address found.

2026-10-30, Tejmul: guessed addresses were mailed and the Gmail was blocked. "First, run an
audit. Take the real mails, separate the duplicate mails, and find the real mails of the
people which we have to search." This script changes nothing — it opens the database
read-only and writes CSVs + a summary to data/audit/.

Every lead with an address gets exactly one bucket, first match wins:

    REAL — proved
      delivered        a cold mail went to it over a day ago and did not bounce
      apify-verified   Apify's LinkedIn email search: SMTP-deliverable, not catch-all
    DEAD — proved not to take mail from us
      bounced          "address not found" came back
      blocked          the mail was refused ("message blocked")
    FAKE — made up, never seen anywhere
      guessed          built from a name + a pattern (pattern-guessed / "email by pattern")
    SUSPECT — seen somewhere, but not this person's
      name-mismatch    a page/LinkedIn address whose mailbox name shares nothing with the person
      no-person        an address with no name, profile or handle behind it
      generic          a role inbox (careers@, info@, press@) — not a person
    UNPROVED — the person published it, never checked
      github-recent    their commit / profile address, used inside 12 months
      github-old       a commit address older than that
      page             on a page, mailbox name matches the person

Usage: python scripts/email_audit.py   (→ data/audit/<date>/)
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobhunter.db"
READY = ("verified-current", "likely-current")
GROUPS = {
    "delivered": "REAL", "apify-verified": "REAL",
    "bounced": "DEAD", "blocked": "DEAD",
    "guessed": "FAKE",
    "name-mismatch": "SUSPECT", "no-person": "SUSPECT", "generic": "SUSPECT",
    "github-recent": "UNPROVED", "github-old": "UNPROVED", "page": "UNPROVED",
}


def tokens(name: str | None) -> list[str]:
    if not name:
        return []
    a = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z]+", a) if len(t) >= 2]


def name_matches(name: str | None, addr: str) -> bool:
    local = re.sub(r"[^a-z]", "", addr.split("@")[0].lower())
    toks = tokens(name)
    if not toks or not local:
        return False
    if any(len(t) >= 3 and t in local for t in toks):
        return True
    initials = "".join(t[0] for t in toks)
    return local in (initials, initials[::-1]) or (len(toks) > 1 and local in (toks[0][0] + toks[-1], toks[-1] + toks[0][0]))


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_ago = (now - timedelta(days=1)).isoformat(" ")
    year_ago = (now - timedelta(days=365)).isoformat(" ")

    # what the mailboxes told us, per address
    outcome: dict[str, str] = {}
    for r in con.execute("select lower(to_email) a, status, error, sent_at from email where kind in ('cold','followup') "
                         "and to_email is not null order by id"):
        err = (r["error"] or "").lower()
        if r["status"] == "failed" and ("address not found" in err or "5.1.1" in err):
            outcome[r["a"]] = "bounced"
        elif r["status"] == "failed" and "blocked" in err:
            outcome.setdefault(r["a"], "blocked")
        elif r["status"] in ("sent", "replied") and (r["sent_at"] or "") < day_ago and r["a"] not in outcome:
            outcome[r["a"]] = "delivered"
    replied = {r[0] for r in con.execute("select lower(e.to_email) from reply r join email e on e.id=r.email_id")}

    rows = con.execute("""
        select c.id, c.company_id, co.name company, co.domain, c.name, c.role, lower(trim(c.email)) email,
               c.source, c.confidence, c.email_kind, c.affiliation, c.linkedin, c.github, c.x_handle, c.handle,
               c.last_commit_at, c.name_source, c.referral_rank, c.role_class, c.do_not_contact,
               c.research_notes, co.priority_score, co.list_key
        from contact c join company co on co.id = c.company_id
        where c.email is not null and trim(c.email) <> ''""").fetchall()

    holders: dict[str, list] = defaultdict(list)
    for r in rows:
        holders[r["email"]].append(r)

    out_rows = []
    for r in rows:
        addr, notes = r["email"], (r["research_notes"] or "")
        if addr in replied:
            b = "delivered"
        elif addr in outcome:
            b = outcome[addr]
        elif r["source"] == "linkedin-public" and r["confidence"] == "verified":
            b = "apify-verified"
        elif r["confidence"] == "pattern-guessed" or r["source"] == "pattern" or "email by pattern" in notes:
            b = "guessed"
        elif r["email_kind"] == "generic":
            b = "generic"
        elif not (r["name"] or r["handle"] or r["github"] or r["linkedin"]):
            b = "no-person"
        elif r["source"] == "github":
            b = "github-recent" if (r["confidence"] == "verified" or (r["last_commit_at"] or "") >= year_ago) else "github-old"
        elif not name_matches(r["name"], addr):
            b = "name-mismatch"
        else:
            b = "page"
        dup = len(holders[addr])
        companies = len({h["company_id"] for h in holders[addr]})
        out_rows.append({
            "group": GROUPS[b], "bucket": b, "contact_id": r["id"], "company": r["company"], "domain": r["domain"],
            "name": r["name"], "role": r["role"], "email": addr, "source": r["source"], "confidence": r["confidence"],
            "email_kind": r["email_kind"], "affiliation": r["affiliation"],
            "referrer": "yes" if (r["affiliation"] in READY and r["email_kind"] != "generic" and not r["do_not_contact"]
                                 and b != "no-person") else "",
            "linkedin": r["linkedin"], "x_handle": r["x_handle"], "github": r["github"],
            "held_by": dup, "held_at_companies": companies, "on_list": r["list_key"] or "",
            "priority": r["priority_score"] or 0, "referral_rank": r["referral_rank"],
        })

    out_dir = ROOT / "data" / "audit" / now.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(out_rows[0].keys())

    def write(name: str, data: list[dict]) -> None:
        with open(out_dir / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data)

    order = lambda d: (d["on_list"] == "", -(d["priority"] or 0), d["referral_rank"] or 99, d["company"] or "")
    write("all-addresses.csv", sorted(out_rows, key=order))
    write("real.csv", sorted([d for d in out_rows if d["group"] == "REAL"], key=order))
    dups = sorted([d for d in out_rows if d["held_by"] > 1], key=lambda d: (d["email"], d["company"] or ""))
    write("duplicates.csv", dups)
    # the people to search: someone who can refer (works there, not an inbox) without a proved address
    search = [d for d in out_rows if d["referrer"] and d["group"] != "REAL"]
    write("to-search.csv", sorted(search, key=order))

    # leads who can refer and hold no address at all are also people to search
    no_addr = con.execute(f"""select count(*) from contact where (email is null or trim(email)='')
        and affiliation in {READY} and coalesce(email_kind,'') <> 'generic' and coalesce(do_not_contact,0)=0""").fetchone()[0]
    pending = con.execute("""select e.status, lower(e.to_email) a from email e
        where e.kind='cold' and e.status in ('draft','approved','incomplete')""").fetchall()
    pend = Counter(GROUPS.get(next((d["bucket"] for d in out_rows if d["email"] == p["a"]), ""), "not on a lead")
                   for p in pending)

    by_bucket = Counter(d["bucket"] for d in out_rows)
    by_group = Counter(d["group"] for d in out_rows)
    ref_group = Counter(d["group"] for d in out_rows if d["referrer"])
    distinct = len(holders)
    dup_addrs = sum(1 for a, h in holders.items() if len(h) > 1)
    dup_rows = sum(len(h) - 1 for h in holders.values() if len(h) > 1)
    cross = sum(1 for h in holders.values() if len({x["company_id"] for x in h}) > 1)
    with_li = sum(1 for d in search if d["linkedin"])
    with_x = sum(1 for d in search if d["x_handle"])
    companies_to_search = len({d["company"] for d in search})

    lines = [f"# Address audit — {now:%Y-%m-%d %H:%M} UTC (read-only)", "",
             f"Leads with an address: {len(out_rows)} rows, {distinct} distinct addresses.", "",
             "| group | bucket | leads | of them referrers |", "|---|---|---:|---:|"]
    for b in GROUPS:
        lines.append(f"| {GROUPS[b]} | {b} | {by_bucket.get(b, 0)} | "
                     f"{sum(1 for d in out_rows if d['bucket'] == b and d['referrer'])} |")
    lines += ["", "| group | leads | referrers |", "|---|---:|---:|"]
    for g in ("REAL", "DEAD", "FAKE", "SUSPECT", "UNPROVED"):
        lines.append(f"| {g} | {by_group.get(g, 0)} | {ref_group.get(g, 0)} |")
    lines += ["", "## Duplicates",
              f"- {dup_addrs} addresses are held by more than one lead ({dup_rows} extra rows); "
              f"{cross} of them sit at more than one company.", "",
              "## People to search (referrers without a proved address)",
              f"- {len(search)} referrers hold an unproved / fake / dead address, at {companies_to_search} companies; "
              f"{with_li} have a LinkedIn URL, {with_x} an X handle.",
              f"- {no_addr} more referrers hold no address at all.", "",
              "## Mail waiting to go out (draft / approved / incomplete)",
              *[f"- {k}: {v}" for k, v in pend.most_common()], "",
              f"Files: {out_dir.relative_to(ROOT)}/ — all-addresses.csv, real.csv, duplicates.csv, to-search.csv"]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
