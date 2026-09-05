from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from integration.goal1_validation_pilot.model_container import (
    MODEL_TIMEOUT_SECONDS,
    ModelContainerObservation,
    execute_model_container,
    model_lifecycle_budget,
)
from integration.goal1_validation_pilot.pilot_prompt import (
    PILOT_BASELINE_PROMPT_VERSION,
    PILOT_PRODUCT_PROMPT_VERSION,
)
from integration.goal1_validation_pilot.pilot_prompt import (
    render_pilot_baseline_prompt as render_baseline_prompt,
)
from integration.goal1_validation_pilot.pilot_prompt import (
    render_pilot_product_prompt as render_product_prompt,
)
from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.confirmatory_controller import (
    ProductChainArm,
    final_solve_decision,
    product_admission_decision,
)
from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    NO_ANSWER,
    PRODUCT_PREFIX,
    ConfirmatoryResponseKind,
    build_verification_subject,
    classify_baseline_response,
    classify_product_response,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import DispatchAuthority, DispatchEntry
from supernova_goal1.execution.baselines import (
    BaselineDispatch,
    ModelAttemptObservation,
    _execute_baseline_attempt,
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
    ProductionVerification,
    ProductionVerifierPort,
    VerificationSubject,
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
PRODUCT_CONTROLS_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
VERIFIER_PUBLICATION_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIER_PUBLICATION.json"
MAX_MODEL_OUTPUT_BYTES = 1 << 20
COMPLETION_ADAPTER_VERSION = (
    "supernova.non-credit-completion-adapter.v2-preserve-layout"
)
_LEAN_FENCE = re.compile(
    r"(?ims)^\x60{3}(?:lean4|lean|tactics)[ \t]*\r?\n(.*?)^\x60{3}[ \t]*(?:\r?\n|\Z)"
)
_WRAPPER_START = re.compile(
    r"\A\s*(?:theorem|lemma|import|set_option|open|namespace)\b"
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pilot_runtime_provenance() -> dict[str, object]:
    return {
        "completion_adapter_version": COMPLETION_ADAPTER_VERSION,
        "baseline_prompt_version": PILOT_BASELINE_PROMPT_VERSION,
        "product_prompt_version": PILOT_PRODUCT_PROMPT_VERSION,
        "model_lifecycle_budget": model_lifecycle_budget(),
        "model_attach_watchdog_seconds": MODEL_TIMEOUT_SECONDS,
        "model_elapsed_basis": "HOST_FULL_LIFECYCLE_INCLUDING_IMAGE_INSPECTION_AND_CREATE",
        "scientific_credit": "NONE",
    }


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


def adapt_model_completion(
    raw: bytes,
    *,
    theorem_name: str,
    trusted_source: FrozenLeanProblemSource | None = None,
) -> tuple[bytes, str]:
    """Deterministically unwrap only the final complete Lean response fence.

    Preserve tactic layout, including leading spaces and trailing newlines.
    This does not establish proof validity. Rejected wrappers pass through
    unchanged so the hostile verifier still emits evidence for answered output.
    """

    if type(raw) is not bytes:
        raise TypeError("raw completion must be exact bytes")
    if (
        type(theorem_name) is not str
        or not theorem_name
        or theorem_name.strip() != theorem_name
    ):
        raise ValueError("theorem_name must be one exact non-empty token")
    if trusted_source is not None and trusted_source.native_id != theorem_name:
        raise ValueError("trusted source target differs from theorem_name")
    text = raw.decode("utf-8")
    if "\x60\x60\x60" not in text:
        return raw, "RAW_UNCHANGED"
    matches = list(_LEAN_FENCE.finditer(text))
    if not matches or text[matches[-1].end() :].strip():
        return raw, "REJECTED_WRAPPER_RAW_PASSTHROUGH"
    fenced = matches[-1].group(1)
    candidate = fenced.encode("utf-8")
    if trusted_source is not None:
        # Never discover a proof boundary by parsing attacker-shaped Lean with
        # regex. Only discard exact bytes of the already trusted source/header.
        # Comments and strings inside that header cannot move this boundary.
        headers = (
            trusted_source.source.removesuffix(b"\n"),
            trusted_source.theorem_statement + b":= by",
        )
        for header in headers:
            if not candidate.startswith(header):
                continue
            body = candidate[len(header) :]
            if body and body[:1] not in b" \t\r\n":
                return raw, "REJECTED_PROOF_BOUNDARY_RAW_PASSTHROUGH"
            # The actual harness supplies ':= by\n'. Remove only the wrapper's
            # first line break; never dedent or strip the remaining tactic body.
            body = re.sub(rb"\A[ \t]*\r?\n", b"", body, count=1)
            if not body.strip():
                return raw, "REJECTED_EMPTY_BODY_RAW_PASSTHROUGH"
            return body, "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY"
    if _WRAPPER_START.match(fenced):
        return raw, "REJECTED_DECLARATION_RAW_PASSTHROUGH"
    # Do not scan tactic comments/strings for declaration words. Any remaining
    # commands, malformed syntax or forbidden axioms belong to the verifier.
    return (
        (candidate, "FINAL_LEAN_FENCE_TACTIC_BODY")
        if candidate.strip()
        else (raw, "REJECTED_EMPTY_FENCE_RAW_PASSTHROUGH")
    )


def adapt_product_completion(raw: bytes) -> tuple[bytes, str]:
    """Preserve the frozen product protocol; unwrap only one complete Lean fence.

    An unmarked tactic body is deliberately not reclassified as a final answer.
    That would change treatment compliance instead of measuring it.
    """

    if type(raw) is not bytes:
        raise TypeError("raw completion must be exact bytes")
    if raw == NO_ANSWER or raw.startswith((PRODUCT_PREFIX, FINAL_PREFIX)):
        return raw, "EXACT_PRODUCT_PROTOCOL_RESPONSE"
    text = raw.decode("utf-8")
    if "```" not in text:
        return raw, "RAW_UNCHANGED_PRODUCT_PROTOCOL"
    matches = list(_LEAN_FENCE.finditer(text))
    if not matches or text[matches[-1].end() :].strip():
        return raw, "REJECTED_PRODUCT_WRAPPER_RAW_PASSTHROUGH"
    candidate = matches[-1].group(1).encode("utf-8")
    if candidate == NO_ANSWER or candidate.startswith((PRODUCT_PREFIX, FINAL_PREFIX)):
        return candidate, "FINAL_LEAN_FENCE_PRODUCT_PROTOCOL_RESPONSE"
    return raw, "REJECTED_UNMARKED_PRODUCT_FENCE_RAW_PASSTHROUGH"


def run_model_container(
    image: str,
    prompt: bytes,
    *,
    theorem_name: str,
    response_mode: str = "baseline",
    trusted_source: FrozenLeanProblemSource | None = None,
) -> ModelContainerObservation:
    if response_mode == "baseline":
        adapter = lambda raw: adapt_model_completion(
            raw, theorem_name=theorem_name, trusted_source=trusted_source
        )
    elif response_mode == "product":
        adapter = adapt_product_completion
    else:
        raise ValueError("unknown model response mode")
    return execute_model_container(
        image,
        prompt,
        parse_generation_frame=parse_generation_frame,
        adapt_completion=adapter,
    )


def verifier_launcher(
    plan: dict[str, Any], *, image_ref: str | None = None
) -> VerifierSandboxLauncher:
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
        image_ref=value["image_ref"] if image_ref is None else image_ref,
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
    run_id: str = "g1-validation-smoke-20260831-v1",
    experiment_id: str = "goal1-validation-pilot-v1",
    attempt: int = 0,
    benchmark_root_sha256: str | None = None,
) -> FrozenProblemRequest:
    artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        request_utf8,
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id=run_id,
        problem_id=problem.canonical_id,
        arm=arm,
        attempt=attempt,
    )
    budget_id = f"{experiment_id}-budget-v1"
    protocol_dispatch_id = "dispatch-" + sha256(
        canonical_bytes(
            {
                "arm": arm.value,
                "attempt": attempt,
                "problem_id": problem.canonical_id,
                "run_id": run_id,
            }
        )
    )
    return FrozenProblemRequest(
        run_id=run_id,
        experiment_id=experiment_id,
        problem=problem,
        benchmark_root_sha256=(
            load_object(PLAN_PATH)["benchmark"]["benchmark_root_sha256"]
            if benchmark_root_sha256 is None else benchmark_root_sha256
        ),
        problem_sha256=source.source_sha256,
        arm=arm,
        attempt=attempt,
        budget_id=budget_id,
        budget_sha256=sha256(budget_id.encode("utf-8")),
        model_usage_basis="visible_utf8_bytes",
        runtime_sha256=sha256(b"goal1-validation-pilot-requested-runtime-v1"),
        request_artifact=artifact,
        protocol_dispatch_id=protocol_dispatch_id,
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
    problem_id = synthetic_dispatch(
        source, b"  norm_num\n", attempt=0
    ).request.problem_id
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
            raise RuntimeError(
                f"synthetic {name} gate returned {verification.result.status}"
            )
    return result


def exact_smoke_problem_signed_valid_gate(
    supervisor: VerifierSupervisor,
    source: FrozenLeanProblemSource,
) -> dict[str, object]:
    if source.native_id != "amc12a_2003_p1":
        raise ValueError("the exact smoke proof is bound to amc12a_2003_p1")
    candidate = b"""  have hpoint (k : Nat) : u k = v k + 1 := by
    rw [h\xe2\x82\x80 k, h\xe2\x82\x81 k]
  have hsum :
      (\xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, u k) =
        (\xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, v k) + 2003 := by
    calc
      (\xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, u k) =
          \xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, (v k + 1) := by
            apply Finset.sum_congr rfl
            intro k _
            exact hpoint k
      _ = (\xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, v k) +
          (\xe2\x88\x91 _k \xe2\x88\x88 Finset.range 2003, 1) := by
            rw [Finset.sum_add_distrib]
      _ = (\xe2\x88\x91 k \xe2\x88\x88 Finset.range 2003, v k) + 2003 := by simp
  rw [hsum]
  omega
"""
    dispatch = synthetic_dispatch(source, candidate, attempt=2)
    port = ProductionVerifierPort(
        supervisor,
        {dispatch.request.problem_id: source},
        run_spec_id=sha256(b"exact-smoke-problem-run-spec-v2"),
        execution_authority_sha256=sha256(b"exact-smoke-problem-authority-v2"),
        protocol_rules_sha256=sha256(b"exact-smoke-problem-rules-v2"),
        confirmatory_manifest_sha256=sha256(b"exact-smoke-problem-manifest-v2"),
    )
    started = time.monotonic_ns()
    verification = port.verify(dispatch, candidate)
    wall_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
    if verification.result.status is not VerifierStatus.PASS:
        raise RuntimeError(
            "exact smoke problem known-good proof returned "
            f"{verification.result.status.value}"
        )
    return {
        "candidate_sha256": sha256(candidate),
        "record_sha256": verification.record.record_sha256,
        "source_sha256": source.source_sha256,
        "status": verification.result.status.value,
        "wall_milliseconds": wall_ms,
    }


def completion_summary(
    *,
    arm: Arm,
    completion: object,
    model_observation: ModelContainerObservation | None,
    verifier_port: ProductionVerifierPort,
    model_error_detail: str | None = None,
) -> dict[str, object]:
    payload = completion.payload
    receipt = payload.verifier_receipt
    binding = verifier_port.bindings_by_dispatch.get(completion.dispatch_id)
    evidence = None
    if binding is not None:
        try:
            record = verifier_port.supervisor.store.read(binding)
            blobs = verifier_port.supervisor.store.read_blobs(binding)
            evidence = {
                "elapsed_milliseconds": record.body["observations"][
                    "elapsed_milliseconds"
                ],
                "phase_timings": record.body["observations"]["resource_measurements"][
                    "phases"
                ],
                "record_sha256": record.record_sha256,
                "stderr_bytes": len(blobs.stderr),
                "stderr_sha256": sha256(blobs.stderr),
                "stdout_bytes": len(blobs.stdout),
                "stdout_sha256": sha256(blobs.stdout),
                "termination_cause": record.body["observations"]["termination_cause"],
                "verdict": record.body["observations"]["verdict"],
            }
        except (KeyError, ValueError):
            evidence = None
    response = b"" if model_observation is None else model_observation.completion
    raw_response = (
        b"" if model_observation is None else model_observation.raw_completion
    )
    if not payload.attempt_result.response_artifact.verifies(response):
        raise RuntimeError("captured model bytes do not match the completed artifact")
    return {
        "arm": arm.value,
        "attempt": 0,
        "completion_status": completion.status.value,
        "attempt_status": payload.attempt_result.status.value,
        "candidate_sha256": sha256(response),
        "candidate_utf8": response.decode("utf-8", "replace"),
        "candidate_bytes": len(response),
        "raw_completion_sha256": sha256(raw_response),
        "raw_completion_utf8": raw_response.decode("utf-8", "replace"),
        "raw_completion_bytes": len(raw_response),
        "adaptation_rule": (
            None if model_observation is None else model_observation.adaptation_rule
        ),
        "model_elapsed_milliseconds": (
            None
            if model_observation is None
            else model_observation.elapsed_milliseconds
        ),
        "model_image_id": None
        if model_observation is None
        else model_observation.image_id,
        "model_stderr": None if model_observation is None else model_observation.stderr,
        "model_error": payload.attempt_result.error,
        "model_error_detail": model_error_detail,
        "verifier_status": None if receipt is None else receipt.status.value,
        "verifier_evidence": evidence,
    }


def run_smoke(
    *,
    validation_file: Path,
    executor_image: str,
    output_directory: Path,
    verifier_image_ref: str | None = None,
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
        expected_record_schema_version=benchmark["record_schema_version"],
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
    launcher = verifier_launcher(plan, image_ref=verifier_image_ref)
    store = VerifierEvidenceStore(
        output_directory / "verifier-evidence.sqlite3",
        verification_key=signer.public_key,
        expected_signing_key_id=signer.signing_key_id,
        expected_identity=launcher.identity,
    )
    supervisor = VerifierSupervisor(launcher, signer, store)
    gates = synthetic_signed_gates(supervisor)
    exact_problem_gate = exact_smoke_problem_signed_valid_gate(supervisor, source)

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
    model_errors: dict[str, str] = {}

    def ordinary_model(dispatch: BaselineDispatch, prompt: bytes):
        try:
            observed = run_model_container(
                executor_image,
                prompt,
                theorem_name=source.native_id,
                trusted_source=source,
            )
        except Exception as exc:
            model_errors[dispatch.entry.dispatch_id] = f"{type(exc).__name__}: {exc}"
            raise
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
        try:
            observed = run_model_container(
                executor_image,
                prompt,
                theorem_name=source.native_id,
                trusted_source=source,
            )
        except Exception as exc:
            model_errors[dispatch.entry.dispatch_id] = f"{type(exc).__name__}: {exc}"
            raise
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
            model_error_detail=model_errors.get(ordinary.completion.dispatch_id),
        ),
        completion_summary(
            arm=Arm.VERIFIED_CHAIN,
            completion=chain.baseline.completion,
            model_observation=observations.get(chain.baseline.completion.dispatch_id),
            verifier_port=verifier_port,
            model_error_detail=model_errors.get(chain.baseline.completion.dispatch_id),
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
        "prospective_runtime": pilot_runtime_provenance(),
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
        "exact_smoke_problem_signed_valid_gate": exact_problem_gate,
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
        "verifier_signing_public_key_hex": signer.public_key.hex(),
    }
    report_path = output_directory / "smoke-report.json"
    report_path.write_bytes(canonical_bytes(report) + b"\n")
    return report


def one_percent_schedule(
    problem_ids: Sequence[str],
    *,
    attempts_per_problem_arm: int,
) -> tuple[tuple[int, str, Arm], ...]:
    """Return a frozen balanced round-robin schedule for the non-credit pilot."""

    exact = tuple(problem_ids)
    if len(exact) != len(set(exact)) or not exact:
        raise ValueError("pilot problem_ids must be non-empty unique strings")
    if (
        type(attempts_per_problem_arm) is not int
        or attempts_per_problem_arm < 1
        or attempts_per_problem_arm > 16
    ):
        raise ValueError("pilot attempts per problem arm must be in 1..16")
    result: list[tuple[int, str, Arm]] = []
    canonical = (Arm.ORDINARY, Arm.VERIFIED_CHAIN)
    for attempt in range(attempts_per_problem_arm):
        for problem_index, problem_id in enumerate(exact):
            order = (
                canonical
                if (problem_index + attempt) % 2 == 0
                else tuple(reversed(canonical))
            )
            for arm in order:
                result.append((attempt, problem_id, arm))
    return tuple(result)


def _signed_evidence(
    verification: ProductionVerification | None,
) -> dict[str, object] | None:
    if verification is None:
        return None
    record = verification.record
    observed = record.body["observations"]
    resources = observed["resource_measurements"]
    return {
        "elapsed_milliseconds": observed["elapsed_milliseconds"],
        "phase_timings": resources["phases"],
        "product_parser_admissible": verification.product_parser_admissible,
        "record_sha256": record.record_sha256,
        "stderr_bytes": len(verification.blobs.stderr),
        "stderr_sha256": sha256(verification.blobs.stderr),
        "stdout_bytes": len(verification.blobs.stdout),
        "stdout_sha256": sha256(verification.blobs.stdout),
        "termination_cause": observed["termination_cause"],
        "verdict": observed["verdict"],
    }


def _one_percent_metrics(
    attempts: Sequence[dict[str, object]],
    *,
    problem_ids: Sequence[str],
) -> dict[str, object]:
    exact_attempts = tuple(attempts)
    exact_ids = tuple(problem_ids)
    by_arm: dict[str, dict[str, object]] = {}
    for arm in (Arm.ORDINARY.value, Arm.VERIFIED_CHAIN.value):
        rows = [row for row in exact_attempts if row["arm"] == arm]
        solved_ids = sorted(
            {str(row["problem_id"]) for row in rows if row["final_solved"] is True}
        )
        verdict_counts = {"INVALID": 0, "UNKNOWN": 0, "VALID": 0}
        for row in rows:
            evidence = row["verifier_evidence"]
            if type(evidence) is dict and evidence.get("verdict") in verdict_counts:
                verdict_counts[str(evidence["verdict"])] += 1
        by_arm[arm] = {
            "attempts": len(rows),
            "answered": sum(row["attempt_status"] == "ANSWERED" for row in rows),
            "malformed": sum(row["response_kind"] == "MALFORMED" for row in rows),
            "model_errors": sum(row["attempt_status"] == "ERROR" for row in rows),
            "no_answer": sum(row["attempt_status"] == "NO_ANSWER" for row in rows),
            "signed_verdicts": verdict_counts,
            "solved_problem_count": len(solved_ids),
            "solved_problem_ids": solved_ids,
        }
    chain_rows = [
        row for row in exact_attempts if row["arm"] == Arm.VERIFIED_CHAIN.value
    ]
    product_emissions = sum(
        row["response_kind"] == ConfirmatoryResponseKind.PRODUCT_CANDIDATE.value
        for row in chain_rows
    )
    product_admissions = sum(row["product_admitted"] is True for row in chain_rows)
    exposed_final_attempts = sum(
        row["response_kind"] == ConfirmatoryResponseKind.FINAL_ANSWER.value
        and int(row["admitted_products_visible_before_attempt"]) > 0
        for row in chain_rows
    )
    paired = []
    for problem_id in exact_ids:
        ordinary_solved = any(
            row["problem_id"] == problem_id
            and row["arm"] == Arm.ORDINARY.value
            and row["final_solved"] is True
            for row in exact_attempts
        )
        chain_solved = any(
            row["problem_id"] == problem_id
            and row["arm"] == Arm.VERIFIED_CHAIN.value
            and row["final_solved"] is True
            for row in exact_attempts
        )
        paired.append(
            {
                "problem_id": problem_id,
                "ordinary_best_of_2_solved": ordinary_solved,
                "verified_chain_best_of_2_solved": chain_solved,
            }
        )
    return {
        "by_arm": by_arm,
        "paired_problem_outcomes": paired,
        "product_chain": {
            "admission_rate_given_emission": (
                None
                if product_emissions == 0
                else product_admissions / product_emissions
            ),
            "emission_rate": (
                None if not chain_rows else product_emissions / len(chain_rows)
            ),
            "final_attempts_after_usable_product_exposure": exposed_final_attempts,
            "product_admissions": product_admissions,
            "product_emissions": product_emissions,
        },
    }


def run_one_percent(
    *,
    validation_file: Path,
    executor_image: str,
    output_directory: Path,
    verifier_image_ref: str | None = None,
) -> dict[str, object]:
    """Run exactly 5 x 2 x 2 non-credit attempts and stop with measurements."""

    plan_raw = PLAN_PATH.read_bytes()
    plan = load_object(PLAN_PATH)
    plan_sha = sha256(plan_raw.replace(b"\r\n", b"\n"))
    benchmark = plan["benchmark"]
    stage = plan["stages"]["pilot_1_percent"]
    if (
        stage["attempt_count"] != 20
        or stage["problem_count"] != 5
        or stage["attempts_per_problem_arm"] != 2
        or stage["arms"] != ["ordinary", "verified_chain"]
        or stage["stop_after_report"] is not True
    ):
        raise ValueError("one-percent pilot arithmetic or stop rule changed")
    sources = load_frozen_lean_sources(
        validation_file,
        expected_file_sha256=benchmark["validation_file_sha256"],
        expected_records=benchmark["validation_records"],
        benchmark=benchmark["name"],
        version=benchmark["version"],
        split=benchmark["allowed_split"],
        expected_record_schema_version=benchmark["record_schema_version"],
    )
    chosen_ids = selected_problem_ids(
        [source.native_id for source in sources.values()],
        seed=plan["selection"]["seed"],
        count=stage["problem_count"],
    )
    selected_sources = {
        source.native_id: source
        for source in sources.values()
        if source.native_id in chosen_ids
    }
    if set(selected_sources) != set(chosen_ids):
        raise RuntimeError("frozen pilot selection is absent from validation corpus")
    schedule = one_percent_schedule(
        chosen_ids,
        attempts_per_problem_arm=stage["attempts_per_problem_arm"],
    )
    if len(schedule) != stage["attempt_count"]:
        raise RuntimeError("one-percent schedule does not match frozen attempt count")

    output_directory.mkdir(parents=True, exist_ok=False)
    signer = HostVerifierSigner(
        issuer_id="goal1-validation-pilot-host",
        signing_key_id="goal1-validation-pilot-ephemeral-key",
        private_key=os.urandom(32),
    )
    launcher = verifier_launcher(plan, image_ref=verifier_image_ref)
    store = VerifierEvidenceStore(
        output_directory / "verifier-evidence.sqlite3",
        verification_key=signer.public_key,
        expected_signing_key_id=signer.signing_key_id,
        expected_identity=launcher.identity,
    )
    supervisor = VerifierSupervisor(launcher, signer, store)
    gates = synthetic_signed_gates(supervisor)
    exact_problem_gate = exact_smoke_problem_signed_valid_gate(
        supervisor, selected_sources[chosen_ids[0]]
    )

    run_id = "g1-validation-pilot-1pct-20260903-v1"
    experiment_id = "goal1-validation-pilot-1pct-v1"
    canonical_sources = {
        BenchmarkProblemIdentity(
            benchmark["name"],
            benchmark["version"],
            benchmark["allowed_split"],
            native_id,
        ).canonical_id: source
        for native_id, source in selected_sources.items()
    }
    subjects: dict[str, VerificationSubject] = {}
    verifications: dict[str, ProductionVerification] = {}

    def subject_builder(
        dispatch: BaselineDispatch,
        candidate: bytes,
        _source: FrozenLeanProblemSource,
    ) -> VerificationSubject:
        subject = subjects.get(dispatch.entry.dispatch_id)
        if subject is None or subject.candidate_source != candidate:
            raise ValueError("pilot verification subject is not bound to model bytes")
        return subject

    port_kwargs = {
        "run_spec_id": plan_sha,
        "execution_authority_sha256": sha256(b"non-credit-pilot-1pct-authority"),
        "protocol_rules_sha256": sha256(b"non-credit-pilot-1pct-rules"),
        "confirmatory_manifest_sha256": plan_sha,
    }
    protocol_port = ProductionVerifierPort(
        supervisor,
        canonical_sources,
        subject_builder=subject_builder,
        **port_kwargs,
    )
    malformed_port = ProductionVerifierPort(
        supervisor,
        canonical_sources,
        **port_kwargs,
    )
    dispatch_authority = DispatchAuthority(
        str(output_directory / "dispatch.sqlite3"),
        run_id,
    )
    manifest = dispatch_authority.current_manifest()
    baselines = load_object(BASELINES_PATH)
    product_contract = load_object(PRODUCT_CONTROLS_PATH)
    admitted_products: dict[str, list[bytes]] = {
        problem_id: [] for problem_id in chosen_ids
    }
    observations: dict[str, ModelContainerObservation] = {}
    classification_kinds: dict[str, str] = {}
    classification_errors: dict[str, str] = {}
    model_errors: dict[str, str] = {}
    summaries: list[dict[str, object]] = []

    for attempt, native_id, arm in schedule:
        source = selected_sources[native_id]
        problem = BenchmarkProblemIdentity(
            benchmark["name"],
            benchmark["version"],
            benchmark["allowed_split"],
            native_id,
        )
        visible_before = tuple(admitted_products[native_id])
        if arm is Arm.ORDINARY:
            prompt = render_baseline_prompt(
                baselines,
                source,
                arm=Arm.ORDINARY.value,
                attempt=attempt,
            )
            response_mode = "baseline"
        else:
            prompt = render_product_prompt(
                product_contract,
                source,
                attempt=attempt,
                admitted_products=visible_before,
            )
            response_mode = "product"
        request = frozen_request(
            source=source,
            problem=problem,
            arm=arm,
            request_utf8=prompt,
            plan_sha256=plan_sha,
            run_id=run_id,
            experiment_id=experiment_id,
            attempt=attempt,
        )

        def model_call(
            dispatch: BaselineDispatch,
            exact_prompt: bytes,
            *,
            _source: FrozenLeanProblemSource = source,
            _arm: Arm = arm,
            _attempt: int = attempt,
            _visible_before: tuple[bytes, ...] = visible_before,
            _response_mode: str = response_mode,
        ) -> ModelAttemptObservation:
            try:
                observed = run_model_container(
                    executor_image,
                    exact_prompt,
                    theorem_name=_source.native_id,
                    response_mode=_response_mode,
                    trusted_source=_source,
                )
            except Exception as exc:
                model_errors[dispatch.entry.dispatch_id] = (
                    f"{type(exc).__name__}: {exc}"
                )
                raise
            observations[dispatch.entry.dispatch_id] = observed
            if observed.completion:
                try:
                    if _arm is Arm.ORDINARY:
                        classified = classify_baseline_response(
                            observed.completion, _source
                        )
                    else:
                        classified = classify_product_response(
                            observed.completion,
                            _source,
                            attempt=_attempt,
                        )
                    classification_kinds[dispatch.entry.dispatch_id] = (
                        classified.kind.value
                    )
                    subjects[dispatch.entry.dispatch_id] = build_verification_subject(
                        _source,
                        classified,
                        admitted_products=(
                            () if _arm is Arm.ORDINARY else _visible_before
                        ),
                    )
                except ValueError as exc:
                    classification_kinds[dispatch.entry.dispatch_id] = "MALFORMED"
                    classification_errors[dispatch.entry.dispatch_id] = str(exc)
            else:
                classification_kinds[dispatch.entry.dispatch_id] = (
                    ConfirmatoryResponseKind.NO_ANSWER.value
                )
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                observed.completion,
                (
                    AttemptStatus.ANSWERED
                    if observed.completion
                    else AttemptStatus.NO_ANSWER
                ),
            )

        def verifier_call(
            dispatch: BaselineDispatch,
            candidate: bytes,
        ):
            port = (
                protocol_port
                if dispatch.entry.dispatch_id in subjects
                else malformed_port
            )
            verification = port.verify(dispatch, candidate)
            verifications[dispatch.entry.dispatch_id] = verification
            return verification.result

        execution = _execute_baseline_attempt(
            expected_arm=arm,
            authority=dispatch_authority,
            manifest=manifest,
            request=request,
            request_utf8=prompt,
            model_call=model_call,
            verifier_call=verifier_call,
        )
        manifest = execution.manifest
        completion = execution.completion
        dispatch_id = completion.dispatch_id
        observation = observations.get(dispatch_id)
        verification = verifications.get(dispatch_id)
        response_kind = classification_kinds.get(
            dispatch_id,
            (
                "MODEL_ERROR"
                if completion.payload.attempt_result.status is AttemptStatus.ERROR
                else ConfirmatoryResponseKind.NO_ANSWER.value
            ),
        )
        product_admitted = False
        final_solved = False
        if verification is not None:
            if response_kind == ConfirmatoryResponseKind.PRODUCT_CANDIDATE.value:
                product_admitted = product_admission_decision(
                    ProductChainArm.VERIFIED_CHAIN,
                    ConfirmatoryResponseKind.PRODUCT_CANDIDATE,
                    verification.result.status,
                    syntax_admissible=(verification.product_parser_admissible is True),
                )
            elif response_kind == ConfirmatoryResponseKind.FINAL_ANSWER.value:
                final_solved = final_solve_decision(
                    ConfirmatoryResponseKind.FINAL_ANSWER,
                    verification.result.status,
                )
        if product_admitted and observation is not None:
            admitted_products[native_id].append(observation.completion)
        result = completion.payload.attempt_result
        response = b"" if observation is None else observation.completion
        raw = b"" if observation is None else observation.raw_completion
        if not result.response_artifact.verifies(response):
            raise RuntimeError("pilot response artifact differs from captured bytes")
        summaries.append(
            {
                "adaptation_rule": (
                    None if observation is None else observation.adaptation_rule
                ),
                "admitted_products_visible_before_attempt": len(visible_before),
                "arm": arm.value,
                "attempt": attempt,
                "attempt_status": result.status.value,
                "candidate_bytes": len(response),
                "candidate_sha256": sha256(response),
                "candidate_utf8": response.decode("utf-8", "replace"),
                "classification_error": classification_errors.get(dispatch_id),
                "completion_status": completion.status.value,
                "dispatch_id": dispatch_id,
                "final_solved": final_solved,
                "model_elapsed_milliseconds": (
                    None if observation is None else observation.elapsed_milliseconds
                ),
                "model_error": result.error,
                "model_error_detail": model_errors.get(dispatch_id),
                "model_image_id": (
                    None if observation is None else observation.image_id
                ),
                "problem_id": native_id,
                "product_admitted": product_admitted,
                "raw_completion_bytes": len(raw),
                "raw_completion_sha256": sha256(raw),
                "raw_completion_utf8": raw.decode("utf-8", "replace"),
                "response_kind": response_kind,
                "verifier_evidence": _signed_evidence(verification),
            }
        )

    answered_without_signed_evidence = [
        row["dispatch_id"]
        for row in summaries
        if row["attempt_status"] == AttemptStatus.ANSWERED.value
        and row["verifier_evidence"] is None
    ]
    integrity = (
        len(summaries) == stage["attempt_count"]
        and not answered_without_signed_evidence
    )
    report: dict[str, object] = {
        "schema": "supernova.goal1.validation-one-percent-report.v1",
        "prospective_runtime": pilot_runtime_provenance(),
        "classification": plan["classification"],
        "scientific_credit": "NONE",
        "countable_attempts": 0,
        "execution_status": "COMPLETE" if integrity else "INCOMPLETE",
        "integrity": {
            "answered_without_signed_evidence": answered_without_signed_evidence,
            "expected_attempts": stage["attempt_count"],
            "observed_attempts": len(summaries),
            "status": "PASS" if integrity else "FAIL",
        },
        "metrics": _one_percent_metrics(summaries, problem_ids=chosen_ids),
        "plan_sha256": plan_sha,
        "selection": {
            "algorithm": plan["selection"]["algorithm"],
            "problem_ids": list(chosen_ids),
            "seed": plan["selection"]["seed"],
        },
        "schedule": [
            {"arm": arm.value, "attempt": attempt, "problem_id": problem_id}
            for attempt, problem_id, arm in schedule
        ],
        "synthetic_signed_gates": gates,
        "exact_smoke_problem_signed_valid_gate": exact_problem_gate,
        "attempts": summaries,
        "next_action": "STOP_AND_ANALYZE_BEFORE_ANY_LARGER_CALIBRATION",
        "verifier_image_ref": launcher.image_ref,
        "verifier_signing_public_key_sha256": sha256(signer.public_key),
        "verifier_signing_public_key_hex": signer.public_key.hex(),
    }
    report_path = output_directory / "one-percent-report.json"
    report_bytes = canonical_bytes(report) + b"\n"
    report_path.write_bytes(report_bytes)
    report["report_sha256"] = sha256(report_bytes)
    return report


