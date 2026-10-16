"""A greeting name for a contact who has an address and no name — never invented.

2026-10-17: of ~4,900 current-affiliation contacts with an address, 2,463 had no name, so
"Hi {first_name}," could not be written and the dashboard showed a handle. Two sources:

    address   the local part, when it says a name with confidence:
                first.last / first_last / first-last (two alphabetic tokens)  -> "First Last"
                first.l (a surname initial)                                    -> "First"
                a concatenation a dictionary splits one way only              -> "First Last"
                  ("adityachaudhary" = aditya | chaudhary), or the GitHub login splits
                a single token that is a given name: in the name list, or the GitHub
                  login starts with it (zakki-aai, ankush566), or the company's confirmed
                  format is {first} — and it does not read as an initial + surname
                  ("npatel", "kbysani": no first name; "Hi N" is not a greeting)
              stored with name_source "address" (full) or "address-first" (first only).
    github    the public profile's display name (github.com/<login>, plain HTML — no
              token, no account), paced and budgeted; name_source "github-profile".

Every derived name passes guardrails.person_name (full) or guardrails.first_name_only
(first only). An existing name is never overwritten — except that a first-only name from
the address gives way to the person's own GitHub name when its first word agrees.

Junk: an address the guardrails now read as an inbox or garbage (support@, accommodations@,
"u003csupport@", a format example) is labelled generic — a company contact, never a referrer.

Layering: db + guardrails. Synchronous httpx for the GitHub pass.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import col, select

from jobhunter import CONFIG, guardrails
from jobhunter.db import Company, Contact, ExtractionReject, ExtractionWrite, Setting, get_session, utcnow

log = logging.getLogger(__name__)

_R = CONFIG.get("referrals") or {}
GITHUB_PER_CYCLE = int(_R.get("github_names_per_cycle", 12))     # 4 cycles/h -> 48/h, under GitHub's 60/h
GITHUB_PAUSE_S = float(_R.get("github_names_pause_seconds", 1.5))
RETRY_DAYS = 30                       # a profile with no display name is not re-read for a month
COOLDOWN_KEY = "names:github:cooldown_until"
NO_NAME_REASON = "no display name on their GitHub profile"

# ------------------------------------------------------------------ dictionaries

GIVEN = set("""
aarav aarti aarush aashish aayush abhay abhijeet abhijit abhilash abhimanyu abhinav abhishek aditi aditya
adarsh advait ajay ajinkya ajit akash akhil akhilesh akshay akshat alok aman amar amit amitabh amol amrita
anand anant ananya anil anirudh anish anjali ankit ankita ankur ankush anmol anoop anshul anubhav anuj
anupam anurag anusha aparna archana arjun arnav arpit arun aruna arvind aryan ashish ashok ashwin atul
avinash ayush balaji bhanu bharat bhavesh bhavya chaitanya chandan chandra chetan chinmay chirag darshan
deepak deepa deepika deepti devansh devendra dhananjay dhruv dinesh divya gaurav gautam gayatri girish
gokul gopal govind harish harsh harsha harshit hemant himanshu hitesh hrishikesh ishaan ishan ishita
jagdish jatin jay jayant jayesh jitendra karan karthik kartik kashish kavya keshav ketan kiran kishore
krishna kunal kush lakshmi lalit madhav madhu madhur mahesh manish manoj mayank mayur meera megha mihir
mohan mohit mukesh mukul naman nandini naresh naveen navneet neeraj neha nikhil nikita nilesh nirmal
nisha nishant nitin nitish omkar pallavi pankaj paras parth pavan piyush pooja prabhat pradeep prakash
pranav pranay prasad prashant prateek pratik praveen preeti prem priya priyanka pulkit punit pushkar
rachit raghav rahul raj rajat rajeev rajesh rajiv rakesh ram ramesh ranjan ravi rishabh rishi ritesh
ritika rohan rohit sachin sagar sahil sai sajal sakshi samarth sameer sandeep sanjay sanjeev sanket
santosh saransh sarthak satish saurabh shailesh shashank shikhar shivam shivani shivesh shreya shreyas
shrey shruti shubham siddharth siddhant smita sneha sonal soumya sourabh sri srinivas subham sudhir
sumit sunil suraj suresh surya sushant swapnil swati tanmay tanvi tarun tejas tushar uday umesh utkarsh
vaibhav varun vedant venkat vibhor vidya vijay vikas vikram vinay vineet vinod vipin vipul vishal vishnu
vivek yash yashwant yogesh zubair zaid faisal farhan imran irfan salman sameer shahid tariq yusuf
aaron adam adrian alan albert alex alexander alexandra alice alicia allison amanda amber amy andrea
andrew andy angela anna anne anthony antonio ashley austin barbara ben benjamin beth brad bradley brandon
brian brittany bruce bryan caleb cameron carl carlos carol caroline catherine charles charlie chris
christian christina christine christopher claire colin connor craig daniel danielle david dean derek
diana diego dominic donald dylan edward elena eli elizabeth emily emma eric erica erin ethan evan felix
fernando frank gabriel gary george grace greg gregory hannah harry heather helen henry ian isaac jack
jacob jake james jamie jason javier jeff jeffrey jennifer jeremy jesse jessica joe joel jonathan jordan
jose joseph joshua julia julian justin karen kate katherine kathryn kelly kevin kyle laura lauren leo
liam linda lisa logan lucas luis luke marc marco marcus maria mark martin mary matt matthew max megan
melissa michael michelle miguel mike molly nathan nathaniel nicholas nick nicole noah oliver olivia omar
oscar owen patrick paul peter philip rachel rebecca richard robert ryan sam samantha samuel sara sarah
scott sean sebastian sergio shane sophia stephanie stephen steve steven susan taylor thomas tim timothy
todd tom tony tyler victor victoria vincent william zach zachary
""".split())

SURNAMES = set("""
agarwal aggarwal agrawal ahmed ahuja arora bajaj banerjee bansal bhat bhatia bhatt bose chakraborty
chatterjee chaudhary chaudhari chauhan chawla chopra das dasgupta desai deshmukh deshpande dey dhawan
dixit dubey dutta gandhi garg ghosh goel gokhale gupta hegde iyer iyengar jain jha joshi kapoor kaur
khan khanna kohli kulkarni kumar mehta menon mishra mittal mukherjee murthy nair naidu pandey pandit
patel patil pillai prasad rao rathore reddy saxena sen shah sharma shetty shukla singh sinha srivastava
subramanian thakur tiwari trivedi varma verma yadav bysani
smith johnson williams brown jones miller davis wilson anderson taylor thomas moore martin jackson
thompson white harris clark lewis robinson walker young allen king wright scott hill green adams baker
nelson carter mitchell roberts turner phillips campbell parker evans edwards collins stewart morris
murphy cook rogers morgan cooper peterson reed bailey bell kelly howard ward cox richardson wood watson
brooks bennett gray hughes price sanders myers long ross foster garcia martinez rodriguez lopez gonzalez
hernandez perez sanchez ramirez torres flores rivera gomez diaz cruz reyes morales ortiz gutierrez
chavez ramos kim lee park chen wang zhang liu yang huang zhao wu zhou xu sun ma zhu hu guo lin nguyen
tran pham cohen levy goldstein friedman schwartz weiss klein muller schmidt schneider fischer weber
meyer wagner becker schulz hoffmann rossi russo ferrari esposito bianchi romano colombo ricci
""".split())

#: words a local part carries that are not a person's name (on top of the guardrails' inbox words)
_NOT_NAME_WORDS = {
    "first", "last", "firstname", "lastname", "fname", "lname", "name", "surname", "user", "username", "test",
    "demo", "example", "sample", "someone", "yourname", "dummy", "temp", "guest", "anon", "anonymous", "owner",
    "india", "global", "intl", "world", "us", "uk", "eu", "emea", "apac", "blr", "hyd", "group", "corp", "inc",
    "official", "office", "desk", "online", "web", "site", "app", "api", "cloud", "data", "tech", "labs", "lab",
    "ai", "ml", "dev", "devs", "eng", "engineering", "product", "design", "growth", "ceo", "cto", "cfo", "coo",
    "founder", "cofounder", "manager", "lead", "intern", "hire", "work", "join", "apply", "resume", "cv",
    "upload", "shop", "store", "orders", "order", "booking", "bookings", "demo", "trial", "lifestyle", "news",
    "integrations", "integration", "partner", "partnerwithus", "accommodations", "accommodation",
    "accessibility", "benefits", "payroll", "students", "admissions", "membership", "members", "reservations",
    "gethelp", "help", "do", "not", "reply", "mailer", "account", "accounts", "manager", "your", "my", "the",
    "john", "jane", "doe", "jdoe", "jsmith", "johndoe", "janedoe", "johnsmith", "janesmith",
    "me", "mr", "ms", "mrs", "dr", "iphone", "android", "mac", "pc", "real", "its", "im", "iam", "hey", "mail",
}
_STOP = (_NOT_NAME_WORDS | guardrails.GENERIC_LOCAL | guardrails.ROLE_TOKENS
         | {w for w in guardrails._NOT_PERSON})
#: consonant pairs a given name may start with ("Shivesh", "Pradeep", "Chris"); any other
#: pair opening a single token reads as an initial + surname ("npatel", "kbysani", "jsmith")
_ONSETS = {"bh", "bl", "br", "ch", "cl", "cr", "dh", "dr", "dw", "fl", "fr", "gh", "gl", "gr", "gw", "jh",
           "kh", "kl", "kr", "ph", "pl", "pr", "ps", "rh", "sc", "sh", "sk", "sl", "sm", "sn", "sp", "sr",
           "st", "sw", "th", "tr", "tw", "vr", "wh", "wr", "zh", "ny"}
#: words that make a run-together local part a function, not a name, wherever they sit
_EMBEDDED = re.compile(
    r"admin|open|source|school|team|info|mail|help|sales|data|test|code|tech|work|hire|cloud|store|shop|"
    r"news|media|india|global|support|service|legal|github|user|account|contact|press|market|office|"
    r"career|talent|people|finance|billing|security|privacy|product|design|growth|hello|reply|partner|"
    r"invest|admission|student|learn|event|community|order|ticket|alert|report")
_VOWELS = set("aeiouy")
_ALPHA = re.compile(r"^[a-z]+$")


@dataclass
class Derived:
    name: str
    source: str          # address | address-first | github-profile
    confidence: str      # high | medium | low
    why: str


def _has_vowel(t: str) -> bool:
    return any(ch in _VOWELS for ch in t)


def _company_words(company: Company | None) -> set[str]:
    words: set[str] = set()
    if company is None:
        return words
    for raw in (company.name or "", (company.domain or "").split(".")[0]):
        for w in re.split(r"[^a-z0-9]+", raw.lower()):
            if len(w) >= 2:
                words.add(w)
        joined = re.sub(r"[^a-z0-9]", "", raw.lower())
        if len(joined) >= 3:
            words.add(joined)
    return words


def _word_ok(t: str, company_words: set[str], *, min_len: int = 2) -> bool:
    """A token that can be part of a name: letters, a vowel, not an inbox word, not the company."""
    return bool(_ALPHA.match(t) and min_len <= len(t) <= 20 and _has_vowel(t) and t not in _STOP
                and t not in company_words and not re.search(r"[^aeiouy]{5,}", t))


def _initial_surname(t: str) -> bool:
    """"npatel", "kbysani", "akumar": a first initial and a surname, not a first name."""
    if t[1:] in SURNAMES:
        return True
    return t[0] not in _VOWELS and t[1] not in _VOWELS and t[:2] not in _ONSETS


def _given_shaped(t: str, company_words: set[str]) -> bool:
    return (_word_ok(t, company_words, min_len=3) and len(t) <= 12 and not _initial_surname(t)
            and t not in SURNAMES)


def _login_tokens(login: str | None) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", (login or "").lower()) if t]


def _title(t: str) -> str:
    return t[:1].upper() + t[1:]


def _full(first: str, last: str, why: str, conf: str = "medium") -> Derived | None:
    chk = guardrails.person_name(f"{_title(first)} {_title(last)}")
    return Derived(chk.value, "address", conf, why) if chk else None


def _first(first: str, why: str, conf: str = "medium") -> Derived | None:
    chk = guardrails.first_name_only(_title(first))
    return Derived(chk.value, "address-first", conf, why) if chk else None


def from_address(addr: str | None, *, login: str | None = None, company: Company | None = None,
                 pattern: str | None = None) -> Derived | None:
    """The name an address says, or None when it does not say one with confidence."""
    if not addr or "@" not in addr:
        return None
    chk = guardrails.email(addr, domain=company.domain if company else None)
    if not chk or chk.value[1] == "generic":
        return None
    local = chk.value[0].split("@", 1)[0].split("+", 1)[0]
    local = re.sub(r"\d{1,4}$", "", local)              # ashraf.chowdury09
    if not local or re.search(r"\d", local) or ".." in local:
        return None
    cw = _company_words(company)
    toks = [t for t in re.split(r"[._\-]", local) if t]
    if len(toks) == 2:
        a, b = toks
        if _word_ok(a, cw) and _word_ok(b, cw) and len(a) >= 2 and len(b) >= 2:
            if (a, b) in {("john", "doe"), ("jane", "doe"), ("john", "smith"), ("jane", "smith")}:
                return None
            if chk.value[1] == "personal" and not (a in GIVEN or b in SURNAMES
                                                   or (len(a) >= 3 and a in re.sub(r"[^a-z]", "", (login or "").lower()))):
                return None          # gmail's frenzy.madness@, elie.iphone@: a handle with a dot in it
            return _full(a, b, f"the address reads first.last ({local})", "high" if len(a) >= 3 else "medium")
        if len(b) == 1 and _given_shaped(a, cw) and a not in ("john", "jane"):
            return _first(a, f"the address reads first.initial ({local})")
        return None
    if len(toks) != 1:
        return None
    t = toks[0]
    if not _ALPHA.match(t) or t in _STOP or t in cw:
        return None
    lt = _login_tokens(login)
    # the GitHub login spells the concatenation out: aditya-chaudhary for adityachaudhary
    for i in range(len(lt) - 1):
        if lt[i] + lt[i + 1] == t and _word_ok(lt[i], cw, min_len=3) and _word_ok(lt[i + 1], cw, min_len=3):
            return _full(lt[i], lt[i + 1], f"their GitHub login {login} splits {t}", "high")
    # a dictionary split, one way only: given name | surname
    if len(t) >= 7:
        splits = [(t[:i], t[i:]) for i in range(3, len(t) - 2) if t[:i] in GIVEN and t[i:] in SURNAMES]
        if len(splits) == 1:
            a, b = splits[0]
            return _full(a, b, f"{t} = {a} | {b} (given name + surname)", "medium")
        if splits:
            return None
    if not _given_shaped(t, cw) or re.match(r"^(john|jane)[a-z]{0,2}$", t):
        return None
    if t in GIVEN:
        return _first(t, f"{t} is a given name", "high")
    if len(t) > 10 or _EMBEDDED.search(t):
        return None                      # lawschools@, opensource@, itadmin@: words, not a name
    if lt and lt[0] == t and (login or "").lower() != t:
        # zakki-aai, ankush566: the login is the same name plus a tag — they go by it
        return _first(t, f"their GitHub login {login} starts with it")
    if pattern == "{first}" and chk.value[1] == "company":
        return _first(t, "the company's confirmed address format is {first}@")
    return None


# ------------------------------------------------------------------ the save path

def fill_vetted(g: dict | None, company: Company | None, *, login: str | None = None) -> dict | None:
    """guardrails.vet_contact's output, with a name from the address when it has none."""
    if not g or g.get("name") or not g.get("email") or g.get("email_kind") == "generic":
        return g
    d = from_address(g["email"], login=login or g.get("handle"), company=company,
                     pattern=company.email_pattern if company else None)
    if d:
        g["name"], g["name_source"] = d.name, d.source
    return g


