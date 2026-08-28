from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.confirmatory_manifest import (
    NON_CREDIT_DRAFT,
    build_non_credit_draft,
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


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(
                encoding="utf-8"
            )
        )
        cls.manifest_bundle = build_non_credit_draft(
            cls.protocol,
            operator_seed=b"o" * 32,
        )
        cls.native_problem_id = cls.manifest_bundle.operator_plan["entries"][0][
            "problem_id"
        ]
        cls.fixture_operator_plan = json.loads(
            json.dumps(cls.manifest_bundle.operator_plan)
        )
        cls.fixture_operator_plan["entries"] = [
            entry
            for entry in cls.fixture_operator_plan["entries"]
            if entry["problem_id"] == cls.native_problem_id
        ]
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
            str(Path(root, "dispatch.sqlite").resolve()), run_id
        )
        ledger = ExecutionLedgerAuthority(
            str(Path(root, "execution.sqlite").resolve()),
            run_id=run_id,
            issuer_id="test-host",
            execution_authority_sha256=sha("non-credit-test-authority"),
            secret=b"e" * 32,
            protocol=cls.protocol,
            public_manifest=cls.manifest_bundle.public_manifest,
            operator_plan=cls.manifest_bundle.operator_plan,
        )
        problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "test",
            cls.native_problem_id,
        )
        bindings = cls.manifest_bundle.public_manifest["bindings"]
        benchmark_root = cls.protocol["sealed_rules"]["benchmark_selection"][
            "benchmark_root_sha256"
        ]
        manifest = authority.current_manifest()
        completions = []
        events_by_arm = {arm: [] for arm in Arm}
        expected_by_arm = {arm: [] for arm in Arm}

        for attempt in attempts:
            for arm in Arm:
                request_bytes = (
                    f"prove:{problem.native_id}:{arm.value}:{attempt}"
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
                    experiment_id=cls.protocol["protocol_id"],
                    problem=problem,
                    benchmark_root_sha256=benchmark_root,
                    problem_sha256=sha("problem:" + problem.native_id),
                    arm=arm,
                    attempt=attempt,
                    budget_id="goal1-common-envelope-v1",
                    budget_sha256=bindings["cost_policy_sha256"],
                    model_usage_basis="visible_utf8_bytes",
                    runtime_sha256=bindings["runtime_sha256"],
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
                    b""
                    if no_answer
                    else f"by\n  exact proof_{arm.value}_{attempt}".encode("utf-8")
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
                    status = VerifierStatus.PASS if passed else VerifierStatus.FAIL
                    verifier_result = VerifierResult(
                        status,
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
                    ledger._record_completion(
                        completion,
                        context_isolation_receipt=(
                            ledger._issue_context_isolation_receipt(completion)
                        ),
                        predecessor_reconciliation_receipt=(
                            ledger._issue_predecessor_reconciliation_receipt(
                                completion
                            )
                        ),
                        orchestration_milliseconds=1,
                    )

                prefix = request.frozen_request_sha256
                expected_by_arm[arm].extend(
                    (
                        ExpectedCostEvent.scheduled_chat_model_call(
                            f"{prefix}:model"
                        ),
                        ExpectedCostEvent.context_isolation(
                            f"{prefix}:context_isolation"
                        ),
                        ExpectedCostEvent.verifier(f"{prefix}:verifier"),
                        ExpectedCostEvent.orchestration(
                            f"{prefix}:orchestration"
                        ),
                        ExpectedCostEvent.predecessor_reconciliation(
                            f"{prefix}:predecessor_reconciliation"
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
                        CostEvent.context_isolation(
                            f"{prefix}:context_isolation"
                        ),
                        CostEvent.verifier(
                            f"{prefix}:verifier", milliseconds=verifier_ms
                        ),
                        CostEvent.orchestration(
                            f"{prefix}:orchestration", milliseconds=1
                        ),
                        CostEvent.predecessor_reconciliation(
                            f"{prefix}:predecessor_reconciliation"
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
            "protocol": self.protocol,
            "public_manifest": self.manifest_bundle.public_manifest,
            "operator_plan": self.fixture_operator_plan,
            "cost_reports_by_problem": {
                self.native_problem_id: self.report
            },
        }
        arguments.update(overrides)
        with patch(
            "supernova_goal1.evidence_bridge.validate_draft_bundle",
            return_value=None,
        ):
            return bridge_closed_evidence(**arguments)

    def test_bridge_derives_outcomes_and_binds_all_five_evidence_classes(self) -> None:
        bundle = self._bridge()
        self.assertEqual(NON_CREDIT_DRAFT, bundle.manifest_credit_status)
        self.assertEqual(5, len(bundle.records))
        by_arm = {record.arm: record for record in bundle.records}
        self.assertTrue(by_arm[Arm.VERIFIED_CHAIN].solved)
        self.assertFalse(by_arm[Arm.ORDINARY].solved)
        self.assertTrue(by_arm[Arm.PORTFOLIO].verifier_passed)
        record = by_arm[Arm.VERIFIED_CHAIN]
        self.assertEqual(self.native_problem_id, record.problem_id)
        self.assertEqual(16, len(record.protocol_dispatch_ids))
        self.assertEqual(16, len(record.execution_receipt_sha256s))
        self.assertEqual(16, len(record.context_isolation_receipt_sha256s))
        self.assertEqual(16, len(record.predecessor_reconciliation_sha256s))
        mapping = record.to_evaluator_mapping()
        self.assertEqual(record.evidence_sha256, mapping["evidence_sha256"])
        self.assertEqual(bundle.bridge_sha256, bundle.bridge_sha256)

    def test_bridge_api_has_no_raw_outcome_hash_or_cost_parameter(self) -> None:
        parameters = inspect.signature(bridge_closed_evidence).parameters
        for forbidden in (
            "solved",
            "verifier_passed",
            "cost",
            "protocol_rules_sha256",
            "confirmatory_manifest_sha256",
            "execution_authority_sha256",
        ):
            self.assertNotIn(forbidden, parameters)
        with self.assertRaisesRegex(TypeError, "only be produced"):
            EvaluatorEvidenceRecord(solved=True, cost=True)

    def test_arbitrary_protocol_or_manifest_relabeling_is_rejected(self) -> None:
        bad_protocol = json.loads(json.dumps(self.protocol))
        bad_protocol["sealed_rules"]["paired_design"]["attempts_per_problem_arm"] = 1
        with self.assertRaises(ValueError):
            bridge_closed_evidence(
                dispatch_authority=self.authority,
                execution_ledger=self.ledger,
                closed_join=self.closed,
                protocol=bad_protocol,
                public_manifest=self.manifest_bundle.public_manifest,
                operator_plan=self.manifest_bundle.operator_plan,
                cost_reports_by_problem={self.native_problem_id: self.report},
            )
        bad_manifest = json.loads(
            json.dumps(self.manifest_bundle.public_manifest)
        )
        bad_manifest["protocol_id"] = "caller-relabel"
        with self.assertRaises(ValueError):
            bridge_closed_evidence(
                dispatch_authority=self.authority,
                execution_ledger=self.ledger,
                closed_join=self.closed,
                protocol=self.protocol,
                public_manifest=bad_manifest,
                operator_plan=self.manifest_bundle.operator_plan,
                cost_reports_by_problem={self.native_problem_id: self.report},
            )

    def test_real_validated_manifest_rejects_partial_closed_join(self) -> None:
        with self.assertRaisesRegex(ValueError, "do not exactly cover"):
            bridge_closed_evidence(
                dispatch_authority=self.authority,
                execution_ledger=self.ledger,
                closed_join=self.closed,
                protocol=self.protocol,
                public_manifest=self.manifest_bundle.public_manifest,
                operator_plan=self.manifest_bundle.operator_plan,
                cost_reports_by_problem={self.native_problem_id: self.report},
            )

    def test_replayed_execution_receipt_is_rejected(self) -> None:
        completion = self.completions[0]
        with self.assertRaisesRegex(ValueError, "replay rejected"):
            self.ledger._record_completion(
                completion,
                context_isolation_receipt=(
                    self.ledger._issue_context_isolation_receipt(completion)
                ),
                predecessor_reconciliation_receipt=(
                    self.ledger._issue_predecessor_reconciliation_receipt(
                        completion
                    )
                ),
                orchestration_milliseconds=1,
            )

    def test_missing_execution_receipt_is_rejected(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            authority, ledger, closed, report, _ = self._build_run(
                Path(tmp.name),
                run_id="run-missing",
                attempts=(0,),
                record_all=False,
            )
            with self.assertRaisesRegex(ValueError, "does not exactly cover"):
                self._bridge(
                    dispatch_authority=authority,
                    execution_ledger=ledger,
                    closed_join=closed,
                    cost_reports_by_problem={self.native_problem_id: report},
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
            with self.assertRaisesRegex(ValueError, "do not exactly cover"):
                self._bridge(
                    dispatch_authority=authority,
                    execution_ledger=ledger,
                    closed_join=closed,
                    cost_reports_by_problem={self.native_problem_id: report},
                )
        finally:
            tmp.cleanup()

    def test_zeroed_or_unbound_cost_measurements_are_rejected(self) -> None:
        traces = list(self.report.traces)
        ordinary = next(trace for trace in traces if trace.arm is Arm.ORDINARY)
        zeroed = []
        for event in ordinary.events:
            if event.kind.value == "model_call":
                zeroed.append(
                    CostEvent.model_call(
                        event.event_id,
                        input_tokens=0,
                        output_tokens=0,
                        usage_basis=event.model_usage_basis,
                    )
                )
            elif event.kind.value == "context_isolation":
                zeroed.append(CostEvent.context_isolation(event.event_id))
            elif event.kind.value == "verifier":
                zeroed.append(
                    CostEvent.verifier(event.event_id, milliseconds=0)
                )
            elif event.kind.value == "orchestration":
                zeroed.append(
                    CostEvent.orchestration(event.event_id, milliseconds=0)
                )
            else:
                zeroed.append(
                    CostEvent.predecessor_reconciliation(event.event_id)
                )
        traces[traces.index(ordinary)] = ArmCostTrace.from_events(
            Arm.ORDINARY,
            zeroed,
            expected_events=ordinary.expected_events,
            accounting_complete=True,
        )
        forged = CompleteCostReport.from_traces(traces)
        with self.assertRaisesRegex(ValueError, "artifact byte lengths"):
            self._bridge(cost_reports_by_problem={self.native_problem_id: forged})

        events = list(ordinary.events)
        expected = list(ordinary.expected_events)
        model = events[0]
        expected_model = expected[0]
        events[0] = CostEvent.model_call(
            "caller-supplied-cost",
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
            usage_basis=model.model_usage_basis,
        )
        expected[0] = ExpectedCostEvent.model_call(
            "caller-supplied-cost",
            usage_basis=expected_model.model_usage_basis,
        )
        traces = list(self.report.traces)
        traces[traces.index(ordinary)] = ArmCostTrace.from_events(
            Arm.ORDINARY,
            events,
            expected_events=expected,
            accounting_complete=True,
        )
        with self.assertRaisesRegex(ValueError, "unbound, partial, or replayed"):
            self._bridge(
                cost_reports_by_problem={
                    self.native_problem_id: CompleteCostReport.from_traces(traces)
                }
            )

    def test_context_and_predecessor_receipts_are_mandatory(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            ledger = ExecutionLedgerAuthority(
                str(Path(tmp.name, "execution.sqlite").resolve()),
                run_id="run-full",
                issuer_id="test-host",
                execution_authority_sha256=sha("non-credit-test-authority"),
                secret=b"e" * 32,
                protocol=self.protocol,
                public_manifest=self.manifest_bundle.public_manifest,
                operator_plan=self.manifest_bundle.operator_plan,
            )
            completion = self.completions[0]
            with self.assertRaisesRegex(TypeError, "ContextIsolationReceipt"):
                ledger._record_completion(
                    completion,
                    context_isolation_receipt="missing",
                    predecessor_reconciliation_receipt=(
                        ledger._issue_predecessor_reconciliation_receipt(
                            completion
                        )
                    ),
                    orchestration_milliseconds=1,
                )

            valid = ledger._issue_context_isolation_receipt(completion)
            forged = replace(valid, signature=sha("forged-context"))
            with self.assertRaisesRegex(ValueError, "not authenticated"):
                ledger._record_completion(
                    completion,
                    context_isolation_receipt=forged,
                    predecessor_reconciliation_receipt=(
                        ledger._issue_predecessor_reconciliation_receipt(
                            completion
                        )
                    ),
                    orchestration_milliseconds=1,
                )
        finally:
            tmp.cleanup()

    def test_cost_report_coverage_and_typed_inputs_are_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            self._bridge(cost_reports_by_problem={})
        with self.assertRaisesRegex(TypeError, "exact dict"):
            self._bridge(
                cost_reports_by_problem={
                    self.native_problem_id: self.report
                }.items()
            )
        with self.assertRaisesRegex(TypeError, "exact CompleteCostReport"):
            self._bridge(
                cost_reports_by_problem={self.native_problem_id: object()}
            )

    def test_cross_run_execution_ledger_is_rejected(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            other = ExecutionLedgerAuthority(
                str(Path(tmp.name, "execution.sqlite").resolve()),
                run_id="other-run",
                issuer_id="test-host",
                execution_authority_sha256=sha("non-credit-test-authority"),
                secret=b"e" * 32,
                protocol=self.protocol,
                public_manifest=self.manifest_bundle.public_manifest,
                operator_plan=self.manifest_bundle.operator_plan,
            )
            with self.assertRaisesRegex(ValueError, "different run_id"):
                self._bridge(execution_ledger=other)
        finally:
            tmp.cleanup()


    def test_predecessor_receipt_is_bound_to_frozen_graph(self) -> None:
        completion = next(
            value
            for value in self.completions
            if value.payload.request.arm is Arm.VERIFIED_CHAIN
            and value.payload.request.attempt == 1
        )
        receipt = self.ledger._issue_predecessor_reconciliation_receipt(
            completion
        )
        forged = replace(
            receipt,
            protocol_dispatch_id=(
                self.fixture_operator_plan["entries"][0]["dispatch_id"]
            ),
        )
        with self.assertRaisesRegex(ValueError, "frozen predecessor graph"):
            self.ledger._record_completion(
                completion,
                context_isolation_receipt=(
                    self.ledger._issue_context_isolation_receipt(completion)
                ),
                predecessor_reconciliation_receipt=forged,
                orchestration_milliseconds=1,
            )


if __name__ == "__main__":
    unittest.main()
