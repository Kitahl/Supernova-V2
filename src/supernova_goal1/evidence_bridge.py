"""Fail-closed bridge from authenticated execution evidence to evaluator cells.

No public bridge input accepts solved, verifier_passed, or realized cost totals.
Those values are derived from an authority-backed closed join, signed host
execution receipts, authenticated Lean receipts, and reconciled cost events.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .confirmatory_manifest import (
    NON_CREDIT_DRAFT,
    canonical_sha256,
    validate_draft_bundle,
    validate_manifest_bundle,
)
from .contracts import Arm, CompleteCost
from .execution_authority import (
    PRODUCTION_CREDIT_STATUS,
    PRODUCTION_BRIDGE_RECEIPT_SCHEMA,
    PRODUCTION_RECEIPT_SCHEMA,
    ValidatedExecutionAuthority,
)
from .cost import (
    ArmCostTrace,
    CompleteCostReport,
    CostEventKind,
    ModelUsageBasis,
)
from .dispatch import (
    CompletionJoin,
    CompletionRecord,
    CompletionStatus,
    DispatchAuthority,
    DispatchEntry,
)
from .execution.common import FrozenProblemRequest

ATTEMPTS_PER_CELL = 16
_TYPED_ABSENCE = "NOT_INVOKED"
_LEDGER_FACTORY = object()
_RECORD_FACTORY = object()
_BUNDLE_FACTORY = object()


def _canonical_bytes(domain: str, value: object) -> bytes:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return domain.encode("ascii") + b"\0" + payload


def _digest(domain: str, value: object) -> str:
    return hashlib.sha256(_canonical_bytes(domain, value)).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    return value


def _sha256(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value


def _natural(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _arm(value: object) -> Arm:
    if type(value) is Arm:
        return value
    if type(value) is str:
        try:
            return Arm(value)
        except ValueError as exc:
            raise ValueError(f"unknown arm: {value!r}") from exc
    raise ValueError("arm must be an exact Arm or plain arm string")


def _snapshot_completion(value: object) -> CompletionRecord:
    if type(value) is not CompletionRecord:
        raise TypeError("completion must be an exact CompletionRecord")
    return CompletionRecord.from_mapping(value.to_mapping())


def _snapshot_cost(value: object, field: str) -> CompleteCost:
    if type(value) is not CompleteCost:
        raise TypeError(f"{field} must be an exact CompleteCost")
    return CompleteCost.from_mapping(
        {
            "model_calls": value.model_calls,
            "input_tokens": value.input_tokens,
            "output_tokens": value.output_tokens,
            "verifier_milliseconds": value.verifier_milliseconds,
            "orchestration_milliseconds": value.orchestration_milliseconds,
        },
        field,
    )


def _cost_mapping(value: CompleteCost) -> dict[str, int]:
    cost = _snapshot_cost(value, "cost")
    return {
        "model_calls": cost.model_calls,
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
        "verifier_milliseconds": cost.verifier_milliseconds,
        "orchestration_milliseconds": cost.orchestration_milliseconds,
    }


def _trace_mapping(trace: ArmCostTrace) -> dict[str, object]:
    if type(trace) is not ArmCostTrace:
        raise TypeError("cost trace must be an exact ArmCostTrace")
    trace = ArmCostTrace(
        trace.arm, trace.events, trace.expected_events, trace.accounting_complete
    )
    return {
        "accounting_complete": trace.accounting_complete,
        "arm": trace.arm.value,
        "events": [
            {
                "event_id": event.event_id,
                "input_tokens": event.input_tokens,
                "kind": event.kind.value,
                "milliseconds": event.milliseconds,
                "model_usage_basis": (
                    None
                    if event.model_usage_basis is None
                    else event.model_usage_basis.value
                ),
                "output_tokens": event.output_tokens,
            }
            for event in trace.events
        ],
        "expected_events": [
            {
                "event_id": event.event_id,
                "kind": event.kind.value,
                "model_usage_basis": (
                    None
                    if event.model_usage_basis is None
                    else event.model_usage_basis.value
                ),
            }
            for event in trace.expected_events
        ],
        "total": _cost_mapping(trace.total),
    }


def _protocol_dispatch_id(value: object, field: str) -> str:
    value = _token(value, field)
    if not value.startswith("dispatch-"):
        raise ValueError(f"{field} must use the dispatch- namespace")
    _sha256(value.removeprefix("dispatch-"), field)
    return value


@dataclass(frozen=True)
class ProtocolDispatchReceipt:
    issuer_id: str
    execution_authority_sha256: str
    run_id: str
    dispatch_id: str
    dispatch_entry_sha256: str
    request_sha256: str
    problem_id: str
    problem_identity: str
    arm: str
    attempt: int
    protocol_dispatch_id: str
    protocol_rules_sha256: str
    confirmatory_manifest_sha256: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _sha256(self.execution_authority_sha256, "execution_authority_sha256")
        _token(self.run_id, "run_id")
        _sha256(self.dispatch_id, "dispatch_id")
        _sha256(self.dispatch_entry_sha256, "dispatch_entry_sha256")
        _sha256(self.request_sha256, "request_sha256")
        _token(self.problem_id, "problem_id")
        _token(self.problem_identity, "problem_identity")
        _arm(self.arm)
        _natural(self.attempt, "attempt")
        _protocol_dispatch_id(self.protocol_dispatch_id, "protocol_dispatch_id")
        _sha256(self.protocol_rules_sha256, "protocol_rules_sha256")
        _sha256(
            self.confirmatory_manifest_sha256,
            "confirmatory_manifest_sha256",
        )
        _sha256(self.signature, "signature")

    def body(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "attempt": self.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_entry_sha256": self.dispatch_entry_sha256,
            "dispatch_id": self.dispatch_id,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "problem_id": self.problem_id,
            "problem_identity": self.problem_identity,
            "protocol_dispatch_id": self.protocol_dispatch_id,
            "protocol_rules_sha256": self.protocol_rules_sha256,
            "request_sha256": self.request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.protocol-dispatch-receipt.v1",
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.protocol-dispatch-receipt.v1",
            {"body": self.body(), "signature": self.signature},
        )


@dataclass(frozen=True)
class ContextIsolationReceipt:
    issuer_id: str
    run_id: str
    dispatch_id: str
    problem_id: str
    arm: str
    attempt: int
    request_sha256: str
    protocol_dispatch_id: str
    confirmatory_manifest_sha256: str
    mode: str
    status: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _token(self.run_id, "run_id")
        _sha256(self.dispatch_id, "dispatch_id")
        _token(self.problem_id, "problem_id")
        _arm(self.arm)
        _natural(self.attempt, "attempt")
        _sha256(self.request_sha256, "request_sha256")
        _protocol_dispatch_id(self.protocol_dispatch_id, "protocol_dispatch_id")
        _sha256(
            self.confirmatory_manifest_sha256,
            "confirmatory_manifest_sha256",
        )
        if self.mode != "NON_CREDIT_SIMULATED_EMPTY_CONTEXT":
            raise ValueError("unsupported context-isolation mode")
        if self.status != "OBSERVED_EMPTY":
            raise ValueError("context-isolation receipt did not observe an empty context")
        _sha256(self.signature, "signature")

    def body(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "attempt": self.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": self.dispatch_id,
            "issuer_id": self.issuer_id,
            "mode": self.mode,
            "problem_id": self.problem_id,
            "protocol_dispatch_id": self.protocol_dispatch_id,
            "request_sha256": self.request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.context-isolation-receipt.v1",
            "status": self.status,
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.context-isolation-receipt.v1",
            {"body": self.body(), "signature": self.signature},
        )


@dataclass(frozen=True)
class HermeticContextReceipt:
    issuer_id: str
    execution_authority_sha256: str
    confirmatory_manifest_sha256: str
    model_identity_sha256: str
    executor_artifact_sha256: str
    run_id: str
    protocol_dispatch_id: str
    dispatch_id: str
    problem_id: str
    arm: str
    attempt_index: int
    sequence: int
    instance_nonce: str
    clean_image_sha256: str
    initial_context_sha256: str
    request_artifact_sha256: str
    response_artifact_sha256: str
    opened_at: str
    closed_at: str
    network_policy: str
    persistent_writable_state: str
    teardown_observed: bool
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        for field in (
            "execution_authority_sha256",
            "confirmatory_manifest_sha256",
            "model_identity_sha256",
            "executor_artifact_sha256",
            "dispatch_id",
            "clean_image_sha256",
            "initial_context_sha256",
            "request_artifact_sha256",
            "response_artifact_sha256",
        ):
            _sha256(getattr(self, field), field)
        _token(self.run_id, "run_id")
        _protocol_dispatch_id(self.protocol_dispatch_id, "protocol_dispatch_id")
        _token(self.problem_id, "problem_id")
        _arm(self.arm)
        _natural(self.attempt_index, "attempt_index")
        _natural(self.sequence, "sequence")
        for field in ("instance_nonce", "opened_at", "closed_at", "signature"):
            _token(getattr(self, field), field)
        if self.network_policy != "NONE":
            raise ValueError("production context receipt must prove network NONE")
        if self.persistent_writable_state != "DISABLED":
            raise ValueError("production context receipt must prove no persistent state")
        if self.teardown_observed is not True:
            raise ValueError("production context receipt must prove teardown")

    def body(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "attempt_index": self.attempt_index,
            "clean_image_sha256": self.clean_image_sha256,
            "closed_at": self.closed_at,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": self.dispatch_id,
            "execution_authority_sha256": self.execution_authority_sha256,
            "executor_artifact_sha256": self.executor_artifact_sha256,
            "initial_context_sha256": self.initial_context_sha256,
            "instance_nonce": self.instance_nonce,
            "issuer_id": self.issuer_id,
            "model_identity_sha256": self.model_identity_sha256,
            "network_policy": self.network_policy,
            "opened_at": self.opened_at,
            "persistent_writable_state": self.persistent_writable_state,
            "problem_id": self.problem_id,
            "protocol_dispatch_id": self.protocol_dispatch_id,
            "request_artifact_sha256": self.request_artifact_sha256,
            "response_artifact_sha256": self.response_artifact_sha256,
            "run_id": self.run_id,
            "schema": PRODUCTION_RECEIPT_SCHEMA,
            "sequence": self.sequence,
            "teardown_observed": self.teardown_observed,
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            PRODUCTION_RECEIPT_SCHEMA,
            {"body": self.body(), "signature": self.signature},
        )


@dataclass(frozen=True)
class PredecessorReconciliationReceipt:
    issuer_id: str
    run_id: str
    dispatch_id: str
    problem_id: str
    arm: str
    attempt: int
    request_sha256: str
    protocol_dispatch_id: str
    confirmatory_manifest_sha256: str
    predecessor_policy: str
    eligible_predecessor_dispatch_ids: tuple[str, ...]
    selected_predecessor_dispatch_id: str
    status: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _token(self.run_id, "run_id")
        _sha256(self.dispatch_id, "dispatch_id")
        _token(self.problem_id, "problem_id")
        _arm(self.arm)
        _natural(self.attempt, "attempt")
        _sha256(self.request_sha256, "request_sha256")
        _protocol_dispatch_id(self.protocol_dispatch_id, "protocol_dispatch_id")
        _sha256(
            self.confirmatory_manifest_sha256,
            "confirmatory_manifest_sha256",
        )
        _token(self.predecessor_policy, "predecessor_policy")
        if type(self.eligible_predecessor_dispatch_ids) is not tuple:
            raise TypeError("eligible predecessor ids must be an exact tuple")
        for value in self.eligible_predecessor_dispatch_ids:
            _protocol_dispatch_id(value, "eligible_predecessor_dispatch_ids[]")
        if len(self.eligible_predecessor_dispatch_ids) != len(
            set(self.eligible_predecessor_dispatch_ids)
        ):
            raise ValueError("eligible predecessor ids contain duplicates")
        if self.selected_predecessor_dispatch_id != _TYPED_ABSENCE:
            _protocol_dispatch_id(
                self.selected_predecessor_dispatch_id,
                "selected_predecessor_dispatch_id",
            )
            if (
                self.selected_predecessor_dispatch_id
                not in self.eligible_predecessor_dispatch_ids
            ):
                raise ValueError("selected predecessor is not in the frozen eligible set")
        expected_status = (
            "NOT_APPLICABLE"
            if not self.eligible_predecessor_dispatch_ids
            else "RECONCILED"
        )
        if self.status != expected_status:
            raise ValueError("predecessor reconciliation status is inconsistent")
        _sha256(self.signature, "signature")

    def body(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "attempt": self.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": self.dispatch_id,
            "eligible_predecessor_dispatch_ids": list(
                self.eligible_predecessor_dispatch_ids
            ),
            "issuer_id": self.issuer_id,
            "predecessor_policy": self.predecessor_policy,
            "problem_id": self.problem_id,
            "protocol_dispatch_id": self.protocol_dispatch_id,
            "request_sha256": self.request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.predecessor-reconciliation-receipt.v1",
            "selected_predecessor_dispatch_id": (
                self.selected_predecessor_dispatch_id
            ),
            "status": self.status,
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.predecessor-reconciliation-receipt.v1",
            {"body": self.body(), "signature": self.signature},
        )


def _completion_body(
    completion: CompletionRecord,
    *,
    issuer_id: str,
    execution_authority_sha256: str,
    protocol_dispatch_id: str,
    confirmatory_manifest_sha256: str,
    protocol_binding_receipt_sha256: str,
    context_isolation_receipt_sha256: str,
    predecessor_reconciliation_sha256: str,
    orchestration_milliseconds: int,
) -> dict[str, object]:
    completion = _snapshot_completion(completion)
    payload = completion.payload
    verifier = payload.verifier_receipt
    return {
        "completion_record_sha256": completion.record_sha256,
        "completion_status": completion.status.value,
        "context_isolation_receipt_sha256": _sha256(
            context_isolation_receipt_sha256,
            "context_isolation_receipt_sha256",
        ),
        "confirmatory_manifest_sha256": _sha256(
            confirmatory_manifest_sha256,
            "confirmatory_manifest_sha256",
        ),
        "dispatch_id": completion.dispatch_id,
        "execution_authority_sha256": _sha256(
            execution_authority_sha256, "execution_authority_sha256"
        ),
        "issuer_id": _token(issuer_id, "issuer_id"),
        "protocol_binding_receipt_sha256": _sha256(
            protocol_binding_receipt_sha256,
            "protocol_binding_receipt_sha256",
        ),
        "protocol_dispatch_id": _protocol_dispatch_id(
            protocol_dispatch_id, "protocol_dispatch_id"
        ),
        "orchestration_milliseconds": _natural(
            orchestration_milliseconds, "orchestration_milliseconds"
        ),
        "predecessor_reconciliation_sha256": _sha256(
            predecessor_reconciliation_sha256,
            "predecessor_reconciliation_sha256",
        ),
        "request_sha256": payload.request.frozen_request_sha256,
        "request_utf8_bytes": payload.request.request_artifact.byte_length,
        "response_artifact_id": payload.attempt_result.response_artifact.artifact_id,
        "response_utf8_bytes": payload.attempt_result.response_artifact.byte_length,
        "run_id": completion.run_id,
        "schema": "supernova.execution-ledger-receipt.v3",
        "verifier_milliseconds": 0 if verifier is None else verifier.elapsed_milliseconds,
        "verifier_receipt_sha256": (
            _TYPED_ABSENCE if verifier is None else verifier.receipt_sha256
        ),
    }


@dataclass(frozen=True)
class ExecutionLedgerReceipt:
    issuer_id: str
    execution_authority_sha256: str
    protocol_dispatch_id: str
    confirmatory_manifest_sha256: str
    run_id: str
    dispatch_id: str
    completion_record_sha256: str
    request_sha256: str
    response_artifact_id: str
    completion_status: str
    verifier_receipt_sha256: str
    request_utf8_bytes: int
    response_utf8_bytes: int
    verifier_milliseconds: int
    orchestration_milliseconds: int
    protocol_binding_receipt_sha256: str
    context_isolation_receipt_sha256: str
    predecessor_reconciliation_sha256: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _sha256(self.execution_authority_sha256, "execution_authority_sha256")
        _protocol_dispatch_id(self.protocol_dispatch_id, "protocol_dispatch_id")
        _sha256(
            self.confirmatory_manifest_sha256,
            "confirmatory_manifest_sha256",
        )
        _token(self.run_id, "run_id")
        for field in (
            "dispatch_id",
            "completion_record_sha256",
            "request_sha256",
            "protocol_binding_receipt_sha256",
            "context_isolation_receipt_sha256",
            "predecessor_reconciliation_sha256",
            "signature",
        ):
            _sha256(getattr(self, field), field)
        _token(self.response_artifact_id, "response_artifact_id")
        try:
            CompletionStatus(self.completion_status)
        except (TypeError, ValueError) as exc:
            raise ValueError("completion_status is invalid") from exc
        if self.verifier_receipt_sha256 != _TYPED_ABSENCE:
            _sha256(self.verifier_receipt_sha256, "verifier_receipt_sha256")
        for field in (
            "request_utf8_bytes",
            "response_utf8_bytes",
            "verifier_milliseconds",
            "orchestration_milliseconds",
        ):
            _natural(getattr(self, field), field)

    @classmethod
    def _issue(
        cls,
        completion: CompletionRecord,
        *,
        issuer_id: str,
        execution_authority_sha256: str,
        protocol_dispatch_id: str,
        confirmatory_manifest_sha256: str,
        protocol_binding_receipt_sha256: str,
        context_isolation_receipt_sha256: str,
        predecessor_reconciliation_sha256: str,
        orchestration_milliseconds: int,
        secret: bytes,
        _factory: object,
    ) -> "ExecutionLedgerReceipt":
        if _factory is not _LEDGER_FACTORY:
            raise TypeError("execution receipts are issued only by ExecutionLedgerAuthority")
        body = _completion_body(
            completion,
            issuer_id=issuer_id,
            execution_authority_sha256=execution_authority_sha256,
            protocol_dispatch_id=protocol_dispatch_id,
            confirmatory_manifest_sha256=confirmatory_manifest_sha256,
            protocol_binding_receipt_sha256=protocol_binding_receipt_sha256,
            context_isolation_receipt_sha256=context_isolation_receipt_sha256,
            predecessor_reconciliation_sha256=predecessor_reconciliation_sha256,
            orchestration_milliseconds=orchestration_milliseconds,
        )
        signature = hmac.new(
            secret,
            _canonical_bytes("supernova.execution-ledger.signature.v3", body),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            issuer_id=str(body["issuer_id"]),
            execution_authority_sha256=str(body["execution_authority_sha256"]),
            protocol_dispatch_id=str(body["protocol_dispatch_id"]),
            confirmatory_manifest_sha256=str(
                body["confirmatory_manifest_sha256"]
            ),
            run_id=str(body["run_id"]),
            dispatch_id=str(body["dispatch_id"]),
            completion_record_sha256=str(body["completion_record_sha256"]),
            request_sha256=str(body["request_sha256"]),
            response_artifact_id=str(body["response_artifact_id"]),
            completion_status=str(body["completion_status"]),
            verifier_receipt_sha256=str(body["verifier_receipt_sha256"]),
            request_utf8_bytes=int(body["request_utf8_bytes"]),
            response_utf8_bytes=int(body["response_utf8_bytes"]),
            verifier_milliseconds=int(body["verifier_milliseconds"]),
            orchestration_milliseconds=int(body["orchestration_milliseconds"]),
            protocol_binding_receipt_sha256=str(
                body["protocol_binding_receipt_sha256"]
            ),
            context_isolation_receipt_sha256=str(
                body["context_isolation_receipt_sha256"]
            ),
            predecessor_reconciliation_sha256=str(
                body["predecessor_reconciliation_sha256"]
            ),
            signature=signature,
        )

    def body(self) -> dict[str, object]:
        return {
            "completion_record_sha256": self.completion_record_sha256,
            "completion_status": self.completion_status,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "context_isolation_receipt_sha256": (
                self.context_isolation_receipt_sha256
            ),
            "dispatch_id": self.dispatch_id,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "protocol_dispatch_id": self.protocol_dispatch_id,
            "orchestration_milliseconds": self.orchestration_milliseconds,
            "predecessor_reconciliation_sha256": (
                self.predecessor_reconciliation_sha256
            ),
            "protocol_binding_receipt_sha256": (
                self.protocol_binding_receipt_sha256
            ),
            "request_sha256": self.request_sha256,
            "request_utf8_bytes": self.request_utf8_bytes,
            "response_artifact_id": self.response_artifact_id,
            "response_utf8_bytes": self.response_utf8_bytes,
            "run_id": self.run_id,
            "schema": "supernova.execution-ledger-receipt.v3",
            "verifier_milliseconds": self.verifier_milliseconds,
            "verifier_receipt_sha256": self.verifier_receipt_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.execution-ledger.receipt.v3",
            {"body": self.body(), "signature": self.signature},
        )


class ExecutionLedgerAuthority:
    """Durable host-owned receipt authority isolated from model-facing workers."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        run_id: str,
        issuer_id: str,
        execution_authority_sha256: str,
        secret: bytes,
        protocol: Mapping[str, object],
        public_manifest: Mapping[str, object],
        operator_plan: Mapping[str, object],
        execution_authority: ValidatedExecutionAuthority | None = None,
    ) -> None:
        path_text = os.fspath(database_path)
        if not path_text or path_text == ":memory:" or path_text.startswith("file:"):
            raise ValueError("database_path must name an absolute durable SQLite file")
        path = Path(path_text)
        if not path.is_absolute():
            raise ValueError("database_path must be absolute")
        if type(secret) is not bytes or len(secret) < 32:
            raise ValueError("execution ledger secret must be at least 32 bytes")
        self.database_path = str(path.resolve(strict=False))
        self.run_id = _token(run_id, "run_id")
        self.issuer_id = _token(issuer_id, "issuer_id")
        self.execution_authority_sha256 = _sha256(
            execution_authority_sha256, "execution_authority_sha256"
        )
        for value, field in (
            (protocol, "protocol"),
            (public_manifest, "public_manifest"),
            (operator_plan, "operator_plan"),
        ):
            if type(value) is not dict:
                raise TypeError(f"{field} must be an exact dict")
        if execution_authority is None:
            validate_draft_bundle(public_manifest, operator_plan, protocol)
        else:
            if type(execution_authority) is not ValidatedExecutionAuthority:
                raise TypeError("execution_authority must be a validator-issued capability")
            validate_manifest_bundle(
                public_manifest,
                operator_plan,
                protocol,
                execution_authority=execution_authority,
            )
            if execution_authority_sha256 != execution_authority.authority_sha256:
                raise ValueError("caller execution authority digest differs from capability")
            if issuer_id != execution_authority.issuer_id:
                raise ValueError("caller issuer differs from validated execution authority")
        self.production_authority = execution_authority
        self.protocol_rules_sha256 = _sha256(
            protocol["sealed_rules_sha256"], "sealed_rules_sha256"
        )
        self.confirmatory_manifest_sha256 = _sha256(
            public_manifest["manifest_sha256"], "manifest_sha256"
        )
        self.protocol_id = _token(protocol["protocol_id"], "protocol_id")
        rules = protocol["sealed_rules"]
        self.benchmark_root_sha256 = _sha256(
            rules["benchmark_selection"]["benchmark_root_sha256"],
            "benchmark_root_sha256",
        )
        bindings = public_manifest["bindings"]
        self.runtime_sha256 = _sha256(bindings["runtime_sha256"], "runtime_sha256")
        self.cost_policy_sha256 = _sha256(
            bindings["cost_policy_sha256"], "cost_policy_sha256"
        )
        self.__plan_slots = {
            (entry["problem_id"], entry["arm"], entry["budget_attempt_index"]): dict(entry)
            for entry in operator_plan["entries"]
        }
        if len(self.__plan_slots) != len(operator_plan["entries"]):
            raise ValueError("operator plan contains duplicate scientific slots")
        self.__secret = bytes(secret)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.execute(
                """CREATE TABLE IF NOT EXISTS protocol_dispatch_receipts (
                    run_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (run_id, dispatch_id)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS execution_receipts (
                    run_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (run_id, dispatch_id)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS hermetic_instance_receipts (
                    instance_nonce TEXT NOT NULL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    UNIQUE (run_id, sequence)
                )"""
            )
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=30)

    def _slot_for_request(
        self, request: FrozenProblemRequest
    ) -> dict[str, object]:
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())
        slot = self.__plan_slots.get(
            (request.problem.native_id, request.arm.value, request.attempt)
        )
        if slot is None:
            raise ValueError("completion is absent from the frozen operator plan")
        if request.run_id != self.run_id:
            raise ValueError("completion run_id does not match execution ledger")
        if request.experiment_id != self.protocol_id:
            raise ValueError("completion experiment_id differs from sealed protocol")
        if request.benchmark_root_sha256 != self.benchmark_root_sha256:
            raise ValueError("completion benchmark root differs from sealed protocol")
        if request.runtime_sha256 != self.runtime_sha256:
            raise ValueError("completion runtime differs from confirmatory manifest")
        if request.budget_sha256 != self.cost_policy_sha256:
            raise ValueError("completion budget differs from confirmatory cost policy")
        if request.protocol_dispatch_id != slot["dispatch_id"]:
            raise ValueError(
                "frozen request protocol dispatch binding differs from operator plan"
            )
        if (
            request.confirmatory_manifest_sha256
            != self.confirmatory_manifest_sha256
        ):
            raise ValueError(
                "frozen request confirmatory manifest binding differs from ledger"
            )
        return slot

    def _slot_for_completion(
        self, completion: CompletionRecord
    ) -> dict[str, object]:
        completion = _snapshot_completion(completion)
        return self._slot_for_request(completion.payload.request)

    def _protocol_binding_body(
        self,
        entry: DispatchEntry,
        request: FrozenProblemRequest,
    ) -> dict[str, object]:
        if type(entry) is not DispatchEntry:
            raise TypeError("entry must be an exact DispatchEntry")
        if type(request) is not FrozenProblemRequest:
            raise TypeError("request must be an exact FrozenProblemRequest")
        request = FrozenProblemRequest.from_mapping(request.to_mapping())
        slot = self._slot_for_request(request)
        if (
            entry.run_id != request.run_id
            or entry.problem_id != request.problem_id
            or entry.arm is not request.arm
            or entry.attempt_index != request.attempt
            or entry.request_sha256 != request.frozen_request_sha256
        ):
            raise ValueError(
                "actual dispatch registration does not match frozen request"
            )
        return {
            "arm": request.arm.value,
            "attempt": request.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_entry_sha256": entry.entry_sha256,
            "dispatch_id": entry.dispatch_id,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "problem_id": request.problem.native_id,
            "problem_identity": request.problem_id,
            "protocol_dispatch_id": slot["dispatch_id"],
            "protocol_rules_sha256": self.protocol_rules_sha256,
            "request_sha256": request.frozen_request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.protocol-dispatch-receipt.v1",
        }

    def _register_dispatch(
        self,
        entry: DispatchEntry,
        request: FrozenProblemRequest,
    ) -> ProtocolDispatchReceipt:
        body = self._protocol_binding_body(entry, request)
        signature = hmac.new(
            self.__secret,
            _canonical_bytes("supernova.protocol-dispatch.signature.v1", body),
            hashlib.sha256,
        ).hexdigest()
        receipt = ProtocolDispatchReceipt(
            issuer_id=str(body["issuer_id"]),
            execution_authority_sha256=str(
                body["execution_authority_sha256"]
            ),
            run_id=str(body["run_id"]),
            dispatch_id=str(body["dispatch_id"]),
            dispatch_entry_sha256=str(body["dispatch_entry_sha256"]),
            request_sha256=str(body["request_sha256"]),
            problem_id=str(body["problem_id"]),
            problem_identity=str(body["problem_identity"]),
            arm=str(body["arm"]),
            attempt=int(body["attempt"]),
            protocol_dispatch_id=str(body["protocol_dispatch_id"]),
            protocol_rules_sha256=str(body["protocol_rules_sha256"]),
            confirmatory_manifest_sha256=str(
                body["confirmatory_manifest_sha256"]
            ),
            signature=signature,
        )
        encoded = json.dumps(
            {
                **receipt.body(),
                "receipt_sha256": receipt.receipt_sha256,
                "signature": receipt.signature,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        con = self._connect()
        try:
            try:
                con.execute(
                    "INSERT INTO protocol_dispatch_receipts "
                    "(run_id,dispatch_id,receipt_json,receipt_sha256) "
                    "VALUES(?,?,?,?)",
                    (
                        self.run_id,
                        receipt.dispatch_id,
                        encoded,
                        receipt.receipt_sha256,
                    ),
                )
                con.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "protocol dispatch binding already exists; replay rejected"
                ) from exc
        finally:
            con.close()
        return receipt

    def _read_protocol_bindings(self) -> dict[str, ProtocolDispatchReceipt]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT dispatch_id,receipt_json,receipt_sha256 "
                "FROM protocol_dispatch_receipts WHERE run_id=? "
                "ORDER BY dispatch_id",
                (self.run_id,),
            ).fetchall()
        finally:
            con.close()
        receipts: dict[str, ProtocolDispatchReceipt] = {}
        for dispatch_id, encoded, stored_sha in rows:
            raw = json.loads(encoded)
            expected = {
                "arm",
                "attempt",
                "confirmatory_manifest_sha256",
                "dispatch_entry_sha256",
                "dispatch_id",
                "execution_authority_sha256",
                "issuer_id",
                "problem_id",
                "problem_identity",
                "protocol_dispatch_id",
                "protocol_rules_sha256",
                "receipt_sha256",
                "request_sha256",
                "run_id",
                "schema",
                "signature",
            }
            if not isinstance(raw, dict) or set(raw) != expected:
                raise ValueError(
                    "persisted protocol dispatch receipt fields are not canonical"
                )
            if raw["schema"] != "supernova.protocol-dispatch-receipt.v1":
                raise ValueError("persisted protocol dispatch schema changed")
            receipt = ProtocolDispatchReceipt(
                issuer_id=raw["issuer_id"],
                execution_authority_sha256=raw[
                    "execution_authority_sha256"
                ],
                run_id=raw["run_id"],
                dispatch_id=raw["dispatch_id"],
                dispatch_entry_sha256=raw["dispatch_entry_sha256"],
                request_sha256=raw["request_sha256"],
                problem_id=raw["problem_id"],
                problem_identity=raw["problem_identity"],
                arm=raw["arm"],
                attempt=raw["attempt"],
                protocol_dispatch_id=raw["protocol_dispatch_id"],
                protocol_rules_sha256=raw["protocol_rules_sha256"],
                confirmatory_manifest_sha256=raw[
                    "confirmatory_manifest_sha256"
                ],
                signature=raw["signature"],
            )
            expected_signature = hmac.new(
                self.__secret,
                _canonical_bytes(
                    "supernova.protocol-dispatch.signature.v1",
                    receipt.body(),
                ),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(receipt.signature, expected_signature):
                raise ValueError("protocol dispatch receipt signature is invalid")
            if (
                dispatch_id != receipt.dispatch_id
                or raw["receipt_sha256"] != receipt.receipt_sha256
                or stored_sha != receipt.receipt_sha256
            ):
                raise ValueError("protocol dispatch receipt digest is invalid")
            if dispatch_id in receipts:
                raise ValueError("replayed protocol dispatch receipt")
            receipts[dispatch_id] = receipt
        return receipts

    def _read_protocol_binding(
        self, dispatch_id: str
    ) -> ProtocolDispatchReceipt | None:
        dispatch_id = _sha256(dispatch_id, "dispatch_id")
        con = self._connect()
        try:
            row = con.execute(
                "SELECT receipt_json,receipt_sha256 "
                "FROM protocol_dispatch_receipts "
                "WHERE run_id=? AND dispatch_id=?",
                (self.run_id, dispatch_id),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        encoded, stored_sha = row
        raw = json.loads(encoded)
        expected = {
            "arm",
            "attempt",
            "confirmatory_manifest_sha256",
            "dispatch_entry_sha256",
            "dispatch_id",
            "execution_authority_sha256",
            "issuer_id",
            "problem_id",
            "problem_identity",
            "protocol_dispatch_id",
            "protocol_rules_sha256",
            "receipt_sha256",
            "request_sha256",
            "run_id",
            "schema",
            "signature",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError(
                "persisted protocol dispatch receipt fields are not canonical"
            )
        if raw["schema"] != "supernova.protocol-dispatch-receipt.v1":
            raise ValueError("persisted protocol dispatch schema changed")
        receipt = ProtocolDispatchReceipt(
            issuer_id=raw["issuer_id"],
            execution_authority_sha256=raw["execution_authority_sha256"],
            run_id=raw["run_id"],
            dispatch_id=raw["dispatch_id"],
            dispatch_entry_sha256=raw["dispatch_entry_sha256"],
            request_sha256=raw["request_sha256"],
            problem_id=raw["problem_id"],
            problem_identity=raw["problem_identity"],
            arm=raw["arm"],
            attempt=raw["attempt"],
            protocol_dispatch_id=raw["protocol_dispatch_id"],
            protocol_rules_sha256=raw["protocol_rules_sha256"],
            confirmatory_manifest_sha256=raw[
                "confirmatory_manifest_sha256"
            ],
            signature=raw["signature"],
        )
        expected_signature = hmac.new(
            self.__secret,
            _canonical_bytes(
                "supernova.protocol-dispatch.signature.v1",
                receipt.body(),
            ),
            hashlib.sha256,
        ).hexdigest()
        if (
            not hmac.compare_digest(receipt.signature, expected_signature)
            or receipt.dispatch_id != dispatch_id
            or raw["receipt_sha256"] != receipt.receipt_sha256
            or stored_sha != receipt.receipt_sha256
        ):
            raise ValueError(
                "persisted protocol dispatch receipt authentication failed"
            )
        return receipt

    def _verify_protocol_binding(
        self,
        entry: DispatchEntry,
        request: FrozenProblemRequest,
        receipt: ProtocolDispatchReceipt,
    ) -> ProtocolDispatchReceipt:
        if type(receipt) is not ProtocolDispatchReceipt:
            raise TypeError("protocol dispatch receipt has wrong type")
        expected_body = self._protocol_binding_body(entry, request)
        expected_signature = hmac.new(
            self.__secret,
            _canonical_bytes(
                "supernova.protocol-dispatch.signature.v1",
                expected_body,
            ),
            hashlib.sha256,
        ).hexdigest()
        if receipt.body() != expected_body or not hmac.compare_digest(
            receipt.signature, expected_signature
        ):
            raise ValueError(
                "protocol dispatch receipt does not bind the actual registration"
            )
        return receipt

    def _context_body(
        self, completion: CompletionRecord
    ) -> dict[str, object]:
        completion = _snapshot_completion(completion)
        request = completion.payload.request
        slot = self._slot_for_completion(completion)
        return {
            "arm": request.arm.value,
            "attempt": request.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": completion.dispatch_id,
            "issuer_id": self.issuer_id,
            "mode": "NON_CREDIT_SIMULATED_EMPTY_CONTEXT",
            "problem_id": request.problem.native_id,
            "protocol_dispatch_id": slot["dispatch_id"],
            "request_sha256": request.frozen_request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.context-isolation-receipt.v1",
            "status": "OBSERVED_EMPTY",
        }

    def _issue_context_isolation_receipt(
        self, completion: CompletionRecord
    ) -> ContextIsolationReceipt:
        if self.production_authority is not None:
            raise PermissionError(
                "production context receipts are issued only by the hermetic supervisor"
            )
        body = self._context_body(completion)
        signature = hmac.new(
            self.__secret,
            _canonical_bytes("supernova.context-isolation.signature.v1", body),
            hashlib.sha256,
        ).hexdigest()
        return ContextIsolationReceipt(
            issuer_id=str(body["issuer_id"]),
            run_id=str(body["run_id"]),
            dispatch_id=str(body["dispatch_id"]),
            problem_id=str(body["problem_id"]),
            arm=str(body["arm"]),
            attempt=int(body["attempt"]),
            request_sha256=str(body["request_sha256"]),
            protocol_dispatch_id=str(body["protocol_dispatch_id"]),
            confirmatory_manifest_sha256=str(
                body["confirmatory_manifest_sha256"]
            ),
            mode=str(body["mode"]),
            status=str(body["status"]),
            signature=signature,
        )

    def _predecessor_body(
        self, completion: CompletionRecord
    ) -> dict[str, object]:
        completion = _snapshot_completion(completion)
        request = completion.payload.request
        slot = self._slot_for_completion(completion)
        eligible = tuple(slot["eligible_predecessor_dispatch_ids"])
        selected = slot["selected_predecessor_dispatch_id"]
        return {
            "arm": request.arm.value,
            "attempt": request.attempt,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": completion.dispatch_id,
            "eligible_predecessor_dispatch_ids": list(eligible),
            "issuer_id": self.issuer_id,
            "predecessor_policy": slot["predecessor_policy"],
            "problem_id": request.problem.native_id,
            "protocol_dispatch_id": slot["dispatch_id"],
            "request_sha256": request.frozen_request_sha256,
            "run_id": self.run_id,
            "schema": "supernova.predecessor-reconciliation-receipt.v1",
            "selected_predecessor_dispatch_id": (
                _TYPED_ABSENCE if selected is None else selected
            ),
            "status": "NOT_APPLICABLE" if not eligible else "RECONCILED",
        }

    def _issue_predecessor_reconciliation_receipt(
        self, completion: CompletionRecord
    ) -> PredecessorReconciliationReceipt:
        body = self._predecessor_body(completion)
        signature = hmac.new(
            self.__secret,
            _canonical_bytes(
                "supernova.predecessor-reconciliation.signature.v1", body
            ),
            hashlib.sha256,
        ).hexdigest()
        return PredecessorReconciliationReceipt(
            issuer_id=str(body["issuer_id"]),
            run_id=str(body["run_id"]),
            dispatch_id=str(body["dispatch_id"]),
            problem_id=str(body["problem_id"]),
            arm=str(body["arm"]),
            attempt=int(body["attempt"]),
            request_sha256=str(body["request_sha256"]),
            protocol_dispatch_id=str(body["protocol_dispatch_id"]),
            confirmatory_manifest_sha256=str(
                body["confirmatory_manifest_sha256"]
            ),
            predecessor_policy=str(body["predecessor_policy"]),
            eligible_predecessor_dispatch_ids=tuple(
                body["eligible_predecessor_dispatch_ids"]
            ),
            selected_predecessor_dispatch_id=str(
                body["selected_predecessor_dispatch_id"]
            ),
            status=str(body["status"]),
            signature=signature,
        )

    def _verify_context_receipt(
        self,
        completion: CompletionRecord,
        receipt: ContextIsolationReceipt | HermeticContextReceipt,
    ) -> ContextIsolationReceipt | HermeticContextReceipt:
        if self.production_authority is None:
            if type(receipt) is not ContextIsolationReceipt:
                raise TypeError(
                    "draft context receipt must be an exact ContextIsolationReceipt"
                )
            expected = self._issue_context_isolation_receipt(completion)
            if receipt.body() != expected.body() or not hmac.compare_digest(
                receipt.signature, expected.signature
            ):
                raise ValueError(
                    "context-isolation receipt is not authenticated for this dispatch"
                )
            return receipt

        if type(receipt) is not HermeticContextReceipt:
            raise TypeError(
                "production context receipt must be an exact HermeticContextReceipt"
            )
        authority = self.production_authority
        slot = self._slot_for_completion(completion)
        request = completion.payload.request
        response = completion.payload.attempt_result.response_artifact
        expected = {
            "arm": request.arm.value,
            "attempt_index": request.attempt,
            "clean_image_sha256": authority.clean_image_sha256,
            "closed_at": receipt.closed_at,
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "dispatch_id": completion.dispatch_id,
            "execution_authority_sha256": authority.authority_sha256,
            "executor_artifact_sha256": authority.executor_artifact_sha256,
            "initial_context_sha256": hashlib.sha256(b"").hexdigest(),
            "instance_nonce": receipt.instance_nonce,
            "issuer_id": authority.issuer_id,
            "model_identity_sha256": authority.model_identity_sha256,
            "network_policy": "NONE",
            "opened_at": receipt.opened_at,
            "persistent_writable_state": "DISABLED",
            "problem_id": request.problem.native_id,
            "protocol_dispatch_id": slot["dispatch_id"],
            "request_artifact_sha256": request.frozen_request_sha256,
            "response_artifact_sha256": response.sha256_hex,
            "run_id": self.run_id,
            "schema": PRODUCTION_RECEIPT_SCHEMA,
            "sequence": slot["dispatch_index"],
            "teardown_observed": True,
        }
        if receipt.body() != expected:
            raise ValueError("production context receipt does not bind actual execution")
        authority.verify_receipt_signature(
            receipt.signature,
            domain=PRODUCTION_RECEIPT_SCHEMA,
            body=expected,
        )
        return receipt

    def _verify_predecessor_receipt(
        self,
        completion: CompletionRecord,
        receipt: PredecessorReconciliationReceipt,
    ) -> PredecessorReconciliationReceipt:
        if type(receipt) is not PredecessorReconciliationReceipt:
            raise TypeError(
                "predecessor_reconciliation_receipt must be an exact "
                "PredecessorReconciliationReceipt"
            )
        expected = self._issue_predecessor_reconciliation_receipt(completion)
        if receipt.body() != expected.body() or not hmac.compare_digest(
            receipt.signature, expected.signature
        ):
            raise ValueError(
                "predecessor receipt does not match the frozen predecessor graph"
            )
        return receipt

    def _record_completion(
        self,
        completion: CompletionRecord,
        *,
        context_isolation_receipt: ContextIsolationReceipt | HermeticContextReceipt,
        predecessor_reconciliation_receipt: PredecessorReconciliationReceipt,
        orchestration_milliseconds: int,
    ) -> ExecutionLedgerReceipt:
        """Issue once, after the trusted execution adapter returns."""

        completion = _snapshot_completion(completion)
        slot = self._slot_for_completion(completion)
        binding = self._read_protocol_binding(completion.dispatch_id)
        if binding is None:
            raise ValueError(
                "completion has no pre-dispatch protocol binding receipt"
            )
        if (
            binding.request_sha256
            != completion.payload.request.frozen_request_sha256
            or binding.dispatch_entry_sha256 != completion.entry_sha256
        ):
            raise ValueError(
                "pre-dispatch protocol binding does not match completion"
            )
        context_receipt = self._verify_context_receipt(
            completion, context_isolation_receipt
        )
        predecessor_receipt = self._verify_predecessor_receipt(
            completion, predecessor_reconciliation_receipt
        )
        receipt = ExecutionLedgerReceipt._issue(
            completion,
            issuer_id=self.issuer_id,
            execution_authority_sha256=self.execution_authority_sha256,
            protocol_dispatch_id=slot["dispatch_id"],
            confirmatory_manifest_sha256=self.confirmatory_manifest_sha256,
            protocol_binding_receipt_sha256=binding.receipt_sha256,
            context_isolation_receipt_sha256=context_receipt.receipt_sha256,
            predecessor_reconciliation_sha256=predecessor_receipt.receipt_sha256,
            orchestration_milliseconds=orchestration_milliseconds,
            secret=self.__secret,
            _factory=_LEDGER_FACTORY,
        )
        encoded = json.dumps(
            {
                **receipt.body(),
                "receipt_sha256": receipt.receipt_sha256,
                "signature": receipt.signature,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        con = self._connect()
        try:
            try:
                if type(context_receipt) is HermeticContextReceipt:
                    con.execute(
                        "INSERT INTO hermetic_instance_receipts "
                        "(instance_nonce,run_id,sequence,receipt_sha256) VALUES(?,?,?,?)",
                        (
                            context_receipt.instance_nonce,
                            self.run_id,
                            context_receipt.sequence,
                            context_receipt.receipt_sha256,
                        ),
                    )
                con.execute(
                    "INSERT INTO execution_receipts "
                    "(run_id,dispatch_id,receipt_json,receipt_sha256) VALUES(?,?,?,?)",
                    (
                        self.run_id,
                        completion.dispatch_id,
                        encoded,
                        receipt.receipt_sha256,
                    ),
                )
                con.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "execution, sequence, or hermetic instance replay rejected"
                ) from exc
        finally:
            con.close()
        return receipt

    def _read_receipts(self) -> dict[str, ExecutionLedgerReceipt]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT dispatch_id,receipt_json,receipt_sha256 "
                "FROM execution_receipts WHERE run_id=? ORDER BY dispatch_id",
                (self.run_id,),
            ).fetchall()
        finally:
            con.close()
        receipts: dict[str, ExecutionLedgerReceipt] = {}
        for dispatch_id, encoded, stored_sha in rows:
            raw = json.loads(encoded)
            expected = {
                "completion_record_sha256",
                "completion_status",
                "confirmatory_manifest_sha256",
                "context_isolation_receipt_sha256",
                "dispatch_id",
                "execution_authority_sha256",
                "issuer_id",
                "orchestration_milliseconds",
                "predecessor_reconciliation_sha256",
                "protocol_binding_receipt_sha256",
                "protocol_dispatch_id",
                "receipt_sha256",
                "request_sha256",
                "request_utf8_bytes",
                "response_artifact_id",
                "response_utf8_bytes",
                "run_id",
                "schema",
                "signature",
                "verifier_milliseconds",
                "verifier_receipt_sha256",
            }
            if not isinstance(raw, dict) or set(raw) != expected:
                raise ValueError("persisted execution receipt fields are not canonical")
            if raw["schema"] != "supernova.execution-ledger-receipt.v3":
                raise ValueError("persisted execution receipt schema changed")
            receipt = ExecutionLedgerReceipt(
                issuer_id=raw["issuer_id"],
                execution_authority_sha256=raw["execution_authority_sha256"],
                protocol_dispatch_id=raw["protocol_dispatch_id"],
                confirmatory_manifest_sha256=raw[
                    "confirmatory_manifest_sha256"
                ],
                run_id=raw["run_id"],
                dispatch_id=raw["dispatch_id"],
                completion_record_sha256=raw["completion_record_sha256"],
                request_sha256=raw["request_sha256"],
                response_artifact_id=raw["response_artifact_id"],
                completion_status=raw["completion_status"],
                verifier_receipt_sha256=raw["verifier_receipt_sha256"],
                request_utf8_bytes=raw["request_utf8_bytes"],
                response_utf8_bytes=raw["response_utf8_bytes"],
                verifier_milliseconds=raw["verifier_milliseconds"],
                orchestration_milliseconds=raw["orchestration_milliseconds"],
                protocol_binding_receipt_sha256=raw[
                    "protocol_binding_receipt_sha256"
                ],
                context_isolation_receipt_sha256=(
                    raw["context_isolation_receipt_sha256"]
                ),
                predecessor_reconciliation_sha256=(
                    raw["predecessor_reconciliation_sha256"]
                ),
                signature=raw["signature"],
            )
            expected_signature = hmac.new(
                self.__secret,
                _canonical_bytes(
                    "supernova.execution-ledger.signature.v3", receipt.body()
                ),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(receipt.signature, expected_signature):
                raise ValueError("persisted execution receipt signature is invalid")
            if (
                dispatch_id != receipt.dispatch_id
                or raw["receipt_sha256"] != receipt.receipt_sha256
                or stored_sha != receipt.receipt_sha256
            ):
                raise ValueError("persisted execution receipt digest is invalid")
            if receipt.run_id != self.run_id:
                raise ValueError("persisted execution receipt crosses run boundary")
            if receipt.issuer_id != self.issuer_id:
                raise ValueError("persisted execution receipt issuer changed")
            if receipt.execution_authority_sha256 != self.execution_authority_sha256:
                raise ValueError("persisted execution authority binding changed")
            if (
                receipt.confirmatory_manifest_sha256
                != self.confirmatory_manifest_sha256
            ):
                raise ValueError("persisted confirmatory manifest binding changed")
            if dispatch_id in receipts:
                raise ValueError("replayed execution receipt")
            receipts[dispatch_id] = receipt
        return receipts

    def verify_complete_join(
        self, closed_join: CompletionJoin
    ) -> tuple[ExecutionLedgerReceipt, ...]:
        if type(closed_join) is not CompletionJoin:
            raise TypeError("closed_join must be an exact CompletionJoin")
        if closed_join.receipt.run_id != self.run_id:
            raise ValueError("closed join run_id does not match execution ledger")
        receipts = self._read_receipts()
        bindings = self._read_protocol_bindings()
        expected_ids = {item.completion.dispatch_id for item in closed_join.joined}
        if set(bindings) != expected_ids:
            raise ValueError(
                "protocol dispatch ledger does not exactly cover the closed join; "
                f"missing={sorted(expected_ids - set(bindings))}, "
                f"extra={sorted(set(bindings) - expected_ids)}"
            )
        if set(receipts) != expected_ids:
            raise ValueError(
                "execution ledger does not exactly cover the closed join; "
                f"missing={sorted(expected_ids - set(receipts))}, "
                f"extra={sorted(set(receipts) - expected_ids)}"
            )
        ordered = []
        for item in closed_join.joined:
            completion = _snapshot_completion(item.completion)
            binding = self._verify_protocol_binding(
                item.dispatch,
                completion.payload.request,
                bindings[completion.dispatch_id],
            )
            receipt = receipts[completion.dispatch_id]
            expected = _completion_body(
                completion,
                issuer_id=self.issuer_id,
                execution_authority_sha256=self.execution_authority_sha256,
                protocol_dispatch_id=receipt.protocol_dispatch_id,
                confirmatory_manifest_sha256=self.confirmatory_manifest_sha256,
                protocol_binding_receipt_sha256=binding.receipt_sha256,
                context_isolation_receipt_sha256=(
                    receipt.context_isolation_receipt_sha256
                ),
                predecessor_reconciliation_sha256=(
                    receipt.predecessor_reconciliation_sha256
                ),
                orchestration_milliseconds=receipt.orchestration_milliseconds,
            )
            if receipt.body() != expected:
                raise ValueError(
                    "execution receipt does not match authenticated completion evidence"
                )
            ordered.append(receipt)
        return tuple(ordered)

    def _issue_evidence_bridge_receipt(
        self, bridge_sha256: str
    ) -> "EvidenceBridgeReceipt":
        bridge_sha256 = _sha256(bridge_sha256, "bridge_sha256")
        body = {
            "bridge_sha256": bridge_sha256,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "run_id": self.run_id,
            "schema": "supernova.evidence-bridge-receipt.v1",
        }
        signature = hmac.new(
            self.__secret,
            _canonical_bytes("supernova.evidence-bridge.signature.v1", body),
            hashlib.sha256,
        ).hexdigest()
        return EvidenceBridgeReceipt(
            issuer_id=self.issuer_id,
            run_id=self.run_id,
            execution_authority_sha256=self.execution_authority_sha256,
            bridge_sha256=bridge_sha256,
            signature=signature,
        )

    def verify_evidence_bridge_bundle(
        self, bundle: "EvidenceBridgeBundle"
    ) -> str:
        if type(bundle) is not EvidenceBridgeBundle:
            raise TypeError("bundle must be an exact EvidenceBridgeBundle")
        receipt = bundle.authority_receipt
        if type(receipt) is not EvidenceBridgeReceipt:
            raise TypeError("bundle authority receipt must be exact")
        expected_body = {
            "bridge_sha256": bundle.bridge_sha256,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "run_id": self.run_id,
            "schema": "supernova.evidence-bridge-receipt.v1",
        }
        if (
            bundle.run_id != self.run_id
            or bundle.execution_authority_sha256
            != self.execution_authority_sha256
            or bundle.protocol_rules_sha256 != self.protocol_rules_sha256
            or bundle.confirmatory_manifest_sha256
            != self.confirmatory_manifest_sha256
            or receipt.body() != expected_body
        ):
            raise ValueError("evidence bridge receipt does not bind trusted authority")

        production_authority = getattr(self, "production_authority", None)
        if production_authority is None:
            expected_signature = hmac.new(
                self.__secret,
                _canonical_bytes(
                    "supernova.evidence-bridge.signature.v1", expected_body
                ),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(receipt.signature, expected_signature):
                raise ValueError("evidence bridge authentication failed")
        else:
            production_authority.verify_receipt_signature(
                receipt.signature,
                domain=PRODUCTION_BRIDGE_RECEIPT_SCHEMA,
                body=expected_body,
            )
        return receipt.receipt_sha256



@dataclass(frozen=True)
class EvidenceBridgeReceipt:
    """Trusted-host authentication for one complete bridge summary."""

    issuer_id: str
    run_id: str
    execution_authority_sha256: str
    bridge_sha256: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _token(self.run_id, "run_id")
        _sha256(self.execution_authority_sha256, "execution_authority_sha256")
        _sha256(self.bridge_sha256, "bridge_sha256")
        _token(self.signature, "signature")

    def body(self) -> dict[str, object]:
        return {
            "bridge_sha256": self.bridge_sha256,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "run_id": self.run_id,
            "schema": "supernova.evidence-bridge-receipt.v1",
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.evidence-bridge-receipt.v1",
            {"body": self.body(), "signature": self.signature},
        )


def _evidence_bundle_body(
    *,
    run_id: str,
    manifest_credit_status: str,
    protocol_rules_sha256: str,
    confirmatory_manifest_sha256: str,
    dispatch_manifest_sha256: str,
    close_sha256: str,
    completion_set_sha256: str,
    execution_authority_sha256: str,
    records: tuple["EvaluatorEvidenceRecord", ...],
) -> dict[str, object]:
    return {
        "close_sha256": close_sha256,
        "completion_set_sha256": completion_set_sha256,
        "confirmatory_manifest_sha256": confirmatory_manifest_sha256,
        "dispatch_manifest_sha256": dispatch_manifest_sha256,
        "execution_authority_sha256": execution_authority_sha256,
        "manifest_credit_status": manifest_credit_status,
        "protocol_rules_sha256": protocol_rules_sha256,
        "record_sha256s": [record.evidence_sha256 for record in records],
        "run_id": run_id,
    }


_RecordTuple = namedtuple(
    "EvaluatorEvidenceRecord",
    (
        "experiment_id",
        "problem_id",
        "problem_identity",
        "arm",
        "budget_id",
        "model_usage_basis",
        "cost",
        "completion_statuses",
        "manifest_credit_status",
        "protocol_rules_sha256",
        "confirmatory_manifest_sha256",
        "dispatch_manifest_sha256",
        "close_sha256",
        "completion_set_sha256",
        "execution_authority_sha256",
        "protocol_dispatch_ids",
        "protocol_binding_receipt_sha256s",
        "dispatch_ids",
        "completion_record_sha256s",
        "verifier_evidence_sha256s",
        "execution_receipt_sha256s",
        "context_isolation_receipt_sha256s",
        "predecessor_reconciliation_sha256s",
        "cost_trace_sha256",
    ),
    module=__name__,
)


class EvaluatorEvidenceRecord(_RecordTuple):
    """One paired cell. Outcome and cost are derived, never accepted raw."""

    __slots__ = ()

    def __new__(cls, *, _factory: object | None = None, **raw: object):
        if _factory is not _RECORD_FACTORY:
            raise TypeError(
                "EvaluatorEvidenceRecord can only be produced by bridge_closed_evidence"
            )
        statuses = tuple(
            CompletionStatus(value) for value in raw["completion_statuses"]
        )
        if len(statuses) != ATTEMPTS_PER_CELL:
            raise ValueError("evaluator evidence requires exactly 16 attempt statuses")
        digest_vectors = (
            "protocol_dispatch_ids",
            "protocol_binding_receipt_sha256s",
            "dispatch_ids",
            "completion_record_sha256s",
            "execution_receipt_sha256s",
            "context_isolation_receipt_sha256s",
            "predecessor_reconciliation_sha256s",
        )
        for name in digest_vectors:
            values = raw[name]
            if type(values) is not tuple or len(values) != ATTEMPTS_PER_CELL:
                raise ValueError(f"{name} must contain exactly 16 values")
            for value in values:
                if name == "protocol_dispatch_ids":
                    value = _token(value, f"{name}[]")
                    if not value.startswith("dispatch-"):
                        raise ValueError("protocol dispatch id has invalid namespace")
                    _sha256(value.removeprefix("dispatch-"), f"{name}[]")
                else:
                    _sha256(value, f"{name}[]")
        verifier_values = raw["verifier_evidence_sha256s"]
        if type(verifier_values) is not tuple or len(verifier_values) != ATTEMPTS_PER_CELL:
            raise ValueError("verifier_evidence_sha256s must contain exactly 16 values")
        for value in verifier_values:
            if value.startswith(_TYPED_ABSENCE + ":"):
                _sha256(
                    value.removeprefix(_TYPED_ABSENCE + ":"),
                    "verifier typed-absence completion digest",
                )
            else:
                _sha256(value, "verifier_evidence_sha256s[]")
        return super().__new__(
            cls,
            _token(raw["experiment_id"], "experiment_id"),
            _token(raw["problem_id"], "problem_id"),
            _token(raw["problem_identity"], "problem_identity"),
            _arm(raw["arm"]),
            _token(raw["budget_id"], "budget_id"),
            ModelUsageBasis(raw["model_usage_basis"]).value,
            _snapshot_cost(raw["cost"], "cost"),
            statuses,
            _token(raw["manifest_credit_status"], "manifest_credit_status"),
            _sha256(raw["protocol_rules_sha256"], "protocol_rules_sha256"),
            _sha256(
                raw["confirmatory_manifest_sha256"],
                "confirmatory_manifest_sha256",
            ),
            _sha256(raw["dispatch_manifest_sha256"], "dispatch_manifest_sha256"),
            _sha256(raw["close_sha256"], "close_sha256"),
            _sha256(raw["completion_set_sha256"], "completion_set_sha256"),
            _sha256(
                raw["execution_authority_sha256"],
                "execution_authority_sha256",
            ),
            tuple(raw["protocol_dispatch_ids"]),
            tuple(raw["protocol_binding_receipt_sha256s"]),
            tuple(raw["dispatch_ids"]),
            tuple(raw["completion_record_sha256s"]),
            tuple(verifier_values),
            tuple(raw["execution_receipt_sha256s"]),
            tuple(raw["context_isolation_receipt_sha256s"]),
            tuple(raw["predecessor_reconciliation_sha256s"]),
            _sha256(raw["cost_trace_sha256"], "cost_trace_sha256"),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvaluatorEvidenceRecord may not be subclassed")

    @classmethod
    def _make(cls, iterable: object) -> "EvaluatorEvidenceRecord":
        raise TypeError("EvaluatorEvidenceRecord cannot be reconstructed directly")

    def _replace(self, **kwargs: object) -> "EvaluatorEvidenceRecord":
        raise TypeError("EvaluatorEvidenceRecord cannot be replaced directly")

    @property
    def solved(self) -> bool:
        return CompletionStatus.SUCCEEDED in self.completion_statuses

    @property
    def verifier_passed(self) -> bool:
        return self.solved

    def to_evaluator_mapping(
        self, *, include_evidence_sha256: bool = True
    ) -> dict[str, object]:
        raw = {
            "arm": self.arm.value,
            "budget_id": self.budget_id,
            "close_sha256": self.close_sha256,
            "completion_record_sha256s": list(self.completion_record_sha256s),
            "completion_set_sha256": self.completion_set_sha256,
            "completion_statuses": [value.value for value in self.completion_statuses],
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "context_isolation_receipt_sha256s": list(
                self.context_isolation_receipt_sha256s
            ),
            "cost": _cost_mapping(self.cost),
            "cost_trace_sha256": self.cost_trace_sha256,
            "dispatch_ids": list(self.dispatch_ids),
            "dispatch_manifest_sha256": self.dispatch_manifest_sha256,
            "execution_authority_sha256": self.execution_authority_sha256,
            "execution_receipt_sha256s": list(self.execution_receipt_sha256s),
            "experiment_id": self.experiment_id,
            "manifest_credit_status": self.manifest_credit_status,
            "model_usage_basis": self.model_usage_basis,
            "predecessor_reconciliation_sha256s": list(
                self.predecessor_reconciliation_sha256s
            ),
            "problem_id": self.problem_id,
            "problem_identity": self.problem_identity,
            "protocol_dispatch_ids": list(self.protocol_dispatch_ids),
            "protocol_binding_receipt_sha256s": list(
                self.protocol_binding_receipt_sha256s
            ),
            "protocol_rules_sha256": self.protocol_rules_sha256,
            "solved": self.solved,
            "verifier_evidence_sha256s": list(self.verifier_evidence_sha256s),
            "verifier_passed": self.verifier_passed,
        }
        if include_evidence_sha256:
            raw["evidence_sha256"] = self.evidence_sha256
        return raw

    @property
    def evidence_sha256(self) -> str:
        return _digest(
            "supernova.evaluator-evidence-record.v2",
            self.to_evaluator_mapping(include_evidence_sha256=False),
        )


_BundleTuple = namedtuple(
    "EvidenceBridgeBundle",
    (
        "run_id",
        "manifest_credit_status",
        "protocol_rules_sha256",
        "confirmatory_manifest_sha256",
        "dispatch_manifest_sha256",
        "close_sha256",
        "completion_set_sha256",
        "execution_authority_sha256",
        "records",
        "authority_receipt",
    ),
    module=__name__,
)


class EvidenceBridgeBundle(_BundleTuple):
    __slots__ = ()

    def __new__(cls, *, _factory: object | None = None, **raw: object):
        if _factory is not _BUNDLE_FACTORY:
            raise TypeError(
                "EvidenceBridgeBundle can only be produced by bridge_closed_evidence"
            )
        records = raw["records"]
        if type(records) is not tuple or not records:
            raise ValueError("bridge bundle requires a non-empty record tuple")
        if not all(type(value) is EvaluatorEvidenceRecord for value in records):
            raise TypeError("bridge records must be exact EvaluatorEvidenceRecord values")
        if type(raw["authority_receipt"]) is not EvidenceBridgeReceipt:
            raise TypeError("authority_receipt must be an exact EvidenceBridgeReceipt")
        keys = [(value.problem_id, value.arm) for value in records]
        if len(keys) != len(set(keys)):
            raise ValueError("bridge bundle contains duplicate problem/arm cells")
        return super().__new__(
            cls,
            _token(raw["run_id"], "run_id"),
            _token(raw["manifest_credit_status"], "manifest_credit_status"),
            _sha256(raw["protocol_rules_sha256"], "protocol_rules_sha256"),
            _sha256(
                raw["confirmatory_manifest_sha256"],
                "confirmatory_manifest_sha256",
            ),
            _sha256(raw["dispatch_manifest_sha256"], "dispatch_manifest_sha256"),
            _sha256(raw["close_sha256"], "close_sha256"),
            _sha256(raw["completion_set_sha256"], "completion_set_sha256"),
            _sha256(
                raw["execution_authority_sha256"],
                "execution_authority_sha256",
            ),
            tuple(records),
            raw["authority_receipt"],
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvidenceBridgeBundle may not be subclassed")

    @classmethod
    def _make(cls, iterable: object) -> "EvidenceBridgeBundle":
        raise TypeError("EvidenceBridgeBundle cannot be reconstructed directly")

    def _replace(self, **kwargs: object) -> "EvidenceBridgeBundle":
        raise TypeError("EvidenceBridgeBundle cannot be replaced directly")

    @property
    def bridge_sha256(self) -> str:
        return _digest(
            "supernova.evidence-bridge-bundle.v2",
            _evidence_bundle_body(
                run_id=self.run_id,
                manifest_credit_status=self.manifest_credit_status,
                protocol_rules_sha256=self.protocol_rules_sha256,
                confirmatory_manifest_sha256=self.confirmatory_manifest_sha256,
                dispatch_manifest_sha256=self.dispatch_manifest_sha256,
                close_sha256=self.close_sha256,
                completion_set_sha256=self.completion_set_sha256,
                execution_authority_sha256=self.execution_authority_sha256,
                records=self.records,
            ),
        )

    @property
    def authority_receipt_sha256(self) -> str:
        return self.authority_receipt.receipt_sha256

    def to_evaluator_mappings(self) -> tuple[dict[str, object], ...]:
        return tuple(record.to_evaluator_mapping() for record in self.records)


def _required_cost_events(
    completions: tuple[CompletionRecord, ...],
    usage_basis: ModelUsageBasis,
) -> dict[str, tuple[CostEventKind, ModelUsageBasis | None]]:
    result = {}
    for completion in completions:
        request_sha = completion.payload.request.frozen_request_sha256
        result[f"{request_sha}:model"] = (CostEventKind.MODEL_CALL, usage_basis)
        result[f"{request_sha}:context_isolation"] = (
            CostEventKind.CONTEXT_ISOLATION,
            None,
        )
        result[f"{request_sha}:verifier"] = (CostEventKind.VERIFIER, None)
        result[f"{request_sha}:orchestration"] = (
            CostEventKind.ORCHESTRATION,
            None,
        )
        result[f"{request_sha}:predecessor_reconciliation"] = (
            CostEventKind.PREDECESSOR_RECONCILIATION,
            None,
        )
    return result


def _reconcile_trace(
    trace: ArmCostTrace,
    completions: tuple[CompletionRecord, ...],
    ledger_receipts: tuple[ExecutionLedgerReceipt, ...],
    usage_basis: ModelUsageBasis,
) -> None:
    required = _required_cost_events(completions, usage_basis)
    if (
        trace.expected_event_manifest != required
        or trace.observed_event_manifest != required
    ):
        raise ValueError("cost event manifest is unbound, partial, or replayed")
    observed = {event.event_id: event for event in trace.events}
    for completion, receipt in zip(completions, ledger_receipts, strict=True):
        prefix = completion.payload.request.frozen_request_sha256
        model = observed[f"{prefix}:model"]
        verifier = observed[f"{prefix}:verifier"]
        orchestration = observed[f"{prefix}:orchestration"]
        if (
            model.input_tokens != receipt.request_utf8_bytes
            or model.output_tokens != receipt.response_utf8_bytes
        ):
            raise ValueError(
                "model-call cost does not match authenticated artifact byte lengths"
            )
        if verifier.milliseconds != receipt.verifier_milliseconds:
            raise ValueError(
                "verifier cost does not match authenticated Lean receipt"
            )
        if orchestration.milliseconds != receipt.orchestration_milliseconds:
            raise ValueError(
                "orchestration cost does not match signed host execution receipt"
            )


def bridge_closed_evidence(
    *,
    dispatch_authority: DispatchAuthority,
    execution_ledger: ExecutionLedgerAuthority,
    closed_join: CompletionJoin,
    protocol: Mapping[str, object],
    public_manifest: Mapping[str, object],
    operator_plan: Mapping[str, object],
    cost_reports_by_problem: Mapping[str, CompleteCostReport],
    production_receipt_issuer: (
        Callable[[str], EvidenceBridgeReceipt] | None
    ) = None,
) -> EvidenceBridgeBundle:
    """Derive evidence-only evaluator inputs; a draft stays explicitly non-credit."""

    if type(dispatch_authority) is not DispatchAuthority:
        raise TypeError("dispatch_authority must be an exact DispatchAuthority")
    if type(execution_ledger) is not ExecutionLedgerAuthority:
        raise TypeError("execution_ledger must be an exact ExecutionLedgerAuthority")
    if type(closed_join) is not CompletionJoin:
        raise TypeError("closed_join must be an exact CompletionJoin")
    for value, field in (
        (protocol, "protocol"),
        (public_manifest, "public_manifest"),
        (operator_plan, "operator_plan"),
        (cost_reports_by_problem, "cost_reports_by_problem"),
    ):
        if type(value) is not dict:
            raise TypeError(f"{field} must be an exact dict")

    requested_credit_status = public_manifest.get("credit_status")
    if requested_credit_status == NON_CREDIT_DRAFT:
        validate_draft_bundle(public_manifest, operator_plan, protocol)
    else:
        validate_manifest_bundle(
            public_manifest,
            operator_plan,
            protocol,
            execution_authority=execution_ledger.production_authority,
        )
    protocol_rules_sha256 = _sha256(
        protocol["sealed_rules_sha256"], "sealed_rules_sha256"
    )
    confirmatory_manifest_sha256 = _sha256(
        public_manifest["manifest_sha256"], "manifest_sha256"
    )
    if canonical_sha256(
        {key: value for key, value in public_manifest.items() if key != "manifest_sha256"}
    ) != confirmatory_manifest_sha256:
        raise ValueError("manifest_sha256 does not bind the public manifest")
    manifest_credit_status = _token(
        public_manifest["credit_status"], "credit_status"
    )
    if manifest_credit_status == NON_CREDIT_DRAFT:
        if execution_ledger.production_authority is not None:
            raise ValueError("production authority cannot authenticate a draft bridge")
    elif manifest_credit_status == PRODUCTION_CREDIT_STATUS:
        if execution_ledger.production_authority is None:
            raise ValueError("production bridge lacks validated execution authority")
    else:
        raise ValueError(
            "unsupported manifest credit status"
        )

    rules = protocol["sealed_rules"]
    benchmark_root = rules["benchmark_selection"]["benchmark_root_sha256"]
    bindings = public_manifest["bindings"]
    runtime_sha256 = bindings["runtime_sha256"]
    cost_policy_sha256 = bindings["cost_policy_sha256"]
    bound_execution_authority = bindings["execution_authority_sha256"]
    if manifest_credit_status == NON_CREDIT_DRAFT:
        if bound_execution_authority is not None:
            raise ValueError("draft manifest unexpectedly binds execution authority")
    elif bound_execution_authority != execution_ledger.execution_authority_sha256:
        raise ValueError("execution authority does not match confirmatory manifest")
    if execution_ledger.protocol_rules_sha256 != protocol_rules_sha256:
        raise ValueError("execution ledger protocol binding changed")
    if (
        execution_ledger.confirmatory_manifest_sha256
        != confirmatory_manifest_sha256
    ):
        raise ValueError("execution ledger confirmatory manifest binding changed")
    # A draft has no production execution-authority binding. Its bridge output is
    # permanently labeled NON_CREDIT_DRAFT, including in every record digest.

    plan_slots: dict[tuple[str, str, int], dict[str, object]] = {}
    for entry in operator_plan["entries"]:
        key = (
            entry["problem_id"],
            entry["arm"],
            entry["budget_attempt_index"],
        )
        if key in plan_slots:
            raise ValueError("operator plan contains a duplicate scientific slot")
        plan_slots[key] = entry

    authoritative_join = dispatch_authority.verify_closed_join(closed_join)
    if authoritative_join.receipt.run_id != execution_ledger.run_id:
        raise ValueError("dispatch and execution authorities have different run_id values")
    execution_receipts = execution_ledger.verify_complete_join(authoritative_join)
    receipt_by_dispatch = {r.dispatch_id: r for r in execution_receipts}

    joined_by_cell: dict[
        tuple[str, Arm],
        list[tuple[CompletionRecord, ExecutionLedgerReceipt, str]],
    ] = {}
    invariants = {
        "experiment_id": set(),
        "budget_id": set(),
        "model_usage_basis": set(),
        "benchmark_root_sha256": set(),
        "runtime_sha256": set(),
        "budget_sha256": set(),
    }
    identities_by_native_id: dict[str, str] = {}
    observed_plan_slots: set[tuple[str, str, int]] = set()

    for joined in authoritative_join.joined:
        completion = _snapshot_completion(joined.completion)
        request = completion.payload.request
        native_id = request.problem.native_id
        plan_key = (native_id, request.arm.value, request.attempt)
        slot = plan_slots.get(plan_key)
        if slot is None:
            raise ValueError(
                "closed dispatch is absent from the validated confirmatory operator plan"
            )
        receipt = receipt_by_dispatch[completion.dispatch_id]
        if (
            request.protocol_dispatch_id != slot["dispatch_id"]
            or request.confirmatory_manifest_sha256
            != confirmatory_manifest_sha256
        ):
            raise ValueError(
                "frozen request is not bound to its protocol slot and manifest"
            )
        if (
            receipt.protocol_dispatch_id != request.protocol_dispatch_id
            or receipt.confirmatory_manifest_sha256
            != request.confirmatory_manifest_sha256
        ):
            raise ValueError(
                "actual dispatch is not cryptographically bound to its protocol slot"
            )
        observed_plan_slots.add(plan_key)
        if request.benchmark_root_sha256 != benchmark_root:
            raise ValueError("request benchmark root differs from sealed protocol")
        if request.runtime_sha256 != runtime_sha256:
            raise ValueError("request runtime differs from confirmatory manifest")
        if request.budget_sha256 != cost_policy_sha256:
            raise ValueError("request budget differs from confirmatory cost policy")
        previous_identity = identities_by_native_id.setdefault(
            native_id, request.problem_id
        )
        if previous_identity != request.problem_id:
            raise ValueError("one native problem id maps to multiple problem identities")
        key = (native_id, request.arm)
        joined_by_cell.setdefault(key, []).append(
            (
                completion,
                receipt,
                slot["dispatch_id"],
            )
        )
        for field in invariants:
            invariants[field].add(getattr(request, field))

    if observed_plan_slots != set(plan_slots):
        raise ValueError(
            "closed dispatches do not exactly cover the validated operator plan; "
            f"observed={len(observed_plan_slots)}, planned={len(plan_slots)}"
        )

    for field, values in invariants.items():
        if len(values) != 1:
            raise ValueError(f"closed run mixes {field} values")

    problem_ids = {problem_id for problem_id, _ in joined_by_cell}
    if set(cost_reports_by_problem) != problem_ids:
        raise ValueError(
            "cost reports do not exactly cover closed-run problems; "
            f"missing={sorted(problem_ids - set(cost_reports_by_problem))}, "
            f"extra={sorted(set(cost_reports_by_problem) - problem_ids)}"
        )

    records = []
    expected_attempts = tuple(range(ATTEMPTS_PER_CELL))
    usage_basis = ModelUsageBasis(next(iter(invariants["model_usage_basis"])))
    for problem_id in sorted(problem_ids):
        report_raw = cost_reports_by_problem[problem_id]
        if type(report_raw) is not CompleteCostReport:
            raise TypeError("cost reports must be exact CompleteCostReport values")
        report = CompleteCostReport.from_traces(report_raw.traces)
        if report.model_usage_basis is not usage_basis:
            raise ValueError("cost report usage basis differs from frozen requests")
        observed_arms = {arm for pid, arm in joined_by_cell if pid == problem_id}
        if observed_arms != set(Arm):
            raise ValueError(f"problem {problem_id} does not contain all five paired arms")

        for arm in Arm:
            items = sorted(
                joined_by_cell[(problem_id, arm)],
                key=lambda item: item[0].payload.request.attempt,
            )
            attempts = tuple(item[0].payload.request.attempt for item in items)
            if attempts != expected_attempts:
                raise ValueError(
                    "problem/arm cell is partial or replayed: "
                    f"{problem_id}/{arm.value}; attempts={attempts}"
                )
            completions = tuple(item[0] for item in items)
            receipts = tuple(item[1] for item in items)
            protocol_dispatch_ids = tuple(item[2] for item in items)
            trace = next(value for value in report.traces if value.arm is arm)
            _reconcile_trace(trace, completions, receipts, usage_basis)

            records.append(
                EvaluatorEvidenceRecord(
                    experiment_id=next(iter(invariants["experiment_id"])),
                    problem_id=problem_id,
                    problem_identity=identities_by_native_id[problem_id],
                    arm=arm,
                    budget_id=next(iter(invariants["budget_id"])),
                    model_usage_basis=usage_basis.value,
                    cost=trace.total,
                    completion_statuses=tuple(c.status for c in completions),
                    manifest_credit_status=manifest_credit_status,
                    protocol_rules_sha256=protocol_rules_sha256,
                    confirmatory_manifest_sha256=confirmatory_manifest_sha256,
                    dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
                    close_sha256=authoritative_join.receipt.close_sha256,
                    completion_set_sha256=authoritative_join.receipt.completion_set_sha256,
                    execution_authority_sha256=execution_ledger.execution_authority_sha256,
                    protocol_dispatch_ids=protocol_dispatch_ids,
                    protocol_binding_receipt_sha256s=tuple(
                        receipt.protocol_binding_receipt_sha256
                        for receipt in receipts
                    ),
                    dispatch_ids=tuple(c.dispatch_id for c in completions),
                    completion_record_sha256s=tuple(
                        c.record_sha256 for c in completions
                    ),
                    verifier_evidence_sha256s=tuple(
                        (
                            f"{_TYPED_ABSENCE}:{c.record_sha256}"
                            if c.payload.verifier_receipt is None
                            else c.payload.verifier_receipt.receipt_sha256
                        )
                        for c in completions
                    ),
                    execution_receipt_sha256s=tuple(
                        receipt.receipt_sha256 for receipt in receipts
                    ),
                    context_isolation_receipt_sha256s=tuple(
                        receipt.context_isolation_receipt_sha256
                        for receipt in receipts
                    ),
                    predecessor_reconciliation_sha256s=tuple(
                        receipt.predecessor_reconciliation_sha256
                        for receipt in receipts
                    ),
                    cost_trace_sha256=_digest(
                        "supernova.cost-trace-evidence.v2",
                        _trace_mapping(trace),
                    ),
                    _factory=_RECORD_FACTORY,
                )
            )

    records_tuple = tuple(records)
    bridge_sha256 = _digest(
        "supernova.evidence-bridge-bundle.v2",
        _evidence_bundle_body(
            run_id=authoritative_join.receipt.run_id,
            manifest_credit_status=manifest_credit_status,
            protocol_rules_sha256=protocol_rules_sha256,
            confirmatory_manifest_sha256=confirmatory_manifest_sha256,
            dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
            close_sha256=authoritative_join.receipt.close_sha256,
            completion_set_sha256=authoritative_join.receipt.completion_set_sha256,
            execution_authority_sha256=execution_ledger.execution_authority_sha256,
            records=records_tuple,
        ),
    )
    if manifest_credit_status == NON_CREDIT_DRAFT:
        if production_receipt_issuer is not None:
            raise ValueError("draft bridge cannot use a production receipt issuer")
        authority_receipt = execution_ledger._issue_evidence_bridge_receipt(
            bridge_sha256
        )
    else:
        if production_receipt_issuer is None:
            raise PermissionError(
                "production bridge requires the hermetic supervisor receipt issuer"
            )
        authority_receipt = production_receipt_issuer(bridge_sha256)
        if type(authority_receipt) is not EvidenceBridgeReceipt:
            raise TypeError("production receipt issuer returned the wrong type")
        expected_body = {
            "bridge_sha256": bridge_sha256,
            "execution_authority_sha256": execution_ledger.execution_authority_sha256,
            "issuer_id": execution_ledger.issuer_id,
            "run_id": authoritative_join.receipt.run_id,
            "schema": "supernova.evidence-bridge-receipt.v1",
        }
        if authority_receipt.body() != expected_body:
            raise ValueError("production bridge receipt does not bind this bridge")
        authority = execution_ledger.production_authority
        if authority is None:
            raise PermissionError("production bridge lacks fixed execution authority")
        authority.verify_receipt_signature(
            authority_receipt.signature,
            domain=PRODUCTION_BRIDGE_RECEIPT_SCHEMA,
            body=expected_body,
        )
    return EvidenceBridgeBundle(
        run_id=authoritative_join.receipt.run_id,
        manifest_credit_status=manifest_credit_status,
        protocol_rules_sha256=protocol_rules_sha256,
        confirmatory_manifest_sha256=confirmatory_manifest_sha256,
        dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
        close_sha256=authoritative_join.receipt.close_sha256,
        completion_set_sha256=authoritative_join.receipt.completion_set_sha256,
        execution_authority_sha256=execution_ledger.execution_authority_sha256,
        records=records_tuple,
        authority_receipt=authority_receipt,
        _factory=_BUNDLE_FACTORY,
    )


__all__ = [
    "ATTEMPTS_PER_CELL",
    "ContextIsolationReceipt",
    "EvidenceBridgeBundle",
    "EvidenceBridgeReceipt",
    "EvaluatorEvidenceRecord",
    "ExecutionLedgerAuthority",
    "ExecutionLedgerReceipt",
    "HermeticContextReceipt",
    "PredecessorReconciliationReceipt",
    "ProtocolDispatchReceipt",
    "bridge_closed_evidence",
]
