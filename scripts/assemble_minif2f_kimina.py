from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
OUTPUT_SCHEMA_VERSION = 1
DEFAULT_MANIFEST = Path("goal1/BENCHMARK_SOURCES.json")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object member: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _load_json_object(text: str) -> dict[str, Any]:
    value = json.loads(
        text,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    if path.is_symlink():
        raise ValueError(f"source must not be a symlink: {path}")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"source must be a regular file: {path}")

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"source changed while opening: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(handle.fileno())

    if (
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or size != after.st_size
    ):
        raise ValueError(f"source changed while hashing: {path}")
    return digest.hexdigest(), size


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_exact_str(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty exact string")
    return value


def _require_exact_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative exact integer")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = _load_json_object(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported benchmark source manifest schema_version")
    _require_mapping(manifest.get("benchmark"), "benchmark")
    sources = _require_mapping(manifest.get("sources"), "sources")
    _require_mapping(sources.get("deepseek_prover_v15"), "sources.deepseek_prover_v15")
    _require_mapping(sources.get("kimina_corrected_test"), "sources.kimina_corrected_test")
    _require_mapping(manifest.get("assembly"), "assembly")
    _require_mapping(manifest.get("outputs"), "outputs")
    return manifest


def _verify_source(path: Path, source: Mapping[str, Any], label: str) -> None:
    expected = _require_exact_str(source.get("sha256"), f"{label}.sha256")
    observed, _ = _sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, observed {observed}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"blank JSONL record at line {line_number}")
            try:
                records.append(_load_json_object(line))
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid JSONL record at line {line_number}: {exc}") from exc
    return records


def _read_kimina_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise RuntimeError(
            "reading the pinned Kimina Parquet source requires pyarrow; "
            "install an explicitly pinned pyarrow release in the acquisition environment"
        ) from exc

    table = parquet.read_table(path)
    records = table.to_pylist()
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("Kimina Parquet rows must decode to objects")
    return records


