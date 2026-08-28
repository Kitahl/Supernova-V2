from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.contracts import Arm, CONTROL_ARMS


CONTRACT_HEADING = "## Machine-readable analysis contract"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis_id",
    "candidate_arm",
    "control_arms",
    "sampling",
    "estimand",
    "cost_construct",
    "primary_analysis",
    "incomplete_runs",
    "pilot",
    "power_update",
    "look_rule",
    "provenance_gates",
    "forbidden_claims",
}


def load_contract(document: str) -> dict[str, object]:
    if document.count(CONTRACT_HEADING) != 1:
        raise ValueError("analysis plan must contain exactly one contract heading")
    tail = document.split(CONTRACT_HEADING, 1)[1]
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", tail, flags=re.DOTALL)
    if len(blocks) != 1:
        raise ValueError("analysis plan must contain exactly one JSON contract")
    value = json.loads(blocks[0])
    if type(value) is not dict:
        raise ValueError("analysis contract must be a JSON object")
    return value


class AnalysisPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = (ROOT / "docs" / "ANALYSIS_PLAN.md").read_text(
            encoding="utf-8"
        )
        cls.contract = load_contract(cls.document)
        cls.goal = json.loads(
            (ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8")
        )
        cls.protocol = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(
                encoding="utf-8"
            )
        )

    def test_contract_has_one_versioned_closed_topology(self) -> None:
        self.assertEqual(EXPECTED_TOP_LEVEL_KEYS, set(self.contract))
        self.assertEqual(1, self.contract["schema_version"])
        self.assertEqual(
            "goal1-confirmatory-primary-v1", self.contract["analysis_id"]
        )

    def test_candidate_controls_and_pairing_match_live_goal_contract(self) -> None:
        expected_controls = [arm.value for arm in CONTROL_ARMS]
        self.assertEqual(Arm.VERIFIED_CHAIN.value, self.contract["candidate_arm"])
        self.assertEqual(expected_controls, self.contract["control_arms"])
        self.assertEqual(
            [*expected_controls, Arm.VERIFIED_CHAIN.value],
            self.protocol["sealed_rules"]["paired_design"]["arms"],
        )
        sampling = self.contract["sampling"]
        self.assertEqual("problem", sampling["unit"])
        self.assertEqual("problem_id", sampling["pairing_key"])
        self.assertEqual(len(tuple(Arm)), sampling["required_arms_per_unit"])
        self.assertEqual(1, sampling["replicates_per_problem_arm"])
        self.assertIn("FAMILY_IDS", sampling["family_independence_gate"])

    def test_primary_estimand_is_package_effect_not_pure_gating(self) -> None:
        estimand = self.contract["estimand"]
        self.assertEqual(
            "verified_gated_search_and_consumption_package_effect",
            estimand["name"],
        )
        self.assertEqual(
            "final_independently_kernel_verified_solve", estimand["outcome"]
        )
        self.assertEqual("Delta_c=(W_c-L_c)/N", estimand["effect_per_control"])
        self.assertEqual(
            "FORBIDDEN_UNTIL_RETRY_AND_FEEDBACK_POLICY_ARE_MATCHED",
            estimand["pure_gating_claim"],
        )

    def test_primary_test_matches_statistics_and_evaluator_rule(self) -> None:
        primary = self.contract["primary_analysis"]
        self.assertEqual(
            "mcnemar_exact_two_sided(W_c,L_c)", primary["test"]
        )
        self.assertEqual("W_c>L_c", primary["direction"])
        self.assertEqual(
            "holm_step_down(four_control_p_values,familywise_alpha)",
            primary["multiplicity"],
        )
        self.assertEqual(
            "ExperimentSpec.familywise_alpha", primary["alpha_source"]
        )
        self.assertEqual(
            "FOR_EVERY_CONTROL: W_c>L_c AND holm_rejects_null_c",
            primary["pass_rule"],
        )
        self.assertEqual(
            0.05,
            self.protocol["sealed_rules"]["power_design"]["familywise_alpha"],
        )

    def test_incomplete_cells_and_post_terminal_reruns_cannot_be_analyzed(self) -> None:
        incomplete = self.contract["incomplete_runs"]
        self.assertEqual(
            "EVERY_FROZEN_PROBLEM_X_ALL_FIVE_ARMS", incomplete["required_cells"]
        )
        self.assertEqual(
            "INCOMPLETE_NO_PRIMARY_HYPOTHESIS_TEST", incomplete["missing_cell"]
        )
        self.assertIn("PREREGISTERED_SYMMETRIC", incomplete["retry_policy"])
        self.assertEqual(
            "FORBIDDEN_FOR_THE_PRIMARY_ANALYSIS_ID",
            incomplete["post_terminal_rerun"],
        )
        self.assertEqual(
            "FORBIDDEN_AFTER_MANIFEST_FREEZE", incomplete["replacement_problem"]
        )

    def test_cost_claim_is_a_common_ceiling_not_realized_compute_parity(self) -> None:
        cost = self.contract["cost_construct"]
        self.assertEqual(
            "COMMON_FROZEN_COMPLETE_COST_CEILING_WITH_SYMMETRIC_ACCOUNTING",
            cost["primary"],
        )
        self.assertIn("CHARGE_ALL_ATTEMPTS", cost["required_policy"])
        self.assertEqual(
            "DOES_NOT_ESTABLISH_EQUAL_REALIZED_OR_PHYSICAL_COMPUTE",
            cost["non_claim"],
        )

    def test_pilot_is_non_credit_and_power_reads_discordance_not_direction(self) -> None:
        pilot = self.contract["pilot"]
        self.assertEqual(
            "NON_CREDIT_FEASIBILITY_AND_DISCORDANCE_ESTIMATION_ONLY",
            pilot["role"],
        )
        self.assertIs(False, pilot["may_enter_confirmatory_test"])
        self.assertIs(False, pilot["may_choose_confirmatory_items_prompts_or_arms"])
        self.assertEqual(
            [
                "pilot_problem_count",
                "total_discordance_D_c=W_c+L_c_for_each_control",
            ],
            pilot["permitted_power_inputs"],
        )
        self.assertEqual(
            {"pilot_win_direction", "pilot_p_values", "confirmatory_outcomes"},
            set(pilot["forbidden_power_inputs"]),
        )

    def test_power_update_is_prospective_conservative_and_bounded(self) -> None:
        power = self.contract["power_update"]
        self.assertEqual(0.8, power["familywise_target_power"])
        self.assertEqual(
            "BEFORE_PILOT_OUTCOME_INSPECTION", power["effect_target_freeze"]
        )
        self.assertEqual("d_c=D_c/N_pilot", power["discordance_rate"])
        self.assertEqual(
            "q_c=(d_c+delta_c)/(2*d_c)",
            power["conditional_win_probability"],
        )
        self.assertEqual("0<delta_c<d_c<=1", power["validity_condition"])
        self.assertEqual(
            "familywise_alpha/4", power["planning_alpha_per_contrast"]
        )
        self.assertEqual(
            "(1-familywise_target_power)/4",
            power["planning_beta_per_contrast"],
        )
        self.assertEqual(0.95, power["per_contrast_target_power"])
        self.assertIn("EXACT_UNCONDITIONAL_MCNEMAR_POWER", power["algorithm"])
        self.assertIn("mcnemar_exact_two_sided", power["exact_power_sum"])
        self.assertIn("BLOCK_SAMPLE_SIZE_FREEZE", power["zero_or_invalid_discordance"])

        # The frozen formula has the required paired-distribution interpretation.
        d_c = 0.2
        delta_c = 0.1
        q_c = (d_c + delta_c) / (2 * d_c)
        self.assertAlmostEqual(0.75, q_c)
        self.assertGreater(q_c, 0.5)
        self.assertLess(q_c, 1.0)

    def test_one_shot_rule_and_external_gates_are_explicit(self) -> None:
        look = self.contract["look_rule"]
        self.assertEqual(1, look["primary_invocations"])
        self.assertEqual("ONE_SHOT_FIXED_HORIZON", look["horizon"])
        self.assertIn("CONTENT_ADDRESSED", look["input"])
        self.assertIn("SEQUENTIAL_ERROR_CONTROL", look["additional_outcome_bearing_look"])

        expected_gates = {
            "pinned_benchmark_root_and_protected_confirmatory_split",
            "frozen_complete_cost_model_usage_basis_and_residual_policy",
            "content_bound_common_input_arm_delta_model_and_runtime",
            "trusted_predispatch_cost_and_outcome_join",
            "final_kernel_verifier_receipt_bound_to_the_scored_artifact",
            "complete_five_arm_cells_for_every_frozen_problem",
            "independent_sampling_unit_or_a_versioned_cluster_aware_plan",
        }
        self.assertEqual(expected_gates, set(self.contract["provenance_gates"]))

    def test_document_forbids_overclaiming_and_is_superseded_non_authority(self) -> None:
        expected_forbidden = {
            "pilot_establishes_goal1_superiority",
            "pure_verification_gating_effect",
            "equal_realized_or_physical_compute",
            "contamination_free_generalization",
            "evaluator_pass_alone_establishes_science",
            "incomplete_cohort_supports_primary_inference",
        }
        self.assertEqual(expected_forbidden, set(self.contract["forbidden_claims"]))
        active = self.goal["active_experiment"]
        self.assertEqual("CONFIRMATORY_PREEXECUTION", active["phase"])
        self.assertEqual("SEALED", active["protocol_rules_status"])
        self.assertEqual(
            "BLOCKED_NO_EXECUTION_AUTHORITY",
            active["confirmatory_execution_status"],
        )
        self.assertIn("SUPERSEDED_NON_AUTHORITY", self.document)
        self.assertIn("do not certify", self.document)
        self.assertIn("zero scientific credit", self.document)


if __name__ == "__main__":
    unittest.main()
