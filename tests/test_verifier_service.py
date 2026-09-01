from __future__ import annotations

import json
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm
from supernova_goal1.dispatch import DispatchEntry
from supernova_goal1.execution.baselines import BaselineDispatch
from supernova_goal1.execution.common import FrozenProblemRequest
from supernova_goal1.problem import BenchmarkProblemIdentity
from supernova_goal1.production_verifier import ProductionVerifierPort
from supernova_goal1.verifier_evidence import canonical_bytes
from supernova_goal1.verifier_service import (
    MAX_REQUEST_BYTES,
    REQUEST_SCHEMA,
    AuthoritativeVerifierAttempt,
    ProductionVerifierService,
    VerifierAttemptLocator,
)


def sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class VerifierServiceProtocolTests(unittest.TestCase):
    def locator(self) -> VerifierAttemptLocator:
        return VerifierAttemptLocator(
            run_id="run-one",
            actual_dispatch_id="a" * 64,
        )

    def authoritative_attempt(self) -> AuthoritativeVerifierAttempt:
        problem = BenchmarkProblemIdentity(
            "miniF2F-Lean4-Kimina-composite",
            "deepseek-v1.5-2c4ba911+kimina-5def318",
            "validation",
            "demo",
        )
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            b"prove",
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id="run-one",
            problem_id=problem.canonical_id,
            arm=Arm.ORDINARY,
            attempt=0,
        )
        request = FrozenProblemRequest(
            run_id="run-one",
            experiment_id="goal1-confirmatory-v1",
            problem=problem,
            benchmark_root_sha256=sha("benchmark"),
            problem_sha256=sha("source"),
            arm=Arm.ORDINARY,
            attempt=0,
            budget_id="budget-one",
            budget_sha256=sha("budget"),
            model_usage_basis="visible_utf8_bytes",
            runtime_sha256=sha("requested-runtime"),
            request_artifact=artifact,
            protocol_dispatch_id="dispatch-" + sha("protocol-dispatch"),
            confirmatory_manifest_sha256=sha("manifest"),
        )
        entry = DispatchEntry.create(
            run_id=request.run_id,
            sequence=0,
            problem_id=request.problem_id,
            arm=request.arm,
            attempt_index=request.attempt,
            request_sha256=request.frozen_request_sha256,
            completion_verifier_sha256=sha("completion-verifier"),
            predecessor_sha256="0" * 64,
        )
        return AuthoritativeVerifierAttempt(
            BaselineDispatch(request, entry, ()),
            b"  trivial\n",
        )

    def test_locator_request_is_exact_and_canonical(self) -> None:
        locator = self.locator()
        self.assertEqual(
            locator,
            VerifierAttemptLocator.from_request(canonical_bytes(locator.request_body())),
        )
        with self.assertRaisesRegex(ValueError, "canonical JSON"):
            VerifierAttemptLocator.from_request(
                json.dumps(locator.request_body(), indent=2).encode("utf-8")
            )

    def test_fabricated_in_process_valid_is_rejected_at_boundary(self) -> None:
        request = self.locator().request_body()
        request["verdict"] = "VALID"
        request["checker_exit_status"] = 0
        with self.assertRaisesRegex(ValueError, "only an attempt locator"):
            VerifierAttemptLocator.from_request(canonical_bytes(request))

    def test_target_and_runtime_substitution_are_rejected_at_boundary(self) -> None:
        substitutions = (
            {"theorem_names": ["attacker_target"]},
            {"theorem_target_set_sha256": "b" * 64},
            {"requested_runtime_sha256": "c" * 64},
            {"actual_runtime_sha256": "d" * 64},
            {"source_template_sha256": "e" * 64},
            {"rendered_source_sha256": "f" * 64},
        )
        for substitution in substitutions:
            with self.subTest(field=next(iter(substitution))):
                request = self.locator().request_body() | substitution
                with self.assertRaisesRegex(ValueError, "only an attempt locator"):
                    VerifierAttemptLocator.from_request(canonical_bytes(request))

    def test_no_public_caller_supplied_subject_method_remains(self) -> None:
        self.assertFalse(hasattr(ProductionVerifierPort, "verify_subject"))
        self.assertTrue(hasattr(ProductionVerifierPort, "_verify_resolved_subject"))

    def test_service_resolves_registered_dispatch_and_candidate_from_locator(self) -> None:
        attempt = self.authoritative_attempt()
        port = object.__new__(ProductionVerifierPort)
        port.subject_builder = None
        service = ProductionVerifierService(port, {attempt.locator: attempt})
        sentinel = object()
        with patch.object(ProductionVerifierPort, "verify", return_value=sentinel) as run:
            self.assertIs(sentinel, service.verify(attempt.locator))
        run.assert_called_once_with(attempt.dispatch, attempt.candidate)
        with self.assertRaisesRegex(KeyError, "unknown authoritative"):
            service.verify(
                VerifierAttemptLocator("run-one", "f" * 64)
            )

    def test_service_rejects_ports_with_caller_subject_builder(self) -> None:
        attempt = self.authoritative_attempt()
        port = object.__new__(ProductionVerifierPort)
        port.subject_builder = lambda *_args: None
        with self.assertRaisesRegex(ValueError, "authority-derived"):
            ProductionVerifierService(port, {attempt.locator: attempt})

    def test_duplicate_fields_are_rejected_before_resolution(self) -> None:
        raw = (
            b'{"actual_dispatch_id":"'
            + b"a" * 64
            + b'","run_id":"run-one","run_id":"run-two","schema":"'
            + REQUEST_SCHEMA.encode("ascii")
            + b'"}'
        )
        with self.assertRaisesRegex(ValueError, "duplicate.*'run_id'"):
            VerifierAttemptLocator.from_request(raw)

    def test_oversized_request_is_rejected_before_json_parsing(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed byte limit"):
            VerifierAttemptLocator.from_request(b"{" + b"x" * MAX_REQUEST_BYTES)


if __name__ == "__main__":
    unittest.main()
