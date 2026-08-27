from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.evaluate import evaluate_experiment
from supernova_goal1.statistics import HolmResult


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

    def test_complete_evaluation_uses_shared_statistics_library(self) -> None:
        spec = copy.deepcopy(self.spec)
        spec["cost_model_frozen"] = True
        corrections = tuple(
            HolmResult(p_value=0.01, threshold=0.0125, rejects_null=True)
            for _ in range(4)
        )

        with (
            patch(
                "supernova_goal1.evaluate.mcnemar_exact_two_sided",
                return_value=0.01,
            ) as mcnemar,
            patch(
                "supernova_goal1.evaluate.holm_step_down",
                return_value=corrections,
            ) as holm,
        ):
            result = evaluate_experiment(spec, self.records)

        self.assertEqual("PASS", result["decision"])
        self.assertEqual(4, mcnemar.call_count)
        self.assertTrue(all(call.args == (2, 0) for call in mcnemar.call_args_list))
        holm.assert_called_once()
        p_values, alpha = holm.call_args.args
        self.assertEqual((0.01, 0.01, 0.01, 0.01), tuple(p_values))
        self.assertEqual(0.05, alpha)


if __name__ == "__main__":
    unittest.main()
