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
    _one_percent_metrics,
    adapt_model_completion,
    adapt_product_completion,
    completion_summary,
    exact_smoke_problem_signed_valid_gate,
    github_failure_annotation,
    github_pilot_notice,
    one_percent_schedule,
    parse_generation_frame,
    selected_problem_ids,
    verifier_launcher,
)
from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.confirmatory_io import (
    FINAL_PREFIX,
    build_verification_subject,
    classify_baseline_response,
)
from supernova_goal1.execution.common import Arm, AttemptStatus
from supernova_goal1.production_verifier import FrozenLeanProblemSource


def trusted_fixture(
    header: bytes = b"theorem target : True := by\n",
) -> FrozenLeanProblemSource:
    return FrozenLeanProblemSource.from_record(
        {
            "schema_version": 1,
            "problem_id": "target",
            "split": "validation",
            "source_id": "non-credit-adapter-regression",
            "source_record_sha256": "1" * 64,
            "lean_code_sha256": hashlib.sha256(header).hexdigest(),
            "lean_code": header.decode(),
            "informal_prefix": "",
        },
        expected_split="validation",
    )


class Goal1ValidationPilotTests(unittest.TestCase):
    def test_adapter_preserves_layout_through_actual_subject_assembly(self) -> None:
        header = b"import Mathlib\n\ntheorem target : True := by\n"
        body = b"  have h : True := by\n    trivial\n  exact h\n"
        source = FrozenLeanProblemSource.from_record(
            {
                "schema_version": 1,
                "problem_id": "target",
                "split": "validation",
                "source_id": "non-credit-layout-regression",
                "source_record_sha256": "1" * 64,
                "lean_code_sha256": hashlib.sha256(header).hexdigest(),
                "lean_code": header.decode(),
                "informal_prefix": "",
            },
            expected_split="validation",
        )
        raw = b"```lean\n" + header + body + b"```\n"
        candidate, _ = adapt_model_completion(
            raw, theorem_name="target", trusted_source=source
        )
        subject = build_verification_subject(
            source, classify_baseline_response(candidate, source)
        )
        self.assertEqual(
            header + body, subject.challenge_source + subject.candidate_source
        )
        self.assertEqual(body, candidate)

    def test_adapter_preserves_inline_by_and_crlf_body_layout(self) -> None:
        cases = (
            (b"theorem target : True := by trivial\n", b" trivial\n"),
            (b"theorem target : True := by \r\n  trivial\r\n", b"  trivial\r\n"),
        )
        for declaration, expected in cases:
            candidate, _ = adapt_model_completion(
                b"```lean\n" + declaration + b"```\n",
                theorem_name="target",
                trusted_source=trusted_fixture(),
            )
            self.assertEqual(expected, candidate)

    def test_adapter_never_reindents_multiline_literal_content(self) -> None:
        body = b'  let text := "first\nsecond"\n  trivial\n'
        raw = b"```lean\ntheorem target : True := by\n" + body + b"```\n"
        candidate, _ = adapt_model_completion(
            raw, theorem_name="target", trusted_source=trusted_fixture()
        )
        self.assertEqual(body, candidate)

    def test_adapter_preserves_raw_tactic_body(self) -> None:
        raw = b"simp [Finset.sum_range_succ]\n"
        self.assertEqual(
            (raw, "RAW_UNCHANGED"),
            adapt_model_completion(
                raw, theorem_name="target", trusted_source=trusted_fixture()
            ),
        )

    def test_adapter_extracts_exact_theorem_from_final_fence(self) -> None:
        raw = (
            b"analysis\n\x60\x60\x60tactics\nring\n\x60\x60\x60\n"
            b"\x60\x60\x60lean4\ntheorem target (n : Nat) : n = n := by\n  rfl\n"
            b"\x60\x60\x60\n\n  \n"
        )
        self.assertEqual(
            (b"  rfl\n", "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY"),
            adapt_model_completion(
                raw,
                theorem_name="target",
                trusted_source=trusted_fixture(
                    b"theorem target (n : Nat) : n = n := by\n"
                ),
            ),
        )

    def test_adapter_extracts_final_tactic_fence(self) -> None:
        raw = b"reasoning\n\x60\x60\x60tactics\n  omega\n\x60\x60\x60\n"
        self.assertEqual(
            (b"  omega\n", "FINAL_LEAN_FENCE_TACTIC_BODY"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_keeps_nested_by_proof_in_the_tactic_body(self) -> None:
        raw = (
            b"\x60\x60\x60lean\ntheorem target : True := by\n"
            b"  have h : True := by trivial\n  exact h\n\x60\x60\x60\n"
        )
        self.assertEqual(
            (
                b"  have h : True := by trivial\n  exact h\n",
                "FINAL_LEAN_FENCE_EXACT_THEOREM_BODY",
            ),
            adapt_model_completion(
                raw, theorem_name="target", trusted_source=trusted_fixture()
            ),
        )

    def test_adapter_rejects_wrong_or_untrusted_headers(self) -> None:
        for raw in (
            b"```lean\ntheorem other : True := by trivial\n```\n",
            b"```lean\ntheorem target : False := by trivial\n```\n",
            b"```lean\ntheorem target : True /- := by\n  trivial\n```\n",
            b"```lean\nimport Untrusted\ntheorem target : True := by\n  trivial\n```\n",
        ):
            candidate, rule = adapt_model_completion(
                raw, theorem_name="target", trusted_source=trusted_fixture()
            )
            self.assertEqual(raw, candidate)
            self.assertEqual("REJECTED_DECLARATION_RAW_PASSTHROUGH", rule)

    def test_adapter_requires_trusted_header_for_full_declaration(self) -> None:
        raw = b"```lean\ntheorem target : True := by\n  trivial\n```\n"
        self.assertEqual(
            (raw, "REJECTED_DECLARATION_RAW_PASSTHROUGH"),
            adapt_model_completion(raw, theorem_name="target"),
        )

    def test_adapter_does_not_treat_header_comments_or_strings_as_boundaries(
        self,
    ) -> None:
        headers = (
            b"/--\nlemma commentText : True := by\n-/\ntheorem target : True := by\n",
            b'theorem target : ("foo := by" : String) = "foo := by" := by\n',
        )
        for header in headers:
            body = b"  rfl\n"
            candidate, _ = adapt_model_completion(
                b"```lean\n" + header + body + b"```\n",
                theorem_name="target",
                trusted_source=trusted_fixture(header),
            )
            self.assertEqual(body, candidate)

    def test_adapter_retains_declaration_words_inside_tactic_comments_and_strings(
        self,
    ) -> None:
        body = b'  /-\ntheorem inComment : True := by\n-/\n  let text := "x\nlemma inString : True := by\ny"\n  trivial\n'
        self.assertEqual(
            (body, "FINAL_LEAN_FENCE_TACTIC_BODY"),
            adapt_model_completion(
                b"```lean\n" + body + b"```\n",
                theorem_name="target",
                trusted_source=trusted_fixture(),
            ),
        )

    def test_adapter_never_drops_trailing_declarations_or_changes_target(self) -> None:
        body = b"  trivial\ntheorem injected : True := by trivial\n"
        source = trusted_fixture()
        candidate, _ = adapt_model_completion(
            b"```lean\n" + source.source + body + b"```\n",
            theorem_name="target",
            trusted_source=source,
        )
        self.assertEqual(body, candidate)
        with self.assertRaises(ValueError):
            classify_baseline_response(candidate, source)
        with self.assertRaisesRegex(ValueError, "trusted source target"):
            adapt_model_completion(
                b"trivial", theorem_name="different", trusted_source=source
            )

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

    def test_product_adapter_preserves_only_exact_protocol_responses(self) -> None:
        exact = FINAL_PREFIX + b"omega\n"
        self.assertEqual(
            (exact, "EXACT_PRODUCT_PROTOCOL_RESPONSE"),
            adapt_product_completion(exact),
        )
        fenced = b"analysis\n\x60\x60\x60lean\n" + exact + b"\x60\x60\x60\n"
        self.assertEqual(
            (exact, "FINAL_LEAN_FENCE_PRODUCT_PROTOCOL_RESPONSE"),
            adapt_product_completion(fenced),
        )
        unmarked = b"analysis\n\x60\x60\x60lean\nomega\n\x60\x60\x60\n"
        self.assertEqual(
            (unmarked, "REJECTED_UNMARKED_PRODUCT_FENCE_RAW_PASSTHROUGH"),
            adapt_product_completion(unmarked),
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
        self.assertFalse(
            plan["stages"]["pilot_1_percent"]["requires_clean_smoke_report"]
        )
        self.assertEqual(
            33834392060,
            plan["stages"]["pilot_1_percent"]["transition_authority"][
                "github_actions_run_id"
            ],
        )
        self.assertIn(
            "MALFORMED_OUTPUT_IS_A_TYPED_FAILED_ATTEMPT_NOT_A_RUN_BLOCKER",
            plan["pilot_1_percent_integrity_gate"],
        )
        self.assertIn(
            "AT_LEAST_ONE_SIGNED_VALID_MODEL_RESPONSE",
            plan["historical_smoke_admission_gate"],
        )
        self.assertTrue(plan["stages"]["smoke_0_1_percent"]["stop_after_report"])
        self.assertTrue(plan["stages"]["pilot_1_percent"]["stop_after_report"])
        self.assertEqual(60, plan["timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual("UNKNOWN", plan["timing_policy"]["timeout_verdict"])
        self.assertNotIn("PROSE_SIGNED_INVALID_UNDER_5000_MS", plan["pre_model_gates"])
        self.assertEqual(60, verifier_launcher(plan).timeout_seconds)
        override = "ghcr.io/kitahl/supernova-goal1-verifier@sha256:" + "f" * 64
        self.assertEqual(
            override, verifier_launcher(plan, image_ref=override).image_ref
        )
        self.assertEqual(300, plan["model_timing_policy"]["outer_watchdog_seconds"])
        self.assertEqual(
            "NO_ANSWER", plan["model_timing_policy"]["incomplete_finish_reason"]
        )
        # Historical plan remains immutable; prospective runtime covers the
        # frozen executor's sequential 120s readiness + 300s request limits.
        self.assertEqual(450, MODEL_TIMEOUT_SECONDS)

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

    def test_one_percent_schedule_is_exactly_twenty_and_balanced(self) -> None:
        problem_ids = tuple(f"p-{index}" for index in range(5))
        schedule = one_percent_schedule(
            problem_ids,
            attempts_per_problem_arm=2,
        )
        self.assertEqual(20, len(schedule))
        self.assertEqual(20, len(set(schedule)))
        for problem_id in problem_ids:
            for arm in (Arm.ORDINARY, Arm.VERIFIED_CHAIN):
                self.assertEqual(
                    [0, 1],
                    sorted(
                        attempt
                        for attempt, observed_id, observed_arm in schedule
                        if observed_id == problem_id and observed_arm is arm
                    ),
                )
        self.assertEqual((0, "p-0", Arm.ORDINARY), schedule[0])
        self.assertEqual((0, "p-1", Arm.VERIFIED_CHAIN), schedule[2])
        self.assertEqual((1, "p-0", Arm.VERIFIED_CHAIN), schedule[10])

    def test_one_percent_metrics_separate_emission_admission_and_exposure(self) -> None:
        def row(
            *,
            problem_id: str,
            arm: str,
            response_kind: str,
            admitted: bool = False,
            exposed: int = 0,
            solved: bool = False,
            verdict: str | None = "INVALID",
        ) -> dict[str, object]:
            return {
                "admitted_products_visible_before_attempt": exposed,
                "arm": arm,
                "attempt_status": "ANSWERED",
                "final_solved": solved,
                "problem_id": problem_id,
                "product_admitted": admitted,
                "response_kind": response_kind,
                "verifier_evidence": (
                    None if verdict is None else {"verdict": verdict}
                ),
            }

        attempts = [
            row(
                problem_id="p-0",
                arm="verified_chain",
                response_kind="PRODUCT_CANDIDATE",
                admitted=True,
                verdict="VALID",
            ),
            row(
                problem_id="p-0",
                arm="verified_chain",
                response_kind="FINAL_ANSWER",
                exposed=1,
                solved=True,
                verdict="VALID",
            ),
            row(
                problem_id="p-0",
                arm="ordinary",
                response_kind="FINAL_ANSWER",
            ),
            row(
                problem_id="p-0",
                arm="ordinary",
                response_kind="FINAL_ANSWER",
            ),
        ]
        metrics = _one_percent_metrics(attempts, problem_ids=("p-0",))
        product = metrics["product_chain"]
        self.assertEqual(1, product["product_emissions"])
        self.assertEqual(1, product["product_admissions"])
        self.assertEqual(1.0, product["admission_rate_given_emission"])
        self.assertEqual(1, product["final_attempts_after_usable_product_exposure"])
        self.assertTrue(
            metrics["paired_problem_outcomes"][0]["verified_chain_best_of_2_solved"]
        )

    def test_pilot_notice_is_bounded_and_excludes_candidate_text(self) -> None:
        report = {
            "attempts": [{"candidate_utf8": "secret proof bytes"}],
            "countable_attempts": 0,
            "execution_status": "COMPLETE",
            "integrity": {"status": "PASS"},
            "metrics": {"attempts": 20},
            "next_action": "STOP_AND_ANALYZE_BEFORE_ANY_LARGER_CALIBRATION",
            "report_sha256": "a" * 64,
            "selection": {"problem_ids": ["p-0"]},
            "verifier_image_ref": "image@sha256:" + "b" * 64,
        }
        notice = github_pilot_notice(report)
        self.assertTrue(notice.startswith("::notice title="))
        self.assertIn('"countable_attempts":0', notice)
        self.assertNotIn("secret proof bytes", notice)
        self.assertNotIn('"candidate_utf8"', notice)
        self.assertNotIn('"raw_completion_utf8"', notice)

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
