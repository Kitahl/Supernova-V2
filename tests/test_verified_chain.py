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
    VerifiedChain,
    VerifiedProduct,
)


class VerifiedChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chain = VerifiedChain("dry-001")

    def test_unverified_product_cannot_be_consumed_or_finalized(self) -> None:
        ref = self.chain.propose("lemma-1", {"claim": "A"}, producer_id="solver")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, ref.state)
        with self.assertRaisesRegex(UnverifiedProductError, "not been verified"):
            self.chain.consume_verified("lemma-1")
        with self.assertRaisesRegex(UnverifiedProductError, "final outputs"):
            self.chain.finalize("lemma-1")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, self.chain.state)

    def test_pass_mints_consumable_verified_product(self) -> None:
        self.chain.propose("lemma-1", "proof term", producer_id="solver")
        ref = self.chain.record_verification(
            "lemma-1",
            outcome=VerificationOutcome.PASS,
            verifier_id="lean-kernel",
            evidence_id="check-001",
        )
        self.assertEqual(ChainState.VERIFIED, ref.state)
        product = self.chain.consume_verified("lemma-1")
        self.assertIsInstance(product, VerifiedProduct)
        self.assertEqual("proof term", product.value)
        self.assertEqual("lean-kernel", product.verifier_id)
        self.assertEqual(ChainState.READY, self.chain.state)
        self.assertTrue(self.chain.history[-1].consumed)

    def test_rejected_product_cannot_be_consumed_and_may_be_discarded(self) -> None:
        self.chain.propose("lemma-bad", "bad proof", producer_id="solver")
        ref = self.chain.record_verification(
            "lemma-bad",
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
        self.chain.record_verification(
            "lemma-1", outcome="PASS", verifier_id="checker", evidence_id="v1"
        )
        parent = self.chain.consume_verified("lemma-1")

        forged = VerifiedProduct(
            product_id=parent.product_id,
            step_index=parent.step_index,
            value=parent.value,
            producer_id=parent.producer_id,
            verifier_id=parent.verifier_id,
            evidence_id=parent.evidence_id,
            parent_product_id=parent.parent_product_id,
        )
        with self.assertRaisesRegex(InvalidParentError, "exact last consumed"):
            self.chain.propose("lemma-2", "B", producer_id="solver", parent=forged)

        ref = self.chain.propose("lemma-2", "B", producer_id="solver", parent=parent)
        self.assertEqual(1, ref.step_index)

    def test_chain_cannot_skip_consumption_between_verified_steps(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="solver")
        self.chain.record_verification(
            "lemma-1", outcome="PASS", verifier_id="checker", evidence_id="v1"
        )
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("lemma-2", "B", producer_id="solver")

    def test_verifier_must_be_distinct_from_producer(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="same-agent")
        with self.assertRaisesRegex(ValueError, "differ from producer"):
            self.chain.record_verification(
                "lemma-1",
                outcome="PASS",
                verifier_id="same-agent",
                evidence_id="v1",
            )
        self.assertEqual(ChainState.AWAITING_VERIFICATION, self.chain.state)

    def test_final_output_requires_pass_and_closes_chain(self) -> None:
        self.chain.propose("answer", 42, producer_id="solver")
        self.chain.record_verification(
            "answer", outcome="PASS", verifier_id="checker", evidence_id="answer-v"
        )
        final = self.chain.finalize("answer")
        self.assertEqual(42, final.value)
        self.assertEqual(ChainState.COMPLETE, self.chain.state)
        self.assertTrue(self.chain.history[-1].final)
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("after", 43, producer_id="solver", parent=final)

    def test_product_ids_are_unique_even_after_rejection(self) -> None:
        self.chain.propose("lemma", "bad", producer_id="solver")
        self.chain.record_verification(
            "lemma", outcome="FAIL", verifier_id="checker", evidence_id="bad"
        )
        self.chain.discard_rejected("lemma")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.chain.propose("lemma", "retry", producer_id="solver")


if __name__ == "__main__":
    unittest.main()
