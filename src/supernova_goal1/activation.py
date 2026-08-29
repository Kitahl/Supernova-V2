from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

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


@dataclass(frozen=True)
class ConfirmatoryActivation:
    """The complete handoff from the fixed frozen protocol to a sealed manifest."""

    protocol: dict[str, Any]
    manifest: ConfirmatoryManifestBundle
    authority: ValidatedExecutionAuthority


def _json_copy(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TypeError(f"{field} must be an exact dict")
    return json.loads(json.dumps(value, allow_nan=False))


def _fixed_protocol() -> dict[str, Any]:
    path = _repository_root() / PROTOCOL_RELATIVE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _open_operational_gate(protocol: Mapping[str, Any]) -> dict[str, Any]:
    opened = _json_copy(protocol, "protocol")
    if opened != _fixed_protocol():
        raise PermissionError("activation requires the exact checked-in frozen protocol")
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


__all__ = ["ConfirmatoryActivation", "activate_confirmatory_execution"]
