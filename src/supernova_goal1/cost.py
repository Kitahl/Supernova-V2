from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .contracts import Arm, CompleteCost


COST_DIMENSIONS: tuple[str, ...] = (
    "model_calls",
    "input_tokens",
    "output_tokens",
    "verifier_milliseconds",
    "orchestration_milliseconds",
)


class CostEventKind(StrEnum):
    MODEL_CALL = "model_call"
    VERIFIER = "verifier"
    ORCHESTRATION = "orchestration"


class CostRelation(StrEnum):
    EQUAL = "EQUAL"
    LEFT_DOMINATES = "LEFT_DOMINATES"
    RIGHT_DOMINATES = "RIGHT_DOMINATES"
    INCOMPARABLE = "INCOMPARABLE"


def _natural(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class CostEvent:
    event_id: str
    kind: CostEventKind
    input_tokens: int = 0
    output_tokens: int = 0
    milliseconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.kind, CostEventKind):
            try:
                normalized = CostEventKind(self.kind)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown cost event kind: {self.kind!r}") from exc
            object.__setattr__(self, "kind", normalized)

        _natural(self.input_tokens, "input_tokens")
        _natural(self.output_tokens, "output_tokens")
        _natural(self.milliseconds, "milliseconds")

        if self.kind is CostEventKind.MODEL_CALL:
            if self.milliseconds != 0:
                raise ValueError(
                    "model_call events carry token counts only; elapsed non-model work "
                    "must be recorded separately"
                )
        elif self.kind is CostEventKind.VERIFIER:
            if self.input_tokens or self.output_tokens:
                raise ValueError("verifier events cannot carry model token counts")
        elif self.kind is CostEventKind.ORCHESTRATION:
            if self.input_tokens or self.output_tokens:
                raise ValueError("orchestration events cannot carry model token counts")

    @classmethod
    def model_call(cls, event_id: str, *, input_tokens: int, output_tokens: int) -> "CostEvent":
        return cls(
            event_id=event_id,
            kind=CostEventKind.MODEL_CALL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @classmethod
    def verifier(cls, event_id: str, *, milliseconds: int) -> "CostEvent":
        return cls(
            event_id=event_id,
            kind=CostEventKind.VERIFIER,
            milliseconds=milliseconds,
        )

    @classmethod
    def orchestration(cls, event_id: str, *, milliseconds: int) -> "CostEvent":
        return cls(
            event_id=event_id,
            kind=CostEventKind.ORCHESTRATION,
            milliseconds=milliseconds,
        )

    def cost_increment(self) -> CompleteCost:
        if self.kind is CostEventKind.MODEL_CALL:
            return CompleteCost(
                model_calls=1,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                verifier_milliseconds=0,
                orchestration_milliseconds=0,
            )
        if self.kind is CostEventKind.VERIFIER:
            return CompleteCost(0, 0, 0, self.milliseconds, 0)
        return CompleteCost(0, 0, 0, 0, self.milliseconds)


@dataclass(frozen=True)
class ArmCostTrace:
    arm: Arm
    events: tuple[CostEvent, ...]
    accounting_complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.arm, Arm):
            try:
                normalized = Arm(self.arm)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown arm: {self.arm!r}") from exc
            object.__setattr__(self, "arm", normalized)
        if not isinstance(self.accounting_complete, bool):
            raise ValueError("accounting_complete must be boolean")
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate cost event_id in {self.arm.value}")

    @classmethod
    def from_events(
        cls, arm: Arm | str, events: Iterable[CostEvent], *, accounting_complete: bool
    ) -> "ArmCostTrace":
        return cls(
            arm=Arm(arm), events=tuple(events), accounting_complete=accounting_complete
        )

    @property
    def total(self) -> CompleteCost:
        model_calls = input_tokens = output_tokens = 0
        verifier_milliseconds = orchestration_milliseconds = 0
        for event in self.events:
            increment = event.cost_increment()
            model_calls += increment.model_calls
            input_tokens += increment.input_tokens
            output_tokens += increment.output_tokens
            verifier_milliseconds += increment.verifier_milliseconds
            orchestration_milliseconds += increment.orchestration_milliseconds
        return CompleteCost(
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            verifier_milliseconds=verifier_milliseconds,
            orchestration_milliseconds=orchestration_milliseconds,
        )


@dataclass(frozen=True)
class CompleteCostReport:
    traces: tuple[ArmCostTrace, ...]

    def __post_init__(self) -> None:
        incomplete = [trace.arm.value for trace in self.traces if not trace.accounting_complete]
        if incomplete:
            raise ValueError(
                "complete-cost report cannot close with incomplete arm accounting: "
                f"{incomplete}"
            )
        arms = [trace.arm for trace in self.traces]
        if len(arms) != len(set(arms)):
            raise ValueError("each arm must appear exactly once in complete-cost accounting")
        missing = [arm.value for arm in Arm if arm not in arms]
        extra = [arm.value for arm in arms if arm not in tuple(Arm)]
        if missing or extra or len(arms) != len(tuple(Arm)):
            raise ValueError(
                "complete-cost accounting requires exactly all five arms; "
                f"missing={missing}, extra={extra}"
            )

    @classmethod
    def from_traces(cls, traces: Iterable[ArmCostTrace]) -> "CompleteCostReport":
        return cls(tuple(traces))

    def total_for(self, arm: Arm | str) -> CompleteCost:
        normalized = Arm(arm)
        for trace in self.traces:
            if trace.arm is normalized:
                return trace.total
        raise AssertionError("complete-cost report invariant violated")

    def totals(self) -> dict[Arm, CompleteCost]:
        return {trace.arm: trace.total for trace in self.traces}

    def budget_violations(self, ceiling: CompleteCost) -> dict[Arm, tuple[str, ...]]:
        violations: dict[Arm, tuple[str, ...]] = {}
        for arm, actual in self.totals().items():
            exceeded = tuple(
                dimension
                for dimension, used, allowed in zip(
                    COST_DIMENSIONS,
                    actual.as_tuple(),
                    ceiling.as_tuple(),
                    strict=True,
                )
                if used > allowed
            )
            violations[arm] = exceeded
        return violations

    def within_budget(self, ceiling: CompleteCost) -> dict[Arm, bool]:
        return {
            arm: not exceeded
            for arm, exceeded in self.budget_violations(ceiling).items()
        }


def compare_complete_cost(left: CompleteCost, right: CompleteCost) -> CostRelation:
    """Compare costs componentwise; intentionally does not invent scalar weights."""

    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    if left_tuple == right_tuple:
        return CostRelation.EQUAL
    left_no_more = all(a <= b for a, b in zip(left_tuple, right_tuple, strict=True))
    right_no_more = all(b <= a for a, b in zip(left_tuple, right_tuple, strict=True))
    if left_no_more:
        return CostRelation.LEFT_DOMINATES
    if right_no_more:
        return CostRelation.RIGHT_DOMINATES
    return CostRelation.INCOMPARABLE
