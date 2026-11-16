[← 1 Requirements](01-requirement-analysis.md) · [System design](README.md) · **2 Class diagram** · [3 OOP →](03-object-oriented-design.md)

# 2. Class diagram

ZoNuLy is Python. Its classes fall into four groups:
- **Entities**: SQLModel classes in `jobhunter/db.py`, each one a database table.
- **Value objects**: dataclasses passed between stages, such as `RawJob`, `Verdict` and `Target`.
- **Services and interfaces**: `AccountStore`, the scraper interface, and modules of functions, shown below as «module».
- **Errors**: an exception family.

There are 133 classes in `jobhunter/`.

## 2.1 Domain entities

```mermaid
classDiagram
  direction LR
  class SQLModel
  class Company {
    +int id
    +str name
    +str name_key
    +str website
    +str linkedin_url
    +str city
    +str status
    +int pay_power
    +json field_meta
    +json retry_state
    +bool do_not_contact
  }
  class Job {
    +int id
    +int company_id
    +str title
    +str url
    +str fingerprint
    +str location
    +bool remote_anywhere
    +float salary_min_lpa
    +int match_score
  }
  class Contact {
    +int id
    +int company_id
    +str name
    +str role_class
    +str email
    +str email_unverified
    +str affiliation
    +datetime mailed_by_tejmul_at
  }
  class Email {
    +int id
    +int contact_id
    +int company_id
    +str subject
    +str body
    +str status
    +str error
    +bool followup_sent
  }
  class Reply {
    +int id
    +int email_id
    +str sentiment
  }
  class EmailCheck {
    +str address
    +str verdict
    +str source
  }
  class CompanyDecision {
    +int company_id
    +str verdict
    +float p_offer
    +float p_lo
    +float p_hi
  }
  class SendLedger {
    +int id
    +str day
    +int cap
    +int used
  }
  SQLModel <|-- Company
  SQLModel <|-- Job
  SQLModel <|-- Contact
  SQLModel <|-- Email
  SQLModel <|-- Reply
  SQLModel <|-- EmailCheck
  SQLModel <|-- CompanyDecision
  SQLModel <|-- SendLedger
  Company "1" o-- "0..*" Job : posts
  Company "1" o-- "0..*" Contact : employs
  Company "1" *-- "0..1" CompanyDecision : decided as
  Contact "1" *-- "0..*" Email : is written
  Email "1" *-- "0..*" Reply : receives
  Contact "0..*" --> "0..1" EmailCheck : proved by address
```

### Why each relationship type
| Relationship | Type | Multiplicity | Reason |
|---|---|---|---|
| Company → Job | **aggregation** (`o--`) | 1 to 0..* | a job row survives its company being merged into a duplicate (`status = merged`) |
| Company → Contact | **aggregation** | 1 to 0..* | same: people outlive a merge |
| Company → CompanyDecision | **composition** (`*--`) | 1 to 0..1 | a decision has no meaning without its company (its primary key *is* `company_id`) |
| Contact → Email | **composition** | 1 to 0..* | an email exists only for the person it is written to |
| Email → Reply | **composition** | 1 to 0..* | a reply exists only as an answer to an email |
| Contact → EmailCheck | **association** (`-->`) | 0..* to 0..1 | linked by address; one proof covers that address wherever it appears |
| SQLModel → every table | **inheritance** (`<|--`) | – | gives ORM mapping and validation |

## 2.2 Services, interfaces, value objects and errors

```mermaid
classDiagram
  direction TB
  class AccountStore {
    <<interface>>
    +str name
    +upsert_login(email, provider, name, google_sub, request) dict
    +count(request) int
  }
  class SqliteStore {
    -Path path
    -_connect()
    +upsert_login() dict
    +count() int
  }
  class CatalystStore {
    -_call()
    -_find()
    +upsert_login() dict
    +count() int
  }
  AccountStore <|.. SqliteStore
  AccountStore <|.. CatalystStore
  class AuthRoutes {
    <<module accounts/routes.py>>
    +email_start(StartBody)
    +email_verify(VerifyBody)
  }
  AuthRoutes ..> AccountStore : uses via get_store()

  class JobSource {
    <<interface>>
    +fetch(http, companies) list~RawJob~
  }
  class greenhouse_py {
    <<module>>
  }
  class himalayas_py {
    <<module>>
  }
  class remoteok_py {
    <<module>>
  }
  JobSource <|.. greenhouse_py
  JobSource <|.. himalayas_py
  JobSource <|.. remoteok_py
  class RawJob {
    <<dataclass>>
    +str company_name
    +str title
    +str url
    +str location
    +bool remote
    +str salary_raw
  }
  JobSource ..> RawJob : emits
  class Candidate {
    <<dataclass>>
  }
  class Verdict {
    <<dataclass>>
    +bool ok
    +str reason
    +str name
    +str website
  }
  class Admission {
    <<module admission.py>>
    +admit(Candidate) Verdict
  }
  Admission ..> Candidate
  Admission ..> Verdict

  class RuntimeError
  class LLMUnavailable
  class RateLimited
  class NotFree
  class BudgetExceeded
  RuntimeError <|-- LLMUnavailable
  LLMUnavailable <|-- RateLimited
  LLMUnavailable <|-- NotFree
  LLMUnavailable <|-- BudgetExceeded

  class Lock {
    <<tick._Lock>>
    -path
    -fh
    +__enter__()
    +__exit__()
  }
  class SnapshotCache {
    <<ASGI middleware>>
    +__call__(scope, receive, send)
  }
```

| Class | File | Role |
|---|---|---|
| `AccountStore` | `accounts/store.py` | interface (Python `Protocol`) for where accounts live |
| `SqliteStore`, `CatalystStore` | `accounts/store.py` | the two implementations (laptop, deployed) |
| `RawJob` | `scrapers/base.py` | what every scraper emits |
| `Candidate`, `Verdict` | `admission.py` | input and output of the admission gate |
| `Check` | `guardrails.py` | one validator's verdict (`ok`, `value`, `reason`, `confidence`) |
| `Target` | `remote.py` | a remote-from-India company and its best role |
| `LLMUnavailable` family | `openrouter.py`, `llm.py` | typed AI-call failures |
| `_Lock` | `tick.py` | file lock as a context manager |
| `SnapshotCache` | `snapshot_cache.py` | response cache wrapped around the API |
| `Graph` | `kg/store.py` | repository for the knowledge graph (`upsert_node`, `upsert_edge`) |

---
[← 1 Requirements](01-requirement-analysis.md) · [System design](README.md) · [3 OOP →](03-object-oriented-design.md)
