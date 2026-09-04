from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_benchmark_v2.py"
SPEC = importlib.util.spec_from_file_location("build_benchmark_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)


def record(problem_id: str, split: str, statement: str) -> dict[str, object]:
    code = (
        "import Mathlib\n\n"
        "set_option maxHeartbeats 0\n\n"
        f"theorem {problem_id} : {statement} := by\n"
    )
    return {
        "schema_version": 1,
        "problem_id": problem_id,
        "split": split,
        "source_id": "fixture",
        "source_record_sha256": hashlib.sha256(problem_id.encode()).hexdigest(),
        "lean_code_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "lean_code": code,
        "informal_prefix": "-- fixture",
    }


def transforms() -> dict[str, object]:
    return {
        "schema": "supernova.goal1-benchmark-v2-transforms.v1",
        "status": "CANDIDATE_UNSEALED_NON_CREDIT",
        "input_benchmark_root_sha256": "a" * 64,
        "output_record_schema_version": 2,
        "heartbeat_policy": {
            "exact_before": "set_option maxHeartbeats 0",
            "exact_after": "set_option maxHeartbeats 500000",
            "expected_record_count": 2,
            "rationale": "bounded deterministic fixture",
        },
        "statement_patches": [
            {
                "problem_id": "alpha",
                "split": "validation",
                "exact_before": "∑ k in Finset.range 2, k",
                "exact_after": "∑ k ∈ Finset.range 2, k",
                "expected_occurrences": 1,
                "evidence": "fixture parse failure",
            }
        ],
    }


class BuildBenchmarkV2Tests(unittest.TestCase):
    def test_only_declared_problem_gets_statement_patch(self) -> None:
        old = "∑ k in Finset.range 2, k = 1"
        output, evidence = SUBJECT.transform_records(
            {
                "validation": [record("alpha", "validation", old)],
                "test": [record("beta", "test", old)],
            },
            transforms(),
        )

        alpha = output["validation"][0]
        beta = output["test"][0]
        self.assertIn("maxHeartbeats 500000", alpha["lean_code"])
        self.assertIn("∑ k ∈ Finset.range 2, k", alpha["lean_code"])
        self.assertIn("∑ k in Finset.range 2, k", beta["lean_code"])
        self.assertEqual(2, alpha["schema_version"])
        self.assertEqual(
            hashlib.sha256(alpha["lean_code"].encode()).hexdigest(),
            alpha["lean_code_sha256"],
        )
        self.assertEqual(2, evidence["heartbeat_records_changed"])
        self.assertEqual(1, len(evidence["statement_patches"]))

    def test_missing_exact_statement_bytes_fail_closed(self) -> None:
        changed = transforms()
        changed["statement_patches"][0]["exact_before"] = "absent bytes"
        with self.assertRaisesRegex(
            SUBJECT.BenchmarkV2Error,
            "statement patch count is 0, expected 1",
        ):
            SUBJECT.transform_records(
                {
                    "validation": [record("alpha", "validation", "True")],
                    "test": [record("beta", "test", "True")],
                },
                changed,
            )

    def test_heartbeat_must_change_every_declared_record(self) -> None:
        missing = record("beta", "test", "True")
        missing["lean_code"] = missing["lean_code"].replace(
            "set_option maxHeartbeats 0\n\n",
            "",
        )
        with self.assertRaisesRegex(
            SUBJECT.BenchmarkV2Error,
            "heartbeat header count is 0, expected 1",
        ):
            SUBJECT.transform_records(
                {
                    "validation": [record("alpha", "validation", "True")],
                    "test": [missing],
                },
                transforms(),
            )

    def test_candidate_lock_covers_only_the_corpus_subdirectory(self) -> None:
        rows = {
            "development": [
                record(
                    "alpha",
                    "validation",
                    "∑ k in Finset.range 2, k = 1",
                )
            ],
            "report": [record("beta", "test", "True")],
        }
        config = transforms()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            input_directory = root / "input"
            input_directory.mkdir()
            transform_path = root / "transforms.json"
            transform_path.write_text(json.dumps(config), encoding="utf-8")
            output_directory = root / "candidate"

            def load_fixture(path: Path) -> dict[str, object]:
                if path == transform_path:
                    return config
                return {"fixture": True}

            with (
                patch.object(
                    SUBJECT.v1_validator,
                    "validate",
                    return_value={
                        "status": "PASS",
                        "benchmark_root_sha256": "a" * 64,
                    },
                ),
                patch.object(
                    SUBJECT.v1_validator,
                    "load_strict_json",
                    side_effect=load_fixture,
                ),
                patch.object(
                    SUBJECT.v1_validator,
                    "validate_locked_dataset",
                    return_value=rows,
                ),
            ):
                report = SUBJECT.build_candidate(
                    input_directory=input_directory,
                    output_directory=output_directory,
                    transforms_path=transform_path,
                )

            lock = json.loads(
                (output_directory / "BENCHMARK_V2_CANDIDATE.lock.json").read_text()
            )
            verified = SUBJECT.benchmark_lock.verify_lock(
                output_directory / "corpus",
                lock,
            )
            self.assertEqual(
                report["output_benchmark_root_sha256"],
                verified["content"]["root_sha256"],
            )
            self.assertEqual(
                ["test.jsonl", "validation.jsonl"],
                [entry["path"] for entry in verified["content"]["files"]],
            )


if __name__ == "__main__":
    unittest.main()
