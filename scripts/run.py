#!/usr/bin/env python
"""JobHunter CLI.

    python scripts/run.py doctor         # check OpenRouter, budget, Gmail, tokens, DB
    python scripts/run.py profile        # parse resume -> profile.json
    python scripts/run.py scrape         # run the scraper fleet
    python scripts/run.py score          # lexical gate + rubric-score
    python scripts/run.py find-contacts  # discover contacts at high-match companies
    python scripts/run.py draft          # queue referral drafts for review
    python scripts/run.py gmail-auth     # one-time OAuth consent
    python scripts/run.py send           # send approved drafts (respects cap + window)
    python scripts/run.py poll           # check Gmail for replies
    python scripts/run.py daily          # the whole ingestion cycle
    python scripts/run.py serve          # FastAPI + scheduler on :8000

    python scripts/run.py kg build       # (re)build the knowledge graph: context + data + brief + viewer
    python scripts/run.py kg search "warmth"          # full-text search across data + context
    python scripts/run.py kg show feature:warmth-tiers
    python scripts/run.py kg path contact:3 constraint:send-cap-25
    python scripts/run.py kg compose "..."            # best feature per stage for a problem statement
    python scripts/run.py kg note "what changed" --about gap:1-send-ledger

    python scripts/run.py research doctor              # which web-research channels are live
    python scripts/run.py research web "seed-stage AI infra startups hiring in London"
    python scripts/run.py research read <url> --text
    python scripts/run.py research github "llm evaluation framework"
    python scripts/run.py research company "Acme AI" --depth deep
    python scripts/run.py research startups --topic AI --table

    python scripts/run.py models status                # OpenRouter key, aliases, caps, spend today
    python scripts/run.py models list --free           # the zero-cost roster, widest context first
    python scripts/run.py models check --live          # prove each alias answers
    python scripts/run.py models costs --month         # spend by alias and by stage
    python scripts/run.py fit-explain 441              # why the free gate scored a job that way
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

app = typer.Typer(add_completion=False, help="JobHunter — AI job scraping & referral outreach")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


def _echo(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


@app.command()
def doctor() -> None:
    """Check every external dependency and report what's missing."""
    _setup_logging()
    from jobhunter import CONFIG, ROOT, llm, matcher
    from jobhunter.contacts import hunter
    from jobhunter.db import init_db
    from jobhunter.outreach import gmail

    init_db()
    health = llm.health()
    profile_path = ROOT / CONFIG["profile_path"]
    gh_token = bool((CONFIG.get("contacts") or {}).get("github_token"))

    budget = health["budget"]
    checks = [
        ("OpenRouter key", health.get("key_present"), health.get("hint", "")),
        ("Provider enabled", health.get("enabled"), "set openrouter.enabled: true in config.yaml"),
        ("Model aliases", health.get("model_present"), "fill the openrouter.aliases block in config.yaml"),
        (
            f"Budget (today ₹{budget['day']['spent_inr']:.2f}/₹{budget['day']['cap_inr']:.0f})",
            not budget["over_cap"],
            "raise openrouter.daily_inr_cap / monthly_inr_cap in config.yaml",
        ),
        ("Resume parsed", profile_path.exists(), "run `python scripts/run.py profile`"),
        ("GitHub token", gh_token, "optional: set contacts.github_token (60/hr -> 5000/hr)"),
        ("Hunter.io key", hunter.available(), "optional: set contacts.hunter_api_key (25 lookups/mo)"),
        ("Gmail client JSON", gmail.configured(), gmail.status()["hint"]),
        ("Gmail authorized", gmail.authorized(), "run `python scripts/run.py gmail-auth`"),
    ]
    for name, ok, hint in checks:
        mark = "OK  " if ok else "MISS"
        typer.echo(f"[{mark}] {name}" + ("" if ok else f"  ->  {hint}"))

    typer.echo("")
    _echo(matcher.counts())


@app.command()
def profile(path: str = typer.Option(None, help="Path to resume PDF/MD/TXT")) -> None:
    """Parse the resume into profile.json."""
    _setup_logging()
    from jobhunter import resume

    data = resume.build_profile(Path(path) if path else None)
    _echo({k: v for k, v in data.items() if not k.startswith("_")})


@app.command()
def scrape(
    sources: str = typer.Option(None, help="Comma-separated subset, e.g. 'greenhouse,hn_hiring'"),
    verbose: bool = False,
) -> None:
    """Run the scraper fleet and persist new jobs."""
    _setup_logging(verbose)
    from jobhunter import pipeline

    names = [s.strip() for s in sources.split(",")] if sources else None
    _echo(pipeline.scrape(names).as_dict())


@app.command()
def score(
    limit: int = typer.Option(40, help="How many jobs to rubric-score this run"),
    salaries: int = typer.Option(0, help="Also run the LLM salary backfill on N jobs"),
    verbose: bool = False,
) -> None:
    """Lexically gate unscored jobs, then rubric-score the most promising."""
    _setup_logging(verbose)
    from jobhunter import matcher, notify, pipeline

    if salaries:
        typer.echo(f"salary backfill: {pipeline.extract_salaries(limit=salaries)} resolved")
    typer.echo(f"threshold: {matcher.prefilter_threshold()}")
    typer.echo(f"gated: {matcher.prefilter(limit=2000)}")
    result = matcher.score_pending(limit=limit)
    notify.notify_high_matches()
    _echo(result)


@app.command(name="find-contacts")
def find_contacts(
    company: int = typer.Option(None, help="Company id; omit to sweep high-match companies"),
    limit: int = typer.Option(5, help="How many companies to process"),
    verbose: bool = False,
) -> None:
    """Discover contactable people at high-match companies."""
    _setup_logging(verbose)
    from jobhunter import contacts

    _echo(contacts.discover(company) if company else contacts.discover_for_high_matches(limit=limit))


@app.command()
def draft(
    contact: int = typer.Option(None, help="Contact id; omit to draft for high-match companies"),
    job: int = typer.Option(None, help="Job id to reference"),
    limit: int = typer.Option(5, help="How many drafts to queue"),
    per_company: int = typer.Option(2, help="Max contacts per company"),
    verbose: bool = False,
) -> None:
    """Queue referral drafts in the review queue."""
    _setup_logging(verbose)
    from jobhunter.outreach import drafter

    if contact:
        _echo(drafter.draft_for(contact, job))
    else:
        _echo(drafter.draft_for_high_matches(limit=limit, per_company=per_company))


@app.command(name="gmail-auth")
def gmail_auth() -> None:
    """Run the one-time Gmail OAuth consent flow (opens a browser)."""
    _setup_logging()
    from jobhunter.outreach import gmail

    try:
        _echo(gmail.authorize())
    except gmail.GmailNotConfigured as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1) from e


