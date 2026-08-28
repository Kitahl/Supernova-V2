from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class HolmResult:
    """Holm step-down decision for one p-value, restored to input order."""

    p_value: float
    threshold: float
    rejects_null: bool


def _natural(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def _probability(value: int | float, name: str, *, strict: bool) -> float:
    """Validate an exact built-in numeric probability and snapshot as float."""

    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite built-in int or float")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if strict:
        if not 0 < value < 1:
            raise ValueError(f"{name} must be between 0 and 1")
    elif not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


def _binomial_half_lower_tail_numerator(trials: int, successes: int) -> int:
    """Return sum(C(trials, k), k=0..successes) using exact integers."""

    coefficient = 1
    tail = 1
    for index in range(successes):
        coefficient = coefficient * (trials - index) // (index + 1)
        tail += coefficient
    return tail


def mcnemar_exact_two_sided(candidate_only: int, control_only: int) -> float:
    """Return the bootstrap evaluator's exact two-sided McNemar p-value.

    Only discordant pairs matter. Under the null, either direction of a
    discordance has probability 1/2, so the smaller discordant count is tested
    against Binomial(n=candidate_only + control_only, p=1/2). The evaluator
    uses the conventional doubled smaller-tail exact p-value, capped at 1.

    The binomial tail and denominator are kept as exact integers until the
    final division. This avoids converting very large powers of two through
    floating-point arithmetic while preserving the evaluator's exact ratio.
    """

    candidate_only = _natural(candidate_only, "candidate_only")
    control_only = _natural(control_only, "control_only")
    discordant = candidate_only + control_only
    if discordant == 0:
        return 1.0
    smaller = min(candidate_only, control_only)
    tail = _binomial_half_lower_tail_numerator(discordant, smaller)
    numerator = 2 * tail
    denominator = 1 << discordant
    if numerator >= denominator:
        return 1.0
    return numerator / denominator


def holm_step_down(p_values: Iterable[float], alpha: float) -> tuple[HolmResult, ...]:
    """Apply the bootstrap evaluator's Holm step-down correction.

    P-values are sorted ascending, compared to alpha/(m-rank), and rejection
    stops permanently after the first failed comparison. Results are restored
    to the original input order. Sorting is stable, matching the bootstrap
    evaluator for tied p-values.
    """

    alpha_value = _probability(alpha, "alpha", strict=True)
    values = tuple(
        _probability(value, "p-value", strict=False)
        for value in p_values
    )

    total = len(values)
    if total == 0:
        return ()

    ordered = sorted(enumerate(values), key=lambda item: item[1])
    reject = True
    revised: dict[int, HolmResult] = {}
    for rank, (original_index, p_value) in enumerate(ordered):
        threshold = alpha_value / (total - rank)
        reject = reject and p_value <= threshold
        revised[original_index] = HolmResult(
            p_value=p_value,
            threshold=threshold,
            rejects_null=reject,
        )
    return tuple(revised[index] for index in range(total))
