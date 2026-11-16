[← 5 Design patterns](05-design-patterns.md) · [System design](README.md) · **6 Diagrams** · [7 API →](07-api-and-interface-design.md)

# 6. Sequence and activity diagrams

## 6.1 Scheduled scrape (UC1, UC2)
```mermaid
sequenceDiagram
  autonumber
  participant L as launchd
  participant R as scripts/run.py
  participant T as tick.py
  participant P as pipeline.py
  participant S as scrapers/himalayas.py
  participant N as normalize.py
  participant A as admission.py
  participant DB as db.py (SQLite)
  L->>R: tick run --kind free
  R->>T: run("free")
  T->>T: with _Lock(data/tick.lock), exit if busy
  T->>P: scrape(sources)
  P->>S: await fetch(http, companies)
  S-->>P: list[RawJob]
  loop each unique RawJob
    P->>N: keep, fingerprint, parse_salary, remote_anywhere
    P->>A: admit(Candidate) returns Verdict
    A-->>P: ok or reason
    P->>DB: insert or update Job
  end
  P->>DB: COMMIT (one transaction for the batch)
  P-->>T: ScrapeStats
  T->>DB: setting tick:last:free
```

## 6.2 Decide a company (UC3)
```mermaid
sequenceDiagram
  autonumber
  participant T as tick.py
  participant F as fit.py
  participant M as matcher.py
  participant L as llm.py / openrouter.py
  participant D as decide.py
  participant DB as SQLite
  T->>F: score_job(job) (free word overlap)
  F-->>T: fit score
  alt passes the gate
    T->>M: score_job(job, profile)
    M->>L: complete(prompt, alias="judge")
    L-->>M: JSON answer (or next model in chain)
    M->>DB: match_score, reasons
  end
  T->>D: run(ids)
  D->>DB: CompanyDecision(verdict, p_offer, p_lo, p_hi)
```

## 6.3 Find people and prove addresses (UC4)
```mermaid
sequenceDiagram
  autonumber
  participant T as tick.py (apify)
  participant Q as referrals.py
  participant R as remote.py
  participant AP as actors/apify_source.py
  participant DB as db.py
  T->>Q: apify_step()
  Q->>R: targets(need_linkedin=True)
  R-->>Q: remote companies first
  Q->>AP: buy People tab (company)
  AP->>AP: estimate cost, check today's cap, check 24 h cache
  AP-->>Q: people + vendor email proof
  Q->>DB: save Contacts
  DB->>DB: before_flush: unproved address moved out
```

## 6.4 Send one approved email (UC7)
```mermaid
sequenceDiagram
  autonumber
  participant T as tick.py (send)
  participant S as outreach/sender.py
  participant SU as outreach/suppress.py
  participant LG as outreach/ledger.py
  participant G as outreach/gmail.py
  participant DB as SQLite
  T->>S: send_email(email_id)
  S->>DB: load Email
  alt status is not approved, or empty body
    S-->>T: error
  else approved
    S->>SU: domain_cap, referrer checks
    S->>LG: reserve() with BEGIN IMMEDIATE
    alt cap reached
      LG-->>S: False, daily cap reached
    else slot taken
      S->>G: send(to, subject, body)
      alt Gmail error
        S->>LG: release() returns the slot
      else accepted
        S->>DB: status = sent, thread id
      end
    end
  end
```

## 6.5 Sign in (UC11)
```mermaid
sequenceDiagram
  autonumber
  participant U as Visitor
  participant API as accounts/routes.py
  participant RL as accounts/realness.py
  participant M as accounts/mail.py
  participant TK as accounts/tokens.py
  participant ST as AccountStore
  participant V as dashboard/proxy.ts
  U->>API: POST /api/auth/email/start {email}
  API->>RL: format, throwaway, MX, rate window
  API->>M: send 6-digit code
  API-->>U: {challenge}
  U->>API: POST /api/auth/email/verify {challenge, code}
  API->>TK: verify challenge and code
  API->>ST: upsert_login(email, provider="email")
  API-->>U: Set-Cookie zonuly_session (HMAC-signed)
  U->>V: GET /companies
  V->>V: check signature and expiry, else redirect /login
```

## 6.6 Activity: auto-review a draft (UC6)
```mermaid
flowchart TD
  A([Draft created]) --> B{Referrer confirmed current<br/>and not suppressed?}
  B -- no --> R([Reject, reason to logs/autoreview.log])
  B -- yes --> C{Address proved?}
  C -- no --> R
  C -- yes --> D{Quality ok,<br/>no blocking flag?}
  D -- no --> R
  D -- yes --> E{Fresher role?}
  E -- no --> R
  E -- yes --> F{Greeting, resume link,<br/>no unfilled slot?}
  F -- no --> R
  F -- yes --> OK([Approve: status = approved])
```

## 6.7 Activity: one scheduled job run
```mermaid
flowchart TD
  S([launchd fires]) --> L{Lock free?}
  L -- no --> X([Exit: another run is going])
  L -- yes --> N[Pick the most stale step]
  N --> I[Process one item]
  I --> C[COMMIT]
  C --> M{More items and<br/>time budget left?}
  M -- yes --> I
  M -- no --> W[Write summary to setting and log]
  W --> E([Release lock, exit])
```

## 6.8 State: email lifecycle
```mermaid
stateDiagram-v2
  [*] --> draft
  draft --> approved: rules pass
  draft --> rejected: reason logged
  approved --> sending: ledger slot taken
  sending --> sent: Gmail accepted
  sending --> approved: failed, slot returned
  sent --> failed: bounce, address killed
  sent --> replied
  replied --> [*]
  rejected --> [*]
```

---
[← 5 Design patterns](05-design-patterns.md) · [System design](README.md) · [7 API →](07-api-and-interface-design.md)
