"""Runner wiring tests. Mock verdicts here are not verification evidence."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import run_verifier_control as control


class VerifierControlTests(unittest.TestCase):
    def test_exact_control_limits_and_binding(self):
        sandbox = control.launcher()
        manifest = control.control_manifest(sandbox)
        binding = control.binding_for("unit-control", manifest, sandbox)
        self.assertEqual(sandbox.timeout_seconds, 60)
        self.assertEqual(sandbox.memory_bytes, 4 * 1024**3)
        self.assertEqual(sandbox.nano_cpus, 2_000_000_000)
        self.assertEqual(sandbox.container_user, "10001:10001")
        self.assertEqual(sandbox.image_ref, control.IMAGE)
        self.assertEqual(
            control.SOURCE + control.CANDIDATE,
            b"import Mathlib\n\ntheorem supernova_repair_nat_refl (n : Nat) : n = n := by\n  rfl\n",
        )
        self.assertEqual(
            binding.candidate_source_sha256, control.sha(control.CANDIDATE)
        )
        self.assertEqual(
            binding.source_construction_sha256, control.sha(control.SOURCE)
        )
        self.assertEqual(
            binding.run_spec_id, control.sha(control.canonical_bytes(manifest))
        )
        self.assertNotEqual(
            binding.actual_dispatch_id,
            control.binding_for("other-run", manifest, sandbox).actual_dispatch_id,
        )
        self.assertEqual(binding.arm_id, "synthetic-control")
        self.assertTrue(manifest["publication_image_override"])

    def test_no_model_or_pilot_import_or_launch_interface(self):
        tree = ast.parse(Path(control.__file__).read_text(encoding="utf-8"))
        imports = [
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        ]
        self.assertFalse(any("pilot" in name or "model" in name for name in imports))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            control.main(["--output", "unused", "--model", "anything"])

    def test_error_persists_and_cli_fails(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(control, "launcher", side_effect=RuntimeError("probe")),
        ):
            output = Path(root) / "control"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(control.main(["--output", str(output)]), 1)
            report = json.loads((output / "control-report.json").read_bytes())
            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["error"]["type"], "RuntimeError")
            self.assertEqual(report["model_calls"], 0)
            digest = report.pop("report_sha256")
            self.assertEqual(digest, control.sha(control.canonical_bytes(report)))

    def test_output_must_be_fresh(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(control, "launcher") as launch,
        ):
            with self.assertRaises(FileExistsError):
                control.run(Path(root))
            launch.assert_not_called()

    def test_one_call_and_readback_for_each_verdict(self):
        for verdict, expected in [("VALID", 0), ("INVALID", 1), ("UNKNOWN", 1)]:
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as root:
                record = MagicMock()
                record.body = {"observations": {"verdict": verdict}}
                record.record_sha256 = "a" * 64
                record.signature_b64 = "UNIT_TEST_NOT_EVIDENCE"
                store = MagicMock()
                store.read_complete.return_value = (record,)
                store.read_blobs.return_value.stdout = b""
                store.read_blobs.return_value.stderr = b""
                with (
                    patch.object(control, "VerifierEvidenceStore", return_value=store),
                    patch.object(control, "VerifierSupervisor") as supervisor,
                ):
                    supervisor.return_value.run_and_record.return_value = record
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(
                            control.main(["--output", str(Path(root) / "out")]),
                            expected,
                        )
                    supervisor.return_value.run_and_record.assert_called_once()
                    kwargs = supervisor.return_value.run_and_record.call_args.kwargs
                    self.assertEqual(
                        kwargs,
                        {
                            "source": control.SOURCE,
                            "candidate": control.CANDIDATE,
                            "theorem_names": (control.THEOREM,),
                        },
                    )
                    record.verify.assert_called_once()
                    store.read_complete.assert_called_once()
                    store.read_blobs.assert_called_once()

    def test_bad_signature_never_passes(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(control, "VerifierEvidenceStore"),
            patch.object(control, "VerifierSupervisor") as supervisor,
        ):
            record = supervisor.return_value.run_and_record.return_value
            record.verify.side_effect = ValueError("invalid signature")
            report = control.run(Path(root) / "out")
            self.assertEqual(report["status"], "ERROR")
            self.assertNotIn("signature_and_store_readback", report)

    def test_workflow_is_single_pull_only_branch_route(self):
        raw = (
            control.ROOT / ".github/workflows/goal1_verifier_control.yml"
        ).read_text()
        self.assertIn("branches: [work/PM/G1V2-verifier-control]", raw)
        self.assertNotIn("pull_request:", raw)
        self.assertNotIn("workflow_dispatch:", raw)
        self.assertIn("packages: read", raw)
        self.assertIn("contents: read", raw)
        for forbidden in (
            "packages: write",
            "docker build",
            "docker push",
            "run_validation_pilot",
            "run_repair",
            "schedule:",
        ):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(raw.count("python scripts/run_verifier_control.py"), 1)
        self.assertIn("if: always()", raw)
        self.assertIn("persist-credentials: false", raw)
        self.assertIn(control.IMAGE, raw)


if __name__ == "__main__":
    unittest.main()
