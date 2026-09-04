from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ScheduledChatArtifactEnvelope, ScheduledChatArtifactKind
from .execution.baselines import BaselineDispatch
from .execution.common import AttemptResult, AttemptStatus
from .problem import BenchmarkProblemIdentity
from .verifier import VerifierResult, VerifierStatus
from .verifier_evidence import (
    TerminationCause,
    VerifierBinding,
    VerifierEvidenceBlobs,
    VerifierEvidenceRecord,
    VerifierSupervisor,
    VerifierVerdict,
    canonical_bytes,
)

_RECORD_FIELDS = {
    "schema_version",
    "problem_id",
    "split",
    "source_id",
    "source_record_sha256",
    "lean_code_sha256",
    "lean_code",
    "informal_prefix",
}
_HEX = frozenset("0123456789abcdef")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in _HEX for char in value)
    ):
        raise ValueError(f"{field} must be one lowercase sha256")
    return value


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be one non-empty trimmed string")
    value.encode("utf-8")
    return value


def canonical_sha256(value: object) -> str:
    return _sha(canonical_bytes(value))


def _theorem_statement(source: bytes, theorem_name: str) -> bytes:
    ending = b":= by\n"
    if not source.endswith(ending):
        raise ValueError("frozen Lean source must end exactly in ':= by\\n'")
    marker = b"theorem " + theorem_name.encode("utf-8")
    starts = [
        index
        for index in range(len(source))
        if source.startswith(marker, index)
        and (index == 0 or source[index - 1 : index] == b"\n")
    ]
    if len(starts) != 1:
        raise ValueError("frozen Lean source must contain exactly one named theorem")
    end = len(source) - len(ending)
    if starts[0] >= end:
        raise ValueError("frozen theorem statement is empty")
    return source[starts[0] : end]


@dataclass(frozen=True)
class FrozenLeanProblemSource:
    """Exact frozen benchmark source used to construct one verifier request."""

    native_id: str
    split: str
    source_id: str
    source_record_sha256: str
    source: bytes
    source_sha256: str
    theorem_statement: bytes
    theorem_statement_sha256: str

    @classmethod
    def from_record(
        cls,
        raw: Mapping[str, Any],
        *,
        expected_split: str,
        expected_schema_version: int = 1,
    ) -> FrozenLeanProblemSource:
        if type(raw) is not dict or set(raw) != _RECORD_FIELDS:
            raise ValueError("frozen benchmark record fields changed")
        if expected_schema_version not in {1, 2}:
            raise ValueError("expected benchmark record schema is unsupported")
        if raw["schema_version"] != expected_schema_version:
            raise ValueError("frozen benchmark record schema changed")
        native_id = _text(raw["problem_id"], "problem_id")
        split = _text(raw["split"], "split")
        if split != _text(expected_split, "expected_split"):
            raise ValueError("frozen benchmark record is from the wrong split")
        source_id = _text(raw["source_id"], "source_id")
        source_record_sha256 = _sha256(
            raw["source_record_sha256"], "source_record_sha256"
        )
        source_sha256 = _sha256(raw["lean_code_sha256"], "lean_code_sha256")
        lean_code = raw["lean_code"]
        if type(lean_code) is not str or not lean_code:
            raise ValueError("lean_code must be one non-empty exact string")
        source = lean_code.encode("utf-8")
        if _sha(source) != source_sha256:
            raise ValueError("lean_code_sha256 does not match lean_code")
        if type(raw["informal_prefix"]) is not str:
            raise ValueError("informal_prefix must be one exact string")
        statement = _theorem_statement(source, native_id)
        return cls(
            native_id=native_id,
            split=split,
            source_id=source_id,
            source_record_sha256=source_record_sha256,
            source=source,
            source_sha256=source_sha256,
            theorem_statement=statement,
            theorem_statement_sha256=_sha(statement),
        )


