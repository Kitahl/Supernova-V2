from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .confirmatory_manifest import (
    ConfirmatoryManifestBundle,
    _build_authorized_confirmatory_manifest,
    assert_dispatch_authorized,
)
from .execution_authority import (
    AUTHORITY_RELATIVE_PATH,
    AUTHORIZED_DISPATCH_STATUS,
    ValidatedExecutionAuthority,
    _repository_root,
)

BLOCKED_NO_EXECUTION_AUTHORITY = "BLOCKED_NO_EXECUTION_AUTHORITY"
PROTOCOL_RELATIVE_PATH = Path("goal1") / "CONFIRMATORY_PROTOCOL.json"
GOAL1_RELATIVE_PATH = Path("goal1") / "GOAL1.json"
ACTIVATION_SCHEMA = "supernova.confirmatory-activation-record.v1"


@dataclass(frozen=True)
class ConfirmatoryActivation:
    """The complete handoff from the fixed frozen protocol to a sealed manifest."""

    protocol: dict[str, Any]
    manifest: ConfirmatoryManifestBundle
    authority: ValidatedExecutionAuthority


@dataclass(frozen=True)
class DurableConfirmatoryActivation:
    """Read-back-verified one-shot activation retained by the trusted host."""

    activation_id: str
    activation_record_sha256: str
    activation: ConfirmatoryActivation


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nonce_commitment(nonce: bytes) -> str:
    if type(nonce) is not bytes or len(nonce) != 32:
        raise ValueError("activation_nonce must be exactly 32 random bytes")
    return _sha256(b"supernova.confirmatory-activation-nonce.v1\0" + nonce)


