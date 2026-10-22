#!/usr/bin/env python
"""JobHunter CLI.

    python scripts/run.py doctor         # check OpenRouter, budget, Gmail, tokens, DB
    python scripts/run.py profile        # parse resume -> profile.json
    python scripts/run.py scrape         # run the scraper fleet
    python scripts/run.py score          # lexical gate + rubric-score
    python scripts/run.py find-contacts  # discover contacts at high-match companies
    python scripts/run.py draft          # queue referral drafts for review
    python scripts/run.py gmail-verify   # check the app password signs in (gmail-auth for OAuth)
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

    python scripts/run.py audit run                    # the hourly scrape audit, now (audit install = launchd)
    python scripts/revalidate.py                       # re-run the guardrails over stored data (dry run)
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
    from jobhunter import secret
    gh_token = bool(secret("GITHUB_TOKEN") or (CONFIG.get("contacts") or {}).get("github_token"))

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
        ("GitHub token", gh_token, "optional: set GITHUB_TOKEN in .env (60/hr -> 5000/hr)"),
        ("Hunter.io key", hunter.available(), "optional: set contacts.hunter_api_key (25 lookups/mo)"),
        (f"Gmail credentials ({gmail.AUTH})", gmail.configured(), gmail.status()["hint"]),
        (f"Gmail sign-in as {gmail.ADDRESS or '?'}", gmail.authorized(),
         "run `python scripts/run.py gmail-verify`" if gmail.AUTH == "app_password" else "run `python scripts/run.py gmail-auth`"),
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


@app.command(name="gmail-verify")
def gmail_verify() -> None:
    """Sign in to Gmail with the app password and report whether Google accepts it."""
    from jobhunter.outreach import gmail

    _echo({"from": gmail.from_header(), **gmail.verify(force=True)})


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
    from jobhunter.outreach import followup, tracker

    _echo(tracker.poll())
    if followups:
        _echo(followup.queue())
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


@app.command(name="add-company")
def add_company_cmd(
    name: str = typer.Argument(..., help="Company name"),
    website: str = typer.Option(None, help="Their site, which sharpens the board guess"),
    ats: str = typer.Option(None, help="greenhouse | lever | ashby, when you already know"),
    slug: str = typer.Option(None, help="Board token from the careers-page URL"),
    scrape: bool = typer.Option(True, help="Pull the board straight away"),
) -> None:
    """Add one company: find its job board, seed companies.yaml, pull its roles."""
    _setup_logging()
    from jobhunter.onboard import AddError, add_company

    try:
        _echo(add_company(name, website, ats_hint=ats, ats_slug=slug, scrape_now=scrape))
    except AddError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)


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


# ---------------------------------------------------------------- actors (our Apify)

actor_app = typer.Typer(add_completion=False, help="Actors — our own Apify: LinkedIn guest pages, cached, paced, breaker-guarded")
app.add_typer(actor_app, name="actor")


def _actor_done(row: dict, show: int = 10) -> None:
    from jobhunter import actors

    _echo({k: row[k] for k in ("run_id", "status", "error", "items", "network_calls", "cache_hits", "stats", "dataset")})
    for x in actors.read_dataset(row["run_id"])[:show]:
        typer.echo("  " + json.dumps({k: v for k, v in x.items() if k not in ("description", "about", "employee_profiles")},
                                     default=str, ensure_ascii=False)[:400])


@actor_app.command("list")
def actor_list() -> None:
    """The actors this machine can run."""
    from jobhunter import actors

    for name, fn in actors.ACTORS.items():
        typer.echo(f"{name:22} {(fn.__doc__ or '').strip().splitlines()[0]}")


@actor_app.command("jobs")
def actor_jobs(
    keywords: str,
    location: str = typer.Option(None, help='e.g. "India", "Bengaluru", "Worldwide"'),
    remote: bool = typer.Option(False, help="Remote roles only (f_WT=2)"),
    days: int = typer.Option(None, help="Posted in the last N days"),
    experience: str = typer.Option(None, help="internship,entry,associate,mid-senior"),
    max_items: int = typer.Option(25, "--max"),
    details: bool = typer.Option(False, help="Open each job for description, seniority, applicants"),
    fresh: bool = typer.Option(False, help="Bypass the page cache"),
) -> None:
    """LinkedIn job search through the logged-out endpoint."""
    _setup_logging()
    from jobhunter import actors

    _actor_done(actors.run("linkedin-jobs", fresh=fresh, keywords=keywords, location=location, remote=remote,
                           posted_days=days, experience=experience, max_items=max_items, details=details))


@actor_app.command("company")
def actor_company(companies: list[str], fresh: bool = False) -> None:
    """Public company pages, by slug or URL."""
    _setup_logging()
    from jobhunter import actors

    _actor_done(actors.run("linkedin-company", fresh=fresh, companies=companies))


@actor_app.command("profile")
def actor_profile(urls: list[str], fresh: bool = False) -> None:
    """Public profiles: current and former employers, schools, location."""
    _setup_logging()
    from jobhunter import actors

    _actor_done(actors.run("linkedin-profile", fresh=fresh, urls=urls))


@actor_app.command("employees")
def actor_employees(
    company_id: int = typer.Argument(..., help="Company id in jobhunter.db"),
    linkedin: str = typer.Option(None, help="The company's LinkedIn slug or URL (exact match + its employee links)"),
    max_profiles: int = typer.Option(15, "--max"),
    exa: bool = typer.Option(False, help="Also spend one metered Exa search for candidates"),
    save: bool = typer.Option(True, "--save/--no-save", help="Store the people verified as there NOW as contacts (through guardrails)"),
    fresh: bool = False,
) -> None:
    """Who works at a company now — discovered, then verified on each public profile."""
    _setup_logging()
    from jobhunter import actors
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.db import Company, Contact, get_session, init_db
    from sqlmodel import select

    init_db()
    with get_session() as s:
        c = s.get(Company, company_id)
        if c is None:
            raise typer.BadParameter(f"no company {company_id}")
        name, domain, city = c.name, c.domain, c.city
        known = [k.linkedin for k in s.exec(select(Contact).where(Contact.company_id == company_id)).all()
                 if k.linkedin and "linkedin.com/in/" in k.linkedin]
    row = actors.run("linkedin-employees", fresh=fresh, company=name, linkedin=linkedin, domain=domain,
                     city=city, max_profiles=max_profiles, exa=exa, extra_urls=known)
    _actor_done(row)
    if save:
        people = [{"name": x["name"], "url": x["linkedin_url"],
                   "role": x.get("headline") or (x.get("job_titles") or [""])[0] or "",
                   "headline": x.get("headline")}
                  for x in actors.read_dataset(row["run_id"]) if x.get("works_there_now") and x.get("name")]
        typer.echo(f"saved {lp._save(company_id, people, 'actor:linkedin-employees') if people else 0} new contacts")


@actor_app.command("people")
def actor_people(
    company_id: int = typer.Argument(None, help="Company id in jobhunter.db (fills name, domain, website, city, GitHub org)"),
    name: str = typer.Option(None, help="Or: any company by name, not in the DB"),
    domain: str = typer.Option(None), website: str = typer.Option(None), city: str = typer.Option(None),
    linkedin: str = typer.Option(None, help="The company's LinkedIn slug or URL — enables the company page, exact matching and Apify"),
    source: list[str] = typer.Option(None, "--source", help="Repeatable. Default: known linkedin-company search jobs team yc github; opt-in: exa apify"),
    title: list[str] = typer.Option(None, "--title", help="Keep only these title words (repeatable)"),
    exclude_title: list[str] = typer.Option(None, "--exclude-title"),
    location: list[str] = typer.Option(None, "--location"),
    max_items: int = typer.Option(100, "--max"),
    max_verify: int = typer.Option(15, help="Public profiles opened to confirm current employment (paced)"),
    no_verify: bool = typer.Option(False, help="Skip opening profiles"),
    include_former: bool = typer.Option(False),
    apify_max: int = typer.Option(50, help="People-tab rows to buy from Apify when --source apify"),
    apify_mode: str = typer.Option("short", help="short ($4/1k) | full ($8/1k) | email ($12/1k)"),
    save: bool = typer.Option(True, "--save/--no-save", help="Store verified/likely-current LinkedIn people as contacts so the dashboard shows them (default on with a company_id, 2026-10-19)"),
    fresh: bool = False,
) -> None:
    """The LinkedIn People tab for a company — without our LinkedIn account."""
    _setup_logging()
    from jobhunter import actors
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.db import Company, Contact, get_session, init_db
    from sqlmodel import select

    known: list[dict] = []
    gh_org = None
    if company_id is not None:
        init_db()
        with get_session() as s:
            c = s.get(Company, company_id)
            if c is None:
                raise typer.BadParameter(f"no company {company_id}")
            name, domain = name or c.name, domain or c.domain
            website, city, gh_org = website or c.website, city or c.city, c.github_org
            known = [{"name": k.name, "role": k.role, "linkedin": k.linkedin, "github": k.github,
                      "confidence": k.confidence}
                     for k in s.exec(select(Contact).where(Contact.company_id == company_id)).all()]
    if not name:
        raise typer.BadParameter("give a company_id or --name")
    row = actors.run("company-people", fresh=fresh, company=name, domain=domain, website=website, linkedin=linkedin,
                     city=city, github_org=gh_org, sources=source or None, titles=title or None,
                     exclude_titles=exclude_title or None, locations=location or None, max_items=max_items,
                     verify=not no_verify, max_verify=max_verify, include_former=include_former, known=known,
                     apify_max=apify_max, apify_mode=apify_mode)
    _echo({k: row[k] for k in ("run_id", "status", "error", "items", "network_calls", "stats", "dataset")})
    for p in actors.read_dataset(row["run_id"])[:40]:
        typer.echo(f"  {p['status']:17} {p['role_class'] or '':13} {(p['name'] or '')[:26]:26} "
                   f"{(p['title'] or p['headline'] or '')[:44]:44} {','.join(p['sources'])}")
    if save and company_id is not None:
        keep = [{"name": p["name"], "url": p["linkedin_url"], "role": p["title"] or p["headline"] or "",
                 "headline": p["headline"] or p["title"]}
                for p in actors.read_dataset(row["run_id"])
                if p["linkedin_url"] and p["status"] in ("verified-current", "likely-current")]
        typer.echo(f"saved {lp._save(company_id, keep, 'actor:company-people') if keep else 0} new contacts")


@actor_app.command("fill")
def actor_fill(
    limit: int = typer.Option(5, help="How many unfilled companies this run"),
    company_id: int = typer.Option(None, help="Fill just this company"),
    max_verify: int = typer.Option(10, help="Public profiles opened per company (paced)"),
    apify: bool = typer.Option(False, help="Also buy the People tab from Apify (APIFY_TOKEN, $ capped)"),
    apify_max: int = typer.Option(25),
    count: bool = typer.Option(False, help="Only print how many companies have no current referrer"),
) -> None:
    """Fill companies that have no current referrer: find people, store them, apply the address format."""
    _setup_logging()
    from jobhunter.actors import fill

    if count:
        _echo({"unfilled": len(fill.unfilled())})
        return
    rows = [fill.fill_company(company_id, max_verify=max_verify, apify=apify, apify_max=apify_max)] if company_id \
        else fill.fill(limit, max_verify=max_verify, apify=apify, apify_max=apify_max)
    for r in rows:
        typer.echo(f"{(r.get('company') or '')[:28]:28} found {r.get('found', 0):>3}  new {r.get('new', 0):>3}  "
                   f"saved {r.get('saved')}  referrers now {r.get('referrers_now')}  li={(r.get('linkedin') or {}).get('slug')}")
    _echo([{k: r.get(k) for k in ("company", "run_id", "sources", "addresses")} for r in rows])


@actor_app.command("apify")
def actor_apify() -> None:
    """Apify source: token present, today's spend against the cap."""
    from jobhunter.actors import apify_source

    _echo(apify_source.status())


