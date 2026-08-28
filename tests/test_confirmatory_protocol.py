from __future__ import annotations

import copy
import hashlib
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json"
GOAL1_PATH = ROOT / "goal1" / "GOAL1.json"
BENCHMARK_PATH = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
RUNTIME_PATH = ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json"
BASELINES_PATH = ROOT / "goal1" / "CONFIRMATORY_BASELINES.json"
PRODUCT_CONTROLS_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
VERIFIED_CHAIN_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIED_CHAIN.json"
COST_POLICY_PATH = ROOT / "goal1" / "CONFIRMATORY_COST_POLICY.json"

EXPECTED_PROTOCOL_BLOB_SHA1 = "65d65e36a32aa1a73de44b1d2443c9587a14dacb"
EXPECTED_GOAL1_BLOB_SHA1 = "e38f722ddcd3464095423d2ed91001e961626934"
EXPECTED_RULES_SHA256 = (
    "f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9"
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


def source_family_key(problem_id: str) -> str:
    parts = problem_id.rsplit("_", 1)
    if (
        len(parts) == 2
        and parts[1].isdigit()
        and "_p" in parts[0]
        and parts[0].rsplit("_p", 1)[1].isdigit()
    ):
        return parts[0]
    return problem_id


def family_id(problem_id: str) -> str:
    source_key = source_family_key(problem_id)
    material = (
        b"supernova.source-problem-family.v2\0" + source_key.encode("ascii")
    )
    return "sf2-" + hashlib.sha256(material).hexdigest()


def binomial_coefficients(n: int) -> list[list[float]]:
    coefficients = [[0.0] * (n + 1) for _ in range(n + 1)]
    for row in range(n + 1):
        coefficients[row][0] = 1.0
        coefficients[row][row] = 1.0
        for column in range(1, row):
            coefficients[row][column] = (
                coefficients[row - 1][column - 1]
                + coefficients[row - 1][column]
            )
    return coefficients


BINOMIAL = binomial_coefficients(244)


def upper_rejection_threshold(discordant_count: int, alpha: float) -> int:
    if discordant_count == 0:
        return 1
    denominator = 2.0**discordant_count
    upper = 0.0
    high = discordant_count + 1
    for wins in range(discordant_count, discordant_count // 2, -1):
        upper += BINOMIAL[discordant_count][wins]
        if 2.0 * upper / denominator > alpha:
            return wins + 1
        high = wins
    return high


def binomial_probability(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p == 0.0:
        return 1.0 if k == 0 else 0.0
    if p == 1.0:
        return 1.0 if k == n else 0.0
    return BINOMIAL[n][k] * (p**k) * ((1.0 - p) ** (n - k))


def directional_unconditional_mcnemar_power(
    n: int, delta: float, discordance_probability: float, alpha: float
) -> float:
    q = discordance_probability
    conditional_win_probability = (q + delta) / (2.0 * q)
    thresholds = [upper_rejection_threshold(m, alpha) for m in range(n + 1)]
    upper_tails = [0.0] * (n + 1)
    for m in range(n):
        threshold = thresholds[m]
        next_threshold = thresholds[m + 1]
        if next_threshold > m + 1:
            upper_tails[m + 1] = 0.0
            continue
        tail = upper_tails[m] + conditional_win_probability * binomial_probability(
            m, threshold - 1, conditional_win_probability
        )
        if next_threshold > threshold:
            tail -= sum(
                binomial_probability(m + 1, wins, conditional_win_probability)
                for wins in range(threshold, next_threshold)
            )
        elif next_threshold < threshold:
            tail += sum(
                binomial_probability(m + 1, wins, conditional_win_probability)
                for wins in range(next_threshold, threshold)
            )
        upper_tails[m + 1] = tail
    return sum(
        binomial_probability(n, m, q) * upper_tails[m]
        for m in range(n + 1)
    )


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
        {
            "problem_id": problem_id,
            "source_family_key": source_family_key(problem_id),
            "family_id": family_id(problem_id),
        }
        for problem_id in expected_development
    ]
    expected_report_map = [
        {
            "problem_id": problem_id,
            "source_family_key": source_family_key(problem_id),
            "family_id": family_id(problem_id),
        }
        for problem_id in expected_report
    ]
    if family["development_problem_family_map"] != expected_development_map:
        raise ValueError("development family map changed")
    if family["report_problem_family_map"] != expected_report_map:
        raise ValueError("report family map changed")
    development_family_ids = {
        item["family_id"] for item in expected_development_map
    }
    report_family_ids = {item["family_id"] for item in expected_report_map}
    if len(development_family_ids) != 243 or len(report_family_ids) != 244:
        raise ValueError("family counts changed")
    if development_family_ids & report_family_ids:
        raise ValueError("family overlap or collision")
    if family["development_family_count"] != 243:
        raise ValueError("development family count changed")
    if family["report_family_count"] != 244:
        raise ValueError("report family count changed")
    if family["known_grouped_variants"] != [{
        "source_family_key": "imo_1964_p1",
        "problem_ids": ["imo_1964_p1_1", "imo_1964_p1_2"],
    }]:
        raise ValueError("known variant grouping changed")
    if family["at_most_one_selected_report_problem_per_family"] is not True:
        raise ValueError("report family multiplicity changed")
    if family["development_family_duplicates_allowed_for_noncredit_development"] is not True:
        raise ValueError("development family policy changed")
    if family["family_overlap_between_development_and_report"] != "BLOCKED":
        raise ValueError("family overlap handling changed")
    if family["family_rule_id"] != "source-lineage-key-sha256-v2":
        raise ValueError("family rule identity changed")
    if family["latent_dependence_claim"] != (
        "NONE_THIS_SOURCE_LINEAGE_RULE_GROUPS_EXPLICIT_VARIANTS_BUT_DOES_NOT_"
        "PROVE_ABSENCE_OF_UNENCODED_TEMPLATE_DEPENDENCE"
    ):
        raise ValueError("family limitation changed")
    if family["newly_discovered_lineage_after_seal"] != (
        "BLOCK_CONFIRMATORY_DISPATCH_AND_VERSION_THE_PROTOCOL_BEFORE_REPORT_USE"
    ):
        raise ValueError("new lineage handling changed")

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
        "rejection_event": (
            "UPPER_TAIL_ONLY_VERIFIED_CHAIN_WINS_GREATER_THAN_LOSSES_AND_TWO_"
            "SIDED_EXACT_P_AT_MOST_LOCAL_ALPHA"
        ),
        "grid_verification": (
            "ENUMERATE_ALL_10001_FROZEN_Q_POINTS_AND_ASSERT_THE_RECORDED_"
            "MINIMUM_INDEX_Q_AND_POWER"
        ),
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
        "goal1_authority_sha256",
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
    if execution["goal1_authority"] != {
        "path": "goal1/GOAL1.json",
        "digest_binding": "CANONICAL_JSON_SHA256",
        "required_schema_version": 2,
        "required_authority_id": "goal1-active-authority-v2",
        "required_experiment_id": "goal1-confirmatory-v1",
        "required_phase": "CONFIRMATORY_PREEXECUTION",
        "required_protocol_rules_status": "SEALED",
        "required_confirmatory_execution_status": "BLOCKED_NO_EXECUTION_AUTHORITY",
        "required_benchmark_frozen": True,
        "required_complete_cost_policy_frozen": True,
        "required_held_out_dispatch": "BLOCKED",
        "stale_bootstrap_dry_run_authority": "BLOCKED",
    }:
        raise ValueError("Goal-1 authority gate changed")

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
    goal1 = load(GOAL1_PATH)
    if git_blob_sha1(canonical_lf(GOAL1_PATH)) != EXPECTED_GOAL1_BLOB_SHA1:
        raise ValueError("Goal-1 authority bytes drifted")
    if set(goal1) != {
        "schema_version", "authority_id", "goal", "hypothesis",
        "active_experiment", "authority_hierarchy", "scientific_credit",
        "terminal_decisions", "goal2_gate", "legacy_dry_run",
    }:
        raise ValueError("Goal-1 authority fields changed")
    active = goal1["active_experiment"]
    if goal1["schema_version"] != 2 or goal1["authority_id"] != "goal1-active-authority-v2":
        raise ValueError("Goal-1 authority identity changed")
    if active != {
        "experiment_id": "goal1-confirmatory-v1",
        "phase": "CONFIRMATORY_PREEXECUTION",
        "protocol_path": "goal1/CONFIRMATORY_PROTOCOL.json",
        "protocol_rules_sha256": EXPECTED_RULES_SHA256,
        "protocol_rules_status": "SEALED",
        "benchmark_frozen": True,
        "complete_cost_policy_frozen": True,
        "execution_authority_path": "goal1/CONFIRMATORY_EXECUTION_AUTHORITY.json",
        "confirmatory_execution_status": "BLOCKED_NO_EXECUTION_AUTHORITY",
        "manifest_schema": "supernova.confirmatory-manifest.v1",
        "held_out_dispatch": "BLOCKED",
    }:
        raise ValueError("Goal-1 active experiment changed")
    if goal1["authority_hierarchy"]["legacy_dry_run"] != "NON_CREDIT_EXAMPLE_ONLY":
        raise ValueError("legacy dry-run authority changed")
    if goal1["goal2_gate"] != {
        "requires_valid_goal1_pass": True,
        "current_state": "BLOCKED_PENDING_VALID_GOAL1_PASS",
    }:
        raise ValueError("Goal-2 gate changed")

    if protocol["mutation_policy"] != {
        "sealed_rules_or_digest_change": (
            "NEW_PROTOCOL_VERSION_REQUIRED_AND_ANY_EXISTING_MANIFEST_INVALID"
        ),
        "execution_authority_binding": "MUST_NOT_MUTATE_SEALED_RULES",
        "unknown_field_missing_binding_or_unresolved_input": "BLOCKED",
    }:
        raise ValueError("mutation policy changed")

    # Semantic checks above provide precise fail-closed diagnostics.  This
    # immutable identity check remains the final catch-all for any otherwise
    # unrecognised mutation to the sealed rules.
    if protocol["sealed_rules_sha256"] != EXPECTED_RULES_SHA256:
        raise ValueError("sealed rules identity changed")


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

    def test_all_10001_power_grid_points_and_directional_minimum(self) -> None:
        powers = []
        for index in range(10001):
            q = 0.27 + 0.73 * index / 10000
            powers.append(
                directional_unconditional_mcnemar_power(
                    n=244,
                    delta=0.27,
                    discordance_probability=q,
                    alpha=0.0125,
                )
            )
        minimum_index = min(range(len(powers)), key=powers.__getitem__)
        minimum_q = 0.27 + 0.73 * minimum_index / 10000
        minimum_power = powers[minimum_index]
        self.assertEqual(9922, minimum_index)
        self.assertAlmostEqual(0.994306, minimum_q, places=12)
        self.assertAlmostEqual(0.9598828728, minimum_power, places=10)
        self.assertGreaterEqual(
            minimum_power,
            self.protocol["sealed_rules"]["power_design"][
                "required_power_floor"
            ],
        )

    def test_explicit_contest_variants_share_one_development_family(self) -> None:
        family_map = self.protocol["sealed_rules"]["family_design"][
            "development_problem_family_map"
        ]
        by_problem = {item["problem_id"]: item for item in family_map}
        self.assertEqual(
            "imo_1964_p1", by_problem["imo_1964_p1_1"]["source_family_key"]
        )
        self.assertEqual(
            by_problem["imo_1964_p1_1"]["family_id"],
            by_problem["imo_1964_p1_2"]["family_id"],
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
