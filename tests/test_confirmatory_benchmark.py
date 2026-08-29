from __future__ import annotations

import copy
import hashlib
import json
import subprocess
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
PROTOCOL_PATH = ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json"
LOCK_PATH = ROOT / "goal1" / "BENCHMARK.lock.json"
SOURCES_PATH = ROOT / "goal1" / "BENCHMARK_SOURCES.json"
ATTESTATION_PATH = ROOT / "goal1" / "CONFIRMATORY_REPORT_ACCESS_ATTESTATION.json"


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


def _git_blob_sha1(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


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
        cls.protocol = load_strict_json(PROTOCOL_PATH)
        cls.lock = load_strict_json(LOCK_PATH)
        cls.sources = load_strict_json(SOURCES_PATH)
        cls.attestation = load_strict_json(ATTESTATION_PATH)

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
        self.assertEqual(self.manifest["selection"]["status"], "UNRESOLVED")
        self.assertIsNone(self.manifest["selection"]["selected_count_per_split"])
        self.assertEqual(self.manifest["selection"]["development_problem_ids"], [])
        self.assertEqual(self.manifest["selection"]["report_problem_ids"], [])

    def test_report_access_attestation_binds_public_report_and_runtime_gate(self) -> None:
        attestation = self.attestation
        report = self.manifest["benchmark"]["report_file"]
        self.assertEqual(attestation["schema_version"], 1)
        self.assertEqual(attestation["status"], "SEALED")
        self.assertEqual(
            attestation["claim_scope"],
            "EXPERIMENT_TIME_REPORT_ACCESS_NOT_DATA_SECRECY",
        )
        self.assertEqual(
            attestation["report_payload"],
            {
                "path": report["path"],
                "sha256": report["sha256"],
                "bytes": report["bytes"],
                "records": report["records"],
                "source_split": "test",
            },
        )
        self.assertEqual(
            attestation["benchmark_binding"],
            {
                "path": "goal1/CONFIRMATORY_BENCHMARK.json",
                "git_blob_sha1": _git_blob_sha1(MANIFEST_PATH),
                "freeze_id": self.manifest["freeze_id"],
                "scientific_input_mutation": "NONE",
            },
        )
        self.assertEqual(
            attestation["protocol_rules_binding"],
            {
                "path": "goal1/CONFIRMATORY_PROTOCOL.json",
                "git_blob_sha1": _git_blob_sha1(PROTOCOL_PATH),
                "protocol_id": self.protocol["protocol_id"],
                "sealed_rules_sha256": canonical_sha256(
                    self.protocol["sealed_rules"]
                ),
                "scientific_rule_mutation": "NONE",
            },
        )
        self.assertEqual(
            attestation["protocol_rules_binding"]["sealed_rules_sha256"],
            self.protocol["sealed_rules_sha256"],
        )
        self.assertEqual(attestation["benchmark_freeze_id"], self.manifest["freeze_id"])

        public = attestation["public_reconstructibility"]
        self.assertEqual(
            public["claim"],
            "PUBLICLY_RECONSTRUCTIBLE_FROM_PINNED_UPSTREAM_SOURCES_BEFORE_AND_AFTER_ANY_PROTOCOL_OR_DISPATCH_SEAL",
        )
        self.assertEqual(public["secrecy_or_non_public_data_claim"], "NONE")
        self.assertEqual(
            public["source_manifest"],
            {
                "path": "goal1/BENCHMARK_SOURCES.json",
                "git_blob_sha1": _git_blob_sha1(SOURCES_PATH),
            },
        )

        deepseek = self.sources["sources"]["deepseek_prover_v15"]
        kimina = self.sources["sources"]["kimina_corrected_test"]
        self.assertEqual(
            public["upstream_sources"],
            [
                {
                    "source_id": "deepseek-prover-v1.5-minif2f",
                    "repository": deepseek["repository_url"],
                    "commit": deepseek["commit"],
                    "path": deepseek["path"],
                    "sha256": deepseek["sha256"],
                    "role": "PUBLIC_IDENTITY_AND_VALIDATION_SOURCE",
                },
                {
                    "source_id": "ai-mo-minif2f-test",
                    "repository": kimina["repository_url"],
                    "commit": kimina["commit"],
                    "data_last_changed_commit": kimina["data_last_changed_commit"],
                    "path": kimina["path"],
                    "sha256": kimina["sha256"],
                    "records": kimina["expected_records"],
                    "role": "PUBLIC_CORRECTED_REPORT_THEOREM_BYTES_SOURCE",
                },
            ],
        )

        runtime = attestation["controlled_runtime_injection"]
        self.assertEqual(
            runtime["claim"],
            "PUBLIC_AVAILABILITY_DOES_NOT_AUTHORIZE_EXPERIMENT_TIME_REPORT_USE",
        )
        self.assertEqual(
            runtime["development_prompts_memory_retrieval_tuning_and_selection"],
            "BLOCKED",
        )
        self.assertEqual(
            runtime["confirmatory_runtime_injection"],
            "BLOCKED_UNTIL_PROTOCOL_RULES_EXECUTION_AUTHORITY_AND_MANIFEST_ARE_ALL_SEALED",
        )
        self.assertEqual(runtime["required_exact_report_sha256"], report["sha256"])

    def test_frozen_release_terms_mean_runtime_injection_not_data_secrecy(self) -> None:
        interpretation = self.attestation["legacy_terminology_interpretation"]
        self.assertEqual(
            interpretation["meaning"],
            "CONTROLLED_EXPERIMENT_TIME_RUNTIME_INJECTION_GATE_ONLY",
        )
        self.assertEqual(
            interpretation["public_data_disclosure_or_secrecy_claim"], "NONE"
        )
        self.assertEqual(
            interpretation["scope"],
            [
                "goal1/CONFIRMATORY_BENCHMARK.json:contamination_and_duplicate_exclusions.report_file_may_be_released_only_after_protocol_and_dispatch_seal",
                "goal1/CONFIRMATORY_PROTOCOL.json:sealed_rules.benchmark_selection.report_problem_bytes_release",
                "goal1/CONFIRMATORY_PROTOCOL.json:sealed_rules.confirmatory_manifest_interface.seal_before",
            ],
        )
        self.assertIs(
            self.manifest["contamination_and_duplicate_exclusions"][
                "report_file_may_be_released_only_after_protocol_and_dispatch_seal"
            ],
            True,
        )
        selection = self.protocol["sealed_rules"]["benchmark_selection"]
        self.assertEqual(
            selection["report_problem_bytes_release"],
            "ONLY_AFTER_PROTOCOL_RULES_EXECUTION_AUTHORITY_AND_MANIFEST_ARE_ALL_SEALED",
        )
        self.assertEqual(
            self.protocol["sealed_rules"]["confirmatory_manifest_interface"][
                "seal_before"
            ],
            "REPORT_BYTES_RELEASE_OR_FIRST_CONFIRMATORY_DISPATCH_WHICHEVER_WOULD_OCCUR_FIRST",
        )
        self.assertEqual(
            self.manifest["contamination_and_duplicate_exclusions"][
                "no_claim_of_model_training_decontamination"
            ],
            "This contract controls experiment-time leakage only; it does not claim knowledge of model pretraining data.",
        )

    def test_report_access_repository_scan_is_reproducible(self) -> None:
        scan = self.attestation["repository_scan"]
        self.assertEqual(scan["algorithm_id"], "git_tracked_regular_file_exact_sha256_v1")
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        matches: list[str] = []
        for encoded in tracked:
            if not encoded:
                continue
            relative = encoded.decode("utf-8")
            path = ROOT / relative
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == scan[
                "target_sha256"
            ]:
                matches.append(relative)
        self.assertEqual(matches, scan["expected_exact_payload_matches"])

    def test_post_freeze_manifest_mutation_is_rejected(self) -> None:
        expected = canonical_sha256(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed["benchmark"]["name"] = "substituted"
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
