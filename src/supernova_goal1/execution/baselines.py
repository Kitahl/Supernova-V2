from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Callable

from ..artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from ..contracts import Arm
from ..cost import ArmCostTrace, CostEvent, ExpectedCostEvent
from ..dispatch import (
    CompletionPayload,
    CompletionRecord,
    CompletionSigner,
    DispatchAuthority,
    DispatchEntry,
    DispatchManifest,
)
from ..verifier import VerifierResult, VerifierStatus
from .common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)


@dataclass(frozen=True)
class ModelAttemptObservation:
    """Visible terminal output bound to one preregistered scheduled-chat dispatch.

    This is evidence for the frozen observable proxy, not provider-token or hidden
    model-compute telemetry.
    """

    dispatch_id: str
    response_utf8: bytes
    status: AttemptStatus
    error: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.dispatch_id) is not str
            or len(self.dispatch_id) != 64
            or any(char not in "0123456789abcdef" for char in self.dispatch_id)
        ):
            raise ValueError("dispatch_id must be 64 lowercase hexadecimal characters")
        if type(self.response_utf8) is not bytes:
            raise TypeError("response_utf8 must be exact bytes")
        try:
            self.response_utf8.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("response_utf8 must be valid UTF-8") from exc
        try:
            status = AttemptStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown attempt status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if status is AttemptStatus.ANSWERED:
            if not self.response_utf8:
                raise ValueError("ANSWERED observation requires visible response bytes")
            if self.error is not None:
                raise ValueError("ANSWERED observation cannot carry error")
        elif status is AttemptStatus.NO_ANSWER:
            if self.error is not None:
                raise ValueError("NO_ANSWER observation cannot carry error")
        else:
            if type(self.error) is not str or not self.error.strip():
                raise ValueError("ERROR observation requires a non-empty error")


@dataclass(frozen=True)
class BaselineDispatch:
    """Read-only preregistered identity supplied to the external model port."""

    request: FrozenProblemRequest
    entry: DispatchEntry
    expected_events: tuple[ExpectedCostEvent, ...]

    def __post_init__(self) -> None:
        if type(self.request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        if type(self.entry) is not DispatchEntry:
            raise TypeError("entry must be an exact DispatchEntry")
        if type(self.expected_events) is not tuple or not all(
            type(event) is ExpectedCostEvent for event in self.expected_events
        ):
            raise TypeError("expected_events must contain exact ExpectedCostEvent values")
        request = FrozenProblemRequest.from_mapping(self.request.to_mapping())
        entry = DispatchEntry.create(
            run_id=self.entry.run_id,
            sequence=self.entry.sequence,
            problem_id=self.entry.problem_id,
            arm=self.entry.arm,
            attempt_index=self.entry.attempt_index,
            request_sha256=self.entry.request_sha256,
            completion_verifier_sha256=self.entry.completion_verifier_sha256,
            predecessor_sha256=self.entry.predecessor_sha256,
        )
        if (
            entry.dispatch_id != self.entry.dispatch_id
            or entry.entry_sha256 != self.entry.entry_sha256
        ):
            raise ValueError("entry no longer matches its preregistered identity")
        if (
            entry.run_id,
            entry.problem_id,
            entry.arm,
            entry.attempt_index,
            entry.request_sha256,
        ) != (
            request.run_id,
            request.problem_id,
            request.arm,
            request.attempt,
            request.frozen_request_sha256,
        ):
            raise ValueError("dispatch entry does not match frozen request")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "entry", entry)
        object.__setattr__(
            self,
            "expected_events",
            tuple(
                ExpectedCostEvent(
                    event.event_id,
                    event.kind,
                    event.model_usage_basis,
                )
                for event in self.expected_events
            ),
        )