def github_failure_annotation(report: dict[str, object]) -> str:
    """Expose only bounded hashes and typed outcomes through check annotations."""

    attempts = report.get("attempts")
    if type(attempts) is not list:
        raise ValueError("smoke report attempts changed")
    compact = []
    for item in attempts:
        if type(item) is not dict:
            raise ValueError("smoke report attempt changed")
        evidence = item.get("verifier_evidence")
        compact.append(
            {
                "adaptation_rule": item.get("adaptation_rule"),
                "arm": item.get("arm"),
                "attempt_status": item.get("attempt_status"),
                "candidate_bytes": item.get("candidate_bytes"),
                "candidate_sha256": item.get("candidate_sha256"),
                "model_elapsed_milliseconds": item.get("model_elapsed_milliseconds"),
                "model_error": item.get("model_error"),
                "raw_completion_bytes": item.get("raw_completion_bytes"),
                "raw_completion_sha256": item.get("raw_completion_sha256"),
                "record_sha256": (
                    evidence.get("record_sha256") if type(evidence) is dict else None
                ),
                "termination_cause": (
                    evidence.get("termination_cause")
                    if type(evidence) is dict
                    else None
                ),
                "verdict": (
                    evidence.get("verdict") if type(evidence) is dict else None
                ),
                "verifier_elapsed_milliseconds": (
                    evidence.get("elapsed_milliseconds")
                    if type(evidence) is dict
                    else None
                ),
            }
        )
    message = json.dumps(
        {
            "attempts": compact,
            "one_percent_admission": report.get("one_percent_admission"),
            "signed_valid_model_responses": report.get("signed_valid_model_responses"),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"::error title=Goal-1 0.1-percent admission failed::{message}"


def github_pilot_notice(report: dict[str, object]) -> str:
    """Expose the bounded one-percent measurements without candidate text."""

    message = json.dumps(
        {
            "countable_attempts": report.get("countable_attempts"),
            "execution_status": report.get("execution_status"),
            "integrity": report.get("integrity"),
            "metrics": report.get("metrics"),
            "next_action": report.get("next_action"),
            "report_sha256": report.get("report_sha256"),
            "selection": report.get("selection"),
            "verifier_image_ref": report.get("verifier_image_ref"),
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"::notice title=Goal-1 non-credit one-percent pilot::{message}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=("one-percent", "smoke"),
        default="smoke",
    )
    parser.add_argument("--validation-file", type=Path, required=True)
    parser.add_argument("--executor-image", required=True)
    parser.add_argument("--verifier-image-ref")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    parser.error(
        "Historical smoke/one-percent launch modes are paused after the pilot audit. "
        "Use integration/goal1_validation_pilot/run_repair.py for the bounded "
        "non-credit repair gates and at most two explicitly requested canary calls."
    )
    common = {
        "validation_file": args.validation_file.resolve(strict=True),
        "executor_image": args.executor_image,
        "output_directory": args.output_directory.resolve(strict=False),
        "verifier_image_ref": args.verifier_image_ref,
    }
    if args.stage == "smoke":
        report = run_smoke(**common)
        print(json.dumps(report, allow_nan=False, indent=2, sort_keys=True))
        if report["one_percent_admission"] == "PASS":
            return 0
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(github_failure_annotation(report))
        return 1
    report = run_one_percent(**common)
    compact = {
        "countable_attempts": report["countable_attempts"],
        "execution_status": report["execution_status"],
        "integrity": report["integrity"],
        "metrics": report["metrics"],
        "next_action": report["next_action"],
        "report_sha256": report["report_sha256"],
        "schema": report["schema"],
        "scientific_credit": report["scientific_credit"],
        "selection": report["selection"],
        "verifier_image_ref": report["verifier_image_ref"],
    }
    print(json.dumps(compact, allow_nan=False, indent=2, sort_keys=True))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(github_pilot_notice(report))
    return 0 if report["execution_status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
