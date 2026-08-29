from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from supernova_goal1.activation import activate_confirmatory_execution
from supernova_goal1.confirmatory_manifest import assert_dispatch_authorized
from supernova_goal1.dispatch import CompletionStatus
from supernova_goal1.evidence_bridge import (
    EvidenceBridgeBundle,
    EvaluatorEvidenceRecord,
)
from supernova_goal1.evaluate_confirmatory import evaluate_confirmatory
from supernova_goal1.execution_authority import (
    PRODUCTION_CREDIT_STATUS,
    _issue_validated_authority,
    _validate_authority_artifact,
)

from tests.test_confirmatory_execution_authority import (
    GOAL1,
    PROTOCOL,
    _public_bytes,
    _signed_fixture,
)
import tests.test_evidence_bridge as bridge_test_module
import tests.test_evaluate_confirmatory as evaluator_test_module
from tests.test_evaluate_confirmatory import sha


class ConfirmatoryAdversarialTests(unittest.TestCase):
    """Attacks must stop before any scientific PASS can be produced."""

    @classmethod
    def setUpClass(cls) -> None:
        bridge_test_module.EvidenceBridgeTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        bridge_test_module.EvidenceBridgeTests.tearDownClass()

    def bridge_fixture(self) -> bridge_test_module.EvidenceBridgeTests:
        return bridge_test_module.EvidenceBridgeTests(
            methodName="test_bridge_derives_outcomes_and_binds_all_five_evidence_classes"
        )

    def evaluator_fixture(
        self,
    ) -> evaluator_test_module.ConfirmatoryEvaluatorTests:
        return evaluator_test_module.ConfirmatoryEvaluatorTests(
            methodName="test_public_api_rejects_raw_records"
        )

    def capability(self):
        artifact, root_private, _ = _signed_fixture()
        validation = _validate_authority_artifact(
            artifact,
            protocol=PROTOCOL,
            goal1=GOAL1,
            root_key_id="fixture-root-v1",
            root_public_key=_public_bytes(root_private),
        )
        return _issue_validated_authority(validation)

    def test_stale_protocol_and_benchmark_leakage_cannot_activate(self) -> None:
        stale = copy.deepcopy(PROTOCOL)
        stale["protocol_id"] = "stale-protocol"

        leaked = copy.deepcopy(PROTOCOL)
        selection = leaked["sealed_rules"]["benchmark_selection"]
        selection["report_split"] = selection["development_split"]

        for label, candidate in (("stale", stale), ("leaked", leaked)):
            with self.subTest(attack=label), self.assertRaisesRegex(
                PermissionError, "exact checked-in frozen protocol"
            ):
                activate_confirmatory_execution(
                    candidate,
                    GOAL1,
                    operator_seed=bytes.fromhex("91" * 32),
                )

    def test_unregistered_retry_cannot_authorize(self) -> None:
        capability = self.capability()
        with patch(
            "supernova_goal1.confirmatory_manifest.load_execution_authority",
            return_value=capability,
        ):
            activated = activate_confirmatory_execution(
                PROTOCOL,
                GOAL1,
                operator_seed=bytes.fromhex("92" * 32),
            )

        changed_plan = copy.deepcopy(activated.manifest.operator_plan)
        changed_plan["entries"][0]["retry_allowance"] = 1
        with self.assertRaisesRegex(ValueError, "authorized reconstruction"):
            assert_dispatch_authorized(
                activated.manifest.public_manifest,
                changed_plan,
                activated.protocol,
                execution_authority=capability,
            )

    def test_fabricated_pass_outcomes_fail_bridge_authentication(self) -> None:
        fixture = self.bridge_fixture()
        bundle = fixture._bridge()
        record = bundle.records[0]
        record_values = list(record)
        record_values[record._fields.index("completion_statuses")] = (
            CompletionStatus.SUCCEEDED,
        ) * 16
        forged_record = tuple.__new__(
            EvaluatorEvidenceRecord, tuple(record_values)
        )
        bundle_values = list(bundle)
        bundle_values[bundle._fields.index("records")] = (
            forged_record,
            *bundle.records[1:],
        )
        forged_bundle = tuple.__new__(
            EvidenceBridgeBundle, tuple(bundle_values)
        )

        with self.assertRaisesRegex(
            ValueError, "does not bind|authentication failed"
        ):
            fixture.ledger.verify_evidence_bridge_bundle(forged_bundle)

    def test_absent_authority_blocks_caller_hmac_before_statistics(self) -> None:
        fixture = self.evaluator_fixture()
        caller_authority = fixture.authority()
        bundle = fixture.bundle(
            fixture.full_records(
                verified_success_attempt=0,
                control_success_attempt=None,
                credit_status=PRODUCTION_CREDIT_STATUS,
            ),
            credit_status=PRODUCTION_CREDIT_STATUS,
            authority=caller_authority,
        )

        result = evaluate_confirmatory(bundle)
        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual(
            "PRODUCTION_EXECUTION_AUTHORITY_UNAVAILABLE", result["reason"]
        )
        self.assertFalse(result["decision_eligible"])
        self.assertEqual([], result["contrasts"])

    def test_missing_cost_and_verifier_substitution_never_pass(self) -> None:
        fixture = self.bridge_fixture()

        with self.assertRaisesRegex(
            ValueError, "cost reports do not exactly cover"
        ):
            fixture._bridge(cost_reports_by_problem={})

        bundle = fixture._bridge()
        record = bundle.records[0]
        record_values = list(record)
        verifier_digests = list(record.verifier_evidence_sha256s)
        verifier_digests[0] = sha("substituted-verifier")
        record_values[
            record._fields.index("verifier_evidence_sha256s")
        ] = tuple(verifier_digests)
        forged_record = tuple.__new__(
            EvaluatorEvidenceRecord, tuple(record_values)
        )
        bundle_values = list(bundle)
        bundle_values[bundle._fields.index("records")] = (
            forged_record,
            *bundle.records[1:],
        )
        forged_bundle = tuple.__new__(
            EvidenceBridgeBundle, tuple(bundle_values)
        )

        with self.assertRaisesRegex(
            ValueError, "does not bind|authentication failed"
        ):
            fixture.ledger.verify_evidence_bridge_bundle(forged_bundle)

    def test_partial_cohort_is_rejected_by_real_bridge(self) -> None:
        fixture = self.bridge_fixture()
        with tempfile.TemporaryDirectory() as directory:
            authority, ledger, closed, report, _ = fixture._build_run(
                Path(directory),
                run_id="adversarial-partial",
                attempts=(0,),
                record_all=True,
            )
            with self.assertRaisesRegex(ValueError, "do not exactly cover"):
                fixture._bridge(
                    dispatch_authority=authority,
                    execution_ledger=ledger,
                    closed_join=closed,
                    cost_reports_by_problem={
                        fixture.native_problem_id: report
                    },
                )

    def test_simulation_and_recurring_chat_issuers_are_noncredit(self) -> None:
        artifact, root_private, _ = _signed_fixture()
        for issuer in ("NON_CREDIT_SIMULATION", "scheduled-chat-worker"):
            changed = copy.deepcopy(artifact)
            changed["receipt_issuer_id"] = issuer
            with self.subTest(issuer=issuer), self.assertRaisesRegex(
                ValueError, "non-credit"
            ):
                _validate_authority_artifact(
                    changed,
                    protocol=PROTOCOL,
                    goal1=GOAL1,
                    root_key_id="fixture-root-v1",
                    root_public_key=_public_bytes(root_private),
                )


if __name__ == "__main__":
    unittest.main()
