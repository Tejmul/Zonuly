"""The fill pass — go to a company and scrape it COMPLETELY (2026-10-14).

Tejmul, seeing the ★ India list full of "no description" / "no hiring proof": "If you go
to a company, scrape it COMPLETELY. First complete the Indian companies, then the remote
companies." The description path depended on the model, which has been out (the free
OpenRouter daily ceiling) for days, and hiring proof was tied to the Bengaluru/Hyderabad
engineering roles we store. This pass needs no model at all.

PER COMPANY, in this order
    description  candidates read off their own site — <meta name=description>,
                 og:description, twitter:description, JSON-LD Organization.description,
                 the first substantial paragraph — then the same on their /about page, then
                 the "About us" of their public LinkedIn company page (guest, through Jina).
                 Each goes through guardrails.description; the best that passes is stored
                 with its URL. Only when none passes: the hand-picked list's `what` and a
                 short line quoted from their site, combined, each part with its source in
                 the provenance. The model may polish later; it is never needed.
    linkedin     the public company page: size band, members on LinkedIn, HQ, founded year,
                 website (must match theirs), and the guest job search for the company
                 (f_C=<id>) — the count of postings there is a SECOND hiring signal, never
                 proof on its own (guardrails.hiring: proof is their own page or ATS).
    hiring       careers.run_company(use_model=False): their board JSON, JSON-LD, posting
                 links, title headings. ANY open role of any kind or place is proof
                 (hiring_status=verified, titles + URL); the relevant ones are stored as jobs;
                 both counts go on the row (open_roles_count / relevant_roles_count).
    facts        funding through enrich.facts_from_search(keyless) when the row has none.
    referrers    referrals.people_for_company + the learned address format
                 (contacts/addresses.py) — never a guessed address without a learned format.

A part still missing after all that carries the reason in company.fill_state
({part: reason}) — "no careers page anywhere (12 urls)", "site blocks readers", …

ORDER (queue): the ★ list, then every other Bengaluru/Hyderabad company, then the remote
targets by priority. The 10-minute tick runs `run()` (people left to its own people step,
which reads the same order). Layering: pipeline layer — careers, enrich, referrals,
india_jobs, guardrails, research below; tick, api and the CLI above.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import httpx
from sqlmodel import col, select

from jobhunter import CONFIG, admission, backoff, guardrails
from jobhunter.db import Company, Contact, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_F = CONFIG.get("fill") or {}
REFILL_DAYS = int(_F.get("refill_days", 7))          # an incomplete company is looked at again after
COMPLETE_DAYS = int(_F.get("complete_days", 30))     # a complete one is re-read after (hiring goes stale at 30)
LINKEDIN_DAYS = int(_F.get("linkedin_days", 30))
REMOTE_TOP = int(_F.get("remote_top", 300))
TIMEOUT = 20
JINA_WAIT_S = 65
STEP = "fill"
INDIA_CITIES = ("Bengaluru", "Hyderabad")
REMOTE_REGIONS = ("us", "uk", "de", "eu", "nl", "remote")


# ------------------------------------------------------------------ description candidates

@dataclass
class Cand:
    text: str
    kind: str          # jsonld | meta | og | twitter | paragraph | about:* | linkedin | combined
    url: str


_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_LD = re.compile(r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_STRIP_BLOCKS = re.compile(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", re.I | re.S)
_ORG_TYPES = {"organization", "corporation", "localbusiness", "onlinebusiness", "company", "ngo",
              "educationalorganization", "medicalorganization", "newsmediaorganization"}
_META_KINDS = {"description": "meta", "og:description": "og", "twitter:description": "twitter"}


def _clean(text: str | None) -> str:
    t = _html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def meta_candidates(page: str, url: str) -> list[Cand]:
    """<meta name=description>, og:description, twitter:description — the site's own summary."""
    out: list[Cand] = []
    seen: set[str] = set()
    for tag in _META.findall(page or ""):
        attrs = {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _ATTR.finditer(tag)}
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        kind = _META_KINDS.get(key)
        content = _clean(attrs.get("content"))
        if kind and content and content.lower() not in seen:
            seen.add(content.lower())
            out.append(Cand(content, kind, url))
    order = {"meta": 0, "og": 1, "twitter": 2}
    return sorted(out, key=lambda c: order[c.kind])


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def jsonld_candidates(page: str, url: str) -> list[Cand]:
    """The description an Organization declares in its JSON-LD."""
    out: list[Cand] = []
    for raw in _LD.findall(page or ""):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(isinstance(t, str) and t.lower() in _ORG_TYPES for t in types):
                continue
            desc = node.get("description")
            if isinstance(desc, str) and _clean(desc):
                out.append(Cand(_clean(desc), "jsonld", url))
    return out


