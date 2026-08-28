from __future__ import annotations

from hashlib import sha256
import json
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
from supernova_goal1.dispatch import DispatchAuthority
from supernova_goal1.execution.baselines import ModelAttemptObservation
from supernova_goal1.execution.common import AttemptStatus, FrozenProblemRequest
from supernova_goal1.execution.product_controls import (
    FidelityStage,
    ProductControlObservation,
    ProductObservationKind,
    VisibleProduct,
    execute_multi_fidelity_stage,
    execute_product_only_step,
    render_multi_fidelity_request,
    render_product_only_request,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class ProductControlExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.run_id = "run-controls"
        self.authority = DispatchAuthority(
            str(Path(self.tmp.name, "dispatch.sqlite").resolve()),
            self.run_id,
        )
        self.problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            "problem-001",
        )
        self.prompt = b"Prove the exact Lean theorem."

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(
        self,
        *,
        arm: Arm,
        attempt: int,
        request_utf8: bytes,
    ) -> FrozenProblemRequest:
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            request_utf8,
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id=self.run_id,
            problem_id=self.problem.canonical_id,
            arm=arm,
            attempt=attempt,
        )
        return FrozenProblemRequest(
            run_id=self.run_id,
            experiment_id="goal1-pilot-v1",
            problem=self.problem,
            benchmark_root_sha256=digest("benchmark"),
            problem_sha256=digest("problem"),
            arm=arm,
            attempt=attempt,
            budget_id="budget-v1",
            budget_sha256=digest("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=digest("lean-4.33.1"),
            request_artifact=artifact,
        )

    @staticmethod
    def passing_verifier(_dispatch, candidate: bytes) -> VerifierResult:
        if candidate != b"by\n  norm_num":
            raise AssertionError("wrong final candidate")
        return VerifierResult(
            status=VerifierStatus.PASS,
            command=("lake", "env", "lean", "proof.lean"),
            returncode=0,
            stdout="ok\n",
            stderr="",
            elapsed_milliseconds=9,
        )

    def test_product_step_emits_unverified_product_without_verifier_call(self) -> None:
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
        verifier_called = False

        def verifier(*_args):
            nonlocal verifier_called
            verifier_called = True
            raise AssertionError("intermediate product must not be verified")

        execution = execute_product_only_step(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            visible_products=(),
            retry_of_attempt=None,
            model_call=lambda dispatch, payload: ProductControlObservation(
                dispatch.entry.dispatch_id,
                ProductObservationKind.PRODUCT,
                b'{"lemma":"x + 0 = x"}',
            ),
            verifier_call=verifier,
        )

        self.assertFalse(verifier_called)
        self.assertIsNotNone(execution.emitted_product)
        self.assertEqual(
            request.frozen_request_sha256,
            execution.emitted_product.producer_frozen_request_sha256,
        )
        self.assertEqual(
            "UNVERIFIED",
            execution.emitted_product.to_mapping()["verification"],
        )
        self.assertEqual(
            AttemptStatus.NO_ANSWER,
            execution.baseline.completion.payload.attempt_result.status,
        )
        self.assertEqual(0, execution.baseline.cost_trace.total.verifier_milliseconds)

    def test_later_product_step_exposes_exact_products_and_verifies_only_answer(
        self,
    ) -> None:
        product = VisibleProduct(digest("producer"), 0, b'{"lemma":"x + 0 = x"}')
        request_utf8 = render_product_only_request(
            self.prompt,
            visible_products=(product,),
            retry_of_attempt=0,
        )
        request = self.request(
            arm=Arm.PRODUCT_ONLY,
            attempt=1,
            request_utf8=request_utf8,
        )
        seen = {}

        def model_call(dispatch, payload):
            seen["payload"] = payload
            return ProductControlObservation(
                dispatch.entry.dispatch_id,
                ProductObservationKind.ANSWERED,
                b"by\n  norm_num",
            )

        execution = execute_product_only_step(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            visible_products=(product,),
            retry_of_attempt=0,
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )

        parsed = json.loads(seen["payload"])
        self.assertEqual([product.to_mapping()], parsed["visible_products"])
        self.assertEqual(0, parsed["retry_of_attempt"])
        self.assertEqual((product.product_id,), execution.visible_product_ids)
        self.assertIsNone(execution.emitted_product)
        self.assertEqual(
            VerifierStatus.PASS,
            execution.baseline.completion.payload.verifier_receipt.status,
        )
        self.assertEqual(9, execution.baseline.cost_trace.total.verifier_milliseconds)

    def test_hidden_or_undeclared_product_visibility_is_rejected_pre_dispatch(
        self,
    ) -> None:
        frozen_utf8 = render_product_only_request(
            self.prompt,
            visible_products=(),
            retry_of_attempt=None,
        )
        request = self.request(
            arm=Arm.PRODUCT_ONLY,
            attempt=1,
            request_utf8=frozen_utf8,
        )
        hidden = VisibleProduct(digest("producer"), 0, b"hidden lemma")

        with self.assertRaisesRegex(ValueError, "visibility"):
            execute_product_only_step(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=request,
                problem_prompt_utf8=self.prompt,
                visible_products=(hidden,),
                retry_of_attempt=None,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        self.assertEqual(0, len(self.authority.current_manifest().entries))

    def test_product_retry_must_point_to_earlier_attempt(self) -> None:
        request_utf8 = render_product_only_request(
            self.prompt,
            visible_products=(),
            retry_of_attempt=1,
        )
        request = self.request(
            arm=Arm.PRODUCT_ONLY,
            attempt=1,
            request_utf8=request_utf8,
        )
        with self.assertRaisesRegex(ValueError, "precede"):
            execute_product_only_step(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=request,
                problem_prompt_utf8=self.prompt,
                visible_products=(),
                retry_of_attempt=1,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )

    def test_product_visibility_rejects_duplicate_content_addresses(self) -> None:
        left = VisibleProduct(digest("left"), 0, b"same")
        right = VisibleProduct(digest("right"), 1, b"same")
        with self.assertRaisesRegex(ValueError, "unique"):
            render_product_only_request(
                self.prompt,
                visible_products=(left, right),
                retry_of_attempt=None,
            )

    def test_multi_fidelity_stage_binds_rank_retry_and_empty_products(self) -> None:
        stage = FidelityStage("high", 2, 0)
        request_utf8 = render_multi_fidelity_request(
            self.prompt,
            stage=stage,
        )
        request = self.request(
            arm=Arm.MULTI_FIDELITY,
            attempt=1,
            request_utf8=request_utf8,
        )
        seen = {}

        def model_call(dispatch, payload):
            seen["payload"] = payload
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            )

        execution = execute_multi_fidelity_stage(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            stage=stage,
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )

        parsed = json.loads(seen["payload"])
        self.assertEqual([], parsed["visible_products"])
        self.assertEqual(
            {"fidelity_rank": 2, "retry_of_attempt": 0, "stage_id": "high"},
            parsed["stage"],
        )
        self.assertEqual(stage, execution.stage)
        self.assertEqual(VerifierStatus.PASS, execution.baseline.completion.payload.verifier_receipt.status)

    def test_multi_fidelity_stage_mismatch_is_rejected_pre_dispatch(self) -> None:
        frozen_stage = FidelityStage("low", 0)
        supplied_stage = FidelityStage("high", 1)
        request = self.request(
            arm=Arm.MULTI_FIDELITY,
            attempt=0,
            request_utf8=render_multi_fidelity_request(
                self.prompt,
                stage=frozen_stage,
            ),
        )
        with self.assertRaisesRegex(ValueError, "fidelity stage"):
            execute_multi_fidelity_stage(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=request,
                problem_prompt_utf8=self.prompt,
                stage=supplied_stage,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        self.assertEqual(0, len(self.authority.current_manifest().entries))

    def test_multi_fidelity_retry_must_point_to_earlier_attempt(self) -> None:
        stage = FidelityStage("retry", 1, 2)
        request = self.request(
            arm=Arm.MULTI_FIDELITY,
            attempt=2,
            request_utf8=render_multi_fidelity_request(
                self.prompt,
                stage=stage,
            ),
        )
        with self.assertRaisesRegex(ValueError, "precede"):
            execute_multi_fidelity_stage(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=request,
                problem_prompt_utf8=self.prompt,
                stage=stage,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )


if __name__ == "__main__":
    unittest.main()