@actor_app.command("runs")
def actor_runs(limit: int = 15) -> None:
    """The run ledger, newest first."""
    from jobhunter import actors

    for r in actors.recent_runs(limit):
        typer.echo(f"{r['finished_at'][:19]}  {r['status']:9} {r['items']:>4} items  {r['network_calls']:>3} calls  "
                   f"{r['run_id']}  {(r.get('error') or '')[:70]}")


@actor_app.command("show")
def actor_show(run_id: str, limit: int = 50) -> None:
    """A run's dataset."""
    from jobhunter import actors

    for x in actors.read_dataset(run_id)[:limit]:
        typer.echo(json.dumps(x, default=str, ensure_ascii=False))


@actor_app.command("status")
def actor_status() -> None:
    """Per-route breaker state and today's request count."""
    from jobhunter import actors

    _echo(actors.host_status())


@actor_app.command("reset")
def actor_reset(route: str = typer.Argument(..., help='e.g. "www.linkedin.com/in"')) -> None:
    """Clear a route's cooldown by hand. Only after the wall is gone — check in a browser first."""
    from jobhunter import actors

    actors.reset_host(route)
    _echo(actors.host_status())


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
        for name, chain in resolved.items():
            for rank, info in enumerate(chain):
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
                label = name if rank == 0 else f"  {rank + 1}."
                line = f"[{mark}] {label:<7} {info['model']:<44} {', '.join(tags)}"
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
def tick_run(kind: str = typer.Option("free", help="free | paid | all | send | apify")) -> None:
    """Run one bounded slice now (what launchd calls). Takes the lock; skips if another tick is running."""
    _setup_logging()
    from jobhunter import tick

    _echo(tick.run(kind))


