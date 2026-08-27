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


def _natural(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


class MultiFidelityAttemptStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FidelityStage:
    stage_id: str
    fidelity_id: str
    fidelity_rank: int

    def __post_init__(self) -> None:
        _text(self.stage_id, "stage_id")
        _text(self.fidelity_id, "fidelity_id")
        _natural(self.fidelity_rank, "fidelity_rank")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FidelityStage":
        expected = {"stage_id", "fidelity_id", "fidelity_rank"}
        _exact_fields(raw, expected, "fidelity stage")
        return cls(
            stage_id=raw["stage_id"],
            fidelity_id=raw["fidelity_id"],
            fidelity_rank=raw["fidelity_rank"],
        )


@dataclass(frozen=True)
class MultiFidelityRequest:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    problem_statement: str
    stages: tuple[FidelityStage, ...]

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
        ):
            _text(getattr(self, field), field)
        if not isinstance(self.stages, tuple):
            raise ValueError("stages must be a tuple")
        if len(self.stages) < 2:
            raise ValueError("multi-fidelity requires at least two fidelity stages")
        if not all(isinstance(stage, FidelityStage) for stage in self.stages):
            raise ValueError("stages must contain FidelityStage values")

        stage_ids = [stage.stage_id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("multi-fidelity stage_ids must be unique")
        fidelity_ids = [stage.fidelity_id for stage in self.stages]
        if len(fidelity_ids) != len(set(fidelity_ids)):
            raise ValueError("multi-fidelity fidelity_ids must be unique")
        ranks = [stage.fidelity_rank for stage in self.stages]
        if any(left >= right for left, right in zip(ranks, ranks[1:])):
            raise ValueError("multi-fidelity fidelity_rank values must strictly increase")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MultiFidelityRequest":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "problem_statement",
            "stages",
        }
        _exact_fields(raw, expected, "multi-fidelity request")
        stages = raw["stages"]
        if not isinstance(stages, list):
            raise ValueError("stages must be a JSON list")
        parsed_stages = []
        for item in stages:
            if not isinstance(item, Mapping):
                raise ValueError("fidelity stage must be an object")
            parsed_stages.append(FidelityStage.from_mapping(item))
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            problem_statement=raw["problem_statement"],
            stages=tuple(parsed_stages),
        )


@dataclass(frozen=True)
class MultiFidelityCandidate:
    stage_id: str
    status: MultiFidelityAttemptStatus
    answer: str | None
    error: str | None

    def __post_init__(self) -> None:
        _text(self.stage_id, "stage_id")
        try:
            status = MultiFidelityAttemptStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown multi-fidelity attempt status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)

        if status is MultiFidelityAttemptStatus.ANSWERED:
            _text(self.answer, "answer")
            if self.error is not None:
                raise ValueError("ANSWERED multi-fidelity candidate cannot carry error")
        elif status is MultiFidelityAttemptStatus.NO_ANSWER:
            if self.answer is not None or self.error is not None:
                raise ValueError(
                    "NO_ANSWER multi-fidelity candidate carries neither answer nor error"
                )
        else:
            if self.answer is not None:
                raise ValueError("ERROR multi-fidelity candidate cannot carry answer")
            _text(self.error, "error")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MultiFidelityCandidate":
        expected = {"stage_id", "status", "answer", "error"}
        _exact_fields(raw, expected, "multi-fidelity candidate")
        return cls(
            stage_id=raw["stage_id"],
            status=raw["status"],
            answer=raw["answer"],
            error=raw["error"],
        )


@dataclass(frozen=True)
class MultiFidelityResult:
    request_id: str
    experiment_id: str
    problem_id: str
    budget_id: str
    candidates: tuple[MultiFidelityCandidate, ...]
    selected_stage_id: str | None

    def __post_init__(self) -> None:
        for field in ("request_id", "experiment_id", "problem_id", "budget_id"):
            _text(getattr(self, field), field)
        if not isinstance(self.candidates, tuple):
            raise ValueError("candidates must be a tuple")
        if not self.candidates:
            raise ValueError("multi-fidelity result requires at least one attempted stage")
        if not all(
            isinstance(candidate, MultiFidelityCandidate) for candidate in self.candidates
        ):
            raise ValueError("candidates must contain MultiFidelityCandidate values")

        stage_ids = [candidate.stage_id for candidate in self.candidates]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("multi-fidelity candidate stage_ids must be unique")
        if self.selected_stage_id is not None:
            _text(self.selected_stage_id, "selected_stage_id")

        answered = {
            candidate.stage_id
            for candidate in self.candidates
            if candidate.status is MultiFidelityAttemptStatus.ANSWERED
        }
        if answered:
            if self.selected_stage_id not in answered:
                raise ValueError("selected_stage_id must name an ANSWERED candidate")
        elif self.selected_stage_id is not None:
            raise ValueError("selected_stage_id must be null when no candidate answered")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MultiFidelityResult":
        expected = {
            "request_id",
            "experiment_id",
            "problem_id",
            "budget_id",
            "candidates",
            "selected_stage_id",
        }
        _exact_fields(raw, expected, "multi-fidelity result")
        candidates = raw["candidates"]
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a JSON list")
        parsed_candidates = []
        for item in candidates:
            if not isinstance(item, Mapping):
                raise ValueError("multi-fidelity candidate must be an object")
            parsed_candidates.append(MultiFidelityCandidate.from_mapping(item))
        return cls(
            request_id=raw["request_id"],
            experiment_id=raw["experiment_id"],
            problem_id=raw["problem_id"],
            budget_id=raw["budget_id"],
            candidates=tuple(parsed_candidates),
            selected_stage_id=raw["selected_stage_id"],
        )

    @property
    def selected_answer(self) -> str | None:
        if self.selected_stage_id is None:
            return None
        for candidate in self.candidates:
            if candidate.stage_id == self.selected_stage_id:
                return candidate.answer
        raise AssertionError("selected_stage_id invariant violated")

    def validate_for(self, request: MultiFidelityRequest) -> None:
        bindings = (
            ("request_id", self.request_id, request.request_id),
            ("experiment_id", self.experiment_id, request.experiment_id),
            ("problem_id", self.problem_id, request.problem_id),
            ("budget_id", self.budget_id, request.budget_id),
        )
        for field, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"multi-fidelity result {field} does not match request")

        result_stage_ids = tuple(candidate.stage_id for candidate in self.candidates)
        requested_stage_ids = tuple(stage.stage_id for stage in request.stages)
        expected_prefix = requested_stage_ids[: len(result_stage_ids)]
        if result_stage_ids != expected_prefix:
            raise ValueError(
                "multi-fidelity result candidates must be an ordered prefix of requested stages"
            )
