"""Direct durable entry point for Goal-1 confirmatory production.

The activation nonce is consumed only immediately before the first frozen dispatch.
All private keys remain host-only files outside the repository and are never mounted
into either production container.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .activation import DurableActivationAuthority
from .artifacts import ScheduledChatArtifactEnvelope, ScheduledChatArtifactKind
from .confirmatory_io import render_baseline_prompt
from .confirmatory_supervisor import (
    load_private_key_file,
    load_repository_execution_bindings,
    run_supervised_attempt,
)
from .contracts import Arm
from .dispatch import DispatchAuthority
from .evidence_bridge import ExecutionLedgerAuthority
from .execution.baselines import ModelAttemptObservation, execute_ordinary
from .execution.common import AttemptStatus, FrozenProblemRequest
from .problem import BenchmarkProblemIdentity
from .production_verifier import ProductionVerifierPort, load_frozen_lean_sources
from .verifier_evidence import (
    HostVerifierSigner,
    VerifierEvidenceStore,
    VerifierSandboxLauncher,
    VerifierSupervisor,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOAL1_DIRECTORY = REPOSITORY_ROOT / "goal1"
PROTOCOL_PATH = GOAL1_DIRECTORY / "CONFIRMATORY_PROTOCOL.json"
GOAL1_PATH = GOAL1_DIRECTORY / "GOAL1.json"
BASELINES_PATH = GOAL1_DIRECTORY / "CONFIRMATORY_BASELINES.json"
COST_PATH = GOAL1_DIRECTORY / "CONFIRMATORY_COST_POLICY.json"
BENCHMARK_LOCK_PATH = GOAL1_DIRECTORY / "BENCHMARK.lock.json"
VERIFIER_PUBLICATION_PATH = (
    GOAL1_DIRECTORY / "CONFIRMATORY_VERIFIER_PUBLICATION.json"
)
FIRST_ATTEMPT_SCHEMA = "supernova.confirmatory-first-attempt.v1"


@dataclass(frozen=True)
class RunFiles:
    run_directory: Path
    secrets_directory: Path
    benchmark_directory: Path

    def __post_init__(self) -> None:
        for value, field in (
            (self.run_directory, "run_directory"),
            (self.secrets_directory, "secrets_directory"),
            (self.benchmark_directory, "benchmark_directory"),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field} must be an absolute pathlib.Path")

    @property
    def activation_database(self) -> Path:
        return self.run_directory / "activation.sqlite3"

    @property
    def dispatch_database(self) -> Path:
        return self.run_directory / "dispatch.sqlite3"

    @property
    def execution_ledger_database(self) -> Path:
        return self.run_directory / "execution-ledger.sqlite3"

    @property
    def verifier_evidence_database(self) -> Path:
        return self.run_directory / "verifier-evidence.sqlite3"

    @property
    def first_attempt_record(self) -> Path:
        return self.run_directory / "first-attempt.json"

    def secret(self, name: str) -> Path:
        return self.secrets_directory / name


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.name} must be one exact JSON object")
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace durable record: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(_canonical_bytes(dict(value)))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _secret(path: Path, *, minimum: int = 32) -> bytes:
    value = path.read_bytes()
    if len(value) < minimum:
        raise ValueError(f"host secret has fewer than {minimum} bytes: {path.name}")
    return value


def _verifier_launcher() -> VerifierSandboxLauncher:
    value = _json(VERIFIER_PUBLICATION_PATH)
    if (
        value.get("schema") != "supernova.goal1.verifier-publication.v1"
        or value.get("status") != "PUBLISHED_IMMUTABLE"
    ):
        raise ValueError("verifier publication is not immutable production evidence")
    return VerifierSandboxLauncher(
        image_ref=value["image_ref"],
        command=tuple(value["command"]),
        image_environment=tuple(value["image_environment"]),
        container_user=value["container_user"],
        memory_bytes=value["memory_bytes"],
        nano_cpus=value["nano_cpus"],
        pids_limit=value["pids_limit"],
        timeout_seconds=value["timeout_seconds"],
        max_output_bytes=value["max_output_bytes"],
        tmpfs_size_bytes=value["tmpfs_size_bytes"],
        toolchain_lock_sha256=value["toolchain_lock_sha256"],
        project_dependency_lock_sha256=value[
            "project_dependency_lock_sha256"
        ],
        checker_configuration_sha256=value["checker_configuration_sha256"],
        immutable_inputs_sha256=value["immutable_inputs_sha256"],
    )


def _load_report_sources(
    benchmark_directory: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    lock = _json(BENCHMARK_LOCK_PATH)
    benchmark = lock["benchmark"]
    files = {entry["path"]: entry for entry in lock["content"]["files"]}
    report = files["test.jsonl"]
    sources = load_frozen_lean_sources(
        benchmark_directory / "test.jsonl",
        expected_file_sha256=report["sha256"],
        expected_records=244,
        benchmark=benchmark["name"],
        version=benchmark["version"],
        split="test",
    )
    return lock, sources


def _cost_trace_mapping(trace: object) -> dict[str, object]:
    return {
        "arm": trace.arm.value,
        "accounting_complete": trace.accounting_complete,
        "events": [
            {
                "event_id": event.event_id,
                "kind": event.kind.value,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "milliseconds": event.milliseconds,
                "model_usage_basis": (
                    None
                    if event.model_usage_basis is None
                    else event.model_usage_basis.value
                ),
            }
            for event in trace.events
        ],
        "expected_events": [
            {
                "event_id": event.event_id,
                "kind": event.kind.value,
                "model_usage_basis": (
                    None
                    if event.model_usage_basis is None
                    else event.model_usage_basis.value
                ),
            }
            for event in trace.expected_events
        ],
    }


def start_first_attempt(files: RunFiles) -> dict[str, object]:
    """Consume activation and execute the first exact production dispatch."""

    files.run_directory.mkdir(parents=True, exist_ok=True)
    if files.first_attempt_record.exists():
        return _json(files.first_attempt_record)

    protocol = _json(PROTOCOL_PATH)
    goal1 = _json(GOAL1_PATH)
    activation_authority = DurableActivationAuthority(files.activation_database)
    try:
        durable = activation_authority.read_active()
    except LookupError:
        durable = activation_authority.activate_once(
            protocol,
            goal1,
            operator_seed=_secret(files.secret("operator-seed.raw"))[:32],
            activation_nonce=_secret(files.secret("activation-nonce.raw"))[:32],
        )

    activation = durable.activation
    entries = activation.manifest.operator_plan["entries"]
    if type(entries) is not list or len(entries) != 19520:
        raise ValueError("production operator plan does not contain 19,520 slots")
    slot = entries[0]
    if (
        slot.get("dispatch_index") != 0
        or slot.get("arm") != Arm.ORDINARY.value
        or slot.get("budget_attempt_index") != 0
    ):
        raise ValueError("the first frozen dispatch is not ordinary attempt zero")

    benchmark_lock, sources = _load_report_sources(files.benchmark_directory)
    source = next(
        (
            value
            for value in sources.values()
            if value.native_id == slot["problem_id"]
        ),
        None,
    )
    if source is None:
        raise KeyError("first frozen problem is absent from the locked report split")
    benchmark = benchmark_lock["benchmark"]
    problem = BenchmarkProblemIdentity(
        benchmark["name"], benchmark["version"], "test", source.native_id
    )
    baselines = _json(BASELINES_PATH)
    request_utf8 = render_baseline_prompt(
        baselines,
        source,
        arm=Arm.ORDINARY.value,
        attempt=0,
    )
    run_id = "goal1-" + durable.activation_id.removeprefix("activation-")[:32]
    request_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        request_utf8,
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id=run_id,
        problem_id=problem.canonical_id,
        arm=Arm.ORDINARY,
        attempt=0,
    )
    cost_policy = _json(COST_PATH)
    bindings = activation.manifest.public_manifest["bindings"]
    request = FrozenProblemRequest(
        run_id=run_id,
        experiment_id=activation.protocol["protocol_id"],
        problem=problem,
        benchmark_root_sha256=benchmark_lock["content"]["root_sha256"],
        problem_sha256=source.source_sha256,
        arm=Arm.ORDINARY,
        attempt=0,
        budget_id=cost_policy["policy_id"],
        budget_sha256=bindings["cost_policy_sha256"],
        model_usage_basis="visible_utf8_bytes",
        runtime_sha256=bindings["runtime_sha256"],
        request_artifact=request_artifact,
        protocol_dispatch_id=slot["dispatch_id"],
        confirmatory_manifest_sha256=activation.manifest.public_manifest[
            "manifest_sha256"
        ],
    )

    dispatch_authority = DispatchAuthority(str(files.dispatch_database), run_id)
    ledger = ExecutionLedgerAuthority(
        files.execution_ledger_database,
        run_id=run_id,
        issuer_id=activation.authority.issuer_id,
        execution_authority_sha256=activation.authority.authority_sha256,
        secret=_secret(files.secret("execution-ledger-secret.raw")),
        protocol=activation.protocol,
        public_manifest=activation.manifest.public_manifest,
        operator_plan=activation.manifest.operator_plan,
        execution_authority=activation.authority,
    )
    model_launcher, _capacity = load_repository_execution_bindings(REPOSITORY_ROOT)
    receipt_private_key = load_private_key_file(
        files.secret("hermetic-receipt-v2.raw")
    )

    verifier_launcher = _verifier_launcher()
    verifier_signer = HostVerifierSigner(
        issuer_id="goal1-host-verifier-v1",
        signing_key_id="goal1-host-verifier-key-v1",
        private_key=_secret(files.secret("verifier-evidence-key.raw"))[:32],
    )
    verifier_store = VerifierEvidenceStore(
        files.verifier_evidence_database,
        verification_key=verifier_signer.public_key,
        expected_signing_key_id=verifier_signer.signing_key_id,
        expected_identity=verifier_launcher.identity,
    )
    verifier_supervisor = VerifierSupervisor(
        verifier_launcher, verifier_signer, verifier_store
    )
    verifier_port = ProductionVerifierPort(
        verifier_supervisor,
        sources,
        run_spec_id=activation.manifest.public_manifest["manifest_sha256"],
        execution_authority_sha256=activation.authority.authority_sha256,
        protocol_rules_sha256=activation.protocol["sealed_rules_sha256"],
        confirmatory_manifest_sha256=activation.manifest.public_manifest[
            "manifest_sha256"
        ],
    )

    observed_context: dict[str, object] = {}

    def model_call(dispatch: object, prompt: bytes) -> ModelAttemptObservation:
        ledger._register_dispatch(dispatch.entry, dispatch.request)
        attempt = run_supervised_attempt(
            model_launcher,
            activation.authority,
            prompt,
            receipt_private_key=receipt_private_key,
            confirmatory_manifest_sha256=activation.manifest.public_manifest[
                "manifest_sha256"
            ],
            run_id=run_id,
            protocol_dispatch_id=slot["dispatch_id"],
            dispatch_id=dispatch.entry.dispatch_id,
            problem_id=source.native_id,
            arm=Arm.ORDINARY.value,
            attempt_index=0,
            sequence=0,
        )
        observed_context[dispatch.entry.dispatch_id] = attempt.context_receipt
        return ModelAttemptObservation(
            dispatch_id=dispatch.entry.dispatch_id,
            response_utf8=attempt.response,
            status=(
                AttemptStatus.ANSWERED
                if attempt.response
                else AttemptStatus.NO_ANSWER
            ),
        )

    execution = execute_ordinary(
        authority=dispatch_authority,
        manifest=dispatch_authority.current_manifest(),
        request=request,
        request_utf8=request_utf8,
        model_call=model_call,
        verifier_call=verifier_port,
    )
    completion = execution.completion
    context = observed_context.get(completion.dispatch_id)
    if context is None:
        raise RuntimeError("model supervisor did not return a signed context receipt")
    ledger_receipt = ledger._record_completion(
        completion,
        context_isolation_receipt=context,
        predecessor_reconciliation_receipt=(
            ledger._issue_predecessor_reconciliation_receipt(completion)
        ),
        orchestration_milliseconds=(
            execution.cost_trace.total.orchestration_milliseconds
        ),
    )
    verifier_binding = verifier_port.bindings_by_dispatch.get(
        completion.dispatch_id
    )
    record = {
        "schema": FIRST_ATTEMPT_SCHEMA,
        "scientific_credit": "COUNTABLE_CONFIRMATORY_PRODUCTION",
        "activation_id": durable.activation_id,
        "activation_record_sha256": durable.activation_record_sha256,
        "run_id": run_id,
        "dispatch_index": 0,
        "protocol_dispatch_id": slot["dispatch_id"],
        "actual_dispatch_id": completion.dispatch_id,
        "problem_id": source.native_id,
        "arm": Arm.ORDINARY.value,
        "attempt": 0,
        "completion": completion.to_mapping(),
        "completion_status": completion.status.value,
        "cost_trace": _cost_trace_mapping(execution.cost_trace),
        "execution_ledger_receipt_sha256": ledger_receipt.receipt_sha256,
        "context_receipt_sha256": context.receipt_sha256,
        "verifier_evidence_record_sha256": (
            None
            if verifier_binding is None
            else verifier_store.read(verifier_binding).record_sha256
        ),
        "confirmatory_manifest_sha256": activation.manifest.public_manifest[
            "manifest_sha256"
        ],
        "execution_authority_sha256": activation.authority.authority_sha256,
    }
    _write_once(files.first_attempt_record, record)
    return record


def status(files: RunFiles) -> dict[str, object]:
    authority = DurableActivationAuthority(files.activation_database)
    try:
        active = authority.read_active()
    except LookupError:
        return {"status": "NOT_ACTIVATED", "countable_attempts": 0}
    if not files.first_attempt_record.exists():
        return {
            "status": "ACTIVATED_NO_COMPLETED_ATTEMPT",
            "activation_id": active.activation_id,
            "countable_attempts": 0,
        }
    first = _json(files.first_attempt_record)
    return {
        "status": "RUNNING",
        "activation_id": active.activation_id,
        "run_id": first["run_id"],
        "countable_attempts": 1,
        "first_completion_status": first["completion_status"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start Goal-1 confirmatory production"
    )
    parser.add_argument("command", choices=("start-first", "status"))
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--secrets-directory", type=Path, required=True)
    parser.add_argument("--benchmark-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    files = RunFiles(
        args.run_directory.resolve(strict=False),
        args.secrets_directory.resolve(strict=True),
        args.benchmark_directory.resolve(strict=True),
    )
    result = (
        start_first_attempt(files)
        if args.command == "start-first"
        else status(files)
    )
    print(
        json.dumps(
            result,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RunFiles", "start_first_attempt", "status"]
