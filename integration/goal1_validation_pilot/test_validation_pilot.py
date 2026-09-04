from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from integration.goal1_validation_pilot.run_validation_pilot import (
    MODEL_TIMEOUT_SECONDS,
    PLAN_PATH,
    ModelContainerObservation,
    adapt_model_completion,
    completion_summary,
    exact_smoke_problem_signed_valid_gate,
    github_failure_annotation,
    parse_generation_frame,
    selected_problem_ids,
    verifier_launcher,
)
from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.execution.common import Arm, AttemptStatus


class Goal1ValidationPilotTests(unittest.TestCase):
    def test_adapter_preserves_raw_tactic_body(self) -> None:
        raw = b"simp [Finset.sum_range_succ]\n"
        self.assertEqual(
            (raw, "RAW_UNCHANGED"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_extracts_exact_theorem_from_final_fence(self) -> None:
        raw = (
            b"analysis\n\x60\x60\x60tactics\nring\n\x60\x60\x60\n"
            b"\x60\x60\x60lean4\ntheorem target (n : Nat) : n = n := by\n  rfl\n"
            b"\x60\x60\x60\n\n  \n"
        )
        self.assertEqual(
            (b"rfl", "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_extracts_final_tactic_fence(self) -> None:
        raw = b"reasoning\n\x60\x60\x60tactics\n  omega\n\x60\x60\x60\n"
        self.assertEqual(
            (b"omega", "FINAL_LEAN_FENCE_TACTIC_BODY"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_keeps_nested_by_proof_in_the_tactic_body(self) -> None:
        raw = (
            b"\x60\x60\x60lean\ntheorem target : True := by\n"
            b"  have h : True := by trivial\n  exact h\n\x60\x60\x60\n"
        )
        self.assertEqual(
            (
                b"have h : True := by trivial\n  exact h",
                "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY",
            ),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_rejects_ambiguous_or_wrong_declaration(self) -> None:
        duplicate = (
            b"\x60\x60\x60lean\ntheorem target : True := by trivial\n"
            b"theorem target : True := by trivial\n\x60\x60\x60\n"
        )
        wrong = b"\x60\x60\x60lean\ntheorem other : True := by trivial\n\x60\x60\x60\n"
        for raw in (duplicate, wrong):
            candidate, rule = adapt_model_completion(raw, theorem_name="target")
            self.assertEqual(raw, candidate)
            self.assertEqual("REJECTED_DECLARATION_RAW_PASSTHROUGH", rule)

    def test_adapter_rejects_unclosed_trailing_fence(self) -> None:
        raw = (
            b"\x60\x60\x60lean\ntheorem target : True := by\n  trivial\n"
            b"\x60\x60\x60\n\x60\x60\x60lean\n"
        )
        self.assertEqual(
            (raw, "REJECTED_WRAPPER_RAW_PASSTHROUGH"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_rejects_empty_fence(self) -> None:
        raw = b"\x60\x60\x60lean\n\n\x60\x60\x60\n"
        self.assertEqual(
            (raw, "REJECTED_EMPTY_FENCE_RAW_PASSTHROUGH"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_plan_freezes_non_credit_two_then_twenty(self) -> None:
        plan = json.loads(Path(PLAN_PATH).read_text(encoding="utf-8"))
        self.assertEqual("validation", plan["benchmark"]["allowed_split"])
        self.assertEqual("test", plan["benchmark"]["forbidden_split"])
        self.assertEqual(2, plan["benchmark"]["record_schema_version"])
        self.assertEqual(
            "c404215b329dcaca4228a8a23eaa21b64277c85e536c441232e91355ec96d9d8",
            plan["benchmark"]["benchmark_root_sha256"],
        )
        self.assertEqual("NONE", plan["scientific_credit"])
        self.assertEqual(0, plan["countable_attempts_after"])
        self.assertEqual(2, plan["stages"]["smoke_0_1_percent"]["attempt_count"])
        self.assertEqual(20, plan["stages"]["pilot_1_percent"]["attempt_count"])
        self.assertTrue(plan["stages"]["smoke_0_1_percent"]["stop_after_report"])
        self.assertTrue(plan["stages"]["pilot_1_percent"]["stop_after_report"])
        self.assertEqual(60, plan["timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual("UNKNOWN", plan["timing_policy"]["timeout_verdict"])
        self.assertNotIn("PROSE_SIGNED_INVALID_UNDER_5000_MS", plan["pre_model_gates"])
        self.assertEqual(60, verifier_launcher(plan).timeout_seconds)
        override = "ghcr.io/kitahl/supernova-goal1-verifier@sha256:" + "f" * 64
        self.assertEqual(override, verifier_launcher(plan, image_ref=override).image_ref)
        self.assertEqual(300, plan["model_timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual(
            "NO_ANSWER", plan["model_timing_policy"]["incomplete_finish_reason"]
        )
        self.assertEqual(300, MODEL_TIMEOUT_SECONDS)

    def test_exact_problem_gate_refuses_a_different_theorem(self) -> None:
        with self.assertRaisesRegex(ValueError, "bound to amc12a_2003_p1"):
            exact_smoke_problem_signed_valid_gate(
                SimpleNamespace(),
                SimpleNamespace(native_id="different"),
            )

    def test_selection_is_stable_and_order_independent(self) -> None:
        left = selected_problem_ids(("c", "a", "b"), seed="frozen", count=2)
        right = selected_problem_ids(("b", "c", "a"), seed="frozen", count=2)
        self.assertEqual(left, right)
        self.assertEqual(2, len(left))

    def test_generation_frame_returns_only_discrete_completion(self) -> None:
        frame = {
            "completion_utf8": "  norm_num\n",
            "schema": "supernova.hermetic-generation-response.v1",
            "status": "ANSWERED",
        }
        raw = (json.dumps(frame, separators=(",", ":"), sort_keys=True) + "\n").encode()
        self.assertEqual(b"  norm_num\n", parse_generation_frame(raw))
        with self.assertRaisesRegex(ValueError, "trailing transcript"):
            parse_generation_frame(raw + b"Loading model...\n")

    def test_typed_model_error_is_reported_without_observation(self) -> None:
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            b"",
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            run_id="run",
            problem_id="sha256:" + "a" * 64,
            arm=Arm.ORDINARY,
            attempt=0,
        )
        completion = SimpleNamespace(
            dispatch_id="dispatch",
            status=SimpleNamespace(value="COMPLETED"),
            payload=SimpleNamespace(
                verifier_receipt=None,
                attempt_result=SimpleNamespace(
                    response_artifact=artifact,
                    status=AttemptStatus.ERROR,
                    error="original model failure",
                ),
            ),
        )
        summary = completion_summary(
            arm=Arm.ORDINARY,
            completion=completion,
            model_observation=None,
            verifier_port=SimpleNamespace(bindings_by_dispatch={}),
        )
        self.assertEqual("ERROR", summary["attempt_status"])
        self.assertEqual("original model failure", summary["model_error"])
        self.assertIsNone(summary["model_elapsed_milliseconds"])

    def test_completion_summary_keeps_raw_and_adapted_hashes(self) -> None:
        raw = (
            b"\x60\x60\x60lean\ntheorem target : True := by\n  trivial\n\x60\x60\x60\n"
        )
        adapted = b"trivial"
        artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
            adapted,
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            run_id="run",
            problem_id="sha256:" + "a" * 64,
            arm=Arm.ORDINARY,
            attempt=0,
        )
        completion = SimpleNamespace(
            dispatch_id="dispatch",
            status=SimpleNamespace(value="COMPLETED"),
            payload=SimpleNamespace(
                verifier_receipt=None,
                attempt_result=SimpleNamespace(
                    response_artifact=artifact,
                    status=AttemptStatus.ANSWERED,
                    error=None,
                ),
            ),
        )
        observation = ModelContainerObservation(
            raw_completion=raw,
            completion=adapted,
            adaptation_rule="FINAL_LEAN_FENCE_EXACT_THEOREM_BODY",
            elapsed_milliseconds=12,
            image_id="sha256:" + "b" * 64,
            stderr="",
            teardown_observed=True,
        )
        summary = completion_summary(
            arm=Arm.ORDINARY,
            completion=completion,
            model_observation=observation,
            verifier_port=SimpleNamespace(bindings_by_dispatch={}),
        )
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(), summary["raw_completion_sha256"]
        )
        self.assertEqual(
            hashlib.sha256(adapted).hexdigest(), summary["candidate_sha256"]
        )
        self.assertEqual(
            "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY", summary["adaptation_rule"]
        )

    def test_github_failure_annotation_contains_only_typed_metadata(self) -> None:
        annotation = github_failure_annotation(
            {
                "attempts": [
                    {
                        "adaptation_rule": "RAW_UNCHANGED",
                        "arm": "ordinary",
                        "attempt_status": "ANSWERED",
                        "candidate_bytes": 4,
                        "candidate_sha256": "a" * 64,
                        "model_elapsed_milliseconds": 12,
                        "model_error": None,
                        "raw_completion_bytes": 4,
                        "raw_completion_sha256": "b" * 64,
                        "verifier_evidence": {
                            "elapsed_milliseconds": 7,
                            "record_sha256": "c" * 64,
                            "termination_cause": "EXITED",
                            "verdict": "INVALID",
                        },
                    }
                ],
                "one_percent_admission": "FAIL",
                "signed_valid_model_responses": 0,
            }
        )
        self.assertTrue(
            annotation.startswith("::error title=Goal-1 0.1-percent admission failed::")
        )
        self.assertNotIn("candidate_utf8", annotation)
        self.assertNotIn("raw_completion_utf8", annotation)
        self.assertIn('"verdict":"INVALID"', annotation)


if __name__ == "__main__":
    unittest.main()
