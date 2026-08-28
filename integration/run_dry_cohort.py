from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.admission import (  # noqa: E402
    AdmissionEvidence,
    AdmissionPolicy,
    ProductCandidate,
    evaluate_product_admission,
)
from supernova_goal1.arms.multi_fidelity import (  # noqa: E402
    FidelityStage,
    MultiFidelityAttemptStatus,
    MultiFidelityCandidate,
    MultiFidelityRequest,
    MultiFidelityResult,
)
from supernova_goal1.arms.ordinary import (  # noqa: E402
    OrdinaryRequest,
    OrdinaryResult,
    OrdinaryResultStatus,
)
from supernova_goal1.arms.portfolio import (  # noqa: E402
    PortfolioAttemptStatus,
    PortfolioCandidate,
    PortfolioRequest,
    PortfolioResult,
)
from supernova_goal1.arms.product_only import (  # noqa: E402
    ProductOnlyProduct,
    ProductOnlyRequest,
    ProductOnlyResult,
    ProductOnlyStatus,
)
from supernova_goal1.arms.verified_chain import (  # noqa: E402
    SUBJECT_PATH_TOKEN,
    VerifiedChain,
)
from supernova_goal1.assignment import (  # noqa: E402
    Assignment,
    blind_evaluation_order,
    operator_reveal_mapping,
    seeded_paired_assignment,
)
from supernova_goal1.contracts import Arm, CompleteCost, ExperimentSpec  # noqa: E402
from supernova_goal1.cost import (  # noqa: E402
    ArmCostTrace,
    CompleteCostReport,
    CostEvent,
    ExpectedCostEvent,
)
from supernova_goal1.evaluate import evaluate_experiment  # noqa: E402


DEFAULT_SPEC = ROOT / "goal1" / "GOAL1.json"
DEFAULT_BENCHMARK_LOCK = ROOT / "goal1" / "BENCHMARK.lock.json"

PASS_SUBJECT_VERIFIER = r"""
import hashlib,json,sys
with open(sys.argv[1], encoding="utf-8") as handle:
    subject=json.load(handle)
actual=hashlib.sha256(subject["canonical_json"].encode("utf-8")).hexdigest()
raise SystemExit(0 if actual == subject["content_sha256"] else 9)
"""

_PRODUCER_AUTHORITY = "dry-producer-authority"
_VERIFIER_AUTHORITY = "dry-verifier-authority"
_PRODUCER_KEY = hashlib.sha256(b"supernova-v2-g1-014-dry-producer-key").digest()
_VERIFIER_KEY = hashlib.sha256(b"supernova-v2-g1-014-dry-verifier-key").digest()


def _load_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return raw


def _cost_mapping(cost: CompleteCost) -> dict[str, int]:
    return {
        "model_calls": cost.model_calls,
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
        "verifier_milliseconds": cost.verifier_milliseconds,
        "orchestration_milliseconds": cost.orchestration_milliseconds,
    }


def _synthetic_cost_trace(
    assignment: Assignment,
    *,
    model_calls: int,
    verifier_calls: int = 0,
) -> ArmCostTrace:
    """Build explicitly synthetic telemetry for DRY_RUN contract plumbing only."""

    expected: list[ExpectedCostEvent] = []
    observed: list[CostEvent] = []
    for index in range(model_calls):
        event_id = f"{assignment.assignment_id}/synthetic-model/{index}"
        expected.append(ExpectedCostEvent.model_call(event_id))
        observed.append(
            CostEvent.model_call(event_id, input_tokens=10, output_tokens=5)
        )
    for index in range(verifier_calls):
        event_id = f"{assignment.assignment_id}/schema-verifier/{index}"
        expected.append(ExpectedCostEvent.verifier(event_id))
        observed.append(CostEvent.verifier(event_id, milliseconds=1))
    orchestration_id = f"{assignment.assignment_id}/orchestration/0"
    expected.append(ExpectedCostEvent.orchestration(orchestration_id))
    observed.append(CostEvent.orchestration(orchestration_id, milliseconds=1))
    return ArmCostTrace.from_events(
        assignment.arm,
        observed,
        expected_events=expected,
        accounting_complete=True,
    )


