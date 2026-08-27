"""Deterministic product admission, intentionally separate from scientific scoring.

Admission answers only whether a product has the exact independent evidence required by
an admission policy. It does not decide whether the product is scientifically useful,
whether an experiment arm solved a problem, or how any result should be scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from string import hexdigits
from typing import Any, Iterable, Mapping


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


@dataclass(frozen=True)
class AdmissionPolicy:
    policy_id: str
    required_checks: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AdmissionPolicy":
        expected = {"policy_id", "required_checks"}
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
        return cls(policy_id=policy_id, required_checks=checks)


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
        if item.check_id not in required:
            reasons.add(f"unexpected check: {item.check_id}")
            continue
        by_check[item.check_id].append(item)
        if item.product_id != product.product_id:
            reasons.add(f"product_id mismatch for check: {item.check_id}")
        if item.artifact_sha256 != product.artifact_sha256:
            reasons.add(f"artifact digest mismatch for check: {item.check_id}")
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