def _json_copy(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TypeError(f"{field} must be an exact dict")
    return json.loads(json.dumps(value, allow_nan=False))


def _fixed_protocol() -> dict[str, Any]:
    path = _repository_root() / PROTOCOL_RELATIVE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _fixed_goal1() -> dict[str, Any]:
    path = _repository_root() / GOAL1_RELATIVE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _open_operational_gate(protocol: Mapping[str, Any]) -> dict[str, Any]:
    opened = _json_copy(protocol, "protocol")
    if opened != _fixed_protocol():
        raise PermissionError(
            "activation requires the exact checked-in frozen protocol"
        )
    gate = opened.get("execution_opening_gate")
    if type(gate) is not dict:
        raise ValueError("protocol execution_opening_gate must be an exact object")
    if (
        opened.get("confirmatory_execution_status") != BLOCKED_NO_EXECUTION_AUTHORITY
        or gate.get("state") != BLOCKED_NO_EXECUTION_AUTHORITY
        or gate.get("missing_artifact") != AUTHORITY_RELATIVE_PATH.as_posix()
        or gate.get("held_out_report_dispatch") != "BLOCKED"
    ):
        raise PermissionError(
            "activation requires the exact frozen BLOCKED_NO_EXECUTION_AUTHORITY state"
        )
    opened["confirmatory_execution_status"] = AUTHORIZED_DISPATCH_STATUS
    gate["state"] = AUTHORIZED_DISPATCH_STATUS
    gate["missing_artifact"] = None
    return opened


def activate_confirmatory_execution(
    protocol: Mapping[str, Any],
    goal1: Mapping[str, Any],
    *,
    operator_seed: bytes,
) -> ConfirmatoryActivation:
    """Revalidate fixed trust artifacts and return an exact dispatchable manifest.

    No public production entry point accepts a caller-minted capability.  Every call
    loads the fixed trust root and authority, compares the complete preactivation
    protocol to the checked-in bytes, derives the three-field operational transition,
    reconstructs all dispatch slots, and verifies the resulting manifest.
    """

    opened = _open_operational_gate(protocol)
    manifest, authority = _build_authorized_confirmatory_manifest(
        protocol,
        opened,
        goal1,
        operator_seed=operator_seed,
    )
    assert_dispatch_authorized(
        manifest.public_manifest,
        manifest.operator_plan,
        opened,
        execution_authority=authority,
    )
    return ConfirmatoryActivation(
        protocol=opened,
        manifest=manifest,
        authority=authority,
    )


class DurableActivationAuthority:
    """Atomic, append-only authority for the single production activation.

    Manifest construction happens before the SQLite transaction and cannot consume
    the nonce.  The one-row insert is the activation linearization point: a crash
    before it leaves the authority empty, while a committed row is immutable and
    must revalidate against the current checked-in authority chain on every read.
    """

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        path_text = os.fspath(db_path)
        if not path_text or path_text == ":memory:" or path_text.startswith("file:"):
            raise ValueError("activation database must be an absolute durable file")
        path = Path(path_text)
        if not path.is_absolute():
            raise ValueError("activation database path must be absolute")
        self.db_path = path.resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path), timeout=30, isolation_level=None
        )
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS activation_state (
                  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                  activation_id TEXT NOT NULL UNIQUE,
                  nonce_commitment_sha256 TEXT NOT NULL UNIQUE,
                  protocol_rules_sha256 TEXT NOT NULL,
                  authority_sha256 TEXT NOT NULL,
                  opened_protocol_json BLOB NOT NULL,
                  public_manifest_json BLOB NOT NULL,
                  operator_plan_json BLOB NOT NULL,
                  activation_record_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS activation_state_no_update
                BEFORE UPDATE ON activation_state BEGIN
                  SELECT RAISE(ABORT, 'activation state is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS activation_state_no_delete
                BEFORE DELETE ON activation_state BEGIN
                  SELECT RAISE(ABORT, 'activation state is append-only');
                END;
                """
            )
        finally:
            connection.close()

    @staticmethod
    def _record_body(
        *,
        activation_id: str,
        nonce_commitment_sha256: str,
        protocol_rules_sha256: str,
        authority_sha256: str,
        opened_protocol_json: bytes,
        public_manifest_json: bytes,
        operator_plan_json: bytes,
    ) -> dict[str, object]:
        return {
            "activation_id": activation_id,
            "authority_sha256": authority_sha256,
            "nonce_commitment_sha256": nonce_commitment_sha256,
            "opened_protocol_sha256": _sha256(opened_protocol_json),
            "operator_plan_sha256": _sha256(operator_plan_json),
            "protocol_rules_sha256": protocol_rules_sha256,
            "public_manifest_sha256": _sha256(public_manifest_json),
            "schema": ACTIVATION_SCHEMA,
        }

    def activate_once(
        self,
        protocol: Mapping[str, Any],
        goal1: Mapping[str, Any],
        *,
        operator_seed: bytes,
        activation_nonce: bytes,
    ) -> DurableConfirmatoryActivation:
        connection = self._connect()
        try:
            if (
                connection.execute(
                    "SELECT 1 FROM activation_state WHERE singleton=1"
                ).fetchone()
                is not None
            ):
                raise PermissionError("production activation was already consumed")
        finally:
            connection.close()
        commitment = _nonce_commitment(activation_nonce)
        activation_id = "activation-" + _sha256(
            b"supernova.confirmatory-activation-id.v1\0" + bytes.fromhex(commitment)
        )
        activation = activate_confirmatory_execution(
            protocol,
            goal1,
            operator_seed=operator_seed,
        )
        opened_protocol_json = _canonical_bytes(activation.protocol)
        public_manifest_json = _canonical_bytes(activation.manifest.public_manifest)
        operator_plan_json = _canonical_bytes(activation.manifest.operator_plan)
        body = self._record_body(
            activation_id=activation_id,
            nonce_commitment_sha256=commitment,
            protocol_rules_sha256=str(protocol["sealed_rules_sha256"]),
            authority_sha256=activation.authority.authority_sha256,
            opened_protocol_json=opened_protocol_json,
            public_manifest_json=public_manifest_json,
            operator_plan_json=operator_plan_json,
        )
        record_sha256 = _sha256(_canonical_bytes(body))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM activation_state WHERE singleton=1"
                ).fetchone()
                is not None
            ):
                raise PermissionError("production activation was already consumed")
            connection.execute(
                """INSERT INTO activation_state (
                     singleton,activation_id,nonce_commitment_sha256,
                     protocol_rules_sha256,authority_sha256,opened_protocol_json,
                     public_manifest_json,operator_plan_json,
                     activation_record_sha256
                   ) VALUES (1,?,?,?,?,?,?,?,?)""",
                (
                    activation_id,
                    commitment,
                    body["protocol_rules_sha256"],
                    body["authority_sha256"],
                    opened_protocol_json,
                    public_manifest_json,
                    operator_plan_json,
                    record_sha256,
                ),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return self.read_active()

    def read_active(self) -> DurableConfirmatoryActivation:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT activation_id,nonce_commitment_sha256,
                          protocol_rules_sha256,authority_sha256,
                          opened_protocol_json,public_manifest_json,
                          operator_plan_json,activation_record_sha256
                     FROM activation_state WHERE singleton=1"""
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LookupError("production activation has not been consumed")
        (
            activation_id,
            commitment,
            protocol_rules_sha256,
            authority_sha256,
            opened_protocol_json,
            public_manifest_json,
            operator_plan_json,
            record_sha256,
        ) = row
        opened_protocol_json = bytes(opened_protocol_json)
        public_manifest_json = bytes(public_manifest_json)
        operator_plan_json = bytes(operator_plan_json)
        body = self._record_body(
            activation_id=str(activation_id),
            nonce_commitment_sha256=str(commitment),
            protocol_rules_sha256=str(protocol_rules_sha256),
            authority_sha256=str(authority_sha256),
            opened_protocol_json=opened_protocol_json,
            public_manifest_json=public_manifest_json,
            operator_plan_json=operator_plan_json,
        )
        if _sha256(_canonical_bytes(body)) != record_sha256:
            raise ValueError("durable activation record digest is invalid")
        try:
            stored_protocol = json.loads(opened_protocol_json)
            stored_public = json.loads(public_manifest_json)
            stored_operator = json.loads(operator_plan_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("durable activation contains invalid JSON") from exc
        for value, raw, field in (
            (stored_protocol, opened_protocol_json, "opened protocol"),
            (stored_public, public_manifest_json, "public manifest"),
            (stored_operator, operator_plan_json, "operator plan"),
        ):
            if type(value) is not dict or _canonical_bytes(value) != raw:
                raise ValueError(f"durable activation {field} is not canonical")
        seed_hex = stored_operator.get("operator_seed_hex")
        if type(seed_hex) is not str:
            raise ValueError("durable activation lacks its operator seed")
        reconstructed = activate_confirmatory_execution(
            _fixed_protocol(),
            _fixed_goal1(),
            operator_seed=bytes.fromhex(seed_hex),
        )
        if (
            reconstructed.protocol != stored_protocol
            or reconstructed.manifest.public_manifest != stored_public
            or reconstructed.manifest.operator_plan != stored_operator
            or reconstructed.authority.authority_sha256 != authority_sha256
            or reconstructed.protocol["sealed_rules_sha256"] != protocol_rules_sha256
        ):
            raise ValueError("durable activation differs from fixed authority readback")
        return DurableConfirmatoryActivation(
            activation_id=str(activation_id),
            activation_record_sha256=str(record_sha256),
            activation=reconstructed,
        )


__all__ = [
    "ConfirmatoryActivation",
    "DurableActivationAuthority",
    "DurableConfirmatoryActivation",
    "activate_confirmatory_execution",
]
