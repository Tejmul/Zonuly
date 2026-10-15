"""Warm paths, similar companies and a graph referrer signal — the graph doing outreach work.

Tejmul, 2026-10-16: "I don't really see the essence of the knowledge graph here." Until
this module, nothing in outreach read the graph: it was a mirror plus a context store.
This is the part that answers the questions a referral ask actually turns on
(system docs/KG-USES.md has the research and the ranking of uses):

    "Why this person?"          profile:me -AFFILIATED_WITH-> org <-AFFILIATED_WITH- contact
                                 (same school / a place you worked, read from their own text)
    "Who knows this company?"   contact(elsewhere) -PREVIOUSLY_AT-> company   (its alumni)
    "Is this the same person?"  contact -SAME_PERSON- contact   (one LinkedIn / GitHub login
                                 seen at two companies — entity resolution, kept as an edge)
    "Who's near me?"            company -IN_CITY-> city, and the city on your resume
    "Who else is like X?"       company -FUNDED_BY-> investor <-FUNDED_BY- company
                                 (Adamic–Adar: a rare seed fund counts more than a mega fund)
                                 + the skills their jobs REQUIRE (idf-weighted overlap)

Two halves:
  * sync-time: `sync_warm()` derives the four new edge kinds from rows already read by
    `sync.sync_all()`. Pure regex + dict work (≈0.3 s for 28k contacts) and it writes only
    the edges that changed, so the fast sync stays fast.
  * read-time: `warm_paths()`, `contact_paths()`, `similar_companies()`,
    `next_companies()`, `referrer_signal()` — read-only over kg_edge + the Contact rows.

Nothing here is invented (guarantee:no-fabrication). Every path carries `evidence`: the
quoted words from the person's own profile / headline / repo it was read from, and
`source`, the field or URL. A shared school is stated as "their profile mentions
Newton School" — never "they are an alumnus" — because a headline that says
"Ex-Founder's Office @ Newton School" is staff, not a classmate.

Layering: reads db, guardrails, config/profile.json and the kg store. No network, no
model. outreach/researcher.py reaches *up* into this module through one lazy, guarded
import — the only exception to the layering rule, recorded in KG-USES.md.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Iterable

from jobhunter import CONFIG, ROOT
from jobhunter.kg.store import Graph, slugify

PROFILE_ID = "profile:me"

# New edge kinds this module owns. Each one is rebuilt wholesale by sync_warm (and only
# its changed rows written), so a contact whose headline changes loses a stale edge.
WARM_RELS = ("AFFILIATED_WITH", "PREVIOUSLY_AT", "SAME_PERSON", "IN_CITY")

# Path weights — how much an opening line is worth, not a probability. The ordering
# follows the research (KG-USES.md §2): a shared institution the person *wrote down
# themselves* is the strongest cold-to-warm bridge a student has; a former colleague of
# a company you're already talking to comes next; shared stack is a talking point.
WEIGHT = {
    "shared_school": 1.0,
    "shared_employer": 0.9,
    "same_person_engaged": 0.55,
    "previously_at_engaged": 0.5,
    "shared_stack": 0.35,
    "alumni_elsewhere": 0.35,
    "investor_engaged": 0.3,
    "same_city": 0.2,
    "same_person": 0.15,
    "previously_at": 0.1,
}

# Skills everybody lists — a contact mentioning Python says nothing about overlap with you.
GENERIC_SKILLS = {
    "javascript", "python", "sql", "typescript", "git", "github", "react", "node.js", "aws", "docker",
    "machine learning", "deep learning", "devops", "llm", "nosql", "mysql", "postgresql", "s3", "lambda",
    "data structures and algorithms", "computer networks", "dbms", "seo/sem marketing", "random forest",
    "vercel", "next.js", "express js", "railway", "temporal", "genai", "generative ai", "github actions",
}

# Bots and catch-all logins that commit to every org — never a person, never "the same person".
_BOT_HANDLE = re.compile(r"(\[bot\]|-bot$|^bot-|bot$|^actions-user$|^invalid-email-address$|^cursoragent$|"
                         r"^web-flow$|^ghost$|^github-actions|^dependabot|^renovate|^copilot|^claude$|^devin-ai)", re.I)
# One identity seen at more than this many companies is an OSS drive-by contributor, not a mover.
SAME_PERSON_MAX_COMPANIES = 3

# "Ex-Stripe", "Previously @stripe, Index", "prev: Ramp, MongoDB", "Former Eng @ Notion & Slack"
_PREV = re.compile(r"(?:\bex[-. ]?|\bformer(?:ly)?\b|\bprev(?:iously)?\b\.?:?|\bpast\b:?|\balum(?:ni|nus)?\b)"
                   r"\s*(?:[\w ]{0,25}?\s)?(?:@|at\s+)?([^|;•\n()]{2,90})", re.I)
_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b|@|·)\s*")


# ------------------------------------------------------------------ your background

def _profile() -> dict:
    path = ROOT / CONFIG["profile_path"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _alias_patterns(name: str) -> list[str]:
    """Aliases worth matching for one place, longest first. "Newton School of Technology"
    is also written "Newton School" and "newtonschool"; "TheSmartBridge" is "SmartBridge"."""
    base = re.sub(r"\s*\(.*?\)|\s+(LLC|Inc\.?|Ltd\.?|Pvt\.?|Private Limited)$", "", name or "").strip()
    out = {base}
    words = base.split()
    if len(words) >= 3 and words[-2].lower() == "of":          # "X School of Technology" -> "X School"
        out.add(" ".join(words[:-2]))
    if base.lower().startswith("the") and len(base) > 6 and base[3:4].isupper():
        out.add(base[3:])                                        # TheSmartBridge -> SmartBridge
    if len(words) == 2 and words[1].lower() in ("university", "college", "institute") and len(words[0]) >= 6:
        out.add(words[0])                                        # "Rishihood University" -> "Rishihood"
    out |= {a.replace(" ", "") for a in list(out) if " " in a}   # newtonschool
    # a single short word ("Newton", "Labs") is too ambiguous to match on its own
    return sorted((a for a in out if len(a) >= 5 and (" " in a or len(a) >= 6)), key=len, reverse=True)


def background(profile: dict | None = None) -> dict:
    """Your side of every warm path: the places you can honestly share with someone
    (school, past employers), your city if the resume names one, and your specific skills."""
    p = profile if profile is not None else _profile()
    orgs: list[dict] = []
    for edu in p.get("education") or []:
        for i, part in enumerate((edu.get("institution") or "").split(",")):
            part = part.strip()
            if part:
                orgs.append({"id": f"org:{slugify(part)}", "label": part, "as": "school",
                             "aliases": _alias_patterns(part), "years": edu.get("years")})
    for e in p.get("experience") or []:
        name = re.sub(r"\s*\(.*?\)", "", e.get("company") or "").strip()
        label = re.sub(r"\s+(LLC|Inc\.?|Ltd\.?)$", "", name)
        if label:
            orgs.append({"id": f"org:{slugify(label)}", "label": label, "as": "employer",
                         "aliases": _alias_patterns(name), "title": e.get("title")})
    for o in orgs:
        o["regex"] = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(a) for a in o["aliases"]) + r")(?![a-z0-9])",
                                re.I) if o["aliases"] else None
    city = None
    text = p.get("_resume_text") or ""
    m = re.search(r"\b(Bengaluru|Bangalore|Hyderabad|Pune|Mumbai|Delhi|Chennai|Gurugram|Gurgaon|Noida)\b", text)
    if m:
        line = next((ln.strip() for ln in text.splitlines() if m.group(1) in ln), m.group(1))
        city = {"city": "Bengaluru" if m.group(1) == "Bangalore" else m.group(1), "evidence": line[:140]}
    skills = []
    for leaf in _leaves(p.get("skills")):
        if leaf and leaf.lower() not in GENERIC_SKILLS:
            skills.append(leaf)
    return {"orgs": [o for o in orgs if o["regex"]], "city": city, "skills": sorted(set(skills))}


def _leaves(v) -> list[str]:
    if isinstance(v, str):
        return [v.strip()]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _leaves(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _leaves(x)]
    return []


def _skill_regex(skills: Iterable[str]) -> re.Pattern | None:
    """Short all-caps skills (RAG, LoRA) must match case-exactly — "rag" in "drag" or a
    lower-case word is not the technique."""
    parts = []
    for s in sorted(set(skills), key=len, reverse=True):
        esc = re.escape(s)
        parts.append(f"(?-i:{esc})" if len(s) <= 4 else esc)
    if not parts:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(parts) + r")(?![A-Za-z0-9])", re.I)


# ------------------------------------------------------------------ evidence helpers

def _contact_texts(c) -> list[tuple[str, str]]:
    """(field, text) a contact wrote or a source said about them — the only places a
    warm path may be read from."""
    out = []
    for field in ("role", "role_evidence", "affiliation_evidence", "employer_claim", "research_notes"):
        v = getattr(c, field, None) if not isinstance(c, dict) else c.get(field)
        if v:
            out.append((field, str(v)))
    return out


def _quote(text: str, start: int, end: int, pad: int = 60) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    q = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a else "") + q + ("…" if b < len(text) else "")


def _norm_linkedin(url: str | None) -> str | None:
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "", re.I)
    return m.group(1).lower().rstrip("/") if m else None


def _norm_github(c) -> str | None:
    h = (getattr(c, "handle", None) or "").strip() or (getattr(c, "github", None) or "").rstrip("/").split("/")[-1]
    h = (h or "").strip().lower()
    if not h or _BOT_HANDLE.search(h):
        return None
    return h


# ------------------------------------------------------------------ sync-time edges

def derive_affiliations(contacts, orgs: list[dict]) -> dict[tuple[str, str], dict]:
    """contact -AFFILIATED_WITH-> org, when the person's own text names a place in your
    background. The quote is kept on the edge — it is what the email may cite."""
    out: dict[tuple[str, str], dict] = {}
    if not orgs:
        return out
    for c in contacts:
        for field, text in _contact_texts(c):
            for o in orgs:
                m = o["regex"].search(text)
                if m and (f"contact:{c.id}", o["id"]) not in out:
                    out[(f"contact:{c.id}", o["id"])] = {"as": o["as"], "field": field, "match": m.group(1),
                                                         "quote": _quote(text, m.start(), m.end())}
    for o in orgs:
        out[(PROFILE_ID, o["id"])] = {"as": o["as"], "field": "profile.json", "match": o["label"],
                                     "quote": o.get("title") or o.get("years") or o["label"]}
    return out


def derive_previously_at(contacts, companies) -> dict[tuple[str, str], dict]:
    """contact -PREVIOUSLY_AT-> company, from "ex-X / previously @X / prev: X" in their own
    headline, where X is a company already in the graph. Names under 4 characters and
    the contact's current employer are skipped."""
    names: dict[str, int] = {}
    for co in companies:
        if (getattr(co, "status", None) or "") in ("merged", "quarantined"):
            continue
        k = (co.name or "").strip().lower()
        if len(k) >= 4:
            names.setdefault(k, co.id)
    out: dict[tuple[str, str], dict] = {}
    for c in contacts:
        role = c.role or ""
        for m in _PREV.finditer(role):
            for tok in _SPLIT.split(m.group(1)):
                k = tok.strip().strip(".:-–—!").lower()
                cid = names.get(k)
                if cid and cid != c.company_id:
                    out.setdefault((f"contact:{c.id}", f"company:{cid}"),
                                   {"field": "role", "quote": _quote(role, m.start(), m.end(), 30)})
    return out


