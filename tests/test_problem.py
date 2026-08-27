from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.problem import BenchmarkProblemIdentity, SplitContract


class BenchmarkProblemIdentityTests(unittest.TestCase):
    def test_canonical_identity_is_stable_and_scope_sensitive(self) -> None:
        base = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        self.assertEqual(
            base.canonical_id,
            BenchmarkProblemIdentity("bench", "v1", "test", "42").canonical_id,
        )
        for changed in (
            BenchmarkProblemIdentity("other", "v1", "test", "42"),
            BenchmarkProblemIdentity("bench", "v2", "test", "42"),
            BenchmarkProblemIdentity("bench", "v1", "train", "42"),
            BenchmarkProblemIdentity("bench", "v1", "test", "43"),
        ):
            self.assertNotEqual(base.canonical_id, changed.canonical_id)

    def test_identity_rejects_ambiguous_tokens(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "native_id must be a non-empty trimmed string"
        ):
            BenchmarkProblemIdentity("bench", "v1", "test", " 42 ")
        with self.assertRaisesRegex(ValueError, "split must not contain control characters"):
            BenchmarkProblemIdentity("bench", "v1", "te\nst", "42")
        for invalid in ("\ud800", "\udfff"):
            with self.subTest(codepoint=hex(ord(invalid))):
                with self.assertRaisesRegex(
                    ValueError, "native_id must contain only Unicode scalar values"
                ):
                    BenchmarkProblemIdentity("bench", "v1", "test", invalid)

    def test_identity_is_frozen(self) -> None:
        problem = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        with self.assertRaises(AttributeError):
            problem.split = "train"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            object.__setattr__(problem, "native_id", "43")


class SplitContractTests(unittest.TestCase):
    def test_factory_snapshots_ordered_membership(self) -> None:
        native_ids = ["p1", "p2"]
        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=native_ids
        )
        original_contract_id = contract.contract_id
        native_ids.append("p3")

        self.assertEqual(
            ("p1", "p2"), tuple(problem.native_id for problem in contract.problems)
        )
        self.assertEqual(original_contract_id, contract.contract_id)
        self.assertEqual(2, len(contract.problem_ids))

    def test_contract_id_changes_when_order_or_membership_changes(self) -> None:
        first = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1", "p2"]
        )
        reordered = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p2", "p1"]
        )
        expanded = SplitContract.from_native_ids(
            benchmark="bench",
            version="v1",
            split="test",
            native_ids=["p1", "p2", "p3"],
        )
        self.assertNotEqual(first.contract_id, reordered.contract_id)
        self.assertNotEqual(first.contract_id, expanded.contract_id)

    def test_contract_rejects_duplicates_and_cross_split_members(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate problem identity"):
            SplitContract.from_native_ids(
                benchmark="bench",
                version="v1",
                split="test",
                native_ids=["p1", "p1"],
            )

        wrong_split = BenchmarkProblemIdentity("bench", "v1", "train", "p1")
        with self.assertRaisesRegex(ValueError, "contract benchmark/version/split"):
            SplitContract("bench", "v1", "test", (wrong_split,))

    def test_contract_rejects_a_scalar_membership_string(self) -> None:
        with self.assertRaisesRegex(TypeError, "sequence of problem-id strings"):
            SplitContract.from_native_ids(
                benchmark="bench", version="v1", split="test", native_ids="p1"
            )

    def test_contract_rejects_unordered_membership(self) -> None:
        for native_ids in ({"p1", "p2"}, frozenset({"p1", "p2"})):
            with self.subTest(container=type(native_ids).__name__):
                with self.assertRaisesRegex(TypeError, "ordered sequence"):
                    SplitContract.from_native_ids(
                        benchmark="bench",
                        version="v1",
                        split="test",
                        native_ids=native_ids,  # type: ignore[arg-type]
                    )

    def test_contract_rejects_iterator_wrapped_unordered_membership(self) -> None:
        unordered = {"p1", "p2"}
        with self.assertRaisesRegex(TypeError, "ordered sequence"):
            SplitContract.from_native_ids(
                benchmark="bench",
                version="v1",
                split="test",
                native_ids=iter(unordered),  # type: ignore[arg-type]
            )

        problems = {
            BenchmarkProblemIdentity("bench", "v1", "test", "p1"),
            BenchmarkProblemIdentity("bench", "v1", "test", "p2"),
        }
        with self.assertRaisesRegex(TypeError, "ordered sequence"):
            SplitContract(
                "bench", "v1", "test", iter(problems)  # type: ignore[arg-type]
            )

    def test_direct_contract_rejects_unordered_membership(self) -> None:
        problems = {
            BenchmarkProblemIdentity("bench", "v1", "test", "p1"),
            BenchmarkProblemIdentity("bench", "v1", "test", "p2"),
        }
        for unordered in (problems, frozenset(problems)):
            with self.subTest(container=type(unordered).__name__):
                with self.assertRaisesRegex(TypeError, "ordered sequence"):
                    SplitContract("bench", "v1", "test", unordered)  # type: ignore[arg-type]

    def test_contract_requires_nonempty_membership_and_is_frozen(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            SplitContract.from_native_ids(
                benchmark="bench", version="v1", split="test", native_ids=[]
            )

        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        with self.assertRaises(AttributeError):
            contract.split = "train"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            object.__setattr__(contract, "split", "train")
        with self.assertRaises(AttributeError):
            object.__setattr__(contract, "problems", ())

    def test_low_level_reassignment_cannot_mutate_member_identity(self) -> None:
        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        original_contract_id = contract.contract_id
        with self.assertRaises(AttributeError):
            object.__setattr__(contract.problems[0], "native_id", "p2")
        self.assertEqual("p1", contract.problems[0].native_id)
        self.assertEqual(original_contract_id, contract.contract_id)


if __name__ == "__main__":
    unittest.main()
