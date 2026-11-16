[← 2 Class diagram](02-class-diagram.md) · [System design](README.md) · **3 OOP** · [4 SOLID →](04-solid-principles.md)

# 3. Object-oriented design

## Abstraction: show what, hide how
- **`llm.complete(prompt, alias="judge")`**: callers say *what kind* of model work they need. Which provider, which model id, the fallback chain and the cost logging are hidden in `llm.py` / `openrouter.py`.
- **`AccountStore`**: the sign-in code sees two operations, `upsert_login` and `count`. It never sees SQLite or Catalyst's REST API.
- **`admission.admit(candidate) -> Verdict`**: one call answers "may this company be created?". Name cleaning, duplicate lookup and staffing-agency checks are inside.

## Encapsulation: state is private, changed only through methods
```python
# jobhunter/tick.py
class _Lock:
    def __init__(self, path: Path = LOCK, what: str = "tick"):
        self.path = path
        self.what = what

    def __enter__(self):
        self.fh = open(self.path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise RuntimeError(f"another {self.what} is running")
        ...
    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()
```
Callers only write `with _Lock(...):`. The file handle and the locking calls stay inside the class.

Other examples:
- `actors/core.Fetcher` keeps its cache and circuit state private (`_cache_path`, `_cached`, `trip`).
- `accounts/realness.Window` is a rate-limit window whose counters change only through `allow()` and `clear()`.
- A leading underscore marks module-private state (`_store`, `_conn`, `_row`).

## Inheritance: an "is-a" relationship with shared behaviour
```python
# jobhunter/openrouter.py
class LLMUnavailable(RuntimeError): ...       # no key, provider down, model refused
class BudgetExceeded(LLMUnavailable): ...     # cap spent, not retryable
class NotFree(LLMUnavailable): ...            # would cost money while free_only is on
class RateLimited(LLMUnavailable): ...        # 429, retryable by the run owner
class _ModelGone(LLMUnavailable): ...         # this model id can't serve, try the next
```
- **Database entities:** 26 classes inherit `SQLModel`, which gives them ORM mapping and validation.
- **API request bodies:** 19 classes inherit Pydantic `BaseModel` (for example `ApproveIn`, `DraftIn`, `StartBody`).

## Polymorphism: one call, different behaviour
- **Through an interface:** `get_store().upsert_login(email, provider="email")` runs `SqliteStore`'s code on a laptop and `CatalystStore`'s code when deployed. The caller is the same.
- **Through a shared function signature:** `pipeline.scrape` calls `fetch(http, companies)` on 20 different sources, and each source parses its own board.
- **Through exception subtypes:** `except LLMUnavailable:` handles `RateLimited`, `NotFree` and `BudgetExceeded` alike, so the stage degrades instead of crashing.

## Interfaces and abstract types
```python
# jobhunter/accounts/store.py
@runtime_checkable
class AccountStore(Protocol):
    name: str
    def upsert_login(self, email: str, *, provider: str, name: str | None = None,
                     google_sub: str | None = None, request=None) -> dict: ...
    def count(self, request=None) -> int: ...

def get_store() -> AccountStore: ...
```
`isinstance(SqliteStore(...), AccountStore)` is `True`.

The scraper interface is the shared signature `async def fetch(http, companies) -> list[RawJob]`. `scrapers/__init__.get_fetcher(name)` returns it.

## Why mostly modules, not deep class trees
In Python a module is a first-class unit, so each stage is a module with one clear public function.
- **Inheritance is used only where it carries real behaviour:** the ORM base class, exception families and request validation.
- **Everywhere else, composition and interfaces are preferred.** That keeps the code testable and avoids fragile base classes.

---
[← 2 Class diagram](02-class-diagram.md) · [System design](README.md) · [4 SOLID →](04-solid-principles.md)
