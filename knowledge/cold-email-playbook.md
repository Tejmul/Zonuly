# Cold referral email: the playbook

*Researched 2026-10-14. This is how ZoNuLy writes a cold referral ask, and where each rule comes from. The code that applies it is `jobhunter/outreach/compose.py`, `gates.py`, `drafter.py` and `jobhunter/proposals.py`.*

**Source tags:**
- **[V]** vendor data from a company that sells email tools. These are mostly B2B sales sequences, the data is observational, and the vendors often contradict each other.
- **[A]** anecdote or self-reported.
- **[I]** independent, academic or official.

**Main caveat.** No rigorous dataset exists on students asking strangers for referrals. Every number below is a direction, not a forecast.

---

## 0. Realistic reply rates (read this first)

| What | Reply rate | Source |
|---|---|---|
| Cold B2B average | 3.4% (top 10%: 10.7%+) | Instantly 2026 [V] https://instantly.ai/cold-email-benchmark-report-2026 |
| Cold B2B, per sequence / single message | 4.5% / 2.9%; HR / headhunting 7.5% | Hunter, 31M emails [V] https://hunter.io/the-state-of-cold-email |
| Engineering / product recipients | ~5.2% | Lavender [V] https://lavender.ai/blog/the-cold-email-benchmark-report |
| Outreach study (2019) | 8.5% | Backlinko / Pitchbox, 12M emails [V] https://backlinko.com/email-outreach-study |
| Students cold-emailing for internships / jobs | ~10–12% (150 sent → 17 positive; 100 → 10%) | collected by FirstSales, unlinked [A] https://firstsales.io/blog/cold-emails-for-internships/ |
| "Expect 5–10 replies per 100" | 5–10% | Glassdoor engineer [A] |
| "~30 companies to get one reply" | ~3% | @benln, 622K views [A] https://x.com/benln/status/2018035287851704556 |
| Executives vs non-executives | −30% | Gong [V] |
| Free-mail sender vs custom domain | 2.5% vs 5.2% | Hunter [V]; partly confounded |

**Planning number:** expect **5–12% replies** (with one follow-up) for a well-targeted, hand-reviewed ask from a personal Gmail to engineers and founders at small startups. Fewer than half of those replies will be a referral.
- 90–100% is not a realistic target for cold email.
- The goal is to maximise every factor we control, and to measure (§8).

---

## 1. Subject line

- **Length: 3–6 words, under ~50 characters.**
  - Boomerang: 3–4 words was best (40M emails, not cold) [V].
  - Hunter: 5–6 words best for replies [V].
  - Belkins: 2–4 words, 46% opens [V].
  - Backlinko: 36–50 characters [V].
  - The gate enforces 2–8 words and ≤60 characters.
- **Personal tokens lift replies.**
  - Personalised subject line: +30.5% (Backlinko); 7% vs 3% (Belkins) [V].
  - Two personalised attributes: 40.2% vs 35.4% opens (Hunter) [V].
- **Credential + role + company** is the job-hunting pattern that works.
  - "Former Google Intern from UVA Interested in Airbnb" (Nick Singh) [A] https://www.nicksingh.com/posts/cold-email-tips-to-land-your-dream-job-with-examples
  - "Fellow MIT Alum interested in Data Science at Airbnb" (Kristina Lu) [A] https://kristina.substack.com/p/effective-cold-emails
- **Questions** have the top open rate (Belkins) [V]. Reply rate is not measured.
- **Avoid "Quick question".** Hunter: 2.5% vs 2.9% replies [V]. It now reads as a template.
- **No fake "Re:" or "Fwd:", no "!", no shouting, no spam words** (free, urgent, guarantee).
- **Casing:** lowercase beat title case by +14%, but that is one vendor's own campaigns [V, weak]. Treated as untested.

