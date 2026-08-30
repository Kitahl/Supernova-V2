from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


REQUEST_SCHEMA = "supernova.goal1.verifier-container-request.v1"
RESPONSE_SCHEMA = "supernova.goal1.verifier-container-response.v1"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_EXPORT_BYTES = 48 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 8 * 1024
TEMPLATE = Path("/opt/supernova/template")
LEAN4EXPORT = Path("/opt/supernova/bin/lean4export")
CHECK_EXPORTS = Path("/opt/supernova/bin/check_exports")
NANODA = Path("/opt/supernova/bin/nanoda_bin")
TOOLCHAIN_BIN = "/opt/lean/bin"
PERMITTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
PRIMITIVE_TARGETS = (
    "Nat", "String", "String.mk", "Char", "Char.ofNat", "List", "Quot",
    "Quot.mk", "Quot.lift", "Quot.ind", "Nat.add", "Nat.sub", "Nat.mul",
    "Nat.pow", "Nat.gcd", "Nat.div", "Nat.mod", "Nat.beq", "Nat.ble",
    "Nat.land", "Nat.lor", "Nat.xor", "Nat.shiftLeft", "Nat.shiftRight",
    "String.ofList",
)


class Rejected(Exception):
    pass


class InfrastructureError(Exception):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def token(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InfrastructureError(f"{field} must be a non-empty trimmed string")
    return value


def string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise InfrastructureError(f"{field} must be a non-empty array")
    result = tuple(token(item, f"{field}[]") for item in value)
    if len(result) != len(set(result)):
        raise InfrastructureError(f"{field} contains duplicates")
    return result


def decode_blob(request: dict[str, Any], name: str, limit: int) -> bytes:
    encoded = token(request.get(f"{name}_b64"), f"{name}_b64")
    expected = token(request.get(f"{name}_sha256"), f"{name}_sha256")
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise InfrastructureError(f"{name}_b64 is not canonical base64") from exc
    if len(value) > limit:
        raise InfrastructureError(f"{name} exceeds its byte limit")
    if sha256(value) != expected:
        raise InfrastructureError(f"{name} digest mismatch")
    return value


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise InfrastructureError("request exceeds byte limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InfrastructureError("request is not UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise InfrastructureError("request must be one canonical JSON object")
    if value.get("schema") != REQUEST_SCHEMA:
        raise InfrastructureError("request schema mismatch")
    return value


def response(status: str, **fields: object) -> None:
    body = {"schema": RESPONSE_SCHEMA, "status": status, **fields}
    sys.stdout.buffer.write(canonical_bytes(body))
    sys.stdout.buffer.flush()


def project(directory: Path) -> None:
    for name in ("lakefile.toml", "lake-manifest.json", "lean-toolchain"):
        os.symlink(TEMPLATE / name, directory / name)
    lake = directory / ".lake"
    lake.mkdir()
    os.symlink(TEMPLATE / ".lake" / "packages", lake / "packages")


def _bounded_file_bytes(handle: Any, limit: int, field: str) -> bytes:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    if size > limit:
        raise InfrastructureError(f"{field} exceeds its byte limit")
    handle.seek(0)
    value = handle.read(limit + 1)
    if len(value) > limit:
        raise InfrastructureError(f"{field} exceeds its byte limit")
    return value


def run(
    command: list[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    stdout_limit: int = MAX_DIAGNOSTIC_BYTES,
    stderr_limit: int = MAX_DIAGNOSTIC_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Run inside the bounded tmpfs without buffering child output in memory."""

    with (
        tempfile.TemporaryFile(dir="/tmp") as stdout_file,
        tempfile.TemporaryFile(dir="/tmp") as stderr_file,
    ):
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            stdout=stdout_file,
            stderr=stderr_file,
            check=False,
            shell=False,
            env={
                "HOME": "/tmp/home",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": f"{TOOLCHAIN_BIN}:/usr/local/bin:/usr/bin:/bin",
            },
        )
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            _bounded_file_bytes(stdout_file, stdout_limit, "child stdout"),
            _bounded_file_bytes(stderr_file, stderr_limit, "child stderr"),
        )


def export_targets(theorems: tuple[str, ...], axioms: tuple[str, ...]) -> list[str]:
    return sorted(set((*theorems, *axioms, *PRIMITIVE_TARGETS)))


def elaborate(request: dict[str, Any]) -> None:
    expected = {
        "mode", "schema", "solution_source_b64", "solution_source_sha256",
        "theorem_names", "permitted_axioms",
    }
    if set(request) != expected or request.get("mode") != "elaborate":
        raise InfrastructureError("elaborate request fields changed")
    solution = decode_blob(request, "solution_source", MAX_SOURCE_BYTES)
    theorems = string_list(request.get("theorem_names"), "theorem_names")
    axioms = string_list(request.get("permitted_axioms"), "permitted_axioms")
    if axioms != PERMITTED_AXIOMS:
        raise InfrastructureError("permitted axiom policy changed")
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        (root / "home").mkdir()
        project(root)
        (root / "Solution.lean").write_bytes(solution)
        built = run(["lake", "env", "lean", "Solution.lean"], cwd=root)
        if built.returncode != 0:
            raise Rejected((built.stderr + built.stdout)[:8192].decode("utf-8", "replace"))
        exported = run(
            [
                "lake", "env", str(LEAN4EXPORT), "Solution", "--",
                *export_targets(theorems, axioms),
            ],
            cwd=root,
            stdout_limit=MAX_EXPORT_BYTES,
        )
        if exported.returncode != 0 or not exported.stdout:
            raise Rejected((exported.stderr + exported.stdout)[:8192].decode("utf-8", "replace"))
        if len(exported.stdout) > MAX_EXPORT_BYTES:
            raise InfrastructureError("solution export exceeds byte limit")
        response(
            "EXPORTED",
            solution_export_b64=base64.b64encode(exported.stdout).decode("ascii"),
            solution_export_sha256=sha256(exported.stdout),
        )


def check(request: dict[str, Any]) -> None:
    expected = {
        "challenge_source_b64", "challenge_source_sha256", "mode",
        "permitted_axioms", "schema", "solution_export_b64",
        "solution_export_sha256", "theorem_names",
    }
    if set(request) != expected or request.get("mode") != "check":
        raise InfrastructureError("check request fields changed")
    challenge = decode_blob(request, "challenge_source", MAX_SOURCE_BYTES)
    solution_export = decode_blob(request, "solution_export", MAX_EXPORT_BYTES)
    theorems = string_list(request.get("theorem_names"), "theorem_names")
    axioms = string_list(request.get("permitted_axioms"), "permitted_axioms")
    if axioms != PERMITTED_AXIOMS:
        raise InfrastructureError("permitted axiom policy changed")
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        (root / "home").mkdir()
        project(root)
        (root / "Challenge.lean").write_bytes(challenge)
        (root / "solution.ndjson").write_bytes(solution_export)
        built = run(["lake", "env", "lean", "Challenge.lean"], cwd=root)
        if built.returncode != 0:
            raise InfrastructureError(
                "trusted challenge failed to elaborate: "
                + (built.stderr + built.stdout)[:8192].decode("utf-8", "replace")
            )
        challenge_export = run(
            [
                "lake", "env", str(LEAN4EXPORT), "Challenge", "--",
                *export_targets(theorems, axioms),
            ],
            cwd=root,
            stdout_limit=MAX_EXPORT_BYTES,
        )
        if challenge_export.returncode != 0 or not challenge_export.stdout:
            raise InfrastructureError("trusted challenge export failed")
        (root / "challenge.ndjson").write_bytes(challenge_export.stdout)
        checker_config = {
            "permitted_axioms": list(axioms),
            "theorem_names": list(theorems),
        }
        (root / "checker.json").write_bytes(canonical_bytes(checker_config))
        compared = run(
            [
                str(CHECK_EXPORTS), "challenge.ndjson", "solution.ndjson",
                "checker.json",
            ],
            cwd=root,
        )
        if compared.returncode != 0:
            raise Rejected((compared.stderr + compared.stdout)[:8192].decode("utf-8", "replace"))
        nanoda_config = {
            "nat_extension": True,
            "permitted_axioms": list(axioms),
            "print_success_message": False,
            "string_extension": True,
            "unpermitted_axiom_hard_error": True,
            "use_stdin": True,
        }
        (root / "nanoda.json").write_bytes(canonical_bytes(nanoda_config))
        nanoda = run(
            [str(NANODA), "nanoda.json"], cwd=root, input_bytes=solution_export
        )
        if nanoda.returncode != 0:
            raise Rejected((nanoda.stderr + nanoda.stdout)[:8192].decode("utf-8", "replace"))
        response(
            "VALID",
            challenge_export_sha256=sha256(challenge_export.stdout),
            checker="COMPARATOR_DATA_ONLY_PLUS_NANODA",
            solution_export_sha256=sha256(solution_export),
        )


def main() -> int:
    try:
        request = read_request()
        mode = request.get("mode")
        if mode == "elaborate":
            elaborate(request)
        elif mode == "check":
            check(request)
        else:
            raise InfrastructureError("unsupported mode")
        return 0
    except Rejected as exc:
        response("INVALID", diagnostic=str(exc)[:8192])
        return 10
    except (InfrastructureError, OSError, subprocess.SubprocessError) as exc:
        response("UNKNOWN", diagnostic=str(exc)[:8192])
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
