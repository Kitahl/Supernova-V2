from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts import build_benchmark_v2
from scripts import import_benchmark as benchmark_lock
from scripts import validate_confirmatory_benchmark as strict_json
from supernova_goal1.verifier import VerifierResult, VerifierStatus, run_verifier


REPORT_SCHEMA = "supernova.goal1-benchmark-v2-compatibility-report.v1"
EXPECTED_RECORDS_PER_SPLIT = 244
EXPECTED_LEAN_VERSION = "4.33.1"
MAX_DIAGNOSTIC_CHARS = 4096

Runner = Callable[..., VerifierResult]


class CompatibilityError(ValueError):
    pass


def _candidate_records(
    candidate_directory: Path,
    transforms_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    corpus = candidate_directory / "corpus"
    lock = strict_json.load_strict_json(
        candidate_directory / "BENCHMARK_V2_CANDIDATE.lock.json"
    )
    verified_lock = benchmark_lock.verify_lock(corpus, lock)
    candidate_report = strict_json.load_strict_json(
        candidate_directory / "BENCHMARK_V2_CANDIDATE.report.json"
    )
    if (
        candidate_report.get("schema")
        != build_benchmark_v2.REPORT_SCHEMA
        or candidate_report.get("status") != "BUILT_UNQUALIFIED"
        or candidate_report.get("credit")
        != "NON_CREDIT_COMPATIBILITY_CANDIDATE_ONLY"
        or candidate_report.get("output_benchmark_root_sha256")
        != verified_lock["content"]["root_sha256"]
    ):
        raise CompatibilityError("candidate report does not bind the verified corpus")
    transforms = build_benchmark_v2.load_transform_manifest(transforms_path)
    if candidate_report.get("transform_manifest_sha256") != hashlib.sha256(
        build_benchmark_v2.canonical_bytes(transforms)
    ).hexdigest():
        raise CompatibilityError("candidate report binds a different transform manifest")

    required = {
        "informal_prefix",
        "lean_code",
        "lean_code_sha256",
        "problem_id",
        "schema_version",
        "source_id",
        "source_record_sha256",
        "split",
    }
    records: dict[str, list[dict[str, Any]]] = {"validation": [], "test": []}
    seen_problem_ids: set[str] = set()
    for split in ("validation", "test"):
        path = corpus / f"{split}.jsonl"
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            record = json.loads(
                line,
                object_pairs_hook=strict_json._strict_object,
            )
            if type(record) is not dict or set(record) != required:
                raise CompatibilityError(
                    f"{split} line {line_number} record schema changed"
                )
            problem_id = record.get("problem_id")
            if type(problem_id) is not str or not problem_id:
                raise CompatibilityError(f"{split} line {line_number} lacks problem_id")
            if problem_id in seen_problem_ids:
                raise CompatibilityError(f"duplicate problem_id: {problem_id}")
            seen_problem_ids.add(problem_id)
            if record.get("schema_version") != 2 or record.get("split") != split:
                raise CompatibilityError(f"{split}/{problem_id} identity changed")
            code = record.get("lean_code")
            if type(code) is not str or hashlib.sha256(code.encode()).hexdigest() != record.get(
                "lean_code_sha256"
            ):
                raise CompatibilityError(f"{split}/{problem_id} Lean digest changed")
            records[split].append(record)
        if len(records[split]) != EXPECTED_RECORDS_PER_SPLIT:
            raise CompatibilityError(
                f"{split} has {len(records[split])} records, "
                f"expected {EXPECTED_RECORDS_PER_SPLIT}"
            )
    return records, candidate_report


def _materialize(code: str, problem_id: str) -> str:
    if not code.endswith(":= by\n"):
        raise CompatibilityError(
            f"{problem_id} cannot be materialized by exact suffix append"
        )
    return code + "  sorry\n"


def _result(
    *,
    split: str,
    record: Mapping[str, Any],
    source_path: Path,
    runtime_root: Path,
    timeout_seconds: float,
    runner: Runner,
) -> dict[str, object]:
    observed = runner(
        ("lake", "env", "lean", str(source_path)),
        timeout_seconds=timeout_seconds,
        cwd=runtime_root,
    )
    if type(observed) is not VerifierResult:
        raise TypeError("runner must return exact VerifierResult")
    diagnostic = (observed.stderr + "\n" + observed.stdout).strip()
    return {
        "diagnostic": diagnostic[:MAX_DIAGNOSTIC_CHARS],
        "elapsed_milliseconds": observed.elapsed_milliseconds,
        "error": observed.error,
        "lean_code_sha256": record["lean_code_sha256"],
        "problem_id": record["problem_id"],
        "returncode": observed.returncode,
        "split": split,
        "status": observed.status.value,
    }


def check_records(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    runtime_root: Path,
    workers: int,
    timeout_seconds: float,
    runner: Runner = run_verifier,
) -> dict[str, object]:
    if type(workers) is not int or workers < 1 or workers > 4:
        raise CompatibilityError("workers must be an exact integer from 1 through 4")
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise CompatibilityError("timeout_seconds must be positive")
    version = runner(
        ("lake", "env", "lean", "--version"),
        timeout_seconds=timeout_seconds,
        cwd=runtime_root,
    )
    if (
        type(version) is not VerifierResult
        or version.status is not VerifierStatus.PASS
        or f"version {EXPECTED_LEAN_VERSION}" not in version.stdout
    ):
        raise CompatibilityError("runtime is not exact Lean 4.33.1")

    ordered = [
        (split, record)
        for split in ("validation", "test")
        for record in records[split]
    ]
    with tempfile.TemporaryDirectory(prefix="supernova-g1v2-corpus-") as raw:
        temporary = Path(raw)
        work: list[tuple[str, Mapping[str, Any], Path]] = []
        for index, (split, record) in enumerate(ordered):
            problem_id = str(record["problem_id"])
            source = _materialize(str(record["lean_code"]), problem_id)
            source_path = temporary / f"{index:03d}-{hashlib.sha256(problem_id.encode()).hexdigest()}.lean"
            source_path.write_text(source, encoding="utf-8", newline="\n")
            work.append((split, record, source_path))

        def run_one(item: tuple[str, Mapping[str, Any], Path]) -> dict[str, object]:
            split, record, source_path = item
            return _result(
                split=split,
                record=record,
                source_path=source_path,
                runtime_root=runtime_root,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_one, work))

    failures = [result for result in results if result["status"] != VerifierStatus.PASS.value]
    timings = sorted(int(result["elapsed_milliseconds"]) for result in results)
    p95_index = max(0, math.ceil(0.95 * len(timings)) - 1)
    body: dict[str, object] = {
        "credit": "NON_CREDIT_STATEMENT_COMPATIBILITY_ONLY",
        "failures": failures,
        "lean_version": EXPECTED_LEAN_VERSION,
        "record_count": len(results),
        "results": results,
        "runtime": {
            "command": ["lake", "env", "lean"],
            "timeout_seconds": timeout_seconds,
            "workers": workers,
        },
        "schema": REPORT_SCHEMA,
        "status": "PASS" if not failures else "BLOCKED",
        "timing_milliseconds": {
            "maximum": timings[-1],
            "mean": round(statistics.fmean(timings), 3),
            "median": statistics.median(timings),
            "minimum": timings[0],
            "p95": timings[p95_index],
        },
    }
    return {
        **body,
        "report_sha256": hashlib.sha256(
            build_benchmark_v2.canonical_bytes(body)
        ).hexdigest(),
    }


