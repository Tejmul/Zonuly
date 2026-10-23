# Deploying the public demonstration

The API runs on **Zoho Catalyst AppSail**; the dashboard runs on **Vercel**, which proxies every
API call through its own origin. Both serve one read-only snapshot of the real database.
Visitors see the companies, the roles, the leads and the graph as they are. Nobody can send
anything, spend anything, or walk away with an email address.

This does not replace the local install. The pipeline keeps running on your machine; the
deployment is a window onto a masked copy of what it found.

| | Where | Address |
|---|---|---|
| API | Catalyst AppSail (Development environment, India data centre), Python 3.12, 1024 MB | `https://zonuly-api-50046893527.development.catalystappsail.in` |
| Dashboard | Vercel, root directory `dashboard` | `https://dashboard-blue-eight-86.vercel.app` |
| Deploys | GitHub Actions, `.github/workflows/snapshot.yml`, on every push to `main` | — |

---

## What the public instance is allowed to do

`ZONULY_PUBLIC=1` puts [`jobhunter/access.py`](../jobhunter/access.py) in charge. One
middleware sits in front of every route, so a route added next month is covered without
anyone remembering to cover it.

| | Public visitor | You, with `X-API-Key` |
|---|---|---|
| Companies, roles, leads, graph, tracker, queue | yes | yes |
| Lead email addresses | masked: `p•••@company.com` | in full* |
| Replies, config, profile, model spend, tasks, research, dossiers | refused | yes |
| Anything that writes: draft, approve, send, scrape, enrich | refused | yes |
| Scheduler (daily scrape, Gmail poll) | never starts | n/a |

\* The snapshot itself already holds no real address (see 1), so even the key holder sees
stand-ins on the deployed copy. Your real addresses only ever live in `jobhunter.db` on the laptop.

The scheduler row matters: a public instance must not scrape from a datacenter IP (LinkedIn
blocks those on sight) or poll a mailbox nobody there owns.

---

## 1. The snapshot

```bash
python scripts/make_demo_db.py        # jobhunter.db -> demo.db (~230 MB) -> demo.db.gz (~42 MB, the one committed)
git add demo.db.gz && git commit -m "Refresh the public snapshot"
```

It copies the live database read-only (SQLite's backup API, safe while the tick is writing),
then for the public copy:

- **empties** what a visitor has no business seeing: the research cache, the model-spend
  ledger, inbound replies, the scrape audit trail (`extraction_reject`, `extraction_write`),
  your Upskill notes, real interview outcomes and the mixer's spend;
- **masks every email address** in every text column of every table, the knowledge graph's
  search index included, with a deterministic stand-in on the same domain
  (`v8f49985@scalio.app`). `demo.db.gz` sits in a public repository, so masking at the API was
  not enough;
- brings the copy up to the code's schema and stamps it, and refuses to write it if a
  hand-picked list comes out short.

`demo.db.gz` is not served from a volume: it ships inside the AppSail build, and `start.sh`
unpacks it to `/tmp` when the instance starts. Refreshing the public numbers means re-running
this and pushing.

---

## 2. API on Catalyst AppSail

Nothing to do by hand: **every push to `main` deploys**, after the smoke test passes.

1. `smoke`: unpacks `demo.db.gz` and runs `scripts/smoke.py --db demo.db`. It fails the push
   when the snapshot no longer matches the code (a new table or column, a list short of
   companies). After a schema change, rebuild the snapshot (section 1) in the same push.
2. `deploy`: `scripts/build_catalyst.py` assembles `deploy/catalyst/appsail/build/` (the code,
   config, lists, `demo.db.gz`, and every dependency as Linux x86_64 wheels, because AppSail's
   managed Python installs nothing); then `catalyst deploy --only appsail --dc in` with the
   repository secret `CATALYST_TOKEN`; then it waits for `/api/health` and runs the smoke test
   against the live URL.

Settings that live in the repository:

- `deploy/catalyst/appsail/app-config.json`: `"memory": 1024`, `"command": "sh start.sh"`. It
  was 512 MB until 23 Oct 2026: the unpacked snapshot plus the app (about 252 MB at warm-up)
  reached the limit and the instance never started.
- `start.sh` sets `ZONULY_DB_PATH=/tmp/zonuly.db` and `ZONULY_PUBLIC=1` and listens on
  `X_ZOHO_CATALYST_LISTEN_PORT`.

Never shipped: `.env`, `secrets/`, `jobhunter.db`, `profile/`, the backups, `reports/`. No model
key is set on AppSail: a read-only instance never calls a model, and a key that is not present
cannot be spent.

**Development vs Production.** The CLI can only deploy to Development, which is the live
backend today. Development instances sleep after a few idle minutes, so the first visitor after
a quiet spell waits 7–8 s. Promoting to Production (a console step) keeps instances up longer;
then point the dashboard's `ZONULY_API_ORIGIN` at the Production URL.

Check it:

```bash
curl -s https://zonuly-api-50046893527.development.catalystappsail.in/api/health | python -m json.tool | head -20
#   "access": {"public": true, "read_only": true, ...}
#   "scheduler": {"running": false, ...}
python scripts/smoke.py https://zonuly-api-50046893527.development.catalystappsail.in
```

If a deploy uploads but `/api/health` answers 503, the instance did not start: memory is the
first suspect. The CLI has no log command; AppSail's logs are in the Catalyst console.

---

## 3. Dashboard on Vercel

Project → this repository → **Root Directory: `dashboard`**. A push to `main` rebuilds it.

The dashboard never calls AppSail directly. `dashboard/next.config.ts` rewrites
`/zapi/*` to the API origin (`ZONULY_API_ORIGIN`, defaulting to the AppSail URL above), and the
build sets `NEXT_PUBLIC_API_PROXY=/zapi`, so the browser only ever talks to the Vercel origin:
no CORS, and Vercel's edge can cache the read-only answers.

**Caching the API at the edge.** The API sends `s-maxage=600, stale-while-revalidate=604800`
on snapshot reads, and `next.config.ts` adds `x-vercel-enable-rewrite-caching: 1` on `/zapi/*`.
If `curl -sI https://dashboard-blue-eight-86.vercel.app/zapi/api/overview | grep -i x-vercel-cache`
still prints `MISS` on a second request, turn on **Project → Settings → "honor cache-control on
external rewrites"**. Once it prints `HIT` or `STALE`, visitors get answers in ~0.1 s even while
AppSail sleeps.

---

## 4. What a visitor sees

The landing page is static apart from its live counters. Inside, every page carries a banner
saying the instance is read-only and why addresses are masked, the Replies and Dossier views
are not offered, and any action that slips through returns a sentence explaining itself rather
than a bare error.

---

## Driving the deployed instance with the key

```bash
curl -H "X-API-Key: $ZONULY_API_KEY" https://zonuly-api-50046893527.development.catalystappsail.in/api/config
```

That reads the deployed copy, nothing more: the snapshot is read-only, separate from the
database on your laptop, and replaced on every deploy. The working pipeline is the one on your
machine.

---

## Things that will bite

**Scraping from a datacenter IP.** LinkedIn blocks cloud ranges. That is a reason to keep the
pipeline on your machine (and Apify for LinkedIn data), not a reason to configure something.

**The snapshot outgrowing the instance.** Keep an eye on `make_demo_db.py`'s size line; the
unpacked file and the app share the instance's memory.

**`notify.py` is macOS-only.** It shells out to `osascript` and no-ops elsewhere. Irrelevant
while the deployed scheduler is off.
