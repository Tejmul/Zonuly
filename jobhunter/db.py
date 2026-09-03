"""Database models: Company, Job, Contact, Email, Reply, Setting."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: Optional[str] = None
    role: Optional[str] = None            # e.g. "AI Engineer", "Recruiter"
    email: Optional[str] = Field(default=None, index=True)
    github: Optional[str] = None
    linkedin: Optional[str] = None
    source: str                           # github | site | hunter | pattern
    confidence: str = "scraped"           # verified | pattern-guessed | scraped
    is_recruiter: bool = False
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
    status: str = Field(default="draft", index=True)  # draft | approved | rejected | sent | replied | failed
    error: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    gmail_message_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    followup_sent: bool = False


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


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    ensure_columns()


def get_session() -> Session:
    return Session(engine)


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
