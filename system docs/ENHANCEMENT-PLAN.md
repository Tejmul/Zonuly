# ZoNuLy — Product Enhancement Plan (2026-10-28)

> Written 2026-10-28, after the first ten send days. FINAL-PLAN-V3 is still the plan for what
> the system *is*; this document is the order in which it gets **better at the motive**:
> more true emails reaching real engineers at good companies, more of them answered, and no
> "yes" lost. Every step is numbered for approval (V3 §14 rules: one step at a time, each with
> its files, cost and check, a `kg note` after each). Nothing here starts without its number
> being approved.

---

## 1. The motive, as a funnel

The motive (BRIEF "The problem"): find funded, underrated companies that pay well
(PPO ≥ ₹20 LPA, remote-anywhere first, India = BLR/HYD), find the engineers there who can refer
a fresher, send each one honest email, and turn every reply into an interview on the calendar.

Every stage below either feeds the next or leaks:

```
companies → fresher roles → referrers → a real address → draft → review → SEND → DELIVERED → reply → interview
```

The product is only as good as its worst leak. Today the worst leaks are **at and after SEND**.

## 2. Where we are (measured 2026-10-28)

| Stage | Number | What it says |
|---|---|---|
| Companies / referrers | 6,304 / 39,458 (1,657 verified addresses) | Supply upstream is large |
| Fresher roles scored | 453 scored, 38 high-match | Grading works, but slowly |
| Drafts reviewed by rules | 48 approved · 138 rejected | **3 of 4 drafts are wasted** |
| Sent | 41 attempted (19 Oct → 27 Oct) | One personal Gmail, 25/day cap |
| Delivered | 26 | **37% never arrived** |
| — blocked by the recipient's server | 11 (9 in one Sarvam burst on 19 Oct, 2 since) | Recipient policy, "Message rejected" |
| — address not found | 4 (≈10%) | Safe level is < 2% |
| Replies | 1 (negative) | Too few sends to judge the wording yet |
| Interviews on the calendar | 0 — the calendar step does not exist | MOTIV step 11 is still missing |
| Days lost to the 48 h pause | 26 Oct evening → 29 Oct morning | One block stopped all mail |
| Sendable people left | ≈ 250 | ≈ 10 days at 25/day |

Why the 138 drafts were rejected (`logs/autoreview.log`):

| Reason | Count | The stage that should have caught it |
|---|---|---|
| Spam signal (near-copy / same subject into one company) | 87 | Drafter — variants per company |
| Posting asks 3+ years | 61 | Circle — company's role chosen before drafting |
| Address is a pattern guess | 32 | Circle — never draft a guessed address |
| Not a fresher role (senior / lead / II) | 26 | Circle — same as above |
| Referrer not confirmed current | 4 | People — affiliation check |

## 3. What success looks like (the targets this plan is judged by)

| Measure | Now | Target | By |
|---|---|---|---|
| Delivered / attempted | 63% | ≥ 97% | end of Phase A |
| Address-not-found bounces | ≈ 10% | < 2% | end of Phase A |
| Recipient blocks | 2 per send-week | 0–1 per send-week | end of Phase A |
| Drafts rejected by autoreview | 74% | < 25% | end of Phase A |
| Send days lost to pauses | 2.5 of the last 3 | 0 (only the blocked company waits) | A1 |
| Emails per day | 25 | ≈ 85 (one Gmail + two warmed inboxes) | end of Phase B (≈ 5 weeks) |
| Reply rate | 1 / 26 | ≥ 5% of delivered | measured from Phase E |
| Positive replies with a calendar entry | n/a | 100%, within the hour | end of Phase D |

---

## 4. Phase A — Deliver what we already send (this week, ₹0 to ≈ ₹150/month)

Capacity is pointless while 37% of mail does not arrive. Phase A comes first.

**A1 — Narrow the spam pause.** Today one "Message blocked" anywhere stops *all* cold mail for
48 h. All 11 blocks so far say "Message rejected … answer/69585": the *recipient company's*
server refusing, not Gmail flagging the account. Change: a single block holds only that
company/domain for 21 days (already built); a company inbox (careers@, jobs@, kind `inbox`)
never counts; the account-wide 48 h stop stays for **2+ blocks in 24 h** (the Sarvam pattern).
Files: `outreach/suppress.py`, `config.yaml` (`spam_pause_min_blocks: 2`), tests. Cost ₹0.
Check: a simulation on a copy of the DB with one block sends on; with two it pauses.

**A2 — Stop drafting what the review will reject.** Move three rules upstream, before a draft
is ever written: no pattern-guessed address (32 rejections), no company whose chosen role is
senior or asks 3+ years (87), and pick a different wording variant for each person at one company
before drafting rather than after (87 spam signals). Files: `focus.py` (`sendable`, `role_for`),
`outreach/drafter.py`. Cost ₹0 — saves model calls. Check: rejection rate on the next 50 drafts
< 25%.

