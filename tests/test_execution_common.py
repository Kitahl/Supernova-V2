from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import unittest
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus


LOCK_ROOT = "914c05427e1e7e0979f4ca058f90fb3138ee0d3319233b415194c10e67d3683b"


class CommonExecutionContractTests(unittest.TestCase):
    def problem(self, native_id: str = "problem-001") -> BenchmarkProblemIdentity:
        return BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            native_id,
        )

    def artifact(
        self,
        payload: str,
        *,
        kind: ScheduledChatArtifactKind,
        problem: BenchmarkProblemIdentity | None = None,
        run_id: str = "run-001",
        arm: Arm = Arm.ORDINARY,
        attempt: int = 0,
    ) -> ScheduledChatArtifactEnvelope:
        identity = problem or self.problem()
        return ScheduledChatArtifactEnvelope.from_visible_utf8(
            payload,
            kind=kind,
            run_id=run_id,
            problem_id=identity.canonical_id,
            arm=arm,
            attempt=attempt,
        )

    def request(
        self,
        *,
        problem: BenchmarkProblemIdentity | None = None,
        run_id: str = "run-001",
        arm: Arm = Arm.ORDINARY,
        attempt: int = 0,
        request_payload: str = "Prove the exact Lean theorem.",
        benchmark_root_sha256: str = LOCK_ROOT,
        problem_sha256: str = "1" * 64,
        budget_id: str = "budget-v1",
        budget_sha256: str = "2" * 64,
        model_usage_basis: str = "visible_utf8_bytes",
        runtime_sha256: str = "3" * 64,
    ) -> FrozenProblemRequest:
        identity = problem or self.problem()
        return FrozenProblemRequest(
            run_id=run_id,
            experiment_id="goal1-pilot-v1",
            problem=identity,
            benchmark_root_sha256=benchmark_root_sha256,
            problem_sha256=problem_sha256,
            arm=arm,
            attempt=attempt,
            budget_id=budget_id,
            budget_sha256=budget_sha256,
            model_usage_basis=model_usage_basis,
            runtime_sha256=runtime_sha256,
            request_artifact=self.artifact(
                request_payload,
                kind=ScheduledChatArtifactKind.REQUEST,
                problem=identity,
                run_id=run_id,
                arm=arm,
                attempt=attempt,
            ),
        )

    def result(
        self,
        request: FrozenProblemRequest,
        *,
        payload: str = "by\n  norm_num",
        status: AttemptStatus = AttemptStatus.ANSWERED,
        error: str | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            frozen_request_sha256=request.frozen_request_sha256,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
            request_artifact_id=request.request_artifact.artifact_id,
            response_artifact=self.artifact(
                payload,
                kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
                problem=request.problem,
                run_id=request.run_id,
                arm=request.arm,
                attempt=request.attempt,
            ),
            status=status,
            error=error,
        )

    def verifier_result(
        self,
        status: VerifierStatus = VerifierStatus.PASS,
    ) -> VerifierResult:
        if status is VerifierStatus.PASS:
            return VerifierResult(
                status=status,
                command=("lake", "env", "lean", "proof.lean"),
                returncode=0,
                stdout="verified π\n",
                stderr="",
                elapsed_milliseconds=12,
            )
        if status is VerifierStatus.FAIL:
            return VerifierResult(
                status=status,
                command=("lake", "env", "lean", "proof.lean"),
                returncode=1,
                stdout="",
                stderr="type mismatch\n",
                elapsed_milliseconds=14,
            )
        return VerifierResult(
            status=status,
            command=("lake", "env", "lean", "proof.lean"),
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=20,
            error="verifier timeout" if status is VerifierStatus.TIMEOUT else "spawn failed",
        )

    def receipt(
        self,
        request: FrozenProblemRequest,
        result: AttemptResult,
        status: VerifierStatus = VerifierStatus.PASS,
    ) -> LeanVerifierReceipt:
        return LeanVerifierReceipt.from_verifier_result(
            request=request,
            attempt_result=result,
            verifier_result=self.verifier_result(status),
        )

    def test_frozen_request_round_trip_and_identity_are_stable(self) -> None:
        request = self.request()
        raw = request.to_mapping()
        self.assertEqual(FrozenProblemRequest.from_mapping(raw), request)
        self.assertEqual(raw["frozen_request_sha256"], request.frozen_request_sha256)
        self.assertEqual(raw["benchmark_root_sha256"], LOCK_ROOT)
        self.assertEqual(raw["problem"]["native_id"], "problem-001")
        self.assertEqual(raw["request_artifact"]["artifact_id"], request.request_artifact.artifact_id)

    def test_request_identity_binds_every_load_bearing_input(self) -> None:
        base = self.request()
        variants = [
            self.request(run_id="run-002"),
            self.request(problem=self.problem("problem-002")),
            self.request(arm=Arm.PORTFOLIO),
            self.request(attempt=1),
            self.request(request_payload="Prove a different theorem."),
            self.request(benchmark_root_sha256="4" * 64),
            self.request(problem_sha256="5" * 64),
            self.request(budget_id="budget-v2"),
            self.request(budget_sha256="6" * 64),
            self.request(model_usage_basis="provider_tokens"),
            self.request(runtime_sha256="7" * 64),
        ]
        for variant in variants:
            with self.subTest(variant=variant.to_mapping()):
                self.assertNotEqual(variant.frozen_request_sha256, base.frozen_request_sha256)

    def test_request_binds_exact_unicode_bytes_without_normalization(self) -> None:
        composed = "café"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed.encode("utf-8"), decomposed.encode("utf-8"))
        left = self.request(request_payload=composed)
        right = self.request(request_payload=decomposed)
        self.assertNotEqual(left.request_artifact.artifact_id, right.request_artifact.artifact_id)
        self.assertNotEqual(left.frozen_request_sha256, right.frozen_request_sha256)

    def test_request_rejects_wrong_artifact_identity_kind_and_forged_digest(self) -> None:
        identity = self.problem()
        response = self.artifact(
            "x",
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            problem=identity,
        )
        kwargs = dict(
            run_id="run-001",
            experiment_id="goal1-pilot-v1",
            problem=identity,
            benchmark_root_sha256=LOCK_ROOT,
            problem_sha256="1" * 64,
            arm=Arm.ORDINARY,
            attempt=0,
            budget_id="budget-v1",
            budget_sha256="2" * 64,
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256="3" * 64,
        )
        with self.assertRaisesRegex(ValueError, "request"):
            FrozenProblemRequest(request_artifact=response, **kwargs)
        mismatched = self.artifact(
            "x",
            kind=ScheduledChatArtifactKind.REQUEST,
            problem=identity,
            run_id="run-other",
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            FrozenProblemRequest(request_artifact=mismatched, **kwargs)
        raw = self.request().to_mapping()
        raw["frozen_request_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            FrozenProblemRequest.from_mapping(raw)

    def test_request_deserialization_is_closed_world_and_type_strict(self) -> None:
        raw = self.request().to_mapping()
        raw["dispatch_id"] = "unauthorized"
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            FrozenProblemRequest.from_mapping(raw)
        with self.assertRaises(ValueError):
            self.request(attempt=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.request(runtime_sha256="A" * 64)
        with self.assertRaises(ValueError):
            self.request(model_usage_basis="estimated_tokens")
        with self.assertRaisesRegex(ValueError, "object"):
            FrozenProblemRequest.from_mapping([])  # type: ignore[arg-type]

    def test_attempt_result_round_trip_and_request_binding(self) -> None:
        request = self.request()
        result = self.result(request)
        result.validate_for(request)
        self.assertEqual(AttemptResult.from_mapping(result.to_mapping()), result)
        self.assertEqual(result.response_artifact.kind, ScheduledChatArtifactKind.TERMINAL_RESPONSE)
        self.assertEqual(result.request_artifact_id, request.request_artifact.artifact_id)

        different = self.request(attempt=1)
        with self.assertRaisesRegex(ValueError, "does not match"):
            result.validate_for(different)

    def test_attempt_result_rejects_wrong_kind_identity_and_status_error_pairs(self) -> None:
        request = self.request()
        base = dict(
            frozen_request_sha256=request.frozen_request_sha256,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
            request_artifact_id=request.request_artifact.artifact_id,
        )
        with self.assertRaisesRegex(ValueError, "terminal response"):
            AttemptResult(
                response_artifact=request.request_artifact,
                status=AttemptStatus.ANSWERED,
                error=None,
                **base,
            )
        wrong = self.artifact(
            "answer",
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            problem=request.problem,
            run_id="run-other",
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            AttemptResult(
                response_artifact=wrong,
                status=AttemptStatus.ANSWERED,
                error=None,
                **base,
            )
        with self.assertRaisesRegex(ValueError, "cannot carry error"):
            self.result(request, status=AttemptStatus.NO_ANSWER, error="bad")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.result(request, status=AttemptStatus.ERROR, error=None)
        with self.assertRaisesRegex(ValueError, "object"):
            AttemptResult.from_mapping([])  # type: ignore[arg-type]

    def test_verifier_receipt_round_trip_hashes_exact_outputs_and_binds_inputs(self) -> None:
        request = self.request()
        result = self.result(request)
        receipt = self.receipt(request, result)
        receipt.validate_for(request, result)
        self.assertEqual(LeanVerifierReceipt.from_mapping(receipt.to_mapping()), receipt)
        self.assertEqual(receipt.stdout_sha256, sha256("verified π\n".encode("utf-8")).hexdigest())
        self.assertEqual(receipt.stderr_sha256, sha256(b"").hexdigest())
        self.assertEqual(receipt.candidate_artifact_id, result.response_artifact.artifact_id)
        self.assertEqual(receipt.runtime_sha256, request.runtime_sha256)

        other_request = self.request(runtime_sha256="8" * 64)
        other_result = self.result(other_request)
        with self.assertRaisesRegex(ValueError, "does not match"):
            receipt.validate_for(other_request, other_result)

    def test_verifier_receipt_status_coherence(self) -> None:
        request = self.request()
        result = self.result(request)
        for status in VerifierStatus:
            with self.subTest(status=status):
                receipt = self.receipt(request, result, status)
                self.assertEqual(receipt.status, status)
                receipt.validate_for(request, result)

        raw = self.receipt(request, result).to_mapping()
        raw["returncode"] = 1
        raw["receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "PASS receipt"):
            LeanVerifierReceipt.from_mapping(raw)
        with self.assertRaisesRegex(ValueError, "object"):
            LeanVerifierReceipt.from_mapping([])  # type: ignore[arg-type]

    def test_only_answered_attempts_are_verifier_eligible(self) -> None:
        request = self.request()
        for status, error in (
            (AttemptStatus.NO_ANSWER, None),
            (AttemptStatus.ERROR, "model failed"),
        ):
            result = self.result(request, payload="no candidate", status=status, error=error)
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "ANSWERED"):
                self.receipt(request, result)

    def test_tuple_storage_bypass_is_revalidated_at_trust_boundaries(self) -> None:
        request = self.request()
        result = self.result(request)
        receipt = self.receipt(request, result)

        forged_request_values = list(request)
        forged_request_values[0] = " run-with-spaces "
        forged_request = tuple.__new__(FrozenProblemRequest, tuple(forged_request_values))
        with self.assertRaises(ValueError):
            result.validate_for(forged_request)

        forged_receipt_values = list(receipt)
        forged_receipt_values[9] = ("lake",)
        forged_receipt_values[10] = True
        forged_receipt = tuple.__new__(LeanVerifierReceipt, tuple(forged_receipt_values))
        with self.assertRaises(ValueError):
            forged_receipt.validate_for(request, result)

    def test_contracts_are_immutable_non_subclassable_and_authority_free(self) -> None:
        request = self.request()
        result = self.result(request)
        receipt = self.receipt(request, result)
        for value, field in (
            (request, "run_id"),
            (result, "status"),
            (receipt, "returncode"),
        ):
            with self.subTest(type=type(value).__name__), self.assertRaises((AttributeError, TypeError)):
                setattr(value, field, "mutated")

        for cls in (FrozenProblemRequest, AttemptResult, LeanVerifierReceipt):
            with self.subTest(cls=cls.__name__), self.assertRaises(TypeError):
                type("Child", (cls,), {})

        forbidden = {
            "dispatch_id",
            "registration_head",
            "auth_tag",
            "cost_closed",
            "solved",
            "scientific_pass",
            "admitted",
        }
        for mapping in (request.to_mapping(), result.to_mapping(), receipt.to_mapping()):
            self.assertTrue(forbidden.isdisjoint(mapping))


if __name__ == "__main__":
    unittest.main()