def _login(p: Contact) -> str | None:
    if p.handle:
        return p.handle
    m = re.search(r"github\.com/([A-Za-z0-9][A-Za-z0-9\-]{0,38})/?$", (p.github or "").rstrip("/"))
    return m.group(1) if m else None


def name_contact(session, p: Contact, company: Company | None) -> Derived | None:
    """Give one stored contact its address name (never over an existing one). A name this
    module read off the address earlier is read again, so a rule that tightens takes back
    what it no longer stands behind ("Lawschools", "Itadmin")."""
    ours = (p.name_source or "") in ("address", "address-first")
    if (p.name and not ours) or not p.email:
        return None
    d = None if p.email_kind == "generic" else from_address(
        p.email, login=_login(p), company=company, pattern=company.email_pattern if company else None)
    if ours:
        if d and d.name == p.name:
            return None
        if not d:
            guardrails.reject(session, company_id=p.company_id, contact_id=p.id, field="contact.name",
                              value=p.name, reason="the address no longer says this name (rule tightened)",
                              source=p.email, channel="address")
            p.name, p.name_source = None, None
            session.add(p)
            return None
    if not d:
        return None
    p.name, p.name_source = d.name, d.source
    session.add(p)
    session.add(ExtractionWrite(company_id=p.company_id, contact_id=p.id,
                                field="contact.name" if d.source == "address" else "contact.first_name",
                                value=d.name, evidence=d.why[:400], source=p.email, channel="address",
                                confidence=d.confidence))
    return d