@app.command()
def send(
    email_id: int = typer.Option(None, help="Send one specific email"),
    limit: int = typer.Option(None, help="Max to send this run"),
    ignore_window: bool = typer.Option(False, help="Send outside the configured hours"),
    no_stagger: bool = typer.Option(False, help="Skip the delay between sends (testing only)"),
) -> None:
    """Send approved drafts, respecting the daily cap and send window."""
    _setup_logging()
    from jobhunter.outreach import sender

    typer.echo(json.dumps(sender.quota(), indent=2, default=str))
    if email_id:
        _echo(sender.send_email(email_id, ignore_window=ignore_window))
    else:
        _echo(sender.send_approved(limit=limit, ignore_window=ignore_window, stagger=not no_stagger))


@app.command()
def approve(email_id: int) -> None:
    """Approve a draft from the CLI (the dashboard is the usual place)."""
    _setup_logging()
    from jobhunter.outreach import sender

    _echo(sender.approve(email_id))


@app.command()
def poll(followups: bool = typer.Option(True, help="Also queue follow-ups for silent threads")) -> None:
    """Poll Gmail for replies and classify them."""
    _setup_logging()
    from jobhunter import notify
    from jobhunter.outreach import drafter, tracker

    _echo(tracker.poll())
    if followups:
        _echo(drafter.queue_followups())
    notify.notify_replies()


@app.command()
def daily() -> None:
    """Run the full ingestion cycle once: scrape -> salaries -> embed -> score -> notify."""
    _setup_logging()
    from jobhunter import scheduler

    _echo(scheduler.daily_cycle())