def _close_synthetic_cost_report(
    assignments: Mapping[Arm, Assignment],
) -> CompleteCostReport:
    model_calls = {
        Arm.ORDINARY: 1,
        Arm.PORTFOLIO: 2,
        Arm.PRODUCT_ONLY: 2,
        Arm.MULTI_FIDELITY: 2,
        Arm.VERIFIED_CHAIN: 2,
    }
    return CompleteCostReport.from_traces(
        _synthetic_cost_trace(
            assignments[arm],
            model_calls=model_calls[arm],
            verifier_calls=2 if arm is Arm.VERIFIED_CHAIN else 0,
        )
        for arm in Arm
    )


def _exercise_control_arms(
    *,
    spec: ExperimentSpec,
    problem_id: str,
    statement: str,
    assignments: Mapping[Arm, Assignment],
) -> dict[Arm, bool]:
    ordinary_assignment = assignments[Arm.ORDINARY]
    ordinary_request = OrdinaryRequest(
        ordinary_assignment.assignment_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        statement,
    )
    ordinary_result = OrdinaryResult(
        ordinary_request.request_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        OrdinaryResultStatus.NO_ANSWER,
        None,
        None,
    )
    ordinary_result.validate_for(ordinary_request)

    portfolio_assignment = assignments[Arm.PORTFOLIO]
    attempt_ids = tuple(
        f"{portfolio_assignment.assignment_id}/attempt/{index}" for index in range(2)
    )
    portfolio_request = PortfolioRequest(
        portfolio_assignment.assignment_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        statement,
        attempt_ids,
    )
    portfolio_result = PortfolioResult(
        portfolio_request.request_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        tuple(
            PortfolioCandidate(
                attempt_id,
                PortfolioAttemptStatus.NO_ANSWER,
                None,
                None,
            )
            for attempt_id in attempt_ids
        ),
        None,
    )
    portfolio_result.validate_for(portfolio_request)

    product_assignment = assignments[Arm.PRODUCT_ONLY]
    product_request = ProductOnlyRequest(
        product_assignment.assignment_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        statement,
        2,
    )
    product_one = ProductOnlyProduct(
        f"{product_assignment.assignment_id}/product/0",
        None,
        {"dry_lemma": 1},
    )
    product_two = ProductOnlyProduct(
        f"{product_assignment.assignment_id}/product/1",
        product_one.product_id,
        {"dry_lemma": 2},
    )
    product_result = ProductOnlyResult(
        product_request.request_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        (product_one, product_two),
        ProductOnlyStatus.NO_ANSWER,
        None,
        None,
        None,
    )
    product_result.validate_for(product_request)

    multi_assignment = assignments[Arm.MULTI_FIDELITY]
    stages = (
        FidelityStage(f"{multi_assignment.assignment_id}/stage/0", "dry-low", 0),
        FidelityStage(f"{multi_assignment.assignment_id}/stage/1", "dry-high", 1),
    )
    multi_request = MultiFidelityRequest(
        multi_assignment.assignment_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        statement,
        stages,
    )
    multi_result = MultiFidelityResult(
        multi_request.request_id,
        spec.experiment_id,
        problem_id,
        spec.budget_id,
        tuple(
            MultiFidelityCandidate(
                stage.stage_id,
                MultiFidelityAttemptStatus.NO_ANSWER,
                None,
                None,
            )
            for stage in stages
        ),
        None,
    )
    multi_result.validate_for(multi_request)

    common_statements = {
        ordinary_request.problem_statement,
        portfolio_request.problem_statement,
        product_request.problem_statement,
        multi_request.problem_statement,
    }
    if common_statements != {statement}:
        raise AssertionError("control arm requests do not share the dry problem payload")

    return {
        Arm.ORDINARY: ordinary_result.status is OrdinaryResultStatus.ANSWERED,
        Arm.PORTFOLIO: portfolio_result.selected_attempt_id is not None,
        Arm.PRODUCT_ONLY: product_result.status is ProductOnlyStatus.ANSWERED,
        Arm.MULTI_FIDELITY: multi_result.selected_stage_id is not None,
    }