def derive_same_person(contacts) -> dict[tuple[str, str], dict]:
    """contact -SAME_PERSON- contact across companies: one LinkedIn slug or one GitHub
    login seen at 2–3 companies. Deterministic keys only (research: Splink-style
    blocking is for ambiguous pairs; we keep the exact ones). Bots and logins seen at
    more than three companies are drive-by OSS contributors, not people who moved."""
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for c in contacts:
        li = _norm_linkedin(c.linkedin)
        if li:
            groups[("linkedin", li)].append(c)
        gh = _norm_github(c)
        if gh:
            groups[("github", gh)].append(c)
    out: dict[tuple[str, str], dict] = {}
    for (via, key), rows in groups.items():
        comps = {r.company_id for r in rows}
        if not 2 <= len(comps) <= SAME_PERSON_MAX_COMPANIES:
            continue
        rows = sorted(rows, key=lambda r: r.id)
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if a.company_id != b.company_id:
                    out.setdefault((f"contact:{a.id}", f"contact:{b.id}"), {"via": via, "key": key})
    return out


def _city_names(raw: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"\s*[·,/]\s*", raw or "") if x.strip()]


def derive_cities(companies) -> dict[tuple[str, str], dict]:
    out = {}
    for co in companies:
        for city in _city_names(getattr(co, "city", None)):
            out[(f"company:{co.id}", f"city:{slugify(city)}")] = {"city": city}
    return out