@dataclass(frozen=True)
class BaselineExecution:
    """Signed completion plus the exact manifest and complete-cost evidence."""

    manifest: DispatchManifest
    completion: CompletionRecord
    cost_trace: ArmCostTrace

    def __post_init__(self) -> None:
        if type(self.manifest) is not DispatchManifest:
            raise TypeError("manifest must be an exact DispatchManifest")
        if type(self.completion) is not CompletionRecord:
            raise TypeError("completion must be an exact CompletionRecord")
        if type(self.cost_trace) is not ArmCostTrace:
            raise TypeError("cost_trace must be an exact ArmCostTrace")
        matches = [
            entry
            for entry in self.manifest.entries
            if (
                entry.dispatch_id == self.completion.dispatch_id
                and entry.entry_sha256 == self.completion.entry_sha256
            )
        ]
        if len(matches) != 1:
            raise ValueError("completion is not bound to exactly one manifest entry")
        entry = matches[0]
        request = self.completion.payload.request
        if (
            entry.run_id,
            entry.problem_id,
            entry.arm,
            entry.attempt_index,
            entry.request_sha256,
        ) != (
            request.run_id,
            request.problem_id,
            request.arm,
            request.attempt,
            request.frozen_request_sha256,
        ):
            raise ValueError("completion request does not match its manifest entry")
        if request.arm is not self.cost_trace.arm:
            raise ValueError("completion and cost trace arms do not match")
        if not self.cost_trace.coverage_complete:
            raise ValueError("baseline execution cost coverage is incomplete")
        if not self.cost_trace.measurements_complete:
            raise ValueError("baseline execution cost measurements are incomplete")


ModelCall = Callable[[BaselineDispatch, bytes], ModelAttemptObservation]
VerifierCall = Callable[[BaselineDispatch, bytes], VerifierResult]


def _elapsed_milliseconds(start_ns: int) -> int:
    return max(0, (monotonic_ns() - start_ns) // 1_000_000)


def _event_id(request: FrozenProblemRequest, suffix: str) -> str:
    return f"{request.frozen_request_sha256}:{suffix}"


def _safe_model_call(
    model_call: ModelCall,
    dispatch: BaselineDispatch,
    request_utf8: bytes,
) -> ModelAttemptObservation:
    try:
        observation = model_call(dispatch, request_utf8)
    except Exception as exc:
        return ModelAttemptObservation(
            dispatch_id=dispatch.entry.dispatch_id,
            response_utf8=b"",
            status=AttemptStatus.ERROR,
            error=f"model_call raised {type(exc).__name__}",
        )
    if type(observation) is not ModelAttemptObservation:
        return ModelAttemptObservation(
            dispatch_id=dispatch.entry.dispatch_id,
            response_utf8=b"",
            status=AttemptStatus.ERROR,
            error="model_call returned a non-ModelAttemptObservation value",
        )
    if observation.dispatch_id != dispatch.entry.dispatch_id:
        return ModelAttemptObservation(
            dispatch_id=dispatch.entry.dispatch_id,
            response_utf8=b"",
            status=AttemptStatus.ERROR,
            error="model_call returned evidence for a different dispatch",
        )
    return ModelAttemptObservation(
        dispatch_id=observation.dispatch_id,
        response_utf8=observation.response_utf8,
        status=observation.status,
        error=observation.error,
    )


def _safe_verifier_call(
    verifier_call: VerifierCall,
    dispatch: BaselineDispatch,
    candidate_utf8: bytes,
) -> VerifierResult:
    try:
        result = verifier_call(dispatch, candidate_utf8)
    except Exception as exc:
        return VerifierResult(
            status=VerifierStatus.ERROR,
            command=("verifier-port-error",),
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=0,
            error=f"verifier_call raised {type(exc).__name__}",
        )
    if type(result) is not VerifierResult:
        return VerifierResult(
            status=VerifierStatus.ERROR,
            command=("verifier-port-error",),
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=0,
            error="verifier_call returned a non-VerifierResult value",
        )
    return result


def _execute_baseline_attempt(
    *,
    expected_arm: Arm,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    request_utf8: bytes,
    model_call: ModelCall,
    verifier_call: VerifierCall,
) -> BaselineExecution:
    if type(authority) is not DispatchAuthority:
        raise TypeError("authority must be an exact DispatchAuthority")
    if type(manifest) is not DispatchManifest:
        raise TypeError("manifest must be an exact DispatchManifest")
    if type(request) is not FrozenProblemRequest:
        raise TypeError("request must be an exact FrozenProblemRequest")
    if type(request_utf8) is not bytes:
        raise TypeError("request_utf8 must be exact bytes")
    if not callable(model_call) or not callable(verifier_call):
        raise TypeError("model_call and verifier_call must be callable")

    request = FrozenProblemRequest.from_mapping(request.to_mapping())
    if request.arm is not expected_arm:
        raise ValueError(
            f"{expected_arm.value} adapter cannot execute {request.arm.value} request"
        )
    if request.model_usage_basis != "visible_utf8_bytes":
        raise ValueError(
            "scheduled-chat baseline adapters require visible_utf8_bytes usage basis"
        )
    if not request.request_artifact.verifies(request_utf8):
        raise ValueError("request_utf8 does not match frozen request artifact")

    orchestration_ms = 0
    started = monotonic_ns()
    expected_events = (
        ExpectedCostEvent.scheduled_chat_model_call(_event_id(request, "model")),
        ExpectedCostEvent.verifier(_event_id(request, "verifier")),
        ExpectedCostEvent.orchestration(_event_id(request, "orchestration")),
    )
    signer = CompletionSigner.generate()
    updated_manifest = authority.register(
        manifest,
        request=request,
        completion_verifier_sha256=signer.public_commitment,
    )
    registered_entry = updated_manifest.entries[-1]
    dispatch = BaselineDispatch(
        request=request,
        entry=registered_entry,
        expected_events=expected_events,
    )
    orchestration_ms += _elapsed_milliseconds(started)

    observation = _safe_model_call(model_call, dispatch, request_utf8)

    started = monotonic_ns()
    response_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        observation.response_utf8,
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
        response_artifact=response_artifact,
        status=observation.status,
        error=observation.error,
    )
    orchestration_ms += _elapsed_milliseconds(started)

    verifier_result: VerifierResult | None = None
    receipt: LeanVerifierReceipt | None = None
    if observation.status is AttemptStatus.ANSWERED:
        verifier_result = _safe_verifier_call(
            verifier_call,
            dispatch,
            observation.response_utf8,
        )
        started = monotonic_ns()
        try:
            receipt = LeanVerifierReceipt.from_verifier_result(
                request=request,
                attempt_result=attempt_result,
                verifier_result=verifier_result,
            )
        except (TypeError, ValueError) as exc:
            verifier_result = VerifierResult(
                status=VerifierStatus.ERROR,
                command=("verifier-port-error",),
                returncode=None,
                stdout="",
                stderr="",
                elapsed_milliseconds=0,
                error=f"invalid verifier result: {type(exc).__name__}",
            )
            receipt = LeanVerifierReceipt.from_verifier_result(
                request=request,
                attempt_result=attempt_result,
                verifier_result=verifier_result,
            )
        orchestration_ms += _elapsed_milliseconds(started)

    started = monotonic_ns()
    payload = CompletionPayload(request, attempt_result, receipt)
    completion = signer.complete(entry=registered_entry, payload=payload)
    orchestration_ms += _elapsed_milliseconds(started)

    events = (
        CostEvent.scheduled_chat_model_call(
            expected_events[0].event_id,
            request_utf8=request_utf8,
            response_utf8=observation.response_utf8,
        ),
        CostEvent.verifier(
            expected_events[1].event_id,
            milliseconds=(
                0
                if verifier_result is None
                else verifier_result.elapsed_milliseconds
            ),
        ),
        CostEvent.orchestration(
            expected_events[2].event_id,
            milliseconds=orchestration_ms,
        ),
    )
    trace = ArmCostTrace.from_events(
        request.arm,
        events,
        expected_events=expected_events,
        accounting_complete=True,
    )
    return BaselineExecution(updated_manifest, completion, trace)


