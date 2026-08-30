from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "runtime" / "goal1_verifier" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"goal1_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entrypoint = load_script("entrypoint")
freeze_runtime = load_script("freeze_runtime")
qualify_image = load_script("qualify_image")


class Goal1VerifierRuntimeTests(unittest.TestCase):
    def test_parser_initializes_search_path_before_importing_mathlib(self) -> None:
        source = (ROOT / "runtime" / "goal1_verifier" / "ParseProduct.lean").read_text(
            encoding="utf-8"
        )
        self.assertIn("Lean.initSearchPath (← Lean.findSysroot)", source)
        self.assertLess(
            source.index("Lean.initSearchPath"), source.index("Lean.importModules")
        )

    def test_direct_lean_command_is_exact_and_produces_olean(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, *, cwd, environment, **_kwargs):
            commands.append(command)
            root = Path(command[command.index("-R") + 1])
            (root / "Solution.olean").write_bytes(b"olean")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "home").mkdir()
            with (
                patch.object(entrypoint, "TRUSTED_WORKING_DIRECTORY", root),
                patch.object(entrypoint, "runtime_environment", return_value={}),
                patch.object(entrypoint, "run", side_effect=fake_run),
            ):
                entrypoint.compile_module(
                    root=root,
                    module="Solution",
                    source=b"theorem alpha : True := by trivial\n",
                    lock={},
                )
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command[0], str(entrypoint.LEAN))
        self.assertEqual(command[1:3], ["-R", str(root)])
        self.assertIn("-o", command)
        self.assertIn("-t", command)
        self.assertIn("-DwarningAsError=true", command)
        self.assertEqual(command[-3:-1], ["-M", "0"])

    def test_trusted_reference_disables_only_warning_escalation(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, *, cwd, environment, **_kwargs):
            commands.append(command)
            root = Path(command[command.index("-R") + 1])
            (root / "Challenge.olean").write_bytes(b"olean")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "home").mkdir()
            with (
                patch.object(entrypoint, "TRUSTED_WORKING_DIRECTORY", root),
                patch.object(entrypoint, "runtime_environment", return_value={}),
                patch.object(entrypoint, "run", side_effect=fake_run),
            ):
                entrypoint.compile_module(
                    root=root,
                    module="Challenge",
                    source=b"theorem alpha : True := by sorry\n",
                    lock={},
                    warning_as_error=False,
                )
        command = commands[0]
        self.assertNotIn("-DwarningAsError=true", command)
        self.assertEqual(command[command.index("-t") + 1], "0")
        self.assertEqual(command[command.index("-M") + 1], "0")

    def test_runtime_environment_is_locked_and_lake_git_are_absent(self) -> None:
        lock = {
            "module_environment": {
                "LEAN_PATH": ["/trusted/lean"],
                "LEAN_SRC_PATH": [],
                "LD_LIBRARY_PATH": ["/opt/lean/lib"],
                "DYLD_LIBRARY_PATH": [],
            }
        }
        with (
            tempfile.TemporaryDirectory() as raw,
            patch.object(entrypoint.shutil, "which", return_value=None),
        ):
            environment = entrypoint.runtime_environment(lock, Path(raw))
        self.assertEqual(environment["LEAN_SYSROOT"], "/opt/lean")
        self.assertEqual(environment["LEAN_PATH"].split(entrypoint.os.pathsep)[0], raw)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("DOCKER_HOST", environment)

    def test_runtime_environment_rejects_forbidden_command(self) -> None:
        lock = {
            "module_environment": {
                key: []
                for key in (
                    "LEAN_PATH",
                    "LEAN_SRC_PATH",
                    "LD_LIBRARY_PATH",
                    "DYLD_LIBRARY_PATH",
                )
            }
        }
        with (
            tempfile.TemporaryDirectory() as raw,
            patch.object(entrypoint.shutil, "which", return_value="/opt/lean/bin/lake"),
            self.assertRaises(entrypoint.InfrastructureError),
        ):
            entrypoint.runtime_environment(lock, Path(raw))

    def test_build_environment_paths_are_normalized(self) -> None:
        with patch.dict(
            freeze_runtime.os.environ,
            {
                "LEAN_PATH": freeze_runtime.os.pathsep.join(
                    ["/builder/lean/lib", "/trusted/mathlib"]
                )
            },
            clear=True,
        ):
            result = freeze_runtime.normalized_environment(
                build_sysroot=Path("/builder/lean"),
                runtime_sysroot=Path("/opt/lean"),
            )
        self.assertEqual(result["LEAN_PATH"], ["/opt/lean/lib", "/trusted/mathlib"])

    def test_qualification_uses_exact_keyless_security_flags(self) -> None:
        command = qualify_image.docker_base("image@sha256:" + "a" * 64)
        self.assertIn("none", command[command.index("--network") + 1 :])
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command[command.index("--cap-drop") + 1 :])
        self.assertIn("no-new-privileges:true", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("--mount", command)

    def test_failed_qualification_persists_exact_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "qualification.json"
            stdout = io.StringIO()
            with (
                patch.object(
                    qualify_image,
                    "qualify",
                    side_effect=RuntimeError("verifier exit 20: UNKNOWN detail"),
                ),
                patch.object(
                    sys,
                    "argv",
                    ["qualify_image.py", "candidate", "--output", str(output)],
                ),
                redirect_stdout(stdout),
            ):
                exit_code = qualify_image.main()
            persisted = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(1, exit_code)
        self.assertEqual("FAILED", persisted["status"])
        self.assertEqual("RuntimeError", persisted["error_type"])
        self.assertIn("UNKNOWN detail", persisted["error"])
        self.assertIn(
            "::error file=runtime/goal1_verifier/qualify_image.py::", stdout.getvalue()
        )


if __name__ == "__main__":
    unittest.main()