def _commit(session) -> None:
    for attempt in range(6):
        try:
            session.commit()
            return
        except Exception as e:  # noqa: BLE001 — "database is locked" under the WAL writers: retry
            if "locked" not in str(e) or attempt == 5:
                raise
            time.sleep(1 + attempt)


def demote_junk(session, p: Contact) -> str | None:
    """An address the guardrails now read as an inbox (or refuse) is a company contact."""
    if not p.email or (p.email_kind == "generic" and p.affiliation == "generic"):
        return None
    chk = guardrails.email(p.email)
    if chk and chk.value[1] != "generic" and "repaired-escape" not in chk.flags:
        return None
    if chk and "repaired-escape" in chk.flags and chk.value[1] != "generic":
        why = f"a second copy of {chk.value[0]} (an escaped '<'/'>' in front)"
    else:
        why = f"shared inbox {chk.value[0].split('@')[0]}@" if chk else f"not a person's address: {chk.reason}"
    guardrails.reject(session, company_id=p.company_id, contact_id=p.id, field="contact.affiliation",
                      value=p.affiliation, reason=f"re-check: {why}"[:240], source=p.email, channel="names")
    p.email_kind, p.affiliation, p.affiliation_source = "generic", "generic", "none"
    p.affiliation_evidence = why[:300]
    p.affiliation_checked_at = utcnow()
    p.role_class, p.referral_rank = "other", 9
    session.add(p)
    return why


