from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import (
    CompletionRecord, CompletionStatus, DispatchAuthority, DispatchEntry,
    DispatchManifest, join_completions,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "authority.sqlite")
        self.authority = DispatchAuthority(self.db, "run-1")
        m0 = self.authority.current_manifest()
        self.m1, self.s1 = self.authority.register(
            m0, problem_id="p1", arm=Arm.ORDINARY, attempt_index=0, request_sha256=sha("r1"))
        self.m2, self.s2 = self.authority.register(
            self.m1, problem_id="p1", arm=Arm.PORTFOLIO, attempt_index=0, request_sha256=sha("r2"))
        self.c1 = self.s1.complete(status=CompletionStatus.SUCCEEDED, payload_sha256=sha("out1"))
        self.c2 = self.s2.complete(status=CompletionStatus.FAILED, payload_sha256=sha("out2"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manifest_is_immutable_append_only_hash_chain(self) -> None:
        self.assertEqual(1, len(self.m1.entries)); self.assertEqual(2, len(self.m2.entries))
        self.assertEqual(self.m1.entries[-1].entry_sha256, self.m2.entries[-1].predecessor_sha256)
        self.assertNotEqual(self.m1.manifest_sha256, self.m2.manifest_sha256)

    def test_duplicate_attempt_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate dispatch attempt"):
            self.authority.register(self.m2, problem_id="p1", arm=Arm.ORDINARY,
                                    attempt_index=0, request_sha256=sha("different"))

    def test_manifest_reorder_or_relink_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "append-only chain"):
            DispatchManifest("run-1", tuple(reversed(self.m2.entries)))
        e = self.m2.entries[1]
        forged = DispatchEntry.create(run_id=e.run_id, sequence=e.sequence, problem_id=e.problem_id,
                                      arm=e.arm, attempt_index=e.attempt_index, request_sha256=e.request_sha256,
                                      completion_verifier_sha256=e.completion_verifier_sha256,
                                      predecessor_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "append-only chain"):
            DispatchManifest("run-1", (self.m2.entries[0], forged))

    def test_entry_identity_binds_arm_problem_attempt_request_and_authority(self) -> None:
        e = self.m1.entries[0]
        for field, value in (("arm", Arm.PORTFOLIO), ("problem_id", "p2"), ("attempt_index", 1),
                             ("request_sha256", sha("x")), ("completion_verifier_sha256", sha("fake verifier"))):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "dispatch_id"):
                replace(e, **{field: value})

    def test_join_accepts_complete_out_of_order_input_but_returns_manifest_order(self) -> None:
        result = join_completions(self.authority, self.m2, (self.c2, self.c1))
        self.assertEqual([e.dispatch_id for e in self.m2.entries], [j.dispatch.dispatch_id for j in result.joined])
        self.assertEqual(self.m2.manifest_sha256, result.receipt.manifest_sha256)

    def test_stale_prefix_cannot_self_authenticate(self) -> None:
        with self.assertRaisesRegex(ValueError, "authoritative latest"):
            join_completions(self.authority, self.m1, (self.c1,))

    def test_stale_prefix_cannot_advance_head(self) -> None:
        with self.assertRaisesRegex(ValueError, "stale or forked"):
            self.authority.register(self.m1, problem_id="p2", arm=Arm.PRODUCT_ONLY,
                                    attempt_index=0, request_sha256=sha("late"))

    def test_omitted_completion_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "omitted dispatch completions"):
            join_completions(self.authority, self.m2, (self.c1,))

    def test_replayed_completion_within_join_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "replayed completion"):
            join_completions(self.authority, self.m2, (self.c1, self.c1, self.c2))

    def test_successful_close_is_one_shot_and_persists_across_reopen(self) -> None:
        first = join_completions(self.authority, self.m2, (self.c1, self.c2))
        self.assertTrue(first.receipt.close_sha256)
        reopened = DispatchAuthority(self.db, "run-1")
        with self.assertRaisesRegex(ValueError, "already consumed"):
            join_completions(reopened, self.m2, (self.c1, self.c2))

    def test_fabricated_unregistered_completion_is_rejected(self) -> None:
        other_db = os.path.join(self.tmp.name, "other.sqlite")
        other_auth = DispatchAuthority(other_db, "run-1")
        _, other_signer = other_auth.register(other_auth.current_manifest(), problem_id="p9",
            arm=Arm.VERIFIED_CHAIN, attempt_index=0, request_sha256=sha("fake"))
        fake = other_signer.complete(status=CompletionStatus.SUCCEEDED, payload_sha256=sha("fake-out"))
        with self.assertRaisesRegex(ValueError, "unregistered dispatch"):
            join_completions(self.authority, self.m2, (self.c1, self.c2, fake))

    def test_cross_arm_rebinding_is_rejected(self) -> None:
        forged = replace(self.c1, arm=Arm.PORTFOLIO)
        with self.assertRaisesRegex(ValueError, "pre-dispatch binding: arm"):
            join_completions(self.authority, self.m2, (forged, self.c2))

    def test_tampered_completion_payload_fails_signature(self) -> None:
        forged = replace(self.c1, payload_sha256=sha("tampered"))
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            join_completions(self.authority, self.m2, (forged, self.c2))

    def test_closer_has_no_signing_key_and_public_material_cannot_forge(self) -> None:
        self.assertNotIn("completion_keys", join_completions.__code__.co_varnames)
        self.assertNotIn("for_entry", dir(CompletionRecord))
        forged = CompletionRecord(
            self.c1.run_id, self.c1.dispatch_id, self.c1.entry_sha256, self.c1.problem_id,
            self.c1.arm, self.c1.attempt_index, self.c1.request_sha256,
            CompletionStatus.SUCCEEDED, sha("invented"), "00" * (256 * 32),
        )
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            join_completions(self.authority, self.m2, (forged, self.c2))

    def test_signer_is_one_shot(self) -> None:
        with self.assertRaisesRegex(ValueError, "one-shot"):
            self.s1.complete(status=CompletionStatus.ERROR, payload_sha256=sha("second"))

    def test_live_entry_mutation_cannot_keep_cached_manifest_hash_valid(self) -> None:
        original_head = self.m2.manifest_sha256
        e = self.m2.entries[0]
        object.__setattr__(e, "arm", Arm.PORTFOLIO)
        object.__setattr__(e, "completion_verifier_sha256", self.m2.entries[1].completion_verifier_sha256)
        with self.assertRaisesRegex(ValueError, "live dispatch entry"):
            _ = self.m2.manifest_sha256
        with self.assertRaisesRegex(ValueError, "live dispatch entry"):
            join_completions(self.authority, self.m2, (self.c1, self.c2))
        self.assertEqual(original_head, self.authority.current_manifest().manifest_sha256)

    def test_authority_db_path_must_be_durable(self) -> None:
        with self.assertRaisesRegex(ValueError, "durable filesystem"):
            DispatchAuthority(":memory:", "run-x")


if __name__ == "__main__":
    unittest.main()
