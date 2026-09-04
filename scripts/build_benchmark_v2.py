from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import import_benchmark as benchmark_lock
from scripts import validate_confirmatory_benchmark as v1_validator


DEFAULT_TRANSFORMS = ROOT / "goal1" / "BENCHMARK_V2_TRANSFORMS.json"
DEFAULT_V1_MANIFEST = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
DEFAULT_V1_LOCK = ROOT / "goal1" / "BENCHMARK.lock.json"
DEFAULT_V1_SOURCES = ROOT / "goal1" / "BENCHMARK_SOURCES.json"
REPORT_SCHEMA = "supernova.goal1-benchmark-v2-candidate-report.v1"


class BenchmarkV2Error(ValueError):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _exact_string(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise BenchmarkV2Error(f"{field} must be one non-empty exact string")
    value.encode("utf-8")
    return value


def _exact_positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise BenchmarkV2Error(f"{field} must be one positive exact integer")
    return value


def load_transform_manifest(path: Path) -> dict[str, Any]:
    value = v1_validator.load_strict_json(path)
    expected = {
        "heartbeat_policy",
        "input_benchmark_root_sha256",
        "output_record_schema_version",
        "schema",
        "statement_patches",
        "status",
    }
    if set(value) != expected:
        raise BenchmarkV2Error("transform manifest fields changed")
    if value["schema"] != "supernova.goal1-benchmark-v2-transforms.v1":
        raise BenchmarkV2Error("transform manifest schema changed")
    if value["status"] != "CANDIDATE_UNSEALED_NON_CREDIT":
        raise BenchmarkV2Error("transform manifest must remain unsealed and non-credit")
    if (
        type(value["input_benchmark_root_sha256"]) is not str
        or len(value["input_benchmark_root_sha256"]) != 64
        or any(
            char not in "0123456789abcdef"
            for char in value["input_benchmark_root_sha256"]
        )
    ):
        raise BenchmarkV2Error("input benchmark root digest is malformed")
    _exact_positive_int(
        value["output_record_schema_version"],
        "output_record_schema_version",
    )
    if type(value["heartbeat_policy"]) is not dict:
        raise BenchmarkV2Error("heartbeat_policy must be one exact object")
    if type(value["statement_patches"]) is not list:
        raise BenchmarkV2Error("statement_patches must be one exact list")
    return value


def transform_records(
    records_by_split: Mapping[str, Sequence[Mapping[str, Any]]],
    transforms: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, object]]:
    if set(records_by_split) != {"test", "validation"}:
        raise BenchmarkV2Error("input must contain exact validation and test splits")
    heartbeat = transforms["heartbeat_policy"]
    if type(heartbeat) is not dict or set(heartbeat) != {
        "exact_after",
        "exact_before",
        "expected_record_count",
        "rationale",
    }:
        raise BenchmarkV2Error("heartbeat policy fields changed")
    heartbeat_before = _exact_string(heartbeat["exact_before"], "heartbeat exact_before")
    heartbeat_after = _exact_string(heartbeat["exact_after"], "heartbeat exact_after")
    if heartbeat_before == heartbeat_after:
        raise BenchmarkV2Error("heartbeat transform must change exact bytes")
    expected_records = _exact_positive_int(
        heartbeat["expected_record_count"],
        "heartbeat expected_record_count",
    )
    _exact_string(heartbeat["rationale"], "heartbeat rationale")

    output_version = _exact_positive_int(
        transforms["output_record_schema_version"],
        "output_record_schema_version",
    )
    output: dict[str, list[dict[str, Any]]] = {"validation": [], "test": []}
    index: dict[tuple[str, str], dict[str, Any]] = {}
    heartbeat_changes = 0
    for split in ("validation", "test"):
        for position, original in enumerate(records_by_split[split]):
            if type(original) is not dict:
                raise BenchmarkV2Error(f"{split} record {position} is not one exact object")
            record = dict(original)
            problem_id = _exact_string(
                record.get("problem_id"),
                f"{split} record {position} problem_id",
            )
            if record.get("split") != split:
                raise BenchmarkV2Error(f"{problem_id} split binding changed")
            key = (split, problem_id)
            if key in index:
                raise BenchmarkV2Error(f"duplicate problem binding: {split}/{problem_id}")
            code = _exact_string(record.get("lean_code"), f"{problem_id} lean_code")
            observed = code.count(heartbeat_before)
            if observed != 1:
                raise BenchmarkV2Error(
                    f"{split}/{problem_id} heartbeat header count is {observed}, expected 1"
                )
            record["lean_code"] = code.replace(
                heartbeat_before,
                heartbeat_after,
                1,
            )
            record["schema_version"] = output_version
            heartbeat_changes += 1
            output[split].append(record)
            index[key] = record
    if heartbeat_changes != expected_records:
        raise BenchmarkV2Error(
            f"heartbeat transform changed {heartbeat_changes} records, "
            f"expected {expected_records}"
        )

    patches = transforms["statement_patches"]
    if type(patches) is not list:
        raise BenchmarkV2Error("statement_patches must be one exact list")
    patch_evidence: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for position, patch in enumerate(patches):
        if type(patch) is not dict or set(patch) != {
            "evidence",
            "exact_after",
            "exact_before",
            "expected_occurrences",
            "problem_id",
            "split",
        }:
            raise BenchmarkV2Error(f"statement patch {position} fields changed")
        split = _exact_string(patch["split"], f"statement patch {position} split")
        if split not in {"validation", "test"}:
            raise BenchmarkV2Error(f"statement patch {position} split is invalid")
        problem_id = _exact_string(
            patch["problem_id"],
            f"statement patch {position} problem_id",
        )
        key = (split, problem_id)
        if key in seen:
            raise BenchmarkV2Error(f"duplicate statement patch: {split}/{problem_id}")
        seen.add(key)
        record = index.get(key)
        if record is None:
            raise BenchmarkV2Error(f"statement patch target is absent: {split}/{problem_id}")
        before = _exact_string(
            patch["exact_before"],
            f"statement patch {position} exact_before",
        )
        after = _exact_string(
            patch["exact_after"],
            f"statement patch {position} exact_after",
        )
        expected_occurrences = _exact_positive_int(
            patch["expected_occurrences"],
            f"statement patch {position} expected_occurrences",
        )
        evidence = _exact_string(
            patch["evidence"],
            f"statement patch {position} evidence",
        )
        code = record["lean_code"]
        observed = code.count(before)
        if observed != expected_occurrences:
            raise BenchmarkV2Error(
                f"{split}/{problem_id} statement patch count is {observed}, "
                f"expected {expected_occurrences}"
            )
        record["lean_code"] = code.replace(before, after, expected_occurrences)
        patch_evidence.append(
            {
                "evidence": evidence,
                "occurrences": observed,
                "problem_id": problem_id,
                "split": split,
            }
        )

    for split in ("validation", "test"):
        for record in output[split]:
            record["lean_code_sha256"] = hashlib.sha256(
                record["lean_code"].encode("utf-8")
            ).hexdigest()

    evidence_body: dict[str, object] = {
        "heartbeat_records_changed": heartbeat_changes,
        "statement_patches": patch_evidence,
    }
    return output, {
        **evidence_body,
        "transformation_evidence_sha256": hashlib.sha256(
            canonical_bytes(evidence_body)
        ).hexdigest(),
    }


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    payload = b"".join(canonical_bytes(record) + b"\n" for record in records)
    path.write_bytes(payload)