def replace_rel(g: Graph, rel: str, desired: dict[tuple[str, str], dict]) -> dict:
    """Make the `rel` edges exactly `desired`, writing only what changed. A no-op sync
    writes nothing — the property the 10-min → 6-s sync fix depends on."""
    existing = {(r[0], r[1]): r[2] for r in g.conn.execute("SELECT src, dst, props FROM kg_edge WHERE rel = ?", (rel,))}
    want = {k: json.dumps(v, default=str, sort_keys=True) for k, v in desired.items()}
    stale = [(s, rel, d) for (s, d) in existing if (s, d) not in want]
    fresh = [(s, rel, d, p) for (s, d), p in want.items() if existing.get((s, d)) != p]
    if stale:
        g.conn.executemany("DELETE FROM kg_edge WHERE src = ? AND rel = ? AND dst = ?", stale)
    if fresh:
        from jobhunter.kg.store import now_iso
        ts = now_iso()
        g.conn.executemany(
            "INSERT INTO kg_edge (src, rel, dst, props, layer, created_at) VALUES (?, ?, ?, ?, 'data', ?) "
            "ON CONFLICT(src, rel, dst) DO UPDATE SET props = excluded.props, layer = excluded.layer",
            [(s, r, d, p, ts) for s, r, d, p in fresh],
        )
    return {"edges": len(want), "added_or_changed": len(fresh), "removed": len(stale)}


