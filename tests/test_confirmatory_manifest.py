from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
import unittest

from supernova_goal1.confirmatory_manifest import (
    BLOCKED_NO_EXECUTION_AUTHORITY,
    CANONICAL_ARMS,
    CONFIRMATORY_MANIFEST_SCHEMA,
    EXPECTED_CELLS,
    EXPECTED_DISPATCH_RECORDS,
    EXPECTED_PROTOCOL_RULES_SHA256,
    EXPECTED_REPORT_PROBLEMS,
    NON_CREDIT_DRAFT,
    OPERATOR_PLAN_SCHEMA,
    assert_dispatch_authorized,
    build_confirmatory_manifest,
    build_non_credit_draft,
    canonical_sha256,
    paired_arm_counts,
    validate_draft_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json"
OPERATOR_SEED = bytes.fromhex(
    "9f4ca7d33bc2a179076768b2ea3fc6f5991ec8c0f3a91bd90ef33e5346f47721"
)


class ConfirmatoryManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        cls.bundle = build_non_credit_draft(
            cls.protocol,
            operator_seed=OPERATOR_SEED,
        )
        cls.public = cls.bundle.public_manifest
        cls.operator = cls.bundle.operator_plan

    def test_draft_is_deterministic_and_binds_exact_sealed_rules(self) -> None:
        rebuilt = build_non_credit_draft(
            self.protocol,
            operator_seed=OPERATOR_SEED,
        )
        self.assertEqual(self.public["manifest_sha256"], rebuilt.public_manifest["manifest_sha256"])
        self.assertEqual(
            self.operator["operator_plan_sha256"],
            rebuilt.operator_plan["operator_plan_sha256"],
        )
        self.assertEqual(
            EXPECTED_PROTOCOL_RULES_SHA256,
            self.public["protocol_rules_sha256"],
        )
        self.assertEqual(
            EXPECTED_PROTOCOL_RULES_SHA256,
            self.operator["protocol_rules_sha256"],
        )
        validate_draft_bundle(self.public, self.operator, self.protocol)

    def test_complete_one_shot_shape_is_preregistered(self) -> None:
        self.assertEqual(CONFIRMATORY_MANIFEST_SCHEMA, self.public["schema"])
        self.assertEqual(OPERATOR_PLAN_SCHEMA, self.operator["schema"])
        self.assertEqual(
            {
                "attempts_per_problem_arm": 16,
                "dispatch_records": EXPECTED_DISPATCH_RECORDS,
                "paired_cells": EXPECTED_CELLS,
                "report_problems": EXPECTED_REPORT_PROBLEMS,
            },
            self.public["counts"],
        )
        self.assertEqual(EXPECTED_DISPATCH_RECORDS, len(self.public["public_records"]))
        self.assertEqual(EXPECTED_DISPATCH_RECORDS, len(self.operator["entries"]))
        self.assertEqual(0, self.operator["entries"][0]["dispatch_index"])
        self.assertEqual(
            EXPECTED_DISPATCH_RECORDS - 1,
            self.operator["entries"][-1]["dispatch_index"],
        )

    def test_every_problem_attempt_has_exactly_all_five_arms(self) -> None:
        observed: dict[tuple[str, int], list[str]] = {}
        for entry in self.operator["entries"]:
            key = (entry["problem_id"], entry["budget_attempt_index"])
            observed.setdefault(key, []).append(entry["arm"])
        self.assertEqual(EXPECTED_REPORT_PROBLEMS * 16, len(observed))
        for arms in observed.values():
            self.assertEqual(set(CANONICAL_ARMS), set(arms))
            self.assertEqual(5, len(arms))
        counts = paired_arm_counts(self.operator)
        self.assertEqual(EXPECTED_DISPATCH_RECORDS, sum(counts.values()))
        self.assertTrue(all(count == 1 for count in counts.values()))

    def test_schedule_matches_frozen_rotation_and_round_robin(self) -> None:
        entries = self.operator["entries"]
        self.assertEqual(list(CANONICAL_ARMS), [row["arm"] for row in entries[:5]])
        self.assertEqual(
            list(CANONICAL_ARMS[1:] + CANONICAL_ARMS[:1]),
            [row["arm"] for row in entries[5:10]],
        )
        self.assertEqual(0, entries[0]["budget_attempt_index"])
        self.assertEqual(1, entries[EXPECTED_CELLS]["budget_attempt_index"])
        self.assertEqual(entries[0]["problem_id"], entries[EXPECTED_CELLS]["problem_id"])
        self.assertEqual(
            list(CANONICAL_ARMS),
            [
                row["arm"]
                for row in entries[EXPECTED_CELLS : EXPECTED_CELLS + 5]
            ],
        )

    def test_public_payload_is_structurally_blinded(self) -> None:
        for record in self.public["public_records"]:
            self.assertNotIn("arm", record)
            self.assertNotIn("dispatch_id", record)
            self.assertNotIn("eligible_predecessor_dispatch_ids", record)
            self.assertNotIn("selected_predecessor_dispatch_id", record)
        self.assertEqual(
            "OPAQUE_IDS_AND_ORDER_BOUND_TO_OPERATOR_ONLY_256_BIT_SEED",
            self.public["blinding"]["classification"],
        )
        self.assertIs(False, self.public["blinding"]["public_records_contain_arm"])
        public_ids = {
            record["evaluation_id"] for record in self.public["public_records"]
        }
        operator_ids = {entry["evaluation_id"] for entry in self.operator["entries"]}
        self.assertEqual(public_ids, operator_ids)

    def test_public_order_no_longer_reveals_arm_from_frozen_schedule(self) -> None:
        report_problem_ids = self.protocol["sealed_rules"]["benchmark_selection"][
            "report_split"
        ]["problem_ids"]
        problem_index = {
            problem_id: index for index, problem_id in enumerate(report_problem_ids)
        }
        truth = {
            entry["evaluation_id"]: entry["arm"] for entry in self.operator["entries"]
        }
        old_schedule_guess_matches = 0
        for record in self.public["public_records"]:
            guessed_position = record["evaluation_index"] % 5
            guessed_arm = CANONICAL_ARMS[
                (problem_index[record["problem_id"]] % 5 + guessed_position) % 5
            ]
            old_schedule_guess_matches += (
                guessed_arm == truth[record["evaluation_id"]]
            )
        self.assertLess(
            old_schedule_guess_matches,
            EXPECTED_DISPATCH_RECORDS // 2,
        )
        self.assertNotEqual(
            [entry["evaluation_id"] for entry in self.operator["entries"]],
            [record["evaluation_id"] for record in self.public["public_records"]],
        )
        self.assertNotIn("operator_seed_hex", self.public)
        self.assertRegex(
            self.public["blinding"]["operator_seed_commitment_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_seed_changes_only_blinding_not_scientific_dispatch_plan(self) -> None:
        other = build_non_credit_draft(
            self.protocol,
            operator_seed=bytes.fromhex("01" * 32),
        )
        self.assertNotEqual(
            self.public["manifest_sha256"],
            other.public_manifest["manifest_sha256"],
        )
        self.assertNotEqual(
            {row["evaluation_id"] for row in self.public["public_records"]},
            {row["evaluation_id"] for row in other.public_manifest["public_records"]},
        )
        fields = (
            "dispatch_id",
            "dispatch_index",
            "problem_id",
            "family_id",
            "arm",
            "arm_position",
            "budget_attempt_index",
            "eligible_predecessor_attempt_indices",
            "eligible_predecessor_dispatch_ids",
            "predecessor_policy",
            "selected_predecessor_attempt_index",
            "selected_predecessor_dispatch_id",
            "retry_allowance",
        )
        self.assertEqual(
            [
                tuple(entry[field] for field in fields)
                for entry in self.operator["entries"]
            ],
            [
                tuple(entry[field] for field in fields)
                for entry in other.operator_plan["entries"]
            ],
        )

    def test_linear_and_multifidelity_predecessors_are_exactly_registered(self) -> None:
        entries = {
            (entry["problem_id"], entry["arm"], entry["budget_attempt_index"]): entry
            for entry in self.operator["entries"]
        }
        problem_id = self.operator["entries"][0]["problem_id"]

        for arm in ("ordinary", "portfolio"):
            for attempt in range(16):
                entry = entries[(problem_id, arm, attempt)]
                self.assertEqual([], entry["eligible_predecessor_attempt_indices"])
                self.assertEqual([], entry["eligible_predecessor_dispatch_ids"])
                self.assertEqual("NONE_INDEPENDENT_ATTEMPT", entry["predecessor_policy"])
                self.assertIsNone(entry["selected_predecessor_attempt_index"])
                self.assertIsNone(entry["selected_predecessor_dispatch_id"])

        for arm in ("product_only", "verified_chain"):
            initial = entries[(problem_id, arm, 0)]
            self.assertEqual([], initial["eligible_predecessor_attempt_indices"])
            self.assertEqual(
                "FIXED_LINEAR_AUTHENTICATED_COMPLETION",
                initial["predecessor_policy"],
            )
            for attempt in range(1, 16):
                current = entries[(problem_id, arm, attempt)]
                predecessor = entries[(problem_id, arm, attempt - 1)]
                self.assertEqual(
                    [attempt - 1],
                    current["eligible_predecessor_attempt_indices"],
                )
                self.assertEqual(
                    [predecessor["dispatch_id"]],
                    current["eligible_predecessor_dispatch_ids"],
                )
                self.assertEqual(
                    attempt - 1,
                    current["selected_predecessor_attempt_index"],
                )
                self.assertEqual(
                    predecessor["dispatch_id"],
                    current["selected_predecessor_dispatch_id"],
                )

        expected_multifidelity_graph = {
            8: [0, 1],
            9: [2, 3],
            10: [4, 5],
            11: [6, 7],
            12: [8, 9],
            13: [10, 11],
            14: [12, 13],
            15: [14],
        }
        for attempt in range(16):
            current = entries[(problem_id, "multi_fidelity", attempt)]
            eligible_attempts = expected_multifidelity_graph.get(attempt, [])
            self.assertEqual(
                eligible_attempts,
                current["eligible_predecessor_attempt_indices"],
            )
            self.assertEqual(
                [
                    entries[
                        (problem_id, "multi_fidelity", predecessor_attempt)
                    ]["dispatch_id"]
                    for predecessor_attempt in eligible_attempts
                ],
                current["eligible_predecessor_dispatch_ids"],
            )
            self.assertEqual(
                "FROZEN_SUCCESSIVE_HALVING_ELIGIBLE_SET_SELECTION",
                current["predecessor_policy"],
            )
            self.assertIsNone(current["selected_predecessor_attempt_index"])
            self.assertIsNone(current["selected_predecessor_dispatch_id"])


    def test_no_post_manifest_retry_or_capacity_reallocation_exists(self) -> None:
        self.assertEqual(
            {
                "registered_attempt_indices": list(range(16)),
                "retry_allowance_per_dispatch_record": 0,
                "unregistered_or_post_manifest_retry": "BLOCKED",
                "unused_capacity_reallocation": "BLOCKED",
            },
            self.public["retry_policy"],
        )
        self.assertTrue(
            all(row["retry_allowance"] == 0 for row in self.public["public_records"])
        )
        self.assertTrue(
            all(row["retry_allowance"] == 0 for row in self.operator["entries"])
        )

    def test_all_required_bindings_are_derived_and_committed(self) -> None:
        bindings = self.public["bindings"]
        self.assertEqual(
            self.operator["operator_plan_sha256"],
            bindings["all_19520_dispatch_records_sha256"],
        )
        self.assertEqual(
            canonical_sha256(self.operator["entries"]),
            self.operator["operator_plan_sha256"],
        )
        public_identity = dict(self.public)
        manifest_sha256 = public_identity.pop("manifest_sha256")
        self.assertEqual(canonical_sha256(public_identity), manifest_sha256)
        self.assertEqual(manifest_sha256, self.operator["manifest_sha256"])
        for name in (
            "benchmark_selection_sha256",
            "family_map_sha256",
            "cost_policy_sha256",
            "runtime_sha256",
            "schedule_sha256",
        ):
            self.assertRegex(bindings[name], r"^[0-9a-f]{64}$")

    def test_absent_execution_authority_is_explicitly_non_credit_and_blocked(self) -> None:
        self.assertEqual(NON_CREDIT_DRAFT, self.public["purpose"])
        self.assertEqual(NON_CREDIT_DRAFT, self.public["credit_status"])
        self.assertEqual(BLOCKED_NO_EXECUTION_AUTHORITY, self.public["dispatch_status"])
        self.assertIsNone(self.public["bindings"]["execution_authority_sha256"])
        self.assertIsNone(self.public["bindings"]["model_identity_sha256"])
        with self.assertRaisesRegex(PermissionError, "BLOCKED_NO_EXECUTION_AUTHORITY"):
            assert_dispatch_authorized(self.public, self.operator, self.protocol)

    def test_production_manifest_cannot_be_requested_before_authority_ticket(self) -> None:
        fake_authority = {
            "schema": "self-asserted",
            "provider_attested_fresh_empty_context_capability": True,
        }
        with self.assertRaisesRegex(PermissionError, "activation-only"):
            build_confirmatory_manifest(
                self.protocol,
                operator_seed=OPERATOR_SEED,
                execution_authority=fake_authority,
            )

    def test_any_public_or_operator_mutation_is_rejected(self) -> None:
        changed_public = copy.deepcopy(self.public)
        changed_public["public_records"][0]["retry_allowance"] = 1
        with self.assertRaisesRegex(ValueError, "public confirmatory manifest"):
            validate_draft_bundle(changed_public, self.operator, self.protocol)

        changed_operator = copy.deepcopy(self.operator)
        changed_operator["entries"][0]["arm"] = "portfolio"
        with self.assertRaisesRegex(ValueError, "operator plan"):
            validate_draft_bundle(self.public, changed_operator, self.protocol)

    def test_protocol_drift_is_rejected_before_manifest_generation(self) -> None:
        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules"]["paired_design"]["attempts_per_problem_arm"] = 15
        with self.assertRaisesRegex(ValueError, "sealed_rules_sha256"):
            build_non_credit_draft(changed, operator_seed=OPERATOR_SEED)

        changed = copy.deepcopy(self.protocol)
        changed["sealed_rules_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "sealed_rules_sha256"):
            build_non_credit_draft(changed, operator_seed=OPERATOR_SEED)

    def test_bundle_is_json_serializable_without_custom_encoder(self) -> None:
        public_round_trip = json.loads(
            json.dumps(self.public, ensure_ascii=False, sort_keys=True)
        )
        operator_round_trip = json.loads(
            json.dumps(self.operator, ensure_ascii=False, sort_keys=True)
        )
        validate_draft_bundle(public_round_trip, operator_round_trip, self.protocol)


if __name__ == "__main__":
    unittest.main()
