"""Database models: Company, Job, Contact, Email, Reply, Setting."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, Session, SQLModel, create_engine

from jobhunter import CONFIG, ROOT


def utcnow() -> datetime:
    """Naive UTC timestamp — SQLite has no tz type, and everything here is UTC by convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    website: Optional[str] = None
    domain: Optional[str] = None          # email domain, e.g. "mstack.com"
    github_org: Optional[str] = None
    ats: Optional[str] = None             # greenhouse | lever | ashby | None
    ats_slug: Optional[str] = None        # board token on the ATS
    notes: Optional[str] = None
    email_pattern: Optional[str] = None   # learned pattern e.g. "{first}.{last}"
    contacts_found_at: Optional[datetime] = None   # last contact-discovery run
    created_at: datetime = Field(default_factory=utcnow)

    # ---- hand-picked lists (jobhunter/lists.py). A company on a list is chased because
    # the list says so — the grade still runs, but it orders, it never excludes.
    list_key: Optional[str] = Field(default=None, index=True)   # e.g. "india-star"
    list_rank: Optional[int] = None                              # position in that list
    city: Optional[str] = None                                   # "Hyderabad", "Bengaluru"
    list_meta: Optional[str] = None                              # JSON: the list's own row (stage, funding, ctc, ...)

    # ---- targeting (jobhunter/targeting.py). What kind of company is this, and do we chase it?
    tier: Optional[str] = Field(default=None, index=True)  # tier1 | tier2 | reject | unknown
    tier_reason: Optional[str] = None     # one line, human-readable, says which rule decided
    description: Optional[str] = None     # one line: what they build
    hq_region: Optional[str] = None       # us | uk | de | nl | eu | india | remote | None
    underrated: Optional[bool] = None     # False when the name is on the hyped list
    hype_reason: Optional[str] = None
    # pay, the two numbers that decide the tier. Null means "not found", never "zero".
    stipend_inr_month: Optional[int] = None
    stipend_evidence: Optional[str] = None   # the sentence it was read from
    ppo_lpa: Optional[float] = None
    ppo_evidence: Optional[str] = None
    # funding: recently funded and still small is the thesis
    funding_stage: Optional[str] = None
    funding_amount_usd_m: Optional[float] = None
    funding_announced: Optional[str] = None
    funding_investors: Optional[str] = None  # JSON list
    funding_evidence: Optional[str] = None
    # the story and the size — what a person would read before deciding to join
    story: Optional[str] = None             # origin and what happened since, in a few sentences
    story_evidence: Optional[str] = None    # where it was read (URL or "YC directory")
    valuation_usd_m: Optional[float] = None # only when a source states it
    valuation_evidence: Optional[str] = None
    team_size: Optional[int] = None
    team_size_evidence: Optional[str] = None   # the sentence (and URL) the headcount was read from
    # how we decided they can pay: stated | funding-strong | funding-weak | none
    pay_basis: Optional[str] = None
    pay_basis_evidence: Optional[str] = None
    # PAY POWER — the visible benchmark (targeting.pay_power): 0–100 and its band
    pay_power: Optional[int] = Field(default=None, index=True)
    pay_power_band: Optional[str] = None     # pays | deep_pockets | funded | thin | unknown
    pay_power_why: Optional[str] = None      # the sentence that decided it
    money_per_head_usd_k: Optional[float] = None
    graded_at: Optional[datetime] = None

    # ---- hiring authenticity (jobhunter/hiring_verify.py)
    careers_url: Optional[str] = None
    hiring_status: Optional[str] = Field(default=None, index=True)
    # verified | careers_only | not_authorized | unreachable | unchecked
    hiring_claim: Optional[str] = None       # the social/board claim we are testing
    hiring_claim_url: Optional[str] = None
    # the hiring post itself — "where did you get this?" answered with a link and a name
    hiring_claim_by: Optional[str] = None      # who posted it: @handle on X, HN user, "Y Combinator"
    hiring_claim_source: Optional[str] = None  # x | hn | yc | ats | careers | reddit | exa
    hiring_claim_at: Optional[datetime] = None # when the post was made, if the source says
    hiring_evidence: Optional[str] = None    # what the company's own page actually said
    hiring_roles: Optional[str] = None       # JSON list of roles found on their own page
    hiring_checked_at: Optional[datetime] = None

    # ---- admission + priority (jobhunter/admission.py, targeting.priority). 2026-10-06.
    name_key: Optional[str] = Field(default=None, index=True)   # admission.name_key(name): the dedup key
    # None = active | "quarantined" (junk / bare and unrescuable) | "merged" (see merged_into)
    # | "below_pay_bar" (an India company whose best full-time pay evidence is under
    # targeting.india.min_lpa — india.py re-checks it every tick and clears it on new evidence).
    # Never deleted: a hidden row keeps its history and is hidden by default.
    status: Optional[str] = Field(default=None, index=True)
    status_reason: Optional[str] = None
    merged_into: Optional[int] = None        # company.id of the survivor when status = "merged"
    # OVERRATED: famous, listed, valued ≥ $5B or raised ≥ $1B (thresholds in config.yaml)
    overrated: Optional[bool] = Field(default=None, index=True)
    overrated_reason: Optional[str] = None
    funding_total_usd_m: Optional[float] = None   # total raised, when a source states it
    # PRIORITY 0–100: recently funded, actively hiring (fresher / remote-anywhere first),
    # pay power, valuation-for-stage. The sentence says which parts scored.
    priority_score: Optional[int] = Field(default=None, index=True)
    priority_why: Optional[str] = None
    # per-step failure markers with backoff: JSON {step: {"tries", "next", "error"}}
    retry_state: Optional[str] = None
    # SCRAPE FIRST: an active company we hold nothing about (no site, description, roles or
    # people). Every fill-in queue takes these before anything else; hygiene.mark_empty sets
    # the flag each tick and clears it once the company has a site and either people or roles.
    scrape_first: Optional[bool] = Field(default=None, index=True)

    # ---- guardrails (jobhunter/guardrails.py). 2026-10-07. Every field a scrape writes
    # carries where it came from: JSON {field: {value, evidence, source, channel, at,
    # confidence, sources, flags}}. A value the validators refused is not here; it is a
    # row in extraction_reject.
    field_meta: Optional[str] = None
    founded_year: Optional[int] = None        # only when a source states it; bounds the funding date
    # COMPLETENESS 0–100: description, funding stage/amount/date, investors, valuation,
    # hiring verified, careers url, ≥3 confirmed-current referrers, ≥1 of them with an
    # address — and the parts still missing (JSON list). Fill queues read it (db.fill_order).
    completeness: Optional[int] = Field(default=None, index=True)
    completeness_missing: Optional[str] = None
    validated_at: Optional[datetime] = None
    # ---- India discovery (jobhunter/india.py). 2026-10-08. When the India discovery step
    # admitted this row with a quote placing it in Bengaluru / Hyderabad; for
    # targeting.india.discovery.fill_first_days it is filled right after the ★ list.
    india_found_at: Optional[datetime] = Field(default=None, index=True)
    india_source: Optional[str] = None     # rss | search | yc | ats — which source found it
    # ---- referrals (jobhunter/referrals.py). 2026-10-08: "referrals are the main thing".
    # The evidence behind email_pattern: JSON {pattern, status: confirmed|weak|conflict|none,
    # agree, matched, votes, examples, stated, at}. email_pattern is only set when confirmed
    # (≥2 agreeing addresses on their domain, or 1 + their own page stating the format).
    email_pattern_evidence: Optional[str] = None
    # SHOWN: what the dashboard lists by default (guardrails.complete_bar, every tick) —
    # website + description passing the guardrails + hiring proof + ≥1 ready referrer with
    # an address. complete_missing is the JSON list of the parts still missing.
    complete: Optional[bool] = Field(default=None, index=True)
    complete_missing: Optional[str] = None
    complete_at: Optional[datetime] = None     # when it last became complete
    # the rolling re-check (referrals.revalidate_companies): JSON {part: reason} for the
    # parts that failed it — website | description | hiring. A failed part is missing.
    recheck_failed: Optional[str] = None
    # ---- outreach suppression (outreach/suppress.py). 2026-10-14. Someone there asked not
    # to be written to again (or the company is on outreach.do_not_contact): no draft, no
    # approval, no send to anyone at the company — ever, unless a person clears it by hand.
    do_not_contact: Optional[bool] = Field(default=None, index=True)
    dnc_reason: Optional[str] = None
    # ---- selection confidence (jobhunter/confidence.py). 2026-10-14. The company's best
    # role by selection score — what the company page and the Best bets view lead with.
    best_selection_score: Optional[int] = Field(default=None, index=True)
    best_selection_band: Optional[str] = None
    best_selection_job_id: Optional[int] = None
    best_selection_market: Optional[str] = None
    # ---- needs analysis (jobhunter/needs.py): public context — keyless search results and
    # public GitHub repos, JSON {at, web: [...], repos: [...], errors}, cached for days
    needs_context: Optional[str] = None
    needs_context_at: Optional[datetime] = None
    # ---- the fill pass (jobhunter/fill.py). 2026-10-14: "if you go to a company, scrape it
    # COMPLETELY". Every open role on their own careers page / ATS (any kind, any place) and
    # the relevant ones (engineering, Bengaluru / Hyderabad / remote — stored as jobs).
    open_roles_count: Optional[int] = None
    relevant_roles_count: Optional[int] = None
    roles_counted_at: Optional[datetime] = None
    # their public LinkedIn company page, read as a guest through Jina Reader
    linkedin_url: Optional[str] = None
    size_band: Optional[str] = None           # LinkedIn's "51-200 employees"
    linkedin_jobs: Optional[int] = None       # postings on the guest job search for the company (second signal)
    linkedin_checked_at: Optional[datetime] = None
    # JSON {part: reason} — why a part is still missing after the pass looked everywhere
    fill_state: Optional[str] = None
    filled_at: Optional[datetime] = Field(default=None, index=True)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # a senior-titled role is kept (the company is hiring) but ranked after fresher roles
    is_senior: bool = Field(default=False, index=True)
    # the posting itself says "work from anywhere / no visa needed" — the ideal company
    remote_anywhere: bool = Field(default=False, index=True)
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)
    company_name: str = Field(index=True)
    title: str
    location: Optional[str] = None
    remote: bool = False
    url: str = Field(index=True, unique=True)   # dedup key
    fingerprint: Optional[str] = Field(default=None, index=True)  # company+title dedup across sources
    source: str                                  # greenhouse | lever | hn | remoteok | ...
    description: Optional[str] = None
    posted_at: Optional[datetime] = None
    scraped_at: datetime = Field(default_factory=utcnow)
    # salary (normalized to INR LPA; null if unknown)
    salary_min_lpa: Optional[float] = None
    salary_max_lpa: Optional[float] = None
    salary_raw: Optional[str] = None
    currency: Optional[str] = None
    salary_extracted: bool = False               # LLM salary pass already ran
    # internship -> PPO, the route most of these companies actually hire freshers by
    is_internship: bool = False
    stipend_inr_month: Optional[int] = None      # monthly stipend, INR
    ppo_lpa: Optional[float] = None              # stated post-conversion package, INR LPA
    # matching
    match_score: Optional[int] = None            # 0-100 shortlist probability
    match_reasons: Optional[str] = None          # LLM explanation
    skill_gaps: Optional[str] = None
    embed_sim: Optional[float] = None
    scored_at: Optional[datetime] = None
    status: str = Field(default="new", index=True)  # new | scored | high_match | ignored | applied
    notified: bool = False
    # ---- selection confidence (jobhunter/confidence.py). 2026-10-14. 0–100 odds of getting
    # selected, one engine with a weight profile per market (india | remote | abroad), its
    # band (high | medium | low | long_shot) and the itemised, evidence-linked breakdown.
    selection_score: Optional[int] = Field(default=None, index=True)
    selection_band: Optional[str] = Field(default=None, index=True)
    selection_market: Optional[str] = Field(default=None, index=True)
    selection_why: Optional[str] = None          # JSON: terms, blockers, cap, top reasons
    selection_inputs: Optional[str] = None       # hash of the inputs — recompute only on change
    selection_at: Optional[datetime] = None
    # ---- needs brief (jobhunter/needs.py): what they actually need, mapped to the resume
    needs_brief: Optional[str] = None            # JSON brief (quotes + URLs, covered / uncovered)
    needs_brief_at: Optional[datetime] = None
    needs_synthesis: Optional[str] = Field(default=None, index=True)   # none | done | pending | rejected | skipped


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: Optional[str] = None
    role: Optional[str] = None            # e.g. "AI Engineer", "Recruiter"
    email: Optional[str] = Field(default=None, index=True)
    github: Optional[str] = None
    linkedin: Optional[str] = None
    x_handle: Optional[str] = None        # their X handle, when their own profile states it — a DM route
    source: str                           # github | site | hunter | pattern | linkedin-public | web
    confidence: str = "scraped"           # verified | pattern-guessed | scraped
    is_recruiter: bool = False
    # what their own profile says they work at (GitHub "company" field) — employment
    # evidence in their words, which commit history alone is not
    employer_claim: Optional[str] = None
    # ---- role classification (jobhunter/contacts/roles.py). Who can actually refer us?
    role_class: Optional[str] = Field(default=None, index=True)
    # founder | senior_engineer | engineer | eng_manager | tech_recruiter | recruiter | other
    seniority: Optional[str] = None       # junior | mid | senior | staff | exec | None
    referral_rank: Optional[int] = Field(default=None, index=True)  # 1 = ask first
    role_evidence: Optional[str] = None   # the text the class was read from
    classified_at: Optional[datetime] = None
    research_notes: Optional[str] = None  # what the researcher found about them
    researched_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    # ---- guardrails (jobhunter/guardrails.py). 2026-10-07. `confidence` above is the
    # ADDRESS (will it bounce?); these say whether the PERSON still works there.
    handle: Optional[str] = None          # the login (GitHub) kept apart from the name
    name_source: Optional[str] = None     # github-profile | commit | linkedin | page | address
    email_kind: Optional[str] = Field(default=None, index=True)   # company | personal | generic | other
    # verified-current | likely-current | unconfirmed | generic — only the first two are
    # ever offered as a referrer (network._reachable, drafting, the review gate)
    affiliation: Optional[str] = Field(default=None, index=True)
    affiliation_evidence: Optional[str] = None
    # org-member | profile-company | team-page | company-address | recent-commit |
    # linkedin-headline | none
    affiliation_source: Optional[str] = None
    affiliation_checked_at: Optional[datetime] = None
    last_commit_at: Optional[datetime] = None   # their latest commit to the company's own (non-fork) repos
    # ---- outreach suppression (outreach/suppress.py). 2026-10-14.
    # bounced | opted_out — what the mailbox told us about this person
    email_status: Optional[str] = Field(default=None, index=True)
    # an address that bounced: taken off `email`, kept here so no writer ever stores it again
    dead_email: Optional[str] = Field(default=None, index=True)
    do_not_contact: Optional[bool] = Field(default=None, index=True)
    dnc_reason: Optional[str] = None
    # ---- verified addresses only (2026-10-30, Tejmul: "if I send the mail, the mail should be
    # delivered"). `email` holds an address ONLY when email_check says it is safe — the flush
    # hook (_only_verified_addresses) enforces it for every writer. Anything else waits here,
    # kept, never erased: the person is still a lead, the address only a hint until proved.
    email_unverified: Optional[str] = Field(default=None, index=True)
    email_verified_by: Optional[str] = None   # reacher | apify | delivered — who proved `email`