def paragraph_candidates(page: str, url: str, limit: int = 4) -> list[Cand]:
    """The first substantial paragraphs of the page body (nav, header, footer, scripts out)."""
    body = _STRIP_BLOCKS.sub(" ", page or "")
    out: list[Cand] = []
    for raw in _P.findall(body):
        t = _clean(raw)
        if 60 <= len(t) <= 700 and len(t.split()) >= 10:
            out.append(Cand(t, "paragraph", url))
            if len(out) >= limit:
                break
    return out


def page_candidates(page: str, url: str) -> list[Cand]:
    return jsonld_candidates(page, url) + meta_candidates(page, url) + paragraph_candidates(page, url)


_ABOUT_PATH = re.compile(r"^/(?:about(?:-us)?|company|who-we-are|our-story|story|mission)/?$", re.I)


def about_url(page: str, base: str) -> str | None:
    """Their own About page, read off the homepage's links."""
    from jobhunter.scrapers import careers_ats as ca

    host = (urlparse(base).hostname or "").removeprefix("www.")
    for link, _text in ca.links(page or "", base):
        u = urlparse(link)
        if (u.hostname or "").removeprefix("www.") == host and _ABOUT_PATH.match(u.path or ""):
            return link.split("#")[0]
    return None


_LI_SLUG = re.compile(r"linkedin\.com/company/([A-Za-z0-9][A-Za-z0-9_%.-]{1,80})", re.I)


def linkedin_slug(page: str) -> str | None:
    """The LinkedIn company page their own site links to (the most-linked one)."""
    slugs = Counter(s.rstrip("/.").lower() for s in _LI_SLUG.findall(page or "")
                    if s.lower() not in {"company", "linkedin", "login"})
    return slugs.most_common(1)[0][0] if slugs else None


