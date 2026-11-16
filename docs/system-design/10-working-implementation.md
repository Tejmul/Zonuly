[← 9 Errors](09-error-handling-and-edge-cases.md) · [System design](README.md) · **10 Implementation** · [11 Code flow →](11-architecture-and-code-flow.md)

# 10. Working implementation

## 10.1 Run it
```bash
uv sync                                          # install locked dependencies
python scripts/run.py doctor                     # check configuration and keys
python scripts/run.py scrape --sources himalayas # collect remote jobs open to India
python scripts/run.py serve                      # API on http://localhost:8000
cd dashboard && npm install && npm run dev       # dashboard on http://localhost:3000
.venv/bin/python -m pytest -q                    # about 630 test cases
python scripts/export_referrals_csv.py --remote  # CSV hand-off, proved addresses only
```

## 10.2 End-to-end demo (about 5 minutes)
1. **Scrape:** `scrape --sources himalayas`; new remote roles appear on the **Roles** page.
2. **Company:** open **Companies**, pick a remote company, and show its facts, each with a source quote and link.
3. **Decision:** show its P(offer) and the reason (or "fill first").
4. **Leads:** open **Leads** to see the people found, with proved addresses only.
5. **Outreach:** draft an ask, approve it, and show the daily cap counting down.
6. **Hand-off:** export the CSV.
7. **Live copy:** zonuly.tejmul.in, signed in with an email code, read-only.

## 10.3 Where the design lives in the code
| Design element | File |
|---|---|
| Entities and schema (§2, §8) | `jobhunter/db.py` |
| `AccountStore` interface (§3) | `jobhunter/accounts/store.py` |
| Scraper interface and registry (§4 O/C) | `jobhunter/scrapers/__init__.py`, `scrapers/base.py` |
| Admission gate | `jobhunter/admission.py` |
| Observer guard on every save (§5) | `jobhunter/db.py` (`before_flush`) |
| Model fallback chain (§5) | `jobhunter/openrouter.py` |
| Send flow and daily cap (§6.4, §9.4) | `jobhunter/outreach/sender.py`, `outreach/ledger.py` |
| Controllers (§7) | `jobhunter/api.py`, `accounts/routes.py` |
| Access middleware | `jobhunter/access.py` |
| Scheduled jobs | `jobhunter/tick.py`, `~/Library/LaunchAgents/com.zonuly.*.plist` |
| Dashboard | `dashboard/app/*`, `dashboard/proxy.ts` |

## 10.4 Project layout
```
jobhunter/      Python backend: db, tick, pipeline, scrapers/, contacts/, actors/, outreach/, accounts/, kg/
dashboard/      Next.js website
scripts/        command line (run.py), CSV export, snapshot, smoke test
tests/          pytest suite (about 630 cases)
docs/           this system design folder
config.yaml     every limit, threshold and model name
```

## 10.5 Quality and delivery
- **Tests:** about 630 test cases with pytest, covering rules such as the remote-from-India check, the daily cap and the admission gate.
- **CI/CD:** `.github/workflows/snapshot.yml` smoke-tests the public copy, builds it, deploys the API to Zoho Catalyst, then smoke-tests the live API. Vercel deploys the dashboard on every merge to `main`.
- **Size:** 306 code files and 89,534 lines.

## 10.6 Individual contributions
Fill this in before the evaluation. Each member explains their own part.

| Area | Main files | Owner |
|---|---|---|
| Scraping and admission | `scrapers/`, `pipeline.py`, `admission.py`, `normalize.py` | |
| Decision model | `fit.py`, `matcher.py`, `decide.py`, `remote.py` | |
| People and email proof | `contacts/`, `actors/`, `referrals.py` | |
| Outreach | `outreach/` | |
| API, auth and deployment | `api.py`, `access.py`, `accounts/`, `.github/workflows/` | |
| Dashboard | `dashboard/` | |

---
[← 9 Errors](09-error-handling-and-edge-cases.md) · [System design](README.md) · [11 Code flow →](11-architecture-and-code-flow.md)
