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
from typing import Mapping

from .confirmatory_manifest import (
    NON_CREDIT_DRAFT,
    canonical_sha256,
    validate_draft_bundle,
)
from .contracts import Arm, CompleteCost
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
)

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
class ContextIsolationReceipt:
    issuer_id: str
    run_id: str
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
class PredecessorReconciliationReceipt:
    issuer_id: str
    run_id: str
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
        "schema": "supernova.execution-ledger-receipt.v2",
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
        validate_draft_bundle(public_manifest, operator_plan, protocol)
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
                """CREATE TABLE IF NOT EXISTS execution_receipts (
                    run_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (run_id, dispatch_id)
                )"""
            )
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=30)

    def _slot_for_completion(
        self, completion: CompletionRecord
    ) -> dict[str, object]:
        completion = _snapshot_completion(completion)
        request = completion.payload.request
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
        return slot

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
        body = self._context_body(completion)
        signature = hmac.new(
            self.__secret,
            _canonical_bytes("supernova.context-isolation.signature.v1", body),
            hashlib.sha256,
        ).hexdigest()
        return ContextIsolationReceipt(
            issuer_id=str(body["issuer_id"]),
            run_id=str(body["run_id"]),
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
        receipt: ContextIsolationReceipt,
    ) -> ContextIsolationReceipt:
        if type(receipt) is not ContextIsolationReceipt:
            raise TypeError(
                "context_isolation_receipt must be an exact ContextIsolationReceipt"
            )
        expected = self._issue_context_isolation_receipt(completion)
        if receipt.body() != expected.body() or not hmac.compare_digest(
            receipt.signature, expected.signature
        ):
            raise ValueError(
                "context-isolation receipt is not authenticated for this dispatch"
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
        context_isolation_receipt: ContextIsolationReceipt,
        predecessor_reconciliation_receipt: PredecessorReconciliationReceipt,
        orchestration_milliseconds: int,
    ) -> ExecutionLedgerReceipt:
        """Issue once, after the trusted execution adapter returns."""

        completion = _snapshot_completion(completion)
        slot = self._slot_for_completion(completion)
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
                    "execution receipt already issued for dispatch; replay rejected"
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
        expected_ids = {item.completion.dispatch_id for item in closed_join.joined}
        if set(receipts) != expected_ids:
            raise ValueError(
                "execution ledger does not exactly cover the closed join; "
                f"missing={sorted(expected_ids - set(receipts))}, "
                f"extra={sorted(set(receipts) - expected_ids)}"
            )
        ordered = []
        for item in closed_join.joined:
            completion = _snapshot_completion(item.completion)
            receipt = receipts[completion.dispatch_id]
            expected = _completion_body(
                completion,
                issuer_id=self.issuer_id,
                execution_authority_sha256=self.execution_authority_sha256,
                protocol_dispatch_id=receipt.protocol_dispatch_id,
                confirmatory_manifest_sha256=self.confirmatory_manifest_sha256,
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
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvidenceBridgeBundle may not be subclassed")

    @property
    def bridge_sha256(self) -> str:
        return _digest(
            "supernova.evidence-bridge-bundle.v2",
            {
                "close_sha256": self.close_sha256,
                "completion_set_sha256": self.completion_set_sha256,
                "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
                "dispatch_manifest_sha256": self.dispatch_manifest_sha256,
                "execution_authority_sha256": self.execution_authority_sha256,
                "manifest_credit_status": self.manifest_credit_status,
                "protocol_rules_sha256": self.protocol_rules_sha256,
                "record_sha256s": [r.evidence_sha256 for r in self.records],
                "run_id": self.run_id,
            },
        )

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

    validate_draft_bundle(public_manifest, operator_plan, protocol)
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
    if manifest_credit_status != NON_CREDIT_DRAFT:
        raise ValueError(
            "this bridge version accepts only validated non-credit draft manifests; "
            "production opens with the G1-121 execution-authority validator"
        )

    rules = protocol["sealed_rules"]
    benchmark_root = rules["benchmark_selection"]["benchmark_root_sha256"]
    bindings = public_manifest["bindings"]
    runtime_sha256 = bindings["runtime_sha256"]
    cost_policy_sha256 = bindings["cost_policy_sha256"]
    bound_execution_authority = bindings["execution_authority_sha256"]
    if bound_execution_authority is not None:
        if bound_execution_authority != execution_ledger.execution_authority_sha256:
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
            receipt.protocol_dispatch_id != slot["dispatch_id"]
            or receipt.confirmatory_manifest_sha256
            != confirmatory_manifest_sha256
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

    return EvidenceBridgeBundle(
        run_id=authoritative_join.receipt.run_id,
        manifest_credit_status=manifest_credit_status,
        protocol_rules_sha256=protocol_rules_sha256,
        confirmatory_manifest_sha256=confirmatory_manifest_sha256,
        dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
        close_sha256=authoritative_join.receipt.close_sha256,
        completion_set_sha256=authoritative_join.receipt.completion_set_sha256,
        execution_authority_sha256=execution_ledger.execution_authority_sha256,
        records=tuple(records),
        _factory=_BUNDLE_FACTORY,
    )


__all__ = [
    "ATTEMPTS_PER_CELL",
    "ContextIsolationReceipt",
    "EvidenceBridgeBundle",
    "EvaluatorEvidenceRecord",
    "ExecutionLedgerAuthority",
    "ExecutionLedgerReceipt",
    "PredecessorReconciliationReceipt",
    "bridge_closed_evidence",
]
