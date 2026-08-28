from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supernova_goal1.verifier import VerifierResult, VerifierStatus, run_verifier


LOCK_PATH = ROOT / "goal1" / "BENCHMARK.lock.json"
RUNTIME_PATH = ROOT / "runtime" / "lean"
EXPECTED_TOOLCHAIN = "leanprover/lean4:v4.33.1"
EXPECTED_LEAN_VERSION = "4.33.1"
MAX_SAMPLE_SIZE = 8
VERSION_TIMEOUT_SECONDS = 60
SAMPLE_TIMEOUT_SECONDS = 180
REPORT_SCHEMA_VERSION = 1
NON_CREDIT_LABEL = "NON_CREDIT_STATEMENT_ELABORATION_ONLY"
RUNTIME_IDENTITY_FILES = (
    "lean-toolchain",
    "lakefile.toml",
    "lake-manifest.json",
)

_LOCK_SPEC = importlib.util.spec_from_file_location(
    "supernova_benchmark_lock", ROOT / "scripts" / "import_benchmark.py"
)
if _LOCK_SPEC is None or _LOCK_SPEC.loader is None:
    raise RuntimeError("cannot load benchmark lock verifier")
_LOCK_MODULE = importlib.util.module_from_spec(_LOCK_SPEC)
_LOCK_SPEC.loader.exec_module(_LOCK_MODULE)

Runner = Callable[..., VerifierResult]


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object member: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _load_json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _plain_text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be an exact non-empty trimmed string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must contain only Unicode scalar values") from exc
    return value