@dataclass(frozen=True)
class VerificationSubject:
    """Exact keyless-container inputs derived before verification begins."""

    challenge_source: bytes
    candidate_source: bytes
    theorem_names: tuple[str, ...]
    theorem_statement_sha256: str
    theorem_target_set_sha256: str
    source_construction_sha256: str
    product_parser_source: bytes | None = None
    product_parser_expected_name: str | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.challenge_source, "challenge_source"),
            (self.candidate_source, "candidate_source"),
        ):
            if type(value) is not bytes:
                raise TypeError(f"{field} must be exact bytes")
            try:
                value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{field} must be UTF-8") from exc
        if (
            type(self.theorem_names) is not tuple
            or len(self.theorem_names) != 1
            or not all(type(value) is str and value for value in self.theorem_names)
        ):
            raise ValueError("theorem_names must contain one exact target")
        _sha256(self.theorem_statement_sha256, "theorem_statement_sha256")
        _sha256(self.theorem_target_set_sha256, "theorem_target_set_sha256")
        _sha256(self.source_construction_sha256, "source_construction_sha256")
        if _sha(self.challenge_source) != self.source_construction_sha256:
            raise ValueError("source construction digest mismatch")
        if canonical_sha256(list(self.theorem_names)) != self.theorem_target_set_sha256:
            raise ValueError("theorem target-set digest mismatch")
        parser_required = self.product_parser_source is not None
        if parser_required != (self.product_parser_expected_name is not None):
            raise ValueError("product parser source and name must be supplied together")
        if self.product_parser_source is not None:
            if (
                type(self.product_parser_source) is not bytes
                or not self.product_parser_source
            ):
                raise TypeError("product_parser_source must be non-empty exact bytes")
            try:
                self.product_parser_source.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("product_parser_source must be UTF-8") from exc
            if not self.candidate_source.endswith(self.product_parser_source):
                raise ValueError(
                    "product parser source is not bound to candidate bytes"
                )
            _text(self.product_parser_expected_name, "product_parser_expected_name")


def _product_parser_measurement(
    record: VerifierEvidenceRecord,
    candidate: bytes,
) -> dict[str, object] | None:
    measurements = record.body["observations"]["resource_measurements"]
    raw = measurements.get("product_parser")
    if raw is None:
        return None
    expected = {"admissible", "expected_name", "source_bytes", "source_sha256"}
    if type(raw) is not dict or set(raw) != expected:
        raise ValueError("signed product parser evidence fields changed")
    if raw["admissible"] not in {True, False, None}:
        raise ValueError("signed product parser admission changed")
    name = _text(raw["expected_name"], "product parser expected_name")
    size = raw["source_bytes"]
    if type(size) is not int or size <= 0 or size > len(candidate):
        raise ValueError("signed product parser source size changed")
    digest = _sha256(raw["source_sha256"], "product parser source_sha256")
    if _sha(candidate[-size:]) != digest:
        raise ValueError("signed product parser source is not bound to candidate")
    return {
        "admissible": raw["admissible"],
        "expected_name": name,
        "source_bytes": size,
        "source_sha256": digest,
    }


