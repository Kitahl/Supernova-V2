from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "goal1" / "CONFIRMATORY_BASELINES.json"

EXPECTED_STRATEGIES = [
    "direct_minimal",
    "simp_norm_num",
    "linarith",
    "nlinarith",
    "ring_ring_nf",
    "field_simp",
    "omega",
    "positivity",
    "interval_cases",
    "contradiction",
    "by_cases",
    "induction",
    "calc_rewrite",
    "library_lemma",
    "local_have_facts",
    "alternate_concise",
]
EXPECTED_TEMPLATE = "\n".join(
    [
        "You are solving exactly one frozen Lean 4 theorem.",
        "Return only the tactic body that replaces {PROOF_HOLE} in the supplied source.",
        "Do not return Markdown, prose, imports, declarations, theorem statements, `by`, `sorry`, `admit`, custom axioms, or commands.",
        "Use only the supplied theorem statement and the imports already present in the frozen source.",
        "Do not rely on any earlier conversation, benchmark problem, arm, attempt, response, verifier result, saved memory, file, app, tool, or external network.",
        "FROZEN_SOURCE_SHA256={SOURCE_SHA256}",
        "FROZEN_THEOREM_NAME={THEOREM_NAME}",
        "BEGIN_FROZEN_SOURCE",
        "{SOURCE_WITH_PROOF_HOLE}",
        "END_FROZEN_SOURCE",
    ]
)
EXPECTED_CONTEXT_FIELDS = [
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


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_contract(contract: object) -> None:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    expected_top = {
        "schema_version",
        "contract_id",
        "status",
        "scope",
        "estimand_role",
        "scientific_credit",
        "common",
        "arms",
        "cost_binding",
        "terminal_decision_priority",
    }
    if set(contract) != expected_top:
        raise ValueError("top-level fields changed")
    if contract["schema_version"] != 1:
        raise ValueError("schema version changed")
    if contract["contract_id"] != "goal1-confirmatory-baselines-v1":
        raise ValueError("contract identity changed")
    if contract["status"] != "FROZEN":
        raise ValueError("contract is not frozen")
    if contract["scope"] != ["ordinary", "portfolio"]:
        raise ValueError("baseline scope changed")
    if contract["estimand_role"] != "CONTROL_ARM_DEFINITIONS_ONLY":
        raise ValueError("estimand role changed")
    if contract["scientific_credit"] != (
        "NONE_UNTIL_SEALED_BY_CONFIRMATORY_PROTOCOL_AND_EXECUTED_WITH_ADMISSIBLE_EVIDENCE"
    ):
        raise ValueError("scientific-credit boundary changed")

    common = contract["common"]
    if common["attempts_per_problem_arm"] != 16:
        raise ValueError("attempt quota changed")
    if common["attempt_indices"] != list(range(16)):
        raise ValueError("attempt indices changed")
    if common["dispatch_policy"] != "REGISTER_ALL_16_BEFORE_FIRST_MODEL_CALL":
        raise ValueError("dispatch policy changed")
    if common["completion_policy"] != (
        "EXECUTE_AND_RECONCILE_ALL_16_EVEN_AFTER_A_VERIFIED_PASS"
    ):
        raise ValueError("completion policy changed")
    if common["retry_policy"] != {
        "adaptive_retries": "FORBIDDEN",
        "reissue_failed_dispatch": "FORBIDDEN",
        "unregistered_attempt": "BLOCKED",
        "provider_or_transport_error": "INCOMPLETE_NOT_UNSOLVED",
        "terminal_nonanswer_with_complete_artifact_and_cost": "UNSOLVED_ATTEMPT",
    }:
        raise ValueError("retry policy changed")
    if common["expected_events_per_attempt"] != [
        {"event_kind": "scheduled_chat_model_call", "count": 1},
        {"event_kind": "lean_final_verifier", "count": 1},
        {"event_kind": "orchestration", "count": 1},
    ]:
        raise ValueError("expected event schedule changed")
    if common["response_contract"] != {
        "encoding": "UTF-8",
        "artifact_kind": "LEAN_TACTIC_BODY",
        "max_visible_response_utf8_bytes": 32768,
        "empty_response": "TERMINAL_NONANSWER",
        "over_limit_or_invalid_utf8": "BLOCKED",
    }:
        raise ValueError("response contract changed")
    if common["prompt_rendering"] != {
        "encoding": "UTF-8",
        "unicode_normalization": "NONE",
        "line_endings": "LF",
        "placeholder_substitution": "SINGLE_PASS_EXACT_NO_TRIMMING",
        "placeholders": [
            "SOURCE_SHA256",
            "THEOREM_NAME",
            "SOURCE_WITH_PROOF_HOLE",
            "ATTEMPT_INDEX",
            "STRATEGY_ID",
        ],
        "common_template": EXPECTED_TEMPLATE,
    }:
        raise ValueError("prompt rendering changed")

    isolation = common["context_isolation_declaration"]
    if isolation["shared_protocol_is_canonical_authority"] is not True:
        raise ValueError("shared protocol must own context isolation")
    if isolation["required_scope"] != (
        "ONE_NEW_EMPTY_MODEL_CONTEXT_PER_PROBLEM_ARM_ATTEMPT"
    ):
        raise ValueError("fresh-context scope changed")
    if isolation["allowed_predecessor_contexts"] != []:
        raise ValueError("baseline predecessor context is forbidden")
    if isolation["admissible_receipt_modes"] != [
        "PROVIDER_ATTESTED_EMPTY_CONTEXT",
        "HERMETIC_LOCAL_INSTANCE",
    ]:
        raise ValueError("admissible context evidence changed")
    if isolation["required_receipt_fields"] != EXPECTED_CONTEXT_FIELDS:
        raise ValueError("context receipt fields changed")
    if isolation["unique_context_id_per_attempt"] is not True:
        raise ValueError("context identity uniqueness is required")
    if isolation["destroy_or_close_after_response"] is not True:
        raise ValueError("context must close after the attempt")
    if isolation[
        "recurring_chat_or_monitoring_task_without_fresh_context_attestation"
    ] != "NON_CREDIT_ONLY":
        raise ValueError("recurring tasks cannot silently earn credit")
    if isolation["missing_or_self_asserted_receipt"] != "BLOCKED":
        raise ValueError("missing context evidence must block")

    arms = contract["arms"]
    if set(arms) != {"ordinary", "portfolio"}:
        raise ValueError("arm set changed")
    ordinary = arms["ordinary"]
    portfolio = arms["portfolio"]
    if ordinary["strategy_schedule"] != ["direct"] * 16:
        raise ValueError("ordinary strategy schedule changed")
    if portfolio["strategy_schedule"] != EXPECTED_STRATEGIES:
        raise ValueError("portfolio strategy schedule changed")
    if ordinary["predecessor_visibility"] or portfolio["predecessor_visibility"]:
        raise ValueError("baseline attempts may not see predecessors")
    if ordinary["verifier_feedback_to_model"] != "NONE":
        raise ValueError("ordinary verifier feedback changed")
    if portfolio["verifier_feedback_to_model"] != "NONE":
        raise ValueError("portfolio verifier feedback changed")
    if ordinary["product_persistence"] != "NONE":
        raise ValueError("ordinary product persistence changed")
    if portfolio["product_persistence"] != "NONE":
        raise ValueError("portfolio product persistence changed")
    if ordinary["prompt_delta"] != "\n".join(
        [
            "ARM=ordinary",
            "ATTEMPT={ATTEMPT_INDEX}",
            "Solve the theorem directly. Do not create a reusable external product or request feedback.",
        ]
    ):
        raise ValueError("ordinary prompt changed")
    if portfolio["prompt_delta"] != "\n".join(
        [
            "ARM=portfolio",
            "ATTEMPT={ATTEMPT_INDEX}",
            "FROZEN_STRATEGY={STRATEGY_ID}",
            "Produce one independent proof attempt using the named strategy when applicable. Do not read or reuse another attempt.",
        ]
    ):
        raise ValueError("portfolio prompt changed")

    cost = contract["cost_binding"]
    if cost != {
        "usage_basis": "visible_utf8_bytes",
        "input_bytes": "EXACT_RENDERED_REQUEST_BYTES_PER_ATTEMPT",
        "output_bytes": "EXACT_TERMINAL_RESPONSE_BYTES_PER_ATTEMPT",
        "model_calls": 16,
        "verifier_calls": 16,
        "orchestration_events": 16,
        "all_failures_and_nonanswers_are_charged": True,
        "common_ceiling_and_residual_policy": (
            "MUST_BE_BOUND_BY_CONFIRMATORY_COST_POLICY"
        ),
        "unknown_measurement": "BLOCKED",
    }:
        raise ValueError("cost binding changed")
    if contract["terminal_decision_priority"] != [
        "BLOCKED",
        "INCOMPLETE",
        "SOLVED",
        "UNSOLVED",
    ]:
        raise ValueError("terminal priority changed")


class ConfirmatoryBaselineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract()

    def test_checked_in_contract_is_closed(self) -> None:
        validate_contract(self.contract)

    def test_attempt_quota_events_and_costs_are_symmetric(self) -> None:
        common = self.contract["common"]
        cost = self.contract["cost_binding"]
        self.assertEqual(16, common["attempts_per_problem_arm"])
        self.assertEqual(16, cost["model_calls"])
        self.assertEqual(16, cost["verifier_calls"])
        self.assertEqual(16, cost["orchestration_events"])
        self.assertEqual(3, len(common["expected_events_per_attempt"]))

    def test_prompt_or_strategy_change_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["arms"]["ordinary"]["prompt_delta"] += "\nReview your answer."
        with self.assertRaisesRegex(ValueError, "ordinary prompt"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["arms"]["portfolio"]["strategy_schedule"][0] = "posthoc_strategy"
        with self.assertRaisesRegex(ValueError, "portfolio strategy"):
            validate_contract(changed)

    def test_unregistered_retry_or_cost_event_is_rejected(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["common"]["retry_policy"]["adaptive_retries"] = "ALLOWED"
        with self.assertRaisesRegex(ValueError, "retry policy"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["common"]["expected_events_per_attempt"].append(
            {"event_kind": "free_model_call", "count": 1}
        )
        with self.assertRaisesRegex(ValueError, "expected event"):
            validate_contract(changed)

    def test_continuing_chat_without_attestation_is_non_credit(self) -> None:
        isolation = self.contract["common"]["context_isolation_declaration"]
        self.assertEqual(
            "NON_CREDIT_ONLY",
            isolation[
                "recurring_chat_or_monitoring_task_without_fresh_context_attestation"
            ],
        )
        self.assertEqual("BLOCKED", isolation["missing_or_self_asserted_receipt"])

    def test_fresh_context_rule_cannot_be_weakened(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["common"]["context_isolation_declaration"][
            "allowed_predecessor_contexts"
        ] = ["prior_problem"]
        with self.assertRaisesRegex(ValueError, "predecessor context"):
            validate_contract(changed)
        changed = copy.deepcopy(self.contract)
        changed["common"]["context_isolation_declaration"][
            "admissible_receipt_modes"
        ] = ["MODEL_SELF_ASSERTION"]
        with self.assertRaisesRegex(ValueError, "context evidence"):
            validate_contract(changed)

    def test_provider_errors_cannot_be_scored_as_unsolved(self) -> None:
        self.assertEqual(
            "INCOMPLETE_NOT_UNSOLVED",
            self.contract["common"]["retry_policy"]["provider_or_transport_error"],
        )
        self.assertIn(
            "INCOMPLETE",
            self.contract["terminal_decision_priority"],
        )


if __name__ == "__main__":
    unittest.main()
