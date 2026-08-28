from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration"))

from run_dry_cohort import (  # noqa: E402
    DEFAULT_BENCHMARK_LOCK,
    DEFAULT_SPEC,
    assemble_dry_cohort,
    run_dry_cohort,
)


class AssembledDryCohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_dry_cohort()

    def test_exercises_every_paired_cell_and_remains_blocked(self) -> None:
        self.assertEqual("DRY_COHORT_COMPLETE", self.report["status"])
        self.assertFalse(self.report["scientific_credit"])
        self.assertTrue(self.report["synthetic_telemetry"])
        self.assertEqual(10, self.report["assignment_count"])
        self.assertEqual(10, self.report["blind_item_count"])
        self.assertEqual(10, self.report["reveal_entry_count"])
        self.assertEqual("BLOCKED", self.report["evaluator"]["decision"])
        self.assertEqual(10, self.report["evaluator"]["received_record_count"])

    def test_verification_admission_and_cost_close_for_each_problem(self) -> None:
        self.assertEqual(2, len(self.report["problem_reports"]))
        for problem in self.report["problem_reports"]:
            self.assertEqual("ADMITTED", problem["admission"])
            self.assertTrue(problem["cost_report_closed"])
            self.assertEqual(2, problem["verified_chain_history_records"])
            self.assertEqual(5, len(problem["arm_contracts_validated"]))
            self.assertEqual(5, len(problem["synthetic_cost_totals"]))

    def test_report_names_scientific_blockers_and_unclosed_schema_seams(self) -> None:
        blockers = {item["id"] for item in self.report["blocking_conditions"]}
        self.assertEqual(
            {
                "BENCHMARK_NOT_FROZEN",
                "COMPLETE_COST_NOT_FROZEN",
                "NO_MODEL_EXECUTION_ADAPTER",
            },
            blockers,
        )
        seams = {item["id"] for item in self.report["remaining_integration_seams"]}
        self.assertIn("COST_OUTCOME_JOIN_NOT_SCHEMA_BOUND", seams)
        self.assertIn("COMMON_INPUT_DIGEST_NOT_IN_ARM_CONTRACTS", seams)

    def test_runner_refuses_to_impersonate_a_frozen_scientific_run(self) -> None:
        spec = json.loads(DEFAULT_SPEC.read_text(encoding="utf-8"))
        lock = json.loads(DEFAULT_BENCHMARK_LOCK.read_text(encoding="utf-8"))

        frozen_cost = copy.deepcopy(spec)
        frozen_cost["cost_model_frozen"] = True
        with self.assertRaisesRegex(RuntimeError, "refuses a frozen scientific cost model"):
            assemble_dry_cohort(frozen_cost, lock)

        confirmatory = copy.deepcopy(spec)
        confirmatory["phase"] = "CONFIRMATORY"
        with self.assertRaisesRegex(RuntimeError, "restricted to phase=DRY_RUN"):
            assemble_dry_cohort(confirmatory, lock)


if __name__ == "__main__":
    unittest.main()
