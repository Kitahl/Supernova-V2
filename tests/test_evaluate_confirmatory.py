from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import supernova_goal1.evidence_bridge as bridge_module
from supernova_goal1.confirmatory_manifest import NON_CREDIT_DRAFT
from supernova_goal1.contracts import Arm, CompleteCost
from supernova_goal1.dispatch import CompletionStatus
from supernova_goal1.evidence_bridge import (
    EvidenceBridgeBundle,
    EvidenceBridgeReceipt,
    EvaluatorEvidenceRecord,
    ExecutionLedgerAuthority,
)
from supernova_goal1.evaluate_confirmatory import (
    EXPECTED_PROTOCOL_RULES_SHA256,
    EXPECTED_REPORT_PROBLEM_IDS,
    PRODUCTION_CREDIT_STATUS,
    evaluate_confirmatory,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ConfirmatoryEvaluatorTests(unittest.TestCase):
    RUN_ID = "confirmatory-evaluator-test"
    MANIFEST = sha("confirmatory-manifest")
    DISPATCH_MANIFEST = sha("dispatch-manifest")
    CLOSE = sha("close")
    COMPLETION_SET = sha("completion-set")
    AUTHORITY = sha("execution-authority")

    def authority(self) -> ExecutionLedgerAuthority:
        authority = object.__new__(ExecutionLedgerAuthority)
        authority.run_id = self.RUN_ID
        authority.issuer_id = "test-host"
        authority.execution_authority_sha256 = self.AUTHORITY
        authority.protocol_rules_sha256 = EXPECTED_PROTOCOL_RULES_SHA256
        authority.confirmatory_manifest_sha256 = self.MANIFEST
        authority._ExecutionLedgerAuthority__secret = b"s" * 32
        return authority

    def record(
        self,
        problem_id: str,
        arm: Arm,
        *,
        credit_status: str,
        statuses: tuple[CompletionStatus, ...] | None = None,
    ) -> EvaluatorEvidenceRecord:
        if statuses is None:
            statuses = (CompletionStatus.FAILED,) * 16
        prefix = f"{problem_id}:{arm.value}"
        vectors = {
            "protocol_dispatch_ids": tuple(
                "dispatch-" + sha(f"{prefix}:protocol:{attempt}")
                for attempt in range(16)
            ),
            "protocol_binding_receipt_sha256s": tuple(
                sha(f"{prefix}:binding:{attempt}") for attempt in range(16)
            ),
            "dispatch_ids": tuple(
                sha(f"{prefix}:actual:{attempt}") for attempt in range(16)
            ),
            "completion_record_sha256s": tuple(
                sha(f"{prefix}:completion:{attempt}") for attempt in range(16)
            ),
            "verifier_evidence_sha256s": tuple(
                sha(f"{prefix}:verifier:{attempt}") for attempt in range(16)
            ),
            "execution_receipt_sha256s": tuple(
                sha(f"{prefix}:execution:{attempt}") for attempt in range(16)
            ),
            "context_isolation_receipt_sha256s": tuple(
                sha(f"{prefix}:context:{attempt}") for attempt in range(16)
            ),
            "predecessor_reconciliation_sha256s": tuple(
                sha(f"{prefix}:predecessor:{attempt}") for attempt in range(16)
            ),
        }
        return EvaluatorEvidenceRecord(
            experiment_id="goal1-confirmatory-v1",
            problem_id=problem_id,
            problem_identity=sha("identity:" + problem_id),
            arm=arm,
            budget_id="goal1-common-envelope-v1",
            model_usage_basis="visible_utf8_bytes",
            cost=CompleteCost(
                16,
                list(Arm).index(arm) + 1,
                list(Arm).index(arm) + 2,
                list(Arm).index(arm) + 3,
                list(Arm).index(arm) + 4,
            ),
            completion_statuses=statuses,
            manifest_credit_status=credit_status,
            protocol_rules_sha256=EXPECTED_PROTOCOL_RULES_SHA256,
            confirmatory_manifest_sha256=self.MANIFEST,
            dispatch_manifest_sha256=self.DISPATCH_MANIFEST,
            close_sha256=self.CLOSE,
            completion_set_sha256=self.COMPLETION_SET,
            execution_authority_sha256=self.AUTHORITY,
            cost_trace_sha256=sha(prefix + ":cost"),
            _factory=bridge_module._RECORD_FACTORY,
            **vectors,
        )

    def bundle(
        self,
        records: tuple[EvaluatorEvidenceRecord, ...],
        *,
        credit_status: str,
        authority: ExecutionLedgerAuthority,
    ) -> EvidenceBridgeBundle:
        dummy = EvidenceBridgeReceipt(
            issuer_id="test-host",
            run_id=self.RUN_ID,
            execution_authority_sha256=self.AUTHORITY,
            bridge_sha256=sha("dummy-bridge"),
            signature=sha("dummy-signature"),
        )
        arguments = {
            "run_id": self.RUN_ID,
            "manifest_credit_status": credit_status,
            "protocol_rules_sha256": EXPECTED_PROTOCOL_RULES_SHA256,
            "confirmatory_manifest_sha256": self.MANIFEST,
            "dispatch_manifest_sha256": self.DISPATCH_MANIFEST,
            "close_sha256": self.CLOSE,
            "completion_set_sha256": self.COMPLETION_SET,
            "execution_authority_sha256": self.AUTHORITY,
            "records": records,
            "authority_receipt": dummy,
            "_factory": bridge_module._BUNDLE_FACTORY,
        }
        unsigned = EvidenceBridgeBundle(**arguments)
        arguments["authority_receipt"] = authority._issue_evidence_bridge_receipt(
            unsigned.bridge_sha256
        )
        return EvidenceBridgeBundle(**arguments)

    def full_records(
        self,
        *,
        verified_success_attempt: int | None,
        control_success_attempt: int | None,
        credit_status: str = PRODUCTION_CREDIT_STATUS,
    ) -> tuple[EvaluatorEvidenceRecord, ...]:
        records = []
        for problem_id in EXPECTED_REPORT_PROBLEM_IDS:
            for arm in Arm:
                attempt = (
                    verified_success_attempt
                    if arm is Arm.VERIFIED_CHAIN
                    else control_success_attempt
                )
                statuses = [CompletionStatus.FAILED] * 16
                if attempt is not None:
                    statuses[attempt] = CompletionStatus.SUCCEEDED
                records.append(
                    self.record(
                        problem_id,
                        arm,
                        credit_status=credit_status,
                        statuses=tuple(statuses),
                    )
                )
        return tuple(records)

    def test_public_api_rejects_raw_records(self) -> None:
        with self.assertRaisesRegex(TypeError, "exact EvidenceBridgeBundle"):
            evaluate_confirmatory([], evidence_authority=self.authority())

    def test_authenticated_non_credit_draft_is_blocked_without_statistics(self) -> None:
        authority = self.authority()
        record = self.record(
            EXPECTED_REPORT_PROBLEM_IDS[0],
            Arm.ORDINARY,
            credit_status=NON_CREDIT_DRAFT,
        )
        bundle = self.bundle(
            (record,), credit_status=NON_CREDIT_DRAFT, authority=authority
        )
        result = evaluate_confirmatory(bundle, evidence_authority=authority)
        self.assertEqual("BLOCKED", result["decision"])
        self.assertIn("NON_CREDIT_DRAFT", result["blockers"])
        self.assertEqual([], result["contrasts"])
        self.assertFalse(result["decision_eligible"])

    def test_forged_summary_fails_authentication_before_statistics(self) -> None:
        authority = self.authority()
        record = self.record(
            EXPECTED_REPORT_PROBLEM_IDS[0],
            Arm.ORDINARY,
            credit_status=NON_CREDIT_DRAFT,
        )
        genuine = self.bundle(
            (record,), credit_status=NON_CREDIT_DRAFT, authority=authority
        )
        values = list(genuine)
        values[genuine._fields.index("manifest_credit_status")] = (
            PRODUCTION_CREDIT_STATUS
        )
        forged = tuple.__new__(EvidenceBridgeBundle, tuple(values))
        result = evaluate_confirmatory(forged, evidence_authority=authority)
        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual("BRIDGE_AUTHENTICATION_FAILED", result["reason"])
        self.assertEqual([], result["contrasts"])

    def test_complete_superiority_passes_with_unequal_realized_costs(self) -> None:
        authority = self.authority()
        bundle = self.bundle(
            self.full_records(
                verified_success_attempt=15,
                control_success_attempt=None,
            ),
            credit_status=PRODUCTION_CREDIT_STATUS,
            authority=authority,
        )
        result = evaluate_confirmatory(bundle, evidence_authority=authority)
        self.assertEqual("PASS", result["decision"])
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(4, len(result["contrasts"]))
        self.assertTrue(all(item["contrast_pass"] for item in result["contrasts"]))
        self.assertEqual(244, result["solved"]["verified_chain"]["solved"])
        self.assertEqual(
            0,
            result["prefix_frontier"][0]["solved"]["verified_chain"]["solved"],
        )
        self.assertEqual(
            244,
            result["prefix_frontier"][-1]["solved"]["verified_chain"]["solved"],
        )
        self.assertTrue(
            all(
                checkpoint["used_for_terminal_decision"] is False
                for checkpoint in result["prefix_frontier"]
            )
        )
        realized = result["realized_usage"]["per_arm_aggregate"]
        self.assertNotEqual(
            realized["ordinary"]["input_utf8_bytes"],
            realized["verified_chain"]["input_utf8_bytes"],
        )
        self.assertFalse(
            result["realized_usage"][
                "realized_cost_used_for_matching_or_exclusion"
            ]
        )

    def test_zero_discordance_fails_and_holm_ties_use_control_name(self) -> None:
        authority = self.authority()
        bundle = self.bundle(
            self.full_records(
                verified_success_attempt=0,
                control_success_attempt=15,
            ),
            credit_status=PRODUCTION_CREDIT_STATUS,
            authority=authority,
        )
        result = evaluate_confirmatory(bundle, evidence_authority=authority)
        self.assertEqual("FAIL", result["decision"])
        by_control = {item["control"]: item for item in result["contrasts"]}
        self.assertEqual(1.0, by_control["ordinary"]["exact_two_sided_p"])
        self.assertEqual(1, by_control["multi_fidelity"]["holm_rank"])
        self.assertEqual(2, by_control["ordinary"]["holm_rank"])
        self.assertEqual(3, by_control["portfolio"]["holm_rank"])
        self.assertEqual(4, by_control["product_only"]["holm_rank"])
        self.assertEqual(
            244,
            result["prefix_frontier"][0]["solved"]["verified_chain"]["solved"],
        )
        self.assertEqual(
            244,
            result["prefix_frontier"][-1]["solved"]["ordinary"]["solved"],
        )

    def test_missing_cell_is_incomplete_not_fail(self) -> None:
        authority = self.authority()
        records = self.full_records(
            verified_success_attempt=0,
            control_success_attempt=None,
        )
        bundle = self.bundle(
            records[:-1],
            credit_status=PRODUCTION_CREDIT_STATUS,
            authority=authority,
        )
        result = evaluate_confirmatory(bundle, evidence_authority=authority)
        self.assertEqual("INCOMPLETE", result["decision"])
        self.assertEqual([], result["contrasts"])
        self.assertEqual(1, len(result["missing"]))

    def test_verifier_timeout_blocks_complete_cohort(self) -> None:
        authority = self.authority()
        records = list(
            self.full_records(
                verified_success_attempt=0,
                control_success_attempt=None,
            )
        )
        target = records[0]
        values = list(target)
        statuses = list(target.completion_statuses)
        statuses[0] = CompletionStatus.TIMEOUT
        values[target._fields.index("completion_statuses")] = tuple(statuses)
        records[0] = tuple.__new__(EvaluatorEvidenceRecord, tuple(values))
        bundle = self.bundle(
            tuple(records),
            credit_status=PRODUCTION_CREDIT_STATUS,
            authority=authority,
        )
        result = evaluate_confirmatory(bundle, evidence_authority=authority)
        self.assertEqual("BLOCKED", result["decision"])
        self.assertTrue(
            any(reason.startswith("VERIFIER_TIMEOUT") for reason in result["blockers"])
        )
        self.assertEqual([], result["contrasts"])


if __name__ == "__main__":
    unittest.main()
