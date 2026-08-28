from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_benchmark_runtime.py"
SPEC = importlib.util.spec_from_file_location("verify_benchmark_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

LOCK_SPEC = importlib.util.spec_from_file_location(
    "test_benchmark_lock", ROOT / "scripts" / "import_benchmark.py"
)
assert LOCK_SPEC is not None and LOCK_SPEC.loader is not None
LOCKER = importlib.util.module_from_spec(LOCK_SPEC)
LOCK_SPEC.loader.exec_module(LOCKER)

from supernova_goal1.verifier import VerifierResult, VerifierStatus


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FakeRunner:
    def __init__(
        self,
        *,
        version: str = "4.33.1",
        fail_problem: str | None = None,
        failure_status: VerifierStatus = VerifierStatus.FAIL,
    ) -> None:
        self.version = version
        self.fail_problem = fail_problem
        self.failure_status = failure_status
        self.calls: list[tuple[tuple[str, ...], int, Path]] = []
        self.materialized: list[str] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: int,
        cwd: Path,
    ) -> VerifierResult:
        command = tuple(command)
        self.calls.append((command, timeout_seconds, Path(cwd)))
        if command[-1] == "--version":
            return VerifierResult(
                status=VerifierStatus.PASS,
                command=command,
                returncode=0,
                stdout=(
                    f"Lean (version {self.version}, x86_64-test, commit 819816b2e0a3)\n"
                ),
                stderr="",
                elapsed_milliseconds=1,
            )

        source = Path(command[-1]).read_text(encoding="utf-8")
        self.materialized.append(source)
        if self.fail_problem is not None and f"theorem {self.fail_problem} " in source:
            returncode = None if self.failure_status in {
                VerifierStatus.TIMEOUT,
                VerifierStatus.ERROR,
            } else 1
            return VerifierResult(
                status=self.failure_status,
                command=command,
                returncode=returncode,
                stdout="",
                stderr="failed",
                elapsed_milliseconds=2,
                error=(
                    "bounded verifier failure"
                    if self.failure_status in {VerifierStatus.TIMEOUT, VerifierStatus.ERROR}
                    else None
                ),
            )
        return VerifierResult(
            status=VerifierStatus.PASS,
            command=command,
            returncode=0,
            stdout="",
            stderr="declaration uses 'sorry'\n",
            elapsed_milliseconds=2,
        )


