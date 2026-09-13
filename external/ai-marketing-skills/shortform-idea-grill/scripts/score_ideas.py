#!/usr/bin/env python3
"""Validate, rank, and score short-form production ideas."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TEXT_FIELDS = {
    "idea_id",
    "topic",
    "hook",
    "complete_payoff",
    "proof_status",
    "cta_keyword",
    "cta_destination",
    "claim_caveat",
}
SCORE_FIELDS = {
    "virality_score",
    "three_second_hook_score",
    "payoff_confidence",
}
PROOF_STATUSES = {"verified", "available", "needs_verification", "thesis"}


def load(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("input must be a JSON array")
    if not all(isinstance(row, dict) for row in data):
        raise ValueError("every array item must be an object")
    return data


def validate_record(record: dict[str, Any], index: int) -> list[str]:
    prefix = f"row {index}"
    errors: list[str] = []

    for field in TEXT_FIELDS:
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(f"{prefix}: {field} must be a non-empty string")

    idea_id = record.get("idea_id")
    if isinstance(idea_id, str) and not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", idea_id
    ):
        errors.append(f"{prefix}: idea_id must be kebab-case")

    if record.get("proof_status") not in PROOF_STATUSES:
        errors.append(
            f"{prefix}: proof_status must be one of "
            + ", ".join(sorted(PROOF_STATUSES))
        )

    bullets = record.get("yap_bullets")
    if (
        not isinstance(bullets, list)
        or len(bullets) != 3
        or not all(isinstance(item, str) and item.strip() for item in bullets)
    ):
        errors.append(f"{prefix}: yap_bullets must contain exactly three strings")

    sources = record.get("source_basis")
    if not isinstance(sources, list):
        errors.append(f"{prefix}: source_basis must be an array")
    else:
        for source_index, source in enumerate(sources, 1):
            if not isinstance(source, dict):
                errors.append(f"{prefix}: source {source_index} must be an object")
                continue
            for field in ("label", "url"):
                if not isinstance(source.get(field), str) or not source[field].strip():
                    errors.append(
                        f"{prefix}: source {source_index} {field} must be non-empty"
                    )

    for field in SCORE_FIELDS:
        try:
            value = float(record[field])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{prefix}: {field} must be numeric")
            continue
        if not 1 <= value <= 10:
            errors.append(f"{prefix}: {field} must be between 1 and 10")

    return errors


def priority_score(record: dict[str, Any]) -> float:
    return round(
        float(record["virality_score"]) * 0.40
        + float(record["three_second_hook_score"]) * 0.40
        + float(record["payoff_confidence"]) * 0.20,
        1,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    try:
        records = load(args.input)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"validation error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    errors = [
        error
        for index, record in enumerate(records, 1)
        for error in validate_record(record, index)
    ]
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(2)

    if args.validate_only:
        print(f"valid: {len(records)} idea records")
        return

    for record in records:
        record["priority_score"] = priority_score(record)
    records.sort(key=lambda row: row["priority_score"], reverse=True)
    for rank, record in enumerate(records, 1):
        record["rank"] = rank

    payload = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