def _sha256_hex(value: object, label: str) -> str:
    value = _plain_text(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be exactly 64 lowercase hexadecimal characters")
    return value


def _exact_nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative exact integer")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_stable_file(path: Path, label: str) -> tuple[str, int, bytes]:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    with path.open("rb") as handle:
        opened_stat = os.fstat(handle.fileno())
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            chunks.append(chunk)
            size += len(chunk)
        after_read = os.fstat(handle.fileno())
    after_path = path.stat()
    signatures = {
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        for item in (before, opened_stat, after_read, after_path)
    }
    if len(signatures) != 1 or size != after_read.st_size:
        raise ValueError(f"{label} changed while reading: {path}")
    return digest.hexdigest(), size, b"".join(chunks)


def _hash_stable_file(path: Path, label: str) -> tuple[str, int]:
    digest, size, _ = _read_stable_file(path, label)
    return digest, size


def _resolve_input_path(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    if path.exists() and _LOCK_MODULE._is_junction(path):
        raise ValueError(f"{label} must not be a junction: {path}")
    return path.resolve()


def _runtime_identity(runtime_root: Path) -> dict[str, object]:
    if runtime_root.is_symlink() or _LOCK_MODULE._is_junction(runtime_root):
        raise ValueError("runtime root must not be a symlink or junction")
    runtime_root = runtime_root.resolve()
    if not runtime_root.is_dir():
        raise ValueError(f"runtime root is not a directory: {runtime_root}")

    files: list[dict[str, object]] = []
    file_bytes: dict[str, bytes] = {}
    for relative in RUNTIME_IDENTITY_FILES:
        path = runtime_root / relative
        digest, size, payload = _read_stable_file(path, f"runtime input {relative}")
        files.append({"path": relative, "sha256": digest, "bytes": size})
        file_bytes[relative] = payload

    toolchain = file_bytes["lean-toolchain"].decode("utf-8").strip()
    if toolchain != EXPECTED_TOOLCHAIN:
        raise ValueError(
            f"wrong Lean toolchain pin: expected {EXPECTED_TOOLCHAIN!r}, observed {toolchain!r}"
        )

    lakefile = tomllib.loads(file_bytes["lakefile.toml"].decode("utf-8"))
    requirements = lakefile.get("require")
    if requirements != [
        {
            "name": "mathlib",
            "scope": "leanprover-community",
            "rev": "v4.33.1",
        }
    ]:
        raise ValueError("lakefile.toml must pin mathlib exactly to v4.33.1")

    manifest = _load_json_object(
        file_bytes["lake-manifest.json"].decode("utf-8"),
        "lake-manifest.json",
    )
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("lake-manifest.json must contain at least one package")
    mathlib_matches = [
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == "mathlib"
    ]
    if len(mathlib_matches) != 1 or mathlib_matches[0].get("inputRev") != "v4.33.1":
        raise ValueError("lake-manifest.json must bind mathlib inputRev to v4.33.1")
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            raise ValueError(f"lake-manifest package {index} must be an object")
        revision = package.get("rev")
        if type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError(
                f"lake-manifest package {index} is not pinned to a full lowercase commit"
            )

    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "toolchain": toolchain,
        "files": files,
    }
    return {
        **payload,
        "runtime_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    }


def _load_validation_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    expected_fields = {
        "schema_version",
        "problem_id",
        "split",
        "source_id",
        "source_record_sha256",
        "lean_code_sha256",
        "lean_code",
        "informal_prefix",
    }
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith("\n") or not line.strip():
                raise ValueError(
                    f"validation.jsonl line {line_number} must be one non-empty newline-terminated record"
                )
            record = _load_json_object(line, f"validation.jsonl line {line_number}")
            if set(record) != expected_fields:
                raise ValueError(
                    f"validation record fields must be exactly {sorted(expected_fields)}"
                )
            if record["schema_version"] != 1:
                raise ValueError("validation record schema_version must be 1")
            problem_id = _plain_text(record["problem_id"], "problem_id")
            if record["split"] != "validation":
                raise ValueError(f"record {problem_id} is not in the validation split")
            _plain_text(record["source_id"], "source_id")
            _sha256_hex(record["source_record_sha256"], "source_record_sha256")
            lean_code_sha256 = _sha256_hex(record["lean_code_sha256"], "lean_code_sha256")
            lean_code = record["lean_code"]
            if type(lean_code) is not str or not lean_code:
                raise ValueError(f"record {problem_id} lean_code must be a non-empty exact string")
            try:
                encoded = lean_code.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"record {problem_id} lean_code contains a non-Unicode scalar"
                ) from exc
            if hashlib.sha256(encoded).hexdigest() != lean_code_sha256:
                raise ValueError(f"record {problem_id} lean_code_sha256 does not match lean_code")
            if type(record["informal_prefix"]) is not str:
                raise ValueError(f"record {problem_id} informal_prefix must be an exact string")
            records.append(record)

    if not records:
        raise ValueError("validation.jsonl contains no records")
    problem_ids = [record["problem_id"] for record in records]
    if problem_ids != sorted(problem_ids):
        raise ValueError("validation records must remain in frozen problem_id order")
    if len(problem_ids) != len(set(problem_ids)):
        raise ValueError("validation records contain duplicate problem_id values")
    return records


def _select_sample(
    records: Sequence[Mapping[str, Any]],
    *,
    benchmark_root_sha256: str,
    sample_size: int,
) -> tuple[Mapping[str, Any], ...]:
    sample_size = _exact_nonnegative_int(sample_size, "sample_size")
    if sample_size < 1 or sample_size > MAX_SAMPLE_SIZE:
        raise ValueError(f"sample_size must be between 1 and {MAX_SAMPLE_SIZE}")
    if sample_size > len(records):
        raise ValueError("sample_size exceeds validation record count")

    def rank(record: Mapping[str, Any]) -> tuple[str, str]:
        problem_id = _plain_text(record.get("problem_id"), "problem_id")
        payload = (
            benchmark_root_sha256.encode("ascii")
            + b"\0validation\0"
            + problem_id.encode("utf-8")
        )
        return hashlib.sha256(payload).hexdigest(), problem_id

    return tuple(sorted(records, key=rank)[:sample_size])


def _materialize_non_credit_statement(lean_code: object, problem_id: str) -> str:
    if type(lean_code) is not str or not lean_code.endswith(":= by\n"):
        raise ValueError(
            f"record {problem_id} is not an unsolved theorem ending exactly in ':= by\\n'"
        )
    return lean_code + "  sorry\n"


def _result_evidence(result: VerifierResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
        "error": result.error,
    }


def _validate_verifier_result(result: object) -> VerifierResult:
    if type(result) is not VerifierResult:
        raise TypeError("runner must return an exact VerifierResult")
    if type(result.status) is not VerifierStatus:
        raise ValueError("verifier status must be an exact VerifierStatus")
    if (
        type(result.command) is not tuple
        or not result.command
        or not all(type(argument) is str and argument for argument in result.command)
    ):
        raise ValueError("verifier command must be a non-empty exact string tuple")
    if type(result.stdout) is not str or type(result.stderr) is not str:
        raise ValueError("verifier stdout and stderr must be exact strings")
    if type(result.elapsed_milliseconds) is not int or result.elapsed_milliseconds < 0:
        raise ValueError("verifier elapsed_milliseconds must be a non-negative exact integer")
    if result.status is VerifierStatus.PASS:
        if result.returncode != 0 or result.error is not None:
            raise ValueError("PASS verifier result requires returncode=0 and no error")
    elif result.status is VerifierStatus.FAIL:
        if (
            type(result.returncode) is not int
            or result.returncode == 0
            or result.error is not None
        ):
            raise ValueError("FAIL verifier result requires a nonzero exact returncode and no error")
    elif result.returncode is not None or type(result.error) is not str or not result.error:
        raise ValueError(
            f"{result.status.value} verifier result requires returncode=null and a non-empty error"
        )
    return result


def _run(
    runner: Runner,
    command: Sequence[str],
    *,
    timeout_seconds: int,
    cwd: Path,
) -> VerifierResult:
    result = runner(tuple(command), timeout_seconds=timeout_seconds, cwd=cwd)
    return _validate_verifier_result(result)


def check_benchmark_runtime(
    *,
    benchmark_root: Path,
    lock_path: Path = LOCK_PATH,
    runtime_root: Path = RUNTIME_PATH,
    sample_size: int = 3,
    runner: Runner = run_verifier,
) -> dict[str, object]:
    benchmark_root = _resolve_input_path(benchmark_root, "benchmark root")
    lock_path = _resolve_input_path(lock_path, "benchmark lock")
    runtime_root = _resolve_input_path(runtime_root, "runtime root")

    _LOCK_MODULE._require_control_path_outside_source(
        benchmark_root, lock_path, label="benchmark lock"
    )
    lock_sha256, lock_bytes, lock_payload = _read_stable_file(
        lock_path, "benchmark lock"
    )
    lock = _load_json_object(lock_payload.decode("utf-8"), "benchmark lock")
    verified_lock = _LOCK_MODULE.verify_lock(benchmark_root, lock)

    content = verified_lock["content"]
    if not isinstance(content, dict):
        raise ValueError("verified benchmark lock content must be an object")
    root_sha256 = _sha256_hex(content.get("root_sha256"), "benchmark root_sha256")
    benchmark = verified_lock["benchmark"]
    if not isinstance(benchmark, dict):
        raise ValueError("verified benchmark identity must be an object")

    runtime_identity = _runtime_identity(runtime_root)
    version_result = _run(
        runner,
        ("lake", "env", "lean", "--version"),
        timeout_seconds=VERSION_TIMEOUT_SECONDS,
        cwd=runtime_root,
    )
    version_lines = version_result.stdout.splitlines()
    version_output = version_lines[0] if version_lines else ""
    expected_marker = f"Lean (version {EXPECTED_LEAN_VERSION},"
    failures: list[dict[str, object]] = []
    if version_result.status is not VerifierStatus.PASS:
        failures.append(
            {
                "code": "LEAN_VERSION_COMMAND_FAILED",
                "message": "pinned Lean version command did not pass",
                "evidence": _result_evidence(version_result),
            }
        )
    elif not version_output.startswith(expected_marker):
        failures.append(
            {
                "code": "LEAN_VERSION_MISMATCH",
                "message": (
                    f"expected version marker {expected_marker!r}, observed {version_output!r}"
                ),
                "evidence": _result_evidence(version_result),
            }
        )

    checks: list[dict[str, object]] = []
    selected_ids: list[str] = []
    if not failures:
        validation_path = benchmark_root / "validation.jsonl"
        records = _load_validation_records(validation_path)
        selected = _select_sample(
            records,
            benchmark_root_sha256=root_sha256,
            sample_size=sample_size,
        )
        selected_ids = [str(record["problem_id"]) for record in selected]
        with tempfile.TemporaryDirectory(prefix="supernova-goal1-runtime-") as temporary:
            temporary_root = Path(temporary)
            for index, record in enumerate(selected):
                problem_id = str(record["problem_id"])
                source = _materialize_non_credit_statement(record["lean_code"], problem_id)
                source_path = temporary_root / f"sample-{index:02d}.lean"
                source_path.write_text(source, encoding="utf-8", newline="")
                result = _run(
                    runner,
                    ("lake", "env", "lean", str(source_path)),
                    timeout_seconds=SAMPLE_TIMEOUT_SECONDS,
                    cwd=runtime_root,
                )
                check = {
                    "problem_id": problem_id,
                    "lean_code_sha256": record["lean_code_sha256"],
                    "materialized_sha256": hashlib.sha256(
                        source.encode("utf-8")
                    ).hexdigest(),
                    **_result_evidence(result),
                }
                checks.append(check)
                if result.status is not VerifierStatus.PASS:
                    failures.append(
                        {
                            "code": f"LEAN_SAMPLE_{result.status.value}",
                            "problem_id": problem_id,
                            "message": "validation statement did not elaborate in the pinned runtime",
                            "evidence": _result_evidence(result),
                        }
                    )

    closing_runtime_identity = _runtime_identity(runtime_root)
    if closing_runtime_identity != runtime_identity:
        raise ValueError("runtime identity changed during benchmark check")
    closing_lock_sha256, closing_lock_bytes, closing_lock_payload = _read_stable_file(
        lock_path, "benchmark lock"
    )
    if (
        closing_lock_sha256 != lock_sha256
        or closing_lock_bytes != lock_bytes
        or closing_lock_payload != lock_payload
    ):
        raise ValueError("benchmark lock changed during benchmark check")
    closing_verified_lock = _LOCK_MODULE.verify_lock(benchmark_root, lock)
    if closing_verified_lock != verified_lock:
        raise ValueError("benchmark content changed during benchmark check")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "credit": NON_CREDIT_LABEL,
        "benchmark": {
            "name": benchmark.get("name"),
            "version": benchmark.get("version"),
            "split_policy": benchmark.get("split"),
            "root_sha256": root_sha256,
            "lock_sha256": lock_sha256,
            "lock_bytes": lock_bytes,
        },
        "runtime": {
            **runtime_identity,
            "version_output": version_output,
        },
        "sample": {
            "split": "validation",
            "size": sample_size,
            "maximum_size": MAX_SAMPLE_SIZE,
            "selection": "sha256(benchmark_root_sha256 NUL validation NUL problem_id)",
            "problem_ids": selected_ids,
            "placeholder": "sorry",
        },
        "checks": checks,
        "failures": failures,
    }
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded, deterministic, non-credit benchmark/runtime check."
    )
    parser.add_argument("benchmark_root", type=Path)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--runtime", type=Path, default=RUNTIME_PATH)
    parser.add_argument("--sample-size", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = check_benchmark_runtime(
            benchmark_root=args.benchmark_root,
            lock_path=args.lock,
            runtime_root=args.runtime,
            sample_size=args.sample_size,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "ERROR",
            "credit": NON_CREDIT_LABEL,
            "failures": [
                {
                    "code": "CHECKER_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            ],
        }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