@tick_app.command("install")
def tick_install() -> None:
    """Write and load the LaunchAgents: com.zonuly.tick.free (every 10 min + on wake), .paid (daily 06:15 + on login),
    .send (every 5 min: approved mail whose warm-up slot is due), com.zonuly.referrals (the referral job, every
    15 min, its own lock), plus the hourly audit."""
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


# ---------------------------------------------------------------- referrals

referrals_app = typer.Typer(
    add_completion=False,
    help="Referrers first: the people step, address patterns from evidence, re-verification, the complete bar",
)
app.add_typer(referrals_app, name="referrals")


@referrals_app.command("measure")
def referrals_measure() -> None:
    """Ready referrers per company, with an address, the ★ list, complete companies."""
    from jobhunter import referrals

    _echo(referrals.measure())


@referrals_app.command("patterns")
def referrals_patterns(apply: bool = typer.Option(False, "--apply", help="back up, then write (default: dry run)")) -> None:
    """Learn each company's address format from its own addresses (≥2 agree, or 1 + their page) and
    address the ready referrers without one. Dry run by default."""
    _setup_logging()
    from jobhunter import referrals

    out: dict = {}
    if apply:
        out["backup"] = str(referrals.backup("pre-patterns"))
    out.update(referrals.pattern_step(dry_run=not apply))
    _echo(out)


@referrals_app.command("people")
def referrals_people(
    limit: int = typer.Option(50, help="companies"),
    list_key: str = typer.Option(None, "--list", help="only this hand-picked list, e.g. india-star"),
    top: int = typer.Option(None, help="the list rows plus the top N by priority"),
    budget: int = typer.Option(3600, help="seconds"),
) -> None:
    """The people step (GitHub, team pages via Jina, the open web, LinkedIn via keyless search)."""
    _setup_logging()
    from jobhunter import referrals

    _echo(referrals.people_step(limit, budget_s=budget, list_key=list_key, top=top))


@referrals_app.command("run")
def referrals_run(
    budget: int = typer.Option(None, help="seconds (default: referrals.cycle_seconds, 1200)"),
    limit: int = typer.Option(None, help="companies (default: referrals.cycle_companies)"),
    exclude: str = typer.Option(None, help="a JSON file with {'ids': [...]} or a comma list of company ids to leave alone"),
) -> None:
    """One cycle of the referral job — referral work ONLY: Bengaluru (★ first) → Hyderabad → other India →
    remote; public LinkedIn search results, team pages, GitHub (budgeted), public mentions, then the learned
    address format. Its own lock (data/referrals.lock); launchd runs it every 15 min (com.zonuly.referrals)."""
    _setup_logging()
    import json as _json
    from pathlib import Path as _P

    from jobhunter import tick

    ex: set[int] = set()
    if exclude:
        if _P(exclude).exists():
            raw = _json.loads(_P(exclude).read_text())
            ex = {int(x) for x in (raw.get("ids") if isinstance(raw, dict) else raw)}
        else:
            ex = {int(x) for x in exclude.split(",") if x.strip()}
    kw: dict = {}
    if budget:
        kw["budget_s"] = budget
    if limit:
        kw["limit"] = limit
    if ex:
        kw["exclude"] = ex
    _echo(tick.run_referrals(**kw))


@referrals_app.command("reverify")
def referrals_reverify(limit: int = typer.Option(150)) -> None:
    """Re-confirm ready referrers whose evidence is older than 90 days; failing → unconfirmed."""
    _setup_logging()
    from jobhunter import referrals

    _echo(referrals.reverify_referrers(limit))


@referrals_app.command("revalidate")
def referrals_revalidate(limit: int = typer.Option(30)) -> None:
    """Re-check companies (never-checked first): site answers, description passes, careers lists roles."""
    _setup_logging()
    from jobhunter import guardrails, referrals
    from jobhunter.db import get_session

    out = referrals.revalidate_companies(limit)
    with get_session() as s:
        out["complete"] = {k: v for k, v in guardrails.refresh_complete(s).items() if k != "became_ids"}
    _echo(out)


# ---------------------------------------------------------------- audit

audit_app = typer.Typer(
    add_completion=False,
    help="The hourly audit: fill rates now vs 1h/24h ago, throughput, rejects, channel health, stall alerts",
)
app.add_typer(audit_app, name="audit")


