# Mandate from Tejmul — 2026-11-01

In his words, lightly ordered. This is what the company is for until he changes it.

> Our goal right now is to get placed. We don't have enough leads to send. I have a setup where I can
> send 500 Gmail a day, so I want a hell of a lot of emails which are real and verified — do anything
> and get as many leads as possible. The companies you choose should match our motive companies.
> In a day I need approx 1,000 emails. The mail automation I will take care of. Scrape all the
> things — we have infra right now: free tools like Agent Reach, Exa, Apify. The CTO knows how to
> plan and optimize — do it.

## What he decided when asked (same day)

- **Money: none beyond today.** Apify stays capped at **$0.50/day**; no other paid data; no VPS
  for SMTP checks "not yet". Free tiers, free tools, the laptop.
- **Sending is his**, from his own setup. The pipeline never mails. Our 25/day cap is untouched
  because we do not send at all.
- **A CSV is a 1,000-address batch** (clarified when ZON-4 merged): leads accumulate in the database
  until there are enough; nothing is written until he asks. "Whenever I need a CSV, I'll take it,
  tell you, and mark it as sent."
- **A verified address stays verified.** Once Reacher proved it `real`, do not re-check it, re-label
  it or present it as unverified again — "it's not like you are again adding them and still feel
  like 'this is not verified'". Re-verification loops (`reverify_per_tick`) must not touch `real`.
- **Leads stay in the database.** When he asks for a CSV he gets **only `real` (Reacher-proved)
  addresses**, as a numbered batch: `CSV1`, `CSV2`, `CSV3`… Each batch is recorded. When he says
  "CSV*n* is done", every lead in it is marked *mailed by Tejmul* and is never exported again.
- Companies must match the motive: Bengaluru + Hyderabad, fresher-reachable roles, pay power
  (PPO ≥ ₹20 LPA floor), underrated / recently funded — not the famous names.

## What the numbers said that night (so goals are honest)

- 25,115 scraped addresses Reacher-checked → **246 real** (≈1%); 14,717 invalid; **10,397
  `unknown (io: incomplete)`** — SMTP cannot complete from a home connection, so those cannot be
  proved from this laptop until he allows a VPS.
- 130–300 jobs/day scraped; the hourly tick loses most of its budget to `web_emails` searches
  that keep retrying cooled-down backends, and to `database is locked`.
- 1,000 real addresses/day from free sources is **far** above anything measured. The CEO's goals
  must be numbers that can move this week (real addresses/day, candidate addresses/day, yield,
  jobs/day), with the gap to 1,000 stated plainly in every brief — never a goal that can only be
  met by guessing an address. Rule 2 of COMMON.md is older than this mandate and wins.

## What "do anything" does not include

Guessing or pattern-filling an address (that is what got the inbox blocked), logged-in LinkedIn,
personal accounts, paid verifiers, Ollama, widening the city focus, or cutting the gate. Everything
else — more sources, smarter proving order, re-checking `unknown`s when a route exists, using
Exa / Jina / GitHub / Apify-within-cap harder and better — is in scope.
