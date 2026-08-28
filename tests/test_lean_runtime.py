from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "lean"
CHECKER_PATH = RUNTIME / "check_runtime.py"
SPEC = importlib.util.spec_from_file_location("check_goal1_lean_runtime", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class LeanRuntimeTests(unittest.TestCase):
    def test_toolchain_and_mathlib_are_pinned_to_4331(self) -> None:
        self.assertEqual(
            "leanprover/lean4:v4.33.1",
            (RUNTIME / "lean-toolchain").read_text(encoding="utf-8").strip(),
        )
        lakefile = tomllib.loads((RUNTIME / "lakefile.toml").read_text(encoding="utf-8"))
        self.assertEqual(["SupernovaGoal1Smoke"], lakefile["defaultTargets"])
        self.assertEqual(
            [
                {
                    "name": "mathlib",
                    "scope": "leanprover-community",
                    "rev": "v4.33.1",
                }
            ],
            lakefile["require"],
        )
        self.assertEqual([{"name": "SupernovaGoal1Smoke"}], lakefile["lean_lib"])

    def test_manifest_locks_mathlib_and_every_dependency_to_commits(self) -> None:
        manifest = json.loads(
            (RUNTIME / "lake-manifest.json").read_text(encoding="utf-8")
        )
        packages = {package["name"]: package for package in manifest["packages"]}
        self.assertEqual(
            "0df444a360eaa60ab8c11dca51a86af692955474",
            packages["mathlib"]["rev"],
        )
        self.assertEqual("v4.33.1", packages["mathlib"]["inputRev"])
        self.assertGreaterEqual(len(packages), 1)
        for name, package in packages.items():
            self.assertRegex(
                package["rev"],
                re.compile(r"^[0-9a-f]{40}$"),
                msg=f"{name} is not locked to a full commit SHA",
            )

    def test_smoke_file_has_no_unproved_escape_hatch(self) -> None:
        source = (RUNTIME / "SupernovaGoal1Smoke.lean").read_text(encoding="utf-8")
        lowered = source.lower()
        for forbidden in ("sorry", "admit", "axiom"):
            self.assertNotIn(forbidden, lowered)
        self.assertTrue(source.startswith("import Mathlib.Tactic.NormNum\n"))
        self.assertIn("norm_num", source)
        self.assertIn("theorem supernova_goal1_runtime_smoke", source)

    def test_bootstrap_separates_cache_download_from_unpack(self) -> None:
        readme = (RUNTIME / "README.md").read_text(encoding="utf-8")
        self.assertIn("lake exe cache get-", readme)
        self.assertIn("lake exe cache unpack", readme)
        self.assertNotIn("lake exe cache get\n", readme)
        self.assertIn("short physical path", readme)

    def test_checker_requires_exact_lean_version_then_compiles_smoke(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=["lake", "env", "lean", "--version"],
                returncode=0,
                stdout="Lean (version 4.33.1, x86_64-unknown-linux-gnu, commit 819816b2e0a3)\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["lake", "env", "lean", "SupernovaGoal1Smoke.lean"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]
        with mock.patch.object(CHECKER, "_run", side_effect=responses) as run:
            result = CHECKER.check_runtime()

        self.assertEqual("PASS", result["status"])
        self.assertEqual("4.33.1", result["lean_version"])
        self.assertEqual(
            [
                mock.call(
                    ("lake", "env", "lean", "--version"),
                    timeout=CHECKER.VERSION_TIMEOUT_SECONDS,
                ),
                mock.call(
                    ("lake", "env", "lean", "SupernovaGoal1Smoke.lean"),
                    timeout=CHECKER.SMOKE_TIMEOUT_SECONDS,
                ),
            ],
            run.call_args_list,
        )

    def test_checker_rejects_wrong_or_failed_runtime_before_smoke(self) -> None:
        wrong = subprocess.CompletedProcess(
            args=["lake", "env", "lean", "--version"],
            returncode=0,
            stdout="Lean (version 4.33.0, x86_64-unknown-linux-gnu, commit old)\n",
            stderr="",
        )
        with mock.patch.object(CHECKER, "_run", return_value=wrong) as run:
            with self.assertRaisesRegex(RuntimeError, "wrong Lean runtime"):
                CHECKER.check_runtime()
            self.assertEqual(1, run.call_count)

        failed = subprocess.CompletedProcess(
            args=["lake", "env", "lean", "--version"],
            returncode=1,
            stdout="",
            stderr="toolchain unavailable",
        )
        with mock.patch.object(CHECKER, "_run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "toolchain unavailable"):
                CHECKER.check_runtime()

    def test_checker_preserves_stdout_on_smoke_failure(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=["lake", "env", "lean", "--version"],
                returncode=0,
                stdout="Lean (version 4.33.1, x86_64-w64-windows-gnu, commit 819816b2e0a3)\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["lake", "env", "lean", "SupernovaGoal1Smoke.lean"],
                returncode=1,
                stdout="Smoke.lean:1:0: error: missing object file\n",
                stderr="",
            ),
        ]
        with mock.patch.object(CHECKER, "_run", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "missing object file"):
                CHECKER.check_runtime()

    @unittest.skipUnless(
        os.environ.get("SUPERNOVA_RUN_LEAN_SMOKE") == "1",
        "set SUPERNOVA_RUN_LEAN_SMOKE=1 to run the pinned external Lean toolchain",
    )
    def test_external_pinned_runtime(self) -> None:
        self.assertEqual("PASS", CHECKER.check_runtime()["status"])


if __name__ == "__main__":
    unittest.main()
