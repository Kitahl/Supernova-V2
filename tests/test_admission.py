from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.admission import evaluate_product_admission


ARTIFACT = "a" * 64
EVIDENCE_A = "b" * 64
EVIDENCE_B = "c" * 64


class ProductAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = {
            "product_id": "lemma-17",
            "producer_id": "producer-A",
            "artifact_sha256": ARTIFACT,
        }
        self.policy = {
            "policy_id": "verified-product-v1",
            "required_checks": ["kernel", "statement_fidelity"],
        }
        self.evidence = [
            {
                "evidence_id": "ev-kernel",
                "check_id": "kernel",
                "product_id": "lemma-17",
                "artifact_sha256": ARTIFACT,
                "verifier_id": "verifier-kernel",
                "outcome": "PASS",
                "evidence_sha256": EVIDENCE_A,
            },
            {
                "evidence_id": "ev-fidelity",
                "check_id": "statement_fidelity",
                "product_id": "lemma-17",
                "artifact_sha256": ARTIFACT,
                "verifier_id": "verifier-fidelity",
                "outcome": "PASS",
                "evidence_sha256": EVIDENCE_B,
            },
        ]

    def test_complete_independent_pass_evidence_admits(self) -> None:
        result = evaluate_product_admission(self.product, self.evidence, self.policy)
        self.assertEqual("ADMITTED", result["admission"])
        self.assertTrue(result["admitted"])
        self.assertEqual(["ev-fidelity", "ev-kernel"], result["evidence_ids"])
        self.assertEqual([], result["reasons"])

    def test_producer_cannot_self_admit(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_id"] = self.product["producer_id"]
        result = evaluate_product_admission(self.product, evidence, self.policy)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn("producer cannot verify its own product: kernel", result["reasons"])

    def test_non_pass_evidence_rejects(self) -> None:
        for outcome in ("FAIL", "TIMEOUT", "ERROR"):
            with self.subTest(outcome=outcome):
                evidence = copy.deepcopy(self.evidence)
                evidence[0]["outcome"] = outcome
                result = evaluate_product_admission(self.product, evidence, self.policy)
                self.assertFalse(result["admitted"])
                self.assertIn(f"check did not PASS: kernel={outcome}", result["reasons"])

    def test_subject_identity_and_digest_are_bound(self) -> None:
        wrong_product = copy.deepcopy(self.evidence)
        wrong_product[0]["product_id"] = "another-product"
        result = evaluate_product_admission(self.product, wrong_product, self.policy)
        self.assertIn("product_id mismatch for check: kernel", result["reasons"])

        wrong_digest = copy.deepcopy(self.evidence)
        wrong_digest[0]["artifact_sha256"] = "d" * 64
        result = evaluate_product_admission(self.product, wrong_digest, self.policy)
        self.assertIn("artifact digest mismatch for check: kernel", result["reasons"])

    def test_missing_duplicate_and_unexpected_checks_reject(self) -> None:
        result = evaluate_product_admission(self.product, self.evidence[:1], self.policy)
        self.assertIn("missing required check: statement_fidelity", result["reasons"])

        duplicate = [*copy.deepcopy(self.evidence), copy.deepcopy(self.evidence[0])]
        duplicate[-1]["evidence_id"] = "ev-kernel-2"
        result = evaluate_product_admission(self.product, duplicate, self.policy)
        self.assertIn("duplicate required check: kernel", result["reasons"])

        unexpected = copy.deepcopy(self.evidence)
        unexpected.append(
            {
                "evidence_id": "ev-scientific-score",
                "check_id": "scientific_score",
                "product_id": "lemma-17",
                "artifact_sha256": ARTIFACT,
                "verifier_id": "scientific-evaluator",
                "outcome": "PASS",
                "evidence_sha256": "d" * 64,
            }
        )
        result = evaluate_product_admission(self.product, unexpected, self.policy)
        self.assertIn("unexpected check: scientific_score", result["reasons"])

    def test_duplicate_evidence_ids_reject(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
        result = evaluate_product_admission(self.product, evidence, self.policy)
        self.assertIn("duplicate evidence_id", result["reasons"])

    def test_input_order_does_not_change_decision(self) -> None:
        forward = evaluate_product_admission(self.product, self.evidence, self.policy)
        reverse = evaluate_product_admission(
            self.product, reversed(self.evidence), self.policy
        )
        self.assertEqual(forward, reverse)

    def test_scientific_scoring_fields_are_not_admission_inputs(self) -> None:
        for container, field in (
            (self.product, "score"),
            (self.policy, "familywise_alpha"),
            (self.evidence[0], "scientific_score"),
        ):
            with self.subTest(field=field):
                product = copy.deepcopy(self.product)
                policy = copy.deepcopy(self.policy)
                evidence = copy.deepcopy(self.evidence)
                if container is self.product:
                    product[field] = 1.0
                elif container is self.policy:
                    policy[field] = 0.05
                else:
                    evidence[0][field] = 1.0
                with self.assertRaisesRegex(ValueError, "fields must be exactly"):
                    evaluate_product_admission(product, evidence, policy)

    def test_digest_format_is_strict_and_lowercase(self) -> None:
        product = copy.deepcopy(self.product)
        product["artifact_sha256"] = "A" * 64
        with self.assertRaisesRegex(ValueError, "lowercase hex"):
            evaluate_product_admission(product, self.evidence, self.policy)

        evidence = copy.deepcopy(self.evidence)
        evidence[0]["evidence_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            evaluate_product_admission(self.product, evidence, self.policy)


if __name__ == "__main__":
    unittest.main()
