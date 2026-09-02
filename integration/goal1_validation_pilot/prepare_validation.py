from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import assemble_minif2f_kimina as assembler

DEFAULT_MANIFEST = ROOT / "goal1" / "BENCHMARK_SOURCES.json"


def prepare_validation(
    *, manifest_path: Path, deepseek_jsonl: Path, output_path: Path
) -> dict[str, Any]:
    manifest = assembler.load_manifest(manifest_path)
    sources = assembler._require_mapping(manifest["sources"], "sources")
    deepseek = assembler._require_mapping(
        sources["deepseek_prover_v15"], "DeepSeek source"
    )
    assembler._verify_source(deepseek_jsonl, deepseek, "DeepSeek source")
    records = assembler._read_jsonl(deepseek_jsonl)
    expected_total = assembler._require_exact_int(
        deepseek["expected_total_records"], "expected_total_records"
    )
    if len(records) != expected_total:
        raise ValueError(
            "DeepSeek record count mismatch: "
            f"expected {expected_total}, observed {len(records)}"
        )

    validation_value = assembler._require_exact_str(
        deepseek["validation_split_value"], "validation_split_value"
    )
    validation_raw = [
        record
        for record in records
        if assembler._require_exact_str(record.get("split"), "DeepSeek split")
        == validation_value
    ]
    expected_validation = assembler._require_exact_int(
        deepseek["expected_validation_records"], "expected_validation_records"
    )
    if len(validation_raw) != expected_validation:
        raise ValueError(
            "validation count mismatch: "
            f"expected {expected_validation}, observed {len(validation_raw)}"
        )

    by_name = assembler._unique_by_name(validation_raw, "DeepSeek validation")
    assembled: list[dict[str, Any]] = []
    for name in sorted(by_name):
        record = by_name[name]
        lean_code, informal = assembler._lean_code_for_deepseek(
            record, f"DeepSeek validation {name}"
        )
        assembled.append(
            assembler._output_record(
                name=name,
                split="validation",
                source_id="deepseek_prover_v15",
                source_record=record,
                lean_code=lean_code,
                informal_prefix=informal,
            )
        )

    payload = assembler._serialize_jsonl(assembled)
    expected = assembler._require_mapping(
        manifest["outputs"]["validation"], "outputs.validation"
    )
    digest = assembler._sha256_bytes(payload)
    byte_count = len(payload)
    if (
        digest != expected["sha256"]
        or byte_count != expected["bytes"]
        or len(assembled) != expected["records"]
    ):
        raise ValueError("assembled validation output does not match its frozen identity")
    assembler._write_atomic(output_path, payload)
    return {
        "bytes": byte_count,
        "path": output_path.as_posix(),
        "records": len(assembled),
        "sha256": digest,
        "split": "validation",
        "status": "PREPARED_LOCKED_VALIDATION",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--deepseek-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = prepare_validation(
            manifest_path=args.manifest.resolve(strict=True),
            deepseek_jsonl=args.deepseek_jsonl.resolve(strict=True),
            output_path=args.output.resolve(strict=False),
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps({"error": str(exc), "status": "ERROR"}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