def _first_sentences(text: str, limit: int = 380) -> str:
    """Whole sentences up to `limit` characters (a LinkedIn About runs to 2,000)."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = ""
    for p in parts:
        if out and len(out) + 1 + len(p) > limit:
            break
        out = (out + " " + p).strip()
    return out or text[:limit]


def combine(name: str, what: str | None, line: str | None) -> str | None:
    """The list's `what` and a short line quoted from their site, as one sentence naming
    the company. Used only when no single candidate passes on its own."""
    what = (what or "").strip().rstrip(".")
    line = (line or "").strip()
    if not what or not line or what.lower() in line.lower():
        return None
    return f"{name} — {what}. {line if line.endswith(('.', '!', '?')) else line + '.'}"


def _rank(c: Cand, ok: guardrails.Check, name: str, domain: str | None) -> tuple:
    named = guardrails.names_company(ok.value, name, domain)
    kind = {"jsonld": 0, "meta": 1, "about:jsonld": 1, "about:meta": 1, "og": 2, "about:og": 2, "linkedin": 3,
            "twitter": 4, "about:twitter": 4, "paragraph": 5, "about:paragraph": 5, "combined": 9}.get(c.kind, 6)
    return (0 if named else 1, kind, abs(len(ok.value) - 200) // 80)


def best_description(cands: list[Cand], *, name: str, domain: str | None,
                     what: str | None = None, what_source: str | None = None) -> tuple[Cand | None, list[str]]:
    """(the best candidate that passes the guardrails, the reasons the others were refused).
    A combined line is built only when nothing passes, from a candidate refused only for
    being short — never from a cookie banner, a headline or a label."""
    passed: list[tuple[tuple, Cand, guardrails.Check]] = []
    refused: list[str] = []
    short: list[Cand] = []
    for c in cands:
        own = c.kind != "combined"
        ok = guardrails.description(c.text, name=name, domain=domain, own_site=own, strict=True)
        if ok:
            passed.append((_rank(c, ok, name, domain), c, ok))
        else:
            refused.append(f"{c.kind}: {ok.reason}")
            if ok.reason.startswith("too short") and len(c.text) >= 15:
                short.append(c)
    if passed:
        passed.sort(key=lambda x: x[0])
        _, c, ok = passed[0]
        return Cand(ok.value, c.kind, c.url), refused
    for c in short:
        text = combine(name, what, c.text)
        if not text:
            continue
        ok = guardrails.description(text, name=name, domain=domain, own_site=False, strict=True)
        if ok:
            return Cand(ok.value, "combined", f"{c.url} + {what_source or 'list'}"), refused
        refused.append(f"combined: {ok.reason}")
    return None, refused


# ------------------------------------------------------------------ fetching

def _get(url: str) -> tuple[str, str | None, str]:
    """(html, final url, problem) — a direct fetch, then Jina's rendered HTML for a JS shell
    or a bot wall."""
    from jobhunter.research import web

    problem = ""
    try:
        r = httpx.get(url, headers={"User-Agent": web.UA, "Accept": "text/html,*/*;q=0.8"},
                      timeout=TIMEOUT, follow_redirects=True)
        if r.status_code < 400 and "html" in r.headers.get("content-type", "html"):
            body = r.text
            if not guardrails.bot_wall(_clean(body[:20000])) and (len(body) > 3000 or "<meta" in body):
                return body, str(r.url), ""
            problem = "bot wall / JS shell"
        else:
            problem = f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        problem = type(e).__name__
    try:
        r = httpx.get(web.JINA + url, headers={"User-Agent": web.JINA_UA, "X-Return-Format": "html"},
                      timeout=45, follow_redirects=True)
        if r.status_code < 400 and r.text and not guardrails.bot_wall(_clean(r.text[:20000])):
            return r.text, url, ""
        problem += f"; Jina HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        problem += f"; Jina {type(e).__name__}"
    return "", None, problem


# ------------------------------------------------------------------ LinkedIn (guest, paced)

_FOUNDED = re.compile(r"(?m)^\s*Founded\s*(?:\n\s*)?(\d{4})\s*$")
_FC = re.compile(r"f_C=(\d{3,12})")
_MEMBERS = re.compile(r"View all ([\d,]+) employees", re.I)
_SEE_JOBS = re.compile(r"\[See jobs\]\((https://www\.linkedin\.com/jobs/[^)\s]+)\)")
_OPEN_ROLES = re.compile(r"\((\d[\d,]*) Open Roles?\)", re.I)
_LOGIN = re.compile(r"^Title:\s*LinkedIn: Log In or Sign Up", re.M)


def parse_linkedin_extra(text: str | None) -> dict:
    """What parse_company_page does not read: founded year, LinkedIn's company id, the
    number of members who list it as their employer."""
    t = text or ""
    out: dict = {"founded": None, "company_id": None, "members": None}
    m = _FOUNDED.search(t)
    if m and 1900 <= int(m.group(1)) <= utcnow().year:
        out["founded"] = int(m.group(1))
    m = _FC.search(t)
    out["company_id"] = m.group(1) if m else None
    m = _MEMBERS.search(t)
    out["members"] = int(m.group(1).replace(",", "")) if m else None
    m = _SEE_JOBS.search(t)
    out["jobs_url"] = re.sub(r"[&?]trk=[^&]*", "", m.group(1)) if m else None
    return out


def parse_jobs_page(text: str | None, name: str) -> int | None:
    """Open roles on the company's public LinkedIn jobs page: the "(25 Open Roles)" in its
    title, else the cards for this company. None for a login wall (not an answer)."""
    from jobhunter.scrapers import india_jobs as ij

    if not text or _LOGIN.search(text[:400]):
        return None
    m = _OPEN_ROLES.search(text[:600])
    if m:
        return int(m.group(1).replace(",", ""))
    key = admission.name_key(name)
    return sum(1 for c in ij.parse_cards(text) if admission.name_key(c.company) == key)


class _LI:
    """One run's LinkedIn reads, through india_jobs' paced reader and its 12-hour backoff."""

    def __init__(self, budget: int):
        from jobhunter import india_jobs

        self.ij, self.budget, self.blocked = india_jobs, budget, None
        self.rep = india_jobs.Report()
        with get_session() as s:
            until = india_jobs._backoff_until(s)
        if until:
            self.blocked = f"LinkedIn backing off until {until:%Y-%m-%d %H:%M}"

    def read(self, url: str, kind: str) -> str | None:
        if self.blocked or self.budget <= 0:
            return None
        self.budget -= 1
        for attempt in (0, 1):
            with get_session() as s:
                try:
                    return self.ij._linkedin(s, self.rep, url, kind)
                except self.ij.Blocked as e:
                    if str(e) == "jina 429" and attempt == 0:
                        time.sleep(JINA_WAIT_S)     # the reader's own per-minute limit: wait it out once
                        continue
                    self.blocked = f"LinkedIn blocked ({e})" if str(e) != "jina 429" else "Jina Reader rate limit (429)"
                    return None
                finally:
                    s.commit()
        return None


def _same_domain(a: str | None, b: str | None) -> bool:
    ra = admission.registrable(admission.host_of(a) or "") if a else None
    rb = admission.registrable(admission.host_of(b) or "") if b else None
    return bool(ra and rb and ra == rb)


def ij_url(slug: str) -> str:
    from jobhunter.scrapers import india_jobs as ij

    return ij.company_url(slug)


def guess_slug(name: str) -> str:
    """'Equal AI' -> 'equal-ai' — LinkedIn's usual vanity slug. Only ever used with the
    page's own Website matching theirs."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def linkedin_company(li: _LI, slug: str, website: str | None, *, vouched: bool = True) -> dict:
    """The guest company page. `vouched`: their own site links it, so the page is theirs
    even when its Website is a redirect or a sister domain; a guessed slug must show their
    own site as its Website."""
    from jobhunter.scrapers import india_jobs as ij

    url = ij.company_url(slug)
    text = li.read(url, "company")
    if not text:
        return {"error": li.blocked or "no page"}
    info = ij.parse_company_page(text)
    if not (info.get("about") or info.get("size")):
        return {"error": "no About section on the guest page"}
    if not vouched and not (website and info.get("website") and _same_domain(info["website"], website)):
        return {"error": f"guessed page's website {info.get('website')} is not theirs"}
    info.update(parse_linkedin_extra(text))
    info["url"] = url
    return info