**A3 — Verify every address before it is drafted.** The 4 "address not found" bounces hurt the
sender reputation more than anything else. D7 (no SMTP probing from the laptop — port 25 is
blocked at home) stays; instead a verification API checks each address once (cached forever):
`deliverable` → send, `catch-all` → send but count as risky (max 1 per company per day),
`undeliverable` → never draft. **This revises D6 (free tiers only)** — at ≈ 50 new addresses a
day the cost is ≈ ₹100–150/month, inside `constraint:student-budget`. Free path tried first:
GitHub-commit addresses (D5) are already verified and skip the check. Files:
`contacts/verify.py` (new), `focus.py`, `autoreview.py` (new rule: unverified → wait, not send),
`budgets` ledger row per call. Needs: your API key. Check: bounces < 2% over the next 100 sends.

**A4 — Spam-score every draft before approval.** SpamAssassin as a command-line check (no
daemon, fits the 8 GB rule) scores each draft the way a receiving server would; the automatic
review rejects anything ≥ 3.0 and logs the rule that fired, so the loop fixes the wording.
Files: `outreach/autoreview.py`, `outreach/spamcheck.py` (new). Needs: `brew install
spamassassin`. Cost ₹0. Check: every approved draft carries a score in its log line.

**A5 — Gentler pacing into one company.** 3 → 2 cold emails per company per day, at least 2 h
apart; the 10-company circle still fills 25 a day. Files: `config.yaml`
(`outreach.focus.per_company`), `sender.py` gap rule. Cost ₹0.

**A6 — A delivery report, every evening.** Sent / delivered / blocked / bounced per day and per
company, bounce rate over 7 days, and an alert (macOS + mail, like replies) when bounces pass 3%
or a block arrives. Files: `tracker.py`, `notify_bot.py`, dashboard Sends page. Cost ₹0.

## 5. Phase B — More capacity, without risking the personal Gmail (weeks 1–5)

The personal Gmail is the account interviews arrive at (`constraint:send-cap-25`), so it stays at
25 and is never pushed. Capacity comes from **new, separate inboxes**.

**B1 — A sending domain of our own.** A domain (e.g. `<name>.dev`, ≈ ₹1,000/year) on Google
Workspace with two inboxes (≈ ₹150–300 per inbox per month — check the current price), SPF + DKIM +
DMARC set, Google Postmaster Tools on the domain so spam rate and reputation are *measured*
instead of guessed from bounces. Needs: you buy the domain and Workspace and give the
inbox passwords (app passwords) to `secrets/`. Total ≈ ₹400–700/month, inside the student
budget. Check: mail-tester.com score ≥ 9/10 from each inbox.

**B2 — Many senders in `sender.py`.** A `sender_account` table: address, daily cap, warm-up
stage, its own spam pause, its own bounce brake. Each run picks the account with the most room;
a thread (follow-up, reply) always stays on the account that started it; one person is only ever
written to from one account; replies are polled from every account (tracker pass 1 and 2 run
per account). Files: `db.py`, `outreach/{sender,gmail,tracker}.py`, dashboard. Cost ₹0.
Check: a ten-day simulation on a DB copy with three accounts never exceeds any account's cap.

**B3 — Warm-up by ramp, not by a paid service.** Each new inbox: week 1 → 5/day, week 2 → 10,
week 3 → 20, week 4+ → 30, only while its 7-day bounce rate < 2% and no block; any block drops
it one step. Paid warm-up tools (Instantly, Smartlead, ≈ $30–40/month) exceed the budget and
replace our sender; our own ramp is the plan, a paid tool is the fallback only if Postmaster shows
the domain's reputation falling. Files: `outreach/sender.py`, `config.yaml`
(`outreach.accounts[].ramp`). Cost ₹0.

**B4 (option) — The second candidate's Gmail.** MOTIV is two of us; V3 step 9 gives the
teammate their own Gmail, resume and 25/day — honest capacity, free, and the cross-candidate
rule (never both of us to one person in a month) already exists as a constraint. Can run
alongside B1–B3.

Capacity by week: 25 → 35 → 45 → 65 → 85 a day (110 with B4).

## 6. Phase C — Supply that keeps up with capacity

At 85 a day the ≈ 250 sendable people last 3 days. Supply has to grow before Phase B finishes.

**C1 — The circle sizes itself from capacity.** `companies_per_day` = today's total cap ÷
`per_company`, so 25/day → 13 companies, 85/day → 43; the best-bets-first order (ranked shortlist,
then BLR/HYD, then targets) stays. Files: `focus.py`. Cost ₹0.

**C2 — People search sized to the plan.** The Apify people search runs only for circle companies
that are short of verified referrers, its budget raised from ≈ $0.30 to ≈ $1/day only once B2
is live (current credit: $8.46 to 21 Nov). City lock for India stays. Files: `contacts/apify.py`,
`config.yaml`. Cost ≈ ₹2,500/month at the top rate — needs your yes when it is reached.

