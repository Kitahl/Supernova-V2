from __future__ import annotations

from dataclasses import FrozenInstanceError
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

    def test_identity_is_frozen(self) -> None:
        problem = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        with self.assertRaises(FrozenInstanceError):
            problem.split = "train"  # type: ignore[misc]


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

    def test_contract_requires_nonempty_membership_and_is_frozen(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            SplitContract.from_native_ids(
                benchmark="bench", version="v1", split="test", native_ids=[]
            )

        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        with self.assertRaises(FrozenInstanceError):
            contract.split = "train"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
