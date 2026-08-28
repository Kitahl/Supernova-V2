from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.validate_confirmatory_benchmark import (
    canonical_sha256,
    load_strict_json,
    validate_locked_dataset,
    validate_static_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
LOCK_PATH = ROOT / "goal1" / "BENCHMARK.lock.json"
SOURCES_PATH = ROOT / "goal1" / "BENCHMARK_SOURCES.json"


def _record(problem_id: str, split: str) -> dict[str, Any]:
    lean_code = f"theorem {problem_id.replace('-', '_')} : True := by\n  trivial"
    return {
        "schema_version": 1,
        "problem_id": problem_id,
        "split": split,
        "source_id": f"{split}-source",
        "source_record_sha256": hashlib.sha256(
            f"source:{problem_id}".encode("utf-8")
        ).hexdigest(),
        "lean_code_sha256": hashlib.sha256(lean_code.encode("utf-8")).hexdigest(),
        "lean_code": lean_code,
        "informal_prefix": "/-- synthetic fixture -/\n",
    }


def _jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )
        for record in records
    )


def _set_file_contract(
    *,
    manifest: dict[str, Any],
    lock: dict[str, Any],
    sources: dict[str, Any],
    logical_key: str,
    source_key: str,
    payload: bytes,
    records: int,
) -> None:
    declared = manifest["benchmark"][logical_key]
    declared["sha256"] = hashlib.sha256(payload).hexdigest()
    declared["bytes"] = len(payload)
    declared["records"] = records
    for entry in lock["content"]["files"]:
        if entry["path"] == declared["path"]:
            entry["sha256"] = declared["sha256"]
            entry["bytes"] = declared["bytes"]
            break
    else:
        raise AssertionError(f"missing locked path {declared['path']}")
    sources["outputs"][source_key]["sha256"] = declared["sha256"]
    sources["outputs"][source_key]["bytes"] = declared["bytes"]
    sources["outputs"][source_key]["records"] = records


