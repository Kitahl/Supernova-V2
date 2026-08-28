from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
DEFAULT_LOCK = ROOT / "goal1" / "BENCHMARK.lock.json"
DEFAULT_SOURCES = ROOT / "goal1" / "BENCHMARK_SOURCES.json"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def load_strict_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {value}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def systematic_selection(population: list[str], selected_count: int) -> list[str]:
    if population != sorted(population):
        raise ValueError("population must be Unicode-code-point sorted")
    if not 0 < selected_count <= len(population):
        raise ValueError("selected_count is outside the population")
    return [
        population[(index * len(population)) // selected_count]
        for index in range(selected_count)
    ]


def validate_static_manifest(
    manifest: Mapping[str, Any],
    lock: Mapping[str, Any],
    sources: Mapping[str, Any],
    *,
    expected_manifest_sha256: str | None = None,
) -> None:
    if expected_manifest_sha256 is not None:
        if canonical_sha256(manifest) != expected_manifest_sha256:
            raise ValueError("post-freeze manifest mutation")
    if manifest.get("schema_version") != 1 or manifest.get("status") != "FROZEN":
        raise ValueError("confirmatory benchmark is not frozen")

    benchmark = manifest["benchmark"]
    if benchmark["benchmark_root_sha256"] != lock["content"]["root_sha256"]:
        raise ValueError("benchmark root mismatch")
    locked_files = {entry["path"]: entry for entry in lock["content"]["files"]}
    for manifest_key, source_key in (
        ("development_file", "validation"),
        ("report_file", "test"),
    ):
        declared = benchmark[manifest_key]
        locked = locked_files.get(declared["path"])
        if locked is None:
            raise ValueError("declared benchmark file is absent from lock")
        for field in ("path", "sha256", "bytes"):
            if declared[field] != locked[field]:
                raise ValueError("declared benchmark file differs from lock")
        if declared["records"] != sources["outputs"][source_key]["records"]:
            raise ValueError("declared record count differs from source manifest")
        if declared["sha256"] != sources["outputs"][source_key]["sha256"]:
            raise ValueError("declared output digest differs from source manifest")
        if declared["bytes"] != sources["outputs"][source_key]["bytes"]:
            raise ValueError("declared output byte count differs from source manifest")

    reference = manifest["identity_reference"]
    pinned = sources["sources"]["deepseek_prover_v15"]
    for field in ("commit", "path", "sha256"):
        if reference[field] != pinned[field]:
            raise ValueError("identity reference drift")

    populations = manifest["membership_proof_inputs"]["population_problem_ids_by_split"]
    development = populations["development"]
    report = populations["report"]
    if len(development) != benchmark["development_file"]["records"]:
        raise ValueError("development population cardinality mismatch")
    if len(report) != benchmark["report_file"]["records"]:
        raise ValueError("report population cardinality mismatch")
    if development != sorted(development) or report != sorted(report):
        raise ValueError("population order drift")
    if len(set(development)) != len(development) or len(set(report)) != len(report):
        raise ValueError("duplicate problem identity")
    if set(development) & set(report):
        raise ValueError("development/report identity leakage")

    selection = manifest["selection"]
    count = selection["selected_count_per_split"]
    if selection["algorithm_id"] != "unicode_sorted_systematic_v1":
        raise ValueError("selection algorithm drift")
    if selection["development_problem_ids"] != systematic_selection(development, count):
        raise ValueError("development selection drift")
    if selection["report_problem_ids"] != systematic_selection(report, count):
        raise ValueError("report selection drift")
    if set(selection["development_problem_ids"]) & set(
        selection["report_problem_ids"]
    ):
        raise ValueError("selected-set leakage")

    exclusions = manifest["contamination_and_duplicate_exclusions"]
    required_true = (
        "development_and_report_populations_must_be_disjoint",
        "development_and_report_selected_sets_must_be_disjoint",
        "reject_duplicate_problem_id",
        "reject_duplicate_source_record_sha256",
        "reject_duplicate_lean_code_sha256",
        "report_items_forbidden_from_development_prompts_memory_retrieval_tuning_and_selection",
        "report_file_may_be_released_only_after_protocol_and_dispatch_seal",
    )
    if any(exclusions.get(field) is not True for field in required_true):
        raise ValueError("contamination or duplicate exclusion was weakened")


def _read_hash_verified_jsonl(
    path: Path, expected: Mapping[str, Any], expected_split: str
) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"benchmark input must be a regular non-symlink file: {path}")
    payload = path.read_bytes()
    if len(payload) != expected["bytes"]:
        raise ValueError(f"{path.name} byte count mismatch")
    if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
        raise ValueError(f"{path.name} SHA-256 mismatch")

    records: list[dict[str, Any]] = []
    text = payload.decode("utf-8")
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        if not line.endswith("\n"):
            raise ValueError(f"{path.name} line {line_number} lacks terminal newline")
        raw = line[:-1]
        if not raw:
            raise ValueError(f"{path.name} line {line_number} is blank")
        record = json.loads(raw, object_pairs_hook=_strict_object)
        if not isinstance(record, dict):
            raise ValueError(f"{path.name} line {line_number} is not an object")
        records.append(record)
    if len(records) != expected["records"]:
        raise ValueError(f"{path.name} record count mismatch")

    required = {
        "schema_version",
        "problem_id",
        "split",
        "source_id",
        "source_record_sha256",
        "lean_code_sha256",
        "lean_code",
        "informal_prefix",
    }
    for index, record in enumerate(records):
        if set(record) != required:
            raise ValueError(f"{path.name} record {index} schema drift")
        if record["split"] != expected_split:
            raise ValueError(f"{path.name} record {index} split leakage")
        if not isinstance(record["problem_id"], str) or not record["problem_id"]:
            raise ValueError(f"{path.name} record {index} invalid problem_id")
        if not _is_sha256(record["source_record_sha256"]):
            raise ValueError(f"{path.name} record {index} invalid source digest")
        if not isinstance(record["lean_code"], str):
            raise ValueError(f"{path.name} record {index} invalid Lean code")
        recomputed = hashlib.sha256(record["lean_code"].encode("utf-8")).hexdigest()
        if recomputed != record["lean_code_sha256"]:
            raise ValueError(f"{path.name} record {index} Lean-code digest mismatch")
    return records


