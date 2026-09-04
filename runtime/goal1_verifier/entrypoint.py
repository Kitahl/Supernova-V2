from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "supernova.goal1.verifier-container-request.v1"
RESPONSE_SCHEMA = "supernova.goal1.verifier-container-response.v2"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_EXPORT_BYTES = 48 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 8 * 1024
# Lean's -M flag is measured in megabytes.  Zero disables the cumulative
# interpreter threshold so the host-observed cgroup is the single memory
# authority; an actual cgroup OOM is recorded as UNKNOWN rather than INVALID.
LEAN_INTERNAL_MEMORY_MEGABYTES = 0
TEMPLATE = Path("/opt/supernova/template")
RUNTIME_LOCK = Path("/opt/supernova/VERIFIER_RUNTIME_LOCK.json")
TRUSTED_WORKING_DIRECTORY = TEMPLATE / ".lake" / "packages" / "mathlib"
LEAN = Path("/opt/lean/bin/lean")
LEAN4EXPORT = Path("/opt/supernova/bin/lean4export")
CHECK_EXPORTS = Path("/opt/supernova/bin/check_exports")
NANODA = Path("/opt/supernova/bin/nanoda_bin")
PARSE_PRODUCT = Path("/opt/supernova/bin/parse_product")
TOOLCHAIN_BIN = "/opt/lean/bin"
PERMITTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
PRIMITIVE_TARGETS = (
    "Nat",
    "String",
    "String.mk",
    "Char",
    "Char.ofNat",
    "List",
    "Quot",
    "Quot.mk",
    "Quot.lift",
    "Quot.ind",
    "Nat.add",
    "Nat.sub",
    "Nat.mul",
    "Nat.pow",
    "Nat.gcd",
    "Nat.div",
    "Nat.mod",
    "Nat.beq",
    "Nat.ble",
    "Nat.land",
    "Nat.lor",
    "Nat.xor",
    "Nat.shiftLeft",
    "Nat.shiftRight",
    "String.ofList",
)
FORBIDDEN_PRODUCT_SYNTAX = re.compile(
    rb"(?m)^\s*(?:import|namespace|end|section|variable|notation|macro|syntax|"
    rb"attribute|set_option|axiom|opaque|unsafe)\b|\b(?:sorry|admit)\b|"
    rb"^\s*@\["
)
HEARTBEAT_EXHAUSTION = re.compile(
    rb"(?:maximum number of heartbeats \([0-9]+\) has been reached|"
    rb"maximum heartbeats exceeded)",
    re.IGNORECASE,
)
UNKNOWN_CAUSE_HEARTBEAT = "RESOURCE_LIMIT_HEARTBEAT"
UNKNOWN_CAUSE_INTERNAL = "INTERNAL"


class Rejected(Exception):
    pass


class InfrastructureError(Exception):
    pass


class ResourceUnknown(Exception):
    def __init__(self, cause: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.cause = cause


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


def validate_runtime_artifact(item: object) -> None:
    if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
        raise InfrastructureError("verifier runtime artifact entry changed")
    path = Path(token(item["path"], "runtime artifact path"))
    expected_sha = token(item["sha256"], "runtime artifact sha256")
    expected_size = item["size"]
    if not path.is_absolute() or type(expected_size) is not int:
        raise InfrastructureError("verifier runtime artifact identity changed")
    try:
        with path.open("rb", buffering=0) as handle:
            actual_size = os.fstat(handle.fileno()).st_size
            actual_sha = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise InfrastructureError("verifier runtime artifact is missing") from exc
    if actual_size != expected_size or actual_sha != expected_sha:
        raise InfrastructureError("verifier runtime artifact digest mismatch")


def runtime_lock() -> dict[str, Any]:
    try:
        raw = RUNTIME_LOCK.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InfrastructureError("verifier runtime lock is unreadable") from exc
    if not isinstance(value, dict):
        raise InfrastructureError("verifier runtime lock must be an object")
    expected_root = value.get("root_sha256")
    if not isinstance(expected_root, str):
        raise InfrastructureError("verifier runtime lock root is missing")
    body = dict(value)
    del body["root_sha256"]
    if sha256(canonical_bytes(body)) != expected_root:
        raise InfrastructureError("verifier runtime lock root mismatch")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict):
        raise InfrastructureError("verifier runtime artifacts changed")
    binary_map = artifacts.get("binaries")
    trusted_oleans = artifacts.get("trusted_oleans")
    if not isinstance(binary_map, dict) or not isinstance(trusted_oleans, list):
        raise InfrastructureError("verifier runtime artifact inventory changed")
    inventory = [*binary_map.values(), *trusted_oleans]
    for item in inventory:
        validate_runtime_artifact(item)
    if not TRUSTED_WORKING_DIRECTORY.is_dir():
        raise InfrastructureError("trusted mathlib working directory is missing")
    return value


