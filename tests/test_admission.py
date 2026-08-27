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
PRODUCT_DOMAIN = "supernova_goal1.product_candidate.v1"
EVIDENCE_DOMAIN = "supernova_goal1.admission_evidence.v3"
POLICY_DOMAIN = "supernova_goal1.admission_policy.v2"

PRODUCER_AUTHORITY = "authority-producer"
KERNEL_AUTHORITY = "authority-kernel"
FIDELITY_AUTHORITY = "authority-fidelity"

AUTH_KEYS = {
    PRODUCER_AUTHORITY: b"unit-test-producer-authority-key-v2",
    KERNEL_AUTHORITY: b"unit-test-kernel-authority-key-v2",
    FIDELITY_AUTHORITY: b"unit-test-fidelity-authority-key-v2",
}


def canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_product(record: dict[str, object]) -> bytes:
    return canonical_json(
        {
            "artifact_sha256": record["artifact_sha256"],
            "producer_id": record["producer_id"],
            "product_id": record["product_id"],
            "schema": PRODUCT_DOMAIN,
        }
    )


def sign_product(record: dict[str, object], key: bytes) -> dict[str, object]:
    result = copy.deepcopy(record)
    result["producer_hmac_sha256"] = hmac.new(
        key, canonical_product(result), hashlib.sha256
    ).hexdigest()
    return result


def canonical_evidence(record: dict[str, object]) -> bytes:
    return canonical_json(
        {
            "artifact_sha256": record["artifact_sha256"],
            "check_id": record["check_id"],
            "evidence_id": record["evidence_id"],
            "outcome": record["outcome"],
            "policy_sha256": record["policy_sha256"],
            "product_id": record["product_id"],
            "schema": EVIDENCE_DOMAIN,
            "verifier_id": record["verifier_id"],
        }
    )


def sign_evidence(record: dict[str, object], key: bytes) -> dict[str, object]:
    result = copy.deepcopy(record)
    canonical = canonical_evidence(result)
    result["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    result["verifier_hmac_sha256"] = hmac.new(
        key, canonical, hashlib.sha256
    ).hexdigest()
    return result


def canonical_policy(policy: dict[str, object]) -> bytes:
    authorized = policy["authorized_verifiers"]
    producer_authorities = policy["producer_authorities"]
    verifier_authorities = policy["verifier_authorities"]
    commitments = policy["authority_key_sha256"]
    assert isinstance(authorized, dict)
    assert isinstance(producer_authorities, dict)
    assert isinstance(verifier_authorities, dict)
    assert isinstance(commitments, dict)
    return canonical_json(
        {
            "authorized_verifiers": {
                check_id: sorted(authorized[check_id])
                for check_id in sorted(authorized)
            },
            "authority_key_sha256": {
                authority_id: commitments[authority_id]
                for authority_id in sorted(commitments)
            },
            "policy_id": policy["policy_id"],
            "producer_authorities": {
                producer_id: producer_authorities[producer_id]
                for producer_id in sorted(producer_authorities)
            },
            "required_checks": sorted(policy["required_checks"]),
            "schema": POLICY_DOMAIN,
            "verifier_authorities": {
                verifier_id: verifier_authorities[verifier_id]
                for verifier_id in sorted(verifier_authorities)
            },
        }
    )


def policy_digest(policy: dict[str, object]) -> str:
    return hashlib.sha256(canonical_policy(policy)).hexdigest()


class ProductAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth_keys = dict(AUTH_KEYS)
        self.product = sign_product(
            {
                "product_id": "lemma-17",
                "producer_id": "producer-A",
                "artifact_sha256": ARTIFACT,
            },
            AUTH_KEYS[PRODUCER_AUTHORITY],
        )
        self.policy = {
            "policy_id": "verified-product-v2",
            "required_checks": ["kernel", "statement_fidelity"],
            "authorized_verifiers": {
                "kernel": ["verifier-kernel"],
                "statement_fidelity": ["verifier-fidelity"],
            },
            "producer_authorities": {"producer-A": PRODUCER_AUTHORITY},
            "verifier_authorities": {
                "verifier-kernel": KERNEL_AUTHORITY,
                "verifier-fidelity": FIDELITY_AUTHORITY,
            },
            "authority_key_sha256": {
                authority_id: hashlib.sha256(key).hexdigest()
                for authority_id, key in AUTH_KEYS.items()
            },
        }
        self.trusted_policy_sha256 = policy_digest(self.policy)
        self.evidence = [
            sign_evidence(
                {
                    "evidence_id": "ev-kernel",
                    "check_id": "kernel",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-kernel",
                    "outcome": "PASS",
                    "policy_sha256": self.trusted_policy_sha256,
                },
                AUTH_KEYS[KERNEL_AUTHORITY],
            ),
            sign_evidence(
                {
                    "evidence_id": "ev-fidelity",
                    "check_id": "statement_fidelity",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-fidelity",
                    "outcome": "PASS",
                    "policy_sha256": self.trusted_policy_sha256,
                },
                AUTH_KEYS[FIDELITY_AUTHORITY],
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
            authority_auth_keys=self.auth_keys if auth_keys is None else auth_keys,
        )

    def test_complete_independent_pass_evidence_admits(self) -> None:
        result = self.evaluate()
        self.assertEqual("ADMITTED", result["admission"])
        self.assertTrue(result["admitted"])
        self.assertEqual(["ev-fidelity", "ev-kernel"], result["evidence_ids"])
        self.assertEqual(self.trusted_policy_sha256, result["policy_sha256"])
        self.assertEqual([], result["reasons"])
        self.assertNotIn("score", result)

    def test_forged_digest_rejects(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["evidence_sha256"] = "f" * 64
        result = self.evaluate(evidence=evidence)
        self.assertIn("evidence digest mismatch: ev-kernel", result["reasons"])

    def test_tampered_content_rejects_without_recomputed_digest_or_auth(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["evidence_id"] = "ev-kernel-tampered"
        result = self.evaluate(evidence=evidence)
        self.assertIn("evidence digest mismatch: ev-kernel-tampered", result["reasons"])
        self.assertIn(
            "authority authentication failed: evidence=ev-kernel-tampered",
            result["reasons"],
        )

    def test_evidence_is_bound_to_exact_trusted_policy_digest(self) -> None:
        new_key = b"unit-test-kernel-secondary-key-v2"
        new_authority = "authority-kernel-secondary"
        new_policy = copy.deepcopy(self.policy)
        new_policy["authorized_verifiers"]["kernel"].append("verifier-kernel-secondary")
        new_policy["verifier_authorities"]["verifier-kernel-secondary"] = new_authority
        new_policy["authority_key_sha256"][new_authority] = hashlib.sha256(new_key).hexdigest()
        new_policy_sha256 = policy_digest(new_policy)
        keys = dict(self.auth_keys)
        keys[new_authority] = new_key

        # Old evidence is still content-valid and signed by an authority that remains
        # authorized, but it attests to the old policy and must not be replayable.
        result = self.evaluate(
            policy=new_policy,
            auth_keys=keys,
            trusted_policy_sha256=new_policy_sha256,
        )
        self.assertIn("evidence policy digest mismatch: ev-kernel", result["reasons"])
        self.assertIn("evidence policy digest mismatch: ev-fidelity", result["reasons"])
        self.assertEqual("REJECTED", result["admission"])

    def test_policy_digest_field_is_covered_by_digest_and_verifier_auth(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["policy_sha256"] = "b" * 64
        result = self.evaluate(evidence=evidence)
        self.assertIn("evidence digest mismatch: ev-kernel", result["reasons"])
        self.assertIn("evidence policy digest mismatch: ev-kernel", result["reasons"])
        self.assertIn(
            "authority authentication failed: evidence=ev-kernel", result["reasons"]
        )

    def test_fake_verifier_identity_rejects_even_with_attacker_digest_and_hmac(self) -> None:
        attacker_key = b"unit-test-attacker-authority-key-v2"
        evidence = copy.deepcopy(self.evidence)
        evidence[0] = sign_evidence(
            {
                "evidence_id": "ev-kernel",
                "check_id": "kernel",
                "product_id": "lemma-17",
                "artifact_sha256": ARTIFACT,
                "verifier_id": "verifier-pretender",
                "outcome": "PASS",
                "policy_sha256": self.trusted_policy_sha256,
            },
            attacker_key,
        )
        result = self.evaluate(evidence=evidence)
        self.assertIn(
            "unauthorized verifier for check: kernel=verifier-pretender",
            result["reasons"],
        )

    def test_authorized_name_impersonation_rejects_with_wrong_key(self) -> None:
        attacker_key = b"unit-test-attacker-authority-key-v2"
        evidence = copy.deepcopy(self.evidence)
        evidence[0] = sign_evidence(evidence[0], attacker_key)
        result = self.evaluate(evidence=evidence)
        self.assertIn(
            "authority authentication failed: evidence=ev-kernel", result["reasons"]
        )

    def test_runtime_key_substitution_rejects_against_policy_commitment(self) -> None:
        attacker_key = b"unit-test-attacker-authority-key-v2"
        evidence = copy.deepcopy(self.evidence)
        evidence[0] = sign_evidence(evidence[0], attacker_key)
        keys = dict(self.auth_keys)
        keys[KERNEL_AUTHORITY] = attacker_key
        result = self.evaluate(evidence=evidence, auth_keys=keys)
        self.assertIn(
            f"authority key commitment mismatch: {KERNEL_AUTHORITY}", result["reasons"]
        )

    def test_missing_runtime_authority_key_rejects(self) -> None:
        keys = dict(self.auth_keys)
        del keys[KERNEL_AUTHORITY]
        result = self.evaluate(auth_keys=keys)
        self.assertIn(
            f"missing authority authentication key: {KERNEL_AUTHORITY}", result["reasons"]
        )

    def test_short_runtime_authority_key_rejects(self) -> None:
        keys = dict(self.auth_keys)
        keys[KERNEL_AUTHORITY] = b"k" * 16
        result = self.evaluate(auth_keys=keys)
        self.assertIn(
            f"authority authentication key too short: {KERNEL_AUTHORITY}", result["reasons"]
        )

    def test_producer_provenance_is_authenticated(self) -> None:
        product = copy.deepcopy(self.product)
        product["producer_hmac_sha256"] = "0" * 64
        result = self.evaluate(product=product)
        self.assertIn(
            "authority authentication failed: producer=producer-A", result["reasons"]
        )

    def test_unauthorized_producer_identity_rejects(self) -> None:
        product = sign_product(
            {
                "product_id": "lemma-17",
                "producer_id": "producer-pretender",
                "artifact_sha256": ARTIFACT,
            },
            AUTH_KEYS[PRODUCER_AUTHORITY],
        )
        result = self.evaluate(product=product)
        self.assertIn("unauthorized producer identity: producer-pretender", result["reasons"])

    def test_producer_authority_cannot_verify_its_own_product(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["verifier_authorities"]["verifier-kernel"] = PRODUCER_AUTHORITY
        del policy["authority_key_sha256"][KERNEL_AUTHORITY]
        policy_sha256 = policy_digest(policy)

        evidence = copy.deepcopy(self.evidence)
        for index, item in enumerate(evidence):
            item["policy_sha256"] = policy_sha256
            key = (
                AUTH_KEYS[PRODUCER_AUTHORITY]
                if item["check_id"] == "kernel"
                else AUTH_KEYS[FIDELITY_AUTHORITY]
            )
            evidence[index] = sign_evidence(item, key)
        keys = dict(self.auth_keys)
        del keys[KERNEL_AUTHORITY]

        result = self.evaluate(
            evidence=evidence,
            policy=policy,
            auth_keys=keys,
            trusted_policy_sha256=policy_sha256,
        )
        self.assertIn(
            "producer authority cannot verify its own product: kernel", result["reasons"]
        )

    def test_distinct_authority_names_cannot_share_same_hmac_secret(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["authority_key_sha256"][KERNEL_AUTHORITY] = policy[
            "authority_key_sha256"
        ][PRODUCER_AUTHORITY]
        with self.assertRaisesRegex(
            ValueError, "must not share authentication key commitments"
        ):
            self.evaluate(policy=policy, trusted_policy_sha256=policy_digest(policy))

    def test_forged_policy_authority_rejects_against_trusted_policy_digest(self) -> None:
        attacker_key = b"unit-test-attacker-authority-key-v2"
        attacker_authority = "authority-attacker"
        policy = copy.deepcopy(self.policy)
        policy["authorized_verifiers"]["kernel"] = ["verifier-pretender"]
        del policy["verifier_authorities"]["verifier-kernel"]
        policy["verifier_authorities"]["verifier-pretender"] = attacker_authority
        del policy["authority_key_sha256"][KERNEL_AUTHORITY]
        policy["authority_key_sha256"][attacker_authority] = hashlib.sha256(
            attacker_key
        ).hexdigest()
        forged_policy_sha256 = policy_digest(policy)

        evidence = copy.deepcopy(self.evidence)
        evidence[0] = sign_evidence(
            {
                "evidence_id": "ev-kernel",
                "check_id": "kernel",
                "product_id": "lemma-17",
                "artifact_sha256": ARTIFACT,
                "verifier_id": "verifier-pretender",
                "outcome": "PASS",
                "policy_sha256": forged_policy_sha256,
            },
            attacker_key,
        )
        # Make the other evidence internally consistent with the forged policy so the
        # trusted root mismatch is the decisive reason in the first evaluation.
        evidence[1]["policy_sha256"] = forged_policy_sha256
        evidence[1] = sign_evidence(evidence[1], AUTH_KEYS[FIDELITY_AUTHORITY])
        keys = dict(self.auth_keys)
        del keys[KERNEL_AUTHORITY]
        keys[attacker_authority] = attacker_key

        result = self.evaluate(evidence=evidence, policy=policy, auth_keys=keys)
        self.assertIn("admission policy digest mismatch", result["reasons"])

        result_with_forged_root = self.evaluate(
            evidence=evidence,
            policy=policy,
            auth_keys=keys,
            trusted_policy_sha256=forged_policy_sha256,
        )
        self.assertEqual("ADMITTED", result_with_forged_root["admission"])

    def test_policy_digest_is_semantic_order_invariant(self) -> None:
        reordered = copy.deepcopy(self.policy)
        reordered["required_checks"] = list(reversed(reordered["required_checks"]))
        reordered["authorized_verifiers"] = {
            "statement_fidelity": ["verifier-fidelity"],
            "kernel": ["verifier-kernel"],
        }
        reordered["producer_authorities"] = dict(
            reversed(list(reordered["producer_authorities"].items()))
        )
        reordered["verifier_authorities"] = dict(
            reversed(list(reordered["verifier_authorities"].items()))
        )
        reordered["authority_key_sha256"] = dict(
            reversed(list(reordered["authority_key_sha256"].items()))
        )
        self.assertEqual(self.trusted_policy_sha256, policy_digest(reordered))
        self.assertEqual("ADMITTED", self.evaluate(policy=reordered)["admission"])

    def test_non_pass_evidence_rejects(self) -> None:
        for outcome in ("FAIL", "TIMEOUT", "ERROR"):
            with self.subTest(outcome=outcome):
                evidence = copy.deepcopy(self.evidence)
                evidence[0]["outcome"] = outcome
                evidence[0] = sign_evidence(evidence[0], AUTH_KEYS[KERNEL_AUTHORITY])
                result = self.evaluate(evidence=evidence)
                self.assertIn(f"check did not PASS: kernel={outcome}", result["reasons"])

    def test_subject_identity_and_artifact_digest_are_bound(self) -> None:
        wrong_product = copy.deepcopy(self.evidence)
        wrong_product[0]["product_id"] = "another-product"
        wrong_product[0] = sign_evidence(wrong_product[0], AUTH_KEYS[KERNEL_AUTHORITY])
        result = self.evaluate(evidence=wrong_product)
        self.assertIn("product_id mismatch for check: kernel", result["reasons"])

        wrong_digest = copy.deepcopy(self.evidence)
        wrong_digest[0]["artifact_sha256"] = "d" * 64
        wrong_digest[0] = sign_evidence(wrong_digest[0], AUTH_KEYS[KERNEL_AUTHORITY])
        result = self.evaluate(evidence=wrong_digest)
        self.assertIn("artifact digest mismatch for check: kernel", result["reasons"])

    def test_missing_duplicate_and_unexpected_checks_reject(self) -> None:
        result = self.evaluate(evidence=self.evidence[:1])
        self.assertIn("missing required check: statement_fidelity", result["reasons"])

        duplicate = [*copy.deepcopy(self.evidence), copy.deepcopy(self.evidence[0])]
        duplicate[-1]["evidence_id"] = "ev-kernel-2"
        duplicate[-1] = sign_evidence(duplicate[-1], AUTH_KEYS[KERNEL_AUTHORITY])
        result = self.evaluate(evidence=duplicate)
        self.assertIn("duplicate required check: kernel", result["reasons"])

        unexpected = copy.deepcopy(self.evidence)
        unexpected.append(
            sign_evidence(
                {
                    "evidence_id": "ev-scientific-score",
                    "check_id": "scientific_score",
                    "product_id": "lemma-17",
                    "artifact_sha256": ARTIFACT,
                    "verifier_id": "verifier-fidelity",
                    "outcome": "PASS",
                    "policy_sha256": self.trusted_policy_sha256,
                },
                AUTH_KEYS[FIDELITY_AUTHORITY],
            )
        )
        result = self.evaluate(evidence=unexpected)
        self.assertIn("unexpected check: scientific_score", result["reasons"])

    def test_duplicate_evidence_ids_reject(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[1]["evidence_id"] = evidence[0]["evidence_id"]
        evidence[1] = sign_evidence(evidence[1], AUTH_KEYS[FIDELITY_AUTHORITY])
        result = self.evaluate(evidence=evidence)
        self.assertIn("duplicate evidence_id", result["reasons"])

    def test_input_order_does_not_change_decision(self) -> None:
        forward = self.evaluate()
        reverse = self.evaluate(evidence=reversed(self.evidence))
        self.assertEqual(forward, reverse)

    def test_scientific_scoring_fields_are_not_admission_inputs(self) -> None:
        for target, field in (
            ("product", "score"),
            ("policy", "familywise_alpha"),
            ("evidence", "scientific_score"),
        ):
            with self.subTest(field=field):
                product = copy.deepcopy(self.product)
                policy = copy.deepcopy(self.policy)
                evidence = copy.deepcopy(self.evidence)
                if target == "product":
                    product[field] = 1.0
                elif target == "policy":
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
        evidence[0]["evidence_sha256"] = "0" * 63
        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            self.evaluate(evidence=evidence)

        policy = copy.deepcopy(self.policy)
        policy["authority_key_sha256"][KERNEL_AUTHORITY] = "F" * 64
        with self.assertRaisesRegex(ValueError, "lowercase hex"):
            self.evaluate(policy=policy, trusted_policy_sha256=policy_digest(policy))

    def test_identifier_whitespace_is_rejected(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence[0]["verifier_id"] = " verifier-kernel"
        with self.assertRaisesRegex(ValueError, "leading or trailing whitespace"):
            self.evaluate(evidence=evidence)

    def test_policy_authority_maps_are_closed(self) -> None:
        missing = copy.deepcopy(self.policy)
        del missing["verifier_authorities"]["verifier-kernel"]
        with self.assertRaisesRegex(
            ValueError, "keys must exactly match authorized verifier identities"
        ):
            self.evaluate(policy=missing, trusted_policy_sha256=policy_digest(missing))

        extra_commitment = copy.deepcopy(self.policy)
        extra_commitment["authority_key_sha256"]["authority-unused"] = "b" * 64
        with self.assertRaisesRegex(
            ValueError, "keys must exactly match referenced authority identities"
        ):
            self.evaluate(
                policy=extra_commitment,
                trusted_policy_sha256=policy_digest(extra_commitment),
            )


if __name__ == "__main__":
    unittest.main()
