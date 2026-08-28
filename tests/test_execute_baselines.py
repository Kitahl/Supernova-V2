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
from supernova_goal1.dispatch import DispatchAuthority
from supernova_goal1.execution.baselines import (
    ModelAttemptObservation,
    execute_ordinary,
    execute_portfolio_attempt,
)
from supernova_goal1.execution.common import AttemptStatus, FrozenProblemRequest
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class BaselineExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.run_id = "run-baselines"
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(
        self,
        *,
        arm: Arm = Arm.ORDINARY,
        attempt: int = 0,
        payload: bytes = b"Prove the exact Lean theorem.",
        usage_basis: str = "visible_utf8_bytes",
    ) -> FrozenProblemRequest:
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            payload,
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
            model_usage_basis=usage_basis,
            runtime_sha256=digest("lean-4.33.1"),
            request_artifact=artifact,
        )

    @staticmethod
    def passing_verifier(_dispatch, candidate: bytes) -> VerifierResult:
        if candidate != b"by\n  norm_num":
            raise AssertionError("verifier received wrong candidate bytes")
        return VerifierResult(
            status=VerifierStatus.PASS,
            command=("lake", "env", "lean", "proof.lean"),
            returncode=0,
            stdout="ok\n",
            stderr="",
            elapsed_milliseconds=11,
        )

    def test_ordinary_preregisters_dispatch_and_cost_manifest_before_model_call(
        self,
    ) -> None:
        request = self.request()
        observed = {}

        def model_call(dispatch, request_utf8):
            observed["manifest"] = self.authority.current_manifest()
            observed["dispatch"] = dispatch
            observed["request_utf8"] = request_utf8
            self.assertEqual(dispatch.entry, observed["manifest"].entries[-1])
            self.assertEqual(
                ["model", "verifier", "orchestration"],
                [
                    event.event_id.rsplit(":", 1)[-1]
                    for event in dispatch.expected_events
                ],
            )
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            )

        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=request,
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )

        self.assertEqual(b"Prove the exact Lean theorem.", observed["request_utf8"])
        self.assertEqual(1, len(execution.manifest.entries))
        self.assertEqual(
            execution.manifest.entries[-1].dispatch_id,
            execution.completion.dispatch_id,
        )
        self.assertEqual(
            AttemptStatus.ANSWERED,
            execution.completion.payload.attempt_result.status,
        )
        self.assertEqual(VerifierStatus.PASS, execution.completion.payload.verifier_receipt.status)
        self.assertTrue(execution.cost_trace.coverage_complete)
        self.assertTrue(execution.cost_trace.measurements_complete)
        self.assertTrue(execution.cost_trace.accounting_complete)
        self.assertEqual(1, execution.cost_trace.total.model_calls)
        self.assertEqual(len(b"Prove the exact Lean theorem."), execution.cost_trace.total.input_tokens)
        self.assertEqual(len(b"by\n  norm_num"), execution.cost_trace.total.output_tokens)
        self.assertEqual(11, execution.cost_trace.total.verifier_milliseconds)

    def test_portfolio_attempts_are_independent_append_only_dispatches(self) -> None:
        seen = []

        def model_call(dispatch, _request_utf8):
            seen.append(
                (
                    dispatch.request.attempt,
                    len(self.authority.current_manifest().entries),
                )
            )
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            )

        first = execute_portfolio_attempt(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(arm=Arm.PORTFOLIO, attempt=0),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )
        second = execute_portfolio_attempt(
            authority=self.authority,
            manifest=first.manifest,
            request=self.request(arm=Arm.PORTFOLIO, attempt=1),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )

        self.assertEqual([(0, 1), (1, 2)], seen)
        self.assertEqual(2, len(second.manifest.entries))
        self.assertEqual(
            first.manifest.entries[-1].entry_sha256,
            second.manifest.entries[-1].predecessor_sha256,
        )
        self.assertNotIn(
            "prior",
            execute_portfolio_attempt.__code__.co_varnames,
        )

    def test_wrong_arm_bytes_and_usage_basis_fail_before_registration(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot execute"):
            execute_ordinary(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=self.request(arm=Arm.PORTFOLIO),
                request_utf8=b"Prove the exact Lean theorem.",
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            execute_ordinary(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=self.request(),
                request_utf8=b"different bytes",
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        with self.assertRaisesRegex(ValueError, "visible_utf8_bytes"):
            execute_ordinary(
                authority=self.authority,
                manifest=self.authority.current_manifest(),
                request=self.request(usage_basis="provider_tokens"),
                request_utf8=b"Prove the exact Lean theorem.",
                model_call=lambda *_: None,
                verifier_call=lambda *_: None,
            )
        self.assertEqual(0, len(self.authority.current_manifest().entries))

    def test_completion_bundle_rejects_cross_manifest_substitution(self) -> None:
        from supernova_goal1.execution.baselines import BaselineExecution

        first = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(attempt=0),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=lambda dispatch, *_: ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            ),
            verifier_call=self.passing_verifier,
        )
        second = execute_ordinary(
            authority=self.authority,
            manifest=first.manifest,
            request=self.request(attempt=1),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=lambda dispatch, *_: ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            ),
            verifier_call=self.passing_verifier,
        )
        with self.assertRaisesRegex(ValueError, "manifest entry"):
            BaselineExecution(
                first.manifest,
                second.completion,
                second.cost_trace,
            )

    def test_portfolio_rejects_replayed_observation_from_prior_dispatch(self) -> None:
        saved = {}

        def model_call(dispatch, _request_utf8):
            if "observation" not in saved:
                saved["observation"] = ModelAttemptObservation(
                    dispatch.entry.dispatch_id,
                    b"by\n  norm_num",
                    AttemptStatus.ANSWERED,
                )
            return saved["observation"]

        first = execute_portfolio_attempt(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(arm=Arm.PORTFOLIO, attempt=0),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )
        second = execute_portfolio_attempt(
            authority=self.authority,
            manifest=first.manifest,
            request=self.request(arm=Arm.PORTFOLIO, attempt=1),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=self.passing_verifier,
        )
        self.assertEqual(
            AttemptStatus.ERROR,
            second.completion.payload.attempt_result.status,
        )
        self.assertIn(
            "different dispatch",
            second.completion.payload.attempt_result.error,
        )

    def test_model_exception_becomes_signed_terminal_error(self) -> None:
        verifier_called = False

        def model_call(*_args):
            raise RuntimeError("secret details must not escape")

        def verifier_call(*_args):
            nonlocal verifier_called
            verifier_called = True
            raise AssertionError("verifier must not run")

        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=model_call,
            verifier_call=verifier_call,
        )

        result = execution.completion.payload.attempt_result
        self.assertEqual(AttemptStatus.ERROR, result.status)
        self.assertEqual("model_call raised RuntimeError", result.error)
        self.assertNotIn("secret", result.error)
        self.assertIsNone(execution.completion.payload.verifier_receipt)
        self.assertFalse(verifier_called)
        self.assertEqual(0, execution.cost_trace.total.verifier_milliseconds)

    def test_untyped_model_result_is_audited_as_error(self) -> None:
        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=lambda *_: {"answer": "untyped"},
            verifier_call=self.passing_verifier,
        )
        result = execution.completion.payload.attempt_result
        self.assertEqual(AttemptStatus.ERROR, result.status)
        self.assertIn("non-ModelAttemptObservation", result.error)

    def test_verifier_exception_becomes_error_receipt_bound_to_frozen_runtime(
        self,
    ) -> None:
        def verifier_call(*_args):
            raise OSError("host detail")

        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=lambda dispatch, *_: ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"by\n  norm_num",
                AttemptStatus.ANSWERED,
            ),
            verifier_call=verifier_call,
        )

        receipt = execution.completion.payload.verifier_receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(VerifierStatus.ERROR, receipt.status)
        self.assertEqual(digest("lean-4.33.1"), receipt.runtime_sha256)
        self.assertEqual("verifier_call raised OSError", receipt.error)
        self.assertEqual(0, execution.cost_trace.total.verifier_milliseconds)

    def test_no_answer_records_explicit_zero_verifier_boundary(self) -> None:
        verifier_called = False

        def verifier_call(*_args):
            nonlocal verifier_called
            verifier_called = True
            raise AssertionError("verifier must not run")

        execution = execute_ordinary(
            authority=self.authority,
            manifest=self.authority.current_manifest(),
            request=self.request(),
            request_utf8=b"Prove the exact Lean theorem.",
            model_call=lambda dispatch, *_: ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                b"",
                AttemptStatus.NO_ANSWER,
            ),
            verifier_call=verifier_call,
        )

        self.assertFalse(verifier_called)
        self.assertEqual(
            AttemptStatus.NO_ANSWER,
            execution.completion.payload.attempt_result.status,
        )
        self.assertIsNone(execution.completion.payload.verifier_receipt)
        verifier_events = [
            event
            for event in execution.cost_trace.events
            if event.event_id.endswith(":verifier")
        ]
        self.assertEqual(1, len(verifier_events))
        self.assertEqual(0, verifier_events[0].milliseconds)


if __name__ == "__main__":
    unittest.main()
