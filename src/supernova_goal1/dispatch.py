"""Durable pre-dispatch registration and one-shot completion joining."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping

from supernova_goal1.contracts import Arm

_ZERO = "0" * 64
_OTS_SECRET_BYTES = 32
_OTS_BITS = 256
_OTS_PRIVATE_BYTES = _OTS_BITS * 2 * _OTS_SECRET_BYTES
_OTS_SIGNATURE_BYTES = _OTS_BITS * _OTS_SECRET_BYTES


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
    return hashlib.sha256(b"supernova.dispatch.ots-public.v1\0" + public_key).hexdigest()


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


class CompletionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


def _status(v: object) -> CompletionStatus:
    if type(v) is CompletionStatus:
        return v
    if type(v) is str:
        try:
            return CompletionStatus(v)
        except ValueError as exc:
            raise ValueError(f"unknown completion status: {v!r}") from exc
    raise ValueError("status must be an exact CompletionStatus or plain status string")


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
    def create(cls, *, run_id: str, sequence: int, problem_id: str, arm: Arm | str,
               attempt_index: int, request_sha256: str, completion_verifier_sha256: str,
               predecessor_sha256: str) -> "DispatchEntry":
        run_id, problem_id = _s(run_id, "run_id"), _s(problem_id, "problem_id")
        sequence, attempt_index = _n(sequence, "sequence"), _n(attempt_index, "attempt_index")
        arm = _arm(arm)
        request_sha256 = _hex(request_sha256, "request_sha256")
        completion_verifier_sha256 = _hex(completion_verifier_sha256, "completion_verifier_sha256")
        predecessor_sha256 = _hex(predecessor_sha256, "predecessor_sha256")
        identity = {
            "run_id": run_id, "problem_id": problem_id, "arm": arm.value,
            "attempt_index": attempt_index, "request_sha256": request_sha256,
            "completion_verifier_sha256": completion_verifier_sha256,
        }
        dispatch_id = _digest("supernova.dispatch.id.v2", identity)
        entry_sha256 = _digest("supernova.dispatch.entry.v2", {
            "sequence": sequence, "predecessor_sha256": predecessor_sha256,
            "dispatch_id": dispatch_id,
        })
        return cls(run_id, sequence, problem_id, arm, attempt_index, request_sha256,
                   completion_verifier_sha256, predecessor_sha256, dispatch_id, entry_sha256)

    def __post_init__(self) -> None:
        run_id, problem_id = _s(self.run_id, "run_id"), _s(self.problem_id, "problem_id")
        sequence, attempt_index = _n(self.sequence, "sequence"), _n(self.attempt_index, "attempt_index")
        arm = _arm(self.arm)
        request_sha256 = _hex(self.request_sha256, "request_sha256")
        completion_verifier_sha256 = _hex(self.completion_verifier_sha256, "completion_verifier_sha256")
        predecessor_sha256 = _hex(self.predecessor_sha256, "predecessor_sha256")
        dispatch_id = _digest("supernova.dispatch.id.v2", {
            "run_id": run_id, "problem_id": problem_id, "arm": arm.value,
            "attempt_index": attempt_index, "request_sha256": request_sha256,
            "completion_verifier_sha256": completion_verifier_sha256,
        })
        entry_sha256 = _digest("supernova.dispatch.entry.v2", {
            "sequence": sequence, "predecessor_sha256": predecessor_sha256,
            "dispatch_id": dispatch_id,
        })
        if self.dispatch_id != dispatch_id:
            raise ValueError("dispatch_id does not match canonical pre-dispatch content")
        if self.entry_sha256 != entry_sha256:
            raise ValueError("entry_sha256 does not match append-only hash chain")
        object.__setattr__(self, "arm", arm)

    @property
    def logical_key(self) -> tuple[str, Arm, int]:
        return self.problem_id, self.arm, self.attempt_index


def _revalidate_entry(entry: object) -> DispatchEntry:
    if type(entry) is not DispatchEntry:
        raise ValueError("entries must contain exact DispatchEntry values")
    canonical = DispatchEntry.create(
        run_id=entry.run_id, sequence=entry.sequence, problem_id=entry.problem_id,
        arm=entry.arm, attempt_index=entry.attempt_index, request_sha256=entry.request_sha256,
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
            logical.add(entry.logical_key); ids.add(entry.dispatch_id); verifiers.add(entry.completion_verifier_sha256)
            predecessor = entry.entry_sha256
        object.__setattr__(self, "entries", tuple(canonical_entries))

    @classmethod
    def empty(cls, run_id: str) -> "DispatchManifest":
        return cls(_s(run_id, "run_id"), ())

    def append(self, *, problem_id: str, arm: Arm | str, attempt_index: int,
               request_sha256: str, completion_verifier_sha256: str) -> "DispatchManifest":
        entry = DispatchEntry.create(
            run_id=self.run_id, sequence=len(self.entries), problem_id=problem_id, arm=arm,
            attempt_index=attempt_index, request_sha256=request_sha256,
            completion_verifier_sha256=completion_verifier_sha256,
            predecessor_sha256=self.entries[-1].entry_sha256 if self.entries else _ZERO,
        )
        return DispatchManifest(self.run_id, self.entries + (entry,))

    @property
    def manifest_sha256(self) -> str:
        canonical = _revalidate_manifest(self)
        return _digest("supernova.dispatch.manifest.v2", {
            "run_id": canonical.run_id,
            "entry_sha256": [entry.entry_sha256 for entry in canonical.entries],
        })


def _revalidate_manifest(manifest: object) -> DispatchManifest:
    if type(manifest) is not DispatchManifest:
        raise ValueError("manifest must be an exact DispatchManifest")
    return DispatchManifest(manifest.run_id, tuple(_revalidate_entry(e) for e in manifest.entries))


@dataclass(frozen=True)
class CompletionRecord:
    run_id: str
    dispatch_id: str
    entry_sha256: str
    problem_id: str
    arm: Arm
    attempt_index: int
    request_sha256: str
    status: CompletionStatus
    payload_sha256: str
    signature: str

    @staticmethod
    def _body(entry: DispatchEntry, status: CompletionStatus, payload_sha256: str) -> dict[str, object]:
        return {
            "run_id": entry.run_id, "dispatch_id": entry.dispatch_id,
            "entry_sha256": entry.entry_sha256, "problem_id": entry.problem_id,
            "arm": entry.arm.value, "attempt_index": entry.attempt_index,
            "request_sha256": entry.request_sha256, "status": status.value,
            "payload_sha256": payload_sha256,
        }

    def __post_init__(self) -> None:
        _s(self.run_id, "run_id"); _hex(self.dispatch_id, "dispatch_id"); _hex(self.entry_sha256, "entry_sha256")
        _s(self.problem_id, "problem_id"); object.__setattr__(self, "arm", _arm(self.arm))
        _n(self.attempt_index, "attempt_index"); _hex(self.request_sha256, "request_sha256")
        object.__setattr__(self, "status", _status(self.status)); _hex(self.payload_sha256, "payload_sha256")
        _long_hex(self.signature, "signature", _OTS_SIGNATURE_BYTES)


def _revalidate_record(record: object) -> CompletionRecord:
    if type(record) is not CompletionRecord:
        raise ValueError("completions must contain exact CompletionRecord values")
    return CompletionRecord(record.run_id, record.dispatch_id, record.entry_sha256, record.problem_id,
                            record.arm, record.attempt_index, record.request_sha256, record.status,
                            record.payload_sha256, record.signature)


class CompletionSigner:
    """One-shot executor capability; the closer never receives its private key."""
    __slots__ = ("__entry", "__private_key", "__used")

    def __init__(self, entry: DispatchEntry, private_key: bytes) -> None:
        self.__entry = _revalidate_entry(entry)
        if type(private_key) is not bytes or len(private_key) != _OTS_PRIVATE_BYTES:
            raise ValueError("invalid one-time completion signing key")
        if not hmac.compare_digest(_ots_public_commitment(_ots_public_key(private_key)), entry.completion_verifier_sha256):
            raise ValueError("completion signer does not match registered verifier commitment")
        self.__private_key = private_key
        self.__used = False

    @property
    def dispatch_id(self) -> str:
        return self.__entry.dispatch_id

    def complete(self, *, status: CompletionStatus | str, payload_sha256: str) -> CompletionRecord:
        if self.__used:
            raise ValueError("completion signer is one-shot")
        entry = _revalidate_entry(self.__entry)
        status, payload_sha256 = _status(status), _hex(payload_sha256, "payload_sha256")
        body = CompletionRecord._body(entry, status, payload_sha256)
        signature = _ots_sign(self.__private_key, _canon("supernova.dispatch.completion.v2", body))
        self.__used = True
        return CompletionRecord(entry.run_id, entry.dispatch_id, entry.entry_sha256, entry.problem_id,
                                entry.arm, entry.attempt_index, entry.request_sha256, status,
                                payload_sha256, signature)


@dataclass(frozen=True)
class JoinedCompletion:
    dispatch: DispatchEntry
    completion: CompletionRecord


@dataclass(frozen=True)
class CloseReceipt:
    run_id: str
    manifest_sha256: str
    completion_set_sha256: str
    close_sha256: str


@dataclass(frozen=True)
class CompletionJoin:
    joined: tuple[JoinedCompletion, ...]
    receipt: CloseReceipt


class DispatchAuthority:
    """SQLite-backed monotonic authority for registration and one-shot closure.

    The database path is the trust root and must be retained outside producer control.
    Registration uses BEGIN IMMEDIATE plus a head comparison, so stale/forked manifests
    cannot advance state. The database stores only one-time *public* completion
    verification material, never executor signing secrets. Closure atomically consumes
    the current head and persists a close receipt, so successful close replay fails even
    after reopening the authority.
    """

    def __init__(self, db_path: str | os.PathLike[str], run_id: str) -> None:
        path = os.fspath(db_path)
        if not path or path == ":memory:" or path.startswith("file:"):
            raise ValueError("db_path must name a durable filesystem SQLite database")
        self.db_path = str(Path(path))
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
                run_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
                closed INTEGER NOT NULL CHECK (closed IN (0,1)),
                completion_set_sha256 TEXT, close_sha256 TEXT
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS dispatch_entries (
                run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                problem_id TEXT NOT NULL, arm TEXT NOT NULL, attempt_index INTEGER NOT NULL,
                request_sha256 TEXT NOT NULL, completion_verifier_sha256 TEXT NOT NULL,
                predecessor_sha256 TEXT NOT NULL, dispatch_id TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL, verifier_public_key BLOB NOT NULL,
                PRIMARY KEY (run_id, sequence), UNIQUE (run_id, dispatch_id),
                UNIQUE (run_id, completion_verifier_sha256)
            )""")
            empty = DispatchManifest.empty(self.run_id)
            con.execute("INSERT OR IGNORE INTO dispatch_state(run_id,manifest_sha256,closed) VALUES(?,?,0)",
                        (self.run_id, _manifest_digest(empty)))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def _state(self, con: sqlite3.Connection) -> tuple[str, int, str | None, str | None]:
        row = con.execute("SELECT manifest_sha256,closed,completion_set_sha256,close_sha256 FROM dispatch_state WHERE run_id=?",
                          (self.run_id,)).fetchone()
        if row is None:
            raise ValueError("authority state missing")
        return row[0], int(row[1]), row[2], row[3]

    def _manifest_from_db(self, con: sqlite3.Connection) -> DispatchManifest:
        rows = con.execute("""SELECT sequence,problem_id,arm,attempt_index,request_sha256,
            completion_verifier_sha256,predecessor_sha256,dispatch_id,entry_sha256,verifier_public_key
            FROM dispatch_entries WHERE run_id=? ORDER BY sequence""", (self.run_id,)).fetchall()
        entries = []
        for row in rows:
            sequence, problem_id, arm, attempt_index, request_sha256, verifier_sha, predecessor_sha, stored_dispatch, stored_entry, public_key = row
            if not hmac.compare_digest(_ots_public_commitment(public_key), verifier_sha):
                raise ValueError("authority verifier material does not match stored commitment")
            entry = DispatchEntry.create(run_id=self.run_id, sequence=sequence, problem_id=problem_id,
                                         arm=arm, attempt_index=attempt_index, request_sha256=request_sha256,
                                         completion_verifier_sha256=verifier_sha,
                                         predecessor_sha256=predecessor_sha)
            if entry.dispatch_id != stored_dispatch or entry.entry_sha256 != stored_entry:
                raise ValueError("authority entry storage is not canonical")
            entries.append(entry)
        manifest = DispatchManifest(self.run_id, tuple(entries))
        state_head, _, _, _ = self._state(con)
        if not hmac.compare_digest(_manifest_digest(manifest), state_head):
            raise ValueError("authority manifest head does not match persisted entries")
        return manifest

    def current_manifest(self) -> DispatchManifest:
        con = self._connect()
        try:
            return self._manifest_from_db(con)
        finally:
            con.close()

    def register(self, manifest: DispatchManifest, *, problem_id: str, arm: Arm | str,
                 attempt_index: int, request_sha256: str) -> tuple[DispatchManifest, CompletionSigner]:
        supplied = _revalidate_manifest(manifest)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = self._manifest_from_db(con)
            head, closed, _, _ = self._state(con)
            if closed:
                raise ValueError("run is already closed")
            if supplied != current or not hmac.compare_digest(_manifest_digest(supplied), head):
                raise ValueError("stale or forked manifest cannot advance authoritative head")
            private_key = secrets.token_bytes(_OTS_PRIVATE_BYTES)
            public_key = _ots_public_key(private_key)
            verifier_sha = _ots_public_commitment(public_key)
            updated = current.append(problem_id=problem_id, arm=arm, attempt_index=attempt_index,
                                     request_sha256=request_sha256,
                                     completion_verifier_sha256=verifier_sha)
            entry = updated.entries[-1]
            con.execute("""INSERT INTO dispatch_entries(run_id,sequence,problem_id,arm,attempt_index,
                request_sha256,completion_verifier_sha256,predecessor_sha256,dispatch_id,entry_sha256,
                verifier_public_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (entry.run_id, entry.sequence, entry.problem_id, entry.arm.value, entry.attempt_index,
                 entry.request_sha256, entry.completion_verifier_sha256, entry.predecessor_sha256,
                 entry.dispatch_id, entry.entry_sha256, public_key))
            cur = con.execute("UPDATE dispatch_state SET manifest_sha256=? WHERE run_id=? AND manifest_sha256=? AND closed=0",
                              (_manifest_digest(updated), self.run_id, head))
            if cur.rowcount != 1:
                raise ValueError("authoritative manifest CAS failed")
            con.execute("COMMIT")
            return updated, CompletionSigner(entry, private_key)
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def close(self, manifest: DispatchManifest, completions: Iterable[CompletionRecord]) -> CompletionJoin:
        supplied = _revalidate_manifest(manifest)
        records = tuple(_revalidate_record(r) for r in tuple(completions))
        if not supplied.entries:
            raise ValueError("cannot close an empty pre-dispatch manifest")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = self._manifest_from_db(con)
            head, closed, _, _ = self._state(con)
            if closed:
                raise ValueError("authoritative close receipt already consumed")
            if supplied != current or not hmac.compare_digest(_manifest_digest(supplied), head):
                raise ValueError("manifest does not match authoritative latest pre-dispatch head")
            expected = {entry.dispatch_id: entry for entry in current.entries}
            by_dispatch: dict[str, CompletionRecord] = {}
            for record in records:
                if record.dispatch_id in by_dispatch:
                    raise ValueError("replayed completion: dispatch completed more than once")
                entry = expected.get(record.dispatch_id)
                if entry is None:
                    raise ValueError("fabricated completion references an unregistered dispatch")
                fields = ("run_id", "entry_sha256", "problem_id", "arm", "attempt_index", "request_sha256")
                bad = [name for name in fields if getattr(record, name) != getattr(entry, name)]
                if bad:
                    raise ValueError("completion does not match pre-dispatch binding: " + ", ".join(bad))
                row = con.execute("SELECT verifier_public_key FROM dispatch_entries WHERE run_id=? AND dispatch_id=?",
                                  (self.run_id, entry.dispatch_id)).fetchone()
                if row is None or not _ots_verify(
                    row[0],
                    _canon(
                        "supernova.dispatch.completion.v2",
                        CompletionRecord._body(entry, record.status, record.payload_sha256),
                    ),
                    record.signature,
                ):
                    raise ValueError("completion signature verification failed")
                by_dispatch[record.dispatch_id] = record
            missing = [entry.dispatch_id for entry in current.entries if entry.dispatch_id not in by_dispatch]
            if missing:
                raise ValueError("omitted dispatch completions: " + ", ".join(missing))
            ordered = tuple(JoinedCompletion(entry, by_dispatch[entry.dispatch_id]) for entry in current.entries)
            completion_set_sha = _digest("supernova.dispatch.completion-set.v2", {
                "manifest_sha256": head,
                "records": [
                    {
                        "dispatch_id": j.completion.dispatch_id,
                        "signature_sha256": hashlib.sha256(bytes.fromhex(j.completion.signature)).hexdigest(),
                    }
                    for j in ordered
                ],
            })
            close_sha = _digest("supernova.dispatch.close.v2", {
                "run_id": self.run_id, "manifest_sha256": head,
                "completion_set_sha256": completion_set_sha,
            })
            cur = con.execute("""UPDATE dispatch_state SET closed=1,completion_set_sha256=?,close_sha256=?
                WHERE run_id=? AND manifest_sha256=? AND closed=0""",
                (completion_set_sha, close_sha, self.run_id, head))
            if cur.rowcount != 1:
                raise ValueError("authoritative close CAS failed")
            con.execute("COMMIT")
            return CompletionJoin(ordered, CloseReceipt(self.run_id, head, completion_set_sha, close_sha))
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()


def _manifest_digest(manifest: DispatchManifest) -> str:
    canonical = _revalidate_manifest(manifest)
    return _digest("supernova.dispatch.manifest.v2", {
        "run_id": canonical.run_id,
        "entry_sha256": [entry.entry_sha256 for entry in canonical.entries],
    })


def join_completions(authority: DispatchAuthority, manifest: DispatchManifest,
                     completions: Iterable[CompletionRecord]) -> CompletionJoin:
    """Consume exactly one complete join through the retained monotonic authority."""
    if type(authority) is not DispatchAuthority:
        raise ValueError("authority must be an exact DispatchAuthority")
    return authority.close(manifest, completions)
