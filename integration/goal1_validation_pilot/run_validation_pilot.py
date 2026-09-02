from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.confirmatory_io import render_baseline_prompt
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import DispatchAuthority, DispatchEntry
from supernova_goal1.execution.baselines import (
    BaselineDispatch,
    ModelAttemptObservation,
    execute_ordinary,
)
from supernova_goal1.execution.common import (
    AttemptStatus,
    FrozenProblemRequest,
)
from supernova_goal1.execution.verified_chain import (
    VerifiedChainExecutionAuthority,
    VerifiedChainObservation,
    VerifiedChainObservationKind,
    execute_verified_chain_step,
    render_verified_chain_request,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.production_verifier import (
    FrozenLeanProblemSource,
    ProductionVerifierPort,
    load_frozen_lean_sources,
)
from supernova_goal1.verifier import VerifierStatus
from supernova_goal1.verifier_evidence import (
    HostVerifierSigner,
    VerifierEvidenceStore,
    VerifierSandboxLauncher,
    VerifierSupervisor,
    canonical_bytes,
)

PLAN_PATH = Path(__file__).with_name("PILOT_PLAN.json")
BASELINES_PATH = ROOT / "goal1" / "CONFIRMATORY_BASELINES.json"
VERIFIER_PUBLICATION_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIER_PUBLICATION.json"
MAX_MODEL_OUTPUT_BYTES = 1 << 20
MODEL_TIMEOUT_SECONDS = 900


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def selection_score(seed: str, problem_id: str) -> str:
    if type(seed) is not str or not seed:
        raise ValueError("selection seed must be non-empty")
    if type(problem_id) is not str or not problem_id:
        raise ValueError("problem_id must be non-empty")
    return sha256(seed.encode("utf-8") + b"\0" + problem_id.encode("utf-8"))


def selected_problem_ids(
    problem_ids: Sequence[str], *, seed: str, count: int
) -> tuple[str, ...]:
    exact = tuple(problem_ids)
    if len(exact) != len(set(exact)) or not all(type(value) is str for value in exact):
        raise ValueError("problem_ids must be unique exact strings")
    if type(count) is not int or count < 1 or count > len(exact):
        raise ValueError("selection count is outside the population")
    ordered = sorted(exact, key=lambda value: (selection_score(seed, value), value))
    return tuple(ordered[:count])


def parse_generation_frame(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("executor response is not UTF-8") from exc
    decoder = json.JSONDecoder()
    try:
        frame, end = decoder.raw_decode(text.lstrip())
    except json.JSONDecodeError as exc:
        raise ValueError("executor response is not a JSON frame") from exc
    if text.lstrip()[end:].strip():
        raise ValueError("executor response has trailing transcript bytes")
    if type(frame) is not dict or set(frame) != {
        "completion_utf8",
        "schema",
        "status",
    }:
        raise ValueError("executor response fields changed")
    if frame["schema"] != "supernova.hermetic-generation-response.v1":
        raise ValueError("executor response schema changed")
    completion = frame["completion_utf8"]
    status = frame["status"]
    if type(completion) is not str or status not in {"ANSWERED", "NO_ANSWER"}:
        raise ValueError("executor response types or status changed")
    result = completion.encode("utf-8")
    if len(result) > MAX_MODEL_OUTPUT_BYTES:
        raise ValueError("executor completion exceeded the pilot byte limit")
    if (status == "ANSWERED") != bool(result):
        raise ValueError("executor status contradicts completion bytes")
    return result


def _run(argv: Sequence[str], *, input_bytes: bytes | None = None, timeout: int = 60):
    return subprocess.run(
        list(argv),
        input=input_bytes,
        capture_output=True,
        check=False,
        shell=False,
        timeout=timeout,
    )


def _docker_json(argv: Sequence[str], field: str) -> Any:
    result = _run(argv)
    if result.returncode != 0:
        raise RuntimeError(
            f"{field} failed: {result.stderr.decode('utf-8', 'replace')[:1000]}"
        )
    return json.loads(result.stdout.decode("utf-8"))


@dataclass(frozen=True)
class ModelContainerObservation:
    completion: bytes
    elapsed_milliseconds: int
    image_id: str
    stderr: str
    teardown_observed: bool


def run_model_container(image: str, prompt: bytes) -> ModelContainerObservation:
    inspected = _docker_json(["docker", "image", "inspect", image], "image inspect")
    if type(inspected) is not list or len(inspected) != 1:
        raise RuntimeError("image inspect returned an unexpected object")
    image_id = inspected[0].get("Id")
    if type(image_id) is not str or not image_id.startswith("sha256:"):
        raise RuntimeError("model image lacks an immutable local image id")
    create = _run(
        [
            "docker",
            "create",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--ipc",
            "none",
            "--user",
            "65532:65532",
            "--memory",
            str(4 * 1024 * 1024 * 1024),
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=268435456",
            "--interactive",
            image,
            "/opt/supernova/executor",
            "--stdio",
        ]
    )
    if create.returncode != 0:
        raise RuntimeError(
            "model container create failed: "
            + create.stderr.decode("utf-8", "replace")[:1000]
        )
    container_id = create.stdout.decode("ascii").strip()
    teardown = False
    started = time.monotonic_ns()
    try:
        container = _docker_json(["docker", "inspect", container_id], "container inspect")
        if type(container) is not list or len(container) != 1:
            raise RuntimeError("container inspect returned an unexpected object")
        item = container[0]
        host = item.get("HostConfig", {})
        config = item.get("Config", {})
        if (
            item.get("Image") != image_id
            or host.get("NetworkMode") != "none"
            or host.get("ReadonlyRootfs") is not True
            or sorted(host.get("CapDrop") or []) != ["ALL"]
            or sorted(host.get("SecurityOpt") or []) != ["no-new-privileges:true"]
            or (host.get("Binds") or []) != []
            or (item.get("Mounts") or []) != []
            or config.get("User") != "65532:65532"
        ):
            raise RuntimeError("model container security configuration drifted")
        try:
            completed = _run(
                ["docker", "start", "--attach", "--interactive", container_id],
                input_bytes=prompt,
                timeout=MODEL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"model container exceeded {MODEL_TIMEOUT_SECONDS} seconds"
            ) from exc
        if completed.returncode != 0:
            raise RuntimeError(
                "model executor failed: "
                + completed.stderr.decode("utf-8", "replace")[:2000]
            )
        completion = parse_generation_frame(completed.stdout)
        stderr = completed.stderr.decode("utf-8", "replace")[:4000]
    finally:
        removed = _run(["docker", "rm", "--force", container_id])
        teardown = removed.returncode == 0
    if not teardown:
        raise RuntimeError("model container teardown was not observed")
    return ModelContainerObservation(
        completion=completion,
        elapsed_milliseconds=max(0, (time.monotonic_ns() - started) // 1_000_000),
        image_id=image_id,
        stderr=stderr,
        teardown_observed=True,
    )


def verifier_launcher(plan: dict[str, Any]) -> VerifierSandboxLauncher:
    value = load_object(VERIFIER_PUBLICATION_PATH)
    if value.get("status") != "PUBLISHED_IMMUTABLE":
        raise ValueError("verifier publication is not immutable")
    timing = plan.get("timing_policy")
    if type(timing) is not dict:
        raise ValueError("pilot timing policy is missing")
    timeout_seconds = timing.get("outer_watchdog_seconds")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("pilot outer watchdog must be a positive integer")
    return VerifierSandboxLauncher(
        image_ref=value["image_ref"],
        command=tuple(value["command"]),
        image_environment=tuple(value["image_environment"]),
        container_user=value["container_user"],
        memory_bytes=value["memory_bytes"],
        nano_cpus=value["nano_cpus"],
        pids_limit=value["pids_limit"],
        timeout_seconds=timeout_seconds,
        max_output_bytes=value["max_output_bytes"],
        tmpfs_size_bytes=value["tmpfs_size_bytes"],
        toolchain_lock_sha256=value["toolchain_lock_sha256"],
        project_dependency_lock_sha256=value["project_dependency_lock_sha256"],
        checker_configuration_sha256=value["checker_configuration_sha256"],
        immutable_inputs_sha256=value["immutable_inputs_sha256"],
    )


def frozen_request(
    *,
    source: FrozenLeanProblemSource,
    problem: BenchmarkProblemIdentity,
    arm: Arm,
    request_utf8: bytes,
    plan_sha256: str,
) -> FrozenProblemRequest:
    run_id = "g1-validation-smoke-20260831-v1"
    artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        request_utf8,
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id=run_id,
        problem_id=problem.canonical_id,
        arm=arm,
        attempt=0,
    )
    return FrozenProblemRequest(
        run_id=run_id,
        experiment_id="goal1-validation-pilot-v1",
        problem=problem,
        benchmark_root_sha256=load_object(PLAN_PATH)["benchmark"][
            "benchmark_root_sha256"
        ],
        problem_sha256=source.source_sha256,
        arm=arm,
        attempt=0,
        budget_id="goal1-validation-smoke-budget-v1",
        budget_sha256=sha256(b"goal1-validation-smoke-budget-v1"),
        model_usage_basis="visible_utf8_bytes",
        runtime_sha256=sha256(b"goal1-validation-pilot-requested-runtime-v1"),
        request_artifact=artifact,
        protocol_dispatch_id="dispatch-" + sha256(arm.value.encode("utf-8")),
        confirmatory_manifest_sha256=plan_sha256,
    )


def synthetic_dispatch(
    source: FrozenLeanProblemSource, candidate: bytes, *, attempt: int
) -> BaselineDispatch:
    problem = BenchmarkProblemIdentity(
        "supernova-synthetic-gate", "v1", "validation", source.native_id
    )
    request_bytes = f"synthetic gate {attempt}".encode()
    artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        request_bytes,
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id="g1-validation-synthetic-gates-v1",
        problem_id=problem.canonical_id,
        arm=Arm.ORDINARY,
        attempt=attempt,
    )
    request = FrozenProblemRequest(
        run_id="g1-validation-synthetic-gates-v1",
        experiment_id="goal1-validation-pilot-v1",
        problem=problem,
        benchmark_root_sha256=sha256(b"synthetic-benchmark"),
        problem_sha256=source.source_sha256,
        arm=Arm.ORDINARY,
        attempt=attempt,
        budget_id="synthetic-gate",
        budget_sha256=sha256(b"synthetic-gate"),
        model_usage_basis="visible_utf8_bytes",
        runtime_sha256=sha256(b"synthetic-requested-runtime"),
        request_artifact=artifact,
        protocol_dispatch_id="dispatch-" + sha256(candidate + bytes([attempt])),
        confirmatory_manifest_sha256=sha256(b"synthetic-manifest"),
    )
    entry = DispatchEntry.create(
        run_id=request.run_id,
        sequence=attempt,
        problem_id=request.problem_id,
        arm=request.arm,
        attempt_index=attempt,
        request_sha256=request.frozen_request_sha256,
        completion_verifier_sha256=sha256(b"synthetic-completion-verifier"),
        predecessor_sha256="0" * 64,
    )
    return BaselineDispatch(request=request, entry=entry, expected_events=())


def synthetic_signed_gates(
    supervisor: VerifierSupervisor,
) -> dict[str, object]:
    source_text = "import Mathlib\n\ntheorem supernova_gate : (1 : Nat) + 1 = 2 := by\n"
    source = FrozenLeanProblemSource.from_record(
        {
            "schema_version": 1,
            "problem_id": "supernova_gate",
            "split": "validation",
            "source_id": "synthetic_non_credit_gate",
            "source_record_sha256": sha256(b"synthetic-source-record"),
            "lean_code_sha256": sha256(source_text.encode("utf-8")),
            "lean_code": source_text,
            "informal_prefix": "",
        },
        expected_split="validation",
    )
    problem_id = synthetic_dispatch(source, b"  norm_num\n", attempt=0).request.problem_id
    port = ProductionVerifierPort(
        supervisor,
        {problem_id: source},
        run_spec_id=sha256(b"synthetic-run-spec"),
        execution_authority_sha256=sha256(b"synthetic-execution-authority"),
        protocol_rules_sha256=sha256(b"synthetic-protocol"),
        confirmatory_manifest_sha256=sha256(b"synthetic-manifest"),
    )
    cases = (
        ("benign", b"  norm_num\n", 0, VerifierStatus.PASS),
        ("prose", b"This is prose, not a Lean tactic.\n", 1, VerifierStatus.FAIL),
    )
    result: dict[str, object] = {}
    for name, candidate, attempt, expected in cases:
        dispatch = synthetic_dispatch(source, candidate, attempt=attempt)
        started = time.monotonic_ns()
        verification = port.verify(dispatch, candidate)
        wall_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        result[name] = {
            "expected_status": expected.value,
            "observed_status": verification.result.status.value,
            "record_sha256": verification.record.record_sha256,
            "signed_verdict": verification.record.body["observations"]["verdict"],
            "wall_milliseconds": wall_ms,
            "phase_timings": verification.record.body["observations"][
                "resource_measurements"
            ]["phases"],
        }
        if verification.result.status is not expected:
            raise RuntimeError(f"synthetic {name} gate returned {verification.result.status}")
    return result


def completion_summary(
    *,
    arm: Arm,
    completion: object,
    model_observation: ModelContainerObservation | None,
    verifier_port: ProductionVerifierPort,
) -> dict[str, object]:
    payload = completion.payload
    receipt = payload.verifier_receipt
    binding = verifier_port.bindings_by_dispatch.get(completion.dispatch_id)
    evidence = None
    if binding is not None:
        try:
            record = verifier_port.supervisor.store.read(binding)
            evidence = {
                "elapsed_milliseconds": record.body["observations"][
                    "elapsed_milliseconds"
                ],
                "phase_timings": record.body["observations"][
                    "resource_measurements"
                ]["phases"],
                "record_sha256": record.record_sha256,
                "termination_cause": record.body["observations"]["termination_cause"],
                "verdict": record.body["observations"]["verdict"],
            }
        except (KeyError, ValueError):
            evidence = None
    if model_observation is None:
        raise RuntimeError("model observation is missing for completed dispatch")
    response = model_observation.completion
    if not payload.attempt_result.response_artifact.verifies(response):
        raise RuntimeError("captured model bytes do not match the completed artifact")
    return {
        "arm": arm.value,
        "attempt": 0,
        "completion_status": completion.status.value,
        "attempt_status": payload.attempt_result.status.value,
        "candidate_sha256": sha256(response),
        "candidate_utf8": response.decode("utf-8", "replace"),
        "model_elapsed_milliseconds": (
            None if model_observation is None else model_observation.elapsed_milliseconds
        ),
        "model_image_id": None if model_observation is None else model_observation.image_id,
        "model_stderr": None if model_observation is None else model_observation.stderr,
        "verifier_status": None if receipt is None else receipt.status.value,
        "verifier_evidence": evidence,
    }


def run_smoke(
    *, validation_file: Path, executor_image: str, output_directory: Path
) -> dict[str, object]:
    plan_raw = PLAN_PATH.read_bytes()
    plan = load_object(PLAN_PATH)
    plan_sha = sha256(plan_raw.replace(b"\r\n", b"\n"))
    benchmark = plan["benchmark"]
    sources = load_frozen_lean_sources(
        validation_file,
        expected_file_sha256=benchmark["validation_file_sha256"],
        expected_records=benchmark["validation_records"],
        benchmark=benchmark["name"],
        version=benchmark["version"],
        split=benchmark["allowed_split"],
    )
    chosen_ids = selected_problem_ids(
        [source.native_id for source in sources.values()],
        seed=plan["selection"]["seed"],
        count=5,
    )
    chosen = chosen_ids[0]
    source = next(value for value in sources.values() if value.native_id == chosen)
    problem = BenchmarkProblemIdentity(
        benchmark["name"], benchmark["version"], "validation", chosen
    )

    output_directory.mkdir(parents=True, exist_ok=False)
    signer = HostVerifierSigner(
        issuer_id="goal1-validation-pilot-host",
        signing_key_id="goal1-validation-pilot-ephemeral-key",
        private_key=os.urandom(32),
    )
    launcher = verifier_launcher(plan)
    store = VerifierEvidenceStore(
        output_directory / "verifier-evidence.sqlite3",
        verification_key=signer.public_key,
        expected_signing_key_id=signer.signing_key_id,
        expected_identity=launcher.identity,
    )
    supervisor = VerifierSupervisor(launcher, signer, store)
    gates = synthetic_signed_gates(supervisor)

    verifier_port = ProductionVerifierPort(
        supervisor,
        {problem.canonical_id: source},
        run_spec_id=plan_sha,
        execution_authority_sha256=sha256(b"non-credit-pilot-authority"),
        protocol_rules_sha256=sha256(b"non-credit-pilot-rules"),
        confirmatory_manifest_sha256=plan_sha,
    )
    dispatch_authority = DispatchAuthority(
        str(output_directory / "dispatch.sqlite3"),
        "g1-validation-smoke-20260831-v1",
    )
    manifest = dispatch_authority.current_manifest()
    baselines = load_object(BASELINES_PATH)
    ordinary_prompt = render_baseline_prompt(
        baselines, source, arm=Arm.ORDINARY.value, attempt=0
    )
    ordinary_request = frozen_request(
        source=source,
        problem=problem,
        arm=Arm.ORDINARY,
        request_utf8=ordinary_prompt,
        plan_sha256=plan_sha,
    )
    observations: dict[str, ModelContainerObservation] = {}

    def ordinary_model(dispatch: BaselineDispatch, prompt: bytes):
        observed = run_model_container(executor_image, prompt)
        observations[dispatch.entry.dispatch_id] = observed
        return ModelAttemptObservation(
            dispatch.entry.dispatch_id,
            observed.completion,
            AttemptStatus.ANSWERED if observed.completion else AttemptStatus.NO_ANSWER,
        )

    ordinary = execute_ordinary(
        authority=dispatch_authority,
        manifest=manifest,
        request=ordinary_request,
        request_utf8=ordinary_prompt,
        model_call=ordinary_model,
        verifier_call=verifier_port,
    )
    manifest = ordinary.manifest

    chain_authority = VerifiedChainExecutionAuthority(
        str(output_directory / "verified-chain.sqlite3"), os.urandom(32)
    )
    chain_visible = render_verified_chain_request(
        ordinary_prompt,
        execution_authority=chain_authority,
        admitted_products=(),
        retry_of=None,
    )
    chain_request = frozen_request(
        source=source,
        problem=problem,
        arm=Arm.VERIFIED_CHAIN,
        request_utf8=chain_visible,
        plan_sha256=plan_sha,
    )

    def chain_model(dispatch: BaselineDispatch, prompt: bytes):
        observed = run_model_container(executor_image, prompt)
        observations[dispatch.entry.dispatch_id] = observed
        return VerifiedChainObservation(
            dispatch.entry.dispatch_id,
            (
                VerifiedChainObservationKind.ANSWERED
                if observed.completion
                else VerifiedChainObservationKind.NO_ANSWER
            ),
            observed.completion,
        )

    chain = execute_verified_chain_step(
        authority=dispatch_authority,
        execution_authority=chain_authority,
        manifest=manifest,
        request=chain_request,
        problem_prompt_utf8=ordinary_prompt,
        admitted_products=(),
        retry_of=None,
        model_call=chain_model,
        verifier_call=verifier_port,
    )
    summaries = (
        completion_summary(
            arm=Arm.ORDINARY,
            completion=ordinary.completion,
            model_observation=observations.get(ordinary.completion.dispatch_id),
            verifier_port=verifier_port,
        ),
        completion_summary(
            arm=Arm.VERIFIED_CHAIN,
            completion=chain.baseline.completion,
            model_observation=observations.get(chain.baseline.completion.dispatch_id),
            verifier_port=verifier_port,
        ),
    )
    signed_valids = sum(
        item["verifier_evidence"] is not None
        and item["verifier_evidence"]["verdict"] == "VALID"
        for item in summaries
    )
    all_answered_have_evidence = all(
        item["attempt_status"] != "ANSWERED" or item["verifier_evidence"] is not None
        for item in summaries
    )
    admitted = (
        len(summaries) == 2
        and {item["arm"] for item in summaries} == {"ordinary", "verified_chain"}
        and all(item["model_elapsed_milliseconds"] is not None for item in summaries)
        and all_answered_have_evidence
        and signed_valids >= 1
    )
    report: dict[str, object] = {
        "schema": "supernova.goal1.validation-smoke-report.v1",
        "classification": plan["classification"],
        "scientific_credit": "NONE",
        "countable_attempts": 0,
        "plan_sha256": plan_sha,
        "selection": {
            "algorithm": plan["selection"]["algorithm"],
            "seed": plan["selection"]["seed"],
            "smoke_problem_id": chosen,
            "one_percent_problem_ids_frozen_before_smoke": list(chosen_ids),
        },
        "synthetic_signed_gates": gates,
        "attempts": list(summaries),
        "signed_valid_model_responses": signed_valids,
        "one_percent_admission": "PASS" if admitted else "FAIL",
        "next_action": (
            "STOP_AND_ANALYZE_BEFORE_ONE_PERCENT"
            if admitted
            else "STOP_REPAIR_AND_REPEAT_SMOKE"
        ),
        "verifier_image_ref": launcher.image_ref,
        "verifier_signing_public_key_sha256": sha256(signer.public_key),
    }
    report_path = output_directory / "smoke-report.json"
    report_path.write_bytes(canonical_bytes(report) + b"\n")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-file", type=Path, required=True)
    parser.add_argument("--executor-image", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_smoke(
        validation_file=args.validation_file.resolve(strict=True),
        executor_image=args.executor_image,
        output_directory=args.output_directory.resolve(strict=False),
    )
    print(json.dumps(report, allow_nan=False, indent=2, sort_keys=True))
    return 0 if report["one_percent_admission"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
