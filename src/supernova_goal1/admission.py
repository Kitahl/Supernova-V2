"""Deterministic product admission, intentionally separate from scientific scoring.

Admission answers only whether a product has the exact independently authorized evidence
required by a trusted admission policy. It does not decide whether the product is
scientifically useful, whether an experiment arm solved a problem, or how any result
should be scored.

Verifier authentication keys and the trusted policy digest are runtime-only authority
capabilities. They must be provisioned independently of the product producer and must
never be persisted as part of admission evidence. The serialized policy is verified
against that trusted digest before it can authorize any verifier.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum
from string import hexdigits
from typing import Any, Iterable, Mapping


_EVIDENCE_DIGEST_DOMAIN = "supernova_goal1.admission_evidence.v1"
_POLICY_DIGEST_DOMAIN = "supernova_goal1.admission_policy.v1"


class EvidenceOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class AdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"


def _exact_fields(raw: Mapping[str, Any], expected: set[str], prefix: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{prefix} fields must be exactly {sorted(expected)}")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    value = _identifier(value, field)
    if len(value) != 64 or any(character not in hexdigits for character in value):
        raise ValueError(f"{field} must be a 64-character SHA-256 hex digest")
    if value != value.lower():
        raise ValueError(f"{field} must use lowercase hex")
    return value


def _canonical_evidence_bytes(
    *,
    evidence_id: str,
    check_id: str,
    product_id: str,
    artifact_sha256: str,
    verifier_id: str,
    outcome: EvidenceOutcome,
) -> bytes:
    payload = {
        "artifact_sha256": artifact_sha256,
        "check_id": check_id,
        "evidence_id": evidence_id,
        "outcome": outcome.value,
        "product_id": product_id,
        "schema": _EVIDENCE_DIGEST_DOMAIN,
        "verifier_id": verifier_id,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _canonical_policy_bytes(
    *,
    policy_id: str,
    required_checks: tuple[str, ...],
    authorized_verifiers: Mapping[str, tuple[str, ...]],
    verifier_key_sha256: Mapping[str, str],
) -> bytes:
    """Return a semantic-normalized policy representation for trust-root binding.

    Check and verifier list order are not admission semantics, so they are sorted before
    hashing. Key commitments are part of the policy root because changing the verifier
    key for an otherwise identical identity changes who is trusted to attest.
    """

    payload = {
        "authorized_verifiers": {
            check_id: sorted(authorized_verifiers[check_id])
            for check_id in sorted(authorized_verifiers)
        },
        "policy_id": policy_id,
        "required_checks": sorted(required_checks),
        "schema": _POLICY_DIGEST_DOMAIN,
        "verifier_key_sha256": {
            verifier_id: verifier_key_sha256[verifier_id]
            for verifier_id in sorted(verifier_key_sha256)
        },
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class ProductCandidate:
    product_id: str
    producer_id: str
    artifact_sha256: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductCandidate":
        expected = {"product_id", "producer_id", "artifact_sha256"}
        _exact_fields(raw, expected, "product")
        return cls(
            product_id=_identifier(raw["product_id"], "product.product_id"),
            producer_id=_identifier(raw["producer_id"], "product.producer_id"),
            artifact_sha256=_sha256(raw["artifact_sha256"], "product.artifact_sha256"),
        )


@dataclass(frozen=True)
class AdmissionEvidence:
    evidence_id: str
    check_id: str
    product_id: str
    artifact_sha256: str
    verifier_id: str
    outcome: EvidenceOutcome
    evidence_sha256: str
    verifier_hmac_sha256: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AdmissionEvidence":
        expected = {
            "evidence_id",
            "check_id",
            "product_id",
            "artifact_sha256",
            "verifier_id",
            "outcome",
            "evidence_sha256",
            "verifier_hmac_sha256",
        }
        _exact_fields(raw, expected, "evidence")
        try:
            outcome = EvidenceOutcome(raw["outcome"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown evidence outcome: {raw.get('outcome')!r}") from exc
        return cls(
            evidence_id=_identifier(raw["evidence_id"], "evidence.evidence_id"),
            check_id=_identifier(raw["check_id"], "evidence.check_id"),
            product_id=_identifier(raw["product_id"], "evidence.product_id"),
            artifact_sha256=_sha256(raw["artifact_sha256"], "evidence.artifact_sha256"),
            verifier_id=_identifier(raw["verifier_id"], "evidence.verifier_id"),
            outcome=outcome,
            evidence_sha256=_sha256(raw["evidence_sha256"], "evidence.evidence_sha256"),
            verifier_hmac_sha256=_sha256(
                raw["verifier_hmac_sha256"], "evidence.verifier_hmac_sha256"
            ),
        )

    def canonical_bytes(self) -> bytes:
        return _canonical_evidence_bytes(
            evidence_id=self.evidence_id,
            check_id=self.check_id,
            product_id=self.product_id,
            artifact_sha256=self.artifact_sha256,
            verifier_id=self.verifier_id,
            outcome=self.outcome,
        )

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class AdmissionPolicy:
    policy_id: str
    required_checks: tuple[str, ...]
    authorized_verifiers: Mapping[str, tuple[str, ...]]
    verifier_key_sha256: Mapping[str, str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AdmissionPolicy":
        expected = {
            "policy_id",
            "required_checks",
            "authorized_verifiers",
            "verifier_key_sha256",
        }
        _exact_fields(raw, expected, "policy")
        policy_id = _identifier(raw["policy_id"], "policy.policy_id")
        required = raw["required_checks"]
        if not isinstance(required, list) or not required:
            raise ValueError("policy.required_checks must be a non-empty list")
        checks = tuple(
            _identifier(check, "policy.required_checks[]") for check in required
        )
        if len(set(checks)) != len(checks):
            raise ValueError("policy.required_checks must be unique")

        verifier_map = raw["authorized_verifiers"]
        if not isinstance(verifier_map, Mapping):
            raise ValueError("policy.authorized_verifiers must be a mapping")
        if set(verifier_map) != set(checks):
            raise ValueError(
                "policy.authorized_verifiers keys must exactly match required_checks"
            )

        authorized: dict[str, tuple[str, ...]] = {}
        authorized_identities: set[str] = set()
        for check_id in checks:
            verifier_ids = verifier_map[check_id]
            if not isinstance(verifier_ids, list) or not verifier_ids:
                raise ValueError(
                    f"policy.authorized_verifiers[{check_id!r}] must be a non-empty list"
                )
            parsed = tuple(
                _identifier(
                    verifier_id,
                    f"policy.authorized_verifiers[{check_id!r}][]",
                )
                for verifier_id in verifier_ids
            )
            if len(set(parsed)) != len(parsed):
                raise ValueError(
                    f"policy.authorized_verifiers[{check_id!r}] must be unique"
                )
            authorized[check_id] = parsed
            authorized_identities.update(parsed)

        key_commitments = raw["verifier_key_sha256"]
        if not isinstance(key_commitments, Mapping):
            raise ValueError("policy.verifier_key_sha256 must be a mapping")
        if set(key_commitments) != authorized_identities:
            raise ValueError(
                "policy.verifier_key_sha256 keys must exactly match authorized verifier identities"
            )
        parsed_commitments = {
            verifier_id: _sha256(
                key_commitments[verifier_id],
                f"policy.verifier_key_sha256[{verifier_id!r}]",
            )
            for verifier_id in sorted(authorized_identities)
        }

        return cls(
            policy_id=policy_id,
            required_checks=checks,
            authorized_verifiers=authorized,
            verifier_key_sha256=parsed_commitments,
        )

    def canonical_bytes(self) -> bytes:
        return _canonical_policy_bytes(
            policy_id=self.policy_id,
            required_checks=self.required_checks,
            authorized_verifiers=self.authorized_verifiers,
            verifier_key_sha256=self.verifier_key_sha256,
        )

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def evaluate_product_admission(
    product_raw: Mapping[str, Any],
    evidence_raw: Iterable[Mapping[str, Any]],
    policy_raw: Mapping[str, Any],
    *,
    trusted_policy_sha256: str,
    verifier_auth_keys: Mapping[str, bytes],
) -> dict[str, Any]:
    """Return a deterministic evidence-policy decision with no scientific score.

    ``policy_raw`` is serialized policy content, not a trust root. The admission boundary
    must supply ``trusted_policy_sha256`` from independently provisioned configuration;
    the parsed policy must match that digest before it can authorize a verifier.

    ``verifier_auth_keys`` is runtime trust material owned by the admission boundary,
    not caller-authored evidence. The policy stores only SHA-256 commitments to those
    keys. An evidence record is independently attributable only when its HMAC verifies
    under a runtime key whose commitment is authorized by the trusted policy.
    """

    trusted_policy_sha256 = _sha256(
        trusted_policy_sha256, "trusted_policy_sha256"
    )
    if not isinstance(verifier_auth_keys, Mapping):
        raise ValueError("verifier_auth_keys must be a mapping")

    product = ProductCandidate.from_mapping(product_raw)
    policy = AdmissionPolicy.from_mapping(policy_raw)
    evidence = [AdmissionEvidence.from_mapping(raw) for raw in evidence_raw]

    reasons: set[str] = set()
    if not hmac.compare_digest(policy.canonical_sha256(), trusted_policy_sha256):
        reasons.add("admission policy digest mismatch")

    required = set(policy.required_checks)
    by_check: dict[str, list[AdmissionEvidence]] = {
        check_id: [] for check_id in policy.required_checks
    }

    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        reasons.add("duplicate evidence_id")

    for item in evidence:
        canonical_bytes = item.canonical_bytes()
        expected_digest = hashlib.sha256(canonical_bytes).hexdigest()
        if not hmac.compare_digest(item.evidence_sha256, expected_digest):
            reasons.add(f"evidence digest mismatch: {item.evidence_id}")

        if item.check_id not in required:
            reasons.add(f"unexpected check: {item.check_id}")
            continue

        by_check[item.check_id].append(item)
        if item.product_id != product.product_id:
            reasons.add(f"product_id mismatch for check: {item.check_id}")
        if item.artifact_sha256 != product.artifact_sha256:
            reasons.add(f"artifact digest mismatch for check: {item.check_id}")

        authorized = policy.authorized_verifiers[item.check_id]
        if item.verifier_id not in authorized:
            reasons.add(
                f"unauthorized verifier for check: {item.check_id}={item.verifier_id}"
            )
        else:
            key = verifier_auth_keys.get(item.verifier_id)
            if key is None:
                reasons.add(f"missing verifier authentication key: {item.verifier_id}")
            elif not isinstance(key, bytes) or not key:
                reasons.add(f"invalid verifier authentication key: {item.verifier_id}")
            else:
                expected_commitment = policy.verifier_key_sha256[item.verifier_id]
                actual_commitment = hashlib.sha256(key).hexdigest()
                if not hmac.compare_digest(actual_commitment, expected_commitment):
                    reasons.add(f"verifier key commitment mismatch: {item.verifier_id}")
                else:
                    expected_proof = hmac.new(
                        key, canonical_bytes, hashlib.sha256
                    ).hexdigest()
                    if not hmac.compare_digest(
                        item.verifier_hmac_sha256, expected_proof
                    ):
                        reasons.add(
                            f"verifier authentication failed: {item.evidence_id}"
                        )

        if item.verifier_id == product.producer_id:
            reasons.add(f"producer cannot verify its own product: {item.check_id}")
        if item.outcome is not EvidenceOutcome.PASS:
            reasons.add(f"check did not PASS: {item.check_id}={item.outcome.value}")

    for check_id in policy.required_checks:
        count = len(by_check[check_id])
        if count == 0:
            reasons.add(f"missing required check: {check_id}")
        elif count > 1:
            reasons.add(f"duplicate required check: {check_id}")

    admitted = not reasons
    return {
        "product_id": product.product_id,
        "policy_id": policy.policy_id,
        "admission": (
            AdmissionStatus.ADMITTED.value
            if admitted
            else AdmissionStatus.REJECTED.value
        ),
        "admitted": admitted,
        "evidence_ids": sorted(set(evidence_ids)) if admitted else [],
        "reasons": sorted(reasons),
    }
