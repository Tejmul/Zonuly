"""Remote boards — fresher and intern software roles, open to someone outside the US/EU, worldwide.

Tejmul, 2026-11-05: "what is the update on remote comapanies hiring for freshers and interns there
are hell log of oppurtunities".

This is the remote sibling of `india_boards.py` and works the same way, because that way is the one
that produced real companies: a keyless web search finds public board URLs, the (ats, slug) pair is
read off the URL, and then the board's OWN PUBLIC API is fetched — greenhouse, lever, ashby. The
board, not the search snippet, decides everything:

  remote    the posting's own location says remote, and does not then fence it to one country
  fresher   the title says intern / new grad / SDE 1 / associate, and says nothing senior
  software  the title is an engineering title, not mechanical, optics or manufacturing
  pay       a stated band is kept when the posting states one; nothing is inferred

Postings are handed to `pipeline.persist`, which applies the usual relevance gate, dedups against
what we already hold and upserts the company through the admission gate. So a company that is
famous, a body shop or otherwise refused never gets in through this door either.

Nothing here touches a person or an address. It finds OPENINGS; `referrals.py` finds who to ask.

Layering: scrapers + pipeline + research. Nothing above it imports this.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from jobhunter import india_boards as boards
from jobhunter.db import get_session, get_setting, init_db, set_setting

log = logging.getLogger(__name__)

_CURSOR = "remote_boards:cursor"
_SEEN = "remote_boards:seen"

#: a posting is remote when it says so…
_REMOTE = re.compile(r"(?i)\b(remote|work from home|wfh|distributed|anywhere|global(?:ly)?)\b")
#: …and is not then fenced to one country we cannot work from
_FENCED = re.compile(
    r"(?i)\b(?:remote[,\s—-]*)?(?:us|usa|u\.s\.|united states|canada|uk|united kingdom|eu|emea|"
    r"europe|germany|france|netherlands|poland|spain|australia|singapore|japan|brazil|mexico|"
    r"latam|apac)\b[^.]{0,30}\b(?:only|based|resident|work authorization|authorized to work)\b"
    r"|\b(?:us|uk|eu|canada)[- ]only\b|\bmust (?:be|reside) (?:in|located)\b")
#: …and "India" or "anywhere" in the location is a positive signal, not a fence
_OPEN_TO_INDIA = re.compile(r"(?i)\b(india|anywhere|worldwide|global(?:ly)?|any location|"
                            r"remote[,\s-]*(?:global|worldwide|anywhere))\b")

#: the entry-level words — this is the whole point of the source
_FRESHER = re.compile(
    r"(?i)\b(intern|internship|trainee|apprentice|graduate|new ?grad|campus|fresher|entry[- ]level|"
    r"early career|university|student|junior|associate (?:software|engineer|developer|data)|"
    r"sde[ -]?(?:1|i)\b|software engineer (?:1|i)\b|engineer (?:1|i)\b|l1\b)\b")
#: …and the words that disqualify it however junior the rest sounds
_SENIOR = re.compile(
    # 2026-11-05: plain "staff" also matched "Member of Technical Staff", a normal engineer title
    # at AI labs, so it is required to be the seniority word ("Staff Engineer"), not any use of it.
    r"(?i)\b(senior|sr\.?|(?<!technical )staff (?:\w+ ){0,3}(?=engineer|software|swe|scientist|developer|architect)|"
    r"principal|lead (?:\w+ ){0,2}(?=engineer|developer|scientist|architect|sde)|"
    r"(?:tech|team|technical|engineering|product) lead|"
    r"manager|director|head of|vp\b|architect|"
    r"distinguished|fellow|ii\b|iii\b|\b[3-9]\+? years|\b1[0-9]\+? years|"
    # 2026-11-05: "Mid AI Engineer" and "Mid-Level AI Engineer" are not a fresher rung
    r"mid[- ]?level|\bmid\b(?= \w*\s?engineer| \w*\s?developer)|experienced)\b")

#: software only — same stance as India (2026-11-02)
_SOFTWARE = re.compile(
    r"(?i)\b(software|sde|sdet|engineer|developer|programmer|backend|back[- ]end|frontend|"
    r"front[- ]end|full[- ]?stack|devops|sre|site reliability|platform|infrastructure|cloud|"
    r"data engineer|data scientist|machine learning|\bml\b|\bai\b|applied scientist|android|ios|"
    r"mobile|qa|test engineer|security engineer|solutions engineer|forward deployed|research engineer)\b")
_NOT_SOFTWARE = re.compile(
    r"(?i)\b(mechanical|electrical|electronics|hardware|optical|optics|photonics|rf\b|antenna|pcb|"
    r"vlsi|embedded hardware|manufactur\w*|production|assembly|technician|lab engineer|chemical|"
    r"civil|structural|aerospace|avionics|propulsion|sales|marketing|recruit|hr\b|finance|legal|"
    r"designer|content|community manager|customer success|"
    # 2026-11-05: "Cloud FinOps Consulting Intern" matched on "cloud" — a consulting or advisory
    # seat is not an engineering seat, whatever technology it is about
    r"consult\w*|advisory|analyst|project manager|program manager|product manager|"
    r"business|operations|support specialist|technical writer|"
    # 2026-11-05: "AI Coach", "Lead Instructor" and "Staff Radar Systems Engineer" came through
    r"coach|instructor|teacher|trainer|curriculum|mentor|tutor|facilitator|"
    r"account exec\w*|account manager|revenue|growth manager|partnerships?|"
    r"radar|lidar|antenna|satellite|spacecraft|geolog\w*|drilling|metallurg\w*)\b")

_HOSTS = ["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"]
_TERMS = [
    # 2026-11-05: the first pool was 20 phrasings, so with 16 searches a round the cursor wrapped
    # every five rounds and 45 of 96 boards came back already-read. Volume here is breadth of
    # question, not depth of answer — the keyless backend returns ten results whatever we ask for.
    "remote software engineer intern", "remote new grad software engineer", "remote SDE 1",
    "remote associate software engineer", "remote graduate engineer", "remote junior backend engineer",
    "remote junior frontend engineer", "remote entry level software engineer",
    "remote AI engineer intern", "remote machine learning intern", "remote data engineer intern",
    "remote devops intern", "remote full stack intern", "remote engineering internship",
    "work from anywhere software engineer intern", "remote internship 2026 engineering",
    "remote trainee software developer", "remote apprentice engineer",
    "remote university graduate engineer", "remote early career software",
    # stacks — a board that never writes "SDE" still writes the language
    "remote junior python developer", "remote junior javascript developer",
    "remote junior react developer", "remote junior node developer", "remote junior java developer",
    "remote junior golang engineer", "remote junior rust engineer", "remote junior typescript",
    "remote junior android developer", "remote junior ios developer",
    "remote intern kubernetes", "remote intern aws", "remote intern docker",
    "remote intern postgres", "remote intern django", "remote intern flask",
    "remote intern pytorch", "remote intern tensorflow", "remote intern llm",
    "remote intern nlp", "remote intern computer vision", "remote intern data science",
    # sector words
    "remote fintech engineer intern", "remote saas engineer intern",
    "remote developer tools intern", "remote cybersecurity intern",
    "remote blockchain engineer intern", "remote gaming engineer intern",
    "remote healthtech engineer intern", "remote edtech engineer intern",
    # season and year phrasings boards actually use
    "summer 2027 software engineering intern remote", "winter 2027 engineering intern remote",
    "fall 2026 software intern remote", "2027 new grad engineer remote",
    "campus hire remote software engineer", "freshers remote software engineer",
    "remote co-op software engineer", "remote working student engineer",
    "remote graduate software developer programme", "remote first job software engineer",
]


@dataclass
class Stats:
    searched: int = 0
    candidates: int = 0
    boards_read: int = 0
    postings: int = 0
    kept: int = 0
    companies: int = 0
    inserted: int = 0
    rejected: dict = field(default_factory=dict)
    skip_count_seen: int = 0
    skipped: dict = field(default_factory=dict)
    examples: list = field(default_factory=list)
    seconds: int = 0

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def queries() -> list[str]:
    """host × entry-level phrasing — 80 distinct searches the cursor walks a slice of."""
    return [f"site:{h} {t}" for h in _HOSTS for t in _TERMS]


#: a location line that names a city or office is NOT remote, whatever the description says
_PLACE = re.compile(r"(?i)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*,\s*[A-Z]{2,}|"
                    r"\b(san francisco|new york|london|berlin|paris|austin|seattle|boston|toronto|"
                    r"amsterdam|dublin|sydney|singapore|tokyo|bengaluru|bangalore|hyderabad|mumbai|"
                    r"delhi|pune|chennai|noida|gurugram|gurgaon)\b")


#: 2026-11-05: "Remote Canada", "Remote US", "Türkiye, Remote" and "Remote | United Kingdom" all
#: came through, because the first fence test needed a word like "only" or "based". A country named
#: beside "remote" IS the fence — it says which country you must sit in. So any country name that is
#: not India, and no "worldwide/anywhere", means the role is not open to him.
_COUNTRY = re.compile(
    r"(?i)\b(us|usa|u\.s\.a?\.?|united states|canada|uk|united kingdom|england|scotland|ireland|"
    r"germany|deutschland|france|spain|portugal|italy|netherlands|belgium|poland|romania|ukraine|"
    r"turkey|t[uü]rkiye|greece|sweden|norway|denmark|finland|switzerland|austria|czech|hungary|"
    r"australia|new zealand|singapore|japan|korea|china|hong kong|taiwan|vietnam|thailand|"
    r"philippines|indonesia|malaysia|israel|uae|dubai|saudi|egypt|nigeria|kenya|south africa|"
    r"brazil|argentina|chile|colombia|mexico|peru|costa rica|latam|emea|apac|anz|"
    r"europe|european union|\beu\b|north america|south america|"
    r"namer|noram|amer\b|\bna\b(?= region)|ukraine|balkans|nordics|benelux|dach)\b")


#: a list of US state codes or names in a title is a fence, however loudly it says "Remote"
_US_STATES = re.compile(
    r"(?i)\b(?:remote in|anywhere in|based in|located in|work from)\b[^.]{0,40}?"
    r"(?:\bus\b|u\.s\.?a?\.?|\busa\b|united states|canada|uk|united kingdom|europe|\beu\b)"
    r"|\b(?:VA|MD|PA|NC|DE|NJ|DC|CA|NY|TX|WA|MA|IL|GA|FL|OH|CO|AZ|OR|MN|MI|UT|TN|IN|MO|WI|NV)\b"
    r"\s*,\s*\b(?:VA|MD|PA|NC|DE|NJ|DC|CA|NY|TX|WA|MA|IL|GA|FL|OH|CO|AZ|OR|MN|MI|UT|TN|IN|MO|WI|NV)\b"
    r"|\b(?:mid-atlantic|north central|northeast|southeast|southwest|west coast|east coast) region\b"
    # 2026-11-05: "Forward Deployed Developer (CA Remote)" — one state code next to "remote" fences it
    r"|\b(?:VA|MD|PA|NC|DE|NJ|DC|CA|NY|TX|WA|MA|IL|GA|FL|OH|CO|AZ|OR|MN|MI|UT|TN|IN|MO|WI|NV|ON|BC|AB)"
    r"\s*[- ]?\s*remote\b|\bremote\s*[-(,]?\s*(?:VA|MD|PA|NC|DE|NJ|DC|CA|NY|TX|WA|MA|IL|GA|FL|OH)\b")


def is_remote(location: str | None, description: str | None = None) -> bool:
    """Does the POSTING ITSELF say remote, without fencing it to a country we cannot work from?

    2026-11-05: the first version also read the description, and Figma internships in San Francisco,
    New York and London came through because somewhere in the blurb the word "remote" appears. A
    city in the location line settles it — the description never promotes an office job to remote.
    The description is consulted only when the board gives no location at all."""
    loc = (location or "").strip()
    if loc:
        if not _REMOTE.search(loc):
            return False
        # "Remote - San Francisco" is a hybrid desk, not a remote role
        without = _REMOTE.sub(" ", loc)
        if _PLACE.search(without) and not _OPEN_TO_INDIA.search(loc):
            return False
        # 2026-11-05: Alan's "Anywhere in France, Belgium, Spain" read as open, because "anywhere"
        # matched. A named country beats the word "anywhere" — "anywhere in France" is France.
        if _COUNTRY.search(loc) and not re.search(r"(?i)\bindia\b", loc):
            return False
        if _OPEN_TO_INDIA.search(loc):
            return True
        return not _FENCED.search(loc)
    head = (description or "")[:600]
    if not _REMOTE.search(head):
        return False
    if _COUNTRY.search(head) and not re.search(r"(?i)\bindia\b", head):
        return False
    if _OPEN_TO_INDIA.search(head):
        return True
    return not _FENCED.search(head)


def is_fresher(title: str | None) -> bool:
    """Open to a fresher: explicitly entry-level, OR stating no level at all.

    2026-11-05: requiring an explicit fresher word threw away almost everything — of 5,654 postings
    only 33 said "intern" or "new grad", and most engineering titles ("Member of Technical Staff",
    "Applied AI Research Engineer", "Backend Engineer") simply do not state a level. Tejmul's
    standing rule is to frame an unknown-level ask as a fresher ask, so unknown is kept and only an
    explicitly senior title is refused. The ask itself says he is a final-year student, so the
    company is never misled about who is writing."""
    t = title or ""
    if _SENIOR.search(t):
        return False
    return True


def stated_fresher(title: str | None) -> bool:
    """True only when the title says entry-level in so many words — used for ranking, not gating."""
    t = title or ""
    return bool(_FRESHER.search(t)) and not _SENIOR.search(t)


def is_software(title: str | None) -> bool:
    t = title or ""
    return bool(_SOFTWARE.search(t)) and not _NOT_SOFTWARE.search(t)


#: 2026-11-05: PingWind's "Data Scientist I (Remote)" is US Navy work — "Required Clearance:
#: Secret". A security clearance or a citizenship requirement makes a role unreachable for him
#: however remote it is, and the title never says so; only the description does.
_CLEARANCE = re.compile(
    r"(?i)(required clearance|security clearance|\b(?:secret|top secret|ts/sci|public trust)\b"
    r"[^.]{0,30}clearance|clearance[^.]{0,30}\b(?:secret|required|eligible)\b|"
    r"must be a? ?u\.?s\.? citizen|u\.?s\.? citizenship (?:is )?required|"
    r"green card|permanent resident[^.]{0,20}required|authorized to work in the (?:us|united states)|"
    r"itar|export control)")


def blocked_by_clearance(description: str | None) -> bool:
    """True when the posting needs a clearance or a citizenship he cannot have."""
    return bool(_CLEARANCE.search((description or "")[:3000]))


def wanted(job) -> tuple[bool, str]:
    """(keep it, why not). One place, so the report can say exactly what was dropped and why."""
    title = getattr(job, "title", "") or ""
    if not is_software(title):
        return False, "not a software role"
    if not is_fresher(title):
        return False, "explicitly senior"
    # 2026-11-05: GuidePoint Security listed "Application Security Engineer - Northeast region
    # (Remote in VA, MD, PA, NC, DE, NJ, or DC)" with location just "Remote". The fence was in the
    # TITLE, so the title is now read for a fence exactly like the location is.
    if _COUNTRY.search(title) and not _OPEN_TO_INDIA.search(title):
        return False, "not remote, or fenced to one country"
    loc = getattr(job, "location", "") or ""
    if _US_STATES.search(title) or _US_STATES.search(loc):
        return False, "not remote, or fenced to one country"
    if not is_remote(getattr(job, "location", None), getattr(job, "description", None)):
        return False, "not remote, or fenced to one country"
    if blocked_by_clearance(getattr(job, "description", None)):
        return False, "needs a security clearance or citizenship"
    return True, ""


def run(*, searches: int = 20, boards_per_run: int = 80, budget_s: int = 1200) -> dict:
    """One pass: search, read each new board's public API, keep the remote fresher software
    postings and store them through the normal pipeline."""
    from jobhunter import pipeline
    from jobhunter.pipeline import ScrapeStats

    init_db()
    st = Stats()
    t0 = time.time()
    with get_session() as s:
        import json
        try:
            seen = set(json.loads(get_setting(s, _SEEN, "[]") or "[]"))
        except ValueError:
            seen = set()
        try:
            cur = int(get_setting(s, _CURSOR, "0") or 0)
        except ValueError:
            cur = 0

    # the cheap half: board URLs off the search results
    from jobhunter.research import web
    from jobhunter.scrapers import careers_ats as ca
    pool = queries()
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for i in range(searches):
        q = pool[(cur + i) % len(pool)]
        st.searched += 1
        try:
            hit = web.search(q, limit=10, only=web.KEYLESS)
        except Exception as e:                      # noqa: BLE001
            log.debug("search failed: %s", e)
            continue
        for r in hit.get("results") or []:
            url = r.get("url") or ""
            for a, rx in ca.ATS_PATTERNS:
                m = rx.search(url)
                if m and a in ("greenhouse", "lever", "ashby"):
                    slug = m.group(1)
                    if slug and slug.lower() not in ca._NOT_A_SLUG:
                        found.setdefault((a, slug.lower()), (a, slug))
                    break
    st.candidates = len(found)

    # 2026-11-05: the slice used to come BEFORE the already-read check, so a round took the first
    # 70 candidates, found all 70 already read, and did nothing — six rounds in a row read zero
    # boards while still spending their searches. Drop the seen ones FIRST, then take the slice.
    unseen = [(ats, slug) for (a, slug_l), (ats, slug) in found.items()
              if f"{ats}/{slug_l}" not in seen]
    st.skip_count_seen = len(found) - len(unseen)
    if st.skip_count_seen:
        st.skipped["board already read"] = st.skip_count_seen

    fresh, raw = [], []
    for ats, slug in unseen[:boards_per_run]:
        slug_l = slug.lower()
        if time.time() - t0 > budget_s:
            st.skip("out of time")
            break
        fresh.append(f"{ats}/{slug_l}")
        st.boards_read += 1
        jobs = boards._read_board(ats, slug)
        if not jobs:
            st.skip("board empty or unreachable")
            continue
        name = boards.board_name(ats, slug)
        if not name:
            st.skip("no real company name on the board")
            continue
        st.postings += len(jobs)
        for j in jobs:
            ok, why = wanted(j)
            if not ok:
                st.skip(why)
                continue
            j.company_name = name          # the board's real name, never the slug
            j.remote = True
            raw.append(j)
            if len(st.examples) < 40:
                st.examples.append(f"{name} — {j.title} ({j.location or 'remote'})")

    if raw:
        out = pipeline.persist(raw, ScrapeStats())
        st.kept = out.kept
        st.inserted = out.inserted
        st.rejected = dict(out.rejected or {})
        st.companies = len({j.company_name for j in raw})

    with get_session() as s:
        import json
        set_setting(s, _SEEN, json.dumps(sorted(seen | set(fresh))[-20000:]))
        set_setting(s, _CURSOR, str((cur + searches) % len(pool)))
        s.commit()

    st.seconds = round(time.time() - t0)
    res = {"searched": st.searched, "candidates": st.candidates, "new_boards": len(unseen), "boards_read": st.boards_read,
           "postings_seen": st.postings, "remote_fresher_kept": len(raw), "persisted": st.kept,
           "new_roles": st.inserted, "companies": st.companies,
           "company_refused": st.rejected, "skipped": st.skipped, "seconds": st.seconds,
           "examples": st.examples[:25]}
    log.info("remote boards: %s", {k: v for k, v in res.items() if k != "examples"})
    return res


__all__ = ["run", "queries", "wanted", "is_remote", "is_fresher", "stated_fresher",
           "is_software"]
