# ZoNuLy — what the knowledge graph is for

> 2026-10-16. Tejmul: *"I don't really see the essence of the knowledge graph here. Research how
> it should be used at 100% potential in this app, and how we are using it. We have to improve that."*
> Code: `jobhunter/kg/warm.py`, `jobhunter/kg/routes_warm.py`, `dashboard/components/kg-warm.tsx`.
> Tests: `tests/test_kg_warm.py`. Graph nodes: `feature:kg-warm-paths`, `feature:kg-similar-companies`,
> `feature:kg-referrer-signal`, `feature:kg-temporal-validity` (gap), `feature:kg-reply-learning` (gap).

## 1. What we did with it until today

| Use | Where | Who reads it |
|---|---|---|
| Session memory: decisions, gaps, notes, BRIEF.md | `kg/context.py`, `kg/brief.py` | the next session (human or model) |
| Mirror of every table (company, job, contact, investor, email, reply) | `kg/sync.py`, hourly | search, `kg show`, the Graph page |
| `why <company>` verdict + skills I have that they ask for | `kg/hunt.py` → company page "Before we write" box | Tejmul, a little |
| Shared-investor expansion, shortlist, guardrail traversal, audit | `kg/hunt.py` | the CLI only |
| The Graph page (atlas / pipeline / project) | `graphmap.py` | Tejmul — but it shows *pipeline state*, not relationships |

**The gap.** In the data layer the graph was a second copy of the database. It had only the edges
the tables already had: company→investor, job→skill, contact→company. Nothing tied a person to
*Tejmul*. Outreach (drafter, researcher, compose) never read it. The questions a referral ask
depends on are *why would this person answer me?* and *where should I look next?* Those are
two-hop traversals through a shared entity, and the graph could not answer either.

## 2. What the research says (sources at the end)

1. **Recruiting.** Referrals measurably work. Referred applicants are hired more often, accept
   more often and stay longer (Burks et al., QJE 2015). The effect is strongest at lower experience
   levels (Brown/Setren/Topa, JOLE 2016), and a fresher is at the bottom of that range. On LinkedIn,
   *moderately weak* ties produce the most job mobility (Rajkumar et al., *Science* 2022), so the
   target is not close friends. It is people who share **one concrete thing** with you. LinkedIn's
   alumni tool and "how you're connected" are the same query: school/employer node → person →
   current company.
2. **Warm intros in sales and VC** (Affinity, Village, Clay, NFX Signal). These tools score
   connection strength and rank paths by it. They work from mailbox history, which we do not have.
   Without it, strength must come from **shared attributes**: same school, same past employer, the
   same open-source work, how rare the shared thing is, and the person's own statement of it.
   Vendors report reply rates for warm intros of about 27–40%, against 1–5% cold. That direction is
   reliable; the exact numbers are not.
3. **Entity resolution.** Use deterministic keys first: the normalised LinkedIn `/in/` slug, the
   lower-cased GitHub login, the email. Keep the evidence on a `same_as` edge rather than merging
   destructively. Probabilistic matching (Splink, Fellegi–Sunter) only pays when there are many
   ambiguous pairs, and at 28k contacts there are not.
4. **Similar companies without ML.** Project the company–investor graph onto companies and score
   with **Adamic–Adar** (1/log degree). A small seed fund in common then says more than a mega fund
   does, which is the "underrated" bias we want. Add a second signal, skill overlap. Personalised
   neighbourhood aggregation from the companies you already like is a cheap version of personalised
   PageRank. node2vec adds little at this size.
5. **The graph as agent memory** (Graphiti/Zep, Mem0, GraphRAG). The relevant pattern for a drafter
   that must not fabricate is a small per-draft **fact sheet**: only this person's and this
   company's edges, each with the quote it was read from. Graphiti's bi-temporal edges
   (valid/invalid instead of delete) are the next refinement.

## 3. Uses for this app, ranked (value × cheapness)