def runtime_environment(lock: dict[str, Any], cell_root: Path) -> dict[str, str]:
    module_environment = lock.get("module_environment")
    if not isinstance(module_environment, dict):
        raise InfrastructureError("verifier module environment changed")
    result = {
        "HOME": str(cell_root / "home"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LEAN_SYSROOT": "/opt/lean",
        "PATH": f"{TOOLCHAIN_BIN}:/usr/local/bin:/usr/bin:/bin",
    }
    for key in ("LEAN_PATH", "LEAN_SRC_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        entries = module_environment.get(key)
        if not isinstance(entries, list) or any(
            not isinstance(item, str) for item in entries
        ):
            raise InfrastructureError(f"{key} runtime lock entry changed")
        exact = [str(cell_root), *entries] if key == "LEAN_PATH" else entries
        if exact:
            result[key] = os.pathsep.join(exact)
    for forbidden in ("git", "lake"):
        if shutil.which(forbidden, path=result["PATH"]) is not None:
            raise InfrastructureError(
                f"forbidden runtime command is present: {forbidden}"
            )
    return result


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
    environment: dict[str, str],
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
            env=environment,
        )
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            _bounded_file_bytes(stdout_file, stdout_limit, "child stdout"),
            _bounded_file_bytes(stderr_file, stderr_limit, "child stderr"),
        )


def export_targets(theorems: tuple[str, ...], axioms: tuple[str, ...]) -> list[str]:
    return sorted({*theorems, *axioms, *PRIMITIVE_TARGETS})


