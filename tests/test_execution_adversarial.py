from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import (
    CompletionPayload,
    CompletionSigner,
    DispatchAuthority,
)
from supernova_goal1.evaluate import evaluate_experiment
from supernova_goal1.execution.baselines import (
    ModelAttemptObservation,
    execute_ordinary,
)
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
)
from supernova_goal1.execution.product_controls import (
    ProductControlObservation,
    ProductObservationKind,
    execute_product_only_step,
    render_product_emission,
    render_product_only_request,
)
from supernova_goal1.execution.verified_chain import (
    RetryLink,
    VerifiedChainExecutionAuthority,
    VerifiedChainObservation,
    VerifiedChainObservationKind,
    execute_verified_chain_step,
    render_verified_chain_request,
    render_verified_product_emission,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class ExecutionAdversarialIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.run_id = "run-adversarial-integration"
        self.authority = DispatchAuthority(
            str(Path(self.tmp.name, "dispatch.sqlite").resolve()),
            self.run_id,
        )
        self.execution_authority = VerifiedChainExecutionAuthority(
            str(Path(self.tmp.name, "verified-execution.sqlite").resolve()),
            bytes.fromhex(digest("trusted-host-secret")),
        )
        self.prompt = b"Prove the exact frozen theorem."
        self.verified_product_response = render_verified_product_emission(
            b"lemma helper : 2 + 2 = 4 := by norm_num"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def problem(split: str = "validation") -> BenchmarkProblemIdentity:
        return BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            split,
            "problem-001",
        )

    def request(
        self,
        *,
        arm: Arm,
        attempt: int,
        request_utf8: bytes,
        problem: BenchmarkProblemIdentity | None = None,
        runtime_sha256: str | None = None,
    ) -> FrozenProblemRequest:
        problem = problem or self.problem()
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            request_utf8,
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id=self.run_id,
            problem_id=problem.canonical_id,
            arm=arm,
            attempt=attempt,
        )
        return FrozenProblemRequest(
            run_id=self.run_id,
            experiment_id="goal1-pilot-v1",
            problem=problem,
            benchmark_root_sha256=digest("benchmark-root"),
            problem_sha256=digest(f"{problem.split}:{problem.native_id}"),
            arm=arm,
            attempt=attempt,
            budget_id="budget-v1",
            budget_sha256=digest("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=runtime_sha256 or digest("lean-4.33.1"),
            request_artifact=artifact,
        )

    @staticmethod
    def verifier(status: VerifierStatus = VerifierStatus.PASS) -> VerifierResult:
        if status is VerifierStatus.PASS:
            return VerifierResult(
                status,
                ("lake", "env", "lean", "proof.lean"),
                0,
                "ok\n",
                "",
                4,
            )
        return VerifierResult(
            status,
            ("lake", "env", "lean", "proof.lean"),
            1,
            "",
            "rejected\n",
            4,
        )

    def emit_verified_product(self):
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(
            arm=Arm.VERIFIED_CHAIN,
            attempt=0,
            request_utf8=request_utf8,
        )
        execution = execute_verified_chain_step(
            authority=self.authority,
            execution_authority=self.execution_authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            admitted_products=(),
            retry_of=None,
            model_call=lambda dispatch, _payload: VerifiedChainObservation(
                dispatch.entry.dispatch_id,
                VerifiedChainObservationKind.PRODUCT,
                self.verified_product_response,
            ),
            verifier_call=lambda *_: self.verifier(),
        )
        self.assertIsNotNone(execution.admitted_product)
        return execution

    def emit_unverified_product(self):
        request_utf8 = render_product_only_request(
            self.prompt,
            visible_products=(),
            retry_of_attempt=None,
        )
        request = self.request(
            arm=Arm.PRODUCT_ONLY,
            attempt=0,
            request_utf8=request_utf8,
        )
        execution = execute_product_only_step(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            visible_products=(),
            retry_of_attempt=None,
            model_call=lambda dispatch, _payload: ProductControlObservation(
                dispatch.entry.dispatch_id,
                ProductObservationKind.PRODUCT,
                render_product_emission(b"unverified helper"),
            ),
            verifier_call=lambda *_: (_ for _ in ()).throw(
                AssertionError("product-only intermediate must not call Lean")
            ),
        )
        self.assertIsNotNone(execution.emitted_product)
        return execution

    def test_split_leakage_cannot_reuse_validated_product_in_test_cell(self) -> None:
        producer = self.emit_verified_product()
        product = producer.admitted_product
        test_problem = self.problem("test")
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(product,),
            retry_of=None,
        )
        request = self.request(
            arm=Arm.VERIFIED_CHAIN,
            attempt=1,
            request_utf8=request_utf8,
            problem=test_problem,
        )
        model_called = False

        def model_call(*_args):
            nonlocal model_called
            model_called = True
            raise AssertionError("split leakage must fail before model dispatch")

        with self.assertRaisesRegex(ValueError, "frozen cell"):
            execute_verified_chain_step(
                authority=self.authority,
                execution_authority=self.execution_authority,
                manifest=producer.baseline.manifest,
                request=request,
                problem_prompt_utf8=self.prompt,
                admitted_products=(product,),
                retry_of=None,
                model_call=model_call,
                verifier_call=lambda *_: self.verifier(),
            )
        self.assertFalse(model_called)

    def test_hidden_product_reuse_fails_the_frozen_visibility_boundary(self) -> None:
        producer = self.emit_unverified_product()
        product = producer.emitted_product
        frozen_without_product = render_product_only_request(
            self.prompt,
            visible_products=(),
            retry_of_attempt=None,
        )
        request = self.request(
            arm=Arm.PRODUCT_ONLY,
            attempt=1,
            request_utf8=frozen_without_product,
        )
        model_called = False

        def model_call(*_args):
            nonlocal model_called
            model_called = True
            raise AssertionError("hidden product must fail before dispatch")

        with self.assertRaisesRegex(ValueError, "visibility"):
            execute_product_only_step(
                authority=self.authority,
                manifest=producer.baseline.manifest,
                request=request,
                problem_prompt_utf8=self.prompt,
                visible_products=(product,),
                retry_of_attempt=None,
                model_call=model_call,
                verifier_call=lambda *_: self.verifier(),
            )
        self.assertFalse(model_called)

    def test_dispatch_registration_precedes_model_observation(self) -> None:
        request_utf8 = b"ordinary frozen request"
        request = self.request(
            arm=Arm.ORDINARY,
            attempt=0,
            request_utf8=request_utf8,
        )
        observed_registered_entry = False

        def model_call(dispatch, _payload):
            nonlocal observed_registered_entry
            observed_registered_entry = any(
                entry.dispatch_id == dispatch.entry.dispatch_id
                and entry.request_sha256 == request.frozen_request_sha256
                for entry in self.authority.current_manifest().entries
            )
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"",
                AttemptStatus.NO_ANSWER,
            )

        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            request_utf8=request_utf8,
            model_call=model_call,
            verifier_call=lambda *_: self.verifier(),
        )
        self.assertTrue(observed_registered_entry)
        self.assertEqual(
            AttemptStatus.NO_ANSWER,
            execution.completion.payload.attempt_result.status,
        )
        self.assertTrue(execution.cost_trace.coverage_complete)

    def test_signed_but_unexecuted_completion_cannot_authorize_retry(self) -> None:
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(
            arm=Arm.VERIFIED_CHAIN,
            attempt=0,
            request_utf8=request_utf8,
        )
        signer = CompletionSigner.generate()
        manifest = self.authority.register(
            self.authority.current_manifest(),
            request=request,
            completion_verifier_sha256=signer.public_commitment,
        )
        response_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            b"",
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
        )
        result = AttemptResult(
            frozen_request_sha256=request.frozen_request_sha256,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
            request_artifact_id=request.request_artifact.artifact_id,
            response_artifact=response_artifact,
            status=AttemptStatus.NO_ANSWER,
            error=None,
        )
        fabricated_terminal = signer.complete(
            entry=manifest.entries[-1],
            payload=CompletionPayload(request, result, None),
        )
        retry = RetryLink(fabricated_terminal)
        with self.assertRaisesRegex(ValueError, "absent from trusted execution"):
            render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(),
                retry_of=retry,
            )
        self.assertEqual(1, len(self.authority.current_manifest().entries))


    def test_wrong_lean_runtime_cannot_consume_prior_product(self) -> None:
        producer = self.emit_verified_product()
        product = producer.admitted_product
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(product,),
            retry_of=None,
        )
        request = self.request(
            arm=Arm.VERIFIED_CHAIN,
            attempt=1,
            request_utf8=request_utf8,
            runtime_sha256=digest("lean-wrong-runtime"),
        )
        with self.assertRaisesRegex(ValueError, "frozen cell"):
            execute_verified_chain_step(
                authority=self.authority,
                execution_authority=self.execution_authority,
                manifest=producer.baseline.manifest,
                request=request,
                problem_prompt_utf8=self.prompt,
                admitted_products=(product,),
                retry_of=None,
                model_call=lambda *_: None,
                verifier_call=lambda *_: self.verifier(),
            )

    @staticmethod
    def frozen_spec() -> dict[str, object]:
        return {
            "experiment_id": "goal1-adversarial",
            "phase": "CONFIRMATORY",
            "required_problem_ids": ["p1"],
            "cost_model_frozen": True,
            "model_usage_basis": "visible_utf8_bytes",
            "budget_id": "budget-v1",
            "budget_ceiling": {
                "model_calls": 10,
                "input_tokens": 1000,
                "output_tokens": 1000,
                "verifier_milliseconds": 1000,
                "orchestration_milliseconds": 1000,
            },
            "familywise_alpha": 0.05,
        }

    @staticmethod
    def outcome(arm: Arm) -> dict[str, object]:
        return {
            "experiment_id": "goal1-adversarial",
            "problem_id": "p1",
            "arm": arm.value,
            "budget_id": "budget-v1",
            "model_usage_basis": "visible_utf8_bytes",
            "solved": False,
            "verifier_passed": False,
            "cost": {
                "model_calls": 1,
                "input_tokens": 10,
                "output_tokens": 10,
                "verifier_milliseconds": 1,
                "orchestration_milliseconds": 1,
            },
        }

    def test_missing_required_telemetry_is_not_zero_filled(self) -> None:
        record = self.outcome(Arm.ORDINARY)
        del record["cost"]["orchestration_milliseconds"]
        with self.assertRaisesRegex(ValueError, "cost fields"):
            evaluate_experiment(self.frozen_spec(), [record])

    def test_partial_five_arm_cohort_is_incomplete_not_fail(self) -> None:
        records = [
            self.outcome(arm)
            for arm in (
                Arm.ORDINARY,
                Arm.PORTFOLIO,
                Arm.PRODUCT_ONLY,
                Arm.MULTI_FIDELITY,
            )
        ]
        result = evaluate_experiment(self.frozen_spec(), records)
        self.assertEqual("INCOMPLETE", result["decision"])
        self.assertEqual(
            [{"problem_id": "p1", "arm": Arm.VERIFIED_CHAIN.value}],
            result["missing"],
        )
        self.assertEqual([], result["pairwise"])


if __name__ == "__main__":
    unittest.main()
