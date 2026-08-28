from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assemble_minif2f_kimina.py"
SPEC = importlib.util.spec_from_file_location("assemble_minif2f_kimina", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AssembleMiniF2FKiminaTests(unittest.TestCase):
    def _deepseek_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for split, names in (("valid", ("valid_b", "valid_a")), ("test", ("test_b", "test_a"))):
            for name in names:
                records.append(
                    {
                        "name": name,
                        "split": split,
                        "header": "import Mathlib\n",
                        "informal_prefix": f"/-- informal {name} -/\n",
                        "formal_statement": f"theorem {name} : True := by\n",
                        "goal": "⊢ True",
                    }
                )
        return records

    def _kimina_records(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "informal_prefix": f"/-- corrected informal {name} -/\n",
                "formal_statement": (
                    "import Mathlib\n\n"
                    f"/-- corrected informal {name} -/\n"
                    f"theorem {name} : True ∧ True := by\n"
                ),
            }
            for name in ("test_a", "test_b")
        ]

    def _write_fixture(self, root: Path) -> tuple[Path, Path, Path, dict[str, object]]:
        deepseek = root / "minif2f.jsonl"
        deepseek.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in self._deepseek_records()),
            encoding="utf-8",
            newline="",
        )
        kimina = root / "kimina.parquet"
        kimina.write_bytes(b"parquet-fixture")

        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        manifest: dict[str, object] = {
            "schema_version": 1,
            "status": "SOURCE_LOCKED_OUTPUT_UNBUILT",
            "benchmark": {"name": "fixture"},
            "sources": {
                "deepseek_prover_v15": {
                    "sha256": digest(deepseek),
                    "expected_total_records": 4,
                    "expected_validation_records": 2,
                    "expected_test_records": 2,
                    "validation_split_value": "valid",
                    "test_split_value": "test",
                },
                "kimina_corrected_test": {
                    "sha256": digest(kimina),
                    "expected_records": 2,
                },
            },
            "assembly": {
                "output_files": {
                    "validation": "validation.jsonl",
                    "test": "test.jsonl",
                }
            },
            "outputs": {
                "validation": {"records": 2, "sha256": None, "bytes": None},
                "test": {"records": 2, "sha256": None, "bytes": None},
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path, deepseek, kimina, manifest

    def test_assembly_is_deterministic_and_uses_corrected_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, deepseek, kimina, _ = self._write_fixture(root)
            first = root / "first"
            second = root / "second"
            with mock.patch.object(MODULE, "_read_kimina_parquet", return_value=self._kimina_records()):
                left = MODULE.assemble(
                    manifest_path=manifest_path,
                    deepseek_jsonl=deepseek,
                    kimina_parquet=kimina,
                    output_directory=first,
                )
                right = MODULE.assemble(
                    manifest_path=manifest_path,
                    deepseek_jsonl=deepseek,
                    kimina_parquet=kimina,
                    output_directory=second,
                )

            self.assertEqual(left["outputs"]["validation"]["sha256"], right["outputs"]["validation"]["sha256"])
            self.assertEqual(left["outputs"]["test"]["sha256"], right["outputs"]["test"]["sha256"])
            self.assertEqual((first / "validation.jsonl").read_bytes(), (second / "validation.jsonl").read_bytes())
            self.assertEqual((first / "test.jsonl").read_bytes(), (second / "test.jsonl").read_bytes())

            validation = [
                json.loads(line)
                for line in (first / "validation.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            test = [
                json.loads(line)
                for line in (first / "test.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["valid_a", "valid_b"], [record["problem_id"] for record in validation])
            self.assertEqual(["test_a", "test_b"], [record["problem_id"] for record in test])
            self.assertTrue(all(record["split"] == "validation" for record in validation))
            self.assertTrue(all(record["source_id"] == "kimina_corrected_test" for record in test))
            self.assertTrue(all("True ∧ True" in record["lean_code"] for record in test))
            self.assertTrue((first / "test.jsonl").read_bytes().endswith(b"\n"))

    def test_source_hash_mismatch_fails_before_parquet_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, deepseek, kimina, _ = self._write_fixture(root)
            deepseek.write_bytes(deepseek.read_bytes() + b" ")

            with mock.patch.object(MODULE, "_read_kimina_parquet") as parquet:
                with self.assertRaisesRegex(ValueError, "DeepSeek source SHA-256 mismatch"):
                    MODULE.assemble(
                        manifest_path=manifest_path,
                        deepseek_jsonl=deepseek,
                        kimina_parquet=kimina,
                        output_directory=root / "output",
                    )
                parquet.assert_not_called()

    def test_kimina_identities_must_exactly_match_deepseek_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, deepseek, kimina, _ = self._write_fixture(root)
            wrong = self._kimina_records()
            wrong[-1] = {
                "name": "unexpected",
                "informal_prefix": "/-- unexpected -/\n",
                "formal_statement": "theorem unexpected : True := by\n",
            }

            with mock.patch.object(MODULE, "_read_kimina_parquet", return_value=wrong):
                with self.assertRaisesRegex(ValueError, "identities do not exactly match"):
                    MODULE.assemble(
                        manifest_path=manifest_path,
                        deepseek_jsonl=deepseek,
                        kimina_parquet=kimina,
                        output_directory=root / "output",
                    )

    def test_output_digest_in_manifest_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, deepseek, kimina, manifest = self._write_fixture(root)
            manifest["outputs"]["validation"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(MODULE, "_read_kimina_parquet", return_value=self._kimina_records()):
                with self.assertRaisesRegex(ValueError, "validation output SHA-256 mismatch"):
                    MODULE.assemble(
                        manifest_path=manifest_path,
                        deepseek_jsonl=deepseek,
                        kimina_parquet=kimina,
                        output_directory=root / "output",
                    )

    def test_duplicate_json_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON object member"):
                MODULE.load_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