def execute_ordinary(
    *,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    request_utf8: bytes,
    model_call: ModelCall,
    verifier_call: VerifierCall,
) -> BaselineExecution:
    """Execute one preregistered ordinary-solver attempt."""

    return _execute_baseline_attempt(
        expected_arm=Arm.ORDINARY,
        authority=authority,
        manifest=manifest,
        request=request,
        request_utf8=request_utf8,
        model_call=model_call,
        verifier_call=verifier_call,
    )


def execute_portfolio_attempt(
    *,
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    request: FrozenProblemRequest,
    request_utf8: bytes,
    model_call: ModelCall,
    verifier_call: VerifierCall,
) -> BaselineExecution:
    """Execute one preregistered portfolio attempt under the observable proxy.

    Call once per frozen portfolio request. Direct replay of an observation from
    another dispatch is rejected. The trusted host remains responsible for fresh
    scheduled-chat state because provider-hidden work is not locally observable.
    """

    return _execute_baseline_attempt(
        expected_arm=Arm.PORTFOLIO,
        authority=authority,
        manifest=manifest,
        request=request,
        request_utf8=request_utf8,
        model_call=model_call,
        verifier_call=verifier_call,
    )


__all__ = [
    "BaselineDispatch",
    "BaselineExecution",
    "ModelAttemptObservation",
    "execute_ordinary",
    "execute_portfolio_attempt",
]
