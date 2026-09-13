#!/usr/bin/env python3
"""Deterministically validate show-and-tell episode and packaging gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PANEL_KEYS = (
    "viral_hook",
    "youtube_potential",
    "brand_fit",
    "b2b_buyer_value",
    "differentiation",
    "shortform_yield",
    "debate_engagement",
    "demoability",
    "payoff_integrity",
)
PACKAGE_WEIGHTS = {
    "fidelity": 0.20,
    "identity_fit": 0.20,
    "hook_stakes": 0.20,
    "complementarity": 0.15,
    "mobile_legibility": 0.15,
    "evidence_differentiation": 0.10,
}
LANES = {"verdict", "proof", "utility"}
CLAIM_STATUSES = {"achieved", "estimate", "anecdote", "forecast", "target", "plan"}


def word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9$%+.-]+", value or ""))


def score_range(value: object, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and low <= float(value) <= high


def evaluate_episode(episode: dict, forbid_em_dash: bool = False) -> dict:
    errors: list[str] = []
    episode_id = str(episode.get("id") or episode.get("title") or "unnamed")

    runtime = episode.get("runtime_seconds")
    if runtime != 900:
        errors.append(f"runtime_seconds must equal 900, got {runtime!r}")
    if not str(episode.get("first_30_second_promise") or "").strip():
        errors.append("first_30_second_promise is required")

    artifact = episode.get("artifact") or {}
    if not artifact.get("type") or not artifact.get("demo_steps"):
        errors.append("artifact.type and non-empty artifact.demo_steps are required")
    if len(artifact.get("demo_steps") or []) < 2:
        errors.append("artifact.demo_steps must contain at least two visible steps")

    segments = episode.get("segments") or []
    if not segments:
        errors.append("segments are required")
    else:
        expected_start = 0
        for index, segment in enumerate(segments):
            start = segment.get("start_seconds")
            end = segment.get("end_seconds")
            if start != expected_start or not isinstance(end, int) or end <= start:
                errors.append(f"segment {index + 1} is not contiguous or has invalid bounds")
                break
            expected_start = end
        if expected_start != 900:
            errors.append(f"segments must end at 900 seconds, got {expected_start}")
        if segments and segments[0].get("end_seconds", 999) > 30:
            errors.append("the first proof/promise segment must end within 30 seconds")

    claims = episode.get("claims") or []
    for index, claim in enumerate(claims):
        status = claim.get("status")
        if status not in CLAIM_STATUSES:
            errors.append(f"claim {index + 1} has invalid status {status!r}")
        if status == "achieved" and not claim.get("proof_pointer"):
            errors.append(f"achieved claim {index + 1} requires proof_pointer")

    panel = episode.get("panel_scores") or {}
    missing_panel = [key for key in PANEL_KEYS if key not in panel]
    if missing_panel:
        errors.append("missing panel scores: " + ", ".join(missing_panel))
        panel_average = 0.0
    else:
        invalid = [key for key in PANEL_KEYS if not score_range(panel[key], 0, 100)]
        if invalid:
            errors.append("invalid panel scores: " + ", ".join(invalid))
            panel_average = 0.0
        else:
            panel_average = round(sum(float(panel[key]) for key in PANEL_KEYS) / len(PANEL_KEYS), 1)
            if panel_average < 90:
                errors.append(f"panel average {panel_average} is below 90")
            for key in ("demoability", "payoff_integrity"):
                if float(panel[key]) < 90:
                    errors.append(f"{key} score {panel[key]} is below 90")

    packages = episode.get("packages") or []
    if len(packages) != 3:
        errors.append(f"exactly three packages are required, got {len(packages)}")
    lanes = {package.get("lane") for package in packages}
    if lanes != LANES:
        errors.append(f"package lanes must be {sorted(LANES)}, got {sorted(str(x) for x in lanes)}")

    package_results = []
    for package in packages:
        lane = package.get("lane") or "unknown"
        package_errors: list[str] = []
        if not str(package.get("title") or "").strip():
            package_errors.append("title is required")
        thumb_words = word_count(str(package.get("thumbnail_copy") or ""))
        if thumb_words > 4:
            package_errors.append(f"thumbnail_copy has {thumb_words} words; maximum is 4")
        groups = package.get("component_groups") or []
        if not 1 <= len(groups) <= 3:
            package_errors.append(f"component_groups must contain 1-3 groups, got {len(groups)}")

        claim_status = package.get("claim_status")
        if claim_status not in CLAIM_STATUSES:
            package_errors.append(f"invalid claim_status {claim_status!r}")
        if claim_status == "achieved" and not package.get("proof_pointer"):
            package_errors.append("achieved package requires proof_pointer")

        scores = package.get("scores") or {}
        missing = [key for key in PACKAGE_WEIGHTS if key not in scores]
        if missing:
            package_errors.append("missing package scores: " + ", ".join(missing))
            weighted = 0.0
        else:
            invalid = [key for key in PACKAGE_WEIGHTS if not score_range(scores[key], 0, 10)]
            if invalid:
                package_errors.append("invalid package scores: " + ", ".join(invalid))
                weighted = 0.0
            else:
                weighted = round(sum(float(scores[key]) * weight for key, weight in PACKAGE_WEIGHTS.items()), 2)
                floor_failures = [key for key in PACKAGE_WEIGHTS if float(scores[key]) < 8.5]
                if floor_failures:
                    package_errors.append("dimensions below 8.5: " + ", ".join(floor_failures))
                if weighted < 9.0:
                    package_errors.append(f"weighted score {weighted} is below 9.0")

        package_results.append({
            "lane": lane,
            "weighted_score": weighted,
            "thumbnail_word_count": thumb_words,
            "passed": not package_errors,
            "errors": package_errors,
        })
        errors.extend(f"package {lane}: {message}" for message in package_errors)

    if forbid_em_dash:
        text_fields = [
            ("episode title", episode.get("title")),
            ("episode thesis", episode.get("thesis")),
            ("first_30_second_promise", episode.get("first_30_second_promise")),
        ]
        for package in packages:
            lane = package.get("lane") or "unknown"
            text_fields.extend([
                (f"package {lane} title", package.get("title")),
                (f"package {lane} thumbnail_copy", package.get("thumbnail_copy")),
            ])
        for label, value in text_fields:
            if "—" in str(value or ""):
                errors.append(f"{label} contains a forbidden em dash")

    return {
        "id": episode_id,
        "panel_average": panel_average,
        "packages": package_results,
        "passed": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--forbid-em-dash", action="store_true")
    args = parser.parse_args()

    try:
        payload = json.loads(args.input.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    episodes = payload.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        print("ERROR: input must contain a non-empty episodes list", file=sys.stderr)
        return 2

    results = [evaluate_episode(episode, forbid_em_dash=args.forbid_em_dash) for episode in episodes]
    report = {
        "schema_version": 1,
        "episode_count": len(results),
        "passed": all(result["passed"] for result in results),
        "episodes": results,
    }

    rendered = json.dumps(report, indent=2) + "\n"
    if args.output and not args.validate_only:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
