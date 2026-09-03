# Deploying the public demonstration

Frontend on Vercel, API on Railway, one read-only snapshot of the real database. Visitors
see the companies, the roles, the leads and the atlas exactly as they are. Nobody can
send anything, spend anything, or walk away with 3,737 email addresses.

This does not replace the local install. The pipeline keeps running on your machine; the
deployment is a window onto a copy of what it found.

---

## What the public instance is allowed to do

`ZONULY_PUBLIC=1` puts [`jobhunter/access.py`](../jobhunter/access.py) in charge. One
middleware sits in front of every route, so a route added next month is covered without
anyone remembering to cover it.

| | Public visitor | You, with `X-API-Key` |
|---|---|---|
| Companies, roles, leads, atlas, tracker, queue | yes | yes |
| Lead email addresses | masked — `p•••@company.com` | in full |
| Replies, config, profile, model spend, tasks, research | refused | yes |
| Anything that writes: draft, approve, send, scrape, enrich | refused | yes |
| Scheduler (daily scrape, Gmail poll) | never starts | n/a |

The last row matters. A public instance must not run the scheduler: it would scrape from
a datacenter IP, which LinkedIn blocks on sight, and poll a mailbox nobody there owns.

Two things this is **not**: it is not a login, and masking is not anonymisation. The
people in the snapshot are real, they did not ask to be on a public page, and the
domain half of every address is still visible. If that is not a trade you want, rebuild
the snapshot from a filtered database instead.

---

## 1. Build the snapshot

```bash
python scripts/make_demo_db.py        # jobhunter.db 52 MB -> demo.db 35 MB
git add demo.db && git commit -m "Refresh the demo snapshot"
```

It drops the research cache, the model-spend ledger and inbound replies, then vacuums.
What is left is the exhibit: companies, roles, leads, drafts, graph.

`demo.db` is baked into the image, which is the reason the deployment needs no volume,
no upload step and no writable disk. Refreshing the public numbers means re-running this
and redeploying.

---

## 2. API on Railway

New project → Deploy from GitHub repo → this repo. Railway reads
[`railway.json`](../railway.json) and builds [`Dockerfile`](../Dockerfile); nothing to
configure about the build.

Variables:

```
ZONULY_PUBLIC        1
ZONULY_CORS_ORIGINS  https://<your-app>.vercel.app
ZONULY_HOST          0.0.0.0
ZONULY_API_KEY       <python -c "import secrets; print(secrets.token_urlsafe(32))">
```

Do **not** set `OPENROUTER_API_KEY` here. A read-only instance never calls a model, and
a key that is not present cannot be spent.

`PORT` is assigned by Railway; the Dockerfile's `CMD` expands it. `ZONULY_DB_PATH`
already points at the baked snapshot. Settings → Networking → Generate Domain gives you
the URL the frontend will use. Keep replicas at 1.

Check it:

```bash
curl -s https://<api>.up.railway.app/api/health | python -m json.tool | head -20
#   "access": {"public": true, "read_only": true, ...}
#   "scheduler": {"running": false, ...}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<api>.up.railway.app/api/emails/send-approved
#   403
```

If either line disagrees, stop and fix it before pointing anyone at the URL.

---

## 3. Frontend on Vercel

New project → this repo → **Root Directory: `dashboard`**. Next.js is detected; the
build command needs no changes.

Environment variable, set for Production **and** Preview:

```
NEXT_PUBLIC_API_BASE   https://<api>.up.railway.app
```

`NEXT_PUBLIC_*` is inlined at build time, so this has to exist *before* the first build
and a change to it requires a redeploy, not a restart.

Once it deploys, put the Vercel URL into `ZONULY_CORS_ORIGINS` on Railway and redeploy
the API — the two services have to be told about each other in that order, because
neither URL exists until its service does.

---

## 4. What a visitor sees

The landing page is entirely static apart from four counters, which read
`/api/network`. Inside, every page carries a banner saying the instance is read-only and
why the addresses are masked, the Replies tab is not offered, and any action that slips
through returns a sentence explaining itself rather than a bare error.

---

## Driving your own instance remotely

The same deployment is fully operable with the key:

```bash
curl -H "X-API-Key: $ZONULY_API_KEY" https://<api>.up.railway.app/api/config
```

Note what that does *not* get you: the baked snapshot is read-only and separate from
the database on your laptop, so writes go to a copy that a redeploy discards. To operate
a real instance from a browser you need a Railway volume, `ZONULY_DB_PATH` pointing into
it, and the database uploaded there once — at which point you should also give the
frontend a password, because the key lives in a header the browser has to send.

---

## Things that will bite

**Scraping from a datacenter IP.** Even with the scheduler enabled, LinkedIn blocks
Railway's ranges. GitHub, Hacker News, Reddit and the ATS boards are fine. This is a
reason to keep the pipeline on your machine, not a reason to configure something.

**`notify.py` is macOS-only.** It shells out to `osascript` and silently no-ops
elsewhere, so a Linux instance running the pipeline would work perfectly and tell you
nothing. Irrelevant while the scheduler is off; a real gap if you ever turn it on.

**The landing page copy.** It says *"It runs on your machine. Nothing sends itself."*
That stays true — the deployment is a read-only window, and nothing it can do sends
anything. If the deployment ever becomes the working instance, the copy has to change
with it.