@audit_app.command("run")
def audit_run(
    quiet: bool = typer.Option(False, help="no macOS notification on an alert"),
    full: bool = typer.Option(False, "--json", help="print the whole report"),
) -> None:
    """Run one audit now (what launchd calls every hour) and store the report."""
    _setup_logging()
    from jobhunter import audit

    r = audit.run(notify=not quiet)
    if full:
        _echo(r)
        return
    f = r["fill"]
    typer.secho(f"audit {r['at']} — {r['status'].upper()}  ({f['companies']} active companies, "
                f"avg completeness {f['avg_completeness']}%)", bold=True)
    dh, dd = r.get("delta_hour") or {}, r.get("delta_day") or {}
    for k, v in f["rates"].items():
        extra = "".join(f"  {lbl} {d[k] * 100:+.1f}" for lbl, d in (("1h", dh), ("24h", dd)) if k in d)
        typer.echo(f"  {k:<22} {v * 100:5.1f}%{extra}")
    typer.echo(f"last hour: {json.dumps({k: v for k, v in r['last_hour'].items() if k != 'writes_by_field'})}")
    typer.echo(f"rejects (1h): {r['rejects']['total']}  {json.dumps(dict(list(r['rejects']['by_reason'].items())[:6]))}")
    typer.echo(f"sample: {r['sample']['passed']}/{r['sample']['checked']} still pass")
    for ch, h in (r["channels"]["research"] or {}).items():
        typer.echo(f"  channel {ch:<10} hour ok {h['hour'].get('ok') or 0:>4} fail {h['hour'].get('fail') or 0:>4}"
                   f"  last ok {h.get('last_ok_at') or '-'}")
    o = r["channels"]["openrouter"]
    typer.echo(f"  openrouter      hour ok {o['ok']:>4} fail {o['fail']:>4}  daily ceiling hit: {o['daily_ceiling_hit']}")
    for a in r["alerts"]:
        typer.secho(f"ALERT {a}", fg="red")


@audit_app.command("show")
def audit_show() -> None:
    """The last stored report and how many hours of history are kept."""
    from jobhunter import audit

    d = audit.latest()
    _echo({"last": d["last"], "history_hours": len(d["history"])})


@audit_app.command("install")
def audit_install() -> None:
    """Write and load com.zonuly.audit (every hour)."""
    from jobhunter import audit

    _echo(audit.install())


@audit_app.command("uninstall")
def audit_uninstall() -> None:
    from jobhunter import audit

    _echo(audit.uninstall())


@audit_app.command("status")
def audit_status() -> None:
    from jobhunter import audit

    _echo(audit.status())


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


@outreach_app.command("preflight")
def outreach_preflight(
    as_json: bool = typer.Option(False, "--json", help="The raw report"),
    no_sign_in: bool = typer.Option(False, help="Do not sign in to Gmail (offline check)"),
) -> None:
    """Is everything in place for live sending? Red / amber / green, with the exact fix for each."""
    from jobhunter.outreach import preflight

    report = preflight.run(sign_in=not no_sign_in)
    if as_json:
        _echo(report)
        return
    typer.echo(preflight.text(report))
    typer.echo("\nBefore Monday, you:")
    for i, step in enumerate(report["user_checklist"], 1):
        typer.echo(f"  {i}. {step}")


@outreach_app.command("batch")
def outreach_batch(
    n: int = typer.Option(None, help="How many drafts (default: the warm-up week minus what is approved or in review)"),
    dry_run: bool = typer.Option(False, help="Only show who would be written to"),
    no_research: bool = typer.Option(False, help="Skip reading the person's public repos"),
    no_model: bool = typer.Option(False, help="Write every draft with the deterministic template (marked for polish)"),
) -> None:
    """Build the week's drafts — ★ India first, then the best complete companies, one ask per
    company — into the review queue. Nothing is approved: you read and approve each one."""
    _setup_logging()
    from jobhunter import proposals
    from jobhunter.outreach import batch

    if not dry_run:
        proposals.ensure_briefs([t["job_id"] for t in batch.plan(limit=200)["targets"]])
    out = batch.build(n, dry_run=dry_run, research=not no_research, use_model=not no_model)
    if dry_run:
        for t in out.get("would_draft", []):
            typer.echo(f"{t['company'][:28]:28} {(t['person'] or '?')[:24]:24} {t['role'] or '':16} "
                       f"{t['address_confidence']:15} {t['affiliation']}")
        out = {k: v for k, v in out.items() if k != "would_draft"}
    _echo(out)


@outreach_app.command("proposal")
def outreach_proposal(
    n: int = typer.Option(10, help="How many proposals (spread across structures and India / Remote)"),
    no_model: bool = typer.Option(False, help="Template only; skip the model polish"),
    show: bool = typer.Option(False, help="Only print the stored proposals, do not regenerate"),
) -> None:
    """PROPOSALS: how the pipeline would write to the best targets — evidence, structure,
    subjects, the full email, quality and flags, what is not claimed. Never drafts, approves
    or sends; the dashboard shows them at /outreach/proposal."""
    _setup_logging()
    from jobhunter import proposals

    if not show:
        _echo(proposals.build(n, use_model=not no_model))
    d = proposals.latest()
    for i in d["items"]:
        head = f"#{i['rank']} {i['company']['name']} · {i['recipient']['name']} ({i['recipient']['role']}) · " \
               f"{i['market']} · {i['structure']['id']}"
        typer.echo("\n" + "=" * 100 + "\n" + head)
        if not i.get("complete"):
            typer.echo("NOT WRITTEN — " + "; ".join(i.get("missing") or []))
            continue
        typer.echo(f"quality {i['quality']['score']} ({i['quality']['verdict']}) · writer {i['writer']} · "
                   f"hook {i['hook']['type']} ({i['hook']['source']})")
        typer.echo(f"Subject: {i['subject']}\n\n{i['body']}")


@outreach_app.command("polish")
def outreach_polish(limit: int = typer.Option(10)) -> None:
    """Re-write template drafts with the model now that it is available (they stay drafts)."""
    _setup_logging()
    from jobhunter.outreach import drafter

    _echo(drafter.polish_pending(limit))


@outreach_app.command("track")
def outreach_track(status: str = typer.Option(None), sentiment: str = typer.Option(None)) -> None:
    """Every cold email and where it stands: sent, waiting (days, follow-up due), replied
    (positive / neutral / negative / closed), bounced, opted out."""
    from jobhunter.outreach import tracker

    t = tracker.track(status=status, sentiment=sentiment)
    _echo(t["counts"])
    for r in t["rows"]:
        typer.echo(f"{r['email_id']:5} {(r['company_name'] or '')[:24]:24} {(r['contact_name'] or '')[:20]:20} "
                   f"{r['stage']:10} {r['sentiment'] or '':9} {r['sent_at'] or '':20} "
                   f"{'due ' + r['followup_due'] if r['followup_due'] else ''}")


@outreach_app.command("week")
def outreach_week(start: str = typer.Option(None, help="YYYY-MM-DD; default the warm-up start")) -> None:
    """The warm-up week: each day's quota, and what the review queue holds for it."""
    from jobhunter.outreach import batch

    _echo(batch.week(start))