**C3 — Free, verified sources first.** GitHub commit addresses (D5, verified, 0 bounces) and
team pages before any paid search, so the verified share rises and A3 costs less. Files:
`contacts/__init__.py` order. Cost ₹0.

**C4 — Fresher-role supply.** Rescore the 9,207 unscored jobs in batches on the cheap alias with
the fit gate first, so more companies have a known fresher role before the circle reaches them.
Cost ≈ ₹200 one-off (V3 step 2 budget).

## 7. Phase D — A "yes" is never lost (MOTIV step 11)

**D1 — Reply → calendar.** The schedule extractor (V3 step 10) turns a positive reply into
`{intent, proposed_times, link, deadline}`, writes an `event`, syncs to Google Calendar, and
alerts with what to do next. Two events on one slot → a reschedule draft. Needs: Calendar scope
on the OAuth client. Cost ₹0.

**D2 — A reply draft waiting with every reply.** The alert carries a suggested answer (thanks /
slots / resume) for you to send by hand — replies are never sent automatically.

**D3 — Follow-ups inside the cap.** One follow-up per thread after 5 silent days (already
guaranteed), approved by the same rules, but limited to a **reserved share of the day** (e.g. 5
of the cap) so they never crowd out first asks. Decision for you: the share.

## 8. Phase E — Learn which emails work (MOTIV step 12)

**E1 — An outcome row per email.** delivered · bounced · blocked · replied · positive ·
interview, with company tier, region, role class, wording variant, account and send hour. No
tracking pixels or tracked links — they are spam signals and are never added.

**E2 — A weekly report.** Reply rate by wording variant, role class (engineer / EM / founder),
company tier and send hour; a variant with 0 replies after 40 delivered is retired and its slot
goes to a new wording.

**E3 — The autoreview loop, measured.** Rejections per reason per week in the same report; a
reason that does not shrink after its fix is reopened.

## 9. What we will not do

- No bulk-mail services (Mailchimp, SendGrid, Brevo) — their terms forbid cold mail and their
  shared IPs carry bad reputations.
- No blasting from many throwaway domains — the classic spam pattern, and it gets the domain
  blacklisted.
- No more than ≈ 30/day from any one inbox, ever; the personal Gmail stays at 25.
- No tracking pixels, link shorteners or link tracking.
- No SMTP probing from the laptop (D7 stands).
- No logged-in LinkedIn / X / GitHub automation (D12, `constraint:no-linkedin-automation`).
- Never cut: the send cap per account, the confidence labels, the review (now run by rules,
  `jobhunter/outreach/autoreview.py`, every verdict in `logs/autoreview.log`).

## 10. Decisions this plan changes

| Decision | Change | Why |
|---|---|---|
| D6 — free tiers only | Allow one paid address-verification API, ≈ ₹150/month, ledgered | 10% bounces are costing more than the API |
| `constraint:send-cap-25` | 25 per **account**; the personal Gmail stays 25 | Capacity without risking the interview inbox |
| `guarantee:human-gate` | Already changed 2026-10-26: the review is run by rules, every verdict logged | Tejmul's instruction |
| Spam pause | Account-wide only on 2+ blocks in 24 h | One recipient's policy is not our reputation |

## 11. Build order — for your approval, by number

| # | Step | Phase | Cost | Needs from you |
|---|---|---|---|---|
| **1** | A1 Narrow the spam pause | A | ₹0 | yes |
| **2** | A2 Stop drafting what review rejects | A | ₹0 | yes |
| **3** | A5 2 per company per day, 2 h apart | A | ₹0 | yes |
| **4** | A6 Evening delivery report + alerts | A | ₹0 | yes |
| **5** | A4 SpamAssassin score in the review | A | ₹0 | yes |
| **6** | A3 Address verification before drafting | A | ≈ ₹150/mo | API key, D6 revised |
| **7** | B1 Own domain + 2 Workspace inboxes + DNS + Postmaster | B | ≈ ₹400–700/mo | purchase, app passwords |
| **8** | B2 Many senders in the sender and tracker | B | ₹0 | step 7 |
| **9** | B3 Warm-up ramp per inbox | B | ₹0 | step 8 |
| **10** | C1 Circle sized from capacity | C | ₹0 | step 8 |
| **11** | C3 + C2 Free sources first, then Apify sized to the plan | C | up to ≈ ₹2,500/mo | yes at each budget step |
| **12** | C4 Rescore the unscored jobs | C | ≈ ₹200 once | yes |
| **13** | D1 + D2 Reply → calendar, reply drafts | D | ₹0 | Calendar scope |
| **14** | D3 Follow-ups inside a reserved share | D | ₹0 | the share |
| **15** | E1–E3 Outcomes and the weekly report | E | ₹0 | yes |
| opt | B4 Second candidate's Gmail (V3 step 9) | B | ₹0 | teammate's resume + Gmail |

Steps 1–5 are a day or two of work and fix delivery with no spend. Step 7 is the only one that
waits on a purchase, and the warm-up clock only starts once it is done — **so buying the domain
early is the single thing that brings 85/day closest.**
