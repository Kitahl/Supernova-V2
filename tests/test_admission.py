from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.admission import evaluate_product_admission


ARTIFACT = "a" * 64
DIGEST_DOMAIN = "supernova_goal1.admission_evidence.v1"
POLICY_DIGEST_DOMAIN = "supernova_goal1.admission_policy.v1"
VERIFIER_KEYS = {
    "verifier-kernel": b"unit-test-kernel-authority-key-v1",
    "verifier-fidelity": b"unit-test-fidelity-authority-key-v1",
}


def canonical_evidence(record: dict[str, object]) -> bytes:
    payload = {
        "artifact_sha256": record["artifact_sha256"],
        "check_id": record["check_id"],
        "evidence_id": record["evidence_id"],
        "outcome": record["outcome"],
        "product_id": record["product_id"],
        "schema": DIGEST_DOMAIN,
        "verifier_id": record["verifier_id"],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def evidence_digest(record: dict[str, object]) -> str:
    return hashlib.sha256(canonical_evidence(record)).hexdigest()


def evidence_hmac(record: dict[str, object], key: bytes) -> str:
    return hmac.new(key, canonical_evidence(record), hashlib.sha256).hexdigest()


def with_auth(record: dict[str, object], key: bytes) -> dict[str, object]:
    record = copy.deepcopy(record)
    record["evidence_sha256"] = evidence_digest(record)
    record["verifier_hmac_sha256"] = evidence_hmac(record, key)
    return record


def canonical_policy(policy: dict[str, object]) -> bytes:
    authorized = policy["authorized_verifiers"]
    commitments = policy["verifier_key_sha256"]
    assert isinstance(authorized, dict)
    assert isinstance(commitments, dict)
    payload = {
        "authorized_verifiers": {
            check_id: sorted(authorized[check_id])
            for check_id in sorted(authorized)
        },
        "policy_id": policy["policy_id"],
        "required_checks": sorted(policy["required_checks"]),
        "schema": POLICY_DIGEST_DOMAIN,
        "verifier_key_sha256": {
            verifier_id: commitments[verifier_id]
            for verifier_id in sorted(commitments)
        },
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def policy_digest(policy: dict[str, object]) -> str:
    return hashlib.sha256(canonical_policy(policy)).hexdigest()


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
            "verifier_key_sha256": {
                verifier_id: hashlib.sha256(key).hexdigest()
                for verifier_id, key in VERIFIER_KEYS.items()
            },
        }
        self.trusted_policy_sha256 = policy_digest(self.policy)
        self.auth_keys = dict(VERIFIER_KEYS)
        self.evidence = [
            with_auth(
                {
                    "evidence_id": "ev-kernel",
                    "check_id": "kernel",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-kernel",
                    "outcome": "PASS",
                },
                VERIFIER_KEYS["verifier-kernel"],
            ),
            with_auth(
                {
                    "evidence_id": "ev-fidelity",
                    "check_id": "statement_fidelity",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-fidelity",
                    "outcome": "PASS",
                },
                VERIFIER_KEYS["verifier-fidelity"],
            ),
        ]

    def evaluate(
        self,
        product=None,
        evidence=None,
        policy=None,
        *,
        auth_keys=None,
        trusted_policy_sha256=None,
    ):
        return evaluate_product_admission(
            self.product if product is None else product,
            self.evidence if evidence is None else evidence,
            self.policy if policy is None else policy,
            trusted_policy_sha256=(
                self.trusted_policy_sha256
                if trusted_policy_sha256 is None
                else trusted_policy_sha256
            ),
            verifier_auth_keys=self.auth_keys if auth_keys is None else auth_keys,
        )

    def test_complete_independent_pass_evidence_admits(self) -> None:
        result = self.evaluate()
        self.assertEqual("ADMITTED", result["admission"])
        self.assertTrue(result["admitted"])
        self.assertEqual(["ev-fidelity", "ev-kernel"], result["evidence_ids"])
        self.assertEqual([], result["reasons"])

    def test_producer_cannot_self_admit_even_with_valid_verifier_key(self) -> None:
        product = copy.deepcopy(self.product)
        product["producer_id"] = "verifier-kernel"
        result = self.evaluate(product=product)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "producer cannot verify its own product: kernel",
            result["reasons"],
        )

    def test_fake_verifier_identity_rejects_even_with_matching_digest_and_hmac(self) -> None:
        attacker_key = b"unit-test-attacker-key"
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_id"] = "verifier-pretender"
        evidence[0]["evidence_sha256"] = evidence_digest(evidence[0])
        evidence[0]["verifier_hmac_sha256"] = evidence_hmac(
            evidence[0], attacker_key
        )
        result = self.evaluate(evidence=evidence)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "unauthorized verifier for check: kernel=verifier-pretender",
            result["reasons"],
        )

    def test_authorized_name_impersonation_rejects_without_verifier_key(self) -> None:
        attacker_key = b"unit-test-attacker-key"
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_hmac_sha256"] = evidence_hmac(
            evidence[0], attacker_key
        )
        result = self.evaluate(evidence=evidence)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "verifier authentication failed: ev-kernel",
            result["reasons"],
        )

    def test_runtime_key_substitution_rejects_against_policy_commitment(self) -> None:
        attacker_key = b"unit-test-attacker-key"
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_hmac_sha256"] = evidence_hmac(
            evidence[0], attacker_key
        )
        keys = dict(self.auth_keys)
        keys["verifier-kernel"] = attacker_key
        result = self.evaluate(evidence=evidence, auth_keys=keys)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "verifier key commitment mismatch: verifier-kernel",
            result["reasons"],
        )

    def test_missing_runtime_verifier_key_rejects(self) -> None:
        keys = {"verifier-fidelity": self.auth_keys["verifier-fidelity"]}
        result = self.evaluate(auth_keys=keys)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn(
            "missing verifier authentication key: verifier-kernel",
            result["reasons"],
        )

    def test_forged_policy_authority_rejects_against_trusted_policy_digest(self) -> None:
        attacker_key = b"unit-test-attacker-key"
        policy = copy.deepcopy(self.policy)
        policy["authorized_verifiers"]["kernel"] = ["verifier-pretender"]
        del policy["verifier_key_sha256"]["verifier-kernel"]
        policy["verifier_key_sha256"]["verifier-pretender"] = hashlib.sha256(
            attacker_key
        ).hexdigest()

        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_id"] = "verifier-pretender"
        evidence[0]["evidence_sha256"] = evidence_digest(evidence[0])
        evidence[0]["verifier_hmac_sha256"] = evidence_hmac(
            evidence[0], attacker_key
        )
        keys = {
            "verifier-pretender": attacker_key,
            "verifier-fidelity": self.auth_keys["verifier-fidelity"],
        }

        result = self.evaluate(evidence=evidence, policy=policy, auth_keys=keys)
        self.assertEqual("REJECTED", result["admission"])
        self.assertIn("admission policy digest mismatch", result["reasons"])

        result_with_forged_root = self.evaluate(
            evidence=evidence,
            policy=policy,
            auth_keys=keys,
            trusted_policy_sha256=policy_digest(policy),
        )
        self.assertEqual("ADMITTED", result_with_forged_root["admission"])
        self.assertEqual([], result_with_forged_root["reasons"])

    def test_policy_digest_is_semantic_order_invariant(self) -> None:
        reordered = copy.deepcopy(self.policy)
        reordered["required_checks"] = list(reversed(reordered["required_checks"]))
        reordered["authorized_verifiers"] = {
            "statement_fidelity": reordered["authorized_verifiers"]["statement_fidelity"],
            "kernel": reordered["authorized_verifiers"]["kernel"],
        }
        self.assertEqual(self.trusted_policy_sha256, policy_digest(reordered))
        self.assertEqual("ADMITTED", self.evaluate(policy=reordered)["admission"])

    def test_forged_digest_and_tampered_content_reject(self) -> None:
        forged_digest = copy.deepcopy(self.evidence)
        forged_digest[0]["evidence_sha256"] = "f" * 64
        result = self.evaluate(evidence=forged_digest)
        self.assertIn("evidence digest mismatch: ev-kernel", result["reasons"])

        tampered_content = copy.deepcopy(self.evidence)
        tampered_content[0]["evidence_id"] = "ev-kernel-tampered"
        result = self.evaluate(evidence=tampered_content)
        self.assertIn(
            "evidence digest mismatch: ev-kernel-tampered",
            result["reasons"],
        )
        self.assertIn(
            "verifier authentication failed: ev-kernel-tampered",
            result["reasons"],
        )

    def test_non_pass_evidence_rejects(self) -> None:
        for outcome in ("FAIL", "TIMEOUT", "ERROR"):
            with self.subTest(outcome=outcome):
                evidence = copy.deepcopy(self.evidence)
                evidence[0]["outcome"] = outcome
                evidence[0]["evidence_sha256"] = evidence_digest(evidence[0])
                evidence[0]["verifier_hmac_sha256"] = evidence_hmac(
                    evidence[0], self.auth_keys["verifier-kernel"]
                )
                result = self.evaluate(evidence=evidence)
                self.assertFalse(result["admitted"])
                self.assertIn(f"check did not PASS: kernel={outcome}", result["reasons"])

    def test_subject_identity_and_digest_are_bound(self) -> None:
        wrong_product = copy.deepcopy(self.evidence)
        wrong_product[0]["product_id"] = "another-product"
        wrong_product[0]["evidence_sha256"] = evidence_digest(wrong_product[0])
        wrong_product[0]["verifier_hmac_sha256"] = evidence_hmac(
            wrong_product[0], self.auth_keys["verifier-kernel"]
        )
        result = self.evaluate(evidence=wrong_product)
        self.assertIn("product_id mismatch for check: kernel", result["reasons"])

        wrong_digest = copy.deepcopy(self.evidence)
        wrong_digest[0]["artifact_sha256"] = "d" * 64
        wrong_digest[0]["evidence_sha256"] = evidence_digest(wrong_digest[0])
        wrong_digest[0]["verifier_hmac_sha256"] = evidence_hmac(
            wrong_digest[0], self.auth_keys["verifier-kernel"]
        )
        result = self.evaluate(evidence=wrong_digest)
        self.assertIn("artifact digest mismatch for check: kernel", result["reasons"])

    def test_missing_duplicate_and_unexpected_checks_reject(self) -> None:
        result = self.evaluate(evidence=self.evidence[:1])
        self.assertIn("missing required check: statement_fidelity", result["reasons"])

        duplicate = [*copy.deepcopy(self.evidence), copy.deepcopy(self.evidence[0])]
        duplicate[-1]["evidence_id"] = "ev-kernel-2"
        duplicate[-1]["evidence_sha256"] = evidence_digest(duplicate[-1])
        duplicate[-1]["verifier_hmac_sha256"] = evidence_hmac(
            duplicate[-1], self.auth_keys["verifier-kernel"]
        )
        result = self.evaluate(evidence=duplicate)
        self.assertIn("duplicate required check: kernel", result["reasons"])

        unexpected = copy.deepcopy(self.evidence)
        unexpected.append(
            with_auth(
                {
                    "evidence_id": "ev-scientific-score",
                    "check_id": "scientific_score",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "scientific-evaluator",
                    "outcome": "PASS",
                },
                b"unit-test-scientific-key",
            )
        )
        result = self.evaluate(evidence=unexpected)
        self.assertIn("unexpected check: scientific_score", result["reasons"])

    def test_duplicate_evidence_ids_reject(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
        evidence[1]["evidence_sha256"] = evidence_digest(evidence[1])
        evidence[1]["verifier_hmac_sha256"] = evidence_hmac(
            evidence[1], self.auth_keys["verifier-fidelity"]
        )
        result = self.evaluate(evidence=evidence)
        self.assertIn("duplicate evidence_id", result["reasons"])

    def test_input_order_does_not_change_decision(self) -> None:
        forward = self.evaluate()
        reverse = self.evaluate(evidence=reversed(self.evidence))
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
                    self.evaluate(product=product, evidence=evidence, policy=policy)

    def test_digest_and_authentication_formats_are_strict_lowercase_sha256(self) -> None:
        product = copy.deepcopy(self.product)
        product["artifact_sha256"] = "A" * 64
        with self.assertRaisesRegex(ValueError, "lowercase hex"):
            self.evaluate(product=product)

        evidence = copy.deepcopy(self.evidence)
        evidence[0]["evidence_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            self.evaluate(evidence=evidence)

        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_hmac_sha256"] = "NOT-A-MAC"
        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            self.evaluate(evidence=evidence)

        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            self.evaluate(trusted_policy_sha256="not-a-policy-digest")

    def test_policy_requires_closed_verifier_authority_for_every_check(self) -> None:
        missing = copy.deepcopy(self.policy)
        del missing["authorized_verifiers"]["kernel"]
        with self.assertRaisesRegex(
            ValueError,
            "authorized_verifiers keys must exactly match required_checks",
        ):
            self.evaluate(policy=missing)

        duplicate = copy.deepcopy(self.policy)
        duplicate["authorized_verifiers"]["kernel"] = [
            "verifier-kernel",
            "verifier-kernel",
        ]
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.evaluate(policy=duplicate)

        missing_commitment = copy.deepcopy(self.policy)
        del missing_commitment["verifier_key_sha256"]["verifier-kernel"]
        with self.assertRaisesRegex(
            ValueError,
            "verifier_key_sha256 keys must exactly match authorized verifier identities",
        ):
            self.evaluate(policy=missing_commitment)


if __name__ == "__main__":
    unittest.main()