def sync_warm(g: Graph, companies, contacts, *, profile: dict | None = None) -> dict:
    """Derive and write the warm-path edges. Called by sync.sync_all after contacts."""
    bg = background(profile)
    for o in bg["orgs"]:
        g.upsert_node(o["id"], "org", o["label"], summary=f"your {o['as']}",
                      props={"as": o["as"], "aliases": o["aliases"]}, layer="data",
                      body=" ".join([o["label"], *o["aliases"]]))
    g.prune("org", {o["id"] for o in bg["orgs"]})
    cities = derive_cities(companies)
    for city in {v["city"] for v in cities.values()}:
        g.upsert_node(f"city:{slugify(city)}", "city", city, props={}, layer="data", body=city)
    g.prune("city", {d for (_, d) in cities})
    # contacts whose node was pruned would leave dangling edges; only keep live ids
    live = {f"contact:{c.id}" for c in contacts} | {f"company:{c.id}" for c in companies} | {PROFILE_ID}
    live |= {o["id"] for o in bg["orgs"]}
    out = {}
    for rel, desired in (
        ("AFFILIATED_WITH", derive_affiliations(contacts, bg["orgs"])),
        ("PREVIOUSLY_AT", derive_previously_at(contacts, companies)),
        ("SAME_PERSON", derive_same_person(contacts)),
        ("IN_CITY", cities),
    ):
        desired = {k: v for k, v in desired.items() if k[0] in live and (k[1] in live or rel == "IN_CITY")}
        out[rel] = replace_rel(g, rel, desired)
    _CACHE.clear()
    return out


# ------------------------------------------------------------------ read side

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_S = 300


def _cached(key: str, build):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
    val = build()
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)
    return val


def _graph(g: Graph | None) -> tuple[Graph, bool]:
    return (g, False) if g is not None else (Graph(), True)


def _session():
    from jobhunter.db import get_session
    return get_session()


def _company_facts(conn) -> dict[int, dict]:
    """Per-company scalars the recommender ranks on, read once per TTL."""
    def build():
        rows = conn.execute(
            "SELECT id, name, city, hq_region, funding_stage, tier, status, overrated, do_not_contact, "
            "description, pay_power, best_selection_score FROM company").fetchall()
        return {r[0]: {"id": r[0], "name": r[1], "cities": _city_names(r[2]), "region": r[3], "stage": r[4],
                       "tier": r[5], "status": r[6], "overrated": r[7], "dnc": r[8], "description": r[9],
                       "pay_power": r[10], "best_selection": r[11]} for r in rows}
    return _cached("facts", build)


def _engaged(conn) -> dict[int, str]:
    """company id -> how you're already engaged there: 'emailed' (a draft or a send) or
    'ready referrer' (someone confirmed current with an address)."""
    def build():
        out: dict[int, str] = {}
        for (cid,) in conn.execute("SELECT DISTINCT company_id FROM contact WHERE email IS NOT NULL "
                                   "AND affiliation = 'verified-current' AND COALESCE(do_not_contact, 0) = 0"):
            out[cid] = "a ready referrer"
        for (cid,) in conn.execute("SELECT DISTINCT company_id FROM email WHERE status != 'rejected'"):
            out[cid] = "you've written to"
        return out
    return _cached("engaged", build)


def _investors(conn) -> tuple[dict[str, set[str]], dict[str, int], dict[str, str]]:
    def build():
        by_co: dict[str, set[str]] = defaultdict(set)
        for s, d in conn.execute("SELECT src, dst FROM kg_edge WHERE rel = 'FUNDED_BY'"):
            by_co[s].add(d)
        deg = Counter(i for inv in by_co.values() for i in inv)
        labels = {r[0]: r[1] for r in conn.execute("SELECT id, label FROM kg_node WHERE kind = 'investor'")}
        return dict(by_co), dict(deg), labels
    return _cached("investors", build)


def _skills(conn) -> tuple[dict[str, set[str]], dict[str, float], dict[str, str]]:
    """company -> the profile skills its jobs ask for, and each skill's idf over companies."""
    def build():
        by_co: dict[str, set[str]] = defaultdict(set)
        for co, sk in conn.execute(
                "SELECT p.dst, r.dst FROM kg_edge p JOIN kg_edge r ON r.src = p.src AND r.rel = 'REQUIRES' "
                "WHERE p.rel = 'POSTED_BY'"):
            by_co[co].add(sk)
        n = max(1, len(by_co))
        df = Counter(s for ss in by_co.values() for s in ss)
        idf = {s: math.log((n + 1) / (c + 1)) + 0.1 for s, c in df.items()}
        labels = {r[0]: r[1] for r in conn.execute("SELECT id, label FROM kg_node WHERE kind = 'skill'")}
        return dict(by_co), idf, labels
    return _cached("skills", build)


