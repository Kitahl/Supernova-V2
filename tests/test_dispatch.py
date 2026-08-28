from __future__ import annotations

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import ScheduledChatArtifactEnvelope, ScheduledChatArtifactKind
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import (
    CloseReceipt,
    CompletionJoin,
    CompletionPayload,
    CompletionSigner,
    CompletionStatus,
    DispatchAuthority,
    DispatchEntry,
    DispatchManifest,
    JoinedCompletion,
    RunTerminalState,
    join_completions,
)
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.verifier import VerifierResult, VerifierStatus

LOCK_ROOT = "914c05427e1e7e0979f4ca058f90fb3138ee0d3319233b415194c10e67d3683b"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class DispatchTests(unittest.TestCase):
    def problem(self, native_id: str = "problem-001") -> BenchmarkProblemIdentity:
        return BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            native_id,
        )

    def artifact(
        self,
        payload: str,
        *,
        kind: ScheduledChatArtifactKind,
        request: FrozenProblemRequest | None = None,
        run_id: str = "run-1",
        arm: Arm = Arm.ORDINARY,
        attempt: int = 0,
    ) -> ScheduledChatArtifactEnvelope:
        problem = request.problem if request is not None else self.problem()
        if request is not None:
            run_id, arm, attempt = request.run_id, request.arm, request.attempt
        return ScheduledChatArtifactEnvelope.from_visible_utf8(
            payload,
            kind=kind,
            run_id=run_id,
            problem_id=problem.canonical_id,
            arm=arm,
            attempt=attempt,
        )

    def request(
        self,
        *,
        arm: Arm = Arm.ORDINARY,
        attempt: int = 0,
        native_id: str = "problem-001",
        run_id: str = "run-1",
    ) -> FrozenProblemRequest:
        problem = self.problem(native_id)
        return FrozenProblemRequest(
            run_id=run_id,
            experiment_id="goal1-pilot-v1",
            problem=problem,
            benchmark_root_sha256=LOCK_ROOT,
            problem_sha256=sha("problem:" + native_id),
            arm=arm,
            attempt=attempt,
            budget_id="budget-v1",
            budget_sha256=sha("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=sha("lean-runtime"),
            request_artifact=ScheduledChatArtifactEnvelope.from_visible_utf8(
                f"Prove {native_id}",
                kind=ScheduledChatArtifactKind.REQUEST,
                run_id=run_id,
                problem_id=problem.canonical_id,
                arm=arm,
                attempt=attempt,
            ),
        )

    def result(
        self,
        request: FrozenProblemRequest,
        *,
        status: AttemptStatus = AttemptStatus.ANSWERED,
        payload: str = "by\n  norm_num",
        error: str | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            frozen_request_sha256=request.frozen_request_sha256,
            run_id=request.run_id,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt=request.attempt,
            request_artifact_id=request.request_artifact.artifact_id,
            response_artifact=self.artifact(
                payload,
                kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
                request=request,
            ),
            status=status,
            error=error,
        )

    def receipt(
        self,
        request: FrozenProblemRequest,
        result: AttemptResult,
        status: VerifierStatus = VerifierStatus.PASS,
    ) -> LeanVerifierReceipt:
        if status is VerifierStatus.PASS:
            vr = VerifierResult(status, ("lake", "env", "lean", "proof.lean"), 0, "ok\n", "", 12)
        elif status is VerifierStatus.FAIL:
            vr = VerifierResult(status, ("lake", "env", "lean", "proof.lean"), 1, "", "bad\n", 12)
        else:
            vr = VerifierResult(
                status,
                ("lake", "env", "lean", "proof.lean"),
                None,
                "",
                "",
                12,
                "timeout" if status is VerifierStatus.TIMEOUT else "spawn failed",
            )
        return LeanVerifierReceipt.from_verifier_result(
            request=request,
            attempt_result=result,
            verifier_result=vr,
        )

    def typed_payload(
        self,
        request: FrozenProblemRequest,
        *,
        attempt_status: AttemptStatus = AttemptStatus.ANSWERED,
        verifier_status: VerifierStatus = VerifierStatus.PASS,
    ) -> CompletionPayload:
        error = "executor failed" if attempt_status is AttemptStatus.ERROR else None
        result = self.result(request, status=attempt_status, error=error)
        receipt = None
        if attempt_status is AttemptStatus.ANSWERED:
            receipt = self.receipt(request, result, verifier_status)
        return CompletionPayload(request, result, receipt)

    def register(
        self,
        authority: DispatchAuthority,
        manifest: DispatchManifest,
        request: FrozenProblemRequest,
    ) -> tuple[DispatchManifest, CompletionSigner]:
        signer = CompletionSigner.generate()
        updated = authority.register(
            manifest,
            request=request,
            completion_verifier_sha256=signer.public_commitment,
        )
        return updated, signer

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name, "authority.sqlite").resolve())
        self.authority = DispatchAuthority(self.db, "run-1")
        self.r1 = self.request(arm=Arm.ORDINARY, native_id="problem-001")
        self.r2 = self.request(arm=Arm.PORTFOLIO, native_id="problem-001")
        m0 = self.authority.current_manifest()
        self.m1, self.s1 = self.register(self.authority, m0, self.r1)
        self.m2, self.s2 = self.register(self.authority, self.m1, self.r2)
        self.p1 = self.typed_payload(self.r1)
        self.p2 = self.typed_payload(self.r2, verifier_status=VerifierStatus.FAIL)
        self.c1 = self.s1.complete(entry=self.m1.entries[-1], payload=self.p1)
        self.c2 = self.s2.complete(entry=self.m2.entries[-1], payload=self.p2)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manifest_is_immutable_append_only_hash_chain(self) -> None:
        self.assertEqual(1, len(self.m1.entries))
        self.assertEqual(2, len(self.m2.entries))
        self.assertEqual(self.m1.entries[-1].entry_sha256, self.m2.entries[-1].predecessor_sha256)
        self.assertNotEqual(self.m1.manifest_sha256, self.m2.manifest_sha256)

    def test_registration_is_bound_to_exact_frozen_problem_request(self) -> None:
        entry = self.m1.entries[0]
        self.assertEqual(self.r1.frozen_request_sha256, entry.request_sha256)
        self.assertEqual(self.r1.problem_id, entry.problem_id)
        self.assertEqual(self.r1.arm, entry.arm)
        self.assertEqual(self.r1.attempt, entry.attempt_index)
        self.assertNotIn("request_sha256", self.authority.register.__code__.co_varnames)

    def test_duplicate_attempt_is_rejected(self) -> None:
        signer = CompletionSigner.generate()
        with self.assertRaisesRegex(ValueError, "duplicate dispatch attempt"):
            self.authority.register(
                self.m2,
                request=self.r1,
                completion_verifier_sha256=signer.public_commitment,
            )

    def test_manifest_reorder_or_relink_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "append-only chain"):
            DispatchManifest("run-1", tuple(reversed(self.m2.entries)))
        e = self.m2.entries[1]
        forged = DispatchEntry.create(
            run_id=e.run_id,
            sequence=e.sequence,
            problem_id=e.problem_id,
            arm=e.arm,
            attempt_index=e.attempt_index,
            request_sha256=e.request_sha256,
            completion_verifier_sha256=e.completion_verifier_sha256,
            predecessor_sha256="0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "append-only chain"):
            DispatchManifest("run-1", (self.m2.entries[0], forged))

    def test_join_accepts_complete_out_of_order_input_and_persists_readback(self) -> None:
        result = join_completions(self.authority, self.m2, (self.c2, self.c1))
        self.assertEqual([e.dispatch_id for e in self.m2.entries], [j.dispatch.dispatch_id for j in result.joined])
        self.assertEqual(self.m2.manifest_sha256, result.receipt.manifest_sha256)
        reopened = DispatchAuthority(self.db, "run-1")
        self.assertEqual(result, reopened.read_closed_join())
        self.assertEqual(result, reopened.verify_closed_join(result))

    def test_stale_prefix_cannot_self_authenticate_or_advance(self) -> None:
        with self.assertRaisesRegex(ValueError, "authoritative latest"):
            join_completions(self.authority, self.m1, (self.c1,))
        signer = CompletionSigner.generate()
        late = self.request(arm=Arm.PRODUCT_ONLY, native_id="problem-002")
        with self.assertRaisesRegex(ValueError, "stale or forked"):
            self.authority.register(self.m1, request=late, completion_verifier_sha256=signer.public_commitment)

    def test_omitted_and_replayed_completion_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "omitted dispatch completions"):
            join_completions(self.authority, self.m2, (self.c1,))
        with self.assertRaisesRegex(ValueError, "replayed completion"):
            join_completions(self.authority, self.m2, (self.c1, self.c1, self.c2))

    def test_successful_close_is_one_shot_across_reopen(self) -> None:
        join_completions(self.authority, self.m2, (self.c1, self.c2))
        reopened = DispatchAuthority(self.db, "run-1")
        with self.assertRaisesRegex(ValueError, "already consumed"):
            join_completions(reopened, self.m2, (self.c1, self.c2))

    def test_fabricated_unregistered_completion_is_rejected(self) -> None:
        other_db = str(Path(self.tmp.name, "other.sqlite").resolve())
        other = DispatchAuthority(other_db, "run-1")
        req = self.request(arm=Arm.VERIFIED_CHAIN, native_id="problem-009")
        manifest, signer = self.register(other, other.current_manifest(), req)
        fake = signer.complete(entry=manifest.entries[-1], payload=self.typed_payload(req))
        with self.assertRaisesRegex(ValueError, "unregistered dispatch"):
            join_completions(self.authority, self.m2, (self.c1, self.c2, fake))

    def test_typed_payload_prevents_arbitrary_digest_and_request_rebinding(self) -> None:
        raw = self.p1.to_mapping()
        raw["payload_sha256"] = sha("caller-forged")
        with self.assertRaisesRegex(ValueError, "payload_sha256"):
            CompletionPayload.from_mapping(raw)
        other = self.request(arm=Arm.ORDINARY, native_id="problem-002")
        other_payload = self.typed_payload(other)
        signer = CompletionSigner.generate()
        m3 = self.authority.register(
            self.authority.current_manifest(),
            request=other,
            completion_verifier_sha256=signer.public_commitment,
        )
        with self.assertRaisesRegex(ValueError, "does not match registered frozen request"):
            signer.complete(entry=m3.entries[-1], payload=self.p1)
        _ = signer.complete(entry=m3.entries[-1], payload=other_payload)

    def test_completion_status_is_derived_from_typed_attempt_and_verifier_evidence(self) -> None:
        req = self.request(native_id="problem-status")
        for verifier_status, expected in (
            (VerifierStatus.PASS, CompletionStatus.SUCCEEDED),
            (VerifierStatus.FAIL, CompletionStatus.FAILED),
            (VerifierStatus.TIMEOUT, CompletionStatus.TIMEOUT),
            (VerifierStatus.ERROR, CompletionStatus.ERROR),
        ):
            with self.subTest(verifier_status=verifier_status):
                self.assertEqual(expected, self.typed_payload(req, verifier_status=verifier_status).status)
        self.assertEqual(CompletionStatus.FAILED, self.typed_payload(req, attempt_status=AttemptStatus.NO_ANSWER).status)
        self.assertEqual(CompletionStatus.ERROR, self.typed_payload(req, attempt_status=AttemptStatus.ERROR).status)
        result = self.result(req, status=AttemptStatus.ANSWERED)
        with self.assertRaisesRegex(ValueError, "requires a Lean verifier receipt"):
            CompletionPayload(req, result, None)

    def test_executor_signer_is_created_outside_authority_and_copy_guards_are_explicit(self) -> None:
        signer = CompletionSigner.generate()
        with self.assertRaisesRegex(TypeError, "must not be copied"):
            copy.copy(signer)
        with self.assertRaisesRegex(TypeError, "must not be copied"):
            copy.deepcopy(signer)
        with self.assertRaisesRegex(TypeError, "use CompletionSigner.generate"):
            CompletionSigner(b"x" * (256 * 2 * 32))
        self.assertIsInstance(signer.public_commitment, str)

    def test_signer_is_one_shot(self) -> None:
        with self.assertRaisesRegex(ValueError, "one-shot"):
            self.s1.complete(entry=self.m1.entries[-1], payload=self.p1)

    def test_forged_public_join_requires_authority_verification(self) -> None:
        real = join_completions(self.authority, self.m2, (self.c1, self.c2))
        forged_set = hashlib.sha256(b"different").hexdigest()
        forged_receipt = CloseReceipt.create(real.receipt.run_id, real.receipt.manifest_sha256, forged_set)
        bypass = object.__new__(CompletionJoin)
        object.__setattr__(bypass, "joined", real.joined)
        object.__setattr__(bypass, "receipt", forged_receipt)
        with self.assertRaisesRegex(ValueError, "not the authority-backed"):
            self.authority.verify_closed_join(bypass)
        cloned = CompletionJoin(tuple(real.joined), real.receipt)
        self.assertEqual(real, self.authority.verify_closed_join(cloned))

    def test_live_entry_mutation_cannot_keep_cached_manifest_hash_valid(self) -> None:
        original_head = self.m2.manifest_sha256
        e = self.m2.entries[0]
        object.__setattr__(e, "arm", Arm.PORTFOLIO)
        with self.assertRaisesRegex(ValueError, "live dispatch entry"):
            _ = self.m2.manifest_sha256
        with self.assertRaisesRegex(ValueError, "live dispatch entry"):
            join_completions(self.authority, self.m2, (self.c1, self.c2))
        self.assertEqual(original_head, self.authority.current_manifest().manifest_sha256)

    def test_relative_db_path_is_rejected_and_cwd_cannot_switch_trust_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            DispatchAuthority("relative.sqlite", "run-x")
        original = self.authority.db_path
        cwd = os.getcwd()
        other = tempfile.TemporaryDirectory()
        try:
            os.chdir(other.name)
            self.assertEqual(original, self.authority.db_path)
            reopened = DispatchAuthority(original, "run-1")
            self.assertEqual(self.m2.manifest_sha256, reopened.current_manifest().manifest_sha256)
        finally:
            os.chdir(cwd)
            other.cleanup()

    def test_lost_executor_capability_can_abort_non_credit_and_survives_reopen(self) -> None:
        db = str(Path(self.tmp.name, "abort.sqlite").resolve())
        auth = DispatchAuthority(db, "run-abort")
        req = self.request(run_id="run-abort", native_id="problem-abort")
        signer = CompletionSigner.generate()
        manifest = auth.register(
            auth.current_manifest(),
            request=req,
            completion_verifier_sha256=signer.public_commitment,
        )
        del signer
        reopened = DispatchAuthority(db, "run-abort")
        receipt = reopened.abort("executor capability lost before durable delivery")
        self.assertEqual(RunTerminalState.ABORTED, reopened.terminal_state())
        self.assertEqual(receipt, DispatchAuthority(db, "run-abort").read_abort_receipt())
        with self.assertRaisesRegex(ValueError, "aborted"):
            join_completions(reopened, manifest, ())

    def test_g1_118_typed_integration_rejects_mismatched_result_and_accepts_valid_receipt(self) -> None:
        req = self.request(native_id="problem-g118")
        result = self.result(req)
        receipt = self.receipt(req, result, VerifierStatus.PASS)
        payload = CompletionPayload(req, result, receipt)
        db = str(Path(self.tmp.name, "g118.sqlite").resolve())
        auth = DispatchAuthority(db, "run-1")
        signer = CompletionSigner.generate()
        manifest = auth.register(
            auth.current_manifest(),
            request=req,
            completion_verifier_sha256=signer.public_commitment,
        )
        record = signer.complete(entry=manifest.entries[-1], payload=payload)
        closed = auth.close(manifest, (record,))
        self.assertEqual(CompletionStatus.SUCCEEDED, closed.joined[0].completion.status)

        other_req = self.request(native_id="problem-other")
        other_result = self.result(other_req)
        with self.assertRaisesRegex(ValueError, "does not match"):
            CompletionPayload(req, other_result, None)


if __name__ == "__main__":
    unittest.main()
