"""One synthetic, NON-CREDIT signed Mathlib control; no model/pilot dependency."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.verifier_evidence import (
    HostVerifierSigner,
    VerifierBinding,
    VerifierEvidenceStore,
    VerifierSandboxLauncher,
    VerifierSupervisor,
    canonical_bytes,
)

IMAGE = "ghcr.io/kitahl/supernova-goal1-verifier@sha256:6e4008a00beebf5795e3afdc1affcfd549310d357a6662084450a534309b10ba"
THEOREM = "supernova_repair_nat_refl"
SOURCE = (
    b"import Mathlib\n\ntheorem supernova_repair_nat_refl (n : Nat) : n = n := by\n"
)
CANDIDATE = b"  rfl\n"
WATCHDOG_SECONDS = 60
PUBLICATION = ROOT / "goal1/CONFIRMATORY_VERIFIER_PUBLICATION.json"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def launcher() -> VerifierSandboxLauncher:
    # Match the previous local replay exactly. Do not mutate the publication
    # authority or pretend this diagnostic override is a scientific re-seal.
    config = json.loads(PUBLICATION.read_bytes())
    if config["status"] != "PUBLISHED_IMMUTABLE":
        raise ValueError("publication configuration is not immutable")
    fields = (
        "container_user",
        "memory_bytes",
        "nano_cpus",
        "pids_limit",
        "max_output_bytes",
        "tmpfs_size_bytes",
        "toolchain_lock_sha256",
        "project_dependency_lock_sha256",
        "checker_configuration_sha256",
        "immutable_inputs_sha256",
    )
    return VerifierSandboxLauncher(
        image_ref=IMAGE,
        command=tuple(config["command"]),
        image_environment=tuple(config["image_environment"]),
        timeout_seconds=WATCHDOG_SECONDS,
        **{key: config[key] for key in fields},
    )


def control_manifest(sandbox: VerifierSandboxLauncher) -> dict:
    return {
        "schema": "supernova.verifier-control.v1",
        "classification": "SYNTHETIC_NON_CREDIT_ENGINEERING_CONTROL",
        "source_utf8": SOURCE.decode(),
        "candidate_utf8": CANDIDATE.decode(),
        "image_ref": IMAGE,
        "watchdog_seconds_per_phase": WATCHDOG_SECONDS,
        "sandbox_policy": sandbox.sandbox_policy,
        "publication_config_sha256": sha(PUBLICATION.read_bytes()),
        "publication_image_override": True,
        "supervisor_sha256": sha(
            (ROOT / "src/supernova_goal1/verifier_evidence.py").read_bytes()
        ),
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "model_calls": 0,
        "countable_attempts": 0,
    }


def binding_for(
    run_id: str, manifest: dict, sandbox: VerifierSandboxLauncher
) -> VerifierBinding:
    plan = sha(canonical_bytes(manifest))
    request = sha(canonical_bytes({"run_id": run_id, "manifest_sha256": plan}))
    problem = "sha256:" + sha(SOURCE)
    # These are explicit diagnostic identities, not fabricated scientific
    # dispatch/manifest receipts. The experiment and run namespaces say so.
    return VerifierBinding(
        run_spec_id=plan,
        run_id=run_id,
        experiment_id="non-credit-verifier-control",
        execution_authority_sha256=plan,
        confirmatory_manifest_sha256=plan,
        protocol_rules_sha256=plan,
        protocol_dispatch_id="dispatch-" + request,
        actual_dispatch_id=request,
        dispatch_entry_sha256=request,
        frozen_request_sha256=request,
        normalized_request_sha256=request,
        attempt_result_sha256=sha(CANDIDATE),
        problem_id=problem,
        problem_identity=problem,
        arm_id="synthetic-control",
        attempt_id=0,
        candidate_id="sha256:" + sha(CANDIDATE),
        candidate_source_sha256=sha(CANDIDATE),
        theorem_statement_sha256=sha(SOURCE),
        source_template_sha256=sha(SOURCE),
        rendered_source_sha256=sha(SOURCE),
        source_construction_sha256=sha(SOURCE),
        theorem_target_set_sha256=sha(canonical_bytes([THEOREM])),
        requested_runtime_sha256=sandbox.toolchain_lock_sha256,
        actual_runtime_sha256=sandbox.toolchain_lock_sha256,
        immutable_configuration_sha256=plan,
    )


def run(output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "supernova.verifier-control-report.v1",
        "status": "ERROR",
        "countable_attempts": 0,
        "model_calls": 0,
        "host_platform": platform.platform(),
        "host_python": platform.python_version(),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "internal_stage_timings": "NOT_EXPOSED_BY_UNMODIFIED_IMAGE",
        "next_action": "STOP_AND_REVIEW_CONTROL_EVIDENCE",
    }
    started = time.monotonic_ns()
    try:
        sandbox = launcher()
        manifest = control_manifest(sandbox)
        report["manifest"] = manifest
        run_id = "non-credit-control-" + uuid.uuid4().hex
        binding = binding_for(run_id, manifest, sandbox)
        signer = HostVerifierSigner(
            issuer_id="non-credit-control-host",
            signing_key_id=run_id + "-key",
            private_key=os.urandom(32),
        )
        report["run_id"] = run_id
        report["public_key_b64"] = base64.b64encode(signer.public_key).decode("ascii")
        report["signing_key_id"] = signer.signing_key_id
        store = VerifierEvidenceStore(
            output / "verifier-evidence.sqlite3",
            verification_key=signer.public_key,
            expected_signing_key_id=signer.signing_key_id,
            expected_identity=sandbox.identity,
        )
        record = VerifierSupervisor(sandbox, signer, store).run_and_record(
            binding,
            source=SOURCE,
            candidate=CANDIDATE,
            theorem_names=(THEOREM,),
        )
        record.verify(
            signer.public_key,
            expected_signing_key_id=signer.signing_key_id,
            expected_binding=binding,
            expected_identity=sandbox.identity,
        )
        persisted = store.read_complete((binding,))
        blobs = store.read_blobs(binding)
        if len(persisted) != 1 or persisted[0] != record:
            raise ValueError("signed record readback differs")
        report["record_sha256"] = record.record_sha256
        report["signed_record"] = {
            "body": record.body,
            "signature_b64": record.signature_b64,
        }
        report["stdout_utf8"] = blobs.stdout.decode("utf-8", "replace")
        report["stderr_utf8"] = blobs.stderr.decode("utf-8", "replace")
        report["signature_and_store_readback"] = "PASS"
        report["status"] = (
            "PASS" if record.body["observations"]["verdict"] == "VALID" else "NOT_READY"
        )
    except Exception as exc:  # noqa: BLE001 - persist a typed ERROR; never convert failure to readiness
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:4000]}
    finally:
        report["host_elapsed_milliseconds"] = (
            time.monotonic_ns() - started
        ) // 1_000_000
        report["report_sha256"] = sha(canonical_bytes(report))
        (output / "control-report.json").write_bytes(canonical_bytes(report) + b"\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.output)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "model_calls",
                    "countable_attempts",
                    "report_sha256",
                )
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