def _eligible(f: dict | None) -> bool:
    """A company worth recommending: not merged/quarantined/under the pay bar, not
    overrated, not do-not-contact, not a reject."""
    return bool(f) and (f["status"] or "") not in ("merged", "quarantined", "below_pay_bar") \
        and not f["overrated"] and not f["dnc"] and (f["tier"] or "") != "reject"


# ---- people

def _person_paths(c, *, bg: dict, aff: dict, prev: list, same: list, facts: dict, engaged: dict,
                  skill_re: re.Pattern | None) -> list[dict]:
    """Every warm path from you to one contact, strongest first, each with its evidence."""
    paths: list[dict] = []
    orgs = {o["id"]: o for o in bg["orgs"]}
    for org_id, props in aff:
        o = orgs.get(org_id)
        if not o:
            continue
        school = o["as"] == "school"
        paths.append({
            "type": "shared_school" if school else "shared_employer",
            "weight": WEIGHT["shared_school" if school else "shared_employer"],
            "fact": f"Their {props.get('field', 'profile').replace('_', ' ')} mentions {o['label']} — "
                    + ("your school." if school else "where you worked."),
            "opener": (f"I noticed {o['label']} on your profile — I'm a student there."
                       if school else f"I noticed {o['label']} on your profile — I worked there too."),
            "evidence": props.get("quote"), "source": props.get("field"), "via": org_id,
        })
    for dst, props in prev:
        cid = int(dst.split(":")[1])
        f = facts.get(cid) or {}
        how = engaged.get(cid)
        paths.append({
            "type": "previously_at_engaged" if how else "previously_at",
            "weight": WEIGHT["previously_at_engaged" if how else "previously_at"],
            "fact": f"Used to work at {f.get('name', dst)}" + (f", where you have {how}." if how == "a ready referrer"
                                                                 else ", which you've written to." if how else "."),
            "evidence": props.get("quote"), "source": "role", "via": dst,
        })
    for other_id, props, other_company in same:
        f = facts.get(other_company) or {}
        how = engaged.get(other_company)
        paths.append({
            "type": "same_person_engaged" if how else "same_person",
            "weight": WEIGHT["same_person_engaged" if how else "same_person"],
            "fact": f"The same {props.get('via')} login ({props.get('key')}) is also on file at {f.get('name', other_company)}"
                    + (f" — {how}." if how else "."),
            "evidence": f"{props.get('via')}: {props.get('key')}", "source": props.get("via"), "via": other_id,
        })
    if skill_re is not None:
        hits: dict[str, tuple[str, str]] = {}
        for field, text in _contact_texts(c):
            if field in ("affiliation_evidence", "employer_claim"):
                continue
            for m in skill_re.finditer(text):
                hits.setdefault(m.group(1).lower(), (m.group(1), _quote(text, m.start(), m.end(), 40)))
        if hits:
            names = [v[0] for v in hits.values()][:4]
            paths.append({
                "type": "shared_stack", "weight": WEIGHT["shared_stack"] + 0.05 * min(3, len(hits) - 1),
                "fact": f"Works with {', '.join(names)} — on your resume too.",
                "evidence": next(iter(hits.values()))[1], "source": "their profile / repos", "via": None,
            })
    paths.sort(key=lambda p: -p["weight"])
    return paths


def referrer_signal(c, paths: list[dict], *, evidence_rels: int = 0) -> dict:
    """A graph-only signal for ordering referrers (0–100) and its reasons. It does not
    replace referral_rank (contacts/roles.py) — it is a second opinion the UI shows and
    the drafter can sort by: confirmed-current people with more independent evidence and
    a real connection to your background come first."""
    from jobhunter import guardrails

    score, why = 0, []
    try:
        ready = guardrails.is_ready(c)
    except Exception:  # noqa: BLE001 — a dict in tests, or a half-loaded row
        ready = False
    aff = getattr(c, "affiliation", None)
    if ready:
        score += 25
        why.append("confirmed current at the company")
    if getattr(c, "email", None) and ready:
        score += 5
        why.append("has an address")
    if aff == "verified-current":
        score += 10
        why.append("employment verified")
    elif aff == "likely-current":
        score += 5
    if evidence_rels:
        score += min(20, 4 * evidence_rels)
        why.append(f"{evidence_rels} independent evidence link(s) in the graph")
    warm = sum(p["weight"] for p in paths)
    if warm:
        score += min(40, round(40 * warm))
        why.append(paths[0]["fact"])
    return {"score": min(100, score), "why": why, "warm": round(warm, 2)}


