[← 3 OOP](03-object-oriented-design.md) · [System design](README.md) · **4 SOLID** · [5 Design patterns →](05-design-patterns.md)

# 4. SOLID principles

## S: Single Responsibility (one reason to change)
| Module | Its one job |
|---|---|
| `outreach/ledger.py` | the daily send cap: `reserve()` and `release()`, nothing else |
| `admission.py` | decide whether a company may be created |
| `fit.py` | score resume-vs-job word overlap |
| `normalize.py` | turn raw values into clean ones (salary, fingerprint, remote rule) |
| `scrapers/himalayas.py` | read one job board |
| `access.py` | decide who may call which route |

If the sending limit changes, only `ledger.py` changes. If Himalayas changes its API, only `himalayas.py` changes.

## O: Open/Closed (open to extension, closed to modification)
Adding a job source needs no change to the pipeline:
```python
# jobhunter/scrapers/__init__.py
REGISTRY = {
    "greenhouse": "jobhunter.scrapers.greenhouse",
    ...
    "himalayas": "jobhunter.scrapers.himalayas",   # added 13 Nov: one line
}
```
Adding Himalayas took three things: a new file, one registry line, and `sources.himalayas: true` in `config.yaml`. `pipeline.py` was not edited. The same holds elsewhere:
- **A new AI model** is a line in `config.yaml`.
- **A new account backend** is a class that implements `AccountStore`.

## L: Liskov Substitution (a subtype can replace its parent)
- **The two stores are interchangeable.** `SqliteStore` and `CatalystStore` can each stand in for `AccountStore`, and the sign-in routes work unchanged with either. Tests swap in a temporary SQLite store with `set_store()`.
- **The error subtypes are interchangeable.** Any subclass of `LLMUnavailable` can be raised where callers catch `LLMUnavailable`, and they still behave correctly.

## I: Interface Segregation (small, focused interfaces)
- **`AccountStore`** has two methods, and the sign-in code needs nothing more.
- **A scraper** needs only `fetch`. It does not have to implement saving, scoring or logging.
- **Each endpoint has its own small request model** (`ApproveIn {subject?, body?}`, `DraftIn`, `StartBody {email}`, `VerifyBody {challenge, code}`) instead of one large shared object.

## D: Dependency Inversion (depend on abstractions)
| High-level code | Depends on (abstraction) | Not on (detail) |
|---|---|---|
| matcher, drafter, decide | `llm.complete(alias)` | OpenRouter, a specific model id |
| `accounts/routes.py` | `AccountStore` via `get_store()` | SQLite or Catalyst |
| scrapers | an injected `http` client: `fetch(http, companies)` | creating their own connections |
| every stage | `CONFIG` values | hard-coded thresholds |

Dependency injection in practice: `pipeline.scrape` creates one HTTP client with `scrapers/base.make_client()` and passes it into every scraper. That makes scrapers easy to test with a fake client.

---
[← 3 OOP](03-object-oriented-design.md) · [System design](README.md) · [5 Design patterns →](05-design-patterns.md)
