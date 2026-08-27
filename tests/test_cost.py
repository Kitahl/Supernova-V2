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
    compare_complete_cost,
)


class CompleteCostAccountingTests(unittest.TestCase):
    def _empty_report(self) -> CompleteCostReport:
        return CompleteCostReport.from_traces(
            ArmCostTrace.from_events(arm, (), accounting_complete=True) for arm in Arm
        )

    def test_report_requires_all_five_arms_exactly_once(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly all five arms"):
            CompleteCostReport.from_traces(
                ArmCostTrace.from_events(
                    arm, (), accounting_complete=True
                )
                for arm in tuple(Arm)[:-1]
            )

        duplicated = [
            ArmCostTrace.from_events(arm, (), accounting_complete=True) for arm in Arm
        ]
        duplicated.append(
            ArmCostTrace.from_events(Arm.ORDINARY, (), accounting_complete=True)
        )
        with self.assertRaisesRegex(ValueError, "each arm must appear exactly once"):
            CompleteCostReport.from_traces(duplicated)

    def test_trace_aggregates_every_attempt_and_resource_class(self) -> None:
        trace = ArmCostTrace.from_events(
            Arm.VERIFIED_CHAIN,
            (
                CostEvent.model_call("call-1", input_tokens=100, output_tokens=20),
                CostEvent.model_call("retry-1", input_tokens=80, output_tokens=10),
                CostEvent.verifier("verify-1", milliseconds=125),
                CostEvent.orchestration("route-1", milliseconds=40),
            ),
            accounting_complete=True,
        )
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
        traces = [
            ArmCostTrace.from_events(arm, (), accounting_complete=True) for arm in Arm
        ]
        traces[-1] = ArmCostTrace.from_events(
            Arm.VERIFIED_CHAIN,
            (
                CostEvent.model_call("call", input_tokens=101, output_tokens=2),
                CostEvent.verifier("verify", milliseconds=11),
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
        traces = [
            ArmCostTrace.from_events(arm, (), accounting_complete=True) for arm in Arm
        ]
        traces[0] = ArmCostTrace.from_events(
            Arm.ORDINARY, (), accounting_complete=False
        )
        with self.assertRaisesRegex(ValueError, "incomplete arm accounting"):
            CompleteCostReport.from_traces(traces)

    def test_zero_event_trace_is_explicit_zero_not_missing(self) -> None:
        report = self._empty_report()
        self.assertEqual(
            CompleteCost(0, 0, 0, 0, 0), report.total_for(Arm.PORTFOLIO)
        )


if __name__ == "__main__":
    unittest.main()