def _evidence_rels(conn, cid: str) -> int:
    """Distinct kinds of evidence edge on a contact — found via, classified as, affiliated,
    previously at, same person. One source saying it twice counts once."""
    rels = {r[0] for r in conn.execute("SELECT rel FROM kg_edge WHERE src = ? UNION SELECT rel FROM kg_edge WHERE dst = ?",
                                       (cid, cid))}
    rels.discard("WORKS_AT")
    rels.discard("SENT_TO")
    n = len(rels)
    n += max(0, conn.execute("SELECT COUNT(*) FROM kg_edge WHERE src = ? AND rel = 'FOUND_VIA'", (cid,)).fetchone()[0] - 1)
    return n


def _people_paths(g: Graph, contacts: list, *, bg: dict | None = None) -> list[dict]:
    conn = g.conn
    bg = bg or background()
    facts, engaged = _company_facts(conn), _engaged(conn)
    skill_re = _skill_regex(bg["skills"])
    ids = [f"contact:{c.id}" for c in contacts]
    aff, prev, same = defaultdict(list), defaultdict(list), defaultdict(list)
    if ids:
        q = ",".join("?" * len(ids))
        for s, rel, d, p in conn.execute(
                f"SELECT src, rel, dst, props FROM kg_edge WHERE rel IN ('AFFILIATED_WITH','PREVIOUSLY_AT','SAME_PERSON') "
                f"AND (src IN ({q}) OR (rel = 'SAME_PERSON' AND dst IN ({q})))", ids + ids):
            props = json.loads(p or "{}")
            if rel == "AFFILIATED_WITH":
                aff[s].append((d, props))
            elif rel == "PREVIOUSLY_AT":
                prev[s].append((d, props))
            else:
                me, other = (s, d) if s in ids else (d, s)
                row = conn.execute("SELECT dst FROM kg_edge WHERE src = ? AND rel = 'WORKS_AT'", (other,)).fetchone()
                oc = int(row[0].split(":")[1]) if row else None
                same[me].append((other, props, oc))
    out = []
    for c in contacts:
        nid = f"contact:{c.id}"
        paths = _person_paths(c, bg=bg, aff=aff[nid], prev=prev[nid], same=same[nid], facts=facts,
                              engaged=engaged, skill_re=skill_re)
        sig = referrer_signal(c, paths, evidence_rels=_evidence_rels(conn, nid))
        out.append({"contact_id": c.id, "name": c.name, "role": c.role, "role_class": c.role_class,
                    "affiliation": c.affiliation, "has_email": bool(c.email), "linkedin": c.linkedin,
                    "github": c.github, "graph_signal": sig["score"], "signal_why": sig["why"], "paths": paths})
    return out


def contact_paths(contact_id: int, *, g: Graph | None = None) -> dict:
    """Warm paths + graph signal for one person. {} fields when nothing connects."""
    from jobhunter.db import Contact
    with _session() as s:
        c = s.get(Contact, contact_id)
        if c is None:
            return {"error": f"no contact {contact_id}"}
        s.expunge(c)
    g, own = _graph(g)
    try:
        rows = _people_paths(g, [c])
    finally:
        if own:
            g.conn.close()
    return rows[0]