@outreach_app.command("send-due")
def outreach_send_due() -> None:
    """What the send agent does every 5 minutes: send the approved mail whose slot is due."""
    _setup_logging()
    from jobhunter.outreach import sender

    _echo(sender.send_due())


@outreach_app.command("followups")
def outreach_followups(
    after_days: int = typer.Option(None, help="Silent this many days (default outreach.followup_after_days)"),
    no_model: bool = typer.Option(False, help="Use the fixed template, not the model"),
) -> None:
    """Draft one follow-up per silent thread into the review queue."""
    _setup_logging()
    from jobhunter.outreach import followup

    _echo(followup.queue(after_days=after_days, use_model=not no_model))


@outreach_app.command("rehearse-week")
def outreach_rehearse_week(
    start: str = typer.Option("2026-10-19", help="The Monday to start on"),
    days: int = typer.Option(9, help="Calendar days to simulate (9 = Mon..next Tue, so follow-ups show)"),
) -> None:
    """The whole week on a COPY of the database (sqlite backup → data/backups/): warm-up caps,
    jittered slots, a bounce, an opt-out, a yes, follow-ups after 5 days. Live DB untouched."""
    from jobhunter.outreach import rehearse

    out = rehearse.run_on_copy(start=start, days=days)
    typer.echo(rehearse.table(out))
    _echo({k: v for k, v in out.items() if k not in ("days",)})


@outreach_app.command("unsuppress")
def outreach_unsuppress(
    company: int = typer.Option(None, help="Company id"), contact: int = typer.Option(None, help="Contact id"),
) -> None:
    """Clear a do-not-contact flag set by an opt-out (only when they asked to hear from you again)."""
    from jobhunter.db import Company, Contact, get_session

    with get_session() as session:
        for model, rid in ((Company, company), (Contact, contact)):
            if rid:
                row = session.get(model, rid)
                if row is not None:
                    row.do_not_contact, row.dnc_reason = None, None
                    if model is Contact and row.email_status == "opted_out":
                        row.email_status = None
                    session.add(row)
        session.commit()
    _echo({"cleared": {"company": company, "contact": contact}})


@outreach_app.command("learn")
def outreach_learn(days: int = typer.Option(None, help="Only sends in the last N days")) -> None:
    """Reply and yes rates by who we asked, what they can pay, segment, region, source, framing."""
    from jobhunter.outreach import learn

    _echo(learn.report(days=days))


# ---------------------------------------------------------------- harvest

# ---------------------------------------------------------------- lists

lists_app = typer.Typer(help="Hand-picked company lists (data/lists/*.yaml) — ★ India and the like.")
app.add_typer(lists_app, name="lists")


@lists_app.command("ls")
def lists_ls() -> None:
    """Every list on disk and how much of it is attached to the database."""
    from jobhunter import lists

    for l in lists.available():
        typer.echo(f"  {l['key']:14} {l['name']:12} {l['attached']}/{l['companies']} attached — {l['title'] or ''}")


@lists_app.command("show")
def lists_show(key: str = typer.Argument("india-star"), json_: bool = typer.Option(False, "--json")) -> None:
    """The list in its own order, with roles and people found so far."""
    from jobhunter import lists

    data = lists.rows(key)
    if json_:
        _echo(data)
        return
    s = data["stats"]
    typer.secho(f"{data['name']} — {data['title']}", bold=True)
    typer.echo(f"  {s['companies']} companies · {s['with_website']} with a site · {s['with_roles']} with roles · "
               f"{s['with_people']} with people ({s['people']} found, {s['reachable']} could refer) · {s['ready']} ready")
    for r in data["companies"]:
        best = r["people"]["best"]
        ask = f"ask {best['name'] or best['email']} ({best['label']})" if best else "no one yet"
        site = "" if r["website"] else f"  [no website: {r['website_note']}]"
        typer.echo(f"  {r['rank']:>2} {r['name']:20} {r['city'] or '':10} {r['stage'] or '':16} "
                   f"{r['roles']['total']:>3} roles {r['people']['total']:>3} people — {ask}{site}")


@lists_app.command("sync")
def lists_sync(key: str = typer.Argument("india-star"),
               no_verify: bool = typer.Option(False, "--no-verify", help="Skip fetching guessed websites")) -> None:
    """Attach the list's companies to database rows; verify guessed websites first."""
    _setup_logging()
    from jobhunter import lists

    _echo(lists.sync(key, verify_sites=not no_verify))


@lists_app.command("audit")
def lists_audit(key: str = typer.Argument("india-star"),
                dry: bool = typer.Option(False, "--dry", help="Report only; clear nothing")) -> None:
    """Re-verify every GitHub org on the list; a wrong org loses its contacts."""
    _setup_logging()
    from jobhunter import lists

    _echo(lists.audit_orgs(key, fix=not dry))


@lists_app.command("people")
def lists_people(
    key: str = typer.Argument("india-star"),
    no_linkedin: bool = typer.Option(False, "--no-linkedin", help="Skip the public-LinkedIn search (slow, gapped)"),
    refresh_days: int = typer.Option(0, help="Skip companies searched within N days; 0 = every company"),
    limit: int = typer.Option(None, help="Only the first N in list order"),
    linkedin_only: bool = typer.Option(False, "--linkedin-only", help="Just the public-LinkedIn pass"),
    emails_only: bool = typer.Option(False, "--emails-only",
                                     help="The address pass: web search for any @domain address, founders/HR, fill by pattern"),
    x_handles: bool = typer.Option(False, "--x-handles", help="X handles for each company's best people (DM route)"),
) -> None:
    """Boards, roles and referrers for every company on the list, in list order."""
    _setup_logging()
    from jobhunter import lists

    out = lists.find_people(key, linkedin=not no_linkedin, refresh_days=refresh_days, limit=limit,
                            linkedin_only=linkedin_only, emails_only=emails_only, x_handles=x_handles)
    lines = out.pop("lines", [])
    for l in lines:
        if x_handles:
            typer.echo(f"  {l['name']:22} searched {l.get('searched', 0)}  handles +{l.get('new', 0)} (have {l.get('found', 0)})"
                       + ("  " + ", ".join("@" + h["handle"] for h in l.get("handles", [])) if l.get("handles") else ""))
            continue
        if emails_only:
            typer.echo(f"  {l['name']:22} addresses {l.get('addresses', '-'):>3}  founders +{l.get('founders', 0)}  "
                       f"by pattern +{l.get('by_pattern', 0)}  reachable {l.get('reachable_before')} → {l.get('reachable_after')}"
                       f"{'  pattern ' + l['pattern'] if l.get('pattern') else ''}")
            continue
        typer.echo(f"  {l['name']:22} roles +{l.get('roles', 0)}  people +{l.get('people', 0)} "
                   f"(verified {l.get('verified', 0)})  linkedin +{l.get('linkedin', 0)}"
                   + (f"  ! {l['linkedin_error']}" if l.get('linkedin_error') else ""))
    _echo(out)


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
def harvest_probe(limit: int = typer.Option(None), concurrency: int = typer.Option(6),
                  list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)")) -> None:
    """Find each new company's public ATS board. A live board = hiring verified."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.probe_ats(limit=limit, concurrency=concurrency, list_key=list_key))


@harvest_app.command("roles")
def harvest_roles(
    limit: int = typer.Option(None),
    all_boards: bool = typer.Option(False, "--all", help="Re-read boards that already have jobs"),
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
) -> None:
    """Every open role from each discovered board, through the normal relevance and location gates."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.scrape_roles(limit=limit, only_without_jobs=not all_boards, list_key=list_key))