def repair_escaped(session, p: Contact, company: Company | None) -> str | None:
    """"u003ejenn.priske@redspace.com" is jenn.priske@redspace.com with a scraper's escaped
    ">" in front: store the address it is, and judge the row again on it (an inbox stays
    generic; a person gets their name and their labels back from the evidence on the row).
    An address another contact at the company already holds is not stored twice."""
    if not p.email:
        return None
    chk = guardrails.email(p.email, domain=company.domain if company else None)
    if not chk or "repaired-escape" not in chk.flags:
        return None
    fixed, kind = chk.value
    dup = session.exec(select(Contact.id).where(Contact.company_id == p.company_id, Contact.email == fixed,
                                                Contact.id != p.id)).first()
    if dup is not None:
        return None                          # demote_junk labels this copy generic
    old = p.email
    p.email, p.email_kind = fixed, kind
    session.add(ExtractionWrite(company_id=p.company_id, contact_id=p.id, field="contact.email", value=fixed,
                                evidence=f"repaired: {old} carried an escaped '<'/'>' in front", source=old,
                                channel="names", confidence="high"))
    if kind != "generic":
        name_contact(session, p, company)
        plan = guardrails.relabel_contact(p, company)
        for k, v in plan.items():
            if not k.startswith("_") and k not in ("name", "handle"):
                setattr(p, k, v)
        p.affiliation_checked_at = utcnow()
        if p.referral_rank == 9 and p.role_class == "other":
            from jobhunter.contacts import roles

            v = roles.classify(p.role, name=p.name, email=p.email)
            p.role_class, p.seniority, p.referral_rank, p.role_evidence = (v.role_class, v.seniority,
                                                                           v.referral_rank, v.evidence)
    session.add(p)
    return fixed


