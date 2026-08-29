from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from supernova_goal1.activation import activate_confirmatory_execution
from supernova_goal1.confirmatory_manifest import (
    NON_CREDIT_DRAFT,
    assert_dispatch_authorized,
)
from supernova_goal1.contracts import Arm, CompleteCost
from supernova_goal1.evidence_bridge import EvaluatorEvidenceRecord
from supernova_goal1.evaluate_confirmatory import (
    _evaluate_non_credit_draft,
    evaluate_confirmatory,
)
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
import tests.test_evaluate_confirmatory as evaluator_test_module
from tests.test_evaluate_confirmatory import sha


class ConfirmatoryAdversarialTests(unittest.TestCase):
    """Attacks must stop before any scientific PASS can be produced."""

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

    def test_fabricated_pass_outcomes_cannot_use_caller_hmac(self) -> None:
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
        self.assertFalse(result["decision_eligible"])
        self.assertEqual([], result["contrasts"])
        self.assertIn(
            result["reason"],
            {
                "PRODUCTION_EXECUTION_AUTHORITY_UNAVAILABLE",
                "BRIDGE_AUTHENTICATION_FAILED",
            },
        )

    def test_missing_cost_and_verifier_substitution_never_pass(self) -> None:
        fixture = self.evaluator_fixture()
        caller_authority = fixture.authority()
        record = fixture.record(
            fixture.RUN_ID,
            Arm.ORDINARY,
            credit_status=NON_CREDIT_DRAFT,
        )

        missing_cost_values = list(record)
        missing_cost_values[record._fields.index("cost")] = None
        missing_cost = tuple.__new__(
            EvaluatorEvidenceRecord, tuple(missing_cost_values)
        )
        with self.assertRaises((AttributeError, TypeError, ValueError)):
            fixture.bundle(
                (missing_cost,),
                credit_status=NON_CREDIT_DRAFT,
                authority=caller_authority,
            )

        substituted_values = list(record)
        substituted_verifier = list(record.verifier_evidence_sha256s)
        substituted_verifier[0] = sha("substituted-verifier")
        substituted_values[
            record._fields.index("verifier_evidence_sha256s")
        ] = tuple(substituted_verifier)
        substituted = tuple.__new__(
            EvaluatorEvidenceRecord, tuple(substituted_values)
        )
        bundle = fixture.bundle(
            (substituted,),
            credit_status=NON_CREDIT_DRAFT,
            authority=caller_authority,
        )
        result = _evaluate_non_credit_draft(
            bundle,
            evidence_authority=caller_authority,
        )
        self.assertEqual("BLOCKED", result["decision"])
        self.assertFalse(result["decision_eligible"])
        self.assertEqual([], result["contrasts"])

    def test_partial_cohort_is_explicitly_noncredit(self) -> None:
        fixture = self.evaluator_fixture()
        caller_authority = fixture.authority()
        record = fixture.record(
            fixture.RUN_ID,
            Arm.ORDINARY,
            credit_status=NON_CREDIT_DRAFT,
        )
        bundle = fixture.bundle(
            (record,),
            credit_status=NON_CREDIT_DRAFT,
            authority=caller_authority,
        )
        result = _evaluate_non_credit_draft(
            bundle,
            evidence_authority=caller_authority,
        )
        self.assertEqual("BLOCKED", result["decision"])
        self.assertIn("NON_CREDIT_DRAFT", result["blockers"])
        self.assertIn("MISSING_PAIRED_CELLS", result["incomplete_reasons"])
        self.assertFalse(result["decision_eligible"])

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