def _admit_final_product(final_product: Any, verifier_id: str) -> dict[str, Any]:
    keys = {
        _PRODUCER_AUTHORITY: _PRODUCER_KEY,
        _VERIFIER_AUTHORITY: _VERIFIER_KEY,
    }
    policy_raw: dict[str, Any] = {
        "policy_id": "g1-014-dry-admission-v1",
        "required_checks": ["kernel"],
        "authorized_verifiers": {"kernel": [verifier_id]},
        "producer_authorities": {"dry-solver": _PRODUCER_AUTHORITY},
        "verifier_authorities": {verifier_id: _VERIFIER_AUTHORITY},
        "authority_key_sha256": {
            authority: hashlib.sha256(key).hexdigest()
            for authority, key in keys.items()
        },
    }
    policy_sha256 = AdmissionPolicy.from_mapping(policy_raw).canonical_sha256()

    product_raw: dict[str, Any] = {
        "product_id": final_product.product_id,
        "producer_id": "dry-solver",
        "artifact_sha256": final_product.content_sha256,
        "producer_hmac_sha256": "0" * 64,
    }
    product = ProductCandidate.from_mapping(product_raw)
    product_raw["producer_hmac_sha256"] = hmac.new(
        _PRODUCER_KEY, product.canonical_bytes(), hashlib.sha256
    ).hexdigest()

    evidence_raw: dict[str, Any] = {
        "evidence_id": f"dry-admission-{final_product.product_id}",
        "check_id": "kernel",
        "product_id": final_product.product_id,
        "artifact_sha256": final_product.content_sha256,
        "verifier_id": verifier_id,
        "outcome": "PASS",
        "policy_sha256": policy_sha256,
        "evidence_sha256": "0" * 64,
        "verifier_hmac_sha256": "0" * 64,
    }
    evidence = AdmissionEvidence.from_mapping(evidence_raw)
    canonical_evidence = evidence.canonical_bytes()
    evidence_raw["evidence_sha256"] = hashlib.sha256(canonical_evidence).hexdigest()
    evidence_raw["verifier_hmac_sha256"] = hmac.new(
        _VERIFIER_KEY, canonical_evidence, hashlib.sha256
    ).hexdigest()

    return evaluate_product_admission(
        product_raw,
        [evidence_raw],
        policy_raw,
        trusted_policy_sha256=policy_sha256,
        authority_auth_keys=keys,
    )


def _exercise_verified_chain(problem_id: str) -> tuple[Any, dict[str, Any], int]:
    command = (sys.executable, "-c", PASS_SUBJECT_VERIFIER, SUBJECT_PATH_TOKEN)
    chain = VerifiedChain(
        problem_id,
        verifier_command=command,
        verifier_timeout_seconds=5,
    )
    lemma_id = f"{problem_id}/verified/lemma"
    chain.propose(lemma_id, {"dry_lemma": problem_id}, producer_id="dry-solver")
    chain.verify_pending(lemma_id)
    lemma = chain.consume_verified(lemma_id)

    final_id = f"{problem_id}/verified/final"
    chain.propose(
        final_id,
        {"dry_answer": problem_id},
        producer_id="dry-solver",
        parent=lemma,
    )
    chain.verify_pending(final_id)
    final_product = chain.finalize(final_id)
    if chain.verifier_id is None:
        raise AssertionError("dry verified chain lost verifier identity")
    admission = _admit_final_product(final_product, chain.verifier_id)
    if not admission["admitted"]:
        raise AssertionError(f"dry final product was not admitted: {admission}")
    return final_product, admission, len(chain.history)