def warm_paths(company_id: int, *, g: Graph | None = None, people: int = 12) -> dict:
    """Every warm path into one company: the people (ranked by graph signal, only those
    with at least one path, plus the top writable ones), and company-level paths —
    alumni of the company now elsewhere, investors shared with companies you're already
    engaged with, and your city."""
    from sqlmodel import select

    from jobhunter.db import Company, Contact
    with _session() as s:
        co = s.get(Company, company_id)
        if co is None:
            return {"error": f"no company {company_id}"}
        name, city = co.name, co.city
        contacts = s.exec(select(Contact).where(Contact.company_id == company_id)).all()
        for c in contacts:
            s.expunge(c)
    g, own = _graph(g)
    try:
        conn = g.conn
        bg = background()
        rows = [r for r in _people_paths(g, contacts, bg=bg) if not (r["affiliation"] == "generic")]
        rows.sort(key=lambda r: (-sum(p["weight"] for p in r["paths"]), -r["graph_signal"]))
        warm_people = [r for r in rows if r["paths"]]

        company_paths: list[dict] = []
        facts, engaged = _company_facts(conn), _engaged(conn)
        # alumni: people elsewhere whose own headline says they used to work here
        for s_, p in conn.execute("SELECT src, props FROM kg_edge WHERE rel = 'PREVIOUSLY_AT' AND dst = ?",
                                  (f"company:{company_id}",)):
            props = json.loads(p or "{}")
            node = conn.execute("SELECT label FROM kg_node WHERE id = ?", (s_,)).fetchone()
            at = conn.execute("SELECT dst FROM kg_edge WHERE src = ? AND rel = 'WORKS_AT'", (s_,)).fetchone()
            now = facts.get(int(at[0].split(":")[1])) if at else None
            company_paths.append({
                "type": "alumni_elsewhere", "weight": WEIGHT["alumni_elsewhere"],
                "fact": f"{node[0] if node else s_} used to work here — now at {now['name'] if now else 'another company'}. "
                        "Can tell you who to ask.",
                "evidence": props.get("quote"), "source": "their headline", "via": s_,
                "contact_id": int(s_.split(":")[1]),
            })
        # investors shared with companies you are already engaged with
        inv_by, deg, inv_label = _investors(conn)
        mine = inv_by.get(f"company:{company_id}", set())
        if mine:
            shared: dict[str, list[str]] = defaultdict(list)
            for other, invs in inv_by.items():
                oid = int(other.split(":")[1])
                if oid == company_id or oid not in engaged:
                    continue
                for i in invs & mine:
                    shared[i].append(other)
            for i, others in sorted(shared.items(), key=lambda kv: deg.get(kv[0], 99)):
                names = [facts.get(int(o.split(":")[1]), {}).get("name", o) for o in others[:3]]
                company_paths.append({
                    "type": "investor_engaged", "weight": WEIGHT["investor_engaged"],
                    "fact": f"{inv_label.get(i, i)} also backs {', '.join(names)}"
                            + (f" (+{len(others) - 3} more)" if len(others) > 3 else "")
                            + (" — companies you've written to." if engaged[int(others[0].split(':')[1])] == "you've written to"
                               else " — where you have a ready referrer."),
                    "evidence": f"FUNDED_BY {inv_label.get(i, i)} — backs {deg.get(i, 0)} companies in the graph",
                    "source": "funding_investors", "via": i,
                })
        if bg["city"] and bg["city"]["city"] in _city_names(city):
            company_paths.append({
                "type": "same_city", "weight": WEIGHT["same_city"],
                "fact": f"They're in {bg['city']['city']} — a city on your resume.",
                "evidence": bg["city"]["evidence"], "source": "resume", "via": f"city:{slugify(bg['city']['city'])}",
            })
        company_paths.sort(key=lambda p: -p["weight"])
        top_ready = [r for r in rows if not r["paths"] and r["has_email"]][: max(0, people - len(warm_people))]
    finally:
        if own:
            g.conn.close()
    return {
        "company_id": company_id, "company": name,
        "people": warm_people[:people], "also_writable": top_ready[:5],
        "company_paths": company_paths[:12],
        "background": [{"id": o["id"], "label": o["label"], "as": o["as"]} for o in bg["orgs"]],
        "summary": _summary(warm_people, company_paths),
    }


def _summary(people: list[dict], company_paths: list[dict]) -> str:
    if not people and not company_paths:
        return "No warm path yet — nobody here shares your school, employers or stack, and no shared investor."
    bits = []
    kinds = Counter(p["type"] for r in people for p in r["paths"][:1])
    if kinds.get("shared_school"):
        bits.append(f"{kinds['shared_school']} person(s) whose profile names your school")
    if kinds.get("shared_employer"):
        bits.append(f"{kinds['shared_employer']} who mention a place you worked")
    rest = len(people) - kinds.get("shared_school", 0) - kinds.get("shared_employer", 0)
    if rest:
        bits.append(f"{rest} with a weaker link (stack, past employer, same login)")
    if company_paths:
        bits.append(f"{len(company_paths)} company-level path(s)")
    return "; ".join(bits) + "."


# ---- companies

def similar_companies(company_id: int, *, g: Graph | None = None, limit: int = 8) -> dict:
    """"If you liked X": companies close to X in the graph, each with the reasons.

        score = 2.0 · Σ_shared investors 1/log(2 + degree)      Adamic–Adar
              + 1.5 · idf-weighted Jaccard of the skills their jobs ask for (≥ 3 shared)
              + 0.4 same city · 0.25 same stage · 0.15 same region (no city) · 0.1 same tier

    A candidate needs a shared investor or ≥ 3 shared skills — city and stage alone are
    not similarity, they are a filter."""
    g, own = _graph(g)
    try:
        conn = g.conn
        facts = _company_facts(conn)
        me = facts.get(company_id)
        if me is None:
            return {"error": f"no company {company_id}"}
        rows = _similar(conn, {company_id}, facts)
    finally:
        if own:
            g.conn.close()
    return {"company_id": company_id, "company": me["name"], "similar": rows[:limit]}


