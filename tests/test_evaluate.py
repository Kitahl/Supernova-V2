from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.evaluate import evaluate_experiment


class EvaluateExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8"))
        cls.records = json.loads(
            (ROOT / "examples" / "dry_run_records.json").read_text(encoding="utf-8")
        )

    def test_unfrozen_cost_model_blocks_scientific_decision(self) -> None:
        result = evaluate_experiment(self.spec, self.records)
        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual([], result["missing"])

    def test_missing_record_is_incomplete_after_cost_freeze(self) -> None:
        spec = copy.deepcopy(self.spec)
        spec["cost_model_frozen"] = True
        result = evaluate_experiment(spec, self.records[:-1])
        self.assertEqual("INCOMPLETE", result["decision"])
        self.assertEqual(
            [{"problem_id": "dry-002", "arm": "verified_chain"}], result["missing"]
        )

    def test_duplicate_record_is_invalid(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate outcome"):
            evaluate_experiment(self.spec, [*self.records, self.records[0]])

    def test_solved_requires_verifier_pass(self) -> None:
        records = copy.deepcopy(self.records)
        records[-1]["verifier_passed"] = False
        with self.assertRaisesRegex(ValueError, "requires verifier_passed"):
            evaluate_experiment(self.spec, records)

    def test_cost_overrun_is_invalid(self) -> None:
        records = copy.deepcopy(self.records)
        records[0]["cost"]["model_calls"] = 17
        with self.assertRaisesRegex(ValueError, "cost ceiling exceeded"):
            evaluate_experiment(self.spec, records)


if __name__ == "__main__":
    unittest.main()
