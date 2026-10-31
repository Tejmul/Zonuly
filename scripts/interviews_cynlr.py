"""CynLr's interview reports and prep (2026-10-31), for the company page.

Glassdoor and Reddit came through Apify (jobhunter/interviews.py); the Medium write-up, CynLr's
own LinkedIn post and the GitHub study guide were read by hand. The prep points below are drawn
only from those reports; each names the reports behind it (Glassdoor interview ids).
Skipped: myinternships.in and knok.work. Their pages are generic templates (URL shorteners,
STAR answers) that no candidate reported.

Usage: python scripts/interviews_cynlr.py [--raw <dir with apify_glassdoor.json / apify_reddit.json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobhunter import interviews  # noqa: E402
from jobhunter.db import init_db  # noqa: E402

CYNLR = 5637
GD = "https://www.glassdoor.com/Interview/CynLr-Interview-Questions-E3315687.htm"

REPORTS = [
    dict(source="medium", source_id="rahulreddy-cynlr", url="https://medium.com/@rahulreddy.4004/cynlr-a248d2aac196",
         role="Software Engineer — AI/ML", outcome="no offer",
         title="My Interview Experience at CynLr (Rahul Reddy)",
         body="Online Assessment (3 hours) → Group Discussion → Technical Round → AI/ML domain round → "
              "Founders (did not reach). OA sections: software/algorithmic, mathematics and physics (MCQ and "
              "descriptive, mostly physics), AI/ML paragraphs asking for your opinion, programming (memory size "
              "of a program from its data types, output of a program), system architecture (threads). The "
              "technical round picked one of his OA answers (equilibrium pressure in a water pipeline) and asked "
              "follow-ups, then computer architecture and compilers.",
         questions=["Equilibrium pressure in a water pipeline — solve your OA answer again and explain it",
                    "Types of computer architecture (Harvard vs von Neumann); which uses one memory for data and instructions",
                    "Difference between a program and a process",
                    "How does compilation work? Compiler vs interpreter",
                    "What is a stack and a queue? Implement a stack in your language",
                    "Types of supervised learning",
                    "Why is logistic regression called regression?",
                    "Activation functions — draw their graphs (in Paint)",
                    "Explain convolution with a 3×3 kernel on a 5×5 grid (on notepad); what is the output?",
                    "Are matrix multiplication and convolution the same? What else differs?"]),
    dict(source="github", source_id="adityanayak2000-study-guide",
         url="https://github.com/adityanayak2000/Interview-Prep-Study-Guide/blob/main/Study_Guide/companies/tier_G_services/Cynlr.md",
         role="Software Engineer", title="Study guide entry (compiled mostly from Glassdoor, medium confidence)",
         body="Intro/background call → coding/knowledge assessment → technical interview → founders' round → offer. "
              "Campus: MCQ shortlist → coding (~1.5 hr) → DSA technical, with or without a multi-day take-home. "
              "Feedback cycles often slow. Stronger signal: computer organisation, compilation, bits/bytes, "
              "first-principles reasoning over algorithm trivia. Expect multi-week timelines.",
         questions=["DSA: string occurrence, queue implementation"]),
]

OFFICIAL = dict(
    source="linkedin", source_id="7396790401858404352",
    url="https://www.linkedin.com/posts/cyn-lr_cynlr-hiring-outlookindia-activity-7396790401858404352-7Amc",
    title="CynLr on its own hiring process",
    body="From 10,954 applicants, only 0.2% made it through CynLr's 2025 placement season. … Instead of asking you "
         "to \"perform\" in an interview, we let you experience a glimpse of what problem-solving feels like inside "
         "CynLr. See if you can break down an unfamiliar problem with just raw, fundamental thinking.")

PREP = [
    ("1", "The process",
     "A 3-hour online assessment (subjective + MCQ; some answers written on paper and photographed) → on campus, a "
     "non-eliminatory group discussion → one to three technical interviews of 1.5–3 hours each → a founder round "
     "that can run 4–5 hours. Some roles add a take-home project (10 days, or two phases).",
     "Glassdoor 105657664, 105555975, 101600261, 105584563, 105571722, 99755845, 102179296, 100830577; Medium (Rahul Reddy)"),
    ("2", "Revise 11th–12th physics",
     "The OA leans on basic physics: light and shadows, ray optics, fluids and pressure, plus maths. Expect a physics "
     "problem in the interview too (e.g. equilibrium pressure in a water pipeline).",
     "Glassdoor 99969882, 105375633, 102707340, 100830577, 105308810, 101689149; Medium; r/PESU (\"weird questions which will make you remember your 12th physics\")"),
    ("3", "They re-open your OA answers",
     "The first technical round starts from what you wrote in the test and digs into the basics behind it. "
     "Note down your answers and your approach right after the OA.",
     "Glassdoor 102707340, 101021506; Medium"),
    ("4", "CS fundamentals over LeetCode",
     "Computer organisation (Harvard vs von Neumann), the compilation process, program vs process, bits and bytes "
     "(memory size of a program), threads, RTOS vs general-purpose OS, GPU vs CPU architecture, basic C, and "
     "implement a stack or queue. DSA appears but is not the centre.",
     "Glassdoor 92326639, 105584563, 102179296, 105793863; Medium"),
    ("5", "AI/ML from first principles",
     "Convolution by hand (kernel over a grid), the conv output-size formula, convolution vs matrix multiplication, "
     "activation functions and their graphs, why logistic regression is \"regression\", designing a network from "
     "activation functions only. One ML OA asked for a full model with its training and backprop loop in NumPy only.",
     "Glassdoor 100288843, 101600261, 105295606; Medium"),
    ("6", "Every resume line gets drilled",
     "They go to the bottom of each project you list and every detail on the resume. Only list what you can explain "
     "from scratch.",
     "Glassdoor 92326639, 102613579, 102179296, 94348615, 99755845, 105793863"),
    ("7", "\"Why do you want to join?\"",
     "Asked often. Tie it to their work: visual object intelligence, robots that see and handle unfamiliar objects.",
     "Glassdoor 105308810, 105377703, 105409000"),
    ("8", "Expect a slow, long process",
     "Weeks to over a month; feedback is often late, and some candidates got no closure after case studies. Outstation "
     "candidates are flown in one way for in-person rounds. Case studies are meant to be done without AI.",
     "Glassdoor 102613579, 102179296, 100414240, 105184036, 105657664"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=None, help="saved Apify output; without it the actors run (cached 24 h)")
    args = ap.parse_args()
    init_db()
    out: dict = {}
    if args.raw:
        raw = Path(args.raw)
        out["glassdoor"] = interviews.save_glassdoor(CYNLR, json.loads((raw / "apify_glassdoor.json").read_text())["items"])
        out["reddit"] = interviews.save_reddit(CYNLR, "CynLr", json.loads((raw / "apify_reddit.json").read_text())["items"])
    else:
        out["scrape"] = interviews.scrape(CYNLR)
    for r in REPORTS:
        r = dict(r)
        qs = r.pop("questions", None)
        interviews.add_note(CYNLR, kind="report", questions=json.dumps(qs) if qs else None, **r)
    interviews.add_note(CYNLR, kind="official", **OFFICIAL)
    for n, title, body, ev in PREP:
        interviews.add_note(CYNLR, kind="prep", source="summary", source_id=f"prep-{n}", url=GD,
                            title=title, body=body, evidence=ev)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
