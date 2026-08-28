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
        for invalid in ("te\nst", "te\x7fst", "te\x85st", "te\u202est", "te\u200bst"):
            with self.subTest(value=repr(invalid)):
                with self.assertRaisesRegex(
                    ValueError, "split must not contain control or format characters"
                ):
                    BenchmarkProblemIdentity("bench", "v1", invalid, "42")
        for invalid in ("\ud800", "\udfff"):
            with self.subTest(codepoint=hex(ord(invalid))):
                with self.assertRaisesRegex(
                    ValueError, "native_id must contain only Unicode scalar values"
                ):
                    BenchmarkProblemIdentity("bench", "v1", "test", invalid)

    def test_string_subclass_behavior_cannot_spoof_token_validation(self) -> None:
        class DeceptiveString(str):
            def __eq__(self, other: object) -> bool:
                return True

            def strip(self, chars: str | None = None) -> "DeceptiveString":
                return self

            def encode(self, *args: object, **kwargs: object) -> bytes:
                return b"spoofed"

            __hash__ = str.__hash__

        with self.assertRaisesRegex(ValueError, "split must be a non-empty trimmed string"):
            BenchmarkProblemIdentity("bench", "v1", DeceptiveString(" test "), "p1")

        problem = BenchmarkProblemIdentity("bench", "v1", DeceptiveString("train"), "p1")
        self.assertIs(type(problem.split), str)
        self.assertEqual("train", problem.split)
        with self.assertRaisesRegex(ValueError, "contract benchmark/version/split"):
            SplitContract("bench", "v1", "test", (problem,))

    def test_identity_is_frozen(self) -> None:
        problem = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        with self.assertRaises(AttributeError):
            problem.split = "train"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            object.__setattr__(problem, "native_id", "43")

    def test_identity_does_not_alias_plain_tuple(self) -> None:
        problem = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        raw = ("bench", "v1", "test", "42")

        self.assertNotEqual(problem, raw)
        self.assertNotEqual(raw, problem)
        self.assertNotIn(raw, {problem})
        self.assertIn(problem, {problem})

    def test_namedtuple_helpers_preserve_identity_validation(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "native_id must be a non-empty trimmed string"
        ):
            BenchmarkProblemIdentity._make(("bench", "v1", "test", " 42 "))

        problem = BenchmarkProblemIdentity("bench", "v1", "test", "42")
        with self.assertRaisesRegex(
            ValueError, "native_id must be a non-empty trimmed string"
        ):
            problem._replace(native_id=" 43 ")

    def test_low_level_tuple_construction_fails_closed(self) -> None:
        with self.assertRaisesRegex(TypeError, "may not be subclassed"):
            type("SpoofedIdentity", (BenchmarkProblemIdentity,), {})

        malformed = tuple.__new__(
            BenchmarkProblemIdentity, ("bench", "v1", "test", " p1 ")
        )
        with self.assertRaisesRegex(
            ValueError, "native_id must be a non-empty trimmed string"
        ):
            _ = malformed.canonical_id

        truncated = tuple.__new__(BenchmarkProblemIdentity, ("bench",))
        with self.assertRaisesRegex(ValueError, "exactly four fields"):
            _ = truncated.canonical_id


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

    def test_contract_does_not_alias_plain_tuple(self) -> None:
        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        raw = (contract.benchmark, contract.version, contract.split, contract.problems)

        self.assertNotEqual(contract, raw)
        self.assertNotEqual(raw, contract)
        self.assertNotIn(raw, {contract})
        self.assertIn(contract, {contract})

    def test_low_level_reassignment_cannot_mutate_member_identity(self) -> None:
        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        original_contract_id = contract.contract_id
        with self.assertRaises(AttributeError):
            object.__setattr__(contract.problems[0], "native_id", "p2")
        self.assertEqual("p1", contract.problems[0].native_id)
        self.assertEqual(original_contract_id, contract.contract_id)

    def test_namedtuple_helpers_preserve_contract_validation(self) -> None:
        wrong_split = BenchmarkProblemIdentity("bench", "v1", "train", "p1")
        with self.assertRaisesRegex(ValueError, "contract benchmark/version/split"):
            SplitContract._make(("bench", "v1", "test", (wrong_split,)))

        contract = SplitContract.from_native_ids(
            benchmark="bench", version="v1", split="test", native_ids=["p1"]
        )
        with self.assertRaisesRegex(ValueError, "contract benchmark/version/split"):
            contract._replace(problems=(wrong_split,))

    def test_contract_low_level_construction_fails_closed(self) -> None:
        with self.assertRaisesRegex(TypeError, "may not be subclassed"):
            type("SpoofedContract", (SplitContract,), {})

        problem = BenchmarkProblemIdentity("bench", "v1", "test", "p1")
        mutable_storage = tuple.__new__(
            SplitContract, ("bench", "v1", "test", [problem])
        )
        with self.assertRaisesRegex(TypeError, "immutable problem tuple"):
            _ = mutable_storage.contract_id

        malformed_problem = tuple.__new__(
            BenchmarkProblemIdentity, ("bench", "v1", "test", " p1 ")
        )
        with self.assertRaisesRegex(
            ValueError, "native_id must be a non-empty trimmed string"
        ):
            SplitContract("bench", "v1", "test", (malformed_problem,))


if __name__ == "__main__":
    unittest.main()
