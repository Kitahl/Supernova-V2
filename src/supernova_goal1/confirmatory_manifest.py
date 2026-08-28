from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import unicodedata
from typing import Any, Mapping, Sequence


CONFIRMATORY_MANIFEST_SCHEMA = "supernova.confirmatory-manifest.v1"
OPERATOR_PLAN_SCHEMA = "supernova.confirmatory-operator-plan.v1"
NON_CREDIT_DRAFT = "NON_CREDIT_DRAFT"
BLOCKED_NO_EXECUTION_AUTHORITY = "BLOCKED_NO_EXECUTION_AUTHORITY"
EXPECTED_PROTOCOL_RULES_SHA256 = (
    "f1e650bc1f33d083c92f4df2a314bef79f8f646fa23431e39a2ebb83b28212e9"
)
CANONICAL_ARMS = (
    "ordinary",
    "portfolio",
    "product_only",
    "multi_fidelity",
    "verified_chain",
)
CHAINED_ARMS = frozenset({"product_only", "multi_fidelity", "verified_chain"})
ATTEMPTS = tuple(range(16))
EXPECTED_REPORT_PROBLEMS = 244
EXPECTED_CELLS = EXPECTED_REPORT_PROBLEMS * len(CANONICAL_ARMS)
EXPECTED_DISPATCH_RECORDS = EXPECTED_CELLS * len(ATTEMPTS)