class ExtractionReject(SQLModel, table=True):
    """A value a scrape found and the guardrails refused — kept, never silently dropped."""

    __tablename__ = "extraction_reject"
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, index=True)
    contact_id: Optional[int] = Field(default=None, index=True)
    field: str = Field(index=True)
    value: Optional[str] = None
    reason: str = Field(index=True)
    source: Optional[str] = None          # the URL (or "revalidate", "yc-oss") it came from
    channel: Optional[str] = Field(default=None, index=True)   # exa-mcp | jina | github | model | feed …
    at: datetime = Field(default_factory=utcnow, index=True)


class ExtractionWrite(SQLModel, table=True):
    """A value the guardrails accepted, with its provenance — what the hourly audit samples."""

    __tablename__ = "extraction_write"
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, index=True)
    contact_id: Optional[int] = Field(default=None, index=True)
    field: str = Field(index=True)
    value: Optional[str] = None
    evidence: Optional[str] = None
    source: Optional[str] = None
    channel: Optional[str] = Field(default=None, index=True)
    confidence: Optional[str] = None
    at: datetime = Field(default_factory=utcnow, index=True)


class ChannelHour(SQLModel, table=True):
    """Calls per research channel per hour: is Exa MCP / Jina / GitHub / OpenRouter answering?"""

    __tablename__ = "channel_hour"
    channel: str = Field(primary_key=True)
    hour: str = Field(primary_key=True)   # local "YYYY-MM-DDTHH"
    ok: int = 0
    fail: int = 0
    cooldowns: int = 0
    last_ok_at: Optional[datetime] = None
    last_fail_at: Optional[datetime] = None
    last_error: Optional[str] = None


