from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_ideas.py"


def valid_idea() -> dict:
    return {
        "idea_id": "proof-demo",
        "topic": "Proof demo",
        "hook": "This workflow created a measurable result.",
        "virality_score": 9.0,
        "three_second_hook_score": 8.5,
        "payoff_confidence": 8.0,
        "yap_bullets": ["Set the stakes.", "Show the mechanism.", "Prove the result."],
        "complete_payoff": "Show the real result and workflow.",
        "proof_status": "available",
        "cta_keyword": "GUIDE",
        "cta_destination": "https://example.com/guide",
        "source_basis": [
            {"label": "Owned analytics", "url": "https://example.com/source"}
        ],
        "claim_caveat": "Verify the result before publishing.",
    }


class ScoreIdeasTest(unittest.TestCase):
    def run_script(self, payload: object, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(path), *args],
                capture_output=True,
                text=True,
            )

    def test_validate_rank_and_score(self) -> None:
        validation = self.run_script([valid_idea()], "--validate-only")
        self.assertEqual(validation.returncode, 0)
        scored = self.run_script([valid_idea()])
        self.assertEqual(scored.returncode, 0)
        record = json.loads(scored.stdout)[0]
        self.assertEqual(record["priority_score"], 8.6)
        self.assertEqual(record["rank"], 1)

    def test_requires_exactly_three_yap_bullets(self) -> None:
        idea = valid_idea()
        idea["yap_bullets"] = ["one", "two"]
        result = self.run_script([idea])
        self.assertEqual(result.returncode, 2)
        self.assertIn("exactly three", result.stderr)

    def test_non_object_and_missing_fields_are_concise(self) -> None:
        non_object = self.run_script([1])
        self.assertEqual(non_object.returncode, 2)
        self.assertNotIn("Traceback", non_object.stderr)
        missing = self.run_script([{"idea_id": "bad"}])
        self.assertEqual(missing.returncode, 2)
        self.assertIn("row 1", missing.stderr)
        self.assertNotIn("Traceback", missing.stderr)

    def test_out_of_range_score_fails(self) -> None:
        idea = valid_idea()
        idea["virality_score"] = 11
        result = self.run_script([idea])
        self.assertEqual(result.returncode, 2)
        self.assertIn("between 1 and 10", result.stderr)


if __name__ == "__main__":
    unittest.main()
