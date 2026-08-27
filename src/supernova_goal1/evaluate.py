from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from math import comb
from typing import Any, Iterable, Mapping

from .contracts import Arm, CONTROL_ARMS, ExperimentSpec, GoalDecision, OutcomeRecord


@dataclass(frozen=True)
class PairwiseResult:
    control: str
    candidate_only_wins: int
    control_only_wins: int
    exact_two_sided_p: float
    holm_threshold: float | None = None
    holm_rejects_null: bool = False


def _mcnemar_exact_two_sided(candidate_only: int, control_only: int) -> float:
    discordant = candidate_only + control_only
    if discordant == 0:
        return 1.0
    tail = sum(comb(discordant, index) for index in range(min(candidate_only, control_only) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _apply_holm(results: list[PairwiseResult], alpha: float) -> list[PairwiseResult]:
    ordered = sorted(enumerate(results), key=lambda item: item[1].exact_two_sided_p)
    reject = True
    revised: dict[int, PairwiseResult] = {}
    total = len(results)
    for rank, (original_index, result) in enumerate(ordered):
        threshold = alpha / (total - rank)
        reject = reject and result.exact_two_sided_p <= threshold
        revised[original_index] = PairwiseResult(
            control=result.control,
            candidate_only_wins=result.candidate_only_wins,
            control_only_wins=result.control_only_wins,
            exact_two_sided_p=result.exact_two_sided_p,
            holm_threshold=threshold,
            holm_rejects_null=reject,
        )
    return [revised[index] for index in range(total)]


def evaluate_experiment(
    spec_raw: Mapping[str, Any], records_raw: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    spec = ExperimentSpec.from_mapping(spec_raw)
    records = [OutcomeRecord.from_mapping(raw) for raw in records_raw]
    expected_problems = set(spec.required_problem_ids)
    seen: dict[tuple[str, Arm], OutcomeRecord] = {}

    for record in records:
        if record.experiment_id != spec.experiment_id:
            raise ValueError("record experiment_id does not match the experiment spec")
        if record.problem_id not in expected_problems:
            raise ValueError(f"unexpected problem_id: {record.problem_id}")
        if record.budget_id != spec.budget_id:
            raise ValueError("record budget_id does not match the frozen budget")
        if not record.cost.within(spec.budget_ceiling):
            raise ValueError(
                f"cost ceiling exceeded for {record.problem_id}/{record.arm.value}"
            )
        key = (record.problem_id, record.arm)
        if key in seen:
            raise ValueError(f"duplicate outcome for {record.problem_id}/{record.arm.value}")
        seen[key] = record

    all_arms = tuple(Arm)
    missing = [
        {"problem_id": problem_id, "arm": arm.value}
        for problem_id in spec.required_problem_ids
        for arm in all_arms
        if (problem_id, arm) not in seen
    ]
    solved_counts = {
        arm.value: sum(
            int(seen[(problem_id, arm)].solved)
            for problem_id in spec.required_problem_ids
            if (problem_id, arm) in seen
        )
        for arm in all_arms
    }

    if not spec.cost_model_frozen:
        decision = GoalDecision.BLOCKED
        reason = "complete-cost model is not frozen"
        pairwise: list[PairwiseResult] = []
    elif missing:
        decision = GoalDecision.INCOMPLETE
        reason = "one or more required paired outcomes are missing"
        pairwise = []
    else:
        raw_pairwise: list[PairwiseResult] = []
        for control in CONTROL_ARMS:
            candidate_only = 0
            control_only = 0
            for problem_id in spec.required_problem_ids:
                candidate_solved = seen[(problem_id, Arm.VERIFIED_CHAIN)].solved
                control_solved = seen[(problem_id, control)].solved
                candidate_only += int(candidate_solved and not control_solved)
                control_only += int(control_solved and not candidate_solved)
            raw_pairwise.append(
                PairwiseResult(
                    control=control.value,
                    candidate_only_wins=candidate_only,
                    control_only_wins=control_only,
                    exact_two_sided_p=_mcnemar_exact_two_sided(candidate_only, control_only),
                )
            )
        pairwise = _apply_holm(raw_pairwise, spec.familywise_alpha)
        superior = all(
            result.candidate_only_wins > result.control_only_wins
            and result.holm_rejects_null
            for result in pairwise
        )
        decision = GoalDecision.PASS if superior else GoalDecision.FAIL
        reason = (
            "verified-chain beat every control under Holm-corrected paired tests"
            if superior
            else "verified-chain did not beat every control under the frozen criterion"
        )

    return {
        "experiment_id": spec.experiment_id,
        "phase": spec.phase,
        "decision": decision.value,
        "reason": reason,
        "required_problem_count": len(spec.required_problem_ids),
        "received_record_count": len(records),
        "missing": missing,
        "solved_counts": solved_counts,
        "pairwise": [asdict(result) for result in pairwise],
    }