@harvest_app.command("careers")
def harvest_careers(
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
    limit: int = typer.Option(None, help="Companies to read, list rows first; default all pending"),
    force: bool = typer.Option(False, "--force", help="Read again even if read recently or in backoff"),
    concurrency: int = typer.Option(4, help="Companies read at once"),
    no_search: bool = typer.Option(False, "--no-search", help="Skip the keyless web-search fallback"),
) -> None:
    """Find each company's careers page (homepage links, common paths, keyless search), the hosted board
    behind it (Keka, Darwinbox, Zoho Recruit, Workable, Lever …) and the open roles on it — board JSON,
    JSON-LD, or the page text read by the model (titles kept only if literally on the page)."""
    _setup_logging()
    from jobhunter import careers

    out = careers.run(limit=limit, list_key=list_key, force=force,
                      concurrency=concurrency, use_search=not no_search)
    for l in out.pop("lines", []):
        typer.echo(f"  {(l.get('company') or str(l.get('id')))[:24]:24} "
                   f"{(l.get('careers_url') or '-')[:60]:60} {l.get('ats') or '':12} "
                   f"roles {l.get('found', 0):>3} (+{l.get('inserted', 0)})"
                   + (f"  ! {l['error']}" if l.get("error") else ""))
    _echo(out)


@harvest_app.command("facts")
def harvest_facts(
    limit: int = typer.Option(60, help="Companies to look up today (one Exa search each, inside exa_daily_cap)"),
    keyless: bool = typer.Option(False, "--keyless", help="Use only the hosted Exa MCP (free, rate-limited)"),
    roles_only: bool = typer.Option(False, "--roles-only", help="Only companies that already have roles"),
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
) -> None:
    """Company facts — what they do, round, amount, date, total, valuation, HQ, website — best priority first,
    with or without roles. Skips companies in backoff after a miss."""
    _setup_logging()
    from jobhunter import enrich
    from jobhunter.research import web

    res = enrich.facts_pending(limit=limit, keyless=keyless, roles_only=roles_only, list_key=list_key)
    _echo({"companies": len(res), "described": sum(1 for r in res if r.get("description")),
           "team": sum(1 for r in res if r.get("team_size")), "funding": sum(1 for r in res if r.get("funding")),
           "valuation": sum(1 for r in res if r.get("valuation_usd_m")),
           "errors": sum(1 for r in res if r.get("error")), "exa": web.exa_budget()})


@harvest_app.command("websites")
def harvest_websites(
    limit: int = typer.Option(100, help="Name-only companies to find a website for, best priority first"),
    paid: bool = typer.Option(False, "--paid", help="Let the search fall back to the metered Exa API"),
) -> None:
    """Find and verify the website of companies that arrived as a bare name (funding feeds, X, directories):
    search + domain guesses, kept only when the page names the company. Misses back off (1, 3, 7, 21, 60 days)."""
    _setup_logging()
    from jobhunter import enrich

    _echo(enrich.find_websites(limit=limit, keyless=not paid))


@harvest_app.command("scrape-first")
def harvest_scrape_first(dry_run: bool = typer.Option(False, "--dry-run", help="Count, write nothing")) -> None:
    """Flag the companies we hold nothing about (no site, description, roles or people) so every
    fill-in pass takes them first; clear the flag on the ones that have been filled. Runs every tick."""
    _setup_logging()
    from jobhunter import hygiene

    _echo(hygiene.mark_empty(dry_run=dry_run))


@harvest_app.command("priority")
def harvest_priority(dry_run: bool = typer.Option(False, "--dry-run", help="Compute and report, write nothing")) -> None:
    """Regrade overrated + priority score + city for every company (bulk, seconds)."""
    _setup_logging()
    from jobhunter import targeting

    _echo(targeting.refresh_priority(dry_run=dry_run))


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
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
    topup_days: int = typer.Option(7, help="List rows short of good referrers are searched again after N days"),
) -> None:
    """Who could refer us — the free waterfall (GitHub, site, pattern) over fitting companies, list rows first.
    List rows with fewer than ~5 people who can refer are topped up (public LinkedIn too)."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.find_people(limit=limit, tiers=tuple(t.strip() for t in tiers.split(",") if t.strip()),
                              require_roles=not any_roles, list_key=list_key, topup_days=topup_days))


@harvest_app.command("verify")
def harvest_verify(
    limit: int = typer.Option(60, help="Companies to check, list rows first"),
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
    fresh: bool = typer.Option(False, help="Bypass the page cache"),
) -> None:
    """Hiring verification against the company's own board / careers page. With --list, every list row
    not yet verified is checked again."""
    _setup_logging()
    from jobhunter import hiring_verify as hv

    res = hv.verify_pending(limit=limit, tiers=("tier1", "tier2", "prospect", "unknown"), fresh=fresh,
                            list_key=list_key)
    counts: dict = {}
    for r in res:
        counts[r.get("status") or "error"] = counts.get(r.get("status") or "error", 0) + 1
    _echo({"checked": len(res), "by_status": counts})


@harvest_app.command("linkedin")
def harvest_linkedin(
    limit: int = typer.Option(30, help="Companies to search, list rows first"),
    list_key: str = typer.Option(None, "--list", help="Only companies on this list (e.g. india-star)"),
) -> None:
    """Public-LinkedIn leads (no login): companies with roles and no leads, and list rows short of referrers."""
    _setup_logging()
    from jobhunter.contacts import linkedin_public

    _echo(linkedin_public.find_pending(limit=limit, list_key=list_key))


@harvest_app.command("parallel")
def harvest_parallel(minutes: int = typer.Option(30, help="How long to run the four channels")) -> None:
    """Run all four channels at once (keyless · Exa · OpenRouter · scrape.do), each a different stage, no duplicates."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.parallel_run(minutes=minutes))


