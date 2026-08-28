"""Typed evidence bridge from closed execution authority to evaluator input.

This module deliberately has no API that accepts caller-supplied solved or cost
booleans. Outcomes are derived from a complete, authority-backed dispatch join;
costs are derived from reconciled event traces; and every accepted completion
must also be present in a separate host-owned execution ledger.
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
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return domain.encode("ascii") + b"\0" + encoded


def _digest(domain: str, value: object) -> str:
    return hashlib.sha256(_canonical_bytes(domain, value)).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    return value


def _sha256(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
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


def _cost_mapping(cost: CompleteCost) -> dict[str, int]:
    snapshot = _snapshot_cost(cost, "cost")
    return {
        "model_calls": snapshot.model_calls,
        "input_tokens": snapshot.input_tokens,
        "output_tokens": snapshot.output_tokens,
        "verifier_milliseconds": snapshot.verifier_milliseconds,
        "orchestration_milliseconds": snapshot.orchestration_milliseconds,
    }


def _event_mapping(event: object) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "milliseconds": event.milliseconds,
        "model_usage_basis": (
            None
            if event.model_usage_basis is None
            else event.model_usage_basis.value
        ),
    }


def _expected_event_mapping(event: object) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "model_usage_basis": (
            None
            if event.model_usage_basis is None
            else event.model_usage_basis.value
        ),
    }


def _trace_mapping(trace: ArmCostTrace) -> dict[str, object]:
    if type(trace) is not ArmCostTrace:
        raise TypeError("cost trace must be an exact ArmCostTrace")
    snapshot = ArmCostTrace(
        arm=trace.arm,
        events=trace.events,
        expected_events=trace.expected_events,
        accounting_complete=trace.accounting_complete,
    )
    return {
        "accounting_complete": snapshot.accounting_complete,
        "arm": snapshot.arm.value,
        "events": [_event_mapping(event) for event in snapshot.events],
        "expected_events": [
            _expected_event_mapping(event) for event in snapshot.expected_events
        ],
        "total": _cost_mapping(snapshot.total),
    }


def _completion_body(
    completion: CompletionRecord,
    *,
    issuer_id: str,
    execution_authority_sha256: str,
) -> dict[str, object]:
    completion = _snapshot_completion(completion)
    payload = completion.payload
    receipt = payload.verifier_receipt
    return {
        "completion_record_sha256": completion.record_sha256,
        "completion_status": completion.status.value,
        "dispatch_id": completion.dispatch_id,
        "execution_authority_sha256": execution_authority_sha256,
        "issuer_id": issuer_id,
        "request_sha256": payload.request.frozen_request_sha256,
        "response_artifact_id": payload.attempt_result.response_artifact.artifact_id,
        "run_id": completion.run_id,
        "schema": "supernova.execution-ledger-receipt.v1",
        "verifier_receipt_sha256": (
            _TYPED_ABSENCE if receipt is None else receipt.receipt_sha256
        ),
    }


@dataclass(frozen=True)
class ExecutionLedgerReceipt:
    issuer_id: str
    execution_authority_sha256: str
    run_id: str
    dispatch_id: str
    completion_record_sha256: str
    request_sha256: str
    response_artifact_id: str
    completion_status: str
    verifier_receipt_sha256: str
    signature: str

    def __post_init__(self) -> None:
        _token(self.issuer_id, "issuer_id")
        _sha256(self.execution_authority_sha256, "execution_authority_sha256")
        _token(self.run_id, "run_id")
        _sha256(self.dispatch_id, "dispatch_id")
        _sha256(self.completion_record_sha256, "completion_record_sha256")
        _sha256(self.request_sha256, "request_sha256")
        _token(self.response_artifact_id, "response_artifact_id")
        try:
            CompletionStatus(self.completion_status)
        except (TypeError, ValueError) as exc:
            raise ValueError("completion_status is invalid") from exc
        if self.verifier_receipt_sha256 != _TYPED_ABSENCE:
            _sha256(self.verifier_receipt_sha256, "verifier_receipt_sha256")
        _sha256(self.signature, "signature")

    @classmethod
    def _issue(
        cls,
        completion: CompletionRecord,
        *,
        issuer_id: str,
        execution_authority_sha256: str,
        secret: bytes,
        _factory: object,
    ) -> "ExecutionLedgerReceipt":
        if _factory is not _LEDGER_FACTORY:
            raise TypeError("execution receipts are issued only by ExecutionLedgerAuthority")
        body = _completion_body(
            completion,
            issuer_id=issuer_id,
            execution_authority_sha256=execution_authority_sha256,
        )
        signature = hmac.new(
            secret,
            _canonical_bytes("supernova.execution-ledger.signature.v1", body),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            issuer_id=issuer_id,
            execution_authority_sha256=execution_authority_sha256,
            run_id=str(body["run_id"]),
            dispatch_id=str(body["dispatch_id"]),
            completion_record_sha256=str(body["completion_record_sha256"]),
            request_sha256=str(body["request_sha256"]),
            response_artifact_id=str(body["response_artifact_id"]),
            completion_status=str(body["completion_status"]),
            verifier_receipt_sha256=str(body["verifier_receipt_sha256"]),
            signature=signature,
        )

    def body(self) -> dict[str, object]:
        return {
            "completion_record_sha256": self.completion_record_sha256,
            "completion_status": self.completion_status,
            "dispatch_id": self.dispatch_id,
            "execution_authority_sha256": self.execution_authority_sha256,
            "issuer_id": self.issuer_id,
            "request_sha256": self.request_sha256,
            "response_artifact_id": self.response_artifact_id,
            "run_id": self.run_id,
            "schema": "supernova.execution-ledger-receipt.v1",
            "verifier_receipt_sha256": self.verifier_receipt_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        return _digest(
            "supernova.execution-ledger.receipt.v1",
            {"body": self.body(), "signature": self.signature},
        )


class ExecutionLedgerAuthority:
    """Host-owned, HMAC-protected evidence that each completion executed.

    The database path and secret belong in the isolated host adapter. Model-facing
    workers receive neither. The private recording seam is called only after the
    trusted adapter returns an exact signed CompletionRecord.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        run_id: str,
        issuer_id: str,
        execution_authority_sha256: str,
        secret: bytes,
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
        self.__secret = bytes(secret)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS execution_receipts (
                    run_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (run_id, dispatch_id)
                )"""
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=30)

    def _record_completion(self, completion: CompletionRecord) -> ExecutionLedgerReceipt:
        """Issue once after trusted host execution; never expose this seam to a model."""

        completion = _snapshot_completion(completion)
        if completion.run_id != self.run_id:
            raise ValueError("completion run_id does not match execution ledger")
        receipt = ExecutionLedgerReceipt._issue(
            completion,
            issuer_id=self.issuer_id,
            execution_authority_sha256=self.execution_authority_sha256,
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
        connection = self._connect()
        try:
            try:
                connection.execute(
                    "INSERT INTO execution_receipts "
                    "(run_id,dispatch_id,receipt_json,receipt_sha256) VALUES(?,?,?,?)",
                    (
                        self.run_id,
                        completion.dispatch_id,
                        encoded,
                        receipt.receipt_sha256,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "execution receipt already issued for dispatch; replay rejected"
                ) from exc
        finally:
            connection.close()
        return receipt

    def _read_receipts(self) -> dict[str, ExecutionLedgerReceipt]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT dispatch_id,receipt_json,receipt_sha256 "
                "FROM execution_receipts WHERE run_id=? ORDER BY dispatch_id",
                (self.run_id,),
            ).fetchall()
        finally:
            connection.close()
        receipts: dict[str, ExecutionLedgerReceipt] = {}
        for dispatch_id, encoded, stored_sha256 in rows:
            raw = json.loads(encoded)
            expected_fields = {
                "completion_record_sha256",
                "completion_status",
                "dispatch_id",
                "execution_authority_sha256",
                "issuer_id",
                "receipt_sha256",
                "request_sha256",
                "response_artifact_id",
                "run_id",
                "schema",
                "signature",
                "verifier_receipt_sha256",
            }
            if not isinstance(raw, dict) or set(raw) != expected_fields:
                raise ValueError("persisted execution receipt fields are not canonical")
            if raw["schema"] != "supernova.execution-ledger-receipt.v1":
                raise ValueError("persisted execution receipt schema changed")
            receipt = ExecutionLedgerReceipt(
                issuer_id=raw["issuer_id"],
                execution_authority_sha256=raw["execution_authority_sha256"],
                run_id=raw["run_id"],
                dispatch_id=raw["dispatch_id"],
                completion_record_sha256=raw["completion_record_sha256"],
                request_sha256=raw["request_sha256"],
                response_artifact_id=raw["response_artifact_id"],
                completion_status=raw["completion_status"],
                verifier_receipt_sha256=raw["verifier_receipt_sha256"],
                signature=raw["signature"],
            )
            expected_signature = hmac.new(
                self.__secret,
                _canonical_bytes(
                    "supernova.execution-ledger.signature.v1", receipt.body()
                ),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(receipt.signature, expected_signature):
                raise ValueError("persisted execution receipt signature is invalid")
            if (
                dispatch_id != receipt.dispatch_id
                or raw["receipt_sha256"] != receipt.receipt_sha256
                or stored_sha256 != receipt.receipt_sha256
            ):
                raise ValueError("persisted execution receipt digest is invalid")
            if receipt.run_id != self.run_id:
                raise ValueError("persisted execution receipt crosses run boundary")
            if receipt.issuer_id != self.issuer_id:
                raise ValueError("persisted execution receipt issuer changed")
            if receipt.execution_authority_sha256 != self.execution_authority_sha256:
                raise ValueError("persisted execution authority binding changed")
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
        expected_dispatch_ids = {
            item.completion.dispatch_id for item in closed_join.joined
        }
        if set(receipts) != expected_dispatch_ids:
            missing = sorted(expected_dispatch_ids - set(receipts))
            extra = sorted(set(receipts) - expected_dispatch_ids)
            raise ValueError(
                "execution ledger does not exactly cover the closed join; "
                f"missing={missing}, extra={extra}"
            )
        ordered: list[ExecutionLedgerReceipt] = []
        for item in closed_join.joined:
            completion = _snapshot_completion(item.completion)
            receipt = receipts[completion.dispatch_id]
            expected = _completion_body(
                completion,
                issuer_id=self.issuer_id,
                execution_authority_sha256=self.execution_authority_sha256,
            )
            if receipt.body() != expected:
                raise ValueError(
                    "execution receipt does not match authenticated completion evidence"
                )
            ordered.append(receipt)
        return tuple(ordered)


_EvaluatorRecordTuple = namedtuple(
    "EvaluatorEvidenceRecord",
    (
        "experiment_id",
        "problem_id",
        "arm",
        "budget_id",
        "model_usage_basis",
        "cost",
        "completion_statuses",
        "protocol_rules_sha256",
        "confirmatory_manifest_sha256",
        "dispatch_manifest_sha256",
        "close_sha256",
        "completion_set_sha256",
        "execution_authority_sha256",
        "dispatch_ids",
        "completion_record_sha256s",
        "verifier_evidence_sha256s",
        "execution_receipt_sha256s",
        "cost_trace_sha256",
    ),
    module=__name__,
)


class EvaluatorEvidenceRecord(_EvaluatorRecordTuple):
    """One evaluator cell derived from evidence; no solved boolean enters."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        experiment_id: str,
        problem_id: str,
        arm: Arm,
        budget_id: str,
        model_usage_basis: str,
        cost: CompleteCost,
        completion_statuses: tuple[CompletionStatus, ...],
        protocol_rules_sha256: str,
        confirmatory_manifest_sha256: str,
        dispatch_manifest_sha256: str,
        close_sha256: str,
        completion_set_sha256: str,
        execution_authority_sha256: str,
        dispatch_ids: tuple[str, ...],
        completion_record_sha256s: tuple[str, ...],
        verifier_evidence_sha256s: tuple[str, ...],
        execution_receipt_sha256s: tuple[str, ...],
        cost_trace_sha256: str,
        _factory: object | None = None,
    ) -> "EvaluatorEvidenceRecord":
        if _factory is not _RECORD_FACTORY:
            raise TypeError(
                "EvaluatorEvidenceRecord can only be produced by bridge_closed_evidence"
            )
        statuses = tuple(CompletionStatus(value) for value in completion_statuses)
        if len(statuses) != ATTEMPTS_PER_CELL:
            raise ValueError("evaluator evidence requires exactly 16 attempt statuses")
        vector_fields = (
            ("dispatch_ids", dispatch_ids),
            ("completion_record_sha256s", completion_record_sha256s),
            ("verifier_evidence_sha256s", verifier_evidence_sha256s),
            ("execution_receipt_sha256s", execution_receipt_sha256s),
        )
        for name, values in vector_fields:
            if type(values) is not tuple or len(values) != ATTEMPTS_PER_CELL:
                raise ValueError(f"{name} must contain exactly 16 values")
            for value in values:
                if name == "verifier_evidence_sha256s" and value.startswith(
                    _TYPED_ABSENCE + ":"
                ):
                    continue
                _sha256(value, f"{name}[]")
        return super().__new__(
            cls,
            _token(experiment_id, "experiment_id"),
            _token(problem_id, "problem_id"),
            _arm(arm),
            _token(budget_id, "budget_id"),
            ModelUsageBasis(model_usage_basis).value,
            _snapshot_cost(cost, "cost"),
            statuses,
            _sha256(protocol_rules_sha256, "protocol_rules_sha256"),
            _sha256(
                confirmatory_manifest_sha256, "confirmatory_manifest_sha256"
            ),
            _sha256(dispatch_manifest_sha256, "dispatch_manifest_sha256"),
            _sha256(close_sha256, "close_sha256"),
            _sha256(completion_set_sha256, "completion_set_sha256"),
            _sha256(execution_authority_sha256, "execution_authority_sha256"),
            tuple(dispatch_ids),
            tuple(completion_record_sha256s),
            tuple(verifier_evidence_sha256s),
            tuple(execution_receipt_sha256s),
            _sha256(cost_trace_sha256, "cost_trace_sha256"),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvaluatorEvidenceRecord may not be subclassed")

    @property
    def solved(self) -> bool:
        return CompletionStatus.SUCCEEDED in self.completion_statuses

    @property
    def verifier_passed(self) -> bool:
        return self.solved

    @property
    def evidence_sha256(self) -> str:
        return _digest(
            "supernova.evaluator-evidence-record.v1",
            self.to_evaluator_mapping(include_evidence_sha256=False),
        )

    def to_evaluator_mapping(
        self, *, include_evidence_sha256: bool = True
    ) -> dict[str, object]:
        raw: dict[str, object] = {
            "arm": self.arm.value,
            "budget_id": self.budget_id,
            "close_sha256": self.close_sha256,
            "completion_record_sha256s": list(
                self.completion_record_sha256s
            ),
            "completion_set_sha256": self.completion_set_sha256,
            "completion_statuses": [
                status.value for status in self.completion_statuses
            ],
            "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
            "cost": _cost_mapping(self.cost),
            "cost_trace_sha256": self.cost_trace_sha256,
            "dispatch_ids": list(self.dispatch_ids),
            "dispatch_manifest_sha256": self.dispatch_manifest_sha256,
            "execution_authority_sha256": self.execution_authority_sha256,
            "execution_receipt_sha256s": list(
                self.execution_receipt_sha256s
            ),
            "experiment_id": self.experiment_id,
            "model_usage_basis": self.model_usage_basis,
            "problem_id": self.problem_id,
            "protocol_rules_sha256": self.protocol_rules_sha256,
            "solved": self.solved,
            "verifier_evidence_sha256s": list(
                self.verifier_evidence_sha256s
            ),
            "verifier_passed": self.verifier_passed,
        }
        if include_evidence_sha256:
            raw["evidence_sha256"] = self.evidence_sha256
        return raw


_BridgeBundleTuple = namedtuple(
    "EvidenceBridgeBundle",
    (
        "run_id",
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


class EvidenceBridgeBundle(_BridgeBundleTuple):
    __slots__ = ()

    def __new__(
        cls,
        *,
        run_id: str,
        protocol_rules_sha256: str,
        confirmatory_manifest_sha256: str,
        dispatch_manifest_sha256: str,
        close_sha256: str,
        completion_set_sha256: str,
        execution_authority_sha256: str,
        records: tuple[EvaluatorEvidenceRecord, ...],
        _factory: object | None = None,
    ) -> "EvidenceBridgeBundle":
        if _factory is not _BUNDLE_FACTORY:
            raise TypeError(
                "EvidenceBridgeBundle can only be produced by bridge_closed_evidence"
            )
        if type(records) is not tuple or not records:
            raise ValueError("bridge bundle must contain an immutable non-empty record tuple")
        if not all(type(record) is EvaluatorEvidenceRecord for record in records):
            raise TypeError("bridge records must be exact EvaluatorEvidenceRecord values")
        keys = [(record.problem_id, record.arm) for record in records]
        if len(keys) != len(set(keys)):
            raise ValueError("bridge bundle contains duplicate problem/arm cells")
        return super().__new__(
            cls,
            _token(run_id, "run_id"),
            _sha256(protocol_rules_sha256, "protocol_rules_sha256"),
            _sha256(
                confirmatory_manifest_sha256, "confirmatory_manifest_sha256"
            ),
            _sha256(dispatch_manifest_sha256, "dispatch_manifest_sha256"),
            _sha256(close_sha256, "close_sha256"),
            _sha256(completion_set_sha256, "completion_set_sha256"),
            _sha256(execution_authority_sha256, "execution_authority_sha256"),
            tuple(records),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvidenceBridgeBundle may not be subclassed")

    @property
    def bridge_sha256(self) -> str:
        return _digest(
            "supernova.evidence-bridge-bundle.v1",
            {
                "close_sha256": self.close_sha256,
                "completion_set_sha256": self.completion_set_sha256,
                "confirmatory_manifest_sha256": self.confirmatory_manifest_sha256,
                "dispatch_manifest_sha256": self.dispatch_manifest_sha256,
                "evidence_record_sha256s": [
                    record.evidence_sha256 for record in self.records
                ],
                "execution_authority_sha256": self.execution_authority_sha256,
                "protocol_rules_sha256": self.protocol_rules_sha256,
                "run_id": self.run_id,
            },
        )

    def to_evaluator_mappings(self) -> tuple[dict[str, object], ...]:
        return tuple(record.to_evaluator_mapping() for record in self.records)


def _required_event_manifest(
    completions: tuple[CompletionRecord, ...],
    usage_basis: ModelUsageBasis,
) -> dict[str, tuple[CostEventKind, ModelUsageBasis | None]]:
    required: dict[str, tuple[CostEventKind, ModelUsageBasis | None]] = {}
    for completion in completions:
        request_sha = completion.payload.request.frozen_request_sha256
        required[f"{request_sha}:model"] = (CostEventKind.MODEL_CALL, usage_basis)
        required[f"{request_sha}:verifier"] = (CostEventKind.VERIFIER, None)
        required[f"{request_sha}:orchestration"] = (
            CostEventKind.ORCHESTRATION,
            None,
        )
    return required


def bridge_closed_evidence(
    *,
    dispatch_authority: DispatchAuthority,
    execution_ledger: ExecutionLedgerAuthority,
    closed_join: CompletionJoin,
    protocol_rules_sha256: str,
    confirmatory_manifest_sha256: str,
    execution_authority_sha256: str,
    cost_reports_by_problem: Mapping[str, CompleteCostReport],
) -> EvidenceBridgeBundle:
    """Derive evaluator cells from a complete, multiply-authenticated evidence set."""

    if type(dispatch_authority) is not DispatchAuthority:
        raise TypeError("dispatch_authority must be an exact DispatchAuthority")
    if type(execution_ledger) is not ExecutionLedgerAuthority:
        raise TypeError("execution_ledger must be an exact ExecutionLedgerAuthority")
    if type(closed_join) is not CompletionJoin:
        raise TypeError("closed_join must be an exact CompletionJoin")
    if type(cost_reports_by_problem) is not dict:
        raise TypeError("cost_reports_by_problem must be an exact dict")

    protocol_rules_sha256 = _sha256(
        protocol_rules_sha256, "protocol_rules_sha256"
    )
    confirmatory_manifest_sha256 = _sha256(
        confirmatory_manifest_sha256, "confirmatory_manifest_sha256"
    )
    execution_authority_sha256 = _sha256(
        execution_authority_sha256, "execution_authority_sha256"
    )
    if execution_ledger.execution_authority_sha256 != execution_authority_sha256:
        raise ValueError("execution ledger is bound to a different execution authority")

    authoritative_join = dispatch_authority.verify_closed_join(closed_join)
    if authoritative_join.receipt.run_id != execution_ledger.run_id:
        raise ValueError("dispatch and execution authorities have different run_id values")
    execution_receipts = execution_ledger.verify_complete_join(authoritative_join)
    receipts_by_dispatch = {
        receipt.dispatch_id: receipt for receipt in execution_receipts
    }

    joined_by_cell: dict[
        tuple[str, Arm], list[tuple[CompletionRecord, ExecutionLedgerReceipt]]
    ] = {}
    experiment_ids: set[str] = set()
    budget_ids: set[str] = set()
    budget_sha256s: set[str] = set()
    usage_bases: set[str] = set()
    benchmark_roots: set[str] = set()
    runtime_sha256s: set[str] = set()

    for item in authoritative_join.joined:
        completion = _snapshot_completion(item.completion)
        request = completion.payload.request
        if (
            item.dispatch.problem_id != request.problem_id
            or item.dispatch.arm is not request.arm
            or item.dispatch.attempt_index != request.attempt
            or item.dispatch.request_sha256 != request.frozen_request_sha256
        ):
            raise ValueError("closed dispatch entry is not bound to its typed request")
        key = (request.problem_id, request.arm)
        joined_by_cell.setdefault(key, []).append(
            (completion, receipts_by_dispatch[completion.dispatch_id])
        )
        experiment_ids.add(request.experiment_id)
        budget_ids.add(request.budget_id)
        budget_sha256s.add(request.budget_sha256)
        usage_bases.add(request.model_usage_basis)
        benchmark_roots.add(request.benchmark_root_sha256)
        runtime_sha256s.add(request.runtime_sha256)

    invariant_sets = {
        "experiment_id": experiment_ids,
        "budget_id": budget_ids,
        "budget_sha256": budget_sha256s,
        "model_usage_basis": usage_bases,
        "benchmark_root_sha256": benchmark_roots,
        "runtime_sha256": runtime_sha256s,
    }
    for field, values in invariant_sets.items():
        if len(values) != 1:
            raise ValueError(f"closed run mixes {field} values")

    problem_ids = {problem_id for problem_id, _ in joined_by_cell}
    if set(cost_reports_by_problem) != problem_ids:
        missing = sorted(problem_ids - set(cost_reports_by_problem))
        extra = sorted(set(cost_reports_by_problem) - problem_ids)
        raise ValueError(
            "cost reports do not exactly cover closed-run problems; "
            f"missing={missing}, extra={extra}"
        )

    expected_attempts = tuple(range(ATTEMPTS_PER_CELL))
    records: list[EvaluatorEvidenceRecord] = []
    for problem_id in sorted(problem_ids):
        report_raw = cost_reports_by_problem[problem_id]
        if type(report_raw) is not CompleteCostReport:
            raise TypeError("cost reports must be exact CompleteCostReport values")
        report = CompleteCostReport.from_traces(report_raw.traces)
        usage_basis = ModelUsageBasis(next(iter(usage_bases)))
        if report.model_usage_basis is not usage_basis:
            raise ValueError("cost report usage basis does not match frozen requests")

        observed_arms = {arm for pid, arm in joined_by_cell if pid == problem_id}
        if observed_arms != set(Arm):
            missing_arms = sorted(arm.value for arm in set(Arm) - observed_arms)
            raise ValueError(
                f"problem {problem_id} is missing paired arms: {missing_arms}"
            )

        for arm in Arm:
            items = sorted(
                joined_by_cell[(problem_id, arm)],
                key=lambda item: item[0].payload.request.attempt,
            )
            attempts = tuple(
                completion.payload.request.attempt for completion, _ in items
            )
            if attempts != expected_attempts:
                raise ValueError(
                    f"problem/arm cell is partial or replayed: "
                    f"{problem_id}/{arm.value}; attempts={attempts}"
                )
            completions = tuple(completion for completion, _ in items)
            ledger_receipts = tuple(receipt for _, receipt in items)
            trace = next(trace for trace in report.traces if trace.arm is arm)
            required_events = _required_event_manifest(completions, usage_basis)
            if trace.expected_event_manifest != required_events:
                raise ValueError(
                    f"cost event manifest is unbound, partial, or replayed for "
                    f"{problem_id}/{arm.value}"
                )
            if trace.observed_event_manifest != required_events:
                raise ValueError(
                    f"observed cost events do not reconcile for "
                    f"{problem_id}/{arm.value}"
                )

            statuses = tuple(completion.status for completion in completions)
            verifier_evidence = tuple(
                (
                    f"{_TYPED_ABSENCE}:{completion.record_sha256}"
                    if completion.payload.verifier_receipt is None
                    else completion.payload.verifier_receipt.receipt_sha256
                )
                for completion in completions
            )
            records.append(
                EvaluatorEvidenceRecord(
                    experiment_id=next(iter(experiment_ids)),
                    problem_id=problem_id,
                    arm=arm,
                    budget_id=next(iter(budget_ids)),
                    model_usage_basis=usage_basis.value,
                    cost=trace.total,
                    completion_statuses=statuses,
                    protocol_rules_sha256=protocol_rules_sha256,
                    confirmatory_manifest_sha256=confirmatory_manifest_sha256,
                    dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
                    close_sha256=authoritative_join.receipt.close_sha256,
                    completion_set_sha256=(
                        authoritative_join.receipt.completion_set_sha256
                    ),
                    execution_authority_sha256=execution_authority_sha256,
                    dispatch_ids=tuple(
                        completion.dispatch_id for completion in completions
                    ),
                    completion_record_sha256s=tuple(
                        completion.record_sha256 for completion in completions
                    ),
                    verifier_evidence_sha256s=verifier_evidence,
                    execution_receipt_sha256s=tuple(
                        receipt.receipt_sha256 for receipt in ledger_receipts
                    ),
                    cost_trace_sha256=_digest(
                        "supernova.cost-trace-evidence.v1",
                        _trace_mapping(trace),
                    ),
                    _factory=_RECORD_FACTORY,
                )
            )

    return EvidenceBridgeBundle(
        run_id=authoritative_join.receipt.run_id,
        protocol_rules_sha256=protocol_rules_sha256,
        confirmatory_manifest_sha256=confirmatory_manifest_sha256,
        dispatch_manifest_sha256=authoritative_join.receipt.manifest_sha256,
        close_sha256=authoritative_join.receipt.close_sha256,
        completion_set_sha256=authoritative_join.receipt.completion_set_sha256,
        execution_authority_sha256=execution_authority_sha256,
        records=tuple(records),
        _factory=_BUNDLE_FACTORY,
    )


__all__ = [
    "ATTEMPTS_PER_CELL",
    "EvidenceBridgeBundle",
    "EvaluatorEvidenceRecord",
    "ExecutionLedgerAuthority",
    "ExecutionLedgerReceipt",
    "bridge_closed_evidence",
]
