from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


def _exact_fields(raw: Mapping[str, Any], expected: set[str], prefix: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{prefix} fields must be exactly {sorted(expected)}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


class OrdinaryResultStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class OrdinaryRequest:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    problem_statement: str

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id", "problem_statement"):
            _text(getattr(self, field), field)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OrdinaryRequest":
        expected = {"request_id", "experiment_id", "problem_id", "budget_id", "problem_statement"}
        _exact_fields(raw, expected, "ordinary request")
        return cls(**{field: _text(raw[field], field) for field in expected})


@dataclass(frozen=True)
class OrdinaryResult:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    status: OrdinaryResultStatus
    answer: str | None
    error: str | None

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id"):
            _text(getattr(self, field), field)
        try:
            status = OrdinaryResultStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown ordinary result status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)

        if status is OrdinaryResultStatus.ANSWERED:
            if not isinstance(self.answer, str) or not self.answer.strip():
                raise ValueError("ANSWERED ordinary result requires a non-empty answer")
            if self.error is not None:
                raise ValueError("ANSWERED ordinary result cannot carry error")
        elif status is OrdinaryResultStatus.NO_ANSWER:
            if self.answer is not None or self.error is not None:
                raise ValueError("NO_ANSWER ordinary result carries neither answer nor error")
        else:
            if self.answer is not None:
                raise ValueError("ERROR ordinary result cannot carry answer")
            _text(self.error, "error")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OrdinaryResult":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "status",
            "answer",
            "error",
        }
        _exact_fields(raw, expected, "ordinary result")
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            status=raw["status"],
            answer=raw["answer"],
            error=raw["error"],
        )

    def validate_for(self, request: OrdinaryRequest) -> None:
        bindings = (
            ("request_id", self.request_id, request.request_id),
            ("experiment_id", self.experiment_id, request.experiment_id),
            ("problem_id", self.problem_id, request.problem_id),
            ("budget_id", self.budget_id, request.budget_id),
        )
        for field, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"ordinary result {field} does not match request")