@harvest_app.command("discover")
def harvest_discover(
    topic: list[str] = typer.Option(None, "--topic", "-t", help="Repeatable; default is the eight standard topics"),
    max_searches: int = typer.Option(30, help="Cap on Exa searches this run"),
) -> None:
    """Find new funded startups from the funding press (via Exa) and admit the ones that fit."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.discover_funded(topics=topic or None, max_searches=max_searches).as_dict())


@harvest_app.command("india-jobs")
def harvest_india_jobs(
    force: bool = typer.Option(True, "--force/--if-due", help="Run now, or only when india_jobs.every_hours has passed"),
    searches: int = typer.Option(None, help="LinkedIn search pages (default india_jobs.searches_per_run)"),
    page_reads: int = typer.Option(None, help="LinkedIn job pages to read"),
    company_reads: int = typer.Option(None, help="LinkedIn company pages to read"),
    lead_searches: int = typer.Option(None, help="LinkedIn searches by company name (seeds + leads)"),
    community: int = typer.Option(None, help="Keyless offer-post searches"),
    source: list[str] = typer.Option(None, "--source", "-s", help="linkedin | jobfound | instahyre | community"),
    city: list[str] = typer.Option(None, "--city", help="Bengaluru | Hyderabad"),
    lead: list[str] = typer.Option(None, "--lead", help="Queue a company name for the LinkedIn name search"),
    last: bool = typer.Option(False, "--last", help="Print the last run's report and exit"),
) -> None:
    """Bengaluru / Hyderabad companies found by their postings: LinkedIn Jobs (public, no
    login), jobfound, Instahyre, and community offer posts as leads (india_jobs.py)."""
    _setup_logging()
    from jobhunter import india_jobs

    if last:
        _echo(india_jobs.last_report())
        return
    if lead:
        from jobhunter.db import get_session, init_db

        init_db()
        with get_session() as session:
            for name in lead:
                india_jobs.add_lead(session, name, "manual")
    kw = {"sources": tuple(source)} if source else {}
    if city:
        kw["cities"] = tuple(city)
    _echo(india_jobs.run(force=force, searches=searches, page_reads=page_reads, company_reads=company_reads,
                         lead_searches=lead_searches, community=community, **kw))


@harvest_app.command("discover-parallel")
def harvest_discover_parallel(minutes: int = typer.Option(20)) -> None:
    """Three discovery branches at once (Exa · RSS · scrape.do), each finding new funded companies."""
    _setup_logging()
    from jobhunter import harvest

    _echo(harvest.discover_parallel(minutes=minutes))


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


@people_app.command("emails")
def people_emails(company_id: int = typer.Argument(..., help="The company to find addresses at")) -> None:
    """Any real address at one company from the open web, then fill everyone by pattern."""
    _setup_logging()
    from jobhunter.contacts import fill_by_pattern, web_emails

    out = web_emails.find(company_id)
    out["by_pattern"] = fill_by_pattern(company_id)
    _echo(out)


@people_app.command("x-handles")
def people_x_handles(company_id: int = typer.Argument(..., help="The company whose people to look up"),
                     limit: int = typer.Option(5, help="Searches to spend — best referral rank first")) -> None:
    """X handles for a company's best people — the DM route when there is no address."""
    _setup_logging()
    from jobhunter.contacts import x_handles

    _echo(x_handles.find_for_company(company_id, limit=limit))


@people_app.command("x-backfill")
def people_x_backfill(list_key: str = typer.Option(None, "--list", help="Only companies on this list"),
                      limit: int = typer.Option(400)) -> None:
    """X handles from the GitHub profiles of contacts already on record."""
    _setup_logging()
    from sqlmodel import select

    from jobhunter.contacts import x_handles
    from jobhunter.db import Company, get_session

    ids = None
    if list_key:
        with get_session() as s:
            ids = list(s.exec(select(Company.id).where(Company.list_key == list_key)).all())
    _echo(x_handles.backfill_github(ids, limit=limit))


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


# ---------------------------------------------------------------- India: pay floor + discovery

india_app = typer.Typer(help="India: the ₹20 LPA pay floor and Bengaluru / Hyderabad startup discovery (india.py).")
app.add_typer(india_app, name="india")


@india_app.command("pay-floor")
def india_pay_floor(dry_run: bool = typer.Option(False, "--dry-run", help="Report only; write nothing")) -> None:
    """Hide (status below_pay_bar) or bring back every India company by the ₹20L floor."""
    _setup_logging()
    from jobhunter import india

    _echo(india.apply_pay_floor(dry_run=dry_run))


@india_app.command("discover")
def india_discover(
    limit: int = typer.Option(30, help="New companies to admit this run"),
    searches: int = typer.Option(6, help="Keyless web searches"),
    dir_pages: int = typer.Option(1, help="Directory pages to read"),
    rss_pages: int = typer.Option(1, help="Pages of each paged feed (backfill)"),
    rss_reads: int = typer.Option(10, help="Articles to read for items whose feed text names no city"),
    ats: int = typer.Option(1, help="Careers-board searches"),
    budget: int = typer.Option(420, help="Seconds"),
    force: bool = typer.Option(False, help="Ignore the hourly feed / daily YC gates"),
    sources: str = typer.Option("rss,directory,yc,search,ats", help="Comma-separated subset"),
) -> None:
    """Admit new underrated Bengaluru / Hyderabad startups through the admission gate."""
    _setup_logging()
    from jobhunter import india

    _echo(india.discover(limit, searches=searches, dir_pages=dir_pages, rss_pages=rss_pages, rss_reads=rss_reads,
                         ats_queries=ats, budget_s=budget, force=force,
                         sources=tuple(x.strip() for x in sources.split(",") if x.strip())))