def check_candidate(
    *,
    candidate_directory: Path,
    transforms_path: Path,
    runtime_root: Path,
    workers: int,
    timeout_seconds: float,
    runner: Runner = run_verifier,
) -> dict[str, object]:
    records, candidate_report = _candidate_records(
        candidate_directory,
        transforms_path,
    )
    result = check_records(
        records,
        runtime_root=runtime_root,
        workers=workers,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    body = {
        **result,
        "candidate_report_sha256": candidate_report["report_sha256"],
        "output_benchmark_root_sha256": candidate_report[
            "output_benchmark_root_sha256"
        ],
    }
    without_digest = {
        key: value for key, value in body.items() if key != "report_sha256"
    }
    return {
        **without_digest,
        "report_sha256": hashlib.sha256(
            build_benchmark_v2.canonical_bytes(without_digest)
        ).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Elaborate all 488 Goal-1 benchmark-v2 statements with sorry."
    )
    parser.add_argument("candidate_directory", type=Path)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime" / "lean")
    parser.add_argument("--transforms", type=Path, default=build_benchmark_v2.DEFAULT_TRANSFORMS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = check_candidate(
            candidate_directory=args.candidate_directory.resolve(strict=True),
            transforms_path=args.transforms.resolve(strict=True),
            runtime_root=args.runtime.resolve(strict=True),
            workers=args.workers,
            timeout_seconds=args.timeout_seconds,
        )
    except (CompatibilityError, KeyError, OSError, TypeError, ValueError) as exc:
        report = {
            "credit": "NON_CREDIT_STATEMENT_COMPATIBILITY_ONLY",
            "error": str(exc),
            "schema": REPORT_SCHEMA,
            "status": "BLOCKED",
        }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