def linkedin_jobs(li: _LI, jobs_url: str, name: str) -> int | None:
    """Postings on the company's public LinkedIn jobs page ("See jobs" on its company page)."""
    text = li.read(jobs_url, "jobs")
    return parse_jobs_page(text, name) if text is not None else None


# ------------------------------------------------------------------ one company

def _list_what(c: Company) -> str | None:
    try:
        return (json.loads(c.list_meta or "{}") or {}).get("what")
    except (TypeError, ValueError):
        return None


def _desc_ok(c: Company) -> bool:
    return bool(c.description) and bool(guardrails.description(c.description, name=c.name, domain=c.domain,
                                                                strict=False))


def _ready_with_email(cid: int) -> int:
    with get_session() as s:
        rows = s.exec(select(Contact).where(Contact.company_id == cid,
                                            col(Contact.affiliation).in_(guardrails.READY))).all()
        return sum(1 for p in rows if guardrails.is_ready(p) and p.email)


def company(cid: int, *, li: _LI | None = None, people: bool = True, facts: bool = True,
            careers: bool = True) -> dict:
    """Fill one company as far as the evidence goes; record why each missing part is missing."""
    from jobhunter import careers as careers_mod

    init_db()
    with get_session() as s:
        c = s.get(Company, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        name, website, domain = c.name, c.website, c.domain
        what = _list_what(c) if c.list_key else None
        need_desc = not _desc_ok(c)
        li_due = c.linkedin_checked_at is None or utcnow() - c.linkedin_checked_at > timedelta(days=LINKEDIN_DAYS)
        slug_known = (c.linkedin_url or "").rstrip("/").rsplit("/", 1)[-1] or None
    out: dict = {"id": cid, "company": name}
    why: dict[str, str] = {}

    # 1. their own site: description candidates, the About page, the LinkedIn link
    cands: list[Cand] = []
    home, final, problem = ("", None, "no website on file")
    if website:
        home, final, problem = _get(website)
    site = final or website
    if home:
        cands += page_candidates(home, site)
        if need_desc:
            about = about_url(home, site)
            if about:
                page, aurl, _ = _get(about)
                cands += [Cand(x.text, "about:" + x.kind, x.url) for x in page_candidates(page, aurl or about)]
    slug = slug_known or linkedin_slug(home)
    vouched = bool(slug)

    # 2. their public LinkedIn company page (+ the guest job search for them)
    info: dict = {}
    if li is not None and li_due and (li.blocked or li.budget <= 0):
        why["linkedin"] = li.blocked or "not read yet (this run's LinkedIn reads are used up; the tick continues)"
        li_due = False
    if li is not None and li_due and not slug and website:
        slug = guess_slug(name)
    if li is not None and li_due and slug:
        info = linkedin_company(li, slug, website, vouched=vouched)
        if info.get("error"):
            out["linkedin"] = why["linkedin"] = f"{ij_url(slug)}: {info['error']}"
            info = {}
        elif info.get("jobs_url"):
            info["jobs"] = linkedin_jobs(li, info["jobs_url"], name)
    if info.get("about"):
        cands.append(Cand(_first_sentences(info["about"]), "linkedin", info["url"]))

    # 3. write: description, LinkedIn facts
    with get_session() as s:
        c = s.get(Company, cid)
        if need_desc:
            best, refused = best_description(cands, name=name, domain=domain, what=what,
                                             what_source=f"data/lists/{c.list_key}.yaml" if c.list_key else None)
            if best:
                ok = guardrails.set_description(s, c, best.text, source=best.url, channel=f"fill:{best.kind}",
                                                own_site=best.kind != "combined", strict=True)
                if ok and best.kind == "combined":
                    meta = guardrails.meta_of(c, "description")
                    meta.setdefault("flags", []).append("combined")
                    meta["parts"] = [{"text": what, "source": f"data/lists/{c.list_key}.yaml"},
                                     {"text": best.text.split(". ", 1)[-1], "source": best.url.split(" + ")[0]}]
                    data = json.loads(c.field_meta or "{}")
                    data["description"] = meta
                    c.field_meta = json.dumps(data, default=str)
                out["description"] = best.kind if ok else "kept existing"
            else:
                why["description"] = (
                    f"site unreadable ({problem})" if not home and not info.get("about") else
                    "no candidate passed: " + "; ".join(dict.fromkeys(r.split(" (")[0] for r in refused))[:200]
                    if refused else "their site states no description and no LinkedIn About was readable")
                out["description"] = None
        if slug and info.get("url"):
            c.linkedin_url = info["url"]
        if info and not info.get("error"):
            c.linkedin_checked_at = utcnow()
            if info.get("size"):
                c.size_band = info["size"]
            n = info.get("members")
            if n and not c.team_size:
                ev = f"LinkedIn: 'View all {n} employees' ({info.get('size') or 'no band'})"
                guardrails.put(s, c, "team_size", n, evidence=ev, source=info["url"], channel="linkedin-guest",
                               confidence="medium")
                c.team_size_evidence = f"{ev} — {info['url']}"
            if info.get("founded") and not c.founded_year:
                guardrails.put(s, c, "founded_year", info["founded"], evidence=f"Founded {info['founded']}",
                               source=info["url"], channel="linkedin-guest", confidence="high")
            if info.get("hq") and not c.city:
                guardrails.set_city(s, c, f"{name} — Headquarters: {info['hq']}", source=info["url"],
                                    channel="linkedin-guest", own_page=True)
            if info.get("jobs") is not None:
                c.linkedin_jobs = info["jobs"]
            out["linkedin"] = {k: info.get(k) for k in ("size", "members", "hq", "founded", "jobs")}
        elif li is not None and li_due and not slug:
            why["linkedin"] = "no website, and no LinkedIn company page to check against it"
        s.add(c)
        s.commit()

    # 4. hiring proof: their own careers page / ATS — any role counts
    if careers:
        with get_session() as s:
            c = s.get(Company, cid)
            fresh = c.hiring_status == "verified" and c.hiring_checked_at and \
                utcnow() - c.hiring_checked_at < timedelta(days=guardrails.HIRING_STALE_DAYS) and \
                c.open_roles_count is not None
        if not fresh:
            try:
                r = careers_mod.run_company(cid, use_search=True, use_model=False)
            except Exception as e:  # noqa: BLE001
                r = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
            out["hiring"] = {k: r.get(k) for k in ("careers_url", "via", "how", "found", "kept", "error")}
        with get_session() as s:
            c = s.get(Company, cid)
            if c.hiring_status != "verified":
                li_n = c.linkedin_jobs
                tail = f"; LinkedIn lists {li_n} posting(s) (second signal, not their own page)" if li_n else ""
                err = (out.get("hiring") or {}).get("error")
                if err == "no careers page found":
                    why["hiring"] = f"no careers page anywhere on their site or a hosted board{tail}"
                elif err == "site unreachable":
                    why["hiring"] = f"site blocks readers / does not answer{tail}"
                elif err == "no website on file":
                    why["hiring"] = f"no website on file{tail}"
                elif c.careers_url and not guardrails.own_careers_url(c.careers_url, domain=c.domain):
                    host = admission.host_of(c.careers_url)
                    kind = "an aggregator" if guardrails._AGGREGATOR.search(host or "") else "not their domain, no hosted board behind it"
                    why["hiring"] = f"roles only on {host} ({kind}){tail}"
                elif c.careers_url and c.open_roles_count == 0:
                    why["hiring"] = f"careers page {c.careers_url} lists no open role{tail}"
                else:
                    why["hiring"] = (f"careers page could not be read ({err})" if err else
                                     f"hiring status {c.hiring_status}") + tail

    # 5. funding, when the row has none
    if facts:
        from jobhunter import enrich

        with get_session() as s:
            c = s.get(Company, cid)
            need = not (c.funding_stage or c.funding_amount_usd_m or c.funding_total_usd_m) and backoff.due(c, "facts")
        if need:
            try:
                f = enrich.facts_from_search(cid, keyless=True)
                out["facts"] = f.get("error") or f.get("channel") or "read"
            except Exception as e:  # noqa: BLE001
                out["facts"] = f"{type(e).__name__}"
        with get_session() as s:
            c = s.get(Company, cid)
            if not (c.funding_stage or c.funding_amount_usd_m or c.funding_total_usd_m):
                why["funding"] = backoff.last_error(c, "facts") or "no funding round found in keyless search"

    # 6. referrers with an address
    if people and _ready_with_email(cid) == 0:
        from jobhunter import referrals

        try:
            p = referrals.people_for_company(cid, linkedin=True)
            out["people"] = {k: p.get(k) for k in ("before", "after", "channels", "pattern_status")}
        except Exception as e:  # noqa: BLE001
            out["people"] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
        if _ready_with_email(cid) == 0:
            pp = out.get("people") or {}
            after = pp.get("after") or [0, 0]
            why["referrer_email"] = (
                f"{after[0]} ready referrer(s), none with an address; address format: {pp.get('pattern_status') or 'not learned'}"
                if after and after[0] else
                f"no current employee found on {', '.join(pp.get('channels') or []) or 'any channel'}")

    with get_session() as s:
        c = s.get(Company, cid)
        c.fill_state = json.dumps(why) if why else None
        c.filled_at = utcnow()
        s.add(c)
        s.commit()
    out["missing"] = why
    log.info("fill %s: %s", name, out)
    return out


# ------------------------------------------------------------------ the queue

def cohort(c: Company) -> int | None:
    """0 = the ★ list, 1 = Bengaluru / Hyderabad, 2 = a remote target; None = not in the pass."""
    if c.status is not None:
        return None
    if c.list_key:
        return 0
    if c.city in INDIA_CITIES or c.india_found_at is not None:
        return 1
    if (c.hq_region or "") in REMOTE_REGIONS and c.tier != "reject" and not c.overrated:
        return 2
    return None


def queue(limit: int | None = None, *, only: int | None = None, force: bool = False,
          remote_top: int = REMOTE_TOP) -> list[int]:
    """★ list (list order) → India (priority) → the top `remote_top` remote targets (priority).
    Skips a company filled in the last REFILL_DAYS (COMPLETE_DAYS once complete)."""
    init_db()
    now = utcnow()
    with get_session() as s:
        rows = s.exec(select(Company).where(col(Company.status).is_(None))).all()
    groups: dict[int, list[Company]] = {0: [], 1: [], 2: []}
    for c in rows:
        k = cohort(c)
        if k is not None and (only is None or k == only):
            groups[k].append(c)
    groups[0].sort(key=lambda c: (c.list_rank or 9999, c.id))
    # India: companies with evidence of pay at or above the floor first, then pay unknown.
    groups[1].sort(key=lambda c: (0 if guardrails.meta_of(c, "pay_floor").get("value") == "ok" else 1,
                                  -(c.priority_score or 0), c.id))
    groups[2].sort(key=lambda c: (-(c.priority_score or 0), c.id))
    groups[2] = groups[2][:remote_top]

    def due(c: Company) -> bool:
        if force or c.filled_at is None:
            return True
        return now - c.filled_at > timedelta(days=COMPLETE_DAYS if c.complete else REFILL_DAYS)

    ids = [c.id for k in (0, 1, 2) for c in groups[k] if due(c)]
    return ids[:limit] if limit else ids


def run(limit: int = 6, *, budget_s: int = 240, linkedin_reads: int = 12, people: bool = False,
        only: int | None = None, force: bool = False, ids: list[int] | None = None) -> dict:
    """The pass over `queue()` — what the tick runs (people left to its own people step)."""
    t0 = time.time()
    todo = ids if ids is not None else queue(limit, only=only, force=force)
    li = _LI(linkedin_reads)
    out: dict = {"queued": len(todo), "companies": 0, "descriptions": Counter(), "hiring_verified": 0,
                 "missing": Counter(), "errors": 0}
    for cid in todo:
        if time.time() - t0 > budget_s:
            out["stopped"] = f"time budget ({budget_s}s)"
            break
        try:
            r = company(cid, li=li, people=people)
        except Exception as e:  # noqa: BLE001 — one company must not end the pass
            log.exception("fill: company %s failed", cid)
            out["errors"] += 1
            out.setdefault("error_samples", []).append(f"{cid}: {type(e).__name__}: {str(e)[:80]}")
            continue
        out["companies"] += 1
        if r.get("description") and r["description"] != "kept existing":
            out["descriptions"][r["description"]] += 1
        for k in r.get("missing") or {}:
            out["missing"][k] += 1
    with get_session() as s:
        guardrails.refresh_complete(s)
    out["descriptions"], out["missing"] = dict(out["descriptions"]), dict(out["missing"])
    out["linkedin"] = li.blocked or f"{li.rep.pages.get('company', 0)} company + {li.rep.pages.get('search', 0)} job-search page(s)"
    out["seconds"] = round(time.time() - t0)
    return out


# ------------------------------------------------------------------ the measure

def measure(only: int, *, remote_top: int = REMOTE_TOP) -> dict:
    """Per-field counts for one cohort (0 ★, 1 India, 2 top remote)."""
    init_db()
    with get_session() as s:
        rows = [c for c in s.exec(select(Company).where(col(Company.status).is_(None))).all() if cohort(c) == only]
        if only == 0:
            rows.sort(key=lambda c: (c.list_rank or 9999))
        else:
            rows.sort(key=lambda c: (-(c.priority_score or 0), c.id))
        if only == 2:
            rows = rows[:remote_top]
        ids = {c.id for c in rows}
        with_roles = set(s.exec(select(Job.company_id).where(col(Job.company_id).in_(ids)).distinct()).all())
        counts = guardrails.ready_counts(s)
        n = len(rows)
        return {
            "companies": n,
            "website": sum(bool(c.website) for c in rows),
            "description": sum(_desc_ok(c) for c in rows),
            "hiring_proof": sum(c.hiring_status == "verified" for c in rows),
            "roles_stored": sum(c.id in with_roles for c in rows),
            "open_roles_counted": sum(c.open_roles_count is not None for c in rows),
            "funding": sum(bool(c.funding_stage or c.funding_amount_usd_m or c.funding_total_usd_m) for c in rows),
            "team_size": sum(bool(c.team_size or c.size_band) for c in rows),
            "linkedin": sum(bool(c.linkedin_checked_at) for c in rows),
            "referrer_email": sum(counts.get(c.id, (0, 0))[1] >= 1 for c in rows),
            "complete": sum(bool(c.complete) for c in rows),
        }


__all__ = ["Cand", "meta_candidates", "jsonld_candidates", "paragraph_candidates", "page_candidates",
           "best_description", "combine", "linkedin_slug", "parse_linkedin_extra", "company", "queue", "run",
           "measure", "cohort"]