| # | Use | Value | Effort | Status |
|---|---|---|---|---|
| 1 | **Warm paths per person**: their own text names his school or a past employer, the same person is on file at a company he is engaged with, or they work with a specific skill on his resume | very high: the opening line, and which 25 people get the day's sends | low | **built** |
| 2 | **Warm paths per company**: alumni of the company now elsewhere, an investor shared with a company he already has a draft or ready referrer at, same city | high | low | **built** |
| 3 | **Fact sheet into the research brief** (`warm_paths` section, evidence-carrying) | high: grounded hooks, guarantee:no-fabrication | low | **built** (brief only; see §6) |
| 4 | **Similar companies / next companies** (Adamic–Adar + idf skill overlap) | high: grows the list with underrated companies by construction | low | **built** |
| 5 | **Graph referrer signal** (0–100: confirmed-current, independent evidence edges, warm weight) | medium: a second opinion beside referral_rank | low | **built** |
| 6 | **Entity resolution edge** `SAME_PERSON` (LinkedIn slug / GitHub login at 2–3 companies; bots and drive-by committers excluded) | medium: dedup, and one more warm path | low | **built** |
| 7 | Learn which path types get replies (hook_type × path type → reply rate, then reweight) | high once sends exist | medium | gap: zero sends so far |
| 8 | Temporal edges (valid_from / invalid_at), so "used to work at" is history, not a deletion | medium | medium | gap |
| 9 | One-of-us-per-contact and cooldown as traversals | exists in `hunt.guardrails` | done | already |
| 10 | Investor portfolio expansion into discovery (`hunt.expand` → admission) | medium | medium | CLI only today |

## 4. What was built

**Sync** (`kg/warm.py::sync_warm`, called from `sync.sync_all` after contacts). It adds four edge
kinds, derived from rows that sync has already read:

```
profile:me -AFFILIATED_WITH-> org:newton-school-of-technology <-AFFILIATED_WITH- contact   (quote on the edge)
contact -PREVIOUSLY_AT-> company        "ex-X / previously @X / prev: X" in their own headline, X in the graph
contact -SAME_PERSON-> contact          one LinkedIn slug / GitHub login at 2–3 companies, bots excluded
company -IN_CITY-> city:hyderabad
```

`replace_rel()` writes only the edges that changed and deletes the ones whose evidence went away.
A no-op sync therefore writes nothing. On the live data that is 13 AFFILIATED_WITH, 70
PREVIOUSLY_AT, 904 SAME_PERSON and 1,344 IN_CITY edges.

**Measured** on a snapshot of the live DB (40,975 nodes): `sync_warm` alone takes **0.38–0.45 s**.
A full `sync_all`, alternating runs without and with it, measured 7.3 / 5.9 / 6.0 s without and
6.4 / 7.9 / 6.0 s with (median 6.0 → 6.4 s, inside run-to-run noise). The 6-second sync stays
6 seconds.

**Read side** (each call under 0.1 s, with caches of 5 minutes):

- `warm_paths(company_id)` returns the people with a path, ranked by path weight and then graph
  signal, plus company-level paths.
- `contact_paths(contact_id)` returns one person's paths and signal.
- `similar_companies(company_id)` and `next_companies()` handle recommendations.
- `referrer_signal()` is exposed as the field `graph_signal`.
- `brief_section()` builds the researcher's `warm_paths` block.

**API.** `GET /api/kg/warm/company/{id}`, `/api/kg/warm/contact/{id}`, `/api/kg/similar/{id}` and
`/api/kg/next-companies`. They live in `kg/routes_warm.py`, and api.py has one include line. No email
addresses appear in these payloads, only `has_email`.

**Where Tejmul sees it.**

- The company page has two new sections after the leads. **Warm paths** shows each person, a
  three-tick strength, the fact, the quote it came from, and an optional opening line. **Similar
  companies** shows each neighbour with its reasons.
- On the Graph page, clicking a company opens a side panel with the top 3 warm paths and "If you
  like this one" (4 neighbours). Clicking a neighbour moves the selection there, or opens its page
  if it is not on the map.

