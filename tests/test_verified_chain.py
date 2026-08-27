from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.arms.verified_chain import (
    SUBJECT_PATH_TOKEN,
    ChainState,
    InvalidParentError,
    InvalidTransitionError,
    UnverifiedProductError,
    VerificationAuthorityError,
    VerificationExecutionError,
    VerificationOutcome,
    VerifiedChain,
    VerifiedProduct,
)
from supernova_goal1.verifier import VerifierResult, VerifierStatus

PASS_CODE = r'''
import hashlib,json,sys
with open(sys.argv[1], encoding="utf-8") as handle:
    subject=json.load(handle)
if hashlib.sha256(subject["canonical_json"].encode("utf-8")).hexdigest() != subject["content_sha256"]:
    raise SystemExit(8)
if len(subject["subject_sha256"]) != 64:
    raise SystemExit(9)
raise SystemExit(0)
'''
FAIL_CODE = r'''import json,sys; json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(7)'''
TIMEOUT_CODE = r'''import time; time.sleep(5)'''


def verifier_command(code: str = PASS_CODE) -> tuple[str, ...]:
    return (sys.executable, "-c", code, SUBJECT_PATH_TOKEN)


class VerifiedChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chain = VerifiedChain(
            "dry-001", verifier_command=verifier_command(), verifier_timeout_seconds=2
        )

    def _pass(self, product_id: str) -> None:
        ref = self.chain.verify_pending(product_id)
        self.assertEqual(ChainState.VERIFIED, ref.state)

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
        verified_ref = self.chain.verify_pending("lemma-1")
        self.assertEqual(ChainState.VERIFIED, verified_ref.state)
        self.assertEqual(ref.content_sha256, verified_ref.content_sha256)
        product = self.chain.consume_verified("lemma-1")
        self.assertIsInstance(product, VerifiedProduct)
        self.assertEqual("proof term", product.value)
        self.assertEqual(subject.subject_sha256, product.verification_subject_sha256)
        self.assertEqual(self.chain.verifier_id, product.verifier_id)
        self.assertTrue(product.evidence_id.startswith("verifier-evidence-sha256:"))
        self.assertEqual(ChainState.READY, self.chain.state)

    def test_source_mutation_cannot_change_verified_product(self) -> None:
        source = {"answer": 42, "trace": ["a", "b"]}
        ref = self.chain.propose("lemma-1", source, producer_id="solver")
        self._pass("lemma-1")
        verified = self.chain.consume_verified("lemma-1")
        source["answer"] = 999
        source["trace"].append("mutated")
        self.assertEqual({"answer": 42, "trace": ["a", "b"]}, verified.value)
        self.assertEqual(ref.content_sha256, verified.content_sha256)
        decoded = verified.value
        decoded["answer"] = -1
        decoded["trace"].clear()
        self.assertEqual({"answer": 42, "trace": ["a", "b"]}, verified.value)

    def test_authority_objects_resist_low_level_field_reassignment(self) -> None:
        self.chain.propose("lemma-1", {"answer": 42}, producer_id="solver")
        subject = self.chain.verification_subject("lemma-1")
        with self.assertRaises(AttributeError):
            object.__setattr__(subject.content, "canonical_json", '{"answer":999}')
        with self.assertRaises(AttributeError):
            object.__setattr__(subject, "producer_id", "attacker")
        self._pass("lemma-1")
        verified = self.chain.consume_verified("lemma-1")
        with self.assertRaises(AttributeError):
            object.__setattr__(verified.content, "canonical_json", '{"answer":999}')
        with self.assertRaises(AttributeError):
            object.__setattr__(verified, "verifier_id", "attacker")
        self.assertEqual({"answer": 42}, verified.value)

    def test_history_records_resist_low_level_field_reassignment(self) -> None:
        self.chain.propose("lemma-1", {"answer": 42}, producer_id="solver")
        self._pass("lemma-1")
        self.chain.consume_verified("lemma-1")
        record = self.chain.history[-1]
        with self.assertRaises(AttributeError):
            object.__setattr__(record, "outcome", VerificationOutcome.FAIL)
        with self.assertRaises(AttributeError):
            object.__setattr__(record, "consumed", False)
        self.assertEqual(VerificationOutcome.PASS, record.outcome)
        self.assertTrue(record.consumed)

    def test_verifier_reads_the_exact_immutable_subject(self) -> None:
        source = {"answer": 42}
        ref = self.chain.propose("lemma-1", source, producer_id="solver")
        subject = self.chain.verification_subject("lemma-1")
        source["answer"] = 999
        self.assertEqual({"answer": 42}, subject.value)
        self._pass("lemma-1")
        verified = self.chain.consume_verified("lemma-1")
        self.assertEqual({"answer": 42}, verified.value)
        self.assertEqual(ref.content_sha256, verified.content_sha256)

    def test_subject_hashes_bind_problem_parent_lineage_and_provenance(self) -> None:
        left = VerifiedChain("dry-001", verifier_command=verifier_command())
        right = VerifiedChain("dry-002", verifier_command=verifier_command())
        for chain in (left, right):
            chain.propose("lemma", {"same": True}, producer_id="solver")
        self.assertNotEqual(
            left.verification_subject("lemma").subject_sha256,
            right.verification_subject("lemma").subject_sha256,
        )

        a = VerifiedChain("dry-parent", verifier_command=verifier_command())
        b = VerifiedChain("dry-parent", verifier_command=verifier_command())
        a.propose("parent", {"seed": 1}, producer_id="solver")
        b.propose("parent", {"seed": 2}, producer_id="solver")
        a.verify_pending("parent"); b.verify_pending("parent")
        pa = a.consume_verified("parent"); pb = b.consume_verified("parent")
        a.propose("child", {"same": True}, producer_id="solver", parent=pa)
        b.propose("child", {"same": True}, producer_id="solver", parent=pb)
        self.assertNotEqual(
            a.verification_subject("child").subject_sha256,
            b.verification_subject("child").subject_sha256,
        )

    def test_child_subject_binds_parent_verification_provenance(self) -> None:
        left = VerifiedChain("dry-provenance", verifier_command=verifier_command())
        right = VerifiedChain(
            "dry-provenance",
            verifier_command=verifier_command(PASS_CODE + "\n# distinct command identity"),
        )
        for chain in (left, right):
            chain.propose("parent", {"same": True}, producer_id="solver")
            chain.verify_pending("parent")
        lp = left.consume_verified("parent"); rp = right.consume_verified("parent")
        self.assertNotEqual(lp.verification_receipt_sha256, rp.verification_receipt_sha256)
        left.propose("child", {"same": True}, producer_id="solver", parent=lp)
        right.propose("child", {"same": True}, producer_id="solver", parent=rp)
        self.assertNotEqual(
            left.verification_subject("child").subject_sha256,
            right.verification_subject("child").subject_sha256,
        )

    def test_unencodable_unicode_proposal_fails_atomically(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTF-8-encodable"):
            self.chain.propose("lemma-surrogate", {"claim": "\ud800"}, producer_id="solver")
        self.assertEqual(ChainState.READY, self.chain.state)
        self.assertIsNone(self.chain.current)
        ref = self.chain.propose("lemma-surrogate", {"claim": "valid"}, producer_id="solver")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, ref.state)

    def test_real_fail_rejects_product(self) -> None:
        chain = VerifiedChain("dry-fail", verifier_command=verifier_command(FAIL_CODE))
        chain.propose("lemma-bad", "bad proof", producer_id="solver")
        ref = chain.verify_pending("lemma-bad")
        self.assertEqual(ChainState.REJECTED, ref.state)
        with self.assertRaisesRegex(UnverifiedProductError, "rejected products"):
            chain.consume_verified("lemma-bad")
        chain.discard_rejected("lemma-bad")
        self.assertEqual(ChainState.READY, chain.state)
        self.assertEqual(VerificationOutcome.FAIL, chain.history[-1].outcome)

    def test_unauthenticated_pass_declaration_cannot_mint_verified(self) -> None:
        chain = VerifiedChain("dry-auth", verifier_command=verifier_command(FAIL_CODE))
        chain.propose("lemma", {"claim": "unverified"}, producer_id="solver")
        forged = VerifierResult(
            status=VerifierStatus.PASS,
            command=("fake",),
            returncode=0,
            stdout="",
            stderr="",
            elapsed_milliseconds=0,
        )
        self.assertFalse(hasattr(chain, "record_verification"))
        with self.assertRaises(TypeError):
            chain.verify_pending("lemma", verifier_result=forged)  # type: ignore[call-arg]
        self.assertEqual(ChainState.AWAITING_VERIFICATION, chain.state)
        ref = chain.verify_pending("lemma")
        self.assertEqual(ChainState.REJECTED, ref.state)

    def test_missing_verifier_authority_fails_closed(self) -> None:
        chain = VerifiedChain("dry-no-authority")
        chain.propose("lemma", 1, producer_id="solver")
        with self.assertRaisesRegex(VerificationAuthorityError, "no deterministic verifier"):
            chain.verify_pending("lemma")
        self.assertEqual(ChainState.AWAITING_VERIFICATION, chain.state)

    def test_timeout_does_not_mint_or_reject_product(self) -> None:
        chain = VerifiedChain(
            "dry-timeout",
            verifier_command=verifier_command(TIMEOUT_CODE),
            verifier_timeout_seconds=0.05,
        )
        chain.propose("lemma", 1, producer_id="solver")
        with self.assertRaises(VerificationExecutionError) as caught:
            chain.verify_pending("lemma")
        self.assertIs(VerifierStatus.TIMEOUT, caught.exception.status)
        self.assertEqual(ChainState.AWAITING_VERIFICATION, chain.state)
        with self.assertRaises(UnverifiedProductError):
            chain.consume_verified("lemma")

    def test_next_step_requires_exact_last_consumed_verified_parent(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="solver")
        self._pass("lemma-1")
        parent = self.chain.consume_verified("lemma-1")
        forged = VerifiedProduct(*parent)
        with self.assertRaisesRegex(InvalidParentError, "exact last consumed"):
            self.chain.propose("lemma-2", "B", producer_id="solver", parent=forged)
        ref = self.chain.propose("lemma-2", "B", producer_id="solver", parent=parent)
        self.assertEqual(1, ref.step_index)

    def test_chain_cannot_skip_consumption_between_verified_steps(self) -> None:
        self.chain.propose("lemma-1", "A", producer_id="solver")
        self._pass("lemma-1")
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("lemma-2", "B", producer_id="solver")

    def test_final_output_requires_pass_and_closes_chain(self) -> None:
        self.chain.propose("answer", 42, producer_id="solver")
        self._pass("answer")
        final = self.chain.finalize("answer")
        self.assertEqual(42, final.value)
        self.assertEqual(ChainState.COMPLETE, self.chain.state)
        self.assertTrue(self.chain.history[-1].final)
        with self.assertRaisesRegex(InvalidTransitionError, "cannot propose"):
            self.chain.propose("after", 43, producer_id="solver", parent=final)

    def test_product_ids_are_unique_even_after_rejection(self) -> None:
        chain = VerifiedChain("dry-unique", verifier_command=verifier_command(FAIL_CODE))
        chain.propose("lemma", "bad", producer_id="solver")
        chain.verify_pending("lemma")
        chain.discard_rejected("lemma")
        with self.assertRaisesRegex(ValueError, "already used"):
            chain.propose("lemma", "retry", producer_id="solver")


if __name__ == "__main__":
    unittest.main()