def _synthetic_fixture(
    directory: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = copy.deepcopy(load_strict_json(MANIFEST_PATH))
    lock = copy.deepcopy(load_strict_json(LOCK_PATH))
    sources = copy.deepcopy(load_strict_json(SOURCES_PATH))
    populations = {
        "development": ["dev-a", "dev-b"],
        "report": ["report-a", "report-b"],
    }
    manifest["membership_proof_inputs"]["population_problem_ids_by_split"] = populations
    manifest["selection"]["selected_count_per_split"] = 2
    manifest["selection"]["development_problem_ids"] = populations["development"]
    manifest["selection"]["report_problem_ids"] = populations["report"]

    records = {
        "development": [_record(value, "validation") for value in populations["development"]],
        "report": [_record(value, "test") for value in populations["report"]],
    }
    for logical_key, source_key, split_key in (
        ("development_file", "validation", "development"),
        ("report_file", "test", "report"),
    ):
        payload = _jsonl(records[split_key])
        (directory / manifest["benchmark"][logical_key]["path"]).write_bytes(payload)
        _set_file_contract(
            manifest=manifest,
            lock=lock,
            sources=sources,
            logical_key=logical_key,
            source_key=source_key,
            payload=payload,
            records=len(records[split_key]),
        )
    return manifest, lock, sources


class ConfirmatoryBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_strict_json(MANIFEST_PATH)
        cls.lock = load_strict_json(LOCK_PATH)
        cls.sources = load_strict_json(SOURCES_PATH)

    def test_repository_manifest_is_statically_bound_to_existing_lock(self) -> None:
        validate_static_manifest(self.manifest, self.lock, self.sources)
        self.assertEqual(
            len(self.manifest["membership_proof_inputs"][
                "population_problem_ids_by_split"
            ]["development"]),
            244,
        )
        self.assertEqual(
            len(self.manifest["membership_proof_inputs"][
                "population_problem_ids_by_split"
            ]["report"]),
            244,
        )
        self.assertEqual(
            len(self.manifest["selection"]["development_problem_ids"]), 60
        )
        self.assertEqual(len(self.manifest["selection"]["report_problem_ids"]), 60)

    def test_post_freeze_manifest_mutation_is_rejected(self) -> None:
        expected = canonical_sha256(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed["selection"]["report_problem_ids"][0] = "substituted"
        with self.assertRaisesRegex(ValueError, "post-freeze"):
            validate_static_manifest(
                changed,
                self.lock,
                self.sources,
                expected_manifest_sha256=expected,
            )

    def test_hash_verified_jsonl_drives_membership_not_manifest_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, lock, sources = _synthetic_fixture(directory)
            validate_static_manifest(manifest, lock, sources)
            validate_locked_dataset(manifest, directory)

            forged = copy.deepcopy(manifest)
            forged["membership_proof_inputs"][
                "population_problem_ids_by_split"
            ] = {
                "development": ["invented-a", "invented-b"],
                "report": ["invented-c", "invented-d"],
            }
            forged["selection"]["development_problem_ids"] = [
                "invented-a",
                "invented-b",
            ]
            forged["selection"]["report_problem_ids"] = [
                "invented-c",
                "invented-d",
            ]
            validate_static_manifest(forged, lock, sources)
            with self.assertRaisesRegex(ValueError, "hash-verified population"):
                validate_locked_dataset(forged, directory)

    def test_lean_code_digest_is_recomputed_after_file_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, lock, sources = _synthetic_fixture(directory)
            path = directory / manifest["benchmark"]["development_file"]["path"]
            records = [
                _record("dev-a", "validation"),
                _record("dev-b", "validation"),
            ]
            records[0]["lean_code"] = "FORGED LEAN BYTES"
            payload = _jsonl(records)
            path.write_bytes(payload)
            _set_file_contract(
                manifest=manifest,
                lock=lock,
                sources=sources,
                logical_key="development_file",
                source_key="validation",
                payload=payload,
                records=2,
            )
            validate_static_manifest(manifest, lock, sources)
            with self.assertRaisesRegex(ValueError, "Lean-code digest mismatch"):
                validate_locked_dataset(manifest, directory)

    def test_changed_locked_bytes_block_before_membership_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, _lock, _sources = _synthetic_fixture(directory)
            path = directory / manifest["benchmark"]["development_file"]["path"]
            path.write_bytes(path.read_bytes() + b"mutation")
            with self.assertRaisesRegex(ValueError, "byte count|SHA-256"):
                validate_locked_dataset(manifest, directory)

    def test_split_and_cross_split_duplicates_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, lock, sources = _synthetic_fixture(directory)
            report_path = directory / manifest["benchmark"]["report_file"]["path"]
            report_records = [
                _record("report-a", "test"),
                _record("report-b", "test"),
            ]
            development_record = _record("dev-a", "validation")
            report_records[0]["source_record_sha256"] = development_record[
                "source_record_sha256"
            ]
            payload = _jsonl(report_records)
            report_path.write_bytes(payload)
            _set_file_contract(
                manifest=manifest,
                lock=lock,
                sources=sources,
                logical_key="report_file",
                source_key="test",
                payload=payload,
                records=2,
            )
            validate_static_manifest(manifest, lock, sources)
            with self.assertRaisesRegex(ValueError, "duplicate source_record_sha256"):
                validate_locked_dataset(manifest, directory)

            report_records[0] = _record("report-a", "validation")
            payload = _jsonl(report_records)
            report_path.write_bytes(payload)
            _set_file_contract(
                manifest=manifest,
                lock=lock,
                sources=sources,
                logical_key="report_file",
                source_key="test",
                payload=payload,
                records=2,
            )
            with self.assertRaisesRegex(ValueError, "split leakage"):
                validate_locked_dataset(manifest, directory)

    def test_duplicate_json_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON member"):
                load_strict_json(path)


if __name__ == "__main__":
    unittest.main()