**Never invented.** A path exists only if the person's own text says it. It is phrased as "their
profile mentions Newton School", never as "they're an alumnus". A headline reading "Ex-Founder's
Office @ Newton School" describes staff, not a classmate. Lookalike names don't match: "Drivewealth"
is not "eWealth". Generic skills (Python, Docker, React…) never count as shared ground.

## 5. A real example (live data, 2026-10-16)

```
Simplismart (Bengaluru, Series A, pay power 100) — /api/kg/warm/company/3030
  Shrayansh Agarwal · role class: founder · likely-current · graph signal 87
    [your school] Their role mentions Newton School of Technology — your school.
       "CTO's Office @ Simplismart | Ex.Founder's Office @ Newton School | HSBC | BITS Pilani" — role
       opening line: "I noticed Newton School of Technology on your profile — I'm a student there."
    (no address yet: the next thing to find for this person)
  Shripati Pandey · graph signal 34
    [known elsewhere] The same linkedin login (shripati-pandey) is also on file at GeneralMind — a ready referrer.
Similar to Simplismart: SciSpace, TAAS partners, Cyberhaven, SigNoz, Talview, TrueFoundry
  (hiring for the same skills: GenAI, Distributed Systems, LLM… · same city: Bengaluru · same stage: Series A)
```

## 6. Constraints honoured, and the one exception

- `outreach/researcher.py` received **one additive, delimited block**. After the model's brief, it
  adds `brief["warm_paths"]` when any path exists, and it is wrapped in try/except so research
  never fails because of the graph. It is a **lazy import from outreach up into kg**. That is the
  one deliberate exception to the layering rule. The alternative was to duplicate the graph read
  inside outreach.
- The model prompt was not changed. The drafter (`drafter.gather` / `compose.hooks`) does not yet
  read `warm_paths`. Its own `shared_places` check already covers school and employer for the
  person being drafted. The next step is to let `compose.hooks` consume the brief's `warm_paths`
  (it gains same-person-elsewhere and shared-stack), which needs an approval to touch outreach.
- No pipeline table was changed. `org` and `city` nodes and four edge kinds are the only additions.

## 7. Next

1. When sends start: record the top path type on each email and learn reply rate per type
   (`feature:kg-reply-learning`), then replace the `WEIGHT` prior with data.
2. Let `compose.hooks` read `warm_paths` (needs approval for outreach/).
3. Feed `next_companies()` into the fill queue's ordering, so the graph decides which company is
   filled next.
4. Validity windows on edges (`feature:kg-temporal-validity`).
5. Find addresses first for people with a strong warm path. Example: Shrayansh has the strongest
   path at Simplismart and no address.

## Sources

- Rajkumar et al., "A causal test of the strength of weak ties", *Science* 377 (2022): https://ide.mit.edu/insights/new-publication-mit-harvard-and-stanford-scientists-show-weaker-ties-are-more-beneficial-for-job-seekers-on-linkedin/
- Burks, Cowgill, Hoffman, Housman, "The Value of Hiring through Employee Referrals", QJE 2015: https://experts.umn.edu/en/publications/the-value-of-hiring-through-employee-referrals/
- Brown, Setren, Topa, NY Fed SR568: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr568.html
- Ashby referral benchmarks: https://www.ashbyhq.com/talent-trends-report/reports/referrals
- Affinity relationship intelligence: https://support.affinity.co/s/article/Relationship-intelligence
- Village connection scoring: https://docs.village.ai/help-center/building-your-network/how-we-score-connections
- Splink entity resolution: https://realworlddatascience.net/applied-insights/case-studies/posts/2023/11/22/splink.html
- Zhou et al., VC co-investment link prediction, ICDE 2016: https://keg.cs.tsinghua.edu.cn/jietang/publications/ICDE16-Zhou-et-al-VC-Co-Investment.pdf
- Graphiti / Zep temporal KG memory, arXiv 2501.13956: https://arxiv.org/pdf/2501.13956.pdf
- Mem0, arXiv 2504.19413: https://arxiv.org/abs/2504.19413v1
- Warm vs cold reply rates (vendor figures, weak evidence): https://www.draftboard.com/blog/warm-intros-vs-cold-email-benchmarks-2026
