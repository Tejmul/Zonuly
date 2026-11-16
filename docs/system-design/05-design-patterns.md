[← 4 SOLID](04-solid-principles.md) · [System design](README.md) · **5 Design patterns** · [6 Diagrams →](06-sequence-and-activity-diagrams.md)

# 5. Design patterns

## Creational

### Singleton: one shared instance
| Where | Why |
|---|---|
| `jobhunter/__init__.CONFIG` (loaded once at import) | every module reads the same settings |
| `db.engine` | one connection pool per process; two would fight over the SQLite file |
| `accounts/store.get_store()` | one account store, created lazily and guarded by a `threading.Lock` |

```python
def get_store() -> AccountStore:
    global _store
    with _store_lock:
        if _store is None:
            kind = os.environ.get("ZONULY_ACCOUNTS", "sqlite").strip().lower()
            _store = CatalystStore() if kind == "catalyst" else SqliteStore()
        return _store
```

### Factory: create without naming the class
- **`get_store()`** (above) picks the class from the environment.
- **`scrapers.get_fetcher(name)`** turns a source name into its `fetch` function through the `REGISTRY`.
- **`scrapers/base.make_client()`** builds the HTTP client with the project's timeout, headers and limits.

**Why:** callers ask for "the account store" or "the himalayas source" without knowing the class or import path.

### Builder: not used
No object here is assembled in many optional steps. Dataclasses with default values (`RawJob`, `Verdict`) already cover optional fields, so a Builder would add ceremony without benefit.

## Structural

### Decorator: wrap behaviour around something unchanged
- `app.add_middleware(snapshot_cache.SnapshotCache)` and the access middleware in `api.py` wrap **every** route with caching and access control.
- FastAPI's `@app.get(...)` / `@app.post(...)` decorate handler functions; `@dataclass` decorates value classes.

**Why:** a route added next month is protected and cached without anyone remembering to do it.

### Adapter: convert one interface into another
- **`llm.py`** keeps the old function signatures over the new OpenRouter client, so nine call sites did not change.
- **`actors/apify_source.py`** converts the vendor's records into our person shape.
- **`scrapers/himalayas.to_raw()`** converts board JSON into `RawJob`.

**Why:** outside formats stay at the edge of the system.

### Facade: one simple entry to a complex part
- **`llm.py`** is the only place a model is called; behind it are aliases, chains, budgets and the cost ledger.
- **`tick.py`** gives one entry per scheduled job (`run("free")`).

### Proxy: a stand-in that controls access
- **`dashboard/proxy.ts`** checks the signed session cookie before any page renders.
- **Vercel's `/zapi/*` rewrite** forwards requests to the API and can cache the answers.

### Repository: storage behind a collection-like interface
- **`accounts/store.py`** (`upsert_login`, `count`).
- **`kg/store.Graph`** (`upsert_node`, `upsert_edge`, `prune`).

## Behavioural

### Strategy: swap an algorithm by configuration
- **`AccountStore` implementations**: SQLite or Catalyst.
- **One scraper per source**: each implements `fetch` its own way.
- **`confidence.py`**: one engine with two weight profiles, India and remote.

### Observer: react to an event without the sender knowing
```python
# jobhunter/db.py
@event.listens_for(Session, "before_flush")
def _only_verified_addresses(session, _flush_context, _instances) -> None:
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Contact): continue
        ...  # an address without a 'safe' EmailCheck is moved to email_unverified
```
Also `@event.listens_for(engine, "connect")` sets WAL mode on every connection.

**Why:** guessed emails once bounced and got Gmail blocked. This listener runs on every save from every code path, including code written later.

### Chain of Responsibility: pass along until someone handles it
```python
# jobhunter/openrouter.py → complete()
for i, model_id in enumerate(chain):          # the alias's ranked models
    try:
        return _complete_one(prompt, ..., model_id=model_id)
    except (RateLimited, _ModelGone) as e:
        last = e                                # hand over to the next model
raise last
```
`outreach/autoreview.py` and `outreach/gates.py` pass each draft through a chain of checks, and any check can reject it with a reason.

### State: behaviour depends on the current state
`Email.status` moves `draft → approved → sending → sent → replied` (or `rejected`, `failed`). `sender.send_email` refuses anything not `approved`.

## Summary

| Pattern | Kind | Where |
|---|---|---|
| Singleton | creational | `CONFIG`, `db.engine`, `get_store()` |
| Factory | creational | `get_store()`, `get_fetcher()`, `make_client()` |
| Builder | creational | not used (reason above) |
| Decorator | structural | middleware, route decorators |
| Adapter | structural | `llm.py`, `apify_source.py`, `himalayas.to_raw` |
| Facade | structural | `llm.py`, `tick.py` |
| Proxy | structural | `dashboard/proxy.ts`, `/zapi` |
| Repository | architectural | `accounts/store.py`, `kg/store.Graph` |
| Strategy | behavioural | stores, scrapers, confidence profiles |
| Observer | behavioural | SQLAlchemy `before_flush`, `connect` listeners |
| Chain of Responsibility | behavioural | model fallback chain, auto-review checks |
| State | behavioural | `Email.status` |

---
[← 4 SOLID](04-solid-principles.md) · [System design](README.md) · [6 Diagrams →](06-sequence-and-activity-diagrams.md)
