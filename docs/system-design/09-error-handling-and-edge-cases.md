[← 8 Database](08-database-schema.md) · [System design](README.md) · **9 Errors** · [10 Implementation →](10-working-implementation.md)

# 9. Error handling, validation and edge cases

## 9.1 Validation, at four levels
| Level | Where | What happens on bad input |
|---|---|---|
| API input | Pydantic request models (`ApproveIn`, `StartBody`, …) | **422** with the field that failed, before the handler runs |
| Scraped data | `guardrails.py`, which returns a `Check(ok, value, reason, confidence)` per field | rejected values are logged to `extraction_reject` with the reason |
| New companies | `admission.admit()`, which returns a `Verdict(ok, reason)` | junk names, duplicates and staffing agencies are refused |
| Email addresses | `db._only_verified_addresses` (the `before_flush` listener) | an unproved address is moved to `email_unverified`; a guessed one is refused outright |

## 9.2 HTTP status codes
| Code | When |
|---|---|
| 400 | a service refused (e.g. "email is draft, only approved emails are sent") |
| 401 | not signed in (`access.refusal`) |
| 403 | a write, or an operator-only route, on the public read-only site |
| 404 | unknown id |
| 422 | invalid request body |
| 429 | too many sign-in codes or wrong codes (`accounts/routes.py`) |

## 9.3 Failures and how the system degrades
| Failure | Behaviour | Where |
|---|---|---|
| A job source is down or changed shape | fault-isolated; the other sources continue; the hourly audit reports the drop | `pipeline.py`, `audit.py` |
| A company's website keeps failing | retried after 1, 3, 7, 21, then 60 days | `backoff.py` |
| An AI model is gone or rate-limited | `RateLimited` / `_ModelGone`, so the next model in the chain is tried; if all fail, `LLMUnavailable` and the stage is skipped | `openrouter.complete` |
| AI spending over the cap | `BudgetExceeded` (not retried) | `openrouter.py` |
| An Apify call would cross today's budget | refused before spending (cost estimated first) | `actors/apify_source.py` |
| Gmail send fails after the slot is taken | `ledger.release()` returns the slot | `outreach/sender.py` |
| A job hangs holding a lock | the watchdog flags a live process past its limit with a silent log | `watchdog.py` |
| Public copy older than the code | `/api/health` reports the schema-stamp mismatch; CI smoke tests fail the deploy | `snapshot.py`, `scripts/smoke.py` |

## 9.4 Concurrency
**Two senders racing for the 25th slot.** `ledger.reserve()` takes the SQLite write lock first, so the check and the increment happen in one transaction:
```python
c.execute("BEGIN IMMEDIATE")
rid, cap, used, gcap, gused = _row(c, candidate_id, day)
if used >= cap:
    c.execute("ROLLBACK")
    return False, f"daily cap reached ({used}/{cap})"
c.execute("update sendledger set used=used+1 ... where id=?", (..., rid))
c.execute("COMMIT")
```

**Two scheduled jobs starting at once.** `tick._Lock` uses a non-blocking `fcntl.flock`. The second job raises "another tick is running" and exits, and the OS frees the lock if the holder dies.

**A crash in the middle of a run.** The fill and people steps commit company by company, so the next run carries on. The scrape writes its batch in one transaction, so a crash rolls the whole batch back. Unique keys (`job.url`, `company.name`, the fingerprint) make the re-run idempotent.

**Shared objects across threads.** `get_store()` is guarded by a `threading.Lock`.

**Readers vs writers.** WAL mode means the dashboard reads while a job writes.

## 9.5 Edge cases handled
- The same role posted on three boards is stored once (fingerprint).
- A US-only "remote-first" posting is **not** treated as remote from India. Each listed location is read separately (20 cases in `tests/test_remote_from_india.py`).
- An email with an empty subject or body is never sent.
- An already-sent email is never sent again (`status == "sent"` check).
- An opt-out reply suppresses the person and the company forever.
- A bounce kills that address permanently (`contact.dead_email`).
- A follow-up can be created only once (`email.followup_sent`).
- An address already in any sent CSV is never exported again (`data/merge_sent/`, `mailed_by_tejmul_at`).

---
[← 8 Database](08-database-schema.md) · [System design](README.md) · [10 Implementation →](10-working-implementation.md)
