from __future__ import annotations

import copy
import hashlib
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json"
BENCHMARK_PATH = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
RUNTIME_PATH = ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json"
BASELINES_PATH = ROOT / "goal1" / "CONFIRMATORY_BASELINES.json"
PRODUCT_CONTROLS_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
VERIFIED_CHAIN_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIED_CHAIN.json"
COST_POLICY_PATH = ROOT / "goal1" / "CONFIRMATORY_COST_POLICY.json"

EXPECTED_PROTOCOL_BLOB_SHA1 = "326c15d6d96d6e2d30f9d53826752304f89963ec"
EXPECTED_RULES_SHA256 = (
    "299bb7691f55ea05a86fa21cd03b7ae0a33885f8e1baf447918ba99e49c0043b"
)
EXPECTED_AUTHORITY_BLOBS = {
    "benchmark": "ade21f86d9566ce863ac09acd4c9103a48080ef4",
    "runtime": "1fa7caeefdf3c01cea5603f4865c2f9eec11a0fb",
    "baselines": "2ab23d89b22dd7c21963da1e3543bf2dc0e39193",
    "product_controls": "ab2b298e29022f931ec141bdba50485b0967ad3f",
    "verified_chain": "089592344a85509c5a587f2d77d37d3b7c825061",
    "cost_policy": "5659b1a6c2318c8ea16e4c609e29e2c8f9d86ec5",
}
AUTHORITY_PATHS = {
    "benchmark": BENCHMARK_PATH,
    "runtime": RUNTIME_PATH,
    "baselines": BASELINES_PATH,
    "product_controls": PRODUCT_CONTROLS_PATH,
    "verified_chain": VERIFIED_CHAIN_PATH,
    "cost_policy": COST_POLICY_PATH,
}
ARMS = [
    "ordinary",
    "portfolio",
    "product_only",
    "multi_fidelity",
    "verified_chain",
]
CONTRASTS = [
    "verified_chain_vs_ordinary",
    "verified_chain_vs_portfolio",
    "verified_chain_vs_product_only",
    "verified_chain_vs_multi_fidelity",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def family_id(problem_id: str) -> str:
    material = (
        b"supernova.source-problem-family.v1\0" + problem_id.encode("ascii")
    )
    return "sf1-" + hashlib.sha256(material).hexdigest()


def exact_two_sided_binomial_rejection_region(
    discordant_count: int, alpha: float
) -> tuple[int, int]:
    if discordant_count == 0:
        return -1, 1
    denominator = 2**discordant_count
    upper = 0
    high = discordant_count + 1
    for wins in range(discordant_count, discordant_count // 2, -1):
        upper += math.comb(discordant_count, wins)
        if 2 * upper / denominator > alpha:
            high = wins + 1
            break
        high = wins
    low = discordant_count - high
    return low, high


def binomial_probability(n: int, k: int, p: float) -> float:
    if p == 0.0:
        return 1.0 if k == 0 else 0.0
    if p == 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p**k) * ((1.0 - p) ** (n - k))


def unconditional_mcnemar_power(
    n: int, delta: float, discordance_probability: float, alpha: float
) -> float:
    q = discordance_probability
    conditional_win_probability = (q + delta) / (2.0 * q)
    total = 0.0
    for discordant_count in range(n + 1):
        discordant_mass = binomial_probability(n, discordant_count, q)
        if discordant_mass == 0.0:
            continue
        low, high = exact_two_sided_binomial_rejection_region(
            discordant_count, alpha
        )
        conditional_reject = 0.0
        if low >= 0:
            conditional_reject += sum(
                binomial_probability(
                    discordant_count, wins, conditional_win_probability
                )
                for wins in range(low + 1)
            )
        if high <= discordant_count:
            conditional_reject += sum(
                binomial_probability(
                    discordant_count, wins, conditional_win_probability
                )
                for wins in range(high, discordant_count + 1)
            )
        total += discordant_mass * conditional_reject
    return total


def validate_protocol(protocol: object, benchmark: dict) -> None:
    if not isinstance(protocol, dict):
        raise ValueError("protocol must be an object")
    if set(protocol) != {
        "schema_version",
        "protocol_id",
        "protocol_rules_status",
        "confirmatory_execution_status",
        "scientific_credit",
        "canonicalization",
        "sealed_rules",
        "sealed_rules_sha256",
        "execution_opening_gate",
        "mutation_policy",
    }:
        raise ValueError("top-level fields changed")
    if protocol["schema_version"] != 1:
        raise ValueError("schema version changed")
    if protocol["protocol_id"] != "goal1-confirmatory-protocol-rules-v1":
        raise ValueError("protocol identity changed")
    if protocol["protocol_rules_status"] != "SEALED":
        raise ValueError("protocol rules are not sealed")
    if protocol["confirmatory_execution_status"] != (
        "BLOCKED_NO_EXECUTION_AUTHORITY"
    ):
        raise ValueError("execution status changed")
    if protocol["scientific_credit"] != (
        "NONE_UNTIL_AN_ADMISSIBLE_EXECUTION_AUTHORITY_AND_MANIFEST_ARE_"
        "SEALED_AND_ONE_COMPLETE_COHORT_IS_EVALUATED"
    ):
        raise ValueError("scientific-credit boundary changed")
    if protocol["canonicalization"] != (
        "RFC8785_COMPATIBLE_UTF8_SORTED_KEYS_NO_INSIGNIFICANT_WHITESPACE_"
        "FOR_THIS_ASCII_DOMAIN"
    ):
        raise ValueError("canonicalization changed")

    rules = protocol["sealed_rules"]
    if rules["schema"] != "supernova.confirmatory-protocol-rules.v1":
        raise ValueError("sealed rules schema changed")
    if canonical_sha256(rules) != protocol["sealed_rules_sha256"]:
        raise ValueError("sealed rules digest mismatch")
    if protocol["sealed_rules_sha256"] != EXPECTED_RULES_SHA256:
        raise ValueError("sealed rules identity changed")

    authorities = rules["frozen_authorities"]
    for name, expected_blob in EXPECTED_AUTHORITY_BLOBS.items():
        if git_blob_sha1(canonical_lf(AUTHORITY_PATHS[name])) != expected_blob:
            raise ValueError(f"{name} bytes drifted")
        if authorities[name]["git_blob_sha1"] != expected_blob:
            raise ValueError(f"{name} authority binding changed")
    if authorities["product_controls"]["shared_surface_projection_sha256"] != (
        "f68045d4d9554b0639b4abae86658c6bacee3f29ef1cc4b5c4f5deac7b654ed7"
    ):
        raise ValueError("product-chain surface binding changed")

    population = benchmark["membership_proof_inputs"][
        "population_problem_ids_by_split"
    ]
    expected_development = sorted(population["development"])
    expected_report = sorted(population["report"])
    selection = rules["benchmark_selection"]
    if selection["development_split"] != {
        "source": "validation",
        "count": 244,
        "problem_ids": expected_development,
    }:
        raise ValueError("development selection changed")
    if selection["report_split"] != {
        "source": "test",
        "count": 244,
        "problem_ids": expected_report,
    }:
        raise ValueError("report selection changed")
    if selection["selection_rule"] != (
        "SELECT_ALL_ELIGIBLE_LOCKED_RECORDS_SORTED_BY_UNICODE_CODE_POINT"
    ):
        raise ValueError("selection rule changed")
    if selection["selection_time"] != (
        "BEFORE_EXECUTION_AUTHORITY_MANIFEST_OR_ANY_REPORT_DISPATCH"
    ):
        raise ValueError("selection timing changed")
    if selection["report_use"] != "ONE_SHOT_CONFIRMATORY_REPORT_ONLY":
        raise ValueError("report use changed")
    if selection["report_problem_bytes_release"] != (
        "ONLY_AFTER_PROTOCOL_RULES_EXECUTION_AUTHORITY_AND_MANIFEST_ARE_ALL_SEALED"
    ):
        raise ValueError("held-out release gate changed")
    if selection["post_selection_add_drop_replace_or_reorder"] != "BLOCKED":
        raise ValueError("post-selection mutation changed")

    family = rules["family_design"]
    expected_development_map = [
        {"problem_id": problem_id, "family_id": family_id(problem_id)}
        for problem_id in expected_development
    ]
    expected_report_map = [
        {"problem_id": problem_id, "family_id": family_id(problem_id)}
        for problem_id in expected_report
    ]
    if family["development_problem_family_map"] != expected_development_map:
        raise ValueError("development family map changed")
    if family["report_problem_family_map"] != expected_report_map:
        raise ValueError("report family map changed")
    all_family_ids = [
        item["family_id"]
        for item in expected_development_map + expected_report_map
    ]
    if len(set(all_family_ids)) != 488:
        raise ValueError("family overlap or collision")
    if family["at_most_one_selected_problem_per_family"] is not True:
        raise ValueError("family multiplicity changed")
    if family["family_overlap_between_development_and_report"] != "BLOCKED":
        raise ValueError("family overlap handling changed")
    if family["latent_dependence_claim"] != (
        "NONE_PROBLEM_ID_FAMILIES_ARE_AN_OPERATIONAL_CLUSTERING_RULE_NOT_"
        "PROOF_OF_STATISTICAL_INDEPENDENCE"
    ):
        raise ValueError("family limitation changed")

    power = rules["power_design"]
    if power != {
        "role": "PROSPECTIVE_DESIGN_SENSITIVITY_NOT_OBSERVED_EFFECT_ESTIMATE",
        "report_family_count": 244,
        "contrasts": 4,
        "familywise_alpha": 0.05,
        "conservative_local_alpha": 0.0125,
        "test": "EXACT_TWO_SIDED_CONDITIONAL_MCNEMAR_BINOMIAL",
        "target_absolute_paired_success_advantage": 0.27,
        "discordance_parameterization": "q=p10_plus_p01_p10_minus_p01_equals_0.27",
        "numeric_search_grid": {
            "q_min": 0.27,
            "q_max": 1.0,
            "points": 10001,
            "formula": (
                "q_i=0.27+0.73*i/10000_for_integer_i_0_through_10000"
            ),
        },
        "minimum_unconditional_power_on_frozen_grid": 0.9598828,
        "approximate_minimizer_q": 0.994306,
        "required_power_floor": 0.95,
        "continuum_minimum_claim": "NONE",
        "limitation": (
            "POWER_FLOOR_IS_VERIFIED_ON_THE_FROZEN_DISCORDANCE_GRID_AND_DOES_"
            "NOT_ESTABLISH_A_CONTINUUM_OR_JOINT_FOUR_CONTRAST_POWER_GUARANTEE"
        ),
        "smaller_effects": (
            "REPORT_AS_UNDERPOWERED_SENSITIVITY_NOT_AS_EVIDENCE_OF_NO_EFFECT"
        ),
    }:
        raise ValueError("power design changed")

    paired = rules["paired_design"]
    if paired["arms"] != ARMS:
        raise ValueError("arm order changed")
    if paired["attempts_per_problem_arm"] != 16:
        raise ValueError("attempt count changed")
    if paired["required_cells"] != 1220:
        raise ValueError("required cell count changed")
    if paired["required_model_call_slots"] != 19520:
        raise ValueError("required call-slot count changed")
    if paired["all_five_arms_required_for_every_report_problem"] is not True:
        raise ValueError("paired completeness changed")
    if paired["missing_blocked_or_incomplete_cell"] != (
        "WHOLE_CONFIRMATORY_COHORT_NOT_EVALUABLE"
    ):
        raise ValueError("missing-cell handling changed")
    if paired["no_pairwise_complete_case_deletion"] is not True:
        raise ValueError("pair deletion changed")

    schedule = rules["deterministic_schedule"]
    if schedule["canonical_arm_order"] != ARMS:
        raise ValueError("schedule arm order changed")
    if schedule["arm_order_per_problem"] != (
        "ROTATE_CANONICAL_ARM_ORDER_LEFT_BY_REPORT_PROBLEM_INDEX_MODULO_5"
    ):
        raise ValueError("counterbalancing changed")
    if schedule["attempt_order_per_arm"] != "ASCENDING_0_THROUGH_15":
        raise ValueError("attempt order changed")
    if schedule["scheduling_change_after_manifest"] != "BLOCKED":
        raise ValueError("schedule mutation changed")

    execution = rules["execution_interface"]
    if execution["authority_path"] != (
        "goal1/CONFIRMATORY_EXECUTION_AUTHORITY.json"
    ):
        raise ValueError("execution authority path changed")
    for field in {
        "protocol_rules_sha256",
        "exact_model_version",
        "generation_settings_sha256",
        "executor_artifact_sha256",
        "receipt_issuer_id",
        "receipt_verification_key_sha256",
        "provider_attested_fresh_empty_context_capability",
        "preflight_receipt_sha256",
        "preflight_validation_record_sha256",
        "scheduling_policy_sha256",
        "serving_pool_policy_sha256",
        "signature",
    }:
        if field not in set(execution["required_authority_bindings"]):
            raise ValueError("execution authority binding removed")
    if execution["recurring_chat_or_monitor_without_provider_attestation"] != (
        "NON_CREDIT_ONLY"
    ):
        raise ValueError("recurring-chat boundary changed")
    if execution["self_asserted_capability_alias_only_identity_or_simulation_issuer"] != (
        "BLOCKED"
    ):
        raise ValueError("self-asserted authority changed")
    if execution["authority_missing_invalid_or_digest_mismatch"] != (
        "BLOCKED_NO_EXECUTION_AUTHORITY"
    ):
        raise ValueError("execution opening gate changed")
    if execution["rule_mutation_when_binding_authority"] != "BLOCKED":
        raise ValueError("late-binding mutation changed")

    manifest = rules["confirmatory_manifest_interface"]
    if manifest["required_schema"] != "supernova.confirmatory-manifest.v1":
        raise ValueError("manifest schema changed")
    if manifest["seal_before"] != (
        "REPORT_BYTES_RELEASE_OR_FIRST_CONFIRMATORY_DISPATCH_WHICHEVER_WOULD_OCCUR_FIRST"
    ):
        raise ValueError("manifest seal timing changed")
    if manifest["missing_invalid_or_post_dispatch_mutation"] != "BLOCKED":
        raise ValueError("manifest mutation changed")

    analysis = rules["analysis"]
    if analysis["primary_contrasts"] != CONTRASTS:
        raise ValueError("contrast family changed")
    if analysis["test"] != (
        "EXACT_TWO_SIDED_MCNEMAR_PER_CONTRAST_ON_THE_244_PAIRED_REPORT_FAMILIES"
    ):
        raise ValueError("paired test changed")
    if analysis["multiplicity"] != (
        "HOLM_STEP_DOWN_FAMILYWISE_ALPHA_0.05_SORT_P_ASCENDING_"
        "TIE_BY_CONTROL_NAME_ASCENDING"
    ):
        raise ValueError("multiplicity rule changed")
    if analysis["pass"] != (
        "ALL_FOUR_CONTRASTS_HAVE_POSITIVE_PAIRED_DIFFERENCE_AND_"
        "HOLM_REJECT_THEIR_NULLS"
    ):
        raise ValueError("PASS rule changed")
    if analysis["decision_priority"] != ["BLOCKED", "INCOMPLETE", "PASS", "FAIL"]:
        raise ValueError("decision priority changed")
    if analysis["no_human_adjudication_or_posthoc_exclusion"] is not True:
        raise ValueError("posthoc adjudication changed")

    opening = protocol["execution_opening_gate"]
    if opening != {
        "state": "BLOCKED_NO_EXECUTION_AUTHORITY",
        "missing_artifact": "goal1/CONFIRMATORY_EXECUTION_AUTHORITY.json",
        "transition": (
            "ONLY_AN_ADMISSIBLE_IMMUTABLE_AUTHORITY_BUNDLE_VALIDATED_AGAINST_"
            "THE_SEALED_RULES_DIGEST_CAN_OPEN_CONFIRMATORY_DISPATCH"
        ),
        "non_credit_simulation": (
            "ALLOWED_FOR_ENGINEERING_ONLY_WITH_ISSUER_ID_NON_CREDIT_SIMULATION"
        ),
        "simulation_recurring_chat_or_self_asserted_receipt_for_scientific_credit": (
            "BLOCKED"
        ),
        "held_out_report_dispatch": "BLOCKED",
    }:
        raise ValueError("execution opening gate changed")
    if protocol["mutation_policy"] != {
        "sealed_rules_or_digest_change": (
            "NEW_PROTOCOL_VERSION_REQUIRED_AND_ANY_EXISTING_MANIFEST_INVALID"
        ),
        "execution_authority_binding": "MUST_NOT_MUTATE_SEALED_RULES",
        "unknown_field_missing_binding_or_unresolved_input": "BLOCKED",
    }:
        raise ValueError("mutation policy changed")


class ConfirmatoryProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = load(PROTOCOL_PATH)
        cls.benchmark = load(BENCHMARK_PATH)

    def test_checked_in_bytes_are_frozen_cross_platform(self) -> None:
        self.assertEqual(
            EXPECTED_PROTOCOL_BLOB_SHA1,
            git_blob_sha1(canonical_lf(PROTOCOL_PATH)),
        )

    def test_protocol_rules_are_closed(self) -> None:
        validate_protocol(self.protocol, self.benchmark)

    def test_all_244_report_items_have_unique_operational_families(self) -> None:
        family_map = self.protocol["sealed_rules"]["family_design"][
            "report_problem_family_map"
        ]
        self.assertEqual(244, len(family_map))
        self.assertEqual(244, len({item["family_id"] for item in family_map}))

    def test_power_at_frozen_grid_minimizer_exceeds_floor(self) -> None:
        power = unconditional_mcnemar_power(
            n=244,
            delta=0.27,
            discordance_probability=0.994306,
            alpha=0.0125,
        )
        self.assertGreaterEqual(power, 0.9598)
        self.assertLess(power, 0.9600)
        self.assertGreaterEqual(
            power,
            self.protocol["sealed_rules"]["power_design"][
                "required_power_floor"
            ],
        )

    def test_selection_cannot_drop_or_replace_a_report_problem(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["benchmark_selection"]["report_split"][
            "problem_ids"
        ].pop()
        with self.assertRaisesRegex(ValueError, "sealed rules digest mismatch"):
            validate_protocol(changed, self.benchmark)

    def test_family_collision_or_cross_split_reuse_is_rejected(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["family_design"]["report_problem_family_map"][1][
            "family_id"
        ] = changed["sealed_rules"]["family_design"][
            "report_problem_family_map"
        ][0]["family_id"]
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "report family map"):
            validate_protocol(changed, self.benchmark)

    def test_recurring_chat_or_simulation_cannot_open_scientific_dispatch(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["execution_interface"][
            "recurring_chat_or_monitor_without_provider_attestation"
        ] = "ADMISSIBLE"
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "recurring-chat boundary"):
            validate_protocol(changed, self.benchmark)
        changed = copy.deepcopy(self.protocol)
        changed["execution_opening_gate"]["held_out_report_dispatch"] = "OPEN"
        with self.assertRaisesRegex(ValueError, "execution opening gate"):
            validate_protocol(changed, self.benchmark)

    def test_execution_authority_cannot_omit_identity_or_preflight(self) -> None:
        for field in [
            "exact_model_version",
            "executor_artifact_sha256",
            "receipt_verification_key_sha256",
            "preflight_receipt_sha256",
            "serving_pool_policy_sha256",
        ]:
            changed = copy.deepcopy(self.protocol)
            changed["sealed_rules"]["execution_interface"][
                "required_authority_bindings"
            ].remove(field)
            changed["sealed_rules_sha256"] = canonical_sha256(
                changed["sealed_rules"]
            )
            with self.assertRaisesRegex(ValueError, "authority binding"):
                validate_protocol(changed, self.benchmark)

    def test_incomplete_pair_or_posthoc_exclusion_cannot_become_a_result(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["paired_design"][
            "missing_blocked_or_incomplete_cell"
        ] = "DROP_PAIR"
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "missing-cell handling"):
            validate_protocol(changed, self.benchmark)
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["analysis"][
            "no_human_adjudication_or_posthoc_exclusion"
        ] = False
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "posthoc adjudication"):
            validate_protocol(changed, self.benchmark)

    def test_holm_and_all_four_positive_contrasts_are_required(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["analysis"]["multiplicity"] = "NONE"
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "multiplicity rule"):
            validate_protocol(changed, self.benchmark)
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["analysis"]["pass"] = "ANY_ONE_CONTRAST"
        changed["sealed_rules_sha256"] = canonical_sha256(changed["sealed_rules"])
        with self.assertRaisesRegex(ValueError, "PASS rule"):
            validate_protocol(changed, self.benchmark)


if __name__ == "__main__":
    unittest.main()
