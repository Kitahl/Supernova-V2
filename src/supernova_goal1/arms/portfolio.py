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


class PortfolioAttemptStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class PortfolioRequest:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    problem_statement: str
    attempt_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id", "problem_statement"):
            _text(getattr(self, field), field)
        if not isinstance(self.attempt_ids, tuple):
            raise ValueError("attempt_ids must be a tuple")
        if len(self.attempt_ids) < 2:
            raise ValueError("portfolio requires at least two independent attempts")
        for index, attempt_id in enumerate(self.attempt_ids):
            _text(attempt_id, f"attempt_ids[{index}]")
        if len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise ValueError("attempt_ids must be unique")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PortfolioRequest":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
            "attempt_ids",
        }
        _exact_fields(raw, expected, "portfolio request")
        attempt_ids = raw["attempt_ids"]
        if not isinstance(attempt_ids, list):
            raise ValueError("attempt_ids must be a JSON list")
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            problem_statement=raw["problem_statement"],
            attempt_ids=tuple(attempt_ids),
        )


@dataclass(frozen=True)
class PortfolioCandidate:
    attempt_id: str
    status: PortfolioAttemptStatus
    answer: str | None
    error: str | None

    def __post_init__(self) -> None:
        _text(self.attempt_id, "attempt_id")
        try:
            status = PortfolioAttemptStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown portfolio attempt status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if status is PortfolioAttemptStatus.ANSWERED:
            if not isinstance(self.answer, str) or not self.answer.strip():
                raise ValueError("ANSWERED portfolio candidate requires a non-empty answer")
            if self.error is not None:
                raise ValueError("ANSWERED portfolio candidate cannot carry error")
        elif status is PortfolioAttemptStatus.NO_ANSWER:
            if self.answer is not None or self.error is not None:
                raise ValueError("NO_ANSWER portfolio candidate carries neither answer nor error")
        else:
            if self.answer is not None:
                raise ValueError("ERROR portfolio candidate cannot carry answer")
            _text(self.error, "error")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PortfolioCandidate":
        expected = {"attempt_id", "status", "answer", "error"}
        _exact_fields(raw, expected, "portfolio candidate")
        return cls(
            attempt_id=raw["attempt_id"],
            status=raw["status"],
            answer=raw["answer"],
            error=raw["error"],
        )


@dataclass(frozen=True)
class PortfolioResult:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    candidates: tuple[PortfolioCandidate, ...]
    selected_attempt_id: str | None

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id"):
            _text(getattr(self, field), field)
        if not isinstance(self.candidates, tuple):
            raise ValueError("candidates must be a tuple")
        if len(self.candidates) < 2:
            raise ValueError("portfolio result requires at least two attempt results")
        if not all(isinstance(candidate, PortfolioCandidate) for candidate in self.candidates):
            raise ValueError("candidates must contain PortfolioCandidate values")
        attempt_ids = [candidate.attempt_id for candidate in self.candidates]
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("portfolio candidate attempt_ids must be unique")
        if self.selected_attempt_id is not None:
            _text(self.selected_attempt_id, "selected_attempt_id")
        answered = {
            candidate.attempt_id
            for candidate in self.candidates
            if candidate.status is PortfolioAttemptStatus.ANSWERED
        }
        if answered:
            if self.selected_attempt_id not in answered:
                raise ValueError("selected_attempt_id must name an ANSWERED candidate")
        elif self.selected_attempt_id is not None:
            raise ValueError("selected_attempt_id must be null when no candidate answered")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PortfolioResult":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "candidates",
            "selected_attempt_id",
        }
        _exact_fields(raw, expected, "portfolio result")
        candidates = raw["candidates"]
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a JSON list")
        parsed_candidates = []
        for item in candidates:
            if not isinstance(item, Mapping):
                raise ValueError("portfolio candidate must be an object")
            parsed_candidates.append(PortfolioCandidate.from_mapping(item))
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            candidates=tuple(parsed_candidates),
            selected_attempt_id=raw["selected_attempt_id"],
        )

    @property
    def selected_answer(self) -> str | None:
        if self.selected_attempt_id is None:
            return None
        for candidate in self.candidates:
            if candidate.attempt_id == self.selected_attempt_id:
                return candidate.answer
        raise AssertionError("selected_attempt_id invariant violated")

    def validate_for(self, request: PortfolioRequest) -> None:
        bindings = (
            ("request_id", self.request_id, request.request_id),
            ("experiment_id", self.experiment_id, request.experiment_id),
            ("problem_id", self.problem_id, request.problem_id),
            ("budget_id", self.budget_id, request.budget_id),
        )
        for field, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"portfolio result {field} does not match request")
        candidate_ids = {candidate.attempt_id for candidate in self.candidates}
        if candidate_ids != set(request.attempt_ids):
            raise ValueError("portfolio result must contain exactly the requested attempts")
