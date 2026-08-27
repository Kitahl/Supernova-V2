from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.arms.verified_chain import (
    ChainState,
    InvalidParentError,
    InvalidTransitionError,
    UnverifiedProductError,
    VerificationOutcome,
    VerificationSubjectMismatchError,
    VerifiedChain,
    VerifiedProduct,
)


class VerifiedChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chain = VerifiedChain("dry-001")

    def _pass(
        self,
        product_id: str,
        *,
        verifier_id: str = "checker",
        evidence_id: str = "v1",
    ) -> None:
        subject = self.chain.verification_subject(product_id)
        self.chain.record_verification(
            product_id,
            subject_sha256=subject.subject_sha256,
            outcome=VerificationOutcome.PASS,
            verifier_id=verifier_id,
            evidence_id=evidence_id,
        )

    def test_unverified_product_cannot_be_consumed_or_finalized(self) -> None:
        ref = self.chain.propose("lemma-1", {"claim": "A"}, producer_id="solver")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, ref.state)
        with self.assertRaisesRegex(UnverifiedProductError, "not been verified"):
            self.chain.consume_verified("lemma-1")
        with self.assertRaisesRegex(UnverifiedProductError, "final outputs"):
            self.chain.finalize("lemma-1")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, self.chain.state)

    def test_pass_mints_consumable_verified_product(self) -> None:
        ref = self.chain.propose("lemma-1", "proof term", producer_id="solver")
        subject = self.chain.verification_subject("lemma-1")
        self.assertEqual("proof term", subject.value)
        verified_ref = self.chain.record_verification(
            "lemma-1",
            subject_sha256=subject.subject_sha256,
            outcome=VerificationOutcome.PASS,
            verifier_id="lean-kernel",
            evidence_id="check-001",
        )
        self.assertEqual(ChainState.VERIFIED, verified_ref.state)
        self.assertEqual(ref.content_sha256, verified_ref.content_sha256)
        product = self.chain.consume_verified("lemma-1")
        self.assertIsInstance(product, VerifiedProduct)
        self.assertEqual("proof term", product.value)
        self.assertEqual(ref.content_sha256, product.content_sha256)
        self.assertEqual("lean-kernel", product.verifier_id)
        self.assertEqual(ChainState.READY, self.chain.state)
        self.assertTrue(self.chain.history[-1].consumed)

    def test_source_mutation_cannot_change_verified_product(self) -> None:
        source = {"answer": 42, "trace": ["a", "b"]}
        ref = self.chain.propose("lemma-1", source, producer_id="solver")
        subject = self.chain.verification_subject("lemma-1")
        self.chain.record_verification(
            "lemma-1",
            subject_sha256=subject.subject_sha256,
            outcome="PASS",
            verifier_id="checker",
            evidence_id="v1",
        )
        verified = self.chain.consume_verified("lemma-1")

        source["answer"] = 999
        source["trace"].append("mutated")
        self.assertEqual({"answer": 42, "trace": ["a", "b"]}, verified.value)
        self.assertEqual(ref.content_sha256, verified.content_sha256)

        decoded = verified.value
        decoded["answer"] = -1
        decoded["trace"].clear()
        self.assertEqual({"answer": 42, "trace": ["a", "b"]}, verified.value)
        self.assertEqual(ref.content_sha256, verified.content_sha256)

    def test_verification_result_must_bind_exact_chain_subject(self) -> None:
        source = {"answer": 42}
        ref = self.chain.propose("lemma-1", source, producer_id="solver")
        subject = self.chain.verification_subject("lemma-1")

        source["answer"] = 999
        self.assertEqual({"answer": 42}, subject.value)
        self.assertEqual(ref.content_sha256, subject.content_sha256)
        self.assertEqual("dry-001", subject.problem_id)

        with self.assertRaisesRegex(
            VerificationSubjectMismatchError, "chain context"
        ):
            self.chain.record_verification(
                "lemma-1",
                subject_sha256="0" * 64,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="wrong-subject",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, self.chain.state)
        self.assertEqual(ref.content_sha256, self.chain.current.content_sha256)

        self.chain.record_verification(
            "lemma-1",
            subject_sha256=subject.subject_sha256,
            outcome="PASS",
            verifier_id="checker",
            evidence_id="bound-subject",
        )
        verified = self.chain.consume_verified("lemma-1")
        self.assertEqual({"answer": 42}, verified.value)
        self.assertEqual(subject.content_sha256, verified.content_sha256)
        self.assertEqual(subject.subject_sha256, verified.verification_subject_sha256)

    def test_verification_cannot_replay_across_problem_or_parent_context(self) -> None:
        left = VerifiedChain("dry-001")
        right = VerifiedChain("dry-002")
        for chain in (left, right):
            chain.propose("lemma-1", {"claim": "same"}, producer_id="solver")

        left_subject = left.verification_subject("lemma-1")
        right_subject = right.verification_subject("lemma-1")
        self.assertEqual(left_subject.content_sha256, right_subject.content_sha256)
        self.assertNotEqual(left_subject.subject_sha256, right_subject.subject_sha256)

        with self.assertRaisesRegex(
            VerificationSubjectMismatchError, "chain context"
        ):
            right.record_verification(
                "lemma-1",
                subject_sha256=left_subject.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="cross-problem-replay",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, right.state)

        parent_a = VerifiedChain("dry-parent")
        parent_b = VerifiedChain("dry-parent")
        parent_a.propose("parent", {"seed": 1}, producer_id="solver")
        parent_b.propose("parent", {"seed": 2}, producer_id="solver")
        for chain in (parent_a, parent_b):
            subject = chain.verification_subject("parent")
            chain.record_verification(
                "parent",
                subject_sha256=subject.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="parent-pass",
            )
        verified_a = parent_a.consume_verified("parent")
        verified_b = parent_b.consume_verified("parent")
        parent_a.propose(
            "child", {"claim": "same"}, producer_id="solver", parent=verified_a
        )
        parent_b.propose(
            "child", {"claim": "same"}, producer_id="solver", parent=verified_b
        )
        child_a = parent_a.verification_subject("child")
        child_b = parent_b.verification_subject("child")
        self.assertEqual(child_a.content_sha256, child_b.content_sha256)
        self.assertNotEqual(child_a.parent_content_sha256, child_b.parent_content_sha256)
        self.assertNotEqual(child_a.subject_sha256, child_b.subject_sha256)

        with self.assertRaisesRegex(
            VerificationSubjectMismatchError, "chain context"
        ):
            parent_b.record_verification(
                "child",
                subject_sha256=child_a.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="cross-parent-replay",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, parent_b.state)

    def test_verification_subject_recursively_binds_deeper_lineage(self) -> None:
        left = VerifiedChain("dry-lineage")
        right = VerifiedChain("dry-lineage")

        left.propose("grandparent", {"seed": 1}, producer_id="solver")
        right.propose("grandparent", {"seed": 2}, producer_id="solver")
        for chain in (left, right):
            subject = chain.verification_subject("grandparent")
            chain.record_verification(
                "grandparent",
                subject_sha256=subject.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="grandparent-pass",
            )
        left_grandparent = left.consume_verified("grandparent")
        right_grandparent = right.consume_verified("grandparent")

        left.propose(
            "parent",
            {"claim": "same-parent"},
            producer_id="solver",
            parent=left_grandparent,
        )
        right.propose(
            "parent",
            {"claim": "same-parent"},
            producer_id="solver",
            parent=right_grandparent,
        )
        left_parent_subject = left.verification_subject("parent")
        right_parent_subject = right.verification_subject("parent")
        self.assertEqual(
            left_parent_subject.content_sha256, right_parent_subject.content_sha256
        )
        self.assertNotEqual(
            left_parent_subject.subject_sha256, right_parent_subject.subject_sha256
        )

        for chain, subject in (
            (left, left_parent_subject),
            (right, right_parent_subject),
        ):
            chain.record_verification(
                "parent",
                subject_sha256=subject.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="parent-pass",
            )
        left_parent = left.consume_verified("parent")
        right_parent = right.consume_verified("parent")

        left.propose(
            "child", {"claim": "same-child"}, producer_id="solver", parent=left_parent
        )
        right.propose(
            "child", {"claim": "same-child"}, producer_id="solver", parent=right_parent
        )
        left_child = left.verification_subject("child")
        right_child = right.verification_subject("child")

        self.assertEqual(left_child.content_sha256, right_child.content_sha256)
        self.assertEqual(
            left_child.parent_content_sha256, right_child.parent_content_sha256
        )
        self.assertEqual(left_child.parent_product_id, right_child.parent_product_id)
        self.assertNotEqual(
            left_child.parent_verification_subject_sha256,
            right_child.parent_verification_subject_sha256,
        )
        self.assertNotEqual(
            left_child.parent_verification_receipt_sha256,
            right_child.parent_verification_receipt_sha256,
        )
        self.assertNotEqual(left_child.subject_sha256, right_child.subject_sha256)

        with self.assertRaisesRegex(
            VerificationSubjectMismatchError, "chain context"
        ):
            right.record_verification(
                "child",
                subject_sha256=left_child.subject_sha256,
                outcome="PASS",
                verifier_id="checker",
                evidence_id="cross-lineage-replay",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, right.state)

    def test_child_subject_binds_parent_verification_provenance(self) -> None:
        left = VerifiedChain("dry-provenance")
        right = VerifiedChain("dry-provenance")

        for chain in (left, right):
            chain.propose(
                "parent", {"claim": "same-parent"}, producer_id="solver"
            )
        left_parent_subject = left.verification_subject("parent")
        right_parent_subject = right.verification_subject("parent")
        self.assertEqual(
            left_parent_subject.subject_sha256, right_parent_subject.subject_sha256
        )

        left.record_verification(
            "parent",
            subject_sha256=left_parent_subject.subject_sha256,
            outcome="PASS",
            verifier_id="checker-a",
            evidence_id="evidence-a",
        )
        right.record_verification(
            "parent",
            subject_sha256=right_parent_subject.subject_sha256,
            outcome="PASS",
            verifier_id="checker-b",
            evidence_id="evidence-b",
        )
        left_parent = left.consume_verified("parent")
        right_parent = right.consume_verified("parent")

        self.assertEqual(
            left_parent.verification_subject_sha256,
            right_parent.verification_subject_sha256,
        )
        self.assertNotEqual(
            left_parent.verification_receipt_sha256,
            right_parent.verification_receipt_sha256,
        )

        left.propose(
            "child", {"claim": "same-child"}, producer_id="solver", parent=left_parent
        )
        right.propose(
            "child", {"claim": "same-child"}, producer_id="solver", parent=right_parent
        )
        left_child = left.verification_subject("child")
        right_child = right.verification_subject("child")

        self.assertEqual(left_child.content_sha256, right_child.content_sha256)
        self.assertEqual(
            left_child.parent_verification_subject_sha256,
            right_child.parent_verification_subject_sha256,
        )
        self.assertNotEqual(
            left_child.parent_verification_receipt_sha256,
            right_child.parent_verification_receipt_sha256,
        )
        self.assertNotEqual(left_child.subject_sha256, right_child.subject_sha256)

        with self.assertRaisesRegex(
            VerificationSubjectMismatchError, "chain context"
        ):
            right.record_verification(
                "child",
                subject_sha256=left_child.subject_sha256,
                outcome="PASS",
                verifier_id="checker-b",
                evidence_id="cross-provenance-replay",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, right.state)

    def test_unencodable_unicode_proposal_fails_atomically(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTF-8-encodable"):
            self.chain.propose(
                "lemma-surrogate", {"claim": "\ud800"}, producer_id="solver"
            )

        self.assertEqual(ChainState.READY, self.chain.state)
        self.assertIsNone(self.chain.current)
        self.assertEqual((), self.chain.history)

        ref = self.chain.propose(
            "lemma-surrogate", {"claim": "valid"}, producer_id="solver"
        )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, ref.state)

    def test_rejected_product_cannot_be_consumed_and_may_be_discarded(self) -> None:
        self.chain.propose("lemma-bad", "bad proof", producer_id="solver")
        subject = self.chain.verification_subject("lemma-bad")
        ref = self.chain.record_verification(
            "lemma-bad",
            subject_sha256=subject.subject_sha256,
            outcome="FAIL",
            verifier_id="lean-kernel",
            evidence_id="check-bad",
        )
        self.assertEqual(ChainState.REJECTED, ref.state)
        with self.assertRaisesRegex(UnverifiedProductError, "rejected products"):
            self.chain.consume_verified("lemma-bad")
        self.chain.discard_rejected("lemma-bad")
        self.assertEqual(ChainState.READY, self.chain.state)
        self.assertFalse(self.chain.history[-1].consumed)

    def test_next_step_requires_exact_last_consumed_verified_parent(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="solver")
        self._pass("lemma-1")
        parent = self.chain.consume_verified("lemma-1")

        forged = VerifiedProduct(
            product_id=parent.product_id,
            step_index=parent.step_index,
            content=parent.content,
            producer_id=parent.producer_id,
            verifier_id=parent.verifier_id,
            evidence_id=parent.evidence_id,
            parent_product_id=parent.parent_product_id,
            verification_subject_sha256=parent.verification_subject_sha256,
            verification_receipt_sha256=parent.verification_receipt_sha256,
        )
        with self.assertRaisesRegex(InvalidParentError, "exact last consumed"):
            self.chain.propose("lemma-2", "B", producer_id="solver", parent=forged)

        ref = self.chain.propose("lemma-2", "B", producer_id="solver", parent=parent)
        self.assertEqual(1, ref.step_index)

    def test_chain_cannot_skip_consumption_between_verified_steps(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="solver")
        self._pass("lemma-1")
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("lemma-2", "B", producer_id="solver")

    def test_verifier_must_be_distinct_from_producer(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="same-agent")
        subject = self.chain.verification_subject("lemma-1")
        with self.assertRaisesRegex(ValueError, "differ from producer"):
            self.chain.record_verification(
                "lemma-1",
                subject_sha256=subject.subject_sha256,
                outcome="PASS",
                verifier_id="same-agent",
                evidence_id="v1",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, self.chain.state)

    def test_final_output_requires_pass_and_closes_chain(self) -> None:
        self.chain.propose("answer", 42, producer_id="solver")
        subject = self.chain.verification_subject("answer")
        self.chain.record_verification(
            "answer",
            subject_sha256=subject.subject_sha256,
            outcome="PASS",
            verifier_id="checker",
            evidence_id="answer-v",
        )
        final = self.chain.finalize("answer")
        self.assertEqual(42, final.value)
        self.assertEqual(ChainState.COMPLETE, self.chain.state)
        self.assertTrue(self.chain.history[-1].final)
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("after", 43, producer_id="solver", parent=final)

    def test_product_ids_are_unique_even_after_rejection(self) -> None:
        self.chain.propose("lemma", "bad", producer_id="solver")
        subject = self.chain.verification_subject("lemma")
        self.chain.record_verification(
            "lemma",
            subject_sha256=subject.subject_sha256,
            outcome="FAIL",
            verifier_id="checker",
            evidence_id="bad",
        )
        self.chain.discard_rejected("lemma")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.chain.propose("lemma", "retry", producer_id="solver")


if __name__ == "__main__":
    unittest.main()
