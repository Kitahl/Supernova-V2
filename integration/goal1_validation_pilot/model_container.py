"""Bounded Docker lifecycle for the non-credit model pilot.

The frozen executor's ``runtime/goal1_hermetic_executor/main.go`` defines
``llamaServerReadyTimeout = 120 * time.Second`` and
``llamaServerRequestTimeout = 300 * time.Second``. ``runLlamaServerSession``
waits for readiness before beginning the completion request. The attach budget
therefore covers their sum, plus a policy allowance for startup/exit/transport.
These are configured limits, not observations of internal phase durations. The
executor does not emit those durations. This does not change any Lean timeout.

The total budget is the sum of the configured subprocess timeouts, including
failure diagnostics and cleanup. OS process creation/scheduling and local Python
work are not hard real-time bounded by subprocess.run's timeout mechanism.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

MODEL_READINESS_LIMIT_SECONDS = 120
MODEL_HTTP_REQUEST_LIMIT_SECONDS = 300
MODEL_ATTACH_ALLOWANCE_SECONDS = 30
MODEL_TIMEOUT_SECONDS = (
    MODEL_READINESS_LIMIT_SECONDS
    + MODEL_HTTP_REQUEST_LIMIT_SECONDS
    + MODEL_ATTACH_ALLOWANCE_SECONDS
)
DOCKER_SETUP_TIMEOUT_SECONDS = 30
DOCKER_DIAGNOSTIC_TIMEOUT_SECONDS = 10
DOCKER_CLEANUP_TIMEOUT_SECONDS = 30
MODEL_LIFECYCLE_BUDGET_SECONDS = (
    3 * DOCKER_SETUP_TIMEOUT_SECONDS
    + MODEL_TIMEOUT_SECONDS
    + DOCKER_DIAGNOSTIC_TIMEOUT_SECONDS
    + DOCKER_CLEANUP_TIMEOUT_SECONDS
)
MAX_DIAGNOSTIC_EXCERPT_BYTES = 4096
MODEL_TIMEOUT_PROVENANCE = (
    "runtime/goal1_hermetic_executor/main.go: llamaServerReadyTimeout (120s), "
    "llamaServerRequestTimeout (300s), sequential in runLlamaServerSession; "
    "30s attach allowance and Docker command limits are pilot policy, "
    "not measured internal phase timings or Lean limits"
)


def model_lifecycle_budget() -> dict[str, Any]:
    return {
        "readiness_limit_seconds": MODEL_READINESS_LIMIT_SECONDS,
        "http_request_limit_seconds": MODEL_HTTP_REQUEST_LIMIT_SECONDS,
        "attach_allowance_seconds": MODEL_ATTACH_ALLOWANCE_SECONDS,
        "attach_timeout_seconds": MODEL_TIMEOUT_SECONDS,
        "setup_command_timeout_seconds": DOCKER_SETUP_TIMEOUT_SECONDS,
        "setup_command_count": 3,
        "diagnostic_timeout_seconds": DOCKER_DIAGNOSTIC_TIMEOUT_SECONDS,
        "cleanup_timeout_seconds": DOCKER_CLEANUP_TIMEOUT_SECONDS,
        "subprocess_timeout_sum_seconds": MODEL_LIFECYCLE_BUDGET_SECONDS,
        "internal_phase_timing": "NOT_OBSERVED_BY_FROZEN_EXECUTOR",
        "provenance": MODEL_TIMEOUT_PROVENANCE,
    }


@dataclass(frozen=True)
class ModelContainerObservation:
    raw_completion: bytes
    completion: bytes
    adaptation_rule: str
    elapsed_milliseconds: int
    image_id: str
    stderr: str
    teardown_observed: bool


def _safe_excerpt(raw: bytes, limit: int = MAX_DIAGNOSTIC_EXCERPT_BYTES) -> str:
    # Escape terminal controls, including ESC and C1; keep ordinary whitespace.
    decoded = raw[:limit].decode("utf-8", "replace")
    safe = "".join(
        char if char.isprintable() or char in "\n\r\t" else f"\\u{ord(char):04x}"
        for char in decoded
    )
    return safe.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _output_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else value.encode("utf-8", "replace")


@dataclass(frozen=True)
class CapturedOutput:
    """Counts/hashes cover captured bytes, not any unreceived stream remainder."""

    byte_count: int
    sha256: str
    excerpt: str
    excerpt_truncated: bool

    @classmethod
    def from_raw(cls, value: bytes | str | None) -> CapturedOutput:
        raw = _output_bytes(value)
        excerpt = _safe_excerpt(raw)
        return cls(
            byte_count=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            excerpt=excerpt,
            excerpt_truncated=(
                len(raw) > MAX_DIAGNOSTIC_EXCERPT_BYTES
                or len(_safe_excerpt(raw, len(raw) * 6 + 1).encode("utf-8"))
                > MAX_DIAGNOSTIC_EXCERPT_BYTES
            ),
        )


@dataclass(frozen=True)
class DockerCommandObservation:
    stage: str
    timeout_seconds: int
    elapsed_milliseconds: int
    returncode: int | None
    timed_out: bool
    stdout: CapturedOutput
    stderr: CapturedOutput


@dataclass(frozen=True)
class ModelContainerDiagnostic:
    failure_stage: str
    failure_kind: str
    message: str
    elapsed_milliseconds: int
    attach_elapsed_milliseconds: int | None
    image_id: str | None
    container_id: str | None
    container_name: str | None
    docker_state: dict[str, Any] | None
    docker_state_observed_stage: str | None
    docker_state_error: str | None
    teardown_observed: bool
    teardown_error: str | None
    commands: tuple[DockerCommandObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema"] = "supernova.goal1.model-container-diagnostic.v1"
        value["timeout_budget"] = model_lifecycle_budget()
        return value


class ModelContainerError(RuntimeError):
    """A failed attempt with bounded evidence; it is never a model success."""

    def __init__(self, diagnostic: ModelContainerDiagnostic):
        self.diagnostic = diagnostic
        # Console errors deliberately contain no captured model/runtime output.
        super().__init__(
            f"model container {diagnostic.failure_stage}: "
            f"{diagnostic.failure_kind}; elapsed "
            f"{diagnostic.elapsed_milliseconds}ms; "
            f"teardown_observed={diagnostic.teardown_observed}"
        )


class _StepFailure(RuntimeError):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def _run(
    argv: Sequence[str], *, input_bytes: bytes | None = None, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv), input=input_bytes, capture_output=True, check=False,
        shell=False, timeout=timeout,
    )


def _inspect_object(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise ValueError("Docker inspect returned an unexpected object")
    return value[0]


def _docker_state(item: dict[str, Any]) -> dict[str, Any] | None:
    state = item.get("State")
    if type(state) is not dict:
        return None
    result = {}
    for key in (
        "Status", "Running", "Paused", "Restarting", "OOMKilled", "Dead",
        "ExitCode", "Error", "StartedAt", "FinishedAt",
    ):
        value = state.get(key)
        if type(value) in (bool, int):
            result[key] = value
        elif type(value) is str:
            result[key] = _safe_excerpt(value.encode("utf-8"), 1024)
    return result


def execute_model_container(
    image: str,
    prompt: bytes,
    *,
    parse_generation_frame: Callable[[bytes], bytes],
    adapt_completion: Callable[[bytes], tuple[bytes, str]],
    run_command: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> ModelContainerObservation:
    """Run the frozen image, capturing failures before bounded force removal.

    ``run_command`` is injectable for offline fault tests and follows ``_run``'s
    keyword interface. Only host-observed command/total durations are recorded.
    A unique Docker name permits cleanup even if create times out before giving
    us an ID. Failure to observe cleanup always prevents a success result.
    """

    runner = _run if run_command is None else run_command
    started = time.monotonic_ns()
    commands: list[DockerCommandObservation] = []
    stage = "IMAGE_INSPECT"
    image_id = container_id = container_name = None
    docker_state = None
    docker_state_observed_stage = None
    docker_state_error = teardown_error = None
    teardown = False
    failure: Exception | None = None
    failure_stage = stage
    cleanup_required = False

    def invoke(
        command_stage: str, argv: list[str], timeout: int,
        *, input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command_started = time.monotonic_ns()
        stdout = stderr = b""
        returncode = None
        timed_out = False
        try:
            result = runner(argv, input_bytes=input_bytes, timeout=timeout)
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
            if returncode != 0:
                raise _StepFailure("COMMAND_FAILED", f"{command_stage} exited {returncode}")
            return result
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout, exc.stderr
            timed_out = True
            raise _StepFailure("TIMEOUT", f"{command_stage} exceeded {timeout} seconds") from exc
        finally:
            commands.append(DockerCommandObservation(
                stage=command_stage,
                timeout_seconds=timeout,
                elapsed_milliseconds=max(0, (time.monotonic_ns() - command_started) // 1_000_000),
                returncode=returncode,
                timed_out=timed_out,
                stdout=CapturedOutput.from_raw(stdout),
                stderr=CapturedOutput.from_raw(stderr),
            ))

    def capture_failure_state() -> None:
        nonlocal docker_state, docker_state_observed_stage, docker_state_error
        try:
            item = _inspect_object(invoke(
                "FAILURE_INSPECT", ["docker", "inspect", container_name],
                DOCKER_DIAGNOSTIC_TIMEOUT_SECONDS,
            ).stdout)
            observed_state = _docker_state(item)
            if observed_state is None:
                docker_state_error = "Docker inspect omitted State"
            else:
                docker_state = observed_state
                docker_state_observed_stage = "FAILURE_INSPECT"
        except Exception as exc:  # noqa: BLE001 - retain diagnostic failures without skipping cleanup
            docker_state_error = _safe_excerpt(str(exc).encode("utf-8"), 1024)

    try:
        inspected = _inspect_object(invoke(
            stage, ["docker", "image", "inspect", image], DOCKER_SETUP_TIMEOUT_SECONDS,
        ).stdout)
        image_id = inspected.get("Id")
        if type(image_id) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            image_id = None
            raise ValueError("model image lacks an immutable local image id")

        stage = "CONTAINER_CREATE"
        container_name = "supernova-pilot-" + uuid.uuid4().hex
        cleanup_required = True
        create = invoke(stage, [
            "docker", "create", "--pull", "never", "--name", container_name,
            "--network", "none", "--read-only", "--init", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
            "--ipc", "none", "--user", "65532:65532", "--memory",
            str(4 * 1024 * 1024 * 1024), "--cpus", "2", "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=268435456", "--interactive",
            image, "/opt/supernova/executor", "--stdio",
        ], DOCKER_SETUP_TIMEOUT_SECONDS)
        candidate_id = create.stdout.decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", candidate_id):
            raise ValueError("Docker create returned an invalid container id")
        container_id = candidate_id

        stage = "SECURITY_INSPECT"
        item = _inspect_object(invoke(
            stage, ["docker", "inspect", container_id], DOCKER_SETUP_TIMEOUT_SECONDS,
        ).stdout)
        docker_state = _docker_state(item)
        if docker_state is not None:
            docker_state_observed_stage = stage
        host, config = item.get("HostConfig", {}), item.get("Config", {})
        if (
            type(host) is not dict or type(config) is not dict
            or item.get("Image") != image_id
            or host.get("NetworkMode") != "none"
            or host.get("ReadonlyRootfs") is not True
            or sorted(host.get("CapDrop") or []) != ["ALL"]
            or sorted(host.get("SecurityOpt") or []) != ["no-new-privileges:true"]
            or (host.get("Binds") or []) != []
            or (item.get("Mounts") or []) != []
            or config.get("User") != "65532:65532"
        ):
            raise ValueError("model container security configuration drifted")

        stage = "MODEL_ATTACH"
        completed = invoke(
            stage, ["docker", "start", "--attach", "--interactive", container_id],
            MODEL_TIMEOUT_SECONDS, input_bytes=prompt,
        )
        stage = "RESPONSE_PARSE"
        raw_completion = parse_generation_frame(completed.stdout)
        stage = "RESPONSE_ADAPT"
        completion, adaptation_rule = adapt_completion(raw_completion)
    except Exception as exc:  # noqa: BLE001 - lifecycle boundary rethrows every failure as typed evidence
        failure, failure_stage = exc, stage
    finally:
        if cleanup_required:
            # Inspect before removal so OOM/exit/running evidence can survive a
            # timeout. Keep an earlier snapshot if this diagnostic read fails.
            if failure is not None:
                capture_failure_state()
            try:
                invoke(
                    "TEARDOWN", ["docker", "rm", "--force", container_name],
                    DOCKER_CLEANUP_TIMEOUT_SECONDS,
                )
                teardown = True
            except Exception as exc:  # noqa: BLE001 - cleanup failure must prevent a success result
                teardown_error = _safe_excerpt(str(exc).encode("utf-8"), 1024)
                if failure is None:
                    failure, failure_stage = exc, "TEARDOWN"
                    # A first failure at removal still gets its one bounded
                    # diagnostic read, now describing any surviving container.
                    capture_failure_state()

    elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
    if failure is not None:
        attach = next((command for command in commands if command.stage == "MODEL_ATTACH"), None)
        diagnostic = ModelContainerDiagnostic(
            failure_stage=failure_stage,
            failure_kind=failure.kind if isinstance(failure, _StepFailure) else type(failure).__name__,
            message=_safe_excerpt(str(failure).encode("utf-8"), 1024),
            elapsed_milliseconds=elapsed,
            attach_elapsed_milliseconds=None if attach is None else attach.elapsed_milliseconds,
            image_id=image_id,
            container_id=container_id,
            container_name=container_name,
            docker_state=docker_state,
            docker_state_observed_stage=docker_state_observed_stage,
            docker_state_error=docker_state_error,
            teardown_observed=teardown,
            teardown_error=teardown_error,
            commands=tuple(commands),
        )
        raise ModelContainerError(diagnostic) from failure
    return ModelContainerObservation(
        raw_completion=raw_completion,
        completion=completion,
        adaptation_rule=adaptation_rule,
        elapsed_milliseconds=elapsed,
        image_id=image_id,
        stderr=_safe_excerpt(completed.stderr, 4000),
        teardown_observed=teardown,
    )