def backfill(*, batch: int = 200, limit: int | None = None, sessions=None) -> dict:
    """Every contact with an address and no name: a name from the address where it says
    one; every address that is an inbox or garbage: generic. Small committed batches."""
    out = {"seen": 0, "address": 0, "address_first": 0, "junk": 0, "junk_current": 0, "repaired": 0, "cleared": 0}
    sessions = sessions or get_session
    with sessions() as session:
        companies = {c.id: c for c in session.exec(select(Company)).all()}
        escaped = col(Contact.email).like("u00%") | col(Contact.email).like("x3%")
        ids = session.exec(select(Contact.id).where(
            col(Contact.email).is_not(None), Contact.email != "",
            (col(Contact.affiliation).is_(None)) | (Contact.affiliation != "generic") | escaped)
            .order_by(Contact.id)).all()
    if limit:
        ids = ids[:limit]
    for i in range(0, len(ids), batch):
        with sessions() as session:
            for p in session.exec(select(Contact).where(col(Contact.id).in_(ids[i:i + batch]))).all():
                out["seen"] += 1
                was = p.affiliation
                if repair_escaped(session, p, companies.get(p.company_id)):
                    out["repaired"] += 1
                    if p.email_kind != "generic":
                        continue
                if demote_junk(session, p):
                    out["junk"] += 1
                    out["junk_current"] += was in guardrails.READY
                    continue
                had = p.name
                d = name_contact(session, p, companies.get(p.company_id))
                if had and not p.name:
                    out["cleared"] += 1
                if d:
                    out["address" if d.source == "address" else "address_first"] += 1
            _commit(session)
    return out