class Email(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    to_email: str
    subject: str
    body: str
    kind: str = "cold"                    # cold | followup
    parent_email_id: Optional[int] = None
    status: str = Field(default="draft", index=True)  # draft | incomplete | approved | rejected | sent | replied | failed
    error: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    gmail_message_id: Optional[str] = None
    # which inbox sent it (outreach/inboxes.py, 2026-10-30); None = the Gmail, before inboxes.
    # A follow-up goes from the same inbox, and the tracker reads replies there.
    sender_inbox: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    followup_sent: bool = False
    # ---- the review gate's evidence (outreach/gates.py)
    review_flags: Optional[str] = None      # JSON list of {kind, detail} the reviewer should see
    evidence: Optional[str] = None          # JSON: the facts the draft was allowed to use, with sources
    expires_at: Optional[datetime] = None   # a draft older than this is stale and cannot be approved
    address_confidence: Optional[str] = None  # verified | pattern-guessed | scraped, frozen at draft time
    candidate_id: int = Field(default=1, index=True)   # which of us this is from (two profiles, one machine)
    # ---- the composer's variant record (outreach/compose.py, 2026-10-14) — so learn.py can
    # say which structure / subject / hook earns replies once there is enough data
    structure: Optional[str] = Field(default=None, index=True)   # proof_opinion | referral_engineer | founder_direct | recruiter_direct
    subject_pattern: Optional[str] = None
    hook_type: Optional[str] = None       # person_post | shared_ground | person_commit | person_repo | person_bio | company_*
    words: Optional[int] = None           # the message, without the blurb and the opt-out line
    quality: Optional[int] = None         # 0–100 (compose.quality)
    quality_detail: Optional[str] = None  # JSON: verdict + parts
    writer: Optional[str] = None          # model | template
    polish_pending: Optional[bool] = None # written by the template while the model was unavailable
    contract: Optional[str] = None        # JSON: the personalisation slots and what was missing
    send_hour: Optional[int] = None       # local hour it left (for learn)
    send_weekday: Optional[str] = None
    # ---- pattern approval (outreach/patterns.py, 2026-10-16): approve a kind of email once
    segment: Optional[str] = Field(default=None, index=True)   # "<structure>:<market>", e.g. referral_engineer:india
    pattern_id: Optional[int] = Field(default=None, index=True)  # the pattern whose template wrote / approved it
    approved_by: Optional[str] = None     # None = you, by hand; "pattern" = auto-approved by pattern_id
    pattern_note: Optional[str] = None    # why a matching pattern did or did not approve it


class SendLedger(SQLModel, table=True):
    """One row per candidate per day: the 25-a-day cap, spent irreversibly inside the send."""
    id: Optional[int] = Field(default=None, primary_key=True)
    candidate_id: int = Field(default=1, index=True)
    day: str = Field(index=True)              # local date, YYYY-MM-DD
    cap: int = 25
    used: int = 0
    guessed_cap: int = 5                      # pattern-guessed addresses bounce; they get their own cap
    guessed_used: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    """A "yes" that carries a time: a call, an assessment, an interview — on our calendar."""
    id: Optional[int] = Field(default=None, primary_key=True)
    reply_id: Optional[int] = Field(default=None, foreign_key="reply.id")
    email_id: int = Field(foreign_key="email.id", index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    contact_id: int = Field(foreign_key="contact.id")
    candidate_id: int = Field(default=1, index=True)
    kind: str = "call"                        # call | assessment | interview | referral_done | other
    starts_at: Optional[datetime] = None      # naive UTC
    ends_at: Optional[datetime] = None
    timezone: Optional[str] = None            # the zone the sender wrote in
    link: Optional[str] = None                # Meet / Zoom / Calendly / assessment URL
    deadline: Optional[datetime] = None       # e.g. "complete the assessment by Friday"
    quote: Optional[str] = None               # the sentence the time was read from
    needs_action: Optional[str] = None        # what we must do, in their words
    status: str = Field(default="proposed", index=True)   # proposed | confirmed | conflict | done | cancelled
    conflict_with: Optional[int] = None       # another event id
    calendar_event_id: Optional[str] = None
    notified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Candidate(SQLModel, table=True):
    """Two of us run the same machine: per-candidate identity, mailbox and caps."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: Optional[str] = None
    headline: Optional[str] = None
    profile_path: Optional[str] = None
    gmail_token_file: Optional[str] = None
    daily_cap: int = 25
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class EmailProposal(SQLModel, table=True):
    """A PROPOSAL of how one cold email would be written (outreach/proposal.py) — shown to
    the user to judge the writing, NEVER in the send pipeline: no status, no approval, no
    sender reads this table. Regenerated on demand (a run replaces the previous run)."""
    __tablename__ = "email_proposal"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    rank: int = 0
    contact_id: Optional[int] = Field(default=None, index=True)
    company_id: Optional[int] = Field(default=None, index=True)
    job_id: Optional[int] = None
    market: Optional[str] = None          # india | remote | abroad
    structure: Optional[str] = None
    complete: bool = True                 # False: the contract is missing a slot, no email written
    payload: str = "{}"                   # JSON: evidence, contract, subjects, body, quality, flags, not claimed
    created_at: datetime = Field(default_factory=utcnow)


class EmailPattern(SQLModel, table=True):
    """An approved KIND of email (outreach/patterns.py): one segment (structure x market)
    and the template the user approved for it, with his edits. When outreach.pattern_approval
    is on, a new draft in an active pattern's segment is written from the template and, if it
    passes every gate, approved without a per-email click — marked so it can be undone
    before its send slot. Revoking sends every unsent one back to review."""
    __tablename__ = "email_pattern"
    id: Optional[int] = Field(default=None, primary_key=True)
    segment: str = Field(index=True)          # "<structure>:<market>"
    structure: Optional[str] = None
    market: Optional[str] = None
    status: str = Field(default="active", index=True)   # active | revoked | replaced
    subject_template: str = ""                # {{slot}} markers: first_name, company, role, hook, proof1...
    body_template: str = ""
    source_email_id: Optional[int] = None     # the draft it was approved from
    edited: bool = False                      # the user changed the draft before approving the pattern
    created_at: datetime = Field(default_factory=utcnow)
    revoked_at: Optional[datetime] = None


class EmailCheck(SQLModel, table=True):
    """What is known about whether one address takes mail (contacts/reacher.py).

    safe     proved: check-if-email-exists said deliverable on a domain that is not
             catch-all, Apify's email search said the same, or a mail to it was delivered
    risky    catch-all domain, full inbox, role account — not proof
    invalid  the mailbox server said the mailbox does not exist
    unknown  nothing could be learned (greylisting, a server that will not talk to us)
    Only `safe` lets an address onto contact.email."""
    __tablename__ = "email_check"
    address: str = Field(primary_key=True)      # lower-case
    verdict: str = Field(index=True)
    source: str = "reacher"                     # reacher | apify | delivered
    catch_all: Optional[bool] = None
    detail: Optional[str] = None
    checked_at: datetime = Field(default_factory=utcnow)


class Reply(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email_id: int = Field(foreign_key="email.id", index=True)
    gmail_message_id: Optional[str] = Field(default=None, index=True, unique=True)
    from_addr: str
    body: str
    sentiment: Optional[str] = Field(default=None, index=True)  # positive | negative | closed | neutral
    sentiment_reason: Optional[str] = None
    received_at: datetime = Field(default_factory=utcnow)
    notified: bool = False


class CompanyDecision(SQLModel, table=True):
    """The decision brain's latest answer for one company (decide.py, 2026-10-23): P(offer)
    with its 80% interval, the five stage probabilities, a verdict and the shortlist slot."""

    company_id: int = Field(foreign_key="company.id", primary_key=True)
    pool: str = Field(index=True)           # Bengaluru | Hyderabad
    verdict: str = Field(index=True)        # target | fill_first | after_dsa | skip
    p_offer: float = Field(index=True)
    p_lo: float                              # 10th percentile of the draws
    p_hi: float                              # 90th percentile
    p_open: float
    p_reach: float
    p_screen: float
    p_call: float
    p_loop: float
    p_pay: float
    dsa: float                               # DSA intensity of their loop, 0..1
    info: float                              # share of the deciding fields we hold
    rank: Optional[int] = Field(default=None, index=True)   # 1.. inside the pool's shortlist; None = outside it
    why: Optional[str] = None                # JSON: every question, its evidence, the verdict's reason
    at: datetime = Field(default_factory=utcnow)


class DailyPick(SQLModel, table=True):
    """One company a day (decide.today). The snapshot is frozen at pick time so
    `decide calibrate` can score what the brain said then against what happened."""

    id: Optional[int] = Field(default=None, primary_key=True)
    day: str = Field(index=True)             # YYYY-MM-DD, local (IST); a skip adds the next pick on the same day
    company_id: int = Field(foreign_key="company.id", index=True)
    action: str                              # fill | ask
    status: str = "picked"                   # picked | filled | asked | skipped
    p_offer: float
    p_reach: float
    verdict: str
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: Optional[datetime] = None


class DecisionOutcome(SQLModel, table=True):
    """What actually happened at a company — the ground truth the priors get scored against."""

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    stage: str = Field(index=True)           # replied | referred | interview | offer | rejected | no_response
    note: Optional[str] = None
    at: datetime = Field(default_factory=utcnow)


class MixerScrape(SQLModel, table=True):
    """One paid lookup the mixer made for one company (mixer.py, 2026-10-23): what it bought,
    what matched the company, what was saved, what it cost, and the culture rating it carried."""

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    source: str = Field(index=True)          # linkedin | naukri
    found: int = 0                            # items the actor returned
    matched: int = 0                          # … whose company is this company
    saved: int = 0                            # … that passed the relevance gate into the job table
    cost_usd: float = 0.0
    cached: bool = False
    error: Optional[str] = None
    culture_rating: Optional[float] = None    # AmbitionBox rating carried on the company's own Naukri ads
    culture_reviews: Optional[int] = None
    stopped: Optional[str] = None             # why the cascade stopped after this layer
    at: datetime = Field(default_factory=utcnow, index=True)


class UpskillItem(SQLModel, table=True):
    """Your progress on one gap the upskill pipeline found (upskill.py, 2026-10-23). Nothing
    reaches the profile without proof: a link and a line in your own words."""

    skill: str = Field(primary_key=True)     # the skill dictionary's canonical name, or "DSA"
    status: str = "todo"                      # todo | learning | done
    proof_url: Optional[str] = None           # the repo / demo / write-up that shows it
    proof_note: Optional[str] = None          # what you built, in your words — the resume line is made from this
    added_to_profile_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class InterviewNote(SQLModel, table=True):
    """What a company's interviews are like (interviews.py, 2026-10-31): one candidate's report
    (Glassdoor, Reddit, a blog), the company's own word on its process, or a prep point drawn
    from those reports. Every row carries the page it came from; nothing is invented."""

    __tablename__ = "interview_note"
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    kind: str = Field(index=True)            # report | official | prep
    source: str                               # glassdoor | reddit | medium | linkedin | github | summary
    source_id: Optional[str] = Field(default=None, index=True)  # the source's own id, so a re-scrape updates
    url: Optional[str] = None
    role: Optional[str] = None
    outcome: Optional[str] = None             # offer | no offer | declined | unknown
    difficulty: Optional[str] = None
    experience: Optional[str] = None          # positive | neutral | negative
    title: Optional[str] = None               # a prep point's heading
    body: str = ""                            # the process as written, or the prep point
    questions: Optional[str] = None           # JSON list, the questions as the candidate wrote them
    evidence: Optional[str] = None            # prep: which reports back it
    reported_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=utcnow)


class ShortlistFill(SQLModel, table=True):
    """One pass of shortlist.py over one shortlisted company (2026-10-23): what was missing
    before, what is still missing after and why, and what it cost."""

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    pool: str = Field(index=True)
    rank: Optional[int] = None
    status: str = Field(index=True)          # complete | partial | failed
    missing_before: Optional[str] = None      # JSON list
    missing_after: Optional[str] = None       # JSON {part: why}
    steps: Optional[str] = None               # JSON: what each step did
    cost_usd: float = 0.0
    seconds: float = 0.0
    at: datetime = Field(default_factory=utcnow, index=True)


class Setting(SQLModel, table=True):
    """Small key/value store for runtime state the config file shouldn't own (send counters, cursors)."""

    key: str = Field(primary_key=True)
    value: str


# The path is a config value locally and an environment variable in a deployment,
# where the database lives on a mounted volume (or is a read-only snapshot baked into
# the image) rather than next to the source.
_DB_ENV = os.environ.get("ZONULY_DB_PATH", "").strip()
DB_PATH = Path(_DB_ENV) if _DB_ENV else ROOT / CONFIG["db_path"]
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
# Several processes write this file at once (the API, a people hunt, a harvest). SQLite
# serialises writers; the default 5 s wait was too short whenever one of them held a
# transaction across a network call, and "database is locked" lost work. 30 s is the
# longest a well-behaved stage holds a write lock, so a waiter always gets its turn.
engine = create_engine(
    f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False, "timeout": 30},
)


# WAL: readers never wait on the writer and the writer never waits on readers, so the
# dashboard's reads, the 10-minute tick and a harvest stop locking each other out. Only
# writers still queue. The mode is stored in the file, so setting it once sticks; the
# public snapshot is cut back to a plain journal by scripts/make_demo_db.py.
@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001 — a read-only file keeps whatever mode it has
        pass
    finally:
        cur.close()


_SQLITE_TYPES = {
    "INTEGER": "INTEGER", "BIGINT": "INTEGER", "BOOLEAN": "BOOLEAN",
    "VARCHAR": "VARCHAR", "TEXT": "VARCHAR", "FLOAT": "FLOAT", "DATETIME": "DATETIME",
}


def ensure_columns() -> list[str]:
    """Add columns the models declare but the database file does not have yet.

    SQLite can only ALTER TABLE ADD COLUMN, which is exactly what every schema change
    here has been so far, so that is the whole migration story — no Alembic, no
    versions table. Runs on every init_db(), is a no-op once the file has caught up,
    and never drops or rewrites anything.
    """
    from sqlalchemy import inspect, text

    added: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for name, table in SQLModel.metadata.tables.items():
            if name not in existing_tables:
                continue  # create_all just made it, with every column
            have = {c["name"] for c in inspector.get_columns(name)}
            for col_ in table.columns:
                if col_.name in have:
                    continue
                sql_type = _SQLITE_TYPES.get(str(col_.type).split("(")[0].upper(), "VARCHAR")
                conn.execute(text(f'ALTER TABLE "{name}" ADD COLUMN "{col_.name}" {sql_type}'))
                added.append(f"{name}.{col_.name}")
    if added:
        import logging

        logging.getLogger(__name__).info("schema: added %d column(s): %s", len(added), ", ".join(added))
    return added


_KEEP_STATUS = ("bounced", "opted_out")


@event.listens_for(Session, "before_flush")
def _only_verified_addresses(session, _flush_context, _instances) -> None:
    """No writer puts an unproved address on contact.email (2026-10-30: guessed addresses at
    sarvam.ai and kapture.cx bounced and got the Gmail blocked). A new or changed address
    without a `safe` email_check row is moved to email_unverified — kept as a hint, never
    mailed — and contacts/reacher.verify_pending proves or replaces it later."""
    from sqlalchemy import inspect as sa_inspect

    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Contact) or not obj.email:
            continue
        if obj not in session.new and not sa_inspect(obj).attrs.email.history.has_changes():
            continue
        addr = obj.email.strip().lower()
        with session.no_autoflush:
            chk = session.get(EmailCheck, addr)
        if chk is not None and chk.verdict == "safe":
            obj.email, obj.email_verified_by = addr, chk.source
            continue
        obj.email, obj.email_unverified, obj.email_verified_by = None, addr, None
        if obj.email_status not in _KEEP_STATUS:
            obj.email_status = f"fake:{chk.verdict}" if chk is not None else "fake:not-proved"


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    ensure_columns()