@dataclass(frozen=True)
class ProductionVerification:
    """One host-read signed verifier result and the exact request it attests."""

    binding: VerifierBinding
    record: VerifierEvidenceRecord
    blobs: VerifierEvidenceBlobs
    result: VerifierResult

    def __post_init__(self) -> None:
        if type(self.binding) is not VerifierBinding:
            raise TypeError("binding must be an exact VerifierBinding")
        if type(self.record) is not VerifierEvidenceRecord:
            raise TypeError("record must be an exact VerifierEvidenceRecord")
        if type(self.blobs) is not VerifierEvidenceBlobs:
            raise TypeError("blobs must be exact VerifierEvidenceBlobs")
        if type(self.result) is not VerifierResult:
            raise TypeError("result must be an exact VerifierResult")
        if self.record.body["binding"] != self.binding.body():
            raise ValueError("signed verifier record differs from its dispatch binding")
        if _sha(self.blobs.candidate) != self.binding.candidate_source_sha256:
            raise ValueError("verifier candidate blob differs from its binding")
        if _sha(self.blobs.source) != self.binding.source_construction_sha256:
            raise ValueError("verifier source blob differs from its binding")
        derived = verifier_result_from_evidence(
            self.record,
            self.blobs,
            command=self.result.command,
        )
        if derived != self.result:
            raise ValueError(
                "compatibility result differs from signed verifier evidence"
            )
        _product_parser_measurement(self.record, self.blobs.candidate)

    @property
    def product_parser_admissible(self) -> bool | None:
        measurement = _product_parser_measurement(self.record, self.blobs.candidate)
        return None if measurement is None else measurement["admissible"]  # type: ignore[return-value]


def _default_subject(
    source: FrozenLeanProblemSource,
    candidate: bytes,
) -> VerificationSubject:
    theorem_names = (source.native_id,)
    return VerificationSubject(
        challenge_source=source.source,
        candidate_source=candidate,
        theorem_names=theorem_names,
        theorem_statement_sha256=source.theorem_statement_sha256,
        theorem_target_set_sha256=canonical_sha256(list(theorem_names)),
        source_construction_sha256=source.source_sha256,
    )