@dataclass(frozen=True)
class ConfirmatoryManifestBundle:
    """Separated public manifest and operator-only arm/reveal plan."""

    public_manifest: dict[str, object]
    operator_plan: dict[str, object]


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _sha256_hex(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value


def _git_blob_sha1(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 40 lowercase hexadecimal characters")
    return value


def _digest_id(domain: str, *parts: str) -> str:
    hasher = sha256()
    for value in (domain, *parts):
        encoded = value.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


def _as_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _as_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _validate_protocol(protocol: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(protocol, Mapping):
        raise ValueError("protocol must be an object")
    if protocol.get("protocol_rules_status") != "SEALED":
        raise ValueError("confirmatory protocol rules are not SEALED")
    if protocol.get("confirmatory_execution_status") != BLOCKED_NO_EXECUTION_AUTHORITY:
        raise ValueError("draft builder requires the sealed pre-execution protocol")
    rules = _as_mapping(protocol.get("sealed_rules"), "sealed_rules")
    recorded_digest = _sha256_hex(
        protocol.get("sealed_rules_sha256"), "sealed_rules_sha256"
    )
    if canonical_sha256(rules) != recorded_digest:
        raise ValueError("sealed_rules_sha256 does not match sealed_rules")
    if recorded_digest != EXPECTED_PROTOCOL_RULES_SHA256:
        raise ValueError("unsupported confirmatory protocol rules digest")
    if rules.get("schema") != "supernova.confirmatory-protocol-rules.v1":
        raise ValueError("unsupported confirmatory protocol rules schema")

    paired = _as_mapping(rules.get("paired_design"), "paired_design")
    if paired.get("arms") != list(CANONICAL_ARMS):
        raise ValueError("paired arm order changed")
    if paired.get("attempts_per_problem_arm") != len(ATTEMPTS):
        raise ValueError("attempt count changed")
    if paired.get("required_cells") != EXPECTED_CELLS:
        raise ValueError("required cell count changed")
    if paired.get("required_model_call_slots") != EXPECTED_DISPATCH_RECORDS:
        raise ValueError("required model-call slot count changed")
    if paired.get("all_five_arms_required_for_every_report_problem") is not True:
        raise ValueError("paired completeness requirement changed")

    schedule = _as_mapping(rules.get("deterministic_schedule"), "deterministic_schedule")
    if schedule.get("report_problem_order") != "UNICODE_SORTED_REPORT_PROBLEM_IDS":
        raise ValueError("report problem order changed")
    if schedule.get("canonical_arm_order") != list(CANONICAL_ARMS):
        raise ValueError("canonical arm order changed")
    if schedule.get("arm_order_per_problem") != (
        "ROTATE_CANONICAL_ARM_ORDER_LEFT_BY_REPORT_PROBLEM_INDEX_MODULO_5"
    ):
        raise ValueError("arm rotation changed")
    if schedule.get("attempt_order_per_arm") != "ASCENDING_0_THROUGH_15":
        raise ValueError("attempt order changed")
    if schedule.get("cross_cell_interleaving") != (
        "ROUND_ROBIN_BY_ATTEMPT_THEN_REPORT_PROBLEM_THEN_ROTATED_ARM_POSITION"
    ):
        raise ValueError("cross-cell interleaving changed")
    if schedule.get("scheduling_change_after_manifest") != "BLOCKED":
        raise ValueError("post-manifest schedule mutation is not blocked")

    selection = _as_mapping(rules.get("benchmark_selection"), "benchmark_selection")
    report = _as_mapping(selection.get("report_split"), "report_split")
    problem_ids = _as_list(report.get("problem_ids"), "report problem_ids")
    if report.get("count") != EXPECTED_REPORT_PROBLEMS:
        raise ValueError("report count changed")
    if len(problem_ids) != EXPECTED_REPORT_PROBLEMS:
        raise ValueError("report problem list must contain exactly 244 ids")
    if problem_ids != sorted(problem_ids):
        raise ValueError("report problem ids are not Unicode sorted")
    if len(set(problem_ids)) != len(problem_ids):
        raise ValueError("report problem ids must be unique")
    for index, problem_id in enumerate(problem_ids):
        _token(problem_id, f"report problem_ids[{index}]")

    family = _as_mapping(rules.get("family_design"), "family_design")
    family_map = _as_list(
        family.get("report_problem_family_map"), "report_problem_family_map"
    )
    if family.get("report_family_count") != EXPECTED_REPORT_PROBLEMS:
        raise ValueError("report family count changed")
    if len(family_map) != EXPECTED_REPORT_PROBLEMS:
        raise ValueError("report family map must contain exactly 244 entries")
    family_by_problem: dict[str, str] = {}
    for index, item in enumerate(family_map):
        item = _as_mapping(item, f"report_problem_family_map[{index}]")
        if set(item) != {"family_id", "problem_id", "source_family_key"}:
            raise ValueError("report family entry fields changed")
        problem_id = _token(item["problem_id"], "family problem_id")
        family_id = _token(item["family_id"], "family_id")
        _token(item["source_family_key"], "source_family_key")
        if problem_id in family_by_problem:
            raise ValueError("duplicate report family problem_id")
        family_by_problem[problem_id] = family_id
    if set(family_by_problem) != set(problem_ids):
        raise ValueError("report family map does not cover the selected report problems")
    if len(set(family_by_problem.values())) != EXPECTED_REPORT_PROBLEMS:
        raise ValueError("report family ids must be unique")

    authorities = _as_mapping(rules.get("frozen_authorities"), "frozen_authorities")
    if set(authorities) != {
        "benchmark",
        "runtime",
        "baselines",
        "product_controls",
        "verified_chain",
        "cost_policy",
    }:
        raise ValueError("frozen authority set changed")
    for name, raw in authorities.items():
        authority = _as_mapping(raw, f"frozen_authorities.{name}")
        _token(authority.get("path"), f"frozen_authorities.{name}.path")
        _git_blob_sha1(
            authority.get("git_blob_sha1"),
            f"frozen_authorities.{name}.git_blob_sha1",
        )

    interface = _as_mapping(
        rules.get("confirmatory_manifest_interface"),
        "confirmatory_manifest_interface",
    )
    if interface.get("required_schema") != CONFIRMATORY_MANIFEST_SCHEMA:
        raise ValueError("confirmatory manifest schema changed")
    required_bindings = set(_as_list(interface.get("binds"), "manifest binds"))
    if required_bindings != {
        "protocol_rules_sha256",
        "execution_authority_sha256",
        "benchmark_selection_sha256",
        "family_map_sha256",
        "cost_policy_sha256",
        "runtime_sha256",
        "model_identity_sha256",
        "schedule_sha256",
        "all_19520_dispatch_records_sha256",
    }:
        raise ValueError("confirmatory manifest binding set changed")
    if interface.get("missing_invalid_or_post_dispatch_mutation") != "BLOCKED":
        raise ValueError("manifest mutation policy changed")
    return rules


def _operator_seed(value: object) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError("operator_seed must be exactly 32 bytes")
    return value


def _secret_digest_id(domain: str, seed: bytes, *parts: str) -> str:
    material = bytearray()
    for value in (domain, *parts):
        encoded = value.encode("utf-8")
        material.extend(len(encoded).to_bytes(8, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), sha256).hexdigest()


def _build_records(
    protocol_rules_sha256: str,
    problem_ids: Sequence[str],
    family_by_problem: Mapping[str, str],
    operator_seed: bytes,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    operator_records: list[dict[str, object]] = []
    dispatch_by_slot: dict[tuple[str, str, int], str] = {}

    for attempt_index in ATTEMPTS:
        for problem_index, problem_id in enumerate(problem_ids):
            rotation = problem_index % len(CANONICAL_ARMS)
            rotated = CANONICAL_ARMS[rotation:] + CANONICAL_ARMS[:rotation]
            for arm_position, arm in enumerate(rotated):
                dispatch_index = len(operator_records)
                dispatch_id = "dispatch-" + _digest_id(
                    "supernova.confirmatory-dispatch.v1",
                    protocol_rules_sha256,
                    problem_id,
                    arm,
                    str(attempt_index),
                )
                evaluation_id = "eval-" + _secret_digest_id(
                    "supernova.confirmatory-evaluation.v2",
                    operator_seed,
                    protocol_rules_sha256,
                    dispatch_id,
                )
                predecessor_attempt_index: int | None = None
                predecessor_dispatch_id: str | None = None
                if arm in CHAINED_ARMS and attempt_index > 0:
                    predecessor_attempt_index = attempt_index - 1
                    predecessor_dispatch_id = dispatch_by_slot[
                        (problem_id, arm, attempt_index - 1)
                    ]
                dispatch_by_slot[(problem_id, arm, attempt_index)] = dispatch_id

                operator_records.append(
                    {
                        "arm": arm,
                        "arm_position": arm_position,
                        "budget_attempt_index": attempt_index,
                        "dispatch_id": dispatch_id,
                        "dispatch_index": dispatch_index,
                        "evaluation_id": evaluation_id,
                        "family_id": family_by_problem[problem_id],
                        "predecessor_attempt_index": predecessor_attempt_index,
                        "predecessor_dispatch_id": predecessor_dispatch_id,
                        "problem_id": problem_id,
                        "problem_index": problem_index,
                        "registered_model_call_slots": 1,
                        "retry_allowance": 0,
                    }
                )

    ranked = sorted(
        operator_records,
        key=lambda entry: (
            _secret_digest_id(
                "supernova.confirmatory-evaluation-order.v1",
                operator_seed,
                protocol_rules_sha256,
                str(entry["dispatch_id"]),
            ),
            str(entry["dispatch_id"]),
        ),
    )
    public_records: list[dict[str, object]] = []
    evaluation_index_by_id: dict[str, int] = {}
    for evaluation_index, entry in enumerate(ranked):
        evaluation_id = str(entry["evaluation_id"])
        evaluation_index_by_id[evaluation_id] = evaluation_index
        public_records.append(
            {
                "budget_attempt_index": entry["budget_attempt_index"],
                "evaluation_id": evaluation_id,
                "evaluation_index": evaluation_index,
                "family_id": entry["family_id"],
                "problem_id": entry["problem_id"],
                "registered_model_call_slots": 1,
                "retry_allowance": 0,
            }
        )
    for entry in operator_records:
        entry["evaluation_index"] = evaluation_index_by_id[str(entry["evaluation_id"])]
    return public_records, operator_records

def build_non_credit_draft(
    protocol: Mapping[str, Any],
    *,
    operator_seed: bytes,
) -> ConfirmatoryManifestBundle:
    """Expand sealed rules into a deterministic draft without opening dispatch."""

    rules = _validate_protocol(protocol)
    operator_seed = _operator_seed(operator_seed)
    protocol_rules_sha256 = protocol["sealed_rules_sha256"]
    selection = rules["benchmark_selection"]
    problem_ids = tuple(selection["report_split"]["problem_ids"])
    family_map = rules["family_design"]["report_problem_family_map"]
    family_by_problem = {
        item["problem_id"]: item["family_id"] for item in family_map
    }
    public_records, operator_records = _build_records(
        protocol_rules_sha256,
        problem_ids,
        family_by_problem,
        operator_seed,
    )
    operator_plan_sha256 = canonical_sha256(operator_records)
    operator_seed_commitment_sha256 = _digest_id(
        "supernova.confirmatory-operator-seed-commitment.v1",
        protocol_rules_sha256,
        operator_seed.hex(),
    )

    frozen = rules["frozen_authorities"]
    bindings = {
        "all_19520_dispatch_records_sha256": operator_plan_sha256,
        "benchmark_selection_sha256": canonical_sha256(selection),
        "cost_policy_git_blob_sha1": frozen["cost_policy"]["git_blob_sha1"],
        "cost_policy_sha256": canonical_sha256(frozen["cost_policy"]),
        "execution_authority_sha256": None,
        "family_map_sha256": canonical_sha256(family_map),
        "model_identity_sha256": None,
        "runtime_git_blob_sha1": frozen["runtime"]["git_blob_sha1"],
        "runtime_sha256": canonical_sha256(frozen["runtime"]),
        "schedule_sha256": canonical_sha256(rules["deterministic_schedule"]),
    }
    public_identity: dict[str, object] = {
        "bindings": bindings,
        "blinding": {
            "classification": "OPAQUE_IDS_AND_ORDER_BOUND_TO_OPERATOR_ONLY_256_BIT_SEED",
            "operator_plan_required_for_arm_join": True,
            "operator_seed_commitment_sha256": operator_seed_commitment_sha256,
            "public_records_contain_arm": False,
            "public_records_contain_dispatch_index": False,
        },
        "derivation": {
            "blinding_labels_and_evaluator_order": (
                "OPERATOR_ONLY_256_BIT_SEED_COMMITTED_BEFORE_DISPATCH"
            ),
            "scientific_dispatch_plan": "SEALED_PROTOCOL_RULES_ONLY",
        },
        "counts": {
            "attempts_per_problem_arm": len(ATTEMPTS),
            "dispatch_records": len(operator_records),
            "paired_cells": EXPECTED_CELLS,
            "report_problems": len(problem_ids),
        },
        "credit_status": NON_CREDIT_DRAFT,
        "dispatch_status": BLOCKED_NO_EXECUTION_AUTHORITY,
        "operator_plan_sha256": operator_plan_sha256,
        "protocol_id": protocol["protocol_id"],
        "protocol_rules_sha256": protocol_rules_sha256,
        "public_records": public_records,
        "purpose": NON_CREDIT_DRAFT,
        "retry_policy": {
            "registered_attempt_indices": list(ATTEMPTS),
            "retry_allowance_per_dispatch_record": 0,
            "unregistered_or_post_manifest_retry": "BLOCKED",
            "unused_capacity_reallocation": "BLOCKED",
        },
        "schema": CONFIRMATORY_MANIFEST_SCHEMA,
    }
    manifest_sha256 = canonical_sha256(public_identity)
    public_manifest = dict(public_identity)
    public_manifest["manifest_sha256"] = manifest_sha256
    operator_plan = {
        "entries": operator_records,
        "operator_seed_hex": operator_seed.hex(),
        "manifest_sha256": manifest_sha256,
        "operator_plan_sha256": operator_plan_sha256,
        "protocol_rules_sha256": protocol_rules_sha256,
        "schema": OPERATOR_PLAN_SCHEMA,
    }
    return ConfirmatoryManifestBundle(
        public_manifest=public_manifest,
        operator_plan=operator_plan,
    )


def build_confirmatory_manifest(
    protocol: Mapping[str, Any],
    *,
    operator_seed: bytes,
    execution_authority: Mapping[str, Any] | None = None,
) -> ConfirmatoryManifestBundle:
    """Build a draft now; production construction opens only with G1-121 phase two."""

    if execution_authority is not None:
        raise PermissionError(
            "production manifest construction is blocked until the G1-121 "
            "execution-authority validator is merged"
        )
    return build_non_credit_draft(protocol, operator_seed=operator_seed)


def validate_draft_bundle(
    public_manifest: Mapping[str, Any],
    operator_plan: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> None:
    """Reject every draft mutation by exact deterministic reconstruction."""

    if not isinstance(public_manifest, Mapping):
        raise ValueError("public_manifest must be an object")
    if not isinstance(operator_plan, Mapping):
        raise ValueError("operator_plan must be an object")
    seed_hex = operator_plan.get("operator_seed_hex")
    _sha256_hex(seed_hex, "operator_seed_hex")
    operator_seed = bytes.fromhex(seed_hex)
    expected_commitment = _digest_id(
        "supernova.confirmatory-operator-seed-commitment.v1",
        str(protocol.get("sealed_rules_sha256")),
        seed_hex,
    )
    blinding = public_manifest.get("blinding")
    if not isinstance(blinding, Mapping):
        raise ValueError("public manifest blinding must be an object")
    if blinding.get("operator_seed_commitment_sha256") != expected_commitment:
        raise ValueError("operator seed does not match the public commitment")
    expected = build_non_credit_draft(protocol, operator_seed=operator_seed)
    if canonical_sha256(public_manifest) != canonical_sha256(
        expected.public_manifest
    ):
        raise ValueError("public confirmatory manifest differs from the frozen draft")
    if canonical_sha256(operator_plan) != canonical_sha256(expected.operator_plan):
        raise ValueError("operator plan differs from the frozen draft")


def assert_dispatch_authorized(
    public_manifest: Mapping[str, Any],
    operator_plan: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> None:
    """Fail closed: a non-credit draft is never dispatch authority."""

    validate_draft_bundle(public_manifest, operator_plan, protocol)
    raise PermissionError(
        "confirmatory dispatch is BLOCKED_NO_EXECUTION_AUTHORITY; "
        "NON_CREDIT_DRAFT cannot authorize a model call"
    )


def paired_arm_counts(operator_plan: Mapping[str, Any]) -> Counter[tuple[str, int, str]]:
    """Return a small audit projection for tests and downstream admission."""

    entries = operator_plan.get("entries")
    if not isinstance(entries, list):
        raise ValueError("operator plan entries must be a list")
    counts: Counter[tuple[str, int, str]] = Counter()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("operator plan entry must be an object")
        counts[
            (
                _token(entry.get("problem_id"), "problem_id"),
                int(entry.get("attempt_index", entry.get("budget_attempt_index"))),
                _token(entry.get("arm"), "arm"),
            )
        ] += 1
    return counts
