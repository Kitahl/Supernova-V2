from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
EXPECTED_GIT_BLOB_SHA1 = "07c64de932158be6cd91e281da821b8d7dbd2a40"
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
    {"attempt_index": index, "retry_of_attempt": None if index == 0 else index - 1}
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
    if shared["prompt_base_authority"] != {
        "path": "goal1/CONFIRMATORY_BASELINES.json",
        "git_blob_sha1": "2ab23d89b22dd7c21963da1e3543bf2dc0e39193",
        "field": "common.prompt_rendering.common_template",
        "rendering": "UTF-8_LF_SINGLE_PASS_EXACT_NO_TRIMMING",
    }:
        raise ValueError("prompt authority changed")
    if shared["capability_surface"] != {
        "model_provider_version_reasoning_mode_tools_and_generation_settings": (
            "MUST_MATCH_ACROSS_ALL_FIVE_ARMS_EXCEPT_THE_FROZEN_MULTI_FIDELITY_VISIBLE_OUTPUT_CAP_AND_BE_BOUND_BY_THE_SHARED_CONFIRMATORY_PROTOCOL"
        ),
        "external_tools": "FORBIDDEN",
        "network_during_model_attempt": "FORBIDDEN",
        "model_visible_input": (
            "EXACT_BASELINE_COMMON_TEMPLATE_PLUS_EXACT_ARM_DELTA_PLUS_ONLY_THE_ARM_LOCAL_ARTIFACTS_ALLOWED_BELOW"
        ),
    }:
        raise ValueError("capability surface changed")
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
    if binding["registration_without_terminal_completion"] != (
        "DOES_NOT_AUTHORIZE_RETRY"
    ):
        raise ValueError("retry authorization changed")
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
        "ONE_PREDECLARED_16_NODE_LINEAR_TRACE_WITH_FRESH_CONTEXT_AT_EVERY_NODE"
    ):
        raise ValueError("product relationship changed")
    if product["predecessor_graph"] != EXPECTED_PRODUCT_GRAPH:
        raise ValueError("product predecessor graph changed")
    visible = product["allowed_model_visible_predecessor_material"]
    if visible["source"] != (
        "AUTHENTICATED_TERMINAL_PRODUCT_RESPONSES_FROM_LOWER_ATTEMPTS_IN_THIS_EXACT_RUN_PROBLEM_AND_ARM"
    ):
        raise ValueError("product source changed")
    if visible["kinds"] != ["PRODUCT"]:
        raise ValueError("product kind visibility changed")
    if visible["ordering"] != "STRICT_ASCENDING_PRODUCER_ATTEMPT_INDEX":
        raise ValueError("product ordering changed")
    if visible["inclusion"] != "ALL_AND_ONLY_ELIGIBLE_PRODUCTS":
        raise ValueError("product inclusion changed")
    if visible["verification_state"] != "UNVERIFIED":
        raise ValueError("product verification state changed")
    if set(visible["forbidden"]) != {
        "FINAL_ANSWER_RESPONSE",
        "NO_ANSWER",
        "ERROR_TEXT",
        "VERIFIER_STATUS",
        "VERIFIER_STDOUT",
        "VERIFIER_STDERR",
        "VERIFIER_TIMING",
        "CROSS_PROBLEM_PRODUCT",
        "CROSS_ARM_PRODUCT",
        "UNAUTHENTICATED_PRODUCT",
    }:
        raise ValueError("product forbidden visibility changed")
    if product["output_contract"] != {
        "allowed_kinds": ["PRODUCT", "ANSWERED", "NO_ANSWER"],
        "product_schema": "supernova.product-emission.v1",
        "product_canonical_fields": ["content_utf8", "kind", "schema"],
        "product_kind_literal": "PRODUCT",
        "one_output_kind_per_attempt": True,
        "product_is_never_kernel_admitted": True,
    }:
        raise ValueError("product output contract changed")
    if product["verifier_policy"] != {
        "ANSWERED": "RUN_EXACTLY_ONE_FINAL_LEAN_VERIFIER_AND_NEVER_FEED_RESULT_FORWARD",
        "PRODUCT": "NO_LEAN_INVOCATION_RECORD_TYPED_NOT_INVOKED",
        "NO_ANSWER": "NO_LEAN_INVOCATION_RECORD_TYPED_NOT_INVOKED",
        "ERROR": "NO_LEAN_INVOCATION_RECORD_TYPED_NOT_INVOKED",
        "any_verifier_feedback_visible_to_later_model_attempt": "BLOCKED",
    }:
        raise ValueError("product verifier policy changed")

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
    if multi["stages"][0]["candidate_slots"] != [
        "C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7"
    ]:
        raise ValueError("initial candidate slots changed")
    if multi["promotion_graph"] != EXPECTED_PROMOTION_GRAPH:
        raise ValueError("promotion graph changed")
    if multi["aggregate_visible_output_cap_utf8_bytes"] != 98304:
        raise ValueError("aggregate output cap changed")
    computed_cap = sum(
        len(stage["attempt_indices"]) * stage["visible_output_cap_utf8_bytes"]
        for stage in multi["stages"]
    )
    if computed_cap != multi["aggregate_visible_output_cap_utf8_bytes"]:
        raise ValueError("aggregate output cap does not match stages")
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
    if set(multi["forbidden_promotion_inputs"]) != {
        "LEAN_PASS_FAIL_ERROR",
        "VERIFIER_STDOUT",
        "VERIFIER_STDERR",
        "VERIFIER_TIMING",
        "PREDECESSOR_RESPONSE_BYTES",
        "PREDECESSOR_RESPONSE_DIGEST",
        "PRODUCT_BYTES",
        "PRODUCT_DIGEST",
        "HUMAN_JUDGMENT",
        "POSTHOC_SCORE",
    }:
        raise ValueError("forbidden promotion inputs changed")
    if multi["retry_identity_rule"] != (
        "PROMOTED_SLOT_BINDS_THE_AUTHENTICATED_SELECTED_IMMEDIATE_PREDECESSOR_COMPLETION_AND_INHERITS_ONLY_ITS_CANDIDATE_ID"
    ):
        raise ValueError("retry identity rule changed")
    if multi["verifier_policy"] != {
        "phase_order": (
            "COMPLETE_ALL_16_MODEL_CALLS_AND_FREEZE_ALL_PROMOTION_DECISIONS_BEFORE_ANY_FINAL_LEAN_VERIFIER_CALL"
        ),
        "per_attempt": "RUN_EXACTLY_ONE_FINAL_LEAN_VERIFIER_ON_THE_FULL_VISIBLE_RESPONSE",
        "self_score_comment": "REMAINS_IN_RESPONSE_AS_VALID_LEAN_COMMENT",
        "any_verifier_signal_used_for_promotion_or_visible_to_a_later_model_attempt": "BLOCKED",
    }:
        raise ValueError("multi-fidelity verifier policy changed")

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

    def test_checked_in_bytes_are_frozen(self) -> None:
        canonical_lf = CONTRACT_PATH.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(EXPECTED_GIT_BLOB_SHA1, git_blob_sha1(canonical_lf))

    def test_checked_in_contract_is_closed(self) -> None:
        validate_contract(self.contract)

    def test_product_only_is_unverified_visible_product_ablation(self) -> None:
        product = self.contract["arms"]["product_only"]
        self.assertEqual(EXPECTED_PRODUCT_GRAPH, product["predecessor_graph"])
        self.assertEqual(
            "UNVERIFIED",
            product["allowed_model_visible_predecessor_material"]["verification_state"],
        )
        self.assertTrue(product["output_contract"]["product_is_never_kernel_admitted"])
        self.assertEqual(
            "BLOCKED",
            product["verifier_policy"][
                "any_verifier_feedback_visible_to_later_model_attempt"
            ],
        )

    def test_multi_fidelity_is_metadata_only_successive_halving(self) -> None:
        multi = self.contract["arms"]["multi_fidelity"]
        self.assertEqual([], multi["model_visible_predecessor_material"])
        self.assertEqual(EXPECTED_PROMOTION_GRAPH, multi["promotion_graph"])
        self.assertEqual(
            98304,
            sum(
                len(stage["attempt_indices"]) * stage["visible_output_cap_utf8_bytes"]
                for stage in multi["stages"]
            ),
        )
        self.assertIn("PREDECESSOR_RESPONSE_BYTES", multi["forbidden_promotion_inputs"])
        self.assertIn("LEAN_PASS_FAIL_ERROR", multi["forbidden_promotion_inputs"])

    def test_cross_cell_or_unbound_retry_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["shared"]["predecessor_completion_binding"][
            "same_run_problem_and_arm_required"
        ] = False
        with self.assertRaisesRegex(ValueError, "cell boundary"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["shared"]["predecessor_completion_binding"][
            "registration_without_terminal_completion"
        ] = "AUTHORIZES_RETRY"
        with self.assertRaisesRegex(ValueError, "retry authorization"):
            validate_contract(changed)

    def test_product_visibility_mutations_are_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["allowed_model_visible_predecessor_material"][
            "verification_state"
        ] = "LEAN_PASS"
        with self.assertRaisesRegex(ValueError, "verification state"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["product_only"]["allowed_model_visible_predecessor_material"][
            "forbidden"
        ].remove("CROSS_ARM_PRODUCT")
        with self.assertRaisesRegex(ValueError, "forbidden visibility"):
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
        with self.assertRaisesRegex(ValueError, "forbidden promotion"):
            validate_contract(changed)

    def test_graph_cap_or_score_change_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["multi_fidelity"]["promotion_graph"][0][
            "eligible_predecessor_attempts"
        ] = [0, 2]
        with self.assertRaisesRegex(ValueError, "promotion graph"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["multi_fidelity"]["stages"][0][
            "visible_output_cap_utf8_bytes"
        ] = 4096
        with self.assertRaisesRegex(ValueError, "stage shape"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["multi_fidelity"]["promotion_score"]["source"] = (
            "LEAN_VERIFIER_RESULT"
        )
        with self.assertRaisesRegex(ValueError, "promotion score"):
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