def load_frozen_lean_sources(
    path: Path,
    *,
    expected_file_sha256: str,
    expected_records: int,
    benchmark: str,
    version: str,
    split: str,
    expected_record_schema_version: int = 1,
) -> dict[str, FrozenLeanProblemSource]:
    """Load one fully frozen split after verifying its complete byte identity."""

    if not isinstance(path, Path):
        raise TypeError("path must be one pathlib.Path")
    expected_file_sha256 = _sha256(expected_file_sha256, "expected_file_sha256")
    if type(expected_records) is not int or expected_records < 1:
        raise ValueError("expected_records must be one positive exact integer")
    benchmark = _text(benchmark, "benchmark")
    version = _text(version, "version")
    split = _text(split, "split")
    if expected_record_schema_version not in {1, 2}:
        raise ValueError("expected benchmark record schema is unsupported")
    raw_file = path.resolve(strict=True).read_bytes()
    if _sha(raw_file) != expected_file_sha256:
        raise ValueError("frozen benchmark file digest mismatch")
    lines = raw_file.splitlines(keepends=True)
    if len(lines) != expected_records or any(
        not line.endswith(b"\n") or not line.strip() for line in lines
    ):
        raise ValueError("frozen benchmark record count or framing changed")

    sources: dict[str, FrozenLeanProblemSource] = {}
    native_ids: list[str] = []
    source_record_digests: set[str] = set()
    source_digests: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        payload = line[:-1]

        def pairs(
            values: list[tuple[str, Any]],
            *,
            _line_number: int = line_number,
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in values:
                if key in result:
                    raise ValueError(
                        f"frozen benchmark line {_line_number} contains duplicate key"
                    )
                result[key] = value
            return result

        try:
            record = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON value {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"frozen benchmark line {line_number} is not UTF-8 JSON"
            ) from exc
        if type(record) is not dict or canonical_bytes(record) != payload:
            raise ValueError(
                f"frozen benchmark line {line_number} is not canonical JSON"
            )
        source = FrozenLeanProblemSource.from_record(
            record,
            expected_split=split,
            expected_schema_version=expected_record_schema_version,
        )
        problem = BenchmarkProblemIdentity(
            benchmark=benchmark,
            version=version,
            split=split,
            native_id=source.native_id,
        )
        if problem.canonical_id in sources:
            raise ValueError("frozen benchmark contains duplicate problem identity")
        if source.source_record_sha256 in source_record_digests:
            raise ValueError("frozen benchmark contains duplicate source record")
        if source.source_sha256 in source_digests:
            raise ValueError("frozen benchmark contains duplicate Lean source")
        sources[problem.canonical_id] = source
        native_ids.append(source.native_id)
        source_record_digests.add(source.source_record_sha256)
        source_digests.add(source.source_sha256)
    if native_ids != sorted(native_ids):
        raise ValueError("frozen benchmark problem order changed")
    return sources


def build_verifier_binding(
    dispatch: BaselineDispatch,
    candidate: bytes,
    source: FrozenLeanProblemSource,
    *,
    run_spec_id: str,
    execution_authority_sha256: str,
    protocol_rules_sha256: str,
    confirmatory_manifest_sha256: str,
    actual_runtime_sha256: str,
    subject: VerificationSubject | None = None,
) -> VerifierBinding:
    """Bind exact pre-completion inputs; no verifier verdict is accepted here."""

    if type(dispatch) is not BaselineDispatch:
        raise TypeError("dispatch must be an exact BaselineDispatch")
    if type(candidate) is not bytes:
        raise TypeError("candidate must be exact bytes")
    if type(source) is not FrozenLeanProblemSource:
        raise TypeError("source must be an exact FrozenLeanProblemSource")
    if subject is None:
        subject = _default_subject(source, candidate)
    elif type(subject) is not VerificationSubject:
        raise TypeError("subject must be an exact VerificationSubject or null")
    if subject.candidate_source != candidate:
        raise ValueError("verification subject candidate differs from visible response")
    request = dispatch.request
    if request.problem.native_id != source.native_id:
        raise ValueError("frozen problem identity differs from benchmark source")
    if request.problem.split != source.split:
        raise ValueError("frozen problem split differs from benchmark source")
    if request.problem_sha256 != source.source_sha256:
        raise ValueError("frozen problem digest differs from exact Lean source")
    if dispatch.entry.request_sha256 != request.frozen_request_sha256:
        raise ValueError("registered dispatch differs from frozen request")

    response = ScheduledChatArtifactEnvelope.from_visible_utf8(
        candidate,
        kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
        run_id=request.run_id,
        problem_id=request.problem_id,
        arm=request.arm,
        attempt=request.attempt,
    )
    attempt_result = AttemptResult(
        frozen_request_sha256=request.frozen_request_sha256,
        run_id=request.run_id,
        problem_id=request.problem_id,
        arm=request.arm,
        attempt=request.attempt,
        request_artifact_id=request.request_artifact.artifact_id,
        response_artifact=response,
        status=AttemptStatus.ANSWERED,
        error=None,
    )
    immutable_configuration_sha256 = canonical_sha256(
        {
            "actual_runtime_sha256": actual_runtime_sha256,
            "confirmatory_manifest_sha256": confirmatory_manifest_sha256,
            "execution_authority_sha256": execution_authority_sha256,
            "protocol_rules_sha256": protocol_rules_sha256,
            "requested_runtime_sha256": request.runtime_sha256,
        }
    )
    return VerifierBinding(
        run_spec_id=run_spec_id,
        run_id=request.run_id,
        experiment_id=request.experiment_id,
        execution_authority_sha256=execution_authority_sha256,
        confirmatory_manifest_sha256=confirmatory_manifest_sha256,
        protocol_rules_sha256=protocol_rules_sha256,
        protocol_dispatch_id=request.protocol_dispatch_id,
        actual_dispatch_id=dispatch.entry.dispatch_id,
        dispatch_entry_sha256=dispatch.entry.entry_sha256,
        frozen_request_sha256=request.frozen_request_sha256,
        normalized_request_sha256=request.frozen_request_sha256,
        attempt_result_sha256=attempt_result.attempt_result_sha256,
        problem_id=request.problem_id,
        problem_identity=request.problem.canonical_id,
        arm_id=request.arm.value,
        attempt_id=request.attempt,
        candidate_id=response.artifact_id,
        candidate_source_sha256=response.sha256_hex,
        theorem_statement_sha256=subject.theorem_statement_sha256,
        source_template_sha256=source.source_sha256,
        rendered_source_sha256=subject.source_construction_sha256,
        theorem_target_set_sha256=subject.theorem_target_set_sha256,
        source_construction_sha256=subject.source_construction_sha256,
        requested_runtime_sha256=request.runtime_sha256,
        actual_runtime_sha256=actual_runtime_sha256,
        immutable_configuration_sha256=immutable_configuration_sha256,
    )


def verifier_result_from_evidence(
    record: VerifierEvidenceRecord,
    blobs: VerifierEvidenceBlobs,
    *,
    command: tuple[str, ...],
) -> VerifierResult:
    """Derive the compatibility result from authenticated host observations."""

    if type(record) is not VerifierEvidenceRecord:
        raise TypeError("record must be an exact VerifierEvidenceRecord")
    if type(blobs) is not VerifierEvidenceBlobs:
        raise TypeError("blobs must be exact VerifierEvidenceBlobs")
    if type(command) is not tuple or not command:
        raise TypeError("command must be one non-empty exact tuple")
    body = record.body
    observed = body["observations"]
    verdict = VerifierVerdict(observed["verdict"])
    cause = TerminationCause(observed["termination_cause"])
    if verdict is VerifierVerdict.VALID:
        status = VerifierStatus.PASS
        returncode = 0
        error = None
    elif verdict is VerifierVerdict.INVALID:
        status = VerifierStatus.FAIL
        returncode = (
            observed["checker_exit_status"]
            if observed["checker_exit_status"] is not None
            else observed["elaborator_exit_status"]
        )
        if type(returncode) is not int or returncode == 0:
            raise ValueError("authenticated INVALID lacks a nonzero rejection status")
        error = None
    else:
        status = (
            VerifierStatus.TIMEOUT
            if cause is TerminationCause.TIMEOUT
            else VerifierStatus.ERROR
        )
        returncode = None
        error = f"authenticated verifier UNKNOWN: {cause.value}"
    return VerifierResult(
        status=status,
        command=command,
        returncode=returncode,
        stdout=blobs.stdout.decode("utf-8", errors="replace"),
        stderr=blobs.stderr.decode("utf-8", errors="replace"),
        elapsed_milliseconds=observed["elapsed_milliseconds"],
        error=error,
    )


class ProductionVerifierPort:
    """Execution-adapter port backed only by host-observed signed evidence."""

    def __init__(
        self,
        supervisor: VerifierSupervisor,
        sources_by_problem_id: Mapping[str, FrozenLeanProblemSource],
        *,
        run_spec_id: str,
        execution_authority_sha256: str,
        protocol_rules_sha256: str,
        confirmatory_manifest_sha256: str,
        subject_builder: (
            Callable[
                [BaselineDispatch, bytes, FrozenLeanProblemSource],
                VerificationSubject,
            ]
            | None
        ) = None,
    ) -> None:
        if type(supervisor) is not VerifierSupervisor:
            raise TypeError("supervisor must be an exact VerifierSupervisor")
        if type(sources_by_problem_id) is not dict:
            raise TypeError("sources_by_problem_id must be one exact dict")
        copied = dict(sources_by_problem_id)
        if not copied or not all(
            type(key) is str and type(value) is FrozenLeanProblemSource
            for key, value in copied.items()
        ):
            raise ValueError("sources_by_problem_id contains invalid entries")
        self.supervisor = supervisor
        self.sources_by_problem_id = copied
        self.run_spec_id = _sha256(run_spec_id, "run_spec_id")
        self.execution_authority_sha256 = _sha256(
            execution_authority_sha256, "execution_authority_sha256"
        )
        self.protocol_rules_sha256 = _sha256(
            protocol_rules_sha256, "protocol_rules_sha256"
        )
        self.confirmatory_manifest_sha256 = _sha256(
            confirmatory_manifest_sha256, "confirmatory_manifest_sha256"
        )
        if subject_builder is not None and not callable(subject_builder):
            raise TypeError("subject_builder must be callable or null")
        self.subject_builder = subject_builder
        self._bindings_by_dispatch: dict[str, VerifierBinding] = {}

    @property
    def bindings_by_dispatch(self) -> dict[str, VerifierBinding]:
        """Return the exact pre-completion bindings issued by this port."""

        return dict(self._bindings_by_dispatch)

    def verify(
        self,
        dispatch: BaselineDispatch,
        candidate: bytes,
    ) -> ProductionVerification:
        """Run once and return only evidence read back through the host store."""

        source = self.sources_by_problem_id.get(dispatch.request.problem_id)
        if source is None:
            raise KeyError("no frozen Lean source for registered problem")
        subject = (
            _default_subject(source, candidate)
            if self.subject_builder is None
            else self.subject_builder(dispatch, candidate, source)
        )
        return self._verify_resolved_subject(dispatch, candidate, subject)

    def _verify_resolved_subject(
        self,
        dispatch: BaselineDispatch,
        candidate: bytes,
        subject: VerificationSubject,
    ) -> ProductionVerification:
        """Verify one service-resolved subject after binding every exact byte.

        This method is deliberately private. Production callers cross the
        locator-only boundary in :mod:`supernova_goal1.verifier_service`; they
        cannot supply a theorem target, source, runtime, or verdict.
        """

        source = self.sources_by_problem_id.get(dispatch.request.problem_id)
        if source is None:
            raise KeyError("no frozen Lean source for registered problem")
        if type(subject) is not VerificationSubject:
            raise TypeError("subject must be an exact VerificationSubject")
        binding = build_verifier_binding(
            dispatch,
            candidate,
            source,
            run_spec_id=self.run_spec_id,
            execution_authority_sha256=self.execution_authority_sha256,
            protocol_rules_sha256=self.protocol_rules_sha256,
            confirmatory_manifest_sha256=self.confirmatory_manifest_sha256,
            actual_runtime_sha256=self.supervisor.launcher.toolchain_lock_sha256,
            subject=subject,
        )
        if binding.actual_dispatch_id in self._bindings_by_dispatch:
            raise ValueError("verifier dispatch replay rejected")
        self._bindings_by_dispatch[binding.actual_dispatch_id] = binding
        record = self.supervisor.run_and_record(
            binding,
            source=subject.challenge_source,
            candidate=subject.candidate_source,
            theorem_names=subject.theorem_names,
            product_parser_source=subject.product_parser_source,
            product_parser_expected_name=subject.product_parser_expected_name,
        )
        blobs = self.supervisor.store.read_blobs(binding)
        result = verifier_result_from_evidence(
            record,
            blobs,
            command=self.supervisor.launcher.command,
        )
        verification = ProductionVerification(binding, record, blobs, result)
        parser = _product_parser_measurement(record, blobs.candidate)
        if subject.product_parser_source is None:
            if parser is not None:
                raise ValueError("unexpected signed product parser evidence")
        elif (
            parser is None
            or parser["expected_name"] != subject.product_parser_expected_name
            or parser["source_bytes"] != len(subject.product_parser_source)
            or parser["source_sha256"] != _sha(subject.product_parser_source)
        ):
            raise ValueError("signed product parser evidence differs from subject")
        return verification

    def __call__(self, dispatch: BaselineDispatch, candidate: bytes) -> VerifierResult:
        return self.verify(dispatch, candidate).result


__all__ = [
    "FrozenLeanProblemSource",
    "ProductionVerification",
    "ProductionVerifierPort",
    "VerificationSubject",
    "build_verifier_binding",
    "canonical_sha256",
    "load_frozen_lean_sources",
    "verifier_result_from_evidence",
]