**Our patterns** (`compose.SUBJECT_PATTERNS`, rotated per recipient so each one collects data):
- `referral_q`: "Referral for {role} at {company}?"
- `role_q`: "{role} at {company}?"
- `bond_role`: "CS+AI '27 grad, {role} at {company}"
- `name_role`: "{first}, {role} at {company}?"
- `proof_take`: "{proof topic}: your take?" (proof-of-work structure only)

## 2. Opening line

- **Them first.** Open with one specific thing of theirs, and say how you found it.
  - "Found your email on Hacker News" (Nick Singh) [A].
  - Name the person's papers (Noam Brown, research internships) [A] https://x.com/polynoamial/status/1316022618953150467
- **Evidence that personalisation pays** [V]:
  - Personalised body copy: +32.7% replies (Backlinko).
  - Two custom attributes: 3.6% → 5.6% (Hunter).
  - 67% of decision makers are more likely to reply to personalisation built from public information; 48% name generic messages as a reason they don't reply (Hunter survey).
- **Seconds to decide:** executives decide in under 3 seconds and read for under 9 (Gong) [V]. The first 140 characters show in the preview.
- **Never:**
  - "I hope this finds you well" or "My name is…"
  - Flattery without specifics.
  - Fake personalisation. @benln calls lying the fastest way to lose a reply.

**Hooks, most specific first** (`compose.hooks`, each stored with its source):

| Hook | Example sentence | Specificity |
|---|---|---|
| their post that the company is hiring | "I saw your X post that Acme is hiring." | 1.0 |
| shared ground | "I saw you're also from Newton School of Technology." | 0.95 |
| their commits to the company's repo | "I came across your commits to acme/voice-sdk on GitHub." | 0.9 |
| their own repo (≥3★, not practice or coursework) | "I came across your fastsse repo on GitHub (…)." | 0.8 |
| their bio | "I saw you contribute to React Native." | 0.7 |
| company launch (dated, with URL) | "I saw Acme's recent news, "…"." | 0.7 |
| company round (only when a real source states it) | "Congrats on Acme's $170M Series B." | 0.6 |
| company's own description | "I read how Acme describes its work: "…"." | 0.45 |

With no hook at all, **no email is written**.

## 3. Body length and readability

- **Boomerang** [V, 2016]:
  - 50–125 words had a response rate above 50%; 75–100 peaked at about 51%.
  - A 3rd-grade reading level beat college level by 36%.
  - 1–3 questions beat none by 50%.
- **Others** [V]:
  - Instantly: best emails are under 80 words.
  - Gong: 50–100 best for executives.
  - Hunter: length is a weak lever; 20–39 words did best at 4.5%.
- **Our band: 60–120 words** for the message itself (`outreach.gate.min_words / max_words`). The forwardable blurb (≤55 words) and the opt-out line are counted apart.
- Short sentences. The gate flags any sentence over 30 words and an average over 22.

## 4. The ask: one, low-friction

- **Interest CTAs win on a cold first touch.**
  - Gong Labs, 304K emails: "the highest-performing CTA by far".
  - A specific time slot books 15% when cold vs 37% later [V].
- **Ramit Sethi** [A] https://80000hours.org/articles/email-scripts/
  - Make it answerable with one word.
  - Ask for "anyone I should chat with", not for a job.
  - He claims a >90% response rate on personalised favour asks.
- **One ask, plus an easy out:** "or pointing me to whoever is hiring". The opt-out line is the exit clause, as in Kristina Lu's guide.
- **Asking for advice** makes the asker look *more* competent, when the person asked is an expert in the topic.
  - Brooks, Gino & Schweitzer, *Management Science* 2015 [I]: https://ideas.repec.org/r/inm/ormnsc/v61y2015i6p1421-1435.html

## 5. Credibility and making yes easy