@app.command()
def discover(limit: int = typer.Option(60, help="How many YC companies to probe")) -> None:
    """Probe for new public ATS boards and append them to companies.yaml."""
    _setup_logging()
    import asyncio

    import yaml

    from jobhunter import ROOT
    from jobhunter.pipeline import load_companies
    from jobhunter.scrapers.base import make_client
    from jobhunter.scrapers.yc import discover_companies

    async def go():
        async with make_client() as http:
            return await discover_companies(http, limit=limit)

    found = asyncio.run(go())
    existing = load_companies()
    known = {(c.get("ats"), c.get("ats_slug")) for c in existing}
    fresh = [c for c in found if (c["ats"], c["ats_slug"]) not in known]

    for c in fresh:
        website = c.get("website") or ""
        existing.append(
            {
                "name": c["name"],
                "website": website,
                "domain": website.split("//")[-1].split("/")[0].removeprefix("www.") or None,
                "ats": c["ats"],
                "ats_slug": c["ats_slug"],
                "tags": ["yc", "discovered"],
            }
        )

    path = ROOT / "companies.yaml"
    path.write_text(
        yaml.safe_dump({"companies": existing}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    _echo({"added": len(fresh), "total": len(existing), "new": [c["name"] for c in fresh]})


@app.command()
def serve(
    host: str = typer.Option(None),
    port: int = typer.Option(None),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (disables the scheduler)"),
) -> None:
    """Start the FastAPI backend and the scheduler."""
    _setup_logging()
    from jobhunter import CONFIG
    from jobhunter.api import serve as _serve

    api = CONFIG.get("api") or {}
    typer.echo(f"API on http://{host or api.get('host', '127.0.0.1')}:{port or api.get('port', 8000)}")
    typer.echo("Docs at /docs   |   Dashboard: cd dashboard && npm run dev")
    _serve(host=host, port=port, reload=reload)


# ---------------------------------------------------------------- knowledge graph

kg_app = typer.Typer(add_completion=False, help="Knowledge graph — pipeline data + project context, one queryable store")
app.add_typer(kg_app, name="kg")


@kg_app.command("build")
def kg_build(no_viewer: bool = typer.Option(False, help="Skip writing knowledge/graph.html")) -> None:
    """Load knowledge/context.yaml, mirror every table, write BRIEF.md and the HTML viewer."""
    _setup_logging()
    from jobhunter.kg import analyze, brief, context, export, sync
    from jobhunter.kg.store import Graph

    with Graph() as g:
        ctx = context.load(g)
    out = {"context": ctx, "data": sync.sync_all(), "brief": brief.write()}
    if not no_viewer:
        out["viewer"] = export.write_html()
        out["graphml"] = analyze.write_graphml()
    with Graph() as g:
        out["stats"] = g.stats()
    _echo(out)


@kg_app.command("sync")
def kg_sync() -> None:
    """Mirror the pipeline tables into the data layer (runs automatically after each cycle)."""
    _setup_logging()
    from jobhunter.kg import sync

    _echo(sync.sync_all())


@kg_app.command("context")
def kg_context() -> None:
    """Reload only the context layer from knowledge/context.yaml (session notes are kept)."""
    _setup_logging()
    from jobhunter.kg import context
    from jobhunter.kg.store import Graph

    with Graph() as g:
        _echo(context.load(g))


@kg_app.command("brief")
def kg_brief(print_: bool = typer.Option(False, "--print", help="Print instead of writing the file")) -> None:
    """Render knowledge/BRIEF.md — the context handoff a fresh session reads first."""
    from jobhunter.kg import brief

    typer.echo(brief.render() if print_ else brief.write())


@kg_app.command("stats")
def kg_stats() -> None:
    from jobhunter.kg.store import Graph

    with Graph() as g:
        _echo(g.stats())


@kg_app.command("search")
def kg_search(
    query: str,
    kind: str = typer.Option(None, help="Comma-separated kinds, e.g. feature,decision or job,contact"),
    limit: int = 15,
    any_: bool = typer.Option(False, "--any", help="Match any term instead of all"),
) -> None:
    """Full-text search across both layers."""
    from jobhunter.kg.store import Graph

    kinds = [k.strip() for k in kind.split(",")] if kind else None
    with Graph() as g:
        hits = g.search(query, kinds=kinds, limit=limit, mode="or" if any_ else "and")
    for n in hits:
        status = n["props"].get("status")
        tag = f" [{status}]" if status else ""
        typer.echo(f"{n['id']:<48}{tag} {n['label']}")
        if n.get("summary"):
            typer.echo(f"    {n['summary'][:160]}")
    if not hits:
        typer.echo("no matches")


@kg_app.command("show")
def kg_show(node_id: str, depth: int = 1, json_: bool = typer.Option(False, "--json")) -> None:
    """A node, its properties and everything it links to."""
    from jobhunter.kg.store import Graph

    with Graph() as g:
        node = g.get(node_id)
        if node is None:
            hits = g.search(node_id, limit=5, mode="or")
            typer.secho(f"no node '{node_id}'", fg=typer.colors.RED)
            for h in hits:
                typer.echo(f"  did you mean {h['id']}  {h['label']}")
            raise typer.Exit(1)
        hood = g.neighbors(node_id, depth=depth)
    if json_:
        _echo({"node": node, **hood})
        return
    typer.secho(f"{node['id']}  ({node['kind']}, {node['layer']})", bold=True)
    typer.echo(node["label"])
    if node.get("summary"):
        typer.echo(f"\n{node['summary']}")
    props = {k: v for k, v in node["props"].items() if v not in (None, "", [], {})}
    if props:
        typer.echo("")
        for k, v in props.items():
            typer.echo(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (list, dict)) else v}")
    labels = {n["id"]: n["label"] for n in hood["nodes"]}
    outs = [e for e in hood["edges"] if e["src"] == node_id]
    ins = [e for e in hood["edges"] if e["dst"] == node_id]
    if outs:
        typer.echo("\n→ outgoing")
        for e in sorted(outs, key=lambda e: (e["rel"], e["dst"])):
            typer.echo(f"  {e['rel']:<16} {e['dst']:<44} {labels.get(e['dst'], '')[:70]}")
    if ins:
        typer.echo("\n← incoming")
        for e in sorted(ins, key=lambda e: (e["rel"], e["src"])):
            typer.echo(f"  {e['rel']:<16} {e['src']:<44} {labels.get(e['src'], '')[:70]}")


@kg_app.command("path")
def kg_path(a: str, b: str, max_depth: int = 6) -> None:
    """Shortest path between two nodes — e.g. how a contact connects to a constraint."""
    from jobhunter.kg.store import Graph

    with Graph() as g:
        steps = g.path(a, b, max_depth=max_depth)
    if not steps:
        typer.echo(f"no path within {max_depth} hops")
        raise typer.Exit(1)
    for step in steps:
        if "edge" in step:
            e = step["edge"]
            typer.echo(f"    --{e['rel']}-->")
        else:
            typer.echo(f"{step['id']}  {step['label'][:80]}")


@kg_app.command("note")
def kg_note(
    text: str,
    about: list[str] = typer.Option(None, "--about", "-a", help="Node ids this note is about (repeatable)"),
    tag: list[str] = typer.Option(None, "--tag", "-t"),
    title: str = typer.Option(None, help="Short title; defaults to the first line of the text"),
    brief: bool = typer.Option(True, help="Regenerate BRIEF.md afterwards"),
) -> None:
    """Remember something — a decision, a result, what changed. This is how context accumulates."""
    from jobhunter.kg import brief as brief_mod
    from jobhunter.kg.store import Graph

    with Graph() as g:
        result = g.remember(text, about=about or [], tags=tag or [], title=title)
    if result["missing_targets"]:
        typer.secho(f"unknown --about ids (note saved without them): {result['missing_targets']}", fg=typer.colors.YELLOW)
    if brief:
        result["brief"] = brief_mod.write()
    _echo(result)


@kg_app.command("compose")
def kg_compose(
    statement: str = typer.Argument(None, help="Problem statement; defaults to the problem node"),
    for_: list[str] = typer.Option(None, "--for", help="Constraint/guarantee ids that matter (repeatable)"),
    top: int = typer.Option(2, help="Picks per stage"),
    include_dropped: bool = False,
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """Pick the best feature per stage across every architecture draft for a problem statement."""
    from jobhunter.kg import compose

    result = compose.compose(statement, constraints=for_ or None, top=top, include_dropped=include_dropped)
    if json_:
        _echo(result)
        return
    typer.secho(f"statement: {result['statement'][:200].strip()}…", dim=True)
    typer.secho(f"constraints: {result['constraints']}", dim=True)
    typer.echo(f"weights: {result['weights']}   features considered: {result['features_considered']}\n")
    for st in result["stages"]:
        typer.secho(st["label"], bold=True)
        for p in st["picks"]:
            b = p["breakdown"]
            typer.echo(
                f"  {p['score']:.2f}  [{p['status']}] {p['label']}"
                f"\n         rel {b['relevance']:.2f} · fit {b['fit']:.2f} · status {b['status']:.2f} · consensus {b['consensus']:.2f}"
                f"   from: {', '.join(x.split(' — ')[0] for x in p['proposed_by']) or '(build)'}"
            )
            if p["serves"]:
                typer.echo(f"         serves: {', '.join(p['serves'])}")
        if st["also_considered"]:
            typer.echo("  also: " + ", ".join(f"{a['id'].split(':',1)[1]} ({a['score']:.2f})" for a in st["also_considered"][:6]))
        typer.echo("")


@kg_app.command("hubs")
def kg_hubs(
    layer: str = typer.Option("context", help="context | data | all"),
    kind: str = typer.Option(None, help="Comma-separated kinds to keep"),
    top: int = 15,
) -> None:
    """Most load-bearing nodes by betweenness centrality (NetworkX)."""
    from jobhunter.kg import analyze

    kinds = [k.strip() for k in kind.split(",")] if kind else None
    rows = analyze.hubs(layer=None if layer == "all" else layer, kinds=kinds, top=top)
    typer.echo(f"{'betweenness':>11} {'deg':>4}  id")
    for r in rows:
        typer.echo(f"{r['betweenness']:>11.4f} {r['degree']:>4}  {r['id']:<44} {r['label'][:60]}")
    orphans = analyze.orphans(layer=None if layer == "all" else layer)
    if orphans:
        typer.secho(f"\n{len(orphans)} unlinked node(s): " + ", ".join(o["id"] for o in orphans[:10]), fg=typer.colors.YELLOW)


@kg_app.command("export")
def kg_export(
    all_jobs: bool = typer.Option(False, help="Include unscored jobs (default: scored only)"),
    fmt: str = typer.Option("html", help="html | json | graphml"),
    out: str = typer.Option(None, help="Output path"),
) -> None:
    """Write the graph as a standalone HTML viewer, JSON, or GraphML."""
    from jobhunter.kg import analyze, export

    if fmt == "json":
        typer.echo(export.write_json(out or export.JSON_PATH, include_all_jobs=all_jobs))
    elif fmt == "graphml":
        typer.echo(analyze.write_graphml(out or analyze.GRAPHML_PATH, include_all_jobs=all_jobs))
    else:
        typer.echo(export.write_html(out or export.HTML_PATH, include_all_jobs=all_jobs))


# ---------------------------------------------------------------- web research

@kg_app.command("why")
def kg_why(
    company: list[str] = typer.Argument(..., help="Company name, or a company:<id> node"),
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """Should we write to this company, to whom, and what do we actually know?

    Assembled by traversal: what they do -> funding + investors -> tier and why ->
    whether their own careers page backs the hiring -> which open roles want skills we
    have -> who can refer us -> whether we are allowed to write to them.
    """
    _setup_logging()
    from jobhunter.kg import hunt

    # Company names have spaces ("Scale AI"), so the name is taken as the rest of the
    # line and joined — quotes optional. Anything from a "#" on is a shell comment that
    # survived a copy-paste, not part of the name.
    name = " ".join(company).split("#")[0].strip()
    out = hunt.why(name)
    if out.get("error"):
        typer.secho(out["error"], fg=typer.colors.RED)
        raise typer.Exit(1)
    if json_:
        _echo(out)
        return

    act_colour = {"write": typer.colors.GREEN, "skip": typer.colors.RED,
                  "wait": typer.colors.YELLOW}.get(out["verdict"]["act"], typer.colors.CYAN)
    typer.secho(f"\n{out['company']}  [{out['tier'] or 'ungraded'}]  {out['region'] or ''}", bold=True)
    if out["what_they_do"]:
        typer.echo(f"  {out['what_they_do']}")
    typer.secho(f"\n  -> {out['verdict']['act'].upper()}: {out['verdict']['why']}", fg=act_colour, bold=True)

    typer.secho("\n  pay", bold=True)
    pay = out["pay"]
    stipend = f"₹{pay['stipend_inr_month']:,}/month" if pay["stipend_inr_month"] else "not stated"
    ppo = f"₹{pay['ppo_lpa']:g} LPA" if pay["ppo_lpa"] else "not stated"
    typer.echo(f"    stipend: {stipend}")
    typer.echo(f"    PPO    : {ppo}")
    typer.echo(f"    why    : {out['tier_reason']}")

    fund = out["funding"]
    if fund["stage"] or fund["investors"]:
        typer.secho("\n  funding", bold=True)
        typer.echo(f"    {fund['stage'] or 'stage unstated'}"
                   + (f" · ${fund['amount_usd_m']}M" if fund["amount_usd_m"] else "")
                   + (f" · {', '.join(i['name'] for i in fund['investors'])}" if fund["investors"] else ""))

    hiring = out["hiring"]
    typer.secho("\n  are they really hiring?", bold=True)
    typer.echo(f"    {hiring['status'] or 'unchecked'} — {(hiring['evidence'] or 'nobody has checked')[:150]}")

    if out["skills_they_want_that_i_have"]:
        typer.secho("\n  they ask for, and I have", bold=True)
        typer.echo(f"    {', '.join(out['skills_they_want_that_i_have'][:14])}")

    if out["open_roles"]:
        typer.secho("\n  open roles", bold=True)
        for j in out["open_roles"][:6]:
            typer.echo(f"    {j['title'][:56]:58} {j['skill_overlap']} skill(s) match")

    typer.secho("\n  who to ask", bold=True)
    if not out["who_to_ask"]:
        typer.echo("    nobody found yet — run `find-contacts`")
    for c in out["who_to_ask"][:8]:
        mark, colour = "", None
        if c.get("employment") == "contradicted":
            mark, colour = f"  DOES NOT WORK HERE: {c['employment_why']}", typer.colors.RED
        elif c.get("employment") == "unproven":
            mark, colour = "  (employment unproven)", typer.colors.YELLOW
        if not c["guardrail"]["ok"]:
            mark, colour = f"  BLOCKED: {c['guardrail']['why']}", typer.colors.RED
        line = f"    {c['rank']} {str(c['role_class']):16} {str(c['name'])[:24]:26} <{c['email'] or 'no address'}>"
        typer.secho(line + mark, fg=colour)
    typer.echo()


@kg_app.command("shortlist")
def kg_shortlist(
    limit: int = typer.Option(15),
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """Rank targets by everything the graph knows: grade, verified hiring, skill overlap, reachable people."""
    _setup_logging()
    from jobhunter.kg import hunt

    rows = hunt.shortlist(limit=limit)
    if json_:
        _echo(rows)
        return
    for r in rows:
        typer.secho(f"  [{r['tier']}] {r['name']}", bold=True, nl=False)
        typer.echo(f"  · {r['region'] or '?'} · hiring:{r['hiring'] or 'unchecked'}"
                   f" · {r['open_roles']} role(s) · {r['skill_overlap']} skill(s) · {r['reachable_people']} reachable")
        if r["what_they_do"]:
            typer.echo(f"        {r['what_they_do'][:104]}")
        if r["ask_first"]:
            typer.echo(f"        ask first: {r['ask_first']['name']} ({r['ask_first']['role_class']})")


@kg_app.command("expand")
def kg_expand(
    ref: list[str] = typer.Argument(..., help="A company or an investor"),
    limit: int = typer.Option(20),
) -> None:
    """Who else did their investors fund? Companies two hops away, underrated by construction."""
    _setup_logging()
    from jobhunter.kg import hunt

    out = hunt.expand(" ".join(ref).split("#")[0].strip(), limit=limit)
    if out.get("error"):
        typer.secho(out["error"], fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"{out['seed']} — via {', '.join(out['investors']) or 'no investor edges'}", bold=True)
    if out.get("hint"):
        typer.secho(f"  {out['hint']}", fg=typer.colors.YELLOW)
    for r in out["companies"]:
        typer.echo(f"  [{r['tier'] or '-'}] {r['name']:34} shared: {', '.join(r['via'][:3])}")


@kg_app.command("guard")
def kg_guard(ref: list[str] = typer.Argument(..., help="A contact or a company")) -> None:
    """May we write to them? MOTIV §6's cooldown rules, answered by traversal."""
    _setup_logging()
    from jobhunter.kg import hunt

    out = hunt.guardrails(" ".join(ref).split("#")[0].strip())
    if out.get("error"):
        typer.secho(out["error"], fg=typer.colors.RED)
        raise typer.Exit(1)
    if "contacts" in out:
        typer.secho(f"{out['company']}: {out['clear']} clear, {out['blocked']} blocked", bold=True)
        for c in out["contacts"]:
            colour = typer.colors.GREEN if c["ok"] else typer.colors.RED
            typer.secho(f"  {'OK ' if c['ok'] else 'NO '} {c['contact'][:30]:32} {c['why']}", fg=colour)
    else:
        colour = typer.colors.GREEN if out["ok"] else typer.colors.RED
        typer.secho(f"{out['contact']}: {out['why']}", fg=colour)


@kg_app.command("audit")
def kg_audit(
    limit: int = typer.Option(10, help="Rows per check"),
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """Contradictions between what different parts of the graph believe.

    Each table is individually consistent; it is the relationships that go wrong — a
    contact whose address is on another company's domain, a draft against a hiring claim
    we disproved, a rejected company we spent model calls scoring.
    """
    _setup_logging()
    from jobhunter.kg import hunt

    out = hunt.audit(limit_per_check=limit)
    if json_:
        _echo(out)
        return
    if not out["findings"]:
        typer.secho("no contradictions found", fg=typer.colors.GREEN)
        return
    for check, rows in out["findings"].items():
        typer.secho(f"\n{check}  ({len(rows)})", fg=typer.colors.YELLOW, bold=True)
        for r in rows:
            bits = " · ".join(f"{k}={v}" for k, v in r.items() if k != "id" and v is not None)
            typer.echo(f"    {bits[:150]}")


research_app = typer.Typer(
    add_completion=False,
    help="Web research — search, read and mine the open web via the Agent Reach backends",
)
app.add_typer(research_app, name="research")


@research_app.command("doctor")
def research_doctor() -> None:
    """Which research channels work on this machine, and how to fix the rest."""
    _setup_logging()
    from jobhunter import research

    report = research.doctor()
    for exe, info in report["tools"].items():
        mark = "OK  " if info["status"] == "ok" else "MISS"
        typer.echo(f"[{mark}] {exe:<12} {info['detail'][:90]}")
    typer.echo("")
    for cap, info in report["capabilities"].items():
        if info["preferred"]:
            typer.echo(f"[OK  ] {cap:<12} -> {info['preferred']}  (fallbacks: {', '.join(info['usable'][1:]) or 'none'})")
        else:
            typer.secho(f"[MISS] {cap:<12} -> nothing usable", fg=typer.colors.YELLOW)
        for backend, hint in (info["hints"] or {}).items():
            if hint:
                typer.echo(f"         {backend}: {hint}")
    typer.echo("")
    _echo({"secrets_present": report["secrets"], "reddit": report["reddit"], "cache": report["cache"],
           "exa_budget": report["exa_budget"]})


@research_app.command("web")
def research_web(
    query: str,
    limit: int = typer.Option(8, help="How many results"),
    fresh: bool = typer.Option(False, help="Bypass the 24h cache"),
) -> None:
    """Semantic web search. Describe the ideal page, not keywords."""
    _setup_logging()
    from jobhunter import research

    _echo(research.search_web(query, limit=limit, fresh=fresh))


@research_app.command("x")
def research_x(
    query: str,
    limit: int = typer.Option(20, help="How many posts"),
    days: int = typer.Option(30, help="Reported back only — the search engine gives no dates yet"),
    fresh: bool = typer.Option(False, help="Bypass the 24h cache"),
) -> None:
    """Posts on X via a site:x.com web search — no X account, no cookies, no X API. Records only."""
    _setup_logging()
    from jobhunter import research

    _echo(research.search_x(query, limit=limit, days=days, fresh=fresh))


@research_app.command("x-search")
def research_x_search(
    query: str,
    limit: int = typer.Option(20, help="How many posts"),
    fresh: bool = typer.Option(False, help="Bypass the 24h cache"),
) -> None:
    """Live X search through the THROWAWAY session in .env — read-only, capped per day, gapped. Records only."""
    _setup_logging()
    from jobhunter.research import x_search

    _echo(x_search.search(query, limit=limit, fresh=fresh))


@research_app.command("read")
def research_read(
    url: str,
    max_chars: int = typer.Option(12000),
    fresh: bool = typer.Option(False),
    text: bool = typer.Option(False, "--text", help="Print the page text instead of JSON"),
) -> None:
    """Fetch one page as readable text."""
    _setup_logging()
    from jobhunter import research

    page = research.read_page(url, max_chars=max_chars, fresh=fresh)
    typer.echo(page.get("text", "") if text else json.dumps(page, indent=2, default=str))


@research_app.command("github")
def research_github(query: str, limit: int = 10, fresh: bool = False) -> None:
    """Search public GitHub repositories."""
    _setup_logging()
    from jobhunter import research

    _echo(research.search_github(query, limit=limit, fresh=fresh))


@research_app.command("reddit")
def research_reddit(
    query: str,
    limit: int = 10,
    subreddit: str = typer.Option(None, "-r", help="Restrict to one subreddit"),
    fresh: bool = False,
) -> None:
    """Search Reddit. Needs a logged-in backend — Reddit has no anonymous path."""
    _setup_logging()
    from jobhunter import research

    _echo(research.search_reddit(query, limit=limit, subreddit=subreddit, fresh=fresh))


@research_app.command("youtube")
def research_youtube(
    query: str,
    limit: int = 5,
    transcript: str = typer.Option(None, help="Instead of searching, transcribe this video URL"),
) -> None:
    """Search YouTube, or pull one video's subtitles with --transcript URL."""
    _setup_logging()
    from jobhunter import research

    _echo(research.youtube_transcript(transcript) if transcript else research.search_youtube(query, limit=limit))


@research_app.command("company")
def research_company_cmd(
    name: str,
    depth: str = typer.Option("standard", help="quick | standard | deep"),
    website: str = typer.Option(None, help="Skip discovery if you already know the site"),
    fresh: bool = False,
) -> None:
    """Research one company: what it builds, funding, GitHub, hiring signals."""
    _setup_logging()
    from jobhunter import research

    _echo(research.research_company(name, website=website, depth=depth, fresh=fresh))


@research_app.command("startups")
def research_startups(
    topic: str = typer.Option("AI", help="Sector, e.g. AI / fintech / robotics"),
    regions: str = typer.Option("United States,United Kingdom,United Arab Emirates"),
    stages: str = typer.Option("seed or Series A", help="Comma-separated stage phrases"),
    limit: int = typer.Option(10, help="How many companies to return"),
    enrich: int = typer.Option(5, help="How many to research in depth (website, GitHub, hiring)"),
    fresh: bool = False,
    table: bool = typer.Option(False, "--table", help="Human-readable summary instead of JSON"),
) -> None:
    """Recently funded startups, with funding and hiring information."""
    _setup_logging()
    from jobhunter import research

    out = research.find_startups(
        topic=topic,
        regions=[r.strip() for r in regions.split(",") if r.strip()],
        stages=[s.strip() for s in stages.split(",") if s.strip()],
        limit=limit,
        enrich=enrich,
        fresh=fresh,
    )
    if not table:
        _echo(out)
        return
    typer.echo(f"{len(out['companies'])} companies from {len(out['queries'])} searches")
    typer.echo("")
    for c in out["companies"]:
        f = c.get("funding") or {}
        money = " / ".join(x for x in (f.get("stage"), f.get("amount_raw")) if x) or "funding not stated"
        typer.secho(f"{c['name']}  [{c.get('confidence', 'inferred')}]", bold=True)
        typer.echo(f"  region   {c.get('region', '-')}")
        typer.echo(f"  site     {c.get('website') or '(not resolved)'}")
        typer.echo(f"  funding  {money}")
        if f.get("investors"):
            typer.echo(f"  backers  {', '.join(f['investors'])}")
        roles = c.get("open_roles") or []
        typer.echo(f"  hiring   {', '.join(roles) if roles else (c.get('careers_url') or 'no signal found')}")
        typer.echo(f"  source   {c.get('announcement_url', '')}")
        typer.echo("")


@research_app.command("cache")
def research_cache(purge: bool = typer.Option(False, help="Delete entries older than the TTL")) -> None:
    """Research cache stats, or purge stale entries."""
    from jobhunter.research import cache

    _echo({"deleted": cache.purge()} if purge else cache.stats())


# ---------------------------------------------------------------- models & spend

models_app = typer.Typer(add_completion=False, help="OpenRouter — aliases, spend, and a live check")
app.add_typer(models_app, name="models")


@models_app.command("status")
def models_status() -> None:
    """Key, aliases, caps and what has been spent today."""
    _setup_logging()
    from jobhunter import llm

    _echo(llm.health())


@models_app.command("check")
def models_check(
    alias: str = typer.Option(None, help="Verify one alias; omit to verify every configured alias"),
    live: bool = typer.Option(False, "--live", help="Actually spend one tiny call per alias"),
) -> None:
    """Verify the configured model ids exist, and optionally that they answer.

    Without --live this costs nothing: it only checks the ids against OpenRouter's
    public model list. With --live it spends a handful of tokens per alias.
    """
    _setup_logging()
    from jobhunter import openrouter

    resolved = openrouter.resolve_aliases()
    if "error" in resolved:
        typer.secho(f"could not reach the model list: {resolved['error']}", fg=typer.colors.YELLOW)
    else:
        for name, info in resolved.items():
            mark = "OK  " if info["available"] and info["free"] else "MISS"
            tags = []
            if info["free"]:
                tags.append("free")
            else:
                tags.append("PAID")
            if info.get("json_mode"):
                tags.append("json")
            if info.get("context"):
                tags.append(f"{info['context']:,} ctx")
            line = f"[{mark}] {name:<7} {info['model']:<44} {', '.join(tags)}"
            if not info["available"]:
                line += "   <- not on openrouter.ai/models"
            typer.secho(line, fg=None if info["free"] else typer.colors.YELLOW)
    if not live:
        typer.echo("")
        typer.echo("(no call made — pass --live to actually spend a few tokens)")
        return

    typer.echo("")
    for name in ([alias] if alias else list(openrouter.ALIASES)):
        result = openrouter.verify(name)
        if result["ok"]:
            typer.secho(
                f"[OK  ] {name:<7} {result['model']}  {result['tokens_in']}+{result['tokens_out']} tok"
                f"  ₹{result['cost_inr']:.4f}  {result['latency_ms']}ms  -> {result['text'][:40]!r}",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(f"[FAIL] {name:<7} {result['error']}", fg=typer.colors.RED)


@models_app.command("list")
def models_list(
    query: str = typer.Argument("", help="Substring of a model id"),
    limit: int = 20,
    free: bool = typer.Option(False, "--free", help="Only zero-cost models, widest context first"),
) -> None:
    """Search OpenRouter's catalogue. Costs nothing and needs no key.

    `--free` is the one to use when picking aliases: the free roster changes as
    models come and go, and `json` in the output is what this pipeline needs.
    """
    from jobhunter import openrouter

    out = openrouter.models(query, limit=limit, free=free)
    if out.get("error"):
        typer.secho(out["error"], fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"{out['matched']} of {out['total']} models match {query!r}")
    typer.echo("")
    zero_but_unsuffixed = False
    for m in out["models"]:
        costs_nothing = not (m["prompt_usd_per_m"] or m["completion_usd_per_m"])
        if costs_nothing and not m["free"]:
            # zero-priced but without the `:free` suffix free_only keys off, so the
            # guard would still refuse it — say so rather than showing "$0.000"
            price, zero_but_unsuffixed = "free *", True
        elif costs_nothing:
            price = "free"
        else:
            price = f"${m['prompt_usd_per_m'] or 0:.3f}/${m['completion_usd_per_m'] or 0:.3f} per Mtok"
        ctx = f"{m['context']:,}" if m["context"] else "?"
        typer.echo(f"  {m['id']:<50} {ctx:>11} ctx  {'json' if m['json_mode'] else '    '}  {price}")
    if zero_but_unsuffixed:
        typer.echo("")
        typer.echo("  * costs nothing, but has no `:free` suffix — openrouter.free_only"
                   " refuses it anyway, since the guard reads the id, not a price list.")


@models_app.command("costs")
def models_costs(
    days: int = typer.Option(1, help="Look back this many days"),
    month: bool = typer.Option(False, "--month", help="Calendar month to date instead"),
) -> None:
    """What the model layer has cost, by alias and by stage."""
    from jobhunter import openrouter

    _echo({"spend": openrouter.spend(days=days, month=month), "budget": openrouter.budget_status()})


@app.command()
def fit_explain(job_id: int) -> None:
    """Show why the free lexical gate scored one job the way it did."""
    _setup_logging()
    from jobhunter import fit
    from jobhunter.db import Job, get_session

    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            typer.secho(f"no job {job_id}", fg=typer.colors.RED)
            raise typer.Exit(1)
        blob = "\n".join([job.title, job.company_name, (job.description or "")[:6000]])
        title, stored = job.title, job.embed_sim
    out = fit.explain(blob)
    typer.secho(f"{title}  —  score {out['score']}  (stored {stored})", bold=True)
    typer.echo("  matched: " + ", ".join(f"{m['term']}({m['weight']})" for m in out["matched"]))
    typer.echo("  missing: " + ", ".join(f"{m['term']}({m['weight']})" for m in out["missing"]))


# ---------------------------------------------------------------- targeting

targets_app = typer.Typer(
    add_completion=False,
    help="Company targeting — underrated, recently funded, stipend ₹50k+/month, PPO tiers",
)
app.add_typer(targets_app, name="targets")


def _tier_colour(tier: str | None) -> str:
    return {
        "tier1": typer.colors.GREEN, "tier2": typer.colors.CYAN,
        "prospect": typer.colors.BLUE, "unknown": typer.colors.YELLOW,
    }.get(tier or "", typer.colors.RED)


@targets_app.command("grade")
def targets_grade(
    pay: bool = typer.Option(True, help="Re-read stipend / PPO out of job descriptions first"),
    limit: int = typer.Option(None, help="Only this many companies"),
) -> None:
    """Grade every company: hyped -> reject, then funding, then the stipend bar and the tiers."""
    _setup_logging()
    from jobhunter import targeting

    if pay:
        typer.echo(f"job pay pass: {targeting.extract_job_pay()}")
    counts = targeting.grade_companies(limit=limit)
    for tier in ("tier1", "tier2", "prospect", "unknown", "reject"):
        typer.secho(f"  {tier:9} {counts.get(tier, 0):4}", fg=_tier_colour(tier))
    typer.echo(f"  (of the rejects, {counts.get('hyped_excluded', 0)} are hyped names)")


@targets_app.command("list")
def targets_list(
    tier: str = typer.Option(None, help="tier1 | tier2 | prospect | unknown | reject"),
    limit: int = typer.Option(25),
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """The graded registry, best first."""
    _setup_logging()
    from jobhunter import targeting

    rows = targeting.targets(tier=tier, limit=limit)
    if json_:
        _echo(rows)
        return
    for r in rows:
        pay = f"₹{r['ppo_lpa']:g} LPA" if r["ppo_lpa"] else "pay unknown"
        stipend = f" · stipend ₹{r['stipend_inr_month']:,}/mo" if r["stipend_inr_month"] else ""
        hiring = f" · hiring:{r['hiring_status']}" if r["hiring_status"] else ""
        typer.secho(f"  [{r['tier'] or '-'}] {r['name']}", fg=_tier_colour(r["tier"]), bold=True)
        typer.echo(f"        {pay}{stipend} · {r['region'] or 'region unknown'}{hiring}")
        if r["description"]:
            typer.echo(f"        {r['description'][:100]}")
        typer.echo(f"        why: {(r['reason'] or '')[:110]}")


@targets_app.command("enrich")
def targets_enrich(
    limit: int = typer.Option(10, help="How many companies to research"),
    company_id: int = typer.Option(None, help="Just this one"),
    fresh: bool = typer.Option(False, help="Bypass the research cache"),
    model: bool = typer.Option(True, help="Use the `cheap` alias for the description"),
) -> None:
    """Read each company's own site: what they do, and who funded them. Re-grades after."""
    _setup_logging()
    from jobhunter import enrich

    results = ([enrich.enrich_company(company_id, fresh=fresh, use_model=model)]
               if company_id else
               enrich.enrich_pending(limit=limit, fresh=fresh, use_model=model))
    for r in results:
        if r.get("error"):
            typer.secho(f"  {r['error']}", fg=typer.colors.RED)
            continue
        typer.secho(f"  {r['company']} -> {r.get('tier')}", fg=_tier_colour(r.get("tier")), bold=True)
        typer.echo(f"      {r.get('description') or 'no description found'}")
        if r.get("funding"):
            typer.echo(f"      funding: {r['funding']}")


@targets_app.command("verify")
def targets_verify(
    limit: int = typer.Option(10),
    company_id: int = typer.Option(None, help="Verify one company"),
    job_id: int = typer.Option(None, help="Verify the company behind one scraped posting"),
    role: str = typer.Option(None, help="Test a specific claimed role"),
    fresh: bool = typer.Option(False, help="Bypass the page cache"),
) -> None:
    """Is the hiring claim real? Check the company's own ATS board and careers page.

    A claim their own pages do not back is marked `not_authorized` and never drafted on.
    """
    _setup_logging()
    from jobhunter import hiring_verify as hv

    if job_id:
        results = [hv.claim_from_job(job_id)]
    elif company_id:
        results = [hv.verify_company(company_id, claimed_role=role, fresh=fresh)]
    else:
        results = hv.verify_pending(limit=limit, fresh=fresh)

    colours = {
        hv.VERIFIED: typer.colors.GREEN, hv.ROLE_MISSING: typer.colors.YELLOW,
        hv.NOT_AUTHORIZED: typer.colors.RED, hv.UNREACHABLE: typer.colors.MAGENTA,
    }
    for r in results:
        if r.get("error"):
            typer.secho(f"  {r['error']}", fg=typer.colors.RED)
            continue
        typer.secho(f"  {r['company']}: {r['status']}", fg=colours.get(r["status"]), bold=True)
        typer.echo(f"      {(r.get('evidence') or '')[:160]}")
        if r.get("roles"):
            typer.echo(f"      roles on their own page: {', '.join(r['roles'][:5])}")


# ---------------------------------------------------------------- tick (launchd)

tick_app = typer.Typer(
    add_completion=False,
    help="The unattended pipeline: an hourly free tick and a daily paid tick under launchd, on a schedule and on wake",
)
app.add_typer(tick_app, name="tick")


@tick_app.command("run")
def tick_run(kind: str = typer.Option("free", help="free | paid | all")) -> None:
    """Run one bounded slice now (what launchd calls). Takes the lock; skips if another tick is running."""
    _setup_logging()
    from jobhunter import tick

    _echo(tick.run(kind))


@tick_app.command("install")
def tick_install() -> None:
    """Write and load both LaunchAgents: com.zonuly.tick.free (hourly + on wake) and .paid (daily 06:15 + on login)."""
    from jobhunter import tick

    _echo(tick.install())


@tick_app.command("uninstall")
def tick_uninstall() -> None:
    """Unload and remove both LaunchAgents."""
    from jobhunter import tick

    _echo(tick.uninstall())


@tick_app.command("status")
def tick_status() -> None:
    """Are the agents loaded, when did each last run, and what did it do."""
    from jobhunter import tick

    _echo(tick.status())


# ---------------------------------------------------------------- outreach

outreach_app = typer.Typer(
    add_completion=False,
    help="The back half: gated drafts -> your approval -> send (dry-run or Gmail) -> replies -> calendar -> what works",
)
app.add_typer(outreach_app, name="outreach")


@outreach_app.command("bets")
def outreach_bets(limit: int = typer.Option(10, help="One draft per ready-to-ask company")) -> None:
    """Draft one gated email per company that is ready to ask (best lead, best role). Goes to the queue."""
    _setup_logging()
    from jobhunter.outreach import drafter

    _echo(drafter.draft_for_bets(limit=limit))


@outreach_app.command("send")
def outreach_send(
    limit: int = typer.Option(None, help="At most this many, inside the daily cap"),
    ignore_window: bool = typer.Option(False, help="Send outside the 10:00–19:00 window (rehearsals only)"),
    no_stagger: bool = typer.Option(False, help="Skip the human-ish spacing (rehearsals only)"),
) -> None:
    """Send what you approved. outreach.send_mode decides dry-run (outbox/) or Gmail."""
    _setup_logging()
    from jobhunter.outreach import sender

    _echo(sender.send_approved(limit=limit, ignore_window=ignore_window, stagger=not no_stagger))


@outreach_app.command("ledger")
def outreach_ledger_cmd() -> None:
    """Today's send ledger: cap, used, guessed-address cap."""
    from jobhunter.outreach import ledger, sender

    _echo({"send_mode": sender.SEND_MODE, **ledger.status()})


@outreach_app.command("reply")
def outreach_reply(
    email_id: int = typer.Argument(..., help="The sent email the reply belongs to"),
    body: str = typer.Argument(..., help="The reply text, pasted"),
    sender: str = typer.Option(None, help="Their address; defaults to the email's recipient"),
) -> None:
    """Record a reply by hand: classified, and scheduled if it carries a yes with a time."""
    _setup_logging()
    from jobhunter.outreach import tracker

    _echo(tracker.record_reply(email_id, body, sender=sender or "them"))


@outreach_app.command("events")
def outreach_events(upcoming: bool = typer.Option(False)) -> None:
    """Every call, assessment and interview on record, with the sentence it was read from."""
    from jobhunter.outreach import schedule

    _echo(schedule.list_events(upcoming_only=upcoming))


@outreach_app.command("confirm")
def outreach_confirm(event_id: int) -> None:
    """Confirm a proposed time; it goes on the calendar when the OAuth client is in place."""
    _setup_logging()
    from jobhunter.outreach import schedule

    _echo(schedule.confirm(event_id))


@outreach_app.command("learn")
def outreach_learn(days: int = typer.Option(None, help="Only sends in the last N days")) -> None:
    """Reply and yes rates by who we asked, what they can pay, segment, region, source, framing."""
    from jobhunter.outreach import learn

    _echo(learn.report(days=days))


# ---------------------------------------------------------------- harvest

harvest_app = typer.Typer(
    add_completion=False,
    help="Bulk company harvest — N different companies that fit, then their boards, roles and grade",
)
app.add_typer(harvest_app, name="harvest")


@harvest_app.command("yc")
def harvest_yc(limit: int = typer.Option(None, help="Newest batches first; default all that fit")) -> None:
    """Admit every hiring YC company that fits (region, size, not hyped) from the open yc-oss mirror."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.admit_yc(limit=limit).as_dict())


@harvest_app.command("exa")
def harvest_exa(
    query: list[str] = typer.Option(None, "--query", "-q", help="Repeatable; default is harvest.exa_queries"),
    per_query: int = typer.Option(25, help="Company sites per search (25 is the cheap tier)"),
    fresh: bool = typer.Option(False, help="Bypass the 24h cache"),
) -> None:
    """Company sites from Exa's company index — one search per query, inside exa_daily_cap."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.admit_exa(query or None, per_query=per_query, fresh=fresh).as_dict())


@harvest_app.command("x")
def harvest_x(
    query: list[str] = typer.Option(None, "--query", "-q", help="Repeatable; default is harvest.x_queries"),
    per_query: int = typer.Option(20, help="Posts per search"),
    fresh: bool = typer.Option(False, help="Bypass the 24h cache"),
) -> None:
    """Hiring posts on X (throwaway session, capped) -> companies with the post as their hiring record."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.admit_x(query or None, per_query=per_query, fresh=fresh).as_dict())


@harvest_app.command("probe")
def harvest_probe(limit: int = typer.Option(None), concurrency: int = typer.Option(6)) -> None:
    """Find each new company's public ATS board. A live board = hiring verified."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.probe_ats(limit=limit, concurrency=concurrency))


@harvest_app.command("roles")
def harvest_roles(
    limit: int = typer.Option(None),
    all_boards: bool = typer.Option(False, "--all", help="Re-read boards that already have jobs"),
) -> None:
    """Every open role from each discovered board, through the normal relevance and location gates."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.scrape_roles(limit=limit, only_without_jobs=not all_boards))


@harvest_app.command("facts")
def harvest_facts(limit: int = typer.Option(60, help="Companies to look up today (one Exa search each, inside exa_daily_cap)")) -> None:
    """Company facts from Exa's company index — what they do, headcount, HQ, founded, round — for companies with roles."""
    _setup_logging()
    from jobhunter import enrich
    from jobhunter.research import web

    res = enrich.facts_pending(limit=limit)
    _echo({"companies": len(res), "described": sum(1 for r in res if r.get("description")),
           "team": sum(1 for r in res if r.get("team_size")), "funding": sum(1 for r in res if r.get("funding")),
           "errors": sum(1 for r in res if r.get("error")), "exa": web.exa_budget()})


@harvest_app.command("story")
def harvest_story(
    limit: int = typer.Option(300, help="Companies to read tonight"),
    what: str = typer.Option("story", help="'description' for unread sites, 'story' for the About-page origin"),
) -> None:
    """Read each company's own site (home + About) for description, origin story, funding, valuation, team. No Exa."""
    _setup_logging()
    from jobhunter import enrich

    res = enrich.enrich_pending(limit=limit, use_search=False, missing=what)
    _echo({"companies": len(res), "described": sum(1 for r in res if r.get("description")),
           "story": sum(1 for r in res if r.get("story")), "funding": sum(1 for r in res if r.get("funding")),
           "valuation": sum(1 for r in res if r.get("valuation_usd_m")), "errors": sum(1 for r in res if r.get("error"))})


@harvest_app.command("people")
def harvest_people(
    limit: int = typer.Option(200, help="How many companies to search this run"),
    tiers: str = typer.Option("tier1,tier2,prospect,unknown", help="Comma-separated, best first"),
    any_roles: bool = typer.Option(False, "--any", help="Also companies with no scraped roles yet"),
) -> None:
    """Who could refer us — the free waterfall (GitHub, site, pattern) over fitting companies, best tier first."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.find_people(limit=limit, tiers=tuple(t.strip() for t in tiers.split(",") if t.strip()),
                              require_roles=not any_roles))


@harvest_app.command("parallel")
def harvest_parallel(minutes: int = typer.Option(30, help="How long to run the four channels")) -> None:
    """Run all four channels at once (keyless · Exa · OpenRouter · scrape.do), each a different stage, no duplicates."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.parallel_run(minutes=minutes))


@harvest_app.command("levels")
def harvest_levels(limit: int = typer.Option(40, help="Companies to look up on levels.fyi")) -> None:
    """Read levels.fyi pay (via scrape.do) for companies with roles and no stated figure."""
    _setup_logging()
    from jobhunter import levels

    _echo(levels.lookup_pending(limit=limit))


@harvest_app.command("status")
def harvest_status() -> None:
    """How many different companies fit, and how far each has got."""
    from jobhunter import harvest

    _echo(harvest.status())


@harvest_app.command("run")
def harvest_run(
    target: int = typer.Option(500, help="How many different fitting companies we are after"),
    yc_limit: int = typer.Option(None),
    exa: bool = typer.Option(True, help="Top up from Exa when YC alone falls short"),
) -> None:
    """The whole scrape: companies -> roles + hiring proof -> description, story, pay (Pay Power) -> people who can refer."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.run(target=target, yc_limit=yc_limit, exa=exa))


# ---------------------------------------------------------------- people

people_app = typer.Typer(
    add_completion=False,
    help="People targeting — who at a company can actually refer us, best first",
)
app.add_typer(people_app, name="people")


@people_app.command("classify")
def people_classify(
    all_: bool = typer.Option(False, "--all", help="Re-classify everyone, not just the unlabelled"),
    model: bool = typer.Option(False, help="Send the residue the rules can't place to `cheap`"),
    limit: int = typer.Option(None),
) -> None:
    """Label every contact: founder · senior engineer · engineer · EM · tech HR · recruiter."""
    _setup_logging()
    from jobhunter.contacts import roles

    stats = roles.classify_contacts(limit=limit, only_missing=not all_, use_model=model)
    scanned = stats.pop("scanned", 0)
    calls = stats.pop("model_calls", 0)
    typer.echo(f"  {scanned} contacts, {calls} model call(s)")
    for key in sorted(stats, key=lambda k: roles.RANKS.get(k, 9)):
        typer.echo(f"    {roles.RANKS.get(key, 9)} {key:16} {stats[key]}")


@people_app.command("queue")
def people_queue(
    company_id: int = typer.Option(None, help="Only this company"),
    limit: int = typer.Option(25),
    json_: bool = typer.Option(False, "--json"),
) -> None:
    """The referral queue: who to ask first, and why we think they can refer."""
    _setup_logging()
    from jobhunter.contacts import roles

    rows = roles.referral_queue(company_id=company_id, limit=limit)
    if json_:
        _echo(rows)
        return
    for r in rows:
        typer.secho(f"  {r['rank']} {r['label'] or r['role_class']}", fg=typer.colors.CYAN, nl=False)
        typer.echo(f" — {r['name'] or 'unnamed'} <{r['email'] or 'no address'}> [{r['confidence']}]")
        if r["evidence"]:
            typer.echo(f"      read from: {r['evidence']}")


if __name__ == "__main__":
    app()
