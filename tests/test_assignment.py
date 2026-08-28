from __future__ import annotations

import sys
import unittest
from collections import defaultdict
from dataclasses import asdict, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.assignment import (
    Assignment,
    EvaluatorBlindOrder,
    OperatorRevealMap,
    blind_evaluation_order,
    operator_reveal_mapping,
    seeded_paired_assignment,
)
from supernova_goal1.contracts import Arm


class HostileStr(str):
    def __hash__(self) -> int:
        raise AssertionError("hostile identifier hash must never run")


class SeededPairedAssignmentTests(unittest.TestCase):
    def test_assignment_is_paired_and_reproducible(self) -> None:
        problem_ids = ["p-003", "p-001", "p-002"]
        first = seeded_paired_assignment(problem_ids, seed="assignment-seed-v1")
        second = seeded_paired_assignment(reversed(problem_ids), seed="assignment-seed-v1")
        self.assertEqual(first, second)
        self.assertEqual(15, len(first))
        self.assertEqual(list(range(15)), [item.execution_index for item in first])

        arms_by_problem: dict[str, set[Arm]] = defaultdict(set)
        for item in first:
            arms_by_problem[item.problem_id].add(item.arm)
        self.assertEqual(
            {problem_id: set(Arm) for problem_id in problem_ids}, arms_by_problem
        )

    def test_different_assignment_seed_changes_order_without_changing_pairs(self) -> None:
        problem_ids = ["p-001", "p-002", "p-003", "p-004"]
        first = seeded_paired_assignment(problem_ids, seed="seed-a")
        second = seeded_paired_assignment(problem_ids, seed="seed-b")
        self.assertNotEqual(
            [(item.problem_id, item.arm) for item in first],
            [(item.problem_id, item.arm) for item in second],
        )
        self.assertEqual(
            {(item.problem_id, item.arm) for item in first},
            {(item.problem_id, item.arm) for item in second},
        )

    def test_assignment_rejects_duplicate_problem_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            seeded_paired_assignment(["p-001", "p-001"], seed="seed")

    def test_assignment_rejects_str_subclass_problem_id_before_hashing(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact str"):
            seeded_paired_assignment([HostileStr("p-001")], seed="seed")

    def test_assignment_reproducibility_vector(self) -> None:
        assignments = seeded_paired_assignment(["p-001", "p-002"], seed="vector-v1")
        self.assertEqual(
            [
                ("p-002", "multi_fidelity"),
                ("p-002", "ordinary"),
                ("p-002", "portfolio"),
                ("p-002", "verified_chain"),
                ("p-001", "multi_fidelity"),
                ("p-001", "verified_chain"),
                ("p-001", "product_only"),
                ("p-002", "product_only"),
                ("p-001", "portfolio"),
                ("p-001", "ordinary"),
            ],
            [(item.problem_id, item.arm.value) for item in assignments],
        )


class BlindEvaluationOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assignments = seeded_paired_assignment(
            ["p-001", "p-002", "p-003"], seed="execution-seed"
        )

    def test_blind_order_is_reproducible_and_public_items_hide_arm_mapping(self) -> None:
        first = blind_evaluation_order(self.assignments, seed="blind-seed")
        second = blind_evaluation_order(self.assignments, seed="blind-seed")
        self.assertEqual(first, second)
        self.assertIsInstance(first, EvaluatorBlindOrder)
        self.assertEqual(15, len(first.items))
        self.assertEqual(list(range(15)), [item.evaluation_index for item in first.items])
        self.assertTrue(all(not hasattr(item, "arm") for item in first.items))
        self.assertTrue(all(not hasattr(item, "assignment_id") for item in first.items))
        self.assertTrue(all(item.evaluation_id.startswith("eval-") for item in first.items))

    def test_evaluator_object_has_no_reveal_or_assignment_authority(self) -> None:
        evaluator_material = blind_evaluation_order(self.assignments, seed="blind-seed")
        self.assertEqual(["items"], [field.name for field in fields(evaluator_material)])
        self.assertFalse(hasattr(evaluator_material, "reveal"))
        self.assertFalse(hasattr(evaluator_material, "entries"))
        self.assertFalse(hasattr(evaluator_material, "assignment_id"))
        self.assertFalse(hasattr(evaluator_material, "arm"))

        serialized = asdict(evaluator_material)
        for item in serialized["items"]:
            self.assertEqual(
                {"evaluation_id", "problem_id", "evaluation_index"}, set(item)
            )
            self.assertNotIn("assignment_id", item)
            self.assertNotIn("arm", item)

    def test_operator_reveal_requires_separate_interface_and_type(self) -> None:
        evaluator_material = blind_evaluation_order(self.assignments, seed="blind-seed")
        operator_material = operator_reveal_mapping(self.assignments, seed="blind-seed")
        self.assertIsInstance(operator_material, OperatorRevealMap)
        self.assertNotIsInstance(evaluator_material, OperatorRevealMap)
        self.assertEqual(["entries"], [field.name for field in fields(operator_material)])

        assignment_ids = {item.assignment_id for item in self.assignments}
        self.assertEqual(
            assignment_ids, {entry.assignment_id for entry in operator_material.entries}
        )
        self.assertEqual(
            {item.evaluation_id for item in evaluator_material.items},
            {entry.evaluation_id for entry in operator_material.entries},
        )

    def test_blind_artifact_commits_to_exact_assignment_plan(self) -> None:
        assignments_a = seeded_paired_assignment(["p-001"], seed="execution-a")
        assignments_b = seeded_paired_assignment(["p-001"], seed="execution-b")
        blind_a = blind_evaluation_order(assignments_a, seed="blind")
        blind_b = blind_evaluation_order(assignments_b, seed="blind")
        reveal_a = operator_reveal_mapping(assignments_a, seed="blind")
        reveal_b = operator_reveal_mapping(assignments_b, seed="blind")

        blind_ids_a = {item.evaluation_id for item in blind_a.items}
        blind_ids_b = {item.evaluation_id for item in blind_b.items}
        reveal_ids_a = {entry.evaluation_id for entry in reveal_a.entries}
        reveal_ids_b = {entry.evaluation_id for entry in reveal_b.entries}

        self.assertNotEqual(blind_a, blind_b)
        self.assertNotEqual(reveal_a, reveal_b)
        self.assertEqual(blind_ids_a, reveal_ids_a)
        self.assertEqual(blind_ids_b, reveal_ids_b)
        self.assertNotEqual(blind_ids_a, reveal_ids_b)
        self.assertNotEqual(blind_ids_b, reveal_ids_a)

    def test_blind_seed_changes_evaluation_order_without_changing_assignments(self) -> None:
        first = blind_evaluation_order(self.assignments, seed="blind-a")
        second = blind_evaluation_order(self.assignments, seed="blind-b")
        first_reveal = operator_reveal_mapping(self.assignments, seed="blind-a")
        second_reveal = operator_reveal_mapping(self.assignments, seed="blind-b")
        self.assertNotEqual(first.items, second.items)
        self.assertEqual(
            {entry.assignment_id for entry in first_reveal.entries},
            {entry.assignment_id for entry in second_reveal.entries},
        )

    def test_evaluation_order_is_independent_from_execution_order(self) -> None:
        plan = blind_evaluation_order(self.assignments, seed="blind-seed")
        reveal = operator_reveal_mapping(self.assignments, seed="blind-seed")
        reveal_by_eval = {
            entry.evaluation_id: entry.assignment_id for entry in reveal.entries
        }
        assignment_by_id = {item.assignment_id: item for item in self.assignments}
        evaluation_execution_indices = [
            assignment_by_id[reveal_by_eval[item.evaluation_id]].execution_index
            for item in plan.items
        ]
        self.assertNotEqual(
            list(range(len(self.assignments))), evaluation_execution_indices
        )

    def test_blind_order_rejects_incomplete_pairing(self) -> None:
        with self.assertRaisesRegex(ValueError, "paired assignment is incomplete"):
            blind_evaluation_order(self.assignments[:-1], seed="blind-seed")
        with self.assertRaisesRegex(ValueError, "paired assignment is incomplete"):
            operator_reveal_mapping(self.assignments[:-1], seed="blind-seed")

    def test_blind_order_rejects_duplicate_assignment_id(self) -> None:
        duplicate = Assignment(
            assignment_id=self.assignments[0].assignment_id,
            problem_id=self.assignments[1].problem_id,
            arm=self.assignments[1].arm,
            execution_index=self.assignments[1].execution_index,
        )
        malformed = (self.assignments[0], duplicate, *self.assignments[2:])
        with self.assertRaisesRegex(ValueError, "assignment_id values must be unique"):
            blind_evaluation_order(malformed, seed="blind-seed")
        with self.assertRaisesRegex(ValueError, "assignment_id values must be unique"):
            operator_reveal_mapping(malformed, seed="blind-seed")

    def test_blind_order_rejects_str_subclass_identifiers_before_hashing(self) -> None:
        bad_assignment_id = Assignment(
            assignment_id=HostileStr(self.assignments[0].assignment_id),
            problem_id=self.assignments[0].problem_id,
            arm=self.assignments[0].arm,
            execution_index=self.assignments[0].execution_index,
        )
        malformed_assignment_id = (bad_assignment_id, *self.assignments[1:])
        with self.assertRaisesRegex(ValueError, "exact str"):
            blind_evaluation_order(malformed_assignment_id, seed="blind-seed")
        with self.assertRaisesRegex(ValueError, "exact str"):
            operator_reveal_mapping(malformed_assignment_id, seed="blind-seed")

        bad_problem_id = Assignment(
            assignment_id=self.assignments[0].assignment_id,
            problem_id=HostileStr(self.assignments[0].problem_id),
            arm=self.assignments[0].arm,
            execution_index=self.assignments[0].execution_index,
        )
        malformed_problem_id = (bad_problem_id, *self.assignments[1:])
        with self.assertRaisesRegex(ValueError, "exact str"):
            blind_evaluation_order(malformed_problem_id, seed="blind-seed")
        with self.assertRaisesRegex(ValueError, "exact str"):
            operator_reveal_mapping(malformed_problem_id, seed="blind-seed")


if __name__ == "__main__":
    unittest.main()
