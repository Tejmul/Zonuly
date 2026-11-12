# Launch TODO — 12 Nov 2026

ZoNuLy is live at zonuly.tejmul.in behind sign-in. Tejmul's list after launch, with what was found
for each and what fixes it. Status is updated as each one lands.

| # | Problem (his words) | What is actually wrong | Fix | Status |
|---|---|---|---|---|
| 1 | "in the upskill it is showing that it is nothing there" | `/api/upskill` returns **500** on the live site. For a role with no stored brief it builds one from `profile/profile.json` (the resume), which is never shipped to the public server. | Without the resume, use only the briefs already stored (340 roles): 65 skills across 250 companies. | done |
| 2 | "less companies showing like it is being filled … show total companies" | The Companies page opens on *Complete* (1,114) with *Being filled* (5,090) as a second tab, so a visitor sees ~1k of 7,078. The Graph header reads the same split. | Companies opens on **All** (every active company, complete ones first), with the total in the header; Graph shows the total first. | done |
| 3 | "now we launched it remove the cold dm automations shows tejmul name" | Queue, Outreach, Email proposal, Replies, Calendar, Tracker and Settings are his own cold-email pipeline and setup (drafts signed with his name, his send counts, his Gmail). They are served to every signed-in user. | Operator-only on the public site: hidden from the nav (and the dashboard's outbox card), the pages redirect to the dashboard, and the API refuses `/api/outreach`, `/api/emails`, `/api/campaigns`, `/api/calendar`. Locally nothing changes. The Leads page drops the "CSV batch · mailed" badge publicly, and the API no longer sends that flag to visitors. | done |
| 4 | "in the atlas the You section does not show any animation" | The *You* column is built from the resume (`/api/profile`, refused publicly), so it is empty and no wire is drawn from it. | Without a resume, *You* is built from what the visitor picks on the page (field, where, stage, must-haves), so the wires and pulses run. | done |
| 5 | "in the queue it is showing my name" | Same as 3. | Covered by 3. | done |
| 6 | "the docs are not yet fully and properly built" | No page on signing in / accounts; Deployment still describes an open read-only copy; the Dashboard tour lists the personal pages as if every user had them. | New *Sign in & accounts* page; Deployment and Dashboard tour brought up to the launched product; every page that still said guessed addresses are used, or that a person approves each draft, now says what is true (proved addresses only; review by rule, reasons in `logs/autoreview.log`). | done |
| 7 | "in the kg also it is showing less companies" | Same split as 2 on `/graph` (6,204 tracked, shown as 1,114 complete + 5,090 filling). | Covered by 2. | done |
| 8 | "from docs to overview there is no proper back button" | The docs header's only way back is "Open the app ↗", hidden below 640px and styled like an outbound link. | A plain **← Back to the app** at the left of the docs header, at every width, and **← Docs overview** above every doc page. | done |

Counts are the live site's snapshot of 12 Nov 2026: 7,078 companies (6,476 active, 329
quarantined junk, 151 merged duplicates, 122 below the pay bar).
