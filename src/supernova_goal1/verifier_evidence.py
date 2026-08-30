"""Authenticated host observations for hostile Lean verification.

This module deliberately does not treat a caller-created ``VerifierResult``, a
``LeanVerifierReceipt``, candidate stdout, or a process exit code as production
authority.  Candidate-controlled Lean and the checker run without the signing
key or evidence database.  Only the host supervisor may ask the signer to issue
an append-only record over what it directly launched and observed.

Production ``VALID`` requires two fresh keyless containers.  The first may
execute hostile Lean metaprograms but emits only an exported environment.  The
second receives that data plus trusted challenge source, then applies Comparator
and NanoDA.  Both containers are removed before the host signs or persists the
observation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


SCHEMA = "supernova.confirmatory.verifier-evidence.v2"
SCHEMA_VERSION = 2
VERIFIER_PROTOCOL_VERSION = "goal1-host-verifier-evidence-v2"
SIGNATURE_DOMAIN = b"supernova.confirmatory.verifier-evidence.signature.v2\0"
INDEPENDENT_CHECKER_ID = "LEAN_COMPARATOR_PLUS_NANODA"
CONTAINER_REQUEST_SCHEMA = "supernova.goal1.verifier-container-request.v1"
CONTAINER_RESPONSE_SCHEMA = "supernova.goal1.verifier-container-response.v1"
PERMITTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
PRODUCTION_VALIDITY_BLOCKER = (
    "BLOCKED_UNTRUSTED_VALIDITY_ASSERTION: production VALID is constructible "
    "only from the supervisor-observed two-container verifier protocol"
)
_SUPERVISOR_FACTORY = object()
_SUPERVISOR_VALIDATION = object()
_HEX = frozenset("0123456789abcdef")


class VerifierVerdict(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class TerminationCause(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    SANDBOX_POLICY_VIOLATION = "SANDBOX_POLICY_VIOLATION"
    SANDBOX_START_FAILURE = "SANDBOX_START_FAILURE"
    CHECKER_CRASH = "CHECKER_CRASH"
    HOST_INFRASTRUCTURE_ERROR = "HOST_INFRASTRUCTURE_ERROR"
    INCOMPLETE_EXPORT = "INCOMPLETE_EXPORT"
    MALFORMED_CHECKER_OUTPUT = "MALFORMED_CHECKER_OUTPUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    INDETERMINATE = "INDETERMINATE"


_UNKNOWN_CAUSES = frozenset(
    cause for cause in TerminationCause
    if cause not in {TerminationCause.ACCEPTED, TerminationCause.REJECTED}
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be one exact non-empty trimmed string")
    return value


def _sha256(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _digest_ref(value: object, field: str) -> str:
    value = _token(value, field)
    if not value.startswith("sha256:"):
        raise ValueError(f"{field} must be an immutable sha256 digest")
    _sha256(value.removeprefix("sha256:"), field)
    return value


def _content_address(value: object, field: str) -> str:
    return _digest_ref(value, field)


def _natural(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    if value is not None and type(value) is not int:
        raise ValueError(f"{field} must be an integer or null")
    return value


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _utc_timestamp(value: object, field: str) -> str:
    value = _token(value, field)
    if not value.endswith("Z"):
        raise ValueError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{field} must be UTC")
    return value


def _strict_object(raw: bytes, field: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"{field} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not canonical UTF-8 JSON") from exc
    if type(value) is not dict or canonical_bytes(value) != raw:
        raise ValueError(f"{field} is not one canonical JSON object")
    return value


@dataclass(frozen=True)
class VerifierBinding:
    run_spec_id: str
    run_id: str
    experiment_id: str
    execution_authority_sha256: str
    confirmatory_manifest_sha256: str
    protocol_rules_sha256: str
    protocol_dispatch_id: str
    actual_dispatch_id: str
    dispatch_entry_sha256: str
    frozen_request_sha256: str
    normalized_request_sha256: str
    attempt_result_sha256: str
    problem_id: str
    problem_identity: str
    arm_id: str
    attempt_id: int
    candidate_id: str
    candidate_source_sha256: str
    theorem_statement_sha256: str
    source_construction_sha256: str
    requested_runtime_sha256: str
    actual_runtime_sha256: str
    immutable_configuration_sha256: str

    def __post_init__(self) -> None:
        for field in ("run_id", "experiment_id", "arm_id"):
            _token(getattr(self, field), field)
        for field in (
            "run_spec_id",
            "execution_authority_sha256",
            "confirmatory_manifest_sha256",
            "protocol_rules_sha256",
            "actual_dispatch_id",
            "dispatch_entry_sha256",
            "frozen_request_sha256",
            "normalized_request_sha256",
            "attempt_result_sha256",
            "candidate_source_sha256",
            "theorem_statement_sha256",
            "source_construction_sha256",
            "requested_runtime_sha256",
            "actual_runtime_sha256",
            "immutable_configuration_sha256",
        ):
            _sha256(getattr(self, field), field)
        _content_address(self.problem_id, "problem_id")
        _content_address(self.problem_identity, "problem_identity")
        _content_address(self.candidate_id, "candidate_id")
        if not self.protocol_dispatch_id.startswith("dispatch-"):
            raise ValueError("protocol_dispatch_id must use the dispatch- namespace")
        _sha256(
            self.protocol_dispatch_id.removeprefix("dispatch-"),
            "protocol_dispatch_id",
        )
        _natural(self.attempt_id, "attempt_id")

    def body(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }

    @classmethod
    def from_body(cls, raw: object) -> "VerifierBinding":
        if type(raw) is not dict or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("verifier binding fields changed")
        return cls(**raw)  # type: ignore[arg-type]


@dataclass(frozen=True)
class VerifierIdentity:
    verifier_protocol_version: str
    independent_checker_id: str
    elaborator_image_digest: str
    external_checker_image_digest: str
    toolchain_lock_sha256: str
    project_dependency_lock_sha256: str
    sandbox_policy_sha256: str
    checker_configuration_sha256: str
    verification_command_sha256: str
    immutable_inputs_sha256: str

    def __post_init__(self) -> None:
        if self.verifier_protocol_version != VERIFIER_PROTOCOL_VERSION:
            raise ValueError("verifier protocol version is not supported")
        if self.independent_checker_id != INDEPENDENT_CHECKER_ID:
            raise ValueError("independent checker identity is not supported")
        _digest_ref(self.elaborator_image_digest, "elaborator_image_digest")
        _digest_ref(
            self.external_checker_image_digest,
            "external_checker_image_digest",
        )
        for field in (
            "toolchain_lock_sha256",
            "project_dependency_lock_sha256",
            "sandbox_policy_sha256",
            "checker_configuration_sha256",
            "verification_command_sha256",
            "immutable_inputs_sha256",
        ):
            _sha256(getattr(self, field), field)

    def body(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }

    @classmethod
    def from_body(cls, raw: object) -> "VerifierIdentity":
        if type(raw) is not dict or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("verifier identity fields changed")
        return cls(**raw)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ObservedVerifierRun:
    binding: VerifierBinding
    verifier_identity: VerifierIdentity
    source_bytes: bytes
    candidate_bytes: bytes
    exported_artifact: bytes
    checker_output: bytes
    stdout: bytes
    stderr: bytes
    verdict: VerifierVerdict
    termination_cause: TerminationCause
    elaborator_exit_status: int | None
    elaborator_signal: int | None
    checker_exit_status: int | None
    checker_signal: int | None
    timed_out: bool
    oom_killed: bool
    resource_limited: bool
    sandbox_policy_violated: bool
    started_at_utc: str
    ended_at_utc: str
    elapsed_milliseconds: int
    resource_measurements: Mapping[str, object]
    teardown_observed: bool
    _supervisor_validation: object | None = dataclass_field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.binding) is not VerifierBinding:
            raise TypeError("binding must be an exact VerifierBinding")
        if type(self.verifier_identity) is not VerifierIdentity:
            raise TypeError("verifier_identity must be exact")
        for field in (
            "source_bytes", "candidate_bytes", "exported_artifact",
            "checker_output", "stdout", "stderr",
        ):
            if type(getattr(self, field)) is not bytes:
                raise TypeError(f"{field} must be exact bytes")
        if _sha(self.source_bytes) != self.binding.source_construction_sha256:
            raise ValueError("source bytes do not match source construction digest")
        if _sha(self.candidate_bytes) != self.binding.candidate_source_sha256:
            raise ValueError("candidate bytes do not match candidate source digest")
        for field in (
            "elaborator_exit_status", "elaborator_signal",
            "checker_exit_status", "checker_signal",
        ):
            _optional_int(getattr(self, field), field)
        for field in (
            "timed_out", "oom_killed", "resource_limited",
            "sandbox_policy_violated", "teardown_observed",
        ):
            _boolean(getattr(self, field), field)
        _utc_timestamp(self.started_at_utc, "started_at_utc")
        _utc_timestamp(self.ended_at_utc, "ended_at_utc")
        _natural(self.elapsed_milliseconds, "elapsed_milliseconds")
        if type(self.resource_measurements) is not dict:
            raise TypeError("resource_measurements must be an exact dict")
        canonical_bytes(self.resource_measurements)
        _validate_result_algebra(
            verdict=self.verdict,
            cause=self.termination_cause,
            elaborator_exit_status=self.elaborator_exit_status,
            checker_exit_status=self.checker_exit_status,
            timed_out=self.timed_out,
            oom_killed=self.oom_killed,
            resource_limited=self.resource_limited,
            sandbox_policy_violated=self.sandbox_policy_violated,
            supervisor_validated=(
                self._supervisor_validation is _SUPERVISOR_VALIDATION
            ),
        )


def _validate_result_algebra(
    *,
    verdict: VerifierVerdict,
    cause: TerminationCause,
    elaborator_exit_status: int | None,
    checker_exit_status: int | None,
    timed_out: bool,
    oom_killed: bool,
    resource_limited: bool,
    sandbox_policy_violated: bool,
    supervisor_validated: bool = True,
) -> None:
    if verdict is VerifierVerdict.VALID:
        if (
            not supervisor_validated
            or cause is not TerminationCause.ACCEPTED
            or elaborator_exit_status != 0
            or checker_exit_status != 0
            or timed_out
            or oom_killed
            or resource_limited
            or sandbox_policy_violated
        ):
            raise PermissionError(PRODUCTION_VALIDITY_BLOCKER)
        return
    if verdict is VerifierVerdict.INVALID:
        if (
            cause is not TerminationCause.REJECTED
            or elaborator_exit_status != 0
            or checker_exit_status != 10
            or timed_out
            or oom_killed
            or resource_limited
            or sandbox_policy_violated
        ):
            raise ValueError(
                "INVALID requires a normal deterministic external-checker rejection"
            )
        return
    if verdict is not VerifierVerdict.UNKNOWN or cause not in _UNKNOWN_CAUSES:
        raise ValueError("UNKNOWN requires an explicit uncertainty cause")
    expected_flag = {
        TerminationCause.TIMEOUT: timed_out,
        TerminationCause.OOM: oom_killed,
        TerminationCause.RESOURCE_LIMIT: resource_limited,
        TerminationCause.SANDBOX_POLICY_VIOLATION: sandbox_policy_violated,
    }.get(cause)
    if expected_flag is False:
        raise ValueError(f"{cause.value} requires its observed flag")


def _artifact_body(observation: ObservedVerifierRun) -> dict[str, object]:
    return {
        "candidate_bytes": len(observation.candidate_bytes),
        "candidate_source_sha256": _sha(observation.candidate_bytes),
        "checker_output_bytes": len(observation.checker_output),
        "checker_output_sha256": _sha(observation.checker_output),
        "exported_artifact_bytes": len(observation.exported_artifact),
        "exported_artifact_sha256": _sha(observation.exported_artifact),
        "source_bytes": len(observation.source_bytes),
        "source_sha256": _sha(observation.source_bytes),
        "stderr_bytes": len(observation.stderr),
        "stderr_sha256": _sha(observation.stderr),
        "stdout_bytes": len(observation.stdout),
        "stdout_sha256": _sha(observation.stdout),
    }


def _observation_body(observation: ObservedVerifierRun) -> dict[str, object]:
    resource = dict(observation.resource_measurements)
    return {
        "checker_exit_status": observation.checker_exit_status,
        "checker_signal": observation.checker_signal,
        "elapsed_milliseconds": observation.elapsed_milliseconds,
        "elaborator_exit_status": observation.elaborator_exit_status,
        "elaborator_signal": observation.elaborator_signal,
        "ended_at_utc": observation.ended_at_utc,
        "oom_killed": observation.oom_killed,
        "resource_limited": observation.resource_limited,
        "resource_measurements": resource,
        "resource_usage_sha256": _sha(canonical_bytes(resource)),
        "sandbox_policy_violated": observation.sandbox_policy_violated,
        "started_at_utc": observation.started_at_utc,
        "teardown_observed": observation.teardown_observed,
        "termination_cause": observation.termination_cause.value,
        "timed_out": observation.timed_out,
        "verdict": observation.verdict.value,
    }


def _validate_record_body(body: Mapping[str, Any]) -> None:
    expected = {
        "artifacts", "binding", "issuer_id", "nonce", "observations",
        "record_id", "schema", "schema_version", "signing_key_id",
        "verifier_identity",
    }
    if set(body) != expected or body.get("schema") != SCHEMA:
        raise ValueError("verifier evidence fields or schema changed")
    if body.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("verifier evidence schema_version changed")
    VerifierBinding.from_body(body["binding"])
    VerifierIdentity.from_body(body["verifier_identity"])
    _token(body["issuer_id"], "issuer_id")
    _token(body["signing_key_id"], "signing_key_id")
    record_id = _token(body["record_id"], "record_id")
    if not record_id.startswith("verifier-record-"):
        raise ValueError("record_id has invalid namespace")
    _sha256(record_id.removeprefix("verifier-record-"), "record_id")
    _sha256(body["nonce"], "nonce")
    artifacts = body["artifacts"]
    artifact_fields = {
        "candidate_bytes", "candidate_source_sha256", "checker_output_bytes",
        "checker_output_sha256", "exported_artifact_bytes",
        "exported_artifact_sha256", "source_bytes", "source_sha256",
        "stderr_bytes", "stderr_sha256", "stdout_bytes", "stdout_sha256",
    }
    if type(artifacts) is not dict or set(artifacts) != artifact_fields:
        raise ValueError("verifier artifact fields changed")
    for name, value in artifacts.items():
        if name.endswith("_bytes"):
            _natural(value, name)
        else:
            _sha256(value, name)
    binding = body["binding"]
    if artifacts["candidate_source_sha256"] != binding["candidate_source_sha256"]:
        raise ValueError("signed candidate digest differs from binding")
    if artifacts["source_sha256"] != binding["source_construction_sha256"]:
        raise ValueError("signed source digest differs from binding")
    observed = body["observations"]
    observation_fields = {
        "checker_exit_status", "checker_signal", "elapsed_milliseconds",
        "elaborator_exit_status", "elaborator_signal", "ended_at_utc",
        "oom_killed", "resource_limited", "resource_measurements",
        "resource_usage_sha256", "sandbox_policy_violated", "started_at_utc",
        "teardown_observed", "termination_cause", "timed_out", "verdict",
    }
    if type(observed) is not dict or set(observed) != observation_fields:
        raise ValueError("verifier observation fields changed")
    for name in ("checker_exit_status", "checker_signal", "elaborator_exit_status", "elaborator_signal"):
        _optional_int(observed[name], name)
    for name in ("oom_killed", "resource_limited", "sandbox_policy_violated", "teardown_observed", "timed_out"):
        _boolean(observed[name], name)
    _utc_timestamp(observed["started_at_utc"], "started_at_utc")
    _utc_timestamp(observed["ended_at_utc"], "ended_at_utc")
    _natural(observed["elapsed_milliseconds"], "elapsed_milliseconds")
    if type(observed["resource_measurements"]) is not dict:
        raise ValueError("resource_measurements must be an exact object")
    resource_sha = _sha(canonical_bytes(observed["resource_measurements"]))
    if observed["resource_usage_sha256"] != resource_sha:
        raise ValueError("resource usage digest is invalid")
    _validate_result_algebra(
        verdict=VerifierVerdict(observed["verdict"]),
        cause=TerminationCause(observed["termination_cause"]),
        elaborator_exit_status=observed["elaborator_exit_status"],
        checker_exit_status=observed["checker_exit_status"],
        timed_out=observed["timed_out"],
        oom_killed=observed["oom_killed"],
        resource_limited=observed["resource_limited"],
        sandbox_policy_violated=observed["sandbox_policy_violated"],
    )


@dataclass(frozen=True)
class VerifierEvidenceRecord:
    body_json: bytes
    signature_b64: str

    def __post_init__(self) -> None:
        body = _strict_object(self.body_json, "verifier evidence body")
        _validate_record_body(body)
        try:
            signature = base64.b64decode(self.signature_b64, validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("signature is not canonical base64") from exc
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != self.signature_b64:
            raise ValueError("signature must encode exactly one Ed25519 signature")

    @property
    def body(self) -> dict[str, Any]:
        return _strict_object(self.body_json, "verifier evidence body")

    @property
    def record_sha256(self) -> str:
        return _sha(canonical_bytes({"body": self.body, "signature": self.signature_b64}))

    def verify(
        self,
        public_key: bytes,
        *,
        expected_signing_key_id: str,
        expected_binding: VerifierBinding | None = None,
        expected_identity: VerifierIdentity | None = None,
    ) -> None:
        if type(public_key) is not bytes or len(public_key) != 32:
            raise ValueError("public_key must be one raw Ed25519 public key")
        body = self.body
        if body["signing_key_id"] != _token(expected_signing_key_id, "expected_signing_key_id"):
            raise ValueError("verifier evidence signing key identity changed")
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                base64.b64decode(self.signature_b64, validate=True),
                SIGNATURE_DOMAIN + self.body_json,
            )
        except (ValueError, InvalidSignature) as exc:
            raise ValueError("verifier evidence signature is invalid") from exc
        if expected_binding is not None and body["binding"] != expected_binding.body():
            raise ValueError("verifier evidence run/arm/attempt/input binding changed")
        if expected_identity is not None and body["verifier_identity"] != expected_identity.body():
            raise ValueError("verifier evidence toolchain/checker identity changed")


class HostVerifierSigner:
    """Host-only Ed25519 signer; never pass this object to candidate/checker code."""

    def __init__(self, *, issuer_id: str, signing_key_id: str, private_key: bytes) -> None:
        self.issuer_id = _token(issuer_id, "issuer_id")
        self.signing_key_id = _token(signing_key_id, "signing_key_id")
        if type(private_key) is not bytes or len(private_key) != 32:
            raise ValueError("private_key must be one raw Ed25519 private key")
        self.__key = Ed25519PrivateKey.from_private_bytes(private_key)

    @property
    def public_key(self) -> bytes:
        return self.__key.public_key().public_bytes_raw()

    def _issue(
        self,
        observation: ObservedVerifierRun,
        *,
        _factory: object,
    ) -> VerifierEvidenceRecord:
        if _factory is not _SUPERVISOR_FACTORY:
            raise TypeError("production verifier evidence is issued only by VerifierSupervisor")
        if type(observation) is not ObservedVerifierRun:
            raise TypeError("observation must be an exact ObservedVerifierRun")
        if not observation.teardown_observed:
            raise ValueError("cannot sign before verifier teardown is observed")
        nonce = secrets.token_hex(32)
        body = {
            "artifacts": _artifact_body(observation),
            "binding": observation.binding.body(),
            "issuer_id": self.issuer_id,
            "nonce": nonce,
            "observations": _observation_body(observation),
            "record_id": "verifier-record-" + secrets.token_hex(32),
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "signing_key_id": self.signing_key_id,
            "verifier_identity": observation.verifier_identity.body(),
        }
        body_json = canonical_bytes(body)
        signature = self.__key.sign(SIGNATURE_DOMAIN + body_json)
        return VerifierEvidenceRecord(
            body_json=body_json,
            signature_b64=base64.b64encode(signature).decode("ascii"),
        )


class VerifierEvidenceStore:
    """Append-only host SQLite store with atomic blob/signature readback."""

    def __init__(
        self,
        path: str | Path,
        *,
        verification_key: bytes,
        expected_signing_key_id: str,
        expected_identity: VerifierIdentity,
    ) -> None:
        raw_path = str(path)
        resolved = Path(path).resolve()
        if (
            not raw_path
            or raw_path == ":memory:"
            or raw_path.startswith("file:")
            or not Path(path).is_absolute()
        ):
            raise ValueError("verifier evidence path must be an absolute durable SQLite file")
        if type(verification_key) is not bytes or len(verification_key) != 32:
            raise ValueError("verification_key must be one raw Ed25519 public key")
        if type(expected_identity) is not VerifierIdentity:
            raise TypeError("expected_identity must be an exact VerifierIdentity")
        self.path = resolved
        self.verification_key = verification_key
        self.expected_signing_key_id = _token(
            expected_signing_key_id, "expected_signing_key_id"
        )
        self.expected_identity = expected_identity
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS verifier_evidence (
                  record_sha256 TEXT PRIMARY KEY,
                  record_id TEXT NOT NULL UNIQUE,
                  nonce TEXT NOT NULL UNIQUE,
                  run_spec_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  problem_id TEXT NOT NULL,
                  arm_id TEXT NOT NULL,
                  attempt_id INTEGER NOT NULL,
                  protocol_dispatch_id TEXT NOT NULL,
                  actual_dispatch_id TEXT NOT NULL,
                  candidate_source_sha256 TEXT NOT NULL,
                  normalized_request_sha256 TEXT NOT NULL,
                  theorem_statement_sha256 TEXT NOT NULL,
                  verifier_identity_sha256 TEXT NOT NULL,
                  body_json BLOB NOT NULL,
                  signature_b64 TEXT NOT NULL,
                  source_blob BLOB NOT NULL,
                  candidate_blob BLOB NOT NULL,
                  exported_artifact_blob BLOB NOT NULL,
                  checker_output_blob BLOB NOT NULL,
                  stdout_blob BLOB NOT NULL,
                  stderr_blob BLOB NOT NULL,
                  UNIQUE(run_spec_id, problem_id, arm_id, attempt_id),
                  UNIQUE(run_id, protocol_dispatch_id),
                  UNIQUE(run_id, actual_dispatch_id)
                );
                CREATE TRIGGER IF NOT EXISTS verifier_evidence_no_update
                BEFORE UPDATE ON verifier_evidence BEGIN
                  SELECT RAISE(ABORT, 'verifier evidence is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS verifier_evidence_no_delete
                BEFORE DELETE ON verifier_evidence BEGIN
                  SELECT RAISE(ABORT, 'verifier evidence is append-only');
                END;
                """
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _check_blobs(
        record: VerifierEvidenceRecord,
        *,
        source: bytes,
        candidate: bytes,
        exported_artifact: bytes,
        checker_output: bytes,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        artifacts = record.body["artifacts"]
        values = (
            ("source", source, "source_sha256", "source_bytes"),
            ("candidate", candidate, "candidate_source_sha256", "candidate_bytes"),
            (
                "exported_artifact",
                exported_artifact,
                "exported_artifact_sha256",
                "exported_artifact_bytes",
            ),
            (
                "checker_output",
                checker_output,
                "checker_output_sha256",
                "checker_output_bytes",
            ),
            ("stdout", stdout, "stdout_sha256", "stdout_bytes"),
            ("stderr", stderr, "stderr_sha256", "stderr_bytes"),
        )
        for name, blob, digest_field, size_field in values:
            if type(blob) is not bytes:
                raise TypeError(f"{name} blob must be exact bytes")
            if artifacts[digest_field] != _sha(blob) or artifacts[size_field] != len(blob):
                raise ValueError(f"{name} blob does not match signed verifier evidence")

    def append(
        self,
        record: VerifierEvidenceRecord,
        *,
        expected_binding: VerifierBinding,
        source: bytes,
        candidate: bytes,
        exported_artifact: bytes,
        checker_output: bytes,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        if type(record) is not VerifierEvidenceRecord:
            raise TypeError("record must be an exact VerifierEvidenceRecord")
        if type(expected_binding) is not VerifierBinding:
            raise TypeError("expected_binding must be an exact VerifierBinding")
        record.verify(
            self.verification_key,
            expected_signing_key_id=self.expected_signing_key_id,
            expected_binding=expected_binding,
            expected_identity=self.expected_identity,
        )
        self._check_blobs(
            record,
            source=source,
            candidate=candidate,
            exported_artifact=exported_artifact,
            checker_output=checker_output,
            stdout=stdout,
            stderr=stderr,
        )
        body = record.body
        binding = body["binding"]
        identity_sha = _sha(canonical_bytes(body["verifier_identity"]))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO verifier_evidence (
                     record_sha256,record_id,nonce,run_spec_id,run_id,problem_id,
                     arm_id,attempt_id,protocol_dispatch_id,actual_dispatch_id,
                     candidate_source_sha256,normalized_request_sha256,
                     theorem_statement_sha256,verifier_identity_sha256,body_json,
                     signature_b64,source_blob,candidate_blob,exported_artifact_blob,
                     checker_output_blob,stdout_blob,stderr_blob
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.record_sha256,
                    body["record_id"],
                    body["nonce"],
                    binding["run_spec_id"],
                    binding["run_id"],
                    binding["problem_id"],
                    binding["arm_id"],
                    binding["attempt_id"],
                    binding["protocol_dispatch_id"],
                    binding["actual_dispatch_id"],
                    binding["candidate_source_sha256"],
                    binding["normalized_request_sha256"],
                    binding["theorem_statement_sha256"],
                    identity_sha,
                    record.body_json,
                    record.signature_b64,
                    source,
                    candidate,
                    exported_artifact,
                    checker_output,
                    stdout,
                    stderr,
                ),
            )
            persisted = self._read_row(connection, expected_binding)
            if persisted.record_sha256 != record.record_sha256:
                raise ValueError("atomic verifier evidence readback changed")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _read_row(
        self,
        connection: sqlite3.Connection,
        binding: VerifierBinding,
    ) -> VerifierEvidenceRecord:
        row = connection.execute(
            """SELECT record_sha256,body_json,signature_b64,source_blob,
                      candidate_blob,exported_artifact_blob,checker_output_blob,
                      stdout_blob,stderr_blob
                 FROM verifier_evidence
                WHERE run_spec_id=? AND run_id=? AND problem_id=?
                  AND arm_id=? AND attempt_id=?""",
            (
                binding.run_spec_id,
                binding.run_id,
                binding.problem_id,
                binding.arm_id,
                binding.attempt_id,
            ),
        ).fetchone()
        if row is None:
            raise KeyError(
                "missing verifier evidence for "
                f"{binding.run_spec_id}/{binding.run_id}/{binding.problem_id}/"
                f"{binding.arm_id}/{binding.attempt_id}"
            )
        (
            expected_digest,
            body_json,
            signature,
            source,
            candidate,
            exported_artifact,
            checker_output,
            stdout,
            stderr,
        ) = row
        record = VerifierEvidenceRecord(
            body_json=bytes(body_json), signature_b64=str(signature)
        )
        record.verify(
            self.verification_key,
            expected_signing_key_id=self.expected_signing_key_id,
            expected_binding=binding,
            expected_identity=self.expected_identity,
        )
        self._check_blobs(
            record,
            source=bytes(source),
            candidate=bytes(candidate),
            exported_artifact=bytes(exported_artifact),
            checker_output=bytes(checker_output),
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )
        if record.record_sha256 != expected_digest:
            raise ValueError("persisted verifier evidence digest is invalid")
        return record

    def read(self, binding: VerifierBinding) -> VerifierEvidenceRecord:
        if type(binding) is not VerifierBinding:
            raise TypeError("binding must be an exact VerifierBinding")
        connection = self._connect()
        try:
            return self._read_row(connection, binding)
        finally:
            connection.close()

    def read_complete(
        self,
        bindings: Sequence[VerifierBinding],
    ) -> tuple[VerifierEvidenceRecord, ...]:
        if isinstance(bindings, (str, bytes)):
            raise TypeError("bindings must be an ordered sequence")
        exact = tuple(bindings)
        if not all(type(value) is VerifierBinding for value in exact):
            raise TypeError("bindings must contain exact VerifierBinding values")
        expected = {
            (
                value.run_spec_id,
                value.run_id,
                value.problem_id,
                value.arm_id,
                value.attempt_id,
            )
            for value in exact
        }
        if len(expected) != len(exact):
            raise ValueError("expected verifier bindings contain replay")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT run_spec_id,run_id,problem_id,arm_id,attempt_id "
                "FROM verifier_evidence"
            ).fetchall()
        finally:
            connection.close()
        actual = {
            (str(run_spec), str(run_id), str(problem), str(arm), int(attempt))
            for run_spec, run_id, problem, arm, attempt in rows
        }
        if actual != expected:
            raise ValueError(
                "verifier evidence does not exactly cover expected attempts; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        return tuple(self.read(value) for value in exact)


@dataclass(frozen=True)
class VerifierSandboxLauncher:
    """Immutable outer verifier container policy.

    The host launches the pinned image twice: first as a hostile elaborator and
    then, in a fresh container, as the data-only Comparator plus NanoDA checker.
    Neither container receives signing authority or the evidence-store path.
    """

    image_ref: str
    command: tuple[str, ...]
    image_environment: tuple[str, ...]
    container_user: str
    memory_bytes: int
    nano_cpus: int
    pids_limit: int
    timeout_seconds: int
    max_output_bytes: int
    tmpfs_size_bytes: int
    toolchain_lock_sha256: str
    project_dependency_lock_sha256: str
    checker_configuration_sha256: str
    immutable_inputs_sha256: str

    def __post_init__(self) -> None:
        if "@sha256:" not in self.image_ref:
            raise ValueError("image_ref must be pinned as repository@sha256")
        _sha256(self.image_ref.rsplit("@sha256:", 1)[1], "image_ref digest")
        if type(self.command) is not tuple or not self.command:
            raise ValueError("command must be one non-empty tuple")
        for value in self.command:
            _token(value, "command[]")
        if type(self.image_environment) is not tuple:
            raise ValueError("image_environment must be one exact tuple")
        names = []
        for value in self.image_environment:
            value = _token(value, "image_environment[]")
            if "=" not in value:
                raise ValueError("image environment entries must be NAME=VALUE")
            names.append(value.split("=", 1)[0])
        if len(names) != len(set(names)):
            raise ValueError("image environment contains duplicate names")
        forbidden = {
            "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL", "DOCKER_HOST", "GH_TOKEN", "GITHUB_TOKEN",
            "OPENAI_API_KEY", "SUPERNOVA_VERIFIER_PRIVATE_KEY",
        }
        if forbidden & set(names):
            raise ValueError("verifier image environment exposes a credential or socket")
        user = _token(self.container_user, "container_user")
        if user.split(":", 1)[0].lower() in {"0", "root"}:
            raise ValueError("container_user must be non-root")
        for field in (
            "memory_bytes", "nano_cpus", "pids_limit", "timeout_seconds",
            "max_output_bytes", "tmpfs_size_bytes",
        ):
            if type(getattr(self, field)) is not int or getattr(self, field) <= 0:
                raise ValueError(f"{field} must be a positive integer")
        for field in (
            "toolchain_lock_sha256", "project_dependency_lock_sha256",
            "checker_configuration_sha256", "immutable_inputs_sha256",
        ):
            _sha256(getattr(self, field), field)

    @property
    def image_digest(self) -> str:
        return "sha256:" + self.image_ref.rsplit("@sha256:", 1)[1]

    @property
    def sandbox_policy(self) -> dict[str, object]:
        return {
            "cap_add": [],
            "cap_drop": ["ALL"],
            "container_runtime": "runc",
            "container_user": self.container_user,
            "cpu_nano": self.nano_cpus,
            "devices": [],
            "filesystem": "READ_ONLY_ROOT_PLUS_BOUNDED_TMPFS",
            "file_size_bytes": self.tmpfs_size_bytes,
            "host_mounts": [],
            "ipc": "none",
            "memory_bytes": self.memory_bytes,
            "network": "none",
            "no_new_privileges": True,
            "pid_namespace": "private",
            "pids_limit": self.pids_limit,
            "privileged": False,
            "tmpfs": {"/tmp": self.tmpfs_size_bytes},
            "transport": "STDIN_STDOUT_ONLY",
        }

    @property
    def identity(self) -> VerifierIdentity:
        command_sha = _sha(canonical_bytes(list(self.command)))
        sandbox_sha = _sha(canonical_bytes(self.sandbox_policy))
        return VerifierIdentity(
            verifier_protocol_version=VERIFIER_PROTOCOL_VERSION,
            independent_checker_id=INDEPENDENT_CHECKER_ID,
            elaborator_image_digest=self.image_digest,
            external_checker_image_digest=self.image_digest,
            toolchain_lock_sha256=self.toolchain_lock_sha256,
            project_dependency_lock_sha256=self.project_dependency_lock_sha256,
            sandbox_policy_sha256=sandbox_sha,
            checker_configuration_sha256=self.checker_configuration_sha256,
            verification_command_sha256=command_sha,
            immutable_inputs_sha256=self.immutable_inputs_sha256,
        )


class _SandboxPolicyError(RuntimeError):
    pass


def _invoke(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int | None = None,
    max_output_bytes: int = 1024 * 1024,
) -> subprocess.CompletedProcess[bytes]:
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be a positive integer")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            completed = subprocess.run(
                list(argv),
                input=input_bytes,
                stdout=stdout_file,
                stderr=stderr_file,
                check=False,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_file.flush()
            stderr_file.flush()
            stdout_file.seek(0)
            stderr_file.seek(0)
            exc.stdout = stdout_file.read(max_output_bytes)
            exc.stderr = stderr_file.read(max_output_bytes)
            raise

        def bounded(handle: Any, field: str) -> bytes:
            handle.flush()
            size = handle.seek(0, 2)
            if size > max_output_bytes:
                raise RuntimeError(f"{field} exceeded its byte limit")
            handle.seek(0)
            return handle.read(max_output_bytes + 1)

        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            bounded(stdout_file, "docker stdout"),
            bounded(stderr_file, "docker stderr"),
        )


