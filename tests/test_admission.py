from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.admission import evaluate_product_admission


ARTIFACT = "a" * 64
DIGEST_DOMAIN = "supernova_goal1.admission_evidence.v1"


def evidence_digest(record: dict[str, object]) -> str:
    payload = {
        "artifact_sha256": record["artifact_sha256"],
        "check_id": record["check_id"],
        "evidence_id": record["evidence_id"],
        "outcome": record["outcome"],
        "product_id": record["product_id"],
        "schema": DIGEST_DOMAIN,
        "verifier_id": record["verifier_id"],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def with_digest(record: dict[str, object]) -> dict[str, object]:
    record = copy.deepcopy(record)
    record["evidence_sha256"] = evidence_digest(record)
    return record


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
            "authorized_verifiers": {
                "kernel": ["verifier-kernel"],
                "statement_fidelity": ["verifier-fidelity"],
            },
        }
        self.evidence = [
            with_digest(
                {
                    "evidence_id": "ev-kernel",
                    "check_id": "kernel",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-kernel",
                    "outcome": "PASS",
                }
            ),
            with_digest(
                {
                    "evidence_id": "ev-fidelity",
                    "check_id": "statement_fidelity",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-fidelity",
                    "outcome": "PASS",
                }
            ),
        ]

    def test_complete_independent_pass_evidence_admits(self) -> None:
        result = evaluate_product_admission(self.product, self.evidence, self.policy)
        self.assertEqual("ADMITTED", result["admission"])
        self.assertTrue(result["admitted"])
        self.assertEqual(["ev-fidelity", "ev-kernel"], result["evidence_ids"])
        self.assertEqual([], result["reasons"])

    def test_producer_cannot_self_admit_even_if_policy_authorizes_identity(self) -> None:
        product = copy.deepcopy(self.product)
        product["producer_id"] = "verifier-kernel"
        result = evaluate_product_admission(product, self.evidence, self.policy)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "producer cannot verify its own product: kernel",
            result["reasons"],
        )

    def test_fake_verifier_identity_rejects_even_with_matching_content_digest(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_id"] = "verifier-pretender"
        evidence[0]["evidence_sha256"] = evidence_digest(evidence[0])
        result = evaluate_product_admission(self.product, evidence, self.policy)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "unauthorized verifier for check: kernel=verifier-pretender",
            result["reasons"],
        )

    def test_forged_digest_and_tampered_content_reject(self) -> None:
        forged_digest = copy.deepcopy(self.evidence)
        forged_digest[0]["evidence_sha256"] = "f" * 64
        result = evaluate_product_admission(
            self.product,
            forged_digest,
            self.policy,
        )
        self.assertIn("evidence digest mismatch: ev-kernel", result["reasons"])

        tampered_content = copy.deepcopy(self.evidence)
        tampered_content[0]["evidence_id"] = "ev-kernel-tampered"
        result = evaluate_product_admission(
            self.product,
            tampered_content,
            self.policy,
        )
        self.assertIn(
            "evidence digest mismatch: ev-kernel-tampered",
            result["reasons"],
        )

    def test_non_pass_evidence_rejects(self) -> None:
        for outcome in ("FAIL", "TIMEOUT", "ERROR"):
            with self.subTest(outcome=outcome):
                evidence = copy.deepcopy(self.evidence)
                evidence[0]["outcome"] = outcome
                evidence[0]["evidence_sha256"] = evidence_digest(evidence[0])
                result = evaluate_product_admission(self.product, evidence, self.policy)
                self.assertFalse(result["admitted"])
                self.assertIn(f"check did not PASS: kernel={outcome}", result["reasons"])

    def test_subject_identity_and_digest_are_bound(self) -> None:
        wrong_product = copy.deepcopy(self.evidence)
        wrong_product[0]["product_id"] = "another-product"
        wrong_product[0]["evidence_sha256"] = evidence_digest(wrong_product[0])
        result = evaluate_product_admission(self.product, wrong_product, self.policy)
        self.assertIn("product_id mismatch for check: kernel", result["reasons"])

        wrong_digest = copy.deepcopy(self.evidence)
        wrong_digest[0]["artifact_sha256"] = "d" * 64
        wrong_digest[0]["evidence_sha256"] = evidence_digest(wrong_digest[0])
        result = evaluate_product_admission(self.product, wrong_digest, self.policy)
        self.assertIn("artifact digest mismatch for check: kernel", result["reasons"])

    def test_missing_duplicate_and_unexpected_checks_reject(self) -> None:
        result = evaluate_product_admission(self.product, self.evidence[:1], self.policy)
        self.assertIn("missing required check: statement_fidelity", result["reasons"])

        duplicate = [*copy.deepcopy(self.evidence), copy.deepcopy(self.evidence[0])]
        duplicate[-1]["evidence_id"] = "ev-kernel-2"
        duplicate[-1]["evidence_sha256"] = evidence_digest(duplicate[-1])
        result = evaluate_product_admission(self.product, duplicate, self.policy)
        self.assertIn("duplicate required check: kernel", result["reasons"])

        unexpected = copy.deepcopy(self.evidence)
        unexpected.append(
            with_digest(
                {
                    "evidence_id": "ev-scientific-score",
                    "check_id": "scientific_score",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "scientific-evaluator",
                    "outcome": "PASS",
                }
            )
        )
        result = evaluate_product_admission(self.product, unexpected, self.policy)
        self.assertIn("unexpected check: scientific_score", result["reasons"])

    def test_duplicate_evidence_ids_reject(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
        evidence[1]["evidence_sha256"] = evidence_digest(evidence[1])
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

    def test_policy_requires_closed_verifier_authority_for_every_check(self) -> None:
        missing = copy.deepcopy(self.policy)
        del missing["authorized_verifiers"]["kernel"]
        with self.assertRaisesRegex(
            ValueError,
            "authorized_verifiers keys must exactly match required_checks",
        ):
            evaluate_product_admission(self.product, self.evidence, missing)

        duplicate = copy.deepcopy(self.policy)
        duplicate["authorized_verifiers"]["kernel"] = [
            "verifier-kernel",
            "verifier-kernel",
        ]
        with self.assertRaisesRegex(ValueError, "must be unique"):
            evaluate_product_admission(self.product, self.evidence, duplicate)


if __name__ == "__main__":
    unittest.main()
