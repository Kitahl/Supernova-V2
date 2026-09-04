from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from supernova_goal1.confirmatory_run import RunFiles, start_first_attempt


class ConfirmatoryRunRetirementTests(unittest.TestCase):
    def test_superseded_v1_fails_before_run_directory_or_secret_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_directory = root / "must-not-exist"
            files = RunFiles(
                run_directory=run_directory,
                secrets_directory=root / "absent-secrets",
                benchmark_directory=root / "absent-benchmark",
            )

            with self.assertRaisesRegex(
                PermissionError,
                "BLOCKED_SUPERSEDED_CONFIRMATORY_V1",
            ):
                start_first_attempt(files)

            self.assertFalse(run_directory.exists())


if __name__ == "__main__":
    unittest.main()