- **One quantified proof per ask.** Nick Singh used "RapStock.io, 2,000 users" [A].
- **Skip ROI language and buzzwords.** Gong: −15% [V].
- **A two-line blurb to forward, plus a resume link.**
  - This is consistent advice: give the referrer the role, 2–3 lines of fit and the resume. https://engineerturnedrecruiter.beehiiv.com/p/how-to-get-referrals-for-tech-jobs
  - It is unmeasured. Referrers want something they can vouch with: "a weak referral reflects poorly" [A].
- **Format:**
  - Plain text. HTML cold mail bounced 650%+ more (Hunter) [V, confounded].
  - **No tracking pixel.** Replies were 7.4% without one vs 4.4% with (Hunter); inbox placement is 8–12% lower with a pixel (Mailtrap) [V].
  - 1–2 links, no shorteners, no attachment on the first touch. Only recruiters and managers want a CV up front (MIT survey, N=24) [A] http://mitcommlab.mit.edu/aeroastro/2025/07/23/cold-contacting/
  - A LinkedIn link in the signature: +11.5% (Backlinko) [V].
- **Sign-off:** "Thanks" (63%) is near "Thanks in advance" (65.7%) in Boomerang's help-request data [V]. We pair "Thanks" with an exit clause.

## 6. Timing and follow-ups

- **Day and hour:**
  - Tue–Thu mid-morning in the recipient's time zone (Backlinko, Instantly, Nick Singh).
  - Hunter finds day of week has **no** meaningful effect [V].
  - Weekdays beat weekends by 23% (Backlinko) [V].
  - Our sends: Mon–Fri, 10:00–19:00 IST, jittered. `send_hour` is stored on each email so §8 can test this on our own data.
- **Follow-ups lift replies a lot:**
  - One follow-up: +65.8% (Backlinko).
  - 9% → 13% (Woodpecker).
  - 3.3% → 6.8% at three messages (Hunter).
  - 42% of replies come from follow-ups (Instantly) [V].
- **Each follow-up should add something new, not "just bumping this".**
  - A conversational step 2 beats a formal one by about 30% (Instantly).
  - Nick Singh's third email cited new onsites [A].
- **Ours:**
  - One follow-up after 5 silent days, as the `guarantee:one-followup` and `max_followups: 1` settings require.
  - The proposal shows it carrying a **second, different proof**.
  - Vendors suggest 2–3 follow-ups for sales. For a student's favour ask, MIT's respondents say one or two at most, and 4+ messages triple spam complaints (Belkins) [V].

## 7. Deliverability from a personal Gmail

- **Google's rules** [I] https://support.google.com/a/answer/14229414
  - The bulk-sender rules start at 5,000+/day.
  - Keep the user-reported spam rate under 0.3%; above 0.1% already hurts inboxing.
  - We are far below the bulk threshold. What matters is not being reported.
- **Volume:**
  - Hunter: 20–49 a day per account replied best at 5.7% [V].
  - Warm-up for a new sender: 5–10 a day, rising [V].
  - Ours: a 5 → 25 ramp, never above 25 a day, guessed addresses capped at 5 a day, a bounce brake. All unchanged.
- **Content:** no pixel, no shortener, plain text, 2 links (resume in the blurb, LinkedIn in the signature), verified addresses first.

## 8. What we measure (`learn.variants`)

- **Stored on every email:**
  - `structure`, `subject_pattern`, `hook_type`
  - `words`, `quality`, `writer` (model or template)
  - `send_hour` and `send_weekday`
- **Reply and positive rates per variant** appear on the Tracker page.
  - Below 20 sends a variant shows "not enough data yet" (`outreach.compose.learn_min_sent`).
  - A 1-in-3 is not a finding.

---

## The structures (`compose.choose_structure`)