def _success(result: subprocess.CompletedProcess[bytes], field: str) -> bytes:
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{field} failed: {detail}")
    return result.stdout


def _docker_object(container_id: str) -> dict[str, Any]:
    raw = _success(_invoke(["docker", "inspect", container_id]), "docker inspect")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("docker inspect returned invalid JSON") from exc
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise RuntimeError("docker inspect returned an unexpected object")
    return value[0]


def _image_identity(launcher: VerifierSandboxLauncher) -> str:
    raw = _success(
        _invoke(["docker", "image", "inspect", launcher.image_ref]),
        "docker image inspect",
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("docker image inspect returned invalid JSON") from exc
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise RuntimeError("docker image inspect returned an unexpected object")
    image_id = value[0].get("Id")
    if (
        type(image_id) is not str
        or not image_id.startswith("sha256:")
        or launcher.image_ref not in (value[0].get("RepoDigests") or [])
    ):
        raise RuntimeError("local verifier image does not match pinned repository digest")
    return image_id


def _create_argv(launcher: VerifierSandboxLauncher) -> list[str]:
    tmpfs = f"rw,noexec,nosuid,nodev,size={launcher.tmpfs_size_bytes}"
    return [
        "docker", "create", "--pull", "never", "--network", "none",
        "--read-only", "--init", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges:true", "--pids-limit", str(launcher.pids_limit),
        "--ipc", "none", "--user", launcher.container_user, "--runtime",
        "runc", "--memory", str(launcher.memory_bytes), "--cpus",
        format(launcher.nano_cpus / 1_000_000_000, ".9f"), "--tmpfs",
        f"/tmp:{tmpfs}", "--ulimit",
        f"fsize={launcher.tmpfs_size_bytes}:{launcher.tmpfs_size_bytes}",
        "--interactive", launcher.image_ref, *launcher.command,
    ]


def _security_snapshot(
    inspection: Mapping[str, Any],
    launcher: VerifierSandboxLauncher,
    image_id: str,
) -> dict[str, object]:
    host = inspection.get("HostConfig")
    config = inspection.get("Config")
    mounts = inspection.get("Mounts")
    if type(host) is not dict or type(config) is not dict or type(mounts) is not list:
        raise _SandboxPolicyError("container inspection omitted security configuration")
    environment = config.get("Env") or []
    tmpfs = host.get("Tmpfs") or {}
    ulimits = host.get("Ulimits") or []
    if (
        host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or sorted(host.get("CapDrop") or []) != ["ALL"]
        or (host.get("CapAdd") or []) != []
        or sorted(host.get("SecurityOpt") or []) != ["no-new-privileges:true"]
        or host.get("PidsLimit") != launcher.pids_limit
        or host.get("Memory") != launcher.memory_bytes
        or host.get("NanoCpus") != launcher.nano_cpus
        or host.get("IpcMode") != "none"
        or host.get("PidMode") != ""
        or host.get("UTSMode") != ""
        or host.get("UsernsMode") not in {None, ""}
        or host.get("PublishAllPorts") is not False
        or (host.get("PortBindings") or {}) != {}
        or (host.get("Binds") or []) != []
        or mounts != []
        or (host.get("Devices") or []) != []
        or (host.get("DeviceRequests") or []) != []
        or (host.get("ExtraHosts") or []) != []
        or (host.get("Links") or []) != []
        or (host.get("VolumesFrom") or []) != []
        or (host.get("GroupAdd") or []) != []
        or ulimits != [
            {
                "Hard": launcher.tmpfs_size_bytes,
                "Name": "fsize",
                "Soft": launcher.tmpfs_size_bytes,
            }
        ]
        or host.get("Init") is not True
        or host.get("Runtime") != "runc"
        or config.get("OpenStdin") is not True
        or config.get("Tty") is not False
        or config.get("Cmd") != list(launcher.command)
        or config.get("User") != launcher.container_user
        or environment != list(launcher.image_environment)
        or inspection.get("Image") != image_id
        or set(tmpfs) != {"/tmp"}
        or "noexec" not in str(tmpfs["/tmp"])
        or "nosuid" not in str(tmpfs["/tmp"])
        or "nodev" not in str(tmpfs["/tmp"])
    ):
        raise _SandboxPolicyError("verifier container security configuration drifted")
    return {
        "image_id": image_id,
        "image_ref": launcher.image_ref,
        "observed_environment": list(environment),
        "observed_tmpfs": dict(tmpfs),
        "policy": launcher.sandbox_policy,
    }


def _remove_observed(container_id: str) -> None:
    _success(_invoke(["docker", "rm", "--force", container_id]), "docker rm")
    probe = _invoke(["docker", "inspect", container_id])
    detail = probe.stderr.decode("utf-8", errors="replace").lower()
    if probe.returncode == 0 or (
        "no such container" not in detail and "no such object" not in detail
    ):
        raise RuntimeError("post-removal container absence was not observed")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class _PhaseObservation:
    name: str
    request_sha256: str
    container_id: str | None
    stdout: bytes
    stderr: bytes
    response: dict[str, Any] | None
    exported_artifact: bytes
    exit_status: int | None
    signal: int | None
    timed_out: bool
    oom_killed: bool
    resource_limited: bool
    sandbox_policy_violated: bool
    cause: TerminationCause | None
    security_snapshot_sha256: str | None

    def measurements(self) -> dict[str, object]:
        return {
            "cause": None if self.cause is None else self.cause.value,
            "container_id": self.container_id,
            "exit_status": self.exit_status,
            "oom_killed": self.oom_killed,
            "request_sha256": self.request_sha256,
            "resource_limited": self.resource_limited,
            "sandbox_policy_violated": self.sandbox_policy_violated,
            "security_snapshot_sha256": self.security_snapshot_sha256,
            "signal": self.signal,
            "stderr_sha256": _sha(self.stderr),
            "stdout_sha256": _sha(self.stdout),
            "timed_out": self.timed_out,
        }


def _decode_export(value: Mapping[str, Any], field: str) -> bytes:
    encoded = _token(value.get(f"{field}_b64"), f"{field}_b64")
    expected = _sha256(value.get(f"{field}_sha256"), f"{field}_sha256")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_b64 is not canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise ValueError(f"{field}_b64 is not canonical base64")
    if _sha(decoded) != expected:
        raise ValueError(f"{field} digest mismatch")
    return decoded


def _parse_container_response(raw: bytes) -> tuple[dict[str, Any], bytes]:
    value = _strict_object(raw, "verifier container response")
    if value.get("schema") != CONTAINER_RESPONSE_SCHEMA:
        raise ValueError("verifier container response schema changed")
    status = value.get("status")
    if status == "EXPORTED":
        expected = {
            "schema", "solution_export_b64", "solution_export_sha256", "status",
        }
        if set(value) != expected:
            raise ValueError("elaborator response fields changed")
        return value, _decode_export(value, "solution_export")
    if status == VerifierVerdict.VALID.value:
        expected = {
            "challenge_export_sha256", "checker", "schema",
            "solution_export_sha256", "status",
        }
        if set(value) != expected:
            raise ValueError("checker VALID response fields changed")
        _sha256(value["challenge_export_sha256"], "challenge_export_sha256")
        _sha256(value["solution_export_sha256"], "solution_export_sha256")
        if value["checker"] != "COMPARATOR_DATA_ONLY_PLUS_NANODA":
            raise ValueError("independent checker identity changed")
        return value, b""
    if status in {VerifierVerdict.INVALID.value, VerifierVerdict.UNKNOWN.value}:
        if set(value) != {"diagnostic", "schema", "status"}:
            raise ValueError("non-valid verifier response fields changed")
        if type(value["diagnostic"]) is not str:
            raise ValueError("verifier diagnostic must be a string")
        return value, b""
    raise ValueError("verifier container response status changed")


def _run_container_phase(
    launcher: VerifierSandboxLauncher,
    *,
    image_id: str,
    name: str,
    request: bytes,
) -> _PhaseObservation:
    container_id: str | None = None
    stdout = b""
    stderr = b""
    response: dict[str, Any] | None = None
    exported = b""
    exit_status: int | None = None
    signal_value: int | None = None
    timed_out = False
    oom_killed = False
    resource_limited = False
    policy_violated = False
    cause: TerminationCause | None = None
    snapshot_sha: str | None = None
    try:
        created = _invoke(_create_argv(launcher))
        if created.returncode != 0:
            cause = TerminationCause.SANDBOX_START_FAILURE
            stderr = created.stderr
        else:
            container_id = created.stdout.decode("ascii", errors="strict").strip()
            if len(container_id) != 64 or any(char not in _HEX for char in container_id):
                raise RuntimeError("docker create did not return one full container id")
            inspection = _docker_object(container_id)
            try:
                snapshot = _security_snapshot(inspection, launcher, image_id)
            except _SandboxPolicyError as exc:
                cause = TerminationCause.SANDBOX_POLICY_VIOLATION
                policy_violated = True
                stderr = str(exc).encode("utf-8")
            else:
                snapshot_sha = _sha(canonical_bytes(snapshot))
                try:
                    started = _invoke(
                        ["docker", "start", "--attach", "--interactive", container_id],
                        input_bytes=request,
                        timeout=launcher.timeout_seconds,
                        max_output_bytes=launcher.max_output_bytes,
                    )
                    stdout = started.stdout
                    stderr = started.stderr
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    cause = TerminationCause.TIMEOUT
                    stdout = bytes(exc.stdout or b"")
                    stderr = bytes(exc.stderr or b"")
                except RuntimeError as exc:
                    if "exceeded its byte limit" not in str(exc):
                        raise
                    cause = TerminationCause.RESOURCE_LIMIT
                    resource_limited = True
                    stderr = str(exc).encode("utf-8")
                state = _docker_object(container_id).get("State") or {}
                if type(state) is not dict:
                    raise RuntimeError("docker state is unavailable")
                exit_status = (
                    state.get("ExitCode")
                    if type(state.get("ExitCode")) is int
                    else None
                )
                oom_killed = state.get("OOMKilled") is True
                if type(exit_status) is int and 128 <= exit_status <= 255:
                    signal_value = exit_status - 128
                if oom_killed:
                    cause = TerminationCause.OOM
                if cause is None:
                    try:
                        response, exported = _parse_container_response(stdout)
                    except ValueError as exc:
                        cause = TerminationCause.MALFORMED_CHECKER_OUTPUT
                        stderr = (stderr + b"\n" + str(exc).encode("utf-8")).strip()
    except (OSError, RuntimeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if cause not in {
            TerminationCause.RESOURCE_LIMIT,
            TerminationCause.SANDBOX_POLICY_VIOLATION,
            TerminationCause.SANDBOX_START_FAILURE,
        }:
            cause = TerminationCause.HOST_INFRASTRUCTURE_ERROR
        stderr = (stderr + b"\n" + str(exc).encode("utf-8")).strip()
    finally:
        if container_id is not None:
            try:
                _remove_observed(container_id)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(
                    f"{name} teardown was not observed; evidence was not signed"
                ) from exc
    return _PhaseObservation(
        name=name,
        request_sha256=_sha(request),
        container_id=container_id,
        stdout=stdout,
        stderr=stderr,
        response=response,
        exported_artifact=exported,
        exit_status=exit_status,
        signal=signal_value,
        timed_out=timed_out,
        oom_killed=oom_killed,
        resource_limited=resource_limited,
        sandbox_policy_violated=policy_violated,
        cause=cause,
        security_snapshot_sha256=snapshot_sha,
    )


class VerifierSupervisor:
    """Trusted host launcher, observer, signer, and atomic persistence boundary."""

    def __init__(
        self,
        launcher: VerifierSandboxLauncher,
        signer: HostVerifierSigner,
        store: VerifierEvidenceStore,
    ) -> None:
        if type(launcher) is not VerifierSandboxLauncher:
            raise TypeError("launcher must be an exact VerifierSandboxLauncher")
        if type(signer) is not HostVerifierSigner:
            raise TypeError("signer must be an exact HostVerifierSigner")
        if type(store) is not VerifierEvidenceStore:
            raise TypeError("store must be an exact VerifierEvidenceStore")
        if signer.public_key != store.verification_key:
            raise ValueError("signer key does not match verifier evidence store")
        if signer.signing_key_id != store.expected_signing_key_id:
            raise ValueError("signer key id does not match verifier evidence store")
        if launcher.identity != store.expected_identity:
            raise ValueError("launcher identity does not match verifier evidence store")
        self.launcher = launcher
        self.signer = signer
        self.store = store

    def _persist(
        self,
        *,
        binding: VerifierBinding,
        source: bytes,
        candidate: bytes,
        exported_artifact: bytes,
        checker_output: bytes,
        stdout: bytes,
        stderr: bytes,
        cause: TerminationCause,
        elaborator_exit_status: int | None,
        elaborator_signal: int | None,
        verdict: VerifierVerdict,
        checker_exit_status: int | None,
        checker_signal: int | None,
        timed_out: bool,
        oom_killed: bool,
        resource_limited: bool,
        sandbox_policy_violated: bool,
        started_at_utc: str,
        ended_at_utc: str,
        elapsed_milliseconds: int,
        resource_measurements: dict[str, object],
    ) -> VerifierEvidenceRecord:
        observation = ObservedVerifierRun(
            binding=binding,
            verifier_identity=self.launcher.identity,
            source_bytes=source,
            candidate_bytes=candidate,
            exported_artifact=exported_artifact,
            checker_output=checker_output,
            stdout=stdout,
            stderr=stderr,
            verdict=verdict,
            termination_cause=cause,
            elaborator_exit_status=elaborator_exit_status,
            elaborator_signal=elaborator_signal,
            checker_exit_status=checker_exit_status,
            checker_signal=checker_signal,
            timed_out=timed_out,
            oom_killed=oom_killed,
            resource_limited=resource_limited,
            sandbox_policy_violated=sandbox_policy_violated,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
            elapsed_milliseconds=elapsed_milliseconds,
            resource_measurements=resource_measurements,
            teardown_observed=True,
            _supervisor_validation=_SUPERVISOR_VALIDATION,
        )
        record = self.signer._issue(observation, _factory=_SUPERVISOR_FACTORY)
        self.store.append(
            record,
            expected_binding=binding,
            source=source,
            candidate=candidate,
            exported_artifact=exported_artifact,
            checker_output=checker_output,
            stdout=stdout,
            stderr=stderr,
        )
        return self.store.read(binding)

    def record_unknown_without_execution(
        self,
        binding: VerifierBinding,
        *,
        source: bytes,
        candidate: bytes,
        cause: TerminationCause,
        detail: str,
    ) -> VerifierEvidenceRecord:
        """Record a host-observed nondecision such as missing candidate input."""

        if type(binding) is not VerifierBinding:
            raise TypeError("binding must be an exact VerifierBinding")
        if cause not in _UNKNOWN_CAUSES:
            raise ValueError("non-execution record requires an UNKNOWN cause")
        detail = _token(detail, "detail")
        started = _now_utc()
        return self._persist(
            binding=binding,
            source=source,
            candidate=candidate,
            exported_artifact=b"",
            checker_output=b"",
            stdout=b"",
            stderr=detail.encode("utf-8"),
            cause=cause,
            verdict=VerifierVerdict.UNKNOWN,
            elaborator_exit_status=None,
            elaborator_signal=None,
            checker_exit_status=None,
            checker_signal=None,
            timed_out=cause is TerminationCause.TIMEOUT,
            oom_killed=cause is TerminationCause.OOM,
            resource_limited=cause is TerminationCause.RESOURCE_LIMIT,
            sandbox_policy_violated=(
                cause is TerminationCause.SANDBOX_POLICY_VIOLATION
            ),
            started_at_utc=started,
            ended_at_utc=_now_utc(),
            elapsed_milliseconds=0,
            resource_measurements={"container_created": False, "detail": detail},
        )

    def _run_two_phase_and_record(
        self,
        binding: VerifierBinding,
        *,
        source: bytes,
        candidate: bytes,
        theorem_names: Sequence[str],
    ) -> VerifierEvidenceRecord:
        if type(binding) is not VerifierBinding:
            raise TypeError("binding must be an exact VerifierBinding")
        if type(source) is not bytes or type(candidate) is not bytes:
            raise TypeError("source and candidate must be exact bytes")
        if _sha(source) != binding.source_construction_sha256:
            raise ValueError("source bytes differ from the bound source construction")
        if _sha(candidate) != binding.candidate_source_sha256:
            raise ValueError("candidate bytes differ from the bound candidate")
        if isinstance(theorem_names, (str, bytes)):
            raise TypeError("theorem_names must be an ordered sequence")
        exact_theorems = tuple(theorem_names)
        if not exact_theorems:
            raise ValueError("theorem_names must not be empty")
        for theorem in exact_theorems:
            _token(theorem, "theorem_names[]")
        if len(exact_theorems) != len(set(exact_theorems)):
            raise ValueError("theorem_names contains duplicates")

        solution_source = source + candidate
        challenge_source = source + b"  sorry\n"
        elaborate_request = canonical_bytes(
            {
                "mode": "elaborate",
                "permitted_axioms": list(PERMITTED_AXIOMS),
                "schema": CONTAINER_REQUEST_SCHEMA,
                "solution_source_b64": base64.b64encode(solution_source).decode("ascii"),
                "solution_source_sha256": _sha(solution_source),
                "theorem_names": list(exact_theorems),
            }
        )
        started_at = _now_utc()
        monotonic_start = time.monotonic_ns()
        phases: list[_PhaseObservation] = []
        docker_runtime_sha = ""
        image_id = ""
        verdict = VerifierVerdict.UNKNOWN
        cause = TerminationCause.HOST_INFRASTRUCTURE_ERROR
        exported = b""
        checker_output = b""
        try:
            docker_version = _success(
                _invoke(["docker", "version", "--format", "{{json .}}"]),
                "docker version",
            )
            docker_value = json.loads(docker_version.decode("utf-8"))
            docker_runtime_sha = _sha(canonical_bytes(docker_value))
            image_id = _image_identity(self.launcher)
            elaborator = _run_container_phase(
                self.launcher,
                image_id=image_id,
                name="elaborator",
                request=elaborate_request,
            )
            phases.append(elaborator)
            if elaborator.cause is not None:
                cause = elaborator.cause
            else:
                status = None if elaborator.response is None else elaborator.response["status"]
                if status == VerifierVerdict.INVALID.value and elaborator.exit_status == 10:
                    # An elaboration error can be produced by hostile metaprogram
                    # behavior, so it is not a mathematical rejection.
                    cause = TerminationCause.INDETERMINATE
                elif status == VerifierVerdict.UNKNOWN.value and elaborator.exit_status == 20:
                    cause = TerminationCause.CHECKER_CRASH
                elif status != "EXPORTED" or elaborator.exit_status != 0:
                    cause = TerminationCause.MALFORMED_CHECKER_OUTPUT
                elif not elaborator.exported_artifact:
                    cause = TerminationCause.INCOMPLETE_EXPORT
                else:
                    exported = elaborator.exported_artifact
                    check_request = canonical_bytes(
                        {
                            "challenge_source_b64": base64.b64encode(
                                challenge_source
                            ).decode("ascii"),
                            "challenge_source_sha256": _sha(challenge_source),
                            "mode": "check",
                            "permitted_axioms": list(PERMITTED_AXIOMS),
                            "schema": CONTAINER_REQUEST_SCHEMA,
                            "solution_export_b64": base64.b64encode(exported).decode(
                                "ascii"
                            ),
                            "solution_export_sha256": _sha(exported),
                            "theorem_names": list(exact_theorems),
                        }
                    )
                    checker = _run_container_phase(
                        self.launcher,
                        image_id=image_id,
                        name="checker",
                        request=check_request,
                    )
                    phases.append(checker)
                    checker_output = checker.stdout
                    if checker.cause is not None:
                        cause = checker.cause
                    else:
                        checker_status = (
                            None if checker.response is None else checker.response["status"]
                        )
                        if (
                            checker_status == VerifierVerdict.VALID.value
                            and checker.exit_status == 0
                            and checker.response is not None
                            and checker.response["solution_export_sha256"] == _sha(exported)
                        ):
                            verdict = VerifierVerdict.VALID
                            cause = TerminationCause.ACCEPTED
                        elif (
                            checker_status == VerifierVerdict.INVALID.value
                            and checker.exit_status == 10
                        ):
                            verdict = VerifierVerdict.INVALID
                            cause = TerminationCause.REJECTED
                        elif (
                            checker_status == VerifierVerdict.UNKNOWN.value
                            and checker.exit_status == 20
                        ):
                            cause = TerminationCause.CHECKER_CRASH
                        else:
                            cause = TerminationCause.MALFORMED_CHECKER_OUTPUT
        except (OSError, RuntimeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            phases.append(
                _PhaseObservation(
                    name="host",
                    request_sha256=_sha(elaborate_request),
                    container_id=None,
                    stdout=b"",
                    stderr=str(exc).encode("utf-8"),
                    response=None,
                    exported_artifact=b"",
                    exit_status=None,
                    signal=None,
                    timed_out=False,
                    oom_killed=False,
                    resource_limited=False,
                    sandbox_policy_violated=False,
                    cause=TerminationCause.HOST_INFRASTRUCTURE_ERROR,
                    security_snapshot_sha256=None,
                )
            )
            verdict = VerifierVerdict.UNKNOWN
            cause = TerminationCause.HOST_INFRASTRUCTURE_ERROR

        elaborator = next((phase for phase in phases if phase.name == "elaborator"), None)
        checker = next((phase for phase in phases if phase.name == "checker"), None)
        timed_out = any(phase.timed_out for phase in phases)
        oom_killed = any(phase.oom_killed for phase in phases)
        resource_limited = any(phase.resource_limited for phase in phases)
        policy_violated = any(phase.sandbox_policy_violated for phase in phases)
        stdout = b"" if elaborator is None else elaborator.stdout
        stderr = b"\n".join(
            phase.name.encode("ascii") + b":" + phase.stderr
            for phase in phases
            if phase.stderr
        )
        elapsed = (time.monotonic_ns() - monotonic_start) // 1_000_000
        measurements: dict[str, object] = {
            "challenge_source_sha256": _sha(challenge_source),
            "docker_runtime_sha256": docker_runtime_sha,
            "image_id": image_id,
            "permitted_axioms": list(PERMITTED_AXIOMS),
            "phases": [phase.measurements() for phase in phases],
            "solution_source_sha256": _sha(solution_source),
            "theorem_names": list(exact_theorems),
            "two_fresh_containers_required": True,
        }
        return self._persist(
            binding=binding,
            source=source,
            candidate=candidate,
            exported_artifact=exported,
            checker_output=checker_output,
            stdout=stdout,
            stderr=stderr,
            cause=cause,
            verdict=verdict,
            elaborator_exit_status=(None if elaborator is None else elaborator.exit_status),
            elaborator_signal=(None if elaborator is None else elaborator.signal),
            checker_exit_status=(None if checker is None else checker.exit_status),
            checker_signal=(None if checker is None else checker.signal),
            timed_out=timed_out,
            oom_killed=oom_killed,
            resource_limited=resource_limited,
            sandbox_policy_violated=policy_violated,
            started_at_utc=started_at,
            ended_at_utc=_now_utc(),
            elapsed_milliseconds=int(elapsed),
            resource_measurements=measurements,
        )


    def run_and_record(
        self,
        binding: VerifierBinding,
        *,
        source: bytes,
        candidate: bytes,
        theorem_names: Sequence[str],
    ) -> VerifierEvidenceRecord:
        """Run the hostile elaborator and independent checker in fresh sandboxes.

        Candidate output is never accepted as a verdict.  Only the host-observed
        two-phase protocol may construct ``VALID`` or ``INVALID``; every
        resource, policy, protocol, and infrastructure failure is ``UNKNOWN``.
        """

        return self._run_two_phase_and_record(
            binding,
            source=source,
            candidate=candidate,
            theorem_names=theorem_names,
        )

__all__ = [
    "HostVerifierSigner",
    "INDEPENDENT_CHECKER_ID",
    "PRODUCTION_VALIDITY_BLOCKER",
    "SCHEMA",
    "TerminationCause",
    "VerifierBinding",
    "VerifierEvidenceRecord",
    "VerifierEvidenceStore",
    "VerifierIdentity",
    "VerifierSandboxLauncher",
    "VerifierSupervisor",
    "VerifierVerdict",
    "canonical_bytes",
]
