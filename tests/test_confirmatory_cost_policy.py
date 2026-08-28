from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "goal1" / "CONFIRMATORY_COST_POLICY.json"
BASELINES_PATH = ROOT / "goal1" / "CONFIRMATORY_BASELINES.json"
PRODUCT_CONTROLS_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
VERIFIED_CHAIN_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIED_CHAIN.json"
RUNTIME_PATH = ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json"

EXPECTED_POLICY_BLOB_SHA1 = "5659b1a6c2318c8ea16e4c609e29e2c8f9d86ec5"
EXPECTED_AUTHORITY_BLOBS = {
    "baselines": "2ab23d89b22dd7c21963da1e3543bf2dc0e39193",
    "product_controls": "ab2b298e29022f931ec141bdba50485b0967ad3f",
    "verified_chain": "089592344a85509c5a587f2d77d37d3b7c825061",
    "runtime": "1fa7caeefdf3c01cea5603f4865c2f9eec11a0fb",
}
AUTHORITY_PATHS = {
    "baselines": BASELINES_PATH,
    "product_controls": PRODUCT_CONTROLS_PATH,
    "verified_chain": VERIFIED_CHAIN_PATH,
    "runtime": RUNTIME_PATH,
}
EXPECTED_OUTPUT_CAPS = [
    *([2048] * 8),
    *([4096] * 4),
    *([8192] * 2),
    16384,
    32768,
]
EXPECTED_INPUT_CAPS = [131072] * 16
EXPECTED_CHECKPOINTS = [
    {
        "completed_attempt_prefix": count,
        "cumulative_request_capacity_utf8_bytes": sum(EXPECTED_INPUT_CAPS[:count]),
        "cumulative_output_capacity_utf8_bytes": sum(EXPECTED_OUTPUT_CAPS[:count]),
        "cumulative_model_call_slots": count,
        "cumulative_verifier_slots": count,
        "cumulative_verifier_wall_clock_capacity_milliseconds": count * 600000,
        "cumulative_orchestration_wall_clock_capacity_milliseconds": count * 600000,
    }
    for count in [1, 2, 4, 8, 16]
]
EXPECTED_EVENT_KINDS = [
    "MODEL_CALL",
    "CONTEXT_ISOLATION_RECEIPT",
    "VERIFIER_SLOT",
    "ORCHESTRATION",
    "PREDECESSOR_RECONCILIATION",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def validate_policy(policy: object) -> None:
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    if set(policy) != {
        "schema_version",
        "policy_id",
        "status",
        "estimand",
        "claim_boundary",
        "authorities",
        "applicability",
        "model_usage_basis",
        "common_envelope",
        "request_overflow_policy",
        "response_overflow_policy",
        "verifier_and_orchestration_accounting",
        "event_ledger",
        "reconciliation",
        "primary_and_robustness_analysis",
        "limitations",
    }:
        raise ValueError("top-level fields changed")
    if policy["schema_version"] != 1:
        raise ValueError("schema version changed")
    if policy["policy_id"] != (
        "goal1-confirmatory-complete-observable-allocation-v1"
    ):
        raise ValueError("policy identity changed")
    if policy["status"] != "FROZEN":
        raise ValueError("policy is not frozen")
    if policy["estimand"] != (
        "PAIRED_SUCCESS_UNDER_AN_IDENTICAL_FROZEN_COMPLETE_OBSERVABLE_ALLOCATION_ENVELOPE"
    ):
        raise ValueError("estimand changed")

    claim = policy["claim_boundary"]
    if claim["permitted"] != (
        "VERIFIED_CHAIN_SUPERIORITY_UNDER_A_MATCHED_COMPLETE_OBSERVABLE_ALLOCATION_ENVELOPE"
    ):
        raise ValueError("permitted claim changed")
    if set(claim["forbidden"]) != {
        "EQUAL_REALIZED_COST",
        "EQUAL_PROVIDER_TOKENS",
        "EQUAL_HIDDEN_REASONING_COMPUTE",
        "EQUAL_PHYSICAL_COMPUTE",
        "EQUAL_PROVIDER_QUEUE_OR_SHARED_SERVING_LOAD",
    }:
        raise ValueError("forbidden claims changed")
    if claim["visible_utf8_bytes_are"] != (
        "MODEL_USAGE_PROXY_NOT_PROVIDER_TOKEN_OR_PHYSICAL_COMPUTE"
    ):
        raise ValueError("proxy label changed")
    if claim["realized_usage_is"] != (
        "TREATMENT_AFFECTED_AUDIT_AND_SENSITIVITY_OUTPUT_NOT_A_MATCHING_OR_EXCLUSION_VARIABLE"
    ):
        raise ValueError("realized-usage role changed")

    authorities = policy["authorities"]
    for name, expected_blob in EXPECTED_AUTHORITY_BLOBS.items():
        if git_blob_sha1(canonical_lf(AUTHORITY_PATHS[name])) != expected_blob:
            raise ValueError(f"{name} authority bytes drifted")
        if authorities[name]["git_blob_sha1"] != expected_blob:
            raise ValueError(f"{name} authority binding changed")
    if authorities["runtime"]["runtime_id"] != (
        "goal1-confirmatory-lean-runtime-v1"
    ):
        raise ValueError("runtime identity changed")
    if authorities["protocol"] != {
        "path": "goal1/CONFIRMATORY_PROTOCOL.json",
        "required_status": "SEALED",
        "missing_or_digest_mismatch": "BLOCKED",
    }:
        raise ValueError("future protocol authority changed")

    applicability = policy["applicability"]
    if applicability != {
        "arms": [
            "ordinary",
            "portfolio",
            "product_only",
            "multi_fidelity",
            "verified_chain",
        ],
        "unit": "ONE_PROBLEM_ARM_CELL",
        "paired_unit": (
            "ONE_FROZEN_PROBLEM_FAMILY_REPRESENTATIVE_WITH_ALL_FIVE_COMPLETE_ARM_CELLS"
        ),
        "attempts": list(range(16)),
        "register_before_first_dispatch": True,
        "post_dispatch_budget_change": "BLOCKED",
    }:
        raise ValueError("applicability changed")

    usage = policy["model_usage_basis"]
    if usage != {
        "primary_observable_unit": "VISIBLE_UTF8_BYTES",
        "request_measurement": (
            "EXACT_CANONICAL_RENDERED_MODEL_VISIBLE_REQUEST_BYTES_BEFORE_PROVIDER_DISPATCH"
        ),
        "response_measurement": (
            "EXACT_TERMINAL_MODEL_VISIBLE_RESPONSE_BYTES_RETURNED_BY_PROVIDER_OR_TYPED_ZERO_BYTE_TRANSPORT_FAILURE"
        ),
        "byte_count": (
            "LENGTH_OF_EXACT_UTF8_ENCODING_NO_NORMALIZATION_TRIMMING_OR_REGENERATION"
        ),
        "provider_token_count": (
            "RECORDED_IF_AUTHENTICATED_BUT_NEVER_SUBSTITUTED_FOR_PRIMARY_BASIS"
        ),
        "missing_request_or_response_measurement": "BLOCKED",
    }:
        raise ValueError("model-usage basis changed")

    envelope = policy["common_envelope"]
    if envelope["model_call_slots"] != 16:
        raise ValueError("model call slots changed")
    if envelope["request_capacity_utf8_bytes_by_attempt"] != EXPECTED_INPUT_CAPS:
        raise ValueError("request capacity schedule changed")
    if envelope["aggregate_request_capacity_utf8_bytes"] != sum(EXPECTED_INPUT_CAPS):
        raise ValueError("aggregate request capacity changed")
    if envelope["response_capacity_utf8_bytes_by_attempt"] != EXPECTED_OUTPUT_CAPS:
        raise ValueError("response capacity schedule changed")
    if envelope["aggregate_response_capacity_utf8_bytes"] != 98304:
        raise ValueError("aggregate response capacity changed")
    if envelope["aggregate_response_capacity_utf8_bytes"] != sum(
        EXPECTED_OUTPUT_CAPS
    ):
        raise ValueError("response schedule does not sum to aggregate")
    if envelope["verifier_slots"] != 16 or envelope["orchestration_slots"] != 16:
        raise ValueError("host event slots changed")
    if envelope["verifier_wall_clock_capacity_milliseconds_by_attempt"] != (
        [600000] * 16
    ):
        raise ValueError("verifier capacity schedule changed")
    if envelope["aggregate_verifier_wall_clock_capacity_milliseconds"] != 9600000:
        raise ValueError("aggregate verifier capacity changed")
    if envelope["orchestration_wall_clock_capacity_milliseconds_by_attempt"] != (
        [600000] * 16
    ):
        raise ValueError("orchestration capacity schedule changed")
    if envelope["aggregate_orchestration_wall_clock_capacity_milliseconds"] != (
        9600000
    ):
        raise ValueError("aggregate orchestration capacity changed")
    if envelope["context_isolation_receipt_slots"] != 16:
        raise ValueError("context receipt slots changed")
    if envelope["predecessor_reconciliation_slots"] != 16:
        raise ValueError("predecessor reconciliation slots changed")
    if envelope["predecessor_reconciliation_not_applicable"] != (
        "EMIT_TYPED_NOT_APPLICABLE_EVENT"
    ):
        raise ValueError("not-applicable event changed")
    if envelope["equality_rule"] != (
        "EVERY_ARM_RECEIVES_THIS_EXACT_REGISTERED_CAPACITY_VECTOR"
    ):
        raise ValueError("envelope equality changed")
    if envelope["unused_capacity"] != (
        "EXPIRES_WITH_ITS_REGISTERED_SLOT_AND_CANNOT_MOVE_TO_ANOTHER_SLOT_ARM_PROBLEM_OR_RERUN"
    ):
        raise ValueError("unused capacity changed")
    if envelope["residual_budget"] != (
        "CANNOT_AUTHORIZE_POSTHOC_CALLS_RETRIES_SELECTION_OR_REPAIR"
    ):
        raise ValueError("residual budget changed")

    request = policy["request_overflow_policy"]
    if request["applies_before_every_dispatch"] is not True:
        raise ValueError("request preflight changed")
    if request["exact_rendered_request_includes"] != (
        "FROZEN_PROMPT_SOURCE_AND_ALL_ARM_PERMITTED_INJECTED_PRODUCT_BYTES"
    ):
        raise ValueError("request accounting surface changed")
    if request["over_per_attempt_or_aggregate_request_capacity"] != (
        "BLOCKED_CELL_BEFORE_PROVIDER_DISPATCH"
    ):
        raise ValueError("request overrun changed")
    if set(request["forbidden_recovery"]) != {
        "TRUNCATE_REQUEST",
        "DROP_OR_SUMMARIZE_ADMITTED_PRODUCT",
        "REORDER_ADMITTED_PRODUCTS",
        "NORMALIZE_OR_REGENERATE_BYTES",
        "BORROW_CAPACITY_FROM_LATER_SLOT_OR_OTHER_ARM",
        "ADD_RETRY_OR_REPLACEMENT_ATTEMPT",
    }:
        raise ValueError("request overflow recovery changed")
    if request["terminal_effect"] != (
        "BLOCKED_NOT_UNSOLVED_AND_NEVER_EXCLUDED_FROM_THE_PAIRED_COHORT"
    ):
        raise ValueError("request overflow terminal effect changed")

    response = policy["response_overflow_policy"]
    if response != {
        "provider_generation_limit": "EXACT_REGISTERED_ATTEMPT_RESPONSE_CAP",
        "returned_response_over_cap_invalid_utf8_or_truncated_measurement": (
            "BLOCKED"
        ),
        "unused_response_capacity": "NOT_TRANSFERABLE",
        "product_or_final_parser_failure": (
            "CHARGED_AS_THE_EXACT_RETURNED_BYTES_AND_TYPED_TERMINAL_OUTCOME"
        ),
    }:
        raise ValueError("response overflow policy changed")

    host = policy["verifier_and_orchestration_accounting"]
    if host != {
        "every_attempt_has_exactly_one_verifier_slot": True,
        "product_candidate_or_final_answer": (
            "ONE_BOUND_PINNED_RUNTIME_VERIFIER_INVOCATION"
        ),
        "no_answer_provider_error_or_noninvocable_response": (
            "ONE_TYPED_NOT_INVOKED_VERIFIER_EVENT_WITH_ZERO_VERIFIER_RUNTIME"
        ),
        "verifier_timeout_or_output_truncation": (
            "BLOCKED_AFTER_CHARGING_OBSERVED_RUNTIME"
        ),
        "every_attempt_has_exactly_one_orchestration_event": True,
        "wall_clock_source": "HOST_MONOTONIC_CLOCK",
        "duration_rule": (
            "MAX_ZERO_END_MONOTONIC_NS_MINUS_START_MONOTONIC_NS_"
            "CONVERTED_TO_INTEGER_MILLISECONDS_CEILING"
        ),
        "concurrency": (
            "ONE_MODEL_DISPATCH_AND_ONE_VERIFIER_PROCESS_PER_CELL_AT_A_TIME"
        ),
        "scheduling_and_counterbalancing": (
            "MUST_BE_FROZEN_BY_G1_121_BEFORE_DISPATCH"
        ),
        "unknown_negative_missing_or_unbound_duration": "BLOCKED",
    }:
        raise ValueError("host accounting changed")

    ledger = policy["event_ledger"]
    if ledger != {
        "schema": "supernova.complete-observable-cost-event.v1",
        "required_event_kinds": EXPECTED_EVENT_KINDS,
        "required_common_bindings": [
            "policy_sha256",
            "protocol_sha256",
            "manifest_sha256",
            "run_id",
            "problem_id",
            "full_problem_sha256",
            "arm",
            "attempt_index",
            "dispatch_sha256",
            "event_kind",
            "registered_capacity_sha256",
            "started_monotonic_ns",
            "ended_monotonic_ns",
            "issuer_id",
            "host_execution_record_sha256",
            "signature",
        ],
        "model_call_bindings": [
            "request_artifact_sha256",
            "request_utf8_bytes",
            "response_artifact_sha256",
            "response_utf8_bytes",
            "model_identity_sha256",
            "generation_settings_sha256",
            "provider_completion_id_or_typed_absence",
        ],
        "verifier_bindings": [
            "invocation_or_typed_not_invoked",
            "constructed_source_sha256_or_typed_absence",
            "runtime_contract_git_blob_sha1",
            "verifier_result",
            "verifier_wall_clock_milliseconds",
        ],
        "orchestration_bindings": [
            "orchestration_wall_clock_milliseconds",
        ],
        "duplicate_missing_replayed_cross_cell_unregistered_or_bad_signature": (
            "BLOCKED"
        ),
    }:
        raise ValueError("event ledger changed")

    reconciliation = policy["reconciliation"]
    if reconciliation != {
        "requires_all_16_attempts_and_all_5_event_kinds_per_attempt": True,
        "expected_model_calls": 16,
        "expected_verifier_slots": 16,
        "expected_orchestration_slots": 16,
        "exact_actual_totals": [
            "model_calls",
            "request_utf8_bytes",
            "response_utf8_bytes",
            "verifier_invocations",
            "typed_not_invoked_verifier_slots",
            "verifier_wall_clock_milliseconds",
            "orchestration_wall_clock_milliseconds",
        ],
        "unknown_measurement": "BLOCKED",
        "capacity_overrun": "BLOCKED",
        "unregistered_retry_product_promotion_or_cost_event": "BLOCKED",
        "missing_provider_usage_metadata": (
            "RECORDED_AS_UNAVAILABLE_NOT_ZERO_AND_DOES_NOT_INVALIDATE_"
            "THE_PRIMARY_VISIBLE_UTF8_BYTE_BASIS"
        ),
        "early_solved_cell": (
            "MUST_STILL_COMPLETE_AND_RECONCILE_ALL_16_REGISTERED_ATTEMPTS"
        ),
        "terminal_priority": ["BLOCKED", "INCOMPLETE", "COMPLETE"],
    }:
        raise ValueError("reconciliation changed")

    analysis = policy["primary_and_robustness_analysis"]
    if analysis != {
        "primary": "FULL_16_CALL_COMMON_ENVELOPE",
        "cumulative_prefix_checkpoints": EXPECTED_CHECKPOINTS,
        "checkpoint_role": (
            "PREREGISTERED_ROBUSTNESS_FRONTIER_NOT_AN_ALTERNATE_POSTHOC_PRIMARY"
        ),
        "matching": (
            "COMPARE_ALL_FIVE_PAIRED_OUTCOMES_AT_THE_SAME_REGISTERED_PREFIX_CAPACITY_VECTOR"
        ),
        "realized_usage_matching_or_pair_exclusion": "FORBIDDEN",
        "overrun_pair_exclusion": "FORBIDDEN_OVERRUN_BLOCKS_THE_COHORT",
        "favorable_checkpoint_selection": "FORBIDDEN",
    }:
        raise ValueError("analysis cost matching changed")

    limitations = policy["limitations"]
    if set(limitations["unobserved"]) != {
        "HIDDEN_REASONING_TOKENS_OR_STEPS",
        "PROVIDER_CACHE_EFFECTS",
        "PROVIDER_QUEUE_DELAY",
        "SHARED_SERVING_COMPUTE",
        "HARDWARE_ENERGY",
    }:
        raise ValueError("unobserved limitation changed")
    if limitations["report_requirement"] != (
        "REPORT_THE_REGISTERED_ENVELOPE_REALIZED_USAGE_VECTOR_AND_ALL_UNOBSERVED_LIMITATIONS_WITHOUT_TRANSLATING_BYTES_TO_TOKENS_OR_PHYSICAL_COMPUTE"
    ):
        raise ValueError("report requirement changed")


class ConfirmatoryCostPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load(POLICY_PATH)

    def test_checked_in_bytes_are_frozen_cross_platform(self) -> None:
        self.assertEqual(
            EXPECTED_POLICY_BLOB_SHA1,
            git_blob_sha1(canonical_lf(POLICY_PATH)),
        )

    def test_policy_is_closed(self) -> None:
        validate_policy(self.policy)

    def test_realized_cost_or_provider_tokens_cannot_be_claimed_equal(self) -> None:
        for forbidden in ["EQUAL_REALIZED_COST", "EQUAL_PROVIDER_TOKENS"]:
            changed = copy.deepcopy(self.policy)
            changed["claim_boundary"]["forbidden"].remove(forbidden)
            with self.assertRaisesRegex(ValueError, "forbidden claims"):
                validate_policy(changed)

    def test_all_arms_receive_the_same_registered_envelope(self) -> None:
        self.assertEqual(
            98304,
            sum(self.policy["common_envelope"][
                "response_capacity_utf8_bytes_by_attempt"
            ]),
        )
        self.assertEqual(
            [1, 2, 4, 8, 16],
            [
                row["completed_attempt_prefix"]
                for row in self.policy["primary_and_robustness_analysis"][
                    "cumulative_prefix_checkpoints"
                ]
            ],
        )

    def test_product_injection_cannot_trigger_silent_truncation_or_subsidy(self) -> None:
        for recovery in ["TRUNCATE_REQUEST", "DROP_OR_SUMMARIZE_ADMITTED_PRODUCT"]:
            changed = copy.deepcopy(self.policy)
            changed["request_overflow_policy"]["forbidden_recovery"].remove(recovery)
            with self.assertRaisesRegex(ValueError, "request overflow recovery"):
                validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["request_overflow_policy"][
            "over_per_attempt_or_aggregate_request_capacity"
        ] = "TRUNCATE_AND_DISPATCH"
        with self.assertRaisesRegex(ValueError, "request overrun"):
            validate_policy(changed)

    def test_host_duration_and_realized_totals_are_closed(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["verifier_and_orchestration_accounting"]["duration_rule"] = (
            "CALLER_SUPPLIED"
        )
        with self.assertRaisesRegex(ValueError, "host accounting"):
            validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["reconciliation"]["exact_actual_totals"] = []
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            validate_policy(changed)

    def test_unknown_overrun_or_missing_event_never_becomes_zero_or_excluded(self) -> None:
        for field in ["unknown_measurement", "capacity_overrun"]:
            changed = copy.deepcopy(self.policy)
            changed["reconciliation"][field] = "ZERO"
            with self.assertRaisesRegex(ValueError, field.replace("_", " ")):
                validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["primary_and_robustness_analysis"]["overrun_pair_exclusion"] = (
            "DROP_PAIR"
        )
        with self.assertRaisesRegex(ValueError, "analysis cost matching"):
            validate_policy(changed)

    def test_verifier_and_orchestration_ledger_bindings_are_required(self) -> None:
        for section, field in [
            ("required_common_bindings", "started_monotonic_ns"),
            ("verifier_bindings", "runtime_contract_git_blob_sha1"),
            ("verifier_bindings", "verifier_wall_clock_milliseconds"),
            ("orchestration_bindings", "orchestration_wall_clock_milliseconds"),
        ]:
            changed = copy.deepcopy(self.policy)
            changed["event_ledger"][section].remove(field)
            with self.assertRaisesRegex(ValueError, "event ledger"):
                validate_policy(changed)

    def test_ledger_cannot_lose_identity_or_usage_bindings(self) -> None:
        for field in [
            "full_problem_sha256",
            "arm",
            "attempt_index",
            "registered_capacity_sha256",
            "host_execution_record_sha256",
        ]:
            changed = copy.deepcopy(self.policy)
            changed["event_ledger"]["required_common_bindings"].remove(field)
            with self.assertRaisesRegex(ValueError, "event ledger"):
                validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["event_ledger"]["model_call_bindings"].remove(
            "request_utf8_bytes"
        )
        with self.assertRaisesRegex(ValueError, "event ledger"):
            validate_policy(changed)

    def test_unused_or_residual_capacity_cannot_move(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["common_envelope"]["unused_capacity"] = "REALLOCATE"
        with self.assertRaisesRegex(ValueError, "unused capacity"):
            validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["common_envelope"]["residual_budget"] = "EXTRA_RETRY"
        with self.assertRaisesRegex(ValueError, "residual budget"):
            validate_policy(changed)

    def test_realized_usage_cannot_drive_matching_or_checkpoint_selection(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["primary_and_robustness_analysis"][
            "realized_usage_matching_or_pair_exclusion"
        ] = "ALLOWED"
        with self.assertRaisesRegex(ValueError, "analysis cost matching"):
            validate_policy(changed)
        changed = copy.deepcopy(self.policy)
        changed["primary_and_robustness_analysis"][
            "favorable_checkpoint_selection"
        ] = "ALLOWED"
        with self.assertRaisesRegex(ValueError, "analysis cost matching"):
            validate_policy(changed)


if __name__ == "__main__":
    unittest.main()