def get_session() -> Session:
    return Session(engine)


_INDIA_FILL_FIRST_DAYS = int((((CONFIG.get("targeting") or {}).get("india") or {}).get("discovery") or {})
                             .get("fill_first_days", 30))


def fill_order(company: Company) -> tuple:
    """The one order every fill-in queue reads (2026-10-07: "the ★ India list first, then
    all companies"): hand-picked list rows first, in list order; then the Bengaluru /
    Hyderabad companies India discovery found in the last fill_first_days (2026-10-08);
    then the scrape-first rows
    we hold nothing about; then the least-complete of the high-priority companies — priority
    in bands of 20, least complete first inside a band (guardrails.refresh_completeness),
    then exact priority. Callers append their own tie-breakers."""
    listed = bool(company.list_key)
    prio = company.priority_score or 0
    done = company.completeness if company.completeness is not None else 0
    found = company.india_found_at
    fresh_india = (not listed and found is not None
                   and (utcnow() - found).days < _INDIA_FILL_FIRST_DAYS)
    return (0 if listed else 1 if fresh_india else 2, (company.list_rank or 9999) if listed else 0,
            0 if company.scrape_first else 1, -(prio // 20), done, -prio)


def people_order(company: Company) -> tuple:
    """The people step's own order (2026-10-08, "referrers first"): the hand-picked list
    rows in list order; then companies complete except for a referrer with an address;
    then everyone else by priority (db.fill_order's bands)."""
    import json as _json

    listed = bool(company.list_key)
    try:
        missing = set(_json.loads(company.complete_missing or "[]"))
    except (TypeError, ValueError):
        missing = set()
    only_referrer = bool(missing) and missing <= {"referrer_email"}
    return (0 if listed else 1 if only_referrer else 2, (company.list_rank or 9999) if listed else 0,
            *fill_order(company)[2:])


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        row = Setting(key=key, value=value)
    session.add(row)
    session.commit()
