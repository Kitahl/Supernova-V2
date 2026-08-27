"""Deterministic product admission, intentionally separate from scientific scoring.

Admission answers only whether a product has the exact independently authorized evidence
required by a trusted admission policy. It does not decide scientific utility, experiment
success, or any scientific score.

The bounded module models authority explicitly. Producer and verifier identities are
mapped by trusted policy to authority identities, and both product provenance and
verification evidence are authenticated against runtime-only authority material. Distinct
authority identities must also have distinct key commitments: with shared-secret HMAC,
two names backed by the same secret are not independent authorities.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from string import hexdigits
from typing import Any


_PRODUCT_AUTH_DOMAIN = "supernova_goal1.product_candidate.v1"
_EVIDENCE_DIGEST_DOMAIN = "supernova_goal1.admission_evidence.v2"
_POLICY_DIGEST_DOMAIN = "supernova_goal1.admission_policy.v2"


class EvidenceOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class AdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"


def _mapping(raw: Any, prefix: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix} must be a mapping")
    return raw


def _exact_fields(raw: Mapping[str, Any], expected: set[str], prefix: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{prefix} fields must be exactly {sorted(expected)}")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field} must not contain leading or trailing whitespace")
    return value


def _sha256(value: Any, field: str) -> str:
    value = _identifier(value, field)
    if len(value) != 64 or any(character not in hexdigits for character in value):
        raise ValueError(f"{field} must be a 64-character SHA-256 hex digest")
    if value != value.lower():
        raise ValueError(f"{field} must use lowercase hex")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _canonical_product_bytes(
    *, product_id: str, producer_id: str, artifact_sha256: str
) -> bytes:
    return _canonical_json(
        {
            "artifact_sha256": artifact_sha256,
            "producer_id": producer_id,
            "product_id": product_id,
            "schema": _PRODUCT_AUTH_DOMAIN,
        }
    )


def _canonical_evidence_bytes(
    *,
    evidence_id: str,
    check_id: str,
    product_id: str,
    artifact_sha256: str,
    verifier_id: str,
    outcome: EvidenceOutcome,
) -> bytes:
    return _canonical_json(
        {
            "artifact_sha256": artifact_sha256,
            "check_id": check_id,
            "evidence_id": evidence_id,
            "outcome": outcome.value,
            "product_id": product_id,
            "schema": _EVIDENCE_DIGEST_DOMAIN,
            "verifier_id": verifier_id,
        }
    )


def _canonical_policy_bytes(
    *,
    policy_id: str,
    required_checks: tuple[str, ...],
    authorized_verifiers: Mapping[str, tuple[str, ...]],
    producer_authorities: Mapping[str, str],
    verifier_authorities: Mapping[str, str],
    authority_key_sha256: Mapping[str, str],
) -> bytes:
    """Return a semantic-normalized policy representation for trust-root binding."""

    return _canonical_json(
        {
            "authorized_verifiers": {
                check_id: sorted(authorized_verifiers[check_id])
                for check_id in sorted(authorized_verifiers)
            },
            "authority_key_sha256": {
                authority_id: authority_key_sha256[authority_id]
                for authority_id in sorted(authority_key_sha256)
            },
            "policy_id": policy_id,
            "producer_authorities": {
                producer_id: producer_authorities[producer_id]
                for producer_id in sorted(producer_authorities)
            },
            "required_checks": sorted(required_checks),
            "schema": _POLICY_DIGEST_DOMAIN,
            "verifier_authorities": {
                verifier_id: verifier_authorities[verifier_id]
                for verifier_id in sorted(verifier_authorities)
            },
        }
    )


@dataclass(frozen=True)
class ProductCandidate:
    product_id: str
    producer_id: str
    artifact_sha256: str
    producer_hmac_sha256: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductCandidate":
        raw = _mapping(raw, "product")
        expected = {
            "product_id",
            "producer_id",
            "artifact_sha256",
            "producer_hmac_sha256",
        }
        _exact_fields(raw, expected, "product")
        return cls(
            product_id=_identifier(raw["product_id"], "product.product_id"),
            producer_id=_identifier(raw["producer_id"], "product.producer_id"),
            artifact_sha256=_sha256(raw["artifact_sha256"], "product.artifact_sha256"),
            producer_hmac_sha256=_sha256(
                raw["producer_hmac_sha256"], "product.producer_hmac_sha256"
            ),
        )

    def canonical_bytes(self) -> bytes:
        return _canonical_product_bytes(
            product_id=self.product_id,
            producer_id=self.producer_id,
            artifact_sha256=self.artifact_sha256,
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
        raw = _mapping(raw, "evidence")
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
    producer_authorities: Mapping[str, str]
    verifier_authorities: Mapping[str, str]
    authority_key_sha256: Mapping[str, str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AdmissionPolicy":
        raw = _mapping(raw, "policy")
        expected = {
            "policy_id",
            "required_checks",
            "authorized_verifiers",
            "producer_authorities",
            "verifier_authorities",
            "authority_key_sha256",
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

        verifier_map = _mapping(raw["authorized_verifiers"], "policy.authorized_verifiers")
        if set(verifier_map) != set(checks):
            raise ValueError(
                "policy.authorized_verifiers keys must exactly match required_checks"
            )

        authorized: dict[str, tuple[str, ...]] = {}
        authorized_verifier_ids: set[str] = set()
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
            authorized_verifier_ids.update(parsed)

        producer_map = _mapping(raw["producer_authorities"], "policy.producer_authorities")
        if not producer_map:
            raise ValueError("policy.producer_authorities must be a non-empty mapping")
        producer_authorities = {
            _identifier(producer_id, "policy.producer_authorities key"): _identifier(
                authority_id,
                f"policy.producer_authorities[{producer_id!r}]",
            )
            for producer_id, authority_id in producer_map.items()
        }

        verifier_authority_map = _mapping(
            raw["verifier_authorities"], "policy.verifier_authorities"
        )
        if set(verifier_authority_map) != authorized_verifier_ids:
            raise ValueError(
                "policy.verifier_authorities keys must exactly match authorized verifier identities"
            )
        verifier_authorities = {
            verifier_id: _identifier(
                verifier_authority_map[verifier_id],
                f"policy.verifier_authorities[{verifier_id!r}]",
            )
            for verifier_id in sorted(authorized_verifier_ids)
        }

        overlapping_ids = set(producer_authorities).intersection(verifier_authorities)
        for identity in overlapping_ids:
            if producer_authorities[identity] != verifier_authorities[identity]:
                raise ValueError(
                    f"identity {identity!r} cannot map to different producer and verifier authorities"
                )

        authority_ids = set(producer_authorities.values()) | set(
            verifier_authorities.values()
        )
        key_commitments = _mapping(
            raw["authority_key_sha256"], "policy.authority_key_sha256"
        )
        if set(key_commitments) != authority_ids:
            raise ValueError(
                "policy.authority_key_sha256 keys must exactly match referenced authority identities"
            )
        parsed_commitments = {
            authority_id: _sha256(
                key_commitments[authority_id],
                f"policy.authority_key_sha256[{authority_id!r}]",
            )
            for authority_id in sorted(authority_ids)
        }

        commitment_owners: dict[str, str] = {}
        for authority_id, commitment in parsed_commitments.items():
            prior = commitment_owners.get(commitment)
            if prior is not None and prior != authority_id:
                raise ValueError(
                    "policy authority identities must not share authentication key commitments"
                )
            commitment_owners[commitment] = authority_id

        return cls(
            policy_id=policy_id,
            required_checks=checks,
            authorized_verifiers=authorized,
            producer_authorities=producer_authorities,
            verifier_authorities=verifier_authorities,
            authority_key_sha256=parsed_commitments,
        )

    def canonical_bytes(self) -> bytes:
        return _canonical_policy_bytes(
            policy_id=self.policy_id,
            required_checks=self.required_checks,
            authorized_verifiers=self.authorized_verifiers,
            producer_authorities=self.producer_authorities,
            verifier_authorities=self.verifier_authorities,
            authority_key_sha256=self.authority_key_sha256,
        )

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _authenticate_authority(
    *,
    authority_id: str,
    canonical_bytes: bytes,
    proof_sha256: str,
    policy: AdmissionPolicy,
    authority_auth_keys: Mapping[str, bytes],
    label: str,
    reasons: set[str],
) -> None:
    key = authority_auth_keys.get(authority_id)
    if key is None:
        reasons.add(f"missing authority authentication key: {authority_id}")
        return
    if not isinstance(key, bytes) or not key:
        reasons.add(f"invalid authority authentication key: {authority_id}")
        return

    expected_commitment = policy.authority_key_sha256[authority_id]
    actual_commitment = hashlib.sha256(key).hexdigest()
    if not hmac.compare_digest(actual_commitment, expected_commitment):
        reasons.add(f"authority key commitment mismatch: {authority_id}")
        return

    expected_proof = hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof_sha256, expected_proof):
        reasons.add(f"authority authentication failed: {label}")


def evaluate_product_admission(
    product_raw: Mapping[str, Any],
    evidence_raw: Iterable[Mapping[str, Any]],
    policy_raw: Mapping[str, Any],
    *,
    trusted_policy_sha256: str,
    authority_auth_keys: Mapping[str, bytes],
) -> dict[str, Any]:
    """Return a deterministic evidence-policy decision with no scientific score.

    ``policy_raw`` is serialized policy content, not a trust root. The admission boundary
    must supply ``trusted_policy_sha256`` from independently provisioned configuration.
    ``authority_auth_keys`` is runtime-only trust material owned by that admission
    boundary. The policy stores only SHA-256 commitments to those keys.
    """

    trusted_policy_sha256 = _sha256(trusted_policy_sha256, "trusted_policy_sha256")
    if not isinstance(authority_auth_keys, Mapping):
        raise ValueError("authority_auth_keys must be a mapping")

    product = ProductCandidate.from_mapping(product_raw)
    policy = AdmissionPolicy.from_mapping(policy_raw)
    evidence = [AdmissionEvidence.from_mapping(raw) for raw in evidence_raw]

    reasons: set[str] = set()
    if not hmac.compare_digest(policy.canonical_sha256(), trusted_policy_sha256):
        reasons.add("admission policy digest mismatch")

    producer_authority = policy.producer_authorities.get(product.producer_id)
    if producer_authority is None:
        reasons.add(f"unauthorized producer identity: {product.producer_id}")
    else:
        _authenticate_authority(
            authority_id=producer_authority,
            canonical_bytes=product.canonical_bytes(),
            proof_sha256=product.producer_hmac_sha256,
            policy=policy,
            authority_auth_keys=authority_auth_keys,
            label=f"producer={product.producer_id}",
            reasons=reasons,
        )

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
            verifier_authority = policy.verifier_authorities[item.verifier_id]
            _authenticate_authority(
                authority_id=verifier_authority,
                canonical_bytes=canonical_bytes,
                proof_sha256=item.verifier_hmac_sha256,
                policy=policy,
                authority_auth_keys=authority_auth_keys,
                label=f"evidence={item.evidence_id}",
                reasons=reasons,
            )
            if producer_authority is not None and verifier_authority == producer_authority:
                reasons.add(
                    f"producer authority cannot verify its own product: {item.check_id}"
                )

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