# ------------------------------------------------------------------ the GitHub pass

_NAME_SPAN = re.compile(r'itemprop="name"[^>]*>\s*([^<]*?)\s*<', re.S)


def _cooling(session) -> bool:
    row = session.get(Setting, COOLDOWN_KEY)
    try:
        return bool(row and datetime.fromisoformat(row.value) > utcnow())
    except ValueError:
        return False


def _cool(session, minutes: int) -> None:
    until = (utcnow() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    row = session.get(Setting, COOLDOWN_KEY) or Setting(key=COOLDOWN_KEY, value=until)
    row.value = until
    session.add(row)
    _commit(session)


def profile_name(client, login: str) -> tuple[str, str | None]:
    """("ok", display name or None) | ("missing", None) | ("limited", why) | ("error", why)."""
    try:
        r = client.get(f"https://github.com/{login}", timeout=20)
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}"
    if r.status_code in (403, 429):
        return "limited", f"HTTP {r.status_code}"
    if r.status_code == 404:
        return "missing", None
    if r.status_code >= 400:
        return "error", f"HTTP {r.status_code}"
    m = _NAME_SPAN.search(r.text)
    return "ok", (m.group(1).strip() if m and m.group(1).strip() else None)


def candidates(session, limit: int) -> list[int]:
    """Current contacts with a GitHub login and no name (or a first name from the address),
    not tried in the last RETRY_DAYS — Sarvam and the ★ / Bengaluru companies first."""
    since = utcnow() - timedelta(days=RETRY_DAYS)
    tried = set(session.exec(select(ExtractionReject.contact_id).where(
        ExtractionReject.channel == "github-name", col(ExtractionReject.at) >= since)).all())
    rows = session.exec(select(Contact, Company).join(Company, Company.id == Contact.company_id).where(
        col(Contact.github).is_not(None), col(Contact.affiliation).in_(guardrails.READY),
        (col(Contact.name).is_(None)) | (Contact.name == "") | (Contact.name_source == "address-first"))).all()

    def rank(pc):
        p, c = pc
        india = (c.list_key == "india-star") + ((c.city or "").lower() in ("bengaluru", "bangalore"))
        return (c.id != 54, -india, p.email is None, p.referral_rank or 9, p.id)

    out = []
    for p, c in sorted(rows, key=rank):
        if p.id in tried or (p.email and p.email_kind == "generic"):
            continue
        out.append(p.id)
        if len(out) >= limit:
            break
    return out


def _one_word_first(value: str | None, p: Contact, login: str) -> str | None:
    """A one-word display name ("Shivesh", "Dhruv") is a first name when it is a given name
    in the list, or the address / login begins with it — not "Pandey", not "Zenitzzu"."""
    chk = guardrails.first_name_only((value or "").strip())
    if not chk:
        return None
    low = chk.value.lower()
    local = (p.email or "").split("@")[0].lower()
    if low in GIVEN or (len(low) >= 3 and (local.startswith(low) or login.lower().startswith(low))
                        and low not in SURNAMES):
        return chk.value
    return None


