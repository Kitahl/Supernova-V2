"""Frozen confirmatory Goal-1 evaluator over authenticated bridge evidence."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import comb
from typing import Iterable

from .confirmatory_manifest import NON_CREDIT_DRAFT, canonical_sha256
from .contracts import Arm, CompleteCost, GoalDecision
from .dispatch import CompletionStatus
from .evidence_bridge import (
    ATTEMPTS_PER_CELL,
    EvidenceBridgeBundle,
    EvaluatorEvidenceRecord,
    ExecutionLedgerAuthority,
)
from .statistics import holm_step_down, mcnemar_exact_two_sided


RESULT_SCHEMA = "supernova.goal1-confirmatory-evaluation.v1"
EXPECTED_EXPERIMENT_ID = "goal1-confirmatory-v1"
EXPECTED_PROTOCOL_RULES_SHA256 = (
    "f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9"
)
EXPECTED_BUDGET_ID = "goal1-common-envelope-v1"
EXPECTED_USAGE_BASIS = "visible_utf8_bytes"
PRODUCTION_CREDIT_STATUS = "CONFIRMATORY_CREDIT_ELIGIBLE"
FAMILYWISE_ALPHA = 0.05
FROZEN_CONTROLS = (
    Arm.ORDINARY,
    Arm.PORTFOLIO,
    Arm.PRODUCT_ONLY,
    Arm.MULTI_FIDELITY,
)
EXPECTED_REPORT_PROBLEM_IDS = tuple([
    "aime_1983_p1",
    "aime_1983_p2",
    "aime_1983_p3",
    "aime_1984_p1",
    "aime_1984_p7",
    "aime_1987_p5",
    "aime_1988_p8",
    "aime_1989_p8",
    "aime_1990_p15",
    "aime_1990_p4",
    "aime_1991_p9",
    "aime_1994_p3",
    "aime_1995_p7",
    "aime_1997_p9",
    "aime_1999_p11",
    "algebra_2varlineareq_fp3zeq11_3tfm1m5zeqn68_feqn10_zeq7",
    "algebra_9onxpypzleqsum2onxpy",
    "algebra_abpbcpcageq3_sumaonsqrtapbgeq3onsqrt2",
    "algebra_absapbon1pabsapbleqsumabsaon1pabsa",
    "algebra_absxm1pabsxpabsxp1eqxp2_0leqxleq1",
    "algebra_amgm_sum1toneqn_prod1tonleq1",
    "algebra_amgm_sumasqdivbgeqsuma",
    "algebra_apbmpcneq0_aeq0anbeq0anceq0",
    "algebra_apbon2pownleqapownpbpowon2",
    "algebra_apbpceq2_abpbcpcaeq1_aleq1on3anbleq1ancleq4on3",
    "algebra_bleqa_apbon2msqrtableqambsqon8b",
    "algebra_cubrtrp1oncubrtreq3_rcubp1onrcubeq5778",
    "algebra_ineq_nto1onlt2m1on",
    "algebra_others_exirrpowirrrat",
    "algebra_sqineq_at2malt1",
    "algebra_sqineq_unitcircatbpabsamblt1",
    "algebra_sqineq_unitcircatbpamblt1",
    "algebra_sum1onsqrt2to1onsqrt10000lt198",
    "amc12_2000_p1",
    "amc12_2000_p12",
    "amc12_2000_p20",
    "amc12_2000_p6",
    "amc12_2001_p21",
    "amc12_2001_p5",
    "amc12a_2002_p13",
    "amc12a_2002_p6",
    "amc12a_2003_p23",
    "amc12a_2003_p5",
    "amc12a_2008_p25",
    "amc12a_2009_p6",
    "amc12a_2009_p7",
    "amc12a_2013_p4",
    "amc12a_2019_p12",
    "amc12a_2020_p10",
    "amc12a_2020_p15",
    "amc12a_2020_p25",
    "amc12a_2020_p4",
    "amc12a_2020_p7",
    "amc12a_2020_p9",
    "amc12a_2021_p12",
    "amc12a_2021_p14",
    "amc12a_2021_p18",
    "amc12a_2021_p19",
    "amc12a_2021_p22",
    "amc12a_2021_p25",
    "amc12a_2021_p3",
    "amc12a_2021_p8",
    "amc12a_2021_p9",
    "amc12b_2002_p19",
    "amc12b_2002_p2",
    "amc12b_2002_p4",
    "amc12b_2002_p7",
    "amc12b_2020_p13",
    "amc12b_2020_p2",
    "amc12b_2020_p21",
    "amc12b_2020_p22",
    "amc12b_2020_p6",
    "amc12b_2021_p1",
    "amc12b_2021_p13",
    "amc12b_2021_p18",
    "amc12b_2021_p3",
    "amc12b_2021_p4",
    "amc12b_2021_p9",
    "imo_1959_p1",
    "imo_1960_p2",
    "imo_1962_p2",
    "imo_1963_p5",
    "imo_1964_p2",
    "imo_1965_p2",
    "imo_1968_p5_1",
    "imo_1969_p2",
    "imo_1974_p3",
    "imo_1977_p6",
    "imo_1981_p6",
    "imo_1982_p1",
    "imo_1983_p6",
    "imo_1984_p6",
    "imo_1985_p6",
    "imo_1992_p1",
    "imo_1997_p5",
    "imo_2001_p6",
    "imo_2019_p1",
    "imosl_2007_algebra_p6",
    "induction_11div10tonmn1ton",
    "induction_12dvd4expnp1p20",
    "induction_1pxpownlt1pnx",
    "induction_nfactltnexpnm1ngt3",
    "induction_pord1p1on2powklt5on2",
    "induction_pprime_pdvdapowpma",
    "induction_prod1p1onk3le3m1onn",
    "induction_sumkexp3eqsumksq",
    "mathd_algebra_107",
    "mathd_algebra_113",
    "mathd_algebra_114",
    "mathd_algebra_125",
    "mathd_algebra_129",
    "mathd_algebra_137",
    "mathd_algebra_139",
    "mathd_algebra_141",
    "mathd_algebra_142",
    "mathd_algebra_143",
    "mathd_algebra_148",
    "mathd_algebra_153",
    "mathd_algebra_156",
    "mathd_algebra_158",
    "mathd_algebra_160",
    "mathd_algebra_17",
    "mathd_algebra_170",
    "mathd_algebra_171",
    "mathd_algebra_176",
    "mathd_algebra_184",
    "mathd_algebra_188",
    "mathd_algebra_196",
    "mathd_algebra_208",
    "mathd_algebra_209",
    "mathd_algebra_215",
    "mathd_algebra_24",
    "mathd_algebra_246",
    "mathd_algebra_263",
    "mathd_algebra_270",
    "mathd_algebra_275",
    "mathd_algebra_276",
    "mathd_algebra_288",
    "mathd_algebra_289",
    "mathd_algebra_293",
    "mathd_algebra_296",
    "mathd_algebra_302",
    "mathd_algebra_304",
    "mathd_algebra_313",
    "mathd_algebra_314",
    "mathd_algebra_320",
    "mathd_algebra_329",
    "mathd_algebra_33",
    "mathd_algebra_332",
    "mathd_algebra_338",
    "mathd_algebra_342",
    "mathd_algebra_346",
    "mathd_algebra_354",
    "mathd_algebra_359",
    "mathd_algebra_362",
    "mathd_algebra_388",
    "mathd_algebra_392",
    "mathd_algebra_398",
    "mathd_algebra_400",
    "mathd_algebra_412",
    "mathd_algebra_419",
    "mathd_algebra_427",
    "mathd_algebra_432",
    "mathd_algebra_44",
    "mathd_algebra_440",
    "mathd_algebra_441",
    "mathd_algebra_452",
    "mathd_algebra_459",
    "mathd_algebra_478",
    "mathd_algebra_484",
    "mathd_algebra_487",
    "mathd_algebra_513",
    "mathd_algebra_598",
    "mathd_algebra_756",
    "mathd_algebra_76",
    "mathd_algebra_80",
    "mathd_numbertheory_100",
    "mathd_numbertheory_1124",
    "mathd_numbertheory_12",
    "mathd_numbertheory_127",
    "mathd_numbertheory_135",
    "mathd_numbertheory_150",
    "mathd_numbertheory_175",
    "mathd_numbertheory_185",
    "mathd_numbertheory_207",
    "mathd_numbertheory_212",
    "mathd_numbertheory_222",
    "mathd_numbertheory_227",
    "mathd_numbertheory_229",
    "mathd_numbertheory_233",
    "mathd_numbertheory_234",
    "mathd_numbertheory_235",
    "mathd_numbertheory_237",
    "mathd_numbertheory_239",
    "mathd_numbertheory_247",
    "mathd_numbertheory_254",
    "mathd_numbertheory_277",
    "mathd_numbertheory_293",
    "mathd_numbertheory_296",
    "mathd_numbertheory_299",
    "mathd_numbertheory_3",
    "mathd_numbertheory_314",
    "mathd_numbertheory_320",
    "mathd_numbertheory_321",
    "mathd_numbertheory_328",
    "mathd_numbertheory_34",
    "mathd_numbertheory_341",
    "mathd_numbertheory_342",
    "mathd_numbertheory_343",
    "mathd_numbertheory_345",
    "mathd_numbertheory_353",
    "mathd_numbertheory_427",
    "mathd_numbertheory_430",
    "mathd_numbertheory_435",
    "mathd_numbertheory_447",
    "mathd_numbertheory_451",
    "mathd_numbertheory_457",
    "mathd_numbertheory_483",
    "mathd_numbertheory_495",
    "mathd_numbertheory_5",
    "mathd_numbertheory_517",
    "mathd_numbertheory_521",
    "mathd_numbertheory_541",
    "mathd_numbertheory_551",
    "mathd_numbertheory_552",
    "mathd_numbertheory_559",
    "mathd_numbertheory_582",
    "mathd_numbertheory_618",
    "mathd_numbertheory_66",
    "mathd_numbertheory_711",
    "mathd_numbertheory_728",
    "mathd_numbertheory_764",
    "mathd_numbertheory_765",
    "mathd_numbertheory_769",
    "mathd_numbertheory_85",
    "mathd_numbertheory_99",
    "numbertheory_2pownm1prime_nprime",
    "numbertheory_3pow2pownm1mod2pownp3eq2pownp2",
    "numbertheory_4x3m7y3neq2003",
    "numbertheory_aoddbdiv4asqpbsqmod8eq1",
    "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
    "numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown",
    "numbertheory_notequiv2i2jasqbsqdiv8",
    "numbertheory_x5neqy2p4"
])
EXPECTED_REPORT_PROBLEMS = len(EXPECTED_REPORT_PROBLEM_IDS)
EXPECTED_CELLS = EXPECTED_REPORT_PROBLEMS * len(tuple(Arm))
EXPECTED_ATTEMPTS = EXPECTED_CELLS * ATTEMPTS_PER_CELL
EXPECTED_MODEL_CALLS = EXPECTED_ATTEMPTS

CELL_CEILING = CompleteCost(
    model_calls=16,
    input_tokens=2_097_152,
    output_tokens=98_304,
    verifier_milliseconds=9_600_000,
    orchestration_milliseconds=9_600_000,
)
PREFIX_ENVELOPES = {
    1: CompleteCost(1, 131_072, 2_048, 600_000, 600_000),
    2: CompleteCost(2, 262_144, 4_096, 1_200_000, 1_200_000),
    4: CompleteCost(4, 524_288, 8_192, 2_400_000, 2_400_000),
    8: CompleteCost(8, 1_048_576, 16_384, 4_800_000, 4_800_000),
    16: CELL_CEILING,
}
VECTOR_FIELDS = (
    "protocol_dispatch_ids",
    "protocol_binding_receipt_sha256s",
    "dispatch_ids",
    "completion_record_sha256s",
    "verifier_evidence_sha256s",
    "execution_receipt_sha256s",
    "context_isolation_receipt_sha256s",
    "predecessor_reconciliation_sha256s",
)
LIMITATIONS = (
    "HIDDEN_REASONING_TOKENS_OR_STEPS",
    "PROVIDER_CACHE_EFFECTS",
    "PROVIDER_QUEUE_DELAY",
    "SHARED_SERVING_COMPUTE",
    "HARDWARE_ENERGY",
)


@dataclass(frozen=True)
class _CellSnapshot:
    problem_id: str
    arm: Arm
    statuses: tuple[CompletionStatus, ...]
    cost: CompleteCost


def _cost_mapping(cost: CompleteCost) -> dict[str, int]:
    return {
        "model_calls": cost.model_calls,
        "input_utf8_bytes": cost.input_tokens,
        "output_utf8_bytes": cost.output_tokens,
        "verifier_milliseconds": cost.verifier_milliseconds,
        "orchestration_milliseconds": cost.orchestration_milliseconds,
    }


def _sum_costs(costs: Iterable[CompleteCost]) -> CompleteCost:
    values = tuple(costs)
    return CompleteCost(
        sum(value.model_calls for value in values),
        sum(value.input_tokens for value in values),
        sum(value.output_tokens for value in values),
        sum(value.verifier_milliseconds for value in values),
        sum(value.orchestration_milliseconds for value in values),
    )


def _fractional_mcnemar(candidate_only: int, control_only: int) -> Fraction:
    discordant = candidate_only + control_only
    if discordant == 0:
        return Fraction(1, 1)
    smaller = min(candidate_only, control_only)
    tail = sum(comb(discordant, index) for index in range(smaller + 1))
    return min(Fraction(2 * tail, 1 << discordant), Fraction(1, 1))


def _solved(statuses: tuple[CompletionStatus, ...], prefix: int = 16) -> bool:
    return CompletionStatus.SUCCEEDED in statuses[:prefix]


def _pairwise_counts(
    cells: dict[tuple[str, Arm], _CellSnapshot],
    control: Arm,
    *,
    prefix: int = 16,
) -> dict[str, int]:
    candidate_only = control_only = both = neither = 0
    for problem_id in EXPECTED_REPORT_PROBLEM_IDS:
        candidate = _solved(cells[(problem_id, Arm.VERIFIED_CHAIN)].statuses, prefix)
        baseline = _solved(cells[(problem_id, control)].statuses, prefix)
        candidate_only += int(candidate and not baseline)
        control_only += int(baseline and not candidate)
        both += int(candidate and baseline)
        neither += int(not candidate and not baseline)
    return {
        "candidate_only_wins": candidate_only,
        "control_only_wins": control_only,
        "concordant_both_solved": both,
        "concordant_neither_solved": neither,
        "discordant_total": candidate_only + control_only,
    }


def _safe_attr(value: object, name: str) -> object:
    try:
        return getattr(value, name)
    except Exception:
        return "UNAVAILABLE"


def _base_result(bundle: EvidenceBridgeBundle) -> dict[str, object]:
    bridge_sha256 = _safe_attr(bundle, "bridge_sha256")
    records = _safe_attr(bundle, "records")
    return {
        "schema": RESULT_SCHEMA,
        "run_id": _safe_attr(bundle, "run_id"),
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "bridge_sha256": bridge_sha256,
        "bridge_authority_receipt_sha256": _safe_attr(
            bundle, "authority_receipt_sha256"
        ),
        "protocol_rules_sha256": _safe_attr(bundle, "protocol_rules_sha256"),
        "confirmatory_manifest_sha256": _safe_attr(
            bundle, "confirmatory_manifest_sha256"
        ),
        "dispatch_manifest_sha256": _safe_attr(
            bundle, "dispatch_manifest_sha256"
        ),
        "close_sha256": _safe_attr(bundle, "close_sha256"),
        "completion_set_sha256": _safe_attr(bundle, "completion_set_sha256"),
        "execution_authority_sha256": _safe_attr(
            bundle, "execution_authority_sha256"
        ),
        "manifest_credit_status": _safe_attr(
            bundle, "manifest_credit_status"
        ),
        "model_usage_basis": EXPECTED_USAGE_BASIS,
        "required_problem_count": EXPECTED_REPORT_PROBLEMS,
        "required_cell_count": EXPECTED_CELLS,
        "required_attempt_count": EXPECTED_ATTEMPTS,
        "required_model_call_count": EXPECTED_MODEL_CALLS,
        "received_record_count": len(records) if type(records) is tuple else 0,
        "validated_problem_count": 0,
        "validated_cell_count": 0,
        "validated_attempt_count": 0,
        "validated_model_call_count": 0,
        "missing": [],
        "extra": [],
        "blockers": [],
        "incomplete_reasons": [],
        "decision_eligible": False,
        "decision": GoalDecision.BLOCKED.value,
        "reason": "AUTHENTICATION_NOT_EVALUATED",
        "registered_allocation_envelope_per_cell": _cost_mapping(CELL_CEILING),
        "realized_usage": {},
        "solved": {},
        "contrasts": [],
        "prefix_frontier": [],
        "compute_claim_boundary": {
            "permitted": (
                "VERIFIED_CHAIN_SUPERIORITY_UNDER_A_MATCHED_COMPLETE_"
                "OBSERVABLE_ALLOCATION_ENVELOPE"
            ),
            "forbidden": [
                "EQUAL_REALIZED_COST",
                "EQUAL_PROVIDER_TOKENS",
                "EQUAL_HIDDEN_REASONING_COMPUTE",
                "EQUAL_PHYSICAL_COMPUTE",
            ],
        },
        "unobserved_cost_limitations": list(LIMITATIONS),
    }


def _finish(result: dict[str, object]) -> dict[str, object]:
    frozen = dict(result)
    frozen["result_sha256"] = canonical_sha256(frozen)
    return frozen


def _validate_authenticated_bundle(
    bundle: EvidenceBridgeBundle,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str]],
    list[dict[str, str]],
    tuple[_CellSnapshot, ...],
]:
    blockers: list[str] = []
    incomplete: list[str] = []
    missing: list[dict[str, str]] = []
    extra: list[dict[str, str]] = []
    snapshots: list[_CellSnapshot] = []

    if bundle.protocol_rules_sha256 != EXPECTED_PROTOCOL_RULES_SHA256:
        blockers.append("PROTOCOL_RULES_DIGEST_MISMATCH")
    if bundle.manifest_credit_status == NON_CREDIT_DRAFT:
        blockers.append("NON_CREDIT_DRAFT")
    elif bundle.manifest_credit_status != PRODUCTION_CREDIT_STATUS:
        blockers.append("UNSEALED_CREDIT_STATUS")

    records = bundle.records
    if type(records) is not tuple:
        blockers.append("RECORDS_NOT_IMMUTABLE_TUPLE")
        return blockers, incomplete, missing, extra, ()

    expected_bindings = {
        "manifest_credit_status": bundle.manifest_credit_status,
        "protocol_rules_sha256": bundle.protocol_rules_sha256,
        "confirmatory_manifest_sha256": bundle.confirmatory_manifest_sha256,
        "dispatch_manifest_sha256": bundle.dispatch_manifest_sha256,
        "close_sha256": bundle.close_sha256,
        "completion_set_sha256": bundle.completion_set_sha256,
        "execution_authority_sha256": bundle.execution_authority_sha256,
    }
    observed_keys: list[tuple[str, Arm]] = []
    identity_by_problem: dict[str, str] = {}
    global_vectors: dict[str, list[str]] = {name: [] for name in VECTOR_FIELDS}
    evidence_sha256s: list[str] = []
    cost_trace_sha256s: list[str] = []

    for index, record in enumerate(records):
        if type(record) is not EvaluatorEvidenceRecord:
            blockers.append(f"NON_BRIDGE_RECORD:{index}")
            continue
        for field, expected in expected_bindings.items():
            if getattr(record, field) != expected:
                blockers.append(f"CROSS_BOUND_RECORD:{index}:{field}")
        if record.experiment_id != EXPECTED_EXPERIMENT_ID:
            blockers.append(f"EXPERIMENT_ID_MISMATCH:{index}")
        if record.budget_id != EXPECTED_BUDGET_ID:
            blockers.append(f"BUDGET_ID_MISMATCH:{index}")
        if record.model_usage_basis != EXPECTED_USAGE_BASIS:
            blockers.append(f"MODEL_USAGE_BASIS_MISMATCH:{index}")
        if type(record.arm) is not Arm:
            blockers.append(f"ARM_TYPE_INVALID:{index}")
            continue
        if type(record.problem_id) is not str or not record.problem_id:
            blockers.append(f"PROBLEM_ID_INVALID:{index}")
            continue
        observed_keys.append((record.problem_id, record.arm))
        previous_identity = identity_by_problem.setdefault(
            record.problem_id, record.problem_identity
        )
        if previous_identity != record.problem_identity:
            blockers.append(f"PROBLEM_IDENTITY_MISMATCH:{record.problem_id}")

        statuses = record.completion_statuses
        if type(statuses) is not tuple:
            blockers.append(f"STATUSES_NOT_TUPLE:{index}")
            continue
        if len(statuses) < ATTEMPTS_PER_CELL:
            incomplete.append(f"MISSING_ATTEMPTS:{record.problem_id}:{record.arm.value}")
            continue
        if len(statuses) > ATTEMPTS_PER_CELL:
            blockers.append(f"EXTRA_ATTEMPTS:{record.problem_id}:{record.arm.value}")
            continue
        if not all(type(status) is CompletionStatus for status in statuses):
            blockers.append(f"STATUS_TYPE_INVALID:{index}")
            continue

        vector_ok = True
        for name in VECTOR_FIELDS:
            values = getattr(record, name)
            if type(values) is not tuple:
                blockers.append(f"EVIDENCE_VECTOR_NOT_TUPLE:{index}:{name}")
                vector_ok = False
                continue
            if len(values) < ATTEMPTS_PER_CELL:
                incomplete.append(
                    f"MISSING_EVIDENCE:{record.problem_id}:{record.arm.value}:{name}"
                )
                vector_ok = False
                continue
            if len(values) > ATTEMPTS_PER_CELL:
                blockers.append(
                    f"EXTRA_EVIDENCE:{record.problem_id}:{record.arm.value}:{name}"
                )
                vector_ok = False
                continue
            if not all(type(value) is str and value for value in values):
                blockers.append(f"EVIDENCE_VALUE_INVALID:{index}:{name}")
                vector_ok = False
                continue
            global_vectors[name].extend(values)
        if not vector_ok:
            continue

        for attempt, (status, verifier_evidence) in enumerate(
            zip(statuses, record.verifier_evidence_sha256s, strict=True)
        ):
            if status is CompletionStatus.TIMEOUT:
                blockers.append(
                    f"VERIFIER_TIMEOUT:{record.problem_id}:{record.arm.value}:{attempt}"
                )
            if (
                status is CompletionStatus.ERROR
                and not verifier_evidence.startswith("NOT_INVOKED:")
            ):
                blockers.append(
                    f"VERIFIER_ERROR:{record.problem_id}:{record.arm.value}:{attempt}"
                )

        cost = record.cost
        if type(cost) is not CompleteCost:
            blockers.append(f"COST_TYPE_INVALID:{index}")
            continue
        if any(type(value) is not int or value < 0 for value in cost.as_tuple()):
            blockers.append(f"COST_VALUE_INVALID:{index}")
            continue
        if cost.model_calls < ATTEMPTS_PER_CELL:
            incomplete.append(
                f"MISSING_MODEL_CALLS:{record.problem_id}:{record.arm.value}"
            )
            continue
        if cost.model_calls > ATTEMPTS_PER_CELL:
            blockers.append(
                f"EXTRA_MODEL_CALLS:{record.problem_id}:{record.arm.value}"
            )
            continue
        if not cost.within(CELL_CEILING):
            blockers.append(f"COST_CEILING_EXCEEDED:{record.problem_id}:{record.arm.value}")
            continue

        evidence_sha256s.append(record.evidence_sha256)
        cost_trace_sha256s.append(record.cost_trace_sha256)
        snapshots.append(
            _CellSnapshot(record.problem_id, record.arm, statuses, cost)
        )

    if len(observed_keys) != len(set(observed_keys)):
        blockers.append("DUPLICATE_PROBLEM_ARM_CELL")

    expected_keys = {
        (problem_id, arm)
        for problem_id in EXPECTED_REPORT_PROBLEM_IDS
        for arm in Arm
    }
    observed_set = set(observed_keys)
    missing = [
        {"problem_id": problem_id, "arm": arm.value}
        for problem_id in EXPECTED_REPORT_PROBLEM_IDS
        for arm in Arm
        if (problem_id, arm) not in observed_set
    ]
    extra = [
        {"problem_id": problem_id, "arm": arm.value}
        for problem_id, arm in sorted(
            observed_set - expected_keys, key=lambda item: (item[0], item[1].value)
        )
    ]
    if missing:
        incomplete.append("MISSING_PAIRED_CELLS")
    if extra:
        blockers.append("UNREGISTERED_OR_EXTRA_CELLS")

    expected_order = tuple(
        (problem_id, arm)
        for problem_id in EXPECTED_REPORT_PROBLEM_IDS
        for arm in Arm
    )
    if not missing and not extra and tuple(observed_keys) != expected_order:
        blockers.append("NONCANONICAL_RECORD_ORDER")

    for name, values in global_vectors.items():
        if len(values) != len(set(values)):
            blockers.append(f"REPLAYED_EVIDENCE:{name}")
    if len(evidence_sha256s) != len(set(evidence_sha256s)):
        blockers.append("REPLAYED_EVALUATOR_RECORD")
    if len(cost_trace_sha256s) != len(set(cost_trace_sha256s)):
        blockers.append("REPLAYED_COST_TRACE")

    return (
        sorted(set(blockers)),
        sorted(set(incomplete)),
        missing,
        extra,
        tuple(snapshots),
    )


def _evaluate_complete_snapshots(
    snapshots: tuple[_CellSnapshot, ...],
) -> dict[str, object]:
    cells = {(cell.problem_id, cell.arm): cell for cell in snapshots}
    solved_counts = {
        arm.value: sum(
            int(_solved(cells[(problem_id, arm)].statuses))
            for problem_id in EXPECTED_REPORT_PROBLEM_IDS
        )
        for arm in Arm
    }
    solved_summary = {
        arm.value: {
            "solved": solved_counts[arm.value],
            "denominator": EXPECTED_REPORT_PROBLEMS,
            "rate": solved_counts[arm.value] / EXPECTED_REPORT_PROBLEMS,
        }
        for arm in Arm
    }

    raw: dict[Arm, dict[str, object]] = {}
    for control in FROZEN_CONTROLS:
        counts = _pairwise_counts(cells, control)
        fraction = _fractional_mcnemar(
            counts["candidate_only_wins"], counts["control_only_wins"]
        )
        p_value = mcnemar_exact_two_sided(
            counts["candidate_only_wins"], counts["control_only_wins"]
        )
        if p_value != float(fraction):
            raise AssertionError("McNemar implementations disagree")
        raw[control] = {
            **counts,
            "candidate_solved_count": solved_counts[Arm.VERIFIED_CHAIN.value],
            "control_solved_count": solved_counts[control.value],
            "paired_difference": (
                solved_counts[Arm.VERIFIED_CHAIN.value]
                - solved_counts[control.value]
            ),
            "exact_two_sided_p": p_value,
            "exact_p_numerator": fraction.numerator,
            "exact_p_denominator": fraction.denominator,
        }

    holm_order = sorted(
        FROZEN_CONTROLS,
        key=lambda control: (
            float(raw[control]["exact_two_sided_p"]),
            control.value,
        ),
    )
    corrections = holm_step_down(
        (float(raw[control]["exact_two_sided_p"]) for control in holm_order),
        FAMILYWISE_ALPHA,
    )
    correction_by_control = {
        control: (rank, correction)
        for rank, (control, correction) in enumerate(
            zip(holm_order, corrections, strict=True), start=1
        )
    }
    contrasts = []
    passes = []
    for control in FROZEN_CONTROLS:
        rank, correction = correction_by_control[control]
        positive = int(raw[control]["paired_difference"]) > 0
        contrast_pass = positive and correction.rejects_null
        passes.append(contrast_pass)
        contrasts.append(
            {
                "control": control.value,
                **raw[control],
                "holm_rank": rank,
                "holm_threshold": correction.threshold,
                "holm_rejects_null": correction.rejects_null,
                "positive_direction": positive,
                "contrast_pass": contrast_pass,
            }
        )

    prefix_frontier = []
    for prefix, envelope in PREFIX_ENVELOPES.items():
        prefix_solved = {
            arm.value: sum(
                int(_solved(cells[(problem_id, arm)].statuses, prefix))
                for problem_id in EXPECTED_REPORT_PROBLEM_IDS
            )
            for arm in Arm
        }
        prefix_contrasts = []
        for control in FROZEN_CONTROLS:
            counts = _pairwise_counts(cells, control, prefix=prefix)
            prefix_contrasts.append(
                {
                    "control": control.value,
                    **counts,
                    "paired_difference": (
                        prefix_solved[Arm.VERIFIED_CHAIN.value]
                        - prefix_solved[control.value]
                    ),
                }
            )
        prefix_frontier.append(
            {
                "completed_attempt_prefix": prefix,
                "registered_capacity_per_cell": _cost_mapping(envelope),
                "solved": {
                    arm.value: {
                        "solved": prefix_solved[arm.value],
                        "denominator": EXPECTED_REPORT_PROBLEMS,
                        "rate": prefix_solved[arm.value]
                        / EXPECTED_REPORT_PROBLEMS,
                    }
                    for arm in Arm
                },
                "contrasts": prefix_contrasts,
                "role": "DESCRIPTIVE_ONLY",
                "used_for_terminal_decision": False,
            }
        )

    realized_by_arm = {
        arm.value: _cost_mapping(
            _sum_costs(
                cells[(problem_id, arm)].cost
                for problem_id in EXPECTED_REPORT_PROBLEM_IDS
            )
        )
        for arm in Arm
    }
    realized_by_cell = [
        {
            "problem_id": problem_id,
            "arm": arm.value,
            **_cost_mapping(cells[(problem_id, arm)].cost),
        }
        for problem_id in EXPECTED_REPORT_PROBLEM_IDS
        for arm in Arm
    ]
    decision = GoalDecision.PASS if all(passes) else GoalDecision.FAIL
    return {
        "decision": decision.value,
        "reason": (
            "ALL_FOUR_FROZEN_CONTRASTS_PASS"
            if decision is GoalDecision.PASS
            else "ONE_OR_MORE_FROZEN_CONTRASTS_DO_NOT_PASS"
        ),
        "solved": solved_summary,
        "contrasts": contrasts,
        "prefix_frontier": prefix_frontier,
        "realized_usage": {
            "per_arm_aggregate": realized_by_arm,
            "per_cell": realized_by_cell,
            "realized_cost_used_for_matching_or_exclusion": False,
        },
    }


def evaluate_confirmatory(
    bundle: EvidenceBridgeBundle,
    *,
    evidence_authority: ExecutionLedgerAuthority,
) -> dict[str, object]:
    """Evaluate one authenticated frozen cohort without accepting raw outcomes."""

    if type(bundle) is not EvidenceBridgeBundle:
        raise TypeError("bundle must be an exact EvidenceBridgeBundle")
    if type(evidence_authority) is not ExecutionLedgerAuthority:
        raise TypeError(
            "evidence_authority must be an exact ExecutionLedgerAuthority"
        )
    result = _base_result(bundle)

    try:
        verified_receipt = evidence_authority.verify_evidence_bridge_bundle(bundle)
    except Exception as exc:
        result["blockers"] = ["BRIDGE_AUTHENTICATION_FAILED"]
        result["reason"] = "BRIDGE_AUTHENTICATION_FAILED"
        result["authentication_error_type"] = type(exc).__name__
        return _finish(result)
    result["bridge_authority_receipt_sha256"] = verified_receipt

    try:
        blockers, incomplete, missing, extra, snapshots = (
            _validate_authenticated_bundle(bundle)
        )
    except Exception as exc:
        result["blockers"] = ["EVALUATOR_INTEGRITY_CHECK_FAILED"]
        result["reason"] = "EVALUATOR_INTEGRITY_CHECK_FAILED"
        result["integrity_error_type"] = type(exc).__name__
        return _finish(result)

    result["blockers"] = blockers
    result["incomplete_reasons"] = incomplete
    result["missing"] = missing
    result["extra"] = extra
    result["validated_cell_count"] = len(snapshots)
    result["validated_problem_count"] = len(
        {snapshot.problem_id for snapshot in snapshots}
    )
    result["validated_attempt_count"] = len(snapshots) * ATTEMPTS_PER_CELL
    result["validated_model_call_count"] = sum(
        snapshot.cost.model_calls for snapshot in snapshots
    )

    if blockers:
        result["decision"] = GoalDecision.BLOCKED.value
        result["reason"] = blockers[0]
        return _finish(result)
    if incomplete or missing:
        result["decision"] = GoalDecision.INCOMPLETE.value
        result["reason"] = (incomplete or ["MISSING_PAIRED_CELLS"])[0]
        return _finish(result)
    if len(snapshots) != EXPECTED_CELLS:
        result["decision"] = GoalDecision.INCOMPLETE.value
        result["reason"] = "COMPLETE_COHORT_SIZE_NOT_REACHED"
        return _finish(result)

    scientific = _evaluate_complete_snapshots(snapshots)
    result.update(scientific)
    result["decision_eligible"] = True
    return _finish(result)


__all__ = [
    "EXPECTED_REPORT_PROBLEM_IDS",
    "PRODUCTION_CREDIT_STATUS",
    "RESULT_SCHEMA",
    "evaluate_confirmatory",
]