def compile_module(
    *,
    root: Path,
    module: str,
    source: bytes,
    lock: dict[str, Any],
    warning_as_error: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    source_path = root / f"{module}.lean"
    olean_path = root / f"{module}.olean"
    source_path.write_bytes(source)
    command = [
        str(LEAN),
        "-R",
        str(root),
        "-o",
        str(olean_path),
        "-t",
        "0",
    ]
    if warning_as_error:
        command.append("-DwarningAsError=true")
    command.extend(
        [
            "-M",
            str(LEAN_INTERNAL_MEMORY_MEGABYTES),
            str(source_path),
        ]
    )
    completed = run(
        command,
        cwd=TRUSTED_WORKING_DIRECTORY,
        environment=runtime_environment(lock, root),
    )
    if completed.returncode == 0:
        try:
            stat = olean_path.stat()
        except OSError as exc:
            raise InfrastructureError(
                "Lean did not produce the expected olean"
            ) from exc
        if not olean_path.is_file() or stat.st_size == 0:
            raise InfrastructureError("Lean produced an invalid olean")
    return completed


def raise_candidate_failure(completed: subprocess.CompletedProcess[bytes]) -> None:
    diagnostic = (completed.stderr + completed.stdout)[:MAX_DIAGNOSTIC_BYTES]
    text = diagnostic.decode("utf-8", "replace")
    if HEARTBEAT_EXHAUSTION.search(diagnostic):
        raise ResourceUnknown(UNKNOWN_CAUSE_HEARTBEAT, text)
    raise Rejected(text)


def export_module(
    *,
    root: Path,
    module: str,
    targets: list[str],
    lock: dict[str, Any],
) -> subprocess.CompletedProcess[bytes]:
    return run(
        [str(LEAN4EXPORT), module, "--", *targets],
        cwd=TRUSTED_WORKING_DIRECTORY,
        environment=runtime_environment(lock, root),
        stdout_limit=MAX_EXPORT_BYTES,
    )


def elaborate(request: dict[str, Any], lock: dict[str, Any]) -> None:
    expected = {
        "mode",
        "schema",
        "solution_source_b64",
        "solution_source_sha256",
        "theorem_names",
        "permitted_axioms",
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
        built = compile_module(root=root, module="Solution", source=solution, lock=lock)
        if built.returncode != 0:
            raise_candidate_failure(built)
        exported = export_module(
            root=root,
            module="Solution",
            targets=export_targets(theorems, axioms),
            lock=lock,
        )
        if exported.returncode != 0:
            raise_candidate_failure(exported)
        if not exported.stdout:
            raise InfrastructureError("solution export is empty")
        if len(exported.stdout) > MAX_EXPORT_BYTES:
            raise InfrastructureError("solution export exceeds byte limit")
        response(
            "EXPORTED",
            solution_export_b64=base64.b64encode(exported.stdout).decode("ascii"),
            solution_export_sha256=sha256(exported.stdout),
        )


def parse_product(request: dict[str, Any], lock: dict[str, Any]) -> None:
    expected = {
        "expected_name",
        "mode",
        "product_source_b64",
        "product_source_sha256",
        "schema",
    }
    if set(request) != expected or request.get("mode") != "parse_product":
        raise InfrastructureError("parse_product request fields changed")
    source = decode_blob(request, "product_source", MAX_SOURCE_BYTES)
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Rejected("product declaration is not UTF-8") from exc
    expected_name = token(request.get("expected_name"), "expected_name")
    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    allowed_prefixes = (
        f"theorem {expected_name}",
        f"lemma {expected_name}",
    )
    if not any(
        first_line.startswith(prefix)
        and (len(first_line) == len(prefix) or first_line[len(prefix)].isspace())
        for prefix in allowed_prefixes
    ):
        raise Rejected("product declaration kind or exact name changed")
    if FORBIDDEN_PRODUCT_SYNTAX.search(source):
        raise Rejected("product declaration contains frozen forbidden syntax")
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        (root / "home").mkdir()
        source_path = root / "Product.lean"
        source_path.write_bytes(source)
        parsed = run(
            [str(PARSE_PRODUCT), str(source_path)],
            cwd=TRUSTED_WORKING_DIRECTORY,
            environment=runtime_environment(lock, root),
        )
        if parsed.returncode != 0:
            raise Rejected(
                (parsed.stderr + parsed.stdout)[:8192].decode("utf-8", "replace")
            )
    response(
        "PARSED",
        declaration_name=expected_name,
        product_source_sha256=sha256(source),
    )


def check(request: dict[str, Any], lock: dict[str, Any]) -> None:
    expected = {
        "challenge_source_b64",
        "challenge_source_sha256",
        "mode",
        "permitted_axioms",
        "schema",
        "solution_export_b64",
        "solution_export_sha256",
        "theorem_names",
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
        (root / "solution.ndjson").write_bytes(solution_export)
        built = compile_module(
            root=root,
            module="Challenge",
            source=challenge,
            lock=lock,
            warning_as_error=False,
        )
        if built.returncode != 0:
            raise InfrastructureError(
                "trusted challenge failed to elaborate: "
                + (built.stderr + built.stdout)[:8192].decode("utf-8", "replace")
            )
        challenge_export = export_module(
            root=root,
            module="Challenge",
            targets=export_targets(theorems, axioms),
            lock=lock,
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
                str(CHECK_EXPORTS),
                "challenge.ndjson",
                "solution.ndjson",
                "checker.json",
            ],
            cwd=root,
            environment=runtime_environment(lock, root),
        )
        if compared.returncode != 0:
            raise Rejected(
                (compared.stderr + compared.stdout)[:8192].decode("utf-8", "replace")
            )
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
            [str(NANODA), "nanoda.json"],
            cwd=root,
            environment=runtime_environment(lock, root),
            input_bytes=solution_export,
        )
        if nanoda.returncode != 0:
            raise Rejected(
                (nanoda.stderr + nanoda.stdout)[:8192].decode("utf-8", "replace")
            )
        response(
            "VALID",
            challenge_export_sha256=sha256(challenge_export.stdout),
            checker="COMPARATOR_DATA_ONLY_PLUS_NANODA",
            solution_export_sha256=sha256(solution_export),
        )


def main() -> int:
    try:
        lock = runtime_lock()
        request = read_request()
        mode = request.get("mode")
        if mode == "elaborate":
            elaborate(request, lock)
        elif mode == "parse_product":
            parse_product(request, lock)
        elif mode == "check":
            check(request, lock)
        else:
            raise InfrastructureError("unsupported mode")
        return 0
    except ResourceUnknown as exc:
        response(
            "UNKNOWN",
            diagnostic=str(exc)[:MAX_DIAGNOSTIC_BYTES],
            termination_cause=exc.cause,
        )
        return 20
    except Rejected as exc:
        response("INVALID", diagnostic=str(exc)[:8192])
        return 10
    except (InfrastructureError, OSError, subprocess.SubprocessError) as exc:
        response(
            "UNKNOWN",
            diagnostic=str(exc)[:MAX_DIAGNOSTIC_BYTES],
            termination_cause=UNKNOWN_CAUSE_INTERNAL,
        )
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
