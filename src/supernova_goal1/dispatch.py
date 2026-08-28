"""Durable pre-dispatch registration and authority-backed completion joining.

The SQLite database is the monotonic trust root for this bounded module. Executor
completion signing material is created outside :class:`DispatchAuthority` and must
remain process-isolated from the registrar/closer. Python object privacy, name
mangling, and copy guards are defense-in-depth only; they are not the security
boundary.

Completeness is enforceable only for dispatches preregistered through the retained
authority. An out-of-band model or scheduled-chat call that bypasses registration is
outside this module's observation boundary; credited consumers must require the
authority-backed closed-join readback/verification path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping

from supernova_goal1.contracts import Arm
from supernova_goal1.execution.common import (
    AttemptResult,
    AttemptStatus,
    FrozenProblemRequest,
    LeanVerifierReceipt,
)
from supernova_goal1.verifier import VerifierStatus

_ZERO = "0" * 64
_OTS_SECRET_BYTES = 32
_OTS_BITS = 256
_OTS_PRIVATE_BYTES = _OTS_BITS * 2 * _OTS_SECRET_BYTES
_OTS_SIGNATURE_BYTES = _OTS_BITS * _OTS_SECRET_BYTES
_SIGNER_FACTORY = object()


def _s(v: object, name: str) -> str:
    if type(v) is not str or not v:
        raise ValueError(f"{name} must be a non-empty plain string")
    return v


def _n(v: object, name: str) -> int:
    if type(v) is not int or v < 0:
        raise ValueError(f"{name} must be a non-negative plain integer")
    return v


def _hex(v: object, name: str) -> str:
    if type(v) is not str or len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return v


def _long_hex(v: object, name: str, byte_len: int) -> str:
    if type(v) is not str or len(v) != byte_len * 2 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"{name} must be exactly {byte_len} bytes of lowercase hex")
    return v


def _arm(v: object) -> Arm:
    if type(v) is Arm:
        return v
    if type(v) is str:
        try:
            return Arm(v)
        except ValueError as exc:
            raise ValueError(f"unknown arm: {v!r}") from exc
    raise ValueError("arm must be an exact Arm or plain arm string")


def _canon(domain: str, data: Mapping[str, object]) -> bytes:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return domain.encode("ascii") + b"\0" + payload.encode("utf-8")


def _digest(domain: str, data: Mapping[str, object]) -> str:
    return hashlib.sha256(_canon(domain, data)).hexdigest()


def _ots_public_key(private_key: bytes) -> bytes:
    if type(private_key) is not bytes or len(private_key) != _OTS_PRIVATE_BYTES:
        raise ValueError("invalid one-time completion signing key")
    return b"".join(
        hashlib.sha256(private_key[i:i + _OTS_SECRET_BYTES]).digest()
        for i in range(0, len(private_key), _OTS_SECRET_BYTES)
    )


def _ots_public_commitment(public_key: bytes) -> str:
    if type(public_key) is not bytes or len(public_key) != _OTS_PRIVATE_BYTES:
        raise ValueError("invalid one-time completion verification key")
    return hashlib.sha256(b"supernova.dispatch.ots-public.v2\0" + public_key).hexdigest()


def _bit(digest: bytes, index: int) -> int:
    return (digest[index // 8] >> (7 - index % 8)) & 1


def _ots_sign(private_key: bytes, message: bytes) -> str:
    digest = hashlib.sha256(message).digest()
    parts = []
    for i in range(_OTS_BITS):
        choice = _bit(digest, i)
        offset = (2 * i + choice) * _OTS_SECRET_BYTES
        parts.append(private_key[offset:offset + _OTS_SECRET_BYTES])
    return b"".join(parts).hex()


def _ots_verify(public_key: bytes, message: bytes, signature: str) -> bool:
    try:
        sig = bytes.fromhex(_long_hex(signature, "signature", _OTS_SIGNATURE_BYTES))
    except ValueError:
        return False
    if type(public_key) is not bytes or len(public_key) != _OTS_PRIVATE_BYTES:
        return False
    digest = hashlib.sha256(message).digest()
    for i in range(_OTS_BITS):
        choice = _bit(digest, i)
        secret = sig[i * _OTS_SECRET_BYTES:(i + 1) * _OTS_SECRET_BYTES]
        expected = public_key[(2 * i + choice) * _OTS_SECRET_BYTES:(2 * i + choice + 1) * _OTS_SECRET_BYTES]
        if not hmac.compare_digest(hashlib.sha256(secret).digest(), expected):
            return False
    return True


def _snapshot_request(value: object) -> FrozenProblemRequest:
    if type(value) is not FrozenProblemRequest:
        raise TypeError("request must be an exact FrozenProblemRequest")
    return FrozenProblemRequest.from_mapping(value.to_mapping())


def _snapshot_result(value: object) -> AttemptResult:
    if type(value) is not AttemptResult:
        raise TypeError("attempt_result must be an exact AttemptResult")
    return AttemptResult.from_mapping(value.to_mapping())


def _snapshot_receipt(value: object | None) -> LeanVerifierReceipt | None:
    if value is None:
        return None
    if type(value) is not LeanVerifierReceipt:
        raise TypeError("verifier_receipt must be an exact LeanVerifierReceipt or null")
    return LeanVerifierReceipt.from_mapping(value.to_mapping())


class CompletionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class RunTerminalState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    ABORTED = "ABORTED"


def _completion_status(
    request: FrozenProblemRequest,
    result: AttemptResult,
    receipt: LeanVerifierReceipt | None,
) -> CompletionStatus:
    result.validate_for(request)
    if result.status is AttemptStatus.ERROR:
        if receipt is not None:
            raise ValueError("ERROR attempt must not carry a verifier receipt")
        return CompletionStatus.ERROR
    if result.status is AttemptStatus.NO_ANSWER:
        if receipt is not None:
            raise ValueError("NO_ANSWER attempt must not carry a verifier receipt")
        return CompletionStatus.FAILED
    if receipt is None:
        raise ValueError("ANSWERED attempt requires a Lean verifier receipt")
    receipt.validate_for(request, result)
    mapping = {
        VerifierStatus.PASS: CompletionStatus.SUCCEEDED,
        VerifierStatus.FAIL: CompletionStatus.FAILED,
        VerifierStatus.TIMEOUT: CompletionStatus.TIMEOUT,
        VerifierStatus.ERROR: CompletionStatus.ERROR,
    }
    return mapping[receipt.status]


@dataclass(frozen=True)
class CompletionPayload:
    request: FrozenProblemRequest
    attempt_result: AttemptResult
    verifier_receipt: LeanVerifierReceipt | None

    def __post_init__(self) -> None:
        request = _snapshot_request(self.request)
        result = _snapshot_result(self.attempt_result)
        receipt = _snapshot_receipt(self.verifier_receipt)
        _completion_status(request, result, receipt)
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "attempt_result", result)
        object.__setattr__(self, "verifier_receipt", receipt)

    @property
    def status(self) -> CompletionStatus:
        return _completion_status(self.request, self.attempt_result, self.verifier_receipt)

    def _body(self) -> dict[str, object]:
        return {
            "request": self.request.to_mapping(),
            "attempt_result": self.attempt_result.to_mapping(),
            "verifier_receipt": None if self.verifier_receipt is None else self.verifier_receipt.to_mapping(),
            "status": self.status.value,
        }

    @property
    def payload_sha256(self) -> str:
        return _digest("supernova.dispatch.completion-payload.v3", self._body())

    def to_mapping(self) -> dict[str, object]:
        raw = self._body()
        raw["payload_sha256"] = self.payload_sha256
        return raw

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "CompletionPayload":
        if not isinstance(raw, Mapping):
            raise ValueError("completion payload must be an object")
        expected = {"request", "attempt_result", "verifier_receipt", "status", "payload_sha256"}
        if set(raw) != expected:
            raise ValueError(f"completion payload fields must be exactly {sorted(expected)}")
        request_raw = raw["request"]
        result_raw = raw["attempt_result"]
        receipt_raw = raw["verifier_receipt"]
        if not isinstance(request_raw, Mapping) or not isinstance(result_raw, Mapping):
            raise ValueError("completion payload request/result must be objects")
        if receipt_raw is not None and not isinstance(receipt_raw, Mapping):
            raise ValueError("completion payload verifier_receipt must be an object or null")
        payload = cls(
            FrozenProblemRequest.from_mapping(request_raw),
            AttemptResult.from_mapping(result_raw),
            None if receipt_raw is None else LeanVerifierReceipt.from_mapping(receipt_raw),
        )
        if raw["status"] != payload.status.value:
            raise ValueError("completion payload status does not match typed execution evidence")
        if raw["payload_sha256"] != payload.payload_sha256:
            raise ValueError("payload_sha256 does not match canonical typed completion content")
        return payload


def _revalidate_payload(payload: object) -> CompletionPayload:
    if type(payload) is not CompletionPayload:
        raise ValueError("payload must be an exact CompletionPayload")
    return CompletionPayload.from_mapping(payload.to_mapping())


@dataclass(frozen=True)
class DispatchEntry:
    run_id: str
    sequence: int
    problem_id: str
    arm: Arm
    attempt_index: int
    request_sha256: str
    completion_verifier_sha256: str
    predecessor_sha256: str
    dispatch_id: str
    entry_sha256: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        problem_id: str,
        arm: Arm | str,
        attempt_index: int,
        request_sha256: str,
        completion_verifier_sha256: str,
        predecessor_sha256: str,
    ) -> "DispatchEntry":
        run_id, problem_id = _s(run_id, "run_id"), _s(problem_id, "problem_id")
        sequence, attempt_index = _n(sequence, "sequence"), _n(attempt_index, "attempt_index")
        arm = _arm(arm)
        request_sha256 = _hex(request_sha256, "request_sha256")
        completion_verifier_sha256 = _hex(completion_verifier_sha256, "completion_verifier_sha256")
        predecessor_sha256 = _hex(predecessor_sha256, "predecessor_sha256")
        identity = {
            "run_id": run_id,
            "problem_id": problem_id,
            "arm": arm.value,
            "attempt_index": attempt_index,
            "request_sha256": request_sha256,
            "completion_verifier_sha256": completion_verifier_sha256,
        }
        dispatch_id = _digest("supernova.dispatch.id.v3", identity)
        entry_sha256 = _digest(
            "supernova.dispatch.entry.v3",
            {"sequence": sequence, "predecessor_sha256": predecessor_sha256, "dispatch_id": dispatch_id},
        )
        return cls(
            run_id,
            sequence,
            problem_id,
            arm,
            attempt_index,
            request_sha256,
            completion_verifier_sha256,
            predecessor_sha256,
            dispatch_id,
            entry_sha256,
        )

    def __post_init__(self) -> None:
        run_id, problem_id = _s(self.run_id, "run_id"), _s(self.problem_id, "problem_id")
        sequence, attempt_index = _n(self.sequence, "sequence"), _n(self.attempt_index, "attempt_index")
        arm = _arm(self.arm)
        request_sha256 = _hex(self.request_sha256, "request_sha256")
        verifier_sha = _hex(self.completion_verifier_sha256, "completion_verifier_sha256")
        predecessor = _hex(self.predecessor_sha256, "predecessor_sha256")
        dispatch_id = _digest("supernova.dispatch.id.v3", {
            "run_id": run_id,
            "problem_id": problem_id,
            "arm": arm.value,
            "attempt_index": attempt_index,
            "request_sha256": request_sha256,
            "completion_verifier_sha256": verifier_sha,
        })
        entry_sha = _digest("supernova.dispatch.entry.v3", {
            "sequence": sequence,
            "predecessor_sha256": predecessor,
            "dispatch_id": dispatch_id,
        })
        if self.dispatch_id != dispatch_id:
            raise ValueError("dispatch_id does not match canonical pre-dispatch content")
        if self.entry_sha256 != entry_sha:
            raise ValueError("entry_sha256 does not match append-only hash chain")
        object.__setattr__(self, "arm", arm)

    @property
    def logical_key(self) -> tuple[str, Arm, int]:
        return self.problem_id, self.arm, self.attempt_index


def _revalidate_entry(entry: object) -> DispatchEntry:
    if type(entry) is not DispatchEntry:
        raise ValueError("entries must contain exact DispatchEntry values")
    canonical = DispatchEntry.create(
        run_id=entry.run_id,
        sequence=entry.sequence,
        problem_id=entry.problem_id,
        arm=entry.arm,
        attempt_index=entry.attempt_index,
        request_sha256=entry.request_sha256,
        completion_verifier_sha256=entry.completion_verifier_sha256,
        predecessor_sha256=entry.predecessor_sha256,
    )
    if canonical.dispatch_id != entry.dispatch_id or canonical.entry_sha256 != entry.entry_sha256:
        raise ValueError("live dispatch entry no longer matches canonical committed content")
    return canonical


@dataclass(frozen=True)
class DispatchManifest:
    run_id: str
    entries: tuple[DispatchEntry, ...] = ()

    def __post_init__(self) -> None:
        run_id = _s(self.run_id, "run_id")
        if type(self.entries) is not tuple:
            raise ValueError("entries must be an immutable tuple")
        predecessor, logical, ids, verifiers = _ZERO, set(), set(), set()
        canonical_entries = []
        for i, raw in enumerate(self.entries):
            entry = _revalidate_entry(raw)
            if entry.run_id != run_id or entry.sequence != i or entry.predecessor_sha256 != predecessor:
                raise ValueError("manifest breaks run/sequence/predecessor append-only chain")
            if entry.logical_key in logical:
                raise ValueError("duplicate dispatch attempt in pre-dispatch manifest")
            if entry.dispatch_id in ids:
                raise ValueError("duplicate dispatch_id in pre-dispatch manifest")
            if entry.completion_verifier_sha256 in verifiers:
                raise ValueError("completion verifier commitments must be unique per dispatch")
            canonical_entries.append(entry)
            logical.add(entry.logical_key)
            ids.add(entry.dispatch_id)
            verifiers.add(entry.completion_verifier_sha256)
            predecessor = entry.entry_sha256
        object.__setattr__(self, "entries", tuple(canonical_entries))

    @classmethod
    def empty(cls, run_id: str) -> "DispatchManifest":
        return cls(_s(run_id, "run_id"), ())

    def _append_request(self, request: FrozenProblemRequest, completion_verifier_sha256: str) -> "DispatchManifest":
        request = _snapshot_request(request)
        verifier_sha = _hex(completion_verifier_sha256, "completion_verifier_sha256")
        entry = DispatchEntry.create(
            run_id=self.run_id,
            sequence=len(self.entries),
            problem_id=request.problem_id,
            arm=request.arm,
            attempt_index=request.attempt,
            request_sha256=request.frozen_request_sha256,
            completion_verifier_sha256=verifier_sha,
            predecessor_sha256=self.entries[-1].entry_sha256 if self.entries else _ZERO,
        )
        return DispatchManifest(self.run_id, self.entries + (entry,))

    @property
    def manifest_sha256(self) -> str:
        return _manifest_digest(self)


def _revalidate_manifest(manifest: object) -> DispatchManifest:
    if type(manifest) is not DispatchManifest:
        raise ValueError("manifest must be an exact DispatchManifest")
    return DispatchManifest(manifest.run_id, tuple(_revalidate_entry(e) for e in manifest.entries))


def _manifest_digest(manifest: DispatchManifest) -> str:
    canonical = _revalidate_manifest(manifest)
    return _digest(
        "supernova.dispatch.manifest.v3",
        {"run_id": canonical.run_id, "entry_sha256": [entry.entry_sha256 for entry in canonical.entries]},
    )


@dataclass(frozen=True)
class CompletionRecord:
    run_id: str
    dispatch_id: str
    entry_sha256: str
    payload: CompletionPayload
    verifier_public_key: str
    signature: str

    def __post_init__(self) -> None:
        _s(self.run_id, "run_id")
        _hex(self.dispatch_id, "dispatch_id")
        _hex(self.entry_sha256, "entry_sha256")
        object.__setattr__(self, "payload", _revalidate_payload(self.payload))
        _long_hex(self.verifier_public_key, "verifier_public_key", _OTS_PRIVATE_BYTES)
        _long_hex(self.signature, "signature", _OTS_SIGNATURE_BYTES)

    @property
    def status(self) -> CompletionStatus:
        return self.payload.status

    @property
    def payload_sha256(self) -> str:
        return self.payload.payload_sha256

    @staticmethod
    def _body(entry: DispatchEntry, payload: CompletionPayload) -> dict[str, object]:
        return {
            "run_id": entry.run_id,
            "dispatch_id": entry.dispatch_id,
            "entry_sha256": entry.entry_sha256,
            "request_sha256": entry.request_sha256,
            "status": payload.status.value,
            "payload_sha256": payload.payload_sha256,
        }

    @property
    def record_sha256(self) -> str:
        return _digest("supernova.dispatch.completion-record.v3", {
            "run_id": self.run_id,
            "dispatch_id": self.dispatch_id,
            "entry_sha256": self.entry_sha256,
            "payload": self.payload.to_mapping(),
            "verifier_public_key": self.verifier_public_key,
            "signature": self.signature,
        })

    def to_mapping(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "dispatch_id": self.dispatch_id,
            "entry_sha256": self.entry_sha256,
            "payload": self.payload.to_mapping(),
            "verifier_public_key": self.verifier_public_key,
            "signature": self.signature,
            "record_sha256": self.record_sha256,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "CompletionRecord":
        if not isinstance(raw, Mapping):
            raise ValueError("completion record must be an object")
        expected = {"run_id", "dispatch_id", "entry_sha256", "payload", "verifier_public_key", "signature", "record_sha256"}
        if set(raw) != expected:
            raise ValueError(f"completion record fields must be exactly {sorted(expected)}")
        payload_raw = raw["payload"]
        if not isinstance(payload_raw, Mapping):
            raise ValueError("completion record payload must be an object")
        record = cls(
            raw["run_id"],
            raw["dispatch_id"],
            raw["entry_sha256"],
            CompletionPayload.from_mapping(payload_raw),
            raw["verifier_public_key"],
            raw["signature"],
        )
        if raw["record_sha256"] != record.record_sha256:
            raise ValueError("record_sha256 does not match canonical completion record")
        return record


def _revalidate_record(record: object) -> CompletionRecord:
    if type(record) is not CompletionRecord:
        raise ValueError("completions must contain exact CompletionRecord values")
    return CompletionRecord.from_mapping(record.to_mapping())


class CompletionSigner:
    """Executor-owned one-time signing capability.

    Create this in the isolated executor process with :meth:`generate`. Only
    ``public_commitment`` is preregistered. The private key is never returned by
    ``DispatchAuthority``. Copy/pickle guards prevent accidental duplication but do
    not defend against a malicious process that can inspect its own memory.
    """

    __slots__ = ("__private_key", "__public_key", "__used", "__lock")

    def __init__(self, private_key: bytes | None = None, *, _factory: object | None = None) -> None:
        if _factory is not _SIGNER_FACTORY or type(private_key) is not bytes or len(private_key) != _OTS_PRIVATE_BYTES:
            raise TypeError("use CompletionSigner.generate() inside the executor process")
        self.__private_key = bytearray(private_key)
        self.__public_key = _ots_public_key(private_key)
        self.__used = False
        self.__lock = threading.Lock()

    @classmethod
    def generate(cls) -> "CompletionSigner":
        return cls(secrets.token_bytes(_OTS_PRIVATE_BYTES), _factory=_SIGNER_FACTORY)

    @property
    def public_commitment(self) -> str:
        return _ots_public_commitment(self.__public_key)

    def __copy__(self) -> "CompletionSigner":
        raise TypeError("CompletionSigner must not be copied; isolate it in the executor process")

    def __deepcopy__(self, memo: dict[int, object]) -> "CompletionSigner":
        raise TypeError("CompletionSigner must not be copied; isolate it in the executor process")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("CompletionSigner must not be serialized; isolate it in the executor process")

    def complete(self, *, entry: DispatchEntry, payload: CompletionPayload) -> CompletionRecord:
        entry = _revalidate_entry(entry)
        payload = _revalidate_payload(payload)
        request = payload.request
        if (
            entry.run_id != request.run_id
            or entry.problem_id != request.problem_id
            or entry.arm != request.arm
            or entry.attempt_index != request.attempt
            or entry.request_sha256 != request.frozen_request_sha256
        ):
            raise ValueError("typed completion payload does not match registered frozen request")
        if not hmac.compare_digest(self.public_commitment, entry.completion_verifier_sha256):
            raise ValueError("executor signer does not match preregistered verifier commitment")
        body = CompletionRecord._body(entry, payload)
        message = _canon("supernova.dispatch.completion.v3", body)
        with self.__lock:
            if self.__used:
                raise ValueError("completion signer is one-shot")
            self.__used = True
            key_bytes = bytes(self.__private_key)
            for i in range(len(self.__private_key)):
                self.__private_key[i] = 0
            signature = _ots_sign(key_bytes, message)
        return CompletionRecord(
            entry.run_id,
            entry.dispatch_id,
            entry.entry_sha256,
            payload,
            self.__public_key.hex(),
            signature,
        )


@dataclass(frozen=True)
class JoinedCompletion:
    dispatch: DispatchEntry
    completion: CompletionRecord

    def __post_init__(self) -> None:
        dispatch = _revalidate_entry(self.dispatch)
        completion = _revalidate_record(self.completion)
        if completion.run_id != dispatch.run_id or completion.dispatch_id != dispatch.dispatch_id or completion.entry_sha256 != dispatch.entry_sha256:
            raise ValueError("joined completion does not match dispatch identity")
        object.__setattr__(self, "dispatch", dispatch)
        object.__setattr__(self, "completion", completion)


@dataclass(frozen=True)
class CloseReceipt:
    run_id: str
    manifest_sha256: str
    completion_set_sha256: str
    close_sha256: str

    @classmethod
    def create(cls, run_id: str, manifest_sha256: str, completion_set_sha256: str) -> "CloseReceipt":
        run_id = _s(run_id, "run_id")
        manifest_sha256 = _hex(manifest_sha256, "manifest_sha256")
        completion_set_sha256 = _hex(completion_set_sha256, "completion_set_sha256")
        close_sha = _digest("supernova.dispatch.close.v3", {
            "run_id": run_id,
            "manifest_sha256": manifest_sha256,
            "completion_set_sha256": completion_set_sha256,
        })
        return cls(run_id, manifest_sha256, completion_set_sha256, close_sha)

    def __post_init__(self) -> None:
        run_id = _s(self.run_id, "run_id")
        manifest_sha = _hex(self.manifest_sha256, "manifest_sha256")
        completion_set_sha = _hex(self.completion_set_sha256, "completion_set_sha256")
        expected = _digest("supernova.dispatch.close.v3", {
            "run_id": run_id,
            "manifest_sha256": manifest_sha,
            "completion_set_sha256": completion_set_sha,
        })
        if self.close_sha256 != expected:
            raise ValueError("close_sha256 does not match canonical close receipt")


@dataclass(frozen=True)
class CompletionJoin:
    joined: tuple[JoinedCompletion, ...]
    receipt: CloseReceipt

    def __post_init__(self) -> None:
        if type(self.joined) is not tuple or not self.joined:
            raise ValueError("closed join must contain an immutable non-empty completion tuple")
        canonical_joined = []
        for item in self.joined:
            if type(item) is not JoinedCompletion:
                raise ValueError("joined values must be exact JoinedCompletion")
            canonical_joined.append(JoinedCompletion(item.dispatch, item.completion))
        joined = tuple(canonical_joined)
        manifest = DispatchManifest(self.receipt.run_id, tuple(j.dispatch for j in joined))
        if manifest.manifest_sha256 != self.receipt.manifest_sha256:
            raise ValueError("closed join manifest does not match close receipt")
        completion_set = _completion_set_digest(manifest.manifest_sha256, joined)
        if completion_set != self.receipt.completion_set_sha256:
            raise ValueError("closed join completion set does not match close receipt")
        object.__setattr__(self, "joined", joined)


@dataclass(frozen=True)
class AbortReceipt:
    run_id: str
    manifest_sha256: str
    reason: str
    abort_sha256: str

    @classmethod
    def create(cls, run_id: str, manifest_sha256: str, reason: str) -> "AbortReceipt":
        run_id = _s(run_id, "run_id")
        manifest_sha256 = _hex(manifest_sha256, "manifest_sha256")
        reason = _s(reason, "abort reason")
        abort_sha = _digest("supernova.dispatch.abort.v1", {
            "run_id": run_id,
            "manifest_sha256": manifest_sha256,
            "reason": reason,
            "credit": "NON_CREDIT",
        })
        return cls(run_id, manifest_sha256, reason, abort_sha)

    def __post_init__(self) -> None:
        expected = _digest(
            "supernova.dispatch.abort.v1",
            {
                "run_id": _s(self.run_id, "run_id"),
                "manifest_sha256": _hex(self.manifest_sha256, "manifest_sha256"),
                "reason": _s(self.reason, "abort reason"),
                "credit": "NON_CREDIT",
            },
        )
        if self.abort_sha256 != expected:
            raise ValueError("abort_sha256 does not match canonical non-credit abort receipt")


def _completion_set_digest(manifest_sha256: str, joined: tuple[JoinedCompletion, ...]) -> str:
    return _digest("supernova.dispatch.completion-set.v3", {
        "manifest_sha256": manifest_sha256,
        "record_sha256": [j.completion.record_sha256 for j in joined],
    })


class DispatchAuthority:
    """SQLite-backed monotonic authority for registration, close, readback and abort."""

    def __init__(self, db_path: str | os.PathLike[str], run_id: str) -> None:
        path_text = os.fspath(db_path)
        if not path_text or path_text == ":memory:" or path_text.startswith("file:"):
            raise ValueError("db_path must name an absolute durable filesystem SQLite database")
        path = Path(path_text)
        if not path.is_absolute():
            raise ValueError("db_path must be absolute; relative trust-root paths are forbidden")
        self.db_path = str(path.resolve(strict=False))
        self.run_id = _s(run_id, "run_id")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS dispatch_state (
                run_id TEXT PRIMARY KEY,
                manifest_sha256 TEXT NOT NULL,
                terminal_state TEXT NOT NULL CHECK (terminal_state IN ('OPEN','CLOSED','ABORTED')),
                completion_set_sha256 TEXT,
                close_sha256 TEXT,
                abort_reason TEXT,
                abort_sha256 TEXT
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS dispatch_entries (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                problem_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                attempt_index INTEGER NOT NULL,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                completion_verifier_sha256 TEXT NOT NULL,
                predecessor_sha256 TEXT NOT NULL,
                dispatch_id TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence),
                UNIQUE (run_id, dispatch_id),
                UNIQUE (run_id, completion_verifier_sha256)
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS dispatch_completions (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                dispatch_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                record_sha256 TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence),
                UNIQUE (run_id, dispatch_id)
            )""")
            empty = DispatchManifest.empty(self.run_id)
            con.execute(
                "INSERT OR IGNORE INTO dispatch_state(run_id,manifest_sha256,terminal_state) VALUES(?,?,?)",
                (self.run_id, empty.manifest_sha256, RunTerminalState.OPEN.value),
            )
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def _state(self, con: sqlite3.Connection) -> tuple[str, RunTerminalState, str | None, str | None, str | None, str | None]:
        row = con.execute(
            "SELECT manifest_sha256,terminal_state,completion_set_sha256,close_sha256,abort_reason,abort_sha256 FROM dispatch_state WHERE run_id=?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("authority state missing")
        return row[0], RunTerminalState(row[1]), row[2], row[3], row[4], row[5]

    def terminal_state(self) -> RunTerminalState:
        con = self._connect()
        try:
            return self._state(con)[1]
        finally:
            con.close()

    def _requests_from_db(self, con: sqlite3.Connection) -> dict[str, FrozenProblemRequest]:
        rows = con.execute(
            "SELECT dispatch_id,request_json FROM dispatch_entries WHERE run_id=? ORDER BY sequence",
            (self.run_id,),
        ).fetchall()
        result: dict[str, FrozenProblemRequest] = {}
        for dispatch_id, request_json in rows:
            raw = json.loads(request_json)
            request = FrozenProblemRequest.from_mapping(raw)
            result[dispatch_id] = request
        return result

    def _manifest_from_db(self, con: sqlite3.Connection) -> DispatchManifest:
        rows = con.execute(
            """SELECT sequence,problem_id,arm,attempt_index,request_sha256,request_json,
            completion_verifier_sha256,predecessor_sha256,dispatch_id,entry_sha256
            FROM dispatch_entries WHERE run_id=? ORDER BY sequence""",
            (self.run_id,),
        ).fetchall()
        entries = []
        for row in rows:
            sequence, problem_id, arm, attempt_index, request_sha256, request_json, verifier_sha, predecessor_sha, stored_dispatch, stored_entry = row
            raw_request = json.loads(request_json)
            request = FrozenProblemRequest.from_mapping(raw_request)
            if (
                request.run_id != self.run_id
                or request.problem_id != problem_id
                or request.arm != _arm(arm)
                or request.attempt != attempt_index
                or request.frozen_request_sha256 != request_sha256
            ):
                raise ValueError("authority request storage does not match typed frozen request")
            entry = DispatchEntry.create(
                run_id=self.run_id,
                sequence=sequence,
                problem_id=problem_id,
                arm=arm,
                attempt_index=attempt_index,
                request_sha256=request_sha256,
                completion_verifier_sha256=verifier_sha,
                predecessor_sha256=predecessor_sha,
            )
            if entry.dispatch_id != stored_dispatch or entry.entry_sha256 != stored_entry:
                raise ValueError("authority entry storage is not canonical")
            entries.append(entry)
        manifest = DispatchManifest(self.run_id, tuple(entries))
        state_head, _, _, _, _, _ = self._state(con)
        if not hmac.compare_digest(manifest.manifest_sha256, state_head):
            raise ValueError("authority manifest head does not match persisted entries")
        return manifest

    def current_manifest(self) -> DispatchManifest:
        con = self._connect()
        try:
            return self._manifest_from_db(con)
        finally:
            con.close()

    def register(
        self,
        manifest: DispatchManifest,
        *,
        request: FrozenProblemRequest,
        completion_verifier_sha256: str,
    ) -> DispatchManifest:
        supplied = _revalidate_manifest(manifest)
        request = _snapshot_request(request)
        verifier_sha = _hex(completion_verifier_sha256, "completion_verifier_sha256")
        if request.run_id != self.run_id:
            raise ValueError("frozen request run_id does not match dispatch authority")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = self._manifest_from_db(con)
            head, state, _, _, _, _ = self._state(con)
            if state is not RunTerminalState.OPEN:
                raise ValueError(f"run is terminal: {state.value}")
            if supplied != current or not hmac.compare_digest(supplied.manifest_sha256, head):
                raise ValueError("stale or forked manifest cannot advance authoritative head")
            updated = current._append_request(request, verifier_sha)
            entry = updated.entries[-1]
            request_json = json.dumps(request.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            con.execute(
                """INSERT INTO dispatch_entries(run_id,sequence,problem_id,arm,attempt_index,
                request_sha256,request_json,completion_verifier_sha256,predecessor_sha256,
                dispatch_id,entry_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry.run_id,
                    entry.sequence,
                    entry.problem_id,
                    entry.arm.value,
                    entry.attempt_index,
                    entry.request_sha256,
                    request_json,
                    entry.completion_verifier_sha256,
                    entry.predecessor_sha256,
                    entry.dispatch_id,
                    entry.entry_sha256,
                ),
            )
            cur = con.execute(
                "UPDATE dispatch_state SET manifest_sha256=? WHERE run_id=? AND manifest_sha256=? AND terminal_state='OPEN'",
                (updated.manifest_sha256, self.run_id, head),
            )
            if cur.rowcount != 1:
                raise ValueError("authoritative manifest CAS failed")
            con.execute("COMMIT")
            return updated
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def _validate_record_for_entry(
        self,
        con: sqlite3.Connection,
        entry: DispatchEntry,
        record: CompletionRecord,
        request: FrozenProblemRequest,
    ) -> CompletionRecord:
        record = _revalidate_record(record)
        if record.run_id != entry.run_id or record.dispatch_id != entry.dispatch_id or record.entry_sha256 != entry.entry_sha256:
            raise ValueError("completion does not match pre-dispatch identity")
        payload = record.payload
        if payload.request != request:
            raise ValueError("completion payload request does not match authority-stored frozen request")
        if (
            request.frozen_request_sha256 != entry.request_sha256
            or request.problem_id != entry.problem_id
            or request.arm != entry.arm
            or request.attempt != entry.attempt_index
        ):
            raise ValueError("authority-stored request does not match dispatch binding")
        public_key = bytes.fromhex(_long_hex(record.verifier_public_key, "verifier_public_key", _OTS_PRIVATE_BYTES))
        if not hmac.compare_digest(_ots_public_commitment(public_key), entry.completion_verifier_sha256):
            raise ValueError("completion verifier public key does not match preregistered commitment")
        if not _ots_verify(
            public_key,
            _canon("supernova.dispatch.completion.v3", CompletionRecord._body(entry, payload)),
            record.signature,
        ):
            raise ValueError("completion signature verification failed")
        return record

    def close(self, manifest: DispatchManifest, completions: Iterable[CompletionRecord]) -> CompletionJoin:
        supplied = _revalidate_manifest(manifest)
        records = tuple(_revalidate_record(r) for r in tuple(completions))
        if not supplied.entries:
            raise ValueError("cannot close an empty pre-dispatch manifest")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = self._manifest_from_db(con)
            head, state, _, _, _, _ = self._state(con)
            if state is RunTerminalState.CLOSED:
                raise ValueError("authoritative close receipt already consumed")
            if state is RunTerminalState.ABORTED:
                raise ValueError("aborted run cannot be closed")
            if supplied != current or not hmac.compare_digest(supplied.manifest_sha256, head):
                raise ValueError("manifest does not match authoritative latest pre-dispatch head")
            expected = {entry.dispatch_id: entry for entry in current.entries}
            requests = self._requests_from_db(con)
            by_dispatch: dict[str, CompletionRecord] = {}
            for record in records:
                if record.dispatch_id in by_dispatch:
                    raise ValueError("replayed completion: dispatch completed more than once")
                entry = expected.get(record.dispatch_id)
                if entry is None:
                    raise ValueError("fabricated completion references an unregistered dispatch")
                request = requests[record.dispatch_id]
                by_dispatch[record.dispatch_id] = self._validate_record_for_entry(con, entry, record, request)
            missing = [entry.dispatch_id for entry in current.entries if entry.dispatch_id not in by_dispatch]
            if missing:
                raise ValueError("omitted dispatch completions: " + ", ".join(missing))
            ordered = tuple(JoinedCompletion(entry, by_dispatch[entry.dispatch_id]) for entry in current.entries)
            completion_set_sha = _completion_set_digest(head, ordered)
            receipt = CloseReceipt.create(self.run_id, head, completion_set_sha)
            for joined in ordered:
                record_json = json.dumps(joined.completion.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                con.execute(
                    "INSERT INTO dispatch_completions(run_id,sequence,dispatch_id,record_json,record_sha256) VALUES(?,?,?,?,?)",
                    (self.run_id, joined.dispatch.sequence, joined.dispatch.dispatch_id, record_json, joined.completion.record_sha256),
                )
            cur = con.execute(
                """UPDATE dispatch_state SET terminal_state='CLOSED',completion_set_sha256=?,close_sha256=?
                WHERE run_id=? AND manifest_sha256=? AND terminal_state='OPEN'""",
                (completion_set_sha, receipt.close_sha256, self.run_id, head),
            )
            if cur.rowcount != 1:
                raise ValueError("authoritative close CAS failed")
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.read_closed_join()

    def read_closed_join(self) -> CompletionJoin:
        con = self._connect()
        try:
            manifest = self._manifest_from_db(con)
            head, state, completion_set_sha, close_sha, _, _ = self._state(con)
            if state is not RunTerminalState.CLOSED:
                raise ValueError("run has no authoritative closed join")
            if completion_set_sha is None or close_sha is None:
                raise ValueError("closed authority state is missing receipt digests")
            requests = self._requests_from_db(con)
            rows = con.execute(
                "SELECT sequence,dispatch_id,record_json,record_sha256 FROM dispatch_completions WHERE run_id=? ORDER BY sequence",
                (self.run_id,),
            ).fetchall()
            if len(rows) != len(manifest.entries):
                raise ValueError("persisted completion set is incomplete")
            joined_values = []
            for entry, row in zip(manifest.entries, rows):
                sequence, dispatch_id, record_json, stored_record_sha = row
                if sequence != entry.sequence or dispatch_id != entry.dispatch_id:
                    raise ValueError("persisted completion order does not match manifest")
                raw = json.loads(record_json)
                record = CompletionRecord.from_mapping(raw)
                if record.record_sha256 != stored_record_sha:
                    raise ValueError("persisted completion record digest mismatch")
                record = self._validate_record_for_entry(con, entry, record, requests[entry.dispatch_id])
                joined_values.append(JoinedCompletion(entry, record))
            joined = tuple(joined_values)
            calculated_set = _completion_set_digest(head, joined)
            if calculated_set != completion_set_sha:
                raise ValueError("persisted completion set does not match authoritative receipt")
            receipt = CloseReceipt.create(self.run_id, head, completion_set_sha)
            if receipt.close_sha256 != close_sha:
                raise ValueError("persisted close digest does not match authoritative receipt")
            return CompletionJoin(joined, receipt)
        finally:
            con.close()

    def verify_closed_join(self, candidate: CompletionJoin) -> CompletionJoin:
        if type(candidate) is not CompletionJoin:
            raise ValueError("candidate must be an exact CompletionJoin")
        authoritative = self.read_closed_join()
        if candidate != authoritative:
            raise ValueError("candidate join is not the authority-backed persisted close")
        return authoritative

    def abort(self, reason: str) -> AbortReceipt:
        reason = _s(reason, "abort reason")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            manifest = self._manifest_from_db(con)
            head, state, _, _, _, _ = self._state(con)
            if state is not RunTerminalState.OPEN:
                raise ValueError(f"run is already terminal: {state.value}")
            receipt = AbortReceipt.create(self.run_id, manifest.manifest_sha256, reason)
            cur = con.execute(
                """UPDATE dispatch_state SET terminal_state='ABORTED',abort_reason=?,abort_sha256=?
                WHERE run_id=? AND manifest_sha256=? AND terminal_state='OPEN'""",
                (reason, receipt.abort_sha256, self.run_id, head),
            )
            if cur.rowcount != 1:
                raise ValueError("authoritative abort CAS failed")
            con.execute("COMMIT")
            return receipt
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def read_abort_receipt(self) -> AbortReceipt:
        con = self._connect()
        try:
            head, state, _, _, reason, abort_sha = self._state(con)
            if state is not RunTerminalState.ABORTED or reason is None or abort_sha is None:
                raise ValueError("run has no authoritative abort receipt")
            receipt = AbortReceipt.create(self.run_id, head, reason)
            if receipt.abort_sha256 != abort_sha:
                raise ValueError("persisted abort digest does not match authoritative receipt")
            return receipt
        finally:
            con.close()


def join_completions(
    authority: DispatchAuthority,
    manifest: DispatchManifest,
    completions: Iterable[CompletionRecord],
) -> CompletionJoin:
    """Consume exactly one complete join through the retained monotonic authority."""
    if type(authority) is not DispatchAuthority:
        raise ValueError("authority must be an exact DispatchAuthority")
    return authority.close(manifest, completions)


__all__ = [
    "AbortReceipt",
    "CloseReceipt",
    "CompletionJoin",
    "CompletionPayload",
    "CompletionRecord",
    "CompletionSigner",
    "CompletionStatus",
    "DispatchAuthority",
    "DispatchEntry",
    "DispatchManifest",
    "JoinedCompletion",
    "RunTerminalState",
    "join_completions",
]
