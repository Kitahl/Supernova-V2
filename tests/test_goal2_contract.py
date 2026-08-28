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
EVALUATOR_KEY = b"goal2-independent-evaluator-test-key"
DIMENSIONS = {
    "model_calls",
    "input_utf8_bytes",
    "output_utf8_bytes",
    "verifier_milliseconds",
    "orchestration_milliseconds",
}


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def valid_sha256(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def signed(schema, payload, key=FIXTURE_KEY, key_id="goal2-test-authority"):
    return {
        "schema": schema,
        "key_id": key_id,
        "payload": payload,
        "signature": hmac.new(
            key, canonical_bytes(payload), hashlib.sha256
        ).hexdigest(),
    }


def verify_signed(record, schema, key, key_id="goal2-test-authority"):
    if not isinstance(record, dict) or record.get("schema") != schema:
        return None
    if record.get("key_id") != key_id:
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    expected = hmac.new(key, canonical_bytes(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(record.get("signature", ""), expected):
        return None
    return payload


def component_fixture():
    roles = ["solver_F", "memory_M0", "improver_I0", "memory_M1", "improver_I1"]
    return {
        role: {
            "component_id": role + "-id",
            "artifact_sha256": digest(role + "-artifact"),
            "runtime_or_schema_sha256": digest(role + "-runtime"),
        }
        for role in roles
    }


def frozen_fixture(contract):
    frozen = copy.deepcopy(contract)
    frozen["phase"] = "FROZEN"
    gate = frozen["opening_gate"]
    gate["authority_key_id"] = "goal2-test-authority"
    gate["authority_key_sha256"] = hashlib.sha256(FIXTURE_KEY).hexdigest()
    fresh = frozen["fresh_evaluation"]
    fresh["evaluator_authority_key_id"] = "goal2-test-evaluator"
    fresh["evaluator_authority_key_sha256"] = hashlib.sha256(EVALUATOR_KEY).hexdigest()
    source_components = {
        role: component_fixture()[role]
        for role in frozen["components"]["frozen_source_roles"]
    }
    frozen["components"]["source_component_manifest_sha256"] = hashlib.sha256(
        canonical_bytes(source_components)
    ).hexdigest()

    cost = frozen["complete_r_and_d_cost"]
    cost["budget_id"] = "g2-budget-v1"
    cost["budget_manifest_sha256"] = digest("budget-manifest")
    cost["budget_ceiling_by_dimension"] = {
        "model_calls": 4,
        "input_utf8_bytes": 4000,
        "output_utf8_bytes": 2000,
        "verifier_milliseconds": 400,
        "orchestration_milliseconds": 400,
    }
    cost["expected_event_ids_by_arm"] = {
        "control": ["c-improve", "c-select"],
        "treatment": ["t-improve", "t-select"],
    }

    selection = frozen["selection_and_sealing"]
    selection["selection_rule_sha256"] = digest("selection-rule")
    selection["candidate_set_manifest_sha256_by_arm"] = {
        "control": digest("control-candidates"),
        "treatment": digest("treatment-candidates"),
    }

    frozen["fresh_evaluation"]["evaluation_manifest_sha256"] = digest(
        "fresh-evaluation"
    )
    frozen["effect_target"].update(
        {
            "metric_id": "paired-kernel-verified-success-rate",
            "direction": "higher",
            "control_margin": 0.05,
            "untouched_margin": 0.05,
            "sampling_unit": "held-out-problem",
            "clustering_rule": "none",
            "analysis_plan_sha256": digest("analysis-plan"),
        }
    )
    return frozen


def complete_bundle(contract):
    run_id = "goal2-run-001"
    goal1 = {
        "run_id": "goal1-confirmatory-run-001",
        "decision": "PASS",
        "goal1_final_report_sha256": digest("goal1-report"),
        "goal1_protocol_sha256": digest("goal1-protocol"),
        "goal1_cohort_sha256": digest("goal1-cohort"),
        "goal1_evidence_bridge_sha256": digest("goal1-bridge"),
        "goal1_evaluator_sha256": digest("goal1-evaluator"),
    }
    components = component_fixture()
    source_components = {
        role: components[role]
        for role in contract["components"]["frozen_source_roles"]
    }
    parent = components["solver_F"]["artifact_sha256"]
    control_descendant = digest("control-descendant")
    treatment_descendant = digest("treatment-descendant")

    freeze = {
        "contract_id": contract["contract_id"],
        "budget_id": contract["complete_r_and_d_cost"]["budget_id"],
        "budget_manifest_sha256": contract["complete_r_and_d_cost"][
            "budget_manifest_sha256"
        ],
        "budget_ceiling_by_dimension": contract["complete_r_and_d_cost"][
            "budget_ceiling_by_dimension"
        ],
        "expected_event_ids_by_arm": contract["complete_r_and_d_cost"][
            "expected_event_ids_by_arm"
        ],
        "selection_rule_sha256": contract["selection_and_sealing"][
            "selection_rule_sha256"
        ],
        "candidate_set_manifest_sha256_by_arm": contract[
            "selection_and_sealing"
        ]["candidate_set_manifest_sha256_by_arm"],
        "evaluation_manifest_sha256": contract["fresh_evaluation"][
            "evaluation_manifest_sha256"
        ],
        "analysis_plan_sha256": contract["effect_target"]["analysis_plan_sha256"],
        "source_component_manifest_sha256": contract["components"]["source_component_manifest_sha256"],
        "evaluator_authority_key_id": contract["fresh_evaluation"]["evaluator_authority_key_id"],
        "evaluator_authority_key_sha256": contract["fresh_evaluation"]["evaluator_authority_key_sha256"],
    }

    lineage = {
        "control": signed(
            contract["components"]["arm_lineage_schema"],
            {
                "run_id": run_id,
                "arm": "control",
                "parent_solver_sha256": parent,
                "improver_id": components["improver_I0"]["component_id"],
                "improver_artifact_sha256": components["improver_I0"]["artifact_sha256"],
                "memory_id": components["memory_M0"]["component_id"],
                "memory_artifact_sha256": components["memory_M0"]["artifact_sha256"],
                "descendant_sha256": control_descendant,
            },
        ),
        "treatment": signed(
            contract["components"]["arm_lineage_schema"],
            {
                "run_id": run_id,
                "arm": "treatment",
                "parent_solver_sha256": parent,
                "starting_improver_id": components["improver_I0"]["component_id"],
                "starting_improver_artifact_sha256": components["improver_I0"]["artifact_sha256"],
                "starting_memory_id": components["memory_M0"]["component_id"],
                "starting_memory_artifact_sha256": components["memory_M0"]["artifact_sha256"],
                "improved_improver_id": components["improver_I1"]["component_id"],
                "improved_improver_artifact_sha256": components["improver_I1"]["artifact_sha256"],
                "improved_memory_id": components["memory_M1"]["component_id"],
                "improved_memory_artifact_sha256": components["memory_M1"]["artifact_sha256"],
                "descendant_sha256": treatment_descendant,
            },
        ),
    }

    def cost_ledger(arm):
        expected = contract["complete_r_and_d_cost"]["expected_event_ids_by_arm"][arm]
        observed = [
            {
                "event_id": event_id,
                "usage": {
                    "model_calls": 1,
                    "input_utf8_bytes": 100,
                    "output_utf8_bytes": 50,
                    "verifier_milliseconds": 10,
                    "orchestration_milliseconds": 10,
                },
            }
            for event_id in expected
        ]
        return signed(
            contract["complete_r_and_d_cost"]["cost_ledger_schema"],
            {
                "run_id": run_id,
                "arm": arm,
                "budget_id": contract["complete_r_and_d_cost"]["budget_id"],
                "budget_manifest_sha256": contract["complete_r_and_d_cost"][
                    "budget_manifest_sha256"
                ],
                "expected_event_ids": expected,
                "observed_events": observed,
            },
        )

    selection = {
        "control": signed(
            contract["selection_and_sealing"]["selection_ledger_schema"],
            {
                "run_id": run_id,
                "arm": "control",
                "candidate_set_manifest_sha256": contract[
                    "selection_and_sealing"
                ]["candidate_set_manifest_sha256_by_arm"]["control"],
                "selection_rule_sha256": contract["selection_and_sealing"][
                    "selection_rule_sha256"
                ],
                "selected_descendant_sha256": control_descendant,
                "seal_sequence": 10,
            },
        ),
        "treatment": signed(
            contract["selection_and_sealing"]["selection_ledger_schema"],
            {
                "run_id": run_id,
                "arm": "treatment",
                "candidate_set_manifest_sha256": contract[
                    "selection_and_sealing"
                ]["candidate_set_manifest_sha256_by_arm"]["treatment"],
                "selection_rule_sha256": contract["selection_and_sealing"][
                    "selection_rule_sha256"
                ],
                "selected_descendant_sha256": treatment_descendant,
                "seal_sequence": 11,
            },
        ),
    }

    evaluation = signed(
        contract["fresh_evaluation"]["evaluation_receipt_schema"],
        {
            "run_id": run_id,
            "evaluation_manifest_sha256": contract["fresh_evaluation"][
                "evaluation_manifest_sha256"
            ],
            "release_sequence": 12,
            "evaluation_item_ids": ["heldout-1", "heldout-2"],
            "r_and_d_item_ids": ["diag-1", "select-1"],
            "evaluation_authority_id": "independent-evaluator",
            "same_protocol_for_all_arms": True,
            "untouched_solver_outcome_present": True,
        },
        key=EVALUATOR_KEY,
        key_id="goal2-test-evaluator",
    )

    return {
        "run_id": run_id,
        "goal1_receipt": signed(
            contract["opening_gate"]["goal1_receipt_schema"], goal1
        ),
        "frozen_artifact_receipt": signed(
            contract["opening_gate"]["frozen_artifact_receipt_schema"], freeze
        ),
        "source_components": source_components,
        "components": components,
        "lineage": lineage,
        "cost_ledgers": {
            "control": cost_ledger("control"),
            "treatment": cost_ledger("treatment"),
        },
        "selection_ledgers": selection,
        "evaluation_release": evaluation,
    }


def pre_dispatch_admission(contract, bundle, authority_key):
    gate = contract.get("opening_gate", {})
    if contract.get("phase") != gate.get("required_contract_phase"):
        return "BLOCKED"
    if hashlib.sha256(authority_key).hexdigest() != gate.get("authority_key_sha256"):
        return "BLOCKED"

    goal1 = verify_signed(
        bundle.get("goal1_receipt"), gate.get("goal1_receipt_schema"), authority_key
    )
    if goal1 is None or goal1.get("decision") != gate.get("required_goal1_decision"):
        return "BLOCKED"
    required = gate.get("required_goal1_payload_fields", [])
    if any(field not in goal1 for field in required):
        return "BLOCKED"
    if not isinstance(goal1.get("run_id"), str) or not goal1["run_id"]:
        return "BLOCKED"
    if any(
        not valid_sha256(goal1.get(field))
        for field in required
        if field.endswith("_sha256")
    ):
        return "BLOCKED"

    freeze = verify_signed(
        bundle.get("frozen_artifact_receipt"),
        gate.get("frozen_artifact_receipt_schema"),
        authority_key,
    )
    if freeze is None:
        return "BLOCKED"
    cost = contract["complete_r_and_d_cost"]
    selection = contract["selection_and_sealing"]
    fresh = contract["fresh_evaluation"]
    effect = contract["effect_target"]
    expected = {
        "contract_id": contract["contract_id"],
        "budget_id": cost["budget_id"],
        "budget_manifest_sha256": cost["budget_manifest_sha256"],
        "budget_ceiling_by_dimension": cost["budget_ceiling_by_dimension"],
        "expected_event_ids_by_arm": cost["expected_event_ids_by_arm"],
        "selection_rule_sha256": selection["selection_rule_sha256"],
        "candidate_set_manifest_sha256_by_arm": selection[
            "candidate_set_manifest_sha256_by_arm"
        ],
        "evaluation_manifest_sha256": fresh["evaluation_manifest_sha256"],
        "analysis_plan_sha256": effect["analysis_plan_sha256"],
        "source_component_manifest_sha256": contract["components"][
            "source_component_manifest_sha256"
        ],
        "evaluator_authority_key_id": fresh["evaluator_authority_key_id"],
        "evaluator_authority_key_sha256": fresh["evaluator_authority_key_sha256"],
    }
    if freeze != expected:
        return "BLOCKED"
    if fresh["evaluator_authority_key_id"] == gate["authority_key_id"]:
        return "BLOCKED"
    if fresh["evaluator_authority_key_sha256"] == gate["authority_key_sha256"]:
        return "BLOCKED"
    source = bundle.get("source_components")
    source_roles = contract["components"]["frozen_source_roles"]
    if not isinstance(source, dict) or set(source) != set(source_roles):
        return "BLOCKED"
    if hashlib.sha256(canonical_bytes(source)).hexdigest() != contract["components"][
        "source_component_manifest_sha256"
    ]:
        return "BLOCKED"
    return gate["open_state"]


def evidence_admission(contract, bundle, authority_key, evaluator_key):
    if pre_dispatch_admission(contract, bundle, authority_key) != "OPEN":
        return "BLOCKED"

    gate = contract.get("opening_gate", {})
    if contract.get("phase") != gate.get("required_contract_phase"):
        return "BLOCKED"
    if hashlib.sha256(authority_key).hexdigest() != gate.get(
        "authority_key_sha256"
    ):
        return "BLOCKED"

    goal1 = verify_signed(
        bundle.get("goal1_receipt"), gate.get("goal1_receipt_schema"), authority_key
    )
    if goal1 is None or goal1.get("decision") != gate.get("required_goal1_decision"):
        return "BLOCKED"
    required = gate.get("required_goal1_payload_fields", [])
    if any(field not in goal1 for field in required):
        return "BLOCKED"
    if not isinstance(goal1.get("run_id"), str) or not goal1["run_id"]:
        return "BLOCKED"
    if any(
        not valid_sha256(goal1.get(field))
        for field in required
        if field.endswith("_sha256")
    ):
        return "BLOCKED"

    freeze = verify_signed(
        bundle.get("frozen_artifact_receipt"),
        gate.get("frozen_artifact_receipt_schema"),
        authority_key,
    )
    if freeze is None:
        return "BLOCKED"
    cost = contract["complete_r_and_d_cost"]
    selection_contract = contract["selection_and_sealing"]
    fresh_contract = contract["fresh_evaluation"]
    effect = contract["effect_target"]
    expected_freeze = {
        "contract_id": contract["contract_id"],
        "budget_id": cost["budget_id"],
        "budget_manifest_sha256": cost["budget_manifest_sha256"],
        "budget_ceiling_by_dimension": cost["budget_ceiling_by_dimension"],
        "expected_event_ids_by_arm": cost["expected_event_ids_by_arm"],
        "selection_rule_sha256": selection_contract["selection_rule_sha256"],
        "candidate_set_manifest_sha256_by_arm": selection_contract[
            "candidate_set_manifest_sha256_by_arm"
        ],
        "evaluation_manifest_sha256": fresh_contract["evaluation_manifest_sha256"],
        "analysis_plan_sha256": effect["analysis_plan_sha256"],
        "source_component_manifest_sha256": contract["components"]["source_component_manifest_sha256"],
        "evaluator_authority_key_id": fresh_contract["evaluator_authority_key_id"],
        "evaluator_authority_key_sha256": fresh_contract["evaluator_authority_key_sha256"],
    }
    if freeze != expected_freeze:
        return "BLOCKED"
    if not all(
        valid_sha256(value)
        for value in (
            cost.get("budget_manifest_sha256"),
            selection_contract.get("selection_rule_sha256"),
            fresh_contract.get("evaluation_manifest_sha256"),
            effect.get("analysis_plan_sha256"),
        )
    ):
        return "BLOCKED"

    expected_by_arm = cost.get("expected_event_ids_by_arm")
    ceilings = cost.get("budget_ceiling_by_dimension")
    if not isinstance(expected_by_arm, dict) or set(expected_by_arm) != {
        "control",
        "treatment",
    }:
        return "BLOCKED"
    all_ids = expected_by_arm["control"] + expected_by_arm["treatment"]
    if not all_ids or len(all_ids) != len(set(all_ids)):
        return "BLOCKED"
    if not isinstance(ceilings, dict) or set(ceilings) != DIMENSIONS:
        return "BLOCKED"
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in ceilings.values()):
        return "BLOCKED"

    components = bundle.get("components")
    required_roles = contract["components"]["required_roles"]
    if not isinstance(components, dict) or set(components) != set(required_roles):
        return "BLOCKED"
    identities = []
    artifacts = []
    for role in required_roles:
        component = components[role]
        if set(component) != set(contract["components"]["required_identity_fields"]):
            return "BLOCKED"
        identities.append(component["component_id"])
        artifacts.append(component["artifact_sha256"])
        if not valid_sha256(component["artifact_sha256"]):
            return "BLOCKED"
        if not valid_sha256(component["runtime_or_schema_sha256"]):
            return "BLOCKED"
    if len(identities) != len(set(identities)) or len(artifacts) != len(set(artifacts)):
        return "BLOCKED"

    run_id = bundle.get("run_id")
    lineage_payloads = {}
    for arm in ("control", "treatment"):
        payload = verify_signed(
            bundle.get("lineage", {}).get(arm),
            contract["components"]["arm_lineage_schema"],
            authority_key,
        )
        if payload is None or payload.get("run_id") != run_id or payload.get("arm") != arm:
            return "BLOCKED"
        if payload.get("parent_solver_sha256") != components["solver_F"]["artifact_sha256"]:
            return "BLOCKED"
        lineage_payloads[arm] = payload
    if (
        lineage_payloads["control"].get("improver_id")
        != components["improver_I0"]["component_id"]
        or lineage_payloads["control"].get("improver_artifact_sha256")
        != components["improver_I0"]["artifact_sha256"]
        or lineage_payloads["control"].get("memory_id")
        != components["memory_M0"]["component_id"]
        or lineage_payloads["control"].get("memory_artifact_sha256")
        != components["memory_M0"]["artifact_sha256"]
        or lineage_payloads["treatment"].get("starting_improver_id")
        != components["improver_I0"]["component_id"]
        or lineage_payloads["treatment"].get("starting_improver_artifact_sha256")
        != components["improver_I0"]["artifact_sha256"]
        or lineage_payloads["treatment"].get("starting_memory_id")
        != components["memory_M0"]["component_id"]
        or lineage_payloads["treatment"].get("starting_memory_artifact_sha256")
        != components["memory_M0"]["artifact_sha256"]
        or lineage_payloads["treatment"].get("improved_improver_id")
        != components["improver_I1"]["component_id"]
        or lineage_payloads["treatment"].get("improved_improver_artifact_sha256")
        != components["improver_I1"]["artifact_sha256"]
        or lineage_payloads["treatment"].get("improved_memory_id")
        != components["memory_M1"]["component_id"]
        or lineage_payloads["treatment"].get("improved_memory_artifact_sha256")
        != components["memory_M1"]["artifact_sha256"]
    ):
        return "BLOCKED"
    descendants = [
        lineage_payloads["control"].get("descendant_sha256"),
        lineage_payloads["treatment"].get("descendant_sha256"),
    ]
    if not all(valid_sha256(value) for value in descendants):
        return "BLOCKED"
    if len(set(descendants + [components["solver_F"]["artifact_sha256"]])) != 3:
        return "BLOCKED"

    for arm in ("control", "treatment"):
        ledger = verify_signed(
            bundle.get("cost_ledgers", {}).get(arm),
            cost["cost_ledger_schema"],
            authority_key,
        )
        if ledger is None or ledger.get("run_id") != run_id or ledger.get("arm") != arm:
            return "BLOCKED"
        if (
            ledger.get("budget_id") != cost["budget_id"]
            or ledger.get("budget_manifest_sha256") != cost["budget_manifest_sha256"]
            or ledger.get("expected_event_ids") != expected_by_arm[arm]
        ):
            return "BLOCKED"
        observed = ledger.get("observed_events")
        if not isinstance(observed, list):
            return "BLOCKED"
        observed_ids = [event.get("event_id") for event in observed]
        if sorted(observed_ids) != sorted(expected_by_arm[arm]):
            return "BLOCKED"
        totals = {dimension: 0 for dimension in DIMENSIONS}
        for event in observed:
            usage = event.get("usage")
            if not isinstance(usage, dict) or set(usage) != DIMENSIONS:
                return "BLOCKED"
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in usage.values()
            ):
                return "BLOCKED"
            for dimension in DIMENSIONS:
                totals[dimension] += usage[dimension]
        if any(totals[d] > ceilings[d] for d in DIMENSIONS):
            return "BLOCKED"

    seals = {}
    for arm in ("control", "treatment"):
        ledger = verify_signed(
            bundle.get("selection_ledgers", {}).get(arm),
            selection_contract["selection_ledger_schema"],
            authority_key,
        )
        if ledger is None or ledger.get("run_id") != run_id or ledger.get("arm") != arm:
            return "BLOCKED"
        if (
            ledger.get("candidate_set_manifest_sha256")
            != selection_contract["candidate_set_manifest_sha256_by_arm"][arm]
            or ledger.get("selection_rule_sha256")
            != selection_contract["selection_rule_sha256"]
            or ledger.get("selected_descendant_sha256")
            != lineage_payloads[arm]["descendant_sha256"]
        ):
            return "BLOCKED"
        seal = ledger.get("seal_sequence")
        if not isinstance(seal, int) or isinstance(seal, bool) or seal < 0:
            return "BLOCKED"
        seals[arm] = seal

    release = verify_signed(
        bundle.get("evaluation_release"),
        fresh_contract["evaluation_receipt_schema"],
        evaluator_key,
        key_id=fresh_contract["evaluator_authority_key_id"],
    )
    if release is None or release.get("run_id") != run_id:
        return "BLOCKED"
    if release.get("evaluation_manifest_sha256") != fresh_contract[
        "evaluation_manifest_sha256"
    ]:
        return "BLOCKED"
    sequence = release.get("release_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        return "BLOCKED"
    if sequence <= max(seals.values()):
        return "BLOCKED"
    evaluation_items = release.get("evaluation_item_ids")
    rd_items = release.get("r_and_d_item_ids")
    if not isinstance(evaluation_items, list) or not evaluation_items:
        return "BLOCKED"
    if not isinstance(rd_items, list) or set(evaluation_items) & set(rd_items):
        return "BLOCKED"
    if (
        release.get("evaluation_authority_id") in identities
        or release.get("same_protocol_for_all_arms") is not True
        or release.get("untouched_solver_outcome_present") is not True
    ):
        return "BLOCKED"

    if effect.get("direction") not in {"higher", "lower"}:
        return "BLOCKED"
    if any(
        not isinstance(effect.get(field), (int, float))
        or isinstance(effect.get(field), bool)
        or effect[field] < 0
        for field in ("control_margin", "untouched_margin")
    ):
        return "BLOCKED"
    if any(
        not isinstance(effect.get(field), str)
        or not effect[field]
        or "MUST_BE_" in effect[field]
        for field in ("metric_id", "sampling_unit", "clustering_rule")
    ):
        return "BLOCKED"

    return gate["open_state"]


class Goal2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.frozen = frozen_fixture(cls.contract)

    def assert_blocked(self, contract=None, bundle=None):
        contract = self.frozen if contract is None else contract
        bundle = complete_bundle(contract) if bundle is None else bundle
        self.assertEqual(evidence_admission(contract, bundle, FIXTURE_KEY, EVALUATOR_KEY), "BLOCKED")

    def test_checked_in_contract_is_definition_only_and_blocked(self):
        self.assertEqual(self.contract["phase"], "CONTRACT_ONLY")
        self.assert_blocked(
            contract=self.contract, bundle=complete_bundle(self.frozen)
        )

    def test_pre_dispatch_opens_without_post_run_evidence(self):
        bundle = complete_bundle(self.frozen)
        for field in ("lineage", "cost_ledgers", "selection_ledgers", "evaluation_release", "components"):
            bundle.pop(field)
        self.assertEqual(pre_dispatch_admission(self.frozen, bundle, FIXTURE_KEY), "OPEN")

    def test_complete_authenticated_fixture_opens(self):
        self.assertEqual(
            evidence_admission(
                self.frozen, complete_bundle(self.frozen), FIXTURE_KEY, EVALUATOR_KEY
            ),
            "OPEN",
        )

    def test_forged_goal1_or_freeze_receipt_is_blocked(self):
        bundle = complete_bundle(self.frozen)
        bundle["goal1_receipt"]["signature"] = "0" * 64
        self.assert_blocked(bundle=bundle)
        bundle = complete_bundle(self.frozen)
        bundle["frozen_artifact_receipt"]["payload"][
            "selection_rule_sha256"
        ] = digest("substituted-rule")
        self.assert_blocked(bundle=bundle)

    def test_aliased_components_or_lineage_is_blocked(self):
        bundle = complete_bundle(self.frozen)
        bundle["components"]["memory_M1"]["component_id"] = bundle["components"][
            "memory_M0"
        ]["component_id"]
        self.assert_blocked(bundle=bundle)
        bundle = complete_bundle(self.frozen)
        bundle["lineage"]["treatment"]["payload"]["starting_memory_id"] = "other"
        bundle["lineage"]["treatment"] = signed(
            self.frozen["components"]["arm_lineage_schema"],
            bundle["lineage"]["treatment"]["payload"],
        )
        self.assert_blocked(bundle=bundle)

    def test_cross_arm_or_incomplete_cost_events_are_blocked(self):
        contract = copy.deepcopy(self.frozen)
        contract["complete_r_and_d_cost"]["expected_event_ids_by_arm"][
            "treatment"
        ] = list(
            contract["complete_r_and_d_cost"]["expected_event_ids_by_arm"]["control"]
        )
        bundle = complete_bundle(contract)
        self.assert_blocked(contract=contract, bundle=bundle)

        bundle = complete_bundle(self.frozen)
        payload = bundle["cost_ledgers"]["control"]["payload"]
        payload["observed_events"].pop()
        bundle["cost_ledgers"]["control"] = signed(
            self.frozen["complete_r_and_d_cost"]["cost_ledger_schema"], payload
        )
        self.assert_blocked(bundle=bundle)

    def test_early_evaluation_release_or_leakage_is_blocked(self):
        bundle = complete_bundle(self.frozen)
        payload = bundle["evaluation_release"]["payload"]
        payload["release_sequence"] = 11
        bundle["evaluation_release"] = signed(
            self.frozen["fresh_evaluation"]["evaluation_receipt_schema"], payload
        )
        self.assert_blocked(bundle=bundle)

        bundle = complete_bundle(self.frozen)
        payload = bundle["evaluation_release"]["payload"]
        payload["r_and_d_item_ids"].append("heldout-1")
        bundle["evaluation_release"] = signed(
            self.frozen["fresh_evaluation"]["evaluation_receipt_schema"], payload
        )
        self.assert_blocked(bundle=bundle)

    def test_successor_artifact_substitution_is_blocked(self):
        bundle = complete_bundle(self.frozen)
        bundle["components"]["improver_I1"]["artifact_sha256"] = digest("substituted-i1")
        self.assert_blocked(bundle=bundle)

    def test_independent_evaluator_key_is_required(self):
        bundle = complete_bundle(self.frozen)
        payload = bundle["evaluation_release"]["payload"]
        bundle["evaluation_release"] = signed(
            self.frozen["fresh_evaluation"]["evaluation_receipt_schema"], payload
        )
        self.assert_blocked(bundle=bundle)

    def test_selected_descendant_substitution_is_blocked(self):
        bundle = complete_bundle(self.frozen)
        payload = bundle["selection_ledgers"]["treatment"]["payload"]
        payload["selected_descendant_sha256"] = digest("substitute")
        bundle["selection_ledgers"]["treatment"] = signed(
            self.frozen["selection_and_sealing"]["selection_ledger_schema"], payload
        )
        self.assert_blocked(bundle=bundle)

    def test_caller_cannot_supply_or_remove_effect_target(self):
        for field in (
            "metric_id",
            "direction",
            "control_margin",
            "untouched_margin",
            "sampling_unit",
            "clustering_rule",
            "analysis_plan_sha256",
        ):
            contract = copy.deepcopy(self.frozen)
            contract["effect_target"][field] = "MUST_BE_FROZEN_BEFORE_OPEN"
            self.assert_blocked(
                contract=contract, bundle=complete_bundle(contract)
            )

    def test_terminal_states_are_exhaustive_and_credit_safe(self):
        rules = self.contract["decision_rules"]
        self.assertEqual(set(rules), {"BLOCKED", "INCOMPLETE", "PASS", "FAIL"})
        self.assertEqual(
            self.contract["decision_priority"],
            ["BLOCKED", "INCOMPLETE", "PASS", "FAIL"],
        )
        self.assertFalse(rules["BLOCKED"]["scientific_credit"])
        self.assertFalse(rules["INCOMPLETE"]["scientific_credit"])
        self.assertTrue(rules["PASS"]["scientific_credit"])
        self.assertTrue(rules["FAIL"]["scientific_credit"])


if __name__ == "__main__":
    unittest.main()
