from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_benchmark_v2_compatibility.py"
SPEC = importlib.util.spec_from_file_location(
    "check_benchmark_v2_compatibility",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

from supernova_goal1.verifier import VerifierResult, VerifierStatus


def record(problem_id: str, split: str) -> dict[str, object]:
    code = (
        "import Mathlib\n\n"
        "set_option maxHeartbeats 500000\n\n"
        f"theorem {problem_id} : True := by\n"
    )
    return {
        "problem_id": problem_id,
        "split": split,
        "lean_code": code,
        "lean_code_sha256": hashlib.sha256(code.encode()).hexdigest(),
    }


class FakeRunner:
    def __init__(
        self,
        *,
        failing_problem: str | None = None,
        failure_status: VerifierStatus = VerifierStatus.FAIL,
    ) -> None:
        self.failing_problem = failing_problem
        self.failure_status = failure_status
        self.sources: list[str] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        cwd: Path,
    ) -> VerifierResult:
        del timeout_seconds, cwd
        if command[-1] == "--version":
            return VerifierResult(
                status=VerifierStatus.PASS,
                command=command,
                returncode=0,
                stdout="Lean (version 4.33.1, test build)\n",
                stderr="",
                elapsed_milliseconds=1,
            )
        source = Path(command[-1]).read_text(encoding="utf-8")
        self.sources.append(source)
        if self.failing_problem and f"theorem {self.failing_problem} " in source:
            return VerifierResult(
                status=self.failure_status,
                command=command,
                returncode=None if self.failure_status is VerifierStatus.TIMEOUT else 1,
                stdout="",
                stderr="incompatible statement",
                elapsed_milliseconds=60,
                error=(
                    "bounded timeout"
                    if self.failure_status is VerifierStatus.TIMEOUT
                    else None
                ),
            )
        return VerifierResult(
            status=VerifierStatus.PASS,
            command=command,
            returncode=0,
            stdout="",
            stderr="declaration uses 'sorry'",
            elapsed_milliseconds=10,
        )


class CheckBenchmarkV2CompatibilityTests(unittest.TestCase):
    def records(self) -> dict[str, list[dict[str, object]]]:
        return {
            "validation": [record("alpha", "validation")],
            "test": [record("beta", "test")],
        }

    def test_all_statements_pass_and_timings_are_reported(self) -> None:
        runner = FakeRunner()
        report = SUBJECT.check_records(
            self.records(),
            runtime_root=ROOT / "runtime" / "lean",
            workers=2,
            timeout_seconds=60,
            runner=runner,
        )

        self.assertEqual("PASS", report["status"])
        self.assertEqual(2, report["record_count"])
        self.assertEqual([], report["failures"])
        self.assertEqual(10, report["timing_milliseconds"]["mean"])
        self.assertEqual(2, len(runner.sources))
        self.assertTrue(all(source.endswith(":= by\n  sorry\n") for source in runner.sources))

    def test_failed_statement_blocks_with_exact_problem_evidence(self) -> None:
        report = SUBJECT.check_records(
            self.records(),
            runtime_root=ROOT / "runtime" / "lean",
            workers=1,
            timeout_seconds=60,
            runner=FakeRunner(failing_problem="beta"),
        )

        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual(1, len(report["failures"]))
        self.assertEqual("beta", report["failures"][0]["problem_id"])
        self.assertEqual("FAIL", report["failures"][0]["status"])

    def test_timeout_is_blocking_infrastructure_evidence_not_invalid(self) -> None:
        report = SUBJECT.check_records(
            self.records(),
            runtime_root=ROOT / "runtime" / "lean",
            workers=1,
            timeout_seconds=60,
            runner=FakeRunner(
                failing_problem="alpha",
                failure_status=VerifierStatus.TIMEOUT,
            ),
        )

        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual("TIMEOUT", report["failures"][0]["status"])
        self.assertEqual("bounded timeout", report["failures"][0]["error"])

    def test_github_annotation_groups_failures_without_source_paths(self) -> None:
        report = {
            "failures": [
                {
                    "diagnostic": "/tmp/private-a.lean:12:9: error: old syntax\n",
                    "error": None,
                    "lean_code_sha256": "a" * 64,
                    "problem_id": "alpha",
                    "returncode": 1,
                    "split": "validation",
                    "status": "FAIL",
                },
                {
                    "diagnostic": "/tmp/private-b.lean:12:9: error: old syntax\n",
                    "error": None,
                    "lean_code_sha256": "b" * 64,
                    "problem_id": "beta",
                    "returncode": 1,
                    "split": "test",
                    "status": "FAIL",
                },
            ],
            "record_count": 488,
            "report_sha256": "c" * 64,
            "status": "BLOCKED",
            "timing_milliseconds": {"mean": 1},
        }

        annotation = SUBJECT.github_failure_annotation(report)

        self.assertIsNotNone(annotation)
        self.assertIn("validation/alpha@aaaaaaaaaaaaaaaa", annotation)
        self.assertIn("test/beta@bbbbbbbbbbbbbbbb", annotation)
        self.assertIn("<candidate>.lean:12:9:", annotation)
        self.assertNotIn("/tmp/private-", annotation)
        self.assertNotIn("\n", annotation)

    def test_passing_gate_has_no_failure_annotation(self) -> None:
        self.assertIsNone(SUBJECT.github_failure_annotation({"status": "PASS"}))


if __name__ == "__main__":
    unittest.main()
