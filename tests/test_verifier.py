from __future__ import annotations

import math
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.verifier import VerifierStatus, run_verifier


class RunVerifierTests(unittest.TestCase):
    def test_zero_exit_is_typed_pass_and_captures_stdout(self) -> None:
        result = run_verifier(
            [sys.executable, "-c", "print('verified')"], timeout_seconds=2
        )
        self.assertIs(VerifierStatus.PASS, result.status)
        self.assertTrue(result.passed)
        self.assertEqual(0, result.returncode)
        self.assertEqual("verified\n", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertIsNone(result.error)

    def test_nonzero_exit_is_typed_fail_and_captures_stderr(self) -> None:
        result = run_verifier(
            [
                sys.executable,
                "-c",
                "import sys; print('bad proof', file=sys.stderr); raise SystemExit(7)",
            ],
            timeout_seconds=2,
        )
        self.assertIs(VerifierStatus.FAIL, result.status)
        self.assertFalse(result.passed)
        self.assertEqual(7, result.returncode)
        self.assertEqual("bad proof\n", result.stderr)
        self.assertIsNone(result.error)

    def test_timeout_is_typed_and_returns_promptly(self) -> None:
        started = time.monotonic()
        result = run_verifier(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.05,
        )
        wall_seconds = time.monotonic() - started
        self.assertIs(VerifierStatus.TIMEOUT, result.status)
        self.assertIsNone(result.returncode)
        self.assertIn("exceeded timeout_seconds", result.error or "")
        self.assertLess(wall_seconds, 2.0)

    def test_missing_executable_is_typed_error(self) -> None:
        result = run_verifier(
            ["__supernova_verifier_executable_that_does_not_exist__"],
            timeout_seconds=1,
        )
        self.assertIs(VerifierStatus.ERROR, result.status)
        self.assertIsNone(result.returncode)
        self.assertIn("FileNotFoundError", result.error or "")

    def test_shell_string_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "not a shell string"):
            run_verifier("echo unsafe", timeout_seconds=1)  # type: ignore[arg-type]

    def test_timeout_must_be_finite_and_positive(self) -> None:
        for timeout in (0, -1, math.inf, math.nan):
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    run_verifier([sys.executable, "-c", "pass"], timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