| Structure | For | Shape | Ask |
|---|---|---|---|
| **proof_opinion** | engineers and founders at small startups (≤60 people, or seed / Series A) | hook → "Some things I've built:" 2–3 quantified bullets → what the posting asks for → **"What do you think: is this the kind of work your team needs in that role?"** → the note to forward, "if you think it's a fit" | their opinion |
| **referral_engineer** | engineers at larger companies | hook → who I am, the role → what it asks for + one proof → "Would you be open to referring me for it, or pointing me to whoever is hiring?" | referral or pointer |
| **founder_direct** | founders at larger startups | hook → "I'd like to be considered for the {role} role" + one proof → "…considering me for it, or pointing me to whoever is hiring?" | consideration |
| **recruiter_direct** | recruiters | hook → graduating 2027, the role + one proof → "Could you put me forward for it, or tell me if it's open to 2027 graduates?" | put forward |

### Proof of work, then their opinion (the user's pattern, made first-class)

- **The shape:**
  - Why them, in one line.
  - "I've done this, this and this": shipped things with numbers.
  - "What do you think?" instead of "please refer me".
- **Why it works:**
  - It is a low-pressure, interest-style ask (Gong).
  - Asking an expert for advice signals competence (Brooks et al.) [I].
  - "Ask for advice, get a lead" is a long-standing career-services saying [A].
  - It hands the referrer evidence to vouch with.
- **Variants seen:**
  - "built X for your product, would love your take"
  - "a 2-minute teardown of your onboarding"
  - "reproduced and fixed issue #123, PR here, does this approach make sense?"
  - Sources: Beearly, Proof of Work [A] https://beearly.beehiiv.com/p/proof-of-work ; Cold Email Leverage [V] https://coldemail.substack.com/p/cold-email-leverage-issue-31 ; Nina Mufleh's nina4airbnb, which got a reply from the CEO [A] https://aicourses.fortune.com/2015/04/23/airbnb-resume/ ; Peter Yang, "give first, then ask" [A] https://x.com/petergyang/status/1699451169285026262
- **When it backfires:**
  - Unsolicited spec work: one designer reported 0% from unsolicited redesigns [A] https://uxplanet.org/i-tried-this-viral-strategy-to-find-design-clients-part-01-ed810699c5eb
  - Link dumps.
  - Asking an opinion outside their expertise.
  - An opinion question with no route to a referral. So the proof-of-work structure still ends with "if you think it's a fit, here's a short note you could forward".
- **When it fits:** seed to Series B, where one person owns the product (Beearly) [A].
- **Proof slots** are the resume's quantified bullets (`outreach.compose.proofs` in config). A proof is used **only** where the needs brief says it covers what the role asks for.
  - MVRX Labs: the Temporal migration (−95% infra cost); GTM OS for 10+ YC clients; ChokePoints over 27,000+ companies.
  - eWealth Tech: the Mistral-7B fine-tune (20% → 92%); −88% latency; a serverless move (−95% cost).
  - Maverick Secure: YOLOv11 detection (20% → 88%).
  - Zonuly: 3,528 companies and 19,159 referrers.

### Five real emails, line by line

1. **Niraj Pant → Evan Spiegel, Snapchat CEO; got a reply** [A] https://nextplayso.substack.com/p/the-guide-to-getting-a-job-with-cold
   - The intent goes in the subject: "Want to intern for Snapchat this summer as a high school junior. How?"
   - A one-line identity.
   - Three credential bullets.
   - A one-word ask, "How?"
   - *Our take:* bullets of proof and an ask that costs one word to answer.
2. **Nick Singh → Periscope Data / Reddit; got interviews** [A]
   - A credential subject.
   - Where he found them ("your post on Hacker News").
   - Why this company.
   - One proof (2,000 users).
   - A direct next step.
   - Follow-ups at 3–4 days, the last one adding new information (other onsites).
   - *Our take:* the referral_engineer shape, plus a follow-up that carries new value.
3. **Ramit Sethi's recommendations ask** [A]
   - Shared bond.
   - The specific role and why it fits.
   - "Do you know of anyone there I should chat with?"
   - *Our take:* the "or pointing me to whoever is hiring" exit in every ask.
