from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.evaluate import evaluate_experiment


class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_spec = json.loads(
            (ROOT / "examples" / "dry_run_goal1.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def record(experiment_id: str, problem_id: str, arm: str, solved: bool) -> dict:
        return {
            "experiment_id": experiment_id,
            "problem_id": problem_id,
            "arm": arm,
            "budget_id": "dry-budget-v1",
            "model_usage_basis": "provider_tokens",
            "solved": solved,
            "verifier_passed": solved,
            "cost": {
                "model_calls": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "verifier_milliseconds": 1,
                "orchestration_milliseconds": 1,
            },
        }

    def test_strong_candidate_result_passes(self) -> None:
        problem_ids = [f"p{index:02d}" for index in range(12)]
        spec = copy.deepcopy(self.base_spec)
        spec["experiment_id"] = "strong-candidate"
        spec["required_problem_ids"] = problem_ids
        spec["cost_model_frozen"] = True
        spec["model_usage_basis"] = "provider_tokens"
        records = [
            self.record("strong-candidate", problem_id, arm, arm == "verified_chain")
            for problem_id in problem_ids
            for arm in spec["arms"]
        ]
        result = evaluate_experiment(spec, records)
        self.assertEqual("PASS", result["decision"])
        self.assertTrue(all(item["holm_rejects_null"] for item in result["pairwise"]))

    def test_tie_fails_and_input_order_is_irrelevant(self) -> None:
        spec = copy.deepcopy(self.base_spec)
        spec["cost_model_frozen"] = True
        spec["model_usage_basis"] = "provider_tokens"
        records = [
            self.record(spec["experiment_id"], problem_id, arm, False)
            for problem_id in spec["required_problem_ids"]
            for arm in spec["arms"]
        ]
        forward = evaluate_experiment(spec, records)
        reverse = evaluate_experiment(spec, reversed(records))
        self.assertEqual("FAIL", forward["decision"])
        self.assertEqual(forward, reverse)


if __name__ == "__main__":
    unittest.main()
