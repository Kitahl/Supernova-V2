from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from integration.goal1_validation_pilot.run_validation_pilot import (
    MODEL_TIMEOUT_SECONDS,
    PLAN_PATH,
    completion_summary,
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
    def test_plan_freezes_non_credit_two_then_twenty(self) -> None:
        plan = json.loads(Path(PLAN_PATH).read_text(encoding="utf-8"))
        self.assertEqual("validation", plan["benchmark"]["allowed_split"])
        self.assertEqual("test", plan["benchmark"]["forbidden_split"])
        self.assertEqual("NONE", plan["scientific_credit"])
        self.assertEqual(0, plan["countable_attempts_after"])
        self.assertEqual(2, plan["stages"]["smoke_0_1_percent"]["attempt_count"])
        self.assertEqual(20, plan["stages"]["pilot_1_percent"]["attempt_count"])
        self.assertTrue(plan["stages"]["smoke_0_1_percent"]["stop_after_report"])
        self.assertTrue(plan["stages"]["pilot_1_percent"]["stop_after_report"])
        self.assertEqual(60, plan["timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual("UNKNOWN", plan["timing_policy"]["timeout_verdict"])
        self.assertNotIn(
            "PROSE_SIGNED_INVALID_UNDER_5000_MS", plan["pre_model_gates"]
        )
        self.assertEqual(60, verifier_launcher(plan).timeout_seconds)
        self.assertEqual(300, plan["model_timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual(300, MODEL_TIMEOUT_SECONDS)

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


if __name__ == "__main__":
    unittest.main()
