from __future__ import annotations

from collections import namedtuple
from enum import StrEnum
from hashlib import sha256
import json
import unicodedata
from typing import Any, Mapping, Sequence

from ..artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from ..contracts import (
    Arm,
    MODEL_USAGE_BASES,
    UNFROZEN_MODEL_USAGE_BASIS,
)
from ..problem import BenchmarkProblemIdentity
from ..verifier import VerifierResult, VerifierStatus


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _message(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be an exact non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _sha256_hex(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value


def _content_address(value: object, field: str) -> str:
    value = _token(value, field)
    if not value.startswith("sha256:"):
        raise ValueError(f"{field} must be a sha256 content address")
    _sha256_hex(value.removeprefix("sha256:"), field)
    return value


def _attempt(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("attempt must be a non-negative integer")
    return value


def _arm(value: object) -> Arm:
    if type(value) is Arm:
        return value
    if type(value) is not str:
        raise ValueError(f"unknown arm: {value!r}")
    try:
        return Arm(value)
    except ValueError as exc:
        raise ValueError(f"unknown arm: {value!r}") from exc


def _usage_basis(value: object) -> str:
    allowed = MODEL_USAGE_BASES | {UNFROZEN_MODEL_USAGE_BASIS}
    if type(value) is not str or value not in allowed:
        raise ValueError(
            "model_usage_basis must be provider_tokens, visible_utf8_bytes, "
            "or UNFROZEN"
        )
    return value


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _problem_snapshot(value: object) -> BenchmarkProblemIdentity:
    if type(value) is not BenchmarkProblemIdentity:
        raise TypeError("problem must be an exact BenchmarkProblemIdentity")
    raw = value.to_mapping()
    return BenchmarkProblemIdentity(
        benchmark=raw["benchmark"],
        version=raw["version"],
        split=raw["split"],
        native_id=raw["native_id"],
    )


def _artifact_snapshot(
    value: object, field: str
) -> ScheduledChatArtifactEnvelope:
    if type(value) is not ScheduledChatArtifactEnvelope:
        raise TypeError(f"{field} must be an exact ScheduledChatArtifactEnvelope")
    return ScheduledChatArtifactEnvelope.from_mapping(value.to_mapping())


def _problem_from_mapping(raw: object) -> BenchmarkProblemIdentity:
    if not isinstance(raw, Mapping):
        raise ValueError("problem must be an object")
    expected = {"benchmark", "version", "split", "native_id"}
    if set(raw) != expected:
        raise ValueError(f"problem fields must be exactly {sorted(expected)}")
    return BenchmarkProblemIdentity(
        benchmark=raw["benchmark"],  # type: ignore[arg-type]
        version=raw["version"],  # type: ignore[arg-type]
        split=raw["split"],  # type: ignore[arg-type]
        native_id=raw["native_id"],  # type: ignore[arg-type]
    )


def _artifact_from_mapping(
    raw: object, field: str
) -> ScheduledChatArtifactEnvelope:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} must be an object")
    return ScheduledChatArtifactEnvelope.from_mapping(raw)


_FrozenProblemRequestTuple = namedtuple(
    "FrozenProblemRequest",
    (
        "run_id",
        "experiment_id",
        "problem",
        "benchmark_root_sha256",
        "problem_sha256",
        "arm",
        "attempt",
        "budget_id",
        "budget_sha256",
        "model_usage_basis",
        "runtime_sha256",
        "request_artifact",
    ),
    module=__name__,
)


class FrozenProblemRequest(_FrozenProblemRequestTuple):
    """Authority-free immutable request identity for one scheduled-chat attempt.

    This value binds exact inputs. It does not prove dispatch registration, budget
    closure, or scientific eligibility.
    """

    __slots__ = ()

    def __new__(
        cls,
        *,
        run_id: str,
        experiment_id: str,
        problem: BenchmarkProblemIdentity,
        benchmark_root_sha256: str,
        problem_sha256: str,
        arm: Arm | str,
        attempt: int,
        budget_id: str,
        budget_sha256: str,
        model_usage_basis: str,
        runtime_sha256: str,
        request_artifact: ScheduledChatArtifactEnvelope,
    ) -> "FrozenProblemRequest":
        run_id = _token(run_id, "run_id")
        experiment_id = _token(experiment_id, "experiment_id")
        problem = _problem_snapshot(problem)
        benchmark_root_sha256 = _sha256_hex(
            benchmark_root_sha256, "benchmark_root_sha256"
        )
        problem_sha256 = _sha256_hex(problem_sha256, "problem_sha256")
        arm = _arm(arm)
        attempt = _attempt(attempt)
        budget_id = _token(budget_id, "budget_id")
        budget_sha256 = _sha256_hex(budget_sha256, "budget_sha256")
        model_usage_basis = _usage_basis(model_usage_basis)
        runtime_sha256 = _sha256_hex(runtime_sha256, "runtime_sha256")
        request_artifact = _artifact_snapshot(
            request_artifact, "request_artifact"
        )
        if request_artifact.kind is not ScheduledChatArtifactKind.REQUEST:
            raise ValueError("request_artifact must be a scheduled-chat request")
        expected_artifact_identity = (
            run_id,
            problem.canonical_id,
            arm,
            attempt,
        )
        actual_artifact_identity = (
            request_artifact.run_id,
            request_artifact.problem_id,
            request_artifact.arm,
            request_artifact.attempt,
        )
        if actual_artifact_identity != expected_artifact_identity:
            raise ValueError(
                "request_artifact run/problem/arm/attempt does not match request"
            )
        return super().__new__(
            cls,
            run_id,
            experiment_id,
            problem,
            benchmark_root_sha256,
            problem_sha256,
            arm,
            attempt,
            budget_id,
            budget_sha256,
            model_usage_basis,
            runtime_sha256,
            request_artifact,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FrozenProblemRequest may not be subclassed")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "arm": self.arm.value,
            "attempt": self.attempt,
            "benchmark_root_sha256": self.benchmark_root_sha256,
            "budget_id": self.budget_id,
            "budget_sha256": self.budget_sha256,
            "experiment_id": self.experiment_id,
            "model_usage_basis": self.model_usage_basis,
            "problem": self.problem.to_mapping(),
            "problem_sha256": self.problem_sha256,
            "request_artifact": self.request_artifact.to_mapping(),
            "run_id": self.run_id,
            "runtime_sha256": self.runtime_sha256,
        }

    @property
    def frozen_request_sha256(self) -> str:
        return _canonical_sha256(self._identity_payload())

    @property
    def problem_id(self) -> str:
        return self.problem.canonical_id

    def to_mapping(self) -> dict[str, object]:
        raw = self._identity_payload()
        raw["frozen_request_sha256"] = self.frozen_request_sha256
        return raw

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FrozenProblemRequest":
        expected = {
            "arm",
            "attempt",
            "benchmark_root_sha256",
            "budget_id",
            "budget_sha256",
            "experiment_id",
            "frozen_request_sha256",
            "model_usage_basis",
            "problem",
            "problem_sha256",
            "request_artifact",
            "run_id",
            "runtime_sha256",
        }
        if set(raw) != expected:
            raise ValueError(
                f"frozen request fields must be exactly {sorted(expected)}"
            )
        request = cls(
            run_id=raw["run_id"],
            experiment_id=raw["experiment_id"],
            problem=_problem_from_mapping(raw["problem"]),
            benchmark_root_sha256=raw["benchmark_root_sha256"],
            problem_sha256=raw["problem_sha256"],
            arm=raw["arm"],
            attempt=raw["attempt"],
            budget_id=raw["budget_id"],
            budget_sha256=raw["budget_sha256"],
            model_usage_basis=raw["model_usage_basis"],
            runtime_sha256=raw["runtime_sha256"],
            request_artifact=_artifact_from_mapping(
                raw["request_artifact"], "request_artifact"
            ),
        )
        if raw["frozen_request_sha256"] != request.frozen_request_sha256:
            raise ValueError("frozen_request_sha256 does not match request fields")
        return request


class AttemptStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


_AttemptResultTuple = namedtuple(
    "AttemptResult",
    (
        "frozen_request_sha256",
        "run_id",
        "problem_id",
        "arm",
        "attempt",
        "request_artifact_id",
        "response_artifact",
        "status",
        "error",
    ),
    module=__name__,
)


class AttemptResult(_AttemptResultTuple):
    """Immutable terminal visible result linked to one frozen request."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        frozen_request_sha256: str,
        run_id: str,
        problem_id: str,
        arm: Arm | str,
        attempt: int,
        request_artifact_id: str,
        response_artifact: ScheduledChatArtifactEnvelope,
        status: AttemptStatus | str,
        error: str | None,
    ) -> "AttemptResult":
        frozen_request_sha256 = _sha256_hex(
            frozen_request_sha256, "frozen_request_sha256"
        )
        run_id = _token(run_id, "run_id")
        problem_id = _content_address(problem_id, "problem_id")
        arm = _arm(arm)
        attempt = _attempt(attempt)
        request_artifact_id = _content_address(
            request_artifact_id, "request_artifact_id"
        )
        response_artifact = _artifact_snapshot(
            response_artifact, "response_artifact"
        )
        if type(status) is AttemptStatus:
            parsed_status = status
        elif type(status) is str:
            try:
                parsed_status = AttemptStatus(status)
            except ValueError as exc:
                raise ValueError(f"unknown attempt status: {status!r}") from exc
        else:
            raise ValueError(f"unknown attempt status: {status!r}")
        if response_artifact.kind is not ScheduledChatArtifactKind.TERMINAL_RESPONSE:
            raise ValueError(
                "response_artifact must be a scheduled-chat terminal response"
            )
        expected_artifact_identity = (run_id, problem_id, arm, attempt)
        actual_artifact_identity = (
            response_artifact.run_id,
            response_artifact.problem_id,
            response_artifact.arm,
            response_artifact.attempt,
        )
        if actual_artifact_identity != expected_artifact_identity:
            raise ValueError(
                "response_artifact run/problem/arm/attempt does not match result"
            )
        if parsed_status is AttemptStatus.ERROR:
            error = _message(error, "error")
        elif error is not None:
            raise ValueError(f"{parsed_status.value} attempt cannot carry error")
        return super().__new__(
            cls,
            frozen_request_sha256,
            run_id,
            problem_id,
            arm,
            attempt,
            request_artifact_id,
            response_artifact,
            parsed_status,
            error,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AttemptResult may not be subclassed")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "arm": self.arm.value,
            "attempt": self.attempt,
            "error": self.error,
            "frozen_request_sha256": self.frozen_request_sha256,
            "problem_id": self.problem_id,
            "request_artifact_id": self.request_artifact_id,
            "response_artifact": self.response_artifact.to_mapping(),
            "run_id": self.run_id,
            "status": self.status.value,
        }

    @property
    def attempt_result_sha256(self) -> str:
        return _canonical_sha256(self._identity_payload())

    def to_mapping(self) -> dict[str, object]:
        raw = self._identity_payload()
        raw["attempt_result_sha256"] = self.attempt_result_sha256
        return raw

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AttemptResult":
        expected = {
            "arm",
            "attempt",
            "attempt_result_sha256",
            "error",
            "frozen_request_sha256",
            "problem_id",
            "request_artifact_id",
            "response_artifact",
            "run_id",
            "status",
        }
        if set(raw) != expected:
            raise ValueError(
                f"attempt result fields must be exactly {sorted(expected)}"
            )
        result = cls(
            frozen_request_sha256=raw["frozen_request_sha256"],
            run_id=raw["run_id"],
            problem_id=raw["problem_id"],
            arm=raw["arm"],
            attempt=raw["attempt"],
            request_artifact_id=raw["request_artifact_id"],
            response_artifact=_artifact_from_mapping(
                raw["response_artifact"], "response_artifact"
            ),
            status=raw["status"],
            error=raw["error"],
        )
        if raw["attempt_result_sha256"] != result.attempt_result_sha256:
            raise ValueError("attempt_result_sha256 does not match result fields")
        return result

    def validate_for(self, request: FrozenProblemRequest) -> None:
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())
        result = AttemptResult.from_mapping(self.to_mapping())
        expected = (
            request.frozen_request_sha256,
            request.run_id,
            request.problem_id,
            request.arm,
            request.attempt,
            request.request_artifact.artifact_id,
        )
        actual = (
            result.frozen_request_sha256,
            result.run_id,
            result.problem_id,
            result.arm,
            result.attempt,
            result.request_artifact_id,
        )
        if actual != expected:
            raise ValueError("attempt result does not match frozen request")


_LeanVerifierReceiptTuple = namedtuple(
    "LeanVerifierReceipt",
    (
        "frozen_request_sha256",
        "attempt_result_sha256",
        "run_id",
        "problem_id",
        "arm",
        "attempt",
        "candidate_artifact_id",
        "runtime_sha256",
        "status",
        "command",
        "returncode",
        "stdout_sha256",
        "stderr_sha256",
        "elapsed_milliseconds",
        "error",
    ),
    module=__name__,
)


class LeanVerifierReceipt(_LeanVerifierReceiptTuple):
    """Immutable verifier-output snapshot, not an admission or trust assertion."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        frozen_request_sha256: str,
        attempt_result_sha256: str,
        run_id: str,
        problem_id: str,
        arm: Arm | str,
        attempt: int,
        candidate_artifact_id: str,
        runtime_sha256: str,
        status: VerifierStatus | str,
        command: Sequence[str],
        returncode: int | None,
        stdout_sha256: str,
        stderr_sha256: str,
        elapsed_milliseconds: int,
        error: str | None,
    ) -> "LeanVerifierReceipt":
        frozen_request_sha256 = _sha256_hex(
            frozen_request_sha256, "frozen_request_sha256"
        )
        attempt_result_sha256 = _sha256_hex(
            attempt_result_sha256, "attempt_result_sha256"
        )
        run_id = _token(run_id, "run_id")
        problem_id = _content_address(problem_id, "problem_id")
        arm = _arm(arm)
        attempt = _attempt(attempt)
        candidate_artifact_id = _content_address(
            candidate_artifact_id, "candidate_artifact_id"
        )
        runtime_sha256 = _sha256_hex(runtime_sha256, "runtime_sha256")
        if type(status) is VerifierStatus:
            parsed_status = status
        elif type(status) is str:
            try:
                parsed_status = VerifierStatus(status)
            except ValueError as exc:
                raise ValueError(f"unknown verifier status: {status!r}") from exc
        else:
            raise ValueError(f"unknown verifier status: {status!r}")
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise TypeError("command must be an ordered sequence of arguments")
        command = tuple(_token(value, "command[]") for value in command)
        if not command:
            raise ValueError("command must contain at least one argument")
        if returncode is not None and type(returncode) is not int:
            raise ValueError("returncode must be an exact integer or null")
        stdout_sha256 = _sha256_hex(stdout_sha256, "stdout_sha256")
        stderr_sha256 = _sha256_hex(stderr_sha256, "stderr_sha256")
        if type(elapsed_milliseconds) is not int or elapsed_milliseconds < 0:
            raise ValueError(
                "elapsed_milliseconds must be a non-negative integer"
            )
        if parsed_status is VerifierStatus.PASS:
            if returncode != 0 or error is not None:
                raise ValueError("PASS receipt requires returncode=0 and no error")
        elif parsed_status is VerifierStatus.FAIL:
            if returncode is None or returncode == 0 or error is not None:
                raise ValueError(
                    "FAIL receipt requires a nonzero returncode and no error"
                )
        else:
            if returncode is not None:
                raise ValueError(
                    f"{parsed_status.value} receipt requires returncode=null"
                )
            error = _message(error, "error")
        return super().__new__(
            cls,
            frozen_request_sha256,
            attempt_result_sha256,
            run_id,
            problem_id,
            arm,
            attempt,
            candidate_artifact_id,
            runtime_sha256,
            parsed_status,
            command,
            returncode,
            stdout_sha256,
            stderr_sha256,
            elapsed_milliseconds,
            error,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("LeanVerifierReceipt may not be subclassed")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "arm": self.arm.value,
            "attempt": self.attempt,
            "attempt_result_sha256": self.attempt_result_sha256,
            "candidate_artifact_id": self.candidate_artifact_id,
            "command": list(self.command),
            "elapsed_milliseconds": self.elapsed_milliseconds,
            "error": self.error,
            "frozen_request_sha256": self.frozen_request_sha256,
            "problem_id": self.problem_id,
            "returncode": self.returncode,
            "run_id": self.run_id,
            "runtime_sha256": self.runtime_sha256,
            "status": self.status.value,
            "stderr_sha256": self.stderr_sha256,
            "stdout_sha256": self.stdout_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        return _canonical_sha256(self._identity_payload())

    def to_mapping(self) -> dict[str, object]:
        raw = self._identity_payload()
        raw["receipt_sha256"] = self.receipt_sha256
        return raw

    @classmethod
    def from_verifier_result(
        cls,
        *,
        request: FrozenProblemRequest,
        attempt_result: AttemptResult,
        verifier_result: VerifierResult,
    ) -> "LeanVerifierReceipt":
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        if type(attempt_result) is not AttemptResult:
            raise TypeError("attempt_result must be an exact AttemptResult")
        if type(verifier_result) is not VerifierResult:
            raise TypeError("verifier_result must be an exact VerifierResult")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())
        attempt_result = AttemptResult.from_mapping(attempt_result.to_mapping())
        attempt_result.validate_for(request)
        if attempt_result.status is not AttemptStatus.ANSWERED:
            raise ValueError("only an ANSWERED attempt may produce a verifier receipt")
        stdout = _message(verifier_result.stdout, "verifier stdout") if verifier_result.stdout else ""
        stderr = _message(verifier_result.stderr, "verifier stderr") if verifier_result.stderr else ""
        return cls(
            frozen_request_sha256=request.frozen_request_sha256,
            attempt_result_sha256=attempt_result.attempt_result_sha256,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
            candidate_artifact_id=attempt_result.response_artifact.artifact_id,
            runtime_sha256=request.runtime_sha256,
            status=verifier_result.status,
            command=verifier_result.command,
            returncode=verifier_result.returncode,
            stdout_sha256=sha256(stdout.encode("utf-8")).hexdigest(),
            stderr_sha256=sha256(stderr.encode("utf-8")).hexdigest(),
            elapsed_milliseconds=verifier_result.elapsed_milliseconds,
            error=verifier_result.error,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LeanVerifierReceipt":
        expected = {
            "arm",
            "attempt",
            "attempt_result_sha256",
            "candidate_artifact_id",
            "command",
            "elapsed_milliseconds",
            "error",
            "frozen_request_sha256",
            "problem_id",
            "receipt_sha256",
            "returncode",
            "run_id",
            "runtime_sha256",
            "status",
            "stderr_sha256",
            "stdout_sha256",
        }
        if set(raw) != expected:
            raise ValueError(
                f"verifier receipt fields must be exactly {sorted(expected)}"
            )
        receipt = cls(
            frozen_request_sha256=raw["frozen_request_sha256"],
            attempt_result_sha256=raw["attempt_result_sha256"],
            run_id=raw["run_id"],
            problem_id=raw["problem_id"],
            arm=raw["arm"],
            attempt=raw["attempt"],
            candidate_artifact_id=raw["candidate_artifact_id"],
            runtime_sha256=raw["runtime_sha256"],
            status=raw["status"],
            command=raw["command"],
            returncode=raw["returncode"],
            stdout_sha256=raw["stdout_sha256"],
            stderr_sha256=raw["stderr_sha256"],
            elapsed_milliseconds=raw["elapsed_milliseconds"],
            error=raw["error"],
        )
        if raw["receipt_sha256"] != receipt.receipt_sha256:
            raise ValueError("receipt_sha256 does not match receipt fields")
        return receipt

    def validate_for(
        self,
        request: FrozenProblemRequest,
        attempt_result: AttemptResult,
    ) -> None:
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        if type(attempt_result) is not AttemptResult:
            raise TypeError("attempt_result must be an exact AttemptResult")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())
        attempt_result = AttemptResult.from_mapping(attempt_result.to_mapping())
        receipt = LeanVerifierReceipt.from_mapping(self.to_mapping())
        attempt_result.validate_for(request)
        if attempt_result.status is not AttemptStatus.ANSWERED:
            raise ValueError("verifier receipt requires an ANSWERED attempt")
        expected = (
            request.frozen_request_sha256,
            attempt_result.attempt_result_sha256,
            request.run_id,
            request.problem_id,
            request.arm,
            request.attempt,
            attempt_result.response_artifact.artifact_id,
            request.runtime_sha256,
        )
        actual = (
            receipt.frozen_request_sha256,
            receipt.attempt_result_sha256,
            receipt.run_id,
            receipt.problem_id,
            receipt.arm,
            receipt.attempt,
            receipt.candidate_artifact_id,
            receipt.runtime_sha256,
        )
        if actual != expected:
            raise ValueError(
                "verifier receipt does not match request and attempt result"
            )


__all__ = [
    "AttemptResult",
    "AttemptStatus",
    "FrozenProblemRequest",
    "LeanVerifierReceipt",
]
