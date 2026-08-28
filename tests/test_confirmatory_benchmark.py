from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "goal1" / "CONFIRMATORY_BENCHMARK.json"
LOCK_PATH = ROOT / "goal1" / "BENCHMARK.lock.json"
SOURCES_PATH = ROOT / "goal1" / "BENCHMARK_SOURCES.json"


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def load_strict(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON must be an object")
    return value


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def systematic(ids: list[str], count: int) -> list[str]:
    if ids != sorted(ids):
        raise ValueError("population must be Unicode-code-point sorted")
    if not 0 < count <= len(ids):
        raise ValueError("invalid selected count")
    return [ids[(index * len(ids)) // count] for index in range(count)]


def validate_manifest(
    manifest: dict[str, Any], *, expected_canonical_sha256: str | None = None
) -> None:
    if expected_canonical_sha256 is not None:
        if canonical_digest(manifest) != expected_canonical_sha256:
            raise ValueError("post-freeze manifest mutation")

    if manifest.get("schema_version") != 1 or manifest.get("status") != "FROZEN":
        raise ValueError("confirmatory benchmark is not frozen")
    benchmark = manifest["benchmark"]
    lock = load_strict(LOCK_PATH)
    sources = load_strict(SOURCES_PATH)
    if benchmark["benchmark_root_sha256"] != lock["content"]["root_sha256"]:
        raise ValueError("benchmark root mismatch")

    locked_files = {entry["path"]: entry for entry in lock["content"]["files"]}
    for manifest_key in ("development_file", "report_file"):
        selected_file = benchmark[manifest_key]
        locked = locked_files.get(selected_file["path"])
        if locked is None:
            raise ValueError("selected file is absent from benchmark lock")
        if any(
            selected_file[field] != locked[field]
            for field in ("path", "sha256", "bytes")
        ):
            raise ValueError("selected file metadata differs from benchmark lock")

    if benchmark["development_file"]["records"] != sources["outputs"]["validation"]["records"]:
        raise ValueError("development record count mismatch")
    if benchmark["report_file"]["records"] != sources["outputs"]["test"]["records"]:
        raise ValueError("report record count mismatch")

    reference = manifest["identity_reference"]
    pinned = sources["sources"]["deepseek_prover_v15"]
    for field in ("commit", "path", "sha256"):
        if reference[field] != pinned[field]:
            raise ValueError("identity reference drift")

    populations = manifest["membership_proof_inputs"]["population_problem_ids_by_split"]
    development = populations["development"]
    report = populations["report"]
    if len(development) != 244 or len(report) != 244:
        raise ValueError("population cardinality mismatch")
    if development != sorted(development) or report != sorted(report):
        raise ValueError("population ordering drift")
    if len(set(development)) != len(development) or len(set(report)) != len(report):
        raise ValueError("duplicate problem identity")
    if set(development) & set(report):
        raise ValueError("development/report leakage")

    selection = manifest["selection"]
    count = selection["selected_count_per_split"]
    if selection["algorithm_id"] != "unicode_sorted_systematic_v1":
        raise ValueError("selection algorithm drift")
    if selection["development_problem_ids"] != systematic(development, count):
        raise ValueError("development selection drift")
    if selection["report_problem_ids"] != systematic(report, count):
        raise ValueError("report selection drift")
    if set(selection["development_problem_ids"]) & set(
        selection["report_problem_ids"]
    ):
        raise ValueError("selected-set leakage")
    if not set(selection["development_problem_ids"]) <= set(development):
        raise ValueError("development selection is not a locked member")
    if not set(selection["report_problem_ids"]) <= set(report):
        raise ValueError("report selection is not a locked member")

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


def validate_loaded_records(
    records_by_split: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
) -> None:
    populations = manifest["membership_proof_inputs"]["population_problem_ids_by_split"]
    required_fields = set(manifest["membership_proof_inputs"]["required_record_fields"])
    all_problem_ids: list[str] = []
    all_source_hashes: list[str] = []
    all_code_hashes: list[str] = []
    for logical_split, record_split in (("development", "validation"), ("report", "test")):
        records = records_by_split[logical_split]
        ids: list[str] = []
        for record in records:
            if set(record) != required_fields:
                raise ValueError("record schema drift")
            if record["split"] != record_split:
                raise ValueError("split leakage")
            ids.append(record["problem_id"])
            all_problem_ids.append(record["problem_id"])
            all_source_hashes.append(record["source_record_sha256"])
            all_code_hashes.append(record["lean_code_sha256"])
        if sorted(ids) != populations[logical_split]:
            raise ValueError("loaded population does not match frozen identities")
    if len(all_problem_ids) != len(set(all_problem_ids)):
        raise ValueError("duplicate problem_id")
    if len(all_source_hashes) != len(set(all_source_hashes)):
        raise ValueError("duplicate source_record_sha256")
    if len(all_code_hashes) != len(set(all_code_hashes)):
        raise ValueError("duplicate lean_code_sha256")


def verify_file_bytes(path: Path, expected: dict[str, Any]) -> None:
    payload = path.read_bytes()
    if len(payload) != expected["bytes"]:
        raise ValueError("benchmark byte count mismatch")
    if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
        raise ValueError("benchmark SHA-256 mismatch")


class ConfirmatoryBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_strict(MANIFEST_PATH)

    def test_repository_manifest_is_valid_and_selected_members_are_locked(self) -> None:
        validate_manifest(self.manifest)
        self.assertEqual(
            len(self.manifest["selection"]["development_problem_ids"]), 60
        )
        self.assertEqual(len(self.manifest["selection"]["report_problem_ids"]), 60)

    def test_post_freeze_manifest_mutation_is_rejected(self) -> None:
        expected = canonical_digest(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed["selection"]["report_problem_ids"][0] = "substituted"
        with self.assertRaisesRegex(ValueError, "post-freeze"):
            validate_manifest(changed, expected_canonical_sha256=expected)

    def test_selection_overlap_duplicate_and_nonmember_are_rejected(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["membership_proof_inputs"]["population_problem_ids_by_split"][
            "report"
        ][0] = changed["membership_proof_inputs"]["population_problem_ids_by_split"][
            "development"
        ][0]
        changed["membership_proof_inputs"]["population_problem_ids_by_split"][
            "report"
        ].sort()
        with self.assertRaisesRegex(ValueError, "leakage"):
            validate_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["selection"]["development_problem_ids"][0] = "not-in-root"
        with self.assertRaisesRegex(ValueError, "selection drift"):
            validate_manifest(changed)

    def test_changed_file_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validation.jsonl"
            path.write_bytes(b"locked bytes\n")
            spec = {
                "bytes": len(path.read_bytes()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            verify_file_bytes(path, spec)
            path.write_bytes(path.read_bytes() + b"mutation")
            with self.assertRaisesRegex(ValueError, "byte count|SHA-256"):
                verify_file_bytes(path, spec)

    def test_loaded_record_split_and_duplicate_guards(self) -> None:
        small = copy.deepcopy(self.manifest)
        development = ["dev-a", "dev-b"]
        report = ["report-a", "report-b"]
        small["membership_proof_inputs"]["population_problem_ids_by_split"] = {
            "development": development,
            "report": report,
        }

        def record(problem_id: str, split: str) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "problem_id": problem_id,
                "split": split,
                "source_id": split + "-source",
                "source_record_sha256": hashlib.sha256(
                    ("source-" + problem_id).encode()
                ).hexdigest(),
                "lean_code_sha256": hashlib.sha256(
                    ("code-" + problem_id).encode()
                ).hexdigest(),
                "lean_code": "theorem " + problem_id.replace("-", "_") + " : True := by\n",
                "informal_prefix": "/-- fixture -/\n",
            }

        records = {
            "development": [record(value, "validation") for value in development],
            "report": [record(value, "test") for value in report],
        }
        validate_loaded_records(records, small)

        leaked = copy.deepcopy(records)
        leaked["report"][0]["split"] = "validation"
        with self.assertRaisesRegex(ValueError, "split leakage"):
            validate_loaded_records(leaked, small)

        duplicated = copy.deepcopy(records)
        duplicated["report"][1]["lean_code_sha256"] = duplicated["report"][0][
            "lean_code_sha256"
        ]
        with self.assertRaisesRegex(ValueError, "duplicate lean_code"):
            validate_loaded_records(duplicated, small)


if __name__ == "__main__":
    unittest.main()
