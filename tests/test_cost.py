from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.contracts import Arm, CompleteCost
from supernova_goal1.cost import (
    ArmCostTrace,
    CompleteCostReport,
    CostEvent,
    CostEventKind,
    CostRelation,
    ExpectedCostEvent,
    compare_complete_cost,
)


class CompleteCostAccountingTests(unittest.TestCase):
    def _zero_trace(self, arm: Arm) -> ArmCostTrace:
        event_id = f"{arm.value}-attempt"
        return ArmCostTrace.from_events(
            arm,
            (CostEvent.model_call(event_id, input_tokens=0, output_tokens=0),),
            expected_events=(ExpectedCostEvent.model_call(event_id),),
            accounting_complete=True,
        )

    def _zero_report(self) -> CompleteCostReport:
        return CompleteCostReport.from_traces(self._zero_trace(arm) for arm in Arm)

    def test_report_requires_all_five_arms_exactly_once(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly all five arms"):
            CompleteCostReport.from_traces(
                self._zero_trace(arm) for arm in tuple(Arm)[:-1]
            )

        duplicated = [self._zero_trace(arm) for arm in Arm]
        duplicated.append(self._zero_trace(Arm.ORDINARY))
        with self.assertRaisesRegex(ValueError, "each arm must appear exactly once"):
            CompleteCostReport.from_traces(duplicated)

    def test_trace_aggregates_every_attempt_and_resource_class(self) -> None:
        expected = (
            ExpectedCostEvent.model_call("call-1"),
            ExpectedCostEvent.model_call("retry-1"),
            ExpectedCostEvent.verifier("verify-1"),
            ExpectedCostEvent.orchestration("route-1"),
        )
        trace = ArmCostTrace.from_events(
            Arm.VERIFIED_CHAIN,
            (
                CostEvent.model_call("call-1", input_tokens=100, output_tokens=20),
                CostEvent.model_call("retry-1", input_tokens=80, output_tokens=10),
                CostEvent.verifier("verify-1", milliseconds=125),
                CostEvent.orchestration("route-1", milliseconds=40),
            ),
            expected_events=expected,
            accounting_complete=True,
        )
        self.assertTrue(trace.coverage_complete)
        self.assertTrue(trace.measurements_complete)
        self.assertEqual(
            CompleteCost(
                model_calls=2,
                input_tokens=180,
                output_tokens=30,
                verifier_milliseconds=125,
                orchestration_milliseconds=40,
            ),
            trace.total,
        )

    def test_duplicate_event_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate cost event_id"):
            ArmCostTrace.from_events(
                Arm.ORDINARY,
                (
                    CostEvent.model_call("same", input_tokens=1, output_tokens=1),
                    CostEvent.verifier("same", milliseconds=1),
                ),
                expected_events=(ExpectedCostEvent.model_call("same"),),
                accounting_complete=True,
            )

    def test_event_shapes_prevent_cross_category_double_counting(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot carry model token counts"):
            CostEvent(
                event_id="bad-verifier",
                kind=CostEventKind.VERIFIER,
                input_tokens=1,
                milliseconds=10,
            )
        with self.assertRaisesRegex(ValueError, "recorded separately"):
            CostEvent(
                event_id="bad-model",
                kind=CostEventKind.MODEL_CALL,
                input_tokens=1,
                output_tokens=1,
                milliseconds=10,
            )

    def test_budget_is_checked_componentwise_for_each_arm(self) -> None:
        traces = [self._zero_trace(arm) for arm in Arm]
        traces[-1] = ArmCostTrace.from_events(
            Arm.VERIFIED_CHAIN,
            (
                CostEvent.model_call("call", input_tokens=101, output_tokens=2),
                CostEvent.verifier("verify", milliseconds=11),
            ),
            expected_events=(
                ExpectedCostEvent.model_call("call"),
                ExpectedCostEvent.verifier("verify"),
            ),
            accounting_complete=True,
        )
        report = CompleteCostReport.from_traces(traces)
        ceiling = CompleteCost(1, 100, 10, 10, 10)

        self.assertFalse(report.within_budget(ceiling)[Arm.VERIFIED_CHAIN])
        self.assertEqual(
            ("input_tokens", "verifier_milliseconds"),
            report.budget_violations(ceiling)[Arm.VERIFIED_CHAIN],
        )
        self.assertTrue(report.within_budget(ceiling)[Arm.ORDINARY])

    def test_cost_comparison_preserves_incomparability_without_weights(self) -> None:
        cheap_tokens_expensive_verifier = CompleteCost(1, 10, 10, 500, 5)
        expensive_tokens_cheap_verifier = CompleteCost(1, 100, 10, 50, 5)
        self.assertEqual(
            CostRelation.INCOMPARABLE,
            compare_complete_cost(
                cheap_tokens_expensive_verifier,
                expensive_tokens_cheap_verifier,
            ),
        )

    def test_incomplete_arm_accounting_cannot_be_closed(self) -> None:
        traces = [self._zero_trace(arm) for arm in Arm]
        traces[0] = ArmCostTrace.from_events(
            Arm.ORDINARY,
            (),
            expected_events=(ExpectedCostEvent.model_call("ordinary-attempt"),),
            accounting_complete=True,
        )
        with self.assertRaisesRegex(ValueError, "incomplete arm accounting"):
            CompleteCostReport.from_traces(traces)

    def test_empty_all_arms_cannot_be_reported_as_complete_zero_cost(self) -> None:
        traces = [
            ArmCostTrace.from_events(
                arm,
                (),
                expected_events=(ExpectedCostEvent.model_call(f"{arm.value}-attempt"),),
                accounting_complete=True,
            )
            for arm in Arm
        ]
        with self.assertRaisesRegex(ValueError, "incomplete arm accounting"):
            CompleteCostReport.from_traces(traces)

    def test_empty_expected_event_manifest_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "coverage manifest"):
            ArmCostTrace.from_events(
                Arm.ORDINARY,
                (),
                expected_events=(),
                accounting_complete=True,
            )

    def test_orchestration_only_manifest_cannot_stand_in_for_solver_attempt(self) -> None:
        for arm in Arm:
            with self.subTest(arm=arm):
                event_id = f"{arm.value}-accounting"
                with self.assertRaisesRegex(ValueError, "expected model_call attempt"):
                    ArmCostTrace.from_events(
                        arm,
                        (CostEvent.orchestration(event_id, milliseconds=0),),
                        expected_events=(ExpectedCostEvent.orchestration(event_id),),
                        accounting_complete=True,
                    )

    def test_event_kind_mismatch_does_not_satisfy_coverage(self) -> None:
        trace = ArmCostTrace.from_events(
            Arm.ORDINARY,
            (CostEvent.verifier("attempt-1", milliseconds=1),),
            expected_events=(ExpectedCostEvent.model_call("attempt-1"),),
            accounting_complete=True,
        )
        self.assertFalse(trace.coverage_complete)
        self.assertEqual(("attempt-1",), trace.missing_expected_events)
        self.assertEqual(("attempt-1",), trace.unexpected_events)

    def test_unknown_model_usage_cannot_be_coerced_to_zero_complete_cost(self) -> None:
        traces = [self._zero_trace(arm) for arm in Arm]
        traces[0] = ArmCostTrace.from_events(
            Arm.ORDINARY,
            (CostEvent.model_call("call-1", input_tokens=None, output_tokens=0),),
            expected_events=(ExpectedCostEvent.model_call("call-1"),),
            accounting_complete=True,
        )
        self.assertTrue(traces[0].coverage_complete)
        self.assertFalse(traces[0].measurements_complete)
        self.assertEqual(
            (("call-1", ("input_tokens",)),),
            traces[0].unknown_measurements,
        )
        with self.assertRaisesRegex(ValueError, "incomplete arm accounting"):
            CompleteCostReport.from_traces(traces)

    def test_unknown_elapsed_time_cannot_be_coerced_to_zero_complete_cost(self) -> None:
        trace = ArmCostTrace.from_events(
            Arm.ORDINARY,
            (
                CostEvent.model_call("call-1", input_tokens=0, output_tokens=0),
                CostEvent.orchestration("route-1", milliseconds=None),
            ),
            expected_events=(
                ExpectedCostEvent.model_call("call-1"),
                ExpectedCostEvent.orchestration("route-1"),
            ),
            accounting_complete=True,
        )
        self.assertTrue(trace.coverage_complete)
        self.assertFalse(trace.measurements_complete)
        self.assertEqual(
            (("route-1", ("orchestration_milliseconds",)),),
            trace.unknown_measurements,
        )
        with self.assertRaisesRegex(ValueError, "incomplete cost telemetry"):
            _ = trace.total

    def test_omitted_measurements_default_to_unknown_not_zero(self) -> None:
        model_event = CostEvent(event_id="call-1", kind=CostEventKind.MODEL_CALL)
        verifier_event = CostEvent(event_id="verify-1", kind=CostEventKind.VERIFIER)
        orchestration_event = CostEvent(
            event_id="route-1", kind=CostEventKind.ORCHESTRATION
        )

        self.assertEqual(
            ("input_tokens", "output_tokens"), model_event.unknown_measurements
        )
        self.assertEqual(
            ("verifier_milliseconds",), verifier_event.unknown_measurements
        )
        self.assertEqual(
            ("orchestration_milliseconds",), orchestration_event.unknown_measurements
        )

        traces = [self._zero_trace(arm) for arm in Arm]
        traces[0] = ArmCostTrace.from_events(
            Arm.ORDINARY,
            (model_event,),
            expected_events=(ExpectedCostEvent.model_call("call-1"),),
            accounting_complete=True,
        )
        with self.assertRaisesRegex(ValueError, "incomplete arm accounting"):
            CompleteCostReport.from_traces(traces)

    def test_closed_report_snapshots_mutable_constructor_inputs(self) -> None:
        ordinary_event = CostEvent.model_call(
            "call-1", input_tokens=100, output_tokens=20
        )
        ordinary_expected_event = ExpectedCostEvent.model_call("call-1")
        ordinary_events = [ordinary_event]
        ordinary_expected = [ordinary_expected_event]
        ordinary_trace = ArmCostTrace(
            Arm.ORDINARY,
            ordinary_events,
            ordinary_expected,
            True,
        )
        report_inputs = [ordinary_trace]
        report_inputs.extend(self._zero_trace(arm) for arm in tuple(Arm)[1:])
        report = CompleteCostReport(report_inputs)

        ordinary_events.clear()
        ordinary_expected.clear()
        report_inputs.clear()
        object.__setattr__(ordinary_event, "input_tokens", 0)
        object.__setattr__(ordinary_expected_event, "event_id", "mutated")
        object.__setattr__(ordinary_trace, "events", ())
        object.__setattr__(ordinary_trace, "expected_events", ())

        self.assertIsInstance(report.traces, tuple)
        self.assertEqual(5, len(report.traces))
        self.assertTrue(report.traces[0].coverage_complete)
        self.assertEqual(
            CompleteCost(1, 100, 20, 0, 0),
            report.total_for(Arm.ORDINARY),
        )

    def test_polymorphic_records_cannot_override_accounting_semantics(self) -> None:
        class ForgedCostEvent(CostEvent):
            def cost_increment(self) -> CompleteCost:
                return CompleteCost(0, 0, 0, 0, 0)

        forged_event = ForgedCostEvent(
            event_id="call-1",
            kind=CostEventKind.MODEL_CALL,
            input_tokens=100,
            output_tokens=20,
        )
        with self.assertRaisesRegex(ValueError, "exact CostEvent"):
            ArmCostTrace.from_events(
                Arm.ORDINARY,
                (forged_event,),
                expected_events=(ExpectedCostEvent.model_call("call-1"),),
                accounting_complete=True,
            )

        class ForgedArmCostTrace(ArmCostTrace):
            @property
            def total(self) -> CompleteCost:
                return CompleteCost(0, 0, 0, 0, 0)

        base_trace = self._zero_trace(Arm.ORDINARY)
        forged_trace = ForgedArmCostTrace(
            base_trace.arm,
            base_trace.events,
            base_trace.expected_events,
            base_trace.accounting_complete,
        )
        traces = [forged_trace]
        traces.extend(self._zero_trace(arm) for arm in tuple(Arm)[1:])
        with self.assertRaisesRegex(ValueError, "exact ArmCostTrace"):
            CompleteCostReport.from_traces(traces)

    def test_closed_arm_cost_includes_at_least_one_observed_model_attempt(self) -> None:
        report = self._zero_report()
        self.assertEqual(
            CompleteCost(1, 0, 0, 0, 0), report.total_for(Arm.PORTFOLIO)
        )


if __name__ == "__main__":
    unittest.main()