# ---------------------------------------------------------------- selection confidence + needs briefs

selection_app = typer.Typer(help="Selection confidence per role (confidence.py) and needs briefs (needs.py)", no_args_is_help=True)
app.add_typer(selection_app, name="selection")


@selection_app.command("recompute")
def selection_recompute(all_: bool = typer.Option(False, "--all", help="Re-score every role, changed or not")) -> None:
    """Score roles whose inputs changed (or all of them), then roll up the best role per company."""
    _setup_logging()
    from jobhunter import confidence

    _echo(confidence.recompute(force=all_))
    _echo(confidence.distribution())


@selection_app.command("top")
def selection_top(market: str = typer.Option("india", help="india | remote | abroad"),
                  limit: int = typer.Option(15), json_: bool = typer.Option(False, "--json")) -> None:
    """The best bets in one market: score, band, top reasons, main blocker."""
    _setup_logging()
    from sqlmodel import col, select

    from jobhunter import confidence
    from jobhunter.db import Company, Job, get_session

    with get_session() as s:
        rows = s.exec(select(Job).where(Job.selection_market == market, col(Job.selection_score).is_not(None))
                      .order_by(col(Job.selection_score).desc(), col(Job.id)).limit(limit)).all()
        out = [confidence.role_row(j, s.get(Company, j.company_id) if j.company_id else None) for j in rows]
    if json_:
        _echo(out)
        return
    for r in out:
        typer.secho(f"  {r['score']:3} {r['band_label']:9} {r['company_name']} — {r['title']} (job {r['id']})", bold=True)
        for reason in r["top_reasons"]:
            typer.echo(f"        + {reason[:120]}")
        if r["main_blocker"]:
            typer.echo(f"        - {r['main_blocker'][:120]}")


@selection_app.command("briefs")
def selection_briefs(market: str = typer.Option(None, help="india | remote (default: both)"),
                     top: int = typer.Option(50, help="Top N roles per market"),
                     synth: int = typer.Option(100, help="Model syntheses allowed this run (0 = deterministic only)"),
                     web: int = typer.Option(100, help="Companies whose public context may be fetched"),
                     refresh: bool = typer.Option(False, help="Rebuild fresh briefs too")) -> None:
    """Build needs briefs for the best roles, within the model and search budgets."""
    _setup_logging()
    from jobhunter import confidence

    _echo(confidence.build_briefs(market=market, top=top, synth=synth, web=web, refresh=refresh))


@selection_app.command("brief")
def selection_brief(job_id: int, json_: bool = typer.Option(False, "--json")) -> None:
    """One role's selection breakdown and needs brief."""
    _setup_logging()
    from jobhunter import needs
    from jobhunter.db import Company, Job, get_session

    with get_session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise typer.Exit(1)
        company = s.get(Company, job.company_id) if job.company_id else None
        brief = needs.brief_for(job, company)
        why = json.loads(job.selection_why) if job.selection_why else {}
    if json_:
        _echo({"selection": why, "brief": brief})
        return
    typer.secho(f"{job.title} @ {brief['company']} — {why.get('score')} {why.get('band_label')} ({why.get('market')})", bold=True)
    for t in why.get("terms") or []:
        typer.echo(f"  {t['label']:26} {t['points']:5}/{t['weight']:g}  {t['why'][:100]}")
    for b in why.get("blockers") or []:
        typer.secho(f"  BLOCKER cap {b['cap']}: {b['why']}", fg=typer.colors.RED)
    typer.echo(f"\n  coverage {brief['coverage']}  synthesis={brief.get('synthesis')}")
    for n in (brief.get("implied") or []) + (brief.get("stated") or []):
        mark = {"covered": "✓", "partial": "~", "uncovered": "✗"}.get(n["status"], "?")
        typer.echo(f"  {mark} [{n['kind']}] {n['text'][:90]}")
        if n.get("evidence"):
            typer.echo(f"      ← {n['evidence'][:100]} ({n.get('evidence_where')})")
    typer.echo(f"\n  helps: {brief.get('helps_model') or brief.get('helps')}")
    for a in brief.get("angles") or []:
        typer.echo(f"  angle: {a}")


# ---------------------------------------------------------------- notify (the mail bot)

notify_app = typer.Typer(
    add_completion=False,
    help="Mail to you: positive replies, URGENT alerts, the morning digest; reply with approve/reject/pause/resume/status",
)
app.add_typer(notify_app, name="notify")


@notify_app.command("test")
def notify_test(dry: bool = typer.Option(False, "--dry", help="write outbox/notify/*.eml instead of sending")) -> None:
    """Send ONE test notification now (even with notify.email off). Reply `status` to it to test the loop."""
    _setup_logging()
    from jobhunter import notify_bot

    _echo(notify_bot.test_mail(force_dry=dry))


@notify_app.command("digest")
def notify_digest(dry: bool = typer.Option(True, "--dry/--send"), print_: bool = typer.Option(False, "--print")) -> None:
    """Build today's digest (default: write the .eml; --send mails it). --print shows the text only."""
    _setup_logging()
    from jobhunter import notify_bot

    if print_:
        typer.echo(notify_bot.digest_text())
        return
    _echo(notify_bot.daily_digest(force=True, force_dry=dry))


@notify_app.command("watchdog")
def notify_watchdog() -> None:
    """Run the watchdog's checks and print the issues (sends nothing, kills nothing)."""
    _setup_logging()
    from jobhunter import watchdog

    _echo(watchdog.check(kill=False))


@notify_app.command("cycle")
def notify_cycle(dry: bool = typer.Option(True, "--dry/--send")) -> None:
    """One bot cycle now, as the send tick runs it (default --dry: mails become .eml files)."""
    _setup_logging()
    from jobhunter import notify_bot

    _echo(notify_bot.cycle(force=True, force_dry=dry))


@notify_app.command("commands")
def notify_commands() -> None:
    """Read your replies to bot mails now (IMAP), run their commands, confirm by reply."""
    _setup_logging()
    from jobhunter import notify_bot

    _echo(notify_bot.handle_commands())


@notify_app.command("status")
def notify_status() -> None:
    """What `status` replies with."""
    from jobhunter import notify_bot

    typer.echo(notify_bot.status_text())


if __name__ == "__main__":
    app()