def build_candidate(
    *,
    input_directory: Path,
    output_directory: Path,
    transforms_path: Path = DEFAULT_TRANSFORMS,
) -> dict[str, object]:
    transforms = load_transform_manifest(transforms_path)
    v1_report = v1_validator.validate(
        manifest_path=DEFAULT_V1_MANIFEST,
        lock_path=DEFAULT_V1_LOCK,
        sources_path=DEFAULT_V1_SOURCES,
        dataset_directory=input_directory,
    )
    if (
        v1_report["status"] != "PASS"
        or v1_report["benchmark_root_sha256"]
        != transforms["input_benchmark_root_sha256"]
    ):
        raise BenchmarkV2Error("input corpus differs from the transform authority")

    v1_manifest = v1_validator.load_strict_json(DEFAULT_V1_MANIFEST)
    loaded = v1_validator.validate_locked_dataset(v1_manifest, input_directory)
    transformed, evidence = transform_records(
        {
            "validation": loaded["development"],
            "test": loaded["report"],
        },
        transforms,
    )
    if output_directory.exists():
        raise BenchmarkV2Error("output directory already exists")
    output_directory.mkdir(parents=True)
    corpus_directory = output_directory / "corpus"
    corpus_directory.mkdir()
    _write_jsonl(corpus_directory / "validation.jsonl", transformed["validation"])
    _write_jsonl(corpus_directory / "test.jsonl", transformed["test"])
    lock = benchmark_lock.build_lock(
        corpus_directory,
        name="miniF2F-Lean4-Kimina-composite-goal1-v2-candidate",
        version="goal1-v2-candidate-unsealed",
        split="validation+test:dev-validation/report-test",
    )
    (output_directory / "BENCHMARK_V2_CANDIDATE.lock.json").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    body: dict[str, object] = {
        "credit": "NON_CREDIT_COMPATIBILITY_CANDIDATE_ONLY",
        "input_benchmark_root_sha256": v1_report["benchmark_root_sha256"],
        "output_benchmark_root_sha256": lock["content"]["root_sha256"],
        "output_records": {
            "test": len(transformed["test"]),
            "validation": len(transformed["validation"]),
        },
        "schema": REPORT_SCHEMA,
        "status": "BUILT_UNQUALIFIED",
        "transform_manifest_sha256": hashlib.sha256(
            canonical_bytes(transforms)
        ).hexdigest(),
        "transformation_evidence": evidence,
    }
    report = {**body, "report_sha256": hashlib.sha256(canonical_bytes(body)).hexdigest()}
    (output_directory / "BENCHMARK_V2_CANDIDATE.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an unsealed, non-credit Goal-1 benchmark-v2 candidate."
    )
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--transforms", type=Path, default=DEFAULT_TRANSFORMS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_candidate(
            input_directory=args.input_directory.resolve(strict=True),
            output_directory=args.output_directory.resolve(strict=False),
            transforms_path=args.transforms.resolve(strict=True),
        )
    except (BenchmarkV2Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
