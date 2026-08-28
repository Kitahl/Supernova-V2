from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.artifacts import (  # noqa: E402
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm  # noqa: E402
from supernova_goal1.cost import ArmCostTrace, CompleteCostReport  # noqa: E402
from supernova_goal1.dispatch import DispatchAuthority, DispatchManifest  # noqa: E402
from supernova_goal1.execution.baselines import (  # noqa: E402
    ModelAttemptObservation,
    execute_ordinary,
    execute_portfolio_attempt,
)
from supernova_goal1.execution.common import (  # noqa: E402
    AttemptStatus,
    FrozenProblemRequest,
)
from supernova_goal1.execution.product_controls import (  # noqa: E402
    FidelityStage,
    ProductControlObservation,
    ProductObservationKind,
    execute_multi_fidelity_stage,
    execute_product_only_step,
    render_multi_fidelity_request,
    render_product_emission,
    render_product_only_request,
)
from supernova_goal1.execution.verified_chain import (  # noqa: E402
    RetryLink,
    VerifiedChainExecutionAuthority,
    VerifiedChainObservation,
    VerifiedChainObservationKind,
    execute_verified_chain_step,
    render_verified_chain_request,
    render_verified_product_emission,
)
from supernova_goal1.problem import BenchmarkProblemIdentity  # noqa: E402
from supernova_goal1.verifier import VerifierResult, VerifierStatus  # noqa: E402


CLASSIFICATION = "NON_CREDIT_PILOT"
RUN_ID = "g1-114-non-credit-pilot"
EXPERIMENT_ID = "g1-114-engineering-only"
BENCHMARK_NAME = "miniF2F-Lean4-Kimina-composite"
BENCHMARK_VERSION = "deepseek-v1.5-2c4ba911+kimina-5def318"
BENCHMARK_SPLIT = "validation"
BENCHMARK_ROOT_SHA256 = (
    "914c05427e1e7e0979f4ca058f90fb3138ee0d3319233b415194c10e67d3683b"
)
PROBLEM_NATIVE_ID = "G1-114-ENGINEERING-STUB-NOT-A-BENCHMARK-MEMBER"
PROMPT = b"Engineering stub: prove that 1 + 1 = 2 in Lean."
FINAL_ANSWER = b"by\n  norm_num"
PRODUCT_ONLY_CONTENT = b"candidate helper: one plus one equals two"
FAILED_CHAIN_PRODUCT = b"lemma broken_helper : 1 + 1 = 3 := by norm_num"
PASSED_CHAIN_PRODUCT = b"lemma helper : 1 + 1 = 2 := by norm_num"


def _digest(label: str | bytes) -> str:
    payload = label if type(label) is bytes else label.encode("utf-8")
    return sha256(payload).hexdigest()


# This is intentionally a digest of the engineering fixture, not a claim that
# PROMPT is one of the bytes committed by BENCHMARK_ROOT_SHA256.
PROBLEM_SHA256 = _digest(b"G1-114-ENGINEERING-STUB\0" + PROMPT)
BUDGET_SHA256 = _digest("G1-114 engineering budget stub")
RUNTIME_SHA256 = _digest("G1-114 hermetic runtime identity stub")


def _problem() -> BenchmarkProblemIdentity:
    return BenchmarkProblemIdentity(
        BENCHMARK_NAME,
        BENCHMARK_VERSION,
        BENCHMARK_SPLIT,
        PROBLEM_NATIVE_ID,
    )


def _request(
    *,
    problem: BenchmarkProblemIdentity,
    arm: Arm,
    attempt: int,
    request_utf8: bytes,
) -> FrozenProblemRequest:
    artifact = ScheduledChatArtifactEnvelope.from_visible_utf8(
        request_utf8,
        kind=ScheduledChatArtifactKind.REQUEST,
        run_id=RUN_ID,
        problem_id=problem.canonical_id,
        arm=arm,
        attempt=attempt,
    )
    return FrozenProblemRequest(
        run_id=RUN_ID,
        experiment_id=EXPERIMENT_ID,
        problem=problem,
        benchmark_root_sha256=BENCHMARK_ROOT_SHA256,
        problem_sha256=PROBLEM_SHA256,
        arm=arm,
        attempt=attempt,
        budget_id="g1-114-engineering-budget-stub",
        budget_sha256=BUDGET_SHA256,
        model_usage_basis="visible_utf8_bytes",
        runtime_sha256=RUNTIME_SHA256,
        request_artifact=artifact,
    )


def _verifier_result(status: VerifierStatus, candidate: bytes) -> VerifierResult:
    if status is VerifierStatus.PASS:
        return VerifierResult(
            status=status,
            command=("engineering-stub-verifier", "--no-lean-executed"),
            returncode=0,
            stdout=f"engineering stub accepted {len(candidate)} bytes\n",
            stderr="",
            elapsed_milliseconds=3,
        )
    return VerifierResult(
        status=status,
        command=("engineering-stub-verifier", "--no-lean-executed"),
        returncode=1,
        stdout="",
        stderr="engineering stub rejected candidate\n",
        elapsed_milliseconds=2,
    )


def _passing_verifier(_dispatch, candidate: bytes) -> VerifierResult:
    if candidate != FINAL_ANSWER:
        raise AssertionError("engineering verifier received unexpected final bytes")
    return _verifier_result(VerifierStatus.PASS, candidate)


def _aggregate_trace(arm: Arm, traces: tuple[ArmCostTrace, ...]) -> ArmCostTrace:
    return ArmCostTrace.from_events(
        arm,
        tuple(event for trace in traces for event in trace.events),
        expected_events=tuple(
            event for trace in traces for event in trace.expected_events
        ),
        accounting_complete=all(trace.accounting_complete for trace in traces),
    )


def _cost_mapping(report: CompleteCostReport) -> dict[str, dict[str, int]]:
    names = report.cost_dimension_names
    return {
        arm.value: dict(zip(names, report.total_for(arm).as_tuple(), strict=True))
        for arm in Arm
    }


def run_non_credit_pilot(work_directory: str | Path) -> dict[str, object]:
    """Run one local five-arm engineering cohort without model or Lean calls."""

    work = Path(work_directory).resolve()
    work.mkdir(parents=True, exist_ok=True)
    authority = DispatchAuthority(str(work / "dispatch.sqlite"), RUN_ID)
    execution_authority = VerifiedChainExecutionAuthority(
        str(work / "verified-chain.sqlite"),
        bytes.fromhex(_digest("G1-114 fixed host-owned execution secret")),
    )
    problem = _problem()
    manifest: DispatchManifest = authority.current_manifest()
    completions = []
    traces: dict[Arm, list[ArmCostTrace]] = defaultdict(list)
    model_calls: dict[Arm, int] = defaultdict(int)

    def baseline_model(arm: Arm) -> Callable:
        def call(dispatch, request_utf8):
            if not dispatch.request.request_artifact.verifies(request_utf8):
                raise AssertionError("model port received bytes outside frozen artifact")
            model_calls[arm] += 1
            return ModelAttemptObservation(
                dispatch.entry.dispatch_id,
                FINAL_ANSWER,
                AttemptStatus.ANSWERED,
            )

        return call

    ordinary = execute_ordinary(
        authority=authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.ORDINARY,
            attempt=0,
            request_utf8=PROMPT,
        ),
        request_utf8=PROMPT,
        model_call=baseline_model(Arm.ORDINARY),
        verifier_call=_passing_verifier,
    )
    manifest = ordinary.manifest
    completions.append(ordinary.completion)
    traces[Arm.ORDINARY].append(ordinary.cost_trace)

    portfolio = execute_portfolio_attempt(
        authority=authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.PORTFOLIO,
            attempt=0,
            request_utf8=PROMPT,
        ),
        request_utf8=PROMPT,
        model_call=baseline_model(Arm.PORTFOLIO),
        verifier_call=_passing_verifier,
    )
    manifest = portfolio.manifest
    completions.append(portfolio.completion)
    traces[Arm.PORTFOLIO].append(portfolio.cost_trace)

    product_request_0 = render_product_only_request(
        PROMPT,
        visible_products=(),
        retry_of_attempt=None,
    )

    def emit_product(dispatch, request_utf8):
        if request_utf8 != product_request_0:
            raise AssertionError("wrong product-only producer request")
        model_calls[Arm.PRODUCT_ONLY] += 1
        return ProductControlObservation(
            dispatch.entry.dispatch_id,
            ProductObservationKind.PRODUCT,
            render_product_emission(PRODUCT_ONLY_CONTENT),
        )

    product_0 = execute_product_only_step(
        authority=authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.PRODUCT_ONLY,
            attempt=0,
            request_utf8=product_request_0,
        ),
        problem_prompt_utf8=PROMPT,
        visible_products=(),
        retry_of_attempt=None,
        model_call=emit_product,
        verifier_call=lambda *_: (_ for _ in ()).throw(
            AssertionError("unverified product must not invoke verifier")
        ),
    )
    manifest = product_0.baseline.manifest
    completions.append(product_0.baseline.completion)
    traces[Arm.PRODUCT_ONLY].append(product_0.baseline.cost_trace)
    if product_0.emitted_product is None:
        raise AssertionError("product-only producer emitted no visible product")

    product_request_1 = render_product_only_request(
        PROMPT,
        visible_products=(product_0.emitted_product,),
        retry_of_attempt=0,
    )

    def answer_with_product(dispatch, request_utf8):
        if request_utf8 != product_request_1:
            raise AssertionError("wrong product-only consumer request")
        model_calls[Arm.PRODUCT_ONLY] += 1
        return ProductControlObservation(
            dispatch.entry.dispatch_id,
            ProductObservationKind.ANSWERED,
            FINAL_ANSWER,
        )

    product_1 = execute_product_only_step(
        authority=authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.PRODUCT_ONLY,
            attempt=1,
            request_utf8=product_request_1,
        ),
        problem_prompt_utf8=PROMPT,
        visible_products=(product_0.emitted_product,),
        retry_of_attempt=0,
        model_call=answer_with_product,
        verifier_call=_passing_verifier,
    )
    manifest = product_1.baseline.manifest
    completions.append(product_1.baseline.completion)
    traces[Arm.PRODUCT_ONLY].append(product_1.baseline.cost_trace)

    stage = FidelityStage("engineering-full-stage", 0, None)
    fidelity_request = render_multi_fidelity_request(PROMPT, stage=stage)

    def fidelity_model(dispatch, request_utf8):
        if request_utf8 != fidelity_request:
            raise AssertionError("wrong multi-fidelity request")
        model_calls[Arm.MULTI_FIDELITY] += 1
        return ModelAttemptObservation(
            dispatch.entry.dispatch_id,
            FINAL_ANSWER,
            AttemptStatus.ANSWERED,
        )

    fidelity = execute_multi_fidelity_stage(
        authority=authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.MULTI_FIDELITY,
            attempt=0,
            request_utf8=fidelity_request,
        ),
        problem_prompt_utf8=PROMPT,
        stage=stage,
        model_call=fidelity_model,
        verifier_call=_passing_verifier,
    )
    manifest = fidelity.baseline.manifest
    completions.append(fidelity.baseline.completion)
    traces[Arm.MULTI_FIDELITY].append(fidelity.baseline.cost_trace)

    failed_response = render_verified_product_emission(FAILED_CHAIN_PRODUCT)
    chain_request_0 = render_verified_chain_request(
        PROMPT,
        execution_authority=execution_authority,
        admitted_products=(),
        retry_of=None,
    )

    def failed_chain_model(dispatch, request_utf8):
        if request_utf8 != chain_request_0:
            raise AssertionError("wrong initial verified-chain request")
        model_calls[Arm.VERIFIED_CHAIN] += 1
        return VerifiedChainObservation(
            dispatch.entry.dispatch_id,
            VerifiedChainObservationKind.PRODUCT,
            failed_response,
        )

    chain_0 = execute_verified_chain_step(
        authority=authority,
        execution_authority=execution_authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.VERIFIED_CHAIN,
            attempt=0,
            request_utf8=chain_request_0,
        ),
        problem_prompt_utf8=PROMPT,
        admitted_products=(),
        retry_of=None,
        model_call=failed_chain_model,
        verifier_call=lambda _dispatch, candidate: _verifier_result(
            VerifierStatus.FAIL, candidate
        ),
    )
    manifest = chain_0.baseline.manifest
    completions.append(chain_0.baseline.completion)
    traces[Arm.VERIFIED_CHAIN].append(chain_0.baseline.cost_trace)
    retry = RetryLink(chain_0.baseline.completion)

    passed_response = render_verified_product_emission(PASSED_CHAIN_PRODUCT)
    chain_request_1 = render_verified_chain_request(
        PROMPT,
        execution_authority=execution_authority,
        admitted_products=(),
        retry_of=retry,
    )

    def repaired_chain_model(dispatch, request_utf8):
        if request_utf8 != chain_request_1:
            raise AssertionError("wrong verified-chain retry request")
        model_calls[Arm.VERIFIED_CHAIN] += 1
        return VerifiedChainObservation(
            dispatch.entry.dispatch_id,
            VerifiedChainObservationKind.PRODUCT,
            passed_response,
        )

    chain_1 = execute_verified_chain_step(
        authority=authority,
        execution_authority=execution_authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.VERIFIED_CHAIN,
            attempt=1,
            request_utf8=chain_request_1,
        ),
        problem_prompt_utf8=PROMPT,
        admitted_products=(),
        retry_of=retry,
        model_call=repaired_chain_model,
        verifier_call=lambda _dispatch, candidate: _verifier_result(
            VerifierStatus.PASS, candidate
        ),
    )
    manifest = chain_1.baseline.manifest
    completions.append(chain_1.baseline.completion)
    traces[Arm.VERIFIED_CHAIN].append(chain_1.baseline.cost_trace)
    if chain_1.admitted_product is None:
        raise AssertionError("passing verified product was not admitted")

    chain_request_2 = render_verified_chain_request(
        PROMPT,
        execution_authority=execution_authority,
        admitted_products=(chain_1.admitted_product,),
        retry_of=None,
    )

    def terminal_chain_model(dispatch, request_utf8):
        if request_utf8 != chain_request_2:
            raise AssertionError("wrong verified-chain feed-forward request")
        model_calls[Arm.VERIFIED_CHAIN] += 1
        return VerifiedChainObservation(
            dispatch.entry.dispatch_id,
            VerifiedChainObservationKind.ANSWERED,
            FINAL_ANSWER,
        )

    chain_2 = execute_verified_chain_step(
        authority=authority,
        execution_authority=execution_authority,
        manifest=manifest,
        request=_request(
            problem=problem,
            arm=Arm.VERIFIED_CHAIN,
            attempt=2,
            request_utf8=chain_request_2,
        ),
        problem_prompt_utf8=PROMPT,
        admitted_products=(chain_1.admitted_product,),
        retry_of=None,
        model_call=terminal_chain_model,
        verifier_call=_passing_verifier,
    )
    manifest = chain_2.baseline.manifest
    completions.append(chain_2.baseline.completion)
    traces[Arm.VERIFIED_CHAIN].append(chain_2.baseline.cost_trace)

    aggregated = tuple(
        _aggregate_trace(arm, tuple(traces[arm]))
        for arm in Arm
    )
    cost_report = CompleteCostReport.from_traces(aggregated)
    join = authority.close(manifest, tuple(completions))
    authority.verify_closed_join(join)

    receipts = [
        completion.payload.verifier_receipt
        for completion in completions
        if completion.payload.verifier_receipt is not None
    ]
    return {
        "classification": CLASSIFICATION,
        "scientific_claim": "NONE",
        "goal1_result": "NOT_EVALUATED",
        "benchmark_lineage": {
            "benchmark": BENCHMARK_NAME,
            "version": BENCHMARK_VERSION,
            "split": BENCHMARK_SPLIT,
            "locked_root_sha256": BENCHMARK_ROOT_SHA256,
            "problem_native_id": PROBLEM_NATIVE_ID,
            "problem_sha256": PROBLEM_SHA256,
            "problem_membership": "NOT_ESTABLISHED_ENGINEERING_STUB",
        },
        "evidence": {
            "arms": [arm.value for arm in Arm],
            "completion_count": len(join.joined),
            "dispatch_closed": True,
            "manifest_sha256": join.receipt.manifest_sha256,
            "close_sha256": join.receipt.close_sha256,
            "verifier_receipt_count": len(receipts),
            "verifier_statuses": [receipt.status.value for receipt in receipts],
            "model_call_counts": {
                arm.value: model_calls[arm] for arm in Arm
            },
            "cost_usage_basis": cost_report.model_usage_basis.value,
            "costs": _cost_mapping(cost_report),
            "verified_retry_evidence_id": execution_authority.verify_retry(retry),
            "admitted_product_evidence_id": (
                execution_authority.verify_admitted_product(
                    chain_1.admitted_product
                )
            ),
            "terminal_verified_chain_answer": chain_2.terminal_answer,
        },
        "seam_failures": {
            "authority_to_evaluator": (
                "BLOCKED_NO_TYPED_PATH_FROM_COMPLETION_JOIN_RECEIPTS_AND_COSTS_"
                "TO_EVALUATOR_OUTCOME_RECORDS"
            ),
            "product_control_retry": (
                "ATTEMPT_INDEX_ONLY_WITHOUT_PREDECESSOR_COMPLETION_SIGNATURE_"
                "OR_TRUSTED_EXECUTION_LEDGER_BINDING"
            ),
            "problem_and_verifier": (
                "ENGINEERING_STUB_BYTES_AND_STUB_RECEIPTS_NOT_BENCHMARK_OR_LEAN_EVIDENCE"
            ),
            "determinism": (
                "CONTROL_FLOW_BYTES_AND_OUTCOMES_FIXED_BUT_FRESH_COMPLETION_KEYS_"
                "AUTHORITY_IDS_AND_LIVE_ORCHESTRATION_MILLISECONDS_ARE_NOT_GOLDEN"
            ),
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="supernova-g1-114-") as temporary:
        report = run_non_credit_pilot(temporary)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