def validate_locked_dataset(
    manifest: Mapping[str, Any],
    dataset_directory: Path,
) -> dict[str, list[dict[str, Any]]]:
    benchmark = manifest["benchmark"]
    records = {
        "development": _read_hash_verified_jsonl(
            dataset_directory / benchmark["development_file"]["path"],
            benchmark["development_file"],
            "validation",
        ),
        "report": _read_hash_verified_jsonl(
            dataset_directory / benchmark["report_file"]["path"],
            benchmark["report_file"],
            "test",
        ),
    }
    populations = {
        split: sorted(record["problem_id"] for record in split_records)
        for split, split_records in records.items()
    }
    frozen = manifest["membership_proof_inputs"]["population_problem_ids_by_split"]
    if populations != frozen:
        raise ValueError("hash-verified population does not match frozen identities")

    all_records = records["development"] + records["report"]
    for field in ("problem_id", "source_record_sha256", "lean_code_sha256"):
        values = [record[field] for record in all_records]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {field}")

    count = manifest["selection"]["selected_count_per_split"]
    if systematic_selection(populations["development"], count) != manifest[
        "selection"
    ]["development_problem_ids"]:
        raise ValueError("loaded development selection mismatch")
    if systematic_selection(populations["report"], count) != manifest["selection"][
        "report_problem_ids"
    ]:
        raise ValueError("loaded report selection mismatch")
    return records


def validate(
    *,
    manifest_path: Path,
    lock_path: Path,
    sources_path: Path,
    dataset_directory: Path,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    manifest = load_strict_json(manifest_path)
    lock = load_strict_json(lock_path)
    sources = load_strict_json(sources_path)
    validate_static_manifest(
        manifest,
        lock,
        sources,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    records = validate_locked_dataset(manifest, dataset_directory)
    return {
        "status": "PASS",
        "credit": "BENCHMARK_FREEZE_PREFLIGHT_ONLY",
        "manifest_sha256": canonical_sha256(manifest),
        "benchmark_root_sha256": manifest["benchmark"]["benchmark_root_sha256"],
        "development_records": len(records["development"]),
        "report_records": len(records["report"]),
        "selected_per_split": manifest["selection"]["selected_count_per_split"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the frozen Goal-1 confirmatory benchmark."
    )
    parser.add_argument("dataset_directory", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--expected-manifest-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate(
            manifest_path=args.manifest,
            lock_path=args.lock,
            sources_path=args.sources,
            dataset_directory=args.dataset_directory,
            expected_manifest_sha256=args.expected_manifest_sha256,
        )
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
