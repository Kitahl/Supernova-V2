"""Read-only authority checks that must finish before any dispatch side effect."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .confirmatory_manifest import canonical_sha256
from .confirmatory_supervisor import load_repository_execution_bindings
from .execution_authority import load_execution_authority


SCHEMA = "supernova.goal1-pre-dispatch-conformance.v1"


class PreDispatchConformanceError(PermissionError):
    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"BLOCKED_PRE_DISPATCH_CONFORMANCE[{stage}]: {detail}")


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreDispatchConformanceError(label, "authority JSON is unreadable") from exc
    if type(value) is not dict:
        raise PreDispatchConformanceError(label, "authority must be one JSON object")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def git_blob_sha1(data: bytes) -> str:
    if type(data) is not bytes:
        raise TypeError("Git blob input must be exact bytes")
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def check_frozen_authority_blobs(
    repository_root: Path,
    protocol: Mapping[str, Any],
) -> dict[str, dict[str, object]]:
    rules = protocol.get("sealed_rules")
    if not isinstance(rules, Mapping):
        raise PreDispatchConformanceError("PROTOCOL", "sealed_rules is absent")
    authorities = rules.get("frozen_authorities")
    if not isinstance(authorities, Mapping) or not authorities:
        raise PreDispatchConformanceError(
            "FROZEN_AUTHORITIES",
            "frozen authority map is absent",
        )

    observations: dict[str, dict[str, object]] = {}
    for name in sorted(authorities):
        descriptor = authorities[name]
        if not isinstance(descriptor, Mapping):
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} descriptor is not an object",
            )
        relative = descriptor.get("path")
        expected = descriptor.get("git_blob_sha1")
        if type(relative) is not str or not relative or Path(relative).is_absolute():
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} path is not one repository-relative path",
            )
        if (
            type(expected) is not str
            or len(expected) != 40
            or any(char not in "0123456789abcdef" for char in expected)
        ):
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} Git blob id is malformed",
            )
        path = (repository_root / relative).resolve(strict=False)
        if not _inside(path, repository_root):
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} path escapes the repository",
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} authority is unreadable",
            ) from exc
        if path.suffix.lower() != ".json":
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} authority is not a JSON text artifact",
            )
        git_clean_data = data.replace(b"\r\n", b"\n")
        actual = git_blob_sha1(git_clean_data)
        if actual != expected:
            raise PreDispatchConformanceError(
                "FROZEN_AUTHORITIES",
                f"{name} authority bytes differ from sealed Git blob {expected}",
            )
        observations[str(name)] = {
            "bytes": len(data),
            "git_blob_sha1": actual,
            "path": relative,
            "raw_sha256": hashlib.sha256(data).hexdigest(),
            "git_clean_sha256": hashlib.sha256(git_clean_data).hexdigest(),
        }
    return observations


def assert_repository_conformance(repository_root: Path) -> dict[str, object]:
    """Return a content-addressed PASS only after every read-only check passes."""

    if not isinstance(repository_root, Path) or not repository_root.is_absolute():
        raise TypeError("repository_root must be one absolute pathlib.Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise PreDispatchConformanceError(
            "REPOSITORY",
            "repository root is unavailable",
        ) from exc

    package = sys.modules.get("supernova_goal1")
    package_file_raw = None if package is None else getattr(package, "__file__", None)
    if type(package_file_raw) is not str:
        raise PreDispatchConformanceError(
            "IMPORT_PROVENANCE",
            "supernova_goal1 package origin is unavailable",
        )
    package_file = Path(package_file_raw).resolve(strict=True)
    expected_package_root = (root / "src" / "supernova_goal1").resolve(strict=True)
    if not _inside(package_file, expected_package_root):
        raise PreDispatchConformanceError(
            "IMPORT_PROVENANCE",
            f"loaded package is outside this checkout: {package_file}",
        )

    protocol = _json_object(root / "goal1" / "CONFIRMATORY_PROTOCOL.json", "PROTOCOL")
    goal1 = _json_object(root / "goal1" / "GOAL1.json", "GOAL1")
    rules = protocol.get("sealed_rules")
    expected_rules_sha = protocol.get("sealed_rules_sha256")
    if (
        not isinstance(rules, Mapping)
        or type(expected_rules_sha) is not str
        or canonical_sha256(rules) != expected_rules_sha
    ):
        raise PreDispatchConformanceError(
            "PROTOCOL",
            "sealed protocol rules digest does not match current bytes",
        )
    frozen = check_frozen_authority_blobs(root, protocol)

    try:
        launcher, capacity = load_repository_execution_bindings(root)
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise PreDispatchConformanceError("EXECUTOR_BINDING", str(exc)) from exc
    try:
        authority = load_execution_authority(protocol, goal1)
    except (FileNotFoundError, OSError, PermissionError, TypeError, ValueError) as exc:
        raise PreDispatchConformanceError("EXECUTION_AUTHORITY", str(exc)) from exc

    body: dict[str, object] = {
        "capacity_binding_sha256": canonical_sha256(capacity),
        "execution_authority_sha256": authority.authority_sha256,
        "frozen_authorities": frozen,
        "model_identity_sha256": launcher.model_identity_sha256,
        "package_file": str(package_file),
        "protocol_rules_sha256": expected_rules_sha,
        "repository_root": str(root),
        "schema": SCHEMA,
        "status": "PASS",
    }
    return {**body, "report_sha256": canonical_sha256(body)}


__all__ = [
    "PreDispatchConformanceError",
    "SCHEMA",
    "assert_repository_conformance",
    "check_frozen_authority_blobs",
    "git_blob_sha1",
]