def _similar(conn, seeds: set[int], facts: dict, *, exclude: set[int] = frozenset()) -> list[dict]:
    inv_by, deg, inv_label = _investors(conn)
    sk_by, idf, sk_label = _skills(conn)
    scores: dict[int, dict] = {}

    def row(oid: int) -> dict:
        return scores.setdefault(oid, {"inv": defaultdict(float), "skills": set(), "j": 0.0, "seeds": set()})

    for seed in seeds:
        sid = f"company:{seed}"
        my_inv = inv_by.get(sid, set())
        for other, invs in inv_by.items():
            shared = invs & my_inv
            if not shared:
                continue
            oid = int(other.split(":")[1])
            if oid in seeds:
                continue
            r = row(oid)
            r["seeds"].add(seed)
            for i in shared:
                r["inv"][i] = max(r["inv"][i], 1 / math.log(2 + deg.get(i, 1)))
        my_sk = sk_by.get(sid, set())
        if len(my_sk) >= 3:
            for other, sks in sk_by.items():
                shared = sks & my_sk
                if len(shared) < 3:
                    continue
                oid = int(other.split(":")[1])
                if oid in seeds:
                    continue
                union = sks | my_sk
                j = sum(idf.get(s, 0) for s in shared) / max(1e-9, sum(idf.get(s, 0) for s in union))
                if j < 0.25:
                    continue
                r = row(oid)
                r["seeds"].add(seed)
                if j > r["j"]:
                    r["j"], r["skills"] = j, shared
    out = []
    seed_facts = [facts[s] for s in seeds if s in facts]
    for oid, r in scores.items():
        f = facts.get(oid)
        if oid in exclude or not _eligible(f):
            continue
        inv_score = sum(r["inv"].values())
        why, score = [], 2.0 * inv_score + 1.5 * r["j"]
        if r["inv"]:
            names = [inv_label.get(i, i) for i in sorted(r["inv"], key=lambda i: deg.get(i, 99))]
            why.append(f"shared investor{'s' if len(names) > 1 else ''}: {', '.join(names[:3])}"
                       + f" ({'a small portfolio' if min(deg.get(i, 99) for i in r['inv']) <= 3 else 'a large fund'})")
        if r["skills"]:
            ranked = sorted(r["skills"], key=lambda s: -idf.get(s, 0))
            specific = [s for s in ranked if sk_label.get(s, s).lower() not in GENERIC_SKILLS]
            top = (specific or ranked)[:4]
            why.append("hiring for the same skills: " + ", ".join(sk_label.get(s, s) for s in top))
        if any(set(f["cities"]) & set(s["cities"]) for s in seed_facts):
            score += 0.4
            why.append("same city: " + ", ".join(sorted(set(f["cities"]) & {c for s in seed_facts for c in s["cities"]})))
        elif f["region"] and any(f["region"] == s["region"] for s in seed_facts):
            score += 0.15
        if f["stage"] and any((f["stage"] or "").lower() == (s["stage"] or "").lower() for s in seed_facts):
            score += 0.25
            why.append(f"same stage: {f['stage']}")
        if f["tier"] in ("tier1", "tier2") and any(f["tier"] == s["tier"] for s in seed_facts):
            score += 0.1
        out.append({"company_id": oid, "name": f["name"], "description": f["description"], "tier": f["tier"],
                    "city": " · ".join(f["cities"]) or None, "stage": f["stage"], "pay_power": f["pay_power"],
                    "score": round(score, 3), "why": why,
                    "like": [facts[s]["name"] for s in sorted(r["seeds"]) if s in facts][:3]})
    out.sort(key=lambda r: (-r["score"], -(r["pay_power"] or 0)))
    return out


def next_companies(*, g: Graph | None = None, seeds: list[int] | None = None, limit: int = 12) -> dict:
    """The next companies to work on: similar to the ones you are already engaged with
    (written to, or holding a ready referrer), excluding those. Personalised
    neighbourhood aggregation — the cheap cousin of personalised PageRank."""
    g, own = _graph(g)
    try:
        conn = g.conn
        facts = _company_facts(conn)
        engaged = _engaged(conn)
        if seeds:
            seed_set = set(seeds)
        else:
            written = {c for c, how in engaged.items() if how == "you've written to"}
            seed_set = written or set(sorted(engaged, key=lambda c: -(facts.get(c, {}).get("best_selection") or 0))[:25])
        rows = _similar(conn, seed_set, facts, exclude=set(engaged))
    finally:
        if own:
            g.conn.close()
    return {"seeds": [facts[s]["name"] for s in seed_set if s in facts][:25], "companies": rows[:limit]}


def brief_section(contact_id: int) -> list[dict]:
    """The `warm_paths` block for the researcher's brief: only the evidence-carrying
    fields, strongest first. [] when nothing connects (the brief then has no section)."""
    out = contact_paths(contact_id)
    if out.get("error"):
        return []
    return [{k: p.get(k) for k in ("type", "fact", "opener", "evidence", "source")} for p in out["paths"][:4]]
