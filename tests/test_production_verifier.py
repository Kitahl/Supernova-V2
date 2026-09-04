from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1 import verifier_evidence as verifier_evidence_module
from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import DispatchEntry
from supernova_goal1.execution.baselines import BaselineDispatch
from supernova_goal1.execution.common import FrozenProblemRequest
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.production_verifier import (
    FrozenLeanProblemSource,
    build_verifier_binding,
    canonical_sha256,
    load_frozen_lean_sources,
    verifier_result_from_evidence,
)
from supernova_goal1.verifier import VerifierStatus
from supernova_goal1.verifier_evidence import (
    HostVerifierSigner,
    TerminationCause,
    VerifierEvidenceStore,
    VerifierSandboxLauncher,
    VerifierSupervisor,
    canonical_bytes,
)


def sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


class ProductionVerifierTests(unittest.TestCase):
    source_text = "import Mathlib\n\ntheorem demo : True := by\n"

    def source(self, **changes: object) -> FrozenLeanProblemSource:
        raw: dict[str, object] = {
            "schema_version": 1,
            "problem_id": "demo",
            "split": "validation",
            "source_id": "fixture",
            "source_record_sha256": sha("source-record"),
            "lean_code_sha256": sha(self.source_text),
            "lean_code": self.source_text,
            "informal_prefix": "",
        }
        raw.update(changes)
        return FrozenLeanProblemSource.from_record(raw, expected_split="validation")

    def dispatch(self, source: FrozenLeanProblemSource) -> BaselineDispatch:
        problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            source.native_id,
        )
        request_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            b"prove the theorem",
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id="run-1",
            problem_id=problem.canonical_id,
            arm=Arm.ORDINARY,
            attempt=0,
        )
        request = FrozenProblemRequest(
            run_id="run-1",
            experiment_id="goal1-confirmatory-v1",
            problem=problem,
            benchmark_root_sha256=sha("benchmark-root"),
            problem_sha256=source.source_sha256,
            arm=Arm.ORDINARY,
            attempt=0,
            budget_id="budget-1",
            budget_sha256=sha("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=sha("requested-runtime"),
            request_artifact=request_artifact,
            protocol_dispatch_id="dispatch-" + sha("protocol-dispatch"),
            confirmatory_manifest_sha256=sha("manifest"),
        )
        entry = DispatchEntry.create(
            run_id=request.run_id,
            sequence=0,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt_index=request.attempt,
            request_sha256=request.frozen_request_sha256,
            completion_verifier_sha256=sha("completion-verifier"),
            predecessor_sha256="0" * 64,
        )
        return BaselineDispatch(request=request, entry=entry, expected_events=())

    def binding(self, source: FrozenLeanProblemSource, runtime: str):
        return build_verifier_binding(
            self.dispatch(source),
            b"  trivial\n",
            source,
            run_spec_id=sha("run-spec"),
            execution_authority_sha256=sha("authority"),
            protocol_rules_sha256=sha("protocol"),
            confirmatory_manifest_sha256=sha("manifest"),
            actual_runtime_sha256=runtime,
        )

    def test_binding_uses_exact_source_statement_target_and_observed_runtime(
        self,
    ) -> None:
        source = self.source()
        actual_runtime = sha("actual-runtime")
        binding = self.binding(source, actual_runtime)

        self.assertEqual(source.source_sha256, binding.source_construction_sha256)
        self.assertEqual(source.source_sha256, binding.source_template_sha256)
        self.assertEqual(source.source_sha256, binding.rendered_source_sha256)
        self.assertEqual(sha(b"theorem demo : True "), binding.theorem_statement_sha256)
        self.assertEqual(canonical_sha256(["demo"]), binding.theorem_target_set_sha256)
        self.assertEqual(actual_runtime, binding.actual_runtime_sha256)
        self.assertNotEqual(
            binding.requested_runtime_sha256, binding.actual_runtime_sha256
        )
        with self.assertRaisesRegex(ValueError, "rendered source digest"):
            replace(binding, rendered_source_sha256=sha("substituted-rendered-source"))

    def test_binding_rejects_source_identity_or_content_substitution(self) -> None:
        source = self.source()
        dispatch = self.dispatch(source)
        other = self.source(
            problem_id="other",
            lean_code=self.source_text.replace("demo", "other"),
            lean_code_sha256=sha(self.source_text.replace("demo", "other")),
        )
        with self.assertRaisesRegex(ValueError, "identity differs"):
            build_verifier_binding(
                dispatch,
                b"  trivial\n",
                other,
                run_spec_id=sha("run-spec"),
                execution_authority_sha256=sha("authority"),
                protocol_rules_sha256=sha("protocol"),
                confirmatory_manifest_sha256=sha("manifest"),
                actual_runtime_sha256=sha("actual-runtime"),
            )

    def test_loader_verifies_complete_file_framing_order_and_identity(self) -> None:
        records = []
        for native_id in ("alpha", "beta"):
            lean_code = f"import Mathlib\n\ntheorem {native_id} : True := by\n"
            records.append(
                {
                    "schema_version": 1,
                    "problem_id": native_id,
                    "split": "validation",
                    "source_id": "fixture",
                    "source_record_sha256": sha("record:" + native_id),
                    "lean_code_sha256": sha(lean_code),
                    "lean_code": lean_code,
                    "informal_prefix": "",
                }
            )
        frozen_bytes = b"".join(canonical_bytes(record) + b"\n" for record in records)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "validation.jsonl"
            path.write_bytes(frozen_bytes)
            sources = load_frozen_lean_sources(
                path,
                expected_file_sha256=sha(frozen_bytes),
                expected_records=2,
                benchmark="miniF2F-Lean4-Kimina-composite",
                version="deepseek-v1.5-2c4ba911+kimina-5def318",
                split="validation",
            )
            v2_records = [dict(record, schema_version=2) for record in records]
            v2_bytes = b"".join(
                canonical_bytes(record) + b"\n" for record in v2_records
            )
            v2_path = Path(temporary) / "validation-v2.jsonl"
            v2_path.write_bytes(v2_bytes)
            v2_sources = load_frozen_lean_sources(
                v2_path,
                expected_file_sha256=sha(v2_bytes),
                expected_records=2,
                benchmark="miniF2F-Lean4-Kimina-composite-goal1-v2-candidate",
                version="goal1-v2-candidate-unsealed",
                split="validation",
                expected_record_schema_version=2,
            )
            with self.assertRaisesRegex(ValueError, "file digest mismatch"):
                load_frozen_lean_sources(
                    path,
                    expected_file_sha256="0" * 64,
                    expected_records=2,
                    benchmark="miniF2F-Lean4-Kimina-composite",
                    version="deepseek-v1.5-2c4ba911+kimina-5def318",
                    split="validation",
                )
            with self.assertRaisesRegex(ValueError, "record schema changed"):
                load_frozen_lean_sources(
                    v2_path,
                    expected_file_sha256=sha(v2_bytes),
                    expected_records=2,
                    benchmark="miniF2F-Lean4-Kimina-composite",
                    version="deepseek-v1.5-2c4ba911+kimina-5def318",
                    split="validation",
                )

        self.assertEqual(2, len(sources))
        self.assertEqual(2, len(v2_sources))
        self.assertEqual(
            {"alpha", "beta"}, {value.native_id for value in sources.values()}
        )
        with self.assertRaisesRegex(ValueError, "end exactly"):
            self.source(
                lean_code=self.source_text + "-- changed\n",
                lean_code_sha256=sha(self.source_text + "-- changed\n"),
            )

    def test_unknown_evidence_round_trips_exact_blobs_into_compatibility_result(
        self,
    ) -> None:
        source = self.source()
        runtime = sha("actual-runtime")
        binding = self.binding(source, runtime)
        launcher = VerifierSandboxLauncher(
            image_ref="ghcr.io/kitahl/verifier@sha256:" + "a" * 64,
            command=("/opt/supernova/entrypoint",),
            image_environment=(),
            container_user="10001:10001",
            memory_bytes=1024,
            nano_cpus=1,
            pids_limit=1,
            timeout_seconds=1,
            max_output_bytes=1024,
            tmpfs_size_bytes=1024,
            toolchain_lock_sha256=runtime,
            project_dependency_lock_sha256=sha("dependencies"),
            checker_configuration_sha256=sha("checker"),
            immutable_inputs_sha256=sha("inputs"),
        )
        signer = HostVerifierSigner(
            issuer_id="host-1",
            signing_key_id="key-1",
            private_key=b"\x01" * 32,
        )
        with TemporaryDirectory() as temporary:
            store = VerifierEvidenceStore(
                Path(temporary) / "evidence.sqlite3",
                verification_key=signer.public_key,
                expected_signing_key_id="key-1",
                expected_identity=launcher.identity,
            )
            supervisor = VerifierSupervisor(launcher, signer, store)
            record = supervisor.record_unknown_without_execution(
                binding,
                source=source.source,
                candidate=b"  trivial\n",
                cause=TerminationCause.HOST_INFRASTRUCTURE_ERROR,
                detail="docker unavailable",
            )
            blobs = store.read_blobs(binding)
            result = verifier_result_from_evidence(
                record, blobs, command=launcher.command
            )

        self.assertEqual(VerifierStatus.ERROR, result.status)
        self.assertIsNone(result.returncode)
        self.assertIn("HOST_INFRASTRUCTURE_ERROR", result.error or "")
        self.assertEqual("docker unavailable", result.stderr)
        self.assertEqual(sha(blobs.stderr), record.body["artifacts"]["stderr_sha256"])

    def test_structured_elaborator_rejection_is_invalid_but_timeout_is_not(self) -> None:
        verifier_evidence_module._validate_result_algebra(
            verdict=verifier_evidence_module.VerifierVerdict.INVALID,
            cause=TerminationCause.REJECTED,
            elaborator_exit_status=10,
            checker_exit_status=None,
            timed_out=False,
            oom_killed=False,
            resource_limited=False,
            sandbox_policy_violated=False,
        )
        with self.assertRaisesRegex(ValueError, "normal deterministic"):
            verifier_evidence_module._validate_result_algebra(
                verdict=verifier_evidence_module.VerifierVerdict.INVALID,
                cause=TerminationCause.REJECTED,
                elaborator_exit_status=10,
                checker_exit_status=None,
                timed_out=True,
                oom_killed=False,
                resource_limited=False,
                sandbox_policy_violated=False,
            )


if __name__ == "__main__":
    unittest.main()
