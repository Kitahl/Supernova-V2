from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class Arm(StrEnum):
    ORDINARY = "ordinary"
    PORTFOLIO = "portfolio"
    PRODUCT_ONLY = "product_only"
    MULTI_FIDELITY = "multi_fidelity"
    VERIFIED_CHAIN = "verified_chain"


CONTROL_ARMS: tuple[Arm, ...] = (
    Arm.ORDINARY,
    Arm.PORTFOLIO,
    Arm.PRODUCT_ONLY,
    Arm.MULTI_FIDELITY,
)


MODEL_USAGE_BASES: frozenset[str] = frozenset(
    {"provider_tokens", "visible_utf8_bytes"}
)
UNFROZEN_MODEL_USAGE_BASIS = "UNFROZEN"


class GoalDecision(StrEnum):
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"
    PASS = "PASS"
    FAIL = "FAIL"


def _natural(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class CompleteCost:
    model_calls: int
    input_tokens: int
    output_tokens: int
    verifier_milliseconds: int
    orchestration_milliseconds: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], prefix: str) -> "CompleteCost":
        expected = {
            "model_calls",
            "input_tokens",
            "output_tokens",
            "verifier_milliseconds",
            "orchestration_milliseconds",
        }
        if set(raw) != expected:
            raise ValueError(f"{prefix} fields must be exactly {sorted(expected)}")
        return cls(**{key: _natural(raw[key], f"{prefix}.{key}") for key in expected})

    def within(self, ceiling: "CompleteCost") -> bool:
        return all(
            actual <= allowed
            for actual, allowed in zip(
                self.as_tuple(), ceiling.as_tuple(), strict=True
            )
        )

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (
            self.model_calls,
            self.input_tokens,
            self.output_tokens,
            self.verifier_milliseconds,
            self.orchestration_milliseconds,
        )


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    phase: str
    required_problem_ids: tuple[str, ...]
    cost_model_frozen: bool
    model_usage_basis: str
    budget_id: str
    budget_ceiling: CompleteCost
    familywise_alpha: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExperimentSpec":
        experiment_id = raw.get("experiment_id")
        phase = raw.get("phase")
        budget_id = raw.get("budget_id")
        model_usage_basis = raw.get("model_usage_basis")
        problem_ids = raw.get("required_problem_ids")
        alpha = raw.get("familywise_alpha")
        if not all(isinstance(value, str) and value for value in (experiment_id, phase, budget_id)):
            raise ValueError("experiment_id, phase, and budget_id must be non-empty strings")
        if not isinstance(problem_ids, list) or not problem_ids or not all(
            isinstance(value, str) and value for value in problem_ids
        ):
            raise ValueError("required_problem_ids must be a non-empty list of strings")
        if len(set(problem_ids)) != len(problem_ids):
            raise ValueError("required_problem_ids must be unique")
        if not isinstance(raw.get("cost_model_frozen"), bool):
            raise ValueError("cost_model_frozen must be boolean")
        allowed_usage_bases = MODEL_USAGE_BASES | {UNFROZEN_MODEL_USAGE_BASIS}
        if (
            not isinstance(model_usage_basis, str)
            or model_usage_basis not in allowed_usage_bases
        ):
            raise ValueError(
                "model_usage_basis must be provider_tokens, visible_utf8_bytes, "
                "or UNFROZEN"
            )
        if (
            raw["cost_model_frozen"]
            and model_usage_basis == UNFROZEN_MODEL_USAGE_BASIS
        ):
            raise ValueError(
                "a frozen cost model requires a concrete model_usage_basis"
            )
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            raise ValueError("familywise_alpha must be between 0 and 1")
        ceiling = raw.get("budget_ceiling")
        if not isinstance(ceiling, Mapping):
            raise ValueError("budget_ceiling must be an object")
        return cls(
            experiment_id=experiment_id,
            phase=phase,
            required_problem_ids=tuple(problem_ids),
            cost_model_frozen=raw["cost_model_frozen"],
            model_usage_basis=model_usage_basis,
            budget_id=budget_id,
            budget_ceiling=CompleteCost.from_mapping(ceiling, "budget_ceiling"),
            familywise_alpha=float(alpha),
        )


@dataclass(frozen=True)
class OutcomeRecord:
    experiment_id: str
    problem_id: str
    arm: Arm
    budget_id: str
    model_usage_basis: str
    solved: bool
    verifier_passed: bool
    cost: CompleteCost

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OutcomeRecord":
        string_fields = ("experiment_id", "problem_id", "budget_id")
        if not all(isinstance(raw.get(key), str) and raw[key] for key in string_fields):
            raise ValueError(f"{', '.join(string_fields)} must be non-empty strings")
        if not isinstance(raw.get("solved"), bool) or not isinstance(raw.get("verifier_passed"), bool):
            raise ValueError("solved and verifier_passed must be boolean")
        model_usage_basis = raw.get("model_usage_basis")
        if (
            not isinstance(model_usage_basis, str)
            or model_usage_basis not in MODEL_USAGE_BASES
        ):
            raise ValueError(
                "outcome model_usage_basis must be provider_tokens or "
                "visible_utf8_bytes"
            )
        try:
            arm = Arm(raw.get("arm"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown arm: {raw.get('arm')!r}") from exc
        cost = raw.get("cost")
        if not isinstance(cost, Mapping):
            raise ValueError("cost must be an object")
        record = cls(
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            arm=arm,
            budget_id=raw["budget_id"],
            model_usage_basis=model_usage_basis,
            solved=raw["solved"],
            verifier_passed=raw["verifier_passed"],
            cost=CompleteCost.from_mapping(cost, "cost"),
        )
        if record.solved and not record.verifier_passed:
            raise ValueError("a solved outcome requires verifier_passed=true")
        return record
