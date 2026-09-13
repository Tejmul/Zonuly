import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_slate.py"
SPEC = importlib.util.spec_from_file_location("evaluate_slate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def package(lane):
    return {
        "lane": lane,
        "title": f"A strong {lane} title",
        "thumbnail_copy": "SHOW THE RESULT",
        "component_groups": ["creator", "artifact", "outcome"],
        "claim_status": "achieved",
        "proof_pointer": "native-dashboard",
        "scores": {
            "fidelity": 9.2,
            "identity_fit": 9.1,
            "hook_stakes": 9.3,
            "complementarity": 9.1,
            "mobile_legibility": 9.2,
            "evidence_differentiation": 8.8,
        },
    }


def valid_episode():
    return {
        "id": "demo",
        "title": "A proof-led build demo",
        "runtime_seconds": 900,
        "first_30_second_promise": "I will show the result and the system that produced it.",
        "artifact": {
            "type": "dashboard",
            "demo_steps": ["show outcome", "run workflow", "show output"],
        },
        "segments": [
            {"start_seconds": 0, "end_seconds": 30},
            {"start_seconds": 30, "end_seconds": 120},
            {"start_seconds": 120, "end_seconds": 300},
            {"start_seconds": 300, "end_seconds": 600},
            {"start_seconds": 600, "end_seconds": 780},
            {"start_seconds": 780, "end_seconds": 900},
        ],
        "claims": [
            {"text": "A measured result", "status": "achieved", "proof_pointer": "native-dashboard"}
        ],
        "panel_scores": {
            "viral_hook": 93,
            "youtube_potential": 92,
            "brand_fit": 95,
            "b2b_buyer_value": 94,
            "differentiation": 93,
            "shortform_yield": 92,
            "debate_engagement": 90,
            "demoability": 96,
            "payoff_integrity": 95,
        },
        "packages": [package("verdict"), package("proof"), package("utility")],
    }


class EvaluateSlateTests(unittest.TestCase):
    def test_valid_episode_passes(self):
        result = MODULE.evaluate_episode(valid_episode())
        self.assertTrue(result["passed"], result["errors"])
        self.assertGreaterEqual(result["panel_average"], 90)
        self.assertTrue(all(item["weighted_score"] >= 9 for item in result["packages"]))

    def test_runtime_and_score_failures_are_reported(self):
        episode = valid_episode()
        episode["runtime_seconds"] = 899
        episode["packages"][0]["scores"]["fidelity"] = 8.0
        result = MODULE.evaluate_episode(episode)
        self.assertFalse(result["passed"])
        joined = "\n".join(result["errors"])
        self.assertIn("runtime_seconds must equal 900", joined)
        self.assertIn("dimensions below 8.5", joined)

    def test_achieved_claim_requires_proof(self):
        episode = valid_episode()
        episode["claims"][0]["proof_pointer"] = ""
        result = MODULE.evaluate_episode(episode)
        self.assertFalse(result["passed"])
        self.assertTrue(any("requires proof_pointer" in item for item in result["errors"]))

    def test_invalid_score_type_is_reported_without_crashing(self):
        episode = valid_episode()
        episode["packages"][0]["scores"]["fidelity"] = "excellent"
        result = MODULE.evaluate_episode(episode)
        self.assertFalse(result["passed"])
        self.assertTrue(any("invalid package scores" in item for item in result["errors"]))

    def test_private_voice_gate_rejects_em_dash(self):
        episode = valid_episode()
        episode["packages"][0]["title"] = "A result—if the system works"
        result = MODULE.evaluate_episode(episode, forbid_em_dash=True)
        self.assertFalse(result["passed"])
        self.assertTrue(any("forbidden em dash" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
