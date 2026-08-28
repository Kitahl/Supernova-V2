from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


from integration.non_credit_pilot import (
    BENCHMARK_ROOT_SHA256,
    CLASSIFICATION,
    PROBLEM_NATIVE_ID,
    run_non_credit_pilot,
)


class NonCreditPilotTests(unittest.TestCase):
    def test_five_arm_execution_closes_without_scientific_credit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="g1-114-test-") as temporary:
            report = run_non_credit_pilot(Path(temporary))

        self.assertEqual("NON_CREDIT_PILOT", CLASSIFICATION)
        self.assertEqual(CLASSIFICATION, report["classification"])
        self.assertEqual("NONE", report["scientific_claim"])
        self.assertEqual("NOT_EVALUATED", report["goal1_result"])

        lineage = report["benchmark_lineage"]
        self.assertEqual(BENCHMARK_ROOT_SHA256, lineage["locked_root_sha256"])
        self.assertEqual(PROBLEM_NATIVE_ID, lineage["problem_native_id"])
        self.assertEqual(
            "NOT_ESTABLISHED_ENGINEERING_STUB",
            lineage["problem_membership"],
        )

        evidence = report["evidence"]
        self.assertEqual(
            [
                "ordinary",
                "portfolio",
                "product_only",
                "multi_fidelity",
                "verified_chain",
            ],
            evidence["arms"],
        )
        self.assertTrue(evidence["dispatch_closed"])
        self.assertEqual(8, evidence["completion_count"])
        self.assertEqual(7, evidence["verifier_receipt_count"])
        self.assertEqual(
            ["PASS", "PASS", "PASS", "PASS", "FAIL", "PASS", "PASS"],
            evidence["verifier_statuses"],
        )
        self.assertEqual(
            {
                "ordinary": 1,
                "portfolio": 1,
                "product_only": 2,
                "multi_fidelity": 1,
                "verified_chain": 3,
            },
            evidence["model_call_counts"],
        )
        self.assertEqual("visible_utf8_bytes", evidence["cost_usage_basis"])
        self.assertEqual(set(evidence["arms"]), set(evidence["costs"]))
        for arm, cost in evidence["costs"].items():
            self.assertEqual(evidence["model_call_counts"][arm], cost["model_calls"])
            self.assertGreater(cost["input_utf8_bytes"], 0)
            self.assertGreater(cost["output_utf8_bytes"], 0)
            self.assertGreaterEqual(cost["verifier_milliseconds"], 0)
            self.assertGreaterEqual(cost["orchestration_milliseconds"], 0)
        self.assertTrue(
            evidence["verified_retry_evidence_id"].startswith("hmac-sha256:")
        )
        self.assertTrue(
            evidence["admitted_product_evidence_id"].startswith("hmac-sha256:")
        )
        self.assertTrue(evidence["terminal_verified_chain_answer"])

        seams = report["seam_failures"]
        self.assertIn("NO_TYPED_PATH", seams["authority_to_evaluator"])
        self.assertIn("ATTEMPT_INDEX_ONLY", seams["product_control_retry"])
        self.assertIn("STUB_RECEIPTS", seams["problem_and_verifier"])
        self.assertIn("ARE_NOT_GOLDEN", seams["determinism"])

    def test_logical_result_is_deterministic_except_observed_timing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="g1-114-first-") as first_dir:
            first = run_non_credit_pilot(first_dir)
        with tempfile.TemporaryDirectory(prefix="g1-114-second-") as second_dir:
            second = run_non_credit_pilot(second_dir)

        for report in (first, second):
            evidence = report["evidence"]
            evidence.pop("manifest_sha256")
            evidence.pop("close_sha256")
            evidence.pop("verified_retry_evidence_id")
            evidence.pop("admitted_product_evidence_id")
            for cost in report["evidence"]["costs"].values():
                cost.pop("orchestration_milliseconds")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
