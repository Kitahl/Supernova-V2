from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "goal1" / "CONFIRMATORY_VERIFIED_CHAIN.json"
CONTROLS_PATH = ROOT / "goal1" / "CONFIRMATORY_PRODUCT_CONTROLS.json"
RUNTIME_PATH = ROOT / "goal1" / "CONFIRMATORY_RUNTIME.json"

EXPECTED_CONTRACT_BLOB_SHA1 = "091aed3fa33f0db899f0dd53bad1706aafff0c6c"
EXPECTED_CONTROLS_BLOB_SHA1 = "ab2b298e29022f931ec141bdba50485b0967ad3f"
EXPECTED_RUNTIME_BLOB_SHA1 = "1fa7caeefdf3c01cea5603f4865c2f9eec11a0fb"
EXPECTED_SURFACE_SHA256 = (
    "f68045d4d9554b0639b4abae86658c6bacee3f29ef1cc4b5c4f5deac7b654ed7"
)
EXPECTED_SOLE_DIFFERENCE = (
    "USE_OF_THE_IDENTICAL_PRODUCT_VERIFIER_RESULT_FOR_ADMISSION_"
    "PRODUCT_ONLY_IGNORES_IT_VERIFIED_CHAIN_REQUIRES_ADMISSIBLE_PASS"
)
EXPECTED_GRAPH = [
    {
        "attempt_index": index,
        "predecessor_completion_of_attempt": None if index == 0 else index - 1,
    }
    for index in range(16)
]
EXPECTED_PRODUCT_RECEIPT_BINDINGS = [
    "response_kind",
    "exact_response_sha256",
    "request_artifact_sha256",
    "dispatch_sha256",
    "ordered_predecessor_product_sha256",
    "product_harness_sha256",
    "runtime_contract_git_blob_sha1",
    "runtime_probe_sha256",
    "run_id",
    "problem_id",
    "full_problem_sha256",
    "arm",
    "attempt_index",
    "context_receipt_sha256",
    "predecessor_completion_sha256",
    "host_execution_record_sha256",
    "verifier_result",
    "printed_axioms",
    "receipt_signature",
]
EXPECTED_FINAL_RECEIPT_BINDINGS = [
    "frozen_target_source_sha256",
    "unique_proof_hole_start_byte",
    "unique_proof_hole_end_byte",
    "ordered_admitted_product_response_sha256",
    "exact_final_answer_response_sha256",
    "constructed_final_source_sha256",
    "request_artifact_sha256",
    "dispatch_sha256",
    "runtime_contract_git_blob_sha1",
    "host_execution_record_sha256",
    "verifier_result",
    "printed_axioms",
    "receipt_signature",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def projection_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_contract(contract: object, controls: dict, runtime: dict) -> None:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if set(contract) != {
        "schema_version",
        "contract_id",
        "status",
        "arm",
        "estimand_role",
        "scientific_credit",
        "authorities",
        "shared_surface_binding",
        "execution",
        "response_contract",
        "product_declaration_policy",
        "harness_construction",
        "product_admission",
        "product_receipt",
        "model_visible_memory",
        "final_answer",
        "terminal_result",
        "cost_boundary",
    }:
        raise ValueError("top-level fields changed")
    if contract["schema_version"] != 1:
        raise ValueError("schema version changed")
    if contract["contract_id"] != "goal1-confirmatory-verified-chain-v1":
        raise ValueError("contract identity changed")
    if contract["status"] != "FROZEN" or contract["arm"] != "verified_chain":
        raise ValueError("frozen arm identity changed")
    if contract["estimand_role"] != "TREATMENT_ARM_DEFINITION_ONLY":
        raise ValueError("estimand role changed")
    if contract["scientific_credit"] != (
        "NONE_UNTIL_SEALED_BY_CONFIRMATORY_PROTOCOL_AND_EXECUTED_WITH_ADMISSIBLE_EVIDENCE"
    ):
        raise ValueError("scientific-credit boundary changed")

    authorities = contract["authorities"]
    if git_blob_sha1(canonical_lf(CONTROLS_PATH)) != EXPECTED_CONTROLS_BLOB_SHA1:
        raise ValueError("product-control bytes drifted")
    if authorities["product_controls"] != {
        "path": "goal1/CONFIRMATORY_PRODUCT_CONTROLS.json",
        "git_blob_sha1": EXPECTED_CONTROLS_BLOB_SHA1,
        "shared_surface_field": "shared.product_chain_shared_surface.projection",
        "shared_surface_projection_sha256": EXPECTED_SURFACE_SHA256,
    }:
        raise ValueError("product-control authority changed")
    if git_blob_sha1(canonical_lf(RUNTIME_PATH)) != EXPECTED_RUNTIME_BLOB_SHA1:
        raise ValueError("runtime bytes drifted")
    if authorities["runtime"] != {
        "path": "goal1/CONFIRMATORY_RUNTIME.json",
        "git_blob_sha1": EXPECTED_RUNTIME_BLOB_SHA1,
        "runtime_id": "goal1-confirmatory-lean-runtime-v1",
    }:
        raise ValueError("runtime authority changed")
    if authorities["future_cost_policy"] != {
        "path": "goal1/CONFIRMATORY_COST_POLICY.json",
        "required_status": "FROZEN",
        "missing_or_digest_mismatch": "BLOCKED",
    }:
        raise ValueError("future cost-policy authority changed")

    surface = controls["shared"]["product_chain_shared_surface"]
    if projection_sha256(surface["projection"]) != EXPECTED_SURFACE_SHA256:
        raise ValueError("product-control projection bytes drifted")
    if surface["projection_sha256"] != EXPECTED_SURFACE_SHA256:
        raise ValueError("product-control projection declaration drifted")
    binding = contract["shared_surface_binding"]
    if binding != {
        "attempt_indices": list(range(16)),
        "exact_projection_sha256": EXPECTED_SURFACE_SHA256,
        "must_equal_product_control_projection": True,
        "sole_permitted_arm_difference": EXPECTED_SOLE_DIFFERENCE,
        "mismatch": "BLOCKED",
    }:
        raise ValueError("shared surface binding changed")

    execution = contract["execution"]
    if execution["dispatch_policy"] != (
        "REGISTER_ALL_16_SLOTS_AND_THE_EXACT_LINEAR_PREDECESSOR_GRAPH_BEFORE_FIRST_MODEL_CALL"
    ):
        raise ValueError("dispatch policy changed")
    if execution["completion_policy"] != (
        "EXECUTE_AND_RECONCILE_ALL_16_EVEN_AFTER_A_VERIFIED_FINAL_PASS"
    ):
        raise ValueError("completion policy changed")
    if execution["fresh_context_scope"] != (
        "ONE_NEW_EMPTY_MODEL_CONTEXT_PER_PROBLEM_ARM_ATTEMPT"
    ):
        raise ValueError("fresh-context scope changed")
    if execution["admissible_context_receipt_modes"] != [
        "PROVIDER_ATTESTED_EMPTY_CONTEXT",
        "HERMETIC_LOCAL_INSTANCE",
    ]:
        raise ValueError("context receipt modes changed")
    if execution["recurring_chat_without_provider_fresh_context_attestation"] != (
        "NON_CREDIT_ONLY"
    ):
        raise ValueError("recurring-chat boundary changed")
    if execution["predecessor_graph"] != EXPECTED_GRAPH:
        raise ValueError("predecessor graph changed")
    if execution["successor_requires_authenticated_terminal_predecessor_completion"] is not True:
        raise ValueError("successor authentication changed")
    if execution["predecessor_completion_metadata_model_visible"] is not False:
        raise ValueError("predecessor metadata visibility changed")

    response = contract["response_contract"]
    if response != surface["projection"]["response_discriminator"] | {
        "allowed_kinds": ["PRODUCT_CANDIDATE", "FINAL_ANSWER", "NO_ANSWER"]
    }:
        raise ValueError("response surface changed")
    if contract["product_declaration_policy"] != surface["projection"][
        "product_declaration_policy"
    ] | {"unique_name_problem_arm_attempt_binding_required": True}:
        # The shared projection uses a shorter binding key; all other exact fields must match.
        declaration = copy.deepcopy(contract["product_declaration_policy"])
        declaration.pop("unique_name_problem_arm_attempt_binding_required", None)
        shared_declaration = copy.deepcopy(surface["projection"]["product_declaration_policy"])
        shared_declaration.pop("unique_name_and_attempt_binding_required", None)
        if declaration != shared_declaration:
            raise ValueError("product declaration surface changed")
    if contract["product_declaration_policy"][
        "unique_name_problem_arm_attempt_binding_required"
    ] is not True:
        raise ValueError("product identity binding changed")

    harness = contract["harness_construction"]
    if harness["product_harness"] != surface["projection"]["product_harness"]:
        raise ValueError("product harness changed")
    if harness["final_harness"] != surface["projection"]["final_harness"]:
        raise ValueError("final harness changed")
    if harness["transformation"] != surface["projection"]["construction_transformation"]:
        raise ValueError("construction transformation changed")
    if harness["target_statement_or_import_mutation"] != "BLOCKED":
        raise ValueError("statement-fidelity boundary changed")

    admission = contract["product_admission"]
    if admission["verifier_invocation"] != surface["projection"][
        "product_verifier_invocation"
    ]:
        raise ValueError("product verifier invocation changed")
    if admission["required_result"] != "PASS":
        raise ValueError("product admission result changed")
    if admission["required_runtime_id"] != runtime["runtime_id"]:
        raise ValueError("product runtime changed")
    if admission["required_axiom_allowlist_exact"] != runtime["verifier"][
        "axiom_policy"
    ]["allowed_exact"]:
        raise ValueError("axiom allowlist changed")
    if admission["forbidden_axiom_exact"] != runtime["verifier"]["axiom_policy"][
        "forbidden_exact"
    ]:
        raise ValueError("forbidden axiom changed")
    required_admission = set(admission["admission_requires"])
    for item in {
        "AUTHENTICATED_CONTEXT_RECEIPT",
        "AUTHENTICATED_PREDECESSOR_COMPLETION_BINDING",
        "AUTHENTICATED_HOST_EXECUTION_RECORD",
        "BOUND_PRODUCT_VERIFIER_RECEIPT",
        "PINNED_RUNTIME_PASS",
        "PRINTED_AXIOMS_SUBSET_OF_RUNTIME_ALLOWLIST",
    }:
        if item not in required_admission:
            raise ValueError("product admission evidence weakened")
    if admission["product_pass_is_terminal_solution"] is not False:
        raise ValueError("product terminal role changed")
    if admission["admission_by_model_claim_human_judgment_or_unbound_boolean"] != (
        "BLOCKED"
    ):
        raise ValueError("unbound admission changed")

    receipt = contract["product_receipt"]
    if receipt["schema"] != "supernova.verified-product-receipt.v1":
        raise ValueError("product receipt schema changed")
    if receipt["issuer"] != "HOST_VERIFIER_CONTROLLER_ONLY":
        raise ValueError("product receipt authority changed")
    if receipt["required_bindings"] != EXPECTED_PRODUCT_RECEIPT_BINDINGS:
        raise ValueError("product receipt bindings changed")
    if receipt["replay_cross_problem_cross_arm_cross_attempt_or_digest_mismatch"] != (
        "BLOCKED"
    ):
        raise ValueError("receipt replay boundary changed")

    memory = contract["model_visible_memory"]
    if memory["allowed"] != (
        "EXACT_PRODUCT_CANDIDATE_RESPONSE_BYTES_WITH_ADMISSIBLE_PASS_RECEIPTS_"
        "FROM_LOWER_ATTEMPTS_IN_THIS_EXACT_RUN_PROBLEM_AND_ARM"
    ):
        raise ValueError("model-visible memory changed")
    if memory["ordering"] != "STRICT_ASCENDING_PRODUCER_ATTEMPT_INDEX":
        raise ValueError("memory ordering changed")
    if memory["inclusion"] != "ALL_AND_ONLY_ADMITTED_PRODUCTS":
        raise ValueError("memory inclusion changed")
    if memory["exact_response_bytes_only"] is not True:
        raise ValueError("memory byte identity changed")
    for forbidden in {
        "PRODUCT_RECEIPT",
        "VERIFIER_STATUS",
        "FAIL_OR_ERROR_DIAGNOSTIC",
        "PREDECESSOR_COMPLETION_METADATA",
        "CROSS_PROBLEM_PRODUCT",
        "CROSS_ARM_PRODUCT",
        "UNADMITTED_PRODUCT",
    }:
        if forbidden not in set(memory["forbidden"]):
            raise ValueError("forbidden model-visible evidence removed")
    if memory["any_hidden_conversation_or_saved_memory"] != "BLOCKED":
        raise ValueError("hidden-memory boundary changed")

    final = contract["final_answer"]
    if final["verifier_invocation"] != surface["projection"][
        "final_verifier_invocation"
    ]:
        raise ValueError("final verifier invocation changed")
    if final["required_result"] != "PASS":
        raise ValueError("final result rule changed")
    if final["required_bindings"] != EXPECTED_FINAL_RECEIPT_BINDINGS:
        raise ValueError("final receipt bindings changed")
    if final["target_statement_or_import_mutation"] != "BLOCKED":
        raise ValueError("final statement-fidelity boundary changed")

    terminal = contract["terminal_result"]
    if terminal != {
        "solved": "AT_LEAST_ONE_OF_16_ATTEMPTS_HAS_AN_ADMISSIBLE_FINAL_KERNEL_PASS",
        "selected_attempt": (
            "LOWEST_ATTEMPT_INDEX_WITH_AN_ADMISSIBLE_FINAL_KERNEL_PASS_AFTER_ALL_16_COMPLETE"
        ),
        "no_passing_attempt": (
            "UNSOLVED_ONLY_WHEN_ALL_16_CELLS_AND_COST_EVENTS_ARE_COMPLETE_OTHERWISE_INCOMPLETE"
        ),
        "product_pass_alone_never_solves": True,
        "human_selection_or_posthoc_rerun": "FORBIDDEN",
        "decision_priority": ["BLOCKED", "INCOMPLETE", "SOLVED", "UNSOLVED"],
    }:
        raise ValueError("terminal result changed")

    cost = contract["cost_boundary"]
    if cost["model_calls"] != 16 or cost["orchestration_events"] != 16:
        raise ValueError("common attempt cost changed")
    if cost["product_candidate_and_final_answer"] != (
        "CHARGE_ONE_IDENTICAL_BOUND_LEAN_VERIFIER_EVENT"
    ):
        raise ValueError("verifier cost changed")
    if cost["no_answer_or_error"] != (
        "CHARGE_ONE_TYPED_NOT_INVOKED_VERIFIER_SLOT"
    ):
        raise ValueError("typed verifier slot changed")
    if cost["unknown_or_unreconciled_event"] != "BLOCKED":
        raise ValueError("unknown cost handling changed")
    if cost["complete_cost_rule"] != (
        "MUST_EQUAL_THE_FROZEN_G1_126_COMMON_COMPLETE_COST_BASIS_AND_CEILING"
    ):
        raise ValueError("complete-cost binding changed")


class ConfirmatoryVerifiedChainContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load(CONTRACT_PATH)
        cls.controls = load(CONTROLS_PATH)
        cls.runtime = load(RUNTIME_PATH)

    def test_checked_in_bytes_are_frozen_cross_platform(self) -> None:
        self.assertEqual(
            EXPECTED_CONTRACT_BLOB_SHA1,
            git_blob_sha1(canonical_lf(CONTRACT_PATH)),
        )

    def test_contract_is_closed(self) -> None:
        validate_contract(self.contract, self.controls, self.runtime)

    def test_shared_surface_is_exactly_bound(self) -> None:
        surface = self.controls["shared"]["product_chain_shared_surface"]
        self.assertEqual(EXPECTED_SURFACE_SHA256, projection_sha256(surface["projection"]))
        self.assertEqual(
            EXPECTED_SURFACE_SHA256,
            self.contract["shared_surface_binding"]["exact_projection_sha256"],
        )

    def test_nonpassing_product_cannot_be_admitted(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["product_admission"]["required_result"] = "ANY_TERMINAL_RESULT"
        with self.assertRaisesRegex(ValueError, "admission result"):
            validate_contract(changed, self.controls, self.runtime)
        changed = copy.deepcopy(self.contract)
        changed["product_admission"]["admission_requires"].remove(
            "BOUND_PRODUCT_VERIFIER_RECEIPT"
        )
        with self.assertRaisesRegex(ValueError, "evidence weakened"):
            validate_contract(changed, self.controls, self.runtime)

    def test_receipt_cannot_lose_identity_or_execution_binding(self) -> None:
        for field in [
            "full_problem_sha256",
            "arm",
            "attempt_index",
            "host_execution_record_sha256",
            "ordered_predecessor_product_sha256",
        ]:
            changed = copy.deepcopy(self.contract)
            changed["product_receipt"]["required_bindings"].remove(field)
            with self.assertRaisesRegex(ValueError, "receipt bindings"):
                validate_contract(changed, self.controls, self.runtime)

    def test_verifier_evidence_cannot_enter_model_visible_memory(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["model_visible_memory"]["forbidden"].remove("VERIFIER_STATUS")
        with self.assertRaisesRegex(ValueError, "forbidden model-visible evidence"):
            validate_contract(changed, self.controls, self.runtime)
        changed = copy.deepcopy(self.contract)
        changed["model_visible_memory"]["allowed"] = "ANY_PRIOR_PRODUCT"
        with self.assertRaisesRegex(ValueError, "model-visible memory"):
            validate_contract(changed, self.controls, self.runtime)

    def test_recurring_chat_without_attestation_is_non_credit(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["execution"][
            "recurring_chat_without_provider_fresh_context_attestation"
        ] = "ADMISSIBLE"
        with self.assertRaisesRegex(ValueError, "recurring-chat boundary"):
            validate_contract(changed, self.controls, self.runtime)

    def test_product_pass_cannot_solve_and_all_slots_must_complete(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["product_admission"]["product_pass_is_terminal_solution"] = True
        with self.assertRaisesRegex(ValueError, "product terminal role"):
            validate_contract(changed, self.controls, self.runtime)
        changed = copy.deepcopy(self.contract)
        changed["execution"]["completion_policy"] = "STOP_AFTER_FIRST_PASS"
        with self.assertRaisesRegex(ValueError, "completion policy"):
            validate_contract(changed, self.controls, self.runtime)

    def test_axiom_and_statement_boundaries_cannot_be_weakened(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["product_admission"]["required_axiom_allowlist_exact"].append("sorryAx")
        with self.assertRaisesRegex(ValueError, "axiom allowlist"):
            validate_contract(changed, self.controls, self.runtime)
        changed = copy.deepcopy(self.contract)
        changed["harness_construction"]["target_statement_or_import_mutation"] = (
            "ALLOWED"
        )
        with self.assertRaisesRegex(ValueError, "statement-fidelity"):
            validate_contract(changed, self.controls, self.runtime)

    def test_verifier_or_complete_cost_cannot_be_removed(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["cost_boundary"]["product_candidate_and_final_answer"] = (
            "NO_VERIFIER_COST"
        )
        with self.assertRaisesRegex(ValueError, "verifier cost"):
            validate_contract(changed, self.controls, self.runtime)
        changed = copy.deepcopy(self.contract)
        changed["cost_boundary"]["unknown_or_unreconciled_event"] = "ZERO"
        with self.assertRaisesRegex(ValueError, "unknown cost"):
            validate_contract(changed, self.controls, self.runtime)


if __name__ == "__main__":
    unittest.main()