def assemble_dry_cohort(
    spec_raw: Mapping[str, Any], benchmark_lock: Mapping[str, Any]
) -> dict[str, Any]:
    spec = ExperimentSpec.from_mapping(spec_raw)
    if spec.phase != "DRY_RUN":
        raise RuntimeError("G1-014 runner is restricted to phase=DRY_RUN")
    if spec.cost_model_frozen:
        raise RuntimeError("G1-014 runner refuses a frozen scientific cost model")

    assignments = seeded_paired_assignment(
        spec.required_problem_ids,
        "supernova-v2-g1-014-assignment-v1",
    )
    blind = blind_evaluation_order(assignments, "g1-014-evaluator-order-v1")
    reveal = operator_reveal_mapping(assignments, "g1-014-evaluator-order-v1")
    if {item.evaluation_id for item in blind.items} != {
        item.evaluation_id for item in reveal.entries
    }:
        raise AssertionError("blind order and operator reveal map do not join")

    by_problem: dict[str, dict[Arm, Assignment]] = {
        problem_id: {
            assignment.arm: assignment
            for assignment in assignments
            if assignment.problem_id == problem_id
        }
        for problem_id in spec.required_problem_ids
    }
    records: list[dict[str, Any]] = []
    problem_reports: list[dict[str, Any]] = []
    for problem_id in spec.required_problem_ids:
        statement = f"SYNTHETIC DRY-RUN PROBLEM PAYLOAD: {problem_id}"
        arm_assignments = by_problem[problem_id]
        control_solved = _exercise_control_arms(
            spec=spec,
            problem_id=problem_id,
            statement=statement,
            assignments=arm_assignments,
        )
        final_product, admission, chain_steps = _exercise_verified_chain(problem_id)
        solved = {**control_solved, Arm.VERIFIED_CHAIN: True}
        cost_report = _close_synthetic_cost_report(arm_assignments)
        if not all(cost_report.within_budget(spec.budget_ceiling).values()):
            raise AssertionError("synthetic dry cost report exceeded the dry ceiling")

        for arm in Arm:
            records.append(
                {
                    "experiment_id": spec.experiment_id,
                    "problem_id": problem_id,
                    "arm": arm.value,
                    "budget_id": spec.budget_id,
                    "solved": solved[arm],
                    "verifier_passed": arm is Arm.VERIFIED_CHAIN,
                    "cost": _cost_mapping(cost_report.total_for(arm)),
                }
            )
        problem_reports.append(
            {
                "problem_id": problem_id,
                "common_payload_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                "arm_contracts_validated": [arm.value for arm in Arm],
                "verified_chain_history_records": chain_steps,
                "final_product_sha256": final_product.content_sha256,
                "admission": admission["admission"],
                "cost_report_closed": True,
                "synthetic_cost_totals": {
                    arm.value: _cost_mapping(cost_report.total_for(arm)) for arm in Arm
                },
            }
        )

    evaluation = evaluate_experiment(spec_raw, records)
    if evaluation["decision"] != "BLOCKED":
        raise AssertionError("dry cohort unexpectedly received a scientific decision")

    benchmark_status = benchmark_lock.get("status", "MISSING")
    return {
        "status": "DRY_COHORT_COMPLETE",
        "scientific_credit": False,
        "synthetic_telemetry": True,
        "assignment_count": len(assignments),
        "blind_item_count": len(blind.items),
        "reveal_entry_count": len(reveal.entries),
        "problem_reports": problem_reports,
        "evaluator": evaluation,
        "blocking_conditions": [
            {
                "id": "BENCHMARK_NOT_FROZEN",
                "observed": benchmark_status,
                "effect": "no benchmark outcome can receive scientific credit",
            },
            {
                "id": "COMPLETE_COST_NOT_FROZEN",
                "observed": spec.cost_model_frozen,
                "effect": "evaluator must remain BLOCKED",
            },
            {
                "id": "NO_MODEL_EXECUTION_ADAPTER",
                "observed": "synthetic arm results and telemetry",
                "effect": "the run validates contracts and joins, not solver capability",
            },
        ],
        "remaining_integration_seams": [
            {
                "id": "COST_OUTCOME_JOIN_NOT_SCHEMA_BOUND",
                "finding": "OutcomeRecord receives a copied CompleteCost; it cannot identify the CompleteCostReport that produced it.",
            },
            {
                "id": "COMMON_INPUT_DIGEST_NOT_IN_ARM_CONTRACTS",
                "finding": "the runner checks one common statement, but request/outcome schemas do not carry its digest.",
            },
            {
                "id": "VERIFIER_RUNTIME_NOT_HERMETIC",
                "finding": "VerifiedChain binds command and timeout, not executable/toolchain/environment bytes.",
            },
            {
                "id": "CHAIN_ADMISSION_BRIDGE_IS_RUNNER_LOCAL",
                "finding": "the runner translates VerifiedProduct into admission records; the core package has no native bridge contract.",
            },
        ],
    }


def run_dry_cohort(
    spec_path: Path = DEFAULT_SPEC,
    benchmark_lock_path: Path = DEFAULT_BENCHMARK_LOCK,
) -> dict[str, Any]:
    return assemble_dry_cohort(
        _load_object(spec_path),
        _load_object(benchmark_lock_path),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic, non-scientific Goal-1 assembled dry cohort."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--benchmark-lock", type=Path, default=DEFAULT_BENCHMARK_LOCK)
    args = parser.parse_args(argv)
    report = run_dry_cohort(args.spec, args.benchmark_lock)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
