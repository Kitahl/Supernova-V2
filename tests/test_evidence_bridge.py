from __future__ import annotations

import hashlib
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.cost import (
    ArmCostTrace,
    CompleteCostReport,
    CostEvent,
    ExpectedCostEvent,
)
from supernova_goal1.dispatch import (
    CompletionPayload,
    CompletionSigner,
    DispatchAuthority,
)
from supernova_goal1.evidence_bridge import (
    EvidenceBridgeBundle,
    EvaluatorEvidenceRecord,
    ExecutionLedgerAuthority,
    bridge_closed_evidence,
)
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus


LOCK_ROOT = "914c05427e1e7e0979f4ca058f90fb3138ee0d3319233b415194c10e67d3683b"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        (
            cls.authority,
            cls.ledger,
            cls.closed,
            cls.report,
            cls.completions,
        ) = cls._build_run(
            Path(cls._tmp.name),
            run_id="run-full",
            attempts=tuple(range(16)),
            record_all=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def _build_run(
        cls,
        root: Path,
        *,
        run_id: str,
        attempts: tuple[int, ...],
        record_all: bool,
    ) -> tuple[
        DispatchAuthority,
        ExecutionLedgerAuthority,
        object,
        CompleteCostReport,
        tuple[object, ...],
    ]:
        root.mkdir(parents=True, exist_ok=True)
        authority = DispatchAuthority(
            str(Path(root, "dispatch.sqlite").resolve()),
            run_id,
        )
        execution_authority_sha256 = sha("execution-authority")
        ledger = ExecutionLedgerAuthority(
            str(Path(root, "execution.sqlite").resolve()),
            run_id=run_id,
            issuer_id="test-host",
            execution_authority_sha256=execution_authority_sha256,
            secret=b"e" * 32,
        )
        problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "test",
            "problem-001",
        )
        manifest = authority.current_manifest()
        completions = []
        events_by_arm = {arm: [] for arm in Arm}
        expected_by_arm = {arm: [] for arm in Arm}

        for attempt in attempts:
            for arm in Arm:
                request_bytes = (
                    f"prove:{problem.canonical_id}:{arm.value}:{attempt}"
                ).encode("utf-8")
                request_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
                    request_bytes,
                    kind=ScheduledChatArtifactKind.REQUEST,
                    run_id=run_id,
                    problem_id=problem.canonical_id,
                    arm=arm,
                    attempt=attempt,
                )
                request = FrozenProblemRequest(
                    run_id=run_id,
                    experiment_id="goal1-confirmatory-v1",
                    problem=problem,
                    benchmark_root_sha256=LOCK_ROOT,
                    problem_sha256=sha("problem-001"),
                    arm=arm,
                    attempt=attempt,
                    budget_id="goal1-common-envelope-v1",
                    budget_sha256=sha("budget"),
                    model_usage_basis="visible_utf8_bytes",
                    runtime_sha256=sha("lean-runtime"),
                    request_artifact=request_artifact,
                )
                signer = CompletionSigner.generate()
                manifest = authority.register(
                    manifest,
                    request=request,
                    completion_verifier_sha256=signer.public_commitment,
                )
                entry = manifest.entries[-1]

                no_answer = attempt == 15
                response_bytes = (
                    b"" if no_answer else f"by\n  exact proof_{arm.value}_{attempt}".encode("utf-8")
                )
                response_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
                    response_bytes,
                    kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
                    run_id=run_id,
                    problem_id=problem.canonical_id,
                    arm=arm,
                    attempt=attempt,
                )
                result = AttemptResult(
                    frozen_request_sha256=request.frozen_request_sha256,
                    run_id=run_id,
                    problem_id=problem.canonical_id,
                    arm=arm,
                    attempt=attempt,
                    request_artifact_id=request_artifact.artifact_id,
                    response_artifact=response_artifact,
                    status=(
                        AttemptStatus.NO_ANSWER
                        if no_answer
                        else AttemptStatus.ANSWERED
                    ),
                    error=None,
                )
                receipt = None
                verifier_ms = 0
                if not no_answer:
                    passed = (
                        (arm is Arm.VERIFIED_CHAIN and attempt == 0)
                        or (arm is Arm.PORTFOLIO and attempt == 1)
                    )
                    verifier_status = (
                        VerifierStatus.PASS if passed else VerifierStatus.FAIL
                    )
                    verifier_result = VerifierResult(
                        verifier_status,
                        ("lake", "env", "lean", "proof.lean"),
                        0 if passed else 1,
                        "ok\n" if passed else "",
                        "" if passed else "not proved\n",
                        1,
                    )
                    receipt = LeanVerifierReceipt.from_verifier_result(
                        request=request,
                        attempt_result=result,
                        verifier_result=verifier_result,
                    )
                    verifier_ms = 1
                completion = signer.complete(
                    entry=entry,
                    payload=CompletionPayload(request, result, receipt),
                )
                completions.append(completion)
                if record_all:
                    ledger._record_completion(completion)

                prefix = request.frozen_request_sha256
                expected_by_arm[arm].extend(
                    (
                        ExpectedCostEvent.scheduled_chat_model_call(
                            f"{prefix}:model"
                        ),
                        ExpectedCostEvent.verifier(f"{prefix}:verifier"),
                        ExpectedCostEvent.orchestration(
                            f"{prefix}:orchestration"
                        ),
                    )
                )
                events_by_arm[arm].extend(
                    (
                        CostEvent.scheduled_chat_model_call(
                            f"{prefix}:model",
                            request_utf8=request_bytes,
                            response_utf8=response_bytes,
                        ),
                        CostEvent.verifier(
                            f"{prefix}:verifier",
                            milliseconds=verifier_ms,
                        ),
                        CostEvent.orchestration(
                            f"{prefix}:orchestration",
                            milliseconds=1,
                        ),
                    )
                )

        closed = authority.close(manifest, tuple(completions))
        report = CompleteCostReport.from_traces(
            ArmCostTrace.from_events(
                arm,
                events_by_arm[arm],
                expected_events=expected_by_arm[arm],
                accounting_complete=True,
            )
            for arm in Arm
        )
        return authority, ledger, closed, report, tuple(completions)

    def _bridge(self, **overrides: object) -> EvidenceBridgeBundle:
        arguments = {
            "dispatch_authority": self.authority,
            "execution_ledger": self.ledger,
            "closed_join": self.closed,
            "protocol_rules_sha256": sha("protocol-rules"),
            "confirmatory_manifest_sha256": sha("confirmatory-manifest"),
            "execution_authority_sha256": sha("execution-authority"),
            "cost_reports_by_problem": {
                self.closed.joined[0].completion.payload.request.problem_id: self.report
            },
        }
        arguments.update(overrides)
        return bridge_closed_evidence(**arguments)

    def test_bridge_derives_outcomes_and_binds_all_evidence(self) -> None:
        bundle = self._bridge()
        self.assertEqual(5, len(bundle.records))
        by_arm = {record.arm: record for record in bundle.records}
        self.assertTrue(by_arm[Arm.VERIFIED_CHAIN].solved)
        self.assertFalse(by_arm[Arm.ORDINARY].solved)
        self.assertTrue(by_arm[Arm.PORTFOLIO].verifier_passed)
        self.assertEqual(16, len(by_arm[Arm.VERIFIED_CHAIN].dispatch_ids))
        self.assertEqual(
            16,
            len(by_arm[Arm.VERIFIED_CHAIN].execution_receipt_sha256s),
        )
        self.assertEqual(
            self.closed.receipt.manifest_sha256,
            by_arm[Arm.VERIFIED_CHAIN].dispatch_manifest_sha256,
        )
        mapping = by_arm[Arm.VERIFIED_CHAIN].to_evaluator_mapping()
        self.assertEqual(
            by_arm[Arm.VERIFIED_CHAIN].evidence_sha256,
            mapping["evidence_sha256"],
        )
        self.assertEqual(bundle.bridge_sha256, bundle.bridge_sha256)

    def test_bridge_api_has_no_raw_outcome_or_cost_parameter(self) -> None:
        parameters = inspect.signature(bridge_closed_evidence).parameters
        self.assertNotIn("solved", parameters)
        self.assertNotIn("verifier_passed", parameters)
        self.assertNotIn("cost", parameters)
        with self.assertRaisesRegex(TypeError, "only be produced"):
            EvaluatorEvidenceRecord(
                experiment_id="x",
                problem_id="y",
                arm=Arm.ORDINARY,
                budget_id="z",
                model_usage_basis="visible_utf8_bytes",
                cost=self.report.total_for(Arm.ORDINARY),
                completion_statuses=tuple(
                    completion.status
                    for completion in self.completions
                    if completion.payload.request.arm is Arm.ORDINARY
                ),
                protocol_rules_sha256=sha("p"),
                confirmatory_manifest_sha256=sha("m"),
                dispatch_manifest_sha256=self.closed.receipt.manifest_sha256,
                close_sha256=self.closed.receipt.close_sha256,
                completion_set_sha256=self.closed.receipt.completion_set_sha256,
                execution_authority_sha256=sha("execution-authority"),
                dispatch_ids=tuple(
                    completion.dispatch_id
                    for completion in self.completions
                    if completion.payload.request.arm is Arm.ORDINARY
                ),
                completion_record_sha256s=tuple(
                    completion.record_sha256
                    for completion in self.completions
                    if completion.payload.request.arm is Arm.ORDINARY
                ),
                verifier_evidence_sha256s=(sha("v"),) * 16,
                execution_receipt_sha256s=(sha("e"),) * 16,
                cost_trace_sha256=sha("c"),
            )

    def test_replayed_execution_receipt_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "replay rejected"):
            self.ledger._record_completion(self.completions[0])

    def test_missing_execution_receipt_is_rejected_before_evaluation(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            authority, ledger, closed, report, _ = self._build_run(
                Path(tmp.name),
                run_id="run-missing-ledger",
                attempts=(0,),
                record_all=False,
            )
            problem_id = closed.joined[0].completion.payload.request.problem_id
            with self.assertRaisesRegex(ValueError, "does not exactly cover"):
                bridge_closed_evidence(
                    dispatch_authority=authority,
                    execution_ledger=ledger,
                    closed_join=closed,
                    protocol_rules_sha256=sha("protocol-rules"),
                    confirmatory_manifest_sha256=sha("manifest"),
                    execution_authority_sha256=sha("execution-authority"),
                    cost_reports_by_problem={problem_id: report},
                )
        finally:
            tmp.cleanup()

    def test_partial_problem_arm_cells_are_rejected(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            authority, ledger, closed, report, _ = self._build_run(
                Path(tmp.name),
                run_id="run-partial",
                attempts=(0,),
                record_all=True,
            )
            problem_id = closed.joined[0].completion.payload.request.problem_id
            with self.assertRaisesRegex(ValueError, "partial or replayed"):
                bridge_closed_evidence(
                    dispatch_authority=authority,
                    execution_ledger=ledger,
                    closed_join=closed,
                    protocol_rules_sha256=sha("protocol-rules"),
                    confirmatory_manifest_sha256=sha("manifest"),
                    execution_authority_sha256=sha("execution-authority"),
                    cost_reports_by_problem={problem_id: report},
                )
        finally:
            tmp.cleanup()

    def test_unbound_cost_events_are_rejected(self) -> None:
        traces = list(self.report.traces)
        ordinary = next(trace for trace in traces if trace.arm is Arm.ORDINARY)
        events = list(ordinary.events)
        expected = list(ordinary.expected_events)
        first_event = events[0]
        first_expected = expected[0]
        events[0] = CostEvent.model_call(
            "caller-supplied-cost",
            input_tokens=first_event.input_tokens,
            output_tokens=first_event.output_tokens,
            usage_basis=first_event.model_usage_basis,
        )
        expected[0] = ExpectedCostEvent.model_call(
            "caller-supplied-cost",
            usage_basis=first_expected.model_usage_basis,
        )
        replacement = ArmCostTrace.from_events(
            Arm.ORDINARY,
            events,
            expected_events=expected,
            accounting_complete=True,
        )
        traces[traces.index(ordinary)] = replacement
        forged_report = CompleteCostReport.from_traces(traces)
        problem_id = self.closed.joined[0].completion.payload.request.problem_id
        with self.assertRaisesRegex(ValueError, "unbound, partial, or replayed"):
            self._bridge(cost_reports_by_problem={problem_id: forged_report})

    def test_cost_report_problem_coverage_is_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            self._bridge(cost_reports_by_problem={})

    def test_cross_run_or_wrong_execution_authority_is_rejected(self) -> None:
        other_tmp = tempfile.TemporaryDirectory()
        try:
            other = ExecutionLedgerAuthority(
                str(Path(other_tmp.name, "execution.sqlite").resolve()),
                run_id="other-run",
                issuer_id="test-host",
                execution_authority_sha256=sha("execution-authority"),
                secret=b"e" * 32,
            )
            with self.assertRaisesRegex(ValueError, "different run_id"):
                self._bridge(execution_ledger=other)
            with self.assertRaisesRegex(ValueError, "different execution authority"):
                self._bridge(execution_authority_sha256=sha("wrong"))
        finally:
            other_tmp.cleanup()

    def test_bridge_requires_exact_typed_authorities_and_reports(self) -> None:
        problem_id = self.closed.joined[0].completion.payload.request.problem_id
        with self.assertRaisesRegex(TypeError, "exact dict"):
            bridge_closed_evidence(
                dispatch_authority=self.authority,
                execution_ledger=self.ledger,
                closed_join=self.closed,
                protocol_rules_sha256=sha("protocol-rules"),
                confirmatory_manifest_sha256=sha("manifest"),
                execution_authority_sha256=sha("execution-authority"),
                cost_reports_by_problem={problem_id: self.report}.items(),
            )
        with self.assertRaisesRegex(TypeError, "exact CompleteCostReport"):
            self._bridge(cost_reports_by_problem={problem_id: object()})


if __name__ == "__main__":
    unittest.main()