def apply_profile_name(session, p: Contact, login: str, value: str | None, status: str = "ok") -> str:
    """Store what their GitHub profile calls them. Returns named | upgraded | first_only | kept | no_name."""
    src = f"https://github.com/{login}"
    chk = guardrails.person_name(value) if value else None
    outcome, name, source, field = None, None, None, "contact.name"
    if chk:
        if not p.name:
            outcome, name, source = "named", chk.value, "github-profile"
        elif p.name_source == "address-first" and chk.value.split()[0].lower() == p.name.lower():
            outcome, name, source = "upgraded", chk.value, "github-profile"
        else:
            outcome = "kept"
    elif not p.name:
        first = _one_word_first(value, p, login)
        if first:
            outcome, name, source, field = "first_only", first, "github-first", "contact.first_name"
    if name:
        p.name, p.name_source = name, source
        session.add(p)
        session.add(ExtractionWrite(company_id=p.company_id, contact_id=p.id, field=field, value=name,
                                    evidence=f"display name on github.com/{login}", source=src,
                                    channel="github-name", confidence=chk.confidence if chk else "medium"))
        return outcome
    reason = ("kept the name already on the row" if outcome == "kept"
              else NO_NAME_REASON if not value or status == "missing"
              else f"GitHub display name refused: {guardrails.person_name(value).reason}")
    guardrails.reject(session, company_id=p.company_id, contact_id=p.id, field="contact.name",
                      value=value or login, reason=reason, source=src, channel="github-name")
    return outcome or "no_name"


def reuse_one_word_rejects(*, sessions=None) -> int:
    """Display names an earlier pass refused as one word, read again under the first-name
    rule — from the reject rows, no request."""
    sessions = sessions or get_session
    n = 0
    with sessions() as session:
        rows = session.exec(select(ExtractionReject).where(
            ExtractionReject.channel == "github-name",
            col(ExtractionReject.reason).like("GitHub display name refused: one word%"))).all()
        for r in rows:
            p = session.get(Contact, r.contact_id) if r.contact_id else None
            login = _login(p) if p else None
            if not p or p.name or not login:
                continue
            first = _one_word_first(r.value, p, login)
            if first:
                p.name, p.name_source = first, "github-first"
                session.add(p)
                session.add(ExtractionWrite(company_id=p.company_id, contact_id=p.id, field="contact.first_name",
                                            value=first, evidence=f"display name on github.com/{login}",
                                            source=f"https://github.com/{login}", channel="github-name",
                                            confidence="medium"))
                n += 1
        _commit(session)
    return n


def github_pass(budget: int = GITHUB_PER_CYCLE, *, client=None, pause_s: float = GITHUB_PAUSE_S,
                sessions=None) -> dict:
    """Up to `budget` public profile pages read for a display name. A 403/429 stops the
    pass and cools it for an hour; a profile with no name is not re-read for a month."""
    out = {"requests": 0, "named": 0, "upgraded": 0, "first_only": 0, "kept": 0, "no_name": 0, "stopped": None}
    if budget <= 0:
        return out
    sessions = sessions or get_session
    with sessions() as session:
        if _cooling(session):
            out["stopped"] = "cooling down after a GitHub 403/429"
            return out
        ids = candidates(session, budget)
    own = client is None
    if own:
        import httpx

        client = httpx.Client(follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0 Safari/537.36"})
    try:
        for n, cid in enumerate(ids):
            with sessions() as session:
                p = session.get(Contact, cid)
                login = _login(p) if p else None
                if not p or not login:
                    continue
                if n and pause_s:
                    time.sleep(pause_s)
                status, value = profile_name(client, login)
                out["requests"] += 1
                if status == "limited":
                    out["stopped"] = f"GitHub {value}"
                    _cool(session, 60)
                    break
                if status == "error":
                    continue
                out[apply_profile_name(session, p, login, value, status)] += 1
                _commit(session)
    finally:
        if own:
            client.close()
    return out


def step(github_budget: int = GITHUB_PER_CYCLE, *, sessions=None) -> dict:
    """The referral job's naming step: the address backfill (cheap, local), then a
    budgeted GitHub pass. Never raises."""
    out: dict = {}
    try:
        out["address"] = backfill(sessions=sessions)
    except Exception as e:  # noqa: BLE001
        log.warning("names: address backfill failed: %s", e)
        out["address_error"] = str(e)[:200]
    try:
        out["github"] = github_pass(github_budget, sessions=sessions)
        out["github_one_word"] = reuse_one_word_rejects(sessions=sessions)
    except Exception as e:  # noqa: BLE001
        log.warning("names: GitHub pass failed: %s", e)
        out["github_error"] = str(e)[:200]
    return out


__all__ = ["Derived", "from_address", "fill_vetted", "name_contact", "demote_junk", "repair_escaped", "backfill",
           "github_pass", "apply_profile_name", "reuse_one_word_rejects", "profile_name", "candidates", "step", "GIVEN", "SURNAMES"]
