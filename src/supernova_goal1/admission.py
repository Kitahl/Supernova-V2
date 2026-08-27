"""Deterministic product admission, intentionally separate from scientific scoring.

Admission answers only whether a product has the exact independently authorized evidence
required by a trusted admission policy. It does not decide whether the product is
scientifically useful, whether an experiment arm solved a problem, or how any result
should be scored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from string import hexdigits
from typing import Any, Iterable, Mapping


_EVIDENCE_DIGEST_DOMAIN = "supernova_goal1.admission_evidence.v1"


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


def _canonical_evidence_sha256(
    *,
    evidence_id: str,
    check_id: str,
    product_id: str,
    artifact_sha256: str,
    verifier_id: str,
    outcome: EvidenceOutcome,
) -> str:
    """Hash the canonical evidence content, excluding the digest field itself."""

    payload = {
        "artifact_sha256": artifact_sha256,
        "check_id": check_id,
        "evidence_id": evidence_id,
        "outcome": outcome.value,
        "product_id": product_id,
        "schema": _EVIDENCE_DIGEST_DOMAIN,
        "verifier_id": verifier_id,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
        )

    def canonical_sha256(self) -> str:
        return _canonical_evidence_sha256(
            evidence_id=self.evidence_id,
            check_id=self.check_id,
            product_id=self.product_id,
            artifact_sha256=self.artifact_sha256,
            verifier_id=self.verifier_id,
            outcome=self.outcome,
        )


@dataclass(frozen=True)
class AdmissionPolicy:
    policy_id: str
    required_checks: tuple[str, ...]
    authorized_verifiers: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AdmissionPolicy":
        expected = {"policy_id", "required_checks", "authorized_verifiers"}
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

        return cls(
            policy_id=policy_id,
            required_checks=checks,
            authorized_verifiers=authorized,
        )


def evaluate_product_admission(
    product_raw: Mapping[str, Any],
    evidence_raw: Iterable[Mapping[str, Any]],
    policy_raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a deterministic evidence-policy decision with no scientific score."""

    product = ProductCandidate.from_mapping(product_raw)
    policy = AdmissionPolicy.from_mapping(policy_raw)
    evidence = [AdmissionEvidence.from_mapping(raw) for raw in evidence_raw]

    reasons: set[str] = set()
    required = set(policy.required_checks)
    by_check: dict[str, list[AdmissionEvidence]] = {
        check_id: [] for check_id in policy.required_checks
    }

    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        reasons.add("duplicate evidence_id")

    for item in evidence:
        if item.evidence_sha256 != item.canonical_sha256():
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
