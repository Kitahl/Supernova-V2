from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_benchmark.py"
SPEC = importlib.util.spec_from_file_location("import_benchmark", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BenchmarkImporterTests(unittest.TestCase):
    def _tree(self, root: Path) -> None:
        (root / "zeta").mkdir(parents=True)
        (root / "zeta" / "b.txt").write_bytes(b"beta\n")
        (root / "a.txt").write_bytes(b"alpha\n")

    def test_lock_is_deterministic_and_paths_are_sorted_relative(self) -> None:
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp)
            second = Path(second_tmp)
            self._tree(first)
            (second / "a.txt").write_bytes(b"alpha\n")
            (second / "zeta").mkdir(parents=True)
            (second / "zeta" / "b.txt").write_bytes(b"beta\n")

            left = MODULE.build_lock(first, name="bench", version="v1", split="test")
            right = MODULE.build_lock(second, name="bench", version="v1", split="test")

            self.assertEqual(left, right)
            self.assertEqual(
                [entry["path"] for entry in left["content"]["files"]],
                ["a.txt", "zeta/b.txt"],
            )
            self.assertTrue(all(not Path(entry["path"]).is_absolute() for entry in left["content"]["files"]))

    def test_content_change_changes_root_hash_and_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark"
            source.mkdir()
            self._tree(source)
            lock = MODULE.build_lock(source, name="bench", version="v1", split="test")
            original_hash = lock["content"]["root_sha256"]

            MODULE.verify_lock(source, lock)
            (source / "a.txt").write_bytes(b"changed\n")
            changed = MODULE.build_lock(source, name="bench", version="v1", split="test")

            self.assertNotEqual(original_hash, changed["content"]["root_sha256"])
            with self.assertRaisesRegex(ValueError, "does not match lock"):
                MODULE.verify_lock(source, lock)

    def test_empty_tree_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "no regular files"):
                MODULE.build_lock(source, name="bench", version="v1", split="test")

            target = source / "target.txt"
            target.write_text("x", encoding="utf-8")
            link = source / "alias.txt"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "symlinked file"):
                MODULE.build_lock(source, name="bench", version="v1", split="test")

    def test_symlinked_source_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "benchmark"
            source.mkdir()
            self._tree(source)
            alias = root / "benchmark-alias"
            try:
                os.symlink(source, alias, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")

            with self.assertRaisesRegex(ValueError, "source must not be a symlink"):
                MODULE.build_lock(alias, name="bench", version="v1", split="test")

    def test_walk_errors_fail_closed_instead_of_producing_partial_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark"
            source.mkdir()
            (source / "visible.txt").write_text("visible", encoding="utf-8")

            def failing_walk(root, *, topdown, onerror, followlinks):
                self.assertTrue(topdown)
                self.assertFalse(followlinks)
                self.assertIsNotNone(onerror)
                onerror(PermissionError("blocked subtree"))
                if False:
                    yield root, [], []

            with mock.patch.object(MODULE.os, "walk", failing_walk):
                with self.assertRaisesRegex(PermissionError, "blocked subtree"):
                    MODULE.build_lock(source, name="bench", version="v1", split="test")

    def test_file_change_while_hashing_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark"
            source.mkdir()
            volatile = source / "volatile.txt"
            volatile.write_bytes(b"before")
            real_fstat = MODULE.os.fstat
            calls = 0

            def mutating_fstat(fd):
                nonlocal calls
                result = real_fstat(fd)
                calls += 1
                if calls == 1:
                    volatile.write_bytes(b"after-change")
                return result

            with mock.patch.object(MODULE.os, "fstat", mutating_fstat):
                with self.assertRaisesRegex(ValueError, "changed while hashing"):
                    MODULE.build_lock(source, name="bench", version="v1", split="test")

    def test_cli_lock_and_check_emit_machine_readable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "benchmark"
            source.mkdir()
            self._tree(source)
            lock_path = root / "BENCHMARK.lock.json"

            lock_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "lock",
                    str(source),
                    "--name",
                    "bench",
                    "--version",
                    "v1",
                    "--split",
                    "test",
                    "--output",
                    str(lock_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(lock_result.returncode, 0, lock_result.stderr)
            lock_output = json.loads(lock_result.stdout)
            self.assertEqual(lock_output["status"], "LOCKED")
            self.assertEqual(lock_output["file_count"], 2)

            check_result = subprocess.run(
                [sys.executable, str(SCRIPT), "check", str(source), "--lock", str(lock_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(check_result.returncode, 0, check_result.stderr)
            self.assertEqual(json.loads(check_result.stdout)["status"], "PASS")

    def test_repository_lock_starts_explicitly_unselected(self) -> None:
        lock = json.loads((ROOT / "goal1" / "BENCHMARK.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "UNSELECTED")
        self.assertEqual(lock["benchmark"], {"name": "UNSELECTED", "version": "UNPINNED", "split": "UNLOCKED"})
        self.assertEqual(lock["content"]["files"], [])
        self.assertIsNone(lock["content"]["root_sha256"])


if __name__ == "__main__":
    unittest.main()
