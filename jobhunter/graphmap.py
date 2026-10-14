"""The graph map — the knowledge graph pre-shaped for the dashboard's /graph page (2026-10-14).

Tejmul: "Why can't I see the knowledge graph? Show it in the UI, like ChokePoints … I want
to directly see which companies are still pending — what I still need to scrape."

The graph is ~37,000 data nodes; no browser should be handed that. This module hands it
three small, already-shaped views, every one of them the same three tiers so the canvas
draws them with one renderer (CHOKEPOINTS §13.3):

    hubs    the chain, left to right   market: the industry layers (segments.LAYERS)
                                       pipeline: Discovered → … → Complete → Drafted/Sent
                                       project: the context layer's stages
    subs    clusters inside a hub      market: segments; pipeline / project: none
    leaves  the things themselves      market + pipeline: every active company
                                       project: features, gaps, decisions

and `links` between them. A leaf is PENDING when it is not complete (a company still
being filled; a feature not built; a gap). Every hub and sub carries `pending` and
`star_pending` (pending ★ India companies under it) — the roll-up the map rings so a
pending ★ company deep inside a cluster is visible at overview zoom.

Nothing here is inferred: `missing` is guardrails.complete_bar's own list
(company.complete_missing) and each part's reason is the fill pass's (company.fill_state)
or the rolling re-check's (company.recheck_failed). A part with no recorded reason says
"not looked at yet" only when the fill pass has never run on that company.

Cached per tick: the payload is rebuilt only when the signature (company count, complete
count, last fill, last tick, kg size) changes — or after CACHE_TTL_S as a backstop.

Layering: reads db, segments, guardrails and the kg store. No network, no model, no writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter, defaultdict

from sqlmodel import col, func, select

from jobhunter import guardrails
from jobhunter import segments as seg
from jobhunter.db import DB_PATH, Company, Email, Job, Setting, get_session, init_db

KG_PATH = DB_PATH            # tests point this at their own file
CACHE_TTL_S = 600
VIEWS = ("market", "pipeline", "project")
INDIA_CITIES = ("Bengaluru", "Hyderabad")

PARTS: list[dict] = [
    {"key": "website", "label": "Website"},
    {"key": "description", "label": "Description"},
    {"key": "hiring", "label": "Hiring proof"},
    {"key": "referrer_email", "label": "Referrer with email"},
]

PIPELINE: list[dict] = [
    {"key": "discovered", "label": "Discovered", "blurb": "We know the name. Nothing below it is proven yet."},
    {"key": "described", "label": "Described", "blurb": "A website and a description that passes the guardrails."},
    {"key": "hiring", "label": "Hiring proven", "blurb": "Their own careers page or ATS shows an open role."},
    {"key": "referrer", "label": "Referrer found", "blurb": "Someone the evidence places there now."},
    # "email ready" and "complete" are one gate: the complete bar IS website + description +
    # hiring + a ready referrer with an address, so a separate column would always be empty
    {"key": "complete", "label": "Email ready · complete", "blurb": "That person has an address we can use — "
     "every part a referral ask needs is here, ready to draft."},
    {"key": "outreach", "label": "Drafted / sent", "blurb": "An ask is drafted, approved or sent."},
]

PROJECT_PARTS = {"gap": "Gap — would be built", "partial": "Partly built", "open": "Open — not started"}

_DRAFTED = ("draft", "incomplete", "approved", "sent", "replied")


# ------------------------------------------------------------------ per-company facts

def _obj(raw: str | None) -> dict:
    try:
        v = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _list(raw: str | None) -> list:
    try:
        v = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def active(c: Company) -> bool:
    """The companies the dashboard counts (network.filling's rule): a ★-list row unless it
    is under the pay bar; otherwise active, not overrated, not rejected."""
    if c.list_key:
        return c.status != "below_pay_bar"
    return c.status is None and not c.overrated and (c.tier or "unknown") != "reject"


def market(c: Company, *, anywhere_roles: int = 0) -> str:
    """india (Bengaluru / Hyderabad or India HQ) | remote (remote HQ or a remote-anywhere
    posting) | abroad (everything else)."""
    if c.city in INDIA_CITIES or c.hq_region == "india" or c.india_found_at is not None:
        return "india"
    if c.hq_region == "remote" or anywhere_roles > 0:
        return "remote"
    return "abroad"


def missing_parts(c: Company) -> list[str]:
    """guardrails.complete_bar's list, in SHOWN_PARTS order. A row the bar has never
    judged (complete is None and no list) is treated as missing everything it lacks."""
    if c.complete:
        return []
    got = [p for p in _list(c.complete_missing) if p in guardrails.SHOWN_PARTS]
    if not got and c.complete_missing is None:
        got = [p for p, have in (("website", c.website), ("description", c.description)) if not have]
        got += ["hiring", "referrer_email"] if c.hiring_status != "verified" else ["referrer_email"]
    return [p for p in guardrails.SHOWN_PARTS if p in got]


def reasons(c: Company, missing: list[str]) -> dict[str, str | None]:
    """part -> why it is still missing, from the fill pass and the re-check; never guessed.
    None = no reason recorded (the leaf's `filled_at` says whether the pass has run)."""
    state, failed = _obj(c.fill_state), _obj(c.recheck_failed)
    out: dict[str, str | None] = {}
    for p in missing:
        why = failed.get(p) or state.get(p)
        out[p] = str(why) if why is not None else None
    return out


def _compact(d: dict) -> dict:
    """Drop null / empty / false values — 4,000 leaves, so every repeated key is weight.
    The client reads an absent key as its empty value."""
    return {k: v for k, v in d.items() if v not in (None, False, "", [], {})}


def company_leaf(c: Company, *, roles: int, anywhere: int, ready: tuple[int, int], drafted: bool) -> dict:
    miss = missing_parts(c)
    state = _obj(c.fill_state)
    return _compact({
        "id": f"company:{c.id}", "kind": "company", "cid": c.id, "label": c.name,
        "market": market(c, anywhere_roles=anywhere), "city": c.city,
        "star": bool(c.list_key), "list_rank": c.list_rank,
        "status": "complete" if not miss else "filling",
        "missing": miss, "reasons": {k: v for k, v in reasons(c, miss).items() if v},
        # what else the fill pass noted (linkedin, funding …) — not part of the bar
        "notes": {k: str(v) for k, v in state.items() if k not in miss},
        "no_site": "website" in miss, "no_description": "description" in miss,
        "priority": c.priority_score, "best_selection": c.best_selection_score,
        "best_selection_band": c.best_selection_band,
        "referrers": ready[0], "referrers_email": ready[1],
        "roles": roles, "open_roles": c.open_roles_count,
        "drafted": drafted,
        "filled_at": c.filled_at.isoformat(timespec="seconds") if c.filled_at else None,
    })


# ------------------------------------------------------------------ the world, read once

def _load() -> dict:
    init_db()
    with get_session() as s:
        rows = [c for c in s.exec(select(Company)).all() if active(c)]
        roles = dict(s.exec(select(Job.company_id, func.count()).group_by(Job.company_id)).all())
        anywhere = dict(s.exec(select(Job.company_id, func.count())
                               .where(col(Job.remote_anywhere).is_(True)).group_by(Job.company_id)).all())
        drafted = set(s.exec(select(Email.company_id).where(col(Email.status).in_(_DRAFTED)).distinct()).all())
        ready = guardrails.ready_counts(s)
    leaves = {}
    for c in rows:
        leaves[c.id] = company_leaf(c, roles=roles.get(c.id, 0), anywhere=anywhere.get(c.id, 0),
                                    ready=ready.get(c.id, (0, 0)), drafted=c.id in drafted)
    return {"companies": rows, "leaves": leaves}


def _kg(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    """Read-only query against the graph tables; an absent graph is an empty answer."""
    try:
        conn = sqlite3.connect(f"file:{KG_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def investor_links(company_ids: set[int], *, max_share: int = 12) -> tuple[list[dict], list[dict]]:
    """Two companies sharing an investor, from the graph's company -FUNDED_BY-> investor edges.
    Drawn as a star from the investor's first company (n-1 links, not n²); an investor in
    more than `max_share` of our companies is an index, not a relationship, and is skipped."""
    by_inv: dict[str, list[int]] = defaultdict(list)
    labels: dict[str, str] = {}
    for r in _kg("SELECT e.src, e.dst, n.label FROM kg_edge e JOIN kg_node n ON n.id = e.dst "
                 "WHERE e.rel = 'FUNDED_BY' AND e.src LIKE 'company:%' AND n.kind = 'investor'"):
        try:
            cid = int(str(r["src"]).split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if cid in company_ids:
            by_inv[r["dst"]].append(cid)
            labels[r["dst"]] = r["label"]
    links, investors = [], []
    for inv, cids in sorted(by_inv.items()):
        cids = sorted(set(cids))
        if len(cids) < 2 or len(cids) > max_share:
            continue
        investors.append({"id": inv, "label": labels[inv], "companies": [f"company:{i}" for i in cids]})
        for other in cids[1:]:
            links.append({"source": f"company:{cids[0]}", "target": f"company:{other}", "kind": "investor",
                          "label": labels[inv], "weight": 1})
    return links, investors


# ------------------------------------------------------------------ roll-ups

def _bucket(id: str, label: str, **kw) -> dict:
    return {"id": id, "label": label, "count": 0, "complete": 0, "pending": 0, "star_pending": 0,
            "india_pending": 0, "missing": Counter(), **kw}


def _add(b: dict, leaf: dict) -> None:
    b["count"] += 1
    if leaf["status"] == "complete":
        b["complete"] += 1
        return
    b["pending"] += 1
    b["star_pending"] += int(bool(leaf.get("star")))
    b["india_pending"] += int(leaf.get("market") == "india")
    for p in leaf.get("missing") or []:
        b["missing"][p] += 1


def _done(buckets) -> list[dict]:
    return [{**b, "missing": dict(b["missing"])} for b in buckets]


def counts(leaves: list[dict]) -> dict:
    pend = [l for l in leaves if l["status"] != "complete"]
    return {
        "total": len(leaves),
        "complete": len(leaves) - len(pend),
        "filling": len(pend),
        "missing": dict(Counter(p for l in pend for p in l.get("missing") or [])),
        "star_pending": sum(1 for l in pend if l.get("star")),
        "india_pending": sum(1 for l in pend if l.get("market") == "india"),
        "remote_pending": sum(1 for l in pend if l.get("market") == "remote"),
        "never_filled": sum(1 for l in pend if l.get("kind") == "company" and not l.get("filled_at")),
    }


# ------------------------------------------------------------------ views

def market_view(world: dict) -> dict:
    """Industry layers → segments → companies; segment co-membership and shared investors."""
    hubs = {L["key"]: _bucket(f"layer:{L['key']}", L["label"], code=L["code"], blurb=L["blurb"], order=i, row=0)
            for i, L in enumerate(seg.LAYERS)}
    subs: dict[str, dict] = {}
    pair: Counter = Counter()
    leaves = []
    for c in world["companies"]:
        primary, hits = seg.classify(c.description, c.notes, c.name)
        leaf = {**world["leaves"][c.id], "hub": f"layer:{primary.layer}", "sub": f"segment:{primary.id}"}
        sub = subs.get(primary.id)
        if sub is None:
            sub = subs[primary.id] = _bucket(f"segment:{primary.id}", primary.label, hub=f"layer:{primary.layer}",
                                             blurb=primary.blurb)
        _add(sub, leaf)
        _add(hubs[primary.layer], leaf)
        for h in hits:
            if h.id != primary.id:
                pair[tuple(sorted((primary.id, h.id)))] += 1
        leaves.append(leaf)
    order = [L["key"] for L in seg.LAYERS]
    links = [{"source": f"layer:{a}", "target": f"layer:{b}", "kind": "chain", "weight": 1}
             for a, b in zip(order, order[1:])]
    links += [{"source": f"segment:{a}", "target": f"segment:{b}", "kind": "overlap", "weight": w}
              for (a, b), w in pair.items() if w >= 2 and a in subs and b in subs]
    inv_links, investors = investor_links({c.id for c in world["companies"]})
    return {
        "hubs": _done(hubs.values()),
        "subs": _done(sorted(subs.values(), key=lambda s: (order.index(s["hub"].split(":", 1)[1]), -s["count"]))),
        "leaves": leaves, "links": links + inv_links, "investors": investors,
    }


def pipeline_stage(leaf: dict) -> int:
    """The furthest stage a company has reached in order — a gate it has not passed stops it
    there, whatever lies beyond (a referrer at a company with no description is still at
    Discovered: the chain is only as far as its first gap)."""
    miss = set(leaf.get("missing") or [])
    gates = [
        not ({"website", "description"} & miss),
        "hiring" not in miss,
        (leaf.get("referrers") or 0) > 0 or "referrer_email" not in miss,
        not miss,
        bool(leaf.get("drafted")) and not miss,
    ]
    stage = 0
    for ok in gates:
        if not ok:
            break
        stage += 1
    return stage


def pipeline_view(world: dict) -> dict:
    """Stages left → right; each company sits at the furthest stage it reached."""
    hubs = [_bucket(f"stage:{s['key']}", s["label"], code=f"P{i + 1}", blurb=s["blurb"], order=i, row=0,
                    reached=0) for i, s in enumerate(PIPELINE)]
    leaves = []
    for c in world["companies"]:
        leaf = world["leaves"][c.id]
        k = pipeline_stage(leaf)
        leaf = {**leaf, "hub": hubs[k]["id"], "stage": k}
        _add(hubs[k], leaf)
        for h in hubs[: k + 1]:
            h["reached"] += 1
        leaves.append(leaf)
    links = [{"source": a["id"], "target": b["id"], "kind": "chain", "weight": b["reached"]}
             for a, b in zip(hubs, hubs[1:])]
    return {"hubs": _done(hubs), "subs": [], "leaves": leaves, "links": links, "investors": []}


def project_view() -> dict:
    """The context layer: stages as the chain; features, gaps and decisions as leaves;
    constraints and guarantees as a second row the features serve."""
    nodes = {r["id"]: dict(r) for r in _kg(
        "SELECT id, kind, label, summary, props FROM kg_node WHERE layer = 'context' AND kind IN "
        "('stage','feature','gap','decision','constraint','guarantee')")}
    edges = [dict(r) for r in _kg(
        "SELECT src, rel, dst FROM kg_edge WHERE layer = 'context' AND rel IN "
        "('AT_STAGE','WOULD_BUILD','ADOPTS','SERVES','THEN','ENFORCED_BY','PROTECTS')")]
    props = {k: _obj(v.get("props")) for k, v in nodes.items()}
    stages = sorted((n for n in nodes.values() if n["kind"] == "stage"),
                    key=lambda n: (props[n["id"]].get("order", 99), n["id"]))
    rules = sorted((n for n in nodes.values() if n["kind"] in ("constraint", "guarantee")),
                   key=lambda n: (n["kind"], props[n["id"]].get("order", 99), n["id"]))
    hubs = {n["id"]: _bucket(n["id"], n["label"], code=f"S{i}", blurb=n.get("summary") or "", order=i, row=0)
            for i, n in enumerate(stages)}
    for i, n in enumerate(rules):
        hubs[n["id"]] = _bucket(n["id"], n["label"], code=("C" if n["kind"] == "constraint" else "G") + str(i),
                                blurb=n.get("summary") or "", order=i, row=1, kind=n["kind"])
    at_stage = {e["src"]: e["dst"] for e in edges if e["rel"] == "AT_STAGE" and e["dst"] in hubs}
    leaves, links = [], []
    fallback = stages[0]["id"] if stages else None
    for nid, n in nodes.items():
        kind = n["kind"]
        if kind not in ("feature", "gap", "decision"):
            continue
        if kind == "feature":
            hub = at_stage.get(nid)
            st = str(props[nid].get("status") or "open")
            if st == "dropped":
                continue          # a decision took it off the table; not pending, not done
            status = "complete" if st == "built" else "filling"
            missing = [] if status == "complete" else [st if st in PROJECT_PARTS else "open"]
        else:
            rel = "WOULD_BUILD" if kind == "gap" else "ADOPTS"
            hub = next((at_stage[e["dst"]] for e in edges if e["src"] == nid and e["rel"] == rel
                        and e["dst"] in at_stage), None)
            status = "filling" if kind == "gap" else "complete"
            missing = ["gap"] if kind == "gap" else []
            st = "gap" if kind == "gap" else "adopted"
        hub = hub or fallback
        if hub is None:
            continue
        leaf = {"id": nid, "kind": kind, "label": n["label"], "summary": n.get("summary"), "hub": hub, "sub": None,
                "status": status, "missing": missing, "reasons": {m: PROJECT_PARTS.get(m) for m in missing},
                "feature_status": st, "star": False, "market": None,
                "priority": None}
        _add(hubs[hub], leaf)
        leaves.append(leaf)
    leaf_ids = {l["id"] for l in leaves}
    for e in edges:
        if e["rel"] == "THEN" and e["src"] in hubs and e["dst"] in hubs:
            links.append({"source": e["src"], "target": e["dst"], "kind": "chain", "weight": 1})
        elif e["rel"] in ("SERVES", "ENFORCED_BY", "PROTECTS") and {e["src"], e["dst"]} & leaf_ids \
                and ({e["src"], e["dst"]} - leaf_ids) <= set(hubs):
            links.append({"source": e["src"], "target": e["dst"], "kind": e["rel"].lower(), "weight": 1})
        elif e["rel"] == "WOULD_BUILD" and e["src"] in leaf_ids and e["dst"] in leaf_ids:
            links.append({"source": e["src"], "target": e["dst"], "kind": "would_build", "weight": 1})
    return {"hubs": _done(sorted(hubs.values(), key=lambda h: (h["row"], h["order"]))), "subs": [],
            "leaves": leaves, "links": links, "investors": []}


# ------------------------------------------------------------------ the cached entry point

_cache: dict[str, tuple[tuple, float, dict]] = {}
_lock = threading.Lock()


def signature() -> tuple:
    """Changes whenever a tick or a fill lands: cheap aggregates, no row loads."""
    init_db()
    with get_session() as s:
        n, done, last_fill, last_complete = s.exec(select(
            func.count(Company.id), func.sum(Company.complete), func.max(Company.filled_at),
            func.max(Company.complete_at))).one()
        ticks = s.exec(select(func.max(Setting.value)).where(col(Setting.key).like("tick:last:%"))).one()
        emails = s.exec(select(func.count(Email.id))).one()
    kg = _kg("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM kg_node WHERE layer = 'context'")
    kg_sig = (kg[0]["n"], kg[0]["u"]) if kg else (0, None)
    return (n, done, str(last_fill), str(last_complete), str(ticks)[:60], emails, kg_sig)


def build(view: str = "market") -> dict:
    view = view if view in VIEWS else "market"
    if view == "project":
        body = project_view()
    else:
        world = _load()
        body = market_view(world) if view == "market" else pipeline_view(world)
    return {
        "view": view,
        "views": [{"key": "market", "label": "Market"}, {"key": "pipeline", "label": "Pipeline"},
                  {"key": "project", "label": "Project"}],
        "parts": PARTS if view != "project" else [{"key": k, "label": v} for k, v in PROJECT_PARTS.items()],
        **body,
        "counts": counts(body["leaves"]),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def cached(view: str = "market") -> dict:
    view = view if view in VIEWS else "market"
    sig = signature()
    now = time.time()
    with _lock:
        hit = _cache.get(view)
        if hit and hit[0] == sig and now - hit[1] < CACHE_TTL_S:
            return hit[2]
    out = build(view)
    with _lock:
        _cache[view] = (sig, now, out)
    return out


def invalidate() -> None:
    with _lock:
        _cache.clear()


__all__ = ["build", "cached", "invalidate", "market", "missing_parts", "reasons", "pipeline_stage", "VIEWS",
           "PARTS", "PIPELINE", "active", "company_leaf", "counts"]
