from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import supernova_goal1.evidence_bridge as evidence_bridge_module
import supernova_goal1.verifier_evidence as verifier_evidence_module
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
    CompletionStatus,
    DispatchAuthority,
)
from supernova_goal1.evidence_bridge import (
    EvaluatorEvidenceRecord,
    EvidenceBridgeBundle,
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
from supernova_goal1.verifier_evidence import (
    HostVerifierSigner,
    TerminationCause,
    VerifierBinding,
    VerifierEvidenceRecord,
    VerifierEvidenceStore,
    VerifierSandboxLauncher,
    VerifierSupervisor,
    VerifierVerdict,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(encoding="utf-8")
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
                ).encode()
                request_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
                    request_bytes,
                    kind=ScheduledChatArtifactKind.REQUEST,
                    run_id=run_id,
                    problem_id=problem.canonical_id,
                    arm=arm,
                    attempt=attempt,
                )
                slot = next(
                    entry
                    for entry in cls.manifest_bundle.operator_plan["entries"]
                    if entry["problem_id"] == problem.native_id
                    and entry["arm"] == arm.value
                    and entry["budget_attempt_index"] == attempt
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
                    protocol_dispatch_id=slot["dispatch_id"],
                    confirmatory_manifest_sha256=(
                        cls.manifest_bundle.public_manifest["manifest_sha256"]
                    ),
                )
                signer = CompletionSigner.generate()
                manifest = authority.register(
                    manifest,
                    request=request,
                    completion_verifier_sha256=signer.public_commitment,
                )
                entry = manifest.entries[-1]
                ledger._register_dispatch(entry, request)

                no_answer = attempt == 15
                response_bytes = (
                    b""
                    if no_answer
                    else f"by\n  exact proof_{arm.value}_{attempt}".encode()
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
                        AttemptStatus.NO_ANSWER if no_answer else AttemptStatus.ANSWERED
                    ),
                    error=None,
                )
                receipt = None
                verifier_ms = 0
                if not no_answer:
                    passed = (arm is Arm.VERIFIED_CHAIN and attempt == 0) or (
                        arm is Arm.PORTFOLIO and attempt == 1
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
                            ledger._issue_predecessor_reconciliation_receipt(completion)
                        ),
                        orchestration_milliseconds=1,
                    )

                prefix = request.frozen_request_sha256
                expected_by_arm[arm].extend(
                    (
                        ExpectedCostEvent.scheduled_chat_model_call(f"{prefix}:model"),
                        ExpectedCostEvent.context_isolation(
                            f"{prefix}:context_isolation"
                        ),
                        ExpectedCostEvent.verifier(f"{prefix}:verifier"),
                        ExpectedCostEvent.orchestration(f"{prefix}:orchestration"),
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
                        CostEvent.context_isolation(f"{prefix}:context_isolation"),
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
            "cost_reports_by_problem": {self.native_problem_id: self.report},
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
        self.assertEqual(16, len(record.protocol_binding_receipt_sha256s))
        self.assertEqual(16, len(record.execution_receipt_sha256s))
        self.assertEqual(16, len(record.context_isolation_receipt_sha256s))
        self.assertEqual(16, len(record.predecessor_reconciliation_sha256s))
        mapping = record.to_evaluator_mapping()
        self.assertEqual(record.evidence_sha256, mapping["evidence_sha256"])
        self.assertEqual(bundle.bridge_sha256, bundle.authority_receipt.bridge_sha256)
        self.assertEqual(
            bundle.authority_receipt_sha256,
            self.ledger.verify_evidence_bridge_bundle(bundle),
        )

    def test_bridge_summary_is_authority_authenticated(self) -> None:
        bundle = self._bridge()
        record = bundle.records[-1]
        record_values = list(record)
        record_values[record._fields.index("completion_statuses")] = (
            CompletionStatus.SUCCEEDED,
        ) * 16
        forged_record = tuple.__new__(EvaluatorEvidenceRecord, tuple(record_values))
        bundle_values = list(bundle)
        bundle_values[bundle._fields.index("records")] = (
            *bundle.records[:-1],
            forged_record,
        )
        bundle_values[bundle._fields.index("manifest_credit_status")] = (
            "CONFIRMATORY_CREDIT_ELIGIBLE"
        )
        forged_bundle = tuple.__new__(EvidenceBridgeBundle, tuple(bundle_values))
        with self.assertRaisesRegex(ValueError, "does not bind|authentication failed"):
            self.ledger.verify_evidence_bridge_bundle(forged_bundle)

        with self.assertRaisesRegex(TypeError, "cannot be replaced"):
            bundle._replace(manifest_credit_status="forged")
        with self.assertRaisesRegex(TypeError, "cannot be reconstructed"):
            EvidenceBridgeBundle._make(tuple(bundle))
        with self.assertRaisesRegex(TypeError, "cannot be replaced"):
            record._replace(completion_statuses=())
        with self.assertRaisesRegex(TypeError, "cannot be reconstructed"):
            EvaluatorEvidenceRecord._make(tuple(record))

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
        bad_manifest = json.loads(json.dumps(self.manifest_bundle.public_manifest))
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
                    self.ledger._issue_predecessor_reconciliation_receipt(completion)
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
                zeroed.append(CostEvent.verifier(event.event_id, milliseconds=0))
            elif event.kind.value == "orchestration":
                zeroed.append(CostEvent.orchestration(event.event_id, milliseconds=0))
            else:
                zeroed.append(CostEvent.predecessor_reconciliation(event.event_id))
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
            _, ledger, _, _, completions = self._build_run(
                Path(tmp.name),
                run_id="run-receipts",
                attempts=(0,),
                record_all=False,
            )
            completion = completions[0]
            with self.assertRaisesRegex(TypeError, "ContextIsolationReceipt"):
                ledger._record_completion(
                    completion,
                    context_isolation_receipt="missing",
                    predecessor_reconciliation_receipt=(
                        ledger._issue_predecessor_reconciliation_receipt(completion)
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
                        ledger._issue_predecessor_reconciliation_receipt(completion)
                    ),
                    orchestration_milliseconds=1,
                )
        finally:
            tmp.cleanup()

    def test_frozen_request_protocol_binding_cannot_be_added_post_hoc(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            base = self.completions[0].payload.request
            wrong_slot = next(
                entry["dispatch_id"]
                for entry in self.manifest_bundle.operator_plan["entries"]
                if entry["problem_id"] == base.problem.native_id
                and entry["dispatch_id"] != base.protocol_dispatch_id
            )
            variants = (
                (None, None, "protocol dispatch binding"),
                (
                    wrong_slot,
                    self.manifest_bundle.public_manifest["manifest_sha256"],
                    "protocol dispatch binding",
                ),
                (
                    base.protocol_dispatch_id,
                    sha("wrong-confirmatory-manifest"),
                    "confirmatory manifest binding",
                ),
            )
            for index, (
                protocol_dispatch_id,
                confirmatory_manifest_sha256,
                expected_error,
            ) in enumerate(variants):
                run_id = f"run-binding-{index}"
                ledger = ExecutionLedgerAuthority(
                    str(Path(tmp.name, f"execution-{index}.sqlite").resolve()),
                    run_id=run_id,
                    issuer_id="test-host",
                    execution_authority_sha256=sha("non-credit-test-authority"),
                    secret=b"e" * 32,
                    protocol=self.protocol,
                    public_manifest=self.manifest_bundle.public_manifest,
                    operator_plan=self.manifest_bundle.operator_plan,
                )
                authority = DispatchAuthority(
                    str(Path(tmp.name, f"dispatch-{index}.sqlite").resolve()),
                    run_id,
                )
                request_artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
                    b"binding-negative",
                    kind=ScheduledChatArtifactKind.REQUEST,
                    run_id=run_id,
                    problem_id=base.problem_id,
                    arm=base.arm,
                    attempt=base.attempt,
                )
                request = FrozenProblemRequest(
                    run_id=run_id,
                    experiment_id=base.experiment_id,
                    problem=base.problem,
                    benchmark_root_sha256=base.benchmark_root_sha256,
                    problem_sha256=base.problem_sha256,
                    arm=base.arm,
                    attempt=base.attempt,
                    budget_id=base.budget_id,
                    budget_sha256=base.budget_sha256,
                    model_usage_basis=base.model_usage_basis,
                    runtime_sha256=base.runtime_sha256,
                    request_artifact=request_artifact,
                    protocol_dispatch_id=protocol_dispatch_id,
                    confirmatory_manifest_sha256=(confirmatory_manifest_sha256),
                )
                signer = CompletionSigner.generate()
                manifest = authority.register(
                    authority.current_manifest(),
                    request=request,
                    completion_verifier_sha256=signer.public_commitment,
                )
                with self.assertRaisesRegex(ValueError, expected_error):
                    ledger._register_dispatch(manifest.entries[-1], request)
        finally:
            tmp.cleanup()

    def test_cost_report_coverage_and_typed_inputs_are_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            self._bridge(cost_reports_by_problem={})
        with self.assertRaisesRegex(TypeError, "exact dict"):
            self._bridge(
                cost_reports_by_problem={self.native_problem_id: self.report}.items()
            )
        with self.assertRaisesRegex(TypeError, "exact CompleteCostReport"):
            self._bridge(cost_reports_by_problem={self.native_problem_id: object()})

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
        receipt = self.ledger._issue_predecessor_reconciliation_receipt(completion)
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


class VerifierEvidenceSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        EvidenceBridgeTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        EvidenceBridgeTests.tearDownClass()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = b"import Mathlib\n\ntheorem alpha : True := by\n"
        self.candidate = b"  exact True.intro\n"
        self.launcher = VerifierSandboxLauncher(
            image_ref=f"example.invalid/supernova-verifier@sha256:{sha('image')}",
            command=("/usr/local/bin/supernova-verify",),
            image_environment=(),
            container_user="65532:65532",
            memory_bytes=256 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=32,
            timeout_seconds=10,
            max_output_bytes=4096,
            tmpfs_size_bytes=8 * 1024 * 1024,
            toolchain_lock_sha256=sha("toolchain-lock"),
            project_dependency_lock_sha256=sha("project-lock"),
            checker_configuration_sha256=sha("comparator-plus-nanoda"),
            immutable_inputs_sha256=sha("immutable-inputs"),
        )
        self.signer = HostVerifierSigner(
            issuer_id="trusted-test-host",
            signing_key_id="goal1-verifier-test-key",
            private_key=b"k" * 32,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def binding(self, **changes: object) -> VerifierBinding:
        raw: dict[str, object] = {
            "run_spec_id": sha("run-spec"),
            "run_id": "run-one",
            "experiment_id": "goal1-confirmatory-v1",
            "execution_authority_sha256": sha("execution-authority"),
            "confirmatory_manifest_sha256": sha("manifest"),
            "protocol_rules_sha256": sha("rules"),
            "protocol_dispatch_id": "dispatch-" + sha("protocol-dispatch"),
            "actual_dispatch_id": sha("actual-dispatch"),
            "dispatch_entry_sha256": sha("dispatch-entry"),
            "frozen_request_sha256": sha("frozen-request"),
            "normalized_request_sha256": sha("frozen-request"),
            "attempt_result_sha256": sha("attempt-result"),
            "problem_id": "sha256:" + sha("problem-id"),
            "problem_identity": "sha256:" + sha("problem-id"),
            "arm_id": Arm.ORDINARY.value,
            "attempt_id": 0,
            "candidate_id": "sha256:" + hashlib.sha256(self.candidate).hexdigest(),
            "candidate_source_sha256": hashlib.sha256(self.candidate).hexdigest(),
            "theorem_statement_sha256": sha("statement"),
            "source_template_sha256": hashlib.sha256(self.source).hexdigest(),
            "rendered_source_sha256": hashlib.sha256(self.source).hexdigest(),
            "theorem_target_set_sha256": hashlib.sha256(
                verifier_evidence_module.canonical_bytes(["alpha"])
            ).hexdigest(),
            "source_construction_sha256": hashlib.sha256(self.source).hexdigest(),
            "requested_runtime_sha256": sha("requested-runtime"),
            "actual_runtime_sha256": self.launcher.toolchain_lock_sha256,
            "immutable_configuration_sha256": sha("immutable-config"),
        }
        raw.update(changes)
        return VerifierBinding(**raw)  # type: ignore[arg-type]

    def store(
        self,
        name: str = "verifier.sqlite",
        *,
        verification_key: bytes | None = None,
    ) -> VerifierEvidenceStore:
        return VerifierEvidenceStore(
            (self.root / name).resolve(),
            verification_key=(
                self.signer.public_key if verification_key is None else verification_key
            ),
            expected_signing_key_id=self.signer.signing_key_id,
            expected_identity=self.launcher.identity,
        )

    def observation(
        self,
        binding: VerifierBinding | None = None,
        *,
        verdict: VerifierVerdict = VerifierVerdict.UNKNOWN,
        cause: TerminationCause = TerminationCause.INDETERMINATE,
        checker_exit_status: int | None = 0,
        timed_out: bool = False,
        oom_killed: bool = False,
        resource_limited: bool = False,
        sandbox_policy_violated: bool = False,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> verifier_evidence_module.ObservedVerifierRun:
        return verifier_evidence_module.ObservedVerifierRun(
            binding=self.binding() if binding is None else binding,
            verifier_identity=self.launcher.identity,
            source_bytes=self.source,
            candidate_bytes=self.candidate,
            exported_artifact=b"",
            checker_output=b"",
            stdout=stdout,
            stderr=stderr,
            verdict=verdict,
            termination_cause=cause,
            elaborator_exit_status=checker_exit_status,
            elaborator_signal=None,
            checker_exit_status=checker_exit_status,
            checker_signal=None,
            timed_out=timed_out,
            oom_killed=oom_killed,
            resource_limited=resource_limited,
            sandbox_policy_violated=sandbox_policy_violated,
            started_at_utc="2026-08-29T00:00:00Z",
            ended_at_utc="2026-08-29T00:00:01Z",
            elapsed_milliseconds=1000,
            resource_measurements={"cpu_milliseconds": 1, "peak_bytes": 2},
            teardown_observed=True,
        )

    def issue_unpersisted(
        self,
        binding: VerifierBinding | None = None,
        **observation: object,
    ) -> VerifierEvidenceRecord:
        observed = self.observation(binding, **observation)
        return self.signer._issue(
            observed,
            _factory=verifier_evidence_module._SUPERVISOR_FACTORY,
        )

    def append(
        self,
        store: VerifierEvidenceStore,
        record: VerifierEvidenceRecord,
        binding: VerifierBinding,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        store.append(
            record,
            expected_binding=binding,
            source=self.source,
            candidate=self.candidate,
            exported_artifact=b"",
            checker_output=b"",
            stdout=stdout,
            stderr=stderr,
        )

    def test_caller_cannot_issue_valid_or_submit_verifier_result(self) -> None:
        with self.assertRaisesRegex(PermissionError, "UNTRUSTED_VALIDITY"):
            self.observation(
                verdict=VerifierVerdict.VALID,
                cause=TerminationCause.ACCEPTED,
                checker_exit_status=0,
            )
        caller_result = VerifierResult(
            VerifierStatus.PASS,
            ("lake", "build"),
            0,
            "PASS",
            "",
            1,
        )
        with self.assertRaisesRegex(TypeError, "ObservedVerifierRun"):
            self.signer._issue(  # type: ignore[arg-type]
                caller_result,
                _factory=verifier_evidence_module._SUPERVISOR_FACTORY,
            )
        with self.assertRaisesRegex(TypeError, "only by VerifierSupervisor"):
            self.signer._issue(self.observation(), _factory=object())

    def test_atomic_store_readback_and_substitution_rejection(self) -> None:
        binding = self.binding()
        record = self.issue_unpersisted(binding)
        store = self.store()
        with self.assertRaisesRegex(ValueError, "missing"):
            store.read_complete((binding,))
        self.append(store, record, binding)
        self.assertEqual(record.record_sha256, store.read(binding).record_sha256)
        with self.assertRaisesRegex(ValueError, "extra"):
            store.read_complete(())
        with self.assertRaisesRegex(ValueError, "candidate blob"):
            store.append(
                record,
                expected_binding=binding,
                source=self.source,
                candidate=b"changed after verification",
                exported_artifact=b"",
                checker_output=b"",
                stdout=b"",
                stderr=b"",
            )

        variants = (
            replace(binding, run_spec_id=sha("other-run-spec")),
            replace(binding, run_id="other-run"),
            replace(binding, arm_id=Arm.PORTFOLIO.value),
            replace(binding, attempt_id=1),
            replace(binding, candidate_id="sha256:" + sha("other-candidate")),
            replace(binding, candidate_source_sha256=sha("other-candidate-source")),
            replace(binding, theorem_statement_sha256=sha("other-statement")),
            replace(binding, frozen_request_sha256=sha("other-request")),
            replace(binding, actual_runtime_sha256=sha("other-runtime")),
            replace(binding, actual_dispatch_id=sha("other-dispatch")),
            replace(
                binding,
                protocol_dispatch_id="dispatch-" + sha("other-protocol-dispatch"),
            ),
        )
        for changed in variants:
            with (
                self.subTest(field=changed),
                self.assertRaises((KeyError, ValueError, sqlite3.IntegrityError)),
            ):
                self.append(store, record, changed)

        with self.assertRaises(sqlite3.IntegrityError):
            self.append(store, record, binding)

    def test_invalid_signature_wrong_key_and_corrupt_readback_fail_closed(self) -> None:
        binding = self.binding()
        record = self.issue_unpersisted(binding)
        forged = VerifierEvidenceRecord(
            record.body_json,
            base64.b64encode(b"\0" * 64).decode("ascii"),
        )
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            self.append(self.store("forged.sqlite"), forged, binding)
        wrong_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            self.append(
                self.store("wrong-key.sqlite", verification_key=wrong_key),
                record,
                binding,
            )
        wrong_identity = replace(
            self.launcher.identity,
            external_checker_image_digest="sha256:" + sha("other-checker-image"),
        )
        wrong_identity_store = VerifierEvidenceStore(
            (self.root / "wrong-identity.sqlite").resolve(),
            verification_key=self.signer.public_key,
            expected_signing_key_id=self.signer.signing_key_id,
            expected_identity=wrong_identity,
        )
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.append(wrong_identity_store, record, binding)

        store = self.store("corrupt.sqlite")
        self.append(store, record, binding)
        connection = sqlite3.connect(store.path)
        try:
            connection.execute("DROP TRIGGER verifier_evidence_no_update")
            connection.execute(
                "UPDATE verifier_evidence SET body_json=?",
                (b"{}",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ValueError):
            store.read(binding)

    def test_partial_transaction_and_append_only_triggers(self) -> None:
        binding = self.binding()
        record = self.issue_unpersisted(binding)
        store = self.store("rollback.sqlite")
        with (
            patch.object(store, "_read_row", side_effect=RuntimeError("forced")),
            self.assertRaisesRegex(RuntimeError, "forced"),
        ):
            self.append(store, record, binding)
        connection = sqlite3.connect(store.path)
        try:
            self.assertEqual(
                0,
                connection.execute("SELECT COUNT(*) FROM verifier_evidence").fetchone()[
                    0
                ],
            )
        finally:
            connection.close()

        store = self.store("append-only.sqlite")
        self.append(store, record, binding)
        connection = sqlite3.connect(store.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM verifier_evidence")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE verifier_evidence SET nonce=?", (sha("changed"),)
                )
        finally:
            connection.close()

    def test_sandbox_policy_has_no_mount_network_key_database_or_mutable_tag(
        self,
    ) -> None:
        policy = self.launcher.sandbox_policy
        self.assertEqual("none", policy["network"])
        self.assertEqual([], policy["host_mounts"])
        self.assertEqual([], policy["devices"])
        self.assertEqual(["ALL"], policy["cap_drop"])
        self.assertTrue(policy["no_new_privileges"])
        argv = verifier_evidence_module._create_argv(self.launcher)
        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--privileged", argv)
        self.assertNotIn(str(self.root / "verifier.sqlite"), argv)
        self.assertNotIn(base64.b64encode(b"k" * 32).decode("ascii"), " ".join(argv))
        with self.assertRaisesRegex(ValueError, "repository@sha256"):
            replace(self.launcher, image_ref="example.invalid/verifier:latest")

    @unittest.skipUnless(
        os.environ.get("SUPERNOVA_RUN_HOSTILE_LEAN_TEST") == "1",
        "set SUPERNOVA_RUN_HOSTILE_LEAN_TEST=1 after pulling the pinned image",
    )
    def test_hostile_lean_metaprogram_cannot_read_host_only_file(self) -> None:
        image_ref = (
            "ghcr.io/kitahl/supernova-goal1-verifier@sha256:"
            "3fa91bdfb031e3ec271778dae2e419caccf17f6ac12fc8f00dfe7fcb35403ea1"
        )
        canary = "SUPERNOVA_HOST_ONLY_CANARY_7f61a28c"
        host_only = (self.root / "host-only-sentinel.txt").resolve()
        host_only.write_text(canary, encoding="utf-8")
        if os.name == "nt":
            drive = host_only.drive.removesuffix(":").lower()
            rest = host_only.as_posix().split(":", 1)[1]
            candidate_path = f"/run/desktop/mnt/host/{drive}{rest}"
        else:
            candidate_path = host_only.as_posix()

        source = b"import Mathlib\n\ntheorem alpha : True := by\n"
        candidate = (
            "  run_tac\n"
            f'    let contents <- IO.FS.readFile "{candidate_path}"\n'
            f'    unless contents == "{canary}" do\n'
            '      throwError "host canary mismatch"\n'
            "  exact True.intro\n"
        ).encode()
        binding = self.binding(
            actual_dispatch_id=sha("hostile-host-file-dispatch"),
            candidate_id="sha256:" + hashlib.sha256(candidate).hexdigest(),
            candidate_source_sha256=hashlib.sha256(candidate).hexdigest(),
            source_construction_sha256=hashlib.sha256(source).hexdigest(),
        )
        checker_configuration = verifier_evidence_module.canonical_bytes(
            {
                "check_exports_sha256": hashlib.sha256(
                    (
                        ROOT / "runtime" / "goal1_verifier" / "CheckExports.lean"
                    ).read_bytes()
                ).hexdigest(),
                "permitted_axioms": list(verifier_evidence_module.PERMITTED_AXIOMS),
            }
        )
        immutable_inputs = verifier_evidence_module.canonical_bytes(
            {
                name: hashlib.sha256(
                    (ROOT / "runtime" / "goal1_verifier" / name).read_bytes()
                ).hexdigest()
                for name in ("Dockerfile", "entrypoint.py", "pins.json")
            }
        )
        launcher = VerifierSandboxLauncher(
            image_ref=image_ref,
            command=("--stdio",),
            image_environment=("PATH=/opt/lean/bin:/usr/local/bin:/usr/bin:/bin",),
            container_user="10001:10001",
            memory_bytes=4 * 1024 * 1024 * 1024,
            nano_cpus=2_000_000_000,
            pids_limit=256,
            timeout_seconds=600,
            max_output_bytes=64 * 1024 * 1024,
            tmpfs_size_bytes=512 * 1024 * 1024,
            toolchain_lock_sha256=hashlib.sha256(
                (ROOT / "runtime" / "lean" / "lean-toolchain").read_bytes()
            ).hexdigest(),
            project_dependency_lock_sha256=hashlib.sha256(
                (ROOT / "runtime" / "lean" / "lake-manifest.json").read_bytes()
            ).hexdigest(),
            checker_configuration_sha256=hashlib.sha256(
                checker_configuration
            ).hexdigest(),
            immutable_inputs_sha256=hashlib.sha256(immutable_inputs).hexdigest(),
        )
        store = VerifierEvidenceStore(
            (self.root / "hostile.sqlite").resolve(),
            verification_key=self.signer.public_key,
            expected_signing_key_id=self.signer.signing_key_id,
            expected_identity=launcher.identity,
        )
        record = VerifierSupervisor(launcher, self.signer, store).run_and_record(
            binding, source=source, candidate=candidate, theorem_names=("alpha",)
        )
        observation = record.body["observations"]
        self.assertEqual(VerifierVerdict.UNKNOWN.value, observation["verdict"])
        self.assertEqual(
            TerminationCause.INDETERMINATE.value,
            observation["termination_cause"],
        )
        self.assertNotIn(canary.encode("utf-8"), record.body_json)
        self.assertNotIn(canary.encode("utf-8"), store.read(binding).body_json)

    def _mocked_supervisor_run(
        self,
        *,
        elaborator_status: str = "EXPORTED",
        checker_status: str = "VALID",
        elaborator_exit_override: int | None = None,
        checker_exit_override: int | None = None,
        elaborator_stdout: bytes | None = None,
        checker_stdout: bytes | None = None,
        timeout_phase: str | None = None,
        oom_phase: str | None = None,
        create_failure_phase: str | None = None,
        policy_failure_phase: str | None = None,
        teardown_failure_phase: str | None = None,
        parser_status: str | None = None,
        unknown_cause: str = TerminationCause.INTERNAL.value,
        name: str,
    ) -> VerifierEvidenceRecord:
        binding = self.binding(actual_dispatch_id=sha("dispatch:" + name))
        store = self.store(name + ".sqlite")
        supervisor = VerifierSupervisor(self.launcher, self.signer, store)
        exported = b'{"declarations":["alpha"]}'

        def phase_response(status: str, *, checker: bool) -> bytes:
            if status == "EXPORTED":
                return verifier_evidence_module.canonical_bytes(
                    {
                        "schema": verifier_evidence_module.CONTAINER_RESPONSE_SCHEMA,
                        "solution_export_b64": base64.b64encode(exported).decode(
                            "ascii"
                        ),
                        "solution_export_sha256": hashlib.sha256(exported).hexdigest(),
                        "status": status,
                    }
                )
            if status == "PARSED":
                return verifier_evidence_module.canonical_bytes(
                    {
                        "declaration_name": "alpha",
                        "product_source_sha256": hashlib.sha256(
                            self.candidate
                        ).hexdigest(),
                        "schema": verifier_evidence_module.CONTAINER_RESPONSE_SCHEMA,
                        "status": status,
                    }
                )
            if status == "VALID" and checker:
                return verifier_evidence_module.canonical_bytes(
                    {
                        "challenge_export_sha256": sha("challenge-export"),
                        "checker": "COMPARATOR_DATA_ONLY_PLUS_NANODA",
                        "schema": verifier_evidence_module.CONTAINER_RESPONSE_SCHEMA,
                        "solution_export_sha256": hashlib.sha256(exported).hexdigest(),
                        "status": status,
                    }
                )
            return verifier_evidence_module.canonical_bytes(
                {
                    "diagnostic": f"deterministic {status.lower()}",
                    "schema": verifier_evidence_module.CONTAINER_RESPONSE_SCHEMA,
                    "status": status,
                    **(
                        {"termination_cause": unknown_cause}
                        if status == VerifierVerdict.UNKNOWN.value
                        else {}
                    ),
                }
            )

        phase_names = (
            ("elaborator", "checker")
            if parser_status is None
            else ("product_parser", "elaborator", "checker")
        )
        base_stdout = (
            phase_response(elaborator_status, checker=False)
            if elaborator_stdout is None
            else elaborator_stdout,
            phase_response(checker_status, checker=True)
            if checker_stdout is None
            else checker_stdout,
        )
        expected_elaborator_exit = (
            0
            if elaborator_status == "EXPORTED"
            else 10
            if elaborator_status == "INVALID"
            else 20
        )
        expected_checker_exit = (
            0
            if checker_status == "VALID"
            else 10
            if checker_status == "INVALID"
            else 20
        )
        base_exit = (
            expected_elaborator_exit
            if elaborator_exit_override is None
            else elaborator_exit_override,
            expected_checker_exit
            if checker_exit_override is None
            else checker_exit_override,
        )
        phase_stdout = (
            base_stdout
            if parser_status is None
            else (phase_response(parser_status, checker=False), *base_stdout)
        )
        parser_exit = (
            0 if parser_status == "PARSED" else 10 if parser_status == "INVALID" else 20
        )
        phase_exit = base_exit if parser_status is None else (parser_exit, *base_exit)
        captured_requests: list[bytes] = []
        created_containers: list[str] = []
        removed_containers: list[str] = []
        issue_removed_counts: list[int] = []
        append_removed_counts: list[int] = []
        inspection_count: dict[str, int] = {}
        phase_by_container: dict[str, int] = {}

        def invoke(
            argv: object, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            args = list(argv)  # type: ignore[arg-type]
            if args[1] == "version":
                return subprocess.CompletedProcess(
                    args, 0, b'{"Server":{"Version":"test"}}', b""
                )
            if args[1] == "create":
                phase = len(created_containers)
                phase_name = phase_names[phase]
                if create_failure_phase == phase_name:
                    return subprocess.CompletedProcess(args, 1, b"", b"create denied")
                container_id = chr(ord("a") + phase) * 64
                created_containers.append(container_id)
                phase_by_container[container_id] = phase
                return subprocess.CompletedProcess(
                    args, 0, container_id.encode("ascii") + b"\n", b""
                )
            if args[1] == "start":
                container_id = args[-1]
                phase = phase_by_container[container_id]
                captured_requests.append(kwargs["input_bytes"])  # type: ignore[arg-type]
                if timeout_phase == phase_names[phase]:
                    raise subprocess.TimeoutExpired(
                        args, 10, output=b"", stderr=b"timeout"
                    )
                return subprocess.CompletedProcess(
                    args, phase_exit[phase], phase_stdout[phase], b""
                )
            raise AssertionError(args)

        def docker_object(container_id: str) -> dict[str, object]:
            phase = phase_by_container[container_id]
            inspection_count[container_id] = inspection_count.get(container_id, 0) + 1
            if inspection_count[container_id] == 1:
                return {"test_phase": phase_names[phase]}
            oom_killed = oom_phase == phase_names[phase]
            return {
                "State": {
                    "Dead": False,
                    "Error": "",
                    "ExitCode": 137 if oom_killed else phase_exit[phase],
                    "OOMKilled": oom_killed,
                    "Status": "running" if timeout_phase == phase_names[phase] else "exited",
                    "Running": timeout_phase == phase_names[phase],
                }
            }

        def security_snapshot(
            inspection: dict[str, object],
            _launcher: VerifierSandboxLauncher,
            _image_id: str,
        ) -> dict[str, object]:
            phase_name = inspection["test_phase"]
            if policy_failure_phase == phase_name:
                raise verifier_evidence_module._SandboxPolicyError("policy drift")
            return {"phase": phase_name, "policy": self.launcher.sandbox_policy}

        def remove_observed(container_id: str) -> None:
            phase_name = phase_names[phase_by_container[container_id]]
            if teardown_failure_phase == phase_name:
                raise RuntimeError("forced teardown observation failure")
            removed_containers.append(container_id)

        original_issue = self.signer._issue
        original_append = store.append

        def issue(*args: object, **kwargs: object) -> VerifierEvidenceRecord:
            issue_removed_counts.append(len(removed_containers))
            return original_issue(*args, **kwargs)  # type: ignore[arg-type]

        def append(*args: object, **kwargs: object) -> None:
            append_removed_counts.append(len(removed_containers))
            original_append(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.object(verifier_evidence_module, "_invoke", side_effect=invoke),
            patch.object(
                verifier_evidence_module,
                "_image_identity",
                return_value=self.launcher.image_digest,
            ),
            patch.object(
                verifier_evidence_module, "_docker_object", side_effect=docker_object
            ),
            patch.object(
                verifier_evidence_module,
                "_security_snapshot",
                side_effect=security_snapshot,
            ),
            patch.object(
                verifier_evidence_module,
                "_remove_observed",
                side_effect=remove_observed,
            ),
            patch.object(self.signer, "_issue", side_effect=issue),
            patch.object(store, "append", side_effect=append),
        ):
            parser_kwargs = (
                {}
                if parser_status is None
                else {
                    "product_parser_source": self.candidate,
                    "product_parser_expected_name": "alpha",
                }
            )
            try:
                record = supervisor.run_and_record(
                    binding,
                    source=self.source,
                    candidate=self.candidate,
                    theorem_names=("alpha",),
                    **parser_kwargs,
                )
            finally:
                self.last_supervisor_trace = {
                    "append_removed_counts": tuple(append_removed_counts),
                    "captured_requests": tuple(captured_requests),
                    "created_containers": tuple(created_containers),
                    "issue_removed_counts": tuple(issue_removed_counts),
                    "removed_containers": tuple(removed_containers),
                }
        for request in captured_requests:
            self.assertNotIn(b"k" * 32, request)
            self.assertNotIn(str(store.path).encode("utf-8"), request)
        return record

    def test_valid_requires_two_fresh_keyless_containers_removed_before_signing(
        self,
    ) -> None:
        record = self._mocked_supervisor_run(name="valid")
        observed = record.body["observations"]
        self.assertEqual(VerifierVerdict.VALID.value, observed["verdict"])
        self.assertEqual(TerminationCause.ACCEPTED.value, observed["termination_cause"])
        trace = self.last_supervisor_trace
        self.assertEqual(2, len(trace["created_containers"]))
        self.assertEqual(2, len(set(trace["created_containers"])))
        self.assertEqual(trace["created_containers"], trace["removed_containers"])
        self.assertEqual((2,), trace["issue_removed_counts"])
        requests = tuple(json.loads(raw) for raw in trace["captured_requests"])
        self.assertEqual(
            ("elaborate", "check"), tuple(value["mode"] for value in requests)
        )

    def test_teardown_observation_failure_never_signs_or_persists(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "elaborator teardown was not observed; evidence was not signed",
        ):
            self._mocked_supervisor_run(
                teardown_failure_phase="elaborator",
                name="teardown-unobserved",
            )

        trace = self.last_supervisor_trace
        self.assertEqual(1, len(trace["created_containers"]))
        self.assertEqual((), trace["removed_containers"])
        self.assertEqual((), trace["issue_removed_counts"])
        self.assertEqual((), trace["append_removed_counts"])

    def test_product_parser_admission_is_bound_inside_signed_evidence(self) -> None:
        admitted = self._mocked_supervisor_run(
            parser_status="PARSED",
            name="parser-admitted",
        )
        measurement = admitted.body["observations"]["resource_measurements"][
            "product_parser"
        ]
        self.assertEqual(
            {
                "admissible": True,
                "expected_name": "alpha",
                "source_bytes": len(self.candidate),
                "source_sha256": hashlib.sha256(self.candidate).hexdigest(),
            },
            measurement,
        )
        requests = tuple(
            json.loads(raw) for raw in self.last_supervisor_trace["captured_requests"]
        )
        self.assertEqual(
            ("parse_product", "elaborate", "check"),
            tuple(value["mode"] for value in requests),
        )
        self.assertEqual(3, len(self.last_supervisor_trace["created_containers"]))

        rejected = self._mocked_supervisor_run(
            parser_status="INVALID",
            name="parser-rejected",
        )
        self.assertFalse(
            rejected.body["observations"]["resource_measurements"]["product_parser"][
                "admissible"
            ]
        )
        self.assertEqual(
            VerifierVerdict.VALID.value,
            rejected.body["observations"]["verdict"],
        )

    def test_supervisor_rejects_target_substitution_and_runtime_drift(self) -> None:
        store = self.store("bound-inputs.sqlite")
        supervisor = VerifierSupervisor(self.launcher, self.signer, store)
        binding = self.binding(actual_dispatch_id=sha("bound-inputs"))
        substituted_target = replace(
            binding,
            theorem_target_set_sha256=hashlib.sha256(
                verifier_evidence_module.canonical_bytes(["beta"])
            ).hexdigest(),
        )
        with self.assertRaisesRegex(ValueError, "frozen target set"):
            supervisor.run_and_record(
                substituted_target,
                source=self.source,
                candidate=self.candidate,
                theorem_names=("alpha",),
            )
        drifted_runtime = replace(
            binding,
            actual_runtime_sha256=sha("unobserved-runtime"),
        )
        with self.assertRaisesRegex(ValueError, "host-observed runtime"):
            supervisor.run_and_record(
                drifted_runtime,
                source=self.source,
                candidate=self.candidate,
                theorem_names=("alpha",),
            )

    def test_elaborator_and_checker_rejections_are_invalid(
        self,
    ) -> None:
        elaborator = self._mocked_supervisor_run(
            elaborator_status="INVALID", name="elaborator-invalid"
        ).body["observations"]
        self.assertEqual(VerifierVerdict.INVALID.value, elaborator["verdict"])
        self.assertEqual(
            TerminationCause.REJECTED.value,
            elaborator["termination_cause"],
        )

        checker = self._mocked_supervisor_run(
            checker_status="INVALID", name="checker-invalid"
        ).body["observations"]
        self.assertEqual(VerifierVerdict.INVALID.value, checker["verdict"])
        self.assertEqual(
            TerminationCause.REJECTED.value,
            checker["termination_cause"],
        )

    def test_two_phase_uncertainty_never_validates(self) -> None:
        cases = (
            (
                {"elaborator_stdout": b"PASS\nVALID\n", "name": "fake"},
                TerminationCause.MALFORMED_CHECKER_OUTPUT,
            ),
            (
                {"elaborator_stdout": b"", "name": "early"},
                TerminationCause.MALFORMED_CHECKER_OUTPUT,
            ),
            (
                {"timeout_phase": "elaborator", "name": "timeout"},
                TerminationCause.TIMEOUT,
            ),
            ({"oom_phase": "elaborator", "name": "oom"}, TerminationCause.OOM),
            (
                {"create_failure_phase": "elaborator", "name": "start"},
                TerminationCause.SANDBOX_START_FAILURE,
            ),
            (
                {"policy_failure_phase": "elaborator", "name": "policy"},
                TerminationCause.SANDBOX_POLICY_VIOLATION,
            ),
            (
                {"checker_status": "UNKNOWN", "name": "checker-unknown"},
                TerminationCause.INTERNAL,
            ),
            (
                {"timeout_phase": "checker", "name": "checker-timeout"},
                TerminationCause.TIMEOUT,
            ),
        )
        for arguments, expected_cause in cases:
            with self.subTest(cause=expected_cause):
                record = self._mocked_supervisor_run(**arguments)
                observed = record.body["observations"]
                self.assertEqual(VerifierVerdict.UNKNOWN.value, observed["verdict"])
                self.assertEqual(expected_cause.value, observed["termination_cause"])

    def test_running_container_timeout_has_no_observed_exit(self) -> None:
        for phase in ("elaborator", "checker"):
            with self.subTest(phase=phase):
                observed = self._mocked_supervisor_run(
                    timeout_phase=phase, name="running-" + phase
                ).body["observations"]
                self.assertEqual(observed["verdict"], "UNKNOWN")
                self.assertEqual(observed["termination_cause"], "TIMEOUT")
                self.assertIsNone(observed[phase + "_exit_status"])
                self.assertIsNone(observed[phase + "_signal"])
                last = observed["resource_measurements"]["phases"][-1]
                self.assertIs(last["container_running"], True)
                self.assertEqual(last["container_status"], "running")
                self.assertTrue(observed["teardown_observed"])

    def test_heartbeat_exhaustion_is_signed_as_resource_unknown(self) -> None:
        observed = self._mocked_supervisor_run(
            elaborator_status=VerifierVerdict.UNKNOWN.value,
            unknown_cause=TerminationCause.RESOURCE_LIMIT_HEARTBEAT.value,
            name="heartbeat-unknown",
        ).body["observations"]

        self.assertEqual(VerifierVerdict.UNKNOWN.value, observed["verdict"])
        self.assertEqual(
            TerminationCause.RESOURCE_LIMIT_HEARTBEAT.value,
            observed["termination_cause"],
        )
        self.assertTrue(observed["resource_limited"])

    def test_unknown_response_with_wrong_exit_is_malformed_not_heartbeat(self) -> None:
        observed = self._mocked_supervisor_run(
            elaborator_status=VerifierVerdict.UNKNOWN.value,
            elaborator_exit_override=0,
            unknown_cause=TerminationCause.RESOURCE_LIMIT_HEARTBEAT.value,
            name="heartbeat-wrong-exit",
        ).body["observations"]

        self.assertEqual(VerifierVerdict.UNKNOWN.value, observed["verdict"])
        self.assertEqual(
            TerminationCause.MALFORMED_CHECKER_OUTPUT.value,
            observed["termination_cause"],
        )
        self.assertFalse(observed["resource_limited"])

    def test_real_bridge_blocks_authenticated_unknown_before_evaluator_projection(
        self,
    ) -> None:
        completion = EvidenceBridgeTests.completions[0]
        request = completion.payload.request
        result = completion.payload.attempt_result
        source = f"problem:{request.problem.native_id}".encode()
        candidate = (
            f"by\n  exact proof_{request.arm.value}_{request.attempt}"
        ).encode()
        binding = self.binding(
            run_spec_id=EvidenceBridgeTests.manifest_bundle.public_manifest[
                "manifest_sha256"
            ],
            run_id=request.run_id,
            experiment_id=request.experiment_id,
            execution_authority_sha256=EvidenceBridgeTests.ledger.execution_authority_sha256,
            protocol_rules_sha256=EvidenceBridgeTests.protocol["sealed_rules_sha256"],
            confirmatory_manifest_sha256=(
                EvidenceBridgeTests.manifest_bundle.public_manifest["manifest_sha256"]
            ),
            protocol_dispatch_id=request.protocol_dispatch_id,
            actual_dispatch_id=completion.dispatch_id,
            dispatch_entry_sha256=completion.entry_sha256,
            frozen_request_sha256=request.frozen_request_sha256,
            normalized_request_sha256=request.frozen_request_sha256,
            attempt_result_sha256=result.attempt_result_sha256,
            problem_id=request.problem_id,
            problem_identity=request.problem.canonical_id,
            arm_id=request.arm.value,
            attempt_id=request.attempt,
            candidate_id=result.response_artifact.artifact_id,
            candidate_source_sha256=result.response_artifact.sha256_hex,
            theorem_target_set_sha256=hashlib.sha256(
                verifier_evidence_module.canonical_bytes([request.problem.native_id])
            ).hexdigest(),
            rendered_source_sha256=hashlib.sha256(source).hexdigest(),
            source_construction_sha256=hashlib.sha256(source).hexdigest(),
            requested_runtime_sha256=request.runtime_sha256,
            actual_runtime_sha256=self.launcher.toolchain_lock_sha256,
        )
        self.assertEqual(
            binding.source_construction_sha256, hashlib.sha256(source).hexdigest()
        )
        self.assertEqual(
            binding.candidate_source_sha256, hashlib.sha256(candidate).hexdigest()
        )
        store = self.store("bridge.sqlite")
        observed = verifier_evidence_module.ObservedVerifierRun(
            binding=binding,
            verifier_identity=self.launcher.identity,
            source_bytes=source,
            candidate_bytes=candidate,
            exported_artifact=b"",
            checker_output=b"",
            stdout=b"",
            stderr=b"timeout",
            verdict=VerifierVerdict.UNKNOWN,
            termination_cause=TerminationCause.TIMEOUT,
            elaborator_exit_status=None,
            elaborator_signal=None,
            checker_exit_status=None,
            checker_signal=None,
            timed_out=True,
            oom_killed=False,
            resource_limited=False,
            sandbox_policy_violated=False,
            started_at_utc="2026-08-29T00:00:00Z",
            ended_at_utc="2026-08-29T00:00:01Z",
            elapsed_milliseconds=1,
            resource_measurements={"timeout": True},
            teardown_observed=True,
        )
        record = self.signer._issue(
            observed,
            _factory=verifier_evidence_module._SUPERVISOR_FACTORY,
        )
        store.append(
            record,
            expected_binding=binding,
            source=source,
            candidate=candidate,
            exported_artifact=b"",
            checker_output=b"",
            stdout=b"",
            stderr=b"timeout",
        )
        with self.assertRaisesRegex(ValueError, "pre-completion verifier binding"):
            evidence_bridge_module._production_verifier_records(
                (completion,),
                store=store,
                bindings_by_dispatch={},
                run_spec_id=binding.run_spec_id,
                execution_authority_sha256=binding.execution_authority_sha256,
                protocol_rules_sha256=binding.protocol_rules_sha256,
                confirmatory_manifest_sha256=binding.confirmatory_manifest_sha256,
            )
        with self.assertRaisesRegex(PermissionError, "BLOCKED_UNKNOWN"):
            evidence_bridge_module._production_verifier_records(
                (completion,),
                store=store,
                bindings_by_dispatch={completion.dispatch_id: binding},
                run_spec_id=binding.run_spec_id,
                execution_authority_sha256=binding.execution_authority_sha256,
                protocol_rules_sha256=binding.protocol_rules_sha256,
                confirmatory_manifest_sha256=binding.confirmatory_manifest_sha256,
            )

    def test_draft_bridge_rejects_production_store_boundary_bypass(self) -> None:
        store = self.store("draft-bypass.sqlite")
        with (
            patch(
                "supernova_goal1.evidence_bridge.validate_draft_bundle",
                return_value=None,
            ),
            self.assertRaisesRegex(ValueError, "draft bridge cannot consume"),
        ):
            bridge_closed_evidence(
                dispatch_authority=EvidenceBridgeTests.authority,
                execution_ledger=EvidenceBridgeTests.ledger,
                closed_join=EvidenceBridgeTests.closed,
                protocol=EvidenceBridgeTests.protocol,
                public_manifest=(EvidenceBridgeTests.manifest_bundle.public_manifest),
                operator_plan=EvidenceBridgeTests.fixture_operator_plan,
                cost_reports_by_problem={
                    EvidenceBridgeTests.native_problem_id: EvidenceBridgeTests.report
                },
                verifier_evidence_store=store,
            )


if __name__ == "__main__":
    unittest.main()