class BenchmarkRuntimeCheckerTests(unittest.TestCase):
    def _record(self, problem_id: str) -> dict[str, object]:
        lean_code = f"import Mathlib\n\ntheorem {problem_id} : True := by\n"
        return {
            "schema_version": 1,
            "problem_id": problem_id,
            "split": "validation",
            "source_id": "fixture",
            "source_record_sha256": _sha(f"source:{problem_id}"),
            "lean_code_sha256": _sha(lean_code),
            "lean_code": lean_code,
            "informal_prefix": f"-- {problem_id}",
        }

    def _serialize(self, records: list[dict[str, object]]) -> bytes:
        return (
            "".join(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
                for record in records
            )
        ).encode("utf-8")

    def _fixture(
        self,
    ) -> tuple[
        tempfile.TemporaryDirectory[str],
        Path,
        Path,
        Path,
        list[dict[str, object]],
    ]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        benchmark = root / "benchmark"
        benchmark.mkdir()
        records = [self._record(name) for name in ("alpha", "beta", "delta", "gamma")]
        (benchmark / "validation.jsonl").write_bytes(self._serialize(records))
        # This is deliberately not JSON. A passing check demonstrates that the
        # report/test split is hashed by the lock verifier but never parsed here.
        (benchmark / "test.jsonl").write_bytes(b"sealed-test-bytes\n")

        lock = LOCKER.build_lock(
            benchmark,
            name="fixture-miniF2F",
            version="fixture-v1",
            split="validation+test:dev-validation/report-test",
        )
        lock_path = root / "BENCHMARK.lock.json"
        lock_path.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        runtime = root / "runtime"
        runtime.mkdir()
        for relative in CHECKER.RUNTIME_IDENTITY_FILES:
            shutil.copyfile(ROOT / "runtime" / "lean" / relative, runtime / relative)
        return temporary, benchmark, lock_path, runtime, records

    def test_runtime_identity_binds_exact_pinned_inputs(self) -> None:
        temporary, _, _, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        first = CHECKER._runtime_identity(runtime)
        second = CHECKER._runtime_identity(runtime)
        self.assertEqual(first, second)
        self.assertEqual(CHECKER.EXPECTED_TOOLCHAIN, first["toolchain"])
        self.assertRegex(first["runtime_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            list(CHECKER.RUNTIME_IDENTITY_FILES),
            [entry["path"] for entry in first["files"]],
        )

        (runtime / "lean-toolchain").write_text(
            "leanprover/lean4:v4.33.0\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "wrong Lean toolchain"):
            CHECKER._runtime_identity(runtime)

    def test_pass_is_deterministic_bounded_validation_only_and_non_credit(self) -> None:
        temporary, benchmark, lock_path, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        left_runner = FakeRunner()
        right_runner = FakeRunner()

        left = CHECKER.check_benchmark_runtime(
            benchmark_root=benchmark,
            lock_path=lock_path,
            runtime_root=runtime,
            sample_size=3,
            runner=left_runner,
        )
        right = CHECKER.check_benchmark_runtime(
            benchmark_root=benchmark,
            lock_path=lock_path,
            runtime_root=runtime,
            sample_size=3,
            runner=right_runner,
        )

        self.assertEqual("PASS", left["status"])
        self.assertEqual(CHECKER.NON_CREDIT_LABEL, left["credit"])
        self.assertEqual(left["sample"]["problem_ids"], right["sample"]["problem_ids"])
        self.assertEqual(3, len(left["sample"]["problem_ids"]))
        self.assertEqual("validation", left["sample"]["split"])
        self.assertEqual("sorry", left["sample"]["placeholder"])
        self.assertEqual([], left["failures"])
        self.assertEqual(4, len(left_runner.calls))
        self.assertTrue(all(source.endswith(":= by\n  sorry\n") for source in left_runner.materialized))
        self.assertNotIn("sealed-test-bytes", json.dumps(left))

    def test_selection_is_root_seeded_not_input_position(self) -> None:
        records = [self._record(name) for name in ("alpha", "beta", "gamma")]
        forward = CHECKER._select_sample(
            records,
            benchmark_root_sha256="1" * 64,
            sample_size=2,
        )
        reverse = CHECKER._select_sample(
            tuple(reversed(records)),
            benchmark_root_sha256="1" * 64,
            sample_size=2,
        )
        self.assertEqual(
            [record["problem_id"] for record in forward],
            [record["problem_id"] for record in reverse],
        )

    def test_wrong_reported_lean_version_fails_before_sample_materialization(self) -> None:
        temporary, benchmark, lock_path, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        runner = FakeRunner(version="4.33.0")
        report = CHECKER.check_benchmark_runtime(
            benchmark_root=benchmark,
            lock_path=lock_path,
            runtime_root=runtime,
            runner=runner,
        )
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("LEAN_VERSION_MISMATCH", report["failures"][0]["code"])
        self.assertEqual([], report["checks"])
        self.assertEqual([], runner.materialized)

    def test_sample_failure_is_machine_readable_and_never_credit(self) -> None:
        temporary, benchmark, lock_path, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        selected = CHECKER.check_benchmark_runtime(
            benchmark_root=benchmark,
            lock_path=lock_path,
            runtime_root=runtime,
            sample_size=4,
            runner=FakeRunner(),
        )["sample"]["problem_ids"]
        failing_id = selected[0]
        report = CHECKER.check_benchmark_runtime(
            benchmark_root=benchmark,
            lock_path=lock_path,
            runtime_root=runtime,
            sample_size=4,
            runner=FakeRunner(fail_problem=failing_id),
        )
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(CHECKER.NON_CREDIT_LABEL, report["credit"])
        self.assertEqual("LEAN_SAMPLE_FAIL", report["failures"][0]["code"])
        self.assertEqual(failing_id, report["failures"][0]["problem_id"])
        self.assertRegex(report["failures"][0]["evidence"]["stderr_sha256"], r"^[0-9a-f]{64}$")

    def test_timeout_and_error_statuses_remain_distinct(self) -> None:
        for status in (VerifierStatus.TIMEOUT, VerifierStatus.ERROR):
            temporary, benchmark, lock_path, runtime, _ = self._fixture()
            self.addCleanup(temporary.cleanup)
            selected = CHECKER.check_benchmark_runtime(
                benchmark_root=benchmark,
                lock_path=lock_path,
                runtime_root=runtime,
                sample_size=1,
                runner=FakeRunner(),
            )["sample"]["problem_ids"][0]
            report = CHECKER.check_benchmark_runtime(
                benchmark_root=benchmark,
                lock_path=lock_path,
                runtime_root=runtime,
                sample_size=1,
                runner=FakeRunner(fail_problem=selected, failure_status=status),
            )
            with self.subTest(status=status):
                self.assertEqual(f"LEAN_SAMPLE_{status.value}", report["failures"][0]["code"])

    def test_benchmark_mutation_fails_before_any_lean_command(self) -> None:
        temporary, benchmark, lock_path, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        (benchmark / "validation.jsonl").write_bytes(b"changed\n")
        runner = FakeRunner()
        with self.assertRaisesRegex(ValueError, "does not match lock"):
            CHECKER.check_benchmark_runtime(
                benchmark_root=benchmark,
                lock_path=lock_path,
                runtime_root=runtime,
                runner=runner,
            )
        self.assertEqual([], runner.calls)

    def test_validation_schema_digest_order_and_duplicates_fail_closed(self) -> None:
        cases: list[tuple[str, list[dict[str, object]]]] = []
        wrong_digest = self._record("alpha")
        wrong_digest["lean_code_sha256"] = "0" * 64
        cases.append(("does not match", [wrong_digest]))

        cases.append(("frozen problem_id order", [self._record("beta"), self._record("alpha")]))
        cases.append(("duplicate problem_id", [self._record("alpha"), self._record("alpha")]))

        extra = self._record("alpha")
        extra["unexpected"] = True
        cases.append(("fields must be exactly", [extra]))

        for message, records in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "validation.jsonl"
                path.write_bytes(self._serialize(records))
                with self.assertRaisesRegex(ValueError, message):
                    CHECKER._load_validation_records(path)

    def test_unfinished_theorem_shape_is_rejected_instead_of_rewritten_loosely(self) -> None:
        with self.assertRaisesRegex(ValueError, "ending exactly"):
            CHECKER._materialize_non_credit_statement(
                "theorem alpha : True := by\n  trivial\n",
                "alpha",
            )

    def test_sample_size_is_strictly_bounded(self) -> None:
        records = [self._record("alpha")]
        for value in (0, CHECKER.MAX_SAMPLE_SIZE + 1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CHECKER._select_sample(
                    records,
                    benchmark_root_sha256="1" * 64,
                    sample_size=value,
                )

    def test_inputs_are_revalidated_after_lean_finishes(self) -> None:
        mutations = ("runtime", "benchmark", "lock")
        for mutation in mutations:
            temporary, benchmark, lock_path, runtime, _ = self._fixture()
            self.addCleanup(temporary.cleanup)
            base = FakeRunner()
            mutated = False

            def runner(command, *, timeout_seconds, cwd):
                nonlocal mutated
                result = base(command, timeout_seconds=timeout_seconds, cwd=cwd)
                if command[-1] != "--version" and not mutated:
                    mutated = True
                    if mutation == "runtime":
                        (runtime / "lean-toolchain").write_text(
                            "leanprover/lean4:v4.33.0\n", encoding="utf-8"
                        )
                    elif mutation == "benchmark":
                        (benchmark / "validation.jsonl").write_bytes(b"changed\n")
                    else:
                        lock_path.write_bytes(lock_path.read_bytes() + b" ")
                return result

            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                CHECKER.check_benchmark_runtime(
                    benchmark_root=benchmark,
                    lock_path=lock_path,
                    runtime_root=runtime,
                    sample_size=1,
                    runner=runner,
                )

    def test_path_indirection_is_rejected_before_resolution(self) -> None:
        candidate = Path("indirect-input")
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                CHECKER._resolve_input_path(candidate, "candidate")

    def test_root_and_lock_indirection_are_rejected(self) -> None:
        temporary, benchmark, lock_path, runtime, _ = self._fixture()
        self.addCleanup(temporary.cleanup)
        alias = Path(temporary.name) / "benchmark-alias"
        try:
            alias.symlink_to(benchmark, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            CHECKER.check_benchmark_runtime(
                benchmark_root=alias,
                lock_path=lock_path,
                runtime_root=runtime,
                runner=FakeRunner(),
            )

    def test_cli_error_is_one_json_object_and_nonzero(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            returncode = CHECKER.main([str(ROOT / "missing-benchmark-root")])
        report = json.loads(output.getvalue())
        self.assertEqual(2, returncode)
        self.assertEqual("ERROR", report["status"])
        self.assertEqual(CHECKER.NON_CREDIT_LABEL, report["credit"])
        self.assertEqual("CHECKER_ERROR", report["failures"][0]["code"])


if __name__ == "__main__":
    unittest.main()