def _unique_by_name(records: Iterable[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        name = _require_exact_str(record.get("name"), f"{label}[{index}].name")
        if name in result:
            raise ValueError(f"{label} contains duplicate problem name: {name}")
        result[name] = record
    return result


def _lean_code_for_deepseek(record: Mapping[str, Any], label: str) -> tuple[str, str]:
    header = _require_exact_str(record.get("header"), f"{label}.header")
    informal = _require_exact_str(record.get("informal_prefix"), f"{label}.informal_prefix")
    statement = _require_exact_str(record.get("formal_statement"), f"{label}.formal_statement")
    return header + informal + statement, informal


def _lean_code_for_kimina(record: Mapping[str, Any], label: str) -> tuple[str, str]:
    statement = _require_exact_str(record.get("formal_statement"), f"{label}.formal_statement")
    informal = _require_exact_str(record.get("informal_prefix"), f"{label}.informal_prefix")
    return statement, informal


def _output_record(
    *,
    name: str,
    split: str,
    source_id: str,
    source_record: Mapping[str, Any],
    lean_code: str,
    informal_prefix: str,
) -> dict[str, Any]:
    if f"theorem {name}" not in lean_code:
        raise ValueError(f"{source_id} record {name} does not contain its named theorem")
    source_bytes = _canonical_json(source_record).encode("utf-8")
    code_bytes = lean_code.encode("utf-8")
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "problem_id": name,
        "split": split,
        "source_id": source_id,
        "source_record_sha256": _sha256_bytes(source_bytes),
        "lean_code_sha256": _sha256_bytes(code_bytes),
        "lean_code": lean_code,
        "informal_prefix": informal_prefix,
    }


def assemble_records(
    deepseek_records: Sequence[Mapping[str, Any]],
    kimina_records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = _require_mapping(manifest.get("sources"), "sources")
    deepseek_source = _require_mapping(sources.get("deepseek_prover_v15"), "deepseek source")
    kimina_source = _require_mapping(sources.get("kimina_corrected_test"), "Kimina source")

    expected_total = _require_exact_int(deepseek_source.get("expected_total_records"), "expected_total_records")
    expected_validation = _require_exact_int(
        deepseek_source.get("expected_validation_records"), "expected_validation_records"
    )
    expected_deepseek_test = _require_exact_int(
        deepseek_source.get("expected_test_records"), "expected_test_records"
    )
    expected_kimina = _require_exact_int(kimina_source.get("expected_records"), "Kimina expected_records")
    validation_value = _require_exact_str(
        deepseek_source.get("validation_split_value"), "validation_split_value"
    )
    test_value = _require_exact_str(deepseek_source.get("test_split_value"), "test_split_value")

    if len(deepseek_records) != expected_total:
        raise ValueError(
            f"DeepSeek record count mismatch: expected {expected_total}, observed {len(deepseek_records)}"
        )
    validation_raw = [
        record
        for record in deepseek_records
        if _require_exact_str(record.get("split"), "DeepSeek split") == validation_value
    ]
    deepseek_test_raw = [
        record
        for record in deepseek_records
        if _require_exact_str(record.get("split"), "DeepSeek split") == test_value
    ]
    if len(validation_raw) != expected_validation:
        raise ValueError(
            f"DeepSeek validation count mismatch: expected {expected_validation}, observed {len(validation_raw)}"
        )
    if len(deepseek_test_raw) != expected_deepseek_test:
        raise ValueError(
            f"DeepSeek test count mismatch: expected {expected_deepseek_test}, observed {len(deepseek_test_raw)}"
        )
    if len(kimina_records) != expected_kimina:
        raise ValueError(
            f"Kimina test count mismatch: expected {expected_kimina}, observed {len(kimina_records)}"
        )

    validation_by_name = _unique_by_name(validation_raw, "DeepSeek validation")
    deepseek_test_by_name = _unique_by_name(deepseek_test_raw, "DeepSeek test")
    kimina_by_name = _unique_by_name(kimina_records, "Kimina test")

    if set(deepseek_test_by_name) != set(kimina_by_name):
        missing = sorted(set(deepseek_test_by_name) - set(kimina_by_name))
        unexpected = sorted(set(kimina_by_name) - set(deepseek_test_by_name))
        raise ValueError(
            "Kimina test identities do not exactly match DeepSeek test identities: "
            f"missing={missing}, unexpected={unexpected}"
        )
    overlap = sorted(set(validation_by_name) & set(kimina_by_name))
    if overlap:
        raise ValueError(f"validation/test identity overlap: {overlap}")

    validation: list[dict[str, Any]] = []
    for name in sorted(validation_by_name):
        record = validation_by_name[name]
        lean_code, informal = _lean_code_for_deepseek(record, f"DeepSeek validation {name}")
        validation.append(
            _output_record(
                name=name,
                split="validation",
                source_id="deepseek_prover_v15",
                source_record=record,
                lean_code=lean_code,
                informal_prefix=informal,
            )
        )

    test: list[dict[str, Any]] = []
    for name in sorted(kimina_by_name):
        record = kimina_by_name[name]
        lean_code, informal = _lean_code_for_kimina(record, f"Kimina test {name}")
        test.append(
            _output_record(
                name=name,
                split="test",
                source_id="kimina_corrected_test",
                source_record=record,
                lean_code=lean_code,
                informal_prefix=informal,
            )
        )
    return validation, test


def _serialize_jsonl(records: Sequence[Mapping[str, Any]]) -> bytes:
    return ("".join(_canonical_json(record) + "\n" for record in records)).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def assemble(
    *,
    manifest_path: Path,
    deepseek_jsonl: Path,
    kimina_parquet: Path,
    output_directory: Path,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    sources = _require_mapping(manifest["sources"], "sources")
    _verify_source(
        deepseek_jsonl,
        _require_mapping(sources["deepseek_prover_v15"], "DeepSeek source"),
        "DeepSeek source",
    )
    _verify_source(
        kimina_parquet,
        _require_mapping(sources["kimina_corrected_test"], "Kimina source"),
        "Kimina source",
    )

    validation, test = assemble_records(
        _read_jsonl(deepseek_jsonl),
        _read_kimina_parquet(kimina_parquet),
        manifest,
    )
    assembly = _require_mapping(manifest["assembly"], "assembly")
    output_files = _require_mapping(assembly.get("output_files"), "assembly.output_files")
    validation_path = output_directory / _require_exact_str(output_files.get("validation"), "validation output")
    test_path = output_directory / _require_exact_str(output_files.get("test"), "test output")

    payloads = {
        "validation": (validation_path, _serialize_jsonl(validation)),
        "test": (test_path, _serialize_jsonl(test)),
    }
    summary: dict[str, Any] = {"status": "ASSEMBLED", "outputs": {}}
    expected_outputs = _require_mapping(manifest["outputs"], "outputs")
    for split, (path, payload) in payloads.items():
        expected = _require_mapping(expected_outputs.get(split), f"outputs.{split}")
        digest = _sha256_bytes(payload)
        byte_count = len(payload)
        expected_digest = expected.get("sha256")
        expected_bytes = expected.get("bytes")
        if expected_digest is not None and digest != expected_digest:
            raise ValueError(
                f"{split} output SHA-256 mismatch: expected {expected_digest}, observed {digest}"
            )
        if expected_bytes is not None and byte_count != expected_bytes:
            raise ValueError(
                f"{split} output byte count mismatch: expected {expected_bytes}, observed {byte_count}"
            )
        _write_atomic(path, payload)
        summary["outputs"][split] = {
            "path": path.as_posix(),
            "records": len(validation if split == "validation" else test),
            "sha256": digest,
            "bytes": byte_count,
        }
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble the pinned DeepSeek-valid plus Kimina-corrected-test miniF2F composite."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--deepseek-jsonl", type=Path, required=True)
    parser.add_argument("--kimina-parquet", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = assemble(
            manifest_path=args.manifest,
            deepseek_jsonl=args.deepseek_jsonl,
            kimina_parquet=args.kimina_parquet,
            output_directory=args.output_directory,
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
