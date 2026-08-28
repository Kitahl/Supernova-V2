from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from .contracts import Arm


@dataclass(frozen=True)
class Assignment:
    """One arm execution for one problem in a paired experiment."""

    assignment_id: str
    problem_id: str
    arm: Arm
    execution_index: int


@dataclass(frozen=True)
class BlindEvaluationItem:
    """Evaluator-visible item. It intentionally carries no arm or assignment ID."""

    evaluation_id: str
    problem_id: str
    evaluation_index: int


@dataclass(frozen=True)
class EvaluatorBlindOrder:
    """Evaluator-facing material containing only blinded evaluation items."""

    items: tuple[BlindEvaluationItem, ...]


@dataclass(frozen=True)
class BlindReveal:
    """Operator-only key joining a blind evaluation ID back to an assignment."""

    evaluation_id: str
    assignment_id: str


@dataclass(frozen=True)
class OperatorRevealMap:
    """Operator-only reveal authority; never pass this object to evaluators."""

    entries: tuple[BlindReveal, ...]


def _seed_bytes(seed: str | int | bytes) -> bytes:
    if isinstance(seed, bool):
        raise TypeError("seed must be str, int, or bytes")
    if isinstance(seed, bytes):
        if not seed:
            raise ValueError("seed must not be empty")
        return b"bytes:" + seed
    if isinstance(seed, str):
        if not seed:
            raise ValueError("seed must not be empty")
        return b"str:" + seed.encode("utf-8")
    if isinstance(seed, int):
        return f"int:{seed}".encode("ascii")
    raise TypeError("seed must be str, int, or bytes")


def _digest(domain: str, seed: bytes, *parts: str) -> bytes:
    hasher = sha256()
    for value in (
        domain.encode("ascii"),
        seed,
        *(part.encode("utf-8") for part in parts),
    ):
        hasher.update(len(value).to_bytes(8, "big"))
        hasher.update(value)
    return hasher.digest()


def _canonical_problem_ids(problem_ids: Iterable[str]) -> tuple[str, ...]:
    values = tuple(problem_ids)
    if not values:
        raise ValueError("problem_ids must not be empty")
    if not all(type(value) is str and value for value in values):
        raise ValueError("problem_ids must contain only non-empty exact str values")
    if len(set(values)) != len(values):
        raise ValueError("problem_ids must be unique")
    return tuple(sorted(values))


def seeded_paired_assignment(
    problem_ids: Iterable[str], seed: str | int | bytes
) -> tuple[Assignment, ...]:
    """Assign every problem to every arm and deterministically seed execution order.

    Ordering uses SHA-256 with explicit domain separation, rather than process-global
    randomness, so the same seed and problem set is reproducible across processes and
    independent of the caller's input ordering.
    """

    seed_bytes = _seed_bytes(seed)
    candidates: list[tuple[bytes, str, Arm, str]] = []
    for problem_id in _canonical_problem_ids(problem_ids):
        for arm in Arm:
            assignment_id = "run-" + _digest(
                "assignment-id-v1", seed_bytes, problem_id, arm.value
            ).hex()
            order_key = _digest("execution-order-v1", seed_bytes, problem_id, arm.value)
            candidates.append((order_key, problem_id, arm, assignment_id))

    candidates.sort(key=lambda row: (row[0], row[1], row[2].value))
    return tuple(
        Assignment(
            assignment_id=assignment_id,
            problem_id=problem_id,
            arm=arm,
            execution_index=index,
        )
        for index, (_, problem_id, arm, assignment_id) in enumerate(candidates)
    )


def _validate_paired_assignments(assignments: tuple[Assignment, ...]) -> None:
    if not assignments:
        raise ValueError("assignments must not be empty")
    if not all(
        type(item.assignment_id) is str
        and item.assignment_id
        and type(item.problem_id) is str
        and item.problem_id
        for item in assignments
    ):
        raise ValueError(
            "assignment_id and problem_id must be non-empty exact str values"
        )
    if len({item.assignment_id for item in assignments}) != len(assignments):
        raise ValueError("assignment_id values must be unique")
    if sorted(item.execution_index for item in assignments) != list(
        range(len(assignments))
    ):
        raise ValueError("execution_index values must be contiguous from zero")

    expected_arms = set(Arm)
    observed: dict[str, set[Arm]] = {}
    for item in assignments:
        arms = observed.setdefault(item.problem_id, set())
        if item.arm in arms:
            raise ValueError(
                f"duplicate arm assignment for {item.problem_id}/{item.arm.value}"
            )
        arms.add(item.arm)
    incomplete = sorted(
        problem_id for problem_id, arms in observed.items() if arms != expected_arms
    )
    if incomplete:
        raise ValueError(f"paired assignment is incomplete for: {', '.join(incomplete)}")


def _blind_rows(
    assignments: Iterable[Assignment], seed: str | int | bytes
) -> tuple[tuple[str, Assignment], ...]:
    assignment_tuple = tuple(assignments)
    _validate_paired_assignments(assignment_tuple)
    seed_bytes = _seed_bytes(seed)
    ranked = sorted(
        assignment_tuple,
        key=lambda item: (
            _digest("evaluation-order-v1", seed_bytes, item.assignment_id),
            item.assignment_id,
        ),
    )
    return tuple(
        (
            "eval-"
            + _digest(
                "evaluation-id-v3",
                seed_bytes,
                assignment.assignment_id,
                assignment.problem_id,
                str(index),
            ).hex(),
            assignment,
        )
        for index, assignment in enumerate(ranked)
    )


def blind_evaluation_order(
    assignments: Iterable[Assignment], seed: str | int | bytes
) -> EvaluatorBlindOrder:
    """Return evaluator-facing blinded ordering with no reveal authority.

    Evaluation IDs commit to the exact opaque assignment IDs without exposing them,
    so an evaluator artifact cannot later be joined to a different assignment plan.
    Operators requiring the post-evaluation join must call ``operator_reveal_mapping``
    separately and keep that object outside the evaluator boundary.
    """

    rows = _blind_rows(assignments, seed)
    return EvaluatorBlindOrder(
        items=tuple(
            BlindEvaluationItem(
                evaluation_id=evaluation_id,
                problem_id=assignment.problem_id,
                evaluation_index=index,
            )
            for index, (evaluation_id, assignment) in enumerate(rows)
        )
    )


def operator_reveal_mapping(
    assignments: Iterable[Assignment], seed: str | int | bytes
) -> OperatorRevealMap:
    """Return the operator-only join from blinded IDs to assignment IDs.

    This is a separate authority-bearing interface from ``blind_evaluation_order`` so
    evaluator code does not receive reveal material as part of its result object.
    """

    rows = _blind_rows(assignments, seed)
    return OperatorRevealMap(
        entries=tuple(
            BlindReveal(
                evaluation_id=evaluation_id,
                assignment_id=assignment.assignment_id,
            )
            for evaluation_id, assignment in rows
        )
    )
