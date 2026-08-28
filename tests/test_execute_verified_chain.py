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
from supernova_goal1.dispatch import (
    CompletionPayload,
    CompletionRecord,
    CompletionSigner,
    DispatchAuthority,
)
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)
from supernova_goal1.execution.verified_chain import (
    AdmittedProduct,
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


class VerifiedChainExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.run_id = "run-verified-chain"
        self.authority = DispatchAuthority(
            str(Path(self.tmp.name, "dispatch.sqlite").resolve()),
            self.run_id,
        )
        self.execution_authority = VerifiedChainExecutionAuthority(
            str(Path(self.tmp.name, "verified-execution.sqlite").resolve()),
            bytes.fromhex(digest("host-owned-execution-secret")),
        )
        self.problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            "problem-001",
        )
        self.prompt = b"Prove the exact Lean theorem."
        self.product = b"lemma helper : 1 + 1 = 2 := by norm_num"
        self.product_response = render_verified_product_emission(self.product)
        self.final_answer = b"by\n  norm_num"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(
        self,
        *,
        attempt: int,
        request_utf8: bytes,
        runtime_sha256: str | None = None,
        budget_sha256: str | None = None,
    ) -> FrozenProblemRequest:
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            request_utf8,
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id=self.run_id,
            problem_id=self.problem.canonical_id,
            arm=Arm.VERIFIED_CHAIN,
            attempt=attempt,
        )
        return FrozenProblemRequest(
            run_id=self.run_id,
            experiment_id="goal1-pilot-v1",
            problem=self.problem,
            benchmark_root_sha256=digest("benchmark"),
            problem_sha256=digest("problem"),
            arm=Arm.VERIFIED_CHAIN,
            attempt=attempt,
            budget_id="budget-v1",
            budget_sha256=budget_sha256 or digest("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=runtime_sha256 or digest("lean-4.33.1"),
            request_artifact=artifact,
        )

    @staticmethod
    def verifier(status: VerifierStatus, candidate: bytes) -> VerifierResult:
        if status is VerifierStatus.PASS:
            return VerifierResult(
                status=status,
                command=("lake", "env", "lean", "proof.lean"),
                returncode=0,
                stdout=f"verified {len(candidate)} bytes\n",
                stderr="",
                elapsed_milliseconds=11,
            )
        if status is VerifierStatus.FAIL:
            return VerifierResult(
                status=status,
                command=("lake", "env", "lean", "proof.lean"),
                returncode=1,
                stdout="",
                stderr="type error\n",
                elapsed_milliseconds=7,
            )
        return VerifierResult(
            status=status,
            command=("lake", "env", "lean", "proof.lean"),
            returncode=None,
            stdout="",
            stderr="",
            elapsed_milliseconds=5,
            error="verifier unavailable",
        )

    def emit_product(
        self,
        *,
        authority: DispatchAuthority | None = None,
        manifest=None,
        attempt: int = 0,
        verifier_status: VerifierStatus = VerifierStatus.PASS,
    ):
        authority = authority or self.authority
        manifest = manifest or authority.current_manifest()
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(attempt=attempt, request_utf8=request_utf8)
        seen = {}

        def verify(_dispatch, candidate):
            seen["candidate"] = candidate
            return self.verifier(verifier_status, candidate)

        execution = execute_verified_chain_step(
            authority=authority,
            execution_authority=self.execution_authority,
            manifest=manifest,
            request=request,
            problem_prompt_utf8=self.prompt,
            admitted_products=(),
            retry_of=None,
            model_call=lambda dispatch, _payload: VerifiedChainObservation(
                dispatch.entry.dispatch_id,
                VerifiedChainObservationKind.PRODUCT,
                self.product_response,
            ),
            verifier_call=verify,
        )
        return request, execution, seen

    def test_product_is_admitted_only_after_exact_signed_bytes_pass_lean(self) -> None:
        request, execution, seen = self.emit_product()

        self.assertEqual(self.product_response, seen["candidate"])
        self.assertIsNotNone(execution.admitted_product)
        admitted = execution.admitted_product
        self.assertEqual(self.product, admitted.content_utf8)
        result = execution.baseline.completion.payload.attempt_result
        receipt = execution.baseline.completion.payload.verifier_receipt
        self.assertEqual(AttemptStatus.ANSWERED, result.status)
        self.assertEqual(VerifierStatus.PASS, receipt.status)
        self.assertEqual(result.response_artifact.artifact_id, receipt.candidate_artifact_id)
        self.assertTrue(result.response_artifact.verifies(self.product_response))
        self.assertEqual(request.frozen_request_sha256, admitted.producer_frozen_request_sha256)
        self.assertFalse(execution.terminal_answer)

    def test_later_request_contains_only_authenticated_admitted_products(self) -> None:
        first_request, first, _seen_first = self.emit_product()
        product = first.admitted_product
        self.assertIsNotNone(product)
        retry = None
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(product,),
            retry_of=retry,
        )
        request = self.request(attempt=1, request_utf8=request_utf8)
        seen = {}

        def model_call(dispatch, payload):
            seen["payload"] = payload
            return VerifiedChainObservation(
                dispatch.entry.dispatch_id,
                VerifiedChainObservationKind.ANSWERED,
                self.final_answer,
            )

        execution = execute_verified_chain_step(
            authority=self.authority,
            execution_authority=self.execution_authority,
            manifest=first.baseline.manifest,
            request=request,
            problem_prompt_utf8=self.prompt,
            admitted_products=(product,),
            retry_of=retry,
            model_call=model_call,
            verifier_call=lambda _dispatch, candidate: self.verifier(
                VerifierStatus.PASS, candidate
            ),
        )

        visible = json.loads(seen["payload"])
        expected_product = product.to_mapping()
        expected_product["execution_evidence_id"] = (
            self.execution_authority.verify_admitted_product(product)
        )
        self.assertEqual([expected_product], visible["admitted_products"])
        self.assertIsNone(visible["retry_of"])
        self.assertEqual((product.product_id,), execution.visible_product_ids)
        self.assertTrue(execution.terminal_answer)
        self.assertIsNone(execution.admitted_product)

    def test_failed_product_is_never_admitted(self) -> None:
        _request, execution, _seen = self.emit_product(
            verifier_status=VerifierStatus.FAIL
        )
        self.assertIsNone(execution.admitted_product)
        self.assertFalse(execution.terminal_answer)
        self.assertEqual(
            VerifierStatus.FAIL,
            execution.baseline.completion.payload.verifier_receipt.status,
        )
        with self.assertRaisesRegex(ValueError, "PASS"):
            AdmittedProduct(
                execution.baseline.completion,
                self.product_response,
            )

    def test_verifier_error_is_never_admitted(self) -> None:
        _request, execution, _seen = self.emit_product(
            verifier_status=VerifierStatus.ERROR
        )
        self.assertIsNone(execution.admitted_product)
        self.assertEqual(
            VerifierStatus.ERROR,
            execution.baseline.completion.payload.verifier_receipt.status,
        )

    def test_hidden_product_visibility_mismatch_is_rejected_pre_dispatch(self) -> None:
        _first_request, first, _seen = self.emit_product()
        product = first.admitted_product
        self.assertIsNotNone(product)
        request = self.request(
            attempt=1,
            request_utf8=render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(),
                retry_of=None,
            ),
        )
        with self.assertRaisesRegex(ValueError, "visibility"):
            execute_verified_chain_step(
                authority=self.authority,
                execution_authority=self.execution_authority,
                manifest=first.baseline.manifest,
                request=request,
                problem_prompt_utf8=self.prompt,
                admitted_products=(product,),
                retry_of=None,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        self.assertEqual(1, len(self.authority.current_manifest().entries))

    def test_product_from_different_runtime_cell_is_rejected(self) -> None:
        _first_request, first, _seen = self.emit_product()
        product = first.admitted_product
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(product,),
            retry_of=None,
        )
        request = self.request(
            attempt=1,
            request_utf8=request_utf8,
            runtime_sha256=digest("different-runtime"),
        )
        with self.assertRaisesRegex(ValueError, "frozen cell"):
            execute_verified_chain_step(
                authority=self.authority,
                execution_authority=self.execution_authority,
                manifest=first.baseline.manifest,
                request=request,
                problem_prompt_utf8=self.prompt,
                admitted_products=(product,),
                retry_of=None,
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )

    def test_forged_producer_signature_is_rejected_before_dispatch(self) -> None:
        _first_request, first, _seen = self.emit_product()
        product = first.admitted_product
        record = product.producer_completion
        forged_record = CompletionRecord(
            record.run_id,
            record.dispatch_id,
            record.entry_sha256,
            record.payload,
            record.verifier_public_key,
            "0" * len(record.signature),
        )
        forged = AdmittedProduct(forged_record, product.producer_response_utf8)
        with self.assertRaisesRegex(ValueError, "trusted execution record"):
            render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(forged,),
                retry_of=None,
            )
        self.assertEqual(1, len(self.authority.current_manifest().entries))

    def test_product_from_separate_authority_is_off_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as other_tmp:
            other = DispatchAuthority(
                str(Path(other_tmp, "dispatch.sqlite").resolve()),
                self.run_id,
            )
            _request, foreign, _seen = self.emit_product(authority=other)
            product = foreign.admitted_product
            request_utf8 = render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(product,),
                retry_of=None,
            )
            request = self.request(attempt=1, request_utf8=request_utf8)
            with self.assertRaisesRegex(ValueError, "authority"):
                execute_verified_chain_step(
                    authority=self.authority,
                    execution_authority=self.execution_authority,
                    manifest=foreign.baseline.manifest,
                    request=request,
                    problem_prompt_utf8=self.prompt,
                    admitted_products=(product,),
                    retry_of=None,
                    model_call=lambda *_: None,
                    verifier_call=lambda *_: None,
                )
            self.assertEqual(0, len(self.authority.current_manifest().entries))

    def test_cross_dispatch_observation_cannot_admit_product(self) -> None:
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(attempt=0, request_utf8=request_utf8)
        execution = execute_verified_chain_step(
            authority=self.authority,
            execution_authority=self.execution_authority,
            manifest=self.authority.current_manifest(),
            request=request,
            problem_prompt_utf8=self.prompt,
            admitted_products=(),
            retry_of=None,
            model_call=lambda *_: VerifiedChainObservation(
                "0" * 64,
                VerifiedChainObservationKind.PRODUCT,
                self.product_response,
            ),
            verifier_call=lambda _dispatch, candidate: self.verifier(
                VerifierStatus.PASS, candidate
            ),
        )
        self.assertIsNone(execution.admitted_product)
        self.assertEqual(
            AttemptStatus.ERROR,
            execution.baseline.completion.payload.attempt_result.status,
        )
        self.assertIsNone(execution.baseline.completion.payload.verifier_receipt)

    def test_product_discriminator_cannot_be_reclassified_as_final_answer(self) -> None:
        with self.assertRaisesRegex(ValueError, "PRODUCT discriminator"):
            VerifiedChainObservation(
                "0" * 64,
                VerifiedChainObservationKind.ANSWERED,
                self.product_response,
            )

    def test_duplicate_products_and_producer_dispatches_are_rejected(self) -> None:
        _request, first, _seen = self.emit_product()
        product = first.admitted_product
        with self.assertRaisesRegex(ValueError, "product_ids"):
            render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(product, product),
                retry_of=None,
            )

    def test_signed_failed_completion_can_authorize_exact_retry(self) -> None:
        failed_request, failed, _seen = self.emit_product(
            verifier_status=VerifierStatus.FAIL
        )
        retry = RetryLink(failed.baseline.completion)
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=retry,
        )
        request = self.request(attempt=1, request_utf8=request_utf8)
        execution = execute_verified_chain_step(
            authority=self.authority,
            execution_authority=self.execution_authority,
            manifest=failed.baseline.manifest,
            request=request,
            problem_prompt_utf8=self.prompt,
            admitted_products=(),
            retry_of=retry,
            model_call=lambda dispatch, _payload: VerifiedChainObservation(
                dispatch.entry.dispatch_id,
                VerifiedChainObservationKind.ANSWERED,
                self.final_answer,
            ),
            verifier_call=lambda _dispatch, candidate: self.verifier(
                VerifierStatus.PASS, candidate
            ),
        )
        self.assertTrue(execution.terminal_answer)
        self.assertEqual(
            failed_request.frozen_request_sha256,
            execution.retry_of.frozen_request_sha256,
        )

    def test_registered_but_uncompleted_request_cannot_authorize_retry(self) -> None:
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(attempt=0, request_utf8=request_utf8)
        signer = CompletionSigner.generate()
        manifest = self.authority.register(
            self.authority.current_manifest(),
            request=request,
            completion_verifier_sha256=signer.public_commitment,
        )
        entry = manifest.entries[-1]
        with self.assertRaisesRegex(TypeError, "CompletionRecord"):
            RetryLink(entry)

    def test_self_registered_fake_pass_is_not_admitted(self) -> None:
        request_utf8 = render_verified_chain_request(
            self.prompt,
            execution_authority=self.execution_authority,
            admitted_products=(),
            retry_of=None,
        )
        request = self.request(attempt=0, request_utf8=request_utf8)
        signer = CompletionSigner.generate()
        manifest = self.authority.register(
            self.authority.current_manifest(),
            request=request,
            completion_verifier_sha256=signer.public_commitment,
        )
        entry = manifest.entries[-1]
        response_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            self.product_response,
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
            status=AttemptStatus.ANSWERED,
            error=None,
        )
        fake_pass = self.verifier(VerifierStatus.PASS, self.product_response)
        receipt = LeanVerifierReceipt.from_verifier_result(
            request=request,
            attempt_result=result,
            verifier_result=fake_pass,
        )
        completion = signer.complete(
            entry=entry,
            payload=CompletionPayload(request, result, receipt),
        )
        structurally_valid_fake = AdmittedProduct(
            completion,
            self.product_response,
        )
        with self.assertRaisesRegex(ValueError, "absent from trusted execution"):
            render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(structurally_valid_fake,),
                retry_of=None,
            )

    def test_forged_retry_completion_is_rejected_before_dispatch(self) -> None:
        _failed_request, failed, _seen = self.emit_product(
            verifier_status=VerifierStatus.FAIL
        )
        record = failed.baseline.completion
        forged_record = CompletionRecord(
            record.run_id,
            record.dispatch_id,
            record.entry_sha256,
            record.payload,
            record.verifier_public_key,
            "0" * len(record.signature),
        )
        retry = RetryLink(forged_record)
        with self.assertRaisesRegex(ValueError, "trusted execution record"):
            render_verified_chain_request(
                self.prompt,
                execution_authority=self.execution_authority,
                admitted_products=(),
                retry_of=retry,
            )
        self.assertEqual(1, len(self.authority.current_manifest().entries))

    def test_lean_pass_completion_is_feed_forward_not_retry(self) -> None:
        _request, passed, _seen = self.emit_product()
        with self.assertRaisesRegex(ValueError, "feeds forward"):
            RetryLink(passed.baseline.completion)


if __name__ == "__main__":
    unittest.main()
