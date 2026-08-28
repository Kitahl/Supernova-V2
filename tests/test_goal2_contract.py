import copy
import hashlib
import hmac
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "goal2" / "GOAL2.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FIXTURE_KEY = b"goal2-contract-test-key-not-a-production-secret"


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sign_payload(payload, key=FIXTURE_KEY):
    return hmac.new(key, canonical_bytes(payload), hashlib.sha256).hexdigest()


def valid_sha256(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def frozen_fixture(contract):
    frozen = copy.deepcopy(contract)
    frozen["phase"] = "FROZEN"
    gate = frozen["opening_gate"]
    gate["authority_key_id"] = "goal1-final-authority-test"
    gate["authority_key_sha256"] = hashlib.sha256(FIXTURE_KEY).hexdigest()

    cost = frozen["complete_r_and_d_cost"]
    cost["budget_id"] = "g2-test-budget-v1"
    cost["budget_manifest_sha256"] = sha256_text("budget-manifest")
    cost["expected_event_ids_by_arm"] = {
        "control": ["control-improve", "control-select"],
        "treatment": ["treatment-improve", "treatment-select"],
    }

    frozen["selection_and_sealing"]["selection_rule_sha256"] = sha256_text(
        "selection-rule"
    )
    effect = frozen["effect_target"]
    effect.update(
        {
            "metric_id": "paired-kernel-verified-success-rate",
            "direction": "higher",
            "control_margin": 0.05,
            "untouched_margin": 0.05,
            "sampling_unit": "held-out-problem",
            "clustering_rule": "none",
            "analysis_plan_sha256": sha256_text("analysis-plan"),
        }
    )
    return frozen


def goal1_payload():
    return {
        "run_id": "goal1-confirmatory-run-001",
        "decision": "PASS",
        "goal1_final_report_sha256": sha256_text("final-report"),
        "goal1_protocol_sha256": sha256_text("protocol"),
        "goal1_cohort_sha256": sha256_text("cohort"),
        "goal1_evidence_bridge_sha256": sha256_text("bridge"),
        "goal1_evaluator_sha256": sha256_text("evaluator"),
    }


def goal1_receipt(payload=None):
    payload = goal1_payload() if payload is None else payload
    return {
        "schema": "supernova.goal1.final-pass-receipt.v1",
        "key_id": "goal1-final-authority-test",
        "payload": payload,
        "signature": sign_payload(payload),
    }


def execution_gate(contract, receipt, authority_key):
    """Minimal executable opening check for the contract-only artifact."""
    if contract.get("phase") != contract["opening_gate"]["required_contract_phase"]:
        return "BLOCKED"

    gate = contract["opening_gate"]
    if receipt.get("schema") != gate["goal1_receipt_schema"]:
        return "BLOCKED"
    if receipt.get("key_id") != gate["authority_key_id"]:
        return "BLOCKED"
    if hashlib.sha256(authority_key).hexdigest() != gate["authority_key_sha256"]:
        return "BLOCKED"

    payload = receipt.get("payload")
    if not isinstance(payload, dict):
        return "BLOCKED"
    required = gate["required_goal1_payload_fields"]
    if any(field not in payload for field in required):
        return "BLOCKED"
    if payload.get("decision") != gate["required_goal1_decision"]:
        return "BLOCKED"
    if not isinstance(payload.get("run_id"), str) or not payload["run_id"]:
        return "BLOCKED"
    for field in required:
        if field.endswith("_sha256") and not valid_sha256(payload.get(field)):
            return "BLOCKED"

    supplied = receipt.get("signature", "")
    if not hmac.compare_digest(supplied, sign_payload(payload, authority_key)):
        return "BLOCKED"

    cost = contract["complete_r_and_d_cost"]
    if (
        cost.get("budget_id") == "MUST_BE_FROZEN_BEFORE_OPEN"
        or not valid_sha256(cost.get("budget_manifest_sha256"))
        or not isinstance(cost.get("expected_event_ids_by_arm"), dict)
        or set(cost["expected_event_ids_by_arm"]) != {"control", "treatment"}
    ):
        return "BLOCKED"
    for events in cost["expected_event_ids_by_arm"].values():
        if not isinstance(events, list) or not events or len(events) != len(set(events)):
            return "BLOCKED"

    if not valid_sha256(
        contract["selection_and_sealing"].get("selection_rule_sha256")
    ):
        return "BLOCKED"

    effect = contract["effect_target"]
    if effect.get("direction") not in {"higher", "lower"}:
        return "BLOCKED"
    if not isinstance(effect.get("control_margin"), (int, float)):
        return "BLOCKED"
    if not isinstance(effect.get("untouched_margin"), (int, float)):
        return "BLOCKED"
    if effect["control_margin"] < 0 or effect["untouched_margin"] < 0:
        return "BLOCKED"
    for field in ("metric_id", "sampling_unit", "clustering_rule"):
        if not isinstance(effect.get(field), str) or not effect[field]:
            return "BLOCKED"
        if "MUST_BE_" in effect[field]:
            return "BLOCKED"
    if not valid_sha256(effect.get("analysis_plan_sha256")):
        return "BLOCKED"

    return gate["open_state"]


class Goal2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_checked_in_contract_is_definition_only_and_blocked(self):
        self.assertEqual(self.contract["phase"], "CONTRACT_ONLY")
        self.assertEqual(
            execution_gate(self.contract, goal1_receipt(), FIXTURE_KEY), "BLOCKED"
        )
        self.assertFalse(
            self.contract["decision_rules"]["BLOCKED"]["scientific_credit"]
        )

    def test_valid_synthetic_frozen_fixture_can_open(self):
        frozen = frozen_fixture(self.contract)
        self.assertEqual(execution_gate(frozen, goal1_receipt(), FIXTURE_KEY), "OPEN")

    def test_plain_pass_claim_or_forged_receipt_cannot_open(self):
        frozen = frozen_fixture(self.contract)
        self.assertEqual(execution_gate(frozen, {"decision": "PASS"}, FIXTURE_KEY), "BLOCKED")
        forged = goal1_receipt()
        forged["signature"] = "0" * 64
        self.assertEqual(execution_gate(frozen, forged, FIXTURE_KEY), "BLOCKED")

    def test_missing_or_substituted_goal1_evidence_cannot_open(self):
        frozen = frozen_fixture(self.contract)
        missing_payload = goal1_payload()
        del missing_payload["goal1_cohort_sha256"]
        missing = goal1_receipt(missing_payload)
        self.assertEqual(execution_gate(frozen, missing, FIXTURE_KEY), "BLOCKED")

        substituted = goal1_receipt()
        substituted["payload"]["run_id"] = "another-run"
        self.assertEqual(execution_gate(frozen, substituted, FIXTURE_KEY), "BLOCKED")

    def test_effect_target_is_frozen_not_caller_supplied(self):
        frozen = frozen_fixture(self.contract)
        for field in (
            "metric_id",
            "direction",
            "control_margin",
            "untouched_margin",
            "sampling_unit",
            "clustering_rule",
            "analysis_plan_sha256",
        ):
            broken = copy.deepcopy(frozen)
            broken["effect_target"][field] = "MUST_BE_FROZEN_BEFORE_OPEN"
            self.assertEqual(
                execution_gate(broken, goal1_receipt(), FIXTURE_KEY),
                "BLOCKED",
                field,
            )

    def test_complete_cost_basis_and_exact_event_ledgers_are_required(self):
        cost = self.contract["complete_r_and_d_cost"]
        self.assertEqual(cost["model_usage_basis"], "visible_utf8_bytes")
        self.assertEqual(
            cost["required_dimensions"],
            [
                "model_calls",
                "input_utf8_bytes",
                "output_utf8_bytes",
                "verifier_milliseconds",
                "orchestration_milliseconds",
            ],
        )
        frozen = frozen_fixture(self.contract)
        broken = copy.deepcopy(frozen)
        broken["complete_r_and_d_cost"]["expected_event_ids_by_arm"]["control"] = []
        self.assertEqual(execution_gate(broken, goal1_receipt(), FIXTURE_KEY), "BLOCKED")

    def test_component_and_lineage_separation_is_mandatory(self):
        components = self.contract["components"]
        self.assertTrue(components["component_ids_must_be_distinct"])
        self.assertTrue(components["component_artifact_sha256_values_must_be_distinct"])
        self.assertTrue(components["pristine_solver_parent_must_match_both_arms"])
        self.assertTrue(components["control_must_use_exact_I0_and_M0"])
        self.assertTrue(components["treatment_I1_and_M1_ids_and_artifacts_must_be_new_and_distinct"])
        self.assertEqual(components["arm_lineage_authentication"], "HMAC-SHA256")

    def test_selection_must_seal_before_fresh_release(self):
        selection = self.contract["selection_and_sealing"]
        fresh = self.contract["fresh_evaluation"]
        self.assertTrue(selection["post_release_candidate_rule_ledger_or_descendant_substitution_forbidden"])
        self.assertTrue(fresh["evaluation_release_sequence_must_exceed_both_selection_ledger_seal_sequences"])
        self.assertTrue(fresh["evaluation_items_must_be_disjoint_from_all_r_and_d_and_meta_improvement_data"])
        self.assertTrue(fresh["untouched_solver_outcome_required"])

    def test_terminal_states_are_exhaustive_and_credit_safe(self):
        rules = self.contract["decision_rules"]
        self.assertEqual(set(rules), {"BLOCKED", "INCOMPLETE", "PASS", "FAIL"})
        self.assertEqual(self.contract["decision_priority"], ["BLOCKED", "INCOMPLETE", "PASS", "FAIL"])
        self.assertFalse(rules["BLOCKED"]["scientific_credit"])
        self.assertFalse(rules["INCOMPLETE"]["scientific_credit"])
        self.assertTrue(rules["PASS"]["scientific_credit"])
        self.assertTrue(rules["FAIL"]["scientific_credit"])


if __name__ == "__main__":
    unittest.main()