4. **@benln's "Kevin" pattern** [A]
   - A trigger (a friend's mention or a recent seed round).
   - Two quantified bullets.
   - "Worth a chat?"
   - Under 200 words, at most 1–2 follow-ups.
   - *Our take:* the company-round hook, plus proof bullets.
5. **Kristina Lu's template** [A]
   - Subject: shared bond + role + company.
   - Who I am and how I found you.
   - One piece of their work.
   - One small ask.
   - An exit clause.
   - Under 150 words; Tue–Thu.
   - *Our take:* the `bond_role` subject, the opt-out line as the exit clause.

---

## Do / don't

| Do | Don't |
|---|---|
| Open with one specific, sourced thing of theirs | "I hope this finds you well", "My name is", "Dear Sir/Madam" |
| One quantified proof that answers the posting | Claim a skill the needs brief marks uncovered (the gate blocks it) |
| One ask, plus an easy out | Two asks, a call request on the first touch |
| A forwardable 2-line note + resume link | Attachments, link dumps, shorteners, tracking pixels |
| 60–120 words, short sentences | Walls of text, buzzwords, ROI claims, flattery |
| A real first name | A handle ("kbysani"), "Hi there", a team inbox |
| One follow-up with new value | "Just bumping this" ×4 |
| Refuse to write when evidence is missing | Fill gaps with generic lines |

---

## How each pattern maps to our data

| Slot | Comes from | Missing → |
|---|---|---|
| first name | `contact.name` → `compose.first_name` (real name, not a handle) | no email ("missing first name") |
| their role | `contact.role` (headline / bio) or `contact.role_class` | no email |
| company | `company.name` | no email |
| the role asked about | `drafter.pick_job`: an engineering title, fresher first, remote-anywhere, selection score | target skipped ("no engineering role on file") |
| hook + source | `contact.affiliation_evidence` (commits), `contact.research_notes.repos`, `contact.role`, `company.hiring_claim_*`, the needs brief's `building` launches, `company.funding_*` (amount only as the evidence states it), `company.description` | no email ("missing hook") |
| need → proof | `job.needs_brief`: covered need + `evidence` line → `outreach.compose.proofs` phrasing | no email ("missing a covered need") |
| what is not claimed | the needs brief's uncovered needs and `forbidden` skills | shown in the proposal and review |
| blurb + resume | profile (name, school, year) + `outreach.user_links` "Resume:" | no email |
| opt-out line | `outreach.opt_out_line` (added again at approval) | — |
| variant record | `email.structure / subject_pattern / hook_type / words / quality / writer / send_hour` | — |

**The model's role** (free OpenRouter `writer` chain):
- It only *polishes* the deterministic fill.
- Its version is used only if it keeps every contract slot (name, role, hook anchor, proof numbers, the ask) and adds no gate flag.
- At the daily ceiling, the deterministic fill is the email, and the draft is marked `polish_pending` (`outreach polish` later).

---

## Design only, not built: an evidence-based auto-approve

The review gate (`guarantee:human-gate`) stays exactly as it is. If the user later decides drafts are good enough, an earned auto-approve could look like the config below. Nothing reads these keys today.

```yaml
outreach:
  auto_approve:            # NOT BUILT — design note only
    enabled: false
    min_quality: 90        # only drafts scoring at least this
    hook_types: [person_post, shared_ground, person_commit, person_repo]   # personal hooks only
    address: verified      # never a guessed address
    no_flags: [faithfulness, uncovered_claim, ai_tell, affiliation, guessed, subject]
    earned_after: 30       # the user approved this many in a row with no edit…
    max_edit_ratio: 0.05   # …or with edits under 5% of characters
    structures: [referral_engineer, proof_opinion]   # earned per structure, not globally
    daily_share: 0.5       # at most half of the day's quota; the rest still waits for the user
    revoke_on: [negative_reply_rate_above: 0.2, bounce, opt_out]   # any of these switches it off
```

The send cap, the guessed-address cap, the warm-up ramp, opt-out and suppression apply either way.
