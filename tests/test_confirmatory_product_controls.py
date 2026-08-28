from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
EXPECTED_GIT_BLOB_SHA1 = "eeceab0d674529123db1d91f8ce8ff60422264d0"
EXPECTED_RECEIPT_FIELDS = [
    "receipt_schema",
    "issuer_id",
    "model_identity_sha256",
    "context_id",
    "problem_id",
    "arm",
    "attempt_index",
    "initial_context_sha256",
    "request_artifact_sha256",
    "response_artifact_sha256",
    "opened_at",
    "closed_at",
    "signature",
]
EXPECTED_PRODUCT_GRAPH = [
    {
        "attempt_index": index,
        "predecessor_completion_of_attempt": None if index == 0 else index - 1,
    }
    for index in range(16)
]
EXPECTED_PROMOTION_GRAPH = [
    {"attempt_index": 8, "eligible_predecessor_attempts": [0, 1]},
    {"attempt_index": 9, "eligible_predecessor_attempts": [2, 3]},
    {"attempt_index": 10, "eligible_predecessor_attempts": [4, 5]},
    {"attempt_index": 11, "eligible_predecessor_attempts": [6, 7]},
    {"attempt_index": 12, "eligible_predecessor_attempts": [8, 9]},
    {"attempt_index": 13, "eligible_predecessor_attempts": [10, 11]},
    {"attempt_index": 14, "eligible_predecessor_attempts": [12, 13]},
    {"attempt_index": 15, "eligible_predecessor_attempts": [14]},
]
EXPECTED_STAGE_SHAPE = [
    ("S0", 0, list(range(8)), 2048),
    ("S1", 1, list(range(8, 12)), 4096),
    ("S2", 2, [12, 13], 8192),
    ("S3", 3, [14], 16384),
    ("S4", 4, [15], 32768),
]
EXPECTED_PRODUCT_PREFIX = [
    "-- supernova-kind: PRODUCT_CANDIDATE",
    "-- supernova-schema: supernova.product-candidate-emission.v1",
]
EXPECTED_ANSWER_PREFIX = [
    "-- supernova-kind: FINAL_ANSWER",
    "-- supernova-schema: supernova.final-answer-emission.v1",
]
EXPECTED_NO_ANSWER = (
    "-- supernova-kind: NO_ANSWER\n"
    "-- supernova-schema: supernova.no-answer-emission.v1\n"
)


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def validate_contract(contract: object) -> None:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if set(contract) != {
        "schema_version",
        "contract_id",
        "status",
        "scope",
        "estimand_role",
        "scientific_credit",
        "shared",
        "arms",
        "cost_event_contract",
        "terminal_decision_priority",
    }:
        raise ValueError("top-level fields changed")
    if contract["schema_version"] != 1:
        raise ValueError("schema version changed")
    if contract["contract_id"] != "goal1-confirmatory-product-controls-v1":
        raise ValueError("contract identity changed")
    if contract["status"] != "FROZEN":
        raise ValueError("contract is not frozen")
    if contract["scope"] != ["product_only", "multi_fidelity"]:
        raise ValueError("scope changed")
    if contract["estimand_role"] != "CONTROL_ARM_DEFINITIONS_ONLY":
        raise ValueError("estimand role changed")
    if contract["scientific_credit"] != (
        "NONE_UNTIL_SEALED_BY_CONFIRMATORY_PROTOCOL_AND_EXECUTED_WITH_ADMISSIBLE_EVIDENCE"
    ):
        raise ValueError("scientific-credit boundary changed")

    shared = contract["shared"]
    if shared["attempts_per_problem_arm"] != 16:
        raise ValueError("attempt quota changed")
    if shared["attempt_indices"] != list(range(16)):
        raise ValueError("attempt indices changed")
    if shared["dispatch_policy"] != (
        "REGISTER_ALL_16_SLOTS_AND_THE_EXACT_PREDECESSOR_GRAPH_BEFORE_FIRST_MODEL_CALL"
    ):
        raise ValueError("dispatch policy changed")
    if shared["completion_policy"] != (
        "EXECUTE_AND_RECONCILE_ALL_16_EVEN_AFTER_A_VERIFIED_PASS"
    ):
        raise ValueError("completion policy changed")
    if shared["capability_surface"] != {
        "model_provider_version_reasoning_mode_tools_and_generation_settings": (
            "MUST_MATCH_ACROSS_ALL_FIVE_ARMS_EXCEPT_THE_FROZEN_MULTI_FIDELITY_VISIBLE_OUTPUT_CAP_AND_BE_BOUND_BY_THE_SHARED_CONFIRMATORY_PROTOCOL"
        ),
        "external_tools": "FORBIDDEN",
        "network_during_model_attempt": "FORBIDDEN",
        "model_visible_input": (
            "EXACT_SHARED_PRODUCT_CHAIN_PROMPT_TEMPLATE_PLUS_ONLY_THE_ARM_LOCAL_ADMITTED_PRODUCTS_ALLOWED_BELOW"
        ),
    }:
        raise ValueError("capability surface changed")
    if shared["prompt_base_authority"] != {
        "path": "goal1/CONFIRMATORY_BASELINES.json",
        "git_blob_sha1": "2ab23d89b22dd7c21963da1e3543bf2dc0e39193",
        "source_fields": [
            "SOURCE_SHA256",
            "THEOREM_NAME",
            "SOURCE_WITH_PROOF_HOLE",
        ],
        "rule": (
            "REUSE_EXACT_FROZEN_SOURCE_BINDINGS_BUT_USE_THE_SHARED_PRODUCT_CHAIN_TEMPLATE_BELOW"
        ),
    }:
        raise ValueError("prompt authority changed")
    template = shared["product_chain_prompt_template"]
    for required in [
        "MODEL_VISIBLE_ARM_LABEL=product_chain",
        "REQUIRED_PRODUCT_DECLARATION_NAME={PRODUCT_DECLARATION_NAME}",
        "BEGIN_FROZEN_SOURCE",
        "{SOURCE_WITH_PROOF_HOLE}",
        "BEGIN_ADMITTED_PRODUCTS",
        "{ORDERED_ADMITTED_PRODUCT_RESPONSE_BYTES}",
    ]:
        if required not in template:
            raise ValueError("shared product-chain prompt changed")
    if "VERIFIED" in template:
        raise ValueError("model-visible prompt leaks admission mode")

    isolation = shared["context_isolation_declaration"]
    if isolation["required_scope"] != "ONE_NEW_EMPTY_MODEL_CONTEXT_PER_PROBLEM_ARM_ATTEMPT":
        raise ValueError("fresh-context scope changed")
    if isolation["hidden_predecessor_context"] != "FORBIDDEN":
        raise ValueError("hidden predecessor context changed")
    if isolation["required_receipt_fields"] != EXPECTED_RECEIPT_FIELDS:
        raise ValueError("context receipt fields changed")
    if isolation["admissible_receipt_modes"] != [
        "PROVIDER_ATTESTED_EMPTY_CONTEXT",
        "HERMETIC_LOCAL_INSTANCE",
    ]:
        raise ValueError("context receipt modes changed")
    if isolation["recurring_chat_or_monitoring_task_without_fresh_context_attestation"] != (
        "NON_CREDIT_ONLY"
    ):
        raise ValueError("recurring chat boundary changed")
    if isolation["missing_or_self_asserted_receipt"] != "BLOCKED":
        raise ValueError("missing context receipt changed")

    binding = shared["predecessor_completion_binding"]
    if binding["applies_to_attempt_indices"] != list(range(1, 16)):
        raise ValueError("successor binding scope changed")
    if binding["predecessor_rule"] != (
        "ATTEMPT_I_BINDS_AUTHENTICATED_TERMINAL_COMPLETION_OF_ATTEMPT_I_MINUS_1_REGARDLESS_OF_OUTCOME"
    ):
        raise ValueError("outcome-independent successor rule changed")
    if binding["required_fields"] != [
        "predecessor_attempt_index",
        "predecessor_dispatch_id",
        "predecessor_frozen_request_sha256",
        "predecessor_response_artifact_sha256",
        "predecessor_completion_sha256",
        "completion_authentication",
    ]:
        raise ValueError("predecessor binding fields changed")
    if binding["same_run_problem_and_arm_required"] is not True:
        raise ValueError("predecessor cell boundary changed")
    if binding["model_visible"] is not False:
        raise ValueError("predecessor metadata visibility changed")
    if binding["registration_without_terminal_completion"] != (
        "DOES_NOT_AUTHORIZE_SUCCESSOR"
    ):
        raise ValueError("successor authorization changed")
    if binding["missing_invalid_replayed_or_cross_cell_binding"] != "BLOCKED":
        raise ValueError("invalid predecessor handling changed")

    if shared["terminal_result_rule"] != {
        "solved": "AT_LEAST_ONE_OF_16_ATTEMPTS_HAS_AN_ADMISSIBLE_FINAL_KERNEL_PASS",
        "selected_attempt": (
            "LOWEST_ATTEMPT_INDEX_WITH_AN_ADMISSIBLE_FINAL_KERNEL_PASS_AFTER_ALL_16_COMPLETE"
        ),
        "no_passing_attempt": (
            "UNSOLVED_ONLY_WHEN_ALL_16_CELLS_AND_COST_EVENTS_ARE_COMPLETE_OTHERWISE_INCOMPLETE"
        ),
        "human_selection_or_posthoc_rerun": "FORBIDDEN",
    }:
        raise ValueError("terminal result rule changed")

    arms = contract["arms"]
    if set(arms) != {"product_only", "multi_fidelity"}:
        raise ValueError("arm set changed")
    product = arms["product_only"]
    if product["attempt_relationship"] != (
        "ONE_PREDECLARED_16_NODE_LINEAR_SUCCESSOR_TRACE_WITH_FRESH_CONTEXT_AT_EVERY_NODE"
    ):
        raise ValueError("product relationship changed")
    if product["predecessor_graph"] != EXPECTED_PRODUCT_GRAPH:
        raise ValueError("product predecessor graph changed")
    if product["prompt_delta"] != "EXACT_SHARED_PRODUCT_CHAIN_PROMPT_TEMPLATE":
        raise ValueError("product prompt changed")
    if product["response_discriminator"] != {
        "product_prefix_lines": EXPECTED_PRODUCT_PREFIX,
        "final_answer_prefix_lines": EXPECTED_ANSWER_PREFIX,
        "classification": "EXACT_FIRST_TWO_VISIBLE_LINES",
        "missing_duplicate_or_ambiguous_discriminator": "BLOCKED",
        "no_answer_exact_utf8": EXPECTED_NO_ANSWER,
    }:
        raise ValueError("response discriminator changed")

    declaration = product["product_declaration_policy"]
    if declaration["required_name_template"] != (
        "SupernovaProduct.P_{FULL_PROBLEM_SHA256}.a{ATTEMPT_INDEX_TWO_DIGITS}"
    ):
        raise ValueError("product name policy changed")
    if declaration["declaration_count"] != 1:
        raise ValueError("product declaration count changed")
    if declaration["allowed_declaration_kinds"] != ["theorem", "lemma"]:
        raise ValueError("product declaration kinds changed")
    if declaration["parser_mode"] != "PINNED_LEAN_PARSER_WITHOUT_ELABORATION":
        raise ValueError("product parser mode changed")
    if set(declaration["forbidden_syntax"]) != {
        "import",
        "namespace",
        "end",
        "section",
        "variable",
        "notation",
        "macro",
        "syntax",
        "attribute",
        "set_option",
        "axiom",
        "opaque",
        "unsafe",
        "sorry",
        "admit",
        "declaration_attributes",
    }:
        raise ValueError("product declaration forbidden syntax changed")
    if declaration["unique_name_and_attempt_binding_required"] is not True:
        raise ValueError("product name binding changed")

    visible = product["allowed_model_visible_predecessor_material"]
    if visible["source"] != (
        "ALL_SYNTACTICALLY_ADMISSIBLE_PRODUCT_CANDIDATE_RESPONSES_FROM_LOWER_ATTEMPTS_IN_THIS_EXACT_RUN_PROBLEM_AND_ARM"
    ):
        raise ValueError("product source changed")
    if visible["kinds"] != ["PRODUCT_CANDIDATE"]:
        raise ValueError("product kind visibility changed")
    if visible["ordering"] != "STRICT_ASCENDING_PRODUCER_ATTEMPT_INDEX":
        raise ValueError("product ordering changed")
    if visible["inclusion"] != "ALL_AND_ONLY_ELIGIBLE_PRODUCTS":
        raise ValueError("product inclusion changed")
    if visible["verification_state"] != "NOT_LEAN_VERIFIED_BEFORE_ADMISSION":
        raise ValueError("product verification state changed")
    if visible["exact_response_bytes_only"] is not True:
        raise ValueError("product byte identity changed")
    if set(visible["forbidden"]) != {
        "FINAL_ANSWER_RESPONSE",
        "NO_ANSWER",
        "ERROR_TEXT",
        "VERIFIER_STATUS",
        "VERIFIER_RECEIPT",
        "VERIFIER_STDOUT",
        "VERIFIER_STDERR",
        "VERIFIER_TIMING",
        "PREDECESSOR_COMPLETION_METADATA",
        "CROSS_PROBLEM_PRODUCT",
        "CROSS_ARM_PRODUCT",
        "UNAUTHENTICATED_PRODUCT",
    }:
        raise ValueError("product forbidden visibility changed")

    if product["output_contract"] != {
        "allowed_kinds": ["PRODUCT_CANDIDATE", "FINAL_ANSWER", "NO_ANSWER"],
        "one_output_kind_per_attempt": True,
        "product_candidate_requires_syntax_and_name_policy": True,
        "product_candidate_is_never_lean_verified_before_admission": True,
        "final_answer_requires_exact_answer_discriminator": True,
        "no_answer_requires_exact_bytes": True,
    }:
        raise ValueError("product output contract changed")
    construction = product["construction_policy"]
    if construction["product_harness_forbidden_material"] != [
        "TARGET_DECLARATION",
        "SOLVED_TARGET_STUB",
        "UNADMITTED_PRODUCT",
    ]:
        raise ValueError("product harness boundary changed")
    if construction["transformation"] != (
        "BYTE_SLICE_ONLY_NO_PARSING_NORMALIZATION_OR_TARGET_REGENERATION"
    ):
        raise ValueError("construction transformation changed")
    if construction["required_bindings"] != [
        "frozen_target_source_sha256",
        "unique_proof_hole_start_byte",
        "unique_proof_hole_end_byte",
        "ordered_admitted_product_response_sha256",
        "exact_response_sha256",
        "constructed_source_sha256",
    ]:
        raise ValueError("construction bindings changed")
    if construction["target_statement_or_import_mutation"] != "BLOCKED":
        raise ValueError("statement-fidelity boundary changed")
    if product["verifier_policy"] != {
        "FINAL_ANSWER": (
            "RUN_EXACTLY_ONE_FINAL_LEAN_VERIFIER_ON_THE_BOUND_FINAL_CONSTRUCTED_SOURCE_AND_NEVER_FEED_RESULT_FORWARD"
        ),
        "PRODUCT_CANDIDATE": (
            "RUN_THE_IDENTICAL_BOUND_PRODUCT_HARNESS_LEAN_VERIFIER_THEN_QUARANTINE_ITS_RESULT_WITH_NO_EFFECT_ON_ADMISSION_VISIBILITY_OR_LATER_MODEL_BYTES"
        ),
        "NO_ANSWER": "NO_LEAN_INVOCATION_RECORD_TYPED_NOT_INVOKED",
        "ERROR": "NO_LEAN_INVOCATION_RECORD_TYPED_NOT_INVOKED",
        "any_verifier_or_predecessor_completion_metadata_visible_to_later_model_attempt": "BLOCKED",
    }:
        raise ValueError("product verifier policy changed")
    parity = product["verified_chain_surface_parity"]
    if parity["must_match_verified_chain"] != [
        "attempt_slots",
        "fresh_context_rule",
        "model_visible_prompt_template",
        "response_discriminators",
        "product_declaration_policy",
        "predecessor_completion_requirement",
        "common_complete_cost_ceiling_and_matching_rule",
        "product_and_final_harness_construction",
        "terminal_result_rule",
    ]:
        raise ValueError("surface parity field list changed")
    if parity["sole_permitted_difference"] != (
        "USE_OF_THE_IDENTICAL_PRODUCT_VERIFIER_RESULT_FOR_ADMISSION_PRODUCT_ONLY_IGNORES_IT_VERIFIED_CHAIN_REQUIRES_ADMISSIBLE_PASS"
    ):
        raise ValueError("product-chain causal contrast changed")
    if parity["admission_cost_treatment"] != {
        "product_only": (
            "CHARGE_ONE_IDENTICAL_LEAN_PRODUCT_VERIFIER_EVENT_BUT_QUARANTINE_AND_IGNORE_ITS_RESULT"
        ),
        "verified_chain": (
            "CHARGE_ONE_IDENTICAL_LEAN_PRODUCT_VERIFIER_EVENT_AND_REQUIRE_ADMISSIBLE_PASS_FOR_PRODUCT_VISIBILITY"
        ),
        "matching": (
            "IDENTICAL_PRODUCT_VERIFIER_INVOCATION_AND_G1_126_COMMON_COMPLETE_COST_RULE"
        ),
        "free_omitted_or_model_visible_verification_cost_or_result": "BLOCKED",
    }:
        raise ValueError("admission cost treatment changed")
    if parity["mismatch"] != "BLOCKED":
        raise ValueError("surface parity mismatch handling changed")

    surface = shared["product_chain_shared_surface"]
    projection = surface["projection"]
    expected_projection = {
        "schema": "supernova.product-chain-shared-surface.v1",
        "attempt_indices": shared["attempt_indices"],
        "completion_policy": shared["completion_policy"],
        "context_scope": isolation["required_scope"],
        "predecessor_rule": binding["predecessor_rule"],
        "predecessor_metadata_model_visible": binding["model_visible"],
        "model_visible_prompt_template": shared["product_chain_prompt_template"],
        "response_discriminator": product["response_discriminator"],
        "product_declaration_policy": product["product_declaration_policy"],
        "product_harness": construction["product_harness"],
        "final_harness": construction["final_harness"],
        "construction_transformation": construction["transformation"],
        "required_construction_bindings": construction["required_bindings"],
        "product_verifier_invocation": (
            "ONE_IDENTICAL_BOUND_LEAN_VERIFIER_EVENT_PER_PRODUCT_CANDIDATE"
        ),
        "final_verifier_invocation": (
            "ONE_IDENTICAL_BOUND_LEAN_VERIFIER_EVENT_PER_FINAL_ANSWER"
        ),
        "common_complete_cost_rule": contract["cost_event_contract"][
            "common_complete_cost_ceiling_and_matching_rule"
        ],
        "terminal_result_rule": shared["terminal_result_rule"],
    }
    if projection != expected_projection:
        raise ValueError("shared surface projection changed")
    canonical_projection = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if surface["projection_sha256"] != hashlib.sha256(
        canonical_projection
    ).hexdigest():
        raise ValueError("shared surface projection digest changed")
    if surface["projection_sha256"] != (
        "78cd516ce0f5e908c60f1e58c8b00bbd8c27ac0d50d8fc588c6e52d521df8d92"
    ):
        raise ValueError("shared surface identity changed")
    if surface["g1_125_requirement"] != (
        "MUST_REFERENCE_AND_MATCH_THIS_EXACT_PROJECTION_SHA256"
    ):
        raise ValueError("G1-125 surface binding changed")

    multi = arms["multi_fidelity"]
    if multi["attempt_relationship"] != (
        "ONE_PREDECLARED_16_SLOT_SUCCESSIVE_HALVING_GRAPH_WITH_FRESH_REGENERATION_AT_EVERY_SLOT"
    ):
        raise ValueError("multi-fidelity relationship changed")
    if multi["output_cap_unit_utf8_bytes"] != 2048:
        raise ValueError("fidelity cap unit changed")
    actual_shape = [
        (
            stage["stage_id"],
            stage["fidelity_rank"],
            stage["attempt_indices"],
            stage["visible_output_cap_utf8_bytes"],
        )
        for stage in multi["stages"]
    ]
    if actual_shape != EXPECTED_STAGE_SHAPE:
        raise ValueError("fidelity stage shape changed")
    if multi["promotion_graph"] != EXPECTED_PROMOTION_GRAPH:
        raise ValueError("promotion graph changed")
    computed_cap = sum(
        len(stage["attempt_indices"]) * stage["visible_output_cap_utf8_bytes"]
        for stage in multi["stages"]
    )
    if computed_cap != 98304 or computed_cap != multi[
        "aggregate_visible_output_cap_utf8_bytes"
    ]:
        raise ValueError("aggregate output cap changed")
    if multi["promotion_score"] != {
        "source": "FIRST_VISIBLE_RESPONSE_LINE_ONLY",
        "exact_regex": "^-- MULTI_FIDELITY_SELF_SCORE=([0-9]{4})$",
        "numeric_range": [0, 9999],
        "missing_malformed_no_answer_or_error_score": -1,
        "order": "DESCENDING_SCORE",
        "tie_break": [
            "ASCENDING_CANDIDATE_ID",
            "ASCENDING_PREDECESSOR_ATTEMPT_INDEX",
        ],
        "role": "ALLOCATION_PROXY_ONLY_NOT_PROOF_EVIDENCE",
    }:
        raise ValueError("promotion score changed")
    if multi["model_visible_predecessor_material"] != []:
        raise ValueError("multi-fidelity predecessor visibility changed")
    if "LEAN_PASS_FAIL_ERROR" not in multi["forbidden_promotion_inputs"]:
        raise ValueError("verifier promotion boundary changed")
    if "PREDECESSOR_RESPONSE_BYTES" not in multi["forbidden_promotion_inputs"]:
        raise ValueError("response promotion boundary changed")
    if multi["verifier_policy"]["phase_order"] != (
        "COMPLETE_ALL_16_MODEL_CALLS_AND_FREEZE_ALL_PROMOTION_DECISIONS_BEFORE_ANY_FINAL_LEAN_VERIFIER_CALL"
    ):
        raise ValueError("multi-fidelity phase order changed")
    if multi["verifier_policy"][
        "any_verifier_signal_used_for_promotion_or_visible_to_a_later_model_attempt"
    ] != "BLOCKED":
        raise ValueError("multi-fidelity verifier leakage changed")

    cost = contract["cost_event_contract"]
    if cost["model_calls_per_arm"] != 16:
        raise ValueError("model call cost changed")
    if cost["orchestration_events_per_arm"] != 16:
        raise ValueError("orchestration cost changed")
    if cost["multi_fidelity_verifier_calls"] != 16:
        raise ValueError("multi-fidelity verifier cost changed")
    if cost["unregistered_retry_product_promotion_or_cost_event"] != "BLOCKED":
        raise ValueError("unregistered event handling changed")
    if cost["multi_fidelity_aggregate_output_cap_must_be_the_common_arm_output_cap"] is not True:
        raise ValueError("common output cap binding changed")
    if cost["unknown_measurement"] != "BLOCKED":
        raise ValueError("unknown cost handling changed")
    if contract["terminal_decision_priority"] != [
        "BLOCKED",
        "INCOMPLETE",
        "SOLVED",
        "UNSOLVED",
    ]:
        raise ValueError("terminal priority changed")


class ConfirmatoryProductControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract()

    def test_checked_in_bytes_are_frozen_cross_platform(self) -> None:
        canonical_lf = CONTRACT_PATH.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(EXPECTED_GIT_BLOB_SHA1, git_blob_sha1(canonical_lf))

    def test_checked_in_contract_is_closed(self) -> None:
        validate_contract(self.contract)

    def test_product_only_differs_from_chain_only_by_admission(self) -> None:
        product = self.contract["arms"]["product_only"]
        self.assertEqual(EXPECTED_PRODUCT_GRAPH, product["predecessor_graph"])
        self.assertEqual(
            "EXACT_SHARED_PRODUCT_CHAIN_PROMPT_TEMPLATE",
            product["prompt_delta"],
        )
        self.assertEqual(
            "USE_OF_THE_IDENTICAL_PRODUCT_VERIFIER_RESULT_FOR_ADMISSION_PRODUCT_ONLY_IGNORES_IT_VERIFIED_CHAIN_REQUIRES_ADMISSIBLE_PASS",
            product["verified_chain_surface_parity"]["sole_permitted_difference"],
        )
        self.assertEqual(
            "NOT_LEAN_VERIFIED_BEFORE_ADMISSION",
            product["allowed_model_visible_predecessor_material"][
                "verification_state"
            ],
        )

    def test_successor_binding_does_not_leak_outcome(self) -> None:
        binding = self.contract["shared"]["predecessor_completion_binding"]
        self.assertFalse(binding["model_visible"])
        self.assertIn("REGARDLESS_OF_OUTCOME", binding["predecessor_rule"])
        self.assertEqual(list(range(1, 16)), binding["applies_to_attempt_indices"])

    def test_product_grammar_and_statement_fidelity_are_closed(self) -> None:
        product = self.contract["arms"]["product_only"]
        self.assertEqual(
            EXPECTED_PRODUCT_PREFIX,
            product["response_discriminator"]["product_prefix_lines"],
        )
        self.assertEqual(
            EXPECTED_ANSWER_PREFIX,
            product["response_discriminator"]["final_answer_prefix_lines"],
        )
        self.assertEqual(
            EXPECTED_NO_ANSWER,
            product["response_discriminator"]["no_answer_exact_utf8"],
        )
        self.assertIn("axiom", product["product_declaration_policy"]["forbidden_syntax"])
        self.assertEqual(
            "BLOCKED",
            product["construction_policy"]["target_statement_or_import_mutation"],
        )

    def test_shared_surface_projection_and_harnesses_are_immutable(self) -> None:
        surface = self.contract["shared"]["product_chain_shared_surface"]
        self.assertEqual(
            "78cd516ce0f5e908c60f1e58c8b00bbd8c27ac0d50d8fc588c6e52d521df8d92",
            surface["projection_sha256"],
        )
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["construction_policy"]["product_harness"] = (
            "CALLER_SELECTED"
        )
        with self.assertRaisesRegex(ValueError, "shared surface projection"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["construction_policy"]["final_harness"] = (
            "REGENERATE_TARGET"
        )
        with self.assertRaisesRegex(ValueError, "shared surface projection"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["verified_chain_surface_parity"][
            "must_match_verified_chain"
        ] = []
        with self.assertRaisesRegex(ValueError, "surface parity field list"):
            validate_contract(changed)

    def test_no_answer_representation_is_closed(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["response_discriminator"][
            "no_answer_exact_utf8"
        ] = ""
        with self.assertRaisesRegex(ValueError, "response discriminator"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["output_contract"][
            "no_answer_requires_exact_bytes"
        ] = False
        with self.assertRaisesRegex(ValueError, "output contract"):
            validate_contract(changed)

    def test_multi_fidelity_is_metadata_only_successive_halving(self) -> None:
        multi = self.contract["arms"]["multi_fidelity"]
        self.assertEqual([], multi["model_visible_predecessor_material"])
        self.assertEqual(EXPECTED_PROMOTION_GRAPH, multi["promotion_graph"])
        self.assertIn("PREDECESSOR_RESPONSE_BYTES", multi["forbidden_promotion_inputs"])
        self.assertIn("LEAN_PASS_FAIL_ERROR", multi["forbidden_promotion_inputs"])

    def test_outcome_dependent_retry_or_metadata_visibility_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["shared"]["predecessor_completion_binding"]["predecessor_rule"] = (
            "ONLY_AFTER_FAIL"
        )
        with self.assertRaisesRegex(ValueError, "outcome-independent"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["shared"]["predecessor_completion_binding"]["model_visible"] = True
        with self.assertRaisesRegex(ValueError, "metadata visibility"):
            validate_contract(changed)

    def test_product_surface_or_admission_confound_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["prompt_delta"] = "PRODUCT_ONLY_SPECIAL_PROMPT"
        with self.assertRaisesRegex(ValueError, "product prompt"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["verified_chain_surface_parity"][
            "sole_permitted_difference"
        ] = "PROMPT_AND_ADMISSION"
        with self.assertRaisesRegex(ValueError, "causal contrast"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["construction_policy"][
            "target_statement_or_import_mutation"
        ] = "ALLOWED"
        with self.assertRaisesRegex(ValueError, "statement-fidelity"):
            validate_contract(changed)

    def test_product_policy_weakening_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["product_declaration_policy"][
            "forbidden_syntax"
        ].remove("axiom")
        with self.assertRaisesRegex(ValueError, "forbidden syntax"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["allowed_model_visible_predecessor_material"][
            "verification_state"
        ] = "LEAN_PASS"
        with self.assertRaisesRegex(ValueError, "verification state"):
            validate_contract(changed)

    def test_multi_fidelity_response_or_verifier_leak_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["multi_fidelity"]["model_visible_predecessor_material"] = [
            "PREDECESSOR_RESPONSE_DIGEST"
        ]
        with self.assertRaisesRegex(ValueError, "predecessor visibility"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["multi_fidelity"]["forbidden_promotion_inputs"].remove(
            "LEAN_PASS_FAIL_ERROR"
        )
        with self.assertRaisesRegex(ValueError, "verifier promotion"):
            validate_contract(changed)

    def test_recurring_chat_without_attestation_is_non_credit(self) -> None:
        isolation = self.contract["shared"]["context_isolation_declaration"]
        self.assertEqual(
            "NON_CREDIT_ONLY",
            isolation[
                "recurring_chat_or_monitoring_task_without_fresh_context_attestation"
            ],
        )
        self.assertEqual("BLOCKED", isolation["missing_or_self_asserted_receipt"])

    def test_unregistered_cost_or_retry_cannot_be_free(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["cost_event_contract"][
            "unregistered_retry_product_promotion_or_cost_event"
        ] = "IGNORE"
        with self.assertRaisesRegex(ValueError, "unregistered event"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["cost_event_contract"]["unknown_measurement"] = "ZERO"
        with self.assertRaisesRegex(ValueError, "unknown cost"):
            validate_contract(changed)


if __name__ == "__main__":
    unittest.main()
